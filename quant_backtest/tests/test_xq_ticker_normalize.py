# -*- coding: utf-8 -*-
"""xq_ticker_normalize / xq_norm_migrate 契约测试（P0-1，2026-10-19）。

覆盖面：
  ① 四种词形：港股 5 位码 / A股 6 位码 / 中文指数（映射命中与未命中）/ 基金词形
  ② 混合数组、去空白保序、未知词形 unknown
  ③ 空与畸形 JSON：None/空串/'[]'/非数组/解析失败 → 兜底契约
  ④ 迁移幂等：ensure_norm_column 列存在不重复加；backfill 二次执行零写入；
     原行数据零修改（只写新列）
  ⑤ kind 分布口径与 KIND_DIST_SQL 一致
跑法：python -m pytest tests/test_xq_ticker_normalize.py -q
（独立临时库，不触生产库 data/xueqiu/xueqiu_info.db。）
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
TOOLS_DIR = REPO_ROOT / "tools"

for _p in (str(REPO_ROOT), str(TOOLS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import xq_ticker_normalize as xtn  # noqa: E402
import xq_norm_migrate as xnm      # noqa: E402
import xq_classify as xc           # noqa: E402


# ---------------------------------------------------------------------------
# ① 四种词形
# ---------------------------------------------------------------------------

def test_hk_5digit_stock():
    (it,) = xtn.normalize_tickers('["01810"]')
    assert it == {"raw": "01810", "kind": "stock_hk", "canonical": "01810"}


def test_a_share_6digit_sh_and_sz():
    (sh,) = xtn.normalize_tickers('["600519"]')
    assert sh["kind"] == "stock_a" and sh["canonical"] == "SH600519"
    (sz,) = xtn.normalize_tickers('["000838"]')
    assert sz["kind"] == "stock_a" and sz["canonical"] == "SZ000838"
    (bj,) = xtn.normalize_tickers('["835185"]')
    assert bj["kind"] == "stock_a" and bj["canonical"] == "BJ835185"


def test_index_mapped_via_xq_index_map():
    (it,) = xtn.normalize_tickers('["沪深300指数"]')
    assert it["kind"] == "index" and it["canonical"] == "000300.SH"
    (it2,) = xtn.normalize_tickers('["红利指数"]')
    assert it2["kind"] == "index" and it2["canonical"] == "000922.SH"
    (it3,) = xtn.normalize_tickers('["创业板指数"]')
    assert it3["kind"] == "index" and it3["canonical"] == "399006.SZ"


def test_index_unmapped_keeps_kind_null_canonical():
    """表外指数词：kind=index 保留分栏信息，canonical=null 不冒造代码。"""
    (it,) = xtn.normalize_tickers('["医药指数"]')
    assert it == {"raw": "医药指数", "kind": "index", "canonical": None}


def test_fund_category_word():
    (it,) = xtn.normalize_tickers('["债券基金"]')
    assert it == {"raw": "债券基金", "kind": "fund", "canonical": None}
    (it2,) = xtn.normalize_tickers('["货币基金"]')
    assert it2["kind"] == "fund" and it2["canonical"] is None


def test_fund_known_etf_name_and_sse_code():
    (etf,) = xtn.normalize_tickers('["沪深300ETF"]')
    assert etf["kind"] == "fund" and etf["canonical"] == "510300.SH"
    (code,) = xtn.normalize_tickers('["512910"]')
    assert code["kind"] == "fund" and code["canonical"] == "SH512910"
    (lof,) = xtn.normalize_tickers('["159915"]')
    assert lof["kind"] == "fund" and lof["canonical"] == "SZ159915"


# ---------------------------------------------------------------------------
# ② 混合数组 / 未知词形 / 归一
# ---------------------------------------------------------------------------

def test_mixed_array_order_preserved():
    items = xtn.normalize_tickers('["01810","600519","沪深300指数","债券基金","乱七八糟"]')
    assert [i["kind"] for i in items] == [
        "stock_hk", "stock_a", "index", "fund", "unknown"]
    assert items[0]["canonical"] == "01810"
    assert items[4] == {"raw": "乱七八糟", "kind": "unknown", "canonical": None}


def test_fullwidth_and_whitespace_normalized():
    """NFKC + 去空白：全角数字/空白仍命中同一词形。"""
    (it,) = xtn.normalize_tickers('[" ００７００ "]')
    assert it["kind"] == "stock_hk" and it["canonical"] == "00700"


def test_digit_noise_is_unknown():
    """段外 6 位数字（编号/年月形）不冒认标的；5 位段全合法（上游已语境过滤）；
    「15」段 6 位码属深市场内基金白名单，归 fund 而非金额噪音。"""
    (a,) = xtn.normalize_tickers('["123456"]')   # 段外 6 位
    assert a["kind"] == "unknown" and a["canonical"] is None
    (b,) = xtn.normalize_tickers('["31000"]')
    assert b["kind"] == "stock_hk"
    (c,) = xtn.normalize_tickers('["150000"]')   # 「15」段 → 深市场内基金
    assert c["kind"] == "fund" and c["canonical"] == "SZ150000"
    (d,) = xtn.normalize_tickers('["200009"]')   # 年月形，段外 → unknown
    assert d["kind"] == "unknown" and d["canonical"] is None


# ---------------------------------------------------------------------------
# ③ 空与畸形 JSON
# ---------------------------------------------------------------------------

def test_empty_inputs_return_empty_list():
    assert xtn.normalize_tickers(None) == []
    assert xtn.normalize_tickers("") == []
    assert xtn.normalize_tickers("   ") == []
    assert xtn.normalize_tickers("[]") == []
    # 空白词形元素跳过
    assert xtn.normalize_tickers('["", "  ", "01810"]') == [
        {"raw": "01810", "kind": "stock_hk", "canonical": "01810"}]


def test_malformed_json_bails_to_single_unknown():
    """解析失败/顶层非数组 → 单条 unknown 兜底，raw 保留现场。"""
    for bad in ('not json', '{"a":1}', '"01810"', '["01810", ', '123'):
        items = xtn.normalize_tickers(bad)
        assert len(items) == 1
        assert items[0]["kind"] == "unknown"
        assert items[0]["canonical"] is None
        assert items[0]["raw"] == bad


def test_non_string_elements_coerced():
    items = xtn.normalize_tickers('[18010, null]')
    assert items[0]["kind"] == "stock_hk" and items[0]["raw"] == "18010"
    assert items[1]["kind"] == "unknown"


# ---------------------------------------------------------------------------
# ④ 迁移幂等（独立临时库）
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path):
    """带 xq_status_classified 表 + 样本行的独立临时库。"""
    db = tmp_path / "xq_test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""CREATE TABLE xq_status_classified (
        status_id TEXT PRIMARY KEY, event_type TEXT, sentiment TEXT,
        tickers TEXT, confidence REAL, parse_ok INTEGER, model TEXT,
        prompt_version TEXT, labeled_at TEXT, content_type TEXT,
        retry_count INTEGER DEFAULT 0)""")
    rows = [
        ("s1", '["01810"]'),
        ("s2", '["600519","沪深300指数"]'),
        ("s3", '["债券基金"]'),
        ("s4", "[]"),
        ("s5", 'not json'),
        ("s6", None),
    ]
    conn.executemany(
        "INSERT INTO xq_status_classified (status_id, tickers) VALUES (?, ?)",
        rows)
    conn.commit()
    yield conn, rows
    conn.close()
    db.unlink(missing_ok=True)


