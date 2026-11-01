# -*- coding: utf-8 -*-
"""11-03 组织落地硬复核 · 只读聚合器（2026-11-01 WO-ORG-LANDING-REVIEW-20261101 / task-42）。

一句话：把「11-03 09:30 组织落地复核」的分散事实聚合成一张 PASS/PARTIAL/FAIL 清单：
  1) 11 席能力迭代回执（直接复用 ``tools/seat_iteration_receipts.py::build_report``）；
  2) 派工台账最新状态——接口人 PM1/DSGN1/QA1、导师/学习对 DEV7/DEV1、交付对 DEV4/DEV8
     （读 ``work/tianfeng_dispatch.jsonl``，追加式取每席最后一条）；
  3) M4 正式验收签字（复用 ``tools/m4_signoff_status.py::build_report``，只读 approvals，
     不写签字/不改状态）；
  4) v1.1 批文/计划/M0 冻结清单的**文件只读状态**（代码 live ≠ 批文，未签只记 PARTIAL）。

纪律（硬约束）：
  * **只读**：不写 approvals/派工/config/deliverables；不写报告文件（本工具无 --out）；
  * 不代签、不代填、不把「代码已 live/测试通过」当批文/验收；
  * 缺文件/坏 JSON 只体现为对应项 FAIL/PARTIAL + 原因，不中断聚合（fail-visible）；
  * 退出码：0=全部 PASS｜1=有 PARTIAL/FAIL｜2=自身/环境错误（root 不存在、意外异常）。

用法::

    python -X utf8 tools/org_landing_review.py            # 人类摘要（11-03 09:30 复核用）
    python -X utf8 tools/org_landing_review.py --json     # 机读 JSON
    python -X utf8 tools/org_landing_review.py --root <repo>   # 演练/测试（默认本仓）
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import m4_signoff_status as M4  # noqa: E402  只读 M4 签字看板（不写签字）
import seat_iteration_receipts as RECEIPTS  # noqa: E402  11 席回执聚合器
try:  # 只读工具：子模块异常不应在 import 期炸掉整个复核（保持可诊断）
    _IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - import 失败时下面会体现
    _IMPORT_ERROR = "%s: %s" % (type(exc).__name__, exc)

SCHEMA = "org_landing_review/v1"
PASS, PARTIAL, FAIL = "PASS", "PARTIAL", "FAIL"
PASS_DISPATCH = ("accepted", "done")
IFACE_SEATS = ("PM1", "DSGN1", "QA1")
MENTOR_PAIR = ("DEV7", "DEV1")
DELIVERY_PAIR = ("DEV4", "DEV8")
V11_DOCS = (
    ("decision_pack", "v1.1 批文决策包",
     "deliverables/2026-10-31_v1.1批文决策包_v1.md"),
    ("plan", "11-01~11-15 研发计划",
     "deliverables/2026-10-31_研发计划_11月01至11月15_v1.md"),
    ("m0_freeze", "v1.1 M0 契约冻结执行清单",
     "deliverables/2026-11-01_v1.1_M0契约冻结执行清单_v1.md"),
)
# 已签署批文回执的文件名形态（只认「批文+回执/签批」命名；决策包不算批文）
V11_SIGNED_GLOBS = (
    "*v1.1*批文*回执*.md", "*v1.1*签批*.md", "*v1.1批文*签*.md",
    "*V11*批文*签*.md",
)
# 已签批内容的显式标记（用于回执文件内文判读；不含「代码 live/测试通过」等替代口径）
V11_SIGNED_MARKERS = ("V11:", "批文=签", "批文：签", "条件批", "APPROVED")


# ----------------------------------------------------------------------
# 派工台账
# ----------------------------------------------------------------------
def _seat_of(to) -> str:
    """「产品部/PM1」→ PM1；空/异常 → ""。"""
    text = str(to or "").strip().replace("\\", "/")
    return text.split("/")[-1].strip().upper() if text else ""


def load_dispatch(path):
    """读派工 JSONL：返回 (rows, bad_lines, exists)；缺文件不算错误。"""
    rows, bad = [], 0
    p = Path(path)
    if not p.is_file():
        return rows, 0, False
    try:
        lines = p.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return rows, 1, False
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(rec, dict):
            rows.append(rec)
        else:
            bad += 1
    return rows, bad, True


def latest_by_seat(rows):
    """每席最新派工（追加式：后者覆盖前者）；返回 {seat: row}。"""
    latest = {}
    for rec in rows or []:
        seat = _seat_of(rec.get("to"))
        if seat:
            latest[seat] = rec
    return latest


def _dispatch_seat_detail(seat, row):
    if not row:
        return {"seat": seat, "status": "none", "id": "", "task": "",
                "at": "", "reason": "派工台账无该席记录"}
    status = str(row.get("status") or "none").strip().lower() or "none"
    return {"seat": seat, "status": status, "id": str(row.get("id") or ""),
            "task": str(row.get("task") or ""), "at": str(row.get("at") or ""),
            "reason": ""}


def _dispatch_group_item(item_id, name, seats, latest, source_meta):
    details = [_dispatch_seat_detail(seat, latest.get(seat)) for seat in seats]
    statuses = [d["status"] for d in details]
    reasons = []
    if any(s in ("blocked", "none") for s in statuses):
        status = FAIL
        for d in details:
            if d["status"] == "blocked":
                reasons.append("%s 派工 blocked（%s）" % (d["seat"], d["task"] or "-"))
            elif d["status"] == "none":
                reasons.append("%s 无派工记录" % d["seat"])
    elif all(s in PASS_DISPATCH for s in statuses):
        status = PASS
        reasons.append("全部已回执/完成：%s"
                       % ",".join("%s=%s" % (d["seat"], d["status"]) for d in details))
    else:
        status = PARTIAL
        for d in details:
            if d["status"] not in PASS_DISPATCH:
                reasons.append("%s 最新派工 %s（未回执；%s）"
                               % (d["seat"], d["status"], d["task"] or "-"))
    if source_meta.get("bad_lines"):
        reasons.append("派工台账坏行 %d 条（已跳过，按缺口披露）" % source_meta["bad_lines"])
    return {"id": item_id, "name": name, "status": status,
            "reasons": reasons, "details": {"seats": details,
                                            "source": source_meta.get("path", "")}}


# ----------------------------------------------------------------------
# 11 席回执（复用 seat_iteration_receipts）
# ----------------------------------------------------------------------
def _seat_receipts_item(root):
    try:
        report = RECEIPTS.build_report(root)
    except Exception as exc:  # noqa: BLE001 只读聚合：子聚合异常→FAIL 并给原因
        return {"id": "seat_receipts", "name": "11 席能力迭代回执",
                "status": FAIL,
                "reasons": ["seat_iteration_receipts 聚合异常：%s: %s"
                            % (type(exc).__name__, str(exc)[:200])],
                "details": {}}
    summary = report.get("summary") or {}
    total = int(summary.get("seats_total") or 0)
    done = int(summary.get("seats_complete") or 0)
    gaps = report.get("gaps") or {}
    reasons = ["%d/%d 席回执齐" % (done, total)]
    for seat in report.get("seats") or []:
        if seat.get("gaps"):
            reasons.append("%s 缺口：%s" % (seat.get("seat"),
                                          "；".join(seat.get("gaps") or [])))
    if done == total and total > 0:
        status = PASS
    elif done > 0:
        status = PARTIAL
    else:
        status = FAIL
    sources = report.get("sources") or {}
    return {"id": "seat_receipts", "name": "11 席能力迭代回执", "status": status,
            "reasons": reasons,
            "details": {"completed": done, "total": total, "gaps": gaps,
                        "sources": sources}}


# ----------------------------------------------------------------------
# M4 签字（复用 m4_signoff_status，只读）
# ----------------------------------------------------------------------
def _m4_item(root):
    ledger = Path(root) / "agent_platform" / "data" / "approvals.jsonl"
    config = Path(root) / "agent_platform" / "config" / "approval_signers.json"
    try:
        report = M4.build_report(
            task=M4.DEFAULT_TASK, required=list(M4.DEFAULT_REQUIRED),
            ledger=ledger, config=config,
            evidence_roots=[Path(root), Path(root).parent],
            report_dir=Path(root) / "agent_platform" / "data" / "shadow")
    except Exception as exc:  # noqa: BLE001
        return {"id": "m4_signoff", "name": "M4 正式验收签字/证据", "status": FAIL,
                "reasons": ["m4_signoff_status 聚合异常：%s: %s"
                            % (type(exc).__name__, str(exc)[:200])],
                "details": {}}
    evidence_missing = list(report.get("evidence_missing") or [])
    reasons = list(report.get("reasons") or [])
    if evidence_missing:
        reasons.append("必须证据缺失：%s" % ",".join(evidence_missing))
    if not report.get("complete"):
        if evidence_missing or not (report.get("approvals") or {}).get("exists"):
            status = FAIL
        else:
            status = PARTIAL
    else:
        status = PASS
        reasons.append("签字齐+必须证据齐（state=complete）")
    return {"id": "m4_signoff", "name": "M4 正式验收签字/证据", "status": status,
            "reasons": reasons,
            "details": {"task": report.get("task"),
                        "complete": report.get("complete"),
                        "state": report.get("state"),
                        "signed": report.get("signed"),
                        "missing": report.get("missing"),
                        "signed_count": report.get("signed_count"),
                        "evidence_missing": evidence_missing,
                        "approvals": report.get("approvals"),
                        "read_only": True}}


# ----------------------------------------------------------------------
# v1.1 批文/计划/M0 文件只读状态
# ----------------------------------------------------------------------
def _deliverable_roots(root):
    roots = [Path(root) / "deliverables", Path(root).parent / "deliverables"]
    seen, out = set(), []
    for r in roots:
        key = str(r.resolve()) if r.exists() else str(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _find_rel(roots, rel):
    for base in roots:
        # rel 形如 deliverables/xxx；base 已是 deliverables 目录时用其最后一段
        cand = Path(base) / Path(rel).name
        if cand.is_file():
            return cand
    return None


def _read_text(path):
    for enc in ("utf-8-sig", "utf-16", "gbk"):
        try:
            return Path(path).read_text(encoding=enc, errors="strict")
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError:
            return ""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _find_signed_v11(roots):
    for base in roots:
        for pattern in V11_SIGNED_GLOBS:
            for hit in sorted(glob.glob(str(Path(base) / pattern))):
                text = _read_text(hit)
                if any(marker in text for marker in V11_SIGNED_MARKERS):
                    return hit
    return None


def _v11_item(root):
    roots = _deliverable_roots(root)
    found, missing = [], []
    for key, label, rel in V11_DOCS:
        path = _find_rel(roots, rel)
        (found if path else missing).append(
            {"key": key, "label": label, "path": str(path) if path else rel})
    signed = _find_signed_v11(roots)
    reasons = []
    if signed:
        status = PASS
        reasons.append("已见批文签署证据：%s" % signed)
    elif missing:
        status = PARTIAL if found else FAIL
        reasons.append("缺 v1.1 文件：%s" % ",".join(x["label"] for x in missing))
        reasons.append("批文未见签署证据（代码已 live ≠ 批文；不代签/不代填）")
    else:
        status = PARTIAL
        reasons.append("批文未见签署证据（仅决策包/计划/M0 清单；代码已 live ≠ 批文）")
    return {"id": "v11_batch", "name": "v1.1 批文/计划/M0 文件状态", "status": status,
            "reasons": reasons,
            "details": {"found": found, "missing": [x["key"] for x in missing],
                        "signed_evidence": str(signed) if signed else "",
                        "search_roots": [str(r) for r in roots]}}


# ----------------------------------------------------------------------
# 聚合
# ----------------------------------------------------------------------
def build_review(root=None, dispatch_path=None):
    """只读聚合：返回 {schema, overall, summary, items, read_only, ...}。"""
    root = Path(root or ROOT)
    dispatch = Path(dispatch_path) if dispatch_path else (
        root / "work" / "tianfeng_dispatch.jsonl")
    rows, bad_lines, dispatch_exists = load_dispatch(dispatch)
    latest = latest_by_seat(rows)
    source_meta = {"path": str(dispatch), "exists": dispatch_exists,
                   "rows": len(rows), "bad_lines": bad_lines}
    items = [
        _seat_receipts_item(root),
        _dispatch_group_item("iface_seats", "接口人 PM1/DSGN1/QA1",
                             IFACE_SEATS, latest, source_meta),
        _dispatch_group_item("mentor_pair", "导师/学习对 DEV7/DEV1",
                             MENTOR_PAIR, latest, source_meta),
        _dispatch_group_item("delivery_pair", "交付对 DEV4/DEV8",
                             DELIVERY_PAIR, latest, source_meta),
        _m4_item(root),
        _v11_item(root),
    ]
    counts = {PASS: 0, PARTIAL: 0, FAIL: 0}
    for item in items:
        counts[item["status"]] += 1
    if counts[FAIL]:
        overall = FAIL
    elif counts[PARTIAL]:
        overall = PARTIAL
    else:
        overall = PASS
    return {
        "schema": SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "root": str(root),
        "read_only": True,
        "overall": overall,
        "summary": {"total": len(items), PASS: counts[PASS],
                    PARTIAL: counts[PARTIAL], FAIL: counts[FAIL]},
        "items": items,
        "dispatch_source": source_meta,
        "import_error": _IMPORT_ERROR,
    }


def render_summary(review):
    """人类摘要：每项 PASS/PARTIAL/FAIL + 原因（11-03 09:30 复核可直接贴）。"""
    lines = [
        "11-03 组织落地硬复核（只读）· overall=%s · root=%s"
        % (review.get("overall"), review.get("root")),
        "汇总：%d 项｜PASS %d｜PARTIAL %d｜FAIL %d"
        % ((review.get("summary") or {}).get("total", 0),
           (review.get("summary") or {}).get(PASS, 0),
           (review.get("summary") or {}).get(PARTIAL, 0),
           (review.get("summary") or {}).get(FAIL, 0)),
        "",
    ]
    for item in review.get("items") or []:
        lines.append("[%s] %s" % (item["status"], item["name"]))
        for reason in item.get("reasons") or []:
            lines.append("    - %s" % reason)
    src = review.get("dispatch_source") or {}
    lines += ["",
              "派工台账：%s（exists=%s rows=%s bad_lines=%s）"
              % (src.get("path"), src.get("exists"), src.get("rows"),
                 src.get("bad_lines")),
              "判定：PASS=事实齐（回执/签字/批文/派工均已回）；PARTIAL=有进展但未闭环；"
              "FAIL=硬缺/异常。只读聚合，不代签、不写任何台账。",
              "退出码：0=全部 PASS / 1=有 PARTIAL/FAIL / 2=自身或环境错误。"]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="11-03 组织落地硬复核只读聚合器")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--root", default=None,
                        help="仓库根（默认本文件所在仓；演练/测试用）")
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser() if args.root else ROOT
    if not root.is_dir():
        print("[ERR] root 不是目录：%s" % root, file=sys.stderr)
        return 2
    try:
        review = build_review(root)
    except Exception as exc:  # noqa: BLE001 只读工具自身异常=退出码 2，不冒假绿
        print("[ERR] 内部错误：%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(review, ensure_ascii=False, indent=1))
    else:
        print(render_summary(review))
    return 0 if review.get("overall") == PASS else 1


if __name__ == "__main__":
    sys.exit(main())
