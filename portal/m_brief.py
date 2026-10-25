# -*- coding: utf-8 -*-
"""信息采集·今日简报 H5 payload（2026-10-26）。

与飞书文档同源（news.db / sectors.json），按统一分格输出 blocks：
  今日速览 / 市场速览 / 自选速览 / 关注命中 / 板块热力 / AI晨报 / AI复盘 / 口径
页面：/m/brief（scene=brief）
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
NC = os.path.join(ROOT, "news_collector")
if NC not in sys.path:
    sys.path.insert(0, NC)

IDX_GRPS = ("A股", "港股", "美股", "商品", "汇率")


def _pct(v):
    if v is None:
        return "—"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    if v > 0:
        return "▲%+.2f%%" % v
    if v < 0:
        return "▼%.2f%%" % abs(v)
    return "0.00%"


def _err(msg):
    return {"ok": False, "scene": "brief", "error": msg}


def brief_payload():
    try:
        import news_doc_board as nd
        import news_ai
    except Exception as e:  # noqa: BLE001
        return _err("简报模块不可用：%s" % str(e)[:120])

    try:
        db = sqlite3.connect(nd.DB)
        try:
            brief = nd._report_meta(nd._latest_report(db, "ai_briefs"))
            review = nd._report_meta(nd._latest_report(db, "ai_reviews"))
            hits = nd._recent_hits(db)
        finally:
            db.close()
        sectors, sector_ts = nd._sector_items()
    except Exception as e:  # noqa: BLE001
        return _err("简报数据读取失败：%s" % str(e)[:120])

    mk = review if (review and (not brief or str(review["date"]) >= str(brief["date"]))) else brief
    markets = (mk or {}).get("markets") or []
    idx = [m for m in markets if m.get("grp") in IDX_GRPS]
    watch = [m for m in markets if m.get("grp") not in IDX_GRPS]
    n_up = sum(1 for x in sectors if x.get("pct", 0) > 0)
    n_dn = sum(1 for x in sectors if x.get("pct", 0) < 0)
    now = news_ai.now_cst().strftime("%Y-%m-%d %H:%M") if hasattr(news_ai, "now_cst") else time.strftime("%Y-%m-%d %H:%M")

    blocks = [
        {"type": "stats", "title": "今日速览", "items": [
            {"label": "指数", "value": str(len(idx)), "sub": "市场快照",
             "level": "info"},
            {"label": "自选", "value": str(len(watch)), "sub": "自选速览",
             "level": "info"},
            {"label": "板块", "value": "%d涨/%d跌" % (n_up, n_dn),
             "sub": (sector_ts or "板块快照")[:16],
             "level": "ok" if n_up >= n_dn else "bad"},
            {"label": "命中", "value": str(len(hits)), "sub": "近 24h 情报",
             "level": "warn" if hits else "info"},
        ], "note": "更新 %s · 晨报 08:00 / 复盘 15:30 自动刷新" % now},
    ]

    mk_rows = []
    for m in idx:
        dec = int(m.get("dec") or 2)
        mk_rows.append([str(m.get("grp") or ""), str(m.get("name") or ""),
                        ("%." + str(dec) + "f") % float(m.get("price") or 0),
                        _pct(m.get("pct"))])
    blocks.append({"type": "table", "title": "① 市场速览",
                   "headers": ["市场", "指数", "点位", "涨跌"],
                   "rows": mk_rows,
                   "note": "数据时点 %s · 红涨绿跌" % ((mk or {}).get("ts") or "—")})

    if watch:
        w_rows = []
        for m in watch:
            dec = int(m.get("dec") or 2)
            w_rows.append([str(m.get("name") or ""),
                           ("%." + str(dec) + "f") % float(m.get("price") or 0),
                           _pct(m.get("pct"))])
        blocks.append({"type": "table", "title": "自选速览",
                       "headers": ["标的", "现价", "涨跌"], "rows": w_rows})

    if hits:
        h_rows = []
        for it in list(reversed(hits)):
            t = (it.get("ts") or "")[-5:]
            tag = ("【%s】" % "/".join(it.get("stocks") or [])) if it.get("stocks") else ""
            h_rows.append([t, str(it.get("source") or ""),
                           (tag + (it.get("title") or ""))[:46]])
        blocks.append({"type": "table", "title": "② 今日关注命中（近 24h）",
                       "headers": ["时间", "来源", "标题"], "rows": h_rows,
                       "note": "来源：新闻采集命中 + 关联自选股"})
    else:
        blocks.append({"type": "note", "content": "② 近 24h 暂无关注命中"})

    if sectors:
        s_rows = [["🔴 领涨", s.get("name") or "", "%+.2f%%" % (s.get("pct") or 0)]
                  for s in sectors[:6]]
        s_rows += [["🟢 领跌", s.get("name") or "", "%+.2f%%" % (s.get("pct") or 0)]
                   for s in list(reversed(sectors))[:6]]
        blocks.append({"type": "table", "title": "③ 行业板块热力",
                       "headers": ["方向", "板块", "涨跌"], "rows": s_rows,
                       "note": "全行业 %d 个 · %d 涨 / %d 跌%s" % (
                           len(sectors), n_up, n_dn,
                           " · " + sector_ts if sector_ts else "")})

    for label, rep, sec_title in (("④ AI 金融晨报", brief, "brief"),
                                  ("⑤ AI 收盘复盘", review, "review")):
        if not rep:
            blocks.append({"type": "note", "content": label + "（今日未生成）"})
            continue
        body = rep.get("brief") or ""
        try:
            import news_doc_board as nd2
            body = nd2._compact_brief(body, 2200)
        except Exception:  # noqa: BLE001
            body = body[:2200]
        blocks.append({"type": "md", "title": label, "content": body,
                       "note": "%s · 生成于 %s（北京）· 模型 %s%s" % (
                           rep.get("date"), rep.get("ts"), rep.get("model"),
                           "" if rep.get("ok") else " · ⚠ AI 降级")})

    blocks.append({"type": "note", "content":
                   "口径：只读镜像（与飞书简报文档同源）；行情缺失显示「—」不猜测；"
                   "涨跌红涨绿跌；仅信息参考，不构成投资建议。"})
    return {"ok": True, "scene": "brief", "title": "信息采集 · 今日简报",
            "subtitle": "市场速览 · 关注命中 · 板块热力 · AI 晨报/复盘",
            "crumbs": "情报 / 今日简报",
            "generated_at": now, "blocks": blocks}
