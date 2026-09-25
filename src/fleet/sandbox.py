"""Isolated command execution for agents.

Every shell command an agent issues goes through `Sandbox.run`. Commands are
checked against a denylist first, then executed either inside a throwaway
Docker container (network disabled, only the workspace mounted) or, when
Docker is unavailable, as a subprocess jailed to the workspace directory with
a scrubbed environment and a hard timeout.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# ponytail: regex denylist is a speed bump, not a security boundary. The real
# boundary is the Docker backend (no network, only the worktree mounted).
DENYLIST = [
    (r"\brm\s+(-\S+\s+)*(/|~|\$HOME|\.\.)", "delete outside workspace"),
    (r"\bsudo\b|\bsu\s", "privilege escalation"),
    (r"\bgit\s+push\b", "agents never push; the orchestrator does"),
    (r"\bgit\s+remote\b", "remote manipulation"),
    (r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f)", "destructive git operation"),
    (r"\bgh\s", "GitHub CLI is reserved for the orchestrator"),
    (r"\b(mkfs|fdisk|diskutil|dd\s+if=)", "disk operation"),
    (r"\b(shutdown|reboot|halt|poweroff|launchctl|systemctl)\b", "system control"),
    (r":\(\)\s*\{\s*:\|:&\s*\};:", "fork bomb"),
    (r"\bchmod\s+(-R\s+)?[0-7]{3,4}\s+/(\s|$)", "chmod on root"),
    (r"(curl|wget)[^|]*\|\s*(ba|z)?sh\b", "pipe-to-shell download"),
    (r">\s*/dev/(sd|disk)", "raw device write"),
    (r"\bkill(all)?\s+-9\s+(-1|1)\b", "killing all processes"),
]
_DENY = [(re.compile(p), why) for p, why in DENYLIST]

# Environment variables never forwarded into agent commands.
SECRET_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.I)

MAX_OUTPUT = 20_000


def check_command(cmd: str) -> str | None:
    """Return a reason string if `cmd` is denied, else None."""
    for pattern, why in _DENY:
        if pattern.search(cmd):
            return why
    return None


@dataclass
class Result:
    exit_code: int
    output: str
    timed_out: bool = False

    def render(self) -> str:
        head = "TIMED OUT" if self.timed_out else f"exit code {self.exit_code}"
        return f"[{head}]\n{self.output}"


def _truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + f"\n... [{len(text) - limit} chars truncated] ...\n" + text[-half:]


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class Sandbox:
    """Runs commands with the workspace as the only writable root."""

    def __init__(self, root: str | Path, mode: str = "auto", timeout: int = 120,
                 image: str = "python:3.12-slim"):
        self.root = Path(root).resolve()
        if mode == "auto":
            mode = "docker" if docker_available() else "subprocess"
        if mode not in ("docker", "subprocess"):
            raise ValueError(f"unknown sandbox mode {mode!r}")
        self.mode = mode
        self.timeout = timeout
        self.image = image

    def run(self, cmd: str, timeout: int | None = None) -> Result:
        reason = check_command(cmd)
        if reason:
            return Result(126, f"command denied by sandbox policy: {reason}")
        timeout = timeout or self.timeout
        argv = self._docker_argv(cmd) if self.mode == "docker" else ["/bin/sh", "-c", cmd]
        env = {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)}
        # Stale .pyc files can mask same-second edits (mtime granularity); never write them.
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if self.mode == "subprocess":  # `python` resolves to fleet's own interpreter
            env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")
        proc = subprocess.Popen(
            argv, cwd=self.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True, text=True, errors="replace",
        )
        try:
            out, _ = proc.communicate(timeout=timeout)
            return Result(proc.returncode, _truncate(out))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            out, _ = proc.communicate()
            return Result(124, _truncate(out or ""), timed_out=True)

    def _docker_argv(self, cmd: str) -> list[str]:
        argv = ["docker", "run", "--rm", "--network", "none", "--memory", "2g", "--cpus", "2",
                "--pids-limit", "256", "-v", f"{self.root}:/work", "-w", "/work",
                "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1"]
        if hasattr(os, "getuid"):
            argv += ["--user", f"{os.getuid()}:{os.getgid()}"]
        return argv + [self.image, "sh", "-c", cmd]
