# -*- coding: utf-8 -*-
"""全量选股 Phase A 只读基线（WO-STRAT-FULL-BASE-20261102）。

目的：先回答“全量域里，每张可用策略卡到底覆盖多少股票/多少买点候选”，
而不是继续从 25/30 张复核卡倒推。

只读：
  · 读 zhice-db v2_bar_daily（data_loader，只读）；
  · 读策略卡/适配器，不修改 strategy_registry.json、不启用生产策略；
  · 输出 JSON/Markdown 到 work/strategy_baseline/，不写交易账。

当前可跑的 4 张经典策略：
  ma_cross / rsi_reversal / boll_breakout / trend_follow
  —— ClassicStrategyAdapter，全量 symbols，as-of trade_date。
  chan_monitor：读缠论信号池（不是全市场重算，单独标注）；
  kg_boost：读图谱热度（不是全市场重算，单独标注）；
  其余卡片无 adapter，Phase A 只登记“不可跑”。

用法：
  python -X utf8 tools/full_universe_baseline.py --date 20261030 --limit 100
  python -X utf8 tools/full_universe_baseline.py --date 20261030
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[1]
QB = REPO / "quant_backtest"
if str(QB) not in sys.path:
    sys.path.insert(0, str(QB))

SCHEMA = "full_universe_baseline/v1"
CLASSICS = ("ma_cross", "rsi_reversal", "boll_breakout", "trend_follow")


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ymd8(d):
    s = str(d or "").replace("-", "").replace("/", "").strip()
    return s if len(s) == 8 and s.isdigit() else ""


def _dash(ymd8):
    d = _ymd8(ymd8)
    if not d:
        raise ValueError("date 需 YYYYMMDD")
    return "%s-%s-%s" % (d[:4], d[4:6], d[6:])


def load_universe(limit=0):
    """CN A 股 universe：6位 + .SH/.SZ，剔 BJ/指数/外股。"""
    import data_loader
    from strategy_layer.liudu_universe import is_ashare_symbol
    syms = [str(s) for s in (data_loader.available_symbols("CN") or []) if is_ashare_symbol(s)]
    syms = sorted(set(syms))
    if limit:
        syms = syms[: int(limit)]
    return syms


def load_frames(symbols, trade_date):
    """一次性加载 as-of 行情，供四张经典策略共用（避免重复查询）。"""
    import data_loader
    return data_loader.load_bars("CN", list(symbols), None, _dash(trade_date))


def summarize_signals(signals):
    """Signal 列表 → 计数摘要（纯函数，测试用）。"""
    actions = Counter()
    symbols = set()
    for s in signals:
        a = str(getattr(s, "action", "") or "")
        actions[a] += 1
        sym = str(getattr(s, "symbol", "") or "")
        if sym:
            symbols.add(sym)
    return {
        "signal_count": len(signals),
        "symbol_count": len(symbols),
        "actions": {k: actions[k] for k in sorted(actions)},
        "buy_count": actions.get("buy", 0),
        "sell_count": actions.get("sell", 0),
        "watch_count": actions.get("watch", 0),
        "hold_count": actions.get("hold", 0),
    }


def run_classic(name, symbols, frames, trade_date):
    from strategy_layer.onboarding.classic_adapter import ClassicStrategyAdapter
    t0 = time.time()
    try:
        ad = ClassicStrategyAdapter(
            name, symbols, market="CN",
            loader=lambda market, syms, start, end: frames,
        )
        sigs = ad.score(trade_date)
        out = summarize_signals(sigs)
        out.update({"strategy_id": name, "status": "ok",
                    "runnable": True, "elapsed_s": round(time.time() - t0, 2),
                    "signals": [
                        {"symbol": s.symbol, "action": s.action, "score": s.score,
                         "confidence": s.confidence, "rationale": s.rationale[:180]}
                        for s in sigs
                    ]})
        return out
    except Exception as exc:  # noqa: BLE001
        return {"strategy_id": name, "status": "error", "runnable": True,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200]),
                "elapsed_s": round(time.time() - t0, 2), **summarize_signals([])}


def run_pool_strategy(name, trade_date):
    """chan_monitor / kg_boost：读已有产出，不是全市场重算。"""
    t0 = time.time()
    try:
        if name == "chan_monitor":
            from strategy_layer.adapters.chan_monitor import ChanMonitorAdapter
            sigs = ChanMonitorAdapter().score(trade_date)
        elif name == "kg_boost":
            from strategy_layer.adapters.kg_boost import KgBoostAdapter
            sigs = KgBoostAdapter().score(trade_date)
        else:
            raise ValueError("unknown pool strategy")
        out = summarize_signals(sigs)
        out.update({"strategy_id": name, "status": "ok", "runnable": True,
                    "scope": "pool_feed_not_full_recompute",
                    "elapsed_s": round(time.time() - t0, 2),
                    "signals": [
                        {"symbol": s.symbol, "action": s.action, "score": s.score,
                         "confidence": s.confidence, "rationale": s.rationale[:180]}
                        for s in sigs
                    ]})
        return out
    except Exception as exc:  # noqa: BLE001
        return {"strategy_id": name, "status": "error", "runnable": True,
                "scope": "pool_feed_not_full_recompute",
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200]),
                "elapsed_s": round(time.time() - t0, 2), **summarize_signals([])}


def inventory():
    """10 张卡的可跑状态（只读登记）。"""
    import json as _json
    p = QB / "strategy_layer" / "strategy_cards.json"
    try:
        doc = _json.loads(p.read_text(encoding="utf-8"))
        cards = doc.get("cards") or []
    except (OSError, ValueError):
        cards = []
    runnable = set(CLASSICS) | {"chan_monitor", "kg_boost"}
    out = []
    for c in cards:
        sid = str(c.get("strategy_id") or "")
        if sid in CLASSICS:
            scope = "full_universe_recompute"
        elif sid in ("chan_monitor", "kg_boost"):
            scope = "pool_feed_not_full_recompute"
        else:
            scope = "no_adapter_yet"
        out.append({"strategy_id": sid, "runnable": sid in runnable,
                    "scope": scope,
                    "universe": c.get("universe"),
                    "frequency": c.get("frequency")})
    return out


def build(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="20261030", help="YYYYMMDD，默认最近完整日")
    ap.add_argument("--limit", type=int, default=0, help="0=全量；>0 仅样本")
    ap.add_argument("--out-dir", default=str(REPO / "work" / "strategy_baseline"))
    ap.add_argument("--skip-frames", action="store_true",
                    help="只跑信号池策略，不加载全量行情")
    args = ap.parse_args(argv)
    date = _ymd8(args.date)
    if not date:
        raise SystemExit("--date 需 YYYYMMDD")
    symbols = load_universe(args.limit)
    result = {
        "schema": SCHEMA,
        "trade_date": date,
        "generated_at": _now(),
        "readonly": True,
        "paper_only": True,
        "buy_point_evaluated": False,
        "universe": {
            "market": "CN",
            "count": len(symbols),
            "limited": bool(args.limit),
            "symbols_ref": "data_loader.available_symbols('CN') + is_ashare_symbol",
        },
        "strategies": [],
        "inventory": inventory(),
        "notes": [
            "Phase A 只统计信号覆盖；buy_point 尚未评估（Phase B 落 Signal v2）。",
            "classic 4 张为全量重算；chan_monitor/kg_boost 为信号池/图谱产出，非全市场重算。",
            "未启用生产策略、未写 strategy_registry、未写交易账。",
        ],
    }
    frames = None
    if not args.skip_frames:
        t0 = time.time()
        frames = load_frames(symbols, date)
        result["frames"] = {"symbols": len(frames or {}),
                            "elapsed_s": round(time.time() - t0, 2)}
    for name in CLASSICS:
        if frames is None:
            result["strategies"].append({"strategy_id": name, "status": "skipped",
                                         "runnable": True,
                                         "error": "skip_frames"})
        else:
            result["strategies"].append(run_classic(name, symbols, frames, date))
    for name in ("chan_monitor", "kg_boost"):
        result["strategies"].append(run_pool_strategy(name, date))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    jpath = out_dir / ("full_universe_baseline_%s.json" % date)
    mpath = out_dir / ("full_universe_baseline_%s.md" % date)
    jpath.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
    mpath.write_text(render_markdown(result), encoding="utf-8")
    print(str(jpath))
    print(str(mpath))
    print(json.dumps({s["strategy_id"]: {k: s.get(k) for k in
                                        ("status", "signal_count", "buy_count",
                                         "symbol_count", "error")}
                      for s in result["strategies"]},
                     ensure_ascii=False, indent=1))
    return result


def render_markdown(result):
    lines = [
        "# 全量选股 Phase A 基线 %s" % result.get("trade_date"),
        "",
        "> 只读研究；buy_point 尚未评估；未启用生产策略、未写交易账。",
        "",
        "## Universe",
        "",
        "```text",
        "market=%s count=%s limited=%s" % (
            result["universe"].get("market"), result["universe"].get("count"),
            result["universe"].get("limited")),
        "```",
        "",
        "## 策略覆盖",
        "",
        "| 策略 | 状态 | 信号 | buy | sell | watch | 覆盖股票 | 备注 |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for s in result.get("strategies") or []:
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            s.get("strategy_id", ""), s.get("status", ""),
            s.get("signal_count", 0), s.get("buy_count", 0),
            s.get("sell_count", 0), s.get("watch_count", 0),
            s.get("symbol_count", 0),
            s.get("error") or s.get("scope") or ""))
    lines += ["", "## 10 张卡可跑性", "",
              "| 策略 | 可跑 | scope |", "|---|---|---|"]
    for c in result.get("inventory") or []:
        lines.append("| %s | %s | %s |" % (
            c.get("strategy_id"), c.get("runnable"), c.get("scope")))
    lines += ["", "## 说明", ""]
    for n in result.get("notes") or []:
        lines.append("- " + n)
    return "\n".join(lines) + "\n"


def main(argv=None):
    return 0 if build(sys.argv[1:] if argv is None else argv) else 1


if __name__ == "__main__":
    raise SystemExit(main())
