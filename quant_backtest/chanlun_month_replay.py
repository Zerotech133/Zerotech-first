# -*- coding: utf-8 -*-
"""K-STOCK-239 缠论策略月度信号重放 v2（老田 10-13 01:40 指令：跑一个月的历史数据，重放一个月前缠论看到的信号）
- 数据源：本地 5596 只 A 股日线 parquet（2023-04 → 2026-10-12，约 3.5 年，trade_date 为 str）
- 池子：poc_adapter_chanlun_20261012.json 缠论自选 76 只（= 211c 拍板基准池）
- 窗口：2026-09-12 → 2026-10-12（最近一个月）
- point-in-time：analyze 喂全历史（引擎确认滞后防前视），信号账本只取窗口内
- 复权：tdx ts_adj_factor 覆盖 20260901→20261009（尾部 ffill）；无因子标的引擎回测剔除并登记
- 双口径：①引擎回测（复权 OHLC + 3+1bps 成本）②信号前瞻收益（pct_chg 官方复权日收益，+5/+10/+20 交易日）
"""

import json
import os
import sys
import glob
import pandas as pd

BASE = r"C:/项目/老田的AI助手"
DATA = os.path.join(BASE, "quant_backtest", "data", "ashare")
ADJ_DIR = os.path.join(BASE, "quant_backtest", "data", "tdx", "ts_adj_factor")
POOL_FILE = os.path.join(BASE, "k_stock", "data", "poc_adapter_chanlun_20261012.json")
OUT_JSON = os.path.join(BASE, "quant_backtest", "chanlun_month_replay_result.json")

W0, W1 = "20260912", "20261012"  # 重放窗口（最近一个月）
WI0, WI1 = int(W0), int(W1)
HORIZONS = (5, 10, 20)

sys.path.insert(0, os.path.join(BASE, "quant_backtest"))
from chanlun_backtest import run_backtest  # noqa: E402
from chanlun_signal import analyze as chan_analyze  # noqa: E402

# ── 因子湖：一次载入（2026-09 起的 tdx 补丁）──
parts = sorted(glob.glob(os.path.join(ADJ_DIR, "*.parquet")))
FAC = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
FAC = FAC.sort_values(["ts_code", "trade_date"]).set_index(["ts_code", "trade_date"])[
    "adj_factor"
]
print(
    f"因子湖载入 {len(FAC)} 行（{parts[-1].split(chr(92))[-1]} 20260901→20261009）",
    flush=True,
)

pool = json.load(open(POOL_FILE, encoding="utf-8"))
stocks = sorted({x["stock"] for x in pool})
print(f"池子 {len(stocks)} 只，窗口 {W0}-{W1}，开始重放…", flush=True)

ledger, fwd_rows, bt_rows, no_data, no_factor = [], [], [], [], []


def fwd_returns(code, dt_int):
    """信号日后 +5/+10/+20 交易日复权累计收益（pct_chg 官方口径，跨除权安全）"""
    f = os.path.join(DATA, code.replace(".", "_") + ".parquet")
    df = pd.read_parquet(f).sort_values("trade_date").reset_index(drop=True)
    idx = df.index[df.trade_date == str(dt_int)]
    if len(idx) == 0:
        return None
    i = idx[0]
    out = {}
    for h in HORIZONS:
        seg = df.iloc[i + 1 : i + 1 + h]
        out[f"d{h}"] = (
            round(float(seg["pct_chg"].fillna(0).sum()) / 100.0, 4)
            if len(seg)
            else None
        )
    return out


