# -*- coding: utf-8 -*-
"""regime_center 单测：判据逐条对齐 watching v1.0 §5.1，缺源不假绿。"""
import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import regime_center as rc  # noqa: E402


def _series(n, start=3000.0, step=0.0, wave=0.0):
    """构造收盘序列：step>0 上行 / <0 下行；wave 加正弦扰动。"""
    import math
    return [start + step * i + wave * math.sin(i / 5.0) for i in range(n)]


def _df(n=200, **kw):
    return pd.DataFrame({
        "trade_date": ["20260%03d" % (i + 1) for i in range(n)],
        "close": _series(n, **kw),
    })


# ── 指数特征 ─────────────────────────────────────────────────
def test_index_features_uptrend():
    f = rc.index_features(_df(200, start=3000.0, step=5.0))
    assert f["available"] is True
    assert f["close"] > f["ma60"] and f["ma60_slope5"] > 0 and f["macd_dif"] > 0


def test_index_features_downtrend():
    f = rc.index_features(_df(200, start=5000.0, step=-5.0))
    assert f["available"] is True
    assert f["close"] < f["ma60"] and f["ma60_slope5"] < 0 and f["macd_dif"] < 0


def test_index_features_insufficient_rows():
    f = rc.index_features(_df(30, start=3000.0, step=1.0))
    assert f["available"] is False and "insufficient" in f["reason"]


def test_index_features_empty():
    f = rc.index_features(pd.DataFrame())
    assert f["available"] is False and f["reason"] == "index_empty"


def test_index_features_trade_date_cut():
    f = rc.index_features(_df(200, start=3000.0, step=1.0), trade_date="20260100")
    assert f["available"] is True and f["trade_date"] == "20260100"


# ── 判据（§5.1 三条门 + 四条否决）──────────────────────────────
def test_evaluate_risk_on():
    idx = {"available": True, "close": 3100.0, "ma60": 3000.0,
           "ma60_slope5": 20.0, "macd_dif": 5.0, "macd_dea": 2.0,
           "close_below_ma60_streak": 0}
    v = rc.evaluate(idx, {"available": True, "up_ratio": 0.6})
    assert v["regime"] == "risk_on" and all(v["gates"].values())


def test_evaluate_neutral_when_only_two_gates():
    # DIF<0 但 DEA>0 → 不构成「DIF、DEA 双负」否决，三条门缺一 → neutral
    idx = {"available": True, "close": 3100.0, "ma60": 3000.0,
           "ma60_slope5": 20.0, "macd_dif": -1.0, "macd_dea": 0.5,
           "close_below_ma60_streak": 0}
    v = rc.evaluate(idx, {"available": True, "up_ratio": 0.6})
    assert v["regime"] == "neutral"
    assert v["gates"]["ma60_above"] and v["gates"]["slope_up"]
    assert v["gates"]["dif_positive"] is False


def test_evaluate_risk_off_dif_dea_both_negative():
    idx = {"available": True, "close": 3100.0, "ma60": 3000.0,
           "ma60_slope5": 20.0, "macd_dif": -1.0, "macd_dea": -2.0,
           "close_below_ma60_streak": 0}
    v = rc.evaluate(idx, {"available": True, "up_ratio": 0.6})
    assert "dif_and_dea_both_negative" in v["risk_off_reasons"]
    assert v["regime"] == "risk_off"


def test_evaluate_risk_off_below_ma60_two_days():
    idx = {"available": True, "close": 2900.0, "ma60": 3000.0,
           "ma60_slope5": 5.0, "macd_dif": 1.0, "macd_dea": 0.5,
           "close_below_ma60_streak": 2}
    v = rc.evaluate(idx, {"available": True, "up_ratio": 0.6})
    assert any("close_below_ma60_2d" in r for r in v["risk_off_reasons"])


def test_evaluate_risk_off_breadth():
    idx = {"available": True, "close": 3100.0, "ma60": 3000.0,
           "ma60_slope5": 20.0, "macd_dif": 5.0, "macd_dea": 2.0,
           "close_below_ma60_streak": 0}
    v = rc.evaluate(idx, {"available": True, "up_ratio": 0.2})
    assert any("breadth_risk_off" in r for r in v["risk_off_reasons"])
    assert v["regime"] == "risk_off"


