"""Record a kernel trace on the QNX target and decode it on the host.

Uses the system ssh and scp, and traceprinter from the QNX SDP. Every failure
raises CaptureError with a message that says what to do next.
"""
import os
import re
import shutil
from dataclasses import dataclass

DEFAULT_TARGET = "root@localhost:2222"
TARGET_ENV = "KEVLINT_TARGET"
SDP_FIX = "Fix: source ~/qnx800/qnxsdp-env.sh"

TARGET_RE = re.compile(
    r"^([A-Za-z0-9_][A-Za-z0-9._-]*)@([A-Za-z0-9][A-Za-z0-9.-]*)(?::([0-9]+))?$"
)


class CaptureError(Exception):
    """The capture could not be completed."""


@dataclass(frozen=True)
class Target:
    user: str
    host: str
    port: int

    @property
    def destination(self):
        return f"{self.user}@{self.host}"

    def __str__(self):
        return f"{self.destination}:{self.port}"


def parse_target(text):
    """Parse user@host[:port]. Without a port, use ssh's own default of 22."""
    match = TARGET_RE.match(text)
    if match is None:
        raise CaptureError(f"bad target {text!r}: expected user@host or user@host:port")
    port = int(match[3] or 22)
    if not 1 <= port <= 65535:
        raise CaptureError(f"bad target {text!r}: port must be between 1 and 65535")
    return Target(match[1], match[2], port)


def resolve_target(flag, environ):
    """The --target flag wins over the environment, then the default."""
    return parse_target(flag or environ.get(TARGET_ENV) or DEFAULT_TARGET)


def output_paths(out):
    """Return the .kev, .txt and .json paths for an --out prefix."""
    return {suffix: f"{out}.{suffix}" for suffix in ("kev", "txt", "json")}


def check_outputs(paths, force):
    """Refuse to replace existing files unless forced, before any capture runs."""
    existing = [path for path in paths.values() if os.path.exists(path)]
    if existing and not force:
        raise CaptureError(
            f"output already exists: {', '.join(existing)}. Use --force to replace it."
        )
    parent = os.path.dirname(paths["kev"]) or "."
    if not os.path.isdir(parent):
        raise CaptureError(f"output directory does not exist: {parent}")


def check_host_tools(environ):
    """Find the host tools the capture needs, before touching the target."""
    if not environ.get("QNX_HOST"):
        raise CaptureError(f"QNX_HOST is not set, so traceprinter cannot run. {SDP_FIX}")
    if shutil.which("traceprinter", path=environ.get("PATH")) is None:
        raise CaptureError(f"traceprinter is not on PATH. {SDP_FIX}")
    for tool in ("ssh", "scp"):
        if shutil.which(tool, path=environ.get("PATH")) is None:
            raise CaptureError(f"{tool} is not on PATH. Fix: sudo apt install openssh-client")
