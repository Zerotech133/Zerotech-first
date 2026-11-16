#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""INTEL0 cloud independent precheck - Stage 1 dry-run tool (A0 order 2026-11-16).

After a request from another seat is accepted and BEFORE any reply is issued,
an independent cloud-model review must be prepared. This dry-run tool:

  * reads bridge_inbox/inbox read-only
  * builds the review prompt from 8 FIXED criteria
  * emits a JSONL "pending review" ledger with a four-part reply template
  * NEVER calls the cloud, NEVER replies, NEVER edits state, NEVER writes repository state

Live cloud invocation is deliberately NOT implemented here: it was not part
of the 2026-11-16 A0 order. --live exits with an explicit refusal so nobody can
mistake this tool for a wired production loop.

Landed in tools/ under the A0 five-elements order; QA1 countersign still pending.

Discipline: received != ack != decided; degraded must never be faked as cloud
success; author self-review does not count as independent review; no dispatch,
no decision, no invented numbers or sources.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

NL = chr(10)
INBOX_DEFAULT = Path(__file__).resolve().parents[1] / "agent_platform" / "bridge_inbox" / "inbox"
OUT_DEFAULT = Path(tempfile.gettempdir()) / "intel0_precheck_dryrun.jsonl"
CRITERIA_VERSION = "intel0-precheck-criteria/v1"

CRITERIA = [
    ("C1", "请求主体是否在册（席位／agent_id 可解析），有无冒充或越席"),
    ("C2", "ADR-012 五要素是否齐全：命令全文／影响面／不可逆点／替代方案与已尝试／回滚预案"),
    ("C3", "是否含越权、代签、代裁决、代派单要求"),
    ("C4", "是否含对外发布、推送、分发、外发闸动作"),
    ("C5", "是否含删除、不可逆、系统级配置或生产服务器变更"),
    ("C6", "时限、有效期、适用范围是否明确（无期限不等于不紧急，需 A0 定口径）"),
    ("C7", "证据与数字来源是否可追溯；有无编造数字、旧口径顶当前、不可测写成不存在"),
    ("C8", "是否与既有口径冲突：received≠ack≠decided、作者自评不算独立复核、口径版本一致"),
]

L2_WORDS = ("删除", "不可逆", "对外", "发布", "推送", "分发", "生产服务器",
            "计划任务", "防火墙", "代理", "代签", "派单", "上线", "重启")


def cp(s):
    return ",".join(str(ord(c)) for c in str(s))


def load(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"__parse_error__": type(exc).__name__}


