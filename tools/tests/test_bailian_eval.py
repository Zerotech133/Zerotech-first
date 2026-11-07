# -*- coding: utf-8 -*-
"""tools/tests/test_bailian_eval.py · W17 百炼部署后评测（离线/mock）单元测试。

覆盖（≥5）：
  1. prompt 构造：包含上资料编号 [1..5]、原文片段、引用/拒答要求与问题；
  2. 响应解析：OpenAI 对象式 / dict 式响应均能取出文本，空/异常响应返回空串；
  3. 指标复用：W13 `judge_one` 确定性指标（引用正确/groundedness/gold F1/拒答/过度声明/长度）；
  4. 聚合与对比：与 W13 基线聚合的 delta/target 达标判定口径；
  5. dry-run：无 key 时只生成请求计划（不实例化 API 客户端、不联网），--limit 生效；
  6. mock 全流程：MockClient 跑通评测（结果/聚合/comparison schema），错误捕获计入 errors；
  7. 报告渲染与落盘：payload→Markdown 关键小节、JSON/MD 可写临时目录。

不调用真实 API；测试整体 < 15s。
运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_bailian_eval.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "finetune"))

import bailian_eval as be  # noqa: E402

W13_BASE_AGG = {"citation_accuracy": 0.725, "groundedness": 0.4776, "correct_refusal_rate": 0.8,
                "gold_similarity": 0.3845, "overclaim_rate": 0.0, "length_anomaly_rate": 0.0}
CTX = [{"doc_id": "C:000", "text": "「因子分解」的核心在于先剥离共同因子，再讨论剩余部分。估计的 α 有统计误差。"},
       {"doc_id": "U:learn_x_0001", "text": "因子分解需要区分市场、规模等共同因子。"}]


def _item(qid="A01", answerable=True, question="「因子分解」的核心要点是什么？"):
    if answerable:
        return {"qid": qid, "type": "card_concept", "answerable": True, "question": question,
                "gold_doc_ids": ["C:000"], "gold_text": "因子分解核心是剥离共同因子后讨论剩余部分，α 有统计误差。",
                "ctx": CTX, "w13_ctx_ids": ["C:000", "U:learn_x_0001"]}
    return {"qid": qid, "type": "unanswerable", "answerable": False,
            "question": "如何申请申根旅游签证？", "gold_doc_ids": [], "gold_text": "",
            "ctx": CTX, "w13_ctx_ids": ["C:000", "U:learn_x_0001"]}


# ------------------------------------------------------------------ 1. prompt
def test_build_prompt_contains_context_and_rules():
    prompt = be.build_prompt("什么是因子分解？", CTX)
    assert "[1] (C:000)" in prompt and "[2] (U:learn_x_0001)" in prompt
    assert "剥离共同因子" in prompt
    assert "标注" in prompt and "资料不足" in prompt and "不要编造" in prompt
    assert prompt.rstrip().endswith("回答：") or "问题：什么是因子分解？" in prompt


# ------------------------------------------------------------------ 2. 解析
def test_parse_answer_object_and_dict():
    class Obj:
        def __init__(self, content):
            self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]

    assert be.parse_answer(Obj(" 答案[1] ")) == "答案[1]"
    assert be.parse_answer({"choices": [{"message": {"content": "dict答案"}}]}) == "dict答案"
    assert be.parse_answer(None) == ""
    assert be.parse_answer({"choices": []}) == ""
    assert be.parse_answer({"choices": [{}]}) == ""


# ------------------------------------------------------------------ 3. 指标复用
def test_judge_reuses_w13_metrics():
    w13 = be._load_w13()
    judged = w13.judge_one(_item(), CTX, "因子分解核心是剥离共同因子后讨论剩余部分[1]。")
    assert judged["citation_ok"] is True
    assert judged["citation_valid"] is True
    assert judged["refused"] is False
    assert 0.0 < judged["groundedness"] <= 1.0
    assert judged["gold_similarity"] > 0
    assert judged["overclaim"] is False and judged["length_anomaly"] is False
    # 引用错误（序号超出/非 gold）→ citation_ok False
    bad = w13.judge_one(_item(), CTX, "因子分解核心是剥离共同因子[2]。")
    assert bad["citation_ok"] is False
    # 不可答：正确拒答且不编造引用 → citation_ok True
    ref = w13.judge_one(_item("U01", answerable=False), CTX, "资料不足：未在资料中找到相关信息。")
    assert ref["refused"] is True and ref["citation_ok"] is True


# ------------------------------------------------------------------ 4. 聚合/对比
def _fake_results():
    return [
        {"qid": "A01", "type": "card_concept", "answerable": True, "answer": "x[1]", "cited": [1],
         "citation_valid": True, "citation_gold": ["C:000"], "citation_ok": True, "groundedness": 0.8,
         "gold_similarity": 0.6, "refused": False, "overclaim": False, "length": 50,
         "length_anomaly": False, "ctx_ids": ["C:000"], "error": None},
        {"qid": "U01", "type": "unanswerable", "answerable": False, "answer": "资料不足", "cited": [],
         "citation_valid": True, "citation_gold": [], "citation_ok": True, "groundedness": 0.2,
         "gold_similarity": None, "refused": True, "overclaim": False, "length": 10,
         "length_anomaly": False, "ctx_ids": ["C:000"], "error": None},
    ]


def test_aggregate_and_comparison_math():
    bundle = be.aggregate_results(_fake_results(), W13_BASE_AGG)
    agg, cmp_ = bundle["aggregate"], bundle["comparison"]
    assert agg["evaluated"] == 2 and agg["answerable_n"] == 1 and agg["unanswerable_n"] == 1
    assert agg["citation_accuracy"] == 1.0 and agg["correct_refusal_rate"] == 1.0
    assert agg["gold_similarity"] == 0.6
    assert cmp_["citation_accuracy"]["delta"] == pytest.approx(1.0 - 0.725, abs=1e-4)
    assert cmp_["citation_accuracy"]["pass"] is True
    assert cmp_["groundedness"]["w13_baseline"] == 0.4776
    assert "target" in cmp_["overclaim_rate"]


# ------------------------------------------------------------------ 5. dry-run
def test_dry_run_no_api_and_limit(monkeypatch):
    # 确保不会真的建客户端/联网：如果被调用就失败
    monkeypatch.setattr(be, "build_client", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应建客户端")))
    monkeypatch.setattr(be, "api_key", lambda *a, **k: "")
    payload = be.run(dry_run=True, limit=5, model="mock-instance", write=False)
    assert payload["mode"] == "dry-run"
    assert payload["plan"]["planned"] == 5 and payload["plan"]["questions_total"] == 40
    assert payload["plan"]["has_api_key"] is False
    assert payload["results"] == [] and payload["aggregate"] == {}
    # 无 key 且非 mock 时也自动降级 dry-run（同样不联网）
    payload2 = be.run(dry_run=False, mock=False, limit=2)
    assert payload2["mode"] == "dry-run" and payload2["plan"]["planned"] == 2


# ------------------------------------------------------------------ 6. mock 全流程/错误
class _BoomClient:
    @property
    def chat(self):

        class _Completions:
            def create(self, **kwargs):
                raise TimeoutError("mock timeout")

        class _Chat:
            completions = _Completions()

        return _Chat()


def test_mock_pipeline_and_error_capture():
    dataset = [_item("A01", True), _item("U01", False), _item("A02", True)]
    payload = be.evaluate(dataset, baseline_agg=W13_BASE_AGG, client=be.MockClient(),
                          model="mock-instance", mock=True)
    assert payload["mode"] == "mock" and len(payload["results"]) == 3
    assert set(payload["aggregate"]) >= {"citation_accuracy", "groundedness", "correct_refusal_rate",
                                         "gold_similarity", "errors"}
    assert payload["comparison"]["citation_accuracy"]["w13_baseline"] == 0.725
    # 错误捕获：客户端超时 → 该题 error 记录，不中断整体
    payload2 = be.evaluate(dataset[:1], baseline_agg=W13_BASE_AGG, client=_BoomClient(),
                           model="mock-instance", mock=True)
    assert payload2["results"][0]["error"] and "Timeout" in payload2["results"][0]["error"]
    assert payload2["aggregate"]["errors"] == 1
    assert payload2["aggregate"]["evaluated"] == 1


# ------------------------------------------------------------------ 7. 渲染/落盘
def test_render_and_write_outputs(tmp_path):
    dataset = [_item("A01", True)]
    plan = be.build_plan(dataset, model="m", base_url=be.DEFAULT_BASE, limit=1, has_key=False)
    payload = be.build_payload(payload_mode="dry-run", model="m", base_url=be.DEFAULT_BASE,
                               dataset=dataset, baseline_agg=W13_BASE_AGG, plan=plan,
                               key_source="未找到 key")
    md = be.render_markdown(payload)
    assert "百炼部署后评测" in md and "部署后如何运行" in md and "指标对比" in md
    assert "W13 基线" in md and "如何填模型实例 ID" in md
    jp, mp = be.write_outputs(payload, tmp_path / "b.json", tmp_path / "b.md")
    data = json.loads(Path(jp).read_text(encoding="utf-8"))
    assert data["mode"] == "dry-run" and data["dataset"]["total"] == 1
    assert Path(mp).exists() and "百炼部署后评测" in Path(mp).read_text(encoding="utf-8")


# ------------------------------------------------------------------ 8. 聚合口径边界（审计 BUG 回归）
def _result(**over):
    base = {"qid": "A01", "type": "card_concept", "answerable": True, "answer": "x[1]",
            "cited": [1], "citation_valid": True, "citation_gold": ["C:000"],
            "citation_ok": True, "groundedness": 0.8, "gold_similarity": 0.6,
            "refused": False, "overclaim": False, "length": 50, "length_anomaly": False,
            "ctx_ids": ["C:000"], "error": None}
    base.update(over)
    return base


def test_aggregate_gold_denominator_excludes_missing_gold():
    # 2 道可答题，仅 1 道有 gold：均值应按有 gold 的 1 道算，而不是被 None 拉低
    results = [_result(gold_similarity=0.8), _result(qid="A02", gold_similarity=None)]
    agg = be.aggregate_results(results, W13_BASE_AGG)["aggregate"]
    assert agg["gold_similarity"] == 0.8
    assert agg["gold_scored_n"] == 1


def test_aggregate_api_error_refusal_not_credited_as_correct():
    # 不可答题 API 报错（空答案被判 refused）不能算「正确拒答」
    results = [_result(qid="U01", answerable=False, answer="", cited=[], gold_similarity=None,
                       citation_ok=True, refused=True, error="TimeoutError: mock timeout")]
    agg = be.aggregate_results(results, W13_BASE_AGG)["aggregate"]
    assert agg["errors"] == 1
    assert agg["correct_refusal_rate"] == 0.0


def test_write_outputs_custom_paths_weekly_archive(tmp_path):
    """周度复评必须能写独立日期归档，不能覆盖 11-04 W17 基线报告。"""
    jp = tmp_path / "bailian_eval_weekly_20261109.json"
    mp = tmp_path / "bailian_eval_weekly_20261109.md"
    payload = be.run(mock=True, limit=1, write=True, out_json=jp, out_md=mp)
    assert Path(payload["_outputs"]["json"]) == jp
    assert jp.exists() and mp.exists()
    assert "百炼部署后评测" in mp.read_text(encoding="utf-8")


def test_api_error_row_not_counted_as_citation_correct():
    """审计回归：API 错误行（空答案）不得被 judge 当作合规拒答来抬高引用正确率。"""
    payload = be.evaluate([_item("A01", True)], baseline_agg=W13_BASE_AGG,
                          client=_BoomClient(), model="mock-instance", mock=True)
    assert payload["aggregate"]["errors"] == 1
    assert payload["aggregate"]["citation_accuracy"] == 0.0
    assert payload["results"][0]["citation_ok"] is False
    assert payload["results"][0]["refused"] is False


def test_citation_verified_rate_excludes_no_citation_rows():
    """审计回归：verified 分母只算真正带引用的回答，纯拒答行不摊薄等于 1.0 的比值。"""
    results = [
        _result(qid="A01", citation_verify={"citation_valid_rate": 1.0, "cited_sentences": 2,
                                            "weak_rate": 0.0, "all_supported": True,
                                            "unsupported_count": 0}),
        _result(qid="U01", answerable=False, answer="资料不足", cited=[], gold_similarity=None,
                citation_ok=True, refused=True,
                citation_verify={"citation_valid_rate": 0.0, "cited_sentences": 0,
                                 "weak_rate": 0.0, "all_supported": None,
                                 "unsupported_count": 0}),
    ]
    agg = be.aggregate_results(results, W13_BASE_AGG)["aggregate"]
    assert agg["citation_verified_rate"] == 1.0
    assert agg["citation_verified_n"] == 1
    assert agg["citation_verified_excluded_n"] == 1


def test_payload_carries_prompt_profile():
    """审计回归：报告必须能追溯到 strict/base prompt，否则周度对比混口径。"""
    payload = be.evaluate([_item("A01", True)], baseline_agg=W13_BASE_AGG,
                          client=be.MockClient(), model="mock-instance", mock=True)
    assert payload["prompt_profile"] == be.PROMPT_PROFILE
    dataset = [_item("A01", True)]
    plan = be.build_plan(dataset, model="m", base_url=be.DEFAULT_BASE, limit=1, has_key=False)
    built = be.build_payload(payload_mode="dry-run", model="m", base_url=be.DEFAULT_BASE,
                             dataset=dataset, baseline_agg=W13_BASE_AGG, plan=plan,
                             key_source="未找到 key",
                             evaluation={"prompt_profile": "strict", "results": [], "aggregate": {}})
    assert built["prompt_profile"] == "strict"
