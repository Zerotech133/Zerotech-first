"""工位 Q 测试：OS 单屏驾驶舱六平台汇聚视图（digest）的行为契约。

测试约定：真实 MessageBus（账本/死信落 tmp_path；digest 只读不写、
不落盘、不回写总线）；零真实网络，bus_url 指向不可达地址；注册表用
本文件内 duck typing 假条目（dict 与对象两种形态），不 import
platform_agent 模块（避免跨工位耦合）；实例化 BaseAgent 子类会触碰
Prometheus 共享指标，沿用 test_seats.py 的注册表清理 fixture 风格。
"""
from __future__ import annotations

import pytest

from roles.message_bus import MessageBus
from roles.os_commander import OsCommander

UNREACHABLE_BUS = "http://127.0.0.1:1"

# 六平台注册表（dict 形态，字段契约与派工单工位P一致：name/label/doing）
REGISTRY_DICTS = [
    {"name": "quant_trading", "label": "量化交易", "doing": "模拟回测推演"},
    {"name": "stock", "label": "股票观察", "doing": "模拟行情梳理"},
    {"name": "r_and_d", "label": "预研", "doing": "模拟预研步进"},
    {"name": "ppt", "label": "演示文稿", "doing": "模拟文稿生成"},
    {"name": "biz", "label": "商务", "doing": "模拟商务跟进"},
    {"name": "review", "label": "复盘", "doing": "模拟复盘推演"},
]


class _RegEntry:
    """duck typing 注册表条目（对象形态，非 dict）。"""

    def __init__(self, name: str, label: str, doing: str) -> None:
        self.name = name
        self.label = label
        self.doing = doing


REGISTRY_OBJECTS = [
    _RegEntry("quant_trading", "量化交易", "模拟回测推演"),
    _RegEntry("stock", "股票观察", "模拟行情梳理"),
    _RegEntry("r_and_d", "预研", "模拟预研步进"),
    _RegEntry("ppt", "演示文稿", "模拟文稿生成"),
    _RegEntry("biz", "商务", "模拟商务跟进"),
    _RegEntry("review", "复盘", "模拟复盘推演"),
]


@pytest.fixture(autouse=True)
def _clean_prometheus_registry():
    """沿用 test_seats.py 风格：BaseAgent 子类实例化前清空注册表，
    避免 Duplicated timeseries 报错。"""
    from prometheus_client import REGISTRY
    REGISTRY._names_to_collectors.clear()
    REGISTRY._collector_to_names.clear()
    yield


def make_commander() -> OsCommander:
    return OsCommander(report_to="principal", bus_url=UNREACHABLE_BUS,
                       metrics_port=9106)


def make_bus(tmp_path) -> MessageBus:
    return MessageBus(ledger_path=tmp_path / "bus_ledger.jsonl",
                      dlq_path=tmp_path / "bus_dlq.jsonl")


def _feed(bus: MessageBus, agent: str, platform: str, **overrides) -> None:
    point = {"agent": agent, "platform": platform,
             "doing": "模拟推演中", "progress": "步进 3/10",
             "artifact": f"outputs/{platform}.json", "ts": 1234.5}
    point.update(overrides)
    assert bus.collect_status_point(point)["ok"] is True


# ---- a) 注册表传入：六平台视图齐全且按注册表顺序 ----

def test_digest_registry_full_view_in_registry_order(tmp_path):
    bus = make_bus(tmp_path)
    # 只让其中三个平台上报，其余三个保持未上报
    _feed(bus, "platform:quant_trading", "quant_trading")
    _feed(bus, "platform:ppt", "ppt")
    _feed(bus, "platform:review", "review")
    view = make_commander().digest(bus, REGISTRY_DICTS)
    assert set(view) == {"generated_at", "platforms", "agents_online", "data_gaps"}
    assert view["generated_at"] > 0
    assert [p["platform"] for p in view["platforms"]] == \
        [e["name"] for e in REGISTRY_DICTS]  # 注册表顺序，缺位也占行
    assert len(view["platforms"]) == 6


# ---- b) 未上报平台如实 reported:false（不造数） ----

def test_digest_unreported_platform_reported_false(tmp_path):
    bus = make_bus(tmp_path)
    _feed(bus, "platform:quant_trading", "quant_trading")  # 仅量化上报
    view = make_commander().digest(bus, REGISTRY_DICTS)
    unreported = [p for p in view["platforms"] if p["platform"] == "stock"]
    assert len(unreported) == 1
    row = unreported[0]
    assert row["reported"] is False
    assert row["progress"] is None
    assert row["artifact"] is None
    assert row["ts"] is None
    # label/doing 来自注册表配置来源，如实投影
    assert row["label"] == "股票观察"
    assert row["doing"] == "模拟行情梳理"
    # 未上报必须挂账进 data_gaps，不得静默
    gaps = [g for g in view["data_gaps"]
            if g.get("platform") == "stock" and g.get("reason") == "未上报"]
    assert gaps, "未上报平台应记入 data_gaps"


# ---- c) 上报平台投影正确（board 事实 + 注册表 label） ----

