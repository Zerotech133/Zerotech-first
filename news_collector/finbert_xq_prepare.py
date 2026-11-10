# -*- coding: utf-8 -*-
"""P6 微调数据准备：雪球 LLM 标注 → train/test 切分（防泄漏）。

测试集 = 带票标注（norm_tickers != '[]'）分层 15%（与词典法 baseline
同评测域，可比）；训练集 = 全量标注剔除 test 的 status_id（防泄漏，
含非带票标注扩充口语覆盖）。种子固定，可复现。

产出（news_collector/data/finbert_xq/）：
  train.jsonl {text, label0}  label0 ∈ {0:positive/bullish,1:neutral,2:negative/bearish}
  test.jsonl  {text, label0, llm, status_id}
  manifest.json 切分统计

CLI：python finbert_xq_prepare.py [--db 路径] [--test-ratio 0.15]
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
XQ_DB = r"C:/aios/quant_backtest/data/xueqiu/xueqiu_info.db"
OUT_DIR = os.path.join(HERE, "data", "finbert_xq")
MIN_TEXT = 6
SEED = 20261109
# LLM 标签 → 模型 id（positive/neutral/negative 同序 bullish/neutral/bearish）
LAB = {"bullish": 0, "neutral": 1, "bearish": 2}


def _stable_hash(s):
    """status_id → [0,1) 稳定哈希（跨进程可复现，random 不用）。"""
    return int(hashlib.sha256(s.encode("utf-8")).hexdigest()[:12], 16) / float(2**48)


def load_all(db_path):
    db = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"),
                         uri=True)
    rows = db.execute(
        "SELECT c.status_id, c.sentiment, c.norm_tickers, s.title, s.desc "
        "FROM xq_status_classified c JOIN xq_status s USING (status_id) "
        "WHERE c.parse_ok=1 AND c.sentiment IN ('bullish','neutral','bearish') "
        "AND s.desc IS NOT NULL").fetchall()
    db.close()
    out = []
    for sid, sent, tickers, title, desc in rows:
        text = ("%s %s" % (title or "", desc or "")).strip()
        if len(text) >= MIN_TEXT:
            out.append({"status_id": sid, "llm": sent, "label0": LAB[sent],
                        "text": text,
                        "ticked": bool(tickers and tickers != "[]")})
    return out


def stratified_halfkey(rows, ratio):
    """按 label 分层、稳定哈希切分。返回 (test, rest)。"""
    test, rest = [], []
    by_lab = {}
    for r in rows:
        by_lab.setdefault(r["label0"], []).append(r)
    for lab, grp in sorted(by_lab.items()):
        grp = sorted(grp, key=lambda r: _stable_hash(r["status_id"]))
        k = int(round(len(grp) * ratio))
        test.extend(grp[:k])
        rest.extend(grp[k:])
    return test, rest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=XQ_DB)
    ap.add_argument("--test-ratio", type=float, default=0.15)
    args = ap.parse_args()

    rows = load_all(args.db)
    ticked = [r for r in rows if r["ticked"]]
    test, ticked_rest = stratified_halfkey(ticked, args.test_ratio)
    test_ids = {r["status_id"] for r in test}
    train = [r for r in rows if r["status_id"] not in test_ids]

    os.makedirs(OUT_DIR, exist_ok=True)
    def dump(name, rr, with_meta):
        with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8") as f:
            for r in rr:
                rec = {"text": r["text"], "label0": r["label0"]}
                if with_meta:
                    rec.update({"llm": r["llm"], "status_id": r["status_id"]})
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    dump("train.jsonl", train, False)
    dump("test.jsonl", test, True)

    man = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "seed": SEED, "test_ratio": args.test_ratio,
        "n_all_labeled": len(rows), "n_ticked": len(ticked),
        "train": {"n": len(train),
                  "dist": dict(Counter(r["llm"] for r in train))},
        "test": {"n": len(test),
                 "dist": dict(Counter(r["llm"] for r in test))},
        "leakage_guard": "train excludes test status_id (full scan)",
    }
    with open(os.path.join(OUT_DIR, "manifest.json"), "w",
              encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    print(json.dumps(man, ensure_ascii=False, indent=1))
    # 泄漏自检
    assert not (test_ids & {r["status_id"] for r in train})
    print("leakage check: OK (0 交集)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
