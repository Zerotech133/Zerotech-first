# -*- coding: utf-8 -*-
"""A4 read-only contract probe. No DB writes, portal writes or scheduler actions.

Usage: python -m strategy_layer.a4_readonly_probe [--trade-date YYYYMMDD]
       python -m strategy_layer.a4_readonly_probe --self-test
The probe deliberately does not call SignalAggregator.run (which persists to PG).
"""
import argparse
import glob
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .adapters.chan_monitor import ChanMonitorAdapter
from .aggregator import SignalAggregator

ROOT = Path(__file__).resolve().parents[1]
PORTAL_HISTORY = ROOT.parent / "portal" / "data" / "picks_history"


def _day(value):
    if not isinstance(value, str):
        return None
    try:
        day = datetime.strptime(value, "%Y-%m-%d").date()
        return day if day.isoformat() == value else None
    except ValueError:
        return None


def _market(code):
    code = str(code or "").upper()
    suffix = code.rsplit(".", 1)[-1] if "." in code else "US"
    return "CN" if suffix in ("SH", "SZ", "BJ") else suffix


def _invalid_source_dates(rows):
    """Audit malformed source dates skipped by the selector; keep older history valid."""
    invalid = Counter()
    for row in rows:
        if _day(row.get("detect_date")) is None:
            invalid[_market(row.get("code"))] += 1
    return dict(sorted(invalid.items()))


def _batch_health(picked, anchor):
    """Per-market freshness evidence; invalid/mixed batch dates fail closed."""
    by_market = defaultdict(list)
    for row in picked:
        by_market[_market(row.get("code"))].append(row)
    health = {}
    for market, rows in sorted(by_market.items()):
        dates = {_day(r.get("detect_date")) for r in rows}
        if None in dates or len(dates) != 1:
            health[market] = {"detect_date": None, "age_calendar_days": None,
                              "status": "INVALID_BATCH_DATE", "rows": len(rows)}
            continue
        day = next(iter(dates))
        age = (anchor - day).days
        health[market] = {"detect_date": day.isoformat(),
                          "age_calendar_days": age,
                          "status": "STALE" if age > 4 else "FRESH" if age >= 0 else "FUTURE",
                          "rows": len(rows)}
    return health


def _source_files(adapter):
    """Read each expanded path once; hash and parse the same bytes for this probe.

    Production adapter still reads live paths independently. This snapshot proves
    only this probe's inputs, not the bytes used by the production job.
    """
    evidence, rows, origins, anomalies = [], [], {}, []
    paths = set()
    for pattern in adapter._pool_globs:
        matches = glob.glob(pattern)
        if not matches:
            evidence.append({"path": pattern, "status": "MISSING", "bytes": None,
                             "sha256": None, "parsed_rows": 0, "bad_json": 0,
                             "non_object": 0, "invalid_dates": 0})
        paths.update(matches)
    # Same deduplicated, sorted file order as ChanMonitorAdapter._pool_files().
    for path in sorted(paths):
        item = {"path": path, "status": "OK", "bytes": None,
                "sha256": None, "parsed_rows": 0, "bad_json": 0,
                "non_object": 0, "invalid_dates": 0}
        evidence.append(item)
        try:
            data = Path(path).read_bytes()
            item["bytes"] = len(data)
            item["sha256"] = hashlib.sha256(data).hexdigest()
            if not data.strip():
                item["status"] = "EMPTY"
                continue
            # Decode before parsing: a bad UTF-8 file is quarantined in full.
            text = data.decode("utf-8")
            file_rows = []
            for line_number, line in enumerate(text.splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line.strip())
                except (ValueError, TypeError):
                    item["bad_json"] += 1
                    anomalies.append({"source": {"path": path, "line_number": line_number,
                                                  "sha256": item["sha256"]},
                                      "reason": "BAD_JSON"})
                    continue
                if not isinstance(obj, dict):
                    item["non_object"] += 1
                    anomalies.append({"source": {"path": path, "line_number": line_number,
                                                  "sha256": item["sha256"]},
                                      "reason": "NON_OBJECT"})
                    continue
                file_rows.append((line_number, obj))
                item["parsed_rows"] += 1
                if _day(obj.get("detect_date")) is None:
                    item["invalid_dates"] += 1
                    anomalies.append({"source": {"path": path, "line_number": line_number,
                                                  "sha256": item["sha256"]},
                                      "reason": "INVALID_DETECT_DATE", "code": obj.get("code"),
                                      "type": obj.get("type"),
                                      "detect_date": obj.get("detect_date")})
            if item["bad_json"] or item["non_object"]:
                item["status"] = "MALFORMED_ROWS"
            elif not item["parsed_rows"]:
                item["status"] = "NO_OBJECT_ROWS"
            for line_number, obj in file_rows:
                origins[id(obj)] = {"path": path, "line_number": line_number,
                                    "sha256": item["sha256"]}
                rows.append(obj)
        except (OSError, UnicodeError) as exc:
            item["status"] = "UNREADABLE"
            item["error_type"] = type(exc).__name__
    return evidence, rows, origins, anomalies


