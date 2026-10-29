# -*- coding: utf-8 -*-
"""Phase 1 迁移验证：行数对比 + 抽检字段 + cross_poll 回归。

用法（双模式）：
    python test_sqlite_migration.py            # 直跑：逐项打印 + 汇总
    python -m pytest test_sqlite_migration.py  # pytest：断言失败即 FAIL

说明（2026-10-05）：
    - 断言由 return ok 改为 assert ok：消除 pytest
      PytestReturnNotNoneWarning，且 pytest 下失败真正判 FAIL。
    - 直跑模式由 main() 的 _ok() 包装：断言失败捕获为 FAIL。
    - 比对对象 chanlun_pool.jsonl 与 SQLite pool 表仍一致，全部用例有效
      （对比 Phase 2-4 的 nine_dim 快照比对已因池窗口演进分叉而 skip）。
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import db
import cross_poll
import logging

POOL_JSONL = os.path.join(HERE, "data", "chanlun_pool.jsonl")


def test_row_count():
    """1. 行数对比：JSONL == SQLite。"""
    print("=== 测试 1: 行数对比 ===")
    jsonl_count = 0
    with open(POOL_JSONL, encoding="utf-8") as f:
        for line in f:
            try:
                json.loads(line)
                jsonl_count += 1
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/test_sqlite_migration.py", exc)
    conn = db.get_conn()
    try:
        sqlite_count = db.pool_count(conn)
    finally:
        conn.close()
    ok = jsonl_count == sqlite_count
    print(
        "  JSONL: %d, SQLite: %d, match: %s"
        % (jsonl_count, sqlite_count, "YES" if ok else "NO")
    )
    assert ok, "JSONL 与 SQLite 行数不一致"


def test_field_spot_check():
    """2. 抽检字段：随机取 10 条对比 JSONL vs SQLite。"""
    print("\n=== 测试 2: 字段抽检（10 条）===")
    import random

    random.seed(42)
    jsonl_rows = []
    with open(POOL_JSONL, encoding="utf-8") as f:
        for line in f:
            try:
                jsonl_rows.append(json.loads(line))
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/test_sqlite_migration.py", exc)
    samples = random.sample(jsonl_rows, min(10, len(jsonl_rows)))
    conn = db.get_conn()
    ok = True
    try:
        for r in samples:
            db_row = db.pool_query_by_key(conn, r["code"], r["type"], r["dt"])
            if db_row is None:
                print("  MISS: %s %s %s" % (r["code"], r["type"], r["dt"]))
                ok = False
                continue
            # 对比关键字段
            for k in (
                "market",
                "code",
                "type",
                "dt",
                "price",
                "detect_date",
                "entry_px",
                "lag",
                "source",
            ):
                jv = r.get(k)
                sv = db_row.get(k)
                if jv != sv:
                    print(
                        "  DIFF %s: jsonl=%s db=%s (%s %s %s)"
                        % (k, jv, sv, r["code"], r["type"], r["dt"])
                    )
                    ok = False
            # bool 字段
            for k in ("bei", "bei2", "mbei", "tbe", "vv"):
                jv = r.get(k)
                sv = db_row.get(k)
                if (
                    bool(jv) != bool(sv)
                    if jv is not None
                    else (jv is not None) != (sv is not None)
                ):
                    print(
                        "  DIFF %s: jsonl=%s db=%s (%s %s %s)"
                        % (k, jv, sv, r["code"], r["type"], r["dt"])
                    )
                    ok = False
    finally:
        conn.close()
    print("  结果: %s" % ("PASS" if ok else "FAIL"))
    assert ok, "字段抽检不一致"


def test_cross_poll_sqlite():
    """3. cross_poll._src_chanlun 走 SQLite 路径。"""
    print("\n=== 测试 3: cross_poll SQLite 路径 ===")
    result = cross_poll._src_chanlun()
    ok = isinstance(result, dict) and len(result) > 0
    print("  返回标的数: %d" % len(result))
    if result:
        sample_code = next(iter(result))
        print("  样例: %s → %s" % (sample_code, result[sample_code]))
    print("  结果: %s" % ("PASS" if ok else "FAIL"))
    assert ok, "cross_poll SQLite 路径应返回非空 dict"


def test_cross_poll_consistency():
    """4. cross_poll SQLite vs JSONL 结果一致性。"""
    print("\n=== 测试 4: SQLite vs JSONL 一致性 ===")
    # 强制 SQLite 路径
    sqlite_result = cross_poll._src_chanlun()
    # 强制 JSONL 路径（临时禁用 SQLite）
    import db as _db

    orig_get_conn = _db.get_conn
    _db.get_conn = lambda *a, **kw: (_ for _ in ()).throw(
        Exception("test: force JSONL fallback")
    )
    try:
        jsonl_result = cross_poll._src_chanlun()
    finally:
        _db.get_conn = orig_get_conn
    # 对比
    sqlite_codes = set(sqlite_result.keys())
    jsonl_codes = set(jsonl_result.keys())
    only_sqlite = sqlite_codes - jsonl_codes
    only_jsonl = jsonl_codes - sqlite_codes
    diff_labels = []
    for code in sqlite_codes & jsonl_codes:
        if sqlite_result[code] != jsonl_result[code]:
            diff_labels.append(code)
    ok = not only_sqlite and not only_jsonl and not diff_labels
    print(
        "  SQLite 标的数: %d, JSONL 标的数: %d" % (len(sqlite_codes), len(jsonl_codes))
    )
    if only_sqlite:
        print("  仅 SQLite 有: %s" % sorted(only_sqlite)[:5])
    if only_jsonl:
        print("  仅 JSONL 有: %s" % sorted(only_jsonl)[:5])
    if diff_labels:
        print("  标签差异: %s" % diff_labels[:5])
    print("  结果: %s" % ("PASS" if ok else "FAIL"))
    assert ok, "SQLite 与 JSONL 路径结果不一致"


def test_insert_idempotent():
    """5. INSERT OR IGNORE 幂等：重复插入不增长行数。"""
    print("\n=== 测试 5: INSERT OR IGNORE 幂等 ===")
    conn = db.get_conn()
    try:
        before = db.pool_count(conn)
        # 重新迁移（应全部跳过）
        n = db.migrate_pool(verbose=False)
        after = db.pool_count(conn)
    finally:
        conn.close()
    ok = before == after and n == 0
    print("  before=%d, after=%d, re-inserted=%d" % (before, after, n))
    print("  结果: %s" % ("PASS" if ok else "FAIL"))
    assert ok, "INSERT OR IGNORE 重复插入应零增长"


def _ok(fn):
    """直跑模式汇总：断言失败转 FAIL（pytest 下无需此包装）。"""
    try:
        fn()
        return True
    except AssertionError:
        return False


def main():
    results = [
        ("行数对比", _ok(test_row_count)),
        ("字段抽检", _ok(test_field_spot_check)),
        ("cross_poll SQLite 路径", _ok(test_cross_poll_sqlite)),
        ("SQLite vs JSONL 一致性", _ok(test_cross_poll_consistency)),
        ("INSERT OR IGNORE 幂等", _ok(test_insert_idempotent)),
    ]
    print("\n" + "=" * 50)
    print("  汇总")
    print("=" * 50)
    for name, ok in results:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
    passed = sum(1 for _, ok in results if ok)
    print("\n  %d/%d PASS" % (passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
