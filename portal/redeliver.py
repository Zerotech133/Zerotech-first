# -*- coding: utf-8 -*-
"""W41 死信自动补达（2026-10-26）：退避 + 次数封顶 + 独立补达流水。

设计：
  · 数据源：dead_letter.list_dead_letters（原行 + redeliveries 数组）；
  · 仅对**带 payload 的死信**自动补达（旧死信无 payload → 标注 legacy_no_payload，
    人工处理，不猜内容）；
  · 退避：第 1/2/3 次补达间隔 60s / 300s / 1800s；最多 3 次；
  · 每次尝试写独立补达行（dead_letter.mark_redelivered），不冒充原发送成功；
  · 状态落 work/redeliver_state.json（attempts/last_ts）。
"""

from __future__ import annotations

import json
import os
import time

import dead_letter
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "work")
STATE = os.path.join(WORK, "redeliver_state.json")
BACKOFFS = [60, 300, 1800]
CAP = 3


def _state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _save_state(st):
    try:
        os.makedirs(WORK, exist_ok=True)
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/redeliver.py", exc)


def _parse_ts(ts):
    try:
        from datetime import datetime

        return datetime.fromisoformat(str(ts)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def pending(now=None):
    """→ 待补达列表（含 reason=ready/legacy_no_payload/exhausted/backoff）。

    2026-10-26 风暴修复：
      · 只处理原始死信（跳过 kind=dead_letter_redelivery 的补达失败回填行）；
      · 尝试次数取「账本补达行」与「state attempts」的较大值（防 list 截断丢计数）；
      · 单次最多扫 500 行。
    """
    now = time.time() if now is None else now
    st = _state()
    out = []
    for r in dead_letter.list_dead_letters(500):
        dead_id = r.get("dead_id")
        if not dead_id:
            continue
        kind = str(r.get("kind") or "")
        if kind.startswith("dead_letter_redelivery"):
            continue
        reds = r.get("redeliveries") or []
        if r.get("redelivered_at"):
            continue
        if any(x.get("ok") for x in reds):
            continue
        state_attempts = 0
        try:
            state_attempts = int((st.get(dead_id) or {}).get("attempts") or 0)
        except (TypeError, ValueError):
            state_attempts = 0
        attempts = max(len(reds), state_attempts)
        payload = r.get("payload")
        base = {
            "dead_id": dead_id,
            "kind": r.get("kind"),
            "channel": r.get("channel"),
            "target": r.get("target"),
            "why": r.get("why"),
            "attempts": attempts,
            "payload": payload,
        }
        if attempts >= CAP:
            out.append(dict(base, reason="exhausted"))
            continue
        if not payload:
            out.append(dict(base, reason="legacy_no_payload"))
            continue
        last = _parse_ts(reds[-1].get("ts")) if reds else _parse_ts(r.get("ts"))
        wait = BACKOFFS[min(attempts, len(BACKOFFS) - 1)]
        if now - last < wait:
            out.append(dict(base, reason="backoff", next_in=int(wait - (now - last))))
            continue
        out.append(dict(base, reason="ready"))
    return out


def run_once(sender, now=None):
    """执行一轮补达。sender(payload, item) → (ok, message_id, why)。

    返回 {"tried":n,"ok":n,"fail":n,"skipped":{reason:n}}。
    """
    st = _state()
    # 风暴断路器：10 分钟内累计尝试 >100 次 → 本轮熔断并留痕
    now_ts = time.time() if now is None else now
    gw = st.get("_global") or {}
    try:
        win = float(gw.get("window_start") or 0)
        n = int(gw.get("n") or 0)
    except (TypeError, ValueError):
        win, n = 0.0, 0
    if now_ts - win > 600:
        win, n = now_ts, 0
    if n >= 100:
        return {
            "tried": 0,
            "ok": 0,
            "fail": 0,
            "skipped": {"storm_breaker": 1},
            "tripped": True,
            "window_n": n,
        }
    res = {"tried": 0, "ok": 0, "fail": 0, "skipped": {}}
    for item in pending(now):
        if item["reason"] != "ready":
            res["skipped"][item["reason"]] = res["skipped"].get(item["reason"], 0) + 1
            continue
        res["tried"] += 1
        try:
            ok, mid, why = sender(item.get("payload"), item)
        except Exception as e:  # noqa: BLE001
            ok, mid, why = False, None, "%s: %s" % (type(e).__name__, str(e)[:100])
        dead_letter.mark_redelivered(item["dead_id"], bool(ok), mid, why or "")
        st.setdefault(item["dead_id"], {}).update(
            {"attempts": item["attempts"] + 1, "last_ts": time.time(), "ok": bool(ok)}
        )
        if ok:
            res["ok"] += 1
        else:
            res["fail"] += 1
    st["_global"] = {"window_start": win, "n": n + res["tried"], "ts": time.time()}
    _save_state(st)
    return res


def default_sender(payload, item):
    """默认补达发送器：payload 文本 → 原目标（chat_id/open_id/union_id）。

    经 feishu_client.send_msg_tracked（默认 via_hub=True，fail-closed）；
    critical=True 表示补达不应被静默窗吞。返回 (ok, message_id, why)。
    """
    try:
        import sys as _sys

        root = os.path.dirname(HERE)
        nc = os.path.join(root, "news_collector")
        if nc not in _sys.path:
            _sys.path.insert(0, nc)
        from feishu_client import send_msg_tracked

        target = str(item.get("target") or "")
        if not target:
            return False, None, "无目标"
        if target.startswith("oc_"):
            rid = "chat_id"
        elif target.startswith("on_"):
            rid = "union_id"
        else:
            rid = "open_id"
        text = (
            payload
            if isinstance(payload, str)
            else json.dumps(payload, ensure_ascii=False)
        )
        ok, why, mid = send_msg_tracked(
            str(text)[:4000],
            "text",
            target,
            receive_id_type=rid,
            kind="dead_letter_redelivery",
            critical=True,
        )
        return bool(ok), mid, str(why or "")
    except Exception as e:  # noqa: BLE001
        return False, None, "%s: %s" % (type(e).__name__, str(e)[:100])


def summary():
    items = pending()
    by = {}
    for it in items:
        by[it["reason"]] = by.get(it["reason"], 0) + 1
    return {"pending": len(items), "by_reason": by, "cap": CAP, "backoffs": BACKOFFS}
