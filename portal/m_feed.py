# -*- coding: utf-8 -*-
"""投喂面 H5 payload（P1-3，2026-10-26）：/m/feed。

只读展示 W42 投喂环（inbound_gateway）的工单/快照/去重/导出：
  work/inbound_tickets.jsonl   工单流水（受理/路由/证据/去重）
  work/inbound_dedup.jsonl     去重命中
  work/inbound_snapshots/      原文快照（HTML/JSON）
  work/inbound_export/         导出包（Markdown+JSON）
"""

from __future__ import annotations

import json
import os
import time
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "work")
TICKETS = os.path.join(WORK, "inbound_tickets.jsonl")
DEDUP = os.path.join(WORK, "inbound_dedup.jsonl")
SNAPS = os.path.join(WORK, "inbound_snapshots")
EXPORT = os.path.join(WORK, "inbound_export")

_CACHE = {"ts": 0.0, "data": None}
TTL = 30.0
ROUTE_CN = {
    "url": "链接→采集",
    "text": "长文→知识库",
    "km": "长文→知识库",
    "confirm": "确认→决策账",
    "clarify": "待澄清",
    "rejected": "身份闸拒绝",
    "ticker": "个股代码",
    "smart": "智能受理",
}


def _jsonl(path, limit=None):
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/m_feed.py", exc)
    return rows[-limit:] if limit else rows


def _dir_files(d, limit=10):
    out = []
    try:
        for name in os.listdir(d):
            fp = os.path.join(d, name)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            out.append(
                {
                    "name": name,
                    "size": st.st_size,
                    "ts": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
                }
            )
    except OSError:
        return []
    out.sort(key=lambda x: x["ts"], reverse=True)
    return out[:limit]


def _detail(ticket):
    ticket = str(ticket or "")
    rows = _jsonl(TICKETS)
    hit = None
    for r in rows:
        if str(r.get("ticket") or "") == ticket or str(
            r.get("ticket") or ""
        ).startswith(ticket):
            hit = r
    if not hit:
        return {
            "ok": False,
            "scene": "feed",
            "detail": True,
            "error": "投喂工单不存在：%s" % ticket,
        }
    tid = str(hit.get("ticket") or ticket)
    d = hit.get("detail") or {}
    route = ROUTE_CN.get(str(hit.get("route") or ""), str(hit.get("route") or "—"))
    blocks = [
        {
            "type": "stats",
            "title": "投喂工单",
            "items": [
                {
                    "label": "工单",
                    "value": tid[:12],
                    "sub": str(hit.get("ts") or ""),
                    "level": "info",
                },
                {"label": "路由", "value": route, "sub": "受理结果", "level": "info"},
                {
                    "label": "证据",
                    "value": str(hit.get("evidence") or "—"),
                    "sub": "军机驿口径",
                    "level": "ok",
                },
                {
                    "label": "去重",
                    "value": str(hit.get("duplicate_of") or "无"),
                    "sub": "L1/L2/L3",
                    "level": "warn" if hit.get("duplicate_of") else "ok",
                },
            ],
        },
        {
            "type": "md",
            "title": "摘要",
            "content": str(d.get("summary") or hit.get("summary") or "（无摘要）")[
                :1200
            ],
        },
        {
            "type": "table",
            "title": "字段",
            "headers": ["字段", "值"],
            "rows": [[k, str(v)[:120]] for k, v in (hit.get("detail") or {}).items()][
                :20
            ]
            or [["—", "—"]],
        },
        {
            "type": "note",
            "content": "只读详情：来源 work/inbound_tickets.jsonl；快照/导出见 /m/feed。",
        },
    ]
    return {
        "ok": True,
        "scene": "feed",
        "detail": True,
        "title": "投喂工单 · %s" % tid[:12],
        "subtitle": "投喂详情（只读）",
        "crumbs": "情报 / 投喂面 / 详情",
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "blocks": blocks,
        "ticket": tid,
    }


def _links_block(rows):
    """最近投喂详情链接（/m/feed/<ticket>）。"""
    lines = []
    try:
        import m_links

        seen = set()
        for r in rows[-8:][::-1]:
            tid = str(r.get("ticket") or "")
            if not tid or tid == "-" or tid in seen:
                continue
            seen.add(tid)
            u = m_links.url_for("feed", id=tid)
            if u:
                lines.append(
                    "- [%s](%s) · %s"
                    % (
                        tid,
                        u,
                        ROUTE_CN.get(
                            str(r.get("route") or ""), str(r.get("route") or "—")
                        ),
                    )
                )
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/m_feed.py", exc)
    if not lines:
        return {
            "type": "md",
            "title": "详情入口",
            "content": "（/m 链接未启用或暂无带号工单）",
        }
    return {"type": "md", "title": "工单详情（点击进入）", "content": "\n".join(lines)}


