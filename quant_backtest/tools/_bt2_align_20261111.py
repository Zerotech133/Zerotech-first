# -*- coding: utf-8 -*-
"""2026-11-11 · bt2 align 化：994 jobs 补笔均超额 t（任务 #106，寻优协议下一步①）。

口径（与 bt10m_align 同族，但按 bt2 产物能支撑的方式落地）：
  · 逐笔超额：每笔 SELL 的 pnl（%）对应一个「同日全市场等权持有同长度」
    对照不可从 bt2 产物重建（产物无逐日池成员快照）——退而求其次用
    【窗口全市场等权基准年化折算到笔持有期】做对照会引入仓位期错配。
  · 因此采用**两条并行口径**，都有明确标注：
    A) 笔均超额 vs 全窗基准日均：excess_per_trade = mean(pnl) −
       bench_total/ n_days × mean_hold_days（粗口径，只做排序参考）；
    B) 更严格口径（可排名）：笔内 pnl 的 t 值本身
       （t = mean(pnl)/se(pnl)，衡量「这笔策略的钱是不是稳定赚」），
       标注为 t_raw；DSR 存活线照样适用（它检验的就是均值≠0）。
  · 基准（bt10m.load_market_bench 的等权净值）按 job 的 d0/d1 取窗收益。
产物：data/bt2/_align_index.json（job → {n_sells, mean_pnl, t_raw, bench,
n_bench_days, excess_per_trade}），并回填 trial_ledger（bt2 条目加 t 字段）。
"""
import glob
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BT2 = os.path.join(HERE, "data", "bt2")
LEDGER = os.path.join(HERE, "reports", "trial_ledger.json")
IDX_OUT = os.path.join(BT2, "_align_index.json")

sys.path.insert(0, HERE)


def load_bench():
    """全市场等权日净值 {day: nav}（bt10m 单一事实源）。"""
    import bt10m
    r = bt10m.load_market_bench("20230101", "20261231")
    if isinstance(r, tuple) and len(r) == 3:
        nav, _, _ = r
    else:
        nav, _ = r  # 两返回值形态
    return nav


def main():
    t0 = time.time()
    nav = load_bench()
    days_sorted = sorted(nav)
    out = {}
    n_done = 0
    for f in sorted(glob.glob(os.path.join(BT2, "bt2_*.json"))):
        job = os.path.basename(f)[4:-5]
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            out[job] = {"error": "parse"}
            continue
        sells = [t for t in (d.get("trades") or []) if t.get("side") == "SELL"
                 and isinstance(t.get("pnl"), (int, float))]
        if not sells:
            out[job] = {"n_sells": 0}
            continue
        pnls = [t["pnl"] for t in sells]
        n = len(pnls)
        mean = sum(pnls) / n
        var = sum((x - mean) ** 2 for x in pnls) / max(1, n - 1)
        se = math.sqrt(var / n) if var > 0 else None
        t_raw = round(mean / se, 2) if se else None
        # 窗口基准（用 job meta 的 d0/d1）
        cfg = (d.get("meta") or {}).get("config") or {}
        d0, d1 = str(cfg.get("d0")), str(cfg.get("d1"))
        bench = None
        n_bd = 0
        if d0 and d1:
            a = [k for k in days_sorted if k <= d0]
            b = [k for k in days_sorted if k <= d1]
            if a and b:
                bench = round((nav[b[-1]] / nav[a[-1]] - 1) * 100, 2)
                n_bd = sum(1 for k in days_sorted if d0 < k <= d1)
        # 粗口径逐笔超额：基准日均 × 平均持有天数（用 n_days/n_sells 近似持有期）
        excess = None
        if bench is not None and n_bd and n:
            avg_hold = n_bd / n
            excess = round(mean - bench / n_bd * avg_hold, 2)
        out[job] = {"n_sells": n, "mean_pnl": round(mean, 2),
                    "t_raw": t_raw, "bench_pct": bench,
                    "n_bench_days": n_bd,
                    "excess_per_trade_crude": excess}
        n_done += 1
        if n_done % 150 == 0:
            print("[%s] %d jobs… (%.0fs)" % (time.strftime("%H:%M:%S"),
                                             n_done, time.time() - t0), flush=True)

    json.dump(out, open(IDX_OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print("align 索引 →", IDX_OUT, "（%d jobs，%.0fs）" % (len(out), time.time() - t0))

    # 回填台账
    led = json.load(open(LEDGER, encoding="utf-8"))
    hit = 0
    for e in led["entries"]:
        if e.get("src") != "bt2":
            continue
        a = out.get(e["job"])
        if a and a.get("t_raw") is not None:
            e["t_raw"] = a["t_raw"]
            e["mean_pnl"] = a.get("mean_pnl")
            e["n_sells"] = a.get("n_sells")
            e["bench_pct"] = a.get("bench_pct")
            hit += 1
    led["align_backfilled"] = {"at": time.strftime("%Y-%m-%d %H:%M:%S"),
                               "jobs_with_t": hit,
                               "index": os.path.basename(IDX_OUT)}
    json.dump(led, open(LEDGER, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("台账回填 t_raw：%d 条" % hit)

    # 预览：按 t_raw 排序 top10
    ranked = sorted(((v["t_raw"], k, v["mean_pnl"], v["n_sells"])
                     for k, v in out.items() if v.get("t_raw") is not None),
                    reverse=True)
    print("\n按 t_raw 排序 top10：")
    for t, k, mp, ns in ranked[:10]:
        print("  t=%-5s %-42s mean_pnl=%+.2f%% n=%d" % (t, k, mp, ns))


if __name__ == "__main__":
    main()
