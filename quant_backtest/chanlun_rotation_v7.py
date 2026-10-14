# -*- coding: utf-8 -*-
"""
W3-4 · 缠论 v7 滚动池轮动回测（20261014）
动机: v6c2 C1 年化仅 0.5%，固定 76 只候选池"新买点即进场"导致止损出场占绝对主导(574/719)。
机制: 每 R∈{5,10,20} 交易日按最近三类买点信号(类型加权×新鲜度半衰×背驰加成)重选 TopN 持仓池，
      池外平仓、池内持有；regime/止损/卖点风控与 C1 完全一致，隔离"滚动池选股"单因子。
对照臂: B0 = v6c2 C1 原样复跑（同窗口/成本/风控），复跑数字必须对上留痕
      (annualized=0.5, mdd=-17.24) 作为同口径校验。
口径: use_hfq=true（config.json chanlun 节）→ kfac=pct_chg 累乘后复权，与 v6c/W1-2 同源；
      启动时打 [HFQ] 生效日志。
基建复用: chanlun_signal.analyze 为 import 复用；v6c 全顶层脚本无法安全 import（会重跑七臂
      并覆写留痕），故数据加载 15 行 kfac 逻辑以同口径函数内聚于本文件，用 B0 对数校验兜底。
"""
import sys, os, json, time
import pandas as pd

BASE = r"E:/老田的AI助手"
DATA = os.path.join(BASE, "quant_backtest", "data", "ashare")
POOL_SRC = os.path.join(BASE, "k_stock", "data", "poc_adapter_chanlun_20261012.json")
CFG = os.path.join(BASE, "quant_backtest", "config.json")
BASELINE_JSON = os.path.join(BASE, "quant_backtest", "chanlun_rotation_v6c2_result.json")
TAG = os.environ.get("V7_TAG", "")
OUT = os.path.join(BASE, "quant_backtest", f"chanlun_rotation_v7{TAG}_result.json")
sys.path.insert(0, os.path.join(BASE, "quant_backtest"))
from chanlun_signal import analyze as chan_analyze  # noqa: E402

# ---------- 与 v6c 完全一致的口径常量 ----------
D0 = "20231010"
WARMUP = 250
COST = 0.0015
SELL_TYPES = {"1卖", "2卖", "3卖"}
BUY_TYPES = {"1买", "2买", "3买"}
STOP_PCT = -0.08
CAP_HIGH, CAP_MID, CAP_LOW = 0.80, 0.50, 0.30

# ---------- v7 参数（内聚，不改 config.json; 环境变量可覆盖做敏感性变体）----------
def _envf(k, d):
    v = os.environ.get(k)
    return type(d)(v) if v not in (None, "") else d

ROLL_R = [5, 10, 20]          # 滚动重选周期（交易日）三档
TOP_N = _envf("V7_TOPN", 20)  # 滚动池持仓数
W_BUY = {"1买": _envf("V7_W1", 3.0), "2买": _envf("V7_W2", 2.0), "3买": _envf("V7_W3", 1.0)}
LOOKBACK = _envf("V7_LOOKBACK", 40)     # 信号计分回看窗口（交易日）
HALF_LIFE = _envf("V7_HALFLIFE", 10.0)  # 信号新鲜度半衰期（交易日）
BEI_BONUS = 1.2               # 背驰确认加成
STREAM_CACHE = os.path.join(BASE, "quant_backtest", "_v7_stream_cache.json")  # 信号流与权重无关, 变体共用

t0 = time.time()

# ---------- use_hfq 开关（W1-2 落地的 config 口径）----------
cfg = json.load(open(CFG, encoding="utf-8"))
USE_HFQ = bool((cfg.get("chanlun") or {}).get("use_hfq", False))

# ---------- 数据装载（与 v6c 同口径; use_hfq=true → kfac 后复权）----------
pool = json.load(open(POOL_SRC, encoding="utf-8"))
stocks = sorted({x["stock"] for x in pool})
books, adj_close, rets = {}, {}, {}
n_hfq_ok, n_skip = 0, 0
for s in stocks:
    f = os.path.join(DATA, s.replace(".", "_") + ".parquet")
    if not os.path.exists(f):
        n_skip += 1
        continue
    b = pd.read_parquet(f).sort_values("trade_date").reset_index(drop=True)
    if USE_HFQ:
        r = b["pct_chg"] / 100.0
        r.iloc[0] = 0.0
        kfac = (1.0 + r).cumprod() / b["close"]
        for col in ("open", "high", "low", "close"):
            b["adj_" + col] = b[col] * kfac
        n_hfq_ok += 1
    else:
        for col in ("open", "high", "low", "close"):
            b["adj_" + col] = b[col]
    books[s] = b
    adj_close[s] = b.set_index("trade_date")["adj_close"]
    rets[s] = b.set_index("trade_date")["pct_chg"] / 100.0

