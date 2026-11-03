# -*- coding: utf-8 -*-
"""`/api/d1/watch-plans` 引擎状态/过滤/armed 时效回归（WO-WATCH-API-20261104）。

离线：FakeHandler 直调 `portal/api_d1_handoff.py`；plan ledger 与
watch_engine/status.json 全部指向 tmp；不连服务、不写生产。

覆盖：
1) 保留既有字段（ok/schema_version/count/watch_plans/readonly/paper_only/executes）；
2) engine 块：status.json 存在→running/mode/age_s/stale；缺失/坏 JSON/非 object/超时→
   engine_state=unavailable 且 HTTP 200（不 500）；白名单字段，不泄 token/凭据；
3) status filter：armed/triggered/expired/cancelled；非法→400 bad_status；额外 query 忽略；
4) armed 列表附加 expires_in_s/stale（未来>0，过去<0 且 stale=True；缺失 expires_at→None/stale）；
5) envelope 不变：d1.watch_plans.list / watch_plan/v1 + validate_envelope 全绿；
6) 只读：executes=False、无执行字段、账本调用前后字节不变。
"""
from __future__ import annotations

import io
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "portal"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import api_d1_handoff as A  # noqa: E402
from agent_platform.contracts import ifs  # noqa: E402

REQUEST_ID_RE = re.compile(r"^req-[0-9a-f]{32}$")


class FakeHandler:
    """最小 Handler 替身：记录 _json(obj, code)（与既有 D1 envelope 测试同款）。"""

    def __init__(self, path="/", headers=None):
        self.path = path
        self.command = "GET"
        self.headers = dict(headers or {})
        self.rfile = io.BytesIO(b"")
        self.status = None
        self.payload = None

    def _json(self, obj, code=200):
        self.status = code
        self.payload = obj


def _get(path):
    return FakeHandler(path)


def _now():
    return datetime.now(timezone.utc).astimezone()


def _iso(dt):
    return dt.replace(microsecond=0).isoformat()


def _plan(plan_id, status, expires_delta_s=None, **extra):
    now = _now()
    row = {
        "schema": "watch_plan/v1",
        "plan_id": plan_id,
        "seat": "WA1",
        "method": "e2e_fixture",
        "conditions": {"symbol": "600519.SH", "action": "BUY", "price": 100.0},
        "expires_at": _iso(now + timedelta(seconds=expires_delta_s))
        if expires_delta_s is not None else "",
        "status": status,
        "armed_at": _iso(now - timedelta(seconds=60)) if status == "armed" else None,
        "created_at": _iso(now - timedelta(seconds=120)),
        "updated_at": _iso(now - timedelta(seconds=60)),
        "triggered_at": _iso(now - timedelta(seconds=30)) if status == "triggered" else None,
        "confirmation_id": "CFM-AAAAAAAAAAAA" if status == "triggered" else None,
        "paper_only": True,
        "not_a_trade": True,
        "source": {"receipt_id": "d1h-test", "code": "600519.SH",
                   "decision_id": "d1-test"},
    }
    row.update(extra)
    return row


def _write_ledger(path, plans):
    path.write_text("\n".join(json.dumps(p, ensure_ascii=False, sort_keys=True)
                              for p in plans) + "\n", encoding="utf-8")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    ledger = tmp_path / "watch_plans.jsonl"
    engine = tmp_path / "watch_engine" / "status.json"
    engine.parent.mkdir(parents=True, exist_ok=True)
    plans = [
        _plan("wp-test-armed-future", "armed", expires_delta_s=600),
        _plan("wp-test-armed-past", "armed", expires_delta_s=-60),
        _plan("wp-test-triggered", "triggered"),
        _plan("wp-test-expired", "expired"),
        _plan("wp-test-cancelled", "cancelled"),
    ]
    _write_ledger(ledger, plans)
    monkeypatch.setenv("D1_WATCH_PLAN_LEDGER", str(ledger))
    monkeypatch.setenv(A.WATCH_ENGINE_STATUS_ENV, str(engine))
    return {"ledger": ledger, "engine": engine, "plans": plans}


def _call(qs="", path="/api/d1/watch-plans"):
    h = _get(path)
    A.handle_get(h, qs)
    return h


def _engine_payload(**fields):
    return fields


