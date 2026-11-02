r"""GET /api/v1/system/job-status 只读任务状态 API 的回归测试（200/缺失/损坏）。

隔离原则：端点只读文件，测试用 tmp 文件 + 环境变量 ZHICE_JOB_STATUS_PATH 隔离，
不依赖 C:\Temp 现场文件、不写 DB；公开只读端点（与 /health、/status 同 system router）。
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

JOB_STATUS_PATH = "/api/v1/system/job-status"

_BUNDLE = {
    "status": "success",
    "scheduled_at": "2026-11-01T17:00:00+08:00",
    "started_at": "2026-11-01T17:00:07+08:00",
    "finished_at": "2026-11-01T17:02:31+08:00",
    "source": "scheduled",
    "data_date": "2026-10-31",
    "rc": 0,
    "steps": [{"name": "acad1_pool", "rc": 0, "elapsed_sec": 3.2}],
}


def _app():
    from zhice_os_v2.api.main import app
    return app


def _write(path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_job_status_route_registered():
    assert JOB_STATUS_PATH in _app().openapi()["paths"]


def test_job_status_default_path_is_temp_file():
    from zhice_os_v2.api.routers import system

    assert system.DEFAULT_JOB_STATUS_PATH == r"C:\Temp\zhice_job_status.json"


def test_job_status_200_returns_contract_fields(tmp_path, monkeypatch):
    path = tmp_path / "zhice_job_status.json"
    _write(path, {**_BUNDLE, "extra_field": "ignored"})
    monkeypatch.setenv("ZHICE_JOB_STATUS_PATH", str(path))

    resp = TestClient(_app()).get(JOB_STATUS_PATH)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    for field in (
        "status", "scheduled_at", "started_at", "finished_at",
        "source", "data_date", "rc", "steps",
    ):
        assert body[field] == _BUNDLE[field], field
    assert body["reported"] is True
    assert body["file"] == {"path": str(path), "bytes": path.stat().st_size, "read_only": True}
    assert body["reported_at"]
    assert "extra_field" not in body  # 只返回契约字段 + 文件元数据，不透传任意字段


def test_job_status_200_steps_default_empty(tmp_path, monkeypatch):
    path = tmp_path / "zhice_job_status.json"
    _write(path, {key: value for key, value in _BUNDLE.items() if key != "steps"})
    monkeypatch.setenv("ZHICE_JOB_STATUS_PATH", str(path))

    resp = TestClient(_app()).get(JOB_STATUS_PATH)

    assert resp.status_code == 200, resp.text
    assert resp.json()["steps"] == []


def test_job_status_404_when_not_reported(tmp_path, monkeypatch):
    missing = tmp_path / "zhice_job_status.json"  # 不创建文件
    monkeypatch.setenv("ZHICE_JOB_STATUS_PATH", str(missing))

    resp = TestClient(_app()).get(JOB_STATUS_PATH)

    assert resp.status_code == 404, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "system.job_status_not_reported"
    assert "任务尚未上报" in detail["message"]
    assert detail["path"] == str(missing)
    assert "hint" in detail


def test_job_status_503_when_corrupted(tmp_path, monkeypatch):
    broken = tmp_path / "zhice_job_status.json"
    broken.write_text("{not-json", encoding="utf-8")
    monkeypatch.setenv("ZHICE_JOB_STATUS_PATH", str(broken))

    resp = TestClient(_app()).get(JOB_STATUS_PATH)

    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["code"] == "system.job_status_unreadable"


def test_job_status_503_when_top_level_not_object(tmp_path, monkeypatch):
    invalid = tmp_path / "zhice_job_status.json"
    _write(invalid, ["not", "an", "object"])
    monkeypatch.setenv("ZHICE_JOB_STATUS_PATH", str(invalid))

    resp = TestClient(_app()).get(JOB_STATUS_PATH)

    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["code"] == "system.job_status_invalid"
