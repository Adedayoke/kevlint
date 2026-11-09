"""Rules and parsing against real traces of ordinary QNX activity, with no demo workload."""
import unittest
from pathlib import Path

from kevlint.inversion import check
from kevlint.parser import parse_trace

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"


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


if __name__ == "__main__":
    unittest.main()
