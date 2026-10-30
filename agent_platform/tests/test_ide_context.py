# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from legion_command import ide_context as C  # noqa: E402


def test_repo_map_and_search():
    m = C.repo_map()
    assert m["files"] > 10
    hits = C.search("def acquire_lock", 5)
    assert any("rw_guard.py" in h["path"] for h in hits)


def test_context_budget_and_no_secret_paths():
    r = C.build_context("legion ide 写文件 preview", 1200)
    assert r["ok"] and r["chars"] <= 1200
    assert "credentials" not in r["context"].lower()


def test_ast_symbol_search():
    syms = C.search_symbols("acquire_oh_lock", 5)
    assert any(s.get("name") == "acquire_oh_lock" for s in syms)
    assert any("ide_worktree.py" in s.get("path", "") for s in syms)
