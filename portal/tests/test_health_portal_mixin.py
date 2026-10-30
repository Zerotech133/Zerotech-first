# -*- coding: utf-8 -*-
"""B2b 健康/观测拆分不变量：锁对象共享、缓存归属、方法经 MRO 可见（2026-10-31）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "portal"))

import api_health_portal  # noqa: E402
import portal_api  # noqa: E402


def test_lock_same_object():
    assert portal_api._lock is api_health_portal._lock


def test_caches_moved_out_of_portal_api():
    assert not hasattr(portal_api, "_hcache")
    assert not hasattr(portal_api, "_jcache")
    assert hasattr(api_health_portal, "_hcache")
    assert hasattr(api_health_portal, "_jcache")


def test_methods_visible_via_mro():
    for name in ("_api_health", "_api_jev_stack", "_sys_health", "_api_ctl_alert"):
        assert hasattr(portal_api.Handler, name), name
