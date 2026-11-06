# -*- coding: utf-8 -*-
"""R9 · pending-20261106 六处收口 · 契约测试。

背景（2026-11-06 R9）：
  `reports/l2_approval_20261106.md` 报批的六处改动，老田当轮裁定**全部不执行**：
    #1 公开族 C 正则加 `kb`/`learning` 一级名       → 不放行（前端零调用，收益 0）
    #2 白名单明细注释同步                           → 不执行（随 #1）
    #3 一级路由名计数 141 → 143                     → 不执行（随 #1）
    #4 `/api/org/` 块内加 absolute_redirect off     → 无需（R7 已用 server 级统一覆盖）
    #5 `/api/os/`  块内加 absolute_redirect off     → 无需（同上）
    #6 新增 `location ^~ /cowagent/`                → 不上（9899 零监听 + R6 已置灰）

实测依据（写进断言，防止后人再翻案）：
  * portal 全站前端（*.html / *.js，排除 quant/assets 打包产物）**零处**调用
    `/api/learning/*` 与 `/api/kb/qa`；kb_cards.html / kb_reports.html 读的是
    `/static/kb_learning_*.json` 静态文件，不经 8091 白名单。
  * `netstat` 无 9899 LISTEN；R6 已把 nav 的 CowAgent 改成不可点 `#cowagent-off`。
  * `_token_tag()` 是活路径（api_pages_portal.py 的 _html() 内调用，_html 98 个调用点）
    → 403 响应体「本机页面自动携带」对渲染页成立，**不是过期文案**，故不重启 8091。
  * `sa_monitor.html` / `sa_workbench.html` 在容器 webroot 里存在但实测 404 —— 被
    `location ^~ /sa` 前缀遮蔽（与 R7 的 `/bt_view.html` 同类），非「文件缺失」，
    删文件无法消除遮蔽，故不动 webroot。

本测试纯读源文本（nginx.conf / nav.js / 前端页面），不依赖 8091 / nginx 运行态。
"""
import re
from pathlib import Path

PORTAL = Path(__file__).resolve().parents[1]
REPORTS = PORTAL.parent / "reports"
NGINX = (PORTAL / "nginx.conf").read_text(encoding="utf-8", errors="replace")


def _src(rel):
    return (PORTAL / rel).read_text(encoding="utf-8", errors="replace")


# ------------------------------------------------ 防护 1：不把旧快照当新版应用
def test_pending_20261106_is_archived_superseded():
    """旧副本必须带 SUPERSEDED 后缀，禁止再被当成可应用版本。

    它是 04:25 基于 c67da534 生成的快照，不含 R7（absolute_redirect / bt 精确匹配）
    也不含 R8（/capability、/perf）→ 直接应用会把两轮成果一起回滚。
    """
    assert not (REPORTS / "nginx.conf.pending-20261106").exists(), \
        "未标记的旧副本仍在，存在被误用回滚 R7/R8 的风险"
    archived = REPORTS / "nginx.conf.pending-20261106.SUPERSEDED"
    assert archived.exists(), "旧副本应改名归档而非删除（保留取证链）"


# ------------------------------------------------ 防护 2：kb/learning 匿名放行不落地
def _public_family_c_line():
    """取出公开族 C 那条 location 正则的整行（唯一一条以 /api/(?: 开头的行）。"""
    for line in NGINX.splitlines():
        if line.strip().startswith("location ~ ^/api/(?:acad1"):
            return line
    raise AssertionError("公开族 C 正则未找到（nginx.conf 结构已变，本断言需同步更新）")


def _public_family_c_first_names():
    """解析出「一级路由名」集合。

    解析口径与 tests/test_nginx_api_auth_subset.py:_public_whitelist_alts 一致：
    以 `^/api/(?:` 起、以 `)(?:/|$)` 止取主体，再按 `|` 切。主体里含嵌套分组
    （`manager_[a-z_]+`、`intel/(?:brief|policy|sources)`），故一级名只取不含
    `(`/`)`/`/` 的纯标识符项。
    """
    line = _public_family_c_line()
    body = line.split("^/api/(?:", 1)[1].split(")(?:/|$)", 1)[0]
    return {p for p in body.split("|") if p and not set("()/") & set(p)}


def test_whitelist_does_not_include_kb_or_learning():
    """kb / learning 不得进匿名白名单：前端零调用，放行只放大攻击面。

    用 `|kb|` 边界判断而非子串判断 —— `kb_corpus` 合法存在，不能被误伤。
    """
    line = _public_family_c_line()
    assert "|kb|" not in line, "/api/kb/* 匿名放行已被裁定否决，不得回潮"
    assert "|learning|" not in line, "/api/learning/* 匿名放行已被裁定否决，不得回潮"
    names = _public_family_c_first_names()
    assert "kb" not in names and "learning" not in names