def _history_evidence(path, anchor):
    if not path:
        return {"path": None, "status": "NOT_REQUESTED", "scan_run_date": None,
                "trade_basis": None}, None, None
    info = {"path": str(path), "status": "UNREADABLE", "scan_run_date": None,
            "trade_basis": None}
    try:
        data = Path(path).read_bytes()
        info["sha256"] = hashlib.sha256(data).hexdigest()
        doc = json.loads(data.decode("utf-8"))
        if not isinstance(doc, dict):
            raise ValueError("history must be an object")
        info["scan_run_date"] = doc.get("scan_run_date")
        info["trade_basis"] = doc.get("trade_basis")
        if (_day(info["scan_run_date"]) != anchor
                or not isinstance(info["trade_basis"], str)
                or not info["trade_basis"].startswith(anchor.isoformat() + " 收盘")):
            info["status"] = "DATE_MISMATCH"
            return info, None, None
        strategies = doc.get("strategies") or {}
        if not isinstance(strategies, dict):
            raise ValueError("strategies must be an object")
        meta = strategies.get("chanlun")
        info["chanlun_batch_date"] = meta.get("batch_date") if isinstance(meta, dict) else None
        if meta is not None and (not isinstance(meta, dict)
                                 or not isinstance(meta.get("batch_date"), str)
                                 or _day(meta["batch_date"]) is None):
            info["status"] = "INVALID_ARCHIVE"
            info["error_type"] = "InvalidBatchDate"
            return info, None, None
        stocks = doc["stocks"]
        if not isinstance(stocks, list):
            raise ValueError("stocks must be a list")
        chan_stocks = {}
        for stock in stocks:
            if not isinstance(stock, dict):
                raise ValueError("stock must be an object")
            sources = stock.get("sources", [])
            if not isinstance(sources, list):
                raise ValueError("sources must be a list")
            chan = [src for src in sources
                    if isinstance(src, dict) and src.get("s") == "chanlun"]
            if not chan:
                continue
            code = stock.get("code")
            if not isinstance(code, str) or not code or code in chan_stocks:
                raise ValueError("invalid or duplicate chanlun code")
            if any(not isinstance(src.get("tag"), str) or not src.get("tag")
                   or _day(src.get("dt")) is None for src in chan):
                raise ValueError("invalid chanlun source tag/date")
            chan_stocks[code] = chan
        info["status"] = "MATCHED"
        return info, set(chan_stocks), chan_stocks
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        info["status"] = "UNREADABLE" if isinstance(exc, OSError) else "INVALID_ARCHIVE"
        info["error_type"] = type(exc).__name__
        return info, None, None


