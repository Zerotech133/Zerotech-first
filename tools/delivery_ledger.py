# -*- coding: utf-8 -*-
"""非研发部门交付台账（周度 CSV）：init / check / summary。

用法：
  python -X utf8 tools/delivery_ledger.py --init --week 2026-W45
  python -X utf8 tools/delivery_ledger.py --check --week 2026-W45
  python -X utf8 tools/delivery_ledger.py --summary --week 2026-W45
  python -X utf8 tools/delivery_ledger.py --summary --week 2026-W45 --json
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from collections import Counter, defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DIR = os.path.join(REPO, "agent_platform", "data", "dev", "delivery_ledger")
HEADER = ["week", "date", "dept", "seat", "deliverable", "type", "evidence",
          "acceptance", "status", "note"]


def _path(out_dir, week):
    return os.path.join(out_dir, "%s.csv" % week)


def init_ledger(out_dir, week, force=False):
    os.makedirs(out_dir, exist_ok=True)
    p = _path(out_dir, week)
    if os.path.exists(p) and not force:
        return {"ok": False, "error": "exists", "path": p}
    with io.open(p, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerow(HEADER)
    return {"ok": True, "path": p}


def check_ledger(path):
    errors = []
    rows = 0
    try:
        with io.open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if list(reader.fieldnames or []) != HEADER:
                return {"ok": False, "rows": 0, "errors": ["表头不匹配: %s" % reader.fieldnames]}
            for i, r in enumerate(reader, 2):
                rows += 1
                for k in ("week", "date", "dept", "seat", "deliverable", "type"):
                    if not str(r.get(k) or "").strip():
                        errors.append("第 %d 行缺 %s" % (i, k))
                if r.get("status") and r["status"] not in ("done", "partial", "blocked", ""):
                    errors.append("第 %d 行 status 非法: %s" % (i, r["status"]))
    except OSError as e:
        return {"ok": False, "rows": 0, "errors": ["读取失败: %s" % e]}
    return {"ok": not errors, "rows": rows, "errors": errors}


def summarize(path):
    rows = []
    with io.open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    by_dept = Counter(r.get("dept") for r in rows)
    by_seat = Counter(r.get("seat") for r in rows)
    by_status = Counter(r.get("status") or "unspecified" for r in rows)
    by_type = Counter(r.get("type") for r in rows)
    return {"rows": len(rows), "by_dept": dict(by_dept), "by_seat": dict(by_seat),
            "by_status": dict(by_status), "by_type": dict(by_type)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="交付台账工具")
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--week", required=True)
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    path = _path(args.dir, args.week)
    if args.init:
        res = init_ledger(args.dir, args.week, args.force)
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res["ok"] else 1
    if not os.path.exists(path):
        print("台账不存在：%s（先 --init）" % path, file=sys.stderr)
        return 2
    if args.check:
        res = check_ledger(path)
        print(json.dumps(res, ensure_ascii=False, indent=2) if args.json
              else "ledger check: rows=%d ok=%s errors=%d" % (res["rows"], res["ok"], len(res["errors"])))
        return 0 if res["ok"] else 1
    if args.summary:
        res = summarize(path)
        print(json.dumps(res, ensure_ascii=False, indent=2) if args.json
              else "ledger %s: rows=%d depts=%s seats=%s notes=%s" % (
                  args.week, res["rows"], res["by_dept"], res["by_seat"], res["by_status"]))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
