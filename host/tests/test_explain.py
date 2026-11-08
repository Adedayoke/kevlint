import io
import json
import unittest
from pathlib import Path

from kevlint.explain import (
    REJECTED,
    Explainer,
    Rejected,
    build_evidence,
    clean_text,
    parse_claims,
    pattern_key,
    print_explanations,
    user_message,
)
from kevlint.inversion import check
from kevlint.llm import LlmError
from kevlint.parser import parse_trace
from kevlint.patterns import group_patterns

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "demo_inversion.txt"
ALLOWED = {10, 11, 12, 20}


def answer(*claims):
    return json.dumps({"claims": [{"text": t, "lines": l} for t, l in claims]})


class FakeClient:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def complete(self, system, user):
        self.calls.append((system, user))
        outcome = self.outcomes[min(len(self.calls), len(self.outcomes)) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class CitationTest(unittest.TestCase):
    def assert_rejected(self, content, wanted):
        with self.assertRaises(Rejected) as caught:
            parse_claims(content, ALLOWED)
        self.assertIn(wanted, str(caught.exception))

    def test_good_answer(self):
        claims = parse_claims(answer(("Waiter blocked.", [12, 10, 10])), ALLOWED)
        self.assertEqual([(c.text, c.lines) for c in claims], [("Waiter blocked.", [10, 12])])

    def test_code_fence_is_tolerated(self):
        content = "```json\n" + answer(("x", [10])) + "\n```"
        self.assertEqual(len(parse_claims(content, ALLOWED)), 1)

    def test_citation_outside_the_evidence(self):
        self.assert_rejected(answer(("fine", [10]), ("bad", [999])), "line 999 is not in")

    def test_citation_only_in_the_prose(self):
        for text in (
            "See line 999.",
            "On lines 10, 11 and 999.",
            "On Line 999.",
            "At L999.",
            "As shown [999].",
            "Across lines 10-13.",
            "Across lines 10 to 13.",
        ):
            self.assert_rejected(answer((text, [10])), "is not in the evidence")

    def test_prose_citations_inside_the_evidence_pass(self):
        text = "See lines 10-12, then line 20 [20]."
        self.assertEqual(len(parse_claims(answer((text, [10])), ALLOWED)), 1)

    def test_other_numbers_in_prose_are_not_citations(self):
        text = "Priority 50 waited 249.1 ms on CPU 0."
        self.assertEqual(len(parse_claims(answer((text, [10])), ALLOWED)), 1)

    def test_huge_range_is_rejected_quickly(self):
        self.assert_rejected(answer(("lines 10-99999999999", [10])), "line 13 is not in")

    def test_claim_without_citations(self):
        self.assert_rejected(answer(("no cite", [])), "cited no line")
        self.assert_rejected('{"claims":[{"text":"no cite"}]}', "cited no line")

    def test_citations_must_be_integers(self):
        for bad in ('"10"', "10.0", "true", "null"):
            content = '{"claims":[{"text":"x","lines":[%s]}]}' % bad
            self.assert_rejected(content, "not a line number")

    def test_unusable_answers(self):
        self.assert_rejected("I think the holder was slow.", "not valid JSON")
        self.assert_rejected('{"claims":[]}', "no claims")
        self.assert_rejected("[1,2]", "no claims")
        self.assert_rejected('{"claims":["x"]}', "malformed")
        self.assert_rejected('{"claims":[{"text":" \\u0007 ","lines":[10]}]}', "empty")

    def test_control_characters_are_removed(self):
        self.assertEqual(clean_text("a\x1b[31m\nb‮c"), "a [31m b c")


class TraceCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trace = parse_trace(FIXTURE)
        cls.result = check(cls.trace)
        cls.pattern = group_patterns(cls.result.violations)[0]
        cls.worst = cls.pattern.worst
        cls.lines = FIXTURE.read_text(errors="replace").splitlines()


class EvidenceTest(TraceCase):
    def test_every_role_is_sent_with_its_real_text(self):
        evidence = build_evidence(FIXTURE, self.trace, self.pattern)
        for role, number in evidence.fields["event_lines"].items():
            self.assertEqual(evidence.lines[number], self.lines[number - 1], role)

    def test_multi_line_events_keep_their_continuation_lines(self):
        evidence = build_evidence(FIXTURE, self.trace, self.pattern)
        sync_line = self.worst.wait_call_line + 1
        self.assertIn(f"sync_p:{self.worst.sync_p}", evidence.lines[sync_line])

    def test_sends_only_the_worst_occurrence(self):
        evidence = build_evidence(FIXTURE, self.trace, self.pattern)
        self.assertLess(len(evidence.lines), 30)
        for other in self.pattern.occurrences:
            if other is not self.worst:
                self.assertNotIn(other.wait_call_line, evidence.lines)

    def test_fields_and_message(self):
        evidence = build_evidence(FIXTURE, self.trace, self.pattern)
        self.assertEqual(evidence.fields["waiter"]["priority"], self.worst.waiter_priority)
        self.assertEqual(evidence.fields["holder"]["priority"], self.worst.holder_priority)
        self.assertEqual(evidence.fields["middle"]["priority"], self.worst.middle_priority)
        self.assertIn("inferred", evidence.fields["holder"]["identity"])
        message = user_message(evidence)
        self.assertIn(f"{self.worst.middle_line} | ", message)
        self.assertNotIn(str(FIXTURE), message)
        self.assertNotIn("TRACE_CYCLES_PER_SEC", message)


class ExplainerTest(TraceCase):
    def explain(self, client):
        return Explainer(client).explain(FIXTURE, self.trace, self.result)[pattern_key(self.pattern)]

    def test_grounded_answer_is_kept(self):
        got = self.explain(FakeClient(answer(("Holder preempted.", [self.worst.holder_ready_line]))))
        self.assertIsNone(got.note)
        self.assertEqual(got.claims[0].lines, [self.worst.holder_ready_line])
        self.assertEqual(got.occurrences, len(self.pattern.occurrences))

    def test_line_real_in_the_trace_but_not_sent_is_rejected(self):
        unsent = next(o for o in self.pattern.occurrences if o is not self.worst).wait_call_line
        content = answer(("ok", [self.worst.middle_line]), ("bad", [unsent]))
        got = self.explain(FakeClient(content))
        self.assertEqual(got.claims, [])
        self.assertTrue(got.note.startswith(REJECTED))

    def test_failure_becomes_a_note(self):
        got = self.explain(FakeClient(LlmError("timed out", stop=True)))
        self.assertEqual(got.note, "explanation unavailable: timed out")

    def test_unexpected_error_becomes_a_note_with_no_message(self):
        got = self.explain(FakeClient(ValueError("secret-detail")))
        self.assertEqual(got.note, "explanation unavailable: internal error (ValueError)")

    def test_stop_skips_later_traces(self):
        client = FakeClient(LlmError("rate limited (HTTP 429)", stop=True))
        explainer = Explainer(client)
        explainer.explain(FIXTURE, self.trace, self.result)
        again = explainer.explain(FIXTURE, self.trace, self.result)[pattern_key(self.pattern)]
        self.assertEqual(len(client.calls), 1)
        self.assertIn("skipped after an earlier failure", again.note)

    def test_terminal_output_survives_an_ascii_stream(self):
        cited = self.worst.middle_line
        client = FakeClient(answer(("Priority 10 → stayed.", [cited])))
        explanations = Explainer(client).explain(FIXTURE, self.trace, self.result)
        out = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
        print_explanations(self.trace, self.result, explanations, out)
        out.seek(0)
        text = out.read()
        self.assertIn("AI-GENERATED EXPLANATION", text)
        self.assertIn(f"[line {cited}]", text)


if __name__ == "__main__":
    unittest.main()