def _reconcile_chanlun(picked, archive, history, anchor, cn_health):
    """Compare source event identity, not picks tier or fused action/score."""
    if archive is None:
        return None
    current = defaultdict(list)
    for row in picked:
        code = row.get("code")
        if isinstance(code, str) and _market(code) == "CN":
            current[code].append(row)
    differences = {}
    for code in sorted(set(current) & set(archive)):
        left = Counter((str(r.get("type")), str(r.get("dt"))) for r in current[code])
        right = Counter((s["tag"], s["dt"]) for s in archive[code])
        if left != right:
            differences[code] = {
                "pool_only": [{"tag": t, "dt": d, "count": n}
                              for (t, d), n in sorted((left - right).items())],
                "archive_only": [{"tag": t, "dt": d, "count": n}
                                 for (t, d), n in sorted((right - left).items())],
            }
    batch = history.get("chanlun_batch_date")
    age = (anchor - _day(batch)).days if batch else None
    batch_status = ("UNPROVEN" if batch is None else
                    "MATCHED" if batch == cn_health["detect_date"] else "MISMATCH")
    return {
        "comparable": "source_tag_and_signal_day_only",
        "source_differences": differences,
        "source_difference_count": len(differences),
        "batch_status": batch_status,
        "archive_batch_age_calendar_days": age,
        "probe_batch_age_calendar_days": cn_health["age_calendar_days"],
        "freshness_clock": "archive_trade_basis_equals_observation_day",
        "tier_action_score": "NOT_COMPARABLE_WITH_FUSED_SIGNAL",
    }


def _input_manifest(source_files, history):
    """Fingerprint observations, not a transaction across live pool/archive files."""
    files = [{"path": item["path"], "status": item["status"],
              "sha256": item["sha256"]} for item in source_files]
    archive = {"path": history["path"], "status": history["status"],
               "sha256": history.get("sha256")}
    payload = {"pool_files": files, "archive": archive}
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False,
                                       sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"scope": "PROBE_OBSERVATIONS_NOT_GLOBAL_ATOMIC_OR_PRODUCTION",
            "sha256": digest, **payload}


