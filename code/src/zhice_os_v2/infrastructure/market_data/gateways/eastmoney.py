"""东方财富网关（HK 统一主源）：全市场枚举 + 批量日线 + 批量快照，免 key。

- 枚举：push2 clist `fs=m:116,m:117`（港股主板+创业板）分页，过滤到正股（剔除窝轮/牛熊/ETF）。
- 日线：push2his kline `secid=116.{code}`，klt=101 不复权；单位真实 HKD/股，不缩放。
- 快照：clist 全量单次扫描 → 按 code 映射（fltt=2 直接返回小数价，规避 f2/f152 缩放）。
符号：HK ts_code=5 位（00700）；secid 前缀 116。东财对高频/密连有 IP 节流 → 带 session+重试退避+请求间隔；
上层（HKGateway）在东财失败时回落腾讯/yfinance。
"""
from __future__ import annotations

import time
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import requests

from .dto import DailyBarDTO, LatestSnapshotDTO, MinuteBarDTO

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://quote.eastmoney.com/",
}

# 港股正股：代码区间 1..9999（窝轮/牛熊/结构性产品多在 10000+）；名称含衍生品关键字排除。
_DERIV_KEYWORDS = ("购", "沽", "牛", "熊", "RC", "RP")


def _dec(v: object) -> Decimal | None:
    if v is None or v == "" or v == "-":
        return None
    try:
        if isinstance(v, float) and v != v:  # NaN
            return None
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _epoch_s_utc(v: object) -> datetime | None:
    """秒 epoch(东财 clist f124 更新时间) → UTC datetime。缺失/异常返 None（回退 now）。"""
    if v is None or v == "" or v == "-":
        return None
    try:
        ts = int(str(v))
    except (ValueError, TypeError):
        return None
    if ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=UTC)
    except (ValueError, OSError, OverflowError):
        return None


def _hk5(ts: str) -> str:
    """HK 代码零填充到 5 位（00700）。"""
    try:
        return f"{int(ts):05d}"
    except (ValueError, TypeError):
        return str(ts)


def _ymd(s: str | None) -> str | None:
    """ISO(2026-08-21) / YYYYMMDD → YYYYMMDD。"""
    if not s:
        return None
    s = str(s).replace("-", "")
    return s if len(s) == 8 and s.isdigit() else None


_MINUTE_FIELDS = "f51,f52,f53,f54,f55,f56,f57"  # 时间/开/收/高/低/量/额（同日线）


