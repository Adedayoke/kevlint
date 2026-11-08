"""Command line: python -m kevlint check <trace.txt>

Exit status: 0 no violations, 1 violations found, 2 the trace could not be read.
"""
import argparse
import sys

from .inversion import check
from .parser import TraceError, parse_trace

HOLDER_NOTE = "inferred, not named by the trace"


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

    print(f"\n{total} violation(s), {result.evaluated} block(s) evaluated", file=out)
    for reason, count in sorted(result.not_evaluated.items()):
        print(f"  not evaluated: {count} ({reason})", file=out)
    if total:
        print(f"Note: every holder above is {HOLDER_NOTE}.", file=out)
    print("FAIL" if total else "PASS", file=out)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="kevlint")
    commands = parser.add_subparsers(dest="command", required=True)
    check_command = commands.add_parser("check", help="check a traceprinter text file")
    check_command.add_argument("trace", help="output of traceprinter")
    args = parser.parse_args(argv)

    try:
        trace = parse_trace(args.trace)
    except (OSError, TraceError) as error:
        print(f"kevlint: {error}", file=sys.stderr)
        return 2

    result = check(trace)
    report(args.trace, trace, result, sys.stdout)
    return 1 if result.violations else 0


if __name__ == "__main__":
    sys.exit(main())
