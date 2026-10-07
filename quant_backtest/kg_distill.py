# -*- coding: utf-8 -*-
"""M5 蒸馏准备：样本资产化（先攒后训，训练本身 C 类缓行）。

样本单元 = 一条 HAS_STRUCTURE 边（标的 × 信号日 × 结构类型），三重标签：
· 形态标签 shape   = 结构类型 + 质量档（q_struct >=0.8 强 / >=0.5 中 / 弱）
· 置信标签 conf    = M2 confidence 三因子（q_struct/q_evid/q_hm）+ 档位
· 根因标签 verdict = ACH 规则近似版批量打分：
    s1 = min(1, h1n/4)                          # 信号前10日实质主题 DRIVE 边数
    s2 = min(1, max(0,pre10)/2e8 + (pre10>0)/10 + is_fc*0.25)
    s3 = nd==0 ? 1.0 : 0.4                      # 该信号自身无任何事件佐证→博弈
  → H1_event / H2_funds / H3_game / insufficient
  （kg_trace.ach 同源口径的批量近似，逐样本跑全 ACH 不现实；口径透明可解释）
监督信号（ret5/ret10/ret20/pre10/post10）join M2-W4c 校准存档
  data/semantica/qhm_calibration.csv（key: tc+d0+kind）。
盘口特征（Batch 4 联动）：cd_d0 信号当日盘口事件 + cd_seal5/cd_broken5/
  cd_late5 前5自然日（含当日）封板/炸板/尾盘类计数（HAS_CANDLE）。
G3 负样本（对照组）：匹配配对法——每正样本 (tc,d0) 配 2 个同标的
  干净无信号日（d0±[4,30] 交易日窗口，距任意信号 ≥4 交易日），特征与
  正样本同构（nvol10/h1n/cd_*），label=0；kg_distill_neg.parquet。
  stats 的配对溢价（正-负同标的差分）回答"信号日 vs 自身无信号日"
  ——市场 beta 之外的结构信号增量首次可度量。

用法：
  python kg_distill.py build          # 全量样本 → kg_distill.parquet + 统计 json
  python kg_distill.py neg            # 仅重建负样本对照组（kg_distill_neg.parquet）
  python kg_distill.py stats          # 统计面板（按标签分布 + 对照组溢价）
  python kg_distill.py export TC D0   # 单样本子图 JSON（信号±10日窗口）→ stdout 或 --out
  python kg_distill.py verify [N]     # 分层抽样 N 个（默认12）：Semantica 读取验证
"""
import sys
import os
import json
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd

from kg_client import KgClient
from kg_semantica import export_subgraph

HERE = os.path.dirname(os.path.abspath(__file__))
DISTILL_DIR = os.path.join(HERE, "data", "distill")
PARQUET = os.path.join(DISTILL_DIR, "kg_distill.parquet")
PARQUET_NEG = os.path.join(DISTILL_DIR, "kg_distill_neg.parquet")
STATS_JSON = os.path.join(DISTILL_DIR, "distill_stats.json")
CALIB_CSV = os.path.join(HERE, "data", "semantica", "qhm_calibration.csv")

from kg_trace import BASELINE_GRP  # notice 基线组（H1 不计）


def _iso(ymd):
    return "%s-%s-%s" % (str(ymd)[:4], str(ymd)[4:6], str(ymd)[6:8])


def _shift(ymd, days):
    d = datetime.strptime(str(ymd), "%Y%m%d") + timedelta(days=days)
    return d.strftime("%Y%m%d")


def _load_daily_closes():
    """日线池 → {tc: (dates, closes)}（ts_daily part 分片，close 列）。"""
    import glob
    parts = glob.glob(os.path.join(HERE, "data", "tdx", "ts_daily",
                                   "part_*.parquet"))
    if not parts:
        return {}
    daily = pd.concat([pd.read_parquet(p, columns=["ts_code", "trade_date",
                                                   "close"]) for p in parts])
    daily["trade_date"] = daily["trade_date"].astype(str)
    daily = daily.sort_values(["ts_code", "trade_date"])
    return {tc: (g["trade_date"].tolist(), g["close"].tolist())
            for tc, g in daily.groupby("ts_code")}


