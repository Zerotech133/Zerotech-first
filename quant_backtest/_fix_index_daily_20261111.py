"""补 index_daily 缺的交易日日片（只写缺失日，不覆盖已有片）。

背景: index_daily 供 _load_index_meta 取 000300.SH / 000001.SH 的收益与量能,
用于 beta/alpha/rsrs/量能因子; 但它不在 ts_tail_sync.RECIPES 里, 日常不自动更新。
本脚本按 trade_date 拉全量指数, 校验 schema 与既有日片一致后写 d_<date>.parquet。

用法:
  python -X utf8 quant_backtest/_fix_index_daily_20261111.py --dry
  python -X utf8 quant_backtest/_fix_index_daily_20261111.py --date 20261110
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

ROOT = Path(__file__).resolve().parents[1]
QB = ROOT / "quant_backtest"
DIR = QB / "data" / "tushare" / "index_daily"
API = "http://api.tushare.pro"


def token() -> str:
    for ln in open(QB / ".env.local", encoding="utf-8"):
        if ln.startswith("XT_DATA_KEY="):
            return ln.split("=", 1)[1].strip()
    raise SystemExit("TOKEN 缺失")


def call(tok: str, api: str, params: dict, fields: str, offset: int, limit: int = 5000):
    r = requests.post(
        API,
        json={"api_name": api, "token": tok, "params": {**params, "offset": offset, "limit": limit},
              "fields": fields},
        timeout=40,
    )
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"api err {d.get('code')}: {str(d.get('msg'))[:120]}")
    return d["data"]["fields"], d["data"]["items"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="20261110")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    ref = DIR / "d_20261109.parquet"
    if not ref.exists():
        print("找不到参照日片 d_20261109.parquet")
        return 1
    ref_schema = pq.ParquetFile(ref).schema_arrow
    fields = list(ref_schema.names)
    print(f"参照 schema: {len(fields)} 列")

    out = DIR / f"d_{a.date}.parquet"
    if out.exists():
        print(f"目标已存在，不覆盖: {out}")
        return 0

    tok = token()
    rows: list = []
    off = 0
    while True:
        fl, items = call(tok, "index_daily", {"trade_date": a.date}, ",".join(fields), off)
        if not items:
            break
        rows.extend(items)
        print(f"  拉取 off={off} 得 {len(items)} 行，累计 {len(rows)}", flush=True)
        if len(items) < 5000:
            break
        off += 5000
        if off > 60000:
            print("  超过 6 万行上限，停止")
            break

    print(f"共 {len(rows)} 行")
    if not rows:
        print("上游无该日数据，不写盘")
        return 1

    df = pd.DataFrame(rows, columns=fl)
    # 按参照 schema 对齐列序与类型
    for c in fields:
        if c not in df.columns:
            df[c] = None
    df = df[fields]
    try:
        tbl = pa.Table.from_pandas(df, schema=ref_schema, preserve_index=False)
    except Exception as exc:
        print(f"schema 对齐失败: {exc}")
        return 1

    if a.dry:
        print(f"[DRY] 将写 {out} rows={tbl.num_rows}")
        print(df.head(3).to_string())
        return 0

    pq.write_table(tbl, out)
    back = pq.read_table(out)
    print(f"已写 {out} rows={back.num_rows} cols={back.num_columns}")
    hit = df[df["ts_code"].astype(str).isin(["000300.SH", "000001.SH"])]
    print(f"000300/000001 命中 {len(hit)} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
