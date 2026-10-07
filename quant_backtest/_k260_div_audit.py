# -*- coding: utf-8 -*-
"""K260 门闸改口径取证：从 bias 分歧抽 30 条生成人工标注表。

用法（eval 完成后）：
  python _k260_div_audit.py [--n 30] [--seed 11]
输出 data/semantica/k260_audit_30.csv —— 每行一条分歧：
  文本 / 规则tag / LLMtag / conf / 判定（人工填：LLM对|规则对|都对|都错）

口径说明：回填只碰 bias=None 未命中行，规则误报与回填无关；
分歧人工抽检正确率 = 直接度量 LLM 对未命中行的判定质量。
"""
import argparse
import csv
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL_P = os.path.join(HERE, "data", "semantica", "notice_llm_eval.parquet")
OUT_CSV = os.path.join(HERE, "data", "semantica", "k260_audit.csv")


def main():
    import pandas as pd
    from kg_notice_llm import TAG_BIAS
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()

    ev = pd.read_parquet(EVAL_P)
    ev["bias_rule"] = ev["tag_rule"].map(TAG_BIAS)
    div = ev[ev["bias"] != ev["bias_rule"]].copy()
    print("eval 样本 %d，bias 分歧 %d 条" % (len(ev), len(div)))
    if len(div) < args.n:
        print("分歧不足 %d，全量 %d 条" % (args.n, len(div)))
    rnd = random.Random(args.seed)
    idx = rnd.sample(range(len(div)), min(args.n, len(div)))
    sub = div.iloc[idx]

    rows = []
    for _, r in sub.iterrows():
        rows.append({"text": r["text"][:110], "tag_rule": r["tag_rule"],
                     "tag_llm": r["tag"], "conf": r["conf"],
                     "bias_rule": r["bias_rule"], "bias_llm": r["bias"],
                     "判定": ""})
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print("标注表 %d 条 → %s（utf-8-sig，Excel 可开）" % (len(rows), OUT_CSV))
    print("判读提示：判定列填 LLM对/规则对/都对/都错（看 LLM 判定对回填口径是否有害）")


if __name__ == "__main__":
    main()