def _candle_feats(lst, d0):
    """盘口特征（HAS_CANDLE type@date 列表 → 当日类型 + 三类计数）。
    窗口=前5自然日含当日；单日口径互斥至多一事件：cd_d0=当日类型；
    三类计数=资金进场(封)/出货(炸)/博弈(尾盘) 痕迹。"""
    d0t, s, b, l = None, 0, 0, 0
    for it in (lst or []):
        t, _, d = it.partition("@")
        if d == str(d0):
            d0t = t
        if t in ("early_seal", "seal", "late_seal"):
            s += 1
        elif t == "broken":
            b += 1
        else:
            l += 1
    return (d0t, s, b, l)


def _rets_at(grp, tc, d0, offs=(5, 10, 20)):
    """close[d0+k交易日]/close[d0]-1；d0 不在池或越界 → None（滞后诚实）。"""
    gd = grp.get(tc)
    if not gd:
        return (None,) * len(offs)
    dates, closes = gd
    try:
        i = dates.index(str(d0))
    except ValueError:
        return (None,) * len(offs)
    return tuple(closes[i + k] / closes[i] - 1 if i + k < len(closes)
                 else None for k in offs)


# ── W1 子图封装器：标的+信号日±窗口 → Semantica 兼容 JSON ──
def sample_subgraph(cli, tc, sig_date, pre=10, post=10):
    """信号样本子图：M4.5 导出器全量导出后按时间窗剪枝。

    无时间边（INDUSTRY_OF 等分类边 valid_from=None）视为长期活跃保留；
    时序边与 [sig-pre, sig+post] 相交才保留；孤立节点清除（标的自身常驻）。
    """
    d0, d1 = _iso(_shift(sig_date, -pre)), _iso(_shift(sig_date, post))
    g = export_subgraph(cli, tc, hops=2, close=False)
    rels = []
    for r in g["relationships"]:
        vf, vu = r["valid_from"], r["valid_until"]
        if vf is not None and vf > d1:
            continue
        if vu is not None and vu < d0:
            continue
        rels.append(r)
    keep = set()
    for r in rels:
        keep.add(r["source"])
        keep.add(r["target"])
    ents = [e for e in g["entities"]
            if e["id"] in keep or e.get("ts_code") == tc]
    return {"meta": {"ts_code": tc, "signal_date": str(sig_date),
                     "window": [d0, d1],
                     "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M")},
            "entities": ents, "relationships": rels}


