from fleet.agent import Budget
from fleet.models import ModelFactory
from fleet.pipeline import Pipeline, TaskSpec
from fleet.tools import git
from fleet.trajectory import read

from .conftest import make_repo

APPROVE = [[{"name": "finish", "input": {"verdict": "approve", "feedback": "lgtm"}}]]
ONE_PLAN = [[{"name": "list_dir", "input": {"path": "."}}],
            [{"name": "finish", "input": {"summary": "fix add", "subtasks": [
                {"title": "Fix add", "description": "add() in calc.py subtracts; make it add."}]}}]]


def run(repo, tmp_path, script):
    import json
    p = tmp_path / "script.json"
    p.write_text(json.dumps(script))
    spec = TaskSpec("t1", str(repo), "Fix the failing test", test_cmd="python3 -m unittest -q",
                    sandbox="subprocess", budget=Budget(max_turns=10))
    stages = []
    result = Pipeline(spec, ModelFactory("scripted", script=p), tmp_path / "home", stages.append).run()
    return result, stages


def test_single_worker_happy_path(repo, tmp_path):
    result, stages = run(repo, tmp_path, {
        "planner": ONE_PLAN,
        "worker-1": [[{"name": "apply_patch", "input": {"path": "calc.py", "old": "a - b", "new": "a + b"}}],
                     [{"name": "run_tests", "input": {}}],
                     [{"name": "finish", "input": {"summary": "fixed", "tests_passed": True}}]],
        "reviewer": APPROVE,
    })
    assert result["tests_passed"] and result["branch"] == "fleet/t1"
    assert result["merged"] == ["fleet/t1-w1"] and result["subtasks"][0]["verdict"] == "approve"
    assert "a + b" in git(repo, "show", "fleet/t1:calc.py")
    assert "return a - b" in (repo / "calc.py").read_text()  # user checkout untouched
    assert stages[0] == "preparing" and "integrating" in stages
    assert not (tmp_path / "home" / "worktrees" / "t1" / "worker-1").exists()  # cleaned up
    assert git(repo, "branch", "--list", "fleet/*").split() == ["fleet/t1"]  # merged branch deleted
    runs = tmp_path / "home" / "runs" / "t1"
    assert {p.stem for p in runs.glob("*.jsonl")} == {"planner", "worker-1", "reviewer-1"}
    assert not list(runs.glob("*.ckpt.json"))  # checkpoints are dropped once an agent ends
    assert read(runs / "worker-1.jsonl")[-1]["status"] == "finished"


def test_reviewer_requests_changes_then_approves(repo, tmp_path):
    result, _ = run(repo, tmp_path, {
        "planner": ONE_PLAN,
        "worker-1": [[{"name": "apply_patch", "input": {"path": "calc.py", "old": "a - b", "new": "abs(a - b)"}}]],
        "reviewer-1": [[{"name": "finish", "input": {"verdict": "request_changes",
                                                     "feedback": "still subtracts"}}]],
        "worker-1.r2": [[{"name": "apply_patch", "input": {"path": "calc.py", "old": "abs(a - b)", "new": "a + b"}}]],
        "reviewer-1.r2": APPROVE,
    })
    rounds = result["subtasks"][0]["rounds"]
    assert [r["agent"] for r in rounds] == ["worker-1", "worker-1.r2"]
    assert result["tests_passed"] and "a + b" in git(repo, "show", "fleet/t1:calc.py")


def test_rejected_work_is_not_merged(repo, tmp_path):
    result, _ = run(repo, tmp_path, {
        "planner": ONE_PLAN,
        "worker": [[{"name": "write_file", "input": {"path": "junk.txt", "content": "x"}}]],
        "reviewer": [[{"name": "finish", "input": {"verdict": "request_changes", "feedback": "no"}}]],
    })
    assert result["branch"] is None and result["skipped"] == ["fleet/t1-w1"]
    assert "fleet/t1-w1" in git(repo, "branch", "--list", "fleet/*")  # kept for inspection
    assert len(result["subtasks"][0]["rounds"]) == 3  # first try + 2 revisions


