# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import memory_rag as M  # noqa: E402


def test_index_and_search(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# 审批\nL2 危险操作必须老田批准。\n", encoding="utf-8")
    (docs / "b.md").write_text("# 其他\n今天天气不错。\n", encoding="utf-8")
    idx = tmp_path / "idx.json"
    r = M.index(roots=[str(docs)], index_path=idx)
    assert r["ok"] and r["files"] == 2 and r["chunks"] >= 2
    res = M.search("L2 审批 老田", index_path=idx)
    assert res["ok"] and res["hits"]
    assert res["hits"][0]["path"].endswith("a.md")
    st = M.stats(index_path=idx)
    assert st["files"] == 2


def test_missing_index(tmp_path):
    r = M.search("x", index_path=tmp_path / "none.json")
    assert r["ok"] is False
