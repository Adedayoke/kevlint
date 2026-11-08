"""Parse the traceprinter output that the priority-inversion rule needs.

Only THREAD events, KER_CALL events and thread names are kept. The rule needs
every KER_CALL, not just the sync calls, to tell what a thread last asked for.
"""
import re
from dataclasses import dataclass, field

EVENT_RE = re.compile(r"^t:0x([0-9a-fA-F]+) CPU:(\d+) (\S+)\s+:(\S+)\s*(.*)$")
FIELD_RE = re.compile(r"([^\s:]+):(\S+)")
HEADER_RE = re.compile(r"^\s*([A-Z_]+):: (.*)$")

KEPT_CLASSES = {"THREAD", "KER_CALL"}
NAME_EVENT = "PROCTHREAD_NAME"


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
class Trace:
    cycles_per_sec: int
    first_cycles: int
    events: list
    thread_names: dict
    header: dict = field(default_factory=dict)

    def seconds(self, cycles):
        """Seconds since the first event in the trace."""
        return (cycles - self.first_cycles) / self.cycles_per_sec

    def describe(self, thread):
        name = self.thread_names.get(thread, "unnamed")
        return f"{name} (pid {thread[0]} tid {thread[1]})"


def parse_trace(path):
    header = {}
    events = []
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
    return Trace(int(header["TRACE_CYCLES_PER_SEC"]), first_cycles, events, names, header)
