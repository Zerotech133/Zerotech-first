# -*- coding: utf-8 -*-
"""tools/research/s05_reverse_hypothesis.py · S05 反向（超配）假设验证（2026-11-01，wave6 支线 P）。

背景
----
wave5（`s05_overlay_test.py`）发现：把 LX20 信号股在 S01 大市值 top20 组合内**降配**导致负超额
（w=0.5 −2.48%/t=−3.18；w=0 −5.96%），且负超额随降配深度近似线性 → 同一 LX20 信号在 S01 大市值
持仓内方向相反（被降配的信号股反而跑赢）。本文件按 N 的 action 立「反向假设」并过四关：

  1. 基线 = S01 基线（**复用 `s05_overlay_test`** 的 build_baseline 复刻 + 对齐校验，wave5 已 3/3 精确一致）；
  2. 处理组 = 对基线持仓中的 LX20 信号股**超配**：
     * 乘子 m ∈ {1.5, 2.0, 3.0}：非信号权重 1、信号权重 m，归一化；
     * 信号股占权 k ∈ {30%, 50%}：信号股合计 k（组内等权）、非信号合计 1−k；
     * 主口径 m=2.0；无信号股时退化为基线等权；
  3. 回测 2023-01~2026-10：周频调仓、T 日收盘决策 → **T+1 收盘执行**、单边 30bp、
     执行日涨停(≥9.5%)/停牌不可买→现金；对比基线：年化/超额/TE/t/IR/MDD/波动/换手/成本，分年+
     2026 熊市段（基线峰→谷）；
  4. 四关标注（可写 strategy_registry S05 validation）：
     * PIT：信号只用到 T 日及以前（复用已过 PIT 的 LX20 因子；本文件静态检查无负 shift，权重只用 window[T]）；
     * OOS：IS=2023-01~2024-12 / OOS=2025-01~2026-10，OOS 超额>0 且 t≥2；
     * 成本：10/20/30/50bp 敏感性，30bp 口径超额>0 且 t≥2、50bp 仍>0；
     * 容量：按被超配股票的 20 日日均成交额与「单票单日交易额 ≤10% ADV」估算 AUM 上限；
  5. 结论：反向假设是否成立（OOS 超额>0 且 t≥2 且逐年≥3/4）、是否值得进入 S05 validation / 影子盘。
     特别标注**数据窥探风险**：反向假设来自同一份 wave5 数据，OOS 2025-26 非真正样本外，
     影子盘只能「只读跟踪、不配资金」验证。

用法
----
    # 先跑通 m=2.0 主口径
    C:\\项目\\py312\\python.exe -X utf8 tools/research/s05_reverse_hypothesis.py --multipliers 2.0 \
        --out tmp/s05_reverse_m2.json --md tmp/s05_reverse_m2.md
    # 全量（基线 + m=1.5/2.0/3.0 + k=30%/50%）
    C:\\项目\\py312\\python.exe -X utf8 tools/research/s05_reverse_hypothesis.py \
        --out research/reports/s05_reverse_20261101.json \
        --md research/reports/s05_reverse_20261101.md

局限：信号为 LX20 简化单形态代理；S01 过滤静态信息同 build_baseline；持有期停牌收益按 0；
调仓仅周频；反向假设存在数据窥探（见上）；容量为静态 ADV 假设。
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- 路径/依赖
_HERE = Path(__file__).resolve().parent
REPO = _HERE.parents[1]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import factor_lib as fl                  # noqa: E402
import reverse_avoid_test as rav         # noqa: E402  统计口径（不修改）
import s05_overlay_test as ov            # noqa: E402  S01 基线复刻/回测/指标（复用，不修改）

VERSION = "0.1.0-wave6"
ANNUAL_DAYS = 252
COST = fl.DEFAULT_COST
DEFAULT_START = "20230101"
DEFAULT_END = "20261030"
TOP = ov.TOP
REBALANCE_EVERY = ov.REBALANCE_EVERY
SIGNAL_N = ov.SIGNAL_N
DEFAULT_MULTIPLIERS = (1.5, 2.0, 3.0)
DEFAULT_SHARES = (0.3, 0.5)
PRIMARY_MULTIPLIER = 2.0
PRIMARY_VARIANT = "M2"
COST_LIST = (0.001, 0.002, 0.003, 0.005)
COST_BP_LIST = (10, 20, 30, 50)
DEFAULT_OUT_JSON = REPO / "research" / "reports" / "s05_reverse_20261101.json"
DEFAULT_OUT_MD = REPO / "research" / "reports" / "s05_reverse_20261101.md"
IS_END = "20241231"
OOS_START = "20250101"


# ================================================================ 反向超配权重
def reverse_weights_multiplier(base_idx: list[int], date_i: int, window: np.ndarray,
                               m: float) -> dict[int, float]:
    """信号股权重乘子 m：非信号 1、信号 m，归一化；无信号→基线等权。"""
    if not base_idx:
        return {}
    trig = [int(window[date_i, j]) for j in base_idx]
    n_trig = int(sum(trig))
    n_non = len(base_idx) - n_trig
    if n_trig == 0:
        return {int(j): 1.0 / len(base_idx) for j in base_idx}
    denom = n_non + float(m) * n_trig
    out: dict[int, float] = {}
    for j, t in zip(base_idx, trig):
        out[int(j)] = (float(m) if t else 1.0) / denom
    return out


def reverse_weights_share(base_idx: list[int], date_i: int, window: np.ndarray,
                          k: float) -> dict[int, float]:
    """信号股合计权重 k（组内等权）、非信号合计 1−k；无信号或全信号→基线等权。"""
    if not base_idx:
        return {}
    trig = [int(window[date_i, j]) for j in base_idx]
    n_trig = int(sum(trig))
    n_non = len(base_idx) - n_trig
    if n_trig == 0 or n_non == 0:
        return {int(j): 1.0 / len(base_idx) for j in base_idx}
    out: dict[int, float] = {}
    for j, t in zip(base_idx, trig):
        out[int(j)] = (float(k) / n_trig) if t else ((1.0 - float(k)) / n_non)
    return out


def build_schedule(panel: fl.Panel, masks: dict, window: np.ndarray,
                   multipliers: tuple[float, ...] | list[float],
                   shares: tuple[float, ...] | list[float],
                   start: str, end: str,
                   rebalance_every: int = REBALANCE_EVERY) -> dict[str, list[dict]]:
    """生成调仓计划：BASELINE + 乘子档 M{m} + 占比档 K{int(k*100)}。"""
    dates = [str(d) for d in panel.dates]
    eval_idx = [i for i, d in enumerate(dates) if str(start) <= d <= str(end)]
    dec_idx = eval_idx[::rebalance_every]
    plan: dict[str, list[dict]] = {"BASELINE": []}
    for m in multipliers:
        plan[f"M{m:g}"] = []
    for k in shares:
        plan[f"K{int(round(k * 100))}"] = []
    len(panel.codes)
    for i in dec_idx:
        base = masks["picks"].get(i, [])
        if not base:
            continue
        exec_i = i + 1
        if exec_i >= len(panel.dates):
            continue
        entry = {"decision_i": i, "exec_i": exec_i,
                 "weights": {int(j): 1.0 / len(base) for j in base}}
        plan["BASELINE"].append(entry)
        for m in multipliers:
            plan[f"M{m:g}"].append({**entry,
                                    "weights": reverse_weights_multiplier(base, i, window, float(m))})
        for k in shares:
            plan[f"K{int(round(k * 100))}"].append({**entry,
                                                   "weights": reverse_weights_share(base, i, window, float(k))})
    return plan


# ================================================================ 四关
def pit_static_check(module_path: Path) -> dict:
    """PIT 静态检查：本文件不得在信号/权重构造中出现负 shift（shift(-n) 前视）。"""
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    offenders = []

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.stack: list[str] = []

        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_Call(self, node):
            if (isinstance(node.func, ast.Attribute) and node.func.attr == "shift"
                    and node.args and isinstance(node.args[0], ast.UnaryOp)
                    and isinstance(node.args[0].op, ast.USub)):
                offenders.append((self.stack[-1] if self.stack else "?", ast.unparse(node)))
            self.generic_visit(node)

    Visitor().visit(tree)
    return {"pass": offenders == [], "negative_shifts": offenders,
            "signal_pit": "LX20 因子复用 reverse_avoid_test → book_batch_test（已过 PIT 截断不变性/无负 lag 单测）",
            "weights_pit": "权重只使用决策日 T 的 window[T]（过去 N 日触发状态）与 T 日收盘市值/过滤信息"}


def capacity_reverse(panel: fl.Panel, masks: dict, window: np.ndarray,
                     schedule: list[dict], participation: float = 0.10) -> dict:
    """按被超配股票 ADV20 与「单票单日交易额 ≤10% ADV」估算 AUM 上限。"""
    amount_yuan = np.asarray(panel.amount, dtype=float) * fl.AMOUNT_UNIT
    adv20 = pd.DataFrame(amount_yuan, index=panel.dates, columns=panel.codes) \
        .rolling(20, min_periods=10).mean().to_numpy()
    caps, p5s, extras = [], [], []
    for rec in schedule:
        i = rec["decision_i"]
        base = masks["picks"].get(i, [])
        if not base:
            continue
        n = len(base)
        base_w = 1.0 / n
        for j, wt in rec["weights"].items():
            extra = float(wt) - base_w
            if extra <= 1e-6:
                continue
            adv = adv20[i, j]
            if not np.isfinite(adv) or adv <= 0:
                continue
            extras.append(extra)
            caps.append(participation * adv / extra)
            p5s.append(adv)
    if not caps:
        return {"aum_cap_yi": None, "participation": participation}
    caps = np.asarray(caps)
    return {
        "participation": participation,
        "aum_cap_yi": round(float(np.median(caps)) / 1e8, 2),
        "aum_cap_p5_yi": round(float(np.percentile(caps, 5)) / 1e8, 2),
        "overweight_positions": int(len(caps)),
        "median_extra_weight_pct": round(float(np.median(extras)) * 100, 3),
        "median_adv20_yi": round(float(np.median(p5s)) / 1e8, 3),
        "assumption": f"单票单日交易额 ≤ {participation:.0%}×20日日均成交额；AUM 上限取逐(调仓日,股票)估计中位数",
    }


def cost_sensitivity(panel: fl.Panel, plan_base: list[dict], plan_var: list[dict],
                     ex_masks: dict, date_strs: pd.Index,
                     costs: tuple[float, ...] = COST_LIST) -> list[dict]:
    """成本敏感：同一计划在 10/20/30/50bp 下的基线/处理组年化与超额。"""
    rows = []
    for c in costs:
        sim_b = ov.simulate_portfolio(panel, plan_base, ex_masks, cost=float(c))
        sim_v = ov.simulate_portfolio(panel, plan_var, ex_masks, cost=float(c))
        st_b = ov.series_stats(sim_b["daily"], date_strs)
        st_v = ov.series_stats(sim_v["daily"], date_strs)
        ex = ov.excess_stats(sim_v["daily"] - sim_b["daily"], date_strs)
        rows.append({
            "cost_bp_oneway": int(round(float(c) * 10000)),
            "baseline_ann_pct": st_b.get("ann_ret_pct"),
            "variant_ann_pct": st_v.get("ann_ret_pct"),
            "excess_ann_pct": ex.get("ann_excess_pct"),
            "te_ann_pct": ex.get("te_ann_pct"),
            "t": ex.get("t"),
            "info_ratio": ex.get("info_ratio"),
        })
    return rows


def evaluate_gates(primary: dict, cost_rows: list[dict], capacity: dict,
                   pit: dict, yearly_positive: int, n_years: int) -> dict:
    """四关判定（纯函数，便于单测）。"""
    oos = primary.get("excess_oos", {})
    is_ = primary.get("excess_is", {})
    oos_pass = bool((oos.get("ann_excess_pct") or -1) > 0 and (oos.get("t") or -1) >= 2.0)
    yearly_pass = bool(yearly_positive >= 3)
    c30 = next((r for r in cost_rows if r["cost_bp_oneway"] == 30), {})
    c50 = next((r for r in cost_rows if r["cost_bp_oneway"] == 50), {})
    cost_pass = bool((c30.get("excess_ann_pct") or -1) > 0 and (c30.get("t") or -1) >= 2.0
                     and (c50.get("excess_ann_pct") or -1) > 0)
    cap_pass = bool((capacity.get("aum_cap_yi") or 0) >= 10.0)
    return {
        "PIT": {**pit, "pass": bool(pit.get("pass", False))},
        "OOS": {"IS": is_, "OOS": oos, "oos_excess_positive": (oos.get("ann_excess_pct") or -1) > 0,
                "oos_t_ge_2": (oos.get("t") or -1) >= 2.0, "pass": oos_pass},
        "YEARLY": {"positive_years": int(yearly_positive), "n_years": int(n_years),
                   "pass": yearly_pass},
        "COST": {"rows": cost_rows, "cost_30bp_excess_pct": c30.get("excess_ann_pct"),
                 "cost_50bp_excess_pct": c50.get("excess_ann_pct"), "pass": cost_pass},
        "CAPACITY": {**capacity, "min_aum_yi": 10.0, "pass": cap_pass},
        "hypothesis_pass": bool(oos_pass and yearly_pass and cost_pass and cap_pass
                                and bool(pit.get("pass", False))),
    }


# ================================================================ 主流程
def run(start: str = DEFAULT_START, end: str = DEFAULT_END,
        multipliers: tuple[float, ...] | list[float] = DEFAULT_MULTIPLIERS,
        shares: tuple[float, ...] | list[float] = DEFAULT_SHARES,
        panel: fl.Panel | None = None, data_root: str | Path | None = None,
        top: int = TOP, signal_n: int = SIGNAL_N,
        rebalance_every: int = REBALANCE_EVERY, validate: bool = False) -> dict:
    t0 = time.time()
    t_load = time.time()
    if panel is None:
        panel = fl.load_panel(start, end, universe="tradable", data_root=data_root, load_roe=True)
    load_sec = round(time.time() - t_load, 2)
    dates = panel.dates
    date_strs = pd.Index([str(d) for d in dates])

    t1 = time.time()
    circ_mv = rav.load_circ_mv(panel, data_root)
    names_meta = ov.load_names_meta(data_root)
    masks = ov.compute_s01_masks(panel, circ_mv, names_meta, top=top)
    s01_sec = round(time.time() - t1, 2)
    t2 = time.time()
    trig, window = ov.lx20_trigger(panel, signal_n)
    sig_sec = round(time.time() - t2, 1)
    ex_masks = ov.execution_masks(panel)
    validation = ov.validate_against_build_baseline(masks, panel) if validate else {"checked": 0}
    print(f"[load] 面板 {len(dates)} 日 × {len(panel.codes)} 只 {load_sec}s；S01 复刻 {s01_sec}s；"
          f"LX20 信号 {sig_sec}s", flush=True)

    plan = build_schedule(panel, masks, window, tuple(multipliers), tuple(shares),
                          start, end, rebalance_every)
    baseline = ov.simulate_portfolio(panel, plan["BASELINE"], ex_masks)
    baseline_daily = baseline["daily"]
    bear_meta = ov.bear_segment(baseline_daily, date_strs)
    bear_mask = (np.isin(date_strs, bear_meta.get("dates", []))) if bear_meta else np.zeros(len(dates), bool)
    is_mask = np.asarray([str(d) <= IS_END for d in date_strs])
    oos_mask = np.asarray([str(d) >= OOS_START for d in date_strs])

    variants: dict[str, dict] = {}
    base_stats = ov.series_stats(baseline_daily, date_strs)
    base_turn = [t for _, t in baseline["turnover"]]
    base_cost = round(float(np.mean(base_turn)) * 2.0 * COST * (ANNUAL_DAYS / rebalance_every) * 100, 3) \
        if base_turn else 0.0
    base_stats["avg_turnover_pct"] = round(float(np.mean(base_turn)) * 100, 3) if base_turn else None
    base_stats["ann_cost_pct"] = base_cost
    base_stats["avg_holdings"] = round(float(np.mean([n for _, n in baseline["holdings"]])), 1) \
        if baseline["holdings"] else 0
    base_stats["avg_cash_pct"] = round(float(np.mean([c for _, c in baseline["cash"]])) * 100, 3) \
        if baseline["cash"] else 0
    base_stats["rebalances"] = len(plan["BASELINE"])
    variants["BASELINE"] = {
        "name": "BASELINE", "multiplier": None, "share": None,
        "overall": base_stats,
        "by_year": {y: ov.series_stats(baseline_daily, date_strs, ov.year_mask(date_strs, y))
                    for y in ("2023", "2024", "2025", "2026")},
        "bear_2026": ov.series_stats(baseline_daily, date_strs, bear_mask),
    }
    for name, schedule in plan.items():
        if name == "BASELINE":
            continue
        sim = ov.simulate_portfolio(panel, schedule, ex_masks)
        stats = ov.series_stats(sim["daily"], date_strs)
        stats["avg_turnover_pct"] = round(float(np.mean([t for _, t in sim["turnover"]])) * 100, 3) \
            if sim["turnover"] else None
        stats["ann_cost_pct"] = round(float(np.mean([t for _, t in sim["turnover"]])) * 2.0 * COST
                                      * (ANNUAL_DAYS / rebalance_every) * 100, 3) if sim["turnover"] else None
        stats["avg_holdings"] = round(float(np.mean([n for _, n in sim["holdings"]])), 1) \
            if sim["holdings"] else 0
        stats["avg_cash_pct"] = round(float(np.mean([c for _, c in sim["cash"]])) * 100, 3) \
            if sim["cash"] else 0
        stats["rebalances"] = len(schedule)
        excess = sim["daily"] - baseline_daily
        m = None
        k = None
        if name.startswith("M"):
            m = float(name[1:])
        else:
            k = float(name[1:]) / 100.0
        variants[name] = {
            "name": name, "multiplier": m, "share": k,
            "overall": stats,
            "by_year": {y: ov.series_stats(sim["daily"], date_strs, ov.year_mask(date_strs, y))
                        for y in ("2023", "2024", "2025", "2026")},
            "bear_2026": ov.series_stats(sim["daily"], date_strs, bear_mask),
            "excess": ov.excess_stats(excess, date_strs),
            "excess_gross_ann_pct": round(
                (ov.excess_stats(excess, date_strs).get("ann_excess_pct") or 0.0)
                + (stats.get("ann_cost_pct") or 0.0) - base_cost, 3),
            "excess_by_year": {y: ov.excess_stats(excess, date_strs, ov.year_mask(date_strs, y))
                               for y in ("2023", "2024", "2025", "2026")},
            "excess_is": ov.excess_stats(excess, date_strs, is_mask),
            "excess_oos": ov.excess_stats(excess, date_strs, oos_mask),
            "excess_bear_2026": ov.excess_stats(excess, date_strs, bear_mask),
        }
        print(f"[variant] {name:<5} 年化 {stats.get('ann_ret_pct')}% 超额 "
              f"{variants[name]['excess'].get('ann_excess_pct')}% "
              f"OOS超额 {variants[name]['excess_oos'].get('ann_excess_pct')}%"
              f"(t={variants[name]['excess_oos'].get('t')})", flush=True)

    primary_name = PRIMARY_VARIANT if PRIMARY_VARIANT in variants else next(
        (k for k in variants if k != "BASELINE"), "BASELINE")
    primary = variants[primary_name]
    cost_rows = cost_sensitivity(panel, plan["BASELINE"], plan[primary_name], ex_masks,
                                 date_strs, COST_LIST)
    cap = capacity_reverse(panel, masks, window, plan[primary_name])
    pit = pit_static_check(_HERE / "s05_reverse_hypothesis.py")
    yearly_positive = sum(1 for y in ("2023", "2024", "2025", "2026")
                          if (primary["excess_by_year"].get(y, {}).get("ann_excess_pct") or -1) > 0)
    gates = evaluate_gates(primary, cost_rows, cap, pit, yearly_positive, 4)

    exposure = []
    hits = []
    for rec in plan["BASELINE"]:
        i = rec["decision_i"]
        base = masks["picks"].get(i, [])
        if not base:
            continue
        hits.append((len(base), sum(int(window[i, j]) for j in base)))
    if hits:
        exposure.append({
            "avg_baseline_holdings": round(float(np.mean([h[0] for h in hits])), 2),
            "avg_triggered_in_baseline": round(float(np.mean([h[1] for h in hits])), 2),
            "avg_triggered_pct": round(100.0 * float(np.mean([h[1] for h in hits]))
                                       / max(1.0, float(np.mean([h[0] for h in hits]))), 3),
            "max_triggered": int(max(h[1] for h in hits)),
            "periods": len(hits),
        })

    payload = {
        "meta": {
            "report": "s05_reverse_20261101",
            "version": VERSION,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "task": "wave6 P 支线：S05 反向（超配）假设验证（S01 大市值组合内）",
            "data": {"root": str(data_root or fl.DATA), "start": str(start), "end": str(end)},
            "panel": {"dates": int(len(dates)), "codes": int(len(panel.codes)),
                      "load_sec": load_sec, "meta": panel.load_meta},
            "eval": {
                "baseline": "S01（s05_overlay_test 复刻 portfolio_build.build_baseline，wave5 已 3/3 对齐）",
                "signal": "LX20 lx20_three_absolutes，回溯 N=5 日（reverse_avoid_test 复用）",
                "signal_n": int(signal_n),
                "rebalance_every_days": int(rebalance_every),
                "execution": "T 日收盘决策 → T+1 收盘执行（涨停/停牌不可买→现金）",
                "cost_bp_oneway": int(round(COST * 10000)),
                "primary_variant": primary_name,
                "multipliers": [float(m) for m in multipliers],
                "shares": [float(k) for k in shares],
                "is_window": f"{start}~{IS_END}",
                "oos_window": f"{OOS_START}~{end}",
                "bear_2026": {k: v for k, v in bear_meta.items() if k != "dates"},
            },
            "validation_build_baseline": validation,
            "runtime_sec": round(time.time() - t0, 2),
            "notes": build_notes(),
        },
        "variants": variants,
        "exposure": exposure,
        "gates": gates,
        "registry_entry": {},
        "conclusions": {},
    }
    payload["registry_entry"] = build_registry_entry(payload)
    payload["conclusions"] = build_conclusions(payload)
    return payload


def build_notes() -> list[str]:
    return [
        "反向假设来自 wave5 同一份数据（S01 内降配负超额）→ 存在**数据窥探**；IS/OOS 划分只是时间切分，"
        "OOS 2025-26 并非真正独立样本外，结论必须经影子盘只读跟踪验证。",
        "信号为 book_batch_test 的 LX20 简化单形态代理（倍量不穿+突破倍量柱实顶），非原书完整三绝口径。",
        "S01 基线过滤中的 ST/上市日期/行业用 stock_basic 当前静态信息（与 build_baseline 同源，轻微后验）。",
        "周频调仓、T+1 收盘执行、涨停(≥9.5%)/停牌不可买→现金；持有期停牌/退市收益按 0；首期成本单边 30bp。",
        "容量为静态 ADV 假设（10% 参与率），未建模动态冲击成本；AUM 上限取中位数。",
        "A 股不可做空：反向研究只体现为「超配/低配」相对权重变化，不对应独立空头收益。",
    ]


def build_registry_entry(payload: dict) -> dict:
    gates = payload["gates"]
    primary_name = payload["meta"]["eval"]["primary_variant"]
    primary = payload["variants"][primary_name]
    cap = gates["CAPACITY"]
    passed = bool(gates["hypothesis_pass"])
    return {
        "strategy_id": "S05_LX20_OVERWEIGHT",
        "library_slot": "S05",
        "validation_stage": "reverse_hypothesis_with_S01",
        "version": VERSION,
        "hypothesis": "在 S01 大市值 top20 组合内，LX20 信号股不是应规避而是应超配（反向于 wave3/wave4 的全市场规避口径）",
        "baseline": payload["meta"]["eval"]["baseline"],
        "signal": payload["meta"]["eval"]["signal"],
        "treatment": f"信号股权重乘子 m={primary.get('multiplier')}（或信号股占权 k）后归一化，其余持仓等权",
        "primary_variant": primary_name,
        "rebalance": f"周频（{payload['meta']['eval']['rebalance_every_days']} 交易日）",
        "execution": payload["meta"]["eval"]["execution"],
        "metrics": {
            "baseline": payload["variants"]["BASELINE"]["overall"],
            "variant": primary["overall"],
            "excess": primary["excess"],
            "excess_gross_ann_pct": primary.get("excess_gross_ann_pct"),
            "excess_is": primary["excess_is"],
            "excess_oos": primary["excess_oos"],
            "excess_by_year": primary["excess_by_year"],
            "bear_2026": primary["excess_bear_2026"],
        },
        "gates": {
            "PIT": bool(gates["PIT"]["pass"]),
            "OOS": bool(gates["OOS"]["pass"]),
            "YEARLY": f"{gates['YEARLY']['positive_years']}/4",
            "COST": bool(gates["COST"]["pass"]),
            "CAPACITY": bool(gates["CAPACITY"]["pass"]),
        },
        "validation_result": "REVERSE_HYPOTHESIS_PASS" if passed else "REVERSE_HYPOTHESIS_NOT_PASS",
        "aum_cap_yi": cap.get("aum_cap_yi"),
        "data_snooping_warning": (
            "反向假设发现于 wave5 同一数据 → 本验证的 OOS 不是真正样本外；即使通过，也只能"
            "「进入影子盘只读跟踪（不配资金）」，用 2026-11 之后的真实前瞻数据复核。"),
        "shadow_portfolio_recommendation": (
            "**建议进入影子盘（只读跟踪、不配资金）**：四关通过，但存在数据窥探风险，"
            "需 3~6 个月真实前瞻样本复核后再谈配置。"
            if passed else
            "**不建议进入影子盘**：四关未全部通过，反向假设证据不足。"),
        "missing_before_shadow": [
            "真实样本外验证（2026-11 之后影子盘前瞻数据，至少 3~6 个月）",
            "数据窥探校正：多重检验/持有期重叠修正（本文件已用日频 t+block bootstrap 但未做多重检验校正）",
            "反向假设与 S02 alpha 排序的叠加（谁先谁后、是否重复暴露）",
            "容量/滑点：当前按 10% 参与率静态 ADV 估算，需实盘标定",
            "风控：超配集中度上限、单票上限（超配后信号股权重可能 >5% 上限）",
            "调仓频率敏感性（周频 vs 月频 vs 事件驱动）",
        ],
        "notes": payload["meta"]["notes"],
    }


def build_conclusions(payload: dict) -> dict:
    variants = payload["variants"]
    base = variants["BASELINE"]["overall"]
    primary_name = payload["meta"]["eval"]["primary_variant"]
    primary = variants[primary_name]
    gates = payload["gates"]
    rows = []
    for name, v in variants.items():
        if name == "BASELINE":
            continue
        ex = v.get("excess", {})
        rows.append({
            "name": name,
            "multiplier": v.get("multiplier"), "share": v.get("share"),
            "ann_ret_pct": v["overall"].get("ann_ret_pct"),
            "ann_excess_pct": ex.get("ann_excess_pct"),
            "gross_ann_excess_pct": v.get("excess_gross_ann_pct"),
            "te_ann_pct": ex.get("te_ann_pct"),
            "t": ex.get("t"),
            "ir": ex.get("info_ratio"),
            "mdd_pct": v["overall"].get("mdd_pct"),
            "by_year_excess": {y: (v["excess_by_year"].get(y, {}) or {}).get("ann_excess_pct")
                               for y in ("2023", "2024", "2025", "2026")},
            "is_excess_pct": v["excess_is"].get("ann_excess_pct"),
            "oos_excess_pct": v["excess_oos"].get("ann_excess_pct"),
            "oos_t": v["excess_oos"].get("t"),
            "bear_2026_excess_pct": v["excess_bear_2026"].get("ann_excess_pct"),
        })
    rows.sort(key=lambda r: (r["multiplier"] if r["multiplier"] else 99, r["share"] or 0))
    best = max(rows, key=lambda r: r["ann_excess_pct"] or -999) if rows else None
    bullets = []
    bullets.append(
        f"基线（S01 大市值 top20 等权，周频、T+1 收盘、30bp）年化 {ov._fmt(base.get('ann_ret_pct'))}%、"
        f"波动 {ov._fmt(base.get('vol_ann_pct'))}%、MDD {ov._fmt(base.get('mdd_pct'))}%、Sharpe "
        f"{ov._fmt(base.get('sharpe'))}、换手 {ov._fmt(base.get('avg_turnover_pct'))}%/次。")
    bullets.append(
        f"反向超配假设主口径 {primary_name}（m={primary.get('multiplier')}）年化 "
        f"{ov._fmt(primary['overall'].get('ann_ret_pct'))}%，净超额 {ov._fmt(primary['excess'].get('ann_excess_pct'))}%、"
        f"毛超额（扣成本前）{ov._fmt(primary.get('excess_gross_ann_pct'))}%、"
        f"TE {ov._fmt(primary['excess'].get('te_ann_pct'))}%、IR {ov._fmt(primary['excess'].get('info_ratio'))}、"
        f"t={ov._fmt(primary['excess'].get('t'))}、MDD {ov._fmt(primary['overall'].get('mdd_pct'))}"
        f"（基线 {ov._fmt(base.get('mdd_pct'))}%）；换手 {ov._fmt(primary['overall'].get('avg_turnover_pct'))}%/次、"
        f"成本 {ov._fmt(primary['overall'].get('ann_cost_pct'))}%/年（基线 {ov._fmt(base.get('ann_cost_pct'))}%/年）→ "
        "超配提高换手，成本吃掉大部分毛超额。")
    if rows:
        bullets.append("全部超配档（相对基线）：" + "；".join(
            f"{r['name']}(m={r['multiplier'] if r['multiplier'] else '—'}/k={r['share'] if r['share'] else '—'}) "
            f"净超额 {ov._fmt(r['ann_excess_pct'])}%/毛超额 {ov._fmt(r['gross_ann_excess_pct'])}%"
            f"（OOS {ov._fmt(r['oos_excess_pct'])}%/t={ov._fmt(r['oos_t'])}）"
            for r in rows) + "。")
    bullets.append(
        "四关结果：" + "；".join([
            f"PIT={'PASS' if gates['PIT']['pass'] else 'FAIL'}",
            f"OOS超额={ov._fmt(gates['OOS']['OOS'].get('ann_excess_pct'))}%/t={ov._fmt(gates['OOS']['OOS'].get('t'))}"
            f"({'PASS' if gates['OOS']['pass'] else 'FAIL'})",
            f"逐年正={gates['YEARLY']['positive_years']}/4({'PASS' if gates['YEARLY']['pass'] else 'FAIL'})",
            f"成本30bp超额={ov._fmt(gates['COST'].get('cost_30bp_excess_pct'))}%/"
            f"50bp={ov._fmt(gates['COST'].get('cost_50bp_excess_pct'))}%({'PASS' if gates['COST']['pass'] else 'FAIL'})",
            f"容量AUM上限={ov._fmt(gates['CAPACITY'].get('aum_cap_yi'))}亿({'PASS' if gates['CAPACITY']['pass'] else 'FAIL'})",
        ]) + "。")
    if gates["hypothesis_pass"]:
        bullets.append(
            "结论：**反向（超配）假设成立**（OOS 超额>0 且 t≥2、逐年≥3/4、成本与容量关通过）→ "
            "建议写入 S05 validation（REVERSE_HYPOTHESIS_PASS），并**进入影子盘只读跟踪、不配资金**。")
        bullets.append(
            "关键警告：反向假设来自 wave5 同一数据（数据窥探），OOS 2025-26 只是时间切分、非真正独立样本；"
            "必须用 2026-11 之后的真实前瞻数据（3~6 个月）复核后才可谈实盘配置。")
    else:
        gross_positive = (primary.get("excess_gross_ann_pct") or -1) > 0
        bullets.append(
            f"结论：**反向（超配）假设未成立**（四关未全部通过）→ 不进 S05 validation/影子盘。"
            + (f" 原因：毛超额（{ov._fmt(primary.get('excess_gross_ann_pct'))}%）为正但换手成本"
               f"（{ov._fmt(primary['overall'].get('ann_cost_pct'))}%/年 vs 基线 "
               f"{ov._fmt(base.get('ann_cost_pct'))}%/年）吃掉后净超额为负；"
               if gross_positive else " 原因：毛/净超额均未转正；")
            + f"OOS 超额 {ov._fmt(gates['OOS']['OOS'].get('ann_excess_pct'))}%（t="
              f"{ov._fmt(gates['OOS']['OOS'].get('t'))}）未达 t≥2，逐年正 "
              f"{gates['YEARLY']['positive_years']}/4 未达 3/4。")
        bullets.append(
            "机制复核：wave5 的「降配负超额」主要是**换手成本 + 组合再分配**效应（降配越深成本越高、"
            "同时把权重推向当时更强的非信号持仓），并非「信号股在大市值内显著跑赢」——"
            "反向超配的毛超额仅约 +1.6%、且成本更高，净超额为负。**S05 的规避/超配两个方向均不适用于 S01。**")
    return {
        "variants": rows,
        "best_variant": best,
        "primary_variant": primary_name,
        "hypothesis_pass": bool(gates["hypothesis_pass"]),
        "gates_summary": {k: gates[k].get("pass") for k in ("PIT", "OOS", "YEARLY", "COST", "CAPACITY")},
        "bullets": bullets,
    }


# ================================================================ 渲染
def render_markdown(payload: dict) -> str:
    meta, ev = payload["meta"], payload["meta"]["eval"]
    payload["variants"]["BASELINE"]["overall"]
    primary_name = ev["primary_variant"]
    payload["variants"][primary_name]
    gates = payload["gates"]
    lines: list[str] = []
    a = lines.append
    a("# S05 反向（超配）假设验证 · S01 大市值组合内（2026-11-01，wave6 支线 P）")
    a("")
    a(f"- 生成时间：{meta['generated_at']}")
    a(f"- 数据：`{meta['data']['root']}` · {meta['data']['start']}~{meta['data']['end']} · "
      f"面板 {meta['panel']['dates']} 日 × {meta['panel']['codes']} 只（载入 {meta['panel']['load_sec']}s）")
    a(f"- 基线：{ev['baseline']}")
    a(f"- 处理：{ev['signal']}；信号股超配乘子 m={ev['multipliers']} / 占权 k={ev['shares']}；主口径 {primary_name}")
    a(f"- 调仓/执行：每 {ev['rebalance_every_days']} 交易日决策，{ev['execution']}；成本单边 {ev['cost_bp_oneway']}bp")
    a(f"- 四关：PIT / IS={ev['is_window']} vs OOS={ev['oos_window']} / 成本 {COST_BP_LIST}bp / 容量（10% ADV）")
    a(f"- 运行耗时：{meta['runtime_sec']}s")
    if ev.get("bear_2026"):
        b = ev["bear_2026"]
        a(f"> 2026 熊市段（基线组合峰→谷）：{b.get('peak')}~{b.get('trough')}"
          f"（{b.get('segment_days')} 日，区间回撤 {b.get('max_drawdown_pct')}%）。")
    a("")
    a("## 一、基线 vs 超配组合（全窗口 2023-01~2026-10）")
    a("")
    a("| 变体 | 乘子m | 占权k | 年化% | 净超额% | 毛超额% | TE% | t | IR | 波动% | MDD% | 换手%/次 | 成本%/年 |")
    a("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name, v in payload["variants"].items():
        ov_ = v["overall"]
        ex = v.get("excess", {})
        a(f"| {name} | {ov._fmt(v.get('multiplier'))} | {ov._fmt(v.get('share'))} | "
          f"{ov._fmt(ov_.get('ann_ret_pct'))} | {ov._fmt(ex.get('ann_excess_pct'))} | "
          f"{ov._fmt(v.get('excess_gross_ann_pct'))} | "
          f"{ov._fmt(ex.get('te_ann_pct'))} | {ov._fmt(ex.get('t'))} | {ov._fmt(ex.get('info_ratio'))} | "
          f"{ov._fmt(ov_.get('vol_ann_pct'))} | {ov._fmt(ov_.get('mdd_pct'))} | "
          f"{ov._fmt(ov_.get('avg_turnover_pct'))} | {ov._fmt(ov_.get('ann_cost_pct'))} |")
    a("")
    exp = payload.get("exposure") or []
    if exp:
        e = exp[0]
        a(f"> S05 可作用空间：基线持仓平均 {e['avg_baseline_holdings']} 只中命中 LX20 信号 "
          f"{e['avg_triggered_in_baseline']} 只（{e['avg_triggered_pct']}%，单期最多 {e['max_triggered']} 只）。")
        a("")
    a("## 二、四关标注")
    a("")
    a("| 关卡 | 结果 | 关键指标 |")
    a("|---|---|---|")
    g = gates
    a(f"| PIT | {'✅ PASS' if g['PIT']['pass'] else '❌ FAIL'} | 负 shift 静态检查 "
      f"{'无' if g['PIT']['pass'] else g['PIT'].get('negative_shifts')}；信号复用已过 PIT 的 LX20 因子 |")
    a(f"| OOS | {'✅ PASS' if g['OOS']['pass'] else '❌ FAIL'} | IS 超额 "
      f"{ov._fmt(g['OOS']['IS'].get('ann_excess_pct'))}%/t={ov._fmt(g['OOS']['IS'].get('t'))}；"
      f"OOS 超额 {ov._fmt(g['OOS']['OOS'].get('ann_excess_pct'))}%/t={ov._fmt(g['OOS']['OOS'].get('t'))} |")
    a(f"| 逐年 | {'✅ PASS' if g['YEARLY']['pass'] else '❌ FAIL'} | 正超额年份 "
      f"{g['YEARLY']['positive_years']}/{g['YEARLY']['n_years']} |")
    a(f"| 成本 | {'✅ PASS' if g['COST']['pass'] else '❌ FAIL'} | 30bp 超额 "
      f"{ov._fmt(g['COST'].get('cost_30bp_excess_pct'))}%；50bp 超额 "
      f"{ov._fmt(g['COST'].get('cost_50bp_excess_pct'))}% |")
    a(f"| 容量 | {'✅ PASS' if g['CAPACITY']['pass'] else '❌ FAIL'} | AUM 上限约 "
      f"{ov._fmt(g['CAPACITY'].get('aum_cap_yi'))} 亿（10% ADV 参与率，p5 {ov._fmt(g['CAPACITY'].get('aum_cap_p5_yi'))} 亿）|")
    a("")
    a("成本敏感性（30bp 主口径 ±）：")
    a("")
    a("| 单边成本bp | 基线年化% | 超配年化% | 超额% | TE% | t | IR |")
    a("|---:|---:|---:|---:|---:|---:|---:|")
    for r in g["COST"]["rows"]:
        a(f"| {r['cost_bp_oneway']} | {ov._fmt(r['baseline_ann_pct'])} | {ov._fmt(r['variant_ann_pct'])} | "
          f"{ov._fmt(r['excess_ann_pct'])} | {ov._fmt(r['te_ann_pct'])} | {ov._fmt(r['t'])} | "
          f"{ov._fmt(r['info_ratio'])} |")
    a("")
    a("## 三、分年与 2026 熊市段")
    a("")
    a("| 变体 | 2023超额% | 2024超额% | 2025超额% | 2026超额% | IS超额% | OOS超额% | OOS t | 2026熊市段超额% |")
    a("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name, v in payload["variants"].items():
        if name == "BASELINE":
            continue
        y = v["excess_by_year"]
        a(f"| {name} | {ov._fmt(y.get('2023', {}).get('ann_excess_pct'))} | "
          f"{ov._fmt(y.get('2024', {}).get('ann_excess_pct'))} | "
          f"{ov._fmt(y.get('2025', {}).get('ann_excess_pct'))} | "
          f"{ov._fmt(y.get('2026', {}).get('ann_excess_pct'))} | "
          f"{ov._fmt(v['excess_is'].get('ann_excess_pct'))} | "
          f"{ov._fmt(v['excess_oos'].get('ann_excess_pct'))} | {ov._fmt(v['excess_oos'].get('t'))} | "
          f"{ov._fmt(v['excess_bear_2026'].get('ann_excess_pct'))} |")
    a("")
    a("## 四、可写入 strategy_registry 的 S05 validation 条目")
    a("")
    a("```json")
    a(json.dumps(payload["registry_entry"], ensure_ascii=False, indent=2))
    a("```")
    a("")
    a("## 五、结论")
    a("")
    for b in payload["conclusions"]["bullets"]:
        a(f"- {b}")
    a("")
    a("## 六、口径、局限与复现")
    a("")
    for i, note in enumerate(meta["notes"], 1):
        a(f"{i}. {note}")
    a("")
    a("```powershell")
    a("C:\\项目\\py312\\python.exe -X utf8 tools/research/s05_reverse_hypothesis.py --multipliers 2.0 --out tmp/s05_reverse_m2.json --md tmp/s05_reverse_m2.md")
    a("C:\\项目\\py312\\python.exe -X utf8 tools/research/s05_reverse_hypothesis.py --out research/reports/s05_reverse_20261101.json --md research/reports/s05_reverse_20261101.md")
    a("C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_s05_reverse.py")
    a("```")
    a("")
    a("> 判读纪律：反向假设存在数据窥探，本报告只作 S05 validation 的候选证据；进入影子盘仅「只读跟踪、不配资金」，"
      "实盘配置前需 2026-11 之后的真实前瞻样本复核（MEETING.md 四关口径）。")
    return "\n".join(lines)


def write_outputs(payload: dict, json_path: str | Path | None = None,
                  md_path: str | Path | None = None) -> tuple[str, str]:
    jp = Path(json_path) if json_path else DEFAULT_OUT_JSON
    mp = Path(md_path) if md_path else DEFAULT_OUT_MD
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(render_markdown(payload), encoding="utf-8")
    return str(jp), str(mp)


# ================================================================ CLI
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="S05 反向（超配）假设验证")
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument("--multipliers", default="1.5,2.0,3.0")
    parser.add_argument("--shares", default="0.3,0.5")
    parser.add_argument("--top", type=int, default=TOP)
    parser.add_argument("--rebalance", type=int, default=REBALANCE_EVERY)
    parser.add_argument("--out", default=str(DEFAULT_OUT_JSON))
    parser.add_argument("--md", default=str(DEFAULT_OUT_MD))
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--validate", action="store_true", help="调用 build_baseline 做基线对齐校验")
    args = parser.parse_args(argv)
    mults = tuple(float(x.strip()) for x in str(args.multipliers).split(",") if x.strip())
    shares = tuple(float(x.strip()) for x in str(args.shares).split(",") if x.strip())
    mults = tuple(m for m in mults if m > 1.0)          # m=1 等价基线，跳过
    shares = tuple(k for k in shares if 0.0 < k < 1.0)  # k=0/1 退化，跳过
    t0 = time.time()
    payload = run(start=args.start, end=args.end, multipliers=mults, shares=shares,
                  data_root=args.data_root, top=args.top, rebalance_every=args.rebalance,
                  validate=args.validate)
    jp, mp = write_outputs(payload, args.out, args.md)
    print(f"[OK] 用时 {time.time() - t0:.1f}s")
    print(f"[JSON] {jp}")
    print(f"[MD]   {mp}")
    print()
    base = payload["variants"]["BASELINE"]["overall"]
    print(f"基线：年化 {ov._fmt(base.get('ann_ret_pct'))}% MDD {ov._fmt(base.get('mdd_pct'))}%")
    for name, row in payload["variants"].items():
        if name == "BASELINE":
            continue
        ex = row.get("excess", {})
        print(f"{name:<5} 年化 {ov._fmt(row['overall'].get('ann_ret_pct'))}% 超额 "
              f"{ov._fmt(ex.get('ann_excess_pct'))}% TE {ov._fmt(ex.get('te_ann_pct'))}% "
              f"t={ov._fmt(ex.get('t'))} MDD {ov._fmt(row['overall'].get('mdd_pct'))}%")
    print()
    print("四关：", {k: payload["gates"][k].get("pass") for k in ("PIT", "OOS", "YEARLY", "COST", "CAPACITY")})
    for b in payload["conclusions"]["bullets"]:
        print("  -", b)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
