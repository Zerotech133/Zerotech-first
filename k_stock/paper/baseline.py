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
  - 命中率        = **方向与预案一致**的样本数 / 可判定方向样本数（详见下方
                    「命中率 vs 胜率：拆口径」）
  - 平均收益      = 后验收益算术平均（同单位）
  - 最大回撤      = 净值曲线（按 ts 排序、单位收益率复合累乘）峰谷最大跌幅
  - 胜率          = 后验收益 > 0 的样本数 / n（**赚到没**，只看钱）
  - 盈亏比        = 平均盈利 / |平均亏损|（无亏损样本 → None + note，不填 0）

命中率 vs 胜率：拆口径（WO-QT-20261020-024，老田 2026-10-20 授权拆）
--------------------------------------------------------------------------
**改口径前**：两者同算式（都= `count(后验>0)/n`），只是名字不同。这让「命中
率」既不像命中率也不像胜率——它衡量的其实是「赚钱比例」，被叫成命中率后读者
会误以为它在回答「信号判对否」，实则不回答。本单**显式拆开**：

  - **胜率 win_rate**（口径不变）= 后验收益 > 0 的样本数 / n。回答「**钱赚到
    没**」。分母 = n（已回填后验的可用样本），与历史一致，未改语义。
  - **命中率 hit_rate**（口径**已变更**）= 方向与预案一致的样本数 / **可判定
    方向样本数**。回答「**方向判对否**」——快照实际决策方向（orders_json 的
    action）是否与它引用的 plan_version 的 plan_json.action 相同。
    分母 = **可判定方向样本数**（两侧方向都能取到的样本），**不是 n**：方向判定
    不依赖后验回填，与收益样本集本就不同源。

不造数纪律在拆分后**加码**（三条硬约束）：
  1. **不得沿用胜率值**：命中率无可用依据时显式 `pending`（hit_rate=null +
     hit_rate_status），绝不用 win_rate 顶替——两者量纲与分母都不同，沿用即造数；
  2. **不得填 0**：0 会被读成「一条都没判对」，实为「判不了」；分母 0 或样本
     不足 → null + note；
  3. **不得静默改口径**：语义变更走 `METRIC_SEMANTICS_VERSION` + 输出里的
     `metric_semantics` 变更记录（含裁决日期与依据），见下方常量。

可判定方向的来源（只读，零新增写路径）：快照 `context_json["plan_version"]`
指针（-006 写的预案版本印记）→ 同库 `plan_versions.plan_json.action`；
快照 `orders_json[].action` = 实际决策方向。任一侧取不到 → 该样本**不可判定**，
计入 `hit_rate_unjudged` 并从分母剔除（不按「判错」处理——那是冤枉数据）。

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

BASELINE_SCHEMA_VERSION = 2
COMPARE_SCHEMA_VERSION = 2

# 指标语义版本（WO-QT-20261020-024）。**独立于 schema_version**：schema 变的是
# 结构，本常量变的是**同一个字段名的含义**。改它必须在输出里留变更记录
# （`metric_semantics`），否则就是「静默改口径」——工单纪律明令禁止。
METRIC_SEMANTICS_VERSION = 2

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

# 指标算式（写进输出字段，可审计——对齐 -016 三率口径风格）
FORMULA_SAMPLE_SIZE = "count(可用样本)  # 已回填后验的快照数"
# 命中率 v2（024 拆口径）：方向与预案一致 / 可判定方向样本数
FORMULA_HIT_RATE_V2 = ("count(实际决策方向 == 所引用 plan_version 的 plan_json.action) "
                      "/ count(两侧方向均可判定的样本)")
# 保留 v1 字面量供变更记录引用（不得删——删了变更记录就失去可核对的原点）
FORMULA_HIT_RATE_V1 = "count(后验收益 > 0) / n"
# 兼容别名：FORMULA_HIT_RATE 现指**当前生效**口径（v2）。历史代码/用例若引用该
# 常量断言「算式串」，拿到的是当前口径——这正是我们要的（口径变则断言跟着变）。
FORMULA_HIT_RATE = FORMULA_HIT_RATE_V2
FORMULA_AVG_RETURN = "sum(后验收益) / n"
FORMULA_WIN_RATE = "count(后验收益 > 0) / n  # 赚到没（口径自 v1 未变）"
FORMULA_PROFIT_LOSS_RATIO = "平均盈利 / abs(平均亏损)"
FORMULA_MAX_DRAWDOWN = "max over t of (peak_t - equity_t) / peak_t  # 净值按 ts 复合累乘"

