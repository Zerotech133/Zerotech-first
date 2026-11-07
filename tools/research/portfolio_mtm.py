# -*- coding: utf-8 -*-
"""共用组合引擎：全交易日历 + 盯市计价 + 现金约束（K-RES-20261108-ENGINE）。

为什么重写
----------
`exit_rules_ext.py` 原来的组合层有三个缺陷，每一个都会让"账户层"结论不可用：

1. **净值轴只含买点日**（`cal = sorted(set(df["entry"]))`）。结算日若不在买点日
   集合里，这笔收益就被静默丢弃。实测把累计净值虚高 7pp。
2. **只在 `exit == 前一买点日` 时结算**。退出日不是买点日 → 该笔永远挂着，
   资金永久死锁。实测 1.9% 的笔、9 个完整批次卡死。
3. **`eq_now = cash + Σ建仓成本`**，浮盈不参与复利，也不参与建仓预算。
   等于账户永远按成本价估值，牛市里越买越少。

本模块统一按「全市场交易日历 + 逐日盯市 + 结算日入账」实现，供出场规则对照、
限只数选股、入场择时三处复用，避免各脚本各有一套口径。

资金模型
--------
- 每个开仓日投入 `1/hold` 权益（资金预算制，不是槽位制）。
  槽位制会在信号扎堆日只建 9 批（实测），预算制才符合"每天都有钱要投"的事实。
- 日内多只等权，受现金约束（`amt = min(budget, cash)`）。
- 建仓日收盘起按收盘价盯市，到期日按退出价结算。
- 净值比一律走**净口径**：`(px*(1-卖费)) / (entry_px*(1+买费)) - 1`。
  这样建仓当天就体现 -0.2% 成本，且最后一天的净值比恒等于该笔净收益，
  不会出现"盯市值和结算值对不上"的跳变。

用法
----
    from portfolio_mtm import Engine, build_ratio

    eng = Engine(axis, hold=20)
    out = eng.run(picks)      # picks: {开仓日8位: [Pick, ...]}
"""
import numpy as np

BUY_COST = 0.00075
SELL_COST = 0.00125