# ── W2+W3 全量样本构建：三重标签 + 监督信号 → parquet ──
def build(cli):
    t0 = time.time()
    print("── 拉取全量样本（HAS_STRUCTURE 边 + ACH 特征）──")
    cy = (
        "MATCH (sec:Security)-[r:HAS_STRUCTURE]->(m:MarketEvent) "
        "RETURN sec.ts_code AS tc, sec.name AS nm, m.date AS d0, "
        "m.type AS kind, r.confidence AS conf, r.q_struct AS qs, "
        "r.q_evid AS qe, r.q_hm AS qh, r.hm_tag AS tag, "
        "r.post_hm10 AS post, "
        "size([(e:Event)-[d:DRIVE]->(m2)<-[:HAS_STRUCTURE|HAS_VOL_EVENT]"
        "-(sec) WHERE d.validStart <= m.date AND "
        "duration.inDays(date(d.validStart), date(m.date)).days <= 10 "
        "AND NOT coalesce(d.group,'') IN $base | 1]) AS h1n, "
        "size([()-[:DRIVE]->(m) | 1]) AS nd, "
        "size([(sec)-[:HAS_VOL_EVENT]->(v:MarketEvent) WHERE v.date <= "
        "m.date AND duration.inDays(date(v.date), date(m.date)).days "
        "<= 10 | 1]) AS nvol, "
        "size([(sec)-[:HAS_STRUCTURE]->(w:MarketEvent) WHERE w.date <= "
        "m.date AND duration.inDays(date(w.date), date(m.date)).days "
        "<= 10 | 1]) AS nsig, "
        "size([(sec)-[:HAS_STRUCTURE]->(w2:MarketEvent) WHERE w2.date <= "
        "m.date AND duration.inDays(date(w2.date), date(m.date)).days "
        "<= 10 AND NOT ()-[:DRIVE]->(w2) | 1]) AS noev, "
        "[(sec)-[:HAS_CANDLE]->(c:MarketEvent) WHERE c.date <= m.date "
        "AND duration.inDays(date(c.date), date(m.date)).days <= 5 "
        "| c.type + '@' + toString(c.date)] AS cds "
        "ORDER BY tc, d0")
    rows = [dict(r) for r in cli._session().run(cy, base=list(BASELINE_GRP))]
    print("  样本边 %d 条（%.0fs）" % (len(rows), time.time() - t0))
    df = pd.DataFrame(rows)

    calib = pd.read_csv(CALIB_CSV, dtype={"tc": str, "d0": str})
    # 校准存档 kind 列为统一值 chanlun（实验口径），监督信号为标的日级：
    # 同日多信号共享，drop_duplicates 后按 (tc,d0) join
    calib = calib.drop_duplicates(["tc", "d0"])
    df["d0"] = df["d0"].astype(str)
    df = df.merge(calib[["tc", "d0", "pre10", "ret5", "ret10", "ret20"]],
                  on=["tc", "d0"], how="left")
    hit = df["ret5"].notna().sum()
    print("  join 校准存档（监督信号）：命中 %d/%d" % (hit, len(df)))

    # 监督信号补算（W5 攒样闭环）：CSV 为静态存档，每日新增样本 join 不到；
    # ret5/ret10/ret20 从日线池补算（口径与存档逐位核对一致：
    # close[d0+k交易日]/close[d0]-1）。pre10 为资金净流入额，仅存档有，不补。
    miss = df["ret5"].isna() & df["tc"].notna()
    if miss.any():
        grp = _load_daily_closes()
        if grp:
            got = [_rets_at(grp, r["tc"], r["d0"]) for _, r in df[miss].iterrows()]
            for j, c in enumerate(("ret5", "ret10", "ret20")):
                # None→NaN：pandas 对 float64 列 setitem 含 None 会
                # raise（Invalid value for dtype），显式转 float 保平稳
                df.loc[miss, c] = [g[j] if g[j] is not None else float("nan")
                                   for g in got]
            print("  日线池补算监督信号：补 %d（命中 ret5 %d）"
                  % (int(miss.sum()),
                     int(df.loc[miss, "ret5"].notna().sum())))

    # 盘口特征（Batch 4 HAS_CANDLE 并入；窗口=前5自然日含当日，与 pre10 同口径）
    # 口径见模块级 _candle_feats
    _cf = [_candle_feats(r["cds"], r["d0"]) for _, r in df.iterrows()]
    df["cd_d0"] = [x[0] for x in _cf]
    df["cd_seal5"] = [x[1] for x in _cf]
    df["cd_broken5"] = [x[2] for x in _cf]
    df["cd_late5"] = [x[3] for x in _cf]
    df = df.drop(columns=["cds"])
    print("  盘口特征并入：当日事件 %d / 窗口命中 %d"
          % (df["cd_d0"].notna().sum(),
             (df[["cd_seal5", "cd_broken5", "cd_late5"]].sum(axis=1) > 0)
             .sum()))

    # 根因标签（ACH 规则近似版，口径见模块 docstring）
    def verdict(row):
        s1 = min(1.0, (row["h1n"] or 0) / 4.0)
        pre10 = row.get("pre10")
        pre10 = 0 if pd.isna(pre10) else pre10
        s2 = min(1.0, max(0.0, pre10) / 2e8
                 + (1 if pre10 > 0 else 0) / 10.0
                 + (0.25 if row["tag"] == "follow_confirm" else 0.0))
        # kg_trace 原式同构：量能丰富度 × 无佐证占比
        s3 = min(1.0, (row["nvol"] or 0) / 8.0) * (
            0.5 + 0.5 * ((row["noev"] or 0) / max(1, row["nsig"] or 1)))
        scores = {"H1_event": s1, "H2_funds": s2, "H3_game": s3}
        top = max(scores.values())
        if top <= 0:
            return "insufficient"
        best = max(scores, key=scores.get)
        return best if scores[best] > 0 else "insufficient"

    df["verdict"] = df.apply(verdict, axis=1)
    # 三重标签
    df["shape"] = df["kind"] + "|" + df["qs"].fillna(0).apply(
        lambda q: "强" if q >= 0.8 else ("中" if q >= 0.5 else "弱"))
    df["conf_band"] = df["conf"].fillna(0).apply(
        lambda c: "高" if c >= 0.6 else ("中" if c >= 0.4 else "低"))

    os.makedirs(DISTILL_DIR, exist_ok=True)
    df.to_parquet(PARQUET, index=False)
    # G3 负样本：对照组同链保鲜（幂等全量重建，正样本 parquet 结构不动）
    neg = build_negatives(cli, df)
    stats = summarize(df, neg)
    with open(STATS_JSON, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)
    print("  → %s（%d 行）+ %s（%.0fs）"
          % (PARQUET, len(df), STATS_JSON, time.time() - t0))
    return df


