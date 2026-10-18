"""Task 7：旧数据归属回填脚本 TDD（scripts/backfill_multi_user_ownership.py）。

安全铁律（数据迁移最高优先）：
- dry-run 默认 rollback-only，零写入（比对全库快照）；
- 幂等：重复 --apply 第二次 updated_total==0；
- 不可归属数据 → admin legacy-unassigned 占位，绝不映射到普通用户；
- 只 UPDATE 归属列 + INSERT 占位 account/instance，绝不 INSERT/DELETE 历史行；
- 全部经注入的隔离 sqlite session_factory 测试，绝不连真 PG（conftest 守卫兜底）。
"""
from __future__ import annotations

import json
import re
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import BigInteger, create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from backfill_multi_user_ownership import (  # noqa: E402
    BackfillError,
    OwnershipBackfill,
    main,
    resolve_admin_user_id,
)
from verify_multi_user_ownership import (  # noqa: E402
    CHECK_ORDER,
    OwnershipVerifier,
)
from verify_multi_user_ownership import (
    main as verify_main,
)

from zhice_os_v2.domain.ids import fit_id  # noqa: E402
from zhice_os_v2.domain.ontology.objects import utc_now  # noqa: E402
from zhice_os_v2.infrastructure.db import models as orm  # noqa: E402,F401
from zhice_os_v2.infrastructure.db.base import Base  # noqa: E402


# v2_trade_plan / v2_order_intent 等用 BigInteger 主键；SQLite 仅 INTEGER PRIMARY KEY
# 走 rowid 自增，BigInteger 编译成 BIGINT 不自增 → 插入报 NOT NULL（与
# test_execution_ownership.py 同 trick）：sqlite 方言下编译为 INTEGER。
@compiles(BigInteger, "sqlite")
def _compile_bigint_as_integer_sqlite(type_, compiler, **kw):  # noqa: ANN001, ANN202
    return "INTEGER"


ADMIN_ID = "u-admin"
ACCT_CN = fit_id("acct-legacy-", "CN")
ACCT_US = fit_id("acct-legacy-", "US")
USTRAT_CN1 = fit_id("ustrat-legacy-", "sv-cn-1:CN")
USTRAT_CN2 = fit_id("ustrat-legacy-", "sv-cn-2:CN")
USTRAT_US1 = fit_id("ustrat-legacy-", "sv-us-1:US")
USTRAT_UNASSIGNED_CN = fit_id("ustrat-legacy-", "unassigned:CN")

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

