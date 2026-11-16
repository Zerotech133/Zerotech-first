# -*- coding: utf-8 -*-
"""ACAD1→D1 桥的逐项对账（2026-11-16 WO-ACAD1-D1-RECONCILE）。

「处理条数 × 每条产出」= 账本增量。**不用总量差值**（无法排除并发写入与分布不均）。

    python -X utf8 tools/acad1_bridge_reconcile.py --pool-date 2026-11-16
    python -X utf8 tools/acad1_bridge_reconcile.py --pool-date 2026-11-16 --check   # 不符则 rc=1
    python -X utf8 tools/acad1_bridge_reconcile.py --pool-date 2026-11-16 --markdown # 直接贴文档
"""
from __future__ import annotations

import argparse
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


def pool_cn_count(timeout: int = 15) -> int | None:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(POOL_API, timeout=timeout) as r:
            doc = json.loads(r.read().decode("utf-8"))
        return sum(1 for x in (doc.get("items") or [])
                   if str(x.get("market") or "").upper() == "CN")
    except Exception:  # noqa: BLE001
        return None


def reconcile(pool_date: str, expect: int | None) -> dict:
    tag = pool_date.replace("-", "")
    dec = [x for x in _rows(DATA / "d1_decisions.jsonl")
           if x.get("source") == SOURCE and pool_date in str(x.get("evidence") or "")]
    ho = [x for x in _rows(DATA / "d1_handoffs.jsonl")
          if ("ACAD1 官方池 %s" % pool_date) in str(x.get("rationale") or "")]
    wp = [x for x in _rows(DATA / "watch_plans.jsonl")
          if str(x.get("plan_id") or "").startswith("wp-acad1-%s-" % tag)]

    base = expect if expect is not None else len(dec)
    checks = [
        {"item": "decisions（source=%s）" % SOURCE, "actual": len(dec), "expect": base,
         "ok": len(dec) == base},
        {"item": "handoffs（rationale 含 ACAD1 官方池 %s）" % pool_date, "actual": len(ho),
         "expect": base, "ok": len(ho) == base},
        {"item": "watch_plans（wp-acad1-%s-*）" % tag, "actual": len(wp),
         "expect": base * 2, "ok": len(wp) == base * 2},
        {"item": "stop / take 拆分", "actual": "%d / %d" % (
            sum(1 for x in wp if str(x.get("plan_id", "")).endswith("-stop")),
            sum(1 for x in wp if str(x.get("plan_id", "")).endswith("-take"))),
         "expect": "%d / %d" % (base, base),
         "ok": (sum(1 for x in wp if str(x.get("plan_id", "")).endswith("-stop")) == base
                and sum(1 for x in wp if str(x.get("plan_id", "")).endswith("-take")) == base)},
        {"item": "代码集合一致（裁决 = 交棒）",
         "actual": "一致" if {x["code"] for x in dec} == {x["code"] for x in ho} else "不一致",
         "expect": "一致", "ok": {x["code"] for x in dec} == {x["code"] for x in ho}},
        {"item": "全部 paper_only=true",
         "actual": all(x.get("paper_only") for x in dec + ho + wp),
         "expect": True, "ok": all(x.get("paper_only") for x in dec + ho + wp)},
        {"item": "重复写入（同代码多条裁决）",
         "actual": len(dec) - len({x["code"] for x in dec}), "expect": 0,
         "ok": len(dec) - len({x["code"] for x in dec}) == 0},
    ]
    return {"schema": "acad1_bridge_reconcile/v1", "pool_date": pool_date,
            "expect_base": base, "checks": checks,
            "ok": all(c["ok"] for c in checks)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ACAD1→D1 桥逐项对账")
    ap.add_argument("--pool-date", required=True)
    ap.add_argument("--expect", type=int, default=None,
                    help="期望处理条数；缺省取池 API 的 CN 数，取不到则用已写裁决数")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--check", action="store_true", help="有不符项则退出码 1")
    args = ap.parse_args(argv)

    expect = args.expect
    expect_src = "arg"
    if expect is None:
        n = pool_cn_count()
        if n:
            expect, expect_src = n, "pool_api"
        else:
            # 池退化（source_mode=shadow / market 全 None）时 CN 数为 0——
            # 这属于「期望无法确定」，不是「对账不符」，两者必须分开报。
            print("EXPECT_UNKNOWN 池 API 不可用或 market 字段缺失（CN=0）"
                  "→ 无法确定处理量；请用 --expect N 指定，或先修复池数据源。")
            return 2
    res = reconcile(args.pool_date, expect)
    res["expect_source"] = expect_src
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    elif args.markdown:
        print("| 产出 | 实测 | 期望 | 结果 |")
        print("|---|---|---|---|")
        for c in res["checks"]:
            print("| %s | **%s** | %s | %s |" % (c["item"], c["actual"], c["expect"],
                                                "OK" if c["ok"] else "**MISMATCH**"))
    else:
        print("pool_date=%s expect_base=%s" % (res["pool_date"], res["expect_base"]))
        for c in res["checks"]:
            print("  [%s] %-52s actual=%s expect=%s"
                  % ("OK" if c["ok"] else "FAIL", c["item"], c["actual"], c["expect"]))
        print("RECONCILE %s" % ("PASS" if res["ok"] else "FAIL"))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
