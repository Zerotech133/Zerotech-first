# -*- coding: utf-8 -*-
"""xq_quote_router 单测（T4，2026-10-19）。

覆盖：no_proxy_env 进出还原、token 脱敏、fetch_snapshot 主备路由
（tushare 成功直用 / tushare 挂 akshare 备援 / 两源全挂 fail-visible
空行非空 errs）、fetch_daily 逐码聚合。全部 mock，不发真实网络请求。
跑法：pytest tests/test_xq_quote_router.py -q
"""
import os
import sys
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import xq_quote_router as R


# ── no_proxy_env ─────────────────────────────────────────────
def test_no_proxy_env_strips_and_restores():
    os.environ["HTTP_PROXY"] = "http://host-proxy:7890"
    os.environ["https_proxy"] = "http://host-proxy:7890"
    os.environ.pop("NO_PROXY", None)
    try:
        with R.no_proxy_env():
            assert "HTTP_PROXY" not in os.environ
            assert "https_proxy" not in os.environ
            assert os.environ.get("NO_PROXY") == "*"
        assert os.environ["HTTP_PROXY"] == "http://host-proxy:7890"
        assert os.environ["https_proxy"] == "http://host-proxy:7890"
        assert "NO_PROXY" not in os.environ
    finally:
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("https_proxy", None)


def test_no_proxy_env_preserves_existing_no_proxy():
    os.environ.pop("HTTP_PROXY", None)
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    try:
        with R.no_proxy_env():
            assert os.environ["NO_PROXY"] == "*"
        assert os.environ["NO_PROXY"] == "127.0.0.1,localhost"
    finally:
        os.environ.pop("NO_PROXY", None)


# ── mask_token ───────────────────────────────────────────────
def test_mask_token():
    assert R.mask_token("abcd1234efgh5678") == "abcd****5678"
    assert R.mask_token("short") == "****"
    assert R.mask_token(None) == "****"


# ── fetch_snapshot 路由 ──────────────────────────────────────
_ROW = {"symbol": "SH600519", "ts_code": "600519.SH", "current": 1500.0,
        "percent": 1.2, "chg": 18.0, "volume": 100, "amount": 1.5e8,
        "pe": None, "pb": None, "market_capital": None,
        "float_market_capital": None,
        "trade_date": "20261019", "timestamp": "2026-10-19 15:00:03"}


def test_fetch_snapshot_tushare_primary():
    with mock.patch.object(R, "snapshot_tushare",
                           return_value=([dict(_ROW)], [])), \
         mock.patch.object(R, "snapshot_akshare") as ak:
        rows, src, errs = R.fetch_snapshot(["SH600519"])
    assert src == "tushare" and len(rows) == 1
    ak.assert_not_called()          # 主源成功不触发备援


def test_fetch_snapshot_akshare_fallback():
    with mock.patch.object(R, "snapshot_tushare",
                           return_value=([], ["down"])), \
         mock.patch.object(R, "snapshot_akshare",
                           return_value=([dict(_ROW)], [])):
        rows, src, errs = R.fetch_snapshot(["SH600519"])
    assert src == "akshare" and len(rows) == 1
    assert any("tushare" in e for e in errs)   # 主源失败原因仍可见


def test_fetch_snapshot_both_down_fail_visible():
    with mock.patch.object(R, "snapshot_tushare",
                           return_value=([], ["t-down"])), \
         mock.patch.object(R, "snapshot_akshare",
                           return_value=([], ["a-down"])):
        rows, src, errs = R.fetch_snapshot(["SH600519"])
    assert rows == [] and src == ""        # 绝不静默空：errs 必须非空
    assert len(errs) >= 2


def test_fetch_snapshot_crash_still_falls_through():
    # 主源抛异常（非返回 errs）→ 备援仍接管
    with mock.patch.object(R, "snapshot_tushare",
                           side_effect=RuntimeError("boom")), \
         mock.patch.object(R, "snapshot_akshare",
                           return_value=([dict(_ROW)], [])):
        rows, src, errs = R.fetch_snapshot(["SH600519"])
    assert src == "akshare" and len(rows) == 1
    assert any("crash" in e for e in errs)


# ── fetch_daily 聚合 ─────────────────────────────────────────
def test_fetch_daily_mixed_sources():
    def ts_side(sym, days=300, ts_mod=None, pro=None):
        if sym == "SH600519":
            return [{"symbol": sym, "ts_code": "600519.SH",
                     "timestamp": "2026-10-16 00:00:00", "volume": 1,
                     "open": 1, "high": 1, "low": 1, "close": 1,
                     "chg": 0, "percent": 0, "turnoverrate": None,
                     "amount": 1, "trade_date": "20261016"}], "qfq"
        raise RuntimeError("no map")
    with mock.patch.object(R, "daily_tushare", side_effect=ts_side), \
         mock.patch.object(R, "daily_akshare",
                           return_value=[{"symbol": "SZ000001"}]):
        by_sym, note, errs = R.fetch_daily(["SH600519", "SZ000001"])
    assert set(by_sym) == {"SH600519", "SZ000001"}
    assert "tushare_qfq×1" in note and "akshare_qfq×1" in note


# ── 指数锚剔除（000001 双关污染实证 2026-10-19）──────────────
def test_is_index_symbol():
    assert R.is_index_symbol("SH000001") is True     # 上证指数
    assert R.is_index_symbol("SH000016") is True     # 上证50
    assert R.is_index_symbol("SZ399001") is True     # 深证成指
    assert R.is_index_symbol("SZ000001") is False    # 平安银行（个股！）
    assert R.is_index_symbol("SH600519") is False    # 茅台
    assert R.is_index_symbol("BJ835185") is False    # 北交所不误判


def test_snapshot_tushare_excludes_index():
    # 指数锚在 pairs 构建处剔除，errs 留痕（防 tushare 新浪源双关污染）
    with mock.patch.object(R, "setup_tushare",
                           return_value=(mock.MagicMock(), mock.MagicMock())):
        rows, errs = R.snapshot_tushare(["SH000001", "SH600519"])
    assert any("指数锚" in e for e in errs)
    # mock 的 realtime_quote 返回 MagicMock→df.empty 为 MagicMock 属性
    # （非真值路径不进 rows），这里只验指数剔除与不抛异常
