# -*- coding: utf-8 -*-
"""知识图谱 Phase 3-A ETL：跨市场联动（MarketIndex + CORRELATES_WITH）。

数据流：
  ts_index_daily (A股宽基7) + index_global (全球20)
    → MarketIndex 节点（27 个，含 market 归属）
    → CORRELATES_WITH 边（季度窗口相关系数 + Beta + 外盘隔夜滞后相关）

联动口径：
  · 日收益率 = close.pct_change()（两源统一，避免 pct_chg 口径差异）
  · 同日对齐：内连接共同交易日（跨时区市场自然只保留重叠日）
  · leadLag 两个属性：corr0=同日相关；corrLag1=外盘 T-1 收益 vs 本市场 T 收益
    （捕捉"隔夜传导"：美股昨夜 → A股今日）
  · 窗口：按季度切片（2018Q1→今），窗口内重叠样本 nObs>=40 才建边
  · 方向约定：src→dst 按代码字典序（对称关系只存一条）；
    beta = cov(src,dst)/var(dst)（src 对 dst 的 Beta）

时序图谱铁律：边带 validStart(=窗口季初)/validEnd(=窗口季末)。

用法：
  python kg_build_p3.py             # 全量建图（幂等可重跑）
  python kg_build_p3.py --dryrun    # 只打印不落库
  python kg_build_p3.py --sample    # 抽检联动边（SPX/沪深300）
"""
import argparse
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import kg_schema as S
import datamart as dm


def _now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


# ── 指数清单（code → 名称/市场归属） ─────────────────────────
A_IDX = {
    "000001.SH": ("上证指数", "A股"),
    "399001.SZ": ("深证成指", "A股"),
    "399006.SZ": ("创业板指", "A股"),
    "000300.SH": ("沪深300", "A股"),
    "000905.SH": ("中证500", "A股"),
    "000688.SH": ("科创50", "A股"),
    "000852.SH": ("中证1000", "A股"),
}
GLOBAL_IDX = {
    "HSI":       ("恒生指数", "港股"),
    "HKTECH":    ("恒生科技", "港股"),
    "SPX":       ("标普500", "美股"),
    "DJI":       ("道琼斯", "美股"),
    "IXIC":      ("纳斯达克", "美股"),
    "RUT":       ("罗素2000", "美股"),
    "N225":      ("日经225", "亚太"),
    "KS11":      ("韩国KOSPI", "亚太"),
    "TWII":      ("台湾加权", "亚太"),
    "XIN9":      ("新加坡海峡", "亚太"),
    "AS51":      ("澳洲ASX200", "亚太"),
    "CKLSE":     ("马来西亚KLSE", "亚太"),
    "GDAXI":     ("德国DAX", "欧股"),
    "FTSE":      ("英国富时100", "欧股"),
    "FCHI":      ("法国CAC40", "欧股"),
    "SENSEX":    ("印度SENSEX", "新兴"),
    "RTS":       ("俄罗斯RTS", "新兴"),
    "SPTSX":     ("加拿大SPTSX", "美洲"),
    "IBOVESPA":  ("巴西BOVESPA", "新兴"),
    "HKAH":      ("港股AH溢价", "港股"),
}

# 外盘市场（T-1 收盘影响 A股 T 交易日）：美股/欧股相对 A股隔夜
OVERSEAS = {"SPX", "DJI", "IXIC", "RUT", "GDAXI", "FTSE", "FCHI"}


def load_returns():
    """读两源指数 close → pivot 成 date×code 日收益率矩阵（%）。"""
    frames = []
    # A股宽基
    idx = dm.read("index_daily", fields=["ts_code", "trade_date", "close"])
    idx = idx[idx["ts_code"].isin(A_IDX)]
    frames.append(idx)
    # 全球指数（2026-10-16 收编：dm.read 读层替代逐文件 glob；文件内含
    # ts_code 列，身份不再依赖文件名）
    gidx = dm.read("index_global", fields=["ts_code", "trade_date", "close"])
    frames.append(gidx[gidx["ts_code"].isin(GLOBAL_IDX)])
    px = pd.concat(frames, ignore_index=True)
    # 去重（同日同码取最后）+ pivot
    px = px.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
    mat = px.pivot(index="trade_date", columns="ts_code", values="close").sort_index()
    ret = mat.pct_change() * 100.0
    return ret


