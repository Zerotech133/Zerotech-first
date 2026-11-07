# -*- coding: utf-8 -*-
"""K266 事件图谱查询面板 E2E 测试（真浏览器驱动用户旅程）。

测试金字塔顶层，与下两层互补：
  单元 test_kg_events_portal.py（mock 隔离）
  集成 test_kg_events_integration.py（真实 Neo4j / 文件 / HTTP 端点）
  E2E  本文件（playwright 驱动系统 Edge，验证只有浏览器能验的行为）

覆盖旅程：
  ① 态势条 5 格零告警 + 市场 Tab（5 实 + 3 预留灰置）+ G6 canvas 渲染
  ② 查询框联想（250ms 防抖 fetch）→ 点候选下钻 focusCo（#lh 公司视图）
  ③ 事件行内公司 span 下钻 + 「📄 原文」弹层 openDoc + Escape 关闭
  ④ 市场 Tab 导航（URL / .on 态 / 重渲染）
全程收集 console error / pageerror，任一用例结束时页面零报错。

环境约定：
  · portal(8091) 不可达或图谱暂无事件 → skip
  · playwright 未安装 / 无浏览器（msedge→chrome→bundled 链）→ skip
  · 公司样本从 /kg/events/list 动态取，不锁具体数据
运行：cd quant_backtest && python -m pytest test_kg_events_e2e.py -v
"""

import json
import urllib.parse

import pytest

PORTAL = "http://127.0.0.1:8091"
# 本文件只打本机自启服务：回环请求禁代理（safe_urlopen 内含 127.0.0.1 豁免）
from safe_urlopen import safe_urlopen  # noqa: E402

pw = pytest.importorskip("playwright.sync_api", reason="需要 pip install playwright")


@pytest.fixture(scope="module")
def sample():
    """从真实端点取一行事件作交互样本（公司名/代码/原文 id）。"""
    try:
        with safe_urlopen(PORTAL + "/kg/events/list", timeout=5) as r:
            rows = json.loads(r.read().decode("utf-8"))["rows"]
    except Exception as ex:
        pytest.skip("portal(8091) 不可用：%s" % ex)
    if not rows:
        pytest.skip("图谱暂无入图事件")
    return rows[0]


def _launch(p):
    """msedge → chrome → bundled chromium 三级回退，全失败退化为 skip。"""
    for kw in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
        try:
            return p.chromium.launch(headless=True, **kw)
        except Exception:
            continue
    pytest.skip("无可用浏览器（msedge/chrome/chromium）")


@pytest.fixture(scope="module")
def page(sample):
    with pw.sync_playwright() as p:
        browser = _launch(p)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        pg._e2e_errors = []

        def _on_console(m):
            if m.type != "error":
                return
            # favicon 404 是浏览器自动请求的环境噪声，不算页面缺陷；
            # 真资源（js/css/fetch）404 仍会进入错误清单
            loc = m.location or {}
            if "favicon" in str(loc.get("url") or ""):
                return
            pg._e2e_errors.append(m.text)

        pg.on("console", _on_console)
        pg.on("pageerror", lambda e: pg._e2e_errors.append(str(e)))
        pg.set_default_timeout(8000)
        yield pg
        ctx.close()
        browser.close()


def _open(page):
    page.goto(PORTAL + "/kg/events")
    page.wait_for_selector("#evlist .evrow")  # INIT 数据渲染完成标志
    return page


# ── ① 页面首屏：态势条 / Tab / G6 canvas ────────────────────


def test_e2e_first_paint(page, sample):
    _open(page)
    assert page.title() == "事件图谱 · K266"
    cards = page.locator(".situgrid .sc")
    assert cards.count() == 5
    labels = [cards.nth(i).locator(".sl").inner_text() for i in range(5)]
    assert labels == ["NEO4J 连接", "入图事件", "最新入图", "影子管线", "更新节奏"]
    assert "已连接" in cards.nth(0).inner_text()
    assert page.locator(".situgrid .sc.w").count() == 0  # 无告警格
    assert "条" in cards.nth(1).inner_text() and "标的" in cards.nth(1).inner_text()
    # 市场 Tab：5 个实 Tab + 3 个预留灰置；默认「全部」选中
    tabs = page.locator(".tabs a.tab")
    assert tabs.count() == 5
    assert "on" in (tabs.nth(0).get_attribute("class") or "")
    assert tabs.nth(0).inner_text() == "全部"
    assert page.locator(".tabs .tab.off").count() == 3
    # 事件列表按 INIT.edges 渲染
    assert page.locator("#evlist .evrow").count() >= 1
    # G6 canvas 已渲染且容器尺寸正常（#evg 高 460px）
    cv = page.locator("#evg canvas")
    assert cv.count() >= 1
    box = cv.first.bounding_box()
    assert box and box["width"] > 100 and box["height"] > 100
    assert not page._e2e_errors


# ── ② 搜索联想 → 下钻（focusCo）────────────────────────────


def test_e2e_search_suggest_drilldown(page, sample):
    _open(page)
    name = sample["an"]
    page.fill("#qi", name)
    page.wait_for_selector("#hits .hit")  # 防抖+fetch 后出现候选
    hits = page.locator("#hits .hit")
    target, found = None, False
    for i in range(hits.count()):
        if name in hits.nth(i).inner_text():
            target, found = hits.nth(i), True
            break
    assert found, "联想候选缺少目标公司"
    target.click()  # 候选点击 → focusCo
    page.wait_for_function(
        "document.getElementById('lh').textContent.includes('事件 ·')"
    )
    lh = page.locator("#lh").inner_text()
    assert name in lh and "(" in lh  # 「事件 · 公司 (N)」
    assert "图谱全景" in lh and "K线研判" in lh  # 下钻附带跳转入口
    assert page.locator("#evlist .evrow").count() >= 1
    assert not page._e2e_errors


