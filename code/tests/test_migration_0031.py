"""单元 C Task C-1：0031 支付发放状态机 schema + preflight + 回填 + 受限 downgrade TDD。

- upgrade preflight：subscription 订单 product_ref 孤儿(指向不存在策略)/为 NULL 即 RuntimeError，且在任何 DDL 前；
- 回填：granted_at 非空→granted；status=paid 且未 granted→manual_review + historical_grant_ambiguous
  + next_grant_retry_at=NULL；product_name_snapshot/market_snapshot 从策略(subscription)/固定名(membership)回填；
- downgrade 受限：存在 processing/retrying/failed/manual_review 或 historical_grant_ambiguous 即拒。
DDL 本体(FK/CHECK)由真实 PostgreSQL rehearsal 验证；此处验证 preflight/回填/downgrade 断言。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import create_engine

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "0031_payment_grant_status.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0031", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIG = _load()


def _old_order_table(conn):
    conn.exec_driver_sql(
        "CREATE TABLE v2_payment_order ("
        "id INTEGER PRIMARY KEY, order_id VARCHAR(64), user_id VARCHAR(64), "
        "product_type VARCHAR(16), product_ref VARCHAR(64), status VARCHAR(16), "
        "paid_at TIMESTAMP, granted_at TIMESTAMP)"
    )
    conn.exec_driver_sql(
        "CREATE TABLE v2_strategy (strategy_id VARCHAR(64) PRIMARY KEY, "
        "strategy_name VARCHAR(128), market VARCHAR(16))"
    )


def test_revision_is_0031_on_top_of_0030():
    assert MIG.revision == "0031"
    assert MIG.down_revision == "0030"


def test_preflight_rejects_orphan_subscription_product_ref():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _old_order_table(conn)
        conn.exec_driver_sql(
            "INSERT INTO v2_payment_order (id,order_id,user_id,product_type,product_ref,status) "
            "VALUES (1,'o-orphan','u','subscription','ghost-strat','paid')"
        )
        with pytest.raises(RuntimeError, match="o-orphan"):
            MIG._assert_upgrade_safe(conn)


def test_preflight_rejects_subscription_without_product_ref():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _old_order_table(conn)
        conn.exec_driver_sql(
            "INSERT INTO v2_payment_order (id,order_id,user_id,product_type,product_ref,status) "
            "VALUES (1,'o-noref','u','subscription',NULL,'paid')"
        )
        with pytest.raises(RuntimeError, match="o-noref"):
            MIG._assert_upgrade_safe(conn)


def test_preflight_passes_on_clean_data():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _old_order_table(conn)
        conn.exec_driver_sql("INSERT INTO v2_strategy VALUES ('s-1','缠论v1','CN')")
        conn.exec_driver_sql(
            "INSERT INTO v2_payment_order (id,order_id,user_id,product_type,product_ref,status) "
            "VALUES (1,'o-1','u','subscription','s-1','paid')"
        )
        conn.exec_driver_sql(
            "INSERT INTO v2_payment_order (id,order_id,user_id,product_type,product_ref,status) "
            "VALUES (2,'o-2','u','membership',NULL,'paid')"
        )
        MIG._assert_upgrade_safe(conn)  # 干净数据直接通过


def test_upgrade_refuses_before_any_ddl(monkeypatch):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _old_order_table(conn)
        conn.exec_driver_sql(
            "INSERT INTO v2_payment_order (id,order_id,user_id,product_type,product_ref,status) "
            "VALUES (1,'o-bad','u','subscription',NULL,'paid')"
        )

        class _Recorder:
            def __init__(self):
                self.calls = []

            def get_bind(self):
                return conn

            def __getattr__(self, name):
                def _rec(*a, **k):
                    self.calls.append(name)
                return _rec

        rec = _Recorder()
        monkeypatch.setattr(MIG, "op", rec)
        with pytest.raises(RuntimeError):
            MIG.upgrade()
        assert rec.calls == []  # preflight 失败 → 不发任何 DDL


def _new_order_table(conn):
    conn.exec_driver_sql(
        "CREATE TABLE v2_payment_order ("
        "id INTEGER PRIMARY KEY, order_id VARCHAR(64), user_id VARCHAR(64), "
        "product_type VARCHAR(16), product_ref VARCHAR(64), status VARCHAR(16), "
        "paid_at TIMESTAMP, granted_at TIMESTAMP, "
        "grant_status VARCHAR(16) DEFAULT 'pending', "
        "historical_grant_ambiguous BOOLEAN DEFAULT 0, "
        "next_grant_retry_at TIMESTAMP, "
        "product_name_snapshot VARCHAR(128) DEFAULT '', "
        "market_snapshot VARCHAR(16))"
    )
    conn.exec_driver_sql(
        "CREATE TABLE v2_strategy (strategy_id VARCHAR(64) PRIMARY KEY, "
        "strategy_name VARCHAR(128), market VARCHAR(16))"
    )


def test_backfill_granted_when_granted_at_present():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _new_order_table(conn)
        conn.exec_driver_sql("INSERT INTO v2_strategy VALUES ('s-1','缠论v1','CN')")
        conn.exec_driver_sql(
            "INSERT INTO v2_payment_order "
            "(id,order_id,user_id,product_type,product_ref,status,paid_at,granted_at) "
            "VALUES (1,'o-g','u','subscription','s-1','paid','2026-01-01','2026-01-02')"
        )
        MIG._backfill(conn)
        row = conn.exec_driver_sql(
            "SELECT grant_status,historical_grant_ambiguous,product_name_snapshot,market_snapshot "
            "FROM v2_payment_order WHERE order_id='o-g'"
        ).fetchone()
        assert row[0] == "granted"
        assert not row[1]
        assert row[2] == "缠论v1"
        assert row[3] == "CN"


def test_backfill_manual_review_when_paid_not_granted():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _new_order_table(conn)
        conn.exec_driver_sql(
            "INSERT INTO v2_payment_order "
            "(id,order_id,user_id,product_type,product_ref,status,paid_at,granted_at) "
            "VALUES (1,'o-amb','u','membership',NULL,'paid','2026-01-01',NULL)"
        )
        MIG._backfill(conn)
        row = conn.exec_driver_sql(
            "SELECT grant_status,historical_grant_ambiguous,next_grant_retry_at,product_name_snapshot "
            "FROM v2_payment_order WHERE order_id='o-amb'"
        ).fetchone()
        assert row[0] == "manual_review"
        assert row[1]
        assert row[2] is None
        assert row[3] == "membership"


def test_backfill_pending_when_not_paid():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _new_order_table(conn)
        conn.exec_driver_sql(
            "INSERT INTO v2_payment_order "
            "(id,order_id,user_id,product_type,product_ref,status) "
            "VALUES (1,'o-p','u','membership',NULL,'pending')"
        )
        MIG._backfill(conn)
        row = conn.exec_driver_sql(
            "SELECT grant_status,historical_grant_ambiguous FROM v2_payment_order WHERE order_id='o-p'"
        ).fetchone()
        assert row[0] == "pending"
        assert not row[1]


def _min_grant_table(conn):
    conn.exec_driver_sql(
        "CREATE TABLE v2_payment_order (id INTEGER PRIMARY KEY, order_id VARCHAR(64), "
        "grant_status VARCHAR(16), historical_grant_ambiguous BOOLEAN)"
    )


@pytest.mark.parametrize(
    ("grant_status", "ambiguous"),
    [("manual_review", 1), ("processing", 0), ("retrying", 0), ("failed", 0)],
)
def test_downgrade_refuses_lossy_state(grant_status, ambiguous):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _min_grant_table(conn)
        conn.exec_driver_sql(
            "INSERT INTO v2_payment_order (id,order_id,grant_status,historical_grant_ambiguous) "
            f"VALUES (1,'o','{grant_status}',{ambiguous})"
        )
        with pytest.raises(RuntimeError, match="0031"):
            MIG._assert_downgrade_safe(conn)


def test_downgrade_safe_when_only_pending_or_granted():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _min_grant_table(conn)
        conn.exec_driver_sql(
            "INSERT INTO v2_payment_order (id,order_id,grant_status,historical_grant_ambiguous) "
            "VALUES (1,'o1','pending',0)"
        )
        conn.exec_driver_sql(
            "INSERT INTO v2_payment_order (id,order_id,grant_status,historical_grant_ambiguous) "
            "VALUES (2,'o2','granted',0)"
        )
        MIG._assert_downgrade_safe(conn)  # 无 processing/失败/复核/歧义 → 允许
