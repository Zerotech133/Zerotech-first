"""只读探针: 量化图谱三源边在选股池上的真实覆盖上限。

不建图、不落库。只回答一个问题:
  把 ts_top_inst 从末 2 分区放开到全量、把 top10_holders 从 tail(300)
  放开到全表, 能让 GraphView.dossier() 命中选股池多少只?

用法: python -X utf8 quant_backtest/_graph_cover_probe_20261111.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SEAT_DIR = ROOT / "quant_backtest" / "data" / "tdx" / "ts_top_inst"
HOLD = ROOT / "quant_backtest" / "data" / "tushare_curated" / "top10_holders.parquet"
PICKS = ROOT / "portal" / "data" / "todays_picks.json"


def _ts(c: str) -> str:
    """6 位裸码归一为 ts_code 口径(与 ts_top_inst 对齐)。"""
    c = str(c).strip()
    if "." in c:
        return c
    if c.startswith(("6", "9")):
        return c + ".SH"
    if c.startswith(("4", "8")):
        return c + ".BJ"
    return c + ".SZ"


def picks_codes() -> set[str]:
    d = json.loads(PICKS.read_text(encoding="utf-8"))
    items = d.get("stocks") or d.get("items") or []
    out = set()
    for it in items:
        if isinstance(it, dict):
            c = it.get("code") or it.get("ts_code")
            if c:
                out.add(_ts(c))
    return out


def main() -> int:
    codes = picks_codes()
    print(f"[池] todays_picks 代码数 = {len(codes)}")

    parts = sorted(SEAT_DIR.glob("part_*.parquet")) if SEAT_DIR.exists() else []
    print(f"[席位] 分区数 = {len(parts)}")

    if not parts:
        print("[席位] DATASET_MISSING")
        return 1

    def cover(sel: list[Path], tag: str) -> tuple[set[str], int]:
        codeset: set[str] = set()
        rows = 0
        for p in sel:
            df = pd.read_parquet(p, columns=["ts_code", "trade_date"])
            rows += len(df)
            codeset |= set(df["ts_code"].astype(str))
            del df
        hit = codeset & codes
        print(f"[席位] {tag}: 行={rows:,} 覆盖股票={len(codeset):,} 命中池={len(hit)}")
        return codeset, rows

    _, _ = cover(parts[-2:], "末2分区(现行 build_view)")
    full, _ = cover(parts, "全量")

    hold_codes: set[str] = set()
    if HOLD.exists():
        h = pd.read_parquet(HOLD, columns=["ts_code"])
        tail = pd.read_parquet(HOLD, columns=["ts_code"]).tail(300)
        hold_codes = set(h["ts_code"].astype(str))
        print(
            f"[股东] 全表={len(h):,}行/{len(hold_codes):,}只 "
            f"tail300={len(tail)}行/{len(set(tail['ts_code'].astype(str))):,}只 "
            f"池内命中 tail300={len(set(tail['ts_code'].astype(str)) & codes)} "
            f"全表={len(hold_codes & codes)}"
        )

    now = (set(pd.read_parquet(parts[-1], columns=['ts_code'])['ts_code'].astype(str))
           | set(pd.read_parquet(parts[-2], columns=['ts_code'])['ts_code'].astype(str)))
    now_hold = set(pd.read_parquet(HOLD, columns=["ts_code"]).tail(300)["ts_code"].astype(str)) if HOLD.exists() else set()

    print("\n=== 结论 ===")
    print(f"现行(末2分区+tail300) 池内可查边 = {len((now | now_hold) & codes)} / {len(codes)}")
    print(f"放开全量(席位全量+股东全表) 池内可查边 = {len((full | hold_codes) & codes)} / {len(codes)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
