"""Find the kevlintd markers around a block.

A marker is a note from the workload, logged when kevlintd handled the write.
It gives context for a block and never affects a rule's verdict.
"""

CONTEXT_NOTE = "context written by the workload, not evidence of cause"
# A marker this close to the block, as a fraction of its length, is shown at its edge.
EDGE_MARGIN = 0.25


def before(trace, cycles):
    """The last marker at or before cycles, or None."""
    return max((m for m in trace.markers if m.cycles <= cycles), key=lambda m: m.cycles, default=None)


def after(trace, cycles):
    """The first marker at or after cycles, or None."""
    return min((m for m in trace.markers if m.cycles >= cycles), key=lambda m: m.cycles, default=None)


def between(trace, start, end):
    """Markers strictly inside the block."""
    return [m for m in trace.markers if start < m.cycles < end]


def within(trace, start, end):
    """Markers in a view, edges included."""
    return [m for m in trace.markers if start <= m.cycles <= end]


def around(trace, start, end):
    """Return (earlier, inside, later) for a block's timeline.

    earlier and later hold at most the one nearest marker just outside the block.
    A marker exactly on an edge counts as inside.
    """
    limit = (end - start) * EDGE_MARGIN
    earlier = [m for m in trace.markers if start - limit <= m.cycles < start]
    later = [m for m in trace.markers if end < m.cycles <= end + limit]
    return earlier[-1:], within(trace, start, end), later[:1]
