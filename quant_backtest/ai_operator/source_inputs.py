"""将当前本地快照对齐到标签输入，来源未审核时保持审计用途。"""

import hashlib

from .contracts import canonical_json, normalize_time
from .provenance import validate_snapshot
from .trading_calendar import validate_calendar_capture
from .outcomes import label_decision


def snapshot_for_outcomes(snapshot, calendar_capture) -> dict:
    """完整校验来源和日历；此适配不认证供应商或历史可用时点。"""
    snapshot = validate_snapshot(snapshot)
    capture = validate_calendar_capture(calendar_capture)
    exchange = "SSE" if snapshot["code"].endswith(".SH") else "SZSE"
    if capture["exchange"] != exchange:
        raise ValueError("证券交易所与交易日历不匹配")
    calendar = capture["calendar"]
    if not calendar["sessions"]:
        raise ValueError("标签日历至少需要一个开市日")
    times = {item["session"]: normalize_time(item["close_at"]) for item in calendar["sessions"]}
    bars = []
    for bar in snapshot["bars"]:
        if bar["session"] not in times:
            raise ValueError("行情日期不在日历覆盖的开市日内")
        if normalize_time(bar["close_at"]) != times[bar["session"]]:
            raise ValueError("行情收盘时间与日历不匹配")
        bars.append({name: bar[name] for name in ("session", "close", "close_at", "available_at")})
    identity = {"snapshot_id": snapshot["snapshot_id"], "calendar_capture_id": capture["capture_id"]}
    source = {"source_id": canonical_json({"source_id": snapshot["source_id"],
                                           "revision": snapshot["revision"],
                                           "calendar_source_id": capture["source_id"],
                                           "calendar_revision": capture["revision"]}),
              "source_hash": hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest(),
              "price_basis": snapshot["price_basis"], "availability_status": "unknown"}
    return {"code": snapshot["code"], "period": snapshot["period"], "bars": bars,
            "calendar": calendar, "source": source}


def label_captured_decision(snapshot, calendar_capture, pack, decision, evaluated_at, horizons=None) -> dict:
    """核对证券及周期后生成来源未审核的审计标签。"""
    adapted = snapshot_for_outcomes(snapshot, calendar_capture)
    if type(pack) is not dict or pack.get("code") != adapted["code"] or pack.get("period") != adapted["period"]:
        raise ValueError("事实包证券或周期与行情来源不一致")
    if type(pack.get("as_of")) is not str:
        raise ValueError("事实包 as_of 必须是带时区的 ISO 字符串")
    coverage_start = normalize_time(calendar_capture["coverage_start"] + "T00:00:00+08:00")
    if normalize_time(pack["as_of"]) < coverage_start:
        raise ValueError("决策早于日历覆盖起点，不能推定参考交易日")
    if type(evaluated_at) is not str:
        raise ValueError("evaluated_at 必须是带时区的 ISO 字符串")
    instant = normalize_time(evaluated_at)
    if instant < max(normalize_time(snapshot["received_at"]), normalize_time(calendar_capture["received_at"])):
        raise ValueError("evaluated_at 不能早于来源或日历实际接收时间")
    return label_decision(pack, decision, adapted["bars"], adapted["calendar"],
                          adapted["source"], instant, horizons=horizons)
