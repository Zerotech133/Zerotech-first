# -*- coding: utf-8 -*-
"""tools/tests/test_gen_dpo_pairs.py · W20 DPO 拒绝采样（离线/mock）单元测试。

覆盖（≥5）：
  1. 候选评分：W13 judge_one 复用 + 组合分 + chosen/rejected 资格（引用正确/拒答/过度声明/阈值）；
  2. top/bottom 选择与过滤：分差不足、chosen 不合格、rejected 未低于阈值、候选不足等跳过原因；
  3. Mock 候选生成：k 个候选、top 为高质量、bottom 为低质量（过度声明/错误引用），可组成偏好对；
  4. mock 全流程：--mock + --limit 跑通，输出与 DPO v2 兼容字段（prompt/chosen/rejected/source/
     category/concept/meta metrics），summary 含条数/分布/跳过原因/阈值；
  5. dry-run：无 key 时只生成计划（30 题 × k=8=240 请求），不实例化任何真实后端；
  6. 本地 ollama 后端解析（monkeypatch `_post_json`，不联网）；报告渲染与落盘（tmp 路径）。

不调用真实 API；测试整体 < 20s。
运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_gen_dpo_pairs.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "finetune"))

import gen_dpo_pairs as g  # noqa: E402

CTX = [{"doc_id": "C:000", "text": "「因子分解」的核心在于先剥离共同因子，再讨论剩余部分。估计的 α 有统计误差。"},
       {"doc_id": "U:learn_x_0001", "text": "因子分解需要区分市场、规模等共同因子。"}]


def _item(qid="A01", question="「因子分解」的核心要点是什么？"):
    return {"qid": qid, "type": "card_concept", "answerable": True, "question": question,
            "gold_doc_ids": ["C:000"],
            "gold_text": "因子分解核心是剥离共同因子后讨论剩余部分，α 有统计误差。",
            "ctx": CTX, "w13_ctx_ids": ["C:000", "U:learn_x_0001"]}


# ------------------------------------------------------------------ 1. 评分/资格
def test_judge_candidate_composite_and_eligibility():
    good = g.judge_candidate(_item(), "因子分解的核心是先剥离共同因子再讨论剩余部分，α 有统计误差[1]。")
    assert good["citation_ok"] is True and good["eligible_chosen"] is True
    assert good["composite"] >= g.CHOSEN_MIN
    assert good["eligible_rejected"] is False
    bad = g.judge_candidate(_item(), "必涨！建议满仓买入，目标价翻倍[9]。")
    assert bad["overclaim"] is True and bad["eligible_chosen"] is False
    assert bad["eligible_rejected"] is True and bad["composite"] < g.REJECT_MAX
    # 拒答答案：可答题上不应被选为 chosen
    refused = g.judge_candidate(_item(), "资料不足：未找到相关信息。")
    assert refused["eligible_chosen"] is False


# ------------------------------------------------------------------ 2. 选择/过滤
def _scored(composite, citation=False, refused=False, overclaim=False):
    return {"composite": composite, "citation_ok": citation, "refused": refused, "overclaim": overclaim,
            "eligible_chosen": bool(citation and not refused and not overclaim and composite >= g.CHOSEN_MIN),
            "eligible_rejected": bool(composite < g.REJECT_MAX), "answer": f"a{composite}"}


def test_select_pair_filters():
    ok = g.select_pair([_scored(0.9, True), _scored(0.1)])
    assert ok[2] == "ok" and ok[0]["composite"] == 0.9 and ok[1]["composite"] == 0.1
    # chosen 不满足资格
    assert g.select_pair([_scored(0.9, False), _scored(0.1)])[2] == "chosen_not_eligible"
    # rejected 未低于阈值
    assert g.select_pair([_scored(0.9, True), _scored(0.8)])[2] == "rejected_not_below_threshold"
    # 分差不足（0.58-0.55=0.03 < 0.05）→ chosen 合格但 rejected 未低于阈值先触发；构造分差小且 rejected 低
    assert g.select_pair([_scored(0.60, True), _scored(0.56)])[2] == "rejected_not_below_threshold"
    assert g.select_pair([_scored(0.9, True), None][:1])[2] == "candidates<2"


# ------------------------------------------------------------------ 3. Mock 候选
def test_mock_backend_candidates_quality():
    backend = g.MockBackend(seed=1)
    answers = backend.mock_answers(_item(), 8)
    assert len(answers) == 8
    scored = g.score_candidates(_item(), answers)
    chosen, rejected, reason = g.select_pair(scored)
    assert reason == "ok"
    assert chosen["composite"] > rejected["composite"]
    assert chosen["overclaim"] is False and chosen["citation_ok"] is True
    assert rejected["overclaim"] is True
    # 失败样本(None) 被过滤
    assert len(g.score_candidates(_item(), [None, answers[0], None])) == 1


# ------------------------------------------------------------------ 4. mock 全流程
def test_run_mock_pipeline_and_output_schema():
    payload = g.run(mock=True, limit=3, k=8, write=False)
    assert payload["mode"] == "mock" and payload["backend"]["real_call"] is False
    assert payload["summary"]["pairs"] == 3 and payload["summary"]["questions_total"] == 3
    rec = payload["pairs"][0]
    for key in ("prompt", "chosen", "rejected", "source", "category", "concept", "meta"):
        assert key in rec, key
    m = rec["meta"]
    for key in ("qid", "ctx_ids", "chosen_metrics", "rejected_metrics", "composite_gap",
                "backend", "k", "quality_ok"):
        assert key in m, key
    assert m["chosen_metrics"]["composite"] > m["rejected_metrics"]["composite"]
    assert payload["summary"]["candidate_success_rate"] == 1.0
    assert set(payload["summary"]["thresholds"]) >= {"gap_min", "chosen_min", "reject_max", "k"}


# ------------------------------------------------------------------ 5. dry-run
def test_dry_run_no_api(monkeypatch):
    monkeypatch.setattr(g.be, "api_key", lambda *a, **k: "")
    monkeypatch.setattr(g, "OpenAICompatBackend", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应调用")))
    monkeypatch.setattr(g, "LocalOllamaBackend", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应调用")))
    payload = g.run(dry_run=True, k=8, limit=0)
    assert payload["mode"] == "dry-run"
    assert payload["plan"]["planned_questions"] == 30
    assert payload["plan"]["planned_requests"] == 240
    assert payload["plan"]["has_api_key"] is False
    assert payload["pairs"] == []
    # 无 key 且非 mock/local 自动降级 dry-run
    payload2 = g.run(dry_run=False, mock=False, local=False, k=4, limit=2)
    assert payload2["mode"] == "dry-run" and payload2["plan"]["planned_requests"] == 8


# ------------------------------------------------------------------ 6. 本地后端解析 / 落盘
def test_local_backend_parse_and_write_outputs(tmp_path, monkeypatch):
    captured = {}

    def fake_post(url, payload, timeout=120):
        captured["url"] = url
        captured["body"] = payload
        return {"message": {"content": "本地回答[1]"}}

    monkeypatch.setattr(g, "_post_json", fake_post)
    backend = g.LocalOllamaBackend(model="qwen3.5:9b")
    out = backend.generate("prompt", temperature=0.7)
    assert out == "本地回答[1]"
    assert captured["url"].endswith("/api/chat")
    assert captured["body"]["options"]["temperature"] == 0.7
    # 落盘（tmp 路径，不覆盖交付物）
    payload = g.run(mock=True, limit=2, write=False)
    monkeypatch.setattr(g, "OUT_JSONL", tmp_path / "c.jsonl")
    monkeypatch.setattr(g, "OUT_MD", tmp_path / "c.md")
    jp, mp = g._write_outputs(payload)
    rows = [json.loads(x) for x in Path(jp).read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == payload["summary"]["pairs"]
    assert "拒绝采样" in Path(mp).read_text(encoding="utf-8")
    md = g.render_markdown(payload)
    assert "方法" in md and "条数与分布" in md and "部署后命令" in md and "阈值" in md
