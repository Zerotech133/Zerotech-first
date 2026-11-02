# -*- coding: utf-8 -*-
"""W1-4 有效结论库 v1 契约测试（合成输入 + 真实数据冒烟）。

覆盖：
  1. 置信分级规则（高≥2独立来源一致 / 中≥2源措辞异 / 低单源）；
  2. 多源聚合（簇合并、独立来源去重、同博主多文件不算独立源、候选 pending 标记）；
  3. Top100 排序与输出 schema（JSON+MD 落盘、数据契约字段）；
  4. 只读校验（输入文件 sha256 前后一致，写输出不碰输入）；
  5. 冲突检测（方向相反单列待仲裁）；
  6. 8 类战法与可执行性 A/B/C 映射。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LIB_PATH = REPO / "tools" / "kb_corpus" / "learning_conclusions_lib.py"


def _load_lib():
    spec = importlib.util.spec_from_file_location("learning_conclusions_lib_under_test", LIB_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


lib = _load_lib()


# --------------------------------------------------------------------------- #
# 合成输入
# --------------------------------------------------------------------------- #
def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _make_fixture(tmp_path: Path) -> dict[str, object]:
    concepts = []
    # 1) 多源一致 → 高置信（两条来源答案相同）
    concepts.append({
        "concept": "倍量柱",
        "n_sources": 2,
        "confidence": "高",
        "consistency": {"max_similarity": 1.0, "consistent": True},
        "conflicts": [],
        "sources": [
            {"source_type": "blogger", "source_key": "博主A", "source_label": "博主·博主A",
             "answer_snippets": ["倍量柱是成交量放大一倍的量柱，常作为资金进场的观察信号。"]},
            {"source_type": "blogger", "source_key": "博主B", "source_label": "博主·博主B",
             "answer_snippets": ["倍量柱是成交量放大一倍的量柱，常作为资金进场的观察信号。"]},
        ],
    })
    # 2) 多源不一致 → 中置信 → 仲裁队列
    concepts.append({
        "concept": "均线粘合",
        "n_sources": 2,
        "confidence": "中",
        "consistency": {"max_similarity": 0.0, "consistent": False},
        "conflicts": [],
        "sources": [
            {"source_type": "blogger", "source_key": "博主A", "source_label": "博主·博主A",
             "answer_snippets": ["均线粘合表示多周期成本收敛，等待放量选方向。"]},
            {"source_type": "blogger", "source_key": "博主B", "source_label": "博主·博主B",
             "answer_snippets": ["均线粘合说明趋势进入震荡，应当降低仓位并等待突破。"]},
        ],
    })
    # 3) 方向冲突（同簇两来源，措辞接近但方向相反）→ conflicts 待仲裁
    shared = ("倍量柱战法在出现倍量柱信号之后应当等待次日收盘确认再决定操作方向"
              "并根据趋势强弱与量能配合情况调整持仓比例")
    concepts.append({
        "concept": "倍量柱战法方向",
        "n_sources": 2,
        "confidence": "中",
        "consistency": {"max_similarity": 0.2, "consistent": False},
        "conflicts": [],
        "sources": [
            {"source_type": "blogger", "source_key": "博主C", "source_label": "博主·博主C",
             "answer_snippets": [shared + "买入加仓，上涨趋势看多继续持有等待新高。"]},
            {"source_type": "blogger", "source_key": "博主D", "source_label": "博主·博主D",
             "answer_snippets": [shared + "卖出减仓，下跌趋势看空应当回避风险。"]},
        ],
    })
    # 4) 单源低置信 ×130（用于 Top100 截断与排序）
    for i in range(130):
        concepts.append({
            "concept": f"单源概念{i:03d}",
            "n_sources": 1,
            "confidence": "低",
            "consistency": {"max_similarity": 0.0, "consistent": False},
            "conflicts": [],
            "sources": [{"source_type": "blogger", "source_key": f"单源博主{i:03d}",
                         "source_label": f"博主·单源博主{i:03d}",
                         "answer_snippets": ["放量突破平台后观察量能变化，可作为研究线索。"]}],
        })
    multisource = {"meta": {"report": "knowledge_multisource"}, "multi_source_concepts": [],
                   "concepts": concepts, "conflicts": []}
    synonyms = {"meta": {}, "clusters": [
        {"cluster_id": "CM-0001", "display": "倍量柱", "n_members": 2,
         "members": [{"concept": "倍量柱", "cleaned": "倍量柱"}, {"concept": "倍量", "cleaned": "倍量"}],
         "merged_keys": ["倍量", "倍量柱"], "known_sources": [], "status": "多源-确认",
         "confidence_after": "高"},
        {"cluster_id": "CM-0002", "display": "均线粘合", "n_members": 1,
         "members": [{"concept": "均线粘合", "cleaned": "均线粘合"}],
         "merged_keys": ["均线粘合"], "known_sources": [], "status": "多源-确认",
         "confidence_after": "中"},
        {"cluster_id": "CM-0003", "display": "倍量柱战法方向", "n_members": 1,
         "members": [{"concept": "倍量柱战法方向", "cleaned": "倍量柱战法方向"}],
         "merged_keys": [], "known_sources": [], "status": "单源", "confidence_after": "低"},
    ], "merged_multi_source": []}
    cards = {"generated_at": "2026-11-03T00:00:00+08:00", "cards": [
        {"q": "什么是「倍量柱」？", "a": "倍量柱是成交量放大一倍的量柱，常作为资金进场的观察信号。",
         "concept": "倍量柱", "source": "视频号学习_8博主 · 20261103_博主A.md · L1",
         "topic": "量学/涨停", "evidence": "博主A L1"},
    ]}
    candidates = {"candidates": [
        {"concept": "倍量柱", "a": "倍量柱是成交量放大一倍的量柱，常作为资金进场的观察信号。",
         "source": "视频号学习_8博主 · 20261103_博主B.md · L2", "blogger": "博主B", "theme": "量学/涨停"},
        # 同博主多文件（括号序号）不得算独立来源
        {"concept": "均线粘合", "a": "均线粘合表示多周期成本收敛，等待放量选方向。",
         "source": "视频号学习_8博主 · 20261103_博主A(1).md · L9", "blogger": "博主A", "theme": "均线"},
        # 元数据噪声应被过滤
        {"concept": "> 共 23 条 · 由 faster-whisper 转写", "a": "> 共 23 条 · 由 faster-whisper 转写",
         "source": "视频号学习_8博主 · 20261103_噪声.md · L1", "blogger": "噪声", "theme": "其他"},
    ]}
    return {
        "multisource": _write_json(tmp_path / "multisource.json", multisource),
        "synonyms": _write_json(tmp_path / "synonyms.json", synonyms),
        "cards": _write_json(tmp_path / "cards.json", cards),
        "candidate_paths": [_write_json(tmp_path / "candidates.json", candidates)],
        "out_json": tmp_path / "out" / "conclusions.json",
        "out_md": tmp_path / "out" / "conclusions.md",
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# 1. 置信分级
# --------------------------------------------------------------------------- #
def test_confidence_grading_rule():
    assert lib.grade_confidence(1, None) == "低"
    assert lib.grade_confidence(0, None) == "低"
    assert lib.grade_confidence(2, True) == "高"
    assert lib.grade_confidence(3, True) == "高"
    assert lib.grade_confidence(2, False) == "中"
    assert lib.grade_confidence(2, None) == "中"


# --------------------------------------------------------------------------- #
# 2. 来源解析 / 独立来源去重
# --------------------------------------------------------------------------- #
def test_parse_source_and_independence():
    # 同博主 `xxx(1).md` 与 `xxx.md` 归一为同一来源
    a = lib.parse_source("视频号学习_8博主 · 20261103_一修论市.md · L10")
    b = lib.parse_source("视频号学习_8博主 · 20261103_一修论市(1).md · L99")
    assert a["source_type"] == b["source_type"] == "blogger"
    assert a["source_key"] == b["source_key"] == "一修论市"
    # 研报编号/书 解析
    rp = lib.parse_source("《rp_AP202610031830126542》 太平洋证券 证券研究报告 p1-4")
    assert rp["source_type"] == "research_report" and rp["source_key"] == "rp_AP202610031830126542"
    pit = lib.extract_dates([rp["raw"]])
    assert pit["verifiable"] is True and pit["as_of"] == "20261003"
    book = lib.parse_source("《涨停密码(2)》 前言 p9-10")
    assert book["source_type"] == "book" and book["source_key"] == "涨停密码(2)"


# --------------------------------------------------------------------------- #
# 3. 8 类战法 / 可执行性映射
# --------------------------------------------------------------------------- #
def test_tactic_and_executability_mapping():
    assert lib.classify_tactic("均线粘合后多头排列")["category"] == "均线"
    assert lib.classify_tactic("倍量柱与缩量回调，筹码集中")["category"] == "量价"
    assert lib.classify_tactic("MACD 顶背离")["category"] == "指标"
    assert lib.classify_tactic("SNPO 超节点服务器方案支持 GPU 互联")["category"] == "未分类"
    # 可执行性：日线 A / 分钟 B / 人工 C
    assert lib.classify_executability("倍量柱在日线放量突破")["level"] == "A"
    assert lib.classify_executability("30 分钟级别中枢出现背驰")["level"] == "B"
    assert lib.classify_executability("交易心态与纪律决定长期收益")["level"] == "C"


# --------------------------------------------------------------------------- #
# 4. 多源聚合 + 冲突 + 候选过滤
# --------------------------------------------------------------------------- #
def test_multi_source_aggregation_and_conflicts(tmp_path):
    fixture = _make_fixture(tmp_path)
    result = lib.build_library(
        multisource_path=fixture["multisource"], synonyms_path=fixture["synonyms"],
        cards_path=fixture["cards"], candidate_paths=fixture["candidate_paths"],
        out_json=fixture["out_json"], out_md=fixture["out_md"], top_n=100, write=False,
    )
    by_concept = {item["concept"]: item for item in result["conclusions"]}
    # 全量里有，Top100 里也应有高置信条目
    assert result["read_only_verified"] is True
    top_by_concept = by_concept
    bell = top_by_concept["倍量柱"]
    assert bell["confidence"] == "高"
    assert bell["n_sources"] == 2
    assert set(bell["bloggers"]) == {"博主A", "博主B"}
    assert bell["executability"]["level"] == "A"
    assert bell["tactic"]["category"] == "量价"
    assert bell["claim_type"].startswith("规律型")
    # 同博主多文件（A 与 A(1)）不得抬高来源数：均线粘合仍为 2 源（A、B）且措辞不一 → 中
    smooth = top_by_concept["均线粘合"]
    assert smooth["n_sources"] == 2
    assert smooth["confidence"] == "中"
    assert result["meta"]["arbitration_queue_count"] >= 1
    # 方向相反 → 冲突单列待仲裁
    assert len(result["conflicts"]) >= 1
    conflict = result["conflicts"][0]
    assert conflict["status"] == "待仲裁" and conflict["type"] == "方向相反"
    # 元数据噪声候选被过滤
    assert all("faster-whisper" not in item["concept"] for item in result["conclusions"])
    # 全量输出里无结论缺少关键字段
    for item in result_conclusions_all(result):
        for key in ("conclusion_id", "concept", "text", "confidence", "n_sources", "sources",
                    "confidence_reason", "consistency", "pit", "tactic", "executability",
                    "review_status", "claim_type", "score"):
            assert key in item, (item.get("concept"), key)


def result_conclusions_all(result):
    """write=False 时 conclusions 已是 Top 切片；全量需从 meta 计数看，此处只看 Top。"""
    return result["conclusions"]


# --------------------------------------------------------------------------- #
# 5. Top100 排序 / 输出 schema / 只读
# --------------------------------------------------------------------------- #
def test_top100_sorted_and_outputs(tmp_path):
    fixture = _make_fixture(tmp_path)
    inputs = [fixture["multisource"], fixture["synonyms"], fixture["cards"], *fixture["candidate_paths"]]
    hashes_before = {str(p): _sha256(p) for p in inputs}
    result = lib.build_library(
        multisource_path=fixture["multisource"], synonyms_path=fixture["synonyms"],
        cards_path=fixture["cards"], candidate_paths=fixture["candidate_paths"],
        out_json=fixture["out_json"], out_md=fixture["out_md"], top_n=100, write=True,
    )
    top = result["conclusions"]
    assert len(top) == 100  # 截断到 Top100
    assert result["meta"]["conclusions_total"] > 100  # 全量大于 Top100
    scores = [item["score"] for item in top]
    assert scores == sorted(scores, reverse=True)  # 排序稳定
    assert [item["conclusion_id"] for item in top] == [f"LC-{i:04d}" for i in range(1, 101)]
    assert top[0]["confidence"] == "高"  # 置信优先
    # 输出文件
    assert fixture["out_json"].exists() and fixture["out_md"].exists()
    payload = json.loads(fixture["out_json"].read_text(encoding="utf-8"))
    assert payload["schema_version"] == lib.SCHEMA_VERSION
    assert payload["task"] == "W1-4"
    assert payload["read_only_verified"] is True
    assert payload["meta"]["top_confidence_dist"]["高"] >= 1
    for key in ("conclusion_id", "concept", "text", "confidence", "executability", "tactic",
                "n_sources", "sources", "pit", "review_status", "score"):
        assert key in payload["conclusions"][0], key
    contract = payload["consumer_contract"]
    assert contract["suggested_endpoint"] == "/api/learning/conclusions"
    assert "conclusion_id" in contract["required_fields"]
    md = fixture["out_md"].read_text(encoding="utf-8")
    assert "Top100" in md and "数据契约" in md and "待仲裁" in md
    # 只读：输入 sha256 前后一致
    for path in inputs:
        assert _sha256(path) == hashes_before[str(path)], f"输入被改动：{path}"


# --------------------------------------------------------------------------- #
# 6. 真实数据冒烟（输入存在才跑）
# --------------------------------------------------------------------------- #
def test_real_data_build_readonly():
    required = [lib.DEFAULT_INPUTS["multisource"], lib.DEFAULT_INPUTS["synonyms"], lib.DEFAULT_INPUTS["cards"]]
    if not all(Path(p).exists() for p in required):
        pytest.skip("W1-1/W1-2/学习卡输入不存在，跳过真实数据冒烟")
    result = lib.build_library(write=False, top_n=100)
    assert result["read_only_verified"] is True
    assert result["meta"]["conclusions_total"] > 100
    top = result["conclusions"]
    assert len(top) == 100
    top_conf = result["meta"]["top_confidence_dist"]
    assert sum(top_conf.values()) == 100
    assert set(top_conf) <= {"高", "中", "低"}
    for item in top:
        assert item["confidence"] in ("高", "中", "低")
        assert item["executability"]["level"] in ("A", "B", "C")
        assert item["tactic"]["category"] in {
            "缠论", "均线", "指标", "量价", "形态", "风控", "心理", "量化", "未分类"}
        assert item["pit"]["precision"] in ("day", "none")
        assert isinstance(item["sources"], list)
    assert isinstance(result["conflicts"], list)
    assert isinstance(result["arbitration_queue"], list)
