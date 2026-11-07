# -*- coding: utf-8 -*-
"""zt 条件规则 vs sigdens：独立性检验 + 可交易回测。

## 为什么必须先做这一节

`struct_regime_interaction` 发现：`zt_dens` 在「上行/放量」层内 rho=-0.165（p=0.003，
跨年 3/3 同号），在「上行/缩量」层内 rho=+0.174（p=0.050，跨年 2/2）——符号相反、
跨年稳定，全样本却只剩 -0.019。这是干净的交互效应。

**但它可能只是 sigdens 的马甲**：两者都是「市场热度 / 拥挤度」类日级指标，
上证涨停家数多的日子，缠论信号自然也密。若 corr 很高，那 zt 层内效应只是把
sigdens 换了个算法，没有增量 → 不可落地（因为已有 sigdens）。

## 三个检验，缺一不可

1. **相关**：zt_dens / zt_cnt 与 sigdens 的 Spearman。|rho| > 0.6 基本可判马甲。
2. **增量**：在 sigdens pass 子集内，zt 条件是否还能分出好坏。
   —— 这是唯一重要的检验：sigdens 是我们已经决定要用的东西，
      zt 的价值只能是「在 sigdens 之上再加一层」，不能是替代。
3. **可交易回测**：规则必须只用 t-1 收盘可知信息（zt_q 用滚动 250 日分位、shift(1)），
   且必须过随机对照（随机抽同样天数），否则只是「少交易」。

## 规则定义

  zt_q   = zt_cnt 在过去 250 个交易日的滚动分位（shift(1)，事前可知）
  hot    = (trend=1 上行) & (vol=1 放量) & (zt_q >= 0.70)
           —— 即「放量上行 + 涨停家数处历史高位」= 情绪透支区

  R0 全信号
  R1 sigdens pass
  R2 仅剔除 hot（不吃 sigdens）
  R3 sigdens pass 且非 hot   ← 若 R3 > R1，zt 才有增量价值
"""
import argparse
import glob
import importlib.util
import json
import os
import sys
from collections import defaultdict
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(HERE, "entry_timing_3y.py")

spec = importlib.util.spec_from_file_location("et3y", SPEC)
et = importlib.util.module_from_spec(spec)
sys.modules["et3y"] = et
spec.loader.exec_module(et)

TDX = et.TDX
POOL = et.POOL
SHADOW_POOL = et.POOL
OUT = os.path.join(ROOT, "portal", "zt_vs_sigdens_evidence.json")
SEED = 20261108
SIG_TH = 0.4843
ZT_Q = 0.70
BOOT = 400


