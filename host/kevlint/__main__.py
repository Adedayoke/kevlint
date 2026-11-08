"""Command line: python -m kevlint check <trace.txt>
                python -m kevlint check-all <list.txt> --reports-dir <dir>

Exit status: 0 no violations, 1 violations found, 2 a trace, list or output
file could not be used.
"""
import argparse
import os
import sys
from pathlib import Path

from .inversion import HOLDER_NOTE, boost_text, check
from .parser import TraceError, parse_trace
from .report import render_html
from .summary import Entry, append_summary


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


def report(path, trace, result, out):
    print(f"kevlint check: {path}", file=out)
    print("rule: priority-inversion", file=out)
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
    print(
        f"blocks: {result.evaluated + skipped} total, "
        f"{result.evaluated} evaluated, {skipped} not evaluated",
        file=out,
    )
    for reason, count in sorted(result.not_evaluated.items()):
        print(f"  not evaluated: {count} ({reason})", file=out)
    if total:
        print(f"Note: every holder above is {HOLDER_NOTE}.", file=out)
    verdict = "FAIL" if total else "PASS"
    print(
        f"{verdict}: {total} violation(s) in {result.evaluated} evaluated block(s); "
        f"{skipped} block(s) not evaluated",
        file=out,
    )


def check_trace(path, html_path):
    """Check one trace, print its result, and return an Entry."""
    try:
        trace = parse_trace(path)
    except (OSError, TraceError) as error:
        print(f"kevlint: {error}", file=sys.stderr)
        return Entry(path, error=str(error))

    result = check(trace)
    report(path, trace, result, sys.stdout)
    if html_path:
        try:
            with open(html_path, "w", encoding="utf-8") as out:
                out.write(render_html(path, trace, result))
        except OSError as error:
            message = f"cannot write HTML report: {error}"
            print(f"kevlint: {message}", file=sys.stderr)
            return Entry(path, trace, result, error=message)
        print(f"HTML report: {html_path}")
    return Entry(path, trace, result)


def finish(entries, summary_path):
    """Write the optional summary and return the exit status."""
    status = 0
    if any(e.error is not None for e in entries):
        status = 2
    elif any(e.result.violations for e in entries):
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


def check_all(list_path, reports_dir, summary_path):
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
        entries.append(check_trace(path, str(Path(reports_dir) / f"{name}.html")))
    return finish(entries, summary_path)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="kevlint")
    commands = parser.add_subparsers(dest="command", required=True)
    check_command = commands.add_parser("check", help="check a traceprinter text file")
    check_command.add_argument("trace", help="output of traceprinter")
    check_command.add_argument("--html", metavar="FILE", help="also write an HTML report")
    check_command.add_argument("--summary", metavar="FILE", help="append a Markdown summary")
    all_command = commands.add_parser("check-all", help="check every trace in a list file")
    all_command.add_argument("list", help="file with one trace path per line")
    all_command.add_argument("--reports-dir", required=True, help="directory for HTML reports")
    all_command.add_argument("--summary", metavar="FILE", help="append a Markdown summary")
    args = parser.parse_args(argv)

    if args.command == "check-all":
        return check_all(args.list, args.reports_dir, args.summary)
    return finish([check_trace(args.trace, args.html)], args.summary)


if __name__ == "__main__":
    sys.exit(main())
