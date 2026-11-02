"""迁移 0045：选股日报按市场独立、拆分 MULTI（SQLite 结构级 + 数据级）。

覆盖：
- revision 链（0044 → 0045）；
- upgrade 后唯一约束变为 (report_date, strategy_id, market)，同名兼容旧 ON CONFLICT；
- upgrade 后 CHECK 拒绝 market='MULTI'；
- MULTI / 跨市场日报按 v2_selection_pick.market 拆成多条，pick 关联保留；
- 无 pick 的 MULTI 日报按策略前缀回填市场；
- downgrade 合并回 MULTI + 还原旧唯一约束，数据不丢。
"""
from __future__ import annotations

import importlib.util
from datetime import date, datetime, UTC
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "0045_report_market.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0045", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIG = _load()

_NOW = datetime(2026, 10, 30, 16, 5, tzinfo=UTC)


def _setup_prereqs(conn) -> None:
    """建 0044 末态的最小 schema（report/pick 两表，含 market 列与旧唯一键）。"""
    conn.exec_driver_sql(
        "CREATE TABLE v2_selection_report ("
        "id INTEGER PRIMARY KEY, "
        "report_date DATE NOT NULL, "
        "strategy_id VARCHAR(64) NOT NULL, "
        "analyst VARCHAR(64) NOT NULL DEFAULT '', "
        "market_view TEXT NOT NULL DEFAULT '', "
        "yesterday_review TEXT NOT NULL DEFAULT '', "
        "lessons TEXT NOT NULL DEFAULT '', "
        "status VARCHAR(16) NOT NULL DEFAULT 'submitted', "
        "submitted_at DATETIME NOT NULL DEFAULT '2026-10-30 16:05:00', "
        "market VARCHAR(16) NOT NULL DEFAULT 'CN', "
        "CONSTRAINT uq_v2_selection_report UNIQUE (report_date, strategy_id))"
    )
    conn.exec_driver_sql(
        "CREATE INDEX ix_v2_selection_report_date ON v2_selection_report (report_date)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX ix_v2_selection_report_market ON v2_selection_report (market)"
    )
    conn.exec_driver_sql(
        "CREATE TABLE v2_selection_pick ("
        "id INTEGER PRIMARY KEY, "
        "report_id INTEGER NOT NULL, "
        "report_date DATE NOT NULL, "
        "strategy_id VARCHAR(64) NOT NULL, "
        "code VARCHAR(32) NOT NULL, "
        "name VARCHAR(128) NOT NULL DEFAULT '', "
        "signal_type VARCHAR(32) NOT NULL DEFAULT '', "
        "score NUMERIC(6, 1), "
        "reason TEXT NOT NULL DEFAULT '', "
        "market VARCHAR(16) NOT NULL DEFAULT 'CN', "
        "CONSTRAINT uq_v2_selection_pick UNIQUE (report_id, code))"
    )
    conn.exec_driver_sql(
        "CREATE INDEX ix_v2_selection_pick_date ON v2_selection_pick (report_date)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX ix_v2_selection_pick_market ON v2_selection_pick (market)"
    )


def _insert_report(conn, rid, report_date, strategy_id, market, market_view=""):
    conn.execute(
        sa.text(
            "INSERT INTO v2_selection_report (id, report_date, strategy_id, analyst, "
            "market_view, yesterday_review, lessons, status, submitted_at, market) "
            "VALUES (:id, :d, :s, 'STRAT1', :mv, '复盘', '教训', 'submitted', :now, :m)"
        ),
        {"id": rid, "d": report_date, "s": strategy_id, "mv": market_view,
         "now": _NOW, "m": market},
    )


def _insert_pick(conn, pid, rid, report_date, strategy_id, code, market):
    conn.execute(
        sa.text(
            "INSERT INTO v2_selection_pick (id, report_id, report_date, strategy_id, "
            "code, name, signal_type, score, reason, market) "
            "VALUES (:id, :rid, :d, :s, :c, :n, '买点', 80, '测试', :m)"
        ),
        {"id": pid, "rid": rid, "d": report_date, "s": strategy_id,
         "c": code, "n": code, "m": market},
    )