def test_digest_reported_platform_projection(tmp_path):
    bus = make_bus(tmp_path)
    _feed(bus, "platform:quant_trading", "quant_trading",
          doing="回测推演", progress="步进 7/10",
          artifact="outputs/bt.json", ts=5678.9)
    view = make_commander().digest(bus, REGISTRY_DICTS)
    row = next(p for p in view["platforms"]
               if p["platform"] == "quant_trading")
    assert row == {"platform": "quant_trading", "label": "量化交易",
                   "doing": "回测推演", "progress": "步进 7/10",
                   "artifact": "outputs/bt.json", "ts": 5678.9,
                   "reported": True}


# ---- d) 缺注册表：降级为 board 顺序，label 如实 None ----

def test_digest_no_registry_falls_back_to_board_order(tmp_path):
    bus = make_bus(tmp_path)
    _feed(bus, "platform:quant_trading", "quant_trading")
    _feed(bus, "platform:stock", "stock", doing="盯盘",
          progress="", artifact="", ts=77.0)
    view = make_commander().digest(bus)  # registry 缺省 None
    assert [p["platform"] for p in view["platforms"]] == \
        ["quant_trading", "stock"]  # board 顺序
    for row in view["platforms"]:
        assert row["label"] is None  # 无配置来源，如实 None，不造数
        assert row["reported"] is True
    assert view["agents_online"] == 2
    assert view["data_gaps"] == []


# ---- e) data_gaps 如实：上报点缺字段记 missing_fields ----

def test_digest_data_gaps_missing_fields_truthful(tmp_path):
    bus = make_bus(tmp_path)
    bus.collect_status_point({"agent": "platform:stock", "platform": "stock",
                              "doing": "盯盘", "artifact": "", "ts": 99.0})
    # 上报点缺 progress 字段（注册表内其余五平台未上报也各自挂账）
    view = make_commander().digest(bus, REGISTRY_DICTS)
    assert {"platform": "stock", "missing_fields": ["progress"]} \
        in view["data_gaps"]


# ---- f) 空总线：data_gaps 如实标注 ----

def test_digest_empty_bus_data_gaps_truthful(tmp_path):
    view = make_commander().digest(make_bus(tmp_path), REGISTRY_DICTS)
    assert view["platforms"]  # 注册表在，六平台行占满
    assert len(view["platforms"]) == 6
    assert all(p["reported"] is False for p in view["platforms"])
    assert view["agents_online"] == 0
    assert view["data_gaps"], "空总线应记入 data_gaps"
    assert "无状态点" in view["data_gaps"][0].get("reason", "")


# ---- g) 只读无副作用：board / 账本前后一致，不落盘 ----

def test_digest_is_read_only_no_side_effects(tmp_path):
    bus = make_bus(tmp_path)
    _feed(bus, "platform:quant_trading", "quant_trading")
    _feed(bus, "platform:review", "review", doing="复盘推演")
    before_board = bus.board()
    ledger = tmp_path / "bus_ledger.jsonl"
    before_ledger = ledger.read_text(encoding="utf-8") if ledger.exists() else ""
    view = make_commander().digest(bus, REGISTRY_DICTS)
    assert bus.board() == before_board      # 总线视图未被改动
    after_ledger = ledger.read_text(encoding="utf-8") if ledger.exists() else ""
    assert after_ledger == before_ledger    # 未落任何账
    # 归集视图是副本：篡改视图不穿透到总线
    view["platforms"][0]["progress"] = "篡改"
    assert bus.board()[0]["progress"] != "篡改"


# ---- h) agents_online 与 collect 同源同口径 ----

def test_digest_agents_online_consistent_with_collect(tmp_path):
    bus = make_bus(tmp_path)
    _feed(bus, "platform:quant_trading", "quant_trading")
    _feed(bus, "platform:stock", "stock")
    _feed(bus, "platform:r_and_d", "r_and_d")
    cmd = make_commander()
    assert cmd.digest(bus, REGISTRY_DICTS)["agents_online"] == \
        cmd.collect(bus)["agents_online"] == 3
    _feed(bus, "platform:quant_trading", "quant_trading", doing="更新")
    # 同一 agent 重复上报只留最新，两个视图口径仍一致
    assert cmd.digest(bus)["agents_online"] == \
        cmd.collect(bus)["agents_online"] == 3


# ---- i) duck typing：对象形态注册表条目 + 注册表外平台挂账 ----

def test_digest_registry_duck_typing_objects_and_out_of_registry(tmp_path):
    bus = make_bus(tmp_path)
    _feed(bus, "platform:quant_trading", "quant_trading")
    _feed(bus, "platform:other_platform", "other_platform")  # 不在注册表内
    view = make_commander().digest(bus, REGISTRY_OBJECTS)
    row = next(p for p in view["platforms"]
               if p["platform"] == "quant_trading")
    assert row["label"] == "量化交易"  # 对象属性取值成功
    # 注册表之外的已上报状态点如实挂账，不静默丢弃
    gaps = [g for g in view["data_gaps"]
            if g.get("platform") == "other_platform"
            and g.get("reason") == "平台不在注册表内"]
    assert gaps, "注册表外已上报平台应记入 data_gaps"
