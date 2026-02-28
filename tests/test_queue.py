import threading

import pytest

from fleet.queue import Queue


def test_submit_claim_update(tmp_path):
    q = Queue(tmp_path / "q.db")
    a = q.submit("o/r", "first", test_cmd="pytest")
    b = q.submit("o/r", "second")
    t = q.claim()
    assert t["id"] == a and t["status"] == "running" and t["attempts"] == 1
    assert t["options"] == {"test_cmd": "pytest"}
    q.update(a, status="done", result={"branch": "fleet/x"})
    assert q.get(a)["result"] == {"branch": "fleet/x"}
    assert q.claim()["id"] == b and q.claim() is None
    with pytest.raises(ValueError):
        q.update(a, attempts=0)


def test_retries_then_fails(tmp_path):
    q = Queue(tmp_path / "q.db")
    tid = q.submit("o/r", "x", max_attempts=2)
    q.claim()
    assert q.fail(tid, "boom") == "queued"
    q.claim()
    assert q.fail(tid, "boom again") == "failed"
    assert q.get(tid)["error"] == "boom again" and q.claim() is None


def test_requeue_stale_and_cancel(tmp_path):
    q = Queue(tmp_path / "q.db")
    a = q.submit("o/r", "x")
    b = q.submit("o/r", "y")
    q.claim()
    assert q.requeue_stale() == 1 and q.get(a)["status"] == "queued"
    assert q.cancel(b) and not q.cancel(b)


def test_concurrent_claims_are_exclusive(tmp_path):
    q = Queue(tmp_path / "q.db")
    ids = {q.submit("o/r", str(i)) for i in range(20)}
    got, lock = [], threading.Lock()

    def worker():
        while (t := q.claim()) is not None:
            with lock:
                got.append(t["id"])

    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(got) == sorted(ids)


def test_retry_only_failed_or_cancelled(tmp_path):
    q = Queue(tmp_path / "q.db")
    tid = q.submit("o/r", "x", max_attempts=1)
    assert not q.retry(tid)  # still queued
    q.claim()
    q.fail(tid, "boom")
    assert q.retry(tid)
    t = q.get(tid)
    assert t["status"] == "queued" and t["attempts"] == 0 and t["error"] == ""
