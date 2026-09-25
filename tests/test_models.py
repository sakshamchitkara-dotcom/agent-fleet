from anthropic.types import Message

from fleet.models import ClaudeModel, ModelFactory, ScriptedModel

SCRIPT = {
    "planner": [[{"name": "list_dir", "input": {"path": "."}}]],
    "worker-1": [[{"text": "thinking out loud"}, {"name": "run_tests", "input": {}}]],
}


def test_scripted_replays_by_agent_then_role_then_finishes():
    w = ScriptedModel(SCRIPT, "worker-1")
    r = w.complete("sys", [], [])
    assert r.stop_reason == "tool_use" and r.text == "thinking out loud"
    assert r.tool_uses[0]["name"] == "run_tests" and r.tool_uses[0]["id"] == "toolu_worker-1_0_1"
    assert w.complete("sys", [], []).tool_uses[0]["name"] == "finish"
    p = ScriptedModel(SCRIPT, "planner-2")  # falls back to role name
    assert p.complete("s", [], []).tool_uses[0]["name"] == "list_dir"
    assert ScriptedModel(SCRIPT, "reviewer-1").complete("s", [], []).tool_uses[0]["name"] == "finish"


class _Stream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class FakeClient:
    def __init__(self, msg):
        self.calls = []
        outer = self

        class Messages:
            def stream(self, **kw):
                outer.calls.append(kw)
                return _Stream(msg)

        self.messages = Messages()


def test_claude_request_shape_and_verbatim_content():
    msg = Message.model_validate({
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "stop_reason": "tool_use", "stop_sequence": None,
        "content": [
            {"type": "thinking", "thinking": "", "signature": "sig123"},
            {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"path": "a.py"}},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100,
                  "cache_creation_input_tokens": 0},
    })
    client = FakeClient(msg)
    tools = [{"name": "read_file", "description": "d", "input_schema": {"type": "object"}}]
    r = ClaudeModel(client=client).complete("sys", [{"role": "user", "content": "hi"}], tools)
    kw = client.calls[0]
    assert kw["model"] == "claude-opus-5-5"
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"] == {"effort": "high"}
    assert "tool_choice" not in kw  # forced tool_choice is rejected on Opus 5.5
    assert kw["tools"][0]["eager_input_streaming"] is True
    assert r.content[0] == {"type": "thinking", "thinking": "", "signature": "sig123"}
    assert r.tool_uses[0]["input"] == {"path": "a.py"}
    assert r.usage["input_tokens"] == 110


def test_factory_scripted(tmp_path):
    import json
    p = tmp_path / "s.json"
    p.write_text(json.dumps(SCRIPT))
    f = ModelFactory("scripted", script=p)
    assert isinstance(f("worker-1"), ScriptedModel)
