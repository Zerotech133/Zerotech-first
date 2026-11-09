# -*- coding: utf-8 -*-
"""出场规则研究：hold 比 trail 高 0.97pp 是真的规则优势，还是只是"暴露更久"。

## 为什么单独立一个脚本

择时因子是「选子集」，所以检验必须是「随机剔同样多天」。
出场规则不是——**同一批笔、同一个开仓日，只改退出时点**，天然配对，
所以正确检验是「配对差 + 按日聚类」，不是随机抽天数。

用错的检验会得到错的结论：
- 用「随机抽同样天数」评出场规则 → 笔数没变，零分布根本不该用「抽天数」构造
- 只看「每笔收益」→ hold 持 20 日必然比 trail（均 9.4 日）暴露更多，
  任何正漂移市场里 hold 都会"赢"，这与规则好坏无关

## 三个必须先做的分解（顺序不能乱）

[1] **暴露归因**：hold 与 trail 的差能否被「多持天数 × 期间收益」完全解释。
    精确分解：hold_ret = (1+trail_ret) × (1+seg) - 1，seg = c[20]/c[t_exit]-1。
    若 Σseg 贡献 = 全部差距 → 规则本身没有alpha，只是仓位更重。
[2] **持有期匹配对照**：构造 hold_N 且 N = trail 的平均持有天数（约 9 日）。
    若 hold_9 ≈ trail_8 → 止损的"择时触发"毫无价值，差异全来自"提前离场"。
    若 hold_9 << trail_8 → 止损确实挑对了时机（下跌前出）。
[3] **配对 t 检验（按日聚类）**：diff = rule - hold20，按开仓日聚合日均 diff，
    再对日序列做 t。同日多笔高度相关，不聚类会严重高估显著性。

## 三套指标，回答三个不同问题（不强行选赢家）

- 笔均       ：每笔期望，回答"同样一次机会能赚多少"
- 日批均     ：每开仓日等权再按日等权，回答"每天做这笔值不值"（笔数相同的规则间可比）
- 每持仓日收益：日批均 / 平均持有天数，回答"占用资金的时间效率"
  注意：这是**单笔资金周转的上界**，忽略了同日多笔要分资金，不能当账户收益读。

## 判定口径

主结论只认「参数邻域稳定」（±1 档仍同向）+ 分年一致 + 配对 t 显著。
孤峰参数一律视为过拟合，不采纳。
"""
import argparse
import json
import os
import sys
from collections import defaultdict

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from portfolio_mtm import BUY_COST, SELL_COST, load_price_panel  # noqa: E402

QBT = os.path.join(ROOT, "quant_backtest")
TDX = os.path.join(QBT, "data", "tdx")
POOL = os.path.join(QBT, "data", "chanlun_pool_ext_v2.jsonl")
OUT = os.path.join(ROOT, "portal", "exit_rule_study.json")

BUY = ("1买", "2买")            # 与 ①③ 一致：剔 3买
BASE_HOLD = 20                  # 基准规则 hold_20
MAX_WIN = 61                    # 最长观察窗（hold_60 要 60 根）
COST_MULT = 1.0 / ((1.0 + BUY_COST) / (1.0 - SELL_COST))   # ≈0.998
SEED = 20261108
BOOT = 400


# ---------------------------------------------------------------- 规则实现

