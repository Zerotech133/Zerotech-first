#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S0 因子链探针：用已入湖的中文金融情感词典，给本地雪球帖打分。

目的不是产出可用因子，而是证明「外部数据入湖 -> 本地语料可消费 -> 产出可度量指标」
这条链在当前环境里真的通。只读本地库，不联网。

用法：
    python sentiment_probe.py                 # 默认抽 5000 条
    python sentiment_probe.py --limit 20000
    python sentiment_probe.py --write         # 结果写 parquet（默认只打印）
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
REPO = BASE.parents[1]
DICT_DIR = REPO / "quant_backtest" / "data" / "cnopendata" / "chinese-dictionary-of-financial-sentiment"
DB = REPO / "quant_backtest" / "data" / "xueqiu" / "xueqiu_info.db"
OUT_DIR = REPO / "quant_backtest" / "data" / "cnopendata" / "_probe"


def load_dict() -> tuple[set[str], set[str]]:
    parts = sorted(DICT_DIR.glob("part_*.parquet"))
    if not parts:
        raise SystemExit(f"词典未入湖：{DICT_DIR}")
    pos: set[str] = set()
    neg: set[str] = set()
    for p in parts:
        df = pd.read_parquet(p)
        col = df.columns[0]
        # 词条末尾带换行符，必须 strip，否则整本词典匹配不上
        words = {str(w).strip() for w in df[col].dropna() if str(w).strip()}
        if "positive" in p.name or "Positive" in col:
            pos |= words
        elif "negative" in p.name or "Negative" in col:
            neg |= words
        else:  # 兜底：按列名判断
            (neg if "neg" in col.lower() else pos).update(words)
    return pos, neg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5000)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    pos, neg = load_dict()
    print(f"词典：积极 {len(pos)} 词 / 消极 {len(neg)} 词")

    if not DB.exists():
        raise SystemExit(f"本地雪球库不存在：{DB}")

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        df = pd.read_sql_query(
            "SELECT status_id, created_at, title, desc, comment_count, like_count "
            "FROM xq_status WHERE (desc IS NOT NULL AND desc <> '') "
            "ORDER BY created_at DESC LIMIT ?",
            con, params=(args.limit,),
        )
    finally:
        con.close()
    print(f"取样帖子：{len(df)} 条（按 created_at 倒序，最新为 {df['created_at'].iloc[0] if len(df) else '-'}）")

    def score(text: str) -> tuple[int, int]:
        t = text or ""
        p = sum(1 for w in pos if w in t)
        n = sum(1 for w in neg if w in t)
        return p, n

    text = (df["title"].fillna("") + " " + df["desc"].fillna(""))
    counts = [score(t) for t in text]
    df["pos_hits"] = [c[0] for c in counts]
    df["neg_hits"] = [c[1] for c in counts]
    df["hits"] = df["pos_hits"] + df["neg_hits"]
    df["sentiment"] = [
        (p - n) / (p + n) if (p + n) else None for p, n in counts
    ]

    hit = df[df["hits"] > 0]
    print(f"命中词典的帖子：{len(hit)} / {len(df)}（{len(hit)/max(len(df),1):.1%}）")
    if len(hit):
        print(f"平均情感分（命中样本）：{hit['sentiment'].mean():+.4f}")
        print(f"情感分分布：min {hit['sentiment'].min():+.2f} / 中位 {hit['sentiment'].median():+.2f} / max {hit['sentiment'].max():+.2f}")
        print(f"平均命中词数：{hit['hits'].mean():.2f}")
        print("\n最正面 3 条：")
        for _, r in hit.nlargest(3, "sentiment").iterrows():
            print(f"  {r['sentiment']:+.2f} (+{r['pos_hits']}/-{r['neg_hits']}) {str(r['title'])[:40]}")
        print("最负面 3 条：")
        for _, r in hit.nsmallest(3, "sentiment").iterrows():
            print(f"  {r['sentiment']:+.2f} (+{r['pos_hits']}/-{r['neg_hits']}) {str(r['title'])[:40]}")

    if args.write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUT_DIR / "xq_sentiment_probe.parquet"
        df.to_parquet(out, index=False)
        print(f"\n已写出：{out}")
    else:
        print("\n（未写盘；加 --write 才落 parquet）")
    print(f"探针时间：{datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
