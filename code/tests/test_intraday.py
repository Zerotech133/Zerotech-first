"""盘中工作流测试：止损/止盈自动平仓 + T+1 锁 + 跌停阻断 + 移动止损只上移。"""
from datetime import timedelta
from decimal import Decimal

from zhice_os_v2.application.services import workflow_service as wf
from zhice_os_v2.application.services.workflow_service import WorkflowService
from zhice_os_v2.domain.ontology.objects import utc_now
from zhice_os_v2.domain.portfolio.position import PositionLot, PositionStatus
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

SV = "sv-1"
MARKET = "CN"
CODE = "600519.SH"


def _lot(position_id: str = "pos-1", price: str = "10", qty: str = "100", opened_at=None) -> PositionLot:
    return PositionLot(
        position_id=position_id,
        strategy_version_id=SV,
        market=MARKET,
        ts_code=CODE,
        direction="BUY",
        open_qty=Decimal(qty),
        open_price=Decimal(price),
        opened_at=opened_at or utc_now() - timedelta(days=5),
    )


def _make_service(monkeypatch, lots, prices, pre_closes=None,
                  pos_stops=None, plan_exits=None):
    """InMemory 库 + 全部外部数据访问打桩（行情/昨收/持仓止损/计划出场/落库）。"""
    db = InMemoryDatabase()
    for lot in lots:
        db.save_position(lot)
    monkeypatch.setattr("zhice_os_v2.api.routers.positions._get_latest_prices",
                        lambda symbols, market: prices)
    monkeypatch.setattr("zhice_os_v2.api.routers.positions._get_pre_closes",
                        lambda symbols, market: pre_closes or {})
    monkeypatch.setattr(wf, "_load_position_stops", lambda ids: pos_stops or {})
    monkeypatch.setattr(wf, "_load_plan_exits", lambda codes, market: plan_exits or {})
    monkeypatch.setattr(wf, "_load_security_names", lambda symbols, market: {})
    monkeypatch.setattr(wf, "_persist_position_pnl", lambda pid, amount, pct: True)
    saved_stops: list[tuple[str, Decimal]] = []
    monkeypatch.setattr(wf, "_save_position_stop",
                        lambda pid, stop: saved_stops.append((pid, stop)) or True)
    return db, WorkflowService(db), saved_stops


# ---------- 止损 ----------

def test_stop_loss_triggers_close(monkeypatch):
    lot = _lot()
    db, svc, _ = _make_service(
        monkeypatch, [lot],
        prices={CODE: 9.4}, pre_closes={CODE: 10.4},  # 跌停 9.36 < 9.4 → 可卖
        pos_stops={"pos-1": Decimal("9.5")},
    )
    r = svc.run_intraday(MARKET)
    assert r["checked"] == 1
    assert r["stop_loss_triggered"] == [CODE]
    assert r["take_profit_triggered"] == []
    assert r["blocked"] == []
    closed = db.positions["pos-1"]
    assert closed.status == PositionStatus.CLOSED
    assert closed.close_price == Decimal("9.4")
    assert closed.close_qty == Decimal("100")
    assert closed.closed_at is not None
    assert r["replay_created"] == 1
    assert "replay-pos-1" in db.replays


def test_default_stop_loss_when_unconfigured(monkeypatch):
    """无持仓止损/无计划止损 → 默认 open*0.94=9.4，现价 9.35 触发。"""
    lot = _lot()
    db, svc, _ = _make_service(monkeypatch, [lot], prices={CODE: 9.35}, pre_closes={CODE: 10.0})
    r = svc.run_intraday(MARKET)
    assert r["stop_loss_triggered"] == [CODE]
    assert db.positions["pos-1"].status == PositionStatus.CLOSED


# ---------- 止盈 ----------

def test_take_profit_triggers_close(monkeypatch):
    lot = _lot()
    db, svc, _ = _make_service(
        monkeypatch, [lot],
        prices={CODE: 12.5}, pre_closes={CODE: 10.0},
        plan_exits={(CODE, SV): (Decimal("9.0"), Decimal("12.0"))},
    )
    r = svc.run_intraday(MARKET)
    assert r["take_profit_triggered"] == [CODE]
    assert r["stop_loss_triggered"] == []
    closed = db.positions["pos-1"]
    assert closed.status == PositionStatus.CLOSED
    assert closed.close_price == Decimal("12.5")
    assert r["replay_created"] == 1
    assert "replay-pos-1" in db.replays


# ---------- T+1 ----------

def test_t_plus_one_blocks_today_position(monkeypatch):
    lot = _lot(opened_at=utc_now())  # 当日（CN 时区）开仓
    db, svc, _ = _make_service(
        monkeypatch, [lot],
        prices={CODE: 9.0}, pre_closes={CODE: 10.0},
        pos_stops={"pos-1": Decimal("9.5")},
    )
    r = svc.run_intraday(MARKET)
    assert r["stop_loss_triggered"] == []
    assert db.positions["pos-1"].status == PositionStatus.OPEN
    assert any("T+1" in b["reason"] for b in r["blocked"])


# ---------- 跌停封死 ----------

