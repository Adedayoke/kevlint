# kevlint: automated timing-rule checks for QNX 8.0 kernel traces

## What this is
Records QNX kernel traces, checks them against timing rules (e.g. "no priority
inversion on threads above priority 40"), exits non-zero on violations so CI
fails, and uses an LLM only to explain violations using the exact trace events
found.

## Verified environment (do not assume anything beyond this)
- Host: Ubuntu 24.04 in WSL2. QNX SDP 8.0 at ~/qnx800.
  Load toolchain: `source ~/qnx800/qnxsdp-env.sh`
- Target: QNX 8.0.0 x86_64 in QEMU, started with `./run.sh`.
- SSH to target: `ssh -p 2222 root@localhost` (sshd must be started on target
  with `sh /data/home/root/start_ssh.sh`). Copy files: `scp -P 2222`.
- Target PATH: /proc/boot:/system/bin
- Compile for target: `qcc -Vgcc_ntox86_64 -o out file.c`
- Kernel is instrumented. Capture on target: `tracelogger -w -s <sec> -f /tmp/x.kev`
  ALWAYS use -w: only wide mode includes priority and policy.
- Decode on host: `traceprinter -f x.kev > x.txt`

## Trace facts seen in real fixtures (fixtures/test_wide.txt)
- Event lines: `t:<hex cycles> CPU:<n> <CLASS> :<EVENT> <fields>`
- Convert time: cycles / TRACE_CYCLES_PER_SEC (in the header).
- THREAD states incl. THRUNNING, THREADY, THSEM, THRECEIVE, THSEND_NOTIFY,
  with pid, tid, priority, policy.
- KER_CALL / KER_EXIT events, e.g. SYNC_SEM_WAIT/85.
- PROCESS :PROCCREATE_NAME and :PROCTHREAD_NAME give names (multi-line events).

## Hard rules
- NEVER invent QNX APIs, flags, event names or trace formats. Use only what is
  in /docs or visible in /fixtures. If unsure, stop and ask.
- Headers in ~/qnx800 may be used to confirm that an API, flag or constant
  exists. They prove existence, not behavior: ask about behavior.
- All parsing and rules must be tested against real files in /fixtures.
- Target code: C, built with qcc. Host code: Python 3, minimal dependencies.
- Prefer structural rules (blocking, priority, semaphores) over absolute
  microsecond thresholds: QEMU timing is not realistic.
- Small commits, one feature each, with tests.

## Project status and decisions

### Built
- Parser (`host/kevlint/parser.py`): keeps only what the rule needs.
- Priority-inversion rule (`inversion.py`), also listing waits that resolved
  correctly.
- Self-contained HTML report (`report.py`, `timeline.py`, `patterns.py`) and
  Markdown job summary (`summary.py`).
- Demo workload in `target/` and fixtures `demo_normal`, `demo_inversion`
  (semaphore) and `demo_fixed` (mutex).
- CI (`.github/workflows/ci.yml`): unit tests, then every trace in
  `ci/traces.txt`. Reusable action: `action.yml`.
- Demo PR #1 (`demo/introduce-inversion`) is red on purpose. It must NEVER be
  merged: it would turn `main` red.

### Key decisions
- The holder is inferred, never named by the trace: it is the thread whose post
  or unlock on the same `sync_p`, in the same process, released the waiter.
- Violation: a thread with priority strictly between waiter and holder ran on
  the holder's last CPU while the holder was in THREADY.
- A priority boost only shows on the holder's next THREAD event, and is logged
  before the waiter's block starts. Read the holder's base priority from before
  the waiter's wait call.
- Coverage is always reported: every result states blocks evaluated and not
  evaluated, with reasons. A PASS never means full coverage. The HTML report
  warns if a not-evaluated block outranks every evaluated waiter.
- Tests must not assert that `ci/traces.txt` passes: the demo branch adds a
  failing trace to it.

### Running
```sh
python3 -m unittest discover -s host/tests -t host
PYTHONPATH=host python3 -m kevlint check <trace.txt> --html <report.html>
PYTHONPATH=host python3 -m kevlint check-all ci/traces.txt --reports-dir reports
```
Exit status: 0 no violations, 1 violations, 2 a trace, list or output file could
not be used. Only `python3` exists on this machine.

### Open TODOs
- Clean up the `MsgSend` errors printed at demo shutdown (see `target/kevdemo.h`).
- Condvar coverage: about 38 mutex re-acquires per trace after
  `SYNC_CONDVAR_WAIT/82` are not evaluated, because no wait call matches them.
- Pin the Python version in `action.yml`: it uses the runner's `python3`, and
  kevlint has only been tested on 3.12.
