# -*- coding: utf-8 -*-
"""任务感知模型分配：任务/角色 → 本地 / 云端 / GPT-6。

规则（config/model_allocation.json 可改）：
- 明确任务类型优先（simple→local，reasoning/coding/audit→strong）
- 无任务时按角色默认（A0/ZHAOYUN/AI1/GUANYU→strong，其余 auto）
- 再按消息启发式（长文/推理关键词→strong，翻译/摘要→local）
- 最终 auto：平台中转站 → 强模型 → 本地兜底
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
CONFIG = REPO / "config" / "model_allocation.json"

DEFAULT = {
    "default_tier": "auto",
    "tasks": {"simple": "local", "summarize": "local", "format": "local",
              "classify": "local", "translate": "local",
              "reasoning": "strong", "coding": "strong", "code_review": "strong",
              "audit": "strong", "planning": "strong", "decision": "strong",
              "quality": "strong", "judge": "strong", "research": "auto",
              "tool": "auto", "chat": "auto", "default": "auto"},
    "roles": {"A0": "strong", "ZHAOYUN": "strong", "AI1": "strong",
              "GUANYU": "strong", "default": "auto"},
    "heuristics": {
        "strong_keywords": ["推理", "架构", "审计", "代码", "决策", "规划", "证明", "为什么", "设计"],
        "local_keywords": ["翻译", "摘要", "格式化", "分类", "提取", "改写"],
    },
}


def load_config() -> dict:
    try:
        d = json.loads(CONFIG.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return json.loads(json.dumps(DEFAULT))


def _text(messages) -> str:
    out = []
    for m in messages or []:
        if isinstance(m, dict) and isinstance(m.get("content"), str):
            out.append(m["content"])
    return "\n".join(out)


def classify(agent: str = "", task: str = "", messages=None) -> tuple:
    cfg = load_config()
    tasks = {str(k).lower(): str(v) for k, v in (cfg.get("tasks") or {}).items()}
    roles = {str(k).upper(): str(v) for k, v in (cfg.get("roles") or {}).items()}
    heur = cfg.get("heuristics") or {}
    t = str(task or "").strip().lower()
    if t and t in tasks:
        return tasks[t], "task:%s" % t
    role = str(agent or "").strip().upper()
    if role and role in roles:
        return roles[role], "role:%s" % role
    text = _text(messages)
    if len(text) > 6000:
        return "strong", "heuristic:long"
    for kw in heur.get("strong_keywords") or []:
        if kw and kw in text:
            return "strong", "heuristic:keyword=%s" % kw
    if text:
        for kw in heur.get("local_keywords") or []:
            if kw and kw in text:
                return "local", "heuristic:keyword=%s" % kw
    return str(cfg.get("default_tier") or "auto"), "default"


def tier_route(tier: str) -> dict:
    from legion_command import seat_router
    cfg = seat_router.load_config()
    aliases = cfg.get("aliases") or {}
    tier = str(tier or "auto")
    if tier == "local":
        return {"tier": "local", "seats": [], "allow_local": True}
    if tier == "gpt6" or tier == "gpt-6-sol":
        return {"tier": "gpt6", "seats": ["relay"], "allow_local": False}
    spec = aliases.get(tier if tier in aliases else "auto") or {}
    return {"tier": tier, "seats": list(spec.get("seats") or []),
            "allow_local": bool(spec.get("allow_local", True))}


def allocate(agent: str = "", task: str = "", messages=None,
             requested: str = "") -> dict:
    req = str(requested or "").strip()
    if req and req not in ("auto", ""):
        if req in ("gpt6", "gpt-6-sol", "local", "strong"):
            route = tier_route("gpt6" if req in ("gpt6", "gpt-6-sol") else req)
            route.update({"agent": agent, "task": task, "reason": "requested:%s" % req})
            return route
    tier, reason = classify(agent, task, messages)
    route = tier_route(tier)
    route.update({"agent": agent, "task": task, "reason": reason})
    return route


def explain(agent: str = "", task: str = "", messages=None) -> dict:
    return allocate(agent, task, messages)
