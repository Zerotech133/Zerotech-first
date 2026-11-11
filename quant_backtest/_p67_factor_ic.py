# -*- coding: utf-8 -*-
"""P67 因子 IC 筛查：先证明有没有 alpha，再谈建管道。

口径：
  · 因子在 T 日收盘可得（只用 T 及之前数据，无未来函数）
  · 未来收益 = T+1 开盘 → T+5 收盘（与回测 trade_returns 同口径）
  · 复权：用 pct_chg 累乘出净值，再反推复权因子，避免除权跳空
  · IC = 逐日截面 Spearman 秩相关；ICIR = mean/std；t = ICIR*sqrt(n)
判据（沿用业界常用门槛）：
  |IC 均值| > 0.02 且 ICIR > 0.3 且 与现有 score 相关性低 → 才值得建管道
"""
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

DATA = r"C:/项目/老田的AI助手/quant_backtest/data/ashare"
OUT = r"C:/项目/老田的AI助手/quant_backtest/reports/_p67_factor_ic.json"
START, END = 20251105, 20261105
HOLD = 5
MIN_BARS = 250


def load_wide():
    files = sorted(glob.glob(os.path.join(DATA, "*.parquet")))
    close_d, open_d, low_d, high_d, amt_d, vol_d, pct_d = {}, {}, {}, {}, {}, {}, {}
    for i, fp in enumerate(files):
        try:
            df = pd.read_parquet(fp, columns=["trade_date", "open", "close", "high",
                                              "low", "volume", "amount", "pct_chg"])
        except Exception:
            continue
        if len(df) < MIN_BARS:
            continue
        code = os.path.basename(fp).split(".")[0]
        df = df.sort_values("trade_date")
        d = df["trade_date"].to_numpy()
        close_d[code] = pd.Series(df["close"].to_numpy(), index=d)
        open_d[code] = pd.Series(df["open"].to_numpy(), index=d)
        low_d[code] = pd.Series(df["low"].to_numpy(), index=d)
        high_d[code] = pd.Series(df["high"].to_numpy(), index=d)
        amt_d[code] = pd.Series(df["amount"].to_numpy(), index=d)
        vol_d[code] = pd.Series(df["volume"].to_numpy(), index=d)
        pct_d[code] = pd.Series(df["pct_chg"].to_numpy(dtype=float), index=d)
        if (i + 1) % 1000 == 0:
            print("  loaded %d/%d" % (i + 1, len(files)), flush=True)

    def _df(dic):
        return pd.DataFrame(dic).sort_index()

    close, open_, low, high, amt, vol, pct = (
        _df(x) for x in (close_d, open_d, low_d, high_d, amt_d, vol_d, pct_d))
    print("  wide shape", close.shape, flush=True)
    return close, open_, low, high, amt, vol, pct


def pct_ret(close):
    return close.pct_change()


def main():
    close, open_, low, high, amt, vol, pct = load_wide()
    # 复权：pct_chg 已含除权处理，累乘得到真实净值；再反推复权因子
    nav = (1 + pct.fillna(0) / 100.0).cumprod()
    adj = nav / close
    c = close * adj
    o = open_ * adj
    hi = high * adj
    lo = low * adj
    r1 = pct.fillna(0) / 100.0
    r1 = r1.where(vol > 0)

    fac = {}
    fac["mom20"] = c / c.shift(20) - 1
    fac["mom60"] = c / c.shift(60) - 1
    fac["rev5"] = -(c / c.shift(5) - 1)
    fac["vol20"] = r1.rolling(20).std()
    fac["ampl20"] = ((hi - lo) / c).rolling(20).mean()
    fac["illiq20"] = (r1.abs() / amt.replace(0, np.nan)).rolling(20).mean()
    fac["turn20"] = (vol / vol.rolling(60).mean())
    fac["skew20"] = r1.rolling(20).skew()
    fac["maxret20"] = r1.rolling(20).max()
    fac["dist_ma20"] = c / c.rolling(20).mean() - 1
    fac["hi52"] = c / c.rolling(250, min_periods=200).max() - 1

    # 未来收益：T+1 开盘 → T+5 收盘
    fwd = (c.shift(-HOLD) / o.shift(-1) - 1.0)

    dates = [d for d in c.index if START <= int(d) <= END]
    print("  test dates", len(dates), flush=True)

    res = {}
    for name, f in fac.items():
        ics, ns, spread = [], 0, []
        for d in dates:
            x = f.loc[d]
            y = fwd.loc[d]
            m = x.notna() & y.notna()
            if m.sum() < 100:
                continue
            xv, yv = x[m], y[m]
            # Spearman = 秩相关，避免量纲与极值影响
            ic = np.corrcoef(xv.rank(), yv.rank())[0, 1]
            if np.isfinite(ic):
                ics.append((int(d), ic))
                ns += int(m.sum())
            # 五分组多空：最高组 − 最低组 的 T+5 收益差
            try:
                q = pd.qcut(xv.rank(method="first"), 5, labels=False)
                hi_y = yv[q == 4].mean()
                lo_y = yv[q == 0].mean()
                if np.isfinite(hi_y) and np.isfinite(lo_y):
                    spread.append(float(hi_y - lo_y))
            except Exception:
                pass
        if len(ics) < 20:
            continue
        _ds = [x[0] for x in ics]
        arr = np.array([x[1] for x in ics])
        mu, sd = float(arr.mean()), float(arr.std(ddof=1))
        _cut = len(arr) // 2
        res[name] = {
            "ic_mean": round(mu, 4),
            "ic_std": round(sd, 4),
            "icir": round(mu / sd, 3) if sd > 0 else None,
            "t": round(mu / sd * np.sqrt(len(arr)), 2) if sd > 0 else None,
            "pos_rate": round(float((arr > 0).mean()), 3),
            "ls_spread": round(float(np.mean(spread)), 6) if spread else None,
            "ic_is": round(float(arr[:_cut].mean()), 4),
            "ic_oos": round(float(arr[_cut:].mean()), 4),
            "n_days": len(arr),
            "n_obs": ns,
        }

    # 分 5 组看单调性（用全样本聚合，按日截面分位）
    print()
    print("%-11s %9s %8s %8s %7s %10s %9s %9s %s" % (
        "因子", "IC均值", "ICIR", "t", "正比例", "多空spread", "IS-IC", "OOS-IC", "判定"))
    ranked = sorted(res.items(), key=lambda kv: -abs(kv[1]["ic_mean"]))
    for k, v in ranked:
        # 方向取反后仍达标即为候选：负 IC 取反等价
        ok = abs(v["ic_mean"]) > 0.02 and abs(v["icir"] or 0) > 0.3
        same = (v["ic_is"] * v["ic_oos"]) > 0
        direction = "正向" if v["ic_mean"] > 0 else "反向"
        verdict = ("候选(%s)%s" % (direction, "" if same else "·符号翻转")) if ok else "淘汰"
        print("%-11s %9.4f %8.3f %8.2f %7.1f%% %10s %9.4f %9.4f %s" % (
            k, v["ic_mean"], v["icir"], v["t"], v["pos_rate"] * 100,
            "—" if v["ls_spread"] is None else "%.3f%%" % (v["ls_spread"] * 100),
            v["ic_is"], v["ic_oos"], verdict))

    json.dump({"hold": HOLD, "window": [START, END], "factors": res},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print("\n-> %s" % OUT)


if __name__ == "__main__":
    sys.exit(main())
