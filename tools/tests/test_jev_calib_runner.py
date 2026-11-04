# -*- coding: utf-8 -*-
"""jev_calib_runner 单测（WO-JEV-OPS-20261103）。

覆盖：hit 覆盖率/方向命中率/分段计数 / 产物落盘（JSON+MD）/ 只读（db hash 不变）/
坏行 fail-visible（非法 hit、hit-ret 不一致）/ 缺表缺列 fail-visible / --date 校验。
全部用 tmp_path 临时 sqlite，不碰生产 jev_audit.db。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import jev_calib_runner as cr  # noqa: E402

AUDIT_DDL = """
CREATE TABLE dec_audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    news_id TEXT NOT NULL,
    ts TEXT,
    trade_day TEXT,
    source TEXT,
    bias TEXT,
    conf REAL,
    event_type TEXT,
    companies TEXT,
    model TEXT,
    prompt_version TEXT,
    ret REAL,
    bench REAL,
    excess REAL,
    hit INTEGER,
    posterior_at TEXT,
    updated_at TEXT,
    created_at TEXT,
    horizon TEXT,
    prob TEXT
)
"""

# news_id, hit, model, horizon, source, ret, posterior_at, ts, trade_day
ROWS = [
    ("n1", 1, "qwen3.5:9b", "days", "sina", 2.0, "2026-11-02 18:00",
     "2026-11-01 09:00", "20261102"),
    ("n2", 0, "qwen3.5:9b", "days", "sina", -1.0, "2026-11-02 18:00",
     "2026-11-01 10:00", "20261102"),
    ("n3", None, "qwen3.5:9b", "unknown", "eastmoney", None, None,
     "2026-10-31 09:00", "20261101"),
    ("n4", None, "jev-latest", "intraday", "wallstreetcn", None, None,
     "2026-11-02 09:00", "20261103"),
    ("n5", 1, "unknown", "unknown", "world", 0.3, "2026-11-02 18:00",
     "2026-10-30 09:00", "20261031"),
    ("nbad", 2, "qwen3.5:9b", "days", "sina", 0.5, None,
     "2026-11-01 11:00", "20261102"),
    ("nincons", 1, "qwen3.5:9b", "weeks", "announcement", None, None,
     "2026-11-01 12:00", "20261102"),
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _insert(conn, table: str, values: dict) -> None:
    cols = [str(r[1]) for r in conn.execute('PRAGMA table_info("%s")' % table)]
    keys = [k for k in values if k in cols]
    conn.execute(
        "INSERT INTO %s (%s) VALUES (%s)"
        % (table, ", ".join(keys), ", ".join(["?"] * len(keys))),
        [values[k] for k in keys])


def _make_db(tmp_path: Path, rows=ROWS, *, table=True,
             drop_col: str = "") -> Path:
    db = tmp_path / "jev_audit.db"
    conn = sqlite3.connect(db)
    try:
        if table:
            ddl = AUDIT_DDL
            if drop_col:
                ddl = "\n".join(line for line in AUDIT_DDL.splitlines()
                                if not line.strip().startswith(drop_col))
            conn.execute(ddl)
            fields = ("news_id", "hit", "model", "horizon", "source", "ret",
                      "posterior_at", "ts", "trade_day")
            for row in rows:
                _insert(conn, "dec_audit", dict(zip(fields, row)))
        conn.commit()
    finally:
        conn.close()
    return db


def _run_json(capsys, argv):
    rc = cr.main(argv)
    out = capsys.readouterr()
    assert rc == 0, "rc=%s stderr=%s" % (rc, out.err)
    return json.loads(out.out)


# --------------------------------------------------------------------------- #
# 核心指标
# --------------------------------------------------------------------------- #
def test_overall_and_segments(tmp_path, capsys):
    db = _make_db(tmp_path)
    digest = _sha256(db)
    out_dir = tmp_path / "outputs"
    rep = _run_json(capsys, ["--db", str(db), "--out-dir", str(out_dir),
                             "--date", "20261103", "--json"])

    o = rep["overall"]
    assert o["total"] == 7
    assert o["hit_available"] == 4          # n1/n2/n5/nincons
    assert o["hit_1"] == 3                  # n1/n5/nincons
    assert o["hit_0"] == 1                  # n2
    assert o["hit_missing"] == 2            # n3/n4
    assert o["hit_invalid"] == 1            # nbad
    assert o["hit_coverage"] == round(4 / 7, 4)
    assert o["direction_hit_rate"] == 0.75
    assert o["hit_missing_rate"] == round(2 / 7, 4)
    assert o["posterior_filled"] == 3

    model = {seg["name"]: seg for seg in rep["segments"]["model"]}
    assert model["qwen3.5:9b"]["n"] == 5
    assert model["qwen3.5:9b"]["labeled"] == 3
    assert model["qwen3.5:9b"]["null"] == 1
    assert model["qwen3.5:9b"]["invalid"] == 1
    assert model["qwen3.5:9b"]["hit_rate"] == round(2 / 3, 4)
    assert model["jev-latest"]["labeled"] == 0
    assert model["jev-latest"]["hit_rate"] is None
    assert model["unknown"]["hit_rate"] == 1.0
    horizon = {seg["name"]: seg for seg in rep["segments"]["horizon"]}
    assert horizon["days"]["n"] == 3
    source = {seg["name"]: seg for seg in rep["segments"]["source"]}
    assert source["sina"]["n"] == 3
    assert rep["ranges"]["ts_min"] == "2026-10-30 09:00"
    assert rep["ranges"]["trade_day_max"] == "20261103"
    # 只读：db 字节不变；产物落盘且可解析
    assert _sha256(db) == digest
    out_json = out_dir / "jev_calib_20261103.json"
    out_md = out_dir / "jev_calib_20261103.md"
    assert out_json.is_file() and out_md.is_file()
    doc = json.loads(out_json.read_text(encoding="utf-8"))
    assert doc["overall"]["direction_hit_rate"] == 0.75
    md = out_md.read_text(encoding="utf-8")
    assert "方向命中率（hit 可用样本）" in md
    assert "75.00%" in md
    assert "71.43%" in md or "57.14%" in md   # 覆盖率 4/7=57.14%
    assert not list(out_dir.glob("*.tmp"))


def test_human_text_and_samples(tmp_path, capsys):
    db = _make_db(tmp_path)
    out_dir = tmp_path / "outputs"
    rc = cr.main(["--db", str(db), "--out-dir", str(out_dir),
                  "--date", "20261103"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "hit 覆盖率 57.14%" in out
    assert "方向命中率（hit 可用样本）75.00%" in out
    assert "model 分段 Top" in out


# --------------------------------------------------------------------------- #
# 坏行 fail-visible
# --------------------------------------------------------------------------- #
def test_bad_rows_fail_visible(tmp_path, capsys):
    db = _make_db(tmp_path)
    digest = _sha256(db)
    out_dir = tmp_path / "outputs"
    rep = _run_json(capsys, ["--db", str(db), "--out-dir", str(out_dir),
                             "--date", "20261103", "--json"])
    bad = rep["bad_rows"]
    assert bad["n"] == 3                       # 1 非法 hit + 2 hit/ret 不一致
    assert bad["invalid_hit"] == 1
    assert bad["hit_ret_inconsistent"] == 2
    ids = {s["news_id"] for s in bad["samples_invalid_hit"]}
    assert "nbad" in ids
    kinds = {s["kind"] for s in bad["samples_hit_ret"]}
    assert "hit_labeled_ret_null" in kinds
    assert "hit_missing_ret_present" in kinds
    assert _sha256(db) == digest


def test_fail_on_bad_rc4(tmp_path, capsys):
    db = _make_db(tmp_path)
    rc = cr.main(["--db", str(db), "--out-dir", str(tmp_path / "o"),
                  "--date", "20261103", "--fail-on-bad"])
    out = capsys.readouterr()
    assert rc == 4
    assert "坏行" in out.err


def test_clean_db_bad_rows_zero(tmp_path, capsys):
    clean = [r for r in ROWS if r[0] not in ("nbad", "nincons")]
    db = _make_db(tmp_path, clean)
    rep = _run_json(capsys, ["--db", str(db), "--out-dir", str(tmp_path / "o"),
                             "--date", "20261103", "--json"])
    assert rep["bad_rows"]["n"] == 0
    assert rep["overall"]["hit_coverage"] == round(3 / 5, 4)
    assert rep["overall"]["direction_hit_rate"] == round(2 / 3, 4)


# --------------------------------------------------------------------------- #
# 前置错误 fail-visible
# --------------------------------------------------------------------------- #
def test_missing_table_fail_visible(tmp_path, capsys):
    db = sqlite3.connect(tmp_path / "empty.db")
    db.close()
    rc = cr.main(["--db", str(tmp_path / "empty.db")])
    err = capsys.readouterr().err
    assert rc == 2
    assert "缺表 dec_audit" in err


def test_missing_column_fail_visible(tmp_path, capsys):
    db = _make_db(tmp_path, drop_col="hit")
    rc = cr.main(["--db", str(db)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "缺列" in err and "hit" in err


def test_db_not_found_fail_visible(tmp_path, capsys):
    rc = cr.main(["--db", str(tmp_path / "nope.db")])
    err = capsys.readouterr().err
    assert rc == 2
    assert "DB 不存在" in err


# --------------------------------------------------------------------------- #
# 纯函数
# --------------------------------------------------------------------------- #
def test_classify_hit():
    assert cr.classify_hit(None) == "null"
    assert cr.classify_hit("") == "null"
    assert cr.classify_hit(1) == "hit"
    assert cr.classify_hit(0) == "miss"
    assert cr.classify_hit(2) == "invalid"
    assert cr.classify_hit(-1) == "invalid"
    assert cr.classify_hit("1") == "hit_coerced"
    assert cr.classify_hit("0") == "miss_coerced"
    assert cr.classify_hit("x") == "invalid"
    assert cr.classify_hit(True) == "invalid"


def test_date_arg_validation():
    assert cr._parse_date_arg("20261103") == "20261103"
    for bad in ("2026-11-03", "20261340", "abc", ""):
        try:
            cr._parse_date_arg(bad)
        except cr.PreconditionError:
            continue
        raise AssertionError("应拒绝非法日期: %r" % bad)
