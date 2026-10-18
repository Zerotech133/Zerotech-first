# -*- coding: utf-8 -*-
"""K-ORG-201 轨道A：通知指挥底座契约（单一事实源）。

三轨道共用常量与校验器，只读零副作用：
  - QUEUE_STATES / map_decision_state   四态映射（W36 决策队列）
  - EVIDENCE_LIFECYCLE                  生命周期证据分级：设计≠代码验证≠
                                        演练≠真实发送≠自然定时首跑（W36 验收）
  - EVIDENCE_DELIVERY / map_delivery_evidence  送达证据分级（W38）：
                                        API 受理 ≠ 接收端核实 ≠ 死信
  - load_channels / validate_channels   频道×决策红线登记表（notify_channels.json）
  - validate_subscriber_entry           授权名单条目 schema（push_subscribers groups）
                                        ——W38 targets CRUD 与 W36 队列共用

原则：本模块不发 HTTP、不落盘、不读 DB；登记表缺失/损坏 fail-visible
返回 errors 非空，不抛异常（沿 api_daily_data 只读纪律）。
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CHANNELS_PATH = os.path.join(HERE, "notify_channels.json")

# ── W36 四态（原型「通知指挥」队列） ─────────────────────────
QUEUE_STATES = ("auto", "review", "blocked", "idle")
QUEUE_STATE_LABELS = {
    "auto": "自动决策（授权策略内已执行）",
    "review": "待老田确认",
    "blocked": "异常/阻断（投递失败或身份不匹配）",
    "idle": "不打扰（无新事实，仅留记录）",
}

# ── 生命周期证据分级（设计≠代码验证≠演练≠真实发送≠自然定时首跑） ──
EVIDENCE_LIFECYCLE = ("designed", "code_verified", "drilled",
                      "live_sent", "scheduled_first_run")
EVIDENCE_LIFECYCLE_LABELS = {
    "designed": "设计（未落码）",
    "code_verified": "代码验证（单测通过，未真实发送）",
    "drilled": "演练（DRYRUN/人工试发路径验证）",
    "live_sent": "真实发送（人工触发，已送达）",
    "scheduled_first_run": "自然定时首跑（计划任务自动触发过）",
}

# ── 送达证据分级（W38：rc=0 ≠ 飞书已读） ────────────────────
EVIDENCE_DELIVERY = ("api_accepted", "receiver_verified", "dead_letter")
EVIDENCE_DELIVERY_LABELS = {
    "api_accepted": "API 受理（发送 rc=0，不等于接收端可读）",
    "receiver_verified": "接收端核实（message_id 反查确认消息存在）",
    "dead_letter": "死信（投递失败，待补达，不冒充原发送成功）",
}

# ── 授权名单 schema 常量（push_subscribers.json groups 判例口径） ──
SUBSCRIBER_MARKETS = ("cn", "hk", "us", "commodity", "crypto", "fx")
SUBSCRIBER_TIERS = ("daily", "critical")
SUBSCRIBER_ID_TYPES = ("chat_id", "open_id")
AUTONOMY_LEVELS = ("auto", "approval_required", "record_only")

# decision DB status → 四态（recorded=已登记待确认/执行；attempted=占位后
# 结果不明，按 blocked 诚实呈现，不猜成功）
_DECISION_STATE = {
    "sent": "auto",
    "recorded": "review",
    "attempted": "blocked",
    "gate_or_send_failed": "blocked",
    "unknown_after_attempt": "blocked",
}


def map_decision_state(status):
    """decision DB status → 队列四态；未知状态诚实归 blocked 不猜。"""
    return _DECISION_STATE.get(str(status or ""), "blocked")


def map_delivery_evidence(evidence, status=""):
    """决策行 → 送达证据级别：显式 evidence 优先；无则按 status 保守
    兜底（sent=API 受理，其余不计证据）。未知值归 dead_letter 醒目。"""
    e = str(evidence or "").strip()
    if e:
        return e if e in EVIDENCE_DELIVERY else "dead_letter"
    return "api_accepted" if status == "sent" else ""


def _err(errors, where, why):
    errors.append("%s: %s" % (where, why))


def validate_channels(data):
    """登记表结构校验 → 错误列表（空=通过）。"""
    errors = []
    if not isinstance(data, dict):
        return ["登记表根必须是对象"]
    if not isinstance(data.get("version"), int):
        _err(errors, "version", "必须是整数")
    channels = data.get("channels")
    if not isinstance(channels, list) or not channels:
        _err(errors, "channels", "必须是非空数组")
        return errors
    seen = set()
    for i, ch in enumerate(channels):
        where = "channels[%d]" % i
        if not isinstance(ch, dict):
            _err(errors, where, "必须是对象")
            continue
        cid = ch.get("id")
        if not isinstance(cid, str) or not cid.strip():
            _err(errors, where, "id 必须是非空字符串")
        elif cid in seen:
            _err(errors, where, "id 重复：%s" % cid)
        else:
            seen.add(cid)
        if not isinstance(ch.get("name"), str) or not ch["name"].strip():
            _err(errors, where, "name 必须是非空字符串")
        kinds = ch.get("kinds")
        if not isinstance(kinds, list) or not kinds or \
                not all(isinstance(k, str) and k.strip() for k in kinds):
            _err(errors, where, "kinds 必须是非空字符串数组")
        if ch.get("autonomy") not in AUTONOMY_LEVELS:
            _err(errors, where, "autonomy 必须是 %s 之一" % (AUTONOMY_LEVELS,))
        red = ch.get("red_lines")
        if not isinstance(red, list) or not red or \
                not all(isinstance(r, str) and r.strip() for r in red):
            _err(errors, where, "red_lines 必须是非空字符串数组")
        if ch.get("evidence_level") not in EVIDENCE_LIFECYCLE:
            _err(errors, where, "evidence_level 必须是 %s 之一"
                 % (EVIDENCE_LIFECYCLE,))
    return errors


def load_channels(path=None):
    """登记表加载（fail-visible：缺失/损坏返回 ok=False + errors，不抛）。"""
    p = path or CHANNELS_PATH
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        return {"ok": False, "channels": [], "errors": ["登记表读取失败：%s" % e]}
    except ValueError as e:
        return {"ok": False, "channels": [], "errors": ["登记表 JSON 损坏：%s" % e]}
    errors = validate_channels(data)
    return {"ok": not errors, "channels": data.get("channels", []),
            "errors": errors}


def validate_subscriber_entry(group_id, entry):
    """授权名单单条校验 → 错误列表（空=通过）。schema 沿
    push_subscribers.json _note：name/markets/tier/chat_id/chat_id_type。"""
    errors = []
    where = "groups[%s]" % group_id
    if not isinstance(group_id, str) or not group_id.strip():
        return ["group_id 必须是非空字符串"]
    if not isinstance(entry, dict):
        return ["%s: 条目必须是对象" % where]
    if not isinstance(entry.get("name"), str) or not entry["name"].strip():
        _err(errors, where, "name 必须是非空字符串")
    markets = entry.get("markets")
    if not isinstance(markets, list) or not markets:
        _err(errors, where, "markets 必须是非空数组")
    else:
        bad = [m for m in markets if m not in SUBSCRIBER_MARKETS]
        if bad:
            _err(errors, where, "markets 含未知市场：%s" % ",".join(map(str, bad)))
    if entry.get("tier") not in SUBSCRIBER_TIERS:
        _err(errors, where, "tier 必须是 daily|critical")
    chat_id = entry.get("chat_id")
    if not isinstance(chat_id, str) or not chat_id.strip():
        _err(errors, where, "chat_id 必须是非空字符串")
    else:
        id_type = entry.get("chat_id_type") or "chat_id"
        if id_type not in SUBSCRIBER_ID_TYPES:
            _err(errors, where, "chat_id_type 必须是 chat_id|open_id")
        elif id_type == "chat_id" and not chat_id.startswith("oc_"):
            _err(errors, where, "chat_id 口径须以 oc_ 开头")
        elif id_type == "open_id" and not chat_id.startswith("ou_"):
            _err(errors, where, "open_id 口径须以 ou_ 开头")
    return errors
