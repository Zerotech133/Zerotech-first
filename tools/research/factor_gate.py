# -*- coding: utf-8 -*-
"""因子诊断准入 preflight（只读）v1（2026-11-01 WO-STRAT-FGATE-20261101 / task-27）。

用途：把 `tools/research/factor_diagnostics.py` 的诊断结果套上一组**阈值**，
输出 `pass / warn / fail` 的研究建议，供因子入池前人工复核（S1/S2 前置检查）。
**只读、离线、不自动晋升**：不写策略注册表、不产出选股清单、不新增 API/flag/常驻服务。

输入面板（CSV 或 JSON 记录数组）：
    date,symbol,factor,forward_return
其中 `forward_return` 为小数（0.02=2%），由调用方按 PIT 口径自备。
阈值（默认见 DEFAULT_THRESHOLDS，可 JSON 文件/CLI 覆盖）：
    min_rank_ic_mean / min_icir / min_positive_rate / min_coverage / min_days
    / min_quantile_spearman / min_long_short_ir / max_turnover / max_bad_ratio

输出：报告 dict（schema=factor_gate/v1）
    status: pass|warn|fail
    metrics: IC/ICIR/正比例/分位收益/分位单调/多空差/换手/覆盖率/样本数
    checks: 逐项 {name, metric, value, threshold, severity, status, detail}
    diagnostics: 完整 factor_diagnostics 报告（可追溯）
    notes: 只读/不自动晋升边界说明

退出码：0=pass，1=warn，2=fail，3=数据/用法错误（缺列/空/文件坏等 fail-visible）。

CLI 示例：
    python tools/research/factor_gate.py --csv panel.csv
    python tools/research/factor_gate.py --csv panel.csv --thresholds gate.json --json
    python tools/research/factor_gate.py --panel-json panel.json --out research/reports/gate_x.json

来源说明：本文件依赖 `tools/research/factor_diagnostics.py`（同目录）。该文件在本分支
为**复制参考**自 `WO-STRAT-FDIAG-20261101`（commit `18590d4d3c8fcaccbec2d98980e444246089cb7c`，
文件 sha256 `96CD338DA7069A67F2C1D4C36FD3F1A7807943EF595BBAFEB36B5D7BC3C6C51F`），
合并顺序：**FDIAG 先合并，FGATE 后合并**；两者该文件应字节一致，冲突时以 FDIAG 版本为准。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

SCHEMA = "factor_gate/v1"
DIAGNOSTICS_VERSION = "factor_diagnostics/v1"
DEFAULT_THRESHOLDS = {
    # 硬闸（fail）：不达标即不建议入池
    "min_rank_ic_mean": 0.02,      # 日度 rank IC 均值下限
    "min_icir": 0.20,              # ICIR（mean/std，不年化）下限
    "min_coverage": 20.0,          # 每日平均截面股票数下限（可运行最小闸）
    "min_days": 5,                 # 有效 IC 交易日数下限
    "max_bad_ratio": 0.20,         # 坏行比例上限
    # 软闸（warn）：提示风险，不直接判 fail
    "min_positive_rate": 0.55,     # IC 正比例下限
    "min_quantile_spearman": 0.50, # 分位均值单调性（分位序号 vs 收益 Spearman）下限
    "min_long_short_ir": 0.20,     # 多空差 IR（mean/std）下限
    "max_turnover": 0.80,          # top 分位成员替换率上限（过高=交易成本风险）
}
HARD_KEYS = ("min_rank_ic_mean", "min_icir", "min_coverage", "min_days",
             "max_bad_ratio")
SOFT_KEYS = ("min_positive_rate", "min_quantile_spearman",
             "min_long_short_ir", "max_turnover")
CLI_FLAGS = {
    "min_rank_ic_mean": "--min-rank-ic",
    "min_icir": "--min-icir",
    "min_positive_rate": "--min-positive-rate",
    "min_coverage": "--min-coverage",
    "min_days": "--min-days",
    "min_quantile_spearman": "--min-quantile-spearman",
    "min_long_short_ir": "--min-long-short-ir",
    "max_turnover": "--max-turnover",
    "max_bad_ratio": "--max-bad-ratio",
}
NOTES = [
    "只读 preflight：仅输出研究建议（pass/warn/fail），不自动晋升、不写策略注册表、不产出选股清单。",
    "是否入池/上线需人工复核（N1/QA1/策略线）并按四关+影子盘+审批流程执行。",
    "不新增 API/flag/常驻服务；不联网；forward_return 的 PIT/复权/停牌由调用方负责。",
    "阈值为研究口径：默认值是「可运行最小闸」，生产复核建议用更严的 thresholds JSON。",
]


class FactorGateError(ValueError):
    """数据/用法错误（缺列/空面板/文件坏/阈值非法）——fail-visible，不静默 pass。"""


# 依赖：同目录 factor_diagnostics.py（复制参考，见模块 docstring 来源说明）
def _load_diagnostics():
    try:
        from factor_diagnostics import FactorDiagnosticsError, compute  # noqa: F401
        return compute, FactorDiagnosticsError
    except ImportError:
        import importlib.util
        path = Path(__file__).resolve().parent / "factor_diagnostics.py"
        if not path.is_file():
            raise FactorGateError(
                "缺少依赖 tools/research/factor_diagnostics.py（来源 WO-STRAT-FDIAG-20261101）")
        spec = importlib.util.spec_from_file_location(
            "factor_diagnostics_for_gate", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.compute, mod.FactorDiagnosticsError


# ---------------------------------------------------------------- 输入
def load_panel(path) -> pd.DataFrame:
    """读面板 CSV/JSON → DataFrame；文件/格式/空数据错误 fail-visible。"""
    p = Path(path)
    if not p.is_file():
        raise FactorGateError("面板文件不存在：%s" % p)
    suffix = p.suffix.lower()
    try:
        if suffix in (".csv", ".txt"):
            df = pd.read_csv(p)
        elif suffix == ".json":
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("rows") or data.get("data") or data.get("panel")
            if not isinstance(data, list) or not data:
                raise FactorGateError(
                    "JSON 面板必须是记录数组或 {rows:[...]} 且非空：%s" % p)
            df = pd.DataFrame(data)
        else:
            raise FactorGateError("不支持的扩展名 %r（仅 .csv/.json）" % suffix)
    except FactorGateError:
        raise
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        raise FactorGateError("读取面板失败：%s: %s"
                              % (type(exc).__name__, str(exc)[:200]))
    if df.empty:
        raise FactorGateError("面板为空（无数据行）：%s" % p)
    return df


def load_thresholds(path=None) -> tuple:
    """→ (thresholds dict, warnings)。JSON 覆盖默认值；未知键告警不生效。"""
    thresholds = dict(DEFAULT_THRESHOLDS)
    warnings = []
    if not path:
        return thresholds, warnings
    p = Path(path)
    if not p.is_file():
        raise FactorGateError("阈值文件不存在：%s" % p)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FactorGateError("阈值文件解析失败：%s" % str(exc)[:200])
    if not isinstance(data, dict):
        raise FactorGateError("阈值文件必须是 JSON 对象")
    for key, value in data.items():
        key = str(key).strip()
        if key not in thresholds:
            warnings.append("未知阈值键已忽略：%s" % key)
            continue
        try:
            thresholds[key] = float(value)
        except (TypeError, ValueError):
            raise FactorGateError("阈值 %s 必须是数值（收到 %r）" % (key, value))
    return thresholds, warnings


def merge_cli_thresholds(thresholds: dict, args) -> dict:
    """CLI 单项阈值覆盖 JSON/默认。"""
    out = dict(thresholds)
    for key, flag in CLI_FLAGS.items():
        value = getattr(args, flag.lstrip("-").replace("-", "_"), None)
        if value is not None:
            out[key] = float(value)
    return out


# ---------------------------------------------------------------- 闸门
def _check(name, metric, value, threshold, severity, op, detail=""):
    """构造单项检查结果；value=None → 不可用（硬闸 fail / 软闸 warn）。"""
    if value is None:
        status = "fail" if severity == "fail" else "warn"
        return {"name": name, "metric": metric, "value": None,
                "threshold": threshold, "severity": severity,
                "status": status, "detail": detail or "指标不可用（样本/方差不足）"}
    ok = (value >= threshold) if op == ">=" else (value <= threshold)
    status = "pass" if ok else ("fail" if severity == "fail" else "warn")
    return {"name": name, "metric": metric,
            "value": round(float(value), 6),
            "threshold": round(float(threshold), 6),
            "severity": severity, "status": status,
            "op": op, "detail": detail}


def evaluate(df: pd.DataFrame, thresholds=None) -> dict:
    """诊断 + 阈值 → pass/warn/fail 报告。结构性数据错误抛 FactorGateError。"""
    compute, diag_error = _load_diagnostics()
    th = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        for key, value in thresholds.items():
            if key not in th:
                raise FactorGateError("未知阈值键：%s" % key)
            try:
                th[key] = float(value)
            except (TypeError, ValueError):
                raise FactorGateError("阈值 %s 必须是数值（收到 %r）" % (key, value))
    try:
        diag = compute(df)
    except diag_error as exc:
        raise FactorGateError("因子诊断失败：%s" % exc)
    if diag.get("schema") != DIAGNOSTICS_VERSION:
        raise FactorGateError("诊断报告 schema 不符：%r" % diag.get("schema"))

    samples = diag.get("samples") or {}
    coverage = diag.get("coverage") or {}
    ic = (diag.get("ic") or {}).get("rank") or {}
    quantiles = diag.get("quantiles") or {}
    ls = quantiles.get("long_short") or {}
    turnover = diag.get("turnover") or {}

    rows_in = int(samples.get("rows_in") or 0)
    bad_rows = int(samples.get("bad_rows") or 0)
    bad_ratio = (bad_rows / rows_in) if rows_in else 1.0
    names_mean = coverage.get("names_mean")
    names_mean = float(names_mean) if names_mean is not None else None
    icir = ic.get("icir")
    ls_ir = ls.get("icir")
    q_spear = quantiles.get("quantile_spearman")
    turnover_mean = turnover.get("mean")

    checks = [
        _check("bad_rows", "坏行比例", bad_ratio, th["max_bad_ratio"],
               "fail", "<=", "bad_rows=%d/rows_in=%d" % (bad_rows, rows_in)),
        _check("coverage", "每日平均截面股票数", names_mean, th["min_coverage"],
               "fail", ">="),
        _check("min_days", "有效 IC 交易日数", ic.get("n_days"), th["min_days"],
               "fail", ">="),
        _check("rank_ic_mean", "日度 rank IC 均值", ic.get("mean"),
               th["min_rank_ic_mean"], "fail", ">="),
        _check("icir", "ICIR（不年化）", icir, th["min_icir"], "fail", ">="),
        _check("positive_rate", "IC 正比例", ic.get("positive_rate"),
               th["min_positive_rate"], "warn", ">="),
        _check("quantile_spearman", "分位单调性 Spearman", q_spear,
               th["min_quantile_spearman"], "warn", ">="),
        _check("long_short_ir", "多空差 IR", ls_ir, th["min_long_short_ir"],
               "warn", ">="),
        _check("turnover", "top 分位成员换手率", turnover_mean,
               th["max_turnover"], "warn", "<="),
    ]
    # 方向自洽性（软提示）：IC 方向与分位单调方向相反 → 提示复核
    monotonic_up = bool(quantiles.get("monotonic_up"))
    monotonic_down = bool(quantiles.get("monotonic_down"))
    rank_ic_mean = ic.get("mean")
    direction_note = ""
    if rank_ic_mean is not None and (monotonic_up or monotonic_down):
        if (rank_ic_mean > 0 and monotonic_down) or (rank_ic_mean < 0 and monotonic_up):
            direction_note = "IC 方向与分位单调方向相反（疑似方向/换仓口径问题）"
            checks.append({"name": "direction_consistency", "metric": "方向一致性",
                           "value": None, "threshold": None, "severity": "warn",
                           "status": "warn", "detail": direction_note})
    if not monotonic_up and not monotonic_down:
        checks.append({"name": "quantile_monotonic", "metric": "分位单调性",
                       "value": None, "threshold": None, "severity": "warn",
                       "status": "warn",
                       "detail": "分位收益未单调（分位均值不严格递增/递减）"})

    failures = [c["name"] for c in checks if c["status"] == "fail"]
    warns = [c["name"] for c in checks if c["status"] == "warn"]
    status = "fail" if failures else ("warn" if warns else "pass")

    metrics = {
        "rows_in": rows_in,
        "rows_used": int(samples.get("rows_used") or 0),
        "bad_rows": bad_rows,
        "bad_ratio": round(bad_ratio, 6),
        "n_days_ic": ic.get("n_days"),
        "coverage_names_mean": names_mean,
        "coverage_names_min": coverage.get("names_min"),
        "rank_ic_mean": ic.get("mean"),
        "rank_ic_std": ic.get("std"),
        "rank_icir": icir,
        "rank_ic_positive_rate": ic.get("positive_rate"),
        "pearson_ic_mean": ((diag.get("ic") or {}).get("pearson") or {}).get("mean"),
        "quantile_returns": quantiles.get("returns"),
        "quantile_spearman": q_spear,
        "quantile_monotonic_up": monotonic_up,
        "quantile_monotonic_down": monotonic_down,
        "long_short_mean": ls.get("mean"),
        "long_short_ir": ls_ir,
        "long_short_positive_rate": ls.get("positive_rate"),
        "turnover_mean": turnover_mean,
        "turnover_median": turnover.get("median"),
        "turnover_pairs": turnover.get("n_pairs"),
    }
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": status,
        "thresholds": {k: round(float(v), 6) for k, v in th.items()},
        "checks": checks,
        "failures": failures,
        "warnings": warns,
        "metrics": metrics,
        "diagnostics": diag,      # 完整诊断报告（可追溯；含 warnings/bad_rows）
        "notes": NOTES,
    }


# ---------------------------------------------------------------- CLI
def _print_report(report: dict) -> None:
    m = report["metrics"]
    print("== 因子诊断 preflight（只读建议）==")
    print("status=%s  rows=%s/%s bad=%s days=%s coverage=%s"
          % (report["status"], m["rows_used"], m["rows_in"], m["bad_rows"],
             m["n_days_ic"], m["coverage_names_mean"]))
    print("rankIC mean=%s ICIR=%s 正比例=%s | 分位单调=%s qSpearman=%s"
          % (m["rank_ic_mean"], m["rank_icir"], m["rank_ic_positive_rate"],
             "up" if m["quantile_monotonic_up"] else (
                 "down" if m["quantile_monotonic_down"] else "no"),
             m["quantile_spearman"]))
    print("多空差 mean=%s IR=%s | 换手 mean=%s pairs=%s"
          % (m["long_short_mean"], m["long_short_ir"], m["turnover_mean"],
             m["turnover_pairs"]))
    print("-- checks --")
    for c in report["checks"]:
        print("[%s] %-22s value=%s threshold=%s %s"
              % (c["status"].upper(), c["name"], c.get("value"),
                 c.get("threshold"), c.get("detail") or ""))
    if report["failures"]:
        print("[FAIL] %s" % ",".join(report["failures"]))
    if report["warnings"]:
        print("[WARN] %s" % ",".join(report["warnings"]))
    for note in report.get("notes") or []:
        print("[note] %s" % note)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="因子诊断准入 preflight（只读；输出 pass/warn/fail 研究建议）")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="面板 CSV（date,symbol,factor,forward_return）")
    src.add_argument("--panel-json", help="面板 JSON（记录数组或 {rows:[...]}）")
    ap.add_argument("--thresholds", default="", help="阈值 JSON 文件（覆盖默认值）")
    ap.add_argument("--json", action="store_true", help="打印 JSON 报告")
    ap.add_argument("--out", default="", help="报告 JSON 落盘路径")
    for key, flag in CLI_FLAGS.items():
        ap.add_argument(flag, type=float, default=None,
                        help="覆盖阈值 %s（默认 %s）" % (key, DEFAULT_THRESHOLDS[key]))
    args = ap.parse_args(argv)
    try:
        df = load_panel(args.csv or args.panel_json)
        th, th_warnings = load_thresholds(args.thresholds or None)
        th = merge_cli_thresholds(th, args)
        report = evaluate(df, thresholds=th)
        if th_warnings:
            report["warnings"] = list(report["warnings"]) + th_warnings
            report["threshold_warnings"] = th_warnings
    except FactorGateError as exc:
        print("[ERROR] %s" % exc, file=sys.stderr)
        return 3
    if args.out:
        out = Path(args.out)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            if not args.json:
                print("[report] -> %s" % out)
        except OSError as exc:
            print("[ERROR] 报告落盘失败：%s" % str(exc)[:200], file=sys.stderr)
            return 3
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        _print_report(report)
    return {"pass": 0, "warn": 1, "fail": 2}.get(report["status"], 2)


if __name__ == "__main__":
    sys.exit(main())
