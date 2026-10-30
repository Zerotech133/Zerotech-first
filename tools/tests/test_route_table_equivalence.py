# -*- coding: utf-8 -*-
"""B4a-lite 路由表等价性测试（2026-10-31）。

锁死三件事：
1. route_table.GET_EXACT 的每条路由都在 B0 基线（GET exact）内，且 handler 存在；
2. 被表化的分支已从 portal_api 移除（不再出现 `path == '<route>'`）；
3. 路由清单工具 --check 仍零 diff（表键 + 剩余分支 = 279 条基线）。
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "portal"))

import route_table  # noqa: E402
import portal_api  # noqa: E402


def _baseline_get_exact():
    data = json.loads((ROOT / "tools" / "portal_route_baseline.json").read_text(encoding="utf-8"))
    return {tuple(sig) for sig in data["signatures"] if sig[0] == "GET" and sig[1] == "exact"}


def test_table_routes_in_baseline_and_handlers_exist():
    base = _baseline_get_exact()
    assert len(route_table.GET_EXACT) == 32, len(route_table.GET_EXACT)
    for route, handler in route_table.GET_EXACT.items():
        assert ("GET", "exact", route) in base, route
        assert hasattr(portal_api.Handler, handler), (route, handler)


def test_table_routes_removed_from_legacy_chain():
    src = (ROOT / "portal" / "portal_api.py").read_text(encoding="utf-8")
    for route in route_table.GET_EXACT:
        assert ("path == %r" % route) not in src, "仍存在旧分支: %s" % route


def test_route_inventory_still_zero_diff():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "portal_route_inventory.py"), "--check"],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
