"""plan -> parallel workers (each reviewed) -> integrate, for one task."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .agent import Agent, AgentResult, Budget, TokenMeter
from .roles import INTEGRATOR, PLANNER, REVIEWER, TESTER, WORKER, Role
from .sandbox import DEFAULT_IMAGE, Sandbox
from .tools import Toolbox, diff, git, is_test_path
from .trajectory import Trajectory, summarize
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
    image: str = DEFAULT_IMAGE  # docker sandbox image; bake project deps into your own
    max_workers: int = 3
    review_rounds: int = 2
    budget: Budget = field(default_factory=Budget)
    task_tokens: int | None = None  # shared across all agents of the task
    max_cost: float | None = None   # USD, shared across all agents; hard stop
    test_first: bool = False        # a tester adds a failing regression test before each fix


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
        self.meter = TokenMeter(spec.task_tokens, spec.max_cost)

    # -- helpers ----------------------------------------------------------
    def branch(self, suffix: str = "") -> str:
        return f"fleet/{self.spec.task_id}" + (f"-{suffix}" if suffix else "")

    def toolbox(self, wt: Path) -> Toolbox:
        return Toolbox(wt, Sandbox(wt, mode=self.spec.sandbox, image=self.spec.image), self.spec.test_cmd)

    def agent(self, name: str, role: Role, wt: Path, resume: bool = True) -> Agent:
        return Agent(name, role, self.models(name), self.toolbox(wt),
                     Trajectory(self.runs / f"{name}.jsonl", name), self.spec.budget, self.meter, resume)

    def started(self, agent: str) -> bool:
        """Whether a previous attempt already ran this agent (so its worktree is resumed)."""
        return (self.runs / f"{agent}.jsonl").exists()

    def cleanup(self) -> None:
        if self.repo is None:
            return
        for wt in (self.wt_root.iterdir() if self.wt_root.exists() else []):
            remove_worktree(self.repo, wt)

    # -- stages -----------------------------------------------------------
    def run(self) -> dict:
        resuming = self.runs.exists()
        self.progress("resuming" if resuming else "preparing")
        self.repo = prepare_repo(self.spec.repo, self.home, update=not resuming)
        base_file = self.runs / "base"
        self.base = base_file.read_text().strip() if resuming and base_file.exists() else head(self.repo)
        self.runs.mkdir(parents=True, exist_ok=True)
        base_file.write_text(self.base)
        # Worktrees survive a crash so a retry or a restarted orchestrator resumes the
        # agents' checkpoints against the files they were editing; cleanup() on success.
        subtasks, plan = self.plan()
        self.progress(f"working ({len(subtasks)} subtasks)")
        with ThreadPoolExecutor(max_workers=max(1, self.spec.max_workers)) as pool:
            work = list(pool.map(lambda a: self.work(*a), enumerate(subtasks, 1)))
        self.progress("integrating")
        integration = self.integrate(work)
        self.cleanup()
        for b in integration["merged"]:  # history lives on in the merge commits of fleet/<task>
            git(self.repo, "branch", "-D", b, check=False)
        return {"repo": str(self.repo), "base": self.base, "plan": plan, "subtasks": work, **integration,
                "tokens": self.meter.report(), "trajectory": summarize(self.runs)}

    def plan(self) -> tuple[list[dict], str]:
        self.progress("planning")
        wt = add_worktree(self.repo, self.wt_root / "planner", None, self.base, keep=self.started("planner"))
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
        wt = add_worktree(self.repo, self.wt_root / name, branch, self.base, keep=self.started(name))
        prompt = (f"Overall task:\n{self.spec.text}\n\nYour subtask ({sub['title']}):\n"
                  f"{sub['description']}")
        red = self.write_test(i, sub, wt) if self.spec.test_first else None
        if red:
            prompt += (f"\n\nA regression test that reproduces this was added first and fails on the "
                       f"current code: {', '.join(red['files'])}. Make it pass without changing it.")
        rounds, verdict, feedback = [], "none", ""
        for rnd in range(1, self.spec.review_rounds + 2):  # first attempt + revisions
            agent_name = name if rnd == 1 else f"{name}.r{rnd}"
            res = self.agent(agent_name, WORKER, wt).run(
                prompt if rnd == 1 else f"{prompt}\n\nA reviewer requested changes to your "
                f"previous attempt (already applied in this worktree):\n{feedback}\n\nAddress them.")
            commit_all(wt, f"{sub['title']}\n\nfleet task {self.spec.task_id}, {agent_name}")
            rounds.append({"agent": agent_name, "status": res.status, "summary": res.output.get("summary", "")})
            if red and (touched := git(wt, "diff", "--name-only", red["commit"], "HEAD", "--", *red["files"]).split()):
                # A fix that edits the test it is meant to satisfy proves nothing: put the test
                # back and send the worker round again without spending a review on it.
                git(wt, "checkout", red["commit"], "--", *touched)
                commit_all(wt, f"Restore regression test {', '.join(touched)}\n\nfleet task {self.spec.task_id}")
                verdict, feedback = "request_changes", (
                    f"You changed the regression test ({', '.join(touched)}); it has been restored. "
                    "Make it pass by fixing the code, not the test.")
                continue
            if not diff(wt, self.base).strip():
                verdict, feedback = "no_changes", "worker produced no changes"
                break
            verdict, feedback = self.review(i, rnd, wt, sub, red)
            if verdict == "approve":
                break
        return {"index": i, "title": sub["title"], "branch": branch, "verdict": verdict,
                "feedback": feedback, "rounds": rounds, "regression_test": red}

    def write_test(self, i: int, sub: dict, wt: Path) -> dict | None:
        """Tester stage: a regression test committed on the worker's branch before any fix.

        Kept only if it touches test files alone and those tests fail on the base code; otherwise
        the commit is dropped and the worker starts from base as usual."""
        saved = self.runs / f"tester-{i}.red.json"
        if saved.exists():  # resuming: the stage already ran
            return json.loads(saved.read_text()) or None
        self.agent(f"tester-{i}", TESTER, wt).run(
            f"Overall task:\n{self.spec.text}\n\nSubtask to write a regression test for "
            f"({sub['title']}):\n{sub['description']}")
        commit_all(wt, f"Add regression test: {sub['title']}\n\nfleet task {self.spec.task_id}, tester-{i}")
        files = git(wt, "diff", "--name-only", self.base, "HEAD").split()
        # "red" = the test command narrowed to the new files fails; files the command already
        # names are left out of that run (Toolbox.narrowed).
        ok, _ = self.toolbox(wt).run_tests(files) if files else (True, "")
        red = None
        if files and all(is_test_path(f) for f in files) and not ok:
            red = {"files": files, "commit": head(wt)}
        elif files:  # not a test-only change, or it does not reproduce the bug
            git(wt, "reset", "--quiet", "--hard", self.base)
        saved.write_text(json.dumps(red))
        return red

    def review(self, i: int, rnd: int, wt: Path, sub: dict, red: dict | None = None) -> tuple[str, str]:
        ok, tests = self.toolbox(wt).run_tests()
        note = (f"\n\nThe regression test ({', '.join(red['files'])}) was written before the fix and "
                "failed on the base code; check that it tests the reported behaviour." if red else "")
        res = self.agent(f"reviewer-{i}" + ("" if rnd == 1 else f".r{rnd}"), REVIEWER, wt).run(
            f"Overall task:\n{self.spec.text}\n\nSubtask under review ({sub['title']}):\n"
            f"{sub['description']}\n\nDiff against base:\n```diff\n{diff(wt, self.base)}\n```\n\n"
            f"Test run ({'passing' if ok else 'FAILING'}):\n```\n{tests[-6000:]}\n```{note}")
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
        # Integration restarts from base on resume (merges are cheap and deterministic),
        # so integrator agents start fresh instead of resuming a stale checkpoint.
        return self.agent(f"integrator-{self._integrator_runs}", INTEGRATOR, wt, resume=False).run(
            f"Overall task:\n{self.spec.text}\n\n{problem}")
