"""Contract tests for the read-only Hermes Watch Tower connector."""
from __future__ import annotations

import httpx
import pytest

from zhice_os_v2.infrastructure.hermes.watch_tower_connector import (
    WATCH_TOWER_SCHEMA,
    HermesWatchTowerConnector,
    WatchTowerConnectorError,
)


class FakeClient:
    """Small injectable HTTP client used to keep connector tests network-free."""

    def __init__(self, response: object | None = None, error: Exception | None = None) -> None:
        self.response: object | None = response
        self.error: Exception | None = error
        self.calls: list[tuple[str, float]] = []

    def get(self, url: str, timeout: float) -> object:
        self.calls.append((url, timeout))
        if self.error is not None:
            raise self.error
        if self.response is None:
            raise AssertionError("FakeClient response was not configured")
        return self.response


class FakeResponse:
    def __init__(self, status_code: int, payload: object = None, json_error: Exception | None = None) -> None:
        self.status_code: int = status_code
        self.payload: object = payload
        self.json_error: Exception | None = json_error

    def json(self) -> object:
        if self.json_error is not None:
            raise self.json_error
        return self.payload


def _valid_payload() -> dict[str, object]:
    return {"schema": WATCH_TOWER_SCHEMA, "version": "1", "data": {"symbols": []}}


def test_get_snapshot_success_and_records_attribution_only_on_success() -> None:
    events: list[dict[str, str]] = []
    client = FakeClient(FakeResponse(200, _valid_payload()))
    connector = HermesWatchTowerConnector(
        base_url="http://127.0.0.1:9010/",
        timeout_seconds=1.25,
        client=client,
        audit_recorder=events.append,
    )

    snapshot = connector.get_snapshot()

    assert snapshot.schema == WATCH_TOWER_SCHEMA
    assert snapshot.data == {"symbols": []}
    assert client.calls == [("http://127.0.0.1:9010/api/watch-tower/snapshot", 1.25)]
    assert events == [{"caller": "hermes-watch-tower", "operation": "snapshot_read", "schema": WATCH_TOWER_SCHEMA}]


def test_get_snapshot_timeout_is_fail_visible() -> None:
    client = FakeClient(error=httpx.ReadTimeout("timed out"))
    connector = HermesWatchTowerConnector(client=client)

    with pytest.raises(WatchTowerConnectorError, match="timed out") as caught:
        connector.get_snapshot()

    assert caught.value.code == "watch_tower.timeout"
    assert caught.value.status_code == 502


def test_get_snapshot_non_2xx_is_fail_visible() -> None:
    connector = HermesWatchTowerConnector(client=FakeClient(FakeResponse(503, {"error": "down"})))

    with pytest.raises(WatchTowerConnectorError) as caught:
        connector.get_snapshot()

    assert caught.value.code == "watch_tower.upstream_http_error"
    assert caught.value.status_code == 502


def test_get_snapshot_bad_json_is_fail_visible() -> None:
    response = FakeResponse(200, json_error=ValueError("not json"))
    connector = HermesWatchTowerConnector(client=FakeClient(response))

    with pytest.raises(WatchTowerConnectorError) as caught:
        connector.get_snapshot()

    assert caught.value.code == "watch_tower.invalid_json"


@pytest.mark.parametrize(
    "payload, expected_code",
    [
        ({"schema": "other.v1", "version": "1", "data": {}}, "watch_tower.schema_mismatch"),
        ({"schema": WATCH_TOWER_SCHEMA, "version": "1"}, "watch_tower.invalid_structure"),
        ({"schema": WATCH_TOWER_SCHEMA, "version": "1", "data": []}, "watch_tower.invalid_structure"),
        ({"schema": WATCH_TOWER_SCHEMA, "version": "", "data": {}}, "watch_tower.invalid_structure"),
    ],
)
def test_get_snapshot_schema_and_structure_mismatch(payload: object, expected_code: str) -> None:
    connector = HermesWatchTowerConnector(client=FakeClient(FakeResponse(200, payload)))

    with pytest.raises(WatchTowerConnectorError) as caught:
        connector.get_snapshot()

    assert caught.value.code == expected_code
