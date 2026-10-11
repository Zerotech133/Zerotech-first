# -*- coding: utf-8 -*-
"""K-STOCK-145 · 经理会议线回归（被测：k_stock/paper/meeting.py）

覆盖：正常链（chat 消息含 role_prompt+会议指令 / send kind 正确）/
无 active 雇佣拒/非法 kind 拒/LLM 失败不落半截纪要（send 未被调用）/
默认通道按档案 delivery_channels 解析/CLI 被拒分支 exit 码。
chat_for_hire 与 minutes.send 全部 monkeypatch，绝不真触网；
manager 账本注入同 test_manager.py（临时目录，真账本零触碰）。
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PAPER = HERE / ".." / "paper"
sys.path.insert(0, str(PAPER))

import gaorta_client  # noqa: E402
import manager  # noqa: E402
import meeting  # noqa: E402
import minutes  # noqa: E402
from meeting import MeetingError, hold_meeting  # noqa: E402

MID, SOURCE = "bull_desk", "qd"


class _Spies:
    """收集 fake chat/send 的调用痕迹。"""

    def __init__(self):
        self.chat_calls = []
        self.sent = []

    def fake_chat(self, mid, source, messages, timeout=60):
        self.chat_calls.append({"mid": mid, "source": source, "messages": messages})
        return {"content": "【纪要】今日策略要点……（测试正文）",
                "model": "GLM-5.2", "usage": {"total_tokens": 42}}

    def fake_send(self, mid, kind, title, body="", channel="site", meta=None):
        self.sent.append({"mid": mid, "kind": kind, "title": title,
                          "body": body, "channel": channel, "meta": dict(meta or {})})
        return {"id": "min_test_0", "mid": mid, "kind": kind, "title": title,
                "body": body, "channel": channel, "status": "delivered",
                "meta": dict(meta or {})}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """注入 manager 账本/档案到临时区（同 test_manager.py），mock chat 与 send。"""
    mdir = tmp_path / "managers"
    mdir.mkdir()
    monkeypatch.setattr(manager, "HIRES_PATH", tmp_path / "manager_hires.json")
    monkeypatch.setattr(manager, "MANAGERS_DIR", mdir)
    monkeypatch.setattr(manager, "HERE", tmp_path)  # _hire_price 读 HERE/"models.json"
    (tmp_path / "models.json").write_text(json.dumps(
        {"models": [{"model": "GLM-5.2", "coef_vs_glm52": 1.0}]}), encoding="utf-8")
    (mdir / f"{MID}.json").write_text(json.dumps(
        {"id": MID, "name": "牛市台", "status": "live", "model": "GLM-5.2",
         "hire_base_price_cny": 99,
         "role_prompt": "你是测试经理角色提示词RP-MARKER。",
         "delivery_channels": ["site"]}), encoding="utf-8")
    manager.hire(MID, SOURCE)
    spies = _Spies()
    monkeypatch.setattr(gaorta_client, "chat_for_hire", spies.fake_chat)
    monkeypatch.setattr(minutes, "send", spies.fake_send)
    return spies


def test_hold_meeting_normal_chain(env):
    rec, provider = hold_meeting(MID, SOURCE, "pre_open", context="沪指平开，量能温和")
    # chat 被调用：mid/source 正确，messages[0] 含 role_prompt 与盘前会议指令
    assert len(env.chat_calls) == 1
    call = env.chat_calls[0]
    assert call["mid"] == MID and call["source"] == SOURCE
    sys_msg = call["messages"][0]["content"]
    assert "RP-MARKER" in sys_msg and "盘前" in sys_msg
    assert call["messages"][1]["content"] == "沪指平开，量能温和"
    # send 被调用：kind 正确、body=LLM 输出、title 含经理名、走档案默认通道
    assert len(env.sent) == 1
    s = env.sent[0]
    assert s["mid"] == MID and s["kind"] == "pre_open"
    assert s["body"].startswith("【纪要】")
    assert "牛市台" in s["title"] and "盘前会" in s["title"]
    assert s["channel"] == "site"
    assert s["meta"]["source"] == SOURCE
    assert rec["status"] == "delivered"
    assert provider == "GLM-5.2"
    print("PASS test_hold_meeting_normal_chain")


def test_hold_meeting_post_close_instruction(env):
    hold_meeting(MID, SOURCE, "post_close", context="今日冲高回落")
    sys_msg = env.chat_calls[0]["messages"][0]["content"]
    assert "复盘" in sys_msg and "明日" in sys_msg
    assert env.sent[0]["kind"] == "post_close"
    print("PASS test_hold_meeting_post_close_instruction")


def test_hold_meeting_rejects_bad_kind(env):
    with pytest.raises(MeetingError, match="非法会议类型"):
        hold_meeting(MID, SOURCE, "weekly")
    assert env.chat_calls == [] and env.sent == []
    print("PASS test_hold_meeting_rejects_bad_kind")


def test_hold_meeting_rejects_no_active_hire(env):
    manager.fire(MID, SOURCE)  # 解聘后无 active 雇佣
    with pytest.raises(MeetingError, match="active 雇佣"):
        hold_meeting(MID, SOURCE, "pre_open")
    assert env.chat_calls == [] and env.sent == []
    print("PASS test_hold_meeting_rejects_no_active_hire")


def test_hold_meeting_rejects_unknown_mid(env):
    with pytest.raises(MeetingError):
        hold_meeting("ghost_pm", SOURCE, "pre_open")
    assert env.chat_calls == [] and env.sent == []
    print("PASS test_hold_meeting_rejects_unknown_mid")


def test_hold_meeting_llm_failure_no_half_minutes(env):
    def boom(mid, source, messages, timeout=60):
        raise RuntimeError("未配置 GAORTA_API_KEY，无法调用 gaorta 通道")
    import gaorta_client as g
    orig = g.chat_for_hire
    g.chat_for_hire = boom
    try:
        with pytest.raises(MeetingError, match="LLM"):
            hold_meeting(MID, SOURCE, "pre_open", context="行情摘要")
    finally:
        g.chat_for_hire = orig
    assert env.sent == [], "LLM 失败时 minutes.send 不得被调用（不落半截纪要）"
    print("PASS test_hold_meeting_llm_failure_no_half_minutes")


def test_hold_meeting_llm_empty_body_rejected(env):
    import gaorta_client as g
    orig = g.chat_for_hire
    g.chat_for_hire = lambda *a, **k: {"content": "   ", "model": "GLM-5.2"}
    try:
        with pytest.raises(MeetingError, match="空纪要"):
            hold_meeting(MID, SOURCE, "pre_open")
    finally:
        g.chat_for_hire = orig
    assert env.sent == []
    print("PASS test_hold_meeting_llm_empty_body_rejected")


def test_hold_meeting_explicit_channel_passthrough(env):
    hold_meeting(MID, SOURCE, "pre_open", channel="trader")
    assert env.sent[0]["channel"] == "trader"
    print("PASS test_hold_meeting_explicit_channel_passthrough")


def test_cli_hold_rejected_exit_codes(env, capsys):
    manager.fire(MID, SOURCE)  # 无 active 雇佣 → 被拒分支
    assert meeting._main(["hold", MID, SOURCE, "pre_open"]) == 1
    out = capsys.readouterr().out
    assert "❌" in out
    assert meeting._main(["hold", MID, SOURCE, "pre_open", "--json"]) == 2
    line = capsys.readouterr().out.strip()
    payload = json.loads(line)
    assert payload["ok"] is False
    assert "active 雇佣" in payload["error"]
    print("PASS test_cli_hold_rejected_exit_codes")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
