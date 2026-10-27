# -*- coding: utf-8 -*-
"""test_api_zhuge_adoption.py — C3 契约测试：POST /api/zhuge/adoption 路由接线。

工单 WO-LEGION-ZHUGE-ADOPTROUTE-20261027（只做路由接线，排程归 L2）。
覆盖：
- 路由 200：合法 items 追加到 **tmp 账本**（autouse 隔离 LEDGER，真实
  portal/data/zhuge_adoption.jsonl 零触碰）；
- 400：非数组/空数组/字段越界/超 200 条/at·domain 必填缺失/非法 JSON；
- 413：正文超 64KiB 不读正文不落盘；负数 Content-Length 400；
- GET /api/zhuge/adoption 无读语义 → 404 兜底（测试锁定）；
- 重复/多条 POST 追加行数正确（append-only，不覆盖）。

路由验证不依赖真实 8091 进程：沿 test_api_zhuge_route 判例，
object.__new__(portal_api.Handler) 绕过 BaseHTTPRequestHandler.__init__ +
stub 子类捕获 _json 输出。运行：python -m pytest test_api_zhuge_adoption.py -q
"""

from __future__ import annotations

import importlib
import io
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

ROUTE_PATH = "/api/zhuge/adoption"
REAL_LEDGER = HERE / "data" / "zhuge_adoption.jsonl"  # 只做隔离断言，绝不写入


def _load_module(name):
    return importlib.import_module(name)


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    """每例把模块 LEDGER 指到 tmp_path：真实 data 账本零触碰（硬隔离）。"""
    mod = _load_module("api_zhuge_adoption")
    path = tmp_path / "data" / "zhuge_adoption.jsonl"
    monkeypatch.setattr(mod, "LEDGER", str(path))
    assert path.resolve() != REAL_LEDGER.resolve(), "测试账本必须是 tmp，禁止真实 data"
    return path


def _make_handler():
    """构造 portal_api.Handler 实例（不起服务器）+ 捕获 _json/send_error 输出。"""
    portal_api = _load_module("portal_api")
    seen = {}

    class _Stub(portal_api.Handler):
        def _json(self, obj, code=200):
            seen["obj"] = obj
            seen["code"] = code

        def send_error(self, code, message=None, explain=None):
            seen["code"] = code
            seen["send_error"] = message

    h = object.__new__(_Stub)
    h.client_address = ("127.0.0.1", 0)
    h.headers = {}
    h.path = ROUTE_PATH
    return h, seen


def _h_post(handler, body, path=ROUTE_PATH, length=None):
    handler.path = path
    handler.headers = {"Content-Length": str(len(body) if length is None else length)}
    handler.rfile = io.BytesIO(body)
    handler.do_POST()


def _items(n=1):
    return [
        {"at": "2026-10-27T10:00:%02d+08:00" % i, "domain": "研发"} for i in range(n)
    ]


def _rows(path):
    assert path.is_file(), "合法请求应落账本"
    return [
        json.loads(x)
        for x in path.read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]


class _NoRead:
    """超限/负长度时正文不得被读取（读即失败）。"""

    def read(self, *args):
        raise AssertionError("超限/非法长度正文不得先读取")


# ── ① 路由 200：合法写入 tmp 账本 + 字段落盘 ─────────────────────────
def test_post_valid_200_and_appends(ledger):
    h, seen = _make_handler()
    body = json.dumps(
        {
            "items": [
                {"at": "2026-10-27T10:00:00+08:00", "domain": "研发"},
                {"at": "2026-10-27T10:01:00+08:00", "domain": "情报", "ua": "pytest"},
            ]
        },
        ensure_ascii=False,
    ).encode("utf-8")
    _h_post(h, body)
    assert seen.get("code") == 200, "POST /api/zhuge/adoption 应 200"
    assert seen["obj"]["ok"] is True
    assert "appended 2" in seen["obj"]["message"]
    rows = _rows(ledger)
    assert len(rows) == 2
    assert [r["domain"] for r in rows] == ["研发", "情报"]
    assert rows[1]["ua"] == "pytest"
    assert all(r["received_at"] for r in rows), "received_at 由服务端补写"


# ── ② 兼容裸数组 body（{items:[...]} 之外的形态）─────────────────────
def test_post_bare_list_body_200(ledger):
    h, seen = _make_handler()
    _h_post(h, json.dumps(_items(1)).encode("utf-8"))
    assert seen.get("code") == 200
    assert len(_rows(ledger)) == 1


# ── ③ 非法 JSON → 400（不落账本）─────────────────────────────────────
def test_post_bad_json_400(ledger):
    h, seen = _make_handler()
    _h_post(h, b"{not-json")
    assert seen.get("code") == 400
    assert seen["obj"]["ok"] is False
    assert "非法 JSON" in seen["obj"]["message"]
    assert not ledger.exists(), "非法请求不得落盘"


