# -*- coding: utf-8 -*-
"""推送中台 H5 payload（2026-10-26，本地直读版）。

不依赖 8097 HTTP 轮询（原 /api/state 需 6-15s），改为直接读本机同一份数据源：
  portal/work/push_flow.jsonl        推送流水（今日/24h/渠道/内容类型/降级）
  portal/dead_letter.py              死信计数
  quant_backtest/subscriptions.json  订阅配置 + outbox/subscribe_state.json
  portal/work/schedule.json          静默窗/调度
  portal/notification_decision.py    通知决策分类/目标/流水（本地 sqlite）
页面：/m/push（scene=push）
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORK = os.path.join(HERE, "work")
QBT = os.path.join(ROOT, "quant_backtest")
FLOW = os.path.join(WORK, "push_flow.jsonl")
SCHEDULE = os.path.join(WORK, "schedule.json")
SUBS = os.path.join(QBT, "subscriptions.json")
SUB_STATE = os.path.join(QBT, "outbox", "subscribe_state.json")

_CACHE = {"ts": 0.0, "data": None}
TTL = 60.0


def _json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _flow_stats():
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
    today = datetime.now().strftime("%Y-%m-%d")
    total = ok = fail = fallback = 0
    today_n = 0
    by_channel = {}
    by_kind = {}
    try:
        with open(FLOW, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict):
                    continue
                ts = str(r.get("ts") or "")
                try:
                    c = int(r.get("count") or 1)
                except (TypeError, ValueError):
                    continue
                if ts.startswith(today):
                    today_n += c
                if ts < cutoff:
                    continue
                total += c
                if r.get("ok") is True:
                    ok += c
                else:
                    fail += c
                ch = str(r.get("channel") or "?")
                by_channel[ch] = by_channel.get(ch, 0) + c
                if ch == "openapi-fallback":
                    fallback += c
                kd = str(r.get("kind") or "?")
                by_kind[kd] = by_kind.get(kd, 0) + c
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/m_push.py", exc)
    return {
        "today": today_n,
        "total24": total,
        "ok24": ok,
        "fail24": fail,
        "fallback24": fallback,
        "by_channel": by_channel,
        "by_kind": by_kind,
    }


def _dead_letters():
    try:
        import dead_letter

        n = dead_letter.count_since(24)
    except Exception:  # noqa: BLE001
        n = -1
    return {"count_24h": None if n < 0 else n, "degraded": None if n < 0 else n >= 20}


def _subs():
    cfg = _json(SUBS, {}) or {}
    items = cfg.get("subscriptions") or cfg.get("items") or []
    st = _json(SUB_STATE, {}) or {}
    out = []
    for s in items[:12]:
        when = s.get("when") or s.get("kinds") or []
        when_s = (
            "、".join(str(x) for x in when)[:16]
            if isinstance(when, list)
            else str(when)[:16]
        )
        recv = s.get("receivers") or []
        val = st.get(s.get("id")) if isinstance(st, dict) else None
        if isinstance(val, dict):
            val = val.get("count", val.get("day", "—"))
        out.append(
            [
                str(s.get("name") or s.get("id") or "")[:16],
                when_s,
                "、".join(str(x) for x in recv)[:18],
                "开" if s.get("enabled", True) else "关",
                str(val if val is not None else "—")[:10],
            ]
        )
    return out


def _schedule():
    d = _json(SCHEDULE, {}) or {}
    sc = d.get("schedule") or d
    return sc if isinstance(sc, dict) else {}


def _decisions():
    try:
        import notification_decision as nd

        cats = {
            k: (v[0] if isinstance(v, (list, tuple)) else str(v))
            for k, v in (nd.CATEGORIES or {}).items()
        }
        targets = [
            str(t.get("name") if isinstance(t, dict) else t)
            for t in (nd.targets() or [])
        ]
        recent = nd.recent() or []
        if isinstance(recent, dict):
            recent = recent.get("decisions") or []
        rows = []
        for x in recent[:10]:
            rows.append(
                [
                    str(x.get("created_at") or "")[:16],
                    str(cats.get(x.get("category")) or x.get("category") or "")[:14],
                    "%s/%s" % (x.get("agent_source") or "?", x.get("caller") or "?"),
                    str(x.get("send_at") or "")[:16],
                    str(x.get("status") or "")
                    + ("/" + str(x.get("outcome")) if x.get("outcome") else ""),
                ]
            )
        return {
            "cats": cats,
            "targets": targets,
            "rows": rows,
            "note": "分类 %d 项 · 决策目标 %d 个 · 记录 %d 条 · 只读不发送"
            % (len(cats), len(targets), len(recent)),
        }
    except Exception as e:  # noqa: BLE001
        return {
            "cats": {},
            "targets": [],
            "rows": [],
            "note": "决策账本读取失败：%s" % str(e)[:100],
        }


def push_payload():
    now = time.time()
    if _CACHE["data"] is not None and now - _CACHE["ts"] < TTL:
        return _CACHE["data"]
    fs = _flow_stats()
    dl = _dead_letters()
    subs = _subs()
    sc = _schedule()
    dec = _decisions()
    silent = (
        "%s–%s" % (sc.get("silent_start"), sc.get("silent_end"))
        if sc.get("silent_start")
        else "未拦截"
    )

    blocks = [
        {
            "type": "stats",
            "title": "推送总览（只读）",
            "items": [
                {
                    "label": "今日发送",
                    "value": str(fs["today"]),
                    "sub": "真实流水",
                    "level": "info",
                },
                {
                    "label": "24h 成功/失败",
                    "value": "%d/%d" % (fs["ok24"], fs["fail24"]),
                    "sub": "ok / 非 ok 计数",
                    "level": "ok" if fs["fail24"] == 0 else "warn",
                },
                {
                    "label": "降级直发 24h",
                    "value": str(fs["fallback24"]),
                    "sub": "openapi-fallback",
                    "level": "warn" if fs["fallback24"] else "info",
                },
                {
                    "label": "死信 24h",
                    "value": str(dl["count_24h"])
                    if dl["count_24h"] is not None
                    else "—",
                    "sub": "阈值 20",
                    "level": "bad" if dl.get("degraded") else "ok",
                },
            ],
        },
        {
            "type": "table",
            "title": "① 渠道分布（24h）",
            "headers": ["渠道", "条数"],
            "rows": [
                [k, str(v)]
                for k, v in sorted(fs["by_channel"].items(), key=lambda kv: -kv[1])
            ]
            or [["—", "—"]],
            "note": "闸门统一收口 8097 · 只统计真实发送行",
        },
        {
            "type": "table",
            "title": "② 内容类型 TOP（24h）",
            "headers": ["kind", "条数"],
            "rows": [
                [k[:28], str(v)]
                for k, v in sorted(fs["by_kind"].items(), key=lambda kv: -kv[1])[:8]
            ]
            or [["—", "—"]],
        },
        {
            "type": "md",
            "title": "③ 通知决策（只读）",
            "content": "分类：\n"
            + "\n".join(
                "- %s（%s）" % (v[:22], k) for k, v in list(dec["cats"].items())[:8]
            )
            if dec["cats"]
            else "（无分类数据）",
            "note": dec["note"],
        },
    ]
    if dec["rows"]:
        blocks.append(
            {
                "type": "table",
                "title": "④ 决策流水（最近 10 条）",
                "headers": ["记录时间", "分类", "来源", "计划发送", "状态"],
                "rows": dec["rows"],
                "note": "记录不触发发送；执行需单独授权",
            }
        )
    blocks.append(
        {
            "type": "table",
            "title": "⑤ 订阅推送",
            "headers": ["名称", "时机", "接收人", "状态", "今日"],
            "rows": subs or [["—", "—", "—", "—", "—"]],
            "note": "配置源 quant_backtest/subscriptions.json",
        }
    )
    blocks.append(
        {
            "type": "md",
            "title": "⑥ 调度与静默",
            "content": "静默窗：%s（critical 豁免）\n\n%s"
            % (silent, str(sc.get("note") or "")[:300]),
            "note": "配置源 portal/work/schedule.json",
        }
    )
    blocks.append(
        {
            "type": "note",
            "content": "口径：只读聚合（本机直读，不轮询 8097）；密钥/发送/决策执行不在此页开放；"
            "群成员数等需 8097 实核的信息不在此页猜测。",
        }
    )

    out = {
        "ok": True,
        "scene": "push",
        "title": "推送中台",
        "subtitle": "总览 · 渠道 · 死信 · 通知决策 · 订阅（只读）",
        "crumbs": "系统 / 推送中台",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "blocks": blocks,
    }
    _CACHE["ts"] = time.time()
    _CACHE["data"] = out
    return out
