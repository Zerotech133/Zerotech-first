# -*- coding: utf-8 -*-
"""个股叙事线接口 —— `/api/jev/story/{code}`（2026-11-07）。

老田口径（2026-11-07）：
  以个股为中心，把影响它股价的**所有新闻事件**串成时间轴；对每条做
  方向判断；按信源记账「**谁说对最多**」；监控**事态发生后股价是否
  真有变化**——有变化即证明该判断正确，形成判定→串联→记账→验证闭环。

    GET /api/jev/story/300750?days=30&limit=60
        code   6 位代码（或带后缀）
        days   回看窗 1..120，默认 30
        limit  事件线条数上限 1..200，默认 60

四块（全只读，60s 缓存）：
  timeline   事件线：该股全部已判事件按时间正序——时间/方向/conf/信源/
             标题/后验结果（ret/hit；None=待验证）——「基于时间轴把它
             们串在一起」的直接数据
  dual_track 双轨对齐：事件（上下标记）× 价格日 K（close/pct_chg）——
             消息轨与价格轨逐日并排，看「发事那天价格动没动」
  sources    信源战绩：按 source 聚合——说了几条/已验证几条/命中几条/
             准确率（= 谁说对最多）排行 + 各自信源的方向偏好
  verdict    自动结论：消息面总基调 × 价格趋势是否同向（判定被市场
             证实/背离）× 最可信信源 × 该股消息面可信度（后验命中率）

只读纪律：news/dec_audit mode=ro；datamart 只读；失败块降级不炸页。
"""
from __future__ import annotations

import source_names  # 渠道中文名

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
FACTS_DB = os.path.join(STATION, "agent_platform", "data", "intel",
                        "intel_facts.db")

MIN_DAYS, MAX_DAYS, DEF_DAYS = 1, 120, 30
MIN_LIMIT, MAX_LIMIT, DEF_LIMIT = 1, 200, 60
CACHE_TTL_S = 60
_CACHE: dict = {"key": None, "at": 0.0, "data": None}
_CODE_RE = re.compile(r"^(\d{6})(?:\.(SH|SZ|BJ))?$")


def _ro(path):
    return sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"),
                            uri=True, timeout=10)


