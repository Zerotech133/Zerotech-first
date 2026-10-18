"""aw_* 缺列自愈测试（ensure_aw_columns / create_aw_tables）。

背景：create_all 只建新表不补列，T06/T07 加 playbook_id 后生产旧表写库
UndefinedColumn 降级 JSONL（2026-10-18 实锤）。本文件用 sqlite 内存库
模拟「存量旧表」场景，验证自愈幂等补列且 ORM 可写。
"""
from __future__ import annotations

from sqlalchemy import create_engine, inspect as sa_inspect, text

from zhice_os_v2.agent_platform.db import create_aw_tables, ensure_aw_columns
from zhice_os_v2.agent_platform.models import AwDecisionModel


def _make_engine():
    return create_engine("sqlite://")


def _seed_legacy_aw_decision(engine) -> None:
    """手工建一个「缺 playbook_id 的旧版 aw_decision」，模拟存量生产库。"""
    cols = []
    for tname, table in [("aw_decision", AwDecisionModel)]:
        del tname, table
    legacy_cols = [
        "id INTEGER PRIMARY KEY AUTOINCREMENT",   # 模型主键列（真实 PG 库必在）
        "decision_id VARCHAR(64) UNIQUE",
        "ts_code VARCHAR(32)", "market VARCHAR(16)",
        "bar_time TIMESTAMP", "action VARCHAR(16)",
        "score_sell FLOAT", "score_hold FLOAT",
        "votes_json JSON", "regime_risk_off BOOLEAN",
        "arbitration_path VARCHAR(16)", "model_verdict JSON",
        "status VARCHAR(16)", "fwd_ret_5d FLOAT",
        "created_at TIMESTAMP",
    ]
    cols = ", ".join(legacy_cols)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE TABLE aw_decision ({cols})"))


def test_ensure_heals_legacy_missing_playbook_id():
    eng = _make_engine()
    _seed_legacy_aw_decision(eng)
    assert "playbook_id" not in {
        c["name"] for c in sa_inspect(eng).get_columns("aw_decision")}

    added = ensure_aw_columns(eng)
    assert "aw_decision.playbook_id" in added
    assert "playbook_id" in {
        c["name"] for c in sa_inspect(eng).get_columns("aw_decision")}


def test_ensure_is_idempotent_on_fresh_create_all():
    eng = _make_engine()
    create_aw_tables(eng)  # 全新库：create_all 建全表
    assert ensure_aw_columns(eng) == []  # 自愈应为零操作
    # 再跑一遍仍为零（幂等）
    assert ensure_aw_columns(eng) == []


def test_healed_table_accepts_orm_write_with_playbook_id():
    eng = _make_engine()
    _seed_legacy_aw_decision(eng)
    create_aw_tables(eng)  # 建其余表 + 自愈补列
    assert "playbook_id" in {
        c["name"] for c in sa_inspect(eng).get_columns("aw_decision")}

    from datetime import datetime, timezone
    from sqlalchemy.orm import Session
    row = AwDecisionModel(
        decision_id="aw-test-heal-001", ts_code="BTC/USDT", market="CRYPTO",
        action="WAIT", score_sell=0.1, score_hold=0.2, votes_json=[],
        regime_risk_off=False, arbitration_path="LLM",
        model_verdict={"source": "MOCK"}, status="ARCHIVED",
        playbook_id="pb_test",
        created_at=datetime(2026, 10, 18, tzinfo=timezone.utc),
    )
    with Session(eng) as sess:
        sess.add(row)
        sess.commit()
        got = sess.query(AwDecisionModel).filter_by(
            decision_id="aw-test-heal-001").one()
        assert got.playbook_id == "pb_test"


def test_create_aw_tables_returns_table_count_on_legacy_db():
    eng = _make_engine()
    _seed_legacy_aw_decision(eng)
    n = create_aw_tables(eng)  # 存量旧表不重建，其余新表建成
    assert n > 0
    assert "playbook_id" in {
        c["name"] for c in sa_inspect(eng).get_columns("aw_decision")}
