"""Repositories and per-agent git worktrees.

A task's repo is either a local git repository (used in place; the user's
own checkout is never modified - all work happens in separate worktrees on
`fleet/*` branches) or a GitHub repo, cloned once into the fleet cache.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from .tools import git

GITHUB = re.compile(r"^(?:https://github\.com/)?([\w.-]+)/([\w.-]+?)(?:\.git)?/?$")


def parse_github(source: str) -> tuple[str, str] | None:
    """'owner/repo' or a github https URL -> (owner, repo); local paths -> None."""
    if Path(source).expanduser().exists():
        return None
    m = GITHUB.match(source)
    return (m.group(1), m.group(2)) if m else None


def prepare_repo(source: str, cache: Path) -> Path:
    """Return a local git repo for `source`, cloning/fetching GitHub repos into `cache`."""
    gh = parse_github(source)
    if gh is None:
        path = Path(source).expanduser().resolve()
        if not (path / ".git").exists():
            raise ValueError(f"{path} is not a git repository")
        return path
    owner, name = gh
    dest = cache / "repos" / f"{owner}__{name}"
    if (dest / ".git").exists():
        git(dest, "fetch", "--quiet", "origin")
        git(dest, "reset", "--quiet", "--hard", "origin/HEAD")
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        git(dest.parent, "clone", "--quiet", f"https://github.com/{owner}/{name}.git", dest.name)
    return dest


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def add_worktree(repo: Path, path: Path, branch: str | None, base: str) -> Path:
    """Check out `base` into `path` on a fresh `branch` (detached if branch is None)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        remove_worktree(repo, path)
    target = ["-B", branch] if branch else ["--detach"]
    git(repo, "worktree", "add", "--quiet", *target, str(path), base)
    return path


def remove_worktree(repo: Path, path: Path) -> None:
    git(repo, "worktree", "remove", "--force", str(path), check=False)
    shutil.rmtree(path, ignore_errors=True)
    git(repo, "worktree", "prune", check=False)


def commit_all(worktree: Path, message: str) -> bool:
    """Commit every change in the worktree; returns False if there was nothing to commit."""
    git(worktree, "add", "-A")
    if not git(worktree, "status", "--porcelain").strip():
        return False
    git(worktree, *ident(worktree), "commit", "--quiet", "--no-verify", "-m", message)
    return True


def ident(repo: Path) -> list[str]:
    """Use the user's git identity; fall back to a bot identity when none is configured."""
    if git(repo, "config", "user.email", check=False).strip():
        return []
    return ["-c", "user.name=agent-fleet", "-c", "user.email=agent-fleet@users.noreply.github.com"]
