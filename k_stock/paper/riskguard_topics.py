# -*- coding: utf-8 -*-
"""RiskGuard 规则级误杀专题（WO-QT-20261020-023 落地 1.1 老田 a-d 拍板）。

背景：1.1（RiskGuard 校准）已于 2026-10-20 16:00 由老田拍板 a-d 四项，其中
a=213 历史口径归档、b=R3 绝对数闸 5→8 进影子校准（只出准备件）、c=R2 满月
OOS 后再议、d=默认什么都不改。**-016 的 `not_implemented_pending_1_1` 入口
自此可落地**——本模块即该专题的实现。

口径（全部沿用既有判例，不发明新口径）：
  · 记录契约 5 键：ts / rule_id / rule_version / decision / posterior_return
    （与 -016 契约 §六 逐字一致；provenance 为附加来源标注键，见下）；
  · 误杀定义：**decision=blocked 且 posterior_return > 0**（严格大于 0）；
  · 归集：**按 (rule_id, rule_version)**；
  · 后验收益口径同 kg_riskguard_recon / kg_riskguard_calib：
    T0 = 账本 ts 当日（含）后该标的**首个有行情交易日**的收盘价，
    ret_h = close(T0+h)/close(T0)-1（h 默认 5 交易日）；窗口未成熟 → None
    （**pending，如实不给数，不填 0**）；
  · 去重键 (rule_id, security, data_day) 取账本**首次评估**（同 WS-RG 口径）；
  · 账本行过滤：action=unknown（不评）与 reason 以 insufficient_sample 开头
    （守卫不评）剔除。

**诚实性纪律（本模块的核心）**：
  1. **记录级命中 ≠ 规则级误杀**。拦截组里"拦了但会赚"的单条必然存在（市场
     普跌时尤其多）；只有当**同规则放行组基线**显示拦截组整体**更优**（拦了
     更好的票）且 Welch t ≥ 3（本仓既有严判门）时，才构成**规则级误杀证据**
     并出议题。判据参数化（min_settled / t_threshold）且在输出里明示。
  2. **零证据时出结论，不出空数组充数**：status= no_rule_level_false_kill_evidence
     之类 + 非空 conclusion + 逐组统计（命中数/胜率/t/证据等级），让人能复核。
  3. **213 历史口径不参与计算**：records 带 provenance 标注；只有
     provenance ∈ ALLOWED_PROVENANCE 的记录进入计算，其余进 excluded 并计数
     （含"来源不可自证"）。历史 213 判案在本机**不可复现、不可按规则拆分**，
     本模块只声明不引用。
  4. **只读**：账本与日线 parquet 只读打开；本模块不写任何生产文件（账本 /
     params.yaml / 计划任务一概不动）。议题/校准记录只写到调用方显式给的路径。

依赖：核心逻辑仅标准库；日线 parquet 读取用**惰性** import pandas（缺失时
显式抛 daily_source_unavailable，不静默降级）。零网络出口。
"""

from __future__ import annotations

import glob
import json
import math
import os
from collections import defaultdict
from pathlib import Path
import logging

# ── 契约常量（与 -016 契约 §六 逐字一致）─────────────────────────────
RECORD_REQUIRED = ("ts", "rule_id", "rule_version", "decision", "posterior_return")
FALSE_KILL_DEFINITION = "decision=blocked 且 posterior_return > 0"
GROUP_BY = ("rule_id", "rule_version")

DECISION_BLOCKED = "blocked"
DECISION_PASSED = "passed"
DECISIONS = (DECISION_BLOCKED, DECISION_PASSED)

# provenance（来源标注）：只有 canonical_ledger（canonical v1 影子账本）进入计算。
# 历史 213 判案无逐规则明细、不可复现/不可拆分 → 天然不在此列。
PROVENANCE_KEY = "provenance"
ALLOWED_PROVENANCE = ("canonical_ledger",)

