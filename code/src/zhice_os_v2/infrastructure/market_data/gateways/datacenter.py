"""DataCenter Open API 网关（CN 主源，复刻 V1 DataCenterGateway）。

远程 HTTP 中台，X-API-Key 鉴权，返回信封 {code,msg,data}，code==0 成功、6001 限流。
端点：POST /open/api/quote/daily/batch、POST /open/api/quote/latest/batch、
     GET /open/api/quote/min/{ts_code}?date=YYYY-MM-DD。
"""
from __future__ import annotations

import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import httpx

from zhice_os_v2.config.settings import get_settings

from .dto import DailyBarDTO, LatestSnapshotDTO, MinuteBarDTO

_THROTTLE_CODE = 6001


def _dec(v: object) -> Decimal | None:
    if v is None:
        return None
    try:
        if isinstance(v, float) and v != v:  # NaN
            return None
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _parse_date(s: object) -> date | None:
    if not s:
        return None
    txt = str(s)
    try:
        if len(txt) >= 5 and txt[4] == "-":
            return date.fromisoformat(txt[:10])
        return datetime.strptime(txt[:8], "%Y%m%d").date()
    except (ValueError, TypeError):
        return None


def _parse_dt(s: object) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s))
    except (ValueError, TypeError):
        return None


class DataCenterError(RuntimeError):
    pass


