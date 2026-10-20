"""工位DB测试（WO-LEGION-S16-02）：总线挂起队列跨进程持久化。

覆盖（工单要求逐条对应）：
- 总线 A 挂起 3 条后「重启」（新实例同 pending_path）：3 条俱在且顺序保持；
- 快照文件内容与内存队列一致（target + report 原样，FIFO 顺序）；
- 超限丢最旧后快照同步（丢的是最旧，快照只剩最新 3 条，重启可复现）；
- take（register 补投）后快照同步移除（再重启不复活已补投消息）；
- 坏 JSON 拒绝启动（PendingSnapshotCorrupt，fail-visible，不静默当空）；
  JSON 合法但结构不符同样拒绝；
- 默认 pending_path=None：纯内存零产物（目录里不出现任何 pending 文件）；
- 首次启动快照文件不存在：正常空队列，不抛错；
- 写盘失败不阻断挂起主路径（monkeypatch os.replace 注入故障，warning 留痕）；
- entrypoint 接线：LEGION_PENDING_PATH 注入 pending_path，缺省不启用（None）。

零真实网络；落盘一律进 tmp_path。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

import entrypoint
import roles.message_bus as message_bus
from roles.message_bus import MessageBus, PendingSnapshotCorrupt


def make_bus(tmp_path: Path, pending_path: Path | None = None) -> MessageBus:
    return MessageBus(
        ledger_path=tmp_path / "bus_ledger.jsonl",
        dlq_path=tmp_path / "bus_dlq.jsonl",
        pending_path=pending_path,
    )


def send_pending(bus: MessageBus, source: str, target: str, subject: str) -> dict:
    """目标未注册 → 消息进挂起队列的路径（route ok=true, delivered=false）。"""
    return bus.route(
        {"level": "command", "source": source, "subject": subject, "body": "b"},
        expected_target=target,
    )


def read_snapshot(pending_path: Path) -> dict:
    return json.loads(pending_path.read_text(encoding="utf-8"))


# ---------------- 1) 重启恢复：3 条俱在且顺序保持 ----------------

def test_restart_preserves_three_pending_in_order(tmp_path: Path) -> None:
    pending_path = tmp_path / "pending" / "pending_queue.json"
    bus1 = make_bus(tmp_path, pending_path)
    bus1.register("xun_you", "agent")
    for subject in ("甲", "乙", "丙"):
        assert send_pending(bus1, "xun_you", "zhuge_liang", subject)["ok"] is True
    assert bus1.pending_delivery() == {"zhuge_liang": 3}
    assert pending_path.exists()  # 挂起即落盘，不等重启

    # 「重启」：同 path 新实例，启动加载快照
    bus2 = make_bus(tmp_path, pending_path)
    assert bus2.pending_delivery() == {"zhuge_liang": 3}

    # 顺序保持：FIFO 补投后收件箱按 甲乙丙 原序
    bus2.register("zhuge_liang", "command")
    inbox = bus2.inbox("zhuge_liang")
    assert [m["subject"] for m in inbox] == ["甲", "乙", "丙"]
    assert all(m["source"] == "xun_you" and m["level"] == "command"
               and m["body"] == "b" for m in inbox)


# ---------------- 2) 快照文件内容与内存队列一致 ----------------

def test_snapshot_file_content_matches_queue(tmp_path: Path) -> None:
    pending_path = tmp_path / "pending_queue.json"
    bus = make_bus(tmp_path, pending_path)
    bus.register("xun_you", "agent")
    send_pending(bus, "xun_you", "zhuge_liang", "s1")
    send_pending(bus, "xun_you", "tian_feng", "s2")
    send_pending(bus, "xun_you", "zhuge_liang", "s3")

    data = read_snapshot(pending_path)
    assert data["version"] == message_bus.PENDING_SNAPSHOT_VERSION
    assert [(e["target"], e["report"]["subject"]) for e in data["pending"]] == [
        ("zhuge_liang", "s1"), ("tian_feng", "s2"), ("zhuge_liang", "s3"),
    ]
    # report 字段原样保留（不裁剪不代填）
    assert all(e["report"]["body"] == "b" for e in data["pending"])


# ---------------- 3) 超限丢最旧后快照同步 ----------------

def test_overflow_drop_oldest_syncs_snapshot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(message_bus, "PENDING_DELIVERY_MAX", 3)
    pending_path = tmp_path / "pending_queue.json"
    bus = make_bus(tmp_path, pending_path)
    bus.register("xun_you", "agent")
    for i in range(5):
        send_pending(bus, "xun_you", "zhuge_liang", f"第{i}条")

    assert bus.pending_delivery() == {"zhuge_liang": 3}
    # 快照与内存一致：只剩最新 3 条，最旧 2 条同步移除
    data = read_snapshot(pending_path)
    assert [e["report"]["subject"] for e in data["pending"]] == [
        "第2条", "第3条", "第4条",
    ]

    # 重启后丢最旧的结果可复现（不会让被丢消息复活）
    bus2 = make_bus(tmp_path, pending_path)
    assert bus2.pending_delivery() == {"zhuge_liang": 3}
    bus2.register("zhuge_liang", "command")
    assert [m["subject"] for m in bus2.inbox("zhuge_liang")] == [
        "第2条", "第3条", "第4条",
    ]


# ---------------- 4) take 后快照移除 ----------------

def test_take_removes_taken_entries_from_snapshot(tmp_path: Path) -> None:
    pending_path = tmp_path / "pending_queue.json"
    bus1 = make_bus(tmp_path, pending_path)
    bus1.register("xun_you", "agent")
    send_pending(bus1, "xun_you", "zhuge_liang", "a")
    send_pending(bus1, "xun_you", "zhuge_liang", "b")
    send_pending(bus1, "xun_you", "tian_feng", "c")

    bus2 = make_bus(tmp_path, pending_path)
    bus2.register("zhuge_liang", "command")   # take 2 条补投
    assert [m["subject"] for m in bus2.inbox("zhuge_liang")] == ["a", "b"]
    # 快照同步：zhuge_liang 条目移除，tian_feng 保留
    data = read_snapshot(pending_path)
    assert [(e["target"], e["report"]["subject"]) for e in data["pending"]] == [
        ("tian_feng", "c"),
    ]

    # 再重启：已补投的不复活，未补投的仍在
    bus3 = make_bus(tmp_path, pending_path)
    assert bus3.pending_delivery() == {"tian_feng": 1}


# ---------------- 5) 坏快照拒绝启动（fail-visible） ----------------

def test_corrupt_json_refuses_startup(tmp_path: Path) -> None:
    pending_path = tmp_path / "pending_queue.json"
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    pending_path.write_text("{这不是合法JSON", encoding="utf-8")
    with pytest.raises(PendingSnapshotCorrupt):
        make_bus(tmp_path, pending_path)


def test_structurally_invalid_snapshot_refuses_startup(tmp_path: Path) -> None:
    """JSON 合法但结构不符（非对象 / pending 非列表 / 条目坏）同样拒绝。"""
    bad_payloads = [
        '[1, 2, 3]',                       # 顶层不是对象
        '{"pending": "not-a-list"}',       # pending 不是列表
        '{"pending": [{"no": "keys"}]}',   # 条目缺 target/report
        '{"pending": [{"target": 1, "report": {}}]}',  # target 非字符串
        '{"pending": [{"target": "t", "report": [1]}]}',  # report 非对象
        '{}',                              # 缺 pending 键
    ]
    for payload in bad_payloads:
        pending_path = tmp_path / "pending_queue.json"
        pending_path.parent.mkdir(parents=True, exist_ok=True)
        pending_path.write_text(payload, encoding="utf-8")
        with pytest.raises(PendingSnapshotCorrupt):
            make_bus(tmp_path, pending_path)


def test_missing_snapshot_file_first_boot_is_empty(tmp_path: Path) -> None:
    """快照文件不存在 = 首次启动：空队列正常构造，不抛错、不建文件。"""
    pending_path = tmp_path / "pending_queue.json"
    bus = make_bus(tmp_path, pending_path)
    assert bus.pending_delivery() == {}
    assert not pending_path.exists()


# ---------------- 6) 默认 None：零持久化产物 ----------------

def test_default_none_pure_memory_zero_artifacts(tmp_path: Path) -> None:
    bus = make_bus(tmp_path, None)
    assert bus.pending_path is None
    bus.register("xun_you", "agent")
    send_pending(bus, "xun_you", "zhuge_liang", "内存态")
    # 挂起行为如旧（纯内存）
    assert bus.pending_delivery() == {"zhuge_liang": 1}
    # 目录里不出现任何 pending 快照产物（账本/死信及其锁文件除外）
    names = sorted(p.name for p in tmp_path.iterdir())
    assert not any("pending" in n for n in names), names


# ---------------- 7) 写盘失败不阻断挂起主路径 ----------------

def test_write_failure_does_not_block_enqueue(tmp_path: Path,
                                              monkeypatch, caplog) -> None:
    pending_path = tmp_path / "pending_queue.json"
    bus = make_bus(tmp_path, pending_path)
    bus.register("xun_you", "agent")

    real_replace = message_bus.os.replace

    def denied_replace(src, dst, *args, **kwargs):
        if Path(dst).name == pending_path.name:
            raise PermissionError(13, "拒绝访问（模拟写盘失败）")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(message_bus.os, "replace", denied_replace)

    with caplog.at_level(logging.WARNING, logger="roles.message_bus"):
        result = send_pending(bus, "xun_you", "zhuge_liang", "别堵路")

    # 主路径不受影响：route 成功、消息在挂起队列
    assert result["ok"] is True
    assert bus.pending_delivery() == {"zhuge_liang": 1}
    # 留痕可观测
    assert any("快照" in r.getMessage() for r in caplog.records)
    # 故障消失后下一笔变更点恢复正常落盘
    monkeypatch.setattr(message_bus.os, "replace", real_replace)
    send_pending(bus, "xun_you", "zhuge_liang", "恢复正常")
    data = read_snapshot(pending_path)
    assert [e["report"]["subject"] for e in data["pending"]] == ["别堵路", "恢复正常"]


def test_write_failure_does_not_block_take(tmp_path: Path,
                                           monkeypatch) -> None:
    """take 路径同样不因快照写失败而阻断（补投是主路径）。"""
    pending_path = tmp_path / "pending_queue.json"
    bus1 = make_bus(tmp_path, pending_path)
    bus1.register("xun_you", "agent")
    send_pending(bus1, "xun_you", "zhuge_liang", "x")

    real_replace = message_bus.os.replace

    def denied_replace(src, dst, *args, **kwargs):
        if Path(dst).name == pending_path.name:
            raise PermissionError(13, "拒绝访问（模拟写盘失败）")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(message_bus.os, "replace", denied_replace)
    bus2 = make_bus(tmp_path, pending_path)
    bus2.register("zhuge_liang", "command")
    # 补投照常完成（内存态正确），仅快照留 warning
    assert [m["subject"] for m in bus2.inbox("zhuge_liang")] == ["x"]
    assert bus2.pending_delivery() == {}


# ---------------- 8) entrypoint 接线：LEGION_PENDING_PATH ----------------

def _capture_bus_init(monkeypatch):
    """打桩 MessageBus.__init__，捕获 entrypoint 实际传入的 pending_path。"""
    captured: dict = {}
    orig_init = message_bus.MessageBus.__init__

    def spy_init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        captured["pending_path"] = kwargs.get("pending_path")

    monkeypatch.setattr(message_bus.MessageBus, "__init__", spy_init)
    return captured


@pytest.fixture(autouse=True)
def _clean_pending_env(monkeypatch):
    monkeypatch.delenv(entrypoint.PENDING_PATH_ENV, raising=False)
    monkeypatch.delenv(entrypoint.SEAT_TOKENS_ENV, raising=False)
    yield


def test_entrypoint_env_injects_pending_path(monkeypatch, tmp_path) -> None:
    target = tmp_path / "p" / "pending_queue.json"
    monkeypatch.setenv(entrypoint.PENDING_PATH_ENV, str(target))
    captured = _capture_bus_init(monkeypatch)

    rc = entrypoint._run_message_bus(serve=False, port=9400)

    assert rc == 0
    assert captured["pending_path"] == target


def test_entrypoint_env_unset_means_none(monkeypatch) -> None:
    captured = _capture_bus_init(monkeypatch)

    rc = entrypoint._run_message_bus(serve=False, port=9400)

    assert rc == 0
    assert captured["pending_path"] is None


def test_entrypoint_env_blank_means_none(monkeypatch) -> None:
    """空串/纯空白等价于未配置（沿用该文件既有环境变量解析风格）。"""
    monkeypatch.setenv(entrypoint.PENDING_PATH_ENV, "   \t ")
    captured = _capture_bus_init(monkeypatch)

    rc = entrypoint._run_message_bus(serve=False, port=9400)

    assert rc == 0
    assert captured["pending_path"] is None


# ---------------- 耐久性：快照落盘必须 fsync（WO-LEGION-S18-03） ----------------


def test_snapshot_fsyncs_before_replace(tmp_path, monkeypatch):
    """快照 tmp 写完必须 fsync 再 os.replace（统一耐久落盘契约）。"""
    pending_path = tmp_path / "pending_queue.json"
    bus = make_bus(tmp_path, pending_path)
    calls = {"n": 0}
    real_fsync = message_bus.os.fsync

    def counting_fsync(fd):
        calls["n"] += 1
        return real_fsync(fd)

    monkeypatch.setattr(message_bus.os, "fsync", counting_fsync)
    bus.register("xun_you", "agent")     # 来源须注册；目标未注册 → 挂起
    send_pending(bus, "xun_you", "zhuge_liang", "模拟挂起（模拟数据）")
    assert calls["n"] >= 1


