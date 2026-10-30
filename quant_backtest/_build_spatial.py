# -*- coding: utf-8 -*-
"""K-SPATIAL-01：TradeAction + Location 时空打底入图脚本（dryrun+实跑）

与 kg_build_cl1.py 的关系：
  · 独立运行，不改动原 TRADED_ON 边写入路径
  · 原 TradeAction 节点（按 ts_top_inst 原始行粒度，含 side 方向）+ 6 条新边
  · 旧 TRADED_ON 边继续存在 → 双写过渡，兼容现有前端查询

幂等：所有节点/边用 MERGE，重复跑不重复建

用法：
  python _build_spatial.py --dryrun      # 只算规模不入图
  python _build_spatial.py               # 全量入图（幂等）
"""

import argparse
import hashlib
import os
import re
import sys
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import kg_schema as S
import tdx_read as tdxr
import _seat_locator as sl


def _now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _ta_id(seat_name, ts_code, trade_date, side):
    """确定性 TradeAction 唯一 ID（避免 Python hash() 的随机种子陷阱）。

    格式: ta:{md5_seat[:8]}:{ts_code}:{trade_date}:{side}
    """
    h = hashlib.md5(seat_name.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    return f"ta:{h}:{ts_code}:{trade_date}:{side}"


def build_trade_actions(df, seat2hm=None):
    """从 ts_top_inst 原始行构建 TradeAction 节点 + 关联边。

    Args:
        df: ts_top_inst 原始 DataFrame（含 side 方向）
        seat2hm: {seat_name: hotmoney_name} 映射（可选，用于 OCCUPIED_BY）

    Returns:
        ta_nodes:     list[dict]  TradeAction 节点（按 (seat,ts_code,date,side) 一条）
        edges:        dict[str, list[dict]]  按边类型分组的边
        loc_nodes:    list[dict]  Location 节点
        seat_loc_edges: list[dict]  Seat→Location 时空锚点
        trade_dates:  list[dict]  补建 TradeDate 节点（仅新出现的日期）
    """
    if seat2hm is None:
        seat2hm = {}

    # Location 节点（全量唯一席位名 → 地理锚点）
    loc_nodes, loc_hit, loc_total = sl.build_location_nodes(
        df["exalter"].dropna().unique()
    )
    loc_key_map = {
        n["seat_name"]: n for n in loc_nodes
    }  # seat_name → Location 节点属性

    ta_nodes = []
    executed_by = []  # TradeAction → Seat
    targets = []  # TradeAction → Security
    occupied_by = []  # TradeAction → HotMoney（经 seat2hm 投影）
    located_at = []  # TradeAction → Location（仅可识别地理的席位）
    seat_loc_edges = []  # Seat → Location（同一席位可复用）
    trade_dates_set = set()

    # Seat → Location 只写一次（避免逐 TradeAction 重复）
    seat_loc_written = set()

    for r in df.itertuples(index=False):
        seat_name = r.exalter
        ts_code = r.ts_code
        trade_date = str(r.trade_date)[:8]
        side = int(r.side) if pd.notna(r.side) else 0
        amount = float(r.buy) if side == 0 else float(r.sell)
        if pd.isna(amount) or amount == 0:
            continue

        # TradeAction 唯一 ID
        ta_id = _ta_id(seat_name, ts_code, trade_date, side)

        ta_nodes.append(
            {
                "id": ta_id,
                "trade_date": trade_date,
                "side": side,  # 0=净买 / 1=净卖
                "amount": amount,
                "source": "ts_top_inst",
                "knowledge_time": _now(),
                "updated_at": _now(),
            }
        )

        vstart = trade_date
        executed_by.append({"src": ta_id, "dst": seat_name, "validStart": vstart})
        targets.append({"src": ta_id, "dst": ts_code, "validStart": vstart})

        # OCCUPIED_BY 直挂（仅 seat2hm 有映射时）
        if seat_name in seat2hm:
            occupied_by.append(
                {
                    "src": ta_id,
                    "dst": seat2hm[seat_name],
                    "validStart": vstart,
                    "confidence": 0.9,
                    "source": "youzi_registry_v1",
                    "updated_at": _now(),
                }
            )

        # LOCATED_AT（仅地理可识别席位）
        loc = loc_key_map.get(seat_name)
        if loc:
            located_at.append({"src": ta_id, "dst": loc["key"], "validStart": vstart})

            # Seat → Location 时空锚点（去重写一次）
            if seat_name not in seat_loc_written:
                seat_loc_written.add(seat_name)
                seat_loc_edges.append(
                    {
                        "src": seat_name,
                        "dst": loc["key"],
                        "validStart": trade_date,
                        "validEnd": S.VALID_END_OPEN,
                        "source": "seat_locator_v1",
                        "updated_at": _now(),
                    }
                )

        trade_dates_set.add(trade_date)

    # 补建 TradeDate 节点（确保 HAS_TRADED 目标端存在）
    trade_dates = [{"date": d} for d in sorted(trade_dates_set)]

    # TradeDate -[HAS_TRADED]-> TradeAction 边（按日期分组）
    has_traded = []
    # 注意：TradeAction 节点属性里没有 trade_date，需要从原始 df 重建映射
    # 用 ta_id 的 hash 部分倒推不可靠，用 df 逐行重新关联
    for r in df.itertuples(index=False):
        trade_date = str(r.trade_date)[:8]
        side = int(r.side) if pd.notna(r.side) else 0
        amount = float(r.buy) if side == 0 else float(r.sell)
        if pd.isna(amount) or amount == 0:
            continue
        seat_name = r.exalter
        ts_code = r.ts_code
        ta_id = _ta_id(seat_name, ts_code, trade_date, side)
        has_traded.append({"src": trade_date, "dst": ta_id, "validStart": trade_date})

    edges = {
        S.REL_EXECUTED_BY: executed_by,
        S.REL_TARGETS: targets,
        S.REL_OCCUPIED_BY: occupied_by,
        S.REL_LOCATED_AT: located_at,
        S.REL_SEAT_LOCATED_AT: seat_loc_edges,
        S.REL_HAS_TRADED: has_traded,
    }

    stats = {
        "n_rows": len(df),
        "n_seats": df["exalter"].nunique(),
        "n_unique_ta": len(ta_nodes),
        "n_loc_nodes": len(loc_nodes),
        "loc_hit": loc_hit,
        "loc_total": loc_total,
        "loc_rate": f"{loc_hit / loc_total * 100:.1f}%" if loc_total else "n/a",
        "n_dates": len(trade_dates_set),
        "n_executed_by": len(executed_by),
        "n_targets": len(targets),
        "n_occupied_by": len(occupied_by),
        "n_located_at": len(located_at),
        "n_seat_loc": len(seat_loc_edges),
        "n_has_traded": len(has_traded),
    }
    return ta_nodes, edges, loc_nodes, seat_loc_edges, trade_dates, stats


def run(dryrun=False):
    print("=== K-SPATIAL-01 时空打底 %s ===" % datetime.now().strftime("%F %T"))

    # 加载 ts_top_inst
    print("[1/3] 读取 ts_top_inst 原始数据…")
    df = tdxr.read_range("ts_top_inst")
    df["trade_date"] = df["trade_date"].astype(str).str[:8]
    print(
        f"  原始行: {len(df)}  日期: {df['trade_date'].min()} ~ {df['trade_date'].max()}"
    )

    # 游资词表（用于 OCCUPIED_BY 投影）
    print("[2/3] 游资词表匹配 + TradeAction 构建…")
    sys.path.insert(0, HERE)
    import kg_build_cl1  # noqa: E402

    seat2hm = {}
    for name, pats, _ in kg_build_cl1.YOUZI:
        rx = re.compile("|".join(pats))
        for seat in df["exalter"].dropna().unique():
            if rx.search(seat):
                seat2hm.setdefault(seat, name)

    ta_nodes, edges, loc_nodes, _, trade_dates, stats = build_trade_actions(df, seat2hm)

    print("  TradeAction 节点: %d" % stats["n_unique_ta"])
    print(
        "  Location 节点: %d（命中率 %s）" % (stats["n_loc_nodes"], stats["loc_rate"])
    )
    print("  TradeDate 补建: %d" % stats["n_dates"])
    print("  EXECUTED_BY: %d" % stats["n_executed_by"])
    print("  TARGETS: %d" % stats["n_targets"])
    print("  OCCUPIED_BY: %d" % stats["n_occupied_by"])
    print("  LOCATED_AT: %d" % stats["n_located_at"])
    print("  SEAT_LOCATED_AT: %d" % stats["n_seat_loc"])
    print("  HAS_TRADED: %d" % stats["n_has_traded"])

    if dryrun:
        print("DRYRUN 完成，未写库。")
        return stats

    # 入图
    print("[3/3] 入图（幂等 MERGE）…")
    from kg_client import KgClient

    cli = KgClient()
    cli.ensure_schema()

    # Location 节点
    cli.upsert_nodes(S.LBL_LOCATION, ["key"], loc_nodes)
    print("  Location: %d" % len(loc_nodes))

    # TradeDate 补建（确保 HAS_TRADED 目标端存在）
    cli.upsert_nodes(S.LBL_TRADE_DATE, ["date"], trade_dates)
    print("  TradeDate: %d" % len(trade_dates))

    # TradeAction 节点
    cli.upsert_nodes(S.LBL_TRADE_ACTION, ["id"], ta_nodes)
    print("  TradeAction: %d" % len(ta_nodes))

    # 新边族（6 类）
    # Seat 节点确保存在（EXECUTED_BY 源端）
    cli.upsert_nodes(
        S.LBL_SEAT, ["name"], [{"name": s} for s in df["exalter"].dropna().unique()]
    )

    # EXECUTED_BY：TradeAction → Seat（注意方向！src=ta_id, dst=seat_name）
    # upsert_edge 签名是 (src_label, src_key, dst_label, dst_key, rel, props_list)
    cli.upsert_edge(
        S.LBL_TRADE_ACTION,
        "id",
        S.LBL_SEAT,
        "name",
        S.REL_EXECUTED_BY,
        edges[S.REL_EXECUTED_BY],
    )
    print("  EXECUTED_BY: %d" % len(edges[S.REL_EXECUTED_BY]))

    cli.upsert_edge(
        S.LBL_TRADE_ACTION,
        "id",
        S.LBL_SECURITY,
        "ts_code",
        S.REL_TARGETS,
        edges[S.REL_TARGETS],
    )
    print("  TARGETS: %d" % len(edges[S.REL_TARGETS]))

    if edges[S.REL_OCCUPIED_BY]:
        cli.upsert_edge(
            S.LBL_TRADE_ACTION,
            "id",
            S.LBL_HOTMONEY,
            "name",
            S.REL_OCCUPIED_BY,
            edges[S.REL_OCCUPIED_BY],
        )
        print("  OCCUPIED_BY: %d" % len(edges[S.REL_OCCUPIED_BY]))

    if edges[S.REL_LOCATED_AT]:
        cli.upsert_edge(
            S.LBL_TRADE_ACTION,
            "id",
            S.LBL_LOCATION,
            "key",
            S.REL_LOCATED_AT,
            edges[S.REL_LOCATED_AT],
        )
        print("  LOCATED_AT: %d" % len(edges[S.REL_LOCATED_AT]))

    if edges[S.REL_SEAT_LOCATED_AT]:
        cli.upsert_edge(
            S.LBL_SEAT,
            "name",
            S.LBL_LOCATION,
            "key",
            S.REL_SEAT_LOCATED_AT,
            edges[S.REL_SEAT_LOCATED_AT],
        )
        print("  SEAT_LOCATED_AT: %d" % len(edges[S.REL_SEAT_LOCATED_AT]))

    if edges[S.REL_HAS_TRADED]:
        cli.upsert_edge(
            S.LBL_TRADE_DATE,
            "date",
            S.LBL_TRADE_ACTION,
            "id",
            S.REL_HAS_TRADED,
            edges[S.REL_HAS_TRADED],
        )
        print("  HAS_TRADED: %d" % len(edges[S.REL_HAS_TRADED]))

    cli.close()
    print("=== K-SPATIAL-01 完成 ===")
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dryrun", action="store_true")
    args = ap.parse_args()
    run(dryrun=args.dryrun)


if __name__ == "__main__":
    main()
