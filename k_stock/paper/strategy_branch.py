# -*- coding: utf-8 -*-
"""策略分支可插拔（WO-QT-20261020-008，缺口 W-Q2+W-Q3，依赖 -007 已满足）。

定纲（老田 2026-10-20）：许褚同一信号，**跟随投研**与**激进直响**是两种
策略分支，在模拟环境平行比较；各带权限声明/风险门/模拟执行/复盘规则。
**「激进」≠绕过真实交易权限**——激进直响只作为隔离模拟策略验证，真实
通知、生产执行、真实交易继续 BLOCK（P0 ACK 撤销竞态/持久受理根/真实
分发权限/W-Q3 专项审批未过门）。

本模块四层结构：
  1. 统一策略接口 StrategyBranch（ABC）：permission 权限声明强制（缺声明
     子类定义即拒，closed on failure）+ 决策入口 decide()（输入合法信号点
     记录+combo 上下文 → 执行/放弃+理由）+ 复盘钩子 review()（依据链/偏差）；
  2. 两个内置实现：
     - follow_research  跟随投研：经 plan_meeting_ledger.as_of_plan 反查
       该 combo+stock 在信号时刻生效的投研预案，按预案执行；预案与信号
       方向冲突 → 放弃（reason_code=plan_conflict，分歧留痕）；执行引用
       plan_version（禁回写由 -006 as_of_plan 的「生效时点 ≤ 信号时刻」
       反查口径保证）；
     - aggressive_direct 激进直响：不依赖投研预案，直接按 combo_registry
       persona 参数响应（BUY 受 conf_floor 约束、SELL 该卖还得卖——与
       -005/exec_run 口径一致）；
  3. 分支注册制 register_branch/create_branch（对齐 -003 松耦合验收：
     新增策略分支=注册新实现，不改分发与账本代码）；
  4. BranchRunner 分发器：同一信号喂所有已注册分支，各分支落单进**隔离
     模拟账**（account_id = combo_id@分支标识，ref 幂等键按 @账户 分段，
     互不串账）；实质动作（执行与放弃都是决策结果）统一入 -007
     trader_action 回执账（actor=branch:分支标识）。

回执契约（对齐 -007）：分支的实质动作入 trader_action 回执；同一信号
重复喂入时回执幂等拒绝被显式标记为 already_recorded（回执已在账，不静默
重写）。分支放弃也回执留痕（payload.decided_action=abstain + reason_code）
——定纲：反例与分歧是最有价值的训练资产。

失败语义（closed on failure）：permission 缺失/分支未注册/决策结构非法/
plan 上下文缺失/plan 结构非法 → 显式抛 BranchError 族；单分支内部异常
被 Runner 捕获后**留痕 + 显式标记 status=error**（不静默吞掉、不拖垮
其他分支的平行响应——回执账本就是并行独立入账语义），其余校验失败一律
显式抛错禁静默降级。

未接线说明（汇报口径）：真实订阅分发总线（发布方=信号池 P7）不存在、
许褚触发链为 offline shadow——BranchRunner 的调用方属后续工单接线；
真实通知出口/生产执行通道在本模块零代码路径（权限声明层 BLOCK，
测试断言全链路零网络出口）。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import combo_registry
import event_receipts
import plan_meeting_ledger
import signal_consumer

BRANCH_SCHEMA_VERSION = 1

# 内置两分支标识（亦为隔离模拟账 account_id 的分支段）
FOLLOW_RESEARCH = "follow_research"
AGGRESSIVE_DIRECT = "aggressive_direct"

# 预案方向语义（plan_json["action"]）：与信号 action 不一致即 plan_conflict
PLAN_ACTIONS = ("BUY", "SELL", "HOLD")


class BranchError(Exception):
    """分支契约违例（closed on failure）。携带 reason_code 供留痕归因。"""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


class BranchPermissionError(BranchError):
    """permission 权限声明缺失/非法（分支不得接入）。"""


class BranchNotRegistered(BranchError):
    """branch_id 未注册（closed on failure，不静默落到默认分支）。"""


class DuplicateBranchError(BranchError):
    """branch_id 重复注册（禁覆盖——注册制下同 id 即同实现契约）。"""


# ---------- permission 权限声明（强制，缺声明拒绝接入） ----------


def check_permission(permission, who: str) -> dict:
    """校验分支 permission 声明，返回规范化副本。

    三项必须显式精确匹配：sim_only=True（只允许隔离模拟）、
    real_notify=False（真实通知 BLOCK）、prod_exec=False（生产执行 BLOCK）。
    「激进」≠绕过真实交易权限——激进直响同样受此钳制。缺失/任一项不符
    → BranchPermissionError（closed on failure）。"""
    if not isinstance(permission, dict):
        raise BranchPermissionError(
            "permission_missing",
            f"{who} 缺 permission 权限声明（closed on failure 拒绝接入）——分支注册"
            "必须显式声明 sim_only=true、真实通知=BLOCK、生产执行=BLOCK",
        )
    if permission.get("sim_only") is not True:
        raise BranchPermissionError(
            "permission_invalid",
            f"{who} permission.sim_only 必须显式为 true（分支只允许在隔离模拟环境运行）",
        )
    if permission.get("real_notify") is not False:
        raise BranchPermissionError(
            "permission_invalid",
            f"{who} permission.real_notify 必须显式为 false（真实通知 BLOCK——"
            "通知出口未过门，模拟分支不得触发）",
        )
    if permission.get("prod_exec") is not False:
        raise BranchPermissionError(
            "permission_invalid",
            f"{who} permission.prod_exec 必须显式为 false（生产执行 BLOCK——"
            "激进≠绕过真实交易权限）",
        )
    return dict(permission)


def make_decision(
    execute: bool,
    reason_code: str,
    reason: str,
    *,
    action: str | None = None,
    plan_version: str | None = None,
    basis: list | None = None,
    plan_effective_at: str | None = None,
) -> dict:
    """构造标准化决策输出（执行/放弃 + 理由 + 依据链）。

    plan_effective_at：跟随分支引用的预案生效时点（WO-QT-20261020-012
    接线用——快照 plan 印记四键需 combo_id/stock/plan_version/effective_at；
    缺该键则不向快照透传 plan 印记，决策本身不受影响）。"""
    return {
        "execute": bool(execute),
        "reason_code": reason_code,
        "reason": reason,
        "action": action,
        "plan_version": plan_version,
        "basis": list(basis or []),
        "plan_effective_at": plan_effective_at,
    }


def _validate_decision(decision, branch_id: str) -> dict:
    """Runner 侧决策结构校验（防分支实现返回残缺决策，closed on failure）。"""
    if not isinstance(decision, dict):
        raise BranchError(
            "decision_invalid",
            f"分支 {branch_id} 决策须为 dict: {type(decision).__name__}",
        )
    for k in ("execute", "reason_code", "reason"):
        if k not in decision:
            raise BranchError(
                "decision_invalid",
                f"分支 {branch_id} 决策缺字段 {k}（closed on failure）",
            )
    if not isinstance(decision["execute"], bool):
        raise BranchError(
            "decision_invalid",
            f"分支 {branch_id} decision.execute 须为 bool: {decision['execute']!r}",
        )
    if (
        not isinstance(decision["reason_code"], str)
        or not decision["reason_code"].strip()
    ):
        raise BranchError(
            "decision_invalid",
            f"分支 {branch_id} reason_code 须为非空字符串（理由留痕）",
        )
    return decision


# ---------- 统一策略接口 ----------


class StrategyBranch:
    """策略分支统一接口（ABC 语义：decide 必须实现）。

    permission 声明为**定义时强制**：子类定义即校验（__init_subclass__），
    缺声明/声明不符连类都建不出来（closed on failure，比实例化时才拦更早）。
    注册与实例化路径再校验一次（防运行期被篡改类属性）。

    decide() 输入：
      signal  —— 合法信号点记录（已经 signal_consumer.normalize_signal 规范化）；
      context —— combo 上下文：{combo_id, combo_entry（注册表条目）,
                 plan_con（-006 计划账 sqlite 连接，按分支需要取用）, now}。
    输出：make_decision(...) 结构（执行/放弃 + 理由 + 依据链）。

    review() 复盘钩子：输出平行比较所需字段（依据链/偏差），默认实现基于
    决策输出拼装；子类可覆写 _deviation() 补充分支特有偏差语义。
    """

    branch_id: str = ""
    PERMISSION: dict | None = None

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        if not isinstance(cls.branch_id, str) or not cls.branch_id.strip():
            raise BranchPermissionError(
                "branch_id_missing",
                f"策略分支子类 {cls.__name__} 缺非空 branch_id（closed on failure）",
            )
        check_permission(cls.PERMISSION, f"策略分支 {cls.branch_id or cls.__name__}")

    def decide(self, signal: dict, context: dict) -> dict:
        """决策入口（必须由子类实现）。"""
        raise NotImplementedError

    def review(self, signal: dict, decision: dict, context: dict) -> dict:
        """复盘钩子：依据链/偏差，供平行比较并排对比（只读，不落单不回执）。"""
        return {
            "branch_id": self.branch_id,
            "signal_id": signal["signal_id"],
            "decided_action": "execute" if decision["execute"] else "abstain",
            "action": decision.get("action"),
            "reason_code": decision["reason_code"],
            "plan_version": decision.get("plan_version"),
            "basis_chain": list(decision.get("basis") or []),
            "deviation": self._deviation(decision, context),
            "branch_schema_version": BRANCH_SCHEMA_VERSION,
        }

    def _deviation(self, decision: dict, context: dict):
        """分支特有偏差语义（默认无偏差；子类覆写）。"""
        return None


# ---------- 内置分支一：跟随投研 ----------


class FollowResearchBranch(StrategyBranch):
    """跟随投研分支：信号触发 → 反查信号时刻生效预案 → 按预案执行/冲突放弃。

    语义：
      - as_of_plan(combo_id, stock, at_time=signal.triggered_at) 反查当时生效
        版本（-006 禁回写口径：信号时刻已生效的版本才可引用）；
      - 无任何已生效版本 → 放弃（no_plan_effective，留痕）；
      - 预案方向与信号冲突（如 plan 持有 HOLD、信号 SELL）→ 放弃
        （plan_conflict，分歧留痕——反例是最有价值的训练资产）；
      - 方向一致 → 执行并引用 plan_version（persona 置信门槛等落单守卫
        交由 -005 consume_signal 统一把关，本分支不重复实现风控）。
    """

    branch_id = FOLLOW_RESEARCH
    PERMISSION = {"sim_only": True, "real_notify": False, "prod_exec": False}

    def decide(self, signal: dict, context: dict) -> dict:
        plan_con = (context or {}).get("plan_con")
        if plan_con is None:
            raise BranchError(
                "plan_context_missing",
                "follow_research 需要 context.plan_con（-006 计划账连接）——"
                "缺失属调用方契约违例（closed on failure）",
            )
        combo_id = context["combo_id"]
        stock = signal["stock"]
        basis = [
            {
                "step": "signal",
                "detail": f"{signal['action']} conf={signal['confidence']} "
                f"rule={signal['rule_id']}@{signal['rule_version']}",
            }
        ]
        plan = plan_meeting_ledger.as_of_plan(
            plan_con, combo_id, stock, at_time=signal["triggered_at"]
        )
        if plan is None:
            basis.append(
                {
                    "step": "as_of_plan",
                    "combo_id": combo_id,
                    "stock": stock,
                    "at_time": signal["triggered_at"],
                    "plan_version": None,
                }
            )
            return make_decision(
                False,
                "no_plan_effective",
                f"信号时刻 {signal['triggered_at']} 无已生效投研预案"
                f"（combo={combo_id}, stock={stock}）——跟随分支不盲动",
                action=signal["action"],
                basis=basis,
            )
        plan_body = plan["plan"]
        plan_action = plan_body.get("action") if isinstance(plan_body, dict) else None
        if plan_action not in PLAN_ACTIONS:
            raise BranchError(
                "plan_invalid",
                f"plan_version={plan['plan_version']} 的 plan_json 缺合法 action"
                f"（只认 {PLAN_ACTIONS}）: {plan_action!r}（closed on failure）",
            )
        basis.append(
            {
                "step": "as_of_plan",
                "combo_id": combo_id,
                "stock": stock,
                "at_time": signal["triggered_at"],
                "plan_version": plan["plan_version"],
                "effective_at": plan["effective_at"],
                "risk_profile": plan["risk_profile"],
            }
        )
        match = plan_action == signal["action"]
        basis.append(
            {
                "step": "direction_check",
                "plan_action": plan_action,
                "signal_action": signal["action"],
                "match": match,
            }
        )
        if not match:
            return make_decision(
                False,
                "plan_conflict",
                f"预案方向冲突：plan_version={plan['plan_version']} plan_action="
                f"{plan_action} vs 信号 {signal['action']}——分歧留痕，跟随分支放弃",
                action=signal["action"],
                plan_version=plan["plan_version"],
                basis=basis,
            )
        return make_decision(
            True,
            "plan_aligned",
            f"按信号时刻生效预案执行：plan_version={plan['plan_version']}",
            action=signal["action"],
            plan_version=plan["plan_version"],
            basis=basis,
            plan_effective_at=plan["effective_at"],
        )

    def _deviation(self, decision: dict, context: dict):
        if decision["reason_code"] == "plan_conflict":
            steps = {
                s.get("step"): s
                for s in decision.get("basis", [])
                if isinstance(s, dict)
            }
            dc = steps.get("direction_check", {})
            return {
                "type": "plan_conflict",
                "plan_action": dc.get("plan_action"),
                "signal_action": dc.get("signal_action"),
                "note": "预案与信号方向分歧已留痕（分歧=训练资产）",
            }
        if decision["reason_code"] == "no_plan_effective":
            return {
                "type": "no_plan_effective",
                "note": "信号时刻无生效预案可跟随——跟随分支选择不行动",
            }
        return None


# ---------- 内置分支二：激进直响 ----------


class AggressiveDirectBranch(StrategyBranch):
    """激进直响分支：信号触发 → 直接按 persona 参数响应，不依赖投研预案。

    语义：
      - 复用 combo_registry persona v1 参数（conf_floor/max_pos_pct/
        max_total_pct）——参数同源四元组注册表，不自带第二套风控定义；
      - BUY conf < conf_floor → 放弃（conf_floor）；SELL 该卖还得卖
        （不受门槛限制——与 -005 consume_signal/exec_run 口径一致）；
      - 不查投研预案、不引用 plan_version——其偏差语义即「无预案对照」，
        与 follow_research 的分歧在复盘钩子里并排可见。
      - 「激进」仅指决策路径激进（直响不等待投研），**不绕过任何权限**：
        permission 声明与真实出口钳制同 follow_research 完全一致。
    """

    branch_id = AGGRESSIVE_DIRECT
    PERMISSION = {"sim_only": True, "real_notify": False, "prod_exec": False}

    def decide(self, signal: dict, context: dict) -> dict:
        entry = (context or {}).get("combo_entry")
        if entry is None:
            raise BranchError(
                "combo_context_missing",
                "aggressive_direct 需要 context.combo_entry（-003 注册表条目）——"
                "缺失属调用方契约违例（closed on failure）",
            )
        persona = entry["quad"]["persona"]
        floor = combo_registry.persona_conf_floor(entry)
        basis = [
            {
                "step": "signal",
                "detail": f"{signal['action']} conf={signal['confidence']} "
                f"rule={signal['rule_id']}@{signal['rule_version']}",
            },
            {
                "step": "persona_direct",
                "persona_id": persona["id"],
                "persona_version": persona["version"],
                "conf_floor": floor,
            },
        ]
        if signal["action"] == "BUY" and signal["confidence"] < floor:
            return make_decision(
                False,
                "conf_floor",
                f"persona 拒单：conf {signal['confidence']} < 门槛 {floor}"
                f"（persona={persona['id']}）——该买才买",
                action=signal["action"],
                basis=basis,
            )
        return make_decision(
            True,
            "persona_direct_hit",
            f"按 persona={persona['id']}@{persona['version']} 直接响应"
            f"（SELL 不受限门槛——该卖还得卖）"
            if signal["action"] == "SELL"
            else f"按 persona={persona['id']}@{persona['version']} 直接响应"
            f"（conf {signal['confidence']} ≥ 门槛 {floor}）",
            action=signal["action"],
            basis=basis,
        )

    def _deviation(self, decision: dict, context: dict):
        return {
            "type": "no_plan_reference",
            "note": "按 persona 直接响应，未对照投研预案（与跟随分支的"
            "结构性分歧点，平行比较的核心观察项）",
        }


# ---------- 分支注册制（新增策略分支=注册新实现，不改分发与账本代码） ----------

_BRANCH_REGISTRY: dict[str, type] = {}


def register_branch(cls: type) -> type:
    """注册一个策略分支实现（可作装饰器）。同 id 重复注册 → 显式拒绝。"""
    if not (isinstance(cls, type) and issubclass(cls, StrategyBranch)):
        raise BranchError(
            "register_invalid", f"注册对象须为 StrategyBranch 子类: {cls!r}"
        )
    bid = cls.branch_id
    if not isinstance(bid, str) or not bid.strip():
        raise BranchError(
            "branch_id_missing", "注册分支缺非空 branch_id（closed on failure）"
        )
    if bid in _BRANCH_REGISTRY:
        raise DuplicateBranchError(
            "duplicate_branch",
            f"branch_id {bid!r} 已注册为 {_BRANCH_REGISTRY[bid].__name__}"
            "——禁覆盖，新实现请用新 branch_id",
        )
    check_permission(cls.PERMISSION, f"策略分支 {bid}")  # 注册口再验一道
    _BRANCH_REGISTRY[bid] = cls
    return cls


def unregister_branch(branch_id: str) -> None:
    """注销分支（测试/下线用）。未注册 → 显式抛错。"""
    if branch_id not in _BRANCH_REGISTRY:
        raise BranchError(
            "branch_not_registered", f"branch_id 未注册，无法注销: {branch_id!r}"
        )
    del _BRANCH_REGISTRY[branch_id]


def registered_branch_ids() -> list[str]:
    """已注册分支清单（注册序）。"""
    return list(_BRANCH_REGISTRY)


def create_branch(branch_id: str) -> StrategyBranch:
    """按 branch_id 实例化分支。未注册/permission 被运行期篡改 → 显式抛错。"""
    cls = _BRANCH_REGISTRY.get(branch_id)
    if cls is None:
        raise BranchNotRegistered(
            "branch_not_registered",
            f"branch_id 未注册: {branch_id!r}（closed on failure，不静默落到默认分支）",
        )
    check_permission(cls.PERMISSION, f"策略分支 {branch_id}")  # 防运行期篡改
    return cls()


# ---------- 分发器：同一信号 → 全分支平行响应 → 隔离模拟账 ----------


class BranchRunner:
    """把一条信号喂给所有已注册分支，各分支在各自隔离模拟账平行落单。

    隔离口径：分支 b 的落单账户 account_id = f"{combo_id}@{b.branch_id}"
    （ref 幂等键按 @账户 分段，与 -005 多账户互不串味同机制）；
    回执为**共享账**（-007 天然并行独立入账语义），actor=branch:分支标识
    区分来源，plan_version 仅跟随分支携带。

    落单复用 -005 consume_signal 全守卫（契约校验/时效/persona/combo
    注册表/幂等/快照 fail-visible），本模块不新写任何账本或风控逻辑。
    """

    def __init__(
        self,
        engine,
        registry_path: Path | str | None = None,
        receipt_log: Path | str | None = None,
        rejection_log: Path | str | None = None,
    ):
        self.engine = engine
        self.registry_path = registry_path
        self.receipt_log = (
            Path(receipt_log)
            if receipt_log is not None
            else event_receipts.DEFAULT_RECEIPT_LOG
        )
        self.rejection_log = (
            Path(rejection_log)
            if rejection_log is not None
            else signal_consumer.DEFAULT_REJECTION_LOG
        )

    # -- 内部：单分支执行一个决策并留痕 --------------------------------

    def _run_one(
        self,
        branch_id: str,
        sig: dict,
        record: dict,
        combo_id: str,
        entry: dict,
        plan_con,
        now,
    ) -> dict:
        branch = create_branch(branch_id)
        ctx = {
            "combo_id": combo_id,
            "combo_entry": entry,
            "plan_con": plan_con,
            "now": now,
        }
        try:
            decision = branch.decide(sig, ctx)
        except BranchError as e:
            # 分支内部契约违例：留痕 + 显式标记 error，不静默、不拖垮其他分支
            self._trace(branch_id, sig, "branch_error", f"{e.reason_code}: {e}")
            return {
                "status": "error",
                "reason_code": e.reason_code,
                "reason": str(e),
                "decision": None,
                "account_id": None,
                "receipt_status": None,
            }
        _validate_decision(decision, branch_id)
        aid = f"{combo_id}@{branch_id}"
        # 跟随分支引用的预案版本 → 快照 plan 印记（WO-QT-20261020-012 接线）：
        # 四键 combo_id/stock/plan_version/effective_at 齐备才透传，缺任一
        # 则不写 plan 印记（决策与落单不受影响，零变化优先）。
        plan_stamp = None
        if decision.get("plan_version") and decision.get("plan_effective_at"):
            plan_stamp = {
                "combo_id": combo_id,
                "stock": sig["stock"],
                "plan_version": decision["plan_version"],
                "effective_at": decision["plan_effective_at"],
            }
        if decision["execute"]:
            try:
                res = signal_consumer.consume_signal(
                    self.engine,
                    record,
                    combo_id,
                    registry_path=self.registry_path,
                    account_id=aid,
                    rejection_log=self.rejection_log,
                    now=now,
                    plan_version=plan_stamp,
                )
            except signal_consumer.SignalRejected as e:
                # 落单守卫拒收（过期/门槛/combo 违例）：-005 已留痕，此处显式透传
                self._record_action_receipt(
                    sig,
                    branch_id,
                    {
                        "decided_action": decision["action"],
                        "decision": "execute",
                        "reason_code": decision["reason_code"],
                        "reason": decision["reason"],
                        "order_status": "rejected",
                        "basis": decision["basis"],
                    },
                    decision.get("plan_version"),
                    combo_id,
                )
                return {
                    "status": "rejected",
                    "reason_code": e.reason_code,
                    "reason": str(e),
                    "decision": decision,
                    "account_id": aid,
                    "receipt_status": "recorded",
                }
            payload = {
                "decided_action": decision["action"],
                "decision": "execute",
                "reason_code": decision["reason_code"],
                "reason": decision["reason"],
                "order_status": res["status"],
                "basis": decision["basis"],
            }
            self._record_action_receipt(
                sig, branch_id, payload, decision.get("plan_version"), combo_id
            )
            return {
                "status": res["status"],
                "reason_code": decision["reason_code"],
                "reason": decision["reason"],
                "decision": decision,
                "account_id": aid,
                "receipt_status": "recorded",
            }
        # 放弃：不落单，但决策结果照常回执留痕（分歧/反例保全）
        payload = {
            "decided_action": "abstain",
            "decision": "abstain",
            "reason_code": decision["reason_code"],
            "reason": decision["reason"],
            "basis": decision["basis"],
        }
        self._record_action_receipt(
            sig, branch_id, payload, decision.get("plan_version"), combo_id
        )
        return {
            "status": "abstain",
            "reason_code": decision["reason_code"],
            "reason": decision["reason"],
            "decision": decision,
            "account_id": aid,
            "receipt_status": "recorded",
        }

    def _record_action_receipt(
        self, sig: dict, branch_id: str, payload: dict, plan_version, combo_id: str
    ) -> str:
        """实质决策入 trader_action 回执（-007 契约：非空 dict 载荷）。

        幂等：同 (signal_id, receipt_kind, actor) 重复 → -007 显式拒绝，
        此处标记 already_recorded（回执已在账，不静默重写、不报错中断
        平行比较）——重复喂入是重放场景，原决策已留痕。"""
        try:
            event_receipts.record_receipt(
                self.receipt_log,
                sig["signal_id"],
                event_receipts.TRADER_ACTION,
                actor=f"branch:{branch_id}",
                payload=payload,
                plan_version=plan_version,
                combo_id=combo_id,
            )
            return "recorded"
        except event_receipts.DuplicateReceiptError:
            return "already_recorded"

    def _trace(self, branch_id: str, sig: dict, kind: str, reason: str) -> dict:
        """分支级异常留痕（append-only jsonl，与 -005 拒单账同风格）。"""
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "kind": kind,
            "signal_id": sig.get("signal_id"),
            "combo_id": None,
            "branch_id": branch_id,
            "reason_code": kind,
            "reason": reason,
            "branch_schema_version": BRANCH_SCHEMA_VERSION,
        }
        return signal_consumer.append_rejection(self.rejection_log, entry)

    # -- 对外：喂信号 / 复盘对比 ---------------------------------------

    def feed_signal(self, record: dict, combo_id: str, plan_con=None, now=None) -> dict:
        """同一信号喂所有已注册分支（平行比较主入口）。

        信号规范化与 combo 解析在分发层一次性把关（closed on failure：
        非法信号/combo 未注册直接抛错，不进任何分支）；各分支独立决策、
        独立落单（隔离模拟账）、独立回执留痕。返回按分支分列的结果。"""
        sig = signal_consumer.normalize_signal(record)  # 非法 → 直接抛
        reg = combo_registry.load_registry(
            self.registry_path or combo_registry.default_registry_path()
        )
        entry = combo_registry.resolve(reg, combo_id)  # 未注册 → 直接抛
        results = {
            bid: self._run_one(bid, sig, record, combo_id, entry, plan_con, now)
            for bid in registered_branch_ids()
        }
        return {
            "signal_id": sig["signal_id"],
            "combo_id": combo_id,
            "branches": results,
        }

    def compare_reviews(self, record: dict, combo_id: str, plan_con=None) -> dict:
        """复盘钩子平行比较（只读：不落单、不回执、不留痕）。

        同一信号各分支的 review 输出并排返回——依据链（basis_chain）与
        偏差（deviation）逐分支可对比，供批 3 组合有效性报告 3.4 消费。"""
        sig = signal_consumer.normalize_signal(record)
        reg = combo_registry.load_registry(
            self.registry_path or combo_registry.default_registry_path()
        )
        entry = combo_registry.resolve(reg, combo_id)
        reviews = {}
        for bid in registered_branch_ids():
            branch = create_branch(bid)
            ctx = {
                "combo_id": combo_id,
                "combo_entry": entry,
                "plan_con": plan_con,
                "now": None,
            }
            decision = branch.decide(sig, ctx)
            _validate_decision(decision, bid)
            reviews[bid] = branch.review(sig, decision, ctx)
        return {
            "signal_id": sig["signal_id"],
            "combo_id": combo_id,
            "reviews": reviews,
            "comparison_ready": len(reviews) >= 2,
        }


# 内置两分支随模块注册（注册制：第三个分支=注册新实现，零改本模块）
register_branch(FollowResearchBranch)
register_branch(AggressiveDirectBranch)
