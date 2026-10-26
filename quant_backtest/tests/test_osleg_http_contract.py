# -*- coding: utf-8 -*-
"""OSLINK-W4 通道② HTTP 8795 离线协议契约测试（伪 transport / 无真实 HTTP）。

对应契约：`docs/plans/2026-10-25_OSLINK-W4_司马懿到田丰归口契约v0.1_草案.md` v1.0
（2026-10-26 12:20 老田代 A0 签认）；归属单 `K-QUANT-20261026-OSLEG-CH2`。

覆盖：端点 schema、正常受理映射、401/403 鉴权、幂等重放/异体 409、
4xx/5xx 不落账、缺 trace_id/report_id 拒绝且 status=unknown、双通道
异 msg_id 同 report_id/trace_id、源码无网络 import、不启动 8795、无真实凭据。

运行：python -X utf8 -B -m pytest quant_backtest/tests/test_osleg_http_contract.py -q
"""
from __future__ import annotations

import ast
import inspect
import json
import tokenize
from pathlib import Path

import pytest

from quant_backtest.agent_bridge import osleg_contract as oc
from quant_backtest.agent_bridge import osleg_http_contract as hc
from quant_backtest.agent_bridge.osleg_contract import OslegInbox

# 纯合成占位 token, 不是任何真实凭据
TOKEN = "synthetic-token-na-001"
REPORT = "QR-20261026-001"
TRACE = "tr-20261026-001"
KIND = "stock_consult_result"
PAYLOAD = {"code": "605100", "signal": "3buy"}

# 字符串外禁止出现的全角标点集合
_FULLWIDTH_PUNCT = set("，。；：！？（）【】《》、「」『』“”‘’…—～·．")


class FakeTransport:
    """伪 transport: 直接返回 dict 响应, 不发起任何真实 HTTP 请求。"""

    def __init__(self, peer, fault=None):
        self.peer = peer
        self.fault = fault
        self.sent = []

    def send(self, request):
        assert isinstance(request, dict)
        self.sent.append(request)
        if self.fault is not None:
            return self.fault
        return self.peer.handle(request)


def _peer():
    return hc.OslegHttpPeer()


def _submit_req(**over):
    kw = {
        "kind": KIND,
        "payload": dict(PAYLOAD),
        "source": "qs1",
        "report_id": REPORT,
        "trace_id": TRACE,
        "token": TOKEN,
        "idem_key": "idem-submit-001",
    }
    kw.update(over)
    return hc.build_submit_request(**kw)


def _roundtrip(transport, request, parser):
    status, body = transport.send(request)
    return status, body, parser(status, body)


# ---------------------------------------------------------------- schema
def test_endpoint_schemas_cover_channel2_contract():
    """README 口径: 五个端点的 method/path/headers/body 字段必须齐备。"""
    assert list(hc.ENDPOINTS) == ["submit", "consume", "answer", "deliver", "receipt"]
    for name, schema in hc.ENDPOINTS.items():
        assert schema.name == name
        assert schema.path.startswith(hc.BASE_PATH + "/")
        assert "://" not in schema.path
        assert hc.TOKEN_HEADER in schema.required_headers
        assert hc.SOURCE_HEADER in schema.required_headers
    submit = hc.ENDPOINTS["submit"]
    assert submit.method == "POST"
    assert submit.success_http == 201
    assert submit.success_state == "submitted"
    for field in ("kind", "payload", "source", "report_id", "trace_id"):
        assert field in submit.required_body
    for name in ("submit", "consume", "answer", "deliver"):
        assert hc.IDEMPOTENCY_HEADER in hc.ENDPOINTS[name].required_headers
    assert hc.ENDPOINTS["receipt"].method == "GET"
    assert hc.ENDPOINTS["receipt"].idempotent is False
    assert hc.ENDPOINTS["answer"].success_state == "answered"
    assert hc.ENDPOINTS["deliver"].success_state == "delivered"
    assert hc.CHANNEL_NAMESPACE == oc.CHANNEL_HTTP == "http8795"
    assert hc.MSG_ID_PREFIX != hc.INBOX_MSG_ID_PREFIX


