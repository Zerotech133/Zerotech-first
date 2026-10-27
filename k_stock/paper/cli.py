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
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from engine import PaperEngine, Reject  # noqa: E402
from market import ParquetMarket  # noqa: E402
from trainee import stop_dist_for, _newest_ai_run  # noqa: E402  P-5 止损档案化

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
    """最新 AI 决策产物——委托 trainee._newest_ai_run（单一口径源，不复刻第二份）。

    WO-QT-20261020-049 勘误（实测陷阱，证据见 -046 取证）：原实现
    sorted(glob)[按文件名字典序] 取尾——poc_pf_*/poc_adapter_* 前缀首字母
    （'p'/'a'）恒大于日期戳开头（'2'），10-15 的旧产物 poc_pf_002_20261015
    会压过 10-20 的新产物 poc_20261020_211855 被当"最新"，默认 exec 落到
    全陈旧跳过；且原排除面缺 poc_pf_/poc_adapter_ 两族（K-STOCK-414）。
    trainee 侧已由 WO-QT-20261020-017 修为 mtime_ns + 文件名确定性破平
    （_newest_by_mtime），排除面同款补全——本侧直接对齐，勿再各自演化。
    """
    return _newest_ai_run()


def _us_accounts(db: Path) -> list[str]:
    import sqlite3

    if not db.exists():
        return []
    con = sqlite3.connect(str(db))
    try:
        rows = con.execute(
            "SELECT DISTINCT account_id FROM account WHERE account_id LIKE 'us_%'"
        ).fetchall()
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
    return {
        "accounts": aids,
        "engines": [(aid, PaperEngine(db, UsMarket(), account=aid)) for aid in aids],
        "refreshed": refreshed,
        "refresh_fail": failed,
    }


def _print_eq(e: PaperEngine) -> None:
    eq = e.equity()
    v = e.verify()
    print(
        f"现金 {eq['cash']:>12,.2f} | 持仓市值 {eq['market_value']:>12,.2f} "
        f"| 总资产 {eq['total']:>12,.2f} | 仓位 {eq['pos_pct']:.1%} | 对账 {'OK' if v['ok'] else '❌FAIL'}"
    )
    for p in eq["positions"]:
        print(
            f"  {p['stock']}  {p['qty']:>6} 股（可卖 {p['available']:>6}）"
            f" 成本 {p['avg_cost']:.2f} 现价 {p['last']:.2f} "
            f"市值 {p['market_value']:>12,.2f} 盈亏 {p['pnl']:>+10,.2f}"
        )


def cmd_status(args) -> int:
    e = _engine(args)
    _print_eq(e)
    pend = e.pending()
    if pend:
        print(f"待撮合 {len(pend)} 单:")
        for p in pend:
            print(
                f"  #{p['id']} [{p['source']}] {p['side']} {p['stock']} {p['qty']} 股"
                f" @ {p['plan_date']} 开盘 | {p['reason']}"
            )
    return 0


def cmd_buy(args) -> int:
    e = _engine(args)
    r = e.buy(args.stock, args.qty, args.price, force=args.force, ref=args.ref)
    print(
        f"✅ 成交: BUY {r['stock']} {r['qty']} 股 @ {r['price']}（费 {r['fee']:.2f}）"
        f" 现金余 {r['cash_after']:,.2f}"
    )
    return 0


def cmd_sell(args) -> int:
    e = _engine(args)
    qty = None if args.all else args.qty
    r = e.sell(args.stock, qty, args.price, force=args.force, ref=args.ref)
    print(
        f"✅ 成交: SELL {r['stock']} {r['qty']} 股 @ {r['price']}（费 {r['fee']:.2f}）"
        f" 现金余 {r['cash_after']:,.2f}"
    )
    return 0


def cmd_exec(args) -> int:
    e = _engine(args)
    run = Path(args.run) if args.run else _newest_run()
    r = e.exec_run(run, dry_run=args.dry_run)
    tag = "[dry-run] " if args.dry_run else ""
    print(
        f"{tag}决策执行 {r['run']}: 落单 {len([o for o in r['orders'] if o.get('status') == 'pending'])} 笔"
    )
    for o in r["orders"]:
        print(
            f"  [{o['status']}] {o['side']} {o['stock']} {o['qty']} 股 @ {o['plan_date']} 开盘"
            f" | {o['reason']}"
        )
    for s in r["skipped"]:
        print(f"  [skip] {s['stock']}: {s['why']}")
    return 0


