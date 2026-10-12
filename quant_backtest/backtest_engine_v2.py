# -*- coding: utf-8 -*-
"""
K-STOCK-283 · 回测框架 v2 原型（配置驱动 · 执行时间轴 · 仓位管理一体化）
老田 10-13 07:42 口径修正，本版落实：
  ① 缠论 ≠ 当天满仓。缠论 = 选出股票后"等机会"：买点确认(T 收盘) → 次日开盘(T+1 open)买入；
     总仓位受 regime 帽约束 + slot 制分批；卖点确认 → T+1 open 卖出，资金回池等下一个买点；
     runup 帽（T+1 open 相对 T close >15% 放弃该机会，263 实锤追高是主凶）。
  ② 量子双刀 = 日内纪律：T 14:45 选股（用 T 日 bar）→ T 14:55 只卖不买（盘中禁买入）
     → 买入 T+1 开盘（避免用当日收盘价成交的前视）→ 最早 T+2 可卖（A股 T+1 制度，
     "第二天早盘可卖"口径）；信号消失 / 持有超上限 / 灾难止损 → 当日 14:55（收盘价）离场。
两策略 = 同一引擎两组 BacktestConfig。窗口 20251013→20261012（242 交易日，与 271/277 可比）。
缠论信号复用 277 账本 seen-ever 口径（sig_buy/sig_sell，零重算）；双刀信号现算（hfq，263 铁律）。
"""
import sys, os, json, time
import pandas as pd
from dataclasses import dataclass, asdict

BASE = r"E:/老田的AI助手"
DATA = os.path.join(BASE, "quant_backtest", "data", "ashare")
POOL_SRC = os.path.join(BASE, "k_stock", "data", "poc_adapter_chanlun_20261012.json")
LEDGER = os.path.join(BASE, "quant_backtest", "data", "daily_ledger", "ledger_20251013_20261012.json")
OUT_DIR = os.path.join(BASE, "quant_backtest", "data", "daily_ledger")
sys.path.insert(0, os.path.join(BASE, "quant_backtest"))
sys.path.insert(0, BASE)

D0_WIN, D1_WIN = "20251013", "20261012"
COST = 0.0015

# ============================================================
# BacktestConfig —— 11 参数空间（283 定档）
# ============================================================
@dataclass
class BacktestConfig:
    name: str
    strategy: str              # chanlun | double_knife
    d0: str = D0_WIN
    d1: str = D1_WIN
    buy_timing: str = "T+1_open"        # 买入执行时点
    sell_timing: str = "T+1_open"       # 卖出执行时点: T+1_open | T_close_1455
    min_holding_days: int = 0           # 最短持有交易日（双刀=1，T+1 制度）
    holding_max_days: int = 0           # 0=不限；双刀=5 强制离场
    top_n: int = 10                     # 名义持仓槽数 / 单日新开仓上限
    slot_pct: float = 0.08              # 单票目标仓位（占权益）
    cap_mode: str = "regime_8050"       # regime_8050 | fixed
    cap_fixed: float = 0.80
    runup_cap: float = 0.0              # T+1 open 相对 T close 涨幅帽（缠论=0.15）
    stop_pct: float = 0.0               # 个股灾难止损（收盘判定；0=不启用）
    rebalance_every: int = 0            # 0=信号驱动不定期再平衡
    cost: float = COST

    def cap_of(self, regime_cap: float) -> float:
        return regime_cap if self.cap_mode == "regime_8050" else self.cap_fixed

CFG_CHANLUN = BacktestConfig(
    name="缠论等机会轮动", strategy="chanlun",
    buy_timing="T+1_open", sell_timing="T+1_open",
    min_holding_days=0, holding_max_days=0,
    top_n=10, slot_pct=0.08,
    cap_mode="regime_8050", runup_cap=0.15, stop_pct=0.0, rebalance_every=0,
)
CFG_DKNIFE = BacktestConfig(
    name="量子双刀日内纪律", strategy="double_knife",
    buy_timing="T+1_open", sell_timing="T_close_1455",
    min_holding_days=1, holding_max_days=5,
    top_n=10, slot_pct=0.10,
    cap_mode="regime_8050", runup_cap=0.0, stop_pct=-0.08, rebalance_every=0,
)

