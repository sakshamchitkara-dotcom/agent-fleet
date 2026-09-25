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
    tid = out.split("task ")[1].split()[0]

    main(["--home", home, "status"])
    assert tid in capsys.readouterr().out

    main(["--home", home, "logs", tid, "--agent", "worker-1"])
    logs = capsys.readouterr().out
    assert "> apply_patch" in logs and "= finished" in logs


def test_scripted_requires_script(tmp_path, capsys):
    import pytest
    with pytest.raises(SystemExit):
        main(["--home", str(tmp_path), "submit", ".", "x", "--backend", "scripted"])
