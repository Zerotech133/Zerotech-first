# -*- coding: utf-8 -*-
"""数据湖三表增量前后对比验收（只读，不写任何分区）。

用法： python verify_incr_state.py [标签]
输出 JSON 落 C:\\Temp\\lake_state_<标签>.json，同时打印人读摘要。
"""
from __future__ import annotations

import glob
import json
import os
import sys

import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
TS = os.path.join(HERE, "data", "tushare")
TDX = os.path.join(HERE, "data", "tdx")

# 表根目录：语料类在 data/tushare，概念日线在 data/tdx（ths_dc_incr_sync.py 的 OUT）
TABLE_ROOT = {
    "irm_qa_sh": TS, "irm_qa_sz": TS, "news": TS,
    "major_news": TS, "research_report": TS,
    "ts_ths_daily": TDX, "ts_dc_daily": TDX,
}

# 表 -> 日期列候选（按序取第一个命中列）
TARGETS = {
    "irm_qa_sh":      ["trade_date", "pub_date", "date"],
    "irm_qa_sz":      ["trade_date", "pub_date", "date"],
    "news":           ["datetime", "pub_time", "date"],
    "major_news":     ["pub_time", "datetime", "date"],
    "research_report": ["trade_date", "pub_date", "date"],
    "ts_ths_daily":   ["trade_date", "date"],
    "ts_dc_daily":    ["trade_date", "date"],
}


def norm(x) -> str:
    return str(x)[:10].replace("-", "").strip()


def scan_table(tbl: str):
    """返回 dict：分区数/行数/日期列/最早最新/缺日期列的分区。"""
    d = os.path.join(TABLE_ROOT.get(tbl, TS), tbl)
    parts = sorted(glob.glob(os.path.join(d, "part_*.parquet")))
    if not parts:
        return {"exists": False, "parts": 0, "rows": 0}
    rows = 0
    dates: list[str] = []
    dcol = None
    no_date_col = []
    for p in parts:
        try:
            pf = pq.ParquetFile(p)
            names = pf.schema_arrow.names
            n = pf.metadata.num_rows
            rows += n
            cand = next((c for c in TARGETS.get(tbl, []) if c in names), None)
            if cand is None:
                no_date_col.append(os.path.basename(p))
                continue
            dcol = dcol or cand
            col = pq.read_table(p, columns=[cand]).column(0).to_pylist()
            dates.extend(norm(x) for x in col if x)
        except Exception as e:  # noqa: BLE001
            no_date_col.append("%s(ERR %s)" % (os.path.basename(p), str(e)[:40]))
    out = {
        "exists": True,
        "parts": len(parts),
        "rows": rows,
        "date_col": dcol,
        "n_dates": len(set(dates)),
        "min_date": min(dates) if dates else None,
        "max_date": max(dates) if dates else None,
        "no_date_col_parts": no_date_col,
    }
    return out


def scan_global_fut():
    """外盘商品：GF_*.parquet 在 ts_global_fut/ 与 data/ 两处都查。"""
    found = {}
    for root in (os.path.join(HERE, "ts_global_fut"),
                 os.path.join(HERE, "data", "ts_global_fut")):
        for p in sorted(glob.glob(os.path.join(root, "**", "GF_*.parquet"), recursive=True)):
            try:
                pf = pq.ParquetFile(p)
                rows = pf.metadata.num_rows
                names = pf.schema_arrow.names
                cand = next((c for c in ("trade_date", "date", "date_str") if c in names), None)
                ds = []
                if cand:
                    ds = [norm(x) for x in
                          pq.read_table(p, columns=[cand]).column(0).to_pylist() if x]
                rel = os.path.relpath(p, HERE)
                found[rel] = {
                    "rows": rows,
                    "date_col": cand,
                    "max_date": max(ds) if ds else None,
                    "min_date": min(ds) if ds else None,
                }
            except Exception as e:  # noqa: BLE001
                found[os.path.relpath(p, HERE)] = {"error": str(e)[:80]}
    return found


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "adhoc"
    res = {"label": label, "tables": {}, "global_fut": scan_global_fut()}
    for t in TARGETS:
        res["tables"][t] = scan_table(t)
    p = r"C:\Temp\lake_state_%s.json" % label
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)

    print("== tushare 分区表（label=%s）==" % label)
    print("%-18s %6s %10s %-13s %-11s %-11s %s"
          % ("表", "分区数", "行数", "日期列", "最早", "最新", "异常分区"))
    for t, v in res["tables"].items():
        if not v.get("exists"):
            print("%-18s %6s %10s  —— 主表不存在 ——" % (t, 0, 0))
            continue
        print("%-18s %6d %10d %-13s %-11s %-11s %s"
              % (t, v["parts"], v["rows"], v.get("date_col") or "-",
                 v.get("min_date") or "-", v.get("max_date") or "-",
                 ",".join(v.get("no_date_col_parts") or []) or "-"))
    gf = res["global_fut"]
    print("\n== 外盘商品 GF_*.parquet：%d 个 ==" % len(gf))
    tot = 0
    for k, v in sorted(gf.items()):
        tot += v.get("rows", 0)
        print("  %-42s rows=%-7s 日期列=%-8s 最新=%s"
              % (k, v.get("rows"), v.get("date_col"), v.get("max_date")))
    print("  合计行数=%d" % tot)
    print("\nJSON -> %s" % p)


if __name__ == "__main__":
    main()
