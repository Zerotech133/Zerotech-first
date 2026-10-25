# -*- coding: utf-8 -*-
"""jev_roles_daily 纯函数单测（K-ORG-20261026-JEVROLE）。

原则：**只测纯函数**，不触碰真实日志/DB（避免夜间独占窗口产生额外 IO）。
采集函数的集成验证放在 07:00 之后单独跑一次 dry-run。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import jev_roles_daily as jrd  # noqa: E402


def test_day_bounds():
    lo, hi = jrd._day_bounds("2026-10-26")
    assert lo == datetime(2026, 10, 26, 0, 0, 0)
    assert hi == datetime(2026, 10, 27, 0, 0, 0)


def test_in_day_window():
    lo, hi = jrd._day_bounds("2026-10-26")
    assert jrd._in_day("2026-10-26T06:00:00", lo, hi) is True
    assert jrd._in_day("2026-10-25T23:59:59", lo, hi) is False
    assert jrd._in_day("2026-10-27T00:00:00", lo, hi) is False
    assert jrd._in_day("", lo, hi) is False
    assert jrd._in_day("不是时间", lo, hi) is False


def test_classify_gate_msg_buckets():
    cases = {
        "无新告警（OPEN 总数 0，2h 窗口）": "no_alert",
        "推送 3 条告警到企业微信": "push",
        "降噪拦截：非紧急": "suppress",
        "推送失败：网络错误": "fail",
        "jev 重试 1/2": "retry",
        "backend dispatch: ollama": "other",
    }
    for msg, want in cases.items():
        assert jrd.classify_gate_msg(msg) == want, (msg, jrd.classify_gate_msg(msg))


def test_parse_jev_good_and_bad():
    good = '{"bias": "bull", "conf": 0.9}'
    assert jrd._parse_jev(good)["bias"] == "bull"
    assert jrd._parse_jev("not-json") == {}
    assert jrd._parse_jev("") == {}
    assert jrd._parse_jev('[1,2,3]') == {}


def test_render_md_structure_and_empty_states():
    data = {
        "date": "2026-10-26",
        "generated_at": "2026-10-26T07:00:00+08:00",
        "gate": {"role": "JEV_GATE", "ok": True, "polls": 1440, "push": 2,
                 "suppress": 3, "fail": 1, "retry": 4, "suppress_rate": 0.6},
        "review": {"role": "JEV_REVIEW", "ok": True, "rounds": 144,
                   "items": 0, "regime": "1.00", "source": "tushare"},
        "qd": {"role": "JEV_QD", "ok": True, "pushed": 21, "failed": 1,
               "success_rate": 0.9545, "failed_keys": ["USStock␟SKHY"]},
        "review_queue": [{
            "id": "em:1", "ts": "2026-10-26 06:00", "source": "eastmoney",
            "title": "示例标题", "bias": "neutral", "conf": 0.9,
            "event_type": "rate", "reason": "示例",
        }],
    }
    md = jrd.render_md(data)
    assert "JEV_GATE 廖化" in md
    assert "JEV_REVIEW 蒋干" in md
    assert "JEV_QD 伊籍" in md
    assert "| 降噪率 | 0.6 |" in md
    assert "USStock␟SKHY" in md
    assert "示例标题" in md


def test_render_md_with_missing_sections():
    """采集失败时字段为 None：日报应显示「—」而不是崩。"""
    data = {
        "date": "2026-10-26",
        "generated_at": "",
        "gate": {"role": "JEV_GATE", "ok": False, "err": "OSError"},
        "review": {"role": "JEV_REVIEW", "ok": False, "err": "OSError"},
        "qd": {"role": "JEV_QD", "ok": False, "err": "JSONDecodeError"},
        "review_queue": [],
    }
    md = jrd.render_md(data)
    assert "—" in md
    assert "今日无可抽样条目" in md
