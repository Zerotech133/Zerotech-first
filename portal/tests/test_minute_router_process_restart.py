# -*- coding: utf-8 -*-
"""Exercise minute HTTP recovery across two separate portal OS processes.

Only an ephemeral loopback listener and synthetic in-memory Bar/Order are used.
The real portal Handler and the real minute worker remain in the request path.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import http.client
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

import pytest


REPO = Path(__file__).resolve().parents[2]
PORTAL = REPO / "portal"
QBT = REPO / "quant_backtest"
DEFAULT_CATALOG = REPO / "data" / "runtime" / "minute_job_specs.json"
SOURCE_FILES = (
    PORTAL / "portal_api.py", PORTAL / "api_backtest.py",
    PORTAL / "job_spec_registry.py", PORTAL / "api_common.py",
    QBT / "backtest_engine_v2.py", QBT / "execution_engine.py",
)
REQUEST = {
    "execution_mode": "minute_store", "source_ref": "process_fixture",
    "strategy": "minute_store", "d0": "20261016", "d1": "20261016",
    "initial_cash": 2000,
}


def _fingerprint(path: Path) -> tuple[bool, str | None]:
    """Record both existence and the SHA256 of a protected input."""
    return (path.exists(), hashlib.sha256(path.read_bytes()).hexdigest()
            if path.exists() else None)


def _request(port: int, method: str, url: str,
             payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    """Issue one bounded HTTP request to our explicitly owned loopback port."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    try:
        connection.request(method, url, body=body,
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        assert response.getheader("Content-Type", "").startswith("application/json")
        result = json.loads(response.read())
        assert isinstance(result, dict)
        return response.status, result
    finally:
        connection.close()


def _child(stage: str, root: Path, ready_file: Path) -> None:
    """Bootstrap the genuine portal after redirecting all mutable fixture roots."""
    assert root.is_absolute() and ready_file.parent == root
    assert (root / "data" / "runtime" / "minute_job_specs.json").resolve() != DEFAULT_CATALOG.resolve()
    sys.path[:0] = [str(PORTAL), str(QBT)]
    os.environ["PORTAL_NO_WARMUP"] = "1"
    os.environ["PORTAL_API_TOKEN"] = "isolated-process-fixture-token"
    import api_common
    import job_spec_registry
    import backtest_engine_v2 as engine

    api_common.STATION = str(root)
    api_common.REPORTS_DIR = str(root / "reports")
    api_common.QBT_DIR = str(QBT)
    api_common._ERROR_LOG = str(root / "portal_error.log")
    api_common._PORTAL_TOKEN_FILE = str(root / "portal_token")
    engine.MINUTE_OUT_DIR = str(root / "minute")
    engine.OUT_DIR = str(root / "daily")
    catalog = (root / "data" / "runtime" / "minute_job_specs.json").resolve()
    original_registry = job_spec_registry.JobSpecRegistry

    class GuardedRegistry(original_registry):
        """Prevent import, reload, and save against the default catalog."""

        def __init__(self, catalog_path: str) -> None:
            assert Path(catalog_path).resolve() == catalog
            super().__init__(catalog_path)

        def load(self) -> None:
            assert Path(self.catalog_path).resolve() == catalog
            super().load()

        def _save(self) -> None:
            assert Path(self.catalog_path).resolve() == catalog
            super()._save()

    job_spec_registry.JobSpecRegistry = GuardedRegistry
    portal_api = importlib.import_module("portal_api")
    api = importlib.import_module("api_backtest")
    portal_api._ACCESS_LOG = str(root / "portal_access.log")
    assert Path(api._JOB_SPEC_REGISTRY.catalog_path).resolve() == catalog
    assert Path(api._BT2_STATE_DIR).resolve().is_relative_to(root)
    assert Path(engine.MINUTE_OUT_DIR).resolve().is_relative_to(root)
    if stage == "first":
        assert api._JOB_SPEC_REGISTRY.descriptors() == {}
        from bar_dto import Bar
        from bar_store import BarStore
        from execution_engine import OrderIntent

        signal_time = datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc)
        fill_time = datetime(2026, 10, 16, 1, 31, tzinfo=timezone.utc)
        # BarQuery(closed_only=True, as_of=None) excludes non-null close_at;
        # closed, timestamped synthetic bars are valid without close_at.
        signal_bar = Bar("000001.SZ", "CN", "1m", signal_time, date(2026, 10, 16),
                         10, 11, 9, 10, 100, 100, None, signal_time, True,
                         "fixture", "v1", "none", "observed", ("fixture",))
        next_bar = Bar("000001.SZ", "CN", "1m", fill_time, date(2026, 10, 16),
                       10, 11, 9, 10, 100, 100, None, fill_time, True,
                       "fixture", "v1", "none", "observed", ("fixture",))
        intent = OrderIntent("000001.SZ", "BUY", 100, signal_time)
        api.register_minute_source(
            "process_fixture", BarStore([signal_bar, next_bar]), [intent],
            status_provider=lambda *args: "TRADING",
            limit_provider=lambda *args: {"known": True},
        )
    elif stage == "second":
        assert api._JOB_SPEC_REGISTRY.get("process_fixture") is not None
        assert api._JOB_SPEC_REGISTRY.get("process_fixture").availability == "source_unavailable"
        assert "process_fixture" not in api._MINUTE_SOURCE_REGISTRY
    else:
        raise ValueError("unknown process stage")

    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", 0), portal_api.Handler)
    server.daemon_threads = False
    try:
        ready_file.write_text(json.dumps({"port": server.server_address[1],
                                          "pid": os.getpid()}), encoding="utf-8")
        # Main thread receives an explicit stop command on the private stdin
        # pipe; HTTP worker threads are allowed to finish before process exit.
        import threading
        serving = threading.Thread(target=server.serve_forever, daemon=False)
        serving.start()
        assert sys.stdin.readline().strip() == "STOP"
        server.shutdown()
        serving.join(timeout=5)
        assert not serving.is_alive()
    finally:
        server.server_close()


