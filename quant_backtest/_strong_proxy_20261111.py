# -*- coding: utf-8 -*-
"""强资源断更期的临时替代口径（只读，不入生产清单）。

背景：tdx_sector_daily 板块日线库最后 K 线 2026-10-12，strong_scan 的
freshness_guard 因此每日拦截，strong_latest.json 停在 10-12。T1 共振需要
「强势领涨 + 缠论买点」两腿，strong 缺席导致 T1 连续多日为 0。

本脚本不改生产、不写清单，只用齐全的个股日线（到 11-10）对 11-10 缠论
买点做「相对强度」表征，替代板块级领涨判定：
  · 基准用 qpzm envs 的 A 股全市场 20 日涨跌中位数（-7.11%），
    即个股超额 = 个股 20 日收益 − 全市场中位数，等价于"逆势强度"
  · 结构健康度用距 MA20/MA60 偏离 + 近 5 日量能比
输出：按超额降序，标注信号新鲜度与是否已兑现。

用法: python _strong_proxy_20261111.py [--basis 20261110] [--sig-from 2026-10-01]
"""

import argparse
import json
import os

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ASHARE = os.path.join(HERE, "data", "ashare")
POOL = os.path.join(HERE, "data", "chanlun_pool.jsonl")
RPS = os.path.join(HERE, "reports", "rps_latest.json")

BUY_TYPES = ("1买", "2买", "3买")


def load_batch(basis):
    """取 detect_date == basis 的缠论批次（活跃信号快照）。"""
    rows = []
    with open(POOL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if str(o.get("detect_date"))[:10] == basis and o.get("type") in BUY_TYPES:
                rows.append(o)
    # 同票取最新信号
    best = {}
    for r in rows:
        c = r.get("code")
        dt = str(r.get("dt"))[:10]
        if c not in best or dt > str(best[c].get("dt"))[:10]:
            best[c] = r
    return best


def market_ret20():
    """A 股全市场 20 日涨跌中位数（qpzm envs 口径）。"""
    try:
        d = json.load(open(RPS, encoding="utf-8"))
        for e in d.get("envs") or []:
            if str(e.get("label", "")).startswith("A股"):
                return float(e["ret20"])
    except Exception:
        pass
    return None


def metrics(code, basis, sig_dt=None):
    fp = os.path.join(ASHARE, code.replace(".", "_") + ".parquet")
    if not os.path.exists(fp):
        return None
    df = pd.read_parquet(fp)
    df = df[df["trade_date"].astype(str) <= basis].sort_values("trade_date")
    if len(df) < 61:
        return None
    cl = df["close"].astype(float).reset_index(drop=True)
    vo = df["volume"].astype(float).reset_index(drop=True)
    last = float(cl.iloc[-1])
    # 信号日收盘（entry_px 字段不可用于此，实测等于 detect_date 日价格）
    sig_close = None
    if sig_dt:
        hit = df[df["trade_date"].astype(str) == str(sig_dt).replace("-", "")]
        if len(hit):
            sig_close = float(hit["close"].iloc[-1])
    return {
        "sig_close": sig_close,
        "close": last,
        "r20": (last / float(cl.iloc[-21]) - 1) * 100,
        "r60": (last / float(cl.iloc[-61]) - 1) * 100,
        "ma20": (last / float(cl.rolling(20).mean().iloc[-1]) - 1) * 100,
        "ma60": (last / float(cl.rolling(60).mean().iloc[-1]) - 1) * 100,
        "r5": (last / float(cl.iloc[-6]) - 1) * 100,
        "vr": float(vo.iloc[-5:].mean() / vo.iloc[-20:].mean()) if vo.iloc[-20:].mean() else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--basis", default="2026-11-10")
    ap.add_argument("--sig-from", default="2026-10-01")
    a = ap.parse_args()

    mkt = market_ret20()
    batch = load_batch(a.basis)
    print("批次 %s 买点 %d 只；全市场 20 日中位 %s" % (a.basis, len(batch), mkt))

    out = []
    for code, r in batch.items():
        dt = str(r.get("dt"))[:10]
        m = metrics(code, a.basis.replace("-", ""), dt)
        if not m:
            continue
        sig_px = float(r.get("entry_px") or r.get("price") or 0)
        out.append({
            "code": code, "name": r.get("name"), "type": r.get("type"),
            "dt": dt, "sig_px": sig_px, "reg": r.get("reg"),
            "since": (m["close"] / m["sig_close"] - 1) * 100 if m.get("sig_close") else None,
            "fresh": dt >= a.sig_from,
            **m,
        })

    out.sort(key=lambda x: (x["r20"] - (mkt or 0)), reverse=True)
    fmt = "%-11s %-7s %-4s %-11s %6s %7s %7s %7s %6s %6s"
    print(fmt % ("code", "name", "信号", "信号日", "信号后%", "20日%", "超额pp", "MA20距%", "MA60距", "量比"))
    for x in out:
        ex = x["r20"] - (mkt or 0)
        print(fmt % (
            x["code"], x["name"][:6], x["type"], x["dt"],
            ("%+.1f" % x["since"]) if x["since"] is not None else "NA",
            "%+.1f" % x["r20"], "%+.1f" % ex, "%+.1f" % x["ma20"],
            "%+.1f" % x["ma60"], "%.2f" % x["vr"] if x["vr"] else "NA",
        ))

    print()
    print("=== 新鲜信号(>=%s) 且 超额>0 且 贴均线(-5%%~+8%%) ===" % a.sig_from)
    pick = [x for x in out if x["fresh"] and x["r20"] - (mkt or 0) > 0
            and -5 <= x["ma20"] <= 8]
    for x in pick:
        print("  %s %s %s@%s 超额%+.1fpp MA20%+.1f%% 信号后%+.1f%%" % (
            x["code"], x["name"], x["type"], x["dt"],
            x["r20"] - (mkt or 0), x["ma20"], x["since"] or 0))
    if not pick:
        print("  （无）")


if __name__ == "__main__":
    main()