def cmd_settle(args) -> int:
    e = _engine(args)
    r = e.settle(dry_run=args.dry_run)
    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}撮合: 成交 {len(r['filled'])} / 拒绝或搁置 {len(r['rejected'])}")
    for f in r["filled"]:
        print(
            f"  ✅ #{f.get('id', '-')} {f['stock']} {f['side']} {f['qty']} 股 @ {f['price']}"
            f"（费 {f['fee']:.2f}）"
        )
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
                print(
                    f"  🛡️ main 止损已挂 {len(_stops)} 笔（-{_d:.0%} 成本锚定）: "
                    + ", ".join(_stops)
                )
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
            print(
                f"{tag}US 账本 {aid} 撮合: 成交 {len(r2['filled'])} / 搁置 {len(r2['rejected'])}"
                f"（refresh {len(us['refreshed'])} 票"
                f"{'，失败 ' + str(len(us['refresh_fail'])) if us['refresh_fail'] else ''}）"
            )
            for f in r2["filled"]:
                print(
                    f"  ✅ #{f.get('id', '-')} {f['stock']} {f['side']} {f['qty']} 股 @ {f['price']}"
                    f"（费 {f['fee']:.2f}）"
                )
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
                print(
                    f"  🛡️ US 默认止损已挂 {len(stops_set)} 笔（成本锚定）: "
                    + ", ".join(stops_set)
                )
    return 0


def cmd_pending(args) -> int:
    e = _engine(args)
    pend = e.pending()
    print(f"待撮合 {len(pend)} 单")
    for p in pend:
        print(
            f"  #{p['id']} [{p['source']}] {p['side']} {p['stock']} {p['qty']} 股"
            f" @ {p['plan_date']} 开盘 | {p['reason']}"
        )
    return 0


def cmd_history(args) -> int:
    e = _engine(args)
    rows = e.history(args.limit)
    print(f"最近 {len(rows)} 笔订单:")
    for r in rows:
        px = f"{r['filled_price']:.2f}" if r["filled_price"] else "-"
        print(
            f"  #{r['id']} {r['ts'][:16]} [{r['source']}] {r['status']:>8} "
            f"{r['side']} {r['stock']} {r['qty']} @ {px}"
            f"{'（' + str(r['reason']) + '）' if r['reason'] and r['status'] != 'filled' else ''}"
        )
    return 0


def cmd_reset(args) -> int:
    e = _engine(args)
    r = e.reset(args.cash, yes=args.yes)
    print(f"♻️ 账户已重置: 现金 {r['cash']:,.2f}")
    return 0


def cmd_verify(args) -> int:
    e = _engine(args)
    v = e.verify()
    print(
        ("✅ 对账通过: " if v["ok"] else "❌ 对账失败: ")
        + json.dumps(v, ensure_ascii=False)
    )
    return 0 if v["ok"] else 1


def _mark_accounts(db: Path, requested: list[str] | None = None) -> list[str]:
    """只从已开户实体取账户；显式目标不存在时拒绝，绝不隐式注资开户。"""
    import sqlite3

    if not db.is_file():
        raise Reject(f"账本不存在: {db}")
    con = sqlite3.connect(str(db))
    try:
        existing = {row[0] for row in con.execute("SELECT account_id FROM account")}
    except sqlite3.Error as ex:
        raise Reject(f"读取账户表失败: {ex}") from ex
    finally:
        con.close()
    if requested is None:
        return sorted(existing)
    missing = set(requested) - existing
    if missing:
        raise Reject(f"账户未开户（不会自动创建）: {', '.join(sorted(missing))}")
    return sorted(set(requested))


