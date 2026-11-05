# -*- coding: utf-8 -*-
"""沪金 b3 超额检验：把「黄金牛市 beta」从信号收益里剥掉。

`chanlun_gold_b3_verify.py` 得出 b3 实时口径 d20 毛 +1.85%（t=2.49）。
但 2008-2026 沪金是单边大牛市（约 5 倍），任何买点都会赚——必须回答：
这 +1.85% 里有多少是信号本身，有多少只是「恰好买在上涨市场里」。

三种基准，由宽到严：
  B1 全样本无条件均值 —— 同 K226 的 beta 表口径
  B2 局部基准（入场日 ±250 交易日）—— 消除长期趋势与 regime
  B3 同期同类型反向信号 —— 若卖点同样赚钱，说明是市场不是信号

另外做三件校正：
  · 分年贡献（是否单一年份撑起）
  · 分半年（是否单一 regime 撑起）
  · 多重比较（本次共扫 6 类型×2 口径×5 持期 = 60 格，Bonferroni α=0.05/60）

用法
----
    python chanlun_gold_b3_bench.py [--window 250] [--out data/chanlun_gold_b3_bench.json]
"""

from __future__ import print_function

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "data", "chanlun_gold_b3.json")
BARS = os.path.join(HERE, "data", "_gold_bars.json")
HOLDS = (1, 3, 5, 10, 20)


def _stats(xs):
    n = len(xs)
    if n == 0:
        return {"n": 0}
    mu = sum(xs) / n
    srt = sorted(xs)
    med = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2.0
    win = sum(1 for x in xs if x > 0) * 100.0 / n
    if n < 2:
        return {"n": n, "avg": round(mu, 3), "med": round(med, 3), "win": round(win, 1)}
    var = sum((x - mu) ** 2 for x in xs) / (n - 1)
    se = (var ** 0.5) / n ** 0.5
    return {"n": n, "avg": round(mu, 3), "med": round(med, 3), "win": round(win, 1),
            "sd": round(var ** 0.5, 3), "se": round(se, 3),
            "t": round(mu / se, 2) if se > 0 else None}


