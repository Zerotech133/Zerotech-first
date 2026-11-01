# -*- coding: utf-8 -*-
"""T1/T2 A/B 指标只读聚合（2026-11-01 WO-T1T2-METRICS-20261031）。

背景（K 复盘 / W1 Gate G1）：T1/T2 真实 prompt A/B 此前"只有文档结论、无指标
落盘"——`tools/w1_acceptance.py` 的 `t1_t2_metrics` 只能写死 unavailable。
本工具把真实运行 trace 聚合成机器可读指标 JSON，供 W1 验收直接消费。

数据源（只读，不造数）：
- `agent_platform/data/team_exec/<run_id>.jsonl`：run_invest 每节点一行
  （run_id/node_key/agent_id/status/execution_mode/duration_ms/output/structured）；
- `agent_platform/data/team_exec/<run_id>_summary.json`：run 级摘要
  （duration_ms_total/execution_modes/degraded/recommendation）。
- A/B run_id 清单 = **文档登记口径**（docs/audit/2026-10-30_W1-3*.md 三篇表格），
  本工具只把 run_id 当"标签"，全部指标仍从 trace 现算；trace 缺失即
  显式 unavailable，**不回填文档数字、不造数**。

指标口径（W1 Gate G1：JSON 完整率 ≥90%，延迟不升）：
- json_complete：T1/T2 节点 `structured` 为 dict、无 error 键、且 W1-3c 记录的
  7 字段齐全（overallTrend/selectedIndicators/keySignals/supportLevel/
  resistanceLevel/confidence/summaryTable）；
- json_complete_rate = 完整节点数 / T1+T2 节点数；
- latency_ms = 每个 T1/T2 节点 duration_ms 均值（延迟对照主口径）；
- 另记：node_normal_rate（status/execution_mode=normal 比例）、输出字符数、
  run 总耗时（summary.duration_ms_total，缺则节点和）、D1 节点状态（信息项）。

用法：
  python -X utf8 tools/t1t2_metrics.py --json
  python -X utf8 tools/t1t2_metrics.py --json --data-dir <team_exec 目录> --out <out.json>
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO / "agent_platform" / "data" / "team_exec"
SCHEMA = "t1t2_metrics/v1"
JSON_COMPLETE_MIN = 0.90
REQUIRED_JSON_FIELDS = (
    "overallTrend", "selectedIndicators", "keySignals", "supportLevel",
    "resistanceLevel", "confidence", "summaryTable",
)
T1T2_AGENTS = {"T1", "T2"}
T1T2_NODE_KEYS = {"t1_market", "t2_market"}

# A/B run_id 登记（**仅标签**，抄录自 docs/audit/2026-10-30_W1-3*.md 三篇的 run_id 列；
# 指标一律从 team_exec trace 现算，文档表格数字不入结果）。
AB_RUNS = {
    "baseline": {
        "run_ids": ["313b2b3ab7b3"],
        "label": "生产契约基线（W1-3 基线 313b）",
        "source": "docs/audit/2026-10-30_W1-3_真实T1T2_AB_v1.md",
    },
    "json_first": {
        "run_ids": ["19a37bffc93a"],
        "label": "JSON-first 变体（W1-3）",
        "source": "docs/audit/2026-10-30_W1-3_真实T1T2_AB_v1.md",
    },
    "short_contract": {
        "run_ids": ["e7cd83417dc5", "8f8ef1b070c6"],
        "label": "短契约变体（W1-3b）",
        "source": "docs/audit/2026-10-30_W1-3b_短契约与4b探索_v1.md",
    },
    "t1t2_4b": {
        "run_ids": ["3521a55c2670", "3a5781ce7e61"],
        "label": "仅 T1/T2 换 4b（W1-3c）",
        "source": "docs/audit/2026-10-30_W1-3c_T1T2_4b两轮_v1.md",
    },
    "all_4b_explore": {
        "run_ids": ["df0ec53e7495"],
        "label": "全节点 4b 探索（W1-3b，仅探索）",
        "source": "docs/audit/2026-10-30_W1-3b_短契约与4b探索_v1.md",
    },
}


def _load_trace(data_dir, run_id):
    """读单个 run 的节点 trace；缺失返回 (None, reason)。"""
    path = Path(data_dir) / ("%s.jsonl" % run_id)
    if not path.is_file():
        return None, "trace_missing: %s" % path
    rows = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    except OSError as exc:
        return None, "trace_unreadable: %s (%s)" % (path, exc)
    if not rows:
        return None, "trace_empty: %s" % path
    return rows, ""


def _json_complete(structured):
    if not isinstance(structured, dict):
        return False
    if "error" in structured:
        return False
    return all(key in structured for key in REQUIRED_JSON_FIELDS)


def _node_metrics(rows):
    out = []
    for row in rows:
        agent = str(row.get("agent_id") or row.get("node_key") or "")
        key = str(row.get("node_key") or "")
        if agent not in T1T2_AGENTS and key not in T1T2_NODE_KEYS:
            continue
        mode = str(row.get("execution_mode") or row.get("status") or "").lower()
        output = str(row.get("output") or "")
        try:
            duration = float(row.get("duration_ms") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        structured = row.get("structured")
        out.append({
            "node_key": key or agent.lower() + "_market",
            "agent_id": agent,
            "status": mode,
            "normal": mode == "normal",
            "json_complete": _json_complete(structured),
            "structured_error": str((structured or {}).get("error") or "")
            if isinstance(structured, dict) else "not_object",
            "duration_ms": round(duration, 1),
            "output_chars": len(output),
            "phase": str(row.get("phase") or ""),
        })
    return out


def _run_metrics(data_dir, run_id):
    rows, reason = _load_trace(data_dir, run_id)
    if rows is None:
        return None, reason
    nodes = _node_metrics(rows)
    d1_modes = [str(r.get("execution_mode") or r.get("status") or "").lower()
                for r in rows if str(r.get("agent_id") or "") == "D1"]
    summary = None
    sp = Path(data_dir) / ("%s_summary.json" % run_id)
    if sp.is_file():
        try:
            summary = json.loads(sp.read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            summary = None
    total_ms = None
    if isinstance(summary, dict) and summary.get("duration_ms_total") is not None:
        try:
            total_ms = float(summary["duration_ms_total"])
        except (TypeError, ValueError):
            total_ms = None
    if total_ms is None:
        all_ms = 0.0
        for row in rows:
            try:
                all_ms += float(row.get("duration_ms") or 0.0)
            except (TypeError, ValueError):
                pass
        total_ms = all_ms
    t1t2_count = len(nodes)
    complete = sum(1 for n in nodes if n["json_complete"])
    normal = sum(1 for n in nodes if n["normal"])
    latencies = [n["duration_ms"] for n in nodes]
    return {
        "run_id": run_id,
        "t1t2_node_count": t1t2_count,
        "json_complete_count": complete,
        "json_complete_rate": round(complete / t1t2_count, 4) if t1t2_count else None,
        "node_normal_count": normal,
        "node_normal_rate": round(normal / t1t2_count, 4) if t1t2_count else None,
        "mean_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "mean_output_chars": round(sum(n["output_chars"] for n in nodes) / t1t2_count, 1)
        if t1t2_count else None,
        "run_total_duration_ms": round(total_ms, 1),
        "d1_status": d1_modes[0] if d1_modes else "missing",
        "nodes": nodes,
    }, ""


def _arm_metrics(data_dir, arm_name, arm):
    runs, missing = [], []
    for run_id in arm.get("run_ids") or []:
        rep, reason = _run_metrics(data_dir, run_id)
        if rep is None:
            missing.append({"run_id": run_id, "reason": reason})
        else:
            runs.append(rep)
    node_total = sum(r["t1t2_node_count"] for r in runs)
    complete = sum(r["json_complete_count"] for r in runs)
    normal = sum(r["node_normal_count"] for r in runs)
    latencies = [r["mean_latency_ms"] for r in runs if r["mean_latency_ms"] is not None]
    arm_out = {
        "label": arm.get("label") or arm_name,
        "source": arm.get("source") or "",
        "run_ids": list(arm.get("run_ids") or []),
        "runs_found": [r["run_id"] for r in runs],
        "runs_missing": missing,
        "json_complete_rate": round(complete / node_total, 4) if node_total else None,
        "node_normal_rate": round(normal / node_total, 4) if node_total else None,
        "mean_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "runs": runs,
    }
    return arm_out


def _gate(arms):
    """W1 Gate G1：每个变体 JSON 完整率 ≥90% 且延迟不高于基线。"""
    baseline = arms.get("baseline") or {}
    base_lat = baseline.get("mean_latency_ms")
    base_json = baseline.get("json_complete_rate")
    checks = []
    for name, arm in arms.items():
        if name == "baseline":
            continue
        reasons = []
        if not arm.get("runs_found"):
            reasons.append("variant_trace_missing")
        jr = arm.get("json_complete_rate")
        lat = arm.get("mean_latency_ms")
        if jr is None:
            reasons.append("json_rate_unavailable")
        elif jr < JSON_COMPLETE_MIN:
            reasons.append("json_complete_rate %.4f < %.2f" % (jr, JSON_COMPLETE_MIN))
        latency_ok = None
        if lat is not None and base_lat:
            latency_ok = lat <= base_lat
            if not latency_ok:
                reasons.append("latency %.1fms > baseline %.1fms (+%.1f%%)"
                               % (lat, base_lat, (lat / base_lat - 1) * 100))
        elif lat is None or base_lat is None:
            reasons.append("latency_unavailable")
        checks.append({"arm": name, "passed": not reasons,
                       "latency_not_worse": latency_ok,
                       "json_complete_rate_ok": jr is not None and jr >= JSON_COMPLETE_MIN,
                       "reasons": reasons})
    return {
        "rule": "W1 Gate G1: JSON完整率>=%.0f%% 且 延迟不升" % (JSON_COMPLETE_MIN * 100),
        "baseline_json_complete_rate": base_json,
        "baseline_mean_latency_ms": base_lat,
        "checks": checks,
        "passed": bool(checks) and all(c["passed"] for c in checks),
    }


def aggregate(data_dir=None, runs=None):
    """只读聚合；返回机器可读 dict（绝不从文档表格回填数字）。"""
    data_dir = Path(data_dir or DEFAULT_DATA_DIR)
    runs = runs or AB_RUNS
    arms = {}
    for name, arm in runs.items():
        arms[name] = _arm_metrics(data_dir, name, arm)
    baseline = arms.get("baseline") or {}
    unavailable = []
    if not baseline.get("runs_found"):
        unavailable.append("baseline_trace_missing")
    for name, arm in arms.items():
        if name != "baseline" and not arm.get("runs_found"):
            unavailable.append("variant_trace_missing:%s" % name)
    available = not unavailable and bool(baseline.get("runs_found"))
    report = {
        "schema": SCHEMA,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "available": available,
        "unavailable_reasons": unavailable,
        "unavailable_fields": [
            {"field": "arms.variant", "reason": "；".join(unavailable)}
        ] if unavailable else [],
        "data_dir": str(data_dir),
        "gate": _gate(arms) if available else {
            "rule": "W1 Gate G1: JSON完整率>=%.0f%% 且 延迟不升"
                    % (JSON_COMPLETE_MIN * 100),
            "checks": [], "passed": None,
        },
        "arms": arms,
        "notes": [
            "A/B run_id 清单为文档登记标签（docs/audit/2026-10-30_W1-3*.md），"
            "指标全部从 team_exec trace 现算；trace 缺失即 unavailable，不回填文档数字。",
            "team_exec 数据在 agent_platform/.gitignore（data/*），worktree 常无该数据；"
            "生产主仓 / --data-dir 指向真实目录时才能产出指标。",
        ],
    }
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="T1/T2 A/B 指标只读聚合（不造数）")
    ap.add_argument("--json", action="store_true", help="输出 JSON（默认也输出 JSON）")
    ap.add_argument("--data-dir", default="", help="team_exec 目录（默认仓库内路径）")
    ap.add_argument("--out", default="", help="结果落文件（如 agent_platform/data/evals/...）")
    a = ap.parse_args(argv)
    rep = aggregate(a.data_dir or None)
    text = json.dumps(rep, ensure_ascii=False, indent=1)
    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if rep.get("available") else 2


if __name__ == "__main__":
    sys.exit(main())

