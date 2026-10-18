"""run_fetch_daily_job 采集宇宙口径回归锁。

背景：US 日线走直连逐-code 源，f6428cb 为防 Nasdaq 限流将非-CN/HK 市场限到小宇宙。
US 由 top_n=200 扩到 500 以覆盖更多活跃股（长尾非交易标的仍不采）；CRYPTO 保持 200
（仅数十标的即全覆盖）。此测锁死每市场的 top_n，防未来误改把 US 打回 200 或让全宇宙
逐-code 触发限流。CN/HK 走全量 daily_universe，不受 top_n 约束。

snapshot_universe / get_data_gateway 均为 run_fetch_daily_job 内部延迟 import，故 patch
源模块符号；codes 返回 [] → 无网络采集、无 PG。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from zhice_os_v2 import scheduler_jobs


def _run_capture(market: str) -> dict:
    """跑 run_fetch_daily_job，捕获传给 snapshot_universe 的 (market, top_n)。"""
    captured: dict = {}

    def fake_snapshot_universe(mkt, top_n=None):
        captured["market"] = mkt
        captured["top_n"] = top_n
        return []  # 空宇宙 → 不进采集循环、不触网络/DB

    def fake_daily_universe(mkt):
        captured["daily_universe_called"] = mkt
        return []

    with patch(
        "zhice_os_v2.infrastructure.market_data.universe.snapshot_universe",
        fake_snapshot_universe,
    ), patch(
        "zhice_os_v2.infrastructure.market_data.universe.daily_universe",
        fake_daily_universe,
    ), patch(
        "zhice_os_v2.infrastructure.market_data.gateways.registry.get_data_gateway",
        return_value=MagicMock(),
    ):
        result = scheduler_jobs.run_fetch_daily_job(market, limit=1)
    return {"captured": captured, "result": result}


def test_us_daily_universe_top_n_is_500():
    """US 必须用 top_n=500（f6428cb 的 200 扩容后的值）。"""
    out = _run_capture("US")
    assert out["captured"]["top_n"] == 500
    assert out["captured"]["market"] == "US"
    # US 走 else 分支，不应调用全量 daily_universe
    assert "daily_universe_called" not in out["captured"]


def test_crypto_daily_universe_top_n_is_200():
    """CRYPTO 保持 top_n=200（数十标的即全覆盖，无需扩容）。"""
    out = _run_capture("CRYPTO")
    assert out["captured"]["top_n"] == 200


@pytest.mark.parametrize("market", ["CN", "HK"])
def test_cn_hk_use_full_daily_universe_not_topn(market):
    """CN/HK 走全量 daily_universe，不经 snapshot_universe 的 top_n 截断。"""
    out = _run_capture(market)
    assert out["captured"].get("daily_universe_called") == market
    assert "top_n" not in out["captured"]  # snapshot_universe 未被调用


def test_empty_universe_skips_pre_close_backfill_no_pg():
    """空宇宙（written=0）不触发 pre_close 回填 → 不连 PG（保纯 mock 契约）。"""
    with patch.object(scheduler_jobs, "backfill_pre_close") as bf:
        out = _run_capture("US")  # snapshot_universe→[] → 无采集、written=0
    bf.assert_not_called()
    assert out["result"]["rows_written"] == 0
    assert out["result"]["pre_close_filled"] == 0


def test_written_rows_trigger_pre_close_backfill():
    """写了行 → 调 backfill_pre_close，结果带 pre_close_filled（派生 pre_close 已接线）。"""
    gw = MagicMock()
    gw.get_daily_bars_batch.return_value = {"AAPL": [object()]}  # 1 行成交
    with patch(
        "zhice_os_v2.infrastructure.market_data.universe.snapshot_universe",
        return_value=["AAPL"],
    ), patch(
        "zhice_os_v2.infrastructure.market_data.gateways.registry.get_data_gateway",
        return_value=gw,
    ), patch(
        "zhice_os_v2.infrastructure.market_data.ingest.upsert_daily",
        return_value=1,
    ), patch.object(scheduler_jobs, "backfill_pre_close", return_value=7) as bf:
        result = scheduler_jobs.run_fetch_daily_job("US", limit=1)
    bf.assert_called_once_with("US")
    assert result["rows_written"] == 1
    assert result["pre_close_filled"] == 7
