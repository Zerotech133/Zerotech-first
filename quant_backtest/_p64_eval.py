# -*- coding: utf-8 -*-
"""P64 连续加权评测（RPS 权重 0.25 → 0.30/0.35/0.40，不设阈值）。

判据（沿用 P63 三条 + 新增两条）：
  C0 自检      w4_rps25 与 base(w_cur_th7550_gate_on) 笔数/后验完全一致
  C1 全窗口    三档持有期均值均 >= base，且总当量 >= 1.00x
  C2 分段      前 6 月 / 后 6 月，两段均不低于 base
  C3 切分稳健  切分点 k=4/6/8，三个 k 的样本外段均不低于 base
  C4 崩溃压力  202609（P63 动量崩溃月）不得显著恶化（> 0.5pct 视为恶化）

另出「阈值-笔数对齐」视图：把各变体调阈值对齐到 base 笔数后比后验，
用于剥离「提权导致阈值变相加严」的成分。
"""
import json
import os
import sys

REPORTS = r"C:/项目/老田的AI助手/quant_backtest/reports"
SRC = os.path.join(REPORTS, "_p64_weight_12m.json")
HOLDS = ("5", "10", "20")
BASE_LABEL = "w_cur_th7550_gate_on"
ANCHOR = "w4_rps25"


def pct(x):
    return "n/a" if x is None else "%.4f%%" % (x * 100)


