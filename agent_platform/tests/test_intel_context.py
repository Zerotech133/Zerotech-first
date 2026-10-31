# -*- coding: utf-8 -*-
"""intel_context（reviewed JEV → N1 news_event）单测（WO-INTEL-N1AB / task-13）。

全隔离：tmp sqlite（自建 jev_review_queue schema），不依赖生产 news.db/8091/8796。
覆盖：flag 默认关、无数据 price_proxy+reason、DB 缺失/缺表 fail-visible、
reviewed 文本+event/review id、按 code 归属（stocks/ts_code/name/title/companies）、
坏行不炸、rejected 复核不冒充、limit/新→旧、只读连接（文件只读仍可读）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agent_platform import intel_context as IC  # noqa: E402

SCHEMA = """
CREATE TABLE jev_review_queue (
    id TEXT PRIMARY KEY,
    queued_at TEXT NOT NULL,
    conf REAL,
    bias TEXT,
    title TEXT,
    stocks TEXT,
    original_jev TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewer TEXT,
    reviewed_at TEXT,
    decision TEXT,
    note TEXT
)
"""


# ---------------------------------------------------------------- 夹具
@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """隔离 env：不让宿主 INTEL_TO_N1/INTEL_N1_DB 影响用例。"""
    for key in ("INTEL_TO_N1", "INTEL_N1_DB", "INTEL_N1_LIMIT",
                "INTEL_N1_STOCK_NAMES"):
        monkeypatch.delenv(key, raising=False)


def _jev(bias="bull", conf=0.62, event_cn="业绩", reason="测试摘要"):
    return json.dumps({
        "bias": bias, "bias_cn": {"bull": "看多", "bear": "看空",
                                  "neutral": "中性"}.get(bias, ""),
        "conf": conf, "event_type": "earnings", "event_cn": event_cn,
        "companies": [], "reason": reason,
    }, ensure_ascii=False)


def _row(nid, *, status="resolved", stocks=None, title="测试标题",
         original_jev=None, decision="correct", reviewer="N1",
         reviewed_at="2026-10-31 09:00", bias="bull", conf=0.62,
         queued_at="2026-10-30 17:00", note=""):
    return {
        "id": nid,
        "queued_at": queued_at,
        "conf": conf,
        "bias": bias,
        "title": title,
        "stocks": json.dumps(stocks if stocks is not None else ["600519.SH"],
                             ensure_ascii=False),
        "original_jev": original_jev if original_jev is not None else _jev(bias, conf),
        "status": status,
        "reviewer": reviewer,
        "reviewed_at": reviewed_at,
        "decision": decision,
        "note": note,
    }


def _make_db(tmp_path, rows, table=True) -> Path:
    db = tmp_path / "news.db"
    con = sqlite3.connect(str(db))
    try:
        if table:
            con.execute(SCHEMA)
            cols = ("id", "queued_at", "conf", "bias", "title", "stocks",
                    "original_jev", "status", "reviewer", "reviewed_at",
                    "decision", "note")
            for row in rows:
                con.execute(
                    "INSERT INTO jev_review_queue (%s) VALUES (%s)"
                    % (",".join(cols), ",".join("?" for _ in cols)),
                    [row[c] for c in cols])
        con.commit()
    finally:
        con.close()
    return db


# ---------------------------------------------------------------- flag
def test_flag_default_off_and_on_values():
    assert IC.enabled({}) is False
    assert IC.enabled({"INTEL_TO_N1": ""}) is False
    for off in ("0", "false", "off", "no"):
        assert IC.enabled({"INTEL_TO_N1": off}) is False, off
    for on in ("1", "true", "YES", "on", "y"):
        assert IC.enabled({"INTEL_TO_N1": on}) is True, on


# ---------------------------------------------------------------- 无数据 / 坏 DB
def test_no_reviewed_rows_returns_price_proxy(tmp_path):
    db = _make_db(tmp_path, [_row("p:1", status="pending")])
    res = IC.reviewed_news_event("600519", db_path=db)
    assert res["source"] == "price_proxy"
    assert res["reason"] == "no_reviewed_jev"
    assert res["text"] == "" and res["event_ids"] == []
    assert res["counts"]["scanned"] == 0          # WHERE status='resolved'
    assert res["bad_rows"] == []


def test_missing_db_price_proxy_fail_visible(tmp_path):
    missing = tmp_path / "nope.db"
    res = IC.reviewed_news_event("600519", db_path=missing)
    assert res["source"] == "price_proxy"
    assert res["reason"] == "news_db_missing"
    assert res["errors"] and res["errors"][0]["error"] == "news_db_missing"
    assert res["text"] == ""


def test_queue_table_missing_price_proxy_fail_visible(tmp_path):
    db = _make_db(tmp_path, [], table=False)
    res = IC.reviewed_news_event("600519", db_path=db)
    assert res["source"] == "price_proxy"
    assert res["reason"] == "queue_missing"
    assert res["errors"] and res["errors"][0]["error"] == "queue_missing"


# ---------------------------------------------------------------- reviewed 正常路径
def test_reviewed_row_generates_text_with_event_and_review_id(tmp_path):
    db = _make_db(tmp_path, [_row(
        "sina:5182001", stocks=["600519.SH"], title="600519 茅台三季报超预期",
        decision="correct", reviewer="N1许劭", reviewed_at="2026-10-31 09:10",
        note="已对照公告", bias="bull", conf=0.62)])
    res = IC.reviewed_news_event("600519", db_path=db)
    assert res["source"] == "reviewed_jev"
    assert res["reason"] == ""
    assert res["event_ids"] == ["sina:5182001"]
    assert res["review_ids"] and res["review_ids"][0].startswith("rv-")
    text = res["text"]
    assert "source=reviewed_jev" in text
    assert "sina:5182001" in text and res["review_ids"][0] in text
    assert "看多" in text and "N1许劭" in text
    assert "已对照公告" in text and "测试摘要" in text
    item = res["items"][0]
    assert item["event_id"] == "sina:5182001"
    assert item["review_id"] == IC.review_id(
        {"id": "sina:5182001", "reviewer": "N1许劭",
         "reviewed_at": "2026-10-31 09:10"})
    assert "stocks" in item["matched_by"]


def test_review_id_is_stable_and_derived(tmp_path):
    row = {"id": "a:1", "reviewer": "R", "reviewed_at": "2026-10-31 09:00"}
    assert IC.review_id(row) == IC.review_id(dict(row))
    assert IC.review_id(row).startswith("rv-")
    assert len(IC.review_id(row)) == len("rv-") + 12


# ---------------------------------------------------------------- code 归属
def test_code_matching_channels(tmp_path):
    rows = [
        _row("by:tscode", stocks=["600519.SH"]),
        _row("by:bare", stocks=["600519"]),
        _row("by:sh", stocks=["sh600519"]),
        _row("by:name", stocks=["贵州茅台"], title="贵州茅台公告"),
        _row("by:title", stocks=[], title="[600519] 茅台公告"),
        _row("by:company", stocks=[],
             original_jev=json.dumps({"bias": "bull", "conf": 0.6,
                                      "companies": ["贵州茅台"],
                                      "reason": "公司事件"},
                                     ensure_ascii=False)),
        _row("other", stocks=["000858.SZ"], title="五粮液公告"),
    ]
    db = _make_db(tmp_path, rows)
    res = IC.reviewed_news_event("600519", db_path=db,
                                 names={"贵州茅台"}, limit=20)
    ids = set(res["event_ids"])
    assert {"by:tscode", "by:bare", "by:sh", "by:name",
            "by:title", "by:company"} <= ids
    assert "other" not in ids
    by_id = {x["event_id"]: x for x in res["items"]}
    assert "name" in by_id["by:name"]["matched_by"] or \
        "stocks" in by_id["by:name"]["matched_by"]
    assert "title" in by_id["by:title"]["matched_by"]
    assert "companies" in by_id["by:company"]["matched_by"]


def test_norm_code_variants():
    for variant in ("600519", "sh600519", "600519.SH", " 600519 "):
        assert IC.norm_code(variant) == "600519"
    assert IC.norm_code("买入600519") == "600519"
    assert IC.norm_code("") == ""
    assert IC.norm_code("12345") == ""


def test_limit_and_newest_first(tmp_path):
    rows = [
        _row("old", reviewed_at="2026-10-29 09:00"),
        _row("mid", reviewed_at="2026-10-30 09:00"),
        _row("new", reviewed_at="2026-10-31 09:00"),
    ]
    db = _make_db(tmp_path, rows)
    res = IC.reviewed_news_event("600519", db_path=db, limit=2)
    assert res["event_ids"] == ["new", "mid"]


# ---------------------------------------------------------------- 坏行 / rejected
def test_bad_rows_fail_visible_but_not_crash(tmp_path):
    rows = [
        _row("good:1", stocks=["600519.SH"]),
        _row("bad:jev", original_jev="{not-json"),
        # 裸坏 JSON 文本（_row 会对入参再 dumps，固直接覆盖）
        dict(_row("bad:stocks", title="[600519] 标题仍可归属"),
             stocks="[not-json"),
        _row("bad:type", original_jev=json.dumps(["not", "dict"])),
    ]
    db = _make_db(tmp_path, rows)
    res = IC.reviewed_news_event("600519", db_path=db)  # 不抛
    assert res["source"] == "reviewed_jev"
    assert "good:1" in res["event_ids"]
    bad_ids = {x["id"] for x in res["bad_rows"]}
    assert {"bad:jev", "bad:type"} <= bad_ids
    assert {"bad:stocks"} <= bad_ids          # 坏 stocks 但 title 命中 → 可用+可见
    errors = {x["error"] for x in res["bad_rows"]}
    assert "bad_original_jev" in errors and "bad_stocks_json" in errors


def test_only_bad_rows_falls_back_with_visible_reason(tmp_path):
    rows = [
        _row("bad:1", original_jev="{oops"),
        _row("bad:2", original_jev="[]"),
    ]
    db = _make_db(tmp_path, rows)
    res = IC.reviewed_news_event("600519", db_path=db)  # 不抛
    assert res["source"] == "price_proxy"
    assert res["reason"] in ("no_reviewed_jev", "reviewed_unusable")
    assert res["text"] == ""
    assert len(res["bad_rows"]) == 2          # fail-visible


def test_rejected_reviews_do_not_fake_evidence(tmp_path):
    only_wrong = _make_db(tmp_path, [
        _row("rej:1", decision="wrong", note="JEV 判反")])
    res = IC.reviewed_news_event("600519", db_path=only_wrong)
    assert res["source"] == "price_proxy"
    assert res["reason"] == "only_rejected_reviews"
    assert res["rejected"] == 1

    mixed_dir = tmp_path / "mixed"
    mixed_dir.mkdir()
    rows = [_row("rej:2", decision="wrong"), _row("ok:1", decision="correct")]
    db2 = _make_db(mixed_dir, rows)
    res2 = IC.reviewed_news_event("600519", db_path=db2)
    assert res2["source"] == "reviewed_jev"
    assert res2["event_ids"] == ["ok:1"]
    assert res2["rejected"] == 1


def test_missing_bad_original_jev_still_uses_other_channels(tmp_path):
    """original_jev 坏的行走 bad_rows；同 code 的有效行仍可用（fail-visible 不炸）。"""
    rows = [_row("bad:jev", original_jev="not-json"), _row("ok:1")]
    db = _make_db(tmp_path, rows)
    res = IC.reviewed_news_event("600519", db_path=db)
    assert res["source"] == "reviewed_jev"
    assert res["event_ids"] == ["ok:1"]
    assert [x["id"] for x in res["bad_rows"]] == ["bad:jev"]


# ---------------------------------------------------------------- 只读纪律
def test_opens_db_readonly_and_never_writes(tmp_path):
    db = _make_db(tmp_path, [_row("ro:1")])
    before = db.read_bytes()
    try:
        os.chmod(str(db), 0o444)              # 只读文件仍须可读（mode=ro）
        res = IC.reviewed_news_event("600519", db_path=db)
        assert res["source"] == "reviewed_jev" and res["event_ids"] == ["ro:1"]
        assert db.read_bytes() == before      # 内容零改动
        assert not (tmp_path / "news.db-journal").exists()
    finally:
        os.chmod(str(db), 0o666)              # Windows tmp 清理需可写


# ---------------------------------------------------------------- schema 兼容
def test_minimal_schema_compatible(tmp_path):
    """旧/最小表结构（无 reviewer/decision/reviewed_at 列）也能只读生成文本。"""
    db = tmp_path / "min.db"
    con = sqlite3.connect(str(db))
    try:
        con.execute("CREATE TABLE jev_review_queue (id TEXT PRIMARY KEY, "
                    "queued_at TEXT, status TEXT, title TEXT, stocks TEXT, "
                    "original_jev TEXT)")
        con.execute("INSERT INTO jev_review_queue VALUES (?,?,?,?,?,?)", (
            "min:1", "2026-10-30 17:00", "resolved", "600519 最小表",
            json.dumps(["600519.SH"]), _jev()))
        con.commit()
    finally:
        con.close()
    res = IC.reviewed_news_event("600519", db_path=db)
    assert res["source"] == "reviewed_jev"
    assert res["event_ids"] == ["min:1"]
    assert "source=reviewed_jev" in res["text"]


# ---------------------------------------------------------------- CLI 探针
def test_cli_probe_json(tmp_path, capsys):
    db = _make_db(tmp_path, [_row("cli:1")])
    rc = IC.main(["--code", "600519", "--db", str(db), "--json"])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert rc == 0
    assert data["source"] == "reviewed_jev"
    assert data["event_ids"] == ["cli:1"]
    assert data["text"].startswith("[reviewed_jev]")


# ---------------------------------------------------------------- AI 初筛标注
def test_review_type_ai_first_pass_annotated_not_human(tmp_path):
    """review_type=ai_first_pass 必须在文本/返回中显式标注，不伪装人工复核。"""
    db = tmp_path / "rt.db"
    con = sqlite3.connect(str(db))
    try:
        con.execute(SCHEMA[:-2] + ",\n    review_type TEXT NOT NULL DEFAULT ''\n)")
        cols = ("id", "queued_at", "conf", "bias", "title", "stocks",
                "original_jev", "status", "reviewer", "reviewed_at",
                "decision", "note", "review_type")
        rows = [
            dict(_row("h:1", reviewer="老田", decision="correct",
                      reviewed_at="2026-10-31 09:10"),
                 review_type="human"),
            dict(_row("ai:1", reviewer="N1-AI", decision="correct",
                      reviewed_at="2026-10-31 09:20"),
                 review_type="ai_first_pass"),
        ]
        for row in rows:
            con.execute("INSERT INTO jev_review_queue (%s) VALUES (%s)"
                        % (",".join(cols), ",".join("?" for _ in cols)),
                        [row[c] for c in cols])
        con.commit()
    finally:
        con.close()
    res = IC.reviewed_news_event("600519", db_path=db)
    assert res["source"] == "reviewed_jev"
    by_id = {x["event_id"]: x for x in res["items"]}
    assert by_id["h:1"]["review_type"] == "human"
    assert by_id["ai:1"]["review_type"] == "ai_first_pass"
    assert res["review_type_counts"]["human"] == 1
    assert res["review_type_counts"]["ai_first_pass"] == 1
    text = res["text"]
    assert "review_type=ai_first_pass" in text
    assert "review_type=human" in text
    assert "AI 初筛" in text and "非人工复核" in text
    assert "不等同人工复核" in text


def test_legacy_empty_review_type_counts_human(tmp_path):
    """旧库/空 review_type：按历史人工口径计 human，不误标 AI。"""
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(str(db))
    try:
        con.execute(SCHEMA[:-2] + ",\n    review_type TEXT NOT NULL DEFAULT ''\n)")
        cols = ("id", "queued_at", "conf", "bias", "title", "stocks",
                "original_jev", "status", "reviewer", "reviewed_at",
                "decision", "note", "review_type")
        row = dict(_row("legacy:1", reviewer="老田"), review_type="")
        con.execute("INSERT INTO jev_review_queue (%s) VALUES (%s)"
                    % (",".join(cols), ",".join("?" for _ in cols)),
                    [row[c] for c in cols])
        con.commit()
    finally:
        con.close()
    res = IC.reviewed_news_event("600519", db_path=db)
    assert res["items"][0]["review_type"] == "human"
    assert res["review_type_counts"] == {"human": 1, "ai_first_pass": 0}
    assert "review_type=human" in res["text"]
    assert "review_type=ai_first_pass" not in res["text"]
    assert "AI 初筛" not in res["text"]

