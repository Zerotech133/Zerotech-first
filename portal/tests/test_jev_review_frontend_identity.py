# -*- coding: utf-8 -*-
"""JEV 复核前端身份回归（K-ORG-20261101-H4HARDEN，审计 v2 M3 收尾）。

背景：M3（K-ORG-20261031-APIHARDEN）已把后端 resolve/reopen 改为"reviewer 取会话身份
（zb_session sub），body 自报他人 403 reviewer_mismatch"；但前端 `portal/static/
jev_review.html` 仍默认 reviewer="老田" 并随 body 提交，登录用户会话 sub 不一致时
会被 403。本单前端改为：

- 不再自报 reviewer（页面无 reviewer 输入框，POST body 不再带 reviewer）；
- 从 `/api/auth/me` 读取并显示当前会话身份；
- 未登录时显示登录入口并禁用裁决按钮（避免点了才 403）。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "static" / "jev_review.html"


def _html() -> str:
    assert PAGE.is_file(), "缺少 portal/static/jev_review.html"
    return PAGE.read_text(encoding="utf-8")


def test_page_has_no_self_reported_reviewer_input():
    html = _html()
    assert "f-reviewer" not in html, "页面不应再有 reviewer 输入框（reviewer 取会话身份）"
    assert 'value="老田"' not in html, "不得把 reviewer 默认成固定值"
    # 保留人工备注、裁决按钮与队列加载
    assert "备注（可空）" in html
    assert "/api/jev/review/queue" in html
    assert "/api/jev/review/resolve" in html


def test_page_reads_identity_from_auth_me():
    html = _html()
    assert "/api/auth/me" in html, "页面必须从 /api/auth/me 读取会话身份"
    assert "session" in html and "sub" in html, "应读取 me 返回的 session.sub"
    assert 'id="who"' in html, "缺少会话身份展示位"
    assert "会话身份" in html


def test_page_omits_reviewer_in_resolve_body():
    html = _html()
    # 定位 resolve() 里的 POST body 构造
    m = re.search(r'body:JSON\.stringify\(\{(.*?)\}\)', html, re.S)
    assert m, "找不到 resolve POST body 构造"
    body_fields = m.group(1)
    assert "reviewer" not in body_fields, \
        "POST body 不得再带 reviewer（后端口径：reviewer 取会话身份）: %r" % body_fields
    for field in ("id", "decision", "note"):
        assert field in body_fields, "resolve body 缺少字段: %s" % field


def test_page_requires_login_before_actions():
    html = _html()
    assert "/login" in html, "未登录时应提供登录入口"
    assert "if(!ME)" in html, "未登录（ME 为空）需显式分支"
    assert "b.disabled=true" in html, "未登录时裁决按钮必须禁用"
    assert "loadIdentity" in html and "await loadIdentity" in html, \
        "页面启动必须先加载会话身份，再加载队列"


def test_page_keeps_queue_render_and_pager():
    html = _html()
    for needle in ('id="f-status"', 'id="f-bias"', 'id="f-sort"',
                   'id="rows"', 'id="page"', 'id="refresh"'):
        assert needle in html, "队列筛选/渲染回归: %s" % needle
    assert "esc(" in html, "标题仍须 HTML 转义（防 XSS）"


def test_inline_script_syntax_if_node_available():
    """内联 JS 语法真检查（有 node 时）；无 node 则 skip（不替代浏览器 E2E）。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("host 无 node，跳过内联 JS 语法检查")
    m = re.search(r"<script>(.*)</script>", _html(), re.S)
    assert m, "缺少内联 <script>"
    td = tempfile.mkdtemp(prefix="jev-js-")
    try:
        js = Path(td) / "jev_review.js"
        js.write_text(m.group(1), encoding="utf-8")
        r = subprocess.run([node, "--check", str(js)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=60)
        assert r.returncode == 0, "JEV 内联 JS 语法错误:\n%s" % (r.stderr or r.stdout)
    finally:
        shutil.rmtree(td, ignore_errors=True)
