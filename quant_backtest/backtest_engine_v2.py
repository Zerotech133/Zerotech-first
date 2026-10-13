# -*- coding: utf-8 -*-
"""
K-STOCK-285b · 回测框架 v2 正式版（配置驱动 · 执行时间轴 · 仓位管理一体化）
老田 10-13 07:42 口径（285 原型验证通过，本版正式化）：
  ① 缠论 = 选出股票后"等机会"：买点确认(T 收盘) → T+1 开盘买入（runup 帽）；卖点确认
     → T+1 开盘卖出，资金回池等下一个买点；无硬止损（269）；regime 80/50/30 超帽等比 trim。
  ② 量子双刀 = 日内纪律：T 14:45 选股（原始价 bar）→ T 14:55 只卖不买（信号消失/持有
     超上限/灾难止损，收盘价离场）→ 买入 T+1 开盘 → 最早 T+2 卖（A股 T+1 制度）。
本版新增（285b）：
  - 缠论信号自算（hfq+seen-ever，任意窗口；一年窗优先复用 277 账本缓存，零重算）
  - progress 回调（供 portal daemon 线程写进度 state，前端轮询）
  - run_job() 高层入口（参数 dict → 校验 → 回测 → 落 data/bt2/<job_id>.json）
  - CLI（argparse）供命令行自测：--strategy --d0 --d1 --top-n ...
口径铁律：缠论信号/结算=hfq（263）；双刀信号=原始价（6-200 元实务门槛，hfq 全灭
实证 10-13）而结算=hfq。信号与结算分离，两口径各安其位。
"""
import sys, os, json, time, argparse
import pandas as pd
from dataclasses import dataclass, asdict

BASE = r"E:/老田的AI助手"
DATA = os.path.join(BASE, "quant_backtest", "data", "ashare")
POOL_SRC = os.path.join(BASE, "k_stock", "data", "poc_adapter_chanlun_20261012.json")
LEDGER = os.path.join(BASE, "quant_backtest", "data", "daily_ledger", "ledger_20251013_20261012.json")
OUT_DIR = os.path.join(BASE, "quant_backtest", "data", "bt2")
COST = 0.0015
D0_MIN = "20231010"          # 273 体检诚实上限（因子湖 2023-07 起连续，池指数预热再往前）

_LOADED = False
books, rets, cal, cal_idx_all = {}, {}, [], []
idx_nav = ma20 = ma60 = None

EXEC_TIMELINE = {
    "chanlun": ["T 收盘：缠论买点确认（seen-ever·hfq）", "T+1 开盘：买入（runup帽>15%放弃该机会）",
                "T 收盘：卖点确认", "T+1 开盘：卖出，资金回池等下一个买点",
                "组合层：regime 80/50/30 连续调仓（D-1 判定）+ 超帽等比减仓"],
    "double_knife": ["T 14:45：选股引擎跑双刀信号（十字星2连+MACD多头+均线多头+regime非空）",
                     "T 14:55：只卖不买（信号消失/持有到期/灾难止损，收盘价离场）",
                     "T 盘中：禁止买入", "T+1 开盘：买入新选出的票（slot 内 · 总仓位帽内）",
                     "T+2 起：可卖（T+1 制度），14:55 决策当日收盘执行"],
}
ASSUMPTIONS = {
    "chanlun": ["信号=T 收盘确认的 seen-ever fresh 口径（hfq K 线，263 铁律）",
                "成交价=T+1 开盘（hfq）；15bp 双边成本", "无个股硬止损（269 实锤止损负贡献）"],
    "double_knife": ["信号=原始价 K 线（引擎 6-200 元/成交额为实务门槛，hfq 全灭·10-13 实证）；结算仍走 hfq 因子链",
                     "14:45 选股用 T 日 bar 近似（盘中快照数据未入库，包 C 升级项）",
                     "买入=T+1 开盘而非 T 14:55（避免用当日收盘价成交的前视）",
                     "卖出=当日收盘价近似 14:55；min_holding=1 日对齐 A 股 T+1 制度",
                     "regime 帽暂用池等权指数（全市场指数等包 C 指数移出后切换）"],
}

