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
in the evidence, not that the claim is correct. Hex values, priorities and
durations stated in a claim must also appear in its cited lines or the fields
sent, or the explanation is rejected. The "where to look" line is written by
kevlint, not the model. Explanations never change the
exit status. `--debug` prints a message to stderr if the endpoint refused the
seed and the request was retried without it.

`--config kevlint.toml` selects the rules. The file is only read when you pass
this flag. Without it, only priority-inversion runs, as above.

| Rule | Settings |
|---|---|
| `priority-inversion` | `enabled`, `min_waiter_priority` |
| `max-blocked-time` | `enabled`, `budgets`: `thread`, `max_ms`, optional `states` and `required` |
| `no-semaphore-high-priority` | `enabled`, `min_priority` |

See `kevlint.toml` for a working sample. Results, coverage and evidence are shown
per rule in the terminal, HTML report and summary. Any unknown setting, rule or
state name exits 2, so a typo cannot silently turn a check off.

A budget counts time in `THSEM` and `THMUTEX` unless `states` lists others, such
as `THSEND` or `THREPLY`. A breach is strictly longer than `max_ms`. If a budget's
thread is not in a trace, that budget is `NOT EVALUATED` with a warning, or exit 2
with `required = true`. The 100 ms in the sample suits the QEMU demos only: QEMU
timing is not realistic, so set real budgets from traces of the real target.

## Capturing from a target

`capture` records a trace on a QNX target, decodes it, and runs the normal check
with the same exit status. It needs `source ~/qnx800/qnxsdp-env.sh` first, for
`traceprinter`, and an ssh key that logs in to the target without a prompt.

```sh
PYTHONPATH=host python3 -m kevlint capture --seconds 3 --out fixtures/name \
  --run 'cd /tmp/kevdemo; ./control -d 8 & ./fusion --inversion -d 8 & ./sensor -d 8 & wait'
```

It writes `name.kev`, `name.txt` and `name.json` (how the trace was made), and only
if every step succeeds. Existing files are kept unless you pass `--force`. It also
takes `--config`, `--html`, `--explain` and `--debug`, as `check` does.

- **Target:** `--target user@host[:port]`, else `KEVLINT_TARGET`, else
  `root@localhost:2222`. A target given without a port uses port 22.
- **Workload:** `--run` is a shell command run by the target's shell. It starts
  `--settle` seconds (default 2) before tracing. It must outlast `--seconds`: if it
  ends before tracing ends, the capture exits 2 rather than judge a trace that may
  not contain it. A command with `&` should end in `wait`.
- A workload that keeps running after the trace gets 30 seconds to finish, then is
  left running on the target with a warning.

Two guards stop a trace without the workload from passing:

- **Workload ended early:** if the `--run` workload ends before tracing ends, the
  capture exits 2 and writes no files.
- **Required thread missing:** with `--config`, a budget with `required = true`
  exits 2 if its thread is not in the trace. The sample `kevlint.toml` requires
  `fusion_rt`. This needs a config: without `--config`, only the first guard applies.

| Exit status | Meaning |
|---|---|
| 0 | No violations |
| 1 | At least one violation |
| 2 | A trace, list, config or output file could not be used, a capture failed, or a required thread is missing |

## Trace markers with kevlintd

`kevlintd` is a QNX resource manager in `target/`. Each write to
`/dev/kevlint/marker` becomes one user event in the kernel trace, with the writer's
pid and tid added by `kevlintd`, and `/dev/kevlint/status` reports its version,
uptime and counters. Start it on the target before tracing. It puts itself in the
background (`-f` keeps it in the foreground):

```sh
./kevlintd
echo "starting calibration" > /dev/kevlint/marker
cat /dev/kevlint/status
```

Marker text is at most 128 bytes of printable ASCII. A longer write fails with
`EMSGSIZE` and other bytes with `EINVAL`; one trailing newline is allowed. A write
sends a message to `kevlintd`, so it affects scheduling. Keep markers sparse and
outside the blocks you want to measure. `markers_written` counts markers handed to
the kernel, including when no trace was running.

`check` shows the nearest marker before a block. The HTML timeline draws markers
inside the block on the lanes, and the nearest marker just outside it as an arrow at
the edge. Markers are context from the workload, not evidence of
cause, and they never change a verdict. They are not sent to `--explain` and do not
appear in the Markdown summary. The fusion demo writes them with `--markers`.

`capture --check-kevlintd` exits 2 if `kevlintd` is not running on the target, and
otherwise records its status in `name.json`. Behavior measured for user events is in
[docs/qnx-trace-quirks.md](docs/qnx-trace-quirks.md).

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
