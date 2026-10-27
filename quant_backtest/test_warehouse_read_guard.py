"""受管读发布回归：真实临时 Parquet、SQLite 与 Windows 跨进程锁。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import ast
import importlib
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import datamart as dm
import tdx_read as tdx
from warehouse_commit import PublicationPending, publication_guard


def helper():
    try:
        return importlib.import_module("warehouse_read_guard")
    except ModuleNotFoundError:
        pytest.fail("缺少共享受管读发布保护模块")


def frame(value, code="A"):
    return pd.DataFrame(
        {"ts_code": [code], "trade_date": ["20261008"], "close": [value]}
    )


def pending(root):
    directory = root / ".warehouse-publication" / "pending"
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / "interrupted.json"
    marker.write_text("{}", encoding="utf-8")
    return marker


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    root = tmp_path / "tdx"
    store = root / "ts_daily"
    store.mkdir(parents=True)
    frame(100).to_parquet(store / "part_0000.parquet", index=False)
    frame(200, "B").to_parquet(store / "part_0001.parquet", index=False)
    manifest = root / "_manifest.json"
    manifest.write_text(json.dumps({"ts_daily": {"done": True}}), encoding="utf-8")
    catalog = tmp_path / "catalog.json"
    meta = {
        "source": "xtcloud",
        "store": str(store),
        "layout": "part",
        "done": True,
        "manifest": str(manifest),
        "code_col": "ts_code",
        "date_col": "trade_date",
        "schema": ["ts_code", "trade_date", "close"],
        "primary_key": ["ts_code", "trade_date"],
    }
    catalog.write_text(
        json.dumps(
            {
                "tables": {"ts_daily": meta},
                "aliases": {"daily": {"resolves": ["ts_daily"]}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dm, "CATALOG", str(catalog))
    monkeypatch.setattr(dm, "_cat_cache", {"mtime": None, "data": None})
    monkeypatch.setattr(dm, "_latest_cache", {})
    monkeypatch.setattr(dm, "_fresh_cache", {"ts": 0, "data": None})
    monkeypatch.setattr(dm, "_trade_cal_cache", {"ts": 0, "days": None})
    monkeypatch.setattr(dm, "_prev_cache", {})
    monkeypatch.setattr(dm, "FRESHNESS_SPEC", [("daily", "临时行情", 0, "测试")])
    monkeypatch.setattr(tdx, "OUT", str(root))
    monkeypatch.setattr(tdx, "INFO_DB", str(root / "tdx_info.db"))
    monkeypatch.setattr(tdx, "MANIFEST", str(manifest))
    return root, catalog


@pytest.mark.parametrize(
    "operation",
    [
        lambda: dm.read("daily"),
        lambda: dm.ready("daily"),
        lambda: dm.latest("daily"),
        lambda: dm.explain("daily"),
        lambda: dm.columns("daily"),
        lambda: dm.table_preview("daily"),
        lambda: dm.freshness_report(),
        lambda: dm.freshness_banner(),
    ],
)
def test_datamart_pending_never_returns_empty_fallback_or_cached_success(
    warehouse, monkeypatch, operation
):
    root, _ = warehouse
    assert dm.latest("daily") == "20261008"
    monkeypatch.setattr(
        dm,
        "_fresh_cache",
        {
            "ts": time.time(),
            "data": {
                "expect": "20261008",
                "rows": [{"table": "daily", "status": "ok", "latest": "20261008"}],
            },
        },
    )
    pending(root)
    with pytest.raises(PublicationPending):
        operation()


@pytest.mark.parametrize(
    "operation",
    [
        lambda: tdx.read_day("ts_daily", "20261008"),
        lambda: tdx.read_range("ts_daily"),
        lambda: tdx.read_all("ts_daily"),
        lambda: tdx.latest_date("ts_daily"),
        lambda: tdx.table_ready("ts_daily"),
        lambda: tdx.fresh("ts_daily", "20261008"),
        lambda: tdx.day_map("ts_daily", "20261008", ["close"]),
    ],
)
def test_tdx_pending_is_explicit_before_enumeration_and_manifest(warehouse, operation):
    root, _ = warehouse
    pending(root)
    with pytest.raises(PublicationPending):
        operation()


def test_alias_locks_unready_secondary_root_before_primary_materialization(warehouse):
    root, catalog = warehouse
    other = root.parent / "tushare"
    store = other / "daily"
    store.mkdir(parents=True)
    frame(300).to_parquet(store / "A.parquet", index=False)
    cat = json.loads(catalog.read_text("utf-8"))
    cat["tables"]["daily"] = dict(
        cat["tables"]["ts_daily"],
        source="tushare",
        store=str(store),
        layout="by_code",
        manifest=None,
        done=False,
    )
    cat["aliases"]["daily"]["resolves"].append("daily")
    catalog.write_text(json.dumps(cat), encoding="utf-8")
    pending(other)
    with pytest.raises(PublicationPending):
        dm.read("daily")


def test_info_db_and_manifest_use_actual_dynamic_parent_roots(warehouse, monkeypatch):
    root, _ = warehouse
    db_root = root.parent / "custom-info"
    db_root.mkdir()
    database = db_root / "custom.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE stock_notice(ts_code TEXT, notice_date TEXT)")
        conn.execute("INSERT INTO stock_notice VALUES('A','2026-10-08')")
    monkeypatch.setattr(tdx, "INFO_DB", str(database))
    assert len(tdx.read_info("stock_notice")) == 1
    pending(db_root)
    with pytest.raises(PublicationPending):
        tdx.read_info("stock_notice")
    mf_root = root.parent / "custom-manifest"
    mf_root.mkdir()
    mf = mf_root / "state.json"
    mf.write_text('{"ts_daily":{"done":true}}', encoding="utf-8")
    monkeypatch.setattr(tdx, "MANIFEST", str(mf))
    pending(mf_root)
    with pytest.raises(PublicationPending):
        tdx.table_ready("ts_daily")


def test_absent_warehouse_keeps_empty_contract_without_creating_it(
    tmp_path, monkeypatch
):
    root = tmp_path / "absent"
    monkeypatch.setattr(tdx, "OUT", str(root))
    monkeypatch.setattr(tdx, "MANIFEST", str(root / "_manifest.json"))
    monkeypatch.setattr(tdx, "INFO_DB", str(root / "tdx_info.db"))
    assert tdx.read_all("ts_daily").empty
    assert tdx.read_info("stock_notice") == []
    assert tdx.latest_date("ts_daily") is None
    assert tdx.table_ready("ts_daily") is False
    assert not root.exists()


def test_nested_read_reuses_locks_and_multiple_roots_have_deterministic_order(
    tmp_path, monkeypatch
):
    h = helper()
    roots = [tmp_path / "z", tmp_path / "a"]
    for root in roots:
        root.mkdir()
    entered = []

    @contextmanager
    def tracked(root, *, mode):
        with publication_guard(root, mode=mode):
            entered.append(str(Path(root).resolve()))
            yield root

    monkeypatch.setattr(h, "publication_guard", tracked)
    with h.read_resources(roots):
        with h.read_resources(list(reversed(roots))):
            pass
    assert entered == [str((tmp_path / "a").resolve()), str((tmp_path / "z").resolve())]


def _replace_writer(root, attempting, acquired):
    root = Path(root)
    attempting.set()
    with publication_guard(root, mode="write"):
        acquired.set()
        store = root / "ts_daily"
        for number, value in [(0, 101), (1, 201)]:
            target = store / ("part_%04d.parquet" % number)
            stage = store / ("stage_%d.parquet" % number)
            frame(value, "A" if number == 0 else "B").to_parquet(stage, index=False)
            os.replace(stage, target)


def test_shared_reader_holds_lock_through_materialization_against_process_writer(
    warehouse, monkeypatch
):
    root, _ = warehouse
    paused, resume = threading.Event(), threading.Event()
    original = pd.read_parquet

    def slow_read(path, *args, **kwargs):
        if Path(path).name == "part_0000.parquet":
            paused.set()
            assert resume.wait(15), "测试读线程没有获得放行"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(pd, "read_parquet", slow_read)
    ctx = multiprocessing.get_context("spawn")
    attempting, acquired = ctx.Event(), ctx.Event()
    process = ctx.Process(
        target=_replace_writer, args=(str(root), attempting, acquired)
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(dm.read, "daily")
        assert paused.wait(10)
        process.start()
        try:
            assert attempting.wait(10)
            assert not acquired.wait(0.5), "物化结束前写进程获得了排他发布锁"
        finally:
            resume.set()
        result = future.result(timeout=15)
    process.join(15)
    if process.is_alive():
        process.terminate()
        process.join()
        pytest.fail("写进程未结束")
    assert process.exitcode == 0
    assert result.close.tolist() == [100, 200]
    assert dm.read("daily").close.tolist() == [101, 201]


def _crashing_writer(root, changed, release):
    root = Path(root)
    with publication_guard(root, mode="write"):
        pending(root)
        store = root / "ts_daily"
        frame(101).to_parquet(store / "stage.parquet", index=False)
        os.replace(store / "stage.parquet", store / "part_0000.parquet")
        changed.set()
        if not release.wait(15):
            os._exit(8)
        os._exit(7)


def test_process_crash_between_parts_blocks_mixed_publication_until_recovery(warehouse):
    root, _ = warehouse
    ctx = multiprocessing.get_context("spawn")
    changed, release = ctx.Event(), ctx.Event()
    process = ctx.Process(target=_crashing_writer, args=(str(root), changed, release))
    process.start()
    try:
        assert changed.wait(10)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(dm.read, "daily")
            try:
                with pytest.raises(TimeoutError):
                    future.result(timeout=0.5)
            finally:
                release.set()
            with pytest.raises(PublicationPending):
                future.result(timeout=15)
    finally:
        release.set()
        process.join(15)
        if process.is_alive():
            process.terminate()
            process.join()
    assert process.exitcode == 7
    with pytest.raises(PublicationPending):
        tdx.read_all("ts_daily")
    with publication_guard(root, mode="write"):
        store = root / "ts_daily"
        frame(201, "B").to_parquet(store / "stage.parquet", index=False)
        os.replace(store / "stage.parquet", store / "part_0001.parquet")
        (root / ".warehouse-publication/pending/interrupted.json").unlink()
    assert dm.read("daily").close.tolist() == [101, 201]


def test_completed_publication_invalidates_latest_and_preview_caches(warehouse):
    root, _ = warehouse
    assert dm.latest("daily") == "20261008"
    assert dm.table_preview("daily")["latest"] == "20261008"
    with publication_guard(root, mode="write"):
        store = root / "ts_daily"
        updated = frame(999)
        updated["trade_date"] = "20261009"
        updated.to_parquet(store / "stage.parquet", index=False)
        os.replace(store / "stage.parquet", store / "part_0000.parquet")
    assert dm.latest("daily") == "20261009"
    assert dm.table_preview("daily")["latest"] == "20261009"


def test_catalog_date_semantics_change_invalidates_old_latest_cache(warehouse):
    root, catalog = warehouse
    store = root / "ts_daily"
    for path in store.glob("part_*.parquet"):
        data = pd.read_parquet(path)
        data["report_date"] = "20260630"
        data.to_parquet(path, index=False)
    assert dm.latest("daily") == "20261008"
    cat = json.loads(catalog.read_text("utf-8"))
    cat["tables"]["ts_daily"]["date_col"] = "report_date"
    # 采用不委托 TDX 固定日期列的 by_period，旧缓存同样不可继续返回旧日期。
    cat["tables"]["ts_daily"]["layout"] = "by_code"
    catalog.write_text(json.dumps(cat), encoding="utf-8")
    assert dm.latest("daily") == "20260630"


def test_tdx_cli_diagnostic_does_not_bypass_dynamic_info_pending(
    warehouse, monkeypatch
):
    root, _ = warehouse
    db_root = root.parent / "cli-info"
    db_root.mkdir()
    db = db_root / "info.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE stock_notice(value TEXT)")
    monkeypatch.setattr(tdx, "INFO_DB", str(db))
    (root / "_manifest.json").write_text(
        '{"stock_notice":{"done":true}}', encoding="utf-8"
    )
    pending(db_root)
    tree = ast.parse(Path(tdx.__file__).read_text(encoding="utf-8-sig"))
    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
    )
    namespace = dict(tdx.__dict__, __name__="__main__")
    with pytest.raises(PublicationPending):
        exec(
            compile(ast.Module(body=[main], type_ignores=[]), "<tdx_cli_test>", "exec"),
            namespace,
        )