# ============================================================
# 数据装载（惰性单例；hfq 后复权，263a 铁律）
# ============================================================
def _ensure_loaded(progress=None):
    global _LOADED, books, rets, cal, cal_idx_all, idx_nav, ma20, ma60
    if _LOADED:
        return
    pool = json.load(open(POOL_SRC, encoding="utf-8"))
    stocks = sorted({x["stock"] for x in pool})
    for s in stocks:
        f = os.path.join(DATA, s.replace(".", "_") + ".parquet")
        if not os.path.exists(f):
            continue
        b = pd.read_parquet(f).sort_values("trade_date").reset_index(drop=True)
        r = b["pct_chg"] / 100.0
        r.iloc[0] = 0.0
        kfac = (1.0 + r).cumprod() / b["close"]
        for col in ("open", "high", "low", "close"):
            b["adj_" + col] = b[col] * kfac
        books[s] = b
        rets[s] = b.set_index("trade_date")["pct_chg"] / 100.0
    cal_all = sorted(set().union(*[set(b["trade_date"]) for b in books.values()]))
    cal_idx_all = [d for d in cal_all if d >= D0_MIN]
    idx_ret = {}
    for day in cal_idx_all:
        rs = [rets[s].get(day) for s in books]
        rs = [x for x in rs if x is not None and not pd.isna(x)]
        idx_ret[day] = sum(rs) / len(rs) if rs else 0.0
    idx_nav = (1 + pd.Series(idx_ret).sort_index()).cumprod()
    ma20 = idx_nav.rolling(20).mean()
    ma60 = idx_nav.rolling(60).mean()
    _LOADED = True
    if progress:
        progress("数据装载", 5, f"池 {len(books)} 只 · 日历 {len(cal_idx_all)} 日")

def px(s, day, kind):
    b = books.get(s)
    if b is None:
        return None
    row = b[b.trade_date == day]
    if row.empty:
        return None
    return float(row.iloc[0]["adj_" + kind])

def regime_of(day):
    i = idx_nav.index.get_loc(day)
    if i == 0:
        return 0.50, "mid", "首日默认"
    v, m20v, m60v = idx_nav.iloc[i-1], ma20.iloc[i-1], ma60.iloc[i-1]
    if pd.isna(m60v):
        return 0.50, "mid", "MA60 未预热"
    if v >= m20v:
        return 0.80, "high", "池指数≥MA20"
    if v >= m60v:
        return 0.50, "mid", "MA60≤池指数<MA20"
    return 0.30, "low", "池指数<MA60"

# ============================================================
# 信号源（自算 + 缓存）
# ============================================================
sys.path.insert(0, os.path.join(BASE, "quant_backtest"))
from chanlun_signal import analyze as chan_analyze  # noqa: E402

WARMUP = 250
SELL_TYPES = {"1卖", "2卖", "3卖"}
BUY_TYPES = {"1买", "2买", "3买"}

def _chanlun_run_day(day):
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

def calc_chanlun_signals(d0, d1, progress=None):
    """hfq + seen-ever 缠论信号自算（任意窗口）。一年窗（=277 账本口径）直接复用缓存零重算。"""
    if d0 >= "20251013" and d1 <= "20261012" and os.path.exists(LEDGER):
        j = json.load(open(LEDGER, encoding="utf-8"))
        sig_buy, sig_sell = {}, {}
        for day, d in j["days"].items():
            if not (d0 <= day <= d1):
                continue
            bs, ss = set(), set()
            for a in d.get("actions", []):
                if a["type"] == "sig_buy":
                    bs.add(a["stock"])
                elif a["type"] == "sig_sell":
                    ss.add(a["stock"])
            sig_buy[day], sig_sell[day] = bs, ss
        if progress:
            progress("信号", 30, f"缠论信号复用 277 账本（{len(sig_buy)} 日，零重算）")
        return sig_buy, sig_sell, "277账本缓存"
    # 自算：窗口前一日初始化 seen-ever
    prior = [d for d in cal_idx_all if d < d0]
    init_day = prior[-1] if prior else d0
    t0 = time.time()
    seen_ever = set(_chanlun_run_day(init_day))
    sig_buy, sig_sell = {}, {}
    win = [d for d in cal_idx_all if d0 <= d <= d1]
    for i, day in enumerate(win):
        cur = _chanlun_run_day(day)
        fresh = [k for k in cur if k not in seen_ever]
        seen_ever |= set(cur)
        sig_buy[day] = {s for (s, tp) in fresh if tp in BUY_TYPES}
        sig_sell[day] = {s for (s, tp) in fresh if tp in SELL_TYPES}
        if progress and (i % 20 == 0 or i == len(win) - 1):
            progress("信号", 5 + int(60 * (i + 1) / len(win)),
                     f"缠论 seen-ever {i+1}/{len(win)} 日（{round(time.time()-t0)}s）")
    return sig_buy, sig_sell, "自算(hfq+seen-ever)"

