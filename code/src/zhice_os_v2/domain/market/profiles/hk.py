"""港股市场画像。阶段1:定义港股规则语义,克隆美股模式;不接数据/不改schema。

「可调」阈值(费模型/价格带/成交额下限)为合理默认,端到端验证后校准。
关键差异:含午休双段(sessions),T+2 结算,HKD 本位,印花税双边 0.13%,无涨跌停。
"""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from zhice_os_v2.domain.market.profile import CostModel, Session

_HK_TZ = ZoneInfo("Asia/Hong_Kong")
_HK_COST = CostModel(
    commission_rate=Decimal("0.0005"),   # 佣金率(双边),可调
    stamp_tax_rate=Decimal("0.0013"),    # 港股印花税双边 0.13%
    transfer_fee_rate=Decimal("0"),      # 过户费当前未建模
    min_commission=Decimal("0"),         # 最低佣金当前未建模
    slippage_pct=Decimal("0.001"),       # 0.1% 滑点(可调)
)


class HKProfile:
    market = "HK"
    tz = _HK_TZ
    default_benchmark = "^HSI"          # 恒生指数;数据源须能取
    settlement = "T+2"                  # 港股 T+2 结算
    cost_model = _HK_COST
    currency = "HKD"
    same_day_sell_allowed = True     # 港股 T+0 交易(T+2 结算),当日可卖

    def exchanges(self) -> tuple[str, ...]:
        return ("HKEX",)

    def lot_size(self, ts_code: str | None = None) -> int:
        # 港股每手股数因股票而异（HKEX 官方值）；实际下单量由 position_sizing_service
        # 按标的查 v2_instrument.lot_size 解析，此处仅为查不到时的回退（1=不额外取整）。
        return 1

    def price_limit_pct(self, ts_code: str, name: str | None) -> Decimal | None:
        return None                     # 港股无每日涨跌停

    def exchange_of(self, symbol: str) -> str:
        return "HKEX"

    def board_of(self, symbol: str) -> str:
        return "MAIN"                   # 港股无板块概念

    def to_native_symbol(self, code: str) -> str:
        return str(code)                # 港股 5 位码即原生 symbol,原样返回

    def is_st_like(self, name: str | None) -> bool:
        return False                    # 港股无 ST;卫生过滤由价格带兜底

    def sessions(self) -> list[Session]:
        # 港股含午休:上午 09:30-12:00,下午 13:00-16:00(双段,关键差异)
        return [Session(time(9, 30), time(12, 0)), Session(time(13, 0), time(16, 0))]

    def trading_date(self, now: datetime | None = None) -> date:
        now = now or datetime.now(tz=self.tz)
        return now.astimezone(self.tz).date()

    def fallback_is_open(self, d: date) -> bool:
        return d.weekday() < 5          # 无日历数据时周一~五回退;节假日靠日历

    def calendar_exchange(self) -> str:
        return "HKEX"

    def candidate_price_band(self) -> tuple[Decimal, Decimal]:
        return (Decimal("1.0"), Decimal("100000.0"))   # 仙股下限1港元;上限极宽容(可调)

    def candidate_min_turnover(self) -> Decimal:
        return Decimal("10_000_000")    # 候选日成交额下限1000万港元(可调)

    def risk_price_guardrail(self) -> tuple[Decimal, Decimal]:
        return (Decimal("0.5"), Decimal("1000000.0"))  # 风控价格红线(可调)

    def risk_min_turnover(self) -> Decimal:
        return Decimal("3_000_000")     # 风控流动性下限300万港元(可调)

    def intraday_entry_window(self) -> tuple[str, str]:
        return ("09:35", "15:45")    # 开盘噪音后~收盘前(HK tz);可调,验收后校准

    def intraday_entry_band(self) -> tuple[Decimal, Decimal]:
        return (Decimal("-0.02"), Decimal("0.05"))   # 无涨跌停,沿用动量带;可调

    def settlement_source(self) -> str | None:
        return None   # HK 日线不加同日源过滤
