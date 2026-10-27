# -*- coding: utf-8 -*-
"""K35 三线联动 · 信号③共振底 → 大盘择时/风控联动口径审计（20261014 linkage-dev，只读）。

目的：③ 共振底按选股组合挂账已判暂缓（单日 P95=1605 只、前 3 潮贡献
83.8% 收益，本质是普跌反弹的"市场级信号"）。本脚本论证其作为
**市场级择时状态变量** 的后验价值，零产线改动、全本地只读。

口径：
  · 逐日"共振底触发规模" = 当日触发 ③ 的个股数（判定复用
    linkage_hitrate_backtest.judge_matrix，与产线 v1 对齐，零新口径）；
  · 触发潮：日触发数 ≥ 阈值（P90活跃日 / 固定500只 两档）的交易日，
    相邻间隔 ≤10 交易日合并为一潮；
  · 潮后窗口：以潮尾日（末个触发日）收盘为 T，量 000001.SH T+5/10/20/60
    收益与全市场宽度（close>MA20 占比）变化；
  · 宽度闸门交叉：触发日宽度分布（<40 / 40~50 / 50~60 / ≥60），
    重叠率 = 触发日中宽度<40% 的占比（闸门已拦截的部分）；
  · 两段式后验：触发日次日起空仓（RiskGuard 式"停止新开仓"的指数代理），
    解除 = 宽度回升 >45% 或触发后 20 交易日（孰先），对照买入持有；
  · qpzm/cross 闸门内信号逐 episode 明细未落盘
    （data/linkage_hitrate_result.json 仅聚合数）→ 按任务预案用
    指数 + 宽度代理表达策略层后验。

立项判据（任务书给定）：触发后 20 日指数超额收益 ≥2pp 且与宽度闸门
信息重叠 <70% → 值得立项。

用法:
  C:/Program Files/Python311/python.exe linkage_timing_audit.py
产出:
  reports/linkage_timing_audit.json；日志 C:/Temp/linkage_timing.log
"""

import json
import logging
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

LOG_PATH = "C:/Temp/linkage_timing.log"
OUT_PATH = os.path.join(HERE, "reports", "linkage_timing_audit.json")
HORIZONS = (5, 10, 20, 60)
FIXED_THR = 500  # 固定阈值档（只）
MAX_GAP = 10  # 潮合并：相邻触发日间隔 ≤10 交易日
BREADTH_EXIT = 45.0  # 两段式解除：宽度回升 >45%
OFF_CAP = 20  # 两段式兜底：触发后最多空仓 20 交易日
VERDICT_EXCESS_PP = 2.0  # 立项判据：20 日超额 ≥2pp
VERDICT_OVERLAP_MAX = 0.70  # 立项判据：重叠 <70%

log = logging.getLogger("timing_audit")


def setup_logger():
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(message)s")
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)


def _pct(x):
    return (
        None
        if x is None or (isinstance(x, float) and np.isnan(x))
        else round(float(x), 3)
    )


def _r(x):
    """收益小数 → 百分数保留2位。"""
    return (
        None
        if x is None or (isinstance(x, float) and np.isnan(x))
        else round(float(x) * 100, 2)
    )


def breadth_series(stock):
    """全市场宽度：close>MA20 占比(%)；med20 = 个股20日收益中位数(%)。"""
    ma20 = stock.rolling(20).mean()
    valid = stock.notna() & ma20.notna()
    above = (stock > ma20) & valid
    denom = valid.sum(axis=1).replace(0, np.nan)
    br = above.sum(axis=1) / denom * 100
    r20 = stock / stock.shift(20) - 1
    med20 = r20.median(axis=1) * 100
    return br, med20


