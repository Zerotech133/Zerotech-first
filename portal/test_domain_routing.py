# -*- coding: utf-8 -*-
"""P2 分域（FC-A）：domain 一级路由字段单测。

覆盖五块改动：
  1. push_flow.record() 可选 domain 参数——jsonl 行缺省不写 domain 键
     （旧调用零变化），domain 非 None 时写键；
  2. notify_contracts 分域常量（DOMAINS / DOMAIN_LABELS / blocked kind）；
  3. api_notify item domain 徽章——存在才渲染，缺省不出现
     （flow_items 今日 ok 聚合 / 失败簇 / 闸门抑制三路径 + 旧行兼容）；
  4. feishu_client 透传链——_hub_send body_obj["domain"]（缺省不发字段）、
     send_msg_tracked 签名加参并向 hub 分支转发（urlopen 全桩，零真发）；
  5. push_admin_server api_send——domain 校验（非法拒）、表驱动路由
     （owner/ops 放行，customer observe 留痕不拦截）、缺省兼容
     （feishu_client 与 push_flow.record 全桩，零真发零污染）。

锚点解封补齐（2026-10-20）：此前 feishu_client.py / push_admin_server.py
读取被平台审批拦截以 skip 占位；主会话提供锚点原文后改动落地，本文件
取消 skip 补全实现断言。
"""

import io
import json
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))  # 仓库根（push_flow.py 在根目录）
sys.path.insert(0, str(HERE.parent / "news_collector"))  # feishu_client

import api_notify
import feishu_client as fc
import notify_contracts as nc
import push_admin_server as admin
import push_flow

TODAY = datetime.now().strftime("%Y-%m-%d")
NOW_ISO = datetime.now().isoformat(timespec="seconds")


# ---------- 工具 ----------


@pytest.fixture
def flow_path(tmp_path, monkeypatch):
    """FLOW_PATH 锚到 tmp，绝不污染真实流水账 portal/work/push_flow.jsonl。"""
    p = tmp_path / "push_flow.jsonl"
    monkeypatch.setattr(push_flow, "FLOW_PATH", str(p))
    return p


def read_rows(p):
    return [
        json.loads(line)
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ---------- 1. push_flow.record jsonl 行格式 ----------


def test_record_with_domain_writes_key(flow_path):
    """domain 非 None → 行内写 domain 键（分域一级字段落账）。"""
    push_flow.record("daily_digest", "feishu", "ou_owner", domain="owner")
    (row,) = read_rows(flow_path)
    assert row["domain"] == "owner"
    # 基础键不受影响
    assert row["kind"] == "daily_digest"
    assert row["ok"] is True
    assert row["date"] == TODAY


def test_record_without_domain_no_key(flow_path):
    """旧调用不带 domain → 行结构零变化（向后兼容硬约束）。"""
    push_flow.record("daily_digest", "feishu", "ou_owner")
    (row,) = read_rows(flow_path)
    assert "domain" not in row


def test_record_domain_none_no_key(flow_path):
    """显式 domain=None 与缺省等价：不写键。"""
    push_flow.record("k", "feishu", "t", domain=None)
    (row,) = read_rows(flow_path)
    assert "domain" not in row


def test_record_domain_with_evidence_coexist(flow_path):
    """evidence 与 domain 两个可选键互不影响、可同时落行。"""
    push_flow.record("k", "feishu", "t", evidence="api_accepted", domain="customer")
    (row,) = read_rows(flow_path)
    assert row["evidence"] == "api_accepted"
    assert row["domain"] == "customer"


# ---------- 2. notify_contracts 分域常量 ----------


def test_domains_tuple():
    assert nc.DOMAINS == ("owner", "ops", "customer")


def test_domain_labels():
    assert nc.DOMAIN_LABELS == {"owner": "本人", "ops": "运维", "customer": "客户"}


def test_blocked_kind_subscription_required():
    assert nc.BLOCKED_KIND_SUBSCRIPTION_REQUIRED == "subscription_required"


# ---------- 3. api_notify item domain 徽章 ----------


def test_item_default_no_domain_key():
    """_item 缺省：payload 键集合不含 domain（旧结构零变化）。"""
    it = api_notify._item(id="x", state="auto", title="t")
    assert "domain" not in it


def test_item_with_domain_renders():
    it = api_notify._item(id="x", state="auto", title="t", domain="ops")
    assert it["domain"] == "ops"


def test_item_empty_domain_not_rendered():
    """domain 传空串视为缺省：不渲染。"""
    it = api_notify._item(id="x", state="auto", title="t", domain="")
    assert "domain" not in it


def _flow_root(tmp_path, monkeypatch, rows):
    monkeypatch.setenv("AIOS_ROOT", str(tmp_path))
    (tmp_path / "portal" / "work").mkdir(parents=True)
    p = tmp_path / "portal" / "work" / "push_flow.jsonl"
    p.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )


