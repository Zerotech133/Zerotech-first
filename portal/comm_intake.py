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

import comm_dispatch as dispatch
import comm_interest as interest
import comm_memory as mem
import threading

import comm_router as cr
import comm_zhuge as zhuge

# 处理动作 → 上游（inbound_gateway）据此决定回卡形态
ACTION_REPLY = "reply"          # 直接回文本卡
ACTION_DISPATCH = "dispatch"    # 派单回执（K5 前 stub）
ACTION_FALLBACK = None          # 交回原流程（澄清卡/知识库）


def enabled():
    """开关：默认关。env COMM_INTENT_ENABLED 优先，comm_flags.json 兜底。

    WO-FS-152：bot 由守护进程 spawn，环境变量继承不到旧进程之外的改动，
    故新增本机标志文件通道（见 comm_flags.py 文档）。
    """
    import comm_flags
    return comm_flags.get("intent")


def _mask(open_id):
    s = str(open_id or "")
    if len(s) <= 8:
        return s[:2] + "**"
    return s[:4] + "**" + s[-2:]


# ── K2.1 回复生成 ────────────────────────────────
_CONSULT_RE = None


def _is_consult(text):
    """疑问/求判断类文本 → 交诸葛亮咨询位（K6）。

    只在 smalltalk 意图下判（其余意图已有专属路由）；命中条件是疑问词
    或「分析/建议/怎么办」等求判断表述，避免把寒暄也丢给军师。
    """
    global _CONSULT_RE
    if _CONSULT_RE is None:
        import re
        _CONSULT_RE = re.compile(
            r"(为什么|为啥|怎么办|如何|怎么|该不该|能不能|是不是|"
            r"什么意思|讲讲|分析一下|建议|看法|意见|判断)")
    return bool(_CONSULT_RE.search(text or ""))


def build_reply(intent_result, open_id="", dispatch_result=None):
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

    if intent in ("stock_consult", "watch_setup"):
        # K5.1：真投递成功用真实工单号（lg-xxx）；投递未启用/失败如实说明，
        # 不假装「已转」——诚实口径优于话术漂亮。
        dr = dispatch_result or {}
        real_id = dr.get("dispatch_id") if dr.get("ok") else None
        if real_id:
            ticket_txt = real_id
            state_txt = "预计 5 分钟内回复" if intent == "stock_consult" \
                else "确认卡稍后送达"
            extra = []
        elif dr.get("reason") == "disabled":
            ticket_txt = "登记中（派单通道未启用，稍后确认）"
            state_txt = "已记录您的需求"
            extra = ["通道未启用·如实告知"]
        else:
            ticket_txt = "登记中（派单暂时不可用，稍后确认）"
            state_txt = "已记录您的需求"
            extra = ["派单异常·如实告知"]
        if intent == "stock_consult":
            return {"text": ("已收到您对 %s 的咨询，正在转接盯盘官（司马懿席）"
                             "分析，%s。工单 %s。"
                             % (code or "该标的", state_txt, ticket_txt)),
                    "action": ACTION_DISPATCH, "llm_ok": False,
                    "meta": ["派单·司马懿席", "时限5min",
                             "trace:" + trace] + extra}
        return {"text": ("已收到：为 %s 设置盯盘提醒。已转许褚营设置触发器，%s。"
                         "工单 %s。" % (code or "该标的", state_txt, ticket_txt)),
                "action": ACTION_DISPATCH, "llm_ok": False,
                "meta": ["派单·许褚G1", "操作确认",
                         "trace:" + trace] + extra}

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
def smart_intake(text, open_id, msg_id="", llm=None, async_sink=None):
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

    # K5.1：派单类意图先真投递（开关内置于 comm_dispatch），回执单号进文案
    dr = None
    if res["intent"] in ("stock_consult", "watch_setup", "complaint"):
        dr = dispatch.dispatch(res, open_id, msg_id=msg_id)
    # K6：小聊里夹着真问题（为什么/怎么办）→ 交诸葛亮咨询位；
    # 咨询位给不出答案（key 缺失且本地不可用）则回落澄清卡，不硬编。
    # 触发条件（细化）：smalltalk+疑问 → 军师；stock_consult 但**无具体标的**
    # （大盘/趋势类判断）→ 也交军师；有标的的个股咨询仍走司马懿席（不抢单）。
    _no_code = not (res.get("entities") or {}).get("code")
    _to_zhuge = zhuge.enabled() and _is_consult(text) and (
        res["intent"] == "smalltalk"
        or (res["intent"] == "stock_consult" and _no_code))
    if _to_zhuge:
        # K6.1（WO-FS-156）军师咨询改异步：本地 9b 推理实测 11~14 秒，
        # 同步等待期间客户侧零反馈（飞书显示无响应）。改为：
        # ①本函数立即回「军师思考中」回执卡（<1s）；
        # ②后台线程真咨询，算完经 async_sink 回调补发结果卡；
        # ③线程异常只降级为补发失败提示，不反噬消息主链。
        trace = res["trace_id"]
        rp = {"text": ("已收到您的问题，军师正在研判（通常 10~20 秒），"
                       "答案稍后单独送达。工单 %s。"
                       % ("ZG-" + trace[:6].upper())),
              "action": ACTION_REPLY, "llm_ok": False,
              "meta": ["咨询位·异步", "trace:" + trace]}
        res["intent"] = "consult"
        res["zhuge"] = {"mode": "async"}

        def _bg_consult():
            zr = zhuge.consult(text, open_id=open_id)
            if async_sink:
                try:
                    async_sink(open_id, text, zr, trace)
                except Exception:  # noqa: BLE001 —— 回调异常不反噬
                    pass

        threading.Thread(target=_bg_consult, daemon=True).start()
        dr = None
    else:
        rp = build_reply(res, open_id=open_id, dispatch_result=dr)
    if rp["action"] is ACTION_FALLBACK:
        # 仍判不出：记忆也记一笔（供后续代词消解），然后交回原链路
        mem.remember(open_id, "customer", text, intent=res["intent"],
                     code=(res.get("entities") or {}).get("code"))
        return None

    mem.remember(open_id, "customer", text, intent=res["intent"],
                 code=(res.get("entities") or {}).get("code"))
    mem.remember(open_id, "bot", rp["text"] or "", intent=res["intent"])

    # K4.1：行为统计落 customer_interest（只记统计不记原文）。
    # 订阅闸拦下的（blocked）由调用方另行标记——本层只看意图侧事实。
    ok_i, err_i = interest.record(
        open_id, intent=res["intent"],
        code=(res.get("entities") or {}).get("code"))

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
        "interest_ok": ok_i,
        "interest_error": err_i,
        "dispatch": dr,
        "elapsed_ms": int((time.time() - t0) * 1000),
        "msg_id": str(msg_id or "")[:64],
        "open_id_masked": _mask(open_id),
    }
