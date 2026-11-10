# -*- coding: utf-8 -*-
"""全策略统一总测 + 正交组合验证（2026-11-10，老田指令）。

「把你现在所有的买卖点策略全部平均来测一轮，然后看看有没有组合的可能。」

动机：此前各网格（entry/stop/regime/partial_tp）分批跑在不同时点，
引擎当天还打过 tp_partial/stop_fill_mode 等补丁——数字口径不统一。
本轮把全部已测变体放回同一引擎版本、同一四窗口 × 122bp 口径重跑一遍，
出一张总表；再沿「入场侧 × 退出侧」正交轴测 4 个组合。

变体清单（全部踩 v3 预设，除非注明）：
  入场  A1/A2 min_score 60/70、A3 slot_mode=score、A4 组合、D1/D2 score+cap
  退出  P1/P2/P3/P4 分批止盈族、S1/S2 止损放宽、T1/T2 trail 前置、d_legacy 整腿
  regime R1-R7 MA 快慢周期（零补丁覆盖全局均线，跑完恢复 20/60 并自检）
  组合  C1 A3+P4、C2 D1+P4、C3 A3+放宽止损、C4 A3+P4+放宽止损

口径：PRE/OOS/IN/RY 四窗口 × cost 122bp；超额=逐笔/逐仓 pnl − 等权基准同区间，
持仓级配对（复用 bt10m_partial_tp.pair_alpha，分批腿自动按 tp_frac 加权）。

自检门：
  G1  apply_preset 组合键全部在位；
  G2  V3 基线必须复现 bt10m_entry_grid.json A0_base 合并值（n/α/t）；
  G3  regime 变体跑完后恢复 20/60，重算均线必须与基线逐值一致；
  G4  min_score 变体 meta 必须回读一致。

用法：python bt10m_all_strats.py
产出：reports/bt10m_all_strats.json + 终端总表
"""
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)

import backtest_engine_v2 as eng   # noqa: E402
import bt10m                       # noqa: E402
from bt10m_partial_tp import pair_alpha  # noqa: E402

OUT = os.path.join(BASE, "reports", "bt10m_all_strats.json")
COST = 0.0061

WINDOWS = [("PRE", "20231010", "20240930"),
           ("OOS", "20241008", "20251010"),
           ("IN", "20251013", "20261012"),
           ("RY", "20251110", "20261109")]

TRAIL_P4 = {"trail_activate": 0.20, "trail_drawdown": 0.05}
LEGS = [
    # (tag, extra, kind)  extra 可含 _preset 覆盖预设名
    ("V3_base", {}, "基线"),
    # ---- 入场侧 ----
    ("A1_ms60", {"min_score": 60}, "入场"),
    ("A2_ms70", {"min_score": 70}, "入场"),
    ("A3_slotscore", {"slot_mode": "score"}, "入场"),
    ("A4_ms60+ss", {"min_score": 60, "slot_mode": "score"}, "入场"),
    ("D1_cap25", {"slot_mode": "score", "slot_score_cap": 0.25}, "入场"),
    ("D2_cap15", {"slot_mode": "score", "slot_score_cap": 0.15}, "入场"),
    # ---- 退出侧 ----
    ("P1_part50", {"tp_partial": 0.5}, "退出"),
    ("P2_rearm", {"tp_partial": 0.5, "tp_rearm_pct": 0.2}, "退出"),
    ("P3_part70", {"tp_partial": 0.7}, "退出"),
    ("P4_part50_trail", dict({"tp_partial": 0.5}, **TRAIL_P4), "退出"),
    ("S1_stop10", {"stop_pct": -0.10}, "退出"),
    ("S2_stop12", {"stop_pct": -0.12}, "退出"),
    ("T1_trail松", {"trail_activate": 0.02, "trail_drawdown": 0.05}, "退出"),
    ("T2_trail紧", {"trail_activate": 0.01, "trail_drawdown": 0.04,
                    "stop_pct": -0.10}, "退出"),
    ("d_legacy", {"_preset": "d_legacy"}, "退出"),
    # ---- regime（全局 MA 覆盖）----
    ("R1_10_60", {"_ma": (10, 60)}, "regime"),
    ("R2_30_60", {"_ma": (30, 60)}, "regime"),
    ("R3_20_40", {"_ma": (20, 40)}, "regime"),
    ("R4_20_90", {"_ma": (20, 90)}, "regime"),
    ("R5_10_40", {"_ma": (10, 40)}, "regime"),
    ("R6_30_90", {"_ma": (30, 90)}, "regime"),
    ("R7_5_120", {"_ma": (5, 120)}, "regime"),
    # ---- 组合（入场 × 退出正交轴）----
    ("C1_A3+P4", dict({"slot_mode": "score", "tp_partial": 0.5}, **TRAIL_P4), "组合"),
    ("C2_D1+P4", dict({"slot_mode": "score", "slot_score_cap": 0.25,
                       "tp_partial": 0.5}, **TRAIL_P4), "组合"),
    ("C3_A3+S1", {"slot_mode": "score", "stop_pct": -0.10}, "组合"),
    ("C4_A3+P4+S1", dict({"slot_mode": "score", "tp_partial": 0.5,
                          "stop_pct": -0.10}, **TRAIL_P4), "组合"),
]


