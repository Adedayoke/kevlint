"""Rules and parsing against real traces of ordinary QNX activity, with no demo workload."""
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from kevlint.capture import check_span, trace_span, CaptureError
from kevlint.config import Budget, Config
from kevlint.inversion import UNCHECKED_LOCK_STATES, _qualifying_runs, _scan, check, unchecked_lock_reason
from kevlint.parser import Event, Trace, parse_trace
from kevlint.report import markers
from kevlint.rules import STARTED_WITHIN_BUDGET, collect_waits, run_rules

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
NO_RELEASE = "no visible release of the sync object"
SYNC = "0x0000000000001000"
HOLDER = (10, 1)
WAITER = (10, 2)


def fixture(name):
    return parse_trace(FIXTURES / f"{name}.txt")


class ParsingTest(unittest.TestCase):
    def test_system_fixtures_parse_and_have_priorities(self):
        for name in ("system_idle", "system_busy"):
            trace = fixture(name)
            self.assertTrue(trace.events)
            self.assertTrue(any(e.priority is not None for e in trace.events))

    def test_all_states_in_the_busy_trace_are_kept(self):
        names = {e.name for e in fixture("system_busy").events if e.cls == "THREAD"}
        self.assertTrue({"THRWLOCK_WRITE", "THMUON_MUTEX", "THTRACEBUFFER", "THDESTROY"} <= names)


def lock_block_counts(trace):
    """Count entries to each unchecked lock state, directly from the THREAD events."""
    state = {}
    counts = Counter()
    for event in trace.events:
        if event.cls != "THREAD" or event.name == "THCREATE":
            continue
        if event.name in UNCHECKED_LOCK_STATES and state.get(event.thread) != event.name:
            counts[event.name] += 1
        state[event.thread] = event.name
    return counts


