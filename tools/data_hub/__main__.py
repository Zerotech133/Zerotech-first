# -*- coding: utf-8 -*-
"""data_hub 命令行。

    python -m tools.data_hub sources [--domain quote] [--status verified]
    python -m tools.data_hub probe            # 静态体检，不出网
    python -m tools.data_hub probe --live     # 真探活，出报告
    python -m tools.data_hub govern [--report]
    python -m tools.data_hub check            # 登记表校验，非零退出即不合格

退出码：0 正常；1 校验/治理发现阻断级问题；2 参数或加载错误。
"""

from __future__ import annotations

import argparse
import json
import sys

from . import collect, govern, probe, registry


def cmd_sources(args) -> int:
    data = registry.load()
    rows = data.get("sources", [])
    if args.domain:
        rows = [r for r in rows if r.get("domain") == args.domain]
    if args.status:
        rows = [r for r in rows if r.get("status") == args.status]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    head = f"{'id':20s} {'域':12s} {'状态':18s} {'出口':7s} {'频率':6s} 名称"
    print(head)
    print("-" * len(head))
    for r in rows:
        print(
            f"{r.get('id',''):20s} {r.get('domain',''):12s} {r.get('status',''):18s} "
            f"{r.get('proxy_policy',''):7s} {r.get('freq',''):6s} {r.get('name','')}"
        )
    s = registry.summary({"sources": rows})
    print(f"\n合计 {s['total']}  ·  按域 {s['by_domain']}  ·  按状态 {s['by_status']}")
    return 0


def cmd_check(args) -> int:
    data = registry.load()
    problems = registry.validate(data)
    if problems:
        for p in problems:
            print("FAIL", p)
        return 1
    print(f"OK 源登记表合格：{len(data.get('sources', []))} 条")
    return 0


def cmd_probe(args) -> int:
    data = registry.load()
    only_list = [x for x in (args.only or "").split(",") if x.strip()]
    static = probe.static_checks(data, only=only_list or None)
    bad = [s for s in static if not s["ok"]]
    print(f"静态体检：{len(static)} 条记录，问题 {len(bad)} 条")
    for item in bad[:20]:
        print("  -", item.get("source") or item.get("kind"), item["detail"])
    if not args.live:
        print("（未加 --live，未出网）")
        return 1 if bad else 0
    rows = probe.run_live(data, only=only_list or None, workers=args.workers, slow_ms=args.slow_ms)
    jsonl, md = probe.write_report(rows, static)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print("探活完成：", counts)
    print("明细：", jsonl)
    print("报告：", md)
    return 0


def cmd_govern(args) -> int:
    rep = govern.audit()
    s = rep["scale"]
    print(
        f"源 {s['sources']} · 目录表 {s['catalog_tables']} · 物理表 {s['physical_tables']} "
        f"· 无日期表 {s['undated_tables']}"
    )
    print(f"有表无源：{len(rep['orphan_tables'])} 个渠道 / "
          f"{sum(v['count'] for v in rep['orphan_tables'].values())} 张表")
    for ch, info in rep["orphan_tables"].items():
        print(f"  - {ch}: {info['count']} 张，样例 {info['sample'][:4]}")
    print(f"有源无表：{len(rep['matched_none'])} 条")
    for item in rep["matched_none"]:
        print(f"  - {item['source']} {item['prefixes']}")
    print(f"时效欠账：{len(rep['stale'])} 张")
    grades: dict[str, list[dict]] = {}
    for row in rep["stale"]:
        grades.setdefault(row.get("grade", "lagging"), []).append(row)
    for grade, label in (("stopped", "A 停产"), ("broken", "B 断更"), ("lagging", "C 滞后")):
        rows = grades.get(grade) or []
        print(f"  [{label}] {len(rows)} 张")
        for row in rows[:5]:
            print(f"    - {row['table']} 滞后 {row['lag_days']}d（{row['freshness']}）")
    print(f"生命周期已跳过：{len(rep.get('skipped_by_lifecycle') or [])} 张")
    profile = rep.get("batch_profile") or []
    if profile:
        print(f"批次画像（同源同日期 ≥3 张）：{len(profile)} 个未被最近批次覆盖的表群")
        for row in profile[:6]:
            print(f"    - {row['source']} 止于 {row['freshness']}：{row['count']} 张，如 {', '.join(row['sample'][:3])}")
    print(f"未登记物理表：{len(rep['unregistered'])} 张")
    if rep["unregistered"]:
        print("  样例：", ", ".join(rep["unregistered"][:10]))
    print(f"未声明表的源：{len(rep['declared_empty'])} 个：{', '.join(rep['declared_empty'])}")
    if args.report:
        js, md = govern.write_report(rep)
        print("报告：", md)
        print("明细：", js)
    return 0


def cmd_collect(args) -> int:
    extra = {}
    if args.adapter == "eastmoney":
        extra["limit"] = args.limit
    res = collect.collect(
        args.adapter,
        args.dataset,
        since=args.since,
        dry_run=args.dry_run,
        **extra,
    )
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if res.get("status") in {"written", "dry_run_ok", "no_new"} else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="data_hub", description="全网数据源接入与治理")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("sources", help="列出登记源")
    p.add_argument("--domain")
    p.add_argument("--status")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_sources)

    p = sub.add_parser("check", help="校验登记表")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("probe", help="探活")
    p.add_argument("--live", action="store_true", help="真出网")
    p.add_argument("--only", help="逗号分隔的源 id")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--slow-ms", type=int, default=3000, dest="slow_ms")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("govern", help="三方对账")
    p.add_argument("--report", action="store_true", help="落报告文件")
    p.set_defaults(func=cmd_govern)

    p = sub.add_parser("collect", help="取一个数据集并落地（不写主仓）")
    p.add_argument("--adapter", required=True, choices=sorted(collect.ADAPTERS))
    p.add_argument("--dataset", required=True, help="stooq: aapl.us / eastmoney: 1.600000 / sina: sh600000")
    p.add_argument("--since", help="起始日期 YYYY-MM-DD，缺省用上次水位")
    p.add_argument("--limit", type=int, default=5000, help="eastmoney 取多少根 K 线")
    p.add_argument("--dry-run", action="store_true", dest="dry_run", help="只取数校验，不落盘")
    p.set_defaults(func=cmd_collect)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, FileNotFoundError) as exc:
        print("ERROR", exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
