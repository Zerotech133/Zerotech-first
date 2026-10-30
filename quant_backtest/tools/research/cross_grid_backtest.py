# -*- coding: utf-8 -*-
"""W3-4 共振筛选（cross）网格验证 · 离线回放（只读产线，不写任何生产数据）。

目的：对 cross_poll 聚合 + market_scan 排序/分级这两处"拍的"参数做历史验证。

回放口径（PIT）：
· 对近 2 年每个交易日 T：用 T-1 及以前可得的四源信号聚合（比产线 16:00
  扫描多保守一天），候选股 T+1 开盘买入，持有 5/10 日收盘卖出；
· 成本：单笔往返合计 15bps（收益中一次性扣除 0.0015）；
· 宇宙：market_scan.market_stocks()（剔 ST/北交所/指数），CN only。

四源票定义（每源 ∈ {long=+1, short=-1, neutral=0}，w=权重）：
· qpzm  w=1.0 近端复刻：RPS mix(50:0.4/120:0.3/250:0.3)≥70 且 收盘>MA20 →
        long；mix<30 且 收盘<MA20 → short（产线口径的近似，见报告标注）；
· sig4  w=1.0 近端复刻：逐股四策略（ma_cross/trend_follow/rsi_reversal/
        boll_breakout，参数=PARAM_DEFAULTS）当日持仓数 ≥3 → long，否则
        neutral（与 cross_poll._src_sig4 同判）；
· chanlun w=1.0 精确 PIT：近 20 个交易日内最新 fresh 事件（v3 seen-ever
        事件缓存），含"买"→ long，含"卖"→ short；
· 9dim  w∈网格：最新锚（约周频）score≥75（ALERT_TH）且 cov≥0.75 的票。
        三种处理（网格变量①，ninedim 实证反指）：
        - reverse  反向计分：score≥75 → short（拥挤警报=减分项）
        - down0.5  降权：score≥75 → long，权重 0.5
        - exclude  剔除：无票（共振计数分母=3）

网格变量：①9dim∈{reverse,down0.5,exclude} ②共振最少源数 k∈{2,3}
③买入阈值∈{P50(中位),P60,P75}（当日过 k 门槛候选的排序键分位）
④排序键∈{raw(长票计数), net(净向分)}。共 3×2×3×2=36 组合。

每日选股：score ≥ 当日分位阈值 且 共振数≥k，按排序键降序取 TopN=10。

判据（升格信号层）：PF5≥1.15 且 平台（本组合达标 + 相邻组合≥3 个达标）。
相邻=9dim 档线序邻居 / k 翻转 / 阈值相邻档 / 排序键翻转（≤6 个）。

CLI: python cross_grid_backtest.py [--d0 20240902] [--d1 20260918]
     [--topn 10] [--workers 16]
产物：reports/cross_grid_result.json；日志 /c/Temp/cross_grid.log
纪律：只读产线（cross_poll/market_scan/positions/生产表零写入）。
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
LOG_FP = (
    "C:/Temp/cross_grid.log"
    if os.path.isdir("C:/Temp")
    else os.path.join(os.environ.get("TEMP", "/tmp"), "cross_grid.log")  # nosec B108 - 固定日志文件名
)

# ── 网格与常量 ──────────────────────────────────────────────
NINE_MODES = ["reverse", "down0.5", "exclude"]  # 线序（相邻=线序邻居）
K_SET = [2, 3]
THR_SET = [("P50", 0.50), ("P60", 0.60), ("P75", 0.75)]
SORT_KEYS = ["raw", "net"]
NINE_W = {"reverse": 1.0, "down0.5": 0.5, "exclude": 0.0}
ALERT_TH = 75  # nine_dim.ALERT_TH 拥挤警报线
MIN_COV = 0.75  # nine_dim.MIN_COV 完整评分线
CHAN_FRESH = 20  # 缠论信号有效窗口（交易日，对齐 cross_poll 90 日历≈20 交易日取紧口径）
COST = 0.0015  # 单笔往返合计 15bps
TOPN = 10
BAR_N_SIG = 100  # 达标最少信号数（<100 不构成结论，nine_dim 影子纪律）
BAR_PF = 1.15  # 升格判据 PF 线
MIN_CAND_DAY = 5  # 当日候选 <5 跳过（分位阈值不稳）


_logf = None


def log(msg):
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    if _logf:
        _logf.write(line + "\n")
        _logf.flush()


# ── 行情加载（只读） ────────────────────────────────────────
def load_one(args):
    """worker：单只股票对齐日历 → (code, close_vals, open_vals)。"""
    import numpy as np
    import pandas as pd

    code, cal = args
    fp = os.path.join(HERE, "data", "ashare", code.replace(".", "_") + ".parquet")
    try:
        b = pd.read_parquet(fp, columns=["trade_date", "open", "close"])
    except Exception:
        return code, None, None
    b = b.sort_values("trade_date").drop_duplicates("trade_date")
    a = pd.Index(cal)
    s = b.set_index(b["trade_date"].astype(str))
    c = s["close"].reindex(a)
    o = s["open"].reindex(a)
    return code, c.values.astype(np.float32), o.values.astype(np.float32)


def build_matrices(d0_warm="20230401"):
    """读全市场日线 → close/open 宽表 + 日历。缓存到 /c/Temp 避免重读。"""
    import numpy as np
    import pandas as pd

    cache = os.path.join(os.environ.get("TEMP", "/tmp"), "cross_grid_cache_npz.npz")  # nosec B108 - 固定缓存文件名
    cal_fp = os.path.join(HERE, "data", "ashare", "000001_SZ.parquet")
    cal = (
        pd.read_parquet(cal_fp, columns=["trade_date"])["trade_date"]
        .astype(str)
        .tolist()
    )
    cal = [d for d in cal if d >= d0_warm]
    close = open_ = codes = None
    if os.path.exists(cache):
        z = np.load(cache, allow_pickle=True)
        if list(z["cal"]) == cal:
            close, open_ = z["close"], z["open"]
            codes = list(z["codes"])
            log("行情缓存命中 %s" % cache)
    # 统一轴序为 (日期, 股票)
    if close is not None and close.shape[0] != len(cal):
        close = np.ascontiguousarray(close.T)
        open_ = np.ascontiguousarray(open_.T)
    if close is not None:
        return close, open_, cal, codes
    import market_scan

    stocks = market_scan.market_stocks()
    log("宇宙 %d 只，开始加载日线（warm=%s）" % (len(stocks), d0_warm))
    from multiprocessing import get_context

    ctx = get_context("spawn")
    with ctx.Pool(16) as pool:
        res = pool.map(load_one, [(s, cal) for s in stocks], chunksize=64)
    codes, cls, ops = [], [], []
    for code, c, o in res:
        if c is None or np.isnan(c).all():
            continue
        codes.append(code)
        cls.append(c)
        ops.append(o)
    close = np.ascontiguousarray(np.vstack(cls).T)  # (日期, 股票)
    open_ = np.ascontiguousarray(np.vstack(ops).T)
    np.savez_compressed(
        cache, close=close, open=open_, cal=np.array(cal), codes=np.array(codes)
    )
    log("加载完成 %d 日 × %d 只，缓存 %s" % (len(cal), len(codes), cache))
    return close, open_, cal, codes


# ── 四源 as-of 票矩阵 ───────────────────────────────────────
def rps_mix_matrix(close, cal):
    """qpzm 复刻：RPS mix = 0.4*r50+0.3*r120+0.3*r250（缺周期归一）。"""
    import numpy as np
    from scipy.stats import rankdata

    out = np.zeros_like(close, dtype=np.float32)
    wsum = np.zeros(close.shape[0], dtype=np.float32)
    for n, w in ((50, 0.4), (120, 0.3), (250, 0.3)):
        with np.errstate(invalid="ignore", divide="ignore"):
            r = (
                close / np.vstack([np.full((n, close.shape[1]), np.nan), close[:-n]])
                - 1.0
            )
        for i in range(close.shape[0]):
            col = r[i]
            m = ~np.isnan(col)  # 当日有 n 日收益的股票（逐日判定）
            if m.sum() < 100:
                continue
            rk = rankdata(col[m], method="average") / m.sum() * 100.0
            out[i, m] += w * rk.astype(np.float32)
            wsum[i] += w
    wsum[wsum == 0] = 1.0
    return out / wsum[:, None]


def strat_pos(close):
    """sig4 复刻：四策略持仓 wide（复刻 rps_resonance._strat_pos_wide，
    参数=strategies.PARAM_DEFAULTS，仅用 close 数学一致）。返回 (4, D, N)。"""
    import numpy as np
    import pandas as pd

    w = pd.DataFrame(close.astype(np.float64))
    pos = {}
    # ma_cross 5/20
    fa, sl = w.rolling(5, min_periods=1).mean(), w.rolling(20, min_periods=1).mean()
    pos["ma_cross"] = (fa > sl).fillna(False).astype(int)
    # trend_follow 20/5
    m = w.rolling(20, min_periods=1).mean()
    pos["trend_follow"] = ((w > m) & (m.diff(5) > 0)).fillna(False).astype(int)
    # rsi_reversal 14/30/70 事件帧
    delta = w.diff()
    gain = delta.clip(lower=0).rolling(14, min_periods=1).mean()
    loss = (-delta.clip(upper=0)).rolling(14, min_periods=1).mean()
    rs = gain / loss.replace(0, np.nan)
    r = (100 - 100 / (1 + rs)).fillna(50)
    val = w * np.nan
    val = val.mask(r < 30, 1.0)
    val = val.mask(r > 70, 0.0)
    pos["rsi_reversal"] = val.ffill().fillna(0).astype(int)
    # boll_breakout 20/2
    mid = w.rolling(20, min_periods=1).mean()
    sd = w.rolling(20, min_periods=1).std()
    pos["boll_breakout"] = (w > mid + 2 * sd).fillna(False).astype(int)
    return np.stack(
        [
            pos[k].values.astype(np.int8)
            for k in ("ma_cross", "trend_follow", "rsi_reversal", "boll_breakout")
        ]
    )


def chanlun_votes(cal, code2idx):
    """缠论票：v3 seen-ever 事件 → 当日票矩阵（近 20 交易日 fresh 事件）。"""
    import numpy as np

    fp = os.path.join(
        HERE, "data", "sig_events", "sig_events_market_v3_20231010_20261013.json"
    )
    d = json.load(open(fp, encoding="utf-8"))
    d2i = {dd: i for i, dd in enumerate(cal)}
    n_d, n_s = len(cal), len(code2idx)
    ev_long = np.zeros((n_d, n_s), dtype=np.int8)
    ev_short = np.zeros((n_d, n_s), dtype=np.int8)
    n_used = 0
    for e in d["events"]:
        i = d2i.get(str(e["day"]))
        j = code2idx.get(e["stock"])
        if i is None or j is None:
            continue
        n_used += 1
        if "买" in str(e["type"]):
            ev_long[i, j] = 1
        elif "卖" in str(e["type"]):
            ev_short[i, j] = 1

    # 近 CHAN_FRESH 日内有事件 → 票延续；冲突（买+卖并存）取更近者方向
    # （recency 加权：近一日权重+1，20 日外自然衰减为 0）
    def signed(ev_long, ev_short):
        acc = np.zeros(ev_long.shape, dtype=np.int32)
        for k in range(CHAN_FRESH):
            w = CHAN_FRESH - k
            if k:
                sh = np.zeros_like(ev_long)
                sh[:, k:] = ev_long[:, :-k]
                acc += w * sh
                sh = np.zeros_like(ev_short)
                sh[:, k:] = ev_short[:, :-k]
                acc -= w * sh
            else:
                acc += w * ev_long - w * ev_short
        return np.sign(acc).astype(np.int8)

    v = signed(ev_long, ev_short)
    log("缠论事件入宇宙 %d/%d" % (n_used, len(d["events"])))
    return v


def nine_votes(cal, code2idx):
    """9dim 锚 → (anchor_day_idx, score_vec, cov_vec) 序列。"""
    import numpy as np

    anchors = defaultdict(dict)  # anchor -> {code: (score, cov)}
    fp = os.path.join(HERE, "data", "nine_dim_replay.jsonl")
    for line in open(fp, encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        anchors[str(r.get("anchor"))][r.get("code")] = (
            r.get("score"),
            r.get("cov") or 0.0,
        )
    d2i = {dd: i for i, dd in enumerate(cal)}
    out = []
    for a in sorted(anchors):
        i = d2i.get(a)
        if i is None:
            continue
        sc = np.full(len(code2idx), np.nan, dtype=np.float32)
        cv = np.zeros(len(code2idx), dtype=np.float32)
        for c, (s, cov) in anchors[a].items():
            j = code2idx.get(c)
            if j is None or s is None:
                continue
            sc[j] = s
            cv[j] = cov
        out.append((i, sc, cv))
    return out


# ── 主流程 ──────────────────────────────────────────────────
def run(d0, d1, workers_unused=None, env_gate=False):
    import numpy as np

    close, open_, cal, codes = build_matrices()
    code2idx = {c: j for j, c in enumerate(codes)}
    n_d, n_s = len(cal), len(codes)
    ts = [i for i, dd in enumerate(cal) if d0 <= dd <= d1]
    log(
        "回放窗口 %s..%s：%d 个信号日（宇宙 %d，环境闸门=%s）"
        % (d0, d1, len(ts), n_s, env_gate)
    )
    # 环境闸门（近似产线 rps_resonance 宽度闸门）：全市场站上 MA20 占比
    import pandas as pd

    ma20_all = pd.DataFrame(close).rolling(20, min_periods=20).mean().values
    breadth = np.array(
        [np.nanmean(close[t] > ma20_all[t]) if t >= 20 else np.nan for t in ts]
    )
    gate_on = breadth >= 0.40
    log(
        "环境闸门：宽度中位 %.2f，可交易日 %d/%d"
        % (np.nanmedian(breadth), gate_on.sum(), len(ts))
    )

    # 前向收益：信号日 t → 买 t+1 开盘，持有 H 日收盘卖（exit=t+H）
    fwd = {}
    for H in (5, 10):
        e = np.array(ts) + 1
        x = e + H - 1  # 买入日 e 为第 1 根 → 第 H 根收盘 = e+H-1
        ok = x < n_d
        f = np.full((len(ts), n_s), np.nan, dtype=np.float32)
        f[ok] = close[x[ok]] / open_[e[ok]] - 1.0 - COST
        fwd[H] = f
    n_ok = (~np.isnan(fwd[5][:, 0])).sum()
    log(
        "前向收益可评信号日 %d/%d（尾部 %d 日窗口不足）"
        % (n_ok, len(ts), len(ts) - n_ok)
    )

    # 四源票（as-of T-1 = 行矩阵整体下移一行）
    def shift1(v):
        out = np.zeros_like(v)
        out[1:] = v[:-1]
        return out

    qp_raw = rps_mix_matrix(close, cal)
    import pandas as pd

    ma20 = pd.DataFrame(close).rolling(20, min_periods=20).mean().values
    v_qp = np.zeros((n_d, n_s), dtype=np.int8)
    v_qp[(qp_raw >= 70) & (close > ma20)] = 1
    v_qp[(qp_raw < 30) & (close < ma20)] = -1
    v_qp = shift1(v_qp)
    pos4 = strat_pos(close)
    v_s4 = shift1((pos4.sum(axis=0) >= 3).astype(np.int8))
    v_ch = shift1(chanlun_votes(cal, code2idx))
    anchors = nine_votes(cal, code2idx)
    # 9dim：每信号日取 anchor_idx ≤ t-1 的最新锚 → 高分票集合
    v9_high = np.zeros((len(ts), n_s), dtype=bool)  # as-of 后 score≥75 且 cov 达标
    a_idx = [i for i, _, _ in anchors]
    a_sc = np.vstack([sc for _, sc, _ in anchors]) if anchors else None
    a_cv = np.vstack([cv for _, _, cv in anchors]) if anchors else None
    for ti, t in enumerate(ts):
        cut = t - 1
        k = np.searchsorted(np.array(a_idx), cut, side="right") - 1
        if k < 0 or a_sc is None:
            continue
        m = (a_sc[k] >= ALERT_TH) & (a_cv[k] >= MIN_COV)
        v9_high[ti] = m
    log(
        "四源票就绪：qpzm long=%.0f short=%.0f  sig4 long=%.0f  "
        "chanlun long=%.0f short=%.0f  9dim高分日均值=%.1f"
        % (
            (v_qp[ts] == 1).sum(),
            (v_qp[ts] == -1).sum(),
            (v_s4[ts] == 1).sum(),
            (v_ch[ts] == 1).sum(),
            (v_ch[ts] == -1).sum(),
            v9_high.sum(axis=1).mean() if len(ts) else 0,
        )
    )

    qp_ts, s4_ts, ch_ts = v_qp[ts], v_s4[ts], v_ch[ts]
    results = []
    for mode in NINE_MODES:
        w9 = NINE_W[mode]
        # 9dim 票符号：reverse→-1（拥挤=short），down0.5→+1，exclude→不参与
        v9 = np.zeros((len(ts), n_s), dtype=np.float32)
        if mode == "reverse":
            v9[v9_high] = -1.0 * w9
        elif mode == "down0.5":
            v9[v9_high] = 1.0 * w9
        n_src_base = (
            (qp_ts != 0).astype(np.int8)
            + (s4_ts != 0).astype(np.int8)
            + (ch_ts != 0).astype(np.int8)
        )
        if mode == "exclude":
            n_src = n_src_base
        else:
            n_src = n_src_base + v9_high.astype(np.int8)
        for k in K_SET:
            mask_k = n_src >= k
            for thr_name, thr_q in THR_SET:
                for sk in SORT_KEYS:
                    v9l = (v9 > 0).astype(np.float32) * w9
                    raw = (
                        (qp_ts > 0).astype(np.float32)
                        + (s4_ts > 0).astype(np.float32)
                        + (ch_ts > 0).astype(np.float32)
                        + v9l
                    )
                    net = (
                        qp_ts.astype(np.float32)
                        + s4_ts.astype(np.float32)
                        + ch_ts.astype(np.float32)
                        + v9
                    )
                    score = raw if sk == "raw" else net
                    score = np.where(mask_k, score, np.nan)
                    picks_ret5, picks_ret10 = [], []
                    for ti in range(len(ts)):
                        if env_gate and not gate_on[ti]:
                            continue
                        row = score[ti]
                        cand = np.where(mask_k[ti] & ~np.isnan(row))[0]
                        if len(cand) < MIN_CAND_DAY:
                            continue
                        th = np.nanpercentile(row[cand], thr_q * 100)
                        sel = cand[row[cand] >= th]
                        if len(sel) == 0:
                            continue
                        srt = sel[np.lexsort((sel, -row[sel]))][:TOPN]
                        r5, r10 = fwd[5][ti], fwd[10][ti]
                        picks_ret5 += [r5[j] for j in srt if not np.isnan(r5[j])]
                        picks_ret10 += [r10[j] for j in srt if not np.isnan(r10[j])]
                    res = _metrics(picks_ret5, picks_ret10)
                    res.update({"nine": mode, "k": k, "thr": thr_name, "sort": sk})
                    results.append(res)
                    log(
                        "  %-8s k=%d %-3s %-3s | n=%-6d win5=%.3f pf5=%.3f "
                        "avg5=%+.4f pf10=%.3f"
                        % (
                            mode,
                            k,
                            thr_name,
                            sk,
                            res["n_sig"],
                            res["win5"],
                            res["pf5"],
                            res["avg5"],
                            res["pf10"],
                        )
                    )
    # 稳健性：平台 vs 尖峰
    _robustness(results)
    return results, {
        "d0": d0,
        "d1": d1,
        "n_days": len(ts),
        "universe": n_s,
        "cost_bps": COST * 10000,
        "topn": TOPN,
    }


def _metrics(r5, r10):
    import numpy as np

    def m(r):
        a = np.asarray(r, dtype=np.float64)
        if len(a) == 0:
            return dict(n_sig=0, win=0.0, pf=0.0, avg=0.0)
        pos, neg = a[a > 0], a[a < 0]
        pf = (
            float(pos.sum() / abs(neg.sum()))
            if len(neg) and neg.sum()
            else (float("inf") if len(pos) else 0.0)
        )
        return dict(
            n_sig=int(len(a)),
            win=round(float((a > 0).mean()), 4),
            pf=round(pf, 4) if pf != float("inf") else 99.99,
            avg=round(float(a.mean()), 5),
        )

    a, b = m(r5), m(r10)
    return {
        "n_sig": a["n_sig"],
        "win5": a["win"],
        "pf5": a["pf"],
        "avg5": a["avg"],
        "win10": b["win"],
        "pf10": b["pf"],
        "avg10": b["avg"],
    }


def _robustness(results):
    """相邻参数不塌方=平台；单点尖峰标注。判据：pf5≥BAR_PF 且 n_sig≥BAR_N_SIG。"""
    grid = {(r["nine"], r["k"], r["thr"], r["sort"]): r for r in results}
    order9 = NINE_MODES
    order_thr = [t[0] for t in THR_SET]
    for key, r in grid.items():
        mode, k, thr, sk = key
        nb = []
        mi = order9.index(mode)
        for dm in (-1, 1):
            if 0 <= mi + dm < len(order9):
                nb.append((order9[mi + dm], k, thr, sk))
        nb.append((mode, 3 if k == 2 else 2, thr, sk))
        ti = order_thr.index(thr)
        for dt in (-1, 1):
            if 0 <= ti + dt < len(order_thr):
                nb.append((mode, k, order_thr[ti + dt], sk))
        nb.append((mode, k, thr, "net" if sk == "raw" else "raw"))
        ok_nb = sum(
            1
            for nk in nb
            if nk in grid
            and grid[nk]["pf5"] >= BAR_PF
            and grid[nk]["n_sig"] >= BAR_N_SIG
        )
        ok_self = r["pf5"] >= BAR_PF and r["n_sig"] >= BAR_N_SIG
        r["n_ok_neighbors"] = ok_nb
        r["qualify"] = ok_self
        r["plateau"] = bool(ok_self and ok_nb >= 3)
        r["spike"] = bool(ok_self and ok_nb < 3)
        r["neighbors_ok_detail"] = [
            "%s|%s|%s|%s pf=%.2f" % (nk[0], nk[1], nk[2], nk[3], grid[nk]["pf5"])
            for nk in nb
            if nk in grid
            and grid[nk]["pf5"] >= BAR_PF
            and grid[nk]["n_sig"] >= BAR_N_SIG
        ]


if __name__ == "__main__":
    os.makedirs("/c/Temp", exist_ok=True) if os.path.isdir("/c") else None
    _logf = open(LOG_FP, "a", encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--d0", default="20240902")
    ap.add_argument("--d1", default="20260918")
    ap.add_argument("--topn", type=int, default=10)
    args = ap.parse_args()
    TOPN = args.topn
    log(
        "=== cross_grid_backtest 启动 d0=%s d1=%s topn=%d ==="
        % (args.d0, args.d1, TOPN)
    )
    results, meta = run(args.d0, args.d1)
    results.sort(key=lambda r: -r["pf5"])
    plat = [r for r in results if r.get("plateau")]
    best = plat[0] if plat else (results[0] if results else None)
    concl = {
        "best_combo": best,
        "9dim_reverse_holds": None,
        "upgrade_ready": bool(best and best.get("plateau")),
    }
    if best:
        rev = next(
            (
                r
                for r in results
                if r["nine"] == "reverse"
                and r["k"] == best["k"]
                and r["thr"] == best["thr"]
                and r["sort"] == best["sort"]
            ),
            None,
        )
        exc = next(
            (
                r
                for r in results
                if r["nine"] == "exclude"
                and r["k"] == best["k"]
                and r["thr"] == best["thr"]
                and r["sort"] == best["sort"]
            ),
            None,
        )
        if rev and exc:
            concl["9dim_reverse_holds"] = bool(rev["pf5"] > exc["pf5"])
    out = {
        "meta": meta,
        "conclusion": concl,
        "grid_table": [{k: v for k, v in r.items()} for r in results[:10]],
        "all_results": results,
    }
    fp = os.path.join(HERE, "reports", "cross_grid_result.json")
    json.dump(out, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log("产物 %s" % fp)
    log(
        "最优组合 %s | plateau=%s spike=%s | pf5=%s n_sig=%s"
        % (
            best and (best["nine"], best["k"], best["thr"], best["sort"]),
            best and best.get("plateau"),
            best and best.get("spike"),
            best and best["pf5"],
            best and best["n_sig"],
        )
    )
    log(
        "9dim 反向假说: %s | 升格信号层: %s"
        % (concl["9dim_reverse_holds"], concl["upgrade_ready"])
    )
    _logf.close()
