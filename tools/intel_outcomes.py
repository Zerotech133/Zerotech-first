# -*- coding: utf-8 -*-
"""情报结论反馈与校准（P3）——intel_outcomes 表 + 月度命中率/Brier。

定位（2026-11-11 老田批准 P3）：
  没有“结论 → 行动 → 结果”的回流，就无法证明研判“越用越准”。
  本工具只做三件事：建表、记一条结果、按月汇总校准；不改研判卡、不代签。

表（与研判卡同库 agent_platform/data/intel/intel_facts.db）：
  intel_outcomes(
    brief_key TEXT PRIMARY KEY,   -- 如 300750:2026-11-10:story
    code, trade_date, kind,
    confidence TEXT,              -- high|medium|low
    probability REAL,             -- 发布时给出的概率（0–1，可空）
    action TEXT,                  -- 建议行动（原样留痕）
    outcome TEXT,                 -- hit|miss|partial
    brier REAL,                   -- (p - y)^2；partial 取 y=0.5
    resolved_at TEXT, notes TEXT, created_at TEXT)

用法：
  python -X utf8 tools/intel_outcomes.py --init
  python -X utf8 tools/intel_outcomes.py --add --brief-key 300750:2026-11-10:story \
      --code 300750 --trade-date 2026-11-10 --confidence high \
      --probability 0.7 --action "观察 220.41 得失" --outcome hit
  python -X utf8 tools/intel_outcomes.py --report --month 2026-11
退出码：0=成功；2=参数/数据不合法。
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, "agent_platform", "data", "intel",
                          "intel_facts.db")
OUTCOMES = ("hit", "miss", "partial")
CONFIDENCE = ("high", "medium", "low")

DDL = """
CREATE TABLE IF NOT EXISTS intel_outcomes (
    brief_key    TEXT PRIMARY KEY,
    code         TEXT,
    trade_date   TEXT,
    kind         TEXT,
    confidence   TEXT,
    probability  REAL,
    action       TEXT,
    outcome      TEXT,
    brier        REAL,
    resolved_at  TEXT,
    notes        TEXT,
    created_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_io_month ON intel_outcomes(resolved_at);
"""


def _die(msg: str) -> "NoReturn":  # noqa: F821
    sys.stderr.write("[intel_outcomes] %s\n" % msg)
    raise SystemExit(2)


def _brier(probability, outcome):
    if probability is None:
        return None
    y = {"hit": 1.0, "miss": 0.0, "partial": 0.5}[outcome]
    return round((float(probability) - y) ** 2, 6)


def cmd_init(db_path: str) -> int:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    con = sqlite3.connect(db_path)
    try:
        con.executescript(DDL)
        con.commit()
    finally:
        con.close()
    print("init ok db=%s" % db_path)
    return 0


def cmd_add(db_path: str, a) -> int:
    if a.outcome not in OUTCOMES:
        _die("--outcome 必须为 hit|miss|partial")
    if a.confidence not in CONFIDENCE:
        _die("--confidence 必须为 high|medium|low")
    prob = a.probability
    if prob is not None and not (0.0 <= prob <= 1.0):
        _die("--probability 必须在 0–1")
    brier = _brier(prob, a.outcome)
    cmd_init(db_path)
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT INTO intel_outcomes (brief_key, code, trade_date, kind,"
            " confidence, probability, action, outcome, brier, resolved_at,"
            " notes, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(brief_key) DO UPDATE SET"
            " confidence=excluded.confidence, probability=excluded.probability,"
            " action=excluded.action, outcome=excluded.outcome,"
            " brier=excluded.brier, resolved_at=excluded.resolved_at,"
            " notes=excluded.notes",
            (a.brief_key, a.code, a.trade_date, a.kind or "story",
             a.confidence, prob, a.action, a.outcome, brier,
             a.resolved_at or dt.date.today().isoformat(), a.notes,
             dt.datetime.now().isoformat(timespec="seconds")))
        con.commit()
    finally:
        con.close()
    print("add ok key=%s outcome=%s brier=%s"
          % (a.brief_key, a.outcome, brier))
    return 0


def cmd_report(db_path: str, month: str) -> int:
    if not os.path.exists(db_path):
        _die("库不存在：%s" % db_path)
    con = sqlite3.connect(db_path)
    try:
        where, args = "", []
        if month:
            where = " WHERE resolved_at LIKE ?"
            args = [month + "%"]
        rows = con.execute(
            "SELECT outcome, confidence, probability, brier FROM"
            " intel_outcomes" + where, args).fetchall()
    finally:
        con.close()
    total = len(rows)
    hits = sum(1 for r in rows if r[0] == "hit")
    misses = sum(1 for r in rows if r[0] == "miss")
    partial = sum(1 for r in rows if r[0] == "partial")
    graded = hits + misses
    hit_rate = (hits / graded) if graded else None
    briers = [r[3] for r in rows if r[3] is not None]
    mean_brier = (sum(briers) / len(briers)) if briers else None
    by_conf = {}
    for r in rows:
        by_conf.setdefault(r[1] or "unknown", []).append(r[0])
    print("month=%s total=%d hit=%d miss=%d partial=%d"
          % (month or "all", total, hits, misses, partial))
    print("hit_rate=%s mean_brier=%s"
          % ("n/a" if hit_rate is None else "%.4f" % hit_rate,
             "n/a" if mean_brier is None else "%.4f" % mean_brier))
    for conf, outs in sorted(by_conf.items()):
        h = sum(1 for o in outs if o == "hit")
        g = sum(1 for o in outs if o in ("hit", "miss"))
        print("  confidence=%-6s n=%d hit_rate=%s"
              % (conf, len(outs),
                 "n/a" if not g else "%.4f" % (h / g)))
    print("note: Brier 仅在发布时给出 probability 时计算；partial 按 y=0.5。")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="情报结论反馈与校准（P3）")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--add", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--brief-key")
    ap.add_argument("--code")
    ap.add_argument("--trade-date")
    ap.add_argument("--kind", default="story")
    ap.add_argument("--confidence")
    ap.add_argument("--probability", type=float)
    ap.add_argument("--action")
    ap.add_argument("--outcome")
    ap.add_argument("--notes")
    ap.add_argument("--resolved-at")
    ap.add_argument("--month")
    a = ap.parse_args(argv)

    actions = [x for x in (a.init, a.add, a.report) if x]
    if len(actions) != 1:
        _die("必须且只能指定 --init / --add / --report 之一")
    if a.init:
        return cmd_init(a.db)
    if a.add:
        if not a.brief_key:
            _die("--add 需要 --brief-key")
        return cmd_add(a.db, a)
    return cmd_report(a.db, a.month)


if __name__ == "__main__":
    raise SystemExit(main())
