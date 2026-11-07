# -*- coding: utf-8 -*-
"""S3-05 lineage DB 构建测试（隔离输出，不覆盖 research/lineage.db）。"""
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "datawarehouse"))

import build_lineage_db as bl  # noqa: E402


def test_build_lineage_db(tmp_path):
    db = tmp_path / "lineage.db"
    js = tmp_path / "lineage.json"
    summary = bl.build(db, js)
    assert db.exists() and js.exists()
    assert summary["nodes"] >= 50
    assert summary["edges"] >= 30
    assert summary["hypotheses"]["rows"] == 53
    assert summary["hypotheses"]["with_evidence"] == 53
    assert summary["strategies"]["with_source_hypothesis"] == 11
    assert summary["lineage_yaml"]["files"] == 4


def test_db_schema_and_edges(tmp_path):
    db = tmp_path / "lineage.db"
    bl.build(db, tmp_path / "l.json")
    con = sqlite3.connect(db)
    try:
        nodes = con.execute("select count(*) from nodes").fetchone()[0]
        edges = con.execute("select count(*) from edges").fetchone()[0]
        assert nodes >= 50 and edges >= 30
        kinds = {r[0] for r in con.execute("select distinct kind from nodes")}
        assert {"hypothesis", "strategy", "data_layer"} <= kinds
        ek = {r[0] for r in con.execute("select distinct kind from edges")}
        assert "derived_strategy" in ek and "data_flow" in ek
        assert con.execute("select count(*) from nodes where id='H53'").fetchone()[0] == 1
        assert con.execute("select count(*) from edges where src='H53'").fetchone()[0] >= 1
        assert con.execute("select count(*) from nodes where kind='strategy'").fetchone()[0] == 11
        meta = dict(con.execute("select key,value from meta").fetchall())
        assert int(meta["node_count"]) == nodes
    finally:
        con.close()
