"""监督整改线与例外通道的共享口径（老田 2026-10-20 校准 + 方案B 裁定）。

本模块只定义口径常量与载荷构造，不做业务动作、不发网络、不落盘：
- 整改线：监督席发现问题 → 归口席（tian_feng）登记去重 → 分级 → 研发工单
  整改（无单不写码、过评审门合并、验收归口归口席）。监督席只出问题单，
  不派研发、不直发本人。
- 例外通道：签准/交易/生产变更类 → approval_gate 待签 + risk_guard 预警双动作，
  不经整改线；监督席自身永不直发本人。
- 分级三档（裁定包 B5 口径）：一般整改 normal / 升级请示 escalation /
  阻断 blocking；仅阻断类可由归口席直发本人（白名单扩员，方案B）。

TODO(领域实现): 分级细则（阈值/佐证规则）与改判入口；待签事项与风控预警的
实际送达接线（守门席独立于执行链路，不得由监督席内嵌实例）。
"""
from __future__ import annotations

import time
from typing import Any, Dict, Tuple

from roles.approval_gate import ApprovalGate

# ---- 问题类别 ----

# 一般问题：进整改线，归口席统一登记去重与分级。
ISSUE_CLASS_GENERAL = "general"

# 例外类别：签准/交易/生产变更——不经整改线，走待签 + 预警双动作。
EXCEPTION_ISSUE_CLASSES: Tuple[str, ...] = (
    "approval",          # 签准
    "trade",             # 交易
    "production_change",  # 生产变更
)

# ---- 通道标签（路由口径） ----

ROUTE_TIAN_FENG = "tian_feng"           # 归口：登记去重 → 分级 → 整改/升级
ROUTE_APPROVAL_GATE = "approval_gate"   # 例外：待签事项，本人签准闭环
ROUTE_RISK_GUARD = "risk_guard"         # 例外：风控预警（只预警不执行）

# ---- 归口分级三档 ----

SEVERITY_NORMAL = "normal"          # 一般整改
SEVERITY_ESCALATION = "escalation"  # 升级请示
SEVERITY_BLOCKING = "blocking"      # 阻断类（唯一可由归口席直发本人的档）
SEVERITIES: Tuple[str, ...] = (
    SEVERITY_NORMAL, SEVERITY_ESCALATION, SEVERITY_BLOCKING,
)

# ---- subject 类别前缀（与 roles.message_bus 的类别登记保持一致） ----
#
# 类别以 subject 前缀承载：Report 结构变更需版本化，前缀为等价机制
# （裁定包待拍板项 1 的两个选项之一）。前缀字符串只在此与 message_bus
# 各留一份，字面一致；TODO(领域实现): 若 Report 增列 kind 字段则改为字段承载。

PREFIX_REMEDIATION_TICKET = "[整改工单]"   # 阻断类·整改工单（需本人放行）
PREFIX_ESCALATION_REQUEST = "[升级请示]"   # 阻断类·升级请示（需本人裁决）
PREFIX_ISSUE_INTAKE = "[问题单]"           # 归口请求（command 层）
PREFIX_ESCALATION_ROUTE = "[升级请求]"     # 升级请求交归口（command 层）
PREFIX_PENDING_APPROVAL = "[待签]"         # 待签事项登记请求（command 层）
PREFIX_RISK_NOTICE = "[风控预警]"          # 风控预警通知（command 层）
PREFIX_TRIAGE_NOTICE = "[分级路由]"        # 归口分级路由通知（command 层）


def normalize_issue_class(issue_class: Any) -> str:
    """归一问题类别：空/未知一律落一般问题（不进例外通道）。

    例外类别是收敛白名单：只有明确命中签准/交易/生产变更才算例外，
    未知取值不猜、不兜底成例外，避免误入待签通道。
    """
    key = str(issue_class or "").strip().lower()
    if key in EXCEPTION_ISSUE_CLASSES:
        return key
    return ISSUE_CLASS_GENERAL


def is_exception_class(issue_class: Any) -> bool:
    """是否命中例外类别（签准/交易/生产变更）。"""
    return normalize_issue_class(issue_class) in EXCEPTION_ISSUE_CLASSES


def routes_for(issue_class: Any) -> Tuple[str, ...]:
    """按问题类别给出通道：例外类 → 待签 + 预警双动作；其余 → 归口。"""
    if is_exception_class(issue_class):
        return (ROUTE_APPROVAL_GATE, ROUTE_RISK_GUARD)
    return (ROUTE_TIAN_FENG,)


def normalize_severity(severity: Any) -> str:
    """归一分级：未登记取值落一般整改（不猜阻断）。

    阻断类（唯一可直发本人的档）只认显式登记值；未知取值不兜底成阻断，
    避免把未判定的升级误升为直发本人。
    """
    key = str(severity or "").strip().lower()
    if key in SEVERITIES:
        return key
    return SEVERITY_NORMAL


def build_issue(source: str, subject: str, evidence: Dict[str, Any],
                **extra: Any) -> Dict[str, Any]:
    """构造问题单结构（附证据 + 时间戳）。只组装数据，不投递。"""
    issue: Dict[str, Any] = {
        "source": source,
        "subject": subject,
        "evidence": evidence,
        "ts": time.time(),
    }
    issue.update(extra)
    return issue


def build_exception_actions(subject: str, evidence: Dict[str, Any],
                            raised_by: str) -> Dict[str, Any]:
    """例外通道双动作载荷：approval_gate 待签事项 + risk_guard 预警。

    只造载荷不送达（送达由编排层/总线承担，骨架内为 TODO(领域实现)）：
    - 待签事项绑定材料指纹（与 approval_gate 的指纹算法同源，本人签准闭环；
      沉默与"继续"不是签字，本网关无自动放行路径）；
    - 预警只预警，观察席不执行任何动作；
    - 守门席（approval_gate / risk_guard）独立于执行链路，监督席不内嵌
      守门席实例，只产出载荷与通道标注。
    """
    material = {
        "subject": subject,
        "evidence": evidence,
        "raised_by": raised_by,
    }
    return {
        "gate_item": {
            "category": "pending_approval",
            "material": material,
            "material_fingerprint": ApprovalGate.fingerprint(material),
            "raised_by": raised_by,
            # TODO(领域实现): 经总线送达 approval_gate.submit 登记待签事项
        },
        "risk_alert": {
            "subject": subject,
            "body": "监督席巡检命中签准/交易/生产变更类越权企图，"
                    "已转 approval_gate 待签 + risk_guard 预警双动作",
            "raised_by": raised_by,
            # TODO(领域实现): 经总线送达 risk_guard 观察席
        },
    }
