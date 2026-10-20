# -*- coding: utf-8 -*-
"""训练基线框架（WO-QT-20261020-018，来源条目 3.5 / 缺口 A-6）。

定纲（老田 2026-10-20 并行开发令放行）：3.5「训练基线」原计划依赖 **≥2 周
快照积累**，而 D-1 决策快照目前**零积累**（paper.db 尚无 decision_snapshots
表 = 今日零落单）。继续等会空转，故本单走**解阻塞路径**：**先把口径与评估
框架建起来，数据回填后自动生效**。

**绝不造数**（本单第一硬约束）：
- 无后验数据 → 指标显式标 `pending_backfill`，**绝不返回 0% 胜率 / 0 收益**
  （0 会被读成「打平」「白干」，属造数）；
- 样本量不足门槛 → 全部指标标 `insufficient_sample` + note，**不出结论**；
- **禁吹功**：对比差异不显著时明确写「无显著差异」，不得因小样本波动宣称
  改进；任一侧样本不足 → 标 `insufficient_sample` 不下结论。

样本口径（与 -008 隔离账对齐）：一条决策快照 = 一次决策样本；分支维度按
`account_id = combo@branch`（-011 branch_account_ids 口径）归集，组合维度按
`combo_id` 归集。

指标定义（六项，算式全部写进输出字段 `*_formula`）：
  - 样本量 n      = 可用样本数（已回填后验的快照数）
  - 命中率        = 后验收益 > 0 的样本数 / n
  - 平均收益      = 后验收益算术平均（同单位）
  - 最大回撤      = 净值曲线（按 ts 排序、单位收益率复合累乘）峰谷最大跌幅
  - 胜率          = 后验收益 > 0 的样本数 / n（与命中率同口径，语义独立命名：
                    命中率强调「信号判对否」，胜率强调「钱赚到了吗」）
  - 盈亏比        = 平均盈利 / |平均亏损|（无亏损样本 → None + note，不填 0）

显著性判据（简化口径，**当前用简化统计并显式注明**，见 `significance_note`）：
  - 均值差：**Welch t 检验**（不假设等方差），自由度用 Welch–Satterthwaite
    近似；p 值由 t 分布双尾近似给出；
  - n < 门槛时**不跑检验**（先于检验短路），直接 insufficient_sample；
  - p ≥ alpha → 结论「无显著差异」，**不吹功**。

只读复用（不改他方文件）：decision_snapshot（-004 快照账）、combo_review
（-013 ROI/成本口径，成本口径唯一定义处仍是 -011/-013，本单只读不重写）、
topic_miner（-016 后验喂入口径：需带 unit，缺 unit 抛错）、combo_registry
（-003 组合口径）、strategy_branch（-008 分支隔离账标识）。

未接线路径（汇报口径，如实挂账）：
  - **后验收益自动回填**（快照 run_ref → 结算后验）属后续工单——当前只有
    **显式/人工喂入**一条路径（`posterior` 参数，缺 unit 抛错），数据回填后
    本框架**自动生效**（同一口径，无需改代码）；
  - **≥2 周快照积累**属**时钟依赖**（非本单可解）：本单只交付口径与评估框架，
    阈值/显著性/回填口径已固化，积累到位后同一 API 直接出数；
  - **多重比较校正**（跑多个分支/组合时的假阳性膨胀）未做——当前只做两两
    对比，属简化口径，见 `significance_note`；批量扫描需另立口径。

标准库依赖：`json / math / sqlite3 / datetime / pathlib`。**零网络出口**。
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path

BASELINE_SCHEMA_VERSION = 1
COMPARE_SCHEMA_VERSION = 1

# 样本量门槛：n < MIN_SAMPLE → 指标不出数（insufficient_sample），低于门槛
# 不出结论。30 是本单定的口径值（呼应「快照积累」的现实量级），非统计magic
# number；老田可拍板调整，改常量即可，输出口径同步。
MIN_SAMPLE = 30

# 指标状态（不造数三态 + 正常态）
STATUS_COMPUTED = "computed"                       # 样本量达门槛 → 出数
STATUS_INSUFFICIENT = "insufficient_sample"       # n < 门槛 → 指标不出数
STATUS_PENDING = "pending_backfill"               # 无后验数据 → 待回填（不填 0）
STATUS_EMPTY = "empty_sample"                     # 该分支/组合压根无快照

# 维度：分支隔离账（account_id=combo@branch，-008/-011 口径）/ 组合
DIM_BRANCH = "branch"
DIM_COMBO = "combo"

# 显著性默认显著性水平
DEFAULT_ALPHA = 0.05

# 指标算式（写进输出字段，可审计——对齐 -016 两率口径风格）
FORMULA_SAMPLE_SIZE = "count(可用样本)  # 已回填后验的快照数"
FORMULA_HIT_RATE = "count(后验收益 > 0) / n"
FORMULA_AVG_RETURN = "sum(后验收益) / n"
FORMULA_WIN_RATE = "count(后验收益 > 0) / n"
FORMULA_PROFIT_LOSS_RATIO = "平均盈利 / abs(平均亏损)"
FORMULA_MAX_DRAWDOWN = "max over t of (peak_t - equity_t) / peak_t  # 净值按 ts 复合累乘"


class BaselineError(Exception):
    """基线框架契约违例（closed on failure）。携带 reason_code 供归因。"""

    def __init__(self, reason_code: str, message: str):
        super().__init__(f"[{reason_code}] {message}")
        self.reason_code = reason_code


# ---------- 通用工具 ----------

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _round(v):
    return None if v is None else round(float(v), 6)


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _nonempty_str(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


# ---------- 净值曲线 / 最大回撤 ----------

def _equity_curve(returns: list[float]) -> list[float]:
    """按时间序把逐笔收益率复合累乘成净值曲线（起点 1.0）。

    仅在**单位收益率**（unit ∈ pct/percent/ratio/fraction 等，见
    `_unit_is_return_unit`）时调用；金额口径（CNY 等）不做复利还原——金额累加
    ≠ 收益率复利，混算即造数，故由调用方按单位判定后跳过最大回撤。
    """
    eq = 1.0
    curve = [1.0]
    for r in returns:
        eq *= (1.0 + r)
        curve.append(eq)
    return curve


def _max_drawdown_from_curve(curve: list[float]) -> float:
    """净值曲线最大回撤（比例，0~1 正数）。峰谷口径：峰值→其后谷底的最大跌幅。"""
    peak = curve[0]
    mdd = 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return mdd


# ---------- t 分布双尾 p 值（简化显著性口径） ----------

def _betacf(a: float, b: float, x: float) -> float:
    """连分式展开（Lentz 算法），供不完全 Beta 函数用。"""
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """正则化不完全 Beta 函数 I_x(a,b)。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_beta = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
               + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(ln_beta) * _betacf(a, b, x) / a
    return 1.0 - math.exp(ln_beta) * _betacf(b, a, 1.0 - x) / b


