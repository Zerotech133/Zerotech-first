# -*- coding: utf-8 -*-
"""检索性能优化契约测试（离线，不调 8091/LLM）。

覆盖：
  1. kb_read_api 进程级缓存：units/corpus 首次加载、二次命中（loads/hits 计数）；
  2. mtime 感知失效：数据文件内容+mtime 变化后自动重载（行为保持最新）；
  3. warm_cache / cache_stats 结构与规模；
  4. 性能报告 retrieval_perf_20261102.json/md 契约：前后对比字段、P95≤5s 目标达成、MD 段落。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KRA_PATH = REPO / "tools" / "kb_corpus" / "kb_read_api.py"
PERF_JSON = REPO / "research" / "reports" / "retrieval_perf_20261102.json"
PERF_MD = REPO / "research" / "reports" / "retrieval_perf_20261102.md"


def _load():
    spec = importlib.util.spec_from_file_location("kb_read_api_perf_under_test", KRA_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _reset(module):
    module._UNITS_CACHE.update({"sig": None, "units": None, "loads": 0, "hits": 0})
    module._CORPUS_CACHE.update({"sig": None, "corpus": None, "loads": 0, "hits": 0})


def _write_units(root: Path, units: list[dict]) -> Path:
    d = root / "book1"
    d.mkdir(parents=True, exist_ok=True)
    ku = d / "knowledge_units.jsonl"
    ku.write_text("\n".join(json.dumps(u, ensure_ascii=False) for u in units) + "\n", encoding="utf-8")
    return ku


def _write_corpus(path: Path, records: list[dict]) -> Path:
    path.write_text(json.dumps({"records": records}, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. units 缓存 + mtime 失效
# ---------------------------------------------------------------------------
def test_units_cache_and_invalidation(tmp_path):
    kra = _load()
    _reset(kra)
    root = tmp_path / "root"
    ku = _write_units(root, [
        {"book": "b1", "chapter": "ch1", "page_start": 1, "claim": "claim-one", "evidence": "e1"},
        {"book": "b1", "chapter": "ch1", "page_start": 2, "claim": "claim-two", "evidence": "e2"}])
    kra.ROOTS = [(root, "书籍")]
    first = kra.load_units()
    assert len(first) == 2
    stats = kra.cache_stats()["units"]
    assert stats["loads"] == 1 and stats["hits"] == 0
    second = kra.load_units()
    assert len(second) == 2
    stats = kra.cache_stats()["units"]
    assert stats["loads"] == 1 and stats["hits"] == 1
    # 修改数据文件（内容+mtime）→ 自动失效重载
    _write_units(root, [
        {"book": "b1", "chapter": "ch1", "page_start": 1, "claim": "claim-one"},
        {"book": "b1", "chapter": "ch1", "page_start": 2, "claim": "claim-two"},
        {"book": "b1", "chapter": "ch1", "page_start": 3, "claim": "claim-three"}])
    os.utime(ku, (time.time() + 2, time.time() + 2))
    third = kra.load_units()
    assert len(third) == 3
    assert kra.cache_stats()["units"]["loads"] == 2


# ---------------------------------------------------------------------------
# 2. corpus 缓存 + mtime 失效
# ---------------------------------------------------------------------------
def test_corpus_cache_and_invalidation(tmp_path):
    kra = _load()
    _reset(kra)
    corpus = tmp_path / "corpus.json"
    _write_corpus(corpus, [{"source": "docA.pdf", "index": 0, "text": "文本A"},
                           {"source": "docA.pdf", "index": 1, "text": "文本B"}])
    kra.CORPUS_RAG = corpus
    first = kra.load_corpus()
    assert len(first) == 2 and first[0]["type"] == "chunk"
    assert kra.cache_stats()["corpus"]["loads"] == 1
    second = kra.load_corpus()
    assert len(second) == 2
    assert kra.cache_stats()["corpus"]["hits"] == 1
    # 变更文件 → 失效重载
    _write_corpus(corpus, [{"source": "docB.pdf", "index": 0, "text": "新文本"}])
    os.utime(corpus, (time.time() + 2, time.time() + 2))
    third = kra.load_corpus()
    assert len(third) == 1 and third[0]["video_id"] == "docB"
    assert kra.cache_stats()["corpus"]["loads"] == 2


# ---------------------------------------------------------------------------
# 3. warm_cache / cache_stats
# ---------------------------------------------------------------------------
def test_warm_cache_and_stats(tmp_path):
    kra = _load()
    _reset(kra)
    root = tmp_path / "root"
    _write_units(root, [{"book": "b1", "chapter": "ch1", "page_start": 1, "claim": "c1"}])
    corpus = tmp_path / "corpus.json"
    _write_corpus(corpus, [{"source": "d.pdf", "index": 0, "text": "t"}])
    kra.ROOTS = [(root, "书籍")]
    kra.CORPUS_RAG = corpus
    info = kra.warm_cache()
    assert info["units"] == 1 and info["corpus"] == 1
    assert info["cache_stats"]["units"]["loaded"] is True
    assert info["cache_stats"]["corpus"]["loaded"] is True
    # 再次 warm 全部命中
    info2 = kra.warm_cache()
    assert info2["cache_stats"]["units"]["hits"] >= 1
    assert info2["cache_stats"]["corpus"]["hits"] >= 1


# ---------------------------------------------------------------------------
# 4. 性能报告契约
# ---------------------------------------------------------------------------
def test_perf_report_contract():
    assert PERF_JSON.exists(), f"缺少性能报告：{PERF_JSON}"
    data = json.loads(PERF_JSON.read_text(encoding="utf-8"))
    assert data["kind"] == "retrieval_perf"
    for key in ("diagnosis", "optimizations", "restart", "after", "before_after", "target", "conclusion"):
        assert key in data, key
    assert data["diagnosis"]["slow_points"]
    assert data["optimizations"] and data["optimizations"][0]["file"].endswith("kb_read_api.py")
    before = data["before_after"]
    # 目标：10 并发 P95 ≤5s 且优化后 P95 显著低于优化前
    target = data["target"]
    assert target["achieved"] is True
    assert target["actual_p95_seconds"] <= 5.0
    assert before["concurrent_p95_seconds"]["after"] <= 5.0
    assert before["concurrent_p95_seconds"]["after"] < before["concurrent_p95_seconds"]["before"]
    assert before["serial_p50_seconds"]["after"] < before["serial_p50_seconds"]["before"]
    assert before["server_5xx"]["after"] == 0
    md = PERF_MD.read_text(encoding="utf-8")
    for anchor in ("结论", "诊断：慢点拆解", "优化点", "前后对比", "剩余瓶颈与调参"):
        assert anchor in md, anchor
