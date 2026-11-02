#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HQ LegionReport 只读模型（C3，2026-11-03）。

按**军团 / owner** 聚合 `.worktrees/tasks.json` 任务板（只读），产出「军团 → HQ」汇报对象：
progress（0–100）/ status（green|amber|red）/ blockers / evidence / eta。
不派单、不写盘、不改任务；无任务板数据 → fail-visible（ok=false, error=no_tasks_data, rc=2）。

status 规则（防假绿，口径见 docs/specs/2026-11-03_HQ_directive_report_contract_v1.md）：
  · scope 内 0 任务        → amber（no_tasks_in_scope；没有证据不许报绿）
  · aborted 占比 ≥ 10%     → red
  · 有 doing/review/parked → amber
  · 有 aborted（占比<10%） → amber
  · 其余（全 done、占比<10%）→ green
eta 来源：Directive 只读模型（hq_directives）中该 scope 的**未关闭指令 due**取最大值；
无 due → null（不猜）。

用法：
  python -X utf8 tools/hq_reports.py                       # 人读摘要
  python -X utf8 tools/hq_reports.py --json                # 机器可读（stdout 纯 JSON）
  python -X utf8 tools/hq_reports.py --json --legion acad1-legion
  python -X utf8 tools/hq_reports.py --json --owner ACAD1  --limit-blockers 5

退出码：0 成功；2 fail-visible（无任务板/参数/环境错误）。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / ".worktrees" / "tasks.json"

