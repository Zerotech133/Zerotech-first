# -*- coding: utf-8 -*-
"""探针3（终审）：信号质量 vs 执行时点
对 v4 全部真实进场交易，对比：
  hyp  = 假设信号日(sig_dt)当天收盘就买，拿到同一出场日
  act  = 实际（确认日次日收盘才买）的 pnl
差值 = 确认滞后的入场成本；hyp 自身为负 = 信号本身烂。
另外对全部 fresh 买点（不限成交）算 sig_dt→+20日 前瞻收益，量信号本身含金量。
"""
import sys, os, json
import pandas as pd

BASE = r"C:/项目/老田的AI助手"
DATA = os.path.join(BASE, "quant_backtest", "data", "ashare")
POOL_SRC = os.path.join(BASE, "k_stock", "data", "poc_adapter_chanlun_20261012.json")
sys.path.insert(0, os.path.join(BASE, "quant_backtest"))
from chanlun_signal import analyze as chan_analyze  # noqa

WARMUP = 250
pool = json.load(open(POOL_SRC, encoding="utf-8"))
stocks = sorted({x["stock"] for x in pool})
books, adj_close = {}, {}
for s in stocks:
    f = os.path.join(DATA, s.replace(".", "_") + ".parquet")
    if not os.path.exists(f):
        continue
    b = pd.read_parquet(f).sort_values("trade_date").reset_index(drop=True)
    r = b["pct_chg"] / 100.0
    r.iloc[0] = 0.0
    kfac = (1.0 + r).cumprod() / b["close"]
    for _c in ("open", "high", "low", "close"):
        b["adj_" + _c] = b[_c] * kfac
    b["adj_close"] = b["close"] * kfac
    books[s] = b
    adj_close[s] = b.set_index("trade_date")["adj_close"]

cal = sorted(set().union(*[set(b["trade_date"]) for b in books.values()]))
cal = [d for d in cal if d >= "20251013"]
BUY_TYPES = {"1买", "2买", "3买"}

def run_day(day):
    out = {}
    for s, b in books.items():
        seg = b[b.trade_date <= day]
        if len(seg) < WARMUP // 2:
            continue
        bars = [{"dt": int(r.trade_date), "o": float(r.adj_open), "h": float(r.adj_high),
                 "l": float(r.adj_low), "c": float(r.adj_close)} for r in seg.tail(WARMUP).itertuples()]
        for sg in ((chan_analyze(bars) or {}).get("signals") or []):
            out[(s, sg["type"])] = int(sg["dt"])
    return out

prev = run_day("20251009")
seen_ever = set(prev)
fresh_by_day = {}
for day in cal:
    cur = run_day(day)
    fresh = [k for k in cur if k not in seen_ever]
    seen_ever |= set(cur)
    prev = cur
    fresh_by_day[day] = [(s, tp, cur[(s, tp)]) for (s, tp) in fresh if tp in BUY_TYPES]

# 全部 fresh 买点的前瞻 d20（信号本身含金量，不看是否成交）
rows_fwd = []
for day, evs in fresh_by_day.items():
    for (s, tp, dt) in evs:
        ac = adj_close[s]
        try:
            p0 = ac.get(str(dt))
            i0 = ac.index.get_loc(str(dt))
        except KeyError:
            continue
        if p0 is None or pd.isna(p0):
            continue
        idx = ac.index
        j = min(i0 + 20, len(idx) - 1)
        if j <= i0:
            continue
        rows_fwd.append({"stock": s, "type": tp, "sig_dt": dt, "fwd20": float(ac.iloc[j] / p0 - 1)})
fwd = pd.DataFrame(rows_fwd)
if len(fwd):
    print("全部fresh买点 sig_dt→+20日 前瞻收益（信号本身含金量）：")
    print(fwd.groupby("type")["fwd20"].agg(["count", "mean", lambda x: (x > 0).mean()]).rename(columns={"<lambda_0>": "win"}).round(4).to_string())
    print(f"合计 n={len(fwd)} mean={fwd.fwd20.mean():.4f} win={(fwd.fwd20>0).mean():.3f}")

# v4 实际成交交易对比（pend_buy 在确认日次一交易日成交 → 用 entry_day 的前一交易日匹配信号）
next_td = {cal[i]: (cal[i + 1] if i + 1 < len(cal) else None) for i in range(len(cal))}
exec_day_sig = {}
for day, evs in fresh_by_day.items():
    nd = next_td.get(day)
    if nd:
        exec_day_sig.setdefault(nd, {}).update({e[0]: e[2] for e in evs})
res = json.load(open(os.path.join(BASE, "quant_backtest", "chanlun_rotation_v4_result.json"), encoding="utf-8"))
trades = res["trades_A"]
cmp_rows = []
for t in trades:
    if t["how"] == "addon":
        continue
    s = t["stock"]
    sigmap = exec_day_sig.get(t["entry_day"]) or {}
    if s not in sigmap:
        continue
    dt = sigmap[s]
    ac = adj_close[s]
    try:
        p_sig = ac.get(str(dt))
        p_ent = ac.get(t["entry_day"])
        p_ext = ac.get(t["exit_day"])
    except KeyError:
        continue
    if any(x is None or pd.isna(x) for x in (p_sig, p_ent, p_ext)):
        continue
    cmp_rows.append({"stock": s, "how": t["how"], "sig_dt": dt, "entry_day": t["entry_day"],
                     "lag_days": None, "pnl_act": t["pnl"], "pnl_hyp": p_ext / p_sig - 1,
                     "runup_sig2ent": p_ent / p_sig - 1})
cmp = pd.DataFrame(cmp_rows)
import datetime as _d
cmp["lag_days"] = [(_d.datetime.strptime(e, "%Y%m%d") - _d.datetime.strptime(str(s), "%Y%m%d")).days
                   for s, e in zip(cmp.sig_dt, cmp.entry_day)]
print("\n实际成交(n=%d)：信号日买 vs 实际入场（同出场日）：" % len(cmp))
print("  pnl_hyp(信号日收盘买) 均值 %.4f 中位 %.4f 胜率 %.3f" % (cmp.pnl_hyp.mean(), cmp.pnl_hyp.median(), (cmp.pnl_hyp > 0).mean()))
print("  pnl_act(实际入场)     均值 %.4f 中位 %.4f 胜率 %.3f" % (cmp.pnl_act.mean(), cmp.pnl_act.median(), (cmp.pnl_act > 0).mean()))
print("  确认滞后天数 中位 %d p90 %d max %d" % (cmp.lag_days.median(), cmp.lag_days.quantile(0.9), cmp.lag_days.max()))
print("  信号日→入场日已涨幅 均值 %.4f 中位 %.4f" % (cmp.runup_sig2ent.mean(), cmp.runup_sig2ent.median()))
cmp.to_csv(os.path.join(BASE, "quant_backtest", "_probe3_trades_cmp.csv"), index=False, encoding="utf-8-sig")
print("csv: _probe3_trades_cmp.csv")
