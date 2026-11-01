# -*- coding: utf-8 -*-
"""tools/tests/test_kb_qa_samples_v4.py — 门户问答样例刷新到 v4 默认（task-112）。

覆盖（任务验收 ≥4）：
  1. 样本构造 schema 2：保留 mode/guard/guard_passed/citation_resolvable_rate/inference_ratio/
     fallback，追加 prompt_version/citation_retry_enabled；status 分类（success/fallback/refusal）；
  2. 刷新流程（mock ask，tmp 路径）：先备份旧样例（字节一致）、写新 samples/report.json/report.md；
  3. 真实产物校验：portal/static/kb_qa_samples.json schema 2、8 条、prompt_version=v4、
     citation_retry_enabled=true、guard 字段完整；backup4 为旧版（12 条）；
  4. 页面兼容：kb_reports.html 仍 fetch /static/kb_qa_samples.json 且渲染 guard_passed/
     citation_resolvable_rate/inference_ratio（新增字段为增量，无需改页面）；
  5. HTTP 200：/kb_reports.html 与 /static/kb_qa_samples.json。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_kb_qa_samples_v4.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))


def _load():
    p = ROOT / "tools" / "kb_corpus" / "kb_qa_samples_refresh.py"
    spec = importlib.util.spec_from_file_location("kb_qa_samples_refresh_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load()
SAMPLES = ROOT / "portal" / "static" / "kb_qa_samples.json"
BACKUP = ROOT / "research" / "reports" / "kb_qa_samples_backup4_20261101.json"
REPORT_JSON = ROOT / "research" / "reports" / "kb_qa_samples_v4_20261101.json"
PAGE = ROOT / "portal" / "kb_reports.html"
BASE = "http://127.0.0.1:8734"


def _fake_result(**over):
    base = {
        "mode": "multi", "k": 8, "prompt_version": "v4", "citation_retry": True,
        "guard_marker_mode": "segment", "answer": "答案 [u1] p.1 [u2] p.2",
        "citations": [{"unit_id": "u1"}, {"unit_id": "u2"}],
        "confidence": {"level": "high"}, "fallback": False, "refusal": False,
        "guard_enabled": True, "guard_passed": True, "guard_action": "pass",
        "guard_issues": [], "citation_valid": ["u1", "u2"], "citation_invalid": [],
        "citation_resolvable_rate": 1.0, "inference_ratio": 0.2, "inference_heavy": False,
        "distinct_cited_units": 2, "direct_fact_markers": 4, "inference_markers": 1,
        "clusters": [], "elapsed_seconds": 12.3, "error": None,
    }
    base.update(over)
    return base


# ------------------------------------------------------------------ 1. schema / status
def test_build_sample_fields_and_status():
    q = {"id": "q1", "question": "Q?", "category": "主题", "theme": "AI",
         "source_unit_id": "u1"}
    s = mod.build_sample(q, _fake_result())
    for key in ("mode", "guard_enabled", "guard_passed", "citation_resolvable_rate",
                "inference_ratio", "fallback", "refusal", "status"):
        assert key in s, f"缺字段 {key}"
    assert s["prompt_version"] == "v4"
    assert s["citation_retry_enabled"] is True
    assert s["guard_marker_mode"] == "segment"
    assert s["status"] == "success"
    assert mod.build_sample(q, _fake_result(fallback=True))["status"] == "fallback"
    assert mod.build_sample(q, _fake_result(answer="知识库中没有足够依据回答该问题。",
                                           refusal=True, confidence={"level": "none"}))["status"] == "refusal"


# ------------------------------------------------------------------ 2. 刷新流程（mock+tmp）
def test_refresh_with_mock_writes_backup_and_outputs(tmp_path):
    old = tmp_path / "samples.json"
    old.write_text(json.dumps({"schema_version": 2, "samples": [{"id": "old"}]},
                              ensure_ascii=False), encoding="utf-8")
    old_bytes = old.read_bytes()
    calls = []

    def fake_ask(q, **kw):
        calls.append((q, kw))
        return _fake_result()

    bank = tmp_path / "bank.json"
    bank.write_text(json.dumps({"questions": [
        {"id": f"q{i}", "question": f"问题{i}", "category": "主题", "theme": f"t{i%2}",
         "source_unit_id": f"u{i}"} for i in range(4)]}, ensure_ascii=False), encoding="utf-8")
    backup = tmp_path / "backup4.json"
    rep = mod.refresh(limit=2, ask_fn=fake_ask, bank_path=bank,
                      samples_path=old, backup_path=backup,
                      report_json=tmp_path / "r.json", report_md=tmp_path / "r.md")
    assert len(calls) == 2 and calls[0][1].get("mode") == "multi"
    assert backup.exists() and backup.read_bytes() == old_bytes       # 写前备份（字节一致）
    data = json.loads(old.read_text(encoding="utf-8"))
    assert data["schema_version"] == 2 and data["prompt_version"] == "v4"
    assert data["citation_retry"] is True and len(data["samples"]) == 2
    assert data["stats"]["success"] == 2
    assert (tmp_path / "r.json").exists() and (tmp_path / "r.md").exists()
    assert rep["outputs"]["backup"] == str(backup)


# ------------------------------------------------------------------ 3. 真实产物
def test_real_portal_samples_and_backup4():
    data = json.loads(SAMPLES.read_text(encoding="utf-8"))
    assert data["schema_version"] == 2 and data["kind"] == "kb_qa_samples"
    assert data["prompt_version"] == "v4"
    assert data["guard_marker_mode"] == "segment"
    assert data["citation_retry"] is True
    samples = data["samples"]
    assert len(samples) == 8
    for s in samples:
        assert s["prompt_version"] == "v4"
        assert "citation_retry_enabled" in s
        for key in ("mode", "guard_passed", "citation_resolvable_rate",
                    "inference_ratio", "fallback"):
            assert key in s, f"真实样本缺字段 {key}"
    assert data["stats"]["success"] == 8 and data["stats"]["total_citations"] > 0
    assert REPORT_JSON.exists()
    # 备份4 = 旧版（12 条）
    old = json.loads(BACKUP.read_text(encoding="utf-8"))
    assert old["schema_version"] == 2 and len(old["samples"]) == 12


# ------------------------------------------------------------------ 4. 页面兼容
def test_page_consumes_samples_fields():
    page = PAGE.read_text(encoding="utf-8")
    assert 'fetch(\'/static/kb_qa_samples.json' in page or '/static/kb_qa_samples.json' in page
    for token in ("guard_passed", "citation_resolvable_rate", "inference_ratio",
                  "samples", "kb_qa_samples"):
        assert token in page, f"页面缺少样本字段消费：{token}"
    # 新增字段为增量，不改变页面既有渲染逻辑（仍按 samples 数组/guard 字段展示）
    assert "samples.map" in page or "samples" in page


# ------------------------------------------------------------------ 5. HTTP 200
def _get(path: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(BASE + path, timeout=5) as r:
            return r.status, r.read()
    except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:  # pragma: no cover
        pytest.skip(f"门户离线：{exc}")


def test_http_200_page_and_samples():
    status, body = _get("/kb_reports.html")
    assert status == 200 and "kb_qa_samples" in body.decode("utf-8", "replace")
    status, body = _get("/static/kb_qa_samples.json")
    assert status == 200
    data = json.loads(body.decode("utf-8"))
    assert data["prompt_version"] == "v4" and len(data["samples"]) == 8
