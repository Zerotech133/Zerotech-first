# -*- coding: utf-8 -*-
"""tools/tests/test_screening_page.py — 每日筛选页（portal/screening.html，task-106）。

覆盖（任务验收 ≥4，全部只读静态/HTTP 检查，不依赖浏览器）：
  1. 前端准入三件套：tokens.css / nav.js / shortscreen.js + #nav 挂载点，页面样式只用 tokens 变量（无自绘 hex）；
  2. 数据源与挂载点：fetch /static/daily_screening.json、概览/S02/H18/量学∩RS/板块/边界 各容器、
     esc/num/pct 渲染函数、S02「已暂停」标注、H18 门户精简 schema 缺失时兜底；
  3. 导航：api_common.py ops→数据运营 含「📅 每日筛选」/screening.html，nav.js 已导出该入口（105 入口）；
  4. 静态数据契约：portal/static/daily_screening.json schema/18 条 RS 交集/板块 top5；
  5. 语法：inline `<script>` 用 node --check 通过（node 缺失则 skip）；
  6. HTTP 200：门户在线时 GET /screening.html 与 /static/daily_screening.json 均 200（离线 skip）。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_screening_page.py
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "portal" / "screening.html"
NAV_JS = ROOT / "portal" / "static" / "nav.js"
API_COMMON = ROOT / "portal" / "api_common.py"
PORTAL_JSON = ROOT / "portal" / "static" / "daily_screening.json"
BASE_URL = "http://127.0.0.1:8734"


def _html() -> str:
    assert PAGE.exists(), f"页面缺失：{PAGE}"
    return PAGE.read_text(encoding="utf-8")


def _inline_scripts() -> list[str]:
    return re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", _html(), re.S | re.I)


# ------------------------------------------------------------------ 1. 三件套
def test_three_piece_tokens_nav_shortscreen():
    html = _html()
    assert '/static/tokens.css' in html, "缺 tokens.css（唯一视觉来源）"
    assert '/static/nav.js' in html, "缺 nav.js 统一顶栏"
    assert '/static/shortscreen.js' in html, "缺 shortscreen.js 短屏化"
    assert 'id="nav"' in html, "缺统一顶栏挂载点 #nav"
    style = re.search(r"<style>(.*?)</style>", html, re.S | re.I)
    assert style, "缺页面样式块"
    body = style.group(1)
    assert "var(--" in body, "样式应使用 tokens 变量"
    hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", body)
    assert not hexes, f"禁止自绘颜色 hex：{hexes[:5]}"


# ------------------------------------------------------------------ 2. 数据源/挂载点
def test_mount_points_and_data_source():
    html = _html()
    assert 'fetch("/static/daily_screening.json"' in html
    for mid in ("overview", "s02-body", "h18-body", "rs-body", "boards-body",
                "boundary", "disclaimer", "sub", "err"):
        assert f'id="{mid}"' in html, f"缺挂载点 #{mid}"
    for fn in ("function esc", "function num", "function pct", "function render"):
        assert fn in html, f"缺渲染函数 {fn}"
    assert "已暂停" in html and "S02" in html, "S02 需标注暂停"
    assert "量学信号 ∩ RS" in html
    assert "📅 每日筛选" in html
    # 门户精简 schema 无 H18 明细时的兜底说明
    assert "门户精简 schema 暂未含 H18 明细" in html
    assert "不构成投资建议" in html


# ------------------------------------------------------------------ 3. 导航
def test_nav_entry_and_exported_js():
    api = API_COMMON.read_text(encoding="utf-8")
    assert '("📅 每日筛选", "/screening.html", "screening")' in api, "api_common 缺导航入口"
    nav = NAV_JS.read_text(encoding="utf-8")
    assert "/screening.html" in nav and "每日筛选" in nav, "nav.js 未包含每日筛选入口"
    assert "screening" in nav


# ------------------------------------------------------------------ 4. 静态数据契约
def test_static_json_contract():
    assert PORTAL_JSON.exists(), "缺 portal/static/daily_screening.json（task-101 产出）"
    data = json.loads(PORTAL_JSON.read_text(encoding="utf-8"))
    assert data["schema_version"] == "daily_screening_portal.v1"
    assert data["date"] == "20261030"
    s = data["summary"]
    assert s["input"] == 5571 and s["eligible_after_S01"] == 2583
    assert s["rs_mode"] == "C" and s["rs_intersection"] == 18
    assert len(data["rs_intersection"]) == 18
    assert len(data["s02_top"]) <= 10
    assert len(data["boards_top"]) == 5
    assert data["boundary_notes"] and data["disclaimer"]


# ------------------------------------------------------------------ 5. node --check
def test_inline_js_syntax():
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用")
    js = "\n;\n".join(_inline_scripts())
    tmp = ROOT / "tmp" / "screening_inline_check.js"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(js, encoding="utf-8")
    try:
        proc = subprocess.run([node, "--check", str(tmp)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr[-800:]
    finally:
        tmp.unlink(missing_ok=True)


# ------------------------------------------------------------------ 6. HTTP 200
def _get(path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(BASE_URL + path, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:  # pragma: no cover
        pytest.skip(f"门户离线：{exc}")


def test_http_200_page_and_json():
    status, body = _get("/screening.html")
    assert status == 200 and "每日筛选" in body and "shortscreen.js" in body
    status, body = _get("/static/daily_screening.json")
    assert status == 200
    data = json.loads(body)
    assert data["schema_version"] == "daily_screening_portal.v1"
    assert data["date"] == "20261030"
