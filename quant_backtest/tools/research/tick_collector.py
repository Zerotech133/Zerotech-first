# -*- coding: utf-8 -*-
"""TDX 分笔成交（tick）采集器 —— 本地运行，零采购补全逐笔数据。

背景（20261106 实测）:
- 数据仓与远端 TDX-Trading（181 表 / 225.5GB）**均无逐笔/委托队列表**；
- 但通达信行情协议本身带 `get_history_transaction_data`，实测可回溯到 2017 年；
- 粒度 = **3 秒快照聚合**（约 18 笔/分钟，4723 笔/日/股），非交易所逐笔；
- vol 单位 = 手，分笔 vol 合计 vs 日线 vol 误差 0.02%（实测 000001 20261105）。

能力边界（必读）:
- 可得: 3 秒切片成交量/价/主动方向(buyorsell)、9 年历史、当日盘中增量
- 不可得: 单笔委托原始手数、委托排队明细、十档盘口 → 需交易所 Level-2 授权
- 时间精度只有 HH:MM（无秒），同分钟多笔顺序不可辨

产出: data/tick/{YYYYMM}/tick_{YYYYMMDD}.parquet
用法:
  python tools/research/tick_collector.py --date 20261105 --limit 20          # dry-run 探测
  python tools/research/tick_collector.py --date 20261105 --limit 20 --apply  # 落盘
  python tools/research/tick_collector.py --date 20261105 --all --apply       # 全市场
"""

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

try:
    from pytdx.hq import TdxHq_API
except ImportError:
    sys.exit("需要 pytdx: pip install pytdx -i https://mirrors.aliyun.com/pypi/simple/")

HERE = os.path.dirname(os.path.abspath(__file__))
QB = os.path.abspath(os.path.join(HERE, "..", ".."))
DATA = os.path.join(QB, "data")
OUT_ROOT = os.path.join(DATA, "tick")

# 实测可用率 7/16，按响应速度排序；采集时轮转分摊
SERVER_POOL = [
    ("180.153.18.170", 7709),
    ("60.191.117.167", 7709),
    ("218.75.126.9", 7709),
    ("115.238.90.165", 7709),
    ("115.238.56.198", 7709),
    ("180.153.18.172", 80),
    ("202.108.253.139", 80),
]

PAGE = 2000
MAX_ROWS = 80000  # 单股单日保护上限（实测上限约 5k，留足余量）
LOCK = threading.Lock()


def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def probe_servers(timeout=4.0):
    """探测可用服务器并按延迟排序。"""
    alive = []
    for ip, port in SERVER_POOL:
        api = TdxHq_API(heartbeat=True)
        t0 = time.time()
        try:
            if api.connect(ip, port, time_out=timeout):
                if api.get_security_count(0) is not None:
                    alive.append((ip, port, time.time() - t0))
        except Exception:
            pass
        finally:
            try:
                api.disconnect()
            except Exception:
                pass
    alive.sort(key=lambda x: x[2])
    return alive


def pull_day(api, mkt, code, date_int):
    """拉单股单日全部分笔。返回 list[dict]。

    不通达信分页是**倒序索引**（start=0 为最新），故累计后统一排序。
    重复键（同 time/price/vol/buyorsell）为合法成交片段，**不得去重**，
    否则 vol 合计会低于日线成交量。
    """
    rows, start = [], 0
    while True:
        try:
            batch = api.get_history_transaction_data(mkt, code, start, PAGE, date_int)
        except Exception:
            break
        if not batch:
            break
        rows.extend(batch)
        start += len(batch)
        if len(batch) < PAGE or start >= MAX_ROWS:
            break
    return rows


def normalize(code, mkt, date_int, rows):
    """转 DataFrame，补 ts_code / 金额 / 排序。"""
    if not rows:
        return None
    suffix = "SZ" if mkt == 0 else "SH"
    df = pd.DataFrame(rows)
    df["trade_time"] = df["time"].astype(str)
    df = df.drop(columns=["time"])
    df["ts_code"] = f"{code}.{suffix}"
    df["trade_date"] = int(date_int)
    df["vol"] = pd.to_numeric(df["vol"], errors="coerce").fillna(0).astype("int64")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    # 通达信 vol 单位=手，1 手=100 股；amount 单位=元
    df["amount"] = (df["price"] * df["vol"] * 100).round(2)
    df["buyorsell"] = pd.to_numeric(df["buyorsell"], errors="coerce").fillna(0).astype("int8")
    df = df[["ts_code", "trade_date", "trade_time", "price", "vol", "buyorsell", "amount"]]
    df = df.sort_values("trade_time", kind="mergesort").reset_index(drop=True)
    return df


def load_universe(limit=None, all_codes=False):
    """从本地日线取当日标的池（避免用过期 stock_basic）。"""
    daily_dir = os.path.join(DATA, "tushare", "daily")
    files = sorted(os.listdir(daily_dir))
    if not files:
        sys.exit("缺 data/tushare/daily，无法确定标的池")
    path = os.path.join(daily_dir, files[-1])
    df = pd.read_parquet(path, columns=["ts_code"])
    codes = sorted(set(df["ts_code"].astype(str)))
    out = [(0 if c.endswith(".SZ") else 1, c.split(".")[0], c) for c in codes]
    if not all_codes:
        out = out[: (limit or 20)]
    return out, os.path.basename(path)