def test_submit_request_shape_and_headers():
    """请求信封: token 只进请求头, 不进 body; trace/report 原样传递。"""
    request = _submit_req()
    assert request["method"] == "POST"
    assert request["path"] == "/osleg/v1/submit"
    headers = request["headers"]
    assert headers[hc.TOKEN_HEADER] == TOKEN
    assert headers[hc.SOURCE_HEADER] == "qs1"
    assert headers[hc.IDEMPOTENCY_HEADER] == "idem-submit-001"
    body = request["body"]
    assert body["kind"] == KIND
    assert body["payload"] == PAYLOAD
    assert body["source"] == "qs1"
    assert body["report_id"] == REPORT
    assert body["trace_id"] == TRACE
    assert TOKEN not in json.dumps(body, ensure_ascii=False)
    assert "token" not in json.dumps(request["query"], ensure_ascii=False)


def test_idempotency_key_is_deterministic_and_body_sensitive():
    """幂等键纯函数: 同体同键、异体异键、通道参与命名空间。"""
    body = _submit_req()["body"]
    first = hc.idempotency_key("submit", body)
    second = hc.idempotency_key("submit", dict(body))
    assert first == second
    assert first.startswith("w4h-idem-")
    changed = dict(body, payload={"code": "000001"})
    assert hc.idempotency_key("submit", changed) != first
    assert hc.idempotency_key("submit", body, channel=oc.CHANNEL_INBOX) != first


# ---------------------------------------------------------------- 正例
def test_submit_acceptance_maps_to_submitted():
    """正常受理: 201 + submitted, accounted=True, msg_id 属 http8795 命名空间。"""
    peer = _peer()
    transport = FakeTransport(peer)
    status, body, parsed = _roundtrip(transport, _submit_req(),
                                      hc.parse_submit_response)
    assert status == 201
    assert body["status"] == "submitted"
    assert len(transport.sent) == 1
    assert parsed["ok"] is True
    assert parsed["status"] == "submitted"
    assert parsed["accounted"] is True
    assert parsed["retryable"] is False
    assert parsed["msg_id"].startswith(hc.MSG_ID_PREFIX)
    assert parsed["report_id"] == REPORT
    assert parsed["trace_id"] == TRACE
    assert parsed["channel"] == "http8795"
    assert parsed["delivered_claimed"] is False
    assert peer.count == 1
    record = peer.get(parsed["msg_id"])
    assert record["channel"] == oc.CHANNEL_HTTP
    assert record["state"] == "submitted"
    assert TOKEN not in json.dumps(record, ensure_ascii=False)


def test_http_lifecycle_consume_answer_deliver_receipt():
    """通道②全链: submitted -> consumed -> answered -> delivered -> receipt。"""
    peer = _peer()
    transport = FakeTransport(peer)
    _, _, submitted = _roundtrip(transport, _submit_req(),
                                 hc.parse_submit_response)
    msg_id = submitted["msg_id"]

    status, _, consumed = _roundtrip(transport, hc.build_consume_request(
        msg_id, "A0", TRACE, token=TOKEN, source="a0",
        idem_key="idem-consume-001"), hc.parse_consume_response)
    assert status == 200 and consumed["status"] == "consumed"
    assert consumed["accounted"] is True

    status, _, answered = _roundtrip(transport, hc.build_answer_request(
        msg_id, "A0", "3买成立, 观察 2 日", REPORT, TRACE, token=TOKEN,
        source="a0", idem_key="idem-answer-001"), hc.parse_answer_response)
    assert status == 200 and answered["status"] == "answered"
    assert answered["linked"] is True

    status, _, delivered = _roundtrip(transport, hc.build_deliver_request(
        msg_id, "synthetic", "ref:synthetic-1", TRACE, token=TOKEN,
        source="a0", idem_key="idem-deliver-001"), hc.parse_deliver_response)
    assert status == 200 and delivered["status"] == "delivered"
    assert delivered["delivered_claimed"] is True

    status, _, receipt = _roundtrip(transport, hc.build_receipt_request(
        token=TOKEN, source="a0", msg_id=msg_id), hc.parse_receipt_response)
    assert status == 200 and receipt["ok"] is True
    assert receipt["status"] == "delivered"
    assert receipt["record_states"] == ["delivered"]
    assert peer.count == 1


