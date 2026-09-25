"""Pulls tasks off the queue and runs them with a concurrency limit and retries."""

from __future__ import annotations

import logging
import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from . import github
from .agent import Budget
from .models import ModelFactory
from .pipeline import Pipeline, TaskSpec
from .queue import Queue
from .workspace import parse_github

log = logging.getLogger("fleet")


def short_title(text: str, limit: int = 72) -> str:
    """Cut at a word boundary so PR titles don't end mid-word."""
    if len(text) <= limit:
        return text
    return text[:limit - 3].rsplit(" ", 1)[0].rstrip(":,;") + "..."


def spec_for(task: dict) -> TaskSpec:
    o = task["options"]
    return TaskSpec(
        task_id=task["id"], repo=task["repo"], text=task["text"],
        test_cmd=o.get("test_cmd", "python -m pytest -q"), sandbox=o.get("sandbox", "auto"),
        max_workers=o.get("max_workers", 3), review_rounds=o.get("review_rounds", 2),
        budget=Budget(max_turns=o.get("max_turns", 40), max_tokens=o.get("max_tokens", 3_000_000)),
    )


def models_for(task: dict) -> ModelFactory:
    o = task["options"]
    return ModelFactory(o.get("backend", "claude"), script=o.get("script"),
                        model=o.get("model", "claude-opus-5-5"), effort=o.get("effort", "high"))


def permanent(exc: Exception) -> bool:
    """Client-side API errors that will fail the same way on retry."""
    import anthropic
    return isinstance(exc, anthropic.APIStatusError) and exc.status_code < 500 \
        and exc.status_code not in (408, 409, 429)


class Orchestrator:
    def __init__(self, home: str | Path, queue: Queue, concurrency: int = 2):
        self.home = Path(home)
        self.queue = queue
        self.concurrency = max(1, concurrency)

    def run(self, drain: bool = True, poll: float = 2.0) -> None:
        """Process tasks; with drain=True return once the queue is empty."""
        if n := self.queue.requeue_stale():
            log.info("requeued %d stale task(s)", n)
        with ThreadPoolExecutor(self.concurrency) as pool:
            running = set()
            while True:
                while len(running) < self.concurrency and (task := self.queue.claim()):
                    log.info("task %s: starting (attempt %d)", task["id"], task["attempts"])
                    running.add(pool.submit(self.run_task, task))
                if not running and drain:
                    return
                done, running = wait(running, timeout=poll, return_when=FIRST_COMPLETED)

    def run_task(self, task: dict) -> None:
        tid = task["id"]
        try:
            result = Pipeline(spec_for(task), models_for(task), self.home,
                              lambda stage: self.queue.update(tid, stage=stage)).run()
        except Exception as e:
            if permanent(e):  # e.g. 400/401/403/404 from the API: retrying cannot help
                self.queue.update(tid, status="failed", error=traceback.format_exc())
                status = "failed"
            else:
                status = self.queue.fail(tid, traceback.format_exc())
            log.exception("task %s crashed; now %s", tid, status)
            return
        success = bool(result["branch"]) and result["tests_passed"]
        error, pr_url = "" if success else "no approved change passed the test suite", ""
        if success and task["options"].get("pr"):
            try:
                self.queue.update(tid, stage="opening PR")
                pr_url = self.open_pr(task, result)
            except Exception as e:  # the work is kept on the local branch; PR failures are final
                success, error = False, f"PR not opened: {e}"
        self.queue.update(tid, status="done" if success else "failed", stage="finished",
                          result=result, pr_url=pr_url, error=error)
        log.info("task %s: %s %s", tid, "done" if success else f"failed ({error})", pr_url)

    def open_pr(self, task: dict, result: dict) -> str:
        gh = parse_github(task["repo"])
        if gh is None:
            raise github.NotOwnedError("--pr requires a GitHub repo (owner/repo); local repos stay local")
        title = task["options"].get("title") or short_title(task["text"].strip().splitlines()[0])
        return github.open_pr(Path(result["repo"]), *gh, result["branch"], title,
                              github.pr_body(task, result))
