# -*- coding: utf-8 -*-
"""8091 只读汇总 API（供 Hermes 总控/H4 协同，2026-10-26）。

仅本机 127.0.0.1 消费；无写操作、无密钥透出。
  GET /api/m/strategy/summary   策略工单/影子汇总
  GET /api/m/feed/summary       投喂面汇总
  GET /api/m/customer/summary   客户域/飞书角色健康汇总
"""

from __future__ import annotations

import json
import os
import time
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "work")
DATA = os.path.join(HERE, "data")


def _rows(path, limit=None):
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
        return []
    return out[-limit:] if limit else out


def strategy_summary():
    try:
        import strategy_ring as sr

        orders = _rows(sr.ORDERS)
        shadow = _rows(sr.SHADOW)
        latest = {}
        for r in orders:
            if r.get("id"):
                latest[r["id"]] = r
        vals = list(latest.values())
        now = time.time()

        def _age(ts):
            try:
                from datetime import datetime

                return int(now - datetime.fromisoformat(str(ts)).timestamp())
            except (TypeError, ValueError):
                return None

        shadowing = 0
        for r in vals:
            if r.get("status") != "shadowing":
                continue
            sh = next((x for x in reversed(shadow) if x.get("id") == r["id"]), None)
            if sh and sh.get("status") == "shadowing":
                shadowing += 1
        recent = []
        vals.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
        for r in vals[:5]:
            bt = (r.get("backtest") or {}).get("result") or {}
            sm = bt.get("summary") or {}
            recent.append(
                {
                    "id": str(r.get("id") or "")[:12],
                    "status": r.get("status"),
                    "return_pct": sm.get("total_return_pct"),
                    "trades": sm.get("n_trades"),
                }
            )
        return {
            "ok": True,
            "total": len(vals),
            "shadowing": shadowing,
            "rejected": sum(1 for r in vals if r.get("status") == "rejected"),
            "backtested": sum(
                1 for r in vals if (r.get("backtest") or {}).get("status") == "done"
            ),
            "recent": recent,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "策略汇总不可用：%s" % str(e)[:120]}


def feed_summary():
    try:
        import m_feed

        d = m_feed.feed_payload()
        stats = {
            it.get("label"): it.get("value")
            for it in (d.get("blocks") or [{}])[0].get("items") or []
        }
        return {
            "ok": True,
            "stats": stats,
            "total": stats.get("累计工单", 0),
            "today": stats.get("今日", 0),
            "dedup": stats.get("去重命中", 0),
            "rejected": stats.get("身份闸拒绝", 0),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "投喂汇总不可用：%s" % str(e)[:120]}


def customer_summary():
    """客户域只读：飞书角色/凭据 + bot 心跳新鲜度（不涉客户隐私）。"""
    try:
        roles = []
        for base in (os.path.join(WORK, "feishu_secrets"),):
            if os.path.isdir(base):
                for fn in sorted(os.listdir(base)):
                    if fn.endswith(".json"):
                        roles.append(fn[:-5])
        hb = os.path.join(HERE, "logs", "feishu_bot_heartbeat.txt")
        age_s = None
        try:
            age_s = int(time.time() - os.path.getmtime(hb))
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal/m_summary.py", exc)
        return {
            "ok": True,
            "roles": roles,
            "roles_n": len(roles),
            "bot_heartbeat_age_s": age_s,
            "bot_fresh": bool(age_s is not None and age_s < 600),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "客户域汇总不可用：%s" % str(e)[:120]}
