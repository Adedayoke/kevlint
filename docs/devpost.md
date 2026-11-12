# kevlint: Devpost submission draft

> Draft text for the Devpost form. Every number below comes from this repository; the source
> is linked where it matters.

**Tagline:** CI checks for QNX 8.0 kernel traces: catch priority inversion before it ships.

**Links:** [Repository](https://github.com/Adedayoke/kevlint) ·
[Tutorial](https://github.com/Adedayoke/kevlint/blob/main/docs/tutorial-qnx-wsl.md) ·
[Tutorial on dev.to](https://dev.to/adedayoke/run-qnx-80-on-windows-with-wsl2-and-capture-your-first-kernel-trace-5ekg) ·
[Red-on-purpose demo PR](https://github.com/Adedayoke/kevlint/pull/1)

## Inspiration

In 1997 the Mars Pathfinder lander kept resetting itself on Mars. A low-priority task held a
mutex that a high-priority task needed, while medium-priority tasks kept running: a classic
priority inversion. The fix was to turn on priority inheritance for that mutex. The team had seen
the problem before landing but could not get it to repeat.

I came to this hackathon with no QNX experience, a Windows laptop with 8 GB of RAM and a metered
internet connection, and no hardware budget. Everything here runs in QEMU on that laptop.

That is what makes these bugs dangerous. The system still works, just late, and only when the
timing lines up, so normal testing tends to miss them. Commercial timing tools exist and list QNX
support, but they are enterprise products. I wanted something small, open source and
free that any QNX project could run on every pull request.

**A hypothetical example (not a real company or incident).** Picture a QNX-based automatic
emergency braking module with three threads: `brake_control` at priority 50, `diagnostics_upload`
at 30 and `event_logger` at 10. A developer adds logging from `brake_control` into a buffer that
`event_logger` also uses, guarded by a semaphore. On a rare timing alignment, `event_logger` holds
the semaphore when the upload preempts it, so `brake_control` waits behind the diagnostics upload,
a delay that could be significant for braking. Normal testing almost never lines up that way.
With kevlint checking a trace from a hardware test rig in CI, the pull request fails:
`no-semaphore-high-priority` flags any run where `brake_control` blocks on the semaphore, even when
the timing does not line up, and when it does, `priority-inversion` names all three threads with
the exact trace lines and a `max-blocked-time` budget for `brake_control` reports the overrun.
The typical fix is a priority-inheritance mutex in place of the semaphore, the same change as in
kevlint's own before-and-after demo.

## What it does

kevlint turns QNX 8.0 kernel traces into pass/fail CI checks.

- **Record and decode.** `kevlint capture` runs a workload on the target over SSH, records with
  `tracelogger -w`, copies the trace back, decodes it with `traceprinter`, and checks it.
- **Check.** Three structural rules:
  - `priority-inversion`: a high-priority thread waits on a lock while a thread of intermediate
    priority runs in place of the lock's holder.
  - `max-blocked-time`: a named thread is blocked longer than its budget.
  - `no-semaphore-high-priority`: a thread at or above a set priority waits on a semaphore.
- **Fail the build.** Any finding exits 1, so CI goes red. It ships as a reusable GitHub Action.
- **Show its evidence.** Every finding cites the exact trace lines behind it, in the terminal, a
  self-contained HTML report with a timeline, and a Markdown job summary.
- **Say what it did not check.** Every result, including a PASS, states how many blocks were
  evaluated and how many were not, with reasons.
- **Explain, optionally.** `--explain` asks an LLM to explain each inversion in plain English,
  under strict checks (below). It can never change the verdict.

**Before and after.** I wrote a demo workload where a priority 10 thread holds a lock that a
priority 50 thread needs, while a priority 30 thread hogs the CPU. The only difference between
the two runs is the lock:

| With all three rules | Before: semaphore | After: priority-inheritance mutex |
|---|---|---|
| Result | FAIL, 31 findings | PASS, 0 findings |
| Worst wait of the priority 50 thread | 249.1 ms | 24.3 ms |
| Exit status | 1 | 0 |

In the "after" trace, kevlint shows the holder boosted from priority 10 to 50. These are QEMU
timings, so only the contrast between them means anything.

## How I built it

- **Target (C, built with `qcc`).** A three-process demo pipeline (`sensor`, `fusion`, `control`)
  that talks through QNX message passing, and `kevlintd`, a QNX resource manager. Any program can
  write to `/dev/kevlint/marker` to put a labelled point into the trace.
- **Keeping the tracer out of the verdict.** A marker write is a blocking QNX message, so it
  changes scheduling. `kevlintd`'s channel keeps QNX's default priority inheritance, so it serves
  each marker at the writer's priority. With `_NTO_CHF_FIXED_PRIORITY`, a marker from a
  high-priority thread could wait behind lower-priority work and cause the very inversion kevlint
  looks for. The channel also sets `_NTO_CHF_INHERIT_RUNMASK` to run on the writer's CPU, but in
  my only placement test a default channel did the same, so that flag's benefit is unproven. The
  demo writes its markers outside the waiter's block. In 8 live captures with markers (5 of the
  inversion demo, 3 of the fixed one), `kevlintd` ran in every trace but appeared in none of the
  151 findings. The demo's own `fusion_hog` was the intermediate thread in all 49 inversion
  violations, and all 3 fixed runs passed
  ([results](https://github.com/Adedayoke/kevlint/blob/main/docs/validation.md#markers-do-not-cause-findings)).
- **Host (Python 3, standard library only).** A parser for `traceprinter` output, the three rules,
  the reports, the capture command, and the LLM client, which uses plain `urllib`.
- **Holder inference.** The trace never names who holds a lock, so kevlint infers it: the thread
  whose post or unlock on the same sync object, in the same process, released the waiter.
- **Tests on real data.** 414 unit tests run against real traces recorded on QNX 8.0 and committed
  in `fixtures/`. Fakes stand in for the LLM and the target, so tests never call either.
- **CI.** GitHub Actions runs the tests and checks the listed traces on every PR. A separate job
  proves the action passes a clean trace and fails one with a violation.
- **Environment.** QNX 8.0.0 x86_64 in QEMU, on Ubuntu 24.04 in WSL2 on a Windows laptop.

## Challenges I ran into

- **Getting QNX running under WSL2.** Bridge networking gave the guest only a `169.254` address,
  so I switched to QEMU user networking with a port forward. The stock `sshd` crashed with
  `Memory fault` at every boot because of PAM, so I run my own `sshd` with PAM off. Even then it
  needed `sshd-auth` added to the image, which I confirmed by building fresh images with and
  without it. These fixes became a
  [step-by-step tutorial](https://github.com/Adedayoke/kevlint/blob/main/docs/tutorial-qnx-wsl.md).
- **Event order in real traces.** A holder's unlock on another CPU can be logged before the
  waiter's block event. The trace starts with a dump of every thread's state, which once split
  one block into two. A priority boost only shows on the holder's next event. Each of these was
  a bug I found on real traces and fixed.
- **A quiet trace quirk.** User string events lose their text when the string's length is a
  multiple of 8, and are cut at 255 bytes, while `TraceEvent` still returns 0. I measured it
  across every length from 1 to 140 and many longer ones, and documented it in
  [docs/qnx-trace-quirks.md](https://github.com/Adedayoke/kevlint/blob/main/docs/qnx-trace-quirks.md).
  I don't know whether the kernel or `traceprinter` causes it. `kevlintd` pads around it, and all
  128 marker lengths decode exactly in a test capture.
- **False PASSes.** A capture with a nearly full disk once printed PASS on a truncated trace.
  Now every capture failure exits 2, and a trace that covers under 90% of the requested time is
  an error.

## Accomplishments that I'm proud of

- **No structural findings on real system activity.** I ran kevlint on six traces with no demo
  workload: idle, a busy filesystem, network traffic, a mix of these, and two 30-second mixed
  captures. Nothing crashed. `priority-inversion` found no violation in 3,630 evaluated blocks,
  and `no-semaphore-high-priority` flagged none of 296 semaphore waits. The caveats matter:
  19,292 blocks were not evaluated, mostly read-write lock waits the rules don't model; it all
  ran under QEMU; and only two of the six traces are committed. The budget rule is different:
  100 ms budgets I chose for the test gave 26 findings, and 13 of them were false positives on an
  idle worker thread.
- **Honest coverage.** A PASS always says how much it actually checked.
- **AI that can't move the verdict.** The design is described in the next section.
- **A tutorial others can follow.** Its key steps were replayed on freshly built images.

## What I learned

- **Don't trust an LLM with the facts.** In an early version, the model wrongly said all three
  threads shared the semaphore. So kevlint now writes the "where to look" line itself, and the
  model only explains. The rest of the design follows from that:
  - The verdict is printed before any LLM call, and any failure becomes a note.
  - Only the worst occurrence's trace lines are sent.
  - The model must return exactly two claims. Each must cite lines that were actually sent, and
    every hex value, priority or duration must appear in those lines. One failure drops the
    whole explanation.
  - Output is HTML-escaped, and CI never uses `--explain`.
  - These checks prove that a line was sent, not that it supports the claim, so explanations stay
    labelled "claims were not verified". I have judged accuracy by hand on only one live response.
- **Structure beats thresholds under emulation.** QEMU timing is not realistic, so rules based on
  priorities and sync objects say more than microsecond budgets.
- **QNX's trace is detailed, but you have to read it carefully:** the order of events across
  CPUs, the state dump at the start, and when a priority boost becomes visible.

## What's next

- Evaluate mutex re-acquires after a condvar wait. There are about 38 per trace, and today they
  are not evaluated.
- Model read-write locks and `THMUON_MUTEX`, the biggest gap in coverage, once their behaviour is
  confirmed from QNX documentation.
- Run on real hardware. Everything so far ran under QEMU.
- Cut memory use: about 580 bytes per trace event today, 0.9 GB for a 1.5-million-event trace.

## Built with

`qnx` · `qnx-sdp-8.0` · `qcc` · `tracelogger` · `traceprinter` · `c` · `python` · `qemu` ·
`wsl2` · `ubuntu` · `github-actions` · `openai-compatible-llm-api` · `claude-code`

## Evidence of QNX use

The README section
[Evidence of QNX use](https://github.com/Adedayoke/kevlint#evidence-of-qnx-use) lists every QNX
API by file and function. In summary:

- **Target code in C, built with `qcc -Vgcc_ntox86_64`** (`target/Makefile`):
  - `target/kevlintd.c`: a resource manager (`resmgr_attach`, `dispatch_create`,
    `ChannelCreate`, `iofunc_*`) that writes trace events with `TraceEvent`.
  - `target/fusion.c`, `target/control.c`, `target/sensor.c` and `target/kevdemo.h`: the demo
    pipeline, using `name_attach`, `MsgSend`, `MsgReceive`, `MsgReply`, pulses and
    `ThreadCtl` run masks.
- **Host code that reads QNX's trace format:**
  - `host/kevlint/parser.py`: parses `traceprinter` output.
  - `host/kevlint/inversion.py`: works from kernel calls such as `SYNC_SEM_WAIT/85` and
    `SYNC_MUTEX_UNLOCK/81`, and from thread states.
  - `host/kevlint/capture.py`: runs `tracelogger -w` on the target and `traceprinter` from the
    QNX SDP.
- **Real QNX artifacts:**
  - `fixtures/*.kev`: binary kernel traces recorded on QNX 8.0.0 x86_64, each with its decoded
    `.txt`.
  - `docs/qnx-trace-quirks.md`: measured behaviour of user string trace events.
