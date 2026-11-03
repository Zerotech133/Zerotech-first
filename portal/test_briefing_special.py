# -*- coding: utf-8 -*-
"""田丰简报 / 人专项页面：路由、鉴权、链接静态断言。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NGINX = ROOT / "portal" / "nginx.conf"
BRIEF = ROOT / "portal" / "briefing.html"
SPECIAL = ROOT / "portal" / "special.html"
HQ = ROOT / "portal" / "hq.html"


def test_nginx_protects_briefing_and_special():
    s = NGINX.read_text(encoding="utf-8")
    for path in ("/briefing", "/special"):
        assert "location = " + path + " {" in s
        block = s.split("location = " + path + " {", 1)[1].split("}", 1)[0]
        assert "auth_request /_auth_guard;" in block
        assert "try_files /" + path[1:] + ".html =404;" in block


def test_briefing_links_to_special_and_uses_snapshot():
    s = BRIEF.read_text(encoding="utf-8")
    assert "/api/hq/snapshot" in s
    assert "/special?" in s
    assert "研究题" in s
    assert "部门汇报" in s
    assert 'id="btn-summary"' in s and "立即总结" in s
    assert 'id="btn-report"' in s and "汇报工作" in s
    assert 'id="dept-filter"' in s and 'option value=""' in s
    assert "localStorage" in s
    assert "不再用旧 Directive" in s  # 旧 Directive 仅作模型上下文，不冒充研究题
    assert "cleanTopic" in s
    assert 'id="build"' in s and "v9" in s
    assert "立即总结（13部门运行）" in s
    assert "只汇总已提交" in s
    assert "runAllDepartments" in s
    assert "openDetail" in s and "继续对话" in s
    assert 'id="detail"' in s
    assert "hq_dept_chat_" in s
    assert "hq_people.js" in s
    assert "云端汇报 " in s
    assert "部门研究题" in s


def test_special_is_local_draft_workspace():
    s = SPECIAL.read_text(encoding="utf-8")
    assert "hq/personal_special/v1" in s
    assert "生成专项卡" in s
    assert "不自动执行" in s


def test_hq_has_briefing_entry():
    s = HQ.read_text(encoding="utf-8")
    assert 'href="/briefing"' in s
    assert "早间简报" in s
