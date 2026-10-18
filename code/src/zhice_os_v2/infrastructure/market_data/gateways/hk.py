"""HK 网关（复刻 V1 HKGateway 核心路径）：腾讯 gtimg 快照 + ifzq 日线 → yfinance 兜底。

腾讯免 key（需 UA/Referer，gbk 编码）。HK 分钟走 yfinance 1m 盘中（免 key，逐 code 有限流，
上层限小宇宙）。单位不缩放（股 / HKD）。符号：ts_code=5 位（00700）→ 腾讯 hk00700 / yfinance 0700.HK。
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import requests

from .dto import DailyBarDTO, LatestSnapshotDTO, MinuteBarDTO

_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
_CN_TZ = ZoneInfo("Asia/Shanghai")  # 腾讯 gtimg 时间字段为交易所本地(UTC+8)


def _dec(v: object) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        if isinstance(v, float) and v != v:
            return None
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _parse_tencent_dt(s: object) -> datetime | None:
    """腾讯 gtimg 时间字段 → UTC。支持 'YYYY/MM/DD HH:MM:SS' / 'YYYY-MM-DD HH:MM:SS'
    / 'YYYYMMDDHHMMSS'（交易所本地 UTC+8）。解析失败返 None（调用方回退 now）。"""
    if s is None:
        return None
    txt = str(s).strip()
    if not txt:
        return None
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(txt, fmt).replace(tzinfo=_CN_TZ).astimezone(UTC)
        except (ValueError, TypeError):
            continue
    return None


def _bar_ts_utc(idx: object) -> datetime | None:
    """pandas 索引(bar 时间戳) → UTC datetime。非 datetime/解析失败返 None（回退 now）。"""
    try:
        bt = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(bt, datetime):
        return None
    return bt.astimezone(UTC) if bt.tzinfo is not None else bt.replace(tzinfo=UTC)


def _yf_symbol(ts: str) -> str:
    try:
        return f"{int(ts):04d}.HK"
    except (ValueError, TypeError):
        return f"{ts}.HK"


class HKTencentClient:
    _KLINE = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"

    def snapshot_batch(self, ts_codes: list[str]) -> dict[str, LatestSnapshotDTO]:
        out: dict[str, LatestSnapshotDTO] = {}
        for i in range(0, len(ts_codes), 60):
            chunk = ts_codes[i:i + 60]
            q = ",".join(f"hk{c}" for c in chunk)
            try:
                r = requests.get(f"http://qt.gtimg.cn/q={q}", headers=_HEADERS, timeout=15)
                r.encoding = "gbk"
                text = r.text
            except Exception:  # noqa: BLE001
                continue
            for m in re.finditer(r'v_hk(\w+)="([^"]*)"', text):
                code, payload = m.group(1), m.group(2)
                p = payload.split("~")
                if len(p) <= 37:
                    continue
                price = _dec(p[3])
                if not price or price == 0:
                    continue
                # 新鲜度护栏依赖源 tick 时间：用 payload 自带时间(p[30])，缺失/异常才回退 now
                src_dt = _parse_tencent_dt(p[30]) if len(p) > 30 else None
                out[code] = LatestSnapshotDTO(
                    ts_code=code, snapshot_time=src_dt or datetime.now(UTC), price=price,
                    pre_close=_dec(p[4]), open=_dec(p[5]), high=_dec(p[33]), low=_dec(p[34]),
                    volume_shares=_dec(p[6]), turnover_cny=_dec(p[37]), source_name="tencent_hk",
                )
        return out

    def daily(self, ts: str, limit: int = 60) -> list[DailyBarDTO]:
        today = datetime.now(UTC).date()
        frm = today - timedelta(days=max(limit * 2, 90))
        try:
            r = requests.get(self._KLINE, headers=_HEADERS, timeout=15, params={
                "param": f"hk{ts},day,{frm.isoformat()},{today.isoformat()},{limit}"})
            r.raise_for_status()
            data = (r.json() or {}).get("data") or {}
            node = data.get(f"hk{ts}") or {}
            rows = node.get("day") or node.get("qfqday") or []
        except Exception:  # noqa: BLE001
            return []
        out = []
        for row in rows:
            if len(row) < 6:
                continue
            try:
                td = datetime.strptime(str(row[0]), "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            close = _dec(row[2])
            vol = _dec(row[5])
            _t8 = _dec(row[8]) if len(row) > 8 else None
            turnover = _t8 * Decimal("10000") if _t8 is not None else (
                close * vol if (close and vol) else None)
            out.append(DailyBarDTO(
                ts_code=ts, trade_date=td, open=_dec(row[1]), high=_dec(row[3]), low=_dec(row[4]),
                close=close, volume_shares=vol, turnover_cny=turnover, adj_type="none",
                source_name="tencent_hk",
            ))
        return out[-limit:]


def _yf_daily(ts: str, limit: int = 60) -> list[DailyBarDTO]:
    try:
        import yfinance as yf
        today = datetime.now(UTC).date()
        frm = today - timedelta(days=max(limit * 2, 90))
        df = yf.Ticker(_yf_symbol(ts)).history(
            start=frm.isoformat(), end=(today + timedelta(days=1)).isoformat(), auto_adjust=False)
    except Exception:  # noqa: BLE001
        return []
    out: list[DailyBarDTO] = []
    if df is None or df.empty:
        return out
    for idx, row in df.iterrows():
        td = idx.date() if hasattr(idx, "date") else None
        if td is None:
            continue
        out.append(DailyBarDTO(
            ts_code=ts, trade_date=td, open=_dec(row.get("Open")), high=_dec(row.get("High")),
            low=_dec(row.get("Low")), close=_dec(row.get("Close")),
            volume_shares=_dec(row.get("Volume")), adj_type="none", source_name="yfinance",
        ))
    return out[-limit:]


def _yf_snapshot(ts: str) -> LatestSnapshotDTO | None:
    try:
        import yfinance as yf
        df = yf.Ticker(_yf_symbol(ts)).history(period="1d", auto_adjust=False)
    except Exception:  # noqa: BLE001
        return None
    if df is None or df.empty:
        return None
    row = df.iloc[-1]
    price = _dec(row.get("Close"))
    if not price or price == 0:
        return None
    # 新鲜度护栏依赖源 tick 时间：用 bar 时间戳 df.index[-1]，非抓取时刻 now
    src_dt = _bar_ts_utc(df.index[-1])
    return LatestSnapshotDTO(
        ts_code=ts, snapshot_time=src_dt or datetime.now(UTC), price=price,
        open=_dec(row.get("Open")), high=_dec(row.get("High")), low=_dec(row.get("Low")),
        volume_shares=_dec(row.get("Volume")), source_name="yfinance",
    )


def _yf_minute(ts: str, trade_date: str) -> list[MinuteBarDTO]:
    """yfinance 1m 盘中分钟线（period=1d=最近一个交易日）。

    trade_date 作**宽松**过滤：若返回 bar 里存在 target 当日（交易所本地日）则只取当日；
    否则全取。这样避免宿主时区≠交易所时区时把当日实时分钟误丢。
    trade_date 一律用 bar 自身交易所本地日，bar_time 归一到 UTC。
    """
    try:
        import yfinance as yf
        df = yf.Ticker(_yf_symbol(ts)).history(period="1d", interval="1m", auto_adjust=False)
    except Exception:  # noqa: BLE001
        return []
    if df is None or df.empty:
        return []
    try:
        target = date.fromisoformat(trade_date)
    except (ValueError, TypeError):
        target = None
    parsed: list[MinuteBarDTO] = []
    for idx, row in df.iterrows():
        bar_date = idx.date() if hasattr(idx, "date") else None
        if bar_date is None:
            continue
        bt = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
        bt = bt.astimezone(UTC) if bt.tzinfo is not None else bt.replace(tzinfo=UTC)
        close = _dec(row.get("Close"))
        if not close or close == 0:
            continue
        parsed.append(MinuteBarDTO(
            ts_code=ts, bar_time=bt, trade_date=bar_date, open=_dec(row.get("Open")),
            high=_dec(row.get("High")), low=_dec(row.get("Low")), close=close,
            volume_shares=_dec(row.get("Volume")), source_name="yfinance",
        ))
    if target is not None:
        same_day = [b for b in parsed if b.trade_date == target]
        if same_day:
            return same_day
    return parsed


class HKGateway:
    """HK 网关：东方财富为主源（批量枚举/日线/快照，支持 start/end 回填），
    腾讯 gtimg/ifzq → yfinance 逐个兜底。分钟走 yfinance（小宇宙）。"""

    def __init__(self) -> None:
        from .eastmoney import EastMoneyHKClient
        # 东财 retry_max=1 快速失败（本机对东财间歇封，腾讯才是可靠主源）；枚举另建默认重试客户端
        self._em = EastMoneyHKClient(retry_max=1)
        self._tencent = HKTencentClient()

    def close(self) -> None:
        self._em.close()

    def get_daily_bars_batch(
        self, ts_codes: list[str], limit: int = 60, start: str | None = None, end: str | None = None
    ) -> dict[str, list[DailyBarDTO]]:
        # 腾讯为主源（本机可靠）；东财对本机间歇 IP 封，仅腾讯空时兜底；yfinance 最终兜底。
        out: dict[str, list[DailyBarDTO]] = {}
        for ts in ts_codes:
            bars = (
                self._tencent.daily(ts, limit=limit)
                or self._em.daily(ts, limit=limit, start=start, end=end)
                or _yf_daily(ts, limit=limit)
            )
            if bars:
                out[ts] = bars
        return out

    def get_latest_snapshot_batch(self, ts_codes: list[str]) -> dict[str, LatestSnapshotDTO]:
        out = self._tencent.snapshot_batch(ts_codes)  # 腾讯批量主源（本机可靠）
        missing = [t for t in ts_codes if t not in out]
        if missing:
            out.update(self._em.snapshot_batch(missing))  # 东财兜底（间歇可用）
        for ts in ts_codes:  # yfinance 逐个补最后缺口
            if ts not in out:
                snap = _yf_snapshot(ts)
                if snap is not None:
                    out[ts] = snap
        return out

    def get_minute_bars(self, ts_code: str, trade_date: str) -> list[MinuteBarDTO]:
        return self._em.minute(ts_code, trade_date) or _yf_minute(ts_code, trade_date)