# 守卫不评行（与 kg_riskguard.GUARD_NOTE 同款）
GUARD_NOTE = "insufficient_sample"

# 判据默认（本仓既有惯例：|t|>=3 为严判门；n 太小不下结论）
DEFAULT_HORIZON = 5
DEFAULT_MIN_SETTLED = 30
DEFAULT_T_THRESHOLD = 3.0

# 状态词
STATUS_NO_RECORDS = "no_records"
STATUS_TOPICS_FOUND = "false_kill_topics_found"
STATUS_NO_EVIDENCE = "no_rule_level_false_kill_evidence"
STATUS_INCONCLUSIVE = "no_conclusive_evidence"

# 逐组证据等级
EV_MISKILL = "miskill_evidence"
EV_NONE = "no_miskill_evidence"
EV_INCONCLUSIVE = "inconclusive"
EV_INSUFFICIENT = "insufficient_sample"

# 历史 213 口径声明（**不参与计算，只声明**）
HISTORICAL_CASE_NOTE = (
    "历史 213 判案口径（盯盘 quant_engine 归档原型更早历史口径）：本机任何账本"
    "均不可复现，且无逐规则原始明细、不可拆分 → 不参与本专题计算，"
    "与当前 canonical OOS 数字方向相反，不得混引。"
)


class RiskGuardTopicError(Exception):
    """契约违例（closed on failure）。携带 reason_code 供归因。"""

    def __init__(self, reason_code: str, message: str):
        super().__init__(f"[{reason_code}] {message}")
        self.reason_code = reason_code


