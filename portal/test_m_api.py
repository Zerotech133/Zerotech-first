# -*- coding: utf-8 -*-
"""P1 移动端接口单测：令牌→scene 数据、picks 每票短链、埋点校验、HTTP 适配桩。"""

import json
import os
import tempfile
import shutil
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import m_api as ma  # noqa: E402
import m_tokens as mt  # noqa: E402


@pytest.fixture()
def ws_tmp():
    """沙箱内 temp 目录 ACL 不可写 → 用 workspace 下的临时目录。"""
    root = os.path.join(
        os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(),
        "aios_test_tmp",
        uuid.uuid4().hex,
    )
    os.makedirs(root, exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(autouse=True)
def _tmp_secret(monkeypatch, ws_tmp):
    monkeypatch.setenv("M_LINK_SECRET_FILE", os.path.join(ws_tmp, "secret.txt"))
    monkeypatch.setattr(mt, "SECRET_FILE", os.path.join(ws_tmp, "secret.txt"))
    monkeypatch.setenv("M_BASE_URL", "http://test.local:8080")


def test_picks_payload_and_links():
    d = ma.picks_payload()
    assert d["ok"] and d["scene"] == "picks"
    assert isinstance(d["stocks"], list) and d["total"] >= 1
    s0 = d["stocks"][0]
    assert s0["code"] and s0["name"]
    assert s0["link"].startswith("http://test.local:8080/m/chan?t=")
    tok = s0["link"].split("t=", 1)[1].split("&")[0]
    p = mt.verify(tok, scope="page")
    assert p["scene"] == "chan" and p["code"] == s0["code"]


def test_chan_payload_resolves_exchange_suffix():
    d = ma.chan_payload("605100")
    assert d["ok"] and d["scene"] == "chan"
    assert d["code"].startswith("605100")
    assert d["chanlun"]["dzg"] is not None
    assert isinstance(d["decision"], list) and len(d["decision"]) >= 4
    assert d["stale"] in (True, False)


def test_chan_payload_unknown_code():
    d = ma.chan_payload("999999")
    assert d["ok"] is False and d["status"] == "facts_missing"


def test_chan_payload_chart(monkeypatch):
    """缠论页补图：三周期 structure → chart.periods/bars/pivots/signals。"""
    struct = {
        "ok": True,
        "bars": [
            {"dt": "2026-10-20", "o": 10, "h": 11, "l": 9.5, "c": 10.5, "v": 100},
            {"dt": "2026-10-21", "o": 10.5, "h": 11.2, "l": 10.1, "c": 11, "v": 120},
        ],
        "pivots": [
            {
                "s_dt": "2026-10-20",
                "e_dt": "2026-10-21",
                "zg": 11.2,
                "zd": 9.5,
                "n_bi": 3,
            }
        ],
        "bis": [
            {
                "s_dt": "2026-10-20",
                "e_dt": "2026-10-21",
                "s_price": 9.5,
                "e_price": 11.2,
                "dir": 1,
            }
        ],
        "signals": [
            {
                "type": "3买",
                "dt": "2026-10-21",
                "price": 11,
                "note": "test",
                "bei": True,
                "fv": [1, 2],
                "mt": True,
                "mtv": [1],
                "reg": "bull",
                "reg_ev": [1, 2, 3],
            }
        ],
    }

    def fake_get(path, timeout=15):
        if "chanlun/structure" in path:
            return 200, struct
        return 0, {"ok": False}

    monkeypatch.setattr(ma, "_local_get", fake_get)
    d = ma.chan_payload("605100")
    assert d["ok"] and isinstance(d["chart"], dict)
    ch = d["chart"]
    assert ch["default"] == "1d"
    assert set(ch["periods"]) == {"1d", "30m", "60m"}
    one = ch["periods"]["1d"]
    assert len(one["bars"]) == 2 and one["bars"][0]["v"] == 100
    assert one["pivots"][0]["zg"] == 11.2
    sig = one["signals"][0]
    assert sig["type"] == "3买" and sig["bei"] is True
    assert sig["mt"] is True and sig["reg"] == "bull"
    # 每周期都应有数据（fake 对任意 period 返回同一份）
    assert ch["periods"]["30m"]["signals"][0]["type"] == "3买"
    # 盯盘口径校正：行情价/涨跌以日线最新 bar 为准；事实包时间另存
    assert d["price"] == 11 and d["ts"] == "2026-10-21"
    assert d["quote_src"] == "日线K线" and d["facts_ts"]
    assert round(d["pct"], 2) == 4.76
    # 中枢取图数据最新 pivot（11.2/9.5），状态按现价 11 落在区间内
    assert d["chanlun"]["dzg"] == 11.2 and d["chanlun"]["dzd"] == 9.5
    assert d["chanlun"]["pivot_state"] == "中枢震荡"


def test_position_payload_shape():
    d = ma.position_payload()
    assert d["ok"] and d["scene"] == "position"
    assert d["total"] and d["positions"]
    p = d["positions"][0]
    for k in ("code", "name", "qty", "cost", "price", "pnl_pct"):
        assert k in p


def test_notify_payload_shape():
    d = ma.notify_payload()
    assert d["ok"] and d["scene"] == "notify"
    assert d["total"] >= 1 and isinstance(d["recent"], list)


def test_ops_payload_shape():
    d = ma.ops_payload()
    assert d["ok"] and d["scene"] == "ops"
    assert set(d["ports"]) == {"8097", "8091", "9104"}
    assert isinstance(d["bots"], list) and d["bots"]


def test_page_url_default_ttl_and_scope():
    url, payload = ma.page_url("picks")
    assert url.startswith("http://test.local:8080/m/picks?t=")
    assert payload["scope"] == "page" and payload["exp"] > 0


def test_page_url_unknown_scene():
    with pytest.raises(ma.ApiError):
        ma.page_url("nope")


def test_build_entry_lists_scenes():
    d = ma.build("entry")
    assert d["ok"] and len(d["links"]) >= 7


def test_beacon_validate_ok():
    clean = ma.validate_events(
        [
            {"event": "m_page_view", "scene": "picks", "from": "feishu"},
            {"event": "m_action", "scene": "picks", "action": "refresh"},
        ]
    )
    assert isinstance(clean, list) and len(clean) == 2
    assert clean[0]["event"] == "m_page_view"


def test_beacon_rejects_unknown_and_missing():
    with pytest.raises(ma.ApiError):
        ma.validate_events([{"event": "m_hack", "scene": "picks"}])
    with pytest.raises(ma.ApiError):
        ma.validate_events([{"event": "m_page_view"}])
    with pytest.raises(ma.ApiError):
        ma.validate_events([])


class _H:
    def __init__(self, body=None, token_ok=True, headers=None, payload=None):
        self._body = json.dumps(body or {}).encode("utf-8")
        self.headers = {"Content-Length": str(len(self._body))}
        self.headers.update(headers or {})
        self._token_ok = token_ok
        self.payload = payload
        self.sent = None
        self.rfile = self

    def read(self, n):
        return self._body[:n]

    def _check_portal_token(self):
        return self._token_ok

    def _json(self, obj, code=200):
        self.sent = (code, obj)


def test_handle_get_health():
    h = _H()
    ma.handle_get(h, "/api/m/health", "")
    assert h.sent[0] == 200 and h.sent[1]["ok"]


def test_handle_get_data_with_token():
    url, payload = ma.page_url("position")
    tok = url.split("t=", 1)[1].split("&")[0]
    h = _H()
    ma.handle_get(h, "/api/m/data", "t=" + tok)
    assert h.sent[0] == 200 and h.sent[1]["scene"] == "position"


def test_handle_get_data_bad_token():
    h = _H()
    ma.handle_get(h, "/api/m/data", "t=bad")
    assert h.sent[0] == 401


def test_handle_post_link_requires_auth():
    h = _H(body={"scene": "picks"}, token_ok=False)
    ma.handle_post(h, "/api/m/link")
    assert h.sent[0] == 403
    h2 = _H(body={"scene": "picks"}, token_ok=True)
    ma.handle_post(h2, "/api/m/link")
    assert h2.sent[0] == 200 and "/m/picks?t=" in h2.sent[1]["url"]


def _session_cookie(open_id):
    import time as _t

    now = int(_t.time())
    tok = mt.sign(
        {"scope": "session", "open_id": open_id, "nbf": now, "exp": now + 3600}
    )
    return "m_sess=" + tok


def test_ticket_page_token_cannot_reply(monkeypatch):
    """查看工单的页面令牌 + 无飞书会话：失败关闭，且绝不进入发送链。"""
    import ticket_loop

    monkeypatch.setattr(
        ticket_loop, "reply", lambda *a, **kw: pytest.fail("reply must not run")
    )
    token, _ = mt.make_page_token("ticket", id="lg-test")
    h = _H(body={"t": token, "text": "伪答复"})
    ma.handle_post(h, "/api/m/ticket/reply")
    assert h.sent[0] == 401
    assert "登录" in h.sent[1]["error"]


def test_ticket_reply_wrong_identity_403(monkeypatch):
    """有飞书会话但不是处理人名单成员：403，且不进发送链。"""
    import ticket_loop

    monkeypatch.setattr(
        ticket_loop, "reply", lambda *a, **kw: pytest.fail("reply must not run")
    )
    monkeypatch.setattr(ma, "_handler_open_ids", lambda: ["ou_owner"])
    token, _ = mt.make_page_token("ticket", id="lg-test")
    h = _H(
        body={"t": token, "text": "伪答复"},
        headers={"Cookie": _session_cookie("ou_other")},
    )
    ma.handle_post(h, "/api/m/ticket/reply")
    assert h.sent[0] == 403
    assert "无权" in h.sent[1]["error"]


def test_ticket_reply_no_handler_roster_fails_closed(monkeypatch):
    """处理人名单不可读/为空：失败关闭（403），不静默放行。"""
    import ticket_loop

    monkeypatch.setattr(
        ticket_loop, "reply", lambda *a, **kw: pytest.fail("reply must not run")
    )
    monkeypatch.setattr(ma, "_handler_open_ids", lambda: [])
    token, _ = mt.make_page_token("ticket", id="lg-test")
    h = _H(
        body={"t": token, "text": "伪答复"},
        headers={"Cookie": _session_cookie("ou_owner")},
    )
    ma.handle_post(h, "/api/m/ticket/reply")
    assert h.sent[0] == 403


def test_ticket_reply_handler_identity_passes(monkeypatch):
    """处理人（飞书会话 open_id ∈ 名单）+ 工单页面令牌 → 才允许答复并留身份痕迹。"""
    import ticket_loop

    seen = {}

    def fake_reply(tid, text, by="handler"):
        seen.update({"tid": tid, "text": text, "by": by})
        return {"ok": True, "sent": True, "why": ""}

    monkeypatch.setattr(ticket_loop, "reply", fake_reply)
    monkeypatch.setattr(ma, "_handler_open_ids", lambda: ["ou_owner"])
    token, _ = mt.make_page_token("ticket", id="lg-test")
    h = _H(
        body={"t": token, "text": "已处理"},
        headers={"Cookie": _session_cookie("ou_owner")},
    )
    ma.handle_post(h, "/api/m/ticket/reply")
    assert h.sent[0] == 200 and h.sent[1]["ok"] is True
    assert seen["tid"] == "lg-test" and seen["text"] == "已处理"
    assert seen["by"].startswith("处理人·ou_ow")


def test_handler_open_ids_env_union(monkeypatch):
    monkeypatch.setenv("COMM_OWNER_OPEN_ID", "ou_a, ou_b")
    ids = ma._handler_open_ids()
    assert "ou_a" in ids and "ou_b" in ids


def test_ticket_payload_can_reply_flag(monkeypatch):
    fake = {
        "ticket": {"dispatch_id": "lg-5917d4c4357b", "state": "inbox"},
        "timeline": [],
    }
    monkeypatch.setattr(ma, "_local_get", lambda p, timeout=15: (200, fake))
    import types

    mod = types.ModuleType("ticket_loop")
    mod.summary = lambda tid: {"ok": True, "replies": [], "can_nudge": True}
    monkeypatch.setitem(__import__("sys").modules, "ticket_loop", mod)
    monkeypatch.setattr(ma, "_handler_open_ids", lambda: ["ou_owner"])
    owner = ma.ticket_payload("lg-5917d4c4357b", open_id="ou_owner")
    assert owner["can_reply"] is True and owner["authed"] is True
    cust = ma.ticket_payload("lg-5917d4c4357b", open_id="ou_customer")
    assert cust["can_reply"] is False and cust["reply_hint"]
    anon = ma.ticket_payload("lg-5917d4c4357b")
    assert anon["can_reply"] is False
    via_build = ma.build("ticket", id="lg-5917d4c4357b", open_id="ou_owner")
    assert via_build["can_reply"] is True


def test_ticket_reply_rejects_wrong_scene(monkeypatch):
    import ticket_loop

    monkeypatch.setattr(
        ticket_loop, "reply", lambda *a, **kw: pytest.fail("reply must not run")
    )
    token, _ = mt.make_page_token("picks", id="lg-test")
    h = _H(body={"t": token, "text": "伪答复"})
    ma.handle_post(h, "/api/m/ticket/reply")
    assert h.sent[0] == 400


def test_handle_post_beacon(monkeypatch, ws_tmp):
    monkeypatch.setattr(ma, "EVENTS", os.path.join(ws_tmp, "events.jsonl"))
    h = _H(body={"events": [{"event": "m_page_view", "scene": "picks"}]})
    ma.handle_post(h, "/api/m/beacon")
    assert h.sent[0] == 200 and h.sent[1]["accepted"] == 1
    assert os.path.exists(ma.EVENTS)


def test_beacon_st_triggers_share_visit(monkeypatch, ws_tmp):
    """m_page_view 带 st → 调 _st_visit 落归因账（失败静默）。"""
    calls = []
    monkeypatch.setattr(ma, "_st_visit", lambda tok: calls.append(tok) or True)
    monkeypatch.setattr(ma, "EVENTS", os.path.join(ws_tmp, "events.jsonl"))
    n = ma.append_events(
        [{"event": "m_page_view", "scene": "picks", "st": "st.abc.def"}]
    )
    assert n == 1 and calls == ["st.abc.def"]
    # 无 st / 非法前缀不调用
    ma.append_events([{"event": "m_page_view", "scene": "picks", "st": "junk"}])
    assert calls == ["st.abc.def"]


def test_st_visit_records_visit(monkeypatch, ws_tmp):
    """本地验签 st 并落 share_tokens.jsonl（与 share_tokens 同构）。"""
    import base64
    import hashlib
    import hmac
    import json as _json
    import time as _time

    monkeypatch.setattr(ma, "WORK", ws_tmp)
    secret = "a" * 64
    with open(os.path.join(ws_tmp, "share_secret.json"), "w", encoding="utf-8") as f:
        _json.dump({"secret": secret}, f)
    payload = {
        "jti": "abcd1234",
        "code": "",
        "kind": "test",
        "tenant": "default",
        "iat": int(_time.time()),
        "exp": int(_time.time()) + 3600,
    }
    body = base64.urlsafe_b64encode(
        _json.dumps(payload, separators=(",", ":")).encode()
    ).rstrip(b"=")
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()[:16]
    tok = "st." + body.decode() + "." + sig
    assert ma._st_visit(tok) is True
    flow = os.path.join(ws_tmp, "share_tokens.jsonl")
    rows = [json.loads(x) for x in open(flow, encoding="utf-8")]
    assert rows[-1]["evt"] == "visit" and rows[-1]["jti"] == "abcd1234"
    # 坏签名不落账
    assert ma._st_visit(tok[:-2] + "ff") is False


def test_beacon_spec_includes_st():
    spec = ma._beacon_spec()
    assert "st" in spec["m_page_view"]["fields"]


# ── P2：订阅面 + /s/ 外部分享（过期/越权/未登记）────────────
def test_subscription_payload_unbound():
    d = ma.subscription_payload()
    assert d["ok"] and d["state"] == "unbound" and d["items"] == []


def test_subscription_payload_with_open_id():
    d = ma.subscription_payload("ou_50a95455c7dffec1090ade400886d0d4")
    assert d["ok"] and d["scene"] == "subscription"
    assert d["state"] in ("ok", "empty")
    assert d["open_id_masked"].endswith("**") and len(d["open_id_masked"]) < 12


def test_share_target_roundtrip():
    tok, payload = mt.make_share_token("picks")
    resource, url = ma.share_target(tok)
    assert resource == "picks" and "/m/picks?t=" in url
    p = mt.verify(url.split("t=", 1)[1].split("&")[0], scope="page")
    assert p["scene"] == "picks"


def test_share_target_expired():
    tok, _ = mt.make_share_token("picks", ttl=-10)
    with pytest.raises(mt.TokenError):
        ma.share_target(tok)


def test_share_target_forbidden_scene():
    tok, _ = mt.make_share_token("ops")
    with pytest.raises(ma.ApiError) as ei:
        ma.share_target(tok)
    assert ei.value.status == 403


class _ShareH(_H):
    def send_response(self, code):
        self.response_code = code
        self.headers_sent = {}

    def send_header(self, k, v):
        self.headers_sent[k] = v

    def end_headers(self):
        self.ended = True


def test_handle_share_valid_redirect():
    h = _ShareH()
    tok, _ = mt.make_share_token("picks")
    ma.handle_share(h, "/s/" + tok)
    assert h.response_code == 302
    assert "/m/picks?t=" in h.headers_sent["Location"]


def test_handle_share_invalid():
    h = _ShareH()
    ma.handle_share(h, "/s/bad-token")
    assert h.sent[0] == 401


def test_handle_post_share_requires_auth_and_signs():
    h = _H(body={"resource": "picks"}, token_ok=False)
    ma.handle_post(h, "/api/m/share")
    assert h.sent[0] == 403
    h2 = _H(body={"resource": "picks"}, token_ok=True)
    ma.handle_post(h2, "/api/m/share")
    assert h2.sent[0] == 200 and "/s/" in h2.sent[1]["url"]
    p = mt.verify(h2.sent[1]["url"].split("/s/", 1)[1], scope="share")
    assert p["resource"] == "picks"


def test_data_payload_has_nav_links():
    url, _ = ma.page_url("position")
    tok = url.split("t=", 1)[1].split("&")[0]
    h = _H()
    ma.handle_get(h, "/api/m/data", "t=" + tok)
    assert h.sent[0] == 200
    nav = h.sent[1].get("nav") or {}
    for s in (
        "entry",
        "picks",
        "position",
        "notify",
        "subscription",
        "daily",
        "ledger",
        "lineage",
    ):
        assert nav.get(s, "").startswith("http://test.local:8080/m/")


# ── 全量对接：日报/账本/血缘/工单/统一入口 ───────────────────
def test_daily_payload(monkeypatch):
    fake = {
        "dist": {
            "total": 813,
            "bias": {"bull": 106, "bear": 68, "neutral": 639},
            "tier_buckets": {"strong": 212, "weak": 364, "unknown": 237},
            "event_type": {"earnings": 170, "equity": 81},
        },
        "generated_at": "2026-10-25T10:00:00+08:00",
        "drift": {
            "open_count": 3,
            "alerts": [
                {"title": "漂移", "message": "第一行原因\n详细内容", "status": "OPEN"}
            ],
        },
        "bt": {
            "exists": True,
            "n": 100,
            "news_day_range": ["a", "b"],
            "groups": {
                "bull": {"pct1_avg": -1.2, "vs_mkt1": -0.3},
                "bear": {"pct1_avg": -1.0, "vs_mkt1": 0.1},
            },
        },
        "health": {
            "items": [
                {"name": "a", "exists": True},
                {"name": "b", "exists": False, "path": "C:/x"},
            ]
        },
    }
    monkeypatch.setattr(ma, "_local_get", lambda p, timeout=15: (200, fake))
    d = ma.daily_payload()
    assert d["ok"] and d["total"] == 813
    assert d["level"] == "warn"
    assert d["generated_at"] == "2026-10-25 10:00"
    assert d["events"][0]["label"] == "财报"
    assert d["events"][0]["pct"] == round(170 / 813 * 100, 1)
    assert d["bias_pct"]["bull"] == round(106 / 813 * 100, 1)
    assert d["alerts"][0]["short"] == "第一行原因"
    assert d["bt"]["bull_avg1"] == -1.2
    assert d["health_total"] == 2 and len(d["health_bad"]) == 1
    assert "中性" in d["headline"]


def test_ledger_payload_real():
    d = ma.ledger_payload()
    assert d["ok"] and d["summary"].get("open_n") is not None
    assert isinstance(d["open"], list)


def test_lineage_payload(monkeypatch):
    fake = {
        "view": "breaks",
        "data": {
            "tracked_tables": 20,
            "healthy_count": 20,
            "runs_not_ok": 20,
            "broken": [],
            "freshness": {
                "fresh_count": 18,
                "watch": [{"table": "neo4j:X", "age_trade_days": 2}],
                "stale": [],
                "broken_count": 0,
            },
            "all_latest": [
                {
                    "output_table": "neo4j:X",
                    "job": "j",
                    "status": "ok",
                    "freshness": "watch",
                    "age_trade_days": 2,
                    "rows_written": 80,
                },
                {
                    "output_table": "neo4j:HAS_CANDLE",
                    "job": "j",
                    "status": "ok",
                    "freshness": "fresh",
                    "age_trade_days": 0,
                    "rows_written": 10,
                },
            ],
        },
    }
    monkeypatch.setattr(ma, "_local_get", lambda p, timeout=15: (200, fake))
    d = ma.lineage_payload()
    assert d["ok"] and d["tracked"] == 20
    assert d["level"] == "warn" and d["watch_n"] == 1
    assert d["rows"][0]["table"] == "neo4j:X"  # 偏旧排前
    assert d["rows"][0]["label"] == "X"
    assert d["rows"][1]["label"] == "K线关系"
    assert d["history_fail_runs"] == 20  # 历史累计，非当前异常
    assert d["attention"][0]["level"] == "warn"
    assert "偏旧" in d["status_line"]


def test_ticket_payload_ok(monkeypatch):
    fake = {
        "ok": True,
        "ticket": {
            "dispatch_id": "lg-5917d4c4357b",
            "state": "inbox",
            "kind_label": "派单请求",
            "intent_label": "个股咨询",
            "code": "605100",
            "route_desc": "司马懿席",
            "deadline_min": 5,
            "submitted_at": "2026-10-21T16:29:24",
            "open_id_masked": "ou_50a**d4",
        },
        "timeline": [{"label": "已提交", "time": "2026-10-21T16:29:24"}],
        "next_hint": "预计 5 分钟",
    }
    monkeypatch.setattr(ma, "_local_get", lambda p, timeout=15: (200, fake))
    d = ma.ticket_payload("lg-5917d4c4357b")
    assert d["ok"] and d["ticket"]["code"] == "605100" and d["timeline"]


def test_ticket_payload_bad_id():
    d = ma.ticket_payload("bad")
    assert d["ok"] is False and d["state"] == "bad_id"


def test_ticket_payload_not_found(monkeypatch):
    monkeypatch.setattr(ma, "_local_get", lambda p, timeout=15: (404, {"ok": False}))
    d = ma.ticket_payload("lg-abcdef123456")
    assert d["ok"] is False and d["state"] == "not_found"


# ── 订阅页飞书免登 ────────────────────────────────────────
class _CookieH:
    def __init__(self, cookie=""):
        self.headers = {"Cookie": cookie} if cookie else {}
        self.sent = None

    def _json(self, obj, code=200):
        self.sent = (code, obj)


def test_session_cookie_roundtrip(monkeypatch, ws_tmp):
    monkeypatch.setenv("M_LINK_SECRET_FILE", os.path.join(ws_tmp, "s.txt"))
    monkeypatch.setattr(mt, "SECRET_FILE", os.path.join(ws_tmp, "s.txt"))
    tok = mt.sign(
        {"scope": "session", "open_id": "ou_abc123", "nbf": 0, "exp": 9999999999}
    )
    h = _CookieH("a=1; m_sess=" + tok + "; b=2")
    assert ma._session_open_id(h) == "ou_abc123"
    assert ma._session_open_id(_CookieH("")) == ""
    bad = _CookieH("m_sess=bad.token.xx")
    assert ma._session_open_id(bad) == ""


def test_auth_check(monkeypatch, ws_tmp):
    monkeypatch.setenv("M_LINK_SECRET_FILE", os.path.join(ws_tmp, "s2.txt"))
    monkeypatch.setattr(mt, "SECRET_FILE", os.path.join(ws_tmp, "s2.txt"))
    h = _CookieH("")
    ma.auth_check(h)
    assert h.sent[0] == 200 and h.sent[1]["authed"] is False
    tok = mt.sign(
        {"scope": "session", "open_id": "ou_abc123", "nbf": 0, "exp": 9999999999}
    )
    h2 = _CookieH("m_sess=" + tok)
    ma.auth_check(h2)
    assert h2.sent[1]["authed"] is True
    assert h2.sent[1]["open_id_masked"] == "ou_abc**"


class _RedirectH:
    def __init__(self):
        self.code = None
        self.headers_sent = {}

    def send_response(self, code):
        self.code = code

    def send_header(self, k, v):
        self.headers_sent[k] = v

    def end_headers(self):
        pass


def test_auth_start_redirect(monkeypatch):
    monkeypatch.setattr(ma, "_feishu_app_token", lambda: ("apptok", "cli_test"))
    monkeypatch.setattr(ma, "base_url", lambda: "https://m.example.com")
    h = _RedirectH()
    ma.auth_start(h, "t=tok123")
    assert h.code == 302
    loc = h.headers_sent["Location"]
    assert "open.feishu.cn/open-apis/authen/v1/index" in loc
    assert "app_id=cli_test" in loc
    assert "state=" in loc


def test_entry_payload_links():
    d = ma.entry_payload()
    assert d["ok"] and len(d["links"]) >= 10
    scenes = {x["scene"] for x in d["links"]}
    assert {
        "picks",
        "chan",
        "position",
        "daily",
        "ledger",
        "lineage",
        "ticket",
        "notify",
        "subscription",
    } <= scenes
    for x in d["links"]:
        assert "/m/" in x["url"] and "t=" in x["url"]


def test_ticket_payload_loop_fields(monkeypatch):
    fake = {
        "ticket": {
            "dispatch_id": "lg-5917d4c4357b",
            "state": "inbox",
            "kind_label": "派单请求",
        },
        "timeline": [],
        "next_hint": "预计 5 分钟",
    }
    monkeypatch.setattr(ma, "_local_get", lambda p, timeout=15: (200, fake))
    import types

    mod = types.ModuleType("ticket_loop")
    mod.summary = lambda tid: {
        "ok": True,
        "state": "inbox",
        "waited_min": 3.2,
        "nudge_n": 2,
        "last_nudge": "2026-10-25 16:00",
        "escalate": True,
        "replies": [{"text": "已跟进", "by": "seat"}],
        "can_nudge": True,
    }
    monkeypatch.setitem(__import__("sys").modules, "ticket_loop", mod)
    d = ma.ticket_payload("lg-5917d4c4357b")
    assert d["ok"] is True
    assert d["nudge_n"] == 2 and d["escalate"] is True
    assert d["waited_min"] == 3.2
    assert d["replies"][0]["text"] == "已跟进"
    assert d["can_nudge"] is True