def _t_sf_two_sided(t: float, df: float) -> float:
    """t 分布双尾 p 值：p = I_{df/(df+t²)}(df/2, 1/2)。

    数值法（无 scipy 依赖）——本单显著性判据为**简化口径**，p 值给到
    双尾 4 位有效即可，不追求高精度临界值（口径已在契约文档注明）。
    """
    if df <= 0:
        return None
    return _betai(df / 2.0, 0.5, df / (df + t * t))


def _welch_ttest(a: list[float], b: list[float]) -> dict:
    """Welch t 检验（不假设等方差）。返回 {t, df, p_two_sided, significant?}。

    方差为 0 且均值相等 → t=0, p=1（无差异，如实写「无显著差异」）；
    方差为 0 且均值不等 → t=±inf, p=0（确定性差异，属真差异，照实出数）。
    """
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return {"test": "welch_t", "t": None, "df": None, "p_two_sided": None,
                "mean_a": _round(sum(a) / na) if na else None,
                "mean_b": _round(sum(b) / nb) if nb else None,
                "note": "两侧样本各 < 2 → 无法做 t 检验（不以 0/1 充数）"}
    ma, mb = sum(a) / na, sum(b) / nb
    va = sum((x - ma) ** 2 for x in a) / (na - 1)
    vb = sum((x - mb) ** 2 for x in b) / (nb - 1)
    sa2, sb2 = va / na, vb / nb
    denom = sa2 + sb2
    diff = ma - mb
    out = {"test": "welch_t", "mean_a": _round(ma), "mean_b": _round(mb),
           "diff": _round(diff), "var_a": _round(va), "var_b": _round(vb)}
    if denom <= 0.0:
        # 双侧方差皆 0
        if abs(diff) <= 1e-15:
            out.update({"t": 0.0, "df": None, "p_two_sided": 1.0,
                        "note": "两侧收益方差皆为 0 且均值相等 → 无差异（p=1）"})
        else:
            out.update({"t": math.copysign(math.inf, diff), "df": None,
                        "p_two_sided": 0.0,
                        "note": "两侧收益方差皆为 0 且均值不等 → 确定性差异（p=0）"})
        return out
    t = diff / math.sqrt(denom)
    df = (denom ** 2) / ((sa2 ** 2) / (na - 1) + (sb2 ** 2) / (nb - 1))
    out.update({"t": _round(t), "df": _round(df),
                "p_two_sided": _round(_t_sf_two_sided(t, df))})
    return out


