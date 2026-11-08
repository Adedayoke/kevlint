import unittest
from pathlib import Path

from kevlint.inversion import check
from kevlint.parser import parse_trace

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def load(name):
    path = FIXTURES / f"{name}.txt"
    trace = parse_trace(path)
    return trace, check(trace), path.read_text(errors="replace").splitlines()


class NormalTraceTest(unittest.TestCase):
    def test_no_violations(self):
        _, result, _ = load("demo_normal")
        self.assertEqual(result.violations, [])


class FixedTraceTest(unittest.TestCase):
    def test_no_violations(self):
        _, result, _ = load("demo_fixed")
        self.assertEqual(result.violations, [])

    def test_the_contended_mutex_blocks_were_evaluated(self):
        trace, result, _ = load("demo_fixed")
        rt = next(t for t, n in trace.thread_names.items() if n == "fusion_rt")
        blocks = [e for e in trace.events if e.name == "THMUTEX" and e.thread == rt]
        self.assertEqual(len(blocks), 11)
        self.assertGreaterEqual(result.evaluated, 11)


class InversionTraceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trace, cls.result, cls.lines = load("demo_inversion")
        cls.name = staticmethod(lambda thread: cls.trace.thread_names[thread])

    def test_every_completed_block_is_reported(self):
        self.assertEqual(len(self.result.violations), 10)

    def test_blocks_open_at_end_of_trace_are_not_evaluated(self):
        # fusion_rt's final block, and the system thread iousb_poll_tq.
        self.assertEqual(
            self.result.not_evaluated["block still open when the trace ended"], 2
        )

    def test_threads_and_priorities(self):
        for v in self.result.violations:
            self.assertEqual(self.name(v.waiter), "fusion_rt")
            self.assertEqual(self.name(v.holder), "fusion_holder")
            self.assertEqual(self.name(v.middle), "fusion_hog")
            self.assertEqual(
                (v.waiter_priority, v.middle_priority, v.holder_priority), (50, 30, 10)
            )

    def test_block_times_are_ordered(self):
        for v in self.result.violations:
            self.assertLess(v.start_cycles, v.end_cycles)
            self.assertGreaterEqual(self.trace.seconds(v.start_cycles), 0)

    def test_evidence_lines_point_at_the_right_events(self):
        for v in self.result.violations:
            self.assertIn(":SYNC_SEM_WAIT/85", self.lines[v.wait_call_line - 1])
            self.assertIn(":THSEM", self.lines[v.block_start_line - 1])
            self.assertIn(":THRUNNING", self.lines[v.middle_line - 1])
            self.assertIn(":SYNC_SEM_POST/84", self.lines[v.release_line - 1])
            self.assertIn(":THREADY", self.lines[v.block_end_line - 1])
            self.assertLess(v.block_start_line, v.middle_line)
            self.assertLess(v.middle_line, v.block_end_line)


if __name__ == "__main__":
    unittest.main()
