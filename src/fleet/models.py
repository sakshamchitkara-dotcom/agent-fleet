"""Model backends: Claude via the Anthropic SDK, and an offline scripted replayer.

Both return a `Reply` whose `content` is a list of plain-dict content blocks
that the agent loop appends to the conversation verbatim (thinking blocks
included - Claude Opus 5.5 ties them to the conversation, so history is
append-only and never edited).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MODEL = "claude-opus-5-5"


@dataclass
class Reply:
    content: list[dict]
    stop_reason: str
    usage: dict = field(default_factory=dict)

    @property
    def tool_uses(self) -> list[dict]:
        return [b for b in self.content if b.get("type") == "tool_use"]

    @property
    def text(self) -> str:
        return "\n".join(b["text"] for b in self.content if b.get("type") == "text")


class ClaudeModel:
    """Claude Opus 5.5 with adaptive thinking, streaming and prompt caching."""

    def __init__(self, model: str = DEFAULT_MODEL, effort: str = "high",
                 max_tokens: int = 32_000, client=None):
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.effort = effort  # Opus 5.5 defaults to "medium"; coding work wants more.
        self.max_tokens = max_tokens

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> Reply:
        # Stream tool inputs as they're generated (file contents can be large);
        # the agent loop validates each input against its schema before running it.
        tools = [{**t, "eager_input_streaming": True} for t in tools]
        with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            # Explicit breakpoint on the static prefix (tools render before system, so this
            # caches both): every agent of the same role - parallel workers, revision rounds,
            # post-compaction contexts - reads it instead of paying for it again. The top-level
            # marker caches the growing conversation on top of it.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=tools,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            cache_control={"type": "ephemeral"},
        ) as stream:
            msg = stream.get_final_message()
        return Reply(content=[b.to_dict() for b in msg.content], stop_reason=msg.stop_reason,
                     usage=_usage(msg.usage))

    def summarize(self, system: str, messages: list[dict], tools: list[dict]) -> tuple[str, dict]:
        """Working notes for compaction, plus the call's usage so it is priced like any turn.

        The request is the agent's own next request (same model, system, tools, effort and
        history, thinking blocks included) with one instruction appended, so the whole
        conversation is a cache read instead of fresh input. Should the model answer with
        tool calls only, it falls back to summarizing a rendered transcript.
        """
        from .agent import render_transcript
        reply = self.complete(system, [*messages, {"role": "user", "content": COMPACT_PROMPT}], tools)
        if reply.text.strip():
            return reply.text, reply.usage
        text, usage = self._summarize_transcript(render_transcript(messages))
        return text, {k: reply.usage.get(k, 0) + usage.get(k, 0) for k in usage}

    def _summarize_transcript(self, transcript: str) -> tuple[str, dict]:
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=8_000,
            thinking={"type": "adaptive"},
            output_config={"effort": "low"},
            system="You compress coding-agent transcripts into working notes.",
            messages=[{"role": "user", "content": SUMMARY_PROMPT + transcript}],
        )
        return "\n".join(b.text for b in msg.content if b.type == "text"), _usage(msg.usage)


def _usage(u) -> dict:
    """SDK usage -> dict whose `input_tokens` is the whole prompt (cached parts included)."""
    read, written = u.cache_read_input_tokens or 0, u.cache_creation_input_tokens or 0
    return {"input_tokens": u.input_tokens + read + written, "output_tokens": u.output_tokens,
            "cache_read_input_tokens": read, "cache_creation_input_tokens": written}


COMPACT_PROMPT = (
    "Your context is about to be reset. Do not call any tools now. Reply with working notes "
    "that let you continue from a fresh context: the task, files inspected and what matters in "
    "them, every edit made (path + what changed), the latest test results, open hypotheses, and "
    "the next step. Be concrete (paths, function names, error messages)."
)

SUMMARY_PROMPT = (
    "Summarize this coding-agent transcript so the agent can continue from a fresh context. "
    "Keep: the task, files inspected and what they contain that matters, every edit made (path "
    "+ what changed), the latest test results, open hypotheses, and the next step. Be concrete "
    "(paths, function names, error messages). Drop dead ends unless they rule something out.\n\n"
)


class ScriptedModel:
    """Deterministic offline backend that replays pre-written tool calls.

    A script maps agent names (or role names) to a list of turns; each turn is
    a list of tool calls `{"name": ..., "input": {...}}` or `{"text": ...}`.
    When a script runs out the model calls `finish`, so pipelines always end.
    """

    def __init__(self, script: dict, agent: str):
        role = agent.split("-")[0].split(".")[0]
        self.turns = list(script.get(agent) or script.get(role) or [])
        self.agent = agent
        self.cursor = 0
        self.summaries = 0

    @classmethod
    def load(cls, path: str | Path, agent: str) -> "ScriptedModel":
        return cls(json.loads(Path(path).read_text()), agent)

    def resume(self, turns_done: int) -> None:
        self.cursor = turns_done

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> Reply:
        if self.cursor < len(self.turns):
            turn = self.turns[self.cursor]
        else:
            turn = [{"name": "finish", "input": _exhausted_finish(tools)}]
        n = self.cursor
        self.cursor += 1
        blocks = []
        for i, step in enumerate(turn):
            if "text" in step:
                blocks.append({"type": "text", "text": step["text"]})
            else:
                blocks.append({"type": "tool_use", "id": f"toolu_{self.agent}_{n}_{i}",
                               "name": step["name"], "input": step.get("input", {})})
        in_tok = len(json.dumps(messages)) // 4
        out_tok = len(json.dumps(blocks)) // 4
        stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
        return Reply(blocks, stop, {"input_tokens": in_tok, "output_tokens": out_tok})

    def summarize(self, system: str, messages: list[dict], tools: list[dict]) -> tuple[str, dict]:
        from .agent import render_transcript
        transcript = render_transcript(messages)
        self.summaries += 1
        text = f"[scripted summary #{self.summaries}] {len(transcript)} chars of history compacted."
        return text, {"input_tokens": len(transcript) // 4, "output_tokens": len(text) // 4}


def _exhausted_finish(tools: list[dict]) -> dict:
    """A schema-valid finish() input for when the script runs out.

    Enums take their last option - for the reviewer that is request_changes,
    so an exhausted script never approves anything.
    """
    schema = next((t["input_schema"] for t in tools if t["name"] == "finish"), {})
    defaults = {"string": "script exhausted", "boolean": False, "integer": 0, "array": [], "object": {}}
    out = {}
    for key in schema.get("required", []):
        prop = schema["properties"][key]
        out[key] = prop["enum"][-1] if "enum" in prop else defaults.get(prop.get("type"), None)
    return out


PROVIDERS = ("anthropic", "bedrock", "vertex")


def make_client(provider: str = "anthropic"):
    """The SDK's own client per platform; each reads its usual environment (API key or
    `ant` profile; AWS_REGION + AWS credentials; ANTHROPIC_VERTEX_PROJECT_ID + CLOUD_ML_REGION)."""
    import anthropic
    if provider == "bedrock":
        return anthropic.AnthropicBedrockMantle()
    if provider == "vertex":
        return anthropic.AnthropicVertex()
    return anthropic.Anthropic()


def provider_model(provider: str, model: str) -> str:
    """Bedrock IDs carry an `anthropic.` prefix; Vertex and first-party IDs are bare."""
    if provider == "bedrock" and "anthropic." not in model:
        return f"anthropic.{model}"
    return model


class ModelFactory:
    """Creates one model instance per agent name (scripted cursors are per agent)."""

    def __init__(self, backend: str = "claude", script: str | Path | None = None,
                 model: str = DEFAULT_MODEL, effort: str = "high", provider: str = "anthropic"):
        if backend not in ("claude", "scripted"):
            raise ValueError(f"unknown backend {backend!r}")
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider {provider!r}")
        self.backend = backend
        self.provider = provider
        self.model = provider_model(provider, model)
        self.effort = effort
        self._script = json.loads(Path(script).read_text()) if script else {}
        self._lock = threading.Lock()
        self._client = None

    def __call__(self, agent: str):
        if self.backend == "scripted":
            return ScriptedModel(self._script, agent)
        with self._lock:
            if self._client is None:
                self._client = make_client(self.provider)
        return ClaudeModel(self.model, self.effort, client=self._client)
