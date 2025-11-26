import subprocess
from pathlib import Path

import pytest

CALC = '''def add(a, b):
    return a - b


def mul(a, b):
    return a * b
'''

TEST_CALC = '''import unittest

from calc import add, mul


class CalcTest(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(2, 3), 5)

    def test_mul(self):
        self.assertEqual(mul(2, 3), 6)


if __name__ == "__main__":
    unittest.main()
'''


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "calc.py").write_text(CALC)
    (path / "test_calc.py").write_text(TEST_CALC)
    for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init"]):
        subprocess.run(["git", *args], cwd=path, check=True)
    return path


@pytest.fixture
def repo(tmp_path):
    """A tiny git repo with a failing test (add() subtracts)."""
    return make_repo(tmp_path / "repo")