def calibrate(df_all, date_int):
    """分笔 vol 合计 vs 日线 vol 交叉校验（单位/完整性双重验证）。"""
    daily_dir = os.path.join(DATA, "tushare", "daily")
    p = os.path.join(daily_dir, f"d_{date_int}.parquet")
    if not os.path.exists(p):
        return {"status": "skip", "reason": f"缺 d_{date_int}.parquet"}
    d = pd.read_parquet(p, columns=["ts_code", "vol"])
    d = d.set_index("ts_code")["vol"]
    got = df_all.groupby("ts_code")["vol"].sum()
    joint = got.index.intersection(d.index)
    if len(joint) == 0:
        return {"status": "skip", "reason": "标的无交集"}
    ratio = (got[joint] / d[joint].astype(float)).dropna()
    return {
        "status": "ok",
        "n_compared": int(len(ratio)),
        "ratio_median": round(float(ratio.median()), 5),
        "ratio_p05": round(float(ratio.quantile(0.05)), 5),
        "ratio_p95": round(float(ratio.quantile(0.95)), 5),
        "within_1pct": int(((ratio - 1).abs() <= 0.01).sum()),
        "verdict": "vol单位=手且覆盖完整" if abs(float(ratio.median()) - 1) <= 0.01 else "需复核",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", type=int, required=True, help="YYYYMMDD")
    ap.add_argument("--limit", type=int, default=20, help="非 --all 时采集只数")
    ap.add_argument("--all", action="store_true", help="全市场")
    ap.add_argument("--workers", type=int, default=5, help="并行连接数")
    ap.add_argument("--apply", action="store_true", help="实际落盘（默认 dry-run）")
    ap.add_argument("--out", default=OUT_ROOT)
    args = ap.parse_args()

    date_int = args.date
    out_dir = os.path.join(args.out, str(date_int // 100))
    out_path = os.path.join(out_dir, f"tick_{date_int}.parquet")

    if os.path.exists(out_path) and args.apply:
        log(f"已存在，跳过: {out_path}")
        return

    universe, ref = load_universe(limit=args.limit, all_codes=args.all)
    log(f"标的池来源={ref}  采集只数={len(universe)}  并行={args.workers}  apply={args.apply}")

    alive = probe_servers()
    if not alive:
        sys.exit("无可用行情服务器")
    log(f"可用服务器 {len(alive)} 个，最快 {alive[0][0]}:{alive[0][1]} {alive[0][2]:.2f}s")
    servers = [(a[0], a[1]) for a in alive]

    # 分片给各 worker
    n = max(1, min(args.workers, len(servers)))
    chunks = [universe[i::n] for i in range(n)]
    results, stats = [], []
    t_start = time.time()

    def worker(idx, chunk):
        ip, port = servers[idx % len(servers)]
        api = TdxHq_API(heartbeat=True)
        if not api.connect(ip, port, time_out=6):
            return [], {"server": f"{ip}:{port}", "ok": 0, "fail": len(chunk), "rows": 0}
        local, ok, fail = [], 0, 0
        for mkt, code, _full in chunk:
            rows = pull_day(api, mkt, code, date_int)
            df = normalize(code, mkt, date_int, rows)
            if df is not None and len(df):
                local.append(df)
                ok += 1
            else:
                fail += 1
        try:
            api.disconnect()
        except Exception:
            pass
        return local, {"server": f"{ip}:{port}", "ok": ok, "fail": fail,
                       "rows": int(sum(len(x) for x in local))}

    with ThreadPoolExecutor(max_workers=n) as ex:
        futs = {ex.submit(worker, i, c): i for i, c in enumerate(chunks)}
        for f in as_completed(futs):
            local, st = f.result()
            results.extend(local)
            stats.append(st)

    elapsed = time.time() - t_start
    if not results:
        sys.exit("未采到任何分笔")

    df_all = pd.concat(results, ignore_index=True)
    rows_total = len(df_all)
    log(f"采集完成: {len(df_all['ts_code'].unique())} 只 / {rows_total} 条 / 耗时 {elapsed:.1f}s "
        f"({elapsed / max(1, len(df_all['ts_code'].unique())) * 1000:.0f} ms/股)")

    cal = calibrate(df_all, date_int)
    log(f"与日线校验: {json.dumps(cal, ensure_ascii=False)}")

    bs = df_all["buyorsell"].value_counts().to_dict()
    report = {
        "date": date_int,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "applied": bool(args.apply),
        "out_path": out_path if args.apply else None,
        "codes_requested": len(universe),
        "codes_collected": int(df_all["ts_code"].nunique()),
        "rows": rows_total,
        "elapsed_sec": round(elapsed, 2),
        "ms_per_code": round(elapsed / max(1, df_all["ts_code"].nunique()) * 1000, 1),
        "workers": n,
        "servers": stats,
        "buyorsell_dist": {str(k): int(v) for k, v in bs.items()},
        "calibration": cal,
        "extrapolate_full_market": {
            "codes": 5000,
            "minutes": round(elapsed / max(1, df_all["ts_code"].nunique()) * 5000 / 60, 1),
        },
        "granularity": "3秒快照聚合（HH:MM，无秒）",
    }

    if args.apply:
        os.makedirs(out_dir, exist_ok=True)
        tmp = out_path + ".tmp"
        df_all.to_parquet(tmp, index=False, compression="zstd")
        os.replace(tmp, out_path)
        size_mb = os.path.getsize(out_path) / 1024 / 1024
        report["file_mb"] = round(size_mb, 2)
        report["bytes_per_row"] = round(os.path.getsize(out_path) / max(1, rows_total), 2)
        log(f"已落盘: {out_path}  {size_mb:.2f} MB  {report['bytes_per_row']} B/行")
    else:
        log("dry-run：未落盘（加 --apply 生效）")

    rpt_dir = os.path.join(QB, "reports")
    os.makedirs(rpt_dir, exist_ok=True)
    rpt = os.path.join(rpt_dir, f"tick_collect_{date_int}.json")
    with open(rpt, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    log(f"报告: {rpt}")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