def compute(code_raw: str, days: int, limit: int) -> dict:
    key = (code_raw, days, limit)
    if _CACHE["key"] == key and time.time() - _CACHE["at"] < CACHE_TTL_S:
        return _CACHE["data"]

    m = _CODE_RE.match((code_raw or "").strip().upper())
    if not m:
        raise ValueError("code 须为 6 位数字（可带 .SH/.SZ/.BJ）")
    code6, suf = m.group(1), m.group(2)
    if not suf:
        suf = "SH" if code6.startswith(("6", "9", "5")) else (
            "BJ" if code6.startswith(("4", "8")) else "SZ")
    ts_code = "%s.%s" % (code6, suf)

    since = (dt.datetime.now() - dt.timedelta(days=days)
             ).strftime("%Y-%m-%d %H:%M")

    # ── 事件线：news 判定 + dec_audit 后验逐条对齐 ──
    db = _ro(NEWS_DB)
    try:
        rows = db.execute(
            "SELECT n.id, n.ts, n.source, n.title, n.jev FROM news n "
            "WHERE n.jev IS NOT NULL AND n.jev!='' AND n.ts>=? "
            "AND (n.jev LIKE ? OR n.stocks LIKE ?) "
            "ORDER BY n.ts DESC LIMIT ?",
            (since, '%"companies": ["%' + code6 + '%', '%' + code6 + '%',
             limit * 3)).fetchall()
    finally:
        db.close()

    post_map = {}
    try:
        adb = _ro(AUDIT_DB)
        for nid, td, ret, exc, hit in adb.execute(
                "SELECT news_id, trade_day, ret, excess, hit FROM dec_audit "
                "WHERE companies LIKE ? AND ret IS NOT NULL",
                ('%' + code6 + '%',)):
            post_map[nid] = (td, ret, exc, hit)
        adb.close()
    except Exception:  # noqa: BLE001
        pass

    # ── 情报五要素（intel_facts.db）：event_type / org / place / thing ──
    # 2026-11-08：旧代码读 j.get("event_type")，但 jev JSON 里从来没有这个
    # 字段（13.7 万条实测 0 命中），该处恒为 None、"类型分布"永远是 other。
    # 改读情报要素库；库缺失/未回填时整块跳过，不阻断本接口。
    facts_map = {}
    ids = [r[0] for r in rows]
    if ids:
        try:
            fdb = _ro(FACTS_DB)
            q = ("SELECT news_id, event_type, org, place, thing FROM "
                 "news_facts WHERE news_id IN (%s)" % ",".join("?" * len(ids)))
            for nid, et, org, pl, th in fdb.execute(q, ids):
                facts_map[nid] = {
                    "event_type": et,
                    "org": json.loads(org or "[]"),
                    "place": json.loads(pl or "[]"),
                    "thing": json.loads(th or "[]")}
            fdb.close()
        except Exception:  # noqa: BLE001 —— 要素库可重建，缺失不阻断叙事线
            facts_map = {}

    events, src_stat = [], {}
    for nid, ts, source, title, jev in rows:
        try:
            j = json.loads(jev)
        except Exception:  # noqa: BLE001
            continue
        comps = [str(c).strip() for c in (j.get("companies") or [])]
        stocks = [str(s).strip() for s in (j.get("stocks") or [])]
        if code6 not in comps and code6 not in stocks and \
                code6 not in [s[:6] for s in stocks if len(s) > 6]:
            continue
        bias = j.get("bias") if j.get("bias") in ("bull", "bear", "neutral") else "neutral"
        fx = facts_map.get(nid) or {}
        ev = {
            "id": nid, "ts": ts, "source": source,
            "source_cn": source_names.cn(source),
            "title": (title or "")[:70], "bias": bias,
            "conf": j.get("conf"),
            "event_type": fx.get("event_type"),
            "org": fx.get("org") or [], "place": fx.get("place") or [],
            "thing": fx.get("thing") or [],
            "ret": None, "hit": None, "trade_day": None,
        }
        p = post_map.get(nid)
        if p:
            ev["trade_day"], ev["ret"], _, ev["hit"] = p[0], p[1], None, \
                (None if p[3] is None else bool(p[3]))
        events.append(ev)
        s = src_stat.setdefault(source, {"n": 0, "verified": 0, "hit": 0,
                                         "bull": 0, "bear": 0})
        s["n"] += 1
        if p:
            s["verified"] += 1
            if p[3]:
                s["hit"] += 1
        if bias in ("bull", "bear"):
            s[bias] += 1
        if len(events) >= limit:
            break
    events.sort(key=lambda e: e["ts"])  # 时间正序 = 叙事线

    # ── 价格轨 ──
    quotes = []
    try:
        sys.path.insert(0, os.path.join(STATION, "quant_backtest"))
        import datamart as dm
        d = dm.read("daily", codes=[ts_code],
                    start=(dt.date.today() -
                           dt.timedelta(days=int(days * 1.6) + 10)
                           ).strftime("%Y%m%d"),
                    end=dt.date.today().strftime("%Y%m%d"), limit=400)
        if d is not None and len(d):
            d = d.sort_values("trade_date").tail(days)
            quotes = [{"date": str(int(r.trade_date)),
                       "close": round(float(r.close), 2),
                       "pct_chg": (round(float(r.pct_chg), 2)
                                   if r.pct_chg == r.pct_chg else None),
                       "vol": (round(float(r.vol), 0)
                               if r.vol == r.vol else None)}
                      for r in d.itertuples()]
    except Exception:  # noqa: BLE001
        pass

    # ── 事态变化验证：事件日 vs 次日价格变化 ──
    qmap = {q["date"]: q for q in quotes}
    qlist = [q["date"] for q in quotes]
    confirmed = refuted = pending = 0
    for e in events:
        d8 = (e["ts"] or "")[:10].replace("-", "")
        if e["ret"] is not None:
            # 后验口径：hit=1 判定被价格变化证实
            if e["hit"]:
                confirmed += 1
            else:
                refuted += 1
            continue
        # 无后验时用事件日/次日 pct_chg 兜底标记（proxied=True 区分官方后验）
        i = qlist.index(d8) if d8 in qlist else -1
        if i >= 0 and i + 1 < len(qlist) and qmap[qlist[i + 1]]["pct_chg"] is not None:
            chg = qmap[qlist[i + 1]]["pct_chg"]
            e["ret"] = chg
            e["proxied"] = True  # 兜底口径：次日涨跌，非 dec_audit T+1 官方后验
            if (e["bias"] == "bull" and chg > 0) or \
               (e["bias"] == "bear" and chg < 0):
                e["hit"] = True
                confirmed += 1
            elif e["bias"] in ("bull", "bear"):
                e["hit"] = False
                refuted += 1
        else:
            pending += 1

    # ── 信源战绩排行（谁说对最多）──
    sources = []
    for s, v in src_stat.items():
        acc = round(v["hit"] / v["verified"], 3) if v["verified"] else None
        sources.append({"source": s, "source_cn": source_names.cn(s),
                        "n": v["n"], "verified": v["verified"],
                        "hit": v["hit"], "accuracy": acc,
                        "bull": v["bull"], "bear": v["bear"]})
    sources.sort(key=lambda x: (-(x["accuracy"] or 0), -x["verified"]))

    # ── 时空联动：事件后影响统计（老田令 2026-11-07）──
    # 事件日为 D0，统计 D1/D3/D5 的累计涨跌（自 D0 收盘起算），
    # 历史聚合 → 均值/中位/P25-P75 区间 = 「大致影响区间值」。
    impact_stats = {"windows": ["t1", "t3", "t5"], "by_window": {}}
    if quotes and events:
        closes = {q["date"]: q["close"] for q in quotes if q["close"]}
        dl = [q["date"] for q in quotes]
        for wname, wn in (("t1", 1), ("t3", 3), ("t5", 5)):
            chgs = []
            for e in events:
                if e["bias"] not in ("bull", "bear"):
                    continue
                d0 = (e["ts"] or "")[:10].replace("-", "")
                i = dl.index(d0) if d0 in dl else -1
                if i < 0 or i + wn >= len(dl):
                    continue
                c0 = closes[dl[i]]
                if not c0:
                    continue
                # 方向归一：与事件同向为正（利多看涨跌幅、利空取反）
                cum = (closes[dl[i + wn]] / c0 - 1) * 100
                if e["bias"] == "bear":
                    cum = -cum
                chgs.append(round(cum, 2))
            if chgs:
                chgs.sort()
                n_c = len(chgs)
                med = chgs[n_c // 2] if n_c % 2 else (chgs[n_c//2 - 1] + chgs[n_c//2]) / 2
                impact_stats["by_window"][wname] = {
                    "n": n_c,
                    "mean": round(sum(chgs) / n_c, 2),
                    "median": round(med, 2),
                    "p25": round(chgs[int(n_c * 0.25)], 2),
                    "p75": round(chgs[int(n_c * 0.75)], 2),
                    "min": chgs[0], "max": chgs[-1],
                    "same_dir_rate": round(
                        sum(1 for c in chgs if c > 0) / n_c, 3),
                }

    # ── verdict ──
    n_ev = len(events)
    judged_dir = sum(1 for e in events if e["bias"] in ("bull", "bear"))
    net = sum(1 if e["bias"] == "bull" else -1 for e in events
              if e["bias"] in ("bull", "bear"))
    price_net = (sum(1 for q in quotes if (q["pct_chg"] or 0) > 0) -
                 sum(1 for q in quotes if (q["pct_chg"] or 0) < 0)) if quotes else 0
    v = []
    v.append("窗口 %d 天串起 %d 个事件（方向性 %d 个：多 %d / 空 %d）" % (
        days, n_ev, judged_dir,
        sum(1 for e in events if e["bias"] == "bull"),
        sum(1 for e in events if e["bias"] == "bear")))
    if confirmed + refuted:
        v.append("事态后验证：**%d 个被价格变化证实 / %d 个证伪**（待验 %d）"
                 % (confirmed, refuted, pending))
        tot_v = confirmed + refuted
        v.append("该股消息面可信度（证实率）%.0f%%" % (confirmed / tot_v * 100))
    if sources and sources[0]["accuracy"] is not None:
        v.append("最可信信源 **%s**（准确率 %.0f%%，已验 %d 条）——听它说对最多" % (
            sources[0]["source_cn"], sources[0]["accuracy"] * 100,
            sources[0]["verified"]))
    t3s = impact_stats.get("by_window", {}).get("t3")
    if t3s:
        v.append("历史区间参考：该股同类事件后 3 日**同向累计 %.1f%%~%.1f%%（中位 %.1f%%，同向概率 %.0f%%，样本 %d）**" % (
            t3s["p25"], t3s["p75"], t3s["median"],
            t3s["same_dir_rate"] * 100, t3s["n"]))
    if judged_dir and quotes:
        agree = (net > 0 and price_net > 0) or (net < 0 and price_net < 0)
        v.append("消息基调与价格趋势%s（消息 %s / 价格 %s）" % (
            "同向——判定被市场走线证实" if agree else "背离——判定未被市场接受",
            "偏多" if net > 0 else "偏空" if net < 0 else "均衡",
            "偏涨" if price_net > 0 else "偏跌" if price_net < 0 else "平"))

    data = {
        "ok": True, "schema": "jev_story.v1",
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "code": code6, "ts_code": ts_code, "window_days": days,
        "timeline": events,
        "quotes": quotes,
        "sources": sources,
        "confirm": {"confirmed": confirmed, "refuted": refuted,
                    "pending": pending},
        "impact_stats": impact_stats,
        "verdict": "；".join(v) + "。",
    }
    _CACHE.update(key=key, at=time.time(), data=data)
    return data


def handle_get(handler, path: str, qs_raw: str) -> None:
    m = re.match(r"^/api/jev/story/([^/?]+)$", path)
    if not m:
        handler._json({"ok": False, "error": "invalid_params",
                       "message": "路径须为 /api/jev/story/{code}"}, 400)
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
