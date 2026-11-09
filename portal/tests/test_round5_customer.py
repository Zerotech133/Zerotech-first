# -*- coding: utf-8 -*-
"""R5-B · P1-D 第 1 批 · `portal/customer.html` catalog 双通道静态契约测试。

依据：`reports/impl_round5_contract_20261106.md` §0 红线 + §3（本轮唯一 spec）、
模板 `portal/bt_view.html` 的 `R4-C CATALOG BEGIN/END` 段（命名空间换 `r5b-`）、
文案口径守卫 `portal/tests/test_round4_browser_fixes.py`。

客户门户页（客户配额 / 分析报告）本轮只做**纯前端增量接入**：
1. `probeCatalog()` 实时通道 `/api/data/catalog/tables?detail=brief`，3s 硬超时，
   失败静默回落为「catalog 静态快照」；
2. 可折叠「主仓表溯源」区块（258 表目录 + 关键词搜索 + `by_source` 动态源下拉）；
3. 点行 → `/api/data/catalog/table?name=&limit=20` 弹窗（schema / 主键 / 前 20 行）。

本文件只做源码文本静态断言 + 内联 `<script>` 的 `node --check`，**不启浏览器、不请求 HTTP**。
「无硬编码端口」只针对本轮新增片段（BEGIN/END 配对切片），正则口径沿用上轮
`(https?://)?(127\\.0\\.0\\.1|localhost):\\d{2,5}`；提示语里的「待 8091 自然重启」
是运维进程说明而非 URL，不命中该正则。

★ 重点防守 `browser_review_20261106.md` §3 缺陷 2（lineage.html 上犯过的错）：
实时成功后「实时通道未就绪」提示必须隐藏、失败时必须恢复显示。
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile

PORTAL = Path(__file__).resolve().parents[1]
PAGE = PORTAL / "customer.html"
SRC = PAGE.read_text(encoding="utf-8")

BEGIN = "R5-B CATALOG BEGIN"
END = "R5-B CATALOG END"
PORT_RE = re.compile(r"(https?://)?(127\.0\.0\.1|localhost):\d{2,5}")
DEPRECATED = "注意：limit 已截断"           # 修复后口径严禁复用
FAIL_NOTE = "实时通道未就绪（待 8091 自然重启）"

# 既有六条业务链（一字不许动，防误删）
EXISTING_CHAINS = (
    "/api/customer/login",
    "/analyze_async",
    "/job?id=",
    "/history?token=",
    "/report?run_id=",
    "/quota?token=",
)


def added(src=None):
    """按 BEGIN/END 配对提取本轮新增片段（全部新增代码都在其中）。"""
    s = SRC if src is None else src
    a = s.index(BEGIN)
    b = s.index(END, a)
    assert b > a, "BEGIN 之后找不到 END"
    return s[a:b + len(END)]


# ---------------------------------------------------------------------------
# 1. 源文件真实性与新增标记
# ---------------------------------------------------------------------------

def test_source_file_real():
    """防读到空文件 / 错文件（否则后续断言会退化成恒真）。"""
    assert SRC.lstrip().lower().startswith("<!doctype html>")
    assert SRC.rstrip().endswith("</html>")
    # 体积用原始字节复核（中文 UTF-8 占 3 字节，len() 是字符数会低估）
    assert len(SRC.encode("utf-8")) > 20000
    assert SRC.count(BEGIN) == 1, "BEGIN 标记应恰好一对"
    assert SRC.count(END) == 1, "END 标记应恰好一对"


def test_added_fragment_r5b_namespace_only():
    """新增代码用 r5b- 新命名空间，未混入 r2a- / r3c- / r4c- 旧命名空间。"""
    frag = added()
    assert "r5b-" in frag
    assert "r2a-" not in frag, "混入了 R2-A 命名空间"
    assert "r3c-" not in frag, "混入了 R3-C 命名空间"
    assert "r4c-" not in frag, "混入了 R4-C 命名空间"
    assert frag.count("function probeCatalog()") == 1


# ---------------------------------------------------------------------------
# 2. 端点与同源相对路径
# ---------------------------------------------------------------------------

def test_both_catalog_endpoints_present():
    """页面含 tables 与 table 两个字面量，同源相对、带 URI 编码与 limit=20。"""
    assert SRC.count("/api/data/catalog/tables") == 1
    assert SRC.count("/api/data/catalog/table?name=") == 1
    assert "/api/data/catalog/tables?detail=brief" in SRC
    assert "encodeURIComponent(name)" in SRC
    assert "&limit=20" in SRC
    assert "cache:'no-store'" in SRC


def test_added_fragment_no_hardcoded_port():
    """新增片段无硬编码端口；8091 仅作运维提示文案，不得出现在请求地址里。"""
    frag = added()
    assert not PORT_RE.search(frag), "新增片段出现硬编码端口"
    assert "127.0.0.1" not in frag and "localhost" not in frag
    assert FAIL_NOTE in frag, "8091 仅作运维提示文案"
    for ln in frag.splitlines():
        if "8091" in ln:
            assert "fetch(" not in ln and "://" not in ln and ":8091" not in ln


def test_added_fragment_all_same_origin():
    """新增片段无外部绝对 URL / CDN，全部同源相对路径。"""
    frag = added()
    assert not re.search(r"https?://", frag), "新增片段出现外部 URL"


# ---------------------------------------------------------------------------
# 3. 双通道状态机（try/catch、3s 超时、双标记、失败提示语）
# ---------------------------------------------------------------------------

def test_dual_channel_state_machine():
    """unknown / live / snapshot 三态齐全；成功判据 ok===true && total>0 才置 live。"""
    frag = added()
    assert "var R5B_LIVE=null, R5B_STATE='unknown';" in frag
    assert "R5B_STATE='live'" in frag and "R5B_STATE='snapshot'" in frag
    assert "live.ok!==true" in frag and "live.total>0" in frag
    assert "R5B_LIVE=live; R5B_STATE='live';" in frag
    assert "R5B_STATE='snapshot'; R5B_LIVE=null;" in frag


def test_try_catch_timeout_and_no_noise():
    """3s 硬超时（AbortSignal.timeout 优先 + AbortController 兜底），两路请求都挂 signal；
    失败静默：不 console.error、不 alert、不抛未捕获异常。"""
    frag = added()
    assert "try{" in frag and "}catch(e){" in frag, "缺少 try/catch"
    assert "AbortSignal.timeout(3000)" in frag or "3000" in frag, "缺 3s 超时"
    assert "AbortSignal.timeout" in frag, "缺 AbortSignal.timeout 优先实现"
    assert "typeof AbortController!=='undefined'" in frag, "缺老浏览器兜底"
    assert frag.count("signal:r5bTimeoutSignal()") == 2, "两个实时通道都应带 3s 超时"
    assert "if(!r.ok) throw new Error('HTTP '+r.status);" in frag
    assert "console.error(" not in frag, "失败不得 console.error 刷屏"
    assert "alert(" not in frag, "失败不得弹 alert"
    assert "静默" in frag, "缺少静默回落语义"
    # 调用点也兜住 promise rejection，避免「Uncaught (in promise)」进控制台
    assert "try{ probeCatalog().catch(function(){}); }catch(e){}" in frag


def test_live_and_snapshot_flags_and_fail_note():
    """双标记「catalog 实时」「catalog 静态快照」都在；失败提示语文本存在。"""
    frag = added()
    assert "catalog 实时" in frag and "catalog 静态快照" in frag
    assert "r5bSetState('catalog 实时');" in frag
    assert "r5bSetState('catalog 静态快照');" in frag
    assert FAIL_NOTE in frag


# ---------------------------------------------------------------------------
# 4. ★ 缺陷 2 防守：「实时通道未就绪」按状态隐藏 / 恢复
# ---------------------------------------------------------------------------

def test_notice_hidden_on_live_restored_on_failure():
    """实时成功隐藏提示、失败恢复显示（r5b-hidden 类 + style.display 双分支）。

    browser_review §3 缺陷 2：R3-C 的 r3cSetState 只改状态字、没碰 notice，
    导致 lineage 实时成功后黄色「实时通道未就绪」仍可见。本页必须有按状态
    add/remove('r5b-hidden') 与 style.display 的隐藏 / 恢复代码。
    """
    frag = added()
    marker = "function r5bSetState(t)"
    assert frag.count(marker) == 1, "r5bSetState 应唯一"
    body = frag[frag.index(marker): frag.index(marker) + 420]
    assert "r5b-notice" in body, "状态函数未引用 notice 元素"
    assert "n.classList.add('r5b-hidden')" in body, "实时成功未隐藏提示"
    assert "n.classList.remove('r5b-hidden')" in body, "失败未恢复提示"
    assert "n.style.display='none'" in body, "缺 style.display 隐藏分支"
    assert "n.style.display=''" in body, "缺 style.display 恢复分支"
    assert "catalog 实时" in body
    # 渲染函数里同样按状态隐藏 / 恢复（双保险）
    assert frag.count("notice.classList.add('r5b-hidden')") >= 1
    assert frag.count("notice.classList.remove('r5b-hidden')") >= 1
    assert "notice.textContent=R5B_FAIL_NOTE" in frag


# ---------------------------------------------------------------------------
# 5. 弹窗四要素与修复后文案（守 browser_review §3 缺陷 1）
# ---------------------------------------------------------------------------

def test_popup_shows_sample_note_freshness_clamped_limit_applied():
    """弹窗并列展示 sample_note / meta.freshness / clamped / limit_applied + schema/主键/前 20 行。"""
    frag = added()
    assert "d.sample_note" in frag and "esc(d.sample_note" in frag
    assert "meta.freshness" in frag
    assert "d.clamped" in frag and "d.limit_applied" in frag
    assert "clamped=" in frag and "limit_applied=" in frag
    assert "meta.schema" in frag and "primary_key" in frag
    assert "d.available" in frag and "d.reason" in frag
    assert "rows.slice(0,20)" in frag
    assert "样本数据（前 " in frag


def test_fixed_caliber_wording_and_no_deprecated_wording():
    """修复后口径：「样本说明：limit_applied=N · clamped=<bool>。仅返回样本行，不代表全表数据。」；
    仅 clamped===true 才追加「已夹至 N 行」；严禁复用废弃的「注意：limit 已截断」。"""
    frag = added()
    assert "样本说明：limit_applied=" in frag
    assert "仅返回样本行，不代表全表数据。" in frag
    assert "esc(!!d.clamped)" in frag
    assert "d.clamped===true?'请求的 limit 超过上限，已夹至 '" in frag
    assert DEPRECATED not in SRC, "复用已废弃文案"
    assert DEPRECATED not in frag


def test_esc_escaping_in_added():
    """新增片段自带 esc() 并大量调用：来自 API 的动态字符串全部经 esc() 防 XSS。"""
    frag = added()
    assert "function esc(v)" in frag, "页面原无 esc()，须在代码块内定义"
    assert frag.count("esc(") >= 20, "新增片段 esc 调用过少"
    assert "esc(d.sample_note" in frag
    assert "esc(!!d.clamped)" in frag
    assert "esc(name)" in frag and "esc(it.source" in frag
    assert "esc(live.total)" in frag


# ---------------------------------------------------------------------------
# 6. 目录区块（可折叠 / 搜索 / by_source 动态源下拉 / 258 表六列）
# ---------------------------------------------------------------------------

def test_collapsible_block_and_source_dropdown():
    """可折叠只读区块 + 关键词搜索 + 由 by_source 动态生成的源下拉 + 六列表目录。"""
    frag = added()
    assert "createElement('details')" in frag and "box.open=true" in frag, "区块不可折叠"
    assert 'id="r5b-search"' in frag and 'id="r5b-source"' in frag
    assert 'id="r5b-body"' in frag and 'id="r5b-wrap"' in frag
    assert '<option value="">全部源</option>' in frag, "缺「全部源」空值项"
    assert "Object.keys(live.by_source||{}).sort()" in frag, "源下拉未由 by_source 动态生成"
    assert "data-built" in frag
    assert "r5bApplyFilter" in frag
    assert "r5bOpenTableDetail" in frag
    for th in ("<th>表名</th>", "<th>源</th>", "<th>频率</th>",
               "<th>新鲜度</th>", "<th>行数</th>", "<th>状态</th>"):
        assert th in frag, "目录列缺失: " + th


def test_block_mounted_below_main_and_not_global():
    """区块挂在本页 #main 底部（登录后可见），不新建全局弹层、不动 header/footer。"""
    frag = added()
    assert "document.getElementById('main')" in frag
    assert "host.appendChild(box)" in frag
    assert frag.count("box.id='r5b-box'") == 1
    assert frag.count("getElementById('r5b-box')") == 1, "区块应可幂等构建"
    assert "document.body.appendChild" not in frag