def test_ensure_norm_column_idempotent(tmp_db):
    conn, _ = tmp_db
    assert xtn.ensure_norm_column(conn) is True
    cols = [r[1] for r in conn.execute("PRAGMA table_info(xq_status_classified)")]
    assert "norm_tickers" in cols
    # 二次执行：不加列、零副作用
    assert xtn.ensure_norm_column(conn) is False
    cols2 = [r[1] for r in conn.execute("PRAGMA table_info(xq_status_classified)")]
    assert cols2.count("norm_tickers") == 1 and len(cols2) == len(cols)


def test_backfill_populates_and_preserves_rows(tmp_db):
    conn, rows = tmp_db
    report = xtn.backfill(conn, batch_size=2)
    assert report["total"] == 6 and report["updated"] == 6
    # 原行数据零修改：只写新列
    originals = conn.execute(
        "SELECT status_id, tickers FROM xq_status_classified ORDER BY status_id"
    ).fetchall()
    assert originals == sorted(rows)
    # 新列内容契约
    got = dict(conn.execute(
        "SELECT status_id, norm_tickers FROM xq_status_classified").fetchall())
    assert json.loads(got["s1"]) == [
        {"raw": "01810", "kind": "stock_hk", "canonical": "01810"}]
    assert [i["kind"] for i in json.loads(got["s2"])] == ["stock_a", "index"]
    assert got["s4"] == "[]"
    assert json.loads(got["s5"])[0]["kind"] == "unknown"


