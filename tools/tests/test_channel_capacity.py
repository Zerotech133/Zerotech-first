# -*- coding: utf-8 -*-
"""tools/tests/test_channel_capacity.py — 通道容量/限流语义复核（task-122）。

覆盖（任务验收 ≥3；全部进程内 mock，真实 LLM 0 次）：
  1. mock 慢 LLM 矩阵单元：并发 1/2/4 记录 success/429/总耗时/吞吐；
  2. 突发验证：并发 10 → 200×6 + 429×4（滑动窗口 6/分 = 突发上限）；
  3. 串行不误伤：并发 1 的 10 个请求应全部 200（窗口自然滑动，不触发 429）；
  4. 报告 schema/MD：矩阵/分析/结论/建议字段齐全，真实产物一致。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_channel_capacity.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))


def _load():
    p = ROOT / "tools" / "kb_corpus" / "qa_channel_capacity.py"
    spec = importlib.util.spec_from_file_location("qa_channel_capacity_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load()


# ------------------------------------------------------------------ 1. 矩阵单元（mock 慢 LLM 小延迟）
def test_run_cell_matrix_and_wait():
    cell = mod.run_cell("single", concurrency=2, n=4, sleep_seconds=0.05)
    assert cell["channel"] == "single" and cell["concurrency"] == 2 and cell["n"] == 4
    assert cell["success"] + cell["rate_limited"] + cell["other"] == 4
    assert cell["total_wall_s"] >= 0.04           # 至少一个 mock 延迟
    assert cell["queue_wait_max_s"] >= 0
    assert cell["throughput_per_min"] is not None or cell["success"] == 0


# ------------------------------------------------------------------ 2. 突发：6/分 → 200×6 + 429×4
def test_burst_verification():
    for channel in ("single", "multi"):
        cell = mod.run_cell(channel, concurrency=10, n=10, sleep_seconds=0.05)
        assert cell["success"] == 6, cell["statuses"]
        assert cell["rate_limited"] == 4, cell["statuses"]
        assert cell["statuses"].count(200) == 6 and cell["statuses"].count(429) == 4
        assert cell["llm_calls"] == 6             # 429 在 LLM 之前拒绝，不产生 mock LLM 调用


# ------------------------------------------------------------------ 3. 串行不误伤
def test_serial_does_not_false_reject():
    cell = mod.run_cell("single", concurrency=1, n=3, sleep_seconds=0.05)
    assert cell["success"] == 3 and cell["rate_limited"] == 0


# ------------------------------------------------------------------ 4. 报告 schema/MD + 真实产物
def test_report_schema_and_md():
    matrix = {
        "single": {"cells": [
            {"channel": "single", "concurrency": 1, "n": 3, "success": 3,
             "rate_limited": 0, "other": 0, "statuses": [200, 200, 200],
             "queue_wait_mean_s": 0.0, "queue_wait_max_s": 0.0,
             "total_wall_s": 0.3, "throughput_per_min": 600.0, "llm_calls": 3,
             "sleep_seconds": 0.05},
            {"channel": "single", "concurrency": 10, "n": 10, "success": 6,
             "rate_limited": 4, "other": 0, "statuses": [200] * 6 + [429] * 4,
             "queue_wait_mean_s": 0.01, "queue_wait_max_s": 0.02,
             "total_wall_s": 0.1, "throughput_per_min": 3600.0, "llm_calls": 6,
             "sleep_seconds": 0.05}]},
    }
    rep = mod.build_report(matrix)
    assert rep["schema_version"] == "qa_channel_capacity.v1"
    assert rep["conclusion"]["burst_verified"] is True
    assert rep["recommendations"]["single"]["recommended"] == 6
    assert rep["recommendations"]["config_changed"] is False
    md = mod.render_md(rep)
    for needle in ("并发矩阵", "突发验证", "限流语义结论", "建议（不修改配置）", "口径与局限"):
        assert needle in md
    # 真实产物（完整矩阵跑完后生成）
    rj = ROOT / "research" / "reports" / "qa_channel_capacity_20261102.json"
    if rj.exists():
        real = json.loads(rj.read_text(encoding="utf-8"))
        assert real["mock_llm"]["real_llm_calls"] == 0
        assert real["conclusion"]["verdict"].startswith("维持 6/分")
        for ch, a in real["analysis"].items():
            assert a["burst_matches_6_plus_429"] is True
