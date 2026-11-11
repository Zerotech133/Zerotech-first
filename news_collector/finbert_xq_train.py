# -*- coding: utf-8 -*-
"""P6 微调：bardsai/finance-sentiment-zh-base → 雪球口语域适配 v1。

数据：finbert_xq_prepare.py 产物（train.jsonl/test.jsonl，label0 同序
0=positive/bullish 1=neutral 2=negative/bearish）。
评测：test 上 acc / 宏 F1 / 分档召回，并同测试集重算词典法 baseline
（score_text ±0.15 口径）直接对照——待超越基线 0.4914/0.4608（全量
9310 带票口径，test 子集数字略有差异）。

CLI：
  冒烟  python finbert_xq_train.py --max-samples 240 --epochs 1
  正式  python finbert_xq_train.py --epochs 2 --out models/finbert-xq-v1
产物：models/<out>/ （模型+tokenizer+eval.json）
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

import sentiment_score as SS

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data", "finbert_xq")
BASE_MODEL = "bardsai/finance-sentiment-zh-base"
ID2LAB = {0: "bullish", 1: "neutral", 2: "bearish"}  # 与 label0 同序


def read_jsonl(path):
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                out.append(json.loads(ln))
    return out


def metrics(golds, preds):
    cls = (0, 1, 2)
    acc = sum(1 for g, p in zip(golds, preds) if g == p) / len(golds)
    f1s, recs = {}, {}
    for c in cls:
        tp = sum(1 for g, p in zip(golds, preds) if g == c and p == c)
        fn = sum(1 for g, p in zip(golds, preds) if g == c and p != c)
        fp = sum(1 for g, p in zip(golds, preds) if g != c and p == c)
        rec = tp / (tp + fn) if tp + fn else 0.0
        prec = tp / (tp + fp) if tp + fp else 0.0
        recs[c] = rec
        f1s[c] = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"acc": round(acc, 4),
            "macro_f1": round(sum(f1s.values()) / 3, 4),
            "recall": {ID2LAB[c]: round(recs[c], 3) for c in cls},
            "f1": {ID2LAB[c]: round(f1s[c], 3) for c in cls}}


def lexicon_baseline(test_rows):
    golds = [r["label0"] for r in test_rows]
    preds = []
    lab2id = {"bullish": 0, "neutral": 1, "bearish": 2}
    for r in test_rows:
        preds.append(lab2id[SS.score_text(r["text"])["label"]])
    return metrics(golds, preds)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=DATA_DIR)
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--max-length", type=int, default=256)
    ap.add_argument("--max-samples", type=int, default=0,
                    help=">0 冒烟模式：训练集截断")
    ap.add_argument("--out", default="models/finbert-xq-smoke")
    ap.add_argument("--skip-lexicon", action="store_true")
    args = ap.parse_args()

    train_rows = read_jsonl(os.path.join(args.data_dir, "train.jsonl"))
    test_rows = read_jsonl(os.path.join(args.data_dir, "test.jsonl"))
    if args.max_samples:
        train_rows = train_rows[:args.max_samples]
    print("train=%d test=%d" % (len(train_rows), len(test_rows)), flush=True)

    lex = None if args.skip_lexicon else lexicon_baseline(test_rows)
    print("词典 baseline:", json.dumps(lex, ensure_ascii=False), flush=True)

    import torch
    from transformers import (AutoModelForSequenceClassification, AutoTokenizer,
                              DataCollatorWithPadding, Trainer,
                              TrainingArguments)

    # 模型已本地缓存；hub 探活会被代理 502 拦 → 离线加载
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    tok = AutoTokenizer.from_pretrained(BASE_MODEL, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        BASE_MODEL, num_labels=3, local_files_only=True)
    model.config.id2label = {0: "positive", 1: "neutral", 2: "negative"}
    model.config.label2id = {"positive": 0, "neutral": 1, "negative": 2}

    def enc(rows):
        return tok([r["text"] for r in rows], truncation=True,
                   max_length=args.max_length)

    class D(torch.utils.data.Dataset):
        def __init__(self, encd, labels):
            self.e, self.y = encd, labels

        def __len__(self):
            return len(self.y)

        def __getitem__(self, i):
            item = {k: v[i] for k, v in self.e.items()}
            item["labels"] = torch.tensor(self.y[i])
            return item

    collator = DataCollatorWithPadding(tok)

    train_ds = D(enc(train_rows), [r["label0"] for r in train_rows])
    test_ds = D(enc(test_rows), [r["label0"] for r in test_rows])

    targs = TrainingArguments(
        output_dir=os.path.join(HERE, args.out, "_ckpt"),
        per_device_train_batch_size=args.batch,
        per_device_eval_batch_size=args.batch * 2,
        num_train_epochs=args.epochs,
        learning_rate=args.lr,
        eval_strategy="epoch",
        save_strategy="no",
        logging_steps=20,
        report_to=[],
        use_cpu=True,
        seed=20261109,
        dataloader_num_workers=0,
    )
    trainer = Trainer(model=model, args=targs, train_dataset=train_ds,
                      eval_dataset=test_ds, data_collator=collator)
    t0 = time.time()
    trainer.train()
    train_secs = round(time.time() - t0, 1)

    pred = trainer.predict(test_ds)
    preds = pred.predictions.argmax(-1).tolist()
    golds = [r["label0"] for r in test_rows]
    ft = metrics(golds, preds)

    out_dir = os.path.join(HERE, args.out)
    os.makedirs(out_dir, exist_ok=True)
    trainer.save_model(out_dir)
    tok.save_pretrained(out_dir)
    ev = {"finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
          "base_model": BASE_MODEL,
          "train_n": len(train_rows), "test_n": len(test_rows),
          "epochs": args.epochs, "batch": args.batch, "lr": args.lr,
          "max_length": args.max_length, "train_secs": train_secs,
          "finetuned": ft, "lexicon_same_test": lex}
    with open(os.path.join(out_dir, "eval.json"), "w", encoding="utf-8") as f:
        json.dump(ev, f, ensure_ascii=False, indent=1)
    print(json.dumps(ev, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
