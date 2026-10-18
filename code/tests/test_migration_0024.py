"""Task 9：0024 NOT NULL 收紧门 TDD（migrations/versions/0024_enforce_trade_ownership.py）。

安全铁律（数据完整性最高优先）：
- upgrade 前置校验 `_assert_no_null_ownership(bind)`：五表（plan/approval/intent/
  position/replay）任一行 user_strategy_id 或 account_id 为 NULL 即 RuntimeError
  拒绝升级——绝不带 NULL 强设 NOT NULL；错误消息含表名与 NULL 计数，并指引先跑
  scripts/backfill_multi_user_ownership.py --apply + verify --strict；
- 纯约束收紧：不动任何数据；FK 与单列索引 0021 已建（fk_/ix_ 前缀），0024 不重复加；
- downgrade 对称放开（nullable=True），数据不动；
- 全部隔离 sqlite（ORM create_all 归属列天然 nullable；sqlite 不支持
  ALTER COLUMN，SET NOT NULL 本体由 dev 库 rehearsal 验证），这里用 fake op
  验证 upgrade() 的门控顺序与 5 表 × 2 列的对称完备性。
"""
from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.domain.ontology.objects import utc_now
from zhice_os_v2.infrastructure.db import models as orm  # noqa: F401
from zhice_os_v2.infrastructure.db.base import Base


# v2_trade_plan / v2_order_intent 用 BigInteger 主键；SQLite 仅 INTEGER PRIMARY KEY
# 走 rowid 自增，BigInteger 编译成 BIGINT 不自增 → 插入报 NOT NULL（与
# test_ownership_backfill.py 同 trick）：sqlite 方言下编译为 INTEGER。
@compiles(BigInteger, "sqlite")
def _compile_bigint_as_integer_sqlite(type_, compiler, **kw):  # noqa: ANN001, ANN202
    return "INTEGER"

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "0024_enforce_trade_ownership.py"
)


def _load_migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "migration_0024_enforce_trade_ownership", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIG = _load_migration_module()

DIRECT_TABLES = {
    "v2_trade_plan": orm.TradePlanModel,
    "v2_approval_request": orm.ApprovalRequestModel,
    "v2_order_intent": orm.OrderIntentModel,
    "v2_position": orm.PositionModel,
    "v2_replay": orm.ReplayModel,
}


# ---------------------------------------------------------------------------
# 种子与工具
# ---------------------------------------------------------------------------


def _new_engine() -> Engine:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def _add_plan(
    engine: Engine,
    plan_id: str,
    *,
    ustrat: str | None = None,
    account: str | None = None,
) -> None:
    now = utc_now()
    with sessionmaker(bind=engine)() as session:
        session.add(
            orm.TradePlanModel(
                plan_id=plan_id,
                candidate_id=f"cand-{plan_id}",
                strategy_version_id="sv-any",
                market="CN",
                ts_code="600519.SH",
                plan_status="EXECUTED",
                created_at=now,
                user_strategy_id=ustrat,
                account_id=account,
            )
        )
        session.commit()


def _add_position(
    engine: Engine,
    position_id: str,
    *,
    ustrat: str | None = None,
    account: str | None = None,
) -> None:
    now = utc_now()
    with sessionmaker(bind=engine)() as session:
        session.add(
            orm.PositionModel(
                position_id=position_id,
                strategy_version_id="sv-any",
                market="US",
                ts_code="AAPL",
                direction="LONG",
                open_qty=Decimal("10"),
                open_price=Decimal("100"),
                status="OPEN",
                opened_at=now,
                user_strategy_id=ustrat,
                account_id=account,
            )
        )
        session.commit()


class _RecordingOp:
    """fake alembic op：记录 alter_column 调用，get_bind 返回真 sqlite 连接。"""

    def __init__(self, bind: Connection) -> None:
        self._bind = bind
        self.calls: list[tuple[str, str, bool | None]] = []

    def get_bind(self) -> Connection:
        return self._bind

    def alter_column(
        self, table_name: str, column_name: str, nullable: bool | None = None, **_: object
    ) -> None:
        self.calls.append((table_name, column_name, nullable))


# ---------------------------------------------------------------------------
# 迁移链完整性
# ---------------------------------------------------------------------------


class TestRevisionChain:
    def test_revision_is_0024_on_top_of_0023(self):
        assert MIG.revision == "0024"
        assert MIG.down_revision == "0023"


# ---------------------------------------------------------------------------
# Step 1 RED：前置校验（有 NULL 拒绝 / 无 NULL 通过）
# ---------------------------------------------------------------------------


