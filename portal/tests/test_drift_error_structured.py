# -*- coding: utf-8 -*-
"""S1-04 F3/F4 单测：drift 错误结构化 + portal 侧 DSN 去重（全 mock，不连生产 PG）。

覆盖：
  F3  api_daily_data.drift_section()：成功 exists=true 契约不变；失败 exists=false
      且返回结构化 error_detail{kind,errno,stage,type,message,dsn}；error 字符串兼容；
      口令/DSN 密码段绝不出现在响应（含异常文本注入场景）。
  F4  api_flow：DSN 连接参数/来源委托 jev_common（无重复 host/port/口令拼装）；
      jev_common 不可用时回退旧链（fail-open）。
边界：全部 monkeypatch，不连 PG、不读生产 .env 口令（F4 回退分支被 mock 掉）。

运行（仓库根）：
  C:\\项目\\py312\\python.exe -X utf8 -m pytest portal/tests/test_drift_error_structured.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parents[1]
REPO = PORTAL.parent
for _p in (str(PORTAL), str(REPO / "jev-eval")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import api_daily_data as add  # noqa: E402

try:  # api_flow 依赖 api_common（只读路径常量），导入失败则跳过 F4 用例
    import api_flow as flow  # noqa: E402

    HAVE_FLOW = True
except Exception:  # noqa: BLE001
    flow = None
    HAVE_FLOW = False


# ── fakes ──────────────────────────────────────────────────
class _FakeFallbackError(Exception):
    """模拟 jev_common.DBFallbackError（带 errno/stage 的降级通道异常）。"""

    errno = 22
    stage = "spawn"


class _FakeOperationalError(Exception):
    errno = 1045


class _FakeDB:
    def __init__(self, rows=None, count=0, exc=None, prov=None):
        self._rows = rows if rows is not None else []
        self._count = count
        self._exc = exc
        self._prov = prov

    def db_rows(self, sql, params=None):
        if self._exc is not None:
            raise self._exc
        if "count(*)" in sql:
            return [(self._count,)]
        return self._rows

    def dsn_provenance(self):
        if self._prov is None:
            raise RuntimeError("provenance unavailable")
        return self._prov


_PROV = {
    "host": "127.0.0.1",
    "port": 5446,
    "user": "zhice",
    "dbname": "zhice_os_v2",
    "container": "zhice-db",
    "password_source": "file",
    "password_set": True,
    "password_len": 32,
    "driver": "psycopg2 2.9.12",
    "env_overrides": [],
}


def _patch_db(monkeypatch, db):
    monkeypatch.setattr(add, "_db", lambda: db)


# ── F3：成功契约不变 ───────────────────────────────────────
def test_drift_success_contract_unchanged(monkeypatch):
    db = _FakeDB(
        rows=[("alert-1", "JEV判定漂移·scope", "msg", "OPEN",
               "2026-11-02 07:10:03+00:00")],
        count=5,
        prov=_PROV,
    )
    _patch_db(monkeypatch, db)
    out = add.drift_section()
    assert out["exists"] is True
    assert out["open_count"] == 5
    assert out["alerts"][0]["alert_id"] == "alert-1"
    assert "error" not in out and "error_detail" not in out


# ── F3：失败结构化字段（降级通道异常）──────────────────────
def test_drift_failure_structured_fallback(monkeypatch):
    db = _FakeDB(
        exc=_FakeFallbackError("docker 降级通道不可用(OSError): [Errno 22] Invalid argument"),
        prov=_PROV,
    )
    _patch_db(monkeypatch, db)
    out = add.drift_section()
    assert out["exists"] is False  # 兼容语义不变
    assert isinstance(out["error"], str)
    assert "v2_alert 不可达" in out["error"]
    d = out["error_detail"]
    assert d["kind"] == "fallback_unavailable"
    assert d["errno"] == 22
    assert d["stage"] == "spawn"
    assert d["type"] == "_FakeFallbackError"
    assert set(d["dsn"]) >= {"host", "port", "dbname", "user", "pw_source"}
    assert d["dsn"]["pw_source"] == "file"
    assert d["dsn"]["host"] == "127.0.0.1"


def test_drift_failure_connect_failed(monkeypatch):
    db = _FakeDB(
        exc=_FakeOperationalError(
            "connection to server at \"127.0.0.1\", port 5446 failed: "
            "password authentication failed for user \"zhice\""),
        prov=_PROV,
    )
    _patch_db(monkeypatch, db)
    d = add.drift_section()["error_detail"]
    assert d["kind"] == "connect_failed"
    assert d["errno"] == 1045
    assert d["stage"] is None


def test_drift_failure_query_failed(monkeypatch):
    db = _FakeDB(exc=RuntimeError('column "foo" does not exist'), prov=_PROV)
    _patch_db(monkeypatch, db)
    d = add.drift_section()["error_detail"]
    assert d["kind"] == "query_failed"
    assert d["errno"] is None
    assert d["stage"] is None


def test_drift_failure_driver_missing(monkeypatch):
    def _boom():
        raise ImportError("No module named 'jev_common'")

    monkeypatch.setattr(add, "_db", _boom)
    out = add.drift_section()
    assert out["exists"] is False
    assert out["error_detail"]["kind"] == "driver_missing"
    assert out["error_detail"]["dsn"] is None


# ── F3：口令绝不出现在响应（含异常文本注入）────────────────
def test_drift_failure_never_leaks_password(monkeypatch):
    secret = "S3CR3T-PW-DO-NOT-LEAK"
    exc = _FakeFallbackError(
        "auth failed password=%s for postgres://zhice:%s@127.0.0.1:5446/zhice_os_v2"
        % (secret, secret)
    )
    db = _FakeDB(exc=exc, prov=_PROV)
    _patch_db(monkeypatch, db)
    out = add.drift_section()
    blob = json.dumps(out, ensure_ascii=False)
    assert secret not in blob
    assert "password=%s" % secret not in blob
    # 脱敏占位确实生效
    assert "password=***" in blob or "password=***" in out["error_detail"]["message"]
    assert "://zhice:***@" in out["error_detail"]["message"]
    # DSN 结构里只有来源/是否设置，没有口令值
    assert "password" not in out["error_detail"]["dsn"]
    assert out["error_detail"]["dsn"]["password_set"] is True


def test_dsn_provenance_whitelist(monkeypatch):
    prov = dict(_PROV, password="REAL-PASSWORD-VALUE", password_hint="x")
    _patch_db(monkeypatch, _FakeDB(prov=prov))
    d = add._dsn_provenance()
    assert set(d) == {"host", "port", "dbname", "user", "pw_source",
                      "password_set", "driver"}
    assert "REAL-PASSWORD-VALUE" not in json.dumps(d, ensure_ascii=False)
    assert "password" not in d


# ── F3：Handler 汇总出口（drift 一节坏不拖垮整卡）─────────
def test_daily_data_handler_includes_structured_error(monkeypatch):
    class _H(add.Handler):
        def __init__(self):
            self.payload = None

        def _norm_qs(self, qs):
            return {}

        def _json(self, obj, code=200):
            self.payload = (obj, code)

    monkeypatch.setattr(add, "dist_section", lambda: {"exists": False})
    monkeypatch.setattr(add, "bt_section", lambda: {"exists": False})
    monkeypatch.setattr(add, "impact_section", lambda: {"exists": False})
    monkeypatch.setattr(add, "health_section", lambda: {"items": []})
    _patch_db(monkeypatch, _FakeDB(exc=_FakeFallbackError("boom"), prov=_PROV))
    h = _H()
    h._daily_data("")
    body, code = h.payload
    assert code == 200
    assert body["drift"]["exists"] is False
    assert body["drift"]["error_detail"]["kind"] == "fallback_unavailable"
    assert body["dist"]["exists"] is False


# ── F4：portal 侧重 DSN 拼装已委托 jev_common ──────────────
@pytest.mark.skipif(not HAVE_FLOW, reason="api_flow 依赖不可用")
def test_flow_pg_kwargs_delegate_to_jev_common(monkeypatch):
    class _FakeJC:
        DB_DSN = {"host": "10.0.0.1", "port": 6000, "user": "u",
                  "password": "p", "dbname": "db"}

        @staticmethod
        def dsn_provenance():
            return dict(_PROV)

    monkeypatch.setattr(flow, "_jev_common", lambda: _FakeJC)
    kw = flow._pg_connect_kwargs(3)
    assert kw["host"] == "10.0.0.1" and kw["port"] == 6000
    assert kw["connect_timeout"] == 3
    prov = flow._pg_dsn_provenance()
    assert prov["host"] == "127.0.0.1" and prov["pw_source"] == "file"
    assert "password" not in prov


@pytest.mark.skipif(not HAVE_FLOW, reason="api_flow 依赖不可用")
def test_flow_pg_kwargs_fallback_when_jev_common_missing(monkeypatch):
    monkeypatch.setattr(flow, "_jev_common", lambda: None)
    monkeypatch.setattr(flow, "_zhice_pg_password", lambda: "fallback-pw")
    monkeypatch.setenv("ZHICE_PG_PORT", "5446")
    kw = flow._pg_connect_kwargs(2)
    assert kw["port"] == 5446
    assert kw["password"] == "fallback-pw"
    assert kw["connect_timeout"] == 2
    assert flow._pg_dsn_provenance() is None
