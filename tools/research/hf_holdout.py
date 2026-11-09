# -*- coding: utf-8 -*-
"""hf_holdout —— 入场端规则的一次性验收（第二十轮，第 3 步）。

## 预注册（写在这里，跑之前定死，跑完不许改）

    训练集    d0 ∈ 2024          （约 4,800 笔）
    验收集    d0 ∈ 2025          （约 2,000 笔）
    候选规则  训练集上按 hf_feat2 的判据（|Δt| ≥ 2 且双 β 同号且 ΔA > 0）
              每个特征最多取 1 条（|Δt| 最大），总共最多 5 条
    主规则    候选里训练集 ΔA 最大的那一条；其余只作附带观察，不进判决
    验收门槛  OOS ΔA_net ≥ +50bp 且 OOS 聚类 t ≥ 2 且 右尾份额跌幅 < 10pp
    只开一次  `opened_at` 落盘；再次运行必须 --i-know，且 JSON 里会留痕

## 为什么必须预注册

候选规则是从训练集几千种组合里挑出来的。如果允许在看到 2025 结果之后换规则，
那就不是验收，是拟合。所以门槛、候选数、主规则选法全部写在代码里，
跑完只报告事实，不回头调参。

## 三个 t 并排报，因为 250 日前瞻窗口几乎不独立

    逐笔 t     把 6,903 笔当独立样本 —— 第十九轮已证明会放大约 3.3 倍
    日聚类 t   按入场日先求均值再求 t —— 修正「同日几十只票共享一次冲击」
    重叠修正 t Newey-West，滞后取 min(250, N/2) —— 修正「相邻入场日共享 249/250 样本」

第三个数才是 250 日窗口的真相：398 个入场日只覆盖 398 个交易日，
每笔要看 250 天，独立窗口数 ≈ 1.6。在这种样本上，任何 ΔA 想达到 t=2
都需要极大的效应量，门槛本身可能自相矛盾——脚本会把「达标所需 ΔA」算出来，
让「不可达」和「没效果」分得开。

## 单位

ΔA 一律 bp；`a` / `a1` 落盘时已是百分点，乘 100 得 bp。
A_net = A − 122bp。因为成本按笔收、与选哪些票无关，所以 **ΔA_net ≡ ΔA**
（自检 H4 断言这一点）。所以「OOS ΔA_net ≥ 50bp」等价于「ΔA ≥ 50bp」。

用法::

    python -X utf8 tools/research/hf_holdout.py
    python -X utf8 tools/research/hf_holdout.py --i-know      # 二次开启（留痕）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hf_feat2 as F2        # noqa: E402  分桶与聚类 t 只有一份实现
import hf_path as HP         # noqa: E402

ROOT = HP.ROOT
PANEL = os.path.join(ROOT, "portal", "hf_feat2_panel.jsonl")
OUT = os.path.join(ROOT, "portal", "hf_holdout.json")

TRAIN_YEAR, TEST_YEAR = "2024", "2025"
MAX_RULES = 5
T_GATE = 2.0
DA_NET_GATE = 50.0           # bp
RIGHT_DROP_GATE = 10.0       # pp
COST_BP = 122.0
CAP = 250


def load_panel():
    return [json.loads(x) for x in open(PANEL, encoding="utf-8") if x.strip()]


# ---------------------------------------------------------------- 分桶（训练集定边界）
def train_buckets(rows, field, qs=F2.QS):
    """用**训练集**的分位定桶边界，返回 [(label, pred)]；pred 可用于任何行。"""
    vals = sorted(r[field] for r in rows if isinstance(r.get(field), (int, float)))
    if len(vals) < F2.MIN_N * 4:
        return []
    edges = []
    for q in qs:
        i = min(len(vals) - 1, int(round((len(vals) - 1) * q)))
        edges.append(vals[i])
    edges = sorted(set(edges))
    out = []
    nseg = len(edges) - 1
    for i in range(nseg):
        lo, hi = edges[i], edges[i + 1]
        if lo == hi:
            continue
        last = (i == nseg - 1)

        def pred(r, lo=lo, hi=hi, last=last, field=field):
            v = r.get(field)
            if not isinstance(v, (int, float)):
                return False
            return lo <= v <= hi if last else lo <= v < hi

        out.append(("%g~%g" % (lo, hi), pred))
    return out


def train_cat_buckets(rows, field):
    vals = sorted({str(r.get(field)) for r in rows if r.get(field) is not None})
    keep = [v for v in vals
            if sum(1 for r in rows if str(r.get(field)) == v) >= F2.MIN_N]
    return [(v, (lambda v=v, field=field: lambda r: str(r.get(field)) == v)())
            for v in keep]


# ---------------------------------------------------------------- 统计
def nw_t(diffs, lag=None):
    """Newey-West HAC t（Bartlett 核）。diffs 是「每日组间差」序列。

    日聚类的 t 只修了「同一天几十只票不是独立观测」，没修「相邻入场日共享
    249/250 的前瞻窗口」。用滞后 = min(250, N/2) 的 HAC 方差一次性处理两者。

    方差实现只留 `hf_feat2.hac_var` 一份——两处各写一遍就是两个口径。
    """
    n = len(diffs)
    if n < 10:
        return None
    v = F2.hac_var(diffs, lag)
    if not v or v <= 0:
        return None
    return statistics.mean(diffs) / math.sqrt(v / n)


def cluster_t(vals_a, days_a, vals_b, days_b):
    return F2.cluster_diff_t(vals_a, days_a, vals_b, days_b)


def eval_rule(test, pred, mode, key="a"):
    """在验收集上评估一条规则。mode=drop 丢掉桶内；mode=keep 只留桶内。"""
    hit = [r for r in test if pred(r) and r.get(key) is not None]
    rest = [r for r in test if not pred(r) and r.get(key) is not None]
    if not hit or not rest:
        return None
    if mode == "drop":
        kept, down = rest, hit
    else:
        kept, down = hit, rest
    kv = [r[key] for r in kept]
    dv = [r[key] for r in down]
    allv = [r[key] for r in test if r.get(key) is not None]
    base = statistics.mean(allv)

    # 每日组间差（kept − dropped）序列，供 NW 修正
    byk, byd = {}, {}
    for r in kept:
        byk.setdefault(r["d0"], []).append(r[key])
    for r in down:
        byd.setdefault(r["d0"], []).append(r[key])
    diffs = [statistics.mean(byk[d]) - statistics.mean(byd[d])
             for d in sorted(set(byk) & set(byd))]

    t, ndays = cluster_t(kv, [r["d0"] for r in kept], dv, [r["d0"] for r in down])
    tnw = nw_t(diffs) if diffs else None
    right_kept = sum(1 for r in kept if r["ret"] >= F2.G_UP) / len(kept)
    right_all = sum(1 for r in test if r["ret"] >= F2.G_UP) / len(test)
    left_kept = sum(1 for r in kept if r["ret"] < F2.G_DN) / len(kept)
    left_all = sum(1 for r in test if r["ret"] < F2.G_DN) / len(test)
    return {
        "key": key,
        "n_kept": len(kept), "n_dropped": len(down),
        "n_days": ndays,
        "A_bp": round(100 * statistics.mean(kv), 0),
        "A_base_bp": round(100 * base, 0),
        "dA_bp": round(100 * (statistics.mean(kv) - base), 0),
        "t": round(t, 2) if t is not None else None,
        "t_nw": round(tnw, 2) if tnw is not None else None,
        "n_diff_days": len(diffs),
        "right_pct": round(100 * right_kept, 1),
        "right_base_pct": round(100 * right_all, 1),
        "right_drop_pp": round(100 * (right_kept - right_all), 1),
        "left_pct": round(100 * left_kept, 1),
        "left_base_pct": round(100 * left_all, 1),
        "left_drop_pp": round(100 * (left_kept - left_all), 1),
        "win_pct": round(100 * sum(1 for r in kept if r["ret"] > 0) / len(kept), 1),
    }


def _pass_train(rows):
    """训练集单变量筛 → 通过的桶，每个特征只留 |Δt| 最大的一条。"""
    res = F2.screen(rows)
    best = {}
    for x in res["passed"]:
        f = x["feature"]
        if f not in best or abs(x["use_t"]) > abs(best[f]["use_t"]):
            best[f] = x
    return res, best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--i-know", action="store_true", help="二次开启（留痕）")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()

    if os.path.exists(OUT) and not a.i_know:
        print("已经开过一次：%s" % OUT)
        print("holdout 只许开一次。确需重跑请带 --i-know（会在 JSON 里留痕）。")
        return 2

    panel = load_panel()
    train = [r for r in panel if r["d0"][:4] == TRAIN_YEAR]
    test = [r for r in panel if r["d0"][:4] == TEST_YEAR]
    print("训练 %d 笔 / %d 日，验收 %d 笔 / %d 日"
          % (len(train), len({r["d0"] for r in train}),
             len(test), len({r["d0"] for r in test})))

    res_train, best = _pass_train(train)
    print("训练集通过 %d 条 / 检验 %d 次（纯噪声期望 %.1f）"
          % (len(res_train["passed"]), res_train["noise"]["n_tests"],
             res_train["noise"]["expect_pass"]))

    cands = sorted(best.values(), key=lambda x: -abs(x["use_dA"]))[:MAX_RULES]
    print("\n候选规则（训练集选出）：")
    for x in cands:
        print("  %-12s %-14s %s  ΔA=%+.0fbp Δt=%+.2f Δt1=%+.2f n=%d"
              % (x["feature"], x["bucket"], x["mode"], x["use_dA"],
                 x["use_t"], x["use_t1"], x["n"]))

    # 主规则 = 训练集 ΔA 最大
    primary = cands[0] if cands else None
    out = {
        "opened_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "reopened": bool(a.i_know),
        "protocol": {
            "train": TRAIN_YEAR, "test": TEST_YEAR,
            "max_rules": MAX_RULES, "t_gate": T_GATE,
            "dA_net_gate_bp": DA_NET_GATE, "right_drop_gate_pp": RIGHT_DROP_GATE,
            "cost_bp": COST_BP,
        },
        "n_train": len(train), "n_test": len(test),
        "n_days_train": len({r["d0"] for r in train}),
        "n_days_test": len({r["d0"] for r in test}),
        "train_noise": res_train["noise"],
        "candidates": [],
        "primary": None,
        "verdict": None,
        "power": {},
    }

    # 训练集指标（同一套 eval，只是数据集换成 train）
    for x in cands:
        bk = (train_buckets(train, x["feature"]) if x["feature"] in F2.FEAT_NUM
              + F2.FEAT_OLD_NUM else train_cat_buckets(train, x["feature"]))
        pred = None
        for lab, p in bk:
            if lab == x["bucket"]:
                pred = p
                break
        if pred is None:
            continue
        tr = {"feature": x["feature"], "bucket": x["bucket"], "mode": x["mode"],
              "use_dA_bp": x["use_dA"], "use_t": x["use_t"], "use_t1": x["use_t1"],
              "train": eval_rule(train, pred, x["mode"], "a"),
              "train1": eval_rule(train, pred, x["mode"], "a1")}
        # 验收集：**只跑一次**，三个口径一起出
        te = eval_rule(test, pred, x["mode"], "a")
        te1 = eval_rule(test, pred, x["mode"], "a1")
        te60 = eval_rule(test, pred, x["mode"], "a60")
        te160 = eval_rule(test, pred, x["mode"], "a160")
        tr["test"] = te
        tr["test1"] = te1
        tr["test60"] = te60
        tr["test160"] = te160
        tr["gate_pass"] = bool(
            te and te["dA_bp"] is not None and te["dA_bp"] >= DA_NET_GATE
            and te["t"] is not None and te["t"] >= T_GATE
            and te["right_drop_pp"] > -RIGHT_DROP_GATE)
        tr["is_primary"] = bool(primary and x["feature"] == primary["feature"]
                               and x["bucket"] == primary["bucket"])
        out["candidates"].append(tr)
        print("  → %-12s %s | OOS ΔA=%+.0fbp t=%+.2f t_nw=%+.2f 右尾%+.1fpp 左尾%+.1fpp %s"
              % (x["feature"], x["mode"],
                 te["dA_bp"] if te else 0, te["t"] if te and te["t"] else 0,
                 te["t_nw"] if te and te["t_nw"] else 0,
                 te["right_drop_pp"] if te else 0,
                 te["left_drop_pp"] if te else 0,
                 "✔ 过线" if tr["gate_pass"] else ""))

    # ---------------- 功效诊断：这个门槛在 2025 上能不能达到
    # 反解方式：t = ΔA / (sd/√N) ⇒ 要达到 t=2，ΔA 得是现在的 2/|t| 倍。
    # 这不是"效果不好"的借口，而是把「没效果」和「看不出效果」分开。
    if out["candidates"]:
        te = out["candidates"][0]["test"] or {}
        dA, t_, tnw = te.get("dA_bp"), te.get("t"), te.get("t_nw")
        out["power"] = {
            "n_days_test": out["n_days_test"],
            "indep_windows_250d": round(out["n_days_test"] / float(CAP), 2),
            "indep_windows_60d": round(out["n_days_test"] / 60.0, 2),
            "dA_observed_bp": dA,
            "dA_needed_for_t2_bp": (round(abs(dA * T_GATE / t_), 0)
                                    if dA and t_ else None),
            "dA_needed_for_nw_t2_bp": (round(abs(dA * T_GATE / tnw), 0)
                                       if dA and tnw else None),
            "note": ("250 日前瞻窗口下，验收集只覆盖 %d 个交易日，独立窗口约 %.1f 个。"
                     "在这种样本上要让聚类 t 到 2，ΔA 需要达到 %s bp——"
                     "先把门槛和样本量对齐，再谈成败"
                     % (out["n_days_test"], out["n_days_test"] / float(CAP),
                        (round(abs(dA * T_GATE / t_), 0) if dA and t_ else "—"))),
        }

    if primary is not None:
        p0 = [c for c in out["candidates"] if c["is_primary"]]
        p0 = p0[0] if p0 else out["candidates"][0]
        te = p0["test"] or {}
        out["primary"] = {
            "feature": p0["feature"], "bucket": p0["bucket"], "mode": p0["mode"],
            "train_dA_bp": p0["use_dA_bp"], "train_t": p0["use_t"],
            "oos_dA_bp": te.get("dA_bp"), "oos_t": te.get("t"),
            "oos_t_nw": te.get("t_nw"),
            "oos_right_drop_pp": te.get("right_drop_pp"),
            "oos_left_drop_pp": te.get("left_drop_pp"),
            "gate_pass": p0["gate_pass"],
        }
        ok = p0["gate_pass"]
        out["verdict"] = ("PASS" if ok else "FAIL")
        print("\n判决：%s（主规则 %s / %s / %s）"
              % (out["verdict"], p0["feature"], p0["bucket"], p0["mode"]))
    else:
        out["verdict"] = "NO_RULE"
        print("\n判决：训练集没有通过判据的规则 —— 无需开验收集。")

    if not a.no_write:
        tmp = OUT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        os.replace(tmp, OUT)
        print("→ %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
