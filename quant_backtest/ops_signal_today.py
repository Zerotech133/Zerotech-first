# -*- coding: utf-8 -*-
"""
K-STOCK-281 · 每日运营信号账本（运营层·真实状态）
老田 10-13 07:29 指令：运行要基于真实的东西——每天的更新数、每天的买卖点是否已执行。

- 数据新鲜度：池股 parquet 最新 trade_date 逐只核查（真实更新状态）
- 信号：hfq + seen-ever 与回测同一代码路径（263a/269 铁律），对最新交易日全池计算
- 执行状态如实：269 终案下信号层=影子蓄水池（不执行）；组合层动作=10 日再平衡+regime（真实执行）
- 幂等：按 data_asof 缓存；数据文件不更新则直接回缓存，重复调用零重算
- 无状态依赖：seen-ever 状态自持（data/ops/signal_state.json），首次运行标记 baseline
"""

import sys
import os
import json
import glob
import time
import pandas as pd

BASE = r"C:/项目/老田的AI助手"
DATA = os.path.join(BASE, "quant_backtest", "data", "ashare")
POOL_DIR = os.path.join(BASE, "k_stock", "data")
LEDGER_DIR = os.path.join(BASE, "quant_backtest", "data", "daily_ledger")
STATE_DIR = os.path.join(BASE, "quant_backtest", "data", "ops")
WARMUP = 250
BUY_TYPES = {"1买", "2买", "3买"}
SELL_TYPES = {"1卖", "2卖", "3卖"}
POLICY = (
    "K-STOCK-269 终案：组合层=池等权永在场+每10交易日再平衡+regime三档连续调仓（真实执行）；"
    "信号层=影子蓄水池（只记录不执行，runup帽/笔级提速验证出正edge后作再平衡偏置上岗）"
)

sys.path.insert(0, os.path.join(BASE, "quant_backtest"))
from chanlun_signal import analyze as chan_analyze  # noqa: E402


def _latest_pool_file():
    fs = [
        f
        for f in os.listdir(POOL_DIR)
        if f.startswith("poc_adapter_chanlun_") and f.endswith(".json")
    ]
    if not fs:
        raise RuntimeError("无 poc_adapter_chanlun_*.json 池文件")
    return max(fs)


def _load_books(stocks):
    """读池股 parquet 并构建 hfq 列（263a 铁律：官方 pct_chg 因子链）。"""
    books = {}
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
    return books


def _portfolio_context():
    """从最新回测账本提取组合层真实状态（最近再平衡日/下一次/当前档位）。"""
    fs = sorted(glob.glob(os.path.join(LEDGER_DIR, "ledger_*.json")))
    if not fs:
        return {"note": "回测账本未生成"}
    led = json.load(open(fs[-1], encoding="utf-8"))
    days = sorted(led.get("days", {}))
    if not days:
        return {"note": "账本无逐日数据"}
    last_day = days[-1]
    last = led["days"][last_day]
    reb_days = [
        d
        for d in days
        if any(a.get("type") == "rebalance" for a in led["days"][d].get("actions", []))
    ]
    last_reb = reb_days[-1] if reb_days else None
    nxt = None
    if last_reb:
        i = days.index(last_reb)
        nxt = days[min(i + 10, len(days) - 1)]
    ec = led.get("equity_curve") or []
    nav_last = ec[-1]["nav"] if ec else None
    return {
        "ledger": os.path.basename(fs[-1]),
        "ledger_last_day": last_day,
        "invest_pct": last.get("cap_pct") if "cap_pct" in last else last.get("cap"),
        "cap": last.get("cap"),
        "nav": nav_last,
        "last_rebalance_day": last_reb,
        "next_rebalance_day": nxt,
        "n_rebalances": len(reb_days),
    }


