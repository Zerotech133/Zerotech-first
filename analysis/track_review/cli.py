# -*- coding: utf-8 -*-
"""命令行入口：``python -m analysis.track_review <subcommand>``。

子命令：

- ``card <code> --end <date>``：单股复盘卡（复用 TR0 口径，产物加 ``_tr1`` 后缀）
- ``toplist --date <date> [--days N] [--limit N]``：每日盘后事件—价格影响 Top 列表
- ``build-db --end <date> [--days N]``：把窗口内事件/CAR/案例幂等写入 track.db
"""
from __future__ import annotations

import argparse
import json
import sys

from .benchmark import BenchmarkProvider
from .card import build_card
from .config import Paths, default_paths
from .toplist import build_db, build_toplist


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m analysis.track_review",
        description="TR1 事件—价格影响跟踪复盘（只读真实数据）",
    )
    parser.add_argument("--curated-dir", default=None, help="covered: tushare_curated 目录（默认内置）")
    parser.add_argument("--tdx-dir", default=None, help="tdx 数据目录（默认内置）")
    parser.add_argument("--work-root", default=None, help="产物根目录（默认 analysis_work/track_review）")
    parser.add_argument("--db", default=None, help="track.db 路径（覆盖环境变量 TRACK_DB）")

    sub = parser.add_subparsers(dest="command", required=True)

    card = sub.add_parser("card", help="单股事件—价格影响复盘卡")
    card.add_argument("code", help="ts_code，例如 600337.SH")
    card.add_argument("--end", default=None, help="截止交易日 YYYYMMDD（默认今天）")
    card.add_argument("--days", type=int, default=250, help="回看交易日数（默认 250）")
    card.add_argument("--top", type=int, default=15, help="时间线保留事件条数（默认 15）")
    card.add_argument("--no-write", action="store_true", help="只算不写产物（调试用）")

    toplist = sub.add_parser("toplist", help="每日盘后事件—价格影响 Top 列表")
    toplist.add_argument("--date", default=None, help="目标交易日 YYYYMMDD（默认今天）")
    toplist.add_argument("--days", type=int, default=10, help="事件窗口交易日数（默认 10）")
    toplist.add_argument("--limit", type=int, default=20, help="输出条数（默认 20）")
    toplist.add_argument("--no-write", action="store_true", help="只算不写产物（调试用）")

    build_db_cmd = sub.add_parser("build-db", help="事件/CAR/案例写入 track.db")
    build_db_cmd.add_argument("--end", default=None, help="截止交易日 YYYYMMDD（默认今天）")
    build_db_cmd.add_argument("--days", type=int, default=10, help="事件窗口交易日数（默认 10）")
    build_db_cmd.add_argument("--limit", type=int, default=50, help="cases 保留条数（默认 50）")
    return parser


def _paths(args: argparse.Namespace) -> Paths:
    base = default_paths()
    curated = getattr(args, "curated_dir", None) or base.curated
    tdx = getattr(args, "tdx_dir", None) or base.tdx
    work_root = getattr(args, "work_root", None) or base.work_root
    return Paths(curated=curated, tdx=tdx, work_root=work_root)


def _print(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    paths = _paths(args)
    provider = BenchmarkProvider(paths)
    db_path = getattr(args, "db", None)

    if args.command == "card":
        payload = build_card(
            args.code,
            end=args.end,
            days=args.days,
            top=args.top,
            paths=paths,
            provider=provider,
            write_outputs=not args.no_write,
        )
        meta = payload.get("meta") or {}
        comparison = payload.get("tr0_comparison") or {}
        summary = {
            "command": "card",
            "code": meta.get("code"),
            "name": meta.get("name"),
            "start": meta.get("start"),
            "end": meta.get("end"),
            "trade_days": meta.get("trade_days"),
            "industry": meta.get("industry"),
            "industry_benchmark": (meta.get("industry_benchmark") or {}).get("method_label"),
            "events_total": meta.get("events_total"),
            "events_aligned": meta.get("events_aligned"),
            "outputs": meta.get("outputs"),
            "tr0_comparison": {
                "available": comparison.get("available"),
                "events_total": comparison.get("events_total"),
                "events_aligned": comparison.get("events_aligned"),
                "trade_days": comparison.get("trade_days"),
                "industry_layer": comparison.get("industry_layer"),
            },
        }
        _print(summary)
        return 0

    if args.command == "toplist":
        payload = build_toplist(
            date=args.date,
            days=args.days,
            limit=args.limit,
            paths=paths,
            provider=provider,
            write_outputs=not args.no_write,
        )
        meta = payload.get("meta") or {}
        summary = {
            "command": "toplist",
            "requested_date": meta.get("requested_date"),
            "effective_date": meta.get("date"),
            "window": [meta.get("window_start"), meta.get("window_end")],
            "events_total": meta.get("events_total"),
            "events_aligned": meta.get("events_aligned"),
            "clusters": meta.get("clusters"),
            "evaluated": meta.get("evaluated"),
            "rows": len(payload.get("rows") or []),
            "benchmark": meta.get("benchmark"),
            "outputs": meta.get("outputs"),
            "top": [
                {
                    "rank": row.get("rank"),
                    "code": row.get("code"),
                    "name": row.get("name"),
                    "d0": row.get("d0"),
                    "category": row.get("category"),
                    "car_d0_3": (row.get("car") or {}).get("d0_3"),
                    "industry": row.get("industry_name"),
                    "divergence": row.get("divergence"),
                }
                for row in (payload.get("rows") or [])[:5]
            ],
        }
        _print(summary)
        return 0

    if args.command == "build-db":
        payload = build_db(
            end=args.end,
            days=args.days,
            limit=args.limit,
            paths=paths,
            db_path=db_path,
            provider=provider,
        )
        _print(payload)
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
