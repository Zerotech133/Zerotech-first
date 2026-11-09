"""B1 动量/反转周期拆分分析。

用户目标：把 P1 的“动量族整体反转”拆成 5/21/60/120 日四个回看周期，分别验证
哪一段的反转先验最强，并给出分周期方向表。P0 方向表不改、不覆盖。

用法：
  python tools/research/b1_horizon_analysis.py \
      --start 20230101 --end 20261031 \
      --out research/reports/b1_horizon_reversal_20261106.json \
      --md deliverables/2026-11-06_B1动量反转周期拆分.md
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import factor_lib as fl  # noqa: E402


HORIZONS = (5, 21, 60, 120)
FAMILY = "Tushare-周期拆分"
WINDOWS = {
    "full": ("20230103", "20261031"),
    "dev": ("20230403", "20241231"),
    "holdout": ("20250102", "20260929"),
}


def horizon_specs() -> dict[str, fl.FactorSpec]:
    """构造 5/21/60/120 日过去收益因子；direction=-1 表示反转先验。"""
    specs: dict[str, fl.FactorSpec] = {}
    for h in HORIZONS:
        name = f"b1_mom_{h}d"
        def _compute(panel: fl.Panel, hh: int = h) -> pd.DataFrame:
            ac = panel.fields["adj_close"]
            return ac / ac.shift(int(hh)) - 1.0
        specs[name] = fl.FactorSpec(
            name=name, family=FAMILY, kind="continuous", direction=-1,
            description=f"过去 {h} 个交易日复权收益；P1 反转先验方向 -1",
            source="factor_lib 周期拆分（用户 m08123）",
            requires=("adj_close",), compute=_compute,
        )
    return specs


def _metric_row(window: str, name: str, f: dict) -> dict:
    directed = f.get("ic_directed") or {}
    raw = f.get("ic") or {}
    spread = ((f.get("quintiles") or {}).get("top_minus_bottom") or {})
    return {
        "window": window,
        "factor": name,
        "horizon_days": int(name.rsplit("_", 1)[-1].rstrip("d")),
        "direction": int(f.get("direction", -1)),
        "n_days": int(f.get("n_days") or 0),
        "ic_raw_mean": raw.get("mean"),
        "ic_raw_t": raw.get("t"),
        "ic_reversal_mean": directed.get("mean"),
        "ic_reversal_t": directed.get("t"),
        "ic_reversal_positive_rate": directed.get("positive_rate"),
        "long_short_reversal_ann_pct": spread.get("directed_ann_pct"),
        "long_short_raw_ann_pct": spread.get("ann_pct"),
    }


def _direction_advice(full: dict, dev: dict, holdout: dict) -> dict:
    """按“holdout 反转 IC 为正且 t≥1.5”优先，其次看 dev 同向，否则标记不明确。"""
    h_ic = holdout.get("ic_reversal_mean")
    h_t = holdout.get("ic_reversal_t")
    d_ic = dev.get("ic_reversal_mean")
    f_ic = full.get("ic_reversal_mean")
    if h_ic is None or h_t is None:
        return {"direction": None, "label": "数据不足", "reason": "holdout IC 缺失"}
    if h_ic > 0 and h_t >= 1.5:
        return {"direction": -1, "label": "反转(-1)",
                "reason": f"holdout 反转IC={h_ic:.4f}/t={h_t:.2f}≥1.5；dev={d_ic}"}
    if h_ic < 0 and h_t <= -1.5:
        return {"direction": 1, "label": "动量(+1)",
                "reason": f"holdout 反转IC={h_ic:.4f}/t={h_t:.2f}≤-1.5；dev={d_ic}"}
    return {"direction": None, "label": "不明确",
            "reason": f"holdout 反转IC={h_ic:.4f}/t={h_t:.2f} 未达显著；dev={d_ic}; full={f_ic}"}


def run(start: str, end: str, n_boot: int = 100) -> dict:
    panel = fl.load_panel(start, end, horizon=20, universe="tradable", load_roe=False, warmup=140)
    specs = horizon_specs()
    names = list(specs)
    results: dict[str, dict] = {}
    for window, (ws, we) in WINDOWS.items():
        results[window] = fl.evaluate_panel(
            panel, factor_names=names, horizon=20, cost=0.003,
            start=ws, end=we, is_end=None if window != "full" else "20241231",
            n_boot=n_boot, factor_specs=specs,
        )

    rows: list[dict] = []
    for window, res in results.items():
        for name in names:
            rows.append(_metric_row(window, name, res["factors"][name]))

    by_window = {w: {r["factor"]: r for r in rows if r["window"] == w} for w in WINDOWS}
    table: list[dict] = []
    for name in names:
        full = by_window["full"][name]
        dev = by_window["dev"][name]
        hold = by_window["holdout"][name]
        advice = _direction_advice(full, dev, hold)
        table.append({
            "factor": name, "horizon_days": full["horizon_days"],
            "full": full, "dev": dev, "holdout": hold,
            **advice,
        })
    table.sort(key=lambda x: x["horizon_days"])
    payload = {
        "meta": {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "start": start, "end": end, "forward_horizon": 20,
            "forward_cost": 0.003, "panel": {"dates": int(panel.shape[0]), "codes": int(panel.shape[1])},
            "windows": WINDOWS, "n_boot": n_boot,
            "note": "方向 -1 表示过去涨幅高→未来收益低（反转）；反转 IC = -原始 IC。"
                    "本分析只拆 B1 动量族，不修改 P0 方向表。",
        },
        "table": table,
        "raw": results,
        "interpretation": _interpret(table),
    }
    return payload


def _interpret(table: list[dict]) -> dict:
    valid = [t for t in table if t.get("holdout", {}).get("ic_reversal_mean") is not None]
    ranked = sorted(valid, key=lambda t: abs(t["holdout"]["ic_reversal_mean"]), reverse=True)
    return {
        "holdout_strongest_by_abs_ic": ranked[0]["factor"] if ranked else None,
        "holdout_ranking": [
            {"factor": t["factor"], "ic": t["holdout"]["ic_reversal_mean"],
             "t": t["holdout"]["ic_reversal_t"]}
            for t in ranked
        ],
        "direction_summary": {
            t["factor"]: t["label"] for t in table
        },
    }


def write_md(payload: dict, path: Path) -> None:
    lines = [
        "# B1 动量反转周期拆分（5/21/60/120 日）",
        "",
        f"- 生成时间：{payload['meta']['generated_at']}",
        f"- 面板：{payload['meta']['panel']['dates']} 日 × {payload['meta']['panel']['codes']} 只；前向 20 日 CAR；成本 30bp",
        "- 方向：-1 = 过去涨幅高者未来收益低（反转）；表中“反转 IC”= -原始动量 IC",
        "- P0 方向表未改动；本表只拆 B1 动量族，用于回答“哪段反转最强”。",
        "",
        "## 分周期方向表",
        "",
        "| 周期 | P1 方向 | 全样本 反转IC / t | dev 反转IC / t | holdout 反转IC / t | holdout 多空年化 | holdout 正IC率 | 方向建议 |",
        "|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for t in payload["table"]:
        f, d, h = t["full"], t["dev"], t["holdout"]
        def fmt(x, y):
            return f"{x:+.4f} / {y:+.2f}" if x is not None and y is not None else "—"
        ann = h.get("long_short_reversal_ann_pct")
        pr = h.get("ic_reversal_positive_rate")
        lines.append(
            f"| {t['horizon_days']}d | -1 | {fmt(f.get('ic_reversal_mean'), f.get('ic_reversal_t'))} | "
            f"{fmt(d.get('ic_reversal_mean'), d.get('ic_reversal_t'))} | "
            f"{fmt(h.get('ic_reversal_mean'), h.get('ic_reversal_t'))} | "
            f"{ann:+.2f}% | {pr:.1%} | {t['label']} |" if ann is not None and pr is not None else
            f"| {t['horizon_days']}d | -1 | {fmt(f.get('ic_reversal_mean'), f.get('ic_reversal_t'))} | "
            f"{fmt(d.get('ic_reversal_mean'), d.get('ic_reversal_t'))} | "
            f"{fmt(h.get('ic_reversal_mean'), h.get('ic_reversal_t'))} | — | — | {t['label']} |"
        )
    lines += [
        "",
        "## 逐年反转 IC（全样本，方向 -1）",
        "",
        "| 周期 | 2023 | 2024 | 2025 | 2026 |",
        "|---:|---:|---:|---:|---:|",
    ]
    for t in payload["table"]:
        name = t["factor"]
        by_year = (payload["raw"].get("full", {}).get("factors", {}).get(name, {}) or {}).get("by_year", {}) or {}
        cells = []
        for y in ("2023", "2024", "2025", "2026"):
            v = by_year.get(y) or {}
            mean, tt = v.get("mean"), v.get("t")
            cells.append(f"{mean:+.4f} / {tt:+.2f}" if mean is not None and tt is not None else "—")
        lines.append(f"| {t['horizon_days']}d | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## 结论",
        "",
    ]
    interp = payload["interpretation"]
    if interp.get("holdout_strongest_by_abs_ic"):
        top = interp["holdout_ranking"][0]
        lines.append(f"- holdout 反转 IC 绝对值最强：**{top['factor']}**（IC {top['ic']:+.4f}，t {top['t']:+.2f}）。")
    if len(interp.get("holdout_ranking", [])) >= 2:
        ranked = interp["holdout_ranking"]
        lines.append("- holdout 排序：" + " > ".join(
            f"{r['factor']}（{r['ic']:+.4f}）" for r in ranked) + "。")
    lines += [
        "- 方向建议规则：holdout 反转 IC>0 且 t≥1.5 → 反转(-1)；holdout 反转 IC<0 且 t≤-1.5 → 动量(+1)；否则“不明确”。",
        "- 结论口径：IC 强不等于可交易；需同时看多空年化、正 IC 率和逐年稳定性。",
        "- 注意：holdout 为 2025-01-02–2026-09-29 时间外推；dev 2023-04-03–2024-12-31。若 dev/holdout 方向不一致，不能把单窗口结果当稳定先验。",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20230101")
    ap.add_argument("--end", default="20261031")
    ap.add_argument("--n-boot", type=int, default=100)
    ap.add_argument("--out", default="research/reports/b1_horizon_reversal_20261106.json")
    ap.add_argument("--md", default="deliverables/2026-11-06_B1动量反转周期拆分.md")
    args = ap.parse_args()
    payload = run(args.start, args.end, n_boot=args.n_boot)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    write_md(payload, Path(args.md))
    print(json.dumps({"out": str(out), "md": args.md, "table": payload["table"],
                      "interpretation": payload["interpretation"]}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
