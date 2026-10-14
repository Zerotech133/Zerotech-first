# -*- coding: utf-8 -*-
"""W3-5 量子双刀调参三件套离线变体回测（工单 T-20261009-009 续）。

性质：纯离线分析脚本，只读 W3-5 基线重跑导出的逐信号明细
    （reports/qd_details_raw.json / qd_details_hfq.json），不碰产线。

变体定义：
    V1 rotation 拦截  ：剔除 Regime==rotation 的信号（等价产线在
       rotation 市况加入 blocked_regimes，回测层等价于逐信号过滤）；
    V2 信号价格切 hfq ：信号计算用后复权价（= hfq 口径基线，引用）；
    V3 卖出锚定 hold_20：hold_20 已在基线网格，引用该列；
    组合 V1×V2        ：hfq 口径 + rotation 拦截（本次新算）。

稳健性自检（防三件套过拟合）：
    ① 分段（前 2 年 4 个半年度段 + 后 1 年 OOS）PF 与均收益；
    ② IS 段（20231001~20250930）内 rotation 是否同样负贡献
       （V1 规则的样本内合理性）；OOS 段 PF ≥ 0.9×IS PF 不塌方。

判据（全部满足才达标）：
    ① 组合整体 PF ≥ 1.1（hold_20 主判，hold_10 顺带列出）；
    ② 组合回撤/均收益不劣于基线同档（同口径同持有期）；
    ③ OOS 段 PF ≥ 0.9 × IS 段 PF。

用法：
    "C:/Program Files/Python311/python.exe" -u quant_backtest/qd_variants.py
"""
import json
import time
from datetime import datetime
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPORTS = HERE / "reports"
OUT_JSON = REPORTS / "qd_backtest_1445_variants.json"
DETAILS = {
    "raw": REPORTS / "qd_details_raw.json",
    "hfq": REPORTS / "qd_details_hfq.json",
}
HOLDS = (5, 10, 20)

# 分段：前 2 年 = 4 个半年度段（IS），后 1 年 = OOS 观察段
SEGS = [
    ("IS_S1_2023H2", "20231001", "20240331"),
    ("IS_S2_2024H1", "20240401", "20240930"),
    ("IS_S3_2024H2", "20241001", "20250331"),
    ("IS_S4_2025H1", "20250401", "20250930"),
    ("OOS_2025H2to2026", "20251001", "20261013"),
]
IS_NAME = "IS_20231001_20250930"
OOS_NAME = "OOS_20251001_20261013"


def stats(rets):
    a = np.asarray(rets, dtype=float)
    if len(a) == 0:
        return {"n": 0}
    gains, losses = a[a > 0], a[a < 0]
    pf = float(gains.sum() / abs(losses.sum())) if losses.sum() != 0 else \
        (float("inf") if len(gains) else 0.0)
    eq = np.cumprod(1.0 + a)
    dd = float(1.0 - (eq / np.maximum.accumulate(eq)).min())
    # event 复利序列在数千笔下 event_max_dd 恒趋近 100%（退化，无判别力）；
    # 补按每 100 笔分块的块级复利回撤作可解释的回撤度量。
    b = len(a) // 100
    if b >= 2:
        blocks = a[: b * 100].reshape(b, 100)
        bev = np.cumprod(np.prod(1.0 + blocks, axis=1))
        block_dd = float(1.0 - (bev / np.maximum.accumulate(bev)).min())
    else:
        block_dd = dd
    return {
        "n": int(len(a)),
        "win_rate": round(float((a > 0).mean()), 4),
        "pf": round(pf, 4),
        "mean": round(float(a.mean()), 5),
        "median": round(float(np.median(a)), 5),
        "p5": round(float(np.percentile(a, 5)), 5),
        "p95": round(float(np.percentile(a, 95)), 5),
        "event_max_dd": round(dd, 4),
        "block_dd_100": round(block_dd, 4),
    }


def subset_stats(recs, hold, keep=None):
    """keep(rec)->bool 过滤后取该持有期收益列（缺失=未平仓，自然剔除）。"""
    key = "r%d" % hold
    return [r[key] for r in recs if (keep is None or keep(r)) and key in r]


def regime_breakdown(recs, hold, keep=None):
    by = {}
    for r in recs:
        if keep is not None and not keep(r):
            continue
        if "r%d" % hold in r:
            by.setdefault(r["regime"], []).append(r["r%d" % hold])
    return {k: stats(v) for k, v in sorted(by.items())}


