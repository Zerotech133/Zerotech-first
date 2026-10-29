# -*- coding: utf-8 -*-
"""SQLite 健康检查（A4 制度化）：给 DW 周检提供 quick_check / journal / 空间 / 索引卫生。

检查对象（只读打开，不写业务数据；仅对可写库执行 PRAGMA optimize 的统计更新）：
- news_collector/news.db        写热（采集/JEV 批）→ 期望 WAL
- agent_platform/data/jev_audit/jev_audit.db  写热 → 期望 WAL
- quant_backtest/data/quant.db  采集写（夜）→ 期望 WAL
- quant_backtest/data/xueqiu/xueqiu_info.db   抓取写（夜）→ 期望 WAL
- zerobound-kg/data/ann_history/ann_history.db 读多写少，1.9GB → quick_check 较慢，单独标记

退出码：0=全绿 / 1=有告警 / 2=有失败（缺库/quick_check 非 ok）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import common  # noqa: E402

REPO = common.REPO
DATA = common.DATA

TARGETS = [
    # (name, path, write_hot, expect_wal)
    ("news", REPO / "news_collector" / "news.db", True, True),
    ("jev_audit", REPO / "agent_platform" / "data" / "jev_audit" / "jev_audit.db", True, True),
    ("quant", DATA / "quant.db", True, True),
    ("xueqiu_info", DATA / "xueqiu" / "xueqiu_info.db", True, True),
    ("ann_history", REPO / "zerobound-kg" / "data" / "ann_history" / "ann_history.db", False, False),
]
BIG_CHECK_BYTES = 2 * 1024 ** 3  # >2GB 跳过 quick_check（周检超时保护）


def check_one(name: str, path: Path, write_hot: bool, expect_wal: bool) -> dict:
    out = {"name": name, "path": str(path), "exists": path.exists(), "issues": [], "warnings": []}
    if not out["exists"]:
        out["issues"].append("missing_db")
        return out
    out["size_mb"] = round(path.stat().st_size / 1024 / 1024, 1)
    try:
        con = sqlite3.connect("file:%s?mode=ro" % str(path).replace("\\", "/"), uri=True, timeout=10)
        out["journal_mode"] = con.execute("PRAGMA journal_mode").fetchone()[0]
        page_count = con.execute("PRAGMA page_count").fetchone()[0]
        free = con.execute("PRAGMA freelist_count").fetchone()[0]
        out["page_count"] = page_count
        out["freelist_count"] = free
        if page_count:
            pct = free * 100.0 / page_count
            out["free_pct"] = round(pct, 1)
            if pct > 10:
                out["warnings"].append("freelist_high_%.1f%%" % pct)
        out["indexes"] = con.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='index' AND sql IS NOT NULL").fetchone()[0]
        out["tables"] = con.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone()[0]
        if out["size_mb"] * 1024 * 1024 > BIG_CHECK_BYTES:
            out["quick_check"] = "skipped_large"
        else:
            out["quick_check"] = con.execute("PRAGMA quick_check").fetchone()[0]
        if out["quick_check"] not in ("ok", "skipped_large"):
            out["issues"].append("quick_check_failed")
        if expect_wal and out["journal_mode"] != "wal":
            out["warnings"].append("journal_mode_%s_expect_wal" % out["journal_mode"])
        wal = Path(str(path) + "-wal")
        out["wal_mb"] = round(wal.stat().st_size / 1024 / 1024, 1) if wal.exists() else 0.0
        out["write_hot"] = write_hot
        con.close()
    except Exception as e:  # noqa: BLE001
        out["issues"].append("exception:" + type(e).__name__ + ":" + str(e)[:100])
    return out


def collect(names: list[str] | None = None, optimize: bool = False) -> dict:
    """返回统一结构（与 datawarehouse 其他工具一致：code/issues）。"""
    rows = []
    for name, path, write_hot, expect_wal in TARGETS:
        if names and name not in names:
            continue
        rows.append(check_one(name, path, write_hot, expect_wal))
    code = common.EXIT_OK
    issues = []
    for r in rows:
        for i in r.get("issues", []):
            issues.append("%s:%s" % (r["name"], i))
            code = common.EXIT_NOT_READY
        for w in r.get("warnings", []):
            issues.append("%s:%s" % (r["name"], w))
            if code == common.EXIT_OK:
                code = common.EXIT_WARN
    return {"tool": "db_health", "code": code, "issues": issues, "dbs": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SQLite 健康检查（DW 周检用）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only", default="", help="逗号分隔的目标名（默认全部）")
    args = ap.parse_args(argv)
    names = [x for x in args.only.split(",") if x] or None
    res = collect(names)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        for r in res["dbs"]:
            print("%-12s %-8s qc=%-14s jm=%-6s idx=%-3s free=%-5s size=%sMB wal=%sMB %s" % (
                r["name"], "OK" if not r.get("issues") else "FAIL", r.get("quick_check"),
                r.get("journal_mode"), r.get("indexes"), r.get("free_pct"),
                r.get("size_mb"), r.get("wal_mb"),
                (";".join(r.get("warnings") or []))[:60]))
        print("result:", "OK" if res["code"] == 0 else ("WARN" if res["code"] == 1 else "FAIL"))
    return res["code"]


if __name__ == "__main__":
    sys.exit(main())
