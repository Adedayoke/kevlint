# kevlint reference

Details behind the [README](../README.md).

## Command line

From the repository root, with `PYTHONPATH=host`:

```sh
python3 -m kevlint check trace.txt --html report.html
python3 -m kevlint check-all ci/traces.txt --reports-dir reports
```

| Option | Applies to | Effect |
|---|---|---|
| `--html FILE` | `check`, `capture` | Writes the HTML report |
| `--reports-dir DIR` | `check-all` | Directory for HTML reports (required) |
| `--summary FILE` | `check`, `check-all` | Appends a Markdown results table |
| `--config FILE` | all | Selects the rules |
| `--explain` | all | Adds AI explanations |
| `--debug` | all | Logs to stderr if the endpoint refused the seed and the request was retried without it |

## GitHub Action inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `trace` | yes | | `traceprinter` text output, captured with `tracelogger -w` |
| `report` | no | `kevlint-report.html` | Where to write the HTML report |
| `config` | no | | A `kevlint.toml` rules file. Without it, only `priority-inversion` runs |

The action fails the job on a violation and adds a results table to the job summary. It uses
its own Python 3.12 and leaves the job's `python3` alone.

## Rules and config

| Rule | What it flags | Settings |
|---|---|---|
| `priority-inversion` | In `THSEM` and `THMUTEX`, a thread with priority strictly between waiter and inferred holder ran on the holder's last CPU while the holder was in `THREADY` | `enabled`, `min_waiter_priority` |
| `max-blocked-time` | A named thread blocked strictly longer than its budget | `enabled`, `budgets`: `thread`, `max_ms`, optional `states`, `required` |
| `no-semaphore-high-priority` | Any entry to `THSEM` at or above a priority. The report suggests a mutex, since QNX mutexes use priority inheritance | `enabled`, `min_priority` |

The sample [`kevlint.toml`](../kevlint.toml) enables all three:

```toml
[rules.priority-inversion]
enabled = true
min_waiter_priority = 40

[rules.max-blocked-time]
enabled = true
budgets = [{ thread = "fusion_rt", max_ms = 100, required = true }]

[rules.no-semaphore-high-priority]
enabled = true
min_priority = 40
```

- **Explicit.** The config is read only with `--config`. Without it, only
  `priority-inversion` runs. If a file omits that rule it stays on, and the other two
  run only if configured.
- **Strict.** An unknown rule, key or state, a wrong type, a non-positive budget, a
  duplicate budget thread, or every rule disabled exits 2. A typo cannot silently turn a
  check off.
- **Budgets.** They count `THSEM` and `THMUTEX` unless `states` lists others, because
  `THRECEIVE` and `THNANOSLEEP` are usually idle waits. A budget applies to every thread
  with that name.
- **Missing threads.** A budget's thread that is not in the trace gives `NOT EVALUATED`
  with a warning, never PASS. With `required = true` it exits 2.
- **Partial blocks.** A block with no visible start, or still open at trace end, is
  reported as "at least" its length if already over budget. Otherwise it is not evaluated.
- **Grouping.** When findings from at least two rules share a thread and sync object,
  they are shown as one likely root cause. That is a grouping, not proof of one cause.

The sample's 100 ms suits these QEMU demos only. Set real budgets from traces of the real
target.

Choose budget threads with care: `THSEM` is also an idle wait for some workers. `softirq_0` in
`io-sock`, for example, spends seconds in `THSEM` on an idle system.

## `kevlint capture` and `kevlintd`

These parts need a QNX 8.0 target with an instrumented kernel, the QNX SDP on the host,
and an ssh key that logs in without a prompt. The sample `run.sh` starts this project's
QEMU image, which is not in the repository.

### Build and copy the target programs

```sh
source ~/qnx800/qnxsdp-env.sh
make -C target
ssh -p 2222 root@localhost 'mkdir -p /tmp/kldev'
scp -P 2222 target/kevlintd target/kevlintd_test target/fusion target/control target/sensor \
  root@localhost:/tmp/kldev/
```

### `kevlint capture`

`capture` starts the workload over ssh, runs `tracelogger -w`, copies the trace back,
decodes it, and then runs the normal check with the same exit status.

