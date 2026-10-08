# -*- coding: utf-8 -*-
"""K-STOCK S1 PoC · 日终 runner

用法：
  python k_stock/run_poc.py 600519.SH 贵州茅台 [--mock] [--out data/poc_YYYYMMDD.json]
  python k_stock/run_poc.py --batch data/watchlist.txt   # 批量（每行 "代码 名称"）

产出：data/poc_<日期>.json（五节点结构化输出+trace+degraded，lineage 可追溯）
S1 判据挂钩：连续 5 个交易日无人干预跑通；picks 次日收益秩相关>0（对照脚本 S2 挂）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from k_stock.kstock.llm import LLMClient
from k_stock.kstock.orchestrator import Orchestrator, load_team

TEAM = HERE / "config" / "team_poc.json"
OUT_DIR = HERE / "data"


def run_one(stock: str, context: str = "", mock: bool = False) -> dict:
    nodes = load_team(TEAM)
    client = LLMClient(mock=mock)
    orch = Orchestrator(nodes, client.chat)
    result = orch.run(stock=stock, context=context)
    result["stock"] = stock
    result["asof"] = datetime.now().isoformat(timespec="seconds")
    result["mock"] = mock
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stock", nargs="?", help="如 600519.SH 贵州茅台（加引号）")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--batch", help="清单文件，每行：代码 名称")
    ap.add_argument("--out", help="输出 json 路径（默认 data/poc_<时间戳>.json）")
    args = ap.parse_args()

    targets: list[str] = []
    if args.batch:
        for line in Path(args.batch).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                targets.append(line)
    elif args.stock:
        targets.append(args.stock)
    else:
        ap.error("需要 stock 或 --batch")

    OUT_DIR.mkdir(exist_ok=True)
    results = []
    for stock in targets:
        print(f"[run] {stock} ...", flush=True)
        try:
            r = run_one(stock, mock=args.mock)
            d1 = r["outputs"].get("D1", {})
            print(f"  -> D1: {d1.get('action')} conf={d1.get('confidence')} "
                  f"degraded={r['degraded'] or '无'}", flush=True)
        except Exception as e:
            r = {"stock": stock, "error": str(e)[:300],
                 "asof": datetime.now().isoformat(timespec="seconds")}
            print(f"  -> FAIL: {r['error']}", flush=True)
        results.append(r)

    out = Path(args.out) if args.out else OUT_DIR / f"poc_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved ->", out)


if __name__ == "__main__":
    main()
