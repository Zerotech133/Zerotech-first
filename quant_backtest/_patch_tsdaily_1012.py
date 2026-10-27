# -*- coding: utf-8 -*-
"""K-STOCK-309：补 ts_daily 湖 20261012 断档。
重拉 202610 月窗口 → 与现有含 202610 的 part 合并 → drop_duplicates 回写 →
更新 manifest rows。之后跑 adj_factor_sync.py 补复权因子。
"""

import json
import os
import sys
import glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
import _pull_tsdaily_monthly as P

OUT = P.OUT
MANIFEST = P.MANIFEST

rows, cols, total = P.pull_month(2026, 10)
new = pd.DataFrame([dict(zip(cols, r)) for r in rows])
print("pulled 202610 rows=%d cols=%d" % (len(new), len(new.columns)))

# 找现有含 202610 数据的 part（读 trade_date 前缀判定）
hit_parts = []
for f in sorted(glob.glob(os.path.join(OUT, "part_*.parquet"))):
    td = pd.read_parquet(f, columns=["trade_date"])
    if (td["trade_date"].astype(str).str.startswith("202610")).any():
        hit_parts.append(f)
print("existing parts with 202610:", [os.path.basename(x) for x in hit_parts])
assert len(hit_parts) == 1, "expect exactly 1 oct part, got %d" % len(hit_parts)

old = pd.read_parquet(hit_parts[0])
merged = pd.concat([old, new[new.columns]]).drop_duplicates(
    subset=["ts_code", "trade_date"], keep="last"
)
merged.to_parquet(hit_parts[0] + ".tmp", index=False)
os.replace(hit_parts[0] + ".tmp", hit_parts[0])
added = len(merged) - len(old)
print("part rewritten rows %d -> %d (added %d)" % (len(old), len(merged), added))

# 校验最新日期
td = pd.read_parquet(hit_parts[0], columns=["trade_date"]).astype(str)
print("oct part max date:", td["trade_date"].max())

# manifest rows 权威重算
n_rows = 0
for f in sorted(glob.glob(os.path.join(OUT, "part_*.parquet"))):
    n_rows += pd.read_parquet(f, columns=["ts_code"]).shape[0]
mm = json.load(open(MANIFEST, encoding="utf-8"))
mm["ts_daily"]["rows"] = n_rows
mm["ts_daily"]["done"] = True
tmp = MANIFEST + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(mm, fh)
os.replace(tmp, MANIFEST)
print("manifest ts_daily rows -> %d" % n_rows)
print("TSDAILY_1012_PATCH_DONE")
