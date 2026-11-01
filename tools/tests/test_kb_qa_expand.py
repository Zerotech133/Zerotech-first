# -*- coding: utf-8 -*-
"""kb_qa_expand（问答样本扩量）契约测试 —— mock 不调真模型、不写真实门户文件。

覆盖：
  1. 主题轮转选题：数量钳制 10..15、主题均衡覆盖、空数据；
  2. 串行生成 + 分类：成功/降级/无依据统计、平均引用数、deadline 跳过；
  3. 失败降级：ask_fn 抛异常 → fallback 样本 + 错误记录，不中断批量；
  4. 合并去重：原 5 条 + 新样本 → 15–20 条、同问题去重（原样本优先）、schema 兼容；
  5. 备份：首次创建、二次不覆盖；
  6. CLI --help + --dry-run（真实问题库，不调 LLM）。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EX_PATH = REPO / "tools" / "kb_corpus" / "kb_qa_expand.py"
BANK = REPO / "research" / "reports" / "kb_qa_questions_20261101.json"


def _load_ex():
    spec = importlib.util.spec_from_file_location("kb_qa_expand_under_test", EX_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ex = _load_ex()

THEMES = ["AI算力/半导体", "新能源/电池", "消费/医药", "金融/资本市场",
          "宏观/策略/贸易", "量化/技术分析", "其他/综合"]


def fake_questions(n_per_theme: int = 4) -> list[dict]:
    out = []
    for t in THEMES:
        for i in range(n_per_theme):
            out.append({"id": f"qk_{t}_{i}", "question": f"{t} 问题{i} 的影响如何？",
                        "theme": t, "type": "影响如何", "source_unit_id": f"u_{t}_{i}",
                        "concept": f"{t}概念{i}"})
    return out


def original_payload(n: int = 5) -> dict:
    samples = [{"id": f"q{i}", "question": f"原始问题 {i}", "answer": f"原始答案 {i}",
                "citations": [{"unit_id": f"u_orig_{i}", "page": i}],
                "confidence": {"level": "high"}, "fallback": False,
                "category": "answerable"} for i in range(n)]
    return {"schema_version": 1, "kind": "kb_qa_samples", "generated_at": "2026-11-01T09:00:00+08:00",
            "model": "qwen3.5:9b", "samples": samples}


def mock_result(question, mode="success"):
    if mode == "fallback":
        return {"answer": "（规则降级）摘录原文", "citations": [], "confidence": {"level": "low"},
                "fallback": True, "fallback_reason": "llm_error"}
    if mode == "no_evidence":
        return {"answer": ex and "知识库中没有足够依据回答该问题。", "citations": [],
                "confidence": {"level": "none", "reasons": ["检索无命中"]}, "fallback": False}
    return {"answer": f"答案：{question[:12]} [u_1] p.2", "citations": [{"unit_id": "u_1", "page": 2}],
            "confidence": {"level": "medium", "reasons": ["1 条引用"]}, "fallback": False}


# ---------------------------------------------------------------------------
# 1. 主题轮转选题
# ---------------------------------------------------------------------------
def test_select_by_theme_rotation():
    questions = fake_questions(4)
    selected = ex.select_by_theme_rotation(questions, limit=12)
    assert len(selected) == 12
    themes = [q["theme"] for q in selected]
    assert len(set(themes)) == len(THEMES), "应覆盖全部 7 个主题"
    counts = {t: themes.count(t) for t in THEMES}
    assert max(counts.values()) - min(counts.values()) <= 1, "主题轮转应均衡"
    # 钳制 10..15
    assert len(ex.select_by_theme_rotation(questions, limit=1)) == ex.MIN_LIMIT
    assert len(ex.select_by_theme_rotation(questions, limit=99)) == ex.MAX_LIMIT
    # 数量不足时取全部
    assert len(ex.select_by_theme_rotation(fake_questions(1)[:3], limit=12)) == 3
    assert ex.select_by_theme_rotation([], limit=12) == []


# ---------------------------------------------------------------------------
# 2. 串行生成 + 分类
# ---------------------------------------------------------------------------
def test_run_expand_classifies_success_fallback_no_evidence():
    questions = fake_questions(1)[:3]
    calls: list[str] = []

    def ask_fn(question, topk=8, model=None, timeout=300):
        calls.append(question)
        if "新能源" in question:
            mode = "fallback"
        elif "消费" in question:
            mode = "no_evidence"
        else:
            mode = "success"
        return mock_result(question, mode)

    payload = ex.run_expand(questions, ask_fn=ask_fn, deadline_seconds=600)
    run = payload["run"]
    assert run["requested"] == 3 and len(calls) == 3, "串行逐条调用"
    assert run["success"] == 1 and run["fallback"] == 1 and run["no_evidence"] == 1
    assert run["answered"] == 3
    assert run["avg_citations"] == round(1 / 3, 2)
    assert payload["kind"] == "kb_qa_samples_expanded"
    for s in payload["samples"]:
        for key in ("id", "question", "theme", "answer", "citations", "confidence",
                    "fallback", "no_evidence", "success", "elapsed_seconds"):
            assert key in s, key
    assert any(s["fallback"] for s in payload["samples"])
    assert any(s["no_evidence"] for s in payload["samples"])


def test_run_expand_deadline_skips_remaining():
    questions = fake_questions(1)[:3]
    ticks = iter([0.0, 100.0, 200.0, 300.0, 400.0, 500.0, 600.0, 700.0])

    def clock():
        try:
            return next(ticks)
        except StopIteration:
            return 999.0

    payload = ex.run_expand(questions, ask_fn=lambda q, **k: mock_result(q), clock=clock,
                            deadline_seconds=150)
    assert payload["run"]["skipped"] == 2
    assert len(payload["samples"]) == 1


# ---------------------------------------------------------------------------
# 3. 失败降级
# ---------------------------------------------------------------------------
def test_run_expand_ask_failure_degrades():
    questions = fake_questions(1)[:2]

    def bad_ask(question, **kwargs):
        raise RuntimeError("Ollama 被占满（测试注入）")

    payload = ex.run_expand(questions, ask_fn=bad_ask, deadline_seconds=60)
    assert payload["run"]["fallback"] == 2 and payload["run"]["success"] == 0
    assert all(s["fallback"] is True and s["answer"] is None for s in payload["samples"])
    assert payload["run"]["errors"] and "Ollama" in payload["run"]["errors"][0]


# ---------------------------------------------------------------------------
# 4. 合并去重
# ---------------------------------------------------------------------------
def test_merge_samples_dedupe_and_schema():
    original = original_payload(5)
    expanded = {"model": "qwen3.5:9b", "generated_at": "2026-11-01T09:45:00+08:00",
                "samples": [
                    {"id": "e1", "question": "新问题 1", "answer": "新答案 1",
                     "citations": [{"unit_id": "u_e1", "page": 3}],
                     "confidence": {"level": "high"}, "fallback": False},
                    {"id": "e2", "question": "新问题 2", "answer": "新答案 2",
                     "citations": [], "confidence": {"level": "medium"}, "fallback": False},
                    # 与原始样本重复（应去重，原样本优先）
                    {"id": "e3", "question": "原始问题 1", "answer": "重复答案",
                     "citations": [], "confidence": {"level": "low"}, "fallback": False},
                    # 无答案（降级失败）不入池
                    {"id": "e4", "question": "失败问题", "answer": None,
                     "citations": [], "confidence": {"level": "low"}, "fallback": True},
                ]}
    merged = ex.merge_samples(original, expanded)
    assert merged["original_count"] == 5 and merged["added_count"] == 2
    assert merged["merged_count"] == 7
    payload = merged["payload"]
    assert payload["kind"] == "kb_qa_samples" and payload["schema_version"] == 1
    questions = [s["question"] for s in payload["samples"]]
    assert questions.count("原始问题 1") == 1, "重复问题应去重"
    assert "失败问题" not in questions
    # 原样本优先保留原答案
    dup = next(s for s in payload["samples"] if s["question"] == "原始问题 1")
    assert dup["answer"] == "原始答案 1"
    for s in payload["samples"]:
        for key in ("question", "answer", "citations", "confidence", "fallback"):
            assert key in s, key


# ---------------------------------------------------------------------------
# 5. 备份
# ---------------------------------------------------------------------------
def test_backup_original_first_only(tmp_path):
    original = tmp_path / "portal.json"
    backup = tmp_path / "backup.json"
    original.write_text('{"samples": [1]}', encoding="utf-8")
    path, created = ex.backup_original(original, backup)
    assert created is True and path == backup
    assert backup.read_text(encoding="utf-8") == original.read_text(encoding="utf-8")
    # 二次备份不覆盖首次备份
    original.write_text('{"samples": [2]}', encoding="utf-8")
    path2, created2 = ex.backup_original(original, backup)
    assert created2 is False and path2 == backup
    assert '"samples": [1]' in backup.read_text(encoding="utf-8")
    # 原文件不存在 → 不创建备份
    _, created3 = ex.backup_original(tmp_path / "nope.json", tmp_path / "b2.json")
    assert created3 is False


# ---------------------------------------------------------------------------
# 6. CLI（--help / --dry-run 真实问题库，不调 LLM）
# ---------------------------------------------------------------------------
def test_cli_help_and_dry_run(tmp_path):
    proc = subprocess.run([sys.executable, "-X", "utf8", str(EX_PATH), "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0 and "run" in proc.stdout
    proc = subprocess.run([sys.executable, "-X", "utf8", str(EX_PATH), "run", "--dry-run",
                           "--limit", "12", "--questions", str(BANK),
                           "--out", str(tmp_path / "expanded.json"),
                           "--backup", str(tmp_path / "backup.json"),
                           "--portal", str(tmp_path / "portal.json")],
                          capture_output=True, text=True, timeout=180,
                          encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr
    data = json.loads((tmp_path / "expanded.json").read_text(encoding="utf-8"))
    assert data["kind"] == "kb_qa_samples_expanded"
    assert data["selection"]["requested"] == 12
    assert data["samples"] == [] and data["run"]["dry_run"] is True
    merged = json.loads((tmp_path / "portal.json").read_text(encoding="utf-8"))
    assert merged["kind"] == "kb_qa_samples" and merged["samples"] == []
    assert "requested=12" in proc.stdout
