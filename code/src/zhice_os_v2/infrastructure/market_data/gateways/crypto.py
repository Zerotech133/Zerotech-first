"""CRYPTO 网关（复刻 V1 CryptoGateway）：Binance data-api 镜像 + Gate.io，均免 key。

- 快照：Gate /spot/tickers（批量主源）→ Binance /ticker/24hr（补缺）。
- 日线：Binance /klines interval=1d（主）→ Gate /spot/candlesticks（兜底，列序不同！）。
- 分钟：Binance /klines interval=1m（UTC 交易日分页，单次上限1000，最多3页覆盖1440）。
符号：BTC/USDT → Binance BTCUSDT / Gate BTC_USDT。单位一律不缩放（base 量 / USDT quote）。
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from .dto import DailyBarDTO, LatestSnapshotDTO, MinuteBarDTO

_UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _dec(v: object) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        if isinstance(v, float) and v != v:
            return None
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _epoch_ms_utc(v: object) -> datetime | None:
    """毫秒 epoch → UTC datetime。缺失/异常返 None（调用方回退 now）。"""
    if v is None or v == "":
        return None
    try:
        return datetime.fromtimestamp(int(str(v)) / 1000, tz=UTC)
    except (ValueError, TypeError, OSError, OverflowError):
        return None


class BinanceSpotClient:
    def __init__(self) -> None:
        self._base = "https://data-api.binance.vision"

    @staticmethod
    def _sym(ts: str) -> str:
        return ts.replace("/", "")

    def tickers_24hr(self) -> list[dict]:
        r = requests.get(f"{self._base}/api/v3/ticker/24hr", headers=_UA, timeout=15)
        r.raise_for_status()
        return r.json()

    def snapshot_batch(self, ts_codes: list[str]) -> dict[str, LatestSnapshotDTO]:
        try:
            rows = self.tickers_24hr()
        except Exception:  # noqa: BLE001
            return {}
        by_sym = {d.get("symbol"): d for d in rows}
        out: dict[str, LatestSnapshotDTO] = {}
        for ts in ts_codes:
            d = by_sym.get(self._sym(ts))
            if not d:
                continue
            price = _dec(d.get("lastPrice"))
            if not price or price == 0:
                continue
            # 新鲜度护栏依赖源 tick 时间：用 24hr resp 的 closeTime，缺失才回退 now
            src_dt = _epoch_ms_utc(d.get("closeTime"))
            out[ts] = LatestSnapshotDTO(
                ts_code=ts, snapshot_time=src_dt or datetime.now(UTC), price=price,
                pre_close=_dec(d.get("prevClosePrice")), open=_dec(d.get("openPrice")),
                high=_dec(d.get("highPrice")), low=_dec(d.get("lowPrice")),
                volume_shares=_dec(d.get("volume")), turnover_cny=_dec(d.get("quoteVolume")),
                source_name="binance",
            )
        return out

    def daily(self, ts: str, limit: int = 60) -> list[DailyBarDTO]:
        try:
            _p: dict[str, Any] = {"symbol": self._sym(ts), "interval": "1d", "limit": limit}
            r = requests.get(f"{self._base}/api/v3/klines", headers=_UA, timeout=15, params=_p)
            r.raise_for_status()
            rows = r.json()
        except Exception:  # noqa: BLE001
            return []
        out = []
        for row in rows:
            if len(row) < 8:
                continue
            td = datetime.fromtimestamp(row[0] / 1000, tz=UTC).date()
            out.append(DailyBarDTO(
                ts_code=ts, trade_date=td, open=_dec(row[1]), high=_dec(row[2]),
                low=_dec(row[3]), close=_dec(row[4]), volume_shares=_dec(row[5]),
                turnover_cny=_dec(row[7]), adj_type="none", source_name="binance",
            ))
        return out

    def minute(self, ts: str, trade_date: str) -> list[MinuteBarDTO]:
        d = date.fromisoformat(trade_date)
        start_ms = int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)
        end_ms = start_ms + 24 * 3600 * 1000 - 1
        out: list[MinuteBarDTO] = []
        cursor = start_ms
        for _ in range(3):
            try:
                _p: dict[str, Any] = {
                    "symbol": self._sym(ts), "interval": "1m",
                    "startTime": cursor, "endTime": end_ms, "limit": 1000,
                }
                r = requests.get(f"{self._base}/api/v3/klines", headers=_UA, timeout=15, params=_p)
                r.raise_for_status()
                rows = r.json()
            except Exception:  # noqa: BLE001
                # C1：一页都没拿到还失败 → 整 code 拉取失败，上抛让 jobs 层
                # errors 真实计数（此前静默吞掉返回 []，fetch_minute 的 errors 恒 0）。
                # 已有部分数据时保留（部分覆盖优于零覆盖），仅中止后续页。
                if not out:
                    raise
                break
            if not rows:
                break
            for row in rows:
                if len(row) < 8:
                    continue
                bt = datetime.fromtimestamp(row[0] / 1000, tz=UTC)
                out.append(MinuteBarDTO(
                    ts_code=ts, bar_time=bt, trade_date=bt.date(), open=_dec(row[1]),
                    high=_dec(row[2]), low=_dec(row[3]), close=_dec(row[4]),
                    volume_shares=_dec(row[5]), turnover_cny=_dec(row[7]), source_name="binance",
                ))
            cursor = rows[-1][0] + 60_000
            if cursor > end_ms:
                break
        return out


class GateSpotClient:
    def __init__(self) -> None:
        self._base = "https://api.gateio.ws"

    @staticmethod
    def _sym(ts: str) -> str:
        return ts.replace("/", "_")

    def snapshot_batch(self, ts_codes: list[str]) -> dict[str, LatestSnapshotDTO]:
        try:
            r = requests.get(f"{self._base}/api/v4/spot/tickers", headers=_UA, timeout=15)
            r.raise_for_status()
            rows = r.json()
        except Exception:  # noqa: BLE001
            return {}
        by_pair = {d.get("currency_pair"): d for d in rows}
        out: dict[str, LatestSnapshotDTO] = {}
        for ts in ts_codes:
            d = by_pair.get(self._sym(ts))
            if not d:
                continue
            price = _dec(d.get("last"))
            if not price or price == 0:
                continue
            pre = None
            chg = _dec(d.get("change_percentage"))
            if chg is not None:
                try:
                    pre = price / (1 + chg / Decimal("100"))
                except (InvalidOperation, ZeroDivisionError):
                    pre = None
            # Gate /spot/tickers 不返回逐标的 tick 时间戳，无源时间可用 → 保留 now()。
            # CRYPTO 7×24 无停牌/收盘语义，快照新鲜度护栏意义弱，此回退可接受。
            out[ts] = LatestSnapshotDTO(
                ts_code=ts, snapshot_time=datetime.now(UTC), price=price, pre_close=pre,
                high=_dec(d.get("high_24h")), low=_dec(d.get("low_24h")),
                turnover_cny=_dec(d.get("quote_volume")), source_name="gateio",
            )
        return out

    def daily(self, ts: str, limit: int = 60) -> list[DailyBarDTO]:
        try:
            _p: dict[str, Any] = {"currency_pair": self._sym(ts), "interval": "1d", "limit": limit}
            r = requests.get(f"{self._base}/api/v4/spot/candlesticks", headers=_UA, timeout=15, params=_p)
            r.raise_for_status()
            rows = r.json()
        except Exception:  # noqa: BLE001
            return []
        out = []
        for row in rows:
            if len(row) < 6:
                continue
            # Gate 列序：[0]ts(秒) [1]额 [2]收 [3]高 [4]低 [5]开
            td = datetime.fromtimestamp(int(row[0]), tz=UTC).date()
            out.append(DailyBarDTO(
                ts_code=ts, trade_date=td, open=_dec(row[5]), high=_dec(row[3]),
                low=_dec(row[4]), close=_dec(row[2]), turnover_cny=_dec(row[1]),
                adj_type="none", source_name="gateio",
            ))
        return out


class CryptoGateway:
    def __init__(self) -> None:
        self._binance = BinanceSpotClient()
        self._gate = GateSpotClient()

    def close(self) -> None:
        pass

    def get_latest_snapshot_batch(self, ts_codes: list[str]) -> dict[str, LatestSnapshotDTO]:
        if not ts_codes:
            return {}
        out = self._gate.snapshot_batch(ts_codes)
        missing = [t for t in ts_codes if t not in out]
        if missing:
            out.update(self._binance.snapshot_batch(missing))
        return out

    def get_daily_bars_batch(
        self, ts_codes: list[str], limit: int = 60, start: str | None = None, end: str | None = None
    ) -> dict[str, list[DailyBarDTO]]:
        out: dict[str, list[DailyBarDTO]] = {}
        for ts in ts_codes:
            bars = self._binance.daily(ts, limit=limit) or self._gate.daily(ts, limit=limit)
            if bars:
                out[ts] = bars
        return out

    def get_minute_bars(self, ts_code: str, trade_date: str) -> list[MinuteBarDTO]:
        return self._binance.minute(ts_code, trade_date)
