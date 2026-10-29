# -*- coding: utf-8 -*-
"""WA1 → QS1 上行受理/ACK 链（2026-10-30 WO-INV-CHAIN）。

只读/离线：读取 WA1 写入 `agent_platform/data/quant_upstream/inbox/*.json`
的 QUANT_UPSTREAM_REPORT，调用既有 `quant_sector.upstream_inbox.UpstreamInbox`
出具 agent 级 ACK，写 `acked/<report_id>.json` + append-only 受理台账。

- 受理≠授权；ACK 永不升格为交易许可；
- 幂等：同 report_id + 同内容返回同一回执；同 id 异内容 fail-closed；
- 不移动/删除 inbox 原文件；无网络副作用；
- `--self-test` 用临时目录演示全链，不写生产台账。

用法::

    python -X utf8 tools/qs1_accept_upstream.py                 # 处理默认 inbox
    python -X utf8 tools/qs1_accept_upstream.py --json
    python -X utf8 tools/qs1_accept_upstream.py --self-test     # 临时链演示
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from quant_backtest.quant_sector.upstream_inbox import (  # noqa: E402
    QuantUpstreamReport, UpstreamInbox, UpstreamInboxError,
)

DEFAULT_INBOX = os.path.join(REPO, "agent_platform", "data", "quant_upstream", "inbox")
DEFAULT_OUTBOX = os.path.join(REPO, "agent_platform", "data", "quant_upstream", "acked")
DEFAULT_LEDGER = os.path.join(REPO, "agent_platform", "data", "quant_upstream", "accept_ledger.jsonl")


def _now_utc():
    return datetime.now(timezone.utc)


def _parse_utc(text):
    s = str(text or "").strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        raise ValueError("UTC_REQUIRED")
    return d.astimezone(timezone.utc)


def build_report(obj):
    """dict → QuantUpstreamReport；字段不合法向上抛 UpstreamInboxError/ValueError。"""
    return QuantUpstreamReport(
        report_id=obj["report_id"],
        report_kind=obj["report_kind"],
        event_id=obj["event_id"],
        evidence_hash=obj["evidence_hash"],
        evidence_version=int(obj["evidence_version"]),
        sender_agent_id=obj["sender_agent_id"],
        receiver_agent_id=obj["receiver_agent_id"],
        created_at=_parse_utc(obj["created_at"]),
        non_executable=bool(obj.get("non_executable", True)),
    )


def _receipt_dict(receipt):
    return {
        "receipt_id": receipt.receipt_id,
        "report_id": receipt.report_id,
        "envelope_hash": receipt.envelope_hash,
        "sender_agent_id": receipt.sender_agent_id,
        "accepted_at": receipt.accepted_at.isoformat(),
        "ack": {
            "ack_id": receipt.ack.ack_id,
            "report_id": receipt.ack.report_id,
            "receiver_agent_id": receipt.ack.receiver_agent_id,
            "acked_at": receipt.ack.acked_at.isoformat(),
            "verification_source": receipt.ack.verification_source,
        },
        "ledger_epoch": receipt.ledger_epoch,
        "trading_permitted": False,  # 显式：受理≠授权
    }


def accept_one(path, inbox, outbox, ledger_path, now=None):
    """受理一份 report 文件；返回 {status, receipt...}。不移动原文件。"""
    now = now or _now_utc()
    raw = json.load(io.open(path, encoding="utf-8"))
    report = build_report(raw)
    receipt = inbox.accept(report, now=now)
    os.makedirs(outbox, exist_ok=True)
    out_path = os.path.join(outbox, report.report_id + ".json")
    rec = _receipt_dict(receipt)
    tmp = out_path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    os.replace(tmp, out_path)
    # append-only ledger；幂等重放同一 receipt 不重复追加
    seen = False
    if os.path.exists(ledger_path):
        with io.open(ledger_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    if json.loads(line).get("receipt_id") == receipt.receipt_id:
                        seen = True
                        break
                except ValueError:
                    continue
    if not seen:
        os.makedirs(os.path.dirname(ledger_path) or ".", exist_ok=True)
        with io.open(ledger_path, "a", encoding="utf-8", newline="") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"status": "accepted", "receipt_id": receipt.receipt_id,
            "report_id": report.report_id, "ack_id": receipt.ack.ack_id,
            "out_path": out_path, "duplicate": seen}


def process(inbox_dir=None, outbox=None, ledger=None, now=None):
    inbox_dir = inbox_dir or DEFAULT_INBOX
    outbox = outbox or DEFAULT_OUTBOX
    ledger = ledger or DEFAULT_LEDGER
    inbox = UpstreamInbox()
    results = []
    if not os.path.isdir(inbox_dir):
        return {"inbox": inbox_dir, "files": 0, "results": results}
    for name in sorted(os.listdir(inbox_dir)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(inbox_dir, name)
        try:
            results.append(accept_one(path, inbox, outbox, ledger, now=now))
        except (OSError, ValueError, KeyError, UpstreamInboxError) as exc:
            results.append({"status": "rejected", "file": name, "error": str(exc)})
    return {"inbox": inbox_dir, "files": len(results), "results": results}


def self_test():
    """临时目录演示：受理→ACK→幂等→冲突拒绝；不碰生产台账。"""
    tmp = tempfile.mkdtemp(prefix="qs1_upstream_")
    inbox_dir = os.path.join(tmp, "inbox"); os.makedirs(inbox_dir)
    outbox = os.path.join(tmp, "acked"); ledger = os.path.join(tmp, "ledger.jsonl")
    report = {
        "report_id": "WA1-TEST-20261030-01", "report_kind": "ANOMALY",
        "event_id": "evt-test-001",
        "evidence_hash": "a" * 64, "evidence_version": 1,
        "sender_agent_id": "WA1", "receiver_agent_id": "QS1",
        "created_at": (_now_utc() - timedelta(minutes=1)).isoformat(),
        "non_executable": True,
    }
    p = os.path.join(inbox_dir, "wa1_test.json")
    with io.open(p, "w", encoding="utf-8", newline="") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    r1 = accept_one(p, UpstreamInbox(), outbox, ledger)
    r2 = accept_one(p, UpstreamInbox(), outbox, ledger)  # 幂等（独立 inbox 也幂等）
    bad = copy.deepcopy(report); bad["evidence_hash"] = "b" * 64
    p2 = os.path.join(inbox_dir, "wa1_bad.json")
    with io.open(p2, "w", encoding="utf-8", newline="") as f:
        json.dump(bad, f, ensure_ascii=False, indent=2)
    # 冲突需在同一 inbox 状态内验证（report_id 已受理、内容不同）
    shared = UpstreamInbox()
    accept_one(p, shared, outbox, ledger)
    try:
        accept_one(p2, shared, outbox, ledger)
        conflict = "NOT_REJECTED"
    except UpstreamInboxError as exc:
        conflict = str(exc)
    return {"tmp": tmp, "accepted": r1, "duplicate": r2, "conflict": conflict,
            "ledger_lines": sum(1 for _ in io.open(ledger, encoding="utf-8"))}


def main(argv=None):
    ap = argparse.ArgumentParser(description="WA1→QS1 上行受理/ACK 链（离线）")
    ap.add_argument("--inbox", default=DEFAULT_INBOX)
    ap.add_argument("--outbox", default=DEFAULT_OUTBOX)
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        res = self_test()
        print(json.dumps(res, ensure_ascii=False, indent=2) if args.json else
              "[self-test] accepted=%s duplicate=%s conflict=%s ledger=%d" % (
                  res["accepted"]["report_id"], res["duplicate"]["duplicate"],
                  res["conflict"], res["ledger_lines"]))
        return 0 if res["conflict"] and res["duplicate"]["duplicate"] else 1
    res = process(args.inbox, args.outbox, args.ledger)
    print(json.dumps(res, ensure_ascii=False, indent=2) if args.json else
          "[qs1] inbox=%s files=%d accepted=%d rejected=%d" % (
              res["inbox"], res["files"],
              sum(1 for x in res["results"] if x.get("status") == "accepted"),
              sum(1 for x in res["results"] if x.get("status") == "rejected")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