def inspect(trade_date, pool_paths=None, history_path=None):
    """Read source files only. The returned verdict never authorizes a cutover."""
    if len(trade_date) != 8 or not trade_date.isdigit():
        raise ValueError("trade_date must be YYYYMMDD")
    anchor = datetime.strptime(trade_date, "%Y%m%d").date()
    adapter = ChanMonitorAdapter(pool_globs=pool_paths) if pool_paths else ChanMonitorAdapter()
    source_files, rows, origins, source_anomalies = _source_files(adapter)
    # Only the offline probe consumes this in-memory snapshot. Production adapter
    # behavior is unchanged; unreadable sources remain visible in source_files.
    if not rows:
        raise ValueError("source pool has no valid rows; fail closed")
    picked = adapter._pick_rows(rows, trade_date)
    if not picked:
        raise ValueError("no detectable batch at or before trade date")
    market_health = _batch_health(picked, anchor)
    invalid_source_dates = _invalid_source_dates(rows)
    if "CN" not in market_health:
        raise ValueError("no A-share batch date; fail closed")
    cn_health = market_health["CN"]
    if cn_health["detect_date"] is None:
        raise ValueError("invalid A-share batch date; fail closed")
    age_days = cn_health["age_calendar_days"]
    # Selection preserves the row objects from this probe's captured byte snapshot.
    # Production may read different bytes; no production-equivalence claim follows.
    selected = []
    selected_conversion_skips = []
    signals = []
    grouped = defaultdict(list)
    for index, row in enumerate(picked):
        sig = adapter._convert(row, trade_date)
        selected.append((index, row, sig))
        if sig is None:
            selected_conversion_skips.append({"source": origins[id(row)],
                                              "reason": "UNKNOWN_TYPE_OR_INVALID_CODE",
                                              "code": row.get("code"),
                                              "type": row.get("type"),
                                              "detect_date": row.get("detect_date")})
        if sig is not None:
            signals.append(sig)
            if sig.symbol.upper().endswith((".SH", ".SZ", ".BJ")):
                grouped[sig.symbol].append(sig)
    a_signals = [sig for values in grouped.values() for sig in values]
    duplicates = {symbol: len(v) for symbol, v in grouped.items() if len(v) > 1}
    contradictions = {symbol: len(v) for symbol, v in grouped.items()
                      if {s.action for s in v} >= {"buy", "sell"}}
    fuse = SignalAggregator()
    duplicate_evidence = {}
    fused_conflicts = {}
    for symbol in sorted(duplicates):
        relevant = [(index, row, sig) for index, row, sig in selected
                    if (sig.symbol if sig is not None else
                        row.get("code", "")) == symbol]
        events = Counter((str(row.get("type")), str(row.get("dt")),
                          str(row.get("detect_date"))) for _, row, _ in relevant)
        entries = [(adapter.strategy_id, 0.5, s) for s in grouped[symbol]]
        combined = fuse._fuse_symbol(trade_date, symbol, entries)
        detail = {
            "selected_row_count": len(relevant),
            "signal_count": len(entries),
            "event_identity": "type+dt+detect_date (not full-row equality)",
            "event_counts": [{"type": kind, "dt": dt, "detect_date": day,
                              "count": count}
                             for (kind, dt, day), count in sorted(events.items())],
            "rows": [{"selected_row_index": index, "source_code": row.get("code"),
                      "type": row.get("type"), "dt": row.get("dt"),
                      "detect_date": row.get("detect_date"),
                      "source": origins[id(row)],
                      "conversion": "SIGNAL" if sig is not None else "SKIPPED",
                      "skip_reason": (None if sig is not None else
                                      "UNKNOWN_TYPE_OR_INVALID_CODE"),
                      "action": sig.action if sig is not None else None,
                      "score": sig.score if sig is not None else None,
                      "rationale": sig.rationale if sig is not None else None}
                     for index, row, sig in relevant],
            "combined_action_observed": combined["action"],
            "contribution_keys": list(combined["contributions"]),
            "contribution_overwritten": len(combined["contributions"]) < len(entries),
            "source_file_attribution": "CAPTURED_FILE_AND_LINE",
            "file_hash_conversion_snapshot": "SAME_BYTES_IN_PROBE_ONLY",
        }
        duplicate_evidence[symbol] = detail
        if symbol in contradictions:
            fused_conflicts[symbol] = {
                "actions": [s.action for s in grouped[symbol]],
                "combined_action": combined["action"],
                "divergence": combined["divergence"],
                "contribution_keys": detail["contribution_keys"],
                "signal_count": len(entries),
                "evidence": [{"action": s.action, "score": s.score,
                              "rationale": s.rationale} for s in grouped[symbol]],
                "contribution_overwritten": detail["contribution_overwritten"],
            }
    history, picks_codes, archive = _history_evidence(history_path, anchor)
    input_manifest = _input_manifest(source_files, history)
    reconcile = _reconcile_chanlun(picked, archive, history, anchor, cn_health)
    issues = []
    if reconcile is not None:
        if reconcile["batch_status"] == "MISMATCH":
            issues.append("ARCHIVE_CHAN_BATCH_MISMATCH")
        elif reconcile["batch_status"] == "UNPROVEN":
            issues.append("ARCHIVE_CHAN_BATCH_UNPROVEN")
        if reconcile["source_difference_count"]:
            issues.append("CHAN_SOURCE_EVENT_MISMATCH")
    if history["status"] == "DATE_MISMATCH":
        issues.append("HISTORY_DATE_MISMATCH")
    elif history["status"] in ("UNREADABLE", "INVALID_ARCHIVE"):
        issues.append("HISTORY_UNAVAILABLE")
    elif history["status"] == "NOT_REQUESTED":
        issues.append("HISTORY_NOT_REQUESTED")
    if age_days > 4:
        issues.append("STALE_CN_BATCH")
    if any(h["status"] == "STALE" for h in market_health.values() if h is not cn_health):
        issues.append("STALE_NON_CN_BATCH")
    if any(h["status"] in ("INVALID_BATCH_DATE", "FUTURE")
           for h in market_health.values()):
        issues.append("INVALID_MARKET_BATCH")
    if invalid_source_dates:
        issues.append("INVALID_SOURCE_DATE")
    if any(item["status"] == "MISSING" for item in source_files):
        issues.append("SOURCE_FILE_MISSING")
    if any(item["status"] in ("UNREADABLE", "EMPTY", "NO_OBJECT_ROWS")
           for item in source_files):
        issues.append("SOURCE_FILE_UNUSABLE")
    if any(item["status"] == "MALFORMED_ROWS" for item in source_files):
        issues.append("SOURCE_MALFORMED_ROWS")
    if duplicates:
        issues.append("DUPLICATE_STRATEGY_SYMBOL")
    if contradictions:
        issues.append("BUY_SELL_CONTRADICTION")
    if picks_codes is not None and picks_codes != set(grouped):
        issues.append("PICKS_SET_MISMATCH")
    # A4 cannot be cut over merely because these checks pass: the other five
    # adapters, freshness gates and PG write acknowledgement remain unproven.
    issues += ["SIX_STRATEGY_CONTRACT_UNPROVEN", "PG_PERSISTENCE_UNPROVEN"]
    return {
        "trade_date": trade_date,
        "cn_detect_date": cn_health["detect_date"],
        "cn_age_calendar_days": age_days,
        "market_health": market_health,
        "source_files": source_files,
        "source_anomalies": source_anomalies,
        "selected_conversion_skips": selected_conversion_skips,
        "input_snapshot_scope": "PROBE_ONLY_NOT_PRODUCTION",
        "input_manifest": input_manifest,
        "history": history,
        "chanlun_reconciliation": reconcile,
        "invalid_source_dates": invalid_source_dates,
        "signals_all": len(signals),
        "signals_a": len(a_signals),
        "unique_a": len(grouped),
        "duplicate_symbols": duplicates,
        "duplicate_evidence": duplicate_evidence,
        "contradictory_symbols": fused_conflicts,
        "actions_a": dict(Counter(s.action for s in a_signals)),
        "historic_picks_symbols": len(picks_codes) if picks_codes is not None else None,
        "picks_only": sorted(picks_codes - set(grouped)) if picks_codes is not None else None,
        "adapter_only": sorted(set(grouped) - picks_codes) if picks_codes is not None else None,
        "verdict": "BLOCK_CUTOVER",
        "issues": issues,
    }


