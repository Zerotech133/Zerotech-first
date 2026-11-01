# -*- coding: utf-8 -*-
"""xq_pump 命令行入口：把 5 类增量抽取收口成一个干净命令。

用法（从 quant_backtest/ 下执行）：
  python -m xq_pump.cli run --type posts --category 102 --max-new 200
  python -m xq_pump.cli run --type all --data-root ./xq_pump/data --max-new 500

注意：
- 真实抓取需网络 + 雪球 cookie（XQ_COOKIE 环境变量或 --cookie）。
- 缺 cookie 且匿名被 WAF 拦时，LiveSession 会抛错（不静默）。
- 本入口不接入代理池 / 请求闸 / RightsGate —— 只抓数据、逐步增量。
- 数据根 / 库 / 断点根均可配置，不硬编码 Windows 路径。
"""

from __future__ import annotations

import argparse
import os
import sys

from .core import CATEGORIES, LiveSession, FakeSession
from .cursor import CursorStore
from .store import Store
from .extractors import posts as ex_posts
from .extractors import hot as ex_hot
from .extractors import quotes as ex_quotes
from .extractors import kline as ex_kline
from .extractors import timeline as ex_timeline


def _build_session(live, cookie, proxy):
    if not live:
        return FakeSession()
    return LiveSession(cookie=cookie, proxy=proxy)


def _run_one(etype, session, store, cursor, args):
    if etype in ("posts", "all"):
        cats = [int(c) for c in (args.category or CATEGORIES)]
        return ex_posts.collect_posts(
            session, store, cursor, categories=cats,
            max_new=args.max_new, max_pages_per_cat=args.max_pages)
    if etype == "hot":
        return ex_hot.collect_hot(session, store, cursor,
                                  max_new=args.max_new, max_pages=args.max_pages)
    if etype == "quotes":
        syms = [s.strip() for s in (args.symbols or "").split(",") if s.strip()] or ["SH600519"]
        return ex_quotes.collect_quotes(session, store, cursor, symbols=syms)
    if etype == "kline":
        syms = [s.strip() for s in (args.symbols or "").split(",") if s.strip()] or ["SH600519"]
        return ex_kline.collect_kline(session, store, cursor, symbols=syms,
                                      period=args.period, count=args.count)
    if etype == "timeline":
        if not args.user_id:
            print("[timeline] 需 --user-id")
            return {"new": 0, "requests": 0}
        return ex_timeline.collect_timeline(session, store, cursor,
                                            user_id=args.user_id, max_new=args.max_new)
    raise ValueError("未知类型: %s" % etype)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="xq_pump",
                                 description="雪球增量抽水（免授权、逐步增量）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="运行一次增量抽取")
    r.add_argument("--type", required=True,
                   choices=["posts", "hot", "quotes", "kline", "timeline", "all"])
    r.add_argument("--category", help="类目 ID 列表，逗号分隔（默认 102,104,105,111）")
    r.add_argument("--symbols", help="行情/K线 标的，逗号分隔（默认 SH600519）")
    r.add_argument("--user-id", help="用户时间线 user_id")
    r.add_argument("--period", default="day", help="K线周期（默认 day）")
    r.add_argument("--count", type=int, default=240, help="K线条数 / 每页条数")
    r.add_argument("--max-new", type=int, default=1000, help="本次最多新增条数")
    r.add_argument("--max-pages", type=int, default=120, help="每个类目/榜单最多翻页数")
    r.add_argument("--data-root", default=None, help="数据根目录（默认 xq_pump/data）")
    r.add_argument("--db", default=None, help="SQLite 路径（默认复用 xueqiu_info.db 或新建）")
    r.add_argument("--cursor-root", default=None, help="断点根目录（默认 <data-root>/cursors）")
    r.add_argument("--cookie", default=None, help="雪球 cookie（也可走 XQ_COOKIE 环境变量）")
    r.add_argument("--proxy", default=None, help="可选代理（也可走 XQ_PROXY 环境变量）")
    r.add_argument("--live", action="store_true", help="真实抓取（默认 False，仅做导入自检）")
    r.add_argument("--dry", action="store_true", help="仅打印将跑的配置，不实际抓")

    args = ap.parse_args(argv)

    if args.cmd != "run":
        return 1

    data_root = args.data_root or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(data_root, exist_ok=True)
    cursor_root = args.cursor_root or os.path.join(data_root, "cursors")
    store = Store(args.db) if args.db else Store(os.path.join(data_root, "xq_pump.db"))
    cursor = CursorStore(cursor_root)

    print("[xq_pump] type=%s live=%s data_root=%s db=%s" % (
        args.type, args.live, data_root, store.db_path))

    if args.dry:
        print("[xq_pump] dry-run，未实际触发抽取。")
        store.close()
        return 0

    session = _build_session(args.live, args.cookie, args.proxy)
    if args.type == "all":
        for t in ("posts", "hot", "quotes", "kline"):
            res = _run_one(t, session, store, cursor, args)
            print("[xq_pump] %s -> %s" % (t, res))
    else:
        res = _run_one(args.type, session, store, cursor, args)
        print("[xq_pump] done -> %s" % res)
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
