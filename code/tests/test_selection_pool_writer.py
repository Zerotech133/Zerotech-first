# -*- coding: utf-8 -*-
"""M3-4 选股池单一 writer 单测（SQLite 内存库，不触生产 DB）。

覆盖：
- feature flag 默认 legacy（no-op、不建连接）与 shadow/enabled 解析；
- 按市场最新日报去重（不同市场 pool_date 独立、旧日报不参与）；
- 同一市场多策略同 code 去重（max score、来源/信号去重排序）；
- 幂等先删后插（重复跑行数稳定、旧/陈旧行被清理）；
- 事务回滚（写入中途异常 → delete+insert 全部回滚，历史池行保留）；
- shadow 只读 diff 不写库；无日报市场跳过且不删除既有池行；
- 跨市场同日同 code 冲突 fail-closed（生产唯一键 (pool_date, code) 限制）；
- reports/picks 输入注入路径（不依赖数据库读）。
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from zhice_os_v2.application.services.selection_pool_writer import (
    ENV_MODE,
    MODE_ENABLED,
    MODE_LEGACY,
    MODE_SHADOW,
    PoolConflictError,
    SelectionPoolWriter,
    SelectionPoolWriterError,
    WriterModeError,
    build_pool_plan,
    resolve_mode,
)

D1 = date(2026, 10, 30)
D2 = date(2026, 10, 31)
D_US = date(2026, 10, 29)


# ---------------------------------------------------------------------------
# 隔离 SQLite 三表（与生产 selection 表关键列/唯一键对齐，selection 表无 ORM 模型）
# ---------------------------------------------------------------------------
@pytest.fixture()
def db():
    engine = sa.create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    meta = sa.MetaData()
    reports = sa.Table(
        "v2_selection_report", meta,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("market", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="submitted"),
    )
    picks = sa.Table(
        "v2_selection_pick", meta,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("market", sa.String(16), nullable=False, server_default="CN"),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False, server_default=""),
        sa.Column("signal_type", sa.String(32), nullable=False, server_default=""),
        sa.Column("score", sa.Numeric(6, 1), nullable=True),
        sa.UniqueConstraint("report_id", "code", name="uq_v2_selection_pick"),
    )
    pool = sa.Table(
        "v2_selection_pool", meta,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("pool_date", sa.Date(), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False, server_default=""),
        sa.Column("source_strategies", sa.Text(), nullable=False, server_default=""),
        sa.Column("signal_types", sa.Text(), nullable=False, server_default=""),
        sa.Column("max_score", sa.Numeric(6, 1), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="published"),
        sa.Column("market", sa.String(16), nullable=False, server_default="CN"),
        sa.UniqueConstraint("pool_date", "code", name="uq_v2_selection_pool_item"),
    )
    meta.create_all(engine)
    return SimpleNamespace(engine=engine, reports=reports, picks=picks, pool=pool)


def _add_report(db, report_date, strategy_id, market, status="submitted") -> int:
    with db.engine.begin() as conn:
        result = conn.execute(db.reports.insert().values(
            report_date=report_date, strategy_id=strategy_id,
            market=market, status=status))
        return int(result.inserted_primary_key[0])


def _add_pick(db, report_id, report_date, strategy_id, market, code,
              name="", signal_type="", score=None) -> None:
    with db.engine.begin() as conn:
        conn.execute(db.picks.insert().values(
            report_id=report_id, report_date=report_date, strategy_id=strategy_id,
            market=market, code=code, name=name, signal_type=signal_type, score=score))


def _pool_rows(db) -> list[dict]:
    with db.engine.connect() as conn:
        rows = conn.execute(sa.select(db.pool).order_by(
            db.pool.c.pool_date, db.pool.c.market, db.pool.c.code)).mappings().all()
    return [dict(row) for row in rows]


def _seed_pool(db, pool_date, market, code, name="seed", score=1.0) -> None:
    with db.engine.begin() as conn:
        conn.execute(db.pool.insert().values(
            pool_date=pool_date, market=market, code=code, name=name,
            source_strategies="seed", signal_types="seed",
            max_score=score, status="published"))


def _cn_fixture(db):
    """CN 两个策略同日（D2） + 旧日报 D1；US 独立 D_US。"""
    cn_old = _add_report(db, D1, "s-old", "CN")
    _add_pick(db, cn_old, D1, "s-old", "CN", "000001.SZ", "旧票", "RSI", 88.0)
    cn_a = _add_report(db, D2, "s-a", "CN")
    cn_b = _add_report(db, D2, "s-b", "CN")
    _add_pick(db, cn_a, D2, "s-a", "CN", "000001.SZ", "平安银行", "RSI", 60.0)
    _add_pick(db, cn_b, D2, "s-b", "CN", "000001.SZ", "平安银行B", "TREND", 82.5)
    _add_pick(db, cn_b, D2, "s-b", "CN", "600000.SH", "浦发银行", "TREND", 51.0)
    us = _add_report(db, D_US, "s-us", "US")
    _add_pick(db, us, D_US, "s-us", "US", "AAPL", "Apple", "MA", 70.0)
    return SimpleNamespace(cn_old_report=cn_old, cn_a=cn_a, cn_b=cn_b, us=us)


# ---------------------------------------------------------------------------
# mode / legacy
# ---------------------------------------------------------------------------
def test_default_mode_is_legacy_and_noop(monkeypatch):
    monkeypatch.delenv(ENV_MODE, raising=False)
    assert resolve_mode() == MODE_LEGACY
    writer = SelectionPoolWriter()  # 不传 engine：legacy 不允许建连接
    result = writer.write(reports=[{"id": 1, "report_date": D2, "market": "CN",
                                    "strategy_id": "s", "status": "submitted"}],
                          picks=[{"report_id": 1, "code": "A", "score": 1}])
    assert result["mode"] == MODE_LEGACY
    assert result["enabled"] is False and result["dry_run"] is True
    assert result["totals"]["inserted_rows"] == 0
    assert writer.mode == MODE_LEGACY


def test_mode_resolution_env_and_explicit(monkeypatch):
    monkeypatch.setenv(ENV_MODE, "shadow")
    assert resolve_mode() == MODE_SHADOW
    assert SelectionPoolWriter().mode == MODE_SHADOW
    assert SelectionPoolWriter(mode="enabled").mode == MODE_ENABLED
    monkeypatch.setenv(ENV_MODE, "bogus")
    assert resolve_mode() == MODE_LEGACY  # env 非法 fail-safe 回退
    with pytest.raises(WriterModeError):
        resolve_mode("bogus")


def test_legacy_with_engine_does_not_write(db):
    _seed_pool(db, D2, "CN", "OLD")
    writer = SelectionPoolWriter(engine=db.engine)  # 默认 legacy
    result = writer.write()
    assert result["enabled"] is False
    rows = _pool_rows(db)
    assert [row["code"] for row in rows] == ["OLD"]


# ---------------------------------------------------------------------------
# 计划构建 / 按市场最新日报去重
# ---------------------------------------------------------------------------
def test_per_market_latest_report_and_dedupe(db):
    _cn_fixture(db)
    writer = SelectionPoolWriter(engine=db.engine, mode=MODE_ENABLED)
    result = writer.write()

    assert result["enabled"] is True and result["dry_run"] is False
    markets = {item["market"]: item for item in result["markets"]}
    assert markets["CN"]["pool_date"] == D2.isoformat()
    assert markets["CN"]["planned_rows"] == 2
    assert markets["CN"]["inserted_rows"] == 2
    assert markets["US"]["pool_date"] == D_US.isoformat()
    assert markets["US"]["planned_rows"] == 1

    rows = _pool_rows(db)
    cn = {row["code"]: row for row in rows if row["market"] == "CN"}
    assert set(cn) == {"000001.SZ", "600000.SH"}
    merged = cn["000001.SZ"]
    assert float(merged["max_score"]) == pytest.approx(82.5)
    assert merged["source_strategies"] == "s-a,s-b"
    assert merged["signal_types"] == "RSI,TREND"
    assert merged["pool_date"].isoformat() == D2.isoformat()
    assert merged["status"] == "published"
    # 旧日报 D1 的票不参与
    assert "旧票" not in {row["name"] for row in rows}


def test_draft_report_excluded(db):
    draft = _add_report(db, D2, "s-draft", "CN", status="draft")
    _add_pick(db, draft, D2, "s-draft", "CN", "DRAFT", "草稿票", "X", 99.0)
    writer = SelectionPoolWriter(engine=db.engine, mode=MODE_ENABLED)
    result = writer.write(market="CN")
    assert result["markets"] == []
    assert _pool_rows(db) == []


def test_empty_latest_report_clears_current_pool_date(db):
    """最新日报无 picks 时，应清空该市场当前 pool_date 的旧池行；历史日期保留。"""
    _seed_pool(db, D2, "CN", "STALE")
    _seed_pool(db, D1, "CN", "HIST")
    _add_report(db, D2, "s-empty", "CN")  # 有日报但无 pick
    writer = SelectionPoolWriter(engine=db.engine, mode=MODE_ENABLED)
    result = writer.write(market="CN")
    assert result["markets"][0]["planned_rows"] == 0
    assert result["markets"][0]["deleted_rows"] == 1
    rows = _pool_rows(db)
    assert [(row["pool_date"].isoformat(), row["code"]) for row in rows] == [
        (D1.isoformat(), "HIST")]


def test_as_of_limits_report_date(db):
    _cn_fixture(db)
    writer = SelectionPoolWriter(engine=db.engine, mode=MODE_ENABLED)
    result = writer.write(market="CN", as_of=D1)
    assert result["markets"][0]["pool_date"] == D1.isoformat()
    rows = _pool_rows(db)
    assert [row["code"] for row in rows] == ["000001.SZ"]
    assert rows[0]["name"] == "旧票"


def test_inputs_path_without_db_reads(db):
    writer = SelectionPoolWriter(engine=db.engine, mode=MODE_ENABLED)
    result = writer.write(
        reports=[{"id": 1, "report_date": D2, "market": "CN",
                  "strategy_id": "s1", "status": "submitted"}],
        picks=[{"report_id": 1, "code": "INJ", "name": "注入票",
                "strategy_id": "s1", "signal_type": "RSI", "score": 66.0}],
        market="CN",
    )
    assert result["totals"]["inserted_rows"] == 1
    rows = _pool_rows(db)
    assert [(r["market"], r["code"], float(r["max_score"])) for r in rows] == [
        ("CN", "INJ", 66.0)]


def test_missing_market_report_skips_without_delete(db):
    _seed_pool(db, D2, "HK", "00005")
    writer = SelectionPoolWriter(engine=db.engine, mode=MODE_ENABLED)
    result = writer.write()
    assert result["markets"] == []  # 无任何日报 → 不动既有池
    rows = _pool_rows(db)
    assert [(row["market"], row["code"]) for row in rows] == [("HK", "00005")]


# ---------------------------------------------------------------------------
# 幂等 / 事务
# ---------------------------------------------------------------------------
def test_idempotent_delete_before_insert(db):
    _cn_fixture(db)
    _seed_pool(db, D2, "CN", "STALE")  # 同日同市场陈旧行
    writer = SelectionPoolWriter(engine=db.engine, mode=MODE_ENABLED)
    first = writer.write(market="CN")
    rows_after_first = _pool_rows(db)
    second = writer.write(market="CN")
    rows_after_second = _pool_rows(db)

    assert first["markets"][0]["deleted_rows"] == 1  # STALE 被删
    assert first["markets"][0]["inserted_rows"] == 2
    assert second["markets"][0]["deleted_rows"] == 2  # 幂等重写
    assert second["markets"][0]["inserted_rows"] == 2
    assert [row["code"] for row in rows_after_first] == [
        row["code"] for row in rows_after_second]
    assert "STALE" not in {row["code"] for row in rows_after_second}


def test_transaction_rollback_on_failure(db, monkeypatch):
    _cn_fixture(db)
    _seed_pool(db, D2, "CN", "KEEP-CN")
    _seed_pool(db, D_US, "US", "KEEP-US")
    before = _pool_rows(db)
    writer = SelectionPoolWriter(engine=db.engine, mode=MODE_ENABLED)

    def _boom(conn, plan):  # 模拟写入后校验失败，验证 delete+insert 整体回滚
        raise RuntimeError("simulated failure after writes")

    monkeypatch.setattr(writer, "_verify", _boom)
    with pytest.raises(RuntimeError, match="simulated failure"):
        writer.write()
    after = _pool_rows(db)
    assert [(r["market"], r["code"]) for r in after] == [
        (r["market"], r["code"]) for r in before]


def test_cross_market_code_conflict_fails_closed(db):
    _seed_pool(db, D2, "HK", "X")
    report = _add_report(db, D2, "s-cn", "CN")
    _add_pick(db, report, D2, "s-cn", "CN", "X", "冲突票", "RSI", 50.0)
    writer = SelectionPoolWriter(engine=db.engine, mode=MODE_ENABLED)
    with pytest.raises(PoolConflictError):
        writer.write()
    rows = _pool_rows(db)
    assert [(row["market"], row["code"]) for row in rows] == [("HK", "X")]


# ---------------------------------------------------------------------------
# shadow
# ---------------------------------------------------------------------------
def test_shadow_mode_diff_without_writes(db):
    _cn_fixture(db)
    _seed_pool(db, D2, "CN", "STALE")
    _seed_pool(db, D2, "CN", "000001.SZ", score=10.0)  # 已存在、应算 unchanged
    before = _pool_rows(db)
    writer = SelectionPoolWriter(engine=db.engine, mode=MODE_SHADOW)
    result = writer.write(market="CN")
    assert result["dry_run"] is True and result["enabled"] is False
    item = result["markets"][0]
    assert item["would_delete_rows"] == 1  # STALE
    assert item["would_insert_rows"] == 1  # 600000.SH
    assert item["unchanged_rows"] == 1  # 000001.SZ
    assert item["would_delete_preview"] == ["STALE"]
    assert _pool_rows(db) == before  # 不写库


# ---------------------------------------------------------------------------
# 计划纯函数（不触库）
# ---------------------------------------------------------------------------
def test_build_pool_plan_pure():
    plan = build_pool_plan(
        reports=[
            {"id": 1, "report_date": "2026-10-31", "strategy_id": "s1",
             "market": "CN", "status": "submitted"},
            {"id": 2, "report_date": "2026-10-30", "strategy_id": "s2",
             "market": "CN", "status": "submitted"},
        ],
        picks=[
            {"report_id": 1, "code": "A", "name": "甲", "strategy_id": "s1",
             "signal_type": "RSI", "score": 1},
            {"report_id": 2, "code": "OLD", "name": "旧", "strategy_id": "s2",
             "signal_type": "X", "score": 2},
        ],
        market="cn",
    )
    assert list(plan) == ["CN"]
    assert plan["CN"]["pool_date"].isoformat() == "2026-10-31"
    assert [row["code"] for row in plan["CN"]["rows"]] == ["A"]


def test_unknown_explicit_mode_raises():
    with pytest.raises(WriterModeError):
        SelectionPoolWriter(mode="not-a-mode")
    with pytest.raises(SelectionPoolWriterError):
        # 只给 reports 不给 picks 属调用方错误（enabled 模式才会检查）
        SelectionPoolWriter(mode=MODE_ENABLED).write(reports=[{"id": 1}])