# ── ④ 脏数据整批拒 400：非数组/空/字段越界/必填缺失 ──────────────────
@pytest.mark.parametrize(
    "payload,needle",
    [
        ({"items": "not-a-list"}, "非空数组"),
        ({"items": []}, "非空数组"),
        (
            {
                "items": [
                    {"at": "2026-10-27T10:00:00+08:00", "domain": "研发", "evil": 1}
                ]
            },
            "字段越界",
        ),
        ({"items": ["裸字符串"]}, "字段越界"),
        ({"items": [{"at": "2026-10-27T10:00:00+08:00"}]}, "必填"),
        ({"items": [{"domain": "研发"}]}, "必填"),
    ],
)
def test_post_invalid_items_400(ledger, payload, needle):
    h, seen = _make_handler()
    _h_post(h, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    assert seen.get("code") == 400, "脏数据应 400 而非静默通过"
    assert seen["obj"]["ok"] is False
    assert needle in seen["obj"]["message"]
    assert not ledger.exists()


# ── ⑤ 200 条上限：200 放行 / 201 拒绝（边界）──────────────────────────
def test_post_item_limit_boundary(ledger):
    h, seen = _make_handler()
    _h_post(h, json.dumps({"items": _items(200)}).encode("utf-8"))
    assert seen.get("code") == 200
    assert len(_rows(ledger)) == 200

    h2, seen2 = _make_handler()
    _h_post(h2, json.dumps({"items": _items(201)}).encode("utf-8"))
    assert seen2.get("code") == 400
    assert "上限 200" in seen2["obj"]["message"]
    assert len(_rows(ledger)) == 200, "被拒批次不得追加"


# ── ⑥ 重复/多条 POST：append-only，行数累加不覆盖 ─────────────────────
def test_repeated_posts_append_rows(ledger):
    for _ in range(2):
        h, seen = _make_handler()
        _h_post(h, json.dumps({"items": _items(2)}).encode("utf-8"))
        assert seen.get("code") == 200
    rows = _rows(ledger)
    assert len(rows) == 4, "两次 POST 应追加 4 行（重复提交不去重、不覆盖）"
    assert [r["domain"] for r in rows] == ["研发"] * 4


# ── ⑦ 正文超 64KiB → 413，且不读正文不落盘 ───────────────────────────
def test_post_oversize_body_413(ledger):
    h, seen = _make_handler()
    big = json.dumps(
        {
            "items": [
                {"at": "2026-10-27T10:00:00+08:00", "domain": "研发", "ua": "x" * 70000}
            ]
        }
    ).encode("utf-8")
    assert len(big) > 65536
    h.path = ROUTE_PATH
    h.headers = {"Content-Length": str(len(big))}
    h.rfile = _NoRead()
    h.do_POST()
    assert seen.get("code") == 413
    assert not ledger.exists()


# ── ⑧ 负数 Content-Length → 400，且不读正文 ───────────────────────────
def test_post_negative_content_length_400(ledger):
    h, seen = _make_handler()
    h.path = ROUTE_PATH
    h.headers = {"Content-Length": "-1"}
    h.rfile = _NoRead()
    h.do_POST()
    assert seen.get("code") == 400
    assert "正文长度不合法" in seen["obj"]["error"]
    assert not ledger.exists()


# ── ⑨ GET 无读语义 → 404 兜底（实现锁定）─────────────────────────────
def test_get_adoption_404(ledger):
    h, seen = _make_handler()
    h.path = ROUTE_PATH
    h.do_GET()
    assert seen.get("code") == 404, "adoption 无 GET 语义，应为 404 而非 200"
    assert seen["obj"]["error"] == "not found"
    assert not ledger.exists()


# ── ⑩ 结构回归：portal_api 已接线（import + do_POST 分发行）────────────
def test_route_wired_in_portal_api_source():
    src = (HERE / "portal_api.py").read_text(encoding="utf-8")
    assert "import api_zhuge_adoption" in src, "路由模块须显式 import（mixin 判例）"
    assert 'path == "/api/zhuge/adoption"' in src, "do_POST 须有分发行"
    assert "api_zhuge_adoption.handle_post(" in src, "须回调模块 handle_post"


# ── ⑪ C3DID：可选 decision_id/card_id（只加字段，旧报文兼容）──────────
def test_post_with_decision_id_card_id_200(ledger):
    h, seen = _make_handler()
    body = json.dumps({"items": [{
        "at": "2026-10-27T10:00:00+08:00", "domain": "研发",
        "decision_id": "DR-20261027-01", "card_id": "ZQ-20261027-01"}]},
        ensure_ascii=False).encode("utf-8")
    _h_post(h, body)
    assert seen.get("code") == 200
    rows = _rows(ledger)
    assert rows[0]["decision_id"] == "DR-20261027-01"
    assert rows[0]["card_id"] == "ZQ-20261027-01"


def test_post_invalid_decision_id_400(ledger):
    h, seen = _make_handler()
    body = json.dumps({"items": [{
        "at": "2026-10-27T10:00:00+08:00", "domain": "研发",
        "decision_id": "BAD-ID"}]}, ensure_ascii=False).encode("utf-8")
    _h_post(h, body)
    assert seen.get("code") == 400
    assert "decision_id 格式" in seen["obj"]["message"]
    assert not ledger.exists()


def test_post_invalid_card_id_400(ledger):
    h, seen = _make_handler()
    body = json.dumps({"items": [{
        "at": "2026-10-27T10:00:00+08:00", "domain": "研发",
        "card_id": "not-a-card"}]}, ensure_ascii=False).encode("utf-8")
    _h_post(h, body)
    assert seen.get("code") == 400
    assert "card_id 格式" in seen["obj"]["message"]
    assert not ledger.exists()
