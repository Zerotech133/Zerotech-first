# -*- coding: utf-8 -*-
"""Rejected research functions extracted from qpzm_backtest.py v3c+6 commits.

These 5 nested defs were inside run_backtest() along with P60-P68 research blocks
(+656 lines total). The entire research block was purged in the v3c → v3c-restore
operation because production callers (9 inside + 2 outside) never pass w4=.

Original: quant_backtest/qpzm_backtest.py.bak-pre-restore-20261114_205110
Range:    L974 - L1600 run_backtest() interior (11 hunks, +656/-14)
Commit:   c7af3e66b..HEAD (6 research-only commits)
Archived: 2026-11-14 for audit trail only
"""

# ──────────────────────────────────────────────────────────────────
# _ym — nested in run_backtest() at original L1093-1097
# ──────────────────────────────────────────────────────────────────

def _ym(_d):
    if hasattr(_d, "year"):
        return int(_d.year), int(_d.month)
    _i = int(_d)
    return _i // 10000, (_i // 100) % 100

# ──────────────────────────────────────────────────────────────────
# _band_row — nested in run_backtest() at original L1343-1357
# ──────────────────────────────────────────────────────────────────

def _band_row(mask):
    row = {"n_buy": int(mask.sum().sum()),
           "n_safe": int((mask & _sf_mat).sum().sum()),
           "holds": {}}
    for _n in holds:
        _r, _ = trade_returns(
            mask, open_in, close_in, _n, args.cost, unfill=unfill_in
        )
        row["holds"][str(_n)] = {
            "n_trades": int(len(_r)),
            "mean": round(float(_r.mean()), 6) if len(_r) else None,
            "median": round(float(_r.median()), 6) if len(_r) else None,
            "win_rate": round(float((_r > 0).mean()), 4) if len(_r) else None,
        }
    return row

# ──────────────────────────────────────────────────────────────────
# _portfolio_curve — nested in run_backtest() at original L1405-1423
# ──────────────────────────────────────────────────────────────────

def _portfolio_curve(batch_ret, w_ser, n, all_dates):
    rec = batch_ret.reindex(all_dates)
    ww = w_ser.reindex(all_dates).fillna(0.0)
    cash = 1.0
    due = {}
    navs = []
    for i, _d in enumerate(all_dates):
        if i in due:
            cash += due.pop(i)
        total = cash + sum(due.values())
        _r = rec.loc[_d]
        if pd.notna(_r) and float(ww.loc[_d]) > 0:
            amt = total * float(ww.loc[_d]) / n
            if amt > 0:
                cash -= amt
                _k = i + n
                due[_k] = due.get(_k, 0.0) + amt * (1.0 + float(_r))
        navs.append(cash + sum(due.values()))
    return pd.Series(navs, index=all_dates)

# ──────────────────────────────────────────────────────────────────
# _curve_stats — nested in run_backtest() at original L1425-1437
# ──────────────────────────────────────────────────────────────────

def _curve_stats(nav):
    if len(nav) < 2:
        return {}
    dd = float((nav / nav.cummax() - 1.0).min())
    tot = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
    days = max(1, len(nav))
    ann = (1.0 + tot) ** (243.0 / days) - 1.0 if tot > -1 else -1.0
    return {
        "total_ret": round(tot, 5),
        "annual": round(ann, 5),
        "max_dd": round(dd, 5),
        "calmar": round(ann / abs(dd), 3) if dd < 0 else None,
    }

# ──────────────────────────────────────────────────────────────────
# _mr_row — nested in run_backtest() at original L1499-1504
# ──────────────────────────────────────────────────────────────────

def _mr_row(mask, desc=""):
    out = {"n_buy": int(mask.sum().sum()), "desc": desc}
    for _n in holds:
        _r, _ = trade_returns(mask, open_in, close_in, _n, args.cost, unfill=unfill_in)
        out["t%d" % _n] = round(float(_r.mean()), 6) if len(_r) else None
    return out
