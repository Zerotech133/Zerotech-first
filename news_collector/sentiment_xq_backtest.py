# -*- coding: utf-8 -*-
"""P6 前置：词典法 vs 雪球 LLM 金标准回测（雪球源扩面判据）。

动机：sentiment_card 雪球源只认 xq_status_classified 的 LLM 标注行，
7 天窗对队列票仅 7 命中（覆盖稀疏）。若词典法在雪球口语上对照
LLM 金标准准确率够格，则可对全量 xq_status 打分扩面（零采集
零训练成本）；不够格则维持现状等 P6 微调。

方法：
  · 样本 = xq_status_classified(norm_tickers != '[]', parse_ok=1)
    JOIN xq_status，文本 = title + desc，长度 >= 6；
  · 词典法 score_text（label 阈值 ±0.15，同引擎口径）；
  · 对照 LLM sentiment 三分类：混淆矩阵 / 总体准确率 / 宏 F1 /
    bullish、bearish 召回。

判据（写入报告）：总体准确率 >= 0.60 且 bearish 召回 >= 0.50
→ 扩面（LLM 标注优先、无标注词典兜底）；否则不动。

CLI：python sentiment_xq_backtest.py [--limit 0] [--db 路径]
"""
import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime

import sentiment_score as SS

HERE = os.path.dirname(os.path.abspath(__file__))
XQ_DB = r"C:/aios/quant_backtest/data/xueqiu/xueqiu_info.db"
MIN_TEXT = 6

CLASSES = ("bullish", "neutral", "bearish")


def load_sample(db_path, limit=0):
    """带票标注 join 原帖文本。返回 [(llm_sent, text), ...]"""
    db = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"),
                         uri=True)
    sql = (
        "SELECT c.sentiment, s.title, s.desc "
        "FROM xq_status_classified c JOIN xq_status s USING (status_id) "
        "WHERE c.parse_ok=1 AND c.norm_tickers IS NOT NULL "
        "AND c.norm_tickers != '[]' AND s.desc IS NOT NULL")
    if limit:
        sql += " LIMIT %d" % int(limit)
    out = []
    for sent, title, desc in db.execute(sql):
        text = ("%s %s" % (title or "", desc or "")).strip()
        if len(text) >= MIN_TEXT and sent in CLASSES:
            out.append((sent, text))
    db.close()
    return out


def confusion(golds, preds):
    """混淆矩阵 + 指标。golds/preds 同序 label 列表。"""
    cm = {g: Counter() for g in CLASSES}
    for g, p in zip(golds, preds):
        cm[g][p] += 1
    acc = sum(1 for g, p in zip(golds, preds) if g == p) / len(golds)
    recalls = {}
    f1s = {}
    for c in CLASSES:
        tp = cm[c][c]
        fn = sum(cm[c].values()) - tp
        fp = sum(cm[g][c] for g in CLASSES if g != c)
        rec = tp / (tp + fn) if tp + fn else 0.0
        prec = tp / (tp + fp) if tp + fp else 0.0
        recalls[c] = rec
        f1s[c] = (2 * prec * rec / (prec + rec)) if prec + rec else 0.0
    macro_f1 = sum(f1s.values()) / len(CLASSES)
    return cm, acc, recalls, f1s, macro_f1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=XQ_DB)
    ap.add_argument("--limit", type=int, default=0, help="0=全量")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    rows = load_sample(args.db, args.limit)
    print("样本 %d 条（带票+文本>=%d字）" % (len(rows), MIN_TEXT))
    golds = [g for g, _ in rows]
    preds = [SS.score_text(t)["label"] for _, t in rows]
    cm, acc, recalls, f1s, macro_f1 = confusion(golds, preds)

    L = ["# 雪球源扩面判据回测：词典法 vs LLM 金标准 %s" %
         datetime.now().strftime("%Y-%m-%d %H:%M"), ""]
    L.append("样本 %d 条（xq_status_classified 带票 parse_ok=1 JOIN "
             "xq_status，title+desc >= %d 字）" % (len(rows), MIN_TEXT))
    L.append("")
    L.append("**总体准确率 %.4f · 宏 F1 %.4f**" % (acc, macro_f1))
    L.append("")
    L.append("| 金标准\\词典 | bullish | neutral | bearish | 召回 | F1 |")
    L.append("|---|---:|---:|---:|---:|---:|")
    for g in CLASSES:
        tot = sum(cm[g].values())
        L.append("| %s(n=%d) | %d | %d | %d | %.3f | %.3f |" % (
            g, tot, cm[g]["bullish"], cm[g]["neutral"], cm[g]["bearish"],
            recalls[g], f1s[g]))
    L.append("")
    ok = acc >= 0.60 and recalls["bearish"] >= 0.50
    L.append("**判据**（acc>=0.60 且 bearish 召回>=0.50）：**%s** → %s" % (
        "过" if ok else "不过",
        "雪球源可扩面（LLM 标注优先、无标注词典兜底）" if ok
        else "维持 LLM-only，等 P6 微调"))
    rep = "\n".join(L) + "\n"
    out = args.out or os.path.join(
        os.path.dirname(HERE), "reports",
        "xq_lexicon_backtest_%s.md" % datetime.now().strftime("%Y%m%d"))
    with open(out, "w", encoding="utf-8") as f:
        f.write(rep)
    print(rep)
    print("report:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
