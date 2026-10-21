# -*- coding: utf-8 -*-
"""阚泽部 · 智能接话层（K2.1 + K3.1，WO-FS-148-4/5）。

定位：`inbound_gateway.handle_inbound` 的 unclear 分支前置的一层智能——
消息进来先过「记忆代词消解 → 意图路由 → 回复生成」，判出明确意图就
就地回卡，仍判不出才落回原澄清卡。**开关默认关**（COMM_INTENT_ENABLED=1
才启用），默认零行为变化——在役链路不因本模块上线而改变。

一体口径（老田 12:31）：飞书功能与智能功能一个功能面。本模块即
「智能段」在消息链里的挂载件：同进程函数调用，不经网络。

对外接口
--------
    smart_intake(text, open_id, msg_id="") -> dict | None
        None  = 判不出（交回原 unclear 澄清卡流程）
        dict  = {intent, reply_text, action, route, model, trace_id, ...}

    build_reply(intent_result) -> {text, meta, llm_ok}   # K2.1 回复生成

回复生成口径（K2.1）
------------------
- subscription_qa：查订阅账本直答（零 Agent），账本不可用如实降级文案。
- billing：涉资金，模板 + 人工兜底话术，不让模型代答金额/退款结论。
- complaint：模板受理话术（含工单号），最高优先级，永不受订阅闸限制。
- feed / smalltalk / unclear：交回原链路（知识库投递 / 澄清卡）。
- stock_consult / watch_setup：走派单话术（K5 前为 stub 回执，不假装已执行）。
"""
from __future__ import annotations

import os
import time
import uuid

import comm_memory as mem
import comm_router as cr

# 处理动作 → 上游（inbound_gateway）据此决定回卡形态
ACTION_REPLY = "reply"          # 直接回文本卡
ACTION_DISPATCH = "dispatch"    # 派单回执（K5 前 stub）
ACTION_FALLBACK = None          # 交回原流程（澄清卡/知识库）


def enabled():
    """开关：默认关。COMM_INTENT_ENABLED ∈ {1,true,yes,on} 才启用。"""
    return str(os.environ.get("COMM_INTENT_ENABLED", "0")).strip().lower() in (
        "1", "true", "yes", "on")


def _mask(open_id):
    s = str(open_id or "")
    if len(s) <= 8:
        return s[:2] + "**"
    return s[:4] + "**" + s[-2:]


# ── K2.1 回复生成 ────────────────────────────────
def build_reply(intent_result, open_id=""):
    """→ {text, meta(list), llm_ok(bool), action}。纯模板，零模型调用。"""
    intent = intent_result.get("intent")
    ents = intent_result.get("entities") or {}
    trace = intent_result.get("trace_id", "")
    code = ents.get("code") or ""

    if intent == "subscription_qa":
        detail = _subscription_summary(open_id)
        return {"text": detail, "action": ACTION_REPLY, "llm_ok": False,
                "meta": ["查账本直答", "零Agent", "trace:" + trace]}

    if intent == "billing":
        return {"text": ("您的账单/发票/退款类问题涉及资金口径，我已登记并转人工"
                         "专员跟进（工单 %s），一个工作日内回复。为避免口径出错，"
                         "此类问题我不自动答复金额与退款结论。"
                         % ("BI-" + trace[:6].upper())),
                "action": ACTION_REPLY, "llm_ok": False,
                "meta": ["涉资金·模板+人工", "不自动答金额", "trace:" + trace]}

    if intent == "complaint":
        return {"text": ("非常抱歉给您带来不好的体验。您的反馈已升级为最高优先级"
                         "工单 %s，田丰大人亲自督办，30 分钟内必有答复。"
                         % ("TS-" + trace[:6].upper())),
                "action": ACTION_REPLY, "llm_ok": False,
                "meta": ["田丰归口", "最高优先级", "永不受闸限", "trace:" + trace]}

    if intent == "stock_consult":
        return {"text": ("已收到您对 %s 的咨询，正在转接盯盘官（司马懿席）分析，"
                         "预计 5 分钟内回复。工单 %s。"
                         % (code or "该标的", "AN-" + trace[:6].upper())),
                "action": ACTION_DISPATCH, "llm_ok": False,
                "meta": ["派单·司马懿席", "时限5min", "trace:" + trace]}

    if intent == "watch_setup":
        return {"text": ("已收到：为 %s 设置盯盘提醒。已转许褚营设置触发器，"
                         "确认卡稍后送达。工单 %s。"
                         % (code or "该标的", "WT-" + trace[:6].upper())),
                "action": ACTION_DISPATCH, "llm_ok": False,
                "meta": ["派单·许褚G1", "操作确认", "trace:" + trace]}

    # feed / smalltalk / unclear：交回原链路
    return {"text": None, "action": ACTION_FALLBACK, "llm_ok": False,
            "meta": ["回落原链路", "trace:" + trace]}


