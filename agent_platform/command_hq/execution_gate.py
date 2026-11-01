# -*- coding: utf-8 -*-
"""HQ L0/L1 执行门（W-C4，K-ORG-20261101-HQEXEC）。

定位：指挥部五角色智能体的**安全门**——输入「共识判定 + 风险级别 + 证据 +
回滚计划 + 确认 + kill switch」，输出 ``allow``/``deny`` 判定与可审计理由。
本模块**只判定、不执行**：不写文件、不调子进程、不联网，返回值固定
``dry_run=True`` / ``executed=False``。真正的执行/回滚接线属 G5-C（另单）。

分级口径（复用 K-STOCK-416 §三，不另立标准）：
  - L0 自主：可逆、爆炸半径 ≤ 当前工作区（读文件/测试/文档/worktree 内小改）；
  - L1 升级：可逆但触及生产链路或跨会话共享状态（dev 小任务/本地提交等）→
    本门允许判定 allow（带证据/回滚/kill switch），执行后按 L1 通报；
  - L2 人工确认：不可逆或爆炸半径 ≥ 宿主机/生产/资金/上线/删除 → **一律 deny**，
    AI 只能出建议（审批由人类/approval_control 链路完成，本门不代批）。

规则（fail-closed，任何一项不满足即 deny；reason 机器可读）：
  1. ``risk_level`` ∈ {L0,L1,L2}；**L2 无条件 deny**（即使其余字段齐全）；
  2. 动作必须在白名单内（``ACTION_WHITELIST``），未知动作 deny；
  3. 请求 ``risk_level`` 必须等于白名单动作登记级别（禁止自我降级/错配）；
  4. 共识必须为 ``auto_ready`` 且无 P0 反对、quroum 达标（缺省 3/5）；
  5. 证据非空且**每条**有可复核 ref + sha256 digest + ``verified=true``；
  6. 回滚计划必须符合 ``hq_rollback_plan/v1``（至少 1 步，每步 command+verify）；
  7. kill switch 必须**已配置且未触发**（状态来自 env/file 或显式注入）；
  8. confirmation 若提供则须符合 schema；blast radius 若提供则不得超动作上限。

审计/留痕：本模块只返回判定与理由（含动作白名单规格、kill switch 状态、
证据计数、回滚步数、blast radius），不落盘——由调用方（编排器/执行接线单）
在 G5-C 落审计日志；本单不产生任何副作用。

已知边界（见工单回报）：
  - 不做真实执行/回滚/飞书/审批，不写任何账本；
  - kill switch 读的是**状态**（env/file），不负责创建/清理；
  - 白名单是静态最小集，扩面须评审（新增动作必须同时补测试）。
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

__all__ = [
    "ACTION_WHITELIST",
    "CONFIRMATION_SCHEMA",
    "DECISION_SCHEMA",
    "EVIDENCE_KINDS",
    "GATE_SCHEMA",
    "KILL_ENV_KEYS",
    "ROLLBACK_SCHEMA",
    "RISK_LEVELS",
    "evaluate",
    "kill_switch_state",
    "validate_blast_radius",
    "validate_confirmation",
    "validate_consensus",
    "validate_evidence",
    "validate_rollback",
]

GATE_SCHEMA = "hq_execution_gate/v1"
DECISION_SCHEMA = "hq_execution_decision/v1"
ROLLBACK_SCHEMA = "hq_rollback_plan/v1"
CONSENSUS_SCHEMA = "hq_consensus/v1"
CONFIRMATION_SCHEMA = "hq_execution_confirmation/v1"

RISK_LEVELS = ("L0", "L1", "L2")
ALLOW = "allow"
DENY = "deny"

# kill switch：env 任一为真值 或 约定文件存在 → 视为触发；两者都缺 → 未配置（deny）
KILL_ENV_KEYS = ("HQ_EXEC_KILL_SWITCH", "PAPER_KILL")
KILL_FILE_REL = Path("agent_platform") / "data" / "paper" / "KILL"
DEFAULT_KILL_FILE = Path(__file__).resolve().parents[1] / "data" / "paper" / "KILL"

REQUIRED_QUORUM = 3  # W-C3：L0/L1 auto_ready = 田丰+黄忠+关羽 3/5 且无 P0 反对
EVIDENCE_KINDS = ("file", "command", "test", "live", "hash")

# 白名单动作：只覆盖 docs / tests / read-only / dev 小任务（W-C4 验收口径）。
# max_files：请求 blast radius 的写入文件数上限；0 = 只读（不允许写文件）。
ACTION_WHITELIST = {
    "docs.edit": {
        "risk_level": "L0",
        "blast_radius": "docs/**（仓库文档，可 revert）",
        "max_files": 50,
    },
    "tests.run": {
        "risk_level": "L0",
        "blast_radius": "worktree 内只读跑测试（不写生产）",
        "max_files": 0,
    },
    "readonly.inspect": {
        "risk_level": "L0",
        "blast_radius": "仓库只读查询/审计（零写盘）",
        "max_files": 0,
    },
    "dev.small_task": {
        "risk_level": "L1",
        "blast_radius": "隔离 worktree 内小任务（dev task）",
        "max_files": 250,
    },
    "dev.local_commit": {
        "risk_level": "L1",
        "blast_radius": "worktree 分支本地提交（不 push/不 merge）",
        "max_files": 250,
    },
}
ALLOWED_ACTIONS = frozenset(ACTION_WHITELIST)

_DIGEST_RE = re.compile(r"^(sha256:)?[0-9a-f]{64}$")
_TS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?$")
_TRUE_WORDS = ("1", "on", "true", "yes", "kill", "engaged")


# ---------------------------------------------------------------- 基础
def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def _check(checks, name, ok, detail="", code="") -> None:
    checks.append({"name": name, "ok": bool(ok),
                   "code": "" if ok else (code or name),
                   "detail": str(detail or "")[:300]})


# ---------------------------------------------------------------- kill switch
def kill_switch_state(env=None, kill_file=None) -> dict:
    """读取 kill switch 状态（只读：env + 约定文件），不创建/不清理。

    返回 ``{"configured": bool, "active": bool, "sources": [...],
    "env_keys": [...], "file": "...", "file_exists": bool}``；
    ``configured=False`` 表示无法确认 kill switch 通道 → 调用方必须 deny。
    """
    env = os.environ if env is None else env
    path = Path(kill_file) if kill_file is not None else DEFAULT_KILL_FILE
    sources = []
    configured = False
    active = False
    for key in KILL_ENV_KEYS:
        if key in env:
            configured = True
            value = str(env.get(key) or "").strip().lower()
            sources.append("env:%s=%s" % (key, value or "<empty>"))
            if value in _TRUE_WORDS:
                active = True
    file_exists = path.is_file()
    if file_exists:
        configured = True
        sources.append("file:%s" % path)
        try:
            content = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            content = ""
        # 项目约定（paper_book）：KILL 文件存在即视为触发；显式 off 词除外
        if content.lower() not in ("0", "off", "false", "no", "disabled"):
            active = True
    return {"configured": configured, "active": active,
            "sources": sources, "env_keys": list(KILL_ENV_KEYS),
            "file": str(path), "file_exists": file_exists}


def _resolve_kill_switch(override, env, kill_file):
    """显式注入优先（纯函数测试/编排器传状态），否则读 env/file。"""
    if override is None:
        return kill_switch_state(env=env, kill_file=kill_file), ""
    if not isinstance(override, dict):
        return None, "kill_switch_schema_invalid"
    configured = override.get("configured")
    active = override.get("active")
    if not isinstance(configured, bool) or not isinstance(active, bool):
        return None, "kill_switch_schema_invalid"
    source = str(override.get("source") or "override")
    return {"configured": configured, "active": active,
            "sources": [source], "env_keys": [], "file": "",
            "file_exists": False}, ""


# ---------------------------------------------------------------- 校验器
def validate_consensus(consensus) -> dict:
    """共识判定校验（W-C3 口径）：auto_ready + 无 P0 反对 + quorum。"""
    if not isinstance(consensus, dict):
        return {"ok": False, "reason": "consensus_schema_invalid",
                "status": "", "p0_oppose": False}
    status = str(consensus.get("status") or "").strip()
    p0_oppose = consensus.get("p0_oppose") is True
    if status not in ("auto_ready", "escalated", "blocked"):
        return {"ok": False, "reason": "consensus_schema_invalid",
                "status": status, "p0_oppose": p0_oppose}
    if status != "auto_ready":
        return {"ok": False, "reason": "consensus_not_auto_ready",
                "status": status, "p0_oppose": p0_oppose}
    if p0_oppose:
        return {"ok": False, "reason": "consensus_p0_oppose",
                "status": status, "p0_oppose": True}
    agree = consensus.get("agree")
    if agree is not None:
        if isinstance(agree, bool) or not isinstance(agree, int):
            return {"ok": False, "reason": "consensus_schema_invalid",
                    "status": status, "p0_oppose": p0_oppose}
        if agree < REQUIRED_QUORUM:
            return {"ok": False, "reason": "consensus_quorum_unmet",
                    "status": status, "p0_oppose": p0_oppose}
    return {"ok": True, "reason": "", "status": status, "p0_oppose": False}


def validate_evidence(items) -> dict:
    """证据校验：非空、每条可复核 ref + sha256 digest + verified=true。"""
    if items is None or items == []:
        return {"ok": False, "reason": "no_evidence", "count": 0}
    if not isinstance(items, list):
        return {"ok": False, "reason": "evidence_schema_invalid", "count": 0}
    for item in items:
        if not isinstance(item, dict):
            return {"ok": False, "reason": "evidence_schema_invalid",
                    "count": 0}
        kind = str(item.get("kind") or "").strip()
        ref = str(item.get("ref") or "").strip()
        digest = str(item.get("digest") or "").strip()
        if kind not in EVIDENCE_KINDS or not ref or not _DIGEST_RE.match(digest):
            return {"ok": False, "reason": "evidence_schema_invalid",
                    "count": 0}
        if item.get("verified") is not True:
            # 计划 §3：无证据不得写 verified=true；未核验证据不算证据
            return {"ok": False, "reason": "evidence_unverified", "count": 0}
    return {"ok": True, "reason": "", "count": len(items)}


def validate_rollback(plan) -> dict:
    """回滚计划校验（``hq_rollback_plan/v1``）：至少 1 步且每步可执行/可验证。"""
    if plan is None or plan == {} or plan == []:
        return {"ok": False, "reason": "no_rollback", "steps": 0}
    if not isinstance(plan, dict):
        return {"ok": False, "reason": "rollback_schema_invalid", "steps": 0}
    if str(plan.get("schema") or "").strip() != ROLLBACK_SCHEMA:
        return {"ok": False, "reason": "rollback_schema_invalid", "steps": 0}
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return {"ok": False, "reason": "rollback_schema_invalid", "steps": 0}
    for step in steps:
        if not isinstance(step, dict):
            return {"ok": False, "reason": "rollback_schema_invalid",
                    "steps": 0}
        for key in ("command", "verify"):
            if not str(step.get(key) or "").strip():
                return {"ok": False, "reason": "rollback_schema_invalid",
                        "steps": 0}
    max_duration = plan.get("max_duration_s")
    if max_duration is not None:
        if (isinstance(max_duration, bool)
                or not isinstance(max_duration, int)
                or not 0 < max_duration <= 3600):
            return {"ok": False, "reason": "rollback_schema_invalid",
                    "steps": 0}
    owner = plan.get("owner")
    if owner is not None and not str(owner).strip():
        return {"ok": False, "reason": "rollback_schema_invalid", "steps": 0}
    return {"ok": True, "reason": "", "steps": len(steps)}


def validate_confirmation(confirmation) -> dict:
    """执行确认（可选）：若提供须含确认人与确认时间（ISO）。"""
    if confirmation is None:
        return {"ok": True, "reason": "", "present": False}
    if not isinstance(confirmation, dict):
        return {"ok": False, "reason": "confirmation_schema_invalid",
                "present": True}
    by = str(confirmation.get("by") or "").strip()
    at = str(confirmation.get("at") or "").strip()
    if not by or not at or not _TS_RE.match(at):
        return {"ok": False, "reason": "confirmation_schema_invalid",
                "present": True}
    return {"ok": True, "reason": "", "present": True}


def validate_blast_radius(blast_radius, action_spec=None) -> dict:
    """blast radius 校验：可选 dict{files:int, scope:str}，不得超动作上限。"""
    if blast_radius is None:
        return {"ok": True, "reason": "", "provided": False,
                "files": None, "scope": ""}
    if not isinstance(blast_radius, dict):
        return {"ok": False, "reason": "blast_radius_schema_invalid",
                "provided": True, "files": None, "scope": ""}
    files = blast_radius.get("files")
    scope = str(blast_radius.get("scope") or "").strip()
    if (isinstance(files, bool) or not isinstance(files, int) or files < 0):
        return {"ok": False, "reason": "blast_radius_schema_invalid",
                "provided": True, "files": None, "scope": scope}
    spec = action_spec or {}
    cap = spec.get("max_files")
    if isinstance(cap, int) and not isinstance(cap, bool) and files > cap:
        return {"ok": False, "reason": "blast_radius_exceeded",
                "provided": True, "files": files, "scope": scope}
    return {"ok": True, "reason": "", "provided": True,
            "files": files, "scope": scope}


# ---------------------------------------------------------------- 主判定
def evaluate(*, action, risk_level, consensus, evidence, rollback,
             confirmation=None, blast_radius=None, kill_switch=None,
             env=None, kill_file=None) -> dict:
    """执行门判定：只返回 allow/deny 与理由，**不执行任何动作**。

    参数：
      - ``action``：动作名，必须在 ``ACTION_WHITELIST`` 内；
      - ``risk_level``：请求分级 L0/L1/L2（L2 一律 deny）；
      - ``consensus``：共识判定 dict（W-C3 口径，至少 ``status``）；
      - ``evidence``：证据列表（kind/ref/digest/verified）；
      - ``rollback``：回滚计划（``hq_rollback_plan/v1``）；
      - ``confirmation``：可选执行确认（by/at）；
      - ``blast_radius``：可选 ``{"files": int, "scope": str}``；
      - ``kill_switch``：可选显式 kill switch 状态（默认读 env/file）；
      - ``env`` / ``kill_file``：测试/运维注入（默认 os.environ / 约定文件）。
    返回固定含 ``decision``（allow/deny）、``allow``、``reasons``、``checks``、
    ``dry_run=True``、``executed=False``；不写文件、不调子进程、不联网。
    """
    checks = []
    reasons = []

    def _record(name, result):
        _check(checks, name, result["ok"], result.get("reason", ""),
               result.get("reason", ""))
        if not result["ok"]:
            reasons.append({"code": result.get("reason") or name,
                            "detail": result.get("reason_detail", name)})

    action_text = str(action or "").strip()
    risk_text = str(risk_level or "").strip()
    action_spec = ACTION_WHITELIST.get(action_text)

    # 1) 入参基本 schema
    _check(checks, "action_schema", bool(action_text), action_text,
           "bad_action")
    if not action_text:
        reasons.append({"code": "bad_action", "detail": "action 必填"})
    risk_valid = risk_text in RISK_LEVELS
    _check(checks, "risk_level_valid", risk_valid, risk_text,
           "bad_risk_level")
    if not risk_valid:
        reasons.append({"code": "bad_risk_level",
                        "detail": "risk_level 须为 %s" % "/".join(RISK_LEVELS)})

    # 2) L2：无条件人工（即使其余字段齐全也 deny）
    l2 = risk_text == "L2"
    _check(checks, "l2_forced_human", not l2,
           "L2 不可逆/宿主机级动作必须人工定版，AI 只出建议",
           "l2_requires_human")
    if l2:
        reasons.append({"code": "l2_requires_human",
                        "detail": "L2 一律 deny（人工审批链路走 approval_control）"})

    # 3) 动作白名单 + 分级匹配（禁止自我降级）
    whitelisted = action_spec is not None
    _check(checks, "action_whitelisted", whitelisted, action_text,
           "action_not_whitelisted")
    if not whitelisted:
        reasons.append({"code": "action_not_whitelisted",
                        "detail": "未登记动作：%s（白名单：%s）"
                                  % (action_text or "-",
                                     ",".join(sorted(ALLOWED_ACTIONS)))})
    level_match = (whitelisted and risk_valid
                   and risk_text == action_spec["risk_level"])
    _check(checks, "risk_level_matches_action", level_match,
           "action=%s 登记=%s 请求=%s"
           % (action_text or "-",
              (action_spec or {}).get("risk_level", "-"), risk_text or "-"),
           "risk_level_mismatch")
    if whitelisted and risk_valid and not level_match:
        reasons.append({"code": "risk_level_mismatch",
                        "detail": "动作登记级别 %s ≠ 请求 %s（禁止自我降级/错配）"
                                  % (action_spec["risk_level"], risk_text)})

    # 4) 共识
    consensus_result = validate_consensus(consensus)
    _check(checks, "consensus_auto_ready", consensus_result["ok"],
           consensus_result.get("reason", ""),
           consensus_result.get("reason") or "consensus_not_ready")
    if not consensus_result["ok"]:
        reasons.append({"code": consensus_result["reason"],
                        "detail": "共识未达 auto_ready/无 P0 反对/quorum"})

    # 5) 证据
    evidence_result = validate_evidence(evidence)
    _check(checks, "evidence_verified", evidence_result["ok"],
           "%d 条" % evidence_result.get("count", 0),
           evidence_result.get("reason") or "evidence_invalid")
    if not evidence_result["ok"]:
        reasons.append({"code": evidence_result["reason"],
                        "detail": "证据缺失或不可复核（须 ref+sha256+verified）"})

    # 6) 回滚
    rollback_result = validate_rollback(rollback)
    _check(checks, "rollback_ready", rollback_result["ok"],
           "%d 步" % rollback_result.get("steps", 0),
           rollback_result.get("reason") or "rollback_invalid")
    if not rollback_result["ok"]:
        reasons.append({"code": rollback_result["reason"],
                        "detail": "回滚计划缺失或不符合 %s" % ROLLBACK_SCHEMA})

    # 7) kill switch（未配置 或 已触发 都 deny）
    kill_state, kill_error = _resolve_kill_switch(kill_switch, env, kill_file)
    if kill_error:
        _check(checks, "kill_switch_ready", False, kill_error, kill_error)
        reasons.append({"code": kill_error,
                        "detail": "kill switch 状态注入格式非法"})
    else:
        kill_ok = bool(kill_state.get("configured")) and not kill_state.get("active")
        code = ""
        if not kill_state.get("configured"):
            code = "kill_switch_unconfigured"
        elif kill_state.get("active"):
            code = "kill_switch_active"
        _check(checks, "kill_switch_ready", kill_ok,
               "configured=%s active=%s" % (kill_state.get("configured"),
                                            kill_state.get("active")), code)
        if not kill_ok:
            reasons.append({"code": code,
                            "detail": "kill switch %s（未配置=无法确认停机通道；"
                                      "active=已触发停机）" % code})

    # 8) confirmation（可选但提供即校验）
    confirmation_result = validate_confirmation(confirmation)
    _check(checks, "confirmation_valid", confirmation_result["ok"],
           "present=%s" % confirmation_result.get("present"),
           confirmation_result.get("reason") or "confirmation_invalid")
    if not confirmation_result["ok"]:
        reasons.append({"code": confirmation_result["reason"],
                        "detail": "confirmation 需含 by/at（ISO 时间）"})

    # 9) blast radius（可选但提供即校验，不得超动作上限）
    blast_result = validate_blast_radius(blast_radius, action_spec)
    _check(checks, "blast_radius_within_limit", blast_result["ok"],
           "files=%s scope=%s" % (blast_result.get("files"),
                                  blast_result.get("scope") or "-"),
           blast_result.get("reason") or "blast_radius_invalid")
    if not blast_result["ok"]:
        reasons.append({"code": blast_result["reason"],
                        "detail": "blast radius 非法或超过动作登记上限"})

    allow = not reasons
    return {
        "schema": DECISION_SCHEMA,
        "gate_schema": GATE_SCHEMA,
        "decision": ALLOW if allow else DENY,
        "allow": allow,
        "reasons": reasons,
        "checks": checks,
        "action": action_text,
        "action_spec": dict(action_spec) if action_spec else {},
        "risk_level": risk_text,
        "consensus_status": consensus_result.get("status", ""),
        "evidence_count": evidence_result.get("count", 0),
        "rollback_steps": rollback_result.get("steps", 0),
        "confirmation_present": bool(confirmation_result.get("present")),
        "blast_radius": {"provided": blast_result.get("provided", False),
                         "files": blast_result.get("files"),
                         "scope": blast_result.get("scope", "")},
        "kill_switch": kill_state or {"configured": False, "active": False},
        # 红线：本门不执行、不写盘、不联网；执行器接线属 G5-C
        "dry_run": True,
        "executed": False,
        "evaluated_at": _now_iso(),
    }
