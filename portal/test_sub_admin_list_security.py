# -*- coding: utf-8 -*-
"""`/api/subscription/list` 鉴权加固离线安全测试（WO-FS-112 · P3.8-lite M-B）。

沿 test_subscription_list.py / test_my_subscription_security.py 判例，但**不往
sys.modules 注入桩**（P3.7-lite 判例：模块级注入会污染全量测试）。本文件改用
临时目录 + `SUBSCRIPTION_DB` 环境变量指向临时 sqlite，HTTP 探针只打
127.0.0.1:0 随机高位端口（绝不触碰 8080/8091，不重启任何在役服务，不连真实库）。

覆盖：
1.  未配置 PORTAL_SUB_ADMIN_KEY → 响应与基线（store 原样返回）逐字段一致；
2.  未配置时 ?limit= 钳制语义不变（0/-5→1，非整数→默认 50，超大→上界）；
3.  未配置时 auth_mode 必须为 "none"（裸奔运行可见，不可省略）；
4.  配置 key 后错 key → 401 unauthorized；
5.  配置 key 后缺 header / 空 header → 401；
6.  配置 key 后正确 key → 200 且 auth_mode == "key"；
7.  401 响应体不含任何订阅行数据（独立断言）；
8.  401 响应体不含期望密钥内容（含其前后缀）；
9.  401 在触碰订阅账本之前短路（账本缺失也先回 unauthorized）；
10. 掩码规则未被误用到 list（open_id 仍为明文，钉住本决策）；
11. store 异常 → 稳定错误结构，不泄露 traceback / sqlite 细节；
12. 页面鉴权状态条与「open_id 明文、不得外传」文案；
13. 静态锚：常量时间比较、key 闸先于 list_subscribers、401 字面量。
"""

import http.server
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parent
sys.path.insert(0, str(PORTAL))

# 测试环境跳过启动预热（portal_api 模块级 daemon 线程，冷算约百秒）
os.environ.setdefault("PORTAL_NO_WARMUP", "1")

import portal_api  # noqa: E402
import subscription_store as ss  # noqa: E402

API = PORTAL / "portal_api.py"
PAGE = PORTAL / "static" / "sub_admin.html"

RAW_OPEN_ID = "ou_adminlist_plain_0001"
RAW_NAME = "张三"
KEY = "adm-s3cret-key-9f2c"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 夹具：临时库 + 随机端口探针（零真实库、零服务重启）
# ---------------------------------------------------------------------------

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
    """默认两把钥匙都不配：只有显式 setenv 的用例才开闸。"""
    monkeypatch.delenv("PORTAL_SUB_ADMIN_KEY", raising=False)
    monkeypatch.delenv("PORTAL_SUB_STATUS_KEY", raising=False)


