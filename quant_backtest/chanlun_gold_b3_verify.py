# -*- coding: utf-8 -*-
"""沪金 b3/s3 信号：事后重构 vs 实时可见 —— 前视偏差量化与净收益检验。

背景
----
`gold_chanlun_shadow.py`（K227，每日 18:30）用 `chanlun_bi.analyze`（A 口径）
在连续沪金日线上做 3买/3卖 影子标注。K226 的事后统计给出 b3 n=33、d5 胜率
97.0%、超额 +2.03%，是缠论全部实验里唯一看起来有正 edge 的格子。

但 A 股审计（v6 档1）已证明 A 口径有 44% 的信号在逐日截断重放后消失——
原因是笔终点与 MACD 面积会随后续行情修正，事后看「成功回踩」才标成 b3。
b3 的定义恰恰依赖回踩是否成立，因此 97% 胜率高度可疑。

本脚本回答三件事：
  1. 全历史 75 个信号中，有多少在实时能被看到（A 口径前视比例）
  2. b3 的收益在「实时可见日入场」口径下还剩多少（与事后口径对比）
  3. 扣黄金期货真实成本后，实时口径的净收益是否统计显著

口径
----
- 事后口径（现行 shadow 做法）：entry = dt 次日开盘
- 实时口径（可成交）：entry = 首次可见日次日开盘
- 成本：黄金期货往返，手续费 + 1 tick 滑点（连续合约，不含展期损耗）

用法
----
    python chanlun_gold_b3_verify.py [--workers 8] [--out data/chanlun_gold_b3.json]
"""

from __future__ import print_function

import argparse
import json
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import chanlun_bi  # noqa: E402

BARS_CACHE = os.path.join(HERE, "data", "_gold_bars.json")
MIN_BARS = 120  # 少于该根数不做判定（笔/中枢不足）

# 黄金期货往返成本（占名义价值比例）
AU_COMMISSION = 0.00004  # 上期所 au 手续费约 10 元/手，600元/g×1000g=60万/手 → 单边 0.0017%×2
AU_TICK = 0.02  # 最小变动 0.02 元/克
AU_SLIP_TICKS = 1  # 往返各 0.5 tick，保守取 1 tick 总额
HOLDS = (1, 3, 5, 10, 20)


def load_bars():
    """从缓存读 bars；缓存缺失时提示先跑一次 build（需 tushare）。"""
    if not os.path.exists(BARS_CACHE):
        raise SystemExit(
            "缺少 %s，请先执行：\n"
            "  python -c \"import os,gold_chanlun_shadow as g,json;"
            "tok=[l.split('=',1)[1].strip() for l in open('.env.local',encoding='utf-8') "
            "if l.startswith('XT_DATA_KEY=')][0]; os.environ['TUSHARE_TOKEN']=tok; "
            "df=g.build_continuous(g.SYM,20); "
            "dts=['%%s-%%s-%%s'%%(str(v)[:4],str(v)[4:6],str(v)[6:8]) for v in df['trade_date']]; "
            "json.dump({'dts':dts,'h':[float(x) for x in df['high']],'l':[float(x) for x in df['low']],"
            "'o':[float(x) for x in df['open']],'c':[float(x) for x in df['close']]}, "
            "open('data/_gold_bars.json','w'))\"" % BARS_CACHE
        )
    d = json.load(open(BARS_CACHE, encoding="utf-8"))
    return d["dts"], d["h"], d["l"], d["o"], d["c"]


def _replay_chunk(args):
    """多进程分片：重放 [lo, hi) 区间，返回 {key: 首见 bar 索引的最早值}。"""
    dts, H, L, C, lo, hi = args
    bars = [{"dt": dts[i], "h": H[i], "l": L[i]} for i in range(len(dts))]
    out = {}
    for t in range(lo, hi):
        st = chanlun_bi.analyze(bars[: t + 1], C[: t + 1])
        for s in st["signals"]:
            k = "%s|%s" % (s["t"], s["d"])
            if k not in out:
                out[k] = t
    return out


