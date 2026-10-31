# -*- coding: utf-8 -*-
"""S2 审批卡回调处理（D2-D3，2026-10-30 WO-SUPERVISOR）。

纯函数：解析飞书 card.action.trigger payload → 校验操作人/动作 → 幂等写
`approval` JSONL。**不接 live 事件服务**（接线阶段再挂）；默认只返回结果。

payload 关键结构（飞书卡片回调）：
{"operator":{"open_id":"ou_xxx"},"action":{"value":{"tag":"approval:approve",
  "task":"WO-...","level":"S2","evidence_hash":"..."}}}

2026-10-31 WO-L4-M4-HARDEN-20261031（7d P0，M4 三方签字）：
新增 `evaluate_three_party_signoff()` 最小机制——同一 task 的 approve 记录须
有 **3 个 distinct operator** 且角色齐全（默认 QA1/GUANYU/VERIFY1，角色映射
由调用方注入，**不自定组织政策**）；被 annotate 以 `refs` 引用过的试点点击
不计签。

2026-10-31 WO-D1-B123-20261031（D1 证据链 P0 / B2，M4 多方签字）：
`required_signers` 取代硬编码三方——默认 M4 正式验收四方
``QA1/GUANYU/VERIFY1/A0``（调用方可显式传参覆盖为旧三方/任意子集）。
`handle_action` 返回值新增签字进度：``signoff_state`` / ``signoff.state`` =
``incomplete``（未齐）| ``complete``（齐），``signoff.signed_roles`` 为「已签」
名单；**单签绝不返回 complete、绝不写「通过」**（台账只记签名行，不写裁决
字段；``approved`` 仅在 complete 时为 True）。幂等/越权/自审防护不变：重复
点击只算 1 签、未授权操作人拒收、owner/owner_ids 自审拒收（自签不计签）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "agent_platform" / "data" / "approvals.jsonl"
TAGS = {"approval:approve": "approve", "approval:reject": "reject",
        "approval:escalate": "escalate"}
# M4 正式验收四方签字（2026-10-31 D1 证据链 B2）：QA1 / GUANYU / VERIFY1 / A0·老田。
# 本模块**不自定组织政策**：默认值仅反映 M4 验收口径，调用方可用
# ``required_signers`` 显式覆盖；operator → 角色映射仍由调用方注入 ``role_of``，
# 映射缺失/未命中时状态保持 incomplete（roles_unresolved），绝不猜。
REQUIRED_SIGNERS = ("QA1", "GUANYU", "VERIFY1", "A0")
# 兼容旧名（2026-10-31 WO-L4-M4-HARDEN 的三方灰度机制）。
THREE_PARTY_ROLES = ("QA1", "GUANYU", "VERIFY1")
MIN_THREE_PARTY_SIGNERS = 3

# ----------------------------------------------------------------------
# M4 强制开关（2026-10-31 WO-M4-ENFORCE-PROBE-20261031）
# 背景：`_attach_signoff` 目前只 warn（灰度）。这里加"可配置强制"：
#   开关真源优先级（高→低）：env ``M4_SIGNOFF_ENFORCE`` > 策略文件
#   ``agent_platform/config/l4_m4_policy.json`` 的 ``m4_enforce`` >
#   ``enforce_after`` 日期（仅当 m4_enforce 缺省时生效）> 默认灰度(False)。
# 强制开启且 approve 的 signoff 未齐 → 本次 handle_action 返回 ok=False /
# error=m4_signoff_enforce_incomplete（审批行仍落账，签名可继续累积）；
# reject/escalate 不受强制影响（否则无法否决）。默认灰度 = 不阻断存量。
# ----------------------------------------------------------------------
POLICY_PATH = ROOT / "agent_platform" / "config" / "l4_m4_policy.json"
POLICY_ENV = "M4_SIGNOFF_ENFORCE"
POLICY_ENFORCE_KEY = "m4_enforce"
POLICY_AFTER_KEY = "enforce_after"
_TRUTHY = {"1", "true", "yes", "on", "enforce", "enabled"}
_FALSY = {"0", "false", "no", "off", "gray", "warn", "disabled"}
_POLICY_CACHE = {"mtime": None, "data": None}
_POLICY_CACHE_LOCK = threading.Lock()


def _read_policy_file(path=None):
    """读策略 JSON（缺失/损坏一律回退 {}，绝不因配置坏而阻断审批）。"""
    p = Path(path or POLICY_PATH)
    try:
        st = p.stat()
        with _POLICY_CACHE_LOCK:
            if _POLICY_CACHE["mtime"] == st.st_mtime and _POLICY_CACHE["data"] is not None:
                return dict(_POLICY_CACHE["data"])
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
        with _POLICY_CACHE_LOCK:
            _POLICY_CACHE["mtime"] = st.st_mtime
            _POLICY_CACHE["data"] = dict(data)
        return data
    except (OSError, ValueError, TypeError) as exc:
        log.debug("m4 policy unavailable(%r): %s", p, exc)
        return {}


def _flag(value):
    """归一三态：True/False/None（未设置或无法识别）。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in _TRUTHY:
        return True
    if text in _FALSY:
        return False
    return None


