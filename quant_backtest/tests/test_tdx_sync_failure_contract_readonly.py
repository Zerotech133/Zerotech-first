"""TDX 同步失败退出码与核心锁的离线契约测试。"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest


_SOURCE = Path(__file__).resolve().parents[1] / "tdx_sync.py"
_SPEC = importlib.util.spec_from_file_location("tdx_sync_failure_contract", _SOURCE)
tdx_sync = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tdx_sync)


def test_run_sync_table_failure_continues_and_returns_one(capsys):
    remote = {"first": {"rows": 1}, "second": {"rows": 2}}
    args = SimpleNamespace(tables="first,second", include_rtmin=False)
    calls = []

    def sync_table(table, meta, manifest):
        calls.append(table)
        if table == "first":
            raise RuntimeError("offline failure")

    with patch.object(tdx_sync, "_load_manifest", return_value={}), \
         patch.object(tdx_sync, "sync_parquet_table", side_effect=sync_table), \
         patch.object(tdx_sync, "_get", side_effect=AssertionError("network forbidden")), \
         patch.object(tdx_sync, "_save_manifest", side_effect=AssertionError("manifest write forbidden")):
        rc = tdx_sync._run_sync(args, remote)

    assert rc == 1
    assert calls == ["first", "second"]
    assert "[exit] 1 table(s) FAILED: first" in capsys.readouterr().out


def test_run_sync_all_success_returns_zero():
    remote = {"first": {"rows": 1}, "second": {"rows": 2}}
    args = SimpleNamespace(tables="first,second", include_rtmin=False)
    manifest = {}
    with patch.object(tdx_sync, "_load_manifest", return_value=manifest), \
         patch.object(tdx_sync, "sync_parquet_table") as sync_table, \
         patch.object(tdx_sync, "_get", side_effect=AssertionError("network forbidden")), \
         patch.object(tdx_sync, "_save_manifest", side_effect=AssertionError("manifest write forbidden")):
        rc = tdx_sync._run_sync(args, remote)

    assert rc == 0
    assert sync_table.call_args_list == [
        call("first", remote["first"], manifest),
        call("second", remote["second"], manifest),
    ]


def test_main_existing_core_lock_skips_sync(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tdx_sync.py", "--all"])
    with patch.object(tdx_sync.os, "makedirs") as makedirs, \
         patch.object(tdx_sync, "_remote_tables_with_retry", return_value={}) as probe, \
         patch.object(tdx_sync, "_core_lock_acquire", return_value=False) as acquire, \
         patch.object(tdx_sync, "_run_sync") as run_sync, \
         patch.object(tdx_sync, "_core_lock_release") as release:
        assert tdx_sync.main() is None

    makedirs.assert_called_once_with(tdx_sync.OUT, exist_ok=True)
    probe.assert_called_once_with()
    acquire.assert_called_once_with()
    run_sync.assert_not_called()
    release.assert_not_called()


def test_main_failure_exits_one_and_releases_core_lock(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["tdx_sync.py", "--all"])
    remote = {"first": {"rows": 1}}
    with patch.object(tdx_sync.os, "makedirs") as makedirs, \
         patch.object(tdx_sync, "_remote_tables_with_retry", return_value=remote) as probe, \
         patch.object(tdx_sync, "_core_lock_acquire", return_value=True) as acquire, \
         patch.object(tdx_sync, "_run_sync", return_value=1) as run_sync, \
         patch.object(tdx_sync, "_core_lock_release") as release:
        with pytest.raises(SystemExit) as exc:
            tdx_sync.main()

    assert exc.value.code == 1
    makedirs.assert_called_once_with(tdx_sync.OUT, exist_ok=True)
    probe.assert_called_once_with()
    acquire.assert_called_once_with()
    run_sync.assert_called_once()
    assert run_sync.call_args.args[1] is remote
    release.assert_called_once_with()
