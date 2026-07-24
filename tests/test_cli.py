import json

from fleet.cli import main
from tests.test_orchestrator import FIX_SCRIPT


def test_run_status_logs(repo, tmp_path, capsys):
    script = tmp_path / "s.json"
    script.write_text(json.dumps(FIX_SCRIPT))
    home = str(tmp_path / "home")
    main(["--home", home, "run", str(repo), "Fix the failing test", "--backend", "scripted",
          "--script", str(script), "--sandbox", "subprocess", "--test-cmd", "python3 -m unittest -q"])
    out = capsys.readouterr().out
    assert ": done (finished)" in out and "tests: PASS" in out and "[approve] Fix add" in out
    assert "tokens: " in out and "worker-1 " in out
    assert "sandbox: " in out and "cost: $0.0" in out and "est." in out
    tid = out.split("task ")[1].split()[0]

    main(["--home", home, "status"])
    listing = capsys.readouterr().out
    assert tid in listing and "COST" in listing and "$0.0" in listing

    main(["--home", home, "logs", tid, "--agent", "worker-1", "--follow"])  # task done: returns
    logs = capsys.readouterr().out
    assert "> apply_patch" in logs and "= finished" in logs


def test_scripted_requires_script(tmp_path, capsys):
    import pytest
    with pytest.raises(SystemExit):
        main(["--home", str(tmp_path), "submit", ".", "x", "--backend", "scripted"])


def test_local_repo_stored_as_absolute_path(repo, tmp_path, monkeypatch, capsys):
    from fleet.queue import Queue
    monkeypatch.chdir(repo)
    main(["--home", str(tmp_path / "h"), "submit", ".", "x"])
    tid = capsys.readouterr().out.strip()
    assert Queue(tmp_path / "h" / "fleet.db").get(tid)["repo"] == str(repo.resolve())


def test_retry_starts_fresh(repo, tmp_path, capsys):
    from fleet.queue import Queue
    home = tmp_path / "home"
    q = Queue(home / "fleet.db")
    tid = q.submit(str(repo), "x")
    q.update(tid, status="failed")
    runs = home / "runs" / tid
    runs.mkdir(parents=True)
    (runs / "worker-1.jsonl").write_text('{"event": "end"}\n')
    (runs / "worker-1.ckpt.json").write_text("{}")
    main(["--home", str(home), "retry", tid])
    assert "requeued" in capsys.readouterr().out
    assert not list(runs.glob("*.json*"))
    assert len(list(runs.glob("attempt-*/worker-1.jsonl"))) == 1
    assert q.get(tid)["status"] == "queued"


def test_watch_dry_run_then_apply(tmp_path, capsys, monkeypatch):
    from fleet import github
    from fleet.orchestrator import Orchestrator
    from fleet.queue import Queue
    from tests.test_github import fake_gh

    base = fake_gh(login="me", owner="me")
    calls = []

    def gh(*args):
        calls.append(args)
        if "issues?" in args[1]:
            return json.dumps([
                {"number": 4, "title": "Bug A", "body": "a", "html_url": "https://x/4"},
                {"number": 5, "title": "A PR", "html_url": "https://x/5", "pull_request": {}}])
        return base(*args)

    monkeypatch.setattr(github, "gh", gh)
    monkeypatch.setattr(github.assert_owned, "__defaults__", (gh,))
    monkeypatch.setattr(github.labeled_issues, "__defaults__", (gh,))
    ran = []
    monkeypatch.setattr(Orchestrator, "run", lambda self, drain=True, poll=2.0: ran.append(1))
    home = str(tmp_path / "h")

    main(["--home", home, "watch", "me/r", "--once"])
    out = capsys.readouterr().out
    assert "(dry-run)" in out and "would work on #4: Bug A" in out and "#5" not in out
    assert not Queue(tmp_path / "h" / "fleet.db").list() and not ran
    assert any("labels=fleet" in a[1] for a in calls if a[0] == "api")

    main(["--home", home, "watch", "me/r", "--once", "--apply"])
    assert "#4: Bug A -> task" in capsys.readouterr().out and ran == [1]
    task = Queue(tmp_path / "h" / "fleet.db").find_issue("me/r", 4)
    assert task["options"]["issue"] == 4 and task["repo"] == "me/r"

    main(["--home", home, "watch", "me/r", "--once", "--apply"])  # already queued: skipped
    assert "->" not in capsys.readouterr().out and ran == [1]


def test_watch_refuses_foreign_repo(tmp_path, monkeypatch):
    import pytest

    from fleet import github
    from tests.test_github import fake_gh
    monkeypatch.setattr(github.assert_owned, "__defaults__", (fake_gh(login="me"),))
    with pytest.raises(SystemExit) as e:
        main(["--home", str(tmp_path), "watch", "torvalds/linux", "--once"])
    assert "refused" in str(e.value)


def test_describe_compaction_shows_its_cost():
    from fleet.cli import describe
    e = {"event": "compact", "messages_before": 14, "cost": 0.0312, "usage": {}}
    assert describe(e) == "# compacted 14 messages into notes ($0.0312)"
