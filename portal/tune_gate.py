# -*- coding: utf-8 -*-
"""调优闸门（K-PUSH-002 B2/B3）——客户反馈调优闭环的核心状态机。

铁律（工单拍板）：客户永远不直改模型，只投「调优请求单」；一切变更
经 审批（人工）→ 沙盒草案（回测参数草案）→ 落地（VIP 私有空间先行，
共享模型调整仅挂账另行实施）三道闸。审批不硬依赖 Hermes（K-ORG-112
判例）：主通道=飞书 bot 指令（feishu_bot_ws「审批」指令）+ 8097 API。

流程：submit（受理+解析）→ 审批卡到管理侧 → approve/reject（bot/API）
    → approve 产沙盒参数草案（bt2 深链人工跑对比，不自动跑重引擎）
    → 回执卡回群（客户可见闭环）→ deploy（独立动作：私有空间写入）

存储分工：
    账本 portal/work/tune_requests.jsonl（feedback_flow：阶段事件流）
    详情 portal/work/tune_store/<req_id>.json（原文全文/草案/审批记录）
    VIP 空间 portal/work/vip_overrides.json（customer → 私有参数覆盖）
管理员：portal/work/tune_admins.json {"open_ids":[], "chat_id":""}
    —— open_ids 空 = 内测期任何人可批（上线收费前必须配置，见 AGENTS 红线）
"""
import json
import os
import re
import secrets
import sys
import threading
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, STATION)  # feedback_flow 在站根（与 push_flow 并排）

STORE_DIR = os.path.join(HERE, "work", "tune_store")
VIP_PATH = os.path.join(HERE, "work", "vip_overrides.json")
ADMINS_PATH = os.path.join(HERE, "work", "tune_admins.json")
_lock = threading.Lock()

# 可审批状态（submit 之后、终态之前）
PENDING_STAGES = ("received", "parsed")

# ── 意图解析（轻量规则版；LLM 升级位=P3 Hermes 上岗后接管） ──────────
_INTENT_RULES = [
    ("chanlun", ("缠论", "中枢", "背驰", "笔", "线段")),
    ("risk", ("止损", "风控", "仓位", "止盈")),
    ("freq", ("频率", "推送太多", "推送少", "太吵", "打扰")),
    ("param", ("参数", "阈值", "灵敏度")),
]
_SHARED_WORDS = ("全局", "所有人", "大家", "共享", "模型本身", "整体调整")


def parse_intent(text):
    """原文 → {intent, scope, code}。规则版：代码正则 + 关键词分类。"""
    text = str(text or "")
    code = ""
    m = re.search(r"(\d{6})(?:\.(SH|SZ|BJ))?", text)
    if m:
        code = m.group(0)
    intent = "opinion"
    for tag, words in _INTENT_RULES:
        if any(w in text for w in words):
            intent = tag
            break
    scope = "shared" if any(w in text for w in _SHARED_WORDS) else "private"
    return {"intent": intent, "scope": scope, "code": code}


# ── 详情存储（一单一文件，原子写） ─────────────────────────────────
def _store_path(req_id):
    return os.path.join(STORE_DIR, "%s.json" % req_id)


def _save_store(doc):
    os.makedirs(STORE_DIR, exist_ok=True)
    tmp = _store_path(doc["req_id"]) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _store_path(doc["req_id"]))


