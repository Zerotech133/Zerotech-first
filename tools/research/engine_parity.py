# -*- coding: utf-8 -*-
"""组合口径三方对照：为什么同一批信号能算出 +17% 和 -4.5%。

背景
----
历史上有两套组合算法，结论差 21pp：
  env_strat_3y   全买 hold20  → +16.96%
  exit_rules_ext A 持满20日   → -4.46%（新引擎，盯市+现金约束）

在统一口径之前，任何"账户层"结论都不能用。这里把差异拆成三个可控开关：

  M0  算术复利：eq *= (1 + batch_mean/hold)，按结算日入账，不盯市、无现金约束
      —— env_strat_3y 的做法，等价于假设资金无限、收益只在实际结算时进账
  M1  M0 + 现金约束：建仓额 = min(budget*eq, cash)，但净值仍按已实现收益计
  M2  M1 + 逐日盯市：持仓按收盘价估值（portfolio_mtm.Engine 的做法）
  M3  M2 但去掉现金约束：amt = budget*eq（允许透支），隔离"盯市"与"现金"两个因子

如果 M0 与 M3 接近而 M1/M2 差很多，说明差异来自现金约束（真实约束）；
如果 M0 与 M3 就差很多，说明"按结算日入账"本身是个系统性偏差。
"""
import glob
import json
import os
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from portfolio_mtm import Engine, Pick, build_ratio, load_price_panel  # noqa: E402

TDX = os.path.join(ROOT, "quant_backtest", "data", "tdx")
POOL = os.path.join(ROOT, "quant_backtest", "data", "chanlun_pool_ext_daily_full.jsonl")
OUT = os.path.join(ROOT, "portal", "engine_parity.json")

BUY = ("1买", "2买", "3买")
HOLD = 20
COST = 0.00075 + 0.00125


def _norm(s):
    return (pd.Series(s).astype(str).str.replace("-", "", regex=False)
            .str.slice(0, 8))