# 变更记录（不得静默改口径：每条都写明谁裁的、什么时候、依据是什么）。
# 读这个模块的人若发现 hit_rate 与历史对不上，先来这里看。
METRIC_SEMANTICS_CHANGELOG = [
    {
        "semantics_version": 1,
        "hit_rate": FORMULA_HIT_RATE_V1,
        "win_rate": "count(后验收益 > 0) / n",
        "note": "两者同算式、语义独立命名（018 原设计，工位纪律声明刻意如此）。",
    },
    {
        "semantics_version": 2,
        "hit_rate": FORMULA_HIT_RATE_V2,
        "win_rate": FORMULA_WIN_RATE,
        "ruled_by": "老田",
        "ruled_at": "2026-10-20",
        "ticket": "WO-QT-20261020-024",
        "change": "命中率由「后验>0 比例」改为「方向与预案一致比例」；胜率口径**不变**。",
        "rationale": ("两率同算式导致「命中率」名不副实：它衡量的是赚钱比例，"
                      "却被读作信号方向判对否。拆开后胜率=赚到没、命中率=方向判对否，"
                      "分母亦不同源（n vs 可判定方向样本数）。"),
        "backward_compat": ("字段名与输出结构**未删改**（仍是 metrics.hit_rate / "
                            "metrics.win_rate + *_formula），但 hit_rate 的**值与含义**"
                            "已变；语义版本 +1 并随输出下发 metric_semantics，"
                            "下游须按新口径读，不得与历史值直接比较。"),
    },
]

# 命中率状态（可判定方向样本不足时**显式不可判定**，不沿用胜率值、不填 0）
HIT_STATUS_COMPUTED = "computed"                 # 可判定样本达门槛 → 出数
HIT_STATUS_PENDING = "pending_direction_evidence"  # 无可判定方向样本 → null
HIT_STATUS_INSUFFICIENT = "insufficient_direction_sample"  # 不足门槛 → null


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
               "strategy, run_ref, run_path, orders_json, context_json "
               "FROM decision_snapshots")
        params: list = []
        if account_id is not None:
            sql += " WHERE account_id=?"
            params.append(account_id)
        elif combo_id is not None:
            sql += " WHERE combo_id=?"
            params.append(combo_id)
        sql += " ORDER BY ts, snapshot_id"
        rows = con.execute(sql, params).fetchall()
        # 预案方向（-006 plan_versions）一并只读捞出：命中率要拿快照引用的
        # plan_version 去对 plan_json.action。**同库同连接**，不加连接、不新建表。
        plan_actions = _read_plan_actions(con)
    except sqlite3.Error as e:
        raise BaselineError("snapshot_query_failed",
                            f"-004 快照表查询失败（closed on failure）: {p} ({e})") from e
    finally:
        con.close()
    out = []
    for r in rows:
        d = dict(r)
        # 把方向判定所需的两侧素材挂进样本（orders_json/context_json 是快照自己的
        # 列，只读取出；解析失败的样本在 _judge_direction 里判为不可判定，不抛错——
        # 方向不可判定是**数据事实**，不是契约违例，故与 closed-on-failure 分开）。
        d["_orders"] = _safe_json_list(d.get("orders_json"))
        d["_plan_stamp"] = _safe_plan_stamp(d.get("context_json"))
        d["_plan_actions"] = plan_actions
        out.append(d)
    return out


def _safe_json_list(raw) -> list:
    """解析快照 orders_json；不可解析/非 list → 空 list（该样本方向不可判定）。"""
    try:
        obj = json.loads(raw) if isinstance(raw, str) and raw.strip() else raw
    except (TypeError, ValueError):
        return []
    return obj if isinstance(obj, list) else []


def _safe_plan_stamp(raw) -> dict:
    """从 context_json 取 plan_version 印记（-006 写的指针）。取不到 → {}。"""
    try:
        obj = json.loads(raw) if isinstance(raw, str) and raw.strip() else raw
    except (TypeError, ValueError):
        return {}
    if not isinstance(obj, dict):
        return {}
    stamp = obj.get("plan_version")
    return stamp if isinstance(stamp, dict) else {}


