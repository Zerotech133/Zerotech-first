# -*- coding: utf-8 -*-
"""SHADOW 长周期验证与证据聚合（只读入口）。

本模块只读取显式 root 下已有的 SHADOW、strategy、promotion、perf JSONL
账本，生成一份可回放的聚合快照。默认只返回内存结果，不创建目录、不写文件、
不写 evidence.db，也不导入交易执行模块。

只有调用方显式提供 ``output_path`` 时，才会向 root 内的 append-only JSONL
追加一条聚合事件。run_id 与 event_id 均参与幂等校验：同标识同内容返回
replayed，不覆盖历史；同标识异内容返回 failed 并保留原账本。
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping, Optional, Tuple


SCHEMA_VERSION = "shadow-long-cycle.v1"
DEFAULT_OUTPUT_RELATIVE = Path("data") / "shadow" / "shadow_long_cycle_evidence.jsonl"
SOURCE_RELATIVE = {
    "shadow": Path("data") / "shadow" / "shadow_ledger.jsonl",
    "strategy": Path("data") / "strategy" / "strategy_runs.jsonl",
    "promotion": Path("strategy") / "strategy_promotions.jsonl",
    "perf": Path("data") / "perf_ledger.jsonl",
    "trace": Path("data") / "trace" / "trace_links.jsonl",
}


class ShadowLongCycleError(ValueError):
    """长周期入口的边界、路径或幂等冲突错误。"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _explicit_root(root) -> Path:
    if root is None or (isinstance(root, str) and not root.strip()):
        raise ShadowLongCycleError("必须显式提供 root")
    try:
        path = Path(root).expanduser().resolve()
    except (TypeError, ValueError) as exc:
        raise ShadowLongCycleError("root 必须是有效目录路径") from exc
    if path.exists() and not path.is_dir():
        raise ShadowLongCycleError("root 必须是目录：%s" % path)
    return path


def _inside(root: Path, value, field: str) -> Path:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ShadowLongCycleError("必须显式提供 %s" % field)
    try:
        path = Path(value).expanduser().resolve()
        path.relative_to(root)
    except (TypeError, ValueError) as exc:
        raise ShadowLongCycleError("%s 必须位于 root 内" % field) from exc
    return path


