"""Load and strictly validate a kevlint.toml rules file.

A typo must never silently switch a check off, so anything unrecognized is an
error. tomllib needs Python 3.11, so it is imported only when --config is used.
"""
import math
from dataclasses import dataclass, field

INVERSION = "priority-inversion"
MAX_BLOCKED = "max-blocked-time"
NO_SEMAPHORE = "no-semaphore-high-priority"
RULE_NAMES = (INVERSION, MAX_BLOCKED, NO_SEMAPHORE)

DEFAULT_BUDGET_STATES = ("THSEM", "THMUTEX")
# Every non-running thread state seen in the real traces in fixtures/.
BLOCKING_STATES = frozenset(
    {
        "THCONDVAR",
        "THINTR",
        "THJOIN",
        "THMUON_MUTEX",
        "THMUTEX",
        "THNANOSLEEP",
        "THRECEIVE",
        "THREPLY",
        "THSEM",
        "THSEND",
        "THSEND_NOTIFY",
        "THSIGSUSPEND",
        "THSIGWAITINFO",
        "THTRACEBUFFER",
    }
)


class ConfigError(Exception):
    """The rules file cannot be used."""


@dataclass
class Budget:
    thread: str
    max_ms: float
    states: tuple = DEFAULT_BUDGET_STATES
    required: bool = False


@dataclass
class Config:
    path: str
    inversion: bool = True
    min_waiter_priority: int = None
    budgets: list = None
    semaphore_min_priority: int = None
    disabled: list = field(default_factory=list)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return (_is_int(value) or isinstance(value, float)) and math.isfinite(value)


def _reject_unknown(table, allowed, where):
    unknown = sorted(set(table) - set(allowed))
    if unknown:
        raise ConfigError(f"{where}: unknown key {unknown[0]!r}")


def _enabled(table, where):
    value = table.get("enabled", True)
    if not isinstance(value, bool):
        raise ConfigError(f"{where}: enabled must be true or false")
    return value


def _priority(table, key, where):
    value = table[key]
    if not _is_int(value) or value < 0:
        raise ConfigError(f"{where}: {key} must be a non-negative integer")
    return value


def _states(entry, where):
    states = entry.get("states", list(DEFAULT_BUDGET_STATES))
    if not isinstance(states, list) or not states:
        raise ConfigError(f"{where}: states must be a non-empty list")
    for state in states:
        if not isinstance(state, str) or state not in BLOCKING_STATES:
            known = ", ".join(sorted(BLOCKING_STATES))
            raise ConfigError(f"{where}: unknown state {state!r}; known states: {known}")
    if len(set(states)) != len(states):
        raise ConfigError(f"{where}: states has a duplicate")
    return tuple(states)


def _budget(entry, where):
    if not isinstance(entry, dict):
        raise ConfigError(f"{where}: must be a table")
    _reject_unknown(entry, ("thread", "max_ms", "states", "required"), where)
    thread = entry.get("thread")
    if not isinstance(thread, str) or not thread.strip():
        raise ConfigError(f"{where}: thread must be a non-empty string")
    max_ms = entry.get("max_ms")
    if not _is_number(max_ms) or max_ms <= 0:
        raise ConfigError(f"{where}: max_ms must be a positive number")
    required = entry.get("required", False)
    if not isinstance(required, bool):
        raise ConfigError(f"{where}: required must be true or false")
    return Budget(thread, max_ms, _states(entry, where), required)


def _budgets(table, where):
    entries = table.get("budgets")
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{where}: budgets must be a non-empty list")
    budgets = [_budget(entry, f"{where}: budgets[{i}]") for i, entry in enumerate(entries)]
    names = [budget.thread for budget in budgets]
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise ConfigError(f"{where}: more than one budget for thread {repeated[0]!r}")
    return budgets


def parse_config(data, path):
    """Return a Config for parsed TOML data, or raise ConfigError."""
    _reject_unknown(data, ("rules",), path)
    rules = data.get("rules")
    if not isinstance(rules, dict) or not rules:
        raise ConfigError(f"{path}: needs a [rules.<name>] table")
    _reject_unknown(rules, RULE_NAMES, f"{path}: rules")
    for name, table in rules.items():
        if not isinstance(table, dict):
            raise ConfigError(f"{path}: rules.{name} must be a table")

    config = Config(path)

    where = f"{path}: rules.{INVERSION}"
    table = rules.get(INVERSION, {})
    _reject_unknown(table, ("enabled", "min_waiter_priority"), where)
    config.inversion = _enabled(table, where)
    if "min_waiter_priority" in table:
        config.min_waiter_priority = _priority(table, "min_waiter_priority", where)

    where = f"{path}: rules.{MAX_BLOCKED}"
    table = rules.get(MAX_BLOCKED)
    if table is not None:
        _reject_unknown(table, ("enabled", "budgets"), where)
        enabled = _enabled(table, where)
        if enabled or "budgets" in table:
            budgets = _budgets(table, where)
            config.budgets = budgets if enabled else None

    where = f"{path}: rules.{NO_SEMAPHORE}"
    table = rules.get(NO_SEMAPHORE)
    if table is not None:
        _reject_unknown(table, ("enabled", "min_priority"), where)
        enabled = _enabled(table, where)
        if enabled and "min_priority" not in table:
            raise ConfigError(f"{where}: min_priority is required")
        if "min_priority" in table:
            minimum = _priority(table, "min_priority", where)
            config.semaphore_min_priority = minimum if enabled else None

    config.disabled = [
        name
        for name, on in (
            (INVERSION, config.inversion),
            (MAX_BLOCKED, config.budgets is not None),
            (NO_SEMAPHORE, config.semaphore_min_priority is not None),
        )
        if not on
    ]
    if len(config.disabled) == len(RULE_NAMES):
        raise ConfigError(f"{path}: every rule is disabled, so nothing would be checked")
    return config


def load_config(path):
    """Read and validate a rules file, or raise ConfigError."""
    try:
        import tomllib
    except ImportError:
        raise ConfigError("--config needs Python 3.11 or newer") from None
    try:
        with open(path, "rb") as source:
            data = tomllib.load(source)
    except OSError as error:
        raise ConfigError(f"cannot read config: {error}") from None
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path}: invalid TOML: {error}") from None
    return parse_config(data, path)
