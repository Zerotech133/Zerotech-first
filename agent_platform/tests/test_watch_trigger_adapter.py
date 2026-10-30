# -*- coding: utf-8 -*-
"""watch trigger adapter 离线测试（WO-WATCH-TRIGGER）。"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from agent_platform import watch_trigger_adapter as ad  # noqa: E402


def test_normalize_symbol():
    assert ad.normalize_symbol("600519.SH") == "sh600519"
    assert ad.normalize_symbol("000001") == "sz000001"
    assert ad.normalize_symbol("920378") == "bj920378"
    with pytest.raises(ad.WatchTriggerError):
        ad.normalize_symbol("")


def test_build_payload_requires_signal_fields():
    with pytest.raises(ad.WatchTriggerError, match="SYMBOL"):
        ad.build_payload({"action": "BUY", "price": 10})
    with pytest.raises(ad.WatchTriggerError, match="ACTION"):
        ad.build_payload({"symbol": "600519", "price": 10})
    with pytest.raises(ad.WatchTriggerError, match="PRICE"):
        ad.build_payload({"symbol": "600519", "action": "BUY"})


def test_upstream_only_report_cannot_trigger(tmp_path):
    report = {"report_id": "WA1-1", "report_kind": "ANOMALY",
              "event_id": "evt-1", "non_executable": True}
    with pytest.raises(ad.WatchTriggerError, match="SIGNAL"):
        ad.build_payload(None, report=report)
    # 只有 report 元数据时，signal 缺 symbol/price 仍 fail-closed
    with pytest.raises(ad.WatchTriggerError, match="SYMBOL"):
        ad.build_payload({"action": "BUY", "price": 10}, report=report)


def test_trigger_creates_pending_only(tmp_path):
    rec = ad.trigger(
        {"symbol": "600519", "action": "BUY", "price": 1500, "stop": 1400,
         "confidence": 0.8, "report_id": "WA1-EVT-1"},
        selection={"market": "sh", "name": "茅台"},
        evidence={"code": "600519.SH", "event_id": "evt-1"},
        data_dir=str(tmp_path / "bot"),
    )
    assert rec["status"] == "pending" and rec["human_gate"] is True
    assert rec["symbol"] == "sh600519" and rec["action"] == "BUY"
    assert rec["source"] == "WATCH_TOWER" and rec["source_report_id"] == "WA1-EVT-1"
    # 只建确认，不产生 paper 执行
    assert not (tmp_path / "ledger.jsonl").exists()
    assert not (tmp_path / "bot" / "confirmations" / (rec["confirmation_id"] + ".json")).read_text(
        encoding="utf-8").count("executed")


def test_idempotent_report_and_no_execution_on_build(tmp_path):
    args = dict(
        signal={"symbol": "000001", "action": "SELL", "price": 10, "report_id": "R-1"},
        data_dir=str(tmp_path / "bot"),
    )
    a = ad.trigger(**args)
    b = ad.trigger(**args)
    assert a["confirmation_id"] == b["confirmation_id"]
    files = list((tmp_path / "bot" / "confirmations").glob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8"))["status"] == "pending"