def main():
    rows = []
    for line in open(POOL, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") not in BUY or r.get("status") != "closed":
            continue
        if not (r.get("entry_px") and r.get("price") and r.get("entry_date")):
            continue
        rows.append({"code": r["code"], "type": r["type"],
                     "d": r["entry_date"].replace("-", "")[:8],
                     "x": (r["exit_date"] or "").replace("-", "")[:8],
                     "entry_px": float(r["entry_px"]),
                     "exit_px": float(r["exit_px"]), "ret": float(r["ret"])})
    print("池 %d 笔" % len(rows))

    out = []
    for p in sorted(glob.glob(os.path.join(TDX, "ts_index_daily", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "close"])
        d = d[d["ts_code"] == "000001.SH"]
        if len(d):
            d["trade_date"] = _norm(d["trade_date"])
            out.append(d)
    idx = pd.concat(out, ignore_index=True).drop_duplicates("trade_date")
    idx = idx.sort_values("trade_date")

    days = sorted({r["d"] for r in rows})
    axis = [k for k in idx["trade_date"].tolist()
            if days[0] <= k <= max(r["x"] for r in rows)]
    ap_ = {d: i for i, d in enumerate(axis)}
    print("日历 %d 日 %s~%s" % (len(axis), axis[0], axis[-1]))

    by = defaultdict(list)
    for r in rows:
        by[r["d"]].append(r)

    def metric(eq):
        e = np.asarray(eq, float)
        ret = np.diff(e) / e[:-1]
        sd = ret.std(ddof=1) if len(ret) > 1 else 0
        peak = np.maximum.accumulate(e)
        yrs = len(e) / 244.0
        return {"total": round((e[-1] - 1) * 100, 2),
                "cagr": round((e[-1] ** (1 / yrs) - 1) * 100, 2) if e[-1] > 0 else None,
                "mdd": round(float((e / peak - 1).min() * 100), 2),
                "vol": round(float(sd * np.sqrt(244) * 100), 2),
                "sharpe": round(float(ret.mean() / sd * np.sqrt(244)), 2) if sd else 0}

    # ---------- M0：算术复利（env_strat_3y 口径） ----------
    ev = defaultdict(float)
    for d in days:
        xs = by[d]
        # 结算日取该批的中位退出日（与 env_strat_3y nav 一致）
        settle = sorted(t[1] for t in ((r["d"], r["x"]) for r in xs))[len(xs) // 2]
        ev[settle] += float(np.mean([r["ret"] - 0 for r in xs])) * (1.0 / HOLD)
    eq = 1.0
    m0 = []
    for k in axis:
        eq *= (1.0 + ev.get(k, 0.0))
        m0.append(eq)
    r_m0 = metric(m0)

    # ---------- 建路径 + ratio ----------
    P = load_price_panel({r["code"] for r in rows}, TDX)
    keep = []
    n_nocode = 0
    for r in rows:
        p = P.get(r["code"])
        if p is None:                     # 池里混了指数代码（如 399001.SZ）
            n_nocode += 1
            continue
        ei = p["pos"].get(r["d"])
        xi = p["pos"].get(r["x"])
        if ei is None or xi is None or xi <= ei:
            continue
        r["ratio"] = build_ratio(p, ei, xi, r["entry_px"], r["exit_px"], axis, ap_)
        r["ei_a"], r["xi_a"] = ap_[r["d"]], ap_[r["x"]]
        keep.append(r)
    rows = keep
    by = defaultdict(list)
    for r in rows:
        by[r["d"]].append(r)
    print("可回测 %d 笔（无行情 %d 笔，缺后续K线 %d 笔）"
          % (len(keep), n_nocode, len(rows) - n_nocode - len(keep)))

    picks = {d: [Pick(r["code"], r["ei_a"], r["xi_a"], r["ratio"], r["ret"])
                 for r in by[d]] for d in by}

    # ---------- M1：算术 + 现金约束（不盯市） ----------
    def run_cash_only(picks, hold=HOLD):
        n = len(axis)
        cash, live = 1.0, []
        eqv = np.empty(n)
        n_skip = 0
        byd = {}
        for d, xs in picks.items():
            byd[ap_[d]] = xs
        for i in range(n):
            keep2 = []
            for h in live:
                if h[1] == i:
                    cash += h[0] * (1.0 + h[2])   # 只用最终收益，不盯市
                else:
                    keep2.append(h)
            live = keep2
            xs = byd.get(i)
            if xs:
                # 净值 = 现金 + 成本（不认浮盈）
                eq_now = cash + sum(h[0] for h in live)
                amt = min(1.0 / hold * eq_now, cash)
                if amt > 1e-9:
                    per = amt / len(xs)
                    for pk in xs:
                        live.append([per, pk.exit_i, pk.ret])
                        cash -= per
                else:
                    n_skip += 1
            eqv[i] = cash + sum(h[0] for h in live)
        v = metric(eqv)
        v["n_skip"] = n_skip
        return v

    m1 = run_cash_only(picks)

    # ---------- M2：Engine（盯市 + 现金约束） ----------
    eng = Engine(axis, hold=HOLD)
    v2 = eng.run(picks)
    m2 = {k: v2[k] for k in ("total", "cagr", "mdd", "vol", "sharpe", "n_skip")}

    # ---------- M3：盯市但不限现金（允许透支） ----------
    def run_mtm_nocash(picks, hold=HOLD):
        n = len(axis)
        cash, live = 1.0, []
        eqv = np.empty(n)
        byd = {ap_[d]: xs for d, xs in picks.items()}
        for i in range(n):
            keep2 = []
            for h in live:
                if h[1] == i:
                    cash += h[0] * (1.0 + h[3][i])
                else:
                    keep2.append(h)
            live = keep2
            xs = byd.get(i)
            if xs:
                eq_now = cash + sum(h[0] * (1.0 + h[3][i]) for h in live)
                amt = 1.0 / hold * eq_now        # 不 min(cash)，可透支
                per = amt / len(xs)
                for pk in xs:
                    live.append([per, pk.exit_i, pk.ret, pk.ratio])
                    cash -= per
            eqv[i] = cash + sum(h[0] * (1.0 + h[3][i]) for h in live)
        return metric(eqv)

    m3 = run_mtm_nocash(picks)

    # ---------- M4：固定下注规模（每批投初始权益的 5%，不随权益变化） ----------
    # 这是把"下注规模"从权益里解耦。若 M4 远好于 M3，说明「赚了加注、亏了减注」
    # 的正反馈是主要损耗源——而数据里权益高时的批收益恰恰更差（负相关），
    # 正反馈恰好把最多的钱押在期望最低的时刻。
    def run_fixed_stake(picks, hold=HOLD):
        n = len(axis)
        cash, live = 1.0, []
        eqv = np.empty(n)
        byd = {ap_[d]: xs for d, xs in picks.items()}
        for i in range(n):
            keep3 = []
            for h in live:
                if h[1] == i:
                    cash += h[0] * (1.0 + h[3][i])
                else:
                    keep3.append(h)
            live = keep3
            xs = byd.get(i)
            if xs:
                per = (1.0 / hold) / len(xs)      # 固定 5%/批，允许透支
                for pk in xs:
                    live.append([per, pk.exit_i, pk.ret, pk.ratio])
                    cash -= per
            eqv[i] = cash + sum(h[0] * (1.0 + h[3][i]) for h in live)
        return metric(eqv)

    m4 = run_fixed_stake(picks)

    print("\n" + "=" * 84)
    print("同一批 %d 笔、同一 hold=%d、同一日历，只改组合算法" % (len(rows), HOLD))
    print("=" * 84)
    print("  %-40s %9s %9s %9s %8s" % ("口径", "累计", "年化", "最大回撤", "波动"))
    for lab, v in (("M0 算术复利·结算日入账（env_strat_3y）", r_m0),
                   ("M1 M0 + 现金约束（成本计价，不盯市）", m1),
                   ("M2 M1 + 逐日盯市（Engine，本次重写）", m2),
                   ("M3 盯市但允许透支（隔离现金因子）", m3),
                   ("M4 盯市 + 固定下注规模（不看权益）", m4)):
        print("  %-40s %8.2f%% %8.2f%% %8.2f%% %7.2f%%"
              % (lab, v["total"], v["cagr"] or 0, v["mdd"], v["vol"]))
        if v.get("n_skip"):
            print("  %-40s   未建仓日 %d" % ("", v["n_skip"]))

    print("\n  拆解：")
    print("    盯市贡献   M3 - M0 = %+.2fpp" % (m3["total"] - r_m0["total"]))
    print("    现金约束   M2 - M3 = %+.2fpp" % (m2["total"] - m3["total"]))
    print("    合计       M2 - M0 = %+.2fpp" % (m2["total"] - r_m0["total"]))
    print("    下注规模   M2 - M4 = %+.2fpp  （正反馈拖累：赚了加注、亏了减注）"
          % (m2["total"] - m4["total"]))
    print("    固定下注   M4 - M0 = %+.2fpp  （余下差异才是复利方式本身）"
          % (m4["total"] - r_m0["total"]))

    payload = {"meta": {"n": len(rows), "axis": len(axis), "hold": HOLD,
                        "window": [axis[0], axis[-1]]},
               "M0_arith": r_m0, "M1_cash_only": m1, "M2_engine": m2,
               "M3_mtm_nocash": m3, "M4_fixed_stake": m4,
               "decomp": {"mtm": round(m3["total"] - r_m0["total"], 2),
                          "stake": round(m2["total"] - m4["total"], 2),
                          "cash": round(m2["total"] - m3["total"], 2),
                          "total": round(m2["total"] - r_m0["total"], 2)}}
    json.dump(payload, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n落盘 %s" % OUT)


if __name__ == "__main__":
    main()
