# -*- coding: utf-8 -*-
"""成本灭损线：日批均 0.629%/笔，扣掉各项成本后还剩多少（K-RES-20261108-COST）。

为什么要单独做这一节
--------------------
`combo_shadow.null_test` 报的 `margin_pp = real - p95`，单位是"百分点/笔"，
2026-11-08 现值 0.0282pp。**这个数不是钱**，它是"相对随机抽同样天数"的差值。
成本对实验组（sigdens P30）和对照组（随机抽日）是同一笔、同一个费率，
一阶上两边同减，所以：

    成本 ↑ ⟹ real ↓ 且 p95 ↓ 同量 ⟹ margin 不变、p 值不变

这是恒等式，不是近似（L3 用实证验证）。所以"余量扣掉成本还剩多少"这个问法，
在随机对照框架里是错位的——余量永远不会被成本吃掉。**被吃掉的是绝对日批均**
（0.629%/笔）。本脚本算的是后一笔账：真实成本结构下赚不赚钱、成本到多少归零。

四层口径
--------
L1 结构式成本情景：佣金/印花税/过户费/半价差/冲击分项建模，逐档重算日批均。
L2 成本 sweep：往返 0~200bp，求保本成本（日批均归零点）。
L3 p 值恒等式验证：换成本重跑随机对照，p 必须逐位相同。
L4 执行滞后：`entry_px` 是**检出日收盘**（chanlun_scan.py:1886），实盘收盘后
   才知道信号，只能次日开盘买。测 `次日开盘/当日收盘-1` —— 现有成本模型完全
   没含这一项，而且它是单边不可逆的。

未建模（报告里列为边界）
------------------------
- 涨跌停不可卖：止损触发日一字跌停时卖不掉（方向单边为负）
- 停牌跨期按前收盘成交
- 最低佣金 5 元对小单的抬高（<2 万/笔 时显著）

用法
----
    python tools/research/cost_breakeven.py
    python tools/research/cost_breakeven.py --json
"""
import argparse
import json
import os
import sys
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

import combo_shadow as CS  # noqa: E402
from portfolio_mtm import (BUY_COST, SELL_COST, Engine, Pick, build_ratio,  # noqa: E402
                           load_price_panel)

OUT = os.path.join(ROOT, "portal", "combo_cost_breakeven.json")

# ---------------------------------------------------------------------------
# 成本结构常数（每个都要能说清出处，改数前先改注释）
# ---------------------------------------------------------------------------
STAMP = 0.0005        # 印花税：卖出单边。2023-08-28 起由 0.1% 减半至 0.05%
TRANSFER = 0.00001    # 过户费：双边 0.001%（2022-04-29 沪深统一下调）
# 半价差 = 买卖各吃一半买卖价差；冲击 = 自己的单子推动价格。
SCEN = {
    # label:            佣金(单边)  半价差(单边) 冲击(单边)   适用画像
    "S1 乐观": dict(comm=0.00015, spread=0.0002, impact=0.0000,
                    who="万1.5 佣金 + 高流动性大盘 + 单笔金额可忽略"),
    "S2 中性": dict(comm=0.00025, spread=0.0005, impact=0.0002,
                    who="万2.5 佣金 + 中盘 + 单笔 <0.1% 日成交额"),
    "S3 保守": dict(comm=0.00025, spread=0.0010, impact=0.0010,
                    who="万2.5 + 中小盘 + 单笔 0.3~1% 日成交额"),
    "S4 悲观": dict(comm=0.00030, spread=0.0020, impact=0.0030,
                    who="万3 + 微盘/低流动性 + 单笔 >2% 日成交额"),
}
SWEEP_BP = (0, 10, 20, 30, 40, 50, 60, 80, 100, 120, 150, 200)


def legs(sc):
    """→ (买边, 卖边, 往返, 分项明细)，单位小数。印花税只挂卖边。"""
    buy = sc["comm"] + TRANSFER + sc["spread"] + sc["impact"]
    sell = buy + STAMP
    detail = {"comm": 2 * sc["comm"], "stamp": STAMP, "transfer": 2 * TRANSFER,
              "spread": 2 * sc["spread"], "impact": 2 * sc["impact"]}
    return buy, sell, buy + sell, detail


