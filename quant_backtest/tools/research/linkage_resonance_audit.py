# -*- coding: utf-8 -*-
"""K35 三线联动 · 信号③ 共振底挂账组合论证（2026-10-14 linkage-dev，只读）。

背景：③ 历史回放 T+5/10/20 命中 66.6%/74.7%/87.6%（PF 2.6→19.9），双口径
全过线；遗留风险#1 指出 episode 样本高度相关（87.6% 或主要来自同几轮大跌
反弹潮）。本脚本补三块论证：

1. 时间分块稳健性：按半年度（2024H2..2026H2）分块，各块
   样本数/命中率/PF（口径A 绝对收益，T+5/10/20）。
   判"块间不塌方"：≥4/5 块命中率≥55% 且 无单块 PF<0.5。
2. 独立事件计数（集中度）：全市场层面把信号日按「相邻间隔>10 交易日」
   合并为"独立反弹潮"（同潮=同一轮大跌反弹，跨潮才算独立证据）。
   统计潮数、每潮样本/收益贡献；前 3 潮贡献 >60% 总收益 → 集中度风险标注。
3. 规模现实性：单日信号只数分布（P50/P95/最大，活跃日口径）；
   单日 >50 只 → "普跌反弹"而非选股（当日建议不开新仓）。

挂账三判据（全过才"够格挂账"）：
  · 块间不塌方（主持有期 T+20 与 T+10 两档均过）
  · 独立潮数 ≥5
  · 规模可控：单日 P95 ≤20 只

数据与判定复用 linkage_hitrate_backtest（load_frames/judge_matrix/
episode_starts，与产线 judge_series 逐条对齐），本脚本零新口径。

用法:
  python linkage_resonance_audit.py                       # 全量论证
  python linkage_resonance_audit.py --out reports/linkage_resonance_audit.json
"""

import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from linkage_hitrate_backtest import (  # noqa: E402
    HORIZONS,
    judge_matrix,
    episode_starts,
    load_frames,
)

SIG = "3"
WAVE_GAP = 10  # 交易日间隔 ≤10 视为同一反弹潮
BROAD_DAY = 50  # 单日信号超此只数 → 普跌反弹日
SCALE_P95_CAP = 20  # 规模判据：单日 P95 ≤ 20 只
WAVE_MIN = 5  # 独立潮数判据
BLOCK_HITRATE = 0.55  # 块命中率线
BLOCK_PF_FLOOR = 0.5  # 块 PF 下限


def half_label(dt):
    """'20260913' → '2026H2'。"""
    y = int(str(dt)[:4])
    m = int(str(dt)[4:6])
    return "%dH%d" % (y, 1 if m <= 6 else 2)


def block_stats(starts, fwd):
    """按半年度分块统计。starts=bool_df（episode 首日），fwd=前向收益 df。"""
    rows = []
    for code in starts.columns:
        col = starts[code]
        if not col.any():
            continue
        for dt in col.index[col]:
            v = fwd.at[dt, code]
            if pd.isna(v):
                continue
            rows.append((half_label(dt), float(v)))
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=["blk", "ret"])
    out = {}
    for blk, g in df.groupby("blk"):
        n = len(g)
        wins = int((g["ret"] > 0).sum())
        gp = float(g.loc[g["ret"] > 0, "ret"].sum())
        gl = float(-g.loc[g["ret"] < 0, "ret"].sum())
        out[blk] = {
            "n": n,
            "hits": wins,
            "hitrate": round(wins / n, 4) if n else None,
            "avg_ret": round(float(g["ret"].mean()), 5),
            "pf": round(gp / gl, 3) if gl > 0 else None,
        }
    return dict(sorted(out.items()))


def block_pass(blocks):
    """块间不塌方判定：≥4/5 块命中率≥55% 且 无单块 PF<0.5（有样本的块）。"""
    named = [
        b for b in ("2024H2", "2025H1", "2025H2", "2026H1", "2026H2") if b in blocks
    ]
    hit_ok = sum(
        1
        for b in named
        if blocks[b]["hitrate"] is not None and blocks[b]["hitrate"] >= BLOCK_HITRATE
    )
    pf_bad = [
        b
        for b in named
        if blocks[b]["pf"] is not None and blocks[b]["pf"] < BLOCK_PF_FLOOR
    ]
    return {
        "pass": bool(hit_ok >= 4 and not pf_bad),
        "pass_blocks": hit_ok,
        "n_blocks": len(named),
        "pf_below_floor": pf_bad,
    }


