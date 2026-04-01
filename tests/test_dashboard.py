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