class TestAssertNoNullOwnership:
    def test_passes_on_empty_tables(self):
        engine = _new_engine()
        with engine.connect() as conn:
            MIG._assert_no_null_ownership(conn)  # 空库（含五表零行）必须直接通过

    def test_rejects_when_both_columns_null(self):
        engine = _new_engine()
        _add_plan(engine, "plan-null", ustrat=None, account=None)
        with engine.connect() as conn, pytest.raises(RuntimeError) as exc_info:
            MIG._assert_no_null_ownership(conn)
        message = str(exc_info.value)
        assert "v2_trade_plan" in message  # 错误必须含表名
        assert "1" in message  # 与 NULL 计数

    def test_rejects_when_only_one_column_null(self):
        engine = _new_engine()
        _add_plan(engine, "plan-half", ustrat="ustrat-x", account=None)
        with engine.connect() as conn, pytest.raises(RuntimeError):
            MIG._assert_no_null_ownership(conn)

    def test_rejects_lists_every_offending_table(self):
        engine = _new_engine()
        _add_plan(engine, "plan-null", ustrat=None, account=None)
        _add_position(engine, "pos-null", ustrat=None, account=None)
        with engine.connect() as conn, pytest.raises(RuntimeError) as exc_info:
            MIG._assert_no_null_ownership(conn)
        message = str(exc_info.value)
        assert "v2_trade_plan" in message and "v2_position" in message

    def test_error_points_to_backfill_script(self):
        engine = _new_engine()
        _add_plan(engine, "plan-null", ustrat=None, account=None)
        with engine.connect() as conn, pytest.raises(RuntimeError) as exc_info:
            MIG._assert_no_null_ownership(conn)
        assert "backfill_multi_user_ownership.py" in str(exc_info.value)

    def test_passes_when_all_rows_owned(self):
        engine = _new_engine()
        _add_plan(engine, "plan-owned", ustrat="ustrat-x", account="acct-x")
        _add_position(engine, "pos-owned", ustrat="ustrat-x", account="acct-x")
        with engine.connect() as conn:
            MIG._assert_no_null_ownership(conn)  # 全有归属 → 通过


# ---------------------------------------------------------------------------
# Step 2：upgrade 门控顺序 + 5 表 × 2 列对称完备（fake op；DDL 本体由 dev 库验证）
# ---------------------------------------------------------------------------


class TestUpgradeGateAndSymmetry:
    def test_upgrade_refuses_before_any_alter_when_nulls_remain(self, monkeypatch):
        engine = _new_engine()
        _add_plan(engine, "plan-null", ustrat=None, account=None)

        def _boom(_bind: Connection) -> None:
            raise RuntimeError("gate-refused")

        monkeypatch.setattr(MIG, "_assert_no_null_ownership", _boom)
        with engine.connect() as conn:
            recorder = _RecordingOp(conn)
            monkeypatch.setattr(MIG, "op", recorder)
            with pytest.raises(RuntimeError, match="gate-refused"):
                MIG.upgrade()
        assert recorder.calls == []  # 前置校验失败 → 绝不发任何 DDL

    def test_upgrade_alters_five_tables_both_columns_not_null(self, monkeypatch):
        engine = _new_engine()
        _add_plan(engine, "plan-ok", ustrat="ustrat-x", account="acct-x")
        with engine.connect() as conn:
            recorder = _RecordingOp(conn)
            monkeypatch.setattr(MIG, "op", recorder)
            MIG.upgrade()  # 前置校验（真 sqlite bind）通过后收紧
        assert len(recorder.calls) == 10  # 5 表 × 2 列，一个不多一个不少
        assert {(t, c) for t, c, _ in recorder.calls} == {
            (table, column)
            for table in DIRECT_TABLES
            for column in ("user_strategy_id", "account_id")
        }
        assert all(nullable is False for _, _, nullable in recorder.calls)

    def test_downgrade_relaxes_same_columns_symmetrically(self, monkeypatch):
        engine = _new_engine()
        with engine.connect() as conn:
            recorder = _RecordingOp(conn)
            monkeypatch.setattr(MIG, "op", recorder)
            MIG.downgrade()
        assert len(recorder.calls) == 10
        assert {(t, c) for t, c, _ in recorder.calls} == {
            (table, column)
            for table in DIRECT_TABLES
            for column in ("user_strategy_id", "account_id")
        }
        assert all(nullable is True for _, _, nullable in recorder.calls)
