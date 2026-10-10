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
from trainee import stop_dist_for               # noqa: E402  P-5 止损档案化

DATA_DIR = HERE.parent / "data"
DEFAULT_DB = DATA_DIR / "paper.db"

# US 账本止损距离：P-5 起档案化（trainees/us_qingping.json params.stop_dist_pct），
# 本常量降级为档案缺失时的回退默认（8% 成本锚定，K-STOCK-045c 原值）。
US_STOP_DIST = 0.08


def _stop_dist_of(aid: str) -> float:
    """档案化止损距离：trainees/<aid>.json 优先，缺失回退 US_STOP_DIST。"""
    return stop_dist_for(aid) or US_STOP_DIST


def _engine(args) -> PaperEngine:
    db = Path(args.db) if args.db else DEFAULT_DB
    # v0.3 多交易员子账本：--account 透传（main=主交易台，行为与 v0.2 完全一致）
    return PaperEngine(db, ParquetMarket(), account=getattr(args, "account", "main"))


def _newest_run() -> Path:
    # 排除 poc_qd_（双刀）/ poc_qpzm_（青萍 CN）/ poc_qpzmus_（青萍 US，044 M2）：
    # 否则 07:05 automation 的 exec --newest 会把他源信号当 AI 决策执行（策略串味）。
    # 口径与 trainee._newest_ai_run 对齐——AI 通道只吃 AI 产物。
    runs = sorted(x for x in DATA_DIR.glob("poc_*.json")
                  if not x.name.startswith(("poc_qd_", "poc_qpzm_", "poc_qpzmus_")))
    if not runs:
        raise Reject("k_stock/data/ 下无 poc_*.json 决策产物（AI 通道）")
    return runs[-1]


def _us_accounts(db: Path) -> list[str]:
    import sqlite3
    if not db.exists():
        return []
    con = sqlite3.connect(str(db))
    try:
        rows = con.execute(
            "SELECT DISTINCT account_id FROM account WHERE account_id LIKE 'us_%'").fetchall()
    finally:
        con.close()
    return sorted(r[0] for r in rows)