def self_test():
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as tmp:
        pool = Path(tmp) / "pool.jsonl"
        pool.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in [
            {"code": "000001.SZ", "type": "1买", "detect_date": "2026-10-01", "dt": "2026-09-30"},
            {"code": "000001.SZ", "type": "3卖", "detect_date": "2026-10-01", "dt": "2026-09-29"},
        ]), encoding="utf-8")
        result = inspect("20261010", [str(pool)])
        assert result["verdict"] == "BLOCK_CUTOVER"
        assert "STALE_CN_BATCH" in result["issues"]
        assert "BUY_SELL_CONTRADICTION" in result["issues"]
        assert result["contradictory_symbols"]["000001.SZ"]["divergence"]
        assert result["contradictory_symbols"]["000001.SZ"]["contribution_keys"] == ["chan_monitor"]
        assert result["duplicate_symbols"] == {"000001.SZ": 2}
        assert result["contradictory_symbols"]["000001.SZ"]["contribution_overwritten"]
        assert len(result["contradictory_symbols"]["000001.SZ"]["evidence"]) == 2
        assert result["market_health"]["CN"]["status"] == "STALE"
        fresh = inspect("20261002", [str(pool)])
        assert "STALE_CN_BATCH" not in fresh["issues"]
        history = Path(tmp) / "history.json"
        history.write_text(json.dumps({"scan_run_date": "2026-10-02",
            "trade_basis": "2026-10-02 收盘",
            "strategies": {"chanlun": {"batch_date": "2026-10-01"}}, "stocks": [
            {"code": "000002.SZ", "sources": [{"s": "chanlun", "tag": "1买",
                                                   "dt": "2026-09-30"}]}
        ]}), encoding="utf-8")
        mismatch = inspect("20261002", [str(pool)], str(history))
        assert "PICKS_SET_MISMATCH" in mismatch["issues"]
        assert mismatch["picks_only"] == ["000002.SZ"]
        assert mismatch["adapter_only"] == ["000001.SZ"]
        assert "SIX_STRATEGY_CONTRACT_UNPROVEN" in fresh["issues"]
        assert "PG_PERSISTENCE_UNPROVEN" in fresh["issues"]
        assert fresh["market_health"]["CN"]["status"] == "FRESH"
        with pool.open("a", encoding="utf-8") as f:
            f.write("\n" + json.dumps({"code": "AAPL.US", "type": "1买",
                "detect_date": "2026-09-20", "dt": "2026-09-19"}, ensure_ascii=False))
        mixed = inspect("20261002", [str(pool)])
        assert "STALE_NON_CN_BATCH" in mixed["issues"]
        assert mixed["market_health"]["US"]["status"] == "STALE"
        with pool.open("a", encoding="utf-8") as f:
            f.write("\n" + json.dumps({"code": "00700.HK", "type": "1买",
                "detect_date": "2026-01-XX", "dt": "2026-09-19"}, ensure_ascii=False))
        invalid = inspect("20261002", [str(pool)])
        assert "INVALID_SOURCE_DATE" in invalid["issues"]
        assert "INVALID_MARKET_BATCH" in invalid["issues"]
        assert invalid["invalid_source_dates"]["HK"] == 1
        assert invalid["market_health"]["HK"]["status"] == "INVALID_BATCH_DATE"
        with pool.open("a", encoding="utf-8") as f:
            f.write("\n" + json.dumps({"code": "000001.SZ", "type": "1买",
                "detect_date": "2026-10-XX", "dt": "2026-09-19"}, ensure_ascii=False))
        skipped = inspect("20261002", [str(pool)])
        assert skipped["invalid_source_dates"]["CN"] == 1
        assert skipped["market_health"]["CN"]["status"] == "FRESH"
        assert "INVALID_SOURCE_DATE" in skipped["issues"]
        missing_market = Path(tmp) / "missing_market.jsonl"
        missing_market.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in [
            {"code": "000001.SZ", "type": "1买", "detect_date": "2026-10-01", "dt": "2026-09-30"},
            {"code": "00700.HK", "type": "1买", "detect_date": "2026-10-XX", "dt": "2026-09-30"},
        ]), encoding="utf-8")
        missing = inspect("20261002", [str(missing_market)])
        assert "HK" not in missing["market_health"]
        assert missing["invalid_source_dates"] == {"HK": 1}
        assert "INVALID_SOURCE_DATE" in missing["issues"]
        assert missing["verdict"] == "BLOCK_CUTOVER"
        print("SELF_TEST_PASS: stale, conflict, overwrite, picks mismatch and skipped invalid source dates")


def main():
    parser = argparse.ArgumentParser(description="A4 offline read-only cutover blocker probe")
    parser.add_argument("--trade-date", default="20261019")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--history", default="")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    # Use a same-day archive if it exists; never compare against a fixed old day.
    hist = args.history or str(PORTAL_HISTORY / f"todays_{args.trade_date}.json")
    print(json.dumps(inspect(args.trade_date, history_path=hist), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