# ---------- 数据接入：-004 决策快照（只读 mode=ro） ----------

def _read_snapshots(snapshot_db, *, account_id: str | None = None,
                    combo_id: str | None = None) -> list[dict]:
    """只读 -004 决策快照表（sqlite mode=ro + 纯 SELECT，**不调 ensure_table**
    ——那是 -004 的写路径，本单零写入）。

    account_id / combo_id 给定时按列过滤（分支维度=account_id=combo@branch，
    组合维度=combo_id）；都不给则取全表（调用方自行归集）。
    """
    if snapshot_db is None:
        return []
    p = Path(snapshot_db)
    if not p.exists():
        raise BaselineError("snapshot_db_missing",
                            f"决策快照库不存在（closed on failure）: {p}")
    uri = f"file:{p.resolve().as_posix()}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        raise BaselineError("snapshot_db_unreadable",
                            f"决策快照库不可读（closed on failure）: {p} ({e})") from e
    try:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                          ("decision_snapshots",)).fetchone()
        if row is None:
            raise BaselineError(
                "snapshot_table_missing",
                f"决策快照库无 decision_snapshots 表（closed on failure）: {p}——"
                "今日零落单（D-1 快照零积累），口径已就位、等数据回填自动生效")
        sql = ("SELECT snapshot_id, ts, schema_version, combo_id, account_id, "
               "strategy, run_ref, run_path FROM decision_snapshots")
        params: list = []
        if account_id is not None:
            sql += " WHERE account_id=?"
            params.append(account_id)
        elif combo_id is not None:
            sql += " WHERE combo_id=?"
            params.append(combo_id)
        sql += " ORDER BY ts, snapshot_id"
        rows = con.execute(sql, params).fetchall()
    except sqlite3.Error as e:
        raise BaselineError("snapshot_query_failed",
                            f"-004 快照表查询失败（closed on failure）: {p} ({e})") from e
    finally:
        con.close()
    return [dict(r) for r in rows]


# ---------- 数据接入：后验收益喂入 / 回填接口（复用 -016 口径） ----------

def _validate_posterior(posterior) -> dict:
    """校验后验收益喂入/回填，返回 {snapshot_id: {value, unit, source}}。

    **复用 -016 topic_miner 的后验喂入口径**（同一词汇族、同必填键）：
        {"snapshots": {"<snapshot_id>": {"unit": "pct",    # 必填：缺 unit 抛错
                                         "value": 0.12,    # 必填：数值
                                         "source": "settle:run-xxx"}}}

    未出现在此映射里的快照 = 后验未回填 → 该样本不参与指标计算（标
    pending_backfill）。空映射 / None = 全部待回填（**合法「无数据」**，非违例，
    与 -016 判例一致）；缺 unit / value 非法 → **显式抛错**（closed on failure）。
    """
    if posterior is None:
        return {}
    if not isinstance(posterior, dict):
        raise BaselineError("posterior_invalid",
                            f"posterior 须为 dict 或 None: {type(posterior).__name__}")
    snaps = posterior.get("snapshots")
    if snaps is None:
        return {}
    if not isinstance(snaps, dict):
        raise BaselineError("posterior_invalid",
                            f"posterior.snapshots 须为 dict: {type(snaps).__name__}")
    out = {}
    for sid, item in snaps.items():
        if not _nonempty_str(sid):
            raise BaselineError("posterior_invalid",
                                f"posterior.snapshots 键须为非空 snapshot_id: {sid!r}")
        if not isinstance(item, dict):
            raise BaselineError("posterior_invalid",
                                f"posterior.snapshots[{sid!r}] 须为 dict: "
                                f"{type(item).__name__}")
        unit, value = item.get("unit"), item.get("value")
        if not _nonempty_str(unit):
            raise BaselineError(
                "posterior_invalid",
                f"posterior.snapshots[{sid!r}].unit 必填（非空字符串）——"
                "单位不声明即不造数（-016 同口径）")
        if not _is_number(value):
            raise BaselineError(
                "posterior_invalid",
                f"posterior.snapshots[{sid!r}].value 须为数值（不得为 None/字符串/"
                f"bool）: {value!r}")
        out[sid] = {"value": _round(value), "unit": unit.strip(),
                    "source": item.get("source")}
    return out


