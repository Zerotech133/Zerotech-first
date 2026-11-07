# -*- coding: utf-8 -*-
"""P1-02：qengine_sync 真实测试库的只读契约测试。

这不是 mock 测试，必须显式提供测试库 DSN（``QENGINE_SYNC_TEST_DB_URL``）才会执行。
DSN 必须指向带 ``test`` 字样的 qengine_sync 数据库；测试会拒绝生产库名，
并只以 readonly 会话执行 SELECT。

要求测试库具备：
- schema ``qengine_sync``；
- 一次成功同步的 ``qg_sync_run`` 及其 ``qg_sync_check``（19 表 PASS）；
- 19 张 qg_* 表 + 前端所需的 snapshot 数据。

连接串必须是 psycopg2 可识别的 ``postgresql://...``，不能是
``postgresql+psycopg2://...``（SQLAlchemy 形式）。
"""
from __future__ import annotations

import os
from contextlib import contextmanager

import psycopg2
import pytest
from psycopg2.extras import RealDictCursor
from sqlalchemy.engine import make_url

DSN_ENV = "QENGINE_SYNC_TEST_DB_URL"
SCHEMA = "qengine_sync"


def _normalized_test_dsn() -> str | None:
    raw = os.environ.get(DSN_ENV, "").strip()
    if not raw:
        return None
    try:
        url = make_url(raw)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"{DSN_ENV} 不是合法 DSN: {exc}")
    database = (url.database or "").strip().lower()
    if not database or "test" not in database:
        pytest.fail(
            f"{DSN_ENV} 必须指向名称含 test 的测试库；当前 database={database!r}，"
            "拒绝连接生产/未知库"
        )
    if url.get_backend_name() != "postgresql":
        pytest.fail(f"{DSN_ENV} 必须是 postgresql DSN，当前 backend={url.get_backend_name()}")
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


@pytest.fixture(scope="module")
def contract_dsn() -> str:
    dsn = _normalized_test_dsn()
    if not dsn:
        pytest.skip(
            f"未设置 {DSN_ENV}；P1-02 契约测试必须显式指向 qengine_sync 测试库。"
            "示例：postgresql://zhice:***@localhost:5446/zhice_qengine_sync_test"
        )
    schema_exists = False
    try:
        with psycopg2.connect(dsn) as conn:
            conn.set_session(readonly=True, autocommit=True)
            with conn.cursor() as cur:
                cur.execute(
                    "select 1 from information_schema.schemata where schema_name=%s",
                    (SCHEMA,),
                )
                schema_exists = cur.fetchone() is not None
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"{DSN_ENV} 指向的测试库不可连接: {exc}")
    if not schema_exists:
        pytest.fail(f"测试库缺少 schema {SCHEMA}")
    return dsn


@contextmanager
def _readonly_cursor(dsn: str):
    conn = psycopg2.connect(dsn)
    try:
        conn.set_session(readonly=True, autocommit=True)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"SET search_path TO {SCHEMA}, public")
            yield cur
    finally:
        conn.close()


def _count(cur, table: str) -> int:
    cur.execute(f"select count(*) n from {table}")
    return int(cur.fetchone()["n"])


def _latest_sync(cur) -> dict:
    cur.execute("select * from qg_sync_run order by finished_at desc limit 1")
    row = cur.fetchone()
    assert row, "qengine_sync 尚无同步批次"
    return dict(row)


def _patch_service_dsn(monkeypatch, dsn: str):
    from zhice_os_v2.application.services import qengine_sync_service as svc

    monkeypatch.setattr(svc, "_dsn", lambda: dsn)
    return svc


def test_contract_sync_health_is_green(contract_dsn, monkeypatch):
    svc = _patch_service_dsn(monkeypatch, contract_dsn)
    health = svc.fetch_health()
    run = health["sync"]
    checks = health["checks"]

    assert run["status"] == "success"
    assert "tables=19 pass=19" in (run.get("note") or "")
    assert run.get("source_commit")
    assert run.get("source_dirty") is False
    assert len(checks) == 19
    assert {c["status"] for c in checks} == {"PASS"}
    for c in checks:
        assert c["source_rows"] == c["target_rows"]
        assert c["source_sha256"] == c["target_sha256"]


