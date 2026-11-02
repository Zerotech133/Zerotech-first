"""P1 受控手动选股 API 回归测试（隔离 app + 假 runner；不触生产 DB）。

覆盖：
1. feature flag 默认 off → 503，runner 不执行；
2. ADMIN/TRADER 可执行，VIEWER 403；
3. 交易日校验（非交易日 400 + 审计 rejected）；
4. 非法 market / trade_date；
5. 幂等：body/header key 回放同 run_id，不重复执行；同 key 参数不同 409；
6. 并发锁：已有 run 时 409；过期 stale 锁可接管；
7. 失败：runner 异常 → 500 + failed 审计 + 锁释放（随后可再跑）；
8. 审计 JSONL：source=manual、operator、status/时间戳；
9. GET /selection/run/{run_id} 状态查询；未知 404；
10. 默认 runner 复用 WorkflowService.run_pre_market（用假类验证参数）。
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from zhice_os_v2.api.dependencies import get_current_user
from zhice_os_v2.api.routers import selection_run
from zhice_os_v2.domain.security.user import Role, User

TRADE_DATE = "2026-11-02"


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.error: Exception | None = None
        self.result: dict = {
            "market": "CN",
            "trade_date": TRADE_DATE,
            "candidates": 3,
            "plans": [],
            "approvals": [],
            "regime": "trend",
        }

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return dict(self.result)


def _client(role: Role = Role.ADMIN, runner: FakeRunner | None = None):
    app = FastAPI()
    app.include_router(selection_run.router, prefix="/api/v1")
    fake = runner or FakeRunner()
    app.dependency_overrides[get_current_user] = (
        lambda: User("u-test-1", "tester", role)
    )
    app.dependency_overrides[selection_run.get_selection_runner] = lambda: fake
    return TestClient(app), fake, app


def _body(**overrides):
    body = {
        "market": "CN",
        "trade_date": TRADE_DATE,
        "strategy_type": "TREND",
        "max_candidates": 10,
    }
    body.update(overrides)
    return body


@pytest.fixture(autouse=True)
def _reset_run_state():
    selection_run._reset_state()
    yield
    selection_run._reset_state()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SELECTION_RUN_ENABLED", "1")
    monkeypatch.setenv("SELECTION_RUN_AUDIT_PATH", str(tmp_path / "selection_audit.jsonl"))
    monkeypatch.setenv("SELECTION_RUN_LOCK_PATH", str(tmp_path / "selection_run.lock"))
    monkeypatch.setattr(selection_run, "_is_trading_day", lambda market, td: True)
    return SimpleNamespace(
        tmp=tmp_path,
        audit=tmp_path / "selection_audit.jsonl",
        lock=tmp_path / "selection_run.lock",
    )


def _audit_lines(env) -> list[dict]:
    if not env.audit.exists():
        return []
    return [json.loads(line) for line in env.audit.read_text(encoding="utf-8").splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# 1. feature flag
# --------------------------------------------------------------------------- #
def test_feature_flag_default_off(env, monkeypatch):
    monkeypatch.delenv("SELECTION_RUN_ENABLED", raising=False)
    client, fake, _ = _client()
    r = client.post("/api/v1/selection/run", json=_body())
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "selection_run.disabled"
    assert fake.calls == []


def test_feature_flag_accepts_on_values(env, monkeypatch):
    for value in ("1", "true", "on", "YES"):
        monkeypatch.setenv("SELECTION_RUN_ENABLED", value)
        client, fake, _ = _client()
        r = client.post("/api/v1/selection/run", json=_body(idempotency_key=f"flag-{value.lower()}-key"))
        assert r.status_code == 200, (value, r.text)
        assert fake.calls


# --------------------------------------------------------------------------- #
# 2. 角色 / 执行成功 / 审计
# --------------------------------------------------------------------------- #
def test_admin_run_succeeds_and_audits(env):
    client, fake, _ = _client(Role.ADMIN)
    r = client.post("/api/v1/selection/run", json=_body())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["run_id"].startswith("sel-")
    assert data["status"] == "succeeded"
    assert data["source"] == "manual"
    assert data["operator"] == "tester"
    assert data["market"] == "CN"
    assert data["trade_date"] == TRADE_DATE
    assert data["result"]["candidates"] == 3
    assert data["idempotent_replay"] is False
    assert len(fake.calls) == 1
    assert str(fake.calls[0]["trade_date"]) == TRADE_DATE
    assert fake.calls[0]["max_candidates"] == 10
    lines = _audit_lines(env)
    assert [x["status"] for x in lines] == ["running", "succeeded"]
    assert all(x["source"] == "manual" and x["operator"] == "tester" for x in lines)
    assert lines[1]["run_id"] == data["run_id"]


def test_trader_allowed(env):
    client, _, _ = _client(Role.TRADER)
    r = client.post("/api/v1/selection/run", json=_body())
    assert r.status_code == 200


def test_viewer_forbidden(env):
    client, fake, _ = _client(Role.VIEWER)
    r = client.post("/api/v1/selection/run", json=_body())
    assert r.status_code == 403
    assert fake.calls == []


# --------------------------------------------------------------------------- #
# 3. 交易日 / 参数校验
# --------------------------------------------------------------------------- #
def test_non_trading_day_rejected(env, monkeypatch):
    monkeypatch.setattr(selection_run, "_is_trading_day", lambda market, td: False)
    client, fake, _ = _client()
    r = client.post("/api/v1/selection/run", json=_body())
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["code"] == "selection.non_trading_day"
    assert detail["run_id"]
    assert fake.calls == []
    lines = _audit_lines(env)
    assert lines and lines[-1]["status"] == "rejected"
    assert lines[-1]["reason"] == "non_trading_day"
    assert lines[-1]["source"] == "manual" and lines[-1]["operator"] == "tester"


def test_invalid_market_rejected(env):
    client, fake, _ = _client()
    r = client.post("/api/v1/selection/run", json=_body(market="ZZ"))
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "selection.invalid_market"
    assert fake.calls == []


def test_invalid_trade_date_rejected(env):
    client, fake, _ = _client()
    r = client.post("/api/v1/selection/run", json=_body(trade_date="2026-13-40"))
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "selection.invalid_trade_date"
    assert fake.calls == []


def test_market_is_case_insensitive(env):
    client, fake, _ = _client()
    r = client.post("/api/v1/selection/run", json=_body(market="cn"))
    assert r.status_code == 200
    assert r.json()["market"] == "CN"
    assert fake.calls[0]["market"] == "CN"


# --------------------------------------------------------------------------- #
# 4. 幂等
# --------------------------------------------------------------------------- #
def test_idempotent_replay_same_run_id(env):
    client, fake, _ = _client()
    body = _body(idempotency_key="idem-key-0001")
    first = client.post("/api/v1/selection/run", json=body)
    second = client.post("/api/v1/selection/run", json=body)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["run_id"] == second.json()["run_id"]
    assert second.json()["idempotent_replay"] is True
    assert len(fake.calls) == 1, "幂等回放不得重复执行"
    lines = _audit_lines(env)
    assert [x["status"] for x in lines] == ["running", "succeeded"], "回放不追加审计"


def test_idempotency_conflict_params(env):
    client, fake, _ = _client()
    body = _body(idempotency_key="idem-key-0002")
    assert client.post("/api/v1/selection/run", json=body).status_code == 200
    r = client.post("/api/v1/selection/run", json=_body(idempotency_key="idem-key-0002", market="HK"))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "selection.idempotency_conflict"
    assert len(fake.calls) == 1


def test_idempotency_header_key(env):
    client, fake, _ = _client()
    headers = {"Idempotency-Key": "header-idem-0001"}
    first = client.post("/api/v1/selection/run", json=_body(), headers=headers)
    second = client.post("/api/v1/selection/run", json=_body(), headers=headers)
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["run_id"] == second.json()["run_id"]
    assert second.json()["idempotent_replay"] is True
    assert len(fake.calls) == 1


# --------------------------------------------------------------------------- #
# 5. 并发锁
# --------------------------------------------------------------------------- #
def test_lock_in_progress_rejected(env):
    env.lock.write_text(json.dumps({
        "run_id": "sel-other-running",
        "operator": "other",
        "started_at": datetime.now(UTC).isoformat(),
        "status": "running",
    }), encoding="utf-8")
    client, fake, _ = _client()
    r = client.post("/api/v1/selection/run", json=_body())
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "selection.run_in_progress"
    assert detail["run_id"] == "sel-other-running"
    assert fake.calls == []


def test_stale_lock_taken_over(env, monkeypatch):
    monkeypatch.setenv("SELECTION_RUN_LOCK_TTL_SEC", "60")
    old = datetime.now(UTC) - timedelta(hours=2)
    env.lock.write_text(json.dumps({
        "run_id": "sel-stale-old",
        "operator": "old",
        "started_at": old.isoformat(),
        "status": "running",
    }), encoding="utf-8")
    client, fake, _ = _client()
    r = client.post("/api/v1/selection/run", json=_body())
    assert r.status_code == 200, r.text
    assert len(fake.calls) == 1
    assert not env.lock.exists(), "成功完成后应释放锁文件"


# --------------------------------------------------------------------------- #
# 6. 失败语义 / 锁释放
# --------------------------------------------------------------------------- #
def test_runner_failure_returns_500_audits_and_releases_lock(env):
    client, fake, _ = _client()
    fake.error = RuntimeError("workflow boom")
    r = client.post("/api/v1/selection/run", json=_body(idempotency_key="fail-key-0001"))
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert detail["code"] == "selection_run.failed"
    assert detail["status"] == "failed"
    assert detail["run_id"].startswith("sel-")
    assert "workflow boom" in (detail["error"] or "")
    assert not env.lock.exists()
    lines = _audit_lines(env)
    assert [x["status"] for x in lines] == ["running", "failed"]
    assert lines[-1]["error"].startswith("RuntimeError")

    # 失败后不残留锁：换个 key 可重新执行
    fake.error = None
    r2 = client.post("/api/v1/selection/run", json=_body(idempotency_key="fail-key-0002"))
    assert r2.status_code == 200, r2.text


# --------------------------------------------------------------------------- #
# 7. 状态查询
# --------------------------------------------------------------------------- #
def test_get_run_status(env):
    client, _, _ = _client()
    run = client.post("/api/v1/selection/run", json=_body()).json()
    r = client.get(f"/api/v1/selection/run/{run['run_id']}")
    assert r.status_code == 200
    assert r.json()["run_id"] == run["run_id"]
    assert r.json()["status"] == "succeeded"
    assert r.json()["source"] == "manual"


def test_get_unknown_run_404(env):
    client, _, _ = _client()
    r = client.get("/api/v1/selection/run/sel-does-not-exist")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "selection.run_not_found"


# --------------------------------------------------------------------------- #
# 8. 默认 runner 复用 WorkflowService.run_pre_market（不执行真实服务）
# --------------------------------------------------------------------------- #
def test_default_runner_calls_workflow_pre_market(monkeypatch):
    captured: dict = {}

    class FakeWorkflowService:
        def __init__(self, db):
            captured["db"] = db

        def run_pre_market(self, market, trade_date, **kwargs):
            captured.update({"market": market, "trade_date": trade_date, **kwargs})
            return {"market": market, "trade_date": str(trade_date), "candidates": 0,
                    "plans": [], "approvals": []}

    import zhice_os_v2.application.services.workflow_service as workflow_mod

    monkeypatch.setattr(workflow_mod, "WorkflowService", FakeWorkflowService)
    db = object()
    from datetime import date as _date

    result = selection_run._default_runner(
        market="CN", trade_date=_date(2026, 11, 2), strategy_type="TREND",
        max_candidates=7, operator="tester", db=db,
    )
    assert captured["db"] is db
    assert captured["market"] == "CN"
    assert captured["trade_date"] == _date(2026, 11, 2)
    assert captured["max_candidates"] == 7
    assert captured["strategy_type"] == "TREND"
    assert result["candidates"] == 0


# --------------------------------------------------------------------------- #
# 9. 跨进程/重启幂等兜底：审计 JSONL 回放
# --------------------------------------------------------------------------- #
def test_idempotent_replay_after_process_restart(env):
    """清空进程内索引（模拟重启/多 worker）后，仍按审计 JSONL 回放同 key。"""
    client, fake, _ = _client()
    body = _body(idempotency_key="restart-key-0001")
    first = client.post("/api/v1/selection/run", json=body)
    assert first.status_code == 200, first.text

    selection_run._reset_state()  # 模拟进程重启：内存记录丢失，审计保留

    second = client.post("/api/v1/selection/run", json=body)
    assert second.status_code == 200, second.text
    assert second.json()["run_id"] == first.json()["run_id"]
    assert second.json()["idempotent_replay"] is True
    assert len(fake.calls) == 1, "审计回放不得重复执行选股"


# --------------------------------------------------------------------------- #
# 10. 生产 app 挂载复核（main.py 已 include；默认 flag off 时受控 503）
# --------------------------------------------------------------------------- #
def test_route_mounted_in_main_app_and_flag_default_off(env, monkeypatch):
    """main.py 已 include selection_run：默认 flag off 时应 503（不是 404）。"""
    monkeypatch.delenv("SELECTION_RUN_ENABLED", raising=False)
    from zhice_os_v2.api.dependencies import get_current_user
    from zhice_os_v2.api.main import app as main_app
    from zhice_os_v2.domain.security.user import Role, User

    main_app.dependency_overrides[get_current_user] = (
        lambda: User("u-main-1", "main-admin", Role.ADMIN)
    )
    try:
        paths = main_app.openapi().get("paths") or {}
        assert "/api/v1/selection/run" in paths, "selection_run 未挂载到 main.py"
        resp = TestClient(main_app).post("/api/v1/selection/run", json=_body())
    finally:
        main_app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["code"] == "selection_run.disabled"


def test_main_app_route_accepts_controlled_run(env):
    """真实 main app 路由 + 受控 flag on + 假 runner：端到端 200（不触真实选股）。"""
    from zhice_os_v2.api.dependencies import get_current_user
    from zhice_os_v2.api.main import app as main_app
    from zhice_os_v2.domain.security.user import Role, User

    fake = FakeRunner()
    main_app.dependency_overrides[get_current_user] = (
        lambda: User("u-main-2", "main-admin", Role.ADMIN)
    )
    main_app.dependency_overrides[selection_run.get_selection_runner] = lambda: fake
    try:
        resp = TestClient(main_app).post("/api/v1/selection/run", json=_body())
    finally:
        main_app.dependency_overrides.pop(get_current_user, None)
        main_app.dependency_overrides.pop(selection_run.get_selection_runner, None)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["run_id"].startswith("sel-")
    assert data["status"] == "succeeded"
    assert data["source"] == "manual"
    assert len(fake.calls) == 1


