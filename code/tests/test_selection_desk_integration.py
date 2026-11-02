"""选股中台核心集成测试（请求运行中的 8102 API）。

运行前提：后端 http://127.0.0.1:8102 可用（可用 SELECTION_DESK_BASE 覆盖，
如本机临时入口 http://127.0.0.2:8102）。
覆盖：数据日/日报池子、候选溯源、下游反馈、模拟下单、权限，
以及 P2-2：日报按市场返回（不再塞 MULTI）与跨市场提交按市场拆分。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid

import pytest

BASE = os.environ.get("SELECTION_DESK_BASE", "http://127.0.0.1:8102").rstrip("/") + "/api/v1"


def _req(method: str, path: str, body: dict | None = None, token: str | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def _delete_test_selection_rows() -> None:
    """清理 P2-2 集成测试写入的日报/选股/池子（2026-08-01/02 两个无生产数据的日期）。"""
    try:
        import sqlalchemy as sa

        from zhice_os_v2.config.settings import get_settings
        e = sa.create_engine(get_settings().db_main_url.replace(
            "postgresql://", "postgresql+psycopg2://"))
        with e.begin() as c:
            c.execute(sa.text(
                "DELETE FROM v2_selection_pick WHERE strategy_id LIKE 'it_sel_%'"))
            c.execute(sa.text(
                "DELETE FROM v2_selection_report WHERE strategy_id LIKE 'it_sel_%'"))
            c.execute(sa.text(
                "DELETE FROM v2_selection_pool WHERE pool_date IN "
                "('2026-08-01','2026-08-02') AND code LIKE 'TESTSEL-%'"))
    except Exception:
        pass


@pytest.fixture(scope="module")
def token():
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=3) as r:
            if r.status != 200:
                pytest.skip("8102 后端不可用")
    except Exception:
        pytest.skip("8102 后端不可用")
    u = "it_" + uuid.uuid4().hex[:10]
    code, data = _req("POST", "/auth/register", {
        "username": u, "email": u + "@example.com", "password": "Test123456!",
    })
    if code not in (200, 201) or "token" not in data:
        pytest.skip("测试用户注册失败: %s %s" % (code, data))
    return data["token"]


@pytest.fixture(scope="module", autouse=True)
def cleanup_test_rows():
    """模块结束后清理测试写入的反馈/日报/模拟账户，避免污染生产库。"""
    yield
    _delete_test_selection_rows()
    try:
        import sqlalchemy as sa
        from zhice_os_v2.config.settings import get_settings
        e = sa.create_engine(get_settings().db_main_url.replace("postgresql://", "postgresql+psycopg2://"))
        with e.begin() as c:
            c.execute(sa.text("DELETE FROM v2_selection_feedback WHERE code='TEST_INT' AND reason='集成测试'"))
            c.execute(sa.text("DELETE FROM v2_sim_order WHERE strategy_id LIKE 'it_sim_%'"))
            c.execute(sa.text("DELETE FROM v2_sim_position WHERE strategy_id LIKE 'it_sim_%'"))
            c.execute(sa.text("DELETE FROM v2_sim_account WHERE strategy_id LIKE 'it_sim_%'"))
    except Exception:
        pass


def test_data_date_reports_pool(token):
    code, data = _req("GET", "/selection-desk", token=token)
    assert code == 200
    assert data["date"] == "2026-10-30"
    assert len(data["reports"]) >= 3
    assert len(data["pool"]) > 0


def test_candidate_trace(token):
    code, data = _req("GET", "/selection-desk", token=token)
    assert code == 200
    details = data["pool_detail"]
    assert details
    first = details[0]
    assert first["code"]
    assert isinstance(first["sources"], list) and first["sources"]


def test_market_filtered_reports(token):
    """P2-2：按市场返回该市场独立日报，绝不出现 MULTI。"""
    for market in ("CN", "HK", "US", "CRYPTO"):
        code, data = _req("GET", "/selection-desk?market=" + market, token=token)
        assert code == 200
        assert data["market"] == market
        assert data["reports"], "{market} 应有日报（含平台适配器）"
        assert all(r["market"] == market for r in data["reports"])
        assert all(r["market"] != "MULTI" for r in data["reports"])
        # 池子同样按市场返回；池日可能晚于本市场日报日（如美股数据日滞后）
        assert data["pool"], "{market} 应有统一池"
        assert all(p["market"] == market for p in data["pool"])
        assert all(d["market"] == market for d in data["pool_detail"])
        assert data["pool_date"]
    # 各市场数据日可不同：US 最新日报 2026-10-29（池日 2026-10-30），其余 2026-10-30
    _, us = _req("GET", "/selection-desk?market=US", token=token)
    assert us["date"] == "2026-10-29"
    assert us["pool_date"] == "2026-10-30"


def test_market_invalid_rejected(token):
    code, _ = _req("GET", "/selection-desk?market=MULTI", token=token)
    assert code == 400
    code, _ = _req("GET", "/selection-desk?market=XX", token=token)
    assert code == 400


def test_cross_market_report_split_by_market(token):
    """P2-2：sidecar 一次提交跨市场选股 → 后端拆成 4 条按市场日报。"""
    sid = "it_sel_" + uuid.uuid4().hex[:8]
    report_date = "2026-08-01"
    body = {
        "report_date": report_date,
        "strategy_id": sid,
        "analyst": "集成测试",
        "market_view": "跨市场提交",
        "picks": [
            {"code": "TESTSEL-CN", "name": "测试A股", "signal_type": "买点",
             "score": 80, "market": "CN"},
            {"code": "TESTSEL-HK", "name": "测试港股", "signal_type": "买点",
             "score": 81, "market": "HK"},
            {"code": "TESTSEL-US", "name": "测试美股", "signal_type": "买点",
             "score": 82, "market": "US"},
            {"code": "TESTSEL-CRYPTO", "name": "测试加密", "signal_type": "买点",
             "score": 83, "market": "CRYPTO"},
        ],
    }
    try:
        code, result = _req("POST", "/selection-desk/report", body, token)
        assert code == 200
        assert sorted(result["markets"]) == ["CN", "CRYPTO", "HK", "US"]
        assert result["report_id"] is None  # 跨市场提交没有单一 report_id

        for market in ("CN", "HK", "US", "CRYPTO"):
            code, data = _req(
                "GET", "/selection-desk?date_str=%s&market=%s" % (report_date, market), token=token)
            assert code == 200
            rows = [r for r in data["reports"] if r["strategy_id"] == sid]
            assert len(rows) == 1, (market, rows)
            assert rows[0]["market"] == market
            assert rows[0]["picks"] == 1
        # 不指定市场时同一天同策略返回 4 条，无 MULTI
        code, data = _req("GET", "/selection-desk?date_str=" + report_date, token=token)
        assert code == 200
        rows = [r for r in data["reports"] if r["strategy_id"] == sid]
        assert len(rows) == 4
        assert all(r["market"] != "MULTI" for r in rows)
    finally:
        _delete_test_selection_rows()


def test_feedback(token):
    date_str = "2026-10-30"
    code, _ = _req("POST", "/selection-desk/feedback", {
        "pool_date": date_str, "code": "TEST_INT", "consumer": "测试",
        "decision": "不用", "reason": "集成测试", "result": "忽略",
    }, token)
    assert code == 200
    code, data = _req("GET", "/selection-desk", token=token)
    assert code == 200
    assert any(x["code"] == "TEST_INT" for x in data["feedback"])


def test_sim_manual_order(token):
    sid = "it_sim_" + uuid.uuid4().hex[:8]
    code, _ = _req("POST", "/sim-factor/account", {
        "strategy_id": sid, "initial_capital": 100000, "base_position_pct": 40,
    }, token)
    assert code == 200
    code, _ = _req("POST", "/sim-factor/order", {
        "strategy_id": sid, "trade_date": "2026-10-30", "code": "TEST",
        "name": "测试", "side": "buy", "price": 10.0, "qty": 100, "reason": "集成测试",
    }, token)
    assert code == 200
    code, pf = _req("GET", "/sim-factor/portfolio?strategy_id=" + sid, token=token)
    assert code == 200
    assert pf["positions"][0]["qty"] == 100


def test_permission_denied_without_token():
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=3) as r:
            if r.status != 200:
                pytest.skip("8102 后端不可用")
    except Exception:
        pytest.skip("8102 后端不可用")
    code, _ = _req("GET", "/selection-desk")
    assert code in (401, 403)