def _require_id(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ShadowLongCycleError("%s 必须是非空字符串" % field)
    return value.strip()


def _read_jsonl(path: Path, label: str) -> Tuple[list, dict, list]:
    """读取单个 JSONL，坏行和 I/O 错误均转成可见 finding。"""
    findings = []
    report = {
        "path": str(path),
        "exists": path.exists(),
        "records": 0,
        "valid_records": 0,
        "invalid_records": 0,
        "sha256": None,
    }
    if not path.exists():
        findings.append("%s: 文件缺失，未伪造空证据" % label)
        return [], report, findings
    try:
        raw = path.read_bytes()
        report["sha256"] = _sha256(raw)
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        report["read_error"] = str(exc)
        findings.append("%s: 读取失败：%s" % (label, exc))
        return [], report, findings

    rows = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        report["records"] += 1
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("JSON 顶层不是对象")
        except (json.JSONDecodeError, ValueError) as exc:
            report["invalid_records"] += 1
            findings.append("%s: 第 %d 行非法 JSON：%s" % (label, lineno, exc))
            continue
        rows.append(row)
    report["valid_records"] = len(rows)
    return rows, report, findings


def _summarise_source(label: str, rows: Iterable[Mapping]) -> dict:
    rows = list(rows)
    statuses = {}
    run_ids = []
    for row in rows:
        status = str(row.get("status", "")).lower() or "unspecified"
        statuses[status] = statuses.get(status, 0) + 1
        run_id = row.get("run_id")
        if run_id and run_id not in run_ids:
            run_ids.append(run_id)
    failed = sum(
        statuses.get(value, 0)
        for value in ("failed", "failure", "error", "timeout", "rejected")
    )
    result = {
        "valid_records": len(rows),
        "failed_records": failed,
        "statuses": dict(sorted(statuses.items())),
    }
    if label in ("shadow", "strategy"):
        result["run_ids"] = run_ids
    if label == "shadow":
        result["done_records"] = statuses.get("done", 0)
    if label == "promotion":
        result["accepted_records"] = sum(
            statuses.get(value, 0) for value in ("accepted", "replayed")
        )
        result["rejected_records"] = statuses.get("rejected", 0)
    return result


def _load_output_events(path: Path) -> Tuple[list, list]:
    # 首次显式输出时目标不存在是正常状态，不把它误报为源账本缺失。
    if not path.exists():
        return [], []
    rows, report, findings = _read_jsonl(path, "output")
    if report.get("read_error"):
        return [], findings
    return rows, findings


def _event_identity(event: Mapping) -> dict:
    return {
        "schema_version": event.get("schema_version"),
        "mode": event.get("mode"),
        "run_id": event.get("run_id"),
        "event_id": event.get("event_id"),
        "sources": event.get("sources"),
        "summary": event.get("summary"),
        "findings": event.get("findings"),
    }


def event_id_for(run_id: str, sources: Mapping) -> str:
    """根据 run_id 与源文件指纹生成稳定 event_id。"""
    payload = _canonical({"run_id": run_id, "sources": sources})
    return "shlc-" + _sha256(payload.encode("utf-8"))[:24]


def _status(findings: list) -> str:
    return "WARN" if findings else "PASS"


def aggregate_shadow_evidence(
    root,
    run_id: str,
    *,
    event_id: Optional[str] = None,
    output_path=None,
) -> dict:
    """读取并聚合一次 SHADOW 长周期证据。

    ``root`` 与 ``run_id`` 必须显式提供。没有 ``output_path`` 时绝不写入
    文件系统；提供 output_path 后才会追加 JSONL，并执行 run/event 幂等检查。
    """
    root_path = _explicit_root(root)
    run_id = _require_id(run_id, "run_id")
    if event_id is not None:
        event_id = _require_id(event_id, "event_id")

    source_rows = {}
    source_reports = {}
    findings = []
    source_fingerprints = {}
    source_summaries = {}
    source_paths = {}
    for label, relative in SOURCE_RELATIVE.items():
        path = (root_path / relative).resolve()
        source_paths[label] = path
        rows, report, source_findings = _read_jsonl(path, label)
        source_rows[label] = rows
        source_reports[label] = report
        findings.extend(source_findings)
        source_fingerprints[label] = {
            "path": str(path),
            "exists": report["exists"],
            "sha256": report["sha256"],
        }
        source_summaries[label] = _summarise_source(label, rows)

    event_id = event_id or event_id_for(run_id, source_fingerprints)
    summary = {
        "source_files": len(SOURCE_RELATIVE),
        "source_files_present": sum(
            1 for report in source_reports.values() if report["exists"]
        ),
        "shadow_done": source_summaries["shadow"].get("done_records", 0),
        "shadow_failed": source_summaries["shadow"]["failed_records"],
        "strategy_records": source_summaries["strategy"]["valid_records"],
        "promotion_records": source_summaries["promotion"]["valid_records"],
        "perf_records": source_summaries["perf"]["valid_records"],
    }
    event = {
        "schema_version": SCHEMA_VERSION,
        "mode": "SHADOW",
        "run_id": run_id,
        "event_id": event_id,
        "recorded_at": _utc_now(),
        "root": str(root_path),
        "read_only": True,
        "no_execution": True,
        "execution_allowed": False,
        "sources": source_fingerprints,
        "source_reports": source_reports,
        "source_summaries": source_summaries,
        "summary": summary,
        "findings": findings,
        "status": _status(findings),
    }

    # 失败记录本身也是证据：已有源账本中的失败/拒绝/超时不能被静默吞掉。
    if any(source_summaries[label]["failed_records"] for label in source_summaries):
        findings.append("源账本存在失败、拒绝或超时记录")
        event["findings"] = findings
        event["status"] = _status(findings)

    result = dict(event)
    result["persisted"] = False
    result["replayed"] = False
    if output_path is None:
        return result

    destination = _inside(root_path, output_path, "output_path")
    if destination in source_paths.values():
        raise ShadowLongCycleError("output_path 不得覆盖输入账本")
    existing, output_findings = _load_output_events(destination)
    if output_findings:
        # 坏的历史输出仍可追加，但必须在本次结果中显式可见。
        result["findings"] = list(result["findings"]) + output_findings
        result["status"] = _status(result["findings"])
        event["findings"] = result["findings"]
        event["status"] = result["status"]

    candidate_identity = _event_identity(event)
    for previous in existing:
        same_event = previous.get("event_id") == event_id
        same_run = previous.get("run_id") == run_id
        if not (same_event or same_run):
            continue
        if _event_identity(previous) == candidate_identity:
            result["persisted"] = True
            result["replayed"] = True
            result["status"] = "REPLAYED"
            result["event"] = previous
            return result
        reason = (
            "event_id 已存在但事件内容不同，拒绝覆盖"
            if same_event
            else "run_id 已存在但事件内容不同，拒绝追加"
        )
        result["status"] = "FAILED"
        result["findings"] = list(result["findings"]) + [reason]
        result["error"] = reason
        return result

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8") as stream:
        stream.write(_canonical(event) + "\n")
    result["persisted"] = True
    result["output_path"] = str(destination)
    result["event"] = event
    return result


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="只读聚合 SHADOW 长周期账本，可选追加幂等 JSONL 证据事件。"
    )
    parser.add_argument("--root", required=True, help="显式 agent_platform 根目录")
    parser.add_argument("--run-id", required=True, help="本次长周期运行标识")
    parser.add_argument("--event-id", help="可选的稳定证据事件标识")
    parser.add_argument("--output", help="可选 root 内 append-only JSONL 输出路径")
    args = parser.parse_args(argv)
    try:
        result = aggregate_shadow_evidence(
            args.root,
            args.run_id,
            event_id=args.event_id,
            output_path=args.output,
        )
    except Exception as exc:  # CLI 边界：失败必须可见
        print("ERROR: %s" % exc)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 2 if result.get("status") == "FAILED" else 0


__all__ = [
    "DEFAULT_OUTPUT_RELATIVE",
    "SCHEMA_VERSION",
    "SOURCE_RELATIVE",
    "ShadowLongCycleError",
    "aggregate_shadow_evidence",
    "event_id_for",
]


if __name__ == "__main__":
    raise SystemExit(main())