@pytest.fixture
def db(tmp_path, monkeypatch):
    """SUBSCRIPTION_DB 锚到 tmp 临时文件，播种 3 行订阅者。"""
    p = tmp_path / "sub_admin_list.sqlite3"
    monkeypatch.setenv("SUBSCRIPTION_DB", str(p))
    monkeypatch.delenv("SUBSCRIPTION_OWN_DOMAIN", raising=False)
    ss._connect(create=True).close()
    ss.upsert_subscriber(RAW_OPEN_ID, RAW_NAME)
    ss.grant_subscription(RAW_OPEN_ID, "basic_monthly", 30)
    ss.upsert_subscriber("ou_b2", "乙二")
    ss.upsert_subscriber("ou_c3", "丙三")
    return p


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _body_text(body):
    return json.dumps(body, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 1~3 未配置 key：零破坏 + 鉴权状态自述
# ---------------------------------------------------------------------------

def test_01_gate_off_payload_equals_baseline(base_url, db):
    """未配置 key：载荷与 store 原样返回逐字段一致，仅追加 auth_mode。"""
    code, body = _get(base_url + "/api/subscription/list")
    assert code == 200, body
    assert body.get("auth_mode") == "none"
    got = dict(body)
    got.pop("auth_mode")
    assert got == ss.list_subscribers(limit=50)


def test_02_gate_off_limit_clamp_unchanged(base_url, db):
    """未配置 key：?limit= 钳制语义与基线一致（0/-5→1，abc→50，999→上界）。"""
    cases = {"0": 1, "-5": 1, "abc": 3, "999": 3, "1": 1}
    for raw, want in cases.items():
        code, body = _get(base_url + "/api/subscription/list?limit=" + raw)
        assert code == 200, (raw, body)
        assert body["count"] == want, (raw, body)
        assert len(body["subscribers"]) == want, (raw, body)
        # 与 store 基线逐字段一致（含越界回落默认 50 的口径）
        base = ss.list_subscribers(
            limit=(int(raw) if raw.lstrip("-").isdigit() else 50))
        assert body["subscribers"] == base["subscribers"], raw


def test_03_gate_off_auth_mode_is_none(base_url, db):
    """未配置 key：auth_mode 必须照实为 none（不可省略、不可冒充 key）。"""
    code, body = _get(base_url + "/api/subscription/list?limit=1")
    assert code == 200, body
    assert "auth_mode" in body, "auth_mode 是必出字段，不得可选"
    assert body["auth_mode"] == "none"
    assert body["auth_mode"] != "key"
    # 未配置时任意 X-Portal-Key 也不改变行为（不设闸）
    code2, body2 = _get(base_url + "/api/subscription/list?limit=1",
                        {"X-Portal-Key": "whatever"})
    assert code2 == 200 and body2["auth_mode"] == "none"


# ---------------------------------------------------------------------------
# 4~6 配置 key：401 / 放行
# ---------------------------------------------------------------------------

def test_04_wrong_key_401(base_url, db, monkeypatch):
    """配置 key 后错 key → 401 unauthorized。"""
    monkeypatch.setenv("PORTAL_SUB_ADMIN_KEY", KEY)
    code, body = _get(base_url + "/api/subscription/list",
                      {"X-Portal-Key": KEY + "x"})
    assert code == 401, (code, body)
    assert body.get("error") == "unauthorized"


def test_05_missing_or_empty_header_401(base_url, db, monkeypatch):
    """配置 key 后缺 header / 空 header → 401，且不落到 200。"""
    monkeypatch.setenv("PORTAL_SUB_ADMIN_KEY", KEY)
    for hdrs in ({}, {"X-Portal-Key": ""}, {"X-Portal-Key": "   "}):
        code, body = _get(base_url + "/api/subscription/list", hdrs)
        assert code == 401, (hdrs, code, body)
        assert body.get("error") == "unauthorized"


def test_06_right_key_passes_with_auth_mode_key(base_url, db, monkeypatch):
    """配置 key 后正确 key → 200，auth_mode == "key"，行数照常。"""
    monkeypatch.setenv("PORTAL_SUB_ADMIN_KEY", KEY)
    code, body = _get(base_url + "/api/subscription/list?limit=200",
                      {"X-Portal-Key": KEY})
    assert code == 200, body
    assert body["auth_mode"] == "key"
    assert body["ok"] is True and body["count"] == 3
    assert RAW_OPEN_ID in [s["open_id"] for s in body["subscribers"]]


# ---------------------------------------------------------------------------
# 7~8 401 响应体不含行数据、不含密钥
# ---------------------------------------------------------------------------

def test_07_401_body_has_no_subscriber_rows(base_url, db, monkeypatch):
    """401 响应体：零行数据（open_id / 显示名 / 档位 / 额度均不出现）。"""
    monkeypatch.setenv("PORTAL_SUB_ADMIN_KEY", KEY)
    code, body = _get(base_url + "/api/subscription/list?limit=200",
                      {"X-Portal-Key": "nope"})
    assert code == 401
    text = _body_text(body)
    for needle in (RAW_OPEN_ID, RAW_NAME, "ou_b2", "乙二", "ou_c3",
                   "basic_monthly", "used_today", "display_name",
                   "subscribers", "plan_id"):
        assert needle not in text, "401 泄露行数据字段: %s" % needle
    assert set(body) <= {"ok", "error"}


def test_08_401_body_has_no_key_material(base_url, db, monkeypatch):
    """401 响应体：不含期望密钥原文，也不含其任何前后缀片段。"""
    monkeypatch.setenv("PORTAL_SUB_ADMIN_KEY", KEY)
    code, body = _get(base_url + "/api/subscription/list")
    assert code == 401
    text = _body_text(body)
    assert KEY not in text
    for frag in (KEY[:8], KEY[-8:], KEY[:4], "adm-s3cret", "9f2c"):
        assert frag not in text, "401 泄露密钥片段: %s" % frag
    assert "X-Portal-Key" not in text


# ---------------------------------------------------------------------------
# 9 store 短路口径 + 10 不掩码决策
# ---------------------------------------------------------------------------

def test_09_401_short_circuits_before_store(base_url, tmp_path, monkeypatch):
    """key 错时即使账本缺失也只回 unauthorized（证明闸先于读账本）；
    带对 key 才暴露稳定错误结构。"""
    monkeypatch.setenv("SUBSCRIPTION_DB",
                       str(tmp_path / "no_such_store.sqlite3"))
    monkeypatch.setenv("PORTAL_SUB_ADMIN_KEY", KEY)
    code, body = _get(base_url + "/api/subscription/list",
                      {"X-Portal-Key": "bad"})
    assert code == 401, (code, body)
    assert body.get("error") == "unauthorized"
    assert "store_unavailable" not in _body_text(body)
    code2, body2 = _get(base_url + "/api/subscription/list",
                        {"X-Portal-Key": KEY})
    assert code2 == 200, body2
    assert body2.get("ok") is False
    assert body2.get("error") == "store_unavailable"
    assert body2.get("auth_mode") == "key"


def test_10_open_id_not_masked_on_list(base_url, db):
    """掩码规则不得误用到 list：open_id 仍为明文（钉住「不掩码」决策）。"""
    code, body = _get(base_url + "/api/subscription/list")
    assert code == 200, body
    row = [s for s in body["subscribers"] if s["open_id"] == RAW_OPEN_ID][0]
    assert row["display_name"] == RAW_NAME
    assert row["open_id"] != portal_api.mask_open_id(RAW_OPEN_ID)
    assert "**" not in row["open_id"]
    # 静态锚：list 段不得调用客户面掩码/白名单函数
    src = _read(API)
    m = re.search(r'path == "/api/subscription/list".*?(?=\n        elif path == )',
                  src, re.S)
    assert m, "list branch 结构异常"
    block = m.group(0)
    assert "mask_open_id" not in block
    assert "_sub_status_view" not in block


# ---------------------------------------------------------------------------
# 11 store 异常：稳定结构、不漏 traceback
# ---------------------------------------------------------------------------

def test_11_store_broken_stable_error_no_traceback(base_url, tmp_path,
                                                   monkeypatch):
    """store 损坏 → 稳定 {ok:false,error:store_unavailable}，无堆栈细节。"""
    bad = tmp_path / "broken.sqlite3"
    bad.write_bytes(b"not a sqlite file at all")
    monkeypatch.setenv("SUBSCRIPTION_DB", str(bad))
    code, body = _get(base_url + "/api/subscription/list")
    assert code == 200, body
    assert body == {"ok": False, "error": "store_unavailable",
                    "auth_mode": "none"}
    text = _body_text(body)
    for junk in ("Traceback", "sqlite3", "OperationalError", "not a database",
                 "file is not"):
        assert junk not in text, "错误细节外泄: %s" % junk


# ---------------------------------------------------------------------------
# 12~13 页面与静态锚
# ---------------------------------------------------------------------------

def test_12_page_auth_strip_and_plaintext_warning():
    """页面：鉴权状态条三态文案 + open_id 明文/不得外传提示；既有字段依赖不破。"""
    src = _read(PAGE)
    assert 'id="m-auth"' in src
    assert "已开启（key）" in src
    assert "未开启（任何人可读，仅限内网）" in src
    assert "鉴权状态未知" in src
    assert "auth_mode" in src
    assert "open_id 明文" in src
    assert "属内部域页面，不得对外分享链接" in src
    # 既有渲染字段依赖仍在
    for dep in ("s.open_id", "s.display_name", "s.plan_id", "s.status",
                "s.end", "s.used_today", "open-id-input"):
        assert dep in src, "页面既有字段依赖被破坏: %s" % dep
    assert "POST" not in src


def test_13_branch_static_gate_before_store():
    """静态锚：常量时间比较、key 闸先于 list_subscribers、401 字面量。"""
    block = re.search(
        r'path == "/api/subscription/list".*?(?=\n        elif path == )',
        _read(API), re.S).group(0)
    assert "PORTAL_SUB_ADMIN_KEY" in block
    assert "X-Portal-Key" in block
    assert "hmac.compare_digest" in block
    assert "401" in block and '"unauthorized"' in block
    assert block.index("hmac.compare_digest") < block.index("list_subscribers"), \
        "key 闸必须在读账本之前"
    assert "_limit = 50" in block, "limit 默认值语义不得丢"
    for banned in ("upsert_subscriber", "grant_subscription",
                   "expire_sweep", "entitlement_log"):
        assert banned not in block