def calc_dknife_signals(d0, d1, profile="relaxed", progress=None):
    """量子双刀逐日信号：原始价 K 线（引擎实务门槛），T 日 14:45 口径近似=T 日 bar。"""
    sys.path.insert(0, BASE)
    from agent_platform.engine.quant_double_knife import quant_double_knife
    sig, t0 = {}, time.time()
    win_books = list(books.items())
    for i, (s, b) in enumerate(win_books):
        df = pd.DataFrame({
            "open": b["open"], "high": b["high"], "low": b["low"],
            "close": b["close"], "amount": b["amount"],
        })
        out = quant_double_knife(df, profile=profile)
        mask = out["double_knife_signal"].fillna(False)
        for day in b.loc[mask, "trade_date"]:
            if d0 <= day <= d1:
                sig.setdefault(day, set()).add(s)
        if progress and (i % 10 == 0 or i == len(win_books) - 1):
            progress("信号", 5 + int(60 * (i + 1) / len(win_books)),
                     f"双刀扫描 {i+1}/{len(win_books)} 只（{round(time.time()-t0)}s）")
    return sig

# ============================================================
# BacktestConfig —— 11 参数空间（283 定档）
# ============================================================
@dataclass
class BacktestConfig:
    name: str
    strategy: str              # chanlun | double_knife
    d0: str
    d1: str
    buy_timing: str = "T+1_open"
    sell_timing: str = "T+1_open"       # chanlun | T_close_1455 (double_knife)
    min_holding_days: int = 0
    holding_max_days: int = 0           # 0=不限
    top_n: int = 10
    slot_pct: float = 0.08
    cap_mode: str = "regime_8050"       # regime_8050 | fixed
    cap_fixed: float = 0.80
    runup_cap: float = 0.0
    stop_pct: float = 0.0
    rebalance_every: int = 0
    cost: float = COST

    def cap_of(self, regime_cap):
        return regime_cap if self.cap_mode == "regime_8050" else self.cap_fixed