def _parse_day(text):
    """``YYYY-MM-DD`` → date；坏值返回 None（保守：不自动启用）。"""
    try:
        return time.strptime(str(text).strip(), "%Y-%m-%d")[:3]
    except (ValueError, TypeError):
        return None


def _enforce_after_due(policy, today=None):
    """enforce_after 是否已到（仅 m4_enforce 缺省时作为自动生效日期）。"""
    horizon = _parse_day((policy or {}).get(POLICY_AFTER_KEY))
    if horizon is None:
        return False
    now = today or time.localtime()
    return time.mktime(now[:3] + (0, 0, 0, 0, 0, -1)) >= time.mktime(
        horizon + (0, 0, 0, 0, 0, -1))


def m4_enforce_state(policy=None, env=None, today=None):
    """解析 M4 强制开关，返回 ``{enabled, source, policy}``（纯函数可注入）。

    source 取值：``env`` / ``policy`` / ``enforce_after`` / ``default_gray``。
    ``policy=False`` 表示显式空策略（用于"配置缺失回退灰度"测试）。
    """
    if policy is None:
        policy = _read_policy_file()
    elif not isinstance(policy, dict):
        policy = {}
    env_value = (env if env is not None else os.environ).get(POLICY_ENV)
    env_flag = _flag(env_value)
    if env_flag is not None:
        return {"enabled": env_flag, "source": "env", "policy": dict(policy)}
    pol_flag = _flag(policy.get(POLICY_ENFORCE_KEY))
    if pol_flag is not None:
        return {"enabled": pol_flag, "source": "policy", "policy": dict(policy)}
    if _enforce_after_due(policy, today=today):
        return {"enabled": True, "source": "enforce_after", "policy": dict(policy)}
    return {"enabled": False, "source": "default_gray", "policy": dict(policy)}


def _enforce_result(result, enforce):
    """强制开启且 signoff 未齐 → 明确拒绝（审批行已落账，签名可继续累积）。"""
    if not enforce.get("enabled") or result.get("action") != "approve":
        return result
    signoff = result.get("signoff") or {}
    if signoff.get("state") == "complete" or result.get("approved") is True:
        result["enforce"] = {"enabled": True, "source": enforce.get("source"),
                             "blocked": False}
        return result
    result["ok"] = False
    result["approved"] = False
    result["error"] = "m4_signoff_enforce_incomplete"
    result["enforce"] = {
        "enabled": True, "source": enforce.get("source"), "blocked": True,
        "reason": str(signoff.get("reason") or ""),
        "missing_signers": list(signoff.get("missing_signers") or []),
        "hint": "M4 强制开关已开启：四方签字未齐；本次 approve 拒绝。"
                "修法：补齐 QA1/GUANYU/VERIFY1/A0 签字，或把 "
                "agent_platform/config/l4_m4_policy.json 的 m4_enforce 改回 false"
                "（env M4_SIGNOFF_ENFORCE=0 亦可临时降级）。",
    }
    result.setdefault("warnings", []).append(
        "m4_signoff_enforced_block:" + str(signoff.get("reason") or ""))
    log.warning("approval m4 enforce block task=%r reason=%s",
                result.get("task"), signoff.get("reason"))
    return result


def _read_jsonl(path):
    rows = []
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                pass
    except OSError:
        pass
    return rows


_APPEND_LOCK = threading.Lock()