def replay(dts, H, L, C, workers):
    """逐日截断重放，返回每个信号键的首次可见 bar 索引。"""
    N = len(dts)
    spans = []
    step = max(1, (N - MIN_BARS) // (workers * 4))  # 细分成小块，均衡负载
    lo = MIN_BARS
    while lo < N:
        hi = min(N, lo + step)
        spans.append((dts, H, L, C, lo, hi))
        lo = hi
    first = {}
    if workers <= 1:
        for sp in spans:
            for k, v in _replay_chunk(sp).items():
                if k not in first or v < first[k]:
                    first[k] = v
    else:
        import multiprocessing as mp

        with mp.Pool(workers) as pool:
            for part in pool.imap_unordered(_replay_chunk, spans):
                for k, v in part.items():
                    if k not in first or v < first[k]:
                        first[k] = v
    return first


def _stats(xs):
    """均值/中位/胜率/标准差/std err/t。"""
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
    sd = var ** 0.5
    se = sd / n ** 0.5
    return {
        "n": n,
        "avg": round(mu, 3),
        "med": round(med, 3),
        "win": round(win, 1),
        "sd": round(sd, 3),
        "se": round(se, 3),
        "t": round(mu / se, 2) if se > 0 else None,
    }


def _boot_ci(xs, iters=4000, alpha=0.05, seed=42):
    """bootstrap 均值置信区间（小样本比正态近似可靠）。"""
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


def returns_at(O, C, entry_i, hold, sign):
    """从 entry_i（入场 bar 索引）起持有 hold 根，返回百分比收益。"""
    j = entry_i + hold
    if j >= len(C) or not O[entry_i] or not C[j]:
        return None
    return sign * (C[j] / O[entry_i] - 1.0) * 100.0


def cost_pct(price):
    """黄金期货往返成本占名义价值的百分比（含 1 tick 滑点）。"""
    if not price:
        return 0.0
    slip = AU_SLIP_TICKS * AU_TICK / price
    return (AU_COMMISSION + slip) * 100.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default=os.path.join(HERE, "data", "chanlun_gold_b3.json"))
    a = ap.parse_args()

    t0 = time.time()
    dts, H, L, O, C = load_bars()
    N = len(dts)
    print("bars %d  %s -> %s" % (N, dts[0], dts[-1]))
    bars = [{"dt": dts[i], "h": H[i], "l": L[i]} for i in range(N)]
    idx = {d: i for i, d in enumerate(dts)}

    # ---- 1. 全历史事后口径 ----
    full = chanlun_bi.analyze(bars, C)
    full_sig = [s for s in full["signals"]]
    print("全历史 signals %d" % len(full_sig))

    # ---- 2. 逐日截断重放 ----
    print("逐日截断重放（%d 次 analyze, %d workers）…" % (N - MIN_BARS, a.workers))
    first = replay(dts, H, L, C, a.workers)
    print("  重放完成 %.1fs，实时出现过 %d 个信号键" % (time.time() - t0, len(first)))

    # ---- 3. 前视比例 ----
    audit = {}
    for typ in ("b1", "b2", "b3", "s1", "s2", "s3"):
        keys = [(s["t"], s["d"]) for s in full_sig if s["t"] == typ]
        seen = [k for k in keys if ("%s|%s" % k) in first]
        lags = []
        for k in seen:
            di = idx.get(k[1])
            fi = first["%s|%s" % k]
            if di is not None and fi is not None:
                lags.append(fi - di)
        audit[typ] = {
            "n_full": len(keys),
            "n_seen": len(seen),
            "seen_pct": round(len(seen) * 100.0 / len(keys), 1) if keys else None,
            "lag_med": sorted(lags)[len(lags) // 2] if lags else None,
            "lag_max": max(lags) if lags else None,
            "lag_neg": sum(1 for x in lags if x < 0),
        }
        print(
            "  %-3s 事后 %3d | 实时可见 %3d (%s%%) | 滞后 中位 %s 最大 %s"
            % (
                typ,
                audit[typ]["n_full"],
                audit[typ]["n_seen"],
                audit[typ]["seen_pct"],
                audit[typ]["lag_med"],
                audit[typ]["lag_max"],
            )
        )

    # ---- 4. 收益：事后 vs 实时 ----
    def collect(typ, mode):
        rows = []
        sign = 1.0 if typ.startswith("b") else -1.0
        for s in full_sig:
            if s["t"] != typ:
                continue
            di = idx.get(s["d"])
            if di is None:
                continue
            key = "%s|%s" % (typ, s["d"])
            if mode == "post":
                # 事后口径：dt 次日开盘（现行 shadow 做法）
                entry_i = di + 1
            else:
                # 实时口径：首次可见日次日开盘
                fi = first.get(key)
                if fi is None:
                    continue
                entry_i = fi + 1
            if entry_i >= N:
                continue
            px = O[entry_i]
            rec = {"dt": s["d"], "entry_date": dts[entry_i], "entry_px": px,
                   "cost": round(cost_pct(px), 4)}
            ok = False
            for h in HOLDS:
                r = returns_at(O, C, entry_i, h, sign)
                rec["d%d" % h] = round(r, 3) if r is not None else None
                if r is not None:
                    ok = True
            if ok:
                rows.append(rec)
        return rows

    result = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "symbol": "AU.SHF", "bars": N, "range": [dts[0], dts[-1]],
              "min_bars": MIN_BARS, "replay_seconds": round(time.time() - t0, 1),
              "audit": audit, "by_type": {}}

    print("\n%-4s %-6s %3s  %-22s %-22s %s"
          % ("类型", "口径", "n", "d5 均值/胜率/t", "d20 均值/胜率/t", "d20 bootstrap 95%CI"))
    for typ in ("b3", "s3", "b1", "b2", "s1", "s2"):
        if not any(s["t"] == typ for s in full_sig):
            continue
        cell = {}
        for mode in ("post", "live"):
            rows = collect(typ, mode)
            if not rows:
                continue
            hs = {}
            for h in HOLDS:
                xs = [r["d%d" % h] for r in rows if r.get("d%d" % h) is not None]
                xs_net = [x - r["cost"] for x, r in
                          ((r["d%d" % h], r) for r in rows if r.get("d%d" % h) is not None)]
                hs[str(h)] = {"gross": _stats(xs), "net": _stats(xs_net)}
            cell[mode] = {"n": len(rows), "holds": hs,
                          "ci_d20_net": _boot_ci([r["d20"] - r["cost"] for r in rows
                                                  if r.get("d20") is not None]),
                          "entries": rows}
            g5, g20 = hs["5"]["gross"], hs["20"]["gross"]
            n20 = hs["20"]["net"]
            print("%-4s %-6s %3d  %+6.2f%%/%5.1f%%/t=%5s  %+6.2f%%/%5.1f%%/t=%5s  %s"
                  % (typ, mode, len(rows), g5["avg"], g5["win"], g5["t"],
                     g20["avg"], g20["win"], g20["t"], cell[mode]["ci_d20_net"]))
        result["by_type"][typ] = cell

    # ---- 5. 落盘 ----
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("\n-> %s   总耗时 %.1fs" % (a.out, time.time() - t0))

    # ---- 6. 结论 ----
    b3 = result["by_type"].get("b3", {})
    if "post" in b3 and "live" in b3:
        p20 = b3["post"]["holds"]["20"]["gross"]["avg"]
        l20 = b3["live"]["holds"]["20"]["gross"]["avg"]
        ln = b3["live"]["holds"]["20"]["net"]
        print("\n== b3 判定 ==")
        print("  事后口径 d20 毛 %+.2f%% (n=%d)" % (p20, b3["post"]["n"]))
        print("  实时口径 d20 毛 %+.2f%% (n=%d)  前视虚增 %+.2fpp"
              % (l20, b3["live"]["n"], p20 - l20))
        print("  实时口径 d20 净 %+.2f%%  t=%s  CI=%s"
              % (ln["avg"], ln["t"], b3["live"]["ci_d20_net"]))
        verdict = ("无 edge（t<1.96 或 CI 含 0）" if (ln.get("t") is None or abs(ln["t"]) < 1.96
                  or (b3["live"]["ci_d20_net"] and b3["live"]["ci_d20_net"][0] <= 0 <= b3["live"]["ci_d20_net"][1]))
                  else "通过 t 检验，但 n 小需谨慎")
        print("  判定:", verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
