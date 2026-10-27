# -*- coding: utf-8 -*-
"""凭据轮次 vs 真源痕迹 交叉校验（只读纯函数，M1 审计层补强）。

背景（2026-10-17 审查判例）：
- dev 域死凭据 bug：close_done 不校验当前态，blocked 任务背书先落凭据、
  后引擎 move 恒败 → 账本留下永不生效的凭据。旧审计只查「状态 vs
  凭据有无」的正向绕门，查不出这种「凭据在、痕迹无」的反向异常；
- 轮次化幂等键回灌后，同一转移可能有多轮凭据（#r{n}），轮次与真源
  痕迹数应一致。

两类真源痕迹（判例手册）：
- trace 域（academy/pitch/dev/skill）：打回走自由 move 但域内 history
  留痕（stage / action），每轮生效都有痕迹可回放 → 全量校验；
- 消费标记域（gzh/invest/devtask）：last_wiring_key 只存最新已消费键，
  旧轮次标记被覆盖无法回溯 → 仅校验最新凭据 + 标记引用完整性。

校验项：
- credential_never_effective（FAIL）：凭据的 to_status 从未在痕迹出现、
  现状也不在该态、键也未被消费，且不是该目标最新凭据 → 死凭据；
- pending_execution（WARN）：同上但确是该目标最新凭据 → 崩溃窗口候选
  （凭据已落、真源未写；等价于各域已有的 catch-up 前态）；
- round_exceeds_trace（WARN）：同 (target, to_status) 凭据轮数 > 痕迹
  次数 → 有轮次从未生效（trace 域专属）；
- marker_without_credential（FAIL）：消费标记引用的键在账本中不存在
  （账本被清/篡改或标记写坏）。
"""

from __future__ import annotations

from typing import Dict, List, Optional

SCHEMA_VERSION = "1.0"
READ_ONLY = True

_SKIPPED_DECISIONS = {"rejected"}  # 否决凭据不承诺状态生效


def cross_check(
    domain: str,
    creds: List,
    traces: Dict[str, Dict],
    current_status: Optional[Dict[str, str]] = None,
    latest_only: bool = False,
) -> List[Dict]:
    """交叉校验主入口。

    参数：
    - creds：Approval 对象列表（按账本发生序）；
    - traces：{target_id: {"statuses": [达成状态多重列表]（trace 域）,
                            "consumed_keys": set(已消费凭据键)（可选，
                            消费标记域传最新键）}}；
    - current_status：{target_id: 当前状态}（可选，缺目标按无现状处理）；
    - latest_only：True = 消费标记域，只校验每目标最新一张批准凭据
      （旧轮次标记被覆盖，不可判定，跳过）。

    返回 violations 列表（dict，含 kind/severity/target_id/凭据标识）。
    """
    current_status = current_status or {}
    approved = [
        c for c in creds if getattr(c, "decision", "approved") not in _SKIPPED_DECISIONS
    ]
    # 每目标最新一张批准凭据（creds 按发生序，后者覆盖前者）
    latest_of_target: Dict[str, object] = {}
    for c in approved:
        latest_of_target[c.target_id] = c

    violations: List[Dict] = []
    cred_keys = {c.idempotency_key for c in creds}

    # ── 消费标记引用完整性（两派通用）──
    for tid, t in traces.items():
        for key in t.get("consumed_keys", set()):
            if key and key not in cred_keys:
                violations.append(
                    {
                        "kind": "marker_without_credential",
                        "severity": "FAIL",
                        "domain": domain,
                        "target_id": tid,
                        "idempotency_key": key,
                        "detail": "真源消费标记引用的凭据键不在账本中",
                    }
                )

    eval_set = (
        [latest_of_target[t] for t in latest_of_target] if latest_only else approved
    )
    for c in eval_set:
        if c.target_id not in traces:
            # 目标不在真源清单（被删/未同步/测试隔离环境）→ 无法判定
            # 生效性，保守跳过（目标缺失由各域 credential_*_missing 检查）
            continue
        t = traces[c.target_id]
        statuses = t.get("statuses") or []
        consumed = c.idempotency_key in (t.get("consumed_keys") or set())
        hit = c.to_status in statuses
        cur_ok = current_status.get(c.target_id) == c.to_status
        if consumed or hit or cur_ok:
            continue
        is_latest = latest_of_target.get(c.target_id) is c
        violations.append(
            {
                "kind": (
                    "pending_execution" if is_latest else "credential_never_effective"
                ),
                "severity": "WARN" if is_latest else "FAIL",
                "domain": domain,
                "target_id": c.target_id,
                "approval_id": c.approval_id,
                "idempotency_key": c.idempotency_key,
                "to_status": c.to_status,
                "detail": (
                    "凭据已落而真源未达（崩溃窗口候选，等 catch-up）"
                    if is_latest
                    else "凭据的 to_status 无任何痕迹且现状未达（死凭据）"
                ),
            }
        )

    # ── 轮次 vs 痕迹（仅 trace 域：statuses 多重列表可回放）──
    # 只统计「痕迹命中」的凭据（to_status 出现在 statuses）：
    # 死凭据/崩溃窗口已由上方逐凭据检查报告，不在此重复计数。
    if not latest_only:
        groups: Dict[tuple, int] = {}
        for c in approved:
            k = (c.target_id, c.to_status)
            groups.setdefault(k, 0)
            statuses = traces.get(c.target_id, {}).get("statuses") or []
            if c.to_status in statuses:
                groups[k] += 1
        for (tid, to_st), n_hit in sorted(groups.items()):
            n_trace = (traces.get(tid, {}).get("statuses") or []).count(to_st)
            if n_hit > n_trace:
                violations.append(
                    {
                        "kind": "round_exceeds_trace",
                        "severity": "WARN",
                        "domain": domain,
                        "target_id": tid,
                        "to_status": to_st,
                        "detail": "痕迹命中的凭据 %d 张 > 痕迹 %d 次"
                        "（轮次账与痕迹不一致）" % (n_hit, n_trace),
                    }
                )
    return violations


def verdict_for(violations: List[Dict]) -> str:
    """按严重度归并结论：有 FAIL → FAIL；只有 WARN → WARN；否则 PASS。

    缺 severity 键的条目（各域既有 gate 违规）按 FAIL 处理。
    """
    if any(v.get("severity", "FAIL") == "FAIL" for v in violations):
        return "FAIL"
    if violations:
        return "WARN"
    return "PASS"