def test_answer_without_matching_report_rejected_linked_false():
    """契约 §五-1 / §六 N4: 答复必须回引原 report_id, 否则拒收且不写 answered。"""
    peer = _peer()
    transport = FakeTransport(peer)
    _, _, submitted = _roundtrip(transport, _submit_req(),
                                 hc.parse_submit_response)
    msg_id = submitted["msg_id"]
    _roundtrip(transport, hc.build_consume_request(
        msg_id, "A0", TRACE, token=TOKEN, source="a0",
        idem_key="idem-consume-001"), hc.parse_consume_response)

    status, _, mismatch = _roundtrip(transport, hc.build_answer_request(
        msg_id, "A0", "答复", "QR-OTHER", TRACE, token=TOKEN, source="a0",
        idem_key="idem-answer-bad-001"), hc.parse_answer_response)
    assert status == 422
    assert mismatch["ok"] is False
    assert mismatch["error"] == "report_id_mismatch"
    assert mismatch["linked"] is False
    assert mismatch["accounted"] is False
    assert peer.get(msg_id)["state"] == "consumed"

    status, _, missing = _roundtrip(transport, hc.build_answer_request(
        msg_id, "A0", "答复", "", TRACE, token=TOKEN, source="a0",
        idem_key="idem-answer-missing-001"), hc.parse_answer_response)
    assert status == 422
    assert missing["error"] == "report_id_missing"
    assert missing["linked"] is False
    assert peer.get(msg_id)["state"] == "consumed"


# ---------------------------------------------------------------- 鉴权负例
def test_missing_token_rejected_401_without_accounting():
    """缺 token -> 401 拒绝, 不落账; 请求头里不伪造鉴权头。"""
    peer = _peer()
    transport = FakeTransport(peer)
    request = _submit_req(token="")
    assert hc.TOKEN_HEADER not in request["headers"]
    status, _, parsed = _roundtrip(transport, request, hc.parse_submit_response)
    assert status == 401
    assert parsed["http_status"] == 401
    assert parsed["ok"] is False
    assert parsed["status"] == "rejected"
    assert parsed["error"] == "token_missing"
    assert parsed["accounted"] is False
    assert parsed["retryable"] is False
    assert peer.count == 0
    assert peer.records() == []


def test_placeholder_token_rejected_401():
    """默认/占位凭据一律拒绝, 防止被当成生产 token。"""
    peer = _peer()
    transport = FakeTransport(peer)
    status, _, parsed = _roundtrip(transport, _submit_req(token="changeme"),
                                   hc.parse_submit_response)
    assert status == 401
    assert parsed["error"] == "token_placeholder"
    assert parsed["accounted"] is False
    assert peer.count == 0


def test_source_not_in_whitelist_rejected_403():
    """来源不在白名单 -> 403 拒绝, 不落账; validate_auth 大小写不敏感。"""
    peer = _peer()
    transport = FakeTransport(peer)
    status, _, parsed = _roundtrip(transport, _submit_req(source="external"),
                                   hc.parse_submit_response)
    assert status == 403
    assert parsed["http_status"] == 403
    assert parsed["ok"] is False
    assert parsed["error"] == "source_forbidden"
    assert parsed["accounted"] is False
    assert peer.count == 0

    auth = hc.validate_auth({"x-osleg-auth": TOKEN, "X-OSLEG-Source": "qs1"})
    assert auth["ok"] is True and auth["source"] == "qs1"
    bad_source = hc.validate_auth({"X-OSLEG-Auth": TOKEN,
                                   "X-OSLEG-Source": "external"})
    assert bad_source["ok"] is False and bad_source["http_status"] == 403
    allowed = hc.validate_auth({"X-OSLEG-Auth": TOKEN,
                                "X-OSLEG-Source": "external"},
                               allow=("external",))
    assert allowed["ok"] is True
    assert hc.validate_auth({"X-OSLEG-Source": "qs1"})["error"] == "token_missing"


def test_body_source_must_match_auth_header_source():
    """请求体 source 与鉴权头来源不一致 -> 403 source_mismatch, 不落账。"""
    peer = _peer()
    transport = FakeTransport(peer)
    request = _submit_req()
    request["body"]["source"] = "a0"
    status, _, parsed = _roundtrip(transport, request, hc.parse_submit_response)
    assert status == 403
    assert parsed["error"] == "source_mismatch"
    assert parsed["accounted"] is False
    assert peer.count == 0


# ---------------------------------------------------------------- 幂等
def test_idempotent_replay_returns_same_msg_id():
    """同键同体重放 -> 同一 msg_id, replayed/duplicate 标记, 不产生第二条记录。"""
    peer = _peer()
    transport = FakeTransport(peer)
    first_status, _, first = _roundtrip(transport, _submit_req(),
                                        hc.parse_submit_response)
    second_status, _, second = _roundtrip(transport, _submit_req(),
                                          hc.parse_submit_response)
    assert first_status == 201
    assert second_status == 200
    assert second["replayed"] is True
    assert second["duplicate"] is True
    assert second["ok"] is True
    assert second["msg_id"] == first["msg_id"]
    assert peer.count == 1
    assert peer.get(first["msg_id"])["duplicates"] == 1


