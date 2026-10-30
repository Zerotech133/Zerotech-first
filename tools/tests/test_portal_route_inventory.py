# -*- coding: utf-8 -*-
"""portal_route_inventory 单测（A5 B0，2026-10-31）。"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "portal_route_inventory.py"
spec = importlib.util.spec_from_file_location("portal_route_inventory_t", TOOL)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

SRC = '''
import os
class Handler:
    def do_GET(self):
        path = self.path
        if path == "/api/a":
            pass
        elif path == "/api/b" or path.startswith("/api/b/"):
            pass
        elif path == "/page":
            pass
    def do_POST(self):
        path = self.path
        if path == "/api/x":
            pass
        elif path == "/api/y":
            pass
'''


def test_extract_routes():
    inv = m.extract_routes(SRC)
    got = {(meth, kind, item["route"])
           for meth in ("GET", "POST") for kind in ("exact", "prefix")
           for item in inv[meth][kind]}
    want = {
        ("GET", "exact", "/api/a"), ("GET", "exact", "/api/b"),
        ("GET", "exact", "/page"), ("GET", "prefix", "/api/b/"),
        ("POST", "exact", "/api/x"), ("POST", "exact", "/api/y"),
    }
    assert got == want, got
    assert inv["total"] == len(want)


def test_signatures_and_diff(tmp_path):
    inv = m.extract_routes(SRC)
    sigs = m.signatures(inv)
    assert ("GET", "exact", "/api/a") in sigs
    # 构造基线，删除一条 → check 应报丢失
    base = tmp_path / "baseline.json"
    data = {"signatures": sorted([list(s) for s in sigs if s != ("POST", "exact", "/api/y")])}
    base.write_text(json.dumps(data), encoding="utf-8")
    src = tmp_path / "portal_api.py"
    src.write_text(SRC, encoding="utf-8")
    rc = m.main(["--source", str(src), "--baseline", str(base), "--check"])
    assert rc == 1


def test_check_clean(tmp_path):
    src = tmp_path / "portal_api.py"
    src.write_text(SRC, encoding="utf-8")
    base = tmp_path / "b.json"
    rc = m.main(["--source", str(src), "--baseline", str(base), "--write-baseline"])
    assert rc == 0
    rc = m.main(["--source", str(src), "--baseline", str(base), "--check"])
    assert rc == 0


def test_live_repo_baseline_consistent():
    """真实仓库：基线存在时必须零 diff（B0 完成标志）。"""
    base = ROOT / "tools" / "portal_route_baseline.json"
    if not base.exists():
        return
    rc = m.main(["--check"])
    assert rc == 0, "portal_api 路由与基线不一致（拆分/改动需显式更新基线）"
