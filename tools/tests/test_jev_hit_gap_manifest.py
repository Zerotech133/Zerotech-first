# -*- coding: utf-8 -*-
"""jev_hit_gap_manifest 单测（task-11 / WO-JEV-OPS-20261103）。

覆盖：空 dec_audit、总体/基准日、model/方向/置信度/陈旧度分组、优先级排序、
--limit/--min-age-days、provenance（含缺失 id）、坏行 fail-visible、只读 hash、
缺表缺列 fail-visible、--json/--md/--out、JSON schema、回填路线 L2 标注。
全部用 tmp_path 临时 sqlite，不碰生产 jev_audit.db / news.db。
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

import jev_hit_gap_manifest as mf  # noqa: E402

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

NEWS_DDL = """
CREATE TABLE news (
    id TEXT PRIMARY KEY,
    source TEXT,
    ts TEXT,
    title TEXT,
    judged_at TEXT
)
"""

# news_id, hit, trade_day, model, bias, conf, source
AUDIT_ROWS = [
    ("a1", None, "20260921", "qwen3.5:9b", "bull", 0.9, "sina"),
    ("a2", None, "20261008", "unknown", "neutral", 0.8, "eastmoney"),
    ("a3", None, "20261012", "unknown", "bear", 0.6, "sina"),
    ("a4", None, "20261029", "jev-latest", "neutral", 0.7, "wallstreetcn"),
    ("a5", None, "20261102", "prefilter", "bull", 0.3, "survey"),
    ("a6", None, "20261104", "qwen3.5:9b", "bear", 0.75, "report"),
    ("a7", None, "bad-day", "qwen3.5:9b", "bull", 0.9, "sina"),
    ("a8", None, "20261104", "", "", None, ""),
    ("b1", 1, "20261104", "qwen3.5:9b", "bull", 0.8, "sina"),
    ("b2", 0, "20261103", "qwen3.5:9b", "bear", 0.7, "sina"),
    ("c1", 2, "20261103", "qwen3.5:9b", "bull", 0.8, "sina"),
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_full(tmp_path: Path) -> tuple[Path, Path]:
    audit_db = tmp_path / "jev_audit.db"
    conn = sqlite3.connect(audit_db)
    try:
        conn.execute(AUDIT_DDL)
        conn.executemany(
            "INSERT INTO dec_audit (news_id, hit, trade_day, model, bias, conf, "
            "source, ts) VALUES (?,?,?,?,?,?,?,?)",
            [row + ("2026-10-01 09:00",) for row in AUDIT_ROWS])
        conn.commit()
    finally:
        conn.close()
    news_db = tmp_path / "news.db"
    conn = sqlite3.connect(news_db)
    try:
        conn.execute(NEWS_DDL)
        for row in AUDIT_ROWS:
            nid = row[0]
            conn.execute(
                "INSERT INTO news (id, source, ts, title, judged_at) "
                "VALUES (?,?,?,?,?)",
                (nid, row[6] or "sina", "2026-10-01 09:00",
                 "title-" + nid, "2026-10-01 10:00"))
        conn.commit()
    finally:
        conn.close()
    return audit_db, news_db


def _make_empty(tmp_path: Path) -> tuple[Path, Path]:
    audit_db = tmp_path / "jev_audit.db"
    conn = sqlite3.connect(audit_db)
    try:
        conn.execute(AUDIT_DDL)
        conn.commit()
    finally:
        conn.close()
    news_db = tmp_path / "news.db"
    conn = sqlite3.connect(news_db)
    try:
        conn.execute(NEWS_DDL)
        conn.commit()
    finally:
        conn.close()
    return audit_db, news_db


def _argv(audit_db: Path, news_db: Path, *extra) -> list[str]:
    return ["--audit-db", str(audit_db), "--news-db", str(news_db)] + list(extra)


def _run_json(capsys, argv):
    rc = mf.main(argv)
    out = capsys.readouterr()
    assert rc == 0, "rc=%s stderr=%s" % (rc, out.err)
    return json.loads(out.out)


# --------------------------------------------------------------------------- #
# 1) 空库
# --------------------------------------------------------------------------- #
def test_empty_audit(tmp_path, capsys):
    audit, news = _make_empty(tmp_path)
    rep = _run_json(capsys, _argv(audit, news, "--json"))
    assert rep["overall"]["dec_audit_total"] == 0
    assert rep["overall"]["hit_null"] == 0
    assert rep["overall"]["hit_null_pct"] is None
    assert rep["priority_worklist"] == []
    assert rep["provenance"]["available"] is True
    assert rep["provenance"]["checked_ids"] == 0
    assert rep["bad_rows"]["n"] == 0
    # 文本/MD 模式也不崩
    assert mf.main(_argv(audit, news)) == 0
    capsys.readouterr()
    assert mf.main(_argv(audit, news, "--md")) == 0
    capsys.readouterr()


# --------------------------------------------------------------------------- #
# 2) 总体与基准
# --------------------------------------------------------------------------- #
def test_overall_and_reference(tmp_path, capsys):
    audit, news = _make_full(tmp_path)
    rep = _run_json(capsys, _argv(audit, news, "--json"))
    o = rep["overall"]
    assert o["dec_audit_total"] == 11
    assert o["hit_null"] == 8
    assert o["hit_labeled"] == 2
    assert o["hit_invalid"] == 1
    assert o["hit_null_pct"] == round(8 / 11, 4)
    assert o["hit_coverage"] == round(2 / 11, 4)
    assert o["distinct_news_ids"] == 8
    r = rep["reference"]
    assert r["max_trade_day"] == "20261104"
    assert r["oldest_trade_day"] == "20260921"
    assert r["oldest_age_days"] == 44


# --------------------------------------------------------------------------- #
# 3) 维度分组
# --------------------------------------------------------------------------- #
def test_dimensions(tmp_path, capsys):
    audit, news = _make_full(tmp_path)
    rep = _run_json(capsys, _argv(audit, news, "--json"))
    dims = rep["dimensions"]
    by_model = {d["name"]: d["n"] for d in dims["by_model"]}
    assert by_model == {"qwen3.5:9b": 2, "unknown": 2, "jev-latest": 1,
                        "prefilter": 1, "(unknown)": 1}
    by_bias = {d["name"]: d["n"] for d in dims["by_bias"]}
    assert by_bias == {"bull": 2, "neutral": 2, "bear": 2, "(unknown)": 1}
    by_conf = {d["bucket"]: d["n"] for d in dims["by_conf"]}
    assert by_conf["<0.5"] == 1
    assert by_conf["0.6-0.7"] == 1
    assert by_conf["0.7-0.8"] == 2
    assert by_conf["0.8-0.9"] == 1
    assert by_conf[">=0.9"] == 1
    assert by_conf["missing"] == 1
    by_stale = {d["staleness_bucket"]: (d["tier"], d["n"])
                for d in dims["by_staleness"]}
    assert by_stale == {"<1d": ("P4", 2), "1-3d": ("P3", 1),
                        "3-7d": ("P2", 1), "7-14d": ("P1", 0),
                        ">=14d": ("P0", 3)}
    by_trade_day = {d["trade_day"]: (d["n"], d["tier"], d["age_days"])
                    for d in dims["by_trade_day"]}
    assert by_trade_day["20260921"] == (1, "P0", 44)
    assert by_trade_day["20261104"] == (2, "P4", 0)


# --------------------------------------------------------------------------- #
# 4) 优先级排序 / limit / min-age
# --------------------------------------------------------------------------- #
def test_priority_order_limit_min_age(tmp_path, capsys):
    audit, news = _make_full(tmp_path)
    rep = _run_json(capsys, _argv(audit, news, "--json"))
    work = rep["priority_worklist"]
    assert [e["trade_day"] for e in work] == [
        "20260921", "20261008", "20261012", "20261029", "20261102",
        "20261104"]
    assert [e["priority"] for e in work] == ["P0", "P0", "P0", "P2", "P3",
                                            "P4"]
    assert [e["rank"] for e in work] == [1, 2, 3, 4, 5, 6]
    # limit
    rep2 = _run_json(capsys, _argv(audit, news, "--json", "--limit", "3"))
    assert len(rep2["priority_worklist"]) == 3
    assert rep2["priority_worklist"][0]["trade_day"] == "20260921"
    # min-age
    rep3 = _run_json(capsys, _argv(audit, news, "--json",
                                   "--min-age-days", "14"))
    assert [e["trade_day"] for e in rep3["priority_worklist"]] == [
        "20260921", "20261008", "20261012"]
    # sample 截断
    rep4 = _run_json(capsys, _argv(audit, news, "--json", "--sample", "1"))
    for entry in rep4["priority_worklist"]:
        assert len(entry["news_id_samples"]) <= 1


# --------------------------------------------------------------------------- #
# 5) provenance
# --------------------------------------------------------------------------- #
def test_provenance(tmp_path, capsys):
    audit, news = _make_full(tmp_path)
    rep = _run_json(capsys, _argv(audit, news, "--json"))
    p = rep["provenance"]
    assert p["available"] is True
    assert p["checked_ids"] == 8
    assert p["found_ids"] == 8
    assert p["missing_ids"] == 0
    assert p["coverage"] == 1.0
    first = rep["priority_worklist"][0]
    assert first["news_id_samples"][:1] == ["a1"]
    prov = first["provenance_sample"][0]
    assert prov["news_id"] == "a1"
    assert prov["source"] == "sina"
    assert prov["ts"] == "2026-10-01 09:00"
    assert prov["title"] == "title-a1"


def test_provenance_missing_ids(tmp_path, capsys):
    audit, news = _make_full(tmp_path)
    conn = sqlite3.connect(news)
    try:
        conn.execute("DELETE FROM news WHERE id='a2'")
        conn.commit()
    finally:
        conn.close()
    rep = _run_json(capsys, _argv(audit, news, "--json"))
    assert rep["provenance"]["missing_ids"] == 1
    assert rep["provenance"]["found_ids"] == 7
    assert rep["bad_rows"]["provenance_missing_ids"] == 1
    assert "a2" in rep["provenance"]["missing_samples"]
    # 坏行合计 = 非法 hit 1 + trade_day 非法 1 + provenance 缺失 1
    assert rep["bad_rows"]["n"] == 3


def test_news_db_missing_graceful(tmp_path, capsys):
    audit, _news = _make_full(tmp_path)
    missing = tmp_path / "nope.db"
    rc = mf.main(["--audit-db", str(audit), "--news-db", str(missing),
                  "--json"])
    out = capsys.readouterr()
    assert rc == 0, out.err
    rep = json.loads(out.out)
    assert rep["provenance"]["available"] is False
    assert any("news.db 不存在" in w for w in rep["warnings"])
    # --require-provenance fail-visible
    rc2 = mf.main(["--audit-db", str(audit), "--news-db", str(missing),
                   "--require-provenance"])
    err = capsys.readouterr().err
    assert rc2 == 2
    assert "news.db 不存在" in err


def test_no_provenance_flag(tmp_path, capsys):
    audit, news = _make_full(tmp_path)
    rep = _run_json(capsys, _argv(audit, news, "--json",
                                  "--no-provenance"))
    assert rep["provenance"]["available"] is False
    assert rep["provenance"]["checked_ids"] == 0


# --------------------------------------------------------------------------- #
# 6) 坏行 fail-visible
# --------------------------------------------------------------------------- #
def test_bad_rows_fail_visible(tmp_path, capsys):
    audit, news = _make_full(tmp_path)
    rep = _run_json(capsys, _argv(audit, news, "--json"))
    bad = rep["bad_rows"]
    assert bad["n"] == 2
    assert bad["invalid_hit"] == 1
    assert bad["invalid_trade_day"] == 1
    assert bad["invalid_conf"] == 0
    assert bad["samples"]["invalid_hit"][0]["news_id"] == "c1"
    assert bad["samples"]["invalid_trade_day"][0]["news_id"] == "a7"
    rc = mf.main(_argv(audit, news, "--fail-on-bad"))
    out = capsys.readouterr()
    assert rc == 4
    assert "坏行" in out.err


# --------------------------------------------------------------------------- #
# 7) 只读断言
# --------------------------------------------------------------------------- #
def test_read_only_hash(tmp_path, capsys):
    audit, news = _make_full(tmp_path)
    audit_hash = _sha256(audit)
    news_hash = _sha256(news)
    out_file = tmp_path / "manifest.md"
    rc = mf.main(_argv(audit, news, "--md", "--out", str(out_file)))
    capsys.readouterr()
    assert rc == 0
    assert out_file.is_file()
    assert not list(tmp_path.glob("*.tmp"))
    assert _sha256(audit) == audit_hash
    assert _sha256(news) == news_hash
    # json 模式同样只读
    assert mf.main(_argv(audit, news, "--json")) == 0
    capsys.readouterr()
    assert _sha256(audit) == audit_hash
    assert _sha256(news) == news_hash


# --------------------------------------------------------------------------- #
# 8) 缺表 / 缺列 fail-visible
# --------------------------------------------------------------------------- #
def test_missing_audit_table(tmp_path, capsys):
    empty = tmp_path / "empty.db"
    conn = sqlite3.connect(empty)
    conn.close()
    rc = mf.main(["--audit-db", str(empty), "--no-provenance"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "缺表 dec_audit" in err


def test_missing_audit_column(tmp_path, capsys):
    db = tmp_path / "bad.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE TABLE dec_audit (news_id TEXT, hit INTEGER, "
                     "trade_day TEXT)")
        conn.commit()
    finally:
        conn.close()
    rc = mf.main(["--audit-db", str(db), "--no-provenance"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "缺列" in err


def test_missing_news_table(tmp_path, capsys):
    audit, _news = _make_full(tmp_path)
    bad_news = tmp_path / "bad_news.db"
    conn = sqlite3.connect(bad_news)
    try:
        conn.execute("CREATE TABLE other (x TEXT)")
        conn.commit()
    finally:
        conn.close()
    rc = mf.main(["--audit-db", str(audit), "--news-db", str(bad_news),
                  "--require-provenance"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "news.db" in err or "缺表 news" in err


# --------------------------------------------------------------------------- #
# 9) 输出模式 / 参数 / schema
# --------------------------------------------------------------------------- #
def test_json_md_conflict_and_params(tmp_path, capsys):
    audit, news = _make_full(tmp_path)
    assert mf.main(_argv(audit, news, "--json", "--md")) == 2
    assert "--json 与 --md" in capsys.readouterr().err
    assert mf.main(_argv(audit, news, "--limit", "0")) == 2
    assert "--limit" in capsys.readouterr().err
    assert mf.main(_argv(audit, news, "--sample", "99")) == 2
    assert "--sample" in capsys.readouterr().err
    assert mf.main(_argv(audit, news, "--min-age-days", "-1")) == 2
    assert "--min-age-days" in capsys.readouterr().err
    assert mf.main(["--audit-db", str(tmp_path / "nope.db"),
                    "--no-provenance"]) == 2
    assert "audit DB 不存在" in capsys.readouterr().err


def test_md_output_content(tmp_path, capsys):
    audit, news = _make_full(tmp_path)
    out_file = tmp_path / "manifest.md"
    rc = mf.main(_argv(audit, news, "--md", "--out", str(out_file)))
    out = capsys.readouterr().out
    assert rc == 0
    text = out_file.read_text(encoding="utf-8")
    assert text == out
    assert "# JEV hit 空后验缺口优先清单" in text
    assert "## 三、优先 worklist" in text
    assert "## 五、provenance" in text
    assert "L2/另单" in text
    assert "| 1 | P0 | 20260921 | 44d | 1 |" in text


def test_json_schema(tmp_path, capsys):
    audit, news = _make_full(tmp_path)
    rep = _run_json(capsys, _argv(audit, news, "--json"))
    for key in ("tool", "schema_version", "generated_at", "read_only",
                "criteria", "reference", "overall", "provenance",
                "dimensions", "priority_worklist", "bad_rows", "fill_route",
                "warnings"):
        assert key in rep, key
    assert rep["tool"] == "jev_hit_gap_manifest"
    assert rep["read_only"] is True
    assert rep["fill_route"]["writes_db"] is True
    assert rep["fill_route"]["l2_required"] is True
    assert rep["fill_route"]["executed"] is False
    assert "L2/另单" in rep["fill_route"]["note"]
    for e in rep["priority_worklist"]:
        for key in ("rank", "priority", "trade_day", "age_days", "n",
                    "models", "biases", "conf", "news_id_samples",
                    "provenance_sample"):
            assert key in e, key
    assert rep["criteria"]["min_age_days"] == 0.0


def test_helpers():
    assert mf.parse_trade_day("20261104") is not None
    assert mf.parse_trade_day("bad-day") is None
    assert mf.parse_trade_day(None) is None
    assert mf.bucket_of(0.0) == "<1d"
    assert mf.bucket_of(1.0) == "1-3d"
    assert mf.bucket_of(3.0) == "3-7d"
    assert mf.bucket_of(7.0) == "7-14d"
    assert mf.bucket_of(14.0) == ">=14d"
    assert mf.TIER_OF[">=14d"] == "P0"
    assert mf.conf_bucket(None) == ("missing", "")
    assert mf.conf_bucket("abc") == ("missing", "invalid")
    assert mf.conf_bucket(0.9) == (">=0.9", "")
