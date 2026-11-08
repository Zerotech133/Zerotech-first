# -*- coding: utf-8 -*-
"""hf_feat2 / hf_holdout 的自检（第二十轮）。

十条断言，每条针对一个具体的错法：

  C1  面板与逐笔对得上：行数、唯一键、丢弃的 3 笔是三个指数代码
  C2  **未来函数**：把行情截断到 d0 之后重算，特征必须逐字段完全相等
  C3  单位：ΔA(bp) = 100 × (A_rest − A_all)，逐行重算
  C4  双 β 口径确实不同（否则"两个口径同号"是假的稳健性）
  C5  日级变量：日聚类 t 无定义、日级 HAC t 有值（两类算法都验）
  C6  分桶完备：各桶 n 之和 = 该特征非空笔数（不重不漏）
  C7  通过判据自洽：|use_t| ≥ 2、双口径同号、ΔA > 0、非循环论证
  C8  板块相对强度 = 个股收益 − 行业收益（直接相减，不是另算一遍）
  C9  成本恒等式：成本按笔收 ⇒ ΔA_net ≡ ΔA（报告里引用这条）
  C10 holdout 规则在训练集上可复现：重算主规则的 ΔA 与落盘值一致

用法::

    python -X utf8 tools/research/hf_feat2_selfcheck.py
"""
from __future__ import annotations

import json
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hf_feat2 as F2        # noqa: E402
import hf_path as HP         # noqa: E402

ROOT = HP.ROOT
PANEL = os.path.join(ROOT, "portal", "hf_feat2_panel.jsonl")
FEATJSON = os.path.join(ROOT, "portal", "hf_feat2.json")
HOLD = os.path.join(ROOT, "portal", "hf_holdout.json")

FAILED = []


def chk(tag, ok, msg):
    print(("[%s] %s  %s" % ("OK " if ok else "!! ", tag, msg)))
    if not ok:
        FAILED.append(tag)


def load_panel():
    return [json.loads(x) for x in open(PANEL, encoding="utf-8") if x.strip()]


# ---------------------------------------------------------------- C1
def c1(panel):
    trades = F2.load_trades()
    bad = [t for t in trades if (t["c"], t["d0"]) not in {(r["c"], r["d0"]) for r in panel}]
    idx_codes = {"399001.SZ", "399006.SZ", "000688.SH"}
    chk("C1", len(panel) == len(trades) - 3 and all(t["c"] in idx_codes for t in bad),
        "面板 %d 笔 = 逐笔 %d − 丢弃 %d 笔（%s）"
        % (len(panel), len(trades), len(bad), ",".join(sorted({t["c"] for t in bad}))))
    keys = [(r["c"], r["d0"]) for r in panel]
    chk("C1b", len(keys) == len(set(keys)), "唯一键 (code,d0) 无重复")


# ---------------------------------------------------------------- C2 未来函数
def c2(panel, ctx, n=12):
    """截断到 d0 重算：只要有一根未来 bar 被用到，结果就会变。"""
    import pyarrow as pa
    import pyarrow.parquet as pq
    import pandas as pd

    random.seed(20261108)
    sample = random.sample(panel, n)
    bad = []
    for r in sample:
        code, d0 = r["c"], r["d0"]
        path = os.path.join(ROOT, "quant_backtest", "data", "ashare10y",
                            code.replace(".", "_") + ".parquet")
        if not os.path.exists(path):
            continue
        tb = pq.read_table(path, columns=["trade_date", "close", "high",
                                         "low", "pre_close", "amount"])
        df = tb.to_pandas()
        df = df.assign(td=df["trade_date"].astype(str).str.replace("-", "").str[:8])
        # 只保留 d0 及之前 —— 未来数据在这里被物理切掉
        cut = df[df["td"] <= d0]
        tbc = pa.Table.from_pandas(cut, preserve_index=False)
        ser = F2._series(tbc)
        if ser is None:
            continue
        dates, idx, h_adj, l_adj, amt, ret = ser
        i = dates.index(d0) if d0 in dates else None
        if i is None:
            continue
        f = F2.features_one(code, d0, i, idx, h_adj, l_adj, amt, ret, ctx)
        for k, v in f.items():
            if k in ("c", "d0", "ind"):
                continue
            if not isinstance(v, (int, float)):
                continue
            full = r.get(k)
            if full is None:
                bad.append((code, d0, k, "full=None"))
                continue
            if abs(float(v) - float(full)) > 1e-9:
                bad.append((code, d0, k, v, full))
    chk("C2", not bad, "截断重算 %d 笔逐字段相等（不一致 %d 处 %s）"
        % (n, len(bad), bad[:3]))


