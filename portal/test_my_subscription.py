# -*- coding: utf-8 -*-
"""客户订阅面离线契约测试（飞书中台 P2.5 · FD-A 工位）。

nginx 之外的关键契约，全部读文件断言、零网络、零服务启动：

1.  my_subscription.html 存在且只读（无 POST / 无 form 提交）；
2.  页面含 degraded 降级区与只读声明（含页头「我的订阅 · 邓芝统辖 · 只读投影」）；
3.  页面引用 /api/subscription/status 端点，且全部 fetch 显式 GET；
4.  无 open_id 时有引导输入态文案；
5.  额度渲染含「不限额」诚实分支；
6.  portal_api.py 有 /api/subscription/status branch（open_id 必填缺失 400）；
7.  portal_api.py 有 /my-subscription 静态页 branch；
8.  api_common.py 情报变现组含「我的订阅」「订阅运营」两条 nav 条目；
9.  subscription_store.py 有 get_subscriber_summary（closed on failure 不抛）。
"""

import re
from pathlib import Path

PORTAL = Path(__file__).resolve().parent
REPO = PORTAL.parent

PAGE = PORTAL / "static" / "my_subscription.html"
API = PORTAL / "portal_api.py"
COMMON = PORTAL / "api_common.py"
STORE = PORTAL / "subscription_store.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def page_src() -> str:
    assert PAGE.exists(), f"缺少页面文件: {PAGE}"
    return _read(PAGE)


# ---------------------------------------------------------------------------
# 1. 页面存在且只读
# ---------------------------------------------------------------------------

def test_page_exists_and_readonly():
    src = page_src()
    assert "POST" not in src, "页面不得出现 POST（零写操作契约）"
    assert not re.search(r"<form\b", src, re.I), "页面不得含 form 提交"
    assert "method:'GET'" in src or 'method:"GET"' in src, \
        "所有 fetch 必须显式 GET"


def test_page_all_fetch_are_get():
    src = page_src()
    fetches = re.findall(r"fetch\(([^)]*)\)", src)
    assert fetches, "页面应有 fetch 调用"
    for f in fetches:
        assert "'GET'" in f or '"GET"' in f, f"fetch 非 GET: {f}"


# ---------------------------------------------------------------------------
# 2. 判例要素：degraded / 只读声明 / 页头
# ---------------------------------------------------------------------------

def test_page_has_degraded_and_readonly_banner():
    src = page_src()
    assert 'id="degraded"' in src, "缺 degraded 降级区"
    assert "数据拉取失败" in src, "degraded 区应有降级说明文案"
    assert "我的订阅 · 邓芝统辖 · 只读投影" in src, "页头只读声明缺失"
    assert "只读" in src and "写操作" in src


def test_page_uses_tokens_nav_and_refresh_pattern():
    src = page_src()
    assert "/static/tokens.css" in src
    assert "/static/nav.js" in src
    assert "setInterval(load,60000)" in src, "缺 60s 自动刷新"
    assert "AbortController" in src and "8000" in src, "缺 8s 超时"
    assert "credentials:'same-origin'" in src or \
        'credentials:"same-origin"' in src


# ---------------------------------------------------------------------------
# 3. 端点引用 / 引导态 / 额度渲染
# ---------------------------------------------------------------------------

def test_page_references_status_endpoint_and_guides_without_openid():
    src = page_src()
    assert "/api/subscription/status" in src, "页面必须引用订阅状态端点"
    assert "?open_id=" in src or "open_id=" in src
    assert "飞书内打开将自动带上你的身份" in src, "缺无 open_id 引导态文案"
    assert "M3 接 OAuth" in src


def test_page_quota_and_error_rendering():
    src = page_src()
    assert "不限额" in src, "额度卡应有不限额分支"
    assert "today_used_by_prefix" in src, "额度卡应读当日使用计数"
    assert "daily_quota_by_plan" in src
    # error 响应 closed-on-failure 原样透出（不吞错不造数）
    assert "d.error" in src, "页面应把 error 响应原样透出"


# ---------------------------------------------------------------------------
# 4. portal_api.py branch 契约
# ---------------------------------------------------------------------------

def test_portal_api_has_status_branch_with_400():
    src = _read(API)
    assert '/api/subscription/status' in src
    assert "missing open_id" in src, "status branch 缺 open_id 必填校验"
    assert "400" in src
    # 只读出口：branch 调 get_subscriber_summary，不调任何写函数
    m = re.search(
        r'path == "/api/subscription/status".*?(?=\n        elif path == )',
        src, re.S)
    assert m, "status branch 结构异常"
    block = m.group(0)
    assert "get_subscriber_summary" in block
    for banned in ("upsert_subscriber", "grant_subscription",
                   "expire_sweep", "entitlement_log"):
        assert banned not in block, f"status branch 不得调用写函数 {banned}"
    assert "POST" not in block, "status branch 应只处理 GET 语境"


def test_portal_api_has_my_subscription_branch():
    src = _read(API)
    assert 'path == "/my-subscription"' in src
    assert "my_subscription.html" in src, "branch 应指向静态页文件"


# ---------------------------------------------------------------------------
# 5. api_common.py nav 条目
# ---------------------------------------------------------------------------

def test_api_common_nav_entries():
    src = _read(COMMON)
    assert '("🔐 我的订阅", "/my-subscription", "mysub")' in src
    assert '("📊 订阅运营", "/sub-admin", "subadmin")' in src
    # 两条须落在 DOMAIN_NAV_GROUPS 的情报变现组内
    m = re.search(r'"💼 情报变现", \((.*?)\)\),\n    \),', src, re.S)
    assert m, "情报变现组结构异常"
    assert "mysub" in m.group(1) and "subadmin" in m.group(1)


# ---------------------------------------------------------------------------
# 6. subscription_store.py 摘要函数契约
# ---------------------------------------------------------------------------

def test_store_has_get_subscriber_summary_closed_on_failure():
    src = _read(STORE)
    assert "def get_subscriber_summary(open_id" in src
    m = re.search(r"def get_subscriber_summary\(.*?\n(?=def |\Z)", src, re.S)
    assert m, "get_subscriber_summary 结构异常"
    body = m.group(0)
    assert '"error"' in body or "'error'" in body, \
        "异常应返回 error 键（closed on failure 不抛）"
    assert "store_unavailable" in body, "读路径不建库：store 缺失应报 store_unavailable"
    # 纯只读：不做任何 INSERT/UPDATE/CREATE
    for banned in ("INSERT INTO", "UPDATE ", "CREATE TABLE"):
        assert banned not in body, f"摘要函数应纯只读，发现 {banned}"


def test_nav_js_not_required_to_update():
    """nav.js 由导出器生成，FD-A 不改——本例断言本次未手改其判定锚（记录性）。"""
    src = _read(COMMON)
    assert "DOMAIN_NAV_GROUPS" in src  # 导出器数据源在 api_common
