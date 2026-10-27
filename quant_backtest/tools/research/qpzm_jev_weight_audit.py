# -*- coding: utf-8 -*-
"""W5b · qpzm 打分体系 JEV 维权重复评（只读审计，零产线改动）。

背景（20261014 jev 弱否决审计结论）：jev-gate 当前对候选质量无可测的
边际贡献 → 本脚本回评 JEV 维在 qpzm 四维打分（W4 tech.35/jev.25/rps.25/
env.15，缺维降权）中的权重是否在提供信息。

四组对照（口径完全对齐 qpzm_backtest 锚：w_cur RPS 权重、75/50 阈、
闸门开、T+1 开盘买、往返成本 0.15%、K254 后复权、一字板剔除、
持有期 hold5/10/20 对齐既有报告）：
  A baseline_replay  现行 W4 + 消费 jev_replay_series（覆盖外缺维降权）
                     —— 与产线现行逻辑等价的回测口径
  B no_jev           JEV 剔除（权重 0，其余三维归一化）
  C half_jev         JEV 降权 0.5（jev 权重 0.25→0.125）
  D full_jev         满权对照（产线已是"无判定降权"=置信加权，故按任务书
                     对照"满权 vs 现行"：覆盖外按 50 中性分满权进权重，
                     即锚 --jev-mode neutral50 的语义）

附带：JEV 维覆盖率统计（replay 归档对信号窗格点/买入信号的覆盖比例、
每日归档对 watchlist 的覆盖比例）。

判定（任务书）：
  · B/C 相对 A 改善 ≥0.5pp 均收益或 PF +0.05 且半年度两段同向
    → 给权重调整产线方案（config 开关，只写方案不动手）
  · 差异 <0.2pp → 「JEV 维信息量中性，权重维持，等 jev-gate 毕业后再议」

纪律：不 import 修改 rps_resonance.py / qpzm_backtest.py（只读复用其
纯计算层）；不推飞书、不注册任务、不动生产数据（全部读本地 parquet +
jev 归档 JSON）。

用法:
  "C:/Program Files/Python311/python.exe" qpzm_jev_weight_audit.py
产物: reports/qpzm_jev_weight_audit_20261014.json + _summary.md
"""

import json
import os
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import qpzm_backtest as anchor  # 只读复用锚的纯计算层

REPORTS = anchor.REPORTS
OUT_JSON = os.path.join(REPORTS, "qpzm_jev_weight_audit_20261014.json")
OUT_MD = os.path.join(REPORTS, "qpzm_jev_weight_audit_20261014_summary.md")
START = "20251001"  # 对齐既有锚报告信号窗
SPLIT = 20260401  # 半年度分段界（H1 < 20260401 ≤ H2）
WATCHLIST_CFG = os.path.join(HERE, "..", "news_collector", "news_config.json")
JEV_ARCHIVE_DIR = os.path.join(HERE, "data", "jev_archive")

# 四组对照的 W4 权重（其余三维保持现行 tech.35/rps.25/env.15 不动）
W4_CUR = dict(anchor.W4)  # A: jev .25
W4_NO = {"tech": 0.35, "jev": 0.0, "rps": 0.25, "env": 0.15}  # B: 剔除
W4_HALF = {"tech": 0.35, "jev": 0.125, "rps": 0.25, "env": 0.15}  # C: 降权0.5
W4_FULL = dict(anchor.W4)  # D: 满权（同A权重）
ARMS = (
    ("A_baseline_replay", W4_CUR, "replay"),
    ("B_no_jev", W4_NO, "none"),
    ("C_half_jev", W4_HALF, "replay"),
    ("D_full_jev_neutral", W4_FULL, "neutral50"),
)


