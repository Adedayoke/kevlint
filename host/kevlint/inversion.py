"""Priority-inversion rule.

A block is a thread in THSEM or THMUTEX. Other lock states are only counted as
not evaluated. A block is a violation when a thread with
priority strictly between the waiter's and the holder's ran during the block,
on the CPU where the holder last ran, while the holder was in THREADY.

The trace never names a holder. It is inferred as the thread whose post or
unlock on the same sync object in the same process released the waiter, so a
block with no visible release cannot be evaluated and is counted separately.
The release can be logged just before the block: with the holder on another CPU,
its unlock can land between the waiter's lock call and the waiter's THMUTEX event.
"""
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .timeline import NOT_A_STATE

HOLDER_NOTE = "inferred, not named by the trace"
BELOW_MINIMUM = "waiter below the configured minimum priority"
TYPICAL_FIXES = {
    "THSEM": (
        "Protect this resource with a mutex (QNX mutexes use priority inheritance) "
        "instead of a semaphore."
    ),
    "THMUTEX": "Check that this mutex is not configured without priority inheritance.",
}

RUNNING = "THRUNNING"
READY = "THREADY"
CALLS = {
    "THSEM": ("SYNC_SEM_WAIT/85", "SYNC_SEM_POST/84"),
    "THMUTEX": ("SYNC_MUTEX_LOCK/80", "SYNC_MUTEX_UNLOCK/81"),
}
RELEASE_KIND = {release: kind for kind, (_, release) in CALLS.items()}
# Lock states seen in real traces whose wait and release are not modeled here. They
# are counted as not evaluated, so that a PASS does not hide them.
UNCHECKED_LOCK_STATES = ("THRWLOCK_WRITE", "THRWLOCK_READ", "THMUON_MUTEX")


def unchecked_lock_reason(state):
    return f"blocked in {state}, which this rule does not check"


@dataclass
class Violation:
    kind: str
    sync_p: str
    waiter: tuple
    waiter_priority: int
    holder: tuple
    holder_priority: int
    release_call: str
    middle: tuple
    middle_priority: int
    start_cycles: int
    end_cycles: int
    wait_call_line: int
    block_start_line: int
    release_line: int
    middle_line: int
    block_end_line: int
    middle_cycles: int
    release_cycles: int
    holder_run_line: int
    holder_ready_line: int
    middle_ready_line: int = None

    @property
    def duration_cycles(self):
        return self.end_cycles - self.start_cycles


@dataclass
class ResolvedWait:
    """An evaluated block with no violation, where the waiter outranked the holder."""

    kind: str
    sync_p: str
    waiter: tuple
    waiter_priority: int
    holder: tuple
    holder_base_priority: int
    holder_peak_priority: int
    release_call: str
    middle: tuple
    middle_priority: int
    middle_ran: bool
    start_cycles: int
    end_cycles: int
    wait_call_line: int
    block_start_line: int
    release_line: int
    middle_line: int
    block_end_line: int
    middle_cycles: int
    release_cycles: int

    @property
    def duration_cycles(self):
        return self.end_cycles - self.start_cycles


def boost_text(resolved, arrow="→"):
    """Describe what happened to the holder's priority during the wait."""
    base, peak = resolved.holder_base_priority, resolved.holder_peak_priority
    if peak <= base:
        return f"stayed at priority {base} (no boost seen)"
    text = f"boosted {base} {arrow} {peak}"
    return f"{text} (priority inheritance)" if resolved.kind == "THMUTEX" else text


@dataclass
class Result:
    violations: list
    evaluated: int
    not_evaluated: Counter
    evaluated_max_priority: int = None
    not_evaluated_max_priority: int = None
    resolved: list = field(default_factory=list)
    excluded: int = 0

    @property
    def unevaluated_outranks_evaluated(self):
        """True if a block the rule could not check outranks every waiter it did."""
        skipped, checked = self.not_evaluated_max_priority, self.evaluated_max_priority
        return skipped is not None and (checked is None or skipped > checked)


@dataclass
class _Block:
    waiter: tuple
    kind: str
    index: int
    start: object
    wait_call: object
    wait_index: int = None
    end_index: int = None
    end: object = None
    holder: tuple = None
    release: object = None


@dataclass
class _Run:
    index: int
    event: object
    thread: tuple
    priority: int
    end_cycles: int = None
    end_index: int = None


