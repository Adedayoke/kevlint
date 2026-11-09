# Validation

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

## Results on the committed fixtures

Reproduce these with `check <trace> --config kevlint.toml`. Coverage is shown as evaluated / not
evaluated / excluded by config. All were recorded on QNX 8.0.0 x86_64 under QEMU.

| Fixture | Exit | `priority-inversion` | `max-blocked-time` | `no-semaphore-high-priority` |
|---|---|---|---|---|
| `demo_normal` | 0 | PASS, 0 / 1 / 60 | PASS, 0 / 0 | PASS, 22 / 0 |
| `demo_inversion` | 1 | FAIL, 10 findings, 10 / 1 / 60 | FAIL, 10 findings, 10 / 1 | FAIL, 11 findings, 32 / 0 |
| `demo_fixed` | 0 | PASS, 11 / 0 / 61 | PASS, 11 / 0 | PASS, 21 / 0 |
| `demo_markers_inversion` | 1 | FAIL, 10 findings, 10 / 0 / 65 | FAIL, 10 findings, 10 / 0 | FAIL, 10 findings, 33 / 0 |
| `demo_markers_fixed` | 0 | PASS, 10 / 2 / 63 | PASS, 10 / 0 | PASS, 22 / 0 |

The worst `fusion_rt` block is 249.1 ms in `demo_inversion`, 24.3 ms in `demo_fixed`, 223.4 ms in
`demo_markers_inversion` and 26.6 ms in `demo_markers_fixed`. These are QEMU timings.

`system_idle` and `system_busy` contain no demo workload. With `kevlint.toml` they exit 2 on
purpose, because the sample requires `fusion_rt`. Without a config they exit 0:
`system_idle` evaluates 9 blocks and leaves 83 not evaluated, and `system_busy` evaluates 50 and
leaves 299 not evaluated.