# ---------------------------------------------------------------------------
# 7. 既有主链守卫（严禁改既有六条 fetch 链 / 渲染函数）
# ---------------------------------------------------------------------------

def test_existing_chains_intact():
    """六条业务链（login/analyze_async/job/history/report/quota）仍在，且新增片段不碰它们。"""
    for s in EXISTING_CHAINS:
        assert s in SRC, "既有业务链被误删: " + s
    assert SRC.count("/api/customer/login") == 1
    assert SRC.count("/api/customer/analyze_async") == 1
    assert SRC.count("/api/customer/job?id=") == 1
    assert SRC.count("/api/customer/history?token=") == 1
    assert SRC.count("/api/customer/report?run_id=") == 2, "md/h5 两条下载链应各一处"
    assert SRC.count("/api/customer/quota?token=") == 1

    frag = added()
    for s in ("/api/customer/",):
        assert s not in frag, "新增片段碰了既有客户业务链"


def test_existing_renderers_and_api_wrapper_intact():
    """`api()` 封装、token 逻辑、既有渲染函数与样式主体保持原样。"""
    assert "function api(path, opts){" in SRC
    assert "headers: {'Content-Type':'application/json'}" in SRC
    assert "var TOKEN_KEY = 'zb_customer_token';" in SRC
    assert "localStorage.getItem(TOKEN_KEY)" in SRC
    for fn in ("function showLogin(){", "function showMain(me){", "function renderQuota(u){",
               "function login(){", "function run(){", "function loadHistory(){"):
        assert fn in SRC, "既有渲染函数被改: " + fn
    assert SRC.count(":root{--bg:#0f1216") == 1
    assert SRC.count('<div class="wrap">') == 1


