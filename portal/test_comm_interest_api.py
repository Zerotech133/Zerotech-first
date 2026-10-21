# -*- coding: utf-8 -*-
"""`/api/comm/interest` 商机信号只读端点测试（K4.2 展示端，WO-FS-148-9）。

沿 test_sub_admin_list_security 判例：临时库 + 随机高位端口探针，
零真实库、零服务重启、零真发。覆盖：
1. 未配 key → auth_mode=none，照实自述；
2. 配 key 后错/缺 header → 401 且不读画像库；
3. 正 key → 200；
4. 空态 → signals=[] 不造数；
5. 有数据 → 热客信号出现且字段完整（open_id 掩码）；
6. ?open_id= 单客统计；
7. 只读（无写参数入口）。
"""
import http.server
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parent
sys.path.insert(0, str(PORTAL))
os.environ.setdefault("PORTAL_NO_WARMUP", "1")

import portal_api  # noqa: E402
import comm_interest as ci  # noqa: E402

KEY = "comm-int-key-7b21"


@pytest.fixture(scope="module")
def base_url():
    srv = http.server.HTTPServer(("127.0.0.1", 0), portal_api.Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        yield "http://127.0.0.1:%d" % srv.server_address[1]
    finally:
        srv.shutdown()
        th.join(timeout=5)
        srv.server_close()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("PORTAL_SUB_ADMIN_KEY", raising=False)


@pytest.fixture
def db(tmp_path, monkeypatch):
    p = tmp_path / "comm_interest.sqlite3"
    monkeypatch.setenv("COMM_INTEREST_DB", str(p))
    ci.init_schema()
    return p


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


# ── 鉴权 ────────────────────────────────────
def test_01_auth_mode_none_when_no_key(base_url, db):
    st, body = _get(base_url + "/api/comm/interest")
    assert st == 200
    assert body["ok"] is True
    assert body["auth_mode"] == "none"


def test_02_wrong_key_401(base_url, db, monkeypatch):
    monkeypatch.setenv("PORTAL_SUB_ADMIN_KEY", KEY)
    st, body = _get(base_url + "/api/comm/interest",
                    {"X-Portal-Key": "nope"})
    assert st == 401 and body["error"] == "unauthorized"


def test_03_missing_key_header_401(base_url, db, monkeypatch):
    monkeypatch.setenv("PORTAL_SUB_ADMIN_KEY", KEY)
    st, body = _get(base_url + "/api/comm/interest")
    assert st == 401


def test_04_correct_key_200(base_url, db, monkeypatch):
    monkeypatch.setenv("PORTAL_SUB_ADMIN_KEY", KEY)
    st, body = _get(base_url + "/api/comm/interest", {"X-Portal-Key": KEY})
    assert st == 200 and body["auth_mode"] == "key"


# ── 空态与数据 ───────────────────────────────
def test_05_empty_state_no_fabrication(base_url, db):
    st, body = _get(base_url + "/api/comm/interest")
    assert st == 200
    assert body["signals"] == []
    assert body["count"] == 0


def test_06_hot_signal_rendered(base_url, db):
    for _ in range(5):
        ci.record("ou_hot_api", intent="stock_consult", code="605100",
                  blocked=True)
    st, body = _get(base_url + "/api/comm/interest")
    assert st == 200
    row = [s for s in body["signals"] if s["open_id"] == "ou_hot_api"][0]
    assert row["level"] == "hot"
    assert row["stars"] == 3
    assert row["hint"]
    assert row["open_id_masked"] != "ou_hot_api", "对外须掩码"
    assert "605100" in row["top_codes"]


def test_07_single_customer_stats(base_url, db):
    ci.record("ou_one", intent="stock_consult", code="300750")
    ci.record("ou_one", intent="subscription_qa")
    st, body = _get(base_url + "/api/comm/interest?open_id=ou_one")
    assert st == 200
    assert body["stats"]["consult_count"] == 2
    assert "300750" in body["stats"]["top_codes"]


def test_08_single_customer_guide_text(base_url, db):
    for _ in range(3):
        ci.record("ou_g", intent="stock_consult", code="600519")
    st, body = _get(base_url + "/api/comm/interest?open_id=ou_g")
    assert "3 次" in (body.get("guide") or "")


def test_09_unknown_customer_empty_stats(base_url, db):
    st, body = _get(base_url + "/api/comm/interest?open_id=ou_nobody")
    assert st == 200
    assert body["stats"]["consult_count"] == 0


def test_10_read_only_no_write_params(base_url, db):
    """POST 不支持（只读端点）。"""
    req = urllib.request.Request(base_url + "/api/comm/interest",
                                 data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as r:
            code = r.status
    except urllib.error.HTTPError as exc:
        code = exc.code
    assert code in (404, 405), "只读端点不应接受 POST"