# 2026 年度证券日历：上交所上证公告〔2025〕45号、HKEX
# cc_SEHK_CT_075_2025、NYSE Holidays & Trading Hours。年度外一律失败关闭，
# 特别临时休市/收市须运维核交易所当日公告，不以本静态基线冒充实时权威。
_MARK_HOLIDAYS_2026 = {
    "CN": {
        "0101",
        "0102",
        *[f"02{d:02d}" for d in range(15, 24)],
        "0406",
        *[f"05{d:02d}" for d in range(1, 6)],
        "0619",
        "0925",
        *[f"10{d:02d}" for d in range(1, 8)],
    },
    "HK": {
        "0101",
        "0217",
        "0218",
        "0219",
        "0403",
        "0406",
        "0407",
        "0501",
        "0525",
        "0619",
        "0701",
        "1001",
        "1019",
        "1225",
    },
    "US": {
        "0101",
        "0119",
        "0216",
        "0403",
        "0525",
        "0619",
        "0703",
        "0907",
        "1126",
        "1225",
    },
}
_MARK_EARLY_2026 = {"HK": {"0216", "1224", "1231"}, "US": {"1127", "1224"}}


def _mark_market(aid: str) -> tuple[str, str, str]:
    if aid.startswith("us_"):
        return "US", "America/New_York", "USD"
    if aid.startswith("hk_"):
        return "HK", "Asia/Hong_Kong", "HKD"
    return "CN", "Asia/Shanghai", "CNY"


def _expected_mark_day(aid: str, now=None) -> str:
    """独立于行情湖的已完成市场日；日期越界/时区数据库缺失则拒绝。"""
    from datetime import datetime, time, timedelta
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    market, zone, _ = _mark_market(aid)
    try:
        bj = ZoneInfo("Asia/Shanghai")
        local = ZoneInfo(zone)
        if now is None:
            now = datetime.now(bj)
        if now.tzinfo is None or now.utcoffset() is None:
            raise Reject("快照时钟必须带时区")
        at = now.astimezone(local)
    except ZoneInfoNotFoundError as ex:
        raise Reject(f"缺交易所时区数据库: {ex}") from ex
    if at.year != 2026:
        raise Reject(f"市场日历未覆盖 {at.year} 年，禁止估值")
    for delta in range(0, 12):
        day = at.date() - timedelta(days=delta)
        if day.year != 2026:
            # 年初合法交易日可能在去年；当前仅有 2026 年日历，不跨年猜测。
            raise Reject(f"市场日历未覆盖 {day.year} 年，禁止估值")
        mmdd = day.strftime("%m%d")
        if day.weekday() >= 5 or mmdd in _MARK_HOLIDAYS_2026[market]:
            continue
        end = (
            (time(13, 0) if mmdd in _MARK_EARLY_2026.get(market, ()) else time(16, 0))
            if market == "US"
            else (
                time(12, 10)
                if market == "HK" and mmdd in _MARK_EARLY_2026["HK"]
                else time(16, 10)
                if market == "HK"
                else time(15, 0)
            )
        )
        # 等到收市价生成后预留 10 分钟，不把刚收市而数据尚未固化的值认作可用。
        closed = datetime.combine(day, end, tzinfo=local) + timedelta(minutes=10)
        if at >= closed:
            return day.strftime("%Y%m%d")
    raise Reject(f"{market} 最近已完成交易日不可确定")


def _check_mark_ready(e: PaperEngine, aid: str, now=None) -> tuple[str, dict]:
    """写前资金闸+独立市场目标日+湖日历+逐仓价日；返回估值日及冻结价格。"""
    import math

    if not e.verify(aid)["ok"]:
        raise Reject(f"{aid} 资金或持仓对账失败，不写权益快照")
    expected = _expected_mark_day(aid, now)
    positions = e.positions(aid)
    days = e.market.trading_days()
    if not days or any(
        not isinstance(d, str) or len(d) != 8 or not d.isdigit() for d in days
    ):
        raise Reject(f"{aid} 市场交易日历缺失或格式异常，不写权益快照")
    if max(days) != expected:
        raise Reject(
            f"{aid} 行情湖日历最新 {max(days)} 与市场目标日 {expected} 不符，不写权益快照"
        )
    checked = {}
    for pos in positions:
        bar = e.market.last_bar(pos["stock"])
        if (
            not bar
            or not isinstance(bar.get("close"), (int, float))
            or not math.isfinite(bar["close"])
            or bar["close"] <= 0
        ):
            raise Reject(f"{aid} {pos['stock']} 缺有效收盘价，不写权益快照")
        actual = bar.get("trade_date")
        if not isinstance(actual, str) or actual != expected:
            raise Reject(
                f"{aid} {pos['stock']} 行情日期滞后或异常: {actual!r}，"
                f"市场目标日 {expected}，不写权益快照"
            )
        checked[pos["stock"]] = dict(bar)
    return expected, checked


