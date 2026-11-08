"""Markdown summary for a CI job page."""
from dataclasses import dataclass

from .inversion import HOLDER_NOTE, TYPICAL_FIXES
from .patterns import group_patterns
from .report import plain_summary

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


def render_summary(entries):
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
