"""Record a kernel trace on the QNX target and decode it on the host.

Uses the system ssh and scp, and traceprinter from the QNX SDP. Every failure
raises CaptureError with a message that says what to do next.
"""
import datetime
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from typing import Optional

from . import __version__

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
DECODE_TIMEOUT = 120
MIN_SPAN_FRACTION = 0.9
GIT_TIMEOUT = 10
START_SSHD = "sh /data/home/root/start_ssh.sh"
KEVLINTD_STATUS = "/dev/kevlint/status"

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

    def kevlintd_status(self):
        """Return kevlintd's status text, or raise if it is not running."""
        done = self._run(
            self.ssh_argv(shlex.join(["cat", KEVLINTD_STATUS])),
            PREFLIGHT_TIMEOUT,
            "reading the kevlintd status",
        )
        if done.returncode == SSH_FAILED:
            raise CaptureError(ssh_problem(self.target, done.stderr))
        if done.returncode != 0 or not done.stdout.startswith("version: "):
            raise CaptureError(
                f"kevlintd is not running on {self.target}: could not read {KEVLINTD_STATUS}. "
                "Fix: start kevlintd on the target, or leave out --check-kevlintd."
            )
        return done.stdout

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
    finished = status.get("workload_status")
    if status.get("alive_after_capture") == "0":
        # A verdict on this trace could be a PASS on a trace without the workload.
        raise CaptureError(
            f"the workload ended before tracing ended (exit status {finished}), so the trace "
            f"may not contain it and its verdict would mean nothing. Check the --run command, "
            f"or make it outlast --seconds. Workload output:\n{log_tail(report.log)}"
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
    if finished == "running":
        warnings.append("the workload was still running after the capture and was left running on the target")
    elif finished not in (None, "0"):
        warnings.append(f"the workload exited with status {finished}")
    return warnings


def decode(kev, txt):
    """Run traceprinter and confirm that the result has events."""
    try:
        with open(txt, "w", encoding="utf-8") as out:
            done = subprocess.run(
                ["traceprinter", "-f", kev],
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=DECODE_TIMEOUT,
            )
    except subprocess.TimeoutExpired:
        raise CaptureError("traceprinter did not finish decoding the trace") from None
    except OSError as error:
        raise CaptureError(f"cannot run traceprinter: {error}") from None
    if done.returncode != 0:
        raise CaptureError(f"traceprinter failed with status {done.returncode}: {done.stderr.strip()}")
    with open(txt, encoding="utf-8", errors="replace") as lines:
        if not any(line.startswith("t:0x") for line in lines):
            raise CaptureError(
                "the capture produced no events: the decoded trace is empty. Check that the "
                "target runs an instrumented kernel."
            )


def trace_span(txt):
    """Seconds between the first and last events of decoded text, or None if unknown."""
    rate = first = last = None
    with open(txt, encoding="utf-8", errors="replace") as lines:
        for line in lines:
            if line.startswith("t:0x"):
                cycles = int(line[4:].split(" ", 1)[0], 16)
                first = cycles if first is None else first
                last = cycles
            elif first is None and "TRACE_CYCLES_PER_SEC::" in line:
                rate = int(line.partition("::")[2])
    if rate is None or first is None:
        return None
    return (last - first) / rate


def check_span(txt, seconds):
    """Reject a trace much shorter than requested.

    tracelogger exits 0 with no message when its output disk fills, and the .kev
    just stops, so its exit status cannot be relied on for this.
    """
    span = trace_span(txt)
    if span is not None and span < float(seconds) * MIN_SPAN_FRACTION:
        raise CaptureError(
            f"the trace covers only {span:.2f} s of the {seconds} s requested, so its verdict "
            "would cover less than you asked for. tracelogger stops without an error when its "
            "output disk fills: check free space with df -h /tmp on the target, or shorten "
            "--seconds."
        )


def git_info():
    """Return (commit, dirty) for kevlint's own source, or (None, None)."""
    source = os.path.dirname(os.path.abspath(__file__))

    def git(*args):
        return subprocess.run(
            ["git", "-C", source, *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )

    try:
        commit = git("rev-parse", "HEAD")
        changes = git("status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    if commit.returncode != 0 or changes.returncode != 0:
        return None, None
    return commit.stdout.strip(), bool(changes.stdout.strip())


@dataclass
class Options:
    seconds: str
    out: str
    run: Optional[str] = None
    target: Optional[str] = None
    settle: int = 2
    force: bool = False
    command_line: str = ""
    workload_grace: int = WORKLOAD_GRACE
    check_kevlintd: bool = False


def metadata(options, target, kev, report, kevlintd_status=None):
    commit, dirty = git_info()
    finished = report.status.get("workload_status")
    extra = {} if kevlintd_status is None else {"kevlintd_status": kevlintd_status}
    return {
        "capture_command": tracelogger_command(options.seconds, kev),
        "workload_command": options.run,
        "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kevlint_version": __version__,
        "git_commit": commit,
        "git_dirty": dirty,
        "kevlint_command": options.command_line,
        "target": str(target),
        "settle_seconds": options.settle if options.run is not None else None,
        "workload_status": finished,
        **extra,
    }


def capture(options, environ=None):
    """Capture, copy and decode a trace, and return the path of the decoded text.

    Nothing appears at the output paths unless every step succeeds.
    """
    environ = os.environ if environ is None else environ
    target = resolve_target(options.target, environ)
    paths = output_paths(options.out)
    check_outputs(paths, options.force)
    check_host_tools(environ)
    try:
        work = tempfile.mkdtemp(prefix=".kevlint_capture_", dir=os.path.dirname(paths["kev"]) or ".")
    except OSError as error:
        raise CaptureError(f"cannot write to the output directory: {error}") from None

    session = Session(target)
    name = uuid.uuid4().hex[:12]
    kev = f"/tmp/kevlint_{name}.kev"
    log = f"/tmp/kevlint_{name}.log"
    local = {suffix: os.path.join(work, f"capture.{suffix}") for suffix in paths}
    started = False
    kevlintd_status = None
    try:
        print(f"kevlint capture: connecting to {target}", flush=True)
        session.preflight()
        if options.check_kevlintd:
            kevlintd_status = session.kevlintd_status()
            print(f"kevlintd status:\n{kevlintd_status.rstrip()}", flush=True)
        settle = options.settle if options.run is not None else 0
        print(f"kevlint capture: tracing for {options.seconds} s", flush=True)
        script = remote_script(options.run, options.seconds, settle, kev, log, options.workload_grace)
        timeout = settle + float(options.seconds) + options.workload_grace + CAPTURE_MARGIN
        started = True
        report = parse_report(session.run_script(script, timeout))
        warnings = check_report(report)
        if options.run is not None:
            print(f"Workload output:\n{log_tail(report.log)}", flush=True)
        print("kevlint capture: copying and decoding the trace", flush=True)
        session.copy(kev, local["kev"])
        decode(local["kev"], local["txt"])
        check_span(local["txt"], options.seconds)
        with open(local["json"], "w", encoding="utf-8") as out:
            json.dump(metadata(options, target, kev, report, kevlintd_status), out, indent=2)
            out.write("\n")
        for suffix, path in paths.items():
            os.replace(local[suffix], path)
    except OSError as error:
        raise CaptureError(f"cannot write the capture output: {error}") from None
    finally:
        if started:
            session.remove(kev, log)
        shutil.rmtree(work, ignore_errors=True)

    for warning in warnings:
        print(f"kevlint: warning: {warning}", file=sys.stderr, flush=True)
    print(f"Captured: {paths['kev']}, {paths['txt']}, {paths['json']}\n", flush=True)
    return paths["txt"]