def find_tides(cnt, thr, max_gap=MAX_GAP):
    """触发潮：日触发数≥thr 的交易日，间隔≤max_gap 交易日合并。"""
    dates = list(cnt.index)
    pos_of = {d: i for i, d in enumerate(dates)}
    fire = [d for d in dates if cnt[d] >= thr]
    tides = []
    for d in fire:
        if tides and pos_of[d] - pos_of[tides[-1]["end"]] <= max_gap:
            t = tides[-1]
            t["end"] = d
            t["n_days"] += 1
            t["total_cnt"] += int(cnt[d])
            t["peak_cnt"] = max(t["peak_cnt"], int(cnt[d]))
        else:
            tides.append(
                {
                    "start": d,
                    "end": d,
                    "n_days": 1,
                    "total_cnt": int(cnt[d]),
                    "peak_cnt": int(cnt[d]),
                }
            )
    for t in tides:
        t["mean_cnt"] = round(t["total_cnt"] / t["n_days"], 1)
    return tides


def fwd_ret(sr, d, k):
    """sr 在日期 d 收盘 → +k 交易日收盘的收益（小数；窗口不足返回 NaN）。"""
    dates = sr.index
    i = dates.get_loc(d)
    j = i + k
    if j >= len(dates) or pd.isna(sr.iloc[i]) or pd.isna(sr.iloc[j]):
        return np.nan
    return sr.iloc[j] / sr.iloc[i] - 1


def tide_row(t, idx, br, med20, baseline):
    """单潮后验表行：触发期当下 + 潮尾锚定的各窗口收益。"""
    d0, d1 = t["start"], t["end"]
    during = (
        idx[d1] / idx[d0] - 1 if not (pd.isna(idx[d0]) or pd.isna(idx[d1])) else np.nan
    )
    row = {
        "start": d0,
        "end": d1,
        "n_days": t["n_days"],
        "peak_cnt": t["peak_cnt"],
        "mean_cnt": t["mean_cnt"],
        "during_idx_ret": _r(during),
        "breadth_at_start": _pct(br.get(d0)),
        "breadth_at_end": _pct(br.get(d1)),
        "breadth_min_in_tide": _pct(br.loc[d0:d1].min()),
        "med20_at_start": _pct(med20.get(d0)),
        "fwd_from_end": {},
        "fwd_from_start": {},
    }
    for k in HORIZONS:
        fe = fwd_ret(idx, d1, k)
        fs = fwd_ret(idx, d0, k)
        row["fwd_from_end"][k] = {
            "idx_ret": _r(fe),
            "excess_vs_base": _r(fe - baseline[k]) if not np.isnan(fe) else None,
        }
        row["fwd_from_start"][k] = {"idx_ret": _r(fs)}
    return row


def overlap_stats(cnt, thr, br, idx, baseline):
    """触发日 × 宽度闸门交叉分布与分桶后验。"""
    days = [d for d in cnt.index if cnt[d] >= thr]
    buckets = {"<40": [], "40-50": [], "50-60": [], ">=60": []}
    same_day = []
    f20_by_bucket = {"<40": [], "40-50": [], "50-60": [], ">=60": []}
    for d in days:
        b = br.get(d)
        if pd.isna(b):
            continue
        key = "<40" if b < 40 else "40-50" if b < 50 else "50-60" if b < 60 else ">=60"
        buckets[key].append(d)
        r1 = idx.pct_change().get(d, np.nan)
        if not pd.isna(r1):
            same_day.append(r1)
        f20 = fwd_ret(idx, d, 20)
        if not np.isnan(f20):
            f20_by_bucket[key].append(f20)
    n = sum(len(v) for v in buckets.values())
    out = {
        "n_trigger_days": n,
        "breadth_dist": {
            k: {"n": len(v), "share": _pct(len(v) / n) if n else None}
            for k, v in buckets.items()
        },
        "overlap_lt40": _pct(len(buckets["<40"]) / n) if n else None,
        "trigger_day_idx_ret": _r(np.mean(same_day)) if same_day else None,
        "fwd20_by_bucket": {
            k: {
                "n": len(v),
                "mean_idx_ret": _r(np.mean(v)) if v else None,
                "excess_vs_base": _r(np.mean(v) - baseline[20]) if v else None,
            }
            for k, v in f20_by_bucket.items()
        },
    }
    return out


def max_drawdown(cum):
    """cum: 累计净值序列（1 起步）。"""
    peak = cum.cummax()
    dd = cum / peak - 1
    return _r(dd.min())