def gross(net):
    """净收益 → 成本前毛净值比。逆用净口径 (1+net)=(exit/entry)*(1-sell)/(1+buy)。"""
    return (1.0 + net) * (1.0 + BUY_COST) / (1.0 - SELL_COST)


def net_at(g, buy, sell):
    return g * (1.0 - sell) / (1.0 + buy) - 1.0


def _by_day(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["entry_date"]].append(r)
    return by


def batch_stats(rows, field):
    by = _by_day(rows)
    per_day = np.array([np.mean([x[field] for x in v]) for v in by.values()], float)
    if not len(per_day):
        return None
    return {"days": int(len(per_day)),
            "avg": round(float(per_day.mean()) * 100, 3),
            "med": round(float(np.median(per_day)) * 100, 3),
            "win": round(float((per_day > 0).mean()) * 100, 1),
            "std": round(float(per_day.std(ddof=1)) * 100, 2) if len(per_day) > 1 else None}


def pierce_stats(rows, field):
    v = np.array([r[field] for r in rows], float)
    return {"n": int(len(v)), "avg": round(float(v.mean()) * 100, 3),
            "med": round(float(np.median(v)) * 100, 3),
            "win": round(float((v > 0).mean()) * 100, 1)}


def with_cost(rows, field, buy, sell):
    return [dict(r, **{field: net_at(gross(r[field]), buy, sell)}) for r in rows]


