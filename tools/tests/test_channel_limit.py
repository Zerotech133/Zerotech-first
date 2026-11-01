# -*- coding: utf-8 -*-
"""tools/tests/test_channel_limit.py — single/multi 通道限流复核（task-119）。

覆盖（任务验收 ≥3，mock/进程内，不联网、不调真实 LLM）：
  1. 统计 analyze：成功/失败筛选、均值/P50/P95、串行容量 60/均值；
  2. 建议 recommend：容量 ≥ 当前 → 维持；容量 < 当前 → 下调；无样本 → 维持；
  3. 限流边界（进程内 mock）：single 与 multi 各 7 连发 → 前 6 次 200、第 7 次 429；
  4. 真实产物 schema/MD：预算 6 次、边界匹配、结论/建议字段齐全。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_channel_limit.py
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "kb_corpus"))


def _load():
    p = ROOT / "tools" / "kb_corpus" / "qa_channel_limit.py"
    spec = importlib.util.spec_from_file_location("qa_channel_limit_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load()


# ------------------------------------------------------------------ 1. 统计
def test_analyze_stats():
    results = [{"status": 200, "elapsed_seconds": 10.0},
               {"status": 200, "elapsed_seconds": 20.0},
               {"status": 429, "elapsed_seconds": 0.0},
               {"status": 200, "elapsed_seconds": 30.0}]
    a = mod.analyze(results)
    assert a["n"] == 4 and a["ok"] == 3
    assert a["mean_seconds"] == 20.0 and a["p50_seconds"] == 20.0
    assert a["min_seconds"] == 10.0 and a["max_seconds"] == 30.0
    assert a["serial_capacity_per_min"] == 3.0     # 60/20
    assert mod.analyze([{"status": 500, "elapsed_seconds": 1.0}])["serial_capacity_per_min"] is None


# ------------------------------------------------------------------ 2. 建议逻辑
def test_recommend_logic():
    fast = {"mean_seconds": 8.0, "serial_capacity_per_min": 7.5}
    r_fast = mod.recommend("multi", fast, current=6)
    assert r_fast["recommended"] == 6 and "维持" in r_fast["rationale"]
    slow = {"mean_seconds": 15.0, "serial_capacity_per_min": 4.0}
    r_slow = mod.recommend("single", slow, current=6)
    assert r_slow["recommended"] == 3 and "下调" in r_slow["rationale"]
    r_none = mod.recommend("single", {"serial_capacity_per_min": None}, current=6)
    assert r_none["recommended"] == 6 and r_none["config_changed"] is False


# ------------------------------------------------------------------ 3. 限流边界（进程内 mock）
def test_boundary_simulation_single_and_multi():
    for mode in ("single", "multi"):
        b = mod.simulate_boundary(mode, n=7)
        assert b["statuses"] == [200, 200, 200, 200, 200, 200, 429], b
        assert b["matches"] is True and b["limit"] == 6


# ------------------------------------------------------------------ 4. 真实产物
def test_real_outputs_schema():
    rj = ROOT / "research" / "reports" / "qa_channel_limit_20261102.json"
    rm = ROOT / "research" / "reports" / "qa_channel_limit_20261102.md"
    assert rj.exists() and rm.exists()
    rep = json.loads(rj.read_text(encoding="utf-8"))
    assert rep["schema_version"] == "qa_channel_limit.v1"
    assert rep["real_llm_budget"]["used"] <= rep["real_llm_budget"]["max"] == 6
    assert rep["config_snapshot"]["rate_limit"] == {"single": 6, "multi": 6}
    for mode in ("single", "multi"):
        assert rep["boundary"][mode]["matches"] is True
        assert rep["analysis"][mode]["n"] >= 1
        assert rep["recommendations"][mode]["config_changed"] is False
        assert rep["recommendations"][mode]["recommended"] in (2, 3, 4, 5, 6)
    md = rm.read_text(encoding="utf-8")
    for needle in ("真实延迟实测", "限流边界", "可达性分析", "建议（只建议，不改配置）", "口径与局限"):
        assert needle in md
