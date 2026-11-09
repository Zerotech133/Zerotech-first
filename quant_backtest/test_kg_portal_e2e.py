# -*- coding: utf-8 -*-
"""知识图谱门户面板 E2E 测试（真浏览器驱动用户旅程）。

复用 test_kg_events_e2e.py 的模式（playwright + 系统 Edge + console
零报错门槛），覆盖 kg_portal 数据层服务的三个代表面板：
  ① /kg 图谱探索：服务端渲染首屏 + 搜索框可用
  ② /kg/map 游资地图：流式骨架（冷缓存 10~30s）加载完成 + Tab 切换
  ③ /kg/canvasv2 知识画布：标题 + G6 canvas 渲染

环境约定：portal(8091) 不可达 / 无浏览器 / Neo4j 不可达 → skip。
运行：cd quant_backtest && python -m pytest test_kg_portal_e2e.py -v
"""

import pytest

PORTAL = "http://127.0.0.1:8091"
# 本文件只打本机自启服务：回环请求禁代理（safe_urlopen 内含 127.0.0.1 豁免）
from safe_urlopen import safe_urlopen  # noqa: E402

pw = pytest.importorskip("playwright.sync_api", reason="需要 pip install playwright")


@pytest.fixture(scope="module")
def live():
    import json

    try:
        with safe_urlopen(PORTAL + "/kg/stats", timeout=5) as r:
            assert json.loads(r.read().decode("utf-8")).get("ok")
    except Exception as ex:
        pytest.skip("portal/Neo4j 不可用：%s" % ex)


def _launch(p):
    for kw in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
        try:
            return p.chromium.launch(headless=True, **kw)
        except Exception:
            continue
    pytest.skip("无可用浏览器（msedge/chrome/chromium）")


@pytest.fixture(scope="module")
def page(live):
    with pw.sync_playwright() as p:
        browser = _launch(p)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        pg._e2e_errors = []

        def _on_console(m):
            if m.type != "error":
                return
            loc = m.location or {}
            if "favicon" in str(loc.get("url") or ""):
                return  # 环境噪声，见 test_kg_events_e2e
            pg._e2e_errors.append(m.text)

        pg.on("console", _on_console)
        pg.on("pageerror", lambda e: pg._e2e_errors.append(str(e)))
        pg.set_default_timeout(8000)
        yield pg
        ctx.close()
        browser.close()


# ── ① /kg 图谱探索（服务端渲染）────────────────────────────


def test_e2e_kg_explorer(page):
    page.goto(PORTAL + "/kg", wait_until="domcontentloaded")
    page.wait_for_load_state("load")
    assert "图谱探索" in page.content()  # 导航/页面标识
    kf = page.locator(".kfind")  # 探索搜索框族样式
    assert kf.count() >= 1 and kf.first.is_visible()
    assert not page._e2e_errors


# ── ② /kg/map 游资地图：流式加载 + Tab 切换 ─────────────────


def test_e2e_map_stream_and_tabs(page):
    # 流式页：文档关闭随数据算完才到（冷缓存 10~30s），goto 放宽到 60s
    page.goto(PORTAL + "/kg/map", wait_until="domcontentloaded", timeout=60000)
    # U4-A 流式骨架：数据算完骨架隐藏（冷缓存最长 ~30s）
    page.wait_for_selector("#kgmap-loading", state="hidden", timeout=40000)
    assert "游资地图" in page.content()
    geo_btn = page.locator("button:visible", has_text="地图")
    geo_btn.wait_for(state="visible")
    geo_btn.click()  # _tab → .tabc.on 迁移
    assert "on" in (geo_btn.get_attribute("class") or "")
    pane = page.locator("#kgt-geo.tabc")
    pane.wait_for(state="visible")
    assert "on" in (pane.get_attribute("class") or "")
    assert not page._e2e_errors


# ── ③ /kg/canvasv2 知识画布（HTML 平铺画布，非 G6）────────


def test_e2e_canvasv2(page):
    page.goto(PORTAL + "/kg/canvasv2", wait_until="domcontentloaded")
    page.wait_for_load_state("load")
    assert page.title() == "知识画布 v2 · 图谱"
    qi = page.locator("#qi")  # 实体搜索框
    assert qi.count() == 1 and qi.is_visible()
    assert "搜实体" in (qi.get_attribute("placeholder") or "")
    assert page.locator("input.kfind").count() >= 2  # 搜索 + 编辑键框族
    assert page.content().count("letter-spacing:1px") >= 1  # 分组标题
    assert not page._e2e_errors


def test_e2e_canvasv2_inline_scripts_compile(page):
    """回归（内联 JS 可编译）：canvasv2 曾因一处转义层级错导致整个
    script 块 SyntaxError，页面全部交互（搜索/扩散/编辑/图分析）失效。
    用浏览器 JS 引擎逐块 new Function 编译（只编译不执行），任何内联
    语法错误在此直接暴露——比 pageerror 门槛更早、更定位。"""
    page.goto(PORTAL + "/kg/canvasv2", wait_until="domcontentloaded")
    page.wait_for_load_state("load")
    res = page.evaluate(
        """() => {
            const blocks = Array.from(
                document.querySelectorAll('script:not([src])'));
            const errs = blocks.map(s => {
                try { new Function(s.textContent); return null }
                catch (e) { return String(e) }
            }).filter(Boolean);
            return {n: blocks.length, errs};
        }"""
    )
    assert res["n"] >= 1  # 检查确实跑到了脚本块
    assert res["errs"] == []
    assert not page._e2e_errors
