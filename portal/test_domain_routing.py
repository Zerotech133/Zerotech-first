# -*- coding: utf-8 -*-
"""P2 分域（FC-A）：domain 一级路由字段单测。

覆盖三块已落地改动：
  1. push_flow.record() 可选 domain 参数——jsonl 行缺省不写 domain 键
     （旧调用零变化），domain 非 None 时写键；
  2. notify_contracts 分域常量（DOMAINS / DOMAIN_LABELS / blocked kind）；
  3. api_notify item domain 徽章——存在才渲染，缺省不出现
     （flow_items 今日 ok 聚合 / 失败簇 / 闸门抑制三路径 + 旧行兼容）。

挂账占位（skip，见各用例 reason）：feishu_client.py 与 push_admin_server.py
的读取在本会话被平台审批拦截，改动未落地；透传链与 DOMAIN_ROUTES 表驱动
用例先以 skip 占位，落地后取消 skip 补跑（不造数不假绿）。
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))  # 仓库根（push_flow.py 在根目录）

import api_notify
import notify_contracts as nc
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
    return [json.loads(line) for line in
            p.read_text(encoding="utf-8").splitlines() if line.strip()]


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
    push_flow.record("k", "feishu", "t", evidence="api_accepted",
                     domain="customer")
    (row,) = read_rows(flow_path)
    assert row["evidence"] == "api_accepted"
    assert row["domain"] == "customer"


# ---------- 2. notify_contracts 分域常量 ----------

def test_domains_tuple():
    assert nc.DOMAINS == ("owner", "ops", "customer")


def test_domain_labels():
    assert nc.DOMAIN_LABELS == {"owner": "本人", "ops": "运维",
                                "customer": "客户"}


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
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                         for r in rows), encoding="utf-8")


def test_flow_items_today_ok_domain_badge(tmp_path, monkeypatch):
    """今日 ok 行带 domain → auto item 徽章透出。"""
    _flow_root(tmp_path, monkeypatch, [
        {"ts": NOW_ISO, "date": TODAY, "kind": "daily_digest",
         "channel": "feishu", "target": "ou_a", "count": 1, "ok": True,
         "tenant": "default", "domain": "owner"},
    ])
    items = api_notify.flow_items([])
    auto = [it for it in items if it["id"].startswith("flowauto:")]
    assert len(auto) == 1
    assert auto[0]["domain"] == "owner"


def test_flow_items_legacy_rows_no_domain_key(tmp_path, monkeypatch):
    """旧流水行（无 domain 键）→ item 亦无 domain 键（缺省不出现）。"""
    _flow_root(tmp_path, monkeypatch, [
        {"ts": NOW_ISO, "date": TODAY, "kind": "daily_digest",
         "channel": "feishu", "target": "ou_a", "count": 1, "ok": True,
         "tenant": "default"},
    ])
    items = api_notify.flow_items([])
    auto = [it for it in items if it["id"].startswith("flowauto:")]
    assert len(auto) == 1
    assert "domain" not in auto[0]


def test_flow_items_multi_domain_merge(tmp_path, monkeypatch):
    """同 (kind,target) 今日聚合行混两域 → 域值排序逗号合并，不丢域。"""
    _flow_root(tmp_path, monkeypatch, [
        {"ts": NOW_ISO, "date": TODAY, "kind": "k1", "channel": "feishu",
         "target": "ou_a", "count": 1, "ok": True, "tenant": "default",
         "domain": "customer"},
        {"ts": NOW_ISO, "date": TODAY, "kind": "k1", "channel": "feishu",
         "target": "ou_a", "count": 1, "ok": True, "tenant": "default",
         "domain": "ops"},
    ])
    items = api_notify.flow_items([])
    auto = [it for it in items if it["id"].startswith("flowauto:")]
    assert len(auto) == 1
    assert auto[0]["domain"] == "customer,ops"


def test_flow_items_blocked_row_domain(tmp_path, monkeypatch):
    """近 7 天失败行带 domain → blocked item 徽章透出（代表行取值）。"""
    _flow_root(tmp_path, monkeypatch, [
        {"ts": NOW_ISO, "date": TODAY, "kind": "k_fail", "channel": "feishu",
         "target": "ou_b", "count": 1, "ok": False, "tenant": "default",
         "domain": "customer"},
    ])
    items = api_notify.flow_items([])
    blocked = [it for it in items if it["state"] == "blocked"]
    assert len(blocked) == 1
    assert blocked[0]["domain"] == "customer"


# ---------- 4. 挂账占位（改动落地后取消 skip 补跑） ----------

@pytest.mark.skip(reason="挂账：feishu_client.py 本会话读取被平台审批拦截，"
                         "domain 透传改动未落地；落地后取消 skip 验证 "
                         "_hub_send body_obj['domain'] 与 send_msg_tracked 签名")
def test_feishu_client_domain_passthrough():
    pass


@pytest.mark.skip(reason="挂账：push_admin_server.py 本会话读取被平台审批拦截，"
                         "DOMAIN_ROUTES/req domain/customer observe 未落地；"
                         "落地后取消 skip 验证表驱动路由与 observe 留痕")
def test_push_admin_domain_routes_and_observe():
    pass
