# -*- coding: utf-8 -*-
"""策略变更四段审批：research submit → BACK1 → ACAD1 → AI1 publish。

只记录审批，不部署代码、不改线上策略。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime

import sqlalchemy as sa

from zhice_os_v2.config.settings import get_settings

DDL = """
CREATE TABLE IF NOT EXISTS v2_strategy_change (
  id BIGSERIAL PRIMARY KEY,
  strategy_id VARCHAR(64) NOT NULL,
  from_version VARCHAR(64) NOT NULL,
  to_version VARCHAR(64) NOT NULL,
  stage VARCHAR(24) NOT NULL,
  requested_by VARCHAR(32) NOT NULL,
  backtest_by VARCHAR(32),
  acad1_by VARCHAR(32),
  published_by VARCHAR(32),
  summary TEXT NOT NULL DEFAULT '',
  evidence TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

VALID_STAGES = {"submitted", "backtest_approved", "acad1_approved", "published", "rejected"}


def _conn():
    e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
    return e


def _row(c, cid):
    return c.execute(sa.text("SELECT * FROM v2_strategy_change WHERE id=:i"), {"i": cid}).mappings().first()


def submit(args):
    e = _conn()
    with e.begin() as c:
        c.execute(sa.text(DDL))
        has = c.execute(sa.text(
            "SELECT 1 FROM v2_strategy WHERE strategy_id=:s UNION ALL "
            "SELECT 1 FROM v2_strategy_sidecar WHERE strategy_id=:s"), {"s": args.strategy}).first()
        if not has:
            print("unknown strategy:", args.strategy); return 2
        owner = c.execute(sa.text(
            "SELECT research_owner FROM v2_strategy_ownership WHERE strategy_id=:s"),
            {"s": args.strategy}).scalar()
        if not owner or args.by != owner:
            print("role denied: submit requires research_owner=%s, got %s" % (owner or "NONE", args.by)); return 3
        rid = c.execute(sa.text("""
            INSERT INTO v2_strategy_change
              (strategy_id, from_version, to_version, stage, requested_by, summary, evidence, note)
            VALUES (:s,:fv,:tv,'submitted',:by,:summary,:evidence,:note)
            RETURNING id
        """), {"s": args.strategy, "fv": args.from_version, "tv": args.to_version,
               "by": args.by, "summary": args.summary, "evidence": args.evidence,
               "note": args.note}).scalar()
        print("submitted id=%s strategy=%s stage=submitted" % (rid, args.strategy)); return 0


def advance(args, expected_stage, next_stage, role_field, allowed_by):
    e = _conn()
    with e.begin() as c:
        row = _row(c, args.id)
        if not row:
            print("not found"); return 2
        if row["stage"] != expected_stage:
            print("invalid transition: %s -> %s (need %s)" % (row["stage"], next_stage, expected_stage)); return 2
        if args.by != allowed_by:
            print("role denied: %s requires %s, got %s" % (next_stage, allowed_by, args.by)); return 3
        col = {"backtest_by": "backtest_by", "acad1_by": "acad1_by", "published_by": "published_by"}[role_field]
        c.execute(sa.text(
            "UPDATE v2_strategy_change SET stage=:st, %s=:by, updated_at=now() WHERE id=:i" % col
        ), {"st": next_stage, "by": args.by, "i": args.id})
        print("id=%s %s -> %s by %s" % (args.id, expected_stage, next_stage, args.by)); return 0


def reject(args):
    if args.by not in ("ACAD1", "BACK1"):
        print("role denied: reject requires ACAD1 or BACK1, got %s" % args.by); return 3
    e = _conn()
    with e.begin() as c:
        row = _row(c, args.id)
        if not row:
            print("not found"); return 2
        if row["stage"] in ("published", "rejected"):
            print("cannot reject", row["stage"]); return 2
        c.execute(sa.text("UPDATE v2_strategy_change SET stage='rejected', note=:n, updated_at=now() WHERE id=:i"),
                  {"n": (args.note or "") + " | rejected_by=" + args.by, "i": args.id})
        print("rejected id=", args.id); return 0


def show(args):
    e = _conn()
    with e.connect() as c:
        c.execute(sa.text(DDL))
        rows = c.execute(sa.text("SELECT * FROM v2_strategy_change ORDER BY id DESC LIMIT :n"),
                         {"n": args.limit}).mappings().all()
        for r in rows:
            d = dict(r); d["created_at"] = str(d["created_at"]); d["updated_at"] = str(d["updated_at"])
            print(json.dumps(d, ensure_ascii=False))
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit")
    s.add_argument("--strategy", required=True); s.add_argument("--from-version", required=True)
    s.add_argument("--to-version", required=True); s.add_argument("--by", required=True)
    s.add_argument("--summary", default=""); s.add_argument("--evidence", default=""); s.add_argument("--note", default="")
    s.set_defaults(fn=submit)
    b = sub.add_parser("backtest")
    b.add_argument("id", type=int); b.add_argument("--by", required=True); b.set_defaults(fn=lambda a: advance(a, "submitted", "backtest_approved", "backtest_by", "BACK1"))
    a = sub.add_parser("acad1")
    a.add_argument("id", type=int); a.add_argument("--by", required=True); a.set_defaults(fn=lambda x: advance(x, "backtest_approved", "acad1_approved", "acad1_by", "ACAD1"))
    pub = sub.add_parser("publish")
    pub.add_argument("id", type=int); pub.add_argument("--by", required=True); pub.set_defaults(fn=lambda x: advance(x, "acad1_approved", "published", "published_by", "AI1"))
    r = sub.add_parser("reject")
    r.add_argument("id", type=int); r.add_argument("--by", required=True); r.add_argument("--note", default=""); r.set_defaults(fn=reject)
    l = sub.add_parser("list")
    l.add_argument("--limit", type=int, default=10); l.set_defaults(fn=show)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