def _seed(session: object) -> None:
    now = utc_now()
    session.add(
        orm.UserModel(
            user_id=ADMIN_ID,
            username="admin",
            password_hash="x",
            role="ADMIN",
            is_active=True,
            locale="zh",
            created_at=now,
        )
    )
    session.add(
        orm.UserModel(
            user_id="u-bob",
            username="bob",
            password_hash="x",
            role="USER",
            is_active=True,
            locale="zh",
            created_at=now,
        )
    )
    session.add(
        orm.StrategyModel(
            strategy_id="stg-cn",
            strategy_name="cn-strat",
            market="CN",
            status="ACTIVE",
            created_at=now,
            updated_at=now,
        )
    )
    session.add(
        orm.StrategyModel(
            strategy_id="stg-us",
            strategy_name="us-strat",
            market="US",
            status="ACTIVE",
            created_at=now,
            updated_at=now,
        )
    )
    for sv_id, stg, market in [
        ("sv-cn-1", "stg-cn", "CN"),
        ("sv-cn-2", "stg-cn", "CN"),
        ("sv-us-1", "stg-us", "US"),
    ]:
        session.add(
            orm.StrategyVersionModel(
                strategy_version_id=sv_id,
                strategy_id=stg,
                version_no="v1",
                market=market,
                status="PUBLISHED",
                spec={},
                created_at=now,
            )
        )

    def _plan(plan_id: str, sv: str, market: str, *, owned: bool = False) -> None:
        session.add(
            orm.TradePlanModel(
                plan_id=plan_id,
                candidate_id=f"cand-{plan_id}",
                strategy_version_id=sv,
                market=market,
                ts_code="600519.SH",
                plan_status="EXECUTED",
                created_at=now,
                user_strategy_id="usr-existing" if owned else None,
                account_id="acct-existing" if owned else None,
            )
        )

    # v2_trade_plan：7 行 = 4 可归属 + 1 断链 + 1 未知市场 + 1 已有归属。
    _plan("plan-cn-1", "sv-cn-1", "CN")
    _plan("plan-cn-2", "sv-cn-2", "CN")
    _plan("plan-us-1", "sv-us-1", "US")
    _plan("plan-ghost", "sv-ghost", "CN")  # 断链（version 不存在）→ unassigned:CN
    _plan("plan-owned", "sv-cn-1", "CN", owned=True)  # already_owned，绝不覆盖
    _plan("plan-mismatch", "sv-cn-1", "US")  # 行内 market 与 version market 冲突
    _plan("plan-unknown", "sv-ghost2", "XX")  # 断链 + 未知市场 → unassigned:CN 兜底
    session.add(
        orm.ApprovalRequestModel(
            request_id="apr-1",
            order_intent_id="oi-1",
            strategy_version_id="sv-us-1",
            market="US",
            ts_code="AAPL",
            direction="BUY",
            qty=Decimal("10"),
            price=Decimal("100"),
            reason="r",
            status="APPROVED",
            channel="TELEGRAM",
            token="tok",
            auto_follow=False,
            created_at=now,
        )
    )
    session.add(
        orm.OrderIntentModel(
            intent_id="oi-1",
            plan_id="plan-cn-1",
            strategy_version_id="sv-cn-1",
            market="CN",
            ts_code="600519.SH",
            intent_type="OPEN",
            intent_side="BUY",
            intent_qty=Decimal("100"),
            intent_price=Decimal("1700"),
            status="SUBMITTED",
            created_at=now,
        )
    )
    session.add(
        orm.PositionModel(
            position_id="pos-1",
            strategy_version_id="sv-us-1",
            market="US",
            ts_code="AAPL",
            direction="LONG",
            open_qty=Decimal("10"),
            open_price=Decimal("100"),
            status="OPEN",
            opened_at=now,
        )
    )
    session.add(
        orm.ReplayModel(
            replay_id="rp-1",
            strategy_version_id="sv-us-1",
            market="US",
            ts_code="AAPL",
            pnl=Decimal("10"),
            pnl_pct=Decimal("0.1"),
            holding_period_seconds=3600,
            exit_reason="TAKE_PROFIT",
            attribution={},
            status="CLOSED",
            created_at=now,
        )
    )
    session.commit()


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        _seed(session)
    return factory


@pytest.fixture
def backfill(session_factory):
    return OwnershipBackfill(session_factory, admin_user_id=ADMIN_ID)


def _database_snapshot(session_factory) -> dict[str, list[str]]:
    """全库逐表行内容快照（repr 归一 Decimal/datetime），dry-run 前后比对。"""
    snap: dict[str, list[str]] = {}
    with session_factory() as session:
        for table in sorted(Base.metadata.tables.values(), key=lambda t: t.name):
            rows = session.execute(select(table)).all()
            snap[table.name] = sorted(repr(tuple(row)) for row in rows)
    return snap


def _ownership_counts(session_factory) -> dict[str, int]:
    counts = {}
    with session_factory() as session:
        for name, model in DIRECT_TABLES.items():
            rows = session.execute(select(model.user_strategy_id)).scalars().all()
            counts[name] = sum(1 for v in rows if v is not None)
    return counts


def _get(session_factory, model, key: str, value: str):
    with session_factory() as session:
        obj = session.execute(select(model).where(key == value)).scalar_one()
        session.refresh(obj)
        session.expunge(obj)
        return obj


# ---------------------------------------------------------------------------
# Step 1 RED：dry-run 零写入 + 幂等
# ---------------------------------------------------------------------------

class TestDryRunAndIdempotency:
    def test_dry_run_reports_without_writes(self, backfill, session_factory):
        before = _database_snapshot(session_factory)
        report = backfill.run(dry_run=True)
        assert report["v2_trade_plan"]["source"] == 7
        assert report["dry_run"] is True
        assert report["updated_total"] == 10  # 报告"将更新"，但库必须零变化
        assert _database_snapshot(session_factory) == before

    def test_backfill_is_idempotent(self, backfill, session_factory):
        first = backfill.run(dry_run=False)
        assert first["updated_total"] == 10
        assert _ownership_counts(session_factory) == first["final_counts"]

        second = backfill.run(dry_run=False)
        assert second["updated_total"] == 0
        assert _ownership_counts(session_factory) == first["final_counts"]
        # 第二次不再新增任何占位 account/instance。
        with session_factory() as session:
            accounts = session.execute(select(orm.UserAccountModel)).scalars().all()
            instances = session.execute(select(orm.UserStrategyInstanceModel)).scalars().all()
        assert sorted(a.account_id for a in accounts) == [ACCT_CN, ACCT_US]
        assert sorted(i.user_strategy_id for i in instances) == sorted(
            [USTRAT_CN1, USTRAT_CN2, USTRAT_US1, USTRAT_UNASSIGNED_CN]
        )


