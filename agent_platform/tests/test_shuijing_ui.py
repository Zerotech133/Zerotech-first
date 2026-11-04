# -*- coding: utf-8 -*-
"""司马徽研究台 /academy/shuijing 页面契约（2026-11-03 WO-ACAD1-DARK-20261103）。

覆盖：
1) 静态契约（默认跑）：三件套/只读 API/路由/无 hex/技术框架深屏（无 os_light）。
2) Playwright 三档证据（SHUIJING_PW=1 时跑）：本地静态服务 + 19 只池夹具，
   1440/1180/390 截图，0 console error、无横向溢出、池/筛选/抽屉只读交互。
   截图目录：SHUIJING_PW_EVIDENCE_DIR（默认 C:\\Temp\\acad1_shuijing_evidence）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PORTAL = ROOT / "portal"
HTML = PORTAL / "shuijing.html"
CSS = PORTAL / "static" / "shuijing.css"
JS = PORTAL / "static" / "shuijing.js"
NGINX = PORTAL / "nginx.conf"
PW_ENABLED = os.environ.get("SHUIJING_PW") == "1"
PW_EVIDENCE = Path(os.environ.get("SHUIJING_PW_EVIDENCE_DIR", r"C:\Temp\acad1_shuijing_evidence"))

ACTIONS = ["pending_analysis"] * 36  # live 2026-11-03：全池待分析
RISKS = [["stale_data"], ["env_diff"], ["panic_buy"], [], ["stale_data", "env_diff"]]


def _fixture_payload():
    """对齐 live 实测：pool_total=36 > 设计上限 30（超限 6 只）+ stale 3d。"""
    items = []
    for i in range(36):
        items.append({
            "code": "60%04d" % (1000 + i),
            "name": "夹具标的%02d" % i,
            "industry": "研究行业%d" % (i % 5),
            "tier": "T1" if i < 3 else "T2",
            "sources": ["chanlun", "ninedim"] if i % 2 else ["chanlun"],
            "data_date": "2026-10-30",
            "freshness_days": 3,
            "expiry_date": "2026-11-02",
            "risk_flags": RISKS[i % len(RISKS)],
            "recommendation": None,
            "confidence": None,
            "price": None,
            "decision_status": "pending",
            "action": ACTIONS[i],
            "analyses": {},
            "node_exec": [],
        })
    return {
        "ok": True, "mode": "acad1_pool_review", "trading_permitted": False,
        "generated_at": "2026-11-03 07:00:00", "date": "2026-11-02",
        "pool_id": "ACAD1-REC-20261102", "owner": "ACAD1",
        "data_date": "2026-10-30", "stale": True, "freshness_days": 3,
        "summary": {
            "date": "2026-11-02", "pool_id": "ACAD1-REC-20261102",
            "pool_total": 36, "reviewed": 0, "pending": 36,
            "by_action": {"buy": 0, "watch": 0, "avoid": 0, "pending_analysis": 36},
            "by_tier": {"T1": 0, "T2": 36},
        },
        "plan": {"selected": 0, "watch": 0, "avoid": 0, "total_weight": 0},
        "items": items,
    }


# --------------------------------------------------------------------------
# 1) 静态契约
# --------------------------------------------------------------------------
def test_three_piece_and_short_screen():
    txt = HTML.read_text(encoding="utf-8")
    assert "/static/tokens.css" in txt
    assert "/static/nav.js" in txt
    assert "/static/shortscreen.js" in txt
    assert "/static/os_light.css" not in txt, "技术框架深屏不得再依赖浅色 os_light"
    assert len(re.findall(r"<h2[ >]", txt)) >= 6


def test_framework_dark_baseline():
    """技术框架深屏：tokens 深色、1210 宽、10px 圆角细边框、蓝强调，无浅色依赖。"""
    txt = HTML.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    assert "/static/os_light.css" not in txt
    assert "技术框架深屏" in css, "缺少技术框架深色覆盖段"
    section = css.split("技术框架深屏", 1)[1]
    assert "color-scheme: dark" in section
    for token in ("--bg-page", "--bg-raised", "--text", "--border"):
        assert token in section, "深屏必须使用 tokens 深色变量 %s" % token
    assert "max-width: 1210px" in section
    assert "font-size: 13.2px" in section       # 正文 13–13.5
    assert "font-size: 23px" in section         # h1 22–24
    assert "border-radius: 10px" in section
    assert "box-shadow: 0 1px 2px" in section   # 低阴影
    assert "var(--link)" in section and "var(--os-blue)" in section
    assert "body::before { background: none; }" in section


def test_honest_limit_stale_and_main_padding():
    """P1 诚实性：KPI/池标题显示“超限 N 只 / stale Xd”；.main.wrap padding 真正命中。"""
    txt = HTML.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    assert 'id="kpi-limit"' in txt and 'id="pool-limit-tag"' in txt
    # 诚实提示由页面内联只读脚本渲染（不改 shuijing.js/API）：超限 + stale 天数必须直说
    assert "超限" in txt and "stale " in txt and "CAP = 30" in txt
    assert "MutationObserver" in txt and "fresh-tag" in txt
    assert ".pool-limit" in css
    # HTML 是 <main class="main wrap">：选择器必须 .main.wrap（.wrap .main 不命中）
    assert ".main.wrap" in css
    assert ".wrap .main" not in css
    assert "padding: 18px 18px 46px" in css


def test_readonly_and_api():
    txt = HTML.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")
    assert 'fetch("/api/acad1/pool"' in js
    assert "no-store" in js
    assert "只读" in txt
    assert "不自动下单" in txt or "不接实盘" in txt
    for sid in ("kpi-pool", "pool-rows", "risk-list", "drawer", "error-banner"):
        assert 'id="' + sid + '"' in txt
    # 只读：无写请求
    for bad in ("method: \"POST\"", "method: 'POST'", "XMLHttpRequest", "sendBeacon"):
        assert bad not in js


def test_route():
    nginx = NGINX.read_text(encoding="utf-8")
    assert "location = /academy/shuijing" in nginx
    assert "try_files /shuijing.html" in nginx


def test_no_hardcoded_hex():
    css = CSS.read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}", css), "页面 CSS 禁止自绘 hex 色"


# --------------------------------------------------------------------------
# 2) Playwright 三档证据（默认跳过；SHUIJING_PW=1 时运行）
# --------------------------------------------------------------------------
@pytest.mark.skipif(not PW_ENABLED, reason="设 SHUIJING_PW=1 才跑 Playwright 证据（默认不依赖浏览器）")
def test_playwright_three_viewports(tmp_path):
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as _p:
            _p.chromium.launch().close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip("playwright/chromium 不可用：%s" % exc)

    fixture = _fixture_payload()
    api_calls = {"n": 0}

    class _Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(PORTAL), **kwargs)

        def do_GET(self):  # noqa: N802
            path = self.path.split("?")[0]
            if path == "/api/acad1/pool":
                api_calls["n"] += 1
                body = json.dumps(fixture, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            return super().do_GET()

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % port
    PW_EVIDENCE.mkdir(parents=True, exist_ok=True)
    shots, overflow, errors = [], {}, []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("console", lambda m: errors.append("%s: %s" % (m.type, m.text))
                    if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append("pageerror: %s" % e))
            page.goto(base + "/shuijing.html", wait_until="load")
            page.wait_for_selector("#pool-rows .pool-row", timeout=15000)
            assert page.locator("#kpi-pool").inner_text().strip() == "36"
            assert page.locator("#pool-rows .pool-row").count() == 36
            assert page.locator("#risk-list li").count() >= 1
            # 诚实提示：超限 6 只 / stale 3d（KPI 脚注 + 池标题），不能只写“设计上限 30”
            limit_text = page.locator("#kpi-limit").inner_text()
            assert "超限 6 只" in limit_text and "stale 3d" in limit_text, limit_text
            assert "超限 6 只" in page.locator("#pool-limit-tag").inner_text()
            # 筛选：已复盘 0 / 待复盘 36 / 全部 36
            page.click('.tab[data-filter="reviewed"]')
            assert page.locator("#pool-rows .pool-row").count() == 0
            page.click('.tab[data-filter="pending"]')
            assert page.locator("#pool-rows .pool-row").count() == 36
            page.click('.tab[data-filter="all"]')
            assert page.locator("#pool-rows .pool-row").count() == 36
            # 点击抽屉（只读）：打开后含边界文案，Esc 关闭
            page.locator("#pool-rows .pool-row").first.click()
            assert page.locator("#drawer.is-open").count() == 1
            assert "只读研究" in page.locator("#drawer-checks").inner_text()
            page.keyboard.press("Escape")
            page.wait_for_timeout(250)
            assert page.locator("#drawer.is-open").count() == 0
            for label, w, h in (("1440", 1440, 900), ("1180", 1180, 900), ("390", 390, 844)):
                page.set_viewport_size({"width": w, "height": h})
                page.wait_for_timeout(250)
                overflow[label] = page.evaluate(
                    "() => [document.documentElement.scrollWidth, window.innerWidth]")
                path = PW_EVIDENCE / ("acad1_shuijing_%s.png" % label)
                page.screenshot(path=str(path), full_page=True)
                shots.append({"viewport": label, "path": str(path),
                              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            browser.close()
    finally:
        server.shutdown()
        server.server_close()

    assert api_calls["n"] >= 1
    assert errors == [], "console error 必须为 0：%r" % errors
    for label, (scroll_w, inner_w) in overflow.items():
        assert scroll_w <= inner_w + 1, "%s 横向溢出：scrollWidth=%s innerWidth=%s" % (label, scroll_w, inner_w)
    assert len(shots) == 3
    (PW_EVIDENCE / "acad1_shuijing_evidence.json").write_text(json.dumps(
        {"mode": "36_pool_stale3_fixture", "viewports": shots, "console_errors": errors,
         "overflow": overflow, "api_calls": api_calls["n"]},
        ensure_ascii=False, indent=1), encoding="utf-8")