# ── 1. 既有字段保留 + engine 缺失不 500 ─────────────────────────────
def test_existing_fields_preserved_with_engine_unavailable(env):
    h = _call("")
    assert h.status == 200
    p = h.payload
    assert p["ok"] is True
    assert p["schema_version"] == "watch_plan/v1"
    assert p["count"] == 5 and len(p["watch_plans"]) == 5
    assert p["readonly"] is True and p["paper_only"] is True
    assert p["executes"] is False
    assert p["status_filter"] is None
    engine = p["engine"]
    assert engine["engine_state"] == "unavailable"
    assert engine["status_file"]["exists"] is False
    assert "缺失" in engine["note"]


def test_engine_missing_file_returns_unavailable_not_500(env):
    h = _call("")
    assert h.status == 200
    assert h.payload["engine"]["engine_state"] == "unavailable"


def test_engine_bad_json_is_unavailable_not_500(env):
    env["engine"].write_text("{ this is not json", encoding="utf-8")
    h = _call("")
    assert h.status == 200
    engine = h.payload["engine"]
    assert engine["engine_state"] == "unavailable"
    assert "不可读" in engine["note"] or "非法 JSON" in engine["note"]


def test_engine_non_object_is_unavailable_not_500(env):
    env["engine"].write_text("[]", encoding="utf-8")
    h = _call("")
    assert h.status == 200
    assert h.payload["engine"]["engine_state"] == "unavailable"
    assert "顶层" in h.payload["engine"]["note"]


# ── 2. engine 块（真实 status.json） ────────────────────────────────
def test_engine_running_status_block(env):
    env["engine"].write_text(json.dumps({
        "state": "running",
        "mode": "paper",
        "pid": 4321,
        "started_at": _iso(_now() - timedelta(hours=1)),
        "last_tick_at": _iso(_now() - timedelta(seconds=5)),
        "tick_count": 120,
        "armed_count": 2,
        "token": "SECRET-TOKEN-X",
        "api_key": "SECRET-KEY-Y",
        "nested": {"password": "SECRET-Z"},
    }, ensure_ascii=False), encoding="utf-8")
    h = _call("")
    assert h.status == 200
    engine = h.payload["engine"]
    assert engine["engine_state"] == "running"
    assert engine["mode"] == "paper"
    assert engine["pid"] == 4321
    assert engine["tick_count"] == 120
    assert engine["armed_count"] == 2
    assert isinstance(engine["age_s"], int) and 0 <= engine["age_s"] <= 30
    assert engine["stale"] is False
    blob = json.dumps(h.payload, ensure_ascii=False)
    for secret in ("SECRET-TOKEN-X", "SECRET-KEY-Y", "SECRET-Z"):
        assert secret not in blob
    for bad_key in ('"token"', '"api_key"', '"password"', '"nested"'):
        assert bad_key not in json.dumps(engine, ensure_ascii=False)


def test_engine_stale_when_heartbeat_old(env):
    env["engine"].write_text(json.dumps({
        "state": "running",
        "last_tick_at": _iso(_now() - timedelta(seconds=600)),
    }), encoding="utf-8")
    h = _call("")
    engine = h.payload["engine"]
    assert engine["engine_state"] == "running"
    assert engine["age_s"] >= 590
    assert engine["stale"] is True


def test_engine_refreshes_between_requests(env):
    env["engine"].write_text(json.dumps({"state": "running", "last_tick_at": _iso(_now())}),
                             encoding="utf-8")
    assert _call("").payload["engine"]["engine_state"] == "running"
    env["engine"].write_text(json.dumps({"state": "stopped", "last_tick_at": _iso(_now())}),
                             encoding="utf-8")
    assert _call("").payload["engine"]["engine_state"] == "stopped"


# ── 3. status filter ───────────────────────────────────────────────
def test_status_filter_armed_decorates_expiry(env):
    h = _call("status=armed")
    assert h.status == 200
    p = h.payload
    assert p["count"] == 2 and p["status_filter"] == "armed"
    armed = {x["plan_id"]: x for x in p["watch_plans"]}
    assert set(armed) == {"wp-test-armed-future", "wp-test-armed-past"}
    future = armed["wp-test-armed-future"]
    past = armed["wp-test-armed-past"]
    assert future["status"] == "armed" and past["status"] == "armed"
    assert future["expires_in_s"] > 0 and future["stale"] is False
    assert past["expires_in_s"] < 0 and past["stale"] is True