def test_contract_snapshot_matches_database(contract_dsn, monkeypatch):
    svc = _patch_service_dsn(monkeypatch, contract_dsn)
    snapshot = svc.fetch_snapshot()
    with _readonly_cursor(contract_dsn) as cur:
        run = _latest_sync(cur)
        direct = {
            "strategy": _count(cur, "qg_strategy"),
            "pool_strategy": _count(cur, "qg_pool_strategy"),
            "day": _count(cur, "qg_day"),
            "pick": _count(cur, "qg_pick"),
            "trade": _count(cur, "qg_trade"),
            "pool_candidate": _count(cur, "qg_pool_candidate"),
            "issue": _count(cur, "qg_issue"),
            "pending": _count(cur, "qg_pending"),
        }
        cur.execute(
            "select count(*) n from qg_pending where signal_date=%s and vetoed=0",
            (run["asof"],),
        )
        direct["latest_picks"] = int(cur.fetchone()["n"])
        cur.execute("select * from qg_day order by signal_date")
        days = [dict(r) for r in cur.fetchall()]
        cur.execute("select * from qg_nav order by date")
        nav = [dict(r) for r in cur.fetchall()]

    assert snapshot["meta"]["sync_id"] == run["sync_id"]
    assert snapshot["meta"]["asof"] == str(run["asof"])
    assert snapshot["meta"]["source_commit"] == run["source_commit"]
    assert snapshot["meta"]["database"] == make_url(contract_dsn).database
    assert snapshot["meta"]["schema"] == SCHEMA
    assert snapshot["counts"] == direct
    assert len(snapshot["strategies"]) == direct["strategy"]
    assert len(snapshot["pool"]) == direct["pool_strategy"]
    assert len(snapshot["latest_picks"]) == direct["latest_picks"]
    assert len(snapshot["days"]) == min(5, len(days))
    assert snapshot["latest_day"]["signal_date"] == days[-1]["signal_date"]
    assert len(snapshot["nav"]) == len(nav)
    assert snapshot["latest_nav"]["date"] == nav[-1]["date"]
    assert len({s["sid"] for s in snapshot["strategies"]}) == direct["strategy"]
    assert {s["sid"] for s in snapshot["pool"]} <= {
        s["sid"] for s in snapshot["strategies"]
    }
    assert all(p["signal_date"] == str(run["asof"]) for p in snapshot["latest_picks"])
    assert all(int(p["vetoed"]) == 0 for p in snapshot["latest_picks"])
    # 快照查询合并后必须保持原有顶层字段，且 latest_pick_count /
    # latest_top_pick 由同一批 latest_picks 推导，值需与旧 SQL 语义一致。
    assert set(snapshot) == {
        "meta",
        "counts",
        "grade_counts",
        "lx_count",
        "nav",
        "latest_nav",
        "days",
        "latest_day",
        "pool",
        "pool_day",
        "strategies",
        "latest_picks",
        "issues",
        "detail",
    }
    expected_pick_count: dict[str, int] = {}
    expected_top_pick: dict[str, dict] = {}
    for pick in snapshot["latest_picks"]:
        sid = pick["sid"]
        expected_pick_count[sid] = expected_pick_count.get(sid, 0) + 1
        expected_top_pick[sid] = pick
    for strategy in snapshot["strategies"]:
        sid = strategy["sid"]
        assert strategy["latest_pick_count"] == expected_pick_count.get(sid, 0)
        if sid in expected_top_pick:
            top = strategy["latest_top_pick"]
            assert top is not None
            for key in (
                "sid",
                "ts_code",
                "name",
                "rank_in",
                "gap_pct",
                "stop_px",
                "ref_px",
                "score",
            ):
                assert top[key] == expected_top_pick[sid].get(key)
        else:
            assert strategy["latest_top_pick"] is None