def _read_plan_actions(con) -> dict:
    """只读捞 (combo_id, stock, plan_version) → plan_json.action。

    键用三元组而非单 version 串：plan_version 的唯一性范围是 (combo_id, stock)，
    只按 version 串匹配会在跨标的重名版本上错配（拿 A 股票的预案方向去判 B 股票
    的方向 = 假判定，比不判更坏）。

    表不存在/查询失败 → 返回 {}（**不抛**）：没有预案账就没有方向基准，此时
    命中率整体判 pending，而不是把整个基线炸掉——预案账与快照库同库但可能
    尚未建表，那是部署事实而非调用方错误。
    """
    try:
        row = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND "
                          "name='plan_versions'").fetchone()
        if row is None:
            return {}
        rows = con.execute("SELECT combo_id, stock, plan_version, plan_json "
                           "FROM plan_versions").fetchall()
    except sqlite3.Error:
        return {}
    out = {}
    for combo_id, stock, version, plan_json in rows:
        try:
            body = json.loads(plan_json) if isinstance(plan_json, str) else plan_json
        except (TypeError, ValueError):
            continue
        if not isinstance(body, dict):
            continue
        action = body.get("action")
        if _nonempty_str(action):
            out[(combo_id, stock, version)] = action.strip().upper()
    return out


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


# ---------- 命中率 v2：方向与预案一致性判定（024 拆口径） ----------

# 合法方向取值。**与 -008 strategy_branch.PLAN_ACTIONS 同集**（BUY/SELL/HOLD），
# 不用 import 他方常量（他方不可用时会连带炸掉整个基线），但**在测试里与 -008
# 逐值对齐校验**，防两处口径悄悄漂移。
_HIT_ACTIONS = ("BUY", "SELL", "HOLD")

# 「按住了不动」的方向记号：没有 BUY/SELL 订单 = 未改变持仓 = HOLD。
# 与 -008 的 decide=False→abstain 同义（跟随分支在预案冲突时放弃也是一种
# 方向判断结果，把它记成「无方向」会凭空制造不可判定率）。
_HOLD = "HOLD"


def _judge_direction(sample: dict) -> tuple[bool | None, str]:
    """判定单样本「方向与预案是否一致」。

    返回 ``(是否一致 | None, 依据串)``：
      · ``True``  = 实际决策方向 == 所引用 plan_version 的 plan_json.action；
      · ``False`` = 两侧方向都能取到但不相等（真判错，要计入分母、分子不加）；
      · ``None``  = **不可判定**（任一侧取不到）——不是判错，故不进分母。

    两侧来源：
      · 预案方向 = 快照 ``context_json["plan_version"]`` 指针（-006 写的印记）
        → ``plan_versions.plan_json.action``（-008 同口径读法）；
      · 实际方向 = 快照 ``orders_json[].action``（引擎落的真实下单动作）。

    为什么不可判定要单列（而非记 False）：快照无 plan_version 印记 = 该决策
    **没有预案可比**（如激进直响分支本就不跟预案），记成「判错」是冤枉数据，
    凭空压低命中率；当命中则是造假。唯一正解是剔出分母并留痕。
    """
    orders = sample.get("_orders") or []
    stamp = sample.get("_plan_stamp") or {}
    actions = sample.get("_plan_actions") or {}

    key = (stamp.get("combo_id"), stamp.get("stock"), stamp.get("plan_version"))
    if not all(_nonempty_str(k) for k in key):
        return None, "快照无 plan_version 印记（该决策不跟预案可比）→ 不可判定"
    plan_action = actions.get(key)
    if not plan_action:
        return None, ("plan_versions 缺 (%s, %s, %s) 或其 plan_json 无 action"
                      " → 不可判定" % (key[0], key[1], key[2]))

    decided = set()
    for o in orders:
        if isinstance(o, dict) and _nonempty_str(o.get("action")):
            a = str(o["action"]).strip().upper()
            if a in ("BUY", "SELL"):
                decided.add(a)
    if len(decided) > 1:
        return None, "同一快照内出现多个方向 %s → 不可判定（拒绝瞎配对）" % sorted(decided)
    # 没有 BUY/SELL 订单 = 未改变持仓 = HOLD（与 -008 abstain 同义）。
    d = decided.pop() if decided else _HOLD
    return (d == plan_action), ("实际 %s vs 预案 %s（plan_version=%s）"
                                % (d, plan_action, key[2]))


