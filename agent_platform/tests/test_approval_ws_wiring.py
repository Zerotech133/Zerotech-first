# -*- coding: utf-8 -*-
"""D3 接线冒烟测试：ws 卡片 approval:* 分支 + 动作注册表登记。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "portal"))
WS = ROOT / "portal" / "feishu_bot_ws.py"
CA = ROOT / "portal" / "card_actions.py"


def test_approval_branch_wired():
    src = WS.read_text(encoding="utf-8")
    assert "def _handle_approval_card(val, sender)" in src
    assert 'tag.startswith("approval:")' in src
    assert "approval_control.handle_action" in src
    # 红线：分支内不得出现重启/发布动作
    branch = src.split('tag.startswith("approval:")')[1].split("if tag.startswith(\"dim:\")")[0]
    for banned in ("restart", "publish", "deploy", "shutdown"):
        assert banned not in branch.lower()


def test_actions_registered():
    src = CA.read_text(encoding="utf-8")
    for aid in ("approval_approve", "approval_reject", "approval_escalate"):
        assert aid in src


def test_approval_control_pure_contract():
    import approval_control as ac
    tmp = Path(__file__).parent / "_tmp_approvals.jsonl"
    if tmp.exists():
        tmp.unlink()
    base = {"operator": {"open_id": "ou_test"},
            "action": {"value": {"tag": "approval:approve", "task": "WO-TEST", "level": "S2"}}}
    r1 = ac.handle_action(base, ledger=tmp)
    r2 = ac.handle_action(base, ledger=tmp)
    assert r1["ok"] and r2.get("duplicate") is True
    bad = {"operator": {"open_id": "ou_test"},
           "action": {"value": {"tag": "approval:unknown", "task": "WO-TEST"}}}
    assert ac.handle_action(bad, ledger=tmp)["ok"] is False
    tmp.unlink(missing_ok=True)