@pytest.mark.parametrize("status", ["triggered", "expired", "cancelled"])
def test_status_filter_other_states(env, status):
    p = _call("status=%s" % status).payload
    assert p["count"] == 1
    assert p["watch_plans"][0]["status"] == status
    assert p["status_filter"] == status
    # 仅 armed 条目附加 expires_in_s/stale
    assert "expires_in_s" not in p["watch_plans"][0]
    assert "stale" not in p["watch_plans"][0]


def test_invalid_status_returns_400(env):
    h = _call("status=bogus")
    assert h.status == 400
    p = h.payload
    assert p["ok"] is False
    assert p["error"] == "bad_status"
    assert p["error_detail"]["code"] == "invalid_parameter"
    assert p["error_detail"]["details"]["legacy_code"] == "bad_status"
    assert REQUEST_ID_RE.match(p["request_id"])


def test_extra_query_params_ignored(env):
    h = _call("status=armed&foo=bar&limit=1&x=2")
    assert h.status == 200
    assert h.payload["count"] == 2


def test_status_filter_case_insensitive(env):
    p = _call("status=ARMED").payload
    assert p["count"] == 2 and p["status_filter"] == "armed"


# ── 4. 无过滤/armed 边界 ───────────────────────────────────────────
def test_no_filter_returns_all_only_armed_decorated(env):
    p = _call("").payload
    assert p["count"] == 5
    decorated = [x for x in p["watch_plans"] if "expires_in_s" in x]
    assert len(decorated) == 2
    assert all(x["status"] == "armed" for x in decorated)
    assert all("expires_in_s" not in x for x in p["watch_plans"]
               if x["status"] != "armed")


def test_armed_without_expires_at_marked_stale(env):
    plans = list(env["plans"])
    plans.append(_plan("wp-test-armed-no-expiry", "armed", expires_delta_s=None))
    _write_ledger(env["ledger"], plans)
    p = _call("status=armed").payload
    item = next(x for x in p["watch_plans"] if x["plan_id"] == "wp-test-armed-no-expiry")
    assert item["expires_in_s"] is None
    assert item["stale"] is True


# ── 5. envelope 不变 ───────────────────────────────────────────────
def test_envelope_unchanged_watch_plans_list(env):
    env["engine"].write_text(json.dumps({"state": "running", "last_tick_at": _iso(_now())}),
                             encoding="utf-8")
    h = _call("")
    assert h.status == 200
    p = h.payload
    # IFS-03 envelope 字段
    for key in ("ok", "schema_version", "request_id", "source", "data", "warnings"):
        assert key in p, "缺 envelope 字段: %s" % key
    assert REQUEST_ID_RE.match(p["request_id"])
    assert p["warnings"] == []
    assert p["source"]["interface_id"] == "d1.watch_plans.list"
    assert p["schema_version"] == "watch_plan/v1"
    assert ifs.validate_envelope(p) == []
    # 旧顶层字段 + data 副本一致（兼容期）
    assert p["data"]["count"] == p["count"] == 5
    assert p["data"]["watch_plans"] == p["watch_plans"]
    assert p["data"]["engine"] == p["engine"]
    assert p["data"]["executes"] is False


# ── 6. 只读/无凭据/账本零写入 ───────────────────────────────────────
def test_readonly_and_no_execution_fields(env):
    before = env["ledger"].read_bytes()
    h = _call("status=armed")
    assert h.status == 200
    assert h.payload["executes"] is False
    assert h.payload["readonly"] is True
    assert h.payload["paper_only"] is True
    blob = json.dumps(h.payload, ensure_ascii=False)
    for key in ('"order"', '"order_id"', '"qty"', '"quantity"', '"broker"',
                '"fill"', '"account"', '"execute"'):
        assert key not in blob
    for secret in ("Bearer", "password", "SECRET", "token"):
        assert secret not in blob
    assert env["ledger"].read_bytes() == before, "GET 不应写 watch plan 账本"


def test_default_engine_path_when_env_unset(env, monkeypatch):
    monkeypatch.delenv(A.WATCH_ENGINE_STATUS_ENV, raising=False)
    h = _call("")
    assert h.status == 200
    engine = h.payload["engine"]
    assert engine["source"].endswith("agent_platform/data/watch_engine/status.json")
    assert isinstance(engine["engine_state"], str) and engine["engine_state"]
