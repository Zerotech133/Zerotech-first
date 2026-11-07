# -*- coding: utf-8 -*-
"""补 2023 下半年池子后，检验「开头那波」依赖是否消解（K-RES-20261108-SPAN）。

要回答的问题
------------
三年扩展池（2024-01~2026-10）的所有账户层结论都带着一个隐患：**样本起点正好
压在 2024-01~02 的暴跌上**（上证两个月 -8%，微盘股腰斩）。之前实测：把开头
20 个建仓日去掉，A 规则累计从 22.26% 跳到 41.49%——收益高度依赖「是否在最初
那波里建仓」。这不是策略性质，是样本切点性质。

所以把起点往前推约 3 个月（2023-09 起，受 MIN_BARS=120 与 ashare 起点
2023-04-03 限制），同一套口径重算，看：
  1. 剔除 3买 + 持满 20 日的账户层指标变化多大；
  2. 移动止损 8% 的优势是否还在；
  3. sigdens P30 入场过滤的优势是否还在。

判据（先说清，避免事后找理由）
------------------------------
- 若「累计/回撤」在两个窗口间变化 > 50%，或夏普排序翻转 → 结论不稳定，
  之前所有账户层数字都要标注「依赖样本切点」。
- 若指标变化在合理范围且排序不变 → 可以认为三年结论不是切点产物。

口径
----
剔 3买、hold=20、成本 0.075%/0.125%、路径价 close*adj_factor + k 归一、
账户层用 portfolio_mtm.Engine（全交易日历 + 盯市 + 资金预算 1/20 + 现金约束）。
主判据同时给「日批均」（与暴露无关）与账户层（受 util 影响，仅参考）。
"""
import argparse
import json
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from portfolio_mtm import (BUY_COST, SELL_COST, Engine, Pick,  # noqa: E402
                           build_ratio, load_price_panel)

QBT = os.path.join(ROOT, "quant_backtest")
TDX = os.path.join(QBT, "data", "tdx")
OUT = os.path.join(ROOT, "portal", "pool_span_evidence.json")

HOLD = 20
TRAIL = 0.08
SIG_TH = 0.4886
BUY = ("1买", "2买")


def _n(s):
    return str(s or "").replace("-", "")[:8]


def load_pool(path):
    rows = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") not in BUY:
            continue
        if r.get("status") != "closed":
            continue
        if not (r.get("entry_px") and r.get("price") and r.get("entry_date")):
            continue
        rows.append({"code": r["code"], "type": r["type"],
                     "d": _n(r["entry_date"]), "x": _n(r.get("exit_date")),
                     "entry_px": float(r["entry_px"]), "ret": float(r["ret"])})
    return rows


def load_counts_all(path, axis):
    """日信号数（**不筛 status**，否则末尾 20 日被右删失）。"""
    c = defaultdict(int)
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") not in BUY:
            continue
        d = _n(r.get("entry_date"))
        if d:
            c[d] += 1
    return pd.Series({d: float(c.get(d, 0)) for d in axis})