# ============================================================
# 数据装载（hfq 后复权，263a 铁律：pct_chg 因子链）
# ============================================================
pool = json.load(open(POOL_SRC, encoding="utf-8"))
stocks = sorted({x["stock"] for x in pool})
books, rets = {}, {}
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

def px(s, day, kind):                 # hfq 价查询；停牌/无数据返回 None
    b = books.get(s)
    if b is None:
        return None
    row = b[b.trade_date == day]
    if row.empty:
        return None
    return float(row.iloc[0]["adj_" + kind])

cal_all = sorted(set().union(*[set(b["trade_date"]) for b in books.values()]))
cal_idx_all = [d for d in cal_all if d >= "20231010"]
cal = [d for d in cal_idx_all if D0_WIN <= d <= D1_WIN]
print(f"池 {len(books)} 只 · 窗口 {cal[0]}→{cal[-1]} 共 {len(cal)} 交易日", flush=True)

# ---------- 池等权指数 + regime（D-1 判定） ----------
idx_ret = {}
for day in cal_idx_all:
    rs = [rets[s].get(day) for s in books]
    rs = [x for x in rs if x is not None and not pd.isna(x)]
    idx_ret[day] = sum(rs) / len(rs) if rs else 0.0
idx_nav = (1 + pd.Series(idx_ret).sort_index()).cumprod()
ma20 = idx_nav.rolling(20).mean()
ma60 = idx_nav.rolling(60).mean()

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
# 信号源
# ============================================================
def load_chanlun_signals():
    """从 277 账本 actions 抽 seen-ever 缠论信号（零重算）。返回 buy/sell 双通道。"""
    j = json.load(open(LEDGER, encoding="utf-8"))
    sig_buy, sig_sell = {}, {}
    for day, d in j["days"].items():
        bs, ss = set(), set()
        for a in d.get("actions", []):
            if a["type"] == "sig_buy":
                bs.add(a["stock"])
            elif a["type"] == "sig_sell":
                ss.add(a["stock"])
        sig_buy[day], sig_sell[day] = bs, ss
    print(f"缠论信号（277 账本 seen-ever）：buy {sum(len(v) for v in sig_buy.values())} 只次，"
          f"sell {sum(len(v) for v in sig_sell.values())} 只次", flush=True)
    return sig_buy, sig_sell

def calc_dknife_signals():
    """量子双刀逐日信号：agent_platform 引擎 × 原始价 K 线。
    口径实证（10-13 探查）：双刀引擎含 6-200 元绝对价格门槛与 20 日成交额实务门槛，
    必须吃原始价——hfq 下信号全灭（strict 0 / raw strict 8 只次 / raw relaxed 33 只次）。
    与缠论 hfq 口径不同属引擎语义差异：缠论怕除权假跳（结构形态），双刀是实务价格约束。
    T 日 14:45 口径近似=T 日 bar。"""
    from agent_platform.engine.quant_double_knife import quant_double_knife
    sig = {}
    for s, b in books.items():
        df = pd.DataFrame({
            "open": b["open"], "high": b["high"], "low": b["low"],
            "close": b["close"], "amount": b["amount"],
        })
        out = quant_double_knife(df, profile="relaxed")
        mask = out["double_knife_signal"].fillna(False)
        for day in b.loc[mask, "trade_date"]:
            sig.setdefault(day, set()).add(s)
    sig = {d: v for d, v in sorted(sig.items()) if D0_WIN <= d <= D1_WIN}
    print(f"双刀信号：{sum(len(v) for v in sig.values())} 只次 / {len(sig)} 日", flush=True)
    return sig

