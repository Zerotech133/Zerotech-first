# -*- coding: utf-8 -*-
"""JEV-DSN-20261105：DB 通道治理单测（**全 mock，不连真库**）。

覆盖：
  1 默认关降级（JEV_DB_DOCKER_FALLBACK 未设/0）→ DBChannelDeniedError，绝不走 docker
  2 显式开降级（=1）→ 保留旧行为，正常返回 docker psql 结果
  3 端口 5432 fail-fast → DBChannelConfigError（提示 infinity/5446/ZHICE_PG_PORT）
  4 降级 WARN 限流 → 同类失败同窗口只 1 条，压掉的条数进 suppressed
  5 db_channel_report() 字段（mode/次数/最近归因/是否放行/provenance 脱敏）
  6 所有异常与日志不含口令
  另：db_rows/db_execute/db_run 三入口行为一致；probe_connectivity 复用通道字段。

运行（仓库根目录）：
  "C:/Program Files/Python311/python.exe" -m pytest jev-eval -q

边界：psycopg2 / subprocess / 时钟全部 monkeypatch；不触碰生产库、不启停容器。
注意：同目录 conftest.py 的 autouse 夹具默认把 JEV_DB_DOCKER_FALLBACK 设为 1
（供 test_jev_common_fallback.py 验降级机制），本文件凡「默认关闭」用例都显式
`monkeypatch.delenv`，测的是真默认值。
"""

from __future__ import annotations

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


# ── fakes ───────────────────────────────────────────────────
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
    """模拟 psycopg2.OperationalError（口令认证失败形态）。"""

    errno = 1045


def _raise_operational(*_a, **_k):
    raise _FakeOperationalError("password authentication failed for user zhice")


def _forbidden(*_a, **_k):
    raise AssertionError("降级未开启，却走了 docker 降级通道")


@pytest.fixture()
def logs(monkeypatch):
    records: list[dict] = []

    def _fake_log(msg, level="info", **fields):
        records.append({"msg": msg, "level": level, **fields})

    monkeypatch.setattr(jc, "log", _fake_log)
    return records


