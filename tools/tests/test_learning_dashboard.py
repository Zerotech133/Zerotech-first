# -*- coding: utf-8 -*-
"""学习项目看板（聚合数据 + 门户页 + 导航注册）契约测试。

覆盖：
  1. 聚合器 build()：registry + learning_* 报告 → dashboard schema（项目/流水线/质量门/待办）；
  2. 生成产物 portal/static/learning_projects_dashboard.json：owner=张松、项目字段、gate 结论；
  3. 门户页 portal/learning_projects.html：关键区块/数据源/统一顶栏（nav.js + tokens.css）；
  4. 导航注册：api_common.py 与 nav.js 均含「📚 学习项目」入口（源/导出零漂移）；
  5. HTTP：/learning_projects.html 与 /static/learning_projects_dashboard.json 均 200。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BUILDER = REPO / "tools" / "kb_corpus" / "learning_dashboard_build.py"
DASHBOARD = REPO / "portal" / "static" / "learning_projects_dashboard.json"
PAGE = REPO / "portal" / "learning_projects.html"
API_COMMON = REPO / "portal" / "api_common.py"
NAV_JS = REPO / "portal" / "static" / "nav.js"


def _load():
    spec = importlib.util.spec_from_file_location("learning_dashboard_build_under_test", BUILDER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


B = _load()


def http_ok(path: str, timeout: float = 10.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8734" + path, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# 1. 聚合器
# ---------------------------------------------------------------------------
def test_build_aggregates_schema(tmp_path):
    out = tmp_path / "dashboard.json"
    payload = B.build(out_path=out)
    assert payload["schema_version"].startswith("learning_projects_dashboard")
    assert payload["owner"]["cn"] == "张松"
    assert payload["summary"]["projects"] >= 2
    assert payload["summary"]["pipeline_tasks"] == 4
    assert "gate_passed" in payload["summary"]
    for key in ("projects", "pipeline", "quality", "todos", "sop", "generated_at"):
        assert key in payload, key
    assert len(payload["pipeline"]) == 4
    tasks = {t["task"] for t in payload["pipeline"]}
    assert tasks == {"ZeroBound_LearningIntake", "ZeroBound_LearningStandardize",
                     "ZeroBound_LearningPipeline", "ZeroBound_LearningGovernance"}
    for p in payload["projects"]:
        for key in ("project_id", "name", "status", "stage_label", "owner", "source_count",
                    "drafts", "last_update"):
            assert key in p, (p.get("project_id"), key)
        assert p["owner"] == "张松"
    assert payload["quality"]["checks"]
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["summary"]["pipeline_tasks"] == 4


# ---------------------------------------------------------------------------
# 2. 生成产物
# ---------------------------------------------------------------------------
def test_dashboard_json_contract():
    assert DASHBOARD.exists(), f"缺少看板数据：{DASHBOARD}"
    data = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    assert data["kind"] == "learning_projects_dashboard"
    assert data["owner"]["cn"] == "张松"
    assert data["summary"]["pipeline_tasks"] == 4
    assert any(p["status"] == "archived" for p in data["projects"])
    assert any(p["status"] == "intake_pending" for p in data["projects"])
    assert data["quality"]["gate_passed"] in (True, False)
    assert data["quality"]["checks"]
    assert data["sop"] and "学习项目标准化流程" in data["sop"]


# ---------------------------------------------------------------------------
# 3. 门户页
# ---------------------------------------------------------------------------
def test_page_structure_and_nav():
    assert PAGE.exists(), f"缺少看板页：{PAGE}"
    html = PAGE.read_text(encoding="utf-8")
    for anchor in ("学习项目看板", "项目列表", "流水线", "质量门", "待办"):
        assert anchor in html, anchor
    assert "/static/learning_projects_dashboard.json" in html
    # 前端准入：统一顶栏 + tokens
    assert '<script src="/static/nav.js" defer></script>' in html
    assert "tokens.css" in html
    # 不手写顶栏导航（由 nav.js 统一注入）
    assert 'class="seg"' not in html
    assert "window.__WB_NAV" not in html


# ---------------------------------------------------------------------------
# 4. 导航注册（源 + 导出）
# ---------------------------------------------------------------------------
def test_nav_registration():
    api = API_COMMON.read_text(encoding="utf-8")
    nav = NAV_JS.read_text(encoding="utf-8")
    assert "/learning_projects.html" in api
    assert "学习项目" in api
    assert "/learning_projects.html" in nav
    assert "learningprojects" in nav
    # 自动生成护栏（勿手改）+ 导航数据结构
    assert "自动生成" in nav and "DOMAIN_NAV_GROUPS" in nav
    assert '"sections"' in nav


# ---------------------------------------------------------------------------
# 5. HTTP 200
# ---------------------------------------------------------------------------
def test_http_200():
    st, body = http_ok("/learning_projects.html")
    assert st == 200, f"页面 HTTP {st}: {body[:200]}"
    assert "学习项目看板" in body
    st2, body2 = http_ok("/static/learning_projects_dashboard.json")
    assert st2 == 200, f"数据 HTTP {st2}: {body2[:200]}"
    data = json.loads(body2)
    assert data["summary"]["projects"] >= 2
