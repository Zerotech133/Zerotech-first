# -*- coding: utf-8 -*-
"""Minute API integration tests with an import-time isolated JobSpec catalog."""

from dataclasses import replace
from datetime import date, datetime, timezone
import hashlib
import importlib.util
import json
import sys
import threading
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

PORTAL_ROOT = Path(__file__).resolve().parents[1]
QBT_ROOT = PORTAL_ROOT.parent / "quant_backtest"
sys.path.insert(0, str(PORTAL_ROOT))
sys.path.insert(0, str(QBT_ROOT))

from bar_dto import Bar
from bar_store import BarStore
from execution_engine import OrderIntent
from market_rules import MarketRuleProvider


def _catalog_fingerprint(path: Path) -> tuple[bool, str | None, int | None]:
    """Capture content and timestamp without modifying the default catalog."""
    if not path.exists():
        return False, None, None
    return True, hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Import a new API module against only the per-test catalog and outputs.

    The registry guard is retained by the imported module after import-time patches
    are removed. A timed-out worker is never allowed to regain a production output
    directory during teardown.
    """
    import api_common
    import job_spec_registry
    import backtest_engine_v2 as engine

    sandbox = tmp_path.resolve()
    catalog = sandbox / "data" / "runtime" / "minute_job_specs.json"
    default_catalog = (
        PORTAL_ROOT.parent / "data" / "runtime" / "minute_job_specs.json"
    ).resolve()
    assert catalog.resolve() != default_catalog
    before = _catalog_fingerprint(default_catalog)
    registry_class = job_spec_registry.JobSpecRegistry

    class IsolatedRegistry(registry_class):
        """Deny any JobSpec load or save outside this test's catalog."""

        def __init__(self, catalog_path: str) -> None:
            if Path(catalog_path).resolve() != catalog:
                raise AssertionError("JobSpec attempted to load a non-test catalog")
            super().__init__(catalog_path)

        def _save(self) -> None:
            if Path(self.catalog_path).resolve() != catalog:
                raise AssertionError("JobSpec attempted to write a non-test catalog")
            super()._save()

    # api_backtest computes its catalog path and performs startup recovery while
    # being imported. Patch its source constants and registry BEFORE exec_module.
    with monkeypatch.context() as import_patches:
        import_patches.setattr(api_common, "STATION", str(sandbox))
        import_patches.setattr(api_common, "REPORTS_DIR", str(sandbox / "reports"))
        import_patches.setattr(api_common, "QBT_DIR", str(sandbox / "quant_backtest"))
        import_patches.setattr(job_spec_registry, "JobSpecRegistry", IsolatedRegistry)

        # 2026-10-30 P68（B1 拆分配套）：api_backtest 的 minute/bt2 机制已迁至
        # api_backtest_minute.py / api_backtest_bt2.py。为保持“单文件沙箱”语义，
        # 在 patched 上下文内分别加载两份隔离副本，并用 sys.modules 别名让
        # api_backtest.py 的 from ... import 绑定到隔离副本。
        def _load_iso(name, path, alias_map):
            spec_i = importlib.util.spec_from_file_location(name, path)
            assert spec_i is not None and spec_i.loader is not None
            mod = importlib.util.module_from_spec(spec_i)
            sys.modules[name] = mod
            spec_i.loader.exec_module(mod)
            return mod

        saved_modules = {k: sys.modules.get(k) for k in
                         ("api_backtest_minute", "api_backtest_bt2")}
        isolated_minute = _load_iso("api_backtest_minute",
                                    PORTAL_ROOT / "api_backtest_minute.py", {})
        isolated_bt2 = _load_iso("api_backtest_bt2",
                                 PORTAL_ROOT / "api_backtest_bt2.py", {})
        spec = importlib.util.spec_from_file_location(
            f"_test_api_backtest_minute_{sandbox.name}", PORTAL_ROOT / "api_backtest.py"
        )
        assert spec is not None and spec.loader is not None
        isolated_api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(isolated_api)
        # 恢复真实模块名（isolated_api 内已绑定隔离副本的函数对象）
        for k, v in saved_modules.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    assert Path(isolated_api._JOB_SPEC_CATALOG).resolve() == catalog
    assert Path(isolated_api._JOB_SPEC_REGISTRY.catalog_path).resolve() == catalog
    assert isolated_api._JOB_SPEC_REGISTRY.descriptors() == {}
    assert _catalog_fingerprint(default_catalog) == before

    # 2026-10-30 P68：isolated_api 还承担“旧单文件模块”的赋值入口（测试会改
    # _BT2_DIR/_JOB_SPEC_* 等）；切换到转发 ModuleType，赋值同时落到底层子模块。
    class _IsoApiModule(types.ModuleType):
        _iso_targets = (isolated_minute, isolated_bt2)

        def __setattr__(self, name, value):
            super().__setattr__(name, value)
            for _m in self._iso_targets:
                if hasattr(_m, name):
                    setattr(_m, name, value)

    isolated_api.__class__ = _IsoApiModule

    # These assignments belong to the private modules, not an already-imported API.
    isolated_api.STATION = str(sandbox)
    isolated_minute.STATION = str(sandbox)
    isolated_minute.log_exc = lambda error: None
    isolated_bt2.STATION = str(sandbox)
    isolated_bt2.log_exc = lambda error: None  # No production error-log writes.
    isolated_bt2._BT2_STATE_DIR = str(sandbox / "state")
    isolated_bt2._BT2_DIR = str(sandbox / "daily")
    isolated_api._bt2_state_dir_alias = None  # keep attribute checks backwards-compatible

    # backtest_engine_v2 is shared in sys.modules. Restore ONLY after every
    # auto-started worker has exited; on a timeout leave the safe temp paths set.
    original_output = engine.MINUTE_OUT_DIR
    original_daily = engine.OUT_DIR
    engine.MINUTE_OUT_DIR = str(sandbox / "minute")
    engine.OUT_DIR = str(sandbox / "daily")
    finished: dict[str, threading.Event] = {}
    worker_errors: dict[str, BaseException] = {}
    original_worker = isolated_bt2._bt2_worker

    def tracked_worker(job_id: str, params: dict) -> None:
        event = finished.setdefault(job_id, threading.Event())
        try:
            original_worker(job_id, params)
        except BaseException as error:
            worker_errors[job_id] = error
            raise
        finally:
            event.set()

    isolated_bt2._bt2_worker = tracked_worker
    isolated_api._bt2_worker = tracked_worker

    def await_job(job_id: str) -> dict:
        """Wait at most 30 seconds for the *automatic* worker to exit."""
        event = finished.setdefault(job_id, threading.Event())
        assert event.wait(timeout=30), f"automatic minute worker timed out: {job_id}"
        assert job_id not in worker_errors, repr(worker_errors.get(job_id))
        return isolated_api._bt2_result(job_id)

    isolated_api.await_job = await_job
    try:
        yield isolated_api
    finally:
        if all(event.is_set() for event in finished.values()):
            engine.MINUTE_OUT_DIR = original_output
            engine.OUT_DIR = original_daily
        assert _catalog_fingerprint(default_catalog) == before, (
            "default JobSpec catalog changed"
        )


