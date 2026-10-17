# -*- coding: utf-8 -*-
"""拉取全球指数日线（tushare index_global 接口，21 个指数）。

指数清单：HSI 恒生 / HKTECH 恒生科技 / SPX 标普500 / DJI 道琼斯 /
IXIC 纳斯达克 / N225 日经 / KS11 韩国 / GDAXI 德国 / FTSE 英国 /
FCHI 法国 / SX5E 欧元区 / RUT 罗素2000 / SENSEX 印度 / AS51 澳洲 /
RTS 俄罗斯 / SPTSX 加拿大 / IBOVESPA 巴西 / TWII 台湾 / XIN9 新加坡 /
CKLSE 马来西亚 / HKAH 港股AH溢价

落盘：data/tushare/index_global/<code>.parquet（by_code，断点续传：
     文件存在则从最新日+1 续拉，仅补增量）

用法：
  python pull_global_idx.py            # 全量+增量
  python pull_global_idx.py --from 20200101   # 指定起点重拉
"""
import argparse
import os
import sys
import time
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tushare_sync as T   # 复用 token/pro 初始化与限频工具

OUT_DIR = os.path.join(HERE, "data", "tushare", "index_global")

GLOBAL_IDX = [
    "HSI", "HKTECH", "SPX", "DJI", "IXIC", "N225", "KS11",
    "GDAXI", "FTSE", "FCHI", "SX5E", "RUT", "SENSEX", "AS51",
    "RTS", "SPTSX", "IBOVESPA", "TWII", "XIN9", "CKLSE", "HKAH",
]


def pull_one(code, start, end):
    """单指数全量拉取（接口单次上限约 8000 行，按年分段拉）。"""
    frames = []
    seg_start = int(start)
    seg_end = int(end)
    # 按两年一段切（日线 ~500行/年，两年~1000行 < 单次上限）
    edges = []
    y = seg_start // 10000
    y_end = seg_end // 10000
    while y <= y_end:
        s = max(seg_start, y * 10000 + 101)
        e = min(seg_end, (y + 1) * 10000 - 101 if y < y_end else seg_end)
        if s <= e:
            edges.append((s, e))
        y += 1
    for s, e in edges:
        for attempt in range(3):
            try:
                data = T.ts_call("index_global",
                                 {"ts_code": code, "start_date": str(s),
                                  "end_date": str(e)})
                items = (data or {}).get("items") or []
                if items:
                    fields = data["fields"]
                    frames.append(pd.DataFrame(items, columns=fields))
                break
            except Exception as ex:
                if attempt == 2:
                    print("  ! %s %d-%d 失败: %s" % (code, s, e, ex))
                else:
                    time.sleep(1.2 * (attempt + 1))
        time.sleep(0.35)   # 限频 ~3次/秒 以内
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="20180101",
                    help="起始日（默认 20180101）")
    args = ap.parse_args()
    end = datetime.now().strftime("%Y%m%d")

    os.makedirs(OUT_DIR, exist_ok=True)

    total_new = 0
    for code in GLOBAL_IDX:
        f = os.path.join(OUT_DIR, "%s.parquet" % code)
        start = args.start
        if os.path.exists(f):
            old = pd.read_parquet(f)
            if len(old):
                last = str(old["trade_date"].max())
                # 已是最新（今天或昨晚）则跳过
                if last >= (datetime.now().strftime("%Y%m%d")):
                    print("  = %s 已最新 %s" % (code, last))
                    continue
                start = str(int(last) + 1)
                if start > end:
                    print("  = %s 已最新 %s" % (code, last))
                    continue
        df = pull_one(code, start, end)
        if df is None or not len(df):
            print("  - %s 无数据 [%s,%s]" % (code, start, end))
            continue
        if os.path.exists(f):
            old = pd.read_parquet(f)
            df = pd.concat([old, df], ignore_index=True)
        df = df.drop_duplicates(subset=["ts_code", "trade_date"]).sort_values("trade_date")
        df.to_parquet(f, index=False)
        print("  + %s: %d 行 (%s→%s)" % (code, len(df),
                                         df["trade_date"].min(), df["trade_date"].max()))
        total_new += len(df)

    print("=== 全球指数拉取完成，共 %d 行 ===" % total_new)


if __name__ == "__main__":
    from linreg import run_with_lineage
    run_with_lineage("kg_daily_sync", "pull_global_idx", main)
