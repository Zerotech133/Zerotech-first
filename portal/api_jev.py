# -*- coding: utf-8 -*-
"""JEV 判定只读查询接口 —— `/api/jev/query`（2026-10-26 JEVAPI）。

用途：门户 / Hermes MCP 工具按需读取 JEV 已判新闻，回答「今天 JEV 抓到哪些
利空、哪些有后验命中」这类问题。

    GET /api/jev/query?hours=24
        &bias=bull,bear            # 可选，逗号分隔：bull/bear/neutral
        &code=600519               # 可选，关联 A 股（6 位，模糊匹配 stocks/companies）
        &event_type=policy         # 可选，逗号分隔，匹配 jev.event_type
        &with_posterior=1          # 可选，联查 jev_audit 后验（T+1 ret/bench/excess/hit）
        &limit=50                  # 可选，1..200，默认 50

只读纪律：news.db / jev_audit.db 一律 `mode=ro`，不写任何库、不触发回填。

返回：
    {"ok": true, "generated_at": ..., "window_hours": 24,
     "summary_window": {"total": n, "bull": x, "bear": y, "neutral": z},
     "total": 匹配总数, "returned": 返回条数, "items": [...],
     "posterior_band_pct": 2.5 | null}
"""
from __future__ import annotations

import datetime as dt
import json
import re
import os
import sqlite3
from urllib.parse import parse_qs
import logging

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEWS_DB = os.path.join(STATION, "news_collector", "news.db")
AUDIT_DB = os.path.join(STATION, "agent_platform", "data", "jev_audit", "jev_audit.db")
LATEST_CALIB = os.path.join(STATION, "agent_platform", "data", "jev_audit", "latest.json")

MAX_LIMIT = 200
DEFAULT_LIMIT = 50
MIN_HOURS = 1
MAX_HOURS = 24 * 30
BAND_DEFAULT = 2.5
VALID_BIAS = ("bull", "bear", "neutral")


def _ro_connect(path: str) -> sqlite3.Connection:
    return sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"),
                           uri=True, timeout=10)


def _norm_code(raw) -> str:
    """宽松提取 6 位代码（用于从 stocks/companies 里取值；非法返回空串）。"""
    s = "".join(ch for ch in str(raw or "") if ch.isdigit())
    return s[:6] if len(s) >= 6 else ""


def _code_filter(raw) -> str:
    """查询过滤用：6 位 A 股代码，可选 .SH/.SZ/.BJ 后缀，否则 400。"""
    m = re.match(r"^(\d{6})(?:\.(SH|SZ|BJ))?$", str(raw or "").strip().upper())
    if not m:
        raise ValueError("code 非法：需 6 位 A 股代码（可带 .SH/.SZ/.BJ 后缀）: %r" % (raw,))
    return m.group(1)


_META_CACHE: dict = {"mtime": None, "band": BAND_DEFAULT, "low_signal": frozenset()}


def _latest_meta() -> tuple[float, frozenset[str]]:
    """读 latest.json 的 neutral 带宽与低信号事件类型（60s mtime 缓存）。"""
    try:
        st = os.stat(LATEST_CALIB)
    except OSError:
        return BAND_DEFAULT, frozenset()
    if _META_CACHE["mtime"] == st.st_mtime:
        return _META_CACHE["band"], _META_CACHE["low_signal"]
    band, low = BAND_DEFAULT, frozenset()
    try:
        with open(LATEST_CALIB, encoding="utf-8") as fh:
            data = json.load(fh)
        a = data.get("assessment") or {}
        band = ((a.get("neutral") or {}).get("band_pct")) or BAND_DEFAULT
        low = frozenset(a.get("low_signal_event_types") or [])
    except (OSError, ValueError):
        pass
    _META_CACHE.update(mtime=st.st_mtime, band=band, low_signal=low)
    return band, low


def _jev_like(field: str, value: str) -> tuple[str, list]:
    """JSON 文本 LIKE 片段：兼容 json.dumps 默认分隔符与紧凑分隔符两种历史写法。"""
    return ("(jev LIKE ? OR jev LIKE ?)",
            ['%%"%s": "%s"%%' % (field, value), '%%"%s":"%s"%%' % (field, value)])