def test_backfill_rerun_zero_writes(tmp_db):
    """幂等：二次 backfill 全部 skip（零写入），结果不变。"""
    conn, _ = tmp_db
    first = xtn.backfill(conn)
    before = conn.execute(
        "SELECT status_id, norm_tickers FROM xq_status_classified "
        "ORDER BY status_id").fetchall()
    second = xtn.backfill(conn)
    after = conn.execute(
        "SELECT status_id, norm_tickers FROM xq_status_classified "
        "ORDER BY status_id").fetchall()
    assert second["updated"] == 0 and second["skipped"] == first["total"]
    assert before == after


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


def test_ticker_map_json_contract():
    """ticker_map.json 契约：kind 词表、代码段路由、canonical 风格完整。"""
    assert set(xtn._MAP["kind_vocab"]) == set(xtn.KINDS)
    assert xtn._MAP["index_source"]["module"] == "xq_index_map"
    # 代码段：每段命中可路由，canonical 前缀=交易所键
    for exch, groups in xtn._SEGMENTS.items():
        for kind, segs in groups.items():
            for seg in segs:
                kind_got, canonical = xtn._route_6digit(seg + "000")
                assert (kind_got, canonical) == (kind, exch + seg + "000")
    # ETF 名称表键尾缀均为基金词形
    for name in xtn._FUND_NAME_CODES:
        assert name.lower().endswith(xtn._FUND_SUFFIXES), name


def test_kind_dist_sql_runs(tmp_db):
    """KIND_DIST_SQL 一行验证可执行且口径与 backfill kinds 计数一致。"""
    conn, _ = tmp_db
    xtn.backfill(conn)
    dist = dict(conn.execute(xtn.KIND_DIST_SQL).fetchall())
    assert dist["stock_hk"] == 1 and dist["stock_a"] == 1
    assert dist["index"] == 1 and dist["fund"] == 1
    # unknown 只来自 s5 畸形 JSON 兜底；s6 tickers=None → 空，不产生词条
    assert dist["unknown"] == 1


def test_classify_ensures_norm_column_on_legacy_table(tmp_db):
    """旧表迁移只加一次 norm_tickers，不改已有数据。"""
    conn, rows = tmp_db
    xc.ensure_table(conn)
    xc.ensure_table(conn)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(xq_status_classified)")]
    assert cols.count("norm_tickers") == 1
    assert conn.execute(
        "SELECT status_id, tickers FROM xq_status_classified ORDER BY status_id"
    ).fetchall() == sorted(rows)


def test_classify_writes_normalized_tickers_with_retry(tmp_path, monkeypatch):
    """新写与重标在同一行保留原始词形、归一化词形及重试计数。"""
    conn = sqlite3.connect(tmp_path / "classify.db")
    xc.ensure_table(conn)

    class FakeResponse:
        def read(self):
            return json.dumps({"message": {"content": json.dumps({
                "event_type": "个股研判", "sentiment": "bullish",
                "content_type": "community", "confidence": 0.9,
            }, ensure_ascii=False)}}).encode("utf-8")

    monkeypatch.setattr(xc.urllib.request, "urlopen", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(xc, "log", lambda *args, **kwargs: None)
    row = ("s1", "看600519与沪深300指数", "债券基金", "stock")
    try:
        assert xc.classify_one(conn, row) is True
        first = conn.execute(
            "SELECT tickers, norm_tickers FROM xq_status_classified WHERE status_id='s1'"
        ).fetchone()
        assert json.loads(first[1]) == xtn.normalize_tickers(first[0])
        assert json.loads(first[0])
        conn.execute("UPDATE xq_status_classified SET retry_count=2 WHERE status_id='s1'")
        conn.commit()
        assert xc.classify_one(conn, row) is True
        raw, norm, retry = conn.execute(
            "SELECT tickers, norm_tickers, retry_count FROM xq_status_classified "
            "WHERE status_id='s1'"
        ).fetchone()
        assert json.loads(norm) == xtn.normalize_tickers(raw)
        assert retry == 2
        assert conn.execute("SELECT COUNT(*) FROM xq_status_classified").fetchone()[0] == 1
    finally:
        conn.close()