def backfill_posterior(posterior: dict | None, snapshot_id: str, *, unit: str,
                       value, source: str | None = None) -> dict:
    """回填单条快照的后验收益（**数据回填后基线自动生效**的接线口）。

    就地追加进 posterior 映射并返回新映射（不改传入对象）；unit 必填——缺 unit
    抛 BaselineError（-016 同口径，不声明单位即不造数）。

    回填后**无需改任何代码**：build_baseline / compare_baselines 下一轮调用即
    读到新值。自动回填（快照 run_ref → 结算后验）属后续工单，本函数是当前唯一
    的人工/显式回填路径。
    """
    if not _nonempty_str(snapshot_id):
        raise BaselineError("posterior_invalid",
                            f"snapshot_id 须为非空字符串: {snapshot_id!r}")
    if not _nonempty_str(unit):
        raise BaselineError("posterior_invalid",
                            "unit 必填（非空字符串）——单位不声明即不造数（-016 同口径）")
    if not _is_number(value):
        raise BaselineError("posterior_invalid",
                            f"value 须为数值（不得为 None/字符串/bool）: {value!r}")
    base = dict(_validate_posterior(posterior))
    snaps = dict(base)
    snaps[snapshot_id] = {"unit": unit.strip(), "value": _round(value),
                          "source": source}
    return {"snapshots": snaps}


# ---------- 指标计算 ----------

def _unit_is_return_unit(unit: str) -> bool:
    """单位是否收益率口径（决定能否复合累乘算最大回撤）。"""
    return unit.strip().lower() in {"pct", "percent", "ratio", "fraction", "收益率", "%"}


def compute_metrics(samples: list[dict], *, min_sample: int = MIN_SAMPLE) -> dict:
    """由样本列表（每样本含 value/unit）算六项基线指标。

    三态门（顺序纪律，**先判数据缺失，再判样本量门槛，最后才算数**）：
      1. 无样本           → `empty_sample`；
      2. 有样本无后验     → `pending_backfill`，**六项指标全 null**（不填 0）；
      3. 可用样本 < 门槛   → `insufficient_sample`，**六项指标全 null** + note；
      4. 达门槛           → `computed`，出数。

    samples 每项：{"value": float, "unit": str}。样本按传入顺序即时间序。
    """
    n_total = len(samples)
    usable = [s for s in samples if s.get("value") is not None]
    n = len(usable)

    base = {
        "n_total": n_total,
        "n": n,
        "min_sample": min_sample,
        "sample_size_formula": FORMULA_SAMPLE_SIZE,
        "hit_rate": None, "hit_rate_formula": FORMULA_HIT_RATE,
        "avg_return": None, "avg_return_formula": FORMULA_AVG_RETURN,
        "max_drawdown": None, "max_drawdown_formula": FORMULA_MAX_DRAWDOWN,
        "win_rate": None, "win_rate_formula": FORMULA_WIN_RATE,
        "profit_loss_ratio": None, "profit_loss_ratio_formula": FORMULA_PROFIT_LOSS_RATIO,
        "wins": None, "losses": None, "breakeven": None,
        "unit": None, "note": None,
    }
    if n_total == 0:
        base.update({"status": STATUS_EMPTY,
                     "note": "该维度无决策快照样本（D-1 零落单）→ 指标不出数（不造数）"})
        return base
    if n == 0:
        base.update({"status": STATUS_PENDING,
                     "note": (f"{n_total} 个快照全部无后验收益回填 → "
                              "pending_backfill，指标不出数（不以 0 充数）")})
        return base
    if n < min_sample:
        base.update({"status": STATUS_INSUFFICIENT,
                     "note": (f"可用样本 n={n} < 门槛 {min_sample} → 指标不出数"
                              f"（insufficient_sample），低于门槛不下结论；"
                              f"待回填样本 {n_total - n} 个")})
        return base

    values = [float(s["value"]) for s in usable]
    unit = usable[0].get("unit")
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v < 0]
    breakeven = len(values) - len(wins) - len(losses)
    avg = sum(values) / n

    if _unit_is_return_unit(unit or ""):
        curve = _equity_curve(values)
        mdd = _max_drawdown_from_curve(curve)
    else:
        # 金额口径：不做复利还原（金额累加 ≠ 收益率复利，混算即造数）
        mdd = None

    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = abs(sum(losses) / len(losses)) if losses else None
    plr = (avg_win / avg_loss) if (avg_win is not None and avg_loss) else None

    note_parts = []
    if not losses:
        note_parts.append("无亏损样本 → 盈亏比不可计算（分母 0，不填 0；胜率=1.0 照实填）")
    if not wins:
        note_parts.append("无盈利样本 → 胜率=0.0 为有效数据照实填，盈亏比无分子（null）")
    if mdd is None:
        note_parts.append(f"单位 {unit!r} 非收益率口径 → 最大回撤不出数"
                          "（金额累加≠复利，混算即造数）")

    base.update({
        "status": STATUS_COMPUTED,
        "hit_rate": _round(len(wins) / n),
        "avg_return": _round(avg),
        "max_drawdown": _round(mdd),
        "win_rate": _round(len(wins) / n),
        "profit_loss_ratio": _round(plr),
        "wins": len(wins), "losses": len(losses), "breakeven": breakeven,
        "unit": unit,
        "note": "；".join(note_parts) if note_parts else None,
    })
    return base


