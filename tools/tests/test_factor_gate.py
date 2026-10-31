# -*- coding: utf-8 -*-
"""因子诊断准入 preflight 契约测试（WO-STRAT-FGATE-20261101 / task-27）。

全离线：合成面板 + tmp CSV/JSON，不读真实数据仓、不联网、不写生产。
覆盖：
  · 正样本 pass；负样本 fail（IC 方向）；
  · 样本不足 fail（天数/覆盖率）、ICIR 不可用 fail-visible；
  · 坏行比例 fail / 低坏行 pass，且坏行数量可见；
  · 缺列/空面板/坏文件 → 数据错误（CLI exit 3，stderr 说明）；
  · 软阈值触发 warn（exit 1）；阈值 JSON/CLI 覆盖与未知键告警；
  · CSV/JSON 两种面板输入；报告不自动晋升（notes 边界）。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
MOD_PATH = REPO / "tools" / "research" / "factor_gate.py"


def _load():
    spec = importlib.util.spec_from_file_location("factor_gate_under_test", MOD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fg = _load()


# ---------------------------------------------------------------- 合成面板
def _panel(direction=1.0, n_days=5, n_names=30, noise=0.0012, seed=7):
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(n_days):
        date = "2026-10-%02d" % (20 + d)
        factor = np.arange(n_names, dtype=float)
        fwd = direction * factor * 0.001 + rng.normal(0.0, noise, n_names)
        for i in range(n_names):
            rows.append({"date": date, "symbol": "S%03d" % i,
                         "factor": float(factor[i]),
                         "forward_return": float(fwd[i])})
    return pd.DataFrame(rows)


def _write_csv(tmp_path, df, name="panel.csv"):
    p = tmp_path / name
    df.to_csv(p, index=False)
    return p


# ---------------------------------------------------------------- 正/负样本
def test_positive_panel_pass():
    rep = fg.evaluate(_panel(direction=1.0))
    assert rep["schema"] == "factor_gate/v1"
    assert rep["status"] == "pass", rep["checks"]
    m = rep["metrics"]
    assert m["rank_ic_mean"] > 0.02
    assert m["rank_icir"] is not None and m["rank_icir"] > 0.2
    assert m["coverage_names_mean"] == 30.0
    assert m["n_days_ic"] == 5
    assert m["quantile_returns"] and len(m["quantile_returns"]) == 5
    assert m["bad_rows"] == 0 and m["bad_ratio"] == 0.0
    assert rep["failures"] == []
    assert rep["diagnostics"]["schema"] == "factor_diagnostics/v1"
    joined = " ".join(rep["notes"])
    assert "不自动晋升" in joined and "只读" in joined


def test_negative_panel_fail():
    rep = fg.evaluate(_panel(direction=-1.0))
    assert rep["status"] == "fail"
    names = [c["name"] for c in rep["checks"] if c["status"] == "fail"]
    assert "rank_ic_mean" in names
    assert rep["metrics"]["rank_ic_mean"] < 0
    # 负 IC 仍会正常输出诊断，不因“方向不合意”报数据错误
    assert rep["diagnostics"]["ic"]["rank"]["mean"] < 0


# ---------------------------------------------------------------- 样本不足
def test_insufficient_sample_fail():
    rep = fg.evaluate(_panel(direction=1.0, n_days=2, n_names=10))
    assert rep["status"] == "fail"
    fails = [c["name"] for c in rep["checks"] if c["status"] == "fail"]
    assert "coverage" in fails and "min_days" in fails
    assert rep["metrics"]["coverage_names_mean"] == 10.0
    assert rep["metrics"]["n_days_ic"] == 2


def test_icir_unavailable_fail_visible():
    """完美排序（日间 IC 全部=1 → std=0）→ ICIR 不可用，硬闸必须 fail 而非 pass。"""
    df = _panel(direction=1.0, noise=0.0)
    rep = fg.evaluate(df)
    assert rep["status"] == "fail"
    assert "icir" in [c["name"] for c in rep["checks"] if c["status"] == "fail"]
    icir_check = next(c for c in rep["checks"] if c["name"] == "icir")
    assert icir_check["value"] is None and "不可用" in icir_check["detail"]


# ---------------------------------------------------------------- 坏行 fail-visible
def _with_bad_rows(good, n_bad):
    bad = []
    for i in range(n_bad):
        bad.append({"date": "not-a-date", "symbol": "BAD%d" % i,
                    "factor": "x", "forward_return": None})
    return pd.concat([good, pd.DataFrame(bad)], ignore_index=True)


def test_high_bad_ratio_fail_and_visible():
    df = _with_bad_rows(_panel(), n_bad=60)   # 60/(150+60)≈28.6% > 20%
    rep = fg.evaluate(df)
    assert rep["status"] == "fail"
    bad_check = next(c for c in rep["checks"] if c["name"] == "bad_rows")
    assert bad_check["status"] == "fail"
    assert rep["metrics"]["bad_rows"] == 60
    assert rep["metrics"]["bad_ratio"] > 0.2
    assert rep["diagnostics"]["bad_rows"]        # 样例 fail-visible


def test_low_bad_ratio_pass_with_counts():
    df = _with_bad_rows(_panel(), n_bad=5)    # 5/155≈3.2% < 20%
    rep = fg.evaluate(df)
    assert rep["metrics"]["bad_rows"] == 5
    assert next(c for c in rep["checks"] if c["name"] == "bad_rows")["status"] == "pass"
    assert rep["status"] == "pass", rep["checks"]


# ---------------------------------------------------------------- 软阈值 warn
def test_soft_threshold_triggers_warn():
    rep = fg.evaluate(_panel(), thresholds={"min_positive_rate": 1.1})
    assert rep["status"] == "warn"
    assert rep["failures"] == []
    assert "positive_rate" in rep["warnings"]


def test_custom_threshold_can_fail_positive_panel():
    rep = fg.evaluate(_panel(), thresholds={"min_rank_ic_mean": 0.99,
                                            "min_icir": 1e9})
    assert rep["status"] == "fail"


# ---------------------------------------------------------------- 缺列/坏输入
def test_missing_columns_raise():
    for col in ("date", "symbol", "factor", "forward_return"):
        with pytest.raises(fg.FactorGateError, match=col):
            fg.evaluate(_panel().drop(columns=[col]))


def test_empty_panel_raise():
    with pytest.raises(fg.FactorGateError):
        fg.evaluate(pd.DataFrame(columns=["date", "symbol", "factor",
                                          "forward_return"]))


def test_load_panel_json_and_unsupported_ext(tmp_path):
    df = _panel(n_days=2).head(20)
    pj = tmp_path / "panel.json"
    pj.write_text(json.dumps(df.to_dict("records"), ensure_ascii=False),
                  encoding="utf-8")
    got = fg.load_panel(pj)
    assert len(got) == 20
    txt = tmp_path / "panel.xlsx"
    txt.write_text("x", encoding="utf-8")
    with pytest.raises(fg.FactorGateError, match="不支持的扩展名"):
        fg.load_panel(txt)
    with pytest.raises(fg.FactorGateError, match="不存在"):
        fg.load_panel(tmp_path / "nope.csv")


# ---------------------------------------------------------------- 阈值加载
def test_load_thresholds_merge_and_unknown_key(tmp_path):
    p = tmp_path / "th.json"
    p.write_text(json.dumps({"min_rank_ic_mean": 0.05, "unknown_key": 1}),
                 encoding="utf-8")
    th, warnings = fg.load_thresholds(p)
    assert th["min_rank_ic_mean"] == 0.05
    assert th["min_icir"] == fg.DEFAULT_THRESHOLDS["min_icir"]
    assert any("unknown_key" in w for w in warnings)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"min_rank_ic_mean": "abc"}), encoding="utf-8")
    with pytest.raises(fg.FactorGateError):
        fg.load_thresholds(bad)


# ---------------------------------------------------------------- CLI
def test_cli_csv_pass_json_and_out(tmp_path, capsys):
    csv = _write_csv(tmp_path, _panel())
    out = tmp_path / "gate_report.json"
    rc = fg.main(["--csv", str(csv), "--json", "--out", str(out)])
    captured = capsys.readouterr().out
    report = json.loads(captured[captured.index("{"):])
    assert rc == 0
    assert report["status"] == "pass"
    assert out.is_file()
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["status"] == "pass"


def test_cli_negative_fail_exit_2(tmp_path, capsys):
    csv = _write_csv(tmp_path, _panel(direction=-1.0))
    rc = fg.main(["--csv", str(csv), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert rc == 2 and report["status"] == "fail"


def test_cli_soft_warn_exit_1(tmp_path, capsys):
    csv = _write_csv(tmp_path, _panel())
    rc = fg.main(["--csv", str(csv), "--min-positive-rate", "1.1", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert rc == 1 and report["status"] == "warn"


def test_cli_json_panel_input(tmp_path, capsys):
    df = _panel(n_days=2)
    pj = tmp_path / "panel.json"
    pj.write_text(json.dumps(df.to_dict("records"), ensure_ascii=False),
                  encoding="utf-8")
    rc = fg.main(["--panel-json", str(pj), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert rc == 2 or rc == 0         # 2 天样本 → min_days fail；有输出即可
    assert report["schema"] == "factor_gate/v1"


def test_cli_thresholds_file_and_unknown_warning(tmp_path, capsys):
    csv = _write_csv(tmp_path, _panel())
    th = tmp_path / "th.json"
    th.write_text(json.dumps({"min_positive_rate": 1.1, "nonsense": 1}),
                  encoding="utf-8")
    rc = fg.main(["--csv", str(csv), "--thresholds", str(th), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert rc == 1 and report["status"] == "warn"
    assert report.get("threshold_warnings")


def test_cli_missing_column_exit_3(tmp_path, capsys):
    csv = _write_csv(tmp_path, _panel().drop(columns=["forward_return"]))
    rc = fg.main(["--csv", str(csv)])
    err = capsys.readouterr().err
    assert rc == 3 and "缺列" in err


def test_cli_bad_file_exit_3(tmp_path, capsys):
    rc = fg.main(["--csv", str(tmp_path / "nope.csv")])
    assert rc == 3 and "不存在" in capsys.readouterr().err


def test_cli_does_not_write_without_out(tmp_path, capsys):
    csv = _write_csv(tmp_path, _panel())
    before = {p.name for p in tmp_path.iterdir()}
    rc = fg.main(["--csv", str(csv), "--json"])
    assert rc == 0
    after = {p.name for p in tmp_path.iterdir()}
    assert after == before            # 只读：无 --out 不落任何文件
