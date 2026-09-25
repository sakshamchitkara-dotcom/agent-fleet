"""`fleet` command line."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from . import github
from .orchestrator import Orchestrator, short_title
from .queue import Queue
from .sandbox import DEFAULT_IMAGE
from .pricing import usd
from .trajectory import isolation, read, spend
from .workspace import parse_github


def home_dir(args) -> Path:
    return Path(args.home or os.environ.get("FLEET_HOME") or Path.home() / ".fleet").expanduser()


def task_options(args) -> dict:
    opts = {k: getattr(args, k) for k in ("test_cmd", "sandbox", "image", "backend", "model", "effort",
                                          "max_workers", "review_rounds", "max_turns", "task_tokens", "max_cost",
                                          "pr")}
    if args.script:
        opts["script"] = str(Path(args.script).resolve())
    return opts


def add_task_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--test-cmd", default="python -m pytest -q", help="command that runs the test suite")
    p.add_argument("--sandbox", choices=["auto", "docker", "subprocess"], default="auto")
    p.add_argument("--image", default=DEFAULT_IMAGE,
                   help="docker sandbox image (should contain your project's test dependencies)")
    p.add_argument("--backend", choices=["claude", "scripted"], default="claude")
    p.add_argument("--script", help="JSON script for the scripted backend")
    p.add_argument("--model", default="claude-opus-5-5")
    p.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--max-workers", type=int, default=3, help="parallel workers per task")
    p.add_argument("--review-rounds", type=int, default=2)
    p.add_argument("--max-turns", type=int, default=40, help="turn budget per agent")
    p.add_argument("--task-tokens", type=int, help="token budget shared by all agents of the task")
    p.add_argument("--max-cost", type=float, metavar="USD",
                   help="hard stop once the task's estimated API cost reaches this many dollars")
    p.add_argument("--max-attempts", type=int, default=2, help="retries on crashes")
    p.add_argument("--pr", action="store_true",
                   help="push the branch and open a PR (only on repos you own)")


def submit(args, repo: str, text: str, **extra) -> str:
    if args.backend == "scripted" and not args.script:
        sys.exit("--backend scripted needs --script")
    if parse_github(repo) is None:  # local repo: store an absolute path, workers may run elsewhere
        repo = str(Path(repo).expanduser().resolve())
    q = Queue(home_dir(args) / "fleet.db")
    return q.submit(repo, text, max_attempts=args.max_attempts, **task_options(args), **extra)


def cmd_submit(args) -> None:
    print(submit(args, args.repo, args.task))


def cmd_run(args) -> None:
    tid = submit(args, args.repo, args.task)
    drain_and_report(args, tid)


def cmd_issue(args) -> None:
    owner, repo, number = github.parse_issue_ref(args.ref)
    if args.pr:  # fail fast, before spending any tokens
        github.assert_owned(owner, repo)
    issue = github.fetch_issue(owner, repo, number)
    print(f"issue: {issue['title']} ({issue['url']})")
    tid = submit(args, f"{owner}/{repo}", github.issue_task_text(owner, repo, number, issue),
                 title=short_title(f"Fix #{number}: {issue['title']}"), issue=number)
    drain_and_report(args, tid)


def drain_and_report(args, tid: str) -> None:
    print(f"task {tid} queued; running...")
    Orchestrator(home_dir(args), Queue(home_dir(args) / "fleet.db"), args.concurrency).run(drain=True)
    show(args, tid)


def cmd_worker(args) -> None:
    Orchestrator(home_dir(args), Queue(home_dir(args) / "fleet.db"), args.concurrency).run(
        drain=not args.forever)


def cmd_status(args) -> None:
    if args.id:
        return show(args, args.id)
    rows = Queue(home_dir(args) / "fleet.db").list(args.limit)
    print(f"{'ID':8}  {'STATUS':9}  {'STAGE':24}  {'TRY':3}  {'AGE':>6}  {'COST':>8}  TASK")
    for t in rows:
        age = _age(time.time() - t["created"])
        cost = usd(sum(a["cost"] for a in spend(home_dir(args) / "runs" / t["id"]).values()))
        text = t["text"].splitlines()[0][:50] if t["text"] else ""
        print(f"{t['id']:8}  {t['status']:9}  {t['stage'][:24]:24}  {t['attempts']:3}  {age:>6}  {cost:>8}  "
              f"{text}{'  ' + t['pr_url'] if t['pr_url'] else ''}")


def show(args, tid: str) -> None:
    t = Queue(home_dir(args) / "fleet.db").get(tid)
    if t is None:
        sys.exit(f"no task {tid}")
    r = t["result"] or {}
    print(f"task {t['id']}: {t['status']} ({t['stage']}), attempts {t['attempts']}/{t['max_attempts']}")
    print(f"repo: {t['repo']}")
    if iso := isolation(home_dir(args) / "runs" / t["id"]):
        print(f"sandbox: {iso}")
    if r:
        print(f"branch: {r.get('branch')}  tests: {'PASS' if r.get('tests_passed') else 'FAIL'}")
        for s in r.get("subtasks", []):
            print(f"  - [{s['verdict']}] {s['title']} ({len(s['rounds'])} round(s))")
        if r.get("diffstat"):
            print(r["diffstat"])
    per_agent = spend(home_dir(args) / "runs" / t["id"])
    if per_agent:
        tokens = sum(a["tokens"] for a in per_agent.values())
        cost = sum(a["cost"] for a in per_agent.values())
        cap = t["options"].get("max_cost")
        print(f"tokens: {tokens:,} (" + ", ".join(f"{n} {a['tokens']:,}" for n, a in per_agent.items()) + ")")
        print(f"cost: {usd(cost)} est." + (f" of {usd(cap)} budget" if cap else "") + " ("
              + ", ".join(f"{n} {usd(a['cost'])}" for n, a in per_agent.items()) + ")")
    if t["pr_url"]:
        print(f"PR: {t['pr_url']}")
    if t["error"]:
        print(f"error: {t['error'].strip().splitlines()[-1]}")
    print(f"trajectories: {home_dir(args) / 'runs' / t['id']}")


def cmd_logs(args) -> None:
    runs = home_dir(args) / "runs" / args.id
    queue = Queue(home_dir(args) / "fleet.db")
    seen: dict[str, int] = {}
    while True:
        fresh = []
        for f in sorted(runs.glob(f"{args.agent or '*'}.jsonl")):
            events = read(f)
            fresh += events[seen.get(f.name, 0):]
            seen[f.name] = len(events)
        for e in sorted(fresh, key=lambda e: e["ts"]):
            if args.json:
                print(json.dumps(e), flush=True)
            else:
                stamp = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
                print(f"{stamp} {e['agent']:14} {describe(e)}", flush=True)
        task = queue.get(args.id)
        if not args.follow or (not fresh and task and task["status"] in ("done", "failed", "cancelled")):
            return
        time.sleep(1)


def describe(e: dict) -> str:
    kind = e["event"]
    if kind == "tool":
        arg = json.dumps(e.get("input"))[:100]
        return f"{'!' if e.get('is_error') else '>'} {e['name']} {arg}"
    if kind == "model":
        text = " ".join(b.get("text", "") for b in e.get("content", []) if b.get("type") == "text")
        return f"~ turn {e['turn']} ({e['stop_reason']}) {text[:120]}".rstrip()
    if kind == "end":
        return f"= {e['status']} after {e['turns']} turns, {e['tokens']} tokens, {usd(e.get('cost', 0))}"
    if kind == "resume":
        return f"+ resumed from checkpoint after turn {e['turn']}"
    if kind == "start":
        return f"+ start: {e['task'].splitlines()[0][:100]}"
    return f"# {kind}"


def cmd_cancel(args) -> None:
    ok = Queue(home_dir(args) / "fleet.db").cancel(args.id)
    print("cancelled" if ok else "only queued tasks can be cancelled")


def cmd_retry(args) -> None:
    ok = Queue(home_dir(args) / "fleet.db").retry(args.id)
    print("requeued; run `fleet worker` to process it" if ok else "only failed or cancelled tasks can be retried")


def cmd_serve(args) -> None:
    from .dashboard import serve
    serve(home_dir(args), args.host, args.port)


def _age(seconds: float) -> str:
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= n:
            return f"{int(seconds // n)}{unit}"
    return f"{int(seconds)}s"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="fleet", description="A fleet of autonomous coding agents.")
    ap.add_argument("--home", help="state directory (default $FLEET_HOME or ~/.fleet)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("submit", help="queue a task")
    p.add_argument("repo", help="local git repo path or GitHub owner/repo")
    p.add_argument("task", help="what to do")
    add_task_args(p)
    p.set_defaults(fn=cmd_submit)

    p = sub.add_parser("run", help="queue a task and run the queue until empty")
    p.add_argument("repo")
    p.add_argument("task")
    add_task_args(p)
    p.add_argument("--concurrency", type=int, default=2)
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("issue", help="work on a GitHub issue: owner/repo#N")
    p.add_argument("ref")
    add_task_args(p)
    p.add_argument("--concurrency", type=int, default=2)
    p.set_defaults(fn=cmd_issue)

    p = sub.add_parser("worker", help="process queued tasks")
    p.add_argument("--concurrency", type=int, default=2)
    p.add_argument("--forever", action="store_true", help="keep polling instead of exiting when empty")
    p.set_defaults(fn=cmd_worker)

    p = sub.add_parser("status", help="list tasks, or show one")
    p.add_argument("id", nargs="?")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("logs", help="print a task's trajectories")
    p.add_argument("id")
    p.add_argument("--agent", help="only this agent, e.g. worker-1")
    p.add_argument("--json", action="store_true")
    p.add_argument("-f", "--follow", action="store_true", help="keep streaming until the task ends")
    p.set_defaults(fn=cmd_logs)

    p = sub.add_parser("cancel", help="cancel a queued task")
    p.add_argument("id")
    p.set_defaults(fn=cmd_cancel)

    p = sub.add_parser("retry", help="requeue a failed or cancelled task")
    p.add_argument("id")
    p.set_defaults(fn=cmd_retry)

    p = sub.add_parser("serve", help="web dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(fn=cmd_serve)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        args.fn(args)
    except github.NotOwnedError as e:
        sys.exit(f"refused: {e}")


if __name__ == "__main__":
    main()
