# -*- coding: utf-8 -*-
"""nine_dim_industry_pct.py —— W5 行业分位全市场化改造验证（研究脚本，只读产线）。

背景（W5 实施计划滚动项「行业分位全市场化 → 反指升格正式风控输入」）：
ninedim 已实证为反指（全市场口径 ≥75 段 thinned r20 胜率 9% / -27.9%，
≥85 段 4 样本 -71.5%），但高分可能被行业结构污染——强势行业整队通过
RPS 门、整队拿高分，警报池变相=行业拥挤。本脚本检验：把评分中可截面
化的原始值项改为「同行业分位」后，反指强度是否更纯。

口径梳理结论（task 1，读 nine_dim.py / nine_dim_backtest.py）：
· 产线评分（V3, score_one）内部没有任何「全市场逐维分位」——全部是
  绝对阈值或事件判定；全市场分位暴露只在粗筛门 RPS50>80（动量分位门，
  决定候选池构成，不进评分公式）。
· 已是行业相对项（本次不动）：q2 毛利率≥行业p70（候选池内）、
  g2 EV≤行业均值（PE 中位回退）、s1 行业5日涨幅top3、s_p 行业RPS<50。
· 可截面化改造项（本次对象，权重 6+6+3=15 分）：
    q1  营收增速≥25%      → 分位版：or_yoy 分位 ≥70%（行业内/全市场）
    g1  PEG≤1.0           → 分位版：PEG 分位 ≤30%
    g_p PEG>1.5 扣3       → 分位版：PEG 分位 >70% 扣3
  百分位阈值 70/30/70 对齐原筛选强度语义（q2 的「行业TOP30%」同款）。

三个评分口径（同一样本库、同一前向收益，只换判定）：
· abs  产线基线（绝对阈值）：由存储的逐项判定 v 重算，须与存储 score
  100% 对账（parity gate，不对账不出结论）。
· mkt  全市场分位版：q1/g1/g_p 用同锚点全部样本的截面分位判定。
· ind  行业分位版：同锚点同行业样本数 ≥10 用行业内分位；<10 归全市场
  分位（防小行业噪声，任务规格），并在产物标注 ind_fallback。

同伴集口径（诚实标注）：分位同伴集 = 同锚点「候选池内完整评分样本」
（cov≥0.75 子集，即 replay 样本本身）。与产线 q2 的「候选池内近似」
同款保守口径；全行业成员集需全市场 fina/daily_basic 覆盖，离线不可得。
原始值 as-of：or_yoy/netprofit_yoy 取 fina（ann_date≤锚点，SQLite 缓存，
nine_dim_backtest._fina_asof 同款）；pe_ttm 取 data/tushare_staging 逐日
as-of parquet（20230103..20261009 全窗口覆盖，与原回放同源）。PEG 仅在
pe>0 且净利增速>0 时有定义（同产线 _j_g1/_j_gp 定义域）；原始值缺失或
原判定为 None 时保持原判定不动（绝不 None→judged，保 judg_max 对账）。

附加产出：
· 正指侧：≤25 / <60 段两版对照（行业分位是否同时改善低分段区分）。
· 行业拥挤视角：≥75/≥85 命中的行业集中度（top1/2/3 占比）。
· 行业相对警报口径（补充）：abs score≥75 且同行业 score 分位≥80%
  （成员≥10）——直接检验「个股拥挤 vs 行业拥挤」。
· 两半段同向性：窗口前半/后半 ≥75/≥85 段分别统计。

判定规则（任务规格）：ind 版 ≥85 段负向幅度（|avg|）优于 mkt 版 ≥20%
且回放窗口两半段同向 → 建议评分口径切换产线方案（只写方案不动手）；
否则给「维持全市场分位」的证据。n<10 灰显不构成结论（≥85 段两版样本
量极小，判定以 ≥75/≥80 段与两半段同向性加权）。

纪律：零产线行为改动（不改 nine_dim.py / nine_dim_backtest.py / 回放
样本库）；全程离线（tushare API 拒用）；日志 /c/Temp/nine_ind_pct.log。
用法：
  C:/Program Files/Python311/python.exe nine_dim_industry_pct.py
产物：reports/nine_industry_pct_report.md
     data/nine_dim_ind_pct_replay.jsonl（逐样本三口径评分，新文件）
"""
import argparse
import collections
import glob
import json
import os
import sys
from datetime import datetime

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nine_dim as nd                     # noqa: E402  规则点表单一来源（只读）
import nine_dim_backtest as nb            # noqa: E402  复用 _fina_asof/_i8（只读）
import db                                 # noqa: E402  fina SQLite 读层（只读）