def compute(force=False):
    """主入口：返回今日运营真实状态 dict（按 data_asof 缓存幂等）。"""
    os.makedirs(STATE_DIR, exist_ok=True)
    cache_fp = os.path.join(STATE_DIR, "ops_today_cache.json")
    state_fp = os.path.join(STATE_DIR, "signal_state.json")
    cache_error = ""

    pool_name = _latest_pool_file()
    pool = json.load(open(os.path.join(POOL_DIR, pool_name), encoding="utf-8"))
    stocks = sorted({x["stock"] for x in pool})
    tiers = {x["stock"]: (x.get("_tier") or "-") for x in pool}

    # ---- 缓存判定：池文件与缓存一致且缓存记录的池文件名相同 → 直接回 ----
    if not force and os.path.exists(cache_fp):
        try:
            c = json.load(open(cache_fp, encoding="utf-8"))
            if c.get("pool", {}).get("file") == pool_name:
                c["cached"] = True
                return c
        except Exception as exc:
            # compute 必须保持 AST 可审计（无 import/无 logging）；错误随结果透出
            cache_error = str(exc)[:200]

    books = _load_books(stocks)

    # ---- 数据新鲜度（相对全池最新交易日） ----
    last_dates = {s: str(b["trade_date"].iloc[-1]) for s, b in books.items()}
    asof = max(last_dates.values()) if last_dates else None
    stale = [
        {"stock": s, "last": d}
        for s, d in last_dates.items()
        if asof and (int(asof) - int(d)) > 30
    ]
    freshness = {
        "asof": asof,
        "n_stocks": len(stocks),
        "n_have_data": len(books),
        "n_fresh": sum(1 for d in last_dates.values() if d == asof),
        "stale_over_30d": stale,
    }

    # ---- 信号计算（最新交易日，hfq 同一代码路径） ----
    cur = {}
    for s, b in books.items():
        if b["trade_date"].iloc[-1] != asof:
            continue
        seg = b[b.trade_date <= asof]
        if len(seg) < WARMUP // 2:
            continue
        bars = [
            {
                "dt": int(r.trade_date),
                "o": float(r.adj_open),
                "h": float(r.adj_high),
                "l": float(r.adj_low),
                "c": float(r.adj_close),
            }
            for r in seg.tail(WARMUP).itertuples()
        ]
        for sg in (chan_analyze(bars) or {}).get("signals") or []:
            cur.setdefault(s, {})[sg["type"]] = {
                "dt": int(sg["dt"]),
                "price": round(float(sg.get("price") or 0), 3),
                "note": sg.get("note") or "",
            }

    # ---- seen-ever 状态推进（跨数据日只算一次；同日重算=幂等） ----
    state = (
        json.load(open(state_fp, encoding="utf-8"))
        if os.path.exists(state_fp)
        else None
    )
    first_run = state is None or state.get("asof") != asof
    prev = {} if first_run else state["signals"]
    flat_prev = {(s, tp): v["dt"] for s, d in prev.items() for tp, v in d.items()}

    sigs = []
    n_new = 0
    for s, d in cur.items():
        for tp, v in d.items():
            if first_run:
                status = "baseline"  # 运营账本启用日的存量信号
            elif (s, tp) not in flat_prev:
                status = "fresh"
                n_new += 1
            else:
                status = "seen"
            sigs.append(
                {
                    "stock": s,
                    "tier": tiers.get(s, "-"),
                    "type": tp,
                    "sig_dt": v["dt"],
                    "price": v["price"],
                    "note": v["note"],
                    "status": status,
                    "executed": False,
                    "exec_note": "影子蓄水池·未执行（269终案：组合层再平衡/regime 才是真实执行动作）",
                }
            )
    sigs.sort(key=lambda x: (x["status"] != "fresh", x["type"][-1] == "卖", x["stock"]))

    if first_run:
        new_state = {"asof": asof, "signals": {s: d for s, d in cur.items()}}
    else:
        new_state = {"asof": asof, "signals": {**prev, **cur}}
    json.dump(new_state, open(state_fp, "w", encoding="utf-8"), ensure_ascii=False)

    out = {
        "ok": True,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "run_day": time.strftime("%Y%m%d"),
        "pool": {
            "file": pool_name,
            "n": len(stocks),
            "tiers": {
                t: sum(1 for v in tiers.values() if v == t)
                for t in sorted(set(tiers.values()))
            },
        },
        "freshness": freshness,
        "signals": sigs,
        "n_signals": len(sigs),
        "n_new": n_new if not first_run else 0,
        "portfolio": _portfolio_context(),
        "policy": POLICY,
        "price_basis": "hfq后复权（官方pct_chg因子链·263a铁律）+ seen-ever 去重",
        "cache_error": cache_error,
    }
    json.dump(out, open(cache_fp, "w", encoding="utf-8"), ensure_ascii=False)
    return out


if __name__ == "__main__":
    r = compute(force=True)
    print(
        "asof:",
        r["freshness"]["asof"],
        "fresh:",
        r["freshness"]["n_fresh"],
        "/",
        r["freshness"]["n_stocks"],
    )
    print("signals:", r["n_signals"], "new:", r["n_new"])
    print("portfolio:", json.dumps(r["portfolio"], ensure_ascii=False)[:220])
