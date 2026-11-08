"""Command line: python -m kevlint check <trace.txt>
                python -m kevlint check-all <list.txt> --reports-dir <dir>

Exit status: 0 no violations, 1 violations found, 2 a trace, list, config or
output file could not be used, or a required thread is missing.
"""
import argparse
import logging
import os
import sys
from pathlib import Path

from .config import NO_SEMAPHORE, ConfigError
from .config import load_config as load_rules_config
from .explain import Explainer, print_explanations
from .inversion import BELOW_MINIMUM, HOLDER_NOTE, boost_text, check
from .llm import LlmClient, load_config
from .parser import TraceError, parse_trace
from .report import render_html
from .rules import (
    line_text,
    overall_status,
    root_cause_headline,
    root_cause_text,
    root_causes,
    run_rules,
)
from .summary import Entry, append_summary

EXPLAIN_HELP = "add AI-generated explanations of violations (never changes the exit status)"
CONFIG_HELP = "TOML rules file; without it only priority-inversion runs"
DEBUG_HELP = "print debug messages to stderr, such as a retry without the LLM seed"


def resolved_line(trace, result):
    """One line for the worst resolved wait, ASCII so any CI log can print it."""
    wait = max(result.resolved, key=lambda r: r.duration_cycles)
    name = lambda thread: trace.thread_names.get(thread, f"{thread[0]}/{thread[1]}")
    millis = wait.duration_cycles * 1000 / trace.cycles_per_sec
    line = (
        f"Resolved correctly: {name(wait.waiter)} ({wait.waiter_priority}) waited "
        f"{millis:.1f} ms on {name(wait.holder)} [holder inferred], "
        f"{boost_text(wait, arrow='->')}"
    )
    if wait.middle is not None:
        action = "ran" if wait.middle_ran else "stayed READY"
        line += f"; {name(wait.middle)} ({wait.middle_priority}) {action}"
    return line


def inversion_details(trace, result, out):
    """Print each violation, then the rule's counts and coverage."""
    total = len(result.violations)
    for number, v in enumerate(result.violations, 1):
        print(f"\nVIOLATION {number} of {total}", file=out)
        print(
            f"  waiter: {trace.describe(v.waiter)} priority {v.waiter_priority}, "
            f"{v.kind} on sync_p {v.sync_p}",
            file=out,
        )
        print(
            f"  holder: {trace.describe(v.holder)} priority {v.holder_priority} "
            f"[{HOLDER_NOTE}: issued {v.release_call} that released the waiter]",
            file=out,
        )
        print(
            f"  middle: {trace.describe(v.middle)} priority {v.middle_priority}",
            file=out,
        )
        print(
            f"  block:  start {trace.seconds(v.start_cycles):.6f} s, "
            f"end {trace.seconds(v.end_cycles):.6f} s after first trace event",
            file=out,
        )
        print(
            f"  lines:  wait call {v.wait_call_line}, block start {v.block_start_line}, "
            f"middle running {v.middle_line}, release {v.release_line}, "
            f"block end {v.block_end_line}",
            file=out,
        )

    skipped = sum(result.not_evaluated.values())
    if result.resolved:
        print(f"\n{resolved_line(trace, result)}", file=out)
    print(f"\n{total} violation(s)", file=out)
    excluded = f", {result.excluded} excluded by config" if result.excluded else ""
    print(
        f"blocks: {result.evaluated + skipped + result.excluded} total, "
        f"{result.evaluated} evaluated, {skipped} not evaluated{excluded}",
        file=out,
    )
    for reason, count in sorted(result.not_evaluated.items()):
        print(f"  not evaluated: {count} ({reason})", file=out)
    if result.excluded:
        print(f"  excluded by config: {result.excluded} ({BELOW_MINIMUM})", file=out)
    if total:
        print(f"Note: every holder above is {HOLDER_NOTE}.", file=out)


def report(path, trace, result, out):
    print(f"kevlint check: {path}", file=out)
    print("rule: priority-inversion", file=out)
    inversion_details(trace, result, out)
    total = len(result.violations)
    skipped = sum(result.not_evaluated.values())
    verdict = "FAIL" if total else "PASS"
    print(
        f"{verdict}: {total} violation(s) in {result.evaluated} evaluated block(s); "
        f"{skipped} block(s) not evaluated",
        file=out,
    )


