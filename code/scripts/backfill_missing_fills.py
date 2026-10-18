#!/usr/bin/env python
"""补录缺失成交流水（603311 案收尾，2026-09-20）。

背景：三类成因造成 v2_fill_record 缺行（持仓/盈亏本身正确，仅流水丢失）——
- Bug A：_record_fill 外层 except 吞异常（9-18 窗口，6 持仓丢 SELL + 3 丢 BUY）；
- Bug B：/positions/close 一键平仓不写流水（9-02，13 持仓丢 SELL）；
- _fit 修复（bbdfada，8-23）前超长 position_id 致 INSERT 静默失败
  （8-19 CN9 / 8-20 US7 的 v1- 持仓丢 SELL，8-19/8-20 HK3 丢 BUY）。
代码均已修复；本脚本按持仓表现存数据重建 fill 四链（幂等：fill_id 冲突自动跳过）。

口径：
- SELL：CLOSED 持仓无 close-fill。V2 原生全量；v1- 导入持仓仅 closed_at
  >= 2026-08-19（V2 自该日起对 v1- 持仓写流水，之前的成交在 V1 系统）。
- BUY：V2 原生持仓无 open-fill。pos-sim- 旧数据不参与。
- 静默：notify=False——绝不推 telegram（否则给用户推一堆假历史成交）。

用法：
  PYTHONPATH=src .venv/bin/python scripts/backfill_missing_fills.py           # dry-run
  PYTHONPATH=src .venv/bin/python scripts/backfill_missing_fills.py --apply   # 真写
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from types import SimpleNamespace

# v1- 导入持仓的流水责任起点（V2 平仓链自 8-19 起对 v1- 持仓写 fill，数据可证）
CUTOFF_V1 = datetime(2026, 8, 19, tzinfo=timezone.utc)


def sell_reason_for(closed_at: datetime) -> str:
    d = closed_at.date()
    if d == date(2026, 9, 2):
        return "人工平仓：管理端一键平仓（缺口补录）"
    return "平仓离场：历史缺口补录（原流水写入失败）"


def _fill_pos_sets(session) -> tuple[set[str], set[str]]:
    """(有SELL流水的pos_id集, 有BUY流水的pos_id集)——对账同口径。"""
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
    return sell_pos, buy_pos


def collect_tasks(session) -> list[dict]:
    """扫描缺口，返回补录任务清单（不写库）。"""
    from sqlalchemy import select

    from zhice_os_v2.infrastructure.db import models as orm

    sell_pos, buy_pos = _fill_pos_sets(session)
    positions = session.execute(select(orm.PositionModel)).scalars().all()

    tasks: list[dict] = []
    for p in positions:
        pid = p.position_id
        if pid.startswith("pos-sim-"):
            continue
        if pid.startswith("v1-"):
            # v1- 开仓在 V1 系统，BUY 不要求；SELL 仅 cutoff 之后
            if (
                p.status == "CLOSED"
                and p.closed_at
                and p.closed_at >= CUTOFF_V1
                and pid not in sell_pos
            ):
                tasks.append({"p": p, "side": "SELL"})
            continue
        if pid not in buy_pos:
            tasks.append({"p": p, "side": "BUY"})
        if p.status == "CLOSED" and pid not in sell_pos:
            tasks.append({"p": p, "side": "SELL"})
    return tasks


def _proxy(p) -> SimpleNamespace:
    """_record_fill 只读持仓这几个字段，行对象转轻量代理。"""
    return SimpleNamespace(
        position_id=p.position_id,
        strategy_version_id=p.strategy_version_id,
        market=p.market,
        ts_code=p.ts_code,
        open_price=p.open_price,
        user_strategy_id=p.user_strategy_id,
        account_id=p.account_id,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="真写（默认 dry-run）")
    args = parser.parse_args()

    from zhice_os_v2.config.settings import get_settings

    if get_settings().persistence_backend != "sql":
        print("仅限 sql 后端（真库）执行，当前 persistence_backend 非 sql，退出")
        return 1

    from zhice_os_v2.infrastructure.db.session import get_session

    session = get_session()
    try:
        tasks = collect_tasks(session)
    finally:
        session.close()

    sells = [t for t in tasks if t["side"] == "SELL"]
    buys = [t for t in tasks if t["side"] == "BUY"]
    print(f"缺口清单：SELL {len(sells)} 笔 / BUY {len(buys)} 笔")
    for t in tasks:
        p = t["p"]
        is_sell = t["side"] == "SELL"
        when = p.closed_at if is_sell else p.opened_at
        qty = p.close_qty if is_sell else p.open_qty
        px = p.close_price if is_sell else p.open_price
        print(
            f"  [{t['side']}] {p.market:6} {p.ts_code:14} "
            f"{when:%Y-%m-%d %H:%M} qty={qty} px={px} {p.position_id}"
        )

    if not tasks:
        print("无缺口，无需补录")
        return 0
    if not args.apply:
        print("\ndry-run 结束（未写库）。确认无误后加 --apply 执行补录。")
        return 0

    # 补录：复用 _record_fill 的 id 生成/幂等/四链一致性，notify=False 静默
    from zhice_os_v2.application.services.workflow_service import WorkflowService
    from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

    svc = WorkflowService(InMemoryDatabase())
    session = get_session()
    try:
        tasks = collect_tasks(session)  # 重扫（幂等起见以最新状态为准）
        for t in tasks:
            p = t["p"]
            proxy = _proxy(p)
            if t["side"] == "SELL":
                svc._record_fill(
                    market=p.market,
                    ts_code=p.ts_code,
                    side="SELL",
                    qty=p.close_qty,
                    price=p.close_price,
                    position=proxy,
                    fill_time=p.closed_at,
                    sell_reason=sell_reason_for(p.closed_at),
                    realized_pnl=p.pnl_amount,
                    exit_kind="MANUAL",
                    notify=False,
                )
            else:
                svc._record_fill(
                    market=p.market,
                    ts_code=p.ts_code,
                    side="BUY",
                    qty=p.open_qty,
                    price=p.open_price,
                    position=proxy,
                    fill_time=p.opened_at,
                    notify=False,
                )
        # 写后重扫验证
        left = collect_tasks(session)
    finally:
        session.close()

    print(f"\n补录完成：写入 {len(tasks)} 笔；剩余缺口 {len(left)}（应为 0）")
    return 0 if not left else 2


if __name__ == "__main__":
    raise SystemExit(main())
