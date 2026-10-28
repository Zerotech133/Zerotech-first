#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""因子升级 v2：分词级情感打分，用来替代 v1 的子串匹配探针。

v1（sentiment_probe.py）的问题：用 `w in text` 直接子串匹配，
「好」「大」「高」会命中一切文本，导致 96% 命中率与 +0.34 的系统性正偏，
且「不看好」被判为正向。

v2 做法：
1. jieba 分词，并把词典词全部 add_word，避免多字词被切碎；
2. 分词后做精确匹配，不再子串扫描；
3. 否定词回看窗口内翻转极性；
4. 程度副词调整权重；
5. 输出情感分（极性）与强度（命中密度），并按日聚合成时间序列。

只读本地库与已入湖词典，不联网。

用法：
    python sentiment_v2.py --limit 20000 --write
"""
from __future__ import annotations

import argparse
import sqlite3
import warnings
from pathlib import Path

import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning)

BASE = Path(__file__).resolve().parent
REPO = BASE.parents[1]
DICT_DIR = REPO / "quant_backtest" / "data" / "cnopendata" / "chinese-dictionary-of-financial-sentiment"
DB = REPO / "quant_backtest" / "data" / "xueqiu" / "xueqiu_info.db"
OUT_DIR = REPO / "quant_backtest" / "data" / "cnopendata" / "_probe"

NEGATIONS_RAW = {"不", "没", "没有", "无", "未", "非", "别", "莫", "勿", "难以", "毫无",
                 "并未", "不是", "不会", "不能", "不足", "缺乏", "避免", "否认"}
STRONG = {"很", "非常", "极", "极其", "十分", "特别", "格外", "尤其", "太", "最"}
WEAK = {"略", "稍", "稍微", "有点", "有些", "略微", "较"}
# 否定只看紧邻前一个词。窗口放大到 2-3 会跨句翻转：
# 实测 2.4%-3.2% 的翻转里多数是误伤，且均分几乎不变，说明宽窗口只带来噪声。
NEG_WINDOW = 1


def load_dict() -> tuple[set[str], set[str]]:
    """读词典并去重（原始文件含 413 个重复词条）。"""
    pos: set[str] = set()
    neg: set[str] = set()
    for p in sorted(DICT_DIR.glob("part_*.parquet")):
        df = pd.read_parquet(p)
        col = df.columns[0]
        words = {str(w).strip() for w in df[col].dropna() if str(w).strip()}
        if "Negative" in col or "neg" in col.lower():
            neg |= words
        else:
            pos |= words
    return pos, neg


def build_tokenizer(pos: set[str], neg: set[str]):
    import jieba

    jieba.setLogLevel(60)
    for w in pos | neg:
        if len(w) >= 2:
            jieba.add_word(w, freq=10000)
    return lambda text: [t for t in jieba.lcut(text) if t.strip()]


def effective_negations(pos: set[str], neg: set[str]) -> set[str]:
    """剔除与情感词典重叠的否定词。

    `不能 / 不足 / 否认 / 毫无 / 缺乏 / 避免 / 难以` 既是否定词又是消极词，
    若同时参与翻转，会把「不足」这类消极判断翻成积极，属自相矛盾。
    与词典重叠时以情感词身份为准，不作否定标记。
    """
    conflict = NEGATIONS_RAW & (pos | neg)
    if conflict:
        print(f"否定词与情感词典重叠 {len(conflict)} 个，按情感词处理：{'、'.join(sorted(conflict))}")
    return NEGATIONS_RAW - (pos | neg)


def score_tokens(tokens: list[str], pos: set[str], neg: set[str],
                 negations: set[str] | None = None) -> dict:
    negations = NEGATIONS_RAW if negations is None else negations
    p = n = 0.0
    p_hits = n_hits = 0
    for i, tok in enumerate(tokens):
        if tok not in pos and tok not in neg:
            continue
        weight = 1.0
        window = tokens[max(0, i - NEG_WINDOW):i]
        negated = any(w in negations for w in window)
        for w in window:
            if w in STRONG:
                weight *= 1.5
            elif w in WEAK:
                weight *= 0.6
        if tok in pos:
            if negated:
                n += weight
                n_hits += 1
            else:
                p += weight
                p_hits += 1
        else:
            if negated:
                p += weight
                p_hits += 1
            else:
                n += weight
                n_hits += 1
    total = p + n
    return {
        "pos_w": p, "neg_w": n,
        "pos_hits": p_hits, "neg_hits": n_hits,
        "hits": p_hits + n_hits,
        "sentiment": (p - n) / total if total else None,
        "intensity": (p + n) / max(len(tokens), 1) if tokens else None,
        "n_tokens": len(tokens),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20000)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    pos, neg = load_dict()
    print(f"词典（去重后）：积极 {len(pos)} / 消极 {len(neg)}")
    negations = effective_negations(pos, neg)
    print(f"生效否定词 {len(negations)} 个")
    tokenize = build_tokenizer(pos, neg)

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
    print(f"取样：{len(df)} 条，最新 created_at = {df['created_at'].iloc[0]}")

    texts = (df["title"].fillna("") + " " + df["desc"].fillna("")).tolist()
    rows = [score_tokens(tokenize(t), pos, neg, negations) for t in texts]
    res = pd.DataFrame(rows)
    out = pd.concat([df.reset_index(drop=True), res], axis=1)

    # v1 对照：子串匹配
    v1_hits = [(sum(1 for w in pos if w in t), sum(1 for w in neg if w in t)) for t in texts]
    v1_total = [a + b for a, b in v1_hits]
    v1_sent = [(a - b) / (a + b) if (a + b) else None for a, b in v1_hits]

    m2 = out["hits"] > 0
    m1 = pd.Series(v1_total) > 0
    v1_ser = pd.Series(v1_sent, dtype="float64")

    print("\n=== v1 子串匹配 vs v2 分词打分 ===")
    print(f"命中率        v1 {m1.mean():.1%}   ->   v2 {m2.mean():.1%}")
    print(f"平均情感分    v1 {v1_ser[m1].mean():+.4f}   ->   v2 {out.loc[m2, 'sentiment'].mean():+.4f}")
    print(f"情感分中位    v1 {v1_ser[m1].median():+.4f}   ->   v2 {out.loc[m2, 'sentiment'].median():+.4f}")
    print(f"平均命中词数  v1 {(pd.Series(v1_total)[m1]).mean():.2f}   ->   v2 {out.loc[m2, 'hits'].mean():.2f}")
    print(f"负向占比      v1 {(v1_ser[m1] < 0).mean():.1%}   ->   v2 {(out.loc[m2, 'sentiment'] < 0).mean():.1%}")

    # 否定翻转实际生效了多少次
    flipped = int(((out["pos_hits"] + out["neg_hits"]) > 0).sum())
    print(f"\n有极性词的帖子：{flipped} 条；平均强度(命中/词数)：{out.loc[m2, 'intensity'].mean():.4f}")

    # 按日聚合，验证可形成时间序列
    out["date"] = out["created_at"].str.slice(0, 10)
    daily = (out.dropna(subset=["sentiment"])
                .groupby("date")
                .agg(posts=("status_id", "count"),
                     sentiment=("sentiment", "mean"),
                     intensity=("intensity", "mean"))
                .reset_index()
                .sort_values("date"))
    print(f"\n按日聚合：{len(daily)} 天，区间 {daily['date'].min()} ~ {daily['date'].max()}")
    print(daily.tail(8).to_string(index=False, float_format=lambda x: f"{x:+.3f}"))

    print("\n最正面 3 条（v2）：")
    for _, r in out.loc[m2].nlargest(3, "sentiment").iterrows():
        print(f"  {r['sentiment']:+.2f} (+{r['pos_hits']}/-{r['neg_hits']}) {str(r['title'])[:44]}")
    print("最负面 3 条（v2）：")
    for _, r in out.loc[m2].nsmallest(3, "sentiment").iterrows():
        print(f"  {r['sentiment']:+.2f} (+{r['pos_hits']}/-{r['neg_hits']}) {str(r['title'])[:44]}")

    if args.write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out.to_parquet(OUT_DIR / "xq_sentiment_v2.parquet", index=False)
        daily.to_parquet(OUT_DIR / "xq_sentiment_v2_daily.parquet", index=False)
        print(f"\n已写出：{OUT_DIR/'xq_sentiment_v2.parquet'}")
        print(f"已写出：{OUT_DIR/'xq_sentiment_v2_daily.parquet'}")
    else:
        print("\n（未写盘；加 --write 落 parquet）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
