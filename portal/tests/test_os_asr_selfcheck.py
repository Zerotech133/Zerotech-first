# -*- coding: utf-8 -*-
"""ASR 自检守卫：路由存在且能返回自检结果。"""

import os
import sys

PORTAL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PORTAL not in sys.path:
    sys.path.insert(0, PORTAL)

import api_os_goal as A  # noqa: E402
import os_asr  # noqa: E402
import os_goal_store as S  # noqa: E402


def test_selfcheck_route_returns_result(tmp_path, monkeypatch):
    store = S.OsGoalStore(str(tmp_path / "os_goals"))
    monkeypatch.setattr(
        os_asr,
        "selfcheck",
        lambda: {
            "ok": False,
            "engine": "sensevoice_local",
            "error": "simulated errno22",
        },
    )
    st, d = A.handle_get(store, "/api/os/asr/selfcheck")
    assert st == 200
    assert d.get("error") == "simulated errno22"
    assert d.get("engine") == "sensevoice_local"


def test_selfcheck_ok_result(tmp_path, monkeypatch):
    store = S.OsGoalStore(str(tmp_path / "os_goals"))
    monkeypatch.setattr(
        os_asr,
        "selfcheck",
        lambda: {"ok": True, "engine": "sensevoice_local", "text": "ok"},
    )
    st, d = A.handle_get(store, "/api/os/asr/selfcheck")
    assert st == 200 and d.get("ok") is True and d.get("text") == "ok"
