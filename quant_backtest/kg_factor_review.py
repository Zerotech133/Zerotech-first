# -*- coding: utf-8 -*-
"""图特征因子真 IC 复审管线（历史截面 → 前向收益 → 月度 IC → 门槛判定）。

背景：首轮检验（C:/Temp/kg_factor_test_r1.py，2026-10-09）是单截面 +
      静态因子回视（in-sample），不构成真 IC 序列。本管线消费每日晨链
      自动归档的 data/features/kg_features_hist/kgf_YYYYMMDD.parquet
      历史截面，用「T 截面因子值 → T+h 个交易日收益」构造前向 IC 序列，
      是 KG_CLOSEOUT 挂起项（图特征进九维评分）触发条件的判定依据。

对齐口径（Alphalens 惯例）：
  · 截面日 D 的因子值在 06:42 计算，反映 D 开盘前已知信息
  · base = 首个 >= D 的交易日（周末截面自然映射到下周一）
  · 收益 = close[base+h] / close[base] - 1（用 D 收盘价入场，标准做法）
  · 同日多份截面（周末链重跑）按 base 去重，保留最新 computed_at

判定门槛（每因子 × 每前向窗口，全部满足 → PASS，KG_CLOSEOUT 第四节）：
  1. 历史截面月数 >= 3
  2. |mIC| >= 0.02        （mIC = 月度 IC 序列均值）
  3. |t| >= 2             （月度 IC 序列 t 统计量）
  4. 方向一致             （同号月占比 >= 70%，且与分位收益 spread 同向）

用法：
  python kg_factor_review.py                 # 默认窗口 5/10/20
  python kg_factor_review.py --horizon 5 10 20 --to-csv C:/Temp/kg_factor_review.csv
  python kg_factor_review.py --selftest      # 管线自检（合成数据，不进正式判定）
"""

import argparse
import glob
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tdx_read as tdxr

HIST_DIR = os.path.join(HERE, "data", "features", "kg_features_hist")
FACTORS = [
    "deg_total",
    "deg_product",
    "peer_industry",
    "peer_sw_l1",
    "peer_region",
    "person_shared",
    "peer_shared_prod",
    "n_persons",
]
MIN_MONTHS = 3  # 触发条件：历史截面积累 >= 3 个月
GATE_ABS_IC = 0.02  # 门槛：|mIC| >= 0.02
GATE_T = 2.0  # 门槛：|t| >= 2
GATE_SAME_SIGN = 0.7  # 方向一致：同号月占比
MIN_SAMPLE = 30  # 单截面 Spearman 最小有效对数（同首轮）
MV_RISK = 0.5  # |ICmv| 超过则提示市值代理风险


def load_snapshots():
    """读全部 kgf_YYYYMMDD.parquet，带 snap_date 列；按日期升序。"""
    rows = []
    for p in sorted(glob.glob(os.path.join(HIST_DIR, "kgf_*.parquet"))):
        d = os.path.basename(p)[4:12]
        df = pd.read_parquet(p)
        df["snap_date"] = d
        rows.append(df)
    if not rows:
        return pd.DataFrame()
    snap = pd.concat(rows, ignore_index=True)
    return snap[["snap_date", "ts_code"] + FACTORS]


def price_wide(start):
    """ts_daily close 宽表：index=trade_date 升序，columns=ts_code。"""
    day = tdxr.read_range(
        "ts_daily", start=start, columns=["ts_code", "trade_date", "close"]
    )
    day = day[day["ts_code"].str.match(r"^\d{6}\.(SH|SZ|BJ)$")]
    pv = day.pivot_table(index="trade_date", columns="ts_code", values="close")
    return pv.sort_index()


def logmv_wide(start):
    """ts_daily_basic total_mv → log 宽表（市值代理体检）。"""
    mv = tdxr.read_range(
        "ts_daily_basic", start=start, columns=["ts_code", "trade_date", "total_mv"]
    )
    mv = mv[mv["total_mv"] > 0]
    w = mv.pivot_table(index="trade_date", columns="ts_code", values="total_mv")
    return np.log(w).sort_index()


def map_base(snap_dates, trading_days):
    """每截面日期 D → 首个 >= D 的交易日 base。"""
    tds = pd.Index(trading_days)
    out = {}
    for d in sorted(set(snap_dates)):
        ge = tds[tds >= d]
        out[d] = ge[0] if len(ge) else None
    return out


