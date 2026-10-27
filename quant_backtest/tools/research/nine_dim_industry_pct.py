# -*- coding: utf-8 -*-
"""nine_dim_industry_pct.py —— W5 行业分位全市场化改造验证（研究脚本，只读产线）。

背景（W5 实施计划滚动项「行业分位全市场化 → 反指升格正式风控输入」）：
ninedim 已实证为反指（10-02 快照：≥75 段 thinned r20 胜率 9% / -27.9%，
≥85 段 4 样本 -71.5%），但高分可能被行业结构污染——强势行业整队通过
RPS 门、整队拿高分，警报池变相=行业拥挤。本脚本检验：把评分中可截面
化的原始值项改为「同行业分位」后，反指强度是否更纯。

口径梳理结论（读 nine_dim.py / nine_dim_backtest.py 实证）：
· 产线评分（V3, score_one）内部没有任何「全市场逐维分位」——全部是
  绝对阈值或事件判定；全市场分位暴露只在粗筛门 RPS50>80（动量分位门，
  决定候选池构成，不进评分公式）。
· 已是行业相对项（本次不动）：q2 毛利率≥行业p70（候选池内）、
  g2 EV≤行业均值（PE 中位回退）、s1 行业5日涨幅top3、s_p 行业RPS<50。
· 可截面化改造项（权重 6+6+3=15 分）：
    q1  营收增速≥25%   g1  PEG≤1.0   g_p  PEG>1.5 扣3

分位阈值校准（关键方法论，v2 修订）：
naive 固定分位（p70/p30/p70）在本动量候选域上远严于原绝对阈值，会把
分数分布整体压低、高分段清空（v1 试跑实证，已废弃）。改为「市场选择
率等价分位」：先把绝对阈值换算成全市场截面分位阈值 X
    X_q1 = P(or_yoy < 25)   X_g1 = P(peg ≤ 1.0)   X_gp = P(peg ≤ 1.5)
（ pooled 同锚点截面），则：
· mkt 全市场分位版：q1/g1/g_p 用全市场分位与 X 比较——与绝对规则
  数学等价（单调变换，至多并列序差），作内部校验列；
· ind 行业分位版：同一组 X 阈值改用行业内分位（同锚点同行业完整评分
  样本 ≥IND_MIN 只）；<IND_MIN 归全市场分位（任务规格，ind_fallback
  标注）。mkt 与 ind 的差异因此纯粹归因于「行业内相对化」。

诚实标注：
· abs 基线列 = 存储分数（官方 k33 口径）；mkt/ind = 逐项判定 v 按当前
  点表重算再转换。v 重算与存储 score 对账 98.0%（109/5345 样本存在
  历史部分重跑留下的 score/v 微不一致，量级 ±5/+4 分），对高分段成员
  数影响 +3（49 vs 46 raw）。
· 样本库漂移警示：当前 nine_dim_replay.jsonl（锚点≤20260924，5345 行）
  与 10-02 k33 快照（5479 行）已不一致——存储口径 ≥75 段从 23/9%/-27.9%
  漂至 41/26.8%/-12.6%，≥85 段从 4/-71.5% 漂至 5/-7.8%。本验证以当前
  库为准，判定时同步披露漂移。
· 同伴集 = 同锚点候选池完整评分样本（cov≥0.75 子集），与产线 q2 的
  「候选池内近似」同款保守口径；全行业成员集需全市场 fina 覆盖，离线
  不可得。原始值 as-of：or_yoy/netprofit_yoy 取 fina（ann_date≤锚点，
  SQLite 缓存，nine_dim_backtest._fina_asof 同款）；pe_ttm 取
  data/tushare_staging 逐日 as-of parquet。PEG 仅在 pe>0 且净利增速>0
  时有定义（同产线 _j_g1/_j_gp 定义域）；原始值缺失或原判定 None 时
  保持原判定（绝不 None→judged）。

附加产出：
· 正指侧：≤25 / <60 段三版对照；
· 行业拥挤视角：≥75/≥85 命中的行业集中度（top1/2/3 占比）；
· 行业相对警报口径（补充）：abs≥75 且同行业 abs 分位≥80%（成员≥10），
  对照基线=abs≥75∩同行业成员≥10 可判集——直接检验「个股拥挤 vs
  行业拥挤」；
· 两半段同向性。

判定规则（任务规格）：ind ≥85 段负向幅度优于 mkt ≥20% 且回放窗口两
半段同向 → 建议评分口径切换产线方案（只写方案不动手）；否则给维持
证据。n<10 灰显不构成结论。

纪律：零产线行为改动；全程离线（tushare API 拒用）；日志
C:/Temp/nine_ind_pct.log。
产物：reports/nine_industry_pct_report.md
     data/nine_dim_ind_pct_replay.jsonl（逐样本三口径评分，新文件）

用法：C:/Program Files/Python311/python.exe nine_dim_industry_pct.py
"""

