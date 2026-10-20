"""工位BC测试：汇报总线状态板契约版本化（BOARD_CONTRACT_VERSION / STALE_REASON_*）。

守的是一条契约红线：状态板是**下游消费方**（驾驶舱投影、督办、告警接线）的
解析依据，上游改一句文案/删一个键就可能把下游静默打断。故本文件分两级断言：

**信封级**（HTTP /board）：`contract_version` 挂在响应信封上，是"整个响应"的
属性，不是单条状态点的属性。此处必须真的走一次 `create_app(bus)` +
`TestClient` 的 `GET /board`——只测 `bus.board()` 是测不到信封的。
TestClient 走 ASGI 进程内自管道，不起端口、不发真实 HTTP，符合"零真实网络"；
故也不 monkeypatch 全局 socket（会误伤 TestClient 的 asyncio 自管道）。

**行级**（`bus.board()` 的每个 dict）：键集合必须**精确等于**
`STATUS_POINT_FIELDS | {"stale"}`（ts 异常时再加 `stale_reason`）。用精确相等
而不是子集断言：子集会放过"悄悄多塞一个字段"，正是要防的 schema 漂移。

此外守两件事：`stale_reason` 取值只允许 STALE_REASON_* 常量（不是自由字符串），
且常量集合与实际产出值一一对应（防止"常量提了但代码仍写字符串"）。

账本/死信全部落 tmp_path；MessageBus 不是 BaseAgent 子类，不触碰 prometheus
全局注册表，无需清理 REGISTRY。
"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

import roles.message_bus as message_bus
from roles.message_bus import (
    BOARD_CONTRACT_VERSION,
    STALE_REASON_NONE,
    STALE_REASON_TS_MISSING,
    STALE_REASON_TS_UNPARSEABLE,
    STALE_REASONS,
    STATUS_POINT_FIELDS,
    STATUS_STALE_TTL_S,
    MessageBus,
    create_app,
)

# 基准时间（模拟）：仅用于构造"新鲜/过期"两种状态，不代表真实采样。
BASE_TS = 1_750_000_000.0

# 行级键集合的两条硬口径（本波行内键零删改，故为精确集合）。
FRESH_ROW_KEYS = set(STATUS_POINT_FIELDS) | {"stale"}
BROKEN_ROW_KEYS = FRESH_ROW_KEYS | {"stale_reason"}


def make_bus(tmp_path: Path) -> MessageBus:
    return MessageBus(
        ledger_path=tmp_path / "bus_ledger.jsonl",
        dlq_path=tmp_path / "bus_dlq.jsonl",
    )


def collect(bus: MessageBus, agent: str, ts) -> None:
    bus.collect_status_point({
        "agent": agent, "platform": "quant", "doing": "回测",
        "progress": "50%", "artifact": "a.csv", "ts": ts,
    })


def only_point(bus: MessageBus, *, now: float) -> dict:
    points = bus.board(now=now)
    assert len(points) == 1
    return points[0]


def envelope(bus: MessageBus) -> dict:
    """走真实 ASGI 路由取 /board 信封（进程内，无真实网络）。"""
    client = TestClient(create_app(bus))
    response = client.get("/board")
    assert response.status_code == 200
    return response.json()


# ---------------- 信封级：contract_version ----------------

def test_contract_version_constant_is_v1() -> None:
    """版本号是字面契约：改动即需知会下游，故锁死取值。"""
    assert BOARD_CONTRACT_VERSION == "v1"


def test_board_envelope_carries_contract_version(tmp_path):
    """/board 信封带 contract_version，且既有 points 键原样保留（纯增量）。"""
    bus = make_bus(tmp_path)
    collect(bus, "a1", BASE_TS)
    payload = envelope(bus)
    assert set(payload) == {"contract_version", "points"}
    assert payload["contract_version"] == BOARD_CONTRACT_VERSION


def test_board_envelope_points_are_list_of_rows(tmp_path):
    """points 仍是 list，每项为状态点 dict，键集合精确等于行级契约。"""
    bus = make_bus(tmp_path)
    collect(bus, "a1", BASE_TS)
    payload = envelope(bus)
    assert isinstance(payload["points"], list)
    assert len(payload["points"]) == 1
    assert set(payload["points"][0]) == FRESH_ROW_KEYS


def test_board_envelope_is_empty_safe(tmp_path):
    """空总线：信封仍带版本，points 为空 list（不因无数据就不给版本）。"""
    payload = envelope(make_bus(tmp_path))
    assert payload["contract_version"] == BOARD_CONTRACT_VERSION
    assert payload["points"] == []


def test_board_envelope_without_now_injection(tmp_path):
    """路由走缺省 now 的既有零参路径，同样带版本（不改调用方契约）。"""
    bus = make_bus(tmp_path)
    collect(bus, "a1", time.time())
    payload = envelope(bus)
    assert payload["contract_version"] == BOARD_CONTRACT_VERSION
    assert payload["points"][0]["stale"] is False


# ---------------- 行级：既有键零删改（精确集合） ----------------

def test_fresh_row_keys_exactly_unchanged(tmp_path):
    """未过期行：键集合精确等于「六字段 + stale」，值原样保留。"""
    bus = make_bus(tmp_path)
    collect(bus, "a1", BASE_TS)
    row = only_point(bus, now=BASE_TS)
    assert set(row) == FRESH_ROW_KEYS
    for field in STATUS_POINT_FIELDS:
        assert field in row, f"既有键丢失: {field}"
    assert row["agent"] == "a1"
    assert row["platform"] == "quant"
    assert row["doing"] == "回测"
    assert row["progress"] == "50%"
    assert row["artifact"] == "a.csv"
    assert row["ts"] == BASE_TS
    assert row["stale"] is False
    # 过期/版本信息都不许漏进行内
    assert "stale_reason" not in row
    assert "contract_version" not in row


def test_stale_row_keys_exactly_unchanged(tmp_path):
    """正常超 TTL 过期行：仍是「六字段 + stale」，不带 stale_reason。"""
    bus = make_bus(tmp_path)
    collect(bus, "a1", BASE_TS)
    row = only_point(bus, now=BASE_TS + STATUS_STALE_TTL_S + 0.5)
    assert set(row) == FRESH_ROW_KEYS
    for field in STATUS_POINT_FIELDS:
        assert field in row, f"过期点丢失既有键: {field}"
    assert row["stale"] is True
    assert "stale_reason" not in row
    assert "contract_version" not in row


def test_missing_ts_row_keys_exactly_unchanged(tmp_path):
    """ts 缺失行：六字段 + stale + stale_reason（既有语义，键集不多不少）。"""
    bus = make_bus(tmp_path)
    bus.collect_status_point({"agent": "a1", "platform": "quant"})
    row = only_point(bus, now=0.0)
    assert set(row) == BROKEN_ROW_KEYS
    assert row["ts"] is None
    assert row["stale"] is True


def test_unparseable_ts_row_keys_exactly_unchanged(tmp_path):
    """ts 不可解析行：键集同上，且非数值不被强转（ts 原样保留字符串）。"""
    bus = make_bus(tmp_path)
    collect(bus, "a1", "不是时间戳")
    row = only_point(bus, now=0.0)
    assert set(row) == BROKEN_ROW_KEYS
    assert row["ts"] == "不是时间戳"
    assert "contract_version" not in row


# ---------------- stale_reason 取值域 ----------------

def test_missing_ts_reason_is_enum_constant(tmp_path):
    """ts 缺失：原因取常量 STALE_REASON_TS_MISSING（不是手写自由字符串）。"""
    bus = make_bus(tmp_path)
    bus.collect_status_point({"agent": "a1", "platform": "quant"})
    row = only_point(bus, now=0.0)
    assert row["stale_reason"] == STALE_REASON_TS_MISSING
    assert row["stale_reason"] == "ts_missing"


def test_unparseable_ts_reason_is_enum_constant(tmp_path):
    """ts 不可解析：原因取常量 STALE_REASON_TS_UNPARSEABLE。"""
    bus = make_bus(tmp_path)
    collect(bus, "a1", "不是时间戳")
    row = only_point(bus, now=0.0)
    assert row["stale_reason"] == STALE_REASON_TS_UNPARSEABLE
    assert row["stale_reason"] == "ts_unparseable"


def test_every_produced_reason_is_within_enum_domain(tmp_path):
    """三种状态混装：凡出现 stale_reason，取值必在 STALE_REASONS 内。"""
    bus = make_bus(tmp_path)
    collect(bus, "fresh", BASE_TS)
    bus.collect_status_point({"agent": "no_ts", "platform": "quant"})
    collect(bus, "bad_ts", "不是时间戳")
    rows = bus.board(now=BASE_TS)
    assert len(rows) == 3
    produced = {r["stale_reason"] for r in rows if "stale_reason" in r}
    assert produced
    assert produced <= set(STALE_REASONS)


def test_enum_domain_matches_actual_output(tmp_path):
    """常量集合与实际产出值一一对应：既不掉值，也不留"提了不用"的常量。

    只断言单值相等不够——必须让代码把每个原因都真的产出一次，再拿集合比对，
    才能防住"常量提了但代码仍写自由字符串"。
    """
    bus = make_bus(tmp_path)
    bus.collect_status_point({"agent": "no_ts"})       # -> ts_missing
    collect(bus, "bad_ts", "不是时间戳")               # -> ts_unparseable
    collect(bus, "fresh", BASE_TS)                     # -> 无原因
    rows = bus.board(now=BASE_TS)
    produced = {r["stale_reason"] for r in rows if "stale_reason" in r}
    assert produced == set(STALE_REASONS)
    assert STALE_REASON_NONE not in produced


# ---------------- 不污染内部存储 / TTL 语义不变 ----------------

def test_board_does_not_pollute_internal_status_store(tmp_path):
    """stale/stale_reason/contract_version 都不落内部存储，只在返回副本上。"""
    bus = make_bus(tmp_path)
    collect(bus, "a1", BASE_TS)
    bus.board(now=BASE_TS)
    envelope(bus)
    stored = bus._status_points["a1"]
    assert set(stored) == set(STATUS_POINT_FIELDS)
    for key in ("stale", "stale_reason", "contract_version"):
        assert key not in stored


def test_ttl_semantics_unchanged_by_versioning(tmp_path):
    """本波未改判定逻辑：恰等于 TTL 不算过期，超过一点即过期。"""
    bus = make_bus(tmp_path)
    collect(bus, "a1", BASE_TS)
    assert message_bus.STATUS_STALE_TTL_S == 300
    assert bus.board(now=BASE_TS + 300)[0]["stale"] is False
    assert bus.board(now=BASE_TS + 300.001)[0]["stale"] is True
    assert "stale_reason" not in bus.board(now=BASE_TS)[0]
