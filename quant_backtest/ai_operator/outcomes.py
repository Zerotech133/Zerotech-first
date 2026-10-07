"""按显式交易日历生成仅供影子审计的未来收盘观察标签。"""

import hashlib
import json
import math
import re
from datetime import date, datetime

from .contracts import canonical_json, normalize_time
from .shadow import make_shadow_decision


_CALENDAR_FIELDS = frozenset({"version", "sessions"})
_SESSION_FIELDS = frozenset({"session", "close_at"})
_BAR_FIELDS = frozenset({"session", "close", "close_at", "available_at"})
_SOURCE_FIELDS = frozenset({"source_id", "source_hash", "price_basis", "availability_status"})


def _fields(value, expected, name):
    if type(value) is not dict or set(value) != expected:
        raise ValueError(f"{name} 必须是字段完整且无未知字段的 JSON 对象")


def _nonempty(value, name):
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")


def _time(value, name):
    if type(value) is not str:
        raise ValueError(f"{name} 必须是带时区的 ISO 字符串")
    return normalize_time(value)


def _session(value):
    if type(value) is not str or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValueError("session 必须是 YYYY-MM-DD 日期")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("session 日期非法") from exc
    return value


def _calendar(value):
    _fields(value, _CALENDAR_FIELDS, "calendar")
    _nonempty(value["version"], "calendar.version")
    sessions = value["sessions"]
    if type(sessions) is not list or not 1 <= len(sessions) <= 5000:
        raise ValueError("calendar.sessions 必须是 1 至 5000 项的 JSON 列表")
    result = []
    previous_session = previous_close = None
    for item in sessions:
        _fields(item, _SESSION_FIELDS, "日历项")
        session = _session(item["session"])
        close_at = _time(item["close_at"], "日历 close_at")
        if datetime.fromisoformat(item["close_at"]).date().isoformat() != session:
            raise ValueError("session 必须等于 close_at 所在时区的本地日期")
        if previous_session is not None and (session <= previous_session or close_at <= previous_close):
            raise ValueError("日历 session 和 close_at 必须唯一且严格升序")
        result.append({"session": session, "close_at": close_at})
        previous_session, previous_close = session, close_at
    return result


def _bars(value, sessions):
    if type(value) is not list or len(value) > 5000:
        raise ValueError("bars 必须是最多 5000 项的 JSON 列表")
    calendar_times = {item["session"]: item["close_at"] for item in sessions}
    result = {}
    previous = None
    for item in value:
        _fields(item, _BAR_FIELDS, "行情项")
        session = _session(item["session"])
        if session not in calendar_times or (previous is not None and session <= previous):
            raise ValueError("行情 session 必须在日历内且唯一严格升序")
        price = item["close"]
        try:
            valid = type(price) in (int, float) and math.isfinite(price) and price > 0
        except (OverflowError, ValueError) as exc:
            raise ValueError("close 超出有限数值范围") from exc
        if not valid:
            raise ValueError("close 必须为有限正数且不能是布尔值")
        close_at = _time(item["close_at"], "行情 close_at")
        if close_at != calendar_times[session]:
            raise ValueError("行情 close_at 必须与日历为同一瞬间")
        available_at = item["available_at"]
        if available_at is not None:
            available_at = _time(available_at, "行情 available_at")
            if available_at < close_at:
                raise ValueError("行情 available_at 不能早于 close_at")
        result[session] = {"close": price, "available_at": available_at}
        previous = session
    return result


def _source(value):
    _fields(value, _SOURCE_FIELDS, "source")
    _nonempty(value["source_id"], "source.source_id")
    if type(value["source_hash"]) is not str or re.fullmatch(r"[0-9a-f]{64}", value["source_hash"]) is None:
        raise ValueError("source_hash 必须为小写 64 位 SHA256")
    if type(value["price_basis"]) is not str or value["price_basis"] not in {"raw", "qfq_snapshot", "hfq_snapshot"}:
        raise ValueError("price_basis 仅支持 raw、qfq_snapshot 或 hfq_snapshot")
    if type(value["availability_status"]) is not str or value["availability_status"] not in {"trusted", "unknown"}:
        raise ValueError("availability_status 仅支持 trusted 或 unknown")


