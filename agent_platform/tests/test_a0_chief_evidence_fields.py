# -*- coding: utf-8 -*-
"""A0 shadow 日报字段透传测试（2026-10-31 WO-D1-B123-20261031 / B3）。

背景：``run_shadow.build_ledger_row`` 早已把 ``decision_id`` / ``watch_reason`` /
``evidence_refs`` 写进影子账本行，但 A0 日报（``a0_chief.build`` → 
``data/shadow/daily_report_YYYY-MM-DD.md``）的「今日产出」表未展示——决策证据链
在人的日报里断了。本文件把「三字段透传 + 旧行容错」变成可回归断言。

纪律：只把 ``LEDGER`` monkeypatch 到 tmp；``debate_stats``/``debate_health``
打桩（不碰 checkpoints.db），不写任何生产文件。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "a0_chief_under_test", ROOT / "agent_platform" / "a0_chief.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["a0_chief_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


A0 = _load()
TODAY = "2026-10-31"


def _done_row(**overrides):
    """m5.v1 done 行最小样本（字段口径对齐 run_shadow.build_ledger_row）。"""
    row = {
        "date": TODAY, "symbol": "600519", "name": "贵州茅台",
        "status": "done", "run_id": "run-abc", "price_at_decision": 1500.0,
        "recommendation": "WATCH", "confidence": 0.62, "degraded": False,
        "decision_id": "D1-20261031-abc1234567",
        "watch_reason": "l4_unresolved",
        "evidence_refs": [
            {"source": "market_technical",
             "uri": "make_context://600519/market_technical",
             "as_of": "2026-10-31T17:10:00", "hash": "h1", "freshness_s": 0},
            {"source": "fundamental",
             "uri": "make_context://600519/fundamental",
             "as_of": "2026-10-31T17:10:00", "hash": "h2", "freshness_s": 0},
        ],
    }
    row.update(overrides)
    return row


def _build(tmp_path, monkeypatch, rows):
    ledger = tmp_path / "shadow_ledger.jsonl"
    ledger.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")
    monkeypatch.setattr(A0, "LEDGER", ledger)
    monkeypatch.setattr(A0, "debate_stats", lambda run_id: (0, None))
    monkeypatch.setattr(A0, "debate_health",
                        lambda run_id: {"t1": "?", "t2": "?", "sim_last": None})
    text, p0 = A0.build(TODAY)
    assert p0 == 0
    return text


def test_report_passes_through_evidence_fields(tmp_path, monkeypatch):
    """B3 核心判据：日报透传 decision_id / watch_reason / evidence_refs。"""
    text = _build(tmp_path, monkeypatch, [_done_row()])
    assert "D1-20261031-abc1234567" in text
    assert "l4_unresolved" in text
    assert "market_technical" in text
    assert "fundamental" in text
    # 表头也要有这三列（下游/人读可得）
    assert "决策ID" in text and "WATCH原因" in text and "证据引用" in text


def test_watch_and_direction_rows_both_rendered(tmp_path, monkeypatch):
    """WAIT/方向单并存时各自字段独立展示（不串行、不覆盖）。"""
    rows = [
        _done_row(),
        _done_row(symbol="000858", name="五粮液", run_id="run-2",
                  recommendation="BUY", decision_id="D1-20261031-buy0000000",
                  watch_reason=None, degraded=True,
                  evidence_refs=[{"source": "news_event"}]),
    ]
    text = _build(tmp_path, monkeypatch, rows)
    assert "D1-20261031-abc1234567" in text and "D1-20261031-buy0000000" in text
    assert "l4_unresolved" in text
    assert "news_event" in text


def test_legacy_row_without_fields_uses_placeholders(tmp_path, monkeypatch):
    """旧 m5 前/无字段行：降级为 - / 0 条，日报不崩、不猜。"""
    row = {"date": TODAY, "symbol": "000858", "name": "五粮液", "status": "done",
           "run_id": "legacy", "recommendation": "HOLD", "confidence": 0.5,
           "price_at_decision": 100.0}
    text = _build(tmp_path, monkeypatch, [row])
    assert "000858" in text
    assert "0 条" in text
    assert "| - | - | 0 条 |" in text          # 决策ID/WATCH原因占位
    assert "None" not in text                  # 不得把 None 直接印进日报


def test_evidence_refs_tolerates_bad_shapes(tmp_path, monkeypatch):
    """脏数据容错：字符串/非法项/管道符都不许把日报打崩。"""
    text = _build(tmp_path, monkeypatch, [_done_row(evidence_refs="not-a-list")])
    assert "1 条:not-a-list" in text
    text2 = _build(tmp_path, monkeypatch,
                   [_done_row(evidence_refs=[{}, None, "has|pipe"])])
    assert "3 条:has/pipe" in text2


def test_no_done_rows_keeps_table_well_formed(tmp_path, monkeypatch):
    text = _build(tmp_path, monkeypatch, [])
    assert "今日无完成决策" in text
