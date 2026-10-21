"""研究用影子比较预检：仅检查调用方提供的内存声明，不读取或认证外部证据。"""

from datetime import date, datetime
import re


def _text(value):
    return type(value) is str and bool(value.strip())


def _day(value):
    if type(value) is not str or len(value) != 8 or not value.isascii() or not value.isdigit():
        return None
    try:
        return date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:]}")
    except ValueError:
        return None


def _instant(value):
    if not _text(value):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def preflight_shadow_comparison(old_chain, new_chain, stage_mapping, isolation):
    """仅按调用方声明判定字段自洽候选；不独立核验证据、批准或切流。"""
    reasons = []

    for name, chain in (("OLD", old_chain), ("NEW", new_chain)):
        if type(chain) is not dict:
            reasons.append(f"{name}_EVIDENCE_INVALID")
            continue
        if _day(chain.get("trade_date")) is None:
            reasons.append(f"{name}_TRADE_DATE_INVALID")
        for field in ("market", "stage", "snapshot_id", "pit_evidence_version"):
            if not _text(chain.get(field)):
                reasons.append(f"{name}_{field.upper()}_MISSING")
        digest = chain.get("sha256")
        if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            reasons.append(f"{name}_HASH_INVALID")
        for field in ("snapshot_verified", "hash_verified", "pit_verified"):
            if chain.get(field) is not True:
                reasons.append(f"{name}_{field.upper()}_UNPROVEN")
        available = _instant(chain.get("pit_available_at"))
        cutoff = _instant(chain.get("pit_cutoff_at"))
        if (available is None or cutoff is None or available > cutoff
                or (_day(chain.get("trade_date")) is not None
                    and cutoff.date() != _day(chain["trade_date"]))):
            reasons.append(f"{name}_PIT_CUTOFF_UNPROVEN")

    if type(old_chain) is dict and type(new_chain) is dict:
        if (_text(old_chain.get("pit_evidence_version"))
                and _text(new_chain.get("pit_evidence_version"))
                and old_chain["pit_evidence_version"] != new_chain["pit_evidence_version"]):
            reasons.append("PIT_EVIDENCE_VERSION_MISMATCH")
        if (_day(old_chain.get("trade_date")) is not None
                and _day(new_chain.get("trade_date")) is not None
                and old_chain["trade_date"] != new_chain["trade_date"]):
            reasons.append("TRADE_DATE_MISMATCH")
        if (_text(old_chain.get("market")) and _text(new_chain.get("market"))
                and old_chain["market"] != new_chain["market"]):
            reasons.append("MARKET_MISMATCH")
        if (_text(old_chain.get("stage")) and _text(new_chain.get("stage"))
                and old_chain["stage"] != new_chain["stage"]):
            reasons.append("STAGE_MISMATCH")
        old_cutoff = _instant(old_chain.get("pit_cutoff_at"))
        new_cutoff = _instant(new_chain.get("pit_cutoff_at"))
        if old_cutoff is not None and new_cutoff is not None and old_cutoff != new_cutoff:
            reasons.append("PIT_CUTOFF_MISMATCH")

    if (type(stage_mapping) is not dict
            or stage_mapping.get("approved") is not True
            or not _text(stage_mapping.get("old_stage"))
            or not _text(stage_mapping.get("new_stage"))
            or type(old_chain) is not dict or type(new_chain) is not dict
            or stage_mapping["old_stage"] != old_chain.get("stage")
            or stage_mapping["new_stage"] != new_chain.get("stage")):
        reasons.append("STAGE_MAPPING_UNAPPROVED_OR_MISMATCHED")

    if type(old_chain) is dict:
        for field in ("consumer_version", "consumer_receipt"):
            if not _text(old_chain.get(field)):
                reasons.append(f"OLD_{field.upper()}_MISSING")
        consumed = _instant(old_chain.get("consumer_read_at"))
        cutoff = _instant(old_chain.get("pit_cutoff_at"))
        if (consumed is None or (cutoff is not None and consumed < cutoff)
                or (_day(old_chain.get("trade_date")) is not None
                    and consumed is not None and cutoff is not None
                    and consumed.astimezone(cutoff.tzinfo).date()
                    != _day(old_chain["trade_date"]))):
            reasons.append("OLD_CONSUMER_READ_INVALID")
        if old_chain.get("consumer_receipt_verified") is not True:
            reasons.append("OLD_CONSUMER_RECEIPT_UNPROVEN")

    if (type(isolation) is not dict
            or not _text(isolation.get("research_identity"))
            or not _text(isolation.get("independent_output"))
            or any(isolation.get(field) is not True for field in (
                "research_only", "production_pg_write_unreachable",
                "production_container_write_unreachable"))):
        reasons.append("RESEARCH_ISOLATION_UNPROVEN")

    return {
        "comparability": "NOT_COMPARABLE" if reasons else "COMPARABLE_CANDIDATE",
        "comparability_basis": "CALLER_DECLARATIONS_ONLY",
        "external_evidence_verification": "NOT_PERFORMED",
        "cutover_verdict": "BLOCK_CUTOVER",
        "reasons": reasons,
    }
