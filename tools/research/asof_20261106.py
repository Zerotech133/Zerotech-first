# -*- coding: utf-8 -*-
"""截至 2026-11-06 收盘的组合口径盘点：绝对收益 vs 超额 vs alpha（K-RES-20261108-ASOF）。

为什么必须做这一轮
------------------
`cost_breakeven.py`（commit 66e6e77f4）只看**绝对收益**，得出"正期望由 5 天 7 笔
撑起"。那个结论在绝对口径下没错，但漏了一个决定性事实：样本期基准（等权全A）
累计是**负**的，个股 beta≈0.94，绝对收益被 beta 压住，alpha 被掩盖。

三个口径必须分开报：

  绝对 = trail_ret                        真实多头账户拿到的东西
  超额 = trail_ret - idx_ret              相对等权全A，假设 beta=1
  alpha = 绝对 - beta × idx_ret           beta 用台账逐笔估计（chanlun_alpha.annotate）

绝对为负 + alpha 为正，说明"策略在选股上有信息，但方向暴露吃掉了它"。
这两句对产线的含义完全相反：前者说别做多头，后者说对冲之后可能能做。

数据来源
--------
- `quant_backtest/data/combo_shadow.jsonl`：asof=20261106 的清单（1,716 笔）
- `quant_backtest/data/chanlun_paper.json` 的 `closed`：唯一带 beta/alpha 的地方
  （`open` 1690 笔只有 pnl，无 beta/idx_ret，所以未到期笔只能贡献绝对口径）

统计口径上两个必须守的规矩
--------------------------
1. **按日聚类**。同一天几十笔共享同一个市场冲击，笔级标准误会把 t 值放大 3~5 倍。
   一律先算"每日等权均值"，再对日序列做 t。
2. **剔除 topN 天**。样本只有 82 个交易日，任何均值都要报告"去掉最好的 N 天还剩多少"。

用法
----
    python tools/research/asof_20261106.py
    python tools/research/asof_20261106.py --json
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
from portfolio_mtm import BUY_COST, SELL_COST, Engine, Pick, build_ratio, load_price_panel  # noqa: E402

OUT = os.path.join(ROOT, "portal", "asof_20261106.json")
PAPER = os.path.join(ROOT, "quant_backtest", "data", "chanlun_paper.json")
# 对冲端成本：IC/IM 股指期货年化贴水。2023-2026 中证500 常年 6%~12%，
# 极端时段（2024-01）到过 25%。这里按年化贴水折成"每笔持有期"成本。
HEDGE_ANNUAL = (0.00, 0.06, 0.10, 0.15)
BOOT = 5000


def _days(rows, field):
    by = defaultdict(list)
    for r in rows:
        by[r["entry_date"]].append(r[field])
    return np.array([np.mean(v) for _, v in sorted(by.items())], float)


def day_t(x):
    """按日聚类的 t 值。x 是每日等权均值序列。"""
    x = np.asarray(x, float)
    if len(x) < 3:
        return None
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))


def desc(x):
    x = np.asarray(x, float)
    return {"n_days": int(len(x)), "mean": round(float(x.mean()) * 100, 3),
            "med": round(float(np.median(x)) * 100, 3),
            "win": round(float((x > 0).mean()) * 100, 1),
            "std": round(float(x.std(ddof=1)) * 100, 3) if len(x) > 1 else None,
            "t": round(day_t(x), 2) if day_t(x) is not None else None}


def trim_stats(x, ks=(1, 3, 5, 10)):
    o = np.sort(np.asarray(x, float))[::-1]
    out = {}
    for k in ks:
        if len(o) > k + 2:
            out["top%d" % k] = desc(o[k:])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    say = (lambda *x, **k: None) if a.json else print

    recs = [json.loads(x) for x in open(CS.SHADOW, encoding="utf-8") if x.strip()]
    passed = [r for r in recs if r.get("pass")]
    settled = [r for r in passed if r.get("mature")]
    pending = [r for r in passed if not r.get("mature")]
    asof = max(r["entry_date"] for r in recs)
    say("清单 %d 笔（asof %s）｜pass %d = 已到期 %d + 在途 %d"
        % (len(recs), asof, len(passed), len(settled), len(pending)))

    # 台账 beta/alpha 只存在于 closed（open 无 beta/idx_ret）
    pj = json.load(open(PAPER, encoding="utf-8"))
    meta = {}
    for x in (pj.get("closed") or []):
        k = (str(x.get("entry_date") or "")[:10].replace("-", ""), x.get("code"), x.get("type"))
        if x.get("beta") is not None:
            meta[k] = x
    for r in passed:
        x = meta.get((r["entry_date"], r["code"], r["type"]))
        if x:
            r["beta"] = float(x["beta"])
            r["alpha_l"] = float(x["alpha"])
    alpha_ok = [r for r in passed if r.get("beta") is not None
                and r.get("idx_ret") is not None]
    say("可算 alpha 的笔 %d（其余 %d 笔在台账 open 里没有 beta/idx_ret）"
        % (len(alpha_ok), len(passed) - len(alpha_ok)))

    abs_d = _days(settled, "trail_ret")
    say("\n[1] 绝对口径（已到期 %d 笔 / %d 天）" % (len(settled), len(abs_d)))
    say("  日等权 均值 %+0.3f%% 中位 %+0.3f%% 胜率 %0.1f%% t=%0.2f"
        % (abs_d.mean() * 100, np.median(abs_d) * 100,
           (abs_d > 0).mean() * 100, day_t(abs_d)))

    say("\n[2] 三口径并排（可算 alpha 的 %d 笔）" % len(alpha_ok))
    be = np.array([r["beta"] for r in alpha_ok], float)
    say("  beta 均值 %.3f（中位 %.3f，P5/P95 %.2f/%.2f）"
        % (be.mean(), np.median(be), np.percentile(be, 5), np.percentile(be, 95)))
    day_abs_a = _days(alpha_ok, "trail_ret")
    day_bm = _days(alpha_ok, "idx_ret")
    by_al = defaultdict(list)
    by_alh = defaultdict(list)
    for r in alpha_ok:
        by_al[r["entry_date"]].append(r["trail_ret"] - r["beta"] * r["idx_ret"])
        by_alh[r["entry_date"]].append(r["hold_ret"] - r["beta"] * r["idx_ret"])
    day_al = np.array([np.mean(v) for _, v in sorted(by_al.items())], float)
    day_alh = np.array([np.mean(v) for _, v in sorted(by_alh.items())], float)
    say("  %-8s %10s %10s %10s %10s %10s"
        % ("口径", "日均值", "日中位", "日胜率", "t", "累计(日等权×天数)"))
    for lab, x in (("绝对", day_abs_a), ("基准", day_bm), ("超额", day_abs_a - day_bm),
                   ("alpha", day_al)):
        say("  %-8s %+9.3f%% %+9.3f%% %9.1f%% %10.2f %+9.1f%%"
            % (lab, x.mean() * 100, np.median(x) * 100, (x > 0).mean() * 100,
               day_t(x) or 0, x.sum() * 100))
    # 关键拆分：alpha 是入场信号带来的，还是止损规则带来的？
    # 同一批笔只换出场规则。若 hold 口径 alpha 不显著，说明信号本身没有选股能力，
    # 所谓的 alpha 只是止损把左尾截断产生的凸性——那是"买保险"，不是 alpha。
    say("  --- 同上，但换成持满 20 日（不止损），用来分离入场与出场 ---")
    say("  %-8s %+9.3f%% %+9.3f%% %9.1f%% %10.2f %+9.1f%%"
        % ("alpha_h", day_alh.mean() * 100, np.median(day_alh) * 100,
           (day_alh > 0).mean() * 100, day_t(day_alh) or 0, day_alh.sum() * 100))
    say("  止损贡献 %+0.3fpp/日（trail alpha − hold alpha）"
        % ((day_al.mean() - day_alh.mean()) * 100))
    # 自检：两个 alpha 相减时 β×基准项必须抵消，残差只剩出场规则之差。
    # 若不闭合，说明 beta 或 idx_ret 在两条路径上用了不同值，后面的拆分全部作废。
    day_h = _days(alpha_ok, "hold_ret")
    lhs = day_al.mean() - day_alh.mean()
    rhs = day_abs_a.mean() - day_h.mean()
    if abs(lhs - rhs) > 1e-9:
        say("[FATAL] 止损贡献不闭合：LHS=%.6e RHS=%.6e（β×基准未抵消）" % (lhs, rhs))
        return 3
    say("  自检：止损贡献闭合 max|diff|=%.1e（OK）" % abs(lhs - rhs))

    say("\n[3] 稳健性：剔除最好的 N 天（按日 alpha 排序）")
    tr = trim_stats(day_al)
    for k in sorted(tr, key=lambda s: int(s[3:])):
        d = tr[k]
        say("  %-6s %3d 天  均值 %+7.3f%%  中位 %+7.3f%%  t=%+5.2f"
            % (k, d["n_days"], d["mean"], d["med"], d["t"]))
    o = np.sort(day_al)[::-1]
    say("  top5 日 alpha：%s" % "、".join("%+0.2f%%" % (v * 100) for v in o[:5]))

    say("\n[4] 按月（绝对 / 基准 / alpha）")
    mon_out = {}
    for m in sorted({r["entry_date"][:6] for r in alpha_ok}):
        sub = [r for r in alpha_ok if r["entry_date"][:6] == m]
        am = np.array([r["trail_ret"] for r in sub], float)
        bmm = np.array([r["idx_ret"] for r in sub], float)
        alm = am - np.array([r["beta"] for r in sub], float) * bmm
        mon_out[m] = {"n": len(sub), "abs": round(am.mean() * 100, 3),
                      "bench": round(bmm.mean() * 100, 3),
                      "alpha": round(alm.mean() * 100, 3),
                      "alpha_win": round(float((alm > 0).mean()) * 100, 1)}
        say("  %s %4d 笔  绝对 %+7.3f%%  基准 %+7.3f%%  alpha %+7.3f%%（笔胜率 %4.1f%%）"
            % (m, len(sub), am.mean() * 100, bmm.mean() * 100, alm.mean() * 100,
               (alm > 0).mean() * 100))

    say("\n[5] 按当日信号笔数（绝对 / alpha）")
    by_day = defaultdict(list)
    for r in alpha_ok:
        by_day[r["entry_date"]].append(r)
    tier_out = {}
    for lo, hi, lab in ((1, 2, "1~2"), (3, 9, "3~9"), (10, 30, "10~30"), (31, 10 ** 6, ">=31")):
        ds = {d for d, v in by_day.items() if lo <= len(v) <= hi}
        sub = [r for r in alpha_ok if r["entry_date"] in ds]
        if not sub:
            continue
        am = np.array([r["trail_ret"] for r in sub], float)
        alm = am - np.array([r["beta"] for r in sub], float) * np.array([r["idx_ret"] for r in sub], float)
        tier_out[lab] = {"days": len(ds), "n": len(sub),
                         "abs": round(am.mean() * 100, 3),
                         "alpha": round(alm.mean() * 100, 3)}
        say("  %-6s %2d 天 %4d 笔  绝对 %+7.3f%%  alpha %+7.3f%%"
            % (lab, len(ds), len(sub), am.mean() * 100, alm.mean() * 100))

    # ---- 6. 在途仓位（只有绝对口径）----
    say("\n[6] 在途 %d 笔（截至 %s 收盘的浮动收益，未走完 20 日）" % (len(pending), asof))
    pend_out = {}
    for m in sorted({r["entry_date"][:6] for r in pending}):
        sub = [r for r in pending if r["entry_date"][:6] == m]
        x = np.array([r["trail_ret"] for r in sub], float)
        hold = np.median([r["trail_days"] for r in sub])
        pend_out[m] = {"n": len(sub), "avg": round(x.mean() * 100, 3),
                       "med": round(np.median(x) * 100, 3),
                       "win": round(float((x > 0).mean()) * 100, 1),
                       "hold_med": float(hold)}
        say("  %s %3d 笔  均值 %+7.3f%% 中位 %+7.3f%% 胜率 %4.1f%% 持仓中位 %0.1f 日"
            % (m, len(sub), x.mean() * 100, np.median(x) * 100, (x > 0).mean() * 100, hold))
    pa = np.array([r["trail_ret"] for r in pending], float)
    say("  合计 %d 笔  均值 %+0.3f%% 中位 %+0.3f%% 胜率 %0.1f%%"
        % (len(pa), pa.mean() * 100, np.median(pa) * 100, (pa > 0).mean() * 100))

    # ---- 7. 持有期曲线：alpha 随持有时间怎么衰减 ----
    # 基准必须按 h 重算。上一版沿用该笔实际持有期的 idx_ret，h=1 时减的是 9 天的
    # 基准跌幅 → alpha 被虚高到 +4.3%。这里用上证指数近似（等权全A 的任意区间
    # 收益需重算全市场，成本过高；两者趋势一致，绝对水平略有差异，已在输出标注）。
    say("\n[7] 固定持有 h 天（不止损），绝对与 alpha 随 h 的变化（基准=上证指数近似）")
    import pandas as pd
    import glob as _glob
    _rows = []
    for _p in sorted(_glob.glob(os.path.join(CS.TDX, "ts_index_daily", "part_*.parquet"))):
        _d = pd.read_parquet(_p, columns=["ts_code", "trade_date", "close"])
        _d = _d[_d["ts_code"] == "000001.SH"]
        if len(_d):
            _rows.append(_d)
    _ix = pd.concat(_rows, ignore_index=True)
    _ix["trade_date"] = (_ix["trade_date"].astype(str)
                         .str.replace("-", "", regex=False).str[:8])
    _ix = _ix.drop_duplicates("trade_date").set_index("trade_date")["close"].astype(float).sort_index()
    _ipos = {d: i for i, d in enumerate(_ix.index)}
    _ic = _ix.to_numpy()
    P = load_price_panel({r["code"] for r in alpha_ok}, CS.TDX, start="20240101")
    curve = {}
    for h in (1, 2, 3, 5, 10, 15, 20):
        vals, alps = [], []
        for r in alpha_ok:
            p = P.get(r["code"])
            ei = p["pos"].get(r["entry_date"]) if p else None
            if ei is None or ei + h > len(p["c"]) - 1:
                continue
            k = r["entry_px"] / p["c"][ei]
            px = p["c"][ei + h] * k
            ret = (px * (1 - SELL_COST)) / (r["entry_px"] * (1 + BUY_COST)) - 1
            i0 = _ipos.get(r["entry_date"])
            if i0 is None or i0 + h >= len(_ic):
                continue
            bh = _ic[i0 + h] / _ic[i0] - 1.0
            vals.append(ret)
            alps.append(ret - r["beta"] * bh)
        if vals:
            curve[h] = {"n": len(vals), "abs": round(float(np.mean(vals)) * 100, 3),
                        "alpha": round(float(np.mean(alps)) * 100, 3)}
            say("  h=%2d  %4d 笔  绝对 %+7.3f%%  alpha %+7.3f%%"
                % (h, len(vals), np.mean(vals) * 100, np.mean(alps) * 100))

    # ---- 7b. alpha 是"危机 alpha"还是全天候？按基准涨跌分档 ----
    say("\n[7b] 阿尔法是不是只在暴跌后有效（按该笔持有期的基准收益分档）")
    buckets = [(-1e9, -0.08, "基准<-8%（暴跌）"), (-0.08, -0.03, "-8%~-3%"),
               (-0.03, 0.0, "-3%~0%"), (0.0, 0.03, "0%~3%"), (0.03, 1e9, ">3%（上涨）")]
    regime = {}
    for lo, hi, lab in buckets:
        sub = [r for r in alpha_ok if lo <= r["idx_ret"] < hi]
        if len(sub) < 10:
            continue
        am = np.array([r["trail_ret"] for r in sub], float)
        alm = am - np.array([r["beta"] for r in sub], float) * np.array([r["idx_ret"] for r in sub], float)
        regime[lab] = {"n": len(sub), "abs": round(am.mean() * 100, 3),
                       "bench": round(float(np.mean([r["idx_ret"] for r in sub])) * 100, 3),
                       "alpha": round(alm.mean() * 100, 3),
                       "alpha_win": round(float((alm > 0).mean()) * 100, 1)}
        say("  %-14s %4d 笔  基准 %+7.3f%%  绝对 %+7.3f%%  alpha %+7.3f%%（笔胜率 %4.1f%%）"
            % (lab, len(sub), regime[lab]["bench"], am.mean() * 100,
               alm.mean() * 100, (alm > 0).mean() * 100))

    # ---- 8. 对冲成本敏感性 ----
    say("\n[8] 若做 beta 对冲：alpha 扣掉 IC 贴水后还剩多少")
    hedge_out = {}
    for ann in HEDGE_ANNUAL:
        per_trade = ann * np.mean([r["trail_days"] for r in alpha_ok]) / 250.0
        net_al = np.array([r["trail_ret"] - r["beta"] * r["idx_ret"] - per_trade
                           for r in alpha_ok], float)
        by = defaultdict(list)
        for r, v in zip(alpha_ok, net_al):
            by[r["entry_date"]].append(v)
        d = np.array([np.mean(v) for _, v in sorted(by.items())], float)
        hedge_out[ann] = {"per_trade_bp": round(per_trade * 1e4, 1),
                          "net_alpha": round(float(d.mean()) * 100, 3),
                          "t": round(day_t(d), 2)}
        say("  贴水 %4.0f%%/年 → 每笔 %4.1fbp → 净 alpha %+7.3f%%/日  t=%+5.2f"
            % (ann * 100, per_trade * 1e4, d.mean() * 100, day_t(d)))

    # ---- 9. 对冲组合账户层（把 alpha 当可交易收益，扣贴水）----
    say("\n[9] 对冲组合账户层（每笔收益 = alpha - 贴水，1/20 预算）")
    axis_all = CS.load_axis()
    axis = [d for d in axis_all if min(r["entry_date"] for r in alpha_ok) <= d <= axis_all[-1]]
    ap_ = {d: i for i, d in enumerate(axis)}
    acct = {}
    for ann in (0.06, 0.10):
        per_trade = ann * np.mean([r["trail_days"] for r in alpha_ok]) / 250.0
        picks = defaultdict(list)
        for r in alpha_ok:
            p = P.get(r["code"])
            if not p:
                continue
            xi = r["xt"]
            net = r["trail_ret"] - r["beta"] * r["idx_ret"] - per_trade
            exit_px = (1.0 + net) * r["entry_px"] * (1 + BUY_COST) / (1 - SELL_COST)
            ratio = build_ratio(p, r["ei"], xi, r["entry_px"], exit_px, axis, ap_,
                                buy_cost=BUY_COST, sell_cost=SELL_COST)
            picks[r["entry_date"]].append(
                Pick(r["code"], ap_[r["entry_date"]], ap_[p["d"][xi]], ratio, net))
        v = Engine(axis, hold=CS.HOLD).run(picks)
        acct[ann] = {k: v[k] for k in ("total", "cagr", "mdd", "sharpe", "vol", "util")}
        say("  贴水 %4.0f%%/年  累计 %+7.2f%%  cagr %+7.2f%%  回撤 %7.2f%%  夏普 %6.2f  util %5.1f%%"
            % (ann * 100, v["total"], v["cagr"], v["mdd"], v["sharpe"], v["util"]))

    out = {"meta": {"asof": asof, "n_list": len(recs), "n_pass": len(passed),
                    "n_settled": len(settled), "n_pending": len(pending),
                    "n_alpha": len(alpha_ok),
                    "base_buy": BUY_COST, "base_sell": SELL_COST},
           "abs_days": desc(abs_d),
           "three_views": {"abs": desc(day_abs_a), "bench": desc(day_bm),
                           "excess": desc(day_abs_a - day_bm), "alpha": desc(day_al),
                           "alpha_hold": desc(day_alh),
                           "beta_mean": round(float(be.mean()), 3)},
           "trim": tr, "by_month": mon_out, "by_tier": tier_out,
           "pending": pend_out, "hold_curve": {str(k): v for k, v in curve.items()},
           "regime": regime,
           "hedge": {str(k): v for k, v in hedge_out.items()},
           "hedge_account": {str(k): v for k, v in acct.items()},
           "caveats": ["alpha 仅覆盖 closed 里有 beta 的笔（open 无 beta/idx_ret）",
                       "hold_curve 的基准改用上证指数近似（非等权全A）",
                       "未计 track error：策略池与 IC/IM 成分不同"]}
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("\n落盘 %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
