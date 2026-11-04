# -*- coding: utf-8 -*-
"""tools/tests/test_factor_lib.py · 因子库（tools/research/factor_lib.py）单元测试。

覆盖（任务验收要求）：
  1. 因子注册齐全（≥15 个，点名因子一个不少，元信息完整）；
  2. 全因子在合成面板上可计算，输出形状/取值域正确；
  3. **PIT/前视检查**：golden_pillar 信号索引 = 确认完成日（基柱 T-3），且破坏任一确认条件即无信号；
  4. **静态无未来数据泄漏**：模块内所有负 lag（shift(-n)）只允许出现在 forward_car（未来收益计算）里，
     因子计算函数源码中不得出现负 lag；
  5. **动态 PIT 截断不变性**：把面板截断到 T 后重算，T 日因子值与全样本重算完全一致；
  6. eval 输出 JSON schema 完整、可序列化、无 NaN、方向口径自洽；
  7. 同参数重跑结果可复现（bootstrap 固定 seed）；
  8. 空面板 / 全 NaN 因子容错；
  9. CLI `list` 可跑通；
 10. 逐日横截面 Spearman IC 与 pandas 参考实现一致（口径校验）。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_factor_lib.py
"""
from __future__ import annotations

import ast
import inspect
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))

import factor_lib  # noqa: E402
from factor_lib import FACTORS, Panel, evaluate_panel  # noqa: E402

FACTOR_LIB = ROOT / "tools" / "research" / "factor_lib.py"

# 任务验收点名的 15 个因子（另外 2 个通用因子为增强项）
REQUIRED_FACTORS = [
    "vol_ratio5", "double_vol", "shrink_vol", "up_vol_down", "yin_yang",
    "near_high_vol", "golden_pillar", "zero_vol", "aokou",
    "mom20", "rev5", "low_turnover", "pe", "roe", "quality_trap",
]

BASE_DAY = 37          # 合成黄金柱基柱所在行号（确认日 = BASE_DAY + 3 = 40）


