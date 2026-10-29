# -*- coding: utf-8 -*-
"""B 激活 4 席：能力卡定义/触发/工具属性测试（2026-10-29）。"""
import importlib.util
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("seat_capability_inject", TOOLS / "seat_capability_inject.py")
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)

FOUR = ("comm1", "intel1", "ai1", "prod1")


def test_four_seats_have_home_and_cards():
    for seat in FOUR:
        assert seat in m.HOMES
        assert seat in m.CAPABILITIES
        assert seat in m.WORKING_MEMORY
        caps = m.CAPABILITIES[seat]
        assert len(caps) >= 6, seat
        names = {c["name"] for c in caps}
        assert "evidence-readonly" in names
        assert "trigger-ops" in names


def test_four_seats_use_shared_weekly_trigger_only():
    """B 方案不新增 L2 任务：触发统一走已安装的 SeatsWeekly。"""
    for seat in FOUR:
        assert seat in m.SEAT_TRIGGER_TASKS
        assert m.SEAT_TRIGGER_TASKS[seat] == ("SeatsWeekly",)
        trig = [c for c in m.CAPABILITIES[seat] if c["name"] == "trigger-ops"][0]
        assert trig["tasks"] == ["SeatsWeekly"]
        assert trig["installed"] is False  # 由运行态动态翻 ready


def test_four_seats_do_not_grant_network_or_trading_tools():
    forbidden = {"trade", "order", "exec", "ssh", "browser"}
    for seat in FOUR:
        caps = m.CAPABILITIES[seat]
        blob = " ".join(str(c.get("how") or "") for c in caps).lower()
        assert not any(x in blob for x in forbidden), seat
