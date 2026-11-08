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