def _us_pass(args, do_refresh: bool = True):
    """US 独立账本通道（K-STOCK-044 M3）：存在 us_% 账本才跑；settle 前自动 refresh。

    refresh 只拉有真实敞口的票（pending+持仓）+ SPY 日历基准；单票失败降级记录，
    不炸 settle——缺 bar 的单由引擎「保持 pending」幂等等待次晨补。
    do_refresh=False（mark 用）：缓存已在 settle 段刷新过，避免双份全史拉取。
    返回 [(account_id, engine)] 与 refresh 摘要；无 US 账本返回 None。"""
    from us_market import UsMarket, refresh, us_symbols_in_db
    db = Path(args.db) if args.db else DEFAULT_DB
    aids = _us_accounts(db)
    if not aids:
        return None
    refreshed, failed = [], []
    if do_refresh:
        try:
            refresh("spy")
            refreshed.append("SPY")
        except Exception as ex:  # 日历失败仍可 settle（湖内已有 bar 的单照常成交）
            failed.append(f"SPY: {ex}")
        for sym in us_symbols_in_db(db):
            try:
                refresh(sym)
                refreshed.append(sym)
            except Exception as ex:  # noqa: BLE001
                failed.append(f"{sym}: {ex}")
    return {"accounts": aids,
            "engines": [(aid, PaperEngine(db, UsMarket(), account=aid)) for aid in aids],
            "refreshed": refreshed, "refresh_fail": failed}


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
        # P-5 main 止损拍板机制：距离读 trainees/main.json params.stop_dist_pct
        # （当前 null=未拍板不挂，零行为变化）；老田填值后此路径即自动生效。
        _d = stop_dist_for("main")
        if _d:
            _stops = []
            for pos in e.positions("main"):
                if pos.get("stop_price") is not None:
                    continue
                _stop = round(pos["avg_cost"] * (1 - _d), 2)
                e.set_stop(pos["stock"], _stop, account_id="main")
                _stops.append(f"{pos['stock']}@{_stop}")
            if _stops:
                print(f"  🛡️ main 止损已挂 {len(_stops)} 笔（-{_d:.0%} 成本锚定）: "
                      + ", ".join(_stops))
    # US 独立账本 pass（K-STOCK-044 M3）：us_% 账本 settle 前自动 refresh 行情缓存。
    # 红队审查 045a FIX-2：US 段整体 try——SPY 缓存缺失+refresh 失败的组合会让
    # settle() 抛 FileNotFoundError 炸 CLI（main 已成交但 exit 非零、mark 不跑）。
    us = _us_pass(args)
    if us:
        for aid, e_us in us["engines"]:
            try:
                r2 = e_us.settle(dry_run=args.dry_run)
            except Exception as ex:  # noqa: BLE001
                print(f"⚠️ US 账本 {aid} settle 失败（不阻塞主链）: {ex}")
                continue
            print(f"{tag}US 账本 {aid} 撮合: 成交 {len(r2['filled'])} / 搁置 {len(r2['rejected'])}"
                  f"（refresh {len(us['refreshed'])} 票"
                  f"{'，失败 ' + str(len(us['refresh_fail'])) if us['refresh_fail'] else ''}）")
            for f in r2["filled"]:
                print(f"  ✅ #{f.get('id', '-')} {f['stock']} {f['side']} {f['qty']} 股 @ {f['price']}"
                      f"（费 {f['fee']:.2f}）")
            for x in r2["rejected"]:
                print(f"  ❌ #{x['id']} {x['stock']}: {x['why']}")
            for w in us["refresh_fail"][:3]:
                print(f"  ⚠️ refresh 失败: {w}")
            if not args.dry_run:
                _print_eq(e_us)
            # G2（K-STOCK-045c，P-5 档案化）：成交后自动挂默认止损（成本锚定；
            # 距离读 trainees/<aid>.json——us_qingping=0.08 档案承载，缺失回退
            # US_STOP_DIST。已有止损不覆盖——同 trainee auto_stops 口径。
            # pending 未成交不挂（无成本锚）。
            stops_set = []
            for aid2, e2 in us["engines"]:
                dist = _stop_dist_of(aid2)
                for pos in e2.positions(aid2):
                    if pos.get("stop_price") is not None:
                        continue
                    stop = round(pos["avg_cost"] * (1 - dist), 2)
                    if not args.dry_run:
                        e2.set_stop(pos["stock"], stop, account_id=aid2)
                    stops_set.append(f"{aid2}:{pos['stock']}@{stop}")
            if stops_set:
                print(f"  🛡️ US 默认止损已挂 {len(stops_set)} 笔（成本锚定）: "
                      + ", ".join(stops_set))
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


def cmd_mark(args) -> int:
    e = _engine(args)
    r = e.mark()
    ok = "✅" if r["verify_ok"] else "❌"
    print(f"{ok} 快照 {r['date']}: 总资产 {r['total']:,.2f} 仓位 {r['pos_pct']:.1%} "
          f"待撮合 {r['pending']}")
    # US 独立账本快照（K-STOCK-044 M3）：us_% 账本各记一行（USD 原币口径）；
    # do_refresh=False——缓存已在 settle 段刷新，mark 不重复全史拉取（045a FIX-3）
    us = _us_pass(args, do_refresh=False)
    if us:
        for aid, e_us in us["engines"]:
            try:
                r2 = e_us.mark()
            except Exception as ex:  # noqa: BLE001
                print(f"⚠️ US {aid} 快照失败（不阻塞备份）: {ex}")
                continue
            ok2 = "✅" if r2["verify_ok"] else "❌"
            print(f"{ok2} US {aid} 快照: 总资产 {r2['total']:,.2f} 仓位 {r2['pos_pct']:.1%} "
                  f"待撮合 {r2['pending']}")
    # 账本轻量灾备：*.db 不入 git 也不在 B4 备份链，mark 成功后保留最近一份副本
    try:
        import shutil
        bak = Path(str(e.db_path) + ".bak")
        shutil.copy2(e.db_path, bak)
        print(f"💾 账本已备份 -> {bak.name}")
    except Exception as ex:  # 备份失败不阻塞主流程
        print(f"⚠️ 账本备份失败（不影响快照）: {ex}", file=sys.stderr)
    return 0


