# -*- coding: utf-8 -*-
"""按池对齐：把「已裁但已不在 ACAD1 池内」的盯盘计划置为 cancelled（2026-11-16 WO-POOL-ALIGN）。

## 为什么需要

桥是**增量**的（`already_judged` 去重），账本只增不减。但池会缩容：实测 2026-11-16
当日池曾到 18 只、后回落到 12 只，于是 **6 只标的的 11 条 armed 计划仍在盯着池外标的**。
上游已撤，下游还在守——这是需要显式对齐的缺口，桥本身不该顺手删记录。

## 口径

- **不删记录**：只把 `status` 从 `armed` 改成 `cancelled`，写 `cancelled_at` / `cancelled_reason`；
- **不动裁决/交棒**：历史 `d1_decisions` / `d1_handoffs` 是发生过的事实，保持不动；
- **默认 dry-run**：不加 `--apply` 只打印将影响哪些；
- 池不可用（退化/为空）时**直接拒绝**，绝不在看不到池的情况下批量撤销。

    python -X utf8 tools/acad1_pool_align.py --pool-date 2026-11-16              # 只读
    python -X utf8 tools/acad1_pool_align.py --pool-date 2026-11-16 --apply      # 真正置 cancelled
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "agent_platform" / "data"
POOL_API = "http://127.0.0.1:8091/api/acad1/pool"
SOURCE = "acad1_pool_bridge"


def _rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def pool_cn(day: str, timeout: int = 20) -> tuple[set[str], str]:
    """返回 (池内 CN 代码集合, 说明)。空集或异常时说明里带原因。"""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(POOL_API, timeout=timeout) as r:
            doc = json.loads(r.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return set(), "pool_unreachable:%s" % type(exc).__name__
    mode = str(doc.get("source_mode") or "")
    if mode == "shadow":
        return set(), "pool_degraded:shadow"
    if str(doc.get("pool_date") or "") != day:
        return set(), "pool_date_mismatch:%s!=%s" % (doc.get("pool_date"), day)
    items = [x for x in (doc.get("items") or [])
             if str(x.get("market") or "").upper() == "CN"]
    if not items:
        return set(), "pool_empty_cn"
    return {str(x.get("code")) for x in items}, "ok"


def plan_code(plan_id: str) -> str:
    """wp-acad1-20261116-600679-stop → 600679（不带后缀，匹配时忽略交易所后缀）。"""
    parts = str(plan_id).split("-")
    return parts[3] if len(parts) > 3 else ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="按池对齐：撤销池外标的的盯盘计划")
    ap.add_argument("--pool-date", required=True)
    ap.add_argument("--apply", action="store_true", help="真正写回（默认 dry-run）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    codes, why = pool_cn(args.pool_date)
    if not codes:
        print("REFUSED 池不可用（%s）→ 不在看不到池的情况下批量撤销" % why)
        return 2

    decided = {str(x.get("code")) for x in _rows(DATA / "d1_decisions.jsonl")
               if x.get("source") == SOURCE and args.pool_date in str(x.get("evidence") or "")}
    stale_codes = decided - codes
    stale_bare = {c.split(".")[0] for c in stale_codes}

    plans = _rows(DATA / "watch_plans.jsonl")
    tag = "wp-acad1-%s-" % args.pool_date.replace("-", "")
    targets = [p for p in plans
               if str(p.get("plan_id") or "").startswith(tag)
               and p.get("status") == "armed"
               and plan_code(p.get("plan_id")) in stale_bare]

    print("pool_date=%s 池内 CN=%d 已裁=%d 池外已裁=%d（%s）"
          % (args.pool_date, len(codes), len(decided), len(stale_codes),
             ",".join(sorted(stale_codes)) or "无"))
    print("待撤销 armed 计划=%d%s" % (len(targets), "（dry-run）" if not args.apply else ""))
    for p in targets:
        print("  - %s status=%s" % (p.get("plan_id"), p.get("status")))

    if not targets:
        print("ALIGNED 无需动作")
        return 0
    if not args.apply:
        print("DRY_RUN 加 --apply 才会写回")
        return 0

    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    reason = "池外标的：ACAD1 %s 池已不含该标的（池内 %d 只，已裁 %d 只）" % (
        args.pool_date, len(codes), len(decided))
    ids = {id(p) for p in targets}
    for p in plans:
        if id(p) in ids:
            p["status"] = "cancelled"
            p["cancelled_at"] = now
            p["cancelled_reason"] = reason
    backup = DATA / ("watch_plans.jsonl.bak_%s"
                     % dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
    backup.write_bytes((DATA / "watch_plans.jsonl").read_bytes())
    with (DATA / "watch_plans.jsonl").open("w", encoding="utf-8") as fh:
        for p in plans:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
    print("APPLIED 撤销 %d 条；备份 %s" % (len(targets), backup.name))
    if args.json:
        print(json.dumps({"schema": "pool_align/v1", "cancelled": len(targets),
                          "codes": sorted(stale_codes), "backup": str(backup)},
                         ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
