# -*- coding: utf-8 -*-
"""D1 训练面板页面回归（IFS F6b / WO-D1-TRAINPAGE-20261104）。

覆盖：
1) 页面存在 + 前端准入三件套（tokens.css / nav.js / shortscreen.js）；
2) 只读语义：仅 1 个 GET episodes 接口，无 POST/写接口/训练触发；只读标记关键词齐全；
3) 诚实空态：data_unavailable / count=0 / invalid_lines 分支文案（不造数、不显示假数据）；
4) 接线：portal_api do_GET 页面 dispatch、D1 研究页入口链接、页面回链；
5) 路由基线：页面路由以 tuple 形式接线，route inventory 签名不漂移（基线 297 不变）；
6) composition：lusu-d1-flow 页面 planned → active（真落地后），manifest notes/status 同步。
"""
import importlib.util
import json
import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent.parent
PORTAL = REPO / "portal"
PAGE = PORTAL / "d1_training.html"
API_EPISODES = "/api/d1/training/episodes"


def _page():
    return PAGE.read_text(encoding="utf-8")


def test_page_exists_and_three_piece_assets():
    assert PAGE.is_file(), "缺 portal/d1_training.html"
    html = _page()
    assert len(html) > 3000
    for token in ("/static/tokens.css", "/static/nav.js", "/static/shortscreen.js",
                  "D1 训练面板", "只读"):
        assert token in html, "缺 %s" % token
    # 统一页头/回链允许，但不得自绘配色：出现裸 hex 仅在 tokens fallback 之外不接受
    assert "<style>" in html and "var(--" in html


def test_page_readonly_single_get_api():
    html = _page()
    # 只允许一个 GET 数据接口
    apis = set(re.findall(r"/api/[A-Za-z0-9_./-]+", html))
    assert apis == {API_EPISODES}, "页面出现未授权接口: %s" % sorted(apis)
    # 无任何写方法/写调用
    for bad in ("method:'POST'", 'method:"POST"', "method: 'POST'",
                'method: "POST"', "XMLHttpRequest", "navigator.sendBeacon"):
        assert bad not in html, "页面出现写调用痕迹: %s" % bad
    assert html.count("fetch(") == 1, "只应有一个只读 fetch"
    assert "cache:'no-store'" in html


def test_page_readonly_flags_and_honest_empty_state():
    html = _page()
    for token in ("只读", "不训练", "不写盘", "paper-only", "data_unavailable",
                  "不造数", "不显示假数据", "auto_training=false",
                  "trading_permitted=false", "readonly", "invalid_lines",
                  "无 episode 记录", "数据未生成"):
        assert token in html, "缺只读/诚实空态口径: %s" % token
    # 空态分支与提示都必须来自接口 note，不内置样例 episode
    assert "DATA.note" in html
    assert "不做样例" in html or "不造样例" in html


def test_portal_api_page_dispatch_wired():
    api = (PORTAL / "portal_api.py").read_text(encoding="utf-8")
    assert '("/d1/training", "/d1_training.html")' in api
    assert '"d1_training.html"' in api
    # API 路由本身是既有 F5 的（本单不改 API/训练链）
    assert '"/api/d1/training/episodes"' in api


def test_entry_links_from_d1_research_and_page():
    research = (PORTAL / "d1_research.html").read_text(encoding="utf-8")
    assert 'href="/d1/training"' in research, "D1 研究页缺训练面板入口"
    html = _page()
    assert 'href="/d1/research"' in html, "训练页缺回 D1 研究页链接"


def test_route_inventory_unchanged_page_route_not_scanned():
    """页面 tuple dispatch 不被 route inventory 扫描：签名集保持基线 297 零 diff。"""
    spec = importlib.util.spec_from_file_location(
        "route_inventory_f6b_t", REPO / "tools" / "portal_route_inventory.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    source = (PORTAL / "portal_api.py").read_text(encoding="utf-8")
    inv = mod.extract_routes(source)
    # 与 CLI 同口径：并入 route_table.py 已表化的 GET exact 路由
    table = PORTAL / "route_table.py"
    if table.is_file():
        have = {x["route"] for x in inv["GET"]["exact"]}
        for route in mod.table_exact_routes(table):
            if route not in have:
                inv["GET"]["exact"].append(
                    {"route": route, "kind": "exact", "line": 0})
                have.add(route)
        inv["GET"]["exact"].sort(key=lambda x: x["route"])
        inv["total"] = sum(len(inv[m][k]) for m in ("GET", "POST")
                           for k in ("exact", "prefix"))
    sigs = mod.signatures(inv)
    assert ("GET", "exact", "/d1/training") not in sigs
    assert ("GET", "exact", "/d1_training.html") not in sigs
    baseline = json.loads((REPO / "tools" / "portal_route_baseline.json")
                          .read_text(encoding="utf-8"))
    assert baseline.get("total") == 297
    assert set(tuple(x) for x in baseline.get("signatures") or []) == sigs


def test_composition_page_active_after_real_landing():
    manifest = yaml.safe_load(
        (REPO / "compositions" / "lusu-d1-flow" / "manifest.yaml")
        .read_text(encoding="utf-8"))
    spec = manifest["spec"]
    pages = spec["pages"]
    training = [p for p in pages if p.get("ref") == "page/d1/training"]
    assert len(training) == 1, "manifest 缺 page/d1/training"
    note = str(training[0].get("note") or "")
    assert "active" in note and "d1_training.html" in note
    status = manifest["status"]
    assert status["pages"]["active"] == 3
    assert status["pages"]["planned"] == []
    assert str(status["training_page"]).startswith("active")
    notes = "\n".join(str(x) for x in spec["notes"])
    assert "F6b" in notes and "d1_training.html" in notes
