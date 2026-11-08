import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kevlint.rules import PASS

HOST = Path(__file__).resolve().parents[1]
ROOT = HOST.parent
FIXTURES = ROOT / "fixtures"
SAMPLE = str(ROOT / "kevlint.toml")


def trace(name):
    return str(FIXTURES / f"{name}.txt")


def run(*args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("KEVLINT_LLM_")}
    env["PYTHONPATH"] = str(HOST)
    return subprocess.run(
        [sys.executable, "-m", "kevlint", *args], capture_output=True, text=True, env=env
    )


def config_file(folder, text):
    path = Path(folder) / "k.toml"
    path.write_text(text)
    return str(path)


MISSING = '[rules.max-blocked-time]\nbudgets = [{ thread = "fusion_rt", max_ms = 50 }]\n'
REQUIRED = (
    '[rules.max-blocked-time]\nbudgets = [{ thread = "fusion_rt", max_ms = 50, required = true }]\n'
)


class SampleConfigTest(unittest.TestCase):
    def test_exit_status_for_each_demo(self):
        for name, wanted in (("demo_normal", 0), ("demo_fixed", 0), ("demo_inversion", 1)):
            done = run("check", trace(name), "--config", SAMPLE)
            self.assertEqual(done.returncode, wanted, name + done.stderr)

    def test_inversion_demo_reports_each_rule(self):
        done = run("check", trace("demo_inversion"), "--config", SAMPLE)
        for wanted in (
            "RULE priority-inversion: FAIL",
            "RULE max-blocked-time: FAIL",
            "RULE no-semaphore-high-priority: FAIL",
            "RESULTS BY RULE",
            "priority-inversion: FAIL, 10 finding(s)",
            "max-blocked-time: FAIL, 11 finding(s)",
            "no-semaphore-high-priority: FAIL, 11 finding(s)",
            "FAIL: 32 finding(s) across 3 rule(s)",
            "ROOT CAUSES",
            "32 findings from 3 rules, all from 1 root cause: fusion_rt blocking on semaphore 0x0000001f5d627018",
            "- fusion_rt blocking on semaphore 0x0000001f5d627018: priority-inversion 10, "
            "max-blocked-time 11, no-semaphore-high-priority 11",
            "BREACH 1 of 11",
            "FINDING 11 of 11",
            "at least 52.195 ms (still open at trace end)",
            "lines:   wait call 1172, block start 1177, release 6175, block end 6179",
            "budget fusion_rt: 50 ms on THSEM, THMUTEX: FAIL, 11 breach(es)",
        ):
            self.assertIn(wanted, done.stdout)

    def test_passing_demo_states_each_rule_and_coverage(self):
        done = run("check", trace("demo_fixed"), "--config", SAMPLE)
        self.assertIn("RULE max-blocked-time: PASS", done.stdout)
        self.assertIn("coverage: 11 evaluated, 0 not evaluated", done.stdout)
        self.assertIn("PASS: 0 finding(s) across 3 rule(s)", done.stdout)

    def test_minimum_priority_moves_low_waiters_to_not_evaluated(self):
        done = run("check", trace("demo_fixed"), "--config", SAMPLE)
        self.assertIn("(waiter below the configured minimum priority)", done.stdout)

    def test_check_all_with_html_and_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            listing = Path(folder) / "list.txt"
            listing.write_text(f"{trace('demo_fixed')}\n{trace('demo_inversion')}\n")
            summary = Path(folder) / "s.md"
            done = run(
                "check-all", str(listing), "--reports-dir", folder,
                "--config", SAMPLE, "--summary", str(summary),
            )
            text = summary.read_text()
            page = (Path(folder) / "demo_inversion.html").read_text()
        self.assertEqual(done.returncode, 1)
        self.assertIn("### kevlint results by rule", text)
        self.assertIn("| `max-blocked-time` | ❌ FAIL | 11 |", text)
        self.assertIn("| `no-semaphore-high-priority` | ❌ FAIL | 11 |", text)
        self.assertEqual(text.count("| `priority-inversion` |"), 2)
        self.assertIn("`fusion_rt` exceeded its 50 ms budget 11 times", text)
        self.assertIn("`fusion_rt` blocked on a semaphore at high priority 11 times", text)
        self.assertIn('id="rule-max-blocked-time"', page)
        self.assertIn("#### Root causes in", text)
        self.assertIn("**32 findings from 3 rules, all from 1 root cause:", text)
        self.assertNotIn("ROOT CAUSES", done.stdout.split("demo_inversion")[0])


