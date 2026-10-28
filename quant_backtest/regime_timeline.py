# -*- coding: utf-8 -*-
"""regime_timeline.py — 历史市况时间轴 × 策略表现地图（只读产线，新增本文件）

目标：回答三个问题
  a) 现在处于什么市况？历史上同款市况之后 5/10/20/60 日市场表现如何（条件分布）？
  b) 各策略的正收益周集中在哪些 regime？（regime × 策略周均收益交叉表）
  c) 三年里"宽度 <35% 回升穿越 45%"的时点清单及后 20 日收益（V 反转起点证据）。

口径说明（证据分级见 regime_map.md）：
  - 市场指数代理 = 全市场等权日收益（pct_chg 等权均值，剔除湖内 4 只指数，
    与 qd_breadth_series 同口径）累乘成净值序列；不依赖外部指数数据。
  - 市场级 regime = 产线 agent_platform/engine/quant_double_knife._compute_regime
    直接应用于等权代理序列（high/low 用「等权平均日内相对振幅」重建；
    该函数与 qd_backtest_1445 / qd_scanner 单票 regime 为同一份代码）。
  - 宽度直接复用 reports/qd_breadth_series.json（close>MA20 占比，raw close）。
  - 周划分 = W-FRI，周键 = 该周最后一个实际交易日。

输出：reports/regime_timeline.json + reports/regime_map.md
运行： "C:/Program Files/Python311/python.exe" regime_timeline.py  > /c/Temp/regime_map.log 2>&1
"""

import glob
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, "C:/项目/老田的AI助手/agent_platform/engine")
from quant_double_knife import _compute_regime  # noqa: E402

ASHARE = os.path.join(HERE, "data", "ashare")
REPORTS = os.path.join(HERE, "reports")
LOG = "C:/Temp/regime_map.log"

W0, W1 = "20231001", "20261013"  # 报告窗口（三年）
INDEX_FILES = {"000001_SH", "000688_SH", "399001_SZ", "399006_SZ"}  # 湖内指数，剔除
FWD_HORIZONS = (5, 10, 20, 60)
HOLDS = ("r5", "r10", "r20")


def log(msg):
    print(msg, flush=True)


def jround(x, n=5):
    if x is None:
        return None
    try:
        if not np.isfinite(x):
            return None
    except TypeError:
        return None
    return round(float(x), n)


# ── 1. 全市场等权代理指数 ────────────────────────────────────────────
def build_market_proxy():
    """遍历湖：等权日收益 / 等权日内相对振幅 / 总成交额。"""
    files = sorted(glob.glob(os.path.join(ASHARE, "*.parquet")))
    files = [f for f in files if os.path.basename(f)[:-8] not in INDEX_FILES]
    log("[1] 湖内股票文件：%d（剔除指数 %d 只）" % (len(files), len(INDEX_FILES)))

    ew_rets, rel_highs, rel_lows, amts = [], [], [], []
    t0 = time.time()
    for k, fp in enumerate(files):
        try:
            df = pd.read_parquet(
                fp,
                columns=[
                    "trade_date",
                    "pre_close",
                    "high",
                    "low",
                    "close",
                    "pct_chg",
                    "amount",
                ],
            )
        except Exception:
            continue
        if len(df) < 100:  # 照抄产线 scan_one warmup 口径
            continue
        d = df["trade_date"].to_numpy()
        pc = df["pct_chg"].to_numpy(dtype=float) / 100.0
        pre = df["pre_close"].to_numpy(dtype=float)
        hi = df["high"].to_numpy(dtype=float)
        lo = df["low"].to_numpy(dtype=float)
        am = df["amount"].to_numpy(dtype=float)

        idx = pd.Index(d)
        ew_rets.append(pd.Series(pc, index=idx))
        with np.errstate(divide="ignore", invalid="ignore"):
            rh = np.where(pre > 0, hi / pre - 1.0, np.nan)
            rl = np.where(pre > 0, lo / pre - 1.0, np.nan)
        rel_highs.append(pd.Series(rh, index=idx))
        rel_lows.append(pd.Series(rl, index=idx))
        amts.append(pd.Series(am, index=idx))
        if (k + 1) % 1000 == 0:
            log("    ...%d/%d (%.0fs)" % (k + 1, len(files), time.time() - t0))

    def agg(series_list, how):
        big = pd.concat(series_list, axis=1)
        if how == "mean":
            return big.mean(axis=1)
        return big.sum(axis=1)

    log("    concat 聚合 ...")
    ew = agg(ew_rets, "mean").sort_index()
    rh = agg(rel_highs, "mean").sort_index()
    rl = agg(rel_lows, "mean").sort_index()
    am = agg(amts, "sum").sort_index()
    for s in (ew, rh, rl, am):
        s.index = pd.to_datetime(s.index, format="%Y%m%d")
    log(
        "    完成 %.0fs；交易日 %d 个（%s ~ %s）"
        % (time.time() - t0, len(ew), ew.index[0], ew.index[-1])
    )

    close_m = (1.0 + ew.fillna(0.0)).cumprod()  # 等权净值（起点=1）
    high_m = close_m * (1.0 + rh.fillna(0.0))
    low_m = close_m * (1.0 + rl.fillna(0.0))
    px = pd.DataFrame({"close": close_m, "high": high_m, "low": low_m, "amount": am})
    px.index = pd.to_datetime(px.index, format="%Y%m%d")
    px.index.name = "date"
    return px, ew


