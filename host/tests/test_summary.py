import tempfile
import unittest
from pathlib import Path

from kevlint.inversion import TYPICAL_FIXES, check
from kevlint.parser import parse_trace
from kevlint.summary import ERROR, FAIL, PASS, Entry, append_summary, render_summary

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def entry(name, mutate=None):
    trace = parse_trace(FIXTURES / f"{name}.txt")
    if mutate:
        mutate(trace)
    return Entry(f"fixtures/{name}.txt", trace, check(trace))


class SummaryRowTest(unittest.TestCase):
    def test_pass_row(self):
        self.assertIn(
            f"| `fixtures/demo_fixed.txt` | {PASS} | 0 | none | "
            "13 of 72 blocks evaluated, 59 not evaluated |",
            render_summary([entry("demo_fixed")]),
        )

    def test_fail_row(self):
        self.assertIn(
            f"| `fixtures/demo_inversion.txt` | {FAIL} | 10 | 0.249 s | "
            "12 of 71 blocks evaluated, 59 not evaluated |",
            render_summary([entry("demo_inversion")]),
        )

    def test_error_row(self):
        text = render_summary([Entry("fixtures/missing.txt", error="no such file")])
        self.assertIn(f"| `fixtures/missing.txt` | {ERROR} | n/a | n/a | no such file |", text)

    def test_one_table_for_many_traces(self):
        text = render_summary([entry("demo_normal"), entry("demo_inversion")])
        self.assertEqual(text.count("| Trace | Result |"), 1)
        table = text.split("#### Why")[0]
        self.assertIn("demo_normal", table)
        self.assertIn("demo_inversion", table)

    def test_coverage_warning(self):
        def remove_semaphore_waits(trace):
            trace.events = [e for e in trace.events if e.name != "SYNC_SEM_WAIT/85"]

        text = render_summary([entry("demo_inversion", remove_semaphore_waits)])
        self.assertIn("a not-evaluated block has higher priority (50)", text)


class SummaryExplanationTest(unittest.TestCase):
    def test_failure_explains_why(self):
        text = render_summary([entry("demo_inversion")])
        self.assertIn(
            "The urgent thread `fusion_rt` waited up to 249 ms for a lock held by "
            "`fusion_holder`, which couldn’t finish because the less urgent "
            "`fusion_hog` kept the CPU. Seen 10 times.",
            text,
        )
        self.assertIn(f"**Typical fix:** {TYPICAL_FIXES['THSEM']}", text)
        self.assertIn("Holders are inferred, not named by the trace.", text)

    def test_pass_has_no_explanation(self):
        text = render_summary([entry("demo_fixed")])
        self.assertNotIn("#### Why", text)


class AppendTest(unittest.TestCase):
    def test_appends_to_existing_content(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "summary.md"
            target.write_text("before\n", encoding="utf-8")
            append_summary([entry("demo_normal")], target)
            text = target.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("before\n"))
        self.assertIn(PASS, text)


if __name__ == "__main__":
    unittest.main()
