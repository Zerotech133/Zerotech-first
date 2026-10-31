# -*- coding: utf-8 -*-
"""EvidenceBundle raw-ref 采集（2026-10-31 WO-EVIDRAW，K-ORG-20261031-EVIDRAW）。

把 `make_context` 上下文里**可回查原始数据**的部分升级为 raw ref：

| 上下文段 | 数据来源 | raw 核验方式 |
|---|---|---|
| `market_technical` | 本地日 K parquet（`quant_backtest/data/ashare/<code>_<SH|SZ|BJ>.parquet`） | 整文件 sha256 + **行区间**（上下文 8 根 K 线在文件中的连续行），并逐行核对 `close` 与上下文文本一致；对不上就不挂 raw（不假成功） |
| L4 | `agent_platform/data/xsec/l4_status.json` | 整文件 sha256 + JSON 记录选择器 `symbols.<code>`（须真实存在） |
| `fundamental` / `news_event` / `final_decision` | 腾讯快照 / K 线派生代理 / 拼装文本 | **无原始文件** → 明确记 `unavailable`（降级为摘要 ref，不伪造 raw） |

本模块只读：读 parquet/json + 算 sha256；不写文件、不联网、不启动服务。
采集失败一律返回 `report.unavailable/rejected`，由 run_shadow fail-open 降级，
**不阻塞 D1 主链、也不假装 raw 成功**。
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from evidence_bundle import (
    REPO_ROOT,
    RawRefError,
    make_raw_ref,
    normalize_pit_as_of,
    validate_pit,
)

__all__ = [
    "ASHARE_REL",
    "L4_REL",
    "collect_shadow_raw_refs",
    "context_kline_rows",
    "symbol_parts",
    "validate_pit",
]

ASHARE_REL = Path("quant_backtest") / "data" / "ashare"
L4_REL = Path("agent_platform") / "data" / "xsec" / "l4_status.json"

_KLINE_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) 收(-?\d+(?:\.\d+)?) 高(-?\d+(?:\.\d+)?) "
    r"低(-?\d+(?:\.\d+)?) 量(-?\d+(?:\.\d+)?)$")
_TOLERANCE = 1e-6


def symbol_parts(symbol) -> tuple[str, str]:
    """600519 / sh600519 / 600519.SH / 920378 → (bare, SH|SZ|BJ)。"""
    text = str(symbol or "").strip().upper()
    for token in ("SH", "SZ", "BJ", ".", " "):
        text = text.replace(token, "")
    bare = re.sub(r"[^0-9]", "", text)
    if bare.startswith(("4", "8", "92")):
        suffix = "BJ"
    elif bare.startswith("6"):
        suffix = "SH"
    else:
        suffix = "SZ"
    return bare, suffix


def _norm_date(value) -> str:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return "%s-%s-%s" % (text[:4], text[4:6], text[6:])
    return text


def context_kline_rows(ctx) -> list:
    """从 ``market_technical`` 文本解析出 ``[(date, close), ...]``（可能为空）。"""
    text = str((ctx or {}).get("market_technical") or "")
    rows = []
    for line in text.splitlines():
        match = _KLINE_LINE_RE.match(line.strip())
        if not match:
            continue
        rows.append((match.group(1), float(match.group(2))))
    return rows


def _read_kline_rows(path: Path, pyarrow_parquet):
    table = pyarrow_parquet.read_table(path, columns=["trade_date", "close"])
    frame = table.to_pydict()
    return [(_norm_date(date), float(close))
            for date, close in zip(frame["trade_date"], frame["close"])]


def _json_selector_value(path: Path, selector: str):
    """读取 JSON 点路径 selector 的值（只读；找不到/坏文件返回 None）。"""
    try:
        import json  # noqa: PLC0415  惰性
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    cur = doc
    for part in str(selector or "").split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def _safe_pit(value):
    """把候选数据时点规范化；非法/空 → ""（不伪造，由 issue 记录）。"""
    try:
        return normalize_pit_as_of(value)
    except RawRefError:
        return ""


def _collect_kline(symbol, ctx, root: Path, report: dict):
    bare, suffix = symbol_parts(symbol)
    path = root / ASHARE_REL / ("%s_%s.parquet" % (bare, suffix))
    rows = context_kline_rows(ctx)
    if not rows:
        report["unavailable"].append({
            "source": "market_technical", "reason": "context_kline_unparseable"})
        return None
    if not path.is_file():
        report["unavailable"].append({
            "source": "market_technical", "reason": "kline_parquet_missing",
            "path": path.relative_to(root).as_posix()})
        return None
    try:
        import pyarrow.parquet as pq  # noqa: PLC0415  惰性
    except Exception:  # noqa: BLE001
        report["unavailable"].append({
            "source": "market_technical", "reason": "pyarrow_unavailable"})
        return None
    try:
        local = _read_kline_rows(path, pq)
    except Exception as exc:  # noqa: BLE001
        report["unavailable"].append({
            "source": "market_technical",
            "reason": "kline_parquet_unreadable",
            "detail": str(exc)[:120]})
        return None
    by_date = {}
    for index, (date, close) in enumerate(local):
        by_date.setdefault(date, index)
    indexes = []
    for date, close in rows:
        index = by_date.get(date)
        if index is None:
            report["unavailable"].append({
                "source": "market_technical",
                "reason": "context_date_not_in_parquet", "date": date})
            return None
        if abs(local[index][1] - close) > _TOLERANCE:
            report["unavailable"].append({
                "source": "market_technical",
                "reason": "context_close_mismatch", "date": date,
                "context": close, "parquet": local[index][1]})
            return None
        indexes.append(index)
    start, end = min(indexes), max(indexes) + 1
    if end - start != len(rows):
        report["unavailable"].append({
            "source": "market_technical",
            "reason": "context_kline_not_contiguous"})
        return None
    try:
        return make_raw_ref(
            source="market_technical",
            path=path,
            kind="kline_parquet",
            row_range=[start, end],
            root=root,
            # 数据时点 = 上下文里最新一根 K 线交易日（不是采集时刻）
            pit_as_of=max(date for date, _close in rows),
        )
    except (RawRefError, OSError) as exc:
        report["unavailable"].append({
            "source": "market_technical", "reason": "raw_ref_build_failed",
            "detail": str(exc)[:160]})
        return None


def _collect_l4(symbol, root: Path, report: dict):
    bare, _suffix = symbol_parts(symbol)
    path = root / L4_REL
    if not path.is_file():
        report["unavailable"].append({
            "source": "l4_status", "reason": "l4_file_missing"})
        return None
    selector = "symbols.%s" % bare
    record = _json_selector_value(path, selector)
    pit = ""
    if isinstance(record, dict):
        pit = _safe_pit(record.get("as_of"))
        if str(record.get("as_of") or "").strip() and not pit:
            report.setdefault("pit_issues", []).append({
                "source": "l4_status", "reason": "bad_pit_format",
                "value": str(record.get("as_of"))[:40]})
    try:
        return make_raw_ref(
            source="l4_status",
            path=path,
            kind="l4_record",
            selector=selector,
            root=root,
            # 数据时点 = L4 记录的 as_of（数据侧时点），非采集时刻
            pit_as_of=pit or None,
        )
    except (RawRefError, OSError) as exc:
        report["unavailable"].append({
            "source": "l4_status", "reason": "l4_raw_ref_failed",
            "detail": str(exc)[:160]})
        return None


def collect_shadow_raw_refs(symbol, ctx, root=None) -> dict:
    """采集 run_shadow 可用的 raw refs（不写盘、不联网、失败只记录）。

    返回 ``{"refs": [EvidenceRef], "root": <str>, "report": {...}}``；
    ``report.unavailable`` 明确列出无法 raw 化的段及原因（供降级审计），
    调用方不得把 unavailable 当 raw。
    """
    base = Path(root).resolve() if root else REPO_ROOT.resolve()
    report = {"upgraded": [], "extra": [], "rejected": [],
              "unavailable": [], "root": str(base),
              "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z",
                                            time.localtime())}
    refs = []
    kline = _collect_kline(symbol, ctx, base, report)
    if kline is not None:
        refs.append(kline)
    l4 = _collect_l4(symbol, base, report)
    if l4 is not None:
        refs.append(l4)
    # 明确声明不可 raw 化的段（诚实降级，不留"静默摘要"）
    report["unavailable"].extend([
        {"source": "fundamental", "reason": "network_snapshot_no_raw_file"},
        {"source": "news_event", "reason": "derived_proxy_no_raw_file"},
        {"source": "final_decision", "reason": "derived_text_no_raw_file"},
    ])
    # PIT 分账：as_of=采集时刻，pit_as_of=数据时点；缺失/非法/时点倒挂显式列出。
    pit_refs = []
    pit_issues = list(report.get("pit_issues") or [])
    for ref in refs:
        ok, reason = validate_pit(ref.as_of, getattr(ref, "pit_as_of", ""))
        pit_refs.append({
            "source": ref.source,
            "kind": ref.kind,
            "as_of": ref.as_of,
            "pit_as_of": getattr(ref, "pit_as_of", ""),
            "pit_ok": ok,
            "pit_reason": reason,
        })
        if not ok:
            pit_issues.append({"source": ref.source, "reason": reason})
    report["pit"] = {"collection_as_of": report["generated_at"],
                     "refs": pit_refs, "issues": pit_issues}
    return {"refs": refs, "root": str(base), "report": report}
