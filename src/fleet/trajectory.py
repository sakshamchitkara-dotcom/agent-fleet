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


def read(path: str | Path) -> list[dict]:
    """Read a trajectory, skipping a partially-written trailing line."""
    out = []
    for line in Path(path).read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out
