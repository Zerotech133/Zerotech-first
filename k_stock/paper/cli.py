# -*- coding: utf-8 -*-
"""模拟仓 CLI：人为测试与 automation 共用入口。

用法（系统 Python311 运行真实行情路径；--dry-run/状态查询任何 python 均可）：
  python k_stock/paper/cli.py status
  python k_stock/paper/cli.py buy --stock 600519.SH --qty 100 [--price 1250] [--force]
  python k_stock/paper/cli.py sell --stock 600519.SH [--all] [--qty 100] [--price 1260] [--force]
  python k_stock/paper/cli.py exec --newest [--dry-run]     # AI 决策落 pending 单
  python k_stock/paper/cli.py settle [--dry-run]            # 撮合 pending（当日开盘价）
  python k_stock/paper/cli.py pending | history | verify
  python k_stock/paper/cli.py reset --cash 1000000 --yes    # 清账重开（危险）

automation 每日顺序（07:05）：settle（撮昨日 pending）→ exec --newest（落今日单）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from engine import PaperEngine, Reject          # noqa: E402
from market import ParquetMarket                # noqa: E402

DATA_DIR = HERE.parent / "data"
DEFAULT_DB = DATA_DIR / "paper.db"


def _engine(args) -> PaperEngine:
    db = Path(args.db) if args.db else DEFAULT_DB
    return PaperEngine(db, ParquetMarket())


def _newest_run() -> Path:
    runs = sorted(DATA_DIR.glob("poc_*.json"))
    if not runs:
        raise Reject("k_stock/data/ 下无 poc_*.json 决策产物")
    return runs[-1]


def _print_eq(e: PaperEngine) -> None:
    eq = e.equity()
    v = e.verify()
    print(f"现金 {eq['cash']:>12,.2f} | 持仓市值 {eq['market_value']:>12,.2f} "
          f"| 总资产 {eq['total']:>12,.2f} | 仓位 {eq['pos_pct']:.1%} | 对账 {'OK' if v['ok'] else '❌FAIL'}")
    for p in eq["positions"]:
        print(f"  {p['stock']}  {p['qty']:>6} 股（可卖 {p['available']:>6}）"
              f" 成本 {p['avg_cost']:.2f} 现价 {p['last']:.2f} "
              f"市值 {p['market_value']:>12,.2f} 盈亏 {p['pnl']:>+10,.2f}")


def cmd_status(args) -> int:
    e = _engine(args)
    _print_eq(e)
    pend = e.pending()
    if pend:
        print(f"待撮合 {len(pend)} 单:")
        for p in pend:
            print(f"  #{p['id']} [{p['source']}] {p['side']} {p['stock']} {p['qty']} 股"
                  f" @ {p['plan_date']} 开盘 | {p['reason']}")
    return 0


def cmd_buy(args) -> int:
    e = _engine(args)
    r = e.buy(args.stock, args.qty, args.price, force=args.force)
    print(f"✅ 成交: BUY {r['stock']} {r['qty']} 股 @ {r['price']}（费 {r['fee']:.2f}）"
          f" 现金余 {r['cash_after']:,.2f}")
    return 0


def cmd_sell(args) -> int:
    e = _engine(args)
    qty = None if args.all else args.qty
    r = e.sell(args.stock, qty, args.price, force=args.force)
    print(f"✅ 成交: SELL {r['stock']} {r['qty']} 股 @ {r['price']}（费 {r['fee']:.2f}）"
          f" 现金余 {r['cash_after']:,.2f}")
    return 0


def cmd_exec(args) -> int:
    e = _engine(args)
    run = Path(args.run) if args.run else _newest_run()
    r = e.exec_run(run, dry_run=args.dry_run)
    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}决策执行 {r['run']}: 落单 {len([o for o in r['orders'] if o.get('status') == 'pending'])} 笔")
    for o in r["orders"]:
        print(f"  [{o['status']}] {o['side']} {o['stock']} {o['qty']} 股 @ {o['plan_date']} 开盘"
              f" | {o['reason']}")
    for s in r["skipped"]:
        print(f"  [skip] {s['stock']}: {s['why']}")
    return 0


def cmd_settle(args) -> int:
    e = _engine(args)
    r = e.settle(dry_run=args.dry_run)
    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}撮合: 成交 {len(r['filled'])} / 拒绝或搁置 {len(r['rejected'])}")
    for f in r["filled"]:
        print(f"  ✅ #{f.get('id', '-')} {f['stock']} {f['side']} {f['qty']} 股 @ {f['price']}"
              f"（费 {f['fee']:.2f}）")
    for x in r["rejected"]:
        print(f"  ❌ #{x['id']} {x['stock']}: {x['why']}")
    if not args.dry_run:
        _print_eq(e)
    return 0


def cmd_pending(args) -> int:
    e = _engine(args)
    pend = e.pending()
    print(f"待撮合 {len(pend)} 单")
    for p in pend:
        print(f"  #{p['id']} [{p['source']}] {p['side']} {p['stock']} {p['qty']} 股"
              f" @ {p['plan_date']} 开盘 | {p['reason']}")
    return 0


def cmd_history(args) -> int:
    e = _engine(args)
    rows = e.history(args.limit)
    print(f"最近 {len(rows)} 笔订单:")
    for r in rows:
        px = f"{r['filled_price']:.2f}" if r["filled_price"] else "-"
        print(f"  #{r['id']} {r['ts'][:16]} [{r['source']}] {r['status']:>8} "
              f"{r['side']} {r['stock']} {r['qty']} @ {px}"
              f"{'（' + str(r['reason']) + '）' if r['reason'] and r['status'] != 'filled' else ''}")
    return 0


def cmd_reset(args) -> int:
    e = _engine(args)
    r = e.reset(args.cash, yes=args.yes)
    print(f"♻️ 账户已重置: 现金 {r['cash']:,.2f}")
    return 0


def cmd_verify(args) -> int:
    e = _engine(args)
    v = e.verify()
    print(("✅ 对账通过: " if v["ok"] else "❌ 对账失败: ") + json.dumps(v, ensure_ascii=False))
    return 0 if v["ok"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="paper", description="K-STOCK 模拟仓（纸面交易账本）")
    ap.add_argument("--db", default=None, help="账本 SQLite 路径（默认 k_stock/data/paper.db）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status", help="账户/持仓/盈亏概览")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("buy", help="手动买入（按最近收盘价即时成交，或 --price 指定价）")
    p.add_argument("--stock", required=True)
    p.add_argument("--qty", type=int, required=True, help="股数（100 整数手）")
    p.add_argument("--price", type=float, default=None)
    p.add_argument("--force", action="store_true", help="跳过风控三条（测试极端场景）")
    p.set_defaults(fn=cmd_buy)

    p = sub.add_parser("sell", help="手动卖出（--all 清可卖仓）")
    p.add_argument("--stock", required=True)
    p.add_argument("--qty", type=int, default=None)
    p.add_argument("--all", action="store_true")
    p.add_argument("--price", type=float, default=None)
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_sell)

    p = sub.add_parser("exec", help="AI 决策产物落 pending 单（默认最新 poc_*.json）")
    p.add_argument("--run", default=None)
    p.add_argument("--newest", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_exec)

    p = sub.add_parser("settle", help="撮合 pending 单（按计划日开盘价，涨跌停拦截）")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_settle)

    p = sub.add_parser("pending", help="列出待撮合单")
    p.set_defaults(fn=cmd_pending)

    p = sub.add_parser("history", help="订单历史")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(fn=cmd_history)

    p = sub.add_parser("reset", help="清账重开（危险，需 --yes）")
    p.add_argument("--cash", type=float, default=None)
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_reset)

    p = sub.add_parser("verify", help="资金链对账")
    p.set_defaults(fn=cmd_verify)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except Reject as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