def breakeven_bp(G):
    """日批均归零的往返成本。G 是**毛净值比**（已含 1，不是收益率）。

    设买边 b、卖边 s=b+STAMP，要求 (1-s)/(1+b) = 1/G：
        G(1-b-STAMP) = 1+b  →  b = (G - 1 - G*STAMP)/(1+G)
    """
    b = (G - 1.0 - G * STAMP) / (1.0 + G)
    s = b + STAMP
    return b, s, b + s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    say = (lambda *x, **k: None) if a.json else print

    recs = [json.loads(x) for x in open(CS.SHADOW, encoding="utf-8") if x.strip()]
    settled = [r for r in recs if r.get("mature")]
    if not settled:
        say("清单里没有已到期的笔：%s" % CS.SHADOW)
        return 2
    passed = [r for r in settled if r.get("pass")]
    say("清单 %d 笔 / 已到期 %d / 通过阈值 %d，窗口 %s ~ %s"
        % (len(recs), len(settled), len(passed),
           min(r["entry_date"] for r in settled), max(r["entry_date"] for r in settled)))

    # ---- 0. 恒等式自检：反解再按原成本正算，必须逐位回到原值 ----
    worst = max(abs(net_at(gross(r["trail_ret"]), BUY_COST, SELL_COST) - r["trail_ret"])
                for r in settled)
    if worst > 1e-9:
        say("[FATAL] 反解/正算不闭合 max|diff|=%.3e —— 后续成本档全部不可信" % worst)
        return 3
    say("恒等式自检：20bp 反解后回算 max|diff|=%.1e（OK）" % worst)

    base_t = batch_stats(passed, "trail_ret")
    base_h = batch_stats(passed, "hold_ret")
    say("基准（已扣 20bp）：日批均 trail %+0.3f%%（%d 个开仓日）/ hold %+0.3f%%"
        % (base_t["avg"], base_t["days"], base_h["avg"]))

    # ---- L1 结构式情景 ----
    say("\n[L1] 结构式成本情景")
    say("  %-9s %7s %7s %7s %7s %7s %7s   %s"
        % ("情景", "往返bp", "佣金", "印花", "过户", "价差", "冲击", "日批均 / 较基准"))
    scen_out = {}
    for lab, sc in SCEN.items():
        buy, sell, tot, det = legs(sc)
        st = batch_stats(with_cost(passed, "trail_ret", buy, sell), "trail_ret")
        sth = batch_stats(with_cost(passed, "hold_ret", buy, sell), "hold_ret")
        pi = pierce_stats(with_cost(passed, "trail_ret", buy, sell), "trail_ret")
        scen_out[lab] = {"buy": round(buy, 6), "sell": round(sell, 6),
                         "roundtrip_bp": round(tot * 1e4, 1),
                         "detail_bp": {k: round(v * 1e4, 2) for k, v in det.items()},
                         "who": sc["who"], "batch_trail": st, "batch_hold": sth,
                         "pierce_trail": pi,
                         "delta_vs_base_pp": round(st["avg"] - base_t["avg"], 3)}
        say("  %-9s %7.1f %7.1f %7.1f %7.1f %7.1f %7.1f   %+7.3f%%（%+0.3fpp）"
            % (lab, tot * 1e4, det["comm"] * 1e4, det["stamp"] * 1e4,
               det["transfer"] * 1e4, det["spread"] * 1e4, det["impact"] * 1e4,
               st["avg"], st["avg"] - base_t["avg"]))

    # ---- L2 sweep + 保本 ----
    say("\n[L2] 往返成本 sweep（印花税固定挂卖边，其余对半）")
    sweep = []
    for bp in SWEEP_BP:
        tot = bp / 1e4
        sell = (tot + STAMP) / 2.0
        buy = tot - sell
        if buy < 0:                      # 成本低于印花税一半时，卖边吃掉全部
            buy, sell = 0.0, tot
        st = batch_stats(with_cost(passed, "trail_ret", buy, sell), "trail_ret")
        pi = pierce_stats(with_cost(passed, "trail_ret", buy, sell), "trail_ret")
        sweep.append({"roundtrip_bp": bp, "batch_avg": st["avg"], "batch_med": st["med"],
                      "batch_win": st["win"], "pierce_avg": pi["avg"],
                      "pierce_win": pi["win"]})
        say("  %4dbp  日批均 %+7.3f%%  批中位 %+7.3f%%  日胜率 %4.1f%%  笔均 %+7.3f%%"
            % (bp, st["avg"], st["med"], st["win"], pi["avg"]))

    by = _by_day(passed)
    g_day = float(np.mean([np.mean([gross(x["trail_ret"]) for x in v])
                           for v in by.values()]))
    be_tot_b, be_tot_s, be_tot = breakeven_bp(g_day)
    be = {"gross_batch_avg_pct": round((g_day - 1) * 100, 3),
          "net_batch_avg_at_20bp_pct": base_t["avg"],
          "breakeven_roundtrip_bp": round(be_tot * 1e4, 1),
          "breakeven_buy_bp": round(be_tot_b * 1e4, 1),
          "breakeven_sell_bp": round(be_tot_s * 1e4, 1),
          "headroom_vs_20bp_bp": round(be_tot * 1e4 - 20, 1)}
    say("\n  毛日批均 %+0.3f%%（成本前）→ 20bp 下净 %+0.3f%%"
        % (be["gross_batch_avg_pct"], base_t["avg"]))
    say("  保本往返 %0.1fbp（买 %0.1f / 卖 %0.1f），距现行 20bp 还差 %0.1fbp"
        % (be["breakeven_roundtrip_bp"], be["breakeven_buy_bp"],
           be["breakeven_sell_bp"], be["headroom_vs_20bp_bp"]))

    # ---- L2b 日批均 vs 笔均：正期望靠几天撑起来 ----
    # 日批均给每天相同权重，笔均给每笔相同权重。两者在 20bp 下是 +0.629% vs
    # -1.492%，差 2.1pp —— 只可能是"信号越多的日子越不赚钱"。若不拆开看，
    # 0.629% 会被误读成"每笔赚 0.6%"，而实际每笔是亏的，靠少数几天的极端值翻正。
    say("\n[L2b] 日批均与笔均的背离（正期望的集中度）")
    days = []
    for d, v in _by_day(passed).items():
        rets = [x["trail_ret"] for x in v]
        days.append((d, len(rets), float(np.mean(rets))))
    days.sort(key=lambda x: -x[2])
    cnts = np.array([x[1] for x in days], float)
    means = np.array([x[2] for x in days], float)
    day_avg = float(means.mean())
    pierce = float(np.mean([r["trail_ret"] for r in passed]))
    rho = float(np.corrcoef(np.argsort(np.argsort(cnts)),
                            np.argsort(np.argsort(means)))[0, 1])
    top5 = float(np.mean(means[:5]))
    rest = float(np.mean(means[5:]))
    # 拿去最好的 k 天后的日批均
    trim = {}
    for k in (1, 3, 5, 10):
        trim[k] = round(float(np.mean(means[k:])) * 100, 3)
    say("  每日笔数 min/中位/max = %d/%d/%d；日笔数与日收益 Spearman %+0.2f"
        % (cnts.min(), np.median(cnts), cnts.max(), rho))
    say("  日批均 %+0.3f%% vs 笔均 %+0.3f%%（差 %+0.3fpp，因信号多的日子笔均更差）"
        % (day_avg * 100, pierce * 100, (day_avg - pierce) * 100))
    say("  最好 5 天日批均 %+0.3f%%，其余 %d 天 %+0.3f%%" % (top5 * 100, len(days) - 5, rest * 100))
    say("  去掉最好的 1/3/5/10 天后：%+0.3f%% / %+0.3f%% / %+0.3f%% / %+0.3f%%"
        % (trim[1], trim[3], trim[5], trim[10]))
    say("  最好 5 天合计 %s" % ("、".join("%s(%+0.2f%%,%d笔)" % (d, m * 100, c)
                                          for d, c, m in days[:5])))
    conc = {"n_days": len(days), "cnt_min": int(cnts.min()),
            "cnt_med": float(np.median(cnts)), "cnt_max": int(cnts.max()),
            "spearman_cnt_vs_ret": round(rho, 3),
            "day_avg_pct": round(day_avg * 100, 3),
            "pierce_avg_pct": round(pierce * 100, 3),
            "spread_pp": round((day_avg - pierce) * 100, 3),
            "top5_avg_pct": round(top5 * 100, 3),
            "rest_avg_pct": round(rest * 100, 3),
            "trimmed": trim,
            "top_days": [{"d": d, "n": c, "avg": round(m * 100, 3)}
                         for d, c, m in days[:5]]}

    # ---- L2c 分层诊断：0.629% 到底由谁贡献 ----
    # L2b 已经指出日批均 vs 笔均差 2.1pp。这一节把它拆到能落地：
    # 按当日笔数分档、按月、以及剔除早期管线残缺期。
    # 关键背景：台账日信号数是 0.1→0.3→1.0→2.9→12.7→36.5 单调爬升（见
    # combo_shadow.load_ext_counts 注释），这是**扫描管线成熟度**不是市场情绪。
    # 于是"1 笔日"= 管线只产出 1 笔（方差极大），"36 笔日"= 管线成熟（方差小）。
    # 日等权把两者按 1:1 加权，等于让残缺期的几笔运气拿到 1/75 权重。
    say("\n[L2c] 分层诊断（20bp 基准）")
    day_rows = [(d, v) for d, v in _by_day(passed).items()]

    def layer(rows_sel, lab):
        if not rows_sel:
            return None
        ds = defaultdict(list)
        for r in rows_sel:
            ds[r["entry_date"]].append(r["trail_ret"])
        m = np.array([np.mean(v) for v in ds.values()], float)
        pr = np.array([r["trail_ret"] for r in rows_sel], float)
        return {"label": lab, "days": len(m), "pierce": len(pr),
                "day_avg": round(float(m.mean()) * 100, 3),
                "day_med": round(float(np.median(m)) * 100, 3),
                "day_win": round(float((m > 0).mean()) * 100, 1),
                "pierce_avg": round(float(pr.mean()) * 100, 3),
                "pierce_med": round(float(np.median(pr)) * 100, 3),
                "pierce_win": round(float((pr > 0).mean()) * 100, 1)}

    layers = []
    layers.append(layer(passed, "全窗口"))
    top_days = {x["d"] for x in conc["top_days"]}
    layers.append(layer([r for r in passed if r["entry_date"] not in top_days],
                        "剔除最好5天"))
    layers.append(layer([r for r in passed if r["entry_date"][:6] != "202604"],
                        "剔除2026-04"))
    layers.append(layer([r for r in passed if r["entry_date"] >= "20260801"],
                        "管线成熟期≥202608"))
    for lo, hi, lab in ((1, 2, "日笔数1~2"), (3, 9, "日笔数3~9"),
                        (10, 30, "日笔数10~30"), (31, 10 ** 6, "日笔数≥31")):
        sel_days = {d for d, v in day_rows if lo <= len(v) <= hi}
        layers.append(layer([r for r in passed if r["entry_date"] in sel_days], lab))
    say("  %-18s %4s %5s | %9s %9s %7s | %9s %9s %7s"
        % ("分层", "天", "笔", "日批均", "日中位", "日胜率", "笔均", "笔中位", "笔胜率"))
    for L in layers:
        if not L:
            continue
        say("  %-18s %4d %5d | %+9.3f%% %+9.3f%% %6.1f%% | %+9.3f%% %+9.3f%% %6.1f%%"
            % (L["label"], L["days"], L["pierce"], L["day_avg"], L["day_med"],
               L["day_win"], L["pierce_avg"], L["pierce_med"], L["pierce_win"]))

    # 笔加权随机对照：同一批笔，换权重口径再检验一次。
    # null_test 用的是「日等权、随机抽同样天数」，且 universe 只有台账有信号的
    # 99 天、pass 占 75 天 —— 两组期望重叠 57 天（76%），检验的其实只是不重叠
    # 那十几天的差异，功效极低。笔加权是决策真正关心的口径（每笔一样的钱）。
    rng = np.random.default_rng(CS.SEED)
    pool = np.array([r["trail_ret"] for r in settled], float)
    real_pierce = float(np.mean([r["trail_ret"] for r in passed]))
    draws = np.array([pool[rng.choice(len(pool), len(passed), replace=False)].mean()
                      for _ in range(CS.BOOT_NULL)], float)
    p_pierce = float((draws >= real_pierce).mean())
    pierce_null = {"real": round(real_pierce * 100, 3),
                   "null_p50": round(float(np.percentile(draws, 50)) * 100, 3),
                   "null_p95": round(float(np.percentile(draws, 95)) * 100, 3),
                   "p_value": round(p_pierce, 4),
                   "n_pass": len(passed), "n_pool": len(pool),
                   "verdict": ("优于随机笔" if real_pierce > np.percentile(draws, 95)
                               else "不优于随机抽笔")}
    say("\n  笔加权对照（从全部 %d 笔里随机抽 %d 笔）：real %+0.3f%%，随机中位 %+0.3f%%，"
        "p95 %+0.3f%%，p=%.4f → %s"
        % (len(pool), len(passed), pierce_null["real"], pierce_null["null_p50"],
           pierce_null["null_p95"], p_pierce, pierce_null["verdict"]))
    n_uni = len({r["entry_date"] for r in settled})
    ov = len(day_rows) ** 2 / float(n_uni) if n_uni else 0.0
    say("  [注] 日等权对照的 universe 仅 %d 天、pass %d 天，期望重叠 %.0f 天（%.0f%%），"
        "p 值实际只由不重叠的十余天决定" % (n_uni, len(day_rows), ov, ov / max(1, len(day_rows)) * 100))

    # ---- L3 p 值恒等式 ----
    say("\n[L3] p 值随成本变化（必须逐位相同，否则恒等式推导有错）")
    pchk = {}
    for bp in (20, 60, 120):
        tot = bp / 1e4
        sell = (tot + STAMP) / 2.0
        buy = tot - sell
        if buy < 0:
            buy, sell = 0.0, tot
        # 必须喂全样本（含未通过的日子），null_test 靠 pass 字段取子集
        o = CS.null_test(with_cost(settled, "trail_ret", buy, sell), "trail")
        pchk[bp] = {"real": o["real"], "p95": o["null_p95"],
                    "p_value": o["p_value"], "margin_pp": o["margin_pp"]}
        say("  %4dbp  real %+7.3f%%  p95 %+7.3f%%  p=%.4f  margin %+0.4fpp"
            % (bp, o["real"], o["null_p95"], o["p_value"], o["margin_pp"]))
    same_p = len({v["p_value"] for v in pchk.values()}) == 1
    say("  → p 值 %s" % ("三档逐位一致（成本对实验组/对照组同减，恒等）" if same_p
                         else "**不一致 —— 恒等式推导有误，必须回查**"))
    if not same_p:
        # 这不是可选项：p 值若随成本变化，说明成本对实验组/对照组不是同量削减，
        # §1 全部成本情景与 §3 的结论同时失效，继续算只会产出看似精密的错数。
        say("[FATAL] p 值未逐位相同，成本情景框架失效，终止（不落盘）")
        return 4

    # ---- L4 执行滞后 ----
    say("\n[L4] 执行滞后：entry_px 是检出日收盘，实盘只能次日开盘买")
    P = load_price_panel({r["code"] for r in passed}, CS.TDX, start="20240101", ohlc=True)
    gaps, lagged = [], []
    n_skip = 0
    for r in passed:
        p = P.get(r["code"])
        ei = p["pos"].get(r["entry_date"]) if p else None
        if ei is None or ei + 1 >= len(p["c"]):
            n_skip += 1
            continue
        g = p["o"][ei + 1] / p["c"][ei] - 1.0     # 复权口径，k 归一后等于 次开/entry_px-1
        if not np.isfinite(g):
            n_skip += 1
            continue
        gaps.append(g)
        lagged.append(dict(r, trail_ret=(1.0 + r["trail_ret"]) / (1.0 + g) - 1.0,
                           hold_ret=(1.0 + r["hold_ret"]) / (1.0 + g) - 1.0))
    gaps = np.array(gaps, float)
    lag = {"n": int(len(gaps)), "n_skip": n_skip,
           "gap_mean_bp": round(float(gaps.mean()) * 1e4, 2),
           "gap_med_bp": round(float(np.median(gaps)) * 1e4, 2),
           "gap_p25_bp": round(float(np.percentile(gaps, 25)) * 1e4, 2),
           "gap_p75_bp": round(float(np.percentile(gaps, 75)) * 1e4, 2),
           "gap_win_pct": round(float((gaps > 0).mean()) * 100, 1),
           "t_stat": round(float(gaps.mean() / (gaps.std(ddof=1) / np.sqrt(len(gaps)))), 2)
           if len(gaps) > 2 else None}
    if len(gaps):
        st = batch_stats(lagged, "trail_ret")
        lag.update({"batch_avg_after_lag": st["avg"], "batch_med_after_lag": st["med"],
                    "delta_vs_base_pp": round(st["avg"] - base_t["avg"], 3)})
        say("  次日开盘/当日收盘-1：均值 %+0.2fbp 中位 %+0.2fbp P25/P75 %+0.0f/%+0.0fbp"
            % (lag["gap_mean_bp"], lag["gap_med_bp"], lag["gap_p25_bp"], lag["gap_p75_bp"]))
        say("  跳空为正的占 %0.1f%%，t=%0.2f（n=%d，跳过 %d）"
            % (lag["gap_win_pct"], lag["t_stat"], len(gaps), n_skip))
        say("  换次日开盘建仓：日批均 %+0.3f%%（较基准 %+0.3fpp）"
            % (st["avg"], lag["delta_vs_base_pp"]))

    # ---- L5 账户层 ----
    say("\n[L5] 账户层（仿射变换净值比后重跑 Engine，默认 1/20 预算）")
    axis_all = CS.load_axis()
    d_lo = min(r["entry_date"] for r in settled)
    # 日历必须铺到数据末端：出场日可能晚于最后一个入口日（20 日持有会跨过去），
    # 截在 d_hi 会让 build_ratio 在 p["d"][xi] 上 KeyError（实测 20261012）。
    last_bar = axis_all[-1]
    axis = [d for d in axis_all if d_lo <= d <= last_bar]
    ap_ = {d: i for i, d in enumerate(axis)}
    base_picks = _to_picks(passed, P, axis, ap_, BUY_COST, SELL_COST)
    views = {}
    for lab, (buy, sell) in (("基准20bp", (BUY_COST, SELL_COST)),
                             ("S1 乐观", legs(SCEN["S1 乐观"])[:2]),
                             ("S2 中性", legs(SCEN["S2 中性"])[:2]),
                             ("S3 保守", legs(SCEN["S3 保守"])[:2]),
                             ("S4 悲观", legs(SCEN["S4 悲观"])[:2])):
        pk = _shift_picks(base_picks, BUY_COST, SELL_COST, buy, sell)
        v = Engine(axis, hold=CS.HOLD).run(pk)
        views[lab] = {k: v[k] for k in ("total", "cagr", "mdd", "sharpe", "vol", "util")}
        views[lab].update({"buy": round(buy, 6), "sell": round(sell, 6),
                           "roundtrip_bp": round((buy + sell) * 1e4, 1)})
        say("  %-9s 往返%6.1fbp 累计%+7.2f%% cagr%+7.2f%% 回撤%7.2f%% 夏普%6.2f util%5.1f%%"
            % (lab, (buy + sell) * 1e4, v["total"], v["cagr"], v["mdd"],
               v["sharpe"], v["util"]))

    # ---- L5b 账户层子样本：净值是不是也被同几天撑起来 ----
    say("\n[L5b] 账户层子样本（20bp，看净值对少数几天的依赖）")
    subs = [("全窗口", passed),
            ("剔除最好5天", [r for r in passed if r["entry_date"] not in top_days]),
            ("剔除2026-04", [r for r in passed if r["entry_date"][:6] != "202604"]),
            ("管线成熟期≥202608", [r for r in passed if r["entry_date"] >= "20260801"])]
    sub_out = {}
    for lab, rows_s in subs:
        if not rows_s:
            continue
        pk = _to_picks(rows_s, P, axis, ap_, BUY_COST, SELL_COST)
        v = Engine(axis, hold=CS.HOLD).run(pk)
        sub_out[lab] = {k: v[k] for k in ("total", "cagr", "mdd", "sharpe", "vol", "util")}
        sub_out[lab]["n_pierce"] = len(rows_s)
        say("  %-18s 笔%4d 累计%+7.2f%% cagr%+7.2f%% 回撤%7.2f%% 夏普%6.2f util%5.1f%%"
            % (lab, len(rows_s), v["total"], v["cagr"], v["mdd"], v["sharpe"], v["util"]))

    out = {"meta": {"asof": max(r["entry_date"] for r in settled),
                    "n_settled": len(settled), "n_pass": len(passed),
                    "n_days_pass": base_t["days"],
                    "base_buy": BUY_COST, "base_sell": SELL_COST,
                    "stamp": STAMP, "transfer": TRANSFER},
           "baseline": {"batch_trail": base_t, "batch_hold": base_h,
                        "pierce_trail": pierce_stats(passed, "trail_ret")},
           "scenarios": scen_out, "sweep": sweep, "breakeven": be,
           "concentration": conc, "layers": layers, "pierce_null": pierce_null,
           "p_insensitivity": pchk, "p_identical": bool(same_p),
           "exec_lag": lag, "account_layer": views, "account_subsample": sub_out,
           "unmodeled": ["涨跌停不可卖（止损日一字板顺延）",
                         "停牌跨期按前收盘成交",
                         "最低佣金 5 元对小单的抬高"]}
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("\n落盘 %s" % a.out)
    return 0


