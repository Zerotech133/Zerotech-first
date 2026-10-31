# -*- coding: utf-8 -*-
"""11 席能力迭代「确认回执」只读跟踪器（2026-10-31 WO-SEAT-ITER-RECEIPTS / task-28）。

用途（11-01 派工催办 → **11-03 09:30 硬复核**）
====================================================
    python -X utf8 tools/seat_iteration_receipts.py            # 人类摘要：11 席回执缺口
    python -X utf8 tools/seat_iteration_receipts.py --json     # 机读 JSON（同名 schema）
    python -X utf8 tools/seat_iteration_receipts.py --root <repo>   # 演练/测试（默认本仓）

11-01 派工后各席被要求回执「能力迭代1确认」；11-03 09:30 前用本工具复核
「11 席回执 0/11 还是 N/11、缺谁缺什么」——**只读跟踪，不代填、不伪造、
不改 config/派工/evidence**。退出码：0=11/11 齐；1=有缺口；2=自身/环境错误。

数据源（全部只读）
------------------
1) ``agent_platform/config/seat_iterations/<SEAT>.json``（schema=seat_iteration/v1；
   11 席＝ARCH1/DEV1–DEV8/DOC1/OPS1，真源同 ``tools/seat_iteration.py::TARGETS``）；
2) ``work/tianfeng_dispatch.jsonl``（田丰派工台账；``to`` 形如「研发部/DEV1」，
   ``status`` ∈ dispatched/accepted/done/blocked；追加式，最后一条为该席最新）；
3) 迭代配置内 ``learning`` / ``practice`` / ``assessment`` 的 ``evidence``
   （字符串或列表；若写的是仓内相对路径，则只读核对存在性，**不创建/不补写**）。
   注：``work/`` 在 .gitignore 内，fresh worktree 无派工台账属正常（显示无记录）。

回执判定口径（写死防「凑齐」，11-03 复核按此认账）
--------------------------------------------------
* ``config_state``：planned → learning → practice → assessment（按三阶段 status
  推进；全 planned=planned；assessment 非 planned=assessment）。
* ``evidence_ok``：三阶段 evidence **各至少一条非空**；若条目不空但写了仓内
  路径且该路径不存在 → 记 ``evidence_path_missing`` 缺口（证据不可核验）。
* ``mentor_confirmed``：``assessment.status == "passed"`` 且 assessment.evidence
  非空（**AI 不代签：只有导师评审 pass 才算确认**）；配置若显式落
  ``mentor_confirmed`` 布尔字段（历史/未来 schema）则以其为准。
* ``dispatch_status``：该席最近一条派工 status（无记录=none）。
* **``complete``（回执齐）＝ config_ok ∧ evidence_ok ∧ mentor_confirmed
  ∧ dispatch_status ∈ {accepted, done}**；任一不满足即缺口（逐项列 gaps）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:  # 真源：与 seat_iteration 共用 11 席清单（避免两处漂移）
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from seat_iteration import TARGETS as _SEAT_TARGETS  # noqa: E402
except Exception:  # noqa: BLE001  独立运行/工具异常：退回内置 11 席（与真源一致）
    _SEAT_TARGETS = {s: None for s in (
        "ARCH1", "DEV1", "DEV2", "DEV3", "DEV4", "DEV5", "DEV6", "DEV7",
        "DEV8", "DOC1", "OPS1")}

SEATS = tuple(_SEAT_TARGETS)
STAGES = ("learning", "practice", "assessment")
SCHEMA_SEAT_ITER = "seat_iteration/v1"
SCHEMA_REPORT = "seat_iteration_receipts/v1"
COMPLETE_DISPATCH = ("accepted", "done")
DONE_DISPATCH = ("accepted", "done")
PATH_PREFIXES = ("tools/", "agent_platform/", "deliverables/", "docs/",
                 "work/", "portal/", "quant_backtest/", "research/")
PATH_SUFFIXES = (".md", ".json", ".jsonl", ".py", ".csv", ".txt", ".log",
                 ".html", ".yaml", ".yml", ".png", ".jpg")


def _read_json(path):
    """读 JSON（utf-8-sig 容 BOM）；返回 (obj|None, 错误串)。"""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig")), ""
    except OSError as exc:
        return None, "read_error:%s" % exc
    except ValueError as exc:
        return None, "invalid_json:%s" % exc


def _str_list(value):
    """evidence 归一：字符串/列表/字典 → 非空字符串列表（只取文本，不猜）。"""
    out = []
    if isinstance(value, str):
        if value.strip():
            out.append(value.strip())
    elif isinstance(value, (list, tuple)):
        for item in value:
            out.extend(_str_list(item))
    elif isinstance(value, dict):
        for item in value.values():
            out.extend(_str_list(item))
    return out


def _is_evidence_path(text):
    t = str(text or "").strip()
    if not t or t.lower().startswith(("http://", "https://", "file://")):
        return False
    if t.startswith(("cmd:", "command:", "sha256:", "trace:", "run:", "git:")):
        return False
    return t.startswith(PATH_PREFIXES) or t.endswith(PATH_SUFFIXES)


def _evidence_info(root, iteration):
    """聚合三阶段 evidence：条数/是否空/仓内路径缺失（只读核对，不改文件）。"""
    by_stage, entries, missing = {}, [], []
    for stage in STAGES:
        ev = _str_list((iteration.get(stage) or {}).get("evidence"))
        by_stage[stage] = len(ev)
        entries.extend(ev)
        for item in ev:
            if not _is_evidence_path(item):
                continue
            p = Path(item)
            target = p if p.is_absolute() else (Path(root) / p)
            if not target.exists():
                missing.append(item)
    empty = all(by_stage[stage] == 0 for stage in STAGES)
    evidence_ok = all(by_stage[stage] > 0 for stage in STAGES) and not missing
    return {"evidence_ok": evidence_ok, "evidence_empty": empty,
            "evidence_by_stage": by_stage, "evidence_count": len(entries),
            "evidence_missing": sorted(set(missing))}


def _config_state(iteration):
    """迭代阶段：planned / learning / practice / assessment（按 status 推进）。"""
    status = {stage: str((iteration.get(stage) or {}).get("status") or "")
              .strip().lower() for stage in STAGES}
    if status["assessment"] not in ("", "planned"):
        return "assessment"
    if status["practice"] not in ("", "planned"):
        return "practice"
    if status["learning"] not in ("", "planned"):
        return "learning"
    return "planned"


def _mentor_confirmed(cfg, iteration):
    """导师确认：显式 mentor_confirmed 优先；否则 assessment passed+证据+导师。"""
    assessment = iteration.get("assessment") or {}
    for holder in (assessment, iteration, cfg):
        if isinstance(holder.get("mentor_confirmed"), bool):
            return bool(holder["mentor_confirmed"]), "explicit"
    mentor = str(assessment.get("mentor") or cfg.get("mentor") or "").strip()
    passed = str(assessment.get("status") or "").strip().lower() == "passed"
    has_evidence = bool(_str_list(assessment.get("evidence")))
    return (passed and has_evidence and bool(mentor)), "derived"


def load_dispatch(path):
    """读派工 JSONL：返回 (rows, 坏行数, 文件是否存在)；缺失文件不算错误。"""
    rows, errors = [], 0
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
            errors += 1
            continue
        if isinstance(rec, dict):
            rows.append(rec)
        else:
            errors += 1
    return rows, errors, True


def _seat_of(to):
    """派工 to 字段 → 席位（「研发部/DEV1」→ DEV1；未知/非研发席 → ""）。"""
    text = str(to or "").strip().replace("\\", "/")
    if not text:
        return ""
    tail = text.split("/")[-1].strip().upper()
    return tail if tail in SEATS else ""


def _dispatch_index(rows):
    """每席最新派工 + 最近一次「已回执/已接受」时间（追加式：后者覆盖前者）。"""
    latest, receipt_at = {}, {}
    for rec in rows:
        seat = _seat_of(rec.get("to"))
        if not seat:
            continue
        latest[seat] = rec
        status = str(rec.get("status") or "").strip().lower()
        at = str(rec.get("at") or "")
        if status in DONE_DISPATCH and at:
            receipt_at[seat] = at
    return latest, receipt_at


def _seat_report(root, seat, dispatch_latest, receipt_at):
    """单席报告（config + evidence + mentor + 派工 + gaps）。"""
    cfg_path = Path(root) / "agent_platform" / "config" / "seat_iterations" / (seat + ".json")
    cfg, err = _read_json(cfg_path)
    row = {"seat": seat, "config_file": cfg_path.relative_to(root).as_posix()
           if cfg_path.is_file() else "",
           "config_present": cfg is not None or cfg_path.is_file(),
           "config_ok": False, "config_error": err,
           "config_state": "missing", "stage_statuses": {},
           "evidence_ok": False, "evidence_empty": True,
           "evidence_by_stage": {stage: 0 for stage in STAGES},
           "evidence_count": 0, "evidence_missing": [],
           "cn_name": "", "mentor": "",
           "mentor_confirmed": False, "mentor_confirmed_source": "none",
           "dispatch_status": "none", "dispatch_id": "", "dispatch_task": "",
           "last_dispatch_at": "", "last_receipt_at": receipt_at.get(seat, ""),
           "complete": False, "gaps": []}
    if not isinstance(cfg, dict):
        row["gaps"].append("config_missing" if not cfg_path.is_file()
                           else "config_invalid")
    elif str(cfg.get("schema") or "") != SCHEMA_SEAT_ITER:
        row["config_ok"] = False
        row["gaps"].append("config_schema")
    else:
        iteration = cfg.get("iteration") or {}
        if not isinstance(iteration, dict):
            iteration = {}
        row["config_ok"] = True
        row["config_state"] = _config_state(iteration)
        row["stage_statuses"] = {
            stage: str((iteration.get(stage) or {}).get("status") or "")
            for stage in STAGES}
        row["cn_name"] = str(cfg.get("cn_name") or "")
        row["mentor"] = str(cfg.get("mentor") or "")
        ev = _evidence_info(root, iteration)
        row.update(ev)
        confirmed, source = _mentor_confirmed(cfg, iteration)
        row["mentor_confirmed"] = confirmed
        row["mentor_confirmed_source"] = source
    dispatch = dispatch_latest.get(seat) or {}
    row["dispatch_status"] = str(dispatch.get("status") or "none").strip().lower() or "none"
    row["dispatch_id"] = str(dispatch.get("id") or "")
    row["dispatch_task"] = str(dispatch.get("task") or "")
    row["last_dispatch_at"] = str(dispatch.get("at") or "")
    if cfg is not None and not row["last_receipt_at"]:
        row["last_receipt_at"] = str((cfg or {}).get("updated_at") or "")
    gaps = row["gaps"]
    if not row["config_ok"]:
        pass  # 已有 config_* 缺口
    else:
        empty_stages = [stage for stage in STAGES
                        if row["evidence_by_stage"][stage] == 0]
        if row["evidence_empty"]:
            gaps.append("evidence_empty")
        elif empty_stages:
            gaps.append("evidence_empty:" + ",".join(empty_stages))
        if row["evidence_missing"]:
            gaps.append("evidence_path_missing")
        if not row["mentor_confirmed"]:
            gaps.append("mentor_not_confirmed")
    status = row["dispatch_status"]
    if status == "none":
        gaps.append("dispatch_none")
    elif status not in COMPLETE_DISPATCH:
        gaps.append("dispatch_" + status)
    row["complete"] = not gaps
    return row


def _config_files(root):
    """config 目录下的迭代 JSON 数（只统计 <SEAT>.json；缺失不算错误）。"""
    d = Path(root) / "agent_platform" / "config" / "seat_iterations"
    if not d.is_dir():
        return 0
    return sum(1 for seat in SEATS if (d / (seat + ".json")).is_file())


def build_report(root=None):
    """只读构建回执报告（不写任何文件）。"""
    root = Path(root or ROOT)
    dispatch_path = root / "work" / "tianfeng_dispatch.jsonl"
    rows, dispatch_errors, dispatch_present = load_dispatch(dispatch_path)
    latest, receipt_at = _dispatch_index(rows)
    seats = [_seat_report(root, seat, latest, receipt_at) for seat in SEATS]
    complete = sum(1 for item in seats if item["complete"])
    by_state, by_dispatch = {}, {}
    for item in seats:
        by_state[item["config_state"]] = by_state.get(item["config_state"], 0) + 1
        by_dispatch[item["dispatch_status"]] = (
            by_dispatch.get(item["dispatch_status"], 0) + 1)
    gaps = {item["seat"]: item["gaps"] for item in seats if item["gaps"]}
    return {
        "schema": SCHEMA_REPORT,
        "root": str(root),
        "sources": {
            "config_dir": "agent_platform/config/seat_iterations",
            "config_files": _config_files(root),
            "dispatch": "work/tianfeng_dispatch.jsonl",
            "dispatch_present": dispatch_present,
            "dispatch_rows": len(rows),
            "dispatch_bad_lines": dispatch_errors,
        },
        "summary": {
            "seats_total": len(seats),
            "seats_complete": complete,
            "seats_gap": len(seats) - complete,
            "by_config_state": by_state,
            "by_dispatch_status": by_dispatch,
        },
        "gaps": gaps,
        "seats": seats,
    }


def render_summary(report):
    """人类摘要（11 席一表 + 汇总 + 判定口径），便于 11-03 复核直接贴。"""
    src = report["sources"]
    lines = [
        "11 席能力迭代回执跟踪（11-03 硬复核）· 只读 · root=%s" % report["root"],
        "数据源：%s（%d/11 席 config）｜%s（%d 行，缺失=%s）"
        % (src["config_dir"], src["config_files"], src["dispatch"],
           src["dispatch_rows"], "是" if not src["dispatch_present"] else "否"),
        "",
        "| 席位 | config状态 | evidence | mentor确认 | 派工状态 | 最近回执 | 回执 |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in report["seats"]:
        evidence = "空" if item["evidence_empty"] else (
            "缺" if item["evidence_missing"] else "%d条" % item["evidence_count"])
        lines.append("| %s | %s | %s | %s | %s | %s | %s |"
                     % (item["seat"], item["config_state"], evidence,
                        "是" if item["mentor_confirmed"] else "否",
                        item["dispatch_status"],
                        item["last_receipt_at"] or "-",
                        "✓" if item["complete"] else "✗"))
    summary = report["summary"]
    lines += ["",
              "汇总：回执齐 %d/%d；缺口 %d 席"
              % (summary["seats_complete"], summary["seats_total"],
                 summary["seats_gap"])]
    if report["gaps"]:
        lines.append("缺口明细：")
        for seat in SEATS:
            gaps = report["gaps"].get(seat)
            if gaps:
                lines.append("  - %s：%s" % (seat, "；".join(gaps)))
    lines += ["",
              "判定：complete = config_ok ∧ evidence_ok（三阶段各≥1条且路径可核验）"
              " ∧ mentor_confirmed（assessment passed+证据，AI 不代签）"
              " ∧ dispatch∈{accepted,done}；退出码 0=全齐 / 1=有缺口 / 2=自身错误。"]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="11 席能力迭代确认回执只读跟踪器（11-03 硬复核）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 报告")
    parser.add_argument("--root", default=None,
                        help="仓库根（默认本文件所在仓；演练/测试用）")
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser() if args.root else ROOT
    if not root.is_dir():
        print("[ERR] root 不是目录：%s" % root, file=sys.stderr)
        return 2
    try:
        report = build_report(root)
    except Exception as exc:  # noqa: BLE001 只读工具：自身异常=退出码 2，不冒假绿
        print("[ERR] 内部错误：%s" % exc, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(render_summary(report))
    return 0 if report["summary"]["seats_complete"] == report["summary"]["seats_total"] else 1


if __name__ == "__main__":
    sys.exit(main())
