"""The tools an agent can call, all confined to one workspace directory."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .sandbox import Sandbox, _truncate

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache"}
MAX_READ = 40_000


class ToolError(Exception):
    """Raised for bad tool input; reported back to the model as an error result."""


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


_S = {"type": "string"}
_I = {"type": "integer"}

SCHEMAS = {
    "read_file": ("Read a text file from the repository. Returns numbered lines. Use start/end "
                  "(1-based, inclusive) for large files.",
                  _obj({"path": _S, "start": _I, "end": _I}, ["path"])),
    "list_dir": ("List a directory (non-recursive). Directories end with '/'.",
                 _obj({"path": _S}, ["path"])),
    "grep": ("Search file contents with a Python regular expression. Returns path:line: text "
             "matches. Optionally restrict to a sub-path.",
             _obj({"pattern": _S, "path": _S}, ["pattern"])),
    "write_file": ("Create or fully overwrite a file with the given content.",
                   _obj({"path": _S, "content": _S}, ["path", "content"])),
    "apply_patch": ("Edit a file by replacing one exact occurrence of `old` with `new`. `old` must "
                    "match the file byte-for-byte (including indentation) and be unique; include "
                    "surrounding lines to disambiguate.",
                    _obj({"path": _S, "old": _S, "new": _S}, ["path", "old", "new"])),
    "run_command": ("Run a shell command in the repository root inside the sandbox (no network, "
                    "timeout enforced, some destructive commands denied).",
                    _obj({"command": _S}, ["command"])),
    "run_tests": ("Run the project's test suite and return its output and exit code.",
                  _obj({}, [])),
    "git_diff": ("Show all changes made so far in this workspace (including new files).",
                 _obj({}, [])),
}


class Toolbox:
    def __init__(self, root: str | Path, sandbox: Sandbox | None = None,
                 test_cmd: str = "python -m pytest -q"):
        self.root = Path(root).resolve()
        self.sandbox = sandbox or Sandbox(self.root, mode="subprocess")
        self.test_cmd = test_cmd

    # -- schema -----------------------------------------------------------
    def schemas(self, finish_schema: dict, finish_description: str,
                allowed: tuple[str, ...] | None = None) -> list[dict]:
        tools = [{"name": n, "description": d, "input_schema": s} for n, (d, s) in SCHEMAS.items()
                 if allowed is None or n in allowed]
        tools.append({"name": "finish", "description": finish_description, "input_schema": finish_schema})
        return tools

    # -- dispatch ---------------------------------------------------------
    def execute(self, name: str, args: dict) -> str:
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            raise ToolError(f"unknown tool {name!r}")
        return fn(**args)

    def resolve(self, path: str) -> Path:
        """Resolve `path` inside the workspace; refuse anything that escapes it."""
        p = (self.root / path).resolve()
        if p != self.root and self.root not in p.parents:
            raise ToolError(f"path {path!r} is outside the workspace")
        if ".git" in p.relative_to(self.root).parts:
            raise ToolError("the .git directory is off limits")
        return p

    # -- tools ------------------------------------------------------------
    def t_read_file(self, path: str, start: int | None = None, end: int | None = None) -> str:
        p = self.resolve(path)
        if not p.is_file():
            raise ToolError(f"{path} is not a file")
        lines = p.read_text(errors="replace").splitlines()
        lo = max(1, start or 1)
        hi = min(len(lines), end or len(lines))
        body = "\n".join(f"{i:5d}  {lines[i - 1]}" for i in range(lo, hi + 1))
        return _truncate(body, MAX_READ) or "(empty file)"

    def t_list_dir(self, path: str = ".") -> str:
        p = self.resolve(path)
        if not p.is_dir():
            raise ToolError(f"{path} is not a directory")
        items = sorted(e.name + ("/" if e.is_dir() else "") for e in p.iterdir() if e.name not in SKIP_DIRS)
        return "\n".join(items) or "(empty)"

    def t_grep(self, pattern: str, path: str = ".") -> str:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise ToolError(f"bad regex: {e}") from e
        base = self.resolve(path)
        files = [base] if base.is_file() else _walk(base)
        hits = []
        for f in files:
            try:
                text = f.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{f.relative_to(self.root)}:{n}: {line.strip()[:200]}")
                    if len(hits) >= 200:
                        return "\n".join(hits) + "\n... (more matches truncated)"
        return "\n".join(hits) or "no matches"

    def t_write_file(self, path: str, content: str) -> str:
        p = self.resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"wrote {len(content)} bytes to {path}"

    def t_apply_patch(self, path: str, old: str, new: str) -> str:
        p = self.resolve(path)
        if not p.is_file():
            raise ToolError(f"{path} does not exist; use write_file to create it")
        text = p.read_text()
        count = text.count(old) if old else 0
        if count != 1:
            raise ToolError(f"`old` must match exactly once in {path}; it matched {count} times")
        p.write_text(text.replace(old, new, 1))
        return f"patched {path}"

    def t_run_command(self, command: str) -> str:
        return self.sandbox.run(command).render()

    def t_run_tests(self) -> str:
        return self.run_tests()[1]

    def run_tests(self) -> tuple[bool, str]:
        r = self.sandbox.run(self.test_cmd, timeout=max(self.sandbox.timeout, 600))
        return r.exit_code == 0, f"$ {self.test_cmd}\n{r.render()}"

    def t_git_diff(self) -> str:
        return diff(self.root) or "(no changes)"


def _walk(base: Path):
    for d, dirs, fs in os.walk(base):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
        for f in sorted(fs):
            yield Path(d) / f


def git(root: str | Path, *args: str, check: bool = True) -> str:
    """Run a fixed git argv on the host (never through the agent shell)."""
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def diff(root: str | Path, base: str = "HEAD") -> str:
    git(root, "add", "-A", "-N")  # intent-to-add so new files show up in the diff
    return _truncate(git(root, "diff", base), MAX_READ)
