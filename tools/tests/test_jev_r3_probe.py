# -*- coding: utf-8 -*-
"""HIT-R3-20261107 只读试点探针单测：只读保证 + 分层指标 + 可复现性。"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import jev_r3_probe as probe  # noqa: E402


@pytest.fixture()
def mini_db(tmp_path):
    news = tmp_path / "news.db"
    audit = tmp_path / "audit.db"
    c = sqlite3.connect(news)
    c.execute("CREATE TABLE news (id TEXT PRIMARY KEY, source TEXT, title TEXT,"
              " content TEXT, jev TEXT, stocks TEXT)")
    c.executemany("INSERT INTO news VALUES (?,?,?,?,?,?)", [
        ("ann:1", "announcement", "[600095 湘财股份] 关于子公司收到处罚的公告",
         "湘财股份公告正文", json.dumps({"scope": "cn"}), None),
        ("ann:2", "announcement", "[002670 国盛证券] 关于行政处罚的公告",
         "国盛证券公告正文", json.dumps({"scope": "cn"}), None),
        ("ann:3", "announcement", "无前缀公告标题",
         "", json.dumps({"scope": "cn"}), None),
        ("sina:1", "sina", "贵州茅台发布三季度业绩，营收增长 12%",
         "贵州茅台正文", json.dumps({"scope": "cn"}), None),
        ("wsc:1", "wallstreetcn", "美债收益率上行，非农数据公布前市场观望",
         "", json.dumps({"scope": "macro"}), None),
        ("gold:1", "announcement", "[603284 林平发展] 林平发展公告",
         "", json.dumps({"scope": "cn"}), None),
    ])
    c.commit()
    c.close()
    c = sqlite3.connect(audit)
    c.execute("CREATE TABLE dec_audit (news_id TEXT, source TEXT, trade_day TEXT,"
              " bias TEXT, companies TEXT, hit INTEGER)")
    c.executemany("INSERT INTO dec_audit VALUES (?,?,?,?,?,?)", [
        ("ann:1", "announcement", "20261106", "bear", "[]", None),
        ("ann:2", "announcement", "20261106", "bear", "[]", None),
        ("ann:3", "announcement", "20261106", "neutral", "[]", None),
        ("sina:1", "sina", "20261106", "bull", "[]", None),
        ("wsc:1", "wallstreetcn", "20261106", "neutral", "[]", None),
        ("gold:1", "announcement", "20261106", "neutral", '["603284.SH"]', None),
    ])
    c.commit()
    c.close()
    return news, audit


def test_source_has_no_write_sql():
    import re
    src = Path(probe.__file__).read_text(encoding="utf-8")
    # 只匹配真实写语句形态（docstring 里提到这些词是允许的）
    forbidden = [r"INSERT\s+INTO", r"UPDATE\s+\w+\s+SET", r"DELETE\s+FROM",
                 r"CREATE\s+TABLE", r"DROP\s+TABLE", r"\.commit\(\)", r"--apply\s+\S"]
    for pattern in forbidden:
        assert not re.search(pattern, src, re.I), "write SQL found: " + pattern
    assert "PRAGMA query_only=1" in src
    assert "mode=ro" in src
    assert 'wrote_db_rows": 0' in src


def test_open_readonly_rejects_write(tmp_path):
    p = tmp_path / "ro.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE t (x INTEGER)")
    c.commit()
    c.close()
    con = probe.open_readonly(p)
    assert con.execute("PRAGMA query_only").fetchone()[0] == 1
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO t VALUES (1)")
    con.close()


def test_prefix_re_and_name_consistency():
    m = probe.PREFIX_RE.search("[600095 湘财股份] 湘财股份:关于处罚的公告")
    assert m and m.group(1) == "600095" and m.group(2) == "湘财股份"
    assert probe.PREFIX_RE.search("[ 688498   源杰科技 ] 公告").group(1) == "688498"
    assert probe.PREFIX_RE.search("没有方括号的标题") is None
    assert probe.name_consistent("湘财股份", "湘财股份") is True
    assert probe.name_consistent("亿通科技", "*ST亿通") is False
    assert probe.name_consistent("平安银行", "") is False


def test_symbol_conversions():
    assert probe.sym_to_ts("SH600095") == "600095.SH"
    assert probe.sym_to_ts("SZ002670") == "002670.SZ"
    assert probe.sym_to_ts("") == ""
    assert probe.guess_ts("603284") == "603284.SH"
    assert probe.guess_ts("000001") == "000001.SZ"
    assert probe.guess_ts("920285") == "920285.BJ"


def test_load_master_structure(tmp_path):
    p = tmp_path / "names.json"
    p.write_text(json.dumps({"920285.BJ": "远大健科", "600095.SH": "湘财股份"}),
                 encoding="utf-8")
    m = probe.load_master(p)
    assert m["n"] == 2
    assert m["code2ts"]["920285"] == "920285.BJ"
    assert m["code2name"]["600095"] == "湘财股份"


def test_sample_ids_deterministic():
    rows = list(range(30))
    assert probe.sample_ids(rows, 10, 42) == probe.sample_ids(rows, 10, 42)
    assert probe.sample_ids(rows, 10, 7) != probe.sample_ids(rows, 10, 42)
    assert probe.sample_ids(rows, 100, 42) == rows


def test_scope_of():
    assert probe.scope_of('{"scope": "cn"}') == "cn"
    assert probe.scope_of("{}") == "<none>"
    assert probe.scope_of("not-json") == "<bad_json>"


def test_s1_full_scan_on_mini_db(mini_db, monkeypatch):
    news_p, audit_p = mini_db
    master = {"n": 2,
              "code2ts": {"600095": "600095.SH", "002670": "002670.SZ"},
              "code2name": {"600095": "湘财股份", "002670": "国盛证券"}}
    monkeypatch.setattr(probe, "quote_availability",
                        lambda targets: {"targets_n": len(targets),
                                         "priced_rows": len(targets),
                                         "priced_unique_pairs": len(set(targets)),
                                         "stock_pairs": len(targets),
                                         "bench_days": 1})
    n = probe.open_readonly(news_p)
    a = probe.open_readonly(audit_p)
    try:
        r = probe.probe_s1(n, a, master)
    finally:
        n.close()
        a.close()
    assert r["total"] == 3
    assert r["prefix_parsed"] == 2
    assert r["master_hit"] == 2
    assert r["name_consistent"] == 2
    assert r["quote"]["priced_rows"] == 2
    assert r["quote"]["priced_unique_pairs"] == 2


def test_s2_classification_conserves_sample(mini_db):
    news_p, audit_p = mini_db
    n = probe.open_readonly(news_p)
    a = probe.open_readonly(audit_p)
    try:
        r = probe.probe_s2(n, a, 100, 42, None)
    finally:
        n.close()
        a.close()
    assert r["total"] == 2
    assert r["sampled"] == 2
    assert sum(r["classification"].values()) == 2
    cls = r["classification"]
    assert cls.get("exact_code", 0) + cls.get("no_entity", 0) <= 2
    assert set(r["scope_distribution"]) <= {"cn", "macro", "<none>", "<bad_json>"}


def test_quote_availability_counts_rows_not_pairs(monkeypatch):
    """回归：同一 (代码, 交易日) 多条公告必须按行计数，不能被 set 去重。"""
    import pyarrow as pa
    stock_tbl = pa.table({"ts_code": ["600095.SH"], "trade_date": ["20261106"],
                          "pct_chg": [1.2]})
    idx_tbl = pa.table({"ts_code": ["000300.SH"], "trade_date": ["20261106"],
                        "pct_chg": [0.3]})

    def fake_read(path, columns=None):
        return idx_tbl if "index_daily" in str(path) else stock_tbl

    monkeypatch.setattr(probe.pq, "read_table", fake_read)
    q = probe.quote_availability([("600095.SH", "20261106")] * 3)
    assert q["targets_n"] == 3
    assert q["priced_unique_pairs"] == 1
    assert q["priced_rows"] == 3


def test_gold_masked_replay_recovers_prefix_code(mini_db):
    news_p, audit_p = mini_db
    n = probe.open_readonly(news_p)
    a = probe.open_readonly(audit_p)
    try:
        r = probe.probe_gold(n, a, 100, 42)
    finally:
        n.close()
        a.close()
    assert r["total"] == 1 and r["sampled"] == 1
    assert r["tp"] == 1 and r["fp"] == 0 and r["fn"] == 0
    assert r["precision"] == 1.0 and r["recall"] == 1.0
    assert r["rows_exact_full_match"] == 1
