# -*- coding: utf-8 -*-
"""E-4 组合工厂（WO-QT-20261020-011，缺口 E-4+X-5，依赖 -003/-008 已满足）。

定纲（老田 2026-10-20）：金融游戏里组合=实验单元（四元组：选股源×投研
persona×盯盘风格×交易员）。工厂目标两条：

  1. **新组合创建零代码**——输入一份组合注册文件（JSON 数据文件，四元组
     齐备+预算字段），create_combo() 校验后注册进 combo_registry 注册表、
     创建隔离账本标识（account_id 口径对齐 -008：combo_id@分支标识）、
     挂复盘订阅（消费口对齐 -007 event_receipts.completeness 四类回执
     完整度与 -008 BranchRunner.compare_reviews 复盘钩子），返回组合凭据。
     全程纯数据文件驱动，既有代码 diff 为零（松耦合同 -003 验收口径）。

  2. **成本预算强制（X-5 纪律：组合不是免费的，token 成本要进账）**——
     注册文件必须含 budget.monthly_llm_cost_budget（数值>0），缺 budget
     或 budget≤0 → closed on failure 拒绝创建（不留半成品）。组合维度
     成本账本（append-only jsonl）为 llm_cost 的**登记口**：当前为模拟
     计费（显式喂入成本记录，非真实 token 计量——接真实计量属后续工单）。
     累计成本超预算 → 状态标记 over_budget，工厂拒绝再派任务（
     guard_dispatch 预留 API，供后续调度消费；本单不接真实调度）。

失败语义（closed on failure）：注册文件缺失/JSON 非法/schema 版本不符/
四元组违例（复用 -003 校验）/预算缺失或非法/combo_id 重复 → 显式抛
ComboFactoryError 族，禁静默降级；校验失败不写任何文件（不留半成品——
注册表写入前全量校验，注册表原子写失败则回滚恢复原文）。

只读复用（不改他人工单领地）：combo_registry（-003 注册表+load_registry
+_validate_entry）、strategy_branch（-008 分支注册制+BranchRunner 账户
口径）、event_receipts（-007 回执账）、signal_consumer（-005）、
plan_meeting_ledger（-006）、decision_snapshot（-004）。

未接线说明（汇报口径）：
  - 工厂产出「组合就绪态」——真实 Agent 实例化（hire 机制）属
    agent_platform 侧后续工单，本模块零代码路径；
  - guard_dispatch 只预留 API，真实调度器消费属后续工单；
  - 模拟计费：record_combo_cost 为显式喂入口，真实 token 计量（对接
    llm_cost.record_cost 或上游网关）属后续工单。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import combo_registry
import strategy_branch

FACTORY_SCHEMA_VERSION = 1

# 预算字段（X-5：组合不是免费的——注册文件必须携带，缺/非法即拒绝创建）
BUDGET_FIELD = "monthly_llm_cost_budget"

# 注册文件顶层必填字段（budget 单独强校验，见 _validate_budget——
# 预算缺失归 BudgetViolationError/budget_missing，不混同普通缺字段）
_REGISTRATION_REQUIRED = ("factory_schema_version", "combo_id", "name", "quad")

# 默认数据文件（append-only jsonl；测试注入临时路径）
DEFAULT_SUBSCRIPTION_LOG = (
    Path(__file__).resolve().parent.parent / "data" / "combo_review_subscriptions.jsonl"
)
DEFAULT_BUDGET_LEDGER = (
    Path(__file__).resolve().parent.parent / "data" / "combo_budget_ledger.jsonl"
)

# 复盘订阅消费口（对齐 -007/-008 的既有消费函数，只登记引用，不复制实现）
REVIEW_CONSUMERS = (
    "event_receipts.completeness",  # -007：四类回执完整度
    "strategy_branch.BranchRunner.compare_reviews",  # -008：复盘钩子并排比较
)


class ComboFactoryError(Exception):
    """工厂契约违例（closed on failure）。携带 reason_code 供归因。"""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


class DuplicateComboError(ComboFactoryError):
    """combo_id 重复（注册表已有同 id 或本次创建重复）——禁覆盖。"""


class BudgetViolationError(ComboFactoryError):
    """预算契约违例（缺 budget/预算非法/超预算派任务）。"""


class BudgetExceededError(BudgetViolationError):
    """组合累计成本已超预算，拒绝再派任务（X-5 硬约束）。"""


# ---------- 注册文件校验（先全量校验，后落盘——不留半成品） ----------


def _validate_budget(budget, where: str) -> dict:
    """预算强校验（X-5 硬验收）：budget 须为对象且含数值>0 的
    monthly_llm_cost_budget。缺失/类型错/≤0 → BudgetViolationError。"""
    if not isinstance(budget, dict) or BUDGET_FIELD not in budget:
        raise BudgetViolationError(
            "budget_missing",
            f"注册文件缺预算字段 budget.{BUDGET_FIELD}（X-5：组合不是免费的，"
            f"token 成本要进账）: {where}",
        )
    v = budget[BUDGET_FIELD]
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not v > 0:
        raise BudgetViolationError(
            "budget_invalid",
            f"budget.{BUDGET_FIELD} 须为数值且 > 0，收到 {v!r}: {where}",
        )
    return dict(budget)


def _load_registration(path: Path | str) -> dict:
    """读入并校验注册文件，返回规范化 dict。任何不合法直接抛错。"""
    p = Path(path)
    where = str(p)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ComboFactoryError(
            "registration_missing", f"组合注册文件不存在: {where}"
        ) from e
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ComboFactoryError(
            "registration_invalid_json",
            f"组合注册文件不是合法 UTF-8 JSON: {where} ({e})",
        ) from e
    if not isinstance(raw, dict):
        raise ComboFactoryError(
            "registration_invalid", f"组合注册文件根须为对象: {where}"
        )
    for k in _REGISTRATION_REQUIRED:
        if k not in raw:
            raise ComboFactoryError("missing_field", f"注册文件缺必填字段 {k}: {where}")
    if raw["factory_schema_version"] != FACTORY_SCHEMA_VERSION:
        raise ComboFactoryError(
            "schema_version_unsupported",
            f"factory_schema_version 只认 {FACTORY_SCHEMA_VERSION}，"
            f"收到 {raw['factory_schema_version']!r}: {where}",
        )
    budget = _validate_budget(raw.get("budget"), where)
    entry = {"combo_id": raw["combo_id"], "name": raw["name"], "quad": raw["quad"]}
    # 四元组/persona v1 参数结构复用 -003 校验（同一注册口径，不复制规则）
    try:
        combo_registry._validate_entry(entry, where)
    except combo_registry.ComboRegistryError as e:
        raise ComboFactoryError("quad_invalid", str(e)) from e
    return {"entry": entry, "budget": budget, "source": where}


def _dup_check(registry_path: Path | str | None, combo_id: str) -> None:
    """重复 combo_id 拒绝（对既有注册表查重；注册制下同 id 即同契约）。"""
    reg = combo_registry.load_registry(
        registry_path or combo_registry.default_registry_path()
    )
    try:
        combo_registry.resolve(reg, combo_id)
    except combo_registry.ComboNotFound:
        return
    raise DuplicateComboError(
        "duplicate_combo",
        f"combo_id 已注册，拒绝重复创建: {combo_id}（新实验单元请用新 id）",
    )


def _atomic_write_json(path: Path, data: dict) -> None:
    """原子写 JSON（同目录临时文件 + os.replace，避免半截文件）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


