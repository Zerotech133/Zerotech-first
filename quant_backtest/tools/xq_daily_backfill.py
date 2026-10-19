# -*- coding: utf-8 -*-
"""T9 前置：候选股 2018-2019 历史日线回填（K-ORG-190，老田批准联网拉取）。

背景（K-ORG-190 T3 复测报告 §三）:
    fetch_daily_cached 按"最近 N days"拉取并可能整体重置缓存（days 漂移
    即丢弃旧缓存），造成 2018-2019 早期行情被覆盖丢失（SH600000 仅剩
    2019-05-29 起），v1 因子基线物理不可复现。本工具按显式历史窗口
    回填，**合并语义、写新文件，绝不覆盖现缓存**。

来源: tushare pro_bar(qfq) 优先、pro.daily(raw) 兜底（复用 xq_quote_router
    的 setup_tushare/no_proxy_env/归一化 X.to_ts_code；vol 手→股、
    amount 千元→元，对齐雪球 kline 口径）。

用法:
    python tools/xq_daily_backfill.py \
        [--start 20180101] [--end 20191231] [--sleep 0.35] \
        [--cache <现缓存路径>] [--out <回填合并缓存路径>]

产物:
    OUT（默认 CACHE_DIR/daily_cache_full.json）:
      daily: 现缓存 ∪ 历史回填段（按 trade_date 去重，重叠日现缓存优先）
      _meta.backfill: {start,end,source,fetched_rows,merged_rows,per_sym...}
    报告: CACHE_DIR/daily_backfill_report.json（对账明细）
"""
import argparse
import datetime as dt
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from xq_quote_router import no_proxy_env, setup_tushare, X  # noqa: E402

CACHE_DIR = r"C:\aios\automation_runs\xq_t7_factor"
CACHE_PATH = os.path.join(CACHE_DIR, "daily_cache.json")
OUT_PATH = os.path.join(CACHE_DIR, "daily_cache_full.json")
REPORT_PATH = os.path.join(CACHE_DIR, "daily_backfill_report.json")


def _df_to_rows(df):
    """tushare DataFrame → 雪球 kline 口径行（与 xq_quote_router 对齐）。

    trade_date 统一为紧凑 YYYYMMDD（与现缓存一致；因子管道字符串比较
    依赖此格式，带连字符会静默错位）。
    """
    rows = []
    for _, r in df.sort_values("trade_date").iterrows():
        rows.append({
            "trade_date": str(r["trade_date"]),
            "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]),
            "vol": float(r["vol"]) * 100.0,            # 手 → 股
            "amount": float(r["amount"]) * 1000.0,     # 千元 → 元
            "adj": "qfq",
        })
    return rows


def backfill(start: str, end: str, sleep_s: float,
             cache_path: str, out_path: str):
    cache = json.load(open(cache_path, encoding="utf-8"))
    daily = cache.get("daily", {})
    syms = sorted(daily.keys())
    print("[backfill] 现缓存 %d 只；目标窗口 %s~%s" % (len(syms), start, end))
    ts_mod, pro = setup_tushare()
    merged = {s: list(rows) for s, rows in daily.items()}
    existing_dates = {s: {r.get("trade_date") for r in rows} for s, rows in daily.items()}
    per_sym, errs = {}, []
    fetched_total = 0
    for i, sym in enumerate(syms, 1):
        info = {"fetched": 0, "new_rows": 0, "adj": None, "error": None}
        try:
            ts_code = X.to_ts_code(sym)
            if not ts_code:
                raise RuntimeError("无 ts_code 映射")
            with no_proxy_env():
                df = None
                try:
                    df = ts_mod.pro_bar(ts_code=ts_code, api=pro, adj="qfq",
                                        start_date=start, end_date=end)
                    info["adj"] = "qfq"
                except Exception:
                    df = None
                if df is None or df.empty:
                    df = pro.daily(ts_code=ts_code, start_date=start,
                                   end_date=end)
                    info["adj"] = "raw"
            if df is None or df.empty:
                info["error"] = "空返回"
            else:
                rows = _df_to_rows(df)
                info["fetched"] = len(rows)
                fetched_total += len(rows)
                have = existing_dates[sym]
                fresh = [r for r in rows if r["trade_date"] not in have]
                for r in fresh:
                    have.add(r["trade_date"])
                # 口径一致性守卫：历史段与现缓存 adj 必须同为 qfq；
                # 现缓存 raw + 回填 qfq 混拼会造成收益错位，宁可拒并
                old_adj = {r.get("adj") for r in daily[sym] if r.get("adj")}
                if fresh and old_adj and info["adj"] not in old_adj:
                    info["error"] = "复权口径冲突（现缓存 %s vs 回填 %s），拒并" % (
                        ",".join(sorted(old_adj)), info["adj"])
                    fresh = []
                merged[sym] = sorted(daily[sym] + fresh,
                                     key=lambda r: r.get("trade_date") or "")
                info["new_rows"] = len(fresh)
        except Exception as e:                        # noqa: BLE001
            info["error"] = "%s: %s" % (type(e).__name__, str(e)[:100])
            errs.append("%s %s" % (sym, info["error"]))
        per_sym[sym] = info
        if i % 20 == 0 or i == len(syms):
            print("[backfill] %d/%d fetched=%d new=%d errs=%d"
                  % (i, len(syms), fetched_total,
                     sum(v["new_rows"] for v in per_sym.values()), len(errs)),
                  flush=True)
        time.sleep(sleep_s)

    out = {"daily": merged,
           "_meta": {"days": None, "saved_at": dt.datetime.now().isoformat(),
                     "kind": "backfill-merged",
                     "backfill": {
                         "window": "%s~%s" % (start, end),
                         "source": "tushare pro_bar/pro.daily (qfq优先)",
                         "base_cache": os.path.abspath(cache_path),
                         "base_cache_saved_at": (cache.get("_meta") or {}).get("saved_at"),
                         "fetched_rows": fetched_total,
                         "new_rows": sum(v["new_rows"] for v in per_sym.values()),
                         "symbols": len(syms),
                         "errors": len(errs),
                         "generated_at": dt.datetime.now().astimezone().isoformat(),
                     }}}
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False)
    json.dump({"per_sym": per_sym, "errors": errs},
              open(REPORT_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("[backfill] written %s (new_rows=%d, errors=%d)"
          % (out_path, out["_meta"]["backfill"]["new_rows"], len(errs)))
    print("[backfill] report   %s" % REPORT_PATH)


def main():
    ap = argparse.ArgumentParser(description="候选股历史日线回填（合并式，不覆盖现缓存）")
    ap.add_argument("--start", default="20180101")
    ap.add_argument("--end", default="20191231")
    ap.add_argument("--sleep", type=float, default=0.35)
    ap.add_argument("--cache", default=CACHE_PATH)
    ap.add_argument("--out", default=OUT_PATH)
    args = ap.parse_args()
    backfill(args.start, args.end, args.sleep, args.cache, args.out)


if __name__ == "__main__":
    main()
