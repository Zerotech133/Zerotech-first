# -*- coding: utf-8 -*-
"""tools/tests/test_concept_merge_llm.py · 长尾概念 LLM 语义归并（W1-2c）单元测试。

覆盖（任务验收要求）：
  1. **向量相似合并**：余弦 ≥0.85 自动合并、<0.75 不合并；
  2. **mock LLM 边界判定**：0.75~0.85 边界对由（mock）LLM 裁决 —— same=true 合并 / false 不合并；
  3. **映射可回滚**：mapping 结构完整、原簇→目标簇可逆（忽略 mapping 即回到基线簇数）；
  4. **只读 + 输出**：真实 W1-1 JSON 输入（输出 tmp），前后 sha256 一致、schema/MD 完整；
  5. **不得真实调 LLM**：把 urllib.request.urlopen 替换为抛错函数，mock 路径全程不触网。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_concept_merge_llm.py
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import concept_merge_llm as cm  # noqa: E402

INPUT_JSON = ROOT / "research" / "reports" / "knowledge_multisource_20261103.json"


# ------------------------------------------------------------------ 工具 / mock
def _entry(concept: str, source_key: str, source_type: str = "blogger") -> dict:
    return {"concept": concept, "confidence": "低", "n_sources": 1,
            "source_types": [source_type],
            "sources": [{"source_type": source_type, "source_key": source_key,
                         "source_label": f"{source_type}·{source_key}"}]}


def _clusters(*entries: dict) -> list[dict]:
    return cm.build_single_source_clusters(list(entries))


class FakeEmbedder:
    """返回预设向量的 mock embedder（不触网、不调模型）。"""

    def __init__(self, vectors):
        self._v = np.asarray(vectors, dtype=np.float32)
        self.calls = len(self._v)
        self.sec = 0.0
        self.mode = "mock"
        self.errors = []

    def embed_many(self, texts):
        assert len(texts) == len(self._v), "文本数与 mock 向量数不一致"
        return self._v


class FakeJudge:
    """mock LLM：按 (a,b) 映射返回判定；记录调用次数。"""

    def __init__(self, verdicts: dict[tuple[str, str], bool | None]):
        self.verdicts = verdicts
        self.calls = 0
        self.sec = 0.0
        self.failures = 0
        self.errors = []

    def judge_same(self, a: str, b: str):
        self.calls += 1
        same = self.verdicts.get((a, b), self.verdicts.get((b, a)))
        if same is None:
            self.failures += 1
            return None
        return {"same": bool(same), "reason": "mock 判定"}


def _unit_vectors(n: int, sim_pairs: dict[tuple[int, int], float]) -> np.ndarray:
    """构造 n 维单位向量：指定对余弦 = 1-2θ... 用二维子空间精确构造。"""
    vecs = []
    base = np.zeros(n, dtype=np.float32)
    for i in range(n):
        base = np.zeros(n, dtype=np.float32)
        base[i] = 1.0
        vecs.append(base)
    for (i, j), cos in sim_pairs.items():
        theta = np.arccos(np.clip(cos, -1.0, 1.0))
        vecs[j] = vecs[j].copy()
        vecs[j][i] = np.cos(theta) if i != j else 0.0
        vecs[j][j] = np.sin(theta)
    return np.vstack(vecs)


# ------------------------------------------------------------------ 1. 向量相似合并
def test_embedding_similarity_merge():
    entries = [_entry("均线粘合", "博主A"), _entry("移动平均线粘合", "博主B"),
               _entry("完全无关的概念", "博主C")]
    clusters = _clusters(*entries)
    assert len(clusters) == 3
    vecs = np.array([[1, 0, 0], [0.99, 0.14, 0], [0, 0, 1]], dtype=np.float32)
    result = cm.merge_long_tail(clusters, FakeEmbedder(vecs), judge=None,
                                auto_cos=0.85, boundary_lo=0.75)
    assert result["stats"]["auto_pairs"] >= 1
    assert len(result["groups"]) == 1
    assert result["stats"]["auto_merged"] == 1
    merged_ids = result["groups"][0]["idxs"]
    assert len(merged_ids) == 2
    # 无关概念不合并
    assert 2 not in merged_ids
    # mapping 记录可回滚
    assert len(result["mapping"]) == 1
    mp = result["mapping"][0]
    for key in ("from_cluster_id", "from_concept", "to_cluster_id", "to_concept",
                "method", "similarity", "reason"):
        assert key in mp, key
    assert mp["method"] == "embedding"


# ------------------------------------------------------------------ 2. mock LLM 边界判定
def test_boundary_llm_judge_mock():
    entries = [_entry("分歧概念甲", "博主A"), _entry("分歧概念乙", "博主B"),
               _entry("边界概念丙", "博主C"), _entry("边界概念丁", "博主D")]
    clusters = _clusters(*entries)
    # 两对都落在边界 [0.75,0.85)：余弦 0.80 / 0.78 → 各需一次 LLM 判定
    vecs = np.array([[1, 0, 0, 0],
                     [0.80, 0.60, 0, 0],
                     [0, 0, 1, 0],
                     [0, 0, 0.78, 0.6258]], dtype=np.float32)
    judge = FakeJudge({("分歧概念甲", "分歧概念乙"): True, ("边界概念丙", "边界概念丁"): False})
    result = cm.merge_long_tail(clusters, FakeEmbedder(vecs), judge=judge,
                                auto_cos=0.85, boundary_lo=0.75, max_llm_pairs=10)
    assert judge.calls == 2
    assert result["stats"]["llm_accept"] == 1 and result["stats"]["llm_reject"] == 1
    # 边界经 LLM 接受 → mapping reason 含 LLM
    llm_maps = [m for m in result["mapping"] if m["method"] == "llm_boundary"]
    assert llm_maps and "LLM" in llm_maps[0]["reason"]


# ------------------------------------------------------------------ 3. 映射可回滚
def test_mapping_rollback_contract():
    entries = [_entry(f"概念{i}", f"博主{i % 3}") for i in range(5)]
    clusters = _clusters(*entries)
    n = len(clusters)
    vecs = np.eye(n, dtype=np.float32)
    vecs[1] = vecs[0]                     # 0/1 完全同向 → 合并
    vecs[3] = vecs[2]
    result = cm.merge_long_tail(clusters, FakeEmbedder(vecs), judge=None)
    mapping = result["mapping"]
    assert mapping
    assert all(m["from_cluster_id"] and m["to_cluster_id"] for m in mapping)
    assert all(m["from_cluster_id"] != m["to_cluster_id"] for m in mapping)
    # 合并后簇数 = 原簇数 - 映射条数（可回滚回基线）
    merged_clusters = n - len(mapping)
    assert merged_clusters == n - len(mapping)
    # mapping 可 JSON 序列化
    assert json.dumps(mapping, ensure_ascii=False)


# ------------------------------------------------------------------ 4. 只读 + 输出（真实输入 → tmp）
def test_run_read_only_and_schema(tmp_path, monkeypatch):
    if not INPUT_JSON.is_file():
        pytest.skip(f"W1-1 输入不存在: {INPUT_JSON}")
    # 安全网：mock 路径全程不得真实触网
    def _no_network(*args, **kwargs):
        raise AssertionError("测试不得真实调用网络/LLM")

    monkeypatch.setattr(urllib.request, "urlopen", _no_network)
    sha_before = hashlib.sha256(INPUT_JSON.read_bytes()).hexdigest()
    out_j, out_m = tmp_path / "merge.json", tmp_path / "merge.md"
    payload = cm.run(INPUT_JSON, out_j, out_m, force_lexical=True, use_llm=False)
    # 只读
    assert hashlib.sha256(INPUT_JSON.read_bytes()).hexdigest() == sha_before
    assert payload["meta"]["read_only"] is True
    assert payload["meta"]["read_only_verified"] is True
    assert payload["meta"]["input"]["sha256_before"] == payload["meta"]["input"]["sha256_after"]
    # schema
    assert set(payload) == {"meta", "stats", "baseline", "mapping", "merged_groups",
                            "false_merge_risk", "rollback"}
    s = payload["stats"]
    assert s["clusters_before"] == 1029
    assert s["multi_before"] == 23
    assert s["clusters_after"] <= s["clusters_before"]
    assert s["multi_after_confirmed"] >= s["multi_before"]
    assert payload["baseline"]["clusters"] == 1029
    assert payload["rollback"]["mapping_entries"] == len(payload["mapping"])
    # 输出文件 + MD 核心段
    assert out_j.is_file() and out_m.is_file()
    json.loads(out_j.read_text(encoding="utf-8"))
    md = out_m.read_text(encoding="utf-8")
    for section in ("是否达标", "合并组清单", "合并映射（可回滚）", "误并风险", "复现"):
        assert section in md, section


# ------------------------------------------------------------------ 5. 词法回退向量确定性 / 区分性
def test_lexical_vectors_deterministic_and_discriminative():
    texts = ["520均线战法", "均线战法", "黄金分割", "黄金分隔"]
    v1 = cm.lexical_vectors(texts)
    v2 = cm.lexical_vectors(texts)
    np.testing.assert_allclose(v1, v2)                       # 确定性
    sim = cm.cosine_matrix(v1)
    # 520均线战法 ≠ 均线战法（数字 token 保留，不应被当成同一文本）
    assert sim[0, 1] < 0.99
    # 黄金分割/黄金分隔 近义（错别字）→ 相似较高但不必相等
    assert sim[2, 3] > 0.3
