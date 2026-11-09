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
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
# 本文件只打本机自启服务：回环请求禁代理（safe_urlopen 内含 127.0.0.1 豁免）
from safe_urlopen import safe_urlopen  # noqa: E402
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
        with safe_urlopen("http://127.0.0.1:8734" + path, timeout=timeout) as r:
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
    # 状态随流水线推进变化（intake_pending→…→archived）：只校验合法状态集合
    assert {p["status"] for p in data["projects"]} <= {"intake_pending", "registered", "ingesting",
                                                      "cleaning", "extracting", "governing",
                                                      "distilling", "standardizing", "published",
                                                      "archived", "blocked"}
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

# ---------------------------------------------------------------------------
# 6. v2 门禁状态（learning_gate 聚合 + 页面区块）
# ---------------------------------------------------------------------------
def test_dashboard_gate_section_contract():
    assert DASHBOARD.exists()
    data = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    assert data["schema_version"] == "learning_projects_dashboard.v2"
    gate = data.get("gate")
    assert isinstance(gate, dict) and gate.get("available") is True
    for key in ("ok", "summary", "projects", "failed_stages", "generated_at", "file", "stage"):
        assert key in gate, key
    assert gate["ok"] is True
    assert gate["summary"]["checks"] >= 1
    assert isinstance(gate["failed_stages"], list)
    assert gate["projects"] and all("stages" in p for p in gate["projects"])
    for old in ("projects", "pipeline", "quality", "todos", "owner", "executors", "sop"):
        assert old in data, old
    assert data["summary"].get("gate_v2_ok") is True