for k, code in enumerate(stocks, 1):
    f = os.path.join(DATA, code.replace(".", "_") + ".parquet")
    if not os.path.exists(f):
        no_data.append(code)
        continue
    df = pd.read_parquet(f).sort_values("trade_date").reset_index(drop=True)
    bars = [
        {
            "dt": str(r.trade_date),
            "o": float(r.open),
            "h": float(r.high),
            "l": float(r.low),
            "c": float(r.close),
        }
        for r in df.itertuples()
    ]
    sig_all = (chan_analyze(bars) or {}).get("signals") or []
    win = [s for s in sig_all if WI0 <= int(s["dt"]) <= WI1]
    for s in win:
        dt_i = int(s["dt"])
        row = {"stock": code, "dt": dt_i, "type": s["type"]}
        ledger.append(row)
        fr = fwd_returns(code, dt_i)
        if fr:
            fwd_rows.append({**row, **fr})
    if win:
        ts_code = code  # poc_adapter stock 形如 000001.SZ = ts_code
        fser = FAC.get(ts_code) if FAC.index.nlevels == 2 else None
        fmap = {}
        if fser is not None:
            fdf = fser.reset_index()
            fmap = dict(
                zip(fdf["trade_date"].astype(str), fdf["adj_factor"].astype(float))
            )
        has_factor = any(W0 <= d <= W1 for d in fmap)
        if has_factor:
            fdf = fser.reset_index()
            fmap = dict(zip(fdf.trade_date, fdf.adj_factor))
            bars_win = []
            last_f = None
            ok = True
            for b in bars:
                if b["dt"] < W0:
                    if b["dt"] in fmap:
                        last_f = float(fmap[b["dt"]])
                    continue
                if b["dt"] > W1:
                    break
                if b["dt"] in fmap:
                    last_f = float(fmap[b["dt"]])
                if last_f is None:  # 窗口首日即无因子
                    ok = False
                    break
                f = last_f
                bars_win.append(
                    {
                        "dt": b["dt"],
                        "o": b["o"] * f,
                        "h": b["h"] * f,
                        "l": b["l"] * f,
                        "c": b["c"] * f,
                    }
                )
            if ok and bars_win:
                wsig = [s for s in win if W0 <= str(s["dt"]) <= W1]
                bt = run_backtest(bars_win, wsig, cost_bps=3, slip_bps=1)
                st = bt["stats"]
                bt_rows.append(
                    {
                        "stock": code,
                        "adjusted": True,
                        "signals": len(win),
                        "types": sorted({s["type"] for s in win}),
                        "trades": st.get("trades"),
                        "win_rate": st.get("win_rate"),
                        "total_return": st.get("total_return"),
                        "max_dd": st.get("max_dd"),
                        "buy_hold": st.get("buy_hold"),
                    }
                )
            else:
                no_factor.append(code)
        else:
            no_factor.append(code)
    if k % 20 == 0:
        print(f"  …{k}/{len(stocks)} 已扫描，累计窗口信号 {len(ledger)}", flush=True)

# ── 汇总 ──
from collections import Counter

type_dist = Counter(r["type"] for r in ledger)
dfw = pd.DataFrame(fwd_rows)
fwd_agg, fwd_by_dir = {}, {}
if len(dfw):
    for h in HORIZONS:
        col = f"d{h}"
        seg = dfw[col].dropna()
        fwd_agg[col] = {
            "n": int(len(seg)),
            "mean": round(float(seg.mean()), 4),
            "win_rate": round(float((seg > 0).mean()), 4),
            "median": round(float(seg.median()), 4),
        }
    dfw["dir"] = dfw["type"].map(lambda t: "buy" if "买" in str(t) else "sell")
    for d, seg in dfw.groupby("dir"):
        fwd_by_dir[d] = {
            "n": int(len(seg)),
            "d5_mean": round(float(seg["d5"].dropna().mean()), 4),
            "d10_mean": round(float(seg["d10"].dropna().mean()), 4),
            "d20_mean": round(float(seg["d20"].dropna().mean()), 4),
        }

result = {
    "meta": {
        "window": [W0, W1],
        "pool_size": len(stocks),
        "scanned": len(stocks) - len(no_data),
        "no_data": no_data,
        "data_range": "2023-04-03 → 2026-10-12",
        "pit": "analyze 喂全历史（引擎确认滞后防前视），账本只取窗口内信号",
        "adjustment": "引擎回测=tdx 因子复权 OHLC（202609→窗口尾 ffill）；前瞻收益=官方 pct_chg 复权",
        "no_factor_excluded": no_factor,
    },
    "signal_ledger": ledger,
    "type_dist": dict(type_dist),
    "fwd_agg": fwd_agg,
    "fwd_by_dir": fwd_by_dir,
    "backtest_rows": bt_rows,
}
json.dump(result, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print("\n===== 月度重放汇总（v2 复权口径）=====")
print("窗口信号总数:", len(ledger), "| 类型分布:", dict(type_dist))
print(
    "有信号标的:",
    len(bt_rows) + len(no_factor),
    "/",
    len(stocks) - len(no_data),
    "| 无因子剔除:",
    len(no_factor),
    no_factor,
)
print("前瞻收益(复权 pct_chg):", json.dumps(fwd_agg, ensure_ascii=False))
print("分方向:", json.dumps(fwd_by_dir, ensure_ascii=False))
if bt_rows:
    rets = [(r["stock"], r["total_return"], r["max_dd"], r["trades"]) for r in bt_rows]
    avg = sum(r[1] or 0 for r in rets) / len(rets)
    print(
        f"引擎回测(复权): {len(rets)} 标的等权均值 {avg * 100:.2f}% | Top3:",
        sorted(rets, key=lambda x: -(x[1] or 0))[:3],
    )
print("JSON:", OUT_JSON)
