"""plan -> parallel workers (each reviewed) -> integrate, for one task."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .agent import Agent, AgentResult, Budget, TokenMeter
from .roles import INTEGRATOR, PLANNER, REVIEWER, WORKER, Role
from .sandbox import Sandbox
from .tools import Toolbox, diff, git
from .trajectory import Trajectory
from .workspace import add_worktree, commit_all, head, ident, prepare_repo, remove_worktree

MAX_SUBTASKS = 4
CONFLICT = re.compile(r"^(<{7}|>{7})( |$)", re.M)


@dataclass
class TaskSpec:
    task_id: str
    repo: str
    text: str
    test_cmd: str = "python -m pytest -q"
    sandbox: str = "auto"
    max_workers: int = 3
    review_rounds: int = 2
    budget: Budget = field(default_factory=Budget)
    task_tokens: int | None = None  # shared across all agents of the task


class Pipeline:
    def __init__(self, spec: TaskSpec, models: Callable, home: Path,
                 progress: Callable[[str], None] = lambda stage: None):
        self.spec = spec
        self.models = models
        self.home = Path(home)
        self.progress = progress
        self.runs = self.home / "runs" / spec.task_id
        self.wt_root = self.home / "worktrees" / spec.task_id
        self.repo: Path | None = None
        self.base = ""
        self._integrator_runs = 0
        self.meter = TokenMeter(spec.task_tokens)

    # -- helpers ----------------------------------------------------------
    def branch(self, suffix: str = "") -> str:
        return f"fleet/{self.spec.task_id}" + (f"-{suffix}" if suffix else "")

    def toolbox(self, wt: Path) -> Toolbox:
        return Toolbox(wt, Sandbox(wt, mode=self.spec.sandbox), self.spec.test_cmd)

    def agent(self, name: str, role: Role, wt: Path) -> Agent:
        return Agent(name, role, self.models(name), self.toolbox(wt),
                     Trajectory(self.runs / f"{name}.jsonl", name), self.spec.budget, self.meter)

    # -- stages -----------------------------------------------------------
    def run(self) -> dict:
        self.progress("preparing")
        self.repo = prepare_repo(self.spec.repo, self.home)
        self.base = head(self.repo)
        try:
            subtasks, plan = self.plan()
            self.progress(f"working ({len(subtasks)} subtasks)")
            with ThreadPoolExecutor(max_workers=max(1, self.spec.max_workers)) as pool:
                work = list(pool.map(lambda a: self.work(*a), enumerate(subtasks, 1)))
            self.progress("integrating")
            integration = self.integrate(work)
        finally:
            for wt in (self.wt_root.iterdir() if self.wt_root.exists() else []):
                remove_worktree(self.repo, wt)
        return {"repo": str(self.repo), "base": self.base, "plan": plan, "subtasks": work, **integration,
                "tokens": {"total": self.meter.used, "by_agent": self.meter.by_agent}}

    def plan(self) -> tuple[list[dict], str]:
        self.progress("planning")
        wt = add_worktree(self.repo, self.wt_root / "planner", None, self.base)
        res = self.agent("planner", PLANNER, wt).run(
            f"Task:\n{self.spec.text}\n\nExplore the repository and produce a plan.")
        subtasks = [s for s in res.output.get("subtasks", [])
                    if isinstance(s, dict) and s.get("description")][:MAX_SUBTASKS]
        if not subtasks:  # planner failed or returned nothing: one worker takes the whole task
            subtasks = [{"title": "Complete the task", "description": self.spec.text}]
        return subtasks, res.output.get("summary", "")

    def work(self, i: int, sub: dict) -> dict:
        name = f"worker-{i}"
        branch = self.branch(f"w{i}")
        wt = add_worktree(self.repo, self.wt_root / name, branch, self.base)
        prompt = (f"Overall task:\n{self.spec.text}\n\nYour subtask ({sub['title']}):\n"
                  f"{sub['description']}")
        rounds, verdict, feedback = [], "none", ""
        for rnd in range(1, self.spec.review_rounds + 2):  # first attempt + revisions
            agent_name = name if rnd == 1 else f"{name}.r{rnd}"
            res = self.agent(agent_name, WORKER, wt).run(
                prompt if rnd == 1 else f"{prompt}\n\nA reviewer requested changes to your "
                f"previous attempt (already applied in this worktree):\n{feedback}\n\nAddress them.")
            commit_all(wt, f"{sub['title']}\n\nfleet task {self.spec.task_id}, {agent_name}")
            rounds.append({"agent": agent_name, "status": res.status, "summary": res.output.get("summary", "")})
            if not diff(wt, self.base).strip():
                verdict, feedback = "no_changes", "worker produced no changes"
                break
            verdict, feedback = self.review(i, rnd, wt, sub)
            if verdict == "approve":
                break
        return {"index": i, "title": sub["title"], "branch": branch, "verdict": verdict,
                "feedback": feedback, "rounds": rounds}

    def review(self, i: int, rnd: int, wt: Path, sub: dict) -> tuple[str, str]:
        ok, tests = self.toolbox(wt).run_tests()
        res = self.agent(f"reviewer-{i}" + ("" if rnd == 1 else f".r{rnd}"), REVIEWER, wt).run(
            f"Overall task:\n{self.spec.text}\n\nSubtask under review ({sub['title']}):\n"
            f"{sub['description']}\n\nDiff against base:\n```diff\n{diff(wt, self.base)}\n```\n\n"
            f"Test run ({'passing' if ok else 'FAILING'}):\n```\n{tests[-6000:]}\n```")
        verdict = res.output.get("verdict") if res.ok else None
        if verdict not in ("approve", "request_changes"):
            return "request_changes", f"reviewer did not finish ({res.status})"
        return verdict, res.output.get("feedback", "")

    def integrate(self, work: list[dict]) -> dict:
        branch = self.branch()
        wt = add_worktree(self.repo, self.wt_root / "integrator", branch, self.base)
        merged, skipped = [], []
        for w in work:
            if w["verdict"] != "approve":
                skipped.append(w["branch"])
                continue
            if self.merge(wt, w):
                merged.append(w["branch"])
            else:
                skipped.append(w["branch"])
        ok, tests = self.toolbox(wt).run_tests()
        if merged and not ok:
            self.integrator(wt, f"The merged branch fails its tests:\n```\n{tests[-6000:]}\n```\n"
                                "Fix the integration so the full suite passes.")
            commit_all(wt, f"Fix integration of fleet task {self.spec.task_id}")
            ok, tests = self.toolbox(wt).run_tests()
        stat = git(wt, "diff", "--stat", self.base, "HEAD").rstrip()
        return {"branch": branch if merged else None, "merged": merged, "skipped": skipped,
                "tests_passed": ok, "test_output": tests[-4000:], "diffstat": stat,
                "diff": git(wt, "diff", self.base, "HEAD")[:40_000]}

    def merge(self, wt: Path, w: dict) -> bool:
        msg = f"Merge {w['branch']}: {w['title']}"
        git(wt, *ident(wt), "merge", "--no-ff", "--no-edit", "-m", msg, w["branch"], check=False)
        files = git(wt, "diff", "--name-only", "--diff-filter=U").split()
        if not files:
            return True  # clean merge
        self.integrator(wt, f"Merging {w['branch']} ({w['title']}) produced conflicts in: "
                            f"{', '.join(files)}. Resolve every conflict marker.")
        if any(CONFLICT.search((wt / f).read_text(errors="replace")) for f in files if (wt / f).exists()):
            git(wt, "merge", "--abort", check=False)
            return False
        git(wt, "add", "-A")
        git(wt, *ident(wt), "commit", "--quiet", "--no-verify", "--no-edit")
        return True

    def integrator(self, wt: Path, problem: str) -> AgentResult:
        self._integrator_runs += 1
        return self.agent(f"integrator-{self._integrator_runs}", INTEGRATOR, wt).run(
            f"Overall task:\n{self.spec.text}\n\n{problem}")
