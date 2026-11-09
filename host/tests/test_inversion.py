import unittest
from pathlib import Path

from kevlint.inversion import _qualifying_runs, _scan, boost_text, check
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


class MinimumWaiterPriorityTest(unittest.TestCase):
    def setUp(self):
        self.trace = parse_trace(FIXTURES / "demo_inversion.txt")

    def test_no_minimum_is_the_existing_behavior(self):
        self.assertEqual(check(self.trace, None), check(self.trace))

    def test_minimum_below_the_waiter_keeps_every_violation(self):
        self.assertEqual(len(check(self.trace, 50).violations), 10)

    def test_minimum_above_the_waiter_drops_them_and_reports_why(self):
        result = check(self.trace, 51)
        self.assertEqual(result.violations, [])
        self.assertEqual(result.evaluated, 0)
        self.assertGreater(result.excluded, 0)
        self.assertNotIn("waiter below the configured minimum priority", result.not_evaluated)
        self.assertIsNone(result.not_evaluated_max_priority)

    def test_every_block_is_still_counted(self):
        plain = check(self.trace)
        filtered = check(self.trace, 40)
        self.assertEqual(
            plain.evaluated + sum(plain.not_evaluated.values()),
            filtered.evaluated + sum(filtered.not_evaluated.values()) + filtered.excluded,
        )


class HolderEvidenceLinesTest(unittest.TestCase):
    def test_first_violation_records_the_holder_lines(self):
        _, result, lines = load("demo_inversion")
        first = result.violations[0]
        self.assertEqual((first.holder_run_line, first.holder_ready_line), (1178, 1187))
        self.assertIn(":THRUNNING", lines[first.holder_run_line - 1])
        self.assertIn(":THREADY", lines[first.holder_ready_line - 1])

    def test_first_violation_records_when_the_middle_became_ready(self):
        _, result, lines = load("demo_inversion")
        first = result.violations[0]
        self.assertEqual(first.middle_ready_line, 1184)
        self.assertLess(first.middle_ready_line, first.holder_ready_line)

    def test_middle_ready_lines_are_the_middles_own_thready_events(self):
        _, result, lines = load("demo_inversion")
        for v in result.violations:
            self.assertLess(v.middle_ready_line, v.middle_line)
            text = lines[v.middle_ready_line - 1]
            self.assertIn(":THREADY", text)
            self.assertIn(f"pid:{v.middle[0]} tid:{v.middle[1]} ", text)

    def test_every_violation_points_at_the_holders_own_events(self):
        _, result, lines = load("demo_inversion")
        for v in result.violations:
            wanted = f"pid:{v.holder[0]} tid:{v.holder[1]} "
            self.assertIn(wanted, lines[v.holder_run_line - 1])
            self.assertIn(wanted, lines[v.holder_ready_line - 1])


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


class ResolvedWaitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trace, cls.result, cls.lines = load("demo_fixed")
        cls.worst = max(cls.result.resolved, key=lambda r: r.duration_cycles)

    def name(self, thread):
        return self.trace.thread_names[thread]

    def test_every_contended_mutex_block_is_listed(self):
        self.assertEqual(len(self.result.resolved), 11)

    def test_worst_wait(self):
        self.assertEqual(self.name(self.worst.waiter), "fusion_rt")
        self.assertEqual(self.name(self.worst.holder), "fusion_holder")
        seconds = self.worst.duration_cycles / self.trace.cycles_per_sec
        self.assertAlmostEqual(seconds, 0.02428, places=4)

    def test_base_priority_comes_from_before_the_boost(self):
        for wait in self.result.resolved:
            self.assertEqual(wait.holder_base_priority, 10)
            self.assertEqual(wait.holder_peak_priority, 50)

    def test_middle_stayed_ready(self):
        for wait in self.result.resolved:
            self.assertEqual(self.name(wait.middle), "fusion_hog")
            self.assertEqual(wait.middle_priority, 30)
            self.assertFalse(wait.middle_ran)

    def test_evidence_lines_point_at_the_right_events(self):
        for wait in self.result.resolved:
            self.assertIn(":SYNC_MUTEX_LOCK/80", self.lines[wait.wait_call_line - 1])
            self.assertIn(":THMUTEX", self.lines[wait.block_start_line - 1])
            self.assertIn(":THREADY", self.lines[wait.middle_line - 1])
            self.assertIn(":SYNC_MUTEX_UNLOCK/81", self.lines[wait.release_line - 1])

    def test_boost_wording(self):
        self.assertEqual(boost_text(self.worst), "boosted 10 → 50 (priority inheritance)")
        self.assertEqual(
            boost_text(self.worst, arrow="->"), "boosted 10 -> 50 (priority inheritance)"
        )

    def test_no_resolved_waits_elsewhere(self):
        for name in ("demo_normal", "demo_inversion"):
            self.assertEqual(load(name)[1].resolved, [])


class MiddleMustDelayHolderTest(unittest.TestCase):
    """A middle thread counts only on the holder's CPU, with the holder in THREADY."""

    def setUp(self):
        self.trace = parse_trace(FIXTURES / "demo_inversion.txt")

    def thread(self, name):
        return next(t for t, n in self.trace.thread_names.items() if n == name)

    def middles(self):
        return {self.trace.thread_names[v.middle] for v in check(self.trace).violations}

    def test_thread_on_another_cpu_is_not_counted(self):
        blocks, runs, history = _scan(self.trace)
        rt = self.thread("fusion_rt")
        blocks = [b for b in blocks if b.waiter == rt and b.end and b.release]
        pipeline = {self.thread("sensor_b"), self.thread("fusion_server")}
        in_priority_window = [
            r
            for b in blocks
            for r in runs
            if b.index < r.index < b.end_index
            and r.thread in pipeline
            and r.event.cpu == 1
            and 10 < r.priority < 50
        ]
        self.assertTrue(in_priority_window, "fixture should have such runs")
        counted = {r.thread for b in blocks for r, _ in _qualifying_runs(b, runs, [r.index for r in runs], history)}
        self.assertFalse(counted & pipeline)

    def test_middle_moved_to_another_cpu_stops_counting(self):
        hog = self.thread("fusion_hog")
        self.assertIn("fusion_hog", self.middles())
        for event in self.trace.events:
            if event.cls == "THREAD" and event.name == "THRUNNING" and event.thread == hog:
                event.cpu = 1
        self.assertNotIn("fusion_hog", self.middles())

    def test_holder_not_in_thready_stops_counting(self):
        holder = self.thread("fusion_holder")
        self.assertIn("fusion_hog", self.middles())
        self.trace.events = [
            e
            for e in self.trace.events
            if not (e.cls == "THREAD" and e.name == "THREADY" and e.thread == holder)
        ]
        self.assertNotIn("fusion_hog", self.middles())


if __name__ == "__main__":
    unittest.main()
