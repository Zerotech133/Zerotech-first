# -*- coding: utf-8 -*-
"""联动矩阵生成器测试。"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_page_linkage as e  # noqa: E402


def test_rows_cover_all_entries():
    rows = e.build_rows()
    assert len(rows) == 89
    assert rows[0]["领域"] == "量化"
    assert any(r["路由"] == "/os-entry" for r in rows)
    assert any(r["备注"].startswith("外部链接") for r in rows)


def test_csv_roundtrip(tmp_path):
    rc = e.main(["--out-dir", str(tmp_path)])
    assert rc == 0
    p = tmp_path / "2026-10-27_前端页面联动矩阵.csv"
    with open(p, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 89
    assert rows[0]["层级"] in ("L0/L1", "L2 工作台", "L3 明细/工具")
