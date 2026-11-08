import tempfile
import unittest
from pathlib import Path

from kevlint.config import ConfigError, load_config, parse_config

RT = {"thread": "fusion_rt", "max_ms": 50}


def rules(**tables):
    return {"rules": {name.replace("_", "-"): table for name, table in tables.items()}}


class ValidConfigTest(unittest.TestCase):
    def test_inversion_stays_on_when_the_file_omits_it(self):
        config = parse_config(rules(max_blocked_time={"budgets": [RT]}), "k")
        self.assertTrue(config.inversion)
        self.assertIsNone(config.min_waiter_priority)
        self.assertIsNone(config.semaphore_min_priority)

    def test_new_rules_are_off_unless_configured(self):
        config = parse_config(rules(priority_inversion={"min_waiter_priority": 40}), "k")
        self.assertEqual(config.min_waiter_priority, 40)
        self.assertIsNone(config.budgets)
        self.assertEqual(
            config.disabled, ["max-blocked-time", "no-semaphore-high-priority"]
        )

    def test_budget_defaults(self):
        config = parse_config(rules(max_blocked_time={"budgets": [RT]}), "k")
        budget = config.budgets[0]
        self.assertEqual(budget.states, ("THSEM", "THMUTEX"))
        self.assertFalse(budget.required)

    def test_budget_options(self):
        entry = dict(RT, states=["THSEND", "THREPLY"], required=True, max_ms=12.5)
        budget = parse_config(rules(max_blocked_time={"budgets": [entry]}), "k").budgets[0]
        self.assertEqual(budget.states, ("THSEND", "THREPLY"))
        self.assertTrue(budget.required)
        self.assertEqual(budget.max_ms, 12.5)

    def test_rule_can_be_turned_off(self):
        data = rules(
            priority_inversion={"enabled": False},
            no_semaphore_high_priority={"min_priority": 40},
        )
        config = parse_config(data, "k")
        self.assertFalse(config.inversion)
        self.assertEqual(config.semaphore_min_priority, 40)

    def test_disabled_rules_need_no_settings(self):
        data = rules(max_blocked_time={"enabled": False}, no_semaphore_high_priority={"enabled": False})
        config = parse_config(data, "k")
        self.assertTrue(config.inversion)
        self.assertIsNone(config.budgets)
        self.assertIsNone(config.semaphore_min_priority)

    def test_inline_and_array_table_forms_load_the_same(self):
        forms = (
            '[rules.max-blocked-time]\nbudgets = [{ thread = "a", max_ms = 5 }]\n',
            '[[rules.max-blocked-time.budgets]]\nthread = "a"\nmax_ms = 5\n',
        )
        with tempfile.TemporaryDirectory() as folder:
            for number, text in enumerate(forms):
                path = Path(folder) / f"{number}.toml"
                path.write_text(text)
                self.assertEqual(load_config(str(path)).budgets[0].thread, "a")


class InvalidConfigTest(unittest.TestCase):
    def assert_invalid(self, data, wanted):
        with self.assertRaises(ConfigError) as caught:
            parse_config(data, "k.toml")
        self.assertIn(wanted, str(caught.exception))
        self.assertTrue(str(caught.exception).startswith("k.toml"))

    def budget(self, **changes):
        return rules(max_blocked_time={"budgets": [{**RT, **changes}]})

    def test_structure(self):
        self.assert_invalid({}, "needs a [rules.<name>] table")
        self.assert_invalid({"rule": {}}, "unknown key 'rule'")
        self.assert_invalid(rules(priority_inversions={}), "unknown key 'priority-inversions'")
        self.assert_invalid({"rules": {"priority-inversion": 1}}, "must be a table")

    def test_unknown_keys_in_each_rule(self):
        self.assert_invalid(rules(priority_inversion={"min_prio": 4}), "unknown key 'min_prio'")
        self.assert_invalid(
            rules(max_blocked_time={"budgets": [RT], "budget": []}), "unknown key 'budget'"
        )
        self.assert_invalid(
            rules(no_semaphore_high_priority={"min_priority": 1, "x": 1}), "unknown key 'x'"
        )
        self.assert_invalid(self.budget(max_time=4), "unknown key 'max_time'")

    def test_enabled_must_be_boolean(self):
        self.assert_invalid(rules(priority_inversion={"enabled": "no"}), "enabled must be")

    def test_priorities(self):
        for bad in (-1, 1.5, "40", True):
            self.assert_invalid(
                rules(priority_inversion={"min_waiter_priority": bad}), "non-negative integer"
            )
            self.assert_invalid(
                rules(no_semaphore_high_priority={"min_priority": bad}), "non-negative integer"
            )
        self.assert_invalid(rules(no_semaphore_high_priority={}), "min_priority is required")

    def test_budget_list(self):
        self.assert_invalid(rules(max_blocked_time={}), "non-empty list")
        self.assert_invalid(rules(max_blocked_time={"budgets": []}), "non-empty list")
        self.assert_invalid(rules(max_blocked_time={"budgets": ["a"]}), "must be a table")
        self.assert_invalid(
            rules(max_blocked_time={"budgets": [RT, dict(RT, max_ms=9)]}),
            "more than one budget for thread 'fusion_rt'",
        )

    def test_budget_values(self):
        self.assert_invalid(self.budget(thread=""), "thread must be")
        self.assert_invalid(self.budget(thread=3), "thread must be")
        for bad in (0, -5, "50", True, float("inf"), float("nan")):
            self.assert_invalid(self.budget(max_ms=bad), "max_ms must be")
        self.assert_invalid(self.budget(required="yes"), "required must be")

    def test_states_are_checked_against_known_names(self):
        self.assert_invalid(self.budget(states=[]), "non-empty list")
        self.assert_invalid(self.budget(states="THSEM"), "non-empty list")
        self.assert_invalid(self.budget(states=["THBOGUS"]), "unknown state 'THBOGUS'")
        self.assert_invalid(self.budget(states=["THSEM", "THSEM"]), "duplicate")
        self.assert_invalid(self.budget(states=["THRUNNING"]), "unknown state 'THRUNNING'")
        self.assert_invalid(self.budget(states=["THREADY"]), "unknown state 'THREADY'")

    def test_every_rule_disabled(self):
        self.assert_invalid(rules(priority_inversion={"enabled": False}), "every rule is disabled")


class FileTest(unittest.TestCase):
    def test_missing_file(self):
        with self.assertRaises(ConfigError) as caught:
            load_config("/nonexistent/kevlint.toml")
        self.assertIn("cannot read config", str(caught.exception))

    def test_invalid_toml(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "k.toml"
            path.write_text("[rules\n")
            with self.assertRaises(ConfigError) as caught:
                load_config(str(path))
        self.assertIn("invalid TOML", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