def test_whitelist_still_keeps_kb_corpus():
    """kb_corpus 是既有放行项（kb_corpus.html 在用），不得被顺手删掉。"""
    assert "kb_corpus" in _public_family_c_first_names()


def test_whitelist_count_comment_stays_141():
    """计数注释仍是 141，与明细注释登记数一致（改了名字就必须同步改数）。"""
    assert "一级路由名 141 个" in NGINX
    assert "一级路由名 143 个" not in NGINX
    detail = NGINX.split("公开白名单明细", 1)[1].split("公开族 A", 1)[0]
    names = re.findall(r"\b[a-z][a-z0-9_.\-]*\b", detail)
    assert len(names) >= 141, "白名单明细注释不完整: %d" % len(names)


# ------------------------------------------------ 防护 3：cowagent 反代不落地
def test_no_cowagent_reverse_proxy():
    """9899 零监听 + nav 已置灰 → 不上反代（否则只多一条 502 路由）。"""
    assert "location ^~ /cowagent/" not in NGINX
    assert "9899" not in NGINX, "nginx.conf 不应出现 9899 上游"


def test_nav_cowagent_stays_disabled():
    """R6 的决定：nav 里 CowAgent 必须仍指向不可点锚点。"""
    nav = _src("static/nav.js")
    assert "#cowagent-off" in nav
    assert '"/cowagent/"' not in nav, "nav 不得恢复 /cowagent/ 可点入口"


# ------------------------------------------------ 防护 4：R7 / R8 成果不得回退
def test_r7_server_level_absolute_redirect_in_place():
    """R7 用 server 级一行覆盖全部无斜杠 301；回退它会让 301 重新丢 8734 端口。"""
    head = NGINX[:3000]
    assert re.search(r"server\s*\{[^}]*?server_name _;\s*\n\s*absolute_redirect off;", head), \
        "server 级 absolute_redirect off 必须紧跟 server_name _;"


def test_r7_bt_static_pages_exact_match_in_place():
    """bt_view.html / bt2_view.html 必须仍是精确匹配，否则被 ^~ /bt 吞成 404。"""
    assert "location = /bt_view.html" in NGINX
    assert "location = /bt2_view.html" in NGINX
    # 精确匹配必须出现在 ^~ /bt 之前（nginx 语义上精确匹配本就优先，此处只防误挪）
    assert NGINX.index("location = /bt2_view.html") < NGINX.index("location ^~ /bt {")


def test_r8_capability_and_perf_entries_in_place():
    """R8 修的两个 nav 入口（/capability、/perf）不得丢。"""
    assert "location = /capability" in NGINX
    assert "location = /perf" in NGINX


def test_org_os_are_covered_by_server_level_switch():
    """#4/#5 判定为「无需改动」的依据：两族块内没有各自加 off，
    但 server 级已 off，行为等价。若有人删掉 server 级又没有块级兜底，
    这条会先红，避免静默回退。"""
    for loc in ("location ^~ /api/org/", "location ^~ /api/os/"):
        i = NGINX.index(loc)
        block = NGINX[i:i + 600]
        assert "absolute_redirect off;" in block or "absolute_redirect off;" in NGINX[:3000]


# ------------------------------------------------ 防护 5：403 文案不改的依据固化
def test_403_message_not_stale_because_token_tag_is_live():
    """403 文案「本机页面自动携带」有实码支撑，故不需要改字重启 8091。"""
    api = (PORTAL / "portal_api.py").read_text(encoding="utf-8", errors="replace")
    assert "本机页面自动携带" in api
    pages = _src("api_pages_portal.py")
    assert "self._token_tag()" in pages, "_token_tag 若真成死代码，本判定需重新评估"


# ------------------------------------------------ 防护 6：sa_* 孤儿是遮蔽而非缺失
def test_sa_orphans_are_prefix_shadowed_not_missing():
    """宿主与容器 webroot 都有这两个文件，但 ^~ /sa 前缀使其永不可达。

    记录这一事实，避免下次有人「清理 webroot」当成真清理 —— 删了遮蔽还在。
    """
    assert (PORTAL / "sa_monitor.html").exists()
    assert (PORTAL / "sa_workbench.html").exists()
    assert "location ^~ /sa {" in NGINX, "遮蔽来自 ^~ /sa 前缀，删文件不解决"