def label_decision(pack, decision, bars, calendar, source, evaluated_at, horizons=None) -> dict:
    """生成可重复标签；可信来源与复权口径均为调用者声明。"""
    frozen = json.loads(canonical_json({"pack": pack, "decision": decision, "bars": bars,
                                      "calendar": calendar, "source": source, "evaluated_at": evaluated_at,
                                      "horizons": [5, 20] if horizons is None else horizons}))
    pack, decision, calendar, source = (frozen[name] for name in ("pack", "decision", "calendar", "source"))
    if type(decision) is not dict:
        raise ValueError("decision 必须是完整影子决策 JSON 对象")
    rebuilt = make_shadow_decision(pack, decision.get("action"), decision.get("evidence_ids"),
                                   decision.get("rationale"), decision.get("valid_until"))
    if canonical_json(decision) != canonical_json(rebuilt):
        raise ValueError("影子决策与事实包重建结果不符，内容或 decision_id 可能被篡改")
    instant = _time(frozen["evaluated_at"], "evaluated_at")
    if instant < _time(decision["as_of"], "decision.as_of"):
        raise ValueError("evaluated_at 不能早于 decision.as_of")
    horizons = frozen["horizons"]
    if type(horizons) is not list or not 1 <= len(horizons) <= 10:
        raise ValueError("horizons 必须为 1 至 10 项的 JSON 列表")
    if any(type(item) is not int or not 1 <= item <= 250 for item in horizons):
        raise ValueError("horizon 必须是 1 至 250 的非布尔整数")
    if len(set(horizons)) != len(horizons):
        raise ValueError("horizons 必须唯一")
    _source(source)
    sessions = _calendar(calendar)
    bar_map = _bars(frozen["bars"], sessions)
    as_of = _time(decision["as_of"], "decision.as_of")
    reference = next((i for i, item in enumerate(sessions) if item["close_at"] > as_of), None)
    labels = []
    for horizon in horizons:
        end = None if reference is None or reference + horizon >= len(sessions) else reference + horizon
        # 日历不足时仍检查已存在的窗口行情，未知可用时间优先进入审计状态。
        window = [] if reference is None else sessions[reference:reference + horizon + 1]
        present = [bar_map[item["session"]] for item in window if item["session"] in bar_map]
        label = {"horizon": horizon, "status": "pending", "reason": None,
                 "reference_session": None if reference is None else sessions[reference]["session"],
                 "label_end_session": None if end is None else sessions[end]["session"],
                 "label_end_at": None if end is None else sessions[end]["close_at"],
                 "label_available_at": None, "reference_close": None, "end_close": None,
                 "target": None, "target_kind": "forward_close_return"}
        if source["availability_status"] == "unknown":
            label.update(status="audit_only", reason="unknown_source")
        elif any(item["available_at"] is None for item in present):
            label.update(status="audit_only", reason="unknown_availability")
        elif end is None:
            label["reason"] = "calendar_incomplete"
        elif len(present) != len(window):
            label["reason"] = "missing_bar"
        elif any(item["available_at"] > instant for item in present):
            label["reason"] = "not_available"
        else:
            reference_close = bar_map[sessions[reference]["session"]]["close"]
            end_close = bar_map[sessions[end]["session"]]["close"]
            try:
                target = end_close / reference_close - 1
                if not math.isfinite(target):
                    raise ValueError("观察目标超出有限数值范围")
            except (OverflowError, ZeroDivisionError) as exc:
                raise ValueError("观察目标计算溢出") from exc
            label.update(status="matured", reference_close=reference_close, end_close=end_close,
                         target=target, label_available_at=max(item["available_at"] for item in present))
        labels.append(label)
    result = {"schema_version": 1, "decision_id": decision["decision_id"], "fact_hash": decision["fact_hash"],
              "code": decision["code"], "action": decision["action"], "environment": "shadow",
              "evaluated_at": instant, "source": source, "calendar_version": calendar["version"],
              "labels": labels, "production_eligible": False}
    result["outcome_id"] = hashlib.sha256(canonical_json(result).encode("utf-8")).hexdigest()
    return result