def test_evaluate_index_missing_is_unknown_not_risk_on():
    v = rc.evaluate({"available": False, "reason": "index_empty"}, None)
    assert v["regime"] == "unknown" and v["gates"] == {}
    assert v["unavailable_reasons"] == ["index:index_empty"]


def test_evaluate_breadth_missing_recorded_not_guessed():
    idx = {"available": True, "close": 3100.0, "ma60": 3000.0,
           "ma60_slope5": 20.0, "macd_dif": 5.0, "macd_dea": 2.0,
           "close_below_ma60_streak": 0}
    v = rc.evaluate(idx, {"available": False, "reason": "ts_daily_empty"})
    assert v["regime"] == "risk_on"          # 广度缺源不影响其余判据
    assert "breadth:ts_daily_empty" in v["unavailable_reasons"]


# ── 广度与总量 ───────────────────────────────────────────────
def _fake_reader(df):
    return lambda table, start=None, end=None, codes=None, fields=None, **kw: df


def test_breadth_volume_normal():
    df = pd.DataFrame({
        "ts_code": ["600000.SH", "000001.SZ", "830001.BJ"],
        "trade_date": ["20261016"] * 3,
        "change": [1.0, -1.0, 0.5],
        "amount": [2e6, 3e6, 1e5],
        "vol": [1.0, 2.0, 0.1],
    })
    b = rc.breadth_volume("20261016", reader=_fake_reader(df))
    assert b["available"] is True
    assert b["up_count"] == 2 and b["down_count"] == 1
    assert abs(b["up_ratio"] - 2 / 3) < 1e-3
    assert set(b["amount_yi"]) == {"SH", "SZ", "BJ"}


def test_breadth_volume_empty_flagged():
    b = rc.breadth_volume("20261016", reader=_fake_reader(pd.DataFrame()))
    assert b["available"] is False and b["reason"] == "ts_daily_empty"


def test_breadth_volume_no_row_for_date():
    df = pd.DataFrame({"ts_code": ["600000.SH"], "trade_date": ["20261010"],
                       "change": [1.0], "amount": [1.0], "vol": [1.0]})
    b = rc.breadth_volume("20261016", reader=_fake_reader(df))
    assert b["available"] is False and "no_row_for_date" in b["reason"]


# ── 快照与落盘 ───────────────────────────────────────────────
def test_build_snapshot_missing_index_flagged(tmp_path, monkeypatch):
    snap = rc.build_snapshot(
        trade_date="20261016",
        index_path=str(tmp_path / "nope.parquet"),
        reader=_fake_reader(pd.DataFrame()))
    assert snap["regime"] == "unknown"
    assert snap["data_quality"]["available"] is False
    assert len(snap["data_quality"]["gaps"]) >= 2
    assert snap["criteria"]["source"].startswith("watching-standard-capability")


def test_build_snapshot_stale_index_flagged(tmp_path):
    p = tmp_path / "sh000001.parquet"
    _df(200, start=3000.0, step=1.0).to_parquet(p, index=False)
    snap = rc.build_snapshot(trade_date="20261016", index_path=str(p),
                             reader=_fake_reader(pd.DataFrame()))
    assert any("index_stale" in g for g in snap["data_quality"]["gaps"])


def test_write_snapshot_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "OUT_DIR", str(tmp_path))
    snap = {"trade_date": "20261016", "regime": "risk_off"}
    p1 = rc.write_snapshot(snap, out_dir=str(tmp_path))
    p2 = rc.write_snapshot(snap, out_dir=str(tmp_path))
    assert p1 == p2 and os.path.exists(p1)
    with open(p1, encoding="utf-8") as f:
        assert json.load(f)["regime"] == "risk_off"
    assert len([f for f in os.listdir(str(tmp_path)) if f.startswith("snapshot_")]) == 1


def test_latest_snapshot_none_when_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "OUT_DIR", str(tmp_path))
    assert rc.latest_snapshot(out_dir=str(tmp_path)) is None


# ── 纪律：零 LLM 零网络 ───────────────────────────────────────
def test_no_llm_or_network_imports():
    src = open(rc.__file__, encoding="utf-8").read()
    for banned in ("requests", "urllib.request", "socket", "openai", "tushare"):
        assert ("import " + banned) not in src, "引擎不得引入 %s（零网络纪律）" % banned


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
