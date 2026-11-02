# -*- coding: utf-8 -*-
"""S1-04 F1/F2/F4/F6 单测：jev_common 降级通道与诊断（**全 mock，不连生产库**）。

覆盖：
  F1  docker 降级通道：capture_output/encoding/timeout 显式；OSError → DBFallbackError(errno)
      ；TimeoutExpired → stage=timeout；psql 非零 rc → stage=psql；成功解析 \x1f 行
  F2  直连失败日志带 DSN provenance（host/port/dbname/user/pw_source），口令不出现
  F4  dsn_provenance() 脱敏字段完整（无 password 值、含来源与长度）
  F6  probe_connectivity() 三段（driver/direct/docker_fallback）与 overall 三态
  兼容  db_rows/db_run/db_execute 签名与返回不变

运行（仓库根目录）：
  C:\\项目\\py312\\python.exe -X utf8 -m pytest jev-eval/tests/test_jev_common_fallback.py -q

边界：所有 psycopg2 / subprocess / shutil 调用均被 monkeypatch；不触碰 8091/portal/PG。
"""

from __future__ import annotations

import inspect
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

JEV_DIR = Path(__file__).resolve().parents[1]
if str(JEV_DIR) not in sys.path:
    sys.path.insert(0, str(JEV_DIR))

import jev_common as jc  # noqa: E402


# ── fakes / helpers ──────────────────────────────────────────
class _FakeCursor:
    def __init__(self, rows=None, exc=None):
        self._rows = [(1,)] if rows is None else rows
        self._exc = exc

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, sql, params=None):
        if self._exc is not None:
            raise self._exc

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows=None, exc=None):
        self._rows = rows
        self._exc = exc
        self.autocommit = False

    @property
    def closed(self):
        return 0

    def cursor(self):
        return _FakeCursor(self._rows, self._exc)


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeOperationalError(Exception):
    """模拟 psycopg2.OperationalError（带 errno 属性）。"""

    errno = 1045


def _raise_operational(*_a, **_k):
    raise _FakeOperationalError("password authentication failed for user zhice")


@pytest.fixture(autouse=True)
def _reset_conn_state():
    jc._conn.update({"c": None, "mode": ""})
    yield
    jc._conn.update({"c": None, "mode": ""})


@pytest.fixture()
def captured_logs(monkeypatch):
    records: list[dict] = []

    def _fake_log(msg, level="info", **fields):
        records.append({"msg": msg, "level": level, **fields})

    monkeypatch.setattr(jc, "log", _fake_log)
    return records


