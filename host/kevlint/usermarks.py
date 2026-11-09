"""Find the kevlintd markers around a block.

A marker is a note from the workload, logged when kevlintd handled the write.
It gives context for a block and never affects a rule's verdict.
"""

CONTEXT_NOTE = "context written by the workload, not evidence of cause"
# A marker this close to the block, as a fraction of its length, is drawn with it.
VIEW_MARGIN = 0.25


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


def view_window(trace, start, end):
    """The block's cycle window, widened to show the nearest markers just outside it."""
    limit = (end - start) * VIEW_MARGIN
    earlier, later = before(trace, start), after(trace, end)
    view_start = earlier.cycles if earlier and start - earlier.cycles <= limit else start
    view_end = later.cycles if later and later.cycles - end <= limit else end
    return view_start, view_end