def _seed_legacy_data(conn) -> None:
    """1 条真跨市场 MULTI + 1 条 market 标错 + 2 条无 pick 的 MULTI。"""
    # 跨市场日报：qpzm 同一天选中 CN/HK/US/CRYPTO 四只
    _insert_report(conn, 1, date(2026, 10, 29), "qpzm", "MULTI", "跨市场观点")
    _insert_pick(conn, 11, 1, date(2026, 10, 29), "qpzm", "sh600000", "CN")
    _insert_pick(conn, 12, 1, date(2026, 10, 29), "qpzm", "rt_hk00700", "HK")
    _insert_pick(conn, 13, 1, date(2026, 10, 29), "qpzm", "gb_aapl", "US")
    _insert_pick(conn, 14, 1, date(2026, 10, 29), "qpzm", "BTC/USDT", "CRYPTO")
    # 单市场但 market 标错（旧 pipeline 先写默认 CN、回填前被读到）
    _insert_report(conn, 2, date(2026, 10, 29), "v1-us-strat-6", "CN", "美股观点")
    _insert_pick(conn, 21, 2, date(2026, 10, 29), "v1-us-strat-6", "gb_tsla", "US")
    # 无 pick 的 MULTI：按策略前缀回填 CRYPTO
    _insert_report(conn, 3, date(2026, 10, 30), "v1-crypto-strat-9", "MULTI", "加密观点")
    # 无 pick 的 MULTI：前缀无法判定 → 回退 CN
    _insert_report(conn, 4, date(2026, 10, 30), "dk", "MULTI", "研究态无选股")
    # 正常单市场日报，不应被改动
    _insert_report(conn, 5, date(2026, 10, 30), "chanlun", "CN", "缠论观点")


def _operations(conn) -> Operations:
    return Operations(MigrationContext.configure(conn))


def _upgrade(conn, monkeypatch) -> None:
    monkeypatch.setattr(MIG, "op", _operations(conn))
    MIG.upgrade()


def _downgrade(conn, monkeypatch) -> None:
    monkeypatch.setattr(MIG, "op", _operations(conn))
    MIG.downgrade()


def _report_rows(conn):
    return conn.execute(sa.text(
        "SELECT id, report_date, strategy_id, market, market_view FROM v2_selection_report "
        "ORDER BY report_date, strategy_id, market"
    )).mappings().all()


# --------------------------------------------------------------------------- #
# 基础
# --------------------------------------------------------------------------- #
def test_0045_revision_chain() -> None:
    assert MIG.revision == "0045"
    assert MIG.down_revision == "0044"


@pytest.mark.parametrize(
    ("value", "code", "strategy_id", "expected"),
    [
        ("CN", "", "", "CN"),
        ("cn", "", "", "CN"),
        ("MULTI", "", "", "CN"),
        ("", "sh600000", "", "CN"),
        ("", "sz000001", "v1-us-strat-6", "CN"),
        ("", "600000.SH", "", "CN"),
        ("", "rt_hk00700", "", "HK"),
        ("", "00700.HK", "", "HK"),
        ("", "gb_aapl", "", "US"),
        ("", "AAPL.US", "", "US"),
        ("", "BTC/USDT", "", "CRYPTO"),
        ("", "eth/usdc", "", "CRYPTO"),
        ("", "", "v1-hk-strat-7", "HK"),
        ("", "", "v1-crypto-strat-11", "CRYPTO"),
        ("", "not-a-code", "unknown", "CN"),
    ],
)
def test_0045_normalize_market(value, code, strategy_id, expected) -> None:
    assert MIG.normalize_market(value, code, strategy_id) == expected


