# -*- coding: utf-8 -*-
"""OS 全站注入工具测试：幂等、双标记、check/apply、空 body 兜底。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "unify_os_ui_t", str(ROOT / "tools" / "unify_os_ui.py")
)
U = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(U)


def test_inject_is_idempotent_and_marked():
    src = "<!doctype html><html><head><title>x</title></head><body><div>hi</div></body></html>"
    once = U.inject(src)
    twice = U.inject(once)
    assert once.count("os_theme.css") == 1
    assert once.count("os_drill.js") == 1
    assert twice.count("os_theme.css") == 1
    assert U.LINK_MARK in once and U.SCRIPT_MARK in once
    assert once.index("os_theme.css") < once.index("</head>")
    assert once.index("os_drill.js") < once.index("</body>")


def test_inject_without_body_appends_script():
    src = "<html><head></head><div>x</div>"
    out = U.inject(src)
    assert "os_drill.js" in out
    assert out.rstrip().endswith("</script>")


def test_process_check_apply_roundtrip(tmp_path, monkeypatch):
    portal = tmp_path / "portal"
    portal.mkdir()
    (portal / "a.html").write_text(
        "<html><head></head><body>a</body></html>", encoding="utf-8"
    )
    (portal / "data").mkdir()
    (portal / "data" / "skip.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(U, "REPO", tmp_path)
    chk = U.process(check=True, dirs=("portal",))
    assert chk["missing"] == 1 and chk["changed"] == 0
    app = U.process(check=False, dirs=("portal",))
    assert app["changed"] == 1 and app["errors"] == []
    again = U.process(check=True, dirs=("portal",))
    assert again["ok"] is True and again["missing"] == 0
    assert "os_theme.css" in (portal / "a.html").read_text(encoding="utf-8")
