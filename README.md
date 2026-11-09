# kevlint

kevlint turns QNX 8.0 kernel traces into pass/fail CI checks. It records a trace from
a QNX target, or reads one already decoded, and checks it against structural timing
rules such as "a priority 50 thread must not wait behind an unrelated priority 30
thread". A violation exits non-zero, so the build fails. Every finding cites the exact
trace lines behind it, and every PASS says how many blocks it could not evaluate. An
optional LLM explains violations from those same lines, and can never change the
verdict.

<!-- TODO(screenshots): replace both placeholders below, then delete this comment.
     Regenerate the pages with the two `check` commands in "Run it locally". -->

> **[SCREENSHOT PLACEHOLDER 1: BEFORE]**
> HTML report for `fixtures/demo_inversion.txt`, showing FAIL and the timeline.
> Suggested file: `docs/images/report-before.png`

> **[SCREENSHOT PLACEHOLDER 2: AFTER]**
> HTML report for `fixtures/demo_fixed.txt`, showing PASS and the priority boost.
> Suggested file: `docs/images/report-after.png`

The two reports come from the same demo workload, recorded on QNX 8.0 under QEMU. A
priority 10 thread holds a lock that a priority 50 thread needs, while a priority 30
thread hogs the CPU. The only change is the lock.

| With `kevlint.toml` | Before: semaphore (`demo_inversion`) | After: priority-inheritance mutex (`demo_fixed`) |
|---|---|---|
| `priority-inversion` | FAIL, 10 findings | PASS, 11 blocks evaluated |
| `max-blocked-time` (100 ms) | FAIL, 10 findings | PASS, 11 blocks evaluated |
| `no-semaphore-high-priority` | FAIL, 11 findings | PASS, 21 entries evaluated |
| Worst `fusion_rt` block | 249.1 ms | 24.3 ms |
| Exit status | 1 | 0 |

The durations are QEMU timings, so only their contrast means anything.

