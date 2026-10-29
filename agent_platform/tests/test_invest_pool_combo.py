# -*- coding: utf-8 -*-
"""每日股票池→组合计划测试（2026-10-30 WO-INV-POOL-COMBO）。"""
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wl = _load("watchlist_t", "agent_platform/watchlist.py")
comp = _load("composer_t", "agent_platform/portfolio_composer.py")


def _dec(symbol, rec, conf=0.8, price=10.0, **kw):
    d = {"symbol": symbol, "status": "done", "parse_ok": True,
         "recommendation": rec, "confidence": conf, "price_at_decision": price,
         "fallback": False, "degraded": False}
    d.update(kw)
    return d


def test_resolve_manual_codes():
    rows = wl.resolve_codes(codes="600519.SH, 000858,601318")
    assert [r["code"] for r in rows] == ["600519", "000858", "601318"]


def test_resolve_xunyou_top3(tmp_path, monkeypatch):
    p = tmp_path / "xunyou.json"
    p.write_text('{"top":[{"symbol":"300324.SZ","score":99},{"symbol":"000999.SZ"},{"symbol":"301122.SZ"}]}',
                 encoding="utf-8")
    monkeypatch.setattr(wl, "XUNYOU", str(p))
    rows = wl.resolve_codes(source="xunyou", top=3)
    assert [r["code"] for r in rows] == ["300324", "000999", "301122"]


def test_compose_buy_watch_avoid():
    pool = [{"code": "600519", "name": "茅台", "industry": "白酒"},
            {"code": "000858", "name": "五粮液", "industry": "白酒"},
            {"code": "601318", "name": "平安", "industry": "保险"}]
    decisions = {"600519": _dec("600519", "STRONG_BUY", 0.9, 100.0),
                 "000858": _dec("000858", "WATCH", 0.6, 50.0),
                 "601318": _dec("601318", "SELL", 0.7, 40.0)}
    plan = comp.compose(pool, decisions)
    assert len(plan["selected"]) == 1
    assert plan["selected"][0]["code"] == "600519"
    assert plan["selected"][0]["weight"] == 0.10
    assert plan["selected"][0]["buy_zone"] == [98.0, 101.0]
    assert plan["selected"][0]["stop_loss"] == 92.0
    assert plan["total_weight"] == 0.10
    assert len(plan["watch"]) == 1 and len(plan["avoid"]) == 1


def test_compose_pending_when_no_decisions():
    pool = [{"code": "600519", "name": "茅台", "industry": "白酒"}]
    plan = comp.compose(pool, {})
    assert plan["pending"][0]["action"] == "pending_analysis"
    assert plan["selected"] == [] and plan["cash_weight"] == 1.0


def test_compose_confidence_and_caps():
    pool = [{"code": "600519", "industry": "白酒"}, {"code": "000858", "industry": "白酒"}]
    decisions = {"600519": _dec("600519", "STRONG_BUY", 0.9, 100.0),
                 "000858": _dec("000858", "STRONG_BUY", 0.9, 50.0)}
    plan = comp.compose(pool, decisions)
    assert len(plan["selected"]) == 2
    assert abs(plan["total_weight"] - 0.20) < 1e-9  # 行业上限 30% 不触发；每票 10%
    assert plan["cash_weight"] == 0.80


def test_run_shadow_dynamic_flags_present():
    src = (REPO / "agent_platform" / "run_shadow.py").read_text(encoding="utf-8")
    assert "_watchlist_codes" in src and "--pool-source" in src
    assert "for symbol in watchlist" in src
