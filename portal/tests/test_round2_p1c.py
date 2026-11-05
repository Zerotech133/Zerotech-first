# -*- coding: utf-8 -*-
"""R3-C / P1-C 两页接入 · catalog 双通道契约测试（纯静态断言 + node 执行 URN 解析）。

依据：`impl_round3_contract_20261105.md` §2、`impl_round2_contract_20261105.md` §4、
`catalog_contract_20261105.md` §3 表第 3、4 行 / §5 P1-C，以及用户交付 §5。

本轮 8091 尚未自然重启、两个实时端点实测 404，因此**无法用真实 HTTP 验证 200 分支**；
本测试只做源码文本静态断言（端点字面量 / 404 静默回落 / 200 渲染分支 / XSS 转义 /
既有链守卫 / 口径文案 / 超时与异常捕获 / sample_note·freshness·clamped 披露），
并把 lineage 的 URN 解析具名函数抽出来用 `node` 执行核对映射，另对所有内联
`<script>` 跑 `node --check`。不依赖浏览器 / 服务 / 网络。

风格对齐 `portal/tests/test_dw_catalog_fallback.py`、`test_round2_catalog_pages.py`：
pytest 直接收集的纯 assert 函数，不 import pytest。

约定：「无硬编码端口」只针对**本轮新增片段**（用 `R3-C CATALOG BEGIN/END` 标记配对切片），
避免 `lineage.html:47` 的历史存量外链 `http://127.0.0.1:8585` 造成误判；正则口径
`(https?://)?(127\\.0\\.0\\.1|localhost):\\d{2,5}`。文案里的「待 8091 自然重启」
是运维进程说明而非 URL，不命中该正则。
"""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import tempfile

PORTAL = Path(__file__).resolve().parents[1]
PAGES = {
    "dataops.html": (PORTAL / "dataops.html").read_text(encoding="utf-8"),
    "lineage.html": (PORTAL / "lineage.html").read_text(encoding="utf-8"),
}
DATAOPS = PAGES["dataops.html"]
LINEAGE = PAGES["lineage.html"]

PORT_RE = re.compile(r"(https?://)?(127\.0\.0\.1|localhost):\d{2,5}")
BEGIN = "R3-C CATALOG BEGIN"
END = "R3-C CATALOG END"


def added(src):
    """按 BEGIN/END 配对提取本轮新增片段（CSS + HTML + JS 各一对）。

    用局部片段而非全文 `in` 做断言，确保检查的是「本轮真的这么写了」，
    而不是文件别处（尤其 lineage.html:47 历史外链）恰好出现了同样的字符串。
    """
    parts, i = [], 0
    while True:
        a = src.find(BEGIN, i)
        if a < 0:
            break
        b = src.find(END, a)
        assert b > a, "BEGIN 之后找不到 END"
        parts.append(src[a:b + len(END)])
        i = b + len(END)
    assert parts, "未找到 R3-C 新增标记"
    return "\n".join(parts)


def test_source_files_real():
    """防读到空文件 / 错文件（否则后续断言会退化成恒真）。"""
    for name, src in PAGES.items():
        assert src.lstrip().lower().startswith("<!doctype html>"), name
        assert src.rstrip().endswith("</html>"), name
        # 体积用原始字节复核（中文 UTF-8 占 3 字节，len() 是字符数会低估）
        assert len(src.encode("utf-8")) > 9000, name
        assert src.count(BEGIN) == 3, name + " 新增 BEGIN 标记应为 CSS/HTML/JS 各一对"
        assert src.count(END) == 3, name + " 新增 END 标记应为 CSS/HTML/JS 各一对"


def test_both_catalog_endpoints_present():
    """两页都含 tables 与 table 字面量，且同源相对、带编码与 limit=20。"""
    for name, src in PAGES.items():
        assert src.count("/api/data/catalog/tables?detail=brief") == 1, name
        assert src.count("/api/data/catalog/table?name=") == 1, name
        assert "encodeURIComponent(name)" in src, name
        assert "&limit=20" in src, name
        assert "fetch('/api/data/catalog/tables?detail=brief',{cache:'no-store',signal:timeoutSignal()})" in src, name
        assert "fetch('/api/data/catalog/table?name='+encodeURIComponent(name)+'&limit=20'" in src, name


