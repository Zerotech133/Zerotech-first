# -*- coding: utf-8 -*-
"""知识图谱 Phase 3-C：图特征输出（截面因子，供九维选股等模块注入）。

特征口径（per Security 截面）：
  deg_total        直接边数（度中心性 proxy：公司/行业/区域/申万关联总数）
  deg_product      关联产品数（业务多元度）
  peer_industry    同细分行业标的数（竞争密度）
  peer_sw_l1       同申万一级行业标的数（大行业拥挤度）
  peer_region      同区域标的数（地域集聚）
  person_shared    通过共享高管关联的其他公司数（人物网络 proxy）
  peer_shared_prod 共享产品的公司数（产业链 proximity proxy）
  n_persons        关联高管人数

输出：data/features/kg_features.parquet（ts_code + 特征 + computed_at）

用法：
  python kg_features.py            # 全量计算并落盘
  python kg_features.py --top 10   # 打印各特征 Top N
"""

import datamart as dm
import argparse
import os
import sys
from datetime import datetime

import pandas as pd
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

OUT = os.path.join(HERE, "data", "features", "kg_features.parquet")


def compute():
    from kg_client import KgClient

    cli = KgClient()
    feats = {}
    q = {
        "deg_total": ("MATCH (s:Security)-[r]-() RETURN s.ts_code AS c, count(r) AS v"),
        "deg_product": (
            "MATCH (s:Security)-[:OF_COMPANY]->(:Company)"
            "-[:PRODUCES]->(p:Product) "
            "RETURN s.ts_code AS c, count(DISTINCT p) AS v"
        ),
        "peer_industry": (
            "MATCH (s:Security)-[:INDUSTRY_OF]->(i:Industry)"
            "<-[:INDUSTRY_OF]-(peer:Security) "
            "RETURN s.ts_code AS c, count(DISTINCT peer) AS v"
        ),
        "peer_sw_l1": (
            "MATCH (s:Security)-[e:SW_L1_OF]->(k:SwL1Industry)"
            "<-[e2:SW_L1_OF]-(peer:Security) "
            "WHERE e.validEnd = '9999-12-31' AND e2.validEnd = '9999-12-31' "
            "RETURN s.ts_code AS c, count(DISTINCT peer) AS v"
        ),
        "peer_region": (
            "MATCH (s:Security)-[:AREA_OF]->(a:Region)"
            "<-[:AREA_OF]-(peer:Security) "
            "RETURN s.ts_code AS c, count(DISTINCT peer) AS v"
        ),
        "person_shared": (
            # 在任兼任（G5 入历史离任边后须过滤 validEnd，
            # 否则已离任的跨公司任职会虚增兼任网络）
            "MATCH (s:Security)-[:OF_COMPANY]->(c1:Company)"
            "<-[s1:SERVES_AS]-(p:Person)-[s2:SERVES_AS]->(c2:Company) "
            "WHERE c1 <> c2 AND s1.validEnd = '9999-12-31' "
            "AND s2.validEnd = '9999-12-31' "
            "RETURN s.ts_code AS c, count(DISTINCT c2) AS v"
        ),
        "peer_shared_prod": (
            "MATCH (s:Security)-[:OF_COMPANY]->(c1:Company)"
            "-[:PRODUCES]->(p:Product)<-[:PRODUCES]-(c2:Company) "
            "WHERE c1 <> c2 "
            "RETURN s.ts_code AS c, count(DISTINCT c2) AS v"
        ),
        "n_persons": (
            # 在任高管数（同上 validEnd 过滤）
            "MATCH (s:Security)-[:OF_COMPANY]->(:Company)"
            "<-[sv:SERVES_AS]-(p:Person) "
            "WHERE sv.validEnd = '9999-12-31' "
            "RETURN s.ts_code AS c, count(DISTINCT p) AS v"
        ),
    }
    with cli._session() as s:
        for name, cy in q.items():
            rows = list(s.run(cy))
            feats[name] = {r["c"]: r["v"] for r in rows}
            print("  %-16s %d 标的" % (name, len(rows)))
    cli.close()

    df = pd.DataFrame(feats)
    df.index.name = "ts_code"
    df = df.fillna(0).astype(int).reset_index()
    df["computed_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=0, help="打印各特征 Top N")
    args = ap.parse_args()

    print("=== 知识图谱特征计算 %s ===" % datetime.now().strftime("%F %T"))
    df = compute()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    df.to_parquet(OUT, index=False)
    print("落盘: %s（%d 行 × %d 列）" % (OUT, *df.shape))

    # 历史截面归档（因子有效性检验基建：单截面覆盖写→历史永远积累不起来，
    # 真 IC 序列检验需要 T-1 截面 + 后续收益；滚动保留 400 份≈16 个月）
    hist_dir = os.path.join(HERE, "data", "features", "kg_features_hist")
    os.makedirs(hist_dir, exist_ok=True)
    snap = os.path.join(
        hist_dir,
        "kgf_%s.parquet" % str(df["computed_at"].iloc[0])[:10].replace("-", ""),
    )
    df.to_parquet(snap, index=False)  # 同日重跑幂等覆盖
    olds = sorted(p for p in os.listdir(hist_dir) if p.startswith("kgf_"))
    for p in olds[:-400]:
        try:
            os.remove(os.path.join(hist_dir, p))
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/kg_features.py", exc)

    if args.top:
        for col in [
            c for c in df.columns if c.startswith(("deg", "peer", "person", "n_"))
        ]:
            top = df.nlargest(args.top, col)[["ts_code", col]]
            basic = dm.read("ts_stock_basic", fields=["ts_code", "name"])
            top = top.merge(basic, on="ts_code", how="left")
            print("\n%s Top%d:" % (col, args.top))
            print(top.to_string(index=False))


if __name__ == "__main__":
    from linreg import run_with_lineage

    run_with_lineage("kg_daily_sync", "kg_features", main)
