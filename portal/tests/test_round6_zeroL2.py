# -*- coding: utf-8 -*-
"""R6 · 零 L2 四项（老田 2026-11-06 批准）· 契约测试。

纯读 portal 前端源文本 + 常量比对，不依赖浏览器 / 8091 / nginx 运行态。

对应本轮四件（顺序即执行顺序）：
  ③ academy.html 摘除 8096 误导链接 → 禁用态文本
  ⑤ bt2_view.html 补 X-Portal-Token 注入（写入端点 403 修复）
  ② nav 中 CowAgent 标灰不可点（数据层 # + 渲染层灰态 + CSS）
  ④ 10 个演示/原型页补非生产标注（data-nonprod 统一标记）

实测依据（写进断言，防止后人再改回去）：
  * 8096 根路径 200，返回 <title>军团作战大屏 · LEGION-OPS</title>，与「教材与进度」无关；
    且 href 用 127.0.0.1 对远程用户恒不可达 → 摘除。
  * GET /api/bt2_jobs、/api/bt2_job?id= 均 200；POST /api/bt2_run、/api/bt2_nl 均 403
    （8091 _MUTATION_POSTS 鉴权），页面原无任何 token 注入 → 补 bt2Post。
  * 9899 无 LISTEN；nginx /cowagent/ 反代仅存在于 pending 副本（原件未应用）→ 不可点。
  * portal_api.py 的 _token_tag() 只有定义、零调用点，故「本机页面自动携带 token」不成立。

守卫：本轮未动 quant.html 他人未提交改动（「多维加权」特征串仍在）。
"""
import re
import subprocess
from pathlib import Path

PORTAL = Path(__file__).resolve().parents[1]


def _wip_present(rel):
    """该 worktree 里 rel 是否存在未提交改动（被守卫的本地 WIP 是否存在）。

    R6 的 quant.html 守卫针对「他人未提交改动」——只存在于主工作区；分支/linked
    worktree 按定义 clean，无物可守。主区照常断言，分支 gate 自动跳过，避免假红。
    """
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--", rel],
            cwd=str(PORTAL), capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return True
    if out.returncode != 0:
        return True
    return bool(out.stdout.strip())


def _src(rel):
    return (PORTAL / rel).read_text(encoding="utf-8")


ACADEMY = _src("academy.html")
BT2 = _src("bt2_view.html")
API_COMMON = _src("api_common.py")
NAV_JS = _src("static/nav.js")
NAV_CSS = _src("static/nav.css")

NONPROD_PAGES = (
    "os_demo.html",
    "os_integrated_demo.html",
    "kb_reports.html",
    "d1_war_room.html",
    "zero_home.html",
    "zero_proto.html",
    "sandbox.html",
    "academy.html",
    "quant_v2_demo.html",
)
# 说明（2026-11-06 GATE-HOTFIX）：agent_board.html 是 .gitignore 的运行时生成页
# （ops/refresh_agent_board.ps1 产物，不入清单/不跟仓），clean checkout（分支 worktree/CI）
# 里不存在，无法作为静态契约测试对象；原 ten 页口径改为 9 个 tracked 页，
# 生成页的非生产标注由生成器/运维承接，不在此静态断言。


# ------------------------------------------------------ ③ academy 摘除 8096
def test_academy_8096_href_fully_removed():
    assert 'href="http://127.0.0.1:8096/"' not in ACADEMY
    # 允许注释里保留溯源说明，但不得有可点击的 8096 入口
    assert not re.search(r'<a[^>]*8096', ACADEMY), "不得残留指向 8096 的 <a>"


def test_academy_disabled_label_visible():
    assert "教材与进度 · 未开放" in ACADEMY
    assert "教材与进度 · 已接通" not in ACADEMY, "旧文案「已接通」必须去掉"
    # 禁用态：不是 <a>，且带 aria-disabled 与解释性 title
    seg = ACADEMY.split("教材与进度 · 未开放")[0][-400:]
    assert "aria-disabled=\"true\"" in seg
    assert "军团作战大屏" in seg, "title 必须写明真实指向，便于溯源"


def test_academy_no_legion_api_switch():
    """/legion-api/ 需 X-Portal-Token 且语义仍错，不得作为替代方案。"""
    assert "/legion-api/" not in re.sub(r"<!--.*?-->", "", ACADEMY, flags=re.S)
    assert "X-Portal-Token" not in re.sub(r"<!--.*?-->", "", ACADEMY, flags=re.S)


def test_academy_disposition_comment_records_evidence():
    line = [ln for ln in ACADEMY.splitlines() if "D3 已处置" in ln]
    assert len(line) == 1
    assert "D3 待办" not in ACADEMY, "R5 待办注释必须已被替换，不得两套口径并存"
    assert "8096" in line[0] and "军团作战大屏" in line[0]


