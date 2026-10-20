"""工位P 测试：平台注册表 PLATFORM_REGISTRY + make_platform 五平台实例化（S5 扩编波次）。

测试约定：零真实网络（report_up 经 monkeypatch httpx.post 注入记录器）；
本地队列目录落盘 tmp_path；所有产出均为模拟数据，模拟盘不触真实资金
（红线见 roles/platform_agent.py）。
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from roles.platform_agent import (
    PLATFORMS,
    PLATFORM_REGISTRY,
    PlatformAgent,
    QuantPlatformAgent,
    SimulatedPlatformAgent,
    make_platform,
)

UNREACHABLE_BUS = "http://127.0.0.1:1"
KW = {"report_to": "tian_feng", "bus_url": UNREACHABLE_BUS,
      "metrics_port": 9405}
FIVE_KEYS = ("stock", "rd", "ppt", "biz", "review")


@pytest.fixture(autouse=True)
def _clean_prometheus_registry():
    """BaseAgent 每次实例化都会向默认注册表注册同名指标；
    多席共存测试前清空，避免 Duplicated timeseries 报错（沿用 test_seats 风格）。"""
    from prometheus_client import REGISTRY
    REGISTRY._names_to_collectors.clear()
    REGISTRY._collector_to_names.clear()
    yield


def make_via_factory(name: str, tmp_path, seed: int = 7):
    agent = make_platform(name, seed=seed, **KW)
    agent.pending_dir = str(tmp_path)  # 队列落盘进 tmp_path，零污染
    return agent


# ---- a) 六平台工厂全部可实例化，platform 标识与注册表一致 ----

def test_factory_instantiates_all_six_platforms(tmp_path):
    assert set(PLATFORM_REGISTRY) == {"quant", "stock", "rd", "ppt",
                                      "biz", "review"}
    for key in PLATFORM_REGISTRY:
        agent = make_via_factory(key, tmp_path)
        assert isinstance(agent, PlatformAgent)
        assert agent.platform == PLATFORM_REGISTRY[key]["name"]
        assert agent.platform in PLATFORMS


# ---- b) 未知名字拒绝：ValueError ----

def test_unknown_name_raises_valueerror(tmp_path):
    with pytest.raises(ValueError):
        make_platform("no_such_platform", **KW)
    with pytest.raises(ValueError):
        make_platform("", **KW)


# ---- c) 量化条目返回 QuantPlatformAgent 实例（且非通用模拟席） ----

def test_quant_entry_returns_quant_agent(tmp_path):
    agent = make_via_factory("quant", tmp_path)
    assert isinstance(agent, QuantPlatformAgent)
    assert not isinstance(agent, SimulatedPlatformAgent)
    assert agent.name == "quant_platform"
    assert agent.platform == "quant_trading"
    # 其余五平台均为配置驱动的通用模拟席
    for key in FIVE_KEYS:
        other = make_via_factory(key, tmp_path)
        assert isinstance(other, SimulatedPlatformAgent)


# ---- d) 五平台 self_run_loop 状态点含「模拟」标注与平台中文名 ----

def test_five_platforms_status_points_marked_simulated(tmp_path):
    for key in FIVE_KEYS:
        agent = make_via_factory(key, tmp_path)
        label = PLATFORM_REGISTRY[key]["label"]
        points = list(agent.self_run_loop(steps=2))
        assert len(points) == 2
        for p in points:
            assert p.agent == agent.name
            assert p.platform == PLATFORM_REGISTRY[key]["name"]
            assert "模拟" in p.doing
            assert label in p.doing  # 状态点携带平台中文名
            assert "模拟" in p.progress
            assert "模拟" in p.artifact
            assert p.ts > 0


# ---- e) 种子可控复现：同种子同结果、异种子不同（五平台逐一验证） ----

def test_seed_reproducible_for_five_platforms(tmp_path):
    for key in FIVE_KEYS:
        a1 = make_via_factory(key, tmp_path, seed=42)
        a2 = make_via_factory(key, tmp_path, seed=42)
        p1 = [p.progress for p in a1.self_run_loop(steps=4)]
        p2 = [p.progress for p in a2.self_run_loop(steps=4)]
        assert p1 == p2
        assert a1.ledger_snapshot() == a2.ledger_snapshot()
        a3 = make_via_factory(key, tmp_path, seed=43)
        p3 = [p.progress for p in a3.self_run_loop(steps=4)]
        assert p1 != p3  # 异种子模拟行情路径不同


# ---- f) 注册表常量字段齐全：name/label/doing，name 均在 PLATFORMS 内 ----

def test_registry_constant_fields_complete():
    seen_names = set()
    for key, entry in PLATFORM_REGISTRY.items():
        assert {"name", "label", "doing"} <= set(entry), f"{key} 字段缺失"
        assert entry["name"] in PLATFORMS
        assert isinstance(entry["label"], str) and entry["label"]
        assert isinstance(entry["doing"], str) and entry["doing"]
        seen_names.add(entry["name"])
    assert len(seen_names) == 6  # 六平台标识互不重复


# ---- g) 五平台日报经 report_up 可达（注入记录器，零真实网络） ----

def test_daily_report_reaches_report_up(tmp_path, monkeypatch):
    payloads = []

    def record_post(url, **kwargs):
        payloads.append(kwargs["json"])
        return SimpleNamespace(is_success=True, json=lambda: {"ok": True})

    monkeypatch.setattr(httpx, "post", record_post)
    for key in FIVE_KEYS:
        agent = make_via_factory(key, tmp_path)
        list(agent.self_run_loop(steps=2))
        report = agent.daily_report()
        result = agent.report_up(report)

        assert result == {"ok": True}
        payload = payloads[-1]
        assert payload["expected_target"] == "tian_feng"
        assert payload["report"]["level"] == "platform"
        assert payload["report"]["source"] == agent.name
        assert "平台日报" in payload["report"]["subject"]
        assert "模拟" in payload["report"]["subject"]
        assert "模拟" in payload["report"]["body"]
        assert PLATFORM_REGISTRY[key]["label"] in payload["report"]["subject"]
    assert len(payloads) == 5


# ---- h) 补充：五平台账本快照同样显式标注 simulated，且只读 ----

def test_five_platforms_ledger_snapshot_simulated_flag(tmp_path):
    for key in FIVE_KEYS:
        agent = make_via_factory(key, tmp_path)
        snap = agent.ledger_snapshot()
        assert snap["simulated"] is True
        assert any("模拟" in k for k in snap)
        tampered = dict(snap)
        tampered["步数"] = 999
        assert agent.ledger_snapshot() == snap  # 篡改副本不穿透
