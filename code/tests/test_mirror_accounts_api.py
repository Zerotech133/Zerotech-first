"""/my/mirror-accounts：建/列/详情 + 错误码。隔离 sqlite + patch 服务依赖 + 覆盖鉴权依赖。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import zhice_os_v2.application.services.mirror_sim_service as svc_mod
    import zhice_os_v2.infrastructure.repositories.mirror_sim_repository as repo_mod
    from zhice_os_v2.infrastructure.db.base import Base

    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, expire_on_commit=False)
    for m in (repo_mod, svc_mod):
        monkeypatch.setattr(m, "get_session", lambda: Session(), raising=False)
    monkeypatch.setattr(svc_mod, "_is_subscription_active", lambda uid, sid: sid == "s1")
    monkeypatch.setattr(svc_mod, "_is_observable_builtin", lambda sid: sid == "s1")
    monkeypatch.setattr(svc_mod, "_strategy_market", lambda sid: "CRYPTO")

    from zhice_os_v2.api.dependencies import get_current_user, require_active_membership
    from zhice_os_v2.api.main import app
    from zhice_os_v2.domain.security.user import Role, User

    def _user():
        return User(user_id="u1", username="u1", role=Role.USER)
    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[require_active_membership] = _user
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_create_list_detail(client):
    r = client.post("/api/v1/my/mirror-accounts", json={"strategy_id": "s1", "initial_capital": 10000})
    assert r.status_code == 200, r.text
    assert r.json()["strategy_id"] == "s1"
    r2 = client.post("/api/v1/my/mirror-accounts", json={"strategy_id": "s1", "initial_capital": 10000})
    assert r2.status_code == 409 and r2.json()["detail"]["code"] == "mirror.account_exists"
    r3 = client.post("/api/v1/my/mirror-accounts", json={"strategy_id": "other", "initial_capital": 100})
    assert r3.status_code == 403 and r3.json()["detail"]["code"] == "mirror.subscription_required"
    assert len(client.get("/api/v1/my/mirror-accounts").json()) == 1
    d = client.get("/api/v1/my/mirror-accounts/s1")
    assert d.status_code == 200 and d.json()["status"] == "active" and "positions" in d.json()
    assert client.get("/api/v1/my/mirror-accounts/nope").status_code == 404