# ---------- 主入口：build_baseline ----------

def build_baseline(variant: str, *, dimension: str = DIM_BRANCH,
                   snapshot_db=None, posterior=None,
                   min_sample: int = MIN_SAMPLE) -> dict:
    """产出单个 variant（策略分支或组合）的训练基线。

    参数：
      variant      分支隔离账 id（`combo@branch`，-008/-011 口径）或组合 id；
      dimension    DIM_BRANCH（按 account_id=combo@branch 归集）/ DIM_COMBO
                   （按 combo_id 归集）
      snapshot_db  -004 决策快照库路径（None=无快照 → empty_sample）
      posterior    后验收益喂入/回填（契约同 -016：缺 unit 抛错）
      min_sample   样本量门槛（缺省 30）

    返回：{baseline_schema_version, variant, dimension, status, metrics{...}, ...}。
    不造数：无后验 → pending_backfill；n<门槛 → insufficient_sample；两者均
    六项指标全 null + note，绝不返回 0% 胜率/0 收益。
    """
    if dimension not in (DIM_BRANCH, DIM_COMBO):
        raise BaselineError("dimension_invalid",
                            f"dimension 只认 {DIM_BRANCH}/{DIM_COMBO}: {dimension!r}")
    if not _nonempty_str(variant):
        raise BaselineError("variant_invalid",
                            f"variant 须为非空字符串（combo@branch 或 combo_id）: {variant!r}")
    if not isinstance(min_sample, int) or isinstance(min_sample, bool) or min_sample < 1:
        raise BaselineError("min_sample_invalid",
                            f"min_sample 须为 >=1 的 int: {min_sample!r}")

    post = _validate_posterior(posterior)
    if dimension == DIM_BRANCH:
        snaps = _read_snapshots(snapshot_db, account_id=variant)
    else:
        snaps = _read_snapshots(snapshot_db, combo_id=variant)

    samples = []
    for s in snaps:
        sid = s["snapshot_id"]
        p = post.get(sid)
        if p is None:
            # 后验未回填 → 该样本不参与计算（不填 0），但留痕可回放引用
            samples.append({"snapshot_id": sid, "ts": s.get("ts"),
                            "run_ref": s.get("run_ref"), "value": None,
                            "unit": None, "status": STATUS_PENDING,
                            "note": "后验收益未回填 → 不参与指标（pending_backfill）"})
            continue
        samples.append({"snapshot_id": sid, "ts": s.get("ts"),
                        "run_ref": s.get("run_ref"), "value": p["value"],
                        "unit": p["unit"], "status": "available",
                        "source": p.get("source")})

    metrics = compute_metrics(samples, min_sample=min_sample)
    pending = [s for s in samples if s["status"] == STATUS_PENDING]
    return {
        "baseline_schema_version": BASELINE_SCHEMA_VERSION,
        "generated_at": _now(),
        "variant": variant,
        "dimension": dimension,
        "dimension_semantics": ("account_id = combo@branch（-008 隔离账 / -011 "
                                "branch_account_ids 口径）" if dimension == DIM_BRANCH
                                else "combo_id（-003 四元组注册表口径）"),
        "status": metrics["status"],
        "metrics": metrics,
        "samples_total": len(samples),
        "samples_pending_backfill": len(pending),
        "pending_samples": [{"snapshot_id": s["snapshot_id"], "ts": s["ts"],
                             "run_ref": s["run_ref"]} for s in pending],
        # 样本级收益（仅可用样本，按 ts 时间序）——对比 API 的检验输入，避免
        # 从均值反推损失信息。只含已回填后验的样本，pending 一律不进。
        "sample_returns": [{"snapshot_id": s["snapshot_id"], "ts": s["ts"],
                            "value": s["value"], "unit": s["unit"]}
                           for s in samples if s["value"] is not None],
        "unit_consistency": _unit_consistency(samples),
        "cost_view": None,
        "cost_view_note": ("成本/ROI 视图唯一定义处为 -013 combo_review（成本账为 "
                           "-011），本单只读不重写——本单元默认 None，由调用方"
                           "喂入 -013 输出后回填"),
        "notes": [
            "样本单位 = 一条决策快照（-004），分支维度按 account_id=combo@branch 归集",
            f"样本量门槛 n<{min_sample} → 指标不出数（insufficient_sample），低于门槛不下结论",
            "无后验数据 → pending_backfill，六项指标全 null（绝不返回 0% 胜率/0 收益）",
            "≥2 周快照积累属时钟依赖：口径已就位，数据回填后自动生效（无需改代码）",
        ],
    }


