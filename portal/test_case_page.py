# -*- coding: utf-8 -*-
"""Case 前端：路由、审批门、现有会议接口复用。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NGINX = ROOT / "portal" / "nginx.conf"
PAGE = ROOT / "portal" / "case.html"
HQ = ROOT / "portal" / "hq.html"


def test_case_route_is_protected():
    s = NGINX.read_text(encoding="utf-8")
    assert "location = /case {" in s
    block = s.split("location = /case {", 1)[1].split("}", 1)[0]
    assert "auth_request /_auth_guard;" in block
    assert "try_files /case.html =404;" in block


def test_case_page_has_seven_steps_and_approval_gate():
    s = PAGE.read_text(encoding="utf-8")
    for text in ("田丰汇总", "五角色读材料", "组织会议", "总结解决办法", "人类审批", "执行下发"):
        assert text in s
    assert 'id="btn-dispatch"' in s and "disabled" in s
    assert "state.case.approved" in s
    assert "/api/hq/meeting/save" in s and "/api/hq/meeting/dispatch" in s
    assert "localStorage" in s


def test_hq_has_case_entry():
    s = HQ.read_text(encoding="utf-8")
    assert 'href="/case"' in s and "Case 流程" in s
