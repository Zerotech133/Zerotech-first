# -*- coding: utf-8 -*-
"""记忆向量升级测试（WO-MEMORY-VECTOR）：用假 embedder，不下载模型。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "memory_vector", str(_WT / "agent_platform" / "memory" / "vector.py"))
V = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(V)


def _fake_embed(texts):
    # 简单确定性向量：字符 bigram 计数哈希到 8 维
    out = []
    for t in texts:
        vec = [0.0] * 8
        for ch in str(t):
            vec[ord(ch) % 8] += 1.0
        out.append(vec)
    return out


def test_available_reports_missing_gracefully(monkeypatch):
    monkeypatch.setattr(V, "_STATUS", {"checked": True, "ok": False,
                                       "reason": "fake-missing"})
    assert V.available() is False
    rep = V.main(["--index"])
    assert rep in (0, 1)


def test_rerank_orders_by_similarity(monkeypatch):
    monkeypatch.setattr(V, "embed", _fake_embed)
    items = [
        {"id": "a", "cue": "完全不相关", "content": "x"},
        {"id": "b", "cue": "假成功 目标对象未修改", "content": "校验目标"},
        {"id": "c", "cue": "别的内容", "content": "y"},
    ]
    out = V.rerank("假成功 目标未修改", items)
    assert out[0]["id"] == "b"
    assert all("vector_score" in x for x in out)
    assert [x["vector_score"] for x in out] == sorted(
        [x["vector_score"] for x in out], reverse=True)


def test_eval_hits_fake(monkeypatch, tmp_path):
    monkeypatch.setattr(V, "embed", _fake_embed)
    monkeypatch.setattr(V, "_connect_plain", lambda db=None: _FakeConn())
    monkeypatch.setattr(V, "EVAL_PATH", _write_queries(tmp_path))
    rep = V.eval_hits()
    assert rep["ok"] and rep["total"] == 2
    assert rep["hit@1"] >= 1 and rep["hit@3"] >= 1


class _FakeRow(dict):
    def __getitem__(self, k):
        return dict.__getitem__(self, k)


class _FakeConn:
    def execute(self, sql, *a):
        class _R:
            def fetchall(self_inner):
                return [
                    _FakeRow(id="semantic:1", cue="假成功 目标对象未修改",
                             content="校验目标对象"),
                    _FakeRow(id="semantic:2", cue="快照假绿 LastTaskResult",
                             content="快照不能当成功"),
                ]
        return _R()

    def close(self):
        pass


def _write_queries(tmp_path):
    p = tmp_path / "q.json"
    p.write_text('{"queries":[{"query":"假成功 目标","expected_any":["假成功"]},'
                 '{"query":"快照假绿","expected_any":["快照假绿"]}]}',
                 encoding="utf-8")
    return p


def test_cosine():
    assert V.cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert V.cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert V.cosine([], [1.0]) == 0.0
