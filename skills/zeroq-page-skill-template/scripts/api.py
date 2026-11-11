"""自带页面型技能骨架：数据桥脚本。

server 把请求上下文以 JSON 写 stdin，本脚本把结果 JSON 写 stdout。
约束：stdlib only、只读 sqlite、30 秒内退出、路径固定来自 frontmatter api。
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

SAFE = re.compile(r"[A-Za-z0-9_-]+")
DEFAULT_COMPANY = "local-selftest"
DEFAULT_SKILL = "zeroq-page-skill-template"
ITEMS_SQL = "SELECT code, label, value, updated_at FROM items ORDER BY code"
ITEMS_FILTER_SQL = "SELECT code, label, value, updated_at FROM items WHERE label = ? ORDER BY code"


def safe_part(value, fallback):
    if value and SAFE.fullmatch(value):
        return value
    return fallback


def db_path(company_id, skill_id):
    root = Path.home() / ".zerobound" / "instances" / "default" / "skill-data"
    return root / company_id / skill_id / "demo.db"


def envelope(status, body):
    return {"statusCode": status, "body": body}


def handle(req):
    path = str(req.get("path") or "")
    company = safe_part(str(req.get("companyId") or ""), DEFAULT_COMPANY)
    skill = safe_part(str(req.get("skillId") or ""), DEFAULT_SKILL)
    db = db_path(company, skill)

    if not db.exists():
        return {
            "empty": True,
            "reason": "db_missing",
            "hint": "Agent 运行 ingest 后可查看",
            "path": path,
        }

    con = sqlite3.connect("file:" + str(db) + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        if path == "summary":
            count = con.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
            last = con.execute("SELECT MAX(updated_at) AS last FROM items").fetchone()["last"]
            return {"empty": count == 0, "path": path, "count": count, "updatedAt": last}

        if path == "items":
            label = str((req.get("query") or {}).get("label") or "")
            if label:
                rows = con.execute(ITEMS_FILTER_SQL, (label,)).fetchall()
            else:
                rows = con.execute(ITEMS_SQL).fetchall()
            items = [
                {
                    "code": row["code"],
                    "label": row["label"],
                    "value": row["value"],
                    "updatedAt": row["updated_at"],
                }
                for row in rows
            ]
            return {"empty": len(items) == 0, "path": path, "items": items}

        if path == "health":
            return {"ok": True, "path": path}

        return envelope(404, {"error": "unknown_endpoint", "path": path})
    finally:
        con.close()


def main():
    try:
        raw = sys.stdin.read()
        req = json.loads(raw) if raw.strip() else {}
        out = handle(req)
    except Exception as exc:
        out = envelope(500, {"error": type(exc).__name__})
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