cal_all = sorted(set().union(*[set(b["trade_date"]) for b in books.values()]))
cal = [d for d in cal_all if d >= D0]
row_idx = {s: b.set_index("trade_date") for s, b in books.items()}
if USE_HFQ:
    print(f"[HFQ] use_hfq=true 生效路径已走到: kfac后复权 {n_hfq_ok}/{len(stocks)} 只, "
          f"降级记名 HFQ_FALLBACK=0, 数据缺失跳过={n_skip}, D0={D0}", flush=True)
else:
    print(f"[HFQ][WARN] use_hfq=false, 不复权口径(与产线不符), 仅作对照", flush=True)

# ---------- 池等权指数 + regime（与 v6c 一致）----------
idx_ret = {}
for day in cal:
    rs = [rets[s].get(day) for s in books]
    rs = [x for x in rs if x is not None and not pd.isna(x)]
    idx_ret[day] = sum(rs) / len(rs) if rs else 0.0
idx_nav = (1 + pd.Series(idx_ret).sort_index()).cumprod()
ma20 = idx_nav.rolling(20).mean()
ma60 = idx_nav.rolling(60).mean()
def regime_cap(day):
    i = idx_nav.index.get_loc(day)
    if i == 0:
        return CAP_MID
    v, m20, m60 = idx_nav.iloc[i-1], ma20.iloc[i-1], ma60.iloc[i-1]
    if pd.isna(m60):
        return CAP_MID
    if v >= m20:
        return CAP_HIGH
    if v >= m60:
        return CAP_MID
    return CAP_LOW

# ---------- 信号流（hfq + seen-ever，一次计算四臂共用）----------
def run_day(day):
    out = {}
    for s, b in books.items():
        seg = b[b.trade_date <= day]
        if len(seg) < WARMUP // 2:
            continue
        bars = [{"dt": int(r.trade_date), "o": float(r.adj_open), "h": float(r.adj_high),
                 "l": float(r.adj_low), "c": float(r.adj_close)} for r in seg.tail(WARMUP).itertuples()]
        for sg in ((chan_analyze(bars) or {}).get("signals") or []):
            out[(s, sg["type"])] = (int(sg["dt"]),
                                    bool(sg.get("bei")) or bool(sg.get("bei2")))
    return out

prev = run_day("20231009")
seen_ever = set(prev)
sell_days, buy_ev = {}, {}     # buy_ev[day] = [(stock, type, bei)]
n_buy_ev = n_sell_ev = 0
if os.path.exists(STREAM_CACHE):
    _c = json.load(open(STREAM_CACHE, encoding="utf-8"))
    sell_days = {int(k): set(v) for k, v in _c["sell_days"].items()}
    buy_ev = {int(k): [(a, b_, bool(c)) for a, b_, c in v] for k, v in _c["buy_ev"].items()}
    n_buy_ev, n_sell_ev = _c["n_buy_ev"], _c["n_sell_ev"]
    print(f"信号流: 命中缓存 {STREAM_CACHE} (新鲜买点={n_buy_ev} 新鲜卖点={n_sell_ev})", flush=True)
else:
    for day in cal:
        cur = run_day(day)
        fresh = [k for k in cur if k not in seen_ever]
        seen_ever |= set(cur)
        prev = cur
        sell_days[day] = {s for (s, tp) in fresh if tp in SELL_TYPES}
        evs = [(s, tp, cur[(s, tp)][1]) for (s, tp) in fresh if tp in BUY_TYPES]
        buy_ev[day] = evs
        n_buy_ev += len(evs)
        n_sell_ev += len(sell_days[day])
        if len(sell_days) % 180 == 0:
            print(f"  …{len(sell_days)}/{len(cal)}（{round(time.time()-t0)}s）", flush=True)
    json.dump({"sell_days": {k: sorted(v) for k, v in sell_days.items()},
               "buy_ev": {k: [list(x) for x in v] for k, v in buy_ev.items()},
               "n_buy_ev": n_buy_ev, "n_sell_ev": n_sell_ev},
              open(STREAM_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"信号流已缓存 → {STREAM_CACHE}", flush=True)
print(f"信号流 {round(time.time()-t0)}s: 新鲜买点={n_buy_ev} 新鲜卖点={n_sell_ev}", flush=True)

# 事件按股票索引，供滚动打分
ev_by_day = []                              # ev_by_day[i] = [(stock, w, bei)]，i 为 cal 下标
cal_i = {d: i for i, d in enumerate(cal)}
for day in cal:
    ev_by_day.append([(s, W_BUY[tp], bei) for (s, tp, bei) in buy_ev.get(day, [])])

def select_pool(di, R):
    """最近 LOOKBACK 交易日内买点信号打分: 类型权重×0.5^(年龄/半衰)×背驰加成, 取 TopN。"""
    lo = max(0, di - LOOKBACK)
    score = {}
    for j in range(lo, di + 1):
        age = di - j
        decay = 0.5 ** (age / HALF_LIFE)
        for s, w, bei in ev_by_day[j]:
            sc = w * decay * (BEI_BONUS if bei else 1.0)
            score[s] = score.get(s, 0.0) + sc
    ranked = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))
    return {s for s, _ in ranked[:TOP_N]}, len(score)

