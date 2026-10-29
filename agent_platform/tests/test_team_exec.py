# -*- coding: utf-8 -*-
"""团队执行账/结果摘要测试（2026-10-30 WO-INV-TEAM-ENGINE2）。"""
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "agent_platform" / "engine"))
sys.path.insert(0, str(REPO))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


exec_mod = _load("team_exec_t", REPO / "agent_platform/engine/team_exec.py")


def test_build_summary():
    dec = {"recommendation": "WATCH", "confidence": 0.6, "rationale": "证据不足",
           "l1_basis": "T1/T2 分歧", "target_price_range": [10, 11]}
    records = [{"execution_mode": "normal", "retry": 0, "duration_ms": 100},
               {"execution_mode": "debate", "retry": 1, "duration_ms": 200},
               {"execution_mode": "degraded", "retry": 2, "duration_ms": 50}]
    s = exec_mod.build_summary(dec, records, stock="600519")
    assert s["recommendation"] == "WATCH"
    assert s["confidence"] == 0.6
    assert s["risk_level"] == "MEDIUM"
    assert s["node_count"] == 3
    assert s["execution_modes"] == {"normal": 1, "debate": 1, "degraded": 1}
    assert s["retry_total"] == 3
    assert s["degraded"] is True
    assert s["target_price"] == [10, 11]


def test_record_and_read(tmp_path, monkeypatch):
    monkeypatch.setenv("TEAM_EXEC_DIR", str(tmp_path))
    node = {"node_key": "t1_market", "agent_id": "T1", "role_profile": "t1_bull",
            "analysis_type": "market_technical"}
    r = exec_mod.record("run1", node, "single", "normal", "normal", 123.4,
                        output_text="输出", structured={"overallTrend": "bearish"})
    assert r["execution_mode"] == "normal"
    rows = exec_mod.read_run("run1")
    assert len(rows) == 1 and rows[0]["structured"]["overallTrend"] == "bearish"
    s = exec_mod.build_summary({"recommendation": "BUY", "confidence": 0.8}, rows)
    p = exec_mod.write_summary("run1", s)
    assert Path(p).exists()
    assert json.loads(Path(p).read_text(encoding="utf-8"))["recommendation"] == "BUY"


def test_build_user_prefers_parent_structured():
    interp = _load("interp_t", REPO / "agent_platform/engine/interpreter.py")
    node = {"node_key": "d1_decision", "agent_id": "D1", "role_profile": "d1_decision",
            "analysis_type": "final_decision", "parent_node_keys": ["t1_market"]}
    ctx = {"stock": "600519", "data": {}, "statements": {"t1_market": "全文不应出现"},
           "structured": {"t1_market": {"overallTrend": "bearish"}}}
    u = interp.Interpreter._build_user(None, node, ctx)
    assert "上游分析摘要" in u and "bearish" in u


def test_build_ledger_row_carries_summary():
    rs = _load("run_shadow_t", REPO / "agent_platform/run_shadow.py")
    row = rs.build_ledger_row("2026-10-30", "600519", "茅台", "r1", 100.0,
                              {"recommendation": "WATCH", "confidence": 0.6},
                              True, {"d1_decision": "normal"}, 1.0,
                              summary={"risk_level": "MEDIUM"})
    assert row["summary"]["risk_level"] == "MEDIUM"
