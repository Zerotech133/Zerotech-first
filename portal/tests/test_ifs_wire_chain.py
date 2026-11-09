# -*- coding: utf-8 -*-
"""IFS v1 端到端链路回归：decision → handoff v2 → watch_plan → confirmation → paper receipt 四 ID。

decision 先在 tmp 账本真实落一条，再把其 decision_id 带入 handoff；测试全程 tmp 目录，
不读写生产账本/paper/pool。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from agent_platform import d1_decision as D  # noqa: E402
from agent_platform import d1_handoff as H  # noqa: E402
from agent_platform import watch_plan_consumer as C  # noqa: E402
from agent_platform import trading_bot_flow as flow  # noqa: E402
from agent_platform.paper import trade_receipt as tr  # noqa: E402


@pytest.fixture(autouse=True)
def _hermetic_trigger_ledger(monkeypatch):
    """宿主若导出 WATCH_TRIGGER_LEDGER_PATH，adapter 会把 trigger 事件写进生产账本。

    本测试声明「全程 tmp，不读写生产账本」，故清空该环境变量，避免第二次运行命中
    「watch_plan 已绑定其它 confirmation」而假红（H1 合并窗口 unblock，2026-11-04）。
    """
    monkeypatch.delenv("WATCH_TRIGGER_LEDGER_PATH", raising=False)


EXPIRES = "2099-01-01T00:00:00+08:00"


def _create_decision(tmp_path):
    """真实落一条 D1 decision，返回其 decision_id；不碰生产账本。"""
    row = D.append_decision(
        {
            "code": "600519.SH",
            "name": "贵州茅台",
            "tier": "BUY",
            "confidence": 0.7,
            "rationale": "IFS e2e：回抽不破中枢，paper-only 交盯盘观察",
            "evidence": ["e2e"],
            "paper_only": True,
        },
        ledger=tmp_path / "d1_decisions.jsonl",
    )
    return row["decision_id"]


def _payload(plan_id="wp-e2e-001", decision_id=None):
    return {
        "schema": "d1_handoff/v2",
        "code": "600519.SH",
        "name": "贵州茅台",
        "target": "WA1",
        "action": "watch",
        "rationale": "IFS 链路回归：回抽不破中枢，交盯盘观察",
        "buy_zone": [1480.0, 1520.0],
        "stop_loss": 1400.0,
        "trigger": "回踩 1480-1520 且缩量",
        "decision_id": decision_id,
        "paper_only": True,
        "watch_plans": [
            {
                "plan_id": plan_id,
                "seat": "WA1",
                "method": "trend_pullback",
                "conditions": {
                    "symbol": "600519.SH",
                    "action": "BUY",
                    "price": 1500.0,
                    "stop": 1400.0,
                    "target": 1700.0,
                },
                "expires_at": EXPIRES,
            }
        ],
    }


def test_handoff_to_paper_receipt_has_four_ids(tmp_path):
    handoff_ledger = tmp_path / "d1_handoffs.jsonl"
    plan_ledger = tmp_path / "watch_plans.jsonl"
    bot_dir = tmp_path / "bot"
    state = tmp_path / "paper_state.json"
    paper_ledger = tmp_path / "paper_ledger.jsonl"

    decision_id = _create_decision(tmp_path)
    row = H.append_handoff(_payload(decision_id=decision_id), ledger=handoff_ledger)
    consumed = C.consume_handoff(row, path=plan_ledger)
    assert consumed["armed"] == 1
    plan = consumed["plans"][0]
    assert plan["status"] == "armed"

    trigger_ledger = tmp_path / "watch_triggers.jsonl"
    triggered = C.trigger_plan("wp-e2e-001", path=plan_ledger,
                               data_dir=str(bot_dir),
                               trigger_ledger=str(trigger_ledger))
    confirmation = triggered["confirmation"]
    assert confirmation["status"] == "pending"
    cid = confirmation["confirmation_id"]

    executed = flow.decide(
        cid, "approve", by="老田",
        data_dir=str(bot_dir),
        paper_state_path=str(state),
        paper_ledger_path=str(paper_ledger),
        receipts_path=str(tr.receipts_path(str(bot_dir))),
    )
    ids = executed["ids"]
    assert ids["decision_id"] == decision_id
    assert ids["handoff_id"] == row["receipt_id"]
    assert ids["watch_plan_id"] == "wp-e2e-001"
    assert ids["confirmation_id"] == cid
    assert ids["trade_id"] == executed["trade_id"]
    assert executed["paper_only"] is True
    assert executed["trading_permitted"] is False

    receipts = tr.read_receipts(tr.receipts_path(str(bot_dir)))
    assert len(receipts) == 1
    rids = receipts[0]["ids"]
    assert rids["decision_id"] == decision_id
    assert rids["handoff_id"] == row["receipt_id"]
    assert rids["watch_plan_id"] == "wp-e2e-001"
    assert rids["confirmation_id"] == cid
    assert rids["trade_id"] == executed["trade_id"]