def test_conflicting_workers_resolved_by_integrator(repo, tmp_path):
    fixed = "def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a * b\n"
    result, _ = run(repo, tmp_path, {
        "planner": [[{"name": "finish", "input": {"summary": "two", "subtasks": [
            {"title": "A", "description": "fix add one way"},
            {"title": "B", "description": "fix add another way"}]}}]],
        "worker-1": [[{"name": "apply_patch", "input": {"path": "calc.py", "old": "a - b", "new": "a + b"}}]],
        "worker-2": [[{"name": "apply_patch", "input": {"path": "calc.py", "old": "a - b", "new": "b + a"}}]],
        "reviewer": APPROVE,
        "integrator": [[{"name": "write_file", "input": {"path": "calc.py", "content": fixed}}],
                       [{"name": "finish", "input": {"summary": "resolved", "tests_passed": True}}]],
    })
    assert result["merged"] == ["fleet/t1-w1", "fleet/t1-w2"]
    assert result["tests_passed"]
    assert git(repo, "show", "fleet/t1:calc.py") == fixed


def test_unresolved_conflict_is_aborted(repo, tmp_path):
    result, _ = run(repo, tmp_path, {
        "planner": [[{"name": "finish", "input": {"summary": "two", "subtasks": [
            {"title": "A", "description": "a"}, {"title": "B", "description": "b"}]}}]],
        "worker-1": [[{"name": "apply_patch", "input": {"path": "calc.py", "old": "a - b", "new": "a + b"}}]],
        "worker-2": [[{"name": "apply_patch", "input": {"path": "calc.py", "old": "a - b", "new": "b + a"}}]],
        "reviewer": APPROVE,
        "integrator": [],  # gives up immediately
    })
    assert result["merged"] == ["fleet/t1-w1"] and result["skipped"] == ["fleet/t1-w2"]
    assert result["tests_passed"]


def test_planner_without_subtasks_falls_back_to_whole_task(repo, tmp_path):
    result, _ = run(repo, tmp_path, {
        "planner": [],
        "worker": [[{"name": "apply_patch", "input": {"path": "calc.py", "old": "a - b", "new": "a + b"}}]],
        "reviewer": APPROVE,
    })
    assert result["subtasks"][0]["title"] == "Complete the task" and result["tests_passed"]


def test_task_token_budget_is_shared_and_reported(repo, tmp_path):
    import json
    p = tmp_path / "script.json"
    p.write_text(json.dumps({"planner": ONE_PLAN, "worker": [[{"name": "list_dir", "input": {"path": "."}}]] * 20}))
    spec = TaskSpec("t1", str(repo), "x", test_cmd="python3 -m unittest -q", sandbox="subprocess",
                    budget=Budget(max_turns=20), task_tokens=3000)
    result = Pipeline(spec, ModelFactory("scripted", script=p), tmp_path / "home").run()
    tok = result["tokens"]
    assert set(tok["by_agent"]) >= {"planner", "worker-1"}
    assert tok["total"] == sum(tok["by_agent"].values())
    end = read(tmp_path / "home" / "runs" / "t1" / "worker-1.jsonl")[-1]
    assert end["status"] == "budget_exhausted" and end["reason"] == "task token budget"


def test_cost_budget_is_a_hard_stop(repo, tmp_path):
    import json
    p = tmp_path / "script.json"
    p.write_text(json.dumps({"planner": ONE_PLAN, "worker": [[{"name": "list_dir", "input": {"path": "."}}]] * 20}))
    spec = TaskSpec("t1", str(repo), "x", test_cmd="python3 -m unittest -q", sandbox="subprocess",
                    budget=Budget(max_turns=20), max_cost=0.01)
    result = Pipeline(spec, ModelFactory("scripted", script=p), tmp_path / "home").run()
    tok = result["tokens"]
    assert tok["exhausted"] and tok["max_cost_usd"] == 0.01
    assert 0.01 <= tok["cost_usd"] < 0.02  # stops within one turn of the limit
    assert abs(tok["cost_usd"] - sum(tok["cost_by_agent"].values())) < 1e-5
    end = read(tmp_path / "home" / "runs" / "t1" / "worker-1.jsonl")[-1]
    assert end["status"] == "budget_exhausted" and end["reason"] == "task cost budget"
    assert end["turns"] < 20 and end["cost"] > 0


