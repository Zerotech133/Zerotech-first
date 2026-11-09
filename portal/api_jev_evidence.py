# -*- coding: utf-8 -*-
"""消息举证证据链接口 —— `/api/jev/evidence`（2026-11-07）。

老田令：任何一组消息可溯源举证——什么时间发的、持续发了多久、发生了
多少条、哪些渠道在发、涉及哪些标的、当天对股价的影响是什么。

    GET /api/jev/evidence?q=关键词或6位代码&days=14&bucket=day|hour
        q      关键词（标题 LIKE）或 6 位股票代码（走 jev companies）
        days   回看窗 1..90，默认 14
        bucket 时间线粒度 day（>2 天窗缺省）或 hour（≤2 天窗可用）

举证链六段（全只读）：
  flow       消息流溯源：首现时间 / 最近时间 / 持续时长 / 总条数 /
             方向分布（已判部分）/ 渠道分布（哪些 source 在发、各几条）
  timeline   逐桶（日/时）消息量 + 方向构成——「什么时间发、持续多久、
             有多少在发生」的直接举证数据
  targets    涉及标的排名（该消息组关联的上市公司，按判定附标次数）
  impact     逐交易日对齐：消息量（当日）vs 标的次日涨跌（主标的）——
             「消息发出后股价当天/次日怎么走」
  posterior  该消息组已判部分的后验战绩（方向命中率）
  verdict    自动举证结论：消息规模定性（刷屏/密集/零星）× 方向倾向 ×
             股价反应方向（涨/跌/平）× 后验支持度——一句话可引用

只读纪律：news.db / jev_audit.db mode=ro；datamart 只读；60s 缓存。
"""
from __future__ import annotations

import source_names  # 渠道中文名（老田令：渠道说中文）

import datetime as dt
import json
import os
import re
import sqlite3
import sys
import time
from urllib.parse import parse_qs, unquote

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEWS_DB = os.path.join(STATION, "news_collector", "news.db")

MIN_DAYS, MAX_DAYS, DEF_DAYS = 1, 90, 14
CACHE_TTL_S = 60
_CACHE: dict = {"key": None, "at": 0.0, "data": None}

_CODE_RE = re.compile(r"^\d{6}$")


def _ro(path):
    return sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"),
                            uri=True, timeout=10)


