# -*- coding: utf-8 -*-
"""日内 5 分钟价格回溯接口 —— `/api/jev/intraday/{code}`（2026-11-07）。

老田口径：当天大量新闻在盘中有实时价格反应；不做实时处理，但可以
**5 分钟粒度回溯**——新闻发出后，盘面 5/15/30/60 分钟价格怎么走。

    GET /api/jev/intraday/300750            # 最新可用交易日
    GET /api/jev/intraday/300750?date=20261106

数据源（全只读）：
    quant_backtest/datamart · stk_mins 1 分钟线（聚合为 5 分钟棒）
    news.db 当日该股方向性判定（jev companies/stocks 匹配）

输出四块：
    bars       当日 5 分钟棒：时间 / 收盘 / 累计涨跌%（vs 昨收） / 量
    events     当日方向性事件（时间正序，含标题/方向/conf/信源）
    reactions  每事件的价格反应：T+5/15/30/60 分钟累计变化
               （基准=事件时刻所在 5 分钟棒收盘；方向归一同 story 口径）
    verdict    日内反应总结（多少事件、多少同向、最大即时反应）

只读纪律 + 60s 缓存；无分钟数据或非交易日 → 如实报 unavailable。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import sys
import time
from urllib.parse import parse_qs

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEWS_DB = os.path.join(STATION, "news_collector", "news.db")

CACHE_TTL_S = 60
_CACHE: dict = {"key": None, "at": 0.0, "data": None}
_CODE_RE = re.compile(r"^(\d{6})(?:\.(SH|SZ|BJ))?$")


def _ro(path):
    return sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"),
                            uri=True, timeout=10)


def _load_day_bars(ts_code: str, d8: str):
    """stk_mins 1 分钟 → 当日 5 分钟棒列表（按 trade_time 升序）。"""
    sys.path.insert(0, os.path.join(STATION, "quant_backtest"))
    import datamart as dm
    d = dm.read("stk_mins", codes=[ts_code], limit=2_000_000)
    if d is None or not len(d):
        return None
    tt = d["trade_time"].astype(str)
    day = tt.str.startswith(d8[:4] + "-" + d8[4:6] + "-" + d8[6:])
    d = d[day]
    if not len(d):
        return None
    d = d.assign(_t=tt[day]).sort_values("_t")
    times = list(d["_t"].astype(str))
    closes = list(d["close"].astype(float))
    vols = [float(v) if v == v else 0.0 for v in d["vol"]]
    # 昨收：取当日首棒 open 的前一根不可得 → 用 day open 近似基准，
    # 更稳：取前一日日线 close
    prev_close = None
    try:
        dd = dm.read("daily", codes=[ts_code], limit=400)
        if dd is not None and len(dd):
            dd = dd.sort_values("trade_date")
            days = [str(int(x)) for x in dd["trade_date"]]
            if d8 in days:
                i = days.index(d8)
                if i > 0:
                    prev_close = float(dd.iloc[i - 1]["close"])
    except Exception:  # noqa: BLE001
        pass
    base_px = prev_close or float(closes[0])

    bars = []
    bucket = {}
    for tstr, c, v in zip(times, closes, vols):
        hhmm = tstr[11:16]
        if hhmm < "09:30" or hhmm > "15:00":
            continue
        h, m = int(hhmm[:2]), int(hhmm[3:])
        m5 = (m // 5) * 5
        key = "%02d:%02d" % (h, m5)
        b = bucket.setdefault(key, {"close": None, "vol": 0.0})
        b["close"] = c  # 桶内最后一条覆盖=该棒收盘
        b["vol"] += v
    for key in sorted(bucket):
        b = bucket[key]
        c = b["close"]
        if c is None:
            continue
        bars.append({
            "t": key, "close": round(c, 2),
            "cum_pct": round((c / base_px - 1) * 100, 2),
            "vol": round(b["vol"], 0),
        })
    return bars, base_px, prev_close is not None


def _load_day_events(code6: str, d8: str):
    """当日该股方向性判定事件（时间正序）。"""
    db = _ro(NEWS_DB)
    try:
        rows = db.execute(
            "SELECT ts, source, title, jev FROM news "
            "WHERE jev IS NOT NULL AND jev!='' AND ts LIKE ? "
            "AND (jev LIKE ? OR stocks LIKE ?) ORDER BY ts ASC",
            (d8[:4] + "-" + d8[4:6] + "-" + d8[6:] + "%",
             '%"companies": ["%' + code6 + '%', '%' + code6 + '%')).fetchall()
    finally:
        db.close()
    out = []
    for ts, source, title, jev in rows:
        try:
            j = json.loads(jev)
        except Exception:  # noqa: BLE001
            continue
        if j.get("bias") not in ("bull", "bear"):
            continue
        comps = [str(c).strip() for c in (j.get("companies") or [])]
        stocks = [str(s).strip() for s in (j.get("stocks") or [])]
        if code6 not in comps and code6 not in stocks:
            continue
        out.append({
            "ts": ts, "hhmm": (ts or "")[11:16], "source": source,
            "title": (title or "")[:60], "bias": j.get("bias"),
            "conf": j.get("conf"),
        })
    return out


def compute(code_raw: str, date: str) -> dict:
    key = (code_raw, date)
    if _CACHE["key"] == key and time.time() - _CACHE["at"] < CACHE_TTL_S:
        return _CACHE["data"]

    m = _CODE_RE.match((code_raw or "").strip().upper())
    if not m:
        raise ValueError("code 须为 6 位数字")
    code6, suf = m.group(1), m.group(2)
    if not suf:
        suf = "SH" if code6.startswith(("6", "9", "5")) else (
            "BJ" if code6.startswith(("4", "8")) else "SZ")
    ts_code = "%s.%s" % (code6, suf)

    loaded = _load_day_bars(ts_code, date)
    if not loaded:
        data = {
            "ok": True, "schema": "jev_intraday.v1",
            "available": False,
            "reason": "该日无分钟线数据（非交易日或未入库）",
            "code": code6, "ts_code": ts_code, "date": date,
            "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _CACHE.update(key=key, at=time.time(), data=data)
        return data
    bars, base_px, has_prev = loaded
    events = _load_day_events(code6, date)

    # ── 事件后价格反应：基准=事件时刻所在（或之前最近）5 分钟棒收盘 ──
    def _bar_index_at(hhmm: str):
        if not hhmm or hhmm < "09:30":
            return 0  # 盘前事件 → 首棒
        idx = None
        for i, b in enumerate(bars):
            if b["t"] > hhmm:
                break
            idx = i
        return idx if idx is not None else 0

    reactions = []
    for e in events:
        i0 = _bar_index_at(e["hhmm"])
        if i0 is None:
            continue
        c0 = bars[i0]["close"]
        if not c0:
            continue
        rx = {"ts": e["ts"], "bias": e["bias"], "hhmm": e["hhmm"],
              "base_bar": bars[i0]["t"], "chg": {}}
        for wname, wn in (("m5", 1), ("m15", 3), ("m30", 6), ("m60", 12)):
            i1 = min(len(bars) - 1, i0 + wn)
            chg = round((bars[i1]["close"] / c0 - 1) * 100, 2)
            if e["bias"] == "bear":
                chg = round(-chg, 2)
            rx["chg"][wname] = chg
        reactions.append(rx)

    # ── verdict ──
    v = []
    v.append("当日 %s 5 分钟线 %d 棒（基准=%s）" % (
        date, len(bars), "昨收" if has_prev else "当日开盘"))
    if events:
        v.append("方向性事件 %d 个" % len(events))
    else:
        v.append("当日无带方向判定事件（消息面平静）")
    if reactions:
        m5 = [r["chg"]["m5"] for r in reactions]
        same = sum(1 for x in m5 if x > 0)
        v.append("事件后 5 分钟同向 %d/%d，最大即时反应 %s%.2f%%" % (
            same, len(m5), "+" if max(m5) >= 0 else "-", abs(max(m5, key=abs))))
    data = {
        "ok": True, "schema": "jev_intraday.v1", "available": True,
        "code": code6, "ts_code": ts_code, "date": date,
        "base": {"price": base_px, "kind": "prev_close" if has_prev else "open"},
        "bars": bars, "events": events, "reactions": reactions,
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "verdict": "；".join(v) + "。",
    }
    _CACHE.update(key=key, at=time.time(), data=data)
    return data


def handle_get(handler, path: str, qs_raw: str) -> None:
    m = re.match(r"^/api/jev/intraday/([^/?]+)$", path)
    if not m:
        handler._json({"ok": False, "error": "invalid_params",
                       "message": "路径须为 /api/jev/intraday/{code}"}, 400)
        return
    qs = parse_qs(qs_raw or "")
    try:
        date = (qs.get("date") or [""])[-1].strip()
        if not date:
            # 缺省=stk_mins 最新交易日（查一次）
            date = _latest_trade_day(m.group(1))
        if not re.match(r"^\d{8}$", date):
            raise ValueError("date 须为 YYYYMMDD")
        result = compute(m.group(1), date)
    except ValueError as exc:
        handler._json({"ok": False, "error": "invalid_params",
                       "message": str(exc)}, 400)
        return
    except Exception as exc:  # noqa: BLE001
        handler._json({"ok": False, "error": "internal",
                       "message": "%s: %s" % (type(exc).__name__, exc)}, 500)
        return
    handler._json(result)


def _latest_trade_day(code_raw: str) -> str:
    m = _CODE_RE.match((code_raw or "").strip().upper())
    if not m:
        return dt.date.today().strftime("%Y%m%d")
    code6, suf = m.group(1), m.group(2)
    if not suf:
        suf = "SH" if code6.startswith(("6", "9", "5")) else (
            "BJ" if code6.startswith(("4", "8")) else "SZ")
    try:
        sys.path.insert(0, os.path.join(STATION, "quant_backtest"))
        import datamart as dm
        d = dm.read("stk_mins", codes=["%s.%s" % (code6, suf)], limit=2_000_000)
        if d is not None and len(d):
            tt = d["trade_time"].astype(str)
            return tt.max()[:10].replace("-", "")
    except Exception:  # noqa: BLE001
        pass
    return dt.date.today().strftime("%Y%m%d")
