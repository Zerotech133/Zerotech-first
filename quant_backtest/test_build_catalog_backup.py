# -*- coding: utf-8 -*-
"""build_catalog 备份目录排除测试（2026-11-05）。

背景：refresh_snapshot 整表重刷时把旧目录改名成 <table>.bak-<ts> 并按设计保留
30 天。build_catalog 只按 "_"/"." 前缀过滤，于是备份目录被收成一张幽灵表——
2026-11-05 实测 ts_ths_member.bak-20261105_123343 成为第 259 张 catalog 表，
rows=null、date_col=null，且名字永远对不上远端，在上游健康度盘点里直接被判"下架"。
"""

import pandas as pd

import build_catalog


def _write_table(root, name):
    d = root / "data" / "tdx" / name
    d.mkdir(parents=True)
    pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20261104"]}).to_parquet(
        d / "part_0001.parquet", index=False)


def test_backup_dir_not_indexed(tmp_path):
    _write_table(tmp_path, "tblA")
    _write_table(tmp_path, "tblA.bak-20261105_123343")

    cat = build_catalog.build(data_root=str(tmp_path),
                              out_path=str(tmp_path / "catalog.json"))
    tables = cat["tables"]
    assert "tblA" in tables
    assert not [t for t in tables if ".bak-" in t]
    assert "tblA.bak-20261105_123343" not in tables