def test_idempotency_conflict_409_on_same_key_different_body():
    """同键异体 -> 409 冲突拒收, 不偷换报告, 不新增记录。"""
    peer = _peer()
    transport = FakeTransport(peer)
    _, _, first = _roundtrip(transport, _submit_req(),
                             hc.parse_submit_response)
    conflicting = _submit_req(payload={"code": "605100", "signal": "sell"})
    assert conflicting["headers"][hc.IDEMPOTENCY_HEADER] == "idem-submit-001"
    status, _, parsed = _roundtrip(transport, conflicting,
                                   hc.parse_submit_response)
    assert status == 409
    assert parsed["http_status"] == 409
    assert parsed["ok"] is False
    assert parsed["status"] == "rejected"
    assert parsed["error"] == "idempotency_conflict"
    assert parsed["accounted"] is False
    assert parsed["conflict_with"] == first["msg_id"]
    assert peer.count == 1
    assert peer.get(first["msg_id"])["payload"] == PAYLOAD


# ---------------------------------------------------------------- 失败关闭
def test_missing_trace_id_rejected_unknown_without_default_backfill():
    """§六 N5: 缺 trace_id -> status=unknown, 不落账, 不回填默认值。"""
    peer = _peer()
    transport = FakeTransport(peer)
    request = _submit_req(trace_id="")
    assert request["body"]["trace_id"] == ""
    status, _, parsed = _roundtrip(transport, request, hc.parse_submit_response)
    assert status == 422
    assert parsed["ok"] is False
    assert parsed["status"] == "unknown"
    assert parsed["error"] == "trace_id_missing"
    assert parsed["accounted"] is False
    assert parsed["retryable"] is False
    assert parsed["trace_id"] == ""
    assert peer.count == 0


def test_missing_report_id_rejected_unknown_without_default_backfill():
    """缺 report_id 同样失败关闭: status=unknown, 不落账, 不猜业务单。"""
    peer = _peer()
    transport = FakeTransport(peer)
    request = _submit_req(report_id="")
    assert request["body"]["report_id"] == ""
    status, _, parsed = _roundtrip(transport, request, hc.parse_submit_response)
    assert status == 422
    assert parsed["ok"] is False
    assert parsed["status"] == "unknown"
    assert parsed["error"] == "report_id_missing"
    assert parsed["accounted"] is False
    assert parsed["report_id"] == ""
    assert peer.count == 0


def test_4xx_never_accounts_and_keeps_ledger_unchanged():
    """4xx (401/403/409/422) 一律不落账, 账本条数不变。"""
    peer = _peer()
    transport = FakeTransport(peer)
    _roundtrip(transport, _submit_req(), hc.parse_submit_response)
    assert peer.count == 1

    scenarios = []
    scenarios.append(_roundtrip(transport, _submit_req(token=""),
                                hc.parse_submit_response))
    scenarios.append(_roundtrip(transport, _submit_req(source="external"),
                                hc.parse_submit_response))
    scenarios.append(_roundtrip(
        transport, _submit_req(payload={"code": "605100", "signal": "sell"}),
        hc.parse_submit_response))
    scenarios.append(_roundtrip(
        transport, _submit_req(trace_id="", idem_key="idem-4xx-trace-001"),
        hc.parse_submit_response))

    codes = [status for status, _, _ in scenarios]
    assert codes == [401, 403, 409, 422]
    for _, _, parsed in scenarios:
        assert parsed["ok"] is False
        assert parsed["accounted"] is False
        assert parsed["retryable"] is False
    assert peer.count == 1


def test_5xx_maps_to_unknown_retryable_and_not_accounted():
    """5xx -> status=unknown + retryable=True, 不落账。"""
    peer = _peer()
    transport = FakeTransport(peer)
    _roundtrip(transport, _submit_req(), hc.parse_submit_response)
    assert peer.count == 1

    transport.fault = (503, {"ok": False, "error": "server_error"})
    status, _, parsed = _roundtrip(
        transport, _submit_req(idem_key="idem-5xx-001"),
        hc.parse_submit_response)
    assert status == 503
    assert parsed["ok"] is False
    assert parsed["status"] == "unknown"
    assert parsed["error"] == "server_error"
    assert parsed["accounted"] is False
    assert parsed["retryable"] is True
    assert peer.count == 1

    direct = hc.parse_submit_response(500, {"ok": False,
                                            "error": "internal_error"})
    assert direct["status"] == "unknown"
    assert direct["retryable"] is True
    assert direct["accounted"] is False