def test_flow_items_today_ok_domain_badge(tmp_path, monkeypatch):
    """今日 ok 行带 domain → auto item 徽章透出。"""
    _flow_root(
        tmp_path,
        monkeypatch,
        [
            {
                "ts": NOW_ISO,
                "date": TODAY,
                "kind": "daily_digest",
                "channel": "feishu",
                "target": "ou_a",
                "count": 1,
                "ok": True,
                "tenant": "default",
                "domain": "owner",
            },
        ],
    )
    items = api_notify.flow_items([])
    auto = [it for it in items if it["id"].startswith("flowauto:")]
    assert len(auto) == 1
    assert auto[0]["domain"] == "owner"


def test_flow_items_legacy_rows_no_domain_key(tmp_path, monkeypatch):
    """旧流水行（无 domain 键）→ item 亦无 domain 键（缺省不出现）。"""
    _flow_root(
        tmp_path,
        monkeypatch,
        [
            {
                "ts": NOW_ISO,
                "date": TODAY,
                "kind": "daily_digest",
                "channel": "feishu",
                "target": "ou_a",
                "count": 1,
                "ok": True,
                "tenant": "default",
            },
        ],
    )
    items = api_notify.flow_items([])
    auto = [it for it in items if it["id"].startswith("flowauto:")]
    assert len(auto) == 1
    assert "domain" not in auto[0]


def test_flow_items_multi_domain_merge(tmp_path, monkeypatch):
    """同 (kind,target) 今日聚合行混两域 → 域值排序逗号合并，不丢域。"""
    _flow_root(
        tmp_path,
        monkeypatch,
        [
            {
                "ts": NOW_ISO,
                "date": TODAY,
                "kind": "k1",
                "channel": "feishu",
                "target": "ou_a",
                "count": 1,
                "ok": True,
                "tenant": "default",
                "domain": "customer",
            },
            {
                "ts": NOW_ISO,
                "date": TODAY,
                "kind": "k1",
                "channel": "feishu",
                "target": "ou_a",
                "count": 1,
                "ok": True,
                "tenant": "default",
                "domain": "ops",
            },
        ],
    )
    items = api_notify.flow_items([])
    auto = [it for it in items if it["id"].startswith("flowauto:")]
    assert len(auto) == 1
    assert auto[0]["domain"] == "customer,ops"


def test_flow_items_blocked_row_domain(tmp_path, monkeypatch):
    """近 7 天失败行带 domain → blocked item 徽章透出（代表行取值）。"""
    _flow_root(
        tmp_path,
        monkeypatch,
        [
            {
                "ts": NOW_ISO,
                "date": TODAY,
                "kind": "k_fail",
                "channel": "feishu",
                "target": "ou_b",
                "count": 1,
                "ok": False,
                "tenant": "default",
                "domain": "customer",
            },
        ],
    )
    items = api_notify.flow_items([])
    blocked = [it for it in items if it["state"] == "blocked"]
    assert len(blocked) == 1
    assert blocked[0]["domain"] == "customer"


