# -*- coding: utf-8 -*-
"""周一(2026-10-13)撮合验收只读验收器（K-STOCK-069）。

五判据逐条检查 paper.db（mode=ro，零写入），输出逐条 PASS/FAIL＋明细，
供盘后一条命令验收；提前跑（盘前/周末）应全部 FAIL——即探测器自证有效。

判据对应 docs/ops/2026-10-13_周一撮合验收清单.md §2：
  C1 CN filled >= 4（main/chaser 各 2，fill 字段齐全；K-STOCK-191 口径 B：
     ≥ 判据——超额成交是好消息不判 FAIL，残留 pending 降为观察项不阻塞）
  C2 US 6 笔 pending→filled（us_qingping 6，硬等式——US 无超额单源）
  C3 持仓生成后止损自动挂出（订单类 stop 或账本 stops 非空）
  C4 positions_snapshot 出现验收日快照行（首战）
  C5 幂等：同一 order_id 至多一条 filled 记录（无重复成交）
口径版本化：docs/ops/acceptance_criteria_v1.md（改口径必升版本）

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
    # 口径（K-STOCK-191 拍板 B，老田 10-12）：filled >= CN_EXPECT 即 PASS——
    #   超额成交（涨停拒单复活/普涨多成交）是好消息，硬等式判 FAIL 不合常理；
    #   残留 pending 降为观察项（pending_watch）不阻塞——过期单清理属运维线
    #   （chaser/人工），非撮合链缺陷。
    # 成交按 fill_date=验收日；残留 pending 按 plan_date<=验收日（含过期单）
    rows = cur.execute(
        "SELECT id, account_id, stock, qty, status, fill_date, filled_price,"
        " filled_qty, fee, plan_date, reason FROM orders"
        " WHERE account_id IN (?,?) AND (fill_date=? OR plan_date<=?)",
        CN_ACCOUNTS + (day, day)).fetchall()
    filled = [r for r in rows if r["status"] == "filled"
              and r["fill_date"] and r["filled_price"] and r["filled_qty"]]
    pending = [r for r in rows if r["status"] == "pending"]
    rejected = [r for r in rows if r["status"] == "rejected"]
    ok = len(filled) >= CN_EXPECT
    detail = {"expected_min": CN_EXPECT, "filled": len(filled),
              "pending_watch": len(pending),
              "rejected": [{"id": r["id"], "stock": r["stock"],
                            "why": (r["reason"] or "")[:80]} for r in rejected],
              "fills": [{k: r[k] for k in ("id", "account_id", "stock", "qty",
                                           "filled_price", "filled_qty", "fee")}
                        for r in filled]}
    return ok, detail


def check_c2_us_filled(cur, day):
    rows = cur.execute(
        "SELECT id, stock, qty, status, fill_date, filled_price, filled_qty,"
        " plan_date, reason FROM orders"
        " WHERE account_id=? AND (fill_date=? OR plan_date<=?)",
        (US_ACCOUNTS[0], day, day)).fetchall()
    filled = [r for r in rows if r["status"] == "filled"
              and r["fill_date"] and r["filled_price"] and r["filled_qty"]]
    pending = [r for r in rows if r["status"] == "pending"]
    rejected = [r for r in rows if r["status"] == "rejected"]
    ok = len(filled) == US_EXPECT and not pending
    detail = {"expected": US_EXPECT, "filled": len(filled),
              "pending_left": len(pending),
              "rejected": [{"id": r["id"], "stock": r["stock"],
                            "why": (r["reason"] or "")[:80]} for r in rejected],
              "fills": [{k: r[k] for k in ("id", "stock", "qty", "filled_price")}
                        for r in filled]}
    return ok, detail


def check_c3_stops(cur, day):
    """止损自动挂实证=positions.stop_price 非空（挂出待触发），
    而非 order_kind='stop' 触发单（收盘跌破后才生成，成交当日必空——首版口径坑）。
    分层断言（K-STOCK-069c 口径）：
      核心=US 6 只持仓 stop_price 全非空（cli G2 8% 成本锚定承诺）；
      chaser=trainee routine auto-stops（0.08）一并断言；
      main 无自动止损机制（设计现状，是否补默认档待老田拍板）——只展示不并判据。"""
    n_pos = cur.execute("SELECT count(*) FROM positions").fetchone()[0]
    if n_pos == 0:
        return None, {"skip": "positions 空（未撮合，判据未激活）"}
    rows = cur.execute(
        "SELECT account_id, stock, stop_price FROM positions").fetchall()
    by_acct = {}
    for r in rows:
        a = by_acct.setdefault(r["account_id"], {"n": 0, "with_stop": 0, "no_stop": []})
        a["n"] += 1
        if r["stop_price"] is not None:
            a["with_stop"] += 1
        else:
            a["no_stop"].append(r["stock"])
    us = by_acct.get(US_ACCOUNTS[0], {"n": 0, "with_stop": 0})
    chaser = by_acct.get("chaser", {"n": 0, "with_stop": 0})
    core_ok = us["n"] > 0 and us["with_stop"] == us["n"]
    chaser_ok = chaser["n"] == 0 or chaser["with_stop"] == chaser["n"]
    ok = core_ok and chaser_ok
    return ok, {"by_account": by_acct,
                "core": "US+chaser 止损全挂" if ok else "US/chaser 有止损缺口",
                "main_note": "main 无自动止损机制（设计现状，待拍板是否补默认档）"
                if "main" in by_acct and by_acct["main"]["no_stop"] else None}


def check_c4_snapshot(cur, day):
    # K-STOCK-189: 表内 date 为连字符形态（2026-10-12），判据入参为 YYYYMMDD
    # ——两形态都查，防日期形态不匹配恒 FAIL
    day_hyphen = f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 else day
    rows = cur.execute(
        "SELECT account_id, stock, qty, market_value FROM positions_snapshot"
        " WHERE date IN (?, ?)", (day, day_hyphen)).fetchall()
    rows = [dict(r) for r in rows]
    ok = len(rows) > 0
    return ok, {"n_rows": len(rows),
                "by_account": {a: sum(1 for r in rows if r["account_id"] == a)
                               for a in CN_ACCOUNTS + US_ACCOUNTS}}


def check_c5_idempotent(cur, day):
    # K-STOCK-189: 空 ref（""）非 order_id 标识，剔除分组（原判据仅排 NULL，
    #   两条手工单空 ref 被误判同组重复）
    dup = cur.execute(
        "SELECT ref, count(*) c FROM orders WHERE status='filled' AND fill_date=?"
        " AND ref IS NOT NULL AND ref != '' GROUP BY ref HAVING c > 1", (day,)).fetchall()
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
