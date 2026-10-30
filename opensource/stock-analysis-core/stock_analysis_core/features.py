# -*- coding: utf-8 -*-
"""特征计算：技术面 / 估值 / 财务（纯 Python，无第三方依赖）。"""
from typing import Any, Dict, List

from .types import Bar, Snapshot


def _pct_rank(values: List[float], x: float) -> float:
    clean = [v for v in values if v is not None and v == v]
    if not clean:
        return 50.0
    n = sum(1 for v in clean if v <= x)
    return round(n / len(clean) * 100, 1)


def _ret(closes: List[float], n: int) -> float | None:
    if len(closes) <= n or closes[-1 - n] == 0:
        return None
    return round(closes[-1] / closes[-1 - n] - 1.0, 4)


def technical(bars: List[Bar]) -> Dict[str, Any]:
    closes = [b.close for b in bars]
    out: Dict[str, Any] = {"bars": len(bars)}
    if not closes:
        return out
    out.update({
        "last_close": round(closes[-1], 2),
        "last_date": bars[-1].date,
        "ret_20": _ret(closes, 20),
        "ret_60": _ret(closes, 60),
        "ret_250": _ret(closes, 250),
        "ma20": round(sum(closes[-20:]) / min(20, len(closes)), 2) if closes else None,
        "ma60": round(sum(closes[-60:]) / min(60, len(closes)), 2) if closes else None,
        "high_250": round(max(closes[-250:]), 2),
        "low_250": round(min(closes[-250:]), 2),
        "pct_rank_250": _pct_rank(closes[-250:], closes[-1]),
    })
    out["above_ma20"] = bool(out["ma20"] and closes[-1] >= out["ma20"])
    return out


def valuation(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"days": len(rows)}
    if not rows:
        return out
    ps = [float(r["ps"]) for r in rows if r.get("ps") not in (None, "")]
    pe = [float(r["pe"]) for r in rows if r.get("pe") not in (None, "")]
    pb = [float(r["pb"]) for r in rows if r.get("pb") not in (None, "")]
    out.update({
        "last_date": str(rows[-1].get("trade_date", "")),
        "ps": round(ps[-1], 2) if ps else None,
        "pe": round(pe[-1], 2) if pe else None,
        "pb": round(pb[-1], 2) if pb else None,
        "ps_pct_hist": _pct_rank(ps, ps[-1]) if ps else None,
        "hist_days": len(ps),
    })
    return out


def fundamental(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"periods": len(rows)}
    if not rows:
        return out
    last = rows[-1]
    out.update({
        "end_date": str(last.get("end_date", "")),
        "revenue": float(last.get("revenue", 0) or 0),
        "net_profit": float(last.get("net_profit", 0) or 0),
        "gross_margin": float(last.get("gross_margin", 0) or 0),
        "debt_ratio": float(last.get("debt_ratio", 0) or 0),
    })
    if len(rows) >= 2:
        prev = rows[-2]

        def _yoy(cur, old):
            try:
                cur, old = float(cur), float(old)
                return round(cur / old - 1.0, 4) if old else None
            except (TypeError, ValueError):
                return None
        out["revenue_yoy"] = _yoy(last.get("revenue"), prev.get("revenue"))
        out["net_profit_yoy"] = _yoy(last.get("net_profit"), prev.get("net_profit"))
    return out


def compute(snap: Snapshot) -> Dict[str, Any]:
    return {
        "technical": technical(snap.bars),
        "valuation": valuation(snap.valuation),
        "fundamental": fundamental(snap.fundamentals),
        "events": {
            "count": len(snap.events),
            "categories": sorted({str(e.get("category", "")) for e in snap.events if e.get("category")}),
        },
    }
