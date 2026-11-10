# -*- coding: utf-8 -*-
"""GET /api/v1/strategies/research-status 只读 API 测试。

覆盖：
1. 只读摘要：manifest artifacts sha256/asof、24 specs、32 策略、25 close/7 needs-more、
   4 冲突、G1-G8 gate counts、freshness；
2. artifacts 哈希/asof 与 manifest 原文件一致（不是随机值）；
3. counts 自洽（24+8=32、25+7=32、conflicts/artifacts 与列表一致）；
4. 未认证 401；manifest 缺失/损坏、gate matrix 损坏 → 503 + 稳定 code（不裸 500）；
5. snapshot 可选：缺失时仍 200，freshness=UNKNOWN（不因可选数据失败）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import zhice_os_v2.api.routers.strategy as strategy_module
from zhice_os_v2.api.dependencies import get_current_user
from zhice_os_v2.api.main import app
from zhice_os_v2.domain.security.user import Role, User

URL = "/api/v1/strategies/research-status"
CONFLICT_IDS = {
    "S01_H14_quality_trap_guard",
    "S02_composite_mvp",
    "S06_reversal_combo_mom20_120",
    "S07_mom20_long_only",
}


@pytest.fixture()
def client():
    app.dependency_overrides[get_current_user] = (
        lambda: User("u-research-status", "research-status-tester", Role.RESEARCHER)
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_research_status_readonly_summary(client: TestClient) -> None:
    resp = client.get(URL)
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["schema"] == "zhice.strategy_research_status/v1"
    assert body["read_only"] is True
    assert body["generated_at"]

    counts = body["counts"]
    assert counts["specs"] == 24
    assert counts["strategies"] == 32
    assert counts["book"] == 24
    assert counts["registered"] == 8
    assert counts["strict_verdict"] == {"close": 25, "needs-more": 7}
    assert counts["close"] == 25
    assert counts["needs_more"] == 7
    assert counts["conflicts"] == 4
    assert counts["artifacts"] == 29

    assert body["specs"] == {"count": 24, "expected": 24, "complete": True}
    assert body["strategies"]["total"] == 32
    assert body["strategies"]["strict_verdict"] == {"close": 25, "needs-more": 7}

    # manifest 摘要：asof/owner/contract/counts/validations
    manifest = body["manifest"]
    assert manifest["schema"] == "zhice.research_artifact_manifest/v1"
    assert manifest["asof"]
    assert manifest["owner"] == "DATA1"
    assert manifest["contract"]["id"] == "strategy_research_zhangsong"
    assert manifest["valid"] is True
    assert manifest["counts"]["research_specs"] == 24
    assert manifest["counts"]["strategy_market_items"] == 32

    # gate matrix 摘要
    gate = body["gate_matrix"]
    assert gate["schema"] == "zhice.strategy_gate_matrix/v1"
    assert gate["counts"]["total"] == 32
    assert gate["counts"]["strict_verdict"] == {"close": 25, "needs-more": 7}
    assert gate["gate_status_tally"]["G8"] == {"done": 32}
    assert gate["conflict_count"] == 4
    assert gate["gaps_count"] >= 1

    # artifacts: 每个都有 sha256/asof/owner/role；共 29 个（含正式补录报告）
    artifacts = body["artifacts"]
    assert len(artifacts) == 29
    sha_re = __import__("re").compile(r"^[0-9a-f]{64}$")
    for item in artifacts:
        assert item["role"]
        assert item["path"]
        assert item["asof"]
        assert item["owner"]
        assert sha_re.match(item["sha256"] or ""), item
    assert any(item["role"] == "deliverable_report" for item in artifacts)

    # 冲突项：4 个，均为 verdict_conflict 语义（strict close vs 市场目录待补验）
    conflicts = body["conflicts"]
    assert len(conflicts) == 4
    assert {x["strategy_id"] for x in conflicts} == CONFLICT_IDS
    for item in conflicts:
        assert item["strict_verdict"] == "close"
        assert item["market_decision"] == "待补验"

    # 新鲜度：asof 与 manifest 对齐，契约 24h；当前状态 WARN/FRESH/STALE 均可，需自洽
    freshness = body["freshness"]
    assert freshness["asof"] == manifest["asof"]
    assert freshness["max_age_hours"] == 24
    assert freshness["snapshot_generated_at"]
    assert freshness["status"] in {"FRESH", "WARN", "STALE", "UNKNOWN"}
    if freshness["status"] == "WARN":
        assert freshness["sync_lag_hours"] is not None

    assert body["sources"]["manifest"].endswith("research_artifact_manifest.json")
    assert body["sources"]["gate_matrix"].endswith("strategy_gate_matrix.json")


def test_research_status_artifact_hashes_match_manifest(client: TestClient) -> None:
    """API 返回的 sha256/asof 必须来自 manifest 原文件，而非重新编造。"""
    raw = json.loads(strategy_module._RESEARCH_ARTIFACT_MANIFEST.read_text(encoding="utf-8"))
    by_key = {
        (a.get("role"), a.get("relative_path") or a.get("path")): a
        for a in raw.get("artifacts") or []
        if isinstance(a, dict)
    }

    body = client.get(URL).json()
    assert len(body["artifacts"]) == len(by_key) == 29
    for item in body["artifacts"]:
        key = (item["role"], item["relative_path"] or item["path"])
        assert key in by_key
        assert item["sha256"] == by_key[key]["sha256"]
        assert item["asof"] == by_key[key]["asof"]


def test_research_status_counts_are_self_consistent(client: TestClient) -> None:
    body = client.get(URL).json()
    counts = body["counts"]
    gate_counts = body["gate_matrix"]["counts"]
    manifest_counts = body["manifest"]["counts"]

    assert counts["book"] + counts["registered"] == counts["strategies"] == 32
    assert counts["close"] + counts["needs_more"] == counts["strategies"] == 32
    assert counts["specs"] == manifest_counts["research_specs"] == 24
    assert counts["conflicts"] == gate_counts["conflicts"] == len(body["conflicts"]) == 4
    assert counts["artifacts"] == manifest_counts["artifacts"] == len(body["artifacts"]) == 29
    assert body["specs"]["complete"] is True
    assert body["manifest"]["error_count"] == 0


def test_research_status_requires_authentication() -> None:
    app.dependency_overrides.pop(get_current_user, None)
    resp = TestClient(app).get(URL)
    assert resp.status_code == 401
    assert "token" in str(resp.json().get("detail", "")).lower()


def test_missing_manifest_returns_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        strategy_module,
        "_RESEARCH_ARTIFACT_MANIFEST",
        tmp_path / "missing_research_artifact_manifest.json",
    )
    resp = client.get(URL)
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["code"] == "strategy.research_status_unavailable"


def test_corrupt_gate_matrix_returns_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad = tmp_path / "bad_gate_matrix.json"
    bad.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(strategy_module, "_STRATEGY_GATE_MATRIX", bad)
    resp = client.get(URL)
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["code"] == "strategy.research_status_unavailable"


def test_missing_market_snapshot_keeps_status_readable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """market snapshot 只用于 freshness；缺失不阻塞 manifest/gate matrix 只读摘要。"""
    monkeypatch.setattr(
        strategy_module,
        "_STRATEGY_MARKET_RESEARCH",
        tmp_path / "missing_strategy_market_research.json",
    )
    resp = client.get(URL)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["counts"]["strategies"] == 32
    assert body["freshness"]["status"] == "UNKNOWN"
    assert body["freshness"]["snapshot_generated_at"] is None