def _unit_consistency(samples: list[dict]) -> dict:
    """单位一致性核查：多单位混喂 → 指标不可加总，显式标（不静默混算）。"""
    units = sorted({s["unit"] for s in samples if s.get("unit")})
    return {"units": units, "consistent": len(units) <= 1,
            "note": None if len(units) <= 1
            else f"多单位混喂 {units} → 均值类指标不可加总，需先统一单位"}


# ---------- 成本/ROI 视图（-013 只读复用，本单不重写口径） ----------

def attach_cost_view(baseline: dict, review: dict | None) -> dict:
    """把 -013 combo_review 的成本/ROI 结论挂到基线上（**只读复用，不重写**）。

    review 为 combo_review.build_review 的输出；None → 保持 None + note（不造数，
    不臆造 ROI）。本函数只搬运 -013 已算好的 roi/cost 字段，不在本单重算成本
    口径（成本口径唯一定义处仍是 -011 成本账 + -013 ROI 定义）。
    """
    if review is None:
        return baseline
    if not isinstance(review, dict) or "roi" not in review:
        raise BaselineError("cost_view_invalid",
                            "review 须为 combo_review.build_review 输出（含 roi 字段）")
    baseline["cost_view"] = {
        "source": "combo_review.build_review（-013，只读复用）",
        "roi": review.get("roi"),
        "cost": review.get("cost"),
        "cost_ledger": "-011 成本账（口径唯一定义处，本单零重写）",
        "note": ("ROI 口径为 收益 − 算力成本（计划 3.3，-013 定义）；"
                 "-013 侧 pending_backfill / unit_mismatch 时本视图同样不出数"),
    }
    return baseline


# ---------- 对比 API：compare_baselines ----------

SIGNIFICANCE_NOTE = (
    "显著性判据当前为**简化口径**：均值差用 Welch t 检验（不假设等方差，"
    "自由度 Welch–Satterthwaite 近似），p 值为数值法 t 分布双尾近似（无 scipy）；"
    "**未做多重比较校正**（批量扫描多分支时假阳性会膨胀）。批量对比需另立口径。"
)


