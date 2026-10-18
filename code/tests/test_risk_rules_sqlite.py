"""风控恢复规则的 SQL 真实路径测试（隔离 sqlite）。

覆盖 risk_service 的直连 SQL 查询逻辑（单测默认内存后端走不到）：
- _sub_account_snapshot：子账户敞口聚合（sv 过滤 / owner 隔离 / 净值=初始+已实现）；
- _daily_loss_snapshot：当日已实现盈亏（今日/非今日边界）；
- 快照 turnover：MIN_LIQUIDITY 从 v2_latest_snapshot 取数（PASS/REJECT/SKIPPED）；
- 查询基础设施故障：敞口 SKIPPED 放行 + DATA_FRESHNESS fail-CLOSED（历史语义保留）。

隔离方式：自建 sqlite + monkeypatch get_session（risk_service 函数内导入 →
session 模块属性 patch 生效）；app_state_store 为模块级绑定需单独 patch，
settings 侧 persistence_backend 伪装为 sql 并把 risk_params 控制在
v2_app_state 表里（读取路径全真）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


# SQLite 下 BIGINT 不自增，编译为 INTEGER（与 test_owned_execution_pipeline 同 hack）。
@compiles(BigInteger, "sqlite")
def _compile_bigint_as_integer_sqlite(type_, compiler, **kw):  # noqa: ANN001, ANN202
    return "INTEGER"


from zhice_os_v2.application.services.risk_service import RiskService
from zhice_os_v2.domain.market.registry import get_profile
from zhice_os_v2.domain.planning.trade_plan import TradePlan
from zhice_os_v2.domain.risk.risk import RiskResult
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base

RISK_PARAMS_80_20 = {"max_single_position_pct": 20.0, "max_total_position_pct": 80.0,
                     "max_daily_loss_pct": 2.0, "stop_loss_pct": 6.0}


@pytest.fixture
def sql_env(monkeypatch):
    """sqlite 内存库 + sql 后端伪装 + v2_app_state 里的受控 risk_params。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    import zhice_os_v2.infrastructure.db.session as sess_mod
    monkeypatch.setattr(sess_mod, "get_session", lambda: Session())

    import zhice_os_v2.api.routers.app_state_store as store_mod
    monkeypatch.setattr(store_mod, "get_session", lambda: Session())

    # positions.py 的 get_session 是模块级绑定（from ... import get_session），
    # patch session 模块属性影响不到它——盯市取价（_get_latest_prices/_get_pre_closes）
    # 走该绑定，不单独 patch 会静默连真 PG。必须同 patch 该模块级绑定。
    import zhice_os_v2.api.routers.positions as positions_mod
    monkeypatch.setattr(positions_mod, "get_session", lambda: Session())

    import zhice_os_v2.config.settings as cfg_mod
    fake = SimpleNamespace(
        persistence_backend="sql",
        sim_initial_capital=1_000_000.0,
        sim_initial_capital_us=100_000.0,
        sim_initial_capital_hk=800_000.0,
        sim_initial_capital_crypto=100_000.0,
    )
    monkeypatch.setattr(cfg_mod, "get_settings", lambda: fake)

    def set_params(params: dict | None):
        s = Session()
        try:
            row = s.get(orm.AppStateModel, "settings")
            value = {"trade_mode": "semi_auto", "risk_params": params or RISK_PARAMS_80_20}
            if row is None:
                s.add(orm.AppStateModel(key="settings", value=value,
                                       updated_at=datetime.now(UTC)))
            else:
                row.value = value
                row.updated_at = datetime.now(UTC)
            s.commit()
        finally:
            s.close()

    set_params(RISK_PARAMS_80_20)
    return SimpleNamespace(Session=Session, set_params=set_params)


def _add_position(session, *, position_id, sv="sv-a", market="CN", ts_code="600519.SH",
                  status="OPEN", qty=1000, price=100.0, pnl=None,
                  closed_at=None, opened_at=None, account_id=None):
    session.add(orm.PositionModel(
        position_id=position_id, strategy_version_id=sv, market=market, ts_code=ts_code,
        direction="LONG", open_qty=Decimal(str(qty)), open_price=Decimal(str(price)),
        status=status, pnl_amount=Decimal(str(pnl)) if pnl is not None else None,
        opened_at=opened_at or datetime.now(UTC), closed_at=closed_at,
        account_id=account_id,
    ))


