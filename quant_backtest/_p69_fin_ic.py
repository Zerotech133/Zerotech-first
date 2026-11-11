# -*- coding: utf-8 -*-
"""P69 质量因子 IC 筛查（全市场，零前视）。

三个必须处理的口径陷阱（不处理则结论必然虚高）：
  1) 前视偏差：财报必须用「公告日」对齐，不是报告期。数据源自带最新公告日期。
  2) 报告期混合：ROE 是累计口径（Q1 中位 1.10 vs 年报 4.60），同一截面里
     混着不同报告期直接排名 = 谁年报谁第一。解法：日内按报告期分组，组内取
     分位（rank pct），组间才可比。
  3) 自相关：财报一年只变 4 次，日频 IC 几十次用的是同一份数据，t 值虚高。
     判据以「月度聚合 IC」为主（每月 1 个观测），日频仅作参考。

未来收益口径与 _p67 / trade_returns 一致：T+1 开盘 → T+5 收盘。
"""
import glob
import json
import os

import numpy as np
import pandas as pd

DATA = r"C:/项目/老田的AI助手/quant_backtest/data/ashare"
CACHE = r"C:/项目/老田的AI助手/quant_backtest/reports/_p69_fin_cache"
OUT = r"C:/项目/老田的AI助手/quant_backtest/reports/_p69_fin_ic.json"

START, END = 20240102, 20261105
HOLD = 5
MIN_BARS = 250
MIN_XS = 100          # 日截面最少有效只数，低于则当日不算 IC
MIN_GROUP = 30        # 同报告期组最少只数，低于则剔除（防孤立组合成伪满分）
STEP = 5              # 每 N 个交易日取一个快照（财报一年才变 4 次，日频无意义）
COST = 0.0030         # 往返成本 0.30%，判据线

FACTORS = ["roe", "gross_margin", "net_margin", "cf_ratio", "np_yoy", "rev_yoy"]


# ---------------- 价格 ----------------
def fin_code_to_full(c):
    """6 位代码 -> 价格宽表的列名（含交易所后缀）。

    后缀不能省：000001_SH 是上证指数、000001_SZ 才是平安银行，
    去掉后缀两者会串成一只。板块规则：6/9→沪，0/3/2→深，4/8→北。
    """
    if not isinstance(c, str) or len(c) != 6 or not c.isdigit():
        return None
    if c[0] in "69":
        return c + "_SH"
    if c[0] in "032":
        return c + "_SZ"
    if c[0] in "48":
        return c + "_BJ"
    return None


PXCACHE = r"C:/项目/老田的AI助手/quant_backtest/reports/_p69_px_cache"


def load_px():
    """读全市场价格宽表。首次约 8 分钟，落缓存后秒级（调试必用）。"""
    if os.path.exists(PXCACHE):
        got = {}
        for k in ("open", "close", "pct", "vol"):
            fp = os.path.join(PXCACHE, k + ".parquet")
            df = pd.read_parquet(fp)
            # 原始 trade_date 可能是字符串，index 不统一会让后续 reindex 全部落空
            # （曾导致 fwd_w 全 NaN、base 长表 0 行而不报错）
            if df.index.dtype != "int64":
                df.index = pd.Index(df.index.astype("int64"))
            got[k] = df
        print("     px from cache", got["close"].shape, flush=True)
        return got["open"], got["close"], got["pct"], got["vol"]

    files = sorted(glob.glob(os.path.join(DATA, "*.parquet")))
    o_d, c_d, p_d, v_d = {}, {}, {}, {}
    for fp in files:
        try:
            df = pd.read_parquet(fp, columns=["trade_date", "close", "open",
                                              "pct_chg", "volume"])
        except Exception:
            continue
        if len(df) < MIN_BARS:
            continue
        # 保留市场后缀（000001_SH / 000001_SZ 是两只不同的标的）
        code = os.path.basename(fp).split(".")[0]
        df = df.sort_values("trade_date")
        idx = df["trade_date"].to_numpy()
        o_d[code] = pd.Series(df["open"].to_numpy(), index=idx)
        c_d[code] = pd.Series(df["close"].to_numpy(), index=idx)
        p_d[code] = pd.Series(df["pct_chg"].to_numpy(dtype=float), index=idx)
        v_d[code] = pd.Series(df["volume"].to_numpy(dtype=float), index=idx)
    o = pd.DataFrame(o_d).sort_index()
    c = pd.DataFrame(c_d).sort_index()
    p = pd.DataFrame(p_d).sort_index()
    v = pd.DataFrame(v_d).sort_index()
    for _df in (o, c, p, v):
        if _df.index.dtype != "int64":
            _df.index = pd.Index(_df.index.astype("int64"))
    os.makedirs(PXCACHE, exist_ok=True)
    for k, val in (("open", o), ("close", c), ("pct", p), ("vol", v)):
        val.astype("float64").to_parquet(os.path.join(PXCACHE, k + ".parquet"))
    print("     px cached", c.shape, flush=True)
    return o, c, p, v


