"""Tests for the coarse, credential-free browser session health endpoint."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from knowledge import api as knowledge_api  # noqa: E402
from knowledge.browser_fetcher import BrowserSessionHealth, BrowserSessionStatus  # noqa: E402
from queue_link.api import app  # noqa: E402


class _Fetcher:
    """Minimal existing-fetcher double; no credential fields."""

    def __init__(self, status: BrowserSessionStatus) -> None:
        self.status = status

    def session_health(self) -> BrowserSessionHealth:
        return BrowserSessionHealth(status=self.status, checked_at=datetime.now(timezone.utc))


@pytest.fixture()
def client() -> TestClient:
    previous = knowledge_api._browser_fetcher
    knowledge_api.register_browser_fetcher(None)
    with TestClient(app) as value:
        yield value
    knowledge_api.register_browser_fetcher(previous)


def test_session_health_without_fetcher_is_unknown(client: TestClient) -> None:
    response = client.get("/api/v1/session/health")
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["status"] == "unknown"
    assert set(body["data"]) == {"status", "checked_at"}


def test_session_health_projects_coarse_status_only(client: TestClient) -> None:
    knowledge_api.register_browser_fetcher(_Fetcher(BrowserSessionStatus.AUTHENTICATED))
    response = client.get("/api/v1/session/health")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "authenticated"
    assert set(data) == {"status", "checked_at"}
    assert "cookie" not in response.text.lower()
    assert "token" not in response.text.lower()