def test_added_fragments_no_hardcoded_port():
    """只断言本轮新增片段无硬编码端口（lineage.html:47 历史存量不干扰）。"""
    for name, src in PAGES.items():
        frag = added(src)
        assert not PORT_RE.search(frag), "%s 新增片段出现硬编码端口" % name
        assert "8091 自然重启" in frag, name + " 8091 仅作运维提示文案"
        # 8091 只允许作为运维提示文案出现，且该行不是请求地址
        for ln in frag.splitlines():
            if "8091" in ln:
                assert "fetch(" not in ln and "://" not in ln and ":8091" not in ln


def test_added_fragments_all_same_origin():
    """新增片段无外部绝对 URL / CDN，全部同源相对路径。"""
    for name, src in PAGES.items():
        assert not re.search(r"https?://", added(src)), name + " 新增片段出现外部 URL"


def test_historical_8585_external_link_preserved():
    """lineage.html:47 历史外链仍在（本轮不改），且不在任何新增片段内。"""
    assert LINEAGE.count("http://127.0.0.1:8585") == 1
    assert "127.0.0.1:8585" not in added(LINEAGE), "历史外链被误带入新增块"


def test_silent_fallback_markers():
    """两页都有失败静默回落标记与未就绪提示、双通道状态文案。"""
    for name, src in PAGES.items():
        assert ("catalog 静态快照" in src) or ("实时通道未就绪" in src), name
        assert "实时通道未就绪（待 8091 自然重启）" in src, name
        assert "catalog 实时" in src and "catalog 静态快照" in src, name


def test_esc_escaping_in_added():
    """两页都有 esc( 转义；新增片段内来自 API 的字符串全部经 esc()。"""
    for name, src in PAGES.items():
        assert "esc(" in src, name
        frag = added(src)
        assert frag.count("esc(") >= 10, name + " 新增片段 esc 调用过少"
        assert "esc(d.sample_note)" in frag, name
        assert "esc(d.limit_applied)" in frag, name
        assert "esc(!!d.clamped)" in frag, name


def test_try_catch_timeout_and_no_noise():
    """每页都有 3s 超时与异常捕获，两路实时请求都挂 signal，失败不刷屏不弹窗。"""
    for name, src in PAGES.items():
        frag = added(src)
        assert "try{" in frag and "catch(e){" in frag, name + " 缺少 try/catch"
        assert "AbortSignal.timeout(3000)" in frag, name
        assert "typeof AbortController!=='undefined'" in frag, name + " 缺老浏览器兜底"
        assert frag.count("signal:timeoutSignal()") == 2, name + " 两个实时通道都应带超时"
        assert "console.error(" not in frag, name + " 失败不得 console.error 刷屏"
        assert "alert(" not in frag, name + " 失败不得弹 alert"


def test_404_silent_branch():
    """404 / 401 / 超时 / 网络 / JSON 错 → 静默回落分支静态断言。"""
    for name, src in PAGES.items():
        frag = added(src)
        assert "if(!r.ok) throw new Error('HTTP '+r.status);" in frag, name
        assert "}catch(e){" in frag, name
        assert "CATALOG_STATE='snapshot';" in frag and "CATALOG_LIVE=null;" in frag, name
        assert "实时通道未就绪（待 8091 自然重启）" in frag, name
        assert "静默" in frag, name + " 缺少静默回落注释/语义"


def test_200_render_branch():
    """200 渲染分支：成功判据 ok&&total>0、存对象标实时、schema/样本渲染逻辑存在。"""
    for name, src in PAGES.items():
        frag = added(src)
        assert "live.ok!==true" in frag and "live.total>0" in frag, name
        assert "CATALOG_LIVE=live; CATALOG_STATE='live';" in frag, name
        assert "r3cSetState('catalog 实时');" in frag, name
        assert "meta.schema" in frag and "primary_key" in frag, name
        assert "rows.slice(0,20)" in frag, name
        assert ("tableDetailHtml(d)" in frag) or ("r3cSchemaHtml(d,name)" in frag), name
    assert "renderTableList();" in DATAOPS, "dataops 实时成功后应渲染表清单"
    assert "r3cProbeTable();" in LINEAGE, "lineage 实时成功后应尝试补表级区块"


