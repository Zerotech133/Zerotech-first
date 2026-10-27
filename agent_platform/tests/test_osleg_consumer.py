# -*- coding: utf-8 -*-
"""K-QUANT-20261026-OSLEG-CONSUMER：8795 消费者骨架离线测试。

承重边界（对应契约 §七 / Wo-FS-157 §2.5）：
- 全部用 ``set_store(tmp_path)`` 隔离，零真实消息、零真 store 写入；
- 历史真单 ``lg-5917d4c4357b`` 永不消费、原地字节不变；
- 默认 dry-run 零写入；--run 才执行；重跑幂等；
- 源码层断言无网络/重依赖 import，不启 8795、不发任何消息。

真 ``agent_platform/bridge_inbox`` 在测试前后做文件数/字节快照对比，确保零变化。
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

import pytest

_PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLATFORM))

from gateway import inbox_gateway, osleg_consumer  # noqa: E402

HISTORICAL_ID = "lg-5917d4c4357b"


# ---------------------------------------------------------------- 夹具 / 工具
@pytest.fixture()
def store(tmp_path):
    inbox_gateway.set_store(tmp_path)
    yield tmp_path
    inbox_gateway._DEFAULT_STORE = None


def _submit(source="synthetic", kind="task_request", payload=None, idem=None):
    body = {
        "intent": "stock_consult",
        "code": "605100",
        "report_id": "QR-TEST-1",
        "trace_id": "tr-test-1",
    }
    if payload:
        body.update(payload)
    env = inbox_gateway.submit(kind, body, source=source, idempotency_key=idem)
    assert env["ok"] is True, env
    return env["data"]["id"]


def _snapshot(root) -> dict:
    """目录快照：文件数、总字节、逐文件 sha256（用于"零变化"断言）。"""
    root = Path(root)
    files = {}
    if not root.exists():
        return {"files": files, "count": 0, "bytes": 0}
    for path in sorted(x for x in root.rglob("*") if x.is_file()):
        data = path.read_bytes()
        files[str(path.relative_to(root))] = hashlib.sha256(data).hexdigest()
    return {
        "files": files,
        "count": len(files),
        "bytes": sum(p.stat().st_size for p in root.rglob("*") if p.is_file()),
    }


def _write_json(path: Path, record) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


def _ledger(store: Path) -> dict:
    path = store / "osleg_consumer" / "ledger.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _answer_doc(store: Path, msg_id: str) -> dict:
    path = store / "osleg_consumer" / "answers" / ("%s.json" % msg_id)
    return json.loads(path.read_text(encoding="utf-8"))


def _historical_record(source="synthetic") -> dict:
    return {
        "id": HISTORICAL_ID,
        "idempotency_key": "hist-real-order-key",
        "kind": "task_request",
        "source": source,
        "payload": {"intent": "stock_consult", "code": "605100"},
        "submitted_at": "2026-10-21T16:29:00+0800",
        "state": "inbox",
    }


# ---------------------------------------------------------------- 1. dry-run
def test_scan_classifies_three_ways_and_dry_run_writes_nothing(store):
    eligible_a = _submit(source="synthetic", idem="dry-a")
    eligible_b = _submit(
        source="e2e",
        kind="question",
        payload={"intent": "ask", "code": "000001"},
        idem="dry-b",
    )
    _submit(source="feishu_comm", idem="dry-real")
    _write_json(store / "inbox" / ("%s.json" % HISTORICAL_ID), _historical_record())
    (store / "inbox" / "lg-broken1.json").write_text("{not json", encoding="utf-8")

    before = _snapshot(store)
    plan = osleg_consumer.scan(store)
    after = _snapshot(store)

    assert plan["mode"] == "dry-run" and plan["read_only"] is True
    assert plan["ok"] is True
    assert plan["counts"] == {
        "eligible": 2,
        "protected": 2,
        "malformed": 1,
        "already_done": 0,
    }
    assert sorted(item["msg_id"] for item in plan["eligible"]) == sorted(
        [eligible_a, eligible_b]
    )
    reasons = {item["reason"] for item in plan["protected"]}
    assert reasons == {"historical_real_order", "source_not_whitelisted"}
    assert plan["malformed"][0]["reason"] == "bad_json"
    assert before == after  # dry-run 零写入
    assert not (store / "osleg_consumer").exists()
    assert list((store / "processed").glob("*.json")) == []
    assert len(list((store / "inbox").glob("*.json"))) == 5


def test_default_cli_is_dry_run_with_zero_writes(store, capsys):
    _submit(idem="cli-dry")
    before = _snapshot(store)
    rc = osleg_consumer.main(["--store-root", str(store)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["mode"] == "dry-run" and out["ok"] is True
    assert out["counts"]["eligible"] == 1
    assert _snapshot(store) == before
    assert not (store / "osleg_consumer").exists()
    assert list((store / "processed").glob("*.json")) == []


# ---------------------------------------------------------------- 2. 正路消费
def test_run_consumes_eligible_once_with_ledger_and_synthetic_delivery(store):
    msg_id = _submit(idem="run-ok")
    out = osleg_consumer.run_once(store)

    assert out["mode"] == "run" and out["ok"] is True
    assert out["counts"]["consumed"] == 1
    assert [item["msg_id"] for item in out["consumed"]] == [msg_id]
    assert out["protected"] == [] and out["malformed"] == []

    # gateway 生命周期被真实推进：processed + answered 戳记 + delivered 标签
    receipt = inbox_gateway.receipt(msg_id)["data"]
    assert receipt["state"] == "processed"
    assert receipt["answered"] is True and receipt["delivered"] is True
    record = receipt["record"]
    assert record["answered_by"] == "A0"
    assert record["answer_ref"] == "osleg_consumer/answers/%s.json" % msg_id
    assert record["channel"] == "synthetic"  # 只打标签，不发送
    assert record["delivered_ref"].startswith("synthetic:%s:" % msg_id)
    assert not (store / "inbox" / ("%s.json" % msg_id)).exists()
    assert (store / "processed" / ("%s.json" % msg_id)).exists()

    # 幂等台账：四步齐全、状态 done
    entry = _ledger(store)["entries"][msg_id]
    assert entry["status"] == "done"
    assert [step["step"] for step in entry["steps"]] == [
        "claimed",
        "answer_prepared",
        "answered",
        "delivered",
    ]
    assert entry["answer_ref"] == record["answer_ref"]
    assert entry["delivered_ref"] == record["delivered_ref"]
    assert entry["done_at"]

    # answer 摘要：合成、不发送、绑定 payload 摘要
    answer = _answer_doc(store, msg_id)
    assert answer["schema"] == "osleg-consumer-answer/v1"
    assert answer["msg_id"] == msg_id and answer["answered_by"] == "A0"
    assert answer["synthetic"] is True and answer["sent"] is False
    assert answer["delivery_channel"] == "not_sent"
    assert answer["payload_digest"] == entry["payload_digest"]
    assert len(answer["payload_digest"]) == 64


def test_rerun_is_idempotent_and_does_not_rewrite_evidence(store):
    msg_id = _submit(idem="rerun")
    first = osleg_consumer.run_once(store)
    assert first["counts"]["consumed"] == 1

    processed_path = store / "processed" / ("%s.json" % msg_id)
    ledger_path = store / "osleg_consumer" / "ledger.json"
    answer_path = store / "osleg_consumer" / "answers" / ("%s.json" % msg_id)
    before = {
        "processed": processed_path.read_bytes(),
        "ledger": ledger_path.read_bytes(),
        "answer": answer_path.read_bytes(),
    }
    second = osleg_consumer.run_once(store)
    assert second["ok"] is True
    assert second["counts"]["consumed"] == 0
    assert second["counts"]["attempted"] == 0
    assert second["counts"]["eligible"] == 0  # 已办单不再出现在待办计划里
    assert second["already_done"] == []
    # 重复运行不重复消费、不改写任何证据
    assert processed_path.read_bytes() == before["processed"]
    assert ledger_path.read_bytes() == before["ledger"]
    assert answer_path.read_bytes() == before["answer"]
    assert len(list((store / "processed").glob("*.json"))) == 1
    assert len(list((store / "inbox").glob("*.json"))) == 0
    receipt = inbox_gateway.receipt(msg_id)["data"]
    assert receipt["answered"] is True and receipt["delivered"] is True


# ---------------------------------------------------------------- 3. protected
def test_historical_real_order_never_consumed_although_whitelisted(store):
    """历史真单即使被伪造成 source=synthetic 也必须原地不动。"""
    eligible = _submit(idem="hist-eligible")
    hist_path = store / "inbox" / ("%s.json" % HISTORICAL_ID)
    _write_json(hist_path, _historical_record(source="synthetic"))
    hist_bytes = hist_path.read_bytes()

    out = osleg_consumer.run_once(store, limit=10)

    assert out["ok"] is True
    assert [item["msg_id"] for item in out["consumed"]] == [eligible]
    protected = {item["msg_id"]: item for item in out["protected"]}
    assert HISTORICAL_ID in protected
    assert protected[HISTORICAL_ID]["reason"] == "historical_real_order"
    # 原地未动：仍在 inbox、字节一致、无 processed、无台账
    assert hist_path.exists() and hist_path.read_bytes() == hist_bytes
    assert not (store / "processed" / ("%s.json" % HISTORICAL_ID)).exists()
    assert inbox_gateway.receipt(HISTORICAL_ID)["data"]["state"] == "inbox"
    assert HISTORICAL_ID not in _ledger(store)["entries"]


def test_non_whitelisted_source_is_skipped_and_untouched(store):
    msg_id = _submit(source="feishu_comm", idem="real-source")
    path = store / "inbox" / ("%s.json" % msg_id)
    raw = path.read_bytes()

    out = osleg_consumer.run_once(store)

    assert out["ok"] is True and out["consumed"] == []
    protected = {item["msg_id"]: item for item in out["protected"]}
    assert protected[msg_id]["reason"] == "source_not_whitelisted"
    assert protected[msg_id]["source"] == "feishu_comm"
    assert path.read_bytes() == raw
    assert inbox_gateway.receipt(msg_id)["data"]["state"] == "inbox"
    assert list((store / "processed").glob("*.json")) == []
    assert not (store / "osleg_consumer" / "ledger.json").exists()


def test_unregistered_kind_is_skipped(store):
    msg_id = "lg-kindnotallowed1"
    path = store / "inbox" / ("%s.json" % msg_id)
    _write_json(
        path,
        {
            "id": msg_id,
            "kind": "stock_consult_result",  # W4 业务 kind，未映射到 gateway 词表
            "source": "synthetic",
            "payload": {"report_id": "QR-1", "trace_id": "tr-1"},
            "state": "inbox",
        },
    )
    raw = path.read_bytes()

    out = osleg_consumer.run_once(store)

    assert out["ok"] is True and out["consumed"] == []
    protected = {item["msg_id"]: item for item in out["protected"]}
    assert protected[msg_id]["reason"] == "kind_not_allowed"
    assert path.read_bytes() == raw
    assert inbox_gateway.receipt(msg_id)["data"]["state"] == "inbox"


# ---------------------------------------------------------------- 4. malformed
def test_malformed_records_are_isolated_without_crash(store):
    good = _submit(idem="mal-good")
    inbox = store / "inbox"
    bad_json = inbox / "lg-broken1.json"
    bad_json.write_text("{not json", encoding="utf-8")
    missing_id = inbox / "lg-noid1.json"
    _write_json(
        missing_id, {"kind": "task_request", "source": "synthetic", "payload": {}}
    )
    mismatched = inbox / "lg-mismatch1.json"
    _write_json(
        mismatched,
        {
            "id": "lg-other1",
            "kind": "task_request",
            "source": "synthetic",
            "payload": {"report_id": "QR-1", "trace_id": "tr-1"},
            "state": "inbox",
        },
    )

    before = {p.name: p.read_bytes() for p in (bad_json, missing_id, mismatched)}
    out = osleg_consumer.run_once(store)

    assert out["ok"] is True  # 坏 JSON 隔离不崩，其余单仍被消费
    assert [item["msg_id"] for item in out["consumed"]] == [good]
    reasons = {item["reason"] for item in out["malformed"]}
    assert reasons == {"bad_json", "missing_fields:id", "id_filename_mismatch"}
    for path in (bad_json, missing_id, mismatched):
        assert path.read_bytes() == before[path.name]  # 不移动/不改写
        assert not (store / "processed" / path.name).exists()
    assert len(list((store / "processed").glob("*.json"))) == 1


# ---------------------------------------------------------------- 5. 失败与恢复
def test_consume_failure_keeps_message_pending_and_retry_succeeds(store, monkeypatch):
    msg_id = _submit(idem="retry")
    real_consume = inbox_gateway.consume
    calls = {"n": 0}

    def flaky_consume(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "ok": False,
                "error": {"code": "stub_consume_fail", "message": "injected"},
            }
        return real_consume(*args, **kwargs)

    monkeypatch.setattr(inbox_gateway, "consume", flaky_consume)
    failed = osleg_consumer.run_once(store)

    assert failed["ok"] is False
    assert failed["failed"][0]["msg_id"] == msg_id
    assert failed["failed"][0]["error"] == "consume_failed:stub_consume_fail"
    # 消费失败不得悄悄从 inbox 消失，也不得伪装已办
    assert (store / "inbox" / ("%s.json" % msg_id)).exists()
    assert not (store / "processed" / ("%s.json" % msg_id)).exists()
    entry = _ledger(store)["entries"][msg_id]
    assert entry["status"] == "failed"
    assert not any(step["step"] == "answered" for step in entry["steps"])

    monkeypatch.setattr(inbox_gateway, "consume", real_consume)
    retried = osleg_consumer.run_once(store)
    assert retried["ok"] is True and retried["counts"]["consumed"] == 1
    receipt = inbox_gateway.receipt(msg_id)["data"]
    assert receipt["state"] == "processed"
    assert receipt["answered"] is True and receipt["delivered"] is True
    assert _ledger(store)["entries"][msg_id]["status"] == "done"


def test_corrupt_ledger_fails_closed_without_consuming(store):
    msg_id = _submit(idem="corrupt-ledger")
    ledger = store / "osleg_consumer" / "ledger.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("{oops", encoding="utf-8")
    raw = ledger.read_bytes()

    with pytest.raises(osleg_consumer.ConsumerRefused):
        osleg_consumer.scan(store)
    with pytest.raises(osleg_consumer.ConsumerRefused):
        osleg_consumer.run_once(store)

    assert ledger.read_bytes() == raw
    assert (store / "inbox" / ("%s.json" % msg_id)).exists()
    assert list((store / "processed").glob("*.json")) == []
    assert inbox_gateway.receipt(msg_id)["data"]["state"] == "inbox"


# ---------------------------------------------------------------- 6. 隔离红线
def test_real_bridge_inbox_is_never_written(store):
    real = osleg_consumer.REAL_STORE
    before = _snapshot(real)

    # 显式闸：--run 对真实 bridge_inbox 直接失败关闭（连扫描都不做）
    with pytest.raises(osleg_consumer.ConsumerRefused):
        osleg_consumer.run_once(real)
    assert _snapshot(real) == before

    # 正常一轮隔离消费/扫描后，真实目录仍零变化（文件数/字节/逐文件摘要）
    _submit(idem="real-untouched")
    assert osleg_consumer.scan(store)["ok"] is True
    osleg_consumer.run_once(store)
    after = _snapshot(real)
    assert after["count"] == before["count"]
    assert after["bytes"] == before["bytes"]
    assert after["files"] == before["files"]


def test_scan_requires_explicit_existing_isolated_store_root(tmp_path):
    missing = tmp_path / "does-not-exist"
    with pytest.raises(osleg_consumer.ConsumerRefused):
        osleg_consumer.scan(missing)
    with pytest.raises(osleg_consumer.ConsumerRefused):
        osleg_consumer.run_once(missing)
    assert not missing.exists()


# ---------------------------------------------------------------- 7. CLI
def test_cli_run_with_limit_consumes_one_per_run(store, capsys):
    _submit(idem="cli-limit-a")
    _submit(
        source="e2e", kind="question", payload={"intent": "ask"}, idem="cli-limit-b"
    )

    rc = osleg_consumer.main(["--store-root", str(store), "--run", "--limit", "1"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["mode"] == "run" and out["ok"] is True
    assert out["counts"]["consumed"] == 1
    assert out["counts"]["eligible"] == 2
    assert len(list((store / "inbox").glob("*.json"))) == 1

    rc2 = osleg_consumer.main(["--store-root", str(store), "--run"])
    out2 = json.loads(capsys.readouterr().out)
    assert rc2 == 0 and out2["counts"]["consumed"] == 1
    assert list((store / "inbox").glob("*.json")) == []

    rc3 = osleg_consumer.main(["--store-root", str(store), "--run"])
    out3 = json.loads(capsys.readouterr().out)
    assert rc3 == 0 and out3["counts"]["consumed"] == 0
    assert out3["counts"]["attempted"] == 0
    assert out3["counts"]["eligible"] == 0
    assert len(list((store / "processed").glob("*.json"))) == 2


def test_cli_refuses_real_store_with_error_json(capsys):
    rc = osleg_consumer.main(["--store-root", str(osleg_consumer.REAL_STORE), "--run"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["ok"] is False and "隔离红线" in out["error"]


# ---------------------------------------------------------------- 8. 源码边界
def test_source_imports_no_network_or_heavy_dependencies():
    source = Path(osleg_consumer.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    forbidden = {
        "socket",
        "urllib",
        "http",
        "requests",
        "httpx",
        "aiohttp",
        "ssl",
        "subprocess",
        "feishu",
        "lark_oapi",
        "larksuite",
        "quant_backtest",
        "portal",
        "engine",
    }
    assert roots & forbidden == set()
    assert roots <= {
        "argparse",
        "hashlib",
        "json",
        "logging",
        "os",
        "re",
        "sys",
        "uuid",
        "datetime",
        "pathlib",
        "gateway",
        "__future__",
    }
    # 不启服务/不真投递：源码里不出现监听或发送栈调用
    for token in (
        "serve_forever",
        "build_server",
        "urlopen",
        "requests.",
        "larksuite",
        "open_id",
    ):
        assert token not in source
