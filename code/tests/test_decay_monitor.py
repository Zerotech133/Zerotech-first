from datetime import UTC, datetime, timedelta
from decimal import Decimal

from zhice_os_v2.application.services.decay_monitor import DecayMonitor
from zhice_os_v2.domain.replay.replay import ReplayCase, ReviewStatus
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase


def _replay(i: int, pnl: int, created: datetime) -> ReplayCase:
    return ReplayCase(
        replay_id=f"r-{i}",
        strategy_version_id="sv-1",
        market="CN",
        ts_code="600519.SH",
        pnl=Decimal(str(pnl)),
        pnl_pct=Decimal("0.01") if pnl > 0 else Decimal("-0.01"),
        holding_period_seconds=60,
        exit_reason="EXIT",
        status=ReviewStatus.PENDING,
        created_at=created,
    )


def test_decay_detected():
    db = InMemoryDatabase()
    base = datetime.now(UTC)
    # 前 10 笔全赢
    for i in range(10):
        db.save_replay(_replay(i, 1, base + timedelta(minutes=i)))
    # 后 10 笔全亏
    for i in range(10, 20):
        db.save_replay(_replay(i, -1, base + timedelta(minutes=i)))
    result = DecayMonitor(db).monitor(threshold=0.3)
    assert len(result) == 1
    assert result[0]["status"] == "DECAY"
