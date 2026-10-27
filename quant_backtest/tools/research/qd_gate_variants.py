# -*- coding: utf-8 -*-
"""W3-5b 量子双刀强市门控（宽度闸门）变体离线验证（任务A）。

性质：纯离线分析脚本，只读数据湖、W3-5 逐信号明细（reports/qd_details_hfq.json）
    与本脚本产出的宽度序列（reports/qd_breadth_series.json），不碰产线。

背景（W3-5 结论）：
    combo V1×V2（rotation 拦截 + 信号 hfq）hold_20 全窗 PF=1.184 过 PF 关，
    但 OOS 段（20251001~20261013）PF=0.891 绝对为负；盈利集中在
    2024H1（PF 2.24）/ 2025H1（PF 2.55）两段强市 → 双刀疑似"强市专属"信号。
    本脚本用全市场宽度（close>MA20 占比）做日级门控验证该假设。

门控语义（同 cross 线环境闸门口径参考）：
    breadth_t = 当日湖内全部 CN 股（剔除 4 只指数，同 qd_backtest_1445 口径）
    中 close > MA20(20 日) 的占比；信号日 breadth_t >= θ 才放行该日全部信号。

14:45 时点近似与偏差方向（必读）：
    宽度按 T 日收盘价计算，产线若在 14:45 用门控，当时点宽度≈收盘宽度
    （尾盘 15 分钟漂移未计）。偏差方向：尾盘拉升日 14:45 真实宽度略低于
    收盘宽度 → 临界日可能被回测放行而实盘不达标，回测门控效果**略偏乐观**；
    尾盘跳水日反之。整体偏差量级小（宽度分子是全市场均值，单日漂移被摊薄），
    但结论解读须计入"偏乐观"方向。

判据（门控版的成功定义，全部满足才算过）：
    ① OOS 段（20251001~20261013）hold_20 PF≥1.0 且回正（均收益>0）；
    ② 全窗 hold_20 PF≥1.2（不低于无门控 combo 的 1.184）；
    ③ θ 平台 ≥2 档达标（相邻档 40/45/50 同向达标才算平台；单点达标=尖峰）。
    其中"档达标" = 该 θ 同时满足 ①②。
    三条全过 → 给产线门控变更方案（只写方案不动手）；
    任一不过 → 给卡点数字 + 维持 B 路线影子观察的最终建议。

对照行：
    无门控 combo（hfq 明细剔除 rotation，应复现 W3-5 PF 1.184）；
    cross 同款 40% 闸门行（= θ40 档本身，单独标注引用）。

用法（唯一合法解释器）：
    "C:/Program Files/Python311/python.exe" -u quant_backtest/qd_gate_variants.py \
        [--build-breadth]   # 强制重建宽度序列（默认文件存在即复用）
"""

import argparse
import glob
import json
import os
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ASHARE = ROOT / "quant_backtest" / "data" / "ashare"
REPORTS = HERE / "reports"
BREADTH_JSON = REPORTS / "qd_breadth_series.json"
OUT_JSON = REPORTS / "qd_gate_variants.json"
DETAILS = REPORTS / "qd_details_hfq.json"

# 指数文件剔除（同 qd_backtest_1445.INDEX_FILES 口径）
INDEX_FILES = {"000001_SH", "000688_SH", "399001_SZ", "399006_SZ"}

MA_WIN = 20
THETAS = (0.40, 0.45, 0.50)
HOLDS = (5, 10, 20)

# 分段（同 qd_variants.py：前 2 年 4 个半年度段 + 后 1 年 OOS）
SEGS = [
    ("IS_S1_2023H2", "20231001", "20240331"),
    ("IS_S2_2024H1", "20240401", "20240930"),
    ("IS_S3_2024H2", "20241001", "20250331"),
    ("IS_S4_2025H1", "20250401", "20250930"),
    ("OOS_2025H2to2026", "20251001", "20261013"),
]
OOS_NAME = "OOS_2025H2to2026"
OOS_LO, OOS_HI = "20251001", "20261013"
RECENT1Y_LO = "20251014"  # 近一年宽度分布窗口
RECENT1Y_HI = "20261013"


