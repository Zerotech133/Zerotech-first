# -*- coding: utf-8 -*-
"""周一(2026-10-13)撮合验收只读验收器（K-STOCK-069）。

五判据逐条检查 paper.db（mode=ro，零写入），输出逐条 PASS/FAIL＋明细，
供盘后一条命令验收；提前跑（盘前/周末）应全部 FAIL——即探测器自证有效。

判据对应 docs/ops/2026-10-13_周一撮合验收清单.md §2：
  C1 CN 4 笔 pending→filled（main/chaser 各 2，fill 字段齐全）
  C2 US 6 笔 pending→filled（us_qingping 6）
  C3 持仓生成后止损自动挂出（订单类 stop 或账本 stops 非空）
  C4 positions_snapshot 出现验收日快照行（首战）
  C5 幂等：同一 order_id 至多一条 filled 记录（无重复成交）

用法：
  python acceptance_check.py --date 20261013          # 周一盘后验收
  python acceptance_check.py --date 20261013 --json   # 机器可读输出
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "paper.db"
CN_ACCOUNTS = ("main", "chaser")
US_ACCOUNTS = ("us_qingping",)
CN_EXPECT = 4   # CN filled 期望笔数
US_EXPECT = 6   # US filled 期望笔数


def ro_connect():
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def check_c1_cn_filled(cur, day):
    # 口径：成交按 fill_date=验收日；残留 pending 按 plan_date<=验收日（含过期单）
    rows = cur.execute(
        "SELECT id, account_id, stock, qty, status, fill_date, filled_price,"
        " filled_qty, fee, plan_date FROM orders"
        " WHERE account_id IN (?,?) AND (fill_date=? OR plan_date<=?)",
        CN_ACCOUNTS + (day, day)).fetchall()
    filled = [r for r in rows if r["status"] == "filled"
              and r["fill_date"] and r["filled_price"] and r["filled_qty"]]
    pending = [r for r in rows if r["status"] == "pending"]
    ok = len(filled) == CN_EXPECT and not pending
    detail = {"expected": CN_EXPECT, "filled": len(filled),
              "pending_left": len(pending),
              "fills": [{k: r[k] for k in ("id", "account_id", "stock", "qty",
                                           "filled_price", "filled_qty", "fee")}
                        for r in filled]}
    return ok, detail


def check_c2_us_filled(cur, day):
    rows = cur.execute(
        "SELECT id, stock, qty, status, fill_date, filled_price, filled_qty,"
        " plan_date FROM orders"
        " WHERE account_id=? AND (fill_date=? OR plan_date<=?)",
        (US_ACCOUNTS[0], day, day)).fetchall()
    filled = [r for r in rows if r["status"] == "filled"
              and r["fill_date"] and r["filled_price"] and r["filled_qty"]]
    pending = [r for r in rows if r["status"] == "pending"]
    ok = len(filled) == US_EXPECT and not pending
    detail = {"expected": US_EXPECT, "filled": len(filled),
              "pending_left": len(pending),
              "fills": [{k: r[k] for k in ("id", "stock", "qty", "filled_price")}
                        for r in filled]}
    return ok, detail


def check_c3_stops(cur, day):
    stops = cur.execute(
        "SELECT id, account_id, stock, side, qty, status, plan_date, order_kind"
        " FROM orders WHERE order_kind='stop'").fetchall()
    stops = [dict(r) for r in stops]
    # 持仓非空时才要求止损挂出（盘前持仓空判据自动 SKIP）
    n_pos = cur.execute("SELECT count(*) FROM positions").fetchone()[0]
    if n_pos == 0:
        return None, {"skip": "positions 空（未撮合，判据未激活）"}
    ok = len(stops) > 0
    return ok, {"n_stops": len(stops), "n_positions": n_pos, "stops": stops[:12]}


def check_c4_snapshot(cur, day):
    rows = cur.execute(
        "SELECT account_id, stock, qty, market_value FROM positions_snapshot"
        " WHERE date=?", (day,)).fetchall()
    rows = [dict(r) for r in rows]
    ok = len(rows) > 0
    return ok, {"n_rows": len(rows),
                "by_account": {a: sum(1 for r in rows if r["account_id"] == a)
                               for a in CN_ACCOUNTS + US_ACCOUNTS}}


def check_c5_idempotent(cur, day):
    dup = cur.execute(
        "SELECT ref, count(*) c FROM orders WHERE status='filled' AND fill_date=?"
        " AND ref IS NOT NULL GROUP BY ref HAVING c > 1", (day,)).fetchall()
    dup = [dict(r) for r in dup]
    ok = not dup
    return ok, {"duplicate_refs": dup,
                "note": "同一 order_id(ref) 只允许一条 filled（同日）"}


def main(argv=None):
    ap = argparse.ArgumentParser(description="周一撮合验收只读验收器")
    ap.add_argument("--date", default="20261013", help="验收日 YYYYMMDD")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(argv)

    if not DB.exists():
        print(f"[exit 2] paper.db 不存在: {DB}")
        return 2
    con = ro_connect()
    cur = con.cursor()
    results = {
        "date": args.date,
        "db": str(DB),
        "C1_cn_filled": check_c1_cn_filled(cur, args.date),
        "C2_us_filled": check_c2_us_filled(cur, args.date),
        "C3_stops_auto": check_c3_stops(cur, args.date),
        "C4_snapshot": check_c4_snapshot(cur, args.date),
        "C5_idempotent": check_c5_idempotent(cur, args.date),
    }
    con.close()

    def tag(v):
        return {True: "PASS", False: "FAIL", None: "SKIP"}[v]

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
    else:
        print("=" * 62)
        print(f"撮合验收 {args.date}（只读，paper.db 未动）")
        for k in ("C1_cn_filled", "C2_us_filled", "C3_stops_auto",
                  "C4_snapshot", "C5_idempotent"):
            ok, det = results[k]
            print(f"  {k:16s}: {tag(ok)}  {json.dumps(det, ensure_ascii=False)[:120]}")
        print("=" * 62)
    verdicts = [results[k][0] for k in ("C1_cn_filled", "C2_us_filled",
                                        "C3_stops_auto", "C4_snapshot",
                                        "C5_idempotent")]
    hard = [v for v in verdicts if v is not None]
    if not hard:
        print("总判：SKIP（判据未激活——尚未撮合）")
        return 0
    all_pass = all(hard)
    print(f"总判：{'PASS 五判据全过' if all_pass else 'FAIL（见上，未过项需排查撮合链）'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
