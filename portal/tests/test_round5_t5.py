# -*- coding: utf-8 -*-
"""R5-C · T5 剩余 6 类清理项 · 契约测试（2026-11-06）。

纯读 portal 前端源文本 + 一个 AST 比对，不依赖浏览器 / 8091 / nginx 运行态，
与 test_round2_cleanup.py 同风格（模块级 SRC + 纯 assert）。

覆盖 impl_round5_contract_20261106.md §4.2 的六项：
  D2 ide.html 8796 → /openhands（+「需登录后使用」提示）
  D3 legion.html 8096 → /legion-api（有 token 注入 ⇒ 已换）
     academy.html 8096 保留（无 token 注入 ⇒ 按条件不换，只留待办注释）
  D4 dev.html localhost:3000 → /probe/webui（只换纯根入口）
  D7 api_common.py 9899 → /cowagent/ 且 nav.js 由导出器重生成
  E1 index.html /screen.html→/screen、/audit.html→/audit + FAV_ALIAS 迁移
  F1 academy.html「非生产 / 评审稿」标注（只标注，未摘任何入口）

末尾守卫断言证明本轮未碰他人未提交改动（quant.html / quant_v2_demo.html /
static/dw_snapshot.json）。

口径约定：
  * D3 的「8096 归零」只对 legion.html 成立；academy.html 按 R5 契约条件**当时**
    保留 8096 直连，故断言方向相反（测试表达真实决策，不写死成「一定换」）。
    **[R6 2026-11-06 更正]** 实测 8096 根路径返回「军团作战大屏 LEGION-OPS」，
    与「教材与进度」语义无关，且 127.0.0.1 对远程用户恒不可达 → 老田批准摘链接。
    本文件的 D3/F1 两处 academy 断言已随 R6 翻转为「已摘除」，R6 口径见
    test_round6_zeroL2.py；保留此注记以便回溯「为什么 R5 保留、R6 摘除」。
  * E1「恰好 1 处 /screen.html」= FAV_ALIAS 的旧 key（迁移用，必须保留）。
"""
import ast
import json
import re
from pathlib import Path

PORTAL = Path(__file__).resolve().parents[1]


def _src(rel):
    return (PORTAL / rel).read_text(encoding="utf-8")


def _raw(rel):
    return (PORTAL / rel).read_bytes()


IDE = _src("ide.html")
LEGION = _src("legion.html")
ACADEMY = _src("academy.html")
DEV = _src("dev.html")
INDEX = _src("index.html")
API_COMMON = _src("api_common.py")
NAV_JS = _src("static/nav.js")
QUANT = _src("quant.html")
QUANT_V2 = _src("quant_v2_demo.html")

# 本轮改过的 6 个文本产物（含导出器产物），用于换行风格复核
TOUCHED = ("ide.html", "legion.html", "academy.html", "dev.html",
           "index.html", "api_common.py", "static/nav.js")


# ------------------------------------------------------------ 0. 源文件真实性
def test_source_files_real():
    for rel in TOUCHED + ("quant.html", "quant_v2_demo.html"):
        p = PORTAL / rel
        assert p.is_file() and p.stat().st_size > 1000, rel


def test_touched_files_keep_crlf():
    """R5 §0.7：改前改后换行风格必须一致（原均为 CRLF、无 BOM）。"""
    for rel in TOUCHED:
        b = _raw(rel)
        assert b[:3] != b"\xef\xbb\xbf", "%s 不得带 BOM" % rel
        assert b.count(b"\n") == b.count(b"\r\n"), "%s 出现裸 LF，换行被破坏" % rel


# ------------------------------------------------------------------- D2 ide.html
def test_d2_ide_8796_gone():
    assert "8796" not in IDE
    assert "http://127.0.0.1:8796" not in IDE


def test_d2_ide_openhands_sameorigin():
    # 9 处 URL + 1 处失败提示文案，全部走同源 /openhands
    assert IDE.count("/openhands") >= 9
    for path in ("/openhands/api/stt",
                 "/openhands/api/mcp/tushare/tools",
                 "/openhands/api/mcp/tushare/call",
                 "/openhands/api/keys/state",
                 "/openhands/api/models/status",
                 "/openhands/api/ide/fs/",
                 "/openhands/v1/chat/completions"):
        assert path in IDE, path


def test_d2_ide_has_visible_login_hint():
    """/openhands/ 带 auth_request，必须给用户可见提示。"""
    assert "需登录后使用" in IDE
    # 提示必须是可见元素（span/div），不能只写在注释里
    line = [ln for ln in IDE.splitlines() if "需登录后使用" in ln]
    assert len(line) == 1, "提示应唯一，实际 %d 行" % len(line)
    assert "<span" in line[0] and "<!--" not in line[0]


