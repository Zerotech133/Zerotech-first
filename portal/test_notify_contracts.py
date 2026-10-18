# -*- coding: utf-8 -*-
"""K-ORG-201 轨道A：notify_contracts 底座契约单测。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import notify_contracts as nc  # noqa: E402


# ── 常量完备性 ──────────────────────────────────────────────

def test_constants_complete():
    assert len(nc.QUEUE_STATES) == 4
    assert set(nc.QUEUE_STATE_LABELS) == set(nc.QUEUE_STATES)
    assert set(nc.EVIDENCE_LIFECYCLE_LABELS) == set(nc.EVIDENCE_LIFECYCLE)
    assert set(nc.EVIDENCE_DELIVERY_LABELS) == set(nc.EVIDENCE_DELIVERY)
    assert "api_accepted" in nc.EVIDENCE_DELIVERY
    assert "receiver_verified" in nc.EVIDENCE_DELIVERY
    assert "dead_letter" in nc.EVIDENCE_DELIVERY
    # 证据分级语义互斥：设计/代码验证/演练/真实发送/自然定时首跑
    assert len(set(nc.EVIDENCE_LIFECYCLE)) == 5


# ── 四态映射 ────────────────────────────────────────────────

def test_map_decision_state():
    assert nc.map_decision_state("sent") == "auto"
    assert nc.map_decision_state("recorded") == "review"
    assert nc.map_decision_state("attempted") == "blocked"
    assert nc.map_decision_state("gate_or_send_failed") == "blocked"
    assert nc.map_decision_state("unknown_after_attempt") == "blocked"
    # 未知状态诚实归 blocked，不猜成功
    assert nc.map_decision_state("") == "blocked"
    assert nc.map_decision_state(None) == "blocked"
    assert nc.map_decision_state("weird") == "blocked"


# ── 送达证据映射 ────────────────────────────────────────────

def test_map_delivery_evidence():
    assert nc.map_delivery_evidence("api_accepted", "sent") == "api_accepted"
    assert nc.map_delivery_evidence("receiver_verified") == "receiver_verified"
    assert nc.map_delivery_evidence("dead_letter") == "dead_letter"
    # 显式空 → status 保守兜底：sent=API 受理（rc=0≠已读），其余不计
    assert nc.map_delivery_evidence("", "sent") == "api_accepted"
    assert nc.map_delivery_evidence(None, "recorded") == ""
    # 未知 evidence 值醒目归死信，不静默吞
    assert nc.map_delivery_evidence("weird") == "dead_letter"


# ── 频道登记表校验 ──────────────────────────────────────────

def _channel(**over):
    ch = {"id": "c1", "name": "频道一", "kinds": ["decision:alert"],
          "autonomy": "auto", "red_lines": ["仅名单内目标"],
          "evidence_level": "drilled"}
    ch.update(over)
    return ch


def test_validate_channels_ok():
    assert nc.validate_channels({"version": 1,
                                 "channels": [_channel()]}) == []


def test_validate_channels_rejects():
    bad = [
        ({}, "根"),
        ({"channels": [_channel()]}, "version"),
        ({"version": 1, "channels": []}, "空 channels"),
        ({"version": 1, "channels": [_channel(id="")]}, "空 id"),
        ({"version": 1, "channels": [_channel(), _channel()]}, "id 重复"),
        ({"version": 1, "channels": [_channel(autonomy="free")]}, "autonomy"),
        ({"version": 1, "channels": [_channel(kinds=[])]}, "空 kinds"),
        ({"version": 1, "channels": [_channel(red_lines=[])]}, "空红线"),
        ({"version": 1, "channels": [_channel(evidence_level="guess")]},
         "证据级别"),
    ]
    for data, why in bad:
        errs = nc.validate_channels(data)
        assert errs, why


def test_load_channels_repo_file_clean():
    """在役登记表本体必须零错误（登记表即契约，坏了立即红）。"""
    r = nc.load_channels()
    assert r["errors"] == []
    assert r["ok"] and len(r["channels"]) >= 5
    ids = {c["id"] for c in r["channels"]}
    assert {"matrix_daily", "decision_announcement", "feishu_hub"} <= ids
    # 管理公告频道必须显式登记为须确认
    ann = next(c for c in r["channels"] if c["id"] == "decision_announcement")
    assert ann["autonomy"] == "approval_required"


def test_load_channels_fail_visible(tmp_path):
    r = nc.load_channels(str(tmp_path / "missing.json"))
    assert not r["ok"] and r["channels"] == [] and r["errors"]
    bad = tmp_path / "broken.json"
    bad.write_text("{not json", encoding="utf-8")
    r2 = nc.load_channels(str(bad))
    assert not r2["ok"] and "JSON 损坏" in r2["errors"][0]


# ── 授权名单条目 schema ─────────────────────────────────────

def _entry(**over):
    e = {"name": "老田（灰度预演）",
         "markets": ["cn", "hk", "us", "commodity", "crypto", "fx"],
         "tier": "daily", "chat_id": "ou_50a95455c7dffec1090ade400886d0d4",
         "chat_id_type": "open_id"}
    e.update(over)
    return e


def test_validate_subscriber_ok():
    assert nc.validate_subscriber_entry("laotian_self", _entry()) == []
    # chat_id_type 缺省 → chat_id 口径
    e = _entry(chat_id="oc_abc123", chat_id_type=None)
    assert nc.validate_subscriber_entry("g", e) == []


def test_validate_subscriber_rejects():
    bad = [
        ("", _entry(), "空 group_id"),
        (7, _entry(), "非字符串 group_id"),
        ("g", "nope", "非对象条目"),
        ("g", _entry(name=" "), "空 name"),
        ("g", _entry(markets=[]), "空 markets"),
        ("g", _entry(markets=["mars"]), "未知市场"),
        ("g", _entry(tier="weekly"), "未知 tier"),
        ("g", _entry(chat_id=""), "空 chat_id"),
        ("g", _entry(chat_id="oc_abc", chat_id_type="open_id"), "口径错配 ou"),
        ("g", _entry(chat_id="ou_abc", chat_id_type=None), "缺省口径 chat_id 须 oc_"),
        ("g", _entry(chat_id="ou_abc", chat_id_type="sms"), "未知口径"),
    ]
    for gid, entry, why in bad:
        errs = nc.validate_subscriber_entry(gid, entry)
        assert errs, why


def test_push_subscribers_json_all_entries_valid():
    """在役订阅配置全部条目过校验（灰度首群 laotian_self 真实口径）。"""
    import json
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "push_subscribers.json")
    with open(path, encoding="utf-8") as f:
        subs = json.load(f)
    errs = []
    for gid, entry in subs.get("groups", {}).items():
        errs += nc.validate_subscriber_entry(gid, entry)
    assert errs == []