def _launch(stage: str, root: Path) -> tuple[subprocess.Popen[str], int]:
    """Start exactly one child and receive its private ephemeral port."""
    ready_file = root / f"{stage}.ready.json"
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--child", stage,
         str(root), str(ready_file)], cwd=str(root),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env={**os.environ, "PORTAL_NO_WARMUP": "1",
                        "PORTAL_API_TOKEN": "isolated-process-fixture-token",
                        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPYCACHEPREFIX": str(root / "pycache")},
    )
    deadline = time.monotonic() + 25
    try:
        while time.monotonic() < deadline:
            if ready_file.exists():
                details = json.loads(ready_file.read_text(encoding="utf-8"))
                assert details["pid"] == process.pid
                port = details["port"]
                assert isinstance(port, int) and 0 < port < 65536
                assert process.poll() is None
                return process, port
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=5)
                pytest.fail(f"portal child exited {process.returncode}: {stdout} {stderr}")
            time.sleep(0.05)
        pytest.fail(f"portal child startup exceeded 25s ({stage})")
    except BaseException:
        _stop(process, graceful=False)
        raise


def _stop(process: subprocess.Popen[str], graceful: bool = True) -> None:
    """Bound child termination and surface any background handler failure."""
    if graceful and process.poll() is None and process.stdin is not None:
        try:
            process.stdin.write("STOP\n")
            process.stdin.flush()
        except BrokenPipeError:
            pass
    if not graceful and process.poll() is None:
        process.kill()
    try:
        stdout, stderr = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate(timeout=5)
        pytest.fail(f"portal child did not stop within 10s: {stdout} {stderr}")
    if graceful:
        assert process.returncode == 0, f"portal child exited {process.returncode}: {stdout} {stderr}"