# ── 通用工具 ────────────────────────────────────────────────────────


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _nonempty_str(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _r6(v):
    return None if v is None else round(float(v), 6)


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


def _stats(xs):
    """均值/中位数/胜率（n=0 时只有 n）。"""
    n = len(xs)
    if not n:
        return {"n": 0, "mean": None, "median": None, "win_rate": None}
    s = sorted(xs)
    return {
        "n": n,
        "mean": sum(xs) / n,
        "median": s[n // 2],
        "win_rate": sum(1 for x in xs if x > 0) / n,
    }


def _welch_t(a, b):
    """Welch t（a 组均值 - b 组均值）。样本不足返回 None。"""
    if len(a) < 2 or len(b) < 2:
        return None
    m1, m2 = sum(a) / len(a), sum(b) / len(b)
    v1 = sum((x - m1) ** 2 for x in a) / (len(a) - 1)
    v2 = sum((x - m2) ** 2 for x in b) / (len(b) - 1)
    den = v1 / len(a) + v2 / len(b)
    # 2026-10-30 P53：零方差/浮点噪声方差退化保护。
    # 原实现只用 den <= 0 兜底；常数序列的方差是浮点噪声（~1e-34），
    # den 极小但 >0 → t 会放大到 1e15 级别（假"强显著"），导致常数样本
    # 被判为规则级误杀证据。双方方差均 < 1e-20 视为无可估方差 → 不下结论。
    if (v1 < 1e-20 and v2 < 1e-20) or den <= 1e-20:
        return None
    return (m1 - m2) / math.sqrt(den)


# ── 路径解析（生产件只读；找不到时显式报，不静默换源）────────────────


def _repo_root() -> Path:
    # paper/ → k_stock/ → 仓库根
    return Path(__file__).resolve().parents[2]


def default_ledger_path() -> Path:
    """canonical 影子账本路径（只读）。env 优先，其次仓库同级的 zerobound-kg。"""
    env = os.environ.get("RISKGUARD_LEDGER")
    if env:
        return Path(env)
    return _repo_root().parent / "zerobound-kg" / "riskguard_ledger" / "ledger.jsonl"


def default_daily_dir() -> Path:
    """ts_daily 分片目录（只读）。env 优先，其次仓库内 quant_backtest/data。"""
    env = os.environ.get("RISKGUARD_DAILY_DIR")
    if env:
        return Path(env)
    env_root = os.environ.get("KG_DATA_ROOT")
    if env_root:
        return Path(env_root) / "tdx" / "ts_daily"
    local = _repo_root() / "quant_backtest" / "data" / "tdx" / "ts_daily"
    if local.exists():
        return local
    cfg = _repo_root().parent / "zerobound-kg" / "config.json"
    try:
        with open(cfg, encoding="utf-8") as f:
            root = (json.load(f) or {}).get("data_root") or ""
        if root:
            return Path(root) / "tdx" / "ts_daily"
    except (OSError, ValueError) as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "k_stock/paper/riskguard_topics.py", exc)
    return local


# ── 日线后验（惰性 pandas；只读 parquet）─────────────────────────────


class ParquetPriceProvider:
    """ts_daily parquet → ret_h 结算（只读；口径同 recon/calib）。

    T0 = 该标的 ts 当日（含）后首个行情交易日收盘；ret_h = close(T0+h)/close(T0)-1；
    窗口未成熟 → None（pending，不给数）。"""

    def __init__(self, daily_dir=None, horizon: int = DEFAULT_HORIZON):
        self.daily_dir = Path(daily_dir) if daily_dir else default_daily_dir()
        self.horizon = horizon
        self._idx = None

    def _index(self):
        if self._idx is not None:
            return self._idx
        try:
            import pandas as pd
        except ImportError as exc:
            raise RiskGuardTopicError(
                "daily_source_unavailable",
                f"日线结算需要 pandas（未安装）：{self.daily_dir}",
            ) from exc
        parts = sorted(glob.glob(str(self.daily_dir / "part_*.parquet")))
        if not parts:
            raise RiskGuardTopicError(
                "daily_source_unavailable", f"ts_daily 无分片：{self.daily_dir}"
            )
        df = pd.concat(
            [
                pd.read_parquet(p, columns=["ts_code", "trade_date", "close"])
                for p in parts
            ],
            ignore_index=True,
        )
        df["trade_date"] = df["trade_date"].astype(str).str[:8]
        df = df.dropna(subset=["close"]).sort_values(["ts_code", "trade_date"])
        idx = {}
        for tc, g in df.groupby("ts_code"):
            idx[tc] = (g["trade_date"].tolist(), g["close"].tolist())
        self._idx = idx
        return idx

    def ret(self, security: str, d0: str):
        """ret_h（d0=YYYYMMDD）；未成熟/无行情 → None。"""
        idx = self._index()
        gd = idx.get(security)
        if not gd:
            return None
        dates, closes = gd
        lo, hi = 0, len(dates)
        while lo < hi:  # 首个 >= d0 的行情日
            mid = (lo + hi) // 2
            if dates[mid] < d0:
                lo = mid + 1
            else:
                hi = mid
        if lo >= len(dates) or lo + self.horizon >= len(dates):
            return None
        return closes[lo + self.horizon] / closes[lo] - 1


# ── 账本读取（只读）+ 记录抽取 ─────────────────────────────────────


def read_ledger(ledger_path=None) -> list[dict]:
    """读 canonical 影子账本（只读）。"""
    p = Path(ledger_path) if ledger_path else default_ledger_path()
    if not p.exists():
        raise RiskGuardTopicError("ledger_missing", f"RiskGuard 账本不存在：{p}")
    rows = []
    with open(p, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as exc:
                raise RiskGuardTopicError(
                    "ledger_corrupt",
                    f"账本第 {lineno} 行不是合法 JSON（closed on failure）：{p}",
                ) from exc
    return rows


def _decision_of(action) -> str:
    if action in ("shadow_block", "enforce_block"):
        return DECISION_BLOCKED
    return DECISION_PASSED


def build_records(
    ledger_path=None,
    *,
    price_provider=None,
    daily_dir=None,
    horizon: int = DEFAULT_HORIZON,
) -> list[dict]:
    """账本 → 5 键契约记录（含 provenance 标注）。

    过滤：action=unknown（不评）、reason 以 insufficient_sample 开头（守卫不评）。
    去重：(rule_id, security, data_day) 取首次评估（按 ts, security 升序）。
    posterior_return 由 price_provider 结算；未成熟 → None。
    """
    rows = read_ledger(ledger_path)
    rows.sort(
        key=lambda r: (
            r.get("ts") or "",
            r.get("security") or "",
            r.get("rule_id") or "",
        )
    )
    provider = price_provider or ParquetPriceProvider(daily_dir, horizon)
    seen, out = set(), []
    for r in rows:
        reason = r.get("reason") or ""
        if r.get("action") == "unknown":
            continue
        if reason.startswith(GUARD_NOTE):
            continue
        d0 = (r.get("ts") or "")[:10].replace("-", "")
        security = r.get("security")
        if not d0 or not security:
            continue
        key = (r.get("rule_id"), security, d0)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "ts": r.get("ts"),
                "rule_id": r.get("rule_id"),
                "rule_version": r.get("data_version"),
                "decision": _decision_of(r.get("action")),
                "posterior_return": provider.ret(security, d0),
                PROVENANCE_KEY: "canonical_ledger",
                # 附加可回放引用（非契约必填键）
                "security": security,
                "data_day": d0,
                "action": r.get("action"),
                "score": r.get("score"),
                "reason": reason,
            }
        )
    return out


