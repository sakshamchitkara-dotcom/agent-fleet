"""GitHub integration via the `gh` CLI: read issues, open PRs on owned repos only."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .tools import git

ISSUE_REF = re.compile(r"^([\w.-]+)/([\w.-]+)#(\d+)$")


class NotOwnedError(PermissionError):
    pass


def gh(*args: str) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {r.stderr.strip()}")
    return r.stdout


def parse_issue_ref(ref: str) -> tuple[str, str, int]:
    m = ISSUE_REF.match(ref.strip())
    if not m:
        raise ValueError(f"expected owner/repo#N, got {ref!r}")
    return m.group(1), m.group(2), int(m.group(3))


def fetch_issue(owner: str, repo: str, number: int, run=gh) -> dict:
    data = json.loads(run("api", f"repos/{owner}/{repo}/issues/{number}"))
    if "pull_request" in data:
        raise ValueError(f"{owner}/{repo}#{number} is a pull request, not an issue")
    return {"title": data["title"], "body": data.get("body") or "", "url": data["html_url"]}


def issue_task_text(owner: str, repo: str, number: int, issue: dict) -> str:
    return (f"Resolve GitHub issue {owner}/{repo}#{number}: {issue['title']}\n\n"
            f"{issue['body']}\n\n(Issue URL: {issue['url']})")


def assert_owned(owner: str, repo: str, run=gh) -> str:
    """Raise NotOwnedError unless the authenticated user owns and administers owner/repo."""
    login = run("api", "user", "--jq", ".login").strip()
    info = json.loads(run("api", f"repos/{owner}/{repo}"))
    if login.lower() != owner.lower() or info["owner"]["login"].lower() != login.lower() \
            or not info.get("permissions", {}).get("admin"):
        raise NotOwnedError(f"{owner}/{repo} is not owned by the authenticated user {login!r}; "
                            "refusing to push or open a PR")
    return info["default_branch"]


def pr_body(task: dict, result: dict) -> str:
    lines = ["## Summary", "", task["text"].strip()[:2000], ""]
    if result.get("plan"):
        lines += ["## Plan", "", result["plan"], ""]
    lines += ["## Subtasks", ""]
    for s in result.get("subtasks", []):
        last = s["rounds"][-1]["summary"] if s.get("rounds") else ""
        lines.append(f"- **{s['title']}** - review: `{s['verdict']}`, "
                     f"{len(s.get('rounds', []))} round(s). {last}".rstrip())
    lines += ["", "## Changes", "", "```", result.get("diffstat", ""), "```", "",
              f"## Tests: {'PASSING' if result.get('tests_passed') else 'FAILING'}", "",
              "```", result.get("test_output", "")[-2500:].strip(), "```", "",
              f"_Opened by agent-fleet (task `{task['id']}`)._"]
    if task.get("options", {}).get("issue"):
        lines += ["", f"Closes #{task['options']['issue']}"]
    return "\n".join(lines)


def open_pr(repo_path: Path, owner: str, repo: str, branch: str, title: str, body: str, run=gh) -> str:
    base = assert_owned(owner, repo, run)
    origin = git(repo_path, "remote", "get-url", "origin").strip().removesuffix(".git")
    if origin.lower() != f"https://github.com/{owner}/{repo}".lower():
        raise NotOwnedError(f"origin {origin!r} does not match {owner}/{repo}")
    git(repo_path, "push", "--quiet", "-u", "origin", f"{branch}:{branch}")  # never --force
    return run("pr", "create", "--repo", f"{owner}/{repo}", "--head", branch, "--base", base,
               "--title", title, "--body", body).strip()
