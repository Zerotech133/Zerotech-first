"""成交流水对账：扫「CLOSED 持仓无 SELL 流水」「持仓无 BUY 流水」。

背景（603311 案，2026-09-18）：两类缺陷曾致流水静默丢失——
- Bug A：_record_fill 外层 except 吞异常（9-18 窗口 8 笔）；
- Bug B：/positions/close 一键平仓不写流水（9-02 同秒 11 笔）。
两者均已修，本对账 job 是兜底哨兵：再有丢失，最迟一个周期内告警可见。

关联口径：fill 与持仓无外键直连，可靠途径是执行链 idempotency_key——
平仓 request 存 'close-{position_id}'、建仓存 position_id 去 'pos-' 前缀，
两列（varchar(128)）均未被 _fit 截断，比解析 fill_id 字符串可靠。
排除 V1 导入（v1-%）与旧 sim（pos-sim-%）持仓：其成交在 V1 系统，无 V2
流水是正常形态，纳入则恒误报（实测 108 个此类持仓）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 不参与对账的持仓前缀（V1 导入 / 旧 sim 数据）
_EXCLUDED_PREFIXES = ("v1-", "pos-sim-")


def find_missing_fills(session) -> tuple[list[str], list[str]]:
    """返回 (缺 SELL 流水的 CLOSED 持仓 id 列表, 缺 BUY 流水的持仓 id 列表)。"""
    from sqlalchemy import select

    from zhice_os_v2.infrastructure.db import models as orm

    rows = session.execute(
        select(orm.OrderRequestModel.idempotency_key)
        .join(
            orm.ExecutionRecordModel,
            orm.ExecutionRecordModel.request_id == orm.OrderRequestModel.request_id,
        )
        .join(
            orm.FillRecordModel,
            orm.FillRecordModel.execution_id == orm.ExecutionRecordModel.execution_id,
        )
    ).all()
    sell_pos: set[str] = set()
    buy_pos: set[str] = set()
    for (idem,) in rows:
        if not idem:
            continue
        if idem.startswith("close-"):
            sell_pos.add(idem[len("close-"):])
        else:
            buy_pos.add(f"pos-{idem}")

    positions = session.execute(
        select(orm.PositionModel.position_id, orm.PositionModel.status)
    ).all()
    missing_sell = [
        pid
        for pid, status in positions
        if status == "CLOSED"
        and not pid.startswith(_EXCLUDED_PREFIXES)
        and pid not in sell_pos
    ]
    missing_buy = [
        pid
        for pid, _ in positions
        if not pid.startswith(_EXCLUDED_PREFIXES) and pid not in buy_pos
    ]
    return missing_sell, missing_buy


def reconcile_fills_job() -> dict:
    """对账入口（scheduler 每 6 小时）：有缺口 → CRITICAL 告警（title 带数量，
    数量变化即新告警；数量不变不重复插——持续态哨兵语义）。"""
    from zhice_os_v2.config.settings import get_settings

    if get_settings().persistence_backend != "sql":
        return {"skipped": True}

    from zhice_os_v2.infrastructure.db.session import get_session

    session = get_session()
    try:
        missing_sell, missing_buy = find_missing_fills(session)
    finally:
        session.close()

    result = {
        "missing_sell": len(missing_sell),
        "missing_buy": len(missing_buy),
        "sell_ids": missing_sell[:50],
        "buy_ids": missing_buy[:50],
    }
    if missing_sell or missing_buy:
        from zhice_os_v2.application.services.alert_service import send_alert

        send_alert(
            "CRITICAL",
            f"成交流水缺口（SELL缺{len(missing_sell)}/BUY缺{len(missing_buy)}）",
            f"CLOSED 持仓缺 SELL 流水 {len(missing_sell)} 笔：{missing_sell[:10]}\n"
            f"持仓缺 BUY 流水 {len(missing_buy)} 笔：{missing_buy[:10]}\n"
            "需运行 scripts/backfill_missing_fills.py 补写。",
            dedup_open=True,
        )
        logger.warning("fill reconciliation gaps: %s", result)
    else:
        logger.info("fill reconciliation clean")
    return result
