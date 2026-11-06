import sys, os, time
sys.path.insert(0, "quant_backtest")
import duckdb
from warehouse_reader import WarehouseReader

R = WarehouseReader()
con = duckdb.connect()
B = os.path.join(R.data_root)
for aid in ["stk_mins", "us_daily", "ts_daily", "ts_opt_daily",
            "report_rc", "ts_cb_daily", "hk_daily", "daily", "ts_daily2"]:
    rec = R._by_asset.get(aid)
    if rec is None:
        print(aid, "NOT in registry"); continue
    names = rec.get("physical_tables") or [aid]
    dirs = R._resolve(names)
    ns = [d for d in dirs if not R._is_staging(d)] or dirs
    print("===", aid, "n_dirs(non-staging)=", len(ns))
    for d in ns[:2]:
        rel = os.path.relpath(d, B)
        # 列名
        try:
            cols = [r[0] for r in con.execute(
                f"DESCRIBE SELECT * FROM read_parquet("
                f"'{d}/**/*.parquet', union_by_name=true)").fetchall()]
        except Exception as e:
            cols = ["ERR:" + str(e)[:60]]
        print("   ", rel, "| cols:", cols[:8])
        # 单文件 max(date) 速度：取最新 mtime 文件
        cand = []
        for r, _s, fs in os.walk(d):
            for f in fs:
                if f.endswith(".parquet"):
                    cand.append(os.path.join(r, f))
        if not cand:
            continue
        cand.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        one = cand[0]
        dc = R._detect_date_col(d) or "trade_date"
        t0 = time.time()
        try:
            mx = con.execute(
                f"SELECT max({dc}) FROM read_parquet('{one}')").fetchone()[0]
            print(f"      one-file max({dc})={mx}  ({time.time()-t0:.2f}s)  {os.path.basename(one)}")
        except Exception as e:
            print("      one-file ERR:", str(e)[:80])
