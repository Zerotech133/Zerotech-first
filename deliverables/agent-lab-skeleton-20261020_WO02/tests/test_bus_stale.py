"""工位AC测试：总线账本接 file_lock 边车锁 + board() 状态点过期判定。

覆盖：多线程并发 route 账本行完整无交错、账本/死信边车锁 spy 断言、
新状态点 fresh、超 TTL stale、ts 缺失/不可解析如实标注、TTL 常量
可 monkeypatch 覆盖、board 既有键零删改且不污染内部存储。

零真实网络；落盘一律进 tmp_path。
"""
from __future__ import annotations

import contextlib
import json
import threading
import time
from pathlib import Path


import roles.message_bus as message_bus
from roles.base_agent import _HELD_LOCK_PATHS
from roles.message_bus import MessageBus, STATUS_POINT_FIELDS


def make_bus(tmp_path: Path) -> MessageBus:
    return MessageBus(
        ledger_path=tmp_path / "bus_ledger.jsonl",
        dlq_path=tmp_path / "bus_dlq.jsonl",
    )


def sidecar(path: Path) -> Path:
    return path.parent / (path.name + ".lock")


def lock_spy(monkeypatch, calls: list) -> None:
    """包一层 spy 的 file_lock（message_bus 命名空间）：记录锁路径，
    行为不变（转调真实 file_lock）。"""
    real_lock = message_bus.file_lock

    @contextlib.contextmanager
    def spy(path):
        calls.append(Path(path))
        with real_lock(path):
            yield

    monkeypatch.setattr(message_bus, "file_lock", spy)


def route_ok(bus: MessageBus, source: str, subject: str) -> dict:
    return bus.route({"source": source, "level": "agent",
                      "subject": subject, "body": "b"})


# ---------------- a) 并发 route 账本行完整无交错 ----------------

def test_concurrent_route_ledger_rows_intact(tmp_path):
    """8 线程 × 10 条并发 route：80 行完整 JSON 无交错，来源齐全，
    锁全部释放、边车锁句柄可关闭。"""
    bus = make_bus(tmp_path)
    bus.register("a1", "agent")
    bus.register("a2", "agent")
    bus.register("a3", "agent")
    bus.register("a4", "agent")
    bus.register("a5", "agent")
    bus.register("a6", "agent")
    bus.register("a7", "agent")
    bus.register("a8", "agent")
    errors: list = []

    def worker(tid: int) -> None:
        try:
            for i in range(10):
                result = route_ok(bus, f"a{tid + 1}", f"s{tid}-{i}")
                assert result["ok"] is True
        except Exception as exc:  # pragma: no cover - 触发即失败
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    lines = (tmp_path / "bus_ledger.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert len(lines) == 80
    sources = set()
    for ln in lines:
        record = json.loads(ln)  # 每行均为完整 JSON => 无写坏/交错
        assert record["kind"] == "routed"
        sources.add(record["source"])
    assert len(sources) == 8
    assert _HELD_LOCK_PATHS == {}
    sidecar(tmp_path / "bus_ledger.jsonl").unlink()  # 句柄已关闭（否则 PermissionError）


# ---------------- b) 账本/死信边车锁 spy ----------------

def test_append_holds_sidecar_lock_on_ledger_and_dlq(tmp_path, monkeypatch):
    """正常路由持账本边车锁、伪署名进死信持死信边车锁（spy 断言）。"""
    bus = make_bus(tmp_path)
    calls: list = []
    lock_spy(monkeypatch, calls)

    route_ok(bus, "ghost", "s")  # 伪署名 -> dlq
    bus.register("a1", "agent")
    route_ok(bus, "a1", "正常")  # -> ledger

    ledger_side = sidecar(tmp_path / "bus_ledger.jsonl")
    dlq_side = sidecar(tmp_path / "bus_dlq.jsonl")
    assert calls == [dlq_side, ledger_side]
    assert _HELD_LOCK_PATHS == {}


# ---------------- c) board 过期判定 ----------------

def collect_at(bus: MessageBus, agent: str, ts) -> None:
    bus.collect_status_point({"agent": agent, "platform": "quant",
                              "doing": "回测", "progress": "50%",
                              "artifact": "a.csv", "ts": ts})


def test_board_fresh_point_within_ttl(tmp_path):
    """新状态点 fresh；now == ts + TTL 边界不算 stale（严格大于才 stale）。"""
    bus = make_bus(tmp_path)
    collect_at(bus, "a1", 1000.0)
    points = bus.board(now=1000.0)
    assert points[0]["stale"] is False
    assert bus.board(now=1300.0)[0]["stale"] is False   # 恰等于 TTL：不超
    assert bus.board(now=1300.5)[0]["stale"] is True    # 刚超 TTL
    # now 注入缺省时走 time.time()，新点年龄≈0，必然 fresh
    collect_at(bus, "a2", time.time())
    assert bus.board()[1]["stale"] is False


def test_board_stale_beyond_ttl(tmp_path):
    """ts 距 now 超 TTL（默认 300s）即 stale。"""
    bus = make_bus(tmp_path)
    collect_at(bus, "a1", 1000.0)
    points = bus.board(now=1000.0 + 300.001)
    assert points[0]["stale"] is True
    assert "stale_reason" not in points[0]  # 正常过期不带原因字段


def test_board_missing_ts_honest(tmp_path):
    """ts 缺失：如实 stale=True 并注明 ts_missing，不猜不代填。"""
    bus = make_bus(tmp_path)
    bus.collect_status_point({"agent": "a1", "platform": "quant"})
    points = bus.board(now=0.0)
    assert points[0]["ts"] is None
    assert points[0]["stale"] is True
    assert points[0]["stale_reason"] == "ts_missing"


def test_board_unparseable_ts_honest(tmp_path):
    """ts 不可解析（非数值）：如实 stale=True 并注明 ts_unparseable。"""
    bus = make_bus(tmp_path)
    collect_at(bus, "a1", "不是时间戳")
    points = bus.board(now=0.0)
    assert points[0]["stale"] is True
    assert points[0]["stale_reason"] == "ts_unparseable"


def test_ttl_constant_monkeypatchable(tmp_path, monkeypatch):
    """TTL 常量可注入覆盖：判定读取调用时的模块级常量。"""
    monkeypatch.setattr(message_bus, "STATUS_STALE_TTL_S", 10)
    bus = make_bus(tmp_path)
    collect_at(bus, "a1", 1000.0)
    assert bus.board(now=1005.0)[0]["stale"] is False   # 未超新 TTL
    assert bus.board(now=1010.5)[0]["stale"] is True    # 超新 TTL


def test_board_existing_keys_preserved_no_internal_pollution(tmp_path):
    """既有六字段键零删改；stale 只加在返回副本上，不污染内部存储。"""
    bus = make_bus(tmp_path)
    collect_at(bus, "a1", 1000.0)
    first = bus.board(now=1001.0)
    assert set(STATUS_POINT_FIELDS) <= set(first[0])
    assert set(first[0]) == set(STATUS_POINT_FIELDS) | {"stale"}
    second = bus.board(now=1001.0)
    assert first == second
    # 内部存储未被增量字段污染（board 每次从原始点重建副本）
    assert "stale" not in bus._status_points["a1"]
    assert "stale_reason" not in bus._status_points["a1"]
