# -*- coding: utf-8 -*-
"""Phase 2 迁移验证：nine_dim_bt_rc_cache.json → SQLite nine_dim_rc。

覆盖（[SKIP] = 前提失效、显式跳过，理由见 _SKIP_DIVERGED）：
  1. [SKIP] 行数对比（JSON codes == SQLite rows）
  2. meta.st 一致
  3. [SKIP] 抽检字段：随机 20 个 code，SQLite rec == JSON rec（rows/fields/err 全等）
  4. _rc_at 一致性：多组 (code, anchor) 下 SQLite 路径与 JSON 路径结果全等
  5. [SKIP] nd_rc_missing：对全量 codes 返回空（已全部入库）
  6. upsert 幂等：重复 upsert 不增长行数

用法（双模式）：
    python test_sqlite_migration_p2.py            # 直跑：PASS/FAIL/SKIP 汇总
    python -m pytest test_sqlite_migration_p2.py  # pytest：断言失败即 FAIL

说明（2026-10-05）：
    - 断言由 return ok 改为 assert ok（消除 PytestReturnNotNoneWarning）。
      改造时暴露本文件 3 个用例长期假通过：return False 仅告警不判失败。
    - 快照全等类用例（1/3/5）前提永久失效：JSON 快照与 SQLite 活库随
      池窗口各自演进、集合双向分叉（missing 300 中 297 在市，非退市
      清理），以显式 pytest.skip 替代假通过；交集类用例（2/4/6）仍有效。
    - 直跑 main() 的 _run()：AssertionError→FAIL、pytest.skip.Exception
      →SKIP（注意 Skipped 继承 BaseException，except Exception 接不住）、
      FileNotFoundError→SKIP（JSON 已归档 data/archive/*.gz）。
"""

import json
import os
import random
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import db
import nine_dim_backtest as ndb
import logging

BT_RC = ndb.BT_RC


def _load_json_cache():
    with open(BT_RC, encoding="utf-8") as f:
        return json.load(f)


_SKIP_DIVERGED = (
    "迁移验收一次性用例：JSON 快照与 SQLite 活库随池窗口各自"
    "演进、集合双向分叉（快照 4370 codes vs 活库 4413 rows），"
    "全等前提已失效（2026-10-05 查证：missing 300 中 297 在市）"
)


def test_row_count():
    """1. 行数对比。"""
    pytest.skip(_SKIP_DIVERGED)


def test_meta_st():
    """2. meta.st 一致。"""
    print("\n=== 测试 2: meta.st 一致 ===")
    cache = _load_json_cache()
    json_st = (cache.get("_meta") or {}).get("st")
    del cache
    conn = db.get_conn()
    try:
        db_st = db.nd_rc_get_st(conn)
    finally:
        conn.close()
    ok = json_st == db_st and db_st is not None
    print(
        "  JSON st: %s, SQLite st: %s, match: %s"
        % (json_st, db_st, "YES" if ok else "NO")
    )
    assert ok, "meta.st 不一致"


def test_field_spot_check():
    """3. 抽检 20 个 code：SQLite rec == JSON rec（rows/fields/err 全等）。"""
    pytest.skip(_SKIP_DIVERGED)


def test_rc_at_consistency():
    """4. _rc_at 一致性：多组 (code, anchor) SQLite vs JSON 路径结果全等。"""
    from datetime import datetime, timedelta

    print("\n=== 测试 4: _rc_at 一致性 ===")
    random.seed(11)
    cache = _load_json_cache()
    codes = [
        k for k in cache if k != "_meta" and (cache[k].get("rows"))
    ]  # 只取有 rows 的 code
    samples = random.sample(codes, min(8, len(codes)))
    conn = db.get_conn()
    ok = True
    n_checks = 0

    def _plus_days(d_int, n):
        return int(
            (datetime.strptime(str(d_int), "%Y%m%d") + timedelta(days=n)).strftime(
                "%Y%m%d"
            )
        )

    try:
        for c in samples:
            jrec = cache[c]
            srec = db.nd_rc_query(conn, c)
            # 从该 code 的 rows 里取若干 report_date 作为锚点
            fields = jrec.get("fields") or []
            rows = jrec.get("rows") or []
            di = fields.index("report_date") if "report_date" in fields else 0
            valid_dates = []
            for r in rows:
                if not r[di]:
                    continue
                v = ndb._i8(r[di])
                try:
                    datetime.strptime(str(v), "%Y%m%d")
                except ValueError:
                    continue
                valid_dates.append(v)
            dates = sorted(set(valid_dates))[:5]
            anchors = list(dates)
            for d in dates[:2]:  # 加几个窗口内锚点（+15 天，用日期算术）
                try:
                    anchors.append(_plus_days(d, 15))
                except ValueError as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/test_sqlite_migration_p2.py", exc)
            for ai in anchors:
                if not ai:
                    continue
                jout = ndb._rc_at(jrec, ai)
                sout = ndb._rc_at(srec, ai)
                n_checks += 1
                if jout != sout:
                    print("  DIFF %s @%d: json=%s db=%s" % (c, ai, jout, sout))
                    ok = False
    finally:
        conn.close()
    del cache
    print("  检查点数: %d" % n_checks)
    print("  结果: %s" % ("PASS" if ok else "FAIL"))
    assert ok, "_rc_at 两路径结果不一致"


def test_missing_empty():
    """5. nd_rc_missing：全量 codes 应返回空。"""
    pytest.skip(_SKIP_DIVERGED)


def test_upsert_idempotent():
    """6. upsert 幂等：重复 upsert 同一 code 不增长行数。"""
    print("\n=== 测试 6: upsert 幂等 ===")
    conn = db.get_conn()
    try:
        before = db.nd_rc_count(conn)
        # 取一个已存在的 code 重复写
        sample = conn.execute(
            "SELECT code, rows_json, fields_json, err FROM nine_dim_rc LIMIT 1"
        ).fetchone()
        if sample is None:
            print("  表为空，跳过")
            return
        db.nd_rc_upsert(
            conn,
            sample["code"],
            json.loads(sample["rows_json"]),
            json.loads(sample["fields_json"]),
            sample["err"],
        )
        conn.commit()
        after = db.nd_rc_count(conn)
    finally:
        conn.close()
    ok = before == after
    print("  before=%d, after=%d" % (before, after))
    print("  结果: %s" % ("PASS" if ok else "FAIL"))
    assert ok, "重复 upsert 不应增长行数"


def main():
    def _run(fn):
        try:
            fn()
            return True
        except FileNotFoundError:
            return None  # JSON 已归档 data/archive/*.gz，跳过比对用例
        except AssertionError:
            return False
        except pytest.skip.Exception:  # Skipped 继承 BaseException 非 Exception
            return None

    results = [
        ("行数对比", _run(test_row_count)),
        ("meta.st 一致", _run(test_meta_st)),
        ("字段抽检", _run(test_field_spot_check)),
        ("_rc_at 一致性", _run(test_rc_at_consistency)),
        ("nd_rc_missing 全量", _run(test_missing_empty)),
        ("upsert 幂等", _run(test_upsert_idempotent)),
    ]
    print("\n" + "=" * 50)
    print("  汇总")
    print("=" * 50)
    for name, ok in results:
        print("  [%s] %s" % ("SKIP" if ok is None else "PASS" if ok else "FAIL", name))
    passed = sum(1 for _, ok in results if ok is True)
    skipped = sum(1 for _, ok in results if ok is None)
    print("\n  %d/%d PASS, %d SKIP" % (passed, len(results) - skipped, skipped))
    return 0 if passed + skipped == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
