"""缠论动态出场：跌破最近日线笔中枢下沿（zd）即离场。"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def price_breaks_pivot_low(zs_list: list[dict], current_price: float) -> bool:
    """现价是否跌破最近有效中枢的下沿 zd（严格小于）。无中枢返回 False。"""
    if not zs_list:
        return False
    last_zd = zs_list[-1].get("zd")
    if last_zd is None:
        return False
    return current_price < float(last_zd)


def evaluate_position_pivot_exit(ts_code: str, df, current_price: float) -> bool:
    """对单笔持仓：用日线 df 现算笔中枢，判断现价是否跌破下沿。

    df 需含 dt/open/high/low/close/volume 列（时间升序）。
    amount 列缺失时自动补 close*volume 以满足 czsc RawBar 构造要求。
    build_zs_list 接收 czsc BI 对象列表（czsc_obj.bi_list），非笔字典列表。
    """
    from zhice_os_v2.domain.strategy.strategies.chan.engine import (
        build_zs_list,
        extract_bi_list,
    )

    df = df.copy()
    if "amount" not in df.columns:
        df["amount"] = df["close"].astype(float) * df["volume"].astype(float)

    try:
        _, czsc_obj = extract_bi_list(ts_code, df)
        zs_list = build_zs_list(czsc_obj.bi_list)  # type: ignore[attr-defined]
    except Exception:
        logger.warning("chan_zs eval failed ts=%s", ts_code, exc_info=True)
        return False

    return price_breaks_pivot_low(zs_list, current_price)
