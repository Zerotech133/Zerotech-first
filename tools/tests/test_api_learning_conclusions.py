# -*- coding: utf-8 -*-
"""W3-3b 学习结论 API（/api/learning/conclusions）契约测试 —— 不依赖 8091 重启。

覆盖：
  1. 过滤：confidence / executability / tactic 单条件与组合，total 与静态 JSON 一致；
  2. 分页：limit/offset 切片、total 不变、越界返回空 items；
  3. 参数校验：非法 confidence/executability/tactic/limit/offset → 400 INVALID_ARGUMENT；
  4. 成功 schema：{ok,total,items,query}，query 回显规范化参数，默认 limit=100；
  5. 静态降级：数据文件缺失 → 503 NOT_READY；
  6. 缓存：数据文件 mtime 变化后重新加载（可选，覆盖 _cache 逻辑）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MOD_PATH = REPO / "portal" / "api_learning_conclusions.py"
STATIC = REPO / "portal" / "static" / "learning_conclusions.json"


def _load():
    spec = importlib.util.spec_from_file_location("api_learning_conclusions_under_test", MOD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


m = _load()


class FakeH:
    def __init__(self):
        self.out = None
        self.code = None

    def _json(self, obj, code=200):
        self.out, self.code = obj, code


def call(qs: str = "") -> tuple[dict, int]:
    h = FakeH()
    m.handle_get(h, "/api/learning/conclusions", qs)
    return h.out, h.code


def raw_data() -> dict:
    return json.loads(STATIC.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. 过滤
# ---------------------------------------------------------------------------
def test_filters_and_total():
    data = raw_data()
    all_items = data["conclusions"]
    for conf in ("高", "中", "低"):
        out, code = call(f"confidence={conf}")
        assert code == 200 and out["ok"] is True
        expect = [c for c in all_items if c.get("confidence") == conf]
        assert out["total"] == len(expect)
        assert all(c and c["confidence"] == conf for c in out["items"])
        assert out["query"]["confidence"] == conf
    for level in ("A", "B", "C"):
        out, code = call(f"executability={level}")
        assert code == 200
        expect = [c for c in all_items
                  if ((c.get("executability") or {}).get("level")) == level]
        assert out["total"] == len(expect)
        assert all(((c.get("executability") or {}).get("level")) == level for c in out["items"])
    for tactic in ("缠论", "均线", "量化"):
        out, code = call(f"tactic={tactic}")
        assert code == 200
        expect = [c for c in all_items
                  if ((c.get("tactic") or {}).get("category")) == tactic]
        assert out["total"] == len(expect)
    # 组合过滤
    out, code = call("confidence=中&executability=B&tactic=均线&limit=5")
    assert code == 200
    expect = [c for c in all_items
              if c.get("confidence") == "中"
              and ((c.get("executability") or {}).get("level")) == "B"
              and ((c.get("tactic") or {}).get("category")) == "均线"]
    assert out["total"] == len(expect)
    assert len(out["items"]) == min(len(expect), 5)


# ---------------------------------------------------------------------------
# 2. 分页
# ---------------------------------------------------------------------------
def test_pagination():
    out, code = call("limit=5")
    assert code == 200 and len(out["items"]) == 5 and out["total"] == 100
    out2, code2 = call("limit=5&offset=5")
    assert code2 == 200 and len(out2["items"]) == 5 and out2["total"] == 100
    ids1 = [c["conclusion_id"] for c in out["items"]]
    ids2 = [c["conclusion_id"] for c in out2["items"]]
    assert not (set(ids1) & set(ids2))
    out3, code3 = call("limit=10&offset=1000")
    assert code3 == 200 and out3["items"] == [] and out3["total"] == 100
    # 默认 limit=100 → 全部 Top100
    out4, _ = call("")
    assert len(out4["items"]) == 100 and out4["total"] == 100


# ---------------------------------------------------------------------------
# 3. 参数校验 400
# ---------------------------------------------------------------------------
def test_invalid_params_400():
    bad = [
        ("confidence=超高", "confidence"),
        ("executability=Z", "executability"),
        ("tactic=玄学", "tactic"),
        ("limit=0", "limit"),
        ("limit=101", "limit"),
        ("limit=abc", "limit"),
        ("offset=-1", "offset"),
        ("offset=abc", "offset"),
    ]
    for qs, field in bad:
        out, code = call(qs)
        assert code == 400, (qs, code)
        assert out["ok"] is False
        assert out["error"]["code"] == "INVALID_ARGUMENT", (qs, out["error"])
        assert field in out["error"]["message"], (qs, out["error"]["message"])


# ---------------------------------------------------------------------------
# 4. 成功 schema
# ---------------------------------------------------------------------------
def test_success_schema_and_query_echo():
    out, code = call("confidence=高&limit=3&offset=1")
    assert code == 200
    assert out["ok"] is True
    for key in ("total", "items", "query", "schema_version"):
        assert key in out, key
    assert out["schema_version"] == "learning_conclusions.v1"
    q = out["query"]
    assert q == {"confidence": "高", "executability": None, "tactic": None,
                 "limit": 3, "offset": 1}
    assert len(out["items"]) <= 3


# ---------------------------------------------------------------------------
# 5. 静态降级 503 + 缓存重载
# ---------------------------------------------------------------------------
def test_static_missing_returns_503(tmp_path, monkeypatch):
    missing = tmp_path / "nope.json"
    old = m.STATIC
    old_cache = dict(m._cache)
    try:
        m.STATIC = missing
        m._cache.update({"mtime": None, "data": None})
        out, code = call("limit=1")
        assert code == 503
        assert out["ok"] is False and out["error"]["code"] == "NOT_READY"
    finally:
        m.STATIC = old
        m._cache.update(old_cache)


def test_load_cache_and_reload(tmp_path):
    src = raw_data()
    p = tmp_path / "lc.json"
    p.write_text(json.dumps(src, ensure_ascii=False), encoding="utf-8")
    old_cache = dict(m._cache)
    try:
        m._cache.update({"mtime": None, "data": None})
        first = m.load_conclusions(p)
        assert first["schema_version"] == "learning_conclusions.v1"
        assert m._cache["data"] is first
        # mtime 变化（写入新内容）→ 重新加载
        src2 = dict(src)
        src2["generated_at"] = "2099-01-01T00:00:00+08:00"
        p.write_text(json.dumps(src2, ensure_ascii=False), encoding="utf-8")
        second = m.load_conclusions(p)
        assert second["generated_at"] == "2099-01-01T00:00:00+08:00"
    finally:
        m._cache.update(old_cache)
