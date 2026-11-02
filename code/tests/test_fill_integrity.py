"""成交流水完整性（603311 案修复）：

- Bug B：POST /positions/close 一键平仓必须写 SELL 流水 + closed_at
  （此前只改持仓状态，9-02 同秒 11 笔无卖出记录）。
- Bug A：_record_fill 落库失败必须落 v2_alert 告警
  （此前外层 except 只记日志静默吞掉，9-18 窗口 8 笔流水永久丢失）。
- 补数据支撑：_record_fill(notify=False) 静默补写，不推历史假成交。
- 对账：reconcile_fills_job 扫「CLOSED 持仓无 SELL fill / 持仓无 BUY fill」。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from zhice_os_v2.api.dependencies import get_current_user, get_db
from zhice_os_v2.api.main import app
from zhice_os_v2.domain.ontology.objects import utc_now
from zhice_os_v2.domain.security.user import Role, User

# --------------------------------------------------------------------------- #
# 公共造数                                                                      #
# --------------------------------------------------------------------------- #


def _lot(position_id: str, *, status: str = "OPEN") -> SimpleNamespace:
    """最小持仓对象（_record_fill / close 端点用到的字段）。"""
    from zhice_os_v2.domain.portfolio.position import PositionStatus

    return SimpleNamespace(
        position_id=position_id,
        strategy_version_id="v1-cn-sv-7",
        market="CN",
        ts_code="603311.SH",
        direction="LONG",
        open_qty=Decimal("100"),
        open_price=Decimal("10.0"),
        status=PositionStatus(status),
        close_qty=None,
        close_price=None,
        opened_at=utc_now() - timedelta(days=1),  # CN T+1：昨仓方可卖（P0-1 守卫）
        closed_at=None,
        trade_plan_id=None,
        pnl_amount=None,
        pnl_pct=None,
        fee=Decimal("0"),
        user_strategy_id=None,
        account_id=None,
    )


class _FakeSession:
    """吃掉四表 upsert 的假会话（不落库）。"""

    def __init__(self, *, commit_raises: Exception | None = None) -> None:
        self.executed: list[object] = []
        self._commit_raises = commit_raises

    def execute(self, stmt) -> None:  # type: ignore[no-untyped-def]
        self.executed.append(stmt)

    def commit(self) -> None:
        if self._commit_raises is not None:
            raise self._commit_raises

    def close(self) -> None:
        pass


def _alert_recorder() -> tuple[list[dict], object]:
    """send_alert 位置+关键字参通吃的记录器。"""
    calls: list[dict] = []

    def _spy(*a, **kw):  # type: ignore[no-untyped-def]
        merged = dict(zip(("level", "title", "body"), a))
        merged.update(kw)
        calls.append(merged)

    return calls, _spy


@pytest.fixture
def sql_env(monkeypatch: pytest.MonkeyPatch):
    """sql 后端 + 可控 FakeSession + telegram/告警双记录器（broadcast 模式）。"""
    import zhice_os_v2.application.services.alert_service as alert_mod
    import zhice_os_v2.application.services.telegram_notifier as tn
    import zhice_os_v2.config.settings as settings_mod
    import zhice_os_v2.infrastructure.db.session as sess_mod

    fake_settings = SimpleNamespace(
        persistence_backend="sql", telegram_notification_mode="broadcast"
    )
    monkeypatch.setattr(settings_mod, "get_settings", lambda: fake_settings)

    notify_calls: list[dict] = []
    alert_calls, alert_spy = _alert_recorder()
    monkeypatch.setattr(tn, "notify_fill", lambda **kw: notify_calls.append(kw))
    monkeypatch.setattr(alert_mod, "send_alert", alert_spy)

    state = SimpleNamespace(
        settings=fake_settings,
        notify_calls=notify_calls,
        alert_calls=alert_calls,
        session=None,
    )

    def _use_session(session: _FakeSession) -> None:
        state.session = session
        monkeypatch.setattr(sess_mod, "get_session", lambda: session)

    state.use_session = _use_session
    return state


# --------------------------------------------------------------------------- #
# Bug B：/positions/close 写流水 + closed_at                                     #
# --------------------------------------------------------------------------- #


def test_positions_close_writes_sell_fill_and_closed_at(
    monkeypatch: pytest.MonkeyPatch,
):
    """一键平仓必须：①写 closed_at ②对每个持仓落 SELL 流水（带盈亏与理由）。"""
    from zhice_os_v2.application.services.workflow_service import WorkflowService
    from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

    db = InMemoryDatabase()
    lot = _lot("pos-t-close-1")
    db.save_position(lot)

    fill_calls: list[dict] = []
    monkeypatch.setattr(
        WorkflowService,
        "_record_fill",
        lambda self, **kw: fill_calls.append(kw),
    )
    # P0-1：/positions/close 已禁止调用方任意价，按最新行情价成交；行情打桩为 12.5。
    import zhice_os_v2.api.routers.positions as pos_mod

    monkeypatch.setattr(
        pos_mod, "_get_latest_prices", lambda symbols, market: {"603311.SH": 12.5}
    )
    monkeypatch.setattr(
        pos_mod, "_get_pre_closes", lambda symbols, market: {"603311.SH": 12.5}
    )

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: User(
        user_id="u-admin", username="admin", role=Role.ADMIN
    )
    try:
        from fastapi.testclient import TestClient

        resp = TestClient(app).post(
            "/api/v1/positions/close", json={"market": "CN"}
        )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200, resp.text
    closed = db.get_position("pos-t-close-1")
    assert closed is not None and closed.status == "CLOSED"
    # ① closed_at 必须落（此前 /positions/close 从不写）
    assert closed.closed_at is not None
    # ② SELL 流水：数量/价格/盈亏/理由齐全
    assert len(fill_calls) == 1
    kw = fill_calls[0]
    assert kw["side"] == "SELL"
    assert kw["market"] == "CN" and kw["ts_code"] == "603311.SH"
    assert kw["qty"] == Decimal("100") and kw["price"] == Decimal("12.5")
    assert kw["realized_pnl"] == Decimal("250.0")  # (12.5-10)*100
    assert kw["exit_kind"] == "MANUAL"
    assert kw["sell_reason"]
    assert kw["fill_time"] is not None


# --------------------------------------------------------------------------- #
# Bug A：_record_fill 失败落告警                                                  #
# --------------------------------------------------------------------------- #


def test_record_fill_failure_raises_alert(sql_env):
    """commit 失败：不抛异常（交易主流程无感）+ send_alert(CRITICAL) 落 v2_alert。"""
    from zhice_os_v2.application.services.workflow_service import WorkflowService
    from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

    sql_env.use_session(_FakeSession(commit_raises=RuntimeError("db down")))

    # 不得抛出
    WorkflowService(InMemoryDatabase())._record_fill(
        market="CN",
        ts_code="603311.SH",
        side="SELL",
        qty=Decimal("100"),
        price=Decimal("12.5"),
        position=_lot("pos-t-err-1", status="CLOSED"),
        realized_pnl=Decimal("250"),
    )

    assert len(sql_env.alert_calls) == 1
    kw = sql_env.alert_calls[0]
    assert kw["level"] == "CRITICAL"
    assert "603311" in kw["title"] or "603311" in kw.get("body", "")


def test_record_fill_success_no_alert(sql_env):
    """正常路径不告警（对照组）。"""
    from zhice_os_v2.application.services.workflow_service import WorkflowService
    from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

    sql_env.use_session(_FakeSession())

    WorkflowService(InMemoryDatabase())._record_fill(
        market="CN",
        ts_code="603311.SH",
        side="SELL",
        qty=Decimal("100"),
        price=Decimal("12.5"),
        position=_lot("pos-t-ok-1", status="CLOSED"),
        realized_pnl=Decimal("250"),
    )
    assert sql_env.alert_calls == []


# --------------------------------------------------------------------------- #
# 补数据支撑：notify=False 静默补写                                               #
# --------------------------------------------------------------------------- #


def test_record_fill_notify_false_silences_telegram(sql_env):
    """notify=False：broadcast 模式下也不推 telegram（历史补数据防假成交推送）。"""
    from zhice_os_v2.application.services.workflow_service import WorkflowService
    from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

    sql_env.use_session(_FakeSession())

    WorkflowService(InMemoryDatabase())._record_fill(
        market="CN",
        ts_code="603311.SH",
        side="SELL",
        qty=Decimal("100"),
        price=Decimal("12.5"),
        position=_lot("pos-t-backfill-1", status="CLOSED"),
        realized_pnl=Decimal("250"),
        notify=False,
    )
    assert sql_env.notify_calls == []

    # 对照：默认 notify=True 照常广播
    WorkflowService(InMemoryDatabase())._record_fill(
        market="CN",
        ts_code="603311.SH",
        side="SELL",
        qty=Decimal("100"),
        price=Decimal("12.5"),
        position=_lot("pos-t-backfill-2", status="CLOSED"),
        realized_pnl=Decimal("250"),
    )
    assert len(sql_env.notify_calls) == 1


# --------------------------------------------------------------------------- #
# 对账 job：扫缺口（隔离 sqlite）                                                 #
# --------------------------------------------------------------------------- #


@pytest.fixture
def reconcile_env(monkeypatch: pytest.MonkeyPatch):
    """隔离 sqlite（四链表 + position/alert）+ send_alert 记录器。"""
    import sqlalchemy as sa
    from sqlalchemy.orm import Session

    from zhice_os_v2.infrastructure.db import models as orm

    # StaticPool：:memory: 下所有连接共享同一底层连接（否则每连接各自建库，
    # 插数 session 与 job session 互相看不见）。
    engine = sa.create_engine(
        "sqlite:///:memory:",
        poolclass=sa.pool.StaticPool,
        connect_args={"check_same_thread": False},
    )
    orm.Base.metadata.create_all(
        engine,
        tables=[
            orm.PositionModel.__table__,
            orm.FillRecordModel.__table__,
            orm.ExecutionRecordModel.__table__,
            orm.OrderRequestModel.__table__,
            orm.OrderIntentModel.__table__,
            orm.AlertModel.__table__,
        ],
    )

    def _factory():  # type: ignore[no-untyped-def]
        return Session(bind=engine.connect())

    monkeypatch.setattr(
        "zhice_os_v2.infrastructure.db.session.get_session", _factory
    )

    import zhice_os_v2.application.services.alert_service as alert_mod
    import zhice_os_v2.config.settings as settings_mod

    monkeypatch.setattr(
        settings_mod,
        "get_settings",
        lambda: SimpleNamespace(
            persistence_backend="sql", telegram_notification_mode="broadcast"
        ),
    )
    alert_calls, alert_spy = _alert_recorder()
    monkeypatch.setattr(alert_mod, "send_alert", alert_spy)

    return SimpleNamespace(engine=engine, alert_calls=alert_calls)


def _insert_position(sess, position_id: str, *, status: str, market: str = "CN") -> None:
    from zhice_os_v2.infrastructure.db import models as orm

    sess.add(
        orm.PositionModel(
            position_id=position_id,
            strategy_version_id="v1-cn-sv-7",
            market=market,
            ts_code="603311.SH",
            direction="LONG",
            open_qty=Decimal("100"),
            open_price=Decimal("10"),
            status=status,
            opened_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
            closed_at=datetime(2026, 9, 18, 6, tzinfo=timezone.utc)
            if status == "CLOSED"
            else None,
            pnl_amount=Decimal("200") if status == "CLOSED" else None,
            fee=Decimal("0"),
        )
    )


def _insert_fill_chain(
    sess, position_id: str, *, is_sell: bool, market: str = "CN", _seq=[0]
) -> None:
    """按 _record_fill 同构写一条完整四链（fill→exec→req→intent）。

    _seq：显式主键序列——sqlite 下 BigInteger 主键不自增（模型未做
    with_variant(Integer, "sqlite")），必须手工给 id。
    """
    from zhice_os_v2.infrastructure.db import models as orm

    def _next_id() -> int:
        _seq[0] += 1
        return _seq[0]

    order_id = f"close-{position_id}" if is_sell else position_id.removeprefix("pos-")
    now = datetime(2026, 9, 18, 6, tzinfo=timezone.utc)
    sid = f"intent-{order_id}"
    sess.add(
        orm.OrderIntentModel(
            id=_next_id(),
            intent_id=sid,
            plan_id="",
            strategy_version_id="v1-cn-sv-7",
            user_strategy_id=None,
            account_id=None,
            market=market,
            ts_code="603311.SH",
            intent_type="CLOSE" if is_sell else "OPEN",
            intent_side="SELL" if is_sell else "BUY",
            intent_qty=Decimal("100"),
            intent_price=Decimal("12"),
            status="FILLED",
            created_at=now,
        )
    )
    rid = f"req-{order_id}"
    sess.add(
        orm.OrderRequestModel(
            id=_next_id(),
            request_id=rid,
            intent_id=sid,
            market=market,
            broker_channel="SIMULATED",
            idempotency_key=order_id,
            status="SUBMITTED",
            created_at=now,
        )
    )
    eid = f"exec-{order_id}"
    sess.add(
        orm.ExecutionRecordModel(
            id=_next_id(),
            execution_id=eid,
            request_id=rid,
            market=market,
            broker_order_id=f"SIM-{order_id}",
            execution_status="FILLED",
            created_at=now,
        )
    )
    sess.add(
        orm.FillRecordModel(
            id=_next_id(),
            fill_id=f"fill-{order_id}",
            execution_id=eid,
            market=market,
            ts_code="603311.SH",
            broker_trade_id=f"SIM-{order_id}",
            fill_price=Decimal("12"),
            fill_qty=Decimal("100"),
            fill_amount=Decimal("1200"),
            fill_time=now,
            sell_reason="平仓" if is_sell else None,
            realized_pnl=Decimal("200") if is_sell else None,
            created_at=now,
        )
    )
    sess.commit()


def test_reconcile_fills_job_detects_missing(reconcile_env):
    """缺口检测：V2原生 CLOSED 无 SELL / 持仓无 BUY 各自报数；v1- 导入与旧 sim 不算。"""
    from zhice_os_v2.application.services.fill_reconciliation import (
        reconcile_fills_job,
    )
    from zhice_os_v2.infrastructure.db.session import get_session

    sess = get_session()
    # 健康对照：完整 BUY+SELL 四链
    _insert_position(sess, "pos-ok-1", status="CLOSED")
    _insert_fill_chain(sess, "pos-ok-1", is_sell=False)
    _insert_fill_chain(sess, "pos-ok-1", is_sell=True)
    # 缺口①：CLOSED 无 SELL（Bug B/A 形态）
    _insert_position(sess, "pos-gap-sell", status="CLOSED")
    _insert_fill_chain(sess, "pos-gap-sell", is_sell=False)
    # 缺口②：OPEN 无 BUY（买入侧丢失形态）
    _insert_position(sess, "pos-gap-buy", status="OPEN")
    # 排除项：v1 导入持仓（V1 时代关闭，无 fill 正常）
    _insert_position(sess, "v1-cn-pos-x", status="CLOSED")
    # 排除项：旧 sim 前缀
    _insert_position(sess, "pos-sim-3", status="CLOSED")
    sess.commit()

    result = reconcile_fills_job()

    assert result["missing_sell"] == 1
    assert result["missing_buy"] == 1
    assert len(reconcile_env.alert_calls) == 1
    kw = reconcile_env.alert_calls[0]
    assert kw["level"] == "CRITICAL"


def test_reconcile_fills_job_clean_when_no_gap(reconcile_env):
    """无缺口：不告警。"""
    from zhice_os_v2.application.services.fill_reconciliation import (
        reconcile_fills_job,
    )
    from zhice_os_v2.infrastructure.db.session import get_session

    sess = get_session()
    _insert_position(sess, "pos-ok-1", status="CLOSED")
    _insert_fill_chain(sess, "pos-ok-1", is_sell=False)
    _insert_fill_chain(sess, "pos-ok-1", is_sell=True)

    result = reconcile_fills_job()
    assert result["missing_sell"] == 0 and result["missing_buy"] == 0
    assert reconcile_env.alert_calls == []


def test_reconcile_skipped_on_memory_backend(monkeypatch: pytest.MonkeyPatch):
    """内存后端（单测默认）直接跳过，不触碰 DB。"""
    import zhice_os_v2.config.settings as settings_mod

    monkeypatch.setattr(
        settings_mod,
        "get_settings",
        lambda: SimpleNamespace(persistence_backend="memory"),
    )
    from zhice_os_v2.application.services.fill_reconciliation import (
        reconcile_fills_job,
    )

    result = reconcile_fills_job()
    assert result["skipped"] is True