def _bar(minute: int) -> Bar:
    dt = datetime(2026, 10, 16, 1, minute, tzinfo=timezone.utc)
    return Bar(
        "000001.SZ",
        "CN",
        "1m",
        dt,
        date(2026, 10, 16),
        10,
        11,
        9,
        10,
        100,
        100,
        dt,
        dt,
        True,
        "fixture",
        "v1",
        "none",
        "observed",
        ("fixture",),
    )


def test_minute_start_worker_status_result(api) -> None:
    store = BarStore([_bar(30), _bar(31)])
    api.register_minute_source(
        "fixture",
        store,
        [OrderIntent("000001.SZ", "BUY", 100, _bar(30).dt)],
        status_provider=lambda *args: "TRADING",
        limit_provider=lambda *args: {"known": True},
    )
    params = {
        "execution_mode": "minute_store",
        "source_ref": "fixture",
        "strategy": "minute_store",
        "start_at": "2026-10-16T01:29:00+00:00",
        "end_at": "2026-10-16T02:00:00+00:00",
        "as_of": "2026-10-16T02:00:00+00:00",
        "initial_cash": 2000,
    }
    rec = api._bt2_start(params)
    assert rec["ok"]
    jid = rec["job"]["id"]
    state = api._read_json(api._bt2_state_path(jid))
    assert state["execution_mode"] == "minute_store"
    result = api.await_job(jid)
    if not result.get("ok"):
        state = api._read_json(api._bt2_state_path(jid))
        raise AssertionError(f"minute worker failed: {state}")
    assert result["schema"] == "minute-exec-v1"
    assert (
        "metrics" in result
        and "orders" in result
        and "fills" in result
        and "audits" in result
    )
    artifact = Path(api._minute_output_root()) / f"minute_{jid}.json"
    assert (
        result["artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    )
    completed = api._read_json(api._bt2_state_path(jid))
    assert completed["artifact_sha256"] == result["artifact_sha256"]
    assert completed["artifact_bytes"] == artifact.stat().st_size


def _minute_artifact_fixture(api, tmp_path: Path) -> tuple[str, Path]:
    """Persist a completed minute job entirely under the isolated test root."""
    jid = "minute-fixture"
    root = tmp_path / "minute"
    root.mkdir(exist_ok=True)
    artifact = root / f"minute_{jid}.json"
    _valid_minute_artifact(jid, artifact)
    artifact_bytes = artifact.read_bytes()
    api._bt2_write_state(
        jid,
        status="done",
        params={"execution_mode": "minute_store"},
        result_file=str(artifact),
        artifact_bytes=len(artifact_bytes),
        artifact_sha256=hashlib.sha256(artifact_bytes).hexdigest(),
    )
    return jid, artifact


def _valid_minute_artifact(jid: str, artifact: Path) -> dict:
    """Write a minimal current engine-shaped payload."""
    payload = {
        "schema": "minute-exec-v1",
        "job_id": jid,
        "fills": [],
        "audits": [],
        "orders": [],
        "cash": 1000.0,
        "positions": {},
        "equity": 1000.0,
        "metrics": {},
        "source_metadata": {},
        "query_metadata": {},
    }
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _unavailable() -> dict:
    """Return the complete stable artifact failure DTO."""
    return {
        "ok": False,
        "error": "ARTIFACT_UNAVAILABLE",
        "error_code": "ARTIFACT_UNAVAILABLE",
        "source_error": "ARTIFACT_UNAVAILABLE",
    }


@pytest.mark.parametrize("missing", ["artifact_bytes", "artifact_sha256", "both"])
def test_minute_artifact_rejects_missing_write_time_evidence(
    api, tmp_path: Path, missing: str
) -> None:
    jid, artifact = _minute_artifact_fixture(api, tmp_path)
    _valid_minute_artifact(jid, artifact)
    if missing == "both":
        api._bt2_write_state(jid, artifact_bytes=None, artifact_sha256=None)
    else:
        api._bt2_write_state(jid, **{missing: None})
    assert api._bt2_result(jid) == _unavailable()


def test_minute_artifact_rejects_valid_shape_cash_tampering(
    api, tmp_path: Path
) -> None:
    jid, artifact = _minute_artifact_fixture(api, tmp_path)
    payload = _valid_minute_artifact(jid, artifact)
    artifact.write_text(json.dumps({**payload, "cash": 1123}), encoding="utf-8")
    assert api._bt2_result(jid) == _unavailable()


def test_minute_artifact_rejects_oversize_before_json_parse(
    api, tmp_path: Path
) -> None:
    jid, artifact = _minute_artifact_fixture(api, tmp_path)
    payload = _valid_minute_artifact(jid, artifact)
    payload["padding"] = "x" * api._MINUTE_ARTIFACT_MAX_BYTES
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    large_bytes = artifact.read_bytes()
    assert len(large_bytes) > api._MINUTE_ARTIFACT_MAX_BYTES
    api._bt2_write_state(
        jid,
        artifact_bytes=len(large_bytes),
        artifact_sha256=hashlib.sha256(large_bytes).hexdigest(),
    )
    assert api._bt2_result(jid) == _unavailable()


def test_minute_artifact_rejects_partial_write(api, tmp_path: Path) -> None:
    jid, artifact = _minute_artifact_fixture(api, tmp_path)
    artifact.write_bytes(artifact.read_bytes()[:23])
    assert api._bt2_result(jid) == _unavailable()


def test_minute_worker_rejects_invalid_persisted_result_before_done(
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker does not trust an engine success result without valid disk bytes."""
    import backtest_engine_v2 as engine

    bar = _bar(30)
    api.register_minute_source(
        "invalid-artifact",
        BarStore([bar]),
        [OrderIntent("000001.SZ", "BUY", 100, bar.dt)],
        status_provider=lambda *args: "TRADING",
        limit_provider=lambda *args: {"known": True},
    )

    def corrupt_run(params: dict, job_id: str, progress=None) -> tuple[dict, None]:
        artifact = Path(engine.MINUTE_OUT_DIR) / f"minute_{job_id}.json"
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_bytes(b'{"schema":"minute-exec-v1","job_id":"wrong"}')
        return {
            "artifact": str(artifact),
            "schema": "minute-exec-v1",
            "metrics": {},
        }, None

    monkeypatch.setattr(engine, "run_job", corrupt_run)
    started = api._bt2_start(
        {
            "execution_mode": "minute_store",
            "source_ref": "invalid-artifact",
            "d0": "20261016",
            "d1": "20261016",
            "initial_cash": 2000,
        }
    )
    assert started["ok"] is True
    jid = started["job"]["id"]
    result = api.await_job(jid)
    assert result["ok"] is False and result["error_code"] == "ARTIFACT_UNAVAILABLE"
    state = api._read_json(api._bt2_state_path(jid))
    assert state["status"] == "dead" and state["error_code"] == "ARTIFACT_UNAVAILABLE"


def test_minute_state_reader_sees_only_complete_versions(
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent reader cannot observe the temp file during serialization."""
    jid = "state-reader-atomic"
    api._bt2_write_state(
        jid,
        execution_mode="minute_store",
        status="running",
        params={"execution_mode": "minute_store"},
    )
    state_path = Path(api._bt2_state_path(jid))
    original = state_path.read_bytes()
    entered = threading.Event()
    release = threading.Event()
    original_dump = api.json.dump
    errors: list[BaseException] = []

    def blocked_dump(payload: dict, output: object, **kwargs: object) -> None:
        if payload.get("status") == "done":
            output.write('{"status":"done"')
            entered.set()
            if not release.wait(5):
                raise TimeoutError("concurrent reader did not release writer")
            output.seek(0)
            output.truncate()
        original_dump(payload, output, **kwargs)

    monkeypatch.setattr(api.json, "dump", blocked_dump)

    def write_done() -> None:
        try:
            api._bt2_write_state(jid, status="done")
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=write_done)
    worker.start()
    try:
        assert entered.wait(5)
        assert state_path.read_bytes() == original
        assert api._read_json(str(state_path))["status"] == "running"
    finally:
        release.set()
        worker.join(timeout=5)
    assert not worker.is_alive() and not errors
    assert api._read_json(str(state_path))["status"] == "done"


def test_minute_start_refuses_existing_done_job_id(
    api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ID collisions cannot replace either old done evidence or old artifact."""

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None) -> datetime:
            return cls(2026, 10, 18, 12, 34, 56, tzinfo=tz)

    bar = _bar(30)
    api.register_minute_source(
        "collision",
        BarStore([bar]),
        [OrderIntent("000001.SZ", "BUY", 100, bar.dt)],
        status_provider=lambda *args: "TRADING",
        limit_provider=lambda *args: {"known": True},
    )
    jid = "20261018-123456-a1b2"
    artifact = tmp_path / "minute" / f"minute_{jid}.json"
    artifact.parent.mkdir(exist_ok=True)
    _valid_minute_artifact(jid, artifact)
    artifact_bytes = artifact.read_bytes()
    api._bt2_write_state(
        jid,
        status="done",
        execution_mode="minute_store",
        params={"execution_mode": "minute_store"},
        result_file=str(artifact),
        artifact_bytes=len(artifact_bytes),
        artifact_sha256=hashlib.sha256(artifact_bytes).hexdigest(),
    )
    state_path = Path(api._bt2_state_path(jid))
    original_state = state_path.read_bytes()
    monkeypatch.setattr(api.datetime, "datetime", FixedDatetime)
    monkeypatch.setattr(api.os, "urandom", lambda size: b"\xa1\xb2")
    started = api._bt2_start(
        {
            "execution_mode": "minute_store",
            "source_ref": "collision",
            "d0": "20261016",
            "d1": "20261016",
            "initial_cash": 2000,
        }
    )
    assert started == {
        "ok": False,
        "error": "JOB_ID_COLLISION",
        "error_code": "JOB_ID_COLLISION",
    }
    assert state_path.read_bytes() == original_state
    assert artifact.read_bytes() == artifact_bytes
    api._bt2_worker(jid, {"execution_mode": "minute_store"})
    assert state_path.read_bytes() == original_state
    assert artifact.read_bytes() == artifact_bytes


def test_old_done_remains_readable_when_all_later_state_writes_fail(
    api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existing completed data survives total storage failure; no rewrite starts."""
    jid, artifact = _minute_artifact_fixture(api, tmp_path)
    original_artifact = artifact.read_bytes()
    state_path = Path(api._bt2_state_path(jid))
    original_state = state_path.read_bytes()
    monkeypatch.setattr(
        api.os,
        "replace",
        lambda *args: (_ for _ in ()).throw(OSError("read-only disk")),
    )
    api._bt2_worker(jid, {"execution_mode": "minute_store"})
    assert artifact.read_bytes() == original_artifact
    assert state_path.read_bytes() == original_state
    # A prior legitimate done result remains readable: an unwritable state
    # cannot revoke it. The collision gate prevents starting a new same-ID job.
    assert api._bt2_result(jid)["ok"] is True


def test_minute_start_refuses_orphan_artifact_id(
    api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even an orphan result cannot be overwritten by a reused generated ID."""

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None) -> datetime:
            return cls(2026, 10, 18, 12, 34, 56, tzinfo=tz)

    bar = _bar(30)
    api.register_minute_source(
        "orphan",
        BarStore([bar]),
        [OrderIntent("000001.SZ", "BUY", 100, bar.dt)],
        status_provider=lambda *args: "TRADING",
        limit_provider=lambda *args: {"known": True},
    )
    jid = "20261018-123456-a1b2"
    artifact = tmp_path / "minute" / f"minute_{jid}.json"
    artifact.parent.mkdir(exist_ok=True)
    artifact.write_bytes(b"orphan existing bytes")
    monkeypatch.setattr(api.datetime, "datetime", FixedDatetime)
    monkeypatch.setattr(api.os, "urandom", lambda size: b"\xa1\xb2")
    started = api._bt2_start(
        {
            "execution_mode": "minute_store",
            "source_ref": "orphan",
            "d0": "20261016",
            "d1": "20261016",
            "initial_cash": 2000,
        }
    )
    assert started["error_code"] == "JOB_ID_COLLISION"
    assert artifact.read_bytes() == b"orphan existing bytes"
    assert not Path(api._bt2_state_path(jid)).exists()


def test_minute_state_replace_failure_preserves_previous_state(
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed minute state commit is observable and cannot truncate old bytes."""
    jid = "state-atomic-failure"
    api._bt2_write_state(
        jid,
        execution_mode="minute_store",
        status="running",
        params={"execution_mode": "minute_store"},
    )
    state_path = Path(api._bt2_state_path(jid))
    original = state_path.read_bytes()
    original_replace = api.os.replace

    def reject_state_replace(source: str, destination: str) -> None:
        if str(destination) == str(state_path):
            raise OSError("state replace failed")
        original_replace(source, destination)

    monkeypatch.setattr(api.os, "replace", reject_state_replace)
    with pytest.raises(OSError, match="state replace failed"):
        api._bt2_write_state(
            jid, status="done", artifact_bytes=1, artifact_sha256="0" * 64
        )
    assert state_path.read_bytes() == original
    assert list(state_path.parent.glob(f".bt2_{jid}.*.tmp")) == []


def test_minute_worker_done_state_write_failure_is_not_success(
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failed done transition is converted into a dead state, not success."""
    bar = _bar(30)
    api.register_minute_source(
        "state-failure",
        BarStore([bar]),
        [OrderIntent("000001.SZ", "BUY", 100, bar.dt)],
        status_provider=lambda *args: "TRADING",
        limit_provider=lambda *args: {"known": True},
    )
    original_replace = api.os.replace
    failed = []

    def reject_done_once(source: str, destination: str) -> None:
        if (
            str(destination).endswith(".state.json")
            and not failed
            and json.loads(Path(source).read_text(encoding="utf-8")).get("status")
            == "done"
        ):
            failed.append(True)
            raise OSError("done state replace failed")
        original_replace(source, destination)

    monkeypatch.setattr(api.os, "replace", reject_done_once)
    started = api._bt2_start(
        {
            "execution_mode": "minute_store",
            "source_ref": "state-failure",
            "d0": "20261016",
            "d1": "20261016",
            "initial_cash": 2000,
        }
    )
    assert started["ok"] is True
    jid = started["job"]["id"]
    result = api.await_job(jid)
    assert failed and result["ok"] is False
    assert result["error_code"] == "ARTIFACT_UNAVAILABLE"
    state = api._read_json(api._bt2_state_path(jid))
    assert state["status"] == "dead" and state["error_code"] == "ARTIFACT_UNAVAILABLE"
    assert list(Path(api._BT2_STATE_DIR).glob(f".bt2_{jid}.*.tmp")) == []


def test_minute_artifact_validates_and_reports_actual_digest(
    api, tmp_path: Path
) -> None:
    jid, artifact = _minute_artifact_fixture(api, tmp_path)
    _valid_minute_artifact(jid, artifact)
    result = api._bt2_result(jid)
    assert result["ok"] is True
    assert result["schema"] == "minute-exec-v1"
    assert result["job_id"] == jid
    assert (
        result["artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    )


@pytest.mark.parametrize(
    "change",
    [
        {"schema": "unknown"},
        {"job_id": "another-job"},
        {"fills": None},
        {"audits": {}},
        {"orders": {}},
        {"cash": True},
        {"cash": float("nan")},
        {"cash": float("inf")},
        {"cash": float("-inf")},
        {"cash": 10**1000},
        {"equity": float("nan")},
        {"equity": float("inf")},
        {"equity": 10**1000},
        {"positions": []},
        {"metrics": []},
        {"equity": "1000"},
        {"source_metadata": None},
        {"query_metadata": None},
    ],
)
def test_minute_artifact_rejects_invalid_fields(
    api, tmp_path: Path, change: dict
) -> None:
    jid, artifact = _minute_artifact_fixture(api, tmp_path)
    payload = _valid_minute_artifact(jid, artifact)
    artifact.write_text(json.dumps({**payload, **change}), encoding="utf-8")
    assert api._bt2_result(jid) == _unavailable()


@pytest.mark.parametrize(
    "missing",
    [
        "schema",
        "job_id",
        "fills",
        "audits",
        "orders",
        "cash",
        "equity",
        "positions",
        "metrics",
        "source_metadata",
        "query_metadata",
    ],
)
def test_minute_artifact_rejects_missing_fields(
    api, tmp_path: Path, missing: str
) -> None:
    jid, artifact = _minute_artifact_fixture(api, tmp_path)
    payload = _valid_minute_artifact(jid, artifact)
    del payload[missing]
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    assert api._bt2_result(jid) == _unavailable()


@pytest.mark.parametrize("content", [b"{broken", b"[]", b"\xff"])
def test_minute_artifact_rejects_bad_json(api, tmp_path: Path, content: bytes) -> None:
    jid, artifact = _minute_artifact_fixture(api, tmp_path)
    artifact.write_bytes(content)
    assert api._bt2_result(jid) == _unavailable()


@pytest.mark.parametrize("params", [None, [], "not-a-dict"])
def test_minute_artifact_rejects_invalid_state_params(
    api, tmp_path: Path, params: object
) -> None:
    """Corrupt state must not bypass validation or produce a server exception."""
    jid, artifact = _minute_artifact_fixture(api, tmp_path)
    api._bt2_write_state(jid, params=params)
    artifact.write_bytes(b"{broken")
    assert api._bt2_result(jid) == _unavailable()


@pytest.mark.parametrize(
    "damaged_params",
    [{}, {"source_ref": "fixture"}, {"execution_mode": "daily_legacy"}],
)
@pytest.mark.parametrize("identity", ["new", "old_schema", "old_path"])
def test_minute_result_rejects_mode_downgrade(
    api, tmp_path: Path, damaged_params: dict, identity: str
) -> None:
    """Missing/conflicting params cannot make a minute result a daily result."""
    jid, artifact = _minute_artifact_fixture(api, tmp_path)
    _valid_minute_artifact(jid, artifact)
    if identity == "new":
        api._bt2_write_state(jid, execution_mode="minute_store")
    elif identity == "old_schema":
        api._bt2_write_state(jid, schema="minute-exec-v1")
    # old_path exercises recovery of a pre-marker/pre-schema minute state.
    api._bt2_write_state(jid, params=damaged_params)
    assert api._bt2_result(jid) == _unavailable()


@pytest.mark.parametrize("damaged_params", [{}, {"source_ref": "fixture"}])
def test_minute_result_rejects_valid_artifact_when_mode_missing(
    api, tmp_path: Path, damaged_params: dict
) -> None:
    """Even a valid minute payload cannot be served as a legacy daily payload."""
    jid, artifact = _minute_artifact_fixture(api, tmp_path)
    payload = _valid_minute_artifact(jid, artifact)
    api._bt2_write_state(jid, schema="minute-exec-v1", params=damaged_params)
    assert json.loads(artifact.read_text(encoding="utf-8")) == payload
    assert api._bt2_result(jid) == _unavailable()


def test_daily_legacy_result_without_mode_remains_compatible(
    api, tmp_path: Path
) -> None:
    """A valid pre-marker daily state still reads its legacy result unchanged."""
    jid = "legacy-fixture"
    artifact = tmp_path / "daily.json"
    artifact.write_text(json.dumps({"summary": {"cash": 100}}), encoding="utf-8")
    api._bt2_write_state(jid, status="done", params={}, result_file=str(artifact))
    result = api._bt2_result(jid)
    assert result["ok"] is True
    assert result["summary"] == {"cash": 100}


@pytest.mark.parametrize("tamper", ["escape", "wrong_name", "symlink"])
def test_minute_result_rejects_unsafe_artifact_path(
    api, tmp_path: Path, tamper: str
) -> None:
    """Artifact resolution must reject traversal, other names and symlinks."""
    jid, artifact = _minute_artifact_fixture(api, tmp_path)
    if tamper == "symlink":
        import errno

        outside = tmp_path / "outside.json"
        _valid_minute_artifact(jid, outside)
        assert artifact.parent.resolve().is_relative_to(tmp_path.resolve())
        assert artifact.exists()
        assert not artifact.is_dir() and not artifact.is_symlink()
        artifact.unlink()
        try:
            artifact.symlink_to(outside)
        except NotImplementedError as error:
            pytest.xfail(f"NOT EXECUTED: symlink unsupported by platform: {error}")
        except OSError as error:
            unsupported_errno = (errno.EPERM, errno.EACCES, errno.ENOSYS, errno.ENOTSUP)
            unsupported_winerror = (1, 5, 50, 1314)
            if (
                error.errno in unsupported_errno
                or getattr(error, "winerror", None) in unsupported_winerror
            ):
                pytest.xfail(
                    f"NOT EXECUTED: symlink permission or platform unsupported: {error}"
                )
            raise
        if not artifact.is_symlink():
            # Some constrained filesystems return successfully without creating a link.
            # Do not run the business assertion on an ordinary or nonexistent file.
            if artifact.exists():
                pytest.fail(
                    "symlink creation returned successfully but made a non-symlink file"
                )
            pytest.xfail(
                "NOT EXECUTED: symlink creation returned successfully without a link"
            )
        assert artifact.resolve() == outside.resolve(), (
            "symlink does not target the fixture"
        )
        candidate = artifact
    else:
        _valid_minute_artifact(jid, artifact)
        if tamper == "escape":
            candidate = tmp_path / "outside.json"
        else:
            candidate = artifact.with_name("minute_another-job.json")
        candidate.write_bytes(artifact.read_bytes())
    api._bt2_write_state(jid, result_file=str(candidate))
    assert api._bt2_result(jid) == _unavailable()


def test_minute_engine_replace_failure_keeps_previous_artifact_and_cleans_temp(
    api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed atomic commit must not expose partial bytes or leave a temp file."""
    import backtest_engine_v2 as engine

    jid = "atomic-failure"
    output = Path(engine.MINUTE_OUT_DIR)
    output.mkdir(exist_ok=True)
    existing = output / f"minute_{jid}.json"
    existing.write_bytes(b"previous complete artifact")
    bar = _bar(30)

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            schema="minute-exec-v1",
            fills=[],
            audits=[],
            orders=[],
            cash=1000.0,
            positions={},
            equity=1000.0,
            metrics={},
            source_metadata={},
            query_metadata={},
        )

    import execution_engine

    monkeypatch.setattr(execution_engine, "run_from_store", fake_run)
    monkeypatch.setattr(
        engine.os,
        "replace",
        lambda *args: (_ for _ in ()).throw(OSError("replace failed")),
    )
    result, error = engine.run_job(
        {
            "execution_mode": "minute_store",
            "bar_store": BarStore([bar]),
            "order_intents": [],
            "d0": "20261016",
            "d1": "20261016",
        },
        jid,
    )
    assert result is None and "replace failed" in error
    assert existing.read_bytes() == b"previous complete artifact"
    assert list(output.glob(f".minute_{jid}.*.tmp")) == []


def test_minute_start_rejects_unregistered_source(api) -> None:
    result = api._bt2_start(
        {
            "execution_mode": "minute_store",
            "source_ref": "unknown",
            "d0": "20261016",
            "d1": "20261017",
        }
    )
    assert result["ok"] is False
    assert "source_ref" in result["error"]


def test_jobs_project_source_descriptor_and_availability(api) -> None:
    api._bt2_write_state(
        "job-status",
        status="dead",
        params={"execution_mode": "minute_store", "source_ref": "missing"},
        error="SOURCE_UNAVAILABLE",
    )
    jobs = api._bt2_status()
    assert jobs["jobs"][0]["source_availability"] == "source_unavailable"
    assert jobs["jobs"][0]["source_error"] == "SOURCE_UNAVAILABLE"


def test_start_rejects_marked_unavailable_source(api) -> None:
    api.register_minute_source(
        "unavailable",
        BarStore([_bar(30)]),
        [OrderIntent("000001.SZ", "BUY", 100, _bar(30).dt)],
        status_provider=lambda *args: "TRADING",
        limit_provider=lambda *args: {"known": True},
    )
    api._JOB_SPEC_REGISTRY.mark_unavailable("unavailable", "SOURCE_UNAVAILABLE")
    result = api._bt2_start(
        {
            "execution_mode": "minute_store",
            "source_ref": "unavailable",
            "d0": "20261016",
            "d1": "20261017",
        }
    )
    assert result["ok"] is False
    assert result["error"] == "SOURCE_UNAVAILABLE"
    assert result["error_code"] == "SOURCE_UNAVAILABLE"
    assert result["source_ref"] == "unavailable"
    assert result["source_availability"] == "source_unavailable"


def test_register_rejects_non_sha256_calendar_version(api) -> None:
    """注册入口与 CalendarIdentity 契约统一：'v1' 这类版本号直接拒绝。"""
    store = BarStore([_bar(30)])
    with pytest.raises(ValueError, match="calendar_version"):
        api.register_minute_source(
            "badcal",
            store,
            [OrderIntent("000001.SZ", "BUY", 100, _bar(30).dt)],
            status_provider=lambda *args: "TRADING",
            limit_provider=lambda *args: {"known": True},
            calendar_ref="registry:calendar-cn-sse",
            calendar_version="v1",
        )
    assert api._JOB_SPEC_REGISTRY.get("badcal") is None


def _policy_intent(**changes: object) -> OrderIntent:
    """Construct a validated intent without mutating frozen runtime values."""
    bar = _bar(30)
    return OrderIntent("000001.SZ", "BUY", 100, bar.dt, **changes)


def _policy_source(api, ref: str, intents: list[OrderIntent], **policy: object) -> dict:
    """Register a runtime source exclusively in the isolated API instance."""
    version = policy.get("calendar_version")
    metadata = (
        {"calendar_ref": policy["calendar_ref"], "calendar_version": version}
        if policy.get("calendar_ref") is not None
        else None
    )
    bars = [] if metadata else [_bar(30)]
    return api.register_minute_source(
        ref,
        BarStore(bars, metadata=metadata),
        intents,
        status_provider=lambda *args: "TRADING",
        limit_provider=lambda *args: {"known": True},
        **policy,
    )


def _policy_request(ref: str) -> dict:
    return {
        "execution_mode": "minute_store",
        "source_ref": ref,
        "d0": "20261016",
        "d1": "20261016",
        "initial_cash": 2000,
    }


def test_policy_registration_is_atomic_for_mismatch_and_invalid_config(api) -> None:
    bad_cases = [
        ("gtc-day", [_policy_intent()], {"time_in_force": "GTC"}, "POLICY_MISMATCH"),
        ("day-gtc", [_policy_intent(time_in_force="GTC")], {}, "POLICY_MISMATCH"),
        (
            "mixed-attempts",
            [
                _policy_intent(time_in_force="GTC", max_attempts=2),
                _policy_intent(time_in_force="GTC", max_attempts=3),
            ],
            {"time_in_force": "GTC", "max_attempts": 2},
            "POLICY_MISMATCH",
        ),
        (
            "bad-attempts",
            [_policy_intent(time_in_force="GTC")],
            {"time_in_force": "GTC", "max_attempts": True},
            "INVALID_TIF_CONFIG",
        ),
        (
            "naive-expiry",
            [_policy_intent(time_in_force="GTC")],
            {"time_in_force": "GTC", "expire_at": "2026-10-17T09:00:00"},
            "INVALID_TIF_CONFIG",
        ),
        (
            "bad-expiry",
            [_policy_intent(time_in_force="GTC")],
            {"time_in_force": "GTC", "expire_at": "not-a-date"},
            "INVALID_TIF_CONFIG",
        ),
        (
            "not-intent",
            [_policy_intent(), "bad"],
            {},
            "order_intents must contain OrderIntent",
        ),
    ]
    for ref, intents, policy, code in bad_cases:
        with pytest.raises((ValueError, TypeError), match=code):
            _policy_source(api, ref, intents, **policy)
        assert ref not in api._MINUTE_SOURCE_REGISTRY
        assert ref not in api._MINUTE_SOURCE_FACTORIES
        assert api._JOB_SPEC_REGISTRY.get(ref) is None


def _calendar_fixture(version: str = "a" * 64):
    """Create an explicit one-day production calendar for registration tests."""
    from calendar_provider import (
        CalendarIdentity,
        CalendarSession,
        FrozenCalendarProvider,
    )

    session_date = date(2026, 10, 16)
    identity = CalendarIdentity(
        "registry:calendar-cn",
        version,
        "CN",
        "SSE",
        "Asia/Shanghai",
        session_date,
        session_date,
    )
    session = CalendarSession(
        session_date,
        datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc),
        datetime(2026, 10, 16, 7, 0, tzinfo=timezone.utc),
    )
    return FrozenCalendarProvider(identity, {session_date: True}, [session])


def test_calendar_registration_rejects_missing_provider_without_publishing(api) -> None:
    ref = "calendar-atomic"
    catalog = Path(api._JOB_SPEC_REGISTRY.catalog_path)
    before = catalog.read_bytes() if catalog.exists() else None
    with pytest.raises(ValueError, match="CALENDAR_UNAVAILABLE"):
        _policy_source(
            api,
            ref,
            [_policy_intent()],
            calendar_ref="registry:calendar-cn",
            calendar_version="a" * 64,
        )
    assert api._JOB_SPEC_REGISTRY.get(ref) is None
    assert ref not in api._MINUTE_SOURCE_REGISTRY
    assert ref not in api._MINUTE_SOURCE_FACTORIES
    assert ref not in api._MINUTE_CALENDAR_FACTORIES
    assert (catalog.read_bytes() if catalog.exists() else None) == before


def test_calendar_failed_replacement_preserves_old_source_and_factory(api) -> None:
    ref = "calendar-existing"
    original = _policy_source(api, ref, [_policy_intent()])
    spec = api._MINUTE_SOURCE_REGISTRY[ref]
    factory = api._MINUTE_SOURCE_FACTORIES[ref]
    old_calendar_factory = lambda descriptor: _calendar_fixture()
    api._MINUTE_CALENDAR_FACTORIES[ref] = old_calendar_factory
    catalog = Path(api._JOB_SPEC_REGISTRY.catalog_path)
    catalog_bytes = catalog.read_bytes()
    with pytest.raises(ValueError, match="CALENDAR_UNAVAILABLE"):
        _policy_source(
            api,
            ref,
            [_policy_intent()],
            calendar_ref="registry:calendar-cn",
            calendar_version="a" * 64,
        )
    assert api._JOB_SPEC_REGISTRY.get(ref).as_dict() == original
    assert api._MINUTE_SOURCE_REGISTRY[ref] is spec
    assert api._MINUTE_SOURCE_FACTORIES[ref] is factory
    assert api._MINUTE_CALENDAR_FACTORIES[ref] is old_calendar_factory
    assert catalog.read_bytes() == catalog_bytes


@pytest.mark.parametrize("replace_existing", [False, True])
def test_calendar_callback_mutates_batch_before_registration(
    api, replace_existing: bool
) -> None:
    """An identity-valid factory cannot publish a batch changed during its call."""
    ref = "calendar-callback-old" if replace_existing else "calendar-callback-new"
    if replace_existing:
        original = _policy_source(api, ref, [_policy_intent()])
    else:
        original = None
    old_spec = api._MINUTE_SOURCE_REGISTRY.get(ref)
    old_factory = api._MINUTE_SOURCE_FACTORIES.get(ref)
    catalog = Path(api._JOB_SPEC_REGISTRY.catalog_path)
    old_bytes = catalog.read_bytes() if catalog.exists() else None
    provider = _calendar_fixture()

    original_validate = api._validate_runtime_calendar

    def capture_spec(source_ref: str, descriptor: object, spec: dict):
        # Simulate mutation by a factory/store callback during validation.
        spec["order_intents"] = [_policy_intent(time_in_force="GTC")]
        return original_validate(source_ref, descriptor, spec)

    api._validate_runtime_calendar = capture_spec
    try:
        with pytest.raises(ValueError, match="POLICY_MISMATCH"):
            _policy_source(
                api,
                ref,
                [_policy_intent()],
                calendar_ref="registry:calendar-cn",
                calendar_version="a" * 64,
                calendar_provider=provider,
            )
    finally:
        api._validate_runtime_calendar = original_validate
    if original is None:
        assert api._JOB_SPEC_REGISTRY.get(ref) is None
    else:
        assert api._JOB_SPEC_REGISTRY.get(ref).as_dict() == original
    assert api._MINUTE_SOURCE_REGISTRY.get(ref) is old_spec
    assert api._MINUTE_SOURCE_FACTORIES.get(ref) is old_factory
    assert (catalog.read_bytes() if catalog.exists() else None) == old_bytes


def test_calendar_registration_validates_before_commit_and_recovers(api) -> None:
    ref = "calendar-valid"
    provider = _calendar_fixture()
    factory = lambda descriptor: provider
    descriptor = _policy_source(
        api,
        ref,
        [_policy_intent()],
        calendar_ref="registry:calendar-cn",
        calendar_version="a" * 64,
        calendar_factory=factory,
    )
    assert descriptor["availability"] == "available"
    assert api._MINUTE_SOURCE_REGISTRY[ref]["calendar_provider"] is provider
    assert api._MINUTE_CALENDAR_FACTORIES[ref] is factory
    api._MINUTE_SOURCE_REGISTRY.pop(ref)
    ok, error = api._rehydrate_minute_source(ref)
    assert ok and error is None
    assert api._MINUTE_SOURCE_REGISTRY[ref]["calendar_provider"] is provider


def test_calendar_registration_failure_does_not_call_registry_register(
    api, monkeypatch
) -> None:
    ref = "calendar-precommit"
    called = []
    monkeypatch.setattr(
        api._JOB_SPEC_REGISTRY, "register", lambda descriptor: called.append(True)
    )
    with pytest.raises(ValueError, match="CALENDAR_MISMATCH"):
        _policy_source(
            api,
            ref,
            [_policy_intent()],
            calendar_ref="registry:calendar-cn",
            calendar_version="b" * 64,
            calendar_provider=_calendar_fixture(),
        )
    assert not called
    assert ref not in api._MINUTE_SOURCE_REGISTRY


def test_calendar_registration_registry_failure_does_not_publish_runtime(
    api, monkeypatch
) -> None:
    ref = "calendar-register-failure"
    provider = _calendar_fixture()

    def failed_register(descriptor: object) -> None:
        raise OSError("catalog write failed")

    monkeypatch.setattr(api._JOB_SPEC_REGISTRY, "register", failed_register)
    with pytest.raises(OSError, match="catalog write failed"):
        _policy_source(
            api,
            ref,
            [_policy_intent()],
            calendar_ref="registry:calendar-cn",
            calendar_version="a" * 64,
            calendar_provider=provider,
            calendar_factory=lambda descriptor: provider,
        )
    assert ref not in api._MINUTE_SOURCE_REGISTRY
    assert ref not in api._MINUTE_SOURCE_FACTORIES
    assert ref not in api._MINUTE_CALENDAR_FACTORIES


@pytest.mark.parametrize("replace_existing", [False, True])
def test_catalog_save_failure_restores_descriptor_and_runtime(
    api, monkeypatch, replace_existing: bool
) -> None:
    """A failed registry commit cannot publish a new or replace an old source."""
    ref = "calendar-save-existing" if replace_existing else "calendar-save-new"
    if replace_existing:
        _policy_source(api, ref, [_policy_intent()])
    previous_descriptor = api._JOB_SPEC_REGISTRY.get(ref)
    previous_spec = api._MINUTE_SOURCE_REGISTRY.get(ref)
    previous_factory = api._MINUTE_SOURCE_FACTORIES.get(ref)
    previous_calendar_factory = api._MINUTE_CALENDAR_FACTORIES.get(ref)
    previous_status = api._JOB_SPEC_REGISTRY.catalog_status
    previous_error = api._JOB_SPEC_REGISTRY.catalog_error
    catalog = Path(api._JOB_SPEC_REGISTRY.catalog_path)
    before = catalog.read_bytes() if catalog.exists() else None

    def failed_save() -> None:
        raise OSError("catalog write failed")

    monkeypatch.setattr(api._JOB_SPEC_REGISTRY, "_save", failed_save)
    provider = _calendar_fixture()
    with pytest.raises(OSError, match="catalog write failed"):
        _policy_source(
            api,
            ref,
            [_policy_intent()],
            calendar_ref="registry:calendar-cn",
            calendar_version="a" * 64,
            calendar_provider=provider,
            calendar_factory=lambda descriptor: provider,
        )
    assert api._JOB_SPEC_REGISTRY.get(ref) == previous_descriptor
    assert api._JOB_SPEC_REGISTRY.catalog_status == previous_status
    assert api._JOB_SPEC_REGISTRY.catalog_error == previous_error
    assert api._MINUTE_SOURCE_REGISTRY.get(ref) is previous_spec
    assert api._MINUTE_SOURCE_FACTORIES.get(ref) is previous_factory
    assert api._MINUTE_CALENDAR_FACTORIES.get(ref) is previous_calendar_factory
    assert (catalog.read_bytes() if catalog.exists() else None) == before


def test_policy_utc_absolute_expiry_and_legacy_compatibility(api) -> None:
    _policy_source(api, "day-legacy", [_policy_intent()])
    assert api._JOB_SPEC_REGISTRY.get("day-legacy").availability == "available"
    _policy_source(
        api, "gtc-none", [_policy_intent(time_in_force="GTC")], time_in_force="GTC"
    )
    assert api._JOB_SPEC_REGISTRY.get("gtc-none").availability == "available"
    expires = datetime(2026, 10, 17, 1, 0, tzinfo=timezone.utc)
    _policy_source(
        api,
        "gtc-offset",
        [_policy_intent(time_in_force="GTC", expire_at=expires)],
        time_in_force="GTC",
        expire_at="2026-10-17T09:00:00+08:00",
    )
    assert api._JOB_SPEC_REGISTRY.get("gtc-offset").availability == "available"
    descriptor = api._JOB_SPEC_REGISTRY.get("gtc-offset")
    assert (
        api._validate_minute_order_policy(
            replace(descriptor, expire_at="2026-10-17T01:00:00Z"),
            api._MINUTE_SOURCE_REGISTRY["gtc-offset"],
        )
        is None
    )


@pytest.mark.parametrize("private_model", [False, True])
def test_partial_descriptor_fails_closed_even_with_private_model(
    api, private_model: bool
) -> None:
    ref = f"partial-{private_model}"
    if private_model:
        api._MINUTE_FILL_MODELS[ref] = object()
    with pytest.raises(ValueError, match="PARTIAL_FILL_UNSUPPORTED"):
        _policy_source(
            api, ref, [_policy_intent()], partial_fill_params={"max_participation": 0.1}
        )
    assert api._JOB_SPEC_REGISTRY.get(ref) is None
    assert ref not in api._MINUTE_SOURCE_REGISTRY


def test_recovery_rejects_partial_even_with_runtime_model(api) -> None:
    ref = "recover-partial"
    _policy_source(api, ref, [_policy_intent()])
    api._MINUTE_SOURCE_REGISTRY.pop(ref)
    api._JOB_SPEC_REGISTRY._descriptors[ref]["partial_fill_params"] = {
        "max_participation": 0.1
    }
    api._MINUTE_SOURCE_FACTORIES[ref] = lambda descriptor: {
        "bar_store": BarStore([_bar(30)]),
        "order_intents": [_policy_intent()],
        "fill_model": object(),
    }
    ok, code = api._rehydrate_minute_source(ref)
    assert not ok and code == "PARTIAL_FILL_UNSUPPORTED"
    assert ref not in api._MINUTE_SOURCE_REGISTRY
    assert api._JOB_SPEC_REGISTRY.get(ref).availability == "source_unavailable"
    assert api._JOB_SPEC_REGISTRY.get(ref).error == "PARTIAL_FILL_UNSUPPORTED"


def test_recovery_rejects_policy_drift_and_calendar_reenable(api) -> None:
    ref = "recover-policy"
    _policy_source(api, ref, [_policy_intent()])
    api._MINUTE_SOURCE_REGISTRY.pop(ref)
    api._MINUTE_SOURCE_FACTORIES[ref] = lambda descriptor: {
        "bar_store": BarStore([_bar(30)]),
        "order_intents": [_policy_intent(time_in_force="GTC")],
    }
    ok, code = api._rehydrate_minute_source(ref)
    assert not ok and code == "POLICY_MISMATCH"
    assert ref not in api._MINUTE_SOURCE_REGISTRY
    assert api._JOB_SPEC_REGISTRY.get(ref).availability == "source_unavailable"
    assert api._JOB_SPEC_REGISTRY.get(ref).error == "POLICY_MISMATCH"
    # The separate calendar registration path must not rehabilitate an invalid batch.
    api._MINUTE_SOURCE_REGISTRY[ref] = {
        "order_intents": [_policy_intent(time_in_force="GTC")]
    }
    api._MINUTE_CALENDAR_FACTORIES[ref] = lambda descriptor: object()
    api._JOB_SPEC_REGISTRY._descriptors[ref]["calendar_ref"] = "registry:calendar-cn"
    api._JOB_SPEC_REGISTRY._descriptors[ref]["calendar_version"] = "a" * 64
    ok, code = api.register_minute_calendar_factory(ref, lambda descriptor: object())
    assert not ok and code == "POLICY_MISMATCH"
    assert ref not in api._MINUTE_SOURCE_REGISTRY
    assert api._JOB_SPEC_REGISTRY.get(ref).availability == "source_unavailable"


@pytest.mark.parametrize(
    "code",
    [
        "POLICY_MISMATCH",
        "INVALID_TIF_CONFIG",
        "PARTIAL_FILL_UNSUPPORTED",
    ],
)
def test_policy_error_remains_specific_after_isolated_catalog_reload(
    api, code: str
) -> None:
    """Only approved stable policy codes survive descriptor persistence/reload."""
    import job_spec_registry

    ref = "persisted-policy-error"
    _policy_source(api, ref, [_policy_intent()])
    catalog = Path(api._JOB_SPEC_REGISTRY.catalog_path)
    api._JOB_SPEC_REGISTRY.mark_unavailable(ref, code)
    persisted = json.loads(catalog.read_text(encoding="utf-8"))
    assert persisted[ref]["error"] == code
    restored = job_spec_registry.JobSpecRegistry(str(catalog))
    assert restored.catalog_status == "ok"
    assert restored.get(ref).availability == "source_unavailable"
    assert restored.get(ref).error == code


def test_unrecognized_error_text_is_never_persisted(api) -> None:
    """Arbitrary exception text must not escape via the on-disk catalog."""
    import job_spec_registry

    ref = "persisted-unknown-error"
    _policy_source(api, ref, [_policy_intent()])
    catalog = Path(api._JOB_SPEC_REGISTRY.catalog_path)
    api._JOB_SPEC_REGISTRY.mark_unavailable(ref, "Exception: credential=secret")
    persisted = json.loads(catalog.read_text(encoding="utf-8"))
    assert persisted[ref]["error"] == "SOURCE_UNAVAILABLE"
    assert "credential=secret" not in catalog.read_text(encoding="utf-8")
    restored = job_spec_registry.JobSpecRegistry(str(catalog))
    assert restored.catalog_status == "ok"
    assert restored.get(ref).error == "SOURCE_UNAVAILABLE"


def test_invalid_policy_cannot_replace_existing_available_source(api) -> None:
    original = _policy_source(api, "existing-policy", [_policy_intent()])
    previous_spec = api._MINUTE_SOURCE_REGISTRY["existing-policy"]
    previous_factory = api._MINUTE_SOURCE_FACTORIES["existing-policy"]
    with pytest.raises(ValueError, match="POLICY_MISMATCH"):
        _policy_source(api, "existing-policy", [_policy_intent(time_in_force="GTC")])
    assert api._JOB_SPEC_REGISTRY.get("existing-policy").as_dict() == original
    assert api._MINUTE_SOURCE_REGISTRY["existing-policy"] is previous_spec
    assert api._MINUTE_SOURCE_FACTORIES["existing-policy"] is previous_factory


@pytest.mark.parametrize("bad_value", [0, -1, True, 1.5, "3"])
def test_policy_invalid_attempt_values(api, bad_value: object) -> None:
    descriptor = replace(
        _policy_descriptor(api), time_in_force="GTC", max_attempts=bad_value
    )
    with pytest.raises(ValueError, match="INVALID_TIF_CONFIG"):
        api._validate_minute_order_policy(
            descriptor, {"order_intents": [_policy_intent(time_in_force="GTC")]}
        )


def _policy_descriptor(api):
    _policy_source(api, "descriptor-fixture", [_policy_intent()])
    return api._JOB_SPEC_REGISTRY.get("descriptor-fixture")


def test_start_rejects_runtime_drift_before_queue(api) -> None:
    _policy_source(api, "start-drift", [_policy_intent()])
    api._MINUTE_SOURCE_REGISTRY["start-drift"]["order_intents"] = [
        _policy_intent(time_in_force="GTC")
    ]
    started = api._bt2_start(_policy_request("start-drift"))
    assert started["error_code"] == "POLICY_MISMATCH"
    assert list(Path(api._BT2_STATE_DIR).glob("*.state.json")) == []


def test_start_rechecks_policy_after_query_callback_before_queue(
    api, monkeypatch
) -> None:
    import backtest_engine_v2 as engine

    _policy_source(api, "late-start-drift", [_policy_intent()])
    original_query = engine._minute_query_from_params

    def change_batch(*args: object, **kwargs: object):
        api._MINUTE_SOURCE_REGISTRY["late-start-drift"]["order_intents"] = [
            _policy_intent(time_in_force="GTC")
        ]
        return original_query(*args, **kwargs)

    monkeypatch.setattr(engine, "_minute_query_from_params", change_batch)
    started = api._bt2_start(_policy_request("late-start-drift"))
    assert started["error_code"] == "POLICY_MISMATCH"
    assert list(Path(api._BT2_STATE_DIR).glob("*.state.json")) == []


def test_worker_revalidates_runtime_drift_without_execution(api, monkeypatch) -> None:
    import backtest_engine_v2 as engine

    _policy_source(api, "worker-drift", [_policy_intent()])
    called = []
    monkeypatch.setattr(engine, "run_job", lambda *args, **kwargs: called.append(True))
    jid = "policy-worker-drift"
    params = _policy_request("worker-drift")
    api._bt2_write_state(
        jid, status="queued", execution_mode="minute_store", params=params
    )
    api._MINUTE_SOURCE_REGISTRY["worker-drift"]["order_intents"] = [
        _policy_intent(time_in_force="GTC")
    ]
    api._bt2_worker(jid, params)
    state = api._read_json(api._bt2_state_path(jid))
    assert state["status"] == "dead" and state["error_code"] == "POLICY_MISMATCH"
    assert state["fills"] == [] and state["cash"] == 2000.0
    assert state["positions"] == {} and state["inventory_lots"] == []
    assert not called


def test_market_rule_provider_is_enforced_in_minute_worker(api) -> None:
    """MarketRuleProvider's halted decision must prevent all fills."""
    store = BarStore([_bar(30), _bar(31)])
    rule = MarketRuleProvider(
        status_provider=lambda *args: {"listed": True, "halted": True},
        limit_provider=lambda *args: "TRADING",
        market="CN",
    )
    api.register_minute_source(
        "rulesrc",
        store,
        [OrderIntent("000001.SZ", "BUY", 100, _bar(30).dt)],
        status_provider=lambda *args: "TRADING",
        limit_provider=lambda *args: {"known": True},
        market_rule_provider=rule,
    )
    params = {
        "execution_mode": "minute_store",
        "source_ref": "rulesrc",
        "strategy": "minute_store",
        "start_at": "2026-10-16T01:29:00+00:00",
        "end_at": "2026-10-16T02:00:00+00:00",
        "as_of": "2026-10-16T02:00:00+00:00",
        "initial_cash": 2000,
    }
    rec = api._bt2_start(params)
    assert rec["ok"]
    jid = rec["job"]["id"]
    state = api._read_json(api._bt2_state_path(jid))
    assert state["execution_mode"] == "minute_store"
    result = api.await_job(jid)
    if not result.get("ok"):
        state = api._read_json(api._bt2_state_path(jid))
        raise AssertionError(f"minute worker failed: {state}")
    assert result["fills"] == []
    halted = [a for a in result["audits"] if a.get("reason") == "HALTED"]
    assert halted, "rule provider decision must surface as HALTED audit"
