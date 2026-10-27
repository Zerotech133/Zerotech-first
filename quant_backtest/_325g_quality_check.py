# -*- coding: utf-8 -*-
"""
K-STOCK-329 · 首窗 301 信号质量抽检（只读）
复算 calc_dknife_signals_market(20260601→20261013) 取全量信号，四项分析：
  A 信号日密度分布
  B 重复上榜码（连续性）
  C 与影子篮子重叠
  D 名称/板块分布
输出: quant_backtest/_325g_signals_full.json + _325g_quality_report.md
"""

import os
import sys
import json
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "quant_backtest"))

import pandas as pd  # 3.11 保证有
from backtest_engine_v2 import calc_dknife_signals_market

BASKET = {"000858", "300750", "600036", "600519", "601318"}


def norm6(code):
    return code.split(".")[0]


def main():
    t0 = time.time()
    print("[1/4] 复算全量信号...", flush=True)
    sig, _, meta, err = calc_dknife_signals_market("20260601", "20261013")
    assert err is None, "calc err: %r" % (err,)
    days = sorted(sig.keys())
    full = {d: sorted(sig[d]) for d in days}
    n_total = sum(len(v) for v in full.values())
    print(
        "  days=%d n_sig=%d (%.0fs)" % (len(days), n_total, time.time() - t0),
        flush=True,
    )

    # A 密度
    dens = {d: len(full[d]) for d in days}

    # B 重复上榜
    cnt = Counter(c for v in full.values() for c in v)
    repeats = [(c, n) for c, n in cnt.most_common() if n >= 2]
    once = sum(1 for n in cnt.values() if n == 1)

    # C 影子篮子重叠
    overlap = {}
    for d in days:
        hit = sorted(c for c in full[d] if norm6(c) in BASKET)
        if hit:
            overlap[d] = hit

    # D 名称映射 + 板块
    names = {}
    for p in ("data/stock_names.json", "data/ashare/_names.json"):
        fp = os.path.join(ROOT, "quant_backtest", p)
        if os.path.exists(fp):
            try:
                raw = json.load(open(fp, encoding="utf-8"))
                if isinstance(raw, dict):
                    names.update(
                        {
                            str(k).split(".")[0]: v
                            for k, v in raw.items()
                            if isinstance(v, str)
                        }
                    )
            except Exception as e:
                print("  [warn] %s: %s" % (p, e), flush=True)

    sector_map = {}
    try:
        sdef = pd.read_parquet(
            os.path.join(ROOT, "quant_backtest", "data/tdx/ts_sector_def.parquet")
        )
    except Exception:
        try:
            sdef = pd.read_parquet(
                os.path.join(
                    ROOT,
                    "quant_backtest",
                    "data/tdx/tdx_sector_def/parquet/dknife.parquet",
                )
            )
        except Exception:
            sdef = None
    sec_dir = os.path.join(ROOT, "quant_backtest", "data/tdx/tdx_sector_member")
    if os.path.isdir(sec_dir):
        try:
            import glob as _g

            parts = []
            for f in sorted(_g.glob(os.path.join(sec_dir, "*.parquet")))[:40]:
                try:
                    parts.append(pd.read_parquet(f))
                except Exception:
                    pass
            if parts:
                mem = pd.concat(parts)
                cols = [
                    c
                    for c in mem.columns
                    if "sector" in c.lower() or "block" in c.lower()
                ]
                code_col = (
                    "code"
                    if "code" in mem.columns
                    else ("stock_code" if "stock_code" in mem.columns else None)
                )
                if code_col and cols:
                    sec_name_col = (
                        "sector_name" if "sector_name" in mem.columns else cols[0]
                    )
                    m = mem[[code_col, sec_name_col]].drop_duplicates()
                    sector_map = dict(
                        zip(
                            m[code_col].astype(str).str.split(".").str[0],
                            m[sec_name_col].astype(str),
                        )
                    )
                    print(
                        "  sector 映射 %d 条（%s）" % (len(sector_map), sec_name_col),
                        flush=True,
                    )
        except Exception as e:
            print("  [warn] sector: %s" % e, flush=True)

    # 末日榜单带名
    last = days[-1]
    last_named = [
        {
            "code": c,
            "name": names.get(norm6(c), "?"),
            "sector": sector_map.get(norm6(c), "?"),
        }
        for c in full[last]
    ]

    # 重复榜带名（前 15）
    rep_named = [
        {
            "code": c,
            "n": n,
            "name": names.get(norm6(c), "?"),
            "sector": sector_map.get(norm6(c), "?"),
        }
        for c, n in repeats[:15]
    ]

    report = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "meta": meta,
        "A_density": dens,
        "B_repeats_top": rep_named,
        "B_stats": {
            "codes_total": len(cnt),
            "codes_repeat": len(repeats),
            "codes_once": once,
        },
        "C_basket_overlap": overlap,
        "D_last_day": last_named,
    }
    with open(
        os.path.join(HERE, "_325g_signals_full.json"), "w", encoding="utf-8"
    ) as f:
        json.dump({"full": full, **report}, f, ensure_ascii=False)
    print("[done] %.0fs -> _325g_signals_full.json" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
