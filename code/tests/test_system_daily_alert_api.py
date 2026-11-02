r"""GET /api/v1/system/daily-alert 只读告警 API 回归测试。

隔离：全部用 tmp_path + ZHICE_DAILY_ALERT_PATH，不读现场 C:\Temp、不写 DB、不触发任务。
契约：无文件/无 failures -> 204；有 failures -> 200（severity/failures）；损坏 -> 503。
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

DAILY_ALERT_PATH = "/api/v1/system/daily-alert"

_ALERT = {
    "schema": "zhice.daily_alert/v1",
    "event": "daily_tail_partial_failure",
    "severity": "P1",
    "generated_at": "2026-11-02T03:13:36+08:00",
    "run_id": "sim-run-1",
    "source": "schedule",
    "scheduled_at": "2026-11-02T03:00:00+08:00",
    "data_date": "2026-10-31",
    "status": "partial_failure",
    "rc": 1,
    "message": "wrapper tail failed: agent_worker.py(rc=3)",
    "job_status_path": r"C:\Temp\zhice_job_status.json",
    "failures": [
        {
            "name": "agent_worker.py",
            "rc": 3,
            "stderr": "Traceback (most recent call last):\nRuntimeError: boom",
            "stderr_bytes": 53,
            "stderr_truncated": False,
        }
    ],
    "extra_field": "ignored",
}


def _app():
    from zhice_os_v2.api.main import app
    return app


def _write(path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_daily_alert_route_registered():
    assert DAILY_ALERT_PATH in _app().openapi()["paths"]


def test_daily_alert_default_path_is_temp_file():
    from zhice_os_v2.api.routers import system

    assert system.DEFAULT_DAILY_ALERT_PATH == r"C:\Temp\zhice_daily_task_alert.json"


def test_daily_alert_204_when_file_missing(tmp_path, monkeypatch):
    missing = tmp_path / "zhice_daily_task_alert.json"
    monkeypatch.setenv("ZHICE_DAILY_ALERT_PATH", str(missing))

    resp = TestClient(_app()).get(DAILY_ALERT_PATH)

    assert resp.status_code == 204, resp.text
    assert resp.content == b""


def test_daily_alert_204_when_no_failures(tmp_path, monkeypatch):
    path = tmp_path / "zhice_daily_task_alert.json"
    _write(path, {"severity": "P1", "failures": []})
    monkeypatch.setenv("ZHICE_DAILY_ALERT_PATH", str(path))

    resp = TestClient(_app()).get(DAILY_ALERT_PATH)

    assert resp.status_code == 204, resp.text


def test_daily_alert_204_when_empty_object(tmp_path, monkeypatch):
    path = tmp_path / "zhice_daily_task_alert.json"
    _write(path, {})
    monkeypatch.setenv("ZHICE_DAILY_ALERT_PATH", str(path))

    assert TestClient(_app()).get(DAILY_ALERT_PATH).status_code == 204


def test_daily_alert_200_returns_severity_and_failures(tmp_path, monkeypatch):
    path = tmp_path / "zhice_daily_task_alert.json"
    _write(path, _ALERT)
    monkeypatch.setenv("ZHICE_DAILY_ALERT_PATH", str(path))

    resp = TestClient(_app()).get(DAILY_ALERT_PATH)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["severity"] == "P1"
    assert body["event"] == "daily_tail_partial_failure"
    assert body["run_id"] == "sim-run-1"
    assert body["status"] == "partial_failure"
    assert body["rc"] == 1
    assert body["message"] == _ALERT["message"]
    assert len(body["failures"]) == 1
    assert body["failures"][0]["name"] == "agent_worker.py"
    assert body["failures"][0]["rc"] == 3
    assert "Traceback" in body["failures"][0]["stderr"]
    assert body["reported"] is True
    assert body["reported_at"]
    assert body["file"] == {
        "path": str(path), "bytes": path.stat().st_size, "read_only": True,
    }
    assert "extra_field" not in body  # 只返回契约字段 + 文件元数据


def test_daily_alert_handles_utf8_bom(tmp_path, monkeypatch):
    path = tmp_path / "zhice_daily_task_alert.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(_ALERT, ensure_ascii=False).encode("utf-8"))
    monkeypatch.setenv("ZHICE_DAILY_ALERT_PATH", str(path))

    resp = TestClient(_app()).get(DAILY_ALERT_PATH)

    assert resp.status_code == 200, resp.text
    assert resp.json()["severity"] == "P1"


def test_daily_alert_503_when_corrupted(tmp_path, monkeypatch):
    path = tmp_path / "zhice_daily_task_alert.json"
    path.write_text("{not-json", encoding="utf-8")
    monkeypatch.setenv("ZHICE_DAILY_ALERT_PATH", str(path))

    resp = TestClient(_app()).get(DAILY_ALERT_PATH)

    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["code"] == "system.daily_alert_unreadable"


def test_daily_alert_503_when_top_level_not_object(tmp_path, monkeypatch):
    path = tmp_path / "zhice_daily_task_alert.json"
    _write(path, ["not", "an", "object"])
    monkeypatch.setenv("ZHICE_DAILY_ALERT_PATH", str(path))

    resp = TestClient(_app()).get(DAILY_ALERT_PATH)

    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["code"] == "system.daily_alert_invalid"