def print_finding(trace, label, number, total, finding, out):
    millis = finding.duration_cycles * 1000 / trace.cycles_per_sec
    print(f"\n  {label} {number} of {total}", file=out)
    where = f" on sync_p {finding.sync_p}" if finding.sync_p else ""
    print(
        f"    thread:  {trace.describe(finding.thread)} priority {finding.priority}, "
        f"{finding.state}{where}",
        file=out,
    )
    blocked = (
        f"{millis:.3f} ms"
        if finding.end_cycles is not None
        else f"at least {millis:.3f} ms (still open at trace end)"
    )
    if finding.budget_ms is not None:
        blocked += f", budget {finding.budget_ms:g} ms, over by {millis - finding.budget_ms:.3f} ms"
    print(f"    blocked: {blocked}", file=out)
    if finding.holder is not None:
        print(
            f"    holder:  {trace.describe(finding.holder)} "
            f"[{HOLDER_NOTE}: issued {finding.release_call} that released the waiter]",
            file=out,
        )
    print(f"    lines:   {line_text(finding.lines)}", file=out)


def print_budget_rows(rule, out):
    for row in rule.budgets:
        worst = "no blocks" if row.worst_ms is None else f"worst {row.worst_ms:.3f} ms"
        print(
            f"  budget {row.budget.thread}: {row.budget.max_ms:g} ms on "
            f"{', '.join(row.budget.states)}: {row.status}, {row.breaches} breach(es) in "
            f"{row.evaluated} evaluated block(s), {worst}",
            file=out,
        )


def report_rules(path, trace, rules, config, out):
    print(f"kevlint check: {path}", file=out)
    print(f"config: {config.path}", file=out)
    if config.disabled:
        print(f"not enabled: {', '.join(config.disabled)}", file=out)

    for rule in rules:
        print(f"\nRULE {rule.name}: {rule.status}", file=out)
        if rule.inversion is not None:
            inversion_details(trace, rule.inversion, out)
        else:
            print_budget_rows(rule, out)
            if rule.name == NO_SEMAPHORE:
                print(f"  flags THSEM at priority {config.semaphore_min_priority} or above", file=out)
            label = "BREACH" if rule.budgets else "FINDING"
            for number, finding in enumerate(rule.findings, 1):
                print_finding(trace, label, number, len(rule.findings), finding, out)
            print(f"\n  coverage: {rule.coverage}", file=out)
            for reason, count in sorted(rule.not_evaluated.items()):
                print(f"    not evaluated: {count} ({reason})", file=out)
        for warning in rule.warnings:
            print(f"  WARNING: {warning}", file=out)
        for error in rule.errors:
            print(f"  ERROR: {error}", file=out)

    headline = root_cause_headline(trace, rules)
    if headline is not None:
        print("\nROOT CAUSES", file=out)
        print(f"  {headline}", file=out)
        for cause in root_causes(rules):
            counts = ", ".join(f"{name} {count}" for name, count in cause.counts.items())
            print(f"  - {root_cause_text(trace, cause)}: {counts}", file=out)

    print("\nRESULTS BY RULE", file=out)
    for rule in rules:
        print(f"  {rule.name}: {rule.status}, {len(rule.findings)} finding(s), {rule.coverage}", file=out)
    findings = sum(len(rule.findings) for rule in rules)
    print(f"{overall_status(rules)}: {findings} finding(s) across {len(rules)} rule(s)", file=out)


def make_explainer(enabled):
    """Return an Explainer, or None if --explain is off or not configured."""
    if not enabled:
        return None
    config, problem = load_config(os.environ)
    if config is None:
        print(f"kevlint: explanations skipped: {problem}")
        return None
    return Explainer(LlmClient(config))


def explain_trace(explainer, path, trace, result):
    """Print explanations and return them. Never raises: the verdict is already final."""
    try:
        explanations = explainer.explain(path, trace, result)
        print_explanations(trace, result, explanations, sys.stdout)
        return explanations
    except Exception as error:
        print(f"kevlint: explanations unavailable: internal error ({type(error).__name__})")
        return None