def _as_int(qs: dict, key: str, default: int, lo: int, hi: int) -> int:
    raw = (qs.get(key) or [""])[-1].strip()
    if not raw:
        return default
    try:
        val = int(raw)
    except ValueError:
        raise ValueError("参数 %s 必须是整数: %r" % (key, raw))
    if val < lo or val > hi:
        raise ValueError("参数 %s 超出范围 [%d, %d]: %d" % (key, lo, hi, val))
    return val


def _as_bool(qs: dict, key: str) -> bool:
    raw = (qs.get(key) or [""])[-1].strip().lower()
    return raw in ("1", "true", "yes", "on")


def _as_list(qs: dict, key: str) -> list[str]:
    raw = (qs.get(key) or [""])[-1].strip()
    return [x.strip() for x in raw.split(",") if x.strip()] if raw else []


def _clean_title(title: str) -> str:
    return " ".join(str(title or "").split())[:300]


def _load_posterior(news_ids: list[str], band: float) -> dict[str, dict]:
    if not news_ids or not os.path.exists(AUDIT_DB):
        return {}
    out: dict[str, dict] = {}
    try:
        db = _ro_connect(AUDIT_DB)
    except sqlite3.Error:
        return {}
    try:
        for i in range(0, len(news_ids), 400):
            chunk = news_ids[i:i + 400]
            marks = ",".join("?" for _ in chunk)
            rows = db.execute(
                "SELECT news_id, trade_day, ret, bench, excess, hit FROM dec_audit"  # nosec B608 - 动态标识符来自内部白名单/值已参数化（已抽查）
                " WHERE news_id IN (%s)" % marks, chunk).fetchall()
            for nid, trade_day, ret, bench, excess, hit in rows:
                if trade_day is None:
                    continue
                item = {"trade_day": trade_day, "ret": ret, "bench": bench,
                        "excess": excess,
                        "hit": None if hit is None else bool(hit)}
                if excess is not None:
                    item["highvol"] = abs(excess) > band
                out[str(nid)] = item
    finally:
        db.close()
    return out


