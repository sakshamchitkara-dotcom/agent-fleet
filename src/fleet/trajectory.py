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
    """Live tokens and USD per agent, summed from model events (works mid-run)."""
    out: dict[str, dict] = {}
    for f in sorted(Path(runs).glob("*.jsonl")):
        agg = out.setdefault(f.stem, {"tokens": 0, "cost": 0.0})
        for e in read(f):
            if e.get("event") == "model":
                u = e.get("usage", {})
                agg["tokens"] += u.get("input_tokens", 0) + u.get("output_tokens", 0)
                agg["cost"] += e.get("cost", 0.0)
    return out


def read(path: str | Path) -> list[dict]:
    """Read a trajectory, skipping a partially-written trailing line."""
    out = []
    for line in Path(path).read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out
