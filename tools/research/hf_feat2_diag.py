# -*- coding: utf-8 -*-
"""hf_feat2_diag —— holdout 的事后诊断（不改判，只定位）。

holdout 已经开过并且 FAIL。判决不改，但「为什么失败」有三种完全不同的解释，
必须靠数据分开：

    A. 训练集里本来就没有结构 —— 那 26 条通过是多重检验的噪声
    B. 有结构，但不迁移 —— 训练集 ΔA 与验收集 ΔA 无关
    C. 有结构、也迁移，只是幅度太小 —— 两个 ΔA 正相关，只是够不到 50bp

分辨办法：**不只用通过的那 5 条，而是把全部桶都拿来算一次相关性**。
只挑「训练集显著」的那些桶去看，等于把选择偏差又做了一遍。

## 三个诊断

1. 迁移相关：所有 (feature, bucket) 的 train ΔA vs test ΔA 的 Spearman 秩相关。
   显著为正 → 结构部分迁移（C）；≈0 → 结构不迁移（B）。
2. 置换零分布：随机留同样比例的票，OOS ΔA 的分布。用来给 +25bp 定位——
   它在随机分布的哪个分位。这不是 p 值裁决，是标尺。
3. 分年稳定性：训练集通过的那 5 条，在 2024 / 2025 两列的 ΔA 与 t 并排。

用法::

    python -X utf8 tools/research/hf_feat2_diag.py
"""
from __future__ import annotations

import json
import math
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hf_feat2 as F2          # noqa: E402
import hf_holdout as HO        # noqa: E402
import hf_path as HP           # noqa: E402

ROOT = HP.ROOT
PANEL = os.path.join(ROOT, "portal", "hf_feat2_panel.jsonl")
HOLD = os.path.join(ROOT, "portal", "hf_holdout.json")
OUT = os.path.join(ROOT, "portal", "hf_feat2_diag.json")

N_PERM = 2000


