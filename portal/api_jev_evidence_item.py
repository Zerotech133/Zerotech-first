# -*- coding: utf-8 -*-
"""单条证据四级溯源接口 —— `/api/jev/evidence/item`（2026-11-07）。

老田口径：这一板块的核心能力是「可追溯的证据」——任何一条结论都必须能
一路追到原始消息本体，且中间每个环节都要有据可查。

    GET /api/jev/evidence/item?id=wsc:3196404
    GET /api/jev/evidence/item?id=wsc:3196404&code=300750   # 指定后验标的

四级溯源链（四环，逐环标注是否查到，缺环如实说明）：
    ① 原文  原始消息本体（新闻库 news 表：id / 时间 / 信源 / 标题 / 正文）
    ② 判定  谁判的、怎么判的（模型名、耗时、方向、置信、事件类型、理由）
    ③ 后验  判定之后价格怎么走（dec_audit T+1 官方后验；缺失时行情次日兜底
            并打 proxied 标记，口径可区分）
    ④ 归组  这条在同期同类消息里处在什么位置（同日同标的条数 / 同向条数）

纪律：只读（news mode=ro、audit mode=ro、datamart 只读）；缺环不编造，
`rings[i].ok=false` + `reason` 说明；单条不可变 → 进程内 LRU 缓存。
"""
from __future__ import annotations

import source_names  # 渠道中文名
import datetime as dt
import json
import os
import re
import sqlite3
import time
from urllib.parse import parse_qs, unquote

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEWS_DB = os.path.join(STATION, "news_collector", "news.db")
AUDIT_DB = os.path.join(STATION, "agent_platform", "data", "jev_audit",
                        "jev_audit.db")

_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]{2,64}$")
_CACHE: dict = {}
_CACHE_ORDER: list = []
CACHE_MAX = 512

BIAS_CN = {"bull": "利多", "bear": "利空", "neutral": "中性"}


def _ro(path: str) -> sqlite3.Connection:
    uri = "file:%s?mode=ro" % path.replace("\\", "/")
    conn = sqlite3.connect(uri, uri=True, timeout=6.0)
    conn.row_factory = sqlite3.Row
    return conn


def _suf(code6: str) -> str:
    return "SH" if code6.startswith(("6", "9", "5")) else (
        "BJ" if code6.startswith(("4", "8")) else "SZ")


def _quotes_after(code6: str, ts_day: str, n: int = 6) -> list:
    """判定日之后的日线涨跌（兜底后验口径，来自 datamart daily）。"""
    try:
        import sys
        sys.path.insert(0, os.path.join(STATION, "quant_backtest"))
        import datamart as dm
        d = dm.read("daily", codes=["%s.%s" % (code6, _suf(code6))],
                    start=(dt.date.today() - dt.timedelta(days=40)
                           ).strftime("%Y%m%d"),
                    end=dt.date.today().strftime("%Y%m%d"), limit=400)
        if d is None or not len(d):
            return []
        rows = []
        for r in d.itertuples():
            td = str(int(r.trade_date))
            if td <= ts_day:
                continue
            rows.append({"trade_day": td,
                         "pct_chg": (round(float(r.pct_chg), 2)
                                     if r.pct_chg == r.pct_chg else None),
                         "close": (round(float(r.close), 2)
                                   if r.close == r.close else None)})
        rows.sort(key=lambda x: x["trade_day"])
        return rows[:n]
    except Exception:  # noqa: BLE001
        return []


def _audit(nid: str, code6: str) -> list:
    """dec_audit 官方 T+1 后验（按 news_id + 标的）。"""
    try:
        adb = _ro(AUDIT_DB)
        try:
            cols = [r[1] for r in adb.execute("PRAGMA table_info(dec_audit)")]
            has_exc = "excess" in cols
            sql = ("SELECT trade_day, ret, hit%s FROM dec_audit "
                   "WHERE news_id=?" % (", excess" if has_exc else ""))
            rows = []
            for r in adb.execute(sql, (nid,)):
                rows.append({
                    "trade_day": str(r["trade_day"]),
                    "ret": (round(float(r["ret"]), 2)
                            if r["ret"] is not None else None),
                    "excess": (round(float(r["excess"]), 2)
                               if has_exc and r["excess"] is not None else None),
                    "hit": bool(r["hit"]) if r["hit"] is not None else None,
                })
            return rows
        finally:
            adb.close()
    except Exception:  # noqa: BLE001
        return []