def compare_baselines(a: str, b: str, *, dimension: str = DIM_BRANCH,
                      snapshot_db=None, posterior=None,
                      min_sample: int = MIN_SAMPLE,
                      alpha: float = DEFAULT_ALPHA) -> dict:
    """两个 variant 的基线对比（差异显著性带样本量前提）。

    **禁吹功**纪律：
    - 任一侧 `insufficient_sample` → 结论 `insufficient_sample`，**不下结论**；
    - 任一侧 `pending_backfill` → 结论 `pending_backfill`，**不下结论**；
    - p ≥ alpha → 结论明确写「**无显著差异**」，不得因小样本波动宣称改进；
    - 单位不可比 → `unit_mismatch`，不出数（不混算）。

    参数同 build_baseline，另加 alpha（显著性水平，缺省 0.05）。
    """
    ba = build_baseline(a, dimension=dimension, snapshot_db=snapshot_db,
                        posterior=posterior, min_sample=min_sample)
    bb = build_baseline(b, dimension=dimension, snapshot_db=snapshot_db,
                        posterior=posterior, min_sample=min_sample)

    out = {
        "compare_schema_version": COMPARE_SCHEMA_VERSION,
        "generated_at": _now(),
        "dimension": dimension,
        "alpha": alpha,
        "min_sample": min_sample,
        "a": {"variant": a, "status": ba["status"], "n": ba["metrics"]["n"],
              "unit": ba["metrics"]["unit"], "metrics": ba["metrics"]},
        "b": {"variant": b, "status": bb["status"], "n": bb["metrics"]["n"],
              "unit": bb["metrics"]["unit"], "metrics": bb["metrics"]},
        "verdict": None,
        "verdict_text": None,
        "test": None,
        "deltas": None,
        "significance_note": SIGNIFICANCE_NOTE,
        "notes": [],
    }

    # ---- 样本量/数据前提门（先于任何检验短路） ----
    blocking = [ba["status"], bb["status"]]
    if STATUS_PENDING in blocking:
        out["verdict"] = STATUS_PENDING
        out["verdict_text"] = ("pending_backfill：至少一侧无后验收益数据 → "
                               "不下结论（不以 0 充数）")
        out["notes"].append("数据未到位：先回填后验，再谈基线对比")
        return out
    if STATUS_EMPTY in blocking:
        out["verdict"] = STATUS_EMPTY
        out["verdict_text"] = ("empty_sample：至少一侧无决策快照样本 → 不下结论")
        return out
    if STATUS_INSUFFICIENT in blocking:
        out["verdict"] = STATUS_INSUFFICIENT
        out["verdict_text"] = (f"insufficient_sample：至少一侧可用样本 < {min_sample} → "
                               "不下结论（低于门槛不出数、不吹功）")
        out["notes"].append(f"样本量前提未达（门槛 n>={min_sample}）→ 禁止宣称改进")
        return out

    # ---- 单位可比性门 ----
    ua, ub = ba["metrics"]["unit"], bb["metrics"]["unit"]
    if ua != ub:
        out["verdict"] = "unit_mismatch"
        out["verdict_text"] = (f"unit_mismatch：两侧单位 {ua!r} vs {ub!r} 不可比 → "
                               "不出数（不混算）")
        return out

    # ---- 达门槛后才跑检验 ----
    va = _usable_values(ba)
    vb = _usable_values(bb)
    test = _welch_ttest(va, vb)
    out["test"] = test
    p = test.get("p_two_sided")
    ma, mb = ba["metrics"]["avg_return"], bb["metrics"]["avg_return"]
    if ma is not None and mb is not None:
        out["deltas"] = {
            "avg_return_diff": _round(ma - mb),
            "win_rate_diff": _round((ba["metrics"]["win_rate"] or 0.0)
                                    - (bb["metrics"]["win_rate"] or 0.0)),
            "hit_rate_diff": _round((ba["metrics"]["hit_rate"] or 0.0)
                                    - (bb["metrics"]["hit_rate"] or 0.0)),
            "max_drawdown_diff": (_round(ba["metrics"]["max_drawdown"]
                                         - bb["metrics"]["max_drawdown"])
                                  if (ba["metrics"]["max_drawdown"] is not None
                                      and bb["metrics"]["max_drawdown"] is not None)
                                  else None),
        }

    if p is None:
        out["verdict"] = STATUS_INSUFFICIENT
        out["verdict_text"] = ("样本量不足以做显著性检验 → 不下结论")
        return out
    if p >= alpha:
        # **禁吹功**：不显著就是「无显著差异」，不得因均值波动宣称改进
        out["verdict"] = "no_significant_difference"
        out["verdict_text"] = (f"**无显著差异**（p={p} ≥ alpha={alpha}）——"
                               "差异在样本噪声范围内，不得据此宣称任一侧改进")
        out["notes"].append("禁吹功纪律：不显著即写无显著差异，禁止小样本波动吹改进")
        return out

    improved = ma > mb
    out["verdict"] = "significant_difference"
    out["verdict_text"] = (
        f"平均收益差异**统计显著**（p={p} < alpha={alpha}）："
        f"{a}={ma} vs {b}={mb} → "
        f"{'a 优于 b' if improved else 'b 优于 a'}"
        "（注：统计显著 ≠ 策略优劣定论，仍需结合样本量与回撤审阅）")
    out["notes"].append("显著差异亦不构成「策略更优」定论——须结合样本量/回撤/成本审阅")
    return out


def _usable_values(baseline: dict) -> list[float]:
    """取基线的可用样本收益列表（t 检验输入）。

    基线输出落 `sample_returns`（仅含已回填后验的样本，按 ts 时间序，pending
    样本一律不入）——直接消费，避免从均值反推（会丢样本级信息）。
    """
    return [float(s["value"]) for s in (baseline.get("sample_returns") or [])]


# ---------- Markdown 渲染 ----------

def _fmt(v, digits: int = 4):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{digits}f}".rstrip("0").rstrip(".") or "0"
    return str(v)


