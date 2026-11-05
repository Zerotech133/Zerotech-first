# -*- coding: utf-8 -*-
"""dw.html「实时 catalog 优先 + 静态快照兜底」双通道契约测试。

纯读 portal/dw.html 源文本做断言，无浏览器 / 无服务 / 无网络依赖，
不需要 8091 在线，也不需要 T1 端点已部署（本轮端点部署前必然 404）。

覆盖 spec §5 与 T2 实现要求：
  1. 原静态快照 fetch 仍在，且仍是 load() 主体数据源与 fallback；
  2. 实时端点 URL 逐字正确、带 3s 超时与 cache:no-store；
  3. try/catch 静默兜底、双通道状态机、SNAP.catalog 覆盖；
  4. SNAP 结构关键字段 / 既有渲染函数 / CARD_DEFS / render() 顺序 / dlJSON 未被破坏；
  5. 全部同源相对路径，无硬编码主机端口；
  6. 表清单 + 关键词搜索 + 源下拉筛选 + 单表弹窗复用 openModal/esc/row；
  7. AbortSignal 兼容兜底与明确说明。

约定：「无硬编码端口」指请求地址不得写死主机/端口；spec §5.2 指定的运维
提示文案「实时通道未就绪（待 8091 自然重启）」里的 8091 是进程说明，不是 URL，
本测试用「该行不得含 fetch / 不得含 :// / 不得含 :8091 端口写法」来区分。
"""
from pathlib import Path
import re

HTML = Path(__file__).resolve().parents[1] / "dw.html"
SRC = HTML.read_text(encoding="utf-8")


def _slice(start, end):
    """截取 [start, end) 之间的源码片段；要求起始锚点在全文中唯一。

    用局部片段而非全文 `in` 做断言，确保检查的是「某个函数里真的这么写了」，
    而不是文件别处恰好出现了同样的字符串。
    """
    assert SRC.count(start) == 1, "锚点不唯一或缺失: %r (count=%d)" % (start, SRC.count(start))
    i = SRC.index(start)
    j = SRC.find(end, i + len(start))
    assert j > i, "%r 之后找不到 %r" % (start, end)
    return SRC[i:j]


def test_source_is_real_dw_html():
    """防止读到空文件 / 错文件（否则后续断言会退化成恒真）。"""
    assert HTML.is_file(), HTML
    assert SRC.lstrip().startswith("<!DOCTYPE html>")
    assert SRC.rstrip().endswith("</html>")
    assert len(SRC) > 30000, "dw.html 体积异常"
    assert SRC.count("<script>") == 3, "内联 script 块数量异常"


def test_snapshot_channel_kept_as_primary_and_fallback():
    """原快照 fetch 逐字保留、唯一，且仍位于 load() 主体链路中。"""
    anchor = "await fetch('/static/dw_snapshot.json?ts='+Date.now(),{cache:'no-store'})"
    assert SRC.count(anchor) == 1, "原快照 fetch 被改或重复"
    load_body = _slice("async function load(){", "\n/* ===== KPI cards")
    assert anchor in load_body, "快照 fetch 不再位于 load() 内"
    assert "SNAP=await r.json(); render();" in load_body, "「快照→SNAP→render()」主体链路被改"
    assert "if(!r.ok) throw new Error('HTTP '+r.status);" in load_body
    assert "快照加载失败：" in load_body, "原 catch 兜底文案被删"
    assert "let SNAP=null;" in SRC
    assert SRC.count("load();") == 1, "页面启动调用应唯一"


def test_live_endpoint_urls_exact():
    """两个实时端点 URL 与 spec §4/§5 逐字一致，且经由 fetch 发出。"""
    assert SRC.count("/api/data/catalog/tables?detail=brief") == 1
    assert SRC.count("/api/data/catalog/table?name=") == 1
    assert "encodeURIComponent(name)" in SRC, "单表查询未对表名做 URL 编码"
    assert "&limit=20" in SRC, "单表查询未带 limit=20"
    urls = re.findall(r"fetch\('([^']+)'", SRC)
    assert "/api/data/catalog/tables?detail=brief" in urls
    assert any(u.startswith("/api/data/catalog/table?name=") for u in urls)
    assert "/static/dw_snapshot.json?ts=" in urls, "双通道：快照与实时是两条独立请求"


def test_timeout_and_no_store_on_both_live_channels():
    """两个实时请求都带 cache:no-store 与 3s 超时信号。"""
    tables_fetch = ("fetch('/api/data/catalog/tables?detail=brief',"
                    "{cache:'no-store',signal:timeoutSignal()})")
    assert SRC.count(tables_fetch) == 1, "tables 探测请求未按 spec §5.1 组装"
    assert SRC.count("signal:timeoutSignal()") == 2, "tables / table 两个实时通道都应带超时"
    assert "AbortSignal.timeout(3000)" in SRC, "缺少 3000ms 硬超时（spec §5.1 字面口径）"


