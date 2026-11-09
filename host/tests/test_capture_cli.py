import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kevlint.capture import CaptureError, Options, capture

HOST = Path(__file__).resolve().parents[1]
FAKE = Path(__file__).resolve().parent / "fake_target.py"
REPO = HOST.parent
INVERSION = str(REPO / "fixtures" / "demo_inversion")
NORMAL = str(REPO / "fixtures" / "demo_normal")


class FakeTargetCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.out = self.root / "output"
        self.out.mkdir()
        self.prefix = str(self.out / "name")
        self.install("host_bin", ["ssh", "scp", "traceprinter"])
        self.install("remote_bin", ["tracelogger"])
        self.env = {
            key: value
            for key, value in os.environ.items()
            if key != "KEVLINT_TARGET" and not key.startswith("KEVLINT_LLM")
        }
        self.env.update(
            FAKE_ROOT=str(self.root),
            FAKE_FIXTURE=INVERSION,
            QNX_HOST="/fake/qnx",
            PATH=f"{self.root}/host_bin:/usr/bin:/bin",
            PYTHONPATH=str(HOST),
        )

    def install(self, folder, tools):
        directory = self.root / folder
        directory.mkdir()
        for tool in tools:
            wrapper = directory / tool
            wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" {tool} "$@"\n')
            wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)

    def kevlint(self, *args, **env):
        return subprocess.run(
            [sys.executable, "-m", "kevlint", *args],
            capture_output=True,
            text=True,
            env={**self.env, **env},
            cwd=self.root,
        )

    def capture(self, *extra, run="sleep 1", **env):
        args = ["capture", "--seconds", "3", "--settle", "0", "--out", self.prefix]
        if run is not None:
            args += ["--run", run]
        return self.kevlint(*args, *extra, **env)

    def calls(self, tool=None):
        path = self.root / "calls.jsonl"
        if not path.exists():
            return []
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        return [row for row in rows if tool is None or row[0] == tool]

    def assert_no_output(self):
        self.assertEqual(list(self.out.iterdir()), [])

    def assert_failed(self, done, text):
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn(text, done.stderr)
        self.assert_no_output()


class VerdictTest(FakeTargetCase):
    def test_inversion_exits_one_and_keeps_files(self):
        done = self.capture()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("FAIL: 10 violation(s) in 12 evaluated block(s)", done.stdout)
        self.assertEqual(
            Path(self.prefix + ".kev").read_bytes(), Path(INVERSION + ".kev").read_bytes()
        )
        self.assertEqual(
            Path(self.prefix + ".txt").read_bytes(), Path(INVERSION + ".txt").read_bytes()
        )

    def test_normal_exits_zero(self):
        done = self.capture(FAKE_FIXTURE=NORMAL)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("PASS", done.stdout)

    def test_verdict_matches_a_plain_check(self):
        done = self.capture()
        plain = self.kevlint("check", self.prefix + ".txt")
        self.assertEqual(plain.returncode, done.returncode)
        self.assertTrue(done.stdout.endswith(plain.stdout), done.stdout[-400:])

    def test_config_and_html_reach_the_check(self):
        html = self.root / "report.html"
        config = str(REPO / "kevlint.toml")
        done = self.capture("--config", config, "--html", str(html))
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("RESULTS BY RULE", done.stdout)
        self.assertTrue(html.exists())

    def test_bad_config_stops_before_the_target(self):
        bad = self.root / "bad.toml"
        bad.write_text("[nonsense]\n")
        done = self.capture("--config", str(bad))
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(self.calls(), [])

    def test_explain_without_llm_settings_only_notes(self):
        done = self.capture("--explain")
        self.assertEqual(done.returncode, 1)
        self.assertIn("explanations skipped", done.stdout)


