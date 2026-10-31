# -*- coding: utf-8 -*-
"""P3.5-lite km_card 链路 kind 透传单测（2026-10-20 派工单 J-A）。

离线纪律：桩 feishu_client.send_msg 全部网络出口；不真发飞书；
不启任何服务。feishu_bot_ws / km_feishu_hub 仅静态读源码断言
（不 import，避免长跑模块副作用）。

覆盖：
  A1 send_card kind 透传 —— kind="km_card" 经 kwarg 到达 send_msg；
  A2 kind 缺省（None）零变化 —— send_msg 收到 kind=None（与旧签名
     等价，send_msg 缺省即 None，不发模板字段）；
  A3 位置参数与返回值保持 —— json/"interactive"/receive_id/
     receive_id_type 顺序不变，send_msg 返回值原样透出；
     2026-10-28 OS-FIX-L1-AUDIT（5fbb814ea）起 send_card 新增
     critical 透传（预警卡豁免静默窗），故 kwarg 恒含
     critical（缺省 False）——契约已同步更新，非回归；
  A3b critical=True 透传 —— 红卡/预警卡必须到达 send_msg；
  A4 feishu_bot_ws 5 处 send_card 调用无 kind 实参（静态）；
  A5 km_feishu_hub 调用处含 kind="km_card"（静态）；
  A6 锚点存在性 —— feishu_card_v2 kind=kind 透传锚点 count==1。
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_NC = os.path.join(os.path.dirname(HERE), "news_collector")
for _p in (HERE, _NC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import feishu_client  # noqa: E402
import feishu_card_v2  # noqa: E402


_CARD = {"schema": "2.0", "header": {"title": {"tag": "plain_text", "content": "t"}}}

# ── 桩工具 ────────────────────────────────────────────────────────


def _stub_send_msg(monkeypatch):
    """替换 feishu_client.send_msg，捕获实参并返回固定二元组。"""
    calls = []

    def fake_send_msg(*args, **kwargs):
        calls.append((args, kwargs))
        return True, "stub ok"

    monkeypatch.setattr(feishu_client, "send_msg", fake_send_msg)
    return calls


# ── A1：kind 透传 ─────────────────────────────────────────────────


def test_send_card_passes_kind_kwarg(monkeypatch):
    calls = _stub_send_msg(monkeypatch)
    ok, why = feishu_card_v2.send_card(
        _CARD, receive_id="ou_x", receive_id_type="open_id", kind="km_card"
    )
    assert (ok, why) == (True, "stub ok")
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert kwargs.get("kind") == "km_card"
    assert json.loads(args[0]) == _CARD
    assert args[1] == "interactive"
    assert args[2] == "ou_x"
    assert args[3] == "open_id"


def test_send_card_kind_none_is_zero_change(monkeypatch):
    """缺省与显式 kind=None 等价：send_msg 收到 kind=None（不发模板字段）。"""
    calls = _stub_send_msg(monkeypatch)
    feishu_card_v2.send_card(_CARD)  # 全缺省（receive_id 落 USER_ID）
    feishu_card_v2.send_card(_CARD, receive_id="ou_y", kind=None)
    assert len(calls) == 2
    for args, kwargs in calls:
        assert kwargs.get("kind") is None
        assert args[1] == "interactive"


# ── A3：位置参数与返回值保持 ──────────────────────────────────────


def test_send_card_positional_args_unchanged(monkeypatch):
    calls = _stub_send_msg(monkeypatch)
    feishu_card_v2.send_card(_CARD, "ou_z", "chat_id", "km_card")
    args, kwargs = calls[0]
    assert args == (
        json.dumps(_CARD, ensure_ascii=False),
        "interactive",
        "ou_z",
        "chat_id",
    )
    # 契约更新（2026-10-31 WO-QUAL-PORTAL-20261031）：2026-10-28
    # OS-FIX-L1-AUDIT（5fbb814ea）有意给 send_card 增加 critical 透传
    # （预警类豁免飞书静默窗，修复红卡连续三晚被吞），send_msg 恒收
    # critical（缺省 False）。旧断言 == {"kind": "km_card"} 已过期 →
    # 显式写全 kwargs，避免再多透传参数时又假红/假绿。
    assert kwargs == {"kind": "km_card", "critical": False}


def test_send_card_critical_true_forwarded(monkeypatch):
    """critical=True 必须透传到 send_msg（2026-10-28 红卡避静默窗回归护栏）。"""
    calls = _stub_send_msg(monkeypatch)
    feishu_card_v2.send_card(_CARD, kind="km_card", critical=True)
    args, kwargs = calls[0]
    assert kwargs == {"kind": "km_card", "critical": True}
    assert args[1] == "interactive"


def test_send_card_returns_send_msg_result(monkeypatch):
    monkeypatch.setattr(
        feishu_client, "send_msg", lambda *a, **k: (False, "silent window")
    )
    ok, why = feishu_card_v2.send_card(_CARD, kind="km_card")
    assert ok is False and why == "silent window"


# ── 静态断言工具：括号配对提取调用块 ──────────────────────────────


def _call_blocks(src, func_name):
    """提取 src 中所有 func_name(...) 调用的完整文本块（括号配对）。"""
    blocks = []
    for m in re.finditer(r"%s\(" % re.escape(func_name), src):
        i = m.end()
        depth = 1
        while i < len(src) and depth:
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
            i += 1
        blocks.append(src[m.start() : i])
    return blocks


# ── A4：feishu_bot_ws 5 处调用兼容（不传 kind，零变化） ───────────


def test_feishu_bot_ws_5_calls_no_kind():
    path = os.path.join(HERE, "feishu_bot_ws.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    blocks = _call_blocks(src, "send_card")
    assert len(blocks) == 5  # :804/:810/:816/:841/:864
    for b in blocks:
        # 无 kind= 关键字实参（val.get("kind") 是取卡内容字段，非传参）
        assert not re.search(r"\bkind\s*=", b)


# ── A5：km_feishu_hub 调用处含 kind="km_card" ────────────────────


def test_km_hub_call_has_km_card_kind():
    path = os.path.join(HERE, "km_feishu_hub.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    blocks = _call_blocks(src, "send_card")
    assert len(blocks) == 1  # 唯一 send_card 调用（:252）
    assert 'kind="km_card"' in blocks[0]


# ── A6：锚点存在性 ────────────────────────────────────────────────


def test_feishu_card_v2_kind_passthrough_anchor():
    path = os.path.join(HERE, "feishu_card_v2.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    assert src.count("kind=kind") == 1  # send_card 内唯一透传锚点


def test_feishu_card_v2_signature_has_kind_default_none():
    path = os.path.join(HERE, "feishu_card_v2.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"def send_card\([^)]*\)", src)
    assert m, "send_card 签名缺失"
    assert "kind=None" in m.group(0)
