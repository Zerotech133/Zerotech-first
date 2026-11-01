# -*- coding: utf-8 -*-
"""kb_qa_bank_v2（问题模板 v2）契约测试 —— mock 不调真模型。

覆盖：
  1. 模板结构：6–8 类、每类 4–6 条；
  2. 候选生成：问题文本去重、同(主题,概念)≤2、类别/主题覆盖、含 concept；
  3. 选取钳制 40..60 + 类别均衡；
  4. retrieval-only 自检结构（@1/@5、按类别/主题），mock 检索；
  5. 可答率验证（--answer N）：成功/降级/无依据统计与 answerable_rate，异常容错；
  6. 全链路 schema + JSON/MD/portal 三份落盘 + CLI --help。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
V2_PATH = REPO / "tools" / "kb_corpus" / "kb_qa_bank_v2.py"


def _load_v2():
    spec = importlib.util.spec_from_file_location("kb_qa_bank_v2_under_test", V2_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bk = _load_v2()
THEMES = ["AI算力/半导体", "新能源/电池", "消费/医药", "金融/资本市场",
          "宏观/策略/贸易", "量化/技术分析", "其他/综合"]


def fake_units() -> list[dict]: 
    units = []
    idx = 0
    for theme in THEMES:
        for c in range(9):
            idx += 1
            text = {"AI算力/半导体": "AI 算力 GPU 服务器 覆铜板",
                    "新能源/电池": "固态电池 锂电 储能",
                    "消费/医药": "白酒 消费 渠道 动销",
                    "金融/资本市场": "资本市场 改革 并购 IPO",
                    "宏观/策略/贸易": "宏观 贸易 经济 出口",
                    "量化/技术分析": "量学 量柱 选股 涨停",
                    "其他/综合": "综合 观察 行业"}[theme]
            units.append({"id": f"u{idx:04d}", "book": f"rp_{theme}_{c}", "chapter": "章",
                          "page_start": c + 1, "page_end": c + 2,
                          "concept": f"{theme}概念{c}", "claim": f"{text} 的结论与证据 {idx}。",
                          "evidence": f"数据 {idx}", "source": "研报" if idx % 2 else "书籍"})
    return units


MOCK_CALLS: list[str] = []


def mock_search(question, topk=5):
    # 用问题里的来源概念构造命中：50% 命中 @1，25% 命中 @2-5，25% 不命中
    h = hash(question) % 4
    if h == 0:
        return [{"unit_id": f"u{abs(hash(question)) % 35 + 1:04d}"}]
    return [{"unit_id": f"u{i:04d}"} for i in range(1, min(topk, 5) + 1)]


def mock_ask(question, topk=8, model=None, timeout=None):
    MOCK_CALLS.append(question)
    mode = hash(question) % 3
    if mode == 0:
        return {"answer": "答案 [u0001] p.1", "citations": [{"unit_id": "u0001", "page": 1}],
                "confidence": {"level": "high"}, "fallback": False}
    if mode == 1:
        return {"answer": "知识库中没有足够依据回答该问题。", "citations": [],
                "confidence": {"level": "none"}, "fallback": False}
    return {"answer": "（规则降级）摘录", "citations": [], "confidence": {"level": "low"},
            "fallback": True}


# ---------------------------------------------------------------------------
# 1. 模板结构
# ---------------------------------------------------------------------------
def test_templates_structure():
    cats = bk.TEMPLATES
    assert 6 <= len(cats) <= 8
    assert len(bk.CATEGORIES) == len(cats)
    for cat, templates in cats.items():
        assert 4 <= len(templates) <= 6, (cat, len(templates))
        assert all("{concept}" in t for t in templates), cat
    total = sum(len(t) for t in cats.values())
    assert total >= 25
    assert {"是什么", "影响如何", "竞争格局", "关键数据", "风险限制", "如何识别"} <= set(cats)


# ---------------------------------------------------------------------------
# 2. 候选生成 + 3. 选取钳制
# ---------------------------------------------------------------------------
def test_build_candidates_dedupe_and_coverage():
    candidates = bk.build_candidates(fake_units())
    assert candidates
    norms = [bk.norm_question(c["question"]) for c in candidates]
    assert len(norms) == len(set(norms)), "问题文本应去重"
    # 同(主题,概念)最多 2 条
    counts: dict[tuple, int] = {}
    for c in candidates:
        counts[(c["theme"], bk.norm_question(c["concept"]))] = \
            counts.get((c["theme"], bk.norm_question(c["concept"])), 0) + 1
    assert max(counts.values()) <= 2
    assert len({c["category"] for c in candidates}) >= 6
    for c in candidates:
        assert c["question"] and c["concept"] in c["question"]
        assert c["category"] in bk.CATEGORIES
        assert c["source_unit_id"]


def test_select_balanced_clamp_and_balance():
    candidates = bk.build_candidates(fake_units())
    selected = bk.select_balanced(candidates, limit=10)
    assert len(selected) == bk.MIN_QUESTIONS  # 40
    selected2 = bk.select_balanced(candidates, limit=999)
    assert len(selected2) == bk.MAX_QUESTIONS  # 60
    themes = {q["theme"] for q in selected2}
    assert len(themes) == len(THEMES)
    cats = {q["category"] for q in selected2}
    assert len(cats) >= 6, "类别应均衡覆盖"


# ---------------------------------------------------------------------------
# 4. retrieval-only 自检结构
# ---------------------------------------------------------------------------
def test_selfcheck_structure_with_mock_search():
    units = fake_units()
    questions, _ = bk.generate_bank(units, limit=40)
    check = bk.selfcheck_questions(questions, topk=5, search_fn=mock_search)
    for key in ("summary", "by_category", "by_theme", "questions"):
        assert key in check, key
    s = check["summary"]
    assert s["total"] == 40
    assert 0 <= s["hit_at_1"] <= s["hit_at_5"] <= 40
    assert s["hit_rate_at_5"] == round(s["hit_at_5"] / 40, 4)
    assert len(check["questions"]) == 40
    assert check["by_category"] and check["by_theme"]
    for row in check["questions"]:
        assert "hit_at_1" in row and "hit_at_5" in row and "source_rank" in row


# ---------------------------------------------------------------------------
# 5. 可答率验证
# ---------------------------------------------------------------------------
def test_validate_answers_rate_and_tolerance():
    units = fake_units()
    questions, _ = bk.generate_bank(units, limit=40)
    default = bk.validate_answers(questions, n=0)
    assert default["requested"] == 0 and default["answerable_rate"] is None

    def clock():
        return 0.0

    res = bk.validate_answers(questions, n=6, ask_fn=mock_ask, clock=clock)
    assert res["requested"] == 6 and len(res["samples"]) == 6
    assert res["success"] + res["fallback"] + res["no_evidence"] <= 6
    assert res["answerable_rate"] == round(res["success"] / 6, 4)
    assert {s["status"] for s in res["samples"]} <= {"success", "fallback", "no_evidence", "skipped_deadline"}

    def bad_ask(*a, **k):
        raise RuntimeError("LLM 挂起（测试）")

    res_fail = bk.validate_answers(questions, n=2, ask_fn=bad_ask, clock=clock)
    assert res_fail["fallback"] == 2 and res_fail["success"] == 0
    assert res_fail["answerable_rate"] == 0.0


# ---------------------------------------------------------------------------
# 6. 全链路 schema + 落盘 + CLI
# ---------------------------------------------------------------------------
def test_build_payload_schema_and_writes(tmp_path):
    units = fake_units()
    payload = bk.build_payload(units, limit=40, answer_n=0, search_fn=mock_search)
    assert payload["kind"] == "kb_qa_question_bank_v2" and payload["schema_version"] == 2
    for key in ("templates", "stats", "selfcheck", "answer_validation", "questions",
                "themes", "category_groups", "suggestions"):
        assert key in payload, key
    assert payload["stats"]["total"] == 40
    assert len(payload["questions"]) == 40
    assert len(payload["category_groups"]) >= 6
    grouped = sum(g["count"] for g in payload["category_groups"])
    assert grouped == 40
    assert payload["disclaimer"] and "不构成投资建议" in payload["disclaimer"]
    assert payload["suggestions"]
    jp, mp, pp = bk.write_outputs(payload, tmp_path / "v2.json", tmp_path / "v2.md",
                                  tmp_path / "portal" / "v2.json")
    assert jp.exists() and mp.exists() and pp.exists()
    assert json.loads(pp.read_text(encoding="utf-8")) == json.loads(jp.read_text(encoding="utf-8"))
    md = mp.read_text(encoding="utf-8")
    for anchor in ("概览", "模板类别与问题分布", "主题覆盖与自检", "改进建议"):
        assert anchor in md, anchor
    assert "kb_qa_questions_v2" in md or "v2 问题库" in md
    # 不覆盖 v1：输出名不同（v2 独立路径）
    assert "v2" in jp.name and "v2" in pp.name
    proc = subprocess.run([sys.executable, "-X", "utf8", str(V2_PATH), "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0 and "build" in proc.stdout
