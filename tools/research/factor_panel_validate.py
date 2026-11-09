# -*- coding: utf-8 -*-
"""真实因子面板验证运行器 v1（2026-11-01 WO-VAL-FACTOR-20261101 / task-32）。

用途：在本地真实 A 股数据上构造**周频、20 交易日 forward return** 的因子面板，调用
已合入 master 的 `tools/research/factor_diagnostics.py` 与 `tools/research/factor_gate.py`，
输出 IC/ICIR/分位/多空/换手/覆盖率 + pass/warn/fail；主因子 `low_turnover`，对照 `momentum20`。

数据与口径（只读，不写数据仓）：
  * 数据源：`quant_backtest/data/tushare`（daily / adj_factor / daily_basic / stock_basic），
    本地数据仓；`--data-root` 可指定（默认自动探测主 checkout）。
  * 股票池：`factor_lib.load_panel(universe="tradable")`（T 日价格≥2 元、成交额≥5000 万、
    上市≥250 天；只用 T 日可知价格/成交额+静态上市日，无前视）。
  * 复权：`close × adj_factor`（factor_lib 的 adj_close）；收益全部用复权价。
  * PIT：信号日 T（每周最后交易日）收盘；T 日因子只用 T 及以前数据（factor_lib 保证）；
    收益 = adj_close(T+21)/adj_close(T+1) − 1（T+1 收盘入场、持有 20 个交易日，无前视）。
  * 过滤：停牌/缺价（不在 tradable 或无 T/T+1/T+21 价格）、ST（T 日 `bak_basic.name`
    含 ST/退）、涨跌停（T 日涨停/跌停不入选；T+1 涨停无法买入亦剔除；用
    pct_chg+OHLC 按板块阈值推导，2025-09+ 与 `limit_list_d` 交叉核对）。
  * 因子方向：factor_diagnostics 约定「因子值越大越看多」，故送入面板的因子 = 注册方向 ×
    原始因子（low_turnover 注册 direction=-1 → −turnover_rate；mom20 direction=+1 → 原始值）。

产出（只写研究输出）：
  * `research/reports/val_factor_20261101.json`：完整结果（面板/过滤计数/诊断/gate/局限）
  * `research/reports/val_factor_20261101.md`：人读报告（由本脚本生成）
  **不接生产选股、不改策略注册、不转 flag、不联网、不产买卖信号。**

运行（示例）：
    python tools/research/factor_panel_validate.py \
      --start 20230101 --end 20260930 --horizon 20 \
      --factors low_turnover,mom20 \
      --data-root "C:/项目/老田的AI助手/quant_backtest/data/tushare" \
      --out research/reports/val_factor_20261101.json \
      --md research/reports/val_factor_20261101.md
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]
for p in (_HERE,):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import factor_diagnostics as fd  # noqa: E402
import factor_gate as fg  # noqa: E402
import factor_lib as fl  # noqa: E402

SCHEMA = "factor_panel_validate/v1"
DEFAULT_DATA_ROOT = Path(r"C:\项目\老田的AI助手\quant_backtest\data\tushare")
DEFAULT_HORIZON = 20
DEFAULT_MIN_CROSS_SECTION = 100
# 验证用阈值（研究口径；非生产预注册阈值，报告会回显）
VALIDATION_THRESHOLDS = {
    "min_rank_ic_mean": 0.02,
    "min_icir": 0.20,
    "min_positive_rate": 0.55,
    "min_coverage": 500.0,
    "min_days": 100,
    "min_quantile_spearman": 0.50,
    "min_long_short_ir": 0.20,
    "max_turnover": 0.80,
    "max_bad_ratio": 0.20,
}
LIMIT_TOLERANCE_PCT = 0.2      # 涨跌停判定容差（百分点）


# ---------------------------------------------------------------- 数据定位
def resolve_data_root(given: str | None = None) -> Path:
    """→ tushare 数据根（含 daily/adj_factor/daily_basic/stock_basic）。"""
    candidates = []
    if given:
        candidates.append(Path(given))
    repo_local = _REPO / "quant_backtest" / "data" / "tushare"
    candidates.append(repo_local)
    candidates.append(DEFAULT_DATA_ROOT)
    for p in candidates:
        if (p / "daily").is_dir():
            return p
    raise FileNotFoundError("找不到 tushare 数据根（尝试：%s）"
                            % ", ".join(str(c) for c in candidates))


def _curated_root(data_root: Path) -> Path:
    """辅助表（bak_basic/limit_list_d）在 data_root 同级的 tushare_curated。"""
    return data_root.parent / "tushare_curated"


# ---------------------------------------------------------------- 周频信号日
def weekly_signal_dates(panel_dates, start: str, end: str) -> list[str]:
    """信号日 = [start,end] 内每个自然周（W-FRI）最后一个交易日的 YYYYMMDD。"""
    dates = [str(d) for d in panel_dates]
    s, e = str(start), str(end)
    keep = [d for d in dates if s <= d <= e]
    if not keep:
        return []
    dt = pd.to_datetime(pd.Index(keep), format="%Y%m%d")
    frame = pd.DataFrame({"date": keep, "period": dt.to_period("W-FRI")})
    out = frame.groupby("period")["date"].max().sort_values().tolist()
    return [str(x) for x in out]


# ---------------------------------------------------------------- ST / 涨跌停
def load_st_matrix(panel, signal_dates: list[str], data_root: Path) -> pd.DataFrame:
    """T 日是否 ST/退（bak_basic 历史名称，PIT；缺表则返回全 False 并告警）。"""
    codes = panel.codes
    idx = [d for d in panel.dates if d in set(signal_dates)]
    matrix = pd.DataFrame(False, index=idx, columns=codes)
    path = _curated_root(data_root) / "bak_basic.parquet"
    if not path.is_file():
        return matrix
    need = set(idx)
    names = pd.read_parquet(path, columns=["trade_date", "ts_code", "name"])
    names["trade_date"] = names["trade_date"].astype(str)
    names["ts_code"] = names["ts_code"].astype(str)
    names = names[names["trade_date"].isin(need)]
    names = names.drop_duplicates(["trade_date", "ts_code"], keep="last")
    bad = names["name"].astype("string").str.contains("ST|退", na=False)
    names = names[bad]
    for trade_date, group in names.groupby("trade_date"):
        if trade_date not in matrix.index:
            continue
        cols = [c for c in group["ts_code"] if c in matrix.columns]
        if cols:
            matrix.loc[trade_date, cols] = True
    return matrix


def _board_limit_pct(code: str, is_st: bool) -> float:
    """涨跌停幅度（小数）。板块规则：主板 10%（ST 5%）、创业板/科创板 20%（ST 仍 20%）、
    北交所 30%。"""
    if code.endswith(".BJ"):
        return 0.30
    if code.split(".")[0][:3] in ("688", "689", "300", "301"):
        return 0.20
    return 0.05 if is_st else 0.10


def derive_limit_matrices(panel, st_matrix: pd.DataFrame) -> tuple:
    """由 pct_chg/OHLC 推导涨跌停矩阵（True=当日收盘处于涨停/跌停）。

    幅度规则：北交所 30%；创业板/科创板 20%（含 ST）；主板 10%（ST 5%）。
    判定：收盘=当日最高且 pct_chg ≥ 幅度−0.2pp → 涨停；对称 → 跌停。
    """
    codes = list(panel.codes)
    base = np.array([_board_limit_pct(c, False) for c in codes], dtype=float)
    st = st_matrix.reindex(index=panel.dates, columns=panel.codes).fillna(False)
    st_np = st.to_numpy(dtype=bool)
    thr = np.where(st_np & (base[None, :] == 0.10), 0.05, base[None, :])
    thr_pct = thr * 100.0 - LIMIT_TOLERANCE_PCT
    close = panel.close.to_numpy(dtype=float)
    high = panel.high.to_numpy(dtype=float)
    low = panel.low.to_numpy(dtype=float)
    pct = panel.pct_chg.to_numpy(dtype=float)
    eps = 1e-6
    with np.errstate(invalid="ignore"):
        up = (close >= high - eps) & (pct >= thr_pct)
        down = (close <= low + eps) & (pct <= -thr_pct)
    up = pd.DataFrame(up, index=panel.dates, columns=panel.codes)
    down = pd.DataFrame(down, index=panel.dates, columns=panel.codes)
    return up, down


def load_actual_limit_matrix(signal_dates: list[str], next_dates: list[str],
                             data_root: Path) -> pd.DataFrame | None:
    """实际涨跌停（limit_list_d，仅 2025-09 起有）→ 用于交叉核对，不参与主过滤。"""
    path = _curated_root(data_root) / "limit_list_d.parquet"
    need = set(signal_dates) | set(next_dates)
    if not path.is_file():
        return None
    df = pd.read_parquet(path, columns=["trade_date", "ts_code", "limit"])
    df["trade_date"] = df["trade_date"].astype(str)
    df = df[df["trade_date"].isin(need)]
    if df.empty:
        return None
    df = df.drop_duplicates(["trade_date", "ts_code"], keep="last")
    return df.set_index(["trade_date", "ts_code"])["limit"]


def cross_check_limits(limit_up: pd.DataFrame, limit_down: pd.DataFrame,
                       actual: pd.DataFrame | None) -> dict:
    """推导涨跌停 vs 实际 limit_list_d 的混淆矩阵（覆盖期 2025-09+，数据质量参考）。"""
    if actual is None or len(actual) == 0:
        return {"available": False,
                "note": "limit_list_d 本地仅 2025-09 起，无法全期核对"}
    tp = fp = fn = n = tn = 0
    for (trade_date, ts_code), label in actual.items():
        if trade_date not in limit_up.index or ts_code not in limit_up.columns:
            continue
        derived = bool(limit_up.at[trade_date, ts_code])
        n += 1
        if label == "U":
            if derived:
                tp += 1
            else:
                fn += 1
        elif derived:
            fp += 1
        else:
            tn += 1
    precision = (tp / (tp + fp)) if (tp + fp) else None
    recall = (tp / (tp + fn)) if (tp + fn) else None
    return {
        "available": True,
        "checked_rows": n,
        "actual_limit_up": tp + fn,
        "derived_limit_up": tp + fp,
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "note": ("limit_list_d 仅覆盖 2025-09 起；precision=推导涨停中被实际确认为涨停的比例，"
                 "recall=实际涨停中被推导捕获的比例；覆盖期外无法核对"),
    }


# ---------------------------------------------------------------- 面板构造
def build_factor_panel(panel, factor_name: str, signal_dates: list[str], *,
                       st_matrix: pd.DataFrame, limit_up: pd.DataFrame,
                       limit_down: pd.DataFrame, horizon: int = DEFAULT_HORIZON,
                       limit_actual: pd.DataFrame | None = None) -> tuple:
    """按周频信号日构造 [date,symbol,factor,forward_return] + 过滤计数。

    PIT：T 日因子（factor_lib 保证只用过去数据）；收益 adj(T+1+h)/adj(T+1)-1。
    向量化按周截面过滤（5k+ 股票 × 190 周）。
    """
    spec = fl.FACTORS[factor_name]
    factor = spec.compute(panel) * float(spec.direction)   # 方向归一：越大越看多
    adj = panel.adj_close
    fwd = adj.shift(-(1 + int(horizon))) / adj.shift(-1) - 1.0
    eligible = panel.eligible_mask()
    st_all = st_matrix.reindex(index=panel.dates, columns=panel.codes).fillna(False)
    dates = list(panel.dates)
    pos = {d: i for i, d in enumerate(dates)}
    code_arr = np.array([str(c) for c in panel.codes], dtype=object)
    parts = []
    stats = {"candidates": 0, "skipped_untradable": 0, "skipped_st": 0,
             "skipped_limit_signal": 0, "skipped_limit_entry": 0,
             "skipped_missing_data": 0, "rows": 0, "weeks": 0}
    stats["limit_check"] = cross_check_limits(limit_up, limit_down, limit_actual)
    for signal_date in signal_dates:
        i = pos.get(signal_date)
        if i is None or i + 1 + int(horizon) >= len(dates):
            continue
        next_date = dates[i + 1]
        stats["weeks"] += 1
        elig = eligible.loc[signal_date].to_numpy(dtype=bool)
        st = st_all.loc[signal_date].to_numpy(dtype=bool)
        lu = limit_up.loc[signal_date].to_numpy(dtype=bool)
        ld = limit_down.loc[signal_date].to_numpy(dtype=bool)
        lu_next = limit_up.loc[next_date].to_numpy(dtype=bool)
        f = factor.loc[signal_date].to_numpy(dtype=float)
        r = fwd.loc[signal_date].to_numpy(dtype=float)
        finite = np.isfinite(f) & np.isfinite(r)
        m0 = elig
        m1 = m0 & ~st
        m2 = m1 & ~(lu | ld)
        m3 = m2 & ~lu_next
        valid = m3 & finite
        stats["candidates"] += int(len(code_arr))
        stats["skipped_untradable"] += int((~m0).sum())
        stats["skipped_st"] += int((m0 & st).sum())
        stats["skipped_limit_signal"] += int((m1 & ~st & (lu | ld)).sum())
        stats["skipped_limit_entry"] += int((m2 & lu_next).sum())
        stats["skipped_missing_data"] += int((m3 & ~finite).sum())
        if valid.any():
            parts.append(pd.DataFrame({
                "date": signal_date,
                "symbol": code_arr[valid],
                "factor": f[valid],
                "forward_return": r[valid],
            }))
            stats["rows"] += int(valid.sum())
    df = (pd.concat(parts, ignore_index=True) if parts
          else pd.DataFrame(columns=["date", "symbol", "factor", "forward_return"]))
    return df, stats


# ---------------------------------------------------------------- 诊断 + gate
def evaluate_panel(df: pd.DataFrame, thresholds: dict,
                   min_cross_section: int = DEFAULT_MIN_CROSS_SECTION) -> dict:
    """调用已合入的 factor_diagnostics + factor_gate（fail-visible）。"""
    diag = fd.compute(df, n_quantiles=5,
                      min_names_per_day=int(min_cross_section),
                      min_days=3)
    gate = fg.evaluate(df, thresholds=dict(thresholds))
    return {"diagnostics": diag, "gate": gate}


# ---------------------------------------------------------------- 主流程
def run(start: str, end: str, horizon: int, factors: list[str],
        data_root: str | None = None, min_cross_section: int = DEFAULT_MIN_CROSS_SECTION,
        thresholds: dict | None = None) -> dict:
    t0 = time.time()
    root = resolve_data_root(data_root)
    thresholds = dict(VALIDATION_THRESHOLDS if thresholds is None else thresholds)
    panel = fl.load_panel(start, end, horizon=int(horizon), universe="tradable",
                          data_root=root, load_roe=False)
    signals = weekly_signal_dates(panel.dates, start, end)
    if not signals:
        raise ValueError("区间内没有周频信号日（%s~%s）" % (start, end))
    st_matrix = load_st_matrix(panel, signals, root)
    limit_up, limit_down = derive_limit_matrices(panel, st_matrix)
    next_dates = []
    pos = {d: i for i, d in enumerate(panel.dates)}
    for s in signals:
        i = pos.get(s)
        if i is not None and i + 1 < len(panel.dates):
            next_dates.append(panel.dates[i + 1])
    limit_actual = load_actual_limit_matrix(signals, next_dates, root)
    out_factors = {}
    for name in factors:
        if name not in fl.FACTORS:
            raise ValueError("未知因子 %r（可用：%s）" % (name, ",".join(sorted(fl.FACTORS))))
        df, stats = build_factor_panel(
            panel, name, signals, st_matrix=st_matrix, limit_up=limit_up,
            limit_down=limit_down, horizon=int(horizon), limit_actual=limit_actual)
        if df.empty:
            raise ValueError("因子 %s 面板为空（过滤后无有效行）——样本/覆盖不足，fail-visible"
                             % name)
        result = evaluate_panel(df, thresholds, min_cross_section=min_cross_section)
        diag = result["diagnostics"]
        gate = result["gate"]
        out_factors[name] = {
            "spec": {"name": name, "direction": int(fl.FACTORS[name].direction),
                     "description": fl.FACTORS[name].description,
                     "factor_note": "送入 diagnostics 的因子 = direction × 原始因子（越大越看多）"},
            "panel": {"rows": int(len(df)), "weeks": int(df["date"].nunique()),
                      "symbols": int(df["symbol"].nunique()),
                      "date_min": str(df["date"].min()),
                      "date_max": str(df["date"].max()),
                      "discarded_rows": max(0, stats["candidates"] - stats["rows"]),
                      "filter_stats": stats},
            "diagnostics": {
                "coverage": diag["coverage"],
                "samples": diag["samples"],
                "ic": {"rank": diag["ic"]["rank"], "pearson": diag["ic"]["pearson"]},
                "quantiles": {k: diag["quantiles"][k] for k in
                              ("returns", "counts", "monotonic_up", "monotonic_down",
                               "quantile_spearman")},
                "long_short": {k: diag["quantiles"]["long_short"][k] for k in
                               ("mean", "std", "icir", "t_stat",
                                "positive_rate", "n_days")},
                "turnover": diag["turnover"],
                "warnings": diag["warnings"],
            },
            "gate": {"status": gate["status"], "checks": gate["checks"],
                     "failures": gate["failures"], "warnings": gate["warnings"],
                     "metrics": gate["metrics"]},
        }
        print("[%s] rows=%d weeks=%d coverage=%.1f rankIC=%.4f ICIR=%s gate=%s"
              % (name, len(df), df["date"].nunique(),
                 diag["coverage"]["names_mean"] or -1,
                 diag["ic"]["rank"]["mean"] or float("nan"),
                 diag["ic"]["rank"]["icir"], gate["status"]))
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "work_order": "WO-VAL-FACTOR-20261101",
        "data": {
            "root": str(root),
            "daily_last_file": max((f.name for f in (root / "daily").glob("d_*.parquet")),
                                   default=""),
            "window": {"start": str(start), "end": str(end), "horizon": int(horizon)},
            "frequency": "weekly (W-FRI 最后交易日)",
            "panel_shape": {"dates": len(panel.dates), "codes": len(panel.codes)},
            "universe": "factor_lib.tradable（T 日价格≥2 元、成交额≥5000 万、上市≥250 天）",
            "forward_return": "adj_close(T+1+h)/adj_close(T+1)-1（T+1 收盘入、持有 h 个交易日）",
            "weekly_signals": len(signals),
            "first_signal": signals[0], "last_signal": signals[-1],
            "limit_filter": "pct_chg/OHLC 推导（主板10%/ST5%/创业板·科创板20%/北交所30%）",
            "st_filter": "bak_basic T 日 name 含 ST/退",
            "suspension_filter": "tradable + T/T+1/T+21 复权价完整",
        },
        "thresholds": thresholds,
        "factors": out_factors,
        "limitations": [
            "复权收益用 local adj_factor（未做分红再投资单独校验）；数据 as-of 以本地仓为准。",
            "涨跌停无法用 limit_list_d 覆盖全期（本地 2025-09 起）→ 主过滤用 pct_chg/OHLC 推导，"
            "存在边界误差；T+1 涨停按推导矩阵剔除。",
            "周频信号 = 每周最后交易日；forward 尾部因 20 日持有期截断（last_signal 早于 end）。",
            "factor_diagnostics IC 为周频截面、forward 20 日窗口→相邻周重叠 4/5，ICIR/t 仅描述性，"
            "不做独立样本显著性推断（需 block bootstrap）。",
            "单因子排序诊断，不含成本/滑点/容量/换手成本；组合级净收益见既有 factor_validate/"
            "lowturn_freq_validate 报告（本单只做诊断口径）。",
            "阈值（报告已回显）为验证研究口径，非生产预注册阈值；pass 不构成上线结论。",
            "not 复权/ST/停牌口径与生产选股引擎不完全一致：本单不改生产，仅为研究验证。",
        ],
        "runtime_sec": round(time.time() - t0, 1),
        "notes": [
            "只读验证：不产买卖信号、不接生产选股、不改策略注册、不转 flag、不联网。",
            "调用 master 已合入的 tools/research/factor_diagnostics.py 与 factor_gate.py。",
        ],
    }


# ---------------------------------------------------------------- 报告渲染
def render_md(report: dict) -> str:
    d = report["data"]
    lines = [
        "# 真实因子面板验证 v1（low_turnover / momentum20）",
        "",
        "- 工单：`%s`（task-32）" % report["work_order"],
        "- 生成时间：%s" % report["generated_at"],
        "- 数据：`%s`（daily 最后文件 %s）" % (d["root"], d["daily_last_file"]),
        "- 窗口：%s ~ %s，周频，forward_return = %s 日"
        % (d["window"]["start"], d["window"]["end"], d["window"]["horizon"]),
        "- 信号日：%d 个（%s ~ %s）" % (d["weekly_signals"], d["first_signal"], d["last_signal"]),
        "- 股票池：%s" % d["universe"],
        "- 过滤：%s；%s；%s" % (d["st_filter"], d["suspension_filter"], d["limit_filter"]),
        "- 口径：调用 master 已合入 `factor_diagnostics.py` + `factor_gate.py`（研究诊断，不接生产）",
        "",
        "## 结果",
        "",
        "| 因子 | 行数 | 周数 | 覆盖均值 | rankIC 均值 | ICIR | IC 正比例 | 多空差 | 多空 IR | 换手 | gate |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, f in report["factors"].items():
        ic = f["diagnostics"]["ic"]["rank"]
        ls = f["diagnostics"]["long_short"]
        to = f["diagnostics"]["turnover"]
        gate = f["gate"]["status"]
        lines.append("| %s | %d | %d | %.1f | %.4f | %s | %s | %.4f | %s | %s | **%s** |"
                     % (name, f["panel"]["rows"], f["panel"]["weeks"],
                        f["diagnostics"]["coverage"]["names_mean"] or 0,
                        ic["mean"] or 0, ic["icir"], ic["positive_rate"],
                        ls["mean"] or 0, ls["icir"], to["mean"], gate))
    lines += ["", "## 分位收益（Q1=因子最低 … Q5=最高）", ""]
    for name, f in report["factors"].items():
        q = f["diagnostics"]["quantiles"]
        lines.append("- **%s**：%s（单调 up=%s down=%s，qSpearman=%s）"
                     % (name, ", ".join("Q%s=%s" % (k, v) for k, v in q["returns"].items()),
                        q["monotonic_up"], q["monotonic_down"], q["quantile_spearman"]))
    lines += ["", "## gate 检查明细", ""]
    for name, f in report["factors"].items():
        lines.append("### %s → **%s**" % (name, f["gate"]["status"]))
        lines.append("")
        for c in f["gate"]["checks"]:
            lines.append("- %s `%s` value=%s threshold=%s"
                         % (c["status"].upper(), c["name"], c.get("value"),
                            c.get("threshold")))
        if f["gate"]["warnings"]:
            lines.append("- warnings: %s" % ", ".join(f["gate"]["warnings"]))
        lines.append("")
    lines += ["## 数据 / 样本", ""]
    for name, f in report["factors"].items():
        p = f["panel"]
        fs = p["filter_stats"]
        lines.append("- **%s**：rows=%d weeks=%d symbols=%d；candidates=%d，"
                     "剔除 股票池/停牌缺价=%d/ST=%d/信号日涨跌停=%d/次日涨停=%d/缺数据=%d；"
                     "limit_check=%s"
                     % (name, p["rows"], p["weeks"], p["symbols"], fs["candidates"],
                        fs["skipped_untradable"], fs["skipped_st"],
                        fs["skipped_limit_signal"], fs["skipped_limit_entry"],
                        fs["skipped_missing_data"],
                        json.dumps(fs.get("limit_check"), ensure_ascii=False)))
    lines += ["", "## 局限", ""]
    for x in report["limitations"]:
        lines.append("- %s" % x)
    lines += ["", "## 复现", "", "```powershell",
              "python tools/research/factor_panel_validate.py --start %s --end %s "
              "--horizon %d --factors %s --data-root \"%s\" "
              "--out research/reports/val_factor_20261101.json "
              "--md research/reports/val_factor_20261101.md"
              % (d["window"]["start"], d["window"]["end"], d["window"]["horizon"],
                 ",".join(report["factors"]), d["root"]),
              "```", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="真实因子面板验证（周频/20日 forward；只读）")
    ap.add_argument("--start", default="20230101")
    ap.add_argument("--end", default="20260930")
    ap.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    ap.add_argument("--factors", default="low_turnover,mom20")
    ap.add_argument("--data-root", default="")
    ap.add_argument("--min-cross-section", type=int, default=DEFAULT_MIN_CROSS_SECTION)
    ap.add_argument("--thresholds", default="", help="阈值 JSON（覆盖默认验证阈值）")
    ap.add_argument("--out", default=str(_REPO / "research" / "reports"
                                         / "val_factor_20261101.json"))
    ap.add_argument("--md", default=str(_REPO / "research" / "reports"
                                        / "val_factor_20261101.md"))
    args = ap.parse_args(argv)
    thresholds = dict(VALIDATION_THRESHOLDS)
    if args.thresholds:
        try:
            data = json.loads(Path(args.thresholds).read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("阈值 JSON 必须是对象")
            thresholds.update({k: float(v) for k, v in data.items()})
        except (OSError, ValueError) as exc:
            print("[ERROR] 阈值文件无效：%s" % str(exc)[:200], file=sys.stderr)
            return 3
    factors = [x.strip() for x in str(args.factors).split(",") if x.strip()]
    try:
        report = run(args.start, args.end, args.horizon, factors,
                     data_root=args.data_root or None,
                     min_cross_section=args.min_cross_section,
                     thresholds=thresholds)
    except (ValueError, FileNotFoundError) as exc:
        print("[ERROR] %s" % exc, file=sys.stderr)
        return 3
    for path, content in ((args.out, json.dumps(report, ensure_ascii=False, indent=1)),
                          (args.md, render_md(report))):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        print("[report] -> %s" % p)
    for name, f in report["factors"].items():
        print("[gate] %s -> %s" % (name, f["gate"]["status"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
