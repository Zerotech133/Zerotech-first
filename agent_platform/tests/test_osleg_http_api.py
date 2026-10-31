# -*- coding: utf-8 -*-
"""OSLINK-W4 通道② 8795 HTTP 端点层 + 鉴权测试（离线 / 隔离 store / 不启 8795）。

对应工单：`K-QUANT-20261026-OSLEG-8795API`。红线（契约 §七）：
- 不启动 8795（serve.py 路由测试用 `build_server(port=0)` 进程内临时 server）；
- 不联网（唯一网络动作是本地回环临时 server；模块本身无网络 import，源码断言）；
- 不写真实 `agent_platform/bridge_inbox`（`set_store(tmp)` 隔离 + 模块级守卫
  断言真实 store 前后文件数/字节/摘要零变化）；
- 不发真实消息（deliver 用 `channel="synthetic"`，只落账）。
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLATFORM = ROOT / "agent_platform"
for _path in (str(PLATFORM), str(ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from gateway import inbox_gateway, osleg_http_api  # noqa: E402
from gateway.serve import build_server  # noqa: E402
from quant_backtest.agent_bridge import osleg_http_contract as contract  # noqa: E402
from quant_backtest.agent_bridge.osleg_inbox_channel import (  # noqa: E402
    KIND_TO_GATEWAY as INBOX_KIND_TO_GATEWAY,
)

API = osleg_http_api
MODULE_PATH = PLATFORM / "gateway" / "osleg_http_api.py"
SERVE_PATH = PLATFORM / "gateway" / "serve.py"
REAL_STORE = PLATFORM / "bridge_inbox"

TOKEN = "test-token-not-a-real-credential"
SOURCE = "qs1"
ANSWERED_BY = "A0-tianfeng"
ANSWER_REF = "ref-answer-1"


# ---------------------------------------------------------------- 工具
def _headers(source=SOURCE, token=TOKEN, idem="idem-1"):
    headers = {}
    if token is not None:
        headers[API.TOKEN_HEADER] = token
    if source is not None:
        headers[API.SOURCE_HEADER] = source
    if idem is not None:
        headers[API.IDEMPOTENCY_HEADER] = idem
    return headers


def _call(endpoint, body=None, headers=None, method=None, query=None):
    spec = API.ENDPOINTS[endpoint]
    return API.handle(
        method=method or spec.method,
        path=spec.path,
        headers=headers if headers is not None else _headers(),
        body=body,
        query=query,
    )


def _submit_body(
    kind="stock_consult_result",
    payload=None,
    source=SOURCE,
    report_id="QR-2026-0001",
    trace_id="tr-2026-0001",
):
    return {
        "kind": kind,
        "payload": {"q": "今日风控结论"} if payload is None else payload,
        "source": source,
        "report_id": report_id,
        "trace_id": trace_id,
    }


def _submit(headers=None, body=None):
    body = body or _submit_body()
    digest = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    status, payload = _call(
        "submit", body, headers or _headers(idem="idem-submit-" + digest)
    )
    assert status == 201, payload
    return payload


def _consume(msg_id, trace_id, headers=None):
    body = {
        "msg_id": msg_id,
        "consumed_by": ANSWERED_BY,
        "answered_by": ANSWERED_BY,
        "answer_ref": ANSWER_REF,
        "trace_id": trace_id,
    }
    return _call("consume", body, headers or _headers(idem="idem-consume-%s" % msg_id))


def _answer(msg_id, report_id, trace_id, text="答复正文", headers=None):
    body = {
        "msg_id": msg_id,
        "answered_by": ANSWERED_BY,
        "text": text,
        "report_id": report_id,
        "trace_id": trace_id,
    }
    return _call("answer", body, headers or _headers(idem="idem-answer-%s" % msg_id))


def _deliver(
    msg_id, trace_id, channel="synthetic", delivered_ref="ref-deliver-1", headers=None
):
    body = {
        "msg_id": msg_id,
        "channel": channel,
        "delivered_ref": delivered_ref,
        "trace_id": trace_id,
    }
    return _call("deliver", body, headers or _headers(idem="idem-deliver-%s" % msg_id))


def _receipt(msg_id=None, report_id=None, trace_id=None, headers=None):
    query = {}
    if msg_id:
        query["msg_id"] = msg_id
    if report_id:
        query["report_id"] = report_id
    if trace_id:
        query["trace_id"] = trace_id
    return _call(
        "receipt", headers=headers or _headers(idem=None), method="GET", query=query
    )


def _flow_to_answered(report_id="QR-2026-0001", trace_id="tr-2026-0001"):
    submitted = _submit(body=_submit_body(report_id=report_id, trace_id=trace_id))
    status, consumed = _consume(submitted["msg_id"], trace_id)
    assert status == 200, consumed
    status, answered = _answer(submitted["msg_id"], report_id, trace_id)
    assert status == 200, answered
    return submitted, consumed, answered


def _json_names(directory):
    return (
        sorted(path.name for path in directory.glob("*.json"))
        if directory.is_dir()
        else []
    )


def _store_view(store):
    return {
        "inbox": _json_names(store / "inbox"),
        "processed": _json_names(store / "processed"),
        "records": _json_names(store / "osleg_http" / "records"),
        "idem": (store / "osleg_http" / "idem.json").is_file(),
    }


def _empty_view():
    return {"inbox": [], "processed": [], "records": [], "idem": False}


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _record(store, msg_id):
    return _read_json(store / "osleg_http" / "records" / ("%s.json" % msg_id))


def _snapshot_tree(root):
    """目录快照：文件数 + 总字节 + (相对路径, 大小, sha256) 列表。"""
    files = []
    total = 0
    if root.exists():
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            data = path.read_bytes()
            total += len(data)
            files.append(
                (
                    str(path.relative_to(root)),
                    len(data),
                    hashlib.sha256(data).hexdigest(),
                )
            )
    return {"count": len(files), "bytes": total, "files": files}


# ---------------------------------------------------------------- fixture
@pytest.fixture(scope="module", autouse=True)
def _real_store_guard():
    """真实 bridge_inbox 零变化守卫（契约 §七：测试不得碰真实 store）。"""
    before = _snapshot_tree(REAL_STORE)
    yield
    after = _snapshot_tree(REAL_STORE)
    assert before == after, "测试改动了真实 agent_platform/bridge_inbox：%r -> %r" % (
        before,
        after,
    )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (API.TOKEN_ENV, API.TOKEN_FILE_ENV, API.STORE_ROOT_ENV, API.ENABLE_ENV):
        monkeypatch.delenv(name, raising=False)
    API._DEFAULT_STORE = None
    inbox_gateway._DEFAULT_STORE = None
    yield
    API._DEFAULT_STORE = None
    inbox_gateway._DEFAULT_STORE = None


@pytest.fixture()
def store(tmp_path):
    """隔离 store（绝不指向真实 bridge_inbox）。"""
    root = tmp_path / "isolated_store"
    API.set_store(root)
    return root


@pytest.fixture()
def auth(store, monkeypatch):
    """token env 注入 + 隔离 store。"""
    monkeypatch.setenv(API.TOKEN_ENV, TOKEN)
    return TOKEN


@pytest.fixture()
def token_only(monkeypatch):
    """只注入 token、不注入 store（验证 store 未配置失败关闭）。"""
    monkeypatch.setenv(API.TOKEN_ENV, TOKEN)
    return TOKEN


# ---------------------------------------------------------------- 开关/配置
def test_enabled_switch_parsing(monkeypatch):
    monkeypatch.delenv(API.ENABLE_ENV, raising=False)
    assert API.enabled() is False
    for value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv(API.ENABLE_ENV, value)
        assert API.enabled() is True
    monkeypatch.setenv(API.ENABLE_ENV, "0")
    assert API.enabled() is False


def test_token_unconfigured_503_fail_closed(store):
    status, body = _call("submit", _submit_body(), _headers(idem="idem-t0"))
    assert status == 503
    assert body["ok"] is False and body["accounted"] is False
    assert body["error"] == "token_unconfigured"
    assert body["status"] == "unknown" and body["retryable"] is True
    assert _store_view(store) == _empty_view()


def test_token_file_injection_works(store, monkeypatch, tmp_path):
    token_file = tmp_path / "token.txt"
    token_file.write_text("file-injected-not-a-real-token\n", encoding="utf-8")
    monkeypatch.setenv(API.TOKEN_FILE_ENV, str(token_file))
    status, body = _call(
        "submit",
        _submit_body(),
        _headers(token="file-injected-not-a-real-token", idem="idem-file"),
    )
    assert status == 201, body
    assert body["status"] == "submitted"


def test_env_token_takes_priority_over_file(store, monkeypatch, tmp_path):
    token_file = tmp_path / "token.txt"
    token_file.write_text("file-token-not-real\n", encoding="utf-8")
    monkeypatch.setenv(API.TOKEN_ENV, TOKEN)
    monkeypatch.setenv(API.TOKEN_FILE_ENV, str(token_file))
    status, body = _call("submit", _submit_body(), _headers(idem="idem-env"))
    assert status == 201, body
    status, body = _call(
        "submit",
        _submit_body(),
        _headers(token="file-token-not-real", idem="idem-file-x"),
    )
    assert status == 401 and body["error"] == "token_invalid"


def test_default_token_file_path_is_gateway_dir():
    assert API.DEFAULT_TOKEN_FILE == PLATFORM / "gateway" / ".osleg_http_token"
    assert API.DEFAULT_TOKEN_FILE.name == ".osleg_http_token"


def test_placeholder_token_treated_as_unconfigured(store, monkeypatch):
    monkeypatch.setenv(API.TOKEN_ENV, "changeme")
    assert API.token_configured() is False
    status, body = _call(
        "submit", _submit_body(), _headers(token="changeme", idem="idem-ph")
    )
    assert status == 503 and body["error"] == "token_unconfigured"


def test_store_unconfigured_503(token_only):
    assert API._DEFAULT_STORE is None
    status, body = _call("submit", _submit_body(), _headers(idem="idem-nostore"))
    assert status == 503
    assert body["error"] == "store_unconfigured" and body["accounted"] is False


def test_store_env_root_injection(monkeypatch, tmp_path):
    monkeypatch.setenv(API.TOKEN_ENV, TOKEN)
    root = tmp_path / "env_store"
    monkeypatch.setenv(API.STORE_ROOT_ENV, str(root))
    status, body = _call("submit", _submit_body(), _headers(idem="idem-envstore"))
    assert status == 201, body
    assert (root / "inbox" / ("%s.json" % body["msg_id"])).is_file()
    assert (root / "osleg_http" / "records" / ("%s.json" % body["msg_id"])).is_file()


# ---------------------------------------------------------------- 鉴权
def test_missing_token_header_401(auth):
    status, body = _call(
        "submit", _submit_body(), _headers(token=None, idem="idem-401a")
    )
    assert status == 401 and body["error"] == "token_missing"
    assert body["accounted"] is False


def test_wrong_token_401(auth):
    status, body = _call(
        "submit", _submit_body(), _headers(token=TOKEN + "-wrong", idem="idem-401b")
    )
    assert status == 401 and body["error"] == "token_invalid"
    assert body["accounted"] is False


def test_bad_source_403(auth, store):
    before = _store_view(store)
    status, body = _call(
        "submit", _submit_body(), _headers(source="mallory", idem="idem-403")
    )
    assert status == 403 and body["error"] == "source_forbidden"
    assert body["accounted"] is False
    assert _store_view(store) == before


def test_submit_body_source_mismatch_403(auth, store):
    before = _store_view(store)
    status, body = _call(
        "submit", _submit_body(source="a0"), _headers(source="qs1", idem="idem-srcmix")
    )
    assert status == 403 and body["error"] == "source_mismatch"
    assert _store_view(store) == before


def test_submit_body_source_forbidden_403(auth, store):
    before = _store_view(store)
    status, body = _call(
        "submit",
        _submit_body(source="external"),
        _headers(source="qs1", idem="idem-extsrc"),
    )
    assert status == 403 and body["error"] == "source_forbidden"
    assert _store_view(store) == before


# ---------------------------------------------------------------- submit/幂等
def test_submit_201_maps_to_gateway(auth, store):
    status, body = _call("submit", _submit_body(), _headers(idem="idem-submit-ok"))
    assert status == 201, body
    assert body["ok"] is True and body["accounted"] is True
    assert body["status"] == "submitted" and body["state"] == "submitted"
    assert body["channel"] == "http8795" and body["alive"] is True
    assert body["msg_id"].startswith("lg-")
    assert body["report_id"] == "QR-2026-0001"
    assert body["trace_id"] == "tr-2026-0001"

    view = _store_view(store)
    assert len(view["inbox"]) == 1 and view["processed"] == []
    assert len(view["records"]) == 1 and view["idem"] is True
    gateway_record = _read_json(store / "inbox" / ("%s.json" % body["msg_id"]))
    assert gateway_record["kind"] == "question"  # kind 显式映射
    assert gateway_record["source"] == "qs1"
    record = _record(store, body["msg_id"])
    assert record["kind"] == "stock_consult_result"
    assert record["gateway_kind"] == "question"
    assert record["state"] == "submitted"
    assert record["report_id"] == "QR-2026-0001"
    assert record["trace_id"] == "tr-2026-0001"


def test_submit_idempotent_replay_same_msg_id(auth, store):
    body = _submit_body()
    headers = _headers(idem="idem-replay-1")
    status1, first = _call("submit", body, headers)
    assert status1 == 201
    status2, replay = _call("submit", body, headers)
    assert status2 == 200
    assert replay["msg_id"] == first["msg_id"]
    assert replay["replayed"] is True and replay["duplicate"] is True
    view = _store_view(store)
    assert len(view["inbox"]) == 1 and len(view["records"]) == 1
    status, receipt = _receipt(first["msg_id"])
    assert status == 200
    assert receipt["duplicate"] is True


def test_submit_idempotency_conflict_409(auth, store):
    status1, first = _call("submit", _submit_body(), _headers(idem="idem-conflict"))
    assert status1 == 201
    before = _store_view(store)
    status2, conflict = _call(
        "submit",
        _submit_body(payload={"q": "异体请求"}),
        _headers(idem="idem-conflict"),
    )
    assert status2 == 409
    assert conflict["ok"] is False and conflict["accounted"] is False
    assert conflict["error"] == "idempotency_conflict"
    assert conflict["conflict_with"] == first["msg_id"]
    assert _store_view(store) == before


def test_submit_missing_trace_id_422_unknown_no_write(auth, store):
    body = _submit_body()
    body["trace_id"] = ""
    status, out = _call("submit", body, _headers(idem="idem-notrace"))
    assert status == 422
    assert out["status"] == "unknown" and out["accounted"] is False
    assert out["error"] == "trace_id_missing"
    assert _store_view(store) == _empty_view()


def test_submit_missing_report_id_422_no_write(auth, store):
    body = _submit_body()
    body["report_id"] = ""
    status, out = _call("submit", body, _headers(idem="idem-norep"))
    assert status == 422 and out["error"] == "report_id_missing"
    assert _store_view(store) == _empty_view()


def test_submit_unregistered_kind_fail_closed(auth, store):
    status, out = _call(
        "submit",
        _submit_body(kind="stock_consult_result_v2"),
        _headers(idem="idem-kind"),
    )
    assert status == 422
    assert out["error"] == "kind_not_bridged"
    assert out["kind"] == "stock_consult_result_v2"
    assert out["accounted"] is False
    assert _store_view(store) == _empty_view()


def test_submit_missing_idempotency_header_422(auth, store):
    status, out = _call("submit", _submit_body(), _headers(idem=None))
    assert status == 422 and out["error"] == "idempotency_key_missing"
    assert _store_view(store) == _empty_view()


def test_submit_payload_must_be_object(auth, store):
    body = _submit_body()
    body["payload"] = ["not", "dict"]
    status, out = _call("submit", body, _headers(idem="idem-badpayload"))
    assert status == 422 and out["error"] == "invalid_request"
    assert _store_view(store) == _empty_view()


def test_submit_non_object_body_422(auth):
    status, out = _call("submit", ["not", "object"], _headers(idem="idem-listbody"))
    assert status == 422 and out["error"] == "invalid_request"


# ---------------------------------------------------------------- consume
def test_consume_requires_answer_evidence(auth, store):
    submitted = _submit()
    msg_id, trace_id = submitted["msg_id"], submitted["trace_id"]
    body = {"msg_id": msg_id, "consumed_by": ANSWERED_BY, "trace_id": trace_id}
    status, out = _call("consume", body, _headers(idem="idem-consume-evidence"))
    assert status == 422
    assert out["error"] == "bad_answer_evidence"
    assert out["accounted"] is False
    assert _store_view(store)["processed"] == []
    assert _record(store, msg_id)["state"] == "submitted"


def test_consume_success_maps_gateway(auth, store):
    submitted = _submit()
    msg_id, trace_id = submitted["msg_id"], submitted["trace_id"]
    status, out = _consume(msg_id, trace_id)
    assert status == 200, out
    assert out["status"] == "consumed" and out["state"] == "consumed"
    assert out["alive"] is True and out["answered"] is False
    assert out["consumed_by"] == ANSWERED_BY

    view = _store_view(store)
    assert view["inbox"] == [] and len(view["processed"]) == 1
    gateway_record = _read_json(store / "processed" / ("%s.json" % msg_id))
    assert gateway_record["answered_by"] == ANSWERED_BY
    assert gateway_record["answer_ref"] == ANSWER_REF
    record = _record(store, msg_id)
    assert record["state"] == "consumed"
    assert record["consumed_by"] == ANSWERED_BY
    # 同键同体重放
    status2, replay = _consume(msg_id, trace_id)
    assert status2 == 200 and replay["replayed"] is True


def test_consume_unknown_msg_404(auth):
    status, out = _call(
        "consume",
        {
            "msg_id": "lg-unknown",
            "consumed_by": ANSWERED_BY,
            "answered_by": ANSWERED_BY,
            "answer_ref": ANSWER_REF,
            "trace_id": "tr-x",
        },
        _headers(idem="idem-consume-404"),
    )
    assert status == 404 and out["error"] == "not_found"


def test_consume_trace_mismatch_422(auth, store):
    submitted = _submit()
    msg_id = submitted["msg_id"]
    body = {
        "msg_id": msg_id,
        "consumed_by": ANSWERED_BY,
        "answered_by": ANSWERED_BY,
        "answer_ref": ANSWER_REF,
        "trace_id": "tr-wrong",
    }
    status, out = _call("consume", body, _headers(idem="idem-consume-trace"))
    assert status == 422 and out["error"] == "trace_mismatch"
    assert out["status"] == "unknown" and out["accounted"] is False
    assert _store_view(store)["processed"] == []
    assert _record(store, msg_id)["state"] == "submitted"


def test_consume_missing_trace_id_422_unknown(auth):
    submitted = _submit()
    body = {
        "msg_id": submitted["msg_id"],
        "consumed_by": ANSWERED_BY,
        "answered_by": ANSWERED_BY,
        "answer_ref": ANSWER_REF,
        "trace_id": "",
    }
    status, out = _call("consume", body, _headers(idem="idem-consume-notrace"))
    assert status == 422 and out["error"] == "trace_id_missing"
    assert out["status"] == "unknown"


# ---------------------------------------------------------------- answer
def test_answer_requires_consumed_state(auth, store):
    submitted = _submit()
    status, out = _answer(
        submitted["msg_id"], submitted["report_id"], submitted["trace_id"]
    )
    assert status == 409 and out["error"] == "state_conflict"
    assert out["accounted"] is False
    assert _record(store, submitted["msg_id"])["state"] == "submitted"


def test_answer_missing_report_id_rejected(auth, store):
    submitted, _, _ = _flow_to_answered()
    msg_id = submitted["msg_id"]
    status, out = _call(
        "answer",
        {
            "msg_id": msg_id,
            "answered_by": ANSWERED_BY,
            "text": "答复",
            "report_id": "",
            "trace_id": submitted["trace_id"],
        },
        _headers(idem="idem-answer-norep"),
    )
    assert status == 422 and out["error"] == "report_id_missing"
    assert out["linked"] is False and out["accounted"] is False
    record = _record(store, msg_id)
    assert record["state"] == "answered"  # 前序 flow 已答复：本次拒绝不改写
    # 用全新单验证"缺 report_id 不写 answered"
    fresh = _submit(
        body=_submit_body(report_id="QR-2026-0002", trace_id="tr-2026-0002")
    )
    _consume(fresh["msg_id"], fresh["trace_id"])
    status2, out2 = _call(
        "answer",
        {
            "msg_id": fresh["msg_id"],
            "answered_by": ANSWERED_BY,
            "text": "答复",
            "trace_id": fresh["trace_id"],
        },
        _headers(idem="idem-answer-norep-2"),
    )
    assert status2 == 422 and out2["error"] == "report_id_missing"
    assert _record(store, fresh["msg_id"])["state"] == "consumed"


def test_answer_report_id_mismatch_rejected(auth, store):
    submitted, _, _ = _flow_to_answered(
        report_id="QR-2026-0010", trace_id="tr-2026-0010"
    )
    msg_id = submitted["msg_id"]
    before = _record(store, msg_id)
    status, out = _call(
        "answer",
        {
            "msg_id": msg_id,
            "answered_by": ANSWERED_BY,
            "text": "答复",
            "report_id": "QR-OTHER",
            "trace_id": submitted["trace_id"],
        },
        _headers(idem="idem-answer-mismatch"),
    )
    assert status == 422 and out["error"] == "report_id_mismatch"
    assert out["linked"] is False
    assert _record(store, msg_id) == before


def test_answer_trace_mismatch_rejected(auth, store):
    submitted, _, _ = _flow_to_answered()
    msg_id = submitted["msg_id"]
    before = _record(store, msg_id)
    status, out = _call(
        "answer",
        {
            "msg_id": msg_id,
            "answered_by": ANSWERED_BY,
            "text": "答复",
            "report_id": submitted["report_id"],
            "trace_id": "tr-wrong",
        },
        _headers(idem="idem-answer-trace"),
    )
    assert status == 422 and out["error"] == "trace_mismatch"
    assert out["status"] == "unknown"
    assert _record(store, msg_id) == before


def test_answer_success_writes_sidecar_atomically(auth, store):
    submitted = _submit()
    msg_id = submitted["msg_id"]
    _consume(msg_id, submitted["trace_id"])
    status, out = _answer(
        msg_id, submitted["report_id"], submitted["trace_id"], text="风控结论：合格"
    )
    assert status == 200, out
    assert out["status"] == "answered" and out["linked"] is True
    assert out["answered"] is True and out["alive"] is True
    record = _record(store, msg_id)
    assert record["state"] == "answered"
    assert record["answer_text"] == "风控结论：合格"
    assert record["answered_by"] == ANSWERED_BY
    assert record["answer_report_id"] == submitted["report_id"]
    assert record["answer_trace_id"] == submitted["trace_id"]
    # 原子写：不得残留 tmp 文件
    leftovers = [
        path.name
        for path in (store / "osleg_http").rglob("*")
        if path.is_file() and path.name.startswith(".")
    ]
    assert leftovers == []


def test_answer_unknown_msg_404(auth):
    status, out = _call(
        "answer",
        {
            "msg_id": "lg-unknown",
            "answered_by": ANSWERED_BY,
            "text": "x",
            "report_id": "QR-1",
            "trace_id": "tr-1",
        },
        _headers(idem="idem-answer-404"),
    )
    assert status == 404 and out["error"] == "not_found"


def test_answer_text_must_be_nonempty_string(auth, store):
    submitted = _submit()
    msg_id = submitted["msg_id"]
    _consume(msg_id, submitted["trace_id"])
    before = _record(store, msg_id)
    status, out = _call(
        "answer",
        {
            "msg_id": msg_id,
            "answered_by": ANSWERED_BY,
            "text": {"bad": "object"},
            "report_id": submitted["report_id"],
            "trace_id": submitted["trace_id"],
        },
        _headers(idem="idem-answer-badtext"),
    )
    assert status == 422 and out["error"] == "invalid_request"
    assert _record(store, msg_id) == before


# ---------------------------------------------------------------- deliver
def test_deliver_requires_answered(auth, store):
    submitted = _submit()
    msg_id = submitted["msg_id"]
    status, out = _deliver(msg_id, submitted["trace_id"])
    assert status == 409 and out["error"] == "state_conflict"
    assert out["accounted"] is False
    _consume(msg_id, submitted["trace_id"])
    status2, out2 = _deliver(msg_id, submitted["trace_id"])
    assert status2 == 409 and out2["error"] == "state_conflict"
    assert _record(store, msg_id)["state"] == "consumed"
    assert (
        _read_json(store / "processed" / ("%s.json" % msg_id)).get("delivered_at")
        is None
    )


def test_deliver_success_channel_is_label_only(auth, store):
    submitted, _, _ = _flow_to_answered()
    msg_id = submitted["msg_id"]
    status, out = _deliver(msg_id, submitted["trace_id"], channel="synthetic")
    assert status == 200, out
    assert out["status"] == "delivered" and out["state"] == "delivered"
    assert out["delivered"] is True and out["alive"] is False
    record = _record(store, msg_id)
    assert record["state"] == "delivered"
    assert record["delivered_channel"] == "synthetic"
    gateway_record = _read_json(store / "processed" / ("%s.json" % msg_id))
    assert gateway_record["channel"] == "synthetic"
    assert gateway_record["delivered_ref"] == "ref-deliver-1"


def test_deliver_requires_delivery_evidence(auth, store):
    submitted, _, _ = _flow_to_answered()
    msg_id = submitted["msg_id"]
    body = {
        "msg_id": msg_id,
        "channel": "",
        "delivered_ref": "",
        "trace_id": submitted["trace_id"],
    }
    status, out = _call("deliver", body, _headers(idem="idem-deliver-bad"))
    assert status == 422 and out["error"] == "bad_delivery_evidence"
    assert _record(store, msg_id)["state"] == "answered"


def test_deliver_unknown_msg_404(auth):
    status, out = _call(
        "deliver",
        {
            "msg_id": "lg-unknown",
            "channel": "synthetic",
            "delivered_ref": "r",
            "trace_id": "tr-1",
        },
        _headers(idem="idem-deliver-404"),
    )
    assert status == 404 and out["error"] == "not_found"


# ---------------------------------------------------------------- receipt
def test_receipt_status_mapping_through_lifecycle(auth):
    submitted = _submit()
    msg_id, trace_id = submitted["msg_id"], submitted["trace_id"]

    status, body = _receipt(msg_id)
    assert status == 200
    assert body["status"] == "submitted" and body["state"] == "submitted"
    assert body["alive"] is True and body["answered"] is False
    assert body["delivered"] is False and body["duplicate"] is False
    assert body["gateway_state"] == "inbox"
    assert body["records"][0]["state"] == "submitted"
    assert body["trace_consistent"] is True

    _consume(msg_id, trace_id)
    status, body = _receipt(msg_id)
    assert body["status"] == "consumed" and body["alive"] is True
    assert body["answered"] is False and body["delivered"] is False
    assert body["gateway_state"] == "processed"
    assert body["records"][0]["gateway_answered"] is True

    _answer(msg_id, submitted["report_id"], trace_id)
    status, body = _receipt(msg_id)
    assert body["status"] == "answered" and body["alive"] is True
    assert body["answered"] is True and body["linked"] is True
    assert body["delivered"] is False

    _deliver(msg_id, trace_id)
    status, body = _receipt(msg_id)
    assert body["status"] == "delivered" and body["alive"] is False
    assert body["answered"] is True and body["delivered"] is True
    assert body["trace_ids"] == [trace_id]
    assert body["channels"] == ["http8795"]


def test_receipt_by_report_id(auth):
    submitted = _submit(
        body=_submit_body(report_id="QR-2026-0100", trace_id="tr-2026-0100")
    )
    status, body = _receipt(report_id="QR-2026-0100")
    assert status == 200
    assert body["msg_id"] == submitted["msg_id"]
    assert body["report_id"] == "QR-2026-0100"


def test_receipt_by_report_id_ambiguous_409(auth):
    _submit(body=_submit_body(report_id="QR-DUP", trace_id="tr-dup"))
    _submit(
        body=_submit_body(
            kind="risk_verdict",
            payload={"verdict": "pass"},
            report_id="QR-DUP",
            trace_id="tr-dup",
        )
    )
    status, body = _receipt(report_id="QR-DUP")
    assert status == 409 and body["error"] == "ambiguous_report_id"
    assert len(body["msg_ids"]) == 2


def test_receipt_not_found_404(auth):
    status, body = _receipt(msg_id="lg-unknown")
    assert status == 404
    assert body["error"] == "not_found" and body["accounted"] is False


def test_receipt_requires_key_422(auth):
    status, body = _receipt()
    assert status == 422 and body["error"] == "invalid_request"


def test_receipt_trace_mismatch_422(auth):
    submitted = _submit()
    status, body = _receipt(submitted["msg_id"], trace_id="tr-wrong")
    assert status == 422 and body["error"] == "trace_mismatch"
    assert body["status"] == "unknown"


# ---------------------------------------------------------------- 统一口径
def test_unknown_endpoint_404():
    status, body = API.handle(method="POST", path="/osleg/v1/nope", headers={}, body={})
    assert status == 404 and body["error"] == "invalid_endpoint"
    status, body = API.handle(method="POST", path="/nope", headers={}, body={})
    assert status == 404 and body["error"] == "invalid_endpoint"


def test_method_mismatch_404(auth):
    status, body = API.handle(
        method="GET", path="/osleg/v1/submit", headers=_headers(idem=None), body=None
    )
    assert status == 404 and body["error"] == "invalid_endpoint"


def test_constants_match_offline_contract():
    assert API.BASE_PATH == contract.BASE_PATH
    assert API.TOKEN_HEADER == contract.TOKEN_HEADER
    assert API.SOURCE_HEADER == contract.SOURCE_HEADER
    assert API.IDEMPOTENCY_HEADER == contract.IDEMPOTENCY_HEADER
    assert tuple(API.ALLOWED_SOURCES) == tuple(contract.ALLOWED_SOURCES)
    assert tuple(API.W4_STATES) == tuple(contract.W4_STATES)
    assert API.CHANNEL_NAMESPACE == contract.CHANNEL_NAMESPACE
    for name, schema in contract.ENDPOINTS.items():
        spec = API.ENDPOINTS[name]
        assert spec.method == schema.method, name
        assert spec.path == schema.path, name
        assert spec.success_http == schema.success_http, name
        assert spec.success_state == schema.success_state, name
        assert spec.idempotent == schema.idempotent, name
        assert tuple(spec.query_required) == tuple(schema.query_required), name
        assert tuple(spec.required_headers) == tuple(schema.required_headers), name
        # 本层 required_body 是契约 body 的超集：consume 额外要求 gateway
        # 证据字段 answered_by/answer_ref（证据门槛不放松，见模块说明）。
        assert set(schema.required_body) <= set(spec.required_body), name
    for error, http_status in contract.ERROR_HTTP_STATUS.items():
        assert API.http_status_for_error(error) == http_status, error
    assert API.http_status_for_error(
        "not_registered_error"
    ) == contract.http_status_for_error("not_registered_error")


def test_kind_mapping_matches_inbox_channel_ch1():
    assert API.KIND_TO_GATEWAY == INBOX_KIND_TO_GATEWAY
    assert set(API.KIND_TO_GATEWAY.values()) <= set(API.GATEWAY_KINDS)


def test_success_envelope_contract_parse_compatible(auth):
    status, submitted = _call("submit", _submit_body(), _headers(idem="idem-parse"))
    parsed = contract.parse_submit_response(status, submitted)
    assert parsed["ok"] is True and parsed["accounted"] is True
    assert parsed["state"] == contract.STATE_SUBMITTED
    assert parsed["delivered_claimed"] is False
    assert parsed["msg_id"] == submitted["msg_id"]

    status, receipt = _receipt(submitted["msg_id"])
    parsed_receipt = contract.parse_receipt_response(status, receipt)
    assert parsed_receipt["ok"] is True and parsed_receipt["accounted"] is True
    assert parsed_receipt["record_states"] == [contract.STATE_SUBMITTED]

    _consume(submitted["msg_id"], submitted["trace_id"])
    status, consumed = _consume(submitted["msg_id"], submitted["trace_id"])
    parsed_consumed = contract.parse_consume_response(status, consumed)
    assert parsed_consumed["ok"] is True
    assert parsed_consumed["state"] == contract.STATE_CONSUMED


def test_failure_envelope_contract_parse_compatible(auth):
    status, body = _call(
        "submit", _submit_body(), _headers(source="mallory", idem="idem-parse-fail")
    )
    parsed = contract.parse_submit_response(status, body)
    assert parsed["ok"] is False and parsed["accounted"] is False
    assert parsed["state"] == contract.STATE_REJECTED
    assert parsed["http_status"] == 403


def test_4xx_requests_do_not_write_store(auth, store):
    before = _store_view(store)
    _call("submit", _submit_body(), _headers(source="mallory", idem="idem-w1"))  # 403
    _call(
        "submit", _submit_body(kind="not_registered"), _headers(idem="idem-w2")
    )  # 422
    body = _submit_body()
    body["trace_id"] = ""
    _call("submit", body, _headers(idem="idem-w3"))  # 422
    _call("receipt", method="GET", headers=_headers(idem=None), query={})  # 422
    _call(
        "consume",
        {
            "msg_id": "lg-unknown",
            "consumed_by": ANSWERED_BY,
            "answered_by": ANSWERED_BY,
            "answer_ref": ANSWER_REF,
            "trace_id": "tr-1",
        },
        _headers(idem="idem-w4"),
    )  # 404
    assert _store_view(store) == before


def test_gateway_failure_maps_5xx_unknown_retryable(auth, store, monkeypatch):
    from gateway.envelope import make_envelope

    monkeypatch.setattr(
        inbox_gateway,
        "submit",
        lambda *args, **kwargs: make_envelope(
            kind="inbox_submit", ok=False, error={"code": "boom", "message": "内部失败"}
        ),
    )
    status, body = _call("submit", _submit_body(), _headers(idem="idem-gwfail"))
    assert status == 502
    assert body["ok"] is False and body["accounted"] is False
    assert body["status"] == "unknown" and body["retryable"] is True
    assert body["error"] == "gateway_error"
    assert _store_view(store) == _empty_view()


def test_internal_exception_maps_500_unknown(auth, store, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("模拟内部异常")

    monkeypatch.setattr(inbox_gateway, "submit", boom)
    status, body = _call("submit", _submit_body(), _headers(idem="idem-internal"))
    assert status == 500
    assert body["status"] == "unknown" and body["retryable"] is True
    assert body["accounted"] is False
    assert _store_view(store) == _empty_view()


# ---------------------------------------------------------------- 源码断言
def _module_import_roots(tree):
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_module_has_no_network_imports():
    source = MODULE_PATH.read_text(encoding="utf-8")
    roots = _module_import_roots(ast.parse(source))
    assert roots.isdisjoint(
        {"socket", "urllib", "requests", "http", "aiohttp", "httpx", "ssl", "asyncio"}
    )


def test_module_token_only_comes_from_env_or_file():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "compare_digest" in source  # 常量时间比较
    assert API.TOKEN_ENV in source
    assert API.TOKEN_FILE_ENV in source
    # 不得硬编码 token：无 token/secret/password 字面量赋值
    assert not re.search(
        r"(?i)\b(?:auth_token|api[_-]?key|secret|password|token)\s*=\s*"
        r"[\"'][A-Za-z0-9_\-\.+/]{8,}[\"']",
        source,
    )


def test_serve_route_is_opt_in_only():
    source = SERVE_PATH.read_text(encoding="utf-8")
    assert "OSLEG_HTTP_API" in source
    assert "/osleg/v1" in source
    # 未开启时不得进入通道②分支：开关判断必须在 dispatch 之前
    assert source.index("_osleg_enabled()") < source.index("_osleg_dispatch(")
    assert "osleg_http_api" in source


# ---------------------------------------------------------------- serve 路由
@contextmanager
def _serve():
    server = build_server(port=0)  # 进程内随机端口，不占 8795
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d" % server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def _http(method, url, body=None, headers=None):
    data = None
    request_headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(
        url, data=data, headers=request_headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_serve_osleg_disabled_by_default_404(auth):
    with _serve() as base:
        status, health = _http("GET", base + "/health")
        assert status == 200 and health["ok"] is True
        status, body = _http(
            "POST",
            base + "/osleg/v1/submit",
            _submit_body(),
            _headers(idem="idem-serve-off"),
        )
        assert status == 404
        assert body["ok"] is False and body["error"]["code"] == "not_found"
        status, body = _http("GET", base + "/osleg/v1/receipt?msg_id=lg-x")
        assert status == 404 and body["error"]["code"] == "not_found"


def test_serve_osleg_enabled_routes_over_real_http(auth, monkeypatch):
    monkeypatch.setenv(API.ENABLE_ENV, "1")
    with _serve() as base:
        status, body = _http(
            "POST",
            base + "/osleg/v1/submit",
            _submit_body(),
            _headers(idem="idem-serve-on-1"),
        )
        assert status == 201, body
        assert body["status"] == "submitted"
        msg_id = body["msg_id"]
        status, receipt = _http(
            "GET",
            base + "/osleg/v1/receipt?msg_id=" + msg_id,
            headers=_headers(idem=None),
        )
        assert status == 200 and receipt["state"] == "submitted"
        # 既有 /health 与默认 404 行为不变
        status, health = _http("GET", base + "/health")
        assert status == 200 and health["service"] == "legion-gateway"
        status, _ = _http("GET", base + "/nope")
        assert status == 404


def test_serve_osleg_enabled_unauthorized_401(auth, monkeypatch):
    monkeypatch.setenv(API.ENABLE_ENV, "1")
    with _serve() as base:
        status, body = _http(
            "POST",
            base + "/osleg/v1/submit",
            _submit_body(),
            {
                API.TOKEN_HEADER: "wrong-token-value",
                API.SOURCE_HEADER: SOURCE,
                API.IDEMPOTENCY_HEADER: "idem-serve-401",
            },
        )
        assert status == 401 and body["error"] == "token_invalid"


def test_serve_osleg_enabled_without_token_503(store, monkeypatch):
    monkeypatch.setenv(API.ENABLE_ENV, "1")
    with _serve() as base:
        status, body = _http(
            "POST",
            base + "/osleg/v1/submit",
            _submit_body(),
            _headers(idem="idem-serve-notoken"),
        )
        assert status == 503 and body["error"] == "token_unconfigured"


def test_serve_osleg_enabled_bad_source_403(auth, monkeypatch):
    monkeypatch.setenv(API.ENABLE_ENV, "1")
    with _serve() as base:
        status, body = _http(
            "POST",
            base + "/osleg/v1/submit",
            _submit_body(),
            _headers(source="mallory", idem="idem-serve-403"),
        )
        assert status == 403 and body["error"] == "source_forbidden"


# ---------------------------------------------------------------- M2 msg_id 路径安全
def test_invalid_msg_id_rejected_by_sidecar_helpers(tmp_path):
    """M2（K-ORG-20261031-APIHARDEN）：msg_id 必须 _ID_RE_TEXT 全匹配且不越 records 根。"""
    store = API._HttpStore(tmp_path / "store").ensure()
    bad_ids = [
        "../escape", "../../secret", "a/b", "a\\b", "%2e%2e%2fescape",
        "C:\\Windows\\win.ini", "/etc/passwd", "..",
        "", "x" * 65, "lg-测试",
    ]
    for bad in bad_ids:
        assert API._valid_msg_id(bad) is False, bad
        assert API._record_path(store, bad) is None, bad
        assert API._get_record(store, bad) is None, bad
        with pytest.raises(ValueError):
            API._save_record(store, {"msg_id": bad})
    # 合法 msg_id 正常可用，且路径必须落在 records/ 内
    assert API._valid_msg_id("lg-abc_123") is True
    path = API._record_path(store, "lg-abc_123")
    assert path is not None
    assert path.name == "lg-abc_123.json"
    assert path.parent.name == "records"
    assert ".." not in path.parts


def test_traversal_msg_id_handlers_fail_closed(auth, store, monkeypatch):
    """恶意 msg_id 一律 404 失败关闭，且不得触达 gateway 账（防越权/穿越查询）。"""
    gateway_calls = []
    monkeypatch.setattr(
        API, "_gateway_view",
        lambda msg_id: gateway_calls.append(msg_id) or {
            "ok": True, "state": "processed", "answered": True, "delivered": False},
    )
    bad_ids = ["../secret", "..\\secret", "/etc/passwd",
               "C:\\Windows\\win.ini", "lg-ok/../../secret", "%2e%2e/escape"]
    for idx, bad in enumerate(bad_ids):
        body = {
            "msg_id": bad,
            "consumed_by": ANSWERED_BY,
            "answered_by": ANSWERED_BY,
            "answer_ref": ANSWER_REF,
            "trace_id": "tr-x",
        }
        status, out = _call("consume", body,
                            _headers(idem="idem-m2-consume-%d" % idx))
        assert status == 404 and out["error"] == "not_found", bad
        status, out = _call("answer",
                            {"msg_id": bad, "answered_by": ANSWERED_BY,
                             "text": "x", "report_id": "QR-1",
                             "trace_id": "tr-x"},
                            _headers(idem="idem-m2-answer-%d" % idx))
        assert status == 404 and out["error"] == "not_found", bad
        status, out = _call("deliver",
                            {"msg_id": bad, "channel": "synthetic",
                             "delivered_ref": "r", "trace_id": "tr-x"},
                            _headers(idem="idem-m2-deliver-%d" % idx))
        assert status == 404 and out["error"] == "not_found", bad
    # receipt 非法 msg_id：404，且不调用 _gateway_view
    status, out = _receipt(msg_id="../secret")
    assert status == 404 and out["error"] == "not_found"
    assert gateway_calls == [], "非法 msg_id 不得回查 gateway 账"
    # 合法链路仍正常（回归）
    submitted = _submit()
    status, out = _consume(submitted["msg_id"], submitted["trace_id"])
    assert status == 200 and out["status"] == "consumed"
