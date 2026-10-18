"""A股交易可行性规则：涨跌停板 / 停牌 → 这一笔卖得掉吗。

盘中出场（止盈/止损）成交前用于判断"能否卖出"，避免模拟盘在极端行情里
"顺利止损"而现实中根本卖不掉（千股跌停封死、停牌冻结），给出虚假安全感。

判据（仅用现价/昨收/快照时间，因 DataCenter 买卖盘一档几乎不填充）：
  - 停牌/数据失联：价≤0 或快照超 STALE_MINUTES 未更新 → 卖不掉
  - 跌停封死：现价 ≤ 昨收×(1−涨跌停幅) → 卖不掉（保守假设封死无买盘、不可成交）
  - 涨停(limit-up)不阻止卖出（有买盘），故只判跌停。
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

STALE_MINUTES = 30  # 快照超过此分钟未更新 → 疑似停牌/数据失联


def limit_pct(ts_code: str, name: str | None, market: str = "CN") -> Decimal | None:
    """当日涨跌停幅度(小数)。按 market 取 profile;US 等无涨跌停市场返 None。CN 默认逐字节不变。"""
    from zhice_os_v2.domain.market.registry import get_profile
    return get_profile(market).price_limit_pct(ts_code, name)


def limit_down_price(pre_close, ts_code: str, name: str | None, market: str = "CN") -> Decimal | None:
    """跌停价 = 昨收 ×(1 − 涨跌停幅)，两位小数。昨收缺失或无涨跌停(如美股)返回 None。"""
    if pre_close is None or float(pre_close) <= 0:
        return None
    lp = limit_pct(ts_code, name, market=market)
    if lp is None:            # 无涨跌停市场:无跌停价概念
        return None
    pc = Decimal(str(pre_close))
    return (pc * (Decimal("1") - lp)).quantize(Decimal("0.01"))


def can_sell(price, pre_close, ts_code: str, name: str | None,
             snapshot_time: datetime | None = None,
             now: datetime | None = None,
             market: str = "CN") -> tuple[bool, str]:
    """能否卖出。返回 (可卖, 原因)。"""
    if price is None or float(price) <= 0:
        return False, "停牌/无行情(价≤0)"

    if snapshot_time is not None:
        now = now or datetime.now(tz=UTC)
        st = snapshot_time if snapshot_time.tzinfo else snapshot_time.replace(tzinfo=UTC)
        age_min = (now - st).total_seconds() / 60
        if age_min > STALE_MINUTES:
            return False, f"疑似停牌(快照{age_min:.0f}分钟未更新)"

    ld = limit_down_price(pre_close, ts_code, name, market=market)
    if ld is not None and Decimal(str(price)) <= ld:
        return False, f"跌停封死(现价{float(price):.2f}≤跌停{float(ld):.2f})"

    return True, "可卖"


def limit_up_price(pre_close, ts_code: str, name: str | None, market: str = "CN") -> Decimal | None:
    """涨停价 = 昨收 ×(1 + 涨跌停幅)，两位小数。昨收缺失或无涨跌停(如美股)返回 None。"""
    if pre_close is None or float(pre_close) <= 0:
        return None
    lp = limit_pct(ts_code, name, market=market)
    if lp is None:
        return None
    pc = Decimal(str(pre_close))
    return (pc * (Decimal("1") + lp)).quantize(Decimal("0.01"))


def can_buy(price, pre_close, ts_code: str, name: str | None,
            snapshot_time: datetime | None = None,
            now: datetime | None = None,
            market: str = "CN") -> tuple[bool, str]:
    """能否买入。涨停封死(无卖盘)/停牌 → 买不进；跌停不阻止买入(有卖盘)。返回 (可买, 原因)。"""
    if price is None or float(price) <= 0:
        return False, "停牌/无行情(价≤0)"

    if snapshot_time is not None:
        now = now or datetime.now(tz=UTC)
        st = snapshot_time if snapshot_time.tzinfo else snapshot_time.replace(tzinfo=UTC)
        age_min = (now - st).total_seconds() / 60
        if age_min > STALE_MINUTES:
            return False, f"疑似停牌(快照{age_min:.0f}分钟未更新)"

    lu = limit_up_price(pre_close, ts_code, name, market=market)
    if lu is not None and Decimal(str(price)) >= lu:
        return False, f"涨停封死(现价{float(price):.2f}≥涨停{float(lu):.2f})"

    return True, "可买"