def _append_line(log_path: Path, entry: dict) -> dict:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


# ---------- 复盘订阅（挂接 -007 completeness / -008 compare_reviews） ----------


def _subscription_entry(combo_id: str, account_ids: dict) -> dict:
    """复盘订阅条目：组合创建即订阅同一信号的回执完整度与分支复盘对比。

    只登记消费口引用（REVIEW_CONSUMERS 指向 -007/-008 既有只读函数），
    不复制实现、不做真实分发——真实订阅总线（发布方=信号池 P7）属后续
    工单接线，与 -007/-008 未接线口径一致。"""
    return {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "kind": "review_subscription",
        "combo_id": combo_id,
        "account_ids": dict(account_ids),
        "consumes": list(REVIEW_CONSUMERS),
        "receipt_kinds": list(combo_receipt_kinds()),
        "factory_schema_version": FACTORY_SCHEMA_VERSION,
    }


def combo_receipt_kinds() -> tuple:
    """本组合复盘订阅关注的回执类型（-007 四类全量）。"""
    import event_receipts

    return event_receipts.RECEIPT_KINDS


def load_subscriptions(
    log_path: Path | str | None = None, combo_id: str | None = None
) -> list[dict]:
    """读复盘订阅账（只读）。combo_id 给定时过滤单组合。"""
    p = Path(log_path) if log_path is not None else DEFAULT_SUBSCRIPTION_LOG
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        e = json.loads(line)
        if combo_id is None or e.get("combo_id") == combo_id:
            out.append(e)
    return out


