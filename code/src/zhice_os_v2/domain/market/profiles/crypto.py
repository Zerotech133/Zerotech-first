"""虚拟币现货市场画像。第4市场:克隆美股模式;24/7 交易、UTC 时区、USDT 本位、无涨跌停。

「可调」阈值(费模型/价格带/成交额下限)为合理默认,端到端验证后校准。
关键差异:24x7 无休(周末恒开市)、支持分数量(allow_fractional)、结算 T+0、无每日涨跌停。
"""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from zhice_os_v2.domain.market.profile import CostModel, Session

_CRYPTO_TZ = ZoneInfo("UTC")
_CRYPTO_COST = CostModel(
    commission_rate=Decimal("0.001"),   # 现货 taker 佣金 0.1%(可调)
    stamp_tax_rate=Decimal("0"),        # 加密无印花税
    transfer_fee_rate=Decimal("0"),     # 无过户费
    min_commission=Decimal("0"),        # 无最低佣金
    slippage_pct=Decimal("0.001"),      # 0.1% 滑点(可调)
)


class CryptoProfile:
    market = "CRYPTO"
    tz = _CRYPTO_TZ
    is_24x7 = True                       # 7x24 无休(新增,与其他市场并列)
    allow_fractional = True              # 支持分数量下单(新增,与其他市场并列)
    default_benchmark = "BTC/USDT"       # 基准:BTC;数据源须能取
    settlement = "T+0"                   # 加密现货即时结算
    cost_model = _CRYPTO_COST
    currency = "USDT"                    # USDT 本位;禁跨币种求和
    same_day_sell_allowed = True         # 加密 T+0,当日可卖

    def exchanges(self) -> tuple[str, ...]:
        return ("BINANCE",)

    def lot_size(self, ts_code: str | None = None) -> int:
        return 1                         # 加密支持分数量,整手概念不适用,返回 1

    def price_limit_pct(self, ts_code: str, name: str | None) -> Decimal | None:
        return None                      # 加密无每日涨跌停

    def exchange_of(self, symbol: str) -> str:
        return "BINANCE"

    def board_of(self, symbol: str) -> str:
        return "CRYPTO"

    def to_native_symbol(self, code: str) -> str:
        return str(code)                 # BTC/USDT 即原生 symbol,原样返回

    def is_st_like(self, name: str | None) -> bool:
        return False                     # 加密无 ST;卫生过滤由价格带兜底

    def sessions(self) -> list[Session]:
        return [Session(time(0, 0), time(23, 59))]   # 全天段(24/7)

    def trading_date(self, now: datetime | None = None) -> date:
        now = now or datetime.now(tz=self.tz)
        return now.astimezone(self.tz).date()

    def fallback_is_open(self, d: date) -> bool:
        return True                      # 加密 7x24,周末/节假日恒开市

    def calendar_exchange(self) -> str:
        return "CRYPTO"

    def candidate_price_band(self) -> tuple[Decimal, Decimal]:
        return (Decimal("0"), Decimal("1000000000000"))   # 价格跨度极大,极宽容(可调)

    def candidate_min_turnover(self) -> Decimal:
        return Decimal("10000000")       # 候选日成交额下限 1000 万 USDT(可调)

    # ---- TrendMomentum 选股阈值 crypto 化（默认 CN 常量对币偏严，此处放宽；可校准）----
    def trend_min_avg_turnover_20d(self) -> float:
        return 10_000_000.0              # 20日均额门槛 1000万 USDT(CN=30M;币成交额大,门槛非主约束)

    def trend_momentum_band(self) -> tuple[float, float]:
        return (0.03, 3.0)               # 动量带(CN=[0.05,0.50]);币波动大,放宽上限至300%避免误杀强势币

    def trend_min_bars(self) -> int:
        return 26                        # 最小 bars 同 CN(保留;分钟/日线足量)

    def risk_price_guardrail(self) -> tuple[Decimal, Decimal]:
        return (Decimal("0"), Decimal("10000000000"))   # 风控价格红线(可调)

    def risk_min_turnover(self) -> Decimal:
        return Decimal("1000000")        # 风控流动性下限 100 万 USDT(可调)

    def intraday_entry_window(self) -> tuple[str, str]:
        return ("00:00", "23:59")        # 24/7 全天可入场(UTC tz);可调

    def intraday_entry_band(self) -> tuple[Decimal, Decimal]:
        return (Decimal("-0.05"), Decimal("0.08"))   # 加密波动大,放宽容忍带;可调

    def requires_intraday_entry_confirmation(self) -> bool:
        # 加密不走 CN 那套盘中动量确认门(站上开盘价/较上周期走强/不接刀=追涨逻辑),
        # 与均值回归抄底相反。候选(如 RSI<30 已选出) + can_buy + 风控通过即建仓。
        return False

    def max_position_ratio(self) -> Decimal:
        return Decimal("0.10")        # 单笔仓位上限 10%(默认5%)

    def max_total_exposure_pct(self) -> float:
        return 0.50                   # 账户总仓位上限 50%(默认80%)

    def max_single_security_pct(self) -> float:
        return 0.50                   # 单标的累计上限 50%(允许同币多次建仓;默认~10%/20%)

    def settlement_source(self) -> str | None:
        return None                      # CRYPTO 不加同日源过滤