# --------------------------------------------------------------------------- #
# upgrade
# --------------------------------------------------------------------------- #
def test_0045_upgrade_splits_multi_and_rebuilds_unique(monkeypatch) -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _setup_prereqs(conn)
        _seed_legacy_data(conn)
        _upgrade(conn, monkeypatch)

        # 唯一约束改为三列且沿用旧约束名（兼容 ON CONFLICT ON CONSTRAINT）
        uniques = {
            c["name"]: tuple(c["column_names"])
            for c in inspect(conn).get_unique_constraints("v2_selection_report")
        }
        assert uniques["uq_v2_selection_report"] == (
            "report_date", "strategy_id", "market")
        checks = {
            c["name"]: c["sqltext"]
            for c in inspect(conn).get_check_constraints("v2_selection_report")
        }
        assert "MULTI" in checks["ck_v2_selection_report_no_multi"]

        # 拆分结果：1 条跨市场日报 → 4 条按市场日报
        rows = _report_rows(conn)
        qpzm = [r for r in rows if r["strategy_id"] == "qpzm"]
        assert len(qpzm) == 4
        assert [r["market"] for r in qpzm] == ["CN", "CRYPTO", "HK", "US"]
        assert all(r["market_view"] == "跨市场观点" for r in qpzm)
        assert any(r["id"] == 1 for r in qpzm)  # 保留原日报 id

        # pick 关联：每个市场的 pick 挂到对应市场日报
        by_market = {r["market"]: r["id"] for r in qpzm}
        pick_parents = {
            row["code"]: (row["market"], row["report_id"])
            for row in conn.execute(sa.text(
                "SELECT code, market, report_id FROM v2_selection_pick "
                "WHERE strategy_id='qpzm'"
            )).mappings()
        }
        assert pick_parents == {
            "sh600000": ("CN", by_market["CN"]),
            "rt_hk00700": ("HK", by_market["HK"]),
            "gb_aapl": ("US", by_market["US"]),
            "BTC/USDT": ("CRYPTO", by_market["CRYPTO"]),
        }
        assert len(by_market) == 4

        # 单市场日报 market 被纠正
        us_report = conn.execute(sa.text(
            "SELECT id, market FROM v2_selection_report WHERE strategy_id='v1-us-strat-6'"
        )).mappings().one()
        assert us_report["market"] == "US"
        # 无 pick 的 MULTI 按策略前缀回填 / 回退 CN
        market_by_sid = {
            r["strategy_id"]: r["market"]
            for r in conn.execute(sa.text(
                "SELECT strategy_id, market FROM v2_selection_report"
            )).mappings()
        }
        assert market_by_sid["v1-crypto-strat-9"] == "CRYPTO"
        assert market_by_sid["dk"] == "CN"
        assert market_by_sid["chanlun"] == "CN"

        # 全库不再有 MULTI
        assert conn.execute(sa.text(
            "SELECT count(*) FROM v2_selection_report WHERE market='MULTI'"
        )).scalar_one() == 0
        # 原 pick 行数不变（只改 report_id / market）
        assert conn.execute(sa.text(
            "SELECT count(*) FROM v2_selection_pick"
        )).scalar_one() == 5


def test_0045_upgrade_constraints_allow_per_market_and_reject_multi(monkeypatch) -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _setup_prereqs(conn)
        _seed_legacy_data(conn)
        _upgrade(conn, monkeypatch)

        # 同 (date, strategy) 不同 market 允许
        _insert_report(conn, 90, date(2026, 10, 29), "chanlun", "HK")
        # 同 (date, strategy, market) 重复拒绝
        with pytest.raises(IntegrityError):
            _insert_report(conn, 91, date(2026, 10, 29), "chanlun", "HK")
        # MULTI 被 CHECK 拒绝
        with pytest.raises(IntegrityError):
            _insert_report(conn, 92, date(2026, 10, 31), "any", "MULTI")


# --------------------------------------------------------------------------- #
# downgrade
# --------------------------------------------------------------------------- #
def test_0045_downgrade_merges_back_to_multi_and_restores_unique(monkeypatch) -> None:
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _setup_prereqs(conn)
        _seed_legacy_data(conn)
        _upgrade(conn, monkeypatch)
        assert len(_report_rows(conn)) == 8  # 5 - 1 + 4

        _downgrade(conn, monkeypatch)

        # 旧唯一约束回来
        uniques = {
            c["name"]: tuple(c["column_names"])
            for c in inspect(conn).get_unique_constraints("v2_selection_report")
        }
        assert uniques["uq_v2_selection_report"] == ("report_date", "strategy_id")
        # CHECK 已删除
        assert not inspect(conn).get_check_constraints("v2_selection_report")

        # 多市场日报合并回 MULTI，pick 重新挂回保留行、不丢
        rows = _report_rows(conn)
        assert len(rows) == 5
        qpzm = [r for r in rows if r["strategy_id"] == "qpzm"][0]
        assert qpzm["market"] == "MULTI"
        assert conn.execute(sa.text(
            "SELECT count(*) FROM v2_selection_pick WHERE report_id=:r"
        ), {"r": qpzm["id"]}).scalar_one() == 4
        assert conn.execute(sa.text(
            "SELECT count(*) FROM v2_selection_pick"
        )).scalar_one() == 5
        # MULTI 再次可写（旧语义允许）
        _insert_report(conn, 93, date(2026, 10, 31), "any", "MULTI")