def sim(rule, o, h, l, c, ei, entry_px):
    """在单只票的序列上模拟一次出场。价格已由 k 归一到 entry_px 标度。

    o/h/l/c: 该票的复权 OHLC（原始标度，函数内部乘 k）
    ei     : 入场日在票内序列的下标
    rule   : (kind, p1, p2, max_hold)

    返回 (exit_i, exit_px, days, reason)。days 为持有交易日数。

    三条实现约定（都踩过坑）：
    1. peak 初值必须是 entry_px。若从 0 或从首根 high 起算，
       要么入场即触发，要么首根跌停绕过止损。
    2. 触发价取 min(lvl, open)：跳空低开时只能按开盘价成交，
       按 lvl 成交会系统性高估止损收益。
    3. 只在 j≥1 检查，入场当日不检查（当日已按 entry_px 成交）。
    """
    kind, p1, p2, max_hold = rule
    n = len(c)
    k = entry_px / c[ei]
    hard = min(ei + max_hold, n - 1)

    stop_lvl = entry_px * (1.0 - p2) if kind in ("trail_stop", "stop") else None
    peak = entry_px
    be_armed = False

    for j in range(ei + 1, hard + 1):
        oj, hj, lj = o[j] * k, h[j] * k, l[j] * k
        if not np.isfinite(oj):
            continue

        lvl = -np.inf
        if kind == "hold":
            pass
        elif kind == "trail":
            lvl = max(entry_px * (1.0 - p1), peak * (1.0 - p1))
        elif kind == "stop":
            lvl = entry_px * (1.0 - p1)
        elif kind == "trail_stop":
            lvl = max(entry_px * (1.0 - p1), peak * (1.0 - p1), stop_lvl)
        elif kind == "breakeven":
            # 先涨到 p1 才把止损抬到成本线，之后按 -p2 移动止损
            if peak >= entry_px * (1.0 + p1):
                be_armed = True
            lvl = entry_px if be_armed else entry_px * (1.0 - p2)
            if be_armed:
                lvl = max(entry_px, peak * (1.0 - p2))
        else:
            raise ValueError(kind)

        if kind != "hold" and lj <= lvl:
            return j, min(lvl, oj), j - ei, "rule"
        peak = max(peak, hj)

    return hard, c[hard] * k, hard - ei, ("hold" if hard == ei + max_hold else "eod")


def net_ret(entry_px, exit_px):
    return (exit_px * (1.0 - SELL_COST)) / (entry_px * (1.0 + BUY_COST)) - 1.0


# ---------------------------------------------------------------- 统计工具

def batch(rows, fld):
    """日批均：每开仓日等权，再按日等权。返回 (日数, 日均值%, 日胜率%)。"""
    by = defaultdict(list)
    for r in rows:
        v = r.get(fld)
        if v is not None:
            by[r["d"]].append(v)
    if not by:
        return 0, None, None
    per = np.array([np.mean(v) for v in by.values()], float)
    return len(per), round(float(per.mean()) * 100, 3), round(float((per > 0).mean()) * 100, 1)


def paired_t(rows, fld_a, fld_b):
    """配对差（a-b）按开仓日聚类的 t 检验。

    为什么必须按日聚类：同日几十笔共享同一段行情，独立同分布假设不成立。
    直接把每笔当独立样本，t 值会被放大数倍。

    返回 dict 或 None（无有效笔）。
    """
    by = defaultdict(list)
    for r in rows:
        va, vb = r.get(fld_a), r.get(fld_b)
        if va is None or vb is None:
            continue
        by[r["d"]].append(va - vb)
    if len(by) < 10:
        return None
    per = np.array([np.mean(v) for v in by.values()], float)
    n = len(per)
    mean = float(per.mean())
    sd = float(per.std(ddof=1)) if n > 1 else 0.0
    se = sd / np.sqrt(n) if n > 1 else 0.0
    t = mean / se if se > 0 else 0.0
    # 双侧 p（正态近似；n 通常 >600，足够）
    from math import erfc, sqrt
    p = erfc(abs(t) / sqrt(2.0))
    # 按日 bootstrap：更稳健，也给 95% CI
    rng = np.random.default_rng(SEED)
    boots = np.array([per[rng.integers(0, n, n)].mean() for _ in range(BOOT)], float)
    return {"days": n, "mean_pp": round(mean * 100, 3), "t": round(t, 2),
            "p": round(float(p), 4), "p_boot": round(float((np.abs(boots) >= abs(mean)).mean()), 4),
            "ci95_pp": [round(float(np.percentile(boots, 2.5)) * 100, 3),
                        round(float(np.percentile(boots, 97.5)) * 100, 3)],
            "win_rate": round(float((per > 0).mean()) * 100, 1)}


def by_year(rows, fld):
    out = {}
    for yr in sorted({r["d"][:4] for r in rows}):
        sub = [r for r in rows if r["d"][:4] == yr]
        n, avg, _ = batch(sub, fld)
        out[yr] = {"days": n, "avg": avg}
    return out