def _boot_ci(xs, iters=4000, alpha=0.05, seed=42):
    import random
    n = len(xs)
    if n < 5:
        return None
    rng = random.Random(seed)
    mus = []
    for _ in range(iters):
        s = 0.0
        for _ in range(n):
            s += xs[rng.randrange(n)]
        mus.append(s / n)
    mus.sort()
    return [round(mus[int(iters * alpha / 2)], 3), round(mus[int(iters * (1 - alpha / 2))], 3)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=250)
    ap.add_argument("--out", default=os.path.join(HERE, "data", "chanlun_gold_b3_bench.json"))
    a = ap.parse_args()

    src = json.load(open(SRC, encoding="utf-8"))
    b = json.load(open(BARS, encoding="utf-8"))
    dts, O, C = b["dts"], b["o"], b["c"]
    N = len(dts)
    idx = {d: i for i, d in enumerate(dts)}

    # 全样本无条件 h 日收益（每一根 bar 都可以是入场点）
    uncond = {}
    for h in HOLDS:
        xs = []
        for i in range(N - h):
            if O[i] and C[i + h]:
                xs.append((C[i + h] / O[i] - 1.0) * 100.0)
        uncond[h] = _stats(xs)

    def local_bench(h, center, win):
        """入场日 ±win 交易日内所有 bar 的 h 日收益均值（局部 regime 基准）。"""
        lo = max(0, center - win)
        hi = min(N - h, center + win)
        xs = [(C[i + h] / O[i] - 1.0) * 100.0 for i in range(lo, hi)
              if O[i] and C[i + h]]
        return sum(xs) / len(xs) if xs else None

    out = {"generated_at": src.get("generated_at"), "window": a.window,
           "uncond": {str(h): v for h, v in uncond.items()}, "types": {}}

    print("== 全样本无条件基准（黄金自身 beta）==")
    for h in HOLDS:
        u = uncond[h]
        print("  d%-2d  avg %+6.2f%%  胜率 %5.1f%%  n=%d" % (h, u["avg"], u["win"], u["n"]))
    print()

    for typ in ("b3", "s3", "b1", "b2", "s1", "s2"):
        cell = (src.get("by_type") or {}).get(typ)
        if not cell or "live" not in cell:
            continue
        rows = cell["live"]["entries"]
        if len(rows) < 5:
            continue
        res = {"n": len(rows), "holds": {}}
        for h in HOLDS:
            gross, ex_all, ex_loc = [], [], []
            for r in rows:
                v = r.get("d%d" % h)
                if v is None:
                    continue
                i = idx.get(r["entry_date"])
                if i is None:
                    continue
                gross.append(v)
                ex_all.append(v - uncond[h]["avg"])
                lb = local_bench(h, i, a.window)
                if lb is not None:
                    ex_loc.append(v - lb)
            res["holds"][str(h)] = {
                "gross": _stats(gross),
                "ex_uncond": _stats(ex_all),
                "ex_local": _stats(ex_loc),
                "ci_ex_local": _boot_ci(ex_loc),
            }
        out["types"][typ] = res

        print("== %s  (n=%d, 实时可见口径) ==" % (typ, len(rows)))
        print("  %-4s %-26s %-26s %s"
              % ("持期", "毛收益", "超额 vs 无条件", "超额 vs 局部±%d日" % a.window))
        for h in HOLDS:
            hc = res["holds"][str(h)]
            g, ea, el = hc["gross"], hc["ex_uncond"], hc["ex_local"]
            print("  d%-3d %+6.2f%% w%5.1f%% t=%5s    %+6.2f%% t=%5s    %+6.2f%% t=%5s CI=%s"
                  % (h, g.get("avg", 0), g.get("win", 0), g.get("t"),
                     ea.get("avg", 0), ea.get("t"),
                     el.get("avg", 0), el.get("t"), hc["ci_ex_local"]))
        print()

    # ---- b3 深度拆分 ----
    b3 = (src.get("by_type") or {}).get("b3", {}).get("live", {}).get("entries") or []
    if b3:
        print("== b3 分年 / 分半年（d20 毛收益）==")
        byy = {}
        for r in b3:
            v = r.get("d20")
            if v is None:
                continue
            y = r["entry_date"][:4]
            byy.setdefault(y, []).append(v)
        for y in sorted(byy):
            xs = byy[y]
            print("  %s  n=%2d  avg %+6.2f%%  win %5.1f%%"
                  % (y, len(xs), sum(xs) / len(xs), sum(1 for x in xs if x > 0) * 100.0 / len(xs)))

        # 半年
        byh = {}
        for r in b3:
            v = r.get("d20")
            if v is None:
                continue
            k = r["entry_date"][:4] + "H" + ("1" if int(r["entry_date"][5:7]) <= 6 else "2")
            byh.setdefault(k, []).append(v)
        pos = sum(1 for k, xs in byh.items() if sum(xs) / len(xs) > 0)
        print("  半年窗共 %d 个，正收益 %d 个（%d/%d）"
              % (len(byh), pos, pos, len(byh)))

        # 多头市场占比：b3 入场日前 250 日黄金涨跌
        up = 0
        for r in b3:
            i = idx.get(r["entry_date"])
            if i is None or i < 250:
                continue
            if C[i] > C[i - 250]:
                up += 1
        tot = sum(1 for r in b3 if idx.get(r["entry_date"], 0) >= 250)
        print("  b3 入场前 250 日黄金上涨的比例: %d/%d = %.0f%%" % (up, tot, up * 100.0 / max(1, tot)))

        # 多重比较
        print("\n== 多重比较校正 ==")
        n_tests = 60  # 6 类型 × 2 口径 × 5 持期
        import math
        # Bonferroni：α=0.05/60 → 双侧 z 阈值
        alpha = 0.05 / n_tests
        # 用正态近似求临界 z（无 scipy 时足够）
        z = 3.0
        for cand in [2.5, 2.8, 3.0, 3.2, 3.5, 3.9, 4.2]:
            # P(|Z|>cand) ≈ 2*(1-Φ(cand))，用 erf 近似
            p = 2 * (1 - 0.5 * (1 + math.erf(cand / math.sqrt(2))))
            if p <= alpha:
                z = cand
                break
        print("  共扫 %d 格 → Bonferroni α=%.5f → 临界 |t| ≈ %.2f" % (n_tests, alpha, z))
        h20 = (out["types"].get("b3") or {}).get("holds", {}).get("20", {})
        for k in ("gross", "ex_uncond", "ex_local"):
            t = (h20.get(k) or {}).get("t")
            if t is None:
                continue
            verdict = "过" if abs(t) >= z else "不过"
            print("  b3 d20 %-10s t=%5s  → %s（普通 1.96 门槛下%s）"
                  % (k, t, verdict, "过" if abs(t) >= 1.96 else "不过"))

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\n-> %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
