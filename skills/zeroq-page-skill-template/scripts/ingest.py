"""自带页面型技能骨架：采集/建库脚本。

由 Agent 在 run 中执行，或人工本地自测。stdlib only、不联网、不写包内目录。
写入规范约定的公司隔离数据目录，与 api.py 的派生规则保持一致。
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

SAFE = re.compile(r"[A-Za-z0-9_-]+")
DEFAULT_COMPANY = "local-selftest"
DEFAULT_SKILL = "zeroq-page-skill-template"
TABLE_DDL = "CREATE TABLE IF NOT EXISTS items (code TEXT PRIMARY KEY, label TEXT, value REAL, updated_at TEXT)"
INSERT_SQL = "INSERT OR REPLACE INTO items (code, label, value, updated_at) VALUES (?, ?, ?, ?)"


def safe_part(value, fallback):
    """防路径逃逸：companyId / skillId 只允许安全字符，否则回落到本地自测缺省值。"""
    if value and SAFE.fullmatch(value):
        return value
    return fallback


def db_path(company_id, skill_id):
    root = Path.home() / ".zerobound" / "instances" / "default" / "skill-data"
    return root / company_id / skill_id / "demo.db"


def build_rows(source):
    """取数函数：换真实数据源只改这里，写库 schema 不动。演示数据不含任何手写日期或编号。"""
    if source != "demo":
        raise ValueError("unsupported source: " + source)
    labels = ["alpha", "beta", "gamma", "delta"]
    stamp = datetime.now().isoformat(timespec="seconds")
    return [(label.upper(), label, float(len(label)), stamp) for label in labels]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="demo")
    args = parser.parse_args()

    company = safe_part(os.environ.get("ZB_COMPANY_ID"), DEFAULT_COMPANY)
    skill = safe_part(os.environ.get("ZB_SKILL_ID"), DEFAULT_SKILL)
    path = db_path(company, skill)
    path.parent.mkdir(parents=True, exist_ok=True)

    rows = build_rows(args.source)
    con = sqlite3.connect(str(path))
    try:
        con.execute(TABLE_DDL)
        con.executemany(INSERT_SQL, rows)
        con.commit()
    finally:
        con.close()

    out = {
        "ok": True,
        "source": args.source,
        "companyId": company,
        "skillId": skill,
        "db": str(path),
        "rows": len(rows),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