# ---------- 通用统计（含任务要求的夏普/PF/换手率）----------
def stats(equity, trades, inv, label):
    eq = pd.Series({e["day"]: e["equity"] for e in equity})
    total = eq.iloc[-1] - 1
    ann = eq.iloc[-1] ** (245 / len(eq)) - 1
    mdd = ((eq / eq.cummax()) - 1).min()
    dr = eq.pct_change().dropna()
    sharpe = float(dr.mean() / dr.std() * (245 ** 0.5)) if len(dr) > 2 and dr.std() > 0 else None
    closed = [t for t in trades if t["exit_day"] is not None]
    wins = [t for t in closed if (t["pnl"] or 0) > 0]
    gp = sum(t["pnl"] for t in closed if (t["pnl"] or 0) > 0)
    gl = -sum(t["pnl"] for t in closed if (t["pnl"] or 0) <= 0)
    years = len(eq) / 245.0
    r = {"label": label, "total_return_pct": round(total * 100, 2), "annualized_pct": round(ann * 100, 2),
         "max_drawdown_pct": round(mdd * 100, 2),
         "sharpe": round(sharpe, 3) if sharpe is not None else None,
         "profit_factor": round(gp / gl, 3) if gl > 0 else None,
         "calmar": round(ann / abs(mdd), 2) if mdd else None,
         "turnover_pa": round(len(closed) / years, 1),
         "n_exits": len(closed),
         "win_rate_pct": round(len(wins) / len(closed) * 100, 1) if closed else None,
         "avg_invested_pct": round(sum(inv) / len(inv) * 100, 1)}
    hows = {}
    for t in closed:
        hows[t["how"]] = hows.get(t["how"], 0) + 1
    r["exit_mix"] = hows
    print(label, json.dumps(r, ensure_ascii=False), flush=True)
    return r

# ---------- B0 对照臂: v6c2 C1 原样复跑 ----------
def simulate_c1():
    cash, pos = 1.0, {}
    pending_init = set(books)
    equity, trades, inv_hist = [], [], []
    SLOT0 = CAP_HIGH / len(books)
    for day in cal:
        for s in pos:
            r = rets[s].get(day)
            if r is not None and not pd.isna(r):
                pos[s]["val"] *= (1 + r)
        eqv = cash + sum(p["val"] for p in pos.values())
        for s in list(pos):
            p = pos[s]
            out = False
            if s in sell_days.get(day, set()):
                out = True; how = "sell_signal"
            elif p["val"] / p["cost"] - 1 <= STOP_PCT:
                out = True; how = "stop"
            if out:
                cash += p["val"] * (1 - COST)
                trades.append({"stock": s, "exit_day": day, "pnl": p["val"] / p["cost"] - 1,
                               "entry_day": p["entry_day"], "how": how})
                del pos[s]
                pending_init.add(s)
        eqv = cash + sum(p["val"] for p in pos.values())
        cap = regime_cap(day)
        deployed = eqv - cash
        target = eqv * cap
        if deployed > target * 1.01:
            ratio = 1 - target / deployed
            for s in list(pos):
                p = pos[s]
                cut = p["val"] * ratio
                cash += cut * (1 - COST)
                p["val"] -= cut; p["cost"] *= (1 - ratio)
                if p["val"] < 0.0005 * eqv:
                    cash += p["val"] * (1 - COST)
                    del pos[s]
        want_in = {s for s in pending_init if s in row_idx and day in row_idx[s].index}
        want_in |= {s for s in (buy_ev.get(day) and {e[0] for e in buy_ev[day]} or set()) if s in books} - set(pos)
        for s in sorted(want_in):
            if s in pos or deployed >= target * 0.999:
                continue
            amt = min(cash, eqv * SLOT0)
            if amt <= 0.002:
                continue
            cash -= amt
            pos[s] = {"val": amt * (1 - COST), "cost": amt * (1 - COST), "entry_day": day}
            pending_init.discard(s)
            trades.append({"stock": s, "exit_day": None, "entry_day": day, "how": "open", "pnl": None})
            deployed += amt
        deployed = eqv - cash
        if deployed < target * 0.99 and pos:
            add_total = min(cash, target - deployed)
            tw = sum(p["val"] for p in pos.values())
            for s, p in pos.items():
                amt = add_total * p["val"] / tw
                if amt > 0.005:
                    cash -= amt; p["val"] += amt * (1 - COST); p["cost"] += amt * (1 - COST)
        eqv = cash + sum(p["val"] for p in pos.values())
        equity.append({"day": day, "equity": eqv, "inv": (eqv - cash) / eqv if eqv else 0})
        inv_hist.append((eqv - cash) / eqv if eqv else 0)
    return equity, trades, inv_hist