# ---------------------------------------------------------------- 主流程

def load_pool(since="20231009"):
    rows = []
    for line in open(POOL, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("type") not in BUY or r.get("status") != "closed":
            continue
        if not (r.get("entry_px") and r.get("price") and r.get("entry_date")):
            continue
        d = r["entry_date"].replace("-", "")[:8]
        if d < since.replace("-", "")[:8]:
            continue
        rows.append({"code": r["code"], "type": r["type"], "d": d,
                     "entry_px": float(r["entry_px"]),
                     "pool_ret": float(r["ret"])})
    return rows


def build_rules():
    """24 个规则。命名带参数，便于邻域检查。"""
    R = []
    for n in (5, 9, 10, 15, 20, 30, 40, 60):
        R.append(("hold_%d" % n, ("hold", 0.0, 0.0, n)))
    for x in (0.04, 0.06, 0.08, 0.10, 0.12, 0.15, 0.20):
        R.append(("trail_%02d" % int(x * 100), ("trail", x, 0.0, 20)))
    for x in (0.04, 0.06, 0.08, 0.10, 0.12):
        R.append(("stop_%02d" % int(x * 100), ("stop", x, 0.0, 20)))
    for s in (0.06, 0.10, 0.15):
        R.append(("trail08_stop%02d" % int(s * 100), ("trail_stop", 0.08, s, 20)))
    for t in (0.05, 0.10):
        R.append(("be%02d_trail08" % int(t * 100), ("breakeven", t, 0.08, 20)))
    # 止损族 + 更长上限，检验"20 日上限"本身是否约束了上限
    for x in (0.08, 0.12):
        R.append(("trail_%02d_h40" % int(x * 100), ("trail", x, 0.0, 40)))
    return R


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="20231009")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    say = (lambda *x, **k: None) if a.json else print

    rows = load_pool(a.since)
    say("池 %d 笔（1买/2买 closed，since %s）" % (len(rows), a.since))

    codes = {r["code"] for r in rows}
    P = load_price_panel(codes, TDX, ohlc=True)
    say("价格面板 %d 只" % len(P))

    # ---- 逐规则模拟 ----
    rules = build_rules()
    keep = []
    for r in rows:
        p = P.get(r["code"])
        if not p:
            continue
        ei = p["pos"].get(r["d"])
        if ei is None:
            continue
        r["ei"] = ei
        r["_p"] = p
        keep.append(r)
    rows = keep
    say("可模拟 %d 笔" % len(rows))

    series = {}
    for name, spec in rules:
        fld = "ret_" + name
        ok = 0
        for r in rows:
            p, ei = r["_p"], r["ei"]
            xi, px, days, why = sim(spec, p["o"], p["h"], p["l"], p["c"], ei, r["entry_px"])
            r[fld] = net_ret(r["entry_px"], px)
            r["days_" + name] = days
            r["why_" + name] = why
            ok += 1
        series[name] = fld
        say("  %-18s 模拟 %d 笔" % (name, ok))

    base = "hold_20"
    bfld = series[base]

    # ---- 表 1：全样本 ----
    say("\n[1] 全样本（已扣双边成本 %.2f%%）" % ((1 - COST_MULT) * 100))
    say("  %-18s %6s %8s %9s %7s %7s %9s"
        % ("规则", "均持", "笔均%", "日批均%", "日胜率", "每持日%", "vs hold20"))
    tab = {}
    for name, fld in series.items():
        rets = np.array([r[fld] for r in rows], float)
        dys = np.array([r["days_" + name] for r in rows], float)
        n, avg, win = batch(rows, fld)
        avg_days = float(dys.mean())
        per_day = (avg / avg_days) if avg is not None and avg_days > 0 else None
        pt = paired_t(rows, fld, bfld) if name != base else None
        tab[name] = {"avg_days": round(avg_days, 2), "pick_avg": round(float(rets.mean()) * 100, 3),
                     "day_avg": avg, "day_win": win, "per_hold_day": round(per_day, 4) if per_day else None,
                     "paired_vs_base": pt, "by_year": by_year(rows, fld)}
        say("  %-18s %6.2f %+8.3f %+9.3f %6.1f%% %+8.4f %s"
            % (name, avg_days, tab[name]["pick_avg"], avg, win,
               per_day or 0.0,
               ("t=%+.2f p=%.3f %+.3fpp" % (pt["t"], pt["p"], pt["mean_pp"])) if pt else "—"))

    # ---- 表 2：暴露归因 ----
    # hold_ret 与 trail_ret 的精确关系：(1+r_hold) = (1+r_trail) × (1+seg)
    # seg = 从 trail 出场日到第 20 日的收益（含成本修正，两边成本相同可约掉）
    say("\n[2] 暴露归因：trail 出场后到第 20 日那段，平均赚还是亏")
    attrib = {}
    # 显式列表：不能用 startswith("trail_") 模糊匹配，否则 trail08_stop06（以数字结尾）
    # 也会进来，名字与参数对不上，读表时容易误判
    for name in ("trail_04", "trail_06", "trail_08", "trail_10",
                 "trail_12", "trail_15", "trail_20"):
        segs = []
        for r in rows:
            why = r["why_" + name]
            if why != "rule":          # 只看出场确实被规则触发的笔
                continue
            p, ei = r["_p"], r["ei"]
            j = ei + r["days_" + name]
            e20 = min(ei + 20, len(p["c"]) - 1)
            if e20 <= j:
                continue
            segs.append(p["c"][e20] / p["c"][j] - 1.0)
        if not segs:
            continue
        segs = np.array(segs, float)
        fired = len(segs)
        attrib[name] = {"fired": fired, "fired_share_pct": round(fired * 100.0 / len(rows), 1),
                        "seg_mean_pct": round(float(segs.mean()) * 100, 3),
                        "seg_median_pct": round(float(np.median(segs)) * 100, 3),
                        "seg_win_pct": round(float((segs > 0).mean()) * 100, 1),
                        "seg_sum_pp": round(float(segs.mean()) * fired / len(rows) * 100, 3)}
        say("  %-18s 触发 %5.1f%% 笔  出场后到20日 均值%+.3f%% 中位%+.3f%% 正收益占%4.1f%%"
            % (name, attrib[name]["fired_share_pct"], attrib[name]["seg_mean_pct"],
               attrib[name]["seg_median_pct"], attrib[name]["seg_win_pct"]))
    say("  读法：均值为正 = 止损砍在半山腰（砍错了）；为负 = 砍对了")
    say("  注：seg_sum_pp 是全样本摊薄后的贡献，与 vs hold20 的差近似对应（两口径成本相同可约）")

    # ---- 表 3：分年 ----
    say("\n[3] 分年日批均（判断是否只在某一年赢）")
    yrs = sorted({r["d"][:4] for r in rows})
    say("  %-18s %s" % ("规则", " ".join("%9s" % y for y in yrs)))
    for name in series:
        vals = tab[name]["by_year"]
        say("  %-18s %s" % (name, " ".join("%+9.3f" % (vals[y]["avg"] or 0.0) for y in yrs)))

    # ---- 表 4：类型交互 ----
    say("\n[4] 1买 / 2买 交互（配对差，按日聚类）")
    type_tab = {}
    for tp in BUY:
        sub = [r for r in rows if r["type"] == tp]
        rec = {}
        for name in ("trail_08", "hold_09", "hold_10", "hold_15", "hold_30", "stop_08"):
            if name not in series:
                continue
            pt = paired_t(sub, series[name], bfld)
            n, avg, _ = batch(sub, series[name])
            rec[name] = {"n": len(sub), "day_avg": avg, "paired": pt}
        type_tab[tp] = rec
        say("  %s（%d 笔）" % (tp, len(sub)))
        for name, v in rec.items():
            pt = v["paired"]
            say("    %-12s 日批均%+8.3f%%  vs hold20 %s"
                % (name, v["day_avg"] or 0.0,
                   ("%+.3fpp t=%+.2f p=%.3f" % (pt["mean_pp"], pt["t"], pt["p"])) if pt else "—"))

    # ---- 表 5：随机出场零模型 ----
    # 出场规则特有的零模型：在同样上限内随机挑一天出场。
    # 这不是为了证明"胜"，而是量化"提前离场"本身的期望代价。
    say("\n[5] 随机出场零模型（在 1..20 内均匀随机挑一天出场，%d 次）" % BOOT)
    rng = np.random.default_rng(SEED)
    null_days = []
    for b in range(BOOT):
        vals = []
        for r in rows:
            p, ei = r["_p"], r["ei"]
            k = int(rng.integers(1, 21))
            j = min(ei + k, len(p["c"]) - 1)
            px = p["c"][j] * (r["entry_px"] / p["c"][ei])
            vals.append(net_ret(r["entry_px"], px))
        tmp = [{"d": r["d"], "v": v} for r, v in zip(rows, vals)]
        n, avg, _ = batch(tmp, "v")
        null_days.append(avg)
    null_days = np.array(null_days, float)
    say("  随机出场日批均：中位 %+.3f%%  5%%~95%% 区间 [%+.3f%%, %+.3f%%]"
        % (float(np.percentile(null_days, 50)), float(np.percentile(null_days, 5)),
           float(np.percentile(null_days, 95))))
    for name in ("hold_20", "hold_9", "trail_08", "stop_08"):
        if name not in series:
            continue
        r_ = tab[name]["day_avg"]
        p_hi = round(float((null_days >= r_).mean()), 4)
        say("  %-12s %+.3f%%  优于随机出场的比例 %.1f%%（>95%% 才算优于随机）"
            % (name, r_, (1 - p_hi) * 100))

    # ---- 判定 ----
    say("\n[6] 判定")
    t8 = tab["trail_08"]; h20 = tab["hold_20"]; h9 = tab["hold_9"]
    gap = (h20["day_avg"] or 0) - (t8["day_avg"] or 0)
    seg = attrib.get("trail_08") or {}
    verdict = {}
    verdict["cost_fix"] = ("原 1.172pp 差距含 0.2pp 成本口径错误（trail 扣了成本、hold 没扣）；"
                           "同口径下差距 %.3fpp" % gap)
    verdict["exposure_attrib"] = (
        "trail_08 触发 %.1f%% 笔，出场后到第 20 日均值 %+.3f%%（正收益占 %.1f%%）→ %s"
        % (seg.get("fired_share_pct", 0), seg.get("seg_mean_pct", 0),
           seg.get("seg_win_pct", 0),
           "止损系统性砍在半山腰" if (seg.get("seg_mean_pct") or 0) > 0 else "止损确实躲开了后续下跌"))
    if h9["day_avg"] is not None and t8["day_avg"] is not None:
        verdict["hold_matched"] = (
            "持有期匹配（hold_9 %.2f 日均持 vs trail_08 %.2f 日）：hold_9 %+.3f%% vs trail_08 %+.3f%%，"
            "差 %+.3fpp → %s"
            % (h9["avg_days"], t8["avg_days"], h9["day_avg"], t8["day_avg"],
               h9["day_avg"] - t8["day_avg"],
               "止损触发本身无价值，差异全来自提前离场"
               if abs(h9["day_avg"] - t8["day_avg"]) < 0.15 else
               "止损的择时触发有实质贡献（非单纯提前离场）"))
    for k in ("cost_fix", "exposure_attrib", "hold_matched"):
        say("  - " + verdict[k])

    json.dump({"meta": {"pool": os.path.basename(POOL), "since": a.since,
                        "n": len(rows), "cost_pct": round((1 - COST_MULT) * 100, 4),
                        "base": base, "seed": SEED, "boot": BOOT},
               "table": tab, "exposure_attrib": attrib, "type_interaction": type_tab,
               "random_exit_null": {"p50": round(float(np.percentile(null_days, 50)), 3),
                                    "p5": round(float(np.percentile(null_days, 5)), 3),
                                    "p95": round(float(np.percentile(null_days, 95)), 3)},
               "verdict": verdict},
              open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("\n落盘 %s" % a.out)


if __name__ == "__main__":
    main()
