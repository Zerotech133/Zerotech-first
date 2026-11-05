# -*- coding: utf-8 -*-
"""acad1_v2_export.py — 从张松仓（zhice v2）只读导出 ACAD1 30 卡桥接 JSON。

目的：DATA1 生成层修好之前，用只读数据仓事实先产出“可被生成门审计”的 30 卡：
  · 分数按 market 百分位归一（不是 raw max_score）
  · 带 provenance：bar_source/bar_asof/price_adjust/rsi_method/complete_bar
  · 带行情事实：close/pre_close/MA20/RSI14/量比/20日高低/日收益
  · 三买中枢从上游 reason 解析
  · trigger_ts/trigger_close 仅从 v2_strategy_daily 取；没有就 null，不猜

边界：
  · 只读 DB（SET TRANSACTION READ ONLY），不写库/不改外部代码；
  · 输出标 bridge_export=true，不是 DATA1 正式产物；
  · 不代签 reviewed/handoff、不产生交易指令。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from quant_backtest.strategy_layer import bar_fact_contract as bfc  # noqa: E402

SCHEMA = "acad1_v2_export/v1"
CENTER_RE = re.compile(r"中枢\s*\[([0-9.+-]+)\s*,\s*([0-9.+-]+)\]")
CLOSE_RE = re.compile(r"close=([0-9.+-]+)")
RANK_CACHE = {}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def percentile_rank(values):
    """0~1 百分位；相同值平均秩。空列表返回 []。"""
    n = len(values)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: (values[i], i))
    out = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0
        pct = 0.0 if n == 1 else avg / (n - 1)
        for k in range(i, j + 1):
            out[order[k]] = pct
        i = j + 1
    return out


def parse_center(text):
    m = CENTER_RE.search(text or "")
    if not m:
        return None, None
    return _f(m.group(1)), _f(m.group(2))


def parse_signal_type(signal, reason):
    text = "%s %s" % (signal or "", reason or "")
    if "缠论三买" in text or "三买" in text or "3买" in text:
        return "3买"
    if "均值回归" in text:
        return "均值回归"
    if "趋势动量" in text:
        return "趋势动量"
    if "RSI" in (signal or ""):
        return "RSI反转"
    return (signal or "").strip() or "unknown"


def bar_symbol_aliases(code):
    """把 sh688004/sz000001/bj920001 归一成 688004.SH 等行情代码。"""
    m = re.match(r"^(sh|sz|bj)(\d{6})$", str(code or ""), re.I)
    if m:
        return ["%s.%s" % (m.group(2), m.group(1).upper())]
    return [str(code or "")]


def _market_rows(rows):
    by = {}
    for r in rows:
        by.setdefault(r.get("market") or "CN", []).append(r)
    return by


def build_cards(pool_rows, selection_by_key, bars_by_key, daily_by_key, pool_date, picks_by_key=None):
    """纯函数：由查询结果构建卡片（测试可注入 synthetic rows）。"""
    picks_by_key = picks_by_key or {}
    cards = []
    by_market = _market_rows(pool_rows)
    market_pct = {}
    for market, items in by_market.items():
        vals = [_f((selection_by_key.get((market, x.get("code"))) or {}).get("max_score"))
                or _f(x.get("score")) or 0.0 for x in items]
        market_pct[market] = percentile_rank(vals)
    for market, items in by_market.items():
        for idx, row in enumerate(items):
            code = row.get("code")
            sel = selection_by_key.get((market, code)) or {}
            bars = list(bars_by_key.get((market, code)) or [])
            lows = [_f(b.get("low")) for b in bars]
            lows = [v for v in lows if v is not None]
            volumes = [_f(b.get("volume")) for b in bars]
            closes = [_f(b.get("close")) for b in bars]
            last = bars[-1] if bars else {}
            prev = bars[-2] if len(bars) >= 2 else {}
            close = _f(last.get("close"))
            pre_close = _f(last.get("pre_close"))
            fallback_prev = _f(prev.get("close"))
            vr_fact = bfc.volume_ratio_fact(volumes, complete_bar=True) if volumes else {"status": "unknown"}
            raw_vr = None
            if len(volumes) >= 21:
                prior = [float(v) for v in volumes[-21:-1] if v is not None]
                if prior and sum(prior) > 0 and volumes[-1] is not None:
                    raw_vr = float(volumes[-1]) / (sum(prior) / len(prior))
            # 半根：保留 raw 量比 + complete_bar=false，让门显式 fail/unknown
            complete_bar = raw_vr is not None and raw_vr >= 0.01
            volume_ratio = raw_vr if raw_vr is not None else vr_fact.get("volume_ratio")
            pick = picks_by_key.get((market, code)) or {}
            reason = ((sel.get("reason") or "") + " " + (row.get("selection_reason") or "")
                      + " " + (pick.get("reason") or ""))
            lo, hi = parse_center(reason)
            raw_score = _f(sel.get("max_score")) or _f(row.get("score"))
            pct = market_pct[market][idx] if market in market_pct else None
            resonance = len([x for x in (sel.get("source_strategies") or "").split(",") if x.strip()])
            dq = 1.0 if bars else 0.0
            total = (0.40 * (pct if pct is not None else 0.0)
                     + 0.25 * min(1.0, resonance / 4.0)
                     + 0.15 * dq + 0.10 * 0.5)
            daily = daily_by_key.get((market, code)) or {}
            trig_ts = daily.get("trade_date")
            trig_source = "v2_strategy_daily" if trig_ts else None
            pick = picks_by_key.get((market, code)) or {}
            if not trig_ts and pick.get("report_date"):
                trig_ts = pick.get("report_date")
                trig_source = "v2_selection_pick.report_date"
            trig_close = None
            m = CLOSE_RE.search(daily.get("reason") or "")
            if m:
                trig_close = _f(m.group(1))
            asof = str(last.get("trade_date"))[:10] if last.get("trade_date") else str(row.get("data_date") or pool_date)
            strategy_timeframe = "5m" if "5分钟" in (row.get("selection_reason") or "") or str(row.get("strategy_id") or "").startswith("v1-crypto-strat-11") else "1d"
            card = {
                "candidate_id": code,
                "name": row.get("name") or "",
                "market": market,
                "strategy": row.get("strategy_id") or sel.get("source_strategies") or "",
                "signal_type": parse_signal_type(row.get("signal"), reason),
                "pick_reason": pick.get("reason"),
                "score": round(total * 100.0, 4),
                "score_method": "bridge_percentile_v1",
                "raw_score": raw_score,
                "pct_rank": round(pct, 4) if pct is not None else None,
                "resonance": resonance,
                "center_low": lo,
                "center_high": hi,
                "close": close,
                "pre_close": pre_close,
                "prev_close_src": "pre_close" if pre_close else ("prev_close" if fallback_prev else None),
                "daily_return": bfc.compute_return(close, pre_close, fallback_prev),
                "asof": asof,
                "bar_asof": asof,
                "bar_source": "zhice_v2:v2_bar_daily",
                "price_adjust": "none",
                "rsi_method": "sma14",
                "evidence_rsi_method": "sma14",
                "complete_bar": complete_bar,
                "volume_ratio": volume_ratio,
                "rsi14": bfc.compute_rsi(closes, 14, "sma14") if closes else None,
                "rsi14_wilder": bfc.compute_rsi(closes, 14, "wilder") if closes else None,
                "ma20": bfc.compute_ma(closes, 20) if closes else None,
                "ret20": ((close / float(closes[-21]) - 1.0) * 100.0) if close and len(closes) >= 21 and closes[-21] else None,
                "dist_20d_low": ((close / min(lows[-20:]) - 1.0) * 100.0) if close and lows else None,
                "trigger_ts": str(trig_ts)[:10] if trig_ts else None,
                "trigger_ts_source": trig_source,
                "trigger_close": trig_close,
                "strategy_timeframe": strategy_timeframe,
                "evidence_timeframe": "1d",
                "card_status": "draft",
                "bridge_export": True,
                "bridge_note": "只读桥接导出；不是 DATA1 正式卡",
            }
            cards.append(card)
    return cards


def _connect(dsn):
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except ImportError as exc:
        raise RuntimeError("psycopg2 不可用: %s" % exc)
    url = dsn.replace("postgresql+psycopg2://", "postgresql://")
    conn = psycopg2.connect(url, connect_timeout=10)
    conn.set_session(readonly=True)
    return conn, RealDictCursor


def fetch(dsn, pool_date):
    conn, cur_factory = _connect(dsn)
    try:
        with conn.cursor(cursor_factory=cur_factory) as c:
            c.execute("SET TRANSACTION READ ONLY")
            c.execute("SELECT max(pool_date) FROM v2_acad1_pool")
            latest = c.fetchone()["max"]
            day = pool_date or str(latest)
            c.execute("""SELECT code,name,market,strategy_id,signal,score,selection_reason,data_date
                         FROM v2_acad1_pool WHERE pool_date=%s ORDER BY market,code""", (day,))
            pool_rows = [dict(r) for r in c.fetchall()]
            if not pool_rows:
                raise RuntimeError("no v2_acad1_pool rows for %s" % day)
            c.execute("""SELECT market,code,max_score,source_strategies,signal_types
                         FROM v2_selection_pool WHERE pool_date=%s""", (day,))
            selection = {(r["market"], r["code"]): dict(r) for r in c.fetchall()}
            bars_by_key = {}
            for r in pool_rows:
                found = []
                for symbol in bar_symbol_aliases(r["code"]):
                    c.execute("""SELECT trade_date,high,low,close,volume,pre_close
                                 FROM v2_bar_daily WHERE market=%s AND symbol=%s AND trade_date<=%s
                                 ORDER BY trade_date DESC LIMIT 30""", (r["market"], symbol, day))
                    found = list(reversed([dict(x) for x in c.fetchall()]))
                    if found:
                        break
                bars_by_key[(r["market"], r["code"])] = found
            picks_by_key = {}
            c.execute("""SELECT DISTINCT ON (market,code) market,code,report_date,reason
                         FROM v2_selection_pick WHERE report_date<=%s
                         ORDER BY market,code,report_date DESC""", (day,))
            for x in c.fetchall():
                picks_by_key[(x["market"], x["code"])] = dict(x)
            daily_by_key = {}
            for r in pool_rows:
                c.execute("""SELECT trade_date,reason FROM v2_strategy_daily
                             WHERE strategy_id=%s AND market=%s AND code=%s AND data_date<=%s
                             ORDER BY data_date DESC LIMIT 1""",
                          (r.get("strategy_id"), r["market"], r["code"], day))
                row = c.fetchone()
                if row:
                    daily_by_key[(r["market"], r["code"])] = dict(row)
            return day, pool_rows, selection, bars_by_key, daily_by_key, picks_by_key
    finally:
        conn.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description="ACAD1 zhice v2 只读桥接导出")
    ap.add_argument("--pool-date", default=None)
    ap.add_argument("--out", default="")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dsn-env", default="ZHICE_V2_DSN")
    args = ap.parse_args(argv)
    dsn = os.environ.get(args.dsn_env)
    if not dsn:
        print(json.dumps({"ok": False, "error": "missing_env:%s" % args.dsn_env}, ensure_ascii=False))
        return 2
    try:
        day, pool_rows, selection, bars, daily, picks = fetch(dsn, args.pool_date)
        cards = build_cards(pool_rows, selection, bars, daily, day, picks)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": "db_error: %s" % str(exc)[:300]}, ensure_ascii=False))
        return 3
    payload = {
        "schema": SCHEMA,
        "pool_date": day,
        "bridge_export": True,
        "source": "zhice_v2 read-only",
        "note": "只读桥接导出；不是 DATA1 正式卡；分数为 bridge_percentile_v1；不代签",
        "cards": cards,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text + "\n", encoding="utf-8")
        print("wrote", args.out, "cards", len(cards))
    if args.json or not args.out:
        print(text)
    return 0 if len(cards) == 30 else 1


if __name__ == "__main__":
    raise SystemExit(main())