def _plan(qty=100, price=100, sv="sv-a", ts_code="600519.SH", account_id=None):
    return TradePlan(
        plan_id="p-sql", strategy_version_id=sv, market="CN", ts_code=ts_code,
        direction="BUY", qty=Decimal(str(qty)), ref_price=Decimal(str(price)),
        max_position_ratio=Decimal("0.05"), account_id=account_id,
    )


# ── PORTFOLIO_TOTAL_EXPOSURE（SQL 聚合路径，复刻 08-18 历史 85%>80% 场景）────

def test_sql_portfolio_exposure_reject(sql_env):
    s = sql_env.Session()
    try:
        _add_position(s, position_id="pos-1", qty=8400, price=100.0)  # 成本 84 万
        s.commit()
    finally:
        s.close()
    # 计划成本 100×100=1 万 → projected 85 万 / 净值 100 万 = 85% > 80%
    decision = RiskService().check_plan(_plan(), profile=get_profile("CN"), price=100)
    rr = decision.rule_results
    assert rr["PORTFOLIO_TOTAL_EXPOSURE"]["result"] == "REJECT"
    assert rr["PORTFOLIO_TOTAL_EXPOSURE"]["exposure_pct"] == "85.0"
    assert rr["PORTFOLIO_TOTAL_EXPOSURE"]["limit_pct"] == "80.0"
    assert decision.result == RiskResult.BLOCK
    assert "PORTFOLIO_EXPOSURE_EXCEEDED" in decision.reasons


def test_sql_portfolio_exposure_pass_and_realized_in_net_value(sql_env):
    s = sql_env.Session()
    try:
        _add_position(s, position_id="pos-1", qty=5000, price=100.0)  # 成本 50 万
        # 已实现盈利 +10 万 → 净值 110 万 → 50 万 + 计划 1 万 = 46.4% < 80%
        _add_position(s, position_id="pos-2", status="CLOSED", qty=1000, price=100.0,
                      pnl=100_000.0, closed_at=datetime.now(UTC) - timedelta(days=3))
        s.commit()
    finally:
        s.close()
    decision = RiskService().check_plan(_plan(), profile=get_profile("CN"), price=100)
    assert decision.rule_results["PORTFOLIO_TOTAL_EXPOSURE"]["result"] == "PASS"


def test_sql_exposure_owner_isolation(sql_env):
    """owned 计划只统计本账户持仓（Phase B 归属口径）。"""
    s = sql_env.Session()
    try:
        _add_position(s, position_id="pos-a1", account_id="acct-1", qty=6000, price=100.0)
        _add_position(s, position_id="pos-a2", account_id="acct-2", qty=6000, price=100.0)
        s.commit()
    finally:
        s.close()
    # acct-1 视角：60 万 + 1 万 = 61% < 80% → PASS
    d1 = RiskService().check_plan(_plan(account_id="acct-1"),
                                  profile=get_profile("CN"), price=100)
    assert d1.rule_results["PORTFOLIO_TOTAL_EXPOSURE"]["result"] == "PASS"
    assert d1.rule_results["PORTFOLIO_TOTAL_EXPOSURE"]["exposure_pct"] == "61.0"
    # legacy（无 owner）视角：两账户合并 120 万 + 1 万 = 121% → REJECT
    d2 = RiskService().check_plan(_plan(), profile=get_profile("CN"), price=100)
    assert d2.rule_results["PORTFOLIO_TOTAL_EXPOSURE"]["result"] == "REJECT"


def test_sql_exposure_other_strategy_excluded(sql_env):
    """敞口按 plan.strategy_version_id 过滤：其他 sv 的持仓不计入。"""
    s = sql_env.Session()
    try:
        _add_position(s, position_id="pos-other", sv="sv-b", qty=9000, price=100.0)
        s.commit()
    finally:
        s.close()
    decision = RiskService().check_plan(_plan(), profile=get_profile("CN"), price=100)
    assert decision.rule_results["PORTFOLIO_TOTAL_EXPOSURE"]["result"] == "PASS"


