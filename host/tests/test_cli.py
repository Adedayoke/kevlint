import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1]
FIXTURES = HOST.parent / "fixtures"


def run(*args):
    env = dict(os.environ, PYTHONPATH=str(HOST))
    return subprocess.run(
        [sys.executable, "-m", "kevlint", *args],
        capture_output=True,
        text=True,
        env=env,
    )


class CliTest(unittest.TestCase):
    def test_normal_passes(self):
        done = run("check", str(FIXTURES / "demo_normal.txt"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("PASS", done.stdout)

    def test_every_verdict_states_coverage(self):
        for name in ("demo_normal", "demo_fixed", "demo_inversion"):
            done = run("check", str(FIXTURES / f"{name}.txt"))
            self.assertRegex(
                done.stdout, r"blocks: \d+ total, \d+ evaluated, \d+ not evaluated"
            )
            self.assertRegex(
                done.stdout,
                r"(PASS|FAIL): \d+ violation\(s\) in \d+ evaluated block\(s\); "
                r"\d+ block\(s\) not evaluated",
            )

    def test_fixed_prints_one_resolved_line(self):
        done = run("check", str(FIXTURES / "demo_fixed.txt"))
        wanted = (
            "Resolved correctly: fusion_rt (50) waited 24.3 ms on fusion_holder "
            "[holder inferred], boosted 10 -> 50 (priority inheritance); "
            "fusion_hog (30) stayed READY"
        )
        self.assertEqual(done.stdout.count(wanted), 1)
        self.assertEqual(done.stdout.count("Resolved correctly"), 1)

    def test_no_resolved_line_without_a_resolved_wait(self):
        done = run("check", str(FIXTURES / "demo_inversion.txt"))
        self.assertNotIn("Resolved correctly", done.stdout)

    def test_fixed_passes(self):
        done = run("check", str(FIXTURES / "demo_fixed.txt"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_inversion_fails_with_evidence(self):
        done = run("check", str(FIXTURES / "demo_inversion.txt"))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        for text in (
            "VIOLATION 1 of 10",
            "waiter: fusion_rt",
            "holder: fusion_holder",
            "inferred, not named by the trace",
            "middle: fusion_hog",
            "wait call",
            "FAIL",
        ):
            self.assertIn(text, done.stdout)

    def test_html_keeps_the_exit_code_and_terminal_output(self):
        with tempfile.TemporaryDirectory() as folder:
            for name, code in (("demo_inversion", 1), ("demo_normal", 0)):
                target = Path(folder) / f"{name}.html"
                trace = str(FIXTURES / f"{name}.txt")
                plain = run("check", trace)
                done = run("check", trace, "--html", str(target))
                self.assertEqual(done.returncode, code, done.stdout + done.stderr)
                self.assertTrue(done.stdout.startswith(plain.stdout))
                self.assertTrue(target.read_text(encoding="utf-8").startswith("<!doctype html>"))

    def test_summary_option_writes_the_table(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "summary.md"
            done = run("check", str(FIXTURES / "demo_inversion.txt"), "--summary", str(target))
            self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
            self.assertIn("| Trace | Result |", target.read_text(encoding="utf-8"))

    def test_summary_records_an_unreadable_trace(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "summary.md"
            done = run("check", str(FIXTURES / "does_not_exist.txt"), "--summary", str(target))
            self.assertEqual(done.returncode, 2)
            self.assertIn("ERROR", target.read_text(encoding="utf-8"))

    def test_unwritable_summary_path_is_exit_2(self):
        target = FIXTURES / "no_such_directory" / "summary.md"
        done = run("check", str(FIXTURES / "demo_normal.txt"), "--summary", str(target))
        self.assertEqual(done.returncode, 2)
        self.assertIn("cannot write summary", done.stderr)

    def test_unwritable_html_path_is_exit_2(self):
        target = FIXTURES / "no_such_directory" / "report.html"
        done = run("check", str(FIXTURES / "demo_normal.txt"), "--html", str(target))
        self.assertEqual(done.returncode, 2)
        self.assertIn("cannot write HTML report", done.stderr)

    def test_unreadable_trace_is_exit_2(self):
        done = run("check", str(FIXTURES / "does_not_exist.txt"))
        self.assertEqual(done.returncode, 2)

    def test_non_wide_trace_is_exit_2(self):
        done = run("check", str(FIXTURES / "test.txt"))
        self.assertEqual(done.returncode, 2)
        self.assertIn("tracelogger -w", done.stderr)


if __name__ == "__main__":
    unittest.main()