def _to_picks(rows, P, axis, ap_, buy, sell):
    by = defaultdict(list)
    for r in rows:
        p = P.get(r["code"])
        if not p:
            continue
        xi = r["xt"]
        exit_px = ((1.0 + r["trail_ret"]) * r["entry_px"]
                   * (1 + CS.BUY_COST) / (1 - CS.SELL_COST))
        ratio = build_ratio(p, r["ei"], xi, r["entry_px"], exit_px, axis, ap_,
                            buy_cost=buy, sell_cost=sell)
        by[r["entry_date"]].append(Pick(r["code"], ap_[r["entry_date"]],
                                        ap_[p["d"][xi]], ratio, r["trail_ret"]))
    return by


def _shift_picks(picks, b0, s0, b1, s1):
    """把已按 (b0,s0) 扣过成本的净值比，仿射变换到 (b1,s1)。

    (1+r1) = (1+r0) * (1-s1)(1+b0) / ((1+b1)(1-s0))
    与 build_ratio 的定义逐项对应，不需要重算路径。
    """
    f = (1.0 - s1) * (1.0 + b0) / ((1.0 + b1) * (1.0 - s0))
    return {d: [Pick(x.code, x.entry_i, x.exit_i,
                     {k: (1.0 + v) * f - 1.0 for k, v in x.ratio.items()},
                     (1.0 + x.ret) * f - 1.0) for x in xs]
            for d, xs in picks.items()}


if __name__ == "__main__":
    sys.exit(main() or 0)