class CommandsTest(FakeTargetCase):
    def test_tracelogger_is_wide_with_the_requested_seconds(self):
        self.capture()
        row = self.calls("tracelogger")[0]
        self.assertEqual(row[1:5], ["-w", "-s", "3", "-f"])

    def test_no_run_captures_alone(self):
        done = self.capture(run=None)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertNotIn("Workload output", done.stdout)
        self.assertIsNone(json.loads(Path(self.prefix + ".json").read_text())["workload_command"])

    def test_workload_is_interpreted_only_by_the_target_shell(self):
        run = """echo "a  b" 'c $d' $REMOTE_ONLY $(echo sub); echo ';|&'; sleep 1"""
        done = self.capture(run=run, REMOTE_ONLY="seen")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("a  b c $d seen sub", done.stdout)
        self.assertIn(";|&", done.stdout)

    def test_default_flag_and_environment_targets(self):
        self.capture(run=None)
        self.assertEqual(self.calls("ssh")[0][1:3], ["-p", "2222"])
        self.assertIn("root@localhost", self.calls("ssh")[0])
        self.assertEqual(self.calls("scp")[0][1:3], ["-P", "2222"])

    def test_environment_target_is_used(self):
        self.capture(run=None, KEVLINT_TARGET="me@board:2200")
        row = self.calls("ssh")[0]
        self.assertEqual(row[1:3], ["-p", "2200"])
        self.assertIn("me@board", row)

    def test_flag_beats_environment(self):
        self.capture("--target", "you@other:24", run=None, KEVLINT_TARGET="me@board:2200")
        self.assertEqual(self.calls("ssh")[0][1:3], ["-p", "24"])

    def test_remote_files_are_removed(self):
        self.capture()
        self.assertEqual(list(self.root.glob("kevlint_*")), [])