def quarter_windows(dates):
    """交易日序列 → [(q_start, q_end, [dates]), ...] 季度切片。"""
    s = pd.Series(sorted(dates))
    yq = s.str[:4].astype(int) * 10 + (s.str[4:6].astype(int) - 1) // 3 + 1
    out = []
    for (y, q), g in s.groupby([yq // 10, yq % 10]):
        out.append(("%04d%02d01" % (y, (q - 1) * 3 + 1), g.iloc[-1], g.tolist()))
    return out


def build_corr_edges(ret, min_obs=40):
    """收益率矩阵 → 季度窗口 CORRELATES_WITH 边。

    对每对指数 (a,b)（a<b 字典序）每个季度：
      corr0    = 同日相关系数
      beta0    = cov(a,b)/var(b)
      corrLag1 = b 为外盘时 shift(1) 的相关（外盘昨夜 vs a 今日）；
                 a 为外盘时反向同理；都不是外盘则 None
    """
    codes = sorted(ret.columns)
    wins = quarter_windows(ret.index)
    now = _now()
    edges = []
    for ws, we, dates in wins:
        sub = ret.loc[dates]
        sub_lag = sub.shift(1)          # T-1 值（对齐"外盘昨夜→今日"）
        for i in range(len(codes)):
            for j in range(i + 1, len(codes)):
                a, b = codes[i], codes[j]
                both = sub[[a, b]].dropna()
                if len(both) < min_obs:
                    continue
                va, vb = both[a], both[b]
                if va.std() < 1e-9 or vb.std() < 1e-9:
                    continue
                corr0 = va.corr(vb)
                beta0 = va.cov(vb) / vb.var()
                # 隔夜滞后相关：外盘 T-1 vs 本市场 T
                corr_lag1 = None
                if b in OVERSEAS and a not in OVERSEAS:
                    pair = pd.concat([sub_lag[b], sub[a]], axis=1).dropna()
                    if len(pair) >= min_obs:
                        corr_lag1 = pair.iloc[:, 0].corr(pair.iloc[:, 1])
                elif a in OVERSEAS and b not in OVERSEAS:
                    pair = pd.concat([sub_lag[a], sub[b]], axis=1).dropna()
                    if len(pair) >= min_obs:
                        corr_lag1 = pair.iloc[:, 0].corr(pair.iloc[:, 1])
                e = {
                    "src": a, "dst": b,
                    "validStart": ws, "validEnd": we,
                    "corr0": round(float(corr0), 4),
                    "beta0": round(float(beta0), 4),
                    "nObs": int(len(both)),
                    # 统一携带（None→删除属性），保证 upsert SET 列模板完整
                    "corrLag1": None,
                    "source": "index_global+ts_index_daily", "updated_at": now,
                }
                if corr_lag1 is not None and not np.isnan(corr_lag1):
                    e["corrLag1"] = round(float(corr_lag1), 4)
                edges.append(e)
    return edges


def build(dryrun=False):
    print("=== 知识图谱 Phase 3-A 跨市场联动开始 %s ===" % _now())
    print("[1/4] 读收益率矩阵…")
    ret = load_returns()
    print("  指数 %d 个 × %d 交易日 (%s→%s)"
          % (ret.shape[1], len(ret), ret.index[0], ret.index[-1]))

    print("[2/4] MarketIndex 节点…")
    nodes = []
    for code, (name, mkt) in {**A_IDX, **GLOBAL_IDX}.items():
        if code in ret.columns:
            nodes.append({"code": code, "name": name, "market": mkt,
                          "source": "index_global+ts_index_daily"})
    print("  MarketIndex: %d" % len(nodes))

    print("[3/4] 季度窗口 CORRELATES_WITH 边…")
    edges = build_corr_edges(ret)
    # 只保留最近 3 年窗口（2018 起数据老，联动意义弱；保留 2023Q4 起）
    edges = [e for e in edges if e["validStart"] >= "20230101"]
    print("  CORRELATES_WITH: %d 条（2023Q1 起，nObs>=40）" % len(edges))
    if edges:
        df_e = pd.DataFrame(edges)
        latest = df_e[df_e["validEnd"] == df_e["validEnd"].max()]
        top = latest.reindex(latest["corr0"].abs().sort_values(ascending=False).index)
        print("  最新窗口 |corr| Top5:")
        for _, r in top.head(5).iterrows():
            print("    %s ↔ %s corr0=%.3f beta0=%.2f n=%d"
                  % (r["src"], r["dst"], r["corr0"], r["beta0"], r["nObs"]))

    if dryrun:
        print("DRYRUN 结束，未连接 Neo4j。")
        return

    print("[4/4] 落库…")
    from kg_client import KgClient
    cli = KgClient()
    cli.ensure_schema()
    n = cli.upsert_nodes(S.LBL_MARKET_INDEX, ["code"], nodes)
    print("  MarketIndex upsert: %d" % n)
    # upsert_edge 的 MERGE 键是 validStart；CORRELATES_WITH 各属性动态 SET
    ne = cli.upsert_edge(S.LBL_MARKET_INDEX, "code", S.LBL_MARKET_INDEX,
                         "code", S.REL_CORRELATES, edges)
    print("  CORRELATES_WITH upsert: %d" % ne)

    cli.stats()
    cli.close()
    print("=== Phase 3-A 完成 %s ===" % _now())


def sample():
    """抽检：沪深300 最新窗口的域外市场联动 + 纳指隔夜传导时序。"""
    from kg_client import KgClient
    cli = KgClient()
    with cli._session() as s:
        ve = s.run("MATCH ()-[r:CORRELATES_WITH]->() "
                   "RETURN max(r.validEnd) AS m").single()["m"]
        print("=== 沪深300 最新窗口（%s）域外市场联动 ===" % ve)
        rows = list(s.run(
            "MATCH (a:MarketIndex {code:'000300.SH'})-[r:CORRELATES_WITH]->(b:MarketIndex) "
            "WHERE r.validEnd = $ve AND b.market IN ['美股','欧股','港股','亚太'] "
            "RETURN b.code AS other, b.name AS nm, r.corr0 AS c, "
            "r.corrLag1 AS lag, r.nObs AS n "
            "ORDER BY abs(coalesce(r.corrLag1, r.corr0)) DESC LIMIT 10", ve=ve))
        for r in rows:
            print("  <- %-9s %-8s corr0=%7.3f lag1=%s n=%d" % (
                r["other"], r["nm"], r["c"] or 0,
                ("%7.3f" % r["lag"]) if r["lag"] is not None else "      -",
                r["n"]))

        print("=== 沪深300 x 纳指 时序演变（lag1=隔夜传导）===")
        rows = list(s.run(
            "MATCH (a:MarketIndex {code:'000300.SH'})-[r:CORRELATES_WITH]->"
            "(b:MarketIndex {code:'IXIC'}) "
            "RETURN r.validStart AS vs, r.corr0 AS c, r.corrLag1 AS lag "
            "ORDER BY vs"))
        for r in rows:
            print("  %s corr0=%7.3f lag1=%s" % (
                r["vs"], r["c"],
                ("%7.3f" % r["lag"]) if r["lag"] is not None else "      -"))
    cli.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dryrun", action="store_true")
    ap.add_argument("--sample", action="store_true")
    args = ap.parse_args()
    if args.sample:
        sample()
        return
    build(dryrun=args.dryrun)


if __name__ == "__main__":
    main()
