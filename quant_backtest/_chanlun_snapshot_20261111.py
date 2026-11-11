# -*- coding: utf-8 -*-
"""只读：还原缠论池在 2026-11-10 / 11-11 两次快照的买点清单。"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

POOL = Path(__file__).resolve().parent / "data" / "chanlun_pool.jsonl"


def main() -> None:
    if not POOL.exists():
        print("池文件不存在:", POOL)
        return
    rows = []
    with POOL.open(encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except Exception:
                continue
    print("总行数", len(rows))
    print("字段:", list(rows[-1].keys()) if rows else "-")

    det = Counter(str(r.get("detect_date") or "")[:10] for r in rows)
    print("\n=== detect_date 分布（前8） ===")
    for d, n in sorted(det.items(), reverse=True)[:8]:
        print(f"  {d}  {n}")

    # 取最新两个快照
    snaps = sorted(det, reverse=True)[:2]
    for snap in snaps:
        batch = [r for r in rows if str(r.get("detect_date") or "")[:10] == snap]
        print(f"\n=== 快照 {snap}: {len(batch)} 条 ===")
        typ = Counter(str(r.get("buy_type") or r.get("type") or "?") for r in batch)
        print("  买点类型:", dict(typ.most_common()))
        bd = Counter(str(r.get("dt") or "")[:10] for r in batch)
        print("  买点日分布（前10）:")
        for d, n in sorted(bd.items(), reverse=True)[:10]:
            print(f"    {d}  {n}")
        side = Counter(str(r.get("side") or r.get("signal") or "?") for r in batch)
        if len(side) <= 6:
            print("  方向/信号:", dict(side.most_common()))

    # 明细：最新快照按 dt 降序全部列出
    if snaps:
        snap = snaps[0]
        batch = [r for r in rows if str(r.get("detect_date") or "")[:10] == snap]
        batch.sort(key=lambda r: (str(r.get("dt") or "")[:10], str(r.get("code") or "")), reverse=True)
        print(f"\n=== 快照 {snap} 全明细（按买点日降序，共 {len(batch)} 条） ===")
        print(f"{'代码':<12}{'名称':<10}{'买点日':<12}{'类型':<8}{'低点':>9}{'现价':>9}")
        for r in batch:
            code = str(r.get("code") or "")
            name = str(r.get("name") or "")[:8]
            dt = str(r.get("dt") or "")[:10]
            bt = str(r.get("buy_type") or r.get("type") or "?")
            px = r.get("price")
            last = r.get("last") or r.get("close") or r.get("last_price")
            def f(x):
                try:
                    return f"{float(x):.2f}"
                except Exception:
                    return "-"
            print(f"{code:<12}{name:<10}{dt:<12}{bt:<8}{f(px):>9}{f(last):>9}")

        # 分组：买点日在近 30 个自然日内的
        cut = "2026-10-12"
        recent = [r for r in batch if str(r.get("dt") or "")[:10] >= cut]
        print(f"\n=== 买点日 >= {cut}: {len(recent)} 只 ===")
        for r in recent:
            print(f"  {str(r.get('code') or ''):<12}{str(r.get('name') or '')[:10]:<10}{str(r.get('dt') or '')[:10]:<12}{r.get('buy_type') or r.get('type') or '?'}")


if __name__ == "__main__":
    main()