def test_catalog_state_machine():
    """双通道状态机：unknown / live / snapshot 三态齐全。"""
    for name, src in PAGES.items():
        frag = added(src)
        assert "var CATALOG_LIVE=null;" in frag, name
        assert "var CATALOG_STATE='unknown';" in frag, name
        assert "CATALOG_STATE='live'" in frag and "CATALOG_STATE='snapshot'" in frag, name


def test_sample_note_freshness_clamp_shown():
    """两页新增片段都含 sample_note / freshness / clamped / limit_applied 展示逻辑。"""
    for name, src in PAGES.items():
        frag = added(src)
        assert "d.sample_note" in frag and "esc(d.sample_note)" in frag, name
        assert "meta.freshness" in frag, name
        assert "d.clamped" in frag and "d.limit_applied" in frag, name
        assert "clamped=" in frag and "limit_applied=" in frag, name


def test_existing_chains_dataops_untouched():
    """dataops 既有三处 fetch / switchTab / render / load 链与既有渲染函数逐字仍在。"""
    assert DATAOPS.count("fetch('/api/dataops/overview'") == 1
    assert DATAOPS.count("fetch('/api/dataops/map'") == 1
    assert DATAOPS.count("fetch('/api/dataops/entity?id='") == 1
    assert "switchTab(" in DATAOPS
    assert "load(); setInterval(load,60000);" in DATAOPS, "60s 自动刷新链被改"
    assert "render(await r.json());" in DATAOPS
    assert "function render(d){" in DATAOPS
    assert "function renderMap(m){" in DATAOPS
    assert "async function openDrawer(nid){" in DATAOPS


def test_existing_chains_lineage_untouched():
    """lineage 既有 /api/kg/lineage 主链、doSearch / jump / setTab / render、URN 输入框、:47 外链仍在。"""
    # 新增注释里也提到该路径，故只断言真实 fetch 形式逐字存在且未被改写
    assert "fetch('/api/kg/lineage?'+p,{cache:'no-store'})" in LINEAGE
    assert LINEAGE.count("/api/kg/lineage") >= 1
    assert "function doSearch(){load()}" in LINEAGE
    assert "doSearch()" in LINEAGE
    assert "function jump(t){" in LINEAGE
    assert "function setTab(v){" in LINEAGE
    assert "function render(j){" in LINEAGE
    assert "http://127.0.0.1:8585" in LINEAGE
    assert 'placeholder="输入逻辑表 URN，如 neo4j:TRADED_ON / parquet:tdx/ts_top_inst / jsonl:chanlun_pool"' in LINEAGE


def test_caliber_note_258_vs_233():
    """口径说明文案：258 唯一口径 / 233 是历史写死值（消除 233/258 展示层漂移）。"""
    frag = added(DATAOPS)
    assert "逻辑表唯一口径 = catalog.json 258 表" in frag
    assert "233 表" in frag
    assert "历史写死值" in frag
    assert "不代表当前表数" in frag


def test_dataops_card_markup_and_distributions():
    """dataops 卡片可折叠、六列表头、by_source/by_freq 分布、搜索与源筛选元素齐全。"""
    frag = added(DATAOPS)
    assert "<details" in frag and "</details>" in frag, "卡片不可折叠"
    assert "<th>表名</th><th>源</th><th>频率</th><th>新鲜度</th><th>行数</th><th>状态</th>" in frag
    assert "Object.keys(live.by_source||{}).sort()" in frag, "源下拉未由 by_source 动态生成"
    assert 'id="r3cSearch"' in frag and 'id="r3cSource"' in frag
    assert "r3cBySource" in frag and "r3cByFreq" in frag
    assert "by_source" in frag and "by_freq" in frag, "缺 by_source / by_freq 分布渲染"


