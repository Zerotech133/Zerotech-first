# -*- coding: utf-8 -*-
"""滚动前推（walk-forward）+ 连续全段回测 + 回撤归因。

老田 2026-11-10：「我们有历史数据，基于历史数据往前推再跑，跑出回撤结果以后看问题。」

数据事实（实测）：data/ashare 共 5816 只、2023-04-03 → 2026-11-09（约 880 交易日）。
按既定预热惯例（分型/笔/中枢结构需要前史），走推从 20231010 起算，此前 6 个月只作结构预热。

两种跑法互补：
  A. 滚动前推：60 交易日无重叠窗口 ×13，每窗独立起止（窗口末强平 exit_open），
     链乘净值看「每个季度独立开仓」的稳定性——这是最接近实盘换仓节奏的口径；
  B. 连续全段：20231010→20261109 一条龙，持仓跨窗不做人为切割，
     出真实净值曲线与最大回撤段，并对每个回撤段归因（退出构成/最差五笔/基准同期）。

事件缓存策略：整段只扫一次（market_scan --d0 20231010 --d1 20261109），
窗口缓存由本脚本从整段缓存按日切片落盘（引擎加载器按 sig_buy 窗口日过滤，切片等价）。

用法：python bt10m_walkforward.py
产出：reports/bt10m_walkforward.json
"""
import json
import math
import os
import statistics
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bt10m  # noqa: E402
import backtest_engine_v2 as eng  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "reports", "bt10m_walkforward.json")

D0, D1 = "20231010", "20261109"
FULL_CACHE = os.path.join(
    BASE, "data", "sig_events", "sig_events_market_v3_%s_%s.json" % (D0, D1)
)
WIN_DAYS = 60
COSTS = [(0.0061, "122bp"), (0.0015, "30bp")]


def slice_cache():
    """整段缓存 → 每 60td 窗口切片落盘（引擎按窗口日过滤，切片等价于单独扫）。"""
    j = json.load(open(FULL_CACHE, encoding="utf-8"))
    events = j["events"]
    meta0 = j.get("meta", {})
    print("整段缓存：%d 事件（meta.score_weights=%s）"
          % (len(events), meta0.get("score_weights")), flush=True)
    ks = sorted({ev["day"] for ev in events})
    wins = []
    for i in range(0, len(ks), WIN_DAYS):
        w0, w1 = ks[i], ks[min(i + WIN_DAYS, len(ks)) - 1]
        wins.append((w0, w1))
    # 末窗太短（<20td）并入前窗
    if len(wins) >= 2 and sum(1 for d in ks if wins[-1][0] <= d <= wins[-1][1]) < 20:
        w0, _ = wins.pop()
        wins[-1] = (wins[-1][0], w1 if False else wins[-1][1])
        wins[-1] = (wins[-1][0], _)
    out = []
    for w0, w1 in wins:
        evs = [ev for ev in events if w0 <= ev["day"] <= w1]
        meta = dict(meta0)
        meta.update({"d0": w0, "d1": w1, "n_events": len(evs),
                     "sliced_from": "%s_%s" % (D0, D1)})
        fp = os.path.join(BASE, "data", "sig_events",
                          "sig_events_market_v3_%s_%s.json" % (w0, w1))
        json.dump({"meta": meta, "events": evs}, open(fp, "w", encoding="utf-8"),
                  ensure_ascii=False)
        out.append((w0, w1, len(evs)))
    return out


def pair_alpha(trades, nav, ks):
    q, pairs = {}, []
    for t in trades:
        if t["side"] == "BUY":
            q.setdefault(t["stock"], []).append(t["day"])
        elif t["side"] == "SELL":
            lst = q.get(t["stock"])
            if lst and t.get("pnl") is not None:
                pairs.append((lst.pop(0), t["day"], float(t["pnl"]), t.get("how", "?")))
    def bret(a, b):
        la = [k for k in ks if k <= a]
        lb = [k for k in ks if k <= b]
        if not la or not lb:
            return None
        return (nav[lb[-1]] / nav[la[-1]] - 1) * 100
    al = [p - bret(bd, sd) for bd, sd, p, h in pairs
          if (bret(bd, sd)) is not None]
    m = statistics.mean(al) if al else 0.0
    sd_ = statistics.stdev(al) if len(al) > 1 else 0.0
    t = m / (sd_ / math.sqrt(len(al))) if sd_ else 0.0
    return len(pairs), m * 100, t