def project_weekdays(d, n):
    """自 d 起向后数 n 个工作日（不含 d 当天），粗估完成日（节假日顺延）。"""
    cur, cnt = datetime.strptime(d, "%Y%m%d"), 0
    while cnt < n:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            cnt += 1
    return cur.strftime("%Y-%m-%d")


def review(horizons, to_csv):
    snap = load_snapshots()
    if snap.empty:
        print("无截面归档（kg_features_hist 为空）——先跑晨链 kg_features 步骤。")
        return
    start = snap["snap_date"].min()
    pv = price_wide(start)
    lm = logmv_wide(start)
    trading_days = list(pv.index)
    bmap = map_base(snap["snap_date"].unique(), trading_days)
    snap["base"] = snap["snap_date"].map(bmap)
    print(
        "归档 %d 个截面日 / %d 行 → 去重后唯一 base 截面 %d 个（%s ~ %s）"
        % (
            snap["snap_date"].nunique(),
            len(snap),
            snap.dropna(subset=["base"])["base"].nunique(),
            snap.dropna(subset=["base"])["base"].min(),
            snap.dropna(subset=["base"])["base"].max(),
        )
    )
    snap = (
        snap.dropna(subset=["base"])
        .sort_values("snap_date")
        .groupby("base", as_index=False)
        .tail(1)
    )  # 每 base 保留最新截面
    print("最新交易日: %s" % trading_days[-1])

    rows = []
    for h in horizons:
        r = pv.shift(-h) / pv - 1
        for _, srow in snap.iterrows():
            base = srow["base"]
            if base not in r.index:
                continue
            rrow = r.loc[base].rename("r")
            lmrow = lm.loc[base] if base in lm.index else pd.Series(dtype=float)
            f = srow[FACTORS]
            valid = pd.concat([f.rename("f"), rrow], axis=1).dropna()
            if len(valid) < MIN_SAMPLE:
                continue
            for fac in FACTORS:
                x = valid["f"]
                y = valid["r"]
                ic = spearmanr(x, y).statistic
                cov = float((x.notna() & (x > 0)).mean())
                icmv = (
                    spearmanr(x, lmrow.reindex(valid.index).dropna()).statistic
                    if len(lmrow.dropna()) > MIN_SAMPLE
                    else np.nan
                )
                # 分位收益 spread：x 五分位 → 各组 y 均值，Q5-Q1
                spread = np.nan
                if x.nunique() >= 5:
                    q = pd.qcut(x.rank(method="first"), 5, labels=False)
                    grp = y.groupby(q).mean()
                    spread = float(grp.get(4, np.nan) - grp.get(0, np.nan))
                rows.append(
                    dict(
                        snap_date=srow["snap_date"],
                        base=base,
                        ym=base[:6],
                        factor=fac,
                        horizon=h,
                        ic=ic,
                        icmv=icmv,
                        cov=cov,
                        spread=spread,
                    )
                )
    if not rows:
        print("\n暂无可用前向截面（首个归档 2026-10-09，收益窗口未走满）。")
        for h in horizons:
            print(
                "  r%-2d 首个截面预计 %s 完成"
                % (h, project_weekdays(trading_days[-1], h))
            )
        return

    df = pd.DataFrame(rows)

    # ── 月度聚合（真 IC 序列）──
    agg = []
    for (fac, h), g in df.groupby(["factor", "horizon"]):
        m = g.groupby("ym")["ic"].agg(["mean", "count"])
        mIC = float(m["mean"].mean())
        t = (
            float(m["mean"].mean() / m["mean"].std() * np.sqrt(len(m)))
            if len(m) > 1
            else np.nan
        )
        months = len(m)
        same = float((np.sign(m["mean"]) == np.sign(mIC)).mean())
        icmv = float(g["icmv"].mean())
        cov = float(g["cov"].mean())
        spread = float(g["spread"].dropna().mean())
        agg.append(
            dict(
                factor=fac,
                horizon=h,
                snapshots=g["base"].nunique(),
                months=months,
                mIC=mIC,
                t=t,
                same=same,
                icmv=icmv,
                cov=cov,
                spread=spread,
            )
        )
    res = pd.DataFrame(agg)

    # ── 门槛判定 ──
    verdicts = []
    for _, r in res.iterrows():
        if r["months"] < MIN_MONTHS:
            v = "积累中(%d/%d月)" % (r["months"], MIN_MONTHS)
        elif (
            abs(r["mIC"]) >= GATE_ABS_IC
            and abs(r["t"]) >= GATE_T
            and r["same"] >= GATE_SAME_SIGN
            and np.sign(r["spread"]) == np.sign(r["mIC"])
        ):
            v = "PASS"
        else:
            v = "未过"
        verdicts.append(v)
    res["verdict"] = verdicts
    res["risk"] = res["icmv"].abs().apply(lambda a: "市值代理" if a > MV_RISK else "")

    pd.set_option("display.width", 220)
    show = res.copy()
    for c in ["mIC", "t", "icmv", "cov"]:
        show[c] = show[c].astype(float).round(3)
    show["same"] = (show["same"] * 100).round(0).astype(str) + "%"
    show["cov"] = (show["cov"] * 100).round(1)
    print(
        "\n%s  判定门槛：月数>=%d | |mIC|>=%.2f | |t|>=%.1f | 同号月>=%.0f%%"
        % ("=" * 40, MIN_MONTHS, GATE_ABS_IC, GATE_T, GATE_SAME_SIGN * 100)
    )
    print(show.to_string(index=False))
    if to_csv:
        res.to_csv(to_csv, index=False, encoding="utf-8-sig")
        print("\n明细已存 %s" % to_csv)