def _mark_selected(args, db: Path, accounts: list[str]) -> int:
    """按账户市场原币估值；单账户失败须退出非零，不以部分成功冒充全绿。"""
    from us_market import UsMarket
    from hk_market import HkMarket
    from market import ParquetMarket

    if not accounts:
        print("❌ 无已开户账户，未生成任何快照", file=sys.stderr)
        return 1
    from datetime import datetime
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        captured = datetime.now(ZoneInfo("Asia/Shanghai"))
    except ZoneInfoNotFoundError as ex:
        print(f"❌ 缺北京时间数据库: {ex}", file=sys.stderr)
        return 1
    snapshot_day = captured.date().isoformat()
    failed = []
    for aid in accounts:
        e = None
        try:
            market = (
                UsMarket()
                if aid.startswith("us_")
                else (HkMarket() if aid.startswith("hk_") else ParquetMarket())
            )
            e = PaperEngine(db, market, account=aid)
            valuation_day, checked_bars = _check_mark_ready(e, aid, captured)
            # mark/equity 会再次读取 last_bar；必须只读到本轮已验证的价格，
            # 否则湖在预检与落库间更新会发生 TOCTOU（含成本价兜底）。
            original_market = e.market

            class _FrozenMarket:
                def last_bar(self, stock):
                    return checked_bars.get(stock)

                def __getattr__(self, name):
                    return getattr(original_market, name)

            e.market = _FrozenMarket()
            try:
                r = e.mark(day=snapshot_day)
            finally:
                e.market = original_market
            if not r["verify_ok"]:
                failed.append(aid)
            _, _, currency = _mark_market(aid)
            print(
                f"{'✅' if r['verify_ok'] else '❌'} [{aid}] 北京快照日 {r['date']} "
                f"市场估值日 {valuation_day} 原币 {currency}: "
                f"总资产 {r['total']:,.2f} 仓位 {r['pos_pct']:.1%} "
                f"待撮合 {r['pending']}"
            )
        except Exception as ex:  # noqa: BLE001 - 逐账户报错，完整尝试后返回非零
            failed.append(aid)
            print(f"❌ [{aid}] 快照失败: {ex}", file=sys.stderr)
        finally:
            if e is not None:
                try:
                    e.con.close()
                except Exception as ex:  # noqa: BLE001
                    if aid not in failed:
                        failed.append(aid)
                    print(f"❌ [{aid}] 账本连接关闭失败: {ex}", file=sys.stderr)
    if failed:
        print(
            f"❌ 快照未完成 {len(failed)}/{len(accounts)} 账户: {', '.join(failed)}",
            file=sys.stderr,
        )
        return 1
    # 全账户均成功才交付备份；SQLite backup API 保证一致快照（含 WAL）。
    # 失败前已成功账户已提交，不能将返回非零误解为整批回滚。
    try:
        import sqlite3
        import tempfile

        bak = Path(str(db) + ".bak")
        fd, temp_name = tempfile.mkstemp(
            prefix="mark_backup_", suffix=".db", dir=db.parent
        )
        import os

        os.close(fd)
        temp = Path(temp_name)
        try:
            source = sqlite3.connect(str(db))
            target = sqlite3.connect(str(temp))
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
            temp.replace(bak)
        finally:
            temp.unlink(missing_ok=True)
        print(f"💾 账本已备份 -> {bak.name}")
    except Exception as ex:  # noqa: BLE001
        print(f"❌ 账本备份失败: {ex}", file=sys.stderr)
        return 1
    return 0


