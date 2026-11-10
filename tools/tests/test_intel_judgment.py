# -*- coding: utf-8 -*-
"""P0–P3 情报判断层测试：schema 迁移 / 评分敏感性 / 交叉影响 / 反馈校准。"""

from __future__ import annotations

import json
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
TOOLS = os.path.join(ROOT, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)


# ── P0 schema 迁移 ─────────────────────────────────────────────

def test_p0_migration_adds_six_columns_and_is_idempotent(tmp_path):
    import intel_brief_pregen as P

    db_path = tmp_path / "briefs.db"
    con = sqlite3.connect(str(db_path))
    con.executescript(
        "CREATE TABLE intel_briefs (code TEXT NOT NULL, trade_date TEXT NOT NULL,"
        " kind TEXT NOT NULL, name TEXT, tier TEXT, facts TEXT, conclusion TEXT,"
        " engine TEXT, engine_label TEXT, model TEXT, degrade_note TEXT,"
        " datasig TEXT, built_at TEXT, PRIMARY KEY (code, trade_date, kind))")
    con.execute("INSERT INTO intel_briefs (code, trade_date, kind, name)"
                " VALUES ('300750','2026-11-10','story','宁德时代')")
    con.commit()
    con.close()

    con = sqlite3.connect(str(db_path))
    P._migrate_judgment_cols(con)
    P._migrate_judgment_cols(con)  # 幂等
    cols = {r[1] for r in con.execute("PRAGMA table_info(intel_briefs)")}
    for c in P.JUDGMENT_COLS:
        assert c in cols
    n = con.execute("SELECT count(*) FROM intel_briefs").fetchone()[0]
    con.close()
    assert n == 1


# ── P1 评分 + 敏感性 ──────────────────────────────────────────

def _weights():
    return [
        {"key": "impact", "label": "影响", "weight": 0.30},
        {"key": "timeliness", "label": "时效", "weight": 0.20},
        {"key": "evidence", "label": "证据强度", "weight": 0.25},
        {"key": "actionability", "label": "可行动性", "weight": 0.15},
        {"key": "cost", "label": "成本", "weight": 0.10},
    ]


def test_p1_rank_order_and_score_range():
    import intel_priority_score as S
    criteria = _weights()
    w = [c["weight"] for c in criteria]
    items = [
        {"code": "A", "scores": {"impact": 5, "timeliness": 5, "evidence": 5,
                                 "actionability": 5, "cost": 5}},
        {"code": "B", "scores": {"impact": 1, "timeliness": 1, "evidence": 1,
                                 "actionability": 1, "cost": 1}},
    ]
    rows = S.rank(items, criteria, w, 1.0, 5.0, 2)
    assert [r["code"] for r in rows] == ["A", "B"]
    assert rows[0]["score"] == pytest.approx(100.0)
    assert rows[1]["score"] == pytest.approx(0.0)


def test_p1_sensitivity_detects_weight_flip():
    import intel_priority_score as S
    criteria = _weights()
    w = [c["weight"] for c in criteria]
    # 基准 B 略高；时效权重 -25% 后 A/B 打平，按代码 A 在前 → top1 翻转
    items = [
        {"code": "A", "scores": {"impact": 5, "timeliness": 1, "evidence": 1,
                                 "actionability": 1, "cost": 1}},
        {"code": "B", "scores": {"impact": 4, "timeliness": 3, "evidence": 1,
                                 "actionability": 1, "cost": 1}},
    ]
    base = S.rank(items, criteria, w, 1.0, 5.0, 1)
    assert base[0]["code"] == "B"
    sens = S.sensitivity(items, criteria, w, 1.0, 5.0, 0.25, 1)
    assert sens["verdict"] == "sensitive"
    assert sens["flips"] >= 1


def test_p1_rejects_missing_score():
    import intel_priority_score as S
    criteria = _weights()
    w = [c["weight"] for c in criteria]
    with pytest.raises(SystemExit):
        S._score_one({"code": "X", "scores": {"impact": 3}}, criteria, w,
                     1.0, 5.0)


# ── P2 交叉影响 ───────────────────────────────────────────────

def _case():
    return {
        "events": [
            {"id": "A", "base_prob": 0.4},
            {"id": "B", "base_prob": 0.3},
        ],
        "influences": [
            {"from": "A", "to": "B", "direction": 1, "strength": 0.5},
        ],
    }


def test_p2_adjusted_bounds_and_direction():
    import intel_cross_impact as C
    adj = C.adjust(_case())
    assert set(adj) == {"A", "B"}
    for v in adj.values():
        assert 0.01 <= v <= 0.99
    assert adj["B"] > 0.3  # 正向影响应抬升 B


def test_p2_monte_carlo_deterministic():
    import intel_cross_impact as C
    case = _case()
    adj = C.adjust(case)
    a = C.monte_carlo(case, adj, 500, 7)
    b = C.monte_carlo(case, adj, 500, 7)
    assert a == b
    assert a["samples"] == 500
    assert sum(a["marginals"].values()) > 0


# ── P3 反馈校准 ───────────────────────────────────────────────

def test_p3_brier_and_report(tmp_path, capsys):
    import intel_outcomes as O
    db = str(tmp_path / "facts.db")
    base = ["--db", db, "--add", "--brief-key", "300750:2026-11-10:story",
            "--code", "300750", "--trade-date", "2026-11-10",
            "--confidence", "high", "--probability", "0.8",
            "--action", "观察", "--outcome", "hit"]
    assert O.main(base) == 0
    con = sqlite3.connect(db)
    row = con.execute("SELECT outcome, brier FROM intel_outcomes").fetchone()
    con.close()
    assert row[0] == "hit"
    assert row[1] == pytest.approx(0.04)

    assert O.main(["--db", db, "--add", "--brief-key", "x:1:story",
                   "--confidence", "low", "--probability", "0.6",
                   "--outcome", "miss"]) == 0
    assert O.main(["--db", db, "--report", "--month", "2026-11"]) == 0
    out = capsys.readouterr().out
    assert "total=2" in out