# ---------- 4. feishu_client 透传链（urlopen 全桩，零真发） ----------


class _FakeResp(io.BytesIO):
    """urlopen 返回体：BytesIO 原生支持 with 与 read。"""


def _fake_urlopen(capture, resp_body=None):
    def _open(rq, timeout=None):
        capture.append(rq)
        body = resp_body or json.dumps(
            {"code": 0, "msg": "ok", "data": {"message_id": "om_fake"}}
        ).encode("utf-8")
        return _FakeResp(body)

    return _open


def test_hub_send_domain_passthrough(tmp_path, monkeypatch):
    """_hub_send(domain=...) → 请求 body_obj 写 domain 一级字段。"""
    monkeypatch.setattr(push_flow, "FLOW_PATH", str(tmp_path / "f.jsonl"))
    cap = []
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(cap))
    fc._hub_send("hi", "text", "ou_a", "open_id", domain="owner")
    body = json.loads(cap[0].data.decode("utf-8"))
    assert body["domain"] == "owner"
    # 基础字段不受影响
    assert body["content"] == "hi" and body["msg_type"] == "text"


def test_hub_send_no_domain_no_field(tmp_path, monkeypatch):
    """缺省不带 domain → body 无 domain 键（存量调用零变化）。"""
    monkeypatch.setattr(push_flow, "FLOW_PATH", str(tmp_path / "f.jsonl"))
    cap = []
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(cap))
    fc._hub_send("hi", "text", "ou_a", "open_id")
    body = json.loads(cap[0].data.decode("utf-8"))
    assert "domain" not in body


def test_send_msg_tracked_forwards_domain_to_hub(monkeypatch):
    """send_msg_tracked(domain=...) → via_hub 分支转发给 _hub_send。"""
    cap = {}

    def fake_hub(
        content,
        msg_type,
        receive_id,
        receive_id_type,
        kind=None,
        critical=False,
        timeout=8,
        route_targets=None,
        domain=None,
    ):
        cap["domain"] = domain
        return (True, "ok", "om_x")

    monkeypatch.setattr(fc, "_hub_send", fake_hub)
    fc.send_msg_tracked("hi", "text", "ou_a", "open_id", via_hub=True, domain="ops")
    assert cap["domain"] == "ops"


def test_send_msg_tracked_domain_default_none(monkeypatch):
    """send_msg_tracked 缺省 → 转发 domain=None（兼容存量调用方）。"""
    cap = {}

    def fake_hub(
        content,
        msg_type,
        receive_id,
        receive_id_type,
        kind=None,
        critical=False,
        timeout=8,
        route_targets=None,
        domain=None,
    ):
        cap["domain"] = domain
        return (True, "ok", "om_x")

    monkeypatch.setattr(fc, "_hub_send", fake_hub)
    fc.send_msg_tracked("hi", "text", "ou_a", "open_id", via_hub=True)
    assert cap["domain"] is None


def test_send_msg_tracked_webhook_path_accepts_domain(tmp_path, monkeypatch):
    """via_hub=False 走 _agent_webhook_send：签名接受 domain 不炸；
    本期 webhook 路径不透传 domain（挂账语义锚定：body 无 domain 键）。"""
    monkeypatch.setattr(push_flow, "FLOW_PATH", str(tmp_path / "f.jsonl"))
    cap = []
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen(cap))
    fc.send_msg_tracked(
        "hi",
        "text",
        "ou_a",
        "open_id",
        via_hub=False,
        flow_record=False,
        domain="customer",
    )
    body = json.loads(cap[0].data.decode("utf-8"))
    assert "domain" not in body


# ---------- 5. push_admin_server api_send（feishu_client/record 全桩） ----------


def _req(**extra):
    base = {
        "content": "hello",
        "msg_type": "text",
        "receive_id": "ou_test",
        "critical": True,
    }
    base.update(extra)
    return base


