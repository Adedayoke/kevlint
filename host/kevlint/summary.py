"""Markdown summary for a CI job page."""
from dataclasses import dataclass

from .config import MAX_BLOCKED, NO_SEMAPHORE
from .inversion import HOLDER_NOTE, TYPICAL_FIXES
from .patterns import group_patterns
from .report import plain_summary
from . import rules as rule_status

PASS = "\u2705 PASS"
FAIL = "\u274c FAIL"
ERROR = "\u26a0\ufe0f ERROR"
WARNING_SIGN = "\u26a0\ufe0f"


@dataclass
class Entry:
    """One checked trace. error is set when it could not be checked or reported."""

    path: str
    trace: object = None
    result: object = None
    error: str = None
    rules: list = None

    @property
    def rule_errors(self):
        return [error for rule in self.rules or [] for error in rule.errors]

    @property
    def failed(self):
        if self.rules is not None:
            return any(rule.findings for rule in self.rules)
        return bool(self.result.violations)


def _cell(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def _thread_code(trace, thread):
    name = trace.thread_names.get(thread, "unnamed").replace("`", "'")
    return f"`{name}`"


def _coverage(result):
    skipped = sum(result.not_evaluated.values())
    total = result.evaluated + skipped
    line = f"{result.evaluated} of {total} blocks evaluated, {skipped} not evaluated"
    if result.unevaluated_outranks_evaluated:
        line += (
            f" {WARNING_SIGN} a not-evaluated block has higher priority "
            f"({result.not_evaluated_max_priority})"
        )
    return line


def _row(entry):
    path = f"`{_cell(entry.path)}`"
    if entry.error is not None:
        return f"| {path} | {ERROR} | n/a | n/a | {_cell(entry.error)} |"
    violations = entry.result.violations
    worst = "none"
    if violations:
        cycles = max(v.duration_cycles for v in violations)
        worst = f"{cycles / entry.trace.cycles_per_sec:.3f} s"
    verdict = FAIL if violations else PASS
    return f"| {path} | {verdict} | {len(violations)} | {worst} | {_coverage(entry.result)} |"


def _explanation(entry):
    lines = [f"#### Why `{entry.path}` failed", ""]
    for pattern in group_patterns(entry.result.violations):
        sentence = plain_summary(pattern, entry.trace, _thread_code)
        count = len(pattern.occurrences)
        lines.append(f"- {sentence} Seen {count} time{'' if count == 1 else 's'}.")
        for kind in sorted({v.kind for v in pattern.occurrences}, reverse=True):
            lines.append(f"  - **Typical fix:** {TYPICAL_FIXES[kind]}")
    lines.extend(["", f"Holders are {HOLDER_NOTE}.", ""])
    return lines


STATUS_TEXT = {
    rule_status.PASS: PASS,
    rule_status.FAIL: FAIL,
    rule_status.ERROR: ERROR,
    rule_status.NOT_EVALUATED: f"{WARNING_SIGN} NOT EVALUATED",
}


def _rule_coverage(rule):
    notes = [f"{WARNING_SIGN} {text}" for text in rule.errors + rule.warnings]
    return " ".join([rule.coverage, *notes])


def _rule_rows(entry):
    path = f"`{_cell(entry.path)}`"
    rows = []
    for rule in entry.rules or []:
        worst = "none" if rule.worst_ms is None else f"{rule.worst_ms:.3f} ms"
        rows.append(
            f"| {path} | `{rule.name}` | {STATUS_TEXT[rule.status]} | {len(rule.findings)} "
            f"| {worst} | {_cell(_rule_coverage(rule))} |"
        )
    if entry.error is not None:
        rows.append(f"| {path} | n/a | {ERROR} | n/a | n/a | {_cell(entry.error)} |")
    return rows


def _finding_groups(rule):
    """Count each thread's findings and find its longest, in first-seen order."""
    groups = {}
    for finding in rule.findings:
        count, worst = groups.get(finding.thread, (0, 0))
        groups[finding.thread] = (count + 1, max(worst, finding.duration_cycles))
    return groups.items()


def _rule_details(entry, rule):
    if not rule.findings:
        return []
    lines = [f"#### `{rule.name}` in `{entry.path}`", ""]
    trace = entry.trace
    for thread, (count, worst) in _finding_groups(rule):
        millis = worst * 1000 / trace.cycles_per_sec
        times = f"{count} time{'' if count == 1 else 's'}"
        if rule.name == MAX_BLOCKED:
            budget = next(
                b.budget.max_ms for b in rule.budgets
                if b.budget.thread == trace.thread_names.get(thread)
            )
            lines.append(
                f"- {_thread_code(trace, thread)} exceeded its {budget:g} ms budget {times}, "
                f"worst {millis:.3f} ms."
            )
        else:
            lines.append(
                f"- {_thread_code(trace, thread)} blocked on a semaphore at high priority {times}, "
                f"worst {millis:.3f} ms."
            )
    if rule.name == NO_SEMAPHORE:
        lines.append(f"- **Typical fix:** {TYPICAL_FIXES['THSEM']}")
    lines.append("")
    return lines


def render_rule_summary(entries):
    lines = [
        "### kevlint results by rule",
        "",
        "| Trace | Rule | Result | Findings | Worst block | Coverage |",
        "|---|---|---|---|---|---|",
    ]
    for entry in entries:
        lines.extend(_rule_rows(entry))
    lines.append("")
    for entry in entries:
        if entry.rules is None:
            continue
        if entry.result is not None and entry.result.violations:
            lines.extend(_explanation(entry))
        for rule in entry.rules:
            if rule.inversion is None:
                lines.extend(_rule_details(entry, rule))
    return "\n".join(lines) + "\n"


def render_summary(entries):
    if any(entry.rules is not None for entry in entries):
        return render_rule_summary(entries)
    lines = [
        "### kevlint priority-inversion results",
        "",
        "| Trace | Result | Violations | Worst block | Coverage |",
        "|---|---|---|---|---|",
    ]
    lines.extend(_row(entry) for entry in entries)
    lines.append("")
    for entry in entries:
        if entry.error is None and entry.result.violations:
            lines.extend(_explanation(entry))
    return "\n".join(lines) + "\n"


def append_summary(entries, path):
    with open(path, "a", encoding="utf-8") as out:
        out.write(render_summary(entries))
