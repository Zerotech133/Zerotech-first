# -*- coding: utf-8 -*-
"""merge_preflight 纯逻辑单测（2026-10-28，P13；P3c 依赖提示）。"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from merge_preflight import dep_age_hint, deps_changed, is_docs_only  # noqa: E402


def test_docs_only_true():
    assert is_docs_only(["docs/plans/x.md", "deliverables/y.md"])
    assert is_docs_only(["agent_platform/skills/guanyu/SKILL.md"])
    # A9（2026-10-31）：html/css 可跳过；.js 是前端代码，必须跑测试
    assert is_docs_only(["portal/static/tokens.css", "portal/static/index.html"])


def test_docs_only_false_for_js():
    assert not is_docs_only(["portal/static/nav.js"])
    assert not is_docs_only(["portal/static/tokens.css", "portal/static/m.js"])


def test_docs_only_false_for_code():
    assert not is_docs_only(["tools/merge_preflight.py"])
    assert not is_docs_only(["docs/a.md", "portal/api_kg.py"])


def test_docs_only_false_for_empty_or_root_md():
    assert not is_docs_only([])
    assert not is_docs_only(["README.md"])


def test_docs_only_false_for_mixed():
    assert not is_docs_only(["docs/a.md", "tools/x.json", "tools/y.py"])


def test_deps_changed():
    assert deps_changed(["requirements/lines/x.lock"])
    assert deps_changed(["uv.lock"])
    assert deps_changed(["pyproject.toml"])
    assert not deps_changed(["docs/a.md", "tools/x.py"])
    assert not deps_changed([])


def test_dep_age_hint_skips_non_deps():
    assert dep_age_hint(["docs/a.md"]) is None


def test_dep_age_hint_warn_only(monkeypatch):
    monkeypatch.delenv("DEVTOOL_DEP_AGE_HINT", raising=False)
    payload = {"checked": 3, "fresh_count": 1, "error_count": 0,
               "fresh": [{"name": "newpkg", "version": "1.0", "age_days": 2.5}]}

    def fake_run(cmd, **kwargs):
        assert "--limit" in cmd and "--json" in cmd
        return SimpleNamespace(stdout=json.dumps(payload), returncode=0)

    out = dep_age_hint(["requirements/lines/system_py311_full.lock"], runner=fake_run)
    assert out["fresh_count"] == 1
    assert out["fresh_top"][0][0] == "newpkg"


def test_dep_age_hint_never_raises(monkeypatch):
    monkeypatch.delenv("DEVTOOL_DEP_AGE_HINT", raising=False)

    def boom(cmd, **kwargs):
        raise RuntimeError("network down")

    out = dep_age_hint(["uv.lock"], runner=boom)
    assert "error" in out