def twostage(cnt, thr, idx, br):
    """两段式后验：触发潮次日起空仓，宽度>45% 或 20 交易日解除。

    返回全期对照与逐潮明细。pos=0 的日收益被跳过（空仓）。
    """
    dates = list(idx.index)
    ret = idx.pct_change().fillna(0)
    pos = pd.Series(1.0, index=dates)
    tides = find_tides(cnt, thr)
    detail = []
    for t in tides:
        i0 = dates.index(t["start"])
        i1 = dates.index(t["end"])
        cap = i1 + OFF_CAP
        resume = cap
        for j in range(i0 + 1, min(cap, len(dates) - 1) + 1):
            if not pd.isna(br.iloc[j]) and br.iloc[j] > BREADTH_EXIT:
                resume = j
                break
        off_idx = list(range(i0 + 1, resume + 1))
        off_days = [dates[j] for j in off_idx if j < len(dates)]
        for j in off_idx:
            if j < len(pos):
                pos.iloc[j] = 0.0
        off_ret = (
            float((1 + ret.iloc[[j for j in off_idx if j < len(ret)]]).prod() - 1)
            if off_idx
            else 0.0
        )
        detail.append(
            {
                "start": t["start"],
                "end": t["end"],
                "off_days": len(off_days),
                "off_first": off_days[0] if off_days else None,
                "off_last": off_days[-1] if off_days else None,
                "exit_reason": "breadth>45%" if resume < cap else "cap20d",
                "off_window_idx_ret": _r(off_ret),
            }
        )
    strat = (1 + ret * pos).cumprod()
    bh = (1 + ret).cumprod()
    valid = bh.dropna().index
    summary = {
        "period": [str(valid[0]), str(valid[-1])],
        "buyhold_ret": _r(float(bh.iloc[-1] - 1)),
        "strategy_ret": _r(float(strat.iloc[-1] - 1)),
        "buyhold_mdd": max_drawdown(bh.dropna()),
        "strategy_mdd": max_drawdown(strat.dropna()),
        "n_off_days": int((pos == 0).sum()),
        "n_tides": len(tides),
    }
    return summary, detail


