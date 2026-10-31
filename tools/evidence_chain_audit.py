# -*- coding: utf-8 -*-
"""P0 evidence 链式审计器（evidence_chain_audit，只读 / 默认不落盘）。

背景（审计补缺 P0 证据链）：
  ``_vault/legion_ide/evidence`` 下每个 ``*.json`` 是 LDE/A0/OH 的 run manifest
  （``legion-evidence/v1``），由 ``agent_platform/legion_command/evidence.py`` 在
  有 cap master key 时附 HMAC-SHA256。本工具做 **tamper-evident 快照/审计**：
  - 逐条计算 canonical SHA-256（稳定 JSON：``sort_keys=True``、``ensure_ascii=False``、
    排除 ``signature`` 自身，与 evidence.py 的 HMAC 口径一致）；
  - 按 created_at/finalized_at/mtime 排序生成链式 ledger（``prev_hash`` +
    ``record_hash``，创世哈希 = 64 个 0）；
  - 校验 signature 存在性 + HMAC（cap key 只读；不可读则 verified=unknown，不伪造）；
  - 校验 run_id 重复、文件 stem≠run_id、时间倒序/mtime 早于声明时间、时间缺口、
    无法解析/非对象/缺 run_id 的坏文件、记录内嵌 prev_hash/record_hash 与复算不符；
  - 可选 baseline（上一次 ``--out`` 生成的 ledger）对比：篡改/删行/中间插入/重排
    = 链断裂；新增行必须追加在链尾（head 连续）。

用法::

    python -X utf8 tools/evidence_chain_audit.py                     # Markdown，默认不写盘
    python -X utf8 tools/evidence_chain_audit.py --json
    python -X utf8 tools/evidence_chain_audit.py --json --baseline none   # 不做基线比对
    python -X utf8 tools/evidence_chain_audit.py --json \\
        --baseline deliverables/evidence_chain_20261031.json
    # 生成快照（默认不写；显式 --out 才写 <out>.json + <out>.md）：
    python -X utf8 tools/evidence_chain_audit.py --out deliverables/evidence_chain_20261031

退出码：0=pass；1=fail（坏文件/重复/signature 不符/时间异常/链断裂）；
        2=unknown（目录缺失/空/显式基线不可读等无法判定）。
只读边界：不写 evidence、不写 DB、不联网、不重启服务；仅 ``--out`` 显式指定时
写审计 ledger 文件（不改任何 evidence）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

SCHEMA = "evidence_chain_audit/v1"
REPO = Path(__file__).resolve().parents[1]
GENESIS_HASH = "0" * 64
DEFAULT_DIR = Path(r"C:\项目\_vault\legion_ide\evidence")
DIR_ENV = "LEGION_EVIDENCE_DIR"
DEFAULT_KEY = Path(r"C:\项目\_vault\legion_ide\cap_master.key")
KEY_ENV = "LEGION_CAP_KEY_FILE"
DEFAULT_BASELINE_GLOB = "deliverables/evidence_chain_*.json"
DEFAULT_GAP_HOURS = 72.0
DEFAULT_TIME_TOLERANCE = 300.0
MAX_ISSUE_SAMPLES = 20


# ---------------------------------------------------------------------------
# canonical 哈希 / 签名（与 evidence.py 同一口径）
# ---------------------------------------------------------------------------
def canonical_payload(record: dict) -> bytes:
    """稳定序列化：排除 signature 自身、sort_keys、ensure_ascii=False（与 HMAC 同口径）。"""
    body = {key: value for key, value in record.items() if key != "signature"}
    return json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")


def record_hash(record: dict) -> str:
    """记录内容的 canonical SHA-256（链上 record_hash）。"""
    return hashlib.sha256(canonical_payload(record)).hexdigest()


def verify_signature(record: dict, key: bytes) -> str:
    """返回 verified：true / false / absent / unknown（无 key 不伪造结论）。"""
    signature = str(record.get("signature") or "").strip()
    if not signature:
        return "absent"
    if not key:
        return "unknown"
    expected = hmac.new(key, canonical_payload(record), hashlib.sha256).hexdigest()
    return "true" if hmac.compare_digest(expected, signature) else "false"


def _read_key(path: Path) -> bytes:
    try:
        return Path(path).read_bytes().strip()
    except OSError:
        return b""


def _parse_ts(value) -> Optional[dt.datetime]:
    """解析 ISO 时间（容忍 Z / 无时区）；失败返回 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return None