def cmd_curve(args) -> int:
    e = _engine(args)
    rows = e.curve()
    if not rows:
        print("（无快照，跑 paper.py mark 生成）")
        return 0
    base = rows[0]["total"] or 1.0
    print(f"权益曲线（{len(rows)} 日，基线 {base:,.2f}）:")
    for r in rows:
        chg = (r["total"] / base - 1) * 100
        ok = "✅" if r["verify_ok"] else "❌"
        print(f"  {r['date']}  总资产 {r['total']:>12,.2f}  累计 {chg:+.2f}%  "
              f"仓位 {r['pos_pct']:>5.1%}  待撮合 {r['pending']}  {ok}")
    return 0


def cmd_stop(args) -> int:
    e = _engine(args)
    if args.clear:
        r = e.clear_stop(args.stock)
        print(f"🔓 已清除 {r['stock']} 止损")
        return 0
    if args.price is None:
        raise Reject("需要 --price 或 --clear")
    r = e.set_stop(args.stock, args.price)
    print(f"🛑 止损已设: {r['stock']} {r['qty']} 股 @ 止损价 {r['stop_price']}（收盘触发，次日开盘清仓）")
    return 0


def cmd_stops(args) -> int:
    e = _engine(args)
    rows = e.stops()
    if not rows:
        print("（无已设止损）")
    else:
        print(f"已设止损 {len(rows)} 笔:")
        for r in rows:
            d = f"{r['distance_pct']:+.2f}%" if r["distance_pct"] is not None else "-"
            print(f"  {r['stock']}  {r['qty']} 股  止损 {r['stop_price']}  现价 {r['last']}  距离 {d}")
    # US 账本止损展示（045c G2；轻量：不 refresh，用现有缓存）
    us = _us_pass(args, do_refresh=False)
    if us:
        for aid, e_us in us["engines"]:
            try:
                rows2 = e_us.stops(account_id=aid)
            except Exception as ex:  # noqa: BLE001
                print(f"⚠️ US {aid} stops 查询失败: {ex}")
                continue
            if not rows2:
                print(f"US {aid}: （无已设止损）")
                continue
            print(f"US {aid} 已设止损 {len(rows2)} 笔:")
            for r in rows2:
                d = f"{r['distance_pct']:+.2f}%" if r["distance_pct"] is not None else "-"
                print(f"  {r['stock']}  {r['qty']} 股  止损 {r['stop_price']}  现价 {r['last']}  距离 {d}")
    return 0


def cmd_check_stops(args) -> int:
    e = _engine(args)
    r = e.check_stops(dry_run=args.dry_run)
    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}止损检查: 触发 {len([t for t in r['triggered'] if t.get('status')])} "
          f"/ 观察 {len(r['watch'])}")
    for t in r["triggered"]:
        print(f"  🛑 {t['stock']}: {t['why']}")
    for w in r["watch"]:
        print(f"  👀 {w['stock']}: 收盘 {w['close']} ≥ 止损 {w['stop']}")
    # US 账本止损检查（045c G2）：触发判定要最新收盘 → do_refresh=True（独立跑也安全）
    us = _us_pass(args)
    if us:
        for aid, e_us in us["engines"]:
            try:
                r2 = e_us.check_stops(dry_run=args.dry_run, account_id=aid)
            except Exception as ex:  # noqa: BLE001
                print(f"⚠️ US {aid} 止损检查失败（不阻塞主链）: {ex}")
                continue
            print(f"{tag}US {aid} 止损检查: 触发 {len([t for t in r2['triggered'] if t.get('status')])} "
                  f"/ 观察 {len(r2['watch'])}")
            for t in r2["triggered"]:
                print(f"  🛑 {t['stock']}: {t['why']}")
            for w in r2["watch"]:
                print(f"  👀 {w['stock']}: 收盘 {w['close']} ≥ 止损 {w['stop']}")
    return 0


