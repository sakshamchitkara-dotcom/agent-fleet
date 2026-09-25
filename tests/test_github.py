import json

import pytest

from fleet.github import (NotOwnedError, assert_owned, fetch_issue, issue_task_text, open_pr,
                          parse_issue_ref, pr_body)


def fake_gh(login="me", owner="me", admin=True, calls=None):
    def run(*args):
        if calls is not None:
            calls.append(args)
        if args[:2] == ("api", "user"):
            return login + "\n"
        if args[0] == "api" and args[1].startswith("repos/") and "/issues/" in args[1]:
            return json.dumps({"title": "Bug", "body": "add() is wrong", "html_url": "https://x/1"})
        if args[0] == "api":
            return json.dumps({"owner": {"login": owner}, "permissions": {"admin": admin},
                               "default_branch": "main"})
        if args[0] == "pr":
            return "https://github.com/me/r/pull/1\n"
        raise AssertionError(args)
    return run


def test_parse_issue_ref():
    assert parse_issue_ref("me/repo#12") == ("me", "repo", 12)
    with pytest.raises(ValueError):
        parse_issue_ref("me/repo")


def test_fetch_issue_and_text():
    issue = fetch_issue("me", "r", 1, run=fake_gh())
    assert issue["title"] == "Bug"
    assert "me/r#1: Bug" in issue_task_text("me", "r", 1, issue)


def test_ownership_guard():
    assert assert_owned("me", "r", run=fake_gh()) == "main"
    with pytest.raises(NotOwnedError):
        assert_owned("someone-else", "r", run=fake_gh())
    with pytest.raises(NotOwnedError):
        assert_owned("me", "r", run=fake_gh(owner="org"))
    with pytest.raises(NotOwnedError):
        assert_owned("me", "r", run=fake_gh(admin=False))


def test_open_pr_refuses_foreign_repo_before_pushing(repo):
    calls = []
    with pytest.raises(NotOwnedError):
        open_pr(repo, "torvalds", "linux", "fleet/x", "t", "b", run=fake_gh(calls=calls))
    assert not any(c[0] == "pr" for c in calls)


def test_open_pr_refuses_mismatched_origin(repo):
    import subprocess
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/other/r.git"], cwd=repo, check=True)
    with pytest.raises(NotOwnedError):
        open_pr(repo, "me", "r", "fleet/x", "t", "b", run=fake_gh())


def test_pr_body():
    body = pr_body({"id": "abc", "text": "Fix add"}, {
        "plan": "one step", "diffstat": " calc.py | 2 +-", "tests_passed": True, "test_output": "OK",
        "subtasks": [{"title": "Fix add", "verdict": "approve", "rounds": [{"summary": "fixed"}]}]})
    assert "PASSING" in body and "calc.py | 2" in body and "`approve`" in body and "`abc`" in body


def test_pr_body_links_issue_and_title_cut_at_word():
    from fleet.orchestrator import short_title
    body = pr_body({"id": "abc", "text": "x", "options": {"issue": 7}}, {"subtasks": []})
    assert body.endswith("Closes #7")
    t = short_title("Fix #1: Discounts are inverted: apply_discount returns the discount instead of the price")
    assert len(t) <= 72 and t.endswith("...") and not t.endswith(" ...")
    assert t == "Fix #1: Discounts are inverted: apply_discount returns the discount..."
