"""Priority-inversion rule.

A block is a thread in THSEM or THMUTEX. It is a violation when a thread with
priority strictly between the waiter's and the holder's ran during the block,
on the CPU where the holder last ran, while the holder was in THREADY.

The trace never names a holder. It is inferred as the thread whose post or
unlock on the same sync object in the same process released the waiter, so a
block with no visible release cannot be evaluated and is counted separately.
"""
from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import dataclass

RUNNING = "THRUNNING"
READY = "THREADY"
CALLS = {
    "THSEM": ("SYNC_SEM_WAIT/85", "SYNC_SEM_POST/84"),
    "THMUTEX": ("SYNC_MUTEX_LOCK/80", "SYNC_MUTEX_UNLOCK/81"),
}
RELEASE_KIND = {release: kind for kind, (_, release) in CALLS.items()}


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


@dataclass
class Result:
    violations: list
    evaluated: int
    not_evaluated: Counter


@dataclass
class _Block:
    waiter: tuple
    kind: str
    index: int
    start: object
    wait_call: object
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
    open_blocks = {}
    blocks = []
    runs = []
    last_run_on_cpu = {}
    history = defaultdict(list)

    for index, event in enumerate(trace.events):
        if event.cls == "KER_CALL":
            thread = running.get(event.cpu)
            if thread is None:
                continue
            last_call[thread] = event
            kind = RELEASE_KIND.get(event.name)
            if kind is None:
                continue
            sync_p = event.fields.get("sync_p")
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
        if block is not None and event.name != block.kind:
            block.end_index, block.end = index, event
            del open_blocks[thread]
        elif block is None and event.name in CALLS:
            call = last_call.get(thread)
            matches = call is not None and call.name == CALLS[event.name][0]
            block = _Block(thread, event.name, index, event, call if matches else None)
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


def _delays_holder(history, block, run):
    """True if run was on the holder's last CPU while the holder was in THREADY.

    The holder's state comes from its latest THREAD event. The THREADY event
    can be logged just after the preempting THRUNNING, so the holder's next
    event also counts if it falls inside this run.
    """
    entries = history.get(block.holder, [])
    position = bisect_left(entries, (run.index, -1))
    holder_cpu = next(
        (e[3] for e in reversed(entries[:position]) if e[2] == RUNNING), None
    )
    if holder_cpu != run.event.cpu:
        return False
    if position > 0 and entries[position - 1][2] == READY:
        return True
    stop = block.end_index if run.end_index is None else min(run.end_index, block.end_index)
    return (
        position < len(entries)
        and entries[position][0] < stop
        and entries[position][2] == READY
    )


def _qualifying_runs(block, runs, history):
    """Yield (run, holder priority) for each run that makes the block a violation."""
    for run in runs:
        if not block.index < run.index < block.end_index:
            continue
        if run.thread in (block.waiter, block.holder):
            continue
        holder_priority = _holder_priority(history, block, run.index)
        if holder_priority is None:
            continue
        if not holder_priority < run.priority < block.start.priority:
            continue
        if _delays_holder(history, block, run):
            yield run, holder_priority


def _evaluate(block, runs, history, last_cycles):
    """Return the Violation for one evaluable block, or None."""
    waiter_priority = block.start.priority
    # Evidence names the qualifying thread that ran longest during the block.
    time_total = Counter()
    first_run = {}
    lowest_holder = {}

    for run, holder_priority in _qualifying_runs(block, runs, history):
        end = min(run.end_cycles or last_cycles, block.end.cycles)
        time_total[run.thread] += max(end - run.event.cycles, 0)
        first_run.setdefault(run.thread, run)
        lowest_holder.setdefault(run.thread, holder_priority)

    if not first_run:
        return None
    middle = max(first_run, key=lambda t: time_total[t])
    run = first_run[middle]
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
    )


def check(trace):
    blocks, runs, history = _scan(trace)
    last_cycles = trace.events[-1].cycles
    violations = []
    evaluated = 0
    not_evaluated = Counter()

    for block in blocks:
        if block.wait_call is None:
            not_evaluated["no matching wait call in the trace"] += 1
        elif block.end is None:
            not_evaluated["block still open when the trace ended"] += 1
        elif block.release is None:
            not_evaluated["no visible release of the sync object"] += 1
        else:
            evaluated += 1
            violation = _evaluate(block, runs, history, last_cycles)
            if violation is not None:
                violations.append(violation)

    return Result(violations, evaluated, not_evaluated)