# ---------------------------------------------------- D3 legion.html（已换同源）
def test_d3_legion_8096_gone_and_sameorigin():
    assert "8096" not in LEGION
    assert "http://127.0.0.1:8096" not in LEGION
    assert LEGION.count("/legion-api") >= 5
    # 兜底分支也必须是同源绝对路径（已无跨端口直连）
    assert 'API = "/legion-api/api/legion/ops"' in LEGION
    assert 'API2 = "/legion-api"' in LEGION


def test_d3_legion_keeps_token_injection():
    """换同源的前提：保留 X-Portal-Token 注入逻辑，否则 /legion-api/ 必 403。"""
    assert 'h["X-Portal-Token"] = t' in LEGION
    assert 'sessionStorage.getItem("legionToken")' in LEGION


# ------------------------------------------- D3 academy.html（R6 已摘除链接）
def test_d3_academy_8096_href_removed_after_r6():
    """R6：8096 实为军团大屏 ≠ 教材页，href 已摘为禁用态文本。

    R5 时因「无 token 注入」保留直连；R6 实测语义错位 + 127.0.0.1 对远程不可达，
    老田批准摘除。本断言随之翻转（原 test_d3_academy_8096_kept_on_purpose 已废弃）。
    """
    assert not re.search(r"h\[\"X-Portal-Token\"\]", ACADEMY), "academy 不应有 token 注入"
    assert ACADEMY.count('href="http://127.0.0.1:8096/"') == 0, "8096 链接必须已摘除"
    assert "教材与进度 · 未开放" in ACADEMY
    assert "/legion-api/" not in re.sub(r"<!--.*?-->", "", ACADEMY, flags=re.S), \
        "academy 不得出现真实 /legion-api 调用（注释里提到属正常）"


def test_d3_academy_has_disposition_comment():
    """R6 把 R5 的「D3 待办」注释改为「D3 已处置」，须写明实测依据。"""
    line = [ln for ln in ACADEMY.splitlines() if "D3 已处置" in ln]
    assert len(line) == 1
    assert line[0].strip().startswith("<!--") and line[0].strip().endswith("-->")
    assert "8096" in line[0] and "军团作战大屏" in line[0]
    assert "127.0.0.1" in line[0]
    # R5 的待办注释必须已不存在（避免两套口径并存）
    assert "D3 待办" not in ACADEMY


# ------------------------------------------------------------- D4 dev.html
def test_d4_dev_ports_gone():
    for port in ("localhost:3000", "localhost:4100", "localhost:8888",
                 "127.0.0.1:3000", "127.0.0.1:4100", "127.0.0.1:8888"):
        assert port not in DEV, port


def test_d4_dev_probe_root_only():
    """只允许精确入口 /probe/webui；任何 /probe/xxx 子路径都会命中 ^~ 兜底 502。"""
    assert 'href="/probe/webui"' in DEV
    hits = re.findall(r"/probe/[A-Za-z0-9_-]+", DEV)
    assert hits and set(hits) == {"/probe/webui"}, hits
    # 探测逻辑与入口链接同源一致（原 fetch 已是 /probe/webui，未改）
    assert DEV.count("/probe/webui") == 3


def test_d4_dev_no_probe_subpath_links():
    """未换项复核：dev.html 本轮不存在带子路径的 3000/4100/8888 链接（无保留项）。"""
    assert "localhost" not in DEV
    # 4100 / 8888 只在 quant*.html 出现，本轮按契约跳过（见守卫断言）


# ------------------------------------------------------------- D7 nav 单一源
def test_d7_api_common_9899_gone():
    assert "9899" not in API_COMMON
    # [R6 2026-11-06 更正] 9899 实测无 LISTEN、nginx /cowagent/ 反代仅在 pending 副本，
    # 入口从「裸端口」改为「不可点灰态」（href 以 # 开头），不再指向 /cowagent/。
    assert '"#cowagent-off"' in API_COMMON
    assert "未启用" in API_COMMON


def test_d7_nav_js_regenerated():
    assert "9899" not in NAV_JS
    assert NAV_JS.count('"#cowagent-off"') == 1
    assert "自动生成：tools/export_nav.py" in NAV_JS, "nav.js 必须是导出器产物"


