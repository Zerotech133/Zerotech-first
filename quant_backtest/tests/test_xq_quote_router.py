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
from types import SimpleNamespace

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
    ts_mod, pro = object(), object()
    with mock.patch.object(R, "setup_tushare", return_value=(ts_mod, pro)) as setup, \
         mock.patch.object(R, "daily_tushare", side_effect=ts_side) as daily_ts, \
         mock.patch.object(R, "daily_akshare",
                           return_value=[{"symbol": "SZ000001"}]) as daily_ak:
        by_sym, note, errs = R.fetch_daily(["SH600519", "SZ000001"])
    setup.assert_called_once_with()
    assert daily_ts.call_args_list == [
        mock.call("SH600519", 300, ts_mod, pro),
        mock.call("SZ000001", 300, ts_mod, pro),
    ]
    daily_ak.assert_called_once_with("SZ000001", 300)
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


def test_fetch_snapshot_rejects_only_indices_without_provider_calls():
    with mock.patch.object(R, "snapshot_tushare") as ts, \
         mock.patch.object(R, "snapshot_akshare") as ak:
        rows, source, errs = R.fetch_snapshot(["SH000001", "SZ399001"])
    assert (rows, source) == ([], "")
    assert len(errs) == 2 and all("指数锚" in err for err in errs)
    ts.assert_not_called()
    ak.assert_not_called()


def test_fetch_snapshot_mixed_sources_filter_indices_on_fallback():
    stock = dict(_ROW)
    bank = dict(_ROW, symbol="SZ000001", ts_code="000001.SZ")
    with mock.patch.object(R, "snapshot_tushare", return_value=([], ["down"])) as ts, \
         mock.patch.object(R, "snapshot_akshare", return_value=([bank], [])) as ak:
        rows, source, errs = R.fetch_snapshot(
            ["SH000001", "SZ399001", "SZ000001"])
    assert rows == [bank] and source == "akshare"
    assert any("SH000001" in err for err in errs)
    assert any("SZ399001" in err for err in errs)
    assert any("down" in err for err in errs)
    ts.assert_called_once_with(["SZ000001"])
    ak.assert_called_once_with(["SZ000001"])
    with mock.patch.object(R, "snapshot_tushare", return_value=([stock], [])) as ts, \
         mock.patch.object(R, "snapshot_akshare") as ak:
        rows, source, errs = R.fetch_snapshot(["SH000001", "SH600519"])
    assert rows == [stock] and source == "tushare"
    assert any("指数锚" in err for err in errs)
    ts.assert_called_once_with(["SH600519"])
    ak.assert_not_called()


def test_fetch_snapshot_mixed_indices_both_providers_fail_visible():
    with mock.patch.object(R, "snapshot_tushare", return_value=([], ["t-down"])) as ts, \
         mock.patch.object(R, "snapshot_akshare", return_value=([], ["a-down"])) as ak:
        rows, source, errs = R.fetch_snapshot(["SZ399001", "SZ000001"])
    assert rows == [] and source == ""
    assert any("SZ399001" in err for err in errs)
    assert any("t-down" in err for err in errs)
    assert any("a-down" in err for err in errs)
    ts.assert_called_once_with(["SZ000001"])
    ak.assert_called_once_with(["SZ000001"])


def test_fetch_snapshot_rejects_provider_returned_index_and_tries_fallback():
    index = dict(_ROW, symbol="SH000001", ts_code="000001.SH")
    bank = dict(_ROW, symbol="SZ000001", ts_code="000001.SZ")
    with mock.patch.object(R, "snapshot_tushare", return_value=([index], [])) as ts, \
         mock.patch.object(R, "snapshot_akshare",
                           return_value=([index, bank], [])) as ak:
        rows, source, errs = R.fetch_snapshot(["SH000001", "SZ000001"])
    assert rows == [bank] and source == "akshare"
    assert sum("已剔除" in err for err in errs) == 2
    ts.assert_called_once_with(["SZ000001"])
    ak.assert_called_once_with(["SZ000001"])


def test_fetch_snapshot_only_unrequested_rows_fail_visible():
    index = dict(_ROW, symbol="SZ399001", ts_code="399001.SZ")
    with mock.patch.object(R, "snapshot_tushare", return_value=([index], [])), \
         mock.patch.object(R, "snapshot_akshare", return_value=([index], [])):
        rows, source, errs = R.fetch_snapshot(["SZ000001"])
    assert rows == [] and source == ""
    assert len(errs) == 2 and all("已剔除" in err for err in errs)


def test_snapshot_tushare_direct_only_indices_skips_setup():
    with mock.patch.object(R, "setup_tushare") as setup:
        rows, errs = R.snapshot_tushare(["SH000001", "SZ399001"])
    assert rows == [] and any("指数锚" in err for err in errs)
    setup.assert_not_called()


def test_snapshot_akshare_direct_filters_indices_before_import():
    with mock.patch.dict(sys.modules, {"akshare": None}), \
         mock.patch.object(R.X, "to_ts_code") as code:
        rows, errs = R.snapshot_akshare(["SH000001", "SZ399001"])
    assert rows == [] and len(errs) == 2
    assert all("指数锚" in err for err in errs)
    code.assert_not_called()