# ------------------------------------------------- ⑤ bt2_view token 注入
def test_bt2_token_helpers_defined_once():
    assert BT2.count("function bt2Headers(") == 1
    assert BT2.count("function promptBt2Token(") == 1
    assert BT2.count("async function bt2Post(") == 1
    assert BT2.count("function bt2Err(") == 1


def test_bt2_headers_read_session_storage_only():
    """token 只落 sessionStorage，不落 localStorage / cookie / 源码常量。"""
    assert 'sessionStorage.getItem("bt2Token")' in BT2
    assert 'sessionStorage.setItem("bt2Token",t)' in BT2
    assert 'localStorage.setItem("bt2Token"' not in BT2
    assert "document.cookie" not in BT2


def test_bt2_post_retries_once_on_403():
    assert "res.status===403" in BT2
    assert BT2.count('h["X-Portal-Token"]=t') == 1, "header 只在 bt2Headers 里赋值一次"
    # 403 后只重试一次：bt2Post 内 fetch 恰好两次
    body = BT2.split("async function bt2Post(")[1][:600]
    assert body.count("await fetch(") == 2


def test_bt2_two_write_endpoints_use_helper():
    assert BT2.count('bt2Post("/api/bt2_run"') == 1
    assert BT2.count('bt2Post("/api/bt2_nl"') == 1
    # 不得再有裸写 POST（method:"POST" 只允许出现在 bt2Post 内部）
    assert BT2.count('method:"POST"') == 1


def test_bt2_read_endpoints_untouched():
    """GET 两个端点本就 200，不得改成 POST-only 之外的形态。"""
    assert 'fetch("/api/bt2_jobs")' in BT2
    assert 'fetch("/api/bt2_job?id="' in BT2
    for p in ("/api/bt2_run", "/api/bt2_nl", "/api/bt2_jobs", "/api/bt2_job"):
        assert p in BT2, p


def test_bt2_forbidden_message_not_raw_dumped():
    """403 的长段 forbidden 文案不得直接糊进 UI，须经 bt2Err 归一。"""
    assert "bt2Err(r," in BT2
    assert BT2.count('r.error||"提交失败"') == 0
    assert BT2.count('r.error||"解析失败"') == 0


# ------------------------------------------------------ ② CowAgent 标灰
def test_cowagent_nav_href_is_disabled_anchor():
    m = re.search(r'\("🐴 CowAgent 🚧", "([^"]+)"', API_COMMON)
    assert m, "CowAgent 入口必须带 🚧 标记"
    assert m.group(1).startswith("#"), "href 必须以 # 开头，渲染层据此输出灰态"


def test_cowagent_not_pointing_to_dead_route():
    assert '"/cowagent/"' not in API_COMMON
    assert "/cowagent/" not in NAV_JS.split("NAV=")[1][:4000].replace('"cowagent-off"', "")


def test_nav_renderer_has_off_branch():
    assert "href.startswith(\"#\")" in API_COMMON, "pill_nav 的 _grp 须有灰态分支"
    assert "wb-nav-off" in NAV_JS
    assert "it[1].charAt(0)==='#'" in NAV_JS
    assert ".wb-nav-off" in NAV_CSS


def test_nav_export_still_complete():
    """导出器重跑后入口不丢（cowagent 仍是入口，只是不可点）。"""
    assert '"#cowagent-off"' in NAV_JS
    assert '"cowagent"' in NAV_JS
    assert NAV_JS.count("wb-nav-link") >= 1, "正常入口渲染器不得被破坏"


# ------------------------------------------------ ④ 非生产标注统一
def test_nonprod_pages_have_marker():
    missing = [p for p in NONPROD_PAGES if 'data-nonprod="1"' not in _src(p)]
    assert not missing, "缺非生产标记：%s" % missing


def test_nonprod_marker_has_visible_text():
    for p in NONPROD_PAGES:
        txt = _src(p)
        seg = txt.split('data-nonprod="1"')[1][:400]
        assert "非生产" in seg, "%s 的标记缺少可见「非生产」文案" % p


def test_nonprod_marker_is_idempotent():
    for p in NONPROD_PAGES:
        assert _src(p).count('data-nonprod="1"') == 1, "%s 标记重复" % p


def test_nonprod_badge_is_fixed_and_click_through():
    """页尾角标用 fixed 定位，不参与文档流；pointer-events:none 不吃点击。"""
    txt = _src("os_demo.html")
    seg = txt.split('data-nonprod="1"')[1][:300]
    assert "position:fixed" in seg
    assert "pointer-events:none" in seg


# ------------------------------------------------------------------ 守卫
def test_guard_quant_untouched():
    if not _wip_present("quant.html"):
        return  # 分支/linked worktree 无此本地 WIP，跳过（主区照常断言）
    assert "多维加权" in _src("quant.html")


def test_guard_no_new_l2_dependency():
    """本轮四件全部纯前端 / 纯生成器，不得出现新的 nginx 依赖。"""
    for rel in ("academy.html", "bt2_view.html"):
        txt = _src(rel)
        assert "auth_request" not in txt
