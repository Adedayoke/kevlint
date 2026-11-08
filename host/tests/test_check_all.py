import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOST = Path(__file__).resolve().parents[1]
REPO = HOST.parent
FIXTURES = REPO / "fixtures"


class CheckAllTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.list = self.folder / "traces.txt"
        self.reports = self.folder / "reports"
        self.summary = self.folder / "summary.md"

    def run_list(self, *lines):
        self.list.write_text("\n".join(lines) + "\n", encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(HOST))
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "kevlint",
                "check-all",
                str(self.list),
                "--reports-dir",
                str(self.reports),
                "--summary",
                str(self.summary),
            ],
            capture_output=True,
            text=True,
            env=env,
        )

    def trace(self, name):
        return str(FIXTURES / f"{name}.txt")

    def test_clean_list_passes_and_writes_each_report(self):
        done = self.run_list("# comment", "", self.trace("demo_normal"), self.trace("demo_fixed"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(
            sorted(p.name for p in self.reports.iterdir()),
            ["demo_fixed.html", "demo_normal.html"],
        )

    def test_a_violation_fails_but_every_trace_is_still_checked(self):
        done = self.run_list(
            self.trace("demo_inversion"), self.trace("demo_normal"), self.trace("demo_fixed")
        )
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(len(list(self.reports.iterdir())), 3)
        text = self.summary.read_text(encoding="utf-8")
        for name in ("demo_inversion", "demo_normal", "demo_fixed"):
            self.assertIn(name, text)
        self.assertIn("#### Why", text)

    def test_missing_trace_is_exit_2_and_others_still_run(self):
        done = self.run_list(self.trace("does_not_exist"), self.trace("demo_normal"))
        self.assertEqual(done.returncode, 2)
        self.assertTrue((self.reports / "demo_normal.html").exists())
        self.assertIn("ERROR", self.summary.read_text(encoding="utf-8"))

    def test_missing_trace_outranks_a_violation(self):
        done = self.run_list(self.trace("does_not_exist"), self.trace("demo_inversion"))
        self.assertEqual(done.returncode, 2)

    def test_empty_list_fails(self):
        done = self.run_list("# nothing listed", "")
        self.assertEqual(done.returncode, 2)
        self.assertIn("the trace list is empty", done.stderr)
        self.assertIn("ERROR", self.summary.read_text(encoding="utf-8"))

    def test_unreadable_list_fails(self):
        env = dict(os.environ, PYTHONPATH=str(HOST))
        done = subprocess.run(
            [
                sys.executable,
                "-m",
                "kevlint",
                "check-all",
                str(self.folder / "absent.txt"),
                "--reports-dir",
                str(self.reports),
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(done.returncode, 2)
        self.assertIn("cannot read trace list", done.stderr)

    def test_two_traces_with_one_report_name_fail(self):
        done = self.run_list(self.trace("demo_normal"), "other/demo_normal.txt")
        self.assertEqual(done.returncode, 2)
        self.assertIn("share a report name", done.stderr)


class RepositoryListTest(unittest.TestCase):
    """Only checks the list is usable: its verdict is CI's job, and may be red."""

    def test_every_listed_trace_exists(self):
        lines = (REPO / "ci" / "traces.txt").read_text(encoding="utf-8").splitlines()
        listed = [l.strip() for l in lines if l.strip() and not l.strip().startswith("#")]
        self.assertTrue(listed)
        for path in listed:
            self.assertTrue((REPO / path).is_file(), path)


if __name__ == "__main__":
    unittest.main()