def _iso(value: dt.datetime) -> str:
    return value.astimezone().isoformat(timespec="seconds")


def _mtime_iso(path: Path) -> str:
    try:
        return _iso(dt.datetime.fromtimestamp(path.stat().st_mtime))
    except OSError:
        return ""


def _sort_time(record: dict, path: Path) -> dt.datetime:
    """链排序键：created_at → finalized_at → 文件 mtime（均失败用 epoch）。"""
    for field in ("created_at", "finalized_at"):
        parsed = _parse_ts(record.get(field))
        if parsed is not None:
            return parsed
    try:
        return dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    except OSError:
        return dt.datetime.fromtimestamp(0).astimezone()


def _as_local(value: dt.datetime) -> dt.datetime:
    return value.astimezone() if value.tzinfo else value.replace(tzinfo=dt.timezone.utc).astimezone()


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------
def _scan(directory: Path) -> tuple:
    """返回 (rows, bad_files, files)。rows = [{path, record, ...}] 未排序。"""
    rows = []
    bad_files = []
    try:
        files = sorted(Path(p) for p in directory.glob("*.json"))
    except OSError as exc:
        return rows, [{"file": str(directory), "error": "%s: %s"
                       % (type(exc).__name__, str(exc)[:120])}], []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            bad_files.append({"file": path.name,
                              "error": "unreadable: %s" % type(exc).__name__})
            continue
        try:
            record = json.loads(text)
        except ValueError as exc:
            bad_files.append({"file": path.name, "error": "json: %s" % str(exc)[:100]})
            continue
        if not isinstance(record, dict):
            bad_files.append({"file": path.name, "error": "not_object"})
            continue
        if not str(record.get("run_id") or "").strip():
            bad_files.append({"file": path.name, "error": "missing_run_id"})
            continue
        rows.append({"path": path, "record": record})
    return rows, bad_files, files