def test_sql_exposure_uses_settings_from_app_state(sql_env):
    """阈值真实读 v2_app_state：max_total 80→60 后同敞口 61% 由放行变拒单。"""
    s = sql_env.Session()
    try:
        _add_position(s, position_id="pos-1", qty=6000, price=100.0)
        s.commit()
    finally:
        s.close()
    sql_env.set_params({**RISK_PARAMS_80_20, "max_total_position_pct": 80.0})
    d1 = RiskService().check_plan(_plan(), profile=get_profile("CN"), price=100)
    assert d1.rule_results["PORTFOLIO_TOTAL_EXPOSURE"]["result"] == "PASS"
    sql_env.set_params({**RISK_PARAMS_80_20, "max_total_position_pct": 60.0})
    d2 = RiskService().check_plan(_plan(), profile=get_profile("CN"), price=100)
    assert d2.rule_results["PORTFOLIO_TOTAL_EXPOSURE"]["result"] == "REJECT"
    assert d2.rule_results["PORTFOLIO_TOTAL_EXPOSURE"]["limit_pct"] == "60.0"


# ── DAILY_LOSS（SQL 路径：当日已实现）────────────────────────────────────────

def test_sql_daily_loss_reject_closed_today(sql_env):
    s = sql_env.Session()
    try:
        # 今日平仓亏损 3 万 > 阈值 2%（100 万 × 2% = 2 万）
        _add_position(s, position_id="pos-c1", status="CLOSED", qty=1000, price=100.0,
                      pnl=-30_000.0, closed_at=datetime.now(UTC))
        s.commit()
    finally:
        s.close()
    decision = RiskService().check_plan(_plan(), profile=get_profile("CN"), price=100)
    rr = decision.rule_results
    assert rr["DAILY_LOSS"]["result"] == "REJECT"
    assert rr["DAILY_LOSS"]["today_pnl"] == -30000.0
    assert decision.result == RiskResult.BLOCK
    assert "DAILY_LOSS_EXCEEDED" in decision.reasons


def test_sql_daily_loss_ignores_other_days(sql_env):
    s = sql_env.Session()
    try:
        _add_position(s, position_id="pos-c2", status="CLOSED", qty=1000, price=100.0,
                      pnl=-30_000.0, closed_at=datetime.now(UTC) - timedelta(days=2))
        s.commit()
    finally:
        s.close()
    decision = RiskService().check_plan(_plan(), profile=get_profile("CN"), price=100)
    rr = decision.rule_results
    assert rr["DAILY_LOSS"]["result"] == "PASS"
    assert rr["DAILY_LOSS"]["today_pnl"] == 0.0


# ── DAILY_LOSS 盯市三形态（OPEN 持仓浮动部分，经 positions 查价链路）─────────

def _add_daily_bar(session, *, close=90.0, pre_close=110.0, ts_code="600519.SH"):
    """插一根今日日线：close 作现价（_get_daily_close_prices base），
    pre_close 作昨收（_get_pre_closes 优先最新日K pre_close 列）。"""
    session.add(orm.BarDailyModel(
        instrument_id=ts_code, market="CN", symbol=ts_code,
        trade_date=get_profile("CN").trading_date(),
        close=Decimal(str(close)), pre_close=Decimal(str(pre_close)),
        created_at=datetime.now(UTC),
    ))


def test_sql_daily_loss_mark_to_market_opened_today(sql_env):
    """形态一：当日开仓 → (现价-开仓价)×qty。开仓 100、现价 90、3000 股 → -3 万。"""
    s = sql_env.Session()
    try:
        _add_position(s, position_id="pos-m1", qty=3000, price=100.0,
                      opened_at=datetime.now(UTC))
        _add_daily_bar(s, close=90.0, pre_close=110.0)
        s.commit()
    finally:
        s.close()
    decision = RiskService().check_plan(_plan(), profile=get_profile("CN"), price=100)
    rr = decision.rule_results
    assert rr["DAILY_LOSS"]["result"] == "REJECT"
    assert rr["DAILY_LOSS"]["today_pnl"] == -30000.0
    assert decision.result == RiskResult.BLOCK


def test_sql_daily_loss_mark_to_market_prior_day_uses_pre_close(sql_env):
    """形态二：隔日持仓 → (现价-昨收)×qty。现价 90、昨收 110、2000 股 → -4 万
    （开仓价 100 不参与——昨收才是今日盯市基准，V1 口径）。"""
    s = sql_env.Session()
    try:
        _add_position(s, position_id="pos-m2", qty=2000, price=100.0,
                      opened_at=datetime.now(UTC) - timedelta(days=3))
        _add_daily_bar(s, close=90.0, pre_close=110.0)
        s.commit()
    finally:
        s.close()
    decision = RiskService().check_plan(_plan(), profile=get_profile("CN"), price=100)
    rr = decision.rule_results
    assert rr["DAILY_LOSS"]["result"] == "REJECT"
    assert rr["DAILY_LOSS"]["today_pnl"] == -40000.0


