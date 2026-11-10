# -*- coding: utf-8 -*-
"""雪球源影子对照：finbert-xq-v1 vs 词典，在 xq_timeline 实时帖上并跑。
纪律：只出数字与判据，不改主管线（sentiment_card 仍词典法）；切换裁定归上级。
用法: python finbert_xq_shadow.py [--n 600]
产物: data/xq_shadow_{date}.json + reports/xq_shadow_{date}.md
"""
import argparse
import glob
import json
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from sentiment_score import score_text  # 词典通道（现役）


def load_posts(n):
    files = sorted(glob.glob(os.path.join(HERE, "data", "xq_timeline", "*.jsonl")))
    posts = []
    for f in files:
        for ln in open(f, encoding="utf-8"):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            t = (r.get("text") or "").strip()
            if len(t) >= 8:
                posts.append(t)
    random.seed(20261110)
    if len(posts) > n:
        posts = random.sample(posts, n)
    return posts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=600)
    args = ap.parse_args()

    posts = load_posts(args.n)
    print("影子样本 %d 条（data/xq_timeline 实时帖）" % len(posts))

    # 词典通道
    lex = [score_text(t) for t in posts]

    # 模型通道
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    mdir = os.path.join(HERE, "models", "finbert-xq-v1")
    tok = AutoTokenizer.from_pretrained(mdir, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        mdir, local_files_only=True)
    model.eval()
    id2label = model.config.id2label
    LABELS = ["bullish", "neutral", "bearish"]  # 与 id2label 0/1/2 同序（prepare 时已核）

    preds = []
    BS = 64
    with torch.no_grad():
        for i in range(0, len(posts), BS):
            batch = posts[i:i + BS]
            enc = tok(batch, padding=True, truncation=True,
                      max_length=192, return_tensors="pt")
            logits = model(**enc).logits
            probs = torch.softmax(logits, dim=-1)
            for p in probs:
                preds.append(p.tolist())

    def dist(scores):
        c = {"bullish": 0, "neutral": 0, "bearish": 0}
        for s in scores:
            c[s] += 1
        tot = sum(c.values()) or 1
        return {k: round(v / tot, 3) for k, v in c.items()}

    lex_labels = []
    for s in lex:
        pol = s.get("polarity", 0.0)
        ev = s.get("evidence", 0)
        if not ev or pol > 0.15:
            lex_labels.append("bullish" if pol > 0.15 else "neutral")
        elif pol < -0.15:
            lex_labels.append("bearish")
        else:
            lex_labels.append("neutral")
    # 词典 label 判定复用 score_text 内部口径不便直取，此处的阈值需与 score_text 一致：
    # 若 score_text 返回 label 字段则直接用
    lex_labels = []
    for s in lex:
        lb = s.get("label")
        if lb is None:
            pol = s.get("polarity", 0.0)
            lb = "bullish" if pol > 0.15 else ("bearish" if pol < -0.15 else "neutral")
        lex_labels.append(lb)

    mdl_labels = [LABELS[int(max(range(3), key=lambda k: p[k]))] for p in preds]
    mdl_conf = [max(p) for p in preds]

    agree = sum(1 for a, b in zip(lex_labels, mdl_labels) if a == b)
    out = {
        "generated_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "n": len(posts),
        "lexicon_dist": dist(lex_labels),
        "model_dist": dist(mdl_labels),
        "agreement": round(agree / len(posts), 3),
        "model_confidence_median": round(statistics.median(mdl_conf), 3),
        "disagree_examples": [],
    }
    # 分歧样例（模型高置信 + 词典不同）
    ex = [(t, a, b, round(max(p), 2)) for t, a, b, p in
          zip(posts, lex_labels, mdl_labels, preds)
          if a != b and max(p) >= 0.85]
    random.shuffle(ex)
    out["disagree_examples"] = [
        {"text": t[:120], "lexicon": a, "model": b, "conf": c}
        for t, a, b, c in ex[:12]]

    date = out["generated_at"][:10].replace("-", "")
    os.makedirs(os.path.join(HERE, "..", "reports"), exist_ok=True)
    with open(os.path.join(HERE, "data", "xq_shadow_%s.json" % date), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    L = []
    L.append("# 雪球源影子对照（finbert-xq-v1 vs 词典）— %s" % out["generated_at"])
    L.append("")
    L.append("样本：xq_timeline 实时帖随机抽样 %d 条（随机种子 20261110）。" % len(posts))
    L.append("纪律：影子运行，主管线未变；切换裁定归上级（马超）。")
    L.append("")
    L.append("| 指标 | 词典（现役） | finbert-xq-v1 |")
    L.append("|---|---|---|")
    for k, zh in (("bullish", "bullish"), ("neutral", "neutral"), ("bearish", "bearish")):
        L.append("| %s 占比 | %.1f%% | %.1f%% |" % (
            zh, out["lexicon_dist"][k] * 100, out["model_dist"][k] * 100))
    L.append("| 一致率 | colspan=2 %.1f%%（两通道逐帖一致） |" % (out["agreement"] * 100))
    L.append("")
    L.append("模型置信度中位 %.2f。" % out["model_confidence_median"])
    L.append("")
    L.append("## 高置信分歧样例（conf≥0.85，前 12）")
    L.append("")
    for e in out["disagree_examples"]:
        L.append("- 【词典=%s 模型=%s conf=%.2f】%s" % (
            e["lexicon"], e["model"], e["conf"], e["text"]))
    L.append("")
    L.append("判据提示：占比漂移大 + 高置信分歧多为词典 neutral→模型定向，即域适配收益的直接证据；")
    L.append("最终切换与否由上级裁定（建议路径：影子双轨并行 1-2 周对照六源卡稳定性）。")
    rep = os.path.join(HERE, "..", "reports", "xq_shadow_%s.md" % date)
    with open(rep, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("报告:", rep)
    print("占比 词典:", out["lexicon_dist"], " 模型:", out["model_dist"])
    print("一致率:", out["agreement"], " 置信中位:", out["model_confidence_median"])


if __name__ == "__main__":
    main()