# ============================================================
# 统一引擎
# ============================================================
def run_backtest(cfg: BacktestConfig, sig_buy: dict, sig_sell: dict, progress=None):
    global cal
    cash, pos = 1.0, {}
    days, equity_curve, trades, holdings = {}, [], [], {}
    pending_buy, pending_sell = set(), set()
    holding_since = {}
    n_trim = 0
    prev_eq = 1.0
    win = [d for d in cal_idx_all if cfg.d0 <= d <= cfg.d1]
    cal_saved, cal = cal, win

    def rec(day, side, s, how, pnl=None):
        trades.append({"day": day, "stock": s, "side": side, "how": how,
                       "note": "", "pnl": None if pnl is None else round(pnl * 100, 2)})

    for di, day in enumerate(win):
        if progress and di % 20 == 0:
            progress("回测", 65 + int(30 * di / len(win)), f"逐日执行 {di+1}/{len(win)}")
        prev_day = win[di - 1] if di > 0 else None
        actions = []

        # A1. 开盘卖出（T+1_open 口径，停牌顺延不丢单）
        still = set()
        for s in sorted(pending_sell):
            p = pos.get(s)
            o = px(s, day, "open")
            if p is None:
                continue
            if o is None:
                still.add(s)
                continue
            val_exit = p["val"] * o / p["ref_px"]
            pnl = val_exit / p["cost"] - 1
            cash += val_exit * (1 - cfg.cost)
            del pos[s]; holding_since.pop(s, None)
            actions.append({"type": "sell", "stock": s, "note": "T+1 开盘离场",
                            "pnl_pct": round(pnl * 100, 2)})
            rec(day, "SELL", s, "exit_open", pnl)
        pending_sell = still

        # A2. 开盘买入（统一 T+1 open；runup 帽 / 总仓位帽 / slot）
        cap, regime_lv, regime_why = regime_of(day)
        capv = cfg.cap_of(cap)
        eqv_open = cash + sum(p["val"] for p in pos.values())
        deployed = eqv_open - cash
        still = set()
        n_buy_today = 0
        for s in sorted(pending_buy):
            if n_buy_today >= cfg.top_n:
                still.add(s)
                continue
            if s in pos:
                continue
            o = px(s, day, "open")
            if o is None:
                still.add(s)
                continue
            if cfg.runup_cap > 0 and prev_day:
                c_prev = px(s, prev_day, "close")
                if c_prev and o / c_prev - 1 > cfg.runup_cap:
                    actions.append({"type": "skip", "stock": s,
                                    "note": f"runup帽：开盘较昨收 +{(o/c_prev-1)*100:.1f}% >{cfg.runup_cap:.0%}，放弃"})
                    continue
            amt = min(cash, eqv_open * cfg.slot_pct, max(0.0, eqv_open * capv - deployed))
            if amt < 0.002 * eqv_open:
                continue
            cash -= amt
            pos[s] = {"val": amt * (1 - cfg.cost), "cost": amt * (1 - cfg.cost), "ref_px": o}
            holding_since[s] = di
            deployed += amt; n_buy_today += 1
            actions.append({"type": "buy", "stock": s,
                            "note": f"{cfg.buy_timing} 买入 {amt / eqv_open:.1%}"})
            rec(day, "BUY", s, cfg.strategy)
        pending_buy = still

        # B. 盯市 open→close
        for s, p in pos.items():
            c = px(s, day, "close")
            if c is not None:
                p["val"] *= c / p["ref_px"]
                p["ref_px"] = c
        eqv = cash + sum(p["val"] for p in pos.values())

        # C. regime 超帽 → 收盘等比减
        deployed = eqv - cash
        target = eqv * capv
        if deployed > target * 1.01 and deployed > 0 and pos:
            ratio = 1 - target / deployed
            for s in list(pos):
                p = pos[s]
                cut = p["val"] * ratio
                cash += cut * (1 - cfg.cost)
                p["val"] -= cut; p["cost"] *= (1 - ratio)
                if p["val"] < 0.0005 * eqv:
                    cash += p["val"] * (1 - cfg.cost)
                    del pos[s]; holding_since.pop(s, None)
            n_trim += 1
            actions.append({"type": "trim", "note": f"regime={regime_lv} 超帽：等比减仓 {ratio:.0%}（目标 {capv:.0%}）"})
            eqv = cash + sum(p["val"] for p in pos.values())

        # D. 收盘事件
        if cfg.strategy == "chanlun":
            for s in sorted(sig_sell.get(day, set())):
                if s in pos:
                    pending_sell.add(s)
                    actions.append({"type": "note", "stock": s, "note": "卖点确认→明日开盘卖"})
            for s in sorted(sig_buy.get(day, set())):
                if s not in pos and s not in pending_buy:
                    pending_buy.add(s)
                    actions.append({"type": "note", "stock": s, "note": "买点确认→明日开盘候选"})
        else:
            for s in sorted(sig_buy.get(day, set())):
                if s not in pos and s not in pending_buy:
                    pending_buy.add(s)
                    actions.append({"type": "note", "stock": s, "note": "14:45 选出→明日开盘买入"})
            for s in list(pos):
                p = pos[s]
                held = di - holding_since[s]
                if held < cfg.min_holding_days:
                    continue
                loss_hit = cfg.stop_pct < 0 and p["val"] / p["cost"] - 1 <= cfg.stop_pct
                expired = cfg.holding_max_days > 0 and (held + 1) >= cfg.holding_max_days
                sig_gone = s not in sig_buy.get(day, set())
                if sig_gone or expired or loss_hit:
                    why = "灾难止损" if loss_hit else ("持有到期" if expired else "信号消失")
                    pnl = p["val"] / p["cost"] - 1
                    cash += p["val"] * (1 - cfg.cost)
                    del pos[s]; holding_since.pop(s, None)
                    actions.append({"type": "sell", "stock": s, "note": f"14:55 {why}（收盘价）",
                                    "pnl_pct": round(pnl * 100, 2)})
                    rec(day, "SELL", s, "dk_" + why, pnl)

        # E. 记账
        eqv = cash + sum(p["val"] for p in pos.values())
        pnl_day = (eqv / prev_eq - 1) * 100
        prev_eq = eqv
        wins = sum(1 for p in pos.values() if p["val"] / p["cost"] - 1 > 0)
        days[day] = {"pnl_pct": round(pnl_day, 3), "equity": round(eqv, 6),
                     "n_pos": len(pos), "wins": wins, "losses": len(pos) - wins,
                     "cap": capv, "regime": regime_lv,
                     "cash_pct": round(cash / eqv * 100, 1) if eqv else 0,
                     "actions": actions}
        equity_curve.append({"day": day, "equity": round(eqv, 6),
                             "inv_pct": round((eqv - cash) / eqv * 100, 1) if eqv else 0})
        holdings[day] = [{"stock": s, "pnl_pct": round((p["val"] / p["cost"] - 1) * 100, 2),
                          "wgt_pct": round(p["val"] / eqv * 100, 2)} for s, p in sorted(pos.items())]

    cal = cal_saved

    closed = [t for t in trades if t["pnl"] is not None]
    eq = pd.Series({e["day"]: e["equity"] for e in equity_curve})
    total = eq.iloc[-1] - 1
    ann = eq.iloc[-1] ** (245 / len(eq)) - 1
    mdd = ((eq / eq.cummax()) - 1).min()
    wins_tr = [t for t in closed if t["pnl"] > 0]
    loss_sum = -sum(t["pnl"] for t in closed if t["pnl"] <= 0)
    summary = {"name": cfg.name, "total_return_pct": round(total * 100, 2),
               "annualized_pct": round(ann * 100, 2), "max_drawdown_pct": round(mdd * 100, 2),
               "calmar": round(ann / abs(mdd), 2) if mdd else None,
               "n_trades": len(closed),
               "win_rate_pct": round(len(wins_tr) / len(closed) * 100, 1) if closed else None,
               "profit_factor": round(sum(t["pnl"] for t in wins_tr) / loss_sum, 2) if closed and loss_sum > 0 else None,
               "avg_invested_pct": round(sum(e["inv_pct"] for e in equity_curve) / len(equity_curve), 1),
               "n_trim": n_trim}
    return {"meta": {"config": asdict(cfg),
                     "execution_timeline": EXEC_TIMELINE[cfg.strategy],
                     "assumptions": ASSUMPTIONS[cfg.strategy]},
            "summary": summary, "equity_curve": equity_curve,
            "days": days, "holdings": holdings, "trades": trades}

