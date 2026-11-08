"""Stands in for ssh, scp, traceprinter and tracelogger in tests.

Each tool is a small wrapper that runs this file with the tool's name first.
Every call is appended to $FAKE_ROOT/calls.jsonl. The fake ssh runs the remote
command in a real shell, so the quoting is really exercised. Behavior comes from
FAKE_* variables, and the trace content from the real files in fixtures/.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REMOTE_TMP = "/tmp/kevlint_"
SSH_ERRORS = {
    "refused": "ssh: connect to host localhost port 2222: Connection refused",
    "closed": "Connection closed by 127.0.0.1 port 2222",
    "reset": "kex_exchange_identification: read: Connection reset by peer",
    "banner": "Connection timed out during banner exchange",
    "timeout": "ssh: connect to host board port 22: Connection timed out",
    "denied": "root@localhost: Permission denied (publickey).",
    "hostkey": "Host key verification failed.",
}


def root():
    return Path(os.environ["FAKE_ROOT"])


def sandboxed(text):
    return text.replace(REMOTE_TMP, f"{root()}/kevlint_")


def ssh(args):
    remote = args[-1]
    mode = os.environ.get("FAKE_SSH", "ok")
    capturing = "tracelogger -w" in remote
    if mode in SSH_ERRORS:
        print(SSH_ERRORS[mode], file=sys.stderr)
        return 255
    if mode == "lost" and capturing:
        print("client_loop: send disconnect: Broken pipe", file=sys.stderr)
        return 255
    env = dict(os.environ, PATH=f"{root()}/remote_bin:/usr/bin:/bin")
    return subprocess.run(["sh", "-c", sandboxed(remote)], env=env, stdin=subprocess.DEVNULL).returncode


def scp(args):
    source, destination = args[-2:]
    if os.environ.get("FAKE_SCP") == "fail":
        print(f"scp: {source}: No such file or directory", file=sys.stderr)
        return 1
    shutil.copyfile(sandboxed(source.partition(":")[2]), destination)
    return 0


def tracelogger(args):
    mode = os.environ.get("FAKE_TRACELOGGER", "ok")
    if mode == "fail":
        print("tracelogger: could not start", file=sys.stderr)
        return 1
    output = args[args.index("-f") + 1]
    if mode == "empty":
        open(output, "w").close()
    else:
        shutil.copyfile(os.environ["FAKE_FIXTURE"] + ".kev", output)
    return 0


def traceprinter(args):
    if os.environ.get("FAKE_TRACEPRINTER") == "fail":
        print("traceprinter: bad file", file=sys.stderr)
        return 1
    with open(os.environ["FAKE_FIXTURE"] + ".txt", errors="replace") as trace:
        for line in trace:
            if os.environ.get("FAKE_TRACEPRINTER") == "noevents" and line.startswith("t:0x"):
                break
            sys.stdout.write(line)
    return 0


TOOLS = {"ssh": ssh, "scp": scp, "tracelogger": tracelogger, "traceprinter": traceprinter}

if __name__ == "__main__":
    name, arguments = sys.argv[1], sys.argv[2:]
    with open(root() / "calls.jsonl", "a", encoding="utf-8") as calls:
        calls.write(json.dumps([name, *arguments]) + "\n")
    sys.exit(TOOLS[name](arguments))
