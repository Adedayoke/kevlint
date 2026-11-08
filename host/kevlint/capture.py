"""Record a kernel trace on the QNX target and decode it on the host.

Uses the system ssh and scp, and traceprinter from the QNX SDP. Every failure
raises CaptureError with a message that says what to do next.
"""
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field

DEFAULT_TARGET = "root@localhost:2222"
TARGET_ENV = "KEVLINT_TARGET"
SDP_FIX = "Fix: source ~/qnx800/qnxsdp-env.sh"

SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
SSH_FAILED = 255
PREFLIGHT_TIMEOUT = 30
COPY_TIMEOUT = 120
CAPTURE_MARGIN = 60
WORKLOAD_GRACE = 30
MARK = "@kevlint "
LOG_TAIL = 20
START_SSHD = "sh /data/home/root/start_ssh.sh"

# The port answered and then dropped us. QEMU's port forward accepts the
# connection itself, so a stopped sshd looks like this rather than a refusal.
SSHD_DOWN = (
    "kex_exchange_identification",
    "connection closed",
    "connection reset",
    "banner exchange",
)
UNREACHABLE = (
    "timed out",
    "no route to host",
    "could not resolve",
    "network is unreachable",
)

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


def ssh_problem(target, stderr):
    """Turn ssh's stderr into a specific message with the fix."""
    text = stderr.lower()
    detail = stderr.strip()
    if "permission denied" in text:
        return f"{target} rejected the login. Install your ssh key on the target. ssh said: {detail}"
    if "host key" in text or "host identification" in text:
        return (
            f"the host key of {target} is not trusted, which is usual after rebuilding the "
            f"target image. Fix: ssh-keygen -R '[{target.host}]:{target.port}'. ssh said: {detail}"
        )
    if any(pattern in text for pattern in SSHD_DOWN):
        return (
            f"{target} accepted the connection and then closed it, so sshd is probably not "
            f"running. Fix: on the target console run: {START_SSHD}. ssh said: {detail}"
        )
    if "connection refused" in text:
        return (
            f"target unreachable: {target} refused the connection. If it is the QEMU target, "
            f"start it with ./run.sh. On a real board, run on its console: {START_SSHD}. "
            f"ssh said: {detail}"
        )
    if any(pattern in text for pattern in UNREACHABLE):
        return f"target unreachable: {target}. Check the address and that it is running. ssh said: {detail}"
    return f"ssh to {target} failed: {detail}"


class Session:
    """Runs commands on the target through the system ssh and scp."""

    def __init__(self, target):
        self.target = target

    def ssh_argv(self, command):
        return ["ssh", "-p", str(self.target.port), *SSH_OPTIONS, self.target.destination, command]

    def scp_argv(self, remote, local):
        source = f"{self.target.destination}:{remote}"
        return ["scp", "-P", str(self.target.port), *SSH_OPTIONS, source, local]

    def _run(self, argv, timeout, doing):
        try:
            return subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise CaptureError(f"{self.target} stopped responding while {doing}") from None
        except OSError as error:
            raise CaptureError(f"cannot run {argv[0]}: {error}") from None

    def preflight(self):
        """Prove the login works and tracelogger exists before starting anything."""
        done = self._run(self.ssh_argv("command -v tracelogger"), PREFLIGHT_TIMEOUT, "connecting")
        if done.returncode == SSH_FAILED:
            raise CaptureError(ssh_problem(self.target, done.stderr))
        if done.returncode != 0:
            raise CaptureError(
                f"tracelogger was not found on {self.target}. It must be in the target image "
                "and on the PATH of a non-interactive ssh session."
            )

    def run_script(self, script, timeout):
        done = self._run(self.ssh_argv(script), timeout, "capturing")
        if done.returncode == SSH_FAILED:
            raise CaptureError(
                f"lost the connection to {self.target} during the capture. ssh said: "
                f"{done.stderr.strip()}"
            )
        return done.stdout

    def copy(self, remote, local):
        done = self._run(self.scp_argv(remote, local), COPY_TIMEOUT, "copying the trace")
        if done.returncode != 0:
            raise CaptureError(f"could not copy the trace from {self.target}: {done.stderr.strip()}")

    def remove(self, *paths):
        """Delete remote files. Best effort: the trace is already safe or already lost."""
        try:
            self._run(self.ssh_argv(shlex.join(["rm", "-f", *paths])), PREFLIGHT_TIMEOUT, "cleaning up")
        except CaptureError:
            pass