# ---------- v7 滚动池臂 ----------
def simulate_roll(R):
    cash, pos = 1.0, {}
    equity, trades, inv_hist = [], [], []
    reb_days = {d for i, d in enumerate(cal) if i % R == 0}
    pool_now, n_rebal, n_cov_sum = set(), 0, 0
    for day in cal:
        di = cal_i[day]
        for s in pos:
            r = rets[s].get(day)
            if r is not None and not pd.isna(r):
                pos[s]["val"] *= (1 + r)
        eqv = cash + sum(p["val"] for p in pos.values())
        # 个股出口: 卖点信号 / 结构止损（与 C1 同款; 池外平仓只发生在重选日）
        for s in list(pos):
            p = pos[s]
            out = False
            if s in sell_days.get(day, set()):
                out = True; how = "sell_signal"
            elif p["val"] / p["cost"] - 1 <= STOP_PCT:
                out = True; how = "stop"
            if out:
                cash += p["val"] * (1 - COST)
                trades.append({"stock": s, "exit_day": day, "pnl": p["val"] / p["cost"] - 1,
                               "entry_day": p["entry_day"], "how": how})
                del pos[s]
        eqv = cash + sum(p["val"] for p in pos.values())
        cap = regime_cap(day)
        deployed = eqv - cash
        target = eqv * cap
        if deployed > target * 1.01:
            ratio = 1 - target / deployed
            for s in list(pos):
                p = pos[s]
                cut = p["val"] * ratio
                cash += cut * (1 - COST)
                p["val"] -= cut; p["cost"] *= (1 - ratio)
                if p["val"] < 0.0005 * eqv:
                    cash += p["val"] * (1 - COST)
                    del pos[s]
        # 滚动重选日: 重选 TopN → 池外平仓 → 池内等权建/补
        if day in reb_days:
            pool_now, cov = select_pool(di, R)
            n_rebal += 1; n_cov_sum += cov
            for s in list(pos):
                if s not in pool_now:
                    p = pos[s]
                    cash += p["val"] * (1 - COST)
                    trades.append({"stock": s, "exit_day": day, "pnl": p["val"] / p["cost"] - 1,
                                   "entry_day": p["entry_day"], "how": "pool_out"})
                    del pos[s]
            eqv = cash + sum(p["val"] for p in pos.values())
            deployed = eqv - cash
            target = eqv * cap
            slot = eqv * cap / TOP_N
            for s in sorted(pool_now):
                if s in pos or s not in row_idx or day not in row_idx[s].index:
                    continue
                amt = min(cash, slot, max(0.0, target - deployed))
                if amt <= 0.002:
                    continue
                cash -= amt
                pos[s] = {"val": amt * (1 - COST), "cost": amt * (1 - COST), "entry_day": day}
                trades.append({"stock": s, "exit_day": None, "entry_day": day, "how": "open", "pnl": None})
                deployed += amt
        # 池内富余回补（regime 升档/出场释放资金 → 等比例补至目标仓位）
        eqv = cash + sum(p["val"] for p in pos.values())
        deployed = eqv - cash
        target = eqv * regime_cap(day)
        if deployed < target * 0.99 and pos:
            add_total = min(cash, target - deployed)
            tw = sum(p["val"] for p in pos.values())
            for s, p in pos.items():
                amt = add_total * p["val"] / tw
                if amt > 0.005:
                    cash -= amt; p["val"] += amt * (1 - COST); p["cost"] += amt * (1 - COST)
        eqv = cash + sum(p["val"] for p in pos.values())
        equity.append({"day": day, "equity": eqv, "inv": (eqv - cash) / eqv if eqv else 0})
        inv_hist.append((eqv - cash) / eqv if eqv else 0)
    print(f"  R={R}: 重选{n_rebal}次, 重选日候选覆盖均值={n_cov_sum/max(1,n_rebal):.1f}, 最终持仓={len(pos)}", flush=True)
    return equity, trades, inv_hist, {"n_rebalance": n_rebal,
                                      "avg_candidates": round(n_cov_sum / max(1, n_rebal), 1)}

