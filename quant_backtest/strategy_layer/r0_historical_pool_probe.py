"""历史 CN 多日 JSONL 事后截取；只读取一次源字节，不签发冻结身份或落盘。"""

from __future__ import annotations

from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re

from .adapters.chan_monitor import ChanMonitorAdapter
from .aggregator import SignalAggregator, preaggregate_symbol
from .r0_research_brief import build_research_brief
from .signal import Signal
from .xunyou import build_pool


_DAY = re.compile(r"[0-9]{8}\Z")
_CN = re.compile(r"[0-9]{6}\.(?:SH|SZ|BJ)\Z")
_TYPES = frozenset(("1买", "2买", "3买", "1卖", "2卖", "3卖"))


def _calendar_day(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value
    ):
        raise ValueError("日期必须为 YYYY-MM-DD")
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError("日期无效")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"重复 JSON 字段: {key}")
        obj[key] = value
    return obj


def _reject_constant(value: str) -> None:
    raise ValueError(f"非法 JSON 数值: {value}")


def _validate_row(row: object) -> dict:
    if not isinstance(row, dict):
        raise ValueError("事件行必须为 JSON 对象")
    if row.get("market") != "CN":
        raise ValueError("market 必须明确为 CN")
    if not isinstance(row.get("code"), str) or not _CN.fullmatch(row["code"]):
        raise ValueError("code 必须为六位 CN 市场代码")
    if not isinstance(row.get("type"), str) or row["type"] not in _TYPES:
        raise ValueError("type 非受支持的买卖点")
    detect = _calendar_day(row.get("detect_date"))
    if _calendar_day(row.get("dt")) > detect:
        raise ValueError("dt 晚于 detect_date")
    price = row.get("price")
    if type(price) not in (int, float) or not math.isfinite(price) or price <= 0:
        raise ValueError("price 必须为有限正数")
    for key in ("mbei", "bei", "bei2", "vv"):
        if key in row and row[key] is not None and type(row[key]) is not bool:
            raise ValueError(f"{key} 必须为布尔值或 null")
    for key in ("trend", "reg"):
        if key in row and row[key] is not None and not isinstance(row[key], str):
            raise ValueError(f"{key} 必须为字符串或 null")
    return row


def inspect_historical_pool(business_day: str, source_path: str | Path) -> dict:
    """显式历史日、显式源路径；整份快照逐行校验后仅转当日行。永不写文件。"""
    if not isinstance(business_day, str) or not _DAY.fullmatch(business_day):
        raise ValueError("business_day 必须显式指定 YYYYMMDD")
    target = _calendar_day(f"{business_day[:4]}-{business_day[4:6]}-{business_day[6:]}")
    source = Path(source_path).resolve()
    result = {
        "scope": "RESEARCH_ONLY_NOT_PRODUCTION",
        "cutover_verdict": "BLOCK_CUTOVER",
        "trade_date": business_day,
        "extraction": "历史多日池事后截取，非点时冻结批次",
        "status": "NOT_VERIFIED",
        "source": {
            "path": str(source),
            "manifest": None,
            "sha256": None,
            "business_date": business_day,
            "observed_date": None,
            "identity_status": "UNVERIFIED",
            "identity_reason": "源字节哈希仅标识本次所读快照，不能自证独立冻结身份或历史可见性",
        },
        "counts": {
            "source_events": None,
            "matched_events": None,
            "signals": None,
            "kept": None,
            "combined": None,
            "pool": None,
        },
        "chain_status": "NOT_VERIFIED",
        "pool_status": "NOT_VERIFIED",
        "brief_status": "NOT_VERIFIED",
        "receipt_status": "NOT_VERIFIED",
        "real_source_verdict": "BLOCKED_REAL_DATA",
        "failures": [],
    }

    def finish() -> dict:
        brief = build_research_brief(result)
        brief["risks"] = [
            risk for risk in brief["risks"] if "自声明 manifest" not in risk
        ]
        brief["risks"].insert(
            0,
            "历史多日池事后截取：哈希仅标识本次快照，不证明独立冻结、点时可见性或真实影子作业",
        )
        if result["status"] == "NO_MATCH_UNVERIFIED":
            brief["risks"].append(
                "未匹配当日行不等于可信零事件，无法证明当日实际无信号"
            )
        result["brief"] = brief
        return result

    try:
        # 唯一文件读取：后续解码、哈希、校验、转换都消费同一个 bytes 对象。
        raw = source.read_bytes()
        result["source"]["sha256"] = hashlib.sha256(raw).hexdigest()
        text = raw.decode("utf-8")
        lines = text.split("\n")
        if lines[-1] == "":
            lines.pop()
        rows = []
        for number, line in enumerate(lines, 1):
            try:
                if not line.strip():
                    raise ValueError("空白行")
                row = json.loads(
                    line,
                    object_pairs_hook=_unique_object,
                    parse_constant=_reject_constant,
                )
                rows.append(_validate_row(row))
            except (ValueError, TypeError) as exc:
                raise ValueError(f"第 {number} 行: {exc}") from exc
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        result["status"] = "INVALID_SOURCE"
        result["failures"].append(
            {
                "stage": "source",
                "strategy": None,
                "reason": f"{type(exc).__name__}: {exc}",
            }
        )
        return finish()

    result["counts"]["source_events"] = len(rows)
    picked = [row for row in rows if row["detect_date"] == target]
    if not picked:
        result["status"] = "NO_MATCH_UNVERIFIED"
        result["chain_status"] = "POST_HOC_NO_MATCH"
        return finish()

    result["counts"]["matched_events"] = len(picked)
    try:
        adapter = ChanMonitorAdapter(pool_globs=[str(source)])
        signals = [adapter._convert(row, business_day) for row in picked]
        if any(
            not isinstance(sig, Signal)
            or sig.strategy != adapter.strategy_id
            or sig.trade_date != business_day
            for sig in signals
        ):
            raise ValueError("信号转换不完整")
        grouped = {}
        for sig in signals:
            grouped.setdefault(sig.symbol, []).append((adapter.strategy_id, 1.0, sig))
        combined, divergences = [], []
        kept_total = 0
        fuser = SignalAggregator()
        for symbol in sorted(grouped):
            kept, superseded = preaggregate_symbol(grouped[symbol])
            kept_total += len(kept)
            item = fuser._fuse_symbol(business_day, symbol, kept, superseded=superseded)
            combined.append(item)
            if item["divergence"]:
                divergences.append({"symbol": symbol, "trade_date": business_day})
        pool = build_pool(business_day, combined, divergences)
        result["counts"].update(
            signals=len(signals),
            kept=kept_total,
            combined=len(combined),
            pool=pool["stats"]["buy_total"],
        )
        result.update(
            status="UNVERIFIED",
            chain_status="VERIFIED_IN_MEMORY",
            pool_status="IN_MEMORY_ONLY",
            combined=combined,
            pool=pool,
        )
    except Exception as exc:
        result["status"] = "CONVERSION_ERROR"
        result["failures"].append(
            {
                "stage": "adapter",
                "strategy": "chan_monitor",
                "reason": f"{type(exc).__name__}: {exc}",
            }
        )

    return finish()
