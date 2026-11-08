"""End to end through the real urllib path, against a local fake endpoint only."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kevlint.inversion import check
from kevlint.parser import parse_trace
from kevlint.patterns import group_patterns
from tests.fake_llm import FakeLlm, reply

HOST = Path(__file__).resolve().parents[1]
FIXTURES = HOST.parent / "fixtures"
INVERSION = str(FIXTURES / "demo_inversion.txt")
NORMAL = str(FIXTURES / "demo_normal.txt")
KEY = "sentinel-key-do-not-print"
SKIP_NOTE = "kevlint: explanations skipped:"


def run(*args, base_url=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("KEVLINT_LLM_")}
    env["PYTHONPATH"] = str(HOST)
    if base_url:
        env.update(
            KEVLINT_LLM_BASE_URL=base_url, KEVLINT_LLM_API_KEY=KEY, KEVLINT_LLM_MODEL="fake"
        )
    return subprocess.run(
        [sys.executable, "-m", "kevlint", *args], capture_output=True, text=True, env=env
    )


def good_answer():
    worst = group_patterns(check(parse_trace(INVERSION)).violations)[0].worst
    claim = {"text": "fusion_hog <b>ran</b> first.", "lines": [worst.middle_line]}
    return json.dumps({"claims": [claim]}), worst.middle_line


class NotConfiguredTest(unittest.TestCase):
    def test_one_note_and_otherwise_unchanged(self):
        plain = run("check", INVERSION)
        done = run("check", INVERSION, "--explain")
        self.assertEqual(done.returncode, 1)
        note, _, rest = done.stdout.partition("\n")
        self.assertTrue(note.startswith(SKIP_NOTE), note)
        self.assertIn("KEVLINT_LLM_API_KEY", note)
        self.assertEqual(rest, plain.stdout)

    def test_check_all_notes_once(self):
        with tempfile.TemporaryDirectory() as folder:
            listing = Path(folder) / "list.txt"
            listing.write_text(f"{INVERSION}\n{NORMAL}\n")
            done = run("check-all", str(listing), "--reports-dir", folder, "--explain")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout.count(SKIP_NOTE), 1)

    def test_html_without_explanations_has_no_ai_block(self):
        with tempfile.TemporaryDirectory() as folder:
            html = Path(folder) / "r.html"
            run("check", INVERSION, "--html", str(html), "--explain")
            self.assertNotIn("AI-generated", html.read_text())


class ExplainedTest(unittest.TestCase):
    def test_terminal_html_and_exit_status(self):
        content, line = good_answer()
        with tempfile.TemporaryDirectory() as folder, FakeLlm(reply(content)) as fake:
            html = Path(folder) / "r.html"
            done = run("check", INVERSION, "--html", str(html), "--explain", base_url=fake.base_url)
            page = html.read_text()
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("AI-GENERATED EXPLANATION", done.stdout)
        self.assertIn(f"[line {line}]", done.stdout)
        self.assertLess(done.stdout.index("FAIL:"), done.stdout.index("AI-GENERATED"))
        self.assertIn("AI-generated explanation", page)
        self.assertIn(f"line {line}", page)
        self.assertIn("&lt;b&gt;ran&lt;/b&gt;", page)
        self.assertNotIn("<b>ran</b>", page)
        for text in (done.stdout, done.stderr, page):
            self.assertNotIn(KEY, text)
        self.assertEqual(fake.requests[0][1]["Authorization"], f"Bearer {KEY}")

    def test_passing_trace_makes_no_request(self):
        with FakeLlm(reply("unused")) as fake:
            done = run("check", NORMAL, "--explain", base_url=fake.base_url)
        self.assertEqual(done.returncode, 0)
        self.assertEqual(fake.requests, [])
        self.assertNotIn("AI-GENERATED", done.stdout)

    def test_check_all_explains_only_failing_traces(self):
        content, line = good_answer()
        with tempfile.TemporaryDirectory() as folder, FakeLlm(reply(content)) as fake:
            listing = Path(folder) / "list.txt"
            listing.write_text(f"{NORMAL}\n{INVERSION}\n")
            done = run(
                "check-all", str(listing), "--reports-dir", folder, "--explain",
                base_url=fake.base_url,
            )
            page = (Path(folder) / "demo_inversion.html").read_text()
        self.assertEqual(done.returncode, 1)
        self.assertEqual(len(fake.requests), 1)
        self.assertIn(f"[line {line}]", done.stdout)
        self.assertIn("AI-generated explanation", page)


class FailureNeverChangesTheVerdictTest(unittest.TestCase):
    def check_failure(self, step, wanted):
        plain = run("check", INVERSION)
        with tempfile.TemporaryDirectory() as folder, FakeLlm(step) as fake:
            html = Path(folder) / "r.html"
            done = run("check", INVERSION, "--html", str(html), "--explain", base_url=fake.base_url)
            page = html.read_text()
        self.assertEqual(done.returncode, plain.returncode)
        self.assertEqual(done.returncode, 1)
        self.assertIn(wanted, done.stdout)
        self.assertIn(wanted, page)
        self.assertNotIn(KEY, done.stdout + done.stderr + page)
        return done, page

    def test_bad_citation_is_rejected_and_no_claim_is_shown(self):
        bad = json.dumps({"claims": [{"text": "Invented claim.", "lines": [99999999]}]})
        done, page = self.check_failure(
            reply(bad), "explanation rejected: unverifiable citation"
        )
        self.assertNotIn("Invented claim.", done.stdout)
        self.assertNotIn("Invented claim.", page)
        self.assertIn("AI-GENERATED EXPLANATION", done.stdout)

    def test_rate_limit(self):
        self.check_failure(
            (429, {"Retry-After": "0"}, b""), "explanation unavailable: rate limited (HTTP 429)"
        )

    def test_auth_failure(self):
        self.check_failure(
            (401, {}, KEY.encode()), "explanation unavailable: not authorized (HTTP 401)"
        )

    def test_garbage_response(self):
        self.check_failure((200, {}, b"<html>"), "explanation unavailable: malformed API response")


if __name__ == "__main__":
    unittest.main()
