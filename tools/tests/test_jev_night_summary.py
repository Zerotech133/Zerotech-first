# -*- coding: utf-8 -*-
"""NIGHTSUM 纯函数单测。"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import jev_night_summary as ns  # noqa: E402

SAMPLE = """[2026-10-29 22:40:00]   JEV rc=0 1500s BACKFILL judged=600/600 left=5000 1500.0s deadline_hit=1 alerted=0 cloud_fail=0 item_sleep=0.00 lease_yield=skip dup_skip=0 pref_skip=3 pref_keep=600
[2026-10-29 23:05:00] ROUND 1 | 剩余 JEV=5000
[2026-10-29 23:30:00]   JEV rc=0 1500s BACKFILL judged=300/600 left=4700 1500.0s deadline_hit=1 alerted=0 cloud_fail=0 item_sleep=0.00 lease_yield=skip dup_skip=0 pref_skip=1 pref_keep=600
[2026-10-29 23:55:00] END 轮数=2 | 剩余 JEV=0 | 本轮完成=900
"""


def test_parse_rounds():
    rounds = ns.parse_rounds(SAMPLE)
    assert len(rounds) == 2
    assert rounds[0]["judged"] == 600 and rounds[0]["rate_per_h"] == 1440.0
    assert rounds[0]["pref_skip"] == 3
    assert rounds[1]["rate_per_h"] == 720.0


def test_summarize():
    s = ns.summarize(ns.parse_rounds(SAMPLE), "END")
    assert s["rounds"] == 2 and s["judged_total"] == 900
    assert s["pref_skip_total"] == 4
    assert s["rounds_under_300"] == 0


def test_last_night_splits_on_gap():
    rounds = ns.parse_rounds(SAMPLE)
    assert len(ns.last_night(rounds, 1)) == 2


def test_cli_entry_present():
    from pathlib import Path as _P
    import jev_night_summary as _ns
    code = _P(_ns.__file__).read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in code


def test_truncated_line_without_pref_keep():
    line = ("[2026-10-29 23:05:30]   JEV rc=0 1510s BACKFILL judged=592/600 "
            "left=4649 1510.0s deadline_hit=1 alerted=0 cloud_fail=0 "
            "item_sleep=0.00 lease_yield=skip dup_skip=0 pref_skip=0 pref")
    rounds = ns.parse_rounds(line)
    assert len(rounds) == 1
    assert rounds[0]["judged"] == 592 and rounds[0]["pref_skip"] == 0
