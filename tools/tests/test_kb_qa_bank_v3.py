# -*- coding: utf-8 -*-
"""kb_qa_bank_v3（claim 覆盖度过滤 + 可答率扩验）契约测试 —— mock 不调真模型。

覆盖：
  1. claim 覆盖度纯函数：实词提取（去模板词）、高/低覆盖率判定；
  2. 覆盖度过滤：阈值生效、过滤统计（before/after/pass_rate/dropped）、kept 带覆盖率；
  3. 生成去重 + 数量钳制 40..60 + 类别覆盖；
  4. 可答率扩验：成功/降级/无依据/跳过 状态与失败原因、平均引用、可答率、默认 0 不跑；
  5. v2 对比：缺失引用文件容错、delta 计算；
  6. 全链路 schema + JSON/MD/portal 三份落盘 + CLI --help。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
V3_PATH = REPO / "tools" / "kb_corpus" / "kb_qa_bank_v3.py"
THEMES = ["AI算力/半导体", "新能源/电池", "消费/医药", "金融/资本市场",
          "宏观/策略/贸易", "量化/技术分析", "其他/综合"]


def _load_v3():
    spec = importlib.util.spec_from_file_location("kb_qa_bank_v3_under_test", V3_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bk = _load_v3()


def fake_units(n_per_theme: int = 9) -> list[dict]:
    units = []
    idx = 0
    for theme in THEMES:
        for c in range(n_per_theme):
            idx += 1
            keyword = {"AI算力/半导体": "AI 算力 GPU 服务器 覆铜板 PCB",
                       "新能源/电池": "固态电池 锂电 储能",
                       "消费/医药": "白酒 消费 渠道 动销",
                       "金融/资本市场": "资本市场 改革 并购 IPO",
                       "宏观/策略/贸易": "宏观 贸易 经济 出口",
                       "量化/技术分析": "量学 量柱 选股 涨停",
                       "其他/综合": "综合 观察 行业"}[theme]
            concept = f"{theme}概念{c}"
            units.append({"id": f"u{idx:04d}", "book": f"rp_{theme}_{c}", "chapter": "章",
                          "page_start": c + 1, "page_end": c + 2, "concept": concept,
                          "claim": f"{concept}的核心结论：{keyword} 数据与证据 {idx}。",
                          "evidence": f"证据 {idx}", "source": "研报" if idx % 2 else "书籍"})
    return units


MOCK_CALLS: list[str] = []


def mock_search(question, topk=5):
    return [{"unit_id": f"u{i:04d}"} for i in range(1, min(topk, 5) + 1)]


def mock_ask(question, topk=8, model=None, timeout=None):
    MOCK_CALLS.append(question)
    mode = hash(question) % 4
    if mode == 0:
        return {"answer": "答案 [u0001] p.1", "citations": [{"unit_id": "u0001", "page": 1}],
                "confidence": {"level": "high", "reasons": ["1 条引用"]}, "fallback": False}
    if mode == 1:
        return {"answer": "知识库中没有足够依据回答该问题。", "citations": [],
                "confidence": {"level": "none", "reasons": ["检索材料仅定性描述"]}, "fallback": False}
    if mode == 2:
        return {"answer": "（规则降级）摘录", "citations": [{"unit_id": "u2", "page": 2}],
                "confidence": {"level": "low"}, "fallback": True, "fallback_reason": "llm_error"}
    return {"answer": "答案2 [u3] p.3 [u4] p.4", "citations": [{"unit_id": "u3"}, {"unit_id": "u4"}],
            "confidence": {"level": "medium"}, "fallback": False}


# ---------------------------------------------------------------------------
# 1. claim 覆盖度纯函数
# ---------------------------------------------------------------------------
def test_claim_coverage_pure():
    claim = "M9 级覆铜板支持 224Gbps 与 78 层 PCB 量产，介电损耗低于 0.0015。"
    high = bk.claim_coverage("M9 级覆铜板的结论由哪些证据支撑？", claim, concept="M9 级覆铜板")
    assert high >= 0.5, high
    low = bk.claim_coverage("白酒行业动销与渠道库存的判断是什么？", claim, concept="白酒行业动销")
    assert low < 0.5, low
    assert bk.claim_coverage("的 了 是", claim) == 0.0
    # 模板功能词被剔除，不虚增覆盖率
    tokens = bk.question_content_tokens("关于M9 级覆铜板的结论由哪些证据支撑？")
    assert "哪些" not in tokens and "结论" not in tokens
    assert any("覆铜" in t or "M9" in t.upper() for t in tokens)


# ---------------------------------------------------------------------------
# 2. 覆盖度过滤
# ---------------------------------------------------------------------------
def test_build_candidates_v3_filter_stats():
    units = fake_units(3)
    # 加入一个 claim 与 concept 完全无关的单元 → 应被过滤
    units.append({"id": "u_bad", "book": "rp_bad", "chapter": "章", "page_start": 1, "page_end": 2,
                  "concept": "美联储加息路径", "claim": "海绵宝宝动画片收视率上升。", "source": "研报"})
    kept, stats = bk.build_candidates_v3(units, threshold=0.5)
    assert stats["before"] >= stats["after"] > 0
    assert stats["dropped"] == stats["before"] - stats["after"]
    assert stats["pass_rate"] == round(stats["after"] / stats["before"], 4)
    assert all(c["claim_coverage"] >= 0.5 for c in kept)
    assert all("claim_coverage" in c for c in kept)
    assert not any(c["concept"] == "美联储加息路径" for c in kept), "无关 claim 应被过滤"
    # 阈值 0 → 全部通过；阈值 1.0 → 更少
    _, stats_zero = bk.build_candidates_v3(units, threshold=0.0)
    _, stats_max = bk.build_candidates_v3(units, threshold=1.0)
    assert stats_zero["after"] == stats_zero["before"]
    assert stats_max["after"] <= stats["after"]


# ---------------------------------------------------------------------------
# 3. 生成去重 + 数量钳制
# ---------------------------------------------------------------------------
def test_generate_bank_v3_dedupe_and_clamp():
    units = fake_units(9)
    questions, stats = bk.generate_bank_v3(units, limit=10)
    assert len(questions) == bk.MIN_QUESTIONS  # 40
    norm = [bk.v2_bank().norm_question(q["question"]) for q in questions]
    assert len(norm) == len(set(norm)), "问题应去重"
    q60, _ = bk.generate_bank_v3(units, limit=999)
    assert len(q60) == bk.MAX_QUESTIONS
    assert len({q["category"] for q in q60}) >= 6
    assert all("claim_coverage" in q for q in q60)


# ---------------------------------------------------------------------------
# 4. 可答率扩验（状态 + 失败原因）
# ---------------------------------------------------------------------------
def test_validate_answers_v3_status_and_reasons():
    units = fake_units(9)
    questions, _ = bk.generate_bank_v3(units, limit=40)
    default = bk.validate_answers_v3(questions, n=0)
    assert default["requested"] == 0 and default["answerable_rate"] is None
    MOCK_CALLS.clear()
    res = bk.validate_answers_v3(questions, n=12, ask_fn=mock_ask, clock=lambda: 0.0)
    assert res["requested"] == 12 and len(res["samples"]) == 12
    assert len(MOCK_CALLS) == 12, "应串行调用 12 次"
    total = res["success"] + res["fallback"] + res["no_evidence"] + res["skipped"]
    assert total == 12
    assert res["answerable_rate"] == round(res["success"] / 12, 4)
    assert res["avg_citations"] >= 0
    for s in res["samples"]:
        assert s["status"] in ("success", "fallback", "no_evidence", "skipped_deadline")
        assert s["reason"], "每条应有状态/失败原因"
    # 异常容错：ask 抛错 → fallback + 原因
    def bad_ask(*a, **k):
        raise RuntimeError("Ollama 被占满（测试）")

    res_bad = bk.validate_answers_v3(questions, n=2, ask_fn=bad_ask, clock=lambda: 0.0)
    assert res_bad["fallback"] == 2 and res_bad["answerable_rate"] == 0.0
    assert all("规则降级" in s["reason"] for s in res_bad["samples"])


def test_validate_answers_v3_deadline_skip():
    units = fake_units(9)
    questions, _ = bk.generate_bank_v3(units, limit=40)
    ticks = iter([0.0, 700.0] + [700.0] * 50)

    def clock():
        try:
            return next(ticks)
        except StopIteration:
            return 999.0

    res = bk.validate_answers_v3(questions, n=3, ask_fn=mock_ask, clock=clock,
                                 deadline_seconds=600)
    assert res["skipped"] >= 1
    assert any(s["status"] == "skipped_deadline" for s in res["samples"])


# ---------------------------------------------------------------------------
# 5. v2 对比 + 全链路 schema + 落盘 + CLI
# ---------------------------------------------------------------------------
def test_comparison_missing_and_present(tmp_path):
    missing = bk.load_v2_reference(tmp_path / "no_v2.json")
    assert missing["available"] is False
    v2_file = tmp_path / "v2.json"
    v2_file.write_text(json.dumps({"stats": {"total": 50},
                                   "selfcheck": {"summary": {"hit_rate_at_1": 0.96, "hit_rate_at_5": 1.0}},
                                   "answer_validation": {"requested": 6, "success": 2, "fallback": 0,
                                                         "no_evidence": 4, "answerable_rate": 0.3333,
                                                         "elapsed_seconds": 36.33,
                                                         "avg_citations": 2.0}}, ensure_ascii=False),
                       encoding="utf-8")
    ref = bk.load_v2_reference(v2_file)
    assert ref["available"] is True and ref["answerable_rate"] == 0.3333
    payload = {"answer_validation": {"requested": 12, "success": 9, "fallback": 0, "no_evidence": 3,
                                     "skipped": 0, "answerable_rate": 0.75, "avg_citations": 2.5,
                                     "elapsed_seconds": 80.0},
               "selfcheck": {"summary": {"hit_rate_at_1": 0.94, "hit_rate_at_5": 1.0}},
               "stats": {"total": 50}}
    cmp = bk.build_comparison(ref, payload)
    assert cmp["v2"]["answerable_rate"] == 0.3333 and cmp["v3"]["answerable_rate"] == 0.75
    assert cmp["delta"]["answerable_rate"] == round(0.75 - 0.3333, 4)
    assert cmp["scope_note"]


def test_build_payload_schema_and_writes(tmp_path):
    units = fake_units(9)
    payload = bk.build_payload(units, limit=40, answer_n=0, search_fn=mock_search)
    assert payload["kind"] == "kb_qa_question_bank_v3" and payload["schema_version"] == 3
    for key in ("claim_filter", "templates", "stats", "selfcheck", "answer_validation",
                "comparison_v2", "suggestions", "questions"):
        assert key in payload, key
    cf = payload["claim_filter"]
    assert cf["threshold"] == 0.5 and cf["after"] >= 40
    assert len(payload["questions"]) == 40
    assert payload["claim_filter"]["rule"]
    assert payload["disclaimer"] and "不构成投资建议" in payload["disclaimer"]
    jp, mp, pp = bk.write_outputs(payload, tmp_path / "v3.json", tmp_path / "v3.md",
                                  tmp_path / "portal" / "v3.json")
    assert jp.exists() and mp.exists() and pp.exists()
    assert json.loads(pp.read_text(encoding="utf-8")) == json.loads(jp.read_text(encoding="utf-8"))
    md = mp.read_text(encoding="utf-8")
    for anchor in ("claim 覆盖度", "v2 / v3 对比", "建议"):
        assert anchor in md, anchor
    proc = subprocess.run([sys.executable, "-X", "utf8", str(V3_PATH), "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0 and "build" in proc.stdout