def set_ma(pf, ps):
    assert eng._LOADED, "引擎未装载（先跑一次 run_job）"
    eng.ma20 = eng.idx_nav.rolling(pf).mean()
    eng.ma60 = eng.idx_nav.rolling(ps).mean()


def restore_ma_check():
    """G3：恢复 20/60 后必须与重新装载的基线均线一致。"""
    set_ma(20, 60)
    s1 = eng.ma20.dropna()
    s2 = eng.idx_nav.rolling(20).mean().dropna()
    assert len(s1) == len(s2) and (s1 == s2).all(), "G3 失败：恢复均线不一致"


def run_variant(tag, extra, navs):
    """单变体四窗口：返回 rows + pooled 样本。extra._ma 特殊处理。"""
    rows, pooled = [], []
    for wtag, w0, w1 in WINDOWS:
        p = {"d0": w0, "d1": w1, "cost": COST}
        # 注意：必须用 get 不用 pop——pop 会让第 2 窗起静默回落成 v3（曾踩）
        p["preset"] = extra.get("_preset", "v3")
        p.update(extra)
        if "_ma" in extra:
            set_ma(*extra["_ma"])
        payload, err = eng.run_job(p, "allstrat_%s_%s" % (tag, wtag),
                                   progress=lambda *a, **k: None)
        if err or not payload:
            raise SystemExit("%s %s FAIL: %s" % (tag, wtag, err))
        nav, ks = navs[wtag]
        ex = pair_alpha(payload["trades"], nav, ks, want_samples=True)
        pooled.extend(ex)
        m = sum(ex) / len(ex) if ex else 0.0
        t = 0.0
        if len(ex) > 1:
            import statistics
            sd = statistics.stdev(ex) or 1e-9
            t = m / (sd / len(ex) ** 0.5)
        ret = (payload["equity_curve"][-1]["equity"] - 1) * 100
        rows.append({"w": wtag, "ret": round(ret, 2),
                     "mdd": payload["summary"].get("max_drawdown_pct"),
                     "n": len(ex), "alpha_bp": round(m * 100), "t": round(t, 2)})
        print("  %-16s %s  %+7.2f%%  mdd %6.2f%%  n %3d  α %+5dbp  t %5.2f"
              % (tag, wtag, ret, rows[-1]["mdd"] or 0, len(ex), m * 100, t),
              flush=True)
        if "_ma" in extra:
            restore_ma_check()
    return rows, pooled


