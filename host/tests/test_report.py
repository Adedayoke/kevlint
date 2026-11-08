import re
import unittest
from pathlib import Path

from kevlint.inversion import TYPICAL_FIX, check
from kevlint.parser import parse_trace
from kevlint.report import render_html

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def render(name, mutate=None):
    trace = parse_trace(FIXTURES / f"{name}.txt")
    if mutate:
        mutate(trace)
    result = check(trace)
    return render_html(f"fixtures/{name}.txt", trace, result), trace, result


class InversionReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page, cls.trace, cls.result = render("demo_inversion")

    def test_banner(self):
        self.assertIn('class="banner fail"', self.page)
        self.assertIn('<div class="verdict">FAIL</div>', self.page)
        self.assertIn("10 violations in 1 pattern, worst block 0.249 s", self.page)

    def test_pattern_card(self):
        self.assertEqual(self.page.count('class="card pattern"'), 1)
        self.assertIn("<b>10 occurrences</b>", self.page)
        for name in ("fusion_rt", "fusion_holder", "fusion_hog"):
            self.assertIn(f"<code>{name}</code>", self.page)

    def test_typical_fix_is_on_each_pattern(self):
        self.assertEqual(self.page.count(TYPICAL_FIX), 1)
        self.assertIn("Typical fix", self.page)

    def test_timeline_has_one_lane_per_role(self):
        self.assertEqual(self.page.count('<svg class="timeline"'), 1)
        self.assertEqual(self.page.count('class="role"'), 3)
        for role in ("WAITER", "HOLDER", "MIDDLE"):
            self.assertIn(f">{role}</text>", self.page)

    def test_all_three_state_categories_are_drawn(self):
        for category in ("running", "ready", "blocked"):
            self.assertIn(f'class="mark {category}"', self.page)

    def test_exact_state_name_is_in_the_tooltip(self):
        self.assertRegex(self.page, r'data-tip="[^"]*THSEM \(blocked\)')

    def test_evidence_lines_for_every_occurrence(self):
        for v in self.result.violations:
            for line in (
                v.wait_call_line,
                v.block_start_line,
                v.middle_line,
                v.release_line,
                v.block_end_line,
            ):
                self.assertIn(f"<td>{line}</td>", self.page)

    def test_coverage(self):
        self.assertIn("<b>12 of 71</b> blocks evaluated", self.page)
        self.assertIn("<b>59</b> not evaluated", self.page)
        self.assertIn("no matching wait call in the trace", self.page)

    def test_holder_note(self):
        self.assertIn("Holders are inferred, not named by the trace", self.page)


class PassReportTest(unittest.TestCase):
    def test_pass_still_shows_partial_coverage(self):
        page, _, _ = render("demo_fixed")
        self.assertIn('class="banner pass"', page)
        self.assertIn('<div class="verdict">PASS</div>', page)
        self.assertIn("<b>13 of 71</b> blocks evaluated", page)
        self.assertIn("<b>58</b> not evaluated", page)
        self.assertIn("Holders are inferred", page)

    def test_pass_has_no_pattern_card(self):
        page, _, _ = render("demo_normal")
        self.assertNotIn('class="card pattern"', page)
        self.assertNotIn(TYPICAL_FIX, page)


class SelfContainedTest(unittest.TestCase):
    def assert_no_external_references(self, page):
        self.assertIsNone(re.search(r"https?:|//cdn|@import", page))
        self.assertIsNone(re.search(r"<link\b|\ssrc=|\shref=", page))
        self.assertIsNone(re.search(r"url\((?!#)", page))

    def test_no_external_references(self):
        for name in ("demo_normal", "demo_fixed", "demo_inversion"):
            self.assert_no_external_references(render(name)[0])

    def test_csp_blocks_network_access(self):
        self.assertIn("default-src 'none'", render("demo_normal")[0])

    def test_output_is_deterministic(self):
        self.assertEqual(render("demo_inversion")[0], render("demo_inversion")[0])


class EscapingTest(unittest.TestCase):
    def test_thread_names_from_the_trace_are_escaped(self):
        hostile = '<script>alert("x")</script>'

        def rename(trace):
            for thread, name in list(trace.thread_names.items()):
                if name == "fusion_rt":
                    trace.thread_names[thread] = hostile

        page, _, _ = render("demo_inversion", rename)
        self.assertNotIn(hostile, page)
        self.assertIn("&lt;script&gt;", page)


if __name__ == "__main__":
    unittest.main()
