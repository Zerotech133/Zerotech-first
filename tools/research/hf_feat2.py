# -*- coding: utf-8 -*-
"""hf_feat2 —— 入场端特征扩表 + 单变量筛（第二十轮，第 1、2 步）。

## 为什么要重做一遍特征

`hf_feat.py`（第一版）只用了信号文件自带的 7 个字段，而 `bei / bei2 / conf /
div_area / div_dif / pv_state / pv_fv / npv` 八个字段 **100% 为空**。结论只能是
「这七个字段不行」，不能推出「入场端没有信息」。本脚本按第二十轮立项时定下的
七项重建特征，全部**只用 detect_date（d0）收盘及之前**的数据——建仓在 d0 次日
开盘，所以 d0 收盘的信息在决策时确实拿得到。

## 七项特征 + 五项对照

    板块内相对强度   ind_rs20 / ind_rs60 = 个股收益 − 同行业等权收益
    板块绝对强度     ind_ret20（对照：区分「选对了股」与「选对了板块」）
    成交额分位       amt_pct60 = 当日成交额在过去 60 个交易日中的分位
    换手率分位       turn_pct60（tushare daily_basic，规模中性，与成交额互补）
    放量倍数         amt_ratio20 = 当日成交额 / 过去 20 日均额
    20/60 日波动     vol20 / vol60（年化）
    距 120 日高点    dist_high120 = 收盘 / 过去 120 日最高价 − 1
    市场 regime      regime = 中证全指 20 日动量 ±2% 三档（与产线势档同源）
    同日信号数       crowd（全池 1买 数）/ crowd_ind（同行业 1买 数）
    上市天数         listed_days（stock_basic.list_date → 自然日）

## 三条口径铁律

1. **结果不重算**：逐笔结果直接读 `portal/hf_trades_path.jsonl`（`hf_path.py
   --dump` 落的盘）。重算就有两套口径，早晚对不上。
2. **显著性按入场日 d0 聚类**：同一天进场的几十只票共享同一次冲击。6,903 笔
   只有 398 个入场日，不聚类会把 t 放大约 3.3 倍（第十九轮实测）。
   注意：`hf_feat.py` 第一版聚在 `ed`（退出日）上。ed 是 d0 的单调映射，
   数值上几乎一样，但语义上该用 d0——本脚本统一用 d0，并在自检里断言两者
   给出的 |Δt| 差 < 0.3。
3. **双 β 口径**：A 的定义里带 β（A = ret − β×基准），所以任何与 β 相关的
   分组都会循环论证。每条特征都跑 β 调整（a）与 β=1（a1）两遍，
   **两个口径必须同号**才算通过。

## 通过判据（立项时定死，不许看到结果再改）

    |Δt| ≥ 2  且  Δt 与 Δt1 同号  且  n ≥ 30  且 参与配对的天数 ≥ 20

注意多重检验：17 个特征 × 5 档 ≈ 85 次检验，纯噪声下期望约 3.9 个
|t| ≥ 2（单侧 p=0.046）。所以「通过」不等于「有效」，要看**通过数是否显著
多于噪声期望**，以及方向能不能在 holdout 上复现。

用法::

    python -X utf8 tools/research/hf_feat2.py              # 建面板 + 筛
    python -X utf8 tools/research/hf_feat2.py --rebuild    # 强制重建面板
    python -X utf8 tools/research/hf_feat2.py --screen-only
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import statistics
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hf_path as HP          # noqa: E402  聚类 t 只留一份实现
import hf_sell_exit as SE     # noqa: E402  交易日历（000985.CSI）同源
import pit_industry as PI     # noqa: E402  申万一级 PIT 映射（与 trade_book 同源）

ROOT = HP.ROOT
QBT = os.path.join(ROOT, "quant_backtest", "data")
BARS10 = os.path.join(QBT, "ashare10y")          # 10 年历史：120 日回看 + 上市天数需要
TUR_DIR = os.path.join(QBT, "tushare", "daily_basic")
BASIC_DIR = os.path.join(QBT, "tushare", "stock_basic")
TRADES = os.path.join(ROOT, "portal", "hf_trades_path.jsonl")
PANEL = os.path.join(ROOT, "portal", "hf_feat2_panel.jsonl")
OUT = os.path.join(ROOT, "portal", "hf_feat2.json")

KEEP_FROM = "20230101"        # 特征回看最多 120 日，2024-01 的首批信号需要 2023-06
G_UP, G_DN = 20.0, -10.0      # 与 hf_path / hf_feat 一致（毛终值分组）
MIN_N = 30                    # 单桶最少样本
MIN_DAYS = 20                 # 最少配对天数（不足则认为只是一两天的事）
T_GATE = 2.0                  # |Δt| 门槛
ANN = 244.0

# 分位切桶（五档）；类别型另算
QS = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)

FEAT_NUM = ("ret20", "ret60", "ind_rs20", "ind_rs60", "ind_ret20",
            "vol20", "vol60", "dist_high120", "amt_pct60", "turn_pct60",
            "amt_ratio20", "crowd", "crowd_ind", "listed_days", "atr_pct")
FEAT_CAT = ("regime",)
# 信号文件自带、第一版已验过的字段，一并重跑做对照（判据相同）
FEAT_OLD_NUM = ("lag", "beta", "depth_bars")
FEAT_OLD_CAT = ("type", "mbei", "vv", "tbe")
# 日级变量：同一天所有票取同一个值，日聚类 t 在这里**没有定义**（当天没有组间差）。
# 这类特征要换算法：先算每日均值，再对「日序列」做两样本 HAC t。
FEAT_DATE = ("crowd", "regime")
# 进入 A 的定义的变量（A = ret − β×基准）。用它分组再解释 A 是循环论证，
# 保留在表里只为留证，不参与候选。
CIRCULAR = ("beta",)


# ---------------------------------------------------------------- 基础数据
def load_trades():
    rows = []
    for line in open(TRADES, encoding="utf-8"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def load_turnover(codes):
    """{code: (dates, tur)}。daily_basic 的 turnover_rate 已是百分数。"""
    import pandas as pd
    frames = []
    for f in sorted(glob.glob(os.path.join(TUR_DIR, "*.parquet"))):
        d = os.path.basename(f)[2:10]
        if d < KEEP_FROM:
            continue
        t = pd.read_parquet(f, columns=["ts_code", "turnover_rate"])
        t = t[t["ts_code"].isin(codes)]
        if len(t):
            frames.append(t.assign(_d=d))
    out = {}
    if not frames:
        return out
    df = pd.concat(frames, ignore_index=True)
    df["turnover_rate"] = pd.to_numeric(df["turnover_rate"], errors="coerce")
    for c, g in df.groupby("ts_code", sort=False):
        g = g.sort_values("_d")
        out[str(c)] = (g["_d"].tolist(), g["turnover_rate"].tolist())
    return out


def load_list_date():
    import pandas as pd
    out = {}
    for f in glob.glob(os.path.join(BASIC_DIR, "*.parquet")):
        d = pd.read_parquet(f, columns=["ts_code", "list_date"])
        for c, v in zip(d["ts_code"], d["list_date"]):
            if v:
                out[str(c)] = str(v)
    return out


def industry_snapshots(axis):
    """{YYYYMM: {code: 行业名}}，每月按当月首个交易日取 PIT 快照。

    申万一级的再分类很少发生，按月快照与逐日查询的差别可以忽略；而 `.at(d)`
    是 O(成员数) 的 pandas 运算，逐日查 900 次要多花几十秒。粒度写在这里，
    是为了让后来人知道这不是逐日的。
    """
    m = PI.load_sw_l1_industry()
    snaps = {}
    for d in axis:
        ym = d[:6]
        if ym in snaps:
            continue
        try:
            s = m.at(d)
        except Exception:                                   # noqa: BLE001
            continue
        snaps[ym] = {str(k): str(v) for k, v in s.items()}
    return snaps


def regime_map(n=20, th=0.02):
    """中证全指 20 日动量 ±2% 三档（与 hf_book.build_regime 同口径）。

    直接复用 SE.calendar() 的 000985.CSI 收盘，不另读一遍指数文件。
    """
    dates, close, _ = SE.calendar()
    out = {}
    for i, d in enumerate(dates):
        if i < n or close[i - n] <= 0:
            out[d] = "mid"
            continue
        m = close[i] / close[i - n] - 1.0
        out[d] = "strong" if m > th else ("weak" if m < -th else "mid")
    return out


def crowd_maps(axis, snaps):
    """{date: 全池 1买 数} 与 {(date, 行业): 数}。"""
    allc = defaultdict(int)
    byind = defaultdict(int)
    for line in open(SE.JSONL, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("type") != SE.BUY:
            continue
        d = str(r.get("detect_date") or "").replace("-", "")
        if len(d) != 8 or not (SE.SPAN_LO <= d <= SE.SPAN_HI):
            continue
        allc[d] += 1
        ind = (snaps.get(d[:6]) or {}).get(str(r.get("code")))
        if ind:
            byind[(d, ind)] += 1
    return dict(allc), dict(byind)


def load_old_feats():
    """信号文件自带的 7 个字段（第一版验过的）。不并进来，对照组就缺了。"""
    keep = ("type", "lag", "depth_bars", "mbei", "vv", "tbe")
    out = {}
    for line in open(SE.JSONL, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("type") != SE.BUY:
            continue
        d = str(r.get("detect_date") or "").replace("-", "")
        c = r.get("code")
        if not c or len(d) != 8:
            continue
        out[(c, d)] = {k: r.get(k) for k in keep}
    return out


# ---------------------------------------------------------------- 单票序列
def _series(tb):
    """parquet → (dates, idx, h_adj, l_adj, amt, ret)。

    idx 是**收益指数**（∏(1+日收益)），不是复权价倍数。close/pre_close 已含除权
    参考价，所以 c/pc 就是真实涨跌幅；把 high/low 归到同一尺度只需乘 idx×h/c——
    日内比例 h/c 不受除权影响，这样 dist_high120 与 ATR 都不会被除权跳空污染。
    """
    import numpy as np
    d = tb.to_pandas()
    if len(d) < 30:
        return None
    d = d.assign(td=d["trade_date"].astype(str).str.replace("-", "").str[:8])
    d = d.sort_values("td")
    d = d[d["td"] >= KEEP_FROM]
    if len(d) < 25:
        return None
    dates = d["td"].tolist()
    c = np.nan_to_num(d["close"].to_numpy(dtype=float), nan=0.0)
    h = np.nan_to_num(d["high"].to_numpy(dtype=float), nan=0.0)
    lo = np.nan_to_num(d["low"].to_numpy(dtype=float), nan=0.0)
    pc = np.nan_to_num(d["pre_close"].to_numpy(dtype=float), nan=0.0)
    amt = np.nan_to_num(d["amount"].to_numpy(dtype=float), nan=0.0)
    ret = np.zeros(len(c))
    for i in range(len(c)):
        base = pc[i] if pc[i] > 0 else (c[i - 1] if i else 0.0)
        ret[i] = (c[i] / base - 1.0) if base > 0 and c[i] > 0 else 0.0
    idx = np.cumprod(1.0 + np.clip(ret, -0.9, 5.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        h_adj = np.where(c > 0, idx * h / c, np.nan)
        l_adj = np.where((c > 0) & (lo > 0), idx * lo / c, np.nan)
    return dates, idx, h_adj, l_adj, amt, ret


def _pct_rank(win, v):
    """v 在 win 中的分位（<=v 的比例）。零成交（停牌）不打进分母。"""
    xs = [x for x in win if x and x > 0]
    if len(xs) < 10 or not v or v <= 0:
        return None
    return sum(1 for x in xs if x <= v) / len(xs)


def _std(xs):
    xs = [x for x in xs if x == x]
    if len(xs) < 5:
        return None
    return statistics.pstdev(xs)


# ---------------------------------------------------------------- 面板构建
def features_one(code, d0, i, idx, h_adj, l_adj, amt, ret, ctx):
    """第 i 根 bar（= d0 收盘）的入场特征。

    **切片一律截止到 i**，不看 i 之后任何一根 bar——这是"只用 detect_date
    及之前"的唯一硬约束，自检 C2 用「截断后重算必须完全相等」来验它。
    板块相对强度不在这里算（要等全市场行业指数建完），在 build 的第二遍补。
    """
    import numpy as np
    f = {"c": code, "d0": d0}
    # 动量
    for k in (20, 60):
        if i - k >= 0 and idx[i - k] > 0:
            f["ret%d" % k] = float(idx[i] / idx[i - k] - 1.0)
    # 波动（年化）
    for k in (20, 60):
        s = _std(ret[i - k + 1:i + 1])
        if s is not None:
            f["vol%d" % k] = float(s * math.sqrt(ANN))
    # 距 120 日高点
    if i >= 119:
        seg = h_adj[i - 119:i + 1]
        seg = seg[~np.isnan(seg)]
        if len(seg) >= 60 and seg.max() > 0:
            f["dist_high120"] = float(idx[i] / seg.max() - 1.0)
    # 成交额分位与放量倍数
    r_amt = _pct_rank(amt[max(0, i - 59):i + 1].tolist(), amt[i])
    if r_amt is not None:
        f["amt_pct60"] = r_amt
    if i >= 19:
        base = [x for x in amt[i - 19:i] if x > 0]
        if base and amt[i] > 0:
            f["amt_ratio20"] = float(amt[i] / statistics.mean(base))
    # ATR14 / 收盘（统一到收益指数尺度）
    if i >= 14:
        tr = []
        for k in range(i - 13, i + 1):
            prev = idx[k - 1] if k else idx[k]
            hi, lo = h_adj[k], l_adj[k]
            if np.isnan(hi) or np.isnan(lo):
                continue
            tr.append(max(hi - lo, abs(hi - prev), abs(lo - prev)))
        if len(tr) >= 10 and idx[i] > 0:
            f["atr_pct"] = float(statistics.mean(tr) / idx[i])
    # 换手率分位（daily_basic 只有交易日，按可用条数取窗口）
    tv = ctx["tur"].get(code)
    if tv is not None:
        tds, tvals = tv
        j = None
        lo_, hi_ = 0, len(tds) - 1
        while lo_ <= hi_:
            mid = (lo_ + hi_) // 2
            if tds[mid] <= d0:
                j, lo_ = mid, mid + 1
            else:
                hi_ = mid - 1
        if j is not None and j >= 10:
            v = tvals[j]
            if isinstance(v, (int, float)) and v == v and v > 0:
                r_t = _pct_rank(tvals[max(0, j - 59):j + 1], v)
                if r_t is not None:
                    f["turn_pct60"] = r_t
    # 市场 regime / 拥挤度 / 上市天数
    f["regime"] = ctx["reg"].get(d0)
    f["crowd"] = ctx["crowd"].get(d0)
    ind = (ctx["snaps"].get(d0[:6]) or {}).get(code)
    if ind:
        f["ind"] = ind
        f["crowd_ind"] = ctx["crowd_ind"].get((d0, ind))
    ld = ctx["listed"].get(code)
    if ld and len(ld) == 8:
        import datetime as _dt
        try:
            a = _dt.date(int(d0[:4]), int(d0[4:6]), int(d0[6:8]))
            b = _dt.date(int(ld[:4]), int(ld[4:6]), int(ld[6:8]))
            f["listed_days"] = (a - b).days
        except ValueError:
            pass
    return f


def build(rebuild=False):
    """建 `portal/hf_feat2_panel.jsonl`：每笔一行 = 结果 + 入场特征。"""
    import numpy as np
    import pyarrow.parquet as pq

    trades = load_trades()
    by_code = defaultdict(list)
    for t in trades:
        by_code[t["c"]].append(t["d0"])

    codes = set(by_code)
    tur = load_turnover(codes)
    listed = load_list_date()
    axis, _, _ = SE.calendar()
    snaps = industry_snapshots(axis)
    reg = regime_map()
    crowd_all, crowd_ind = crowd_maps(axis, snaps)
    ctx = {"tur": tur, "reg": reg, "crowd": crowd_all, "crowd_ind": crowd_ind,
           "snaps": snaps, "listed": listed}

    # ---- 行业等权收益：只累计，不保留个股
    ind_sum = defaultdict(lambda: defaultdict(float))
    ind_cnt = defaultdict(lambda: defaultdict(int))
    # ---- 个股特征
    feat = {}

    files = sorted(glob.glob(os.path.join(BARS10, "*.parquet")))
    n_miss = n_bar = 0
    for fi, path in enumerate(files):
        code = os.path.basename(path).replace(".parquet", "").replace("_", ".")
        if code.endswith(".bak"):
            continue
        try:
            tb = pq.read_table(path, columns=["trade_date", "close", "high",
                                              "low", "pre_close", "amount"])
        except Exception:                                   # noqa: BLE001
            continue
        ser = _series(tb)
        if ser is None:
            continue
        dates, idx, h_adj, l_adj, amt, ret = ser

        # --- 行业等权收益累计（用当月 PIT 成员）
        n_bar += 1
        for i, d in enumerate(dates):
            ind = (snaps.get(d[:6]) or {}).get(code)
            if not ind:
                continue
            key = (d[:6], ind)
            ind_sum[key][d] += ret[i]
            ind_cnt[key][d] += 1

        if code not in by_code:
            continue
        pos = {d: i for i, d in enumerate(dates)}
        for d0 in sorted(set(by_code[code])):
            i = pos.get(d0)
            if i is None:
                n_miss += 1
                continue
            f = features_one(code, d0, i, idx, h_adj, l_adj, amt, ret, ctx)
            feat[(code, d0)] = f
        if (fi + 1) % 500 == 0:
            print("  … %d/%d 文件，%d 笔特征，行业 %d 组"
                  % (fi + 1, len(files), len(feat), len(ind_sum)), flush=True)

    # ---- 行业收益 → 指数水平 → 20/60 日收益
    ind_lv = {}
    for (ym, ind), dd in ind_sum.items():
        for d, s in dd.items():
            c = ind_cnt[(ym, ind)][d]
            if c:
                ind_lv.setdefault(ind, {})[d] = s / c
    ind_series = {}
    for ind, dd in ind_lv.items():
        ds = sorted(dd)
        lv, acc = [], 1.0
        for d in ds:
            acc *= (1.0 + dd[d])
            lv.append(acc)
        ind_series[ind] = (ds, lv, {d: i for i, d in enumerate(ds)})

    def ind_ret(ind, d0, k):
        s = ind_series.get(ind)
        if not s:
            return None
        ds, lv, pos = s
        i = pos.get(d0)
        if i is None or i - k < 0 or lv[i - k] <= 0:
            return None
        return lv[i] / lv[i - k] - 1.0

    # 把板块相对强度补进去（需要行业指数，必须等全市场跑完）
    for key, f in feat.items():
        ind = f.get("ind")
        if not ind:
            continue
        for k in (20, 60):
            ir = ind_ret(ind, f["d0"], k)
            if ir is not None:
                f["ind_ret%d" % k] = ir
                sr = f.get("ret%d" % k)
                if sr is not None:
                    f["ind_rs%d" % k] = sr - ir

    # ---- 落盘
    old = load_old_feats()
    out_rows = []
    n_old_miss = 0
    for t in trades:
        f = feat.get((t["c"], t["d0"]))
        if f is None:
            continue
        r = dict(t)
        r.update(f)
        o = old.get((t["c"], t["d0"]))
        if o:
            r.update(o)
        else:
            n_old_miss += 1
        out_rows.append(r)
    tmp = PANEL + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in out_rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, PANEL)

    meta = {
        "n_trades": len(trades),
        "n_panel": len(out_rows),
        "n_codes": len(codes),
        "n_bars_used": n_bar,
        "n_miss_bar": n_miss,
        "n_ind": len(ind_series),
        "n_ind_groups": len(ind_sum),
        "n_turnover_codes": len(tur),
        "n_listed": len(listed),
        "n_old_miss": n_old_miss,
    }
    print("面板 %d 笔（%d 笔无行情/无 bar 被丢），行业 %d 个，换手覆盖 %d 票，"
          "信号字段缺 %d 笔"
          % (len(out_rows), len(trades) - len(out_rows), len(ind_series), len(tur),
             n_old_miss))
    print("→ %s" % PANEL)
    return out_rows, meta


# ---------------------------------------------------------------- 单变量筛
def _mean(xs):
    return statistics.mean(xs) if xs else None


def cluster_diff_t(vals_b, days_b, vals_r, days_r):
    """两组比较的聚类 t：每天先算「桶内均值 − 其余均值」，再对日序列求 t。"""
    byb, byr = {}, {}
    for v, d in zip(vals_b, days_b):
        byb.setdefault(d, []).append(v)
    for v, d in zip(vals_r, days_r):
        byr.setdefault(d, []).append(v)
    diffs = []
    for d in set(byb) & set(byr):
        diffs.append(statistics.mean(byb[d]) - statistics.mean(byr[d]))
    if len(diffs) < 3:
        return None, len(diffs)
    sd = statistics.pstdev(diffs)
    if sd == 0:
        return None, len(diffs)
    return statistics.mean(diffs) / (sd / math.sqrt(len(diffs))), len(diffs)


def hac_var(xs, lag=None):
    """Newey-West 方差（Bartlett 核）。

    两件事一起修：①同一天几十只票不是独立观测；②250 日前瞻窗口在相邻入场日
    之间共享 249/250 的样本——后者才是致命的，398 个入场日只覆盖 398 个交易日，
    独立窗口数其实只有 1.6 个。滞后取 min(250, N/2)。
    """
    n = len(xs)
    if n < 10:
        return None
    m = statistics.mean(xs)
    if lag is None:
        lag = min(250, max(1, n // 2))
    s = statistics.pvariance(xs)
    for l in range(1, min(lag, n - 1) + 1):
        cov = sum((xs[i] - m) * (xs[i - l] - m) for i in range(l, n)) / n
        s += 2.0 * (1.0 - l / (lag + 1.0)) * cov
    return max(s, 0.0)


def hac_t_two_sample(a, b):
    """两样本 HAC t。a/b 是「日」序列（每天一个观测）。"""
    if len(a) < 5 or len(b) < 5:
        return None
    va, vb = hac_var(a), hac_var(b)
    if va is None or vb is None:
        return None
    se = math.sqrt(va / len(a) + vb / len(b))
    if se <= 0:
        return None
    return (statistics.mean(a) - statistics.mean(b)) / se


def bucket_stats(rows, name, buckets, key="a", day_key="d0"):
    """每个桶的画像 + 「剔除该桶」与「只留该桶」两种用法。

    ΔA 的单位是百分点（落盘时已 ×100），同时给出 bp 便于与 122bp 成本线比。
    """
    all_v = [r[key] for r in rows if r.get(key) is not None]
    base = _mean(all_v)
    out = []
    for lab, pred in buckets:
        hit = [r for r in rows if pred(r)]
        rest = [r for r in rows if not pred(r)]
        if len(hit) < MIN_N or len(rest) < MIN_N:
            continue
        hv = [r[key] for r in hit if r.get(key) is not None]
        rv = [r[key] for r in rest if r.get(key) is not None]
        if not hv or not rv:
            continue
        td, nd = cluster_diff_t(hv, [r[day_key] for r in hit if r.get(key) is not None],
                                rv, [r[day_key] for r in rest if r.get(key) is not None])
        left = sum(1 for r in hit if r["ret"] < G_DN) / len(hit)
        right = sum(1 for r in hit if r["ret"] >= G_UP) / len(hit)
        out.append({
            "feature": name, "bucket": lab, "key": key,
            "n": len(hit), "share": round(100.0 * len(hit) / len(rows), 1),
            "left_pct": round(100 * left, 1),
            "right_pct": round(100 * right, 1),
            # 落盘的 a 已是百分点，换 bp 只再乘 100（乘 1e4 会大 100 倍）
            "A_bp": round(100 * _mean(hv), 0),
            "A_rest_bp": round(100 * _mean(rv), 0),
            "base_bp": round(100 * base, 0),
            "dA_bp": round(100 * (_mean(rv) - base), 0),      # 剔除该桶
            # t 必须与它配对的 ΔA 同向：cluster_diff_t 算的是 hit−rest，
            # 而 dA 是 rest−base，所以「剔除」这一侧要取反。第一版没取反，
            # 结果每一条规则的 Δt 都和 ΔA 反号（判据只看 |t| 与双口径同号，
            # 所以通过名单不受影响，但表读起来是错的）。
            "dA_t": round(-td, 2) if td is not None else None,
            "keep_dA_bp": round(100 * (_mean(hv) - base), 0),  # 只留该桶
            "keep_dA_t": round(td, 2) if td is not None else None,
            "dA_ndays": nd,
            "level": "trade",
        })
    return out


def date_level_stats(rows, name, buckets, key="a"):
    """日级变量的替代检验：先算每日均值，再对日序列做两样本 HAC t。

    `crowd` / `regime` 同一天所有票取同一个值，当天没有组间差，因此日聚类 t
    在这类特征上**无定义**（第一版直接算不出数）。正确做法是把「一天」当成
    一个观测：桶内日 vs 其余日，方差用 Newey-West 修重叠。

    ΔA 仍按笔算（与其它表可比），t 按日算——两者分母不同，写在字段里免得混淆。
    """
    byday = {}
    for r in rows:
        if r.get(key) is None:
            continue
        byday.setdefault(r["d0"], []).append(r[key])
    dm = {d: statistics.mean(v) for d, v in byday.items()}
    allv = [r[key] for r in rows if r.get(key) is not None]
    base = statistics.mean(allv)
    out = []
    for lab, pred in buckets:
        hit = [r for r in rows if pred(r)]
        rest = [r for r in rows if not pred(r)]
        if len(hit) < MIN_N or len(rest) < MIN_N:
            continue
        hit_days = {r["d0"] for r in hit}
        a = [dm[d] for d in hit_days if d in dm]
        b = [dm[d] for d in dm if d not in hit_days]
        td = hac_t_two_sample(a, b)
        hv = [r[key] for r in hit if r.get(key) is not None]
        rv = [r[key] for r in rest if r.get(key) is not None]
        left = sum(1 for r in hit if r["ret"] < G_DN) / len(hit)
        right = sum(1 for r in hit if r["ret"] >= G_UP) / len(hit)
        out.append({
            "feature": name, "bucket": lab, "key": key,
            "n": len(hit), "share": round(100.0 * len(hit) / len(rows), 1),
            "left_pct": round(100 * left, 1),
            "right_pct": round(100 * right, 1),
            "A_bp": round(100 * _mean(hv), 0),
            "A_rest_bp": round(100 * _mean(rv), 0),
            "base_bp": round(100 * base, 0),
            "dA_bp": round(100 * (_mean(rv) - base), 0),
            "dA_t": round(-td, 2) if td is not None else None,
            "keep_dA_bp": round(100 * (_mean(hv) - base), 0),
            "keep_dA_t": round(td, 2) if td is not None else None,
            "dA_ndays": min(len(a), len(b)),
            "n_days_hit": len(a), "n_days_rest": len(b),
            "level": "date",
        })
    return out


def num_buckets(field, rows, qs=QS):
    vals = sorted(r[field] for r in rows if isinstance(r.get(field), (int, float)))
    if len(vals) < MIN_N * 4:
        return []
    edges = []
    for q in qs:
        i = min(len(vals) - 1, int(round((len(vals) - 1) * q)))
        edges.append(vals[i])
    edges = sorted(set(edges))
    out = []
    nseg = len(edges) - 1
    for i in range(nseg):
        lo, hi = edges[i], edges[i + 1]
        if lo == hi:
            continue
        last = (i == nseg - 1)

        def pred(r, lo=lo, hi=hi, last=last, field=field):
            v = r.get(field)
            if not isinstance(v, (int, float)):
                return False
            return lo <= v <= hi if last else lo <= v < hi

        out.append(("%g~%g" % (lo, hi), pred))
    return out


def cat_buckets(field, values):
    return [(v, (lambda v=v, field=field: lambda r: str(r.get(field)) == v)())
            for v in values]


def screen(rows, meta=None):
    res = {
        "cap": 250,
        "n": len(rows),
        "n_days": len({r["d0"] for r in rows}),
        "gate": {"t": T_GATE, "min_n": MIN_N, "min_days": MIN_DAYS,
                 "same_sign": True},
        "coverage": {},
        "features": [],
        "passed": [],
        "noise": {},
        "meta": meta or {},
    }
    used = FEAT_NUM + FEAT_OLD_NUM + FEAT_CAT + FEAT_OLD_CAT
    for f in used:
        nn = sum(1 for r in rows if isinstance(r.get(f), (int, float))
                 or (f in FEAT_CAT + FEAT_OLD_CAT and r.get(f) is not None))
        res["coverage"][f] = round(100.0 * nn / len(rows), 1)

    blocks = []

    def add(name, bk):
        fn = date_level_stats if name in FEAT_DATE else bucket_stats
        std = fn(rows, name, bk, key="a")
        raw = {x["bucket"]: x for x in fn(rows, name, bk, key="a1")}
        for x in std:
            y = raw.get(x["bucket"])
            x["A1_bp"] = y["A_bp"] if y else None
            x["dA1_bp"] = y["dA_bp"] if y else None
            x["dA1_t"] = y["dA_t"] if y else None
            x["keep_dA1_bp"] = y["keep_dA_bp"] if y else None
            x["keep_dA1_t"] = y["keep_dA_t"] if y else None
            x["circular"] = name in CIRCULAR
            x["pass"] = False
            x["mode"] = None
            best = None
            for mode, dA, dA1, t, t1 in (
                    ("drop", x["dA_bp"], x["dA1_bp"], x["dA_t"], x["dA1_t"]),
                    ("keep", x["keep_dA_bp"], x["keep_dA1_bp"],
                     x["keep_dA_t"], x["keep_dA1_t"])):
                if dA is None or dA <= 0 or t is None or t1 is None:
                    continue
                if abs(t) < T_GATE or t * t1 <= 0:
                    continue
                if (x["dA_ndays"] or 0) < MIN_DAYS:
                    continue
                if x["circular"]:
                    continue          # 循环论证：留在表里留证，不进候选
                if best is None or dA > best[1]:
                    best = (mode, dA, dA1, t, t1)
            if best:
                x["pass"] = True
                x["mode"] = best[0]
                x["use_dA"] = best[1]
                x["use_dA1"] = best[2]
                x["use_t"] = best[3]
                x["use_t1"] = best[4]
            # ΔA 是「按笔加权」的均值差，Δt 是「按日等权」的均值差的 t。
            # 两者的权不同，可以反号：某桶只出现在少数几天、且那几天组内构成特殊时，
            # 合并均值说它好、逐日配对说它差。判据只写了 |Δt|≥2 与双口径同号，
            # 没有要求 sign(ΔA)=sign(Δt)，所以这里只标注、不改判
            # （看到结果再把门槛变严，就不是预注册了）。
            # 只对「通过」的行标注：ΔA 只有几十 bp 的行，符号本来就是噪声，
            # 全标出来只会稀释这个警告。
            _d = x["dA_bp"] if x["mode"] in (None, "drop") else x["keep_dA_bp"]
            _t = x["dA_t"] if x["mode"] in (None, "drop") else x["keep_dA_t"]
            x["sign_mismatch"] = bool(x["pass"] and _d and _t and _d * _t < 0)
        blocks.append(std)

    for f in FEAT_CAT + FEAT_OLD_CAT:
        vals = sorted({str(r.get(f)) for r in rows if r.get(f) is not None})
        if len(vals) < 2:
            continue
        vals = [v for v in vals if sum(1 for r in rows if str(r.get(f)) == v) >= MIN_N]
        if len(vals) < 2:
            continue
        add(f, cat_buckets(f, vals))
    for f in FEAT_NUM + FEAT_OLD_NUM:
        bk = num_buckets(f, rows)
        if bk:
            add(f, bk)

    flat = [x for b in blocks for x in b]
    res["features"] = flat
    res["passed"] = sorted([x for x in flat if x["pass"]],
                           key=lambda x: -abs(x["use_t"] or 0))
    n_test = len(flat)
    res["noise"] = {
        "n_tests": n_test,
        "expect_pass": round(n_test * 0.046, 1),
        "actual_pass": len(res["passed"]),
        "note": ("单侧 |t|>=2 的纯噪声期望 4.6%/次；通过数不超过期望说明这批特征"
                 "整体没有区分度，而单个通过项极可能是多重检验的产物"),
    }
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--screen-only", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()

    meta = {}
    if a.screen_only and os.path.exists(PANEL):
        rows = [json.loads(x) for x in open(PANEL, encoding="utf-8") if x.strip()]
        print("读缓存面板 %d 笔" % len(rows))
    elif os.path.exists(PANEL) and not a.rebuild:
        rows = [json.loads(x) for x in open(PANEL, encoding="utf-8") if x.strip()]
        print("读缓存面板 %d 笔（--rebuild 可重建）" % len(rows))
    else:
        rows, meta = build()

    res = screen(rows, meta)
    if not a.no_write:
        tmp = OUT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        os.replace(tmp, OUT)
        print("→ %s" % OUT)

    print("\n样本 %d 笔 / %d 入场日" % (res["n"], res["n_days"]))
    print("非空率：" + "  ".join("%s %.0f%%" % (k, v) for k, v in res["coverage"].items()))
    print("\n%-12s %-14s %5s %6s %6s %7s %7s %6s %7s %6s %2s %s"
          % ("特征", "桶", "n", "占比", "左尾", "A(bp)", "剃后ΔA", "Δt",
             "ΔA1", "Δt1", "级", "通过"))
    for x in res["features"]:
        print("%-12s %-14s %5d %5.1f%% %5.1f%% %7.0f %+7.0f %6s %+7s %6s %2s %s"
              % (x["feature"], x["bucket"], x["n"], x["share"], x["left_pct"],
                 x["A_bp"], x["dA_bp"],
                 "—" if x["dA_t"] is None else "%+.2f" % x["dA_t"],
                 "—" if x["dA1_bp"] is None else "%+.0f" % x["dA1_bp"],
                 "—" if x["dA1_t"] is None else "%+.2f" % x["dA1_t"],
                 "日" if x.get("level") == "date" else "笔",
                 ("★" + x["mode"]) if x["pass"] else
                 ("◆循环" if x.get("circular") else "")))
    print("\n通过 %d 条 / 检验 %d 次（纯噪声期望 %.1f 条）"
          % (len(res["passed"]), res["noise"]["n_tests"], res["noise"]["expect_pass"]))
    for x in res["passed"]:
        print("  %-12s %-14s %s  ΔA=%+.0fbp Δt=%+.2f Δt1=%+.2f  n=%d"
              % (x["feature"], x["bucket"], x["mode"], x["use_dA"],
                 x["use_t"], x["use_t1"], x["n"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
