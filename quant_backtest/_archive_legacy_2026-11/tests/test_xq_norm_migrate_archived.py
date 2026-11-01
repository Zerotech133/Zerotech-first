# -*- coding: utf-8 -*-
"""[已归档] xq_norm_migrate 迁移脚本测试（随 D① 归档；真源合并至 xq_ticker_norm）。
原属 tests/test_xq_ticker_normalize.py 的 migrate 段，脚本归档后随迁。
跑法（如需复验归档脚本）：pytest _archive_legacy_2026-11/tests/test_xq_norm_migrate_archived.py -q
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent          # _archive_legacy_2026-11/tests/
ARCH_ROOT = HERE.parent                        # _archive_legacy_2026-11/
QB_ROOT = ARCH_ROOT.parent                     # quant_backtest/
ARCH_TOOLS = ARCH_ROOT / "tools"               # 归档脚本所在

TOOLS_DIR = QB_ROOT / "tools"                # xq_ticker_normalize shim 所在
for _p in (str(QB_ROOT), str(ARCH_TOOLS), str(TOOLS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import xq_norm_migrate as xnm  # noqa: E402
import xq_ticker_normalize as xtn  # noqa: E402  # monkeypatch 目标(shim)

def test_migration_dry_run_is_read_only_and_preserves_delete_journal(tmp_path, monkeypatch):
    """预演保持 delete 模式、文件字节、行与 sidecar 原样，且不走写入函数。"""
    db = tmp_path / "dry run #1%.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE xq_status_classified (status_id TEXT PRIMARY KEY, "
            "tickers TEXT)"
        )
        conn.executemany(
            "INSERT INTO xq_status_classified VALUES (?, ?)",
            [("s1", '["01810","沪深300指数"]'), ("s2", "[]")],
        )
        conn.commit()
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        before_rows = conn.execute(
            "SELECT status_id, tickers FROM xq_status_classified ORDER BY status_id"
        ).fetchall()
    before_bytes = db.read_bytes()
    sidecars = [Path(str(db) + suffix) for suffix in ("-journal", "-wal", "-shm")]
    before_sidecars = {str(path): path.read_bytes() if path.exists() else None
                       for path in sidecars}

    def unexpected_write(*args, **kwargs):
        raise AssertionError("dry-run must not invoke a migration write path")

    monkeypatch.setattr(xnm, "_set_wal", unexpected_write)
    monkeypatch.setattr(xtn, "ensure_norm_column", unexpected_write)
    monkeypatch.setattr(xtn, "backfill", unexpected_write)
    report = xnm.migrate(db, dry_run=True)
    assert report == {
        "db": str(db), "journal_mode": "delete", "dry_run": True,
        "total": 2, "updated": 0, "skipped": 0,
        "kinds": {"stock_hk": 1, "index": 1},
    }
    assert db.read_bytes() == before_bytes
    assert {str(path): path.read_bytes() if path.exists() else None
            for path in sidecars} == before_sidecars
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert conn.execute(
            "SELECT status_id, tickers FROM xq_status_classified ORDER BY status_id"
        ).fetchall() == before_rows
        assert [row[1] for row in conn.execute(
            "PRAGMA table_info(xq_status_classified)"
        )] == ["status_id", "tickers"]


def test_migration_dry_run_read_only_file_and_missing_database(tmp_path):
    """只读文件能安全预演；含 URI 特殊字符的缺失库明确失败且不新建。"""
    db = tmp_path / "readonly #%.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE xq_status_classified (status_id TEXT, tickers TEXT)")
        conn.execute("INSERT INTO xq_status_classified VALUES ('s1', '[\"00700\"]')")
    original_mode = db.stat().st_mode
    try:
        db.chmod(0o444)
        assert xnm.migrate(db, dry_run=True)["kinds"] == {"stock_hk": 1}
        assert db.stat().st_mode & 0o222 == 0
    finally:
        db.chmod(original_mode)
    missing = tmp_path / "missing #%.db"
    with pytest.raises(sqlite3.OperationalError, match="unable to open database file"):
        xnm.migrate(missing, dry_run=True)
    assert not missing.exists()
    assert not Path(str(missing) + "-journal").exists()


def test_verify_read_only_uri_does_not_create_missing_database(tmp_path):
    """verify 遇到 URI 特殊字符的缺失路径，明确失败且不创建任何文件。"""
    missing = tmp_path / "verify missing #%.db"
    with pytest.raises(sqlite3.OperationalError, match="unable to open database file"):
        xnm.verify(missing)
    assert not missing.exists()
    assert not any(Path(str(missing) + suffix).exists()
                   for suffix in ("-journal", "-wal", "-shm"))


def test_verify_sees_uncheckpointed_wal_and_preserves_main_file(tmp_path):
    """只读 URI 不使用 immutable，能看见尚在 WAL 中的已提交分类记录。"""
    db = tmp_path / "verify WAL #%.db"
    writer = sqlite3.connect(db)
    try:
        writer.execute(
            "CREATE TABLE xq_status_classified (status_id TEXT PRIMARY KEY, "
            "norm_tickers TEXT)"
        )
        writer.commit()
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        main_before = db.read_bytes()
        writer.execute(
            "INSERT INTO xq_status_classified VALUES (?, ?)",
            ("s1", json.dumps([{"raw": "01810", "kind": "stock_hk",
                                 "canonical": "01810"}])),
        )
        writer.commit()
        wal = Path(str(db) + "-wal")
        assert wal.exists() and wal.stat().st_size > 0
        assert db.read_bytes() == main_before
        wal_before = wal.read_bytes()
        assert xnm.verify(db) == [("stock_hk", 1)]
        assert db.read_bytes() == main_before
        assert wal.read_bytes() == wal_before
        assert wal.stat().st_size > 0
    finally:
        writer.close()


def test_dry_run_sees_committed_uncheckpointed_wal_without_writes(tmp_path):
    """预演必须读取 WAL 中已提交的新行，且不得触发 checkpoint。"""
    db = tmp_path / "dry WAL #%.db"
    writer = sqlite3.connect(db)
    try:
        writer.execute(
            "CREATE TABLE xq_status_classified (status_id TEXT PRIMARY KEY, "
            "tickers TEXT)"
        )
        writer.commit()
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute(
            "INSERT INTO xq_status_classified VALUES (?, ?)",
            ("s1", '["01810","债券基金"]'),
        )
        writer.commit()
        wal = Path(str(db) + "-wal")
        assert wal.exists() and wal.stat().st_size > 0
        main_before, wal_before = db.read_bytes(), wal.read_bytes()

        report = xnm.migrate(db, dry_run=True)

        assert report["journal_mode"] == "wal"
        assert report["total"] == 1
        assert report["kinds"] == {"stock_hk": 1, "fund": 1}
        assert report["updated"] == 0
        assert db.read_bytes() == main_before
        assert wal.read_bytes() == wal_before
    finally:
        writer.close()


def test_verify_preserves_delete_journal_database_and_sidecars(tmp_path):
    """DELETE 模式验证只读；路径空格、井号、百分号检验 URI 转义。"""
    db = tmp_path / "verify #%.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE xq_status_classified "
            "(status_id TEXT PRIMARY KEY, norm_tickers TEXT)"
        )
        conn.execute(
            "INSERT INTO xq_status_classified VALUES (?, ?)",
            ("s1", json.dumps([{"kind": "stock_a"}]))
        )
        conn.commit()
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    sidecars = [Path(str(db) + suffix) for suffix in ("-journal", "-wal", "-shm")]
    main_before = db.read_bytes()
    sidecars_before = {str(path): path.read_bytes() if path.exists() else None
                       for path in sidecars}

    assert xnm.verify(db) == [("stock_a", 1)]

    assert db.read_bytes() == main_before
    assert {str(path): path.read_bytes() if path.exists() else None
            for path in sidecars} == sidecars_before
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_missing_schema_errors_leave_existing_database_untouched(tmp_path):
    """读入口无法读取缺失表/列时应显式报错且不补建结构。"""
    db = tmp_path / "schema #%.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE unrelated (id INTEGER)")
        conn.commit()
    before = db.read_bytes()

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        xnm.migrate(db, dry_run=True)
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        xnm.verify(db)

    assert db.read_bytes() == before
    with sqlite3.connect(db) as conn:
        assert [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )] == ["unrelated"]


def test_migration_script_end_to_end(tmp_path):
    """xq_norm_migrate.migrate 全链：加列+回填；重复跑零副作用。"""
    db = tmp_path / "mig.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE xq_status_classified (status_id TEXT PRIMARY KEY, tickers TEXT)")
    conn.executemany(
        "INSERT INTO xq_status_classified VALUES (?, ?)",
        [("a1", '["00700"]'), ("a2", '["红利指数"]'), ("a3", "[]")])
    conn.commit()
    conn.close()

    r1 = xnm.migrate(db, batch_size=2)
    assert r1["added_column"] is True and r1["updated"] == 3 and r1["total"] == 3
    dist = xnm.verify(db)
    assert dict(dist) == {"stock_hk": 1, "index": 1}

    r2 = xnm.migrate(db, batch_size=2)
    assert r2["added_column"] is False and r2["updated"] == 0
    assert r2["skipped"] == 3
    assert xnm.verify(db) == dist