# ---------------------------------------------------------------------------
# Step 2 RED：确定性映射
# ---------------------------------------------------------------------------

class TestDeterministicMapping:
    def test_null_rows_mapped_to_version_market_legacy_instance(self, backfill, session_factory):
        backfill.run(dry_run=False)
        plan = _get(session_factory, orm.TradePlanModel, orm.TradePlanModel.plan_id, "plan-cn-1")
        assert plan.user_strategy_id == USTRAT_CN1
        assert plan.account_id == ACCT_CN

    def test_different_markets_get_different_legacy_accounts(self, backfill, session_factory):
        backfill.run(dry_run=False)
        acct_cn = _get(session_factory, orm.UserAccountModel, orm.UserAccountModel.account_id, ACCT_CN)
        acct_us = _get(session_factory, orm.UserAccountModel, orm.UserAccountModel.account_id, ACCT_US)
        assert ACCT_CN != ACCT_US
        assert (acct_cn.market, acct_cn.currency, acct_cn.account_type) == ("CN", "CNY", "STRATEGY_DEDICATED")
        assert (acct_us.market, acct_us.currency, acct_us.account_type) == ("US", "USD", "STRATEGY_DEDICATED")
        assert acct_cn.user_id == ADMIN_ID and acct_us.user_id == ADMIN_ID

    def test_legacy_instances_disabled_and_owned_by_admin(self, backfill, session_factory):
        backfill.run(dry_run=False)
        inst = _get(
            session_factory,
            orm.UserStrategyInstanceModel,
            orm.UserStrategyInstanceModel.user_strategy_id,
            USTRAT_US1,
        )
        assert inst.user_id == ADMIN_ID
        assert inst.strategy_version_id == "sv-us-1"
        assert inst.account_id == ACCT_US
        assert inst.enabled is False  # 绝不参与新执行
        assert inst.auto_execute is False

    def test_broken_chain_goes_to_unassigned_placeholder(self, backfill, session_factory):
        backfill.run(dry_run=False)
        plan = _get(session_factory, orm.TradePlanModel, orm.TradePlanModel.plan_id, "plan-ghost")
        assert plan.user_strategy_id == USTRAT_UNASSIGNED_CN
        assert plan.account_id == ACCT_CN
        inst = _get(
            session_factory,
            orm.UserStrategyInstanceModel,
            orm.UserStrategyInstanceModel.user_strategy_id,
            USTRAT_UNASSIGNED_CN,
        )
        assert inst.enabled is False
        assert inst.account_id == ACCT_CN
        # unassigned 占位锚定同市场最旧 version（strategy_version_id NOT NULL FK）。
        assert inst.strategy_version_id == "sv-cn-1"

    def test_unknown_market_routes_to_cn_fallback_and_conflict(self, backfill, session_factory):
        report = backfill.run(dry_run=False)
        plan = _get(session_factory, orm.TradePlanModel, orm.TradePlanModel.plan_id, "plan-unknown")
        assert plan.user_strategy_id == USTRAT_UNASSIGNED_CN
        assert plan.account_id == ACCT_CN
        assert report["v2_trade_plan"]["conflicts"] == 2  # market_mismatch + unknown_market
        kinds = sorted(d["kind"] for d in report["conflict_details"])
        assert kinds == ["market_mismatch", "unknown_market"]

    def test_market_mismatch_resolved_by_version_market(self, backfill, session_factory):
        backfill.run(dry_run=False)
        plan = _get(session_factory, orm.TradePlanModel, orm.TradePlanModel.plan_id, "plan-mismatch")
        assert plan.user_strategy_id == USTRAT_CN1  # version.market=CN 权威
        assert plan.account_id == ACCT_CN

    def test_all_five_tables_mapped(self, backfill, session_factory):
        report = backfill.run(dry_run=False)
        assert report["v2_approval_request"] == {
            "source": 1, "already_owned": 0, "assignable": 1,
            "unassigned": 0, "updated": 1, "conflicts": 0,
        }
        assert report["v2_order_intent"]["updated"] == 1
        assert report["v2_position"]["updated"] == 1
        assert report["v2_replay"]["updated"] == 1
        intent = _get(session_factory, orm.OrderIntentModel, orm.OrderIntentModel.intent_id, "oi-1")
        assert (intent.user_strategy_id, intent.account_id) == (USTRAT_CN1, ACCT_CN)
        position = _get(session_factory, orm.PositionModel, orm.PositionModel.position_id, "pos-1")
        assert (position.user_strategy_id, position.account_id) == (USTRAT_US1, ACCT_US)
        replay = _get(session_factory, orm.ReplayModel, orm.ReplayModel.replay_id, "rp-1")
        assert (replay.user_strategy_id, replay.account_id) == (USTRAT_US1, ACCT_US)

    def test_already_owned_rows_untouched(self, backfill, session_factory):
        report = backfill.run(dry_run=False)
        plan = _get(session_factory, orm.TradePlanModel, orm.TradePlanModel.plan_id, "plan-owned")
        assert plan.user_strategy_id == "usr-existing"
        assert plan.account_id == "acct-existing"
        assert report["v2_trade_plan"]["already_owned"] == 1


