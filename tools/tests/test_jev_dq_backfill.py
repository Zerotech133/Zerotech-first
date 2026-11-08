# -*- coding: utf-8 -*-
"""HIT-DQ-20261107 / V1' 回填执行器单测。

全部在 tmp_path 内构造独立 news.db / manifest / 分类文件，不触真源。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jev_dq_backfill as b  # noqa: E402


def _manifest(seqs):
    rows = [{"seq": s, "news_id": "ann:TEST%04d" % s, "trade_day": "20261001",
             "ts_code": "600095.SH",
             "companies_new": json.dumps(["600095.SH"], separators=(",", ":"))}
            for s in seqs]
    return {"count": len(rows),
            "sha256": hashlib.sha256(
                json.dumps(seqs, separators=(",", ":")).encode("ascii")).hexdigest(),
            "rows": rows}


@pytest.fixture
def fx(tmp_path):
    db = tmp_path / "news.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE news (id TEXT PRIMARY KEY, ts TEXT, stocks TEXT, jev TEXT)")
    # seq 1: 空数组 -> 可写
    con.execute("INSERT INTO news VALUES (?,?,?,?)",
                ("ann:TEST0001", "2026-10-01", "[]", json.dumps({"bias": "neutral"})))
    # seq 2: NULL -> 可写
    con.execute("INSERT INTO news VALUES (?,?,?,?)",
                ("ann:TEST0002", "2026-10-01", None, json.dumps({"bias": "bull"})))
    # seq 3: 非空 stocks -> 跳过
    con.execute("INSERT INTO news VALUES (?,?,?,?)",
                ("ann:TEST0003", "2026-10-01", '["600000.SH"]', "{}"))
    # seq 4: jev.companies 非空 -> 跳过
    con.execute("INSERT INTO news VALUES (?,?,?,?)",
                ("ann:TEST0004", "2026-10-01", "[]",
                 json.dumps({"bias": "bear", "companies": ["600001.SH"]})))
    # seq 5: news 中不存在 -> 跳过
    con.commit()
    con.close()

    el = _manifest([1, 2, 3, 4, 5, 6])
    (tmp_path / "manifest.json").write_text(
        json.dumps(el, ensure_ascii=False), encoding="utf-8")
    classified = [
        {"seq": 1, "upstream_has_codes": False},
        {"seq": 2, "upstream_has_codes": False},
        {"seq": 3, "upstream_has_codes": False},
        {"seq": 4, "upstream_has_codes": False},
        {"seq": 5, "upstream_has_codes": False},
        {"seq": 6, "upstream_has_codes": True},  # 排除出写入集
    ]
    (tmp_path / "rows.json").write_text(
        json.dumps(classified, ensure_ascii=False), encoding="utf-8")
    return {"db": db, "manifest": tmp_path / "manifest.json",
            "rows": tmp_path / "rows.json", "backup": tmp_path / "backup",
            "expected": 5, "el": el}


def _plan(f):
    el, plan = b.load_plan(f["manifest"], f["rows"], expected=f["expected"],
                           expected_excluded=1)
    assert el["count"] == 6
    assert len(plan) == f["expected"]
    assert {x["seq"] for x in plan} == {1, 2, 3, 4, 5}
    return plan


def _stocks(db, nid):
    con = sqlite3.connect("file:" + db.as_posix() + "?mode=ro", uri=True)
    try:
        return con.execute("SELECT stocks FROM news WHERE id=?", (nid,)).fetchone()
    finally:
        con.close()


def test_manifest_digest_rejects_tampering(fx):
    bad = dict(fx["el"])
    bad["sha256"] = "0" * 64
    (fx["manifest"]).write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(RuntimeError, match="sha256 mismatch"):
        b.load_plan(fx["manifest"], fx["rows"], expected=fx["expected"],
                    expected_excluded=1)


def test_write_set_size_guard(fx):
    with pytest.raises(RuntimeError, match="write set size"):
        b.load_plan(fx["manifest"], fx["rows"], expected=5497,
                    expected_excluded=1)


def test_classified_may_contain_rows_outside_manifest(fx):
    """回归：38_ 是全量 6,016 行分类，manifest 只是 5,514 子集；
    子集外的 has_codes 行不得被当成越界，也不得影响写入集。"""
    rows = json.loads(fx["rows"].read_text(encoding="utf-8"))
    rows.append({"seq": 999, "upstream_has_codes": True})
    rows.append({"seq": 998, "upstream_has_codes": False})
    fx["rows"].write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    el, plan = b.load_plan(fx["manifest"], fx["rows"], expected=5,
                           expected_excluded=1)
    assert len(plan) == 5
    assert el["_plan_meta"]["excluded_upstream_has_codes"] == 1
    assert el["_plan_meta"]["classified_rows"] == 8


def test_valid_target():
    assert b.valid_target('["600095.SH"]')
    assert b.valid_target('["920285.BJ"]')
    assert not b.valid_target('["中国平安"]')
    assert not b.valid_target('["600095.SH","000001.SZ"]')
    assert not b.valid_target("not-json")


def test_is_empty_stocks():
    assert b.is_empty_stocks(None)
    for v in ("", "[]", "[ ]", "[\n]"):
        assert b.is_empty_stocks(v)
    assert not b.is_empty_stocks('["600000.SH"]')
    assert not b.is_empty_stocks("not-json")


def test_dry_run_is_read_only(fx):
    plan = _plan(fx)
    before = [_stocks(fx["db"], "ann:TEST%04d" % s) for s in (1, 2, 3, 4)]
    stats = b.scan(fx["db"], plan)
    assert stats["plan"] == 5
    assert stats["would_write"] == 2
    assert stats["skip_nonempty_stocks"] == 1
    assert stats["skip_jev_companies"] == 1
    assert stats["skip_missing"] == 1
    assert stats["already_target"] == 0
    assert stats["integrity"] == "ok"
    after = [_stocks(fx["db"], "ann:TEST%04d" % s) for s in (1, 2, 3, 4)]
    assert before == after


def test_apply_requires_confirm_and_backup(fx):
    plan = _plan(fx)
    with pytest.raises(RuntimeError, match="--confirm"):
        b.apply_write(fx["db"], fx["backup"], plan, confirm=False)
    with pytest.raises(RuntimeError, match="backup missing"):
        b.apply_write(fx["db"], fx["backup"], plan, confirm=True)


def test_backup_snapshot_and_refuse_overwrite(fx):
    plan = _plan(fx)
    meta = b.make_backup(fx["db"], fx["backup"], plan)
    assert meta["backup_integrity"] == "ok"
    # fixture 故意让 seq=5 不存在（覆盖 skip_missing 分支），备份只如实记录
    assert meta["target_rows_found"] == 4
    assert meta["target_rows_stocks_empty"] == 3
    assert meta["target_rows_missing"] == 1
    assert meta["raw_byte_identical"] is True
    assert meta["expected_write_set"] == 5
    with pytest.raises(RuntimeError, match="refuse to overwrite"):
        b.make_backup(fx["db"], fx["backup"], plan)


def test_backup_all_targets_present(fx, tmp_path):
    """真实场景语义：5,497 行全部存在且 stocks 全空时，meta 应记录 missing=0。"""
    plan = _plan(fx)
    con = sqlite3.connect(str(fx["db"]))
    con.execute("INSERT INTO news VALUES (?,?,?,?)",
                ("ann:TEST0005", "2026-10-01", "[]", json.dumps({"bias": "neutral"})))
    con.commit()
    con.close()
    meta = b.make_backup(fx["db"], tmp_path / "backup_full", plan)
    assert meta["target_rows_found"] == 5
    assert meta["target_rows_missing"] == 0
    assert meta["target_rows_stocks_empty"] == 4  # seq3 非空
    assert meta["backup_integrity"] == "ok"


def test_apply_receipt_and_rollback(fx):
    plan = _plan(fx)
    b.make_backup(fx["db"], fx["backup"], plan)
    receipt = b.apply_write(fx["db"], fx["backup"], plan, confirm=True)
    assert receipt["stats"]["written"] == 2
    assert receipt["stats"]["skip_nonempty_stocks"] == 1
    assert receipt["stats"]["skip_jev_companies"] == 1
    assert receipt["stats"]["skip_missing"] == 1
    assert len(receipt["rows"]) == 2
    assert all(r["source"] == "prefix_parse" for r in receipt["rows"])
    assert all(r["before"] in (None, "[]") for r in receipt["rows"])
    assert all(r["after"] == '["600095.SH"]' for r in receipt["rows"])
    assert _stocks(fx["db"], "ann:TEST0001") == ('["600095.SH"]',)
    assert _stocks(fx["db"], "ann:TEST0002") == ('["600095.SH"]',)
    assert _stocks(fx["db"], "ann:TEST0003") == ('["600000.SH"]',)  # 未被覆盖
    assert (fx["backup"] / "receipt.json").exists()
    assert not (fx["backup"] / "receipt.json.tmp").exists()

    # 重复 apply 被拒
    with pytest.raises(RuntimeError, match="refuse duplicate apply"):
        b.apply_write(fx["db"], fx["backup"], plan, confirm=True)

    rb = b.rollback(fx["db"], fx["backup"])
    assert rb["stats"]["restored"] == 2
    assert _stocks(fx["db"], "ann:TEST0001") == ("[]",)
    assert _stocks(fx["db"], "ann:TEST0002") == (None,)
    assert _stocks(fx["db"], "ann:TEST0003") == ('["600000.SH"]',)


def test_rollback_skips_rows_changed_after_apply(fx):
    plan = _plan(fx)
    b.make_backup(fx["db"], fx["backup"], plan)
    b.apply_write(fx["db"], fx["backup"], plan, confirm=True)
    # 模拟采集服务在 apply 之后又改了其中一行
    con = sqlite3.connect(str(fx["db"]))
    con.execute("UPDATE news SET stocks=? WHERE id=?",
                ('["000001.SZ"]', "ann:TEST0001"))
    con.commit()
    con.close()
    rb = b.rollback(fx["db"], fx["backup"])
    assert rb["stats"]["restored"] == 1
    assert rb["stats"]["skip_changed_after_apply"] == 1
    assert _stocks(fx["db"], "ann:TEST0001") == ('["000001.SZ"]',)  # 未被回滚覆盖
    assert _stocks(fx["db"], "ann:TEST0002") == (None,)


def test_cli_dry_run_json_out(fx, tmp_path):
    out = tmp_path / "dry.json"
    rc = b.main(["--news-db", str(fx["db"]), "--manifest", str(fx["manifest"]),
                 "--rows", str(fx["rows"]), "--expected-write-set", "5",
                 "--expected-excluded", "1",
                 "--backup-dir", str(fx["backup"]), "--dry-run",
                 "--json-out", str(out)])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["mode"] == "dry-run"
    assert data["would_write"] == 2
    assert data["write_set"] == 5
