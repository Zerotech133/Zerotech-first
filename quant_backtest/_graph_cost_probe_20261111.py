"""只读: 量全量建图的时间/内存成本, 决定在线合成 or 离线落盘。

不写任何数据文件。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from intel_graph_view import GraphView
from intel_seat_shadow import load_seat_rows, seat_pairs
from intel_relation_rules import holding

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def run(limit_parts: int | None) -> None:
    t0 = time.time()
    seats = load_seat_rows(ROOT, limit_parts=limit_parts)
    t_load = time.time() - t0
    t1 = time.time()
    cands = seat_pairs(seats)
    t_pair = time.time() - t1
    g = GraphView()
    t2 = time.time()
    for c in cands:
        key = getattr(c, "evidence_key", "") or ""
        code = key.split(":")[-1] if key.startswith("top_inst:") else None
        g.add(c, stock_code=code)
    t_add = time.time() - t2
    print(
        f"limit_parts={limit_parts}: 读{t_load:.2f}s 配对{t_pair:.2f}s 建图{t_add:.2f}s "
        f"| 行={len(seats):,} 候选边={len(cands):,} 实体={g.stats()['entities']:,} "
        f"股票={g.stats()['stocks']:,}"
    )


def main() -> int:
    for n in (2, 21):
        run(n)
    # 股东全表
    t0 = time.time()
    h = pd.read_parquet(
        ROOT / "quant_backtest" / "data" / "tushare_curated" / "top10_holders.parquet"
    )
    print(f"股东全表读取 {time.time()-t0:.2f}s 行={len(h):,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
