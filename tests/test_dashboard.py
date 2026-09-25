import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from fleet.dashboard import make_handler
from fleet.queue import Queue
from fleet.trajectory import Trajectory


@pytest.fixture
def server(tmp_path):
    q = Queue(tmp_path / "fleet.db")
    tid = q.submit("o/r", "fix <script>alert(1)</script>")
    Trajectory(tmp_path / "runs" / tid / "planner.jsonl", "planner").log("start", task="t",
                                                                        isolation="bwrap: no network")
    w = Trajectory(tmp_path / "runs" / tid / "worker-1.jsonl", "worker-1")
    w.log("model", turn=1, usage={"input_tokens": 900, "output_tokens": 100}, cost=0.0056, content=[])
    w.log("tool", name="run_tests", input={}, is_error=False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", tid
    srv.shutdown()


def get(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.read().decode()


def test_pages_and_api(server):
    base, tid = server
    assert "agent-fleet" in get(base + "/")[1]
    assert get(f"{base}/task/{tid}")[0] == 200
    tasks = json.loads(get(base + "/api/tasks")[1])
    assert tasks[0]["id"] == tid and tasks[0]["cost"] == 0.0056
    detail = json.loads(get(f"{base}/api/task/{tid}")[1])
    assert list(detail["agents"]) == ["planner", "worker-1"]
    assert detail["isolation"] == "bwrap: no network"
    assert detail["spend"]["worker-1"] == {"tokens": 1000, "cost": 0.0056}
    assert detail["task"]["text"].startswith("fix <script>")  # raw in JSON; rendered via textContent


@pytest.mark.parametrize("path", ["/api/task/../../etc", "/api/task/zzzzzzzz", "/task/../x", "/nope"])
def test_bad_paths_404(server, path):
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base + path)
    assert e.value.code == 404


def post(url, body=b"{}", headers=None):
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_approve_and_reject_from_the_dashboard(server, tmp_path, monkeypatch):
    from fleet.orchestrator import Orchestrator
    monkeypatch.setattr(Orchestrator, "open_pr", lambda self, task, result: "https://example.invalid/pr/7")
    base, queued = server
    q = Queue(tmp_path / "fleet.db")
    a, b = q.submit("o/r", "a", pr=True), q.submit("o/r", "b", pr=True)
    for tid in (a, b):
        q.update(tid, status="awaiting_approval", result={"branch": f"fleet/{tid}", "diff": "+fix"})
    detail = json.loads(get(f"{base}/api/task/{a}")[1])
    assert detail["task"]["status"] == "awaiting_approval" and detail["task"]["result"]["diff"] == "+fix"

    # cross-site requests are refused before anything happens
    assert post(f"{base}/api/task/{a}/approve", headers={"Content-Type": "text/plain"})[0] == 403
    assert post(f"{base}/api/task/{a}/approve", headers={"Origin": "https://evil.example"})[0] == 403
    assert post(f"{base}/api/task/{a}/approve", headers={"Host": "evil.example"})[0] == 403
    assert q.get(a)["status"] == "awaiting_approval"

    assert post(f"{base}/api/task/{a}/approve", headers={"Origin": base}) == (200, {"pr_url": "https://example.invalid/pr/7"})
    assert q.get(a)["status"] == "done" and q.get(a)["pr_url"] == "https://example.invalid/pr/7"
    assert post(f"{base}/api/task/{a}/approve")[0] == 409  # already approved
    assert post(f"{base}/api/task/{queued}/reject")[0] == 409  # never awaited approval

    assert post(f"{base}/api/task/{b}/reject", b'{"reason": "too broad"}')[0] == 200
    assert q.get(b)["error"] == "rejected at approval: too broad"
    assert post(f"{base}/api/task/{b}/delete")[0] == 404
