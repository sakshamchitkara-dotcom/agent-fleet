"""End-to-end: the scripted fleet fixes the bundled sample repo."""

import shutil
import subprocess
from pathlib import Path

from fleet.cli import main
from fleet.tools import git

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_sample_repo_fixed_by_two_parallel_workers(tmp_path, capsys):
    repo = tmp_path / "shop"
    shutil.copytree(EXAMPLES / "sample-repo", repo)
    for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init"]):
        subprocess.run(["git", *args], cwd=repo, check=True)
    home = str(tmp_path / "home")
    main(["--home", home, "run", str(repo), "Fix the failing tests", "--backend", "scripted",
          "--script", str(EXAMPLES / "scripts" / "sample-fix.json"), "--sandbox", "subprocess",
          "--test-cmd", "python -m unittest -v"])
    out = capsys.readouterr().out
    assert "tests: PASS" in out and out.count("[approve]") == 2
    tid = out.split("task ")[1].split()[0]
    branch = f"fleet/{tid}"
    merges = git(repo, "log", "--merges", "--format=%s", f"main..{branch}")
    assert f"Merge {branch}-w1: Fix apply_discount" in merges and f"{branch}-w2" in merges
    ok = subprocess.run(["git", "checkout", "-q", branch], cwd=repo)
    assert ok.returncode == 0
    assert subprocess.run(["python3", "-m", "unittest", "-q"], cwd=repo).returncode == 0
