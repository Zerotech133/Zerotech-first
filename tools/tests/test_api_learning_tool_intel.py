# -*- coding: utf-8 -*-
"""工具情报库只读 API 测试（task-161 / W3，不依赖 8091 在线）。

覆盖：
  1) registry 资产：45 工具、字段完整、类别/置信枚举合法、分类汇总一致；
  2) API 响应：mock handler → ok=true/count/total/by_category/by_confidence/items/schema_version；
  3) 过滤与分页：category/confidence/q/limit/offset；非法参数 → 400；registry 缺失 → 503；
  4) 路由注册：portal_api.py 含路由分发 + 行数预算 ≤5200；route baseline 含 GET exact /api/learning/tool-intel；
  5) route inventory --check 零 diff（subprocess）。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REGISTRY = REPO / "agent_platform" / "registry" / "tool_intel.json"
API = REPO / "portal" / "api_learning_tool_intel.py"
PORTAL_API = REPO / "portal" / "portal_api.py"
BASELINE = REPO / "tools" / "portal_route_baseline.json"

_spec = importlib.util.spec_from_file_location("api_tool_intel_test", API)
api = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = api
_spec.loader.exec_module(api)  # type: ignore[union-attr]


class FakeHandler:
    def __init__(self):
        self.payload = None
        self.status = 200

    def _json(self, payload, status=200):
        self.payload = payload
        self.status = status


def call(qs: str = "") -> FakeHandler:
    h = FakeHandler()
    api.handle_get(h, "/api/learning/tool-intel", qs)
    return h


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1) registry 资产
# ---------------------------------------------------------------------------
def test_registry_asset():
    d = load(REGISTRY)
    assert d["schema_version"] == "tool_intel.v1"
    assert d["count"] == 45 == len(d["tools"])
    assert sum(d["by_category"].values()) == 45
    assert sum(d["by_confidence"].values()) == 45
    assert d["by_confidence"] == {"高": 16, "中": 29}
    for t in d["tools"]:
        for key in ("name", "category", "description", "capabilities", "confidence",
                    "source_files", "sources_count", "address"):
            assert key in t, f"{t.get('name')} 缺 {key}"
        assert t["category"] in api.VALID_CATEGORIES
        assert t["confidence"] in api.VALID_CONFIDENCE
        assert t["description"] and isinstance(t["capabilities"], list)


# ---------------------------------------------------------------------------
# 2) API 响应
# ---------------------------------------------------------------------------
def test_api_response():
    h = call("")
    assert h.status == 200
    p = h.payload
    assert p["ok"] is True
    assert p["count"] == 45 and p["total"] == 45
    assert len(p["items"]) == 45
    assert p["schema_version"] == "tool_intel.v1"
    assert p["by_category"]["行情"] == 12
    item = p["items"][0]
    for key in ("name", "category", "description", "capabilities", "confidence", "address"):
        assert key in item
    assert p["query"] == {"category": None, "confidence": None, "q": None, "limit": 100, "offset": 0}


# ---------------------------------------------------------------------------
# 3) 过滤 / 分页 / 错误
# ---------------------------------------------------------------------------
def test_filters_and_errors():
    h = call("category=行情")
    assert h.payload["ok"] and h.payload["total"] == 12
    assert all(t["category"] == "行情" for t in h.payload["items"])
    h = call("confidence=高")
    assert h.payload["total"] == 16 and all(t["confidence"] == "高" for t in h.payload["items"])
    h = call("q=TradingView")
    assert h.payload["total"] >= 1
    assert any("TradingView" in t["name"] for t in h.payload["items"])
    h = call("category=量化&limit=3&offset=2")
    assert h.payload["total"] == 7 and len(h.payload["items"]) == 3
    # 非法参数
    h = call("category=不存在")
    assert h.status == 400 and h.payload["error"]["code"] == "INVALID_ARGUMENT"
    h = call("limit=999")
    assert h.status == 400 and "limit" in h.payload["error"]["message"]
    h = call("confidence=低")
    assert h.payload["ok"] and h.payload["total"] == 0
    # registry 缺失 → 503
    old = api.REGISTRY
    try:
        api.REGISTRY = Path(r"C:\Temp\__no_tool_intel__.json")
        api._cache.update({"mtime": None, "data": None})
        h = call("")
        assert h.status == 503 and h.payload["error"]["code"] == "NOT_READY"
    finally:
        api.REGISTRY = old
        api._cache.update({"mtime": None, "data": None})


# ---------------------------------------------------------------------------
# 4) 路由注册 + 基线
# ---------------------------------------------------------------------------
def test_route_registered_and_baseline():
    src = PORTAL_API.read_text(encoding="utf-8")
    assert "if path == '/api/learning/tool-intel':" in src
    assert "import api_learning_tool_intel" in src
    assert "api_learning_tool_intel.handle_get(self, path, qs)" in src
    assert len(src.splitlines()) <= 5200, "portal_api.py 超行数预算"
    b = load(BASELINE)
    assert ["GET", "exact", "/api/learning/tool-intel"] in b["signatures"]
    assert b["total"] >= 298


# ---------------------------------------------------------------------------
# 5) route inventory 零 diff
# ---------------------------------------------------------------------------
def test_route_inventory_check():
    proc = subprocess.run([sys.executable, "-X", "utf8", str(REPO / "tools" / "portal_route_inventory.py"), "--check"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          cwd=str(REPO), timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK" in (proc.stdout or "") or "OK" in (proc.stderr or "")
