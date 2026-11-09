# Limitations

The [README](../README.md#honest-limitations) lists the five that matter most. This is the full
list.

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
- On `demo_normal` with the sample config, `priority-inversion` evaluates 0 blocks, because no
  high-priority lock contention occurs in that run: the holder releases the lock before waking
  `fusion_rt`, so `fusion_rt` never blocks on it. Of the 61 blocks, 60 are below
  `min_waiter_priority` and 1 is a kernel-internal wait that is not evaluated. That PASS says
  nothing about inversions, and the output shows it as 0 evaluated.
- A long block with no thread of intermediate priority is not an inversion. Only a budget
  on `max-blocked-time` catches it, and budgets are absolute thresholds.

**What can be wrong**

- Holders are inferred. A shared sync object released from another process is not matched,
  and that block is reported as having no visible release.
- Kernel internals in `procnto` (pid 1) are outside kevlint's scope. A high-priority
  not-evaluated kernel block gets a note instead of a warning.
- AI explanations can be wrong in ways the checks cannot catch. See the
  [safeguards and their limits](reference.md#ai-explanations-and-their-safeguards).

**Practical limits**

- Tracing changes what it measures. See
  [observer effects](architecture.md#observer-effects).
- Captures of the demo can be rejected under load, when tracing finishes after the workload has
  ended. One batch rejected 9 of 12 at `-d 8` and a later one 2 of 36. Why that changed is
  unknown.
- Memory is about 580 bytes per trace event, which matters for long traces.
- Target setup is manual. `kevlintd` does not start on its own after a reboot.
- The `kevlintd` user-event workarounds are based on measured behavior whose cause is unknown.
