# -*- coding: utf-8 -*-
r"""T1/T2 A/B trace 采集与受控归档（2026-11-01 WO-T1T2-CAPTURE-20261031）。

背景（M 报告数据缺口）：W1-3/b/c 六个变体 run 的完整 trace 不在
`agent_platform/data/team_exec`（该目录被 `agent_platform/.gitignore` 忽略，
实验在已回收的实验 worktree 内跑 → trace 随 worktree 蒸发）；仅 console 日志
`C:\Temp\run_invest_*.log` 与 docs/audit 表格残留。本工具把**真实**数据收进
仓库内受控目录，供 metrics/审计复算，禁止编造数字。

两种采集保真度（如实标注）：
1. ``full_trace``：源 `team_exec/<run_id>.jsonl` + `_summary.json` 存在 → 原样
   复制 + sha256 登记（字段完整：structured/duration_ms 全在）；
2. ``log_salvage``：完整 trace 已丢失、但真实 console 日志在 → 解析日志得到
   run_id/stock/各节点 status/输出预览（**日志本身被截断**，故 structured /
   duration_ms 不可得，记 null 并标 ``degraded_fields``），不据此算 JSON 完整率
   与延迟。

目录约定（随单提交、可审计）：
    docs/audit/team_exec_archive/
      manifest.json                     # arm → run_id → 来源/sha256/保真度
      <arm>/<run_id>.jsonl|_summary.json  # full_trace 原件
      <arm>/<run_id>.salvage.json         # log_salvage 解析件（非完整 trace）

用法：
  python -X utf8 tools/t1t2_capture.py status --json
  python -X utf8 tools/t1t2_capture.py capture --run-id 313b2b3ab7b3 --arm baseline
  python -X utf8 tools/t1t2_capture.py capture --run-id 19a37bffc93a --arm json_first \
      --from-log C:\\Temp\\run_invest_jsonfirst.log
  python -X utf8 tools/t1t2_capture.py capture --all --from-log-dir C:\\Temp --dry-run
  python -X utf8 tools/t1t2_capture.py plan          # 未来 A/B 的受控采集命令
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO / "agent_platform" / "data" / "team_exec"
DEFAULT_ARCHIVE_DIR = REPO / "docs" / "audit" / "team_exec_archive"
SCHEMA = "t1t2_capture/v1"
MANIFEST = "manifest.json"
LOG_DEFAULT_DIR = Path(r"C:\Temp")
NODE_RE = re.compile(r"^\[(D1|F1|N1|T1|T2)\]\s+(\S+)")
RUN_RE = re.compile(r"run_id=([0-9a-f]{12})")
STOCK_RE = re.compile(r"stock=(\d{6})")

# W1-3/b/c 文档登记的 A/B run_id（与 tools/t1t2_metrics.py AB_RUNS 对齐；
# 仅标签与文件名映射，指标仍从真实文件现算）
AB_RUNS = {
    "baseline": {"313b2b3ab7b3": "run_invest_002712.log"},
    "json_first": {"19a37bffc93a": "run_invest_jsonfirst.log"},
    "short_contract": {"e7cd83417dc5": "run_invest_short_1.log",
                       "8f8ef1b070c6": "run_invest_short_2.log"},
    "t1t2_4b": {"3521a55c2670": "run_invest_t1t2_4b_1.log",
                "3a5781ce7e61": "run_invest_t1t2_4b_2.log"},
    "all_4b_explore": {"df0ec53e7495": "run_invest_4b.log"},
}


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _load_manifest(archive_dir):
    path = Path(archive_dir) / MANIFEST
    if not path.is_file():
        return {"schema": SCHEMA, "updated_at": "", "arms": {}}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {"schema": SCHEMA, "updated_at": "", "arms": {}}
    doc.setdefault("arms", {})
    return doc


def _save_manifest(archive_dir, manifest):
    path = Path(archive_dir) / MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest["schema"] = SCHEMA
    manifest["updated_at"] = _now()
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")


def find_full_trace(run_id, data_dir):
    """在 team_exec 找完整 trace；返回 (jsonl_path, summary_path|None, reason)。"""
    run_id = str(run_id)
    trace = Path(data_dir) / ("%s.jsonl" % run_id)
    if not trace.is_file():
        return None, None, "trace_missing: %s" % trace
    summary = Path(data_dir) / ("%s_summary.json" % run_id)
    return trace, (summary if summary.is_file() else None), ""


def _read_log_text(log_path):
    """console 日志实际编码不定（PowerShell 重定向常为 UTF-16LE）→ 逐编码探测。"""
    raw = Path(log_path).read_bytes()
    for enc in ("utf-8-sig", "utf-16", "utf-16-le", "gbk"):
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
        if "run_id=" in text:
            return text
    return raw.decode("utf-8", "replace")


def parse_log(log_path):
    """解析 run_invest console 日志（真实日志，字段缺失记 null，不猜）。"""
    log_path = Path(log_path)
    if not log_path.is_file():
        return None, "log_missing: %s" % log_path
    text = _read_log_text(log_path)
    run_match = RUN_RE.search(text)
    run_id = run_match.group(1) if run_match else ""
    stock_match = STOCK_RE.search(text)
    nodes, current = [], None
    for line in text.splitlines():
        m = NODE_RE.match(line.strip())
        if m:
            current = {"agent_id": m.group(1), "status": m.group(2).lower(),
                       "output_preview": "", "structured": None,
                       "duration_ms": None}
            nodes.append(current)
            continue
        if current is not None and line.startswith("   ") and not current["output_preview"]:
            current["output_preview"] = line.strip()[:200]
    if not run_id:
        return None, "run_id_not_found_in_log: %s" % log_path
    return {
        "run_id": run_id,
        "stock": stock_match.group(1) if stock_match else "",
        "source_log": str(log_path),
        "source_log_sha256": _sha256(log_path),
        "fidelity": "log_salvage",
        "captured_at": _now(),
        "nodes": nodes,
        "degraded_fields": [
            "structured", "duration_ms",
        ],
        "degraded_reason": "console 日志输出被截断，完整 structured/duration 不可得；"
                           "本件仅用于状态/样本级审计，不用于 JSON 完整率/延迟计算。",
    }, ""


def capture(run_id, arm, data_dir=None, archive_dir=None, from_log=None,
            dry_run=False, manifest=None):
    """采集单 run：优先 full_trace，其次 log_salvage；无真实源 → missing（零写入）。"""
    data_dir = Path(data_dir or DEFAULT_DATA_DIR)
    archive_dir = Path(archive_dir or DEFAULT_ARCHIVE_DIR)
    arm = str(arm or "unassigned")
    result = {"run_id": str(run_id), "arm": arm, "status": "missing",
              "mode": "", "files": [], "reason": "", "dry_run": bool(dry_run)}
    trace, summary, reason = find_full_trace(run_id, data_dir)
    files = {}
    if trace is not None:
        result.update(status="captured", mode="full_trace")
        files[str(trace)] = archive_dir / arm / ("%s.jsonl" % run_id)
        if summary is not None:
            files[str(summary)] = archive_dir / arm / ("%s_summary.json" % run_id)
    elif from_log:
        salvage, reason2 = parse_log(from_log)
        if salvage is None:
            result["reason"] = reason2
            return result, manifest
        if salvage.get("run_id") != str(run_id):
            result["reason"] = ("run_id_mismatch: log=%s expect=%s"
                                % (salvage.get("run_id"), run_id))
            return result, manifest
        result.update(status="captured", mode="log_salvage")
        files[str(Path(from_log))] = archive_dir / arm / ("%s.salvage.json" % run_id)
    else:
        result["reason"] = reason or "trace_missing"
        return result, manifest
    manifest = manifest if manifest is not None else _load_manifest(archive_dir)
    arm_entry = manifest["arms"].setdefault(arm, {})
    digest = {}
    for src, dst in files.items():
        digest[src] = _sha256(src)
        result["files"].append({"source": src, "target": str(dst),
                                "sha256": digest[src],
                                "size": Path(src).stat().st_size})
    if dry_run:
        result["note"] = "dry-run：只登记计划，不写文件"
        return result, manifest
    for src, dst in files.items():
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.suffix == ".jsonl" or dst.suffix == ".json":
            if dst.name.endswith(".salvage.json"):
                salvage, _ = parse_log(src)
                dst.write_text(json.dumps(salvage, ensure_ascii=False, indent=1) + "\n",
                               encoding="utf-8")
            elif str(src).endswith(".jsonl"):
                shutil.copyfile(src, dst)
            else:
                shutil.copyfile(src, dst)
    arm_entry[str(run_id)] = {
        "mode": result["mode"],
        "fidelity": result["mode"],
        "captured_at": _now(),
        "files": [{"target": str(f["target"]), "sha256": f["sha256"],
                   "size": f["size"]} for f in result["files"]],
        "provenance": ("team_exec full trace" if result["mode"] == "full_trace"
                       else "C:\\Temp run_invest console log（截断，salvage）"),
    }
    _save_manifest(archive_dir, manifest)
    return result, manifest


def _log_for_arm(run_id, from_log_dir):
    """按 AB_RUNS 映射找该 run 的日志文件名。"""
    for arm, runs in AB_RUNS.items():
        if run_id in runs:
            path = Path(from_log_dir) / runs[run_id]
            if path.is_file():
                return path
    return None


def cmd_capture(a):
    data_dir, archive_dir = Path(a.data_dir or DEFAULT_DATA_DIR), \
        Path(a.archive_dir or DEFAULT_ARCHIVE_DIR)
    manifest = _load_manifest(archive_dir)
    if a.all:
        plans = []
        for arm, runs in AB_RUNS.items():
            for run_id in runs:
                plans.append((run_id, arm))
        results = []
        for run_id, arm in plans:
            log = _log_for_arm(run_id, a.from_log_dir or LOG_DEFAULT_DIR) \
                if not find_full_trace(run_id, data_dir)[0] else None
            rep, _ = capture(run_id, arm, data_dir, archive_dir,
                             from_log=log, dry_run=a.dry_run, manifest=manifest)
            results.append(rep)
        out = {"schema": SCHEMA, "dry_run": bool(a.dry_run), "results": results,
               "summary": {"captured": sum(1 for r in results if r["status"] == "captured"),
                           "missing": sum(1 for r in results if r["status"] == "missing")}}
    else:
        if not a.run_id:
            raise SystemExit("capture 需要 --run-id 或 --all")
        log = a.from_log or (_log_for_arm(a.run_id, a.from_log_dir or LOG_DEFAULT_DIR)
                             if not find_full_trace(a.run_id, data_dir)[0] else None)
        rep, _ = capture(a.run_id, a.arm, data_dir, archive_dir,
                         from_log=log, dry_run=a.dry_run, manifest=manifest)
        out = {"schema": SCHEMA, "dry_run": bool(a.dry_run), "results": [rep],
               "summary": {"captured": 1 if rep["status"] == "captured" else 0,
                           "missing": 1 if rep["status"] == "missing" else 0}}
    text = json.dumps(out, ensure_ascii=False, indent=1)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if out["summary"]["missing"] == 0 else 2


def inventory(data_dir=None, archive_dir=None, from_log_dir=None):
    """A/B 六 run 的可得性盘点（missing 原因 + 建议命令）。"""
    data_dir = Path(data_dir or DEFAULT_DATA_DIR)
    archive_dir = Path(archive_dir or DEFAULT_ARCHIVE_DIR)
    manifest = _load_manifest(archive_dir)
    log_dir = Path(from_log_dir or LOG_DEFAULT_DIR)
    arms = {}
    for arm, runs in AB_RUNS.items():
        arm_out = {"run_ids": list(runs), "runs": {}}
        for run_id, log_name in runs.items():
            trace = (data_dir / ("%s.jsonl" % run_id)).is_file()
            entry = (manifest.get("arms", {}).get(arm) or {}).get(run_id)
            log = (log_dir / log_name).is_file()
            state = "missing"
            reason = ""
            if trace:
                state = "team_exec_full"
            elif entry and entry.get("mode") == "full_trace":
                state = "archived_full"
            elif entry and entry.get("mode") == "log_salvage":
                state = "archived_salvage"
            elif log:
                state = "log_only"  # 可 salvage
                reason = "完整 trace 缺失，仅 console 日志（%s）" % (log_dir / log_name)
            else:
                reason = "trace 缺失且日志缺失（未跑/被清理）"
            arm_out["runs"][run_id] = {"state": state, "reason": reason,
                                       "log": str(log_dir / log_name) if log else ""}
        arms[arm] = arm_out
    return {"schema": SCHEMA + "/inventory", "generated_at": _now(),
            "data_dir": str(data_dir), "archive_dir": str(archive_dir),
            "log_dir": str(log_dir), "arms": arms}


def plan():
    """未来 A/B 的受控采集命令（把 TEAM_EXEC_DIR 指到仓库受控目录再跑）。"""
    staging = DEFAULT_ARCHIVE_DIR / "staging"
    return {
        "schema": SCHEMA + "/plan",
        "commands": [
            "# 1) 实验跑之前把 team_exec 输出指到仓库受控目录（随单可提交）",
            '$env:TEAM_EXEC_DIR="%s"' % staging,
            "# 2) 跑 A/B 变体（示例；参数以实际实验单为准）",
            "python -X utf8 tools/run_invest.py --live --stock 002712",
            "# 3) 采集归档 + 校验（full_trace 优先，缺 trace 时用 --from-log 兜底）",
            "python -X utf8 tools/t1t2_capture.py capture --all --json",
            "python -X utf8 tools/t1t2_capture.py status --json",
            "# 4) 指标复算（metrics 同时读 team_exec 与 docs/audit/team_exec_archive）",
            "python -X utf8 tools/t1t2_metrics.py --json",
        ],
        "staging_dir": str(staging),
        "note": "staging 内的完整 trace 采集后随单提交到 docs/audit/team_exec_archive/；"
                "commit 前确认无敏感字段（本工具只复制 run trace，不改写内容）。",
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="T1/T2 A/B trace 采集与受控归档")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture", help="采集单 run 或全部 A/B run")
    c.add_argument("--run-id", default="")
    c.add_argument("--arm", default="unassigned")
    c.add_argument("--all", action="store_true")
    c.add_argument("--data-dir", default="")
    c.add_argument("--archive-dir", default="")
    c.add_argument("--from-log", default="")
    c.add_argument("--from-log-dir", default="")
    c.add_argument("--dry-run", action="store_true")
    c.add_argument("--json", action="store_true")
    c.add_argument("--out", default="")
    s = sub.add_parser("status", help="盘点 A/B run 可得性/缺失原因")
    s.add_argument("--data-dir", default="")
    s.add_argument("--archive-dir", default="")
    s.add_argument("--from-log-dir", default="")
    s.add_argument("--json", action="store_true")
    s.add_argument("--out", default="")
    p = sub.add_parser("plan", help="输出未来 A/B 受控采集命令")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "capture":
        return cmd_capture(a)
    if a.cmd == "status":
        rep = inventory(a.data_dir or None, a.archive_dir or None,
                        a.from_log_dir or None)
        text = json.dumps(rep, ensure_ascii=False, indent=1)
        if a.out:
            Path(a.out).write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0
    rep = plan()
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
