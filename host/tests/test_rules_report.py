import unittest
from pathlib import Path

from kevlint.config import Budget, Config
from kevlint.parser import parse_trace
from kevlint.report import render_html
from kevlint.rules import run_rules

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE = dict(
    min_waiter_priority=40, budgets=[Budget("fusion_rt", 50)], semaphore_min_priority=40
)


def page(name, **settings):
    trace = parse_trace(FIXTURES / f"{name}.txt")
    config = Config("k.toml", **settings)
    rules = run_rules(trace, config)
    return render_html("t.txt", trace, None, rules=rules, config=config)


class RulesPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = page("demo_inversion", **SAMPLE)

    def test_overview_lists_each_rule(self):
        self.assertIn("Results by rule", self.page)
        for name in ("priority-inversion", "max-blocked-time", "no-semaphore-high-priority"):
            self.assertIn(f'href="#rule-{name}"', self.page)
            self.assertIn(f'id="rule-{name}"', self.page)

    def test_banner_gives_the_overall_result(self):
        self.assertIn('class="banner fail"', self.page)
        self.assertIn(
            "32 findings from 3 rules, all from 1 root cause: "
            "fusion_rt blocking on semaphore 0x0000001f5d627018",
            self.page,
        )

    def test_root_causes_card_links_to_each_rule(self):
        self.assertIn("Root causes", self.page)
        for count, name in (
            (10, "priority-inversion"),
            (11, "max-blocked-time"),
            (11, "no-semaphore-high-priority"),
        ):
            self.assertIn(f'href="#rule-{name}">{count} {name}</a>', self.page)

    def test_a_single_rule_has_no_root_cause_claim(self):
        one = page("demo_inversion", inversion=False, semaphore_min_priority=40)
        self.assertNotIn("Root causes", one)
        self.assertIn("1 of 1 rule failed, 11 findings", one)

    def test_inversion_section_keeps_its_cards(self):
        self.assertIn("Priority inversions", self.page)
        self.assertIn("blocks in the trace", self.page)

    def test_excluded_blocks_are_counted_and_drawn_separately(self):
        self.assertRegex(self.page, r"10 evaluated, \d+ not evaluated, \d+ excluded by config")
        self.assertIn('<div class="stat excluded">', self.page)
        self.assertIn('<i class="excluded" style="flex:', self.page)
        self.assertIn('<i class="skipped" style="flex:', self.page)
        self.assertIn('<li class="excluded">', self.page)
        self.assertIn('class="meter-key"', self.page)

    def test_no_minimum_means_no_excluded_markup(self):
        plain = page("demo_inversion", budgets=[Budget("fusion_rt", 50)])
        self.assertNotIn('<div class="stat excluded">', plain)
        self.assertNotIn('class="meter-key"', plain)

    def test_budget_table_and_breach_evidence(self):
        self.assertIn("Breaches (11)", self.page)
        self.assertIn("THSEM, THMUTEX", self.page)
        self.assertIn("wait call 1172, block start 1177, release 6175, block end 6179", self.page)
        self.assertIn("249.094", self.page)
        self.assertIn("at least 52.195 (still open at trace end)", self.page)

    def visible_rows(self, label, summary):
        start = self.page.index(f"{label} (11), worst 3 shown")
        return self.page[start : self.page.index(f"<summary>{summary}", start)].count("<tr")

    def test_only_the_worst_three_rows_are_visible(self):
        self.assertEqual(self.visible_rows("Breaches", "Show all 11 breaches"), 4)
        self.assertEqual(self.visible_rows("Findings", "Show all 11 findings"), 4)

    def test_worst_row_comes_first(self):
        start = self.page.index("Breaches (11), worst 3 shown")
        section = self.page[start : self.page.index("<summary>Show all 11 breaches", start)]
        self.assertLess(section.index("249.094"), section.index("</tbody>"))
        first_row = section[section.index("<tbody>") : section.index("</tr>", section.index("<tbody>"))]
        self.assertIn("249.094", first_row)
        self.assertIn('class="worst"', first_row)

    def test_show_all_lists_every_row(self):
        start = self.page.index("<summary>Show all 11 breaches")
        table = self.page[start : self.page.index("</details>", start)]
        self.assertEqual(table.count("<tr"), 12)

    def test_three_or_fewer_rows_have_no_toggle(self):
        few = page("demo_inversion", inversion=False, budgets=[Budget("fusion_rt", 200)])
        self.assertIn("Breaches (2)</h3>", few)
        self.assertNotIn("Show all", few)

    def test_semaphore_section(self):
        self.assertIn("Findings (11)", self.page)
        self.assertIn("at priority 40 or above", self.page)
        self.assertIn("semaphores have no priority inheritance", self.page)

    def test_passing_rules_say_what_was_checked(self):
        fixed = page("demo_fixed", **SAMPLE)
        self.assertIn('class="banner pass"', fixed)
        self.assertIn("No block exceeded its budget.", fixed)
        self.assertIn("No thread at priority 40 or above blocked on a semaphore.", fixed)

    def test_missing_thread_warns_and_never_shows_pass(self):
        settings = dict(SAMPLE)
        wide = page("test_wide", **settings)
        self.assertIn('class="banner warn"', wide)
        self.assertIn("NOT EVALUATED", wide)
        self.assertIn('role="alert"', wide)
        self.assertIn("no thread named &#x27;fusion_rt&#x27; in this trace", wide)

    def test_required_missing_thread_shows_an_error(self):
        wide = page("test_wide", inversion=False, budgets=[Budget("fusion_rt", 50, required=True)])
        self.assertIn('class="banner fail"', wide)
        self.assertIn("required thread &#x27;fusion_rt&#x27; is not in this trace", wide)

    def test_disabled_rule_is_named_and_has_no_section(self):
        off = page(
            "demo_fixed",
            inversion=False,
            budgets=[Budget("fusion_rt", 50)],
            disabled=["priority-inversion", "no-semaphore-high-priority"],
        )
        self.assertIn("Not enabled: priority-inversion, no-semaphore-high-priority", off)
        self.assertNotIn('id="rule-priority-inversion"', off)

    def test_rule_styles_appear_only_with_a_config(self):
        trace = parse_trace(FIXTURES / "demo_fixed.txt")
        from kevlint.inversion import check

        self.assertNotIn(".pill", render_html("t.txt", trace, check(trace)))
        self.assertIn(".pill", self.page)


if __name__ == "__main__":
    unittest.main()
