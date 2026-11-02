# -*- coding: utf-8 -*-
"""tools/tests/test_datawarehouse_quality.py — W3-1 数据仓质量遗留修复（task-146）。

覆盖（不触真实数据根，全部 tmp 合成）：
  1. manifest 行数对账：footer 求和 vs manifest → delta 检出；
  2. manifest 修复：apply 后 rows 与 footer 一致 + 备份 manifest；
  3. 行 hash：显式列 md5（不用 COLUMNS(*) 展开），可空值哨兵；
  4. freshness 口径：ann_date 为 catalog 口径，end_date 含未来期 → 说明文案。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "datawarehouse"))


def _load():
    p = ROOT / "tools" / "datawarehouse" / "quality_legacy_repair.py"
    spec = importlib.util.spec_from_file_location("qlr_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load()


def _write_parquet(path: Path, rows: list[dict]):
    import pyarrow as pa
    import pyarrow.parquet as pq
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def test_manifest_delta_and_fix(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(mod, "DATA", data)
    monkeypatch.setattr(mod, "CHECK_TABLES", ["ts_daily_basic"])
    monkeypatch.setattr(mod, "DUP_TABLES", [])
    tdir = data / "tdx" / "ts_daily_basic"
    _write_parquet(tdir / "part_0000.parquet",
                   [{"ts_code": "000001.SZ", "trade_date": "20261030"},
                    {"ts_code": "600000.SH", "trade_date": "20261030"}])
    (data / "tdx" / "_manifest.json").write_text(json.dumps(
        {"ts_daily_basic": {"rows": 10, "files": 0}}), encoding="utf-8")
    chk = mod.check_manifest_delta(tables=["ts_daily_basic"])
    assert chk["all_ok"] is False
    assert chk["mismatch"][0]["footer_rows"] == 2
    assert chk["mismatch"][0]["delta"] == -8
    res = mod.apply_manifest_fix(tag="t1")
    assert res["changed"][0]["after_rows"] == 2
    assert Path(res["backup"]).exists()
    assert mod.check_manifest_delta(tables=["ts_daily_basic"])["all_ok"] is True


def test_row_hash_expr_explicit_no_columns_star():
    expr = mod._row_hash_expr(["ts_code", "trade_date"])
    assert "md5(" in expr and "COLUMNS(" not in expr
    assert 'coalesce(CAST("ts_code" AS VARCHAR)' in expr
    assert expr.count("coalesce") == 2


def test_freshness_basis_ann_date(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(mod, "DATA", data)
    tdir = data / "tdx" / "ts_forecast"
    _write_parquet(tdir / "part_0000.parquet", [
        {"ts_code": "000001.SZ", "ann_date": "20261013", "end_date": "20271231"},
        {"ts_code": "600000.SH", "ann_date": "20261012", "end_date": "20261231"},
    ])
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"tables": {"ts_forecast": {
        "freshness": "20261013", "date_col": "ann_date"}}}), encoding="utf-8")
    import common
    monkeypatch.setattr(common, "CATALOG", catalog)
    info = mod.freshness_basis()
    assert info["catalog_freshness"] == "20261013"
    assert info["footer_max_end_date"] == "20271231"
    assert info["footer_max_ann_date"] == "20261013"
    assert "ann_date" in info["explanation"] and "非数据缺陷" in info["explanation"]
