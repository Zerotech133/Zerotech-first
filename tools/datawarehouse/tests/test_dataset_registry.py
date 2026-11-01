# -*- coding: utf-8 -*-
"""dataset_registry 契约测试：id 唯一/list/search/get/sample/emit。"""
import json
import sqlite3
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dataset_registry import DatasetRegistry  # noqa: E402


def _make_env(tmp_path: Path):
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.execute("create table t1 (a int, b text)")
    con.executemany("insert into t1 values (?,?)", [(1, "x"), (2, "y")])
    con.commit(); con.close()
    parquet_dir = tmp_path / "pq"; parquet_dir.mkdir()
    (parquet_dir / "part_0000.parquet").write_bytes(b"")
    inv = tmp_path / "inv.json"
    inv.write_text(json.dumps({"datasets": [
        {"domain": "学习派生", "label": "testdb.t1", "kind": "sqlite_table", "path": str(db).replace("\\", "/"),
         "registered": False, "exposed": False, "suggested_id": "t1_table"},
        {"domain": "学习派生", "label": "pq", "kind": "corpus_layer", "path": str(parquet_dir).replace("\\", "/"),
         "files": 1, "mb": 0.0, "registered": True, "exposed": False,
         "suggested_id": "pq"},
    ]}, ensure_ascii=False), encoding="utf-8")
    return inv


def test_registry_crud(tmp_path):
    reg = DatasetRegistry(_make_env(tmp_path), allowed_roots=[tmp_path])
    assert reg.stats()["total"] == 2
    ids = {d["dataset_id"] for d in reg.datasets}
    assert len(ids) == 2
    assert reg.get("t1_table")
    assert reg.list(domain="学习派生", limit=10)
    assert [d["dataset_id"] for d in reg.list(q="t1")] == ["t1_table"]


def test_sample_sqlite(tmp_path):
    reg = DatasetRegistry(_make_env(tmp_path), allowed_roots=[tmp_path])
    s = reg.sample("t1_table", n=2)
    assert s["columns"] == ["a", "b"]
    assert len(s["sample"]) == 2


def test_sample_dir_and_emit(tmp_path):
    reg = DatasetRegistry(_make_env(tmp_path), allowed_roots=[tmp_path])
    s = reg.sample("pq", n=1)
    assert s["top_entries"]
    assert s["freshness"] is not None or s["top_entries"]
    out = reg.emit_yaml(tmp_path / "out.yaml")
    doc = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert doc["version"] == 1
    assert "学习派生" in doc["domains"]
    assert len(doc["domains"]["学习派生"]) == 2