# ------------------------------------------------------------------ 合成面板
def make_panel(n_dates: int = 90, n_codes: int = 60, seed: int = 7) -> Panel:
    """构造 90 交易日 × 60 只的随机面板（复权因子恒为 1，字段齐全，便于逐因子单测）。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2026-01-05", periods=n_dates).strftime("%Y%m%d")
    codes = [f"{i:06d}.SZ" for i in range(1, n_codes + 1)]
    ret = rng.normal(0.0005, 0.02, size=(n_dates, n_codes))
    close = 10.0 * np.exp(np.cumsum(np.log1p(ret), axis=0))
    pre_close = np.vstack([close[0:1], close[:-1]])
    pct_chg = (close / pre_close - 1.0) * 100.0
    open_ = pre_close * (1.0 + rng.normal(0.0, 0.005, close.shape))
    high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0.0, 0.005, close.shape)))
    low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0.0, 0.005, close.shape)))
    vol = rng.lognormal(11.0, 0.5, size=close.shape)
    amount = vol * close * 0.1    # 千元（vol 手 × 100 股 × 价格 / 1000）
    pe = rng.uniform(8.0, 80.0, size=close.shape)
    pe[rng.random(close.shape) < 0.05] = np.nan
    pe[rng.random(close.shape) < 0.05] = -5.0   # 含负 PE（pe 因子应剔除）
    turnover = rng.uniform(0.2, 8.0, size=close.shape)
    roe = rng.normal(8.0, 4.0, size=close.shape)
    total_mv = close * rng.uniform(1e7, 1e9, size=close.shape)
    industry = ["银行", "地产", "科技", "医药", "能源"]

    def frame(arr: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(arr, index=dates, columns=codes)

    fields = {
        "open": frame(open_), "high": frame(high), "low": frame(low),
        "close": frame(close), "pre_close": frame(pre_close), "pct_chg": frame(pct_chg),
        "vol": frame(vol), "amount": frame(amount), "adj_close": frame(close),
        "pe_ttm": frame(pe), "turnover_rate": frame(turnover), "roe": frame(roe),
        "total_mv": frame(total_mv),
    }
    meta = {"industry": pd.Series([industry[i % len(industry)] for i in range(n_codes)],
                                  index=codes)}
    return Panel(fields, meta=meta)


def craft_golden_pillar(panel: Panel, break_close_day: int | None = None,
                        no_shrink_day: int | None = None) -> Panel:
    """深拷贝面板并改写第 1 只股票：基柱(BASE_DAY) + 三日确认，确认日 = BASE_DAY+3。

    break_close_day：把该日收盘压到 9.8（破实顶）→ 不应产生信号；
    no_shrink_day：把该日量放大到 300（不再缩量）→ 不应产生信号。
    """
    fields = {k: v.copy() for k, v in panel.fields.items()}
    code = panel.codes[0]
    n = panel.shape[0]
    close = np.full(n, 10.0)
    vol = np.full(n, 100.0)
    pct = np.zeros(n)
    open_ = np.full(n, 10.0)
    close[BASE_DAY] = 10.3
    pct[BASE_DAY] = 3.0
    vol[BASE_DAY] = 300.0
    open_[BASE_DAY] = 10.0
    for k, v in enumerate((150.0, 120.0, 130.0), start=1):
        close[BASE_DAY + k] = 10.3
        vol[BASE_DAY + k] = v
        open_[BASE_DAY + k] = 10.3
    if break_close_day is not None:
        close[break_close_day] = 9.8
    if no_shrink_day is not None:
        vol[no_shrink_day] = 300.0
    pre_close = np.concatenate([[10.0], close[:-1]])
    updates = {
        "close": close, "adj_close": close, "pre_close": pre_close, "pct_chg": pct,
        "vol": vol, "open": open_, "high": np.maximum(close, open_),
        "low": np.minimum(close, open_), "amount": vol * close * 0.1,
    }
    for name, arr in updates.items():
        fields[name][code] = arr
    return Panel(fields, eligible=panel.eligible)


# ------------------------------------------------------------------ 1. 注册表
def test_registry_complete():
    assert len(FACTORS) >= 15
    assert set(REQUIRED_FACTORS) <= set(FACTORS)
    for name, spec in FACTORS.items():
        assert spec.name == name
        assert callable(spec.compute)
        assert spec.kind in ("event", "continuous")
        assert spec.direction in (1, -1)
        assert spec.family in ("量学形态", "通用", "Tushare-动量", "Tushare-流动性",
                               "Tushare-风险", "Tushare-反转", "Tushare-规模",
                               "Tushare-Alpha101")
        assert isinstance(spec.description, str) and spec.description.strip()
        assert isinstance(spec.requires, tuple)
    rows = factor_lib.factor_rows()
    assert len(rows) == len(FACTORS)
    assert {r["name"] for r in rows} == set(FACTORS)
    for row in rows:
        for key in ("name", "family", "kind", "direction", "description"):
            assert key in row


# ------------------------------------------------------------------ 2. 全因子可计算
def test_all_factors_compute_on_synthetic_panel():
    panel = make_panel()
    for name, spec in FACTORS.items():
        out = spec.compute(panel)
        assert isinstance(out, pd.DataFrame), name
        assert out.shape == panel.shape, name
        assert list(out.index) == list(panel.dates), name
        assert list(out.columns) == list(panel.codes), name
        vals = out.to_numpy(dtype=float)
        assert not np.isinf(vals).any(), f"{name} 出现 inf"
        # Tushare factor_list 批次需要 daily_basic/index_daily 等扩展字段，
        # 合成面板（90 日、仅 OHLCV）不满足其数据依赖或 warmup 长度，只校验形状/无 inf。
        if spec.source != "tushare.factor_list":
            assert np.isfinite(vals).mean() > 0.5, f"{name} 有效值占比过低"
        if spec.kind == "event":
            finite = vals[np.isfinite(vals)]
            assert set(np.unique(finite)).issubset({0.0, 1.0}), f"{name} 事件因子取值应为 0/1"


# ------------------------------------------------------------------ 3. 黄金柱确认日 PIT
def test_golden_pillar_signal_on_confirmation_day():
    panel = craft_golden_pillar(make_panel())
    code = panel.codes[0]
    out = FACTORS["golden_pillar"].compute(panel)
    row = out[code].to_numpy(dtype=float)
    # 基柱前 3 日无完整确认窗口 → NaN（PIT 不可算）；之后无信号
    assert np.isnan(row[:3]).all()
    # 基柱日 / 确认第 1、2 日都不是信号（信号必须落在确认完成日）
    assert row[BASE_DAY] == 0.0
    assert row[BASE_DAY + 1] == 0.0
    assert row[BASE_DAY + 2] == 0.0
    # 第 3 日（确认完成日）才是信号，且该股全样本仅此一个信号
    assert row[BASE_DAY + 3] == 1.0
    signal_idx = np.flatnonzero(np.nan_to_num(row, nan=0.0) == 1.0)
    assert signal_idx.tolist() == [BASE_DAY + 3]
    assert (BASE_DAY + 3) - BASE_DAY == 3   # 信号日比基柱晚 3 日，不存在「按形态日索引」前视

    # 破坏「三日不破实顶」→ 无信号
    broken = craft_golden_pillar(make_panel(), break_close_day=BASE_DAY + 2)
    row_broken = FACTORS["golden_pillar"].compute(broken)[code].to_numpy(dtype=float)
    assert np.nan_to_num(row_broken, nan=0.0).sum() == 0.0

    # 破坏「逐日缩量」→ 无信号
    no_shrink = craft_golden_pillar(make_panel(), no_shrink_day=BASE_DAY + 3)
    row_noshrink = FACTORS["golden_pillar"].compute(no_shrink)[code].to_numpy(dtype=float)
    assert np.nan_to_num(row_noshrink, nan=0.0).sum() == 0.0


# ------------------------------------------------------------------ 4. 静态无前视检查
def test_no_future_leakage_static():
    """源码静态检查：负 lag（shift(-n)）只允许出现在 forward_car。"""
    tree = ast.parse(FACTOR_LIB.read_text(encoding="utf-8"))
    funcs_with_negative_shift: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "shift" and sub.args):
                try:
                    value = ast.literal_eval(sub.args[0])
                except (ValueError, SyntaxError):
                    continue
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0:
                    funcs_with_negative_shift.add(node.name)
    assert funcs_with_negative_shift == {"forward_car"}, (
        f"负 lag 出现在非 forward_car 函数: {sorted(funcs_with_negative_shift)}")
    # 因子计算函数源码内不得出现负 lag（去除空白后检查 "shift(-"）
    for name, spec in FACTORS.items():
        src = inspect.getsource(spec.compute).replace(" ", "")
        assert "shift(-" not in src, f"因子 {name} 源码含负 lag（疑似前视）"
        assert "shift(int(-" not in src, f"因子 {name} 源码含负 lag（疑似前视）"


# ------------------------------------------------------------------ 5. 动态 PIT 截断不变性
def test_pit_truncation_invariance():
    """把面板截断到 T 后重算，T 日因子值必须与全样本重算一致（无未来信息依赖）。"""
    panel = craft_golden_pillar(make_panel())
    cut = str(panel.dates[BASE_DAY + 3])
    truncated = panel.subset(max_date=cut)
    assert len(truncated.dates) == BASE_DAY + 4
    for name, spec in FACTORS.items():
        full = spec.compute(panel)
        part = spec.compute(truncated)
        a = full.loc[cut].to_numpy(dtype=float)
        b = part.iloc[-1].to_numpy(dtype=float)
        np.testing.assert_allclose(a, b, rtol=1e-10, atol=1e-12, equal_nan=True,
                                   err_msg=f"因子 {name} 在 {cut} 日存在未来数据依赖（前视）")
    # 确认日信号在截断面板上同样成立
    assert FACTORS["golden_pillar"].compute(truncated)[panel.codes[0]].iloc[-1] == 1.0


# ------------------------------------------------------------------ 6. eval schema
def test_eval_output_schema():
    panel = make_panel()
    names = ["mom20", "rev5", "double_vol", "pe", "roe", "quality_trap", "golden_pillar"]
    report = evaluate_panel(panel, names, start=str(panel.dates[0]), end=str(panel.dates[-1]),
                            is_end=str(panel.dates[45]), min_cross_section=10, n_boot=50)
    assert set(report) == {"meta", "factors"}
    meta = report["meta"]
    for key in ("lib", "version", "generated_at", "requested", "effective", "is_end",
                "is_end_source", "horizon", "cost", "market", "seed", "baseline",
                "notes", "runtime_sec", "factors_evaluated"):
        assert key in meta, key
    assert meta["horizon"] == 20
    assert meta["cost"] == pytest.approx(0.003)
    assert meta["is_end"] == str(panel.dates[45])
    assert meta["effective"]["signal_days"] > 0
    # 末端裁剪：面板 90 日 → 最后可算满 20 日收益的信号日 = 第 68 行
    assert meta["effective"]["end"] == str(panel.dates[90 - 1 - (1 + 20)])
    assert meta["baseline"]["n_obs"] > 0
    assert set(report["factors"]) == set(names)

    txt = json.dumps(report, ensure_ascii=False)      # 必须可 JSON 序列化
    assert "NaN" not in txt and "Infinity" not in txt  # 不允许 NaN/Inf 泄漏进 JSON

    for name, entry in report["factors"].items():
        for key in ("name", "family", "kind", "direction", "description", "source",
                    "n_obs", "n_days", "signal", "ic", "ic_directed", "by_year",
                    "is_oos", "bootstrap", "quintiles", "event_split"):
            assert key in entry, f"{name}.{key}"
        for key in ("n_days", "mean", "std", "ir", "t", "positive_rate"):
            assert key in entry["ic"]
        for key in ("split_date", "source", "IS", "OOS"):
            assert key in entry["is_oos"]
        for key in ("ic_directed", "long_short_spread"):
            assert key in entry["bootstrap"]
        boot_ic = entry["bootstrap"]["ic_directed"]
        if boot_ic["p_le_0"] is not None:
            assert 0.0 <= boot_ic["p_le_0"] <= 1.0
        quant = entry["quintiles"]
        assert quant["n_groups"] == 5
        assert set(quant["groups"]) == {"Q1", "Q2", "Q3", "Q4", "Q5"}
        for key in ("mean_car_pct", "ann_pct", "t", "directed_ann_pct"):
            assert key in quant["top_minus_bottom"]
        if entry["kind"] == "event":
            # 0/1 事件因子：Q5=信号组、Q1=非信号组（二值分组，避免同值并列引入代码顺序偏差）
            assert quant["method"] == "binary_signal_vs_non_signal"
            assert quant["tie_break"] is None
            assert entry["signal"] is not None
            ev = entry["event_split"]
            assert ev is not None and "excess" in ev
            sig_m = ev["signal"]["mean_car_pct"]
            if sig_m is not None:
                assert quant["groups"]["Q5"]["mean_car_pct"] == pytest.approx(sig_m)
                assert quant["top_minus_bottom"]["mean_car_pct"] == pytest.approx(
                    ev["excess"]["mean_car_pct"])
        else:
            assert quant["method"] == "quantile"
            assert quant["tie_break"] == "ts_code_order"
            assert entry["event_split"] is None

    # 方向口径自洽：directed IC 均值 = direction × 原始 IC 均值
    for entry in report["factors"].values():
        if entry["ic"]["mean"] is not None and entry["ic_directed"]["mean"] is not None:
            assert entry["ic_directed"]["mean"] == pytest.approx(
                entry["direction"] * entry["ic"]["mean"], rel=1e-6, abs=1e-9)

    # 未注册因子应显式报错
    with pytest.raises(KeyError):
        evaluate_panel(panel, ["no_such_factor"], min_cross_section=10)


# ------------------------------------------------------------------ 7. 可复现
def test_eval_reproducible():
    panel = make_panel()
    kwargs = dict(factor_names=["mom20", "rev5", "double_vol", "golden_pillar"],
                  start=str(panel.dates[0]), end=str(panel.dates[-1]),
                  min_cross_section=10, n_boot=50, seed=123)
    r1 = evaluate_panel(panel, **kwargs)
    r2 = evaluate_panel(panel, **kwargs)
    assert json.dumps(r1["factors"], sort_keys=True, ensure_ascii=False) == \
        json.dumps(r2["factors"], sort_keys=True, ensure_ascii=False)
    # 默认自动 IS/OOS 切分：应在 meta 标注 source 且落在信号窗口内
    auto = evaluate_panel(panel, ["mom20"], min_cross_section=10, n_boot=10)
    assert auto["meta"]["is_end_source"] == "auto_midpoint"
    assert auto["meta"]["effective"]["start"] <= auto["meta"]["is_end"] <= auto["meta"]["effective"]["end"]


# ------------------------------------------------------------------ 8. 空数据容错
def test_empty_data_tolerance():
    empty = Panel.empty()
    assert empty.shape == (0, 0)
    for name, spec in FACTORS.items():
        out = spec.compute(empty)
        assert isinstance(out, pd.DataFrame), name
        assert out.shape == empty.shape, name
    report = evaluate_panel(empty, ["mom20", "golden_pillar"], n_boot=10)
    assert report["meta"]["effective"]["signal_days"] == 0
    assert report["meta"]["baseline"]["n_obs"] == 0
    for entry in report["factors"].values():
        assert entry["n_days"] == 0
        assert entry["ic"]["mean"] is None
        assert entry["quintiles"]["top_minus_bottom"]["ann_pct"] is None
    json.dumps(report, ensure_ascii=False)   # 空结果同样可序列化

    # 全 NaN 因子（如 pe_ttm 缺失）→ 不崩溃，n_days=0
    panel = make_panel()
    panel.fields["pe_ttm"] = pd.DataFrame(np.nan, index=panel.dates, columns=panel.codes)
    report2 = evaluate_panel(panel, ["pe"], min_cross_section=10, n_boot=20)
    assert report2["factors"]["pe"]["n_days"] == 0
    assert report2["factors"]["pe"]["quintiles"]["n_obs"] == 0


# ------------------------------------------------------------------ 9. CLI
def test_cli_list():
    proc = subprocess.run([sys.executable, "-X", "utf8", str(FACTOR_LIB), "list", "--json"],
                          capture_output=True, text=True, encoding="utf-8",
                          cwd=str(ROOT), timeout=120)
    assert proc.returncode == 0, proc.stderr
    rows = json.loads(proc.stdout)
    assert isinstance(rows, list)
    assert len(rows) == len(FACTORS)
    assert set(REQUIRED_FACTORS) <= {row["name"] for row in rows}

    proc2 = subprocess.run([sys.executable, "-X", "utf8", str(FACTOR_LIB), "list"],
                           capture_output=True, text=True, encoding="utf-8",
                           cwd=str(ROOT), timeout=120)
    assert proc2.returncode == 0, proc2.stderr
    assert "golden_pillar" in proc2.stdout
    assert "因子库" in proc2.stdout


# ------------------------------------------------------------------ 10. IC 数学口径
def test_spearman_ic_matches_pandas():
    rng = np.random.default_rng(11)
    n_dates, n_codes = 12, 80
    x = rng.normal(size=(n_dates, n_codes))
    y = 0.3 * x + rng.normal(size=(n_dates, n_codes))
    x[rng.random(x.shape) < 0.1] = np.nan
    ic = factor_lib._spearman_ic(x, y, min_n=20)
    for i in range(n_dates):
        both = np.isfinite(x[i]) & np.isfinite(y[i])
        expected = pd.Series(x[i][both]).corr(pd.Series(y[i][both]), method="spearman")
        np.testing.assert_allclose(ic[i], expected, rtol=1e-10, atol=1e-12,
                                   err_msg=f"第 {i} 日 IC 与 pandas 参考实现不一致")