def exit_mix(pairs):
    buckets = {}
    for bd, sd, pnl, how in pairs:
        b = buckets.setdefault(how, {"n": 0, "sum": 0.0, "pnls": []})
        b["n"] += 1
        b["sum"] += pnl
        b["pnls"].append(pnl)
    for h, b in buckets.items():
        b["sum"] = round(b["sum"], 1)
        b["avg"] = round(statistics.mean(b["pnls"]), 2)
        del b["pnls"]
    return buckets


def dd_episodes(eq_curve, nav, ks, trades, min_depth=0.05):
    """连续净值的回撤段：峰→谷深度 ≥ min_depth，含归因。"""
    eqs = [(r["day"], r["equity"]) for r in eq_curve]
    nav_last = {k: nav[k] for k in ks}
    episodes = []
    peak_d, peak_v = eqs[0][0], eqs[0][1]
    cur = None
    for d, v in eqs:
        if v >= peak_v:
            if cur and cur["depth"] <= -min_depth:  # depth 是负分数
                cur["end"] = d
                episodes.append(cur)
            cur = None
            peak_d, peak_v = d, v
        else:
            dd = v / peak_v - 1
            if cur is None:
                cur = {"start": peak_d, "trough": d, "depth": dd,
                       "bench_same": None, "exits": None, "worst5": None}
            elif dd < cur["depth"]:
                cur["trough"] = d
                cur["depth"] = dd
    if cur and cur["depth"] <= -min_depth:
        episodes.append(cur)
    for ep in episodes:
        # 同期基准（峰→谷）
        la = [k for k in ks if ep["start"] <= k <= ep["trough"]]
        if la:
            ep["bench_same"] = round((nav_last[la[-1]] / nav_last[la[0]] - 1) * 100, 2)
        # 谷前 60td 内平仓的亏损单归因
        lo_cut = la[0] if la else ep["start"]
        win_pairs = [(t.get("how", "?"), float(t["pnl"]), t["day"], t["stock"])
                     for t in trades
                     if t["side"] == "SELL" and t.get("pnl") is not None
                     and lo_cut <= t["day"] <= ep["trough"]]
        ep["exits"] = exit_mix([(None, d, p, h) for h, p, d, s in win_pairs])
        worst = sorted(win_pairs, key=lambda x: x[1])[:5]
        ep["worst5"] = ["%s %s %+.1f%%" % (s, d, p) for h, p, d, s in worst]
        ep["depth"] = round(ep["depth"] * 100, 2)
    return episodes