# ── G3 负样本（无信号对照组）：匹配配对法，正:负 = 1:2 ──
# 每个正样本 (tc,d0) 配 2 个同标的"干净无信号日"（d0 前/后窗口各一），
# 条件：该日无 HAS_STRUCTURE 边、与最近信号日间隔 ≥4 交易日（隔离结构
# 确认窗口，避免边缘重叠）、日线在池（监督信号可算）。特征与正样本同构
# （nvol10/h1n/cd_* 自然日口径一致），label=0。pre10 仅校准存档有，
# 负样本恒缺失——语义自洽（无信号日无资金佐证存档）。
NEG_GAP_TD = 4     # 与最近信号日的最小交易日间隔
NEG_WIN_TD = 30    # 候选窗口半径（交易日）


def _pick_neg(dates, axis, sig_axis_idx, i, lo, hi):
    """在轴索引 [i+lo, i+hi] 内找最靠近 i 的干净日（无信号且距最近
    信号 ≥NEG_GAP_TD）；dates=该标的自有交易日集合，axis=全市场交易日轴。"""
    step = 1 if lo < hi else -1
    for j in range(i + lo, i + hi + 1, step):
        if not (0 <= j < len(axis)):
            continue
        d = axis[j]
        if d not in dates:
            continue
        if any(abs(j - sj) < NEG_GAP_TD for sj in sig_axis_idx):
            continue
        return d
    return None


