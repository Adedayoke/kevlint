import unittest
from pathlib import Path

from kevlint.parser import TraceError, parse_trace

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


class ParserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = FIXTURES / "demo_inversion.txt"
        cls.trace = parse_trace(cls.path)
        cls.lines = cls.path.read_text(errors="replace").splitlines()

    def test_header_clock(self):
        wanted = next(l for l in self.lines if "TRACE_CYCLES_PER_SEC" in l).split()[-1]
        self.assertEqual(self.trace.cycles_per_sec, int(wanted))

    def test_keeps_only_thread_and_kernel_calls(self):
        self.assertEqual({e.cls for e in self.trace.events}, {"THREAD", "KER_CALL"})

    def test_event_count_matches_the_file(self):
        wanted = sum(
            1 for l in self.lines if " THREAD " in l[:60] or " KER_CALL " in l[:60]
        )
        self.assertEqual(len(self.trace.events), wanted)

    def test_line_numbers_point_at_the_event(self):
        for event in self.trace.events[:500]:
            text = self.lines[event.line - 1]
            self.assertIn(f":{event.name}", text)
            self.assertTrue(text.startswith(f"t:{event.cycles:#x} "))

    def test_multiline_sync_pointer(self):
        wait = next(e for e in self.trace.events if e.name == "SYNC_SEM_WAIT/85")
        self.assertRegex(wait.fields["sync_p"], r"^0x[0-9a-f]+$")

    def test_thread_fields(self):
        running = next(e for e in self.trace.events if e.name == "THRUNNING")
        self.assertIsInstance(running.priority, int)
        self.assertEqual(len(running.thread), 2)

    def test_thread_names(self):
        self.assertIn("fusion_rt", self.trace.thread_names.values())
        self.assertIn("idle_cpu_0", self.trace.thread_names.values())

    def test_non_wide_trace_is_rejected(self):
        with self.assertRaisesRegex(TraceError, "tracelogger -w"):
            parse_trace(FIXTURES / "test.txt")

    def test_non_trace_is_rejected(self):
        with self.assertRaises(TraceError):
            parse_trace(FIXTURES.parent / "CLAUDE.md")


if __name__ == "__main__":
    unittest.main()