def read_calibration_log(path) -> list[dict]:
    """读 5 键契约校准记录（jsonl，一行一条；-016 契约约定的喂入形态）。"""
    p = Path(path)
    if not p.exists():
        raise RiskGuardTopicError("calibration_log_missing", f"校准记录文件不存在：{p}")
    out = []
    with open(p, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError as exc:
                raise RiskGuardTopicError(
                    "calibration_log_corrupt",
                    f"校准记录第 {lineno} 行不是合法 JSON（closed on failure）：{p}",
                ) from exc
    return out


def write_calibration_log(records, path) -> str:
    """把记录写成 5 键契约校准记录（**只写调用方显式给的路径**）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    return str(p)


# ── 专题提炼（核心）─────────────────────────────────────────────────


def _validate_record(rec, idx: int) -> dict:
    """记录结构校验（5 键契约）。违例 → 显式抛（closed on failure）。"""
    if not isinstance(rec, dict):
        raise RiskGuardTopicError(
            "riskguard_records_invalid",
            f"records[{idx}] 须为 dict：{type(rec).__name__}",
        )
    missing = [k for k in RECORD_REQUIRED if k not in rec]
    if missing:
        raise RiskGuardTopicError(
            "riskguard_records_invalid",
            f"records[{idx}] 缺契约必填键 {missing}（必填 {list(RECORD_REQUIRED)}）",
        )
    if not _nonempty_str(rec.get("ts")):
        raise RiskGuardTopicError(
            "riskguard_records_invalid", f"records[{idx}].ts 须为非空字符串"
        )
    for k in ("rule_id", "rule_version"):
        if not _nonempty_str(rec.get(k)):
            raise RiskGuardTopicError(
                "riskguard_records_invalid", f"records[{idx}].{k} 须为非空字符串"
            )
    if rec.get("decision") not in DECISIONS:
        raise RiskGuardTopicError(
            "riskguard_records_invalid",
            f"records[{idx}].decision 须为 {list(DECISIONS)}：{rec.get('decision')!r}",
        )
    pr = rec.get("posterior_return")
    if pr is not None and not _is_number(pr):
        raise RiskGuardTopicError(
            "riskguard_records_invalid",
            f"records[{idx}].posterior_return 须为数值或 None（未成熟）：{pr!r}",
        )
    return rec


def _is_false_kill(rec: dict) -> bool:
    """误杀判定（契约定义，严格）：decision=blocked 且 posterior_return > 0。"""
    pr = rec.get("posterior_return")
    return rec.get("decision") == DECISION_BLOCKED and _is_number(pr) and pr > 0


def mine_topics(
    records=None,
    *,
    min_settled: int = DEFAULT_MIN_SETTLED,
    t_threshold: float = DEFAULT_T_THRESHOLD,
) -> dict:
    """RiskGuard 规则级误杀专题提炼。

    流程：
      1. 记录结构校验（5 键契约；违例显式抛）；
      2. provenance 过滤：只有 ALLOWED_PROVENANCE 进入计算，其余进 excluded
         （**213 历史口径等非 canonical 来源天然被挡在计算之外**）；
      3. 按 (rule_id, rule_version) 归集，逐组统计：
         拦截组/放行组成熟样本、命中数（误杀定义）、均值/胜率；
      4. 规则级误杀证据判据：同规则**放行组基线**（同 rule_id 全部版本合并，
         因部分版本无 pass 行）下，拦截组均值**更高**（拦了更好的票）且
         Welch t ≥ t_threshold → 该组出议题；样本不足 → insufficient_sample；
         方向不利但不显著 → inconclusive；
      5. 零证据 → status=无规则级误杀证据 + 非空 conclusion（**不出空数组充数**）。

    返回：dict（status / topics / groups / conclusion / counts / notes ...）。
    """
    if records is None:
        records = []
    if not isinstance(records, (list, tuple)):
        raise RiskGuardTopicError(
            "riskguard_records_invalid",
            f"records 须为 list/tuple：{type(records).__name__}",
        )
    recs = [_validate_record(r, i) for i, r in enumerate(records)]

    # ── provenance 过滤（213 历史口径等一律不参与计算）─────────────────
    used, excluded = [], defaultdict(int)
    excluded_detail = defaultdict(list)
    for r in recs:
        prov = r.get(PROVENANCE_KEY) or "unspecified"
        if prov in ALLOWED_PROVENANCE:
            used.append(r)
        else:
            excluded[prov] += 1
            if len(excluded_detail[prov]) < 5:
                excluded_detail[prov].append(
                    {
                        "ts": r.get("ts"),
                        "rule_id": r.get("rule_id"),
                        "rule_version": r.get("rule_version"),
                    }
                )

    if not used:
        return {
            "status": STATUS_NO_RECORDS,
            "implemented": True,
            "source_type": "riskguard_reserved",
            "topic_schema_version": 1,
            "topics": [],
            "counts": {
                "records_total": len(recs),
                "records_used": 0,
                "records_excluded": len(recs),
                "groups": 0,
                "false_kill_hits": 0,
                "settled_records": 0,
            },
            "groups": [],
            "conclusion": (
                "无可用记录：全部记录因来源标注不在允许集合（"
                f"{list(ALLOWED_PROVENANCE)}）被排除，未做任何规则级误杀判定。"
            ),
            "false_kill_definition": FALSE_KILL_DEFINITION,
            "group_by": list(GROUP_BY),
            "excluded": {
                "by_provenance": dict(excluded),
                "detail": {k: v for k, v in excluded_detail.items()},
            },
            "historical_case_note": HISTORICAL_CASE_NOTE,
            "criterion": {
                "min_settled": min_settled,
                "t_threshold": t_threshold,
                "rule": "拦截组均值 > 同规则放行组基线均值 且 Welch t >= "
                "t_threshold → 规则级误杀证据",
            },
            "notes": [],
        }

    # ── 归集 (rule_id, rule_version) ─────────────────────────────────
    groups = defaultdict(list)
    for r in used:
        groups[(r["rule_id"], r["rule_version"])].append(r)

    # 同规则放行组基线（跨版本合并：部分版本无 pass 行）
    baseline_by_rule = defaultdict(list)
    baseline_versions_by_rule = defaultdict(set)
    for r in used:
        if r["decision"] == DECISION_PASSED and r.get("posterior_return") is not None:
            baseline_by_rule[r["rule_id"]].append(r["posterior_return"])
            baseline_versions_by_rule[r["rule_id"]].add(r.get("rule_version"))

    settled_total = 0
    hits_total = 0
    out_groups = []
    topics = []
    for key in sorted(groups):
        rule_id, rule_version = key
        items = groups[key]
        blocked = [r for r in items if r["decision"] == DECISION_BLOCKED]
        passed = [r for r in items if r["decision"] == DECISION_PASSED]
        b5 = [
            r["posterior_return"]
            for r in blocked
            if r.get("posterior_return") is not None
        ]
        p5_same = [
            r["posterior_return"]
            for r in passed
            if r.get("posterior_return") is not None
        ]
        base = baseline_by_rule.get(rule_id, [])
        sb, sp_base, sp_same = _stats(b5), _stats(base), _stats(p5_same)
        hits = [r for r in blocked if _is_false_kill(r)]
        t = _welch_t(b5, base)
        settled_total += len(b5)
        hits_total += len(hits)

        if len(b5) < min_settled or len(base) < min_settled:
            ev = EV_INSUFFICIENT
        elif t is not None and t >= t_threshold and sb["mean"] > sp_base["mean"]:
            ev = EV_MISKILL
        elif t is not None and sb["mean"] > sp_base["mean"]:
            ev = EV_INCONCLUSIVE
        else:
            ev = EV_NONE

        g = {
            "rule_id": rule_id,
            "rule_version": rule_version,
            "records": len(items),
            "blocked_records": len(blocked),
            "passed_records": len(passed),
            "settled_blocked": sb["n"],
            "settled_passed_same_group": sp_same["n"],
            "settled_baseline_same_rule": sp_base["n"],
            "false_kill_hits": len(hits),
            "false_kill_hit_rate": _r6(len(hits) / sb["n"]) if sb["n"] else None,
            "blocked_mean": _r6(sb["mean"]),
            "blocked_win_rate": _r6(sb["win_rate"]),
            "baseline_mean": _r6(sp_base["mean"]),
            "baseline_win_rate": _r6(sp_base["win_rate"]),
            "baseline_rule_versions": sorted(
                str(v) for v in baseline_versions_by_rule.get(rule_id, set())
            ),
            "baseline_same_group_records": sp_same["n"],
            "mean_diff_pp": _r6((sb["mean"] - sp_base["mean"]) * 100)
            if sb["n"] and sp_base["n"]
            else None,
            "welch_t": _r6(t),
            "evidence": ev,
            "hit_examples": [
                {
                    "ts": h.get("ts"),
                    "security": h.get("security"),
                    "posterior_return": _r6(h.get("posterior_return")),
                    "rule_version": h.get("rule_version"),
                }
                for h in sorted(hits, key=lambda x: -(x.get("posterior_return") or 0))[
                    :5
                ]
            ],
        }
        out_groups.append(g)

        if ev == EV_MISKILL:
            topics.append(
                {
                    "topic_schema_version": 1,
                    "source_type": "riskguard_reserved",
                    "rule_id": rule_id,
                    "rule_version": rule_version,
                    "deviation_type": "riskguard_false_kill",
                    "deviation_type_source": "riskguard_topics.criterion",
                    "evidence": g,
                    "status": "draft",
                    "meeting_initiated": False,
                    "mined_at": _now_iso(),
                }
            )

    # ── 结论（零证据时也必须有结论，不出空数组充数）──────────────────
    conclusive = [g for g in out_groups if g["evidence"] == EV_NONE]
    inconclusive = [g for g in out_groups if g["evidence"] == EV_INCONCLUSIVE]
    insufficient = [g for g in out_groups if g["evidence"] == EV_INSUFFICIENT]
    if topics:
        status = STATUS_TOPICS_FOUND
    elif conclusive:
        status = STATUS_NO_EVIDENCE
    else:
        status = STATUS_INCONCLUSIVE

    def _names(gs):
        return "、".join(f"{g['rule_id']}@{g['rule_version']}" for g in gs) or "无"

    parts = [
        f"canonical 影子账本去重后可用记录 {len(used)} 条（成熟后验 {settled_total} 条），"
        f"按 (rule_id, rule_version) 归集 {len(out_groups)} 组；"
        f"记录级命中（{FALSE_KILL_DEFINITION}）合计 {hits_total} 条。"
    ]
    if topics:
        parts.append(
            f"规则级误杀证据成立 {len(topics)} 组（{'、'.join(t['rule_id'] + '@' + t['rule_version'] for t in topics)}）→ 出议题。"
        )
    else:
        parts.append(
            f"**无规则级误杀证据**：拦截组整体未优于同规则放行组基线，"
            f"命中 {hits_total} 条属记录级现象而非系统性误杀 → 本轮 0 条议题。"
        )
    if conclusive:
        parts.append(f"已下结论组（拦截方向正确）：{_names(conclusive)}。")
    if inconclusive:
        parts.append(f"方向不利但不显著（不作结论）：{_names(inconclusive)}。")
    if insufficient:
        parts.append(
            f"成熟样本不足（不下结论，勿读作无误杀）：{_names(insufficient)}。"
        )
    parts.append("记录级命中数与基线胜率一并列出，供会上直接复核。")

    return {
        "status": status,
        "implemented": True,
        "source_type": "riskguard_reserved",
        "topic_schema_version": 1,
        "generated_at": _now_iso(),
        "topics": topics,
        "counts": {
            "records_total": len(recs),
            "records_used": len(used),
            "records_excluded": len(recs) - len(used),
            "groups": len(out_groups),
            "false_kill_hits": hits_total,
            "settled_records": settled_total,
            "groups_with_miskill_evidence": len(topics),
            "groups_conclusive": len(conclusive),
            "groups_inconclusive": len(inconclusive),
            "groups_insufficient_sample": len(insufficient),
        },
        "groups": out_groups,
        "conclusion": " ".join(parts),
        "false_kill_definition": FALSE_KILL_DEFINITION,
        "group_by": list(GROUP_BY),
        "criterion": {
            "min_settled": min_settled,
            "t_threshold": t_threshold,
            "rule": "拦截组均值 > 同规则放行组基线均值 且 Welch t >= t_threshold "
            "→ 规则级误杀证据（记录级命中单独列示，不单独出议题）",
            "baseline_scope": "同 rule_id 的放行组（跨 rule_version 合并）",
            "note": "判据参数化并在输出中明示；本仓既有惯例 |t|>=3 为严判门",
        },
        "excluded": {
            "by_provenance": dict(excluded),
            "detail": {k: v for k, v in excluded_detail.items()},
        },
        "historical_case_note": HISTORICAL_CASE_NOTE,
        "notes": [
            "记录级命中 ≠ 规则级误杀：命中单独计数并列表，不单独出议题",
            "213 历史口径不可复现/不可拆分，未参与计算，也不得与当前 OOS 混引",
            "基线为同 rule_id 的放行组（跨 rule_version 合并；各组 baseline_rule_versions "
            "已列出）——R1 的放行样本仅来自 2026-09-30 批次，跨版本基线对 R1 的"
            "结论只作方向参考，不作精确估计",
            "本模块只读账本与日线，不写任何生产文件",
        ],
    }


def run_topic(
    *,
    ledger_path=None,
    daily_dir=None,
    horizon: int = DEFAULT_HORIZON,
    min_settled: int = DEFAULT_MIN_SETTLED,
    t_threshold: float = DEFAULT_T_THRESHOLD,
) -> dict:
    """端到端：读账本 → 结算后验 → 提炼专题（只读生产件）。"""
    provider = ParquetPriceProvider(daily_dir, horizon)
    records = build_records(ledger_path, price_provider=provider)
    return mine_topics(records, min_settled=min_settled, t_threshold=t_threshold)


# ── Markdown 渲染（同一份数据，两种呈现）────────────────────────────


def render_markdown(result: dict) -> str:
    """专题结果 → Markdown（可直接贴会议议程）。"""
    lines = [f"# RiskGuard 规则级误杀专题（{result.get('status')}）", ""]
    lines.append(f"- 状态：`{result.get('status')}`")
    lines.append(f"- 结论：{result.get('conclusion')}")
    lines.append(f"- 误杀定义：{result.get('false_kill_definition')}")
    lines.append(f"- 归集：{', '.join(result.get('group_by') or [])}")
    c = result.get("counts") or {}
    lines.append(
        f"- 计数：记录 {c.get('records_total')}（用 {c.get('records_used')}"
        f"／排除 {c.get('records_excluded')}）｜组 {c.get('groups')}"
        f"｜命中 {c.get('false_kill_hits')}｜议题 {len(result.get('topics') or [])}"
    )
    cr = result.get("criterion") or {}
    lines.append(
        f"- 判据：{cr.get('rule')}（min_settled={cr.get('min_settled')},"
        f" t_threshold={cr.get('t_threshold')}）"
    )
    lines += [
        "",
        "## 一、逐组证据",
        "",
        "| rule_id | rule_version | 拦截(成熟) | 命中 | 命中率 | 拦截均值 | 基线均值 | 差pp | t | 证据 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for g in result.get("groups") or []:
        lines.append(
            f"| {g['rule_id']} | {g['rule_version']} | {g['settled_blocked']} | "
            f"{g['false_kill_hits']} | {g['false_kill_hit_rate']} | "
            f"{g['blocked_mean']} | {g['baseline_mean']} | {g['mean_diff_pp']} | "
            f"{g['welch_t']} | {g['evidence']} |"
        )
    lines += ["", "## 二、议题清单", ""]
    topics = result.get("topics") or []
    if not topics:
        lines.append("（本轮 0 条议题——见结论与逐组证据，不以空数组充数）")
    else:
        for t in topics:
            lines.append(
                f"- **{t['rule_id']}@{t['rule_version']}**"
                f"｜命中 {t['evidence']['false_kill_hits']}"
                f"｜t={t['evidence']['welch_t']}｜状态 {t['status']}"
            )
    lines += ["", "## 三、口径与边界", ""]
    lines.append(f"- {result.get('historical_case_note')}")
    for n in result.get("notes") or []:
        lines.append(f"- {n}")
    return "\n".join(lines)


# ── CLI（只读；输出只写显式给定的路径）──────────────────────────────


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="RiskGuard 规则级误杀专题（只读）")
    ap.add_argument("--ledger", default=None, help="canonical 影子账本路径（只读）")
    ap.add_argument("--daily-dir", default=None, help="ts_daily 分片目录（只读）")
    ap.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    ap.add_argument("--min-settled", type=int, default=DEFAULT_MIN_SETTLED)
    ap.add_argument("--t-threshold", type=float, default=DEFAULT_T_THRESHOLD)
    ap.add_argument(
        "--out-json", default=None, help="结果 JSON 落盘路径（显式给定才写）"
    )
    ap.add_argument("--out-md", default=None, help="结果 Markdown 落盘路径")
    a = ap.parse_args(argv)
    res = run_topic(
        ledger_path=a.ledger,
        daily_dir=a.daily_dir,
        horizon=a.horizon,
        min_settled=a.min_settled,
        t_threshold=a.t_threshold,
    )
    if a.out_json:
        with open(a.out_json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    if a.out_md:
        with open(a.out_md, "w", encoding="utf-8") as f:
            f.write(render_markdown(res))
    print(render_markdown(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