def build_negatives(cli, pos):
    t0 = time.time()
    tcs = sorted(pos["tc"].dropna().unique())
    print("── G3 负样本：对照组配对（%d 标的）──" % len(tcs))
    s = cli._session()

    def _collect(rel, proj):
        m = {}
        cy = ("MATCH (sec:Security)-[:%s]->(m:MarketEvent) "
              "WHERE sec.ts_code IN $tcs RETURN sec.ts_code AS tc, "
              "%s AS items" % (rel, proj))
        for r in s.run(cy, tcs=tcs):
            m[r["tc"]] = r["items"]
        return m

    sig = {tc: set(str(x) for x in v)
           for tc, v in _collect("HAS_STRUCTURE",
                                 "collect(DISTINCT m.date)").items()}
    vol = {tc: [str(x) for x in v]
           for tc, v in _collect("HAS_VOL_EVENT",
                                 "collect(DISTINCT m.date)").items()}
    cds = _collect("HAS_CANDLE",
                   "collect(m.type + '@' + toString(m.date))")
    print("  图谱侧映射：信号 %d / 量能 %d / 盘口 %d（%.0fs）"
          % (len(sig), len(vol), len(cds), time.time() - t0))

    # h1n 同构：实质组 DRIVE（非基线组）驱动日列表，按标的。
    # 方向：sec-[:HAS_*]->m<-[:DRIVE]-(e)（两条边都指向 MarketEvent）
    drv = {}
    cy = ("MATCH (sec:Security)-[:HAS_STRUCTURE|HAS_VOL_EVENT]->(m)"
          "<-[d:DRIVE]-(e:Event) WHERE sec.ts_code IN $tcs "
          "RETURN sec.ts_code AS tc, toString(d.validStart) AS vd, "
          "coalesce(d.group,'') AS grp")
    for r in s.run(cy, tcs=tcs):
        drv.setdefault(r["tc"], []).append(
            (str(r["vd"]).replace("-", ""), r["grp"]))
    print("  DRIVE 映射：%d 标的（%.0fs）" % (len(drv), time.time() - t0))

    closes = _load_daily_closes()
    axis = sorted(set(d for ds, _ in closes.values() for d in ds))
    axis_idx = {d: i for i, d in enumerate(axis)}

    base = list(BASELINE_GRP)

    def _day_diff(a, b):
        return abs((datetime.strptime(a, "%Y%m%d")
                    - datetime.strptime(b, "%Y%m%d")).days)

    def _ctx(tc, x):
        """负样本上下文特征（自然日口径与正样本 Cypher 一致）。"""
        nvol = sum(1 for d in vol.get(tc, []) if d <= x and _day_diff(x, d) <= 10)
        h1 = sum(1 for vd, g in drv.get(tc, [])
                 if g not in base and vd <= x and _day_diff(x, vd) <= 10)
        cd = _candle_feats(cds.get(tc), x)
        return nvol, h1, cd

    pos_u = pos.drop_duplicates(["tc", "d0"])[["tc", "d0"]]
    rows = []
    n_pre = n_post = 0
    for tc, d0 in pos_u.itertuples(index=False):
        gd = closes.get(tc)
        if not gd or d0 not in gd[0]:
            continue
        dates = set(gd[0])
        sigs = sorted(sig.get(tc, []))
        sig_axis = [axis_idx[d] for d in sigs if d in axis_idx]
        i = axis_idx[d0]
        for side, lo, hi in (("pre", -NEG_WIN_TD, -NEG_GAP_TD),
                             ("post", NEG_GAP_TD, NEG_WIN_TD)):
            x = _pick_neg(dates, axis, sig_axis, i, lo, hi)
            if not x:
                continue
            nvol, h1, cd = _ctx(tc, x)
            r5, r10, r20 = _rets_at(closes, tc, x)
            rows.append({"tc": tc, "d0": x, "side": side, "ref_d0": d0,
                         "label": 0, "nvol10": nvol, "h1n": h1,
                         "cd_d0": cd[0], "cd_seal5": cd[1],
                         "cd_broken5": cd[2], "cd_late5": cd[3],
                         "ret5": r5 if r5 is not None else float("nan"),
                         "ret10": r10 if r10 is not None else float("nan"),
                         "ret20": r20 if r20 is not None else float("nan")})
            if side == "pre":
                n_pre += 1
            else:
                n_post += 1
    neg = pd.DataFrame(rows)
    neg.to_parquet(PARQUET_NEG, index=False)
    print("  → %s（%d 行：pre %d / post %d / 信号日覆盖 %d/%d，%.0fs）"
          % (PARQUET_NEG, len(neg), n_pre, n_post,
             neg["ref_d0"].nunique() if len(neg) else 0,
             pos_u["d0"].nunique(), time.time() - t0))
    return neg