def main():
    if not os.path.exists(FULL_CACHE):
        raise SystemExit("整段缓存不存在：%s\n先跑：python market_scan.py --d0 %s --d1 %s"
                         % (FULL_CACHE, D0, D1))
    t0 = time.time()
    nav, n = bt10m.load_market_bench(D0, D1)
    ks = sorted(nav)
    bench_total = (nav[ks[-1]] / nav[ks[0]] - 1) * 100
    print("日历 %s→%s：%d 交易日 · 基准 %+0.2f%%（%.0fs）"
          % (ks[0], ks[-1], len(ks), bench_total, time.time() - t0), flush=True)

    res = {"span": {"d0": D0, "d1": D1, "days": len(ks),
                    "bench_total": round(bench_total, 2)}, "walkforward": {}, "continuous": {}}

    # ---- A. 滚动前推 ----
    wins = slice_cache()
    print("\n[A] 滚动前推：%d 个窗口 × 60td" % len(wins), flush=True)
    for cost, ctag in COSTS:
        chain = 1.0
        rows = []
        pooled = []  # 全窗合并逐笔超额（答「整体是否显著」）
        for w0, w1, nev in wins:
            p = {"preset": "v3", "d0": w0, "d1": w1, "cost": cost}
            payload, err = eng.run_job(p, "wf_%s_%s_%s" % (w0, w1, ctag))
            if err or not payload:
                print("  %s→%s@%s 失败：%s" % (w0, w1, ctag, err), flush=True)
                continue
            pairs = []
            q = {}
            for t in payload["trades"]:
                if t["side"] == "BUY":
                    q.setdefault(t["stock"], []).append(t["day"])
                elif t["side"] == "SELL" and t.get("pnl") is not None:
                    lst = q.get(t["stock"])
                    if lst:
                        pairs.append((lst.pop(0), t["day"], float(t["pnl"]), t.get("how")))
            def bret(a, b):
                la = [k for k in ks if k <= a]
                lb = [k for k in ks if k <= b]
                if not la or not lb:
                    return None
                return (nav[lb[-1]] / nav[la[-1]] - 1) * 100
            for bd, sd, pnl, h in pairs:
                br = bret(bd, sd)
                if br is not None:
                    pooled.append(pnl - br)
            nn, a, tstat = pair_alpha(payload["trades"], nav, ks)
            ret = (payload["equity_curve"][-1]["equity"] - 1) * 100
            mdd = payload["summary"].get("max_drawdown_pct")
            chain *= (1 + ret / 100)
            row = {"w0": w0, "w1": w1, "ret": round(ret, 2), "mdd": mdd,
                   "n": nn, "alpha_bp": round(a), "t": round(tstat, 2),
                   "chain": round(chain * 100, 2), "events": nev}
            rows.append(row)
            print("  %s→%s@%s %+7.2f%%  回撤 %6.2f%%  %3d笔  α %+5.0fbp  t=%5.2f  链乘 %+7.2f%%"
                  % (w0, w1, ctag, ret, mdd or 0, nn, a, tstat, chain * 100 - 100),
                  flush=True)
        m = statistics.mean(pooled) if pooled else 0.0
        sd_ = statistics.stdev(pooled) if len(pooled) > 1 else 0.0
        res["walkforward"][ctag] = {
            "rows": rows,
            "pooled_n": len(pooled),
            "pooled_alpha_bp": round(m * 100),
            "pooled_t": round(m / (sd_ / math.sqrt(len(pooled))), 2) if sd_ else 0.0,
        }
        print("  [%s] 全窗合并：%d 笔  α %+0.0fbp  t=%.2f"
              % (ctag, len(pooled), m * 100,
                 m / (sd_ / math.sqrt(len(pooled))) if sd_ else 0), flush=True)

    # ---- B. 连续全段 + 回撤归因 ----
    print("\n[B] 连续全段 %s→%s" % (D0, D1), flush=True)
    for cost, ctag in COSTS:
        p = {"preset": "v3", "d0": D0, "d1": D1, "cost": cost}
        t1 = time.time()
        payload, err = eng.run_job(p, "cont_v3_%s" % ctag)
        if err or not payload:
            print("  连续@%s 失败：%s" % (ctag, err), flush=True)
            continue
        ret = (payload["equity_curve"][-1]["equity"] - 1) * 100
        eps = dd_episodes(payload["equity_curve"], nav, ks, payload["trades"])
        q, pairs = {}, []
        for t in payload["trades"]:
            if t["side"] == "BUY":
                q.setdefault(t["stock"], []).append(t["day"])
            elif t["side"] == "SELL" and t.get("pnl") is not None:
                lst = q.get(t["stock"])
                if lst:
                    pairs.append((lst.pop(0), t["day"], float(t["pnl"]), t.get("how")))
        nn, a, tstat = pair_alpha(payload["trades"], nav, ks)
        res["continuous"][ctag] = {
            "ret": round(ret, 2), "n": nn, "alpha_bp": round(a), "t": round(tstat, 2),
            "exit_mix": exit_mix(pairs), "dd_episodes": eps,
            "equity": [[r["day"], round(r["equity"], 4)] for r in payload["equity_curve"]],
            "secs": round(time.time() - t1, 1)}
        print("  @%s 连续 %+0.2f%%（基准 %+0.2f%%）  %d笔  α %+0.0fbp t=%.2f  回撤段 %d 个"
              % (ctag, ret, bench_total, nn, a, tstat, len(eps)), flush=True)
        for ep in eps:
            print("    回撤 %.1f%%  峰%s→谷%s  同期基准 %+0.1f%%  退出构成 %s"
                  % (ep["depth"], ep["start"], ep["trough"], ep["bench_same"] or 0,
                     " ".join("%s×%d(%+.0f点)" % (h, b["n"], b["sum"])
                              for h, b in sorted(ep["exits"].items(),
                                                 key=lambda kv: kv[1]["sum"]))),
                  flush=True)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n产物：%s" % OUT)


if __name__ == "__main__":
    main()
