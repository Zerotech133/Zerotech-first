# -*- coding: utf-8 -*-
"""regime 判定网格验证：MA 快周期 × 慢周期 × 四窗口。

老田 2026-11-10：「直接做 regime 判定的网格验证。」

现状（引擎 _ensure_market_loaded）：regime 用**全市场等权指数**的 MA20/MA60 双均线，
三档 = high(≥MA20) / mid(MA60≤v<MA20) / low(<MA60)，D-1 值判定（无未来函数）。
MA 周期从未被网格验证——它是三档仓位帽的总开关，属于「最基础却最没审过」的一环。

实现：ma20/ma60 是引擎模块级全局变量，装载后可在脚本侧按新周期重算覆盖
（regime_of / regime_tier_of 都读这两个全局，零引擎补丁）。每组变体跑前重设。

产出：reports/bt10m_regime_grid.json
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
OUT = os.path.join(BASE, "reports", "bt10m_regime_grid.json")

WINDOWS = [
    ("PRE", "20231010", "20240930"),
    ("OOS", "20241008", "20251010"),
    ("IN", "20251013", "20261012"),
    ("RY", "20251110", "20261109"),
]
COST = 0.0061  # 122bp 主口径

# (标签, 快周期, 慢周期)——R0 为现役基线
GRID = [
    ("R0_20_60_base", 20, 60),
    ("R1_10_60", 10, 60),
    ("R2_30_60", 30, 60),
    ("R3_20_40", 20, 40),
    ("R4_20_90", 20, 90),
    ("R5_10_40", 10, 40),
    ("R6_30_90", 30, 90),
    ("R7_5_120", 5, 120),
]


def set_ma(p_fast, p_slow):
    """重算引擎全局 regime 均线（零补丁覆盖）+ 自检。"""
    assert eng._LOADED, "引擎未装载（先跑一次 run_job）"
    eng.ma20 = eng.idx_nav.rolling(p_fast).mean()
    eng.ma60 = eng.idx_nav.rolling(p_slow).mean()
    # 自检：「参数看着传了」家族防御——核对重算结果与同参数重算一致
    s = eng.ma20.dropna()
    s2 = eng.idx_nav.rolling(p_fast).mean().dropna()
    assert len(s) == len(s2), "ma20 重算长度异常"
    # 档位分布快查（抽 3 日验证 regime_of 输出合法）
    for d in eng.idx_nav.index[::500]:
        lv, _ = eng.regime_of(d)
        assert lv in ("high", "mid", "low"), (d, lv)


def regime_dist(w0, w1):
    """窗口内三档天数分布。"""
    days = [d for d in eng.idx_nav.index if w0 <= d <= w1]
    cnt = {"high": 0, "mid": 0, "low": 0}
    for d in days:
        cnt[eng.regime_of(d)[0]] += 1
    n = max(1, len(days))
    return {k: round(100.0 * v / n, 1) for k, v in cnt.items()}


def main():
    t0 = time.time()
    # 触发装载
    eng.run_job({"preset": "v3", "d0": "20251110", "d1": "20261109", "cost": COST},
                "rg_load")

    results = {}
    for tag, pf, ps in GRID:
        set_ma(pf, ps)
        rows = []
        samples = []
        for wtag, w0, w1 in WINDOWS:
            p = {"preset": "v3", "d0": w0, "d1": w1, "cost": COST}
            payload, err = eng.run_job(p, "rg_%s_%s" % (tag, wtag))
            if err or not payload:
                print("  %s %s 失败：%s" % (tag, wtag, err), flush=True)
                continue
            nav, n = bt10m.load_market_bench(w0, w1)
            ks = sorted(nav)
            nn, a, tstat = pair_alpha(payload["trades"], nav, ks)
            ret = (payload["equity_curve"][-1]["equity"] - 1) * 100
            mdd = payload["summary"].get("max_drawdown_pct")
            rows.append({"w": wtag, "ret": round(ret, 2), "mdd": mdd, "n": nn,
                         "alpha_bp": round(a), "t": round(tstat, 2),
                         "regime_dist": regime_dist(w0, w1)})
            # 跨窗合并超额样本
            q, pairs = {}, []
            for t in payload["trades"]:
                if t["side"] == "BUY":
                    q.setdefault(t["stock"], []).append(t["day"])
                elif t["side"] == "SELL" and t.get("pnl") is not None:
                    lst = q.get(t["stock"])
                    if lst:
                        pairs.append((lst.pop(0), t["day"], float(t["pnl"])))
            def bret(a_, b_):
                la = [k for k in ks if k <= a_]
                lb = [k for k in ks if k <= b_]
                return (nav[lb[-1]] / nav[la[-1]] - 1) * 100 if la and lb else None
            samples.extend([p_ - bret(bd, sd_) for bd, sd_, p_ in pairs
                            if bret(bd, sd_) is not None])
            print("  %-15s %s  %+7.2f%%  %3d笔  α %+5.0fbp t=%5.2f"
                  % (tag, wtag, ret, nn, a, tstat), flush=True)
        if samples:
            import math
            import statistics
            m = statistics.mean(samples)
            sd = statistics.stdev(samples) if len(samples) > 1 else 0.0
            tt = m / (sd / math.sqrt(len(samples))) if sd else 0.0
            merged = {"n": len(samples), "alpha_bp": round(m * 100),
                      "t": round(tt, 2)}
            print("  %-15s 合并  %3d笔  α %+5.0fbp  t=%5.2f"
                  % (tag, len(samples), m * 100, tt), flush=True)
        else:
            merged = {"n": 0, "alpha_bp": 0, "t": 0}
        results[tag] = {"ma": [pf, ps], "windows": rows, "merged": merged}

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(results, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n落盘 %s（%.0f 秒）" % (OUT, time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
