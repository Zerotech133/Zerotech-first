# -*- coding: utf-8 -*-
"""司马懿汇报器生成器（WO-QT-20261020-014，缺口 E-2 汇报器，**DRYRUN 档**）。

背景：009（E-2 汇报器）原依赖 1.3 HK 灰度观测起算，今晨 automation 69b51985
失败（`~/.workbuddy/logs/automation.log`：`2026-10-20T00:35:01Z run failed for
69b51985 … [CANCELLED] Automation prompt interrupted: unknown_interrupted`），
1.3 起算日顺延 → 009 继续等会空转。故 009 **拆两单**：

  - **本单（-014）= 生成器**：产出汇报正文（Markdown + JSON），**零真实出口**；
  - **015 = 真实推送**：收件人分域 / 订阅闸门 / 零真实出口铁律落地，**BLOCK 待
    老田授权**。

汇报口径五段（老田定纲：司马懿唯一对外；板块内组件不得直连对外出口；汇报不含
实盘）：

| 段 | 标题 | 口径来源（只读消费） |
|---|---|---|
| 一 | 本期研究了什么 | -006 `plan_meeting_ledger`（预案版本数 / 会议场次 / 分歧率） |
| 二 | 模拟达到什么 | -013 `combo_review.build_review`（收益 / 成本 / ROI 三态） |
| 三 | 各分支状态对比 | -008 `compare_reviews`（执行 / 放弃 / 分歧留痕）+ -007 `completeness`（4/4 达成率） |
| 四 | 成本与预算 | -011 `combo_budget_status` 口径（预算 / 当月累计 / 剩余 / 超预算） |
| 五 | 红黄绿告警 | -0.2 `acceptance_check` 每日三查分级输出（只读消费 findings） |

**不造数原则（硬约束）**：
  - **ROI 三态直通**：读 -013 的 `roi_status`，`pending_backfill` / `unit_mismatch`
    一律**原样透出**，绝不把 null 当 0 渲染成「打平」（0 会被读成「白干且不亏」）；
  - 预算未登记 → 预算/剩余/超预算记 null 并标 `budget_not_registered`，不臆造；
  - 分歧率在会议场次为 0 时记 null（不以 0 充数）；
  - 数据缺失的章节**显式标注**（未接入 / 无数据），**禁静默省略章节**——五段
    标题恒定出现，宁可写「未接入」也不让读者误以为「本期无异常」。

**DRYRUN 边界（本单铁律）**：
  - 本模块**只生成文本**，不推送任何出口：头部恒带
    `DRYRUN — 未推送真实出口（015 待授权）`；
  - 零真实出口：stdlib-only（无网络库），全链路在网络出口钳制下可跑通；
  - 输出结构**永远不含实盘字段**（`_assert_no_live_fields` 递归守卫，命中即抛
    `ReportError`）——防误用。

只读复用（不改他方一行业务码）：combo_review（-013 复盘/ROI 三态）、
combo_factory（-011 预算/成本口径）、strategy_branch（-008 compare_reviews 输出）、
event_receipts（-007 四类回执 completeness）、plan_meeting_ledger（-006 版本账/会议
账）、acceptance_check（-0.2 三查分级输出）。

未接线路径（如实挂账，见契约文档）：
  - **真实推送**：015 单，BLOCK 待老田授权（收件人分域 + 订阅闸门 + 零真实出口
    铁律）；本单产出的 payload 即 015 的输入形状。
  - **三查 findings 自动跑**：本单**只消费**调用方喂入的 findings，**不主动连
    paper.db**（生成器隐式触库会让「只读」退化为「不可预期的读」，且 -0.2 已有
    独立入口 `acceptance_check --daily`）；接线时由调用方跑一次三查再喂入。
  - **后验收益自动配对**：同 -013 挂账（当前仅人工/显式喂入）。
  - **1.3 HK 灰度起算日**：automation 69b51985 恢复后由 015 侧顺延，本单不估。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

import acceptance_check
import combo_review

REPORT_SCHEMA_VERSION = 1

# DRYRUN 标记（015 待授权）——Markdown 头部与 JSON payload 双处恒带
DRYRUN_NOTICE = "DRYRUN — 未推送真实出口（015 待授权）"
PENDING_PUSH_TICKET = "WO-QT-20261020-015"

# 五段顺序恒定（禁静默省略章节：渲染时五段标题恒出现）
SECTIONS = ("research", "simulation", "branches", "cost_budget", "alerts")

SECTION_TITLES = {
    "research": "一、本期研究了什么",
    "simulation": "二、模拟达到什么",
    "branches": "三、各分支状态对比",
    "cost_budget": "四、成本与预算",
    "alerts": "五、红黄绿告警",
}

# 章节可用性三态
STATUS_OK = "ok"                    # 有数据
STATUS_NOT_WIRED = "not_wired"      # 未接入（未喂入数据源）
STATUS_NO_DATA = "no_data"          # 已接入但源内无数据

STATUS_LABEL = {STATUS_NOT_WIRED: "未接入", STATUS_NO_DATA: "无数据"}

# ROI 三态（复用 -013 字面量，不另立口径）
ROI_STATUSES = frozenset({
    combo_review.ROI_COMPUTED,
    combo_review.ROI_PENDING_BACKFILL,
    combo_review.ROI_UNIT_MISMATCH,
})
ROI_DISPLAY_TOKEN = {
    combo_review.ROI_COMPUTED: "computed",
    combo_review.ROI_PENDING_BACKFILL: "pending_backfill",
    combo_review.ROI_UNIT_MISMATCH: "unit_mismatch",
}

# 告警分级：P0 红 / P1 黄 / P2 绿（-0.2 三查 severity 口径直通）
SEVERITY_LEVEL = {"P0": "red", "P1": "yellow", "P2": "green"}
SEVERITY_LEVEL_INV = {"red": "P0", "yellow": "P1", "green": "P2"}
LEVEL_ORDER = {"green": 0, "yellow": 1, "red": 2}
LEVEL_LABEL = {"red": "红", "yellow": "黄", "green": "绿"}
LEVEL_ALL_GREEN = "全绿"

# 三查项 → 告警类别（未知项归「其他三查项」，不臆造类别名）
CHECK_CATEGORY = {"ledger_freshness": "账本断线", "consumer_chain": "消费链停摆"}
CATEGORY_OTHER = "其他三查项"
GRAYSCALE_LABEL = "灰度"

# 实盘字段守卫：键名命中即抛错（防误用——汇报永远不含实盘）。
# 只查键名不查值：值里自然可能出现「realized」这类词，但键一旦叫 live_pnl /
# real_account / 实盘收益 就是口径越界，必须拒。
_LIVE_KEY_PATTERN = re.compile(
    r"(^|[_\-])(live|real|realized|broker|settled|actual)([_\-]|$)")
_LIVE_KEY_SUBSTRINGS = ("实盘",)


class ReportError(Exception):
    """汇报生成契约违例（closed on failure）。携带 reason_code 供归因。"""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


# ---------- 通用小工具 ----------

def _fmt(v, digits: int = 4):
    """数值渲染：None → 「—」（不填 0 充数）；浮点去尾零。"""
    if v is None:
        return "—"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        return f"{v:.{digits}f}".rstrip("0").rstrip(".") or "0"
    return str(v)


def _rate(part: int, whole: int):
    """比率：分母为 0 → None（不以 0 充数）。"""
    return round(part / whole, 6) if whole else None


def _month_of(ts) -> str:
    """归月口径：ISO 时刻前 7 位即 YYYY-MM（与 -011/-013 同口径）。"""
    return str(ts or "")[:7]


# ---------- 实盘字段守卫 ----------

def _assert_no_live_fields(obj, path: str = "$") -> None:
    """递归守卫：输出结构**永远不含实盘字段**。命中即抛（closed on failure）。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k)
            if _LIVE_KEY_PATTERN.search(key.lower()) or \
                    any(s in key for s in _LIVE_KEY_SUBSTRINGS):
                raise ReportError(
                    "live_field_forbidden",
                    f"汇报输出不得含实盘字段（防误用）: {path}.{key}")
            _assert_no_live_fields(v, f"{path}.{key}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _assert_no_live_fields(v, f"{path}[{i}]")


# ---------- 段落骨架 ----------

def _ok(reason: str | None = None, **payload) -> dict:
    """有数据章节。"""
    return {"status": STATUS_OK, "available": True, "reason": reason, **payload}


def _not_wired(reason: str) -> dict:
    """未接入章节：显式标注，绝不静默省略。"""
    return {"status": STATUS_NOT_WIRED, "available": False, "reason": reason}


def _no_data(reason: str, **payload) -> dict:
    """已接入但源内无数据章节。"""
    return {"status": STATUS_NO_DATA, "available": False, "reason": reason, **payload}


# ---------- 一、本期研究了什么（-006 计划与会议版本账，只读） ----------

def _research_section(plan_db, month: str | None) -> dict:
    """预案版本数 / 会议场次 / 分歧率（读 -006 两张 append-only 账）。

    分歧率口径（明示，不含糊）：**结论非 maintain（revise/veto）的会议占比**——
    即「分歧导致结论改动」的比例。-006 的 `dissent`（会前分歧）字段写入时必填非空，
    故不能用「有无 dissent」当分歧判据（恒为真 = 废指标）。

    只读：`mode=ro` 连接 + 纯 SELECT，**不调 ensure_table**（那是写路径）。
    库缺 / 表缺 → 无数据（不造数）；dissent_json 损坏 → 显式抛错。
    """
    if plan_db is None:
        return _not_wired("plan_db_not_provided")
    p = Path(plan_db)
    if not p.exists():
        return _no_data("plan_db_missing")
    uri = f"file:{p.resolve().as_posix()}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        return _no_data(f"plan_db_unreadable: {e}")
    try:
        con.row_factory = sqlite3.Row
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('plan_versions','meeting_records')").fetchall()}
        if not {"plan_versions", "meeting_records"} <= tables:
            return _no_data("plan_ledger_table_missing")
        pv = con.execute(
            "SELECT COUNT(*) AS n, COUNT(DISTINCT stock) AS stocks, "
            "COUNT(DISTINCT combo_id) AS combos, "
            "SUM(CASE WHEN prev_version IS NOT NULL THEN 1 ELSE 0 END) AS chained "
            "FROM plan_versions").fetchone()
        mr_rows = con.execute(
            "SELECT meeting_id, conclusion, dissent_json FROM meeting_records "
            "ORDER BY meeting_id").fetchall()
    except sqlite3.Error as e:
        raise ReportError("plan_ledger_query_failed",
                          f"-006 版本账查询失败（closed on failure）: {p} ({e})") from e
    finally:
        con.close()

    conclusions: dict[str, int] = {}
    dissent_items = 0
    for row in mr_rows:
        c = str(row["conclusion"])
        conclusions[c] = conclusions.get(c, 0) + 1
        try:
            dissent = json.loads(row["dissent_json"])
        except (TypeError, ValueError) as e:
            raise ReportError(
                "dissent_corrupt",
                f"会议 {row['meeting_id']} 的 dissent_json 非法（closed on failure）: "
                f"{row['dissent_json']!r}") from e
        if not isinstance(dissent, list):
            raise ReportError("dissent_corrupt",
                              f"会议 {row['meeting_id']} 的 dissent_json 非 list")
        dissent_items += len(dissent)

    meetings_total = len(mr_rows)
    divergent = sum(v for k, v in conclusions.items() if k != "maintain")
    plan_versions_total = int(pv["n"] or 0)
    if plan_versions_total == 0 and meetings_total == 0:
        return _no_data("plan_ledger_empty", plan_db=str(p), month=month,
                        plan_versions_total=0, plan_stocks_total=0,
                        plan_combos_total=0, plan_chained_total=0,
                        meetings_total=0, conclusions={}, divergence_total=0,
                        divergence_rate=None, dissent_items_total=0)
    return _ok(
        plan_db=str(p),
        month=month,
        plan_versions_total=plan_versions_total,
        plan_stocks_total=int(pv["stocks"] or 0),
        plan_combos_total=int(pv["combos"] or 0),
        plan_chained_total=int(pv["chained"] or 0),
        meetings_total=meetings_total,
        conclusions=dict(sorted(conclusions.items())),
        divergence_total=divergent,
        divergence_rate=_rate(divergent, meetings_total),
        divergence_formula="conclusions(revise+veto) / meetings_total",
        dissent_items_total=dissent_items,
        divergence_note=(None if meetings_total else
                         "会议场次为 0 → 分歧率不可计算（不以 0 充数）"))


# ---------- 二、模拟达到什么（-013 复盘，只读） ----------

def _roi_display(roi: dict) -> dict:
    """ROI 呈现：算出数才给数值；未算出**原样透出状态词**，绝不渲染 0。

    roi_status 白名单外的异常状态 → 透出原值并标注（不臆断其语义）。"""
    status = roi.get("status")
    if status == combo_review.ROI_COMPUTED and roi.get("value") is not None:
        return {"status": status, "value": _fmt(roi["value"]),
                "display": _fmt(roi["value"]), "is_number": True}
    if status in ROI_STATUSES:
        return {"status": status, "value": None,
                "display": ROI_DISPLAY_TOKEN[status], "is_number": False}
    return {"status": status, "value": None,
            "display": f"{status}(未登记口径)", "is_number": False}


def _simulation_section(reviews_by_id: dict, combo_order: list[str]) -> dict:
    """各组合收益 / 成本 / ROI（读 -013 build_review 产物，只读重组）。"""
    combos = []
    counts: dict[str, int] = {}
    for cid in combo_order:
        rev = reviews_by_id[cid]
        roi = rev["roi"]
        rd = _roi_display(roi)
        counts[rd["status"]] = counts.get(rd["status"], 0) + 1
        combos.append({
            "combo_id": cid,
            "name": rev["combo"].get("name"),
            "roi": rd,
            "roi_note": roi.get("note"),
            "revenue": rev["revenue"].get("total"),
            "revenue_status": rev["revenue"].get("status"),
            "revenue_unit": rev["revenue"].get("unit"),
            "cost_accumulated": rev["cost"].get("cost_accumulated"),
            "cost_unit": rev["cost"].get("unit"),
        })
    return _ok(
        combos=combos,
        combo_count=len(combos),
        roi_status_counts=dict(sorted(counts.items())),
        pending_backfill_count=counts.get(combo_review.ROI_PENDING_BACKFILL, 0),
        unit_mismatch_count=counts.get(combo_review.ROI_UNIT_MISMATCH, 0),
        roi_note="ROI 未算出时原样透出 pending_backfill / unit_mismatch，绝不填 0")


# ---------- 三、各分支状态对比（-008 + -007，只读） ----------

def _branches_section(reviews_by_id: dict, combo_order: list[str]) -> dict:
    """跟随 vs 激进：执行 / 放弃 / 分歧留痕数 + 四类回执 4/4 达成率。

    跨组合按 branch_id 聚合（同一分支在不同组合的留痕合并计数）；分歧留痕
    口径 = 该决策带 deviation 记录（-008 输出 deviation 非空）。
    """
    per_branch: dict[str, dict] = {}
    signals_total = 0
    signals_full = 0
    receipt_kinds: list[str] = []
    for cid in combo_order:
        rev = reviews_by_id[cid]
        rc = rev["receipts"]
        signals_total += rc.get("signals_total") or 0
        signals_full += rc.get("signals_full_4_of_4") or 0
        if not receipt_kinds:
            receipt_kinds = list(rc.get("receipt_kinds") or [])
        for b in rev.get("branches") or []:
            row = per_branch.setdefault(b["branch_id"], {
                "branch_id": b["branch_id"], "combos": [], "decisions": 0,
                "executed": 0, "abstained": 0, "divergence_traces": 0,
                "reason_codes": {}, "plan_versions": [],
                "posterior_available": 0, "posterior_pending_backfill": 0})
            row["combos"].append(cid)
            row["decisions"] += b.get("decisions") or 0
            row["executed"] += b.get("executed") or 0
            row["abstained"] += b.get("abstained") or 0
            for sig in b.get("signals") or []:
                if sig.get("deviation"):
                    row["divergence_traces"] += 1
                if sig.get("reason_code"):
                    rc_key = sig["reason_code"]
                    row["reason_codes"][rc_key] = row["reason_codes"].get(rc_key, 0) + 1
                if sig.get("plan_version"):
                    row["plan_versions"].append(sig["plan_version"])
            if b.get("posterior_revenue") is not None:
                row["posterior_available"] += 1
            else:
                row["posterior_pending_backfill"] += 1
    rows = []
    for bid in sorted(per_branch):
        row = per_branch[bid]
        row["combos"] = sorted(set(row["combos"]))
        row["plan_versions"] = sorted(set(row["plan_versions"]))
        row["reason_codes"] = dict(sorted(row["reason_codes"].items()))
        rows.append(row)
    return _ok(
        branches=rows,
        branch_count=len(rows),
        divergence_traces_total=sum(r["divergence_traces"] for r in rows),
        divergence_formula="分支带 deviation 记录的决策数",
        receipts={
            "kinds": receipt_kinds,
            "signals_total": signals_total,
            "signals_full_4_of_4": signals_full,
            "rate_4_of_4": _rate(signals_full, signals_total),
        })


# ---------- 四、成本与预算（-011 combo_budget_status 口径，只读） ----------

def _cost_section(reviews_by_id: dict, combo_order: list[str], month: str | None) -> dict:
    """成本与预算：逐组合预算 / 当月累计 / 剩余 / 超预算 + 合计。

    预算未登记（-003 早期条目无 budget）→ budget/remaining/over_budget 记 null
    并计入 unregistered，**不臆造预算值**；合计里 `budget` 亦记 null（不可把
    部分组合的预算之和当全量预算）。
    """
    rows = []
    total_cost = 0.0
    budget_sum = 0.0
    budget_all_registered = True
    over_count = 0
    for cid in combo_order:
        c = reviews_by_id[cid]["cost"]
        total_cost += float(c.get("cost_accumulated") or 0.0)
        registered = bool(c.get("budget_registered"))
        if registered and c.get("budget") is not None:
            budget_sum += float(c["budget"])
        else:
            budget_all_registered = False
        if c.get("over_budget"):
            over_count += 1
        rows.append({
            "combo_id": cid,
            "month": c.get("month"),
            "budget": c.get("budget"),
            "cost_accumulated": c.get("cost_accumulated"),
            "remaining": c.get("remaining"),
            "over_budget": c.get("over_budget"),
            "status": c.get("status"),
            "budget_registered": registered,
            "unit": c.get("unit"),
        })
    unregistered = [r["combo_id"] for r in rows if not r["budget_registered"]]
    return _ok(
        month=month,
        rows=rows,
        totals={
            "cost_accumulated": round(total_cost, 6),
            "budget": round(budget_sum, 6) if budget_all_registered else None,
            "budget_complete": budget_all_registered,
            "over_budget_count": over_count,
            "unregistered_count": len(unregistered),
            "unregistered_combos": unregistered,
            "unit": rows[0]["unit"] if rows else None,
        },
        unregistered_note=(None if not unregistered else
                           f"预算未登记组合 {unregistered}：预算/剩余/超预算记 null，"
                           f"合计预算记 null（不臆造）"))


# ---------- 五、红黄绿告警（-0.2 每日三查分级输出，只读消费） ----------

def _alerts_section(findings) -> dict:
    """红黄绿告警行：P0 红 / P1 黄 / P2 绿，逐条透出三查项与账号。

    只读消费调用方喂入的 findings（-0.2 `run_daily_checks` 的第 0 元）——本单
    **不主动连 paper.db**（见模块文档「未接线路径」）。灰度异常：账号命中
    `acceptance_check.HK_ACCOUNTS` 的条目另打灰度标（口径引 -0.2，不另立名单）。
    """
    if findings is None:
        return _not_wired("findings_not_provided")
    if not isinstance(findings, list):
        raise ReportError("findings_invalid",
                          f"findings 须为 list 或 None: {type(findings).__name__}")
    rows = []
    counts: dict[str, int] = {}
    grayscale_count = 0
    for i, f in enumerate(findings):
        if not isinstance(f, dict):
            raise ReportError("findings_invalid", f"findings[{i}] 须为 dict")
        sev = str(f.get("severity"))
        counts[sev] = counts.get(sev, 0) + 1
        check = str(f.get("check"))
        account = str(f.get("account"))
        gray = account in acceptance_check.HK_ACCOUNTS
        if gray:
            grayscale_count += 1
        category = CHECK_CATEGORY.get(check, CATEGORY_OTHER)
        rows.append({
            "severity": sev,
            "level": SEVERITY_LEVEL.get(sev, "green"),
            "check": check,
            "account": account,
            "category": f"{GRAYSCALE_LABEL}/{category}" if gray else category,
            "grayscale": gray,
            "detail": f.get("detail"),
        })
    worst = "green"
    for r in rows:
        if LEVEL_ORDER[r["level"]] > LEVEL_ORDER[worst]:
            worst = r["level"]
    verdict = (f"{LEVEL_LABEL[worst]}（{SEVERITY_LEVEL_INV[worst]}）" if rows
               else LEVEL_ALL_GREEN)
    return _ok(
        rows=rows,
        finding_total=len(rows),
        counts=dict(sorted(counts.items())),
        p0_count=counts.get("P0", 0),
        highest_level=worst,
        verdict=verdict,
        grayscale_count=grayscale_count,
        source="acceptance_check.run_daily_checks findings（只读消费）")


# ---------- 主入口：build_report ----------

def build_report(combo_ids=None, *, plan_db=None, registry_path=None, ledger=None,
                 receipts=None, reviews_by_combo=None, posterior_by_combo=None,
                 findings=None, month: str | None = None, snapshot_db=None,
                 clock: str | None = None) -> dict:
    """产出司马懿汇报 payload（JSON dict；Markdown 见 `render_markdown`）。

    **DRYRUN**：本函数只组装文本，绝不推送任何出口。

    参数：
      combo_ids           组合 id 列表（空/None → 二三四段标「未接入」）
      plan_db             -006 版本账/会议账 sqlite 路径（只读 mode=ro）
      registry_path       -003 注册表路径（透传给 -013）
      ledger              -011 成本账路径（透传给 -013）
      receipts            -007 回执账路径（透传给 -013）
      reviews_by_combo    {combo_id: -008 compare_reviews 输出}（透传给 -013）
      posterior_by_combo  {combo_id: 后验收益}（透传给 -013；缺 → ROI 待回填）
      findings            -0.2 每日三查 findings（**只读消费**，见模块文档）
      month               成本归月 YYYY-MM（None=当月）
      snapshot_db         -004 快照库路径（透传给 -013；默认 None 不读）
      clock               报表生成时刻 ISO 文本（**可注入以保 roundtrip 可复现**）

    失败语义（closed on failure）：combo_ids 非法 / 上游 -013 抛错 / 三查
    findings 结构非法 / 版本账 dissent 损坏 / **输出命中实盘字段** → 显式抛
    `ReportError`（带 reason_code），禁静默降级、禁填 0 充数。
    """
    if combo_ids is not None and not isinstance(combo_ids, (list, tuple)):
        raise ReportError("combo_ids_invalid",
                          f"combo_ids 须为 list/tuple 或 None: {type(combo_ids).__name__}")
    order = [str(c) for c in (combo_ids or [])]
    generated_at = clock or datetime.now().isoformat(timespec="seconds")
    cur_month = month or _month_of(generated_at)

    reviews_by_id: dict[str, dict] = {}
    if order:
        for cid in order:
            try:
                reviews_by_id[cid] = combo_review.build_review(
                    cid, registry_path=registry_path, ledger=ledger, receipts=receipts,
                    reviews=(reviews_by_combo or {}).get(cid),
                    posterior=(posterior_by_combo or {}).get(cid),
                    month=cur_month, snapshot_db=snapshot_db)
            except combo_review.ComboReviewError as e:
                raise ReportError("upstream_review_failed",
                                  f"-013 复盘失败（closed on failure）: {cid} "
                                  f"[{e.reason_code}] {e}") from e

    no_combo = "no_combo_specified"
    sections = {
        "research": _research_section(plan_db, cur_month),
        "simulation": (_simulation_section(reviews_by_id, order) if order
                       else _not_wired(no_combo)),
        "branches": (_branches_section(reviews_by_id, order) if order
                     else _not_wired(no_combo)),
        "cost_budget": (_cost_section(reviews_by_id, order, cur_month) if order
                        else _not_wired(no_combo)),
        "alerts": _alerts_section(findings),
    }
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": generated_at,
        "dryrun": True,
        "notice": DRYRUN_NOTICE,
        "outlet": {
            "pushed": False,
            "target": None,
            "pending_ticket": PENDING_PUSH_TICKET,
            "note": "生成器零真实出口；推送待 015 授权",
        },
        "scope": {
            "kind": "simulation_only",
            "trading_mode": "simulation_only",
            "out_of_scope": ["live_trading", "filled_orders", "settled_pnl"],
            "note": "汇报只含模拟口径，输出结构永远不含实盘字段",
        },
        "period": {"month": cur_month, "combo_ids": order},
        "section_order": list(SECTIONS),
        "sections": sections,
        "sources": [
            "plan_meeting_ledger(-006 只读)", "combo_review(-013 只读)",
            "strategy_branch(-008 compare_reviews 只读)",
            "event_receipts(-007 completeness 只读)",
            "combo_factory(-011 预算口径只读)",
            "acceptance_check(-0.2 三查 findings 只读)",
        ],
        "notes": [
            "DRYRUN 档：只生成文本，零真实出口",
            "ROI 未算出时原样透出 pending_backfill / unit_mismatch，绝不填 0",
            "数据缺失章节显式标注（未接入 / 无数据），五段标题恒出现",
            "输出结构永远不含实盘字段",
        ],
    }
    _assert_no_live_fields(report)
    return report


