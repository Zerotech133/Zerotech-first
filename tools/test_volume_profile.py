# -*- coding: utf-8 -*-
"""量能画像测试（2026-11-16 WO-VOLUME-PROFILE）。

用假 API（monkeypatch `_query`）覆盖：量比/突破/回调判定、日内结构、**单位交叉校验**、无数据分支。
不发起真实网络请求。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _load():
    spec = importlib.util.spec_from_file_location(
        "volume_profile_t", REPO / "tools" / "volume_profile.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vp = _load()
CODE = "300476.SZ"


def _daily_rows(closes, vols, pcts=None):
    """构造 ts_daily 的返回（columns + rows 形式）。"""
    pcts = pcts or [0.0] * len(closes)
    cols = ["ts_code", "trade_date", "close", "pct_chg", "vol"]
    rows = [[CODE, "2026%04d" % (1000 + i), c, p, v]
            for i, (c, v, p) in enumerate(zip(closes, vols, pcts))]
    return {"columns": cols, "rows": rows}


def _mins_rows(n=240, vol_each=1000.0, first_close=10.0, last_close=9.0):
    cols = ["ts_code", "trade_time", "freq", "open", "high", "low", "close", "vol", "amount"]
    rows = []
    for i in range(n):
        hh = 9 + (30 + i) // 60
        mm = (30 + i) % 60
        close = first_close if i == 0 else (last_close if i == n - 1 else first_close)
        rows.append([CODE, "2026-11-16 %02d:%02d:00" % (hh, mm), "1min", close, close, close, close, vol_each, 0.0])
    return {"columns": cols, "rows": rows}


def test_daily_volume_ratio_and_flags(monkeypatch):
    # 20 天平量 1000，最后一天 2000（量比 2.0）+ 涨 5% + 破前高 → 放量突破
    closes = [10.0] * 20 + [11.0]
    vols = [1000.0] * 20 + [2000.0]
    pcts = [0.0] * 20 + [5.0]
    monkeypatch.setattr(vp, "_query", lambda tbl, params, timeout=60: _daily_rows(closes, vols, pcts))
    d = vp.daily_volume(CODE)
    assert d["available"] is True
    assert d["vol_ratio_20"] == 2.0
    assert d["breakout_with_volume"] is True
    assert d["volume_price"] == "价涨量增"
    assert d["vol_unit"].startswith("手")


def test_daily_shrink_pullback(monkeypatch):
    closes = [10.0] * 20 + [9.5]
    vols = [1000.0] * 20 + [700.0]
    pcts = [0.0] * 20 + [-2.0]
    monkeypatch.setattr(vp, "_query", lambda tbl, params, timeout=60: _daily_rows(closes, vols, pcts))
    d = vp.daily_volume(CODE)
    assert d["vol_ratio_20"] == 0.7
    assert d["shrink_pullback"] is True
    assert d["volume_price"] == "价跌量缩"


def test_daily_insufficient_samples(monkeypatch):
    monkeypatch.setattr(vp, "_query", lambda tbl, params, timeout=60: _daily_rows([10.0, 11.0], [1000.0, 1000.0]))
    d = vp.daily_volume(CODE)
    assert d["available"] is False and "样本不足" in d["reason"]


def test_intraday_structure(monkeypatch):
    monkeypatch.setattr(vp, "_query", lambda tbl, params, timeout=60: _mins_rows())
    iv = vp.intraday_volume(CODE, "2026-11-16")
    assert iv["available"] is True
    assert iv["bars"] == 240
    assert abs(iv["am_share"] + iv["pm_share"] - 100.0) < 0.2
    assert iv["vol_unit"].startswith("股")


def test_intraday_zero_volume(monkeypatch):
    monkeypatch.setattr(vp, "_query", lambda tbl, params, timeout=60: _mins_rows(vol_each=0.0))
    iv = vp.intraday_volume(CODE, "2026-11-16")
    assert iv["available"] is False and "分钟量为 0" in iv["reason"]


def test_unit_cross_check_consistent(monkeypatch):
    """日线（手）与分钟线（股）应差 100 倍；一致时给出确认结论。"""
    closes = [10.0] * 20 + [11.0]
    vols = [1000.0] * 20 + [2000.0]   # 手
    pcts = [0.0] * 20 + [5.0]

    def fake(tbl, params, timeout=60):
        if tbl == "ts_daily":
            return _daily_rows(closes, vols, pcts)
        return _mins_rows(n=240, vol_each=200000.0 / 240)   # 总量 200,000 股 = 2000 手 × 100

    monkeypatch.setattr(vp, "_query", fake)
    p = vp.profile(CODE, "2026-11-16")
    assert p["available"] is True
    assert "一致" in p["unit_check"]


def test_unit_cross_check_mismatch(monkeypatch):
    """不匹配时必须报「口径待核」，不能自行换算糊过去。"""
    closes = [10.0] * 20 + [11.0]
    vols = [1000.0] * 20 + [2000.0]
    pcts = [0.0] * 20 + [5.0]

    def fake(tbl, params, timeout=60):
        if tbl == "ts_daily":
            return _daily_rows(closes, vols, pcts)
        return _mins_rows(n=240, vol_each=1000.0)           # 总量 240,000 股 ≠ 200,000

    monkeypatch.setattr(vp, "_query", fake)
    p = vp.profile(CODE, "2026-11-16")
    assert "口径待核" in p["unit_check"]


def test_profile_unavailable_when_both_fail(monkeypatch):
    monkeypatch.setattr(vp, "_query", lambda tbl, params, timeout=60: {"columns": [], "rows": []})
    p = vp.profile(CODE, "2026-11-16")
    assert p["available"] is False
    assert len(p["warnings"]) >= 2


def test_slim_vol_whitelist():
    spec = importlib.util.spec_from_file_location(
        "rd_vol_t", REPO / "tools" / "research_dispatch.py")
    rd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rd)
    raw = {"code": "X", "source": "drop", "available": True,
           "daily": {"vol_ratio_20": 1.1, "volume_price": "量价中性", "vol_ma20": 1, "drop_me": 1},
           "intraday": {"bars": 241, "open30_share": 31.5, "drop_me": 1},
           "unit_check": "ok", "warnings": []}
    s = rd.slim_vol(raw)
    assert "vol_ratio_20" in s["daily"] and "drop_me" not in s["daily"]
    assert "open30_share" in s["intraday"] and "drop_me" not in s["intraday"]
    assert s["unit_check"] == "ok"
    assert rd.slim_vol({}) == {}
