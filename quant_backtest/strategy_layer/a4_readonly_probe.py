# -*- coding: utf-8 -*-
"""A4 read-only contract probe. No DB writes, portal writes or scheduler actions.

Usage: python -m strategy_layer.a4_readonly_probe [--trade-date YYYYMMDD]
       python -m strategy_layer.a4_readonly_probe --self-test
The probe deliberately does not call SignalAggregator.run (which persists to PG).
"""
import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .adapters.chan_monitor import ChanMonitorAdapter
from .aggregator import SignalAggregator

ROOT = Path(__file__).resolve().parents[1]
PORTAL_HISTORY = ROOT.parent / "portal" / "data" / "picks_history"


def _day(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def inspect(trade_date, pool_paths=None, history_path=None):
    """Read source files only. The returned verdict never authorizes a cutover."""
    if len(trade_date) != 8 or not trade_date.isdigit():
        raise ValueError("trade_date must be YYYYMMDD")
    anchor = datetime.strptime(trade_date, "%Y%m%d").date()
    adapter = ChanMonitorAdapter(pool_globs=pool_paths) if pool_paths else ChanMonitorAdapter()
    rows = adapter._load_rows()
    if not rows:
        raise ValueError("source pool has no valid rows; fail closed")
    picked = adapter._pick_rows(rows, trade_date)
    if not picked:
        raise ValueError("no detectable batch at or before trade date")
    latest_cn = max((_day(r.get("detect_date")) for r in picked
                     if str(r.get("code", "")).upper().endswith((".SH", ".SZ", ".BJ"))
                    if _day(r.get("detect_date"))), default=None)
    if latest_cn is None:
        raise ValueError("no A-share batch date; fail closed")
    age_days = (anchor - latest_cn).days
    signals = [s for r in picked if (s := adapter._convert(r, trade_date)) is not None]
    a_signals = [s for s in signals if s.symbol.upper().endswith((".SH", ".SZ", ".BJ"))]
    grouped = defaultdict(list)
    for sig in a_signals:
        grouped[sig.symbol].append(sig)
    duplicates = {symbol: len(v) for symbol, v in grouped.items() if len(v) > 1}
    contradictions = {symbol: len(v) for symbol, v in grouped.items()
                      if {s.action for s in v} >= {"buy", "sell"}}
    fuse = SignalAggregator()
    fused_conflicts = {}
    for symbol in contradictions:
        entries = [(adapter.strategy_id, 0.5, s) for s in grouped[symbol]]
        combined = fuse._fuse_symbol(trade_date, symbol, entries)
        fused_conflicts[symbol] = {
            "actions": [s.action for s in grouped[symbol]],
            "combined_action": combined["action"],
            "divergence": combined["divergence"],
            "contribution_keys": list(combined["contributions"]),
            "signal_count": len(entries),
        }
    picks_codes = None
    if history_path:
        with open(history_path, encoding="utf-8") as f:
            doc = json.load(f)
        picks_codes = {s["code"] for s in doc["stocks"]
                       if any(x.get("s") == "chanlun" for x in s.get("sources", []))}
    issues = []
    if age_days > 4:
        issues.append("STALE_CN_BATCH")
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
        "cn_detect_date": latest_cn.isoformat(),
        "cn_age_calendar_days": age_days,
        "signals_all": len(signals),
        "signals_a": len(a_signals),
        "unique_a": len(grouped),
        "duplicate_symbols": duplicates,
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
        fresh = inspect("20261002", [str(pool)])
        assert "STALE_CN_BATCH" not in fresh["issues"]
        history = Path(tmp) / "history.json"
        history.write_text(json.dumps({"stocks": [
            {"code": "000002.SZ", "sources": [{"s": "chanlun"}]}
        ]}), encoding="utf-8")
        mismatch = inspect("20261002", [str(pool)], str(history))
        assert "PICKS_SET_MISMATCH" in mismatch["issues"]
        assert mismatch["picks_only"] == ["000002.SZ"]
        assert mismatch["adapter_only"] == ["000001.SZ"]
        assert "SIX_STRATEGY_CONTRACT_UNPROVEN" in fresh["issues"]
        assert "PG_PERSISTENCE_UNPROVEN" in fresh["issues"]
        print("SELF_TEST_PASS: stale, conflict, duplicate, contribution overwrite, freshness and picks mismatch")


def main():
    parser = argparse.ArgumentParser(description="A4 offline read-only cutover blocker probe")
    parser.add_argument("--trade-date", default="20261019")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--history", default="")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    hist = args.history or str(PORTAL_HISTORY / "todays_20261016.json")
    print(json.dumps(inspect(args.trade_date, history_path=hist), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