# ── 宽度序列 ────────────────────────────────────────────────
def build_breadth():
    """pivot+向量化：逐票 close>MA20 布尔 → 宽 DataFrame → 行均值。

    湖价 raw close（与 cross 环境闸门口径一致）；MA20 不足的日期不计入
    分母（只统计有 20 日历史的有效样本）。"""
    t0 = time.time()
    files = [
        fp
        for fp in sorted(glob.glob(str(ASHARE / "*.parquet")))
        if os.path.basename(fp)[:-8] not in INDEX_FILES
    ]
    print("[breadth] 池 %d 只（剔 %d 指数）" % (len(files), len(INDEX_FILES)))
    cols = {}
    for k, fp in enumerate(files):
        code = os.path.basename(fp)[:-8].replace("_", ".")
        try:
            df = pd.read_parquet(fp, columns=["trade_date", "close"])
        except Exception as e:
            print("[warn] %s 读取失败：%s" % (code, e))
            continue
        if len(df) < MA_WIN + 1:
            continue
        s = df.set_index("trade_date")["close"].astype(float).sort_index()
        ma = s.rolling(MA_WIN).mean()
        cols[code] = (s > ma).where(ma.notna())  # MA20 无值日 → NaN（不计分母）
        if (k + 1) % 1000 == 0:
            print(
                "[breadth] %d/%d 已算 %.0fs" % (k + 1, len(files), time.time() - t0),
                flush=True,
            )
    wide = pd.DataFrame(cols)  # 日期 × 股票 布尔/NaN
    n_stocks = wide.notna().sum(axis=1)
    breadth = wide.mean(axis=1)  # mean 自动跳过 NaN
    dates = [str(d)[:8] for d in breadth.index]
    payload = {
        "task": "W3-5b 全市场宽度序列（close>MA20 占比）",
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "params": {
            "ma_win": MA_WIN,
            "universe": "湖内全部 CN 股 %d 只（剔除 4 只指数，同 qd_backtest_1445 口径）"
            % len(cols),
            "price": "raw close（湖价，与 cross 环境闸门同口径）",
            "denominator": "当日有 20 日历史（MA20 有效）的股票数",
        },
        "dates": dates,
        "breadth": [
            round(float(x), 6) if pd.notna(x) else None for x in breadth.to_numpy()
        ],
        "n_stocks": [int(x) for x in n_stocks.to_numpy()],
        "elapsed_s": round(time.time() - t0, 1),
    }
    REPORTS.mkdir(exist_ok=True)
    BREADTH_JSON.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(
        "[breadth] %d 个交易日 %.0fs → %s"
        % (len(dates), time.time() - t0, BREADTH_JSON)
    )
    return {d: b for d, b in zip(dates, payload["breadth"]) if b is not None}


def load_breadth(force=False):
    if force or not BREADTH_JSON.exists():
        return build_breadth()
    raw = json.loads(BREADTH_JSON.read_text(encoding="utf-8"))
    m = {d: b for d, b in zip(raw["dates"], raw["breadth"]) if b is not None}
    print(
        "[breadth] 复用 %s（%d 日，%s ~ %s）"
        % (BREADTH_JSON.name, len(m), m and min(m), m and max(m))
    )
    return m


# ── 统计（同 qd_variants 口径）──────────────────────────────
def stats(rets):
    a = np.asarray([r for r in rets if r is not None], dtype=float)
    if len(a) == 0:
        return {"n": 0}
    gains, losses = a[a > 0], a[a < 0]
    pf = (
        float(gains.sum() / abs(losses.sum()))
        if losses.sum() != 0
        else (float("inf") if len(gains) else 0.0)
    )
    return {
        "n": int(len(a)),
        "win_rate": round(float((a > 0).mean()), 4),
        "pf": round(pf, 4),
        "mean": round(float(a.mean()), 5),
    }


