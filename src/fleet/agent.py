"""The core agent loop: model <-> tools until `finish`, within budgets."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field

from . import pricing
from .roles import Role
from .tools import Toolbox, ToolError
from .trajectory import Trajectory

MAX_RESULT_CHARS = 30_000
TYPES = {"string": str, "integer": int, "boolean": bool, "array": list, "object": dict}


@dataclass
class Budget:
    max_turns: int = 40
    max_tokens: int = 3_000_000   # cumulative input + output across the run
    compact_at: int = 150_000     # per-request input tokens that trigger compaction


class TokenMeter:
    """Task-wide token and USD budget shared by every agent working on one task."""

    def __init__(self, limit: int | None = None, max_cost: float | None = None):
        self.limit = limit
        self.max_cost = max_cost
        self.used = 0
        self.cost = 0.0
        self.by_agent: dict[str, int] = {}
        self.cost_by_agent: dict[str, float] = {}
        self._lock = threading.Lock()

    def charge(self, agent: str, n: int, usd: float = 0.0) -> None:
        with self._lock:
            self.used += n
            self.cost += usd
            self.by_agent[agent] = self.by_agent.get(agent, 0) + n
            self.cost_by_agent[agent] = self.cost_by_agent.get(agent, 0.0) + usd

    @property
    def over_cost(self) -> bool:
        return self.max_cost is not None and self.cost >= self.max_cost

    @property
    def exhausted(self) -> bool:
        return (self.limit is not None and self.used >= self.limit) or self.over_cost

    def report(self) -> dict:
        with self._lock:
            return {"total": self.used, "by_agent": dict(self.by_agent),
                    "cost_usd": round(self.cost, 6),
                    "cost_by_agent": {a: round(c, 6) for a, c in self.cost_by_agent.items()},
                    "max_cost_usd": self.max_cost, "exhausted": self.exhausted}


@dataclass
class AgentResult:
    status: str                   # finished | budget_exhausted | refused
    output: dict = field(default_factory=dict)
    turns: int = 0
    tokens: int = 0
    cost: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == "finished"


def validate(args, schema: dict) -> str | None:
    """Minimal JSON-schema check for tool inputs (required keys + top-level types)."""
    if not isinstance(args, dict):
        return "tool input must be a JSON object"
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in args:
            return f"missing required field {key!r}"
    for key, val in args.items():
        if key not in props:
            return f"unexpected field {key!r}"
        want = TYPES.get(props[key].get("type"))
        if want and (not isinstance(val, want) or (want is int and isinstance(val, bool))):
            return f"field {key!r} must be {props[key]['type']}"
    return None


class Agent:
    def __init__(self, name: str, role: Role, model, toolbox: Toolbox, trajectory: Trajectory,
                 budget: Budget | None = None, meter: TokenMeter | None = None):
        self.name = name
        self.system = role.system
        self.model = model
        self.toolbox = toolbox
        self.log = trajectory
        self.budget = budget or Budget()
        self.meter = meter or TokenMeter()
        self.tools = toolbox.schemas(role.finish_schema, role.finish_description, role.tools)
        self.schemas = {t["name"]: t["input_schema"] for t in self.tools}

    def run(self, task: str) -> AgentResult:
        messages: list[dict] = [{"role": "user", "content": task}]
        tokens, cost = 0, 0.0
        model_id = getattr(self.model, "model", None)
        self.log.log("start", task=task, model=getattr(self.model, "model", type(self.model).__name__),
                     sandbox=self.toolbox.sandbox.mode,
                     isolation=self.toolbox.sandbox.isolation, tools=[t["name"] for t in self.tools])
        for turn in range(1, self.budget.max_turns + 1):
            if tokens >= self.budget.max_tokens:
                return self._end("budget_exhausted", turn - 1, tokens, cost, reason="token budget")
            if self.meter.exhausted:
                why = "task cost budget" if self.meter.over_cost else "task token budget"
                return self._end("budget_exhausted", turn - 1, tokens, cost, reason=why)
            reply = self.model.complete(self.system, messages, self.tools)
            used = reply.usage.get("input_tokens", 0) + reply.usage.get("output_tokens", 0)
            usd = pricing.cost(model_id, reply.usage)
            tokens, cost = tokens + used, cost + usd
            self.meter.charge(self.name, used, usd)
            self.log.log("model", turn=turn, stop_reason=reply.stop_reason, usage=reply.usage,
                         cost=round(usd, 6), content=[_loggable(b) for b in reply.content])
            messages.append({"role": "assistant", "content": reply.content})

            if reply.stop_reason == "refusal":
                return self._end("refused", turn, tokens, cost)
            if not reply.tool_uses:
                messages.append({"role": "user", "content":
                                 "Continue. Use the tools to make progress, and call `finish` when done."})
                continue

            results, finished = [], None
            for tu in reply.tool_uses:
                text, is_error = self._call(tu, truncated=reply.stop_reason == "max_tokens")
                if tu["name"] == "finish" and not is_error:
                    finished = tu["input"]
                results.append({"type": "tool_result", "tool_use_id": tu["id"],
                                "content": text, "is_error": is_error})
            messages.append({"role": "user", "content": results})
            if finished is not None:
                return self._end("finished", turn, tokens, cost, output=finished)
            if reply.usage.get("input_tokens", 0) >= self.budget.compact_at:
                messages = self._compact(task, messages)
        return self._end("budget_exhausted", self.budget.max_turns, tokens, cost, reason="turn budget")

    def _call(self, tu: dict, truncated: bool) -> tuple[str, bool]:
        name, args = tu["name"], tu.get("input")
        if truncated:
            text, err = "tool call was cut off by the output limit; retry with a smaller input", True
        elif name not in self.schemas:
            text, err = f"unknown tool {name!r}", True
        elif (problem := validate(args, self.schemas[name])):
            text, err = f"invalid input: {problem}", True
        elif name == "finish":
            text, err = "ok", False
        else:
            try:
                text, err = self.toolbox.execute(name, args), False
            except ToolError as e:
                text, err = str(e), True
            except Exception as e:  # a tool crash must not kill the agent
                text, err = f"tool crashed: {type(e).__name__}: {e}", True
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + "\n... (truncated)"
        self.log.log("tool", name=name, input=args, is_error=err, output=text[:4000])
        return text, err

    def _compact(self, task: str, messages: list[dict]) -> list[dict]:
        """Simple compaction: replace the whole history with a summary.

        Earlier thinking blocks are not replayed into the fresh context, so this
        stays valid under preserved thinking (no in-place history edits).
        """
        summary = self.model.summarize(render_transcript(messages))
        self.log.log("compact", messages_before=len(messages), summary=summary)
        return [{"role": "user", "content":
                 f"{task}\n\n## Progress so far (earlier context was compacted)\n{summary}\n\n"
                 "Continue from here."}]

    def _end(self, status: str, turns: int, tokens: int, cost: float, output: dict | None = None,
             **extra) -> AgentResult:
        self.log.log("end", status=status, turns=turns, tokens=tokens, cost=round(cost, 6),
                     output=output, **extra)
        return AgentResult(status, output or {}, turns, tokens, cost)


def _loggable(block: dict) -> dict:
    return {k: v for k, v in block.items() if k != "signature"}


def render_transcript(messages: list[dict], per_block: int = 2_000) -> str:
    out = []
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append(f"{m['role'].upper()}: {content[:per_block]}")
            continue
        for b in content:
            kind = b.get("type")
            if kind == "text":
                out.append(f"{m['role'].upper()}: {b['text'][:per_block]}")
            elif kind == "tool_use":
                out.append(f"TOOL CALL {b['name']}: {json.dumps(b.get('input'))[:per_block]}")
            elif kind == "tool_result":
                body = b.get("content") if isinstance(b.get("content"), str) else json.dumps(b.get("content"))
                out.append(f"TOOL RESULT{' (error)' if b.get('is_error') else ''}: {body[:per_block]}")
    return "\n".join(out)
