import io
import tempfile
import unittest
from pathlib import Path

from kevlint import usermarks
from kevlint.__main__ import report, report_rules
from kevlint.config import load_config
from kevlint.inversion import check
from kevlint.parser import Marker, parse_trace
from kevlint.report import render_html
from kevlint.rules import run_rules

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures"
INVERSION = FIXTURES / "demo_markers_inversion.txt"
FIXED = FIXTURES / "demo_markers_fixed.txt"


def terminal(trace, result, name="trace.txt"):
    out = io.StringIO()
    report(name, trace, result, out)
    return out.getvalue()


class InversionWithMarkersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trace = parse_trace(INVERSION)
        cls.result = check(cls.trace)

    def name(self, thread):
        return self.trace.thread_names.get(thread)

    def test_verdict_and_threads_match_the_demo_without_markers(self):
        self.assertTrue(self.result.violations)
        for violation in self.result.violations:
            self.assertEqual(self.name(violation.middle), "fusion_hog")
            self.assertEqual(self.name(violation.waiter), "fusion_rt")
            self.assertEqual(self.name(violation.holder), "fusion_holder")

    def test_only_the_holder_writes_markers(self):
        self.assertEqual({self.name(m.thread) for m in self.trace.markers}, {"fusion_holder"})
        self.assertEqual({m.text for m in self.trace.markers}, {"lock taken", "lock released"})

    def test_no_marker_falls_inside_a_block(self):
        for violation in self.result.violations:
            self.assertEqual(usermarks.between(self.trace, violation.start_cycles, violation.end_cycles), [])

    def test_nearest_marker_before_each_block_is_lock_taken(self):
        for violation in self.result.violations:
            self.assertEqual(usermarks.before(self.trace, violation.start_cycles).text, "lock taken")

    def test_terminal_shows_the_nearest_marker_as_context(self):
        text = terminal(self.trace, self.result)
        self.assertIn(f"markers: {len(self.trace.markers)} in trace", text)
        self.assertIn('nearest before the block is "lock taken" from fusion_holder', text)
        self.assertIn(usermarks.CONTEXT_NOTE, text)

    def test_html_draws_and_lists_markers(self):
        page = render_html("trace.txt", self.trace, self.result)
        self.assertIn('class="user-arrow"', page)
        self.assertIn("Nearest marker before the block", page)
        self.assertIn("&quot;lock taken&quot;", page)
        self.assertIn(usermarks.CONTEXT_NOTE, page)

    def test_rules_output_shows_markers(self):
        config = load_config(REPO / "kevlint.toml")
        rules = run_rules(self.trace, config)
        out = io.StringIO()
        report_rules("trace.txt", self.trace, rules, config, out)
        self.assertIn(f"markers: {len(self.trace.markers)} in trace", out.getvalue())
        self.assertIn('nearest before the block is "lock taken"', out.getvalue())


class FixedWithMarkersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trace = parse_trace(FIXED)
        cls.result = check(cls.trace)

    def test_passes_and_resolves(self):
        self.assertEqual(self.result.violations, [])
        self.assertTrue(self.result.resolved)

    def test_resolved_card_shows_markers(self):
        self.assertIn("Nearest marker before the block", render_html("trace.txt", self.trace, self.result))

    def test_terminal_shows_markers_for_the_resolved_wait(self):
        self.assertIn('nearest before the block is "lock taken"', terminal(self.trace, self.result))


class NoMarkersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trace = parse_trace(FIXTURES / "demo_inversion.txt")
        cls.result = check(cls.trace)

    def test_terminal_says_nothing_about_markers(self):
        self.assertNotIn("marker", terminal(self.trace, self.result).lower())

    def test_html_has_no_marker_section_or_styles(self):
        page = render_html("trace.txt", self.trace, self.result)
        self.assertNotIn("user-mark", page)
        self.assertNotIn("Trace markers", page)


class TimelinePlacementTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trace = parse_trace(INVERSION)
        cls.result = check(cls.trace)
        cls.page = render_html("trace.txt", cls.trace, cls.result)

    def test_markers_outside_the_block_are_arrows_not_lines(self):
        self.assertNotIn('<line class="user-mark"', self.page)
        self.assertEqual(self.page.count('class="user-arrow"'), 2)
        self.assertIn('class="user-box outside"', self.page)

    def test_tooltips_say_which_side_of_the_block(self):
        self.assertRegex(self.page, r'data-tip="&quot;lock taken&quot; from [^"]*before the block, line')
        self.assertRegex(self.page, r'data-tip="&quot;lock released&quot; from [^"]*after the block, line')

    def test_a_marker_inside_the_block_gets_a_line_and_no_arrow(self):
        trace = parse_trace(INVERSION)
        result = check(trace)
        worst = max(result.violations, key=lambda v: v.duration_cycles)
        middle = (worst.start_cycles + worst.end_cycles) // 2
        trace.markers = [Marker(1, middle, 0, (1, 1), "inside")]
        page = render_html("trace.txt", trace, result)
        self.assertIn('<line class="user-mark"', page)
        self.assertNotIn('class="user-arrow"', page)
        self.assertRegex(page, r'data-tip="&quot;inside&quot; from [^"]*during the block, line')


class EscapingTest(unittest.TestCase):
    def test_marker_text_is_escaped_in_html(self):
        trace = parse_trace(INVERSION)
        for marker in trace.markers:
            marker.text = '<img src=x onerror=alert(1)>"'
        page = render_html("trace.txt", trace, check(trace))
        self.assertNotIn("<img src=x", page)
        self.assertIn("&lt;img src=x", page)


class AroundTest(unittest.TestCase):
    class FakeTrace:
        def __init__(self, cycles):
            self.markers = [Marker(i, c, 0, (1, 1), "m") for i, c in enumerate(cycles)]

    def cycles(self, markers, start=100, end=200):
        return [[m.cycles for m in group] for group in usermarks.around(self.FakeTrace(markers), start, end)]

    def test_no_markers(self):
        self.assertEqual(self.cycles([]), [[], [], []])

    def test_close_markers_are_outside_the_block(self):
        self.assertEqual(self.cycles([90, 150, 210]), [[90], [150], [210]])

    def test_only_the_nearest_on_each_side(self):
        self.assertEqual(self.cycles([80, 90, 210, 220]), [[90], [], [210]])

    def test_far_markers_are_left_out(self):
        self.assertEqual(self.cycles([10, 290]), [[], [], []])

    def test_a_marker_on_the_edge_is_inside(self):
        self.assertEqual(self.cycles([100, 200]), [[], [100, 200], []])

    def test_before_is_inclusive(self):
        self.assertEqual(usermarks.before(self.FakeTrace([50, 100]), 100).cycles, 100)

    def test_between_excludes_the_edges(self):
        self.assertEqual([m.cycles for m in usermarks.between(self.FakeTrace([100, 150, 200]), 100, 200)], [150])