def cmd_order(args) -> int:
    """统一下单入口（v0.2 平台化）：BUY/SELL/STOP 三合一，--json 供门户 API
    子进程调用（portal_api 保持纯标准库，引擎走本 CLI）。"""
    e = _engine(args)
    try:
        if args.side == "BUY":
            if not args.qty or args.qty <= 0 or args.qty % 100:
                raise Reject("qty 须为 100 的整数倍股数")
            r = e.buy(args.stock, args.qty, args.price, force=args.force)
            if args.stop_price is not None:
                s = e.set_stop(args.stock, args.stop_price)
                r["stop_price"] = s["stop_price"]
        elif args.side == "SELL":
            if not args.all and not args.qty:
                raise Reject("SELL 需要 --qty 或 --all")
            r = e.sell(args.stock, None if args.all else args.qty,
                       args.price, force=args.force)
        elif args.side == "STOP":
            if args.clear:
                r = e.clear_stop(args.stock)
            elif args.stop_price is not None:
                r = e.set_stop(args.stock, args.stop_price)
            else:
                raise Reject("STOP 需要 --stop-price 或 --clear")
        else:
            raise Reject(f"未知 side: {args.side}")
    except Reject as ex:
        if args.json:
            print(json.dumps({"ok": False, "error": str(ex)}, ensure_ascii=False))
            return 2
        raise
    if args.json:
        print(json.dumps({"ok": True, "order": r}, ensure_ascii=False))
    else:
        print(f"✅ {args.side} {args.stock} 完成: "
              + json.dumps(r, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # v0.3 多交易员子账本：--account 全局透传（位置不限，缺省 main=主交易台）
    account = "main"
    if "--account" in argv:
        i = argv.index("--account")
        if i + 1 < len(argv):
            account = argv[i + 1]
            del argv[i:i + 2]
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

    p = sub.add_parser("mark", help="记录当日权益快照（automation 每日收尾调用）")
    p.set_defaults(fn=cmd_mark)

    p = sub.add_parser("curve", help="查看权益曲线（历史快照）")
    p.set_defaults(fn=cmd_curve)

    p = sub.add_parser("stop", help="设置/清除持仓止损价（收盘触发→次日开盘清仓）")
    p.add_argument("--stock", required=True)
    p.add_argument("--price", type=float, default=None)
    p.add_argument("--clear", action="store_true")
    p.set_defaults(fn=cmd_stop)

    p = sub.add_parser("stops", help="列出已设止损（含触发距离）")
    p.set_defaults(fn=cmd_stops)

    p = sub.add_parser("check-stops", help="执行止损检查（automation 每日 settle 后调用）")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_check_stops)

    p = sub.add_parser("order", help="统一下单入口 BUY/SELL/STOP（--json 供门户 API）")
    p.add_argument("--side", required=True, choices=["BUY", "SELL", "STOP"])
    p.add_argument("--stock", required=True)
    p.add_argument("--qty", type=int, default=None)
    p.add_argument("--all", action="store_true")
    p.add_argument("--price", type=float, default=None)
    p.add_argument("--stop-price", dest="stop_price", type=float, default=None)
    p.add_argument("--clear", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_order)

    args = ap.parse_args(argv)
    args.account = account   # v0.3 子账本透传（_engine 消费）
    try:
        return args.fn(args)
    except Reject as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
