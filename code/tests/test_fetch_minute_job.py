"""C1 修复：CRYPTO 分钟采集吞异常。

- gateway：BinanceSpotClient.minute() 整个 code 一页都拉不到且发生异常时必须上抛
  （不再静默 break 返回 []）；首页成功、后续页失败保留部分数据不抛。
- jobs：run_fetch_minute_job 真实统计单 code 失败数（gateway 上抛后 errors 不再恒 0）
  并记 warning 日志；universe 成功率 < 60% 时经现有 send_alert 发 WARN 告警。
"""
import logging

import pytest

from zhice_os_v2.infrastructure.market_data.gateways.crypto import BinanceSpotClient

_REQ = "zhice_os_v2.infrastructure.market_data.gateways.crypto.requests.get"


class _Resp:
    def __init__(self, rows):
        self._rows = rows

    def raise_for_status(self):
        pass

    def json(self):
        return self._rows


def _kline(ts_ms: int) -> list:
    return [ts_ms, "99", "101", "98", "100", "1.5", 0, "150"]


# ── gateway 错误路径 ──────────────────────────────────────────────────

def test_minute_total_failure_raises(monkeypatch):
    """整 code 全部页失败 → 上抛原异常（此前静默吞掉返回 []，errors 恒 0 的根因）。"""
    def boom(*a, **k):
        raise ConnectionError("binance down")

    monkeypatch.setattr(_REQ, boom)
    with pytest.raises(ConnectionError):
        BinanceSpotClient().minute("BTC/USDT", "2026-09-07")


def test_minute_partial_failure_keeps_fetched_rows(monkeypatch):
    """首页成功、后续页失败 → 保留已得部分数据（部分覆盖优于零覆盖），不抛。"""
    calls = {"n": 0}

    def fake_get(url, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Resp([_kline(1_800_000_000_000)])
        raise ConnectionError("page2 down")

    monkeypatch.setattr(_REQ, fake_get)
    bars = BinanceSpotClient().minute("BTC/USDT", "2026-09-07")
    assert len(bars) == 1
    assert bars[0].ts_code == "BTC/USDT"


def test_minute_no_data_without_error_returns_empty(monkeypatch):
    """源正常返回空（当日真无数据）→ 维持返回 []，不是失败、不抛。"""
    monkeypatch.setattr(_REQ, lambda *a, **k: _Resp([]))
    assert BinanceSpotClient().minute("BTC/USDT", "2026-09-07") == []


# ── jobs 层统计/告警透传（打桩，无网络无库）──────────────────────────

class _StubGateway:
    """fail_codes 中的 code 拉取抛异常，其余返回 n_rows 根。"""

    def __init__(self, fail_codes: set[str], n_rows: int = 2):
        self._fail = fail_codes
        self._n = n_rows

    def get_minute_bars(self, code: str, d: str) -> list:
        if code in self._fail:
            raise RuntimeError(f"fetch fail {code}")
        from datetime import UTC, datetime
        from decimal import Decimal

        from zhice_os_v2.infrastructure.market_data.gateways.dto import MinuteBarDTO
        return [
            MinuteBarDTO(
                ts_code=code, bar_time=datetime.now(UTC),
                trade_date=datetime.now(UTC).date(), close=Decimal("1"),
            )
            for _ in range(self._n)
        ]

    def close(self) -> None:
        pass


def _run(codes: list[str], g, monkeypatch, caplog):
    from zhice_os_v2 import scheduler_jobs

    monkeypatch.setattr(
        "zhice_os_v2.infrastructure.market_data.universe.minute_universe",
        lambda m, top_n=None: codes,
    )
    monkeypatch.setattr(
        "zhice_os_v2.infrastructure.market_data.gateways.registry.get_data_gateway",
        lambda m: g,
    )
    monkeypatch.setattr(
        "zhice_os_v2.infrastructure.market_data.ingest.upsert_minute",
        lambda rows, m: 0,
    )
    alerted: list[tuple] = []
    monkeypatch.setattr(
        "zhice_os_v2.application.services.alert_service.send_alert",
        lambda *a, **k: alerted.append(a) or True,
    )
    with caplog.at_level(logging.WARNING, logger="zhice_os_v2.scheduler_jobs"):
        result = scheduler_jobs.run_fetch_minute_job("CRYPTO")
    return result, alerted


def test_fetch_minute_counts_real_errors_and_logs(monkeypatch, caplog):
    """gateway 上抛后 jobs 真实计数失败 code 并记 warning 日志。"""
    g = _StubGateway(fail_codes={"SOL/USDT"})
    result, _ = _run(["BTC/USDT", "ETH/USDT", "SOL/USDT"], g, monkeypatch, caplog)
    assert result["errors"] == 1
    assert result["universe"] == 3
    assert result["rows_fetched"] == 4  # 2 成功 code × 2 根
    warnings = [r for r in caplog.records
                if r.levelno == logging.WARNING and "SOL/USDT" in r.getMessage()]
    assert warnings, "单 code 失败必须记 warning 日志（含 code）"


def test_fetch_minute_low_success_sends_warn_alert(monkeypatch, caplog):
    """universe 成功率 < 60%（失败率 ≥ 40%）→ 现有 send_alert 发 WARN。"""
    g = _StubGateway(fail_codes={"A", "B"})  # 2/3 失败 ≈ 66.7%
    result, alerted = _run(["A", "B", "C"], g, monkeypatch, caplog)
    assert result["errors"] == 2
    assert alerted, "成功率 < 60% 必须发告警"
    assert alerted[0][0] == "WARN"
    assert "CRYPTO" in alerted[0][1]


def test_fetch_minute_healthy_round_no_alert(monkeypatch, caplog):
    """成功率 ≥ 60% 的正常轮次不告警（防告警噪音）。"""
    g = _StubGateway(fail_codes=set())
    _, alerted = _run(["A", "B", "C"], g, monkeypatch, caplog)
    assert alerted == []
