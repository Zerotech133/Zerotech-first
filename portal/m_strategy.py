# -*- coding: utf-8 -*-
"""策略工单 H5（W43 环 3 工程落地，2026-10-26）。

页面 /m/strategy：
  · 手机提交选股方法（文本）→ 生成工单 → 后台 worker 跑解析/回测/前视/影子/拒绝；
  · 查看工单列表与回测/观察名单状态（只读）。
提交限制：页面令牌（scope=page, scene=strategy）有效即可；方法 10–4000 字；
窗口只能选预置且命中事件缓存的三个（1y/40d/3y）；worker 独立进程跑引擎，
不占 API 进程内存。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "work")
WLOG = os.path.join(WORK, "strategy_worker")
ORDERS = os.path.join(WORK, "strategy_orders.jsonl")
SHADOW = os.path.join(WORK, "strategy_shadow.jsonl")
WORKER = os.path.join(HERE, "strategy_worker.py")

WINDOWS = {
    "1y": ("20251013", "20261013", "近 1 年（有事件缓存）"),
    "40d": ("20260901", "20261012", "近 40 日（有事件缓存）"),
    "3y": ("20231010", "20261013", "近 3 年（有事件缓存）"),
}
STATE_CN = {
    "received": "已受理",
    "parsed": "已解析",
    "drafted": "草案完成",
    "lookahead_checked": "前视已检",
    "backtested": "已回测",
    "card_sent": "已出卡",
    "shadowing": "影子期",
    "approved": "已批准",
    "rejected": "已拒绝",
    "backtest_failed": "回测失败",
}
_SUBMIT_LOCK = {"last": {}, "ttl": 30}


def _rows(path):
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def _latest_orders(limit=15):
    rows = _rows(ORDERS)
    latest = {}
    for r in rows:
        if r.get("id"):
            latest[r["id"]] = r
    out = list(latest.values())
    out.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
    return out[:limit]


def _shadow_map():
    m = {}
    for r in _rows(SHADOW):
        if r.get("id"):
            m[r["id"]] = r
    return m


def _find_order(order_id):
    """按完整/前缀匹配最近一条工单。"""
    order_id = str(order_id or "")
    rows = _rows(ORDERS)
    hits = [r for r in rows if str(r.get("id") or "").startswith(order_id)]
    return hits[-1] if hits else None


def _detail(order_id):
    row = _find_order(order_id)
    if not row:
        return {
            "ok": False,
            "scene": "strategy",
            "detail": True,
            "error": "工单不存在：%s" % order_id,
        }
    oid = str(row.get("id") or order_id)
    bt = row.get("backtest") or {}
    sm = (bt.get("result") or {}).get("summary") or {}
    sh = _shadow_map().get(oid) or {}
    rows = _rows(ORDERS)
    trows = []
    for r in rows:
        if str(r.get("id") or "").startswith(oid):
            trows.append(
                [
                    str(r.get("ts") or "")[5:19],
                    STATE_CN.get(str(r.get("status")), str(r.get("status") or "")),
                    (r.get("reason") or "")[:40],
                ]
            )
    blocks = [
        {
            "type": "stats",
            "title": "工单概览",
            "items": [
                {
                    "label": "状态",
                    "value": STATE_CN.get(
                        str(row.get("status")), str(row.get("status") or "—")
                    ),
                    "sub": oid[:16],
                    "level": "warn" if row.get("status") == "shadowing" else "info",
                },
                {
                    "label": "回测收益",
                    "value": ("%+.2f%%" % sm["total_return_pct"])
                    if sm.get("total_return_pct") is not None
                    else "—",
                    "sub": "真实引擎",
                    "level": "ok" if (sm.get("total_return_pct") or 0) > 0 else "bad",
                },
                {
                    "label": "交易笔数",
                    "value": str(sm.get("n_trades", "—")),
                    "sub": "回测窗口内",
                    "level": "info",
                },
                {
                    "label": "影子",
                    "value": ("至 " + str(sh.get("end") or ""))
                    if sh.get("end")
                    else "未入影子",
                    "sub": "30 天期",
                    "level": "warn" if sh else "info",
                },
            ],
        },
        {
            "type": "md",
            "title": "方法与结论",
            "content": (
                "**方法**\n"
                + str(row.get("method") or "—")[:600]
                + "\n\n**结论/原因**\n"
                + str(row.get("reason") or bt.get("why") or "—")[:600]
            ),
        },
        {
            "type": "table",
            "title": "状态时间线",
            "headers": ["时间", "状态", "说明"],
            "rows": trows or [["—", "—", "暂无流水"]],
        },
        {
            "type": "note",
            "content": "闸门：回测失败/负收益不进影子；影子 30 天 + 人工拍板才转正。"
            "本页只读，提交/回测在 /m/strategy。",
        },
    ]
    return {
        "ok": True,
        "scene": "strategy",
        "detail": True,
        "title": "策略工单 · %s" % oid[:12],
        "subtitle": "工单详情（只读）",
        "crumbs": "情报 / 策略工单 / 详情",
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "blocks": blocks,
        "order_id": oid,
    }


def _links_block():
    """最近工单详情链接（/m/strategy/<id>）。"""
    lines = []
    try:
        import m_links

        for o in _latest_orders(6):
            oid = str(o.get("id") or "")
            u = m_links.url_for("strategy", id=oid)
            if u:
                lines.append(
                    "- [%s](%s) · %s"
                    % (
                        oid[:16],
                        u,
                        STATE_CN.get(str(o.get("status")), str(o.get("status") or "")),
                    )
                )
    except Exception:
        pass
    if not lines:
        return {
            "type": "md",
            "title": "详情入口",
            "content": "（/m 链接未启用或暂无工单）",
        }
    return {"type": "md", "title": "工单详情（点击进入）", "content": "\n".join(lines)}


def strategy_payload(order_id=""):
    orders = _latest_orders(20)
    sh = _shadow_map()
    n_done = sum(1 for o in orders if (o.get("backtest") or {}).get("status") == "done")
    n_shadow = sum(
        1
        for o in orders
        if o.get("status") == "shadowing"
        and (sh.get(o["id"]) or {}).get("status") == "shadowing"
    )
    n_rejected = sum(1 for o in orders if o.get("status") == "rejected")
    rows = []
    for o in orders:
        bt = o.get("backtest") or {}
        sm = (bt.get("result") or {}).get("summary") or {}
        ret = sm.get("total_return_pct")
        rows.append(
            [
                str(o.get("id") or "")[:12],
                str(o.get("ts") or "")[5:16],
                (o.get("method") or "")[:18],
                STATE_CN.get(str(o.get("status")), str(o.get("status") or "")),
                "—" if ret is None else "%+.2f%%" % ret,
                str(sm.get("n_trades", "—")),
                (o.get("reason") or (bt.get("why") or ""))[:22],
            ]
        )
    windows = "、".join("%s(%s)" % (k, v[2]) for k, v in WINDOWS.items())
    blocks = [
        {
            "type": "stats",
            "title": "策略工单（环 3）",
            "items": [
                {
                    "label": "工单",
                    "value": str(len(orders)),
                    "sub": "最近 20 条",
                    "level": "info",
                },
                {
                    "label": "回测完成",
                    "value": str(n_done),
                    "sub": "真实引擎",
                    "level": "ok" if n_done else "info",
                },
                {
                    "label": "影子中",
                    "value": str(n_shadow),
                    "sub": "30 天期",
                    "level": "warn" if n_shadow else "info",
                },
                {
                    "label": "已拒绝",
                    "value": str(n_rejected),
                    "sub": "闸门拦截",
                    "level": "bad" if n_rejected else "ok",
                },
            ],
        },
        {
            "type": "md",
            "title": "研究结论（观察名单）",
            "content": "· 二买（buy_filter=2）：3 年 walk-forward 4/7 段正、合计 +34.26%，"
            "但仅 17 笔（≈6 笔/年）→ 观察中，不进影子；\n"
            "· 三买（buy_filter=3）：各窗口均为负（合计 -37.52%）→ 弃用；\n"
            "· 提交方法后由后台 worker 解析→回测→前视检测→按闸门决定影子/拒绝。",
            "note": "依据：2026-10-26_W43_二买walkforward分段验证_v1.md",
        },
        _links_block(),
        {
            "type": "table",
            "title": "工单列表",
            "headers": ["工单", "时间", "方法", "状态", "回测收益", "笔数", "说明"],
            "rows": rows or [["—", "—", "—", "—", "—", "—", "暂无工单"]],
            "note": "窗口可选：" + windows,
        },
        {
            "type": "note",
            "content": "只读看板 + 提交入口；回测在独立 worker 进程跑（不占 API 内存），"
            "结果先过「负收益/回测失败不得进影子」闸门。",
        },
    ]
    if order_id:
        return _detail(order_id)
    return {
        "ok": True,
        "scene": "strategy",
        "title": "策略工单",
        "subtitle": "提交选股方法 · 回测 · 观察名单",
        "crumbs": "情报 / 策略工单",
        "generated_at": time.strftime("%Y-%m-%d %H:%M"),
        "blocks": blocks,
        "windows": {k: v[2] for k, v in WINDOWS.items()},
    }


def submit(method, window="1y", operator_open_id="", token_hash=""):
    method = str(method or "").strip()
    if not (10 <= len(method) <= 4000):
        return {"ok": False, "error": "方法需 10–4000 字"}
    if window not in WINDOWS:
        return {"ok": False, "error": "窗口非法（可选 %s）" % "/".join(WINDOWS)}
    now = time.time()
    if token_hash:
        last = _SUBMIT_LOCK["last"].get(token_hash) or 0
        if now - last < _SUBMIT_LOCK["ttl"]:
            return {"ok": False, "error": "提交过于频繁，请稍后再试"}
        _SUBMIT_LOCK["last"][token_hash] = now
    sys.path.insert(0, HERE)
    import strategy_ring

    d0, d1, _label = WINDOWS[window]
    r = strategy_ring.create_order(
        method, source="m_strategy", operator=operator_open_id or "h5"
    )
    if not r.get("ok"):
        return r
    oid = r["order"]["id"]
    os.makedirs(WLOG, exist_ok=True)
    log_path = os.path.join(WLOG, oid + ".log")
    err_path = os.path.join(WLOG, oid + ".err")
    try:
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        with open(log_path, "ab") as out, open(err_path, "ab") as err:
            subprocess.Popen(
                [sys.executable, "-X", "utf8", WORKER, oid, d0, d1],
                cwd=HERE,
                stdout=out,
                stderr=err,
                creationflags=flags,
                env=dict(
                    os.environ, STRATEGY_BACKTEST_ENABLED="1", PYTHONIOENCODING="utf-8"
                ),
            )
        return {
            "ok": True,
            "order_id": oid,
            "msg": "已受理，后台回测中（1–3 分钟）",
            "log": os.path.basename(log_path),
        }
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "order_id": oid,
            "error": "worker 启动失败：%s" % str(e)[:120],
        }