def _resolve_baseline(baseline, repo: Path, directory: Path) -> tuple:
    """解析基线：返回 (baseline_data|None, source_desc, error)。"""
    if baseline in (None, "", False, "none", "off"):
        return None, "none", ""
    if baseline == "auto":
        candidates = sorted(repo.glob(DEFAULT_BASELINE_GLOB),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        for path in candidates:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
                continue
            recorded_dir = str(data.get("dir") or "")
            if recorded_dir and os.path.normcase(os.path.abspath(recorded_dir)) != os.path.normcase(
                    os.path.abspath(str(directory))):
                continue
            return data, "auto:%s" % path.name, ""
        return None, "auto:none", ""
    path = Path(baseline)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, "explicit:%s" % path, "基线不可读/非 JSON：%s: %s" % (
            type(exc).__name__, str(exc)[:120])
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return None, "explicit:%s" % path, "基线缺 entries 列表"
    return data, "explicit:%s" % path, ""


def _compare_baseline(baseline: dict, entries: list, max_samples: int) -> dict:
    """current entries vs baseline entries：篡改/删行/插入/重排 = 链断裂。"""
    base_entries = baseline.get("entries") or []
    base_files = [str(item.get("file") or "") for item in base_entries if isinstance(item, dict)]
    base_hashes = {str(item.get("file") or ""): str(item.get("record_hash") or "")
                   for item in base_entries if isinstance(item, dict)}
    current_files = [str(item.get("file") or "") for item in entries]
    current_hashes = {str(item.get("file") or ""): str(item.get("record_hash") or "")
                      for item in entries}
    matched_prefix = 0
    while (matched_prefix < len(base_files) and matched_prefix < len(current_files)
           and base_files[matched_prefix] == current_files[matched_prefix]):
        matched_prefix += 1
    modified = []
    missing = []
    for name in base_files:
        if name not in current_hashes:
            missing.append(name)
        elif base_hashes.get(name) != current_hashes.get(name):
            modified.append(name)
    appended = current_files[matched_prefix:]
    base_head = str(baseline.get("chain", {}).get("head_hash") or "")
    head_ok = True
    if matched_prefix == len(base_files) and base_files:
        prefix_head = entries[len(base_files) - 1].get("record_hash") if entries else ""
        head_ok = (not base_head) or prefix_head == base_head
    elif matched_prefix < len(base_files):
        head_ok = False
    chain_breaks = []
    if missing:
        chain_breaks.append({"kind": "deleted", "count": len(missing),
                             "files": missing[:max_samples]})
    if modified:
        chain_breaks.append({"kind": "modified", "count": len(modified),
                             "files": modified[:max_samples]})
    if not head_ok:
        chain_breaks.append({"kind": "head_discontinuity", "count": 1,
                             "detail": "基线链尾与当前前缀链尾不一致"})
    if matched_prefix < min(len(base_files), len(current_files)) and not missing:
        chain_breaks.append({"kind": "reordered_or_inserted",
                             "count": min(len(base_files), len(current_files)) - matched_prefix,
                             "base_file": base_files[matched_prefix],
                             "current_file": current_files[matched_prefix]})
    return {"entries": len(base_files), "matched_prefix": matched_prefix,
            "appended": len(appended), "missing": missing[:max_samples],
            "modified": modified[:max_samples], "head_ok": head_ok,
            "chain_ok": not chain_breaks, "chain_breaks": chain_breaks,
            "appended_sample": appended[:max_samples]}


# ---------------------------------------------------------------------------
# 采集
# ---------------------------------------------------------------------------
def collect(directory: Optional[Path] = None, *, key_path: Optional[Path] = None,
            baseline="auto", repo: Optional[Path] = None,
            gap_hours: float = DEFAULT_GAP_HOURS,
            time_tolerance: float = DEFAULT_TIME_TOLERANCE,
            max_samples: int = MAX_ISSUE_SAMPLES,
            now: Optional[dt.datetime] = None) -> dict:
    """只读审计 evidence 目录并返回报告（fail-soft：目录缺失 → unknown）。"""
    directory = Path(directory or os.environ.get(DIR_ENV) or DEFAULT_DIR)
    key_path = Path(key_path or os.environ.get(KEY_ENV) or DEFAULT_KEY)
    repo = Path(repo or REPO)
    now = now or dt.datetime.now().astimezone()
    max_samples = max(1, int(max_samples))
    key = _read_key(key_path)
    report = {
        "schema": SCHEMA,
        "generated_at": now.astimezone().isoformat(timespec="seconds"),
        "dir": str(directory),
        "key_path": str(key_path),
        "key_available": bool(key),
        "baseline": {"source": "none", "entries": 0, "matched_prefix": 0,
                     "appended": 0, "missing": [], "modified": [],
                     "head_ok": None, "chain_ok": None},
        "verdict": "unknown",
        "counts": {"files": 0, "records": 0, "bad_files": 0, "signed": 0,
                   "unsigned": 0, "signature_verified": 0, "signature_failed": 0,
                   "signature_unknown": 0, "duplicate_run_ids": 0,
                   "time_anomalies": 0, "chain_breaks": 0, "gaps": 0,
                   "stored_chain_records": 0},
        "chain": {"genesis_hash": GENESIS_HASH, "head_hash": "",
                  "length": 0, "anchored": False, "self_consistent": False},
        "issues": {"bad_files": [], "duplicates": [], "signature_failed": [],
                   "signature_missing": [], "time_anomalies": [], "chain_breaks": [],
                   "gaps": [], "file_stem_mismatch": []},
        "entries": [],
        "notes": ["只读审计：不改 evidence、不写 DB、不联网、不重启服务。"],
    }
    if not directory.is_dir():
        report["notes"].append("evidence 目录不存在：%s" % directory)
        return report
    rows, bad_files, files = _scan(directory)
    report["counts"]["files"] = len(files)
    report["counts"]["records"] = len(rows)
    report["counts"]["bad_files"] = len(bad_files)
    report["issues"]["bad_files"] = bad_files[:max_samples]
    if not files:
        report["notes"].append("evidence 目录为空（0 个 *.json），无可审计记录")
        return report
    if bad_files:
        report["notes"].append("存在无法解析/不合法的 evidence 文件：%d" % len(bad_files))

    # 链排序：created_at → finalized_at → mtime；同键按 run_id 稳定排序
    for row in rows:
        row["sort_time"] = _sort_time(row["record"], row["path"])
    rows.sort(key=lambda item: (item["sort_time"], str(item["record"].get("run_id") or ""),
                                item["path"].name))

    seen_run_ids = {}
    duplicate_samples = []
    time_anomalies = []
    gaps = []
    signature_failed = []
    signature_missing = []
    stem_mismatch = []
    stored_chain_records = 0
    prev_hash = GENESIS_HASH
    prev_time = None
    verified_counts = {"true": 0, "false": 0, "unknown": 0, "absent": 0}
    for index, row in enumerate(rows):
        path, record = row["path"], row["record"]
        run_id = str(record.get("run_id") or "").strip()
        content_hash = record_hash(record)
        signature_state = verify_signature(record, key)
        verified_counts[signature_state if signature_state in verified_counts else "unknown"] += 1
        if signature_state == "absent":
            signature_missing.append({"file": path.name, "run_id": run_id})
        elif signature_state == "false":
            signature_failed.append({"file": path.name, "run_id": run_id})
        if path.stem != run_id:
            stem_mismatch.append({"file": path.name, "run_id": run_id})
        # 时间异常：created_at > finalized_at；mtime 早于声明时间（超容忍）
        created = _parse_ts(record.get("created_at"))
        finalized = _parse_ts(record.get("finalized_at"))
        if created is not None and finalized is not None and created > finalized:
            time_anomalies.append({"file": path.name, "kind": "created_after_finalized",
                                   "detail": "%s > %s" % (_iso(created), _iso(finalized))})
        declared = finalized or created
        try:
            mtime = dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        except OSError:
            mtime = None
        if declared is not None and mtime is not None:
            skew = (declared - mtime).total_seconds()
            if skew > float(time_tolerance):
                time_anomalies.append({"file": path.name, "kind": "mtime_before_declared",
                                       "detail": "mtime 比声明时间早 %.0fs" % skew})
        # 记录内嵌链字段（未来 schema）：与复算不符 = 链断裂
        if "record_hash" in record or "prev_hash" in record:
            stored_chain_records += 1
            stored_hash = str(record.get("record_hash") or "")
            stored_prev = record.get("prev_hash")
            if stored_hash and stored_hash != content_hash:
                report["issues"]["chain_breaks"].append(
                    {"file": path.name, "kind": "record_hash_mismatch"})
            if stored_prev is not None and str(stored_prev) != prev_hash:
                report["issues"]["chain_breaks"].append(
                    {"file": path.name, "kind": "prev_hash_mismatch"})
        # run_id 重复
        if run_id in seen_run_ids:
            duplicate_samples.append({"run_id": run_id,
                                      "files": [seen_run_ids[run_id], path.name]})
        else:
            seen_run_ids[run_id] = path.name
        # 时间缺口（warning）
        if prev_time is not None:
            gap = (row["sort_time"] - prev_time).total_seconds() / 3600.0
            if gap > float(gap_hours):
                gaps.append({"after": rows[index - 1]["path"].name, "before": path.name,
                             "hours": round(gap, 1)})
        prev_time = row["sort_time"]
        report["entries"].append({
            "index": index,
            "file": path.name,
            "run_id": run_id,
            "kind": record.get("kind"),
            "task": record.get("task"),
            "agent": record.get("agent"),
            "created_at": record.get("created_at"),
            "finalized_at": record.get("finalized_at"),
            "mtime": _mtime_iso(path),
            "sort_time": _iso(row["sort_time"]),
            "size": path.stat().st_size if path.exists() else 0,
            "record_hash": content_hash,
            "prev_hash": prev_hash,
            "signature_present": bool(str(record.get("signature") or "").strip()),
            "signature_verified": signature_state,
            "stored_chain": "record_hash" in record or "prev_hash" in record,
        })
        prev_hash = content_hash

    report["counts"].update({
        "signed": verified_counts["true"] + verified_counts["false"] + verified_counts["unknown"],
        "unsigned": verified_counts["absent"],
        "signature_verified": verified_counts["true"],
        "signature_failed": verified_counts["false"],
        "signature_unknown": verified_counts["unknown"],
        "duplicate_run_ids": len(duplicate_samples),
        "time_anomalies": len(time_anomalies),
        "gaps": len(gaps),
        "file_stem_mismatch": len(stem_mismatch),
        "stored_chain_records": stored_chain_records,
    })
    report["issues"].update({
        "duplicates": duplicate_samples[:max_samples],
        "signature_failed": signature_failed[:max_samples],
        "signature_missing": signature_missing[:max_samples],
        "time_anomalies": time_anomalies[:max_samples],
        "gaps": gaps[:max_samples],
        "file_stem_mismatch": stem_mismatch[:max_samples],
    })
    report["chain"]["head_hash"] = prev_hash
    report["chain"]["length"] = len(report["entries"])
    report["chain"]["self_consistent"] = True

    # baseline 比对（篡改/删行/插入/重排）
    baseline_data, baseline_source, baseline_error = _resolve_baseline(baseline, repo, directory)
    baseline_breaks = []
    if baseline_error:
        report["notes"].append(baseline_error)
        report["verdict"] = "unknown"
        if baseline_data is not None or "explicit:" in baseline_source:
            report["baseline"]["source"] = baseline_source
            return report
    if baseline_data is not None:
        comparison = _compare_baseline(baseline_data, report["entries"], max_samples)
        report["baseline"].update(comparison)
        report["baseline"]["source"] = baseline_source
        report["chain"]["anchored"] = True
        baseline_breaks = comparison["chain_breaks"]
        if not comparison["chain_ok"]:
            report["issues"]["chain_breaks"].extend(baseline_breaks)
    else:
        report["baseline"]["source"] = baseline_source
        report["chain"]["anchored"] = False
        report["notes"].append("无基线（首次审计）：链自洽但无法证明未被整体重写；"
                               "用 --out 生成 ledger 供下次 --baseline 比对")
    report["counts"]["chain_breaks"] = len(report["issues"]["chain_breaks"])
    if stem_mismatch:
        report["notes"].append("文件 stem 与 run_id 不一致 %d 个（仅告警，不判负）"
                               % len(stem_mismatch))
    if gaps:
        report["notes"].append("时间缺口 > %.1fh 共 %d 处（仅告警，属空窗非篡改）"
                               % (float(gap_hours), len(gaps)))
    if not key:
        report["notes"].append("cap key 不可读：signature 只做存在性检查，HMAC verified=unknown"
                               "（需 %s）" % key_path)
    hard = (len(bad_files) > 0 or len(duplicate_samples) > 0
            or len(signature_failed) > 0 or len(signature_missing) > 0
            or len(time_anomalies) > 0 or len(report["issues"]["chain_breaks"]) > 0)
    report["verdict"] = "fail" if hard else "pass"
    return report


# ---------------------------------------------------------------------------
# 渲染 / 落盘
# ---------------------------------------------------------------------------
_STATUS_LABEL = {"pass": "PASS", "fail": "FAIL", "unknown": "UNKNOWN"}
_ISSUE_LABEL = {
    "bad_files": "无法解析/不合法 evidence 文件",
    "duplicates": "run_id 重复",
    "signature_missing": "signature 缺失",
    "signature_failed": "HMAC 校验失败（内容被改或 key 不符）",
    "time_anomalies": "时间异常（倒序/mtime 早于声明）",
    "chain_breaks": "链断裂（记录内嵌链不符/基线篡改/删行/插入/重排）",
}


def _md_cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def render_markdown(report: dict) -> str:
    counts = report.get("counts") or {}
    chain = report.get("chain") or {}
    baseline = report.get("baseline") or {}
    lines = [
        "# evidence 链式审计 · 只读",
        "",
        "> verdict: **%s** · dir `%s` · generated_at %s"
        % (_STATUS_LABEL.get(str(report.get("verdict")), "UNKNOWN"),
           report.get("dir") or "-", report.get("generated_at") or "-"),
        "> key_available=%s · baseline=%s · 记录 %s 条（文件 %s / 坏文件 %s）"
        % (report.get("key_available"), baseline.get("source") or "-",
           counts.get("records"), counts.get("files"), counts.get("bad_files")),
        "> 只读：不改 evidence、不写 DB、不联网、不重启服务；默认不落盘。",
        "",
        "## 汇总",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        "| 记录数 | %s |" % counts.get("records"),
        "| signature 存在 | %s（verified %s / failed %s / unknown %s / 缺失 %s）"
        % (counts.get("signed"), counts.get("signature_verified"),
           counts.get("signature_failed"), counts.get("signature_unknown"),
           counts.get("unsigned")),
        "| run_id 重复 | %s |" % counts.get("duplicate_run_ids"),
        "| 时间异常 | %s |" % counts.get("time_anomalies"),
        "| 时间缺口（>阈值，告警） | %s |" % counts.get("gaps"),
        "| 链断裂 | %s |" % counts.get("chain_breaks"),
        "| 链长 / 是否锚定基线 | %s / %s |"
        % (chain.get("length"), chain.get("anchored")),
        "",
        "## 链头",
        "",
        "- genesis：`%s`" % GENESIS_HASH,
        "- head：`%s`" % (chain.get("head_hash") or "-"),
        "- 自洽：%s；基线比对：%s"
        % (chain.get("self_consistent"), "有" if chain.get("anchored") else "无（首次/独立审计）"),
    ]
    if baseline.get("source") not in (None, "", "none", "auto:none"):
        lines.extend([
            "- 基线：%s（%s 条，匹配前缀 %s，新增 %s，删 %s / 改 %s，head_ok=%s）"
            % (baseline.get("source"), baseline.get("entries"),
               baseline.get("matched_prefix"), baseline.get("appended"),
               len(baseline.get("missing") or []), len(baseline.get("modified") or []),
               baseline.get("head_ok")),
        ])
    issues = report.get("issues") or {}
    lines.extend(["", "## 问题明细", ""])
    any_issue = False
    for key, label in _ISSUE_LABEL.items():
        items = issues.get(key) or []
        if not items:
            continue
        any_issue = True
        lines.append("- **%s**（%d）：%s" % (label, len(items),
                                            _md_cell(json.dumps(items[:5], ensure_ascii=False))))
    if not any_issue:
        lines.append("无（坏文件/重复/signature/时间/链断裂 全部通过）。")
    if issues.get("file_stem_mismatch"):
        lines.append("- 告警：文件 stem≠run_id %d 个：%s"
                     % (len(issues["file_stem_mismatch"]),
                        _md_cell(json.dumps(issues["file_stem_mismatch"][:5],
                                            ensure_ascii=False))))
    if issues.get("gaps"):
        lines.append("- 告警：时间缺口 %d 处：%s"
                     % (len(issues["gaps"]),
                        _md_cell(json.dumps(issues["gaps"][:5], ensure_ascii=False))))
    lines.extend([
        "",
        "## 未决口径",
        "",
        "- **HMAC key 可用性**：cap key 不可读时 signature 只报存在性、verified=unknown"
        "（不判负，但审计强度降级）；生产审计应保证 key 可读。",
        "- **链锚定**：首次运行无基线 → 链自洽但不能证明未被整体重写；"
        "建议每次审计 `--out deliverables/evidence_chain_<date>`，下次 `--baseline` 比对。",
        "- **排序口径**：created_at → finalized_at → mtime；同键按 run_id 稳定排序。",
        "- **时间缺口**：> gap_hours（默认 %.1f）只告警，不判负（属空窗非篡改）。" % DEFAULT_GAP_HOURS,
        "",
        "## 只读声明",
        "",
        "- 仅读取 evidence JSON 与 cap key；`--out` 显式指定时才写审计 ledger；"
        "不改任何 evidence / DB / 服务。",
    ])
    for note in report.get("notes") or []:
        lines.append("- %s" % note)
    return "\n".join(lines)


def _write_outputs(base: str, report: dict, markdown: str, raw_json: str) -> list:
    """按 --out 写审计 ledger：返回实际写出的文件列表。"""
    target = Path(base)
    written = []
    if target.suffix.lower() == ".json":
        targets = [(target, raw_json)]
    elif target.suffix.lower() == ".md":
        targets = [(target, markdown)]
    else:
        targets = [(target.with_name(target.name + ".json"), raw_json),
                   (target.with_name(target.name + ".md"), markdown)]
    for path, text in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8")
        written.append(str(path))
    return written


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="evidence 链式审计（只读；默认不落盘；不改 evidence）")
    fmt = parser.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
    fmt.add_argument("--markdown", action="store_true", help="输出 Markdown（默认）")
    parser.add_argument("--dir", default="", help="evidence 目录（默认环境变量/生产路径）")
    parser.add_argument("--key", default="", help="cap master key 路径（只读）")
    parser.add_argument("--baseline", default="auto",
                        help="基线 ledger：auto（默认，找 deliverables/evidence_chain_*.json）/"
                             "none/显式路径")
    parser.add_argument("--repo", default="", help="仓库根（auto 基线搜索用）")
    parser.add_argument("--gap-hours", type=float, default=DEFAULT_GAP_HOURS,
                        help="时间缺口告警阈值小时（默认 %s）" % DEFAULT_GAP_HOURS)
    parser.add_argument("--time-tolerance", type=float, default=DEFAULT_TIME_TOLERANCE,
                        help="mtime 早于声明时间的容忍秒（默认 %s）" % DEFAULT_TIME_TOLERANCE)
    parser.add_argument("--max-samples", type=int, default=MAX_ISSUE_SAMPLES,
                        help="问题样例上限（默认 %s）" % MAX_ISSUE_SAMPLES)
    parser.add_argument("--out", default="",
                        help="可选：写审计 ledger（.json/.md，或基名同时写两者）")
    args = parser.parse_args(argv)
    started = time.time()
    report = collect(
        directory=Path(args.dir) if args.dir else None,
        key_path=Path(args.key) if args.key else None,
        baseline=args.baseline,
        repo=Path(args.repo) if args.repo else None,
        gap_hours=args.gap_hours,
        time_tolerance=args.time_tolerance,
        max_samples=args.max_samples,
    )
    report["duration_ms"] = round((time.time() - started) * 1000, 1)
    raw_json = json.dumps(report, ensure_ascii=False, indent=1)
    markdown = render_markdown(report)
    print(raw_json if args.json else markdown)
    if args.out:
        try:
            written = _write_outputs(args.out, report, markdown, raw_json)
            print("[ok] 审计 ledger 已写出：%s" % ", ".join(written), file=sys.stderr)
        except OSError as exc:
            print("[warn] --out 写入失败（不影响判定）：%s" % exc, file=sys.stderr)
    verdict = str(report.get("verdict") or "unknown")
    return 0 if verdict == "pass" else (1 if verdict == "fail" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
