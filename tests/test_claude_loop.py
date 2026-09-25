"""The Claude backend driven through the real agent loop with a fake SDK client."""

import copy

from anthropic.types import Message

from fleet.agent import Agent, Budget
from fleet.models import ClaudeModel
from fleet.roles import WORKER
from fleet.tools import Toolbox
from fleet.trajectory import Trajectory


def msg(content, stop="tool_use", inp=100):
    return Message.model_validate({
        "id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "stop_reason": stop, "stop_sequence": None, "content": content,
        "usage": {"input_tokens": inp, "output_tokens": 20}})


class FakeClient:
    def __init__(self, replies, summary="notes"):
        self.replies = list(replies)
        self.requests = []
        self.summary_requests = []
        outer = self

        class Stream:
            def __init__(self, m):
                self.m = m

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get_final_message(self):
                return self.m

        class Messages:
            def stream(self, **kw):
                outer.requests.append(copy.deepcopy(kw))
                return Stream(outer.replies.pop(0))

            def create(self, **kw):
                outer.summary_requests.append(kw)
                return msg([{"type": "text", "text": summary}], stop="end_turn")

        self.messages = Messages()


THINK = {"type": "thinking", "thinking": "", "signature": "sig-abc"}


def test_thinking_replayed_verbatim_and_results_batched(repo, tmp_path):
    client = FakeClient([
        msg([THINK,
             {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "calc.py"}},
             {"type": "tool_use", "id": "t2", "name": "grep", "input": {"pattern": "def "}}]),
        msg([{"type": "tool_use", "id": "t3", "name": "apply_patch",
              "input": {"path": "calc.py", "old": "a - b", "new": "a + b"}}]),
        msg([{"type": "tool_use", "id": "t4", "name": "finish",
              "input": {"summary": "fixed", "tests_passed": True}}]),
    ])
    agent = Agent("worker-1", WORKER, ClaudeModel(client=client), Toolbox(repo),
                  Trajectory(tmp_path / "t.jsonl", "worker-1"))
    res = agent.run("fix add")
    assert res.ok and "a + b" in (repo / "calc.py").read_text()
    second = client.requests[1]["messages"]
    assert second[1]["role"] == "assistant" and second[1]["content"][0] == THINK  # unchanged
    results = second[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]  # both in ONE user message
    # history is append-only: every request extends the previous one
    assert client.requests[2]["messages"][:3] == second[:3]
    assert all(r["model"] == "claude-opus-5-5" and "tool_choice" not in r for r in client.requests)


def test_compaction_uses_summary_in_fresh_context(repo, tmp_path):
    client = FakeClient([
        msg([THINK, {"type": "tool_use", "id": "t1", "name": "list_dir", "input": {"path": "."}}], inp=500),
        msg([{"type": "tool_use", "id": "t2", "name": "finish",
              "input": {"summary": "done", "tests_passed": False}}]),
    ], summary="SUMMARY: listed the repo")
    agent = Agent("worker-1", WORKER, ClaudeModel(client=client), Toolbox(repo),
                  Trajectory(tmp_path / "t.jsonl", "worker-1"), Budget(compact_at=400))
    assert agent.run("the task").ok
    fresh = client.requests[1]["messages"]
    assert len(fresh) == 1 and "SUMMARY: listed the repo" in fresh[0]["content"]
    assert "sig-abc" not in str(fresh)  # no earlier thinking replayed after compaction
    s = client.summary_requests[0]
    assert s["model"] == "claude-opus-5-5" and s["thinking"] == {"type": "adaptive"}
    assert "TOOL CALL list_dir" in s["messages"][0]["content"]