def build_prompt(rec):
    lines = [
        "你是独立云端复核模型，受 INTEL0（情报部受理席）委托，对其他席位送达的请求做「有没有问题」的复核。",
        "只从受理与合规职能面判断，不代派单、不代拍板、不代签、不编造具体数字或来源。",
        "必须区分事实／推断／需查证；输出一个 JSON 对象：conclusion/evidence/risks/recommendation/questions。",
        "固定判据 " + CRITERIA_VERSION + "：",
    ]
    lines += ["  " + k + " " + v for k, v in CRITERIA]
    lines += [
        "待复核请求：",
        "  IR: " + str(rec["ir_id"]),
        "  target: " + str(rec["target"]) + " / kind: " + str(rec["kind"]),
        "  actor: " + str(rec["actor"]) + " / source: " + str(rec["source"]),
        "  title: " + str(rec["title"]),
        "  body_excerpt: " + str(rec["body_excerpt"]),
        "注意：target=A0 时只做 INTEL0 职能面预审，不替指挥席思考；裁决权归 A0／老田。",
    ]
    return NL.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="INTEL0 cloud precheck (Stage 1, dry-run only)")
    ap.add_argument("--inbox", default=str(INBOX_DEFAULT))
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--limit", type=int, default=1)
    ap.add_argument("--target", default="")
    ap.add_argument("--kind", default="")
    ap.add_argument("--live", action="store_true",
                    help="REFUSED: live cloud call is outside the 2026-11-16 A0 order")
    args = ap.parse_args(argv)

    if args.live:
        print("REFUSED: --live is outside the 2026-11-16 A0 order.", file=sys.stderr)
        print("Live cloud invocation requires a separate A0 five-elements order.",
              file=sys.stderr)
        return 3

    inbox = Path(args.inbox)
    if not inbox.is_dir():
        print("inbox_missing: " + str(inbox), file=sys.stderr)
        return 2

    picked = []
    for p in sorted(inbox.glob("lg-*.json")):
        d = load(p)
        if "__parse_error__" in d:
            continue
        pl = d.get("payload") or {}
        if args.target and str(pl.get("target") or "") != args.target:
            continue
        if args.kind and str(d.get("kind") or "") != args.kind:
            continue
        picked.append((p, d, pl))
        if len(picked) >= args.limit:
            break

    out = []
    for p, d, pl in picked:
        raw = p.name + "|" + str(d.get("id") or "")
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
        ir_id = "IR-PROTO-" + digest
        title = str(pl.get("title") or "")
        body = str(pl.get("body") or "")
        joined = title + " " + body
        flags = sorted({w for w in L2_WORDS if w in joined})
        rec = {
            "ir_id": ir_id,
            "source_file": p.name,
            "idempotency_key": str(d.get("idempotency_key") or ""),
            "kind": str(d.get("kind") or "?"),
            "state": str(d.get("state") or "?"),
            "target": str(pl.get("target") or "?"),
            "actor": str(pl.get("actor") or "?"),
            "source": str(d.get("source") or "?"),
            "title": title[:140],
            "body_excerpt": body[:400],
            "submitted_at": str(d.get("submitted_at") or ""),
        }
        rec["rule_prescreen_l2_words"] = flags
        rec["rule_prescreen_note"] = (
            "规则预筛，不是云端复核结论；仅用于提示人工关注，不得当作 verdict。"
        )
        rec["criteria_version"] = CRITERIA_VERSION
        rec["prompt_preview"] = build_prompt(rec)[:900]
        rec["cloud"] = {
            "binding": None,
            "model_family": None,
            "degraded": None,
            "latency_ms": None,
            "caliber_version": CRITERIA_VERSION,
            "channel": "hq_cloud_think.py --lead INTEL0 (NOT CALLED)",
        }
        rec["review_state"] = "unavailable"
        rec["verdict"] = "pending_cloud_review"
        rec["reply_template"] = {
            "part1_acceptance": "受理确认：IR 编号 + 幂等哈希 + 受理时间（NOT SENT）",
            "part2_cloud_findings": "云端复核发现：conclusion/evidence/risks/recommendation/questions + binding/degraded/口径版本 —— 当前 unavailable，如实写「未完成独立复核」",
            "part3_boundary": "received ≠ ack ≠ decided；本回执不代表同意内容",
            "part4_escalation": "交 A0／老田书面裁决；外发／推送／五要素动作一律上行",
        }
        rec["discipline"] = [
            "degraded 不得伪造云端成功",
            "作者自评／规则预筛不算独立复核",
            "云端超时即停在 review_state=unavailable，不得跳过本门直接回执",
            "default dry-run：不回复、不改 state、不写生产、不联网",
        ]
        out.append(rec)

    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(NL.join(json.dumps(r, ensure_ascii=False) for r in out) + NL,
                    encoding="utf-8")

    print("MODE=dry-run")
    print("cloud_called=False replies_sent=False state_changed=False repo_state_written=False")
    print("criteria_version=" + CRITERIA_VERSION)
    print("picked=" + str(len(out)) + " cp=" + cp(len(out)))
    print("out=" + str(dest))
    print("out_bytes=" + str(dest.stat().st_size))
    for r in out:
        print("IR=" + r["ir_id"] + " target=" + r["target"] +
              " kind=" + r["kind"] + " review_state=" + r["review_state"] +
              " verdict=" + r["verdict"] +
              " l2_words=" + (",".join(r["rule_prescreen_l2_words"]) or "none"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