SCHEMA = "hq/legion_reports/v1"
READ_ONLY = True
ABORT_RED_RATIO = 0.1
ACTIVE_STATUSES = ("doing", "review")
BLOCK_MARKERS = ("阻塞", "blocked", "blocker", "[blocked]")
# 仅当 hq_directives 不可用时的降级映射（正常路径复用 hq_directives.OWNER_LEGION）
FALLBACK_OWNER_LEGION = {
    "ACAD1": "acad1-legion", "DATA1": "data-warehouse",
    "D1": "lusu-d1-flow", "LUSU1": "lusu-d1-flow",
    "STRAT1": "quant-strategy-line", "ST1": "quant-strategy-line",
    "STRAT2": "quant-strategy-line", "BACK1": "quant-strategy-line",
    "HUANGZHONG": "huangzhong-acceptance",
    "A0": "hq-command", "AI1": "hq-command", "ARCH1": "hq-command",
    "DSH": "hq-command", "DSH-Lead": "hq-command", "LAOTIAN": "hq-command",
    "ZHAOYUN": "hq-governance", "GUANYU": "hq-audit",
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return str(path).replace("\\", "/")


def _text(v, limit: int = 400) -> str:
    return str(v if v is not None else "").strip()[:limit]


def _load_directives_module():
    """按文件路径加载兄弟模块（避免 sys.path/同名包问题）。"""
    path = Path(__file__).resolve().with_name("hq_directives.py")
    spec = importlib.util.spec_from_file_location("hq_directives_for_reports", path)
    if spec is None or spec.loader is None:
        raise ImportError("hq_directives unavailable")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_tasks(path: Path) -> tuple[dict | None, str]:
    p = Path(path)
    if not p.is_file():
        return None, "tasks_file_missing:%s" % _rel(p)
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None, "tasks_file_invalid"
    if not isinstance(data, dict):
        return None, "tasks_file_not_object"
    return data, ""


def _legion_map():
    try:
        mod = _load_directives_module()
        return dict(getattr(mod, "OWNER_LEGION", {}) or {}), mod, ""
    except Exception as exc:  # noqa: BLE001 —— 降级但不假绿：LegionReport 仍可按兜底映射
        return dict(FALLBACK_OWNER_LEGION), None, "directives_unavailable:%s" % type(exc).__name__


def _task_created(rec: dict) -> str:
    return _text(rec.get("created"), 32)


def _mask_desc(desc: str, limit: int = 200) -> str:
    return _text(desc, limit).replace("\n", " ")


def _blocker_rows(task_id: str, rec: dict, limit: int = 400) -> dict | None:
    status = _text(rec.get("status"), 16)
    desc = _text(rec.get("desc"))
    reason = (_text(rec.get("abort_reason") or rec.get("park_reason") or rec.get("note"), limit)
              or desc)
    blocked = (status == "aborted" or status == "parked"
               or any(m in (desc + " " + reason) for m in BLOCK_MARKERS))
    if not blocked:
        return None
    return {
        "task_id": task_id,
        "owner": _text(rec.get("owner"), 24),
        "status": status,
        "reason": _text(reason, limit),
        "created": _task_created(rec),
        "desc": _mask_desc(desc, 160) if desc else "",
    }


def _metrics(tasks: dict, owner: str | None = None, legion: str | None = None,
             owner_legion: dict | None = None, max_blockers: int = 10) -> dict:
    rows = []
    owner_legion = owner_legion or {}
    for tid, rec in tasks.items():
        if not isinstance(rec, dict):
            continue
        o = _text(rec.get("owner"), 24)
        if owner is not None and o != owner:
            continue
        if legion is not None and owner_legion.get(o, "unmapped") != legion:
            continue
        rows.append((str(tid), rec))
    counts = {"done": 0, "aborted": 0, "parked": 0, "doing": 0, "review": 0, "other": 0}
    for _, rec in rows:
        st = _text(rec.get("status"), 16)
        if st in counts:
            counts[st] += 1
        else:
            counts["other"] += 1
    total = len(rows)
    active = counts["doing"] + counts["review"]
    done = counts["done"]
    aborted = counts["aborted"]
    parked = counts["parked"]
    aborted_ratio = round(aborted / total, 4) if total else 0.0
    progress = round(done / total * 100.0, 1) if total else 0.0
    if total == 0:
        status, reason = "amber", "no_tasks_in_scope"
    elif aborted_ratio >= ABORT_RED_RATIO:
        status, reason = "red", "aborted_ratio>=%.0f%%" % (ABORT_RED_RATIO * 100)
    elif active > 0 or parked > 0:
        status, reason = "amber", "active_tasks>0"
    elif aborted > 0:
        status, reason = "amber", "aborted>0"
    else:
        status, reason = "green", "all_done"
    blockers = []
    for tid, rec in sorted(rows, key=lambda x: _task_created(x[1]), reverse=True):
        b = _blocker_rows(tid, rec)
        if b:
            blockers.append(b)
        if len(blockers) >= max_blockers:
            break
    return {
        "tasks_total": total,
        "tasks_done": done,
        "tasks_active": active,
        "tasks_parked": parked,
        "tasks_aborted": aborted,
        "tasks_other": counts["other"],
        "aborted_ratio": aborted_ratio,
        "progress": progress,
        "status": status,
        "status_reason": reason,
        "blockers_count": len(blockers),
        "blockers": blockers,
    }


def _directives_view(directives_dir_args: dict) -> tuple[list, list, str]:
    """→ (open directives, warnings, source note)。失败降级但不影响任务聚合。"""
    try:
        mod = _load_directives_module()
    except Exception as exc:  # noqa: BLE001
        return [], ["directives_unavailable:%s" % type(exc).__name__], "off"
    payload = mod.build_directives(**directives_dir_args)
    if not payload.get("ok"):
        return [], ["directives_unavailable:%s" % payload.get("error")], "off"
    open_rows = [d for d in payload.get("directives") or []
                 if d.get("status") not in ("closed",)]
    return open_rows, list(payload.get("warnings") or []), "on"


def _eta_for(scope: dict, open_directives: list) -> tuple[str | None, int]:
    """scope={'owner':o} 或 {'legion':l}；due 取 ISO 日期最大值（不猜）。"""
    dues = []
    matched = 0
    for d in open_directives:
        if "owner" in scope:
            if _text(d.get("owner"), 24) != scope["owner"]:
                continue
        else:
            if _text(d.get("legion"), 32) != scope["legion"]:
                continue
        matched += 1
        due = _text(d.get("due"), 40)
        if due[:10].count("-") == 2 and due[:4].isdigit():
            dues.append(due[:10])
    return (max(dues) if dues else None), matched


def _evidence(tasks_rel: str, m: dict, directives_source: str, open_count: int) -> list:
    return [
        "source:%s" % tasks_rel,
        "tasks_total=%d" % m["tasks_total"],
        "done=%d aborted=%d active=%d parked=%d" % (
            m["tasks_done"], m["tasks_aborted"], m["tasks_active"], m["tasks_parked"]),
        "directives_source=%s open=%d" % (directives_source, open_count),
    ]


def _fail(error: str, searched: list, warnings: list) -> dict:
    return {"schema": SCHEMA, "ok": False, "error": error, "searched": searched,
            "warnings": warnings, "read_only": READ_ONLY,
            "generated_at": _now(), "legions": [], "owners": []}


def build_reports(tasks_file=None, meetings_dir=None, council_dir=None,
                  max_blockers: int = 10) -> dict:
    """聚合 LegionReport（纯函数；只读任务板 + 只读 Directive）。"""
    tasks_path = Path(tasks_file or TASKS)
    tasks, terr = _load_tasks(tasks_path)
    searched = [_rel(tasks_path)]
    if terr:
        return _fail("no_tasks_data", searched, [terr])
    owner_legion, _mod, map_warn = _legion_map()
    warnings = [map_warn] if map_warn else []
    dir_args = {}
    if meetings_dir:
        dir_args["meetings_dir"] = str(meetings_dir)
    if council_dir:
        dir_args["council_dir"] = str(council_dir)
    dir_args["tasks_file"] = str(tasks_path)
    open_directives, d_warn, d_source = _directives_view(dir_args)
    warnings += d_warn

    legions = sorted(set(owner_legion.values())) + (
        ["unmapped"] if any(
            owner_legion.get(_text(v.get("owner"), 24), "unmapped") == "unmapped"
            for v in tasks.values() if isinstance(v, dict)) else [])
    legion_rows = []
    for legion in legions:
        m = _metrics(tasks, owner=None, legion=legion, owner_legion=owner_legion,
                     max_blockers=max_blockers)
        eta, matched = _eta_for({"legion": legion}, open_directives)
        row = {"scope": {"legion": legion}, **m,
               "eta": eta, "directives_open": matched,
               "evidence": _evidence(_rel(tasks_path), m, d_source, matched)}
        legion_rows.append(row)

    owners = sorted({_text(v.get("owner"), 24) for v in tasks.values() if isinstance(v, dict)}
                    - {""})
    owner_rows = []
    for owner in owners:
        m = _metrics(tasks, owner=owner, owner_legion=owner_legion,
                     max_blockers=max_blockers)
        eta, matched = _eta_for({"owner": owner}, open_directives)
        owner_rows.append({"scope": {"owner": owner}, "legion": owner_legion.get(owner, "unmapped"),
                           **m, "eta": eta, "directives_open": matched,
                           "evidence": _evidence(_rel(tasks_path), m, d_source, matched)})
    owner_rows.sort(key=lambda r: (-r["tasks_total"], r["scope"]["owner"]))

    status_counts = {"green": 0, "amber": 0, "red": 0}
    for r in legion_rows:
        status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1
    return {
        "schema": SCHEMA,
        "ok": True,
        "read_only": READ_ONLY,
        "generated_at": _now(),
        "sources": {
            "tasks_file": _rel(tasks_path),
            "tasks": len(tasks),
            "directives_source": d_source,
            "legions": len(legion_rows),
            "owners": len(owner_rows),
        },
        "warnings": warnings,
        "legions": legion_rows,
        "owners": owner_rows,
        "summary": {
            "legions": len(legion_rows),
            "status_counts": status_counts,
            "tasks_total": sum(r["tasks_total"] for r in legion_rows),
            "tasks_aborted": sum(r["tasks_aborted"] for r in legion_rows),
            "owners": len(owner_rows),
        },
    }


def _filter_rows(rows: list, key: str, wanted: str) -> list:
    if not wanted:
        return rows
    wanted_set = {x.strip() for x in str(wanted).split(",") if x.strip()}
    return [r for r in rows if r.get("scope", {}).get(key) in wanted_set]


def _print_text(payload: dict, legions: list, owners: list) -> None:
    if not payload.get("ok"):
        print("[FAIL] %s | searched=%s" % (payload.get("error"),
                                           payload.get("searched")), file=sys.stderr)
        for w in payload.get("warnings") or []:
            print("  WARN", w, file=sys.stderr)
        return
    src = payload["sources"]
    print("== HQ LegionReport（只读，C3）==")
    print("  来源：%s（%d tasks）| directives=%s"
          % (src["tasks_file"], src["tasks"], src["directives_source"]))
    print("  汇总：legions=%d status=%s" % (
        payload["summary"]["legions"],
        json.dumps(payload["summary"]["status_counts"], ensure_ascii=False)))
    for r in legions:
        print("  [%s] legion=%s progress=%.1f%% eta=%s blockers=%d active=%d aborted=%d"
              % (r["status"], r["scope"]["legion"], r["progress"], r["eta"] or "-",
                 r["blockers_count"], r["tasks_active"], r["tasks_aborted"]))
    print("  owners：%d（--json 查看明细）" % len(owners))
    for w in (payload.get("warnings") or [])[:5]:
        print("  WARN", w)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="HQ LegionReport 只读模型（C3）")
    ap.add_argument("--json", action="store_true", help="stdout 输出纯 JSON")
    ap.add_argument("--tasks-file", default=str(TASKS))
    ap.add_argument("--meetings-dir", default="", help="Directive eta 来源（缺省用真源）")
    ap.add_argument("--council-dir", default="")
    ap.add_argument("--legion", default="", help="过滤 legion（逗号分隔）")
    ap.add_argument("--owner", default="", help="过滤 owner（逗号分隔）")
    ap.add_argument("--limit-blockers", type=int, default=10)
    args = ap.parse_args(argv)
    try:
        payload = build_reports(args.tasks_file, args.meetings_dir or None,
                                args.council_dir or None, args.limit_blockers)
    except Exception as exc:  # noqa: BLE001 —— fail-visible
        payload = _fail("internal_error:%s" % type(exc).__name__,
                        [args.tasks_file], [str(exc)[:200]])
    if not payload.get("ok"):
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=1))
        _print_text(payload, [], [])
        return 2
    legions = _filter_rows(payload["legions"], "legion", args.legion)
    owners = _filter_rows(payload["owners"], "owner", args.owner)
    payload["filtered"] = {"legion": args.legion, "owner": args.owner,
                           "legions": len(legions), "owners": len(owners)}
    payload["legions"] = legions
    payload["owners"] = owners
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        _print_text(payload, legions, owners)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