def test_lineage_urn_parse_source_and_node_exec():
    """URN 解析具名函数：源码文本断言 + 抽函数体用 node 执行核对映射。

    parquet:tdx/ts_top_inst → ts_top_inst；neo4j:TRADED_ON → TRADED_ON；
    jsonl:chanlun_pool → chanlun_pool；解析不出返回空串，不猜。
    """
    assert LINEAGE.count("function parseLogicalTable(urn){") == 1
    assert "parquet:tdx/ts_top_inst → ts_top_inst" in LINEAGE, "URN 映射示例注释缺失"
    assert "split('/')" in LINEAGE and "segs[segs.length-1]" in LINEAGE

    start = LINEAGE.index("function parseLogicalTable(urn){")
    depth, end = 0, None
    for i in range(start, len(LINEAGE)):
        if LINEAGE[i] == "{":
            depth += 1
        elif LINEAGE[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    assert end, "抽取 parseLogicalTable 函数体失败"
    fn = LINEAGE[start:end]
    assert "segs[segs.length-1]" in fn and "split('/')" in fn

    js = fn + "\n" + (
        'var cases=["parquet:tdx/ts_top_inst","neo4j:TRADED_ON","jsonl:chanlun_pool",'
        '"","no-colon","parquet:","neo4j:","parquet:tdx/bad-name"];\n'
        'var out={};cases.forEach(function(c){out[c]=parseLogicalTable(c);});\n'
        'console.log(JSON.stringify(out));\n'
    )
    assert shutil.which("node"), "需要 node 可执行文件来跑 URN 解析断言"
    fd, path = tempfile.mkstemp(suffix=".js", prefix="r3c_urn_", dir=tempfile.gettempdir())
    os.close(fd)
    try:
        with open(path, "wb") as f:
            f.write(js.encode("utf-8"))
        r = subprocess.run(["node", path], capture_output=True, text=True)
        assert r.returncode == 0, "node 执行 URN 解析失败: " + r.stderr
        out = json.loads(r.stdout.strip())
    finally:
        os.remove(path)

    assert out["parquet:tdx/ts_top_inst"] == "ts_top_inst"
    assert out["neo4j:TRADED_ON"] == "TRADED_ON"
    assert out["jsonl:chanlun_pool"] == "chanlun_pool"
    for bad in ("", "no-colon", "parquet:", "neo4j:", "parquet:tdx/bad-name"):
        assert out[bad] == "", "解析失败应返回空串而非猜测: %r -> %r" % (bad, out[bad])


def test_lineage_urn_failure_silently_collapses():
    """lineage 表级区块默认隐藏；解析失败 / 404 / available:false 静默收起，不写主链错误区。"""
    frag = added(LINEAGE)
    assert 'class="r3c-box r3c-hidden"' in frag, "区块默认应隐藏（404 时页面与改造前一致）"
    assert frag.count("box.classList.add('r3c-hidden')") >= 3, "失败分支应静默收起"
    assert "d.available===false" in frag, "表未登记（available:false）应收起"
    assert "if(!name){ box.classList.add('r3c-hidden'); return; }" in frag, "URN 解析失败应收起"
    assert "$('#errbox').innerHTML" not in frag, "不得覆盖 /api/kg/lineage 的错误提示区"


def test_inline_scripts_node_check():
    """抽两页内联 <script> 到 tempfile 跑 node --check（无浏览器、无服务）。"""
    assert shutil.which("node"), "需要 node 可执行文件"
    for name in ("dataops.html", "lineage.html"):
        src = (PORTAL / name).read_bytes().decode("utf-8")
        scripts = re.findall(r"<script>(.*?)</script>", src, re.S)
        assert len(scripts) == 1, name + " 内联 <script> 块数量异常"
        for s in scripts:
            fd, path = tempfile.mkstemp(suffix=".js", prefix="r3c_check_",
                                        dir=tempfile.gettempdir())
            os.close(fd)
            try:
                with open(path, "wb") as f:
                    f.write(s.encode("utf-8"))
                r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
                assert r.returncode == 0, name + " 内联 script 语法错误: " + r.stderr
            finally:
                os.remove(path)


def test_crlf_style_preserved():
    """两页保持原 CRLF 换行风格，无孤立 LF。"""
    for name in ("dataops.html", "lineage.html"):
        raw = (PORTAL / name).read_bytes()
        assert raw.count(b"\r\n") > 0, name
        assert raw.count(b"\n") == raw.count(b"\r\n"), name + " 出现孤立 LF"