# ---------- 隔离账本标识（对齐 -008：account_id = combo_id@分支标识） ----------


def branch_account_ids(combo_id: str) -> dict:
    """组合在各已注册分支下的隔离模拟账 account_id（-008 口径镜像）。"""
    return {bid: f"{combo_id}@{bid}" for bid in strategy_branch.registered_branch_ids()}


# ---------- 成本账本（组合维度，模拟计费：显式喂入成本记录） ----------


def record_combo_cost(
    combo_id: str,
    cost,
    note: str = "",
    ledger: Path | str | None = None,
    ts: str | None = None,
) -> dict:
    """登记一笔组合维度 LLM 成本（模拟计费：调用方显式喂入，非真实 token
    计量——真实计量属后续工单）。cost 须为数值>0；append-only jsonl。"""
    if not isinstance(cost, (int, float)) or isinstance(cost, bool) or not cost > 0:
        raise BudgetViolationError(
            "cost_invalid", f"成本记录须为数值且 > 0，收到 {cost!r}（combo={combo_id}）"
        )
    if not isinstance(combo_id, str) or not combo_id.strip():
        raise ComboFactoryError(
            "combo_id_invalid", f"combo_id 须为非空字符串: {combo_id!r}"
        )
    entry = {
        "ts": ts or datetime.now().isoformat(timespec="seconds"),
        "kind": "llm_cost",
        "combo_id": combo_id,
        "cost": round(float(cost), 6),
        "note": str(note or ""),
        "factory_schema_version": FACTORY_SCHEMA_VERSION,
    }
    return _append_line(
        Path(ledger) if ledger is not None else DEFAULT_BUDGET_LEDGER, entry
    )


def _month_of(ts: str) -> str:
    return ts[:7]  # ISO 时刻前 7 位即 YYYY-MM


def combo_budget_status(
    combo_id: str,
    registry_path: Path | str | None = None,
    ledger: Path | str | None = None,
    month: str | None = None,
) -> dict:
    """组合预算状态（只读）：当月累计成本 vs budget.monthly_llm_cost_budget。

    status: "ok" | "over_budget"（超预算即标记，供 guard_dispatch 与后续
    调度消费）。combo 未注册 → ComboNotFound（closed on failure）。"""
    reg = combo_registry.load_registry(
        registry_path or combo_registry.default_registry_path()
    )
    entry = combo_registry.resolve(reg, combo_id)
    budget = float(entry["budget"][BUDGET_FIELD])
    p = Path(ledger) if ledger is not None else DEFAULT_BUDGET_LEDGER
    cur = month or datetime.now().isoformat(timespec="seconds")[:7]
    accumulated = 0.0
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            if (
                e.get("combo_id") == combo_id
                and e.get("kind") == "llm_cost"
                and _month_of(e.get("ts", "")) == cur
            ):
                accumulated += float(e.get("cost") or 0.0)
    accumulated = round(accumulated, 6)
    over = accumulated > budget
    return {
        "combo_id": combo_id,
        "month": cur,
        BUDGET_FIELD: budget,
        "cost_accumulated": accumulated,
        "remaining": round(max(0.0, budget - accumulated), 6),
        "over_budget": over,
        "status": "over_budget" if over else "ok",
    }


