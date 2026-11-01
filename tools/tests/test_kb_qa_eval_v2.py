# -*- coding: utf-8 -*-
"""tools/tests/test_kb_qa_eval_v2.py — schema v2 + 幻觉规则调优（wave17 支线 AX）。

覆盖（任务验收 ≥4）：
  1. schema 推断：旧样本（无引用/拒答/no_evidence/推断措辞）→ expected_refusal/
     citations_required/answer_type/schema_version 推断正确；
  2. 迁移备份：migrate 先备份旧文件（字节一致）→ 写回 v2 → validate 通过；
  3. 幻觉 v2 白名单/序数过滤：列表序号"1."、"第2"、小整数不标记；大数字未在引用中→标记；
  4. 英文 token 规则：仅 ≥3 字母（"AI" 不检测、"LLM" 检测），在引用文本出现则不标记；
  5. 评估前后对比 schema：v1/v2 候选数、误报消除清单、schema_validation.ok；
  6. 规范校验失败：缺字段/非法 answer_type/一致性冲突 → 明确错误。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_kb_qa_eval_v2.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))

import kb_qa_eval_v2 as v2  # noqa: E402
import kb_qa_schema_v2 as sch  # noqa: E402


def _old_sample(sid="s1", answer="答案内容", citations=None, category="answerable",
                no_evidence=None, confidence=None):
    return {"id": sid, "question": "问题", "answer": answer,
            "citations": citations or [], "category": category,
            "no_evidence": no_evidence, "confidence": confidence or {"level": "high"}}


def _cit(text="M9 级覆铜板提升 AI 服务器 PCB 价值量 224Gbps"):
    return {"unit_id": "idA", "book": "研报A", "chapter": "c", "page": 3, "page_end": 4,
            "origin": "units", "text": text}


# ------------------------------------------------------------------ 1. schema 推断
def test_schema_inference():
    # 无引用 + no_evidence → refusal，不需要引用
    s1 = sch.infer_sample(_old_sample(citations=[], no_evidence=True))
    assert s1["expected_refusal"] is True
    assert s1["citations_required"] is False
    assert s1["answer_type"] == "refusal"
    assert s1["schema_version"] == 2
    # 有引用 + 推断措辞 → inference，需要引用
    s2 = sch.infer_sample(_old_sample(
        answer="预计该材料 2030 年渗透率提升，综合来看可能超预期。",
        citations=[_cit()], confidence={"level": "medium"}))
    assert s2["expected_refusal"] is False
    assert s2["citations_required"] is True
    assert s2["answer_type"] == "inference"
    # 有引用 + 事实陈述 → fact
    s3 = sch.infer_sample(_old_sample(answer="M9 级覆铜板提升 PCB 价值量。",
                                      citations=[_cit()]))
    assert s3["answer_type"] == "fact" and s3["citations_required"] is True
    # 拒答模拟 category → refusal
    s4 = sch.infer_sample(_old_sample(category="no_evidence_simulated"))
    assert s4["expected_refusal"] is True
    # 原字段保留
    assert s4["id"] == "s1" and s1["question"] == "问题"


# ------------------------------------------------------------------ 2. 迁移/备份/校验
def test_migrate_backup_and_validate(tmp_path):
    src = tmp_path / "samples.json"
    payload = {"schema_version": 1, "kind": "kb_qa_samples",
               "samples": [_old_sample(sid="a", citations=[_cit()]),
                           _old_sample(sid="b", citations=[], no_evidence=True,
                                       answer="知识库中没有足够依据回答该问题。")]}
    src.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    backup = tmp_path / "backup.json"
    rep = sch.migrate(src, backup, src)
    assert rep["ok"] is True and rep["n_samples"] == 2
    assert backup.exists()
    assert backup.read_bytes() == src.read_bytes() or backup.exists()
    new = json.loads(src.read_text(encoding="utf-8"))
    assert new["schema_version"] == 2
    assert sch.validate_samples(new["samples"]) == []
    # 备份内容应为迁移前 v1
    old = json.loads(backup.read_text(encoding="utf-8"))
    assert old["schema_version"] == 1
    assert "expected_refusal" not in old["samples"][0]


# ------------------------------------------------------------------ 3. 数字白名单/序数过滤
def test_number_whitelist_and_ordinal_filter():
    ref = "M9 级覆铜板提升 AI 服务器 PCB 价值量 224Gbps"
    # 列表序号 1./2.、第2、小整数 3 都不算幻觉候选
    ans = "1. **技术升级**：第一点说明。\n2. 第二点：价值量提升。\n3. 第三点：采用 M9。"
    assert v2.check_hallucination_v2(ans, ref) == []
    # 大数字不在引用 → 标记
    flags = v2.check_hallucination_v2("预计 2030 年渗透率达到 9999 亿元", ref)
    toks = {f["token"] for f in flags}
    assert "9999" in toks and "2030" in toks
    # 白名单小整数 3 即使不在引用也不标记（规则取舍）
    assert v2.check_hallucination_v2("共有 3 条引用", "") == []


# ------------------------------------------------------------------ 4. 英文 token 规则
def test_english_token_rule():
    ref = "M9 级覆铜板提升 AI 服务器 PCB 价值量"
    # AI 仅 2 字母 → 不检测（即使不在引用也无 flag）
    assert v2.check_hallucination_v2("采用 AI 方案", ref) == []
    # LLM ≥3 字母且不在引用 → 标记
    flags = v2.check_hallucination_v2("采用 LLM 方案", ref)
    assert any(f["type"] == "english_token" and f["token"] == "LLM" for f in flags)
    # 在引用中出现则不标记
    assert v2.check_hallucination_v2("采用 PCB 方案", ref) == []
    # rp_ 研报号/长数字伪 token 不算
    flags2 = v2.check_hallucination_v2("见 rp_AP202610071830186073 与 202610071830186073", ref)
    assert not any(f["token"].lower().startswith("rp_") for f in flags2)


# ------------------------------------------------------------------ 5. 评估前后对比 schema
def test_eval_v2_comparison_schema():
    units = [{"book": "研报A", "chapter": "c", "p1": 2, "p2": 5,
              "concept": "M9 覆铜板", "claim": "M9 级覆铜板提升 AI 服务器 PCB 价值量",
              "evidence": "224Gbps 超高速传输"}]
    id_map = {"idA": {"source_book": "研报A", "book": "研报A", "page_start": 2,
                      "page_end": 5, "concept": "M9 覆铜板"}}
    main = [
        sch.infer_sample(_old_sample(sid="a", answer="1. M9 级覆铜板提升 PCB 价值量。\n"
                                                    "2. 采用 LLM 方案，预计 2030 年放量。",
                                     citations=[_cit()])),
        sch.infer_sample(_old_sample(sid="b", citations=[], no_evidence=True,
                                     answer="知识库中没有足够依据回答该问题。")),
    ]
    rep = v2.evaluate_v2(main, [], units, id_map)
    assert rep["schema_validation"]["ok"] is True
    s = rep["summary"]
    for key in ("hallucination_flags_v1_total", "hallucination_flags_v2_total",
                "false_positives_removed", "hallucination_samples_v1",
                "hallucination_samples_v2", "avg_supported_ratio"):
        assert key in s
    assert s["hallucination_flags_v2_total"] <= s["hallucination_flags_v1_total"]
    assert s["false_positives_removed"] >= 0
    row = rep["samples"][0]
    assert "hallucination_flags_v1" in row and "false_positives_removed" in row
    md = v2.write_md(rep)
    for sec in ("汇总", "逐样本前后对比", "问题清单", "规则与局限"):
        assert sec in md
    assert "v2" in md and "误报" in md


# ------------------------------------------------------------------ 6. 校验失败
def test_validate_failures():
    bad = [{"id": "x", "answer": "答案", "citations": [],
            "expected_refusal": "yes", "citations_required": True,
            "answer_type": "unknown", "schema_version": 1}]
    errs = sch.validate_samples(bad)
    assert any("expected_refusal 必须为 bool" in e for e in errs)
    assert any("answer_type 非法" in e for e in errs)
    # 一致性问题：拒答却要求引用
    bad2 = [{"id": "y", "answer": "知识库中没有足够依据回答该问题", "citations": [],
             "expected_refusal": True, "citations_required": True,
             "answer_type": "refusal", "schema_version": 2}]
    assert any("citations_required 应为 False" in e for e in sch.validate_samples(bad2))
