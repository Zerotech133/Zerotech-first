# -*- coding: utf-8 -*-
"""tianfeng_view 8787 探针统一单测（2026-10-31 WO-M4-ENFORCE-PROBE-20261031）。

验证：登录门（302→/login、401）= alive+auth_gated 而非 down；探针异常软降级；
视图不再使用裸 `/health` 200=活 判定。
"""
from __future__ import annotations

from pathlib import Path
import sys

PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLATFORM))
from legion_command import tianfeng_view as tv  # noqa: E402


def test_hermes_auth_gated_is_alive(monkeypatch):
    monkeypatch.setattr(
        tv, "hermes_probe",
        lambda base, timeout=3: {"alive": True, "auth_gated": True,
                                 "status": 302, "error": ""})
    h = tv._hermes_health()
    assert h["alive"] is True and h["auth_gated"] is True
    assert tv._fmt_health(h) == "302(auth_gated)"


def test_hermes_200_is_alive_plain(monkeypatch):
    monkeypatch.setattr(
        tv, "hermes_probe",
        lambda base, timeout=3: {"alive": True, "auth_gated": False,
                                 "status": 200, "error": ""})
    assert tv._fmt_health(tv._hermes_health()) == "200"


def test_hermes_down_formatted(monkeypatch):
    monkeypatch.setattr(
        tv, "hermes_probe",
        lambda base, timeout=3: {"alive": False, "auth_gated": False,
                                 "status": None, "error": "refused"})
    h = tv._hermes_health()
    assert h["alive"] is False and tv._fmt_health(h) == "down"


def test_probe_exception_degrades_not_crash(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("probe boom")

    monkeypatch.setattr(tv, "hermes_probe", boom)
    h = tv._hermes_health()
    assert h["alive"] is False and h["status"] is None
    assert "boom" in h["error"]


def test_view_no_longer_uses_bare_8787_health_endpoint():
    src = Path(tv.__file__).read_text(encoding="utf-8")
    assert ":8787/health" not in src      # 旧裸探针已移除
    assert "hermes_probe" in src          # 统一走登录门感知探针
