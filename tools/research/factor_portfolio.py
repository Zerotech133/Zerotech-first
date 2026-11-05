"""因子组合回测：中性化因子 + 换手率/可交易性约束的多空组合。

研究用途。核心约定：

- 信号在 T 日收盘生成，T+1 日收盘执行（与 factor_lib 的 20 日 CAR 口径一致）。
- 组合为 dollar-neutral 多空：多头 0.5 资金、空头 0.5 资金，合计 gross=1。
- 每个调仓日按因子分位选多头/空头，等权；默认每 20 个交易日调仓。
- 可交易性约束：ST/退市、停牌、涨跌停、成交额、换手率区间、上市天数、价格。
- 换手率约束：组合单边换手上限（默认 50%/次），超出时按比例向目标权重靠拢。
- 缓冲带：已有持仓只要仍在扩展分位（如 30%）内就保留，减少边界换手；新开仓仍要求进入
  最极端分位（默认 20%）。
- 换手惩罚：对目标权重与当前权重的差异做收缩（partial adjustment），再按多空两腿
  归一化回 gross，降低无谓换手。
- 成本按单边换手 × 费率扣除；支持 0/10/30/50bp 敏感性。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
import numpy as np
import pandas as pd


@dataclass
class PortfolioConfig:
    rebalance_days: int = 20
    quantile: float = 0.2
    buffer_quantile: float | None = None  # 已有持仓保留的扩展分位（如 0.3）
    turnover_penalty: float = 0.0         # 0~1，越大越向当前权重收缩
    cost_bp: float = 30.0
    max_one_way_turnover: float | None = 1.0  # None/>=1 表示不约束
    min_amount: float = 5e7
    min_turnover: float = 0.2   # 换手率单位为 %：0.2%
    max_turnover: float = 20.0  # 20%
    exclude_st: bool = True
    exclude_limit: bool = True
    min_price: float = 2.0
    min_list_days: int = 250
    gross: float = 1.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["cost_rate"] = float(self.cost_bp) / 10000.0
        return d


def _limit_threshold(codes: pd.Index, st: pd.Series | None = None) -> np.ndarray:
    """按板块返回涨跌停阈值（%）。ST 单独 5%。"""
    arr = np.full(len(codes), 10.0, dtype=float)
    for i, code in enumerate(codes):
        c = str(code)
        if c.endswith(".BJ"):
            arr[i] = 30.0
        elif c.startswith(("688", "689")):
            arr[i] = 20.0
        elif c.startswith(("300", "301")):
            arr[i] = 20.0
    if st is not None:
        arr[np.asarray(st.fillna(False).astype(bool))] = 5.0
    return arr


def _st_flag(codes: pd.Index, names: dict[str, str]) -> pd.Series:
    flags = []
    for code in codes:
        name = str(names.get(str(code), ""))
        flags.append(("ST" in name.upper()) or ("退" in name))
    return pd.Series(flags, index=codes, dtype=bool)


def investable_masks(panel, names: dict[str, str], cfg: PortfolioConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    """返回 (selection_mask, trade_mask)。

    selection_mask 用 T 日信息选择股票；trade_mask 用 T+1 日信息判断能否成交。
    """
    dates, codes = panel.dates, panel.codes
    st = _st_flag(codes, names)
    limits = _limit_threshold(codes, st)

    close = panel.fields["close"]
    amount = panel.fields["amount"]
    vol = panel.fields["vol"]
    pct = panel.fields["pct_chg"]
    turnover = panel.fields["turnover_rate"] if "turnover_rate" in panel.fields else panel.optional("turnover_rate")
    total_mv = panel.fields.get("total_mv")
    eligible = panel.eligible_mask()

    base = eligible.copy()
    if st is not None and cfg.exclude_st:
        base &= ~st.to_numpy()[None, :]
    base &= (close >= cfg.min_price)
    if total_mv is not None:
        base &= (total_mv > 0)
    base &= (amount * 1000.0 >= cfg.min_amount)
    base &= (vol > 0)
    base &= (turnover >= cfg.min_turnover) & (turnover <= cfg.max_turnover)
    if cfg.exclude_limit:
        lim = np.tile(limits, (len(dates), 1))
        base &= (pct.abs() < (lim - 0.30))

    trade = base.copy()
    if cfg.exclude_limit:
        lim = np.tile(limits, (len(dates), 1))
        trade &= (pct.abs() < (lim - 0.30))
    return base, trade


def rebalance_indices(panel, start: str | None, end: str | None, every: int) -> list[int]:
    dates = [str(d) for d in panel.dates]
    lo = 0 if start is None else int(np.searchsorted(dates, str(start), side="left"))
    hi = len(dates) - 1 if end is None else int(np.searchsorted(dates, str(end), side="left"))
    out = []
    i = lo
    while i <= hi:
        if i + 1 < len(dates):
            out.append(i)
        i += int(every)
    return out


def _target_weights(factor_row: pd.Series, mask_row: pd.Series,
                    cfg: PortfolioConfig, prior: pd.Series | None = None) -> pd.Series:
    """按分位构造目标权重；prior 非空时应用缓冲带保留已有持仓。"""
    valid = factor_row[mask_row.reindex(factor_row.index).fillna(False)]
    valid = valid[np.isfinite(valid.astype(float))]
    n = len(valid)
    if n < max(10, int(2 / cfg.quantile)):
        return pd.Series(dtype=float)
    k = max(1, int(np.floor(n * cfg.quantile)))
    ordered = valid.sort_values(ascending=False)
    longs = set(ordered.index[:k])
    shorts = set(ordered.index[-k:])

    if prior is not None and cfg.buffer_quantile:
        rank_pct = valid.rank(pct=True)
        keep_long = prior[(prior > 0) & (prior.index.isin(valid.index))].index
        keep_short = prior[(prior < 0) & (prior.index.isin(valid.index))].index
        for code in keep_long:
            if rank_pct.loc[code] >= 1.0 - cfg.buffer_quantile:
                longs.add(code)
        for code in keep_short:
            if rank_pct.loc[code] <= cfg.buffer_quantile:
                shorts.add(code)
        # 极端情况下同一标的可能同时满足两侧；以打分方向为准
        overlap = longs & shorts
        for code in list(overlap):
            if factor_row.loc[code] >= factor_row[list(valid.index)].median():
                shorts.discard(code)
            else:
                longs.discard(code)

    w = pd.Series(0.0, index=factor_row.index, dtype=float)
    if longs:
        w.loc[list(longs)] = (cfg.gross / 2.0) / len(longs)
    if shorts:
        w.loc[list(shorts)] = -(cfg.gross / 2.0) / len(shorts)
    return w


def _apply_turnover_penalty(target: pd.Series, prior: pd.Series, cfg: PortfolioConfig) -> pd.Series:
    """把目标向当前持仓收缩，再按多空两腿归一化回 gross。"""
    if cfg.turnover_penalty <= 0 or prior.empty or target.empty:
        return target
    penalty = float(np.clip(cfg.turnover_penalty, 0.0, 1.0))
    all_idx = target.index.union(prior.index)
    tgt = target.reindex(all_idx).fillna(0.0)
    cur = prior.reindex(all_idx).fillna(0.0)
    blended = cur + (1.0 - penalty) * (tgt - cur)
    pos = blended.clip(lower=0.0)
    neg = blended.clip(upper=0.0)
    gross = float(cfg.gross)
    if float(pos.sum()) > 1e-12:
        pos = pos * (gross / 2.0) / float(pos.sum())
    if float(neg.abs().sum()) > 1e-12:
        neg = neg * (gross / 2.0) / float(neg.abs().sum())
    return (pos + neg).reindex(target.index).fillna(0.0)


def _drift_weights(w: pd.Series, rets: pd.DataFrame, start: int, end: int) -> pd.Series:
    """从 start 到 end（不含 end）按日收益漂移权重。"""
    cur = w.copy()
    for i in range(start, end):
        r = rets.iloc[i].reindex(cur.index).fillna(0.0)
        cur = cur * (1.0 + r.to_numpy(dtype=float))
    return cur


def simulate(panel, factor: pd.DataFrame, cfg: PortfolioConfig | None = None,
             names: dict[str, str] | None = None, start: str | None = None,
             end: str | None = None) -> dict:
    """执行组合回测，返回净值、交易、指标与配置。"""
    cfg = cfg or PortfolioConfig()
    names = names or {}
    factor = factor.reindex(index=panel.dates, columns=panel.codes)
    adj = panel.fields["adj_close"].reindex(index=panel.dates, columns=panel.codes)
    rets = adj.pct_change().iloc[1:]
    sel_mask, trade_mask = investable_masks(panel, names, cfg)
    idxs = rebalance_indices(panel, start, end, cfg.rebalance_days)
    cost_rate = float(cfg.cost_bp) / 10000.0

    nav = 1.0
    nav_dates: list[str] = []
    nav_values: list[float] = []
    daily_rets: list[float] = []
    trades: list[dict] = []
    w = pd.Series(dtype=float)
    prev_entry: int | None = None

    for pos, i in enumerate(idxs):
        entry = i + 1
        nxt = idxs[pos + 1] + 1 if pos + 1 < len(idxs) else len(panel.dates) - 1
        if nxt <= entry:
            continue
        if w.empty:
            prior = pd.Series(0.0, index=panel.codes, dtype=float)
        else:
            prior = _drift_weights(w, rets, prev_entry + 1, entry)
        target = _target_weights(
            factor.iloc[i], sel_mask.iloc[i] & trade_mask.iloc[entry], cfg,
            prior=None if w.empty else prior,
        )
        if target.empty:
            continue
        target = _apply_turnover_penalty(target, prior, cfg)
        all_idx = target.index.union(prior.index)
        tgt = target.reindex(all_idx).fillna(0.0)
        base_w = prior.reindex(all_idx).fillna(0.0)
        one_way = float(0.5 * (tgt - base_w).abs().sum())
        alpha = 1.0
        if cfg.max_one_way_turnover is not None and one_way > cfg.max_one_way_turnover > 0:
            alpha = float(cfg.max_one_way_turnover) / one_way
        traded = base_w + alpha * (tgt - base_w)
        actual_turn = float(0.5 * (traded - base_w).abs().sum())
        cost = actual_turn * cost_rate
        trades.append({
            "signal_date": str(panel.dates[i]), "entry_date": str(panel.dates[entry]),
            "target_turnover": one_way, "actual_turnover": actual_turn,
            "alpha": alpha, "cost": cost,
            "n_long": int((traded > 0).sum()), "n_short": int((traded < 0).sum()),
            "gross": float(traded.abs().sum()),
        })
        w = traded
        nav *= (1.0 - cost)
        # entry 日收盘建仓；下一交易日起计组合收益
        for d in range(entry + 1, nxt + 1):
            if d >= len(panel.dates):
                break
            r = rets.iloc[d - 1].reindex(w.index).fillna(0.0)
            port_r = float((w * r).sum())
            w = w * (1.0 + r.to_numpy(dtype=float))
            nav *= (1.0 + port_r)
            nav_dates.append(str(panel.dates[d]))
            nav_values.append(nav)
            daily_rets.append(port_r)
        prev_entry = entry

    nav_ser = pd.Series(nav_values, index=pd.Index(nav_dates, name="trade_date"), dtype=float)
    ret_ser = pd.Series(daily_rets, index=nav_ser.index, dtype=float)
    bench = panel.market_daily_return().reindex(nav_ser.index).fillna(0.0)
    metrics = summarize(nav_ser, ret_ser, bench, trades, cfg)
    return {"config": cfg.to_dict(), "metrics": metrics,
            "trades": trades, "nav": nav_ser, "daily_returns": ret_ser,
            "benchmark": bench}


def summarize(nav: pd.Series, rets: pd.Series, bench: pd.Series, trades: list[dict],
              cfg: PortfolioConfig) -> dict:
    n = len(rets)
    ann = float(nav.iloc[-1] ** (252.0 / n) - 1.0) if n and len(nav) else None
    vol = float(rets.std(ddof=0) * np.sqrt(252.0)) if n else None
    sharpe = float(ann / vol) if ann is not None and vol and vol > 1e-12 else None
    peak = nav.expanding().max()
    mdd = float((nav / peak - 1.0).min()) if len(nav) else None
    bench_nav = (1.0 + bench).cumprod()
    bench_ann = float(bench_nav.iloc[-1] ** (252.0 / n) - 1.0) if n else None
    years = n / 252.0 if n else None
    total_turn = float(sum(t["actual_turnover"] for t in trades))
    return {
        "days": int(n), "years": round(years, 4) if years else None,
        "ann_return": None if ann is None else round(ann, 6),
        "ann_vol": None if vol is None else round(vol, 6),
        "sharpe": None if sharpe is None else round(sharpe, 4),
        "max_drawdown": None if mdd is None else round(mdd, 6),
        "bench_ann_return": None if bench_ann is None else round(bench_ann, 6),
        "excess_ann_return": None if (ann is None or bench_ann is None) else round(ann - bench_ann, 6),
        "win_rate": round(float((rets > 0).mean()), 4) if n else None,
        "rebalances": len(trades),
        "avg_one_way_turnover": round(total_turn / len(trades), 4) if trades else 0.0,
        "ann_one_way_turnover": round(total_turn / years, 4) if years and trades else None,
        "total_cost": round(sum(t["cost"] for t in trades), 6),
        "cost_bp": cfg.cost_bp,
        "max_one_way_turnover": cfg.max_one_way_turnover,
        "quantile": cfg.quantile,
        "buffer_quantile": cfg.buffer_quantile,
        "turnover_penalty": cfg.turnover_penalty,
        "rebalance_days": cfg.rebalance_days,
        "gross": cfg.gross,
    }
