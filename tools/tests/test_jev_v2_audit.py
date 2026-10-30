# -*- coding: utf-8 -*-
"""契约 v2 账本列测试：horizon/prob 迁移与写入。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import jev_audit  # noqa: E402


def _rows():
    return [{
        "news_id": "n1", "ts": "2026-10-30 10:00:00", "created_at": "2026-10-30 10:05:00",
        "source": "sina", "bias": "bull", "conf": 0.8, "codes": ["600519"],
        "event_type": "price", "model": "qwen3.5:9b", "prompt_version": "v2",
        "horizon": "days", "prob": {"bull": 0.8, "bear": 0.1, "neutral": 0.1},
    }]


def test_upsert_persists_v2_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(jev_audit, "AUDIT_DIR", tmp_path)
    monkeypatch.setattr(jev_audit, "AUDIT_DB", tmp_path / "jev_audit.db")
    conn = jev_audit.open_db()
    opens = ["2026-10-31", "2026-11-02", "2026-11-03"]
    jev_audit.upsert_judged(conn, _rows(), opens)
    row = conn.execute("SELECT horizon, prob FROM dec_audit WHERE news_id='n1'").fetchone()
    conn.close()
    assert row[0] == "days"
    assert json.loads(row[1])["bull"] == 0.8