def wave_stats(starts, fwd, k):
    """市场级独立反弹潮：信号日按间隔>WAVE_GAP 交易日切段。
    返回 (潮列表, top3 收益贡献占比)。贡献口径=潮内 episode 前向收益之和
    占全样本总收益比。"""
    dates = sorted(
        {dt for code in starts.columns for dt in starts[code].index[starts[code]]}
    )
    if not dates:
        return [], None, 0
    ret_of = {}
    for code in starts.columns:
        col = starts[code]
        if not col.any():
            continue
        for dt in col.index[col]:
            v = fwd.at[dt, code]
            if not pd.isna(v):
                ret_of[(dt, code)] = float(v)
    total = sum(ret_of.values())

    # 切潮
    waves, cur = [], [dates[0]]
    for d in dates[1:]:
        gap = starts.index.get_loc(d) - starts.index.get_loc(cur[-1])
        if gap > WAVE_GAP:
            waves.append(cur)
            cur = [d]
        else:
            cur.append(d)
    waves.append(cur)

    out = []
    for i, wdates in enumerate(waves, 1):
        items = [(dt, c) for (dt, c) in ret_of if dt in set(wdates)]
        rets = [ret_of[(dt, c)] for dt, c in items]
        n = len(rets)
        out.append(
            {
                "wave": i,
                "start": str(wdates[0])[:10],
                "end": str(wdates[-1])[:10],
                "n": n,
                "hitrate": round(sum(1 for r in rets if r > 0) / n, 4) if n else None,
                "profit": round(sum(rets), 4),
                "profit_share": round(sum(rets) / total, 4) if total > 0 else None,
            }
        )
    top3 = sum(w["profit"] for w in sorted(out, key=lambda w: -w["profit"])[:3])
    return out, (round(top3 / total, 4) if total > 0 else None), total


def daily_scale(starts):
    """单日信号只数分布（活跃日=当日≥1只）。"""
    per_day = starts.sum(axis=1)
    per_day = per_day[per_day > 0]
    if per_day.empty:
        return {
            "active_days": 0,
            "p50": None,
            "p95": None,
            "max": 0,
            "days_gt50": 0,
            "pass": False,
        }
    return {
        "active_days": int(len(per_day)),
        "p50": float(np.percentile(per_day, 50)),
        "p95": float(np.percentile(per_day, 95)),
        "max": int(per_day.max()),
        "days_gt50": int((per_day > BROAD_DAY).sum()),
        "pass": bool(np.percentile(per_day, 95) <= SCALE_P95_CAP),
    }


def run():
    stock, block, idx, uni = load_frames()
    sig = judge_matrix(stock, block, idx, uni)
    starts = episode_starts(sig[SIG])
    fwd = {k: stock.shift(-k) / stock - 1 for k in HORIZONS}

    blocks = {k: block_stats(starts, fwd[k]) for k in HORIZONS}
    bpass = {k: block_pass(blocks[k]) for k in HORIZONS}
    waves, top3_share, total = wave_stats(starts, fwd[HORIZONS[-1]], HORIZONS[-1])
    scale = daily_scale(starts)

    block_ok = bool(bpass.get(20, {}).get("pass")) and bool(
        bpass.get(10, {}).get("pass")
    )
    waves_ok = len(waves) >= WAVE_MIN
    scale_ok = bool(scale["pass"])
    eligible = bool(block_ok and waves_ok and scale_ok)

    return {
        "signal": SIG,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "criteria": {
            "block_hitrate_line": BLOCK_HITRATE,
            "block_pf_floor": BLOCK_PF_FLOOR,
            "wave_gap_days": WAVE_GAP,
            "wave_min": WAVE_MIN,
            "scale_p95_cap": SCALE_P95_CAP,
            "broad_day_threshold": BROAD_DAY,
        },
        "blocks": {str(k): v for k, v in blocks.items()},
        "block_pass": {str(k): v for k, v in bpass.items()},
        "waves": {
            "definition": "全市场信号日按相邻间隔>%d交易日切潮" % WAVE_GAP,
            "count": len(waves),
            "total_profit": round(total, 4),
            "top3_profit_share": top3_share,
            "concentration_flag": bool(top3_share is not None and top3_share > 0.60),
            "list": waves,
        },
        "daily": scale,
        "verdict": {
            "block_ok": block_ok,
            "waves_ok": waves_ok,
            "scale_ok": scale_ok,
            "eligible": eligible,
        },
    }


