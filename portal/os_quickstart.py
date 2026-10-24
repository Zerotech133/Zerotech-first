# -*- coding: utf-8 -*-
"""Quickstart：一句话 → 一张卡 → 确认即执行（首版主交互）

目标：把用户动作压到 ≤3 句、≤2 分钟，同时保留同一本账的全部证据。
- 用户给一句话 → 系统出"一张卡"（目标/边界/成功标准/推荐/舍弃/风险/默认授权）；
- 用户回"按这个办"或"改成…" → **一次确认**后系统自动串起：建目标/事项→冻结需求→出卡→选卡→签授权→工单→模拟认领→隔离执行→自检→自动打开试用；
- 用户第 3 句：说"接受"或"这里改"（复用既有 verdict/rework 端点）。

诚实边界（G1 延续）：
- 推荐卡为 rule_assisted（规则辅助，非模型结论）；
- 认领只能 simulated 且带"隔离验证/未获真实认领"标注；
- 默认授权仅隔离 4 动作 + 24h + 可撤回；越权/不可逆仍阻断；
- 确认后自动打开试用会记 trial.opened（actor=user，附注"确认后自动打开试用"），不冒充独立验收。
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

try:
    import os_goal_contract as C
    import os_goal_store as S
    import os_software_flow as F
except ImportError:  # pragma: no cover
    from . import os_goal_contract as C  # type: ignore
    from . import os_goal_store as S  # type: ignore
    from . import os_software_flow as F  # type: ignore

BEIJING = timezone(timedelta(hours=8))
DEFAULT_ACTIONS = ["prototype.generate", "prototype.preview",
                   "prototype.test", "workspace.cleanup"]
DEFAULT_ENV = "isolated"
DEFAULT_VALID_HOURS = 24
DEFAULT_GOAL = "把想法变成可验收成果（自动建立的事业目标）"
ISOLATION_LABEL = "隔离验证/未获真实认领"

_DRAFTS: Dict[str, Dict[str, Any]] = {}
_SEQ = [0]


def _now() -> str:
    return datetime.now(BEIJING).isoformat(timespec="seconds")


def _valid_until() -> str:
    return (datetime.now(BEIJING) + timedelta(hours=DEFAULT_VALID_HOURS)).isoformat(
        timespec="seconds")


_PROC_TAG = "%05d" % os.getpid()


def _next_id() -> str:
    _SEQ[0] += 1
    # 2026-10-24 QUICKFIX3/4: 进程重启后 _SEQ 归零会复用旧草稿 ID，导致确认时
    # 幂等键撞上账本旧事件（duplicate_idempotency_key）。用进程 PID 保证跨重启唯一。
    return "QK-%s-%s-%s-%03d" % (time.strftime("%Y%m%d"), time.strftime("%H%M%S"),
                                 _PROC_TAG, _SEQ[0])

def build_card(text: str, edits: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """一句话 → 一张卡（纯规则草案；不写账、不执行）。"""
    text = str(text or "").strip()[:500]
    if not text:
        raise C.ContractError("说一句话，我才好出卡")
    edits = edits or {}
    changes = str(edits.get("changes") or "").strip()
    goals = ["（原话）" + text]
    if changes:
        goals.append("（按你的修改）" + changes)
    criteria = [
        {"id": "SC1", "statement": "成果能打开、能操作，符合这句话的要求",
         "verify_method": "本人打开试用"},
        {"id": "SC2", "statement": "我说“这里改”后能生成新版本",
         "verify_method": "返工后对比新版本"},
    ]
    acceptance = changes or "本人打开试用：说“接受”或“这里改”"
    card = {
        "draft_id": _next_id(),
        "text": text,
        "title": text[:40],
        "goals": goals,
        "non_goals": ["不接生产数据", "不对外发布", "不做真实交易"],
        "success_criteria": criteria,
        "recommended": {"option": "A｜隔离单页原型（按这句话自动生成）",
                        "why": "可逆、能立刻打开试用、失败可撤回"},
        "discarded": [
            {"option": "B｜直接改现网", "why": "不可逆，首版不做"},
            {"option": "C｜只出文档", "why": "不可试用，无法验收"},
        ],
        "risks": ["产物是隔离样本，不代表生产可用",
                  "推荐卡为规则辅助，非模型结论"],
        "acceptance_method": acceptance,
        "scope": {"actions": list(DEFAULT_ACTIONS), "env": DEFAULT_ENV,
                  "valid_until": _valid_until(), "reversible": True,
                  "note": "默认授权：隔离 4 动作 / 24h / 可撤回；越权或不可逆才打断本人"},
        "honesty": {"card_source": "rule_assisted", "claim": "simulated",
                    "note": "规则辅助 + 模拟认领；不接真实消费者/交易/外发"},
        "created_at": _now(),
    }
    if edits.get("extra_goal"):
        card["goals"].append(str(edits["extra_goal"])[:200])
    return card


def create_draft(text: str, edits: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    card = build_card(text, edits)
    _DRAFTS[card["draft_id"]] = card
    return card


def get_draft(draft_id: str) -> Dict[str, Any]:
    card = _DRAFTS.get(str(draft_id))
    if card is None:
        raise KeyError("草稿不存在或已过期：%r" % draft_id)
    return card


def _ensure_default_goal(store: S.OsGoalStore) -> str:
    for g in store.list_goals():
        if g.get("statement") == DEFAULT_GOAL:
            return g["goal_id"]
    ev = store.create_goal(
        DEFAULT_GOAL, "老田（本人）",
        [{"id": "A1", "text": "用户希望少操作、多结果", "status": "hypothesis"}],
        ["3 句话内完成一次想法→成果→验收"],
        idem="QUICK-DEFAULT-GOAL")
    return ev["payload"]["goal_id"]


def confirm(draft_id: str, store: S.OsGoalStore,
            edits: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """一次确认 → 自动执行到"试用就绪"。幂等：同一 draft 重复调用返回同一结果。"""
    card = get_draft(draft_id)
    if card.get("result"):
        return card["result"]
    if edits:
        merged = dict(card)
        if edits.get("changes"):
            merged["goals"] = card["goals"] + ["（按你的修改）" + str(edits["changes"])[:200]]
            merged["acceptance_method"] = str(edits["changes"])[:200]
        if edits.get("extra_goal"):
            merged["goals"].append(str(edits["extra_goal"])[:200])
        card.update(merged)
    goal_id = _ensure_default_goal(store)
    item_ev = store.create_item(
        goal_id, card["title"] or card["text"][:40],
        "用户一句话提出（quickstart %s）" % card["draft_id"],
        "software", attempt_no=1, idem="QUICK-ITEM-%s" % card["draft_id"])
    iid = item_ev["payload"]["item_id"]
    F.freeze_brief(store, iid, card["goals"], card["success_criteria"],
                   non_goals=card["non_goals"],
                   constraints=["隔离环境", "不碰生产/交易/外发"],
                   idem="QUICK-BRIEF-%s" % card["draft_id"])
    F.freeze_card(store, iid, card["recommended"], card["discarded"],
                  card["acceptance_method"], risks=card["risks"],
                  evidence_refs=["quickstart:%s" % card["draft_id"]],
                  card_version="c1", idem="QUICK-CARD-%s" % card["draft_id"])
    F.select_card(store, iid, "c1", idem="QUICK-SEL-%s" % card["draft_id"])
    F.sign_grant(store, iid, "c1", list(DEFAULT_ACTIONS),
                 valid_until=card["scope"]["valid_until"],
                 idem="QUICK-GRANT-%s" % card["draft_id"])
    F.create_workorder(store, iid, idem="QUICK-WO-%s" % card["draft_id"])
    F.claim(store, iid, method="simulated", isolation_label=ISOLATION_LABEL,
            idem="QUICK-CLAIM-%s" % card["draft_id"])
    result = F.execute(store, iid, idem="QUICK-EXEC-%s" % card["draft_id"])
    artifact = result["artifact"]
    # 确认即执行 = 用户点了确认，前端随后自动打开产物；trial.opened 如实附注来源
    store.append_event(
        "trial.opened", {"item_id": iid},
        {"artifact_id": artifact["artifact_id"], "opened_by": "laotian",
         "note": "确认后自动打开试用（quickstart 一次确认）"},
        actor={"kind": "user", "id": "laotian", "seat": "laotian"})
    chain = F.chain_report(store, iid)
    out = {
        "draft_id": card["draft_id"],
        "goal_id": goal_id,
        "item_id": iid,
        "artifact": artifact,
        "test": result["test"],
        "preview_url": "/api/os/item/%s/workspace/index.html" % iid,
        "chain": {"verdict": chain["verdict"], "exit_code": chain["exit_code"],
                  "gates": {k: v["status"] for k, v in chain["gates"].items()}},
        "card": {"goals": card["goals"], "scope": card["scope"],
                 "acceptance_method": card["acceptance_method"]},
        "next": "本人打开试用：说“接受”或“这里改”",
    }
    card["result"] = out
    _DRAFTS[card["draft_id"]] = card
    return out


def discard_draft(draft_id: str) -> bool:
    return _DRAFTS.pop(str(draft_id), None) is not None
