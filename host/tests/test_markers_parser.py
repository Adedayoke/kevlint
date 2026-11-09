import tempfile
import unittest
from pathlib import Path

from kevlint.parser import parse_marker, parse_trace

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures"
LENGTHS = FIXTURES / "kevlintd_lengths.txt"
MAX_LENGTH = 128


class EveryLengthTest(unittest.TestCase):
    """The real capture of one marker of each length, written through kevlintd."""

    @classmethod
    def setUpClass(cls):
        cls.trace = parse_trace(LENGTHS)

    def test_every_length_decodes_exactly(self):
        by_length = {len(m.text): m.text for m in self.trace.markers}
        self.assertEqual(len(self.trace.markers), MAX_LENGTH)
        for length in range(1, MAX_LENGTH + 1):
            with self.subTest(length=length):
                self.assertEqual(by_length.get(length), chr(ord("a") + length % 26) * length)

    def test_nothing_was_undecoded(self):
        self.assertEqual(self.trace.undecoded_markers, 0)

    def test_padding_is_really_in_the_capture(self):
        raw = LENGTHS.read_text(errors="replace")
        self.assertEqual(sum(1 for line in raw.splitlines() if ' :EVENT:100' in line and ' ", d0:' in line), 16)

    def test_writer_and_line(self):
        marker = self.trace.markers[0]
        self.assertEqual(self.trace.markers[0].thread[1], 1)
        line = LENGTHS.read_text(errors="replace").splitlines()[marker.line - 1]
        self.assertIn("USREVENT", line)


class FixtureTest(unittest.TestCase):
    def test_old_fixtures_have_no_markers(self):
        for name in ("demo_normal", "demo_inversion", "demo_fixed", "test_wide"):
            with self.subTest(name=name):
                trace = parse_trace(FIXTURES / f"{name}.txt")
                self.assertEqual(trace.markers, [])
                self.assertEqual(trace.undecoded_markers, 0)

    def test_markers_do_not_become_events(self):
        trace = parse_trace(FIXTURES / "demo_markers_inversion.txt")
        self.assertTrue(trace.markers)
        self.assertFalse(any(e.cls == "USREVENT" for e in trace.events))


class ParseMarkerTest(unittest.TestCase):
    def parse(self, rest):
        return parse_marker(10, 20, 1, rest)

    def test_text_with_quotes(self):
        marker = self.parse('STR:"pid:1 tid:2 say "hi" now", d0:0x1 d1:0x2')
        self.assertEqual(marker.text, 'say "hi" now')

    def test_text_that_looks_like_the_end_of_the_string(self):
        marker = self.parse('STR:"pid:1 tid:2 a", d0:b", d0:0x1')
        self.assertEqual(marker.text, 'a", d0:b')

    def test_text_that_looks_like_fields_keeps_the_real_writer(self):
        marker = self.parse('STR:"pid:1 tid:2 pid:7 tid:8", d0:0x1')
        self.assertEqual(marker.thread, (1, 2))
        self.assertEqual(marker.text, "pid:7 tid:8")

    def test_padding_is_removed(self):
        self.assertEqual(self.parse('STR:"pid:1 tid:2 abc ", d0:0x1').text, "abc")

    def test_unprintable_text_is_replaced(self):
        self.assertEqual(self.parse('STR:"pid:1 tid:2 a\x1b[2Jb", d0:0x1').text, "a?[2Jb")

    def test_event_without_text_is_not_a_marker(self):
        self.assertIsNone(self.parse(", d0:0x61616161 d1:0x61616161"))


class UndecodedTest(unittest.TestCase):
    def parse_with(self, line):
        text = (FIXTURES / "demo_normal.txt").read_text(errors="replace").splitlines()
        first = next(i for i, row in enumerate(text) if row.startswith("t:0x"))
        text.insert(first + 1, line)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "trace.txt"
            path.write_text("\n".join(text) + "\n")
            return parse_trace(path)

    def test_marker_without_text_is_counted(self):
        trace = self.parse_with("t:0x1 CPU:00 USREVENT        :EVENT:100         , d0:0x61616161 d1:0x61616161")
        self.assertEqual(trace.markers, [])
        self.assertEqual(trace.undecoded_markers, 1)

    def test_other_user_events_are_ignored(self):
        trace = self.parse_with('t:0x1 CPU:00 USREVENT        :EVENT:7          STR:"other", d0:0x1')
        self.assertEqual(trace.markers, [])
        self.assertEqual(trace.undecoded_markers, 0)