# ── 2. 宽度序列（复用产线产物） ──────────────────────────────────────
def load_breadth():
    with open(os.path.join(REPORTS, "qd_breadth_series.json"), encoding="utf-8") as f:
        d = json.load(f)
    s = pd.Series(d["breadth"], index=d["dates"], dtype=float)
    s.index = pd.to_datetime(s.index, format="%Y%m%d")
    s = s[s.notna()]
    log(
        "[2] 宽度序列：%d 个有效日（%s ~ %s），MA%d 口径"
        % (len(s), s.index[0], s.index[-1], d["params"]["ma_win"])
    )
    return s


# ── 3. 策略明细 ─────────────────────────────────────────────────────
def load_strategies():
    """返回 {name: {"kind":"signals"|"note", ...}}"""
    out = {}

    # 双刀（逐信号明细，hfq 口径）
    p = os.path.join(REPORTS, "qd_details_hfq.json")
    d = json.load(open(p, encoding="utf-8"))
    recs = pd.DataFrame(d["records"])
    out["qd_shuangdao"] = {
        "kind": "signals",
        "df": recs,
        "note": "双刀 strict 逐信号 hfq 口径（qd_details_hfq）",
        "grade": "A",
    }
    log("[3] qd_shuangdao：%d 条信号" % len(recs))

    # 青萍之末（被环境闸门否决信号的反事实明细）
    p = os.path.join(REPORTS, "qpzm_killed_signals.json")
    d = json.load(open(p, encoding="utf-8"))
    rows = pd.DataFrame(d["rows"])
    out["qpzm_killed"] = {
        "kind": "signals",
        "df": rows,
        "note": "青萍之末被闸门否决信号反事实（202510 起，qpzm_killed_signals）",
        "grade": "A（反事实口径：若闸门放行的收益）",
    }
    log("[3] qpzm_killed：%d 条" % len(rows))

    # 缠论轮动 v6c（逐笔交易，C1 主配置）
    p = os.path.join(HERE, "chanlun_rotation_v6c_result.json")
    d = json.load(open(p, encoding="utf-8"))
    tr = pd.DataFrame(d["trades_C1"])
    tr = tr[tr["pnl"].notna()][["entry_day", "pnl"]]
    out["chanlun_v6c"] = {
        "kind": "signals",
        "df": tr.rename(columns={"entry_day": "date", "pnl": "r20"}),
        "note": "缠论轮动 v6c C1（组合制+regime 帽+止损）逐笔已平仓",
        "grade": "A",
    }
    log("[3] chanlun_v6c：%d 笔已平仓" % len(tr))

    # cross（无逐信号 → 分段结论）
    p = os.path.join(REPORTS, "cross_grid_envgate_check.json")
    d = json.load(open(p, encoding="utf-8"))
    segs = {k: v for k, v in d.items() if k in ("FULL", "H1", "H2", "H3", "H4")}
    out["cross"] = {
        "kind": "note",
        "segments": segs,
        "note": "cross 九维 k3 P75（cross_grid_envgate_check 分段，无逐信号明细）",
        "grade": "B（官方报告分段结论直接引用）",
    }
    log("[3] cross：分段 %s" % list(segs.keys()))

    # linkage（半年度块 + 潮汐）
    p = os.path.join(REPORTS, "linkage_resonance_audit.json")
    d = json.load(open(p, encoding="utf-8"))
    out["linkage"] = {
        "kind": "note",
        "blocks": d["blocks"].get("5", {}),
        "waves": d.get("waves", {}).get("list", []),
        "note": "联动共振信号③ 按半年度块与潮汐段（linkage_resonance_audit）",
        "grade": "B（官方报告分段结论直接引用）",
    }
    log(
        "[3] linkage：blocks=%s waves=%d"
        % (
            list(d["blocks"].get("5", {}).keys()),
            len(d.get("waves", {}).get("list", [])),
        )
    )
    return out