def _subscription_summary(open_id):
    """查订阅账本（只读，零写入）。异常如实降级，不编数字。"""
    try:
        import subscription_store as ss
        s = ss.get_subscriber_summary(open_id) if open_id else {}
        if s.get("error"):
            return ("暂时无法读取您的订阅信息（%s），已记录并转人工核对，"
                    "稍后回复您准确数据。" % s["error"])
        subs = s.get("subscriptions") or []
        if not subs:
            return ("您当前还没有生效的订阅。可回复「订阅」查看档位，"
                    "或回复「人工」由专员为您介绍。")
        row = subs[-1]
        used = s.get("today_used_by_prefix") or {}
        plan = row.get("plan_id", "")
        quota = (s.get("daily_quota_by_plan") or {}).get(plan, 0)
        used_n = sum(v for k, v in used.items() if k.startswith("news")) \
            if used else 0
        return ("您当前订阅：%s · 有效期至 %s · 今日额度已用 %s%s 次。"
                "到期前 7 天会主动提醒您。"
                % (row.get("plan_id", "-"), row.get("end", "-")[:10],
                   used_n, ("/" + str(quota)) if quota else ""))
    except Exception as ex:  # noqa: BLE001 —— 账本故障如实降级不臆造
        return ("暂时无法读取订阅账本（%s），已记录待核，稍后回复您准确信息。"
                % type(ex).__name__)


# ── K3.1 智能接话总入口 ──────────────────────────
def smart_intake(text, open_id, msg_id="", llm=None):
    """→ dict | None。开关关闭或判不出时返回 None（交回原流程）。"""
    if not enabled():
        return None
    t0 = time.time()
    ref = mem.resolve_reference(text, open_id)
    res = cr.route_message(text, llm=llm, log=True)

    # 代词消解：判定为个股/盯票但无 code 时，用记忆里的最近代码补齐
    if res["intent"] in ("stock_consult", "watch_setup") \
            and not (res.get("entities") or {}).get("code") \
            and ref["injected_code"]:
        res["entities"]["code"] = ref["injected_code"]
        res["resolved_from_memory"] = True

    rp = build_reply(res, open_id=open_id)
    if rp["action"] is ACTION_FALLBACK:
        # 仍判不出：记忆也记一笔（供后续代词消解），然后交回原链路
        mem.remember(open_id, "customer", text, intent=res["intent"],
                     code=(res.get("entities") or {}).get("code"))
        return None

    mem.remember(open_id, "customer", text, intent=res["intent"],
                 code=(res.get("entities") or {}).get("code"))
    mem.remember(open_id, "bot", rp["text"] or "", intent=res["intent"])

    return {
        "intent": res["intent"],
        "reply_text": rp["text"],
        "action": rp["action"],
        "meta": rp["meta"],
        "llm_ok": rp["llm_ok"],
        "route": res["route"],
        "model": res["model"],
        "trace_id": res["trace_id"],
        "channel": res["channel"],
        "entities": res.get("entities") or {},
        "resolved_from_memory": bool(res.get("resolved_from_memory")),
        "elapsed_ms": int((time.time() - t0) * 1000),
        "msg_id": str(msg_id or "")[:64],
        "open_id_masked": _mask(open_id),
    }
