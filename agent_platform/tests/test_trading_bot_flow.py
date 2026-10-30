# -*- coding: utf-8 -*-
"""盯盘触发→人工确认→TR1 模拟执行 端到端测试（WO-BOT-CONFIRM）。"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from agent_platform import trading_bot_flow as flow  # noqa: E402


def _paths(tmp_path):
    return {
        "data_dir": str(tmp_path / "bot"),
        "paper_state_path": str(tmp_path / "state.json"),
        "paper_ledger_path": str(tmp_path / "ledger.jsonl"),
    }


def _confirm(tmp_path, action="BUY", report="WA1-1"):
    p = _paths(tmp_path)
    return flow.create_confirmation(
        symbol="600519", name="茅台", action=action, price=100.0, stop=92.0,
        confidence=0.8, reason="盯盘触发", source="WA1",
        source_report_id=report, data_dir=p["data_dir"],
    )


def test_pending_then_approve_executes(tmp_path):
    p = _paths(tmp_path)
    c = _confirm(tmp_path)
    assert c["status"] == "pending" and c["human_gate"] is True
    rows = flow.list_confirmations("pending", data_dir=p["data_dir"])
    assert len(rows) == 1
    out = flow.decide(c["confirmation_id"], "approve", by="老田",
                      data_dir=p["data_dir"], paper_state_path=p["paper_state_path"],
                      paper_ledger_path=p["paper_ledger_path"])
    assert out["status"] == "executed"
    ledger = [json.loads(x) for x in open(p["paper_ledger_path"], encoding="utf-8") if x.strip()]
    assert len(ledger) == 1
    assert ledger[0]["symbol"] == "600519" and ledger[0]["trading_permitted"] is False
    # 幂等：重复批准不再执行
    again = flow.decide(c["confirmation_id"], "approve", by="老田",
                        data_dir=p["data_dir"], paper_state_path=p["paper_state_path"],
                        paper_ledger_path=p["paper_ledger_path"])
    assert again["status"] == "executed"
    assert len([x for x in open(p["paper_ledger_path"], encoding="utf-8") if x.strip()]) == 1


def test_reject_does_not_execute(tmp_path):
    p = _paths(tmp_path)
    c = _confirm(tmp_path)
    out = flow.decide(c["confirmation_id"], "reject", by="老田", data_dir=p["data_dir"])
    assert out["status"] == "rejected"
    assert not Path(p["paper_ledger_path"]).exists()


def test_kill_switch_blocks(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    c = _confirm(tmp_path)
    monkeypatch.setenv("PAPER_KILL", "1")
    out = flow.decide(c["confirmation_id"], "approve", by="老田", data_dir=p["data_dir"])
    assert out["status"] == "killed"
    assert not Path(p["paper_ledger_path"]).exists()


def test_duplicate_report_idempotent(tmp_path):
    p = _paths(tmp_path)
    a = _confirm(tmp_path, report="WA1-SAME")
    b = _confirm(tmp_path, report="WA1-SAME")
    assert a["confirmation_id"] == b["confirmation_id"]
    assert len(flow.list_confirmations(data_dir=p["data_dir"])) == 1


def test_feishu_card_payload(tmp_path):
    c = _confirm(tmp_path)
    card = flow.build_feishu_card(c)
    assert card["header"]["title"]["content"].startswith("模拟交易人工确认")
    actions = card["elements"][-1]["actions"]
    assert {a["value"]["action"] for a in actions} == {"approve", "reject"}
    p = flow.save_card(c, data_dir=_paths(tmp_path)["data_dir"])
    assert Path(p).exists()
