import unittest
from pathlib import Path

from kevlint.inversion import check
from kevlint.parser import parse_trace
from kevlint.patterns import group_patterns
from kevlint.timeline import BLOCKED, READY, RUNNING, category, thread_segments

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


class CategoryTest(unittest.TestCase):
    def test_categories(self):
        self.assertEqual(category("THRUNNING"), RUNNING)
        self.assertEqual(category("THREADY"), READY)
        for state in ("THSEM", "THMUTEX", "THRECEIVE", "THINTR", "THNANOSLEEP"):
            self.assertEqual(category(state), BLOCKED)


class WorstBlockTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trace = parse_trace(FIXTURES / "demo_inversion.txt")
        cls.patterns = group_patterns(check(cls.trace).violations)
        cls.worst = cls.patterns[0].worst

    def segments(self, thread):
        return thread_segments(
            self.trace, thread, self.worst.start_cycles, self.worst.end_cycles
        )

    def test_grouped_into_one_pattern(self):
        self.assertEqual(len(self.patterns), 1)
        self.assertEqual(len(self.patterns[0].occurrences), 10)

    def test_worst_block_duration(self):
        seconds = self.worst.duration_cycles / self.trace.cycles_per_sec
        self.assertAlmostEqual(seconds, 0.2491, places=3)

    def test_occurrences_stay_in_trace_order(self):
        starts = [v.start_cycles for v in self.patterns[0].occurrences]
        self.assertEqual(starts, sorted(starts))

    def test_waiter_is_blocked_for_the_whole_window(self):
        segments = self.segments(self.worst.waiter)
        self.assertEqual([s.state for s in segments], ["THSEM"])
        self.assertEqual(segments[0].category, BLOCKED)

    def test_holder_starts_ready_and_ends_running(self):
        segments = self.segments(self.worst.holder)
        self.assertEqual(segments[0].state, "THREADY")
        self.assertEqual(segments[-1].state, "THRUNNING")
        self.assertEqual({s.cpu for s in segments if s.category == RUNNING}, {0})

    def test_middle_mostly_runs_then_blocks(self):
        segments = self.segments(self.worst.middle)
        self.assertEqual(segments[0].state, "THRECEIVE")
        self.assertEqual(segments[-1].state, "THRECEIVE")
        running = sum(
            s.end_cycles - s.start_cycles for s in segments if s.category == RUNNING
        )
        self.assertGreater(running, self.worst.duration_cycles * 0.9)

    def test_segments_cover_the_window_without_gaps(self):
        for thread in (self.worst.waiter, self.worst.holder, self.worst.middle):
            segments = self.segments(thread)
            self.assertEqual(segments[0].start_cycles, self.worst.start_cycles)
            self.assertEqual(segments[-1].end_cycles, self.worst.end_cycles)
            for before, after in zip(segments, segments[1:]):
                self.assertEqual(before.end_cycles, after.start_cycles)

    def test_segment_lines_point_at_the_trace(self):
        lines = (FIXTURES / "demo_inversion.txt").read_text(errors="replace").splitlines()
        for segment in self.segments(self.worst.middle):
            self.assertIn(f":{segment.state}", lines[segment.line - 1])


if __name__ == "__main__":
    unittest.main()
