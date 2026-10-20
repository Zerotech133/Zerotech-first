# -*- coding: utf-8 -*-
"""客户面订阅端点安全加固回归（WO-FS-101 · P3.7-lite R1）。

覆盖 /api/subscription/status 的三条加固契约，全部离线自包含：
subscription_store 用内存桩替换（不连真实库、不建库文件），HTTP 探针只打
127.0.0.1:0 随机高位端口（绝不触碰 8080/8091，不重启任何在役服务）。

1.  字段最小化：响应只含白名单键，无 open_id 明文、无 display_name /
    reason / generated_at / plan 目录 / 金额类字段；
2.  open_id 掩码格式（前 8 位 + **，过短降级，空值 **）；
3.  可选 key 闸：PORTAL_SUB_STATUS_KEY 未配置 → 行为与基线一致（放行）；
    配置后缺失/错 key → 401 unauthorized，正确 key → 200；
4.  open_id 缺失 → 400 missing open_id；
5.  上游 error 细节不外泄（归并为 store_unavailable）；
6.  页面仍读得 plan_id / plan_name / start / end / status 四字段并渲染，
    身份只展示掩码，含「本页不展示金额信息」文案；
7.  该端点不接收任何写参数（静态断言 + 探针：查询串里的写字段被忽略）。
"""

import http.server
import json
import os
import re
import sys
import threading
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
PAGE = HERE / "static" / "my_subscription.html"
API = HERE / "portal_api.py"

