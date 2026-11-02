# -*- coding: utf-8 -*-
"""DWH-103 wave2 定向测试：pg_table 动态发现 public.v2_% 全量 + 安全取样 + CLI 契约。

口径（ADR-019 D3）：DSN 可用时以 `discover_v2_tables()` 发现结果为准（不硬编码 83/84）；
`stats_v2().pg.total`=发现数、`pg.available`=可读元数据数（relkind='r'）；
DSN 未设置保持 wave1 固定清单 + missing_dsn（不报错、不假绿）。
测试全部用 mock 连接/游标，不连接真实数据库、不写任何数据。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dataset_registry as dr  # noqa: E402
import dataset_registry_cli as cli  # noqa: E402


class _FakeCursor:
    def __init__(self, rows=None, error=None):
        self.rows = list(rows or [])
        self.error = error
        self.executed = []
        self.closed = False
        self.description = [("id",), ("name",)]

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if self.error is not None:
            raise self.error

    def fetchall(self):
        return list(self.rows)

    def close(self):
        self.closed = True


class _FakeConn:
    def __init__(self, cursors):
        self._cursors = list(cursors)
        self.closed = False

    def cursor(self):
        assert self._cursors, "unexpected extra cursor() call"
        return self._cursors.pop(0)

    def close(self):
        self.closed = True


def _fake_connect_factory(conns):
    queue = list(conns)
    calls = []

    def connect(dsn):
        calls.append(dsn)
        assert queue, "unexpected extra connect() call"
        return queue.pop(0)

    connect.calls = calls
    return connect


def _inventory(tmp_path: Path) -> Path:
    inv = tmp_path / "inv.json"
    inv.write_text(json.dumps({"datasets": [
        {"domain": "学习派生", "label": "testdb.t1", "kind": "sqlite_table",
         "path": "testdb.db", "registered": True, "exposed": False,
         "suggested_id": "t1_table"},
    ]}, ensure_ascii=False), encoding="utf-8")
    return inv


def _reg(tmp_path: Path, **kwargs) -> dr.DatasetRegistry:
    kwargs.setdefault("allowed_roots", [tmp_path])
    kwargs.setdefault("temp_whitelist", [])  # 聚焦 PG 动态发现，契约白名单不在本文件断言范围
    return dr.DatasetRegistry(_inventory(tmp_path), **kwargs)


def _discovery_conn(names, metadata=None):
    meta_rows = metadata if metadata is not None else [
        (name, "r", idx * 10) for idx, name in enumerate(names)]
    disc_cur = _FakeCursor(rows=[(name,) for name in names])
    meta_cur = _FakeCursor(rows=meta_rows)
    return _FakeConn([disc_cur, meta_cur]), disc_cur, meta_cur


# ---------- discover_v2_tables：SQL/排序/异常 ----------

def test_discover_v2_sql_and_returns_sorted_full_set():
    names = ["v2_t%03d" % i for i in range(84)]
    cur = _FakeCursor(rows=[(name,) for name in reversed(names)])
    result = dr.discover_v2_tables(_FakeConn([cur]))
    assert result == names and len(result) == 84
    sql = cur.executed[0][0]
    assert "FROM pg_tables" in sql
    assert "schemaname = 'public'" in sql
    assert "LIKE 'v2\\_%'" in sql  # 下划线已转义（\\_），% 才是通配符
    assert "ORDER BY 1" in sql
    assert cur.closed is True
    # 不硬编码：发现结果与 wave1 固定清单无关
    assert set(result) != {spec["table"] for spec in dr.PG_WAVE1_TABLES}


def test_discover_query_error_is_fail_visible():
    conn = _FakeConn([_FakeCursor(error=RuntimeError("pg boom"))])
    with pytest.raises(RuntimeError, match="pg boom"):
        dr.discover_v2_tables(conn)


def test_registry_discovery_error_marks_connector_unavailable(tmp_path):
    def bad_connect(dsn):
        raise ConnectionError("connect refused (test)")

    reg = _reg(tmp_path, pg_dsn="postgresql://fake/zhice_os_v2", pg_connect=bad_connect)
    assert reg.connector_status == "connector_unavailable"
    assert "connect refused" in reg.connector_error
    with pytest.raises(dr.ConnectorUnavailable) as exc:
        reg.sample("pg_public_v2_acad1_pool")
    assert exc.value.status == "connector_unavailable"


# ---------- DSN 可用：动态发现全量（以发现结果为准） ----------

def test_dsn_available_discovers_full_set(tmp_path):
    names = ["v2_t%03d" % i for i in range(84)]
    probe_conn, disc_cur, meta_cur = _discovery_conn(names)
    connect = _fake_connect_factory([probe_conn])
    reg = _reg(tmp_path, pg_dsn="postgresql://fake/zhice_os_v2", pg_connect=connect)
    assert connect.calls == ["postgresql://fake/zhice_os_v2"]
    assert reg.connector_status == "ok"
    assert len(reg.pg_datasets) == 84
    assert reg.pg_discovered == set(names)
    assert meta_cur.executed[0][1] == (names,)  # 元数据查询参数=发现清单
    row = reg.pg_datasets[0]
    assert row["kind"] == "pg_table"
    assert row["ref"] == "dataset/pg/" + row["physical"]["table"]
    assert row["physical"] == {"schema": "public", "table": names[0]}
    assert row["connector"] == dr.PG_CONNECTOR
    assert row["access"] == "read_only" and row["freshness"] is None
    sv2 = reg.stats_v2()
    assert sv2["connector_status"] == "ok"
    assert sv2["pg"]["total"] == 84
    assert sv2["pg"]["available"] == 84
    assert sv2["all"]["total"] == reg.stats()["total"] + 84


def test_stats_v2_total_and_available_from_relkind(tmp_path):
    names = ["v2_t%03d" % i for i in range(84)]
    metadata = [(n, "r", 10) for n in names[:80]] + [(n, "v", None) for n in names[80:]]
    probe_conn, _, _ = _discovery_conn(names, metadata)
    reg = _reg(tmp_path, pg_dsn="postgresql://fake/zhice_os_v2",
               pg_connect=_fake_connect_factory([probe_conn]))
    sv2 = reg.stats_v2()
    assert sv2["pg"]["total"] == 84           # = 发现数（不硬编码）
    assert sv2["pg"]["available"] == 80       # = relkind='r' 可读元数据数
    assert sv2["all"]["total"] == reg.stats()["total"] + 80
    unreadable = [d for d in reg.pg_datasets if d["status"] == "metadata_unreadable"]
    assert len(unreadable) == 4 and all(d["available"] is False for d in unreadable)


# ---------- sample：仅发现集合内 + 安全标识符 ----------

def test_sample_only_discovered_identifiers(tmp_path):
    probe_conn, _, _ = _discovery_conn(["v2_t000", "v2_t001"])
    sample_cur = _FakeCursor(rows=[[1, "a"], [2, "b"]])
    sample_conn = _FakeConn([sample_cur])
    connect = _fake_connect_factory([probe_conn, sample_conn])
    reg = _reg(tmp_path, pg_dsn="postgresql://fake/zhice_os_v2", pg_connect=connect)
    out = reg.sample("pg_public_v2_t000", n=5)
    assert out["kind"] == "pg_table"
    assert out["columns"] == ["id", "name"]
    assert out["sample"] == [[1, "a"], [2, "b"]]
    assert 'SELECT * FROM "public"."v2_t000" LIMIT %s' in sample_cur.executed[0][0]

    rogue = dict(reg.pg_datasets[0])
    rogue.update({"dataset_id": "pg_public_v2_rogue",
                  "physical": {"schema": "public", "table": "v2_rogue"}})
    reg.pg_datasets.append(rogue)
    reg._extra_by_id[rogue["dataset_id"]] = rogue
    with pytest.raises(PermissionError, match="发现集合"):
        reg.sample("pg_public_v2_rogue")

    bad = dict(rogue)
    bad.update({"dataset_id": "pg_public_V2_BAD",
                "physical": {"schema": "public", "table": "V2_BAD"}})
    reg._extra_by_id[bad["dataset_id"]] = bad
    with pytest.raises(PermissionError):
        reg.sample("pg_public_V2_BAD")


# ---------- DSN 未设置：保持 wave1 行为 ----------

def test_no_dsn_keeps_wave1_behavior(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHICE_V2_DSN", raising=False)
    reg = _reg(tmp_path)
    assert reg.connector_status == "missing_dsn"
    assert reg.pg_discovered is None
    assert [d["physical"]["table"] for d in reg.pg_datasets] == \
        [spec["table"] for spec in dr.PG_WAVE1_TABLES]
    sv2 = reg.stats_v2()
    assert sv2["connector_status"] == "missing_dsn"
    assert sv2["pg"]["total"] == len(dr.PG_WAVE1_TABLES)
    assert sv2["pg"]["available"] == 0
    with pytest.raises(dr.ConnectorUnavailable) as exc:
        reg.sample("pg_public_v2_acad1_pool")
    assert exc.value.status == "missing_dsn"


# ---------- CLI stats-v2 --json 契约 ----------

class _StubRegistry:
    def __init__(self, connector_status="ok", total=84, available=80):
        self._connector_status = connector_status
        self._total = total
        self._available = available

    def stats_v2(self):
        return {
            "denominator_version": "v2",
            "connector_status": self._connector_status,
            "pg": {"total": self._total, "available": self._available,
                   "connector_status": self._connector_status},
            "all": {"total": self._total, "registered": self._available,
                    "exposed": self._available},
        }


def test_cli_stats_v2_json_contract(monkeypatch, capsys):
    monkeypatch.setattr(cli, "DatasetRegistry", lambda: _StubRegistry())
    assert cli.main(["--json", "stats-v2"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pg"]["total"] == 84 and payload["pg"]["available"] == 80
    assert payload["connector_status"] == "ok"

    monkeypatch.setattr(cli, "DatasetRegistry",
                        lambda: _StubRegistry(connector_status="connector_unavailable"))
    assert cli.main(["--json", "stats-v2"]) == 2  # fail-visible：连接器异常 rc=2
    payload = json.loads(capsys.readouterr().out)
    assert payload["connector_status"] == "connector_unavailable"

def test_normalize_sqlalchemy_dsn_to_libpq():
    """SQLAlchemy 风格 DSN（.env 常见）自动归一为 libpq，避免 invalid dsn。"""
    assert dr._normalize_dsn("postgresql+psycopg2://u:p@h:5446/db") == "postgresql://u:p@h:5446/db"
    assert dr._normalize_dsn("postgresql+psycopg://u:p@h/db") == "postgresql://u:p@h/db"
    assert dr._normalize_dsn("postgresql://u:p@h/db") == "postgresql://u:p@h/db"

