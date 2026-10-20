"""工位L 测试：QuantPlatformAgent 模拟推演引擎 + 平台日报（S3 正式波次）。

测试约定：全部零真实网络（report_up 经 monkeypatch httpx.post 注入记录器）；
所有产出均为模拟数据，模拟盘不触真实资金（红线见 roles/platform_agent.py）。
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from roles.base_agent import Report
from roles.platform_agent import QuantPlatformAgent

UNREACHABLE_BUS = "http://127.0.0.1:1"


@pytest.fixture(autouse=True)
def _clean_prometheus_registry():
    """BaseAgent 每次实例化都会向默认注册表注册同名指标；
    多席共存测试前清空，避免 Duplicated timeseries 报错（沿用 test_seats 风格）。"""
    from prometheus_client import REGISTRY
    REGISTRY._names_to_collectors.clear()
    REGISTRY._collector_to_names.clear()
    yield


def make_agent(seed: int = 7) -> QuantPlatformAgent:
    return QuantPlatformAgent(seed=seed, report_to="tian_feng",
                              bus_url=UNREACHABLE_BUS, metrics_port=9105)


# ---- a) 种子可控可复现：同种子两实例结果一致，异种子不同 ----

def test_seed_reproducible_across_instances():
    a1, a2 = make_agent(seed=42), make_agent(seed=42)
    p1 = [p.progress for p in a1.self_run_loop(steps=5)]
    p2 = [p.progress for p in a2.self_run_loop(steps=5)]
    assert p1 == p2
    assert a1.ledger_snapshot() == a2.ledger_snapshot()

    a3 = make_agent(seed=43)
    p3 = [p.progress for p in a3.self_run_loop(steps=5)]
    assert p1 != p3  # 异种子序列不同（模拟行情路径不同）


# ---- b) 步进产出状态点字段齐全 ----

def test_self_run_loop_yields_complete_status_points():
    agent = make_agent()
    points = list(agent.self_run_loop(steps=3))
    assert len(points) == 3
    for idx, p in enumerate(points, start=1):
        assert p.agent == "quant_platform"
        assert p.platform == "quant_trading"
        assert p.doing == "模拟推演"
        assert isinstance(p.progress, str) and p.progress
        assert isinstance(p.artifact, str) and p.artifact
        assert p.ts > 0
        assert f"模拟步进{idx}/3" in p.progress
    # 默认步数为 1
    assert len(list(agent.self_run_loop())) == 1


# ---- c) 模拟标注在位：状态点/快照/日报均含「模拟」字样 ----

def test_simulated_markers_present():
    agent = make_agent()
    points = list(agent.self_run_loop(steps=2))
    for p in points:
        assert "模拟" in p.progress
        assert "模拟" in p.artifact
        assert "模拟账本" in p.artifact
    snap = agent.ledger_snapshot()
    assert snap["simulated"] is True
    assert any("模拟" in k for k in snap)  # 「累计模拟盈亏」键名含「模拟」
    report = agent.daily_report()
    assert "模拟" in report.subject
    assert "模拟" in report.body


# ---- d) 日报字段与账本投影一致 ----

def test_daily_report_matches_ledger_projection():
    agent = make_agent()
    list(agent.self_run_loop(steps=4))
    snap = agent.ledger_snapshot()
    report = agent.daily_report()
    assert isinstance(report, Report)
    assert report.level == "platform"
    assert report.source == "quant_platform"
    assert f"模拟步数：{snap['步数']}" in report.body
    assert f"模拟持仓数：{snap['持仓数']}" in report.body
    assert f"累计模拟盈亏：{snap['累计模拟盈亏']:+.2f}" in report.body
    assert "当日状态点：4 条" in report.body


# ---- e) 账本快照只读：篡改返回值不影响内部状态 ----

def test_ledger_snapshot_is_readonly():
    agent = make_agent()
    list(agent.self_run_loop(steps=3))
    snap = agent.ledger_snapshot()
    tampered = dict(snap)
    tampered["持仓数"] = 999
    tampered["步数"] = 0
    tampered["累计模拟盈亏"] = 12345.0
    tampered["simulated"] = False
    # 篡改副本后，内部账本与再次投影均不受影响
    fresh = agent.ledger_snapshot()
    assert fresh == snap
    assert fresh["持仓数"] == 1
    assert fresh["步数"] == 3
    assert fresh["simulated"] is True
    # 继续步进时账本仍按原轨迹累计（未被篡改穿透）
    list(agent.self_run_loop(steps=1))
    assert agent.ledger_snapshot()["步数"] == 4


# ---- f) 日报经 report_up 链路可达（注入记录器，零真实网络） ----

def test_daily_report_reaches_report_up(monkeypatch):
    agent = make_agent()
    list(agent.self_run_loop(steps=2))
    payloads = []

    def record_post(url, **kwargs):
        payloads.append(kwargs["json"])
        return SimpleNamespace(is_success=True, json=lambda: {"ok": True})

    monkeypatch.setattr(httpx, "post", record_post)
    result = agent.report_up(agent.daily_report())

    assert result == {"ok": True}
    assert len(payloads) == 1
    assert payloads[0]["expected_target"] == "tian_feng"
    assert payloads[0]["report"]["level"] == "platform"
    assert payloads[0]["report"]["source"] == "quant_platform"
    assert "平台日报" in payloads[0]["report"]["subject"]
    assert "模拟" in payloads[0]["report"]["body"]


# ---- g) 空账本如实：未步进时零值与缺失标注，不造数 ----

def test_empty_ledger_truthful():
    agent = make_agent()
    snap = agent.ledger_snapshot()
    assert snap == {"simulated": True, "持仓数": 0,
                    "累计模拟盈亏": 0.0, "步数": 0}
    report = agent.daily_report()
    assert "模拟步数：0" in report.body
    assert "模拟持仓数：0" in report.body
    assert "当日状态点：无" in report.body  # 字段缺失如实标注


# ---- h) 补充：steps=0 不步进、账本不变 ----

def test_zero_steps_yields_nothing():
    agent = make_agent()
    assert list(agent.self_run_loop(steps=0)) == []
    assert agent.ledger_snapshot()["步数"] == 0
    assert agent.ledger_snapshot()["持仓数"] == 0