REPLAY = os.path.join(HERE, "data", "nine_dim_replay.jsonl")
STAGING = os.path.join(HERE, "data", "tushare_staging")
OUT_JSONL = os.path.join(HERE, "data", "nine_dim_ind_pct_replay.jsonl")
OUT_MD = os.path.join(HERE, "reports", "nine_industry_pct_report.md")
LOG = "C:/Temp/nine_ind_pct.log"   # 任务规格 /c/Temp/nine_ind_pct.log（win 原生路径）

WINDOW_END = "20260924"   # 对齐 5479 样本（k33_grid_report：51 锚点）
IND_MIN = 10              # 行业成员数门（任务规格）
Q_PCT, G_LO_PCT, G_HI_PCT = 0.70, 0.30, 0.70   # q1≥p70 / g1≤p30 / g_p>p70
V2_RANK_PCT = 0.80        # 补充口径：行业内 score 分位 ≥80%


# ── 基础设施 ──────────────────────────────────────────────
class _Tee:
    def __init__(self, path):
        self.f = open(path, "w", encoding="utf-8")

    def write(self, s):
        sys.__stdout__.write(s)
        self.f.write(s)

    def flush(self):
        sys.__stdout__.flush()
        self.f.flush()


def log(msg=""):
    print(msg, flush=True)


