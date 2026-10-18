"""计划状态同步测试（P3-1 修复）。

背景：盘前自动批准原只改 approval 表,plan_status 永挂 PENDING → "待授权"虚高。
修复给 PersistencePort 加 update_trade_plan_status,自动批准后同步计划状态。
本测试锁定该 port 方法契约(memory 实现;真库实现同为 UPDATE plan_status where plan_id)。
"""
from __future__ import annotations

from decimal import Decimal

from zhice_os_v2.domain.planning.trade_plan import PlanStatus, TradePlan
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase


def _plan(pid: str) -> TradePlan:
    return TradePlan(
        plan_id=pid, strategy_version_id="v1-cn-sv-1", market="CN",
        ts_code="600000.SH", direction="BUY", qty=Decimal("100"), ref_price=Decimal("10"),
    )


def test_update_trade_plan_status_transitions():
    db = InMemoryDatabase()
    db.save_trade_plan(_plan("p1"))
    assert db.get_trade_plan("p1").status == PlanStatus.PENDING  # 默认
    db.update_trade_plan_status("p1", "APPROVED")
    assert db.get_trade_plan("p1").status == PlanStatus.APPROVED
    db.update_trade_plan_status("p1", "EXECUTED")
    assert db.get_trade_plan("p1").status == PlanStatus.EXECUTED


def test_update_trade_plan_status_missing_id_noop():
    """不存在的 plan_id 静默 no-op（不抛错）。"""
    db = InMemoryDatabase()
    db.update_trade_plan_status("nonexistent", "APPROVED")  # 不应抛异常
    assert db.get_trade_plan("nonexistent") is None