def _parse_minute_klines(
    klines: list[str], ts: str, trade_date: str, tz_name: str, source: str = "eastmoney"
) -> list[MinuteBarDTO]:
    """解析 push2his 1 分钟 klines（"YYYY-MM-DD HH:MM,开,收,高,低,量,额"）。

    时间为交易所本地（tz_name）→ 归一 UTC。trade_date 宽松过滤：有当日则取当日，
    否则全取（避免宿主时区≠交易所时区把当日分钟误丢，与 yfinance 路径一致）。
    """
    tz = ZoneInfo(tz_name)
    try:
        target = date.fromisoformat(trade_date)
    except (ValueError, TypeError):
        target = None
    parsed: list[MinuteBarDTO] = []
    for row in klines:
        p = row.split(",")
        if len(p) < 7:
            continue
        try:
            bt_local = datetime.strptime(p[0], "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        except (ValueError, TypeError):
            continue
        close = _dec(p[2])
        if not close or close == 0:
            continue
        parsed.append(MinuteBarDTO(
            ts_code=ts, bar_time=bt_local.astimezone(UTC), trade_date=bt_local.date(),
            open=_dec(p[1]), high=_dec(p[3]), low=_dec(p[4]), close=close,
            volume_shares=_dec(p[5]), turnover_cny=_dec(p[6]), source_name=source,
        ))
    if target is not None:
        same = [b for b in parsed if b.trade_date == target]
        if same:
            return same
    return parsed


class EastMoneyHKClient:
    _CLIST = "https://push2.eastmoney.com/api/qt/clist/get"
    _KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    _HK_FS = "m:116,m:117"  # 港股主板 + 创业板

    def __init__(self, req_interval: float = 0.12, retry_max: int = 4) -> None:
        self._s = requests.Session()
        self._s.headers.update(_HEADERS)
        self._interval = req_interval
        self._retry_max = retry_max

    def close(self) -> None:
        try:
            self._s.close()
        except Exception:  # noqa: BLE001
            pass

    def _get(self, url: str, params: dict) -> dict | None:
        """带重试退避的 GET → JSON；全失败返回 None（调用方回落）。"""
        for i in range(self._retry_max):
            try:
                r = self._s.get(url, params=params, timeout=20)
                r.raise_for_status()
                return r.json()
            except Exception:  # noqa: BLE001 - 网络/节流不中断，退避重试
                time.sleep(self._interval * (i + 1) * 2)
        return None

    # ---- 枚举全量正股 ----
    def enumerate_symbols(self) -> list[dict]:
        """分页拉全量港股正股。返回 [{'code'(5位),'name'}]。

        对间歇性节流健壮：某页拉取失败（_get 返回 None）不当作"列表结束"，
        页级重试至多 page_retry 次；按 total 驱动终止；持续失败则返回已得（部分）。
        """
        out: list[dict] = []
        seen: set[str] = set()
        pz = 200
        pn = 1
        total: int | None = None
        page_retry = 0
        while True:
            j = self._get(self._CLIST, {
                "pn": pn, "pz": pz, "fs": self._HK_FS, "po": 1, "np": 1,
                "fltt": 2, "invt": 2, "fields": "f12,f14",
            })
            data = (j or {}).get("data") or {}
            if total is None and data.get("total"):
                total = data.get("total")
            rows = data.get("diff") or []
            if not rows:
                if j is None and page_retry < 5:  # 拉取失败（非真空页）→ 退避重试同页
                    page_retry += 1
                    time.sleep(self._interval * page_retry * 3)
                    continue
                break  # 真空页（到底）或重试耗尽
            page_retry = 0
            for x in rows:
                code = str(x.get("f12") or "").strip()
                name = str(x.get("f14") or "").strip()
                if not code or not code.isdigit():
                    continue
                if not (1 <= int(code) <= 9999):  # 正股主区间；衍生品在 10000+
                    continue
                if any(k in name for k in _DERIV_KEYWORDS):
                    continue
                c5 = _hk5(code)
                if c5 in seen:
                    continue
                seen.add(c5)
                out.append({"code": c5, "name": name})
            pn += 1
            if total and (pn - 1) * pz >= total:
                break
            time.sleep(self._interval)
        return out

    # ---- 日线 ----
    def daily(self, ts: str, limit: int = 60,
              start: str | None = None, end: str | None = None) -> list[DailyBarDTO]:
        """push2his 日 K（不复权）。给 start/end（ISO 或 YYYYMMDD）则按日期范围拉，否则近 limit 根。"""
        code = _hk5(ts)
        params: dict = {
            "secid": f"116.{code}", "klt": 101, "fqt": 0,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57",
            "beg": _ymd(start) or "19900101",
            "end": _ymd(end) or "20500101",
        }
        if not start and not end:
            params["lmt"] = limit
        j = self._get(self._KLINE, params)
        klines = (((j or {}).get("data") or {}).get("klines")) or []
        out: list[DailyBarDTO] = []
        for row in klines:
            p = row.split(",")
            if len(p) < 7:
                continue
            try:
                td = date.fromisoformat(p[0])
            except (ValueError, TypeError):
                continue
            out.append(DailyBarDTO(
                ts_code=code, trade_date=td, open=_dec(p[1]), close=_dec(p[2]),
                high=_dec(p[3]), low=_dec(p[4]), volume_shares=_dec(p[5]),
                turnover_cny=_dec(p[6]), adj_type="none", source_name="eastmoney",
            ))
        out.sort(key=lambda b: b.trade_date)
        return out[-limit:] if (not start and not end) else out

    # ---- 分钟线（1m，交易所本地 → UTC） ----
    def minute(self, ts: str, trade_date: str) -> list[MinuteBarDTO]:
        code = _hk5(ts)
        j = self._get(self._KLINE, {
            "secid": f"116.{code}", "klt": 1, "fqt": 0,
            "fields1": "f1", "fields2": _MINUTE_FIELDS, "end": "20500101", "lmt": 1000,
        })
        klines = (((j or {}).get("data") or {}).get("klines")) or []
        return _parse_minute_klines(klines, code, trade_date, "Asia/Hong_Kong")

    # ---- 快照（全量单次扫描 → 映射） ----
    def snapshot_batch(self, ts_codes: list[str]) -> dict[str, LatestSnapshotDTO]:
        if not ts_codes:
            return {}
        want = {_hk5(t) for t in ts_codes}
        by_code: dict[str, dict] = {}
        pn = 1
        pz = 500
        total = None
        while True:
            j = self._get(self._CLIST, {
                "pn": pn, "pz": pz, "fs": self._HK_FS, "po": 1, "np": 1,
                "fltt": 2, "invt": 2,
                # code,现价,高,低,开,昨收,量,额,更新时间(f124 秒 epoch)
                "fields": "f12,f2,f15,f16,f17,f18,f5,f6,f124",
            })
            data = (j or {}).get("data") or {}
            if total is None:
                total = data.get("total") or 0
            rows = data.get("diff") or []
            if not rows:
                break
            for x in rows:
                by_code[_hk5(str(x.get("f12") or ""))] = x
            pn += 1
            if pn * pz > (total or 0):
                break
            time.sleep(self._interval)
        now = datetime.now(UTC)
        out: dict[str, LatestSnapshotDTO] = {}
        for code in want:
            x = by_code.get(code)
            if not x:
                continue
            price = _dec(x.get("f2"))
            if not price or price == 0:
                continue
            # 新鲜度护栏依赖源 tick 时间：用 clist 更新时间 f124，缺失/异常才回退 now
            src_dt = _epoch_s_utc(x.get("f124"))
            out[code] = LatestSnapshotDTO(
                ts_code=code, snapshot_time=src_dt or now, price=price,
                pre_close=_dec(x.get("f18")), open=_dec(x.get("f17")),
                high=_dec(x.get("f15")), low=_dec(x.get("f16")),
                volume_shares=_dec(x.get("f5")), turnover_cny=_dec(x.get("f6")),
                source_name="eastmoney",
            )
        return out


# US 交易所 → 东财 secid 前缀
_US_PREFIX = {"NASDAQ": "105", "NYSE": "106", "AMEX": "107"}


class EastMoneyUSClient:
    """东财美股分钟线：secid 前缀按交易所（NASDAQ=105/NYSE=106/AMEX=107）。

    仅补 US 分钟（US 日线仍走 Nasdaq，US 快照走 Finnhub/yfinance）。免 key，带重试退避。
    """
    _KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

    def __init__(self, req_interval: float = 0.12, retry_max: int = 2) -> None:
        self._s = requests.Session()
        self._s.headers.update(_HEADERS)
        self._interval = req_interval
        self._retry_max = retry_max

    def close(self) -> None:
        try:
            self._s.close()
        except Exception:  # noqa: BLE001
            pass

    def _get(self, params: dict) -> dict | None:
        for i in range(self._retry_max):
            try:
                r = self._s.get(self._KLINE, params=params, timeout=20)
                r.raise_for_status()
                return r.json()
            except Exception:  # noqa: BLE001
                time.sleep(self._interval * (i + 1) * 2)
        return None

    def minute(self, ts: str, exchange: str, trade_date: str) -> list[MinuteBarDTO]:
        prefix = _US_PREFIX.get((exchange or "").upper(), "105")
        j = self._get({
            "secid": f"{prefix}.{ts}", "klt": 1, "fqt": 0,
            "fields1": "f1", "fields2": _MINUTE_FIELDS, "end": "20500101", "lmt": 1000,
        })
        klines = (((j or {}).get("data") or {}).get("klines")) or []
        return _parse_minute_klines(klines, ts, trade_date, "America/New_York")