def test_gate_aggregation_with_fixture(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    fixture = {
        "generated_at": "2026-11-02T12:00:00+08:00", "stage": "all",
        "projects": [
            {"project_id": "p_ok", "status": "published", "fail": 0,
             "stages": {"intake": {"ok": True, "errors": []},
                        "units": {"ok": True, "errors": []}}},
            {"project_id": "p_bad", "status": "distilling", "fail": 1,
             "stages": {"candidates": {"ok": False, "errors": ["stage_dirty:candidates"]},
                        "publish": {"ok": True, "errors": []}}},
        ],
        "summary": {"projects": 2, "checks": 4, "fail": 1}, "ok": False,
    }
    (reports / "learning_gate_20261102_fixture.json").write_text(
        json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "dashboard.json"
    payload = B.build(reports_dir=reports, out_path=out)
    assert payload["schema_version"] == "learning_projects_dashboard.v2"
    gate = payload["gate"]
    assert gate["ok"] is False
    assert gate["summary"]["fail"] == 1 and gate["summary"]["checks"] == 4
    assert gate["generated_at"] == "2026-11-02T12:00:00+08:00"
    failed = gate["failed_stages"]
    assert len(failed) == 1
    assert failed[0]["project_id"] == "p_bad" and failed[0]["stage"] == "candidates"
    assert failed[0]["errors"] == ["stage_dirty:candidates"]
    proj = {p["project_id"]: p for p in gate["projects"]}
    assert proj["p_bad"]["fail"] == 1
    assert any(not st["ok"] for st in proj["p_bad"]["stages"])
    assert "projects" in payload and "pipeline" in payload and "todos" in payload
    assert out.exists()


def test_page_gate_block():
    html = PAGE.read_text(encoding="utf-8")
    assert "门禁状态（v2）" in html
    assert 'id="gate-summary"' in html
    assert 'id="gate"' in html
    assert "gate.failed_stages" in html
    assert "❌ 失败" in html and "✅ 通过" in html

# ---------------------------------------------------------------------------
# 7. 项目注册表 lifecycle v2（阶段历史 + gate 写回）
# ---------------------------------------------------------------------------
REGISTRY = REPO / "agent_platform" / "registry" / "learning_projects.json"


def test_registry_lifecycle_contract():
    import json as _json
    reg = _json.loads(REGISTRY.read_text(encoding="utf-8"))
    stages = set(reg.get("stages") or [])
    assert stages, "registry stages 缺失"
    backfilled = {"learnpdf_liangxue", "laomu_notes"}  # 已按 v2 完成 lifecycle backfill 的项目
    for proj in reg.get("projects") or []:
        pid = proj.get("project_id")
        life = proj.get("lifecycle") or {}
        if pid in backfilled:
            assert proj.get("schema_version") == "learning_project.v1", pid
            assert life.get("stage") == proj.get("status"), pid
            assert life.get("stage_updated_at"), pid
            gate = life.get("gate") or {}
            assert "ok" in gate and "checked_at" in gate, pid
            history = life.get("history") or []
            assert isinstance(history, list) and len(history) >= 1
            assert all("stage" in h and "at" in h for h in history)
        else:
            # 新入项目（如 auto_intake）：允许 lifecycle 尚未 backfill，但结构必须合法
            if life:
                gate = life.get("gate") or {}
                assert "ok" in gate and "checked_at" in gate, pid
                assert isinstance(life.get("history") or [], list), pid
        # 保留旧字段（兼容 gate/看板）
        for old in ("name", "type", "status", "owner", "project_id"):
            assert old in proj, (proj.get("project_id"), old)
    statuses = {p.get("project_id"): p.get("status") for p in reg["projects"]}
    assert statuses.get("learnpdf_liangxue") == "archived"
    assert statuses.get("laomu_notes") == "distilling"


def test_dashboard_lifecycle_and_registry_gate_sync():
    import json as _json
    import re as _re
    # 构建一次，registry 的 lifecycle.gate 应与最新**全量** learning_gate 报告一致
    payload = B.build(out_path=DASHBOARD)
    gate_files = sorted((REPO / "research" / "reports").glob("learning_gate_*.json"))
    assert gate_files, "缺少 learning_gate 报告"
    full = [f for f in gate_files if _re.fullmatch(r"learning_gate_\d{8}\.json", f.name)]
    gate_report = _json.loads((full[-1] if full else gate_files[-1]).read_text(encoding="utf-8"))
    last_gate_at = gate_report.get("generated_at")
    assert payload["schema_version"] == "learning_projects_dashboard.v2"
    assert payload["lifecycle"]["registry_synced"] in (True, False)
    assert payload["lifecycle"]["gate_ok"] == len(payload["projects"])
    # 全量 gate 可能尚未覆盖新入项目；这些项目应保留各自较新的 gate，而不是回退到旧全量报告。
    full_ids = {p.get("project_id") for p in gate_report.get("projects") or []}
    for proj in payload["projects"]:
        pid = proj.get("project_id")
        life = proj.get("lifecycle") or {}
        assert life.get("stage") == proj.get("status")
        gate = life.get("gate") or {}
        if pid in full_ids:
            assert gate.get("checked_at") == last_gate_at, pid
        else:
            assert gate.get("checked_at"), pid
        assert gate.get("ok") is True
        assert isinstance(life.get("history") or [], list)
    reg = _json.loads(REGISTRY.read_text(encoding="utf-8"))
    for proj in reg["projects"]:
        if proj["project_id"] in full_ids:
            assert proj["lifecycle"]["gate"]["checked_at"] == last_gate_at
        else:
            assert proj["lifecycle"]["gate"]["checked_at"]
    # HTTP：看板 JSON 已带 lifecycle
    with safe_urlopen("http://127.0.0.1:8734/static/learning_projects_dashboard.json", timeout=10) as r:
        served = _json.loads(r.read().decode("utf-8"))
    assert served["schema_version"] == "learning_projects_dashboard.v2"
    assert served["lifecycle"]["projects"] >= 2
    assert all((p.get("lifecycle") or {}).get("gate") for p in served["projects"])

# ---------------------------------------------------------------------------
# 8. W2-5 学习因子实验室（learning_factors.json + 页面区块）
# ---------------------------------------------------------------------------
FACTORS_JSON = REPO / "portal" / "static" / "learning_factors.json"


def test_learning_factors_json_contract():
    assert FACTORS_JSON.exists(), f"缺少因子看板数据：{FACTORS_JSON}"
    data = json.loads(FACTORS_JSON.read_text(encoding="utf-8"))
    assert data["schema_version"] == "learning_factors_dashboard.v1"
    assert data.get("generated_at") and data.get("source")
    for key in ("summary", "factors", "data_gaps", "unimplemented"):
        assert key in data, key
    summary = data["summary"]
    assert summary["n_factors"] == len(data["factors"]) == 8
    assert summary["implemented"] == 8
    assert 0 < summary["avg_coverage_pct"] <= 100
    assert summary["pit_safe"] >= 6
    for factor in data["factors"]:
        for key in ("name", "title", "formula", "coverage_pct", "pit_safe", "stats", "examples"):
            assert key in factor, (factor.get("name"), key)
        assert 0 <= float(factor["coverage_pct"]) <= 100
        assert factor["status"] in ("可用", "部分", "数据缺口")
    assert isinstance(data["data_gaps"], list)


def test_page_factor_lab_block_and_http():
    html = PAGE.read_text(encoding="utf-8")
    assert "学习因子实验室（W2）" in html
    assert "/static/learning_factors.json" in html
    for anchor in ('id="factor-summary"', 'id="factor-list"', 'id="factor-gaps"'):
        assert anchor in html, anchor
    # 既有区块未被移除（不改已有区块）
    for old in ("项目列表", "流水线（4 任务）", "质量门", "学习能力矩阵", "待办"):
        assert old in html, old
    # HTTP 200：页面 + 因子 JSON
    with safe_urlopen("http://127.0.0.1:8734/learning_projects.html", timeout=10) as r:
        body = r.read().decode("utf-8", errors="replace")
        assert r.status == 200 and "学习因子实验室（W2）" in body
    with safe_urlopen("http://127.0.0.1:8734/static/learning_factors.json", timeout=10) as r:
        served = json.loads(r.read().decode("utf-8"))
        assert r.status == 200 and len(served["factors"]) == 8


def test_latest_gate_prefers_generated_at_over_mtime(tmp_path):
    """补跑/复制可能让旧报告 mtime 更新；应取 generated_at 最新的全量报告。"""
    old = tmp_path / "learning_gate_20261103.json"
    new = tmp_path / "learning_gate_20261104.json"
    old.write_text(json.dumps({"generated_at": "2026-11-03T16:37:41+08:00"}), encoding="utf-8")
    new.write_text(json.dumps({"generated_at": "2026-11-04T02:43:33+08:00"}), encoding="utf-8")
    now = 1793737326.0
    os.utime(old, (now + 3600, now + 3600))  # 旧报告 mtime 反而更晚
    os.utime(new, (now, now))
    picked, data = B.latest_gate(tmp_path)
    assert picked == new
    assert data["generated_at"] == "2026-11-04T02:43:33+08:00"
