import unittest
from pathlib import Path

from kevlint.explain import Claim, Explanation, pattern_key
from kevlint.inversion import check
from kevlint.parser import parse_trace
from kevlint.patterns import group_patterns
from kevlint.report import render_html

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "demo_inversion.txt"


class ExplanationHtmlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trace = parse_trace(FIXTURE)
        cls.result = check(cls.trace)
        cls.key = pattern_key(group_patterns(cls.result.violations)[0])

    def render(self, explanation):
        return render_html("t.txt", self.trace, self.result, {self.key: explanation})

    def test_no_explanations_leaves_the_report_unchanged(self):
        plain = render_html("t.txt", self.trace, self.result)
        self.assertEqual(plain, render_html("t.txt", self.trace, self.result, None))
        self.assertNotIn("AI-generated", plain)
        self.assertNotIn(".ai-label", plain)

    def test_claims_are_labeled_with_visible_lines(self):
        page = self.render(Explanation(10, claims=[Claim("Holder preempted.", [1187, 1188])]))
        self.assertIn("AI-generated explanation", page)
        self.assertIn("worst of 10 occurrences", page)
        self.assertIn("the claims were not verified", page)
        self.assertIn("Holder preempted.", page)
        self.assertIn("lines 1187, 1188", page)

    def test_kevlint_where_line_is_labeled_as_not_ai(self):
        where = Claim("waiter a <x> blocked.", [1173, 6176])
        page = self.render(Explanation(1, claims=[Claim("c", [1])], where=where))
        self.assertIn("written by kevlint from the trace, not AI", page)
        self.assertIn("waiter a &lt;x&gt; blocked.", page)
        self.assertIn("lines 1173, 6176", page)

    def test_model_text_is_escaped(self):
        page = self.render(Explanation(1, claims=[Claim("<img src=x>", [1])]))
        self.assertIn("&lt;img src=x&gt;", page)
        self.assertNotIn("<img src=x>", page)

    def test_note_replaces_the_claims(self):
        page = self.render(Explanation(2, note="explanation rejected: unverifiable citation (x)"))
        self.assertIn("explanation rejected: unverifiable citation (x)", page)
        self.assertNotIn("<ul></ul>", page)


if __name__ == "__main__":
    unittest.main()