class UncheckedLockStatesTest(unittest.TestCase):
    def test_real_trace_counts_each_state_as_not_evaluated(self):
        trace = fixture("system_busy")
        result = check(trace)
        counts = lock_block_counts(trace)
        self.assertGreater(counts["THRWLOCK_WRITE"], 0)
        self.assertGreater(counts["THMUON_MUTEX"], 0)
        for state, count in counts.items():
            self.assertEqual(result.not_evaluated[unchecked_lock_reason(state)], count, state)

    def test_read_lock_is_counted_too(self):
        def state(line, name):
            return Event(line, line, 0, "THREAD", name, {"pid": "7", "tid": "1", "priority": "21"})

        events = [state(1, "THRUNNING"), state(2, "THRWLOCK_READ"), state(3, "THREADY")]
        result = check(Trace(1000, 1, events, {}))
        self.assertEqual(result.not_evaluated[unchecked_lock_reason("THRWLOCK_READ")], 1)
        self.assertEqual(result.evaluated, 0)

    def test_they_never_produce_a_finding_or_change_the_verdict(self):
        self.assertEqual(check(fixture("system_busy")).violations, [])

    def test_below_the_minimum_they_are_excluded_not_a_gap(self):
        result = check(fixture("system_busy"), 40)
        self.assertNotIn(unchecked_lock_reason("THRWLOCK_WRITE"), result.not_evaluated)
        self.assertGreater(result.excluded, 0)

    def test_budget_reports_them_only_for_its_own_thread(self):
        trace = fixture("system_busy")
        reason = "blocked in THRWLOCK_WRITE, not in this budget's states"
        rule = run_rules(trace, Config("sample", budgets=[Budget("resmgr chan 1", 100)]))[-1]
        self.assertEqual(rule.not_evaluated[reason], 119)
        self.assertEqual(rule.findings, [])
        other = run_rules(trace, Config("sample", budgets=[Budget("fsys_resmgr", 1000)]))[-1]
        self.assertEqual(other.not_evaluated[reason], 0)

    def test_a_state_opted_in_to_a_budget_is_measured_instead(self):
        trace = fixture("system_busy")
        budget = Budget("virtio_pci0 irq 257", 1000, states=("THMUON_MUTEX",))
        rule = run_rules(trace, Config("sample", budgets=[budget]))[-1]
        self.assertEqual(rule.not_evaluated["blocked in THMUON_MUTEX, not in this budget's states"], 0)
        self.assertEqual(rule.budgets[0].evaluated, 3)

    def test_terminal_and_html_state_the_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "r.html"
            done = subprocess.run(
                [
                    sys.executable, "-m", "kevlint", "check", str(FIXTURES / "system_busy.txt"),
                    "--html", str(report),
                ],
                capture_output=True,
                text=True,
                env={"PYTHONPATH": str(ROOT / "host")},
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            reason = unchecked_lock_reason("THRWLOCK_WRITE")
            self.assertIn(f"not evaluated: 257 ({reason})", done.stdout)
            self.assertIn(reason, report.read_text(encoding="utf-8"))


class VerdictTest(unittest.TestCase):
    def test_no_inversion_on_ordinary_system_activity(self):
        for name in ("system_idle", "system_busy"):
            self.assertEqual(check(fixture(name)).violations, [])


if __name__ == "__main__":
    unittest.main()


class ReleaseBeforeBlockEventTest(unittest.TestCase):
    """On another CPU, the unlock can be logged before the waiter's THMUTEX event."""

    def test_real_trace_has_such_blocks_and_counts_them_as_evaluated(self):
        trace = fixture("system_busy")
        blocks, _, _ = _scan(trace)
        early = [b for b in blocks if b.release is not None and b.release.cycles < b.start.cycles]
        self.assertTrue(early, "fixture should contain a release before a block event")
        self.assertNotIn(NO_RELEASE, check(trace).not_evaluated)

    def test_release_after_the_wait_call_is_the_holder(self):
        block = self.blocked_after(unlock_before_call=False)
        self.assertEqual(block.holder, HOLDER)

    def test_release_before_the_wait_call_is_not_the_holder(self):
        block = self.blocked_after(unlock_before_call=True)
        self.assertIsNone(block.release)

    @staticmethod
    def blocked_after(unlock_before_call):
        def thread(line, cycles, cpu, name, who, priority=10):
            fields = {"pid": str(who[0]), "tid": str(who[1]), "priority": str(priority)}
            return Event(line, cycles, cpu, "THREAD", name, fields)

        def call(line, cycles, cpu, name):
            return Event(line, cycles, cpu, "KER_CALL", name, {"sync_p": SYNC})

        unlock = call(0, 15, 1, "SYNC_MUTEX_UNLOCK/81")
        lock = call(0, 25, 0, "SYNC_MUTEX_LOCK/80")
        ordered = [unlock, lock] if unlock_before_call else [lock, unlock]
        events = [thread(1, 1, 0, "THRUNNING", WAITER), thread(2, 2, 1, "THRUNNING", HOLDER)]
        for number, event in enumerate(ordered, 3):
            event.line, event.cycles = number, number * 10
            events.append(event)
        events += [thread(5, 50, 0, "THMUTEX", WAITER), thread(6, 60, 0, "THREADY", WAITER)]
        trace = Trace(1000, 1, events, {})
        return _scan(trace)[0][0]


class ReleaseMarkerTest(unittest.TestCase):
    def test_a_release_before_the_block_stays_on_the_timeline(self):
        item = SimpleNamespace(
            start_cycles=100,
            block_start_line=2,
            middle_cycles=None,
            middle_line=None,
            release_cycles=90,
            release_line=1,
            end_cycles=120,
            block_end_line=3,
        )
        labels = {row[1]: row[2] for row in markers(item)}
        self.assertEqual(labels["release"], 100)


class StatusEventsTest(unittest.TestCase):
    """THCREATE says a thread exists, so it must not end a block."""

    def test_no_block_or_wait_ends_at_thcreate(self):
        for name in ("system_idle", "system_busy"):
            trace = fixture(name)
            for block in _scan(trace)[0]:
                self.assertTrue(block.end is None or block.end.name != "THCREATE", block)
            for wait in collect_waits(trace):
                self.assertTrue(wait.end is None or wait.end.name != "THCREATE", wait)

    def test_block_continues_across_the_state_dump(self):
        trace = fixture("system_idle")
        waits = [w for w in collect_waits(trace) if w.thread == (71454742, 4)]
        self.assertEqual((waits[0].start.line, waits[0].end.line), (204, 787))


class BlockedBeforeTheTraceTest(unittest.TestCase):
    def rule(self, max_ms):
        config = Config("sample", budgets=[Budget("softirq_0", max_ms)])
        return run_rules(fixture("system_idle"), config)[-1]

    def test_over_budget_is_a_lower_bound_breach(self):
        rule = self.rule(100)
        self.assertEqual(len(rule.findings), 1)
        self.assertTrue(rule.findings[0].start_unknown)
        self.assertIsNone(rule.findings[0].end_cycles)
        self.assertTrue(rule.budgets[0].blocks[0].start_unknown)

    def test_within_budget_is_not_evaluated(self):
        rule = self.rule(10_000)
        self.assertEqual(rule.findings, [])
        self.assertEqual(rule.not_evaluated[STARTED_WITHIN_BUDGET], 1)
        self.assertEqual(rule.evaluated, 0)

    def test_start_unknown_only_when_the_thread_has_no_earlier_state(self):
        for name in ("system_idle", "system_busy"):
            trace = fixture(name)
            earlier = {}
            for event in trace.events:
                if event.cls == "THREAD" and event.name != "THCREATE":
                    earlier.setdefault(event.thread, event.line)
            for wait in collect_waits(trace):
                self.assertEqual(wait.start_unknown, earlier[wait.thread] == wait.start.line, wait)

    def test_output_says_it_is_a_lower_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "rules.toml"
            config.write_text(
                '[rules.max-blocked-time]\nbudgets = [{thread = "softirq_0", max_ms = 100}]\n',
                encoding="utf-8",
            )
            report = Path(tmp) / "r.html"
            done = subprocess.run(
                [
                    sys.executable, "-m", "kevlint", "check", str(FIXTURES / "system_idle.txt"),
                    "--config", str(config), "--html", str(report),
                ],
                capture_output=True,
                text=True,
                env={"PYTHONPATH": str(ROOT / "host")},
            )
            self.assertEqual(done.returncode, 1, done.stderr)
            self.assertIn("at least 4991.486 ms (blocked for the whole trace)", done.stdout)
            self.assertIn("blocked for the whole trace", report.read_text(encoding="utf-8"))


class WindowTest(unittest.TestCase):
    def test_only_runs_inside_the_block_are_considered(self):
        for name in ("demo_inversion", "system_busy"):
            trace = fixture(name)
            blocks, runs, history = _scan(trace)
            indices = [r.index for r in runs]
            for block in blocks:
                if block.end is None or block.holder is None:
                    continue
                inside = [r for r in runs if block.index < r.index < block.end_index]
                want = list(_qualifying_runs(block, inside, [r.index for r in inside], history))
                self.assertEqual(list(_qualifying_runs(block, runs, indices, history)), want)


class CaptureSpanTest(unittest.TestCase):
    def test_span_of_a_real_three_second_trace(self):
        self.assertAlmostEqual(trace_span(FIXTURES / "demo_fixed.txt"), 3.0, delta=0.1)

    def test_real_traces_are_not_rejected_as_short(self):
        check_span(FIXTURES / "demo_normal.txt", "3")
        check_span(FIXTURES / "system_idle.txt", "5")

    def test_short_trace_says_what_to_check(self):
        with self.assertRaises(CaptureError) as caught:
            check_span(FIXTURES / "system_idle.txt", "30")
        self.assertIn("covers only", str(caught.exception))
        self.assertIn("df -h /tmp", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