REGRESSION = ("import unittest\nfrom calc import add\n\n\nclass Regression(unittest.TestCase):\n"
              "    def test_add_negative(self):\n        self.assertEqual(add(-2, 5), 3)\n")
FIX = [[{"name": "apply_patch", "input": {"path": "calc.py", "old": "a - b", "new": "a + b"}}]]


def run_test_first(repo, tmp_path, tester):
    import json
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "script.json"
    p.write_text(json.dumps({"planner": ONE_PLAN, "tester": tester, "worker": FIX, "reviewer": APPROVE}))
    spec = TaskSpec("t1", str(repo), "Fix the failing test", test_cmd="python3 -m unittest -q",
                    sandbox="subprocess", budget=Budget(max_turns=10), test_first=True)
    return Pipeline(spec, ModelFactory("scripted", script=p), tmp_path / "home").run()


def test_test_first_commits_a_red_test_before_the_fix(repo, tmp_path):
    result = run_test_first(repo, tmp_path, [
        [{"name": "write_file", "input": {"path": "test_regression.py", "content": REGRESSION}}],
        [{"name": "run_tests", "input": {}}],
        [{"name": "finish", "input": {"summary": "add(-2, 5) must be 3", "test_files": ["test_regression.py"]}}]])
    red = result["subtasks"][0]["regression_test"]
    assert red["files"] == ["test_regression.py"]
    assert result["tests_passed"] and "Regression" in git(repo, "show", "fleet/t1:test_regression.py")
    # the test commit comes before the fix on the worker branch
    log = git(repo, "log", "--format=%s", "fleet/t1").splitlines()
    assert log.index("Fix add") < log.index("Add regression test: Fix add")
    worker = read(tmp_path / "home" / "runs" / "t1" / "worker-1.jsonl")[0]
    assert "test_regression.py" in worker["task"] and "without changing it" in worker["task"]
    reviewer = read(tmp_path / "home" / "runs" / "t1" / "reviewer-1.jsonl")[0]
    assert "regression test (test_regression.py) was written before the fix" in reviewer["task"]


def test_test_first_drops_a_test_that_passes_or_touches_code(repo, tmp_path):
    passing = REGRESSION.replace("add(-2, 5), 3", "2 + 2, 4")
    result = run_test_first(repo, tmp_path, [
        [{"name": "write_file", "input": {"path": "test_regression.py", "content": passing}}]])
    assert result["subtasks"][0]["regression_test"] is None and result["tests_passed"]
    assert "test_regression.py" not in git(repo, "ls-tree", "--name-only", "fleet/t1")

    result = run_test_first(make_repo(tmp_path / "r2"), tmp_path / "second", [
        [{"name": "write_file", "input": {"path": "test_regression.py", "content": REGRESSION}},
         {"name": "apply_patch", "input": {"path": "calc.py", "old": "a * b", "new": "a * b  # touched"}}]])
    assert result["subtasks"][0]["regression_test"] is None  # edited non-test code: dropped


def test_is_test_path():
    from fleet.pipeline import is_test_path
    for p in ("tests/test_x.py", "test_calc.py", "pkg/foo_test.go", "src/a.test.ts", "web/__tests__/a.js",
              "spec/models/user_spec.rb"):
        assert is_test_path(p), p
    for p in ("calc.py", "src/latest.ts", "contest/solve.py", "attestation.py"):
        assert not is_test_path(p), p