# ── ③ 行内公司下钻 + 原文弹层 + Escape ──────────────────────


def test_e2e_row_company_and_doc_modal(page, sample):
    _open(page)
    co = page.locator("#evlist .evrow .co").first  # 行内公司 span 入口
    name = co.inner_text()
    co.click()
    page.wait_for_function(
        "document.getElementById('lh').textContent.includes('事件 ·')"
    )
    assert name in page.locator("#lh").inner_text()
    # 下钻后的列表行仍带原文链接 → 打开弹层
    page.locator("#evlist .evrow .emeta a").first.click()
    page.wait_for_selector("#modal", state="visible")
    page.wait_for_function(  # fetch 完成替换标题
        "var t=document.getElementById('mtitle').textContent;t && t!=='加载中…'"
    )
    mbody = page.locator("#mbody")
    assert len(mbody.locator(".mtext").inner_text()) > 50  # 真原文而非占位
    assert mbody.locator(".mmeta").count() == 1  # 源/字数/doc_id
    page.keyboard.press("Escape")  # 键盘关闭
    page.wait_for_function(
        "getComputedStyle(document.getElementById('modal')).display==='none'"
    )
    assert not page._e2e_errors


# ── ④ 市场 Tab 导航 ────────────────────────────────────────


def test_e2e_market_tab_navigation(page, sample):
    try:
        with safe_urlopen(PORTAL + "/kg/events/situ", timeout=5) as r:
            mk = json.loads(r.read().decode("utf-8"))["inv_markets"]
    except Exception as ex:
        pytest.skip("portal(8091) 不可用：%s" % ex)
    if "主板" not in mk:
        pytest.skip("当前无主板入图事件")
    _open(page)
    page.locator(".tabs a.tab", has_text="主板").click()
    page.wait_for_load_state("domcontentloaded")
    assert "market=主板" in urllib.parse.unquote(page.url)
    assert page.locator(".tabs a.tab.on").inner_text() == "主板"
    page.wait_for_selector("#evlist .evrow")  # 过滤后仍有主板事件
    assert page.locator(".situgrid .sc.w").count() == 0
    assert not page._e2e_errors


# ── ⑤ G6 canvas 点击：点节点下钻 / 点边看原文 ───────────────


def _modal_shown(page):
    return page.evaluate(
        "getComputedStyle(document.getElementById('modal')).display==='flex'"
    )


def _focused(page):
    return page.evaluate("document.getElementById('lh').textContent.includes('事件 ·')")


def test_e2e_graph_canvas_click(page, sample):
    """力导向布局坐标随机，在中心区域多次尝试点击：
    命中节点 → focusCo（#lh 公司视图）；命中边 → openDoc（modal）。
    两者取其一即证明 canvas 事件链路（node:click/edge:click）通。"""
    _open(page)
    page.wait_for_timeout(1500)  # d3-force 布局收敛
    box = page.locator("#evg canvas").first.bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    responded = False
    for dx, dy in ((0, 0), (35, 20), (-30, -25), (15, -45)):
        page.mouse.click(cx + dx, cy + dy)
        page.wait_for_timeout(600)  # fetch 往返
        if _modal_shown(page) or _focused(page):
            responded = True
            break
    assert responded, "canvas 中心区域点击无节点/边响应"
    if _modal_shown(page):
        # 点边打开的弹层：顺带验证点遮罩关闭（mbox 居中，边角属遮罩）
        page.wait_for_function(
            "var t=document.getElementById('mtitle').textContent;t && t!=='加载中…'"
        )
        page.mouse.click(8, 8)
        page.wait_for_function(
            "getComputedStyle(document.getElementById('modal')).display==='none'"
        )
    assert not page._e2e_errors


# ── ⑥ 查询表单 GET 提交（q + 方向下拉）─────────────────────


def test_e2e_form_submit_filters(page, sample):
    name, direction = sample["an"], (sample.get("dir") or "").strip()
    if direction not in ("利好", "中性", "利空"):
        pytest.skip("样本事件方向非标准三值：%r" % direction)
    _open(page)
    page.fill("#qi", name)
    page.select_option("select[name=dir]", direction)
    page.click(".kfbtn")  # form GET → 页面跳转
    page.wait_for_url("**/kg/events?*")
    u = urllib.parse.unquote(page.url)
    assert "market=全部" in u  # hidden input 全量提交
    assert "q=" in u and ("dir=" + direction) in u
    page.wait_for_selector("#evlist .evrow")  # 样本行自身必在结果内
    assert page.input_value("#qi") == name  # 服务端回显
    assert page.locator("#evlist .bdgx").first.inner_text() == direction
    assert not page._e2e_errors


# ── ⑦ 下钻返回全部 / 重置：导航闭环 ────────────────────────


def test_e2e_back_to_all_and_reset(page, sample):
    _open(page)
    page.locator("#evlist .evrow .co").first.click()
    page.wait_for_function(
        "document.getElementById('lh').textContent.includes('事件 ·')"
    )
    page.locator("#lh a", has_text="返回全部").click()  # reload 回全量
    page.wait_for_load_state()
    page.wait_for_selector("#evlist .evrow")
    assert "事件列表" in page.locator("#lh").inner_text()
    page.locator("a.tab", has_text="重置").click()  # href 无参
    page.wait_for_load_state()
    assert page.url == PORTAL + "/kg/events"  # query 清空
    assert not page._e2e_errors