def main():
    t0 = time.time()
    # 预装载（首个 run_job 装载全量数据）
    _, err = eng.run_job({"preset": "v3", "d0": WINDOWS[0][1],
                          "d1": WINDOWS[0][2], "cost": COST}, "allstrat_boot",
                         progress=lambda *a, **k: None)
    assert not err, err
    print("[load] 引擎装载完成（%.0fs）" % (time.time() - t0), flush=True)

    navs = {}
    for wtag, w0, w1 in WINDOWS:
        nav, _nfiles = bt10m.load_market_bench(w0, w1)
        navs[wtag] = (nav, sorted(nav))

    # G1：组合键在位（家族防御）
    ap = eng.apply_preset({"preset": "v3", "slot_mode": "score",
                           "slot_score_cap": 0.25, "min_score": 60,
                           "tp_partial": 0.5, "tp_rearm_pct": 0.2,
                           "trail_activate": 0.20, "trail_drawdown": 0.05,
                           "stop_pct": -0.10})
    for key, want in (("slot_mode", "score"), ("slot_score_cap", 0.25),
                      ("min_score", 60), ("tp_partial", 0.5),
                      ("tp_rearm_pct", 0.2), ("trail_activate", 0.20),
                      ("trail_drawdown", 0.05), ("stop_pct", -0.10)):
        assert ap.get(key) == want, "G1 失败：%s=%r" % (key, ap.get(key))
    print("G1 通过：组合参数 apply_preset 全部在位", flush=True)

    out = {"windows": dict(((w, "%s-%s" % (a, b)) for w, a, b in WINDOWS)),
           "cost": COST, "variants": {}}
    for tag, extra, kind in LEGS:
        extra = dict(extra)  # 防 pop 污染
        rows, pooled = run_variant(tag, extra, navs)
        m = sum(pooled) / len(pooled) if pooled else 0.0
        t = 0.0
        if len(pooled) > 1:
            import statistics
            sd = statistics.stdev(pooled) or 1e-9
            t = m / (sd / len(pooled) ** 0.5)
        ry = [r for r in rows if r["w"] == "RY"][0]
        out["variants"][tag] = {"kind": kind, "windows": rows,
                                "ry_ret": ry["ret"], "ry_mdd": ry["mdd"],
                                "merged": {"n": len(pooled),
                                           "alpha_bp": round(m * 100),
                                           "t": round(t, 2)}}
        print("%-16s [%s] 合并 n %3d  α %+5dbp  t %5.2f   RY %+6.2f%%"
              % (tag, kind, len(pooled), m * 100, t, ry["ret"]), flush=True)

    # G2：V3 基线必须复现 entry_grid A0_base
    eg = json.load(open(os.path.join(BASE, "reports", "bt10m_entry_grid.json"),
                        encoding="utf-8"))
    a0 = eg["variants"]["A0_base"]["merged"]
    got = out["variants"]["V3_base"]["merged"]
    assert got["n"] == a0["n"] and abs(got["alpha_bp"] - a0["alpha_bp"]) <= 2 \
        and abs(got["t"] - a0["t"]) <= 0.05, \
        "G2 失败：V3 %s vs entry_grid A0 %s" % (got, a0)
    print("G2 通过：V3 基线复现 entry_grid A0（n=%d α=%dbp t=%.2f）"
          % (got["n"], got["alpha_bp"], got["t"]), flush=True)

    # G5：换预设的腿（d_legacy）每窗笔数必须与 V3 不同（防 preset 静默回落）
    v3n = {r["w"]: r["n"] for r in out["variants"]["V3_base"]["windows"]}
    for tag, v in out["variants"].items():
        if tag.startswith("d_legacy"):
            for r in v["windows"]:
                assert r["n"] != v3n[r["w"]], \
                    "G5 失败：%s %s n=%d 与 V3 相同（preset 未生效）" \
                    % (tag, r["w"], r["n"])
            print("G5 通过：d_legacy 四窗笔数均与 V3 不同", flush=True)

    json.dump(out, open(OUT, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("落盘 %s（%.0fs）" % (OUT, time.time() - t0))
    return out


if __name__ == "__main__":
    main()
