import json
import os
import shutil
import subprocess
import sys

import pytest

from fleet import bench
from fleet.tools import git


NODE = shutil.which("node")
# node >= 22.18 runs .ts files directly (type stripping)
NODE_TS = bool(NODE) and subprocess.run([NODE, "-p", "process.features.typescript || ''"],
                                        capture_output=True, text=True).stdout.strip() != ""


def node_ok(case: str) -> bool:
    return NODE_TS if case.endswith("-ts") else bool(NODE)


@pytest.mark.parametrize("case", bench.case_names())
def test_every_case_starts_red(tmp_path, case):
    spec = json.loads((bench.CASES / case / "case.json").read_text())
    if spec["test_cmd"].startswith("node") and not node_ok(case):
        pytest.skip("node (>= 22.18 for TypeScript) is not installed")
    repo = bench.seed_repo(case, tmp_path / case)
    r = subprocess.run(spec["test_cmd"], shell=True, cwd=repo, capture_output=True,
                       env={**os.environ, "PATH": os.path.dirname(sys.executable) + os.pathsep + os.environ["PATH"]})
    assert r.returncode != 0, f"{case} has no failing test"


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_scripted_bench_fixes_the_node_cases(tmp_path):
    cases = [c for c in bench.case_names() if c.endswith(("-js", "-ts")) and node_ok(c)]
    results = bench.run(cases, "scripted", tmp_path, sandbox="subprocess")
    assert cases and all(r.passed for r in results), results


def test_scripted_bench_passes_and_reports(tmp_path):
    results = bench.run(["median", "bank"], "scripted", tmp_path, sandbox="subprocess")
    assert [r.case for r in results] == ["median", "bank"]
    assert all(r.passed and r.status == "done" for r in results), results
    bank = results[1]
    assert bank.turns >= 10 and bank.tokens > 0 and bank.cost > 0  # planner + 2 workers + 2 reviews
    out = bench.report(results, "scripted")
    assert "pass rate 2/2 (100%)" in out and "simulated" in out
    assert '"pass_rate": 1.0' in bench.as_json(results, "scripted")


def test_verify_rejects_edited_tests_and_missing_branch(tmp_path):
    repo = bench.seed_repo("paginate", tmp_path / "p")
    assert bench.verify(repo, None, "python -m unittest") == (False, "no branch produced")
    git(repo, "checkout", "-q", "-b", "cheat")
    (repo / "tests" / "test_pager.py").write_text("")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "delete tests")
    assert bench.verify(repo, "cheat", "python -m unittest") == (False, "tests were modified")


def test_verify_accepts_added_regression_tests(tmp_path):
    repo = bench.seed_repo("median", tmp_path / "m")
    git(repo, "checkout", "-q", "-b", "fix")
    stats = repo / "stats.py"
    stats.write_text(stats.read_text().replace(
        "    return s[mid]", "    if len(s) % 2 == 0:\n        return (s[mid - 1] + s[mid]) / 2\n    return s[mid]"))
    (repo / "tests" / "test_regression.py").write_text(
        "import unittest\nfrom stats import median\n\n\nclass R(unittest.TestCase):\n"
        "    def test_pair(self):\n        self.assertEqual(median([1, 2]), 1.5)\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "fix + regression test")
    assert bench.verify(repo, "fix", "python -m unittest") == (True, "")
