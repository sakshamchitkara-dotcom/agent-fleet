from fleet.agent import Agent, Budget, validate
from fleet.models import Reply, ScriptedModel
from fleet.roles import READ_ONLY, WORKER, Role
from fleet.tools import Toolbox
from fleet.trajectory import Trajectory, read

FINISH = {"type": "object", "properties": {"summary": {"type": "string"}},
          "required": ["summary"], "additionalProperties": False}
ROLE = Role("worker", "sys", WORKER.tools, "done", FINISH)


def make_agent(repo, tmp_path, script, budget=None, model=None, role=ROLE):
    tb = Toolbox(repo, test_cmd="python3 -m unittest -q")
    model = model or ScriptedModel(script, "worker-1")
    traj = Trajectory(tmp_path / "t.jsonl", "worker-1")
    return Agent("worker-1", role, model, tb, traj, budget), tmp_path / "t.jsonl"


FIX = {"worker": [
    [{"name": "run_tests", "input": {}}],
    [{"name": "apply_patch", "input": {"path": "calc.py", "old": "a - b", "new": "a + b"}}],
    [{"name": "run_tests", "input": {}}],
    [{"name": "finish", "input": {"summary": "fixed add"}}],
]}


def test_agent_fixes_bug_and_logs_every_step(repo, tmp_path):
    agent, log = make_agent(repo, tmp_path, FIX)
    res = agent.run("fix the failing test")
    assert res.ok and res.output == {"summary": "fixed add"} and res.turns == 4
    assert "a + b" in (repo / "calc.py").read_text()
    events = [r["event"] for r in read(log)]
    assert events[0] == "start" and events[-1] == "end"
    assert events.count("model") == 4 and events.count("tool") == 4


def test_bad_inputs_become_error_results(repo, tmp_path):
    script = {"worker": [
        [{"name": "read_file", "input": {}}],
        [{"name": "nope", "input": {}}],
        [{"name": "read_file", "input": {"path": "../../etc/passwd"}}],
        [{"name": "finish", "input": {}}],  # invalid finish is rejected, not accepted
    ]}
    agent, log = make_agent(repo, tmp_path, script, Budget(max_turns=4))
    res = agent.run("x")
    assert res.status == "budget_exhausted"
    tools = [r for r in read(log) if r["event"] == "tool"]
    assert all(t["is_error"] for t in tools)
    assert "missing required field 'path'" in tools[0]["output"]


def test_turn_budget(repo, tmp_path):
    script = {"worker": [[{"name": "list_dir", "input": {"path": "."}}]] * 10}
    agent, _ = make_agent(repo, tmp_path, script, Budget(max_turns=3))
    res = agent.run("x")
    assert res.status == "budget_exhausted" and res.turns == 3


def test_token_budget(repo, tmp_path):
    script = {"worker": [[{"name": "list_dir", "input": {"path": "."}}]] * 10}
    agent, _ = make_agent(repo, tmp_path, script, Budget(max_tokens=1))
    res = agent.run("x")
    assert res.status == "budget_exhausted" and res.turns == 1


def test_compaction_restarts_with_summary(repo, tmp_path):
    seen = []

    class Spy(ScriptedModel):
        def complete(self, system, messages, tools):
            seen.append(list(messages))
            return super().complete(system, messages, tools)

    model = Spy({"worker": [[{"name": "list_dir", "input": {"path": "."}}]] * 3}, "worker-1")
    agent, log = make_agent(repo, tmp_path, None, Budget(compact_at=1), model=model)
    agent.run("the task")
    assert model.summaries >= 1
    # after compaction the next request starts from a single fresh user message
    assert len(seen[1]) == 1 and "the task" in seen[1][0]["content"]
    assert "scripted summary" in seen[1][0]["content"]
    assert any(r["event"] == "compact" for r in read(log))


def test_truncated_tool_calls_not_executed(repo, tmp_path):
    class Truncating:
        def __init__(self):
            self.n = 0

        def complete(self, s, m, t):
            self.n += 1
            if self.n == 1:
                return Reply([{"type": "tool_use", "id": "a", "name": "write_file",
                               "input": {"path": "x.py", "content": "partial"}}], "max_tokens")
            return Reply([{"type": "tool_use", "id": "b", "name": "finish",
                           "input": {"summary": "ok"}}], "tool_use")

    agent, _ = make_agent(repo, tmp_path, None, model=Truncating())
    assert agent.run("x").ok
    assert not (repo / "x.py").exists()


def test_refusal_stops(repo, tmp_path):
    class Refuser:
        def complete(self, s, m, t):
            return Reply([], "refusal")

    agent, _ = make_agent(repo, tmp_path, None, model=Refuser())
    assert agent.run("x").status == "refused"


def test_validate():
    s = {"properties": {"a": {"type": "integer"}, "b": {"type": "string"}}, "required": ["b"]}
    assert validate({"b": "x", "a": 1}, s) is None
    assert validate({"b": "x", "a": True}, s)
    assert validate({"b": 1}, s)
    assert validate({"b": "x", "c": 1}, s)
    assert validate("nope", s)


def test_role_tool_allowlist_enforced(repo, tmp_path):
    ro = Role("reviewer", "sys", READ_ONLY, "done", FINISH)
    script = {"worker": [[{"name": "write_file", "input": {"path": "x.py", "content": "x"}}]]}
    agent, log = make_agent(repo, tmp_path, script, Budget(max_turns=1), role=ro)
    agent.run("x")
    assert not (repo / "x.py").exists()
    assert "unknown tool" in [r for r in read(log) if r["event"] == "tool"][0]["output"]
