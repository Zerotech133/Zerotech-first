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
    assert "/api/auth/me" in s and "canApprove" in s
    assert "ADMIN" in s and "OWNER" in s and "APPROVER" in s
    assert 'id="identity"' in s
    assert 'id="flowfill"' in s and 'id="flowtext"' in s and 'id="flowpct"' in s
    assert 'id="quickfill"' in s and 'id="quicktext"' in s
    assert "setProgress" in s and "refreshFlow" in s
    assert "APPROVER" in s and "重新登录刷新身份" in s
    assert 'id="btn-retry"' in s and "重试失败角色" in s
    assert "Promise.all(roles.map" in s and "已完成 '+ok+'/5" in s
    assert 'id="auto-box"' in s and 'id="btn-auto"' in s and "autoRun" in s
    assert 'id="btn-approve-dispatch"' in s and "approveAndDispatch" in s
    assert "/api/case/save" in s and "/api/case/latest" in s and "serverHydrate" in s
    assert 'id="quick-box"' in s and 'id="btn-quick"' in s and "虚拟临战会" in s
    assert "Promise.all" in s and "升级为正式 Case" in s


def test_hq_has_case_entry():
    s = HQ.read_text(encoding="utf-8")
    assert 'href="/case"' in s and "Case 流程" in s
