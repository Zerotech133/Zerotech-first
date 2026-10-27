# -*- coding: utf-8 -*-
"""知识图谱 Phase 2-B ETL：申万一级行业归属时序边建图。

数据流：
  sw_l1_member/*.parquet (31 个 L1 指数的成分股, 7740 行)
    → Security -[SW_L1_OF {validStart=in_date, validEnd=out_date/9999}]-> SwL1Industry

时序图谱铁律：
  - validStart = 成分股进入日期 in_date
  - validEnd = 退出日期 out_date（在市/未退出 → 9999-12-31）
  - MERGE 键 (src, dst, validStart) 允许同一标的在不同时段归属不同 L1
    （如某股曾属"综合"后调入"计算机"）

数据源：tushare index_member 接口（31 次 API 调用，已落盘
  data/tushare/sw_l1_member/<index_code>.parquet，断点续传）

注意：SwL1Industry 节点已在 Phase 1 建好（511 个，含 L1/L2/L3 全部），
本脚本只建时序边，不重建节点。

用法：
  python kg_build_p2_sw.py             # 全量建图（幂等可重跑）
  python kg_build_p2_sw.py --dryrun    # 只打印不落库
  python kg_build_p2_sw.py --sample 688411.SH   # 抽检（含 SW_L1_OF）
"""

import argparse
import glob
import os
import sys
from datetime import datetime

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import kg_schema as S


def _now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _safe_str(v):
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if isinstance(v, (list, tuple)):
        return "; ".join(str(x) for x in v)
    s = str(v).strip()
    return s or None


def extract_sw_l1_edges():
    """从 sw_l1_member/*.parquet 构建 SW_L1_OF 时序边。

    Returns: list[{src, dst, validStart, validEnd, source, updated_at, is_new}]
    """
    now = _now()
    in_dir = os.path.join(HERE, "data", "tushare", "sw_l1_member")
    files = sorted(glob.glob(os.path.join(in_dir, "*.parquet")))
    if not files:
        raise SystemExit("sw_l1_member 目录无 parquet，先拉数据")

    edges = []
    stats = {"files": 0, "rows": 0, "skip_no_in": 0}
    for fp in files:
        df = pd.read_parquet(fp)
        stats["files"] += 1
        stats["rows"] += len(df)
        # 从文件名还原 index_code（801010_SI.parquet → 801010.SI）
        ic = os.path.basename(fp).replace(".parquet", "").replace("_", ".")
        for _, r in df.iterrows():
            ts_code = _safe_str(r.get("con_code"))
            in_date = _safe_str(r.get("in_date"))
            out_date = _safe_str(r.get("out_date"))
            is_new = _safe_str(r.get("is_new"))
            if not ts_code or not in_date:
                stats["skip_no_in"] += 1
                continue
            valid_end = out_date if out_date else S.VALID_END_OPEN
            edges.append(
                {
                    "src": ts_code,
                    "dst": ic,  # SwL1Industry.index_code
                    "validStart": in_date,
                    "validEnd": valid_end,
                    "is_new": is_new or "Y",  # Y=在市 N=已退出
                    "source": "tushare.index_member",
                    "updated_at": now,
                }
            )
    print(
        "  扫描 %d 文件, %d 行, 跳过 %d, 生成边 %d"
        % (stats["files"], stats["rows"], stats["skip_no_in"], len(edges))
    )
    return edges


def build(dryrun=False):
    print("=== 知识图谱 Phase 2-B 申万一级归属 %s ===" % _now())
    # 1) 抽取边
    print("[1/3] 抽取 SW_L1_OF 时序边…")
    edges = extract_sw_l1_edges()
    # 在市 vs 退市统计
    in_mkt = sum(1 for e in edges if e["validEnd"] == S.VALID_END_OPEN)
    out_mkt = len(edges) - in_mkt
    print("  在市边: %d  退出边: %d  合计: %d" % (in_mkt, out_mkt, len(edges)))

    if dryrun:
        print("DRYRUN 抽样 5 条边:")
        for e in edges[:5]:
            print(
                "  %s -[%s, %s, is_new=%s]-> %s"
                % (e["src"], e["validStart"], e["validEnd"], e["is_new"], e["dst"])
            )
        print("DRYRUN 结束。")
        return

    # 2) 落库
    print("[2/3] 连接 Neo4j 落库…")
    from kg_client import KgClient

    cli = KgClient()
    cli.ensure_schema()

    n_e = cli.upsert_edge(
        S.LBL_SECURITY, "ts_code", S.LBL_SW_L1, "index_code", S.REL_SW_L1_OF, edges
    )
    print("  SW_L1_OF upsert: %d" % n_e)

    # 3) 统计
    print("[3/3] 统计…")
    cli.stats()
    cli.close()
    print("=== Phase 2-B 完成 %s ===" % _now())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dryrun", action="store_true")
    ap.add_argument("--sample", help="抽检某 ts_code 子图（含 SW_L1_OF）")
    args = ap.parse_args()

    if args.sample:
        from kg_client import KgClient

        cli = KgClient()
        cli.sample_subgraph(args.sample)
        cli.close()
        return

    build(dryrun=args.dryrun)


if __name__ == "__main__":
    main()
