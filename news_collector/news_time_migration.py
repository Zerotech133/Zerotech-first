# -*- coding: utf-8 -*-
"""新闻旧时间迁移：prepare 只读，apply 备份后原子修改，rollback 逐行回退。"""
import argparse
import json
import os
import re
import sqlite3
from pathlib import Path

from news_time import parse_news_time


def _readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=20)


def prepare(path):
    plan = {"version": 1, "database": str(Path(path).resolve()), "changes": [], "unresolved": []}
    db = _readonly(path)
    try:
        for ident, source, ts, created_at in db.execute("SELECT id,source,ts,created_at FROM news"):
            if not re.fullmatch(r"\d{2}-\d{2} \d{2}:\d{2}", str(ts or "")):
                continue
            row = {"id": ident, "source": source, "old_ts": ts, "created_at": created_at}
            event = parse_news_time(ts, reference=created_at, source=source)
            if event is None:
                plan["unresolved"].append(row)
            else:
                row["new_ts"] = event.strftime("%Y-%m-%d %H:%M")
                plan["changes"].append(row)
    finally:
        db.close()
    return plan


def _validate_plan(plan):
    if plan.get("version") != 1 or not isinstance(plan.get("changes"), list):
        raise ValueError("迁移计划格式无效")
    ids = set()
    for row in plan["changes"]:
        if row["id"] in ids:
            raise ValueError("迁移计划存在重复新闻键")
        ids.add(row["id"])
        event = parse_news_time(row["old_ts"], row["created_at"], row["source"])
        if event is None or event.strftime("%Y-%m-%d %H:%M") != row["new_ts"]:
            raise ValueError("迁移计划时间与原始记录不一致")


def _pending(db, plan, reverse=False):
    pending = []
    for row in plan["changes"]:
        current = db.execute("SELECT source,ts,created_at FROM news WHERE id=?", (row["id"],)).fetchone()
        old, new = (row["new_ts"], row["old_ts"]) if reverse else (row["old_ts"], row["new_ts"])
        if current == (row["source"], new, row["created_at"]):
            continue
        if current != (row["source"], old, row["created_at"]):
            raise ValueError("目标新闻已变化，拒绝写入：%s" % row["id"])
        pending.append((new, row["id"], old, row["source"], row["created_at"]))
    return pending


def apply(plan, backup_path):
    _validate_plan(plan)
    path = Path(plan["database"]).resolve()
    backup = Path(backup_path).resolve()
    if path == backup or backup.exists():
        raise ValueError("备份路径必须是新的独立文件")
    db = sqlite3.connect(path, timeout=20)
    try:
        db.execute("BEGIN IMMEDIATE")
        pending = _pending(db, plan)
        if not pending:
            db.rollback()
            return 0
        # 写保留锁阻止其他采集者修改；另一个只读连接生成同一时点完整备份。
        source = _readonly(path)
        saved = sqlite3.connect(backup)
        try:
            source.backup(saved)
            if saved.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("迁移备份完整性检查失败")
        finally:
            source.close()
            saved.close()
        db.executemany("UPDATE news SET ts=? WHERE id=? AND ts=? AND source IS ? AND created_at IS ?", pending)
        db.commit()
        return len(pending)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def rollback(plan):
    _validate_plan(plan)
    db = sqlite3.connect(plan["database"], timeout=20)
    try:
        db.execute("BEGIN IMMEDIATE")
        pending = _pending(db, plan, reverse=True)
        db.executemany("UPDATE news SET ts=? WHERE id=? AND ts=? AND source IS ? AND created_at IS ?", pending)
        db.commit()
        return len(pending)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--database", required=True)
    prep.add_argument("--plan", required=True)
    change = commands.add_parser("apply")
    change.add_argument("--plan", required=True)
    change.add_argument("--backup", required=True)
    undo = commands.add_parser("rollback")
    undo.add_argument("--plan", required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        plan = prepare(args.database)
        with open(args.plan, "x", encoding="utf-8") as target:
            json.dump(plan, target, ensure_ascii=False, indent=2)
        print(json.dumps({"changes": len(plan["changes"]), "unresolved": len(plan["unresolved"])}))
    else:
        with open(args.plan, encoding="utf-8") as source:
            plan = json.load(source)
        count = apply(plan, args.backup) if args.command == "apply" else rollback(plan)
        print(json.dumps({"command": args.command, "changed": count}))


if __name__ == "__main__":
    main()
