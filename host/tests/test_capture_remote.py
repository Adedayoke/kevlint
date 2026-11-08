import unittest

from kevlint.capture import (
    CaptureError,
    Session,
    Target,
    check_report,
    parse_report,
    remote_script,
    ssh_problem,
)
from tests.fake_target import SSH_ERRORS

TARGET = Target("root", "localhost", 2222)


class SshProblemTest(unittest.TestCase):
    def test_port_that_drops_us_means_sshd_is_down(self):
        for mode in ("closed", "reset", "banner"):
            with self.subTest(mode=mode):
                message = ssh_problem(TARGET, SSH_ERRORS[mode])
                self.assertIn("sshd is probably not running", message)
                self.assertIn("sh /data/home/root/start_ssh.sh", message)

    def test_refused_says_start_qemu(self):
        message = ssh_problem(TARGET, SSH_ERRORS["refused"])
        self.assertIn("target unreachable", message)
        self.assertIn("./run.sh", message)

    def test_timeout_is_unreachable(self):
        self.assertIn("target unreachable", ssh_problem(TARGET, SSH_ERRORS["timeout"]))

    def test_login_and_host_key_are_named(self):
        self.assertIn("rejected the login", ssh_problem(TARGET, SSH_ERRORS["denied"]))
        self.assertIn("ssh-keygen -R", ssh_problem(TARGET, SSH_ERRORS["hostkey"]))

    def test_unknown_error_keeps_ssh_text(self):
        self.assertIn("strange thing", ssh_problem(TARGET, "strange thing\n"))


class CommandLineTest(unittest.TestCase):
    def test_ssh_never_prompts_and_uses_the_port(self):
        argv = Session(TARGET).ssh_argv("true")
        self.assertEqual(argv[:3], ["ssh", "-p", "2222"])
        self.assertIn("BatchMode=yes", argv)
        self.assertEqual(argv[-2:], ["root@localhost", "true"])

    def test_scp_uses_capital_p(self):
        argv = Session(TARGET).scp_argv("/tmp/a.kev", "local.kev")
        self.assertEqual(argv[:3], ["scp", "-P", "2222"])
        self.assertEqual(argv[-2:], ["root@localhost:/tmp/a.kev", "local.kev"])

    def test_script_always_uses_wide_mode(self):
        script = remote_script(None, "3", 2, "/tmp/a.kev", "/tmp/a.log", 30)
        self.assertIn("tracelogger -w -s 3 -f /tmp/a.kev", script)

    def test_script_without_workload_does_not_wait(self):
        script = remote_script(None, "3", 2, "/tmp/a.kev", "/tmp/a.log", 30)
        self.assertNotIn("sleep", script)

    def test_workload_is_quoted_as_one_word(self):
        script = remote_script("a 'b'; c", "3", 0, "/tmp/a.kev", "/tmp/a.log", 30)
        self.assertIn("""sh -c 'a '"'"'b'"'"'; c' </dev/null""", script)


class ReportTest(unittest.TestCase):
    def test_workload_cannot_forge_a_status(self):
        stdout = (
            "@kevlint tracelogger_status 0\n@kevlint kev_present 1\n@kevlint log_begin\n"
            "@kevlint tracelogger_status 9\n@kevlint log_end\nmore\n\n@kevlint log_end\n"
        )
        report = parse_report(stdout)
        self.assertEqual(report.status["tracelogger_status"], "0")
        self.assertEqual(
            report.log, ["@kevlint tracelogger_status 9", "@kevlint log_end", "more"]
        )

    def test_early_workload_exit_is_an_error_with_its_output(self):
        report = parse_report(
            "@kevlint workload_before_capture 126\n@kevlint log_begin\nsh: typo: not found\n@kevlint log_end\n"
        )
        with self.assertRaisesRegex(CaptureError, "(?s)before tracing began.*126.*typo"):
            check_report(report)

    def test_tracelogger_failure_shows_its_output(self):
        report = parse_report("no kernel buffers\n\n@kevlint tracelogger_status 1\n")
        with self.assertRaisesRegex(CaptureError, "status 1: no kernel buffers"):
            check_report(report)

    def test_no_data_means_no_events(self):
        report = parse_report("@kevlint tracelogger_status 0\n@kevlint kev_present 0\n")
        with self.assertRaisesRegex(CaptureError, "no events"):
            check_report(report)

    def test_warnings(self):
        report = parse_report(
            "@kevlint tracelogger_status 0\n@kevlint kev_present 1\n"
            "@kevlint alive_after_capture 0\n@kevlint workload_status 4\n"
        )
        warnings = check_report(report)
        self.assertTrue(any("partly covers" in w for w in warnings))
        self.assertTrue(any("status 4" in w for w in warnings))

    def test_clean_run_has_no_warnings(self):
        report = parse_report(
            "@kevlint tracelogger_status 0\n@kevlint kev_present 1\n"
            "@kevlint alive_after_capture 1\n@kevlint workload_status 0\n"
        )
        self.assertEqual(check_report(report), [])
