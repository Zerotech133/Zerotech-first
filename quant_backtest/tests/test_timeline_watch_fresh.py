# -*- coding: utf-8 -*-
"""report 项 mtime 新鲜度判定单测（watch-timeline-dev，2026-10-18）。

口径：daily/xq_intel_YYYYMMDD.json 的 mtime >= 当日 18:50 视为今晚 19:00
日报新产物（PASS 路径）；早于 18:50 判 UNKNOWN（冒烟/残留旧产物，
19:00 日报生成未确认），防"旧 JSON 过 15 键 → 假绿"。
"""
import json
import os
import sys
from datetime import datetime

import pytest

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import xq_timeline_watch as xtw  # noqa: E402

DAY = "2026-10-18"


def _ts(day: str, hhmm: str) -> float:
    return datetime.strptime("%s %s:00" % (day, hhmm), "%Y-%m-%d %H:%M:%S").timestamp()


def _make_payload() -> dict:
    """最小合法 15 键 payload（过 verify_daily_report.check_payload 语义）。"""
    return {
        "date": DAY, "generated_at": DAY + " 19:01:00",
        "extractor_version": "v1.3",
        "pipeline": {"llm_model": "qwen3.5:9b", "prompt_version": "v1.3"},
        "ingested_today": 3, "posts_today": 0,
        "classified_today": 0,
        "event_type": {}, "sentiment": {}, "content_type": {},
        "top_codes": {}, "top_themes": {},
        "anomaly_signals": [], "high_confidence": [],
        "word_tempo": {"bearish": [], "bullish": []},
    }


@pytest.fixture
def watch_env(tmp_path, monkeypatch):
    """隔离 OUT_ROOT，注入最小合法日报 JSON；返回 daily json 路径。"""
    out_root = tmp_path / "xq_report"
    daily_dir = out_root / "daily"
    daily_dir.mkdir(parents=True)
    jp = daily_dir / ("xq_intel_%s.json" % DAY.replace("-", ""))
    jp.write_text(json.dumps(_make_payload()), encoding="utf-8")
    monkeypatch.setattr(xtw, "OUT_ROOT", str(out_root))
    return str(jp)


def test_fresh_threshold_boundary(watch_env):
    """18:50 整点（含）即视为新鲜。"""
    assert xtw.report_is_fresh(_ts(DAY, "18:50"), DAY) is True


def test_fresh_after_1850(watch_env):
    assert xtw.report_is_fresh(_ts(DAY, "19:02"), DAY) is True


def test_stale_before_1850(watch_env):
    """14:41 人工冒烟残留 → 不新鲜（UNKNOWN 依据）。"""
    assert xtw.report_is_fresh(_ts(DAY, "14:41"), DAY) is False


def test_collect_report_pass_when_fresh(watch_env):
    os.utime(watch_env, (_ts(DAY, "19:02"),) * 2)
    item = xtw.collect_report(DAY)
    assert item["status"] == "PASS"


def test_collect_report_unknown_when_stale(watch_env):
    """旧产物过 15 键也必须 UNKNOWN，不得假绿。"""
    os.utime(watch_env, (_ts(DAY, "14:41"),) * 2)
    item = xtw.collect_report(DAY)
    assert item["status"] == "UNKNOWN"
    assert "18:50" in item["detail"]