def signals_weekly(df, ret_col="r20"):
    """信号明细 → {week_key: (n, mean_ret)}；week_key = 该周最后交易日。"""
    d = df.copy()
    d = d[d[ret_col].notna()]
    dt = pd.to_datetime(d["date"], format="%Y%m%d")
    wk = dt.dt.to_period("W-FRI")
    g = pd.DataFrame({"wk": wk, "ret": d[ret_col].to_numpy()}).groupby("wk")
    n = g.size()
    m = g["ret"].mean()
    return n, m


def period_to_weekkey(period, trade_days_idx):
    """W-FRI period → 该周最后一个实际交易日（字符串 YYYYMMDD）。"""
    end = period.end_time.normalize()
    sub = trade_days_idx[trade_days_idx <= end]
    if len(sub) == 0:
        return None
    last = sub[-1]
    if last < period.start_time.normalize():
        return None
    return last.strftime("%Y%m%d")


# ── 4. 前瞻收益与条件分布 ────────────────────────────────────────────
def fwd_ret(close_m, i, n):
    if i + n >= len(close_m):
        return None
    return close_m[i + n] / close_m[i] - 1.0


def dist_stats(vals):
    a = np.asarray([v for v in vals if v is not None], dtype=float)
    if len(a) == 0:
        return {"n": 0}
    return {
        "n": int(len(a)),
        "mean": jround(a.mean(), 4),
        "median": jround(np.median(a), 4),
        "win_rate": jround((a > 0).mean(), 4),
        "p5": jround(np.percentile(a, 5), 4),
        "p25": jround(np.percentile(a, 25), 4),
        "p75": jround(np.percentile(a, 75), 4),
        "p95": jround(np.percentile(a, 95), 4),
    }


