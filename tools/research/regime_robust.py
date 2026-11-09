# -*- coding: utf-8 -*-
"""「越弱越涨」到底是规律还是样本幻觉 —— 四道检验。

`regime_criteria.py` 的结果全部反向：中枢下移 +5.73%、均线空头 +4.01%、
跌破中枢 +3.00%、20 日跌超 10% 后 +5.39%。四个判据、三个维度都指向
「越弱越涨」，看起来像铁证。

但它可能只是三件事的伪装：
  A. 样本期恰好是震荡市（2024-07~2026-10），震荡市里反转天然强
  B. 极值组样本集中在少数暴跌后的反弹窗，是幸存者偏差不是规律
  C. 20 日这个视界特有，换视界就没了

三道检验逐一排除：
  1. 分年稳定性    —— 每年都反向，才是规律；某一年反向，就是那年的行情
  2. 时间集中度    —— 最弱档的日子是否挤在少数几个窗口
  3. 视界敏感性    —— 5 / 10 / 20 / 60 日是否一致
  4. 极端行情鲁棒  —— 剔除最大反弹窗后还剩多少

第 4 道最关键：如果剔除 2024-09 那种暴力反弹后规律消失，那这个「势」
就是「赌反弹」，一旦遇到单边下跌会把本金赔光。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (os.path.join(ROOT, "quant_backtest"), HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import chanlun_regime as RG  # noqa: E402


def collect(code, asof, lookback, horizons=(5, 10, 20, 60)):
    bars = RG._load_index(code, asof)
    lo = max(140, len(bars) - lookback)
    recs = []
    for j in range(lo, len(bars)):
        st = RG.structure_state(bars[: j + 1])
        if not st:
            continue
        r = {"i": j, "dt": st["asof"], "px": st["px"],
             "pv_dir": st["pv_dir"], "zone": st["zone"],
             "ma_align": None, "qpos60": st["qpos60"]}
        if st["ma20"] and st["ma60"]:
            px, ma20, ma60 = st["px"], st["ma20"], st["ma60"]
            r["ma_align"] = ("多" if px > ma20 > ma60 else
                             ("空" if px < ma20 < ma60 else "交"))
        for h in horizons:
            r["f%d" % h] = (bars[j + h]["c"] / bars[j]["c"] - 1
                            if j + h < len(bars) else None)
        recs.append(r)
    return bars, recs


def grp(recs, key, target, h):
    v = [r["f%d" % h] for r in recs if r.get(key) == target and r.get("f%d" % h) is not None]
    return np.asarray(v, float)


def tt(v):
    if len(v) < 3 or v.std(ddof=1) == 0:
        return 0.0
    return float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="000985.CSI")
    ap.add_argument("--asof", default="20261106")
    ap.add_argument("--lookback", type=int, default=650)
    ap.add_argument("--json", default=os.path.join(ROOT, "portal", "regime_robust.json"))
    args = ap.parse_args()

    bars, recs = collect(args.code, args.asof.replace("-", ""), args.lookback)
    print("=" * 80)
    print("「越弱越涨」稳健性检验 · %s · %s ~ %s · n=%d"
          % (args.code, recs[0]["dt"], recs[-1]["dt"], len(recs)))
    print("=" * 80)

    out = {}

    # ---- 检验 1：分年稳定性 ----
    print("\n[1] 分年稳定性：±0.46%/−0.13%/… 这种符号会不会年年一致")
    print("  %-8s %-22s %-22s" % ("年份", "中枢下移 − 上移（20日）", "空头 − 多头（20日）"))
    by_year = {}
    for y in sorted({r["dt"][:4] for r in recs}):
        sub = [r for r in recs if r["dt"][:4] == y]
        dn, up_ = grp(sub, "pv_dir", -1, 20), grp(sub, "pv_dir", 1, 20)
        kk, bb = grp(sub, "ma_align", "空", 20), grp(sub, "ma_align", "多", 20)
        d1 = (dn.mean() - up_.mean()) * 100 if len(dn) >= 5 and len(up_) >= 5 else None
        d2 = (kk.mean() - bb.mean()) * 100 if len(kk) >= 5 and len(bb) >= 5 else None
        by_year[y] = {"pv_diff": round(d1, 2) if d1 is not None else None,
                      "ma_diff": round(d2, 2) if d2 is not None else None,
                      "n": len(sub)}
        print("  %-8s %-22s %-22s"
              % (y,
                 ("%+.2fpp (n=%d/%d)" % (d1, len(dn), len(up_))) if d1 is not None else "样本不足",
                 ("%+.2fpp (n=%d/%d)" % (d2, len(kk), len(bb))) if d2 is not None else "样本不足"))
    signs = [v["pv_diff"] for v in by_year.values() if v["pv_diff"] is not None]
    same = all(s > 0 for s in signs) or all(s < 0 for s in signs)
    print("  → 中枢方向差逐年同号：%s（%s）"
          % ("是" if same else "否", "、".join("%+.2f" % s for s in signs)))
    out["by_year"] = by_year
    out["year_sign_consistent"] = same

    # ---- 检验 2：时间集中度 ----
    print("\n[2] 时间集中度：最弱档的日子是不是挤在少数窗口")
    weak = [r for r in recs if r["pv_dir"] == -1 and r["f20"] is not None]
    print("  「中枢下移」共 %d 天，分布在 %d 个自然月"
          % (len(weak), len({r["dt"][:6] for r in weak})))
    mon = {}
    for r in weak:
        mon.setdefault(r["dt"][:6], []).append(r["f20"])
    rows = sorted(((k, len(v), np.mean(v) * 100) for k, v in mon.items()),
                  key=lambda x: -x[1])
    print("  最集中的 6 个月：")
    for k, n, m in rows[:6]:
        print("    %s  %2d 天  20日均 %+7.2f%%" % (k, n, m))
    top3 = sum(n for _, n, _ in rows[:3])
    print("  前 3 个月占 %.0f%%（%d/%d）"
          % (top3 / len(weak) * 100, top3, len(weak)))
    out["weak_months"] = [{"month": k, "n": n, "mean_pct": round(m, 2)}
                          for k, n, m in rows]
    out["weak_top3_share_pct"] = round(top3 / len(weak) * 100, 1)

    # ---- 检验 3：视界敏感性 ----
    print("\n[3] 视界敏感性：换个视界还在不在")
    print("  %-8s %9s %9s %9s" % ("视界", "下移−上移", "空头−多头", "below−above"))
    hz = {}
    for h in (5, 10, 20, 60):
        d1 = (grp(recs, "pv_dir", -1, h).mean() - grp(recs, "pv_dir", 1, h).mean()) * 100
        d2 = (grp(recs, "ma_align", "空", h).mean() - grp(recs, "ma_align", "多", h).mean()) * 100
        d3 = (grp(recs, "zone", "below", h).mean() - grp(recs, "zone", "above", h).mean()) * 100
        hz[h] = {"pv": round(d1, 2), "ma": round(d2, 2), "zone": round(d3, 2)}
        print("  %-8s %+8.2fpp %+8.2fpp %+8.2fpp" % ("%d日" % h, d1, d2, d3))
    out["by_horizon"] = hz

    # ---- 检验 4：极端行情鲁棒 ----
    print("\n[4] 极端行情鲁棒：剔掉最大的几个反弹窗还剩多少")
    # 按 20 日收益排序，找最集中的反弹窗（以 10 个交易日为一窗对齐）
    win = {}
    for r in weak:
        k = r["i"] // 10
        win.setdefault(k, []).append(r["f20"])
    wins = sorted(((k, np.mean(v), len(v)) for k, v in win.items()), key=lambda x: -x[1])
    print("  中枢下移档里 20 日收益最高的 5 个十天窗：")
    drop_idx = set()
    for k, m, n in wins[:5]:
        days = [r["dt"] for r in weak if r["i"] // 10 == k]
        print("    %s 起 %d 天  20日均 %+7.2f%%" % (min(days), n, m * 100))
        drop_idx |= {r["i"] for r in weak if r["i"] // 10 == k}
    kept = np.asarray([r["f20"] for r in weak if r["i"] not in drop_idx], float)
    full = np.asarray([r["f20"] for r in weak], float)
    print("  全部   n=%d  均 %+.2f%%  t=%+.2f" % (len(full), full.mean() * 100, tt(full)))
    print("  剔顶5窗 n=%d  均 %+.2f%%  t=%+.2f" % (len(kept), kept.mean() * 100, tt(kept)))
    survive = kept.mean() > 0 and tt(kept) > 1.5
    print("  → 剔除后是否仍有正超额与显著性：%s" % ("是" if survive else "**否**"))
    out["weak_full"] = {"n": int(len(full)), "mean_pct": round(float(full.mean()) * 100, 3),
                        "t": round(tt(full), 2)}
    out["weak_drop_top5"] = {"n": int(len(kept)), "mean_pct": round(float(kept.mean()) * 100, 3),
                             "t": round(tt(kept), 2), "survive": bool(survive)}

    # ---- 检验 5：最大回撤视角（最弱档是不是在接飞刀）----
    print("\n[5] 接飞刀风险：中枢下移档买入后，20 日内的最差路径")
    worst = []
    for r in recs:
        if r["pv_dir"] != -1:
            continue
        j = r["i"]
        if j + 20 >= len(bars):
            continue
        path = [bars[j + k]["c"] / bars[j]["c"] - 1 for k in range(1, 21)]
        worst.append(min(path))
    worst = np.asarray(worst, float)
    print("  中枢下移档 n=%d：20 日内最大浮亏 均 %.2f%%  中位 %.2f%%  P10 %.2f%%  最差 %.2f%%"
          % (len(worst), worst.mean() * 100, np.median(worst) * 100,
             np.percentile(worst, 10) * 100, worst.min() * 100))
    out["weak_path_maxdd"] = {"n": int(len(worst)),
                              "mean_pct": round(float(worst.mean()) * 100, 2),
                              "median_pct": round(float(np.median(worst)) * 100, 2),
                              "p10_pct": round(float(np.percentile(worst, 10)) * 100, 2),
                              "worst_pct": round(float(worst.min()) * 100, 2)}

    print("\n" + "=" * 80)
    print("结论口径：以上四项只要有一项不过，就不能把「越弱越涨」当择势规则用。")
    print("=" * 80)

    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("→ %s" % args.json)


if __name__ == "__main__":
    main()