def compute(q: str, days: int, bucket: str) -> dict:
    key = (q, days, bucket)
    if _CACHE["key"] == key and time.time() - _CACHE["at"] < CACHE_TTL_S:
        return _CACHE["data"]

    q = (q or "").strip()
    if not q:
        raise ValueError("q 必填（关键词或 6 位代码）")
    is_code = bool(_CODE_RE.match(q))
    if not is_code and len(q) < 2:
        raise ValueError("关键词至少 2 个字符")

    since = (dt.datetime.now() - dt.timedelta(days=days)
             ).strftime("%Y-%m-%d %H:%M:%S")
    if bucket == "hour" and days > 2:
        bucket = "day"  # 小时桶只允许短窗，防 2000+ 桶
    if bucket not in ("day", "hour"):
        bucket = "day"

    db = _ro(NEWS_DB)
    try:
        if is_code:
            # 6 位代码：判定 companies（语义锚）+ stocks 兜底
            rows = db.execute(
                "SELECT id, ts, source, title, jev FROM news "
                "WHERE ts>=? AND (jev LIKE ? OR stocks LIKE ?) "
                "ORDER BY ts ASC",
                (since, '%"companies": ["%' + q + '%', '%' + q + '%')).fetchall()
        else:
            rows = db.execute(
                "SELECT id, ts, source, title, jev FROM news "
                "WHERE ts>=? AND title LIKE ? ORDER BY ts ASC",
                (since, "%" + q + "%")).fetchall()
    finally:
        db.close()

    n_total = len(rows)
    flow = {"q": q, "is_code": is_code, "total": n_total, "first": None,
            "last": None, "duration_h": None, "sources": {}, "bias": {}}
    if not n_total:
        data = {
            "ok": True, "schema": "jev_evidence.v1",
            "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "window_days": days, "flow": flow, "timeline": [],
            "targets": [], "impact": [], "posterior": {"available": False},
            "verdict": "窗口内无匹配消息——举证对象不存在。",
        }
        _CACHE.update(key=key, at=time.time(), data=data)
        return data

    # 注意：SELECT 首列是 id（2026-11-07 加溯源锚点），时间在索引 1
    flow["first"], flow["last"] = rows[0][1], rows[-1][1]
    try:
        t0 = dt.datetime.strptime(flow["first"][:16], "%Y-%m-%d %H:%M")
        t1 = dt.datetime.strptime(flow["last"][:16], "%Y-%m-%d %H:%M")
        flow["duration_h"] = round((t1 - t0).total_seconds() / 3600.0, 1)
    except Exception:  # noqa: BLE001
        pass

    src_cnt, bias_cnt, comp_cnt = {}, {"bull": 0, "bear": 0, "neutral": 0}, {}
    sample_buf = []  # (id, ts, source, title, bias, conf) 最近若干条（详情面板用）
    tl_map = {}
    judged = 0
    fmt = "%Y-%m-%d" if bucket == "day" else "%Y-%m-%d %H:00"
    day_vol = {}  # 交易日 → 消息量（impact 用）
    for nid, ts, source, title, jev in rows:
        src_cnt[source] = src_cnt.get(source, 0) + 1
        try:
            tk = dt.datetime.strptime(ts[:16], "%Y-%m-%d %H:%M").strftime(fmt)
        except Exception:  # noqa: BLE001
            tk = (ts or "?")[:10]
        slot = tl_map.setdefault(tk, {"n": 0, "bull": 0, "bear": 0, "neutral": 0})
        slot["n"] += 1
        d = None
        if jev:
            try:
                d = json.loads(jev)
            except Exception:  # noqa: BLE001
                d = None
        b = (d or {}).get("bias")
        if len(sample_buf) < 6 or True:
            sample_buf.append((nid, ts, source, (title or "")[:70], b,
                               (d or {}).get("conf")))
        if b in bias_cnt:
            bias_cnt[b] += 1
            slot[b] += 1
            judged += 1
            for c in (d.get("companies") or []):
                c6 = str(c).strip()
                if len(c6) == 6 and c6.isdigit():
                    comp_cnt[c6] = comp_cnt.get(c6, 0) + 1
        day_vol[(ts or "")[:10].replace("-", "")] = \
            day_vol.get((ts or "")[:10].replace("-", ""), 0) + 1

    flow["sources"] = [{"source": s, "n": n,
                        "source_cn": source_names.cn(s),
                        "source_desc": source_names.desc(s)}
                       for s, n in sorted(src_cnt.items(),
                                          key=lambda kv: -kv[1])]
    flow["bias"] = dict(bias_cnt, judged=judged)
    timeline = [{"t": t, **tl_map[t]} for t in sorted(tl_map)]

    targets = [{"code": c, "n": n} for c, n in
               sorted(comp_cnt.items(), key=lambda kv: -kv[1])[:10]]

    # ── impact：逐交易日 消息量 vs 主标的涨跌 ──
    impact = []
    main_code = targets[0]["code"] if (is_code and targets and
                                       targets[0]["code"] == q) else \
        (q if is_code else (targets[0]["code"] if targets else None))
    if main_code:
        try:
            sys.path.insert(0, os.path.join(STATION, "quant_backtest"))
            import datamart as dm
            suf = "SH" if main_code.startswith(("6", "9", "5")) else (
                "BJ" if main_code.startswith(("4", "8")) else "SZ")
            d = dm.read("daily", codes=["%s.%s" % (main_code, suf)],
                        start=(dt.date.today() -
                               dt.timedelta(days=days + 12)).strftime("%Y%m%d"),
                        end=dt.date.today().strftime("%Y%m%d"), limit=400)
            if d is not None and len(d):
                qmap = {str(int(r.trade_date)): (round(float(r.pct_chg), 2)
                         if r.pct_chg == r.pct_chg else None)
                        for r in d.itertuples()}
                for td, vol in sorted(day_vol.items()):
                    if td in qmap and qmap[td] is not None:
                        impact.append({"date": td, "news_n": vol,
                                       "pct_chg": qmap[td]})
        except Exception:  # noqa: BLE001 —— 行情块失败不炸举证
            pass

    # ── posterior：该消息组已判部分的后验（复用 dec_audit companies 口径） ──
    posterior = {"available": False}
    if main_code:
        try:
            adb = _ro(os.path.join(STATION, "agent_platform", "data",
                                   "jev_audit", "jev_audit.db"))
            pat = "%" + main_code + "%"
            n_all = adb.execute(
                "SELECT COUNT(*) FROM dec_audit WHERE companies LIKE ?",
                (pat,)).fetchone()[0]
            n_v = adb.execute(
                "SELECT COUNT(*) FROM dec_audit WHERE companies LIKE ? "
                "AND ret IS NOT NULL", (pat,)).fetchone()[0]
            n_hit = adb.execute(
                "SELECT COUNT(*) FROM dec_audit WHERE companies LIKE ? "
                "AND ret IS NOT NULL AND hit=1", (pat,)).fetchone()[0]
            adb.close()
            posterior = {"available": True, "code": main_code,
                         "judged_total": n_all, "verified": n_v,
                         "hit_rate": round(n_hit / n_v, 3) if n_v else None}
        except Exception:  # noqa: BLE001
            pass

    # ── verdict：自动举证结论 ──
    v = []
    per_day = n_total / max(1, len(timeline))
    scale = ("刷屏级" if per_day >= 50 else
             "密集" if per_day >= 10 else
             "零星" if per_day < 3 else "中等")
    v.append("消息规模 %s（%d 条 / %d 个%s，均 %.1f 条/%s）" % (
        scale, n_total, len(timeline),
        "日" if bucket == "day" else "时", per_day,
        "日" if bucket == "day" else "时"))
    if flow["duration_h"] is not None:
        v.append("自 %s 起持续 %.1f 小时至 %s" %
                 (flow["first"][:16], flow["duration_h"], flow["last"][:16]))
    if judged:
        net = bias_cnt["bull"] - bias_cnt["bear"]
        tone = ("明显利多" if net > judged * 0.2 else
                "明显利空" if net < -judged * 0.2 else "多空混杂")
        v.append("已判 %d 条中利多 %d / 利空 %d / 中性 %d，基调 %s" % (
            judged, bias_cnt["bull"], bias_cnt["bear"], bias_cnt["neutral"],
            tone))
    top_src = (flow["sources"][0]["source_cn"] if flow["sources"] else "-")
    v.append("主要扩散渠道 %s（%d 条）" % (
        top_src, flow["sources"][0]["n"] if flow["sources"] else 0))
    if impact:
        up = sum(1 for x in impact if (x["pct_chg"] or 0) > 0)
        dn = sum(1 for x in impact if (x["pct_chg"] or 0) < 0)
        v.append("重叠交易日 %d 天：%d 涨 %d 跌（消息日股价%s）" % (
            len(impact), up, dn,
            "偏涨" if up > dn else "偏跌" if dn > up else "无方向"))
    if posterior.get("hit_rate") is not None:
        v.append("该标的历史判定后验命中率 %.0f%%（判读可信度参照）" %
                 (posterior["hit_rate"] * 100))

    data = {
        "ok": True, "schema": "jev_evidence.v1",
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "window_days": days, "bucket": bucket,
        "flow": flow, "timeline": timeline, "targets": targets,
        "impact": impact, "posterior": posterior,
        "samples": [{"id": s[0], "ts": s[1], "source": s[2], "title": s[3],
                     "source_cn": source_names.cn(s[2]),
                     "bias": s[4], "conf": s[5],
                     "trace_url": "/api/jev/evidence/item?id=" + str(s[0])}
                    for s in sample_buf[-6:][::-1]],
        "verdict": "；".join(v) + "。",
    }
    _CACHE.update(key=key, at=time.time(), data=data)
    return data


def handle_get(handler, path: str, qs_raw: str) -> None:
    qs = parse_qs(qs_raw or "")
    try:
        q = unquote((qs.get("q") or [""])[-1]).strip()
        days = int((qs.get("days") or [str(DEF_DAYS)])[-1])
        bucket = (qs.get("bucket") or ["day"])[-1]
        if not MIN_DAYS <= days <= MAX_DAYS:
            raise ValueError("days %d..%d" % (MIN_DAYS, MAX_DAYS))
        result = compute(q, days, bucket)
    except ValueError as exc:
        handler._json({"ok": False, "error": "invalid_params",
                       "message": str(exc)}, 400)
        return
    except Exception as exc:  # noqa: BLE001
        handler._json({"ok": False, "error": "internal",
                       "message": "%s: %s" % (type(exc).__name__, exc)}, 500)
        return
    handler._json(result)
