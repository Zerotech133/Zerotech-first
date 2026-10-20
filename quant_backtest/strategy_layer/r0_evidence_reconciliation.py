"""WO-QT-20261020-036：调用方已给出的 R0 内存证据逐段对账；不签发、不接线。

注意：探针返回值里的 ``probe["brief"]`` 只是 ``NOT_VERIFIED`` 存根，
与本函数的 ``brief`` 参数（``build_research_brief`` 的研究简报）同名但不同物，
误传会按「简报来源/日期不一致」失败关闭。
"""
from __future__ import annotations

from datetime import date
import re


_STAGES = ("source", "signal", "combined", "pool", "brief", "receipt")
_COUNTS = ("source_events", "matched_events", "signals", "kept", "combined", "pool")
_BRIEF_COUNT_KEYS = (*_COUNTS, "brief", "receipt")
_SUPERSEDED_KEYS = frozenset(("action", "score", "dt", "rationale"))


def _number(value):
    return type(value) is int and value >= 0


def _same(left, right):
    """类型严格相等：禁止 True 冒充 1、3.0 冒充 3。"""
    return type(left) is type(right) and left == right


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{8}", value):
        return False
    try:
        date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:]}")
    except ValueError:
        return False
    return True


def reconcile_evidence(trade_date: str, probe: dict | None = None,
                       source_attestation: dict | None = None,
                       brief: dict | None = None,
                       receipt_attestation: dict | None = None) -> dict:
    """仅对调用方提供的四份 dict 做结构及计数对账，不将局部核验提升为 READY。"""
    if not _day(trade_date):
        raise ValueError("trade_date 必须为有效的 YYYYMMDD")
    result = {
        "scope": "RESEARCH_ONLY_NOT_PRODUCTION", "trade_date": trade_date,
        "status": "NOT_VERIFIED", "real_source_verdict": "BLOCKED_REAL_DATA",
        "cutover_verdict": "BLOCK_CUTOVER",
        "stages": {stage: {"status": "NOT_VERIFIED", "count": None,
                           "reason": "缺少可对账证据"} for stage in _STAGES},
        "counts": {key: None for key in (*_COUNTS, "brief", "receipt")},
        "reasons": [],
    }

    def mark(stage, status, count, reason):
        result["stages"][stage] = {"status": status, "count": count, "reason": reason}
        if status == "REJECTED":
            result["status"] = "REJECTED"
            result["reasons"].append(f"{stage}: {reason}")

    def stop(stage, reason):
        mark(stage, "REJECTED", None, reason)
        return result

    if probe is None:
        return result
    if (type(probe) is not dict or probe.get("scope") != result["scope"]
            or probe.get("cutover_verdict") != "BLOCK_CUTOVER"
            or probe.get("real_source_verdict") != "BLOCKED_REAL_DATA"
            or probe.get("trade_date") != trade_date):
        return stop("source", "探针边界或业务日不一致")
    source, counts = probe.get("source"), probe.get("counts")
    if type(source) is not dict or type(counts) is not dict:
        return stop("source", "缺少探针来源或计数段")
    if source.get("business_date") != trade_date:
        return stop("source", "来源业务日与请求日不一致")
    if (not isinstance(source.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", source["sha256"])):
        return stop("source", "缺少有效的本次源快照摘要")
    if (probe.get("status") not in ("UNVERIFIED", "NO_MATCH_UNVERIFIED")
            or probe.get("failures") != []
            or probe.get("source", {}).get("identity_status") != "UNVERIFIED"
            or probe.get("brief_status") not in (None, "NOT_VERIFIED")
            or probe.get("receipt_status") not in (None, "NOT_VERIFIED")
            or probe.get("chain_status") not in
            ("VERIFIED_IN_MEMORY", "SELF_DECLARED_EMPTY", "POST_HOC_NO_MATCH")
            or (probe.get("status") == "NO_MATCH_UNVERIFIED") !=
            (probe.get("chain_status") == "POST_HOC_NO_MATCH")):
        return stop("source", "探针失败或内存链不完整")
    total = counts.get("source_events")
    if not _number(total):
        return stop("source", "源事件总数缺失或无效，不得按零处理")
    result["counts"]["source_events"] = total
    matched = counts.get("matched_events")
    historical = "matched_events" in counts
    if historical:
        if matched is not None and (not _number(matched) or matched > total):
            return stop("source", "历史池当日匹配数无效或超过整池总数")
        result["counts"]["matched_events"] = matched
    day_count = matched if historical else total
    mark("source", "IN_MEMORY_ONLY", total,
         "历史多日池整池事件数，非当日事件数" if historical else "自声明批次内存事件数，非独立来源身份")

    if source_attestation is not None:
        if type(source_attestation) is not dict:
            return stop("source", "来源核验结果格式错误")
        if source_attestation.get("status") == "VERIFIED":
            if (source_attestation.get("identity_status") != "VERIFIED"
                    or source_attestation.get("historical_single_day_frozen") is not False
                    or source_attestation.get("real_source_verdict") != "BLOCKED_REAL_DATA"
                    or source_attestation.get("business_date") != trade_date
                    or not source.get("sha256")
                    or source_attestation.get("source_sha256") != source["sha256"]
                    or source_attestation.get("event_count") != total
                    or historical and matched != total):
                return stop("source", "签名核验与同日源快照/事件数不一致；多日池不得冒充单日签名")
            mark("source", "CALLER_ATTESTATION_CLAIM", total,
                 "仅收到调用方提供的验签结果，未在此处验签；不证明历史冻结或 READY")
        elif source_attestation.get("status") == "REJECTED":
            return stop("source", "来源验签已明确失败（伪造、换源或作用域不符），不得继续对账")
        elif source_attestation.get("status") != "UNVERIFIED":
            return stop("source", "来源核验状态不可信")
        elif source_attestation.get("business_date") not in (None, trade_date):
            return stop("source", "未验证的来源核验结果业务日也不得错日")
        else:
            mark("source", "NOT_VERIFIED", total,
                 "来源签名未通过独立核验；内存源计数不能证明真实来源")

    if probe["chain_status"] in ("SELF_DECLARED_EMPTY", "POST_HOC_NO_MATCH"):
        if probe["chain_status"] == "SELF_DECLARED_EMPTY" and (
                historical or total != 0 or any(counts.get(k) != 0 for k in
                                                 ("signals", "kept", "combined", "pool"))):
            return stop("signal", "自声明空批次的计数矛盾")
        if probe["chain_status"] == "POST_HOC_NO_MATCH" and (
                not historical or matched is not None
                or any(counts.get(k) is not None for k in
                                           ("signals", "kept", "combined", "pool"))):
            return stop("signal", "历史池未匹配时下游计数必须未知")
        mark("signal", "NOT_VERIFIED", None,
             "自声明空批次或历史池未匹配不等于可信零")
        return result
    if not _number(day_count) or day_count == 0:
        return stop("signal", "当日事件缺失/为零但宣称内存链完成")
    if probe.get("pool_status") != "IN_MEMORY_ONLY":
        return stop("pool", "股票池未完成内存构建")
    for key in ("signals", "kept", "combined", "pool"):
        if not _number(counts.get(key)):
            return stop("signal" if key == "signals" else key if key in _STAGES else "combined",
                        f"{key} 计数缺失或无效")
    signals, kept, fused, buys = (counts[k] for k in ("signals", "kept", "combined", "pool"))
    if signals != day_count:
        return stop("signal", "Signal 数与当日事件数不一致（历史池不得使用整池总数）")
    result["counts"]["signals"] = signals
    mark("signal", "IN_MEMORY_ONLY", signals, "当日源事件与 Signal 一对一，仅内存")
    rows = probe.get("combined")
    if type(rows) is not list or len(rows) != fused or not 0 < fused <= kept <= signals:
        return stop("combined", "融合行缺失或 source→kept→combined 计数矛盾")
    symbols = set()
    contributions = superseded = 0
    for row in rows:
        if (type(row) is not dict or row.get("trade_date") != trade_date
                or not isinstance(row.get("symbol"), str) or not row["symbol"]
                or row["symbol"] in symbols or row.get("action") not in
                ("buy", "sell", "hold", "watch")):
            return stop("combined", "融合行日期、标的或动作不合法/重复")
        symbols.add(row["symbol"])
        items = row.get("contributions")
        if type(items) is not dict or not items:
            return stop("combined", "融合行缺少贡献明细")
        if any(type(key) is not str or not key for key in items):
            return stop("combined", "贡献明细的策略标识不合法")
        contributions += len(items)
        for item in items.values():
            if type(item) is not dict:
                return stop("combined", "被覆盖信号明细格式错误")
            superseded_items = item.get("superseded", [])
            if type(superseded_items) is not list:
                return stop("combined", "被覆盖信号明细格式错误")
            for entry in superseded_items:
                if (type(entry) is not dict or set(entry) != _SUPERSEDED_KEYS
                        or type(entry.get("action")) is not str or not entry["action"]
                        or type(entry.get("score")) is not float
                        or not (entry.get("dt") is None or type(entry.get("dt")) is str)
                        or type(entry.get("rationale")) is not str
                        or len(entry["rationale"]) > 80):
                    return stop("combined",
                                "被覆盖条目结构不合法（非四键/类型不符的条目不得充数）")
            superseded += len(superseded_items)
    if contributions != kept or contributions + superseded != signals:
        return stop("combined", "贡献/被覆盖条数与 Signal、kept 计数不一致")
    result["counts"].update(kept=kept, combined=fused)
    mark("combined", "IN_MEMORY_ONLY", fused, "方案 C 贡献及被覆盖计数一致，仅内存")

    pool = probe.get("pool")
    if (type(pool) is not dict or pool.get("trade_date") != trade_date
            or pool.get("scope") != result["scope"]
            or pool.get("cutover_verdict") != "BLOCK_CUTOVER"
            or type(pool.get("stats")) is not dict or type(pool.get("pool")) is not list):
        return stop("pool", "股票池结构或业务日/边界不一致")
    stats = pool["stats"]
    expected_buy = {r["symbol"] for r in rows if r["action"] == "buy"}
    pool_symbols = [r.get("symbol") for r in pool["pool"] if type(r) is dict]
    if (len(pool_symbols) != len(pool["pool"])
            or any(type(symbol) is not str or not symbol for symbol in pool_symbols)):
        return stop("pool", "股票池标的结构无效")
    if (len(pool_symbols) != len(set(pool_symbols))
            or any(r.get("action") != "buy" for r in pool["pool"])
            or set(pool_symbols) != expected_buy or buys != len(expected_buy)
            or not _same(stats.get("combined_total"), fused)
            or not _same(stats.get("buy_total"), buys)
            or not _same(stats.get("superseded_total"), superseded)):
        return stop("pool", "股票池候选/统计与融合明细不一致")
    result["counts"]["pool"] = buys
    mark("pool", "IN_MEMORY_ONLY", buys, "buy 候选数，不代表真实池已发布")

    if brief is None:
        return result
    if (type(brief) is not dict or brief.get("scope") != result["scope"]
            or brief.get("cutover_verdict") != "BLOCK_CUTOVER"
            or brief.get("trade_date") != trade_date
            or type(brief.get("source")) is not dict
            or brief["source"].get("sha256") != source.get("sha256")
            or brief["source"].get("business_date") != trade_date
            or type(brief.get("counts")) is not dict):
        return stop("brief", "简报来源、日期或研究边界不一致")
    brief_counts = brief["counts"]
    if (any(key not in brief_counts for key in _BRIEF_COUNT_KEYS)
            or any(not _same(brief_counts[key], result["counts"][key]) for key in _COUNTS)
            or not _same(brief_counts["brief"], None)
            or not _same(brief_counts["receipt"], None)
            or brief.get("chain_status") != "VERIFIED_IN_MEMORY"
            or brief.get("probe_status") not in (None, "UNVERIFIED")
            or brief.get("real_source_verdict") not in (None, "BLOCKED_REAL_DATA")
            or type(brief.get("pool_summary")) is not dict
            or type(brief.get("scheme_c")) is not dict
            or not _same(brief["pool_summary"].get("buy_total"), buys)
            or not _same(brief["scheme_c"].get("contributions"), kept)
            or not _same(brief["scheme_c"].get("superseded"), superseded)):
        return stop("brief", "简报计数/方案 C/池摘要与探针不一致（缺键或类型不符亦拒）")
    result["counts"]["brief"] = 1
    mark("brief", "IN_MEMORY_ONLY", 1, "生成内存简报不等于已被接收")
    if receipt_attestation is None:
        return result
    if type(receipt_attestation) is not dict:
        return stop("receipt", "回执核验结果格式错误")
    if receipt_attestation.get("status") == "VERIFIED_RECEIPT_ONLY":
        result["counts"]["receipt"] = 1
        mark("receipt", "CALLER_RECEIPT_CLAIM", 1,
             "仅收到调用方提供的回执核验结论，未在此处验签或证明实际接收；不代表 READY")
    elif receipt_attestation.get("status") == "NOT_VERIFIED":
        mark("receipt", "NOT_VERIFIED", None, "未取得独立接收方的有效回执（含合成/自回执）")
    else:
        return stop("receipt", "回执状态不可信，不能将自报 READY 当作验证")
    return result