def query(hours: int = 24, bias: list[str] | None = None, code: str = "",
          event_type: list[str] | None = None, with_posterior: bool = False,
          limit: int = DEFAULT_LIMIT, band: float | None = None,
          now: dt.datetime | None = None) -> dict:
    """核心查询（纯读；可被单测直接调用，路径由模块常量决定可 monkeypatch）。

    2026-10-30 BATCH1 修复：旧实现 `LIMIT 20000` 后 `total=len(matched)`：
    - total 被 limit 截断（hours=168&limit=1 → total=1，而真实 32,087）；
    - summary_window 只统计前 2 万行（30 天窗口 93,811 行 → 报 20,000）。
    现改为：计数走 SQL（bias/event 用 JSON LIKE 双分隔符兼容），items 单独 LIMIT。
    """
    if hours < MIN_HOURS or hours > MAX_HOURS:
        raise ValueError("hours 超出范围 [%d, %d]: %d" % (MIN_HOURS, MAX_HOURS, hours))
    if limit < 1:
        raise ValueError("limit 必须 >= 1")
    limit = min(limit, MAX_LIMIT)
    bias = [b for b in (bias or []) if b]
    for b in bias:
        if b not in VALID_BIAS:
            raise ValueError("bias 非法：%s（可选 %s）" % (b, "/".join(VALID_BIAS)))
    if not os.path.exists(NEWS_DB):
        raise RuntimeError("news.db 不存在：%s" % NEWS_DB)

    now = now or dt.datetime.now()
    since = (now - dt.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    code6 = _code_filter(code) if code else ""
    latest_band, low_signal = _latest_meta()
    band = latest_band if band is None else band

    # 2026-10-26 JEVAPI2：窗口/排序用「采集时间 created_at」——新闻 ts 常是
    # 发布日（如次日 00:00 的公告），按 ts 会漏掉今天抓到的条目或混入未来项。
    base_where = ["jev IS NOT NULL", "jev<>''", "created_at IS NOT NULL", "created_at>=?"]
    base_params: list = [since]
    if code6:
        base_where.append("(stocks LIKE ? OR jev LIKE ?)")
        base_params += ["%" + code6 + "%", "%" + code6 + "%"]

    items_where = list(base_where)
    items_params = list(base_params)
    for b in bias:
        frag, frag_params = _jev_like("bias", b)
        items_where.append(frag)
        items_params += frag_params
    for ev in event_type or []:
        frag, frag_params = _jev_like("event_type", ev)
        items_where.append(frag)
        items_params += frag_params

    db = _ro_connect(NEWS_DB)
    try:
        total = int(db.execute(
            "SELECT count(*) FROM news WHERE " + " AND ".join(items_where),  # nosec B608
            items_params).fetchone()[0] or 0)
        rows = db.execute(
            "SELECT id, ts, created_at, source, title, stocks, jev FROM news"  # nosec B608
            " WHERE " + " AND ".join(items_where)
            + " ORDER BY created_at DESC LIMIT ?",
            items_params + [limit]).fetchall()
        window_counts = {"total": 0, "bull": 0, "bear": 0, "neutral": 0}
        for b in VALID_BIAS:
            frag, frag_params = _jev_like("bias", b)
            cnt = int(db.execute(
                "SELECT count(*) FROM news WHERE " + " AND ".join(base_where + [frag]),  # nosec B608
                base_params + frag_params).fetchone()[0] or 0)
            window_counts[b] = cnt
            window_counts["total"] += cnt
    finally:
        db.close()

    matched: list[dict] = []
    for nid, ts, created_at, source, title, stocks, jev in rows:
        try:
            j = json.loads(jev or "{}")
        except ValueError:
            continue
        if not isinstance(j, dict):
            continue
        b = j.get("bias")
        if b not in VALID_BIAS:
            continue
        ev = j.get("event_type") or "unknown"
        companies = [c for c in (j.get("companies") or []) if c]
        stock_codes = []
        try:
            for c in (json.loads(stocks or "[]") or []):
                n = _norm_code(c)
                if n:
                    stock_codes.append(n)
        except ValueError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_jev.py", exc)
        for c in companies:
            n = _norm_code(c)
            if n:
                stock_codes.append(n)
        stock_codes = sorted(set(stock_codes))
        if code6 and code6 not in stock_codes:
            continue
        matched.append({
            "id": str(nid), "ts": str(ts), "collected_at": str(created_at or ""),
            "source": str(source or ""),
            "title": _clean_title(title),
            "bias": b, "bias_cn": j.get("bias_cn") or b,
            "conf": j.get("conf"),
            "scope": j.get("scope") or "",
            "event_type": ev, "event_cn": j.get("event_cn") or "",
            "low_signal": ev in low_signal,
            "impact": j.get("impact") or "", "impact_cn": j.get("impact_cn") or "",
            "companies": companies, "codes": stock_codes,
            "reason": (j.get("reason") or "")[:200],
            "horizon": j.get("horizon") or "unknown",
            "prob": j.get("prob") or {},
        })
    if with_posterior:
        if not os.path.exists(AUDIT_DB):
            raise RuntimeError("后验账本不存在：%s" % AUDIT_DB)
        post = _load_posterior([m["id"] for m in matched], band)
        for m in matched:
            m["posterior"] = post.get(m["id"])
    else:
        for m in matched:
            m["posterior"] = None

    return {
        "ok": True,
        "schema_version": "jev.v2",  # 2026-10-30 JEVCONTRACTVER：契约版本
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "window_hours": hours,
        "filters": {"bias": bias, "code": code6, "event_type": event_type or [],
                    "with_posterior": bool(with_posterior), "limit": limit},
        "summary_window": window_counts,
        "total": total,
        "returned": len(matched),
        "posterior_band_pct": band if with_posterior else None,
        "low_signal_event_types": sorted(low_signal),
        "items": matched,
    }


def handle_get(handler, path: str, qs_raw: str) -> None:
    """portal_api do_GET 分派入口；异常一律 400/500 明确返回。"""
    qs = parse_qs(qs_raw or "")
    try:
        hours = _as_int(qs, "hours", 24, MIN_HOURS, MAX_HOURS)
        limit = _as_int(qs, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT)
        result = query(
            hours=hours,
            bias=_as_list(qs, "bias"),
            code=(qs.get("code") or [""])[-1],
            event_type=_as_list(qs, "event_type"),
            with_posterior=_as_bool(qs, "with_posterior"),
            limit=limit,
        )
    except ValueError as exc:
        handler._json({"ok": False, "error": "invalid_params", "message": str(exc)}, 400)
        return
    except RuntimeError as exc:
        handler._json({"ok": False, "error": "unavailable", "message": str(exc)}, 503)
        return
    except Exception as exc:  # noqa: BLE001
        handler._json({"ok": False, "error": "internal",
                       "message": "%s: %s" % (type(exc).__name__, exc)}, 500)
        return
    handler._json(result)