def _pct(x):
    return "-" if x is None else "%.1f%%" % (x * 100)


def _pf(x):
    return "-" if x is None else "%.2f" % x


def report(r):
    L = [
        "== 信号③ 共振底 · 挂账组合论证（时间分块/独立潮/规模）==",
        "判据：块间不塌方(≥4/5块命中≥%.0f%%且无PF<%.1f) + 独立潮≥%d + "
        "单日P95≤%d只，全过才够格挂账"
        % (BLOCK_HITRATE * 100, BLOCK_PF_FLOOR, WAVE_MIN, SCALE_P95_CAP),
    ]
    L.append("-- 半年度分块（口径A 绝对收益）--")
    for k in HORIZONS:
        L.append(" T+%d：" % k)
        for blk, b in r["blocks"][str(k)].items():
            L.append(
                "   %s  n=%-6d 命中=%-6s PF=%-6s 均收=%+.2f%%"
                % (blk, b["n"], _pct(b["hitrate"]), _pf(b["pf"]), b["avg_ret"] * 100)
            )
        bp = r["block_pass"][str(k)]
        L.append(
            "   → %s（%d/%d 块过线，PF塌方块=%s）"
            % (
                "过" if bp["pass"] else "不过",
                bp["pass_blocks"],
                bp["n_blocks"],
                bp["pf_below_floor"] or "无",
            )
        )
    w = r["waves"]
    L.append("-- 独立反弹潮（%s）--" % w["definition"])
    L.append(
        " 潮数=%d（判据≥%d：%s）｜总收益=%.1f%%｜前3潮贡献=%s%s"
        % (
            w["count"],
            WAVE_MIN,
            "过" if r["verdict"]["waves_ok"] else "不过",
            (w["total_profit"] or 0) * 100,
            _pct(w["top3_profit_share"]),
            " ⚠集中度风险(>60%)" if w["concentration_flag"] else "",
        )
    )
    for x in w["list"]:
        L.append(
            "   潮%-2d %s~%s  n=%-6d 命中=%-6s 收益贡献=%s"
            % (
                x["wave"],
                x["start"],
                x["end"],
                x["n"],
                _pct(x["hitrate"]),
                _pct(x["profit_share"]),
            )
        )
    d = r["daily"]
    L.append("-- 规模现实性（活跃日 %d 天）--" % d["active_days"])
    L.append(
        " 单日只数 P50=%.0f P95=%.0f 最大=%d ｜>50只日=%d 天 → %s"
        % (
            d["p50"] or 0,
            d["p95"] or 0,
            d["max"],
            d["days_gt50"],
            "过" if d["pass"] else "不过",
        )
    )
    v = r["verdict"]
    L.append("-- 判定 --")
    L.append(
        " 块间=%s ｜ 独立潮=%s ｜ 规模=%s → 【%s】"
        % (
            "过" if v["block_ok"] else "不过",
            "过" if v["waves_ok"] else "不过",
            "过" if v["scale_ok"] else "不过",
            "够格挂账" if v["eligible"] else "暂缓",
        )
    )
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="信号③挂账组合论证（只读）")
    ap.add_argument(
        "--out", default=os.path.join(HERE, "reports", "linkage_resonance_audit.json")
    )
    a = ap.parse_args()
    r = run()
    print(report(r))
    if a.out:
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
        print("明细 → %s" % a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
