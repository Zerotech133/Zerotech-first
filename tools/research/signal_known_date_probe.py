# -*- coding: utf-8 -*-
"""信号「可知日」探针：池里的 dt 到底是可交易信号日，还是事后确认的底部？

背景：按 dt+1 开盘入场做出 1买 胜率 97.7%、均 +14.6%——好得不真实。
可能是缠论的 1买 需要后续 K 线才能确认（分型/笔/背驰都要"长出来"），
那么 dt 只是那个低点，信息在 dt 当天并不存在，属于未来函数。

做法：对抽样信号，把该股的 K 线截断在 dt、dt+1、dt+2……逐个重放 `analyze()`，
取第一次出现该信号的位置 k。k 就是「信号可知滞后」：
  k=0  → dt 当天就能算出，dt+1 开盘可执行（之前的回测成立）
  k>0  → 需要 k 根后续 K 线才能确认，真实入场应推迟 k 个交易日

用法：python tools/research/signal_known_date_probe.py [--n 30]
"""
import argparse
import glob
import json
import os
import random
import sys
from collections import defaultdict

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
QBT = os.path.join(ROOT, "quant_backtest")
TDX = os.path.join(QBT, "data", "tdx")
POOL = os.path.join(QBT, "data", "chanlun_pool.jsonl")

sys.path.insert(0, QBT)
from chanlun_signal import analyze  # noqa: E402

BUY = ("1买", "2买", "3买")


def load_pool_sig():
    rows, seen = [], set()
    with open(POOL, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("type") not in BUY or not r.get("dt"):
                continue
            k = (r["code"], r["dt"], r["type"])
            if k in seen:
                continue
            seen.add(k)
            rows.append(r)
    return rows


def load_code_bars(code, start="20250101", end="20261106"):
    out = []
    for p in sorted(glob.glob(os.path.join(TDX, "ts_daily", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "open",
                                        "high", "low", "close", "vol"])
        d = d[(d["ts_code"] == code)]
        if len(d):
            out.append(d)
    if not out:
        return []
    df = (pd.concat(out, ignore_index=True)
          .drop_duplicates("trade_date").sort_values("trade_date"))
    df = df[(df["trade_date"] >= start) & (df["trade_date"] <= end)]
    bars = []
    for r in df.itertuples(index=False):
        d8 = str(r.trade_date).replace("-", "")
        bars.append({"dt": "%s-%s-%s" % (d8[:4], d8[4:6], d8[6:]),
                     "o": float(r.open), "h": float(r.high),
                     "l": float(r.low), "c": float(r.close),
                     "v": float(r.vol)})
    return bars


def first_known(bars, sig_dt, sig_type, max_k=45):
    """→ (k, 信号当时的价)。k 为首次出现所需的后验 K 线数。"""
    idx = next((i for i, b in enumerate(bars) if b["dt"] == sig_dt), None)
    if idx is None:
        return None, None
    for k in range(0, max_k + 1):
        cut = idx + k + 1
        if cut > len(bars):
            break
        try:
            st = analyze(bars[:cut])
        except Exception:
            return None, None
        for s in st.get("signals") or []:
            if s.get("type") == sig_type and s.get("dt") == sig_dt:
                return k, s.get("price")
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=36)
    ap.add_argument("--seed", type=int, default=20261107)
    a = ap.parse_args()

    sig = [r for r in load_pool_sig() if r["dt"] >= "2026-03-01"]
    random.seed(a.seed)
    random.shuffle(sig)
    # 分型均衡抽样
    per = defaultdict(list)
    for r in sig:
        per[r["type"]].append(r)
    picked = []
    for t in BUY:
        picked.extend(per[t][: a.n // 3])

    print("抽样 %d 笔（%s）" % (len(picked),
                              " ".join("%s=%d" % (t, len(per[t][:a.n // 3]))
                                       for t in BUY)))
    res = []
    for r in picked:
        bars = load_code_bars(r["code"])
        if not bars:
            print("  %-10s 无行情，跳过" % r["code"])
            continue
        k, px = first_known(bars, r["dt"], r["type"])
        res.append({"code": r["code"], "type": r["type"], "dt": r["dt"],
                    "k": k, "pool_price": r.get("price"), "engine_price": px,
                    "lag_field": r.get("lag")})
        print("  %-10s %s dt=%s  可知滞后 k=%s  池价=%s 引擎价=%s (lag=%s)"
              % (r["code"], r["type"], r["dt"], k, r.get("price"), px,
                 r.get("lag")))

    ok = [x for x in res if x["k"] is not None]
    print("\n可判定 %d / %d" % (len(ok), len(res)))
    if not ok:
        return 1
    from collections import Counter
    print("k 分布:", Counter(x["k"] for x in ok).most_common())
    print("k=0 占比: %.1f%%" % (100 * sum(1 for x in ok if x["k"] == 0) / len(ok)))
    print("中位 k:", sorted(x["k"] for x in ok)[len(ok) // 2])
    out = os.path.join(QBT, "reports", "signal_known_date_probe.json")
    json.dump({"n": len(ok), "items": res},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("写出", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
