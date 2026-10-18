from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from zhice_os_v2.domain.market.context import MarketContext
from zhice_os_v2.domain.market.profile import CostModel, MarketProfile, Session


@dataclass(frozen=True)
class FakeProfile:
    market: str = "CN"
    tz: ZoneInfo = ZoneInfo("Asia/Shanghai")
    currency: str = "CNY"
    settlement: str = "T+1"
    cost_model: CostModel = CostModel(Decimal("0.0003"))

    def exchanges(self) -> tuple[str, ...]:
        return ("SSE", "SZSE")

    def lot_size(self, symbol: str | None = None) -> int:
        return 100

    def price_limit_pct(self, symbol: str, name: str | None = None) -> Decimal | None:
        return Decimal("0.10")

    def sessions(self) -> list[Session]:
        return [Session(time(9, 30), time(15, 0))]

    def trading_date(self, now=None) -> date:
        return date(2026, 8, 18)

    def fallback_is_open(self, d: date) -> bool:
        return d.weekday() < 5


def test_market_context_holds_profile():
    profile: MarketProfile = FakeProfile()
    ctx = MarketContext(market="CN", profile=profile, currency="CNY", tz=profile.tz)
    assert ctx.market == "CN"
    assert ctx.profile.lot_size() == 100
    assert ctx.profile.price_limit_pct("600519.SH") == Decimal("0.10")