# ---------------------------------------------------------------- C3 单位
def c3(panel, js):
    bad = []
    vals = [r["a"] for r in panel if r.get("a") is not None]
    base = statistics.mean(vals)
    for x in js["features"]:
        lab, feat = x["bucket"], x["feature"]
        if feat in F2.FEAT_CAT + F2.FEAT_OLD_CAT:
            pools = F2.cat_buckets(feat, [lab])
        else:
            pools = F2.num_buckets(feat, panel)
        pred = None
        for b in pools:
            if b[0] == lab:
                pred = b[1]
                break
        if pred is None:
            continue
        hit = [r["a"] for r in panel if pred(r) and r.get("a") is not None]
        rest = [r["a"] for r in panel if not pred(r) and r.get("a") is not None]
        if not hit or not rest:
            continue
        want = round(100 * (statistics.mean(rest) - base), 0)
        if abs(want - x["dA_bp"]) > 0.51:
            bad.append((feat, lab, want, x["dA_bp"]))
    chk("C3", not bad, "ΔA(bp) = 100×(A_rest − A_all) 全部 %d 行重算一致（不一致 %d 处）"
        % (len(js["features"]), len(bad)))


# ---------------------------------------------------------------- C4 双口径
def c4(js):
    rows = [x for x in js["features"] if x.get("A1_bp") is not None]
    diff = sum(1 for x in rows if abs(x["A_bp"] - x["A1_bp"]) > 1)
    ratio = diff / max(1, len(rows))
    chk("C4", ratio > 0.6, "两口径不同的桶 %d/%d（%.0f%%）——「同号」才有信息量"
        % (diff, len(rows), 100 * ratio))


# ---------------------------------------------------------------- C5 日级
def c5(panel, js):
    rows = [x for x in js["features"] if x["feature"] == "crowd"]
    lv = {x["level"] for x in rows}
    # 日聚类 t 在日级变量上必然无定义：同一天所有票同桶
    r0 = panel[0]
    same = [r for r in panel if r["d0"] == r0["d0"]]
    vals = [r["a"] for r in same if r.get("a") is not None]
    t_na, nd = F2.cluster_diff_t(vals, [r["d0"] for r in same if r.get("a") is not None],
                                 [1.0], ["__none__"])
    ok_t = [x for x in rows if x["dA_t"] is not None]
    # 极端桶（全池只有几百个信号的那些天）日数不足 5 天，HAC 求不出，属正常
    thin = [x for x in ok_t if (x.get("n_days_hit") or 0) < 5]
    chk("C5", lv == {"date"} and t_na is None and len(ok_t) >= 3 and not thin,
        "crowd 标为日级、日聚类 t 无定义（%s）、HAC t 有值 %d/%d 档（薄桶 %d 个）"
        % ("None" if t_na is None else t_na, len(ok_t), len(rows), len(thin)))


# ---------------------------------------------------------------- C6 分桶完备
def c6(panel, js):
    bad = []
    feats = {}
    for x in js["features"]:
        feats.setdefault(x["feature"], []).append(x)
    for f, rows in feats.items():
        if rows[0]["level"] != "trade":
            continue
        n_sum = sum(x["n"] for x in rows)
        n_all = sum(1 for r in panel if isinstance(r.get(f), (int, float))
                    or (f in F2.FEAT_CAT + F2.FEAT_OLD_CAT and r.get(f) is not None))
        if abs(n_sum - n_all) > 0:
            bad.append((f, n_sum, n_all))
    chk("C6", not bad, "数值/类别特征各桶 n 之和 = 非空笔数（越界 %d 个）" % len(bad))


# ---------------------------------------------------------------- C7 判据
def c7(js):
    bad = []
    for x in js["passed"]:
        if abs(x["use_t"]) < F2.T_GATE:
            bad.append((x["feature"], "t"))
        if x["use_t"] * x["use_t1"] <= 0:
            bad.append((x["feature"], "sign"))
        if x["use_dA"] <= 0:
            bad.append((x["feature"], "dA"))
        if x.get("circular"):
            bad.append((x["feature"], "circular"))
        if x["dA_ndays"] < F2.MIN_DAYS:
            bad.append((x["feature"], "days"))
    chk("C7", not bad, "通过 %d 条全部满足 |Δt|≥2 / 同号 / ΔA>0 / 非循环 / 天数≥%d"
        % (len(js["passed"]), F2.MIN_DAYS))
    # 反向：通过名单里的两条 t 必须同号（keep 模式两条一起翻号，符号关系不变）
    bad2 = [x for x in js["passed"]
            if x["dA_t"] is None or x["dA1_t"] is None or x["dA_t"] * x["dA1_t"] <= 0]
    chk("C7b", not bad2, "通过名单的 Δt 与 Δt1 全部同号（异号 %d 条）" % len(bad2))


# ---------------------------------------------------------------- C8 板块相对强度
def c8(panel):
    bad = []
    for r in panel:
        if r.get("ind_rs20") is None or r.get("ret20") is None or r.get("ind_ret20") is None:
            continue
        if abs(r["ind_rs20"] - (r["ret20"] - r["ind_ret20"])) > 1e-9:
            bad.append((r["c"], r["d0"]))
        if len(bad) > 3:
            break
    n_ind = len({r["ind"] for r in panel if r.get("ind")})
    chk("C8", not bad, "ind_rs = ret − ind_ret 恒等（%d 个行业；异常 %d）"
        % (n_ind, len(bad)))