def test_contract_strategy_detail_is_consistent(contract_dsn, monkeypatch):
    svc = _patch_service_dsn(monkeypatch, contract_dsn)
    detail = svc.fetch_strategy_detail("LX20")
    strategy = detail["strategy"]

    assert detail["sid"] == "LX20"
    assert strategy["sid"] == "LX20"
    assert strategy["in_pool"] is True
    assert detail["days"]
    assert detail["picks"]
    assert detail["trades"]
    assert detail["extremes"]["best"] is not None
    assert detail["extremes"]["worst"] is not None
    assert detail["extremes"]["best"]["ret_net_pct"] >= detail["extremes"]["worst"]["ret_net_pct"]

    with _readonly_cursor(contract_dsn) as cur:
        cur.execute(
            "select count(*) n from qg_strategy_day where sid='LX20'"
        )
        assert len(detail["days"]) == int(cur.fetchone()["n"])
        cur.execute("select count(*) n from qg_pick where sid='LX20'")
        pick_rows = int(cur.fetchone()["n"])
        cur.execute("select count(*) n from qg_trade where sid='LX20'")
        trade_rows = int(cur.fetchone()["n"])
    assert len(detail["picks"]) == min(16, pick_rows)
    assert len(detail["trades"]) == min(12, trade_rows)


def test_contract_pool_candidates_pagination(contract_dsn, monkeypatch):
    svc = _patch_service_dsn(monkeypatch, contract_dsn)
    first = svc.fetch_pool_candidates(limit=5, offset=0)
    second = svc.fetch_pool_candidates(limit=5, offset=5)

    assert first["asof"] == second["asof"]
    assert first["total"] == second["total"]
    assert first["total"] > 0
    assert len(first["rows"]) == 5
    assert len(second["rows"]) == 5
    assert {r["asof"] for r in first["rows"]} == {first["asof"]}
    assert {r["asof"] for r in second["rows"]} == {second["asof"]}
    assert [r["row_no"] for r in first["rows"]] != [r["row_no"] for r in second["rows"]]

    with _readonly_cursor(contract_dsn) as cur:
        cur.execute(
            "select count(*) n from qg_pool_candidate where asof=%s",
            (first["asof"],),
        )
        assert int(cur.fetchone()["n"]) == first["total"]


def test_contract_connection_pool_reuses_backend(contract_dsn, monkeypatch):
    """页面多次请求复用同一条只读连接，避免每次重连的 ~30ms 开销。"""
    svc = _patch_service_dsn(monkeypatch, contract_dsn)
    svc.close_pool()
    try:
        with svc._connection() as first:
            with first.cursor() as cur:
                cur.execute("select pg_backend_pid()")
                first_pid = cur.fetchone()[0]
        with svc._connection() as second:
            with second.cursor() as cur:
                cur.execute("select pg_backend_pid()")
                second_pid = cur.fetchone()[0]
        assert first_pid == second_pid
        assert svc._pool is not None
    finally:
        svc.close_pool()


def test_contract_api_reads_real_test_db(contract_dsn, monkeypatch, auth_headers):
    from fastapi.testclient import TestClient
    from zhice_os_v2.api.main import app

    _patch_service_dsn(monkeypatch, contract_dsn)
    with TestClient(app) as client:
        snapshot = client.get(
            "/api/v1/strategy-center/snapshot", headers=auth_headers
        )
        detail = client.get(
            "/api/v1/strategy-center/strategies/LX20", headers=auth_headers
        )
        health = client.get(
            "/api/v1/strategy-center/health", headers=auth_headers
        )
        candidates = client.get(
            "/api/v1/strategy-center/pool-candidates?limit=3",
            headers=auth_headers,
        )

    assert snapshot.status_code == 200
    body = snapshot.json()
    assert body["meta"]["asof"]
    assert body["counts"]["strategy"] > 0
    assert body["counts"]["latest_picks"] == len(body["latest_picks"])
    assert detail.status_code == 200
    assert detail.json()["strategy"]["in_pool"] is True
    assert health.status_code == 200
    assert health.json()["sync"]["status"] == "success"
    assert len(health.json()["checks"]) == 19
    assert candidates.status_code == 200
    assert candidates.json()["total"] > 0
    assert len(candidates.json()["rows"]) == 3
