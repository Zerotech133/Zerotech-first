# -*- coding: utf-8 -*-
"""Exercise the actual portal HTTP router with an isolated minute JobSpec root.

Run this module alone in a fresh Python process. No production port, catalog,
market connection, or trading endpoint is used by the request sequence.
"""
from datetime import date, datetime, timezone
import hashlib
import http.client
import importlib
import json
from pathlib import Path
import sys
import threading

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


def _fingerprint(path: Path) -> tuple[bool, str | None]:
    """Take a read-only existence/content snapshot."""
    return (path.exists(), hashlib.sha256(path.read_bytes()).hexdigest()
            if path.exists() else None)


def _request(port: int, method: str, url: str, payload: object = None,
             headers: dict[str, str] | None = None) -> tuple[int, dict]:
    """Call the real BaseHTTPRequestHandler route on an ephemeral loopback port."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    body = payload if isinstance(payload, bytes) else (
        json.dumps(payload).encode("utf-8") if payload is not None else None)
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    try:
        connection.request(method, url, body=body, headers=request_headers)
        response = connection.getresponse()
        content = response.read()
        assert response.getheader("Content-Type", "").startswith("application/json")
        return response.status, json.loads(content)
    finally:
        connection.close()


def _bar_and_order() -> tuple[tuple[object, ...], object]:
    """Provide a visible signal bar and a later, eligible execution bar."""
    from bar_dto import Bar
    from execution_engine import OrderIntent

    signal_at = datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc)
    fill_at = datetime(2026, 10, 16, 1, 31, tzinfo=timezone.utc)
    bars = (
        Bar("000001.SZ", "CN", "1m", signal_at, date(2026, 10, 16),
            10, 11, 9, 10, 100, 1000, signal_at, signal_at, True,
            "fixture", "v1", "none", "observed", ("fixture",)),
        Bar("000001.SZ", "CN", "1m", fill_at, date(2026, 10, 16),
            11, 12, 10, 12, 100, 1100, fill_at, fill_at, True,
            "fixture", "v1", "none", "observed", ("fixture",)),
    )
    return bars, OrderIntent("000001.SZ", "BUY", 100, signal_at)


def _serve(portal_module: object) -> tuple[object, threading.Thread, int]:
    """Bind only an OS-selected ephemeral 127.0.0.1 port and start the router."""
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", 0), portal_module.Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, server.server_address[1]


def _stop(server: object, thread: threading.Thread) -> None:
    """Release the owned router before starting a replacement instance."""
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_real_minute_router_start_worker_result_and_cold_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify real POST/GET HTTP statuses and disk-backed cold-start recovery."""
    # A fresh process is mandatory: portal_api imports the backtest module by
    # name, so an already imported production registry cannot be safely reused.
    assert "api_backtest" not in sys.modules
    assert "portal_api" not in sys.modules
    original_paths = list(sys.path)
    sys.path[:0] = [str(PORTAL), str(QBT)]
    baseline = {str(path): _fingerprint(path) for path in SOURCE_FILES}
    default_before = _fingerprint(DEFAULT_CATALOG)
    sandbox = tmp_path.resolve()
    catalog = sandbox / "data" / "runtime" / "minute_job_specs.json"
    assert catalog != DEFAULT_CATALOG.resolve()
    worker_done = threading.Event()
    worker_errors: list[BaseException] = []
    server = None
    server_thread = None
    engine = None
    engine_output_paths: tuple[str, str] | None = None
    try:
        monkeypatch.setenv("PORTAL_NO_WARMUP", "1")
        import api_common
        import job_spec_registry
        import backtest_engine_v2 as engine
        from bar_store import BarStore

        engine_output_paths = (str(engine.MINUTE_OUT_DIR), str(engine.OUT_DIR))
        monkeypatch.setattr(api_common, "STATION", str(sandbox))
        monkeypatch.setattr(api_common, "REPORTS_DIR", str(sandbox / "reports"))
        monkeypatch.setattr(api_common, "QBT_DIR", str(QBT))
        monkeypatch.setattr(api_common, "_ERROR_LOG", str(sandbox / "portal_error.log"))
        engine.MINUTE_OUT_DIR = str(sandbox / "minute")
        engine.OUT_DIR = str(sandbox / "daily")
        original_registry = job_spec_registry.JobSpecRegistry

        class GuardedRegistry(original_registry):
            """Refuse all attempts to load/save a production JobSpec catalog."""

            def __init__(self, catalog_path: str) -> None:
                assert Path(catalog_path).resolve() == catalog
                super().__init__(catalog_path)

            def _save(self) -> None:
                assert Path(self.catalog_path).resolve() == catalog
                super()._save()

        monkeypatch.setattr(job_spec_registry, "JobSpecRegistry", GuardedRegistry)
        portal_api = importlib.import_module("portal_api")
        api = importlib.import_module("api_backtest")
        # split B1 后 worker 线程在 api_backtest_bt2 模块内启动，补丁必须打在
        # 该模块的全局名上（只改 api_backtest 再导出的副本不会影响线程目标）。
        bt2 = importlib.import_module("api_backtest_bt2")
        monkeypatch.setattr(portal_api, "_ACCESS_LOG", str(sandbox / "portal_access.log"))
        assert Path(api._JOB_SPEC_REGISTRY.catalog_path).resolve() == catalog
        assert api._JOB_SPEC_REGISTRY.descriptors() == {}
        assert Path(api._BT2_STATE_DIR).resolve().is_relative_to(sandbox)
        assert Path(engine.MINUTE_OUT_DIR).resolve().is_relative_to(sandbox)
        assert _fingerprint(DEFAULT_CATALOG) == default_before

        bars, intent = _bar_and_order()
        api.register_minute_source(
            "router_fixture", BarStore(bars), [intent],
            status_provider=lambda *args: "TRADING",
            limit_provider=lambda *args: {"known": True},
        )
        assert catalog.exists()
        real_worker = bt2._bt2_worker

        def tracked_worker(job_id: str, params: dict) -> None:
            try:
                real_worker(job_id, params)
            except BaseException as error:
                worker_errors.append(error)
                raise
            finally:
                worker_done.set()

        monkeypatch.setattr(bt2, "_bt2_worker", tracked_worker)
        monkeypatch.setattr(api, "_bt2_worker", tracked_worker, raising=False)
        server, server_thread, port = _serve(portal_api)
        # The bounded d0/d1 query enforces closed bars; make as-of explicit so
        # both observed fixture bars remain visible without weakening that gate.
        request = {"execution_mode": "minute_store", "source_ref": "router_fixture",
                   "strategy": "minute_store", "d0": "20261016", "d1": "20261016",
                   "as_of": bars[1].dt.isoformat(), "initial_cash": 2000}

        # Direct loopback requests (no proxy headers) remain trusted by
        # cainiao_auth. Since the P1 trust-source fix (2026-10-19), proxy
        # headers with a non-loopback X-Real-IP are no longer trusted even
        # from a loopback fixture, so the spoofed-proxy 403 IS verifiable
        # here (previously the loopback client_address masked it).
        status, proxied = _request(
            port, "POST", "/api/bt2_run", {**request, "source_ref": "missing"})
        assert status == 200 and proxied["error_code"] == "SOURCE_UNAVAILABLE"
        status, spoofed = _request(
            port, "POST", "/api/bt2_run", {**request, "source_ref": "missing"},
            {"X-Portal-Proxy": "1", "X-Real-IP": "192.0.2.1"})
        assert status == 403 and "forbidden" in (spoofed.get("error") or "")
        status, malformed = _request(port, "POST", "/api/bt2_run", b"{broken")
        assert status == 400 and malformed == {"ok": False, "error": "bad json"}
        status, unavailable = _request(
            port, "POST", "/api/bt2_run", {**request, "source_ref": "missing"})
        assert status == 200 and unavailable["error_code"] == "SOURCE_UNAVAILABLE"
        status, invalid_window = _request(
            port, "POST", "/api/bt2_run", {**request, "d0": "20261017", "d1": "20261016"})
        assert status == 200 and invalid_window["error_code"] == "TIME_INVALID"
        status, bad_id = _request(port, "GET", "/api/bt2_job?id=../bad")
        assert status == 200 and bad_id == {"ok": False, "error": "bad id"}
        status, missing = _request(port, "GET", "/api/bt2_job?id=missing-job")
        assert status == 200 and missing == {"ok": False, "error": "job not found"}

        status, started = _request(port, "POST", "/api/bt2_run", request)
        assert status == 200 and started["ok"] is True
        job_id = started["job"]["id"]
        assert started["job"]["status"] == "queued"
        state_file = Path(api._bt2_state_path(job_id))
        assert state_file.resolve().is_relative_to(sandbox)
        assert worker_done.wait(timeout=30), "automatic engine worker did not finish"
        assert not worker_errors, repr(worker_errors)
        persisted = json.loads(state_file.read_text(encoding="utf-8"))
        assert persisted["status"] == "done" and persisted["execution_mode"] == "minute_store"
        assert persisted["params"]["source_ref"] == "router_fixture"
        artifact = Path(engine.MINUTE_OUT_DIR) / f"minute_{job_id}.json"
        assert artifact.exists() and artifact.resolve().is_relative_to(sandbox)
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        assert persisted["artifact_bytes"] == artifact.stat().st_size
        assert persisted["artifact_sha256"] == digest

        status, jobs = _request(port, "GET", "/api/bt2_jobs")
        assert status == 200 and jobs["ok"] is True
        job = next(item for item in jobs["jobs"] if item["id"] == job_id)
        assert job["status"] == "done" and job["source_availability"] == "available"
        status, result = _request(port, "GET", f"/api/bt2_job?id={job_id}")
        assert status == 200 and result["ok"] is True
        assert result["schema"] == "minute-exec-v1" and result["job_id"] == job_id
        assert result["artifact_sha256"] == digest
        assert len(result["orders"]) == len(result["fills"]) == len(result["audits"]) == 1
        order, fill, audit = result["orders"][0], result["fills"][0], result["audits"][0]
        assert order["symbol"] == fill["symbol"] == audit["symbol"] == "000001.SZ"
        assert order["side"] == fill["side"] == audit["side"] == "BUY"
        assert order["quantity"] == audit["requested_quantity"] == fill["quantity"] == 100
        assert fill["order_id"] == audit["order_id"] == "order-1"
        assert result["query_metadata"]["rows"] == 2
        assert result["query_metadata"]["as_of"] == bars[1].dt.isoformat()
        assert audit["query_metadata"]["eligibility_policy_ref"] == "strict_next_bar_v1"
        assert datetime.fromisoformat(fill["dt"]) == bars[1].dt
        assert fill["dt"] == audit["eligible_at"] == audit["fill_at"]
        assert datetime.fromisoformat(audit["submitted_at"]) == bars[0].dt
        assert audit["status"] == "filled" and audit["reason"] == "FILLED"
        assert audit["filled_quantity"] == 100 and audit["remaining_quantity"] == 0
        assert audit["fill_count"] == 1
        # Default portal cost policy has no fee provider and zero fee/slippage bps.
        assert fill["price"] == bars[1].o == 11
        assert fill["gross_value"] == 1100
        assert fill["fee"] == fill["slippage"] == 0
        assert fill["fee_mode"] == audit["fee_mode"] == "LEGACY_BPS"
        assert fill["fee_source"] == audit["fee_source"] == "legacy_bps"
        assert result["cash"] == 2000 - fill["gross_value"] - fill["fee"] == 900
        assert result["positions"] == {"000001.SZ": 100}
        assert result["equity"] == result["cash"] + 100 * bars[1].c == 2100
        assert result["metrics"]["submitted"] == result["metrics"]["filled"] == 1
        assert result["metrics"]["fully_filled_count"] == 1
        assert result["metrics"]["filled_quantity"] == 100
        assert result["metrics"]["unfilled"] == result["metrics"]["partial_count"] == 0
        ledger = {key: result[key] for key in
                  ("orders", "fills", "audits", "cash", "equity", "positions", "metrics")}

        _stop(server, server_thread)
        server = None
        server_thread = None
        assert all(_fingerprint(path) == baseline[str(path)] for path in SOURCE_FILES), "source snapshot drift before restart"
        # Reimport real router and real API module: this is a new in-process
        # application instance over the same isolated persisted catalog/state.
        del sys.modules["portal_api"]
        del sys.modules["api_backtest"]
        # 冷启动必须同时丢弃 bt2 模块：分钟源注册表是进程内存态，
        # 若 bt2 仍被 sys.modules 缓存，cold reimport 会错误地保持 available。
        sys.modules.pop("api_backtest_bt2", None)
        sys.modules.pop("api_backtest_minute", None)  # 分钟源注册表/锁所在模块，需一并重建
        cold_portal = importlib.import_module("portal_api")
        cold_api = importlib.import_module("api_backtest")
        monkeypatch.setattr(cold_portal, "_ACCESS_LOG", str(sandbox / "portal_access.log"))
        assert Path(cold_api._JOB_SPEC_REGISTRY.catalog_path).resolve() == catalog
        assert cold_api._JOB_SPEC_REGISTRY.get("router_fixture") is not None
        assert cold_api._JOB_SPEC_REGISTRY.get("router_fixture").availability == "source_unavailable"
        server, server_thread, port = _serve(cold_portal)
        status, recovered_jobs = _request(port, "GET", "/api/bt2_jobs")
        assert status == 200 and any(item["id"] == job_id for item in recovered_jobs["jobs"])
        status, recovered = _request(port, "GET", f"/api/bt2_job?id={job_id}")
        assert status == 200 and recovered["ok"] is True
        assert recovered["artifact_sha256"] == digest
        assert {key: recovered[key] for key in ledger} == ledger
        assert recovered["source_availability"] == "source_unavailable"
        status, blocked = _request(port, "POST", "/api/bt2_run", request)
        assert status == 200 and blocked["ok"] is False
        assert blocked["error_code"] == "SOURCE_UNAVAILABLE"
        assert _fingerprint(DEFAULT_CATALOG) == default_before
    finally:
        if server is not None and server_thread is not None:
            _stop(server, server_thread)
        # On a worker timeout, never restore engine output paths: a daemon still
        # running must not regain the production output directory.
        if engine is not None and engine_output_paths is not None and worker_done.is_set():
            engine.MINUTE_OUT_DIR, engine.OUT_DIR = engine_output_paths
        sys.modules.pop("portal_api", None)
        sys.modules.pop("api_backtest", None)
        sys.path[:] = original_paths
        assert _fingerprint(DEFAULT_CATALOG) == default_before, "default catalog modified"
        assert all(_fingerprint(path) == baseline[str(path)] for path in SOURCE_FILES), "source snapshot drift"
