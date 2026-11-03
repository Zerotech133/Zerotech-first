# -*- coding: utf-8 -*-
"""concept_merge_cloud 测试：候选对/判定/合并多源/只读（mock 云端，不触网）。"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cmc", REPO / "tools/kb_corpus/concept_merge_cloud.py")
cmc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cmc)


def mk(cid, concept, sources):
    return {"concept": concept, "concept_key": cid, "n_sources": len(sources),
            "sources": [{"source_key": s, "source_type": "blogger"} for s in sources]}


def test_candidate_pairs_domain_terms():
    concepts = [mk("a", "均线粘合", {"x"}), mk("b", "均线缠绕", {"y"}), mk("c", "MACD背离", {"z"})]
    pairs = cmc.candidate_pairs(concepts, limit=10)
    keys = [(p["a"], p["b"]) for p in pairs]
    assert ("均线粘合", "均线缠绕") in keys
    assert all(p["score"] >= 0 for p in pairs)


def test_candidate_pairs_limit_and_cross_first():
    concepts = [mk(f"k{i}", f"中枢{i}", {f"s{i%3}"}) for i in range(20)]
    pairs = cmc.candidate_pairs(concepts, limit=5)
    assert len(pairs) <= 5
    assert pairs[0]["cross_source"] is True


class FakeClient:
    def __init__(self, verdicts):
        self.verdicts = verdicts
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        user = messages[-1]["content"]
        n = len([ln for ln in user.splitlines() if ln.strip()])
        pairs = [{"i": i, "same": self.verdicts[i]} for i in range(n)]
        return json.dumps({"pairs": pairs}, ensure_ascii=False)


def test_judge_pairs_batching():
    pairs = [{"a": "A", "b": "B"} for _ in range(25)]
    fake = FakeClient([True] * 25)
    out = cmc.judge_pairs(fake, pairs, batch=10)
    assert len(out) == 25 and all(out)
    assert fake.calls == 3


def test_merge_multi_source():
    concepts = [mk("a", "均线粘合", {"s1", "s2"}), mk("b", "均线缠绕", {"s2", "s3"}),
                mk("c", "中枢", {"s9"})]
    pairs = [{"a": "均线粘合", "b": "均线缠绕", "ka": "a", "kb": "b", "reason": "term:均线", "score": 0.8}]
    res = cmc.merge(concepts, pairs, [True])
    assert res["clusters_after"] == 2
    assert res["multi_source_after"] >= 1
    assert res["merged_pairs"]


def test_merge_no_verdict_no_change():
    concepts = [mk("a", "A", {"s1"}), mk("b", "B", {"s2"})]
    pairs = [{"a": "A", "b": "B", "ka": "a", "kb": "b", "reason": "x", "score": 0.1}]
    res = cmc.merge(concepts, pairs, [False])
    assert res["clusters_after"] == 2
    assert res["merged_pairs"] == []


def test_source_readonly():
    src = REPO / "research/reports/knowledge_multisource_20261103.json"
    if not src.exists():
        pytest.skip("source missing")
    import hashlib
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    data = json.loads(src.read_text(encoding="utf-8"))
    cmc.candidate_pairs(data["concepts"], limit=20)
    after = hashlib.sha256(src.read_bytes()).hexdigest()
    assert before == after
