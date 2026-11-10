# -*- coding: utf-8 -*-
"""入场侧上升空间探索：评分门槛 / 评分仓位 / 背驰真值分桶。

老田 2026-11-10：「探索其他可上升空间。」退出侧三方向已全线否证，剩余空间在入场侧：
  A. min_score 评分门槛（引擎现成参数，0 补丁）——评分只被量能+背驰驱动（位置分恒满分时代
     遗留已修），高分=量大+背驰真；
  B. slot_mode="score" 评分权重仓位（替代 fixed 等权槽位）；
  C. 背驰真值验证：整段缓存里 3买 bei 100% 填充（77.4% True）——此前「背驰从未验证」
     是 hf_feat 因子盘点管线的事，market_scan 管线的 bei 有真值，直接按 bei 分桶看后验。

窗口：四份既有缓存（PRE/OOS/IN/RY），主口径 122bp。
产出：reports/bt10m_entry_grid.json
"""
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bt10m  # noqa: E402
import backtest_engine_v2 as eng  # noqa: E402
from bt10m_walkforward import pair_alpha  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "reports", "bt10m_entry_grid.json")

WINDOWS = [
    ("PRE", "20231010", "20240930"),
    ("OOS", "20241008", "20251010"),
    ("IN", "20251013", "20261012"),
    ("RY", "20251110", "20261109"),
]
COST = 0.0061  # 122bp 主口径

# A 组变体（v3 基底）
VARIANTS = [
    ("A0_base", {}),
    ("A1_ms60", {"min_score": 60}),
    ("A2_ms70", {"min_score": 70}),
    ("A3_slotscore", {"slot_mode": "score"}),
    ("A4_ms60+slotscore", {"min_score": 60, "slot_mode": "score"}),
]


def cache_path(w0, w1):
    return os.path.join(BASE, "data", "sig_events",
                        "sig_events_market_v3_%s_%s.json" % (w0, w1))


def load_bench(w0, w1):
    nav, n = bt10m.load_market_bench(w0, w1)
    return nav, sorted(nav)


def alpha_samples(trades, nav, ks):
    """逐笔超额样本（复用 pair_alpha 的配对与基准口径），供跨窗合并算 t。"""
    import math
    import statistics
    q, pairs = {}, []
    for t in trades:
        if t["side"] == "BUY":
            q.setdefault(t["stock"], []).append(t["day"])
        elif t["side"] == "SELL":
            lst = q.get(t["stock"])
            if lst and t.get("pnl") is not None:
                pairs.append((lst.pop(0), t["day"], float(t["pnl"])))
    def bret(a, b):
        la = [k for k in ks if k <= a]
        lb = [k for k in ks if k <= b]
        if not la or not lb:
            return None
        return (nav[lb[-1]] / nav[la[-1]] - 1) * 100
    return [p - bret(bd, sd) for bd, sd, p in pairs
            if bret(bd, sd) is not None]


def merge_t(samples):
    import math
    import statistics
    if not samples:
        return 0, 0.0, 0.0
    m = statistics.mean(samples)
    sd = statistics.stdev(samples) if len(samples) > 1 else 0.0
    t = m / (sd / math.sqrt(len(samples))) if sd else 0.0
    return len(samples), m * 100, t


def run_variant(tag, extra, bench, dayseqs, results):
    """跑一个变体 ×四窗口；逐窗报数 + 跨窗合并（每窗用本窗基准）。"""
    rows = []
    all_samples = []
    for wtag, w0, w1 in WINDOWS:
        p = {"preset": "v3", "d0": w0, "d1": w1, "cost": COST}
        p.update(extra)
        payload, err = eng.run_job(p, "eg_%s_%s" % (tag, wtag))
        if err or not payload:
            print("  %s %s 失败：%s" % (tag, wtag, err), flush=True)
            continue
        # 自检：min_score 必须真实生效（「参数看着传了」家族防御）
        meta = payload.get("meta", {})
        if "min_score" in extra:
            got = meta.get("min_score")
            if got != extra["min_score"]:
                raise SystemExit("自检失败：%s %s min_score 传了 %s 实际 %s"
                                 % (tag, wtag, extra["min_score"], got))
        nav, ks = bench[wtag]
        nn, a, tstat = pair_alpha(payload["trades"], nav, ks)
        samples = alpha_samples(payload["trades"], nav, ks)
        all_samples.extend(samples)
        ret = (payload["equity_curve"][-1]["equity"] - 1) * 100
        mdd = payload["summary"].get("max_drawdown_pct")
        row = {"w": wtag, "ret": round(ret, 2), "mdd": mdd, "n": nn,
               "alpha_bp": round(a), "t": round(tstat, 2)}
        rows.append(row)
        print("  %-18s %s  %+7.2f%%  %3d笔  α %+5.0fbp  t=%5.2f"
              % (tag, wtag, ret, nn, a, tstat), flush=True)
    nn, a, tstat = merge_t(all_samples)
    merged = {"n": nn, "alpha_bp": round(a), "t": round(tstat, 2)}
    print("  %-18s 合并  %3d笔  α %+5.0fbp  t=%5.2f" % (tag, nn, a, tstat), flush=True)
    results.setdefault("variants", {})[tag] = {"windows": rows, "merged": merged}


