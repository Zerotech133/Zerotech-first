# -*- coding: utf-8 -*-
"""8091 验收复跑（限流/性能优化后）：mock 契约测试（不真打 8091/LLM）。

覆盖：
  1. percentile 延迟分位；
  2. summarize_serial20：200/错误率/P50/P95/P99、限流 meta limits 提取；
  3. build_recheck 结论：全达标 → go；multi 灰度缺失 → conditional + 需修项；
     retrieval P95 超标/限流≠300 → 需修项；wave38 对比字段；
  4. run_recheck 全流程（mock acceptance + fetch）→ schema/JSON/MD 落盘 + CLI。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RC_PATH = REPO / "tools" / "kb_corpus" / "qa_http_recheck.py"
V3_BANK = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"


def _load():
    spec = importlib.util.spec_from_file_location("qa_http_recheck_under_test", RC_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rc = _load()


def mock_acceptance():
    return {
        "schema_version": 1, "kind": "qa_http_live_acceptance",
        "generated_at": "2026-11-02T00:40:00+08:00",
        "config": {"base_url": "http://127.0.0.1:8091"},
        "cases": {"normal": [], "boundary": []},
        "summary": {
            "totals": {"normal": 8, "boundary": 3, "http_200": 8, "errors": 0},
            "retrieval": {"n": 3, "http_200": 3, "llm_called": False, "avg_elapsed_seconds": 0.06},
            "single": {"n": 2, "http_200": 2, "llm_called": 2, "avg_elapsed_seconds": 11.7,
                       "avg_citations": 3.0},
            "multi": {"n": 3, "http_200": 3, "llm_called": 2, "avg_elapsed_seconds": 25.0,
                      "avg_citations": 2.0, "guard_passed": 3, "fallback": 0,
                      "prompt_versions": ["v4"], "guard_markers": ["segment"],
                      "citation_retry_values": [True, True, True]},
            "boundary": {"n": 3, "passed": 3,
                        "cases": [{"case": "a", "status": 400, "expected": 400, "pass": True},
                                  {"case": "b", "status": 400, "expected": 400, "pass": True},
                                  {"case": "c", "status": 400, "expected": 400, "pass": True}]},
            "llm_calls": 4,
        },
        "conclusion": {"can_go_live": "go", "verdict": "ok", "fixes_needed": [], "risks": []},
    }


def mock_serial20(p95=0.06, limits=(300,)):
    results = [{"status": 200, "ok": True, "elapsed_seconds": 0.05,
                "rate_limit": {"limit": limits[0] if limits else None}} for _ in range(20)]
    return rc.summarize_serial20(results) if p95 == 0.06 else rc.summarize_serial20(
        [dict(r, elapsed_seconds=p95) for r in results])


# build_recheck 接收 load_wave38 归一化后的结构；run_recheck 测试则提供原始报告 JSON
WAVE38 = {"generated_at": "2026-11-01T23:56:00+08:00",
          "retrieval_avg_elapsed_seconds": 2.06,
          "multi_prompt_versions": ["v1"], "multi_guard_markers": ["strict"],
          "multi_retry_values": [True, True, True],
          "conclusion": {"can_go_live": "conditional",
                         "fixes_needed": ["multi 通道 meta.prompt_version 未显示 v4"]}}
WAVE38_RAW = {"generated_at": "2026-11-01T23:56:00+08:00",
              "summary": {"retrieval": {"avg_elapsed_seconds": 2.06},
                          "multi": {"prompt_versions": ["v1"], "guard_markers": ["strict"],
                                    "citation_retry_values": [True, True, True]}},
              "conclusion": {"can_go_live": "conditional",
                             "fixes_needed": ["multi 通道 meta.prompt_version 未显示 v4"]}}


def test_percentile():
    assert rc.percentile([], 50) == 0.0
    assert rc.percentile([0.05], 95) == 0.05
    assert rc.percentile([0.01, 0.02, 0.03, 0.04], 50) == 0.025
    assert rc.percentile([0.01, 0.02, 0.03, 0.04], 100) == 0.04


def test_summarize_serial20():
    results = [{"status": 200, "ok": True, "elapsed_seconds": 0.05,
                "rate_limit": {"limit": 300}} for _ in range(19)] + \
              [{"status": 429, "ok": False, "elapsed_seconds": 0.01, "rate_limit": {"limit": 300}}]
    s = rc.summarize_serial20(results)
    assert s["n"] == 20 and s["http_200"] == 19
    assert s["errors"] == 1 and s["error_rate"] == 0.05
    assert s["p50"] == 0.05 and s["p95"] == 0.05
    assert s["rate_limit_limits"] == [300]


def test_build_recheck_decision_cases():
    res = rc.build_recheck(mock_acceptance(), mock_serial20(), WAVE38)
    assert res["meets"] is True and res["can_go_live"] == "go"
    assert not res["fixes_needed"]
    cmp = res["compare_wave38"]
    assert cmp["wave38_multi_prompt_versions"] == ["v1"]
    assert cmp["this_multi_prompt_versions"] == ["v4"]
    assert cmp["wave38_can_go_live"] == "conditional"
    assert res["scale_advice"]["next_steps"]

    # multi 灰度缺失 → conditional + 需修项
    acc = mock_acceptance()
    acc["summary"]["multi"]["prompt_versions"] = ["v1"]
    acc["summary"]["multi"]["guard_markers"] = ["strict"]
    res2 = rc.build_recheck(acc, mock_serial20(), WAVE38)
    assert res2["can_go_live"] == "conditional"
    assert any("v4" in x for x in res2["fixes_needed"])

    # retrieval P95 超标 + 限流非 300 → 需修项
    res3 = rc.build_recheck(mock_acceptance(), mock_serial20(p95=0.35, limits=(60,)), WAVE38)
    assert res3["can_go_live"] == "conditional"
    assert any("0.2s" in x or "P95" in x for x in res3["fixes_needed"])
    assert any("300" in x for x in res3["fixes_needed"])

    # 正常路径失败 → no-go
    bad = mock_acceptance()
    bad["summary"]["totals"] = {"normal": 8, "boundary": 3, "http_200": 7, "errors": 1}
    assert rc.build_recheck(bad, mock_serial20(), WAVE38)["can_go_live"] == "no-go"


def test_run_recheck_schema_with_mocks(tmp_path):
    def fake_fetch(url, timeout=60):
        return 200, {"ok": True, "meta": {"rate_limit": {"limit": 300}}}, 0.05, {"limit": 300}

    wave38 = tmp_path / "wave38.json"
    wave38.write_text(json.dumps(WAVE38_RAW), encoding="utf-8")
    payload = rc.run_recheck(acceptance_fn=lambda **kw: mock_acceptance(), fetch_fn=fake_fetch,
                             wave38_path=wave38,
                             questions=json.loads(Path(V3_BANK).read_text(encoding="utf-8"))["questions"])
    assert payload["kind"] == "qa_http_recheck"
    assert payload["recheck"]["can_go_live"] == "go"
    assert payload["serial20"]["rate_limit_limits"] == [300]
    jp, mp = rc.write_outputs(payload, tmp_path / "r.json", tmp_path / "r.md")
    assert jp.exists() and mp.exists()
    md = mp.read_text(encoding="utf-8")
    for anchor in ("结论", "验收检查项", "与 wave38 验收对比", "热缓存 retrieval 基准", "放量建议", "风险"):
        assert anchor in md, anchor
    assert json.loads(jp.read_text(encoding="utf-8"))["recheck"]["meets"] is True
    proc = subprocess.run([sys.executable, "-X", "utf8", str(RC_PATH), "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0 and "run" in proc.stdout
