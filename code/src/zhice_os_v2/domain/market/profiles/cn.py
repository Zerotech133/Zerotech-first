"""A股市场画像。阶段0:数值与散落的现有真源逐一对齐,行为不变。"""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from zhice_os_v2.domain.market.profile import CostModel, Session

_CN_TZ = ZoneInfo("Asia/Shanghai")
_CN_COST = CostModel(
    commission_rate=Decimal("0.0003"),   # 万三(双边) — simulated_broker.py:21
    stamp_tax_rate=Decimal("0.0005"),    # 印花税(仅卖) — simulated_broker.py:22
    transfer_fee_rate=Decimal("0"),      # A股过户费当前未建模
    min_commission=Decimal("0"),         # A股最低5元当前未建模
    slippage_pct=Decimal("0.001"),       # 0.1% — simulated_broker.py:23
)
_ST_MARKERS = ("ST", "st", "退", "*")    # trend_momentum.py:33 口径
_SUFFIX_TO_EXCHANGE = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}   # ts_code 后缀→交易所


class CNProfile:
    market = "CN"
    tz = _CN_TZ
    default_benchmark = "000300.SH"
    settlement = "T+1"
    cost_model = _CN_COST
    currency = "CNY"
    same_day_sell_allowed = False

    def exchanges(self) -> tuple[str, ...]:
        return ("SSE", "SZSE")

    def lot_size(self, ts_code: str | None = None) -> int:
        return 100

    def price_limit_pct(self, ts_code: str, name: str | None) -> Decimal:
        # 逐字等值于改造前 market_rules.limit_pct;板块分类内部化到 board_of。
        board = self.board_of(ts_code)
        if board in ("GEM", "STAR"):
            return Decimal("0.20")
        if board == "BSE":
            return Decimal("0.30")
        if "ST" in (name or "").upper():
            return Decimal("0.05")
        return Decimal("0.10")

    def exchange_of(self, symbol: str) -> str:
        # ts_code 后缀 → 交易所;无后缀返回空串(承 gateway/adapter 现有口径)
        parts = (symbol or "").split(".")
        suffix = (parts[1] if len(parts) > 1 else "").upper()
        return _SUFFIX_TO_EXCHANGE.get(suffix, "")

    def board_of(self, symbol: str) -> str:
        # 前缀 → 板块枚举;边界逐字对齐改造前 price_limit_pct 的前缀判断
        code = (symbol or "").split(".")[0]
        if code.startswith(("300", "301")):
            return "GEM"    # 创业板
        if code.startswith("688"):
            return "STAR"   # 科创板
        if code.startswith(("8", "4", "92")):
            return "BSE"    # 北交所
        return "MAIN"       # 主板

    def to_native_symbol(self, code: str) -> str:
        # 裸 6 位代码 → ts_code;逐字搬自 sync_universe.code_to_ts 的生成规则
        code = str(code).zfill(6)
        if code.startswith("6"):
            return f"{code}.SH"
        if code[0] in ("0", "3"):
            return f"{code}.SZ"
        return f"{code}.BJ"

    def is_st_like(self, name: str | None) -> bool:
        if not name:
            return True   # 空名 fail-safe:无名标的当作ST剔除 — trend_momentum.py 口径
        return any(m in name for m in _ST_MARKERS)

    def sessions(self) -> list[Session]:
        return [Session(time(9, 30), time(11, 30)), Session(time(13, 0), time(15, 0))]

    def trading_date(self, now: datetime | None = None) -> date:
        now = now or datetime.now(tz=self.tz)
        return now.astimezone(self.tz).date()

    def fallback_is_open(self, d: date) -> bool:
        # A股: 无日历数据时以"周一~周五"回退(节假日需日历覆盖) — trading_calendar.py:72 口径
        return d.weekday() < 5

    def calendar_exchange(self) -> str:
        return "SSE"   # A股交易日历取上交所 — trading_calendar.py:30

    def candidate_price_band(self) -> tuple[Decimal, Decimal]:
        # 候选池入池价格带(策略选股+生成器兜底统一) — 0-B决策:上限统一到500
        return (Decimal("3.0"), Decimal("500.0"))

    def candidate_min_turnover(self) -> Decimal:
        return Decimal("5_000_000")   # 候选池单日成交额下限500万 — candidate_generator.py:26

    def risk_price_guardrail(self) -> tuple[Decimal, Decimal]:
        # 风控价格告警红线(宽松兜底,block_on_fail=False) — risk_check_service.py:181
        return (Decimal("2.0"), Decimal("1000.0"))

    def risk_min_turnover(self) -> Decimal:
        return Decimal("3_000_000")   # 风控流动性阻断下限300万 — risk_check_service.py:197

    def intraday_entry_window(self) -> tuple[str, str]:
        return ("09:35", "14:45")   # entry_signal.ENTRY_WINDOW 逐字

    def intraday_entry_band(self) -> tuple[Decimal, Decimal]:
        return (Decimal("-0.02"), Decimal("0.05"))   # ENTRY_MIN_PCT/ENTRY_MAX_PCT 逐字

    def settlement_source(self) -> str | None:
        return "tushare"   # 同日只信 tushare 结算价 — valuation.py 口径