def _append(path, rec):
    """O_APPEND 单次写，避免多进程/线程交错半行。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
    with _APPEND_LOCK:
        fd = os.open(str(p), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)


def _role_resolver(role_of):
    """把 role_of（mapping 或 callable）归一为 operator → role 的解析器。"""
    if role_of is None:
        return None
    if callable(role_of):
        return role_of
    mapping = {str(k): str(v) for k, v in dict(role_of).items()}
    return mapping.get


def _normalise_required(required_signers):
    """required_signers → 去空白/空项的 list（保持调用方顺序）。"""
    if required_signers is None:
        required_signers = REQUIRED_SIGNERS
    if isinstance(required_signers, (str, bytes)):
        required_signers = [required_signers]
    return [str(item).strip() for item in required_signers if str(item).strip()]


def evaluate_signoff(rows, task, required_signers=None, role_of=None,
                     min_signers=None):
    """M4 多方签字判定（纯函数、只读；不自定组织政策）。

    台账口径：
    - 只认 ``action == "approve"``；reject/escalate/annotate 不算签字；
    - 同一 operator 重复点击只算 1 签（幂等）；须覆盖全部
      ``required_signers``（默认 M4 四方 ``REQUIRED_SIGNERS``），且
      distinct operator 数 ≥ ``min_signers``（缺省 = required 个数）；
    - 被 ``annotate`` 的 ``refs`` 引用过的 ts 视为「试点/已排除」，不计签；
    - 角色齐：``role_of``（{open_id: role} 或 callable）须覆盖
      ``required_signers`` 各一个；映射未提供/未命中 → ``roles_unresolved``
      （不算通过——不自定谁是谁）。

    返回 ``{ok, state, task, distinct_signers, signed, signed_roles, signers,
    required_signers, missing_signers, excluded_ts, reason}`` 及旧键名别名
    （``required_roles``/``missing_roles``）。``state`` 只有
    ``complete``/``incomplete``；``reason`` 为空串才算 complete。
    """
    task = str(task or "").strip()
    rows = list(rows or [])
    required = _normalise_required(required_signers)
    min_signers = len(required) if min_signers is None else int(min_signers)
    excluded = set()
    for row in rows:
        if row.get("action") != "annotate":
            continue
        refs = row.get("refs")
        if isinstance(refs, list):
            excluded.update(str(item) for item in refs)
    resolver = _role_resolver(role_of)
    signers, seen, roles_seen = [], set(), {}
    unresolved = False
    for row in rows:
        if str(row.get("task") or "") != task or row.get("action") != "approve":
            continue
        if str(row.get("ts") or "") in excluded:
            continue
        operator = str(row.get("operator") or "").strip()
        if not operator or operator in seen:
            continue
        seen.add(operator)
        role = ""
        if resolver is not None:
            try:
                role = str(resolver(operator) or "").strip().upper()
            except Exception:  # noqa: BLE001 校验不得因映射异常中断
                role = ""
        if role:
            roles_seen.setdefault(role, operator)
        else:
            unresolved = True
        signers.append({"operator": operator, "role": role})
    missing_signers = [item for item in required if item not in roles_seen]
    if len(signers) < min_signers:
        reason = "distinct_signers_lt_%d(%d)" % (min_signers, len(signers))
    elif resolver is None or unresolved:
        reason = "roles_unresolved"
    elif missing_signers:
        reason = "missing_signers:" + ",".join(missing_signers)
    else:
        reason = ""
    state = "complete" if reason == "" else "incomplete"
    signed_roles = [item for item in required if item in roles_seen]
    return {"ok": state == "complete", "state": state, "task": task,
            "distinct_signers": len(signers), "signed": bool(signers),
            "signed_roles": signed_roles, "signers": signers,
            "required_signers": list(required),
            "missing_signers": list(missing_signers),
            "excluded_ts": sorted(excluded), "reason": reason,
            # 旧键名兼容（2026-10-31 WO-L4-M4-HARDEN 消费方）
            "required_roles": list(required),
            "missing_roles": list(missing_signers)}


def evaluate_three_party_signoff(rows, task, required_roles=THREE_PARTY_ROLES,
                                 role_of=None, min_signers=MIN_THREE_PARTY_SIGNERS):
    """兼容入口（2026-10-31 WO-L4-M4-HARDEN 的三方灰度契约）。

    旧键名与旧 ``reason`` 文案（``missing_roles:``、
    ``distinct_signers_lt_3``）保持不变；新逻辑见 ``evaluate_signoff``。
    """
    result = evaluate_signoff(rows, task, required_signers=required_roles,
                              role_of=role_of, min_signers=min_signers)
    reason = str(result.get("reason") or "")
    if reason.startswith("missing_signers:"):
        reason = "missing_roles:" + reason[len("missing_signers:"):]
    result["reason"] = reason
    result["ok"] = reason == ""
    result["state"] = "complete" if result["ok"] else "incomplete"
    return result


def _attach_signoff(result, ledger, task, signoff_roles, required_signers,
                    min_signers):
    """挂载多方签字进度（不改审批主流程 ok/记录语义，不写「通过」）。"""
    try:
        signoff = evaluate_signoff(
            _read_jsonl(ledger), task, required_signers=required_signers,
            role_of=signoff_roles, min_signers=min_signers)
    except Exception as exc:  # noqa: BLE001 校验自身异常不得影响审批
        required = _normalise_required(required_signers)
        signoff = {"ok": False, "state": "incomplete", "task": task,
                   "distinct_signers": 0, "signed": False, "signed_roles": [],
                   "signers": [], "required_signers": required,
                   "missing_signers": list(required), "excluded_ts": [],
                   "reason": "signoff_eval_error:%s" % exc,
                   "required_roles": required,
                   "missing_roles": list(required)}
    result["signoff"] = signoff
    result["signoff_state"] = signoff["state"]
    result["signed"] = bool(signoff.get("signed"))
    # 只有要求签集合全部到齐才可能 approved=True；单签/缺签恒 False。
    result["approved"] = signoff["state"] == "complete"
    if signoff["state"] != "complete":
        result.setdefault("warnings", []).append(
            "m4_signoff_incomplete:" + str(signoff["reason"]))
        log.warning("approval m4 signoff incomplete task=%r reason=%s"
                    "（多签未齐，不写通过）", task, signoff["reason"])
    return result


def handle_action(payload, ledger=None, allowed_ops=None, owner=None,
                  owner_ids=None, now=None, signoff_roles=None,
                  required_signers=None, signoff_required_roles=None,
                  signoff_min_signers=None, m4_policy=None, m4_env=None,
                  m4_today=None):
    """返回 {ok, action, task, error?, duplicate?, signoff?, signoff_state?,
    signed, approved, warnings?, enforce?}。

    ``ok`` 仍表示「本次动作已记录」（向后兼容审批主流程）；签字是否齐全看
    ``signoff_state`` / ``approved``——单签恒为 ``incomplete`` / False，且台账
    不写「通过」裁决。

    2026-10-31 WO-D1-B123-20261031（B2）：``required_signers`` 为要求签字的
    角色集合（默认 M4 四方 QA1/GUANYU/VERIFY1/A0，可由调用方覆盖）；兼容旧
    参数名 ``signoff_required_roles`` / ``signoff_min_signers``。

    2026-10-31 WO-M4-ENFORCE-PROBE-20261031：``m4_enforce_state()`` 为真时，
    approve 且 signoff 未齐 → ``ok=False`` / ``error=m4_signoff_enforce_incomplete``
    （台账行仍写入，签名可继续累积；reject/escalate 不受影响）。默认灰度。
    测试/调用方可注入 ``m4_policy`` / ``m4_env`` / ``m4_today``。
    """
    ledger = Path(ledger or LEDGER)
    now = now or time.time()
    enforce = m4_enforce_state(policy=m4_policy, env=m4_env, today=m4_today)
    if required_signers is None:
        required_signers = signoff_required_roles
    required = _normalise_required(required_signers)
    min_signers = (len(required) if signoff_min_signers is None
                   else int(signoff_min_signers))
    op = str(((payload or {}).get("operator") or {}).get("open_id") or "")
    val = ((payload or {}).get("action") or {}).get("value") or {}
    tag = str(val.get("tag") or "")
    action = TAGS.get(tag)
    task = str(val.get("task") or "").strip()
    if not op:
        return {"ok": False, "error": "missing_operator"}
    if allowed_ops is not None and op not in set(allowed_ops):
        return {"ok": False, "error": "operator_not_allowed"}
    if not action:
        return {"ok": False, "error": "unknown_action"}
    if not task:
        return {"ok": False, "error": "missing_task"}
    owners = {str(x) for x in (owner_ids or []) if str(x)}
    if owner:
        owners.add(str(owner))
    if owners and op in owners:  # 自审防护：owner/owner_ids 用 open_id
        return {"ok": False, "error": "self_approval_blocked"}
    rows = _read_jsonl(ledger)
    for r in rows:
        if r.get("task") == task and r.get("action") == action and r.get("operator") == op:
            return _enforce_result(_attach_signoff(
                {"ok": True, "action": action, "task": task, "duplicate": True},
                ledger, task, signoff_roles, required, min_signers), enforce)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
           "schema": "approval/v1", "task": task, "action": action,
           "operator": op, "level": str(val.get("level") or "S2"),
           "evidence_hash": str(val.get("evidence_hash") or ""),
           "source": "feishu_card", "revocable": True}
    _append(ledger, rec)
    return _enforce_result(_attach_signoff(
        {"ok": True, "action": action, "task": task, "record": rec},
        ledger, task, signoff_roles, required, min_signers), enforce)
