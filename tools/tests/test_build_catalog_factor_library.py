# -*- coding: utf-8 -*-
"""build_catalog._scan_factor_library 单测（2026-11-05 factor_list 静态资产登记）。

不碰生产数据：在 tmp 目录构造 latest.parquet + snapshots/*.parquet + manifest.json，
断言 catalog 条目为静态元数据（无日期列/static_no_date/freq=M/rows 取 manifest）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "quant_backtest"))
import build_catalog  # noqa: E402

FIELDS = ["factor_name", "asset_type", "factor_type", "factor_desc"]


def _write_parquet(path: Path, n: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "factor_name": ["alpha_001", "growth_001"][:n],
            "asset_type": ["STK"] * n,
            "factor_type": ["Alpha101", "Growth"][:n],
            "factor_desc": ["动量因子", "成长因子"][:n],
        }
    ).to_parquet(path, index=False)


def _make_root(tmp_path: Path, *, with_manifest: bool = True, errors=None) -> Path:
    root = tmp_path / "tushare_factor_list"
    _write_parquet(root / "latest.parquet")
    _write_parquet(root / "snapshots" / "factor_list_20260101.parquet")
    if with_manifest:
        rec = {
            "api": "factor_list",
            "rows": 2,
            "errors": errors or [],
            "by_type": {"Alpha101": 1, "Growth": 1},
            "by_asset": {"STK": 2},
            "sha256": "a" * 64,
            "fetched_at": "2026-01-01 00:00:00",
            "token_sha8": "deadbeef",
        }
        (root / "manifest.json").write_text(
            json.dumps(rec, ensure_ascii=False), encoding="utf-8"
        )
    return root


def test_factor_library_catalog_entry_is_static_metadata(tmp_path):
    entry = build_catalog._scan_factor_library(_make_root(tmp_path))["factor_list"]
    assert entry["source"] == "factor_library"
    assert entry["layout"] == "parquet_snapshot"
    assert entry["rows"] == 2 and entry["factor_count"] == 2
    assert entry["files"] == 2  # latest + 1 个 snapshot
    assert entry["done"] is True
    assert entry["static_no_date"] is True
    assert entry["date_status"] == "not_applicable"
    assert entry["date_col"] is None
    assert entry["freq"] == "M"
    assert entry["primary_key"] == ["factor_name"]
    assert entry["manifest_status"] == "closed"
    assert entry["schema"] == FIELDS
    assert entry["by_type"] == {"Alpha101": 1, "Growth": 1}
    assert entry["by_asset"] == {"STK": 2}
    assert entry["sha256"] == "a" * 64


def test_factor_library_manifest_errors_not_done(tmp_path):
    entry = build_catalog._scan_factor_library(
        _make_root(tmp_path, errors=["parse failed"])
    )["factor_list"]
    assert entry["done"] is False
    assert entry["manifest_status"] == "open"


def test_factor_library_missing_manifest_degrades_visible(tmp_path):
    entry = build_catalog._scan_factor_library(
        _make_root(tmp_path, with_manifest=False)
    )["factor_list"]
    assert entry["rows"] is None
    assert entry["done"] is False
    assert entry["manifest"] is None
    assert entry["manifest_status"] == "missing"
    assert entry["readiness_basis"] == "file_presence"
    # 仍必须标静态：datamart 不应把无日期字典表判 stale
    assert entry["static_no_date"] is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
