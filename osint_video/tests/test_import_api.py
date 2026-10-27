from __future__ import annotations


from fastapi.testclient import TestClient

from queue_link.api import app
from queue_link import import_api


def test_reject_private_network(monkeypatch):
    monkeypatch.setattr(import_api.socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 0, "", ("127.0.0.1", 80))])
    try:
        import_api._fetch_public_text("http://localhost/")
    except ValueError as exc:
        assert "非公网" in str(exc)
    else:
        raise AssertionError("private host accepted")


def test_file_import_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(import_api, "DATA_DIR", tmp_path)
    tasks = {}

    def create(kind, name, url=None):
        task = {"id": len(tasks) + 1, "source_type": kind, "source_name": name, "status": "processing"}
        tasks[task["id"]] = task
        return task

    def update(task_id, status, **fields):
        tasks[task_id].update(status=status, **fields)
        return tasks[task_id]

    monkeypatch.setattr(import_api, "_create", create)
    monkeypatch.setattr(import_api, "_update", update)
    with TestClient(app) as client:
        response = client.post("/api/v1/imports/files", files=[("files", ("../../report.txt", b"hello", "text/plain")), ("files", ("clip.mp4", b"video", "video/mp4"))])
        assert response.status_code == 200
        first, second = response.json()["data"]["items"]
        assert first["source_name"] == "report.txt"
        assert first["status"] == "completed" and first["content"] == "hello"
        assert second["status"] == "pending_processing" and second["content"] is None
        assert (tmp_path / "imports").exists()
        assert client.post("/api/v1/imports/files", files={"files": ("bad.exe", b"data")}).status_code == 400


def test_link_failure_is_persisted(monkeypatch):
    monkeypatch.setattr(import_api, "_create", lambda *args: {"id": 10})
    monkeypatch.setattr(import_api, "_fetch_public_text", lambda _: (_ for _ in ()).throw(ValueError("登录后可见")))
    saved = {}

    def update(task_id, status, **fields):
        saved.update(id=task_id, status=status, **fields)
        return saved

    monkeypatch.setattr(import_api, "_update", update)
    with TestClient(app) as client:
        response = client.post("/api/v1/imports/links", json={"url": "https://weibo.com/123/456"})
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "failed"
    assert "登录后可见" in saved["error"]