def _scan(trace):
    """Collect blocks, runs and each thread's priority history in one pass."""
    running = {}
    last_call = {}
    last_call_index = {}
    open_blocks = {}
    blocks = []
    runs = []
    last_run_on_cpu = {}
    history = defaultdict(list)
    latest_release = {}

    for index, event in enumerate(trace.events):
        if event.cls == "KER_CALL":
            thread = running.get(event.cpu)
            if thread is None:
                continue
            last_call[thread] = event
            last_call_index[thread] = index
            kind = RELEASE_KIND.get(event.name)
            if kind is None:
                continue
            sync_p = event.fields.get("sync_p")
            latest_release[(thread[0], kind, sync_p)] = (thread, event, index)
            for block in open_blocks.values():
                if (
                    block.kind == kind
                    and block.wait_call is not None
                    and block.wait_call.fields.get("sync_p") == sync_p
                    and block.waiter[0] == thread[0]
                ):
                    block.holder, block.release = thread, event
            continue

        thread = event.thread
        if event.priority is not None:
            history[thread].append((index, event.priority, event.name, event.cpu))
        if event.name == RUNNING:
            running[event.cpu] = thread
            previous = last_run_on_cpu.get(event.cpu)
            if previous is not None:
                previous.end_cycles = event.cycles
                previous.end_index = index
            run = _Run(index, event, thread, event.priority)
            runs.append(run)
            last_run_on_cpu[event.cpu] = run

        block = open_blocks.get(thread)
        if event.name in NOT_A_STATE:
            continue
        if block is not None and event.name != block.kind:
            block.end_index, block.end = index, event
            del open_blocks[thread]
        elif block is None and (event.name in CALLS or event.name in UNCHECKED_LOCK_STATES):
            call = last_call.get(thread)
            matches = event.name in CALLS and call is not None and call.name == CALLS[event.name][0]
            block = _Block(
                thread,
                event.name,
                index,
                event,
                call if matches else None,
                last_call_index.get(thread) if matches else None,
            )
            if matches:
                released = latest_release.get((thread[0], event.name, call.fields.get("sync_p")))
                # Only a release after this wait call can be the one that freed it.
                if released is not None and released[2] > block.wait_index and released[0] != thread:
                    block.holder, block.release = released[0], released[1]
            open_blocks[thread] = block
            blocks.append(block)

    return blocks, runs, history


def _holder_priority(history, block, index):
    """The holder's priority at index.

    A priority boost is only visible on the holder's next THREAD event, so
    that event wins if it falls inside the block.
    """
    entries = history.get(block.holder, [])
    position = bisect_left(entries, (index, -1))
    if position < len(entries) and entries[position][0] < block.end_index:
        return entries[position][1]
    return entries[position - 1][1] if position > 0 else None


def _holder_delay(history, block, run):
    """Return (holder's last RUNNING index, its THREADY index) if run delayed it.

    Returns None unless run was on the holder's last CPU while the holder was
    in THREADY. The holder's state comes from its latest THREAD event. The
    THREADY event can be logged just after the preempting THRUNNING, so the
    holder's next event also counts if it falls inside this run.
    """
    entries = history.get(block.holder, [])
    position = bisect_left(entries, (run.index, -1))
    holder_run = next((e for e in reversed(entries[:position]) if e[2] == RUNNING), None)
    if holder_run is None or holder_run[3] != run.event.cpu:
        return None
    if position > 0 and entries[position - 1][2] == READY:
        return holder_run[0], entries[position - 1][0]
    stop = block.end_index if run.end_index is None else min(run.end_index, block.end_index)
    if position < len(entries) and entries[position][0] < stop and entries[position][2] == READY:
        return holder_run[0], entries[position][0]
    return None


def _ready_before(history, thread, run_index):
    """The THREADY event that led directly to this run, or None if not in the trace."""
    entries = history.get(thread, [])
    position = bisect_left(entries, (run_index, -1))
    if position > 0 and entries[position - 1][2] == READY:
        return entries[position - 1][0]
    return None


def _qualifying_runs(block, runs, run_indices, history):
    """Yield (run, holder priority) for each run that makes the block a violation."""
    first = bisect_right(run_indices, block.index)
    last = bisect_left(run_indices, block.end_index)
    for run in runs[first:last]:
        if run.thread in (block.waiter, block.holder):
            continue
        holder_priority = _holder_priority(history, block, run.index)
        if holder_priority is None:
            continue
        if not holder_priority < run.priority < block.start.priority:
            continue
        if _holder_delay(history, block, run) is not None:
            yield run, holder_priority


def _evaluate(block, runs, run_indices, history, trace):
    """Return the Violation for one evaluable block, or None."""
    waiter_priority = block.start.priority
    last_cycles = trace.events[-1].cycles
    # Evidence names the qualifying thread that ran longest during the block.
    time_total = Counter()
    first_run = {}
    lowest_holder = {}

    for run, holder_priority in _qualifying_runs(block, runs, run_indices, history):
        end = min(run.end_cycles or last_cycles, block.end.cycles)
        time_total[run.thread] += max(end - run.event.cycles, 0)
        first_run.setdefault(run.thread, run)
        lowest_holder.setdefault(run.thread, holder_priority)

    if not first_run:
        return None
    middle = max(first_run, key=lambda t: time_total[t])
    run = first_run[middle]
    holder_run, holder_ready = _holder_delay(history, block, run)
    middle_ready = _ready_before(history, middle, run.index)
    return Violation(
        kind=block.kind,
        sync_p=block.wait_call.fields["sync_p"],
        waiter=block.waiter,
        waiter_priority=waiter_priority,
        holder=block.holder,
        holder_priority=lowest_holder[middle],
        release_call=block.release.name,
        middle=middle,
        middle_priority=run.priority,
        start_cycles=block.start.cycles,
        end_cycles=block.end.cycles,
        wait_call_line=block.wait_call.line,
        block_start_line=block.start.line,
        release_line=block.release.line,
        middle_line=run.event.line,
        block_end_line=block.end.line,
        middle_cycles=run.event.cycles,
        release_cycles=block.release.cycles,
        holder_run_line=trace.events[holder_run].line,
        holder_ready_line=trace.events[holder_ready].line,
        middle_ready_line=None if middle_ready is None else trace.events[middle_ready].line,
    )


