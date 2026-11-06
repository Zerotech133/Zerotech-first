# -*- coding: utf-8 -*-
"""P2 实体解析前置探针：摸清 Person / Seat 各数据源的真实形态.

只读，不写任何数据。输出 reports/p2_source_probe.json + 控制台摘要。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # quant_backtest 的上一级=项目根
sys.path.insert(0, str(ROOT / "quant_backtest"))

from warehouse_reader import WarehouseReader  # noqa: E402

_reader = WarehouseReader()
read = _reader.read

OUT = Path(__file__).resolve().parents[2] / "reports" / "p2_source_probe.json"

# (表, 抽样列, 主键列)  主键列用于估算实体基数
SOURCES = {
    # ---- Seat 侧 ----
    "ts_top_list": (["ts_code", "trade_date", "exalter", "sales_name", "net_buy",
                     "buy", "sell", "buy_rate", "reason"], "exalter"),
    "ts_block_trade": (["ts_code", "trade_date", "buyer", "seller", "price", "vol"], "buyer"),
    # ---- Person 侧 ----
    "ts_top10_holders": (["ts_code", "end_date", "holder_name", "hold", "hold_ratio"], "holder_name"),
    "ts_top10_floatholders": (["ts_code", "end_date", "holder_name", "hold", "hold_ratio"], "holder_name"),
    "ts_stk_managers": (["ts_code", "ann_date", "name", "title", "gender"], "name"),
    "ts_managers": (["ts_code", "ann_date", "name", "title"], "name"),
    "ts_stk_holdertrade": (["ts_code", "ann_date", "holder_name", "holder_type",
                            "in_de", "change_vol"], "holder_name"),
    "ts_holdernumber": (["ts_code", "end_date", "holder_num"], None),
    "ts_pledge_detail": (["ts_code", "ann_date", "holder_name", "pledge_amount"], "holder_name"),
    "ts_inner_trade": (["ts_code", "trade_date", "holder", "vol", "dir"], "holder"),
}

summary = {}
for table, (cols, keycol) in SOURCES.items():
    entry = {"table": table}
    try:
        df = read(table)
        entry["rows"] = len(df)
        entry["columns_present"] = [c for c in df.columns]
        missing = [c for c in cols if c not in df.columns]
        entry["missing_cols"] = missing
        use = [c for c in cols if c in df.columns]
        sub = df[use].drop_duplicates()
        entry["distinct_rows_sample"] = len(sub)
        if keycol and keycol in df.columns:
            n = df[keycol].dropna().astype(str).str.strip()
            n = n[n != ""]
            entry["key_col"] = keycol
            entry["distinct_names"] = int(n.nunique())
            entry["top20"] = n.value_counts().head(20).to_dict()
            entry["len_hist"] = n.str.len().value_counts().sort_index().head(20).to_dict()
        # 日期范围
        for dc in ("trade_date", "end_date", "ann_date"):
            if dc in df.columns:
                s = df[dc].dropna().astype(str)
                if len(s):
                    entry["date_col"] = dc
                    entry["date_min"] = str(s.min())
                    entry["date_max"] = str(s.max())
                break
    except Exception as e:  # noqa: BLE001
        entry["error"] = f"{type(e).__name__}: {e}"
    summary[table] = entry
    print(f"[{table}] rows={entry.get('rows')} names={entry.get('distinct_names')} "
          f"err={entry.get('error', '-')}")

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"\nsaved -> {OUT}")