# 必须在 import portal_api 之前：跳过启动预热线程（官方测试钩子）
os.environ.setdefault("PORTAL_NO_WARMUP", "1")
os.environ.setdefault("PORTAL_API_TOKEN", "wo-fs-101-test-token")
for p in (str(HERE), str(REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)

import portal_api  # noqa: E402


# ---------------------------------------------------------------------------
# subscription_store 内存桩（端点内部 `import subscription_store` 命中此处）
# ---------------------------------------------------------------------------

CALLS = []          # 每次摘要调用的 open_id（只读取值，写路径不存在）
RAW_OPEN_ID = "ou_1234567890abcdef"
RAW_SUMMARY = {
    "open_id": RAW_OPEN_ID,
    "display_name": "张三",
    "subscriptions": [
        {"plan_id": "pro", "start": "2026-10-01", "end": "2026-10-31",
         "status": "active"},
        {"plan_id": "trial", "start": "2026-09-01", "end": "2026-09-30",
         "status": "expired"},
    ],
    "today_used_by_prefix": {"quote": 3, "screen": 2},
    "daily_quota_by_plan": {"pro": 50, "trial": 5},
    "plan_name_by_id": {"pro": "专业版", "trial": "试用版"},
    "reason": "subscription_active",
    "generated_at": "2026-10-20T10:00:00",
    # 金额类字段（客户面禁止透出）
    "amount_due": 199.0,
    "price_cny": 199,
}


def _stub_summary(open_id, now=None):
    CALLS.append(open_id)
    if open_id == "ou_boom":
        return {"error": "store_unavailable: sqlite disk I/O error"}
    data = json.loads(json.dumps(RAW_SUMMARY))
    data["open_id"] = open_id
    return data


def _install_stub_store():
    mod = types.ModuleType("subscription_store")
    mod.get_subscriber_summary = _stub_summary
    sys.modules["subscription_store"] = mod


# 收编修补（2026-10-20 13:45）：原实现在**模块级**调用 _install_stub_store()，
# 导致 pytest 收集期就把 sys.modules["subscription_store"] 换成只含
# get_subscriber_summary 的桩且永不恢复 —— 单文件单跑绿，全量跑时后续任何
# 依赖真模块（from subscription_store import STATUS_ACTIVE 等）的文件在
# 收集期拿到桩即 AttributeError。改为 module 级 autouse fixture 内安装、
# 退出时还原原模块；收集期不再有任何全局副作用。
@pytest.fixture(scope="module", autouse=True)
def _stub_store():
    saved = sys.modules.get("subscription_store")
    _install_stub_store()
    try:
        yield
    finally:
        if saved is not None:
            sys.modules["subscription_store"] = saved
        else:
            sys.modules.pop("subscription_store", None)


# ---------------------------------------------------------------------------
# 隔离 HTTP 探针（127.0.0.1:0，单文件内唯一实例）
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def base_url(_stub_store, tmp_path_factory):
    portal_api._ACCESS_LOG = str(tmp_path_factory.mktemp("log") / "access.log")
    srv = http.server.HTTPServer(("127.0.0.1", 0), portal_api.Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        yield "http://127.0.0.1:%d" % srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


@pytest.fixture(autouse=True)
def _no_key_env(monkeypatch):
    """默认清空 key 闸环境变量：只有显式 setenv 的用例才开闸。"""
    monkeypatch.delenv("PORTAL_SUB_STATUS_KEY", raising=False)
    CALLS.clear()


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=8) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:  # 4xx 也要读到 body
        return e.code, json.loads(e.read().decode("utf-8"))


WHITELIST = {"ok", "open_id", "plan_id", "plan_name", "start", "end",
             "status", "today_used_by_prefix", "daily_quota_by_plan"}
FORBIDDEN = {"display_name", "reason", "generated_at", "plan_name_by_id",
             "subscriptions", "amount_due", "price_cny", "amount", "price"}


def test_1_view_whitelist_and_no_plaintext(base_url):
    """响应只含白名单键；无 open_id 明文、无内部/金额字段。"""
    code, body = _get(base_url + "/api/subscription/status?open_id=" + RAW_OPEN_ID)
    assert code == 200, body
    assert set(body) == WHITELIST, "响应键不在白名单内: %s" % (set(body) ^ WHITELIST)
    assert not (set(body) & FORBIDDEN), "透出了禁止字段: %s" % (set(body) & FORBIDDEN)
    assert RAW_OPEN_ID not in json.dumps(body, ensure_ascii=False), "open_id 明文外泄"
    assert body["open_id"] != RAW_OPEN_ID
    assert body["plan_id"] == "pro" and body["plan_name"] == "专业版"
    assert body["start"] == "2026-10-01" and body["end"] == "2026-10-31"
    assert body["status"] == "active"


def test_2_open_id_masked_format(base_url):
    """掩码格式：前 8 位 + **，且不以明文结尾。"""
    code, body = _get(base_url + "/api/subscription/status?open_id=" + RAW_OPEN_ID)
    assert code == 200
    assert body["open_id"] == "ou_12345**", body["open_id"]
    assert body["open_id"].endswith("**") and len(body["open_id"]) <= 10
    assert RAW_OPEN_ID[8:] not in body["open_id"]


def test_3_mask_open_id_short_ids():
    """掩码纯函数边界：>8 前8+**；5~8 前4+**；1~4 前2+**；空 **。"""
    assert portal_api.mask_open_id("ou_1234567890") == "ou_12345**"
    assert portal_api.mask_open_id("ou_1234") == "ou_1**"
    assert portal_api.mask_open_id("ou_1") == "ou**"
    assert portal_api.mask_open_id("") == "**"
    assert portal_api.mask_open_id(None) == "**"


def test_4_gate_disabled_behaves_like_baseline(base_url):
    """未配置 PORTAL_SUB_STATUS_KEY：无 key 头也放行，四个字段取自账本基线。"""
    assert os.environ.get("PORTAL_SUB_STATUS_KEY") is None
    assert portal_api._sub_status_key_ok(None) is True
    assert portal_api._sub_status_key_ok("") is True
    assert portal_api._sub_status_key_ok("anything") is True
    code, body = _get(base_url + "/api/subscription/status?open_id=" + RAW_OPEN_ID)
    assert code == 200
    base = RAW_SUMMARY["subscriptions"][0]
    for k in ("plan_id", "start", "end", "status"):
        assert body[k] == base[k], "字段 %s 与基线不一致" % k


def test_5_gate_wrong_or_missing_key_401(base_url, monkeypatch):
    """配置 key 后：缺失/错 key → 401 unauthorized，且不回任何账本字段。"""
    monkeypatch.setenv("PORTAL_SUB_STATUS_KEY", "s3cret-key")
    for hdrs in ({}, {"X-Portal-Key": "wrong"}, {"X-Portal-Key": ""}):
        code, body = _get(base_url + "/api/subscription/status?open_id=" + RAW_OPEN_ID,
                          hdrs)
        assert code == 401, (hdrs, code, body)
        assert body == {"ok": False, "error": "unauthorized"}
    assert CALLS == [], "401 必须在触碰订阅账本之前短路"


def test_6_gate_right_key_passes(base_url, monkeypatch):
    """配置 key 后：正确 X-Portal-Key → 200，字段最小化照旧生效。"""
    monkeypatch.setenv("PORTAL_SUB_STATUS_KEY", "s3cret-key")
    code, body = _get(base_url + "/api/subscription/status?open_id=" + RAW_OPEN_ID,
                      {"X-Portal-Key": "s3cret-key"})
    assert code == 200, body
    assert set(body) == WHITELIST
    assert RAW_OPEN_ID not in json.dumps(body, ensure_ascii=False)
    assert CALLS == [RAW_OPEN_ID]


def test_7_missing_open_id_400(base_url):
    """open_id 缺失/空白 → 400 missing open_id（不读账本）。"""
    for q in ("", "?open_id=", "?open_id=%20%20"):
        code, body = _get(base_url + "/api/subscription/status" + q)
        assert code == 400, (q, code, body)
        assert body == {"ok": False, "error": "missing open_id"}
    assert CALLS == []


def test_8_page_reads_four_fields_and_masks_identity():
    """页面仍读白名单字段即可渲染；身份只展示掩码。"""
    src = PAGE.read_text(encoding="utf-8")
    for k in ("sum.plan_id", "sum.plan_name", "sum.start", "sum.end",
              "sum.status"):
        assert k in src, "页面缺字段消费: %s" % k
    assert "sum.open_id" in src, "页面应展示掩码身份"
    assert "掩码" in src
    # 已从响应移除的字段不得再被页面依赖（否则最小化后渲染必断）
    for gone in ("sum.display_name", "sum.reason", "sum.generated_at",
                 "sum.subscriptions", "sum.plan_name_by_id"):
        assert gone not in src, "页面仍依赖已移除字段: %s" % gone


def test_9_page_declares_no_amount_and_no_plaintext():
    """页面文案：不展示金额、购买/续费走飞书；无 open_id 明文回显。"""
    src = PAGE.read_text(encoding="utf-8")
    assert "本页不展示金额信息，购买/续费请通过飞书联系" in src
    assert "POST" not in src
    assert not re.search(r"<form\b", src, re.I)
    assert "method:'GET'" in src or 'method:"GET"' in src


def test_10_endpoint_takes_no_write_params(base_url):
    """静态：branch 无请求体读取/无写函数；探针：查询串写字段被忽略。"""
    src = API.read_text(encoding="utf-8")
    m = re.search(
        r'path == "/api/subscription/status".*?(?=\n        elif path == )',
        src, re.S)
    assert m, "status branch 结构异常"
    block = m.group(0)
    for banned in ("rfile", "Content-Length", "upsert_subscriber",
                   "grant_subscription", "expire_sweep", "entitlement_log",
                   "do_POST"):
        assert banned not in block, "branch 出现写路径痕迹: %s" % banned
    assert "401" in block and "unauthorized" in block, "缺 key 闸 401"
    assert block.index("_sub_status_key_ok") < block.index("missing open_id"), \
        "key 闸必须先于 open_id 解析"
    assert "_sub_status_view" in block, "响应必须经字段最小化"
    assert "POST" not in block

    code, body = _get(base_url + "/api/subscription/status?open_id=" + RAW_OPEN_ID
                      + "&plan_id=evil&status=expired&action=grant&amount=0")
    assert code == 200, body
    assert body["plan_id"] == "pro" and body["status"] == "active", \
        "查询串写字段污染了响应"
    assert CALLS == [RAW_OPEN_ID], "只应有只读摘要调用"


def test_11_upstream_error_details_not_leaked(base_url):
    """上游 error 文本不外泄：统一 store_unavailable。"""
    code, body = _get(base_url + "/api/subscription/status?open_id=ou_boom")
    assert code == 200, body
    assert body == {"error": "store_unavailable"}
    assert "sqlite" not in json.dumps(body)
    assert portal_api._sub_status_view({"error": "boom: secret dsn"}) == \
        {"error": "store_unavailable"}