def test_try_catch_silent_fallback():
    """实时探测全程 try/catch，失败静默：不写 #err、不弹窗、不抛未捕获异常。"""
    probe = _slice("async function probeCatalog(){", "\n/* ===== 可折叠")
    assert "try{" in probe and "}catch(e){" in probe, "探测缺少 try/catch 兜底"
    assert "静默" in probe
    assert "getElementById('err')" not in probe, "探测失败不得写页面错误条"
    assert "openModal" not in probe, "探测失败不得弹错"
    assert "renderTableList();" in probe, "无论成败都要渲染表清单（不空白）"
    load_body = _slice("async function load(){", "\n/* ===== KPI cards")
    assert "await probeCatalog();" in load_body, "load() 未挂接实时探测"
    assert "try{ await probeCatalog(); }catch(e){" in load_body, "缺少外层双保险 try/catch"
    assert "（catalog 实时）" in SRC and "（catalog 静态快照）" in SRC, "副标题两种通道标记缺失"


def test_catalog_live_state_machine_and_snap_override():
    """成功判据 ok&&total>0、全局 CATALOG_LIVE、覆盖 SNAP.catalog、失败回落。"""
    probe = _slice("async function probeCatalog(){", "\n/* ===== 可折叠")
    assert "let CATALOG_LIVE=null;" in SRC and "let CATALOG_STATE='unknown';" in SRC
    assert "CATALOG_STATE='unknown';" in probe, "每次刷新应重置状态机，保证幂等"
    assert "live.ok!==true" in probe and "live.total>0" in probe, "成功判据不是 ok && total>0"
    assert "CATALOG_LIVE=live; CATALOG_STATE='live';" in probe
    assert "SNAP.catalog.version=live.version;" in probe, "未用 live.version 消除 v39→v43 漂移"
    assert "SNAP.catalog.tables=live.total;" in probe, "未用 live.total 消除 256→258 漂移"
    assert "setCatalogFlag('（catalog 实时）')" in probe
    assert "CATALOG_STATE='snapshot';" in probe and "CATALOG_LIVE=null;" in probe
    assert "setCatalogFlag('（catalog 静态快照）')" in probe
    assert "renderTableList();" in probe


def test_snap_structure_and_existing_renderers_intact():
    """30+ 渲染函数依赖的 SNAP 结构、CARD_DEFS、render() 顺序、导出逻辑均未被改。"""
    keys = [
        "SNAP.catalog.version", "SNAP.catalog.tables", "SNAP.catalog.registry.physical",
        "SNAP.catalog.contracts", "SNAP.cockpit", "SNAP.cockpit.hypotheses_by_status",
        "SNAP.cockpit.risks", "SNAP.generated_at", "SNAP.today", "SNAP.sources",
        "SNAP.hypotheses", "SNAP.reports", "SNAP.ops", "SNAP.ops.approvals",
        "SNAP.sync", "SNAP.audit", "SNAP.governance", "SNAP.roadmap",
        "SNAP.ah_signal", "SNAP.tasks", "SNAP.new_today_sources",
    ]
    for k in keys:
        assert k in SRC, "SNAP 关键字段被移除: %s" % k
    assert SRC.count("renderCards();renderToday();renderSources();renderGov();renderMining();renderOps();") == 1
    assert "const CARD_DEFS=[" in SRC
    assert "function dlJSON(){dl('dw_snapshot_'+SNAP.today+'.json',JSON.stringify(SNAP,null,1));}" in SRC
    assert "function dlCSV(kind){" in SRC
    for fn in ["function esc(s){", "function badge(st,map){", "function openModal(title,html){",
               "function closeModal(){", "function row(k,v){", "function dl(name,text){"]:
        assert SRC.count(fn) == 1, "既有工具函数被改: %s" % fn


def test_no_hardcoded_port_all_same_origin_relative():
    """所有 fetch 目标都是同源相对路径，无 127.0.0.1 / localhost / :8091 地址。"""
    assert "127.0.0.1" not in SRC
    assert "localhost" not in SRC
    assert ":8091" not in SRC, "出现 主机:端口 形式的硬编码地址"
    assert not re.search(r"https?://", SRC), "出现外部绝对 URL / CDN"
    urls = re.findall(r"fetch\('([^']+)'", SRC)
    assert len(urls) >= 5, "快照1 + 实时2 + 两个 kb 静态，共 5 条同源请求"
    assert all(u.startswith("/") for u in urls), urls
    assert all("://" not in u and not re.search(r":\d{2,5}(/|$)", u) for u in urls), urls
    # 8091 只允许作为运维提示文案出现，且该行不是请求地址
    lines = [ln for ln in SRC.splitlines() if "8091" in ln]
    assert lines, "缺少『待 8091 自然重启』提示文案"
    assert all("fetch(" not in ln and "://" not in ln and ":8091" not in ln for ln in lines)