# ---------------------------------------------------------------------------
# Step 3 RED：admin 解析 + CLI
# ---------------------------------------------------------------------------

class TestAdminResolution:
    def test_admin_missing_raises(self, session_factory):
        with session_factory() as session:
            session.execute(
                orm.UserModel.__table__.delete().where(orm.UserModel.username == "admin")
            )
            session.commit()
        with pytest.raises(BackfillError, match="admin"):
            resolve_admin_user_id(session_factory, "admin")

    def test_non_admin_user_rejected(self, session_factory):
        # 安全铁律：历史数据绝不挂给普通用户——非 ADMIN 角色直接拒绝。
        with pytest.raises(BackfillError, match="ADMIN"):
            resolve_admin_user_id(session_factory, "bob")


class TestCli:
    def test_cli_dry_run_default_no_writes_and_json_out(self, session_factory, tmp_path):
        before = _database_snapshot(session_factory)
        out = tmp_path / "ownership.json"
        rc = main(["--json-out", str(out)], session_factory=session_factory)
        assert rc == 0
        assert _database_snapshot(session_factory) == before
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["dry_run"] is True
        assert report["updated_total"] == 10
        assert report["final_counts"]["v2_trade_plan"] == 7  # 事务内投影：1 已有 + 6 将更新
        for key in ("source", "already_owned", "assignable", "updated", "unassigned", "conflicts"):
            assert key in report["v2_trade_plan"]

    def test_cli_apply_commits(self, session_factory):
        rc = main(["--apply", "--admin-user", "admin"], session_factory=session_factory)
        assert rc == 0
        assert _ownership_counts(session_factory)["v2_trade_plan"] == 7  # 1 已有 + 6 回填

    def test_cli_strict_conflict_rolls_back_and_exits_nonzero(self, session_factory, tmp_path):
        before = _ownership_counts(session_factory)
        out = tmp_path / "strict.json"
        rc = main(["--apply", "--strict", "--json-out", str(out)], session_factory=session_factory)
        assert rc == 1
        assert _ownership_counts(session_factory) == before  # 冲突 → 全量回滚
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["conflicts_total"] == 2

    def test_cli_admin_missing_exits_nonzero(self, session_factory, capsys):
        with session_factory() as session:
            session.execute(
                orm.UserModel.__table__.delete().where(orm.UserModel.username == "admin")
            )
            session.commit()
        rc = main(["--apply"], session_factory=session_factory)
        assert rc == 1
        assert "admin" in capsys.readouterr().err

    def test_cli_non_admin_rejected(self, session_factory, capsys):
        rc = main(["--apply", "--admin-user", "bob"], session_factory=session_factory)
        assert rc == 1
        assert "ADMIN" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Task 8：严格 ownership verifier TDD（scripts/verify_multi_user_ownership.py）
#
# 只读校验器（0024 NOT NULL 门的前置守卫）。健康基线 = T7 回填后的干净库；
# 缺陷夹具逐项注入 → 仅对应检查 FAIL、其它全 PASS；全程隔离 sqlite。
# ---------------------------------------------------------------------------

ACCT_BOB = "acct-bob-1"
USTRAT_BOB = "ustrat-bob-1"
ACCT_SHARED = "acct-shared-1"
USTRAT_SHARED = "ustrat-shared-a"
EV_1 = "nev-1"
DELIVERY_UQ = "uq_v2_notification_delivery_event_user_channel"