@pytest.fixture()
def docker_runs(monkeypatch):
    """记录 subprocess.run 的 kwargs，并按 handler 返回 fake 结果。"""
    calls: list[dict] = []
    state = {"handler": lambda *a, **k: _FakeProc(0, "", "")}

    def _fake_run(args, **kwargs):
        calls.append({"args": args, **kwargs})
        return state["handler"](args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    return calls, state


# ── F1：docker 降级通道异常包装 ─────────────────────────────
def test_f1_oserror_wrapped_with_errno(monkeypatch, captured_logs, docker_runs):
    """裸 OSError（WinError 87→EINVAL 形态）必须变成带 errno 的 DBFallbackError。"""
    _calls, state = docker_runs
    monkeypatch.setattr(jc, "_get_conn", _raise_operational)

    def _boom(*_a, **_k):
        raise OSError(22, "Invalid argument")

    state["handler"] = _boom

    with pytest.raises(jc.DBFallbackError) as ei:
        jc.db_rows("SELECT 1")

    err = ei.value
    assert err.errno == 22
    assert err.stage == "spawn"
    assert "docker" in str(err)
    assert "docker exec" in err.cmd  # 脱敏命令描述（含 user/db，不含口令）
    assert jc.DB_DSN["password"] not in str(err)


def test_f1_timeout_wrapped(monkeypatch, captured_logs, docker_runs):
    """docker 降级超时 → DBFallbackError(stage=timeout)。"""
    _calls, state = docker_runs
    monkeypatch.setattr(jc, "_get_conn", _raise_operational)

    def _timeout(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=20)

    state["handler"] = _timeout

    with pytest.raises(jc.DBFallbackError) as ei:
        jc.db_rows("SELECT 1")

    assert ei.value.stage == "timeout"
    assert ei.value.errno is None
    assert "超时" in str(ei.value)


def test_f1_psql_nonzero_rc(monkeypatch, captured_logs, docker_runs):
    """psql 非零退出 → DBFallbackError(stage=psql)，stderr 截断进消息。"""
    _calls, state = docker_runs
    monkeypatch.setattr(jc, "_get_conn", _raise_operational)
    state["handler"] = lambda *a, **k: _FakeProc(
        1, "", "psql: error: connection to server failed"
    )

    with pytest.raises(jc.DBFallbackError) as ei:
        jc.db_rows("SELECT 1")

    assert ei.value.stage == "psql"
    assert "psql 失败" in str(ei.value)
    assert "connection to server failed" in str(ei.value)


def test_f1_success_explicit_kwargs_and_row_parse(monkeypatch, captured_logs, docker_runs):
    """成功路径：显式 capture_output/encoding/timeout，且按 \\x1f 解析行。"""
    calls, state = docker_runs
    monkeypatch.setattr(jc, "_get_conn", _raise_operational)
    rows = [("alert-1", "JEV判定漂移·scope"), ("alert-2", "JEV判定漂移·bias")]
    state["handler"] = lambda *a, **k: _FakeProc(
        0, "\n".join("\x1f".join(r) for r in rows), ""
    )

    assert jc.db_rows("SELECT alert_id, title FROM v2_alert") == rows
    assert calls, "subprocess.run 未被调用"
    kw = calls[-1]
    assert kw["capture_output"] is True  # F1：显式捕获，不继承父进程句柄
    assert kw["encoding"] == "utf-8"
    assert kw["errors"] == "replace"
    assert kw["timeout"] == 20


def test_db_rows_direct_success_skips_fallback(monkeypatch, captured_logs):
    """直连成功时不走降级通道（行为兼容）。"""
    monkeypatch.setattr(jc, "_get_conn", lambda: _FakeConn(rows=[(42,)]))

    def _forbidden(*_a, **_k):
        raise AssertionError("直连成功却走了 docker 降级")

    monkeypatch.setattr(jc, "_docker_psql_rows", _forbidden)
    assert jc.db_rows("SELECT 42") == [(42,)]
    assert captured_logs == []


# ── F2：直连失败日志带脱敏 provenance ───────────────────────
def test_f2_direct_failure_log_has_masked_provenance(
    monkeypatch, captured_logs, docker_runs
):
    _calls, state = docker_runs
    monkeypatch.setattr(jc, "_get_conn", _raise_operational)
    monkeypatch.setattr(jc, "_docker_psql_rows", lambda sql, params: [])

    assert jc.db_rows("SELECT 1") == []
    assert captured_logs, "直连失败未记日志"
    rec = captured_logs[-1]
    assert rec["op"] == "db_rows"
    assert rec["exc_type"] == "_FakeOperationalError"
    assert rec["host"] and rec["port"] and rec["dbname"] and rec["user"]
    assert rec["pw_source"] in ("env", "file", "default")
    # 口令既不是字段也不是值
    assert "password" not in rec
    dumped = json.dumps(rec, ensure_ascii=False)
    assert jc.DB_DSN["password"] not in dumped


def test_f2_db_run_failure_log_op_name(monkeypatch, captured_logs):
    monkeypatch.setattr(jc, "_get_conn", _raise_operational)
    monkeypatch.setattr(jc, "_docker_psql_rows", lambda sql, params: [])

    assert jc.db_run("UPDATE t SET x = 1") is None
    assert captured_logs[-1]["op"] == "db_run"
    assert captured_logs[-1]["pw_source"] in ("env", "file", "default")


# ── F4：dsn_provenance 脱敏 ─────────────────────────────────
def test_f4_dsn_provenance_masked():
    prov = jc.dsn_provenance()
    for key in (
        "host",
        "port",
        "user",
        "dbname",
        "container",
        "password_source",
        "password_set",
        "password_len",
        "driver",
        "env_overrides",
    ):
        assert key in prov, "缺少 provenance 字段: %s" % key
    assert "password" not in prov
    assert prov["password_set"] is True
    assert prov["password_source"] in ("env", "file", "default")
    assert isinstance(prov["password_len"], int)
    assert jc.DB_DSN["password"] not in json.dumps(prov, ensure_ascii=False)
    assert prov["env_overrides"] == sorted(prov["env_overrides"])


# ── F6：probe_connectivity 三段 ────────────────────────────
def test_f6_probe_three_segments_ok(monkeypatch):
    monkeypatch.setattr(jc, "_get_conn", lambda: _FakeConn(rows=[(1,)]))
    monkeypatch.setattr(shutil, "which", lambda name: "docker" if name == "docker" else None)
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _FakeProc(0, "27.3.1\n", "")
    )

    res = jc.probe_connectivity()
    assert res["overall"] == "ok"
    assert set(res["segments"]) == {"driver", "direct", "docker_fallback"}
    assert res["segments"]["direct"]["ok"] is True
    assert res["segments"]["docker_fallback"]["available"] is True
    assert "password" not in res["provenance"]
    assert "generated_at" in res


def test_f6_probe_degraded(monkeypatch):
    monkeypatch.setattr(jc, "_get_conn", _raise_operational)
    monkeypatch.setattr(shutil, "which", lambda name: "docker")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeProc(0, "27\n", ""))

    res = jc.probe_connectivity()
    assert res["overall"] == "degraded"
    assert res["segments"]["direct"]["ok"] is False
    assert res["segments"]["direct"]["errno"] == 1045
    assert res["segments"]["docker_fallback"]["available"] is True


def test_f6_probe_down_when_docker_missing(monkeypatch):
    monkeypatch.setattr(jc, "_get_conn", _raise_operational)
    monkeypatch.setattr(shutil, "which", lambda name: None)

    res = jc.probe_connectivity()
    assert res["overall"] == "down"
    assert res["segments"]["docker_fallback"]["available"] is False
    assert "PATH" in res["segments"]["docker_fallback"]["error"]


def test_f6_probe_docker_cli_oserror_visible(monkeypatch):
    monkeypatch.setattr(jc, "_get_conn", lambda: _FakeConn(rows=[(1,)]))
    monkeypatch.setattr(shutil, "which", lambda name: "docker")

    def _boom(*_a, **_k):
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr(subprocess, "run", _boom)

    res = jc.probe_connectivity()
    assert res["overall"] == "ok"  # 直连 OK 即 ok；降级段暴露错误供诊断
    assert "errno=22" in res["segments"]["docker_fallback"]["error"]


# ── 兼容：签名与返回 ───────────────────────────────────────
def test_signatures_compatible():
    for name in ("db_rows", "db_run", "db_execute"):
        params = list(inspect.signature(getattr(jc, name)).parameters)
        assert params == ["sql", "params"], "%s 签名被破坏: %s" % (name, params)
        assert inspect.signature(getattr(jc, name)).parameters["params"].default is None