```sh
source ~/qnx800/qnxsdp-env.sh
PYTHONPATH=host python3 -m kevlint capture --seconds 3 --out fixtures/name --config kevlint.toml \
  --run 'cd /tmp/kldev; ./control & ./fusion --inversion & ./sensor & wait'
```

It writes `name.kev`, `name.txt` and `name.json`, which records how the trace was made.
The target comes from `--target user@host[:port]`, then `KEVLINT_TARGET`, then
`root@localhost:2222`.

Every failure exits 2 and writes nothing to the `--out` paths. Three guards stop a trace
that lacks the workload from passing:

- **Workload ended early.** If `--run` ends before tracing ends, the capture fails. An
  early live run traced an idle system and printed PASS, which is why this is an error.
- **Trace too short.** A trace covering under 90% of `--seconds` fails. `tracelogger`
  exits 0 and prints nothing when its disk fills, so its status cannot be trusted.
- **Required thread missing.** With `--config`, a budget with `required = true` exits 2
  if its thread is not in the trace.

### `kevlintd`

`kevlintd` is a QNX resource manager that lets any program mark points in a trace with
an ordinary file write.

```sh
cd /tmp/kldev && ./kevlintd                          # backgrounds itself; -f stays in front
echo "starting calibration" > /dev/kevlint/marker    # one user event in the trace
cat /dev/kevlint/status                              # version, uptime and counters
```

- Marker text is at most 128 bytes of printable ASCII. A longer write fails with
  `EMSGSIZE`, other bytes with `EINVAL`, and one trailing newline is allowed.
- `kevlintd` adds the writer's pid and tid, and pads text to avoid a text-losing quirk in
  QNX user string events. That behavior is measured in
  [qnx-trace-quirks.md](qnx-trace-quirks.md).
- Markers appear in the terminal and on the HTML timeline. They are context from the
  workload, not evidence of cause. They never change a verdict and are never sent to the
  LLM.
- A marker write is a blocking message, so keep markers sparse and outside the blocks you
  want to measure. The fusion demo writes them with `--markers`.
- `capture --check-kevlintd` exits 2 if `kevlintd` is not running, and otherwise records
  its status in `name.json`.

### Other capture details

- `--settle` is how many seconds (default 2) the workload runs before tracing starts.
- `--force` replaces existing output files, which are kept otherwise.
- A `--run` command containing `&` should end in `wait`.
- A workload still running after the trace gets 30 seconds to finish, then is left running on
  the target with a warning.
- A target given without a port uses port 22.
- The demo programs run for 30 seconds unless given `-d SECONDS`. Under load on QEMU, a 3-second
  trace finished up to 10 seconds after the workload started, so a short `-d` can get the
  capture rejected.
- `capture` also takes `--html`, `--explain` and `--debug`, as `check` does.

## AI explanations and their safeguards

`--explain` on `check`, `check-all` or `capture` adds a plain-English explanation of each
priority-inversion pattern to the terminal and HTML report. It uses an OpenAI-compatible
endpoint set by `KEVLINT_LLM_BASE_URL`, `KEVLINT_LLM_API_KEY` and `KEVLINT_LLM_MODEL`.
Without those, it prints one note and skips.

The design assumes the model can be wrong:

- **The verdict is already decided.** Explanations run after the result is printed. Any
  failure becomes a note, and the exit status does not change.
- **Minimal evidence.** Each pattern sends only its worst occurrence: the relevant raw
  trace lines and structured fields. It sends no file path or trace header.
- **Citations are checked.** The model must return exactly two claims, a summary and a
  cause, each citing trace lines. A claim with no lines, or a line that was not sent,
  rejects the whole explanation.
- **Values are checked.** A hex value, priority or duration must appear in that claim's
  cited lines or the fields. Otherwise the explanation is rejected and the reason shown.
- **kevlint writes the pointer.** The "where to look" line comes from the fields and is
  labeled as not AI, because a model wrongly said all three threads shared the semaphore.
- **Model text is untrusted.** It is HTML-escaped and stripped of control characters.
- **Key safety.** Redirects are not followed, plain http is refused except on localhost,
  and errors never include URLs or response bodies.
- **Not in CI.** The action and this repository's CI do not use `--explain`, and
  explanations never appear in the Markdown summary.

These checks prove that a citation exists and a number is in the evidence. They do not
prove the reasoning is right, so output stays labeled "claims were not verified".
Accuracy has been judged by hand on only one live response, on `demo_inversion`.