# ---------------------------------------------------------------------------
# 8. 换行风格与内联 script 语法（无浏览器；node --check 为 CLI 校验）
# ---------------------------------------------------------------------------

def test_crlf_style_preserved():
    """页面保持原 CRLF 换行风格，无孤立 LF（用原始字节 / ord 复核，不凭打印）。"""
    raw = PAGE.read_bytes()
    assert raw.count(b"\r\n") > 0
    assert raw.count(b"\n") == raw.count(b"\r\n"), "出现孤立 LF"
    # 逐字节复核：每个 \n 的前一字节必须是 \r
    for i, b in enumerate(raw):
        if b == 0x0A:
            assert i > 0 and raw[i - 1] == 0x0D, "字节级发现孤立 LF (offset %d)" % i


def test_added_fragment_crlf_too():
    """BEGIN/END 切片本身也是 CRLF（不引入混合换行）。"""
    raw = PAGE.read_bytes()
    a = raw.index(BEGIN.encode("utf-8"))
    b = raw.index(END.encode("utf-8"), a)
    chunk = raw[a:b]
    assert chunk.count(b"\r\n") > 0
    assert chunk.count(b"\n") == chunk.count(b"\r\n"), "新增片段内出现孤立 LF"


def test_inline_scripts_node_check():
    """抽内联 <script> 到 tempfile.gettempdir() 跑 node --check（无浏览器）。"""
    assert shutil.which("node"), "需要 node 可执行文件"
    src = PAGE.read_bytes().decode("utf-8")
    scripts = re.findall(r"<script>(.*?)</script>", src, re.S)
    assert len(scripts) == 1, "内联 <script> 块数量异常: %d" % len(scripts)
    for s in scripts:
        fd, path = tempfile.mkstemp(suffix=".js", prefix="r5b_check_",
                                    dir=tempfile.gettempdir())
        os.close(fd)
        try:
            with open(path, "wb") as f:
                f.write(s.encode("utf-8"))
            r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
            assert r.returncode == 0, "内联 script 语法错误: " + r.stderr
        finally:
            os.remove(path)
