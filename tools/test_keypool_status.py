# -*- coding: utf-8 -*-
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent_platform"))
from legion_command import keypool as K  # noqa: E402


def test_four_state_summary():
    assert K._seat_summary([])["status"] == "unconfigured"
    assert K._seat_summary([{"status": "healthy", "last_checked": "2026-10-28T01:00:00"}]) == {
        "status": "available", "available_count": 1, "total_count": 1,
        "partial": False, "last_tested_at": "2026-10-28T01:00:00"}
    s = K._seat_summary([
        {"status": "healthy", "last_checked": "2026-10-28T01:00:00"},
        {"status": "quota", "last_checked": "2026-10-28T02:00:00"},
    ])
    assert s["status"] == "available" and s["partial"] is True
    assert s["last_tested_at"] == "2026-10-28T02:00:00"
    assert K._seat_summary([{"status": "invalid"}])["status"] == "invalid"
    assert K._seat_summary([{"status": "degraded"}])["status"] == "throttled"
