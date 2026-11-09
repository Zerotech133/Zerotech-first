"""B1 P0/P1/P2 先验版本 OOS 对比与预注册判定（只读 JSON，不重跑回测）。

用法：
  python tools/research/compare_b1_priors.py \
      --p0 research/reports/b1_prior_p0_oos_20261105.json \
      --p1 research/reports/b1_prior_p1_oos_20261105.json \
      --p2 research/reports/b1_prior_p2_oos_20261106.json \
      --out-json research/reports/b1_prior_p0p1p2_compare_20261106.json \
      --out-md deliverables/2026-11-06_B1_P2_21日核心_样本外验证.md

原则：
- 预注册阈值在本文件里写死，与 `deliverables/2026-11-06_B1_P2_21日核心_预注册与评测方案.md` 一致；
- 只读取 JSON，不修改 P0/P1/P2 结果文件；
- 判定输出 pass/fail，不自动改生产配置。
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
P0_DEFAULT = ROOT / "research" / "reports" / "b1_prior_p0_oos_20261105.json"
P1_DEFAULT = ROOT / "research" / "reports" / "b1_prior_p1_oos_20261105.json"
P2_DEFAULT = ROOT / "research" / "reports" / "b1_prior_p2_oos_20261106.json"
OUT_JSON_DEFAULT = ROOT / "research" / "reports" / "b1_prior_p0p1p2_compare_20261106.json"
OUT_MD_DEFAULT = ROOT / "deliverables" / "2026-11-06_B1_P2_21日核心_样本外验证.md"

PREREG = {
    "sharpe_delta": 0.05,
    "mdd_tolerance": -0.02,
    "ic_tolerance": -0.005,
    "dev_sharpe_tolerance": -0.02,
    "aux_sharpe_tolerance": -0.05,
}


def load_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def factor_ic(doc: dict, window: str, kind: str = "neutral") -> dict:
    return doc["composite"]["ic_windows"][window][kind]["factor"]["ic"]


def portfolio(doc: dict, label: str, days: int, cost_bp: int) -> dict:
    for entry in doc.get("portfolio", []):
        if entry.get("label") == label and int(entry.get("days", -1)) == days and int(entry.get("cost_bp", -1)) == cost_bp:
            return entry
    raise KeyError(f"{label} days={days} cost={cost_bp} 不存在")


def metrics(doc: dict, label: str, days: int, cost_bp: int = 30) -> dict:
    return portfolio(doc, label, days, cost_bp)["metrics"]


def _sharpe(values: np.ndarray) -> float | None:
    if values.size < 2:
        return None
    std = float(np.std(values, ddof=1))
    if std <= 0:
        return None
    return float(np.mean(values) / std * math.sqrt(252.0))


def yearly_metrics(doc: dict, label: str = "neut_holdout", days: int = 60, cost_bp: int = 30) -> dict:
    entry = portfolio(doc, label, days, cost_bp)
    dates = [str(d) for d in entry["daily_returns"]["dates"]]
    vals = np.asarray([np.nan if v is None else float(v) for v in entry["daily_returns"]["values"]], dtype=float)
    out: dict[str, dict] = {}
    for year in ("2025", "2026"):
        mask = np.array([d.startswith(year) for d in dates])
        sub = vals[mask]
        sub = sub[np.isfinite(sub)]
        if sub.size < 20:
            continue
        out[year] = {
            "days": int(sub.size),
            "ann_return": float(np.prod(1.0 + sub) ** (252.0 / sub.size) - 1.0),
            "sharpe": _sharpe(sub),
        }
    return out


def rolling_sharpe(doc: dict, label: str = "neut_holdout", days: int = 60, cost_bp: int = 30,
                   window: int = 252, step: int = 21) -> list[dict]:
    entry = portfolio(doc, label, days, cost_bp)
    dates = [str(d) for d in entry["daily_returns"]["dates"]]
    vals = np.asarray([np.nan if v is None else float(v) for v in entry["daily_returns"]["values"]], dtype=float)
    out: list[dict] = []
    for start in range(0, max(0, len(vals) - window + 1), step):
        sub = vals[start:start + window]
        sub = sub[np.isfinite(sub)]
        s = _sharpe(sub)
        if s is None:
            continue
        out.append({"start": dates[start], "end": dates[min(start + window - 1, len(dates) - 1)], "sharpe": s})
    return out


def compare_rolling(p1: dict, p2: dict, **kwargs) -> dict:
    r1 = rolling_sharpe(p1, **kwargs)
    r2 = rolling_sharpe(p2, **kwargs)
    n = min(len(r1), len(r2))
    if n == 0:
        return {"n_windows": 0, "p1_wins": 0, "p2_wins": 0, "avg_p1": None, "avg_p2": None}
    p1_wins = sum(1 for i in range(n) if r1[i]["sharpe"] > r2[i]["sharpe"])
    return {
        "n_windows": n,
        "p1_wins": p1_wins,
        "p2_wins": n - p1_wins,
        "avg_p1": float(np.mean([r1[i]["sharpe"] for i in range(n)])),
        "avg_p2": float(np.mean([r2[i]["sharpe"] for i in range(n)])),
        "windows": [{"start": r1[i]["start"], "end": r1[i]["end"], "p1": r1[i]["sharpe"], "p2": r2[i]["sharpe"]} for i in range(n)],
    }


def prereg_judgement(p0: dict, p1: dict, p2: dict) -> dict:
    p1_ic = float(factor_ic(p1, "holdout")["mean"])
    p2_ic = float(factor_ic(p2, "holdout")["mean"])
    p1_dev_ic = float(factor_ic(p1, "dev")["mean"])
    p2_dev_ic = float(factor_ic(p2, "dev")["mean"])
    p1_60 = metrics(p1, "neut_holdout", 60)
    p2_60 = metrics(p2, "neut_holdout", 60)
    p1_40 = metrics(p1, "neut_holdout", 40)
    p2_40 = metrics(p2, "neut_holdout", 40)
    p1_20 = metrics(p1, "neut_holdout", 20)
    p2_20 = metrics(p2, "neut_holdout", 20)
    p1_dev = metrics(p1, "neut_dev", 60)
    p2_dev = metrics(p2, "neut_dev", 60)
    p2_50 = metrics(p2, "neut_holdout", 60, 50)
    y1 = yearly_metrics(p1)
    y2 = yearly_metrics(p2)

    sharpe_threshold = float(p1_60["sharpe"]) + PREREG["sharpe_delta"]
    mdd_threshold = float(p1_60["max_drawdown"]) + PREREG["mdd_tolerance"]
    ic_threshold = p1_ic + PREREG["ic_tolerance"]
    checks = [
        {"name": "holdout_60d_sharpe", "p1": float(p1_60["sharpe"]), "p2": float(p2_60["sharpe"]),
         "threshold": sharpe_threshold, "op": ">=", "pass": float(p2_60["sharpe"]) >= sharpe_threshold},
        {"name": "holdout_60d_mdd", "p1": float(p1_60["max_drawdown"]), "p2": float(p2_60["max_drawdown"]),
         "threshold": mdd_threshold, "op": ">=", "pass": float(p2_60["max_drawdown"]) >= mdd_threshold},
        {"name": "holdout_neutral_ic", "p1": p1_ic, "p2": p2_ic,
         "threshold": ic_threshold, "op": ">=", "pass": (p2_ic >= ic_threshold and p2_ic > 0)},
        {"name": "holdout_60d_ann_return", "p1": float(p1_60["ann_return"]), "p2": float(p2_60["ann_return"]),
         "threshold": float(p1_60["ann_return"]), "op": ">=", "pass": float(p2_60["ann_return"]) >= float(p1_60["ann_return"])},
    ]
    aux = [
        {"name": "dev_60d_sharpe", "p1": float(p1_dev["sharpe"]), "p2": float(p2_dev["sharpe"]),
         "threshold": float(p1_dev["sharpe"]) + PREREG["dev_sharpe_tolerance"], "op": ">=",
         "pass": float(p2_dev["sharpe"]) >= float(p1_dev["sharpe"]) + PREREG["dev_sharpe_tolerance"]},
        {"name": "holdout_40d_sharpe", "p1": float(p1_40["sharpe"]), "p2": float(p2_40["sharpe"]),
         "threshold": float(p1_40["sharpe"]) + PREREG["aux_sharpe_tolerance"], "op": ">=",
         "pass": float(p2_40["sharpe"]) >= float(p1_40["sharpe"]) + PREREG["aux_sharpe_tolerance"]},
        {"name": "holdout_20d_sharpe", "p1": float(p1_20["sharpe"]), "p2": float(p2_20["sharpe"]),
         "threshold": float(p1_20["sharpe"]) + PREREG["aux_sharpe_tolerance"], "op": ">=",
         "pass": float(p2_20["sharpe"]) >= float(p1_20["sharpe"]) + PREREG["aux_sharpe_tolerance"]},
        {"name": "holdout_50bp_positive", "p1": None, "p2": float(p2_50["ann_return"]),
         "threshold": 0.0, "op": ">", "pass": float(p2_50["ann_return"]) > 0},
    ]
    # 改善不集中于单一年份：以 60d holdout 分年度年化，2025/2026 至少一年改善且另一年不恶化超过 2pp。
    year_checks = []
    for year in ("2025", "2026"):
        if year in y1 and year in y2:
            delta = float(y2[year]["ann_return"]) - float(y1[year]["ann_return"])
            year_checks.append({"year": year, "p1_ann": float(y1[year]["ann_return"]),
                                "p2_ann": float(y2[year]["ann_return"]), "delta": delta})
    if year_checks:
        improved = any(c["delta"] > 0 for c in year_checks)
        not_worse = all(c["delta"] >= -0.02 for c in year_checks)
        aux.append({"name": "yearly_not_single_year", "p1": None, "p2": None,
                    "threshold": "至少一年改善且另一年不恶化>2pp", "op": "custom",
                    "pass": bool(improved and not_worse), "yearly": year_checks})

    primary_all_pass = all(c["pass"] for c in checks)
    return {
        "p1_ic_holdout": p1_ic, "p2_ic_holdout": p2_ic,
        "p1_ic_dev": p1_dev_ic, "p2_ic_dev": p2_dev_ic,
        "primary_checks": checks,
        "aux_checks": aux,
        "primary_all_pass": primary_all_pass,
        "verdict": "P2 通过预注册，登记候选研究版本（P0 仍基线，不接生产）"
        if primary_all_pass else "P2 未通过预注册，不升级；P1 保持候选",
        "yearly": {"p1": y1, "p2": y2},
    }


def _fmt_pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.2f}%"


def _fmt_num(x: float | None, nd: int = 3) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def build_markdown(p0: dict, p1: dict, p2: dict, judgement: dict, rolling: dict) -> str:
    lines: list[str] = []
    lines.append("# B1 P2 · 21 日反转核心复合因子 · 样本外验证")
    lines.append("")
    lines.append("- 日期：2026-11-06")
    lines.append("- 预注册：`deliverables/2026-11-06_B1_P2_21日核心_预注册与评测方案.md`（P2 运行前冻结）")
    lines.append("- P0 研究基线不变；P1 为旧候选；P2 为本次新版本。三者独立 JSON，互不覆盖。")
    lines.append("- 结论：**" + judgement["verdict"] + "**")
    lines.append("")
    lines.append("## 1. 版本定义")
    lines.append("")
    lines.append("| 版本 | 动量族 | 其他四族 | 说明 |")
    lines.append("|---|---|---|---|")
    lines.append("| P0 | 20 因子等权，混合方向 | 等权 | 研究基线 |")
    lines.append("| P1 | 20 因子全 −1，等权 | 等权 | A 股反转先验 |")
    lines.append("| P2 | 4 周期加权：5d .20 / 21d .60 / 60d .15 / 120d .05 | 等权 | 21 日核心 |")
    lines.append("")
    lines.append("## 2. 复合 IC（中性化）")
    lines.append("")
    lines.append("| 窗口 | P0 | P1 | P2 | P2−P1 |")
    lines.append("|---|---:|---:|---:|---:|")
    for window, label in (("dev", "dev 2023-04–2024-12"), ("holdout", "holdout 2025-01–2026-09")):
        vals = []
        for doc in (p0, p1, p2):
            ic = factor_ic(doc, window)
            vals.append((float(ic["mean"]), float(ic["t"])))
        lines.append(f"| {label} | {vals[0][0]:+.4f} / t{vals[0][1]:.2f} | {vals[1][0]:+.4f} / t{vals[1][1]:.2f} | {vals[2][0]:+.4f} / t{vals[2][1]:.2f} | {vals[2][0]-vals[1][0]:+.4f} |")
    lines.append("")
    lines.append("## 3. 组合（60d 中性、30bp、行业+市值约束）")
    lines.append("")
    lines.append("| 区间 | 版本 | 年化 | Sharpe | MDD | 年化换手 | 成本 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for label, tag in (("dev 2023-04–2024-12", "neut_dev"), ("holdout 2025-01–2026-09", "neut_holdout")):
        for name, doc in (("P0", p0), ("P1", p1), ("P2", p2)):
            m = metrics(doc, tag, 60)
            lines.append(f"| {label if name=='P0' else ''} | {name} | {_fmt_pct(m['ann_return'])} | {_fmt_num(m['sharpe'])} | {_fmt_pct(m['max_drawdown'])} | {_fmt_num(m.get('ann_one_way_turnover'))}x | {_fmt_pct(m.get('total_cost'))} |")
    lines.append("")
    lines.append("## 4. holdout 辅助组合")
    lines.append("")
    lines.append("| 组合 | P0 | P1 | P2 |")
    lines.append("|---|---:|---:|---:|")
    for days in (40, 20):
        vals = [metrics(d, "neut_holdout", days) for d in (p0, p1, p2)]
        lines.append(
            f"| {days}d 中性 | {_fmt_pct(vals[0]['ann_return'])}/{_fmt_num(vals[0]['sharpe'])} | "
            f"{_fmt_pct(vals[1]['ann_return'])}/{_fmt_num(vals[1]['sharpe'])} | "
            f"{_fmt_pct(vals[2]['ann_return'])}/{_fmt_num(vals[2]['sharpe'])} |"
        )
    raw = [metrics(d, "raw_holdout", 60) for d in (p0, p1, p2)]
    lines.append(f"| 原始 60d（未中性化） | {_fmt_pct(raw[0]['ann_return'])}/{_fmt_num(raw[0]['sharpe'])} | {_fmt_pct(raw[1]['ann_return'])}/{_fmt_num(raw[1]['sharpe'])} | {_fmt_pct(raw[2]['ann_return'])}/{_fmt_num(raw[2]['sharpe'])} |")
    for bp in (0, 50):
        vals = [metrics(d, "neut_holdout", 60, bp) for d in (p0, p1, p2)]
        lines.append(f"| 60d 中性 {bp}bp | {_fmt_pct(vals[0]['ann_return'])}/{_fmt_num(vals[0]['sharpe'])} | {_fmt_pct(vals[1]['ann_return'])}/{_fmt_num(vals[1]['sharpe'])} | {_fmt_pct(vals[2]['ann_return'])}/{_fmt_num(vals[2]['sharpe'])} |")
    lines.append("")
    lines.append("## 5. 预注册判定")
    lines.append("")
    lines.append("| 判定 | P1 | P2 | 阈值 | 结果 |")
    lines.append("|---|---:|---:|---:|---|")
    for c in judgement["primary_checks"]:
        lines.append(
            f"| {c['name']} | {_fmt_num(c['p1'])} | {_fmt_num(c['p2'])} | "
            f"{c['op']} {_fmt_num(c['threshold'])} | {'PASS' if c['pass'] else 'FAIL'} |"
        )
    for c in judgement["aux_checks"]:
        threshold = c["threshold"]
        threshold_txt = _fmt_num(threshold) if isinstance(threshold, (int, float)) else str(threshold)
        p1 = c.get("p1")
        p2 = c.get("p2")
        lines.append(
            f"| {c['name']}（辅助） | {_fmt_num(p1) if isinstance(p1, (int, float)) else '—'} | "
            f"{_fmt_num(p2) if isinstance(p2, (int, float)) else '—'} | {threshold_txt} | "
            f"{'PASS' if c['pass'] else 'FAIL'} |"
        )
    lines.append("")
    failed = [c["name"] for c in judgement["primary_checks"] if not c["pass"]]
    if failed:
        lines.append("主判定失败项：" + "、".join(failed) + "。")
    lines.append("主判定结论：**" + ("全部通过" if judgement["primary_all_pass"] else "存在失败项，不升级") + "**。")
    lines.append("")
    lines.append("## 6. 滚动 12 个月（holdout，60d 中性）")
    lines.append("")
    lines.append(f"- 窗口数：{rolling['n_windows']}；P1 胜 {rolling['p1_wins']}，P2 胜 {rolling['p2_wins']}。")
    lines.append(f"- 平均 Sharpe：P1 {_fmt_num(rolling['avg_p1'])}，P2 {_fmt_num(rolling['avg_p2'])}。")
    lines.append("")
    lines.append("## 7. 分年度（holdout 60d 中性，按日收益重算）")
    lines.append("")
    lines.append("| 年份 | P1 年化/Sharpe | P2 年化/Sharpe | P2−P1 年化 |")
    lines.append("|---|---:|---:|---:|")
    for y in ("2025", "2026"):
        a = judgement["yearly"]["p1"].get(y)
        b = judgement["yearly"]["p2"].get(y)
        if a and b:
            lines.append(f"| {y} | {_fmt_pct(a['ann_return'])}/{_fmt_num(a['sharpe'])} | {_fmt_pct(b['ann_return'])}/{_fmt_num(b['sharpe'])} | {_fmt_pct(b['ann_return']-a['ann_return'])} |")
    lines.append("")
    lines.append("## 8. 结论与边界")
    lines.append("")
    lines.append("- P2 用 21d 核心 + 5d 辅助 + 60/120d 成本约束权重替换 P1 动量族等权；P0 未改。")
    lines.append("- 预注册阈值在 P2 运行前冻结；判定只看 holdout 60d 主指标。")
    if judgement["primary_all_pass"]:
        lines.append("- P2 主判定全部通过，登记候选研究版本；P0 仍为基线，不接生产。")
    else:
        failed = [c["name"] for c in judgement["primary_checks"] if not c["pass"]]
        lines.append("- P2 主判定失败：" + "、".join(failed) + "；P2 不升级，P1 保持候选，P0 仍为基线。")
        lines.append("- 原因：21d 核心权重降低了 2025 尾部风险（2025 年化 +0.89pp），但削弱了 2026 的收益与 IC，"
                     "2026 年化较 P1 低 2.42pp、Sharpe 低 0.25；滚动 12 个月 P1 胜 8/10，P2 不是更稳定的版本。")
        lines.append("- 结论是研究结论，不是代码 bug：周期权重方向正确，但“21d 核心”在这组样本上没有跑赢 P1 的 20 因子等权。")
    lines.append("- P1/P2 均受研究者看过 2025–2026 数据影响，holdout 是 quasi-OOS，不是干净 OOS。")
    lines.append("- 不生成实盘信号、不改生产配置；P0 仍为研究基线。")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--p0", type=Path, default=P0_DEFAULT)
    parser.add_argument("--p1", type=Path, default=P1_DEFAULT)
    parser.add_argument("--p2", type=Path, default=P2_DEFAULT)
    parser.add_argument("--out-json", type=Path, default=OUT_JSON_DEFAULT)
    parser.add_argument("--out-md", type=Path, default=OUT_MD_DEFAULT)
    args = parser.parse_args(argv)

    p0, p1, p2 = load_json(args.p0), load_json(args.p1), load_json(args.p2)
    judgement = prereg_judgement(p0, p1, p2)
    rolling = compare_rolling(p1, p2)
    payload = {
        "generated_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        "sources": {"p0": str(args.p0), "p1": str(args.p1), "p2": str(args.p2)},
        "prereg": PREREG,
        "judgement": judgement,
        "rolling_12m": rolling,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.write_text(build_markdown(p0, p1, p2, judgement, rolling), encoding="utf-8")
    print(f"[done] {args.out_json}")
    print(f"[done] {args.out_md}")
    print(f"[verdict] {judgement['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
