"""仅对 inspect 的内存观察作可比性判定；不授权生产切换。"""

import hashlib
import json
from collections.abc import Mapping
from datetime import date


_BASELINE_ISSUES = frozenset({"SIX_STRATEGY_CONTRACT_UNPROVEN", "PG_PERSISTENCE_UNPROVEN"})
_ARCHIVE_STATUSES = {
    "MATCHED": "MATCHED",
    "DATE_MISMATCH": "DATE_MISMATCH",
    "NOT_REQUESTED": "NOT_REQUESTED",
    "INVALID_ARCHIVE": "INVALID",
    "UNREADABLE": "INVALID",
}


def evaluate_comparison(observation):
    """消费 inspect 返回值，不读文件、不重算信号；所有未知证据均阻断可比性。"""
    data = observation if isinstance(observation, Mapping) else {}
    reasons = []

    def block(reason):
        if reason not in reasons:
            reasons.append(reason)

    history = data.get("history")
    raw_archive = history.get("status") if isinstance(history, Mapping) else None
    archive_status = _ARCHIVE_STATUSES.get(raw_archive, "INVALID")
    if archive_status != "MATCHED":
        block("ARCHIVE_" + archive_status)
    elif (not isinstance(data.get("trade_date"), str)
          or len(data["trade_date"]) != 8 or not data["trade_date"].isdigit()
          or history.get("scan_run_date") != (
              data["trade_date"][:4] + "-" + data["trade_date"][4:6]
              + "-" + data["trade_date"][6:])
          or not isinstance(history.get("trade_basis"), str)
          or not history["trade_basis"].startswith(history["scan_run_date"] + " 收盘")):
        block("ARCHIVE_DAY_EVIDENCE_INCONSISTENT")

    try:
        trade_day = date.fromisoformat(
            f"{data['trade_date'][:4]}-{data['trade_date'][4:6]}-{data['trade_date'][6:]}"
        )
    except (KeyError, TypeError, ValueError):
        trade_day = None
        block("TRADE_DATE_INVALID")
    health = data.get("market_health")
    cn = health.get("CN") if isinstance(health, Mapping) else None
    if isinstance(health, Mapping) and "CN" not in health:
        cn_batch = "ABSENT"
    elif (isinstance(cn, Mapping) and isinstance(cn.get("detect_date"), str)
          and isinstance(cn.get("rows"), int) and not isinstance(cn["rows"], bool)
          and cn["rows"] > 0 and cn.get("status") in ("FRESH", "STALE", "FUTURE")):
        cn_batch = "PRESENT"
    else:
        cn_batch = "INVALID"
    if cn_batch != "PRESENT":
        block("CN_SELECTED_BATCH_" + cn_batch)
    elif cn["status"] != "FRESH":
        block("CN_BATCH_NOT_FRESH")
    if cn_batch == "PRESENT" and cn["detect_date"] != data.get("cn_detect_date"):
        block("CN_SELECTED_BATCH_DATE_INCONSISTENT")
    if not isinstance(health, Mapping):
        block("MARKET_HEALTH_NOT_FRESH_OR_INVALID")
    else:
        for market, item in health.items():
            try:
                batch_day = date.fromisoformat(item["detect_date"])
                age = (trade_day - batch_day).days
                reported_age = item["age_calendar_days"]
                if (type(reported_age) is not int or reported_age != age
                        or item["status"] != (
                            "FUTURE" if age < 0 else "STALE" if age > 4 else "FRESH")):
                    raise ValueError("batch age/status mismatch")
            except (TypeError, KeyError, ValueError, AttributeError):
                block("MARKET_BATCH_DATE_AGE_INCONSISTENT")
                break
    if cn_batch == "PRESENT" and cn.get("age_calendar_days") != data.get("cn_age_calendar_days"):
        block("CN_BATCH_AGE_INCONSISTENT")

    reconcile = data.get("chanlun_reconciliation")
    source_events = "NOT_COMPARED"
    batch_alignment = "NOT_COMPARED"
    if archive_status == "MATCHED" and cn_batch == "PRESENT":
        if isinstance(reconcile, Mapping):
            batch_alignment = reconcile.get("batch_status")
            if batch_alignment not in ("MATCHED", "MISMATCH", "UNPROVEN"):
                batch_alignment = "INVALID_EVIDENCE"
            elif batch_alignment == "MATCHED" and history.get("chanlun_batch_date") != cn["detect_date"]:
                batch_alignment = "INVALID_EVIDENCE"
            count = reconcile.get("source_difference_count")
            diffs = reconcile.get("source_differences")
            if (isinstance(count, int) and not isinstance(count, bool) and count >= 0
                    and isinstance(diffs, Mapping) and count == len(diffs)):
                source_events = "MATCHED" if count == 0 else "MISMATCH"
            else:
                source_events = "INVALID_EVIDENCE"
        else:
            batch_alignment = source_events = "INVALID_EVIDENCE"
    if source_events != "MATCHED":
        block("CN_SOURCE_EVENTS_" + source_events)
    if batch_alignment != "MATCHED":
        block("CN_BATCH_ALIGNMENT_" + batch_alignment)

    paths = []
    files = data.get("source_files")
    if not isinstance(files, list) or not files:
        block("SOURCE_FILES_INVALID")
    else:
        for item in files:
            if not isinstance(item, Mapping) or not isinstance(item.get("path"), str):
                block("SOURCE_FILES_INVALID")
            elif item.get("status") == "MISSING":
                paths.append(item["path"])
                block("SOURCE_FILE_MISSING")
            elif item.get("status") != "OK":
                block("SOURCE_FILE_UNUSABLE")

    for field in ("source_anomalies", "selected_conversion_skips"):
        value = data.get(field)
        if not isinstance(value, list) or value:
            block(field.upper() + "_PRESENT_OR_INVALID")
    invalid_dates = data.get("invalid_source_dates")
    if not isinstance(invalid_dates, Mapping) or invalid_dates:
        block("INVALID_SOURCE_DATES_PRESENT_OR_INVALID")
    for field in ("duplicate_symbols", "contradictory_symbols"):
        value = data.get(field)
        if not isinstance(value, Mapping) or value:
            block(field.upper() + "_PRESENT_OR_INVALID")
    for field in ("picks_only", "adapter_only"):
        if not isinstance(data.get(field), list) or data[field]:
            block(field.upper() + "_PRESENT_OR_INVALID")
    if not isinstance(health, Mapping) or any(
        not isinstance(value, Mapping) or value.get("status") != "FRESH"
        for value in health.values()
    ):
        block("MARKET_HEALTH_NOT_FRESH_OR_INVALID")
    issues = data.get("issues")
    if (not isinstance(issues, list) or not all(isinstance(x, str) for x in issues)
            or not _BASELINE_ISSUES.issubset(issues)):
        block("PROBE_ISSUES_INVALID")
    elif set(issues) - _BASELINE_ISSUES:
        block("PROBE_ISSUES_PRESENT")
    manifest = data.get("input_manifest")
    if (data.get("input_snapshot_scope") != "PROBE_ONLY_NOT_PRODUCTION"
            or not isinstance(manifest, Mapping)
            or manifest.get("scope") != "PROBE_OBSERVATIONS_NOT_GLOBAL_ATOMIC_OR_PRODUCTION"):
        block("OBSERVATION_SCOPE_INVALID")
    try:
        pool_manifest = manifest["pool_files"]
        archive_manifest = manifest["archive"]
        if not isinstance(pool_manifest, list) or not isinstance(archive_manifest, Mapping):
            raise ValueError("invalid manifest")
        expected_pool = [{"path": item["path"], "status": item["status"],
                          "sha256": item["sha256"]} for item in files]
        expected_archive = {"path": history["path"], "status": history["status"],
                            "sha256": history.get("sha256")}
        if pool_manifest != expected_pool or archive_manifest != expected_archive:
            raise ValueError("manifest fields differ from observation")
        payload = {"pool_files": pool_manifest, "archive": dict(archive_manifest)}
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False,
                                           sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if digest != manifest["sha256"]:
            raise ValueError("manifest digest mismatch")
    except (TypeError, KeyError, ValueError):
        block("OBSERVATION_MANIFEST_INCONSISTENT")
    if data.get("verdict") != "BLOCK_CUTOVER":
        block("PROBE_VERDICT_INVALID")

    return {
        "archive_status": archive_status,
        "cn_selected_batch": cn_batch,
        "cn_source_events": source_events,
        "cn_batch_alignment": batch_alignment,
        "observation_comparability": (
            "COMPARABLE_WITHIN_PROBE" if not reasons else "NOT_COMPARABLE"),
        "missing_source_paths": sorted(set(paths)),
        "missing_market_inference": "NOT_INFERRED_FROM_MISSING_PATHS",
        "observation_scope": "PROBE_ONLY_NOT_PRODUCTION_ATOMIC_SNAPSHOT",
        "cutover_verdict": "BLOCK_CUTOVER",
        "reasons": reasons,
    }