# ── 主流程 ──────────────────────────────────────────────────────────
def main():
    t0 = time.time()
    px, ew = build_market_proxy()
    breadth = load_breadth()
    strats = load_strategies()

    # 日度主表
    daily = pd.DataFrame(index=px.index)
    daily["ew_ret"] = ew
    daily["breadth"] = breadth.reindex(px.index)
    daily["vol20"] = daily["ew_ret"].rolling(20).std()

    # 市场 regime（产线公式应用于等权代理）
    regime_df = _compute_regime(px["close"], px["high"], px["low"], px["amount"])
    daily["regime"] = regime_df["regime_label"]
    log("[4] 市场 regime 分布（全样本）：")
    log(daily["regime"].value_counts().to_string())

    win = daily.loc[W0:W1].copy()
    # 湖尾部分数据日（宽度缺失）剔除，锚定最后有效日
    win = win[win["breadth"].notna()]
    tdays = daily.index

    # ── 周聚合 ──────────────────────────────────────────────────────
    wdf = win.reset_index(names="date")
    wdf["wk"] = wdf["date"].dt.to_period("W-FRI")
    g = wdf.groupby("wk")
    weekly = (
        pd.DataFrame(
            {
                "week_end": g["date"].max().astype(str).str.replace("-", ""),
                "n_days": g.size(),
                "ew_week": g["ew_ret"].apply(lambda s: (1 + s.fillna(0)).prod() - 1),
                "breadth": g["breadth"].last(),
                "vol20": g["vol20"].last(),
                "regime": g["regime"].last(),
            }
        )
        .reset_index()
        .rename(columns={"wk": "wk_p"})
    )

    # 策略逐信号按周叠加
    strat_weekly = {}
    for name, meta in strats.items():
        if meta["kind"] != "signals":
            continue
        n_w, m_w = signals_weekly(meta["df"])
        rows = {}
        for wk_p in weekly["wk_p"]:
            key = period_to_weekkey(wk_p, tdays)
            if key is None:
                continue
            rec = {"n": int(n_w.get(wk_p, 0))}
            rec["mean_r20"] = jround(m_w.get(wk_p)) if wk_p in m_w.index else None
            if rec["mean_r20"] is not None and rec["n"] > 0:
                rec["week_mark"] = "win" if rec["mean_r20"] > 0 else "loss"
            rows[key] = rec
        strat_weekly[name] = rows
        # 挂到 weekly 表
        weekly[name + "_n"] = weekly["week_end"].map(lambda k: rows.get(k, {}).get("n"))
        weekly[name + "_r"] = weekly["week_end"].map(
            lambda k: rows.get(k, {}).get("mean_r20")
        )
        weekly[name + "_mark"] = weekly["week_end"].map(
            lambda k: rows.get(k, {}).get("week_mark")
        )

    # note 类策略：分段结论标注到周
    note_cols = {}
    seg = strats["cross"]["segments"]

    def cross_note(dstr):
        for k, v in seg.items():
            if k == "FULL":
                continue
            if v["d0"] <= dstr <= v["d1"]:
                return "%s(pf5=%.2f)" % (k, v["pf5"])
        return None

    note_cols["cross_seg"] = weekly["week_end"].map(cross_note)

    blocks = strats["linkage"]["blocks"]

    def linkage_note(dstr):
        half = "H1" if dstr[4:6] <= "06" else "H2"
        key = dstr[:4] + half
        if key in blocks and isinstance(blocks[key], dict):
            b = blocks[key]
            return "%s(hit=%.2f,pf=%.1f)" % (key, b.get("hitrate", 0), b.get("pf", 0))
        return None

    note_cols["linkage_seg"] = weekly["week_end"].map(linkage_note)
    for c, v in note_cols.items():
        weekly[c] = v

    log(
        "[5] 周表：%d 周（%s ~ %s）"
        % (len(weekly), weekly["week_end"].iloc[0], weekly["week_end"].iloc[-1])
    )

    # ── 三连 a：当前市况 + 历史同款条件分布 ─────────────────────────
    close_m = px["close"].to_numpy()
    b_arr = daily["breadth"].to_numpy()
    r_arr = daily["regime"].to_numpy()
    dates_arr = daily.index.strftime("%Y%m%d")

    last_i = daily.index.get_loc(win.index[-1])
    cur = {
        "date": win.index[-1].strftime("%Y%m%d"),
        "breadth": jround(win["breadth"].iloc[-1], 4),
        "regime": str(win["regime"].iloc[-1]),
        "vol20_daily": jround(win["vol20"].iloc[-1], 4),
        "vol20_ann": jround(win["vol20"].iloc[-1] * np.sqrt(250), 4),
        "ew_20d": jround(close_m[last_i] / close_m[max(0, last_i - 20)] - 1, 4),
        "ew_60d": jround(close_m[last_i] / close_m[max(0, last_i - 60)] - 1, 4),
    }

    same_state = (b_arr < 0.40) & pd.Series(r_arr).isin(
        ["rotation", "trend_down"]
    ).to_numpy()
    same_state[: daily.index.searchsorted(W0)] = False  # 报告窗口内
    same_state[last_i + 1 :] = False  # 窗口外
    same_state[last_i] = False  # 最新一日无前瞻，单列

    # 连续段
    segs = []
    i = 0
    while i < len(same_state):
        if same_state[i]:
            j = i
            while j + 1 < len(same_state) and same_state[j + 1]:
                j += 1
            rec = {
                "start": dates_arr[i],
                "end": dates_arr[j],
                "days": int(j - i + 1),
                "breadth_min": jround(np.nanmin(b_arr[i : j + 1]), 4),
                "regimes": sorted(set(str(x) for x in r_arr[i : j + 1])),
            }
            for h in FWD_HORIZONS:
                rec["fwd%d" % h] = jround(fwd_ret(close_m, j, h), 4)
            segs.append(rec)
            i = j + 1
        else:
            i += 1

    same_fwd = {
        str(h): dist_stats([fwd_ret(close_m, i, h) for i in np.flatnonzero(same_state)])
        for h in FWD_HORIZONS
    }

    # ── 三连 b：regime × 策略交叉表 ─────────────────────────────────
    regime_of_week = dict(zip(weekly["week_end"], weekly["regime"]))
    xtab = {}
    for name in strat_weekly:
        by = {}
        for key, rec in strat_weekly[name].items():
            rg = regime_of_week.get(key)
            if rg is None or rec.get("mean_r20") is None:
                continue
            by.setdefault(rg, []).append(rec["mean_r20"])
        xtab[name] = {
            rg: {
                "n_weeks": len(v),
                "mean_r20": jround(np.mean(v), 4),
                "win_weeks": int(sum(1 for x in v if x > 0)),
            }
            for rg, v in sorted(by.items())
        }
    # 交叉表透视（regime 行 × 策略列，周均 r20）
    rg_order = ["trend_up", "rotation", "range", "trend_down"]
    pivot = {
        rg: {name: xtab[name].get(rg, {}).get("mean_r20") for name in strat_weekly}
        for rg in rg_order
    }
    pivot_nweeks = {
        rg: {name: xtab[name].get(rg, {}).get("n_weeks", 0) for name in strat_weekly}
        for rg in rg_order
    }

    # ── 三连 c：宽度 <35% → 穿越 45% ────────────────────────────────
    v_reversals = []
    armed = False
    low_seen = None
    w_start = daily.index.searchsorted(W0)
    for i in range(w_start, len(b_arr)):  # 三年窗口内
        b = b_arr[i]
        if np.isnan(b):
            continue
        if not armed and b < 0.35:
            armed = True
            low_seen = {"date": dates_arr[i], "breadth": jround(b, 4)}
        elif armed and b >= 0.45:
            li = daily.index.searchsorted(low_seen["date"])
            rec = {
                "cross_date": dates_arr[i],
                "low_seen": low_seen,
                "days_low_to_cross": int(i - li),
                "low_to_cross_gain": jround(close_m[i] / close_m[li] - 1, 4),
            }
            for h in (5, 10, 20, 60):
                rec["fwd%d" % h] = jround(fwd_ret(close_m, i, h), 4)
            v_reversals.append(rec)
            armed = False
            low_seen = None

    # ── 输出 JSON ───────────────────────────────────────────────────
    out = {
        "meta": {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "window": [W0, W1],
            "universe": "湖内全部 CN 股（剔除 4 只指数，与 qd_breadth_series 同口径）",
            "market_proxy": "全市场等权日收益(pct_chg 均值)累乘净值；regime=产线 _compute_regime 应用于代理序列",
            "breadth_source": "reports/qd_breadth_series.json (close>MA20, raw close)",
            "week_rule": "W-FRI，周键=该周最后交易日",
            "n_weeks": int(len(weekly)),
            "readonly": True,
        },
        "current_state": cur,
        "weekly": [
            {
                "week_end": r.week_end,
                "ew_week": jround(r.ew_week, 4),
                "breadth": jround(r.breadth, 4),
                "vol20_ann": jround(
                    r.vol20 * np.sqrt(250) if pd.notna(r.vol20) else None, 4
                ),
                "regime": str(r.regime),
                "qd_shuangdao": {
                    "n": int(r.qd_shuangdao_n) if pd.notna(r.qd_shuangdao_n) else 0,
                    "mean_r20": jround(r.qd_shuangdao_r)
                    if pd.notna(r.qd_shuangdao_r)
                    else None,
                },
                "qpzm_killed": {
                    "n": int(r.qpzm_killed_n) if pd.notna(r.qpzm_killed_n) else 0,
                    "mean_r20": jround(r.qpzm_killed_r)
                    if pd.notna(r.qpzm_killed_r)
                    else None,
                },
                "chanlun_v6c": {
                    "n": int(r.chanlun_v6c_n) if pd.notna(r.chanlun_v6c_n) else 0,
                    "mean_r20": jround(r.chanlun_v6c_r)
                    if pd.notna(r.chanlun_v6c_r)
                    else None,
                },
                "cross_seg": r.cross_seg,
                "linkage_seg": r.linkage_seg,
            }
            for r in weekly.itertuples()
        ],
        "q3a": {
            "current_state": cur,
            "same_state_def": "宽度<0.40 且 regime∈{rotation,trend_down}（窗口 2023-10 起）",
            "same_state_days": int(same_state.sum()),
            "same_state_fwd_dist": same_fwd,
            "same_state_segments": segs,
        },
        "q3b": {
            "regime_x_strategy_mean_r20": pivot,
            "regime_x_strategy_n_weeks": pivot_nweeks,
            "strategy_notes": {
                name: {"note": m["note"], "grade": m["grade"]}
                for name, m in strats.items()
            },
            "cross_segments": seg,
            "linkage_blocks": {
                k: v
                for k, v in strats["linkage"]["blocks"].items()
                if isinstance(v, dict)
            },
            "linkage_waves": strats["linkage"]["waves"],
        },
        "q3c_v_reversals": v_reversals,
    }
    out_path = os.path.join(REPORTS, "regime_timeline.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log("[6] 已写 %s（%.0fs）" % (out_path, time.time() - t0))

    # 关键数值回显，供报告撰写
    log("=== 当前状态 ===")
    log(json.dumps(cur, ensure_ascii=False))
    log("=== 同款状态后续分布 ===")
    log(json.dumps(same_fwd, ensure_ascii=False))
    log("=== 同款连续段 %d 个 ===" % len(segs))
    for s in segs:
        log(json.dumps(s, ensure_ascii=False))
    log("=== V 反转时点 %d 个 ===" % len(v_reversals))
    for r in v_reversals:
        log(json.dumps(r, ensure_ascii=False))
    log("=== regime × 策略 周均 r20 ===")
    log(json.dumps(pivot, ensure_ascii=False))
    log("=== regime × 策略 周数 ===")
    log(json.dumps(pivot_nweeks, ensure_ascii=False))


if __name__ == "__main__":
    main()