def test_snapshot_akshare_direct_mixed_filters_response():
    class FakeFrame:
        empty = False

        def iterrows(self):
            yield 0, {"代码": "000001", "最新价": 11.5}
            yield 1, {"代码": "600519", "最新价": 1500.0}

    ak = SimpleNamespace(stock_zh_a_spot_em=mock.Mock(return_value=FakeFrame()))
    with mock.patch.dict(sys.modules, {"akshare": ak}):
        rows, errs = R.snapshot_akshare(["SH000001", "SH600519"])
    assert [row["symbol"] for row in rows] == ["SH600519"]
    assert any("SH000001" in err for err in errs)
    ak.stock_zh_a_spot_em.assert_called_once_with()


def test_fetch_daily_rejects_only_indices_without_provider_calls():
    with mock.patch.object(R, "setup_tushare") as setup, \
         mock.patch.object(R, "daily_tushare") as ts, \
         mock.patch.object(R, "daily_akshare") as ak:
        by_sym, note, errs = R.fetch_daily(["SH000001", "SZ399001"])
    assert by_sym == {} and note == ""
    assert len(errs) == 2 and all("指数锚" in err for err in errs)
    setup.assert_not_called()
    ts.assert_not_called()
    ak.assert_not_called()


def test_fetch_daily_mixed_indices_and_failure_keep_stock_fallback():
    with mock.patch.object(R, "setup_tushare", return_value=(object(), object())) as setup, \
         mock.patch.object(R, "daily_tushare", side_effect=RuntimeError("down")) as ts, \
         mock.patch.object(R, "daily_akshare",
                           side_effect=lambda sym, days: [{"symbol": sym}]) as ak:
        by_sym, note, errs = R.fetch_daily(
            ["SH000001", "SZ399001", "SZ000001", "SH600519"])
    assert set(by_sym) == {"SZ000001", "SH600519"}
    assert note == "akshare_qfq×2"
    assert sum("指数锚" in err for err in errs) == 2
    assert sum("down" in err for err in errs) == 2
    setup.assert_called_once_with()
    assert [call.args[0] for call in ts.call_args_list] == ["SZ000001", "SH600519"]
    assert [call.args[0] for call in ak.call_args_list] == ["SZ000001", "SH600519"]


def test_fetch_daily_filters_primary_rows_before_success():
    good = {"symbol": "SZ000001", "close": 10}
    polluted = [{"symbol": "SH000001"}, {"symbol": "SZ399001"},
                {"symbol": "SH600519"}]
    with mock.patch.object(R, "setup_tushare", return_value=(object(), object())), \
         mock.patch.object(R, "daily_tushare",
                           return_value=(polluted + [good], "qfq")), \
         mock.patch.object(R, "daily_akshare") as ak:
        by_sym, note, errs = R.fetch_daily(["SZ000001"])
    assert by_sym == {"SZ000001": [good]}
    assert note == "tushare_qfq×1"
    assert any("tushare" in err and "已剔除" in err for err in errs)
    ak.assert_not_called()


def test_fetch_daily_primary_all_polluted_falls_back_to_filtered_stock():
    good = {"symbol": "SZ000001", "close": 10}
    polluted = [{"symbol": "SH000001"}, {"symbol": "SZ399001"},
                {"symbol": "SH600519"}]
    with mock.patch.object(R, "setup_tushare", return_value=(object(), object())), \
         mock.patch.object(R, "daily_tushare", return_value=(polluted, "qfq")), \
         mock.patch.object(R, "daily_akshare", return_value=polluted + [good]) as ak:
        by_sym, note, errs = R.fetch_daily(["SZ000001"])
    assert by_sym == {"SZ000001": [good]}
    assert note == "akshare_qfq×1"
    assert any("tushare" in err and "无有效" in err for err in errs)
    assert any("akshare" in err and "已剔除" in err for err in errs)
    ak.assert_called_once_with("SZ000001", 300)


def test_fetch_daily_both_sources_all_polluted_fail_visible():
    polluted = [{"symbol": "SH000001"}, {"symbol": "SZ399001"},
                {"symbol": "SH600519"}]
    with mock.patch.object(R, "setup_tushare", return_value=(object(), object())), \
         mock.patch.object(R, "daily_tushare", return_value=(polluted, "raw")), \
         mock.patch.object(R, "daily_akshare", return_value=polluted):
        by_sym, note, errs = R.fetch_daily(["SZ000001"])
    assert by_sym == {} and note == ""
    assert sum("已剔除" in err for err in errs) == 2
    assert sum("无有效" in err for err in errs) == 2


def test_fetch_daily_empty_primary_and_clean_fallback():
    good = {"symbol": "SZ000001"}
    with mock.patch.object(R, "setup_tushare", return_value=(object(), object())), \
         mock.patch.object(R, "daily_tushare", return_value=([], "qfq")), \
         mock.patch.object(R, "daily_akshare", return_value=[good]):
        by_sym, note, errs = R.fetch_daily(["SZ000001"])
    assert by_sym == {"SZ000001": [good]}
    assert note == "akshare_qfq×1"
    assert any("tushare" in err and "无有效" in err for err in errs)


def test_daily_akshare_direct_rejects_index_before_import():
    with mock.patch.dict(sys.modules, {"akshare": None}):
        for index in ("SH000001", "SZ399001"):
            try:
                R.daily_akshare(index)
            except RuntimeError as exc:
                assert "指数锚" in str(exc)
            else:
                raise AssertionError("指数不得进入 akshare 日线备援")