# ---------------- 财报 ----------------
def load_fin():
    frames = []
    for fp in sorted(glob.glob(os.path.join(CACHE, "*.parquet"))):
        frames.append(pd.read_parquet(fp))
    if not frames:
        raise SystemExit("缓存为空，先跑 _p69_fetch_fin.py")
    df = pd.concat(frames, ignore_index=True)
    for col in ("eps", "revenue", "rev_yoy", "np", "np_yoy", "bps", "roe",
                "cfps", "gross_margin"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # 派生：净利率与现金流回报率（比率，跨股可比；绝对额如 eps/bps 不可比）
    with np.errstate(divide="ignore", invalid="ignore"):
        df["net_margin"] = np.where(df["revenue"] > 0, df["np"] / df["revenue"], np.nan)
        df["cf_ratio"] = np.where(df["bps"] > 0, df["cfps"] / df["bps"], np.nan)
    # 同比类先截断：p1/p99 达 ±1300%，不截会把 IC 变成「极端值相关」
    for col in ("np_yoy", "rev_yoy"):
        df[col] = df[col].clip(-200.0, 200.0)
    df["avail"] = df["ann_date"].dt.strftime("%Y%m%d").astype(float)
    df = df.dropna(subset=["avail"])
    # 6 位代码 -> 带交易所后缀，才能与价格宽表对齐
    df["full"] = df["code"].map(fin_code_to_full)
    df = df[df["full"].notna()]
    # 一份财报若公告日晚于报告期太久（补披露），照样按公告日可用，不做额外处理
    return df


def build_panel(fin, dates, codes):
    """把财报按「公告日 ≤ T 的最新一期」映射到交易日面板。

    返回 dict: factor -> DataFrame(index=date, columns=code)
              period -> DataFrame(index=date, columns=code) 报告期
    """
    darr = np.array(dates, dtype=float)
    out = {f: np.full((len(dates), len(codes)), np.nan) for f in FACTORS}
    per = np.full((len(dates), len(codes)), np.nan)
    cpos = {c: i for i, c in enumerate(codes)}

    sub_all = fin[fin["full"].isin(cpos)]
    for code, g in sub_all.groupby("full", sort=False):
        # 按报告期升序，并强制公告日单调不减：
        # 极少数补披露会让「年报」的公告日晚于「一季报」，若不处理，
        # 面板会被旧报告期覆盖新报告期（A2 自检的 174 处倒退）。
        # cummax 的代价是少数票延迟可见，但绝不会用旧数据盖新数据。
        g = g.sort_values("report_period")
        a = np.maximum.accumulate(g["avail"].to_numpy(dtype=float))
        # 每个交易日取「公告日 <= T」的最后一条
        pos = np.searchsorted(a, darr, side="right") - 1
        ok = pos >= 0
        if not ok.any():
            continue
        rows = np.where(ok)[0]
        idx = pos[ok]
        j = cpos[code]
        per[rows, j] = g["report_period"].to_numpy()[idx]
        for f in FACTORS:
            out[f][rows, j] = g[f].to_numpy()[idx]
    res = {f: pd.DataFrame(v, index=dates, columns=codes) for f, v in out.items()}
    res["period"] = pd.DataFrame(per, index=dates, columns=codes)
    return res


def xs_rank(df, period, min_group=MIN_GROUP):
    """日内按报告期分组取分位（0~1），消除累计口径与混合期的水平差。

    min_group 必须设：截面里孤立的报告期组（例如只有 1 只票还在用去年年报），
    组内 rank 必然是 1.0 —— 等于凭空给它满分，与基本面无关。这类伪信号
    必须剔除，否则「小样本期」的票会系统性占据多空两端。
    """
    grp = period.stack()
    x = df.stack()
    grp = grp[grp.notna()]
    x = x[x.notna()]
    common = x.index.intersection(grp.index)
    x, grp = x.loc[common], grp.loc[common]
    g = x.groupby([x.index.get_level_values(0), grp])
    rk = g.rank(pct=True)
    sz = g.transform("size")
    return rk[sz >= min_group]


def winsorize(df, lo=0.01, hi=0.99):
    """按日截面缩尾，压制极端值对秩相关的影响（秩相关本身稳健，但仍防单点主导）。"""
    q_lo = df.quantile(lo, axis=1)
    q_hi = df.quantile(hi, axis=1)
    return df.clip(lower=q_lo, upper=q_hi, axis=0)


def _day_stats(s):
    """单日截面：Spearman IC 与多空 spread。

    注意必须是 rank(x) 与 rank(y) 的相关。只给 x 取秩、y 用原始收益，
    算出来是 Pearson 混合式，会被极端收益压小（P67 同窗口对照曾差 4.5 倍）。
    """
    if len(s) < MIN_XS:
        return pd.Series({"ic": np.nan, "sp": np.nan})
    ic = s["rk"].corr(s["y"].rank())
    hi = s.loc[s["rk"] > 0.8, "y"]
    lo = s.loc[s["rk"] <= 0.2, "y"]
    sp = (hi.mean() - lo.mean()) if (len(hi) >= 20 and len(lo) >= 20) else np.nan
    return pd.Series({"ic": ic, "sp": sp})


def main():
    print("[1/4] 读价格 ...", flush=True)
    o, c, p, v = load_px()
    print("     px shape", c.shape, flush=True)

    # 复权（与 _p67 一致：pct_chg 累乘净值反推因子）
    nav = (1 + p.fillna(0) / 100.0).cumprod()
    adj = nav / c
    c_adj = c * adj
    o_adj = o * adj
    fwd = c_adj.shift(-HOLD) / o_adj.shift(-1) - 1.0     # T+1 开 → T+5 收
    # 必须显式转 bool：v>0 在含 NaN 时会升成 object，reindex 再引入 NaN 后
    # 与 bool Series 做 & 会把整个 mask 变成 object，sum() 恒为 0（曾因此全部无样本）
    tradable = (v > 0).fillna(False).astype(bool)

    print("[2/4] 读财报 ...", flush=True)
    fin = load_fin()
    print("     fin rows %d, codes %d, periods %d" % (
        len(fin), fin["full"].nunique(), fin["report_period"].nunique()), flush=True)

    print("[3/4] 构造可见日面板 ...", flush=True)
    all_dates = [int(d) for d in c.index if START <= int(d) <= END]
    # 采样：财报一年只变 4 次，687 个交易日里同一份财报被重复使用几十次，
    # 既慢又制造伪独立观测。每 STEP 个交易日取一个快照，约 137 个观测。
    dates = all_dates[::STEP]
    # 集合必须预先算好：写成 `if x in set(fin["full"])` 会每次迭代重建
    # 一个十万级字符串集合，5443 次迭代直接跑不完（实测卡死 600 秒）
    fin_set = set(fin["full"].tolist())
    codes = [x for x in c.columns if x in fin_set]
    print("     test dates %d (采样自 %d), codes %d" % (
        len(dates), len(all_dates), len(codes)), flush=True)
    pan = build_panel(fin, dates, codes)
    period = pan.pop("period")
    fwd_w = fwd.reindex(index=dates, columns=codes)
    trd_w = tradable.reindex(index=dates, columns=codes, fill_value=False)

    cov = period.notna().sum(axis=1)
    print("     有效只数 中位 %d  min %d  max %d" % (
        int(cov.median()), int(cov.min()), int(cov.max())), flush=True)

    print("[4/4] 算 IC ...", flush=True)
    # 长表化一次：原实现按日切片 687 天 × 6 因子 = 4122 次 stack，跑到超时。
    # 改为整个面板 stack 一次，6 个因子合并成一次 join、一次分组排名。
    per_l = period.stack().rename("rp")
    per_l = per_l[per_l.notna()]
    y_l = fwd_w.stack().rename("y")
    tr_l = trd_w.stack().rename("trd")
    base = pd.concat([per_l, y_l, tr_l], axis=1)
    base = base[base["y"].notna() & base["trd"].fillna(False).astype(bool)]
    print("     base 长表 %d 行" % len(base), flush=True)

    xcols = {}
    for f in FACTORS:
        xcols[f] = winsorize(pan[f]).stack().rename(f)
    X = pd.concat(xcols.values(), axis=1)
    d = base.join(X, how="inner").reset_index()
    d.columns = ["date", "code", "rp", "y", "trd"] + FACTORS
    d = d.drop(columns=["trd"])
    # 组内分位：同一天内按报告期分组，消除累计口径与混合期的水平差
    g = d.groupby(["date", "rp"], sort=False)
    d["n_grp"] = g[FACTORS[0]].transform("size")
    rk = g[FACTORS].rank(pct=True)
    d[FACTORS] = rk
    d = d[d["n_grp"] >= MIN_GROUP].copy()
    print("     长表 %d 行，%d 个快照日" % (len(d), d["date"].nunique()), flush=True)

    res = {}
    for f in FACTORS:
        dd = d[["date", "y", f]].rename(columns={f: "rk"}).dropna()
        if dd.empty:
            res[f] = {"error": "无有效数据"}
            continue
        agg = dd.groupby("date").apply(_day_stats, include_groups=False)
        agg = agg.dropna(subset=["ic"])
        if len(agg) < 20:
            res[f] = {"error": "样本不足", "n_days": int(len(agg))}
            continue

        arr = agg["ic"].to_numpy(dtype=float)
        spreads = [float(x) for x in agg["sp"].dropna().to_numpy()]
        ics = [int(x) for x in agg.index]
        # 日频统计（自相关，仅参考）
        mu, sd = float(arr.mean()), float(arr.std(ddof=1))
        # 月度聚合：每月 1 个观测 → 主判据
        s = pd.Series(arr, index=pd.Index(ics))
        mser = s.groupby([str(i)[:6] for i in s.index]).mean()
        mmu, msd = float(mser.mean()), float(mser.std(ddof=1))
        cut = len(mser) // 2
        res[f] = {
            "ic_mean": round(mu, 4),
            "icir_daily": round(mu / sd, 3) if sd > 0 else None,
            "t_daily": round(mu / sd * np.sqrt(len(arr)), 2) if sd > 0 else None,
            "n_days": int(len(arr)),
            "n_months": int(len(mser)),
            "ic_m_mean": round(mmu, 4),
            "ic_m_std": round(msd, 4),
            "t_monthly": round(mmu / msd * np.sqrt(len(mser)), 2) if msd > 0 else None,
            "pos_rate_m": round(float((mser > 0).mean()), 3),
            "ic_is": round(float(mser.iloc[:cut].mean()), 4),
            "ic_oos": round(float(mser.iloc[cut:].mean()), 4),
            "ls_spread": round(float(np.mean(spreads)), 6) if spreads else None,
            "ls_hit": round(float(np.mean([x > 0 for x in spreads])), 3) if spreads else None,
            "months": {k: round(float(x), 4) for k, x in mser.items()},
        }

    print()
    print("%-13s %8s %8s %10s %8s %8s %9s %8s" % (
        "因子", "IC日频", "t日频", "IC月度", "t月度", "正比例", "多空spread", "判定"))
    for k, r0 in sorted(res.items(), key=lambda kv: -abs(kv[1].get("ic_m_mean") or 0)):
        if "error" in r0:
            print("%-13s %s %s" % (k, r0["error"], r0.get("n_days", "")))
            continue
        ok = abs(r0["ic_m_mean"]) > 0.02 and abs(r0["t_monthly"] or 0) > 2.0
        same = (r0["ic_is"] * r0["ic_oos"]) > 0
        sp = r0["ls_spread"]
        beat = sp is not None and abs(sp) > COST
        verdict = ("候选" if ok else "淘汰")
        if ok and not same:
            verdict += "·符号翻转"
        if ok and not beat:
            verdict += "·不抵成本"
        print("%-13s %8.4f %8.2f %10.4f %8.2f %8.1f%% %9s %8s" % (
            k, r0["ic_mean"], r0["t_daily"], r0["ic_m_mean"], r0["t_monthly"],
            r0["pos_rate_m"] * 100,
            "—" if sp is None else "%.3f%%" % (sp * 100), verdict))

    json.dump({"window": [START, END], "hold": HOLD, "cost": COST,
               "n_dates": len(dates), "n_codes": len(codes),
               "cov_median": int(cov.median()), "factors": res},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print("\n-> %s" % OUT)


if __name__ == "__main__":
    main()