def test_2xx_response_missing_trace_id_fails_closed():
    """响应缺 trace 回显同样不得回填: status=unknown。"""
    parsed = hc.parse_submit_response(201, {"ok": True, "msg_id": "w4h-x",
                                            "report_id": REPORT})
    assert parsed["ok"] is False
    assert parsed["status"] == "unknown"
    assert parsed["error"] == "trace_id_missing"
    assert parsed["accounted"] is False
    assert parsed["trace_id"] == ""


def test_interface_acceptance_never_claims_delivered():
    """接口受理 (2xx) 不得表述为已送达/客户已读。"""
    accepted = hc.parse_submit_response(202, {
        "ok": True, "msg_id": "w4h-x", "report_id": REPORT,
        "trace_id": TRACE})
    assert accepted["ok"] is True
    assert accepted["status"] == "accepted"
    assert accepted["state_source"] == "http_default"
    assert accepted["delivered_claimed"] is False
    assert "read" not in hc.W4_STATES
    generic = hc.parse_submit_response(200, {
        "ok": True, "msg_id": "w4h-x", "report_id": REPORT,
        "trace_id": TRACE})
    assert generic["status"] == "accepted"
    assert generic["delivered_claimed"] is False


def test_unexpected_3xx_is_unknown_and_not_accounted():
    """非 2xx/4xx/5xx 一律 unknown 且不可重试标记, 不落账。"""
    parsed = hc.parse_submit_response(302, {"ok": True})
    assert parsed["status"] == "unknown"
    assert parsed["accounted"] is False
    assert parsed["retryable"] is False


def test_response_envelope_covers_all_w4_states():
    """响应信封可映射 W4 全状态 (submitted/accepted/consumed/answered/
    delivered/rejected/expired)。"""
    base = {"ok": True, "msg_id": "w4h-x", "report_id": REPORT,
            "trace_id": TRACE}
    cases = {
        "submitted": hc.parse_response(201, dict(base)),
        "accepted": hc.parse_response(202, dict(base)),
        "consumed": hc.parse_response(200, dict(base, status="consumed")),
        "answered": hc.parse_response(200, dict(base, status="answered")),
        "delivered": hc.parse_response(200, dict(base, status="delivered")),
        "rejected": hc.parse_response(422, {"ok": False, "status": "rejected",
                                            "error": "report_id_mismatch"}),
        "expired": hc.parse_response(410, {"ok": False, "error": "expired"}),
    }
    for expected, parsed in cases.items():
        assert parsed["status"] == expected
    assert cases["delivered"]["delivered_claimed"] is True
    assert cases["rejected"]["accounted"] is False
    assert cases["expired"]["accounted"] is False
    assert set(hc.W4_STATES) == set(oc.STATES)


def test_trace_must_run_through_consume():
    """trace_id 贯穿: 消费阶段 trace 不一致 -> unknown, 状态不变。"""
    peer = _peer()
    transport = FakeTransport(peer)
    _, _, submitted = _roundtrip(transport, _submit_req(),
                                 hc.parse_submit_response)
    msg_id = submitted["msg_id"]
    status, _, parsed = _roundtrip(transport, hc.build_consume_request(
        msg_id, "A0", "tr-other", token=TOKEN, source="a0",
        idem_key="idem-consume-bad-trace-001"), hc.parse_consume_response)
    assert status == 422
    assert parsed["status"] == "unknown"
    assert parsed["error"] == "trace_mismatch"
    assert peer.get(msg_id)["state"] == "submitted"