def check_trace(path, html_path, explainer=None, config=None):
    """Check one trace, print its result, and return an Entry."""
    try:
        trace = parse_trace(path)
    except (OSError, TraceError) as error:
        print(f"kevlint: {error}", file=sys.stderr)
        return Entry(path, error=str(error))

    if config is None:
        rules = None
        result = check(trace)
        report(path, trace, result, sys.stdout)
    else:
        rules = run_rules(trace, config)
        result = next((rule.inversion for rule in rules if rule.inversion is not None), None)
        report_rules(path, trace, rules, config, sys.stdout)
        for rule in rules:
            for error in rule.errors:
                print(f"kevlint: {path}: {error}", file=sys.stderr)
    explanations = None
    if explainer is not None and result is not None and result.violations:
        explanations = explain_trace(explainer, path, trace, result)
    if html_path:
        try:
            with open(html_path, "w", encoding="utf-8") as out:
                out.write(render_html(path, trace, result, explanations, rules, config))
        except OSError as error:
            message = f"cannot write HTML report: {error}"
            print(f"kevlint: {message}", file=sys.stderr)
            return Entry(path, trace, result, error=message, rules=rules)
        print(f"HTML report: {html_path}")
    return Entry(path, trace, result, rules=rules)


def finish(entries, summary_path):
    """Write the optional summary and return the exit status."""
    status = 0
    if any(e.error is not None or e.rule_errors for e in entries):
        status = 2
    elif any(e.failed for e in entries):
        status = 1
    if summary_path:
        try:
            append_summary(entries, summary_path)
        except OSError as error:
            print(f"kevlint: cannot write summary: {error}", file=sys.stderr)
            return 2
    return status


def read_trace_list(path):
    """Return the traces in a list file, ignoring blank lines and # comments."""
    with open(path, encoding="utf-8") as lines:
        stripped = (line.strip() for line in lines)
        return [line for line in stripped if line and not line.startswith("#")]


def check_all(list_path, reports_dir, summary_path, explainer=None, config=None):
    """Check every listed trace, never stopping early, and return the exit status."""

    def list_error(message):
        print(f"kevlint: {message}", file=sys.stderr)
        return finish([Entry(list_path, error=message)], summary_path)

    try:
        paths = read_trace_list(list_path)
    except OSError as error:
        return list_error(f"cannot read trace list: {error}")
    if not paths:
        return list_error("the trace list is empty")

    names = [Path(path).stem for path in paths]
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        return list_error(f"two listed traces would share a report name: {', '.join(repeated)}")

    try:
        os.makedirs(reports_dir, exist_ok=True)
    except OSError as error:
        return list_error(f"cannot create reports directory: {error}")

    entries = []
    for path, name in zip(paths, names):
        if entries:
            print()
        entries.append(check_trace(path, str(Path(reports_dir) / f"{name}.html"), explainer, config))
    return finish(entries, summary_path)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="kevlint")
    commands = parser.add_subparsers(dest="command", required=True)
    check_command = commands.add_parser("check", help="check a traceprinter text file")
    check_command.add_argument("trace", help="output of traceprinter")
    check_command.add_argument("--html", metavar="FILE", help="also write an HTML report")
    check_command.add_argument("--summary", metavar="FILE", help="append a Markdown summary")
    check_command.add_argument("--explain", action="store_true", help=EXPLAIN_HELP)
    check_command.add_argument("--debug", action="store_true", help=DEBUG_HELP)
    check_command.add_argument("--config", metavar="FILE", help=CONFIG_HELP)
    all_command = commands.add_parser("check-all", help="check every trace in a list file")
    all_command.add_argument("list", help="file with one trace path per line")
    all_command.add_argument("--reports-dir", required=True, help="directory for HTML reports")
    all_command.add_argument("--summary", metavar="FILE", help="append a Markdown summary")
    all_command.add_argument("--explain", action="store_true", help=EXPLAIN_HELP)
    all_command.add_argument("--debug", action="store_true", help=DEBUG_HELP)
    all_command.add_argument("--config", metavar="FILE", help=CONFIG_HELP)
    args = parser.parse_args(argv)

    if args.debug:
        logging.basicConfig(stream=sys.stderr, format="kevlint debug: %(message)s")
        logging.getLogger("kevlint").setLevel(logging.DEBUG)
    config = None
    if args.config:
        try:
            config = load_rules_config(args.config)
        except ConfigError as error:
            print(f"kevlint: {error}", file=sys.stderr)
            return finish([Entry(args.config, error=str(error))], args.summary)
    explainer = make_explainer(args.explain)
    if args.command == "check-all":
        return check_all(args.list, args.reports_dir, args.summary, explainer, config)
    return finish([check_trace(args.trace, args.html, explainer, config)], args.summary)


if __name__ == "__main__":
    sys.exit(main())
