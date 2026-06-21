"""`fleet bench`: run the fleet on small seeded-bug repos and score it.

Each case in `cases/<name>/` has `repo/` (a tiny project with a failing test
suite), `case.json` (`task`, `test_cmd`) and `script.json` (the scripted
backend's solution, so the harness itself is testable offline). A case passes
when the fleet's branch passes the test suite in a clean checkout *and* the
seeded tests were left untouched (new test files are allowed).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from ..orchestrator import Orchestrator
from ..queue import Queue
from ..tools import git
from ..trajectory import read

CASES = Path(__file__).parent / "cases"


@dataclass
class CaseResult:
    case: str
    passed: bool
    status: str
    turns: int
    tokens: int
    cost: float
    seconds: float
    note: str = ""


def case_names() -> list[str]:
    return sorted(p.name for p in CASES.iterdir() if (p / "case.json").exists())


def seed_repo(case: str, dest: Path) -> Path:
    shutil.copytree(CASES / case / "repo", dest, ignore=shutil.ignore_patterns("__pycache__"))
    for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                 ["-c", "user.name=fleet-bench", "-c", "user.email=bench@example.invalid",
                  "commit", "-q", "-m", f"{case}: seeded bug"]):
        git(dest, *args)
    return dest


def verify(repo: Path, branch: str | None, test_cmd: str) -> tuple[bool, str]:
    """Independent check on a clean checkout of the branch, outside the fleet's sandbox."""
    if not branch:
        return False, "no branch produced"
    # New regression tests are welcome (--test-first writes them); changing or deleting
    # the seeded ones is how a fix could cheat.
    if git(repo, "diff", "--name-only", "--no-renames", "--diff-filter=MDT", "main", branch, "--", "tests").strip():
        return False, "tests were modified"
    with tempfile.TemporaryDirectory(prefix="fleet-bench-verify-") as d:
        git(repo, "worktree", "add", "--quiet", "--detach", d, branch)
        try:
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                   "PATH": os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")}
            r = subprocess.run(test_cmd, shell=True, cwd=d, capture_output=True, text=True,
                               timeout=300, env=env)
        finally:
            git(repo, "worktree", "remove", "--force", d, check=False)
    return r.returncode == 0, "" if r.returncode == 0 else "tests fail on the branch"


def run(cases: list[str], backend: str, home: Path, sandbox: str = "auto", concurrency: int = 2,
        **options) -> list[CaseResult]:
    q = Queue(home / "fleet.db")
    submitted = []
    for case in cases:
        spec = json.loads((CASES / case / "case.json").read_text())
        repo = seed_repo(case, home / "repos-bench" / case)
        opts = {"backend": backend, "sandbox": sandbox, "test_cmd": spec["test_cmd"], **options}
        if backend == "scripted":
            opts["script"] = str(CASES / case / "script.json")
        submitted.append((case, repo, spec, q.submit(str(repo), spec["task"], max_attempts=1, **opts)))
    started = time.time()
    Orchestrator(home, q, concurrency).run(drain=True, poll=0.2)
    out = []
    for case, repo, spec, tid in submitted:
        t = q.get(tid)
        r = t["result"] or {}
        ends = [e for f in (home / "runs" / tid).glob("*.jsonl") for e in read(f) if e["event"] == "end"]
        ok, note = verify(repo, r.get("branch"), spec["test_cmd"])
        tok = r.get("tokens") or {}
        out.append(CaseResult(case, ok, t["status"], sum(e.get("turns", 0) for e in ends),
                              tok.get("total", sum(e.get("tokens", 0) for e in ends)),
                              tok.get("cost_usd", sum(e.get("cost", 0.0) for e in ends)),
                              round(t["updated"] - started, 1), note or (t["error"] or "")[-120:]))
    return out


def report(results: list[CaseResult], backend: str) -> str:
    rows = [f"{'CASE':12} {'RESULT':6} {'TURNS':>5} {'TOKENS':>8} {'COST':>9}  NOTE"]
    for r in results:
        rows.append(f"{r.case:12} {'PASS' if r.passed else 'FAIL':6} {r.turns:>5} {r.tokens:>8,} "
                    f"{'$%.4f' % r.cost:>9}  {r.note}".rstrip())
    n, passed = len(results), sum(r.passed for r in results)
    rows.append(f"pass rate {passed}/{n} ({100 * passed / max(n, 1):.0f}%), "
                f"turns {sum(r.turns for r in results)}, tokens {sum(r.tokens for r in results):,}, "
                f"cost ${sum(r.cost for r in results):.4f} [{backend} backend"
                + ("; tokens and cost are simulated" if backend == "scripted" else "") + "]")
    return "\n".join(rows)


def as_json(results: list[CaseResult], backend: str) -> str:
    return json.dumps({"backend": backend, "pass_rate": sum(r.passed for r in results) / max(len(results), 1),
                       "cases": [asdict(r) for r in results]}, indent=2)