# ---------------------------------------------------------------- 双通道
def test_dual_channel_distinct_msg_id_same_report_and_trace(tmp_path):
    """§六 N6: inbox 与 http8795 异 msg_id, 同 report_id/trace_id。"""
    inbox = OslegInbox(tmp_path / "osleg")
    inbox_env = inbox.submit(kind=KIND, payload=dict(PAYLOAD), source="qs1",
                             report_id=REPORT, trace_id=TRACE,
                             channel=oc.CHANNEL_INBOX)
    assert inbox_env["ok"] is True
    assert inbox_env["msg_id"].startswith(hc.INBOX_MSG_ID_PREFIX)

    peer = _peer()
    transport = FakeTransport(peer)
    _, _, http_env = _roundtrip(transport, _submit_req(),
                                hc.parse_submit_response)
    assert http_env["msg_id"].startswith(hc.MSG_ID_PREFIX)
    assert http_env["msg_id"] != inbox_env["msg_id"]
    inbox_record = inbox.receipt(msg_id=inbox_env["msg_id"])
    assert inbox_record["report_id"] == http_env["report_id"] == REPORT
    assert inbox_record["trace_id"] == http_env["trace_id"] == TRACE
    assert inbox_record["channel"] == oc.CHANNEL_INBOX
    assert peer.get(http_env["msg_id"])["channel"] == oc.CHANNEL_HTTP

    inbox_receipt = inbox.receipt(report_id=REPORT)
    assert inbox_receipt["channels"] == [oc.CHANNEL_INBOX]
    status, _, http_receipt = _roundtrip(
        transport, hc.build_receipt_request(token=TOKEN, source="a0",
                                            report_id=REPORT),
        hc.parse_receipt_response)
    assert status == 200
    assert http_receipt["ok"] is True
    assert http_receipt["msg_ids"] == [http_env["msg_id"]]
    assert http_receipt["channels"] == [oc.CHANNEL_HTTP]
    assert http_receipt["trace_consistent"] is True
    assert set(inbox_receipt["channels"]) | set(http_receipt["channels"]) == {
        oc.CHANNEL_INBOX, oc.CHANNEL_HTTP}


def test_receipt_unknown_msg_id_is_not_accounted():
    """receipt 查不到 -> 404 rejected, 不落账。"""
    parsed = hc.parse_receipt_response(404, {"ok": False, "error": "not_found"})
    assert parsed["ok"] is False
    assert parsed["status"] == "rejected"
    assert parsed["accounted"] is False


# ---------------------------------------------------------------- 离线/安全
def _module_source():
    return inspect.getsource(hc)


def test_module_has_no_network_imports_urls_or_ports():
    """源码断言: 无网络 import、无 URL、无 8795 监听/绑定。"""
    source = _module_source()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                imported.add(node.module)
    forbidden_roots = {"socket", "urllib", "requests", "http", "aiohttp",
                       "httpx", "ssl", "smtplib", "ftplib", "telnetlib",
                       "xmlrpc"}
    bad = {name for name in imported if name.split(".")[0] in forbidden_roots}
    assert bad == set()

    constants = [node.value for node in ast.walk(tree)
                 if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    assert [value for value in constants if "://" in value] == []
    assert [value for value in constants if "127.0.0.1" in value] == []
    numbers = [node.value for node in ast.walk(tree)
               if isinstance(node, ast.Constant)
               and isinstance(node.value, int)
               and not isinstance(node.value, bool)]
    assert 8795 not in numbers
    for token in ("serve_forever", "listen(", ".bind(", "urlopen"):
        assert token not in source, token
    assert "os.environ" not in source


def test_module_has_no_hardcoded_credentials_and_peer_is_memory_only():
    """无硬编码凭据、无落盘: 测试 token 不出现在模块源码里。"""
    source = _module_source()
    assert TOKEN not in source
    assert "Bearer " not in source
    for token in ("write_text", "write_bytes", "open(", "Path("):
        assert token not in source, token
    peer = _peer()
    peer.handle(_submit_req())
    assert peer.count == 1
    assert peer.requests_seen == 1


def _source_paths():
    return [Path(hc.__file__).resolve(), Path(__file__).resolve()]


@pytest.mark.parametrize("path", _source_paths())
def test_style_ascii_code_tokens_and_no_fullwidth_punct_outside_strings(path):
    """风格: 字符串外代码 token 全 ASCII, 注释不得含全角标点。"""
    with path.open("rb") as handle:
        tokens = list(tokenize.tokenize(handle.readline))
    skip = (tokenize.STRING, tokenize.ENCODING, tokenize.NL,
            tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
            tokenize.ENDMARKER)
    for token in tokens:
        if token.type in skip:
            continue
        if token.type == tokenize.COMMENT:
            found = sorted({char for char in token.string
                            if char in _FULLWIDTH_PUNCT})
            assert not found, "字符串外出现全角标点 %s @%s" % (
                found, token.start)
            continue
        assert token.string.isascii(), "代码 token 含非 ASCII @%s: %r" % (
            token.start, token.string)