def combine_w(
    tech,
    rps_mix,
    env_df,
    w4,
    eligible,
    buy_th=75.0,
    obs_th=50.0,
    gate_on=True,
    jev_mat=None,
    jev_neutral=False,
):
    """参数化共振打分（逐条复刻 anchor.combine_score 的缺维降权数学，
    仅把 W4 常量开放为入参；jev_neutral=True 时缺 jev 处按 50 满权进权重
    —— 等价 anchor 的 neutral50 态语义）。

    返回 (score, buy) DataFrame。
    """
    t = tech.to_numpy(dtype=float)
    rp = rps_mix.to_numpy(dtype=float)
    el = (eligible & tech.notna()).to_numpy(dtype=bool)
    hr = el & ~np.isnan(rp)
    env_score = env_df["score"].to_numpy(dtype=float)
    wt, wj, wr, we = (w4["tech"], w4["jev"], w4["rps"], w4["env"])
    if jev_neutral:
        # 缺 jev 处按 50 分满权进权重（D 态）
        jv = el.copy()
        jv_val = np.full(t.shape, 50.0)
        wsum = (wt + we + wj) * el + wr * hr
        num = (
            t * wt
            + env_score[:, None] * we
            + 50.0 * wj * el
            + np.where(np.isnan(rp), 0.0, rp * wr)
        )
    elif jev_mat is not None:
        jm = jev_mat.reindex(index=tech.index, columns=tech.columns).to_numpy(
            dtype=float
        )
        jv = el & ~np.isnan(jm)
        jv_val = np.where(np.isnan(jm), 0.0, jm)
        wsum = (wt + we) * el + wr * hr + wj * jv
        num = (
            t * wt
            + env_score[:, None] * we
            + np.where(np.isnan(rp), 0.0, rp * wr)
            + jv_val * wj
        )
    else:
        jv = np.zeros_like(el)
        wsum = (wt + we) * el + wr * hr
        num = t * wt + env_score[:, None] * we + np.where(np.isnan(rp), 0.0, rp * wr)
    sc = np.divide(num, wsum, out=np.full(num.shape, np.nan), where=wsum > 0)
    gate = (
        env_df["safe"].to_numpy(dtype=bool)
        if gate_on
        else np.ones(len(env_df), dtype=bool)
    )
    gate_ok = np.broadcast_to(gate[:, None], sc.shape)
    ge = np.nan_to_num(sc, nan=-1e9)
    buy_m = el & hr & gate_ok & (ge >= buy_th)
    return (
        pd.DataFrame(sc, index=tech.index, columns=tech.columns),
        pd.DataFrame(buy_m, index=tech.index, columns=tech.columns),
    )


def profit_factor(ret_s):
    """盈利因子 = 毛利 / |毛损|（无亏损时 inf → 截断为 None 表示）。"""
    if len(ret_s) == 0:
        return None
    gain = float(ret_s[ret_s > 0].sum())
    loss = float(-ret_s[ret_s < 0].sum())
    if loss <= 0:
        return None
    return round(gain / loss, 4)


def half_stats(ret_s, bench_s, buy_w, lo, hi, n, cost):
    """一个半段的后验指标（信号日 ∈ [lo, hi)）。"""
    dates = ret_s.index.get_level_values("date")
    m = (dates >= lo) & (dates < hi)
    sub = ret_s[m]
    nb = int(buy_w.loc[(buy_w.index >= lo) & (buy_w.index < hi)].sum().sum())
    bm_dates = bench_s.index.get_level_values("date")
    bench = bench_s[(bm_dates >= lo) & (bm_dates < hi)]
    # 基准限制在同一批信号日
    sig_dates = set(pd.Series(sub.index.get_level_values("date")).unique())
    bench = bench[bench.index.get_level_values("date").isin(sig_dates)]
    return {
        "n_buy": nb,
        "n_trades": int(len(sub)),
        "mean": round(float(sub.mean()), 6) if len(sub) else None,
        "win_rate": round(float((sub > 0).mean()), 4) if len(sub) else None,
        "pf": profit_factor(sub),
        "max_dd": round(anchor.max_drawdown(anchor.date_mean(sub)), 4)
        if len(sub)
        else None,
        "excess_mean": round(float(sub.mean()) - float(bench.mean()), 6)
        if len(sub) and len(bench)
        else None,
    }


