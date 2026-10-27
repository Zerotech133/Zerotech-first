# -*- coding: utf-8 -*-
"""组合隔离账户开户工具（WO-QT-20261021-109）。

**默认 dry-run，只打印计划不写库**；`--apply` 才落库，须老田签准（往交易域写账户）。

## 为什么需要

`combo_factory.branch_account_ids(combo_id)` 返回
`{"follow_research": "combo_x@follow_research", "aggressive_direct": "combo_x@aggressive_direct"}`，
即**组合 × 分支**的隔离账户 id。但 `paper.db` 的 `account` 表里**一个 `combo@*` 账户都没有**
——机制在、账户没建。所以组合的收益无处归集，WO-105 步2（后验收益配对）无法实施。

**注意：不需要给注册表加 `account_id` 字段**（WO-108 曾如此建议，实测后纠正）——
命名约定已经把账户 id 定死了，缺的是账户实体本身。

## 幂等与边界

- 幂等：账户已存在则 `skip`，**不重复注资**（重复注资＝凭空造钱）。
- 只新增 `account` 行 + 对应的 `ledger` 入金行（与现有 6 个账户的建法一致）。
- **不碰**既有账户、不动 `positions`/`orders`/既有 `ledger` 行。

## 用法

  python combo_accounts.py                 # dry-run（默认，只打印计划）
  python combo_accounts.py --apply         # 落库（须签准）
  python combo_accounts.py --db path.db    # 指定库（测试用）
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent          # k_stock/paper
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import combo_registry as creg                   # noqa: E402
import strategy_branch as sb                    # noqa: E402

DEFAULT_DB = HERE.parent / "data" / "paper.db"
INITIAL_CASH = 1_000_000.0                      # 与现有 6 个账户一致


def build_plan(conn, combo_ids=None, branch_ids=None,
               initial_cash=INITIAL_CASH) -> list:
    """返回待建账户计划（不写库）。已存在的标 skip。"""
    if combo_ids is None:
        reg = creg.load_registry()
        combo_ids = [c["combo_id"] for c in reg.get("combos") or []
                     if (c or {}).get("combo_id")]
    if branch_ids is None:
        branch_ids = list(sb.registered_branch_ids())
    have = {r[0] for r in conn.execute("SELECT account_id FROM account")}
    plan = []
    for cid in combo_ids:
        for bid in branch_ids:
            aid = "%s@%s" % (cid, bid)
            plan.append({
                "account_id": aid, "combo_id": cid, "branch_id": bid,
                "initial_cash": initial_cash,
                "action": "skip" if aid in have else "create",
                "reason": "已存在，不重复注资" if aid in have else "",
            })
    return plan


def apply_plan(conn, plan, ts=None) -> dict:
    """执行 create 项：account 行 + ledger 入金行（同一事务）。"""
    ts = ts or time.strftime("%Y-%m-%dT%H:%M:%S")
    created, skipped = [], []
    try:
        for r in plan:
            if r["action"] != "create":
                skipped.append(r["account_id"])
                continue
            c = r["initial_cash"]
            conn.execute(
                "INSERT INTO account(account_id, cash, initial_cash, created_at) "
                "VALUES(?,?,?,?)", (r["account_id"], c, c, ts))
            conn.execute(
                "INSERT INTO ledger(ts, type, amount, cash_after, account_id) "
                "VALUES(?,?,?,?,?)", (ts, "deposit", c, c, r["account_id"]))
            created.append(r["account_id"])
        conn.commit()
    except Exception:
        conn.rollback()                          # 全部或全不
        raise
    return {"ok": True, "created": created, "skipped": skipped}


def _connect(db_path):
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    return conn


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="组合隔离账户开户（默认 dry-run）")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--apply", action="store_true",
                    help="真正落库（须老田签准；缺省仅 dry-run）")
    ap.add_argument("--cash", type=float, default=INITIAL_CASH)
    args = ap.parse_args(argv)

    conn = _connect(args.db)
    try:
        plan = build_plan(conn, initial_cash=args.cash)
        n_create = sum(1 for r in plan if r["action"] == "create")
        print(json.dumps({
            "mode": "apply" if args.apply else "dry-run",
            "db": args.db, "total": len(plan),
            "to_create": n_create, "to_skip": len(plan) - n_create,
            "initial_cash": args.cash,
        }, ensure_ascii=False))
        for r in plan:
            print("  %-8s %-42s %s" % (r["action"], r["account_id"], r["reason"]))
        if not args.apply:
            print("\n[dry-run] 未写库。确认后加 --apply（须老田签准）。")
            return 0
        res = apply_plan(conn, plan)
        print(json.dumps({"created": len(res["created"]),
                          "skipped": len(res["skipped"])}, ensure_ascii=False))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