def test_d7_nav_js_matches_ast_source():
    """nav.js 的 NAV 必须与 api_common.py 的 AST 字面量逐项一致（防手改/防漂移）。"""
    tree = ast.parse(API_COMMON)
    lit = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in ("DOMAIN_NAV_GROUPS", "SECTIONS"):
                    lit[t.id] = ast.literal_eval(node.value)
    groups, sections = lit["DOMAIN_NAV_GROUPS"], lit["SECTIONS"]
    expect = {
        "sections": [[k, lb, h] for k, lb, h in sections],
        "groups": {k: [[glb, [[lb, href, key] for lb, href, key in its]]
                       for glb, its in its_groups]
                   for k, its_groups in groups.items()},
    }
    m = re.search(r"var NAV=(\{.*?\});\r?\nwindow\.__WB_NAV=NAV;", NAV_JS, re.S)
    assert m, "nav.js 里找不到 NAV 字面量"
    assert json.loads(m.group(1)) == expect


# ------------------------------------------------------------------ E1 index
def test_e1_old_keys_only_in_alias():
    """/screen.html 与 /audit.html 各恰好 1 处，且都只能出现在 FAV_ALIAS 里。"""
    assert INDEX.count("/screen.html") == 1
    assert INDEX.count("/audit.html") == 1
    alias_line = [ln for ln in INDEX.splitlines() if "const FAV_ALIAS" in ln]
    assert len(alias_line) == 1
    assert "'/screen.html': '/screen'" in alias_line[0]
    assert "'/audit.html': '/audit'" in alias_line[0]
    # 旧 key 不得出现在别名行以外
    for ln in INDEX.splitlines():
        if "const FAV_ALIAS" in ln:
            continue
        assert "/screen.html" not in ln, ln[:120]
        assert "/audit.html" not in ln, ln[:120]


def test_e1_new_paths_in_groups_and_defaults():
    assert "'数据大屏', '/screen'" in INDEX
    assert "'审计状态', '/audit'" in INDEX
    assert "return ['/screen'].concat(FAV_DEFAULT);" in INDEX
    assert "if (!out.includes('/screen')) { out.unshift('/screen'); favSave(out); }" in INDEX
    assert "'/audit.html': '/audit'" in INDEX  # 别名保留（迁移证据）


def test_e1_alias_before_dedupe():
    """favLoad 里必须先 FAV_ALIAS 归一、再去重落库，否则旧收藏会重复/失配。"""
    body = INDEX[INDEX.index("function favList()"):INDEX.index("function favSave(")]
    i_alias = body.index("h = FAV_ALIAS[h] || h")
    i_dedupe = body.index("if (!out.includes(h))")
    assert i_alias < i_dedupe
    # FAV_ALIAS 必须在 favList 之前定义
    assert INDEX.index("const FAV_ALIAS") < INDEX.index("function favList()")


def test_e1_new_paths_are_real_routes():
    """新路径必须是 nginx 已有的短路由（= /screen、= /audit），否则链接即 404。"""
    nginx = _src("nginx.conf")
    assert "location = /screen " in nginx
    assert "location = /audit " in nginx


# --------------------------------------------------------------- F1 非生产标注
def test_f1_academy_marked_nonproduction():
    lines = [ln for ln in ACADEMY.splitlines() if "非生产" in ln and "评审稿" in ln]
    assert len(lines) >= 1
    visible = [ln for ln in lines if "<div" in ln and "<!--" not in ln]
    assert visible, "必须有页内可见小字，不能只写注释"
    assert any("<!--" in ln and "非生产" in ln for ln in ACADEMY.splitlines())


def test_f1_no_nav_entry_removed():
    """F1 只标注：任何入口链接都不得被摘（摘导航需老田单独裁定）。

    [R6 2026-11-06] 唯一例外：hero「教材与进度」8096 链接经老田批准摘除
    （实测指向军团大屏、语义错位），见 test_round6_zeroL2.py。其余入口必须原样。
    """
    assert ACADEMY.count('href="http://127.0.0.1:8096/"') == 0   # R6 已摘（唯一例外）
    assert 'data-nonprod="1"' in ACADEMY, "非生产标注须带统一标记"
    for key in ("data-view=\"home\"", "data-view=\"teaching\"",
                "data-view=\"market\"", "ac-btn major"):
        assert key in ACADEMY, key


# --------------------------------------------------- 守卫：他人未提交改动未动
def test_guard_quant_files_untouched():
    """quant.html / quant_v2_demo.html 是他人「共振→多维加权」未提交改动，特征串必须仍在。"""
    assert "多维加权" in QUANT
    assert "多维加权" in QUANT_V2


def test_guard_dw_snapshot_v39_untouched():
    snap = _src("static/dw_snapshot.json")
    assert '"version": 39' in snap


def test_guard_no_direct_port_left_in_touched_pages():
    """本轮 6 类清理后，被改页面不得残留指向本机的裸端口直连。"""
    for name, txt in (("ide.html", IDE), ("legion.html", LEGION), ("dev.html", DEV)):
        assert not re.search(r"(?:127\.0\.0\.1|localhost):\d{2,5}", txt), name
