"""Turn one thread's THREAD events into state segments across a time window."""
from dataclasses import dataclass

RUNNING = "running"
READY = "ready"
BLOCKED = "blocked"
UNKNOWN = "unknown"

# THCREATE reports that a thread exists, not what state it is in.
NOT_A_STATE = {"THCREATE"}


def category(state):
    """Every state other than running and ready counts as blocked, THINTR included."""
    if state == "THRUNNING":
        return RUNNING
    if state == "THREADY":
        return READY
    return BLOCKED


@dataclass
class Segment:
    state: str
    category: str
    start_cycles: int
    end_cycles: int
    cpu: int
    priority: int
    line: int


def _segment(event, start, end):
    if event is None:
        return Segment("unknown", UNKNOWN, start, end, None, None, None)
    cpu = event.cpu if event.name == "THRUNNING" else None
    return Segment(event.name, category(event.name), start, end, cpu, event.priority, event.line)


def _same(a, b):
    return (
        a is not None
        and a.name == b.name
        and a.priority == b.priority
        and (a.name != "THRUNNING" or a.cpu == b.cpu)
    )


def thread_segments(trace, thread, start_cycles, end_cycles):
    """Return gap-free segments covering [start_cycles, end_cycles].

    The state at the start comes from the thread's last event at or before it.
    A thread with no earlier event starts in an unknown segment.
    """
    changes = [
        e
        for e in trace.events
        if e.cls == "THREAD" and e.thread == thread and e.name not in NOT_A_STATE
    ]
    current = None
    for event in changes:
        if event.cycles > start_cycles:
            break
        current = event

    segments = []
    since = start_cycles
    for event in changes:
        if not start_cycles < event.cycles < end_cycles or _same(current, event):
            continue
        segments.append(_segment(current, since, event.cycles))
        current, since = event, event.cycles
    segments.append(_segment(current, since, end_cycles))
    return segments