def sigdens_series(axis, pool_path):
    """前一日 1买+2买 笔数 / 前 20 日笔数均值。**不筛 status**。

    与 combo_shadow.load_ext_counts 同口径：筛 status 会让末尾 20 日右删失，
    密度被机械压低（实测 2026-10 从 23.4 掉到 1.7）。
    """
    cnt = defaultdict(int)
    for line in open(pool_path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") not in ("1买", "2买"):
            continue
        ed = str(r.get("entry_date") or "").replace("-", "")[:8]
        if ed:
            cnt[ed] += 1
    c = pd.Series({d: float(cnt.get(d, 0)) for d in axis}, dtype=float)
    return c.shift(1) / c.rolling(20).mean().shift(1)


def rolling_rank(s, win=250):
    """滚动分位（只用 t 及以前的数据）——这是 PIT 的关键：分位不能看未来。"""
    v = s.to_numpy(float)
    out = np.full(len(v), np.nan)
    for i in range(len(v)):
        lo = max(0, i - win + 1)
        w = v[lo:i + 1]
        w = w[np.isfinite(w)]
        if len(w) >= 60 and np.isfinite(v[i]):
            out[i] = float((w <= v[i]).mean())
    return pd.Series(out, index=s.index)


def batch(rows, key="d"):
    by = defaultdict(list)
    for r in rows:
        by[r[key]].append(r["ret"])
    per = np.array([np.mean(v) for v in by.values()], float)
    if not len(per):
        return {"days": 0, "avg": None}
    return {"days": len(per), "avg": round(float(per.mean()) * 100, 3),
            "win": round(float((per > 0).mean()) * 100, 1),
            "std": round(float(per.std(ddof=1)) * 100, 2) if len(per) > 1 else None}


def null_test(rows, n_keep, boot=BOOT, seed=SEED):
    """随机抽同样天数的日，看日批均零分布。"""
    by = defaultdict(list)
    for r in rows:
        by[r["d"]].append(r["ret"])
    allv = np.array([np.mean(v) for v in by.values()], float)
    n = n_keep
    if n == 0 or n >= len(allv):
        return None
    real = None
    rng = np.random.default_rng(seed)
    draws = np.array([allv[rng.choice(len(allv), n, replace=False)].mean()
                      for _ in range(boot)], float)
    return {"n": n, "universe": int(len(allv)),
            "null_p50": round(float(np.percentile(draws, 50)) * 100, 3),
            "null_p95": round(float(np.percentile(draws, 95)) * 100, 3),
            "boot": draws}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--since", default="20231009")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    say = (lambda *x, **k: None) if a.json else print

    rows = et.load_pool(since=a.since.replace("-", "")[:8])
    idx = et.load_index()
    axis = et.build_axis(rows, idx)
    say("池 %d 笔 / 日历 %d 日 %s~%s" % (len(rows), len(axis), axis[0], axis[-1]))

    f = et.load_market_struct(axis)
    sd = sigdens_series(axis, SHADOW_POOL)

    # 事前状态
    s_close = idx.set_index("trade_date")["close"].astype(float).reindex(axis)
    ma60 = s_close.rolling(60).mean()
    trend = (s_close > ma60).astype(float).where(ma60.notna()).shift(1)

    amt = defaultdict(float)
    for p in sorted(glob.glob(os.path.join(TDX, "ts_daily", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["trade_date", "amount"])
        d["trade_date"] = d["trade_date"].astype(str).str.replace("-", "", regex=False).str[:8]
        d = d[d["trade_date"].isin(set(axis))]
        if not len(d):
            continue
        for dt, v in d.groupby("trade_date")["amount"].sum().items():
            amt[dt] += float(v)
    a_s = pd.Series({d: amt.get(d, np.nan) for d in axis}, dtype=float)
    vol = (a_s.rolling(61).apply(lambda w: float((w[:-1] <= w[-1]).mean())
                                 if len(w) > 1 else np.nan, raw=True) >= 0.5).astype(float)
    vol = vol.where(a_s.rolling(61).count() > 1).shift(1)

    zt_cnt = f["zt_cnt"]
    zt_q = rolling_rank(zt_cnt, 250).shift(1)
    zt_dens = f["zt_dens"].shift(1)

    # ---------- 1) 相关性：zt 是不是 sigdens 的马甲 ----------
    dd = sorted({r["d"] for r in rows} & set(axis))
    v_sd = np.array([sd.get(d, np.nan) for d in dd], float)
    v_zc = np.array([zt_cnt.shift(1).get(d, np.nan) for d in dd], float)
    v_zd = np.array([zt_dens.get(d, np.nan) for d in dd], float)
    v_tr = np.array([trend.get(d, np.nan) for d in dd], float)
    v_vo = np.array([vol.get(d, np.nan) for d in dd], float)

    def rho(x, y):
        m = np.isfinite(x) & np.isfinite(y)
        if m.sum() < 20:
            return None
        return round(float(pd.Series(x[m]).corr(pd.Series(y[m]), method="spearman")), 3)

    corr = {"sigdens~zt_cnt": rho(v_sd, v_zc),
            "sigdens~zt_dens": rho(v_sd, v_zd),
            "sigdens~trend": rho(v_sd, v_tr),
            "sigdens~vol": rho(v_sd, v_vo),
            "zt_cnt~zt_dens": rho(v_zc, v_zd)}
    say("\n[1] 相关（Spearman，n=%d 日）" % len(dd))
    for k, v in corr.items():
        say("  %-18s %s" % (k, "%.3f" % v if v is not None else "-"))

    # ---------- 2) 规则构造 ----------
    hot, sig_ok = set(), set()
    for r in rows:
        d = r["d"]
        a1, b1, q = trend.get(d, np.nan), vol.get(d, np.nan), zt_q.get(d, np.nan)
        if np.isfinite(a1) and np.isfinite(b1) and np.isfinite(q):
            if a1 == 1 and b1 == 1 and q >= ZT_Q:
                hot.add(d)
        s = sd.get(d, np.nan)
        if np.isfinite(s) and s <= SIG_TH:
            sig_ok.add(d)

    rules = {
        "R0 全信号": lambda r: True,
        "R1 sigdens pass": lambda r: r["d"] in sig_ok,
        "R2 排除 hot": lambda r: r["d"] not in hot,
        "R3 sigdens pass 且非 hot": lambda r: r["d"] in sig_ok and r["d"] not in hot,
    }
    say("\n[2] 事前状态覆盖：hot 日 %d / sigdens pass 日 %d / 有信号日 %d"
        % (len(hot), len(sig_ok), len({r["d"] for r in rows})))

    res = {"meta": {"pool": os.path.basename(POOL), "sig_th": SIG_TH, "zt_q": ZT_Q,
                    "window": [axis[0], axis[-1]], "n_rows": len(rows),
                    "n_days": len({r["d"] for r in rows}),
                    "hot_days": len(hot), "sig_pass_days": len(sig_ok)},
           "corr": corr, "rules": {}}

    say("\n[3] 规则对比（日批均，持满口径 ret）")
    base_rows = rows
    for name, fn in rules.items():
        sel = [r for r in base_rows if fn(r)]
        b = batch(sel)
        if not b["days"]:
            continue
        nt = null_test(base_rows, b["days"])
        real = b["avg"]
        p = (round(float((nt["boot"] >= real / 100.0).mean()), 4)
             if nt is not None else None)
        by_year = {}
        for yr in sorted({r["d"][:4] for r in sel}):
            sub = [r for r in sel if r["d"][:4] == yr]
            bb = batch(sub)
            by_year[yr] = {"days": bb["days"], "avg": bb["avg"]}
        res["rules"][name] = {"days": b["days"], "avg": real, "win": b["win"],
                              "p_vs_random": p,
                              "null_p50": None if nt is None else nt["null_p50"],
                              "null_p95": None if nt is None else nt["null_p95"],
                              "by_year": by_year}
        say("  %-24s 日数%4d  日批均%+7.3f%%  日胜率%5.1f%%  随机p=%s  分年 %s"
            % (name, b["days"], real, b["win"],
               "%.3f" % p if p is not None else "-",
               {k: "%+.2f" % v["avg"] for k, v in by_year.items()}))

    # ---------- 3) 增量检验：R3 vs R1 ----------
    r1, r3 = res["rules"].get("R1 sigdens pass"), res["rules"].get("R3 sigdens pass 且非 hot")
    say("\n[4] 增量检验（sigdens 之上是否还有 zt 的价值）")
    verdict = ""
    if r1 and r3:
        diff = r3["avg"] - r1["avg"]
        # 只保留「少交易」解释的对照：从 R1 的日里随机去掉同样多天
        by = defaultdict(list)
        for r in rows:
            if r["d"] in sig_ok:
                by[r["d"]].append(r["ret"])
        d1 = {d: float(np.mean(v)) for d, v in by.items()}
        keep = [d for d in d1 if d not in hot]
        n_drop = len(d1) - len(keep)
        rng = np.random.default_rng(SEED)
        keys = list(d1)
        draws = []
        for _ in range(BOOT):
            pick = rng.choice(len(keys), len(keep), replace=False)
            draws.append(float(np.mean([d1[keys[i]] for i in pick])) * 100)
        draws = np.array(draws, float)
        p_drop = float((draws >= r3["avg"]).mean())
        res["increment"] = {"R1_avg": r1["avg"], "R3_avg": r3["avg"],
                            "diff": round(diff, 3), "days_dropped": n_drop,
                            "null_p50": round(float(np.percentile(draws, 50)), 3),
                            "null_p95": round(float(np.percentile(draws, 95)), 3),
                            "p_value": round(p_drop, 4)}
        say("  R1 %+.3f%% → R3 %+.3f%%（差 %+.3fpp，剔除 %d 日）"
            % (r1["avg"], r3["avg"], diff, n_drop))
        say("  「只剔同样多天」的随机对照：中位 %+.3f%% / 95%% %+.3f%% / p=%.3f"
            % (res["increment"]["null_p50"], res["increment"]["null_p95"], p_drop))
        if diff > 0 and p_drop <= 0.05:
            verdict = "zt 在 sigdens 之上有增量（过 5%）"
        elif diff > 0 and p_drop <= 0.15:
            verdict = "zt 有正的增量但未过 5%（边缘，需前推验证）"
        elif diff > 0:
            verdict = "zt 增量不能与「少交易」区分 → 无增量价值"
        else:
            verdict = "zt 剔除反使收益下降 → 反向，规则方向错"
    res["verdict"] = verdict
    say("  判定：%s" % verdict)

    json.dump(res, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("\n落盘 %s" % a.out)


if __name__ == "__main__":
    main()