@pytest.fixture()
def docker(monkeypatch):
    """docker 通路桩：默认返回空结果集，calls 记录调用次数。"""
    calls: list[dict] = []
    state = {"handler": lambda *a, **k: _FakeProc(0, "", "")}

    def _fake_run(args, **kwargs):
        calls.append({"args": args, **kwargs})
        return state["handler"](args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    return calls, state


# ── 1 默认关降级 ────────────────────────────────────────────
def test_default_off_raises_denied(monkeypatch, logs, docker):
    """未设 JEV_DB_DOCKER_FALLBACK → 直连失败必须抛诊断异常，不走 docker。"""
    monkeypatch.delenv("JEV_DB_DOCKER_FALLBACK", raising=False)
    calls, _state = docker
    monkeypatch.setattr(jc, "_get_conn", _raise_operational)

    with pytest.raises(jc.DBChannelDeniedError) as ei:
        jc.db_rows("SELECT 1")

    msg = str(ei.value)
    assert calls == [], "降级默认关闭时不得调用 docker"
    for token in (
        "127.0.0.1",  # host
        "5446",  # port
        "zhice_os_v2",  # dbname
        "pw_source=",  # 口令来源
        "JEV_DB_DOCKER_FALLBACK=1",  # 下一步动作
        "排障三步",
    ):
        assert token in msg, "诊断信息缺少 %r: %s" % (token, msg)
    assert jc._STATE["denied_total"] == 1
    assert jc._STATE["degraded_total"] == 0
    assert jc._current_channel() == "denied"


def test_zero_and_false_are_off(monkeypatch, docker):
    """0/false/off/空串一律视为关闭（只有真值才放行）。"""
    calls, _state = docker
    monkeypatch.setattr(jc, "_get_conn", _raise_operational)
    for raw in ("0", "false", "OFF", ""):
        monkeypatch.setenv("JEV_DB_DOCKER_FALLBACK", raw)
        assert jc.docker_fallback_enabled() is False, raw
        with pytest.raises(jc.DBChannelDeniedError):
            jc.db_rows("SELECT 1")
    assert calls == []


# ── 2 显式开降级 ────────────────────────────────────────────
def test_explicit_on_keeps_docker_fallback(monkeypatch, logs, docker):
    """=1 时保留旧行为：docker psql 结果原样返回，并记降级计数。"""
    monkeypatch.setenv("JEV_DB_DOCKER_FALLBACK", "1")
    calls, state = docker
    monkeypatch.setattr(jc, "_get_conn", _raise_operational)
    state["handler"] = lambda *a, **k: _FakeProc(0, "\x1f".join(("alert-1", "漂移")), "")

    assert jc.db_rows("SELECT alert_id, title FROM v2_alert") == [("alert-1", "漂移")]
    assert len(calls) == 1
    assert jc._STATE["degraded_total"] == 1
    assert jc._STATE["denied_total"] == 0
    assert jc._current_channel() == "docker"


# ── 3 5432 fail-fast ───────────────────────────────────────
def test_port_5432_fail_fast(monkeypatch, docker):
    """端口 5432 → 启动即报错，提示 infinity/5446/ZHICE_PG_PORT，且不走 docker。"""
    calls, _state = docker
    monkeypatch.setattr(jc, "_get_conn", _raise_operational)
    monkeypatch.setitem(jc.DB_DSN, "port", 5432)

    with pytest.raises(jc.DBChannelConfigError) as ei:
        jc.assert_pg_port()

    msg = str(ei.value)
    for token in ("5432", "infinity", "5446", "ZHICE_PG_PORT=5446"):
        assert token in msg, "fail-fast 提示缺少 %r: %s" % (token, msg)
    assert jc.DB_DSN["password"] not in msg

    # 三个入口都要在连库前就炸，不能被 except 吞进降级链
    for fn in (jc.db_rows, jc.db_run):
        with pytest.raises(jc.DBChannelConfigError):
            fn("SELECT 1")
    with pytest.raises(jc.DBChannelConfigError):
        jc.db_execute("UPDATE t SET x = 1")
    assert calls == [], "5432 不得走 docker 降级"


def test_port_5446_passes(monkeypatch):
    monkeypatch.setitem(jc.DB_DSN, "port", 5446)
    assert jc.assert_pg_port() is None
    assert jc.assert_pg_port(6543) is None  # 其它端口不拦，只拦 5432


# ── 4 WARN 限流 ─────────────────────────────────────────────
def test_warn_throttled_per_exc_type(monkeypatch, logs, docker):
    """同类失败同窗口只 1 条 WARN；窗口过后再放 1 条，压掉数进 suppressed。"""
    monkeypatch.delenv("JEV_DB_DOCKER_FALLBACK", raising=False)
    _calls, _state = docker
    monkeypatch.setattr(jc, "_get_conn", _raise_operational)
    now = {"t": 1000.0}
    monkeypatch.setattr(jc.time, "monotonic", lambda: now["t"])

    for _ in range(3):
        with pytest.raises(jc.DBChannelDeniedError):
            jc.db_rows("SELECT 1")

    warns = [r for r in logs if r["level"] == "warn"]
    assert len(warns) == 1, "同窗口同类失败应只留 1 条 WARN"
    assert warns[0]["exc_type"] == "_FakeOperationalError"
    assert warns[0]["suppressed"] == 0
    assert jc._STATE["denied_total"] == 3  # 限流只压日志，不压计数

    now["t"] += jc._WARN_WINDOW_SEC + 1
    with pytest.raises(jc.DBChannelDeniedError):
        jc.db_rows("SELECT 1")
    warns = [r for r in logs if r["level"] == "warn"]
    assert len(warns) == 2, "窗口过后应再放 1 条"
    assert warns[1]["suppressed"] == 2, "被压掉的 2 条要能看见"


def test_warn_throttle_separates_exc_types(monkeypatch, logs, docker):
    """不同归因各占一个窗口（不互相压）：Connection refused 不被认证失败压掉。"""
    monkeypatch.delenv("JEV_DB_DOCKER_FALLBACK", raising=False)
    _calls, _state = docker
    monkeypatch.setattr(jc.time, "monotonic", lambda: 500.0)

    monkeypatch.setattr(jc, "_get_conn", _raise_operational)
    with pytest.raises(jc.DBChannelDeniedError):
        jc.db_rows("SELECT 1")

    def _refused(*_a, **_k):
        raise ConnectionRefusedError(10061, "Connection refused")

    monkeypatch.setattr(jc, "_get_conn", _refused)
    with pytest.raises(jc.DBChannelDeniedError):
        jc.db_rows("SELECT 1")

    types = [r["exc_type"] for r in logs if r["level"] == "warn"]
    assert types == ["_FakeOperationalError", "ConnectionRefusedError"]


# ── 5 db_channel_report 字段 ───────────────────────────────
def test_db_channel_report_fields(monkeypatch, logs, docker):
    monkeypatch.delenv("JEV_DB_DOCKER_FALLBACK", raising=False)
    _calls, _state = docker
    monkeypatch.setattr(jc, "_get_conn", _raise_operational)

    before = jc.db_channel_report()
    assert before["mode"] in ("direct", "docker", "denied", "unknown")
    assert before["fallback_enabled"] is False
    assert before["degraded_total"] == 0
    assert before["last_failure"] is None
    assert before["port_error"] is None
    assert before["healthy"] is True
    assert "password" not in before["provenance"]

    with pytest.raises(jc.DBChannelDeniedError):
        jc.db_rows("SELECT 1")

    after = jc.db_channel_report()
    assert after["mode"] == "denied"
    assert after["denied_total"] == 1
    assert after["healthy"] is False
    fail = after["last_failure"]
    for key in ("at", "op", "exc_type", "errno", "host", "port", "dbname", "pw_source"):
        assert key in fail, "最近失效归因缺少 %s" % key
    assert fail["op"] == "db_rows"
    assert fail["exc_type"] == "_FakeOperationalError"
    assert fail["pw_source"] in ("env", "file", "default")
    assert jc.DB_DSN["password"] not in json.dumps(after, ensure_ascii=False)


def test_db_channel_report_port_error(monkeypatch):
    """端口错不抛异常，只在 port_error 里体现（健康卡要能读出原因）。"""
    monkeypatch.setitem(jc.DB_DSN, "port", 5432)
    rep = jc.db_channel_report()
    assert rep["healthy"] is False
    assert "infinity" in rep["port_error"]
    assert "5446" in rep["port_error"]


def test_log_db_channel_single_info_line(monkeypatch, logs):
    """启动自检（不探测）：一条 info，含脱敏 provenance + 通道，不含口令。"""
    monkeypatch.delenv("JEV_DB_DOCKER_FALLBACK", raising=False)
    monkeypatch.setitem(jc.DB_DSN, "port", 5446)
    jc._set_channel("direct")
    rep = jc.log_db_channel(probe=False)

    assert len(logs) == 1
    rec = logs[0]
    assert rec["level"] == "info"
    assert rec["msg"] == "DB 通道自检"
    assert rec["channel"] == "direct"
    assert rec["port"] == 5446
    assert rec["pw_source"] in ("env", "file", "default")
    assert rec["fallback_enabled"] is False
    assert rep["provenance"]["port"] == 5446
    assert jc.DB_DSN["password"] not in json.dumps(rec, ensure_ascii=False)


def test_log_db_channel_probe_direct_ok(monkeypatch, logs):
    """探测模式：直连 OK → channel=direct，不产生 WARN。"""
    monkeypatch.delenv("JEV_DB_DOCKER_FALLBACK", raising=False)
    monkeypatch.setattr(jc, "_get_conn", lambda: _FakeConn(rows=[(1,)]))

    rep = jc.log_db_channel()
    assert rep["mode"] == "direct"
    assert rep["healthy"] is True
    assert [r for r in logs if r["level"] == "warn"] == []


def test_log_db_channel_probe_failure_never_raises(monkeypatch, logs):
    """探测模式：直连失败 → 记 WARN + channel=denied，但启动自检本身不抛。"""
    monkeypatch.delenv("JEV_DB_DOCKER_FALLBACK", raising=False)
    monkeypatch.setattr(jc, "_get_conn", _raise_operational)

    rep = jc.log_db_channel()  # 不 pytest.raises：这里绝不能抛
    assert rep["mode"] == "denied"
    assert rep["healthy"] is False
    info = [r for r in logs if r["msg"] == "DB 通道自检"][-1]
    assert info["probe_error"].startswith("_FakeOperationalError")
    assert any(r["level"] == "warn" for r in logs)


# ── 6 三入口行为一致 + 口令不外泄 ──────────────────────────
def test_three_entrypoints_consistent(monkeypatch, docker):
    monkeypatch.delenv("JEV_DB_DOCKER_FALLBACK", raising=False)
    calls, _state = docker
    monkeypatch.setattr(jc, "_get_conn", _raise_operational)

    with pytest.raises(jc.DBChannelDeniedError):
        jc.db_rows("SELECT 1")
    with pytest.raises(jc.DBChannelDeniedError):
        jc.db_execute("UPDATE t SET x = 1")
    with pytest.raises(jc.DBChannelDeniedError):
        jc.db_run("CREATE TABLE t (x int)")
    assert calls == []
    assert jc._STATE["denied_total"] == 3


def test_messages_never_leak_password(monkeypatch, logs, docker):
    """异常消息、日志、通道报告三处都不许出现口令值。"""
    monkeypatch.delenv("JEV_DB_DOCKER_FALLBACK", raising=False)
    _calls, _state = docker
    monkeypatch.setattr(jc, "_get_conn", _raise_operational)
    pw = jc.DB_DSN["password"]
    assert pw, "本用例假定口令非空"

    texts = []
    with pytest.raises(jc.DBChannelDeniedError) as ei:
        jc.db_rows("SELECT 1")
    texts.append(str(ei.value))

    monkeypatch.setitem(jc.DB_DSN, "port", 5432)
    with pytest.raises(jc.DBChannelConfigError) as ei2:
        jc.db_rows("SELECT 1")
    texts.append(str(ei2.value))

    texts.append(json.dumps(logs, ensure_ascii=False))
    texts.append(json.dumps(jc.db_channel_report(), ensure_ascii=False))
    for t in texts:
        assert pw not in t, "口令泄露: %s" % t[:200]
        assert "password=" not in t


# ── 7 probe_connectivity 复用通道字段 ──────────────────────
def test_probe_reuses_channel_report(monkeypatch, docker):
    monkeypatch.delenv("JEV_DB_DOCKER_FALLBACK", raising=False)
    _calls, _state = docker
    monkeypatch.setattr(jc, "_get_conn", _raise_operational)
    monkeypatch.setattr(shutil, "which", lambda name: "docker")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeProc(0, "27\n", ""))

    res = jc.probe_connectivity()
    assert res["overall"] == "degraded"  # 潜能：docker 在
    assert res["effective_overall"] == "down"  # 实况：降级未放行
    assert res["channel"]["fallback_enabled"] is False
    assert res["channel"]["port_error"] is None
    assert "password" not in res["channel"]["provenance"]

    monkeypatch.setenv("JEV_DB_DOCKER_FALLBACK", "1")
    res2 = jc.probe_connectivity()
    assert res2["effective_overall"] == "degraded"
    assert res2["channel"]["fallback_enabled"] is True