def eval_arm(buy_w, open_px, close_px, holds, cost, bench_by_hold):
    """一个臂 × 多持有期的全窗 + H1/H2 指标。"""
    out = {"holds": {}}
    for n in holds:
        ret, n_drop = anchor.trade_returns(
            buy_w, open_px, close_px, n, cost, unfill=UNFILL
        )
        dm_dates = ret.index.get_level_values("date")
        full = {
            "n_buy": int(buy_w.sum().sum()),
            "n_trades": int(len(ret)),
            "n_dropped_nobar": n_drop,
            "mean": round(float(ret.mean()), 6) if len(ret) else None,
            "median": round(float(ret.median()), 6) if len(ret) else None,
            "win_rate": round(float((ret > 0).mean()), 4) if len(ret) else None,
            "pf": profit_factor(ret),
            "max_dd": round(anchor.max_drawdown(anchor.date_mean(ret)), 4),
        }
        if len(ret):
            bench = bench_by_hold[n]
            sd = set(dm_dates.unique())
            bench = bench[bench.index.get_level_values("date").isin(sd)]
            full["excess_mean"] = round(float(ret.mean()) - float(bench.mean()), 6)
        else:
            full["excess_mean"] = None
        out["holds"][str(n)] = {
            "full": full,
            "H1": half_stats(ret, bench_by_hold[n], buy_w, 0, SPLIT, n, cost),
            "H2": half_stats(ret, bench_by_hold[n], buy_w, SPLIT, 10**9, n, cost),
        }
    return out


def coverage_stats(jev_mat, eligible, win_index):
    """JEV 维覆盖率：格点级 / 买入信号级 / 每日归档池级。"""
    jm = jev_mat.reindex(index=eligible.index, columns=eligible.columns)
    has = jm.notna()
    win = eligible.loc[win_index]
    has_w = has.loc[win_index]
    cell_elig = int(win.sum().sum())
    cell_cov = int((has_w & win).sum().sum())
    out = {
        "replay_meta": JEV_META,
        "signal_days_in_window": int(len(win_index)),
        "days_with_jev": int(len(jev_mat.index.intersection(win_index))),
        "cell_eligible": cell_elig,
        "cell_with_jev": cell_cov,
        "cell_coverage": round(cell_cov / cell_elig, 6) if cell_elig else None,
        "codes_per_jev_day": {
            "min": int(has_w.sum(axis=1)[has_w.sum(axis=1) > 0].min())
            if cell_cov
            else 0,
            "max": int(has_w.sum(axis=1).max()),
        },
    }
    # 每日归档池级覆盖（近 3 个归档文件）
    pool_rows = []
    try:
        wl_size = len(json.load(open(WATCHLIST_CFG, encoding="utf-8"))["watchlist"])
    except Exception:
        wl_size = None
    for fn in sorted(os.listdir(JEV_ARCHIVE_DIR)):
        if not (fn.startswith("jev_") and fn.endswith(".json") and "replay" not in fn):
            continue
        try:
            doc = json.load(open(os.path.join(JEV_ARCHIVE_DIR, fn), encoding="utf-8"))
            n = len(doc.get("pool", {}))
            pool_rows.append(
                {
                    "file": fn,
                    "codes_with_jev": n,
                    "watchlist_size": wl_size,
                    "pool_coverage": round(n / wl_size, 4) if wl_size else None,
                }
            )
        except Exception:
            continue
    out["daily_archive_pool"] = pool_rows
    return out


