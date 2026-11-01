# -*- coding: utf-8 -*-
"""xq_status_add_symbol 契约测试（WO-XQ，2026-10-21）。

覆盖面：
  ① 确定性推导：$名称(代码)$ / $代码$ 记号、SH/SZ/BJ 前缀、6 位段表
     路由、美股字母词形不猜、关联表兜底、多 symbol 歧义留 None；
  ② 首次 dry-run 不改表（字节级）：无 sidecar、无新列、行原样；
  ③ execute 后列存在且回填正确、原列零修改；
  ④ 二次 execute 幂等（零写入）；
  ⑤ 无法映射的行 symbol 为 NULL。
跑法：python -m pytest tests/test_xq_status_add_symbol.py -q
（tmp_path 独立临时库，不触生产库 data/xueqiu/xueqiu_info.db。）
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

import xq_status_add_symbol as xss  # noqa: E402


# ---------------------------------------------------------------------------
# ① 确定性推导（纯函数）
# ---------------------------------------------------------------------------

def test_marker_with_hk_code_paren():
    syms = xss.symbol_from_marker_text("$融创中国(01918)$ 新房价格倒挂")
    assert syms == ["01918"]


def test_marker_with_prefixed_code_paren():
    assert xss.symbol_from_marker_text("$贵州茅台(SH600519)$") == ["SH600519"]
    assert xss.symbol_from_marker_text("$某某(SZ000838)$") == ["SZ000838"]


def test_marker_bare_code():
    assert xss.symbol_from_marker_text("看好 $00700$") == ["00700"]
    assert xss.symbol_from_marker_text("$600519$") == ["SH600519"]
    # 段外 6 位码（年月形）不冒造
    assert xss.symbol_from_marker_text("$200009$") == []


def test_marker_us_ticker_not_guessed():
    """美股字母词形（京东(JD)/Facebook(FB)）无 A/H canonical 语义，不猜。"""
    assert xss.symbol_from_marker_text("$京东(JD)$") == []
    assert xss.symbol_from_marker_text("$Facebook(FB)$") == []


def test_marker_multi_symbol_is_ambiguous():
    syms = xss.symbol_from_marker_text(
        "$美团点评-W(03690)$ 与 $腾讯控股(00700)$")
    assert len(syms) == 2
    assert xss.derive_symbol(
        "$美团点评-W(03690)$ 与 $腾讯控股(00700)$", None, None) is None


def test_derive_single_marker_row():
    assert xss.derive_symbol(
        "$融创中国(01918)$ 新房", None, None) == "01918"
    # title 无记号时看 desc
    assert xss.derive_symbol(
        "标题无记号", "正文 $康基医疗(09996)$ 详解", None) == "09996"


def test_derive_classified_fallback_only():
    """无记号行用关联表兜底；唯一 canonical 才回填。"""
    norm = json.dumps([
        {"raw": "600519", "kind": "stock_a", "canonical": "SH600519"}],
        ensure_ascii=False)
    assert xss.derive_symbol("标题", "正文无记号", norm) == "SH600519"


def test_derive_classified_multi_left_null():
    norm = json.dumps([
        {"raw": "01810", "kind": "stock_hk", "canonical": "01810"},
        {"raw": "600519", "kind": "stock_a", "canonical": "SH600519"}])
    assert xss.derive_symbol("标题", "正文", norm) is None


def test_derive_classified_index_only_left_null():
    """只命中指数词（无唯一股票语义）不回填。"""
    norm = json.dumps([
        {"raw": "沪深300指数", "kind": "index", "canonical": "000300.SH"}])
    assert xss.derive_symbol("标题", "正文", norm) is None


def test_derive_nothing_mappable_left_null():
    assert xss.derive_symbol("普通标题", "普通正文", None) is None
    assert xss.derive_symbol(None, None, "[]") is None


def test_marker_beats_classified_when_conflict():
    """记号可映射时优先记号（title→desc→classified 顺序）。"""
    norm = json.dumps([
        {"raw": "600519", "kind": "stock_a", "canonical": "SH600519"}])
    # title 记号是 01918，classified 是 SH600519 → 记号优先
    assert xss.derive_symbol("$融创中国(01918)$", None, norm) == "01918"


# ---------------------------------------------------------------------------
# ②③④⑤ 迁移（tmp_path 独立临时库）
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path):
    """仿生产两表 + 覆盖各形态的样本行。"""
    db = tmp_path / "xq_addsym.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""CREATE TABLE xq_status (
        status_id TEXT PRIMARY KEY, created_at TEXT, title TEXT, desc TEXT,
        user_id TEXT, target TEXT, view_count INTEGER, comment_count INTEGER,
        retweet_count INTEGER, like_count INTEGER, reward_count INTEGER,
        source TEXT, category TEXT, fetched_at TEXT)""")
    conn.execute("""CREATE TABLE xq_status_classified (
        status_id TEXT PRIMARY KEY, event_type TEXT, sentiment TEXT,
        tickers TEXT, confidence REAL, parse_ok INTEGER, model TEXT,
        prompt_version TEXT, labeled_at TEXT, content_type TEXT,
        retry_count INTEGER DEFAULT 0, norm_tickers TEXT)""")
    statuses = [
        ("s1", "$融创中国(01918)$ 新房价格倒挂"),          # → 01918 (marker)
        ("s2", "$贵州茅台(SH600519)$ 年报"),               # → SH600519 (marker)
        ("s3", "康基医疗IPO分析+申购策略"),                 # → 09996 (classified 兜底)
        ("s4", "双标的话题 $腾讯控股(00700)$ 与 $美团点评-W(03690)$"),  # 歧义 → NULL
        ("s5", "纯宏观讨论无记号"),                        # → NULL
        ("s6", "$京东(JD)$ 美股词形"),                     # 不可映射 → NULL
    ]
    conn.executemany(
        "INSERT INTO xq_status (status_id, title) VALUES (?, ?)", statuses)
    classified = [
        ("s3", None),  # norm_tickers NULL → 由下方 UPDATE 补兜底
        ("s5", "[]"),
        ("s6", None),  # 美股词形行无可用兜底 → 验证不可映射留 NULL
    ]
    conn.executemany(
        "INSERT INTO xq_status_classified (status_id, norm_tickers) "
        "VALUES (?, ?)", classified)
    # s3 的兜底 norm_tickers（title 无记号）
    conn.execute(
        "UPDATE xq_status_classified SET norm_tickers=? WHERE status_id='s3'",
        (json.dumps([
            {"raw": "09996", "kind": "stock_hk", "canonical": "09996"}]),
         ))
    conn.commit()
    conn.close()
    return db