def seg_of(date):
    for name, lo, hi in SEGS:
        if lo <= date <= hi:
            return name
    return None


def block(recs):
    """hold_5/10/20 ×（全窗 + 分段）。"""
    blk = {}
    for h in HOLDS:
        key = "r%d" % h
        blk["hold_%d" % h] = {
            "overall": stats([r[key] for r in recs if key in r]),
            "by_segment": {
                name: stats(
                    [r[key] for r in recs if key in r and seg_of(r["date"]) == name]
                )
                for name, _, _ in SEGS
            },
        }
    return blk


# ── 主流程 ──────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--build-breadth",
        action="store_true",
        help="强制重建宽度序列（默认文件存在即复用）",
    )
    args = ap.parse_args()

    t0 = time.time()
    breadth = load_breadth(force=args.build_breadth)

    # 逐信号明细 join 宽度（combo V1×V2 信号集 = hfq 明细剔除 rotation）
    raw = json.loads(DETAILS.read_text(encoding="utf-8"))
    recs_all = raw["records"]
    recs = [r for r in recs_all if r["regime"] != "rotation"]
    no_breadth = [r for r in recs if r["date"] not in breadth]
    recs = [r for r in recs if r["date"] in breadth]
    for r in recs:
        r["breadth"] = breadth[r["date"]]
    print(
        "[load] hfq 明细 %d 条 → 剔 rotation %d 条 → 无宽度日 %d 条（剔除）"
        % (len(recs_all), len(recs) + len(no_breadth), len(no_breadth))
    )

    # 对照行：无门控 combo（应复现 W3-5 hold_20 PF 1.184）
    control_nogate = block(recs)
    chk = control_nogate["hold_20"]["overall"]
    print(
        "[check] 无门控 combo hold_20：n=%d PF=%.3f（W3-5 基准 3943/1.184）"
        % (chk.get("n", 0), chk.get("pf", 0))
    )

    # 门控网格：θ ∈ {40%,45%,50%}
    grid = {}
    for th in THETAS:
        kept = [r for r in recs if r["breadth"] >= th]
        grid["theta_%d" % round(th * 100)] = {
            "theta": th,
            "days_pass": int(sum(1 for d, b in breadth.items() if b >= th)),
            **block(kept),
        }

    # 判据对照
    def level_ok(cell):
        o = cell["hold_20"]["overall"]
        oos = cell["hold_20"]["by_segment"][OOS_NAME]
        c1 = bool(
            oos.get("n", 0) and oos.get("pf", 0) >= 1.0 and oos.get("mean", -1) > 0
        )
        c2 = bool(o.get("pf", 0) >= 1.2)
        return {
            "oos_pf": oos.get("pf"),
            "oos_mean": oos.get("mean"),
            "oos_pass": c1,
            "full_pf": o.get("pf"),
            "full_pass": c2,
            "level_pass": bool(c1 and c2),
        }

    levels = {k: level_ok(v) for k, v in grid.items()}
    flags = [levels["theta_%d" % round(t * 100)]["level_pass"] for t in THETAS]
    n_pass = int(sum(flags))
    # 平台：相邻档（40/45、45/50）同向达标
    adjacent = [(flags[i] and flags[i + 1]) for i in range(len(flags) - 1)]
    platform = bool(any(adjacent))
    best = None
    for k, lv in levels.items():
        if lv["level_pass"] and (
            best is None
            or grid[k]["hold_20"]["overall"]["pf"]
            > grid[best]["hold_20"]["overall"]["pf"]
        ):
            best = k
    verdict = {
        "c1_oos_positive": {k: levels[k]["oos_pass"] for k in levels},
        "c2_full_pf_ge_1.2": {k: levels[k]["full_pass"] for k in levels},
        "c3_platform": {
            "levels_pass": n_pass,
            "adjacent_pairs_pass": adjacent,
            "platform": platform,
            "note": "相邻档同向达标才算平台；单点达标=尖峰",
        },
        "best_level": best,
        "all_pass": bool(n_pass >= 2 and platform),
        "rule": "①OOS PF>=1.0 且回正 ②全窗 hold_20 PF>=1.2 ③θ 平台>=2 档达标",
    }

    # 宽度序列概况（近一年验证弱市）
    bser = sorted(breadth.items())

    def bdist(lo, hi):
        vals = np.array([b for d, b in bser if lo <= d <= hi])
        if len(vals) == 0:
            return {}
        return {
            "days": int(len(vals)),
            "mean": round(float(vals.mean()), 4),
            "median": round(float(np.median(vals)), 4),
            "p10": round(float(np.percentile(vals, 10)), 4),
            "p90": round(float(np.percentile(vals, 90)), 4),
            "pct_days_below_40": round(float((vals < 0.40).mean()), 4),
            "pct_days_below_45": round(float((vals < 0.45).mean()), 4),
            "pct_days_below_50": round(float((vals < 0.50).mean()), 4),
        }

    breadth_summary = {
        "full_window": bdist(bser[0][0], bser[-1][0]),
        "recent_1y": bdist(RECENT1Y_LO, RECENT1Y_HI),
        "by_segment": {name: bdist(lo, hi) for name, lo, hi in SEGS},
    }

    payload = {
        "task": "W3-5b 任务A 量子双刀强市门控（宽度闸门）变体离线验证",
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "params": {
            "signal_universe": "qd_details_hfq.json 剔 rotation（combo V1×V2 信号集）",
            "gate": "信号日 breadth_t >= θ 才放行（θ∈{40%,45%,50%}）",
            "breadth_caliber": "湖 raw close > MA20(20日) 全市场占比（剔 4 指数），"
            "与 cross 环境闸门同口径",
            "approx_note": "宽度按 T 日收盘计算近似 14:45 时点宽度：尾盘拉升日 "
            "14:45 真实宽度略低于收盘宽度，临界日回测可能放行实盘"
            "不达标日 → 门控效果略偏乐观（偏差量级小，全市场均值摊薄）",
            "signals_dropped_no_breadth": len(no_breadth),
            "segments": {name: [lo, hi] for name, lo, hi in SEGS},
        },
        "breadth_summary": breadth_summary,
        "control_no_gate": control_nogate,
        "grid": grid,
        "level_checks": levels,
        "criteria": verdict,
        "elapsed_s": round(time.time() - t0, 1),
        "disclaimer": "离线研究回测：结果不构成选股依据；影子验证前不下产线",
    }
    OUT_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print("[done] %.0fs → %s" % (time.time() - t0, OUT_JSON))

    # 控制台摘要
    print(
        "\n[宽度概况] 近一年：%s"
        % json.dumps(breadth_summary["recent_1y"], ensure_ascii=False)
    )
    print("\n[门控网格 hold_20 总览] n / 胜率 / PF / 均收益（全窗）")
    rows = [("no_gate", control_nogate)] + [
        ("theta_%d" % round(t * 100), grid["theta_%d" % round(t * 100)]) for t in THETAS
    ]
    for tag, cell in rows:
        s = cell["hold_20"]["overall"]
        oos = cell["hold_20"]["by_segment"][OOS_NAME]
        lv = levels.get(tag, {}).get("level_pass", "-")
        print(
            "  %-9s 全窗 n=%-5d win=%.1f%% PF=%.3f mean=%+.2f%% | "
            "OOS n=%-5d PF=%.3f mean=%+.2f%% | 达标=%s"
            % (
                tag,
                s.get("n", 0),
                s.get("win_rate", 0) * 100,
                s.get("pf", 0),
                s.get("mean", 0) * 100,
                oos.get("n", 0),
                oos.get("pf", 0),
                oos.get("mean", 0) * 100,
                lv,
            )
        )
    print("[verdict]", json.dumps(verdict, ensure_ascii=False))


if __name__ == "__main__":
    main()
