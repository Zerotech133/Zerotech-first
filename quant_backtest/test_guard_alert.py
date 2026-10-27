# -*- coding: utf-8 -*-
"""guard_alert 单测（alert-loop P0-2 2026-10-14）：四条路径全合成数据。

  1. 去重窗口：同 key 12h 内第二次不重发；超窗重发
  2. 状态文件损坏：fail-open 照发（宁重发不静默）
  3. 推送失败：不炸主流程、不写状态（下轮可重试）
  4. 内容四要素：任务名/拦截原因/双数据锚/建议动作 齐全

零真实推送（_push 打桩）、零真实状态（tmp_path），不碰生产数据。
"""

import json
import sys
from datetime import datetime

import pytest

HERE = sys.path[0]
import guard_alert as ga  # noqa: E402


@pytest.fixture
def state_fp(tmp_path):
    return str(tmp_path / "guard_alert_state.json")


@pytest.fixture(autouse=True)
def log_fp(tmp_path, monkeypatch):
    """WO-QT-20261020-059：隔离 ga.LOG_PATH→tmp_path。

    本文件此前只 patch _push/state_path，测试日志直写生产
    quant_backtest/logs/guard_alert.log（10-21 01:12 实测混入
    dedup skip guard_race/guard_edge 测试行）——告警留痕的
    生产取证面被测试污染。autouse 全用例生效，与
    test_guard_alert_reason.log_fp 同款语义。"""
    fp = str(tmp_path / "guard_alert.log")
    monkeypatch.setattr(ga, "LOG_PATH", fp)
    return fp


@pytest.fixture
def fake_push(monkeypatch):
    """打桩推送通道：记录调用，返回 True。"""
    calls = []

    def _p(text):
        calls.append(text)
        return True

    monkeypatch.setattr(ga, "_push", _p)
    return calls


T0 = datetime(2026, 10, 14, 6, 30, 0)
REASON = "个股锚 600519.SH=2026-10-12（zhice）≠ 板块锚 881019=2026-10-13"


def test_dedup_window(state_fp, fake_push):
    """同 key 12h 内只发 1 次；超窗（13h 后）复发重发。"""
    ok1, _ = ga.guard_alert("StrongEod", REASON, send=True, state_path=state_fp, now=T0)
    assert ok1 and len(fake_push) == 1
    # 11h 后同因再拦截 → 去重跳过
    ok2, _ = ga.guard_alert(
        "StrongEod",
        REASON,
        send=True,
        state_path=state_fp,
        now=T0.replace(hour=17, minute=30),
    )
    assert not ok2 and len(fake_push) == 1
    # 13h 后仍同因 → 窗口外，重发
    ok3, _ = ga.guard_alert(
        "StrongEod",
        REASON,
        send=True,
        state_path=state_fp,
        now=T0.replace(hour=19, minute=30),
    )
    assert ok3 and len(fake_push) == 2
    # 状态文件确为原子写产物且可解析
    st = json.load(open(state_fp, encoding="utf-8"))
    assert len(st) == 1 and "ts" in list(st.values())[0]


def test_corrupt_state_fail_open(state_fp, fake_push):
    """状态文件损坏 → fail-open：不抛错、照发（宁重发不静默）。"""
    with open(state_fp, "w", encoding="utf-8") as f:
        f.write("{broken json!!!")
    ok, _ = ga.guard_alert("StrongEod", REASON, send=True, state_path=state_fp, now=T0)
    assert ok and len(fake_push) == 1


def test_push_fail_no_raise(state_fp, monkeypatch):
    """推送失败：不炸、返回 False、不写状态（下轮重试）。"""
    flaky = {"up": False}

    def _p(text):
        return flaky["up"]

    monkeypatch.setattr(ga, "_push", _p)
    ok, _ = ga.guard_alert("StrongEod", REASON, send=True, state_path=state_fp, now=T0)
    assert not ok
    # 通道恢复后同因立即可发（失败不记状态）
    flaky["up"] = True
    ok2, _ = ga.guard_alert(
        "StrongEod", REASON, send=True, state_path=state_fp, now=T0.replace(minute=40)
    )
    assert ok2


def test_content_four_elements(state_fp, fake_push):
    """dry 拼装：内容含 任务名/拦截原因/双数据锚/建议动作 四要素。"""
    ok, text = ga.guard_alert(
        "StrongEod",
        REASON,
        anchors={"板块锚 881019": "2026-10-13", "个股锚": "2026-10-12"},
        suggest="核查 TdxSync；补齐后 --force 重扫",
        send=False,
        state_path=state_fp,
        now=T0,
    )
    assert not ok and len(fake_push) == 0  # dry 不真推不写状态
    import os

    assert not os.path.exists(state_fp)
    assert "任务: StrongEod" in text
    assert "拦截原因: %s" % REASON in text
    assert "板块锚 881019=2026-10-13" in text
    assert "个股锚=2026-10-12" in text
    assert "建议动作: 核查 TdxSync" in text