def _cols(db):
    conn = sqlite3.connect(str(db))
    try:
        return [r[1] for r in conn.execute("PRAGMA table_info(xq_status)")]
    finally:
        conn.close()


def test_first_dry_run_does_not_touch_table(tmp_db):
    """首次 dry-run：不加列、不改字节、无 sidecar。"""
    before = tmp_db.read_bytes()
    report = xss.migrate(tmp_db, dry_run=True)
    assert report["dry_run"] is True
    assert report["column_exists"] is False
    assert report["sql"] == ["ALTER TABLE xq_status ADD COLUMN symbol TEXT"]
    assert tmp_db.read_bytes() == before
    assert _cols(tmp_db) == [
        "status_id", "created_at", "title", "desc", "user_id", "target",
        "view_count", "comment_count", "retweet_count", "like_count",
        "reward_count", "source", "category", "fetched_at"]
    assert not Path(str(tmp_db) + "-journal").exists()
    assert not Path(str(tmp_db) + "-wal").exists()


def test_dry_run_reports_expected_backfill(tmp_db):
    report = xss.migrate(tmp_db, dry_run=True)
    assert report["total"] == 6
    assert report["backfillable"] == 3   # s1 + s2（marker）+ s3（classified 兜底）
    assert report["from_marker"] == 2
    assert report["from_classified"] == 1
    assert report["unmappable_left_null"] >= 1
    # 未写库
    assert "symbol" not in _cols(tmp_db)


def test_execute_adds_column_and_backfills(tmp_db):
    report = xss.migrate(tmp_db, dry_run=False, execute=True)
    assert report["added_column"] is True
    assert "symbol" in _cols(tmp_db)
    conn = sqlite3.connect(str(tmp_db))
    try:
        got = dict(conn.execute(
            "SELECT status_id, symbol FROM xq_status").fetchall())
        assert got["s1"] == "01918"
        assert got["s2"] == "SH600519"
        assert got["s3"] == "09996"      # classified 兜底
        assert got["s4"] is None          # 歧义留 NULL
        assert got["s5"] is None          # 无可映射
        assert got["s6"] is None          # 美股词形不可映射
        # 原列零修改
        titles = dict(conn.execute(
            "SELECT status_id, title FROM xq_status").fetchall())
        assert titles["s1"] == "$融创中国(01918)$ 新房价格倒挂"
    finally:
        conn.close()


def test_second_execute_idempotent(tmp_db):
    xss.migrate(tmp_db, dry_run=False, execute=True)
    conn = sqlite3.connect(str(tmp_db))
    try:
        snapshot = conn.execute(
            "SELECT status_id, title, symbol FROM xq_status "
            "ORDER BY status_id").fetchall()
    finally:
        conn.close()
    report2 = xss.migrate(tmp_db, dry_run=False, execute=True)
    assert report2["added_column"] is False
    assert report2["updated"] == 0
    assert report2["skipped_unchanged"] == report2["backfillable"]
    conn = sqlite3.connect(str(tmp_db))
    try:
        after = conn.execute(
            "SELECT status_id, title, symbol FROM xq_status "
            "ORDER BY status_id").fetchall()
    finally:
        conn.close()
    assert after == snapshot
    # "already" 幂等打印（ensure_symbol_column 已存在返回 False）
    conn = sqlite3.connect(str(tmp_db))
    try:
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert xss.ensure_symbol_column(conn) is False
        assert "already" in buf.getvalue()
    finally:
        conn.close()


def test_dry_run_missing_database_fails_without_create(tmp_path):
    """缺失库只读模式显式失败且不新建任何文件。"""
    missing = tmp_path / "missing #%.db"
    with pytest.raises(sqlite3.OperationalError,
                       match="unable to open database file"):
        xss.migrate(missing, dry_run=True)
    assert not missing.exists()
    assert not any(Path(str(missing) + s).exists()
                   for s in ("-journal", "-wal", "-shm"))


def test_missing_table_errors_explicitly(tmp_path):
    db = tmp_path / "other.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE unrelated (id INTEGER)")
    conn.commit()
    conn.close()
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        xss.migrate(db, dry_run=True)


def test_verify_distribution(tmp_db):
    xss.migrate(tmp_db, dry_run=False, execute=True)
    dist = dict(xss.verify(tmp_db))
    assert dist == {"NULL": 3, "hk_bare": 2, "prefixed": 1}
