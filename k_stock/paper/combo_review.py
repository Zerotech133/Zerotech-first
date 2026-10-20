# -*- coding: utf-8 -*-
"""组合复盘报表（WO-QT-20261020-013，缺口 X-5 延伸：成本入复盘）。

定纲（老田 2026-10-20 放行）：011 组合工厂已落地成本账（record_combo_cost
登记口、combo_budget_status 预算、guard_dispatch 超预算闸门），但**成本还没
进入复盘报表**——组合复盘只能看收益，看不到算力成本，无法回答「哪个组合性
价比高」。本单把成本维度接进复盘，产出**可横向对比**的组合复盘报表。

报表三段（build_review 输出）：
  1. 收益维度：沿用 -008 compare_reviews 的分支决策对比（各分支执行/放弃、
     依据链、偏差），后验收益若可取则逐分支挂上；
  2. 成本维度：读 -011 成本账（当月累计 / 预算 / 剩余 / over_budget）；
  3. ROI：口径为计划 3.3 验收原文「**组合 ROI = 收益 − 算力成本**」。

**不造数原则（硬约束）**：
  - 后验收益未接入（尚未接入结算后验）→ ROI 字段显式标 `pending_backfill`，
    **不填 0 充数**（0 会被读成「白干且不亏」，属造数）；
  - 收益单位与成本单位不可直接相减（如喂入百分比、成本是元）→ ROI 标
    `unit_mismatch`，不出数；
  - 成本预算未登记（-003 早期注册表条目无 budget）→ 预算/剩余/over_budget
    记 null，不臆造预算值；成本累计照实读 -011 成本账。

只读复用（不改他人工单领地）：combo_factory（-011 成本账/预算/隔离账
标识）、strategy_branch（-008 复盘钩子 compare_reviews 输出格式）、
event_receipts（-007 四类回执完整度 completeness）、combo_registry（-003
四元组注册表）、decision_snapshot（-004 快照表，只读统计）。

未接线说明（汇报口径）：
  - **后验收益自动配对**（快照 run_ref → 结算后验 → 组合收益自动回填）属
    后续工单；当前只有**人工/显式喂入**一条路径（posterior 参数），喂入
    即录来源与单位，报表不猜测收益；
  - **成本分配到分支**不做——成本是组合维度（-011 登记口径 combo_id），
    摊到分支会造数，故只有组合级 ROI，无分支级 ROI；
  - 真实 token 计量（-011 亦为模拟计费）未接，报表只呈现成本账既有口径。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import combo_factory
import combo_registry
import event_receipts

REVIEW_SCHEMA_VERSION = 1
COMPARE_SCHEMA_VERSION = 1

# 成本账本（-011）预算字段名（复用 -011 常量，不复制字面量）
BUDGET_FIELD = combo_factory.BUDGET_FIELD

# 成本单位：-011 成本账为 LLM 成本金额（llm_cost 定价锚点为人民币/百万
# token），故组合级 ROI 的成本侧单位固定 CNY；收益侧单位不匹配即不计算。
COST_UNIT = "CNY"
REVENUE_UNIT_ALIASES = frozenset({"cny", "rmb", "yuan", "元", "人民币"})

# ROI 三态（不造数）
ROI_COMPUTED = "computed"
ROI_PENDING_BACKFILL = "pending_backfill"
ROI_UNIT_MISMATCH = "unit_mismatch"

# 横向对比排序口径
SORT_ROI = "roi"
SORT_COST = "cost"
SORT_COMPLETENESS = "completeness"


class ComboReviewError(Exception):
    """复盘报表契约违例（closed on failure）。携带 reason_code 供归因。"""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


# ---------- 注册表解析（-003，只读） ----------

def _resolve_entry(registry_path, combo_id: str) -> dict:
    """解析组合注册条目。未注册/注册表非法 → 显式抛错（closed on failure）。"""
    if not isinstance(combo_id, str) or not combo_id.strip():
        raise ComboReviewError("combo_id_invalid",
                               f"combo_id 须为非空字符串: {combo_id!r}")
    try:
        reg = combo_registry.load_registry(registry_path)
    except combo_registry.ComboRegistryError as e:
        raise ComboReviewError("registry_invalid", str(e)) from e
    try:
        return combo_registry.resolve(reg, combo_id)
    except combo_registry.ComboNotFound as e:
        raise ComboReviewError(
            "combo_not_registered",
            f"combo_id 未注册，无复盘口径（closed on failure）: {combo_id}") from e


# ---------- 成本维度（-011 成本账，只读） ----------

def _month_of(ts: str) -> str:
    """归月口径：ISO 时刻前 7 位即 YYYY-MM（与 -011 combo_budget_status 同口径）。

    本地实现而非引用 -011 私有函数——跨模块依赖私有 API 会随对方改动破裂；
    口径一致性由本单单测（成本维度与 -011 账本逐位相等）保证。"""
    return str(ts or "")[:7]


def _ledger_accumulated(ledger_path: Path, combo_id: str, month: str) -> float:
    """直读 -011 成本账累计当月成本（预算未登记时的兜底读法）。

    与 -011 combo_budget_status 同口径：按 ts 的 YYYY-MM 归月、只认
    kind=llm_cost、combo_id 匹配。行损坏 → 显式抛错（不静默跳过）。"""
    total = 0.0
    if not ledger_path.exists():
        return 0.0
    for lineno, line in enumerate(ledger_path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except ValueError as err:
            raise ComboReviewError(
                "ledger_corrupt",
                f"成本账第 {lineno} 行不是合法 JSON（closed on failure）: {ledger_path}") from err
        if not isinstance(e, dict):
            raise ComboReviewError(
                "ledger_corrupt",
                f"成本账第 {lineno} 行不是对象（closed on failure）: {ledger_path}")
        if e.get("combo_id") != combo_id or e.get("kind") != "llm_cost":
            continue
        if _month_of(e.get("ts", "")) != month:
            continue
        try:
            total += float(e.get("cost") or 0.0)
        except (TypeError, ValueError) as err:
            raise ComboReviewError(
                "ledger_corrupt",
                f"成本账第 {lineno} 行 cost 非法: {e.get('cost')!r}") from err
    return round(total, 6)


def _cost_dimension(combo_id: str, entry: dict, registry_path, ledger, month: str) -> dict:
    """成本维度：预算 / 当月累计 / 剩余 / over_budget（读 -011，不改账）。

    预算已登记（工厂创建的组合）→ 复用 -011 combo_budget_status（口径唯
    一来源）；预算未登记（-003 早期条目）→ 成本累计照实读账本，预算/剩余/
    over_budget 记 null（不臆造预算）。"""
    ledger_path = Path(ledger) if ledger is not None else combo_factory.DEFAULT_BUDGET_LEDGER
    budget = entry.get("budget")
    has_budget = isinstance(budget, dict) and BUDGET_FIELD in budget
    if has_budget:
        try:
            st = combo_factory.combo_budget_status(
                combo_id, registry_path=registry_path, ledger=ledger_path, month=month)
        except combo_registry.ComboNotFound as e:
            raise ComboReviewError("combo_not_registered", str(e)) from e
        except json.JSONDecodeError as e:
            raise ComboReviewError(
                "ledger_corrupt",
                f"成本账含非法 JSON 行（closed on failure）: {ledger_path}") from e
        except (TypeError, ValueError, KeyError) as e:
            raise ComboReviewError(
                "ledger_corrupt",
                f"成本账读取失败（closed on failure）: {ledger_path} ({e})") from e
        return {"status": st["status"], "month": st["month"],
                "budget": st[BUDGET_FIELD], "cost_accumulated": st["cost_accumulated"],
                "remaining": st["remaining"], "over_budget": st["over_budget"],
                "budget_registered": True, "unit": COST_UNIT,
                "ledger_path": str(ledger_path)}
    # 预算未登记：只报成本累计（照实），预算侧一律 null
    return {"status": "budget_not_registered", "month": month,
            "budget": None, "cost_accumulated": _ledger_accumulated(ledger_path, combo_id, month),
            "remaining": None, "over_budget": None,
            "budget_registered": False, "unit": COST_UNIT,
            "ledger_path": str(ledger_path)}


# ---------- 收益维度（人工/显式喂入后验；未接入即 pending_backfill） ----------

def _revenue_dimension(posterior) -> dict:
    """收益维度：后验收益（显式喂入）。未喂入 → pending_backfill（不造数）。

    喂入契约（posterior 参数）：
      {"unit": "CNY",                       # 必填：收益单位（与成本 CNY 相减）
       "source": "manual_feed:xxx",        # 建议填：来源留痕
       "branches": {"follow_research": 12.0, ...},   # 逐分支收益（金额口径）
       "total": 17.9}                      # 可选：组合级总收益（缺省=分支求和）
    """
    if posterior is None:
        return {"status": ROI_PENDING_BACKFILL, "unit": None, "source": None,
                "total": None, "branches": {}, "total_source": None,
                "note": "后验收益未接入（结算后验自动配对属后续工单，当前仅人工/显式喂入）"}
    if not isinstance(posterior, dict):
        raise ComboReviewError("posterior_invalid",
                               f"posterior 须为 dict 或 None: {type(posterior).__name__}")
    unit = posterior.get("unit")
    if not isinstance(unit, str) or not unit.strip():
        raise ComboReviewError("posterior_invalid",
                               "posterior.unit 必填（非空字符串）——收益单位不声明即不造数")
    branches = posterior.get("branches") or {}
    if not isinstance(branches, dict):
        raise ComboReviewError("posterior_invalid",
                               f"posterior.branches 须为 dict: {type(branches).__name__}")
    clean_branches = {}
    for bid, v in branches.items():
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ComboReviewError(
                "posterior_invalid",
                f"posterior.branches[{bid!r}] 须为数值: {v!r}")
        clean_branches[bid] = round(float(v), 6)
    total = posterior.get("total")
    if total is not None:
        if not isinstance(total, (int, float)) or isinstance(total, bool):
            raise ComboReviewError("posterior_invalid",
                                   f"posterior.total 须为数值或 None: {total!r}")
        total = round(float(total), 6)
        total_source = "explicit"
    else:
        if not clean_branches:
            raise ComboReviewError(
                "posterior_invalid",
                "posterior 须给 branches 或 total（二者皆空即无数据，不得当作 0）")
        total = round(sum(clean_branches.values()), 6)
        total_source = "sum_of_branches"
    norm_unit = COST_UNIT if unit.strip().lower() in REVENUE_UNIT_ALIASES else unit.strip()
    return {"status": "available", "unit": norm_unit,
            "source": posterior.get("source"), "total": total,
            "branches": clean_branches, "total_source": total_source,
            "note": None}


# ---------- ROI（计划 3.3 口径：收益 − 算力成本） ----------

def _roi(revenue: dict, cost: dict) -> dict:
    """组合 ROI = 收益 − 算力成本。数据不足即不出数（不造数）。"""
    out = {"formula": "revenue - compute_cost", "unit": COST_UNIT,
           "revenue": None, "cost": None, "value": None,
           "status": ROI_PENDING_BACKFILL, "note": None}
    if revenue["total"] is None:
        out["note"] = "后验收益缺失 → ROI 待回填（pending_backfill），不以 0 充数"
        out["cost"] = cost["cost_accumulated"]   # 成本侧已实，照实填
        return out
    if revenue["unit"] != COST_UNIT:
        out["status"] = ROI_UNIT_MISMATCH
        out["note"] = (f"收益单位 {revenue['unit']!r} 与算力成本单位 {COST_UNIT!r} "
                       "不可直接相减 → 不出数（不造数）")
        out["revenue"] = revenue["total"]
        out["cost"] = cost["cost_accumulated"]
        return out
    out["status"] = ROI_COMPUTED
    out["revenue"] = revenue["total"]
    out["cost"] = cost["cost_accumulated"]
    out["value"] = round(revenue["total"] - cost["cost_accumulated"], 6)
    out["note"] = None
    return out


# ---------- 收益维度输入：-008 compare_reviews 输出（只读消费） ----------

def _normalize_reviews(reviews) -> list[dict]:
    """归一化 -008 compare_reviews 输出（多信号 → 列表）。

    接受：单条 compare_reviews 结果 dict，或其列表。字段缺失/结构非法 →
    显式抛错（closed on failure），不静默忽略。"""
    if reviews is None:
        return []
    items = reviews if isinstance(reviews, list) else [reviews]
    out = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ComboReviewError("reviews_invalid",
                                   f"reviews[{i}] 须为 dict（-008 compare_reviews 输出）: "
                                   f"{type(item).__name__}")
        sid = item.get("signal_id")
        if not isinstance(sid, str) or not sid.strip():
            raise ComboReviewError("reviews_invalid",
                                   f"reviews[{i}] 缺 signal_id（-008 输出必带）")
        br = item.get("reviews")
        if not isinstance(br, dict):
            raise ComboReviewError("reviews_invalid",
                                   f"reviews[{i}].reviews 须为 dict（-008 输出必带）")
        for bid, rv in br.items():
            if not isinstance(rv, dict):
                raise ComboReviewError("reviews_invalid",
                                       f"reviews[{i}].reviews[{bid!r}] 须为 dict")
        out.append({"signal_id": sid, "reviews": dict(br),
                    "comparison_ready": bool(item.get("comparison_ready"))})
    return out


def _branch_decisions(review_items: list[dict], posterior: dict) -> list[dict]:
    """逐分支决策对比（-008 复盘钩子输出 + 后验收益挂载）。"""
    per_branch: dict[str, dict] = {}
    for item in review_items:
        for bid, rv in item["reviews"].items():
            row = per_branch.setdefault(bid, {
                "branch_id": bid, "decisions": 0, "executed": 0, "abstained": 0,
                "signals": [], "reason_codes": [], "plan_versions": [],
                "deviation_types": []})
            row["decisions"] += 1
            action = rv.get("decided_action")
            if action == "execute":
                row["executed"] += 1
            elif action == "abstain":
                row["abstained"] += 1
            row["signals"].append({"signal_id": item["signal_id"],
                                   "decided_action": action,
                                   "action": rv.get("action"),
                                   "reason_code": rv.get("reason_code"),
                                   "plan_version": rv.get("plan_version"),
                                   "basis_chain": rv.get("basis_chain") or [],
                                   "deviation": rv.get("deviation")})
            if rv.get("reason_code"):
                row["reason_codes"].append(rv["reason_code"])
            if rv.get("plan_version"):
                row["plan_versions"].append(rv["plan_version"])
            dev = rv.get("deviation")
            if isinstance(dev, dict) and dev.get("type"):
                row["deviation_types"].append(dev["type"])
    for bid, row in per_branch.items():
        row["posterior_revenue"] = posterior["branches"].get(bid)
        row["posterior_status"] = ("available" if row["posterior_revenue"] is not None
                                   else ROI_PENDING_BACKFILL)
    return [per_branch[b] for b in sorted(per_branch)]


# ---------- 回执维度（-007 completeness，只读） ----------

def _receipts_dimension(receipts_path: Path, signal_ids: list[str]) -> dict:
    """回执完整度：逐信号四类回执达成情况 + 4/4 达成率（-007 completeness）。"""
    per_signal = []
    full = 0
    for sid in signal_ids:
        try:
            c = event_receipts.completeness(receipts_path, sid)
        except event_receipts.ReceiptError as e:
            raise ComboReviewError(
                "receipts_ledger_error",
                f"回执账读取失败（closed on failure）: {receipts_path} ({e})") from e
        is_full = c["received_count"] == c["total"] and c["total"] == len(event_receipts.RECEIPT_KINDS)
        full += 1 if is_full else 0
        per_signal.append({"signal_id": sid, "received": c["received"],
                           "missing": c["missing"], "received_count": c["received_count"],
                           "total": c["total"], "complete_4_of_4": is_full})
    rate = round(full / len(per_signal), 6) if per_signal else None
    return {"log_path": str(receipts_path), "signals_total": len(per_signal),
            "signals_full_4_of_4": full, "rate_4_of_4": rate,
            "per_signal": per_signal,
            "receipt_kinds": list(event_receipts.RECEIPT_KINDS)}


# ---------- 快照维度（-004 快照表，只读统计） ----------

def _snapshots_dimension(snapshot_db, combo_id: str) -> dict:
    """快照统计：decision_snapshots 表中该组合的快照数（只读）。

    快照库未注入 / 表不存在 / 库不可读 → available=False 并给出原因（不造数）。"""
    if snapshot_db is None:
        return {"available": False, "reason": "snapshot_db_not_provided",
                "count": None, "by_account": {}}
    p = Path(snapshot_db)
    if not p.exists():
        return {"available": False, "reason": "snapshot_db_missing",
                "count": None, "by_account": {}}
    uri = f"file:{p.resolve().as_posix()}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        return {"available": False, "reason": f"snapshot_db_unreadable: {e}",
                "count": None, "by_account": {}}
    try:
        row = con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                          "AND name='decision_snapshots'").fetchone()
        if row is None:
            return {"available": False, "reason": "snapshot_table_missing",
                    "count": None, "by_account": {}}
        rows = con.execute(
            "SELECT account_id, COUNT(*) FROM decision_snapshots WHERE combo_id=? "
            "GROUP BY account_id", (combo_id,)).fetchall()
    except sqlite3.Error as e:
        return {"available": False, "reason": f"snapshot_query_failed: {e}",
                "count": None, "by_account": {}}
    finally:
        con.close()
    by_account = {str(a): int(n) for a, n in rows}
    return {"available": True, "reason": None,
            "count": sum(by_account.values()), "by_account": by_account}


# ---------- 主入口：build_review（组合复盘报表） ----------

def build_review(combo_id: str, registry_path=None, ledger=None, receipts=None,
                 reviews=None, posterior=None, month: str | None = None,
                 snapshot_db=None) -> dict:
    """产出组合复盘报表（JSON dict；Markdown 见 render_markdown）。

    参数：
      combo_id       组合 id（必须已注册，否则显式抛错）
      registry_path  -003 注册表路径（None=默认）
      ledger         -011 成本账路径（None=默认）
      receipts       -007 回执账路径（None=默认）
      reviews        -008 compare_reviews 输出（单条或列表；None=无信号复盘）
      posterior      后验收益（人工/显式喂入；None → ROI pending_backfill）
      month          成本归月 YYYY-MM（None=当月）
      snapshot_db    -004 快照库路径（可选；None 则快照统计不可用）

    失败语义（closed on failure）：组合未注册/注册表非法/成本账损坏/回执账
    损坏/复盘输入结构非法/后验喂入非法 → 显式抛 ComboReviewError（带
    reason_code），禁静默降级、禁填 0 充数。
    """
    entry = _resolve_entry(registry_path, combo_id)
    cur_month = month or datetime.now().isoformat(timespec="seconds")[:7]
    review_items = _normalize_reviews(reviews)
    revenue = _revenue_dimension(posterior)
    cost = _cost_dimension(combo_id, entry, registry_path, ledger, cur_month)
    roi = _roi(revenue, cost)

    receipts_path = Path(receipts) if receipts is not None else event_receipts.DEFAULT_RECEIPT_LOG
    signal_ids = [it["signal_id"] for it in review_items]
    receipts_dim = _receipts_dimension(receipts_path, signal_ids)
    snapshots_dim = _snapshots_dimension(snapshot_db, combo_id)

    # 隔离账标识：-011 口径 combo@分支（已注册分支 + 本次复盘出现的分支）
    branches = _branch_decisions(review_items, revenue)
    account_ids = dict(combo_factory.branch_account_ids(combo_id))
    for row in branches:
        account_ids.setdefault(row["branch_id"], f"{combo_id}@{row['branch_id']}")
    return {
        "review_schema_version": REVIEW_SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "combo_id": combo_id,
        "combo": {"combo_id": combo_id, "name": entry.get("name"),
                  "quad": entry.get("quad"),
                  "registry_schema_version": combo_registry.REGISTRY_SCHEMA_VERSION},
        "account_ids": account_ids,
        "revenue": revenue,
        "cost": cost,
        "roi": roi,
        "branches": branches,
        "receipts": receipts_dim,
        "snapshots": snapshots_dim,
        "review_consumers": list(combo_factory.REVIEW_CONSUMERS),
        "notes": [
            "ROI 口径：收益 − 算力成本（计划 3.3）",
            "后验收益未接入时 ROI 显式标 pending_backfill（不以 0 充数）",
            "成本为组合维度（-011 登记口径），不摊到分支（不造数）",
        ],
    }


# ---------- 横向对比：compare_portfolios（供 009 汇报器消费） ----------

def _sort_key(row: dict, sort_by: str):
    """排序键：ROI 降序（pending_backfill 殿后）/ 成本升序 / 达成率降序。"""
    roi = row["roi"]
    if sort_by == SORT_COST:
        return (row["cost_accumulated"], _roi_rank_key(roi), row["combo_id"])
    if sort_by == SORT_COMPLETENESS:
        rate = row["rate_4_of_4"]
        return (-(rate if rate is not None else -1.0), row["cost_accumulated"],
                _roi_rank_key(roi), row["combo_id"])
    # 默认 ROI：算出数的降序在前，待回填的殿后
    rate = row["rate_4_of_4"]
    return (_roi_rank_key(roi), row["cost_accumulated"],
            -(rate if rate is not None else -1.0), row["combo_id"])


def _roi_rank_key(roi: dict):
    """ROI 排序内键：computed → (-value, 0)；未算出 → (0, 1) 殿后。"""
    if roi["status"] == ROI_COMPUTED and roi["value"] is not None:
        return (-float(roi["value"]), 0)
    return (0.0, 1)


def compare_portfolios(combo_ids, registry_path=None, ledger=None, receipts=None,
                       reviews_by_combo=None, posterior_by_combo=None, month=None,
                       snapshot_db=None, sort_by: str = SORT_ROI) -> dict:
    """多组合横向对比：按 ROI / 成本 / 达成率排序（供 009 汇报器消费）。

    reviews_by_combo / posterior_by_combo 均为 {combo_id: ...} 映射：
      reviews_by_combo[combo_id]  → -008 compare_reviews 输出（单条或列表）
      posterior_by_combo[combo_id] → 后验收益（人工/显式喂入；缺 → ROI
                                     pending_backfill）
    sort_by："roi"（默认，ROI 降序、待回填殿后）/ "cost"（成本升序）/
            "completeness"（4/4 达成率降序）。
    组合未注册/账本损坏等 → 显式抛错（closed on failure），不静默跳过某组合。
    """
    if sort_by not in (SORT_ROI, SORT_COST, SORT_COMPLETENESS):
        raise ComboReviewError("sort_by_invalid",
                               f"sort_by 只认 {SORT_ROI}/{SORT_COST}/{SORT_COMPLETENESS}: {sort_by!r}")
    if not isinstance(combo_ids, (list, tuple)) or not combo_ids:
        raise ComboReviewError("combo_ids_invalid",
                               "combo_ids 须为非空列表（组合 id 列表）")
    reviews_by_combo = reviews_by_combo or {}
    posterior_by_combo = posterior_by_combo or {}
    rows = []
    for cid in combo_ids:
        rev = build_review(cid, registry_path=registry_path, ledger=ledger,
                           receipts=receipts, reviews=reviews_by_combo.get(cid),
                           posterior=posterior_by_combo.get(cid), month=month,
                           snapshot_db=snapshot_db)
        rows.append({
            "combo_id": cid,
            "name": rev["combo"]["name"],
            "roi": rev["roi"],
            "roi_status": rev["roi"]["status"],
            "roi_value": rev["roi"]["value"],
            "revenue": rev["revenue"]["total"],
            "revenue_status": rev["revenue"]["status"],
            "cost_accumulated": rev["cost"]["cost_accumulated"],
            "budget": rev["cost"]["budget"],
            "remaining": rev["cost"]["remaining"],
            "over_budget": rev["cost"]["over_budget"],
            "cost_status": rev["cost"]["status"],
            "rate_4_of_4": rev["receipts"]["rate_4_of_4"],
            "signals_total": rev["receipts"]["signals_total"],
        })
    rows.sort(key=lambda r: _sort_key(r, sort_by))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {"compare_schema_version": COMPARE_SCHEMA_VERSION,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "sort_by": sort_by, "count": len(rows), "rows": rows}


# ---------- Markdown 渲染（同一份数据，两种呈现） ----------

def _fmt(v, digits: int = 4):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{digits}f}".rstrip("0").rstrip(".") or "0"
    return str(v)


def render_markdown(review: dict) -> str:
    """组合复盘报表 → Markdown（009 汇报器可直接贴）。"""
    roi = review["roi"]
    cost = review["cost"]
    rev = review["revenue"]
    lines = [
        f"# 组合复盘 · {review['combo_id']}",
        "",
        f"- 组合名称：{review['combo']['name']}",
        f"- 报表生成：{review['generated_at']}（schema v{review['review_schema_version']}）",
        "",
        "## 一、收益维度（分支决策对比）",
        "",
    ]
    if not review["branches"]:
        lines.append("（无 -008 复盘输入：本次未喂入 compare_reviews 输出）")
    else:
        lines += ["| 分支 | 隔离账 | 决策 | 执行 | 放弃 | 原因码 | 后验收益 |",
                  "|---|---|---|---|---|---|---|"]
        for b in review["branches"]:
            lines.append(
                f"| {b['branch_id']} | {review['account_ids'].get(b['branch_id'], '—')} | "
                f"{b['decisions']} | {b['executed']} | {b['abstained']} | "
                f"{', '.join(sorted(set(b['reason_codes']))) or '—'} | "
                f"{_fmt(b['posterior_revenue'])} |")
    lines += [
        "",
        f"- 组合收益合计：{_fmt(rev['total'])}（单位 {rev['unit'] or '—'}，"
        f"状态 {rev['status']}，来源 {rev['source'] or '—'}）",
        "",
        "## 二、成本维度（当月 LLM 成本）",
        "",
        f"- 归月：{cost['month']}｜预算：{_fmt(cost['budget'])}｜"
        f"当月累计：{_fmt(cost['cost_accumulated'])}｜剩余：{_fmt(cost['remaining'])}",
        f"- 状态：{cost['status']}（over_budget={cost['over_budget']}，"
        f"预算已登记={cost['budget_registered']}）",
        "",
        "## 三、ROI（收益 − 算力成本）",
        "",
        f"- 状态：**{roi['status']}**｜收益 {_fmt(roi['revenue'])} − 成本 "
        f"{_fmt(roi['cost'])} = **{_fmt(roi['value'])}**",
    ]
    if roi.get("note"):
        lines.append(f"- 说明：{roi['note']}")
    lines += [
        "",
        "## 四、回执完整度（-007 四类回执）",
        "",
        f"- 信号数：{review['receipts']['signals_total']}｜4/4 达成："
        f"{review['receipts']['signals_full_4_of_4']}｜达成率："
        f"{_fmt(review['receipts']['rate_4_of_4'])}",
        "",
        "## 五、快照统计",
        "",
        f"- 可用：{review['snapshots']['available']}｜快照数："
        f"{_fmt(review['snapshots']['count'])}｜原因：{review['snapshots']['reason'] or '—'}",
        "",
        "> 不造数原则：后验收益缺失时 ROI 显式标 pending_backfill，不以 0 充数；"
        "成本为组合维度，不摊到分支。",
    ]
    return "\n".join(lines)


def render_comparison_markdown(comparison: dict) -> str:
    """横向对比 → Markdown（009 汇报器可直接贴）。"""
    lines = [f"# 组合横向对比（{comparison['count']} 个组合）", "",
             f"- 排序口径：{comparison['sort_by']}（ROI 降序 / 成本升序 / 4-4 达成率降序）",
             "", "| # | 组合 | ROI | 收益 | 当月成本 | 预算 | 剩余 | 达成率 | ROI 状态 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in comparison["rows"]:
        lines.append(
            f"| {r['rank']} | {r['combo_id']} | {_fmt(r['roi_value'])} | "
            f"{_fmt(r['revenue'])} | {_fmt(r['cost_accumulated'])} | {_fmt(r['budget'])} | "
            f"{_fmt(r['remaining'])} | {_fmt(r['rate_4_of_4'])} | {r['roi_status']} |")
    return "\n".join(lines)
