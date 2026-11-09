"""Rules and parsing against real traces of ordinary QNX activity, with no demo workload."""
import unittest
from pathlib import Path
from types import SimpleNamespace

from kevlint.inversion import _scan, check
from kevlint.parser import Event, Trace, parse_trace
from kevlint.report import markers
from kevlint.rules import collect_waits

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


if __name__ == "__main__":
    unittest.main()