def feed_payload(ticket=""):
    now = time.time()
    if _CACHE["data"] is not None and now - _CACHE["ts"] < TTL:
        return _CACHE["data"]
    if ticket:
        return _detail(ticket)
    rows = _jsonl(TICKETS)
    dedup = _jsonl(DEDUP)
    today = time.strftime("%Y-%m-%d")
    n_today = sum(1 for r in rows if str(r.get("ts") or "").startswith(today))
    n_rej = sum(
        1
        for r in rows
        if str(r.get("status") or "") == "rejected"
        or str(r.get("route") or "") == "rejected"
    )
    n_dup = sum(1 for r in rows if r.get("duplicate_of"))

    trows = []
    for r in rows[-20:][::-1]:
        typ = ROUTE_CN.get(str(r.get("route") or ""), str(r.get("route") or "—"))
        ev = str(r.get("evidence") or "-")[:18]
        trows.append(
            [
                str(r.get("ts") or "")[5:16],
                str(r.get("ticket") or "—")[:12],
                typ,
                str(r.get("status") or "")[:10],
                ev,
            ]
        )

    snaps = _dir_files(SNAPS, 10)
    exports = _dir_files(EXPORT, 6)
    blocks = [
        {
            "type": "stats",
            "title": "投喂面（只读）",
            "items": [
                {
                    "label": "累计工单",
                    "value": str(len(rows)),
                    "sub": "受理/拒绝全量",
                    "level": "info",
                },
                {
                    "label": "今日",
                    "value": str(n_today),
                    "sub": "当天投喂",
                    "level": "info",
                },
                {
                    "label": "去重命中",
                    "value": str(n_dup),
                    "sub": "重复投喂",
                    "level": "warn" if n_dup else "ok",
                },
                {
                    "label": "身份闸拒绝",
                    "value": str(n_rej),
                    "sub": "名单外",
                    "level": "bad" if n_rej else "ok",
                },
            ],
        },
        _links_block(rows),
        {
            "type": "table",
            "title": "① 最近投喂（倒序）",
            "headers": ["时间", "工单", "类型", "状态", "证据"],
            "rows": trows or [["—", "—", "—", "—", "—"]],
            "note": "数据源 work/inbound_tickets.jsonl；证据级别按军机驿口径",
        },
        {
            "type": "table",
            "title": "② 原文快照归档",
            "headers": ["文件", "大小", "时间"],
            "rows": [
                [x["name"][:34], "%.0fKB" % (x["size"] / 1024), x["ts"]] for x in snaps
            ]
            or [["—", "—", "尚无快照"]],
            "note": "HTML+Jina 兜底落 work/inbound_snapshots（本机归档）",
        },
        {
            "type": "table",
            "title": "③ 导出包（Markdown+JSON）",
            "headers": ["文件", "大小", "时间"],
            "rows": [
                [x["name"][:34], "%.0fKB" % (x["size"] / 1024), x["ts"]]
                for x in exports
            ]
            or [["—", "—", "尚未导出"]],
            "note": "导出逃生门：全量 Markdown+JSON，幂等覆盖当日",
        },
        {
            "type": "md",
            "title": "④ 怎么用",
            "content": "· 在飞书把文章/链接/长文直接发给「零界·指令」或对应 bot，即产生一条投喂工单；\n"
            "· 受理卡 + 结果卡会回执工单号与证据级别；\n"
            "· 本页只读展示工单、快照、去重与导出；注入防御三件套随投喂链生效。",
        },
        {
            "type": "note",
            "content": "口径：只读聚合（W42 投喂环账本）；快照仅本机归档；去重等级 L1 MD5/L2 MinHash；"
            "L3 语义去重未启用时诚实标注。",
        },
    ]
    out = {
        "ok": True,
        "scene": "feed",
        "title": "投喂面",
        "subtitle": "投喂工单 · 快照归档 · 去重 · 导出（只读）",
        "crumbs": "情报 / 投喂面",
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "blocks": blocks,
    }
    _CACHE.update({"ts": time.time(), "data": out})
    return out
