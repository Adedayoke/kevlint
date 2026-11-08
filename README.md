# kevlint

Automated timing-rule checks for QNX 8.0 kernel traces. kevlint reads
`traceprinter` text output, checks it for priority inversions, writes an HTML
report, and exits non-zero on a violation so CI fails. It needs only the Python
standard library, and no QNX tools once the trace has been decoded.

Capture with `tracelogger -w`, because only wide mode records thread priorities,
then decode with `traceprinter -f x.kev > x.txt`.

## What it reports

A block is a thread in `THSEM` or `THMUTEX`. It is a violation when a thread with
priority strictly between the waiter and the holder ran on the holder's last CPU
while the holder was in `THREADY`.

- **Holders are inferred, not named by the trace.** Each one is the thread whose
  post or unlock on the same sync object released the waiter.
- **A pass is not full coverage.** Blocks with no matching wait call or no visible
  release cannot be evaluated. Every result states how many were not evaluated.

## Command line

From the repository root, kevlint runs with `PYTHONPATH=host`:

```sh
PYTHONPATH=host python3 -m kevlint check trace.txt --html report.html
PYTHONPATH=host python3 -m kevlint check-all ci/traces.txt --reports-dir reports
```

`--summary <file>` appends a Markdown results table to a file.

`--explain` adds an AI-generated explanation of each violation pattern to the
terminal output and HTML report. It sends the worst occurrence's trace lines and
structured fields to an OpenAI-compatible endpoint set by `KEVLINT_LLM_BASE_URL`,
`KEVLINT_LLM_API_KEY` and `KEVLINT_LLM_MODEL`. Without them it prints one note
and skips explanations. Every claim must cite trace lines, and an explanation
citing a line that was not sent is rejected. That check proves a citation exists
in the evidence, not that the claim is correct. Explanations never change the
exit status. `--debug` prints a message to stderr if the endpoint refused the
seed and the request was retried without it.

| Exit status | Meaning |
|---|---|
| 0 | No violations |
| 1 | At least one violation |
| 2 | A trace, list or output file could not be used |

## GitHub Action

Other repositories can run kevlint on a committed `traceprinter` text file:

```yaml
steps:
  - uses: actions/checkout@v7

  - uses: Adedayoke/kevlint@main
    with:
      trace: traces/my_system.txt
      report: reports/my_system.html

  - uses: actions/upload-artifact@v7
    if: always()
    with:
      name: kevlint-report
      path: reports/my_system.html
```

| Input | Required | Default | Description |
|---|---|---|---|
| `trace` | yes | | Path to `traceprinter` text output, captured with `tracelogger -w` |
| `report` | no | `kevlint-report.html` | Where to write the HTML report |

The action fails the job when the trace has violations and adds a results table
to the job summary. It uses the runner's `python3`, and has been tested with
Python 3.12.

## This repository's CI

`.github/workflows/ci.yml` runs the unit tests, then checks every trace listed in
`ci/traces.txt`. It uploads the HTML reports as the `kevlint-reports` artifact and
writes the results to the job summary. To check another trace, add its path to
that file.
