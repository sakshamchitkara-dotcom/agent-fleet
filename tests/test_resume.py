"""Tasks survive an orchestrator restart: agents resume from their last checkpoint."""
import json

import pytest

from fleet.agent import Agent, Budget
from fleet.models import ModelFactory, ScriptedModel
from fleet.orchestrator import Orchestrator
from fleet.pipeline import Pipeline, TaskSpec
from fleet.queue import Queue
from fleet.roles import WORKER
from fleet.tools import Toolbox, git
from fleet.trajectory import Trajectory, read

WORKER_SCRIPT = [
    [{"name": "read_file", "input": {"path": "calc.py"}}],
    [{"name": "apply_patch", "input": {"path": "calc.py", "old": "a - b", "new": "a + b"}}],
    [{"name": "run_tests", "input": {}}],
    [{"name": "finish", "input": {"summary": "fixed", "tests_passed": True}}],
]
SCRIPT = {
    "planner": [[{"name": "finish", "input": {"summary": "s", "subtasks": [
        {"title": "Fix add", "description": "make add() add"}]}}]],
    "worker-1": WORKER_SCRIPT,
    "reviewer": [[{"name": "finish", "input": {"verdict": "approve", "feedback": "ok"}}]],
}


class Killed(BaseException):
    """Stands in for the process dying (not an Exception, so nothing catches it)."""


class DiesAfter(ScriptedModel):
    def __init__(self, script, agent, turns):
        super().__init__(script, agent)
        self.left = turns

    def complete(self, *a):
        if self.left == 0:
            raise Killed()
        self.left -= 1
        return super().complete(*a)


def test_agent_resumes_from_checkpoint(repo, tmp_path):
    traj = tmp_path / "worker-1.jsonl"

    def agent(model):
        return Agent("worker-1", WORKER, model, Toolbox(repo, test_cmd="python3 -m unittest -q"),
                     Trajectory(traj, "worker-1"), Budget(max_turns=10))

    with pytest.raises(Killed):
        agent(DiesAfter({"worker": WORKER_SCRIPT}, "worker-1", 2)).run("fix add")
    ckpt = json.loads(traj.with_suffix(".ckpt.json").read_text())
    assert ckpt["turn"] == 2 and ckpt["messages"][-1]["content"][0]["content"] == "patched calc.py"

    model = ScriptedModel({"worker": WORKER_SCRIPT}, "worker-1")
    res = agent(model).run("fix add")
    assert res.ok and res.turns == 4 and model.cursor == 4  # only turns 3-4 were generated
    events = read(traj)
    assert [e["event"] for e in events].count("start") == 1
    resume = next(e for e in events if e["event"] == "resume")
    assert resume["turn"] == 2 and resume["tokens"] > 0
    assert [e["turn"] for e in events if e["event"] == "model"] == [1, 2, 3, 4]
    assert not traj.with_suffix(".ckpt.json").exists()

    again = agent(ScriptedModel({}, "worker-1"))  # already finished: replayed, not re-run
    assert again.run("fix add").output == {"summary": "fixed", "tests_passed": True}
    assert again.meter.used == res.tokens


def test_restarted_orchestrator_resumes_interrupted_task(repo, tmp_path, monkeypatch):
    script = tmp_path / "s.json"
    script.write_text(json.dumps(SCRIPT))
    q = Queue(tmp_path / "q.db")
    home = tmp_path / "home"
    tid = q.submit(str(repo), "fix add", backend="scripted", script=str(script),
                   sandbox="subprocess", test_cmd="python3 -m unittest -q")

    real = ModelFactory.__call__

    def dying(self, agent):  # first run: worker-1 dies after patching, mid-task
        m = real(self, agent)
        return DiesAfter(self._script, agent, 2) if agent == "worker-1" else m

    monkeypatch.setattr(ModelFactory, "__call__", dying)
    task = q.claim()
    with pytest.raises(Killed):
        Orchestrator(home, q).run_task(task)
    assert q.get(tid)["status"] == "running"  # the process "died" holding the task
    wt = home / "worktrees" / tid / "worker-1"
    assert "a + b" in (wt / "calc.py").read_text()  # uncommitted edit survives

    monkeypatch.setattr(ModelFactory, "__call__", real)
    Orchestrator(home, q).run(drain=True, poll=0.05)  # restart: requeue_stale + resume
    t = q.get(tid)
    assert t["status"] == "done", t["error"]
    assert t["attempts"] == 1  # the interrupted attempt does not count
    assert "a + b" in git(repo, "show", f"fleet/{tid}:calc.py")
    events = read(home / "runs" / tid / "worker-1.jsonl")
    assert [e["event"] for e in events].count("resume") == 1
    assert [e["turn"] for e in events if e["event"] == "model"] == [1, 2, 3, 4]
    planner = read(home / "runs" / tid / "planner.jsonl")
    assert [e["event"] for e in planner].count("start") == 1  # finished agents are not re-run
    assert not wt.exists()


def test_pipeline_rerun_after_crash_keeps_base(repo, tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(SCRIPT))
    spec = TaskSpec("t1", str(repo), "fix", test_cmd="python3 -m unittest -q", sandbox="subprocess")
    first = Pipeline(spec, ModelFactory("scripted", script=p), tmp_path / "home").run()
    (repo / "new.txt").write_text("x")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "moved on")
    again = Pipeline(spec, ModelFactory("scripted", script=p), tmp_path / "home").run()
    assert again["base"] == first["base"]  # a resumed task stays on the base it started from