def build_ev_index():
    """事件缓存 → {wtag: {(stock, sigday): props}}。"""
    idx = {}
    for wtag, w0, w1 in WINDOWS:
        j = json.load(open(cache_path(w0, w1), encoding="utf-8"))
        m = {}
        for e in j["events"]:
            if e["type"] == "3买":
                m[(e["stock"], e["day"])] = {
                    "bei": e.get("bei"), "bei2": e.get("bei2"),
                    "vr": e.get("vr"), "score": e.get("score"), "zg": e.get("zg")}
        idx[wtag] = m
    return idx


def diverge_full(trades, ev_map, dayseq):
    """完整版分桶：dayseq 为该窗口全部交易日升序（信号日 = BUY 日的前一交易日）。"""
    pos = {d: i for i, d in enumerate(dayseq)}
    q = {}
    pairs = []
    for t in sorted(trades, key=lambda x: x["day"]):
        if t["side"] == "BUY":
            q.setdefault(t["stock"], []).append(t["day"])
        elif t["side"] == "SELL" and t.get("pnl") is not None:
            lst = q.get(t["stock"])
            if lst:
                pairs.append((lst.pop(0), t["day"], float(t["pnl"]), t["stock"]))
    buckets = {"bei_T": [], "bei_F": [], "bei2_T": [], "bei2_F": [],
               "vr_lo": [], "vr_hi": [], "sc_lo": [], "sc_hi": []}
    n_joined = 0
    for buy_day, sell_day, pnl, stock in pairs:
        i = pos.get(buy_day)
        if i is None or i == 0:
            continue
        sig = dayseq[i - 1]
        props = ev_map.get((stock, sig))
        if props is None:
            continue
        n_joined += 1
        if props["bei"] is True:
            buckets["bei_T"].append(pnl)
        elif props["bei"] is False:
            buckets["bei_F"].append(pnl)
        if props["bei2"] is True:
            buckets["bei2_T"].append(pnl)
        elif props["bei2"] is False:
            buckets["bei2_F"].append(pnl)
        if props["vr"] is not None:
            (buckets["vr_hi"] if props["vr"] >= 0.9 else buckets["vr_lo"]).append(pnl)
        if props["score"] is not None:
            (buckets["sc_hi"] if props["score"] >= 54 else buckets["sc_lo"]).append(pnl)
    out = {"n_joined": n_joined, "n_pairs": len(pairs)}
    for k, v in buckets.items():
        if v:
            mu = sum(v) / len(v)
            sd = (sum((x - mu) ** 2 for x in v) / len(v)) ** 0.5 or 1e-9
            out[k] = {"n": len(v), "mean_pnl": round(mu, 2),
                      "t": round(mu / (sd / len(v) ** 0.5), 2),
                      "win": round(100.0 * sum(1 for x in v if x > 0) / len(v), 1)}
        else:
            out[k] = {"n": 0}
    return out


def main():
    t0 = time.time()
    results = {}
    ev_index = build_ev_index()
    print("事件索引建好：", {k: len(v) for k, v in ev_index.items()}, flush=True)

    # 每窗的 nav 基准与交易日序列（C 组 join 需要）
    dayseqs, bench = {}, {}
    for wtag, w0, w1 in WINDOWS:
        nav, ks = load_bench(w0, w1)
        bench[wtag] = (nav, ks)
        dayseqs[wtag] = ks
        print("%s %s→%s 基准 %d 日" % (wtag, w0, w1, len(ks)), flush=True)

    # A 组
    print("\n[A] 评分门槛 / 评分仓位 @122bp", flush=True)
    for tag, extra in VARIANTS:
        run_variant(tag, extra, bench, dayseqs, results)

    # C 组（A0 的 trades 已在 run_variant 中粗提取，这里完整重做）
    print("\n[C] 背驰真值分桶（A0 基线四窗合并）", flush=True)
    div_all = {}
    for wtag, w0, w1 in WINDOWS:
        p = {"preset": "v3", "d0": w0, "d1": w1, "cost": COST}
        payload, err = eng.run_job(p, "eg_A0_base_%s" % wtag)
        if err or not payload:
            continue
        div_all[wtag] = diverge_full(payload["trades"], ev_index[wtag], dayseqs[wtag])
        print("  %s joined %d/%d" % (wtag, div_all[wtag]["n_joined"],
                                     div_all[wtag]["n_pairs"]), flush=True)
    results["divergence"] = div_all

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(results, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n落盘 %s（%.0f 秒）" % (OUT, time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