def _api_send_env(monkeypatch, sent, records):
    """api_send 全桩：feishu_client 走 sys.modules 假模块（api_send 内
    局部 from feishu_client import），静默窗恒假、配额 internal 永不拦、
    push_flow.record 捕获 observe 留痕。"""

    def fake_send(content, msg_type, receive_id=None, receive_id_type="open_id", **kw):
        sent.append(
            {
                "content": content,
                "msg_type": msg_type,
                "receive_id": receive_id,
                "kw": kw,
            }
        )
        return (True, "ok", "om_t")

    fake_fc = types.SimpleNamespace(
        send_msg_tracked=fake_send, verify_message=lambda mid: True
    )
    monkeypatch.setitem(sys.modules, "feishu_client", fake_fc)
    monkeypatch.setattr(admin, "_in_silent_window", lambda now=None, kind="": False)
    monkeypatch.setattr(push_flow, "quota_check", lambda: ("internal", 0, 0, False))
    monkeypatch.setattr(push_flow, "record", lambda *a, **kw: records.append((a, kw)))


def test_api_send_domain_invalid_rejected(monkeypatch):
    """非法 domain → 拒绝且不触发送、不留痕。"""
    sent, records = [], []
    _api_send_env(monkeypatch, sent, records)
    r = admin.api_send(_req(domain="hacker"))
    assert r["ok"] is False
    assert r["error"] == "invalid domain"
    assert sent == [] and records == []


def test_api_send_customer_observe_records_no_block(monkeypatch):
    """customer 域 observe：留痕（channel=domain-observe）且放行不拦截，
    domain 透传到 send_msg_tracked。"""
    sent, records = [], []
    _api_send_env(monkeypatch, sent, records)
    r = admin.api_send(_req(domain="customer"))
    assert r["ok"] is True
    # observe 留痕恰一条（api_send 发送成功后另有统一流水 channel=openapi，
    # 不计入）；channel=domain-observe、domain 透传、不拦发送
    observes = [(a, kw) for a, kw in records if a[1] == "domain-observe"]
    assert len(observes) == 1
    a, kw = observes[0]
    assert a[1] == "domain-observe"
    assert a[2] == "ou_test"
    assert kw.get("domain") == "customer"
    assert len(sent) == 1
    assert sent[0]["kw"].get("domain") == "customer"


def test_api_send_owner_ops_allow_without_observe(monkeypatch):
    """owner/ops 放行且无 observe 留痕（表驱动：仅 customer 为 observe）。"""
    sent, records = [], []
    _api_send_env(monkeypatch, sent, records)
    assert admin.api_send(_req(domain="ops"))["ok"] is True
    assert admin.api_send(_req(domain="owner"))["ok"] is True
    # 非 customer 域不产生 observe 留痕（api_send 自身统一流水 openapi 除外）
    assert [a for a, _kw in records if a[1] == "domain-observe"] == []
    assert [s["kw"].get("domain") for s in sent] == ["ops", "owner"]


def test_api_send_no_domain_legacy_compat(monkeypatch):
    """旧调用不带 domain：放行、无 observe 留痕、透传 domain=None。"""
    sent, records = [], []
    _api_send_env(monkeypatch, sent, records)
    r = admin.api_send(_req())
    assert r["ok"] is True
    # 无 observe 留痕（api_send 自身统一流水 openapi 除外）
    assert [a for a, _kw in records if a[1] == "domain-observe"] == []
    assert len(sent) == 1
    assert sent[0]["kw"].get("domain") is None


def test_api_send_flow_records_message_id(monkeypatch):
    """2026-11-09：api_send 统一流水行必须带飞书回执 message_id（evidence 保留）。"""
    sent, records = [], []
    _api_send_env(monkeypatch, sent, records)
    r = admin.api_send(_req())
    assert r["ok"] is True
    assert r.get("message_id") == "om_t"
    flows = [kw for a, kw in records if a and a[1] == "openapi"]
    assert flows and flows[-1].get("message_id") == "om_t"
    assert flows[-1].get("evidence") == "receiver_verified"