class MetadataTest(FakeTargetCase):
    def test_records_how_the_trace_was_made(self):
        self.capture(run="sleep 1; true")
        data = json.loads(Path(self.prefix + ".json").read_text())
        self.assertEqual(data["workload_command"], "sleep 1; true")
        self.assertIn("tracelogger -w -s 3 -f", data["capture_command"])
        self.assertIn("kevlint capture --seconds 3", data["kevlint_command"])
        self.assertEqual(data["kevlint_version"], "0.1.0")
        self.assertRegex(data["date"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertRegex(data["git_commit"], r"^[0-9a-f]{40}$")
        self.assertIsInstance(data["git_dirty"], bool)
        self.assertEqual(data["workload_status"], "0")


class FailureTest(FakeTargetCase):
    def test_target_unreachable(self):
        self.assert_failed(self.capture(FAKE_SSH="refused"), "target unreachable")

    def test_sshd_not_running(self):
        done = self.capture(FAKE_SSH="closed")
        self.assert_failed(done, "sshd is probably not running")
        self.assertIn("start_ssh.sh", done.stderr)

    def test_tracelogger_missing(self):
        (self.root / "remote_bin" / "tracelogger").unlink()
        self.assert_failed(self.capture(), "tracelogger was not found")

    def test_tracelogger_fails(self):
        self.assert_failed(self.capture(FAKE_TRACELOGGER="fail"), "could not start")

    def test_no_events_when_the_file_is_empty(self):
        self.assert_failed(self.capture(FAKE_TRACELOGGER="empty"), "no events")

    def test_no_events_after_decoding(self):
        self.assert_failed(self.capture(FAKE_TRACEPRINTER="noevents"), "no events")

    def test_traceprinter_fails(self):
        self.assert_failed(self.capture(FAKE_TRACEPRINTER="fail"), "bad file")

    def test_trace_much_shorter_than_requested_is_rejected(self):
        args = ["capture", "--seconds", "30", "--settle", "0", "--out", self.prefix, "--run", "sleep 1"]
        self.assert_failed(self.kevlint(*args), "covers only")
        self.assert_no_output()

    def test_scp_fails(self):
        self.assert_failed(self.capture(FAKE_SCP="fail"), "could not copy")

    def test_connection_lost_during_capture(self):
        self.assert_failed(self.capture(FAKE_SSH="lost"), "lost the connection")

    def test_workload_typo_is_not_a_pass_even_without_settling(self):
        done = self.capture(run="no_such_program_x", FAKE_FIXTURE=NORMAL)
        self.assert_failed(done, "before tracing")
        self.assertIn("no_such_program_x", done.stderr)

    def test_workload_ending_during_the_trace_is_not_a_pass(self):
        done = self.capture(run="true", FAKE_FIXTURE=NORMAL)
        self.assert_failed(done, "before tracing")

    def test_workload_gone_before_tracing_is_not_a_pass(self):
        args = ["capture", "--seconds", "3", "--settle", "1", "--out", self.prefix]
        done = self.kevlint(*args, "--run", "no_such_program_x", FAKE_FIXTURE=NORMAL)
        self.assertEqual(done.returncode, 2, done.stdout)
        self.assertIn("before tracing began", done.stderr)
        self.assertIn("no_such_program_x", done.stderr)
        self.assertEqual(self.calls("tracelogger"), [])
        self.assert_no_output()

    def test_missing_qnx_host_stops_before_the_target(self):
        self.assert_failed(self.capture(QNX_HOST=""), "qnxsdp-env.sh")
        self.assertEqual(self.calls(), [])

    def test_missing_traceprinter_stops_before_the_target(self):
        (self.root / "host_bin" / "traceprinter").unlink()
        done = self.capture()
        self.assertEqual(done.returncode, 2)
        self.assertIn("qnxsdp-env.sh", done.stderr)

    def test_bad_seconds_never_reach_tracelogger(self):
        for value in ("0", "0.0", "-1", "1e3", "nan"):
            with self.subTest(value=value):
                done = self.kevlint("capture", "--seconds", value, "--out", self.prefix)
                self.assertEqual(done.returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_empty_run_is_refused(self):
        self.assert_failed(self.capture(run="  "), "--run is empty")

    def test_bad_target(self):
        self.assert_failed(self.capture("--target=-oFoo=bar@x"), "bad target")


class OverwriteTest(FakeTargetCase):
    def test_existing_output_is_refused_and_kept(self):
        Path(self.prefix + ".txt").write_text("precious")
        done = self.capture()
        self.assertEqual(done.returncode, 2)
        self.assertIn("--force", done.stderr)
        self.assertEqual(Path(self.prefix + ".txt").read_text(), "precious")
        self.assertEqual(self.calls(), [])

    def test_force_replaces(self):
        Path(self.prefix + ".txt").write_text("old")
        done = self.capture("--force")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertNotEqual(Path(self.prefix + ".txt").read_text(), "old")

    def test_failed_capture_keeps_old_files(self):
        Path(self.prefix + ".txt").write_text("old")
        done = self.capture("--force", FAKE_TRACEPRINTER="noevents")
        self.assertEqual(done.returncode, 2)
        self.assertEqual(Path(self.prefix + ".txt").read_text(), "old")


class WarningTest(FakeTargetCase):
    def test_nonzero_workload_warns_without_changing_the_verdict(self):
        done = self.capture(run="sleep 1; exit 7", FAKE_FIXTURE=NORMAL)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("exited with status 7", done.stderr)
        self.assertEqual(json.loads(Path(self.prefix + ".json").read_text())["workload_status"], "7")

    def test_workload_that_stays_running_does_not_hang(self):
        options = Options(seconds="1", out=self.prefix, run="sleep 4", settle=0, workload_grace=0)
        with mock.patch.dict(os.environ, self.env), contextlib.redirect_stdout(io.StringIO()):
            capture(options, self.env)
        self.assertEqual(
            json.loads(Path(self.prefix + ".json").read_text())["workload_status"], "running"
        )

    def test_library_call_raises_capture_error(self):
        env = {**self.env, "QNX_HOST": ""}
        with self.assertRaises(CaptureError):
            capture(Options(seconds="1", out=self.prefix), env)


class CheckKevlintdTest(FakeTargetCase):
    def test_status_is_printed_and_recorded(self):
        done = self.capture("--check-kevlintd", FAKE_KEVLINTD="ok")
        self.assertIn("kevlintd status:", done.stdout)
        self.assertIn("markers_written: 3", done.stdout)
        recorded = json.loads(Path(self.prefix + ".json").read_text())
        self.assertIn("markers_written: 3", recorded["kevlintd_status"])

    def test_missing_kevlintd_fails_before_tracing(self):
        done = self.capture("--check-kevlintd")
        self.assertEqual(done.returncode, 2)
        self.assertIn("kevlintd is not running", done.stderr)
        self.assertIn("--check-kevlintd", done.stderr)
        self.assert_no_output()
        self.assertFalse(any("tracelogger -w" in row[-1] for row in self.calls("ssh")))

    def test_status_is_not_read_without_the_flag(self):
        done = self.capture()
        self.assertFalse(any("/dev/kevlint" in row[-1] for row in self.calls("ssh")))
        self.assertNotIn("kevlintd", Path(self.prefix + ".json").read_text())
        self.assertEqual(done.returncode, 1)
