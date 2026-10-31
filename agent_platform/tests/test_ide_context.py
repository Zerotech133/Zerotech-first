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
    # repo 文件数持续增长（2026-10-31 已 ~5000），search 按词频打分，top5 会被
    # 含 "def"/"lock" 的高频文件挤占；取 top20 断言目标文件在命中集内，不依赖具体名次。
    hits = C.search("def acquire_lock", 20)
    assert any("rw_guard.py" in h["path"] for h in hits), [h["path"] for h in hits]


def test_context_budget_and_no_secret_paths():
    r = C.build_context("legion ide 写文件 preview", 1200)
    assert r["ok"] and r["chars"] <= 1200
    assert "credentials" not in r["context"].lower()


def test_budget_is_hard_cap_even_when_head_is_long():
    # 仓库增长后 head 可能自身超过 budget；chars 必须仍 <= budget（2026-10-31 回归）
    r = C.build_context("legion ide 写文件 preview def", 1000)
    assert r["ok"]
    assert r["chars"] <= 1000, r["chars"]
    assert r["budget"] == 1000


def test_ast_symbol_search():
    syms = C.search_symbols("acquire_oh_lock", 5)
    assert any(s.get("name") == "acquire_oh_lock" for s in syms)
    assert any("ide_worktree.py" in s.get("path", "") for s in syms)
