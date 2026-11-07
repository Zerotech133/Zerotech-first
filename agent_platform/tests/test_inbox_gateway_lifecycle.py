# -*- coding: utf-8 -*-
"""OSLINK-P4 answered/delivered 生命周期测试（隔离树·待签）。

对应合同：《2026-10-23_P4_传输层answered_delivered语义合同v1_待签.md》。
全部用临时目录注入（set_store），零真实消息、零真目录写入。
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLATFORM))

from gateway import inbox_gateway  # noqa: E402
import logging


@pytest.fixture()
def store(tmp_path):
    inbox_gateway.set_store(tmp_path)
    return tmp_path


def _submit(**kw):
    payload = {"intent": "stock_consult", "code": "605100"}
    payload.update(kw.pop("payload", {}))
    env = inbox_gateway.submit("task_request", payload, source="feishu_comm", **kw)
    assert env["ok"] is True
    return env["data"]["id"]


# ---- 1) submit → inbox，answered/delivered 双 false ----
def test_submit_is_inbox_with_no_answer_stamps(store):
    mid = _submit()
    r = inbox_gateway.receipt(mid)["data"]
    assert r["state"] == "inbox"
    assert r["answered"] is False and r["delivered"] is False


# ---- 2) consume → processed + 三类戳记 + answer_ref 原样 ----
def test_consume_stamps_answer(store):
    mid = _submit()
    env = inbox_gateway.consume(
        mid, answered_by="kanze_comm", answer_ref="reply:om_abc123"
    )
    assert env["ok"] is True and env["data"]["replayed"] is False
    rec = env["data"]["record"]
    assert rec["state"] == "processed"
    assert rec["answered_by"] == "kanze_comm"
    assert rec["answer_ref"] == "reply:om_abc123"
    assert rec["answered_at"]
    # 物理迁移：inbox 空、processed 有
    assert not (store / "inbox" / ("%s.json" % mid)).exists()
    assert (store / "processed" / ("%s.json" % mid)).exists()
    r = inbox_gateway.receipt(mid)["data"]
    assert r["state"] == "processed" and r["answered"] is True
    assert r["delivered"] is False


# ---- 3) 重复 consume 幂等：戳记与首次完全一致 ----
def test_consume_is_idempotent(store):
    mid = _submit()
    first = inbox_gateway.consume(mid, answered_by="kanze_comm", answer_ref="reply:1")[
        "data"
    ]["record"]
    second = inbox_gateway.consume(
        mid, answered_by="someone_else", answer_ref="reply:2"
    )["data"]
    assert second["replayed"] is True
    assert second["record"] == first  # 首次获胜，无第二答复
    assert second["record"]["answer_ref"] == "reply:1"


# ---- 4) 未答复直接 deliver → not_answered，且不产生任何戳记 ----
def test_deliver_before_answer_is_rejected(store):
    mid = _submit()
    env = inbox_gateway.deliver(mid, channel="feishu", delivered_ref="m:1")
    assert env["ok"] is False
    assert env["error"]["code"] == "not_answered"
    rec = inbox_gateway.receipt(mid)["data"]["record"]
    assert "delivered_at" not in rec
    assert rec.get("state") == "inbox"


# ---- 5) deliver 后 delivered=true；重复 deliver 幂等 ----
def test_deliver_after_answer_and_idempotent(store):
    mid = _submit()
    inbox_gateway.consume(mid, answered_by="kanze_comm", answer_ref="reply:1")
    env = inbox_gateway.deliver(mid, channel="feishu", delivered_ref="msg:om_x")
    assert env["ok"] is True and env["data"]["replayed"] is False
    rec = env["data"]["record"]
    assert rec["channel"] == "feishu" and rec["delivered_ref"] == "msg:om_x"
    assert rec["delivered_at"]
    r = inbox_gateway.receipt(mid)["data"]
    assert r["answered"] is True and r["delivered"] is True
    again = inbox_gateway.deliver(mid, channel="feishu", delivered_ref="msg:other")[
        "data"
    ]
    assert again["replayed"] is True
    assert again["record"]["delivered_ref"] == "msg:om_x"  # 首次获胜


# ---- 6) consume/deliver 不存在 id → not_found，不造文件 ----
def test_actions_on_missing_id_are_not_found(store):
    c = inbox_gateway.consume("lg-doesnotexist1", answered_by="x", answer_ref="y")[
        "data"
    ]
    assert c["state"] == "not_found" and c["record"] == {}
    d = inbox_gateway.deliver("lg-doesnotexist1", channel="feishu", delivered_ref="z")[
        "data"
    ]
    assert d["state"] == "not_found" and d["record"] == {}
    assert list((store / "processed").glob("*.json")) == []


# ---- 7) submit 幂等重放（既有行为）；已答复后重放 state=processed ----
def test_submit_replay_after_answer_reports_processed(store):
    mid = _submit(idempotency_key="feishu-om_x100b62b330fa0538dea87ac9b8cd39e")
    inbox_gateway.consume(mid, answered_by="kanze_comm", answer_ref="reply:1")
    env = inbox_gateway.submit(
        "task_request",
        {"intent": "stock_consult", "code": "605100"},
        source="feishu_comm",
        idempotency_key="feishu-om_x100b62b330fa0538dea87ac9b8cd39e",
    )
    assert env["ok"] is True
    assert env["data"]["replayed"] is True
    assert env["data"]["id"] == mid
    assert env["data"]["state"] == "processed"


# ---- 8) 双份残留自愈：consume 清理 inbox 残留并回放既有戳记 ----
def test_crash_remnant_self_heals(store):
    mid = _submit()
    inbox_gateway.consume(mid, answered_by="kanze_comm", answer_ref="reply:1")
    # 模拟崩溃残留：inbox 又出现同 id 文件
    remnant = store / "inbox" / ("%s.json" % mid)
    remnant.write_text(json.dumps({"id": mid, "state": "inbox"}), encoding="utf-8")
    env = inbox_gateway.consume(mid, answered_by="other", answer_ref="reply:2")["data"]
    assert env["replayed"] is True
    assert env["record"]["answer_ref"] == "reply:1"  # 既有戳记不被覆盖
    assert not remnant.exists()  # 残留已自愈


# ---- 9) 老式消费（手动移 processed、无戳记）→ answered=false，不得推断已答复 ----
def test_legacy_move_without_stamps_is_not_answered(store):
    mid = _submit()
    src = store / "inbox" / ("%s.json" % mid)
    dst = store / "processed" / ("%s.json" % mid)
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    src.unlink()
    r = inbox_gateway.receipt(mid)["data"]
    assert r["state"] == "processed"
    assert r["answered"] is False and r["delivered"] is False
    # 老式记录上的 deliver 仍被拒（无答复证据不得回传）
    env = inbox_gateway.deliver(mid, channel="feishu", delivered_ref="m:1")
    assert env["ok"] is False and env["error"]["code"] == "not_answered"
    # 显式 consume 后补戳记（真实答复动作）
    env2 = inbox_gateway.consume(
        mid, answered_by="kanze_comm", answer_ref="reply:late"
    )["data"]
    assert env2["record"]["answer_ref"] == "reply:late"
    r2 = inbox_gateway.receipt(mid)["data"]
    assert r2["answered"] is True


# ---- 10) 原子写不留 tmp 残留 ----
def test_atomic_write_leaves_no_tmp(store):
    mid = _submit()
    inbox_gateway.consume(mid, answered_by="kanze_comm", answer_ref="reply:1")
    leftovers = [p.name for p in (store / "processed").iterdir() if ".tmp-" in p.name]
    assert leftovers == []


# ---- 11) 既有回归：三值词表与坏 id 错误码不变 ----
def test_legacy_receipt_vocabulary_unchanged(store):
    mid = _submit()
    assert inbox_gateway.receipt(mid)["data"]["state"] == "inbox"
    assert inbox_gateway.receipt("lg-nope")["data"]["state"] == "not_found"
    bad = inbox_gateway.receipt("")
    assert bad["ok"] is False and bad["error"]["code"] == "bad_id"
    bad_kind = inbox_gateway.submit("not_a_kind", {})
    assert bad_kind["ok"] is False and bad_kind["error"]["code"] == "bad_kind"


@pytest.mark.parametrize("who,ref", [(None, "r"), (" ", "r"), ("a", ""), ("a", None)])
def test_consume_requires_evidence_without_moving_file(store, who, ref):
    mid = _submit()
    result = inbox_gateway.consume(mid, answered_by=who, answer_ref=ref)
    assert result["ok"] is False and result["error"]["code"] == "bad_answer_evidence"
    assert inbox_gateway.receipt(mid)["data"]["state"] == "inbox"
    assert not (store / "processed" / (mid + ".json")).exists()


@pytest.mark.parametrize(
    "channel,ref", [(None, "r"), (" ", "r"), ("c", ""), ("c", None)]
)
def test_deliver_requires_evidence_without_stamping(store, channel, ref):
    mid = _submit()
    inbox_gateway.consume(mid, answered_by="a", answer_ref="r")
    result = inbox_gateway.deliver(mid, channel=channel, delivered_ref=ref)
    assert result["ok"] is False and result["error"]["code"] == "bad_delivery_evidence"
    assert inbox_gateway.receipt(mid)["data"]["delivered"] is False


def test_receipt_rejects_partial_legacy_stamps_and_recovers(store):
    mid = _submit()
    src = store / "inbox" / (mid + ".json")
    dst = store / "processed" / src.name
    rec = json.loads(src.read_text(encoding="utf-8"))
    rec.update(
        answered_at="old",
        answered_by="",
        answer_ref="r",
        delivered_at="old",
        channel="",
        delivered_ref="d",
    )
    dst.write_text(json.dumps(rec), encoding="utf-8")
    status = inbox_gateway.receipt(mid)["data"]
    assert status["state"] == "processed"
    assert not status["answered"] and not status["delivered"]
    assert (
        inbox_gateway.deliver(mid, channel="c", delivered_ref="d")["error"]["code"]
        == "not_answered"
    )
    fixed = inbox_gateway.consume(mid, answered_by="a", answer_ref="r2")["data"]
    assert fixed["replayed"] is False
    assert not inbox_gateway.receipt(mid)["data"]["delivered"]
    assert not src.exists()
    assert inbox_gateway.deliver(mid, channel="c", delivered_ref="d")["ok"]


def test_submit_same_key_different_content_conflicts(store):
    first = inbox_gateway.submit("question", {"q": 1}, idempotency_key="same")
    for kind, payload, source in (
        ("question", {"q": 2}, "external"),
        ("feedback", {"q": 1}, "external"),
        ("question", {"q": 1}, "other"),
    ):
        result = inbox_gateway.submit(
            kind, payload, source=source, idempotency_key="same"
        )
        assert result["ok"] is False
        assert result["error"]["code"] == "idempotency_conflict"
    assert len(list((store / "inbox").glob("*.json"))) == 1
    assert inbox_gateway.receipt(first["data"]["id"])["data"]["record"]["payload"] == {
        "q": 1
    }


def test_submit_concurrent_threads_single_record(store):
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(
            pool.map(
                lambda _: inbox_gateway.submit(
                    "question", {"q": 1}, idempotency_key="thread-key"
                ),
                range(24),
            )
        )
    assert all(r["ok"] for r in results)
    assert len({r["data"]["id"] for r in results}) == 1
    assert sum(not r["data"]["replayed"] for r in results) == 1
    assert len(list((store / "inbox").glob("*.json"))) == 1


def test_submit_concurrent_windows_spawn_processes(store, tmp_path):
    """跨进程同键互斥：spawn 子进程经脚本+PYTHONPATH 自举（pickle 导入在
    pytest 根目录下不可靠），结果各落 JSON 文件由父进程汇总。"""
    import os
    import subprocess

    runner = tmp_path / "_spawn_submit.py"
    runner.write_text(
        "import json, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from gateway import inbox_gateway\n"
        "inbox_gateway.set_store(sys.argv[2])\n"
        "env = inbox_gateway.submit('question', {'q': 1},\n"
        "                           idempotency_key='process-key')\n"
        "with open(sys.argv[3], 'w', encoding='utf-8') as f:\n"
        "    json.dump(env, f, ensure_ascii=False)\n",
        encoding="utf-8",
    )
    env = dict(os.environ, PYTHONPATH=str(_PLATFORM))
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    err_handles = []
    procs = []
    for i in range(4):
        ef = open(tmp_path / ("err%d.log" % i), "wb")
        err_handles.append(ef)
        procs.append(
            subprocess.Popen(
                [
                    sys.executable,
                    str(runner),
                    str(_PLATFORM),
                    str(store),
                    str(results_dir / ("r%d.json" % i)),
                ],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=ef,
            )
        )
    try:
        for p in procs:
            p.wait(timeout=60)
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
        for ef in err_handles:
            try:
                ef.close()
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/tests/test_inbox_gateway_lifecycle.py", exc)
    errs = "\n".join(
        (tmp_path / ("err%d.log" % i)).read_text(encoding="utf-8", errors="replace")
        for i in range(len(procs))
    )
    assert all(p.returncode == 0 for p in procs), errs[-2000:]
    results = [
        json.loads(f.read_text(encoding="utf-8"))
        for f in sorted(results_dir.glob("r*.json"))
    ]
    assert all(r["ok"] for r in results)
    assert len({r["data"]["id"] for r in results}) == 1
    assert sum(not r["data"]["replayed"] for r in results) == 1
    assert len(list((store / "inbox").glob("*.json"))) == 1


def test_concurrent_consume_and_deliver_first_stamp_wins(store):
    mid = _submit()
    with ThreadPoolExecutor(max_workers=12) as pool:
        answers = list(
            pool.map(
                lambda i: inbox_gateway.consume(
                    mid, answered_by="a", answer_ref="r:%s" % i
                ),
                range(20),
            )
        )
    assert all(r["ok"] for r in answers)
    assert sum(not r["data"]["replayed"] for r in answers) == 1
    winner = next(
        r["data"]["record"]["answer_ref"] for r in answers if not r["data"]["replayed"]
    )
    assert all(r["data"]["record"]["answer_ref"] == winner for r in answers)
    with ThreadPoolExecutor(max_workers=12) as pool:
        deliveries = list(
            pool.map(
                lambda i: inbox_gateway.deliver(
                    mid, channel="c", delivered_ref="d:%s" % i
                ),
                range(20),
            )
        )
    assert all(r["ok"] for r in deliveries)
    assert sum(not r["data"]["replayed"] for r in deliveries) == 1
    winner = next(
        r["data"]["record"]["delivered_ref"]
        for r in deliveries
        if not r["data"]["replayed"]
    )
    assert all(r["data"]["record"]["delivered_ref"] == winner for r in deliveries)


def test_crash_window_processed_wins_over_inbox(store):
    mid = _submit(idempotency_key="crash-key")
    src = store / "inbox" / (mid + ".json")
    dst = store / "processed" / src.name
    rec = json.loads(src.read_text(encoding="utf-8"))
    rec.update(state="processed", answered_at="old", answered_by="a", answer_ref="r")
    dst.write_text(json.dumps(rec), encoding="utf-8")
    assert inbox_gateway.receipt(mid)["data"]["state"] == "processed"
    replay = inbox_gateway.submit(
        "task_request",
        rec["payload"],
        source="feishu_comm",
        idempotency_key="crash-key",
    )
    assert replay["data"]["state"] == "processed"
    assert replay["data"]["id"] == mid


# ---- 锁文件 open 的瞬时 Errno 13 重试（2026-11-07 K-ORG-20261107-INBOXLOCK）----
# 背景：4 个进程并发 submit 时 open(lock, "a+b") 会以约 16% 抛 PermissionError
# （25 轮 4 轮，见 tools/research/repro_inbox_lock_race.py）。该异常原本直接穿过
# submit()，调用方看到的是"投递失败"——生产侧等于丢消息。
# 修复见 inbox_gateway._open_lock_file；这里锁住"会重试"与"不无限等"两条语义。

_LOCK_NAME = ".inbox_gateway.lock"


def _patch_lock_open(monkeypatch, fail_times):
    """只干扰锁文件路径的 Path.open，其余路径透传；返回调用计数。"""
    real_open = Path.open
    calls = {"n": 0}

    def flaky(self, *a, **kw):
        if self.name == _LOCK_NAME:
            calls["n"] += 1
            if calls["n"] <= fail_times:
                raise PermissionError(13, "Permission denied", str(self))
        return real_open(self, *a, **kw)

    monkeypatch.setattr(Path, "open", flaky)
    return calls


def test_store_lock_retries_transient_open_permission_error(store, monkeypatch):
    """前两次 open 报 Errno 13 → 第三次成功，submit 正常落盘（不抛给调用方）。"""
    calls = _patch_lock_open(monkeypatch, fail_times=2)
    env = inbox_gateway.submit("question", {"q": 1}, idempotency_key="retry-key")
    assert env["ok"] is True
    assert calls["n"] >= 3, calls


def test_store_lock_open_gives_up_after_deadline(store, monkeypatch):
    """持续拒绝必须抛错，不能无限等待——把真故障变成 hang 更糟。"""
    monkeypatch.setattr(inbox_gateway, "_LOCK_OPEN_DEADLINE_S", 0.05)
    _patch_lock_open(monkeypatch, fail_times=10 ** 9)
    with pytest.raises(PermissionError):
        inbox_gateway.submit("question", {"q": 2}, idempotency_key="denied-key")
