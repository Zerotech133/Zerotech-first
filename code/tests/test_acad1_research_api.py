r"""策略工作台 / ACAD1 研究台 / 鲁肃台 新接口的最小回归测试。

隔离原则：不连生产 PostgreSQL；覆盖路由注册、鉴权、参数校验。
证据包端点只读文件，测试用 tmp 文件 + 环境变量隔离，不依赖 C:\Temp 现场文件。
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError


def _app():
    from zhice_os_v2.api.main import app
    return app


@pytest.fixture()
def authed_client():
    """覆盖鉴权依赖的 TestClient（不触发 lifespan/DB；端点只读文件）。"""
    from zhice_os_v2.api.dependencies import get_current_user
    from zhice_os_v2.api.main import app
    from zhice_os_v2.domain.security.user import Role, User

    app.dependency_overrides[get_current_user] = lambda: User(
        user_id="u-acad1", username="u-acad1", role=Role.USER
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_workspace_paths_registered():
    paths = _app().openapi()["paths"]
    for path in (
        "/api/v1/strategy-workspace/list",
        "/api/v1/strategy-workspace/{strategy_id}/profile",
        "/api/v1/strategy-workspace/{strategy_id}/daily",
        "/api/v1/strategy-workspace/{strategy_id}/observations",
        "/api/v1/strategy-workspace/{strategy_id}/backtest",
        "/api/v1/acad1-research/pool",
        "/api/v1/acad1-research/reviews",
        "/api/v1/acad1-research/stats",
        "/api/v1/acad1-research/decisions",
        "/api/v1/acad1-research/calendar",
        "/api/v1/acad1-research/evidence",
        "/api/v1/lusu-research/queue",
        "/api/v1/lusu-research/decision",
    ):
        assert path in paths, path


def test_requires_auth():
    app = _app()
    with TestClient(app) as c:
        for path in ("/api/v1/acad1-research/pool", "/api/v1/acad1-research/evidence"):
            r = c.get(path)
            assert r.status_code in (401, 403), path


def test_lusu_invalid_verdict_rejected_without_db():
    from zhice_os_v2.api.routers import lusu_research
    from zhice_os_v2.domain.security.user import Role, User

    body = lusu_research.DecisionBody(
        pool_date="2026-10-29", strategy_id="chanlun", code="001286.SZ",
        name="陕西能源", verdict="乱写",
    )
    with pytest.raises(HTTPException) as exc:
        lusu_research.decide(body, User(user_id="u1", username="u1", role=Role.USER))
    assert exc.value.status_code == 400


def test_decision_body_requires_fields():
    from zhice_os_v2.api.routers import lusu_research

    with pytest.raises(ValidationError):
        lusu_research.DecisionBody(pool_date="2026-10-29", strategy_id="", code="x", verdict="观察")


def test_sidecar_rank_rules():
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "select_pool", root / "scripts" / "select_acad1_pool.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    assert mod._rank("买入", "qpzm", "") < mod._rank("1买", "chanlun", "")
    assert mod._rank("1买", "chanlun", "") < mod._rank("观察", "qpzm", "")
    assert mod._rank("2买", "chanlun", "") < mod._rank("3买", "chanlun", "")
    assert mod._rank("回避（环境闸门）", "qpzm", "") == 99


def test_pool_invalid_date_rejected():
    from zhice_os_v2.api.routers import acad1_research
    from zhice_os_v2.domain.security.user import Role, User

    with pytest.raises(HTTPException) as exc:
        acad1_research.pool(date_str="not-a-date", limit=10,
                            _=User(user_id="u1", username="u1", role=Role.USER))
    assert exc.value.status_code == 400


# --------------------------------------------------------------------------- #
# GET /api/v1/acad1-research/evidence：只读证据包（200 / 404 / 503）
# --------------------------------------------------------------------------- #
def test_evidence_default_path_is_temp_bundle():
    from zhice_os_v2.api.routers import acad1_research

    assert acad1_research.DEFAULT_EVIDENCE_BUNDLE_PATH == r"C:\Temp\acad1_evidence_bundle.json"


def test_evidence_200_returns_summary_and_items(tmp_path, monkeypatch, authed_client):
    bundle = {
        "schema": "acad1_evidence_bundle/v1",
        "generated_at": "2026-11-01T07:29:31+08:00",
        "pool": "2026-10-31",
        "read_only": True,
        "summary": {
            "pool": {"rows_requested": 30, "bundle_items": 30},
            "matched": {"research_cards": 30, "kb_refs": 30, "fundamentals": 30},
        },
        "items": [
            {"seq": "B01", "market": "CN", "code": "688004.SH", "name": "博汇科技"},
            {"seq": "B02", "market": "HK", "code": "00700.HK", "name": "腾讯控股"},
        ],
    }
    path = tmp_path / "acad1_evidence_bundle.json"
    path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("ACAD1_EVIDENCE_BUNDLE_PATH", str(path))

    resp = authed_client.get("/api/v1/acad1-research/evidence")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"] == bundle["summary"]
    assert body["items"] == bundle["items"]
    assert body["item_count"] == 2
    assert body["pool_date"] == "2026-10-31"
    assert body["schema"] == "acad1_evidence_bundle/v1"
    assert body["generated_at"] == "2026-11-01T07:29:31+08:00"
    assert body["read_only"] is True
    assert body["source"] == {"path": str(path), "bytes": path.stat().st_size, "read_only": True}


def test_evidence_404_when_bundle_not_generated(tmp_path, monkeypatch, authed_client):
    missing = tmp_path / "acad1_evidence_bundle.json"  # 不创建文件
    monkeypatch.setenv("ACAD1_EVIDENCE_BUNDLE_PATH", str(missing))

    resp = authed_client.get("/api/v1/acad1-research/evidence")

    assert resp.status_code == 404, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "acad1.evidence_bundle_not_generated"
    assert "未生成" in detail["message"]
    assert detail["path"] == str(missing)
    assert "hint" in detail


def test_evidence_503_when_bundle_unreadable(tmp_path, monkeypatch, authed_client):
    broken = tmp_path / "broken.json"
    broken.write_text("{not-json", encoding="utf-8")
    monkeypatch.setenv("ACAD1_EVIDENCE_BUNDLE_PATH", str(broken))

    resp = authed_client.get("/api/v1/acad1-research/evidence")

    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["code"] == "acad1.evidence_bundle_unreadable"


def test_evidence_503_when_bundle_structure_invalid(tmp_path, monkeypatch, authed_client):
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"summary": {}, "items": "not-a-list"}), encoding="utf-8")
    monkeypatch.setenv("ACAD1_EVIDENCE_BUNDLE_PATH", str(invalid))

    resp = authed_client.get("/api/v1/acad1-research/evidence")

    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["code"] == "acad1.evidence_bundle_invalid"