def guard_dispatch(
    combo_id: str,
    registry_path: Path | str | None = None,
    ledger: Path | str | None = None,
) -> dict:
    """派任务前预算闸门（X-5 预留 API，供后续调度消费；本单不接真实调度）。

    over_budget → BudgetExceededError（closed on failure，拒绝再派任务）；
    正常 → 返回预算状态快照。"""
    st = combo_budget_status(combo_id, registry_path=registry_path, ledger=ledger)
    if st["over_budget"]:
        raise BudgetExceededError(
            "over_budget",
            f"组合 {combo_id} 当月累计成本 {st['cost_accumulated']} 已超预算 "
            f"{st[BUDGET_FIELD]}（X-5：超预算拒绝再派任务，closed on failure）",
        )
    return st


# ---------- 工厂主入口：create_combo（零代码创建） ----------


def create_combo(
    registration_path: Path | str,
    registry_path: Path | str | None = None,
    subscription_log: Path | str | None = None,
) -> dict:
    """读一份组合注册文件 → 校验 → 注册 → 建隔离账本标识 → 挂复盘订阅
    → 返回组合凭据。全程零代码改动（纯数据文件驱动）。

    步骤（校验全前置，落盘收口在注册表原子写——写失败回滚恢复原文，
    closed on failure 不留半成品）：
      ① 注册文件校验（schema/四元组复用 -003/预算强校验 X-5）；
      ② combo_id 查重（对既有注册表，重复拒绝）；
      ③ 复盘订阅条目落账（append-only jsonl，消费口对齐 -007/-008）；
      ④ 注册表原子追加新条目（条目携带 budget，供预算状态读取）。
    返回组合凭据 dict（含 account_ids/订阅条目/预算快照）。
    """
    spec = _load_registration(registration_path)
    entry = dict(spec["entry"])
    entry["budget"] = spec["budget"]
    combo_id = entry["combo_id"]
    _dup_check(registry_path, combo_id)

    sub_log = (
        Path(subscription_log)
        if subscription_log is not None
        else DEFAULT_SUBSCRIPTION_LOG
    )
    reg_path = (
        Path(registry_path)
        if registry_path is not None
        else combo_registry.default_registry_path()
    )

    account_ids = branch_account_ids(combo_id)
    sub_entry = _subscription_entry(combo_id, account_ids)

    # 注册表原子写：读原文→追加→写临时文件→os.replace；失败恢复原文
    original = reg_path.read_text(encoding="utf-8") if reg_path.exists() else None
    try:
        reg = combo_registry.load_registry(reg_path)
        reg["combos"].append(entry)
        _atomic_write_json(reg_path, reg)
        _append_line(sub_log, sub_entry)
    except Exception:
        if original is not None:
            reg_path.write_text(original, encoding="utf-8")
        raise
    # 注册表写后复验（load_registry 全量校验——写坏即抛，closed on failure）
    combo_registry.load_registry(reg_path)

    return {
        "combo_id": combo_id,
        "name": entry["name"],
        "quad": entry["quad"],
        "budget": dict(entry["budget"]),
        "account_ids": account_ids,
        "registry_path": str(reg_path),
        "subscription_log": str(sub_log),
        "subscription": sub_entry,
        "factory_schema_version": FACTORY_SCHEMA_VERSION,
        "status": "ready",
    }
