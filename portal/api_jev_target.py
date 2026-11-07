# -*- coding: utf-8 -*-
"""标的三面聚合接口 —— `/api/jev/target/{code}`（2026-11-07）。

老田令：把「一只股票的基本面 + 行情 + 情绪 + 全部相关判定 + 后验」
一页出——下游（投研工作台/Agent）最想调的能力（目录 jev_target_drill 的
聚合形态）。

    GET /api/jev/target/600519?days=30&limit=20
        code   6 位代码（或带后缀 ts_code）
        days   判定/行情回看窗，1..180，默认 30
        limit  判定条数上限，1..100，默认 20

聚合四块（全只读，60s 进程内缓存）：
  profile    基本面快照：stock_basic（名称/行业/地域/上市日）× datamart
  quotes     行情面：daily 近 days 日（close/pct_chg，含区间累计涨跌）
  judgments  判定面：该股相关 JEV 判定（jev/query 同口径匹配
             stocks/companies）+ T+1 后验（dec_audit 联查），带方向分布
  sentiment  情绪面：判定 bias 分布（多/空/中性占比 + 加权净倾向）+
             高置信判定的代表标题（正负各 3）

只读纪律：news.db / jev_audit.db mode=ro；datamart 读层只读；
不写任何库、不调 Ollama。失败块降级（available:false + reason），不炸整页。
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
AUDIT_DB = os.path.join(STATION, "agent_platform", "data", "jev_audit",
                        "jev_audit.db")

MIN_DAYS, MAX_DAYS, DEF_DAYS = 1, 180, 30
MIN_LIMIT, MAX_LIMIT, DEF_LIMIT = 1, 100, 20
CACHE_TTL_S = 60

_CACHE: dict = {"key": None, "at": 0.0, "data": None}

_CODE_RE = re.compile(r"^(\d{6})(?:\.(SH|SZ|BJ))?$")


def _ro(path):
    return sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"),
                            uri=True, timeout=10)


def _norm_code(raw: str) -> tuple[str, str] | None:
    """'600519'/'600519.SH' → (ts_code '600519.SH', code6 '600519')。"""
    m = _CODE_RE.match((raw or "").strip().upper())
    if not m:
        return None
    code6, suf = m.group(1), m.group(2)
    if not suf:
        suf = "SH" if code6.startswith(("6", "9", "5")) else (
            "BJ" if code6.startswith(("4", "8")) else "SZ")
    return "%s.%s" % (code6, suf), code6


def _profile(code6: str) -> dict:
    """基本面快照（datamart stock_basic，只读）。"""
    try:
        sys.path.insert(0, os.path.join(STATION, "quant_backtest"))
        import datamart as dm
        sb = dm.read("stock_basic", codes=["%s" % code6], limit=5)
        hit = sb[sb["symbol"] == code6] if len(sb) else sb
        if not len(hit):
            return {"available": False, "reason": "stock_basic 无此代码"}
        r = hit.iloc[0]
        return {
            "available": True,
            "name": r.get("name"), "industry": r.get("industry"),
            "area": r.get("area"), "list_date": str(r.get("list_date") or ""),
            "list_status": r.get("list_status"),
        }
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": str(e)[:120]}


def _quotes(ts_code: str, days: int) -> dict:
    """行情面：近 days 日日线（datamart 只读）。"""
    try:
        sys.path.insert(0, os.path.join(STATION, "quant_backtest"))
        import datamart as dm
        end = dt.date.today().strftime("%Y%m%d")
        start = (dt.date.today() - dt.timedelta(days=int(days * 1.6) + 10)
                 ).strftime("%Y%m%d")  # 自然日→交易日冗余
        d = dm.read("daily", codes=[ts_code], start=start, end=end, limit=400)
        if d is None or not len(d):
            return {"available": False, "reason": "daily 无数据（停牌/未上市/源未就绪）"}
        d = d.sort_values("trade_date").tail(days)
        rows = [{
            "date": str(int(r.trade_date)), "close": round(float(r.close), 2),
            "pct_chg": round(float(r.pct_chg), 2) if r.pct_chg == r.pct_chg else None,
        } for r in d.itertuples()]
        closes = [r["close"] for r in rows]
        cum = round((closes[-1] / closes[0] - 1) * 100, 2) if len(closes) > 1 and closes[0] else None
        return {
            "available": True, "bars": rows,
            "window_days": days, "n_bars": len(rows),
            "last_close": closes[-1], "cum_chg_pct": cum,
            "span": [rows[0]["date"], rows[-1]["date"]],
        }
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": str(e)[:120]}


def _judgments(code6: str, days: int, limit: int) -> dict:
    """判定面：该股相关 JEV 判定 + T+1 后验。"""
    try:
        db = _ro(NEWS_DB)
        since = (dt.datetime.now() - dt.timedelta(days=days)
                 ).strftime("%Y-%m-%d %H:%M")
        rows = db.execute(
            "SELECT id, ts, title, jev FROM news "
            "WHERE jev IS NOT NULL AND jev!='' AND ts>=? "
            "AND (stocks LIKE ? OR jev LIKE ?) "
            "ORDER BY ts DESC LIMIT ?",
            (since, '%"%%code6%%"'.replace("code6", code6),
             '%%"companies": ["%s%%' % code6, limit * 3),
        ).fetchall()
        db.close()
    except sqlite3.Error as e:
        return {"available": False, "reason": str(e)[:120]}

    items, dist = [], {"bull": 0, "bear": 0, "neutral": 0}
    for nid, ts, title, jev_text in rows:
        try:
            j = json.loads(jev_text)
        except Exception:
            continue
        stocks = j.get("stocks") or []
        comps = j.get("companies") or []
        if code6 not in stocks and code6 not in comps:
            continue  # LIKE 命中的误配精确过滤
        bias = j.get("bias") if j.get("bias") in dist else "neutral"
        dist[bias] += 1
        items.append({
            "id": nid, "ts": ts, "title": (title or "")[:80],
            "bias": bias, "conf": j.get("conf"),
            "event_type": j.get("event_type"),
            "companies": comps,
            "src": j.get("src") or ("prefilter" if j.get("pref") else "model"),
        })
        if len(items) >= limit:
            break

    # T+1 后验（有 trade_day 的判定占比 + 命中率）
    posterior = {"available": False}
    try:
        adb = _ro(AUDIT_DB)
        pat = "%" + code6 + "%"
        n_all = adb.execute(
            "SELECT COUNT(*) FROM dec_audit WHERE companies LIKE ?",
            (pat,)).fetchone()[0]
        n_hit = adb.execute(
            "SELECT COUNT(*) FROM dec_audit WHERE companies LIKE ? "
            "AND ret IS NOT NULL", (pat,)).fetchone()[0]
        hit_ok = adb.execute(
            "SELECT COUNT(*) FROM dec_audit WHERE companies LIKE ? "
            "AND ret IS NOT NULL AND hit=1", (pat,)).fetchone()[0]
        adb.close()
        posterior = {
            "available": True,
            "judged_total": n_all, "verified": n_hit,
            "hit_rate": round(hit_ok / n_hit, 3) if n_hit else None,
        }
    except Exception:  # noqa: BLE001
        pass

    total = sum(dist.values())
    return {
        "available": True,
        "window_days": days,
        "total": total, "dist": dist,
        "net_bias": round((dist["bull"] - dist["bear"]) / total, 3) if total else None,
        "posterior": posterior,
        "items": items,
    }


def compute(code_raw: str, days: int, limit: int) -> dict:
    key = (code_raw, days, limit)
    now = time.time()
    if _CACHE["key"] == key and now - _CACHE["at"] < CACHE_TTL_S:
        return _CACHE["data"]
    nc = _norm_code(code_raw)
    if not nc:
        raise ValueError("code 须为 6 位数字或带 .SH/.SZ/.BJ 后缀")
    ts_code, code6 = nc
    data = {
        "schema": "jev_target.v1",
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "code": code6, "ts_code": ts_code,
        "window_days": days,
        "profile": _profile(code6),
        "quotes": _quotes(ts_code, days),
        "judgments": _judgments(code6, days, limit),
        "sentiment_note": "情绪面 = judgments.dist/net_bias（同源判定的方向聚合）",
    }
    _CACHE.update(key=key, at=now, data=data)
    return data


def handle_get(handler, path: str, qs_raw: str) -> None:
    m = re.match(r"^/api/jev/target/([^/?]+)$", path)
    if not m:
        handler._json({"ok": False, "error": "invalid_params",
                       "message": "路径须为 /api/jev/target/{code}"}, 400)
        return
    qs = parse_qs(qs_raw or "")
    try:
        days = int((qs.get("days") or [str(DEF_DAYS)])[-1])
        limit = int((qs.get("limit") or [str(DEF_LIMIT)])[-1])
        if not MIN_DAYS <= days <= MAX_DAYS:
            raise ValueError("days %d..%d" % (MIN_DAYS, MAX_DAYS))
        if not MIN_LIMIT <= limit <= MAX_LIMIT:
            raise ValueError("limit %d..%d" % (MIN_LIMIT, MAX_LIMIT))
        result = compute(m.group(1), days, limit)
    except ValueError as exc:
        handler._json({"ok": False, "error": "invalid_params",
                       "message": str(exc)}, 400)
        return
    except Exception as exc:  # noqa: BLE001
        handler._json({"ok": False, "error": "internal",
                       "message": "%s: %s" % (type(exc).__name__, exc)}, 500)
        return
    handler._json(result)