def spearman(xs, ys):
    """秩相关（不做 tie 修正的简化版，但对连续量足够）。"""
    n = len(xs)
    if n < 5:
        return None
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for k, i in enumerate(order):
            r[i] = k
        return r
    rx, ry = rank(xs), rank(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return num / (dx * dy) if dx and dy else None


def all_bucket_pairs(train, test):
    """把每个特征的每个桶都在训练/验收集上算一遍，返回对照表。

    边界用**训练集**分位定（与 holdout 一致），验收集照用——这才是真实部署：
    阈值在看数据之前就定好了。
    """
    pairs = []
    feats = list(F2.FEAT_NUM) + list(F2.FEAT_OLD_NUM)
    for f in feats:
        for lab, pred in HO.train_buckets(train, f):
            tr = HO.eval_rule(train, pred, "drop", "a")
            te = HO.eval_rule(test, pred, "drop", "a")
            if not tr or not te:
                continue
            pairs.append({
                "feature": f, "bucket": lab,
                "train_dA_bp": tr["dA_bp"], "train_t": tr["t"],
                "test_dA_bp": te["dA_bp"], "test_t": te["t"],
                "n_train": tr["n_kept"], "n_test": te["n_kept"],
            })
    cats = list(F2.FEAT_CAT) + list(F2.FEAT_OLD_CAT)
    for f in cats:
        for lab, pred in HO.train_cat_buckets(train, f):
            tr = HO.eval_rule(train, pred, "drop", "a")
            te = HO.eval_rule(test, pred, "drop", "a")
            if not tr or not te:
                continue
            pairs.append({
                "feature": f, "bucket": lab,
                "train_dA_bp": tr["dA_bp"], "train_t": tr["t"],
                "test_dA_bp": te["dA_bp"], "test_t": te["t"],
                "n_train": tr["n_kept"], "n_test": te["n_kept"],
            })
    return pairs


def perm_null(test, share, obs, n=N_PERM, seed=20261108):
    """随机留 share 比例的票，看 OOS ΔA 的零分布。"""
    rnd = random.Random(seed)
    vals = [r["a"] for r in test if r.get("a") is not None]
    k = max(30, int(round(share * len(vals))))
    out = []
    for _ in range(n):
        smp = rnd.sample(vals, k)
        out.append(100 * (statistics.mean(smp) - statistics.mean(vals)))
    out.sort()
    p = sum(1 for v in out if v >= obs) / len(out)
    return {
        "n_draws": n, "keep_share": round(share, 3), "k": k,
        "sd_bp": round(statistics.pstdev(out), 1),
        "q05": round(out[int(0.05 * n)], 1),
        "q50": round(out[n // 2], 1),
        "q95": round(out[int(0.95 * n)], 1),
        "obs_bp": obs,
        "p_one_sided": round(p, 4),
        "note": ("随机留同样比例的票，OOS ΔA 的零分布。它只说明「这个幅度在随机"
                 "抽样里占什么位置」，不构成 p 值裁决"),
    }


def main():
    panel = [json.loads(x) for x in open(PANEL, encoding="utf-8") if x.strip()]
    hold = json.load(open(HOLD, encoding="utf-8"))
    train = [r for r in panel if r["d0"][:4] == HO.TRAIN_YEAR]
    test = [r for r in panel if r["d0"][:4] == HO.TEST_YEAR]

    pairs = all_bucket_pairs(train, test)
    rho = spearman([p["train_dA_bp"] for p in pairs],
                   [p["test_dA_bp"] for p in pairs])
    strong = [p for p in pairs if (p["train_t"] or 0) >= F2.T_GATE]
    rho_strong = spearman([p["train_dA_bp"] for p in strong],
                          [p["test_dA_bp"] for p in strong]) if len(strong) >= 5 else None
    # 符号一致率：训练集剔除后 ΔA 为正的那些桶，验收集是否也为正
    pos = [p for p in pairs if p["train_dA_bp"] > 0]
    sign_ok = sum(1 for p in pos if p["test_dA_bp"] > 0) / max(1, len(pos))

    prim = hold.get("primary") or {}
    bk = (HO.train_buckets(train, prim.get("feature"))
          if prim.get("feature") in F2.FEAT_NUM + F2.FEAT_OLD_NUM
          else HO.train_cat_buckets(train, prim.get("feature")))
    pred = None
    for lab, fn in bk:
        if lab == prim.get("bucket"):
            pred = fn
    share = 1.0
    if pred is not None and prim.get("mode") == "keep":
        share = sum(1 for r in test if pred(r)) / len(test)
    elif pred is not None:
        share = sum(1 for r in test if not pred(r)) / len(test)

    res = {
        "opened_at": hold.get("opened_at"),
        "verdict": hold.get("verdict"),
        "n_pairs": len(pairs),
        "transfer": {
            "spearman_all": round(rho, 3) if rho is not None else None,
            "n_all": len(pairs),
            "spearman_strong": round(rho_strong, 3) if rho_strong is not None else None,
            "n_strong": len(strong),
            "sign_rate": round(100 * sign_ok, 1),
            "n_positive_train": len(pos),
            "note": ("全部桶的 train ΔA 与 test ΔA 秩相关。≈0 说明训练集的结构"
                     "在原时间外不成立；显著 >0 才说明「有结构但幅度不够」"),
        },
        "null": perm_null(test, share, prim.get("oos_dA_bp") or 0.0),
        "pairs": pairs,
        "top_in_sample": sorted(pairs, key=lambda p: -abs(p["train_t"] or 0))[:10],
    }
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)

    print("全部桶 %d 组；train ΔA 与 test ΔA 的秩相关 ρ = %s（强信号桶 %d 组，ρ = %s）"
          % (len(pairs), res["transfer"]["spearman_all"], len(strong),
             res["transfer"]["spearman_strong"]))
    print("训练集 ΔA>0 的 %d 组里，验收集仍 >0 的占 %.0f%%"
          % (len(pos), res["transfer"]["sign_rate"]))
    print("置换零分布：保留 %.0f%%，OOS ΔA 的 90%% 区间 [%.0f, %.0f] bp，"
          "sd=%.0f；观测 %.0f bp 落在 p=%.3f"
          % (100 * share, res["null"]["q05"], res["null"]["q95"],
             res["null"]["sd_bp"], res["null"]["obs_bp"],
             res["null"]["p_one_sided"]))
    print("\n训练集 |t| 最大的 10 组，和它们在验收集上的样子：")
    print("%-12s %-14s %8s %6s | %8s %6s" % ("特征", "桶", "trainΔA", "t", "testΔA", "t"))
    for p in res["top_in_sample"]:
        print("%-12s %-14s %+8.0f %6s | %+8.0f %6s"
              % (p["feature"], p["bucket"], p["train_dA_bp"], p["train_t"],
                 p["test_dA_bp"], p["test_t"]))
    print("→ %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
