# -*- coding: utf-8 -*-
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sandbox_verify as S  # noqa: E402


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo)] + list(args), check=True,
                   capture_output=True, text=True)


def test_sandbox_run(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "test_ok.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n",
                                     encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    ev = S.run(["test_ok.py"], repo=repo, timeout=120)
    assert ev["ok"] is True, ev
    assert (ev.get("pytest") or {}).get("rc") == 0
    assert Path(ev["evidence_path"]).exists()


def test_sandbox_requires_tests():
    try:
        S.run([], repo=HERE.parent)
        assert False
    except ValueError:
        pass