# ---------- 跑四臂 ----------
print("B0：v6c2 C1 原样复跑（同窗口/成本/风控, 同口径校验）…", flush=True)
e0, t0_, i0 = simulate_c1()
b0 = stats(e0, t0_, i0, "B0_v6c2C1复跑")
try:
    base = json.load(open(BASELINE_JSON, encoding="utf-8"))["C1"]
    ok = (abs(b0["annualized_pct"] - base["annualized_pct"]) < 0.05
          and abs(b0["max_drawdown_pct"] - base["max_drawdown_pct"]) < 0.05)
    print(f"同口径校验: 复跑 ann={b0['annualized_pct']} mdd={b0['max_drawdown_pct']} vs 留痕 "
          f"ann={base['annualized_pct']} mdd={base['max_drawdown_pct']} → {'PASS' if ok else 'FAIL'}", flush=True)
    b0["baseline_check"] = "PASS" if ok else "FAIL"
except Exception as ex:
    print("同口径校验: 留痕读取失败", ex, flush=True)
    b0["baseline_check"] = "NO_REF"

arms, eq_curves = {}, {}
for R in ROLL_R:
    print(f"V7_R{R}：滚动池 Top{TOP_N} 每{R}交易日重选 …", flush=True)
    e, t, i, meta = simulate_roll(R)
    arms[f"R{R}"] = stats(e, t, i, f"V7_R{R}_Top{TOP_N}")
    arms[f"R{R}"]["roll_meta"] = meta
    eq_curves[f"R{R}"] = {x["day"]: round(x["equity"], 6) for x in e}
eq_curves["B0"] = {x["day"]: round(x["equity"], 6) for x in e0}

# ---------- 判据: 年化改善 ≥2pp 且回撤不恶化 ----------
best_k = max(arms, key=lambda k: arms[k]["annualized_pct"])
best, b_ann, b_mdd = arms[best_k], b0["annualized_pct"], b0["max_drawdown_pct"]
d_ann = round(best["annualized_pct"] - b_ann, 2)
d_mdd = round(best["max_drawdown_pct"] - b_mdd, 2)
verdict = {"baseline_arm": "B0_v6c2C1复跑", "baseline_ann_pct": b_ann, "baseline_mdd_pct": b_mdd,
           "best_arm": best_k, "best_ann_pct": best["annualized_pct"], "best_mdd_pct": best["max_drawdown_pct"],
           "ann_improve_pp": d_ann, "mdd_change_pp": d_mdd,
           "criterion": "年化改善≥2pp 且回撤不恶化",
           "ann_pass": bool(d_ann >= 2.0), "mdd_pass": bool(d_mdd >= 0),
           "shadow_go": bool(d_ann >= 2.0 and d_mdd >= 0)}
print("判据:", json.dumps(verdict, ensure_ascii=False), flush=True)

out = {"meta": {"D0": D0, "cost_bps": 15, "stop": STOP_PCT, "caps": [CAP_HIGH, CAP_MID, CAP_LOW],
                "use_hfq": USE_HFQ, "top_n": TOP_N, "roll_r": ROLL_R,
                "w_buy": W_BUY, "lookback": LOOKBACK, "half_life": HALF_LIFE, "bei_bonus": BEI_BONUS,
                "n_buy_signals": n_buy_ev, "n_sell_signals": n_sell_ev,
                "design": "v7滚动池: 每 R 日按买点类型加权×新鲜度半衰×背驰加成重选TopN, 池外平仓池内持有, "
                          "regime/止损/卖点与 C1 同款; B0=v6c2 C1 原样复跑同口径校验"},
       "B0": b0, "arms": arms, "verdict": verdict,
       "equity": eq_curves}
json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("JSON:", OUT, flush=True)
print(f"总耗时 {round(time.time()-t0)}s", flush=True)