# ---------- Markdown 渲染（同一份数据，两种呈现） ----------

def _section_header(title: str, sec: dict) -> list[str]:
    """章节标题恒出现；不可用时写明「未接入 / 无数据」+ 原因。"""
    lines = [f"## {title}", ""]
    if not sec.get("available"):
        label = STATUS_LABEL.get(sec.get("status"), "未接入")
        lines.append(f"（{label}：{sec.get('reason') or '未注明原因'}）")
        lines.append("")
    return lines


def render_markdown(report: dict) -> str:
    """汇报 payload → Markdown（015 推送侧可直接贴）。"""
    sec = report["sections"]
    period = report["period"]
    lines = [
        "# 司马懿汇报（DRYRUN）",
        "",
        f"> **{report['notice']}**",
        "",
        f"- 报表生成：{report['generated_at']}（schema v{report['report_schema_version']}）",
        f"- 归月：{period['month']}｜组合数：{len(period['combo_ids'])}",
        f"- 出口：未推送（pending {report['outlet']['pending_ticket']}）",
        f"- 口径：仅模拟（trading_mode={report['scope']['trading_mode']}）",
        "",
    ]

    # 一、本期研究了什么
    lines += _section_header(SECTION_TITLES["research"], sec["research"])
    r = sec["research"]
    if r.get("available"):
        concl = ", ".join(f"{k}={v}" for k, v in r["conclusions"].items()) or "—"
        lines += [
            f"- 投研预案版本：{r['plan_versions_total']}（覆盖个股 "
            f"{r['plan_stocks_total']}、组合 {r['plan_combos_total']}、"
            f"版本链续版 {r['plan_chained_total']}）",
            f"- 会议场次：{r['meetings_total']}｜结论分布：{concl}",
            f"- 分歧率：{_fmt(r['divergence_rate'])}"
            f"（口径 {r['divergence_formula']}）",
            f"- 会前分歧条目合计：{r['dissent_items_total']}",
        ]
        if r.get("divergence_note"):
            lines.append(f"- 说明：{r['divergence_note']}")

    # 二、模拟达到什么
    lines += _section_header(SECTION_TITLES["simulation"], sec["simulation"])
    s = sec["simulation"]
    if s.get("available"):
        lines += ["| 组合 | 收益 | 收益状态 | 当月成本 | ROI | ROI 状态 |",
                  "|---|---|---|---|---|---|"]
        for c in s["combos"]:
            lines.append(
                f"| {c['combo_id']} | {_fmt(c['revenue'])} | {c['revenue_status']} | "
                f"{_fmt(c['cost_accumulated'])} | {c['roi']['display']} | "
                f"{c['roi']['status']} |")
        lines += [
            "",
            f"- ROI 三态分布：{s['roi_status_counts']}（待回填 "
            f"{s['pending_backfill_count']}、单位不可减 {s['unit_mismatch_count']}）",
            f"- {s['roi_note']}",
        ]

    # 三、各分支状态对比
    lines += _section_header(SECTION_TITLES["branches"], sec["branches"])
    b = sec["branches"]
    if b.get("available"):
        lines += ["| 分支 | 决策 | 执行 | 放弃 | 分歧留痕 | 后验已得 | 后验待回填 | 原因码 |",
                  "|---|---|---|---|---|---|---|---|"]
        for row in b["branches"]:
            rcs = ", ".join(f"{k}×{v}" for k, v in row["reason_codes"].items()) or "—"
            lines.append(
                f"| {row['branch_id']} | {row['decisions']} | {row['executed']} | "
                f"{row['abstained']} | {row['divergence_traces']} | "
                f"{row['posterior_available']} | {row['posterior_pending_backfill']} | "
                f"{rcs} |")
        rc = b["receipts"]
        lines += [
            "",
            f"- 四类回执：{' / '.join(rc['kinds']) or '—'}",
            f"- 4/4 达成：{rc['signals_full_4_of_4']}/{rc['signals_total']}｜达成率："
            f"{_fmt(rc['rate_4_of_4'])}",
            f"- 分歧留痕口径：{b['divergence_formula']}",
        ]

    # 四、成本与预算
    lines += _section_header(SECTION_TITLES["cost_budget"], sec["cost_budget"])
    c4 = sec["cost_budget"]
    if c4.get("available"):
        t = c4["totals"]
        lines += ["| 组合 | 预算 | 当月累计 | 剩余 | 超预算 | 预算已登记 |",
                  "|---|---|---|---|---|---|"]
        for row in c4["rows"]:
            lines.append(
                f"| {row['combo_id']} | {_fmt(row['budget'])} | "
                f"{_fmt(row['cost_accumulated'])} | {_fmt(row['remaining'])} | "
                f"{_fmt(row['over_budget'])} | {row['budget_registered']} |")
        unit = t["unit"] or ""
        lines += [
            "",
            f"- 合计：当月累计 {_fmt(t['cost_accumulated'])} {unit}｜"
            f"预算 {_fmt(t['budget'])}｜超预算组合 {t['over_budget_count']} 个｜"
            f"预算未登记 {t['unregistered_count']} 个",
        ]
        if c4.get("unregistered_note"):
            lines.append(f"- 说明：{c4['unregistered_note']}")

    # 五、红黄绿告警
    lines += _section_header(SECTION_TITLES["alerts"], sec["alerts"])
    a = sec["alerts"]
    if a.get("available"):
        lines += ["| 灯 | 级别 | 类别 | 三查项 | 账号 | 明细 |",
                  "|---|---|---|---|---|---|"]
        for row in a["rows"]:
            detail = (json.dumps(row.get("detail"), ensure_ascii=False)
                      if row.get("detail") else "—")
            lines.append(
                f"| {LEVEL_LABEL[row['level']]} | {row['severity']} | {row['category']} | "
                f"{row['check']} | {row['account']} | {detail} |")
        lines += [
            "",
            f"- 结论：**{a['verdict']}**｜P0 {a['p0_count']} 条｜灰度条目 "
            f"{a['grayscale_count']} 条",
            f"- 来源：{a['source']}",
        ]

    lines += [
        "",
        "---",
        "",
        f"> {report['notice']}。不造数原则：ROI 未算出即透出状态词，不以 0 充数；"
        "数据缺失章节显式标注，不静默省略。",
    ]
    return "\n".join(lines)


def render_json(report: dict) -> str:
    """汇报 payload → 稳定 JSON 文本（键排序，供 015 推送 / 归档复现）。"""
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)