# -*- coding: utf-8 -*-
"""荀攸 G4 只读工具集单测（读证据链 / 读后验）。

核心断言：调用后证据链账本字节数不变、后验统计输出文件字节不变——
只读性验收；全部内存/tmp 夹具，不碰 PG / 生产数据。
"""
import json
import os

import pytest

from strategy_layer import xunyou
from strategy_layer.xunyou import read_evidence_state, read_posterior, \
    replay_evidence_entry

DAY = "20261016"
SYMBOL = "002633.SZ"
ENTRY = "chan_monitor:%s:%s" % (DAY, SYMBOL)


def _snapshot(root):
    """目录内全部文件字节快照（含 -wal/-shm）。"""
    out = {}
    for name in sorted(os.listdir(root)):
        p = os.path.join(root, name)
        if os.path.isfile(p):
            out[name] = open(p, "rb").read()
    return out


@pytest.fixture()
def ev_db(tmp_path):
    """tmp 证据链库：一条 pool_in 记账（写入完成后关闭连接，WAL 落盘）。"""
    from evidence_chain import EvidenceChainStore

    db = str(tmp_path / "ev.db")
    store = EvidenceChainStore(db)
    store.transition(ENTRY, "pool_in",
                     {"symbol": SYMBOL, "trade_date": DAY,
                      "strategy": "chan_monitor", "action": "buy",
                      "score": 80.0, "rationale": "缠论1买",
                      "_meta": {"symbol": SYMBOL, "trade_date": DAY,
                                "strategy": "chan_monitor"}},
                     actor="test")
    store.close()
    return db


def test_read_evidence_state_filters_by_trade_date(ev_db):
    out = read_evidence_state(trade_date=DAY, db_path=ev_db)
    assert out["status"] == "OK" and out["count"] == 1
    row = out["states"][0]
    assert row["entry_id"] == ENTRY
    assert row["state"] == "pool_in"
    assert row["symbol"] == SYMBOL


def test_read_evidence_state_filter_miss(ev_db):
    out = read_evidence_state(trade_date="19990101", db_path=ev_db)
    assert out["status"] == "OK" and out["count"] == 0
    assert out["states"] == []


def test_read_evidence_state_degrades_when_db_missing(tmp_path):
    out = read_evidence_state(db_path=str(tmp_path / "nope.db"))
    assert out["status"] == "DEGRADED"
    assert out["reason"]


def test_replay_evidence_entry_returns_full_chain(ev_db):
    out = replay_evidence_entry(ENTRY, db_path=ev_db)
    assert out["status"] == "OK"
    assert out["state"]["state"] == "pool_in"
    assert [e["to_state"] for e in out["events"]] == ["pool_in"]
    assert out["events"][0]["to_state"] == "pool_in"
    assert json.loads(out["events"][0]["payload_json"])["rationale"] == "缠论1买"


def test_replay_evidence_entry_unknown_id_is_empty_ok(ev_db):
    out = replay_evidence_entry("ghost:%s:%s" % (DAY, SYMBOL), db_path=ev_db)
    assert out["status"] == "OK"
    assert out["state"] is None and out["events"] == []


def test_read_evidence_tools_leave_db_bytes_unchanged(ev_db):
    # 账本本体 = ev.db（append-only 事件 + 当前态投影）字节不变；
    # ro 连接可能创建 -wal/-shm 瞬态文件（WAL 库头惯例），但 wal 必须为空
    root = os.path.dirname(ev_db)
    before = _snapshot(root)
    assert read_evidence_state(trade_date=DAY, db_path=ev_db)["status"] == "OK"
    assert replay_evidence_entry(ENTRY, db_path=ev_db)["status"] == "OK"
    after = _snapshot(root)
    assert after["ev.db"] == before["ev.db"]
    assert after.get("ev.db-wal", b"") == b""


@pytest.fixture()
def posterior_file(tmp_path):
    """combined_verdicts.py write_out 同构的统计输出夹具。"""
    doc = {"generated_at": "2026-10-19 18:00:00",
           "summary": {"asof": "20261016", "events": 3,
                       "windows": {"1": {"n": 3, "hit": 66.7,
                                         "avg": 1.2, "med": 1.0}},
                       "by_strategy": {"kg_boost":
                                       {"1": {"n": 2, "hit": 50.0,
                                              "avg": 0.5, "med": 0.5}}},
                       "by_weight_group": {"kg_boost":
                                           {"ge075":
                                            {"1": {"n": 1, "hit": 100.0,
                                                   "avg": 2.0, "med": 2.0}}}},
                       "by_shadow": {"panic_buy":
                                     {"1": {"n": 1, "hit": 0.0,
                                            "avg": -1.0, "med": -1.0}}}},
           "per_code": {}}
    p = tmp_path / "combined_verdicts.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_read_posterior_returns_stat_blocks(posterior_file):
    out = read_posterior(posterior_file)
    assert out["status"] == "OK"
    assert out["asof"] == "20261016"
    assert out["windows"]["1"]["n"] == 3
    assert out["by_strategy"]["kg_boost"]["1"]["n"] == 2
    assert out["by_weight_group"]["kg_boost"]["ge075"]["1"]["hit"] == 100.0
    assert out["by_shadow"]["panic_buy"]["1"]["avg"] == -1.0


def test_read_posterior_missing_file_degrades(tmp_path):
    out = read_posterior(str(tmp_path / "nope.json"))
    assert out["status"] == "DEGRADED"


def test_read_posterior_malformed_degrades(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json", encoding="utf-8")
    assert read_posterior(str(p))["status"] == "DEGRADED"


def test_read_posterior_leaves_file_bytes_unchanged(posterior_file):
    before = _snapshot(os.path.dirname(posterior_file))
    assert read_posterior(posterior_file)["status"] == "OK"
    assert _snapshot(os.path.dirname(posterior_file)) == before


def test_report_letter_discipline_annotation():
    """纪律核对：汇报信只落盘返回路径，docstring 标注不直连飞书。"""
    doc = xunyou.write_upstream_report.__doc__ or ""
    assert "不直连飞书" in doc
