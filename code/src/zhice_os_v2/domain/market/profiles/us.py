"""美股市场画像。阶段1-A:定义美股规则语义,不接数据/不改schema。

「可调」阈值(费模型/价格带/成交额下限)为合理默认,阶段1-D端到端验证后校准。
"""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from zhice_os_v2.domain.market.profile import CostModel, Session

_US_TZ = ZoneInfo("America/New_York")
_US_COST = CostModel(
    commission_rate=Decimal("0"),      # 零佣金券商口径
    stamp_tax_rate=Decimal("0"),       # 美股无印花税
    transfer_fee_rate=Decimal("0"),    # 无过户费
    min_commission=Decimal("0"),       # 无最低佣金
    slippage_pct=Decimal("0.001"),     # 0.1% 滑点(可调)
)


class USProfile:
    market = "US"
    tz = _US_TZ
    default_benchmark = "^GSPC"        # 标普500;1-C数据源须能取
    settlement = "T+1"                 # 美股2024-05起T+1
    cost_model = _US_COST
    currency = "USD"
    same_day_sell_allowed = True    # 美股 T+0 交易(T+1 结算),当日可卖

    def exchanges(self) -> tuple[str, ...]:
        return ("NYSE", "NASDAQ", "AMEX")

    def lot_size(self, ts_code: str | None = None) -> int:
        return 1                       # 美股无整手

    def price_limit_pct(self, ts_code: str, name: str | None) -> Decimal | None:
        return None                    # 美股无每日涨跌停

    def exchange_of(self, symbol: str) -> str:
        return ""                      # 美股ticker不编码交易所;真实交易所由security主数据带(1-C)

    def board_of(self, symbol: str) -> str:
        return "MAIN"                  # 美股无板块概念

    def to_native_symbol(self, code: str) -> str:
        return str(code)               # 美股ticker即原生符号,原样返回(含BRK.B的点)

    def is_st_like(self, name: str | None) -> bool:
        return False                   # 美股无ST;卫生过滤由价格带兜底

    def sessions(self) -> list[Session]:
        return [Session(time(9, 30), time(16, 0))]   # 常规时段单段无午休

    def trading_date(self, now: datetime | None = None) -> date:
        now = now or datetime.now(tz=self.tz)
        return now.astimezone(self.tz).date()

    def fallback_is_open(self, d: date) -> bool:
        return d.weekday() < 5         # 无日历数据时周一~五回退;节假日靠日历(1-C)

    def calendar_exchange(self) -> str:
        return "NYSE"

    def candidate_price_band(self) -> tuple[Decimal, Decimal]:
        return (Decimal("5.0"), Decimal("100000.0"))   # 排除penny股;上限极宽容(可调)

    def candidate_min_turnover(self) -> Decimal:
        return Decimal("5_000_000")    # 候选日成交额下限500万美元(可调)

    def risk_price_guardrail(self) -> tuple[Decimal, Decimal]:
        return (Decimal("1.0"), Decimal("1000000.0"))  # 风控价格红线(可调)

    def risk_min_turnover(self) -> Decimal:
        return Decimal("1_000_000")    # 风控流动性下限100万美元(可调)

    def intraday_entry_window(self) -> tuple[str, str]:
        return ("09:35", "15:45")   # 开盘噪音后~收盘前(NY tz);可调,验收后校准

    def intraday_entry_band(self) -> tuple[Decimal, Decimal]:
        return (Decimal("-0.02"), Decimal("0.05"))   # 无涨跌停,沿用动量带;可调

    def settlement_source(self) -> str | None:
        return None   # US 日线无 tushare/快照歧义,不加同日源过滤