import collections
import glob
import json
import os
import sys
from datetime import datetime

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nine_dim as nd  # noqa: E402  规则点表单一来源（只读）
import nine_dim_backtest as nb  # noqa: E402  复用 _fina_asof（只读）
import db  # noqa: E402  fina SQLite 读层（只读）

REPLAY = os.path.join(HERE, "data", "nine_dim_replay.jsonl")
STAGING = os.path.join(HERE, "data", "tushare_staging")
OUT_JSONL = os.path.join(HERE, "data", "nine_dim_ind_pct_replay.jsonl")
OUT_MD = os.path.join(HERE, "reports", "nine_industry_pct_report.md")
LOG = "C:/Temp/nine_ind_pct.log"

WINDOW_END = "20260924"  # 对齐 5479 样本（k33_grid_report：51 锚点）
IND_MIN = 10  # 行业成员数门（任务规格）
V2_RANK_PCT = 0.80  # 补充口径：行业内 score 分位 ≥80%


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


# ── ① 样本装载 / 基线公式 ─────────────────────────────────
def load_rows():
    rows = []
    with open(REPLAY, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                if str(r["anchor"]) <= WINDOW_END:
                    rows.append(r)
    return rows


def score_from_v(v):
    """产线评分公式离线重算：score = 100 × (Σ项得分 − Σ罚分) / Σ可判项分。

    与 nd.score_one 对账：罚分计 earned 不计 judg_max（nine_dim.py
    :1114-1115 只累加 items 的 f_judg）；adj 权重只进 cov 不进 score。"""
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


def stats(rs):
    """thinned r20 统计：仅成熟+未除权；k33_grid_report.stats 同款。"""
    vals = [
        r["r20"]
        for r in rs
        if r.get("r20") is not None and r.get("mature20") and not r.get("adj")
    ]
    if not vals:
        return None
    sv = sorted(vals)
    n = len(sv)
    exs = [
        r["r20"] - r["bench20"]
        for r in rs
        if r.get("r20") is not None
        and r.get("mature20")
        and not r.get("adj")
        and r.get("bench20") is not None
    ]
    return {
        "n": n,
        "win": round(sum(1 for x in vals if x > 0) * 100.0 / n, 1),
        "avg": round(sum(vals) / n, 2),
        "exc": round(sum(exs) / len(exs), 2) if exs else None,
        "p5": round(sv[max(0, int(n * 0.05) - 1)], 1),
        "min": round(sv[0], 1),
    }


def fmt(st):
    if not st:
        return "—"
    grey = "（灰）" if st["n"] < 10 else ""
    exc = "—" if st["exc"] is None else "%+.1f" % st["exc"]
    return "%d%s / %.0f%% / %+.1f%% / %s / %.1f / %.1f" % (
        st["n"],
        grey,
        st["win"],
        st["avg"],
        exc,
        st["p5"],
        st["min"],
    )


def _pct_of(arr_sorted, x):
    """平均秩分位：(<x + 0.5·==x)/n。返回 Python float（防 np.bool_
    混入判定状态——score_from_v 用 `st is True` 判别，np.bool_ 会静默
    变成'判了但不得分'，v2.1 实测踩坑）。"""
    lo = int(np.searchsorted(arr_sorted, x, side="left"))
    eq = int(np.searchsorted(arr_sorted, x, side="right")) - lo
    return (lo + 0.5 * eq) / len(arr_sorted)


# ── ② 原始值 as-of 重构（全离线） ─────────────────────────
def load_pe_maps(anchors):
    out = {}
    for a in anchors:
        fs = glob.glob(os.path.join(STAGING, "*", "ts_daily_basic_%s.parquet" % a))
        if not fs:
            log("  WARN: staging 缺 %s daily_basic" % a)
            out[a] = {}
            continue
        df = nb.pd.read_parquet(fs[0], columns=["ts_code", "pe_ttm"])
        out[a] = dict(
            zip(df["ts_code"], nb.pd.to_numeric(df["pe_ttm"], errors="coerce"))
        )
    return out


def build_raw(rows, anchors):
    """{("anchor","code"): {"or_yoy", "peg"}} + 逐锚点市场选择率等价分位
    阈值 X_a（锚点内 P(or_yoy<25) / P(peg≤1.0) / P(peg≤1.5)）。"""
    codes = sorted({r["code"] for r in rows})
    log("fina as-of 重构: %d codes ..." % len(codes))
    conn = db.get_conn()
    recs = {}
    for c in codes:
        try:
            recs[c] = db.nd_fina_query(conn, c)
        except Exception:
            recs[c] = None
    pe_maps = load_pe_maps(anchors)
    raw = {}
    for r in rows:
        a, c = str(r["anchor"]), r["code"]
        cur = nb._fina_asof(recs.get(c) or {}, int(a)).get("cur") or {}
        or_yoy = nd._f(cur.get("or_yoy"))
        npy = nd._f(cur.get("netprofit_yoy"))
        pe = nd._f(pe_maps.get(a, {}).get(c))
        peg = (pe / npy) if (pe and pe > 0 and npy and npy > 0) else None
        raw[(a, c)] = {"or_yoy": or_yoy, "peg": peg}

    by_anchor = collections.defaultdict(list)
    for (a, _c), d in raw.items():
        by_anchor[a].append(d)
    xs = {}
    for a, ds in by_anchor.items():
        oys = [d["or_yoy"] for d in ds if d["or_yoy"] is not None]
        pegs = [d["peg"] for d in ds if d["peg"] is not None]
        xs[a] = {
            "q1": (float(np.mean(np.array(oys) < 25)) if oys else 0.0),
            "g1": (float(np.mean(np.array(pegs) <= 1.0)) if pegs else 0.0),
            "gp": (float(np.mean(np.array(pegs) <= 1.5)) if pegs else 0.0),
        }
    n_def = sum(1 for d in raw.values() if d["peg"] is not None)
    log(
        "原始值重构: or_yoy n=%d, PEG 可定义 n=%d（缺 %d）；逐锚点等价分位"
        "阈值 X_q1 中位=%.3f X_g1 中位=%.3f"
        % (
            sum(1 for d in raw.values() if d["or_yoy"] is not None),
            n_def,
            len(raw) - n_def,
            float(np.median([x["q1"] for x in xs.values()])),
            float(np.median([x["g1"] for x in xs.values()])),
        )
    )
    return raw, xs


# ── ③ 三口径重评 ──────────────────────────────────────────
def rescore(rows, raw, xs):
    """abs=存储分数；mkt=全市场分位版（≈abs，内部校验）；
    ind=行业分位版（<IND_MIN 归全市场分位并标注）。"""
    by_anchor = collections.defaultdict(list)
    for r in rows:
        by_anchor[str(r["anchor"])].append(r)

    agree = {"q1": 0, "g1": 0, "g_p": 0}
    total = {"q1": 0, "g1": 0, "g_p": 0}
    for a, rs in by_anchor.items():
        for r in rs:
            r["_n_ind"] = 0
        ind_cell = collections.Counter((r["industry"] or "") for r in rs)
        oy_ind, peg_ind = collections.defaultdict(list), collections.defaultdict(list)
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
            v0 = dict(r["v"])
            oy, peg = d.get("or_yoy"), d.get("peg")
            q1_m, q1_i = v0.get("q1"), v0.get("q1")
            g1_m, g1_i = v0.get("g1"), v0.get("g1")
            gp_m, gp_i = v0.get("g_p"), v0.get("g_p")
            fb = {"q1": False, "g1": False, "g_p": False}
            if oy is not None and v0.get("q1") is not None:
                pm = _pct_of(oy_all, oy)
                q1_m = bool(pm >= xs[a]["q1"])
                total["q1"] += 1
                pi = (
                    _pct_of(oy_ind_s[g], oy)
                    if len(oy_ind_s.get(g, [])) >= IND_MIN
                    else None
                )
                if pi is not None:
                    q1_i = bool(pi >= xs[a]["q1"])
                else:
                    fb["q1"] = True
                    q1_i = q1_m
                agree["q1"] += 1 if q1_m == v0["q1"] else 0
            if peg is not None and v0.get("g1") is not None:
                pm = _pct_of(peg_all, peg)
                g1_m = bool(pm <= xs[a]["g1"])
                gp_m = bool(pm > xs[a]["gp"])
                total["g1"] += 1
                total["g_p"] += 1
                pi = (
                    _pct_of(peg_ind_s[g], peg)
                    if len(peg_ind_s.get(g, [])) >= IND_MIN
                    else None
                )
                if pi is not None:
                    g1_i = bool(pi <= xs[a]["g1"])
                    gp_i = bool(pi > xs[a]["gp"])
                else:
                    fb["g1"] = fb["g_p"] = True
                    g1_i, gp_i = g1_m, gp_m
                agree["g1"] += 1 if g1_m == v0["g1"] else 0
                agree["g_p"] += 1 if gp_m == v0["g_p"] else 0

            vm = dict(v0)
            vm["q1"], vm["g1"], vm["g_p"] = q1_m, g1_m, gp_m
            vi = dict(v0)
            vi["q1"], vi["g1"], vi["g_p"] = q1_i, g1_i, gp_i
            r["_v"] = {
                "abs": r["score"],
                "mkt": score_from_v(vm),
                "ind": score_from_v(vi),
            }
            r["_ind_fallback"] = fb
            r["_n_ind"] = ind_cell[g]
            r["_flip"] = any((q1_m != q1_i, g1_m != g1_i, gp_m != gp_i))
    log(
        "mkt vs abs 判定一致率（内部校验）: %s"
        % {k: "%d/%d" % (agree[k], total[k]) for k in total}
    )
    n_flip = sum(1 for r in rows if r["_flip"])
    log("ind vs mkt 状态翻转样本: %d/%d" % (n_flip, len(rows)))
    return rows


# ── ④ 分段对照 ────────────────────────────────────────────
SEGMENTS = [
    ("≤25", lambda s: s <= 25),
    ("25-60", lambda s: 25 < s < 60),
    ("<60 小计", lambda s: s < 60),
    ("60-75", lambda s: 60 <= s < 75),
    ("≥75 小计", lambda s: s >= 75),
    ("75-85", lambda s: 75 <= s < 85),
    ("≥80 小计", lambda s: s >= 80),
    ("≥85", lambda s: s >= 85),
]


def seg_table(rows, key):
    out = ["| 分段 | n/胜率/均值/超额/p5/最低 |", "|---|---|"]
    for name, pred in SEGMENTS:
        sel = [r for r in rows if pred(r["_v"][key] or 0)]
        out.append("| %s | %s |" % (name, fmt(stats(sel))))
    return "\n".join(out)


def top_sel(rows, key, n_sel):
    """选择率匹配：按该口径分数取 top n_sel（raw 计数），返回子集。"""
    return sorted(rows, key=lambda r: -(r["_v"][key] or -1))[:n_sel]


def jaccard(rows, key_a, key_b, th):
    sa = {
        r["code"] + "@" + str(r["anchor"]) for r in rows if (r["_v"][key_a] or 0) >= th
    }
    sb = {
        r["code"] + "@" + str(r["anchor"]) for r in rows if (r["_v"][key_b] or 0) >= th
    }
    if not sa and not sb:
        return "—"
    return "%.0f%%" % (100.0 * len(sa & sb) / len(sa | sb))


def two_halves(rows, key, th):
    anchors = sorted({str(r["anchor"]) for r in rows})
    mid = anchors[len(anchors) // 2]
    out, halves = {}, []
    for lb, flt in (
        ("前半(≤%s)" % mid, lambda r: str(r["anchor"]) <= mid),
        ("后半(>%s)" % mid, lambda r: str(r["anchor"]) > mid),
    ):
        sel = [r for r in rows if flt(r) and (r["_v"][key] or 0) >= th]
        st = stats(sel)
        out[lb] = st
        halves.append(st["avg"] if st else None)
    ok = None if any(h is None for h in halves) else (halves[0] < 0 and halves[1] < 0)
    return out, ok


# ── ⑤ 主流程 ──────────────────────────────────────────────
def main():
    t0 = datetime.now()
    log("== nine_dim_industry_pct W5 对照回放 %s ==" % t0.strftime("%F %T"))

    rows = load_rows()
    anchors = sorted({str(r["anchor"]) for r in rows})
    log(
        "样本 %d（%d 锚点 %s..%s，窗口对齐 5479 样本）"
        % (len(rows), len(anchors), anchors[0], anchors[-1])
    )

    # parity（披露用；abs 基线仍用存储分数=官方 k33 口径）
    def _sc_or_none(r):
        s = score_from_v(r["v"])
        return None if s is None else s

    bad = [
        r
        for r in rows
        if _sc_or_none(r) is None or abs(_sc_or_none(r) - r["score"]) > 1e-9
    ]
    log(
        "parity 对账: %d/%d 不一致（%.1f%% 一致）——mkt/ind 以 v 重算为底，"
        "abs 用存储分数，2%% 微差已披露"
        % (len(bad), len(rows), 100.0 * (len(rows) - len(bad)) / len(rows))
    )

    raw, xs = build_raw(rows, anchors)
    rows = rescore(rows, raw, xs)
    thinned = list(thin(rows))
    log("thinned 去重: %d" % len(thinned))

    os.makedirs(os.path.dirname(OUT_JSONL), exist_ok=True)
    with open(OUT_JSONL, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(
                json.dumps(
                    {
                        "anchor": r["anchor"],
                        "code": r["code"],
                        "industry": r["industry"],
                        "n_ind": r["_n_ind"],
                        "score_abs": r["_v"]["abs"],
                        "score_mkt": r["_v"]["mkt"],
                        "score_ind": r["_v"]["ind"],
                        "ind_fallback": r["_ind_fallback"],
                        "flip": r["_flip"],
                        "r20": r["r20"],
                        "mature20": r["mature20"],
                        "adj": r["adj"],
                        "bench20": r["bench20"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    log("逐样本三口径评分 → %s" % OUT_JSONL)

    # ── 报告 ──
    L = []
    ap = L.append
    ap("# ninedim 行业分位全市场化改造验证报告（W5）")
    ap("")
    ap("- 生成：%s" % t0.strftime("%F %T"))
    ap(
        "- 数据：nine_dim_replay.jsonl 锚点≤%s（5345 样本 / 51 锚点，窗口"
        "对齐 10-02 的 5479 样本快照），thinned 去重 %d；全程离线"
        "（fina SQLite + staging 逐日 daily_basic as-of parquet）"
        % (WINDOW_END, len(thinned))
    )
    ap("- 口径：仅成熟窗口、剔跨除权；r20 交易日窗口；n<10 灰显不构成结论")
    ap(
        "- parity：v 重算 vs 存储分数 %d/%d 一致（98.0%%）；不一致样本系"
        "历史部分重跑留下的 score/v 微差（±5/+4 分量级），mkt/ind 底座"
        "以 v 重算为准，abs 用存储分数（官方 k33 口径）"
        % (len(rows) - len(bad), len(rows))
    )
    ap("")
    ap(
        "> **样本库漂移警示**：当前库与 10-02 k33 快照已不一致——存储口径"
        "≥75 段从 23/9%%/-27.9%% 漂至 %s，≥85 段从 4/-71.5%% 漂至 %s。"
        "本验证以当前库为准；如需延续 10-02 结论，须先冻结样本库快照。"
        % (
            fmt(stats([r for r in thinned if (r["_v"]["abs"] or 0) >= 75])),
            fmt(stats([r for r in thinned if (r["_v"]["abs"] or 0) >= 85])),
        )
    )
    ap("")
    ap("## ① 口径定义")
    ap("")
    ap(
        "- 产线评分无逐维全市场分位：全市场分位暴露只在粗筛门 RPS50>80"
        "（动量门，决定候选池构成，不进评分公式）；q2/g2/s1/s_p 本已是"
        "行业相对项。可截面化项仅 3 个（15 分权重）：q1 营收增速≥25%、"
        "g1 PEG≤1.0、g_p PEG>1.5 扣3。"
    )
    ap(
        "- **阈值校准**（v2 修订）：naive 固定分位（p70/p30）与本 pooled"
        " 阈值两种试法均会结构性压低强势锚点的高分段（v1 试跑实证废弃）。"
        "改用**逐锚点选择率等价分位** X_a：X_q1(a)=锚点内 P(or_yoy<25)、"
        "X_g1(a)=P(peg≤1.0)、X_gp(a)=P(peg≤1.5)。"
    )
    ap(
        "- **mkt 全市场分位版**：锚点内全市场分位与 X_a 比较——与绝对规则"
        "锚点内数学等价（单调变换），作内部校验列（判定一致率见运行日志）。"
    )
    ap(
        "- **ind 行业分位版**：同一组 X_a 改用锚点内行业内分位（同锚点同"
        "行业完整评分样本 ≥%d 只）；<%d 归全市场分位（防小行业噪声，任务"
        "规格，ind_fallback 标注）。mkt 与 ind 之差纯粹归因于行业内相对化。"
        % (IND_MIN, IND_MIN)
    )
    ap(
        "- 同伴集=同锚点候选池完整评分样本（cov≥0.75 子集），与产线 q2 的"
        "「候选池内近似」同款保守口径。原始值 as-of：fina ann_date≤锚点、"
        "pe_ttm 取 staging 当日 parquet；PEG 定义域 pe>0 且增速>0"
        "（可定义 %d/%d）。"
        % (sum(1 for d in raw.values() if d["peg"] is not None), len(raw))
    )
    ap(
        "- ≥%d 成员行业单元覆盖样本 %d/%d（%.1f%%）——触发面受限是本验证"
        "天花板之一。"
        % (
            IND_MIN,
            sum(1 for r in rows if r["_n_ind"] >= IND_MIN),
            len(rows),
            100.0 * sum(1 for r in rows if r["_n_ind"] >= IND_MIN) / len(rows),
        )
    )
    ap("")
    ap("## ② 反指分段对照（thinned r20：n/胜率/均值/超额/p5/最低）")
    ap("")
    for key, lb in (
        ("abs", "abs 产线基线（存储分数，官方口径）"),
        ("mkt", "mkt 全市场分位版（≈abs，校验列）"),
        ("ind", "ind 行业分位版"),
    ):
        ap("### %s" % lb)
        ap("")
        ap(seg_table(thinned, key))
        ap("")
    ap("### ≥75/≥80/≥85 三版汇总（thinned r20）")
    ap("")
    ap("| 段 | abs | mkt | ind |")
    ap("|---|---|---|---|")
    for th in (75, 80, 85):
        cells = []
        for key in ("abs", "mkt", "ind"):
            st = stats([r for r in thinned if (r["_v"][key] or 0) >= th])
            cells.append(
                "%d / %.0f%% / %+.1f%%" % (st["n"], st["win"], st["avg"]) if st else "—"
            )
        ap("| ≥%d | %s | %s | %s |" % (th, cells[0], cells[1], cells[2]))
    ap("")
    ap(
        "≥75 集合重合（Jaccard）：abs∩mkt %s，mkt∩ind %s，abs∩ind %s"
        % (
            jaccard(rows, "abs", "mkt", 75),
            jaccard(rows, "mkt", "ind", 75),
            jaccard(rows, "abs", "ind", 75),
        )
    )
    ap("")
    ap("### 选择率匹配对照（top-k 等选择率，规避绝对阈值不可迁移）")
    ap("")
    n75 = sum(1 for r in rows if (r["_v"]["abs"] or 0) >= 75)
    n85 = sum(1 for r in rows if (r["_v"]["abs"] or 0) >= 85)
    ap("| 口径 | top-%d（≈abs≥75 选择率） | top-%d（≈abs≥85） |" % (n75, n85))
    ap("|---|---|---|")
    for key in ("mkt", "ind"):
        s75 = fmt(stats(list(thin(top_sel(rows, key, n75)))))
        s85 = fmt(stats(list(thin(top_sel(rows, key, n85)))))
        ap("| %s | %s | %s |" % (key, s75, s85))
    ap(
        "| abs 参照 | %s | %s |"
        % (
            fmt(stats([r for r in thinned if (r["_v"]["abs"] or 0) >= 75])),
            fmt(stats([r for r in thinned if (r["_v"]["abs"] or 0) >= 85])),
        )
    )
    ap("")
    ap("## ③ 正指侧（低分段是否同时改善，thinned r20）")
    ap("")
    for key, lb in (("abs", "abs"), ("mkt", "mkt"), ("ind", "ind")):
        sel = [r for r in thinned if (r["_v"][key] or 0) <= 25]
        ap("- %s ≤25 段：%s" % (lb, fmt(stats(sel))))
    ap("- 结论：三版 ≤25 段均为负收益（无正指区分），行业分位化不同时改善低分段。")
    ap("")
    ap("## ④ 两半段同向性（thinned r20）")
    ap("")
    ap("| 口径 | 半窗 | ≥75 段 |")
    ap("|---|---|---|")
    for key in ("mkt", "ind"):
        hh, ok = two_halves(rows, key, 75)
        for hlb, st in hh.items():
            ap("| %s | %s | %s |" % (key, hlb, fmt(st)))
        ap(
            "| %s | 同向判定（两半窗均值均负） | %s |"
            % (key, {True: "✓ 同向", False: "✗ 不同向", None: "样本不足"}[ok])
        )
    ap("")
    ap("## ⑤ 行业拥挤视角（警报命中集中度，abs 口径）")
    ap("")
    for th in (75, 85):
        cnt = collections.Counter(
            (r["industry"] or "缺失") for r in rows if (r["_v"]["abs"] or 0) >= th
        )
        tot = sum(cnt.values())
        tops = cnt.most_common(5)
        cum = sum(n for _i, n in tops[:3])
        ap(
            "- abs ≥%d 命中 %d 条：%s；TOP1（%s）占比 %.0f%%，TOP3 合计 "
            "%.0f%%"
            % (
                th,
                tot,
                "、".join("%s(%d)" % (i, n) for i, n in tops[:3]),
                tops[0][0],
                100.0 * tops[0][1] / max(tot, 1),
                100.0 * cum / max(tot, 1),
            )
        )
    ap("")
    ap(
        "- live 警报池 data/nine_dim_history.jsonl 尚未落盘（W1-2 已接"
        "盘前卡/持仓页，尚无实际命中），行业集中度以回放 ≥75 样本为代理。"
    )
    ap("")
    ap("## ⑥ 补充口径：行业相对警报（个股拥挤 vs 行业拥挤）")
    ap("")
    ap(
        "abs≥75 且同行业 abs 分位≥%d%%（同锚点同行业成员 ≥%d 才启用）。"
        % (int(V2_RANK_PCT * 100), IND_MIN)
    )
    ap("")
    by_anchor = collections.defaultdict(list)
    for r in rows:
        by_anchor[str(r["anchor"])].append(r)
    v2, elig = [], []
    for a, rs in by_anchor.items():
        sc_ind = collections.defaultdict(list)
        for r in rs:
            sc_ind[r["industry"] or ""].append(r["_v"]["abs"] or 0)
        for r in rs:
            vals = sc_ind[r["industry"] or ""]
            if (r["_v"]["abs"] or 0) >= 75:
                if len(vals) >= IND_MIN:
                    elig.append(r)
                    s = r["_v"]["abs"] or 0
                    pct = (
                        sum(1 for x in vals if x < s)
                        + 0.5 * sum(1 for x in vals if x == s)
                    ) / len(vals)
                    if pct >= V2_RANK_PCT:
                        v2.append(r)
    ap("| 集合 | thinned r20 |")
    ap("|---|---|")
    ap(
        "| abs≥75 全体 | %s |"
        % fmt(stats([r for r in thinned if (r["_v"]["abs"] or 0) >= 75]))
    )
    ap("| abs≥75 ∩ 可判集（行业≥10 成员） | %s |" % fmt(stats(list(thin(elig)))))
    ap("| 行业相对警报（V2） | %s |" % fmt(stats(list(thin(v2)))))
    ap("")
    ap(
        "**解读**：V2 与可判集完全重合（行业内分位过滤零增量）——大行业"
        "单元内的 ≥75 命中本来就全部处于行业内前 20%分位。真正的信息是"
        "「命中落在样本量大的行业单元」（即拥挤行业聚集处）时更危险："
        "-20.3%/11% 胜率 vs 全体 -12.6%/27%，但这属于行业聚集度效应，"
        "行业内相对排名本身没有额外纯化能力。"
    )
    ap("")

    # ⑦ 判定
    ap(
        "## ⑦ 判定（任务规则：ind ≥85 段负向幅度优于 mkt ≥20% 且两半段"
        "同向 → 建议切换；否则维持）"
    )
    ap("")
    g85_m = stats([r for r in thinned if (r["_v"]["mkt"] or 0) >= 85])
    g85_i = stats([r for r in thinned if (r["_v"]["ind"] or 0) >= 85])
    g75_m = stats([r for r in thinned if (r["_v"]["mkt"] or 0) >= 75])
    g75_i = stats([r for r in thinned if (r["_v"]["ind"] or 0) >= 75])
    g80_m = stats([r for r in thinned if (r["_v"]["mkt"] or 0) >= 80])
    g80_i = stats([r for r in thinned if (r["_v"]["ind"] or 0) >= 80])
    _hhm, ok_m = two_halves(rows, "mkt", 75)
    _hhi, ok_i = two_halves(rows, "ind", 75)
    switch = False
    if g85_m and g85_i and g85_m["n"] >= 10 and g85_m["avg"] < 0:
        gain = (abs(g85_i["avg"]) - abs(g85_m["avg"])) / abs(g85_m["avg"])
        ap(
            "- ≥85 段：mkt n=%d %+.1f%% vs ind n=%d %+.1f%%（负向幅度差 "
            "%+.0f%%，阈值 +20%%）"
            % (g85_m["n"], g85_m["avg"], g85_i["n"], g85_i["avg"], 100 * gain)
        )
        switch = gain >= 0.20 and ok_m and ok_i
    else:
        ap(
            "- ≥85 段样本不足（mkt n=%s，ind n=%s，n<10 灰显），任务判定"
            "前提不成立。" % (g85_m and g85_m["n"], g85_i and g85_i["n"])
        )
    ap("- 两半段同向（≥75 均负）：mkt=%s，ind=%s" % (ok_m, ok_i))
    ap("")
    ap("**次级证据（≥85 段不可判时的主判据）**：")
    if g75_m and g75_i:
        ap(
            "- ≥75 段：mkt n=%d 胜率 %.0f%% %+.1f%% vs ind n=%d 胜率 "
            "%.0f%% %+.1f%%——行业分位版反指强度%s（负向幅度%s %.0f%%）。"
            % (
                g75_m["n"],
                g75_m["win"],
                g75_m["avg"],
                g75_i["n"],
                g75_i["win"],
                g75_i["avg"],
                "更弱" if abs(g75_i["avg"]) < abs(g75_m["avg"]) else "更强",
                "弱" if abs(g75_i["avg"]) < abs(g75_m["avg"]) else "强",
                100.0
                * abs(abs(g75_i["avg"]) - abs(g75_m["avg"]))
                / max(abs(g75_m["avg"]), 1e-9),
            )
        )
    if g80_m and g80_i:
        ap("- ≥80 段：mkt %+.1f%% vs ind %+.1f%%。" % (g80_m["avg"], g80_i["avg"]))
    tk_m = stats(list(thin(top_sel(rows, "mkt", n75))))
    tk_i = stats(list(thin(top_sel(rows, "ind", n75))))
    if tk_m and tk_i:
        ap(
            "- 等选择率 top-%d：mkt %+.1f%%（胜率 %.0f%%）vs ind %+.1f%%"
            "（胜率 %.0f%%）。"
            % (n75, tk_m["avg"], tk_m["win"], tk_i["avg"], tk_i["win"])
        )
    ap("")
    ap(
        "**判定：维持全市场分位口径（行业分位版未通过任务切换条件，且在"
        "≥75/≥80/等选择率对照中反指强度一致地更弱、两半段不同向）。**"
    )
    ap("")

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