def _hit_rate_block(samples: list[dict], *, min_sample: int) -> dict:
    """算命中率 v2（方向与预案一致）+ 其状态/依据，**绝不沿用胜率值**。

    分母 = **可判定方向样本数**（与胜率分母 n 不同源：方向判定不依赖后验回填）。
    可判定样本不足门槛 → null + insufficient；一个都判不出 → null + pending。
    两种情况都**不填 0**（0 会被读成「一条都没判对」），更不用 win_rate 顶替。
    """
    judged, matched, reasons = 0, 0, []
    for s in samples:
        ok, why = _judge_direction(s)
        if ok is None:
            reasons.append(why)
            continue
        judged += 1
        if ok:
            matched += 1

    if judged == 0:
        return {
            # 2026-10-20 主会话接管修复：原实现此处返回 0.0，与本函数自身
            # 文档「两种情况都**不填 0**」及下方 pending 用例期望相矛盾——
            # 0.0 会被读成「一条都没判对」，是造数。改为 None。
            "hit_rate": None,
            "hit_rate_status": HIT_STATUS_PENDING,
            "hit_rate_judged": 0,
            "hit_rate_matched": None,
            "hit_rate_unjudged": len(samples),
            "hit_rate_note": (
                "无可判定的方向样本（快照缺 plan_version 印记或缺合法 orders 动作）"
                "→ 命中率不可判定，**不沿用胜率值、不填 0**。判方向与判赚钱是"
                "两件事：前者要预案印记 + 实际下单动作，后者要后验收益。"
                + ("样例依据：%s" % reasons[0] if reasons else "")),
        }
    if judged < min_sample:
        return {
            "hit_rate": None,
            "hit_rate_status": HIT_STATUS_INSUFFICIENT,
            "hit_rate_judged": judged,
            "hit_rate_matched": matched,
            "hit_rate_unjudged": len(samples) - judged,
            "hit_rate_note": (
                f"可判定方向样本 {judged} < 门槛 {min_sample} → 命中率不出数"
                "（insufficient_direction_sample），低于门槛不下结论"),
        }
    return {
        "hit_rate": _round(matched / judged),
        "hit_rate_status": HIT_STATUS_COMPUTED,
        "hit_rate_judged": judged,
        "hit_rate_matched": matched,
        "hit_rate_unjudged": len(samples) - judged,
        "hit_rate_note": (
            None if not reasons else
            "另有 %d 个样本方向不可判定（已剔出分母，未按判错处理）" % len(reasons)),
    }


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
        "hit_rate_status": None, "hit_rate_judged": None,
        "hit_rate_matched": None, "hit_rate_unjudged": None,
        "hit_rate_note": None,
        "avg_return": None, "avg_return_formula": FORMULA_AVG_RETURN,
        "max_drawdown": None, "max_drawdown_formula": FORMULA_MAX_DRAWDOWN,
        "win_rate": None, "win_rate_formula": FORMULA_WIN_RATE,
        "profit_loss_ratio": None, "profit_loss_ratio_formula": FORMULA_PROFIT_LOSS_RATIO,
        "wins": None, "losses": None, "breakeven": None,
        "unit": None, "note": None,
        # 语义版本随每次输出下发：下游读到 hit_rate 就能知道它是哪套口径的
        # 命中率（v1 与 v2 同名字段、值不可比——这是拆口径必须显式化的理由）。
        "metric_semantics_version": METRIC_SEMANTICS_VERSION,
    }
    if n_total == 0:
        base.update({"status": STATUS_EMPTY,
                     "hit_rate_status": HIT_STATUS_PENDING,
                     "note": "该维度无决策快照样本（D-1 零落单）→ 指标不出数（不造数）"})
        return base
    if n == 0:
        base.update({"status": STATUS_PENDING,
                     "hit_rate_status": HIT_STATUS_PENDING,
                     "note": (f"{n_total} 个快照全部无后验收益回填 → "
                              "pending_backfill，指标不出数（不以 0 充数）")})
        return base
    if n < min_sample:
        base.update({"status": STATUS_INSUFFICIENT,
                     "hit_rate_status": HIT_STATUS_INSUFFICIENT,
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
        "avg_return": _round(avg),
        "max_drawdown": _round(mdd),
        "win_rate": _round(len(wins) / n),
        "profit_loss_ratio": _round(plr),
        "wins": len(wins), "losses": len(losses), "breakeven": breakeven,
        "unit": unit,
        "note": "；".join(note_parts) if note_parts else None,
    })
    # 命中率 v2：**独立于胜率计算**（不同分母、不同数据源）。绝不在此处写
    # `hit_rate = win_rate` ——那正是本次要拆掉的同算式问题。
    base.update(_hit_rate_block(samples, min_sample=min_sample))
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
        # 方向判定素材（命中率 v2 用）随样本透传，与后验回填**互不影响**：
        # 方向可判但后验未回填的样本，命中率能判、胜率判不了——这正是拆口径
        # 的意义（两率不再被同一份数据绑死）。
        dir_material = {"_orders": s.get("_orders"),
                        "_plan_stamp": s.get("_plan_stamp"),
                        "_plan_actions": s.get("_plan_actions")}
        p = post.get(sid)
        if p is None:
            # 后验未回填 → 该样本不参与收益类指标（不填 0），但留痕可回放引用
            samples.append({"snapshot_id": sid, "ts": s.get("ts"),
                            "run_ref": s.get("run_ref"), "value": None,
                            "unit": None, "status": STATUS_PENDING,
                            **dir_material,
                            "note": "后验收益未回填 → 不参与收益指标（pending_backfill）"})
            continue
        samples.append({"snapshot_id": sid, "ts": s.get("ts"),
                        "run_ref": s.get("run_ref"), "value": p["value"],
                        "unit": p["unit"], "status": "available",
                        **dir_material,
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
        # 口径变更记录随每次输出下发：同名字段 hit_rate 在 v1/v2 下值不可比，
        # 下游不拿到这段就无法判断手上这个数是新口径还是旧口径（=静默改口径）。
        "metric_semantics": {
            "version": METRIC_SEMANTICS_VERSION,
            "hit_rate": FORMULA_HIT_RATE,
            "win_rate": FORMULA_WIN_RATE,
            "changelog": METRIC_SEMANTICS_CHANGELOG,
        },
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
            # 命中率差值**不兜底 0**：任一侧不可判定（null）即整体 null。
            # `(x or 0.0)` 会把「判不了」变成「差 0」，那是凭空造出一个
            # 「两侧命中率相同」的结论——比不出数严重得多。
            "hit_rate_diff": (_round(ba["metrics"]["hit_rate"]
                                     - bb["metrics"]["hit_rate"])
                              if (ba["metrics"]["hit_rate"] is not None
                                  and bb["metrics"]["hit_rate"] is not None)
                              else None),
            "hit_rate_diff_note": (
                None if (ba["metrics"]["hit_rate"] is not None
                         and bb["metrics"]["hit_rate"] is not None)
                else "至少一侧命中率不可判定（方向证据不足）→ 差值不出数，不兜底 0"),
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
        f"| 命中率（方向判对否） | {_fmt(m['hit_rate'])} | `{m['hit_rate_formula']}` |",
        f"| 平均收益 | {_fmt(m['avg_return'])} | `{m['avg_return_formula']}` |",
        f"| 最大回撤 | {_fmt(m['max_drawdown'])} | `{m['max_drawdown_formula']}` |",
        f"| 胜率（赚到没） | {_fmt(m['win_rate'])} | `{m['win_rate_formula']}` |",
        f"| 盈亏比 | {_fmt(m['profit_loss_ratio'])} | `{m['profit_loss_ratio_formula']}` |",
        "",
        f"- 胜负分布：胜 {m['wins']} / 负 {m['losses']} / 平 {m['breakeven']}"
        f"｜单位：{m['unit'] or '—'}",
    ]
    # 命中率状态显式上墙：不可判定时必须让读者看见「这个数没出来」，
    # 而不是把 — 误读成 0 或漏读。
    if m.get("hit_rate_status"):
        lines.append(f"- 命中率状态：**{m['hit_rate_status']}**"
                     f"（可判定 {m.get('hit_rate_judged')} / 一致 {m.get('hit_rate_matched')}"
                     f" / 不可判定 {m.get('hit_rate_unjudged')}）")
    if m.get("hit_rate_note"):
        lines.append(f"- 命中率说明：{m['hit_rate_note']}")
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
        f"- 命中率口径 v{METRIC_SEMANTICS_VERSION}：{FORMULA_HIT_RATE}；"
        "无方向证据 → 显式不可判定，**不沿用胜率值、不填 0**",
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
        f"| 命中率（方向判对否） | {_fmt(a['metrics']['hit_rate'])} | "
        f"{_fmt(b['metrics']['hit_rate'])} |",
        f"| 平均收益 | {_fmt(a['metrics']['avg_return'])} | {_fmt(b['metrics']['avg_return'])} |",
        f"| 最大回撤 | {_fmt(a['metrics']['max_drawdown'])} | {_fmt(b['metrics']['max_drawdown'])} |",
        f"| 胜率（赚到没） | {_fmt(a['metrics']['win_rate'])} | {_fmt(b['metrics']['win_rate'])} |",
        f"| 盈亏比 | {_fmt(a['metrics']['profit_loss_ratio'])} | "
        f"{_fmt(b['metrics']['profit_loss_ratio'])} |",
        "",
        f"- **结论：{comparison['verdict']}**",
        f"- {comparison['verdict_text'] or ''}",
    ]
    for side, key in (("a", "a"), ("b", "b")):
        st = comparison[key]["metrics"].get("hit_rate_status")
        if st:
            lines.append(f"- {side} 侧命中率状态：{st}（可判定 "
                         f"{comparison[key]['metrics'].get('hit_rate_judged')}）")
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
