# -*- coding: utf-8 -*-
"""D1 日终批量裁决+交棒 测试（2026-11-16 WO-D1-DAILY-BATCH）。

不触网：只测映射、去重解析、节流与 dry-run 分支。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _load():
    spec = importlib.util.spec_from_file_location(
        "d1_daily_batch_t", REPO / "tools" / "d1_daily_batch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


db = _load()


def test_tier_of_boundaries():
    assert db.tier_of(0.86) == "STRONG_BUY"
    assert db.tier_of(0.85) == "STRONG_BUY"
    assert db.tier_of(0.70) == "BUY"
    assert db.tier_of(0.60) == "BUY"
    assert db.tier_of(0.50) == "HOLD"
    assert db.tier_of(0.0) == "HOLD"


def test_gid_conversion():
    assert db.gid("002228.SZ") == "sz002228"
    assert db.gid("600012.SH") == "sh600012"


def test_judged_codes_matches_session_in_evidence(tmp_path, monkeypatch):
    ledger = tmp_path / "d1_decisions.jsonl"
    rows = [
        {"code": "002228.SZ", "evidence": "[{'kind': 'pool_v2_session', 'value': 'poolv2.2026-11-13.r1'}]"},
        {"code": "300804.SZ", "evidence": "[{'kind': 'pool_v2_session', 'value': 'poolv2.2026-11-13.r1'}]"},
        {"code": "600000.SH", "evidence": "[{'kind': 'pool_v2_session', 'value': 'poolv2.2026-11-10.r1'}]"},
    ]
    ledger.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(db, "DECISIONS", ledger)
    got = db.judged_codes("poolv2.2026-11-13.r1")
    assert got == {"002228.SZ", "300804.SZ"}  # 别的池日不算已判


def test_judged_codes_missing_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DECISIONS", tmp_path / "nope.jsonl")
    assert db.judged_codes("poolv2.x") == set()


def test_latest_session_reads_last_row(tmp_path, monkeypatch):
    p = tmp_path / "pool_v2_sessions.jsonl"
    p.write_text(
        json.dumps({"session_id": "poolv2.a", "reviews": []}) + "\n" +
        json.dumps({"session_id": "poolv2.b", "reviews": [{"code": "1"}]}) + "\n",
        encoding="utf-8")
    monkeypatch.setattr(db, "SESSION_LEDGER", p)
    s = db.latest_session()
    assert s["session_id"] == "poolv2.b"


def test_arm_plans_dry_run_skips():
    assert db.arm_plans(True) == {"skipped": "dry_run"}


def test_throttle_no_sleep_when_disabled():
    t0 = time.perf_counter()
    db._last_post_at[0] = 0.0
    db._throttle(0)
    assert time.perf_counter() - t0 < 0.05


def test_throttle_spacing(monkeypatch):
    slept = []
    monkeypatch.setattr(db.time, "sleep", lambda s: slept.append(round(s, 3)))
    db._last_post_at[0] = time.time()
    db._throttle(1.0)
    assert slept and 0.8 <= slept[0] <= 1.0
