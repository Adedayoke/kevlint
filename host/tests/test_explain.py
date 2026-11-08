import io
import json
import unittest
from pathlib import Path

from kevlint.explain import (
    REJECTED,
    REJECTED_STRUCTURE,
    REJECTED_VALUE,
    SYSTEM_PROMPT,
    Claim,
    Evidence,
    Explainer,
    Rejected,
    UnverifiableValue,
    build_evidence,
    check_values,
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


class PromptTest(unittest.TestCase):
    def test_asks_for_the_three_claim_structure(self):
        for wanted in ("exactly two claims", "1. Summary", "2. Cause"):
            self.assertIn(wanted, SYSTEM_PROMPT)
        self.assertNotIn("Where to look", SYSTEM_PROMPT)

    def test_requires_names_inferred_holder_and_no_advice(self):
        self.assertIn("never by tid or pid", SYSTEM_PROMPT)
        self.assertIn("inferred", SYSTEM_PROMPT)
        self.assertIn("Do not suggest fixes or give QNX API advice", SYSTEM_PROMPT)

    def test_is_not_tied_to_one_fixture(self):
        for specific in ("fusion_rt", "fusion_holder", "fusion_hog", "priority 30"):
            self.assertNotIn(specific, SYSTEM_PROMPT)


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


class ValueCheckTest(unittest.TestCase):
    EVIDENCE = Evidence(
        lines={
            10: "t:0xabc CPU:00 THREAD :THREADY pid:1 tid:2 priority:77 policy:1",
            11: "sync_p:0x0000001f5d627018",
            12: "code:0x00000099 priority:88",
        },
        fields={
            "sync_p": "0x0000002000000000",
            "waiter": {"priority": 50},
            "holder": {"priority": 10},
            "middle": {"priority": 30},
            "block_start_seconds": 0.062226,
            "block_end_seconds": 0.31132,
            "block_duration_ms": 249.094,
        },
    )

    def run_check(self, text, lines=(10,)):
        check_values([Claim(text, list(lines))], self.EVIDENCE)

    def assert_rejected(self, text, wanted, lines=(10,)):
        with self.assertRaises(UnverifiableValue) as caught:
            self.run_check(text, lines)
        self.assertEqual(str(caught.exception), f"{wanted} is not in the cited lines or fields")

    def test_hex_from_the_fields_or_cited_line_passes(self):
        self.run_check("Blocked on 0x0000002000000000.")
        self.run_check("Blocked on 0x2000000000.")
        self.run_check("Blocked on 0x1F5D627018.", lines=(11,))

    def test_invented_hex_is_rejected(self):
        self.assert_rejected("Blocked on 0xdeadbeef.", "hex value 0xdeadbeef")

    def test_hex_on_an_uncited_line_is_rejected(self):
        self.assert_rejected("Blocked on 0x99.", "hex value 0x99")
        self.run_check("Blocked on 0x99.", lines=(12,))

    def test_priorities_from_the_fields_pass_in_every_form(self):
        for text in (
            "At priority 50.",
            "Priorities 50, 30 and 10.",
            "A priority of 30.",
            "The priority: 10.",
            "A 50-priority waiter.",
            "A 50\u2011priority waiter.",
            "Because 50 > 30 > 10.",
            "fusion_hog (30) ran.",
            "Boosted 10 -> 50.",
        ):
            self.run_check(text)

    def test_invented_priorities_are_rejected_in_every_form(self):
        for text in (
            "At priority 49.",
            "Priorities 50, 30 and 49.",
            "A priority of 49.",
            "A 49-priority waiter.",
            "Because 50 > 49.",
            "Because 50 > 30 > 49.",
            "fusion_hog (49) ran.",
            "Boosted 10 \u2192 49.",
        ):
            self.assert_rejected(text, "priority 49")

    def test_priority_on_a_cited_line_passes_but_not_an_uncited_one(self):
        self.run_check("At priority 77.")
        self.assert_rejected("At priority 88.", "priority 88")
        self.run_check("At priority 88.", lines=(12,))

    def test_correctly_rounded_durations_pass(self):
        for text in (
            "Blocked for 249.094 ms.",
            "Blocked for 249.1 ms.",
            "Blocked for 249 ms.",
            "Blocked for 249.094ms.",
            "Blocked for 0.249 s.",
            "Blocked for 0.25 seconds.",
            "Blocked for 249094 us.",
            "Starting at 0.062226 s.",
        ):
            self.run_check(text)

    def test_wrong_durations_are_rejected(self):
        for text, wanted in (
            ("Blocked for 250 ms.", "duration 250 ms"),
            ("Blocked for 249.09 s.", "duration 249.09 s"),
            ("Blocked for 3 seconds.", "duration 3 seconds"),
            ("Blocked for 12 us.", "duration 12 us"),
        ):
            self.assert_rejected(text, wanted)

    def test_a_duration_next_to_a_priority_is_not_read_as_a_priority(self):
        self.run_check("At priority 50, 249.094 ms passed.")

    def test_other_numbers_are_not_checked(self):
        self.run_check("It ran on CPU 0 and SYNC_SEM_WAIT/85 was called by thread_7.")


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
        content = answer(
            ("Waiter blocked.", [self.worst.block_start_line]),
            ("Holder preempted.", [self.worst.holder_ready_line]),
        )
        got = self.explain(FakeClient(content))
        self.assertIsNone(got.note)
        self.assertEqual(got.claims[1].lines, [self.worst.holder_ready_line])
        self.assertEqual(got.occurrences, len(self.pattern.occurrences))

    def test_line_real_in_the_trace_but_not_sent_is_rejected(self):
        unsent = next(o for o in self.pattern.occurrences if o is not self.worst).wait_call_line
        content = answer(("ok", [self.worst.middle_line]), ("bad", [unsent]))
        got = self.explain(FakeClient(content))
        self.assertEqual(got.claims, [])
        self.assertTrue(got.note.startswith(REJECTED))

    def test_kevlint_writes_where_to_look_with_the_sync_p_lines(self):
        cited = self.worst.middle_line
        got = self.explain(FakeClient(answer(("One.", [cited]), ("Two.", [cited]))))
        self.assertEqual(got.where.lines, [1173, 6176])
        for number in got.where.lines:
            self.assertIn(f"sync_p:{self.worst.sync_p}", self.lines[number - 1])
        for wanted in ("fusion_rt", "inferred holder fusion_holder", "fusion_hog", "semaphore"):
            self.assertIn(wanted, got.where.text)
        self.assertIn(self.worst.sync_p, got.where.text)
        self.assertNotIn("tid", got.where.text)

    def test_where_to_look_never_ties_the_middle_thread_to_the_sync_object(self):
        evidence = build_evidence(FIXTURE, self.trace, self.pattern)
        self.assertIn("middle thread fusion_hog ran during the block.", evidence.where.text)
        self.assertNotIn("all synchronize", evidence.where.text)

    def test_the_model_must_write_exactly_two_claims(self):
        cited = self.worst.middle_line
        for claims in ([("One.", [cited])], [("A.", [cited]), ("B.", [cited]), ("C.", [cited])]):
            got = self.explain(FakeClient(answer(*claims)))
            self.assertEqual(got.claims, [])
            self.assertIsNone(got.where)
            self.assertTrue(got.note.startswith(REJECTED_STRUCTURE), got.note)

    def test_invented_value_rejects_the_whole_explanation(self):
        cited = self.worst.middle_line
        got = self.explain(FakeClient(answer(("Fine.", [cited]), ("It ran at priority 49.", [cited]))))
        self.assertEqual(got.claims, [])
        self.assertIsNone(got.where)
        self.assertEqual(
            got.note,
            f"{REJECTED_VALUE} (priority 49 is not in the cited lines or fields)",
        )

    def test_true_values_from_the_real_fixture_pass(self):
        w = self.worst
        text = (
            f"fusion_rt (priority 50) waited on {w.sync_p} for 249.094 ms, "
            "while priority 30 > 10 kept the inferred holder off the CPU."
        )
        got = self.explain(FakeClient(answer((text, [w.block_start_line]), ("Cause.", [w.middle_line]))))
        self.assertIsNone(got.note)

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
        client = FakeClient(answer(("Priority 10 → stayed.", [cited]), ("Cause.", [cited])))
        explanations = Explainer(client).explain(FIXTURE, self.trace, self.result)
        out = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
        print_explanations(self.trace, self.result, explanations, out)
        out.seek(0)
        text = out.read()
        self.assertIn("AI-GENERATED EXPLANATION", text)
        self.assertIn(f"[line {cited}]", text)


if __name__ == "__main__":
    unittest.main()