def tracelogger_command(seconds, kev):
    """Always -w: only wide mode records priority and policy."""
    return shlex.join(["tracelogger", "-w", "-s", seconds, "-f", kev])


def remote_script(run, seconds, settle, kev, log, grace):
    """Shell script for the target. Reports through "@kevlint key value" lines."""
    emit_log = (
        f'echo "{MARK}log_begin"; cat {shlex.quote(log)} 2>/dev/null; echo; '
        f'echo "{MARK}log_end"; rm -f {shlex.quote(log)}'
    )
    lines = []
    if run is not None:
        lines += [f"sh -c {shlex.quote(run)} </dev/null >{shlex.quote(log)} 2>&1 &", "wl=$!"]
        if settle:
            lines.append(f"sleep {settle}")
        lines += [
            "if ! kill -0 $wl 2>/dev/null; then",
            f'  wait $wl; echo "{MARK}workload_before_capture $?"',
            f"  {emit_log}",
            "  exit 0",
            "fi",
        ]
    lines += [
        f"{tracelogger_command(seconds, kev)} 2>&1",
        f'status=$?; echo; echo "{MARK}tracelogger_status $status"',
        f'if [ -s {shlex.quote(kev)} ]; then echo "{MARK}kev_present 1"; else echo "{MARK}kev_present 0"; fi',
    ]
    if run is not None:
        lines += [
            f'if kill -0 $wl 2>/dev/null; then echo "{MARK}alive_after_capture 1"; '
            f'else echo "{MARK}alive_after_capture 0"; fi',
            "n=0",
            f"while [ $n -lt {grace} ] && kill -0 $wl 2>/dev/null; do sleep 1; n=$((n+1)); done",
            f'if kill -0 $wl 2>/dev/null; then echo "{MARK}workload_status running"; '
            f'else wait $wl; echo "{MARK}workload_status $?"; fi',
            emit_log,
        ]
    return "\n".join(lines) + "\n"


@dataclass
class RemoteReport:
    status: dict = field(default_factory=dict)
    other: list = field(default_factory=list)
    log: list = field(default_factory=list)


def parse_report(stdout):
    """Split the script's output. Workload text cannot forge a status line."""
    lines = stdout.splitlines()
    report = RemoteReport()
    begin_line = f"{MARK}log_begin"
    end_line = f"{MARK}log_end"
    head = lines
    if begin_line in lines:
        begin = lines.index(begin_line)
        head = lines[:begin]
        tail_lines = lines[begin + 1 :]
        if end_line in tail_lines:
            last = len(tail_lines) - 1 - tail_lines[::-1].index(end_line)
            tail_lines = tail_lines[:last]
        while tail_lines and not tail_lines[-1].strip():
            tail_lines.pop()
        report.log = tail_lines
    for line in head:
        if line.startswith(MARK):
            key, _, value = line[len(MARK) :].partition(" ")
            report.status[key] = value
        elif line.strip():
            report.other.append(line)
    return report


def log_tail(lines):
    """Up to the last LOG_TAIL lines of workload output, for humans."""
    if not lines:
        return "(no output)"
    shown = lines[-LOG_TAIL:]
    note = "" if len(shown) == len(lines) else f"(last {len(shown)} of {len(lines)} lines)\n"
    return note + "\n".join(shown)


def check_report(report):
    """Raise on a capture that cannot be used, and return warnings for one that can."""
    status = report.status
    if "workload_before_capture" in status:
        raise CaptureError(
            f"the workload ended before tracing began (exit status "
            f"{status['workload_before_capture']}), so the trace would not contain it. "
            f"Check the --run command. Workload output:\n{log_tail(report.log)}"
        )
    traced = status.get("tracelogger_status")
    if traced is None:
        raise CaptureError("the target did not report a tracelogger result")
    if traced != "0":
        output = " ".join(report.other) or "no output"
        raise CaptureError(f"tracelogger failed on the target with status {traced}: {output}")
    if status.get("kev_present") != "1":
        raise CaptureError(
            "the capture produced no events: tracelogger wrote no data. Check that the "
            "target runs an instrumented kernel."
        )

    warnings = []
    if status.get("alive_after_capture") == "0":
        warnings.append("the workload finished before tracing ended, so the trace only partly covers it")
    finished = status.get("workload_status")
    if finished == "running":
        warnings.append("the workload was still running after the capture and was left running on the target")
    elif finished not in (None, "0"):
        warnings.append(f"the workload exited with status {finished}")
    return warnings
