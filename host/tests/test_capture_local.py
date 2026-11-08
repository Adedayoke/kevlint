import tempfile
import unittest
from pathlib import Path

from kevlint.capture import (
    CaptureError,
    check_host_tools,
    check_outputs,
    output_paths,
    parse_target,
    resolve_target,
)


class TargetTest(unittest.TestCase):
    def test_full_target(self):
        target = parse_target("root@192.168.1.5:2200")
        self.assertEqual((target.user, target.host, target.port), ("root", "192.168.1.5", 2200))
        self.assertEqual(target.destination, "root@192.168.1.5")

    def test_no_port_uses_ssh_default(self):
        self.assertEqual(parse_target("root@board").port, 22)

    def test_default_is_the_qemu_target(self):
        self.assertEqual(str(resolve_target(None, {})), "root@localhost:2222")

    def test_environment_overrides_default(self):
        env = {"KEVLINT_TARGET": "me@box:23"}
        self.assertEqual(str(resolve_target(None, env)), "me@box:23")

    def test_flag_overrides_environment(self):
        env = {"KEVLINT_TARGET": "me@box:23"}
        self.assertEqual(str(resolve_target("you@other:24", env)), "you@other:24")

    def test_rejects_values_ssh_would_read_as_options(self):
        for text in ("-oProxyCommand=x@host", "-x@host", "root@-host", "root", "@host", ""):
            with self.subTest(text=text), self.assertRaises(CaptureError):
                parse_target(text or "x y")

    def test_rejects_bad_ports(self):
        for text in ("root@h:0", "root@h:70000", "root@h:abc"):
            with self.subTest(text=text), self.assertRaises(CaptureError):
                parse_target(text)


class OutputTest(unittest.TestCase):
    def test_suffixes_are_appended_so_dots_survive(self):
        self.assertEqual(output_paths("a/v1.2")["kev"], "a/v1.2.kev")

    def test_refuses_existing_output(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = output_paths(f"{folder}/name")
            Path(paths["txt"]).write_text("old")
            with self.assertRaisesRegex(CaptureError, "--force"):
                check_outputs(paths, False)
            check_outputs(paths, True)

    def test_missing_directory(self):
        with self.assertRaisesRegex(CaptureError, "does not exist"):
            check_outputs(output_paths("/no/such/dir/name"), False)


class HostToolsTest(unittest.TestCase):
    def test_missing_qnx_host_says_how_to_fix(self):
        with self.assertRaisesRegex(CaptureError, "QNX_HOST.*qnxsdp-env.sh"):
            check_host_tools({"PATH": "/usr/bin"})

    def test_missing_traceprinter_says_how_to_fix(self):
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaisesRegex(CaptureError, "traceprinter.*qnxsdp-env.sh"):
                check_host_tools({"QNX_HOST": "/x", "PATH": empty})
