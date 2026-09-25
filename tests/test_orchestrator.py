import json

from fleet.orchestrator import Orchestrator
from fleet.queue import Queue

FIX_SCRIPT = {
    "planner": [[{"name": "finish", "input": {"summary": "s", "subtasks": [
        {"title": "Fix add", "description": "make add() add"}]}}]],
    "worker": [[{"name": "apply_patch", "input": {"path": "calc.py", "old": "a - b", "new": "a + b"}}]],
    "reviewer": [[{"name": "finish", "input": {"verdict": "approve", "feedback": "ok"}}]],
}


def setup(tmp_path, script=FIX_SCRIPT):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(script))
    return Queue(tmp_path / "q.db"), dict(backend="scripted", script=str(p), sandbox="subprocess",
                                          test_cmd="python3 -m unittest -q")


def test_drains_queue_concurrently(repo, tmp_path):
    q, opts = setup(tmp_path)
    ids = [q.submit(str(repo), "fix add", **opts) for _ in range(3)]
    Orchestrator(tmp_path / "home", q, concurrency=2).run(drain=True, poll=0.05)
    for tid in ids:
        t = q.get(tid)
        assert t["status"] == "done", t["error"]
        assert t["result"]["branch"] == f"fleet/{tid}" and t["stage"] == "finished"


def test_crash_is_retried_then_failed(tmp_path):
    q, opts = setup(tmp_path)
    tid = q.submit(str(tmp_path / "missing"), "x", max_attempts=2, **opts)
    Orchestrator(tmp_path / "home", q).run(drain=True, poll=0.05)
    t = q.get(tid)
    assert t["status"] == "failed" and t["attempts"] == 2 and "not a git repository" in t["error"]


def test_pr_on_local_repo_is_refused(repo, tmp_path):
    q, opts = setup(tmp_path)
    tid = q.submit(str(repo), "fix add", pr=True, **opts)
    Orchestrator(tmp_path / "home", q).run(drain=True, poll=0.05)
    t = q.get(tid)
    assert t["status"] == "failed" and "local repos stay local" in t["error"]
    assert t["result"]["tests_passed"] and t["pr_url"] == ""  # work kept on the branch


def test_permanent_api_errors_not_retried():
    import anthropic
    import httpx2 as httpx

    from fleet.orchestrator import permanent

    def err(cls, code):
        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        return cls("x", response=httpx.Response(code, request=req), body=None)

    assert permanent(err(anthropic.AuthenticationError, 401))
    assert permanent(err(anthropic.BadRequestError, 400))
    assert not permanent(err(anthropic.RateLimitError, 429))
    assert not permanent(err(anthropic.InternalServerError, 500))
    assert not permanent(RuntimeError("git failed"))


def test_spec_for_maps_options():
    from fleet.orchestrator import spec_for
    spec = spec_for({"id": "abc", "repo": "o/r", "text": "t", "options": {
        "image": "myproj:test", "sandbox": "docker", "max_turns": 7, "task_tokens": 1000}})
    assert (spec.image, spec.sandbox, spec.budget.max_turns, spec.task_tokens) == ("myproj:test", "docker", 7, 1000)


def test_budget_exhaustion_is_the_reported_error(repo, tmp_path):
    q, opts = setup(tmp_path)
    tid = q.submit(str(repo), "fix add", max_cost=0.000001, **opts)
    Orchestrator(tmp_path / "home", q).run(drain=True, poll=0.05)
    t = q.get(tid)
    assert t["status"] == "failed" and t["error"].startswith("task budget exhausted")
    assert "of $0.00" in t["error"]