def load_axis():
    out = []
    import glob
    for p in sorted(glob.glob(os.path.join(TDX, "ts_index_daily", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date"])
        d = d[d["ts_code"] == "000001.SH"]
        if len(d):
            d["trade_date"] = (d["trade_date"].astype(str)
                               .str.replace("-", "", regex=False).str[:8])
            out.append(d)
    return sorted(set(pd.concat(out, ignore_index=True)["trade_date"]))


def sim(p, ei, entry_px, trail=None):
    k = entry_px / p["c"][ei]
    hard = min(ei + HOLD, len(p["c"]) - 1)
    if trail is None:
        return hard, p["c"][hard] * k, "hold"
    peak = entry_px
    for j in range(ei + 1, hard + 1):
        o, hi, lo = p["o"][j] * k, p["h"][j] * k, p["l"][j] * k
        if not np.isfinite(o):
            continue
        lvl = max(entry_px * (1.0 - trail), peak * (1.0 - trail))
        if lo <= lvl:
            return j, min(lvl, o), "trail"
        peak = max(peak, hi)
    return hard, p["c"][hard] * k, "hold"


def net_ret(ep, xp):
    return (xp * (1.0 - SELL_COST)) / (ep * (1.0 + BUY_COST)) - 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default=os.path.join(
        QBT, "data", "chanlun_pool_ext_2023h2.jsonl"))
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    rows = load_pool(a.pool)
    axis_all = load_axis()
    d_lo = min(r["d"] for r in rows)
    axis = [d for d in axis_all if d >= d_lo]
    ap_ = {d: i for i, d in enumerate(axis)}
    print("池 %d 笔（剔3买/已结算），%s ~ %s，日历 %d 日"
          % (len(rows), d_lo, max(r["d"] for r in rows), len(axis)))

    P = load_price_panel({r["code"] for r in rows}, TDX, ohlc=True)
    keep, n_nocode, n_nobar = [], 0, 0
    for r in rows:
        p = P.get(r["code"])
        if p is None:
            n_nocode += 1
            continue
        ei = p["pos"].get(r["d"])
        if ei is None:
            n_nobar += 1
            continue
        if (ei + HOLD) > (len(p["c"]) - 1):
            continue                      # 未到期，两口径都不能结算，统一剔除
        xt, px_t, _ = sim(p, ei, r["entry_px"], TRAIL)
        xh, px_h, _ = sim(p, ei, r["entry_px"], None)
        r["ei"] = ei
        r["xt"], r["xh"] = xt, xh
        r["rt"], r["rh"] = net_ret(r["entry_px"], px_t), net_ret(r["entry_px"], px_h)
        r["xt_ax"] = ap_[p["d"][xt]]
        r["xh_ax"] = ap_[p["d"][xh]]
        keep.append(r)
    print("可结算 %d 笔（无行情 %d、无入场K线 %d）" % (len(keep), n_nocode, n_nobar))

    cnt = load_counts_all(a.pool, axis)
    dens = (cnt.shift(1) / cnt.shift(1).rolling(20).mean()).replace(
        [np.inf, -np.inf], np.nan)

    def batch(rows_, f):
        by = defaultdict(list)
        for r in rows_:
            by[r["d"]].append(r[f])
        v = np.array([np.mean(x) for x in by.values()], float)
        return float(v.mean()) * 100, len(v)

    def account(rows_, rule):
        eng = Engine(axis, hold=HOLD)
        by = defaultdict(list)
        for r in rows_:
            ret = r["rt"] if rule == "trail" else r["rh"]
            xi = r["xt"] if rule == "trail" else r["xh"]
            xpx = (1.0 + ret) * r["entry_px"] * (1 + BUY_COST) / (1 - SELL_COST)
            p = P[r["code"]]
            ratio = build_ratio(p, r["ei"], xi, r["entry_px"], xpx, axis, ap_)
            by[r["d"]].append(Pick(r["code"], ap_[r["d"]],
                                   ap_[p["d"][xi]], ratio, ret))
        return eng.run(by)

    # 2023-07~09 只有约 220~430 只股票能出信号：ashare 多数标的从 2023-04-03 起，
    # MIN_BARS=120 意味着要到 2023-09 末才全部具备资格，早期是「长历史子集」，
    # 有覆盖偏差。所以真实可用的扩展窗口是 2023-10 起，全样本仅作对照展示。
    wins = [("全样本 %s 起（覆盖不全）" % d_lo, None),
            ("扩展 20231001 起", "20231001"),
            ("对照 20240102 起", "20240102")]
    res = {"pool": os.path.basename(a.pool), "n_rows": len(keep),
           "span": [d_lo, max(r["d"] for r in keep)], "windows": {}}
    for lab, start in wins:
        sel = keep if start is None else [r for r in keep if r["d"] >= start]
        if not sel:
            continue
        bl_t, bl_d = batch(sel, "rt")
        bh_t, bh_d = batch(sel, "rh")
        v_t = account(sel, "trail")
        v_h = account(sel, "hold")
        # sigdens 过滤
        ps = [r for r in sel if np.isfinite(dens.get(r["d"], np.nan))
              and dens[r["d"]] >= SIG_TH]
        pg_t, pg_d = batch(ps, "rt") if ps else (None, 0)
        v_gt = account(ps, "trail") if ps else {}
        res["windows"][lab] = {
            "n": len(sel), "days": len({r["d"] for r in sel}),
            "batch_trail": round(bl_t, 3), "batch_hold": round(bh_t, 3),
            "acct_trail": {k: v_t.get(k) for k in ("total", "cagr", "mdd", "sharpe", "util")},
            "acct_hold": {k: v_h.get(k) for k in ("total", "cagr", "mdd", "sharpe", "util")},
            "sig_n": len(ps), "sig_days": pg_d,
            "sig_batch_trail": round(pg_t, 3) if pg_t is not None else None,
            "sig_acct_trail": {k: v_gt.get(k) for k in ("total", "cagr", "mdd", "sharpe", "util")},
        }
        res["windows"][lab]["codes"] = len({r["code"] for r in sel})
        print("\n[%s] 笔数 %d / 开仓日 %d / 股票 %d"
              % (lab, len(sel), res["windows"][lab]["days"],
                 res["windows"][lab]["codes"]))
        print("  日批均  移动止损 %+.3f%%（%d 日）   持满20 %+.3f%%"
              % (bl_t, bl_d, bh_t))
        print("  账户层  移动止损 累计%+7.2f%% 年化%+7.2f%% 回撤%+8.2f%% 夏普%5.2f"
              % (v_t["total"], v_t["cagr"] or 0, v_t["mdd"], v_t["sharpe"]))
        print("  账户层  持满20   累计%+7.2f%% 年化%+7.2f%% 回撤%+8.2f%% 夏普%5.2f"
              % (v_h["total"], v_h["cagr"] or 0, v_h["mdd"], v_h["sharpe"]))
        if ps:
            print("  sigdensP30 + 止损  日批均 %+.3f%%  账户 累计%+7.2f%% 回撤%+8.2f%% 夏普%5.2f"
                  % (pg_t, v_gt["total"], v_gt["mdd"], v_gt["sharpe"]))

    # ---------- 「开头那波」到底贡献多少 ----------
    # 扩展窗口里把 2024-01-01~2024-02-29 建仓的笔整体剔掉，看还剩多少。
    # 这是把「样本切点」问题转成「贡献占比」问题：只要贡献占比可解释，
    # 结论就不是切点产物。
    ext = [r for r in keep if r["d"] >= "20231001"]
    crash = [r for r in ext if "20240101" <= r["d"] <= "20240229"]
    v_all = account(ext, "trail")
    v_no = account([r for r in ext if not ("20240101" <= r["d"] <= "20240229")], "trail")
    res["crash_window"] = {
        "n_all": len(ext), "n_crash": len(crash),
        "share_picks": round(len(crash) / max(1, len(ext)) * 100, 1),
        "acct_with": {k: v_all.get(k) for k in ("total", "cagr", "mdd", "sharpe")},
        "acct_without": {k: v_no.get(k) for k in ("total", "cagr", "mdd", "sharpe")},
    }
    print("\n2024-01/02 建仓笔数 %d / 扩展窗口 %d（%.1f%%）："
          % (len(crash), len(ext), len(crash) / max(1, len(ext)) * 100))
    print("  含：累计 %+.2f%% 年化 %+.2f%% 回撤 %+.2f%% 夏普 %.2f"
          % (v_all["total"], v_all["cagr"] or 0, v_all["mdd"], v_all["sharpe"]))
    print("  剔：累计 %+.2f%% 年化 %+.2f%% 回撤 %+.2f%% 夏普 %.2f"
          % (v_no["total"], v_no["cagr"] or 0, v_no["mdd"], v_no["sharpe"]))

    # 稳定性判定
    # 只比较两个「XX 起」的窗口，全样本那档有覆盖偏差，不参与判定
    ks = [k for k in res["windows"] if k.endswith("起") and "覆盖不全" not in k]
    if len(ks) >= 2:
        a1, b1 = res["windows"][ks[0]], res["windows"][ks[-1]]
        dd = abs(a1["acct_trail"]["mdd"] - b1["acct_trail"]["mdd"])
        dt_ = abs(a1["acct_trail"]["total"] - b1["acct_trail"]["total"])
        same_order = ((a1["acct_trail"]["sharpe"] > a1["acct_hold"]["sharpe"])
                      == (b1["acct_trail"]["sharpe"] > b1["acct_hold"]["sharpe"]))
        res["stability"] = {
            "mdd_delta_pp": round(dd, 2), "total_delta_pp": round(dt_, 2),
            "order_same": bool(same_order),
            "verdict": ("结论对样本切点稳健" if dd < 12 and dt_ < 25 and same_order
                        else "结论依赖样本切点，需标注"),
        }
        print("\n稳定性：回撤差 %.2fpp、累计差 %.2fpp、规则排序一致=%s → %s"
              % (dd, dt_, same_order, res["stability"]["verdict"]))

    json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("落盘 %s" % a.out)


if __name__ == "__main__":
    main()
