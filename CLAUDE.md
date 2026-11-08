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
- Optional `--explain` on `check` and `check-all` (`explain.py`, `llm.py`):
  AI-generated explanations in the terminal and HTML report only, not in the
  Markdown summary or CI. `--debug` logs a retry without the seed to stderr.
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

### `--explain` design
- Never affects the verdict: explanations run after it is printed, and any
  failure becomes a note. Without the three `KEVLINT_LLM_*` vars it prints one
  note and skips. Output without the flag is unchanged.
- One LLM call per violation pattern, sending only the worst occurrence: the raw
  lines (with continuation lines) of the wait call, block start, holder's last
  run, holder THREADY, middle became ready, middle running, release and block
  end, plus structured fields. No file path or trace header.
- The LLM writes exactly two JSON claims: a summary, and a cause that must say
  "preempted". kevlint writes the "where to look" line itself from the fields,
  citing the `sync_p` lines, and labels it as not AI, because the LLM wrongly
  said all three threads synchronize on the semaphore.
- The holder is always called inferred, and model text is never trusted as
  markup: it is HTML-escaped and stripped of control characters.
- A rejected explanation is dropped whole, with the reason shown:
  - Citation: a claim with no lines, or any line (in `lines` or the prose) whose
    text was not sent.
  - Value: a hex value, priority or duration not in that claim's cited lines or
    the fields. Durations may be rounded.
  - Structure: not exactly two claims, or no "preempted" in the cause.
- Transport is `urllib` only: temperature 0, seed 42, retried once without the
  seed on HTTP 400. Three attempts on timeouts, 429 and 5xx, honoring
  `Retry-After` up to 5 s. After retries run out, later patterns are skipped.
- Key safety: redirects are not followed, plain http is refused except on
  localhost, and errors never include URLs or response bodies.
- Tests never call the real API: they use `host/tests/fake_llm.py`.

### `--explain` limits
- A citation check proves a line was sent, not that it supports the claim. The
  value check only sees values in recognizable forms, and neither catches wrong
  reasoning, so explanations stay labeled "claims were not verified".
- Accuracy was judged by hand on one live response, on `demo_inversion` only.
- The endpoint accepted the seed, which does not guarantee identical output.

### `--explain` plan B (not built)
If explanations prove wrong on real traces, do not iterate on the prompt. kevlint
writes the causal chain deterministically, in order, with citations (middle
became ready, preempted the holder, waiter stayed blocked until the post). The
LLM only rewrites that verified chain as one plain-English paragraph for
non-experts, using only facts from the chain, under the "AI-generated" label.

### Running
```sh
python3 -m unittest discover -s host/tests -t host
PYTHONPATH=host python3 -m kevlint check <trace.txt> --html <report.html>
PYTHONPATH=host python3 -m kevlint check-all ci/traces.txt --reports-dir reports
. ~/.kevlint_env; PYTHONPATH=host python3 -m kevlint check <trace.txt> --explain
```
Exit status: 0 no violations, 1 violations, 2 a trace, list or output file could
not be used. Only `python3` exists on this machine. The LLM vars load only in
interactive shells, so other shells must source `~/.kevlint_env` first. Never
print the key.

### Open TODOs
- Clean up the `MsgSend` errors printed at demo shutdown (see `target/kevdemo.h`).
- Condvar coverage: about 38 mutex re-acquires per trace after
  `SYNC_CONDVAR_WAIT/82` are not evaluated, because no wait call matches them.
- Pin the Python version in `action.yml`: it uses the runner's `python3`, and
  kevlint has only been tested on 3.12.