def _seed_verifier_world(session: object) -> None:
    """健康库 legacy 侧：用户/策略/版本 + 三行干净 NULL 归属（无 mismatch/断链）。"""
    now = utc_now()
    for uid, uname, role in [("u-admin", "admin", "ADMIN"), ("u-bob", "bob", "USER")]:
        session.add(
            orm.UserModel(
                user_id=uid,
                username=uname,
                password_hash="x",
                role=role,
                is_active=True,
                locale="zh",
                created_at=now,
            )
        )
    for sid, market in [("stg-cn", "CN"), ("stg-us", "US")]:
        session.add(
            orm.StrategyModel(
                strategy_id=sid,
                strategy_name=sid,
                market=market,
                status="ACTIVE",
                created_at=now,
                updated_at=now,
            )
        )
    for sv, sid, market in [
        ("sv-cn-1", "stg-cn", "CN"),
        ("sv-cn-2", "stg-cn", "CN"),
        ("sv-us-1", "stg-us", "US"),
    ]:
        session.add(
            orm.StrategyVersionModel(
                strategy_version_id=sv,
                strategy_id=sid,
                version_no="v1",
                market=market,
                status="PUBLISHED",
                spec={},
                created_at=now,
            )
        )
    session.add(
        orm.TradePlanModel(
            plan_id="plan-cn-a",
            candidate_id="c1",
            strategy_version_id="sv-cn-1",
            market="CN",
            ts_code="600519.SH",
            plan_status="EXECUTED",
            created_at=now,
        )
    )
    session.add(
        orm.TradePlanModel(
            plan_id="plan-cn-b",
            candidate_id="c2",
            strategy_version_id="sv-cn-2",
            market="CN",
            ts_code="000001.SZ",
            plan_status="EXECUTED",
            created_at=now,
        )
    )
    session.add(
        orm.PositionModel(
            position_id="pos-us-a",
            strategy_version_id="sv-us-1",
            market="US",
            ts_code="AAPL",
            direction="LONG",
            open_qty=Decimal("10"),
            open_price=Decimal("100"),
            status="OPEN",
            opened_at=now,
        )
    )
    session.commit()


def _seed_verifier_owned(session: object) -> None:
    """健康库多用户侧（backfill 之后追加）：bob 专属账户/实例 + 共享账户配额 +
    通知事件双渠道投递。"""
    now = utc_now()

    def _instance(ustrat: str, account: str, sv: str, *, enabled: bool, quota: Decimal | None) -> None:
        session.add(
            orm.UserStrategyInstanceModel(
                user_strategy_id=ustrat,
                user_id="u-bob",
                strategy_version_id=sv,
                account_id=account,
                enabled=enabled,
                auto_execute=False,
                execution_mode="MANUAL",
                capital_quota=quota,
                max_position_ratio=Decimal("0.1"),
                risk_parameters={},
                strategy_parameters={},
                notification_preferences={},
                created_at=now,
                updated_at=now,
            )
        )

    session.add(
        orm.UserAccountModel(
            account_id=ACCT_BOB,
            user_id="u-bob",
            market="CN",
            currency="CNY",
            account_type="STRATEGY_DEDICATED",
            initial_capital=Decimal("10000"),
            cash_balance=Decimal("8000"),
            reserved_cash=Decimal("500"),
            status="ACTIVE",
            created_at=now,
            updated_at=now,
        )
    )
    session.add(
        orm.UserAccountModel(
            account_id=ACCT_SHARED,
            user_id="u-bob",
            market="CN",
            currency="CNY",
            account_type="SHARED_PORTFOLIO",
            initial_capital=Decimal("1000"),
            cash_balance=Decimal("1000"),
            reserved_cash=Decimal("0"),
            status="ACTIVE",
            created_at=now,
            updated_at=now,
        )
    )
    _instance(USTRAT_BOB, ACCT_BOB, "sv-cn-1", enabled=True, quota=None)  # 专属账户无配额语义
    _instance(USTRAT_SHARED, ACCT_SHARED, "sv-cn-2", enabled=True, quota=Decimal("400"))
    session.add(
        orm.TradePlanModel(
            plan_id="plan-bob-1",
            candidate_id="c3",
            strategy_version_id="sv-cn-1",
            market="CN",
            ts_code="600519.SH",
            plan_status="EXECUTED",
            created_at=now,
            user_strategy_id=USTRAT_BOB,
            account_id=ACCT_BOB,
        )
    )
    session.add(
        orm.NotificationEventModel(
            event_id=EV_1,
            user_strategy_id=USTRAT_BOB,
            account_id=ACCT_BOB,
            user_id="u-bob",
            event_type="ORDER_FILLED",
            payload={},
            occurred_at=now,
            created_at=now,
        )
    )
    for did, channel, status in [("del-1", "telegram", "SENT"), ("del-2", "email", "PENDING")]:
        session.add(
            orm.NotificationDeliveryModel(
                delivery_id=did,
                event_id=EV_1,
                user_id="u-bob",
                channel=channel,
                status=status,
                attempt_count=0,
                created_at=now,
            )
        )
    session.commit()