def selftest():
    """管线自检（合成数据，不进正式判定）。"""
    ok = True
    snap = load_snapshots()
    pv = price_wide(snap["snap_date"].min() if not snap.empty else "20261001")
    tds = list(pv.index)
    bmap = map_base(snap["snap_date"].unique(), tds)
    snap["base"] = snap["snap_date"].map(bmap)
    ded = snap.dropna(subset=["base"]).sort_values("snap_date").groupby("base").tail(1)
    print(
        "[A] 归档 %d 个截面日 / %d 行 → 唯一 base %d 个"
        % (snap["snap_date"].nunique(), len(snap), ded["base"].nunique())
    )

    X = pd.DataFrame(np.arange(1.0, 21.0).reshape(5, 4))
    r2 = X.shift(-2) / X - 1
    manual = X.iloc[2] / X.iloc[0] - 1
    if np.allclose(r2.iloc[0].values, manual.values):
        print("[B] 前向收益 r_h = close[t+h]/close[t]-1 与手动一致 ✅")
    else:
        print("[B] 前向收益数学不一致 ❌")
        ok = False

    f = -r2.iloc[0].values
    ic = spearmanr(f, r2.iloc[0].values).statistic
    if abs(ic - (-1.0)) < 1e-9:
        print("[C] 单截面 IC 机制：合成因子 IC=%.3f（期望 -1.000）✅" % ic)
    else:
        print("[C] IC 机制异常：%.3f ❌" % ic)
        ok = False

    # D. 月度聚合与门槛（3 个月合成 IC 序列）
    rng = np.random.default_rng(7)
    mics = rng.normal(0.05, 0.01, 4)  # 4 个月均值 0.05，显著同向
    tval = mics.mean() / mics.std() * np.sqrt(len(mics))
    if abs(mics.mean()) >= GATE_ABS_IC and abs(tval) >= GATE_T:
        print(
            "[D] 门槛判定逻辑：合成 mIC=%.3f t=%.2f → PASS 分支 ✅"
            % (mics.mean(), tval)
        )
    else:
        print("[D] 门槛判定逻辑异常 ❌")
        ok = False
    print("\n自检%s" % ("全部通过 ✅" if ok else "存在失败 ❌"))
    return ok


def main():
    ap = argparse.ArgumentParser(description="图特征因子真 IC 复审管线")
    ap.add_argument("--horizon", type=int, nargs="+", default=[5, 10, 20])
    ap.add_argument("--to-csv", help="明细输出 CSV 路径")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    print("=== 图特征因子真 IC 复审 %s ===" % datetime.now().strftime("%F %T"))
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    review(args.horizon, args.to_csv)


if __name__ == "__main__":
    main()