def load_price_panel(codes, tdx_dir, start=None, end=None):
    """加载 ts_daily + ts_adj_factor，返回 {code: {"d", "c", "pos"}}。

    **c 必须是复权价 close*adj_factor。** 池的 entry_px/exit_px/ret 都是复权口径，
    用原始 close 建路径会在分红除权日跳空：实测 ratio[exit] 与池 ret 恒差约 1%
    （5-6 月除权高峰最重），止损规则会因假跌提前触发，把"止损有效"系统性夸大。
    adj_factor 是累计因子，标度与 entry_px 不同源，由调用方用
    k = entry_px / c[entry_i] 归一（build_ratio 内部完成）。
    """
    import glob
    import os

    import pandas as pd

    def _n(s):
        return (pd.Series(s).astype(str).str.replace("-", "", regex=False)
                .str.slice(0, 8))

    cs = set(codes)
    price, adj = [], []
    for p in sorted(glob.glob(os.path.join(tdx_dir, "ts_daily", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "close"])
        d["trade_date"] = _n(d["trade_date"])
        d = d[d["ts_code"].isin(cs)]
        if start:
            d = d[d["trade_date"] >= str(start)]
        if end:
            d = d[d["trade_date"] <= str(end)]
        if len(d):
            price.append(d)
    bars = (pd.concat(price, ignore_index=True)
            .drop_duplicates(["ts_code", "trade_date"]))
    for p in sorted(glob.glob(os.path.join(tdx_dir, "ts_adj_factor", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "adj_factor"])
        d["trade_date"] = _n(d["trade_date"])
        d = d[d["ts_code"].isin(cs)]
        if start:
            d = d[d["trade_date"] >= str(start)]
        if end:
            d = d[d["trade_date"] <= str(end)]
        if len(d):
            adj.append(d)
    if adj:
        a = (pd.concat(adj, ignore_index=True)
             .drop_duplicates(["ts_code", "trade_date"]))
        bars = bars.merge(a, on=["ts_code", "trade_date"], how="left")
        af = bars["adj_factor"].to_numpy(float)
        af = np.where(np.isfinite(af) & (af > 0), af, 1.0)
    else:
        af = np.ones(len(bars))
    bars["cp"] = bars["close"].to_numpy(float) * af
    bars = bars.sort_values(["ts_code", "trade_date"])
    out = {}
    for code, g in bars.groupby("ts_code", sort=False):
        dts = g["trade_date"].tolist()
        out[code] = {"d": dts, "c": g["cp"].to_numpy(float),
                     "pos": {d: i for i, d in enumerate(dts)}}
    return out


class Pick(object):
    """一笔待建仓信号。

    ratio:   {axis_idx: 净值比}，从开仓日到退出日，停牌日沿用前值
    exit_i: 退出日对应的 axis 下标
    """
    __slots__ = ("code", "entry_i", "exit_i", "ratio", "ret", "tag")

    def __init__(self, code, entry_i, exit_i, ratio, ret, tag=None):
        self.code = code
        self.entry_i = entry_i
        self.exit_i = exit_i
        self.ratio = ratio
        self.ret = ret
        self.tag = tag


def build_ratio(p, ei, xi, entry_px, exit_px, axis, axis_pos,
                buy_cost=BUY_COST, sell_cost=SELL_COST):
    """把一只票的收盘路径压成「相对 entry_px 的净净值比」。

    p    : {"d": [...], "c": [...], "pos": {date: idx}}
    ei   : entry 在该票序列中的下标
    xi   : exit  在该票序列中的下标
    axis : 全交易日历（8 位字符串列表）
    返回 {axis_idx: ratio}，ratio 已扣双边成本。
    """
    base = entry_px * (1.0 + buy_cost)
    ai = axis_pos[p["d"][ei]]
    aj = axis_pos[p["d"][xi]]
    k = entry_px / p["c"][ei]
    out = {}
    # 开仓日：收盘价 == entry_px，ratio 即买入成本
    out[ai] = (entry_px * (1.0 - sell_cost)) / base - 1.0
    last = out[ai]
    for ad in range(ai + 1, aj + 1):
        pi = p["pos"].get(axis[ad])
        if pi is not None and ei < pi <= xi:
            last = (p["c"][pi] * k * (1.0 - sell_cost)) / base - 1.0
        out[ad] = last
    # 退出日以退出价为准（止损/止盈可能不在收盘）
    out[aj] = (exit_px * (1.0 - sell_cost)) / base - 1.0
    return out


class Engine(object):
    def __init__(self, axis, hold=20, buy_cost=BUY_COST, sell_cost=SELL_COST):
        self.axis = list(axis)
        self.pos = {d: i for i, d in enumerate(self.axis)}
        self.hold = int(hold)
        self.buy_cost = buy_cost
        self.sell_cost = sell_cost

    def run(self, picks, budget_frac=None, max_slots=None, verbose=False):
        """picks: {开仓日8位: [Pick, ...]}，开仓日必须在 axis 内。

        budget_frac: 每日投入权益比例，默认 1/hold
        max_slots  : 同时在途笔数上限，None=不限；超限则按给定顺序截断
                     （调用方负责先排序，引擎不重排）
        """
        n = len(self.axis)
        budget_frac = (1.0 / self.hold) if budget_frac is None else float(budget_frac)

        # 按开仓日预分组，只留 axis 内的
        by_day = {}
        for d, xs in picks.items():
            i = self.pos.get(str(d).replace("-", "")[:8])
            if i is not None and xs:
                by_day[i] = xs

        cash = 1.0
        live = []          # [amt, entry_i, exit_i, ratio]
        curve = np.empty(n, dtype=float)
        util = np.empty(n, dtype=float)
        n_batch = 0
        n_skip = 0
        n_drop = 0

        for i in range(n):
            # 1) 先结算：今日到期（exit_i == i）的持仓按净值比回收
            if live:
                keep = []
                for h in live:
                    if h[2] == i:
                        cash += h[0] * (1.0 + h[3][i])
                    else:
                        keep.append(h)
                live = keep

            # 2) 再建仓：今日开仓，投入 1/hold 权益，日内等权
            xs = by_day.get(i)
            if xs:
                if max_slots is not None:
                    room = max_slots - len(live)
                    if room <= 0:
                        n_drop += len(xs)
                        xs = None
                    elif len(xs) > room:
                        n_drop += len(xs) - room
                        xs = xs[:room]
                if xs:
                    eq_now = cash + sum(h[0] * (1.0 + h[3][i]) for h in live)
                    amt = min(budget_frac * eq_now, cash)
                    if amt > 1e-9:
                        per = amt / len(xs)
                        for pk in xs:
                            live.append([per, i, pk.exit_i, pk.ratio])
                            cash -= per
                        n_batch += 1
                    else:
                        n_skip += 1

            # 3) 收盘盯市
            eq = cash + sum(h[0] * (1.0 + h[3][i]) for h in live)
            curve[i] = eq
            util[i] = (eq - cash) / eq if eq > 0 else 0.0

        return self._stats(curve, util, n_batch, n_skip, n_drop, live, cash)

    def _stats(self, curve, util, n_batch, n_skip, n_drop, live, cash):
        e = curve
        ret = np.diff(e) / e[:-1]
        sd = ret.std(ddof=1) if len(ret) > 1 else 0.0
        peak = np.maximum.accumulate(e)
        mdd = float((e / peak - 1.0).min() * 100) if len(e) else 0.0
        yrs = len(e) / 244.0
        total = (e[-1] - 1.0) * 100 if len(e) else 0.0
        cagr = ((e[-1] ** (1.0 / yrs) - 1.0) * 100) if (yrs > 0 and e[-1] > 0) else None
        return {
            "total": round(float(total), 2),
            "cagr": round(float(cagr), 2) if cagr is not None else None,
            "mdd": round(mdd, 2),
            "sharpe": round(float(ret.mean() / sd * np.sqrt(244)), 2) if sd else 0.0,
            "vol": round(float(sd * np.sqrt(244) * 100), 2),
            "util": round(float(util.mean() * 100), 1),
            "days": int(len(e)),
            "n_batch": n_batch,
            "n_skip": n_skip,
            "n_drop": n_drop,
            "left_open": len(live),
            "left_cash": round(float(cash), 4),
            "curve": [round(float(x), 5) for x in e] if len(e) <= 4000 else None,
        }