def test_limit_down_blocks_exit(monkeypatch):
    # 主板 10%：昨收 10 → 跌停 9.0；现价 8.8 跌停封死 → 止损触发但不可成交顺延
    lot = _lot()
    db, svc, _ = _make_service(
        monkeypatch, [lot],
        prices={CODE: 8.8}, pre_closes={CODE: 10.0},
        pos_stops={"pos-1": Decimal("9.5")},
    )
    r = svc.run_intraday(MARKET)
    assert r["stop_loss_triggered"] == []
    assert db.positions["pos-1"].status == PositionStatus.OPEN
    assert r["blocked"] and r["blocked"][0]["ts_code"] == CODE
    assert "跌停" in r["blocked"][0]["reason"]


# ---------- 移动止损 ----------

def test_trailing_stop_only_moves_up(monkeypatch):
    # 现价跟随语义：默认策略 activate=3%/trail=1.5% → new_stop = 现价×0.985
    # 现价 10.9 → new_stop = 10.9×0.985 = 10.7365
    new_stop = Decimal("10.9") * (Decimal("1") - Decimal("0.015"))
    up = _lot(position_id="pos-up")      # 旧止损 10.2 < 10.7365 → 上移
    stay = _lot(position_id="pos-stay")  # 旧止损 10.8 > 10.7365 → 不动
    db, svc, saved = _make_service(
        monkeypatch, [up, stay],
        prices={CODE: 10.9}, pre_closes={CODE: 10.0},  # 浮盈 9% ≥ activate 3%
        pos_stops={"pos-up": Decimal("10.2"), "pos-stay": Decimal("10.8")},
    )
    r = svc.run_intraday(MARKET)
    assert len(r["trailing_updated"]) == 1
    entry = r["trailing_updated"][0]
    assert entry["position_id"] == "pos-up"
    assert entry["new_stop"] == new_stop
    assert saved == [("pos-up", new_stop)]  # 只写回上移的那笔
    # 均未触发平仓
    assert r["stop_loss_triggered"] == []
    assert db.positions["pos-up"].status == PositionStatus.OPEN
    assert db.positions["pos-stay"].status == PositionStatus.OPEN


def test_trailing_stop_not_activated_below_8pct(monkeypatch):
    # 默认出场策略 activate=3%；浮盈 2% < 3% → 不启动移动止损
    lot = _lot()
    db, svc, saved = _make_service(
        monkeypatch, [lot],
        prices={CODE: 10.2}, pre_closes={CODE: 10.0},  # 浮盈 2% < activate 3%
        pos_stops={"pos-1": Decimal("9.4")},
    )
    r = svc.run_intraday(MARKET)
    assert r["trailing_updated"] == []
    assert saved == []
    assert db.positions["pos-1"].status == PositionStatus.OPEN


# ---------- Task C：出场按策略读 ExitPolicy（含现价跟随移动止损）----------

from zhice_os_v2.domain.strategy.exit_policies import ExitPolicy


def _patch_policy(monkeypatch, policy):
    monkeypatch.setattr(wf, "get_exit_policy_for_version",
                        lambda sv_id, db: policy, raising=False)


def test_take_profit_uses_strategy_policy(monkeypatch):
    """CHAN 止盈 18%：现价 = 开仓价 ×1.18 触发止盈（旧硬编码 20% 不会触发）。"""
    policy = ExitPolicy(stop_loss_pct=Decimal("0.06"), take_profit_pct=Decimal("0.18"),
                        use_trailing=True, trailing_activate_pct=Decimal("0.05"),
                        trailing_trail_pct=Decimal("0.035"))
    lot = _lot(price="10")
    db, svc, _ = _make_service(monkeypatch, [lot],
                               prices={CODE: 11.8}, pre_closes={CODE: 10.0})
    _patch_policy(monkeypatch, policy)
    r = svc.run_intraday(MARKET)
    assert r["take_profit_triggered"] == [CODE]


def test_trailing_uses_current_price_semantics(monkeypatch):
    """移动止损现价跟随：浮盈≥activate 时 new_stop = 现价×(1−trail_pct)。"""
    policy = ExitPolicy(stop_loss_pct=Decimal("0.06"), take_profit_pct=Decimal("0.50"),
                        use_trailing=True, trailing_activate_pct=Decimal("0.05"),
                        trailing_trail_pct=Decimal("0.035"))
    lot = _lot(price="10")
    db, svc, saved = _make_service(monkeypatch, [lot],
                                   prices={CODE: 11.0}, pre_closes={CODE: 10.0})
    _patch_policy(monkeypatch, policy)
    svc.run_intraday(MARKET)
    # 现价 11，trail 3.5% → 11×0.965 = 10.615
    assert saved and saved[-1][1] == Decimal("11.0") * (Decimal("1") - Decimal("0.035"))


def test_mean_reversion_no_trailing(monkeypatch):
    """use_trailing=False 时不产生移动止损写回。"""
    policy = ExitPolicy(stop_loss_pct=Decimal("0.04"), take_profit_pct=Decimal("0.50"),
                        use_trailing=False, trailing_activate_pct=Decimal("0.03"),
                        trailing_trail_pct=Decimal("0.015"))
    lot = _lot(price="10")
    db, svc, saved = _make_service(monkeypatch, [lot],
                                   prices={CODE: 11.0}, pre_closes={CODE: 10.0})
    _patch_policy(monkeypatch, policy)
    svc.run_intraday(MARKET)
    assert saved == []
