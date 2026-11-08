import unittest
from pathlib import Path

from kevlint.config import Budget, Config
from kevlint.parser import parse_trace
from kevlint.rules import (
    ERROR,
    FAIL,
    NOT_EVALUATED,
    PASS,
    collect_waits,
    overall_status,
    root_cause_headline,
    root_cause_text,
    root_causes,
    run_rules,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
RT = Budget("fusion_rt", 50)
TRACES = {}


def trace_of(name):
    if name not in TRACES:
        TRACES[name] = parse_trace(FIXTURES / f"{name}.txt")
    return TRACES[name]


def rule_for(name, *budgets):
    config = Config("k", inversion=False, budgets=list(budgets))
    return run_rules(trace_of(name), config)[0]


class CollectWaitsTest(unittest.TestCase):
    def test_matches_the_fusion_rt_blocks_in_the_fixture(self):
        trace = trace_of("demo_inversion")
        waits = [
            w
            for w in collect_waits(trace)
            if w.state == "THSEM" and trace.thread_names.get(w.thread) == "fusion_rt"
        ]
        self.assertEqual(len(waits), 11)
        self.assertEqual(sum(w.end is None for w in waits), 1)
        self.assertEqual((waits[0].start.line, waits[0].end.line), (1177, 6179))

    def test_running_and_ready_are_never_waits(self):
        states = {w.state for w in collect_waits(trace_of("demo_normal"))}
        self.assertNotIn("THRUNNING", states)
        self.assertNotIn("THREADY", states)


class MaxBlockedTimeTest(unittest.TestCase):
    def test_inversion_fixture_breaches_on_every_block(self):
        rule = rule_for("demo_inversion", RT)
        self.assertEqual(rule.status, FAIL)
        self.assertEqual(len(rule.findings), 11)
        self.assertEqual(rule.evaluated, 11)
        self.assertAlmostEqual(rule.worst_ms, 249.094, places=2)

    def test_block_open_at_trace_end_within_budget_is_not_evaluated(self):
        rule = rule_for("demo_inversion", Budget("fusion_rt", 60))
        self.assertEqual(len(rule.findings), 10)
        self.assertEqual(
            dict(rule.not_evaluated),
            {"block still open when the trace ended, within budget so far": 1},
        )

    def test_block_open_at_trace_end_over_budget_is_a_breach(self):
        rule = rule_for("demo_inversion", RT)
        last = rule.findings[-1]
        self.assertIsNone(last.end_cycles)
        self.assertAlmostEqual(last.duration_cycles * 1000 / trace_of("demo_inversion").cycles_per_sec, 52.195, places=2)
        self.assertNotIn("block end", last.lines)
        self.assertEqual(sum(rule.not_evaluated.values()), 0)

    def test_breach_carries_its_evidence(self):
        rule = rule_for("demo_inversion", RT)
        worst = max(rule.findings, key=lambda f: f.duration_cycles)
        self.assertEqual(
            worst.lines,
            {"wait call": 1172, "block start": 1177, "release": 6175, "block end": 6179},
        )
        self.assertEqual(worst.sync_p, "0x0000001f5d627018")
        self.assertEqual(worst.state, "THSEM")
        self.assertEqual(worst.priority, 50)
        self.assertEqual(worst.budget_ms, 50)
        self.assertEqual(trace_of("demo_inversion").thread_names[worst.holder], "fusion_holder")
        self.assertEqual(worst.release_call, "SYNC_SEM_POST/84")

    def test_fixed_fixture_stays_within_budget(self):
        rule = rule_for("demo_fixed", RT)
        self.assertEqual(rule.status, PASS)
        self.assertEqual(rule.findings, [])
        self.assertEqual(rule.evaluated, 11)
        self.assertAlmostEqual(rule.worst_ms, 24.279, places=2)

    def test_budget_is_strictly_longer_than(self):
        rule = rule_for("demo_fixed", RT)
        exact = rule.worst_ms
        self.assertEqual(rule_for("demo_fixed", Budget("fusion_rt", exact)).findings, [])
        self.assertEqual(len(rule_for("demo_fixed", Budget("fusion_rt", exact - 0.001)).findings), 1)

    def test_normal_fixture_has_nothing_to_measure_and_says_so(self):
        rule = rule_for("demo_normal", RT)
        self.assertEqual(rule.status, PASS)
        self.assertEqual(rule.evaluated, 0)
        self.assertIsNone(rule.worst_ms)
        self.assertEqual(rule.coverage, "0 evaluated, 0 not evaluated")

    def test_idle_waits_do_not_count_by_default(self):
        rule = rule_for("demo_normal", Budget("fusion_rt", 1))
        self.assertEqual(rule.findings, [])

    def test_opting_in_to_other_states(self):
        rule = rule_for("demo_normal", Budget("fusion_rt", 300, ("THRECEIVE",)))
        self.assertEqual(rule.status, FAIL)
        self.assertEqual({f.state for f in rule.findings}, {"THRECEIVE"})
        self.assertAlmostEqual(rule.worst_ms, 318.11, places=1)

    def test_opted_in_states_replace_the_default(self):
        rule = rule_for("demo_inversion", Budget("fusion_rt", 50, ("THREPLY",)))
        self.assertEqual(rule.findings, [])

    def test_opted_in_finding_has_only_block_lines(self):
        rule = rule_for("demo_normal", Budget("fusion_server", 40, ("THREPLY",)))
        self.assertEqual(len(rule.findings), 1)
        finding = rule.findings[0]
        self.assertEqual(list(finding.lines), ["block start", "block end"])
        self.assertIsNone(finding.sync_p)
        self.assertIsNone(finding.holder)

    def test_a_name_shared_by_several_threads_checks_them_all(self):
        trace = trace_of("demo_inversion")
        shared = trace.thread_names.copy()
        rt = next(t for t, n in shared.items() if n == "fusion_rt")
        hog = next(t for t, n in shared.items() if n == "fusion_holder")
        self.assertNotEqual(rt, hog)
        rule = rule_for("demo_inversion", Budget("fusion_rt", 50), Budget("fusion_holder", 50))
        self.assertEqual([row.budget.thread for row in rule.budgets], ["fusion_rt", "fusion_holder"])
        self.assertEqual([row.status for row in rule.budgets], [FAIL, PASS])


class MissingThreadTest(unittest.TestCase):
    def test_missing_thread_is_not_evaluated_and_warns(self):
        rule = rule_for("test_wide", RT)
        self.assertEqual(rule.status, NOT_EVALUATED)
        self.assertEqual(rule.errors, [])
        self.assertEqual(
            rule.warnings, ["no thread named 'fusion_rt' in this trace, so its budget was not checked"]
        )
        self.assertEqual(dict(rule.not_evaluated), {"no thread named fusion_rt in this trace": 1})

    def test_required_missing_thread_is_an_error(self):
        rule = rule_for("test_wide", Budget("fusion_rt", 50, required=True))
        self.assertEqual(rule.status, ERROR)
        self.assertEqual(rule.errors, ["required thread 'fusion_rt' is not in this trace"])
        self.assertEqual(rule.warnings, [])

    def test_a_breach_on_one_budget_outranks_a_missing_one(self):
        rule = rule_for("demo_inversion", RT, Budget("not_a_thread", 5))
        self.assertEqual(rule.status, FAIL)
        self.assertEqual([row.status for row in rule.budgets], [FAIL, NOT_EVALUATED])

    def test_missing_thread_never_passes_even_beside_a_passing_budget(self):
        rule = rule_for("demo_fixed", RT, Budget("not_a_thread", 5))
        self.assertEqual(rule.status, NOT_EVALUATED)


def semaphore_rule(name, minimum):
    config = Config("k", inversion=False, semaphore_min_priority=minimum)
    return run_rules(trace_of(name), config)[0]


class NoSemaphoreTest(unittest.TestCase):
    def test_inversion_fixture_flags_every_high_priority_entry(self):
        rule = semaphore_rule("demo_inversion", 40)
        self.assertEqual(rule.status, FAIL)
        self.assertEqual(len(rule.findings), 11)
        trace = trace_of("demo_inversion")
        self.assertEqual({trace.thread_names[f.thread] for f in rule.findings}, {"fusion_rt"})
        self.assertEqual({f.priority for f in rule.findings}, {50})

    def test_a_block_still_open_at_trace_end_is_flagged(self):
        rule = semaphore_rule("demo_inversion", 40)
        self.assertEqual(sum(f.end_cycles is None for f in rule.findings), 1)

    def test_finding_carries_its_evidence(self):
        first = semaphore_rule("demo_inversion", 40).findings[0]
        self.assertEqual(first.lines["block start"], 1177)
        self.assertEqual(first.lines["wait call"], 1172)
        self.assertEqual(first.sync_p, "0x0000001f5d627018")
        self.assertIsNone(first.budget_ms)

    def test_threshold_is_at_or_above(self):
        self.assertEqual(len(semaphore_rule("demo_inversion", 50).findings), 11)
        self.assertEqual(semaphore_rule("demo_inversion", 51).findings, [])

    def test_a_low_threshold_catches_the_system_threads(self):
        rule = semaphore_rule("demo_normal", 21)
        self.assertEqual(len(rule.findings), 21)
        self.assertEqual(rule.status, FAIL)

    def test_mutex_and_normal_fixtures_pass_at_40(self):
        for name in ("demo_normal", "demo_fixed", "test_wide"):
            rule = semaphore_rule(name, 40)
            self.assertEqual(rule.status, PASS, name)
            self.assertGreater(rule.evaluated, 0, name)

    def test_coverage_counts_every_semaphore_entry(self):
        self.assertEqual(semaphore_rule("demo_normal", 40).evaluated, 22)


class ConfiguredInversionTest(unittest.TestCase):
    def test_runs_with_the_configured_minimum(self):
        trace = trace_of("demo_inversion")
        kept = run_rules(trace, Config("k", min_waiter_priority=50))[0]
        self.assertEqual((kept.status, len(kept.findings)), (FAIL, 10))
        dropped = run_rules(trace, Config("k", min_waiter_priority=51))[0]
        self.assertEqual((dropped.status, len(dropped.findings)), (PASS, 0))

    def test_coverage_text_counts_excluded_separately(self):
        rule = run_rules(trace_of("demo_inversion"), Config("k", min_waiter_priority=40))[0]
        self.assertGreater(rule.excluded, 0)
        self.assertRegex(rule.coverage, r"^\d+ evaluated, \d+ not evaluated, \d+ excluded by config$")
        unfiltered = run_rules(trace_of("demo_inversion"), Config("k"))[0]
        self.assertNotIn("excluded", unfiltered.coverage)

    def test_disabled_rule_is_absent(self):
        config = Config("k", inversion=False, budgets=[RT])
        self.assertEqual([r.name for r in run_rules(trace_of("demo_fixed"), config)], ["max-blocked-time"])


class RootCauseTest(unittest.TestCase):
    SAMPLE = dict(min_waiter_priority=40, budgets=[RT], semaphore_min_priority=40)

    def rules(self, name, **settings):
        return run_rules(trace_of(name), Config("k", **settings))

    def test_all_three_rules_share_one_cause_in_the_demo(self):
        trace = trace_of("demo_inversion")
        rules = self.rules("demo_inversion", **self.SAMPLE)
        causes = root_causes(rules)
        self.assertEqual(len(causes), 1)
        self.assertEqual(causes[0].total, 32)
        self.assertEqual(
            causes[0].counts,
            {
                "priority-inversion": 10,
                "max-blocked-time": 11,
                "no-semaphore-high-priority": 11,
            },
        )
        self.assertEqual(
            root_cause_headline(trace, rules),
            "32 findings from 3 rules, all from 1 root cause: "
            "fusion_rt blocking on semaphore 0x0000001f5d627018",
        )

    def test_passing_trace_has_no_causes(self):
        rules = self.rules("demo_fixed", **self.SAMPLE)
        self.assertEqual(root_causes(rules), [])
        self.assertIsNone(root_cause_headline(trace_of("demo_fixed"), rules))

    def test_findings_from_one_rule_make_no_shared_claim(self):
        trace = trace_of("demo_inversion")
        rules = self.rules("demo_inversion", inversion=False, semaphore_min_priority=40)
        self.assertEqual(len(root_causes(rules)), 1)
        self.assertIsNone(root_cause_headline(trace, rules))

    def test_several_causes_are_counted_and_sorted(self):
        trace = trace_of("demo_normal")
        rules = self.rules(
            "demo_normal",
            inversion=False,
            budgets=[Budget("iousb_poll_tq", 50)],
            semaphore_min_priority=21,
        )
        causes = root_causes(rules)
        self.assertGreater(len(causes), 1)
        self.assertEqual([c.total for c in causes], sorted((c.total for c in causes), reverse=True))
        self.assertTrue(any(len(c.counts) > 1 for c in causes))
        self.assertRegex(root_cause_headline(trace, rules), r"from \d+ root causes$")

    def test_finding_with_no_sync_object_is_never_merged_with_one(self):
        rules = self.rules(
            "demo_normal",
            inversion=False,
            budgets=[Budget("fusion_server", 40, ("THREPLY",))],
        )
        cause = root_causes(rules)[0]
        self.assertIsNone(cause.sync_p)
        self.assertEqual(root_cause_text(trace_of("demo_normal"), cause), "fusion_server in THREPLY")


class OverallStatusTest(unittest.TestCase):
    def test_worst_status_wins(self):
        pass_rule = rule_for("demo_fixed", RT)
        fail_rule = rule_for("demo_inversion", RT)
        missing = rule_for("test_wide", RT)
        error = rule_for("test_wide", Budget("fusion_rt", 50, required=True))
        self.assertEqual(overall_status([pass_rule]), PASS)
        self.assertEqual(overall_status([pass_rule, missing]), NOT_EVALUATED)
        self.assertEqual(overall_status([missing, fail_rule]), FAIL)
        self.assertEqual(overall_status([fail_rule, error]), ERROR)


if __name__ == "__main__":
    unittest.main()