# ============================================================
# run_job 高层入口（portal 线程 / CLI 共用）
# ============================================================
DEFAULTS = {"strategy": "chanlun", "d0": "20251013", "d1": "20261012",
            "top_n": 10, "slot_pct": 0.08, "cap_mode": "regime_8050",
            "runup_cap": 0.15, "stop_pct": 0.0, "min_holding_days": 0,
            "holding_max_days": 0}

def run_job(params: dict, job_id: str, progress=None):
    """参数 dict → 校验 → 信号 → 回测 → 落 data/bt2/<job_id>.json。返回 (result, error)。"""
    try:
        _ensure_loaded(progress)
        p = dict(DEFAULTS)
        p.update({k: v for k, v in (params or {}).items() if v not in (None, "")})
        strategy = p["strategy"] if p["strategy"] in ("chanlun", "double_knife") else "chanlun"
        d0, d1 = str(p["d0"]).replace("-", ""), str(p["d1"]).replace("-", "")
        if not (d0.isdigit() and d1.isdigit() and d0 < d1):
            return None, "窗口须为 8 位日期且 d0<d1"
        if d0 < D0_MIN:
            return None, f"窗口起点早于数据诚实上限 {D0_MIN}（273 体检）"
        last = cal_idx_all[-1] if cal_idx_all else d1
        d1 = min(d1, last)
        win = [d for d in cal_idx_all if d0 <= d <= d1]
        if len(win) < 20:
            return None, f"窗口内交易日不足（{len(win)} 日）"
        d0, d1 = win[0], win[-1]
        cfg = BacktestConfig(
            name={"chanlun": "缠论等机会轮动", "double_knife": "量子双刀日内纪律"}[strategy],
            strategy=strategy, d0=d0, d1=d1,
            sell_timing="T_close_1455" if strategy == "double_knife" else "T+1_open",
            min_holding_days=1 if strategy == "double_knife" else int(p["min_holding_days"] or 0),
            holding_max_days=5 if strategy == "double_knife" else int(p["holding_max_days"] or 0),
            top_n=max(1, min(50, int(p["top_n"] or 10))),
            slot_pct=max(0.01, min(0.5, float(p["slot_pct"] or 0.08))),
            cap_mode="fixed" if p["cap_mode"] == "fixed" else "regime_8050",
            runup_cap=float(p["runup_cap"] or 0) if strategy == "chanlun" else 0.0,
            stop_pct=float(p["stop_pct"] or 0),
        )
        if progress:
            progress("信号", 10, f"计算信号：{cfg.name} {d0}→{d1}")
        if strategy == "chanlun":
            sig_buy, sig_sell, src = calc_chanlun_signals(d0, d1, progress)
        else:
            sig_buy = calc_dknife_signals(d0, d1, progress=progress)
            sig_sell, src = {}, "自算(原始价·relaxed)"
        if progress:
            progress("回测", 65, "逐日执行中")
        result = run_backtest(cfg, sig_buy, sig_sell, progress)
        result["meta"]["signal_source"] = src
        result["meta"]["job_id"] = job_id
        os.makedirs(OUT_DIR, exist_ok=True)
        out = os.path.join(OUT_DIR, f"bt2_{job_id}.json")
        json.dump(result, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        if progress:
            progress("完成", 100, json.dumps(result["summary"], ensure_ascii=False))
        return result, None
    except Exception as e:
        import traceback
        return None, repr(e) + " | " + traceback.format_exc(limit=3)

# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="K-STOCK-285b 回测框架 v2")
    ap.add_argument("--strategy", default="chanlun", choices=["chanlun", "double_knife"])
    ap.add_argument("--d0", default="20251013")
    ap.add_argument("--d1", default="20261012")
    ap.add_argument("--top-n", type=int, default=10)
    ap.add_argument("--slot-pct", type=float, default=0.08)
    ap.add_argument("--cap-mode", default="regime_8050", choices=["regime_8050", "fixed"])
    ap.add_argument("--runup-cap", type=float, default=0.15)
    ap.add_argument("--stop-pct", type=float, default=0.0)
    ap.add_argument("--holding-max", type=int, default=0)
    ap.add_argument("--job-id", default=None)
    args = ap.parse_args()
    jid = args.job_id or ("cli-" + time.strftime("%Y%m%d-%H%M%S"))
    t0 = time.time()
    res, err = run_job(vars(args), jid, progress=lambda st, pc, note: print(f"[{st} {pc}%] {note}", flush=True))
    if err:
        print("FAIL:", err, flush=True)
        sys.exit(1)
    print(f"DONE bt2_{jid}.json summary={json.dumps(res['summary'], ensure_ascii=False)}（{round(time.time()-t0)}s）", flush=True)