def test_minute_router_recovers_after_real_process_restart(tmp_path: Path) -> None:
    """Persist first child's HTTP result and recover it in a fresh interpreter."""
    root = tmp_path.resolve()
    snapshots = {str(path): _fingerprint(path) for path in (*SOURCE_FILES, DEFAULT_CATALOG)}
    catalog = root / "data" / "runtime" / "minute_job_specs.json"
    assert catalog.resolve() != DEFAULT_CATALOG.resolve()
    first: subprocess.Popen[str] | None = None
    second: subprocess.Popen[str] | None = None
    try:
        first, port = _launch("first", root)
        status, started = _request(port, "POST", "/api/bt2_run", REQUEST)
        assert status == 200 and started["ok"] is True
        job_id = started["job"]["id"]
        assert started["job"]["status"] == "queued"
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            status, jobs = _request(port, "GET", "/api/bt2_jobs")
            assert status == 200 and jobs["ok"] is True
            found = [item for item in jobs["jobs"] if item["id"] == job_id]
            if found and found[0]["status"] in ("done", "dead"):
                break
            time.sleep(0.1)
        else:
            pytest.fail("first process worker exceeded 35s")
        assert found[0]["status"] == "done", found[0]
        status, result = _request(port, "GET", f"/api/bt2_job?id={job_id}")
        assert status == 200 and result["ok"] is True
        assert result["schema"] == "minute-exec-v1" and result["job_id"] == job_id
        assert all(key in result for key in ("orders", "fills", "audits", "metrics"))
        assert result["metrics"]["submitted"] == 1
        assert result["metrics"]["filled"] == 1
        assert len(result["fills"]) == 1 and len(result["audits"]) == 1
        assert result["audits"][0]["status"] == "filled"
        state = root / "quant_backtest" / "data" / "bt2_state" / f"bt2_{job_id}.state.json"
        artifact = root / "minute" / f"minute_{job_id}.json"
        assert catalog.exists() and state.is_file() and artifact.is_file()
        persisted = json.loads(state.read_text(encoding="utf-8"))
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        assert persisted["status"] == "done"
        assert persisted["artifact_bytes"] == artifact.stat().st_size
        assert persisted["artifact_sha256"] == result["artifact_sha256"] == digest
        _stop(first)
        first = None
        assert all(_fingerprint(Path(path)) == fingerprint
                   for path, fingerprint in snapshots.items()), "protected input drift after first child"

        second, port = _launch("second", root)
        status, recovered_jobs = _request(port, "GET", "/api/bt2_jobs")
        assert status == 200 and recovered_jobs["ok"] is True
        recovered_job = next(item for item in recovered_jobs["jobs"] if item["id"] == job_id)
        assert recovered_job["status"] == "done"
        assert recovered_job["source_availability"] == "source_unavailable"
        status, recovered = _request(port, "GET", f"/api/bt2_job?id={job_id}")
        assert status == 200 and recovered["ok"] is True
        assert recovered["artifact_sha256"] == digest
        assert recovered["source_availability"] == "source_unavailable"
        status, blocked = _request(port, "POST", "/api/bt2_run", REQUEST)
        assert status == 200 and blocked["ok"] is False
        assert blocked["error_code"] == "SOURCE_UNAVAILABLE"
        _stop(second)
        second = None
    finally:
        if first is not None:
            _stop(first, graceful=False)
        if second is not None:
            _stop(second, graceful=False)
        assert all(_fingerprint(Path(path)) == fingerprint
                   for path, fingerprint in snapshots.items()), "protected input drift after process test"


if __name__ == "__main__":
    if len(sys.argv) != 5 or sys.argv[1] != "--child":
        raise SystemExit("process fixture requires --child stage root ready_file")
    _child(sys.argv[2], Path(sys.argv[3]).resolve(), Path(sys.argv[4]).resolve())