def render_markdown(baseline: dict) -> str:
    """基线 → Markdown（同一份数据，两种呈现）。"""
    m = baseline["metrics"]
    lines = [
        f"# 训练基线 · {baseline['variant']}",
        "",
        f"- 维度：{baseline['dimension']}（{baseline['dimension_semantics']}）",
        f"- 生成：{baseline['generated_at']}（schema v{baseline['baseline_schema_version']}）",
        f"- 状态：**{baseline['status']}**",
        f"- 样本量：n={m['n']} / 门槛 {m['min_sample']}"
        f"（快照总数 {m['n_total']}，待回填 {baseline['samples_pending_backfill']}）",
        "",
        "## 一、六项指标",
        "",
        "| 指标 | 值 | 算式 |",
        "|---|---|---|",
        f"| 命中率 | {_fmt(m['hit_rate'])} | `{m['hit_rate_formula']}` |",
        f"| 平均收益 | {_fmt(m['avg_return'])} | `{m['avg_return_formula']}` |",
        f"| 最大回撤 | {_fmt(m['max_drawdown'])} | `{m['max_drawdown_formula']}` |",
        f"| 胜率 | {_fmt(m['win_rate'])} | `{m['win_rate_formula']}` |",
        f"| 盈亏比 | {_fmt(m['profit_loss_ratio'])} | `{m['profit_loss_ratio_formula']}` |",
        "",
        f"- 胜负分布：胜 {m['wins']} / 负 {m['losses']} / 平 {m['breakeven']}"
        f"｜单位：{m['unit'] or '—'}",
    ]
    if m.get("note"):
        lines.append(f"- 说明：{m['note']}")
    lines += ["", "## 二、成本/ROI 视图（-013 只读复用）", ""]
    cv = baseline.get("cost_view")
    if cv is None:
        lines.append(f"（未挂载：{baseline['cost_view_note']}）")
    else:
        roi = cv.get("roi") or {}
        cost = cv.get("cost") or {}
        lines.append(f"- ROI 状态：**{roi.get('status')}**｜值 {_fmt(roi.get('value'))}"
                     f"（{roi.get('formula')}，单位 {roi.get('unit')}）")
        lines.append(f"- 当月算力成本：{_fmt(cost.get('cost_accumulated'))}"
                     f"｜预算 {_fmt(cost.get('budget'))}｜剩余 {_fmt(cost.get('remaining'))}")
        lines.append(f"- 口径来源：{cv.get('cost_ledger')}")
    lines += [
        "", "## 三、不造数与未接线", "",
        "- n < 门槛 → 指标不出数（insufficient_sample），低于门槛不下结论",
        "- 无后验 → pending_backfill，指标全 null（绝不返回 0% 胜率/0 收益）",
        "- ≥2 周快照积累属**时钟依赖**：口径已就位，数据回填后**自动生效**",
        f"- {SIGNIFICANCE_NOTE}",
    ]
    return "\n".join(lines)


def render_comparison_markdown(comparison: dict) -> str:
    """基线对比 → Markdown。"""
    a, b = comparison["a"], comparison["b"]
    lines = [
        f"# 基线对比 · {a['variant']} vs {b['variant']}",
        "",
        f"- 维度：{comparison['dimension']}｜alpha={comparison['alpha']}"
        f"｜样本量门槛 n>={comparison['min_sample']}",
        f"- 样本量：a n={a['n']}（{a['status']}）｜b n={b['n']}（{b['status']}）",
        "",
        "| 指标 | a | b |",
        "|---|---|---|",
        f"| 命中率 | {_fmt(a['metrics']['hit_rate'])} | {_fmt(b['metrics']['hit_rate'])} |",
        f"| 平均收益 | {_fmt(a['metrics']['avg_return'])} | {_fmt(b['metrics']['avg_return'])} |",
        f"| 最大回撤 | {_fmt(a['metrics']['max_drawdown'])} | {_fmt(b['metrics']['max_drawdown'])} |",
        f"| 胜率 | {_fmt(a['metrics']['win_rate'])} | {_fmt(b['metrics']['win_rate'])} |",
        f"| 盈亏比 | {_fmt(a['metrics']['profit_loss_ratio'])} | "
        f"{_fmt(b['metrics']['profit_loss_ratio'])} |",
        "",
        f"- **结论：{comparison['verdict']}**",
        f"- {comparison['verdict_text'] or ''}",
    ]
    if comparison.get("test"):
        t = comparison["test"]
        lines.append(f"- 检验：Welch t={_fmt(t.get('t'))}｜df={_fmt(t.get('df'))}"
                     f"｜双尾 p={_fmt(t.get('p_two_sided'))}")
    if comparison.get("deltas"):
        d = comparison["deltas"]
        lines.append(f"- 差值：平均收益 {_fmt(d.get('avg_return_diff'))}"
                     f"｜胜率 {_fmt(d.get('win_rate_diff'))}"
                     f"｜最大回撤 {_fmt(d.get('max_drawdown_diff'))}")
    lines += ["", "> 禁吹功纪律：p ≥ alpha 一律写「无显著差异」，"
              "不得因小样本波动宣称改进；任一侧样本不足即不下结论。"]
    return "\n".join(lines)
