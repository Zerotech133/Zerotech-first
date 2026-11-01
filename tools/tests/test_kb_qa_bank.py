# -*- coding: utf-8 -*-
"""kb_qa_bank（问答候选问题库生成器）契约测试 —— 默认不调 LLM。

覆盖：
  1. 主题分类关键词规则（AI/电池/消费/金融/量化/其他）；
  2. 模板问题生成 + 去重（问题文本归一化唯一、同概念≤2 条）；
  3. 分组与 schema：数量钳制 20..40、themes 计数一致、来源/页码/主题字段齐全；
  4. dry-run 不调 LLM（ask_fn 一旦被调用即失败）；--answer N 显式才调（mock）；
  5. 双份落盘（research+portal 内容一致）+ CLI --help / subprocess dry-run。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BANK_PATH = REPO / "tools" / "kb_corpus" / "kb_qa_bank.py"


def _load_bank():
    spec = importlib.util.spec_from_file_location("kb_qa_bank_under_test", BANK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bank = _load_bank()


def _unit(i: int, concept: str, claim: str, theme_text: str = "", source: str = "研报") -> dict:
    return {"id": f"u{i:04d}aaaa{str(i % 10)}", "book": f"rp_TEST{i}", "chapter": "章",
            "page_start": i % 40 + 1, "page_end": i % 40 + 2, "concept": concept,
            "claim": claim + " " + theme_text, "evidence": "证据数据：" + claim,
            "source": source}


def fake_units() -> list[dict]:
    units = []
    for i in range(30):
        units.append(_unit(i, f"AI 算力主题{i % 5}", "AI 算力与 PCB 需求增长", "GPU 服务器 覆铜板"))
        units.append(_unit(100 + i, f"固态电池主题{i % 5}", "固态电池量产与排产提升", "电池 储能 固态"))
        units.append(_unit(200 + i, f"白酒动销主题{i % 5}", "白酒动销与消费复苏", "白酒 消费 渠道"))
        units.append(_unit(300 + i, f"资本市场改革{i % 5}", "资本市场改革与并购重组", "资本市场 改革 并购"))
        units.append(_unit(400 + i, f"量柱分类{i % 5}", "量柱分类与量学选股", "量学 量柱 选股"))
        units.append(_unit(500 + i, f"其他主题{i % 5}", "综合性的行业观察结论", ""))
    return units


MOCK_CALLS: list[str] = []


def mock_ask(question, topk=8, timeout=300, model=None, retrieval_only=False, **_kw):
    MOCK_CALLS.append(question)
    return {"answer": f"答案：{question[:20]}", "citations": [{"unit_id": "u0000aaaa0", "page": 1}],
            "confidence": {"level": "medium", "reasons": ["1 条可核验引用"]},
            "fallback": False, "citation_check": {}}


def fail_ask(*a, **k):
    raise AssertionError("默认 dry-run 不应调用 LLM")


@pytest.fixture(autouse=True)
def _clear_calls():
    MOCK_CALLS.clear()


# ---------------------------------------------------------------------------
# 1. 主题分类
# ---------------------------------------------------------------------------
def test_classify_theme_rules():
    assert bank.classify_theme(_unit(1, "M9 覆铜板", "AI 服务器 PCB 价值量", "GPU 算力")) == "AI算力/半导体"
    assert bank.classify_theme(_unit(2, "固态电池", "锂电产业链排产", "电池 储能")) == "新能源/电池"
    assert bank.classify_theme(_unit(3, "白酒动销", "消费复苏", "渠道 动销")) == "消费/医药"
    assert bank.classify_theme(_unit(4, "资本市场改革", "并购重组", "IPO 债券")) == "金融/资本市场"
    assert bank.classify_theme(_unit(5, "量柱完全分类", "量学选股", "量柱 量线")) == "量化/技术分析"
    assert bank.classify_theme(_unit(6, "综合评价", "行业观察", "")) == "其他/综合"


# ---------------------------------------------------------------------------
# 2. 模板生成 + 去重
# ---------------------------------------------------------------------------
def test_candidates_template_and_dedupe():
    units = fake_units()
    candidates = bank.build_candidates(units)
    assert candidates, "应生成候选问题"
    norms = [bank.normalize_question(c["question"]) for c in candidates]
    assert len(norms) == len(set(norms)), "问题文本应全局唯一"
    # 同主题同概念最多 2 条
    concept_counts: dict[str, int] = {}
    for c in candidates:
        key = (c["theme"], bank.normalize_question(c["concept"]))
        concept_counts[key] = concept_counts.get(key, 0) + 1
    assert max(concept_counts.values()) <= 2
    # 记载来源与类型
    for c in candidates:
        assert c["source_unit_id"] and c["theme"] and c["question"]
        assert c["type"] in list(bank.QUESTION_TYPES) + ["主题专属"]
        assert c["page_start"] is not None
    # 至少覆盖多个主题与多种问题类型
    assert len({c["theme"] for c in candidates}) >= 5
    assert len({c["type"] for c in candidates}) >= 2


# ---------------------------------------------------------------------------
# 3. 分组 + schema + 数量钳制
# ---------------------------------------------------------------------------
def test_build_bank_grouping_schema_and_clamp():
    payload = bank.build_bank(units=fake_units(), limit=5, answer_n=0, ask_fn=fail_ask)
    assert payload["kind"] == "kb_qa_question_bank"
    assert payload["stats"]["total"] == bank.MIN_QUESTIONS  # 下限钳制 20
    assert payload["answers"]["requested"] == 0 and payload["answers"]["answered"] == 0
    assert payload["disclaimer"] and "不构成投资建议" in payload["disclaimer"]
    # themes 分组计数与 flat questions 一致
    grouped = sum(g["count"] for g in payload["themes"])
    assert grouped == len(payload["questions"]) == payload["stats"]["total"]
    for g in payload["themes"]:
        assert g["count"] == len(g["questions"])
        assert all(q["theme"] == g["theme"] for q in g["questions"])
    for q in payload["questions"]:
        for key in ("id", "question", "type", "theme", "source_unit_id", "book", "chapter",
                    "page_start", "page_end", "concept", "claim_snippet", "source"):
            assert key in q, key
    # 上限钳制 40
    payload2 = bank.build_bank(units=fake_units(), limit=999, ask_fn=fail_ask)
    assert payload2["stats"]["total"] == bank.MAX_QUESTIONS


def test_dry_run_never_calls_llm():
    payload = bank.build_bank(units=fake_units(), limit=30, answer_n=0, ask_fn=fail_ask)
    assert payload["answers"]["answered"] == 0
    assert not MOCK_CALLS


# ---------------------------------------------------------------------------
# 4. --answer N 显式才调（mock）
# ---------------------------------------------------------------------------
def test_answer_n_calls_llm_for_first_n_only():
    payload = bank.build_bank(units=fake_units(), limit=25, answer_n=3, ask_fn=mock_ask)
    assert payload["answers"]["requested"] == 3 and payload["answers"]["answered"] == 3
    assert len(MOCK_CALLS) == 3
    answered = [q for q in payload["questions"] if "answer" in q]
    assert len(answered) == 3
    assert all(isinstance(q["answer"], dict) and q["answer"]["citations"] for q in answered)
    # 其余问题无答案字段（默认不生成，控成本）
    assert sum(1 for q in payload["questions"] if "answer" not in q) == len(payload["questions"]) - 3


# ---------------------------------------------------------------------------
# 5. 双份落盘 + CLI
# ---------------------------------------------------------------------------
def test_write_bank_two_files_and_cli(tmp_path):
    payload = bank.build_bank(units=fake_units(), limit=20, ask_fn=fail_ask)
    out, portal = bank.write_bank(payload, tmp_path / "q.json", tmp_path / "portal" / "q.json")
    assert out.exists() and portal.exists()
    assert json.loads(out.read_text(encoding="utf-8")) == json.loads(portal.read_text(encoding="utf-8"))
    assert out.read_text(encoding="utf-8").count("kb_qa_question_bank") >= 1
    # CLI dry-run subprocess（真实知识单元，不调 LLM）
    proc = subprocess.run([sys.executable, "-X", "utf8", str(BANK_PATH), "build", "--dry-run",
                           "--limit", "20", "--out", str(tmp_path / "r.json"),
                           "--portal-out", str(tmp_path / "p.json")],
                          capture_output=True, text=True, timeout=180,
                          encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr
    assert "questions=20" in proc.stdout and "answers=0" in proc.stdout
    data = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert data["stats"]["total"] == 20 and data["answers"]["answered"] == 0
    # --help
    proc2 = subprocess.run([sys.executable, "-X", "utf8", str(BANK_PATH), "--help"],
                           capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc2.returncode == 0 and "build" in proc2.stdout
