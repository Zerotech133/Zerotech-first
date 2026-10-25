# -*- coding: utf-8 -*-
"""派单闭环：催办 / 回复 / 状态摘要（2026-10-25，老田反馈"派单后只能刷新、没有催促"）。

层次：
  comm_dispatch  → 纯存储与判定（nudge 计数、reply 记录、deadline 等待时长）
  本模块         → 动作与通知（给处理人/发起人发飞书卡；供 H5/API/卡片共用）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORTAL = ROOT / "portal"
NC = ROOT / "news_collector"
for p in (str(PORTAL), str(NC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import comm_dispatch  # noqa: E402

OWNER_FILE = PORTAL / "work" / "feishu_secrets" / "owners.json"


def owner_open_id():
    ov = os.environ.get("COMM_OWNER_OPEN_ID")
    if ov:
        return ov.strip()
    try:
        d = json.load(open(OWNER_FILE, encoding="utf-8"))
        ids = d if isinstance(d, list) else (d.get("open_ids") or d.get("owners") or [])
        return str(ids[0]) if ids else ""
    except Exception:  # noqa: BLE001
        return ""


def _mask(s):
    s = str(s or "")
    return s[:6] + "**" if len(s) > 6 else s


def _send(open_id, card, kind="comm_ticket"):
    if not open_id:
        return False, "no open_id"
    try:
        from feishu_client import send_msg
        return send_msg(json.dumps(card, ensure_ascii=False), "interactive",
                        open_id, receive_id_type="open_id",
                        kind=kind, critical=True)
    except Exception as e:  # noqa: BLE001
        return False, "%s: %s" % (type(e).__name__, str(e)[:120])


def _card(title, template, lines):
    els = [{"tag": "div", "text": {"tag": "lark_md",
                                   "content": "\n".join(lines)}}]
    els.append({"tag": "note", "elements": [
        {"tag": "plain_text", "content": "零界 · 工单闭环"}]})
    return {"config": {"wide_screen_mode": True},
            "header": {"template": template,
                       "title": {"tag": "plain_text", "content": title}},
            "elements": els}


def nudge(dispatch_id, by="customer", note="", notify_owner=True,
          notify_requester=True):
    """催办一次：计数 + 通知处理人 + 通知发起人（可选）。"""
    r = comm_dispatch.nudge(dispatch_id, by=by, note=note)
    if not r.get("ok"):
        return r
    owner = owner_open_id()
    req = r.get("open_id") or ""
    wait = r.get("waited_min") or 0
    lines = [
        "**工单 %s 第 %d 次催办**" % (dispatch_id, r["n"]),
        "已等待 %.1f 分钟 · 状态 %s · 渠道 %s" % (
            wait, r.get("state") or "-", by),
        "发起人 %s" % (_mask(req) or "-"),
    ]
    if r.get("escalate"):
        lines.append("⚠️ 已达升级阈值（≥%d 分钟或 ≥2 次），建议督办" %
                     comm_dispatch.ESCALATE_AFTER_MIN)
    if note:
        lines.append("说明：%s" % str(note)[:120])
    sent = {}
    if notify_owner:
        ok, why = _send(owner, _card(
            "⏰ 工单催办提醒", "red" if r.get("escalate") else "orange",
            lines), kind="comm_ticket")
        sent["owner"] = {"ok": ok, "why": why}
    if notify_requester and req and req != owner:
        ok, why = _send(req, _card(
            "⏰ 已为您催办", "blue",
            ["工单 %s 已催办第 %d 次，处理人已收到提醒。" %
             (dispatch_id, r["n"])]), kind="comm_patrol")
        sent["requester"] = {"ok": ok, "why": why}
    r["sent"] = sent
    return r


def reply(dispatch_id, text, by="handler"):
    """处理人回复：落库 + 推给发起人。"""
    rec = comm_dispatch.record_of(dispatch_id)
    if not rec:
        return {"ok": False, "error": "工单不存在或已归档"}
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "error": "回复内容为空"}
    if len(text) > 1000:
        text = text[:1000]
    req = (rec.get("payload") or {}).get("open_id") or ""
    ok, why = _send(req, _card(
        "📬 您的工单有新回复", "blue",
        ["**工单 %s**" % dispatch_id, text,
         "—— %s" % (by or "处理人")]), kind="comm_ticket")
    comm_dispatch.record_reply(dispatch_id, text, by=by)
    return {"ok": True, "sent": bool(ok), "why": why,
            "dispatch_id": dispatch_id}


def summary(dispatch_id):
    """H5 工单页用：状态 + 催办 + 回复列表。"""
    st = comm_dispatch.check_receipt(dispatch_id)
    info = comm_dispatch.nudge_info(dispatch_id)
    return {
        "ok": bool(st.get("ok")),
        "state": st.get("state"),
        "waited_min": round(comm_dispatch.waited_min(dispatch_id) or 0, 1),
        "nudge_n": info.get("n") or 0,
        "last_nudge": info.get("last") or "",
        "escalate": bool(info.get("escalate")),
        "replies": comm_dispatch.replies(dispatch_id),
        "can_nudge": (st.get("state") or "") == "inbox",
    }

def auto_nudge(dispatch_id, stage=1):
    """巡视自动催办（去重、合并、历史工单只提醒一次）。

    - 同一工单同一阶段只提醒一次（stage 持久化，bot 重启不重发）；
    - owner==发起人时只发**一张合并卡**（不再双发「进度提醒+催办提醒」）；
    - 超过 24h 的历史工单：只发一条「建议关闭/重开」，之后不再打扰。
    """
    rec = comm_dispatch.record_of(dispatch_id)
    if not rec:
        return {"ok": False, "error": "工单不存在或已归档"}
    st = comm_dispatch.check_receipt(dispatch_id)
    if (st.get("state") or "") != "inbox":
        return {"ok": True, "skipped": True, "reason": "已处理"}
    wait = comm_dispatch.waited_min(dispatch_id) or 0.0
    done = comm_dispatch.patrol_stage(dispatch_id)
    owner = owner_open_id()
    req = (rec.get("payload") or {}).get("open_id") or ""
    same = (not req) or (req == owner)

    # 历史工单收口：只发一次，之后不再打扰
    if wait >= 1440 and done < 9:
        comm_dispatch.set_patrol_stage(dispatch_id, 9)
        txt = ("⏱ **历史工单收口**\n工单 %s 已等待 %.0f 小时（%s 提交），"
               "建议关闭或重新发起。" % (dispatch_id, wait / 60.0,
                                       (rec.get("submitted_at") or "")[:10]))
        _send(req or owner, _card("⏱ 历史工单提醒", "grey", txt.split("\n")),
              kind="comm_patrol")
        return {"ok": True, "final": True, "waited_min": round(wait, 1)}

    if int(stage) <= done:
        return {"ok": True, "skipped": True, "stage": done}
    comm_dispatch.set_patrol_stage(dispatch_id, stage)
    stage = int(stage)
    if same:
        # 只发一张，不再叠加「进度提醒 + 催办提醒」
        lines = ["**工单 %s** 已等待 %.1f 分钟。" % (dispatch_id, wait)]
        if stage == 1:
            lines.append("还在处理中，已为您加急，稍后回复。")
        else:
            lines.append("已升级督办，稍后给您明确答复。")
        _send(req or owner, _card(
            "⏱ 处理进度提醒", "orange" if stage == 1 else "red", lines),
            kind="comm_patrol")
        return {"ok": True, "merged": True, "stage": stage,
                "waited_min": round(wait, 1)}

    lines = ["**工单 %s** 已等待 %.1f 分钟，请尽快处理。" %
             (dispatch_id, wait)]
    if stage >= 2:
        lines.append("⚠️ 已达升级阈值，建议督办。")
    _send(owner, _card("⏰ 工单催办提醒", "orange" if stage == 1 else "red",
                       lines), kind="comm_ticket")
    _send(req, _card("⏱ 处理进度提醒", "orange" if stage == 1 else "red",
                     ["工单 %s 还在处理中，已为您加急。" % dispatch_id]),
          kind="comm_patrol")
    return {"ok": True, "stage": stage, "waited_min": round(wait, 1)}