class WithoutConfigTest(unittest.TestCase):
    def test_a_kevlint_toml_in_the_working_directory_is_ignored(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("KEVLINT_LLM_")}
        env["PYTHONPATH"] = str(HOST)
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "kevlint.toml").write_text("this is not valid toml [")
            done = subprocess.run(
                [sys.executable, "-m", "kevlint", "check", trace("demo_inversion")],
                capture_output=True, text=True, env=env, cwd=folder,
            )
        self.assertEqual(done.returncode, 1)
        self.assertIn("rule: priority-inversion", done.stdout)
        self.assertNotIn("RULE ", done.stdout)


class MissingThreadExitTest(unittest.TestCase):
    def test_missing_thread_warns_but_does_not_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            done = run("check", trace("test_wide"), "--config", config_file(folder, MISSING))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("RULE max-blocked-time: NOT EVALUATED", done.stdout)
        self.assertIn("WARNING: no thread named 'fusion_rt' in this trace", done.stdout)
        self.assertIn("NOT EVALUATED: 0 finding(s)", done.stdout)
        self.assertNotIn("\nPASS", done.stdout)

    def test_required_missing_thread_exits_2(self):
        with tempfile.TemporaryDirectory() as folder:
            done = run("check", trace("test_wide"), "--config", config_file(folder, REQUIRED))
        self.assertEqual(done.returncode, 2)
        self.assertIn("ERROR: required thread 'fusion_rt' is not in this trace", done.stdout)
        self.assertIn("kevlint:", done.stderr)

    def test_required_thread_that_exists_behaves_normally(self):
        with tempfile.TemporaryDirectory() as folder:
            done = run("check", trace("demo_fixed"), "--config", config_file(folder, REQUIRED))
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_summary_shows_not_evaluated_and_error(self):
        with tempfile.TemporaryDirectory() as folder:
            summary = Path(folder) / "s.md"
            run("check", trace("test_wide"), "--config", config_file(folder, MISSING), "--summary", str(summary))
            self.assertIn("⚠️ NOT EVALUATED", summary.read_text())
            self.assertIn("no thread named", summary.read_text())


class BadConfigExitTest(unittest.TestCase):
    def test_invalid_configs_exit_2_before_checking(self):
        cases = (
            '[rules.max-blocked-time]\nbudgets = [{ thread = "a", max_ms = 0 }]\n',
            '[rules.priority-inversions]\nenabled = true\n',
            'not toml [',
            '[rules.max-blocked-time]\nbudgets = [{ thread = "a", max_ms = 1, states = ["THBOGUS"] }]\n',
        )
        with tempfile.TemporaryDirectory() as folder:
            for text in cases:
                done = run("check", trace("demo_normal"), "--config", config_file(folder, text))
                self.assertEqual(done.returncode, 2, text)
                self.assertEqual(done.stdout, "")
                self.assertTrue(done.stderr.startswith("kevlint:"))

    def test_missing_config_file_exits_2_and_writes_the_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            summary = Path(folder) / "s.md"
            done = run(
                "check", trace("demo_normal"), "--config", str(Path(folder) / "none.toml"),
                "--summary", str(summary),
            )
            self.assertEqual(done.returncode, 2)
            self.assertIn("ERROR", summary.read_text())


class DisabledRuleTest(unittest.TestCase):
    def test_turning_inversion_off_removes_its_failure(self):
        text = "[rules.priority-inversion]\nenabled = false\n" + MISSING.replace("50", "1000")
        with tempfile.TemporaryDirectory() as folder:
            done = run("check", trace("demo_inversion"), "--config", config_file(folder, text))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("not enabled: priority-inversion", done.stdout)
        self.assertNotIn("RULE priority-inversion", done.stdout)

    def test_explain_is_skipped_when_inversion_is_off(self):
        text = "[rules.priority-inversion]\nenabled = false\n" + MISSING
        with tempfile.TemporaryDirectory() as folder:
            done = run("check", trace("demo_inversion"), "--explain", "--config", config_file(folder, text))
        self.assertEqual(done.returncode, 1)
        self.assertNotIn("AI-GENERATED", done.stdout)


class ErrorRowTest(unittest.TestCase):
    def test_unreadable_trace_gets_one_row_beside_rule_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            listing = Path(folder) / "list.txt"
            listing.write_text(f"{trace('demo_fixed')}\n{folder}/missing.txt\n")
            summary = Path(folder) / "s.md"
            done = run(
                "check-all", str(listing), "--reports-dir", folder,
                "--config", SAMPLE, "--summary", str(summary),
            )
            text = summary.read_text()
        self.assertEqual(done.returncode, 2)
        self.assertIn("| n/a | ⚠️ ERROR |", text)
        self.assertIn(f"| `priority-inversion` | ✅ {PASS} |", text)


if __name__ == "__main__":
    unittest.main()
