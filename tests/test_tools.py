import pytest

from fleet.tools import ToolError, Toolbox

TEST_CMD = "python3 -m unittest -q"


@pytest.fixture
def tb(repo):
    return Toolbox(repo, test_cmd=TEST_CMD)


def test_path_jail(tb):
    for bad in ("../x", "/etc/passwd", ".git/config"):
        with pytest.raises(ToolError):
            tb.execute("read_file", {"path": bad})


def test_symlink_escape_blocked(tb, repo, tmp_path):
    (tmp_path / "secret").write_text("s")
    (repo / "link").symlink_to(tmp_path / "secret")
    with pytest.raises(ToolError):
        tb.execute("read_file", {"path": "link"})


def test_read_list_grep(tb):
    assert "return a - b" in tb.execute("read_file", {"path": "calc.py", "start": 1, "end": 2})
    listing = tb.execute("list_dir", {"path": "."})
    assert "calc.py" in listing and ".git" not in listing
    assert tb.execute("grep", {"pattern": r"def add"}) == "calc.py:1: def add(a, b):"


def test_apply_patch_requires_unique_match(tb, repo):
    with pytest.raises(ToolError):
        tb.execute("apply_patch", {"path": "calc.py", "old": "return", "new": "x"})
    tb.execute("apply_patch", {"path": "calc.py", "old": "a - b", "new": "a + b"})
    assert "a + b" in (repo / "calc.py").read_text()


def test_write_file_and_diff_includes_new_files(tb):
    tb.execute("write_file", {"path": "pkg/new.py", "content": "X = 1\n"})
    d = tb.execute("git_diff", {})
    assert "pkg/new.py" in d and "+X = 1" in d


def test_run_tests_reports_failure_then_success(tb):
    ok, out = tb.run_tests()
    assert not ok and "FAIL" in out
    tb.execute("apply_patch", {"path": "calc.py", "old": "a - b", "new": "a + b"})
    ok, out = tb.run_tests()
    assert ok, out


def test_unknown_tool(tb):
    with pytest.raises(ToolError):
        tb.execute("rm_rf", {})


def test_schemas_include_finish(tb):
    names = [t["name"] for t in tb.schemas({"type": "object", "properties": {}}, "done")]
    assert names[-1] == "finish" and "apply_patch" in names


def test_narrowed_test_command(repo, tmp_path):
    (repo / "tests").mkdir()
    (repo / "pytest.ini").write_text("")
    narrow = lambda cmd: Toolbox(repo, test_cmd=cmd).narrowed(["tests/test_new.py"])
    # named test files and directories are replaced by the new files; options and config stay
    assert narrow("python3 -m unittest -q test_calc.py") == "python3 -m unittest -q tests/test_new.py"
    assert narrow("pytest -c pytest.ini tests") == "pytest -c pytest.ini tests/test_new.py"
    assert narrow("node --test") == "node --test tests/test_new.py"
    # unknown runners and shell pipelines are not narrowed
    for cmd in ("npm test", "make test", "go test ./...", "cd sub && pytest", "pytest | tee log"):
        assert narrow(cmd) is None, cmd