def main():
    setup_logger()
    import linkage_hitrate_backtest as LHB

    log.info("== ③共振底 → 大盘择时口径审计（只读）==")
    stock, block, idx, uni = LHB.load_frames()
    log.info(
        "frames loaded: %d days %s~%s, %d stocks",
        len(stock.index),
        stock.index[0],
        stock.index[-1],
        stock.shape[1],
    )

    sig = LHB.judge_matrix(stock, block, idx, uni)
    sig3 = LHB._bool(sig["3"])
    cnt = sig3.sum(axis=1)
    log.info(
        "signal3 daily-count series ready, active days=%d, max=%d",
        int((cnt > 0).sum()),
        int(cnt.max()),
    )

    br, med20 = breadth_series(stock)
    log.info(
        "breadth ready: mean=%.1f%% min=%.1f%%",
        float(np.nanmean(br)),
        float(np.nanmin(br)),
    )

    # 基线：全样本无条件前向收益（日均），超额 = 条件 - 基线
    baseline = {}
    for k in HORIZONS:
        f = idx.shift(-k) / idx - 1
        baseline[k] = float(np.nanmean(f.values))
    log.info(
        "baseline fwd ret: "
        + ", ".join("T+%d=%.2f%%" % (k, baseline[k] * 100) for k in HORIZONS)
    )

    # 阈值两档
    active = cnt[cnt > 0]
    thr_p90 = int(np.ceil(np.percentile(active.values, 90))) if len(active) else 0
    log.info("thresholds: P90(active)=%d, fixed=%d", thr_p90, FIXED_THR)

    result = {
        "meta": {
            "script": "linkage_timing_audit.py",
            "readonly": True,
            "signal": "③共振底=个股×板块×大盘三线收盘同破近60根前低",
            "count_caliber": "judge_matrix 与产线 linkage_signals v1 对齐",
            "sample_days": [str(stock.index[0]), str(stock.index[-1])],
            "thresholds": {"p90_active": thr_p90, "fixed": FIXED_THR},
            "baseline_fwd_idx_ret": {str(k): _r(baseline[k]) for k in HORIZONS},
            "breadth_def": "全市场 close>MA20 占比(%)，闸门参考线 40/45",
            "note": "qpzm/cross 闸门内信号逐 episode 明细未落盘，策略层后验用指数+宽度代理",
        }
    }

    # 触发潮清单
    for name, thr in (("p90", thr_p90), ("fixed500", FIXED_THR)):
        tides = find_tides(cnt, thr)
        rows = [tide_row(t, idx, br, med20, baseline) for t in tides]
        # 潮级汇总：潮后 20 日均值与超额（表内值已是百分数，勿再 ×100）
        f20 = [
            r["fwd_from_end"][20]["idx_ret"]
            for r in rows
            if r["fwd_from_end"][20]["idx_ret"] is not None
        ]
        ex20 = [
            r["fwd_from_end"][20]["excess_vs_base"]
            for r in rows
            if r["fwd_from_end"][20]["excess_vs_base"] is not None
        ]
        dr = [r["during_idx_ret"] for r in rows if r["during_idx_ret"] is not None]
        result.setdefault("tides", {})[name] = {
            "threshold": thr,
            "n_tides": len(tides),
            "list": rows,
            "summary": {
                "mean_fwd20": _pct(np.mean(f20)) if f20 else None,
                "mean_excess20": _pct(np.mean(ex20)) if ex20 else None,
                "mean_during_ret": _pct(np.mean(dr)) if dr else None,
            },
        }
        log.info(
            "tides[%s] thr=%d n=%d mean_fwd20=%s mean_excess20=%s",
            name,
            thr,
            len(tides),
            result["tides"][name]["summary"]["mean_fwd20"],
            result["tides"][name]["summary"]["mean_excess20"],
        )

    # 宽度闸门交叉
    result["gate_cross"] = {
        name: overlap_stats(cnt, thr, br, idx, baseline)
        for name, thr in (("p90", thr_p90), ("fixed500", FIXED_THR))
    }
    for name, s in result["gate_cross"].items():
        log.info(
            "gate_cross[%s] overlap_lt40=%s trigger_day_ret=%s",
            name,
            s["overlap_lt40"],
            s["trigger_day_idx_ret"],
        )

    # 两段式后验（主档 fixed500，附 p90）
    result["twostage"] = {}
    for name, thr in (("fixed500", FIXED_THR), ("p90", thr_p90)):
        summ, detail = twostage(cnt, thr, idx, br)
        result["twostage"][name] = {"summary": summ, "tides": detail}
        log.info(
            "twostage[%s] strat=%s vs bh=%s, mdd %s vs %s, off_days=%d",
            name,
            summ["strategy_ret"],
            summ["buyhold_ret"],
            summ["strategy_mdd"],
            summ["buyhold_mdd"],
            summ["n_off_days"],
        )

    # 立项判定（主档 fixed500）
    t_main = result["tides"]["fixed500"]
    g_main = result["gate_cross"]["fixed500"]
    excess20 = t_main["summary"]["mean_excess20"]
    overlap = g_main["overlap_lt40"]
    cond_ex = excess20 is not None and excess20 >= VERDICT_EXCESS_PP
    cond_ov = overlap is not None and overlap < VERDICT_OVERLAP_MAX
    result["verdict"] = {
        "criteria": {
            "excess20_pp": VERDICT_EXCESS_PP,
            "overlap_max": VERDICT_OVERLAP_MAX,
        },
        "actual": {"mean_excess20": excess20, "overlap_lt40": overlap},
        "pass_excess": cond_ex,
        "pass_overlap": cond_ov,
        "worth_project": bool(cond_ex and cond_ov),
        "verdict": ("值得立项" if (cond_ex and cond_ov) else "不值得立项"),
    }
    log.info(
        "verdict: excess20=%s overlap=%s -> %s",
        excess20,
        overlap,
        result["verdict"]["verdict"],
    )

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    log.info("written -> %s", OUT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
