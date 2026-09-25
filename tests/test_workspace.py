import pytest

from fleet.tools import git
from fleet.workspace import add_worktree, commit_all, head, parse_github, prepare_repo, remove_worktree


def test_parse_github(tmp_path):
    assert parse_github("octo/hello-world") == ("octo", "hello-world")
    assert parse_github("https://github.com/octo/hello.git") == ("octo", "hello")
    assert parse_github(str(tmp_path)) is None
    assert parse_github("not a repo") is None


def test_local_repo_used_in_place(repo, tmp_path):
    assert prepare_repo(str(repo), tmp_path) == repo.resolve()
    with pytest.raises(ValueError):
        prepare_repo(str(tmp_path), tmp_path)


def test_worktree_isolation_and_commit(repo, tmp_path):
    base = head(repo)
    wt = add_worktree(repo, tmp_path / "wt" / "w1", "fleet/t1-w1", base)
    (wt / "calc.py").write_text("changed\n")
    assert commit_all(wt, "worker change")
    assert not commit_all(wt, "nothing")
    # the user's checkout is untouched; the change lives on the branch
    assert "return a - b" in (repo / "calc.py").read_text()
    assert git(repo, "show", "fleet/t1-w1:calc.py") == "changed\n"
    remove_worktree(repo, wt)
    assert not wt.exists()
    assert "fleet/t1-w1" in git(repo, "branch", "--list", "fleet/*")