class DataCenterGateway:
    """CN 行情网关。每次 new 一个 httpx.Client（有状态，用完 close）。"""

    def __init__(self) -> None:
        s = get_settings()
        self._base = s.datacenter_base_url.rstrip("/")
        self._api_key = s.datacenter_api_key
        self._retry_max = max(1, int(s.datacenter_retry_max))
        timeout = max(1.0, s.datacenter_timeout_ms / 1000.0)
        self._client = httpx.Client(
            timeout=timeout, headers={"X-API-Key": self._api_key}
        )

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    # ---------- 传输层：6001 指数退避重试 ----------
    def _request(self, method: str, path: str, *, params=None, payload=None) -> dict:
        url = f"{self._base}{path}"
        last_exc: Exception | None = None
        for attempt in range(1, self._retry_max + 1):
            try:
                if method == "GET":
                    resp = self._client.get(url, params=params)
                else:
                    resp = self._client.post(url, json=payload)
                resp.raise_for_status()
                body = resp.json()
                code = body.get("code", 0)
                if code == _THROTTLE_CODE:
                    time.sleep(2 ** attempt)  # 2s,4s,8s...
                    continue
                if code != 0:
                    raise DataCenterError(f"datacenter code={code} msg={body.get('msg','')}")
                return body.get("data") or {}
            except httpx.TimeoutException as exc:
                last_exc = exc
                continue  # 超时重试
            except DataCenterError:
                raise  # 非 6001 业务错误不重试
            except httpx.HTTPError as exc:
                raise DataCenterError(f"datacenter http error: {exc}") from exc
        if last_exc:
            raise DataCenterError(f"datacenter retries exhausted: {last_exc}")
        return {}

    # ---------- Daily ----------
    def get_daily_bars_batch(
        self, ts_codes: list[str], limit: int = 60, start: str | None = None, end: str | None = None
    ) -> dict[str, list[DailyBarDTO]]:
        if not ts_codes:
            return {}
        payload: dict = {"tsCodes": list(ts_codes), "limit": limit}
        if start:
            payload["start"] = start
        if end:
            payload["end"] = end
        data = self._request("POST", "/open/api/quote/daily/batch", payload=payload)
        out: dict[str, list[DailyBarDTO]] = {}
        for ts_code, items in (data or {}).items():
            bars = [self._parse_daily_bar(ts_code, it) for it in (items or [])]
            out[ts_code] = [b for b in bars if b is not None]
        return out

    def _parse_daily_bar(self, ts_code: str, item: dict) -> DailyBarDTO | None:
        td = _parse_date(item.get("tradeDate"))
        if td is None:
            return None
        close = _dec(item.get("close"))
        vol_raw = _dec(item.get("vol"))       # 手
        amount_raw = _dec(item.get("amount"))  # 千元
        volume_shares = vol_raw * Decimal("100") if vol_raw is not None else None
        turnover_cny = amount_raw * Decimal("1000") if amount_raw is not None else None
        # VWAP≈close 自动探测：标准换算 VWAP 偏离>30% 且 原值口径 VWAP 偏离<30% → 上游已是股/元
        if (
            vol_raw and amount_raw and close and volume_shares and turnover_cny
            and vol_raw != 0 and close != 0
        ):
            try:
                dev_std = abs(turnover_cny / volume_shares / close - 1)
                dev_alt = abs(amount_raw / vol_raw / close - 1)
                if dev_std > Decimal("0.3") and dev_alt < Decimal("0.3"):
                    volume_shares = vol_raw
                    turnover_cny = amount_raw
            except (InvalidOperation, ZeroDivisionError):
                pass
        return DailyBarDTO(
            ts_code=ts_code,
            trade_date=td,
            open=_dec(item.get("open")),
            high=_dec(item.get("high")),
            low=_dec(item.get("low")),
            close=close,
            pre_close=_dec(item.get("preClose")),
            change_amount=_dec(item.get("change")),
            pct_change=_dec(item.get("pctChg")),
            volume_shares=volume_shares,
            turnover_cny=turnover_cny,
            adj_type="none",
            source_name="datacenter",
        )

    # ---------- Minute ----------
    def get_minute_bars(self, ts_code: str, trade_date: str) -> list[MinuteBarDTO]:
        data = self._request(
            "GET", f"/open/api/quote/min/{ts_code}", params={"date": trade_date}
        )
        items = data.get("list") if isinstance(data, dict) else data
        out: list[MinuteBarDTO] = []
        for it in (items or []):
            bt = _parse_dt(it.get("tradeTime"))
            if bt is None:
                continue
            close = _dec(it.get("close"))
            vol = _dec(it.get("vol"))     # min 口径=股
            amt = _dec(it.get("amount"))  # min 口径=元
            # 反向纠偏：个别旧口径为 手/千元
            if vol and amt and close and vol != 0 and close != 0:
                try:
                    if abs(amt / vol / close - 1) > Decimal("0.3"):
                        alt = (amt * Decimal("1000")) / (vol * Decimal("100"))
                        if abs(alt / close - 1) < Decimal("0.3"):
                            vol = vol * Decimal("100")
                            amt = amt * Decimal("1000")
                except (InvalidOperation, ZeroDivisionError):
                    pass
            out.append(MinuteBarDTO(
                ts_code=ts_code,
                bar_time=bt,
                trade_date=bt.date(),
                open=_dec(it.get("open")),
                high=_dec(it.get("high")),
                low=_dec(it.get("low")),
                close=close,
                volume_shares=vol,
                turnover_cny=amt,
                source_name="datacenter",
            ))
        return out

    # ---------- Latest snapshot ----------
    def get_latest_snapshot_batch(self, ts_codes: list[str]) -> dict[str, LatestSnapshotDTO]:
        if not ts_codes:
            return {}
        data = self._request(
            "POST", "/open/api/quote/latest/batch", payload={"tsCodes": list(ts_codes)}
        )
        out: dict[str, LatestSnapshotDTO] = {}
        for ts_code, it in (data or {}).items():
            if not it:
                continue
            num = it.get("num")
            out[ts_code] = LatestSnapshotDTO(
                ts_code=ts_code,
                snapshot_time=_parse_dt(it.get("tradeTime")) or datetime.now(),
                price=_dec(it.get("price")),
                pre_close=_dec(it.get("preClose")),
                open=_dec(it.get("open")),
                high=_dec(it.get("high")),
                low=_dec(it.get("low")),
                volume_shares=_dec(it.get("vol")),      # latest 口径=股
                turnover_cny=_dec(it.get("amount")),    # latest 口径=元
                trade_count=int(num) if num not in (None, "") else None,
                bid_price_1=_dec(it.get("bidPrice1")),
                bid_volume_1=_dec(it.get("bidVolume1")),
                ask_price_1=_dec(it.get("askPrice1")),
                ask_volume_1=_dec(it.get("askVolume1")),
                source_name="datacenter",
            )
        return out
