"""Structured JSONL log of every agent step (one file per agent per task)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path


class Trajectory:
    def __init__(self, path: str | Path, agent: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.agent = agent
        self._lock = threading.Lock()

    def log(self, event: str, **data) -> None:
        rec = {"ts": round(time.time(), 3), "agent": self.agent, "event": event, **data}
        line = json.dumps(rec, default=str)
        with self._lock, self.path.open("a") as f:
            f.write(line + "\n")


def isolation(runs: str | Path) -> str:
    """Sandbox isolation the task's agents ran under (from their start events)."""
    seen = sorted({e["isolation"] for f in Path(runs).glob("*.jsonl") for e in read(f)
                   if e.get("event") == "start" and e.get("isolation")})
    return "; ".join(seen)


def spend(runs: str | Path) -> dict[str, dict]:
    """Live tokens and USD per agent, summed from model and compaction calls (works mid-run)."""
    out: dict[str, dict] = {}
    for f in sorted(Path(runs).glob("*.jsonl")):
        agg = out.setdefault(f.stem, {"tokens": 0, "cost": 0.0})
        for e in read(f):
            if e.get("event") in ("model", "compact"):
                u = e.get("usage", {})
                agg["tokens"] += u.get("input_tokens", 0) + u.get("output_tokens", 0)
                agg["cost"] += e.get("cost", 0.0)
    return out


def summarize(runs: str | Path) -> list[dict]:
    """One row per agent, in start order: outcome, turns, tool calls, spend and what it reported."""
    rows = []
    for f in Path(runs).glob("*.jsonl"):
        events = read(f)
        starts = [i for i, e in enumerate(events) if e["event"] == "start"]
        events = events[starts[-1]:] if starts else events  # the latest run of this agent
        tools = [e for e in events if e["event"] == "tool"]
        end = next((e for e in reversed(events) if e["event"] == "end"), {})
        out = end.get("output") or {}
        rows.append({"agent": f.stem, "ts": events[0]["ts"] if events else 0,
                     "status": end.get("status", "running"), "turns": end.get("turns", 0),
                     "tool_calls": len(tools), "tool_errors": sum(bool(t.get("is_error")) for t in tools),
                     "steps": [t["name"] for t in tools],
                     "tokens": end.get("tokens", 0), "cost": end.get("cost", 0.0),
                     "resumed": any(e["event"] == "resume" for e in events),
                     "report": out.get("summary") or out.get("feedback") or ""})
    rows.sort(key=lambda r: (r["ts"], r["agent"]))
    return rows


def read(path: str | Path) -> list[dict]:
    """Read a trajectory, skipping a partially-written trailing line."""
    out = []
    for line in Path(path).read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out
