"""Parse the traceprinter output that the priority-inversion rule needs.

Only THREAD events, KER_CALL events, thread names and kevlintd markers are kept.
The rule needs every KER_CALL, not just the sync calls, to tell what a thread
last asked for.
"""
import re
from dataclasses import dataclass, field

EVENT_RE = re.compile(r"^t:0x([0-9a-fA-F]+) CPU:(\d+) (\S+)\s+:(\S+)\s*(.*)$")
FIELD_RE = re.compile(r"([^\s:]+):(\S+)")
HEADER_RE = re.compile(r"^\s*([A-Z_]+):: (.*)$")

KEPT_CLASSES = {"THREAD", "KER_CALL"}
NAME_EVENT = "PROCTHREAD_NAME"

MARKER_CLASS = "USREVENT"
MARKER_EVENT = "EVENT:100"  # KEVLINT_MARKER_EVENT in target/kevlintd.h.
# traceprinter does not escape quotes, so the text runs to the last '", d0:'.
MARKER_RE = re.compile(r'^STR:"pid:(\d+) tid:(\d+) (.*)", d0:')
UNPRINTABLE_RE = re.compile(r"[^\x20-\x7e]")


class TraceError(Exception):
    """The file is not a trace this tool can check."""


@dataclass
class Event:
    line: int
    cycles: int
    cpu: int
    cls: str
    name: str
    fields: dict = field(default_factory=dict)

    @property
    def thread(self):
        return (int(self.fields["pid"]), int(self.fields["tid"]))

    @property
    def priority(self):
        value = self.fields.get("priority")
        return None if value is None else int(value)


@dataclass
class Marker:
    """A note written to /dev/kevlint/marker. Kept apart from events, which have threads."""

    line: int
    cycles: int
    cpu: int
    thread: tuple
    text: str


@dataclass
class Trace:
    cycles_per_sec: int
    first_cycles: int
    events: list
    thread_names: dict
    header: dict = field(default_factory=dict)
    markers: list = field(default_factory=list)
    undecoded_markers: int = 0

    def seconds(self, cycles):
        """Seconds since the first event in the trace."""
        return (cycles - self.first_cycles) / self.cycles_per_sec

    def describe(self, thread):
        name = self.thread_names.get(thread, "unnamed")
        return f"{name} (pid {thread[0]} tid {thread[1]})"


def parse_marker(line, cycles, cpu, rest):
    """Return a Marker, or None if the event has no readable marker text.

    kevlintd pads with one space, and never writes a trailing space of its own.
    The text comes from the workload, so anything unprintable is replaced.
    """
    match = MARKER_RE.match(rest)
    if match is None:
        return None
    text = UNPRINTABLE_RE.sub("?", match[3].rstrip(" "))
    return Marker(line, cycles, cpu, (int(match[1]), int(match[2])), text)


def parse_trace(path):
    header = {}
    events = []
    markers = []
    undecoded_markers = 0
    first_cycles = None
    current = None

    with open(path, errors="replace") as lines:
        for number, raw in enumerate(lines, 1):
            text = raw.rstrip("\n")
            head = EVENT_RE.match(text)
            if head:
                cycles = int(head[1], 16)
                if first_cycles is None:
                    first_cycles = cycles
                current = None
                if head[3] == MARKER_CLASS and head[4] == MARKER_EVENT:
                    marker = parse_marker(number, cycles, int(head[2]), head[5])
                    if marker is None:
                        undecoded_markers += 1
                    else:
                        markers.append(marker)
                    continue
                if head[3] in KEPT_CLASSES or head[4] == NAME_EVENT:
                    current = Event(number, cycles, int(head[2]), head[3], head[4])
                    current.fields.update(FIELD_RE.findall(head[5]))
                    events.append(current)
                continue
            if first_cycles is None:
                match = HEADER_RE.match(text)
                if match:
                    header[match[1]] = match[2].strip()
                continue
            if current is not None and ":" in text:
                key, _, value = text.strip().partition(":")
                current.fields.setdefault(key, value.strip())

    if "TRACE_CYCLES_PER_SEC" not in header or first_cycles is None:
        raise TraceError(f"{path}: not traceprinter output")

    names = {e.thread: e.fields.get("name", "") for e in events if e.name == NAME_EVENT}
    events = [e for e in events if e.name != NAME_EVENT]
    if not any(e.cls == "THREAD" and e.priority is not None for e in events):
        raise TraceError(
            f"{path}: no thread priorities found; capture with tracelogger -w"
        )
    return Trace(
        int(header["TRACE_CYCLES_PER_SEC"]),
        first_cycles,
        events,
        names,
        header,
        markers,
        undecoded_markers,
    )
