# -*- coding: utf-8 -*-
"""R2-A 页面接线 · catalog 双通道契约测试（纯读源文本，无浏览器 / 无服务 / 无网络）。

依据：`impl_round2_contract_20261105.md` §4 统一接入模式、用户交付 §4。
本轮 8091 尚未自然重启、两个实时端点实测 404，因此无法用真实 HTTP 验证实时分支；
本测试只做静态契约断言（端点字面量 / 静默回落 / XSS 转义 / 业务数据源未被替换 /
L446 内嵌 JSON 未被删 / 超时与异常捕获 / sample_note·freshness·clamped 披露）。

风格对齐 `portal/tests/test_dw_catalog_fallback.py`：pytest 直接收集的纯 assert 函数，
不 import pytest、不启浏览器。

约定：「无硬编码端口」只针对**本轮新增片段**（用 `R2-A CATALOG BEGIN/END` 标记切片），
避免 picks.html 可能存在的历史存量字符串造成误判；正则口径
`(https?://)?(127\\.0\\.0\\.1|localhost):\\d{2,5}`。文案里的「待 8091 自然重启」
是运维进程说明而非 URL，不命中该正则。
"""
from pathlib import Path
import re

PORTAL = Path(__file__).resolve().parents[1]
PAGES = {
    "static/dw.html": (PORTAL / "static" / "dw.html").read_text(encoding="utf-8"),
    "static/dw_v2.html": (PORTAL / "static" / "dw_v2.html").read_text(encoding="utf-8"),
    "picks.html": (PORTAL / "picks.html").read_text(encoding="utf-8"),
}
DW = PAGES["static/dw.html"]
V2 = PAGES["static/dw_v2.html"]
PICKS = PAGES["picks.html"]

PORT_RE = re.compile(r"(https?://)?(127\.0\.0\.1|localhost):\d{2,5}")
BEGIN = "R2-A CATALOG BEGIN"
END = "R2-A CATALOG END"