def test_table_list_markup_and_filters():
    """可折叠表清单：六列表头 + 关键词搜索 + 由 by_source 动态生成的源下拉。"""
    for ident in ["catalogPanel", "catSearch", "catSource", "catBody",
                  "catNotice", "catTableWrap", "catCount", "catState"]:
        assert 'id="%s"' % ident in SRC, "缺少表清单元素 #%s" % ident
    assert "<details" in SRC and "<summary" in SRC and "</details>" in SRC, "区块不可折叠"
    assert ('<th>表名</th><th>源</th><th>频率</th><th>新鲜度</th>'
            '<th class="num">行数</th><th>状态</th>') in SRC, "六列表头不符"
    assert "placeholder=" in SRC and "全部源" in SRC
    assert "['catSearch','catSource'].forEach" in SRC, "搜索/筛选未绑定事件"
    assert "function applyCatFilter(){" in SRC
    assert "Object.keys(live.by_source||{}).sort()" in SRC, "源下拉未由 CATALOG_LIVE.by_source 动态生成"
    assert "实时通道未就绪（待 8091 自然重启）" in SRC, "无实时数据提示文案缺失"
    assert "notice.textContent='实时通道未就绪（待 8091 自然重启）'" in SRC, "无数据时未主动显示提示"


def test_row_click_reuses_openmodal_esc_and_row():
    """点行 → 单表端点 → openModal 展示 schema + 前 20 行；失败显示 reason；全部 esc。"""
    assert "body.querySelectorAll('tr[data-tname]').forEach" in SRC
    assert "openTableDetail(tr.dataset.tname)" in SRC
    detail = _slice("async function openTableDetail(name){", "\nfunction tableDetailHtml")
    assert "openModal(title," in detail, "未复用 openModal"
    assert detail.count("openModal(") >= 3, "加载态/失败/成功都应复用 openModal"
    assert "esc(reason)" in detail, "失败未显示并转义 reason"
    assert "d.ok!==true" in detail and "tableDetailHtml(d)" in detail
    htmlfn = _slice("function tableDetailHtml(d){", "\n/* 表清单：关键词搜索")
    assert "row('解析'" in htmlfn, "未复用现有 row() 工具函数"
    assert htmlfn.count("esc(") >= 8, "来自 API 的字符串未全部经 esc() 转义"
    assert "Schema" in htmlfn and "样本数据" in htmlfn
    assert "rows.slice(0,20)" in htmlfn, "样本未限制前 20 行"
    assert "headCols.map(c=>'<th>'+esc(c)+'</th>')" in htmlfn


def test_abort_signal_compatibility():
    """AbortSignal.timeout 有兼容兜底（AbortController），且有明确注释说明。"""
    block = _slice("function timeoutSignal(){", "\n/* 副标题标记")
    assert "AbortSignal.timeout(3000)" in block, "主路径未用 AbortSignal.timeout(3000)"
    assert "typeof AbortSignal!=='undefined'" in block
    assert "typeof AbortController!=='undefined'" in block, "缺少 AbortController 兼容兜底"
    assert "setTimeout(()=>ac.abort(),3000)" in block, "兼容兜底未模拟 3s 超时"
    assert "return undefined;" in block, "两者皆无时缺少静默降级"
    assert SRC.count("signal:timeoutSignal()") == 2, "两个实时通道都挂了超时信号"
    assert "兼容兜底" in SRC and "明确说明" in SRC, "未对兼容性做明确说明"


def test_set_catalog_flag_does_not_touch_render():
    """副标题标记由新增函数完成，render() 内部表达式与调用顺序逐字未改。"""
    flag_block = _slice("function setCatalogFlag(txt){", "\n/* 实时探测")
    assert "document.getElementById('sub')" in flag_block
    assert "SNAP.catalog.version" in flag_block and "SNAP.catalog.tables" in flag_block
    assert "el.textContent=base+txt;" in flag_block
    assert "openModal" not in flag_block, "标记动作不得弹窗"
    render = _slice("function render(){", "\n/* ===== tabs / 下载")
    assert "document.getElementById('sub').textContent='catalog v'+(SNAP.catalog.version||'?')" in render
    assert "renderCards();renderToday();renderSources();renderGov();renderMining();renderOps();" in render
    assert "probeCatalog" not in render, "render() 内不得夹带实时探测"


def test_sample_note_and_clamp_disclosure():
    """样本顺序说明 / limit 截断必须对用户可见。

    ts_daily 样本首行实测是 2023-07-31，不提示就会被误读成「数据断更三年」；
    这是前几轮反复踩的「假新鲜度信号」，必须在弹窗里把「样本旧」和「数据旧」分开。
    """
    htmlfn = _slice("function tableDetailHtml(d){", "\n/* 表清单：关键词搜索")
    assert "d.sample_note" in htmlfn, "未展示样本顺序说明"
    assert "esc(d.sample_note)" in htmlfn, "样本说明未经转义"
    assert "meta.freshness" in htmlfn, "未并列显示 catalog 新鲜度"
    assert "d.clamped" in htmlfn and "d.limit_applied" in htmlfn, "未披露 limit 截断"
    assert "esc(d.limit_applied)" in htmlfn, "截断值未经转义"