def test_sql_daily_loss_mark_to_market_missing_pre_close_skipped(sql_env):
    """形态三：隔日持仓缺昨收（无日线）→ 该笔记不计入盯市（不硬拒、不估 0 价）。
    无日线同时意味着无现价 → 现价回落开仓价 → 今日盈亏 0。"""
    s = sql_env.Session()
    try:
        _add_position(s, position_id="pos-m3", qty=1000, price=100.0,
                      opened_at=datetime.now(UTC) - timedelta(days=3))
        s.commit()
    finally:
        s.close()
    decision = RiskService().check_plan(_plan(), profile=get_profile("CN"), price=100)
    rr = decision.rule_results
    assert rr["DAILY_LOSS"]["result"] == "PASS"
    assert rr["DAILY_LOSS"]["today_pnl"] == 0.0


# ── MIN_LIQUIDITY：快照 turnover 取数（此前调用方恒传 None → 永不跑）──────────

def test_sql_liquidity_from_snapshot_turnover(sql_env):
    s = sql_env.Session()
    try:
        s.add(orm.LatestSnapshotModel(
            market="CN", symbol="600519.SH", snapshot_time=datetime.now(UTC),
            price=Decimal("100"), turnover=Decimal("561525883.1"), is_current=True,
            source="test", created_at=datetime.now(UTC),
        ))
        s.commit()
    finally:
        s.close()
    decision = RiskService().check_plan(_plan(), profile=get_profile("CN"), price=100)
    rr = decision.rule_results
    assert rr["MIN_LIQUIDITY"]["result"] == "PASS"
    assert rr["MIN_LIQUIDITY"]["turnover"] == "561525883.1"


def test_sql_liquidity_reject_low_turnover(sql_env):
    s = sql_env.Session()
    try:
        s.add(orm.LatestSnapshotModel(
            market="CN", symbol="600519.SH", snapshot_time=datetime.now(UTC),
            price=Decimal("100"), turnover=Decimal("100.0"), is_current=True,
            source="test", created_at=datetime.now(UTC),
        ))
        s.commit()
    finally:
        s.close()
    decision = RiskService().check_plan(_plan(), profile=get_profile("CN"), price=100)
    assert decision.rule_results["MIN_LIQUIDITY"]["result"] == "REJECT"
    assert "LIQUIDITY_INSUFFICIENT" in decision.reasons


def test_sql_liquidity_skipped_when_snapshot_missing(sql_env):
    """无快照 → SKIPPED 留痕（放行不硬拒）。"""
    decision = RiskService().check_plan(_plan(), profile=get_profile("CN"), price=100)
    rr = decision.rule_results
    assert rr["MIN_LIQUIDITY"]["result"] == "SKIPPED"
    assert "LIQUIDITY_INSUFFICIENT" not in decision.reasons


# ── 基础设施故障语义 ─────────────────────────────────────────────────────────

def test_sql_infra_failure_exposure_skips_freshness_fail_closed(sql_env, monkeypatch):
    """DB 故障：敞口/日损 SKIPPED 放行；DATA_FRESHNESS 保持 fail-CLOSED（历史行为）。"""
    import zhice_os_v2.infrastructure.db.session as sess_mod

    def _broken(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(sess_mod, "get_session", _broken)
    decision = RiskService().check_plan(_plan(), profile=get_profile("CN"), price=100)
    rr = decision.rule_results
    assert rr["PORTFOLIO_TOTAL_EXPOSURE"]["result"] == "SKIPPED"
    assert rr["SINGLE_SECURITY_EXPOSURE"]["result"] == "SKIPPED"
    assert rr["DAILY_LOSS"]["result"] == "SKIPPED"
    assert rr["MIN_LIQUIDITY"]["result"] == "SKIPPED"
    assert rr["DATA_FRESHNESS"]["result"] == "REJECT"
    assert decision.result == RiskResult.BLOCK
    assert "FRESHNESS_CHECK_FAILED" in decision.reasons
