# -*- coding: utf-8 -*-
"""P0 鉴权整改专项回归（2026-10-19 议事厅 G2 前置）。

覆盖四项修复：
1. X-Real-IP 仅在经本门户 nginx 反代（X-Portal-Proxy: 1）时采信；
   直连时自报 X-Real-IP: 127.0.0.1 不得越权。
2. 三方会议列表/详情 GET 需读鉴权（本机可信域或持匹配 token）。
3. Access-Control-Allow-Origin 不再通配 *，仅回显白名单来源。
4. confirmed_by 必填且按"自报署名"落来源属性（IP/经反代/鉴权方式）。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "portal"))

import api_legion_ops_v2 as ops

PASS = []


def check(name, cond):
    if not cond:
        raise AssertionError("FAIL: " + name)
    PASS.append(name)


def fake_handler(headers, client_ip):
    h = object.__new__(ops.H)
    h.headers = dict(headers)
    h.client_address = (client_ip, 55555)
    return h


# ── 1. authz_ip_allowed 纯函数 ──────────────────────────────
check("本机 IPv4 可信", ops.authz_ip_allowed("127.0.0.1"))
check("本机 IPv6 可信", ops.authz_ip_allowed("::1"))
check("本机映射 IPv6 可信", ops.authz_ip_allowed("::ffff:127.0.0.1"))
check("docker 网关（本机经反代 SNAT）可信", ops.authz_ip_allowed("172.17.0.1"))
check("局域网 IP 不可信", not ops.authz_ip_allowed("192.168.1.5"))
check("空 IP 不可信", not ops.authz_ip_allowed(""))
check("仿冒环回字符串带空格仍按本机", ops.authz_ip_allowed(" 127.0.0.1 "))
check("伪造头值 127.0.0.1 以外不误判", not ops.authz_ip_allowed("127.0.0.2"))

# ── 2. _client_ip：直连不采信自报 X-Real-IP ─────────────────
h = fake_handler({"X-Real-IP": "127.0.0.1"}, "10.9.8.7")
check("直连时忽略自报 X-Real-IP", h._client_ip() == "10.9.8.7")

h = fake_handler({"X-Portal-Proxy": "1", "X-Real-IP": "203.0.113.9"}, "192.168.50.6")
check("经反代采信 nginx 覆写的 X-Real-IP", h._client_ip() == "203.0.113.9")

h = fake_handler({}, "127.0.0.1")
check("直连本机取 TCP 对端", h._client_ip() == "127.0.0.1")

# ── 3. _authorized_mutation 组合判定 ────────────────────────
orig_token = ops._load_token
try:
    ops._load_token = lambda: ""
    h = fake_handler({}, "127.0.0.1")
    check("无 token：本机直连放行", h._authorized_mutation())
    h = fake_handler({"X-Real-IP": "127.0.0.1"}, "10.9.8.7")
    check("无 token：直连伪造环回头拒绝", not h._authorized_mutation())

    ops._load_token = lambda: "s3cret"
    h = fake_handler({}, "127.0.0.1")
    check("有 token：本机直连仍放行", h._authorized_mutation())
    h = fake_handler({"X-Portal-Proxy": "1", "X-Real-IP": "127.0.0.1"}, "172.17.0.2")
    check("有 token：经反代本机浏览器放行", h._authorized_mutation())
    h = fake_handler(
        {"X-Portal-Proxy": "1", "X-Real-IP": "192.168.1.5", "X-Portal-Token": "s3cret"},
        "172.17.0.2",
    )
    check("有 token：反代局域网持正确 token 放行", h._authorized_mutation())
    h = fake_handler(
        {"X-Portal-Proxy": "1", "X-Real-IP": "192.168.1.5", "X-Portal-Token": "wrong"},
        "172.17.0.2",
    )
    check("有 token：错误 token 拒绝", not h._authorized_mutation())
    h = fake_handler({"X-Portal-Proxy": "1", "X-Real-IP": "192.168.1.5"}, "172.17.0.2")
    check("有 token：反代局域网无 token 拒绝", not h._authorized_mutation())

    # ── 4. 私人会议读鉴权与写鉴权同规则 ──
    h = fake_handler({"X-Portal-Proxy": "1", "X-Real-IP": "192.168.1.5"}, "172.17.0.2")
    check("私人会议读：局域网无 token 拒绝", not h._authorized_private_read())
finally:
    ops._load_token = orig_token

# ── 5. CORS 白名单 ──────────────────────────────────────────
h = fake_handler({"Origin": "http://localhost:8080"}, "127.0.0.1")
check("CORS：白名单来源回显", h._cors_origin() == "http://localhost:8080")
h = fake_handler({"Origin": "http://evil.example"}, "127.0.0.1")
check("CORS：陌生来源不下发", h._cors_origin() is None)
h = fake_handler({}, "127.0.0.1")
check("CORS：无 Origin 不下发", h._cors_origin() is None)

# ── 6. confirmed_by 必填与来源属性落账 ──────────────────────
orig_update = ops.update_confirmation
try:
    captured = {}

    def fake_update(mid, status, by, note=""):
        captured.update(meeting_id=mid, status=status, confirmed_by=by, note=note)
        return {"meeting_id": mid, "confirmation": {"status": status}}

    ops.update_confirmation = fake_update

    r = ops.tri_party_meeting_confirm(
        {"meeting_id": "m1", "status": "confirmed", "confirmed_by": "", "note": "ok"}
    )
    check("空署名拒绝", not r["ok"] and "confirmed_by" in r["err"])

    r = ops.tri_party_meeting_confirm(
        {
            "meeting_id": "m1",
            "status": "confirmed",
            "confirmed_by": "老田",
            "note": "看过了",
        },
        client_ip="127.0.0.1",
        via_proxy=False,
        token_matched=False,
    )
    check("署名透传", captured["confirmed_by"] == "老田")
    check(
        "note 并入自报标记",
        "署名来源:自报" in captured["note"] and "看过了" in captured["note"],
    )
    check(
        "来源属性含 IP 与鉴权方式",
        "IP:127.0.0.1" in captured["note"] and "鉴权:本机可信域" in captured["note"],
    )
    check("经反代标记为否", "经反代:否" in captured["note"])

    r = ops.tri_party_meeting_confirm(
        {
            "meeting_id": "m2",
            "status": "rejected",
            "confirmed_by": "田丰代录",
            "note": "",
        },
        client_ip="192.168.1.5",
        via_proxy=True,
        token_matched=True,
    )
    check(
        "token 鉴权方式如实落账",
        "鉴权:token" in captured["note"]
        and "经反代:是" in captured["note"]
        and "IP:192.168.1.5" in captured["note"],
    )

    long_note = "x" * 600
    note = ops.confirmation_provenance_note(long_note, "127.0.0.1", False, False)
    check("note 截断至 500 内", len(note) <= 500)
finally:
    ops.update_confirmation = orig_update

print(
    "PASS %d checks: authz pure fn, direct-connection spoof ignored, "
    "proxy-token model, private read gate, CORS allowlist, provenance note" % len(PASS)
)
