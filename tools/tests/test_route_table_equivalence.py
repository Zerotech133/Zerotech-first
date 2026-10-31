# -*- coding: utf-8 -*-
"""B4a-lite 路由表等价性测试（2026-10-31；WO-INTEL-BASELINE-20261031 校准）。

锁死四件事：
1. route_table.GET_EXACT 的每条路由都在 B0 基线（GET exact）内，且 handler 存在；
2. 被表化的分支已从 portal_api 移除（不再出现 `path == '<route>'`）；
3. 路由清单工具 --check 零 diff（当前基线 281 条；新增/删除路由必须同提交重生成）；
4. 基线元数据可移植：source 不得固化临时 worktree 绝对路径；2026-10-31 漂移事故的
   两条路由（黄忠 events GET exact / 诸葛 review POST prefix）必须在基线内。

重生成规则（禁止手改 tools/portal_route_baseline.json）：
  python tools/portal_route_inventory.py --write-baseline --source portal/portal_api.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "portal"))

import route_table  # noqa: E402
import portal_api  # noqa: E402


def _baseline():
    return json.loads(
        (ROOT / "tools" / "portal_route_baseline.json").read_text(encoding="utf-8"))


def _baseline_get_exact():
    data = _baseline()
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
    # 子进程会打印 ✅/❌：显式 UTF-8 输出，避免 Windows cp936 下 UnicodeEncodeError
    # 把「零 diff」误判成失败（2026-10-31 WO-INTEL-BASELINE-20261031 加固）。
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "portal_route_inventory.py"), "--check"],
                       capture_output=True, text=True, encoding="utf-8", env=env)
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "零 diff" in (r.stdout or ""), r.stdout


def test_dispatch_uses_getattr_for_handler_strings():
    """B4a-lite 回归（2026-10-31 P128）：路由表值是方法名字符串，分发必须 getattr。"""
    src = (ROOT / "portal" / "portal_api.py").read_text(encoding="utf-8")
    assert src.count("getattr(self, route_table._GET_EXACT_") >= 16
    import re as _re
    bad = _re.findall(r"route_table\._GET_EXACT_\d+\[path\]\(\)", src)
    assert bad == [], "发现对 handler 字符串直接调用: %s" % bad[:3]


def test_baseline_metadata_sane():
    """基线元数据卫生：总数自洽、可追踪，source 不固化临时 worktree 绝对路径。"""
    data = _baseline()
    sigs = data["signatures"]
    assert data["total"] == len(sigs) >= 279
    assert data.get("generated_at"), "需记录冻结时间"
    src = str(data.get("source") or "").replace("\\", "/")
    assert src, "需记录 source"
    assert ".worktrees" not in src, "source 不得指向临时 worktree: %s" % src
    assert src.rstrip("/").rsplit("/", 1)[-1] == "portal_api.py", src


def test_known_drift_routes_frozen_in_baseline():
    """2026-10-31 漂移事故锁：这两条曾未回写基线，导致改 portal_api 的合并被误拦。"""
    base = {tuple(sig) for sig in _baseline()["signatures"]}
    assert ("GET", "exact", "/api/huangzhong/events") in base
    assert ("POST", "prefix", "/api/zhuge/review/") in base

