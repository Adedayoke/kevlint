"""Run the rules selected by a config and report each one's result and coverage."""
from collections import Counter
from dataclasses import dataclass, field

from .config import BLOCKING_STATES, INVERSION, MAX_BLOCKED, NO_SEMAPHORE
from .inversion import _scan, check

PASS = "PASS"
FAIL = "FAIL"
NOT_EVALUATED = "NOT EVALUATED"
ERROR = "ERROR"

OPEN_WITHIN_BUDGET = "block still open when the trace ended, within budget so far"


@dataclass
class Wait:
    """A stretch a thread spent in one blocking state. end is None if still open."""

    thread: tuple
    state: str
    start: object
    end: object = None


@dataclass
class Finding:
    thread: tuple
    state: str
    priority: int
    start_cycles: int
    end_cycles: int  # None if the block was still open when the trace ended.
    duration_cycles: int
    lines: dict
    sync_p: str = None
    holder: tuple = None
    release_call: str = None
    budget_ms: float = None


@dataclass
class BudgetResult:
    budget: object
    status: str
    evaluated: int = 0
    breaches: int = 0
    worst_ms: float = None


@dataclass
class RuleResult:
    name: str
    findings: list
    evaluated: int
    not_evaluated: Counter
    worst_ms: float = None
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    budgets: list = field(default_factory=list)
    inversion: object = None

    @property
    def status(self):
        if self.errors:
            return ERROR
        if self.findings:
            return FAIL
        if any(row.status == NOT_EVALUATED for row in self.budgets):
            return NOT_EVALUATED
        return PASS

    @property
    def coverage(self):
        skipped = sum(self.not_evaluated.values())
        return f"{self.evaluated} evaluated, {skipped} not evaluated"


def line_text(lines):
    """Evidence lines as text, such as 'wait call 12, block start 15'."""
    return ", ".join(f"{label} {number}" for label, number in lines.items())


def overall_status(rules):
    """The worst status across rules: ERROR, then FAIL, then NOT EVALUATED."""
    statuses = {rule.status for rule in rules}
    for status in (ERROR, FAIL, NOT_EVALUATED):
        if status in statuses:
            return status
    return PASS


def collect_waits(trace):
    """Return every stretch any thread spent in a blocking state, in trace order."""
    current = {}
    waits = []
    for event in trace.events:
        if event.cls != "THREAD":
            continue
        thread = event.thread
        wait = current.get(thread)
        if wait is not None:
            if wait.state == event.name:
                continue
            wait.end = event
            del current[thread]
        if event.name in BLOCKING_STATES:
            wait = Wait(thread, event.name, event)
            current[thread] = wait
            waits.append(wait)
    return waits


def _duration(trace, wait):
    end = wait.end.cycles if wait.end is not None else trace.events[-1].cycles
    return max(end - wait.start.cycles, 0)


def _ms(trace, cycles):
    return cycles * 1000 / trace.cycles_per_sec


def _finding(trace, wait, blocks, budget_ms=None):
    """Build the evidence for one wait, adding what the sync-object scan knows."""
    block = blocks.get(wait.start.line)
    lines = {}
    sync_p = holder = release_call = None
    if block is not None and block.wait_call is not None:
        lines["wait call"] = block.wait_call.line
        sync_p = block.wait_call.fields.get("sync_p")
    lines["block start"] = wait.start.line
    if block is not None and block.release is not None:
        lines["release"] = block.release.line
        holder, release_call = block.holder, block.release.name
    if wait.end is not None:
        lines["block end"] = wait.end.line
    return Finding(
        wait.thread,
        wait.state,
        wait.start.priority,
        wait.start.cycles,
        None if wait.end is None else wait.end.cycles,
        _duration(trace, wait),
        lines,
        sync_p,
        holder,
        release_call,
        budget_ms,
    )


def _inversion_rule(trace, config):
    result = check(trace, config.min_waiter_priority)
    worst = max((v.duration_cycles for v in result.violations), default=None)
    rule = RuleResult(
        INVERSION,
        result.violations,
        result.evaluated,
        result.not_evaluated,
        worst_ms=None if worst is None else _ms(trace, worst),
        inversion=result,
    )
    if result.unevaluated_outranks_evaluated:
        rule.warnings.append(
            f"a not-evaluated block has priority {result.not_evaluated_max_priority}, "
            "higher than every evaluated waiter"
        )
    return rule


def _check_budget(trace, budget, threads, waits, blocks, rule):
    """Add one found thread name's breaches and coverage to rule."""
    row = BudgetResult(budget, PASS)
    for wait in waits:
        if wait.thread not in threads or wait.state not in budget.states:
            continue
        millis = _ms(trace, _duration(trace, wait))
        if wait.end is None and millis <= budget.max_ms:
            rule.not_evaluated[OPEN_WITHIN_BUDGET] += 1
            continue
        row.evaluated += 1
        row.worst_ms = millis if row.worst_ms is None else max(row.worst_ms, millis)
        if millis > budget.max_ms:
            row.breaches += 1
            rule.findings.append(_finding(trace, wait, blocks, budget.max_ms))
    if row.breaches:
        row.status = FAIL
    rule.evaluated += row.evaluated
    if row.worst_ms is not None:
        rule.worst_ms = max(rule.worst_ms or 0, row.worst_ms)
    return row


def _max_blocked_rule(trace, budgets, waits, blocks):
    rule = RuleResult(MAX_BLOCKED, [], 0, Counter())
    for budget in budgets:
        threads = {t for t, name in trace.thread_names.items() if name == budget.thread}
        if threads:
            rule.budgets.append(_check_budget(trace, budget, threads, waits, blocks, rule))
            continue
        rule.not_evaluated[f"no thread named {budget.thread} in this trace"] += 1
        if budget.required:
            rule.errors.append(f"required thread {budget.thread!r} is not in this trace")
            rule.budgets.append(BudgetResult(budget, ERROR))
        else:
            rule.warnings.append(
                f"no thread named {budget.thread!r} in this trace, so its budget was not checked"
            )
            rule.budgets.append(BudgetResult(budget, NOT_EVALUATED))
    rule.findings.sort(key=lambda finding: finding.start_cycles)
    return rule


def _no_semaphore_rule(trace, minimum, waits, blocks):
    """Flag each entry to THSEM at or above minimum, using the priority on that event."""
    rule = RuleResult(NO_SEMAPHORE, [], 0, Counter())
    for wait in waits:
        if wait.state != "THSEM":
            continue
        priority = wait.start.priority
        if priority is None:
            rule.not_evaluated["no priority on the THSEM event"] += 1
            continue
        rule.evaluated += 1
        if priority >= minimum:
            rule.findings.append(_finding(trace, wait, blocks))
    worst = max((f.duration_cycles for f in rule.findings), default=None)
    rule.worst_ms = None if worst is None else _ms(trace, worst)
    return rule


def run_rules(trace, config):
    """Return a RuleResult for each enabled rule."""
    rules = []
    if config.inversion:
        rules.append(_inversion_rule(trace, config))
    waits = collect_waits(trace)
    blocks = {block.start.line: block for block in _scan(trace)[0]}
    if config.budgets is not None:
        rules.append(_max_blocked_rule(trace, config.budgets, waits, blocks))
    if config.semaphore_min_priority is not None:
        rules.append(_no_semaphore_rule(trace, config.semaphore_min_priority, waits, blocks))
    return rules