def cmd_mark(args) -> int:
    db = Path(args.db) if args.db else DEFAULT_DB
    explicit_account = getattr(args, "account_explicit", False)
    if explicit_account and (args.all_accounts or args.mark_accounts):
        raise Reject("--account 不能与 --all-accounts/--mark-account 并用")
    if args.all_accounts or args.mark_accounts or explicit_account:
        requested = (
            None
            if args.all_accounts
            else ([args.account] if explicit_account else args.mark_accounts)
        )
        return _mark_selected(args, db, _mark_accounts(db, requested))
    # 保留原覆盖范围 main + us_*，但统一走写前对账/行情闸与失败非零通道。
    # 不自动开户，缺 main 显式拒绝；不再把 US 缺价/失败吞掉并报全绿。
    requested = ["main", *_us_accounts(db)]
    return _mark_selected(args, db, _mark_accounts(db, requested))


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
        print(
            f"  {r['date']}  总资产 {r['total']:>12,.2f}  累计 {chg:+.2f}%  "
            f"仓位 {r['pos_pct']:>5.1%}  待撮合 {r['pending']}  {ok}"
        )
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
    print(
        f"🛑 止损已设: {r['stock']} {r['qty']} 股 @ 止损价 {r['stop_price']}（收盘触发，次日开盘清仓）"
    )
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
            print(
                f"  {r['stock']}  {r['qty']} 股  止损 {r['stop_price']}  现价 {r['last']}  距离 {d}"
            )
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
                d = (
                    f"{r['distance_pct']:+.2f}%"
                    if r["distance_pct"] is not None
                    else "-"
                )
                print(
                    f"  {r['stock']}  {r['qty']} 股  止损 {r['stop_price']}  现价 {r['last']}  距离 {d}"
                )
    return 0


def cmd_check_stops(args) -> int:
    e = _engine(args)
    r = e.check_stops(dry_run=args.dry_run)
    tag = "[dry-run] " if args.dry_run else ""
    print(
        f"{tag}止损检查: 触发 {len([t for t in r['triggered'] if t.get('status')])} "
        f"/ 观察 {len(r['watch'])}"
    )
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
            print(
                f"{tag}US {aid} 止损检查: 触发 {len([t for t in r2['triggered'] if t.get('status')])} "
                f"/ 观察 {len(r2['watch'])}"
            )
            for t in r2["triggered"]:
                print(f"  🛑 {t['stock']}: {t['why']}")
            for w in r2["watch"]:
                print(f"  👀 {w['stock']}: 收盘 {w['close']} ≥ 止损 {w['stop']}")
    return 0


def _resolve_plan_date(e, spec, asof=None) -> str:
    """--plan-date 解析：'d2' = asof（缺省今天）后第 2 个交易日（D+2 开盘口径，
    与 pick_verdicts win 口径对齐）；否则须为 YYYYMMDD。
    注意 _next_td_impl 盘前（hhmm<0930）含当日 ⇒ 必须传盘后 19 字符 ISO
    才能取到「严格之后」的交易日（与 AsOfMarket 同一范式）。"""
    if spec.lower() == "d2":
        asof = asof or date.today().strftime("%Y%m%d")
        base = f"{asof[:4]}-{asof[4:6]}-{asof[6:8]}T15:00:00"  # 盘后视角
        d1 = e.market.next_trading_day(base)
        if d1 is None:
            raise Reject(f"行情湖无 {asof} 之后的交易日，无法定 D+1")
        d2 = e.market.next_trading_day(f"{d1[:4]}-{d1[4:6]}-{d1[6:8]}T15:00:00")
        if d2 is None:
            raise Reject(f"行情湖无 {d1} 之后的交易日，无法定 D+2")
        return d2
    if len(spec) == 8 and spec.isdigit():
        return spec
    raise Reject(f"--plan-date 须为 YYYYMMDD 或 d2，收到: {spec}")


