# -*- coding: utf-8 -*-
"""DW-PG-DSN：dataset_registry PG 连接解析 fallback 单测（2026-11-04 task-52）。

口径（`resolve_pg_connection`）：
1. 显式参数 `pg_dsn`（含 `""` = 显式禁用，不回退）；
2. 环境变量 `ZHICE_V2_DSN`；
3. `jev_common.DB_DSN` fallback（`DW_PG_DSN_FALLBACK=0` 可关闭）；
4. 全部不可用 → `connector_status=missing_dsn`（fail-visible、不假绿）。

安全边界：连接描述仅保留 host:port/db 标签；密码只在内存 kwargs 中，
**任何 resolver/stats/get/CLI 输出都不含密码或完整 DSN**。

全程 mock：不连真实 PG、不 import 真实 jev_common、不读写生产数据。
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dataset_registry as dr  # noqa: E402
import dataset_registry_cli as cli  # noqa: E402

SECRET = "dummy-pw-for-test-only-42"  # 仅测试用假密码，不是任何真实凭据
JEV_DSN = {
    "host": "127.0.0.1",
    "port": 5446,
    "user": "dw_reader",
    "password": SECRET,
    "dbname": "zhice_os_v2",
}


# ── 工具 ──────────────────────────────────────────────────────────────
def _inventory(tmp_path: Path) -> Path:
    inv = tmp_path / "dataset_inventory_test.json"
    inv.write_text(json.dumps({"datasets": []}, ensure_ascii=False), encoding="utf-8")
    return inv


def _reg(tmp_path: Path, **kwargs) -> dr.DatasetRegistry:
    kwargs.setdefault("allowed_roots", [tmp_path])
    kwargs.setdefault("temp_whitelist", [])
    return dr.DatasetRegistry(_inventory(tmp_path), **kwargs)


def _fake_jev_common(monkeypatch, dsn=None):
    """注入 sys.modules['jev_common']（不 import 真实 jev_common）。"""
    module = types.ModuleType("jev_common")
    if dsn is not None:
        module.DB_DSN = dict(dsn)
    monkeypatch.setitem(sys.modules, "jev_common", module)
    return module


def _no_jev_common(monkeypatch):
    """模拟 jev_common 不可用：DB_DSN 缺失（AttributeError）。"""
    return _fake_jev_common(monkeypatch, dsn=None)


class _ExplodingJevCommon(types.ModuleType):
    """被访问 DB_DSN 即断言失败：证明 env/显式 DSN 优先时不触发 fallback。"""

    def __init__(self):
        super().__init__("jev_common")

    @property
    def DB_DSN(self):  # pragma: no cover - 触发即为测试失败
        raise AssertionError("环境变量/显式 DSN 已提供时不应读取 jev_common fallback")


class _FakeCursor:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.description = [("id",), ("code",)]
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return list(self.rows)

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cursors):
        self._cursors = list(cursors)
        self.closed = False
        self.readonly = None
        self.autocommit = None

    def cursor(self):
        assert self._cursors, "unexpected extra cursor() call"
        return self._cursors.pop(0)

    def set_session(self, readonly=False, autocommit=False):
        self.readonly, self.autocommit = readonly, autocommit

    def close(self):
        self.closed = True


def _clean_env(monkeypatch):
    monkeypatch.delenv(dr.PG_DSN_ENV, raising=False)
    monkeypatch.delenv(dr.PG_DSN_FALLBACK_ENV, raising=False)


# ── 1. 解析优先级 ─────────────────────────────────────────────────────
class TestResolvePrecedence:
    def test_env_beats_jev_common(self, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv(dr.PG_DSN_ENV,
                           "postgresql://reader:env-secret@10.0.0.9:5446/zhice_os_v2")
        monkeypatch.setitem(sys.modules, "jev_common", _ExplodingJevCommon())
        conn = dr.resolve_pg_connection()
        assert conn["source"] == "env"
        assert conn["dsn"].startswith("postgresql://")
        assert conn["kwargs"] is None
        assert conn["label"] == "pg@10.0.0.9:5446/zhice_os_v2"
        assert "env-secret" not in json.dumps(
            {k: v for k, v in conn.items() if k != "dsn"})

    def test_explicit_beats_env_and_fallback(self, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv(dr.PG_DSN_ENV,
                           "postgresql://reader:env-secret@10.0.0.9:5446/zhice_os_v2")
        monkeypatch.setitem(sys.modules, "jev_common", _ExplodingJevCommon())
        conn = dr.resolve_pg_connection("postgresql://x:y@127.0.0.1:5446/db")
        assert conn["source"] == "explicit"
        assert conn["dsn"].endswith("/db")

    def test_explicit_empty_disables_fallback(self, monkeypatch):
        _clean_env(monkeypatch)
        _fake_jev_common(monkeypatch, JEV_DSN)  # fallback 可用也不得使用
        conn = dr.resolve_pg_connection("")
        assert conn["source"] == "none"
        assert conn["reason"] == "explicit_empty_dsn"

    def test_jev_common_fallback_when_env_absent(self, monkeypatch):
        _clean_env(monkeypatch)
        _fake_jev_common(monkeypatch, JEV_DSN)
        conn = dr.resolve_pg_connection()
        assert conn["source"] == "jev_common"
        assert conn["dsn"] is None
        assert conn["kwargs"] == {**JEV_DSN, "connect_timeout": dr.PG_TIMEOUT_S}
        assert conn["label"] == "pg@127.0.0.1:5446/zhice_os_v2"
        assert SECRET not in conn["label"]
        assert conn["reason"] == ""

    def test_fallback_disabled_by_env(self, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv(dr.PG_DSN_FALLBACK_ENV, "0")
        _fake_jev_common(monkeypatch, JEV_DSN)
        conn = dr.resolve_pg_connection()
        assert conn["source"] == "none"
        assert conn["reason"] == "fallback_disabled"

    def test_both_sources_missing(self, monkeypatch):
        _clean_env(monkeypatch)
        _no_jev_common(monkeypatch)
        conn = dr.resolve_pg_connection()
        assert conn["source"] == "none"
        assert conn["reason"].startswith("jev_common_unavailable:")
        assert SECRET not in json.dumps(conn, default=str)


# ── 2. Registry 集成（wav1/wave2、stats、fail-visible） ─────────────
class TestRegistryIntegration:
    def test_env_dsn_probe_gets_string(self, tmp_path, monkeypatch):
        _clean_env(monkeypatch)
        env_dsn = "postgresql://reader:env-secret@127.0.0.1:5446/zhice_os_v2"
        monkeypatch.setenv(dr.PG_DSN_ENV, env_dsn)
        seen = {}

        def probe(target, tables):
            seen["target"], seen["tables"] = target, list(tables)
            return {("public", "v2_acad1_pool"): 9}

        reg = _reg(tmp_path, pg_probe=probe)
        assert seen["target"] == env_dsn
        assert reg.connector_status == "ok"
        assert reg.pg_connector_source == "env"
        assert reg.get("pg_public_v2_acad1_pool")["status"] == "active"
        assert reg.stats_v2()["connector_source"] == "env"
        assert "env-secret" not in json.dumps(reg.stats_v2(), default=str)

    def test_fallback_used_and_no_credential_leak(self, tmp_path, monkeypatch):
        _clean_env(monkeypatch)
        _fake_jev_common(monkeypatch, JEV_DSN)
        seen = {}

        def probe(target, tables):
            seen["target"], seen["tables"] = target, list(tables)
            return {("public", "v2_acad1_pool"): 12}

        reg = _reg(tmp_path, pg_probe=probe)
        assert reg.connector_status == "ok"
        assert reg.pg_connector_source == "jev_common"
        assert reg.pg_connector_label == "pg@127.0.0.1:5446/zhice_os_v2"
        assert seen["target"] == {**JEV_DSN, "connect_timeout": dr.PG_TIMEOUT_S}
        assert seen["target"]["password"] == SECRET  # 仅内存内传给连接层
        row = reg.get("pg_public_v2_acad1_pool")
        assert row["status"] == "active" and row["available"] is True
        assert row["connector_source"] == "jev_common"
        # 安全：所有可输出面（stats/get/label）都不含密码/完整 DSN
        surfaces = json.dumps({
            "stats_v2": reg.stats_v2(),
            "row": row,
            "label": reg.pg_connector_label,
            "error": reg.connector_error,
        }, ensure_ascii=False, default=str)
        assert SECRET not in surfaces
        assert "reader:" not in surfaces
        assert "postgresql://" not in surfaces

    def test_explicit_empty_stays_missing_dsn(self, tmp_path, monkeypatch):
        _clean_env(monkeypatch)
        _fake_jev_common(monkeypatch, JEV_DSN)
        reg = _reg(tmp_path, pg_dsn="")
        assert reg.pg_connector_source == "none"
        assert reg.connector_status == "missing_dsn"
        assert reg.connector_error == "explicit_empty_dsn"
        assert len(reg.pg_datasets) == len(dr.PG_WAVE1_TABLES)
        assert all(row["status"] == "missing_dsn" for row in reg.pg_datasets)
        sv2 = reg.stats_v2()
        assert sv2["connector_status"] == "missing_dsn"
        assert sv2["pg"]["available"] == 0
        with pytest.raises(dr.ConnectorUnavailable) as exc:
            reg.sample("pg_public_v2_acad1_pool")
        assert exc.value.status == "missing_dsn"

    def test_fallback_disabled_keeps_missing_dsn(self, tmp_path, monkeypatch):
        _clean_env(monkeypatch)
        monkeypatch.setenv(dr.PG_DSN_FALLBACK_ENV, "0")
        _fake_jev_common(monkeypatch, JEV_DSN)
        reg = _reg(tmp_path)
        assert reg.connector_status == "missing_dsn"
        assert reg.connector_error == "fallback_disabled"
        assert reg.pg_connector_source == "none"

    def test_connector_failure_is_fail_visible(self, tmp_path, monkeypatch):
        _clean_env(monkeypatch)
        _fake_jev_common(monkeypatch, JEV_DSN)

        def boom(target):
            raise ConnectionError("connection refused (test)")

        reg = _reg(tmp_path, pg_connect=boom)
        assert reg.connector_status == "connector_unavailable"
        assert "connection refused" in reg.connector_error
        assert SECRET not in reg.connector_error
        with pytest.raises(dr.ConnectorUnavailable) as exc:
            reg.sample("pg_public_v2_acad1_pool")
        assert exc.value.status == "connector_unavailable"

    def test_default_probe_uses_kwargs_target_and_wave2(self, tmp_path, monkeypatch):
        _clean_env(monkeypatch)
        _fake_jev_common(monkeypatch, JEV_DSN)
        captured = {}

        disc = _FakeCursor([("v2_acad1_pool",), ("v2_base_signal",)])
        meta = _FakeCursor([("v2_acad1_pool", "r", 12), ("v2_base_signal", "r", 88)])
        sample_cur = _FakeCursor([(1, "600519")])
        sample_conn = _FakeConn([sample_cur])

        def fake_connect(target):
            captured.setdefault("targets", []).append(target)
            if len(captured["targets"]) == 1:
                return _FakeConn([disc, meta])
            return sample_conn

        monkeypatch.setattr(dr, "_pg_connect_default", fake_connect)
        reg = _reg(tmp_path)
        assert reg.connector_status == "ok"
        assert reg.pg_connector_source == "jev_common"
        assert captured["targets"][0]["host"] == JEV_DSN["host"]
        assert captured["targets"][0]["dbname"] == JEV_DSN["dbname"]
        assert captured["targets"][0]["connect_timeout"] == dr.PG_TIMEOUT_S
        assert SECRET not in json.dumps(reg.stats_v2(), default=str)
        row = reg.get("pg_public_v2_acad1_pool")
        assert row["status"] == "active" and row["available"] is True
        assert row["row_estimate"] == 12
        assert reg.get("pg_public_v2_base_signal")["available"] is True
        out = reg.sample("pg_public_v2_acad1_pool", n=2)
        assert out["sample"] == [[1, "600519"]]
        assert out["connector"] == dr.PG_CONNECTOR


# ── 3. _pg_connect_default 接受 kwargs（只读会话） ───────────────────
class TestConnectFactory:
    def test_kwargs_target_connect_and_readonly(self, monkeypatch):
        captured = {}

        class _Conn:
            readonly = None

            def set_session(self, readonly=False, autocommit=False):
                self.readonly = readonly

        class _FakePG(types.ModuleType):
            def __init__(self):
                super().__init__("psycopg2")

            def connect(self, **kwargs):
                captured["kwargs"] = kwargs
                return _Conn()

        monkeypatch.setitem(sys.modules, "psycopg2", _FakePG())
        conn = dr._pg_connect_default({**JEV_DSN})
        assert captured["kwargs"] == {**JEV_DSN, "connect_timeout": dr.PG_TIMEOUT_S}
        assert conn.readonly is True

    def test_empty_target_rejected(self):
        with pytest.raises(dr.ConnectorUnavailable) as exc:
            dr._pg_connect_default("")
        assert exc.value.status == "missing_dsn"


# ── 4. CLI get/status（/api/dw/get 同源 registry.get） ───────────────
class TestCliAndApiStatus:
    def test_cli_get_pg_status_available(self, tmp_path, monkeypatch, capsys):
        _clean_env(monkeypatch)
        _fake_jev_common(monkeypatch, JEV_DSN)

        def probe(target, tables):
            return {("public", "v2_acad1_pool"): 12}

        reg = _reg(tmp_path, pg_probe=probe)
        monkeypatch.setattr(cli, "DatasetRegistry", lambda: reg)
        rc = cli.main(["--json", "get", "--id", "pg_public_v2_acad1_pool"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["kind"] == "pg_table"
        assert payload["status"] == "active"
        assert payload["connector_source"] == "jev_common"
        assert SECRET not in json.dumps(payload, ensure_ascii=False, default=str)