**Contents:** [Quick start](#quick-start) ·
[How it works](#how-it-works) ·
[Rules and config](#rules-and-config) ·
[`kevlint capture` and `kevlintd`](#kevlint-capture-and-kevlintd) ·
[AI explanations](#ai-explanations-and-their-safeguards) ·
[Validation](#validation) ·
[Limitations](#honest-limitations) ·
[Evidence of QNX use](#evidence-of-qnx-use) ·
[License](#license) ·
[Architecture](docs/architecture.md)

## Quick start

### Run it locally

This needs Python 3.11 or newer, with no packages to install and no QNX tools. Decoded
traces from a real QNX 8.0 target are committed in `fixtures/`.

```sh
git clone https://github.com/Adedayoke/kevlint.git
cd kevlint
export PYTHONPATH=host

python3 -m kevlint check fixtures/demo_inversion.txt --config kevlint.toml --html before.html
echo "exit status: $?"    # 1: violations found

python3 -m kevlint check fixtures/demo_fixed.txt --config kevlint.toml --html after.html
echo "exit status: $?"    # 0: no violations

python3 -m unittest discover -s host/tests -t host
```

Open `before.html` and `after.html` in a browser. Each is one self-contained file.

### Use it in CI

CI checks a committed `traceprinter` text file, recorded with `tracelogger -w`. It does
not need QNX on the runner.

```yaml
steps:
  - uses: actions/checkout@v7

  - uses: Adedayoke/kevlint@main
    with:
      trace: traces/my_system.txt
      report: reports/my_system.html
      config: kevlint.toml

  - uses: actions/upload-artifact@v7
    if: always()
    with:
      name: kevlint-report
      path: reports/my_system.html
```

| Input | Required | Default | Description |
|---|---|---|---|
| `trace` | yes | | `traceprinter` text output, captured with `tracelogger -w` |
| `report` | no | `kevlint-report.html` | Where to write the HTML report |
| `config` | no | | A `kevlint.toml` rules file. Without it, only `priority-inversion` runs |

The action fails the job on a violation and adds a results table to the job summary.
It uses its own Python 3.12 and leaves the job's `python3` alone.

This repository's own CI, in `.github/workflows/ci.yml`, runs the unit tests and then
every trace listed in `ci/traces.txt`. [PR #1](https://github.com/Adedayoke/kevlint/pull/1)
adds the inversion trace to that list, so its check is **red on purpose**. It shows
what a failing build looks like and must never be merged.

## How it works

1. **Record.** `tracelogger -w` records the kernel's events on the QNX target. Only wide
   mode includes thread priority and policy.
2. **Decode.** `traceprinter` turns the `.kev` file into text.
3. **Parse.** kevlint keeps thread state changes, kernel calls, thread names and markers.
4. **Check.** Each rule looks for blocked threads and judges them by structure:
   priorities, sync objects and who ran when.
5. **Report.** The result goes to the terminal, an HTML report with a timeline, and a
   Markdown job summary. The exit status drives CI.

The trace never names the thread holding a lock, so kevlint infers it: the holder is the
thread whose post or unlock on the same `sync_p`, in the same process, released the
waiter. A **priority inversion** is a thread with priority strictly between waiter and
holder running on the holder's last CPU while the holder was in `THREADY`.

The reasoning behind these choices is in [docs/architecture.md](docs/architecture.md).

| Exit status | Meaning |
|---|---|
| 0 | No violations |
| 1 | At least one violation |
| 2 | A trace, list, config or output file could not be used, a capture failed, or a required thread is missing |

## Rules and config

| Rule | What it flags | Settings |
|---|---|---|
| `priority-inversion` | The inversion defined above, in `THSEM` and `THMUTEX` | `enabled`, `min_waiter_priority` |
| `max-blocked-time` | A named thread blocked strictly longer than its budget | `enabled`, `budgets`: `thread`, `max_ms`, optional `states`, `required` |
| `no-semaphore-high-priority` | Any entry to `THSEM` at or above a priority. The report suggests a mutex, since QNX mutexes use priority inheritance | `enabled`, `min_priority` |

The sample [`kevlint.toml`](kevlint.toml) enables all three:

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
  [docs/qnx-trace-quirks.md](docs/qnx-trace-quirks.md).
- Markers appear in the terminal and on the HTML timeline. They are context from the
  workload, not evidence of cause. They never change a verdict and are never sent to the
  LLM.
- A marker write is a blocking message, so keep markers sparse and outside the blocks you
  want to measure. The fusion demo writes them with `--markers`.
- `capture --check-kevlintd` exits 2 if `kevlintd` is not running, and otherwise records
  its status in `name.json`.

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

## Validation

- **Unit tests.** 414 tests pass, using real traces in `fixtures/`. They never call a real
  LLM (`host/tests/fake_llm.py`) or a real target (`host/tests/fake_target.py`).
- **Target test program.** `target/kevlintd_test.c` exercises `kevlintd` on QNX. In the
  capture `fixtures/kevlintd_lengths.txt`, all 128 marker lengths decode exactly.
- **CI.** Every push to `main` and every PR runs the tests and checks the listed traces. A separate job proves the
  action passes a clean trace and fails one with a violation.
- **Live captures.** Four repeat captures of the inversion demo gave FAIL. An 81-capture batch
  compared `tracelogger` at default and raised priority.

**Real QNX activity with no demo workload.** kevlint ran on idle (5 s), a busy filesystem
(5 s), network traffic (5 s), a mix of these (1 s), and two 30-second mixed captures, all
under QEMU. Nothing crashed. Only `system_idle` and `system_busy` are committed, so the
totals below cannot be reproduced from this repository alone.

- **Structural rules: zero findings.** `priority-inversion` found no violation in 3,630
  evaluated blocks, and `no-semaphore-high-priority` flagged none of 296 `THSEM` entries.
  This is not full coverage: 19,292 blocks were not evaluated, mostly in `THRWLOCK_WRITE`.
- **`max-blocked-time`: 26 findings from 100 ms budgets chosen for this test.** Thirteen on
  `softirq_0` are false positives from that choice, because it is an idle worker that waits
  on a semaphore for seconds. Thirteen on `fsys_resmgr` (111–355 ms) are real waits on the
  disk path, not defects, since emulated disk timing says little about real hardware.
- **Scale.** The longest trace, 1.5 million events and 215 MB of text, took about 20 seconds
  and 0.9 GB of memory.

That run found and fixed real bugs:

- A capture that filled the disk still printed PASS. It now exits 2.
- An unlock logged just before the waiter's block event was missed.
- `THCREATE` from the trace-start state dump split one block into two.
- Blocks already running when the trace began were printed as exact lengths.
- The inversion rule took 43 s on a 1.35M-event trace, and now takes 11 s.
- Blocks in `THRWLOCK_WRITE`, `THRWLOCK_READ` and `THMUON_MUTEX` were missing from coverage
  counts, so a PASS hid them.

## Honest limitations

**What has been tested**

- Everything ran on QNX 8.0.0 x86_64 under QEMU with 2 CPUs. It has never run on real
  hardware, and QEMU timing is not realistic. The durations here say nothing about
  production behavior.
- CI checks committed traces. It does not boot QNX or capture a fresh trace.

**What a PASS does not mean**

- A trace is a short window, so a PASS covers only what happened in it.
- `priority-inversion` evaluates only `THSEM` and `THMUTEX`. On `system_busy` it evaluates
  50 blocks and leaves 299 not evaluated, mostly `THRWLOCK_WRITE`. Those are counted and
  reported, not checked.
- Mutex re-acquires after a condvar wait are not evaluated, because no wait call matches
  them.
- On `demo_normal` with the sample config, `priority-inversion` evaluates 0 blocks: 60 are below
  `min_waiter_priority` and 1 is not evaluated. That PASS is empty, and the output says so.
- A long block with no thread of intermediate priority is not an inversion. Only a budget
  on `max-blocked-time` catches it, and budgets are absolute thresholds.

**What can be wrong**

- Holders are inferred. A shared sync object released from another process is not matched,
  and that block is reported as having no visible release.
- Kernel internals in `procnto` (pid 1) are outside kevlint's scope. A high-priority
  not-evaluated kernel block gets a note instead of a warning.
- AI explanations can be wrong in ways the checks cannot catch, as described above.

**Practical limits**

- Tracing changes what it measures. See
  [observer effects](docs/architecture.md#observer-effects).
- Captures of the demo can be rejected under load, when tracing finishes after the workload has
  ended. One batch rejected 9 of 12 at `-d 8` and a later one 2 of 36. Why that changed is
  unknown.
- Memory is about 580 bytes per trace event, which matters for long traces.
- Target setup is manual. `kevlintd` does not start on its own after a reboot.
- The `kevlintd` user-event workarounds are based on measured behavior whose cause is unknown.

## Evidence of QNX use

kevlint is built on QNX-specific interfaces on both sides. Every row below can be found in
the named file and function.

### Target code: C, built with `qcc -Vgcc_ntox86_64` (`target/Makefile`)

| File | Function | QNX-specific APIs |
|---|---|---|
| `target/kevlintd.c` | `emit_marker` | `TraceEvent`, `_NTO_TRACE_INSERTUSRSTREVENT`, `resmgr_context_t` |
| `target/kevlintd.c` | `marker_write` | `iofunc_write_verify`, `resmgr_msgread`, `io_write_t`, `_IO_WRITE_GET_NBYTES`, `_IO_SET_WRITE_NBYTES`, `_RESMGR_NPARTS` |
| `target/kevlintd.c` | `status_read` | `iofunc_read_verify`, `io_read_t`, `_IO_READ_GET_NBYTES`, `_IO_SET_READ_NBYTES`, `SETIOV`, `_RESMGR_NPARTS` |
| `target/kevlintd.c` | `create_dispatch` | `ChannelCreate`, `_NTO_CHF_UNBLOCK`, `_NTO_CHF_DISCONNECT`, `_NTO_CHF_COID_DISCONNECT`, `_NTO_CHF_INHERIT_RUNMASK`, `dispatch_create`, `dispatch_create_channel` |
| `target/kevlintd.c` | `attach_file` | `resmgr_attach`, `iofunc_attr_init`, `resmgr_attr_t`, `_FTYPE_ANY` |
| `target/kevlintd.c` | `main` | `iofunc_func_init`, `iofunc_funcs_t`, `ThreadCtl`, `_NTO_TCTL_RUNMASK`, `dispatch_context_alloc`, `procmgr_daemon`, `dispatch_block`, `dispatch_handler` |
| `target/kevdemo.h` | `setup_self` | `ThreadCtl`, `_NTO_TCTL_RUNMASK` |
| `target/kevdemo.h` | `connect_retry` | `name_open` |
| `target/kevdemo.h` | `server_receive` | `MsgReceive`, `MsgReply`, `MsgError`, `ConnectDetach`, `_PULSE_CODE_DISCONNECT`, `_IO_CONNECT` |
| `target/fusion.c` | `gate_init` | `ChannelCreate`, `ConnectAttach`, `ND_LOCAL_NODE`, `_NTO_SIDE_CHANNEL` |
| `target/fusion.c` | `gate_wait` | `MsgReceivePulse` |
| `target/fusion.c` | `gate_open` | `MsgSendPulse` |
| `target/fusion.c` | `main` | `name_attach`, `MsgSend`, `MsgReply` |
| `target/control.c` | `main` | `name_attach`, `MsgReply` |
| `target/sensor.c` | `sender` | `MsgSend` |
| `target/kevlintd_test.c` | `write_from_cpu` | `ThreadCtl`, `_NTO_TCTL_RUNMASK` |

The demo also uses standard thread calls, which are not QNX-specific: `sem_init`,
`sem_wait` and `sem_post` in `lock_init`, `lock_take` and `lock_give`,
`pthread_mutexattr_setprotocol` in `lock_init` (`target/fusion.c`), and
`pthread_setschedparam` and `clock_nanosleep` (`target/kevdemo.h`).

### Host code: QNX tools and trace format

| File | Function or constant | QNX-specific use |
|---|---|---|
| `host/kevlint/capture.py` | `tracelogger_command` | Runs `tracelogger -w` on the target |
| `host/kevlint/capture.py` | `Session.preflight` | Confirms `tracelogger` exists on the target |
| `host/kevlint/capture.py` | `Session.kevlintd_status` | Reads the `/dev/kevlint/status` resource manager path |
| `host/kevlint/capture.py` | `check_host_tools` | Requires `QNX_HOST` and `traceprinter` from the QNX SDP |
| `host/kevlint/capture.py` | `decode` | Runs `traceprinter` on the `.kev` file |
| `host/kevlint/capture.py` | `trace_span` | Reads `TRACE_CYCLES_PER_SEC` from the trace header |
| `host/kevlint/parser.py` | `parse_trace` | Parses `traceprinter` output: `THREAD` and `KER_CALL` classes, `PROCTHREAD_NAME`, `TRACE_CYCLES_PER_SEC` |
| `host/kevlint/parser.py` | `parse_marker` | Decodes the `USREVENT` markers that `kevlintd` writes |
| `host/kevlint/inversion.py` | `CALLS` | Kernel calls `SYNC_SEM_WAIT/85`, `SYNC_SEM_POST/84`, `SYNC_MUTEX_LOCK/80`, `SYNC_MUTEX_UNLOCK/81` |
| `host/kevlint/inversion.py` | `RUNNING`, `READY` | Thread states `THRUNNING`, `THREADY` |
| `host/kevlint/inversion.py` | `UNCHECKED_LOCK_STATES` | Thread states `THRWLOCK_WRITE`, `THRWLOCK_READ`, `THMUON_MUTEX` |
| `host/kevlint/rules.py` | `_no_semaphore_rule` | Flags entries to thread state `THSEM` |

### Real QNX artifacts

- `fixtures/*.kev` are binary kernel traces recorded on QNX 8.0.0 x86_64, each with its
  decoded `.txt` beside it.
- [docs/qnx-trace-quirks.md](docs/qnx-trace-quirks.md) records measured behavior of
  `TraceEvent` user string events, `trace_logf` and `trace_nlogf`.

## License

MIT, see [LICENSE](LICENSE). The QNX SDP and the QNX target image are not part of this
repository and need their own license from QNX.
