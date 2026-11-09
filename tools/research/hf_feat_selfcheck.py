# -*- coding: utf-8 -*-
"""hf_feat 自检。

F1 逐笔数：hf_trades_path.jsonl 的笔数 = hf_path.json 的 n_trades
F2 join 率：逐笔全部能对上信号特征（对不上要计数，不能静默丢）
F3 单位：整体 A(β 调整) 必须 ≈ hf_path 的 A(250)（196bp）。**这条是防 ×1e4 的**
   —— 落盘的 ret/a 已经是百分点，再乘 1e4 会大 100 倍，表看着有数、结论全错
F4 口径对照：β=1 的 A 必须与 β 调整的 A 不同（否则 br1 没接上），且两者都在合理区间
F5 分桶覆盖：类别型特征的各桶 n 之和 = 该特征非空样本数（不能重叠也不能漏）

用法::

    python -X utf8 tools/research/hf_path.py --dump hf_trades_path.jsonl
    python -X utf8 tools/research/hf_feat.py
    python -X utf8 tools/research/hf_feat_selfcheck.py
"""
from __future__ import annotations

import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hf_feat as F          # noqa: E402

ROOT = F.ROOT
FAIL, OK = [], []


def chk(name, cond, msg=""):
    (OK if cond else FAIL).append(name)
    print("%s %-4s %s" % ("✅" if cond else "❌", name, msg))


def _load(fn):
    p = os.path.join(ROOT, "portal", fn)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def main():
    path = _load("hf_path.json")
    feat = _load("hf_feat.json")
    trades = [json.loads(l) for l in
              open(os.path.join(ROOT, "portal", "hf_trades_path.jsonl"), encoding="utf-8")]
    if not (path and feat and trades):
        print("缺产物，先跑 hf_path.py --dump 与 hf_feat.py")
        return 1

    # ── F1 逐笔数 ──
    chk("F1", len(trades) == path["n_trades"],
        "逐笔 %d = hf_path n_trades %d" % (len(trades), path["n_trades"]))

    # ── F2 join 率 ──
    chk("F2", feat["n_join_miss"] == 0,
        "join 不上 %d 笔（%d 笔全部对上）" % (feat["n_join_miss"], feat["n"]))

    # ── F3 单位 ──
    a = [r["a"] for r in trades if r["a"] is not None]
    a_bp = 100 * statistics.mean(a)
    ref = path["curve"]["250"]["A_bp"]
    chk("F3", abs(a_bp - ref) < 5,
        "整体 A = %.0fbp，hf_path A(250) = %.0fbp（差 %.0fbp）"
        % (a_bp, ref, a_bp - ref))

    # ── F4 两种口径必须不同 ──
    a1 = [r["a1"] for r in trades if r.get("a1") is not None]
    a1_bp = 100 * statistics.mean(a1)
    chk("F4", len(a1) > 0.9 * len(trades) and abs(a1_bp - a_bp) > 10,
        "β=1 口径 %.0fbp ≠ β 调整 %.0fbp（差 %.0fbp），覆盖 %d/%d"
        % (a1_bp, a_bp, a1_bp - a_bp, len(a1), len(trades)))

    # ── F5 分桶覆盖（类别型）──
    bad = []
    for f in ("type", "mbei", "vv", "tbe"):
        nn = sum(1 for r in trades if r.get(f) is not None)
        got = sum(x["n"] for x in feat["features"] if x["feature"] == f)
        if nn and got != nn:
            bad.append((f, got, nn))
    chk("F5", not bad, "类别型各桶合计 = 非空样本数；不一致 %d 项 %s"
        % (len(bad), bad[:3]))

    print()
    print("通过 %d / 失败 %d" % (len(OK), len(FAIL)))
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
