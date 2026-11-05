# -*- coding: utf-8 -*-
"""R4-C · P1-D 两页示范接入 · catalog 双通道静态契约测试（不依赖浏览器 / 服务 / 网络）。

依据：`reports/impl_round4_contract_20261106.md` §6（本轮唯一 spec）、
`reports/p1d_rollout_20261106.md` §5、`reports/browser_review_20261106.md` §3（两个展示层缺陷）。

本轮两页（`portal/bt_view.html`、`portal/news_intel.html`）按 R2-A / R3-C 统一双通道模式接入：
实时优先 + 失败静默回落、3s 硬超时、258 表只读目录 + 单表 schema/样本弹窗。
本文件只做源码文本静态断言 + 内联 `<script>` 的 `node --check`，不启浏览器、不请求 HTTP。

风格对齐 `portal/tests/test_round2_p1c.py`、`test_round4_browser_fixes.py`：
pytest 直接收集的纯 assert 函数，按 `PAGES` 双页循环。

约定：「无硬编码端口」只针对**本轮新增片段**（`R4-C CATALOG BEGIN/END` 配对切片），
正则口径沿用上轮 `(https?://)?(127\\.0\\.0\\.1|localhost):\\d{2,5}`；
提示语里的「待 8091 自然重启」是运维进程说明而非 URL，不命中该正则。

★ 重点防守 `browser_review_20261106.md` §3 缺陷 2：R3-C 在 lineage 上让
「实时通道未就绪」提示在实时成功后仍可见（r3cSetState 只改状态字、没碰 notice）。
本轮两页必须存在按状态隐藏 / 恢复该提示的代码分支。
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile

PORTAL = Path(__file__).resolve().parents[1]
PAGES = {
    "bt_view.html": (PORTAL / "bt_view.html").read_text(encoding="utf-8"),
    "news_intel.html": (PORTAL / "news_intel.html").read_text(encoding="utf-8"),
}

BEGIN = "R4-C CATALOG BEGIN"
END = "R4-C CATALOG END"
PORT_RE = re.compile(r"(https?://)?(127\.0\.0\.1|localhost):\d{2,5}")
DEPRECATED = "注意：limit 已截断"          # 修复后口径严禁复用
FAIL_NOTE = "实时通道未就绪（待 8091 自然重启）"


def added(src):
    """按 BEGIN/END 配对提取本轮新增片段（两页各一对，全部新增代码都在其中）。"""
    a = src.index(BEGIN)
    b = src.index(END, a)
    assert b > a, "BEGIN 之后找不到 END"
    return src[a:b + len(END)]


# ---------------------------------------------------------------------------
# 1. 源文件真实性与新增标记
# ---------------------------------------------------------------------------

def test_source_files_real():
    """防读到空文件 / 错文件（否则后续断言会退化成恒真）。"""
    for name, src in PAGES.items():
        assert src.lstrip().lower().startswith("<!doctype html>"), name
        assert src.rstrip().endswith("</html>"), name
        # 体积用原始字节复核（中文 UTF-8 占 3 字节，len() 是字符数会低估）
        assert len(src.encode("utf-8")) > 18000, name
        assert src.count(BEGIN) == 1, name + " BEGIN 标记应恰好一对"
        assert src.count(END) == 1, name + " END 标记应恰好一对"


def test_added_fragment_r4c_namespace_only():
    """新增代码用 r4c- 新命名空间，未混入 r2a- / r3c- 旧命名空间。"""
    for name, src in PAGES.items():
        frag = added(src)
        assert "r4c-" in frag, name
        assert "r2a-" not in frag, name + " 混入了 R2-A 命名空间"
        assert "r3c-" not in frag, name + " 混入了 R3-C 命名空间"


# ---------------------------------------------------------------------------
# 2. 端点与同源相对路径
# ---------------------------------------------------------------------------

def test_both_catalog_endpoints_present():
    """两页都含 tables 与 table 两个端点，同源相对、带 URI 编码与 limit=20。"""
    for name, src in PAGES.items():
        assert src.count("/api/data/catalog/tables?detail=brief") == 1, name
        assert src.count("/api/data/catalog/table?name=") == 1, name
        assert "encodeURIComponent(name)" in src, name
        assert "&limit=20" in src, name
        assert "fetch('/api/data/catalog/tables?detail=brief',{cache:'no-store',signal:r4cTimeoutSignal()})" in src, name
        assert "fetch('/api/data/catalog/table?name='+encodeURIComponent(name)+'&limit=20'" in src, name


def test_added_fragment_no_hardcoded_port():
    """新增片段无硬编码端口；8091 仅作运维提示文案，不得出现在请求地址里。"""
    for name, src in PAGES.items():
        frag = added(src)
        assert not PORT_RE.search(frag), name + " 新增片段出现硬编码端口"
        assert "127.0.0.1" not in frag and "localhost" not in frag, name
        assert FAIL_NOTE in frag, name + " 8091 仅作运维提示文案"
        for ln in frag.splitlines():
            if "8091" in ln:
                assert "fetch(" not in ln and "://" not in ln and ":8091" not in ln, name


def test_added_fragment_all_same_origin():
    """新增片段无外部绝对 URL / CDN，全部同源相对路径。"""
    for name, src in PAGES.items():
        assert not re.search(r"https?://", added(src)), name + " 新增片段出现外部 URL"


# ---------------------------------------------------------------------------
# 3. 双通道状态机（try/catch、3s 超时、双标记、失败提示语）
# ---------------------------------------------------------------------------

def test_dual_channel_state_machine():
    """unknown / live / snapshot 三态齐全；成功判据 ok && total>0 才置 live。"""
    for name, src in PAGES.items():
        frag = added(src)
        assert "var R4C_LIVE=null, R4C_STATE='unknown';" in frag, name
        assert "R4C_STATE='live'" in frag and "R4C_STATE='snapshot'" in frag, name
        assert "live.ok!==true" in frag and "live.total>0" in frag, name
        assert "R4C_LIVE=live; R4C_STATE='live';" in frag, name
        assert "R4C_STATE='snapshot'; R4C_LIVE=null;" in frag, name


def test_try_catch_timeout_and_no_noise():
    """3s 硬超时（AbortSignal.timeout 优先 + AbortController 兜底），两路请求都挂 signal，
    失败静默：不 console.error、不 alert、不抛未捕获异常。"""
    for name, src in PAGES.items():
        frag = added(src)
        assert "try{" in frag and "}catch(e){" in frag, name + " 缺少 try/catch"
        assert "AbortSignal.timeout(3000)" in frag, name
        assert "typeof AbortController!=='undefined'" in frag, name + " 缺老浏览器兜底"
        assert frag.count("signal:r4cTimeoutSignal()") == 2, name + " 两个实时通道都应带 3s 超时"
        assert "if(!r.ok) throw new Error('HTTP '+r.status);" in frag, name
        assert "console.error(" not in frag, name + " 失败不得 console.error 刷屏"
        assert "alert(" not in frag, name + " 失败不得弹 alert"
        assert "静默" in frag, name + " 缺少静默回落语义"


def test_live_and_snapshot_flags_and_fail_note():
    """双标记「catalog 实时」「catalog 静态快照」都在；失败提示语文本存在。"""
    for name, src in PAGES.items():
        frag = added(src)
        assert "catalog 实时" in frag and "catalog 静态快照" in frag, name
        assert "r4cSetState('catalog 实时');" in frag, name
        assert "r4cSetState('catalog 静态快照');" in frag, name
        assert FAIL_NOTE in frag, name


# ---------------------------------------------------------------------------
# 4. ★ 缺陷 2 防守：「实时通道未就绪」按状态隐藏 / 恢复
# ---------------------------------------------------------------------------

def test_notice_hidden_on_live_restored_on_failure():
    """实时成功隐藏提示、失败恢复显示（r4c-hidden 类 + style.display 双分支）。

    browser_review §3 缺陷 2：R3-C 的 r3cSetState 只改状态字、没碰 notice，
    导致 lineage 实时成功后黄色「实时通道未就绪」仍可见。本轮两页必须有按状态
    add/remove('r4c-hidden') 与 style.display 的隐藏/恢复代码。
    """
    for name, src in PAGES.items():
        frag = added(src)
        marker = "function r4cSetState(t)"
        assert frag.count(marker) == 1, name + " r4cSetState 应唯一"
        body = frag[frag.index(marker): frag.index(marker) + 420]
        assert "r4c-notice" in body, name + " 状态函数未引用 notice 元素"
        assert "n.classList.add('r4c-hidden')" in body, name + " 实时成功未隐藏提示"
        assert "n.classList.remove('r4c-hidden')" in body, name + " 失败未恢复提示"
        assert "n.style.display='none'" in body, name + " 缺 style.display 隐藏分支"
        assert "n.style.display=''" in body, name + " 缺 style.display 恢复分支"
        assert "catalog 实时" in body, name
        # renderTableList 里同样按状态隐藏 / 恢复（双保险）
        assert frag.count("notice.classList.add('r4c-hidden')") >= 1, name
        assert frag.count("notice.classList.remove('r4c-hidden')") >= 1, name
        assert FAIL_NOTE in frag, name


# ---------------------------------------------------------------------------
# 5. 弹窗四要素与修复后文案（守 browser_review §3 缺陷 1）
# ---------------------------------------------------------------------------

def test_popup_shows_sample_note_freshness_clamped_limit_applied():
    """弹窗展示 sample_note / meta.freshness / clamped / limit_applied + schema/主键/前 20 行。"""
    for name, src in PAGES.items():
        frag = added(src)
        assert "d.sample_note" in frag and "esc(d.sample_note)" in frag, name
        assert "meta.freshness" in frag, name
        assert "d.clamped" in frag and "d.limit_applied" in frag, name
        assert "clamped=" in frag and "limit_applied=" in frag, name
        assert "meta.schema" in frag and "primary_key" in frag, name
        assert "rows.slice(0,20)" in frag, name


def test_fixed_caliber_wording_and_no_deprecated_wording():
    """修复后口径：「样本说明：… clamped=<bool>。仅返回样本行，不代表全表数据。」；
    仅 clamped===true 才补「已夹至 N 行」；严禁复用废弃的「注意：limit 已截断」。"""
    for name, src in PAGES.items():
        frag = added(src)
        assert "样本说明：" in frag, name
        assert "仅返回样本行，不代表全表数据。" in frag, name
        assert "esc(!!d.clamped)" in frag, name
        assert "d.clamped===true?'请求的 limit 超过上限，已夹至 '" in frag, name
        assert "已夹至 " in frag, name
        assert DEPRECATED not in src, name + " 复用已废弃文案"
        assert DEPRECATED not in frag, name + " 新增片段复用已废弃文案"


def test_esc_escaping_in_added():
    """新增片段有 esc( 转义调用；来自 API 的动态字符串全部经 esc() 防 XSS。"""
    for name, src in PAGES.items():
        frag = added(src)
        assert "esc(" in src, name
        assert frag.count("esc(") >= 10, name + " 新增片段 esc 调用过少"
        assert "esc(d.sample_note)" in frag, name
        assert "esc(d.limit_applied==null?'—':d.limit_applied)" in frag, name
        assert "esc(!!d.clamped)" in frag, name
        assert "esc(name)" in frag and "esc(it.source" in frag, name


# ---------------------------------------------------------------------------
# 6. 目录区块（可折叠 / 搜索 / by_source 动态源下拉）
# ---------------------------------------------------------------------------

def test_collapsible_block_and_source_dropdown():
    """可折叠只读区块 + 关键词搜索 + 由 by_source 动态生成的源下拉 + 258 表目录。"""
    for name, src in PAGES.items():
        frag = added(src)
        assert "createElement('details')" in frag and "box.open=true" in frag, name + " 区块不可折叠"
        assert 'id="r4c-search"' in frag and 'id="r4c-source"' in frag, name
        assert 'id="r4c-body"' in frag and 'id="r4c-wrap"' in frag, name
        assert "Object.keys(live.by_source||{}).sort()" in frag, name + " 源下拉未由 by_source 动态生成"
        assert "by_source" in frag, name
        assert "data-built" in frag, name
        assert "r4cApplyFilter" in frag, name
        assert "r4cOpenTableDetail" in frag, name


# ---------------------------------------------------------------------------
# 7. 既有主链守卫（严禁改两页既有 fetch 链 / 渲染函数）
# ---------------------------------------------------------------------------

def test_existing_chains_bt_view_untouched():
    """bt_view.html 的 /api/bt_daily_ledger 与 /api/ops_today 主链、boot/render 仍在；
    新增片段不得引用这两条链。"""
    src = PAGES["bt_view.html"]
    assert "/api/bt_daily_ledger" in src and "/api/ops_today" in src
    assert "fetch('/api/bt_daily_ledger')" in src
    assert "fetch('/api/ops_today')" in src
    assert "async function boot(){" in src and "boot();" in src
    assert "function render(){" in src
    frag = added(src)
    assert "/api/bt_daily_ledger" not in frag, "新增片段碰了既有账本主链"
    assert "/api/ops_today" not in frag, "新增片段碰了既有运营主链"


def test_existing_chains_news_intel_untouched():
    """news_intel.html 的 /api/news/intel 主链、load/render* 渲染函数、60s 轮询仍在；
    新增片段不得引用该链。"""
    src = PAGES["news_intel.html"]
    assert "/api/news/intel" in src
    assert 'fetch("/api/news/intel")' in src
    assert "function load() {" in src
    assert "function renderOverview(ov) {" in src
    assert "function renderVerdicts(vd) {" in src
    assert "setInterval(load, 60000);" in src, "60s 自动轮询链被改"
    frag = added(src)
    assert "/api/news/intel" not in frag, "新增片段碰了既有情报主链"


# ---------------------------------------------------------------------------
# 8. 换行风格与内联 script 语法（无浏览器；node --check 为 CLI 校验）
# ---------------------------------------------------------------------------

def test_crlf_style_preserved():
    """两页保持原 CRLF 换行风格，无孤立 LF（用原始字节 / ord 复核，不凭打印）。"""
    for name in PAGES:
        raw = (PORTAL / name).read_bytes()
        assert raw.count(b"\r\n") > 0, name
        assert raw.count(b"\n") == raw.count(b"\r\n"), name + " 出现孤立 LF"
        # 逐字节复核：每个 \n 的前一字节必须是 \r
        for i, b in enumerate(raw):
            if b == 0x0A:
                assert i > 0 and raw[i - 1] == 0x0D, name + " 字节级发现孤立 LF"


def test_inline_scripts_node_check():
    """抽两页内联 <script> 到 tempfile.gettempdir() 跑 node --check（无浏览器）。"""
    assert shutil.which("node"), "需要 node 可执行文件"
    for name in PAGES:
        src = (PORTAL / name).read_bytes().decode("utf-8")
        scripts = re.findall(r"<script>(.*?)</script>", src, re.S)
        assert len(scripts) == 1, name + " 内联 <script> 块数量异常"
        for s in scripts:
            fd, path = tempfile.mkstemp(suffix=".js", prefix="r4c_check_",
                                        dir=tempfile.gettempdir())
            os.close(fd)
            try:
                with open(path, "wb") as f:
                    f.write(s.encode("utf-8"))
                r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
                assert r.returncode == 0, name + " 内联 script 语法错误: " + r.stderr
            finally:
                os.remove(path)