# ============================================================
# 统一引擎（配置驱动的执行时间轴）
# ============================================================
def run_backtest(cfg: BacktestConfig, sig_buy: dict, sig_sell: dict):
    cash, pos = 1.0, {}
    days, equity_curve, trades, holdings = {}, [], [], {}
    pending_buy, pending_sell = set(), set()      # T 日确认 → 执行日执行
    holding_since = {}                            # stock -> 首次盯市日序号
    n_trim = 0
    prev_eq = 1.0

    def rec(day, side, s, how, pnl=None):
        trades.append({"day": day, "stock": s, "side": side, "how": how,
                       "note": "", "pnl": None if pnl is None else round(pnl * 100, 2)})

    for di, day in enumerate(cal):
        prev_day = cal[di - 1] if di > 0 else None
        actions = []

        # ---------- A1. 开盘卖出（T+1_open 口径，停牌顺延不丢单） ----------
        still_pending_sell = set()
        for s in sorted(pending_sell):
            p = pos.get(s)
            o = px(s, day, "open")
            if p is None:
                continue
            if o is None:
                still_pending_sell.add(s)
                continue                            # 停牌：留队次日再试
            val_exit = p["val"] * o / p["ref_px"]
            pnl = val_exit / p["cost"] - 1
            cash += val_exit * (1 - cfg.cost)
            del pos[s]; holding_since.pop(s, None)
            actions.append({"type": "sell", "stock": s, "note": "T+1 开盘离场",
                            "pnl_pct": round(pnl * 100, 2)})
            rec(day, "SELL", s, "exit_open", pnl)
        pending_sell = still_pending_sell

        # ---------- A2. 开盘买入（统一 T+1 open；runup 帽 / 总仓位帽 / slot） ----------
        cap, regime_lv, regime_why = regime_of(day)
        capv = cfg.cap_of(cap)
        eqv_open = cash + sum(p["val"] for p in pos.values())
        deployed = eqv_open - cash
        still_pending_buy = set()
        n_buy_today = 0
        for s in sorted(pending_buy):
            if n_buy_today >= cfg.top_n:
                still_pending_buy.add(s)
                continue
            if s in pos:
                continue
            o = px(s, day, "open")
            if o is None:
                still_pending_buy.add(s)
                continue                            # 停牌顺延
            # runup 帽：T+1 open 相对 T close 追高 >15% → 放弃该机会（资金回池）
            if cfg.runup_cap > 0 and prev_day:
                c_prev = px(s, prev_day, "close")
                if c_prev and o / c_prev - 1 > cfg.runup_cap:
                    actions.append({"type": "skip", "stock": s,
                                    "note": f"runup帽：开盘较昨收 +{(o/c_prev-1)*100:.1f}% >{cfg.runup_cap:.0%}，放弃"})
                    continue
            amt = min(cash, eqv_open * cfg.slot_pct, max(0.0, eqv_open * capv - deployed))
            if amt < 0.002 * eqv_open:
                continue                            # 帽内无额度：机会放弃
            cash -= amt
            pos[s] = {"val": amt * (1 - cfg.cost), "cost": amt * (1 - cfg.cost),
                      "ref_px": o}
            holding_since[s] = di
            deployed += amt; n_buy_today += 1
            actions.append({"type": "buy", "stock": s,
                            "note": f"{cfg.buy_timing} 买入 {amt / eqv_open:.1%}"})
            rec(day, "BUY", s, cfg.strategy)
        pending_buy = still_pending_buy

        # ---------- B. 盯市 open→close ----------
        for s, p in pos.items():
            c = px(s, day, "close")
            if c is not None:
                p["val"] *= c / p["ref_px"]
                p["ref_px"] = c
        eqv = cash + sum(p["val"] for p in pos.values())

        # ---------- C. regime 超帽 → 收盘等比减（trim） ----------
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
            actions.append({"type": "trim", "note": f"regime={regime_lv} 超帽：等比减仓 {ratio:.0%}（目标仓位 {capv:.0%}）"})
            eqv = cash + sum(p["val"] for p in pos.values())

        # ---------- D. 收盘事件：信号确认 / 双刀 14:55 决策 ----------
        if cfg.strategy == "chanlun":
            # 卖点 T 收盘确认 → T+1 open 卖
            for s in sorted(sig_sell.get(day, set())):
                if s in pos:
                    pending_sell.add(s)
                    actions.append({"type": "note", "stock": s, "note": "卖点确认→明日开盘卖"})
            # 买点 T 收盘确认 → T+1 open 买（等机会，非满仓）
            for s in sorted(sig_buy.get(day, set())):
                if s not in pos and s not in pending_buy:
                    pending_buy.add(s)
                    actions.append({"type": "note", "stock": s, "note": "买点确认→明日开盘候选"})
        else:
            # 新买点 → T+1 open 买
            for s in sorted(sig_buy.get(day, set())):
                if s not in pos and s not in pending_buy:
                    pending_buy.add(s)
                    actions.append({"type": "note", "stock": s, "note": "14:45 选出→明日开盘买入"})
            # 14:55 卖出决策：信号消失（≥min_holding）/ 持有到期 / 灾难止损 → 收盘价离场
            for s in list(pos):
                p = pos[s]
                held = di - holding_since[s]
                if held < cfg.min_holding_days:
                    continue                        # T+1 制度保护
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

        # ---------- E. 记账 ----------
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