def _load_store(req_id):
    try:
        with open(_store_path(req_id), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ── 管理员 ────────────────────────────────────────────────────────
def _admins():
    try:
        with open(ADMINS_PATH, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def is_admin(open_id):
    """审批权限：白名单非空时严格校验；空 = 内测开放（上线前必须配置）。"""
    ids = _admins().get("open_ids") or []
    return True if not ids else (open_id in ids)


# ── 飞书卡片（审批通知/回执） ─────────────────────────────────────
def _send_card(card, to_group=False):
    """发卡 fail-safe：失败吞掉返回 False，绝不影响闭环主链路。
    to_group=True：优先管理群（未配 chat_id 降级发个人——回执不能哑）。"""
    try:
        from feishu_card_v2 import send_card
        if to_group:
            chat = _admins().get("chat_id") or ""
            if chat:
                ok = bool(send_card(card, receive_id=chat,
                                    receive_id_type="chat_id")[0])
                if ok:
                    return True
        return bool(send_card(card)[0])
    except Exception:
        return False


def _approval_card(rid, doc, p):
    """待审批卡（发管理侧）：v3 交互域（紫）+ 通过/驳回回调按钮。

    K-PUSH-004：回调按钮走 bot WS 长连接链（card.action.trigger 推送，
    不经 8097），手机可点——破 K-STOCK-305「8097 绑 127.0.0.1 点不开」
    局限。文本指令保留为兜底通道（按钮链路故障时仍可审批）。"""
    from feishu_card_v2 import build_v3
    rows = [("客户", "%s（%s / %s）" % (
                doc["customer"], doc["plan"],
                {"voice": "语音", "text": "文字"}.get(doc["source"], doc["source"]))),
            ("意图", "%s ｜ 范围 %s%s" % (
                p["intent"], p["scope"],
                (" ｜ 标的 " + p["code"]) if p["code"] else "")),
            ("原文", doc["text"][:160])]
    howto = ("<font color='grey'>按钮不生效时兜底：回复 `审批 %s 通过` / "
             "`审批 %s 驳回 原因`，或 8097 推送中台 API</font>" % (rid, rid))
    card, _audit = build_v3(
        "interact", rid, "tune_gate", rows=rows, md=howto,
        buttons=[("✓ 通过", ("action", "tune_approve", {"req_id": rid}), "primary"),
                 ("✗ 驳回", ("action", "tune_reject", {"req_id": rid}), "danger")],
        tags="#tune #pending")
    return card


def _receipt_card(rid, ok, extra):
    """回执卡（回群，客户可见）v3 回执域（金）——驳回必须给原因，闭环可感知。"""
    from feishu_card_v2 import build_v3
    if ok:
        body = "**已通过审核** ✅\n%s\n\n下一步：沙盒回测对比（新逻辑 vs 现行），验证达标后落地到您的专属参数空间。" % extra
        tags = "#tune #approved"
    else:
        body = "**未通过审核** ❌\n%s\n\n感谢反馈——您的观点已入档，将汇入后续模型迭代参考。" % extra
        tags = "#tune #rejected"
    card, _audit = build_v3("receipt", rid, "tune_gate", md=body, tags=tags)
    return card


# ── 沙盒草案（B3：不自动跑重引擎，产参数草案 + bt2 深链人工跑） ────
# 2026-10-17 审计修复：bt_view.html 无此文件（死链），portal_api.py 注册的
# 路由是 /btview，改用短路由。
_BT2_URL = "http://127.0.0.1:8091/btview"


def _sandbox_draft(doc, p):
    """审批通过 → 回测参数草案（结构供 bt2 预填，人工点跑对比）。
    重计算复用现有回测台（backtest_engine_v2 可编程，但首期不自动跑：
    数据环境重、跑批时长不可控——钩子留好，P3 治理循环再自动并行）。"""
    intent_cn = {"chanlun": "缠论中枢参数", "risk": "止损/仓位风控",
                 "freq": "推送频率", "param": "策略阈值",
                 "opinion": "观点校准"}[p["intent"]]
    draft = {
        "bt2_params_hint": {
            "symbols": [p["code"]] if p["code"] else [],
            "note": "调优方向：%s（客户 %s）" % (intent_cn, doc["customer"]),
            "compare": "baseline vs override",
        },
        "run_url": _BT2_URL,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    return draft


# ── VIP 私有参数空间 ─────────────────────────────────────────────
def _vip_write(customer, params, req_id, operator):
    """customer → 私有参数覆盖（原子写整文件；策略侧消费接线=后续批次，
    消费端点见 8097 /api/state vip 概览）。"""
    with _lock:
        try:
            with open(VIP_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        data[customer] = {"params": params, "req_id": req_id,
                          "operator": operator,
                          "updated": datetime.now().isoformat(timespec="seconds")}
        os.makedirs(os.path.dirname(VIP_PATH), exist_ok=True)
        tmp = VIP_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, VIP_PATH)


def vip_overrides():
    """读取 VIP 私有参数空间（消费方：策略展示层/后续批次接线）。"""
    try:
        with open(VIP_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ── 对外动作 ──────────────────────────────────────────────────────
def submit(text, customer="匿名客户", plan="internal", source="text"):
    """受理一条调优请求：解析 → 双落账（received+parsed）→ 详情落盘 →
    审批卡。返回 req_id（任何内部失败仍返回 req_id——账本 fail-safe）。"""
    import feedback_flow
    rid = "TUNE-%s-%s" % (datetime.now().strftime("%y%m%d"), secrets.token_hex(2))
    p = parse_intent(text)
    doc = {"req_id": rid, "text": str(text or "")[:2000],
           "customer": str(customer or "匿名客户")[:60],
           "plan": str(plan or "internal")[:20],
           "source": str(source or "text")[:20],
           "intent": p["intent"], "scope": p["scope"], "code": p["code"],
           "created": datetime.now().isoformat(timespec="seconds")}
    feedback_flow.record(rid, "received", customer=doc["customer"],
                         plan=doc["plan"], source=doc["source"],
                         note="语音受理（飞书客户端转文字）"
                         if source == "voice" else "文字受理")
    feedback_flow.record(rid, "parsed", customer=doc["customer"],
                         plan=doc["plan"], source=doc["source"],
                         intent=p["intent"], scope=p["scope"],
                         note="标的 %s" % p["code"] if p["code"] else "无标的")
    try:
        _save_store(doc)
    except Exception:
        pass
    _send_card(_approval_card(rid, doc, p), to_group=True)
    return rid


def _pending_doc(rid):
    """待审状态校验：返回 (doc, err)。doc 从详情+账本视图合并重建。"""
    import feedback_flow
    view = feedback_flow.requests_view().get(rid)
    if not view:
        return None, "请求单不存在：%s" % rid
    if view["stage"] not in PENDING_STAGES:
        return None, "状态 %s 不可审批（仅待审单可操作）" % view["stage"]
    doc = _load_store(rid) or {}
    doc.update({k: view[k] for k in ("customer", "plan", "source",
                                     "intent", "scope") if view.get(k)})
    doc.setdefault("req_id", rid)
    doc.setdefault("text", "")
    return doc, ""


def approve(req_id, operator="", open_id="", note=""):
    """审核通过 → 沙盒草案 + 回执。共享范围仅挂账不实施（另行工单）。"""
    import feedback_flow
    if open_id and not is_admin(open_id):
        return {"ok": False, "error": "无审批权限（tune_admins.json 白名单）"}
    doc, err = _pending_doc(req_id)
    if err:
        return {"ok": False, "error": err}
    p = {"intent": doc.get("intent", "opinion"),
         "scope": doc.get("scope", "private"),
         "code": doc.get("code", "")}
    feedback_flow.record(req_id, "approved", operator=operator or open_id,
                         intent=p["intent"], scope=p["scope"], note=note[:200])
    draft = _sandbox_draft(doc, p)
    doc["draft"] = draft
    doc["approved_by"] = operator or open_id
    doc["approved_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        _save_store(doc)
    except Exception:
        pass
    extra = ("沙盒草案已生成（%s 回测台人工跑对比）。\n操作人：%s"
             % (draft["created"], operator or open_id or "管理台"))
    feedback_flow.record(req_id, "verified", note="草案就绪·bt2人工跑",
                         intent=p["intent"], scope=p["scope"])
    _send_card(_receipt_card(req_id, True, extra), to_group=False)
    feedback_flow.record(req_id, "receipt", note="通过回执已发")
    return {"ok": True, "req_id": req_id, "draft": draft,
            "scope": p["scope"], "code": p["code"]}


def reject(req_id, operator="", open_id="", reason=""):
    """驳回 → 账本 + 回执（原因必填——「驳回即回执」是闭环铁律）。"""
    import feedback_flow
    if open_id and not is_admin(open_id):
        return {"ok": False, "error": "无审批权限（tune_admins.json 白名单）"}
    doc, err = _pending_doc(req_id)
    if err:
        return {"ok": False, "error": err}
    reason = str(reason or "").strip() or "暂未说明（请补充）"
    feedback_flow.record(req_id, "rejected", operator=operator or open_id,
                         note=("驳回原因：" + reason)[:200])
    _send_card(_receipt_card(
        req_id, False, "原因：%s\n操作人：%s" % (reason, operator or "管理台")),
        to_group=False)
    feedback_flow.record(req_id, "receipt", note="驳回回执已发")
    return {"ok": True, "req_id": req_id}


def deploy(req_id, operator="", open_id="", params=None):
    """落地（独立动作，看完回测结果再点）：私有→VIP 空间写入；
    共享→仅挂账（共享模型变更必须另行实施工单，闸门不越权）。"""
    import feedback_flow
    if open_id and not is_admin(open_id):
        return {"ok": False, "error": "无审批权限（tune_admins.json 白名单）"}
    view = feedback_flow.requests_view().get(req_id)
    if not view:
        return {"ok": False, "error": "请求单不存在：%s" % req_id}
    if view["stage"] != "verified":
        return {"ok": False, "error":
                "状态 %s 不可落地（须先 approve 至 verified）" % view["stage"]}
    doc = _load_store(req_id) or {}
    scope = view.get("scope", "private")
    if scope == "private":
        pv = params or (doc.get("draft") or {}).get("bt2_params_hint") or {}
        _vip_write(view.get("customer", "?"), pv, req_id,
                   operator or open_id or "管理台")
        feedback_flow.record(req_id, "deployed", operator=operator or open_id,
                             note="VIP 私有参数空间已写入")
        return {"ok": True, "req_id": req_id, "scope": scope,
                "customer": view.get("customer")}
    feedback_flow.record(req_id, "pending_manual", operator=operator or open_id,
                         note="共享模型变更挂账——另行实施工单（闸门不越权）")
    return {"ok": True, "req_id": req_id, "scope": scope,
            "note": "共享变更已挂账，待另行实施"}


def list_requests(status=None, limit=50):
    """请求单列表（最新在前；status 过滤最新状态）。"""
    import feedback_flow
    view = feedback_flow.requests_view()
    rows = sorted(view.values(), key=lambda v: v.get("last_ts", ""),
                  reverse=True)
    if status:
        rows = [r for r in rows if r["stage"] == status]
    return rows[:limit]


if __name__ == "__main__":
    print(json.dumps(list_requests(), ensure_ascii=False, indent=1)[:2000])
