# -*- coding: utf-8 -*-
"""探测 akshare A 股涨停/情绪数据接口可用性（只探测，不接产线，可重复跑）。

用法:
  C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe tools/probe_akshare_sentiment.py

探测对象:
  a) stock_zt_pool_em        涨停股池（当日 + 历史某日）
  b) stock_zt_pool_zbgc_em   炸板股池（炸板率由 涨停+炸板 推导）
  c) 连板高度 = max(涨停池.连板数)   （另核对 stock_zt_pool_strong_em 的连板字段）

结果落盘 reports/akshare_sentiment_probe.json（若以 --save 运行）。
"""
import os
import sys
import json
import time
import datetime as dt

# 直连：避免吃系统代理导致 502/超时（akshare 底层 requests 会读环境变量）
os.environ["NO_PROXY"] = "*"
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)

import akshare as ak
import pandas as pd

TODAY = dt.date.today().strftime("%Y%m%d")
HIST_DATE = "20261013"
OUT_JSON = os.path.join(os.path.dirname(__file__), "..", "reports", "akshare_sentiment_probe.json")


def probe_call(name: str, fn, **kwargs) -> dict:
    """统一探测包装：记录成功与否、行数、字段、耗时、日期锚。"""
    rec = {"api": name, "params": kwargs, "ok": False, "rows": 0,
           "columns": [], "elapsed_s": 0.0, "error": None, "date_anchor": None,
           "sample": None, "extra": {}}
    t0 = time.time()
    try:
        df = fn(**kwargs)
        rec["elapsed_s"] = round(time.time() - t0, 2)
        rec["ok"] = df is not None and len(df) > 0
        if df is not None and len(df) > 0:
            rec["rows"] = int(len(df))
            rec["columns"] = list(df.columns)
            rec["sample"] = df.head(3).to_dict(orient="records")
            if "日期" in df.columns:
                rec["date_anchor"] = str(df["日期"].iloc[0])
            if "连板数" in df.columns:
                rec["extra"]["max_lianban"] = int(pd.to_numeric(df["连板数"]).max())
        else:
            rec["rows"] = 0 if df is None else int(len(df))
            rec["columns"] = [] if df is None else list(df.columns)
    except Exception as e:
        rec["elapsed_s"] = round(time.time() - t0, 2)
        rec["error"] = f"{type(e).__name__}: {e}"
    return rec


def main() -> int:
    results = {"probe_time": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "akshare_version": ak.__version__, "today": TODAY, "hist_date": HIST_DATE,
               "probes": []}
    probes = [
        ("涨停股池-当日", ak.stock_zt_pool_em, {"date": TODAY}),
        ("涨停股池-历史20261013", ak.stock_zt_pool_em, {"date": HIST_DATE}),
        ("炸板股池-当日", ak.stock_zt_pool_zbgc_em, {"date": TODAY}),
        ("炸板股池-历史20261013", ak.stock_zt_pool_zbgc_em, {"date": HIST_DATE}),
        ("强势股池-当日(核对连板字段)", ak.stock_zt_pool_strong_em, {"date": TODAY}),
        ("昨日涨停股池-当日", ak.stock_zt_pool_previous_em, {"date": TODAY}),
    ]
    for label, fn, kw in probes:
        rec = probe_call(label, fn, **kw)
        results["probes"].append(rec)
        status = "OK" if rec["ok"] else ("EMPTY" if rec["error"] is None and rec["rows"] == 0 else "FAIL")
        print(f"[{status}] {label}  rows={rec['rows']}  {rec['elapsed_s']}s"
              f"  anchor={rec['date_anchor']}  maxLB={rec['extra'].get('max_lianban', '-')}"
              + (f"  ERR={rec['error']}" if rec["error"] else ""))

    # 连板高度推导：从涨停池取 max(连板数)，展示 top5
    lb_detail = None
    hist = next((r for r in results["probes"] if r["api"] == "涨停股池-历史20261013"), None)
    if hist and hist["ok"]:
        df = ak.stock_zt_pool_em(date=HIST_DATE)
        df["连板数"] = pd.to_numeric(df["连板数"])
        top = df.nlargest(5, "连板数")[["代码", "名称", "连板数"]]
        lb_detail = {"date": HIST_DATE, "max_lianban": int(df["连板数"].max()),
                     "top5": top.to_dict(orient="records")}
        results["lianban_derive"] = lb_detail
        tops = ["{}x{}".format(r["名称"], r["连板数"]) for r in lb_detail["top5"]]
        print(f"[OK] 连板高度推导 {HIST_DATE}: max={lb_detail['max_lianban']} top5={tops}")

    # 炸板率推导：炸板数/(涨停数+炸板数)
    zt = next((r for r in results["probes"] if r["api"] == "涨停股池-历史20261013"), None)
    zb = next((r for r in results["probes"] if r["api"] == "炸板股池-历史20261013"), None)
    if zt and zb and zt["ok"] and zb["ok"]:
        rate = zb["rows"] / (zt["rows"] + zb["rows"])
        results["zhaban_rate_hist"] = {"date": HIST_DATE, "zt": zt["rows"],
                                       "zb": zb["rows"], "rate": round(rate, 4)}
        print(f"[OK] 炸板率推导 {HIST_DATE}: {zb['rows']}/({zt['rows']}+{zb['rows']})={rate:.2%}")

    out = os.path.normpath(OUT_JSON)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"saved -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