EXEC_TIMELINE = {
    "chanlun": ["T 收盘：缠论买点确认（seen-ever·hfq）", "T+1 开盘：买入（runup帽>15%放弃该机会）",
                "T 收盘：卖点确认", "T+1 开盘：卖出，资金回池等下一个买点",
                "组合层：regime 80/50/30 连续调仓（D-1 判定）+ 超帽等比减仓"],
    "double_knife": ["T 14:45：选股引擎跑双刀信号（十字星2连+MACD多头+均线多头+regime非空）",
                     "T 14:55：只卖不买（信号消失/持有到期/灾难止损，收盘价离场）",
                     "T 盘中：禁止买入", "T+1 开盘：买入新选出的票（slot 10% · 总仓位帽内）",
                     "T+2 起：可卖（T+1 制度），14:55 决策当日收盘执行"],
}
ASSUMPTIONS = {
    "chanlun": ["信号=T 收盘确认的 seen-ever fresh 口径（277 账本复用，零重算）",
                "成交价=T+1 开盘（hfq）；15bp 双边成本", "无个股硬止损（269 实锤止损负贡献）"],
    "double_knife": ["信号=原始价 K 线（引擎 6-200 元/成交额为实务门槛，hfq 全灭·10-13 实证）；结算仍走 hfq 因子链",
                     "14:45 选股用 T 日 bar 近似（盘中快照数据未入库，包 C 升级项）",
                     "买入=T+1 开盘而非 T 14:55（避免用当日收盘价成交的前视）",
                     "卖出=当日收盘价近似 14:55；min_holding=1 日对齐 A 股 T+1 制度",
                     "regime 帽暂用池等权指数（全市场指数等包 C 指数移出后切换）"],
}

# ============================================================
# main
# ============================================================
if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    t0 = time.time()
    sig_buy, sig_sell = load_chanlun_signals()
    out_c = run_backtest(CFG_CHANLUN, sig_buy, sig_sell)
    json.dump(out_c, open(os.path.join(OUT_DIR, "bt2_chanlun_wait.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"① {CFG_CHANLUN.name}: {json.dumps(out_c['summary'], ensure_ascii=False)}（{round(time.time()-t0)}s）", flush=True)

    dk = calc_dknife_signals()
    out_d = run_backtest(CFG_DKNIFE, dk, {})
    json.dump(out_d, open(os.path.join(OUT_DIR, "bt2_double_knife.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"② {CFG_DKNIFE.name}: {json.dumps(out_d['summary'], ensure_ascii=False)}（{round(time.time()-t0)}s）", flush=True)
    print("DONE", flush=True)