def summarize(df, neg=None):
    vc = lambda col: df[col].value_counts().to_dict()
    pivot = df.pivot_table(index="kind", columns="verdict",
                           values="tc", aggfunc="count",
                           fill_value=0).to_dict()
    out = {
        "n": int(len(df)),
        "n_securities": int(df["tc"].nunique()),
        "date_min": str(df["d0"].min()), "date_max": str(df["d0"].max()),
        "n_with_ret5": int(df["ret5"].notna().sum()),
        "verdict": {k: int(v) for k, v in vc("verdict").items()},
        "conf_band": {k: int(v) for k, v in vc("conf_band").items()},
        "hm_tag": {k: int(v) for k, v in df["tag"].fillna("none")
                   .value_counts().items()},
        "kind": {k: int(v) for k, v in vc("kind").items()},
        "kind_x_verdict": pivot,
        "mean_ret5_by_verdict": df.groupby("verdict")["ret5"].mean()
        .round(4).mul(100).to_dict(),
        "cd_d0": {k: int(v) for k, v in df["cd_d0"].fillna("none")
                  .value_counts().items()},
        "cd_pre5_hit": int((df[["cd_seal5", "cd_broken5", "cd_late5"]]
                            .sum(axis=1) > 0).sum()),
        "mean_cd_by_verdict": {v: {"seal5": round(g["cd_seal5"].mean(), 3),
                                   "broken5": round(g["cd_broken5"].mean(), 3),
                                   "late5": round(g["cd_late5"].mean(), 3)}
                               for v, g in df.groupby("verdict")},
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    if neg is not None and len(neg):
        # 配对差分（同标的同市场期）：正样本 ret5 - 对照 ret5
        # by verdict（根因）× side + by kind（买卖方向语义不同，须分看：
        # 卖类信号 ret5 为负=方向正确，混看会把"有效"读成"负 alpha"）
        base = df.drop_duplicates(["tc", "d0"])[
            ["tc", "d0", "verdict", "kind", "ret5"]] \
            .rename(columns={"d0": "ref_d0"})
        pj = base.merge(neg[["tc", "ref_d0", "side", "ret5"]],
                        on=["tc", "ref_d0"], how="inner")
        pj["diff"] = pj["ret5_x"] - pj["ret5_y"]

        def _prem(keys):
            g = pj.groupby(keys + ["side"])["diff"].mean() \
                .mul(100).round(2)
            return {"%s|%s" % k: float(v) for k, v in g.items()}

        out["neg"] = {
            "n": int(len(neg)),
            "n_pre": int((neg["side"] == "pre").sum()),
            "n_post": int((neg["side"] == "post").sum()),
            "covered_sig_days": int(neg["ref_d0"].nunique()),
            "mean_ret5_neg": {k: round(v * 100, 2)
                              for k, v in
                              neg.groupby("side")["ret5"].mean().items()},
            "premium_by_verdict": _prem(["verdict"]),
            "premium_by_kind": _prem(["kind"]),
        }
    return out


def stats():
    if not os.path.exists(PARQUET):
        print("样本库不存在，先执行 build")
        return
    with open(STATS_JSON, encoding="utf-8") as f:
        s = json.load(f)
    print("── M5 蒸馏样本库统计 ──")
    print("  样本 %d 条 / 标的 %d 个 / 时段 %s~%s / 带监督信号(ret5) %d"
          % (s["n"], s["n_securities"], s["date_min"], s["date_max"],
             s["n_with_ret5"]))
    for k in ("verdict", "conf_band", "hm_tag"):
        print("  %-9s %s" % (k, " ".join("%s=%d" % (a, b)
                                         for a, b in s[k].items())))
    print("  ret5 均值(%%) 按根因: %s"
          % " ".join("%s %+.1f" % (a, b)
                     for a, b in s["mean_ret5_by_verdict"].items()))
    print("  盘口特征：当日 %s / 前5日窗口命中 %d"
          % (" ".join("%s=%d" % (a, b) for a, b in s["cd_d0"].items()),
             s["cd_pre5_hit"]))
    print("  盘口计数均值 按根因: %s"
          % " ".join("%s 封%.2f/炸%.2f/尾%.2f"
                     % (v, c["seal5"], c["broken5"], c["late5"])
                     for v, c in s["mean_cd_by_verdict"].items()))
    print("  kind×verdict（行=结构类型）:")
    kinds = s["kind_x_verdict"]  # {verdict: {kind: n}}
    cols = sorted(kinds.keys())
    print("    %-12s %s" % ("", " ".join("%11s" % c for c in cols)))
    for kind in s["kind"]:
        print("    %-12s %s"
              % (kind, " ".join("%11d" % kinds[c].get(kind, 0)
                                for c in cols)))
    n = s.get("neg")
    if not n:
        print("  对照组：未生成（build 时自动构建）")
        return
    print("  ── G3 对照组（匹配配对，正:负 = 1:%.1f）──" % (
        n["n"] / max(1, s["n"])))
    print("    负样本 %d（pre %d / post %d，覆盖信号日 %d）"
          % (n["n"], n["n_pre"], n["n_post"], n["covered_sig_days"]))
    print("    对照 ret5 均值(%%)：pre %+.2f / post %+.2f"
          % (n["mean_ret5_neg"].get("pre", 0),
             n["mean_ret5_neg"].get("post", 0)))
    for title, key in (("根因", "premium_by_verdict"),
                       ("类型（买卖方向须分看）", "premium_by_kind")):
        print("    配对溢价 ret5(%%) 按%s（正-负，同标的差分）:" % title)
        for k in sorted(n[key]):
            print("      %-22s %+6.2f" % (k, n[key][k]))


# ── W4 格式验证：分层抽样 → Semantica TemporalGraphQuery 读取 ──
def verify(cli, n=12):
    df = pd.read_parquet(PARQUET)
    strat = []
    for v, grp in df.groupby("verdict"):
        strat += grp.head(max(1, n // df["verdict"].nunique()))[
            ["tc", "d0", "verdict"]].values.tolist()
    strat = strat[:n]
    print("── W4 格式验证：%d 个分层样本（%s）──"
          % (len(strat), "/".join(df["verdict"].value_counts().index)))
    from semantica.kg.temporal_query import TemporalGraphQuery
    ok = 0
    for tc, d0, v in strat:
        g = sample_subgraph(cli, tc, d0)
        tq = TemporalGraphQuery(temporal_granularity="day")
        if not hasattr(tq, "_relationship_key"):
            # 同 kg_semantica：semantica 0.7.0 包内 bug 的 monkey-patch
            tq._relationship_key = lambda r: "%s|%s|%s" % (
                r.get("source", ""),
                r.get("type", r.get("relationship", "")),
                r.get("target", ""))
        res = tq.query_at_time(g, "", at_time=_iso(d0))
        rep = tq.validate_temporal_consistency(g)
        st = "✓" if rep.errors == [] else "✗"
        ok += rep.errors == []
        print("  %s %s %s [%s] 实体%d 边%d → @%s 活跃%d/%d err=%d warn=%d"
              % (st, tc, d0, v, len(g["entities"]),
                 len(g["relationships"]), _iso(d0),
                 res["num_entities"], res["num_relationships"],
                 len(rep.errors), len(rep.warnings)))
    print("  通过 %d/%d → %s"
          % (ok, len(strat),
             "格式全部可读，可作蒸馏输入" if ok == len(strat) else "存在失败样本"))


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return
    if a[0] == "stats":
        stats()
        return
    cli = KgClient(retry=1)
    try:
        if a[0] == "build":
            build(cli)
        elif a[0] == "neg":
            pos = pd.read_parquet(PARQUET)
            neg = build_negatives(cli, pos)
            with open(STATS_JSON, encoding="utf-8") as f:
                s = json.load(f)
            s["neg"] = summarize(pos, neg).get("neg")
            with open(STATS_JSON, "w", encoding="utf-8") as f:
                json.dump(s, f, ensure_ascii=False, indent=1)
            stats()
        elif a[0] == "export":
            g = sample_subgraph(cli, a[1], a[2])
            out = a[a.index("--out") + 1] if "--out" in a else None
            js = json.dumps(g, ensure_ascii=False, indent=1)
            if out:
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with open(out, "w", encoding="utf-8") as f:
                    f.write(js)
                print("→ %s（实体 %d / 边 %d）"
                      % (out, len(g["entities"]),
                         len(g["relationships"])))
            else:
                print(js)
        elif a[0] == "verify":
            verify(cli, int(a[1]) if len(a) > 1 else 12)
        else:
            print(__doc__)
    finally:
        cli.close()


if __name__ == "__main__":
    main()