def corrected_form_coverage(union_dates):
    """假想修复列形态后的覆盖率上界（只读估算，不改打分）。

    锚的 load_jev_replay 把归档键转成裸 6 位码（'sh688025'→'688025'），
    而回测 wide 列是 '688025.SH' 形态 → replay jev 实际入打分为 0。
    这里统计：若按 ts 形态匹配，(信号交易日×CN码) 最多能覆盖多少格点
    —— 用于论证即便修复匹配，覆盖率仍可忽略，不影响判定方向。
    """
    try:
        ser = dict(
            json.load(
                open(
                    os.path.join(JEV_ARCHIVE_DIR, "jev_replay_series.json"),
                    encoding="utf-8",
                )
            )["series"]
        )
    except Exception:
        return {"error": "replay 归档不可读"}
    if JEV_META.get("partial_last_day") and len(ser) > 1:
        last = max(ser, key=lambda d: int(d.replace("-", "")))
        ser.pop(last)
    universe = {
        f[:-8].replace("_", ".")
        for f in os.listdir(anchor.DATA_DIR)
        if f.endswith(".parquet") and not f.startswith("_")
    }
    days = sorted(
        {int(d.replace("-", "")) for d in ser} & set(int(x) for x in union_dates)
    )
    cells = 0
    per_day = {}
    for d in days:
        key = "%d-%02d-%02d" % (d // 10000, d // 100 % 100, d % 100)
        n = sum(
            1
            for c in ser.get(key, {})
            if str(c)[:2] in ("sh", "sz", "bj")
            and str(c)[2:] + "." + str(c)[:2].upper() in universe
        )
        per_day[str(d)] = n
        cells += n
    return {
        "hypothetical_cells": cells,
        "per_day": per_day,
        "note": "锚 load_jev_replay 键转裸6位码与 wide 列 "
        "'XXXXXX.SH' 形态失配 → replay jev 实际入打分为 0；"
        "本表为修复形态后的假想命中格点数",
    }


def buy_coverage(buy_w, jev_mat):
    """基线买入信号中有多少比例实际吃到 jev 维。"""
    jm = jev_mat.reindex(index=buy_w.index, columns=buy_w.columns)
    both = buy_w & jm.notna()
    nb = int(buy_w.sum().sum())
    nc = int(both.sum().sum())
    return {
        "baseline_buy_signals": nb,
        "buy_with_jev": nc,
        "buy_jev_coverage": round(nc / nb, 6) if nb else None,
    }


def verdict(stats):
    """按任务书判定：B/C vs A（hold 主口径 hold10，全 hold 列表留证）。"""
    rows = []
    for arm in ("B_no_jev", "C_half_jev"):
        for n in HOLDS:
            a = stats["A_baseline_replay"]["holds"][str(n)]["full"]
            b = stats[arm]["holds"][str(n)]["full"]
            d_mean = round((b["mean"] or 0) - (a["mean"] or 0), 6)
            d_pf = None
            if b["pf"] is not None and a["pf"] is not None:
                d_pf = round(b["pf"] - a["pf"], 4)
            h1a = stats["A_baseline_replay"]["holds"][str(n)]["H1"]["mean"]
            h1b = stats[arm]["holds"][str(n)]["H1"]["mean"]
            h2a = stats["A_baseline_replay"]["holds"][str(n)]["H2"]["mean"]
            h2b = stats[arm]["holds"][str(n)]["H2"]["mean"]
            samedir = (
                h1a is not None
                and h1b is not None
                and h2a is not None
                and h2b is not None
                and np.sign(h1b - h1a) == np.sign(h2b - h2a)
                and np.sign(h1b - h1a) != 0
            )
            rows.append(
                {
                    "arm": arm,
                    "hold": n,
                    "d_mean": d_mean,
                    "d_pf": d_pf,
                    "h1_d_mean": round((h1b or 0) - (h1a or 0), 6)
                    if h1a is not None and h1b is not None
                    else None,
                    "h2_d_mean": round((h2b or 0) - (h2a or 0), 6)
                    if h2a is not None and h2b is not None
                    else None,
                    "halves_same_direction": bool(samedir),
                }
            )
    hit = [
        r
        for r in rows
        if (
            (r["d_mean"] is not None and r["d_mean"] >= 0.005)
            or (r["d_pf"] is not None and r["d_pf"] >= 0.05)
        )
        and r["halves_same_direction"]
    ]
    neutral = all(abs(r["d_mean"]) < 0.002 for r in rows)
    if hit:
        v = "GO_ADJUST"
    elif neutral:
        v = "NEUTRAL_KEEP"
    else:
        v = "INSUFFICIENT"
    return {"verdict": v, "checks": rows}


def _pf(x):
    return "—" if x is None else "%.3f" % x


def _pct(x, is_ratio=False):
    if x is None:
        return "—"
    return ("%+.2f%%" if not is_ratio else "%.1f%%") % (x * 100)


def build_md(payload):
    m = payload["meta"]
    lines = ["# W5b · qpzm JEV 维权重复评（只读审计）", ""]
    lines.append(
        "- 窗口: %s ~ 数据尾（%d 信号日，%d 股）；成本往返 %.2f%%"
        % (
            m["window"]["start"],
            m["window"]["signal_days"],
            m["n_stocks"],
            m["cost_roundtrip"] * 100,
        )
    )
    lines.append(
        "- 口径对齐锚: w_cur RPS 权重 · 75/50 阈 · 闸门开 · "
        "T+1 开盘买 · K254 后复权 · 一字板剔除"
    )
    lines.append("- 半年度分段界: < %d 为 H1，≥ %d 为 H2" % (SPLIT, SPLIT))
    lines.append("")
    lines.append("## 四组对照（主口径 hold=10）")
    lines.append("")
    lines.append("| 臂 | JEV 权重 | 买入信号 | 均收益 | 超额 | 胜率 | PF | 回撤 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    label = {
        "A_baseline_replay": "A 基线(现行·replay)",
        "B_no_jev": "B JEV剔除",
        "C_half_jev": "C JEV降权0.5",
        "D_full_jev_neutral": "D 满权(neutral50对照)",
    }
    for arm, _w, _mode in ARMS:
        s = payload["arms"][arm]["holds"]["10"]["full"]
        lines.append(
            "| %s | %.3f | %d | %s | %s | %s | %s | %s |"
            % (
                label[arm],
                _w["jev"],
                s["n_buy"],
                _pct(s["mean"]),
                _pct(s["excess_mean"]),
                _pct(s["win_rate"], True),
                _pf(s["pf"]),
                _pct(s["max_dd"]),
            )
        )
    lines.append("")
    lines.append("## 多持有期（均收益 / 超额 / PF）")
    lines.append("")
    lines.append(
        "| 臂 | hold | 均收益 | 超额 | 胜率 | PF | 回撤 | H1均收益 | H2均收益 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for arm, _w, _mode in ARMS:
        for n in sorted(HOLDS):
            h = payload["arms"][arm]["holds"][str(n)]
            f, h1, h2 = h["full"], h["H1"], h["H2"]
            lines.append(
                "| %s | %s | %s | %s | %s | %s | %s | %s | %s |"
                % (
                    label[arm],
                    n,
                    _pct(f["mean"]),
                    _pct(f["excess_mean"]),
                    _pct(f["win_rate"], True),
                    _pf(f["pf"]),
                    _pct(f["max_dd"]),
                    "%s(%d笔)" % (_pct(h1["mean"]), h1["n_trades"]),
                    "%s(%d笔)" % (_pct(h2["mean"]), h2["n_trades"]),
                )
            )
    lines.append("")
    lines.append("## JEV 维覆盖率")
    lines.append("")
    cov = payload["coverage"]
    lines.append(
        "- replay 归档: %s ~ %s（%d 天，%s）；信号窗 %d 天中 "
        "%d 天有 jev 数据"
        % (
            cov["replay_meta"].get("start"),
            cov["replay_meta"].get("end"),
            cov["replay_meta"].get("days"),
            cov["replay_meta"].get("note", "")[:60],
            cov["signal_days_in_window"],
            cov["days_with_jev"],
        )
    )
    lines.append(
        "- 格点覆盖: %d / %d 个(信号日×可打分股) = %s"
        % (cov["cell_with_jev"], cov["cell_eligible"], _pct(cov["cell_coverage"], True))
    )
    bc = payload["buy_coverage"]
    lines.append(
        "- 买入信号覆盖: %d / %d = %s"
        % (
            bc["buy_with_jev"],
            bc["baseline_buy_signals"],
            _pct(bc["buy_jev_coverage"], True),
        )
    )
    cf = cov.get("corrected_form_hypothetical", {})
    if "hypothetical_cells" in cf:
        lines.append(
            "- 形态失配说明: 锚 load_jev_replay 键转裸6位码，"
            "与 wide 列 'XXXXXX.SH' 失配 → replay jev 实际入打分"
            "为 0；修复形态后假想命中格点 %d 个（%s），仍可忽略"
            % (
                cf["hypothetical_cells"],
                ", ".join("%s:%d" % (k, v) for k, v in cf["per_day"].items()),
            )
        )
    for r in cov["daily_archive_pool"]:
        lines.append(
            "- 每日归档 %s: %d/%s 只 = %s"
            % (
                r["file"],
                r["codes_with_jev"],
                r["watchlist_size"],
                _pct(r["pool_coverage"], True),
            )
        )
    lines.append("")
    lines.append("## 判定")
    lines.append("")
    v = payload["verdict"]
    lines.append("- 判定结果: **%s**" % v["verdict"])
    lines.append("")
    lines.append("| 对照 | hold | Δ均收益 | ΔPF | H1Δ | H2Δ | 两段同向 |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in v["checks"]:
        lines.append(
            "| %s | %d | %s | %s | %s | %s | %s |"
            % (
                r["arm"],
                r["hold"],
                _pct(r["d_mean"]),
                "—" if r["d_pf"] is None else "%+.4f" % r["d_pf"],
                _pct(r["h1_d_mean"]),
                _pct(r["h2_d_mean"]),
                "✓" if r["halves_same_direction"] else "✗",
            )
        )
    lines.append("")
    return "\n".join(lines)


def main():
    global HOLDS, UNFILL, JEV_META
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    t0 = time.time()
    holds = [5, 10, 20]
    HOLDS = holds
    cost = 0.0015

    print("[1/5] load_wide（只读 parquet）…", flush=True)
    wide = anchor.load_wide(os.path.join(HERE, "data", "ashare"))
    UNFILL = wide["unfill"]
    union, codes = wide["dates"], wide["codes"]
    in_win = union >= int(START)
    win_index = union[in_win]
    eligible = wide["eligible"]

    print("[2/5] env / rps / tech …", flush=True)
    env_df = anchor.env_gate(wide["above20"], wide["ret20"])
    mix_cur, _na = anchor.cross_rps(
        {p: wide["chg%d" % p] for p in anchor.PERIODS}, anchor.RPS_W_CUR
    )
    tech_w = anchor.tech_score(wide["longs"], wide["above20"])

    print("[3/5] jev replay 矩阵 …", flush=True)
    jev_mat, JEV_META = anchor.load_jev_replay()

    print("[4/5] 基准逐笔（每持有期一次，与臂无关）…", flush=True)
    bench_by_hold = {
        n: anchor.bench_returns(
            eligible.loc[in_win],
            wide["open"].loc[in_win],
            wide["close"].loc[in_win],
            n,
            cost,
            unfill=UNFILL,
        )
        for n in holds
    }

    print("[5/5] 四组对照回放 …", flush=True)
    arms_out, buy_maps = {}, {}
    for name, w4, mode in ARMS:
        tb = time.time()
        if mode == "neutral50":
            _sc, buy = combine_w(
                tech_w,
                mix_cur,
                env_df,
                w4,
                eligible,
                buy_th=75.0,
                obs_th=50.0,
                gate_on=True,
                jev_neutral=True,
            )
        elif mode == "replay":
            _sc, buy = combine_w(
                tech_w,
                mix_cur,
                env_df,
                w4,
                eligible,
                buy_th=75.0,
                obs_th=50.0,
                gate_on=True,
                jev_mat=jev_mat,
            )
        else:  # none：jev 权重 0，jev_mat 不消费
            _sc, buy = combine_w(
                tech_w,
                mix_cur,
                env_df,
                w4,
                eligible,
                buy_th=75.0,
                obs_th=50.0,
                gate_on=True,
                jev_mat=None,
            )
        buy_w = buy.loc[in_win]
        buy_maps[name] = buy_w
        arms_out[name] = eval_arm(
            buy_w,
            wide["open"].loc[in_win],
            wide["close"].loc[in_win],
            holds,
            cost,
            bench_by_hold,
        )
        f = arms_out[name]["holds"]["10"]["full"]
        print(
            "  %-22s buy=%6d hold10 mean=%s pf=%s (%.0fs)"
            % (name, f["n_buy"], _pct(f["mean"]), _pf(f["pf"]), time.time() - tb),
            flush=True,
        )

    cov = coverage_stats(jev_mat, eligible, win_index)
    cov["corrected_form_hypothetical"] = corrected_form_coverage(union)
    bcov = buy_coverage(buy_maps["A_baseline_replay"], jev_mat)
    v = verdict(arms_out)

    payload = {
        "meta": {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "interpreter": sys.executable,
            "task": "W5b qpzm JEV 维权重复评（只读审计）",
            "window": {"start": START, "end": None, "signal_days": int(in_win.sum())},
            "n_stocks": len(codes),
            "holds": holds,
            "cost_roundtrip": cost,
            "split_date": SPLIT,
            "gate": "on（环境闸门保持现状）",
            "rps_w": "w_cur",
            "thresholds": [75, 50],
            "arms": {n: {"w4": w, "jev_source": md} for n, w, md in ARMS},
            "elapsed_sec": round(time.time() - t0, 1),
        },
        "coverage": cov,
        "buy_coverage": bcov,
        "arms": arms_out,
        "verdict": v,
    }
    anchor.atomic_write_json(OUT_JSON, payload)
    md = build_md(payload)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(md + "\n")
    print("report -> %s" % OUT_JSON)
    print("summary -> %s" % OUT_MD)
    print("VERDICT: %s" % v["verdict"])
    print(
        "COVERAGE cell=%s buy=%s"
        % (_pct(cov["cell_coverage"], True), _pct(bcov["buy_jev_coverage"], True))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
