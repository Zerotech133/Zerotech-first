"""工位T 测试：平台 status_point 专属化（S6 清障波次）。

测试约定：零真实网络（send_status_point 经 monkeypatch httpx.post 注入
记录器）；状态自述全部取自实例内存态，不造数；模拟产出显式标注「模拟」
（红线见 roles/platform_agent.py）。
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from roles.base_agent import StatusPoint
from roles.platform_agent import (PLATFORM_REGISTRY, PlatformAgent,
                                  QuantPlatformAgent, SimulatedPlatformAgent,
                                  make_platform)

UNREACHABLE_BUS = "http://127.0.0.1:1"


@pytest.fixture(autouse=True)
def _clean_prometheus_registry():
    """BaseAgent 每次实例化都会向默认注册表注册同名指标；
    多席共存测试前清空，避免 Duplicated timeseries 报错（沿用 test_seats 风格）。"""
    from prometheus_client import REGISTRY
    REGISTRY._names_to_collectors.clear()
    REGISTRY._collector_to_names.clear()
    yield


def make_quant(seed: int = 7) -> QuantPlatformAgent:
    return QuantPlatformAgent(seed=seed, report_to="tian_feng",
                              bus_url=UNREACHABLE_BUS, metrics_port=9106)


def make_generic(reg_name: str):
    return make_platform(reg_name, seed=7, report_to="tian_feng",
                         bus_url=UNREACHABLE_BUS, metrics_port=9106)


# ---- a) 量化平台 status_point 字段齐全且含「模拟」标注 ----

def test_quant_status_point_fields_complete_and_simulated():
    agent = make_quant()
    list(agent.self_run_loop(steps=3))
    point = agent.status_point()
    assert isinstance(point, StatusPoint)
    assert point.agent == "quant_platform"
    assert point.platform == "量化交易"  # 注册表同源 label
    assert point.doing == "模拟推演"
    assert "模拟" in point.doing
    assert point.progress and "模拟" in point.progress
    assert "模拟账本" in point.artifact and "模拟" in point.artifact
    assert point.ts > 0


# ---- b) 五通用平台同构专属化：注册表 label 逐一透传 ----

def test_five_generic_platforms_status_point_labels():
    for reg_name in ("stock", "rd", "ppt", "biz", "review"):
        entry = PLATFORM_REGISTRY[reg_name]
        agent = make_generic(reg_name)
        point = agent.status_point()
        assert point.agent == f"{entry['name']}_platform"
        assert point.platform == entry["label"]  # 注册表 label 透传
        assert "模拟" in point.doing
        assert point.progress == "尚未开始推演"  # 同构：未步进时如实
        assert "模拟账本" in point.artifact


# ---- c) 未开始推演时 progress 如实「尚未开始推演」，不造数 ----

def test_not_started_progress_truthful():
    agent = make_quant()
    point = agent.status_point()
    assert point.progress == "尚未开始推演"
    assert agent.ledger_snapshot()["步数"] == 0  # 账本零值投影，未造数


# ---- d) 推演步进后 progress 更新为最近一次步进摘要 ----

def test_progress_updates_after_steps():
    agent = make_quant()
    list(agent.self_run_loop(steps=2))
    first = agent.status_point().progress
    assert "模拟步进2/2" in first
    list(agent.self_run_loop(steps=1))
    second = agent.status_point().progress
    assert "模拟步进1/1" in second
    assert second != first  # progress 随最新步进更新


# ---- e) send_status_point 经此覆写可归集（注入记录器，零真实网络） ----

def test_send_status_point_collected_via_override(monkeypatch):
    agent = make_quant()
    list(agent.self_run_loop(steps=2))
    payloads = []

    def record_post(url, **kwargs):
        payloads.append(kwargs["json"])
        return SimpleNamespace(is_success=True, json=lambda: {"ok": True})

    monkeypatch.setattr(httpx, "post", record_post)
    result = agent.send_status_point()

    assert result == {"ok": True}
    assert len(payloads) == 1
    assert payloads[0]["expected_target"] == "tian_feng"
    report = payloads[0]["report"]
    assert report["level"] == "command"
    assert report["source"] == "quant_platform"
    assert "[状态点] 模拟推演" in report["subject"]
    body = report["body"]
    # 六字段全部进入归集报文（str(point.__dict__) 投影）
    for key in ("agent", "platform", "doing", "progress", "artifact", "ts"):
        assert f"'{key}'" in body
    assert "quant_platform" in body
    assert "量化交易" in body
    assert "模拟推演" in body
    assert "模拟步进2/2" in body


# ---- f) 未覆写的裸 PlatformAgent 仍走 NotImplementedError（fail-visible） ----

def test_bare_platform_agent_still_fail_visible():
    bare = PlatformAgent(platform="stock_picking", report_to="tian_feng",
                         bus_url=UNREACHABLE_BUS, metrics_port=9106)
    with pytest.raises(NotImplementedError):
        bare.status_point()


# ---- g) SimulatedPlatformAgent 直例化时 label 用自身配置值 ----

def test_simulated_agent_label_passthrough():
    agent = SimulatedPlatformAgent(
        platform="ppt_studio", label="演示工坊", doing="演示文稿演练",
        seed=7, report_to="tian_feng", bus_url=UNREACHABLE_BUS,
        metrics_port=9106)
    point = agent.status_point()
    assert point.agent == "ppt_studio_platform"
    assert point.platform == "演示工坊"  # 自身配置 label，非注册表回查
    assert "模拟" in point.doing
    assert point.progress == "尚未开始推演"
    list(agent.self_run_loop(steps=1))
    assert "模拟步进1/1" in agent.status_point().progress