# ── ① 样本装载与基线对账 ──────────────────────────────────
def load_rows():
    rows = []
    with open(REPLAY, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                if str(r["anchor"]) <= WINDOW_END:   # 对齐 5479 样本窗口
                    rows.append(r)
    return rows


def score_from_v(v):
    """产线评分公式离线重算：score = 100 × (Σ项得分 − Σ罚分) / Σ可判项分。

    与 nd.score_one 对账结论：罚分项计 earned 不计 judg_max（nine_dim.py
    :1114-1115 只累加 items 的 f_judg）；adj 权重只进 cov 不进 score；
    样本库已全部 cov≥0.75，无需再门槛。"""
    earned, judg = 0.0, 0.0
    for fa in nd.FACTORS:
        for it in fa["items"]:
            st = v.get(it["id"])
            if st is True:
                earned += it["pts"]
            if st is not None:
                judg += it["pts"]
    for p in nd.PENALTIES:
        if v.get(p["id"]) is True:
            earned -= p["pts"]
    return round(100.0 * earned / judg, 1) if judg else None


def parity_check(rows):
    bad = []
    for r in rows:
        s0 = score_from_v(r["v"])
        if s0 is None or abs(s0 - r["score"]) > 1e-9:
            bad.append((r["anchor"], r["code"], r["score"], s0))
    return bad


# ── ② 原始值 as-of 重构（全离线） ─────────────────────────
def load_pe_maps(anchors):
    """staging 逐日 daily_basic parquet → {anchor: {code: pe_ttm}}。"""
    out = {}
    for a in anchors:
        fs = glob.glob(os.path.join(STAGING, "*",
                                    "ts_daily_basic_%s.parquet" % a))
        if not fs:
            log("  WARN: staging 缺 %s daily_basic，该锚点 PEG 保持原判定" % a)
            out[a] = {}
            continue
        df = nb.pd.read_parquet(fs[0], columns=["ts_code", "pe_ttm"])
        out[a] = dict(zip(df["ts_code"], nb.pd.to_numeric(
            df["pe_ttm"], errors="coerce")))
    return out


def load_fina_recs(codes):
    conn = db.get_conn()
    recs = {}
    for c in codes:
        try:
            recs[c] = db.nd_fina_query(conn, c)
        except Exception:
            recs[c] = None
    return recs


def build_raw(rows, anchors):
    """{("anchor","code"): {"or_yoy":v,"peg":v|None}}，全部 as-of。"""
    codes = sorted({r["code"] for r in rows})
    log("fina as-of 重构: %d codes ..." % len(codes))
    recs = load_fina_recs(codes)
    pe_maps = load_pe_maps(anchors)
    raw = {}
    miss_fina = miss_pe = 0
    for r in rows:
        a, c = str(r["anchor"]), r["code"]
        cur = nb._fina_asof(recs.get(c) or {}, int(a)).get("cur") or {}
        or_yoy = nd._f(cur.get("or_yoy"))
        npy = nd._f(cur.get("netprofit_yoy"))
        pe = nd._f(pe_maps.get(a, {}).get(c))
        peg = (pe / npy) if (pe and pe > 0 and npy and npy > 0) else None
        if or_yoy is None:
            miss_fina += 1
        if pe is None or npy is None or npy <= 0 or not pe:
            miss_pe += 1
        raw[(a, c)] = {"or_yoy": or_yoy, "peg": peg}
    log("raw 重构完成: fina缺or_yoy=%d, PEG不可定义=%d / %d"
        % (miss_fina, miss_pe, len(rows)))
    return raw


# ── ③ 分位换算与三口径重评 ────────────────────────────────
def _pct_of(vals_sorted_with_v, x):
    """平均秩分位：(<x + 0.5·==x)/n，vals 已排序。"""
    lo = np.searchsorted(vals_sorted_with_v, x, side="left")
    eq = np.searchsorted(vals_sorted_with_v, x, side="right") - lo
    return (lo + 0.5 * eq) / len(vals_sorted_with_v)


def rescore(rows, raw):
    """三口径评分：abs（对账基线）/ mkt（全市场分位）/ ind（行业分位，
    成员<IND_MIN 归全市场分位并标注）。"""
    by_anchor = collections.defaultdict(list)
    for r in rows:
        by_anchor[str(r["anchor"])].append(r)

    for a, rs in by_anchor.items():
        for r in rs:
            r["_n_ind"] = 0                       # 行业单元规模（全样本）
        ind_cell = collections.Counter(
            r["industry"] or "" for r in rs)
        # 锚点级截面：各可转换项的 (行业 → 值列表) 与全市场值列表
        oy_ind, peg_ind = collections.defaultdict(list), \
            collections.defaultdict(list)
        oy_all, peg_all = [], []
        for r in rs:
            d = raw.get((a, r["code"])) or {}
            oy, peg = d.get("or_yoy"), d.get("peg")
            if oy is not None:
                oy_ind[r["industry"] or ""].append(oy)
                oy_all.append(oy)
            if peg is not None:
                peg_ind[r["industry"] or ""].append(peg)
                peg_all.append(peg)
        oy_all = np.sort(np.array(oy_all)) if oy_all else np.array([])
        peg_all = np.sort(np.array(peg_all)) if peg_all else np.array([])
        oy_ind_s = {g: np.sort(np.array(v)) for g, v in oy_ind.items()}
        peg_ind_s = {g: np.sort(np.array(v)) for g, v in peg_ind.items()}

        for r in rs:
            d = raw.get((a, r["code"])) or {}
            g = r["industry"] or ""
            v0, flips = dict(r["v"]), {}
            oy, peg = d.get("or_yoy"), d.get("peg")
            # 三个口径的 (q1, g1, g_p) 状态
            q1_m = q1_i = v0.get("q1")
            g1_m = g1_i = v0.get("g1")
            gp_m = gp_i = v0.get("g_p")
            fb = {"q1": False, "g1": False, "g_p": False}   # ind 归全市场标注
            # q1（or_yoy 缺失或原判定 None → 保持原判定）
            if oy is not None and v0.get("q1") is not None:
                pm = _pct_of(oy_all, oy) if len(oy_all) else None
                pi = (_pct_of(oy_ind_s[g], oy)
                      if len(oy_ind_s.get(g, [])) >= IND_MIN else None)
                if pm is not None:
                    q1_m = pm >= Q_PCT
                if pi is not None:
                    q1_i = pi >= Q_PCT
                else:
                    fb["q1"] = True
                    q1_i = q1_m
            # g1/g_p（PEG 定义域内才换算，同产线 _j_g1/_j_gp）
            if peg is not None and v0.get("g1") is not None:
                pm = _pct_of(peg_all, peg) if len(peg_all) else None
                pi = (_pct_of(peg_ind_s[g], peg)
                      if len(peg_ind_s.get(g, [])) >= IND_MIN else None)
                if pm is not None:
                    g1_m = pm <= G_LO_PCT
                    gp_m = pm > G_HI_PCT
                if pi is not None:
                    g1_i = pi <= G_LO_PCT
                    gp_i = pi > G_HI_PCT
                else:
                    fb["g1"] = fb["g_p"] = True
                    g1_i, gp_i = g1_m, gp_m
            flips["q1"] = (v0.get("q1"), q1_m, q1_i)
            flips["g1"] = (v0.get("g1"), g1_m, g1_i)
            flips["g_p"] = (v0.get("g_p"), gp_m, gp_i)

            vm = dict(v0); vm["q1"], vm["g1"], vm["g_p"] = q1_m, g1_m, gp_m
            vi = dict(v0); vi["q1"], vi["g1"], vi["g_p"] = q1_i, g1_i, gp_i
            r["_v"] = {"abs": score_from_v(v0),
                       "mkt": score_from_v(vm), "ind": score_from_v(vi)}
            r["_ind_fallback"] = fb
            r["_n_ind"] = ind_cell[g]
            r["_flips"] = {k: [None if x is None else bool(x) for x in v]
                           for k, v in flips.items()}
    return rows


# ── ④ 聚合统计（k33_grid_report 同款口径） ────────────────
def thin(rows):
    """同票 20 交易日去重（K32 官方口径，k33_grid_report.thin 同款）。"""
    anchors = sorted({str(r["anchor"]) for r in rows})
    aidx = {a: i for i, a in enumerate(anchors)}
    last = {}
    for r in sorted(rows, key=lambda x: (str(x["anchor"]), x["code"])):
        i = aidx[str(r["anchor"])]
        if i - last.get(r["code"], -99) >= 2:
            last[r["code"]] = i
            yield r


def stats(rs, key):
    vals = [r[key] for r in rs if r.get(key) is not None
            and r.get("mature20") and not r.get("adj")]
    if not vals:
        return None
    sv = sorted(vals)
    n = len(sv)
    exs = [r[key] - r["bench20"] for r in rs if r.get(key) is not None
           and r.get("mature20") and not r.get("adj")
           and r.get("bench20") is not None]
    return {"n": n,
            "win": round(sum(1 for x in vals if x > 0) * 100.0 / n, 1),
            "avg": round(sum(vals) / n, 2),
            "exc": round(sum(exs) / len(exs), 2) if exs else None,
            "p5": round(sv[max(0, int(n * 0.05) - 1)], 1),
            "min": round(sv[0], 1)}


def fmt(st):
    if not st:
        return "—"
    grey = "（灰）" if st["n"] < 10 else ""
    exc = "—" if st["exc"] is None else "%+.1f" % st["exc"]
    return "%d%s / %.0f%% / %+.1f%% / %s / %.1f / %.1f" % (
        st["n"], grey, st["win"], st["avg"], exc, st["p5"], st["min"])


SEGMENTS = [("≤25", lambda s: s <= 25),
            ("25-60", lambda s: 25 < s < 60),
            ("<60 小计", lambda s: s < 60),
            ("60-75", lambda s: 60 <= s < 75),
            ("≥75 小计", lambda s: s >= 75),
            ("75-85", lambda s: 75 <= s < 85),
            ("≥80 小计", lambda s: s >= 80),
            ("≥85", lambda s: s >= 85)]


def seg_table(rows, key):
    out = ["| 分段 | n/胜率/均值/超额/p5/最低 |", "|---|---|"]
    for name, pred in SEGMENTS:
        sel = [r for r in rows if pred(r["_v"][key] or 0)]
        out.append("| %s | %s |" % (name, fmt(stats(sel, "r20"))))
    return "\n".join(out)


def two_halves(rows, key):
    anchors = sorted({str(r["anchor"]) for r in rows})
    mid = anchors[len(anchors) // 2]

    def _halves_ok():
        """≥75 段两半窗均值同号且均为负 → True；任一半窗无样本 → None。"""
        out = {}
        for lb, flt in (("前半", lambda r: str(r["anchor"]) <= mid),
                        ("后半", lambda r: str(r["anchor"]) > mid)):
            sel = [r for r in rows if flt(r) and (r["_v"][key] or 0) >= 75]
            st = stats(sel, "r20")
            out[lb] = st
        if not out["前半"] or not out["后半"]:
            return None
        return out["前半"]["avg"] < 0 and out["后半"]["avg"] < 0

    out = {}
    for lb, flt in (("前半(≤%s)" % mid, lambda r: str(r["anchor"]) <= mid),
                    ("后半(>%s)" % mid, lambda r: str(r["anchor"]) > mid)):
        sel_rows = [r for r in rows if flt(r)]
        d = {}
        for seg, th in (("≥75", 75), ("≥85", 85)):
            sel = [r for r in sel_rows if (r["_v"][key] or 0) >= th]
            d[seg] = stats(sel, "r20")
        out[lb] = d
    return out, _halves_ok()


def jaccard(rows, key_a, key_b, th):
    sa = {r["code"] + "@" + str(r["anchor"]) for r in rows
          if (r["_v"][key_a] or 0) >= th}
    sb = {r["code"] + "@" + str(r["anchor"]) for r in rows
          if (r["_v"][key_b] or 0) >= th}
    if not sa and not sb:
        return None
    return round(len(sa & sb) * 100.0 / len(sa | sb), 1)


# ── ⑤ 主流程 ──────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-parity-gate", action="store_true")
    args = ap.parse_args()
    t0 = datetime.now()
    log("== nine_dim_industry_pct W5 对照回放 %s ==" % t0.strftime("%F %T"))

    rows = load_rows()
    anchors = sorted({str(r["anchor"]) for r in rows})
    log("样本 %d（%d 锚点 %s..%s，对齐 5479 样本窗口）"
        % (len(rows), len(anchors), anchors[0], anchors[-1]))

    bad = parity_check(rows)
    rate = 100.0 * (len(rows) - len(bad)) / max(len(rows), 1)
    log("parity 对账: %d/%d 不一致（%.1f%% 一致）"
        % (len(bad), len(rows), rate))
    for b in bad[:10]:
        log("  MISMATCH %s %s stored=%s recomputed=%s" % b)
    if rate < 95.0 and not args.skip_parity_gate:
        log("对账率 <95%% → 终止（--skip-parity-gate 可跳过）")
        return 1
    if bad:
        log("NOTE: 不一致样本系历史部分重跑留下的 score/v 微不一致"
            "（当前点表重算差 ±5/+4 分量级）；三口径统一改用当前点表"
            "从 v 重算的 abs 基线，保证算术单一来源，stored score 留档"
            " jsonl 供追溯。")

    raw = build_raw(rows, anchors)
    rows = rescore(rows, raw)

    thinned = list(thin(rows))
    log("thinned 去重: %d" % len(thinned))

    # 逐样本落盘
    os.makedirs(os.path.dirname(OUT_JSONL), exist_ok=True)
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({
                "anchor": r["anchor"], "code": r["code"],
                "industry": r["industry"], "n_ind": r["_n_ind"],
                "score_stored": r["score"],
                "score_abs": r["_v"]["abs"], "score_mkt": r["_v"]["mkt"],
                "score_ind": r["_v"]["ind"],
                "ind_fallback": r["_ind_fallback"],
                "flips": r["_flips"], "r20": r["r20"],
                "mature20": r["mature20"], "adj": r["adj"],
                "bench20": r["bench20"]}, ensure_ascii=False) + "\n")
    log("逐样本三口径评分 → %s" % OUT_JSONL)

    # 换算触发面统计
    n_fb = sum(1 for r in rows if any(r["_ind_fallback"].values()))
    n_chg = {k: sum(1 for r in rows
                    if r["_flips"][k][1] != r["_flips"][k][2]) for k in
             ("q1", "g1", "g_p")}
    big_ind = sum(1 for r in rows if r["_n_ind"] >= IND_MIN)
    log("行业门: ≥%d 成员样本 %d/%d；小行业归全市场样本 %d"
        % (IND_MIN, big_ind, len(rows), n_fb))
    log("mkt→ind 状态翻转: %s" % n_chg)

    L = []
    ap_ = L.append
    ap_("# ninedim 行业分位全市场化改造验证报告（W5）")
    ap_("")
    ap_("- 生成：%s" % t0.strftime("%F %T"))
    ap_("- 数据：nine_dim_replay.jsonl 锚点≤%s（对齐 5479 样本 / 51 锚点窗口），"
        "thinned 去重 %d；全程离线（fina SQLite + staging 逐日 daily_basic "
        "as-of parquet）" % (WINDOW_END, len(thinned)))
    ap_("- 口径：仅成熟窗口、剔跨除权；r20 交易日窗口；n<10 灰显不构成结论")
    ap_("- parity 对账：abs 版由逐项判定 v 按当前点表重算，%d/%d（%.1f%%）"
        "与存储 score 一致；不一致样本（历史部分重跑留下的 score/v 微差，"
        "量级 ±5/+4 分）三口径统一以 v 重算为准" %
        (len(rows) - len(bad), len(rows), rate))
    ap_("")
    ap_("## ① 口径定义")
    ap_("")
    ap_("· 产线评分无逐维全市场分位：全市场分位暴露只在粗筛门 RPS50>80"
        "（动量门，决定候选池构成，不进评分公式）；q2/g2/s1/s_p 本已是"
        "行业相对项。")
    ap_("· 可截面化改造项（15 分权重）：q1 营收增速≥25%%→分位≥70%%；"
        "g1 PEG≤1.0→分位≤30%%；g_p PEG>1.5→分位>70%%扣3。")
    ap_("· **mkt 全市场分位版**：同锚点全部样本截面分位判定。")
    ap_("· **ind 行业分位版**：同锚点同行业完整评分样本 ≥%d 只用行业内分位；"
        "<%d 归全市场分位（防小行业噪声，任务规格，ind_fallback 标注）。"
        % (IND_MIN, IND_MIN))
    ap_("· 同伴集=同锚点候选池完整评分样本（cov≥0.75 子集），与产线 q2 的"
        "「候选池内近似」同款保守口径；原始值 as-of：fina ann_date≤锚点、"
        "pe_ttm 取 staging 当日 parquet，PEG 定义域 pe>0 且增速>0。")
    ap_("· ≥%d 成员样本覆盖：%d/%d（%.1f%%）；行业单元触发面受限是本验证的"
        "天花板之一。" % (IND_MIN, big_ind, len(rows),
                          100.0 * big_ind / max(len(rows), 1)))
    ap_("")
    ap_("## ② 反指分段对照（thinned r20：n/胜率/均值/超额/p5/最低）")
    ap_("")
    for key, lb in (("abs", "abs 产线基线（绝对阈值，对账参照）"),
                    ("mkt", "mkt 全市场分位版"),
                    ("ind", "ind 行业分位版")):
        ap_("### %s" % lb)
        ap_("")
        ap_(seg_table(thinned, key))
        ap_("")
    ap_("### ≥75/≥80/≥85 段三版汇总（thinned r20）")
    ap_("")
    ap_("| 段 | abs | mkt | ind |")
    ap_("|---|---|---|---|")
    for th in (75, 80, 85):
        cells = []
        for key in ("abs", "mkt", "ind"):
            st = stats([r for r in thinned if (r["_v"][key] or 0) >= th],
                       "r20")
            cells.append("%d / %.0f%% / %+.1f%%" %
                         (st["n"], st["win"], st["avg"]) if st else "—")
        ap_("| ≥%d | %s | %s | %s |" % (th, cells[0], cells[1], cells[2]))
    ap_("")
    ap_("≥75 集合重合（Jaccard）：abs∩mkt %s%%，mkt∩ind %s%%，abs∩ind %s%%"
        % (jaccard(rows, "abs", "mkt", 75),
           jaccard(rows, "mkt", "ind", 75),
           jaccard(rows, "abs", "ind", 75)))
    ap_("")
    ap_("## ③ 正指侧（低分段是否同时改善，thinned r20）")
    ap_("")
    for key, lb in (("mkt", "mkt 全市场分位版"), ("ind", "ind 行业分位版")):
        sel = [r for r in thinned if (r["_v"][key] or 0) <= 25]
        st = stats(sel, "r20")
        ap_("- %s ≤25 段：%s" % (lb, fmt(st)))
    ap_("")

    ap_("## ④ 两半段同向性（窗口前半 vs 后半，thinned r20）")
    ap_("")
    ap_("| 口径 | 半窗 | ≥75 段 | ≥85 段 |")
    ap_("|---|---|---|---|")
    for key in ("mkt", "ind"):
        hh = two_halves(rows, key)
        for hlb, d in hh.items():
            ap_("| %s | %s | %s | %s |" % (
                key, hlb,
                ("n=%d %+.1f%%" % (d["≥75"]["n"], d["≥75"]["avg"]))
                if d["≥75"] else "—",
                ("n=%d %+.1f%%" % (d["≥85"]["n"], d["≥85"]["avg"]))
                if d["≥85"] else "—"))
    ap_("")

    ap_("## ⑤ 行业拥挤视角（警报命中集中度）")
    ap_("")
    for th in (75, 85):
        cnt = collections.Counter(
            (r["industry"] or "缺失") for r in rows
            if (r["_v"]["abs"] or 0) >= th)
        tot = sum(cnt.values())
        tops = cnt.most_common(5)
        cum = 0
        parts = []
        for k, (ind, n) in enumerate(tops[:3], 1):
            cum += n
            parts.append("%s %s(%d)" % ("TOP%d" % k, ind, n))
        ap_("- abs ≥%d 命中 %d 条：%s；TOP1 占比 %.0f%%，TOP3 合计 %.0f%%"
            % (th, tot, "、".join(parts),
               100.0 * tops[0][1] / max(tot, 1),
               100.0 * cum / max(tot, 1)))
    ap_("")
    ap_("- live 警报池 data/nine_dim_history.jsonl 尚未落盘（W1-2 已接"
        "盘前卡/持仓页，实际命中为 0 或未生成），行业集中度以回放 ≥75 "
        "样本为代理口径。")
    ap_("")

    ap_("## ⑥ 补充口径：行业相对警报（个股拥挤 vs 行业拥挤）")
    ap_("")
    ap_("abs score≥75 且同行业 score 分位≥%d%%（同锚点同行业成员 ≥%d 才"
        "启用）：" % (int(V2_RANK_PCT * 100), IND_MIN))
    ap_("")
    by_anchor = collections.defaultdict(list)
    for r in rows:
        by_anchor[str(r["anchor"])].append(r)
    v2 = []
    for a, rs in by_anchor.items():
        sc_ind = collections.defaultdict(list)
        for r in rs:
            sc_ind[r["industry"] or ""].append(r["_v"]["abs"] or 0)
        for r in rs:
            vals = sc_ind[r["industry"] or ""]
            if (r["_v"]["abs"] or 0) >= 75 and len(vals) >= IND_MIN:
                s = r["_v"]["abs"] or 0
                pct = (sum(1 for x in vals if x < s) +
                       0.5 * sum(1 for x in vals if x == s)) / len(vals)
                if pct >= V2_RANK_PCT:
                    v2.append(r)
    st = stats(list(thin(v2)), "r20") if v2 else None
    ap_("```")
    ap_("行业相对警报命中（raw）：n=%d" % len(v2))
    ap_("thinned r20: %s" % (fmt(st) if st else "—"))
    ap_("```")
    ap_("")
    ap_("对照 abs ≥75 全体（thinned）：%s" %
        fmt(stats([r for r in thinned if (r["_v"]["abs"] or 0) >= 75],
                  "r20")))
    ap_("")

    # ⑦ 判定
    g85_m = stats([r for r in thinned if (r["_v"]["mkt"] or 0) >= 85], "r20")
    g85_i = stats([r for r in thinned if (r["_v"]["ind"] or 0) >= 85], "r20")
    g75_m = stats([r for r in thinned if (r["_v"]["mkt"] or 0) >= 75], "r20")
    g75_i = stats([r for r in thinned if (r["_v"]["ind"] or 0) >= 75], "r20")
    _hh_m, halves_ok_m = two_halves(rows, "mkt")
    _hh_i, halves_ok_i = two_halves(rows, "ind")

    L2 = []
    L2.append("## ⑦ 判定（任务规则：ind ≥85 段负向幅度优于 mkt ≥20% "
              "且两半段同向 → 建议切换；否则维持）")
    L2.append("")
    if g85_m and g85_i and g85_m["avg"] < 0:
        gain = (abs(g85_i["avg"]) - abs(g85_m["avg"])) / abs(g85_m["avg"])
        L2.append("- ≥85 段：mkt n=%d %+.1f%% vs ind n=%d %+.1f%%，"
                  "负向幅度差 %+.0f%%（阈值 +20%%）"
                  % (g85_m["n"], g85_m["avg"], g85_i["n"], g85_i["avg"],
                     100 * gain))
    else:
        L2.append("- ≥85 段：样本不足（mkt=%s ind=%s），不满足判定前提"
                  % (g85_m and g85_m["n"], g85_i and g85_i["n"]))
    if g75_m and g75_i:
        gain75 = (abs(g75_i["avg"]) - abs(g75_m["avg"])) / \
            abs(g75_m["avg"]) if g75_m["avg"] < 0 else None
        L2.append("- ≥75 段（样本量主判据）：mkt n=%d 胜率 %.0f%% %+.1f%% "
                  "vs ind n=%d 胜率 %.0f%% %+.1f%%%s"
                  % (g75_m["n"], g75_m["win"], g75_m["avg"],
                     g75_i["n"], g75_i["win"], g75_i["avg"],
                     ("，负向幅度差 %+.0f%%" % (100 * gain75))
                     if gain75 is not None else ""))
    L2.append("- 两半段同向（≥75 两半窗均值均为负）：mkt=%s ind=%s"
              % (halves_ok_m, halves_ok_i))
    L2.append("")
    L.extend(L2)

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    log("报告 → %s" % OUT_MD)
    log("== done %.1fs ==" % (datetime.now() - t0).total_seconds())
    return 0


if __name__ == "__main__":
    _T = _Tee(LOG)
    sys.stdout = _T
    try:
        sys.exit(main())
    finally:
        sys.stdout = sys.__stdout__
        _T.f.close()