def _would_be_middle(block, history, trace, base):
    """Find the thread that spent longest in THREADY, outranking the holder's base.

    Only threads in the waiter-to-base priority window whose last CPU is the
    holder's count. Returns (thread, first THREADY entry, ran after that) or None.
    """
    best = None
    best_ready = 0
    for thread, entries in history.items():
        if thread in (block.waiter, block.holder):
            continue
        start = bisect_left(entries, (block.index, -1))
        ready = 0
        first = None
        for position in range(max(start - 1, 0), len(entries)):
            index, priority, state, _ = entries[position]
            if index >= block.end_index:
                break
            if state != READY or not base < priority < block.start.priority:
                continue
            begin = max(trace.events[index].cycles, block.start.cycles)
            end = block.end.cycles
            if position + 1 < len(entries):
                end = min(trace.events[entries[position + 1][0]].cycles, end)
            if end > begin:
                ready += end - begin
                first = first or entries[position]
        if first is None or ready <= best_ready:
            continue
        earlier = bisect_left(entries, (first[0], -1))
        cpu = next((e[3] for e in reversed(entries[:earlier]) if e[2] == RUNNING), first[3])
        if cpu != block.release.cpu:
            continue
        ran = any(e[2] == RUNNING and first[0] < e[0] < block.end_index for e in entries)
        best, best_ready = (thread, first, ran), ready
    return best


def _resolved_wait(block, history, trace):
    """Describe a non-violating block whose waiter outranked the holder, else None.

    The holder's base is its priority before the waiter's wait call, because
    a boost is logged after that call but before the waiter's block starts.
    """
    entries = history.get(block.holder, [])
    position = bisect_left(entries, (block.wait_index, -1))
    if position == 0 or block.start.priority is None:
        return None
    base = entries[position - 1][1]
    if base >= block.start.priority:
        return None
    peak = max([e[1] for e in entries[position:] if e[0] < block.end_index], default=base)
    peak = max(peak, base)

    middle, middle_priority, ran, middle_line, middle_cycles = None, None, False, None, None
    found = _would_be_middle(block, history, trace, base)
    if found is not None:
        middle, first, ran = found
        middle_priority = first[1]
        event = trace.events[first[0]]
        middle_line = event.line
        middle_cycles = max(event.cycles, block.start.cycles)

    return ResolvedWait(
        kind=block.kind,
        sync_p=block.wait_call.fields["sync_p"],
        waiter=block.waiter,
        waiter_priority=block.start.priority,
        holder=block.holder,
        holder_base_priority=base,
        holder_peak_priority=peak,
        release_call=block.release.name,
        middle=middle,
        middle_priority=middle_priority,
        middle_ran=ran,
        start_cycles=block.start.cycles,
        end_cycles=block.end.cycles,
        wait_call_line=block.wait_call.line,
        block_start_line=block.start.line,
        release_line=block.release.line,
        middle_line=middle_line,
        block_end_line=block.end.line,
        middle_cycles=middle_cycles,
        release_cycles=block.release.cycles,
    )


def _higher(current, priority):
    if priority is None:
        return current
    return priority if current is None else max(current, priority)


def check(trace, min_waiter_priority=None):
    """Run the rule. Waiters below min_waiter_priority are counted as excluded."""
    blocks, runs, history = _scan(trace)
    run_indices = [run.index for run in runs]
    violations = []
    evaluated = 0
    not_evaluated = Counter()
    excluded = 0
    evaluated_max = None
    not_evaluated_max = None
    resolved = []

    for block in blocks:
        priority = block.start.priority
        if min_waiter_priority is not None and priority is not None and priority < min_waiter_priority:
            # Deliberately out of scope, so it never counts as a coverage gap.
            excluded += 1
            continue

        reason = None
        if block.kind in UNCHECKED_LOCK_STATES:
            reason = unchecked_lock_reason(block.kind)
        elif block.wait_call is None:
            reason = "no matching wait call in the trace"
        elif block.end is None:
            reason = "block still open when the trace ended"
        elif block.release is None:
            reason = "no visible release of the sync object"

        if reason is not None:
            not_evaluated[reason] += 1
            not_evaluated_max = _higher(not_evaluated_max, block.start.priority)
            continue
        evaluated += 1
        evaluated_max = _higher(evaluated_max, block.start.priority)
        violation = _evaluate(block, runs, run_indices, history, trace)
        if violation is not None:
            violations.append(violation)
            continue
        wait = _resolved_wait(block, history, trace)
        if wait is not None:
            resolved.append(wait)

    return Result(
        violations, evaluated, not_evaluated, evaluated_max, not_evaluated_max, resolved, excluded
    )