def _context(day: str, code6: str, bias: str) -> dict:
    """归组：同日同标的（或同日全市场已判）消息里的位置。"""
    try:
        db = _ro(NEWS_DB)
    except Exception:  # noqa: BLE001
        return {"ok": False, "reason": "新闻库不可读"}
    try:
        like = "%" + code6 + "%" if code6 else None
        if like:
            rows = db.execute(
                "SELECT id, jev FROM news WHERE ts>=? AND ts<? "
                "AND jev IS NOT NULL AND (jev LIKE ? OR stocks LIKE ?)",
                (day + " 00:00", day + " 23:59", like, like)).fetchall()
        else:
            rows = db.execute(
                "SELECT id, jev FROM news WHERE ts>=? AND ts<? "
                "AND jev IS NOT NULL",
                (day + " 00:00", day + " 23:59")).fetchall()
        same = 0
        tot = 0
        for r in rows:
            tot += 1
            try:
                if json.loads(r["jev"] or "{}").get("bias") == bias:
                    same += 1
            except Exception:  # noqa: BLE001
                pass
        return {"ok": tot > 0, "day": day, "scope": "该标的" if code6 else "全市场",
                "total_judged": tot, "same_bias": same,
                "reason": None if tot else "判定当日无其他已判消息"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": "归组查询失败：%s" % exc}
    finally:
        db.close()


def trace(nid: str, code: str = "") -> dict:
    nid = (nid or "").strip()
    if not nid or not _ID_RE.match(nid):
        raise ValueError("id 必填（形如 wsc:3196404，news 表主键）")
    ck = (nid, code)
    hit = _CACHE.get(ck)
    if hit and time.time() - hit[0] < 600:
        return hit[1]

    db = _ro(NEWS_DB)
    try:
        r = db.execute(
            "SELECT id, ts, source, title, content, stocks, jev, model, "
            "judged_at, hit, pushed, digested FROM news WHERE id=?",
            (nid,)).fetchone()
    finally:
        db.close()
    if r is None:
        raise KeyError("新闻库无此 id：%s（可能已过保留期或 id 有误）" % nid)

    j = {}
    if r["jev"]:
        try:
            j = json.loads(r["jev"]) or {}
        except Exception:  # noqa: BLE001
            j = {}
    meta = j.get("meter") or {}
    bias = j.get("bias") or ""

    # 标的优先级：显式 code > 判定 companies 首个 > 代码形态的 query
    comps = [str(c).strip() for c in (j.get("companies") or [])
             if len(str(c).strip()) == 6 and str(c).strip().isdigit()]
    code6 = ""
    if code and re.match(r"^\d{6}$", code):
        code6 = code
    elif comps:
        code6 = comps[0]

    day = (r["ts"] or "")[:10]
    day8 = day.replace("-", "")

    # ── ③ 后验 ──
    aud = _audit(nid, code6) if code6 else []
    proxied = False
    q_after = _quotes_after(code6, day8, 3) if code6 else []
    if not aud and q_after:
        proxied = True
        aud = [{"trade_day": q_after[0]["trade_day"],
                "ret": q_after[0]["pct_chg"], "excess": None, "hit": None}]

    ctx = _context(day, code6, bias) if day else {"ok": False,
                                                  "reason": "无时间戳"}

    content = (r["content"] or "").strip()
    rings = [
        {"ring": "①原文", "ok": bool(r["title"]),
         "reason": None if r["title"] else "仅标题缺失",
         "data": {"id": r["id"], "ts": r["ts"], "source": r["source"],
                  "source_cn": source_names.cn(r["source"]),
                  "source_desc": source_names.desc(r["source"]),
                  "title": r["title"], "content": content,
                  "content_len": len(content),
                  "stocks": r["stocks"], "pushed": r["pushed"],
                  "digested": r["digested"]}},
        {"ring": "②判定", "ok": bool(bias),
         "reason": None if bias else "该消息尚未判定（jev 为空）",
         "data": {"bias": bias, "bias_cn": j.get("bias_cn") or BIAS_CN.get(bias),
                  "conf": j.get("conf"), "scope": j.get("scope"),
                  "scope_cn": j.get("scope_cn"),
                  "event_type": j.get("event_type"),
                  "event_cn": j.get("event_cn"),
                  "impact": j.get("impact"), "impact_cn": j.get("impact_cn"),
                  "horizon": j.get("horizon"), "prob": j.get("prob"),
                  "reason": j.get("reason"),
                  "judge_model": meta.get("model") or r["model"],
                  "judge_src": meta.get("src") or j.get("src"),
                  "judge_ms": meta.get("ms"),
                  "judged_at": r["judged_at"],
                  "companies": comps}},
        {"ring": "③后验", "ok": bool(aud),
         "reason": None if aud else (
             "判定日之后尚无交易日行情，后验待验（T+1 未到）"
             if code6 and not q_after else
             "该消息未进入 dec_audit 后验（判定日过近或无有效行情）"
             if code6 else "判定未关联到具体标的，无从做价格后验"),
         "data": {"code": code6 or None, "proxied": proxied,
                  "rows": aud,
                  "caliber": ("行情次日涨跌兜底（非 dec_audit 官方 T+1）"
                              if proxied else
                              "dec_audit T+1 官方后验" if aud else None)}},
        {"ring": "④归组", "ok": bool(ctx.get("ok")),
         "reason": ctx.get("reason"),
         "data": ctx},
    ]
    n_ok = sum(1 for x in rings if x["ok"])

    # ── 引用串（可直接粘进报告） ──
    cite = "%s（%s，%s）%s" % (
        source_names.cn(r["source"]), (r["ts"] or "")[:16],
        r["title"] or "",
        "" if not bias else "｜判定：%s 置信%s" % (
            j.get("bias_cn") or BIAS_CN.get(bias),
            ("%.2f" % j["conf"]) if isinstance(j.get("conf"), (int, float))
            else "—"))
    if aud and aud[0]["ret"] is not None:
        cite += "｜后验 %s：%s%s%%%s" % (
            "T+1", "+" if aud[0]["ret"] > 0 else "", aud[0]["ret"],
            "（兜底口径）" if proxied else "")
    elif not code6:
        cite += "｜无关联标的，未做价格后验"

    out = {
        "ok": True, "schema": "jev_evidence_item.v1",
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "id": r["id"],
        "complete": n_ok, "complete_total": len(rings),
        "complete_txt": "%d/%d 环可查" % (n_ok, len(rings)),
        "rings": rings,
        "citation": cite,
        "permalink": "/jev_evidence.html#id=" + str(r["id"]),
        "api": "/api/jev/evidence/item?id=" + str(r["id"]),
    }
    _CACHE[ck] = (time.time(), out)
    _CACHE_ORDER.append(ck)
    while len(_CACHE_ORDER) > CACHE_MAX:
        _CACHE.pop(_CACHE_ORDER.pop(0), None)
    return out


def handle_get(handler, path: str, qs_raw: str) -> None:
    qs = parse_qs(qs_raw or "")
    try:
        nid = unquote((qs.get("id") or [""])[-1]).strip()
        code = (qs.get("code") or [""])[-1].strip()
        result = trace(nid, code)
    except ValueError as exc:
        handler._json({"ok": False, "schema": "jev_evidence_item.v1",
                       "error": "bad_request", "message": str(exc)}, 400)
        return
    except KeyError as exc:
        handler._json({"ok": False, "schema": "jev_evidence_item.v1",
                       "error": "not_found",
                       "message": str(exc).strip("'\"")}, 404)
        return
    except Exception as exc:  # noqa: BLE001
        handler._json({"ok": False, "schema": "jev_evidence_item.v1",
                       "error": "internal",
                       "message": "%s: %s" % (type(exc).__name__, exc)}, 500)
        return
    handler._json(result)
