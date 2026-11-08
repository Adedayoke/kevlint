import os
import subprocess
import sys
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

    def test_unreadable_trace_is_exit_2(self):
        done = run("check", str(FIXTURES / "does_not_exist.txt"))
        self.assertEqual(done.returncode, 2)

    def test_non_wide_trace_is_exit_2(self):
        done = run("check", str(FIXTURES / "test.txt"))
        self.assertEqual(done.returncode, 2)
        self.assertIn("tracelogger -w", done.stderr)


if __name__ == "__main__":
    unittest.main()
