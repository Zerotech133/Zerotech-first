# -*- coding: utf-8 -*-
"""U1 /hq 驾驶舱体验静态断言（2026-11-03，DSH·前端体验）。

覆盖任务单 U1 验收点（全部只读 portal/hq.html 文本，不依赖登录/网络/服务）：
1. 首屏骨架屏（KPI + 表格占位，数据加载后由 JS 替换）；
2. 401 恢复：/login?next=/hq 登录 CTA + 重试按钮 + 需登录文案；
3. 顶部刷新按钮 + 30s 自动刷新开关（悬浮/后台/编辑中暂停、最后更新时间）；
4. 任务/议程客户端搜索 + 状态筛选 + 空结果提示（不新增 API）；
5. ARIA tabs（role=tablist/tab/tabpanel、aria-selected/controls/labelledby）+ 方向键；
6. 保留 8 tabs 原结构与唯一数据源 /api/hq/snapshot；
7. 统一 tokens 变量、移动端 tabs 横滑/表格横向滚动、reduced-motion/焦点可见。

约束守护：本文件只 statically 读 HTML；不 import portal_api、不启服务、不写库。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

HTML_PATH = Path(__file__).resolve().parent / "hq.html"
TAB_ORDER = ["roles", "agenda", "org", "tasks", "approvals", "commercial", "budget", "heartbeat"]
TAB_LABELS = ["五角色", "议程", "组织", "任务", "审批", "商业", "预算", "心跳"]


@pytest.fixture(scope="module")
def html() -> str:
    assert HTML_PATH.is_file(), "缺少 portal/hq.html"
    return HTML_PATH.read_text(encoding="utf-8")


def _api_paths(html: str) -> set:
    return set(re.findall(r"/api/[A-Za-z0-9_./-]+", html))


# ── 1. 首屏骨架屏 ──────────────────────────────────────────────
def test_first_paint_skeleton_for_kpi_and_tables(html):
    assert 'id="cards"' in html
    # 5 张 KPI 骨架卡 + 8 个面板各一段表格骨架
    assert html.count('<div class="card skel-card">') == 5
    assert html.count('<div class="skel-block"') == len(TAB_ORDER)
    assert "aria-busy=\"true\"" in html  # main 初始 busy，load 完成后置 false
    assert "@keyframes skel-shimmer" in html
    assert "prefers-reduced-motion" in html  # 骨架动画尊重减少动效
    assert "clearSkeleton" in html  # 失败/401 时骨架可清除，不残留假数据
    assert "renderAll" in html  # 数据到达后整体替换骨架


# ── 2. 401 登录 CTA + 重试 ─────────────────────────────────────
def test_401_recovery_login_cta_and_retry(html):
    assert 'href="/login?next=/hq"' in html  # 登录后回跳 /hq
    assert "去登录" in html
    assert 'id="login-cta"' in html
    assert 'id="retry-btn"' in html
    assert 'id="error-panel"' in html and 'role="alert"' in html
    assert "需登录" in html  # 兼容旧契约 test_api_hq.test_page_uses_authenticated_api_only
    assert re.search(r"res\.status\s*===\s*401", html)  # 401 单独识别为登录态问题
    assert "401" in html and "未登录" in html
    retry_block = html[html.index('id="retry-btn"'):]
    assert "addEventListener" in retry_block  # 重试按钮有接线


# ── 3. 刷新 + 30s 自动刷新 ────────────────────────────────────
def test_manual_refresh_and_auto_refresh_30s(html):
    assert 'id="refresh-btn"' in html
    assert "刷新中…" in html  # 加载中态可见
    assert re.search(r"AUTO_REFRESH_MS\s*=\s*30000", html)  # 30s 轮询
    assert "setInterval" in html and "clearInterval" in html
    assert 'id="auto-refresh"' in html and 'role="switch"' in html
    assert 'aria-checked="true"' in html  # 开关默认开启
    assert "自动刷新 30s" in html
    # 悬浮暂停 + 页面后台暂停 + 显示最后更新时间
    assert "mouseenter" in html and "mouseleave" in html
    assert "visibilityState" in html
    assert 'id="last-updated"' in html and "最后更新" in html
    assert 'id="load-status"' in html and 'aria-live="polite"' in html
    # 键盘快赢：/ 聚焦搜索（当前 tab 无搜索时切到任务）、r 手动刷新
    assert "e.key==='/'" in html and 'selectTab(\'tasks\',false)' in html
    assert "e.key==='r'" in html


# ── 4. 任务/议程搜索与状态筛选、空结果 ────────────────────────
def test_task_and_agenda_client_search_filter_and_empty_state(html):
    for needle in (
        'id="task-search"', 'id="task-status"', 'id="task-table"',
        'id="agenda-search"', 'id="agenda-status"', 'id="agenda-table"',
    ):
        assert needle in html, needle
    assert '<select id="task-status"' in html and "全部状态" in html
    assert '<select id="agenda-status"' in html
    assert "renderTasks" in html and "renderAgenda" in html
    assert "toLowerCase()" in html  # 客户端大小写无关过滤
    assert "没有匹配的任务" in html and "没有匹配的议程" in html  # 空结果提示
    assert "重置筛选" in html  # 空结果可一键清空筛选
    # 筛选完全客户端：全页只有 1 处 fetch（快照），没有搜索/筛选 API
    assert html.count("fetch(") == 1


# ── 5. ARIA tabs + 方向键 ─────────────────────────────────────
def test_tabs_aria_roles_and_keyboard(html):
    assert 'role="tablist"' in html
    tab_buttons = re.findall(r"<button[^>]*role=\"tab\"[^>]*>", html)
    assert len(tab_buttons) == len(TAB_ORDER) == 8
    assert html.count('role="tabpanel"') == len(TAB_ORDER)
    # aria-selected 只统计 8 个 tab 按钮（CSS 选择器 .tab[aria-selected="true"] 不计）
    assert sum(b.count('aria-selected="true"') for b in tab_buttons) == 1
    assert sum(b.count('aria-selected="false"') for b in tab_buttons) == len(TAB_ORDER) - 1
    for tab in TAB_ORDER:
        assert f'aria-controls="p-{tab}"' in html
        assert f'aria-labelledby="tab-{tab}"' in html
        assert f'id="p-{tab}"' in html
        assert f'id="tab-{tab}"' in html
    for key in ("ArrowRight", "ArrowLeft", "Home", "End"):
        assert f"e.key==='{key}'" in html  # 键盘左右/首尾切换
    assert 'tabindex="-1"' in html  # roving tabindex
    assert "focus-visible" in html and "--color-focus" in html  # 焦点可见


# ── 6. 保留 8 tabs 原结构 + 唯一数据源 ────────────────────────
def test_keeps_eight_tabs_and_single_snapshot_source(html):
    pairs = re.findall(r'data-tab="([a-z]+)"[^>]*>([^<]+)</button>', html)
    assert [p[0] for p in pairs] == TAB_ORDER
    assert [p[1] for p in pairs] == TAB_LABELS
    assert html.count("data-tab=") == len(TAB_ORDER)  # 只出现在 8 个 tab 按钮
    assert "'/api/hq/snapshot'" in html
    assert "/static/hq_snapshot.json" not in html  # 不再读公开静态快照
    assert _api_paths(html) == {"/api/hq/snapshot"}, _api_paths(html)  # 未新增 API


# ── 7. tokens / UI Kit / 移动端 / 只读安全 ────────────────────
def test_tokens_ui_kit_mobile_scroll_and_readonly(html):
    assert "/static/tokens.css" in html
    assert "/static/ui/ui.css" in html
    assert "/static/nav.js" in html
    assert html.count("var(--") >= 30  # 视觉统一走 tokens 变量
    assert "@media(max-width:640px)" in html  # 移动端断点
    assert "overflow-x:auto" in html  # tabs 横滑 + 表格横向滚动
    assert "ui-btn" in html  # 复用 UI Kit 按钮
    assert "cache:'no-store'" in html and "credentials:'same-origin'" in html
    assert "method:'POST'" not in html and "fetch(" in html  # 纯只读 GET
    assert "localStorage" not in html  # 不在客户端缓存快照
    assert 'role="status"' in html and "aria-live" in html  # 动态状态可被读屏播报