def seg_of(date):
    for name, lo, hi in SEGS:
        if lo <= date <= hi:
            return name
    return None


def _kept(r, keep):
    return keep is None or keep(r)


def full_block(recs, keep=None):
    """某变体在某口径下的全套：hold×整体 + hold×Regime + hold×分段。"""
    blk = {}
    for h in HOLDS:
        blk["hold_%d" % h] = {
            "overall": stats(subset_stats(recs, h, keep)),
            "by_regime": regime_breakdown(recs, h, keep),
            "by_segment": {
                name: stats([r["r%d" % h] for r in recs
                             if "r%d" % h in r and _kept(r, keep)
                             and seg_of(r["date"]) == name])
                for name, _, _ in SEGS
            },
            "is_vs_oos": {
                IS_NAME: stats([r["r%d" % h] for r in recs
                                if "r%d" % h in r and _kept(r, keep)
                                and r["date"] <= "20250930"]),
                OOS_NAME: stats([r["r%d" % h] for r in recs
                                 if "r%d" % h in r and _kept(r, keep)
                                 and r["date"] >= "20251001"]),
            },
        }
    return blk


def main():
    t0 = time.time()
    data = {}
    for cal, fp in DETAILS.items():
        raw = json.loads(fp.read_text(encoding="utf-8"))
        # 按信号日时间排序：dd 类指标（时间序等权净值）才有意义；
        # PF/胜率/均值/分段与 Regime 统计均与排序无关
        recs = sorted(raw["records"], key=lambda r: (r["date"], r["code"]))
        data[cal] = recs
        print("[load] %s：%d 条逐信号明细" % (cal, raw["n"]))

    variants = {}

    # ── 基线（自明细重算，先与 W3-4 结果 JSON 校验）──
    baseline = {cal: full_block(recs) for cal, recs in data.items()}
    ref = json.loads((REPORTS / "qd_backtest_1445_result.json")
                     .read_text(encoding="utf-8"))["overall"]
    check = {}
    for h in HOLDS:
        got = baseline["raw"]["hold_%d" % h]["overall"]
        exp = ref.get(str(h)) or ref.get(h) or {}
        check["hold_%d" % h] = {
            "pf_match": abs(got["pf"] - exp.get("pf", -1)) < 5e-4,
            "n_match": got["n"] == exp.get("n", -1),
            "pf_detail": got["pf"], "pf_w34": exp.get("pf"),
        }
    print("[check] 明细重算 vs W3-4 基线：", check)

    # ── V1：rotation 拦截（raw / hfq 双口径）──
    v1_keep = lambda r: r["regime"] != "rotation"  # noqa: E731
    variants["V1_rotation_block"] = {cal: full_block(recs, v1_keep)
                                     for cal, recs in data.items()}

    # ── V2：信号价格切 hfq = hfq 口径基线（引用，不改信号集）──
    variants["V2_signal_hfq"] = {
        "note": "信号计算用后复权价=hfq 口径基线（W3-4 已跑，W3-5 明细重算引用）",
        "hfq": baseline["hfq"],
    }

    # ── V3：卖出语义锚定 hold_20（基线网格内，引用 hold_20 列）──
    variants["V3_hold20_anchor"] = {
        "note": "hold_20 已在基线网格，无新算；结论见各口径 hold_20 列",
    }

    # ── 组合 V1×V2：hfq 口径 + rotation 拦截 ──
    variants["combo_V1xV2"] = {"hfq": full_block(data["hfq"], v1_keep)}

    # ── 稳健性自检 ──
    # ① V1 规则的样本内合理性：IS 段 rotation 是否同样负贡献（raw 主口径）
    is_rot = {}
    for cal in ("raw", "hfq"):
        is_rot[cal] = {
            "hold_%d" % h: stats([r["r%d" % h] for r in data[cal]
                                  if "r%d" % h in r
                                  and r["regime"] == "rotation"
                                  and r["date"] <= "20250930"])
            for h in HOLDS
        }
    # ② OOS 不塌方：后半段 PF ≥ 0.9 × 前半段（对候选组合逐档判）
    oos_check = {}
    for tag, blk in (("baseline_raw", baseline["raw"]),
                     ("V1_raw", variants["V1_rotation_block"]["raw"]),
                     ("V1_hfq", variants["V1_rotation_block"]["hfq"]),
                     ("combo_V1xV2", variants["combo_V1xV2"]["hfq"])):
        for h in HOLDS:
            iv = blk["hold_%d" % h]["is_vs_oos"]
            is_pf, oos_pf = iv[IS_NAME].get("pf", 0), iv[OOS_NAME].get("pf", 0)
            oos_check["%s_hold_%d" % (tag, h)] = {
                "is_pf": is_pf, "oos_pf": oos_pf,
                "ratio": round(oos_pf / is_pf, 4) if is_pf else None,
                "pass": bool(is_pf and oos_pf >= 0.9 * is_pf),
            }

    # ── 判据对照（主判 hold_20）──
    combo20 = variants["combo_V1xV2"]["hfq"]["hold_20"]
    base_raw20 = baseline["raw"]["hold_20"]
    pf_ok = combo20["overall"]["pf"] >= 1.1
    dd_ok = (combo20["overall"]["block_dd_100"]
             <= base_raw20["overall"]["block_dd_100"])
    mean_ok = combo20["overall"]["mean"] >= base_raw20["overall"]["mean"]
    oos_ok = oos_check["combo_V1xV2_hold_20"]["pass"]
    verdict = {
        "hold_20": {
            "combo_pf": combo20["overall"]["pf"], "pf_ge_1.1": pf_ok,
            "combo_block_dd": combo20["overall"]["block_dd_100"],
            "baseline_block_dd": base_raw20["overall"]["block_dd_100"],
            "dd_not_worse": dd_ok,
            "dd_metric_note": "event_max_dd 在数千笔复利序列下恒≈100%（退化），"
                              "回撤判据改用每 100 笔分块复利回撤 block_dd_100",
            "combo_mean": combo20["overall"]["mean"],
            "baseline_mean": base_raw20["overall"]["mean"], "mean_not_worse": mean_ok,
            "oos_ratio": oos_check["combo_V1xV2_hold_20"]["ratio"], "oos_pass": oos_ok,
        },
        "all_pass": bool(pf_ok and dd_ok and mean_ok and oos_ok),
        "rule": "组合整体 PF>=1.1 且回撤/均收益不劣于基线同档 且回测内 "
                "OOS 段 PF>=0.9xIS 段 PF",
    }

    payload = {
        "task": "T-20261009-009 W3-5 量子双刀调参三件套离线变体回测",
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "params": {
            "baseline_script": "qd_backtest_1445.py（W3-5 重跑，加 --details 导出）",
            "universe": "湖内全部 CN 股 5593 只（剔除 4 只指数），20231001~20261013",
            "cost_bps_roundtrip": 30,
            "v1": "剔除 Regime==rotation 信号（等价产线 rotation 加入 blocked）",
            "v2": "信号价格切 hfq（后复权）",
            "v3": "卖出语义锚定 hold_20",
            "segments": {name: [lo, hi] for name, lo, hi in SEGS},
        },
        "detail_recheck_vs_w34": check,
        "baseline": baseline,
        "variants": variants,
        "robustness": {
            "v1_rule_in_sample_check_IS_rotation": is_rot,
            "oos_no_collapse": oos_check,
        },
        "criteria": verdict,
        "elapsed_s": round(time.time() - t0, 1),
        "disclaimer": "离线研究回测：结果不构成选股依据；影子验证前不下产线",
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print("[done] %.0fs → %s" % (time.time() - t0, OUT_JSON))

    # 控制台摘要表
    print("\n[combo V1xV2 (hfq) vs 基线]")
    for tag, blk in (("base_raw", baseline["raw"]), ("base_hfq", baseline["hfq"]),
                     ("V1_raw", variants["V1_rotation_block"]["raw"]),
                     ("V1_hfq", variants["V1_rotation_block"]["hfq"]),
                     ("combo", variants["combo_V1xV2"]["hfq"])):
        for h in HOLDS:
            s = blk["hold_%d" % h]["overall"]
            print("  %-9s hold_%-2d n=%-5d win=%.1f%% PF=%.3f mean=%+.3f%% dd=%.2f%%"
                  % (tag, h, s.get("n", 0), s.get("win_rate", 0) * 100,
                     s.get("pf", 0), s.get("mean", 0) * 100,
                     s.get("event_max_dd", 0) * 100))
    print("[verdict]", json.dumps(verdict, ensure_ascii=False))


if __name__ == "__main__":
    main()
