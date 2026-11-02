# -*- coding: utf-8 -*-
"""曹操军团指挥台 v2 页面契约（2026-11-03 WO-CAOCAO-PAGE-20261103）。

覆盖两类：
1) 静态契约（默认跑、不依赖浏览器）：三件套/只读/四区/首屏决策优先/真实工单总线/
   筛选/事件流/决策 API v2/无静态 16/席位下移/短屏。
2) Playwright 三档证据（设 CAOCAO_PW=1 时才跑；本地起只读静态服务器 + 15 单夹具 API）：
   1440/1180/390 截图、0 console error、无横向溢出、筛选与事件流交互。
   截图目录：CAOCAO_PW_EVIDENCE_DIR（默认 C:\\Temp\\caocao_page_evidence）。
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
HTML = PORTAL / "dev_legion_v2.html"
CSS = PORTAL / "static" / "dev_legion_v2.css"
JS = PORTAL / "static" / "dev_legion_v2.js"

PW_ENABLED = os.environ.get("CAOCAO_PW") == "1"
PW_EVIDENCE = Path(os.environ.get("CAOCAO_PW_EVIDENCE_DIR", r"C:\Temp\caocao_page_evidence"))

# ---- Playwright 夹具：15 单（9 在制 / 5 待复核 / 1 其他）+ 3 决策 + 10 事件 ----
_FIXTURE_WORKORDERS = (
    [{"id": "WO-FIX-DOING-%02d" % i, "owner": "DSH-Lead", "status": "doing",
      "created": "2026-11-03 06:%02d:00" % i, "desc": "夹具在制 %02d：真实总线只读渲染" % i}
     for i in range(9)]
    + [{"id": "WO-FIX-REVIEW-%02d" % i, "owner": "ARCH1", "status": "review",
        "created": "2026-11-02 15:%02d:00" % i, "desc": "夹具待复核 %02d：等质量门" % i}
       for i in range(5)]
    + [{"id": "WO-FIX-OTHER-01", "owner": "QA1", "status": "blocked",
        "created": "2026-11-01 10:00:00", "desc": "夹具阻塞单：验证状态标签降级"}]
)
_FIXTURE_EVENTS = [
    {"ts": "2026-11-03 06:%02d:00" % i, "kind": "commit" if i % 3 else "task_start",
     "agent": "dev" if i % 2 else "DSH-Lead", "task": "WO-FIX-20261103-%02d" % i}
    for i in range(10)
]
_FIXTURE_DECISIONS = [
    {"title": "v1.1 批文 0/1", "due": "11-02 12:00", "owner": "ARCH1", "high": True,
     "desc": "滑期；补签须注明实际时间，不倒签。", "action": "补签/记滑期"},
    {"title": "11 席回执 0/11", "due": "11-03 09:30", "owner": "ARCH1/G1/G2", "high": False,
     "desc": "11 席能力迭代回执仍为 0；未回=未完成。", "action": "分组催办"},
    {"title": "跨席工单回流", "due": "今日", "owner": "ARCH1", "high": True,
     "desc": "跨席工单在工单总线；由 ARCH1 决定回流规则。", "action": "决定派工规则"},
]


def _fixture_payload():
    return {
        "schema": "legion_screen_contract/v1",
        "generated_at": "2026-11-03T06:56:00",
        "readonly": True,
        "ok": True,
        "errors": [],
        "v2": {"readonly": True, "extras": ["workorder_bus", "decisions", "events"]},
        "extensions": ["v1.1_quant_real", "v2_workorder_bus", "v2_decisions", "v2_events"],
        "seats": [{"id": "ARCH1", "name": "曹操", "role_title": "研发总架构师",
                   "group": "研发部", "grade": "E3", "discipline": "architecture",
                   "report_to": "A0", "model": "fixture"}],
        "metrics": {"dev_tasks_recent": 21, "dev_merged_recent": 7,
                    "dev_capture_share_recent": 0.25, "dev_leadtime_p50_min": 40,
                    "owner_legal_rate_recent": 1.0, "e3_trigger": 0,
                    "verification_rate": 0.9, "window_days": 7},
        "governance": {"approval_ok": True, "merge_approval_coverage": 1.0},
        "redlines": [{"id": "RL-FIX", "ok": True}],
        "workorder_bus": _FIXTURE_WORKORDERS,
        "work_in_progress": [],
        "decisions": _FIXTURE_DECISIONS,
        "events": _FIXTURE_EVENTS,
    }


# --------------------------------------------------------------------------
# 1) 静态契约
# --------------------------------------------------------------------------
def test_three_piece_and_short_screen():
    txt = HTML.read_text(encoding="utf-8")
    assert "/static/tokens.css" in txt
    assert "/static/nav.js" in txt
    assert "/static/shortscreen.js" in txt
    assert "/static/os_light.css" in txt
    assert len(re.findall(r"<h2[ >]", txt)) >= 6


def test_live_api_readonly():
    txt = HTML.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")
    assert "fetch(API_V2" in js
    assert 'const API_V2 = "/api/dev/legion"' in js
    assert "workorder_bus" in js
    assert "no-store" in js
    assert "只读" in txt
    assert "不写" in txt or "不派单" in txt
    assert 'href="/hermes/"' in txt
    assert "底座：零界 + Hermes" in txt
    assert "去 Hermes 派工" in txt


def test_four_zones():
    txt = HTML.read_text(encoding="utf-8")
    for zone in ("zone-bing", "zone-zhang-work", "zone-men", "zone-ling"):
        assert 'id="' + zone + '"' in txt
    for ch in ("兵", "仗", "门", "令"):
        assert ch in txt


def test_no_hardcoded_hex_in_page_css():
    css = CSS.read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}", css), "页面 CSS 禁止自绘 hex 色"


def test_nav_entry_and_v1_link():
    nav = (PORTAL / "static" / "nav.js").read_text(encoding="utf-8")
    assert "/dev_legion_v2.html" in nav
    v1 = (PORTAL / "dev_legion.html").read_text(encoding="utf-8")
    assert "/dev_legion_v2.html" in v1


def test_today_board_first_screen():
    """首屏决策优先：今日三件事在 KPI/席位之前，明确断言 v1.1/11 席/跨席。"""
    txt = HTML.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")
    assert 'id="today-board"' in txt and 'id="today-list"' in txt
    assert "今日必须拍板" in txt
    for kw in ("v1.1", "11 席", "跨席"):
        assert kw in js, "今日三件事必须覆盖 %s" % kw
    assert txt.index('id="today-board"') < txt.index('class="kpi-row"')
    assert txt.index('id="today-board"') < txt.index('id="seat-grid"')
    assert "DATA.decisions" in js and "DECISION_SNAPSHOT" in js
    assert "renderToday" in js


def test_workorder_bus_real_and_filters():
    """工单区用真实 DATA.workorder_bus；无静态 16 文案；全部/在制/待复核筛选。"""
    txt = HTML.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")
    for dead in ("16 人工快照", "跨席快照 16", "跨席 16", "人工维护 · 待治理 API"):
        assert dead not in txt, "旧静态文案未清理：%r" % dead
        assert dead not in js
    for f in ("all", "doing", "review"):
        assert 'data-work-filter="%s"' % f in txt
    assert "workorder_bus" in js and "renderWip" in js
    assert "WORK_FILTER" in js and "setWorkFilter" in js
    assert 'id="work-foot"' in txt and "workorder_bus" in txt
    # 徽标数量动态、非硬编码
    assert "tab-all-count" in txt and "tab-doing-count" in txt and "tab-review-count" in txt


def test_workorder_count_is_dynamic_not_hardcoded():
    """工单数是实时数据（live 曾 15→5）：页面不得硬编码 15/16 单，一律按 API 长度渲染。"""
    txt = HTML.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")
    blob = txt + "\n" + js
    for bad in ("15 单", "16 单", "跨席 15", "跨席 16", "CROSS_SEAT_SNAPSHOT"):
        assert bad not in blob, "页面不得硬编码静态工单数：%r" % bad
    # 计数来自 API 数组长度（_workRows().length / all.length），徽标由 JS 写回
    assert "bus.length" in js or "_workRows().length" in js
    assert "$(\"work-count\").textContent = String(all.length)" in js
    assert "$(\"run-sub\").textContent" in js


def test_events_card():
    """事件流卡片：API events（10 条）显示最近 8 条，字段 ts/kind/agent/task，空态明确。"""
    txt = HTML.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")
    assert 'id="events-card"' in txt and 'id="event-list"' in txt
    assert "事件流" in txt
    assert "EVENT_LIMIT = 8" in js and "DATA.events" in js
    for field in ("ts", "kind", "agent", "task"):
        assert "e.%s" % field in js or "e[%r]" % field in js
    assert "暂无事件" in js and "slice(-EVENT_LIMIT)" in js


def test_decisions_api_v2_and_fallback():
    """决策队列用 DATA.decisions（3 项）并标 API v2；无 API 才用静态快照+时点。"""
    txt = HTML.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")
    assert 'id="decision-list"' in txt and 'id="decision-count"' in txt
    assert "DATA.decisions" in js
    assert '"API v2"' in js
    assert "DECISION_SNAPSHOT_AS_OF" in js and "DECISION_SNAPSHOT" in js
    assert "无 API 时回退人工快照" in txt


def test_readonly_no_write_and_hermes():
    """只读：无写请求/写接口；Hermes 入口与底座页脚保留；按钮只跳转。"""
    txt = HTML.read_text(encoding="utf-8")
    js = JS.read_text(encoding="utf-8")
    for bad in ("method: \"POST\"", "method: 'POST'", "XMLHttpRequest",
                ".post(", "sendBeacon"):
        assert bad not in js, "页面不得包含写请求：%r" % bad
    assert txt.count('href="/hermes/"') >= 2
    assert "不写库" in txt
    assert "底座：零界 + Hermes" in txt
    assert "只读" in txt


def test_seats_moved_below_workorder():
    """席位卡下移：席位面板在工单总线之后。"""
    txt = HTML.read_text(encoding="utf-8")
    assert txt.index('id="zone-zhang-work"') < txt.index('id="seat-grid"')


# --------------------------------------------------------------------------
# 2) Playwright 三档证据（默认跳过，CAOCAO_PW=1 时运行）
# --------------------------------------------------------------------------
@pytest.mark.skipif(not PW_ENABLED, reason="设 CAOCAO_PW=1 才跑 Playwright 证据（默认不依赖浏览器）")
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
            if self.path.split("?")[0] == "/api/dev/legion":
                api_calls["n"] += 1
                body = json.dumps(fixture, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.split("?")[0] == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            return super().do_GET()

        def log_message(self, *args):  # 静默
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base = "http://127.0.0.1:%d" % port
    PW_EVIDENCE.mkdir(parents=True, exist_ok=True)
    shots, overflow, console_errors = [], {}, []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("console", lambda m: console_errors.append("%s: %s" % (m.type, m.text))
                    if m.type == "error" else None)
            page.on("pageerror", lambda e: console_errors.append("pageerror: %s" % e))
            page.goto(base + "/dev_legion_v2.html", wait_until="load")
            page.wait_for_selector("#today-list .today-item", timeout=15000)
            assert page.locator("#today-list .today-item").count() == 3
            assert page.locator("#work-rows .work-row").count() == 15
            assert page.locator("#event-list .event-row").count() == 8
            assert "API v2" in page.locator("#decision-list").inner_text()
            # 筛选：在制 9 / 待复核 5 / 全部 15
            page.click('[data-work-filter="doing"]')
            assert page.locator("#work-rows .work-row").count() == 9
            page.click('[data-work-filter="review"]')
            assert page.locator("#work-rows .work-row").count() == 5
            page.click('[data-work-filter="all"]')
            assert page.locator("#work-rows .work-row").count() == 15
            for label, width, height in (("1440", 1440, 900), ("1180", 1180, 900), ("390", 390, 844)):
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_timeout(250)
                overflow[label] = page.evaluate(
                    "() => [document.documentElement.scrollWidth, window.innerWidth]")
                path = PW_EVIDENCE / ("caocao_dev_legion_v2_%s.png" % label)
                page.screenshot(path=str(path), full_page=True)
                shots.append({"viewport": label, "path": str(path),
                              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            browser.close()
    finally:
        server.shutdown()
        server.server_close()

    assert api_calls["n"] >= 1
    assert console_errors == [], "console error 必须为 0：%r" % console_errors
    for label, (scroll_w, inner_w) in overflow.items():
        assert scroll_w <= inner_w + 1, "%s 横向溢出：scrollWidth=%s innerWidth=%s" % (label, scroll_w, inner_w)
    assert len(shots) == 3
    (PW_EVIDENCE / "caocao_page_evidence.json").write_text(json.dumps(
        {"viewports": shots, "console_errors": console_errors,
         "overflow": overflow, "api_calls": api_calls["n"]},
        ensure_ascii=False, indent=1), encoding="utf-8")
