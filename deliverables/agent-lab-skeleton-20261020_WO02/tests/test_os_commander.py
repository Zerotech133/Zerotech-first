"""工位 K 测试：os_commander 归集驾驶舱最小实现的行为契约。

测试约定：真实 MessageBus（账本/死信落 tmp_path；collect 只读不写）；
零真实网络，bus_url 指向不可达地址；本测试实例化 BaseAgent 子类会触碰
Prometheus 共享指标，沿用 test_seats.py 的注册表清理 fixture 风格。
"""
from __future__ import annotations

import pytest

from entrypoint import main
from roles.message_bus import MessageBus
from roles.os_commander import OsCommander

UNREACHABLE_BUS = "http://127.0.0.1:1"


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
                       metrics_port=9105)


def make_bus(tmp_path) -> MessageBus:
    return MessageBus(ledger_path=tmp_path / "bus_ledger.jsonl",
                      dlq_path=tmp_path / "bus_dlq.jsonl")


def _feed(bus: MessageBus, agent: str, **overrides) -> None:
    point = {"agent": agent, "platform": "quant_trading",
             "doing": "回测推演", "progress": "步进 3/10",
             "artifact": "outputs/bt.json", "ts": 1234.5}
    point.update(overrides)
    assert bus.collect_status_point(point)["ok"] is True


# ---- a) 归集视图投影正确 ----

def test_collect_projects_board_truthfully(tmp_path):
    bus = make_bus(tmp_path)
    _feed(bus, "platform:quant_trading")
    _feed(bus, "platform:r_and_d", platform="r_and_d", doing="预研",
          progress="", artifact="")
    view = make_commander().collect(bus)
    assert set(view) == {"generated_at", "points", "agents_online", "data_gaps"}
    assert view["generated_at"] > 0
    assert view["agents_online"] == 2
    assert len(view["points"]) == 2
    row = next(p for p in view["points"]
               if p["agent"] == "platform:quant_trading")
    assert row == {"agent": "platform:quant_trading",
                   "platform": "quant_trading", "doing": "回测推演",
                   "progress": "步进 3/10", "artifact": "outputs/bt.json",
                   "ts": 1234.5}
    assert view["data_gaps"] == []


# ---- b) 空总线 data_gaps 如实 ----

def test_empty_bus_data_gaps_truthful(tmp_path):
    view = make_commander().collect(make_bus(tmp_path))
    assert view["points"] == []
    assert view["agents_online"] == 0
    # 空总线必须如实标注缺数据，不得静默成"一切正常"的样子。
    assert view["data_gaps"], "空总线应记入 data_gaps"
    assert "无状态点" in view["data_gaps"][0].get("reason", "")


# ---- c) 缺 progress 字段如实标注（None 占位，不补全） ----

def test_missing_progress_field_flagged(tmp_path):
    bus = make_bus(tmp_path)
    bus.collect_status_point({"agent": "platform:r_and_d",
                              "platform": "r_and_d", "doing": "调研",
                              "artifact": "", "ts": 99.0})  # 无 progress
    view = make_commander().collect(bus)
    row = view["points"][0]
    assert row["progress"] is None  # 如实 None 占位，不造数补全
    assert view["data_gaps"] == [
        {"agent": "platform:r_and_d", "missing_fields": ["progress"]}]


# ---- d) agents_online 计数 ----

def test_agents_online_counts_distinct_agents(tmp_path):
    bus = make_bus(tmp_path)
    _feed(bus, "platform:a")
    _feed(bus, "platform:b")
    _feed(bus, "platform:c")
    view = make_commander().collect(bus)
    assert view["agents_online"] == 3
    _feed(bus, "platform:a", doing="更新")  # 同一 agent 重复上报只留最新
    view2 = make_commander().collect(bus)
    assert view2["agents_online"] == 3
    assert len(view2["points"]) == 3


# ---- e) status_point 自述字段齐全 ----

def test_status_point_self_description(tmp_path):
    cmd = make_commander()
    sp = cmd.status_point()
    for field in ("agent", "platform", "doing", "progress", "artifact", "ts"):
        assert getattr(sp, field) not in (None, "")
    assert sp.agent == "os_commander"
    assert sp.doing == "归集各板块状态点"
    assert sp.ts > 0
    # 归集一次后，自述 progress 反映最近归集摘要
    bus = make_bus(tmp_path)
    _feed(bus, "platform:a")
    cmd.collect(bus)
    assert "1" in cmd.status_point().progress


# ---- f) 只读投影无副作用（不造第二账） ----

def test_collect_is_read_only_projection(tmp_path):
    bus = make_bus(tmp_path)
    _feed(bus, "platform:quant_trading")
    before_board = bus.board()
    ledger = tmp_path / "bus_ledger.jsonl"
    before_ledger = ledger.read_text(encoding="utf-8") if ledger.exists() else ""
    view = make_commander().collect(bus)
    assert bus.board() == before_board  # 总线视图未被改动
    after_ledger = ledger.read_text(encoding="utf-8") if ledger.exists() else ""
    assert after_ledger == before_ledger  # 未落任何账
    # 归集视图是副本：篡改视图不穿透到总线
    view["points"][0]["doing"] = "篡改"
    assert bus.board()[0]["doing"] != "篡改"


# ---- g) entrypoint --role os_commander 自检 rc=0（不起真实服务） ----

def test_entrypoint_os_commander_selfcheck_rc0(capsys):
    rc = main(["--role", "os_commander"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "自检" in out
    assert "os_commander" in out
    # 无 --serve 不得启动 uvicorn 服务
    assert "启动服务" not in out
