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


def pool_cn_codes(timeout: int = 15) -> set[str] | None:
    """池内 CN 标的代码集合；不可用/退化时返回 None（不返回空集，避免"0 只"歧义）。"""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(POOL_API, timeout=timeout) as r:
            doc = json.loads(r.read().decode("utf-8"))
        items = [x for x in (doc.get("items") or [])
                 if str(x.get("market") or "").upper() == "CN"]
        return {str(x.get("code")) for x in items} if items else None
    except Exception:  # noqa: BLE001
        return None


def pool_cn_count(timeout: int = 15) -> int | None:
    codes = pool_cn_codes(timeout)
    return None if codes is None else len(codes)


def reconcile(pool_date: str, expect: int | None = None, pool_codes: set[str] | None = None) -> dict:
    """逐项对账。

    **基准取「已裁决标的去重数」而不是「当前池的 CN 数」**——池是动态的（实测同日
    12→17 又回落到 12），拿它当基准会让"刚刚多处理了几只"变成假 FAIL。
    池侧只用做**覆盖率**检查（池内 CN 是否都已裁决）。
    """
    tag = pool_date.replace("-", "")
    dec = [x for x in _rows(DATA / "d1_decisions.jsonl")
           if x.get("source") == SOURCE and pool_date in str(x.get("evidence") or "")]
    ho = [x for x in _rows(DATA / "d1_handoffs.jsonl")
          if ("ACAD1 官方池 %s" % pool_date) in str(x.get("rationale") or "")]
    wp = [x for x in _rows(DATA / "watch_plans.jsonl")
          if str(x.get("plan_id") or "").startswith("wp-acad1-%s-" % tag)]

    uniq = {str(x.get("code")) for x in dec}
    base = len(uniq)
    checks = [
        {"item": "decisions 条数 = 去重标的数（无重复写入）", "actual": len(dec),
         "expect": base, "ok": len(dec) == base},
        {"item": "handoffs 条数 = 标的数", "actual": len(ho), "expect": base, "ok": len(ho) == base},
        {"item": "watch_plans = 标的数 × 2", "actual": len(wp), "expect": base * 2,
         "ok": len(wp) == base * 2},
        {"item": "stop / take 拆分", "actual": "%d / %d" % (
            sum(1 for x in wp if str(x.get("plan_id", "")).endswith("-stop")),
            sum(1 for x in wp if str(x.get("plan_id", "")).endswith("-take"))),
         "expect": "%d / %d" % (base, base),
         "ok": (sum(1 for x in wp if str(x.get("plan_id", "")).endswith("-stop")) == base
                and sum(1 for x in wp if str(x.get("plan_id", "")).endswith("-take")) == base)},
        {"item": "代码集合一致（裁决 = 交棒）",
         "actual": "一致" if uniq == {str(x.get("code")) for x in ho} else "不一致",
         "expect": "一致", "ok": uniq == {str(x.get("code")) for x in ho}},
        {"item": "全部 paper_only=true",
         "actual": all(x.get("paper_only") for x in dec + ho + wp),
         "expect": True, "ok": all(x.get("paper_only") for x in dec + ho + wp)},
    ]
    if expect is not None:
        checks.append({"item": "处理量与指定基准一致", "actual": base, "expect": expect,
                       "ok": base == expect})
    if pool_codes is not None:
        missing = sorted(pool_codes - uniq)
        checks.append({"item": "覆盖率：池内 CN 均已裁决", "actual": ("全覆盖" if not missing else "缺%d只" % len(missing)),
                       "expect": "全覆盖", "ok": not missing,
                       "detail": missing[:8]})
    return {"schema": "acad1_bridge_reconcile/v2", "pool_date": pool_date,
            "base": base, "expect_arg": expect,
            "pool_codes_n": (len(pool_codes) if pool_codes is not None else None),
            "checks": checks, "ok": all(c["ok"] for c in checks)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ACAD1→D1 桥逐项对账")
    ap.add_argument("--pool-date", required=True)
    ap.add_argument("--expect", type=int, default=None,
                    help="期望处理条数；缺省取池 API 的 CN 数，取不到则用已写裁决数")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--check", action="store_true", help="有不符项则退出码 1")
    ap.add_argument("--check-pool", action="store_true",
                    help="额外核对覆盖率（池内 CN 是否都已裁决）；池不可用则跳过该项")
    args = ap.parse_args(argv)

    expect = args.expect
    pool_codes = None
    if args.check_pool or expect is None:
        codes = pool_cn_codes()
        if codes:
            pool_codes = codes
        elif expect is None:
            print("EXPECT_UNKNOWN 池 API 不可用或 market 字段缺失（CN=0）"
                  "→ 无法核对覆盖率；请用 --check-pool 之外的方式指定基准，或先修复池数据源。")
            return 2
    res = reconcile(args.pool_date, expect, pool_codes)
    res["expect_source"] = "arg" if expect is not None else "ledger"
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    elif args.markdown:
        print("| 产出 | 实测 | 期望 | 结果 |")
        print("|---|---|---|---|")
        for c in res["checks"]:
            print("| %s | **%s** | %s | %s |" % (c["item"], c["actual"], c["expect"],
                                                "OK" if c["ok"] else "**MISMATCH**"))
    else:
        print("pool_date=%s base(去重标的)=%s pool_cn=%s"
              % (res["pool_date"], res["base"], res["pool_codes_n"]))
        for c in res["checks"]:
            extra = (" detail=%s" % c["detail"]) if c.get("detail") else ""
            print("  [%s] %-42s actual=%s expect=%s%s"
                  % ("OK" if c["ok"] else "FAIL", c["item"], c["actual"], c["expect"], extra))
        print("RECONCILE %s" % ("PASS" if res["ok"] else "FAIL"))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
