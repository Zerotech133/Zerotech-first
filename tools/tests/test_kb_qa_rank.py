# -*- coding: utf-8 -*-
"""kb_qa_rank（检索 @1 重排评估）契约测试 —— 不调 LLM、不改 kb_qa.py。

覆盖：
  1. 特征构建与重排纯函数：分数归一化/来源类型/concept 匹配/同书同章一致性；权重可调、稳定排序；
  2. 指标纯函数：@1/@5/来源排名/MRR（分位、未命中、空输入）；
  3. 汇总统计：rank/source_rank 两种行格式、命中率与平均排名；
  4. 全链路 schema（注入 mock 检索）：baseline/rerank/source_prior/delta/questions/recommendation；
  5. 空数据容错、未知 preset 报错、JSON+MD 落盘；CLI --help。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RANK_PATH = REPO / "tools" / "kb_corpus" / "kb_qa_rank.py"


def _load_rank():
    spec = importlib.util.spec_from_file_location("kb_qa_rank_under_test", RANK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rk = _load_rank()

QUESTIONS = [
    {"id": "q1", "question": "M9 级覆铜板对 PCB 价值量的影响如何？", "concept": "M9 级覆铜板",
     "theme": "AI算力/半导体", "source_unit_id": "u_src_1", "book": "book_a", "chapter": "第一章"},
    {"id": "q2", "question": "固态电池的产业化进度与量产时间表如何？", "concept": "固态电池",
     "theme": "新能源/电池", "source_unit_id": "u_src_2", "book": "book_b", "chapter": "第二章"},
    {"id": "q3", "question": "资本市场改革的政策演进与市场影响如何？", "concept": "资本市场改革",
     "theme": "金融/资本市场", "source_unit_id": "u_src_3", "book": "book_c", "chapter": "第三章"},
]


def hit(uid, score, concept="", book="", chapter="", origin="units"):
    return {"unit_id": uid, "fused_score": score, "concept": concept, "book": book,
            "chapter": chapter, "origin": origin, "page_start": 1,
            "text": concept + " " + book}


def make_pool(src="u_src_1", concept="M9 级覆铜板"):
    return [
        hit("u_other_1", 1.1, concept="无关概念", book="book_x", chapter="X章"),
        hit(src, 1.09, concept=concept, book="book_a", chapter="第一章"),
        hit("u_other_2", 0.5, concept=concept, book="book_a", chapter="第一章"),
        hit("c_chunk", 0.4, concept="", book="video_1", chapter="", origin="corpus"),
    ]


# ---------------------------------------------------------------------------
# 1. 特征 + 重排
# ---------------------------------------------------------------------------
def test_build_features_fields():
    pool = make_pool()
    feats = rk.build_features(pool[1], "M9 级覆铜板对 PCB 价值量的影响？", pool)
    for key in ("score_norm", "origin_unit", "concept_match", "book_coherence", "chapter_coherence"):
        assert key in feats, key
    assert 0.0 <= feats["score_norm"] <= 1.0
    assert feats["origin_unit"] == 1.0
    assert feats["concept_match"] >= 0.5  # 概念精确匹配
    chunk_feats = rk.build_features(pool[3], "M9 级覆铜板对 PCB 价值量的影响？", pool)
    assert chunk_feats["origin_unit"] == 0.0
    # 来源先验特征（评估用）
    prior_feats = rk.build_features(pool[1], "问题", pool, question_meta=QUESTIONS[0], source_prior=True)
    assert prior_feats["same_unit"] == 1.0 and prior_feats["same_book"] == 1.0
    assert prior_feats["same_chapter"] == 1.0


def test_rerank_respects_weights_and_is_stable():
    pool = make_pool()
    qtext = "M9 级覆铜板对 PCB 价值量的影响如何？"
    score_heavy = rk.rerank(pool, qtext, {"score": 1.0})
    assert score_heavy[0]["unit_id"] == "u_other_1"  # 最高原始分
    concept_heavy = rk.rerank(pool, qtext, {"concept": 1.0, "score": 0.0})
    assert concept_heavy[0]["rerank_rank"] == 1
    assert concept_heavy[0]["features"]["concept_match"] >= 0.5  # 概念匹配的候选排到最前
    # 稳定排序 & 带重排分/特征
    for item in concept_heavy:
        assert "rerank_score" in item and "features" in item
    assert [x["rerank_rank"] for x in concept_heavy] == list(range(1, len(concept_heavy) + 1))
    assert rk.rerank([], qtext) == []


# ---------------------------------------------------------------------------
# 2. 指标 / 3. 汇总
# ---------------------------------------------------------------------------
def test_rank_metrics():
    ranked = [hit("x", 1), hit("u_src_1", 0.9), hit("y", 0.8)]
    m1 = rk.rank_metrics(ranked, "u_src_1", topk=5)
    assert m1["source_rank"] == 2 and m1["hit_at_1"] is False and m1["hit_at_5"] is True
    assert m1["mrr"] == pytest.approx(0.5)
    top = rk.rank_metrics([hit("u_src_1", 1)], "u_src_1")
    assert top["hit_at_1"] is True and top["source_rank"] == 1 and top["mrr"] == 1.0
    miss = rk.rank_metrics(ranked, "u_src_1", topk=1)
    assert miss["source_rank"] is None and miss["hit_at_5"] is False and miss["mrr"] == 0.0
    assert rk.rank_metrics([], "x")["source_rank"] is None


def test_summarize_metrics_accepts_rank_and_source_rank():
    rows = [{"rank": 1}, {"source_rank": 2}, {"source_rank": None}]
    s = rk.summarize_metrics(rows)
    assert s["total"] == 3 and s["hit_at_1"] == 1 and s["hit_at_5"] == 2
    assert s["hit_rate_at_1"] == pytest.approx(1 / 3, abs=1e-3)
    assert s["miss_source"] == 1
    assert s["avg_source_rank"] == 1.5
    empty = rk.summarize_metrics([])
    assert empty["total"] == 0 and empty["hit_rate_at_1"] == 0.0
    assert empty["avg_source_rank"] is None


# ---------------------------------------------------------------------------
# 4. 全链路 schema（mock 检索）
# ---------------------------------------------------------------------------
def test_run_eval_schema_with_mock_search(tmp_path):
    qfile = tmp_path / "qs.json"
    qfile.write_text(json.dumps({"questions": QUESTIONS}, ensure_ascii=False), encoding="utf-8")

    def mock_search(question, topk=10):
        if "覆铜板" in question:
            return make_pool("u_src_1")
        if "固态电池" in question:
            return [hit("u_src_2", 0.9, concept="固态电池", book="book_b"),
                    hit("x", 1.0, concept="其他"), hit("y", 0.5)]
        return [hit("x", 1.0, concept="其他")]  # q3 来源未进池

    payload = rk.run_eval(qfile, topk=5, pool_size=10, search_fn=mock_search)
    assert payload["kind"] == "kb_qa_rank_eval"
    assert payload["config"]["no_llm"] is True
    assert payload["config"]["chosen_preset"] in rk.WEIGHT_PRESETS
    for key in ("baseline", "rerank", "source_prior_eval", "delta", "questions", "recommendation"):
        assert key in payload, key
    base = payload["baseline"]["summary"]
    after = payload["rerank"]["summary"]
    assert base["total"] == 3 and after["total"] == 3
    assert 0 <= after["hit_at_1"] <= 3
    assert payload["delta"]["hit_at_1"] == after["hit_at_1"] - base["hit_at_1"]
    assert len(payload["questions"]) == 3
    assert payload["source_prior_eval"]["mode"] == "leaky_upper_bound"
    rec = payload["recommendation"]
    assert rec["verdict"] and rec["risks"] and rec["next_actions"]
    # 落盘：JSON + MD
    jp, mp = rk.write_outputs(payload, tmp_path / "eval.json", tmp_path / "eval.md")
    assert jp.exists() and mp.exists()
    md = mp.read_text(encoding="utf-8")
    assert "汇总对比" in md and "是否接入" in md and "逐题变化" in md
    assert json.loads(jp.read_text(encoding="utf-8"))["config"]["topk"] == 5


def test_run_eval_empty_and_bad_preset(tmp_path):
    qfile = tmp_path / "qs.json"
    qfile.write_text(json.dumps({"questions": QUESTIONS}, ensure_ascii=False), encoding="utf-8")
    empty = rk.run_eval(qfile, search_fn=lambda q, topk=10: [])
    assert empty["baseline"]["summary"]["total"] == 3
    assert empty["baseline"]["summary"]["miss_source"] == 3
    assert empty["rerank"]["summary"]["hit_at_1"] == 0
    assert empty["questions"] and all(q["baseline_rank"] is None for q in empty["questions"])
    with pytest.raises(rk.RankError):
        rk.run_eval(qfile, preset="不存在")
    with pytest.raises(rk.RankError):
        rk.load_questions(tmp_path / "nope.json")


def test_cli_help():
    proc = subprocess.run([sys.executable, "-X", "utf8", str(RANK_PATH), "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0 and "eval" in proc.stdout
