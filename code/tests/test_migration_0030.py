"""单元 B Task B1-1：0030 权益/审计 schema + preflight + 受限 downgrade TDD。

- upgrade preflight（_assert_upgrade_safe）：历史 v2_membership/v2_strategy_subscription
  存在异常（member 缺到期 / free 带到期 / 未知 status）即 RuntimeError，且在任何 DDL 前；
- downgrade 受限（_assert_downgrade_safe）：存在永久/撤销记录即拒绝，绝不有损回退；
- v2_admin_operation 幂等唯一键 (actor_user_id, idempotency_key)。
DDL 本体（约束/新表）由真实 PostgreSQL rehearsal 验证；这里验证 preflight 与结构。
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
    / "0030_admin_entitlements.py"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0030", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIG = _load()


def _old_membership_table(conn):
    conn.exec_driver_sql(
        "CREATE TABLE v2_membership ("
        "id INTEGER PRIMARY KEY, user_id VARCHAR(64), tier VARCHAR(16), "
        "valid_until TIMESTAMP, status VARCHAR(16))"
    )
    conn.exec_driver_sql(
        "CREATE TABLE v2_strategy_subscription ("
        "id INTEGER PRIMARY KEY, user_id VARCHAR(64), strategy_id VARCHAR(64), "
        "valid_until TIMESTAMP, status VARCHAR(16))"
    )


def test_revision_is_0030_on_top_of_0029():
    assert MIG.revision == "0030"
    assert MIG.down_revision == "0029"


def test_upgrade_preflight_rejects_member_without_valid_until():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _old_membership_table(conn)
        conn.exec_driver_sql(
            "INSERT INTO v2_membership (id,user_id,tier,valid_until,status) "
            "VALUES (1,'u-bad','member',NULL,'active')"
        )
        with pytest.raises(RuntimeError, match=r"member_without_valid_until.*u-bad"):
            MIG._assert_upgrade_safe(conn)


def test_upgrade_preflight_rejects_free_with_valid_until():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _old_membership_table(conn)
        conn.exec_driver_sql(
            "INSERT INTO v2_membership (id,user_id,tier,valid_until,status) "
            "VALUES (1,'u-free','free','2027-01-01 00:00:00','active')"
        )
        with pytest.raises(RuntimeError, match=r"free_with_valid_until.*u-free"):
            MIG._assert_upgrade_safe(conn)


def test_upgrade_preflight_rejects_unknown_status():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _old_membership_table(conn)
        conn.exec_driver_sql(
            "INSERT INTO v2_membership (id,user_id,tier,valid_until,status) "
            "VALUES (1,'u-x','free',NULL,'weird')"
        )
        with pytest.raises(RuntimeError, match="unknown_status"):
            MIG._assert_upgrade_safe(conn)


def test_upgrade_preflight_passes_on_clean_data():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _old_membership_table(conn)
        conn.exec_driver_sql(
            "INSERT INTO v2_membership (id,user_id,tier,valid_until,status) "
            "VALUES (1,'u-ok','member','2027-01-01 00:00:00','active')"
        )
        conn.exec_driver_sql(
            "INSERT INTO v2_membership (id,user_id,tier,valid_until,status) "
            "VALUES (2,'u-free','free',NULL,'active')"
        )
        MIG._assert_upgrade_safe(conn)  # 干净数据直接通过


def test_upgrade_refuses_before_any_ddl(monkeypatch):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _old_membership_table(conn)
        conn.exec_driver_sql(
            "INSERT INTO v2_membership (id,user_id,tier,valid_until,status) "
            "VALUES (1,'u-bad','member',NULL,'active')"
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


def _new_membership_table(conn):
    conn.exec_driver_sql(
        "CREATE TABLE v2_membership ("
        "id INTEGER PRIMARY KEY, user_id VARCHAR(64), tier VARCHAR(16), "
        "valid_until TIMESTAMP, status VARCHAR(16), is_permanent BOOLEAN, "
        "revoked_at TIMESTAMP, revoked_by_user_id VARCHAR(64), version INTEGER)"
    )
    conn.exec_driver_sql(
        "CREATE TABLE v2_strategy_subscription ("
        "id INTEGER PRIMARY KEY, user_id VARCHAR(64), strategy_id VARCHAR(64), "
        "valid_until TIMESTAMP, status VARCHAR(16), is_permanent BOOLEAN, "
        "revoked_at TIMESTAMP, revoked_by_user_id VARCHAR(64), version INTEGER)"
    )


@pytest.mark.parametrize(
    ("col", "value"),
    [("is_permanent", "1"), ("revoked_at", "'2026-09-09 00:00:00'")],
)
def test_downgrade_refuses_lossy_state(col, value):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _new_membership_table(conn)
        conn.exec_driver_sql(
            f"INSERT INTO v2_membership (id,user_id,tier,{col}) "
            f"VALUES (1,'u','member',{value})"
        )
        with pytest.raises(RuntimeError, match="0030"):
            MIG._assert_downgrade_safe(conn)


def test_downgrade_safe_when_no_permanent_or_revoked():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        _new_membership_table(conn)
        conn.exec_driver_sql(
            "INSERT INTO v2_membership (id,user_id,tier,is_permanent,revoked_at) "
            "VALUES (1,'u','member',0,NULL)"
        )
        MIG._assert_downgrade_safe(conn)  # 无永久/撤销 → 允许


def test_admin_operation_unique_actor_key():
    from datetime import UTC, datetime

    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import sessionmaker

    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.base import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    now = datetime.now(UTC)
    s.add(orm.AdminOperationModel(
        operation_id="op-1", actor_user_id="a", idempotency_key="k1",
        request_fingerprint="fp", action="membership.set", target_user_id="t",
        response_status=200, response_body={}, created_at=now,
    ))
    s.commit()
    s.add(orm.AdminOperationModel(
        operation_id="op-2", actor_user_id="a", idempotency_key="k1",
        request_fingerprint="fp2", action="membership.set", target_user_id="t",
        response_status=200, response_body={}, created_at=now,
    ))
    with pytest.raises(IntegrityError):
        s.commit()
    s.close()
