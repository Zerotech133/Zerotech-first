# -*- coding: utf-8 -*-
"""ACAD1 推荐池测试（2026-10-30 WO-INV-RECOMMEND-POOL）。"""
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pool_mod = _load("rec_pool_t", "agent_platform/recommendation_pool.py")
review_mod = _load("pool_review_t", "tools/pool_review_daily.py")


def _picks(tmp_path):
    p = tmp_path / "picks.json"
    p.write_text(json.dumps({
        "generated_at": "2026-10-30 09:00:00", "scan_run_date": "2026-10-30",
        "stocks": [
            {"code": "600519.SH", "name": "茅台", "tier": "T2", "n_hit": 2,
             "sources": [{"s": "ninedim", "score": 90}, {"s": "strong"}]},
            {"code": "000858.SZ", "name": "五粮液", "tier": "T2", "n_hit": 1,
             "sources": [{"s": "strong"}]},
            {"code": "601318.SH", "name": "平安", "tier": "T2", "n_hit": 1,
             "sources": [{"s": "unknownstrat"}]},
            {"code": "300324.SZ", "name": "旋极", "tier": "T3", "n_hit": 1,
             "sources": [{"s": "ninedim"}]},
        ]}), encoding="utf-8")
    return p


def _portfolio(tmp_path):
    p = tmp_path / "portfolio.json"
    p.write_text(json.dumps({"strategies": [
        {"id": "ninedim", "health": "green", "status": "live", "launch_status": "launch_ready"},
        {"id": "strong", "health": "red", "status": "live", "launch_status": "launch_ready"},
        {"id": "unknownstrat", "health": None, "status": "live", "launch_status": None},
    ]}), encoding="utf-8")
    return p


def test_build_pool_health_filter(tmp_path):
    pool = pool_mod.build_pool(day="2026-10-30", picks_path=str(_picks(tmp_path)),
                               portfolio_path=str(_portfolio(tmp_path)), write=False)
    assert [x["code"] for x in pool["items"]] == ["600519"]
    assert pool["items"][0]["sources"] == ["ninedim"]
    assert pool["counts"]["total"] == 1
    assert "strong" in pool["excluded"]["red_or_maintenance"]
    # 未知健康状态不得被猜成“健康”
    assert "unknownstrat" in pool["excluded"]["red_or_maintenance"]


def test_freshness_uses_requested_day_not_today(tmp_path):
    pool = pool_mod.build_pool(day="2026-11-05", picks_path=str(_picks(tmp_path)),
                               portfolio_path=str(_portfolio(tmp_path)), write=False)
    assert pool["data_date"] == "2026-10-30"
    assert pool["freshness_days"] == 6
    assert pool["stale"] is True


def test_review_pending_and_selected(tmp_path):
    pool = {"pool_id": "P1", "tiers": ["T1", "T2"], "items": [
        {"item_id": "i1", "code": "600519", "name": "茅台", "tier": "T2", "sources": ["ninedim"],
         "data_date": "2026-10-30", "freshness_days": 0, "status": "pending", "risk_flags": []},
        {"item_id": "i2", "code": "000858", "name": "五粮液", "tier": "T2", "sources": ["ninedim"],
         "data_date": "2026-10-30", "freshness_days": 0, "status": "pending", "risk_flags": []},
    ]}
    decisions = {"600519": {"symbol": "600519", "status": "done", "parse_ok": True,
                            "recommendation": "BUY", "confidence": 0.8,
                            "price_at_decision": 100.0}}
    res = review_mod.review("2026-10-30", pool, decisions, write=False)
    assert res["summary"]["reviewed"] == 1
    assert res["summary"]["pending"] == 1
    assert res["summary"]["by_action"]["buy"] == 1
    assert res["plan"]["selected"][0]["code"] == "600519"
    rec = [r for r in res["records"] if r["code"] == "600519"][0]
    assert rec["action"] == "buy" and rec["analyses"] == {}


def test_run_shadow_dynamic_codes_present():
    src = (REPO / "agent_platform" / "run_shadow.py").read_text(encoding="utf-8")
    assert "--codes" in src and "for symbol in watchlist" in src
