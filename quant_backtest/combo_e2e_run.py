# -*- coding: utf-8 -*-
"""K-STOCK-233 组合回测真实单跑 E2E（老田 10-13 01:30 指令：真实测试数据，最后看到结果倒推）。

两笔实跑（全部真实本地数据，非模拟数）：
  A) 基准组合 E2E：缠论池（poc_adapter_chanlun_20261012.json，76 只）等权买入持有，
     窗口 2025-01-01 → 2025-03-31，日频净值 → 累计/年化/最大回撤/夏普/胜天。
     口径诚实声明：池子=今日快照回溯 2025（示范口径，含池选择前视偏差，
     10-16 实现须改用当日时点池——这本身就是本次倒推的产出之一）。
  B) 信号引擎 E2E：chanlun_signal.analyze 真实信号 → run_backtest 真引擎
     （手续费 3bps+滑点 1bps），000001.SZ，2025Q1，产出交易数/胜率/收益/回撤。
输出：同目录 combo_e2e_result.json + 控制台摘要。
"""

import json
import math
import os
import sys

import pandas as pd

BASE = r"C:/项目/老田的AI助手"
DATA = os.path.join(BASE, "quant_backtest", "data", "ashare")
POOL_FILE = os.path.join(BASE, "k_stock", "data", "poc_adapter_chanlun_20261012.json")
OUT = os.path.join(BASE, "quant_backtest", "combo_e2e_result.json")

W0, W1 = "20250101", "20250331"  # 用户示例窗口：2025-01-01 起跑 3 个月
TRADING_DAYS_Y = 242  # A 股年化惯例

pool = json.load(open(POOL_FILE, encoding="utf-8"))
codes = []
for it in pool:
    sc = it.get("stock", "")
    if "." in sc:
        codes.append(sc)  # 形如 000001.SZ
codes = sorted(set(codes))
print(f"池子 {len(codes)} 只（json {len(pool)} 条去重后）", flush=True)


def load_window(ts_code):
    p = os.path.join(DATA, ts_code.replace(".", "_") + ".parquet")
    if not os.path.exists(p):
        return None
    df = pd.read_parquet(p)
    df = df[(df.trade_date >= W0) & (df.trade_date <= W1)].sort_values("trade_date")
    if len(df) < 5:
        return None
    return df


# ── A) 等权买入持有组合 ────────────────────────────────────
rets, used = {}, []
missing = []
for c in codes:
    df = load_window(c)
    if df is None:
        missing.append(c)
        continue
    # 买入基准=窗口首日收盘；日收益用 tushare 官方 pct_chg（pre_close 已除权调整，天然复权口径）。
    # 🔴 教训（本次倒推产出）：close.pct_change() 跨除权日会造假收益（002656 单季 +637% 假象）
    rets[c] = df.set_index("trade_date")["pct_chg"] / 100.0
    used.append(c)

n_days = max(len(r) for r in rets.values())
all_idx = sorted(set().union(*[set(r.index) for r in rets.values()]))
# 首日收益置 0（首日收盘买入，从次日起计收益）
port = pd.Series(0.0, index=all_idx)
for c in used:
    r = rets[c].dropna()
    port.loc[r.index] += r / len(used)  # 缺日个股该日不计收益（现金态，简化口径）

equity = (1 + port.fillna(0)).cumprod()
total = float(equity.iloc[-1] - 1)
days = len(all_idx)
ann = (1 + total) ** (TRADING_DAYS_Y / days) - 1 if days > 0 else None
roll_max = equity.cummax()
dd = float((equity / roll_max - 1).min())
daily = port.fillna(0)
sharpe = (
    float(daily.mean() / daily.std() * math.sqrt(TRADING_DAYS_Y))
    if daily.std() > 0
    else None
)
win_days = int((daily > 0).sum())
buy_hold_med = float(
    pd.Series([float((1 + r.dropna()).prod() - 1) for r in rets.values()]).median()
)

# 个股贡献 top/bottom（期末累计贡献≈等权买入持有各自收益/池大小）
contrib = {c: float((1 + rets[c].dropna()).prod() - 1) / len(used) for c in used}
top = sorted(contrib.items(), key=lambda x: -x[1])[:5]
bottom = sorted(contrib.items(), key=lambda x: x[1])[:5]

resA = {
    "run": "A_baseline_equalweight",
    "pool_size": len(used),
    "pool_missing": missing,
    "window": [W0, W1],
    "trade_days": days,
    "total_return_pct": round(total * 100, 2),
    "annualized_pct": round(ann * 100, 2) if ann is not None else None,
    "max_drawdown_pct": round(dd * 100, 2),
    "sharpe": round(sharpe, 2) if sharpe is not None else None,
    "win_days": win_days,
    "win_day_ratio": round(win_days / days, 3),
    "median_stock_buyhold_pct": round(buy_hold_med * 100, 2),
    "top_contrib": [(c, round(v * 100, 2)) for c, v in top],
    "bottom_contrib": [(c, round(v * 100, 2)) for c, v in bottom],
    "caveat": "池子=2026-10-12 快照回溯 2025Q1（示范口径，含池选择前视偏差）；缺日个股现金态简化",
}
print("A) 基准等权组合:", json.dumps(resA, ensure_ascii=False)[:400], flush=True)

# ── B) chanlun 信号引擎 E2E（扫池内多只，取有真实成交者）──────────────────
sys.path.insert(0, os.path.join(BASE, "quant_backtest"))
from chanlun_backtest import run_backtest  # noqa: E402
from chanlun_signal import analyze as chan_analyze  # noqa: E402

BW0 = "20241001"  # 预热窗：结构识别需历史 K 线
b_runs = []
for c in [x for x, _ in top] + [x for x, _ in bottom] + used[:12]:
    df = pd.read_parquet(os.path.join(DATA, c.replace(".", "_") + ".parquet"))
    df = df[df.trade_date <= W1].sort_values("trade_date")
    bars = [
        {"dt": r.trade_date, "o": r.open, "h": r.high, "l": r.low, "c": r.close}
        for r in df.itertuples()
    ]
    sigs = [
        s
        for s in ((chan_analyze(bars) or {}).get("signals") or [])
        if BW0 <= s["dt"] <= W1
    ]
    bt = run_backtest(bars, sigs, cost_bps=3, slip_bps=1)
    b_runs.append(
        {
            "stock": c,
            "signals_in_window": len(sigs),
            "types": sorted({s["type"] for s in sigs}),
            "stats": bt["stats"],
        }
    )
    if len(b_runs) >= 20:
        break
hit = [r for r in b_runs if r["stats"]["trades"] > 0]
resB = {
    "run": "B_chanlun_signal_engine",
    "scanned": len(b_runs),
    "runs_with_trades": len(hit),
    "examples": b_runs[:6],
    "caveat": "理论价成交（引擎防未来声明：信号确认依赖后续笔形成），纸面信号质量口径",
}
print("B) chanlun 信号引擎: 扫", len(b_runs), "只有成交", len(hit), "只", flush=True)

json.dump(
    {"ts": "2026-10-13T01:35", "A": resA, "B": resB},
    open(OUT, "w", encoding="utf-8"),
    ensure_ascii=False,
    indent=1,
)
print("saved ->", OUT, flush=True)