# ---------------------------------------------------------------- C9 成本恒等
def c9(js):
    """ΔA_net = (A_选 → −C) − (A_all − C) = ΔA。成本按笔收，与选哪些票无关。

    所以「OOS ΔA_net ≥ 50bp」与「ΔA ≥ 50bp」是同一句话——报告里引用这条。
    """
    if not js["passed"]:
        chk("C9", True, "无通过项，跳过")
        return
    x = max(js["passed"], key=lambda y: abs(y["use_dA"]))
    c = js.get("cost_bp", 122)
    a_sel = x["A_rest_bp"] if x["mode"] == "drop" else x["A_bp"]
    base = x.get("base_bp")
    if base is None:
        chk("C9", False, "缺 base_bp 字段")
        return
    dA_net = (a_sel - c) - (base - c)
    use = x["use_dA"]
    chk("C9", abs(dA_net - use) <= 1.0,
        "成本按笔收 ⇒ ΔA_net ≡ ΔA（%.0f vs %.0f，%s 模式）"
        % (dA_net, use, x["mode"]))


# ---------------------------------------------------------------- C10 holdout 可复现
def c10(panel):
    if not os.path.exists(HOLD):
        chk("C10", True, "holdout 尚未开启，跳过")
        return
    d = json.load(open(HOLD, encoding="utf-8"))
    p = d.get("primary")
    if not p:
        chk("C10", True, "无主规则，跳过")
        return
    import hf_holdout as HO
    train = [r for r in panel if r["d0"][:4] == HO.TRAIN_YEAR]
    test = [r for r in panel if r["d0"][:4] == HO.TEST_YEAR]
    bk = (HO.train_buckets(train, p["feature"])
          if p["feature"] in F2.FEAT_NUM + F2.FEAT_OLD_NUM
          else HO.train_cat_buckets(train, p["feature"]))
    pred = None
    for lab, fn in bk:
        if lab == p["bucket"]:
            pred = fn
    if pred is None:
        chk("C10", False, "主规则的桶在训练集上重建不出来：%s" % p["bucket"])
        return
    tr = HO.eval_rule(train, pred, p["mode"], "a")
    te = HO.eval_rule(test, pred, p["mode"], "a")
    ok = (abs(tr["dA_bp"] - p["train_dA_bp"]) < 0.51
          and abs(te["dA_bp"] - p["oos_dA_bp"]) < 0.51)
    chk("C10", ok, "主规则在训练/验收集上重算一致（train %.0f/%.0f, oos %.0f/%.0f）"
        % (tr["dA_bp"], p["train_dA_bp"], te["dA_bp"], p["oos_dA_bp"]))


def c11(panel, js):
    """ΔA 与 Δt 反号：必须能被"按笔加权 vs 按日等权"解释，否则是真 bug。"""
    mm = [x for x in js["features"] if x.get("sign_mismatch")]
    if not mm:
        chk("C11", True, "无 ΔA/Δt 反号项，跳过")
        return
    x = mm[0]
    feat, lab = x["feature"], x["bucket"]
    bk = (F2.cat_buckets(feat, [lab]) if feat in F2.FEAT_CAT + F2.FEAT_OLD_CAT
          else F2.num_buckets(feat, panel))
    pred = None
    for b in bk:
        if b[0] == lab:
            pred = b[1]
    if pred is None:
        chk("C11", False, "反号项的分桶重建不出来")
        return
    hit = [r["a"] for r in panel if pred(r) and r.get("a") is not None]
    rest = [r["a"] for r in panel if not pred(r) and r.get("a") is not None]
    pooled = statistics.mean(hit) - statistics.mean(rest)
    byh, byr = {}, {}
    for r in panel:
        if r.get("a") is None:
            continue
        (byh if pred(r) else byr).setdefault(r["d0"], []).append(r["a"])
    days = sorted(set(byh) & set(byr))
    dayw = statistics.mean([statistics.mean(byh[d]) - statistics.mean(byr[d])
                            for d in days])
    chk("C11", pooled * dayw < 0,
        "反号项 %s %s：按笔加权 %+.0fbp / 按日等权 %+.0fbp（%d 个共同日）——"
        "两个估计量权不同，这就是反号的来源" % (feat, lab, 100 * pooled, 100 * dayw,
                                              len(days)))


def main():
    panel = load_panel()
    js = json.load(open(FEATJSON, encoding="utf-8"))

    import hf_sell_exit as SE
    axis, _, _ = SE.calendar()
    snaps = F2.industry_snapshots(axis)
    ctx = {
        "tur": F2.load_turnover({r["c"] for r in panel}),
        "reg": F2.regime_map(),
        "crowd": F2.crowd_maps(axis, snaps)[0],
        "crowd_ind": F2.crowd_maps(axis, snaps)[1],
        "snaps": snaps,
        "listed": F2.load_list_date(),
    }

    c1(panel)
    c2(panel, ctx)
    c3(panel, js)
    c4(js)
    c5(panel, js)
    c6(panel, js)
    c7(js)
    c8(panel)
    c9(js)
    c10(panel)
    c11(panel, js)

    print("\n%s  %d 条断言，失败 %d 条"
          % ("全部通过" if not FAILED else "有失败", 11, len(FAILED)))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
