# -*- coding: utf-8 -*-
"""路由表草案生成器单测 + 与 B0 路由基线一致性测试（A5 B4/B5，2026-10-31）。"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "portal_route_table_draft.py"
spec = importlib.util.spec_from_file_location("route_table_draft_t", TOOL)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

SRC = '''
class Handler:
    def do_GET(self):
        path = self.path
        if path == "/api/a":
            self._json({})
        elif path == "/page" or path.startswith("/page/"):
            self._portal_page()
        else:
            self.send_response(404)
    def do_POST(self):
        path = self.path
        if path == "/api/x":
            self._api_x(None)
'''


def test_extract_branches():
    rep = m.build_draft(SRC)
    assert rep["ok"]
    st = rep["stats"]
    assert st["if_branches"] == 3   # GET 两分支 + POST 一分支
    assert st["exact_routes"] == 3   # /api/a, /page, /api/x
    assert st["prefix_routes"] == 1  # /page/
    assert st["handler_identified_pct"] == 100.0
    handlers = [e["handler"] for e in rep["entries"]]
    assert "self._json" in handlers
    assert "self._portal_page" in handlers
    assert "self._api_x" in handlers


def test_unparsed_counted():
    src = "class Handler:\n    def do_GET(self):\n        if 1:\n            self._x()\n"
    rep = m.build_draft(src)
    assert rep["stats"]["unparsed_branches"] == 1


def _baseline_routes():
    p = ROOT / "tools" / "portal_route_baseline.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    out = set()
    for sig in data.get("signatures") or []:
        method, kind, route = sig
        out.add((method, kind, route))
    return out


def test_draft_covers_baseline_routes():
    """草案必须覆盖 B0 基线的全部 exact/prefix 路由（一致性回归）。"""
    src = (ROOT / "portal" / "portal_api.py").read_text(encoding="utf-8")
    rep = m.build_draft(src, ROOT / "portal" / "route_table.py")
    assert rep["ok"]
    draft = set()
    for e in rep["entries"]:
        for p in e["predicates"]:
            if p["kind"] in ("exact", "prefix"):
                draft.add((e["method"], p["kind"], p["route"]))
    base = _baseline_routes()
    missing = base - draft
    assert not missing, "草案缺失基线路由: %s" % sorted(missing)[:10]


def test_draft_stats_sane():
    src = (ROOT / "portal" / "portal_api.py").read_text(encoding="utf-8")
    rep = m.build_draft(src, ROOT / "portal" / "route_table.py")
    st = rep["stats"]
    assert st["if_branches"] >= 150
    assert st["exact_routes"] >= 150
    assert st["handler_identified_pct"] >= 95.0
    assert st["unparsed_branches"] <= 150  # 非路由 If（逻辑分支）计数，随嵌套遍历增加