def _healthy_verifier_factory():
    """健康库 = 干净 legacy 种子 → T7 回填（--apply 语义）→ bob 多用户世界。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        _seed_verifier_world(session)
    OwnershipBackfill(factory, admin_user_id=ADMIN_ID).run(dry_run=False)
    with factory() as session:
        _seed_verifier_owned(session)
    return factory, engine


@pytest.fixture
def verifier_factory():
    factory, _engine = _healthy_verifier_factory()
    return factory


def _checks(report: dict) -> dict:
    return {c["name"]: c for c in report["checks"]}


def _assert_only_fail(report: dict, name: str) -> None:
    assert report["ok"] is False
    failed = [c["name"] for c in report["checks"] if not c["ok"]]
    assert failed == [name], f"预期仅 {name} FAIL，实际 FAIL：{failed}"


def _add_plan(factory, plan_id: str, *, sv: str = "sv-cn-1", market: str = "CN",
              ustrat: str | None = None, account: str | None = None) -> None:
    with factory() as session:
        session.add(
            orm.TradePlanModel(
                plan_id=plan_id,
                candidate_id=f"c-{plan_id}",
                strategy_version_id=sv,
                market=market,
                ts_code="600519.SH",
                plan_status="EXECUTED",
                created_at=utc_now(),
                user_strategy_id=ustrat,
                account_id=account,
            )
        )
        session.commit()


def _add_instance(factory, ustrat: str, account: str, sv: str, *, enabled: bool,
                  quota: Decimal | None, user: str = "u-bob") -> None:
    now = utc_now()
    with factory() as session:
        session.add(
            orm.UserStrategyInstanceModel(
                user_strategy_id=ustrat,
                user_id=user,
                strategy_version_id=sv,
                account_id=account,
                enabled=enabled,
                auto_execute=False,
                execution_mode="MANUAL",
                capital_quota=quota,
                max_position_ratio=Decimal("0.1"),
                risk_parameters={},
                strategy_parameters={},
                notification_preferences={},
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()


def _mutate_account(factory, account_id: str, **values) -> None:
    with factory() as session:
        acct = session.execute(
            select(orm.UserAccountModel).where(orm.UserAccountModel.account_id == account_id)
        ).scalar_one()
        for k, v in values.items():
            setattr(acct, k, v)
        session.commit()


def _drop_delivery_unique(engine) -> None:
    """sqlite 无 DROP CONSTRAINT：按 ORM 元数据重编译 CREATE TABLE、剔除
    unique(event_id,user_id,channel) 后重建表——duplicate_delivery 缺陷夹具在
    unique 约束生效下不可直接造（插不进），此为唯一可行注入路径（其余约束/列不变）。"""
    table = orm.NotificationDeliveryModel.__table__
    ddl = str(CreateTable(table).compile(dialect=engine.dialect))
    pattern = rf",?\s*CONSTRAINT {DELIVERY_UQ} UNIQUE \([^)]*\)"
    new_ddl, n = re.subn(pattern, "", ddl, count=1)
    assert n == 1, "unique 约束子句未命中，DDL 手术失败（勿静默跳过）"
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE v2_notification_delivery"))
        conn.execute(text(new_ddl))


class TestVerifierHealthyRun:
    def test_all_checks_pass_after_backfill(self, verifier_factory):
        report = OwnershipVerifier(verifier_factory).run()
        assert report["ok"] is True
        assert report["failed"] == []
        assert [c["name"] for c in report["checks"]] == list(CHECK_ORDER)
        assert report["totals"] == {"checks": len(CHECK_ORDER), "failed": 0}
        for c in report["checks"]:
            assert c["ok"] is True and c["count"] == 0 and c["sample_ids"] == []
        assert _checks(report)["quota_overflow"]["shared_missing_quota"] == 0

    def test_verifier_never_writes(self, verifier_factory):
        before = _database_snapshot(verifier_factory)
        OwnershipVerifier(verifier_factory).run()
        assert _database_snapshot(verifier_factory) == before


class TestVerifierDefectFixtures:
    def test_null_owner_detected(self, verifier_factory):
        _add_plan(verifier_factory, "plan-null-1", ustrat=None, account=None)
        report = OwnershipVerifier(verifier_factory).run()
        _assert_only_fail(report, "null_owner")
        check = _checks(report)["null_owner"]
        assert check["count"] == 1
        assert check["sample_ids"] == ["v2_trade_plan:plan-null-1"]
        assert check["per_table"]["v2_trade_plan"] == 1

    def test_cross_user_detected(self, verifier_factory):
        # bob 的实例 + admin 的 legacy 账户 → 同行实例/账户属不同用户。
        _add_plan(verifier_factory, "plan-cross-1", ustrat=USTRAT_BOB, account=ACCT_CN)
        report = OwnershipVerifier(verifier_factory).run()
        _assert_only_fail(report, "cross_user")
        check = _checks(report)["cross_user"]
        assert check["count"] == 1
        assert check["sample_ids"] == ["v2_trade_plan:plan-cross-1"]

    def test_market_mismatch_detected(self, verifier_factory):
        # 实例链 sv-cn-1→stg-cn.market=CN，行内 market=US。
        _add_plan(verifier_factory, "plan-market-1", market="US",
                  ustrat=USTRAT_BOB, account=ACCT_BOB)
        report = OwnershipVerifier(verifier_factory).run()
        _assert_only_fail(report, "market_mismatch")
        check = _checks(report)["market_mismatch"]
        assert check["count"] == 1
        assert check["sample_ids"] == ["v2_trade_plan:plan-market-1"]

    def test_orphan_chain_detected(self, verifier_factory):
        # sqlite 不强制 FK：实例/账户双断链可造（防御性检查的靶子）。
        _add_plan(verifier_factory, "plan-orphan-1", ustrat="ustrat-ghost", account="acct-ghost")
        report = OwnershipVerifier(verifier_factory).run()
        _assert_only_fail(report, "orphan_chain")
        check = _checks(report)["orphan_chain"]
        assert check["count"] == 1
        assert check["sample_ids"] == ["v2_trade_plan:plan-orphan-1"]

    def test_negative_available_detected(self, verifier_factory):
        # cash<0 / reserved<0 被 CHECK 挡住造不出；reserved>cash 无约束，可造。
        _mutate_account(verifier_factory, ACCT_BOB, reserved_cash=Decimal("9999"))
        report = OwnershipVerifier(verifier_factory).run()
        _assert_only_fail(report, "negative_available")
        check = _checks(report)["negative_available"]
        assert check["count"] == 1
        assert check["sample_ids"] == [ACCT_BOB]

    def test_quota_overflow_detected(self, verifier_factory):
        # 共享账户 initial=1000：已启用 400 + 新启用 9999 → 超配。
        _add_instance(verifier_factory, "ustrat-shared-b", ACCT_SHARED, "sv-cn-1",
                      enabled=True, quota=Decimal("9999"))
        # 只有禁用实例的第二个共享账户：禁用实例不参与配额求和（若计入会多报一个账户）。
        now = utc_now()
        with verifier_factory() as session:
            session.add(
                orm.UserAccountModel(
                    account_id="acct-shared-2",
                    user_id="u-bob",
                    market="CN",
                    currency="CNY",
                    account_type="SHARED_PORTFOLIO",
                    initial_capital=Decimal("100"),
                    cash_balance=Decimal("100"),
                    reserved_cash=Decimal("0"),
                    status="ACTIVE",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()
        _add_instance(verifier_factory, "ustrat-shared-disabled", "acct-shared-2", "sv-us-1",
                      enabled=False, quota=Decimal("999999"))
        report = OwnershipVerifier(verifier_factory).run()
        _assert_only_fail(report, "quota_overflow")
        check = _checks(report)["quota_overflow"]
        assert check["count"] == 1  # 仅 acct-shared-1 超配；acct-shared-2 禁用实例不计入
        assert check["sample_ids"] == [ACCT_SHARED]
        assert check["shared_missing_quota"] == 0

    def test_shared_enabled_missing_quota_detected(self, verifier_factory):
        # T5 后端 enable 已禁 capital_quota=None；历史残留按缺陷处理（防御）。
        _add_instance(verifier_factory, "ustrat-shared-free", ACCT_SHARED, "sv-cn-1",
                      enabled=True, quota=None)
        report = OwnershipVerifier(verifier_factory).run()
        _assert_only_fail(report, "quota_overflow")
        check = _checks(report)["quota_overflow"]
        assert check["ok"] is False
        assert check["count"] == 0  # 无超配账户（None 不参与求和，400 ≤ 1000）
        assert check["shared_missing_quota"] == 1
        assert check["shared_missing_sample_ids"] == ["ustrat-shared-free"]

    def test_legacy_placeholder_enabled_detected(self, verifier_factory):
        with verifier_factory() as session:
            inst = session.execute(
                select(orm.UserStrategyInstanceModel).where(
                    orm.UserStrategyInstanceModel.user_strategy_id == USTRAT_CN1
                )
            ).scalar_one()
            inst.enabled = True  # 占位被误启用参与执行——verifier 必须拦下
            session.commit()
        report = OwnershipVerifier(verifier_factory).run()
        _assert_only_fail(report, "legacy_placeholder_sanity")
        check = _checks(report)["legacy_placeholder_sanity"]
        assert check["count"] == 1
        assert check["sample_ids"] == [USTRAT_CN1]

    def test_legacy_placeholder_on_non_admin_account_detected(self, verifier_factory):
        _add_instance(verifier_factory, "ustrat-legacy-evil", ACCT_BOB, "sv-cn-1",
                      enabled=False, quota=None)
        report = OwnershipVerifier(verifier_factory).run()
        _assert_only_fail(report, "legacy_placeholder_sanity")
        check = _checks(report)["legacy_placeholder_sanity"]
        assert check["count"] == 1
        assert check["sample_ids"] == ["ustrat-legacy-evil"]


class TestVerifierDuplicateDelivery:
    def test_unique_constraint_blocks_duplicate(self, verifier_factory):
        # 约束防御存在性：unique(event,user,channel) 完好时重复投递插不进去。
        with verifier_factory() as session:
            session.add(
                orm.NotificationDeliveryModel(
                    delivery_id="del-dup",
                    event_id=EV_1,
                    user_id="u-bob",
                    channel="telegram",
                    status="PENDING",
                    attempt_count=0,
                    created_at=utc_now(),
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()

    def test_duplicate_detected_when_constraint_bypassed(self):
        factory, engine = _healthy_verifier_factory()
        _drop_delivery_unique(engine)  # 模拟约束缺失/被绕过的病态库（重建后表为空）
        now = utc_now()
        with factory() as session:
            # 同 (event_id,user_id,channel) 两行：约束完好时插不进，病态库可造。
            for did in ("del-dup-a", "del-dup-b"):
                session.add(
                    orm.NotificationDeliveryModel(
                        delivery_id=did,
                        event_id=EV_1,
                        user_id="u-bob",
                        channel="telegram",
                        status="PENDING",
                        attempt_count=0,
                        created_at=now,
                    )
                )
            session.commit()
        report = OwnershipVerifier(factory).run()
        _assert_only_fail(report, "duplicate_delivery")
        check = _checks(report)["duplicate_delivery"]
        assert check["count"] == 1
        assert check["sample_ids"] == [f"{EV_1}|u-bob|telegram"]


class TestVerifierCli:
    def test_cli_json_out_and_strict_exit_0_on_healthy(self, verifier_factory, tmp_path):
        before = _database_snapshot(verifier_factory)
        out = tmp_path / "verify.json"
        rc = verify_main(["--json-out", str(out), "--strict"], session_factory=verifier_factory)
        assert rc == 0
        assert _database_snapshot(verifier_factory) == before  # CLI 路径同样只读
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["ok"] is True and report["failed"] == []
        assert len(report["checks"]) == len(CHECK_ORDER)
        for c in report["checks"]:
            assert set(c) >= {"name", "ok", "count", "sample_ids"}

    def test_cli_strict_exit_1_on_defect(self, verifier_factory, tmp_path):
        _add_plan(verifier_factory, "plan-null-1", ustrat=None, account=None)
        out = tmp_path / "verify.json"
        rc = verify_main(["--json-out", str(out), "--strict"], session_factory=verifier_factory)
        assert rc == 1
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["ok"] is False
        assert report["failed"] == ["null_owner"]

    def test_cli_default_human_summary(self, verifier_factory, capsys):
        assert verify_main([], session_factory=verifier_factory) == 0
        out = capsys.readouterr().out
        assert "null_owner" in out and "legacy_placeholder_sanity" in out
        assert "PASS" in out and "TOTAL" in out

    def test_cli_defect_without_strict_still_exit_0(self, verifier_factory, capsys):
        _add_plan(verifier_factory, "plan-null-1", ustrat=None, account=None)
        assert verify_main([], session_factory=verifier_factory) == 0
        assert "FAIL" in capsys.readouterr().out  # 报告缺陷但不阻断（--strict 才门禁）
