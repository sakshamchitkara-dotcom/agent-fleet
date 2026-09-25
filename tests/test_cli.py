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