def main():
    d = json.load(open(SRC, encoding="utf-8"))
    base = [v for v in d["variants"] if v["label"] == BASE_LABEL][0]
    wa = d["weight_ab"]["variants"]
    rows = d["weight_ab"]["monthly"]["rows"]
    thc = d.get("weight_ab", {}).get("th_curve", {}).get("rows", [])

    by_cfg = {}
    for r in rows:
        by_cfg.setdefault(r["cfg"], []).append(r)
    months = sorted({r["month"] for r in rows})

    # ── C0 自检 ──
    anc = [v for v in wa if v["label"] == ANCHOR][0]
    c0 = anc["n_buy_in_window"] == base["n_buy_in_window"] and all(
        abs(anc["holds"][h]["mean"] - base["holds"][h]["mean"]) < 1e-9 for h in HOLDS
    )

    # ── C1 全窗口 ──
    def total_eq(v):
        return sum(v["holds"][h]["n_trades"] * (v["holds"][h]["mean"] or 0) for h in HOLDS)

    base_eq = sum(base["holds"][h]["n_trades"] * (base["holds"][h]["mean"] or 0) for h in HOLDS)
    base_means = {h: base["holds"][h]["mean"] for h in HOLDS}

    # ── 分段工具 ──
    def seg(cfg, mset, h):
        vals = [r["t" + h] for r in by_cfg.get(cfg, []) if r["month"] in mset and r.get("t" + h) is not None]
        return (sum(vals) / len(vals)) if vals else None

    half = len(months) // 2
    m_is, m_oos = set(months[:half]), set(months[half:])

    def pass_seg(cfg, h, mset, bcfg=ANCHOR):
        a, b = seg(cfg, mset, h), seg(bcfg, mset, h)
        if a is None or b is None:
            return None
        return a >= b - 1e-12

    # ── C3 切分点 ──
    splits = [k for k in (4, 6, 8) if 0 < k < len(months)]

    results = []
    for v in wa:
        lab = v["label"]
        if lab == ANCHOR:
            continue
        c1 = all((v["holds"][h]["mean"] or -9) >= base_means[h] for h in HOLDS)
        eq = total_eq(v) / base_eq if base_eq else None
        c1 = bool(c1 and eq is not None and eq >= 1.0)

        seg_flags = []
        for h in ("5", "10"):
            for mset, nm in ((m_is, "IS"), (m_oos, "OOS")):
                r = pass_seg(lab, h, mset)
                seg_flags.append((h, nm, r))
        c2 = all(f[2] is True for f in seg_flags)

        c3_detail = []
        for k in splits:
            ms = set(months[k:])
            ok = all(pass_seg(lab, h, ms) for h in ("5", "10"))
            c3_detail.append((k, ok))
        c3 = all(x[1] for x in c3_detail) if c3_detail else None

        sep = seg(lab, {"202609"}, "5")
        sep_b = seg(ANCHOR, {"202609"}, "5")
        c4 = None if (sep is None or sep_b is None) else (sep >= sep_b - 0.005)

        results.append(
            {
                "label": lab,
                "n_buy": v["n_buy_in_window"],
                "holds": {h: v["holds"][h]["mean"] for h in HOLDS},
                "eq_ratio": None if eq is None else round(eq, 4),
                "C1": c1,
                "C2": c2,
                "C3": c3,
                "C4": c4,
                "seg": [{"hold": f[0], "window": f[1], "ok": f[2]} for f in seg_flags],
                "split": [{"k": x[0], "ok": x[1]} for x in c3_detail],
                "sep202609": sep,
                "sep202609_base": sep_b,
            }
        )

    # ── 阈值-笔数对齐 ──
    th_rows = {}
    for r in thc:
        th_rows.setdefault(r["cfg"], []).append(r)
    aligned = []
    for cfg, rs in th_rows.items():
        if not rs:
            continue
        best = min(rs, key=lambda r: abs(r["n_buy"] - base["n_buy_in_window"]))
        aligned.append(
            {
                "cfg": cfg,
                "buy_th": best["buy_th"],
                "n_buy": best["n_buy"],
                "n_gap": best["n_buy"] - base["n_buy_in_window"],
                "t5": best.get("t5"),
                "t10": best.get("t10"),
                "t20": best.get("t20"),
            }
        )

    out = {
        "src": SRC,
        "base": {"label": BASE_LABEL, "n_buy": base["n_buy_in_window"], "holds": base_means,
                 "total_eq": round(base_eq, 2)},
        "months": months,
        "C0_selfcheck": c0,
        "results": results,
        "th_aligned": aligned,
    }
    json.dump(out, open(os.path.join(REPORTS, "_p64_eval.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # ── markdown ──
    L = []
    L.append("# P64 连续加权评测（RPS 0.25 → 0.30/0.35/0.40）\n")
    L.append("窗口 %s~%s，%d 只，%d 个信号日，成本 %.4f\n"
             % (d["meta"]["window"]["start"], d["meta"]["window"]["end"],
                d["meta"]["n_stocks"], d["meta"]["window"]["signal_days"],
                d["meta"]["cost_roundtrip"]))
    L.append("基准 `%s`：%d 笔，T+5 %s / T+10 %s / T+20 %s\n"
             % (BASE_LABEL, base["n_buy_in_window"], pct(base_means["5"]),
                pct(base_means["10"]), pct(base_means["20"])))
    L.append("C0 自检（%s ≡ base）：**%s**\n" % (ANCHOR, "通过" if c0 else "失败"))
    L.append("## 全窗口\n")
    L.append("| 变体 | 笔数 | T+5 | T+10 | T+20 | 当量比 | C1 | C2 | C3 | C4 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        L.append("| %s | %d | %s | %s | %s | %s | %s | %s | %s | %s |"
                 % (r["label"], r["n_buy"], pct(r["holds"]["5"]), pct(r["holds"]["10"]),
                    pct(r["holds"]["20"]), r["eq_ratio"],
                    "PASS" if r["C1"] else "FAIL",
                    "PASS" if r["C2"] else "FAIL",
                    "-" if r["C3"] is None else ("PASS" if r["C3"] else "FAIL"),
                    "-" if r["C4"] is None else ("PASS" if r["C4"] else "FAIL")))
    L.append("\n## 分段 / 切分明细（T+5、T+10）\n")
    L.append("| 变体 | IS | OOS | k=4 | k=6 | k=8 | 202609 |")
    L.append("|---|---|---|---|---|---|---|")
    for r in results:
        isok = all(x["ok"] for x in r["seg"] if x["window"] == "IS")
        oosok = all(x["ok"] for x in r["seg"] if x["window"] == "OOS")
        sk = {x["k"]: x["ok"] for x in r["split"]}
        L.append("| %s | %s | %s | %s | %s | %s | %s |"
                 % (r["label"],
                    "PASS" if isok else "FAIL", "PASS" if oosok else "FAIL",
                    *["-" if sk.get(k) is None else ("PASS" if sk[k] else "FAIL") for k in (4, 6, 8)],
                    pct(r["sep202609"])))
    L.append("\n## 阈值-笔数对齐（剥离「变相加严」）\n")
    L.append("基准 %d 笔。各变体在 th_curve 中取笔数最接近基准的一档：\n" % base["n_buy_in_window"])
    L.append("| 变体 | 对齐阈值 | 笔数 | 笔数差 | T+5 | T+10 | T+20 |")
    L.append("|---|---|---|---|---|---|---|")
    for a in sorted(aligned, key=lambda x: x["cfg"]):
        L.append("| %s | %.0f | %d | %+d | %s | %s | %s |"
                 % (a["cfg"], a["buy_th"], a["n_buy"], a["n_gap"],
                    pct(a["t5"]), pct(a["t10"]), pct(a["t20"])))
    md = "\n".join(L)
    open(os.path.join(REPORTS, "_p64_eval.md"), "w", encoding="utf-8").write(md)
    print(md)


if __name__ == "__main__":
    sys.exit(main())