def cmd_order(args) -> int:
    """统一下单入口（v0.2 平台化）：BUY/SELL/STOP 三合一，--json 供门户 API
    子进程调用（portal_api 保持纯标准库，引擎走本 CLI）。
    v0.4：BUY 可选 --plan-date（YYYYMMDD 或 d2）落 pending 单（选票桥通道），
    按计划日开盘价撮合；缺省保持 v0.2 即时成交语义，完全向后兼容。"""
    e = _engine(args)
    try:
        if args.side == "BUY":
            if not args.qty or args.qty <= 0 or args.qty % 100:
                raise Reject("qty 须为 100 的整数倍股数")
            if args.plan_date:
                pd = _resolve_plan_date(e, args.plan_date, getattr(args, "asof", None))
                r = e.plan_bridge_buy(
                    args.stock,
                    args.qty,
                    pd,
                    args.ref or f"bridge#{args.stock}#{pd}",
                    strategy=args.strategy or "pick_bridge",
                    trace_id=args.trace_id or None,
                    fact_hash=args.fact_hash or None,
                    decision_id=args.decision_id or None,
                    idempotency_key=args.idempotency_key or None,
                )
            else:
                r = e.buy(args.stock, args.qty, args.price, force=args.force)
            if args.stop_price is not None:
                s = e.set_stop(args.stock, args.stop_price)
                r["stop_price"] = s["stop_price"]
        elif args.side == "SELL":
            if not args.all and not args.qty:
                raise Reject("SELL 需要 --qty 或 --all")
            r = e.sell(
                args.stock, None if args.all else args.qty, args.price, force=args.force
            )
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
        print(f"✅ {args.side} {args.stock} 完成: " + json.dumps(r, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # v0.3 多交易员子账本：--account 全局透传（位置不限，缺省 main=主交易台）
    account = "main"
    account_explicit = "--account" in argv
    if account_explicit:
        i = argv.index("--account")
        if i + 1 >= len(argv) or not argv[i + 1] or argv[i + 1].startswith("-"):
            raise SystemExit("--account 需要有效账户 ID")
        account = argv[i + 1]
        del argv[i : i + 2]
    ap = argparse.ArgumentParser(
        prog="paper", description="K-STOCK 模拟仓（纸面交易账本）"
    )
    ap.add_argument(
        "--db", default=None, help="账本 SQLite 路径（默认 k_stock/data/paper.db）"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("status", help="账户/持仓/盈亏概览")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser(
        "buy", help="手动买入（按最近收盘价即时成交，或 --price 指定价）"
    )
    p.add_argument("--stock", required=True)
    p.add_argument("--qty", type=int, required=True, help="股数（100 整数手）")
    p.add_argument("--price", type=float, default=None)
    p.add_argument("--force", action="store_true", help="跳过风控三条（测试极端场景）")
    p.add_argument(
        "--ref",
        default="",
        help="幂等引用（K-STOCK-195 规范：建议 manual-YYYYMMDD-用途；同日同 ref 只允许一条成交，C5 幂等判据保护面）",
    )
    p.set_defaults(fn=cmd_buy)

    p = sub.add_parser("sell", help="手动卖出（--all 清可卖仓）")
    p.add_argument("--stock", required=True)
    p.add_argument("--qty", type=int, default=None)
    p.add_argument("--all", action="store_true")
    p.add_argument("--price", type=float, default=None)
    p.add_argument("--force", action="store_true")
    p.add_argument("--ref", default="", help="幂等引用（同 buy --ref）")
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
    targets = p.add_mutually_exclusive_group()
    targets.add_argument(
        "--all-accounts",
        action="store_true",
        help="按已开户实体遍历全部账户（按市场原币估值）",
    )
    targets.add_argument(
        "--mark-account",
        dest="mark_accounts",
        action="append",
        help="仅快照指定账户，可重复；不存在则拒绝且不自动开户",
    )
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

    p = sub.add_parser(
        "check-stops", help="执行止损检查（automation 每日 settle 后调用）"
    )
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
    p.add_argument(
        "--plan-date",
        dest="plan_date",
        default=None,
        help="BUY 专用：YYYYMMDD 或 d2（asof 后第 2 个交易日）→ 落 pending 单",
    )
    p.add_argument("--asof", default=None, help="d2 基准日（缺省今天），格式 YYYYMMDD")
    p.add_argument("--ref", default=None, help="pending 单幂等键（选票桥传 trace_id）")
    p.add_argument("--trace-id", dest="trace_id", default=None)
    p.add_argument("--fact-hash", dest="fact_hash", default=None)
    p.add_argument("--decision-id", dest="decision_id", default=None)
    p.add_argument("--idempotency-key", dest="idempotency_key", default=None)
    p.add_argument(
        "--strategy", default=None, help="pending 单策略标签（缺省 pick_bridge）"
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_order)

    args = ap.parse_args(argv)
    args.account = account  # v0.3 子账本透传（_engine 消费）
    args.account_explicit = account_explicit
    try:
        return args.fn(args)
    except Reject as e:
        print(f"❌ {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
