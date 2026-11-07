# -*- coding: utf-8 -*-
"""把池里每条买点信号的「可知日」重算出来（截断重放），供诚实回测使用。

结论先行（探针已证）：`dt` 是信号所在的那根 K 线，但缠论的买点需要后续
5~43 根 K 线才能成立——把 K 线截断在 dt 时，`analyze()` 一条信号都不给。
所以「按 dt+1 开盘入场」是未来函数，回测出来的 97.7% 胜率是假的。

本脚本对每条信号重放，取首次出现的位置 k：
    可知日 = dt 之后第 k 个交易日（k=0 表示当天可知）
    可执行入场 = 可知日之后第 1 个交易日开盘

性能：**必须一次性把行情读进内存再分组**。早期版本在 `load_code_bars(code)`
里对每只票重读 62 个 parquet 分片，2800 只票 = 2800 次全量扫描，实测要 60 分钟；
预载后 4000 笔信号约 4 分钟。

产出 reports/signal_known.json：
    {code, type, dt, k, known_dt, price}

用法：python tools/research/signal_known_dates.py [--from 2026-03-01]
"""
import argparse
import glob
import json
import os
import sys
from collections import Counter, defaultdict

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
QBT = os.path.join(ROOT, "quant_backtest")
TDX = os.path.join(QBT, "data", "tdx")
OUT = os.path.join(QBT, "reports", "signal_known.json")
PART = OUT + ".part"
START, END = "20240601", "20261106"

sys.path.insert(0, QBT)
sys.path.insert(0, HERE)
from chanlun_signal import analyze          # noqa: E402
from signal_known_date_probe import load_pool_sig  # noqa: E402

MAX_K = 60
BUY = ("1买", "2买", "3买")


def load_all_bars(codes):
    """一次读全量分片 → {code: [bar,...]}（升序）。"""
    code_set = set(codes)
    out = []
    for p in sorted(glob.glob(os.path.join(TDX, "ts_daily", "part_*.parquet"))):
        d = pd.read_parquet(p, columns=["ts_code", "trade_date", "open",
                                        "high", "low", "close", "vol"])
        d = d[(d["trade_date"] >= START) & (d["trade_date"] <= END)]
        if len(d):
            d = d[d["ts_code"].isin(code_set)]
            if len(d):
                out.append(d)
    df = (pd.concat(out, ignore_index=True)
          .drop_duplicates(["ts_code", "trade_date"])
          .sort_values(["ts_code", "trade_date"]))
    res = {}
    for code, g in df.groupby("ts_code", sort=False):
        bars = []
        for r in g.itertuples(index=False):
            d8 = str(r.trade_date).replace("-", "")
            if len(d8) != 8:
                continue
            bars.append({"dt": "%s-%s-%s" % (d8[:4], d8[4:6], d8[6:]),
                         "o": float(r.open), "h": float(r.high),
                         "l": float(r.low), "c": float(r.close),
                         "v": float(r.vol)})
        res[code] = bars
    return res


def first_k(bars, idx, sig_dt, sig_type, cache):
    for k in range(0, MAX_K + 1):
        cut = idx + k + 1
        if cut > len(bars):
            return None
        st = cache.get(cut)
        if st is None:
            try:
                st = analyze(bars[:cut])
            except Exception:
                return None
            cache[cut] = st
        for s in st.get("signals") or []:
            if s.get("type") == sig_type and s.get("dt") == sig_dt:
                return k
    return -1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dt_from", default="2026-03-01")
    a = ap.parse_args()

    sig = [r for r in load_pool_sig() if r["dt"] >= a.dt_from]
    by_code = defaultdict(list)
    for r in sig:
        by_code[r["code"]].append(r)
    print("信号 %d 笔，%d 只票" % (len(sig), len(by_code)), flush=True)

    print("预载行情…", flush=True)
    allbars = load_all_bars(by_code)
    print("行情 %d 只就绪" % len(allbars), flush=True)

    done_codes, out = set(), []
    if os.path.exists(PART):
        for line in open(PART, encoding="utf-8"):
            d = json.loads(line)
            out.append(d)
            done_codes.add(d["code"])
        print("续跑：已完成 %d 票 / %d 笔" % (len(done_codes), len(out)), flush=True)
    fh = open(PART, "a", encoding="utf-8")

    ks = Counter()
    n = 0
    for ci, (code, rows) in enumerate(sorted(by_code.items()), 1):
        if code in done_codes:
            continue
        bars = allbars.get(code) or []
        if not bars:
            recs = [{"code": code, "type": r["type"], "dt": r["dt"], "k": None,
                     "known_dt": None, "price": r.get("price"),
                     "note": "no_bars"} for r in rows]
        else:
            date_pos = {}
            for i, b in enumerate(bars):
                date_pos.setdefault(b["dt"], i)
            cache = {}
            recs = []
            for r in rows:
                i = date_pos.get(r["dt"])
                if i is None:
                    recs.append({"code": code, "type": r["type"], "dt": r["dt"],
                                 "k": None, "known_dt": None,
                                 "price": r.get("price"), "note": "no_bar_at_dt"})
                    continue
                k = first_k(bars, i, r["dt"], r["type"], cache)
                known = bars[i + k]["dt"] if isinstance(k, int) and k >= 0 \
                    and i + k < len(bars) else None
                ks[k] += 1
                recs.append({"code": code, "type": r["type"], "dt": r["dt"],
                             "k": k, "known_dt": known,
                             "price": r.get("price"),
                             "lag_field": r.get("lag")})
        for rec in recs:
            out.append(rec)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()
        n += 1
        if n % 100 == 0:
            print("  %d/%d 票  %d 笔…" % (ci, len(by_code), len(out)), flush=True)

    fh.close()
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=0)

    ok = [x for x in out if isinstance(x.get("k"), int) and x["k"] >= 0]
    print("\n完成 %d 笔；可判定 %d 笔（%.1f%%）"
          % (len(out), len(ok), 100 * len(ok) / max(1, len(out))))
    print("k 分布前 12:", ks.most_common(12))
    if ok:
        kk = sorted(x["k"] for x in ok)
        print("k 中位 %d，均值 %.1f，p10 %d，p90 %d"
              % (kk[len(kk) // 2], sum(kk) / len(kk),
                 kk[int(len(kk) * 0.1)], kk[int(len(kk) * 0.9)]))
        print("k=0 占比 %.1f%%" % (100 * sum(1 for x in ok if x["k"] == 0) / len(ok)))
        bytype = defaultdict(list)
        for x in ok:
            bytype[x["type"]].append(x["k"])
        for t in BUY:
            v = sorted(bytype.get(t) or [])
            if v:
                print("  %s: n=%d 中位 k=%d" % (t, len(v), v[len(v) // 2]))
    print("写出", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