def added(src):
    """提取本轮新增片段（HTML 注释块 + JS 注释块），供「只检查新增片段」的用例使用。

    用局部片段而非全文 `in` 做断言，确保检查的是「本轮真的这么写了」，
    而不是文件别处恰好出现了同样的字符串。
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
    assert parts, "未找到 R2-A 新增标记"
    return "\n".join(parts)


def test_source_files_real():
    """防读到空文件 / 错文件（否则后续断言会退化成恒真）。"""
    assert DW.lstrip().lower().startswith("<!doctype html>")
    assert V2.lstrip().lower().startswith("<!doctype html>")
    assert PICKS.lstrip().startswith("<!DOCTYPE html>")
    # 体积用原始字节复核（中文 UTF-8 占 3 字节，len() 是字符数会低估）
    assert len(DW.encode("utf-8")) > 38000
    assert len(V2.encode("utf-8")) > 319000
    assert len(PICKS.encode("utf-8")) > 65000
    for name, src in PAGES.items():
        assert src.rstrip().endswith("</html>"), name
        assert src.count(BEGIN) >= 2, name + " 缺少新增标记（HTML + JS 各一对）"
        assert src.count(END) >= 2, name


def test_both_catalog_endpoints_present():
    """三个页面都含 /api/data/catalog/tables 与 /api/data/catalog/table 字面量，且同源相对、带编码与 limit=20。"""
    for name, src in PAGES.items():
        assert src.count("/api/data/catalog/tables?detail=brief") == 1, name
        assert src.count("/api/data/catalog/table?name=") == 1, name
        assert "encodeURIComponent(name)" in src, name
        assert "&limit=20" in src, name
        assert "fetch('/api/data/catalog/tables?detail=brief'" in src, name
        assert "fetch('/api/data/catalog/table?name='+encodeURIComponent(name)+'&limit=20'" in src, name


def test_added_fragments_no_hardcoded_port():
    """只断言本轮新增片段无硬编码端口（picks.html 历史存量不干扰）。"""
    for name, src in PAGES.items():
        frag = added(src)
        assert not PORT_RE.search(frag), "%s 新增片段出现硬编码端口" % name
        assert "8091 自然重启" in frag, name + " 8091 仅作运维提示文案"


def test_added_fragments_all_same_origin():
    """新增片段无外部绝对 URL / CDN，全部同源相对路径。"""
    for name, src in PAGES.items():
        assert not re.search(r"https?://", added(src)), name + " 新增片段出现外部 URL"


def test_silent_fallback_markers():
    """三页都有失败静默回落标记文本与未就绪提示。"""
    for name, src in PAGES.items():
        assert ("catalog 静态快照" in src) or ("实时通道未就绪" in src), name
        assert "实时通道未就绪（待 8091 自然重启）" in src, name
        assert "catalog 实时" in src and "catalog 静态快照" in src, name


def test_esc_escaping_in_added():
    """三页都有 esc( 转义调用；新增片段内来自 API 的字符串全部经 esc()。"""
    for name, src in PAGES.items():
        assert "esc(" in src, name
        frag = added(src)
        assert frag.count("esc(") >= 10, name + " 新增片段 esc 调用过少"
        assert "esc(reason)" in frag, name
        assert "esc(d.sample_note)" in frag, name


def test_picks_business_source_untouched():
    """picks.html 主数据源 / render / boot 未被改，未拿 ts_daily 前 20 行样本替换选股清单。"""
    assert PICKS.count('fetch("data/todays_picks.json")') == 1
    assert "render(await res.json());" in PICKS
    assert 'fetch("data/pick_verdicts.json")' in PICKS
    assert 'fetch("data/xunyou_latest.json"' in PICKS  # 该 fetch 带 {cache:"no-store"} 第二参数
    assert PICKS.count(
        "function render(data){ DATA=data; renderVitals(); renderMkt(); "
        "bindFilters(); bindListOnce(); renderView(); }"
    ) == 1
    assert PICKS.count("async function boot(){") == 1
    assert PICKS.count("boot();") == 1
    # 主清单渲染仍在（未被表样本替换）
    assert '$("#list").innerHTML=show.map(' in PICKS
    # JS 新增片段位于 boot(); 之后 => 纯增量，boot() 本体未改
    tail = PICKS[PICKS.index("boot();"):]
    assert "/* R2-A CATALOG BEGIN" in tail, "JS 新增片段应位于 boot(); 之后（纯增量）"


def test_dw_v2_embedded_data_and_query_kept():
    """dw_v2 L446 内嵌 DW_DATA 与 /api/dw/query 未删；运行时只覆盖 meta 展示字段。"""
    assert V2.count("const DW_DATA = {") == 1
    assert "let DW = DW_DATA;" in V2
    assert V2.count('/api/dw/query?') == 1
    assert "原型页 · 生产范围待裁定" in V2
    frag = added(V2)
    assert "m.catalog_version=live.version" in frag
    assert "m.catalog_updated=live.updated" in frag
    assert "m.display=live.total" in frag
    assert "DW_DATA.meta" in frag
    # 新增片段不得物理重写/删除内嵌 JSON 定义
    assert "const DW_DATA = {" not in frag


def test_static_dw_preserved_chains_and_footnote():
    """static/dw.html 聚合链 / 快照链 / 样本原链路 / renderAll 顺序 / state 语义未改；脚注追加 258 口径不删 314。"""
    assert DW.count('var OPS="/api/dataops/overview";') == 1
    assert '/static/dw_snapshot.json' in DW
    assert 'DW+"/sample?id="+encodeURIComponent(id)+"&n=5"' in DW, "原 /api/dw 采样链路被删"
    assert "function showSampleOriginal(id){" in DW, "原采样链路未作为回落保留"
    assert "renderBanner();renderKpis();renderStatus();renderTable();renderApiMap();" in DW, "renderAll 调用顺序被改"
    assert 'var state={mode:"probing"' in DW, "state 既有字段语义被改"
    # L179 脚注：追加 258 唯一口径，但不删原 314 物理资产视图说明
    assert "逻辑表唯一口径 = catalog.json 258 表" in DW
    assert "dataset_inventory_*.json" in DW, "原 314 数据集盘点口径说明被删"
    assert "物理资产视图 314 仍成立" in DW


def test_sample_note_freshness_and_clamp_shown():
    """三页新增片段都含 sample_note / freshness / clamped / limit_applied 展示逻辑。"""
    for name, src in PAGES.items():
        frag = added(src)
        assert "d.sample_note" in frag and "esc(d.sample_note)" in frag, name
        assert "meta.freshness" in frag, name
        assert "d.clamped" in frag and "d.limit_applied" in frag, name
        assert "clamped=" in frag and "limit_applied=" in frag, name


def test_timeout_and_exception_capture_per_page():
    """每页都有 3s 超时与异常捕获（catch/try），两路实时请求都挂 signal，失败静默不刷屏。"""
    for name, src in PAGES.items():
        frag = added(src)
        assert "try{" in frag and "catch(e){" in frag, name + " 缺少 try/catch"
        assert "AbortSignal.timeout(3000)" in frag, name
        assert "signal:timeoutSignal()" in frag, name
        assert frag.count("signal:timeoutSignal()") == 2, name + " 两个实时通道都应带超时"
        # 只匹配「调用」形态；注释里「不 console.error、不 alert」不含左括号，不算
        assert "console.error(" not in frag, name + " 失败不得 console.error 刷屏"
        assert "alert(" not in frag, name + " 失败不得弹 alert"


def test_catalog_state_machine_and_table_list():
    """双通道状态机 + 可折叠目录六列 + 由 by_source 动态生成的源下拉。"""
    for name, src in PAGES.items():
        frag = added(src)
        assert "var CATALOG_LIVE=null;" in frag, name
        assert "live.ok!==true" in frag and "live.total>0" in frag, name
        assert "CATALOG_STATE='live'" in frag and "CATALOG_STATE='snapshot'" in frag, name
        assert "<details" in frag and "</details>" in frag, name + " 目录不可折叠"
        assert "<th>表名</th><th>源</th><th>频率</th><th>新鲜度</th><th>行数</th><th>状态</th>" in frag, name
        assert "Object.keys(live.by_source||{}).sort()" in frag, name
        assert 'id="r2a-search"' in frag and 'id="r2a-source"' in frag, name


def test_modal_reuse_or_independent_per_page():
    """static/dw 复用既有 openModal；另两页有独立 #r2a-modal；三页弹窗都显示 schema / 主键 / 前 20 行。"""
    assert "function r2aShowModal(t,s,h){ openModal(t,s,h); }" in DW
    for name in ("static/dw_v2.html", "picks.html"):
        src = PAGES[name]
        assert 'id="r2a-modal"' in src, name
        assert "function r2aCloseModal()" in src, name
    for name, src in PAGES.items():
        frag = added(src)
        assert "meta.schema" in frag and "primary_key" in frag, name
        assert "rows.slice(0,20)" in frag, name


# ---------------------------------------------------------------------------
# R3-A 集成缺陷修复（impl_round3_contract_20261105.md §1.1 / §1.2）
# ---------------------------------------------------------------------------

def test_showSample_no_openTableDetail_single_channel():
    """R3-A §1.1：精确切片 showSample 函数体，断言函数体内不再出现 openTableDetail。"""
    s = DW.index("function showSample(id){")
    e = DW.index("function showSampleOriginal(id){", s)
    body = DW[s:e]  # 精确切片，不整文件 grep（避免误伤目录行点击处的 openTableDetail 调用）
    assert "openTableDetail" not in body, "样本按钮仍走 catalog.table（dataset_id≠catalog 表名）"
    assert "CATALOG_LIVE" not in body, "showSample 不应再按实时状态做分支"
    assert body.count("showSampleOriginal(id)") == 1, "应只保留单通道回落"
    # 准确说明注释（上移到函数上方、逐字保留）仍在：两条链路分开
    assert "dataset_id 是 314 物理资产 slug" in DW
    assert "不是 catalog 表名" in DW
    assert "不能走 /api/data/catalog/table" in DW
    assert "经 openTableDetail 进入" in DW
    assert "两条链路分开" in DW


def test_dw_sample_chain_and_table_detail_kept():
    """R3-A：原 /api/dw/sample 采样链路与目录行点击经 openTableDetail 的新通道都保留。"""
    assert "function showSampleOriginal(id){" in DW
    assert 'DW+"/sample?id="+encodeURIComponent(id)+"&n=5"' in DW, "/api/dw/sample 原链路被删"
    # 目录行点击 → openTableDetail 的新通道仍在（防把两条链路一起删）
    assert "async function openTableDetail(name, onFail){" in DW
    assert "tr.onclick=function(){openTableDetail(tr.getAttribute('data-tname'));}" in DW
    assert DW.count("/api/data/catalog/table?name=") == 1
    assert DW.count("openTableDetail") >= 2


def test_dw_v2_coverage_note_wording_and_no_fake():
    """R3-A §1.2：258/244/256 口径注记存在，且未伪造 display_covered/physical。"""
    assert "内嵌快照的覆盖统计" in V2
    assert "两者口径不同" in V2
    assert "11-03 内嵌快照" in V2
    # 258 用 live.total 动态取，不写死
    assert "'catalog 实时 '+live.total+' 表" in V2
    assert "catalog 实时 258 表" not in V2
    # renderHero 仍读快照真实统计，未改成实时值（不伪造）
    assert "esc(m.display_covered)" in V2
    assert "esc(m.physical)" in V2
    assert not re.search(r"display_covered\s*=\s*live\.total", V2)
    assert not re.search(r"display_covered\s*=\s*CATALOG_LIVE", V2)
    # 合法的 meta 展示字段覆盖仍在
    assert "m.display=live.total" in V2


def test_dw_v2_coverage_note_gated_by_live_state():
    """R3-A §1.2：注记仅在 CATALOG_STATE==='live'/r2aOnLive 成功分支显示，404 静默回落时不显示。"""
    # 元素默认隐藏，404/探测失败时保持逐像素一致
    assert '<span class="r2a-covnote r2a-hidden" id="r2a-covnote"></span>' in V2
    assert ".r2a-hidden{display:none}" in V2
    # 200 渲染分支：r2aOnLive 成功钩子挂上注记
    s = V2.index("function r2aOnLive(live){")
    e = V2.index("function r2aSetCoverageNote(live){", s)
    onlive = V2[s:e]
    assert "r2aSetCoverageNote(live)" in onlive, "实时成功钩子未挂注记"
    # 显示逻辑受 CATALOG_STATE==='live' 控制；失败分支清空并重新隐藏
    assert "CATALOG_STATE!=='live'" in V2
    assert "el.classList.add('r2a-hidden'); el.textContent=''" in V2
    assert "el.classList.remove('r2a-hidden')" in V2
    # r2aSetFlag 在 404 回落分支传 null 隐藏注记（404 静默回落 / 200 渲染 双分支）
    assert "r2aSetCoverageNote(CATALOG_STATE==='live'?CATALOG_LIVE:null)" in V2
    assert "r2aSetFlag('catalog 静态快照')" in V2
    assert "r2aSetFlag('catalog 实时')" in V2
