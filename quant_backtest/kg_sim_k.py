# -*- coding: utf-8 -*-
"""K线形态相似夜批（KG-GAP P2-②）。

口径：60 交易日收盘对数路径 x_t = ln(p_t/p_0)（起点归零消量级，
保留形态路径）；窗口内不足 60 个交易日的票（次新/长停）跳过。
数据源 tdx ts_daily；池 = 图内 Security ∩ ts_daily（保证边端点在图）。

两级相似计算（全对暴力 C(5494,2)≈1500 万对×O(60²) 不可行）：
  1) 欧氏预筛 GEMM 矩阵化（D2 = ||x||²+||y||²-2x·y 一次矩阵乘），
     每票取欧氏最近 top-20 候选——做召回够用，做排序不够
  2) Sakoe-Chiba 带宽 DTW（|i-j|<=7，60×15=900 步/对）候选精算，
     带约束防"首对尾"病态扭曲；不引 dtaidistance，numpy/纯列表自算
  附 corr：窗口内对数路径 Pearson（DTW 低=形态像，corr 高=节奏同步）。

写回（Security-[SIMILAR_K]->Security，无向语义、字典序定向防双向重复）：
  属性 dist/corr/win_start/win_end，validStart=win_end（窗口推进生成
  新快照边、旧边保留=历史相似可回溯），MERGE on (src,dst,validStart)
  同窗口重跑幂等；每票 top-5。

挂载：start_kg_sync.ps1 步骤 sim_k（dt_gap 后、kg_features 前，
守"图写入先于特征重算"纪律）。展示：kg_portal.stock 个股页相似块 +
席位交集（形态相似+同席位近期双碰=手法复现线索）。
"""
import sys
import time
from datetime import datetime, timedelta

import numpy as np

import kg_schema as S
import tdx_read as tdxr
from kg_client import KgClient
import linreg  # X3 血缘登记（best-effort，不阻断）

WIN = 60          # 特征窗口（交易日）
TOP_CAND = 20     # 欧氏预筛每票候选数
TOP_K = 5         # 每票相似边数
BAND = 7          # Sakoe-Chiba 带宽 |i-j|<=BAND
LOOKBACK_DAYS = 130   # ts_daily 回读自然日（~88 交易日，留余量）


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _dtw(a, b, w=BAND):
    """Sakoe-Chiba 带宽 DTW，返回累计距离（纯列表实现，~1ms/对）。"""
    n = len(a)
    inf = float("inf")
    prev = [0.0] + [inf] * n          # D[0][j]
    for i in range(1, n + 1):
        cur = [inf] * (n + 1)
        lo = max(1, i - w)
        hi = min(n, i + w)
        ai = a[i - 1]
        for j in range(lo, hi + 1):
            c = ai - b[j - 1]
            if c < 0:
                c = -c
            m = prev[j]
            m2 = prev[j - 1]
            if m2 < m:
                m = m2
            m3 = cur[j - 1]
            if m3 < m:
                m = m3
            cur[j] = c + m
        prev = cur
    return prev[n]


def _corr(a, b):
    """对数路径 Pearson（向量化）。"""
    a = a - a.mean()
    b = b - b.mean()
    den = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / den) if den > 0 else 0.0


def calc(verbose=True):
    """返回 (edge_rows, stats_str)，不写库。"""
    t0 = time.time()
    # ── 池：图内 Security（保证 SIMILAR_K 端点在图）∩ ts_daily
    cli = KgClient()
    try:
        with cli._session() as s:
            secs = [dict(r) for r in s.run(
                "MATCH (n:Security) RETURN n.ts_code AS tc, n.name AS nm")]
    finally:
        cli.close()
    in_graph = {r["tc"] for r in secs}

    start = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    dd = tdxr.read_range("ts_daily", start=start,
                         columns=["ts_code", "trade_date", "close"])
    if dd.empty:
        raise SystemExit("ts_daily 为空，先跑 tdx 同步")
    dd = dd[dd["ts_code"].isin(in_graph)]
    dd["d"] = dd["trade_date"].astype(str).str[:8]
    dd = dd.sort_values(["ts_code", "d"])

    # ── 每票最近 WIN 个交易日；不足 WIN 跳过（次新/长停）
    cnt = dd.groupby("ts_code").size()
    keep = cnt[cnt >= WIN].index
    dd = dd[dd["ts_code"].isin(keep)]
    win = dd.groupby("ts_code", group_keys=False).tail(WIN)
    win["seq"] = win.groupby("ts_code").cumcount()

    piv = win.pivot(index="ts_code", columns="seq", values="close")
    codes = list(piv.index)
    P = piv.values                            # (N, WIN) 收盘价
    X = np.log(P / P[:, [0]])                 # 对数路径，起点归零
    win_start = win.groupby("ts_code")["d"].min().to_dict()
    win_end = win.groupby("ts_code")["d"].max().to_dict()
    if verbose:
        print("  池 %d 票（图内 %d，ts_daily 满窗 %d/%d）· 窗口示例 %s~%s"
              % (len(codes), len(in_graph), len(keep), len(cnt),
                 min(win_start.values()), max(win_end.values())))

    # ── 第一级：欧氏预筛（GEMM 矩阵化，float32 省半内存）
    Xf = X.astype(np.float32)
    sq = (Xf * Xf).sum(axis=1)
    D2 = sq[:, None] + sq[None, :] - 2.0 * (Xf @ Xf.T)
    np.fill_diagonal(D2, np.float32(np.inf))
    D2[D2 < 0] = 0.0
    cand = np.argpartition(D2, TOP_CAND, axis=1)[:, :TOP_CAND]
    del D2
    pairs = set()
    for i in range(len(codes)):
        for j in cand[i]:
            j = int(j)
            pairs.add((i, j) if i < j else (j, i))
    if verbose:
        print("  欧氏预筛：每票 top-%d → 去重候选对 %d（%.1fs）"
              % (TOP_CAND, len(pairs), time.time() - t0))

    # ── 第二级：带宽 DTW 精算 + Pearson corr
    t1 = time.time()
    res = {}
    for (i, j) in pairs:
        res[(i, j)] = (_dtw(X[i], X[j]), _corr(X[i], X[j]))
    if verbose:
        print("  DTW 精算 %d 对完成（%.1fs，约 %.2f ms/对）"
              % (len(pairs), time.time() - t1,
                 1000.0 * (time.time() - t1) / max(len(pairs), 1)))

    # ── 每票 top-K → 字典序定向出边（同对同窗只留一条）
    best = {}
    for (i, j), (d, c) in res.items():
        best.setdefault(i, []).append((d, c, j))
        best.setdefault(j, []).append((d, c, i))
    ts = _now()
    edge_map = {}
    for i, lst in best.items():
        lst.sort(key=lambda t: t[0])
        for d, c, j in lst[:TOP_K]:
            ca, cb = codes[i], codes[j]
            if ca > cb:
                ca, cb = cb, ca
            we = max(win_end[ca], win_end[cb])
            edge_map[(ca, cb, we)] = {
                "src": ca, "dst": cb, "validStart": we,
                "validEnd": S.VALID_END_OPEN,
                "dist": round(d, 4), "corr": round(c, 3),
                "win_start": min(win_start[ca], win_start[cb]),
                "win_end": we,
                "source": "ts_daily+euclid20+dtw_band7",
                "updated_at": ts}
    edge_rows = list(edge_map.values())
    dv = sorted(r["dist"] for r in edge_rows)
    cv = [r["corr"] for r in edge_rows]
    stats = ("K线相似 %d 票 · 候选 %d 对 · 边 %d（去重后）· "
             "dist 中位 %.3f / p90 %.3f · corr>0.5 占比 %.1f%% · "
             "总耗时 %.0fs"
             % (len(codes), len(pairs), len(edge_rows),
                dv[len(dv) // 2], dv[int(len(dv) * 0.9)],
                100.0 * sum(1 for c in cv if c > 0.5) / max(len(cv), 1),
                time.time() - t0))
    return edge_rows, stats


def main():
    _jr = linreg.JobRun("kg_daily_sync", "sim_k").__enter__()  # X3 血缘
    dry = "--dryrun" in sys.argv
    print("[sim_k] K线形态相似重算（60日对数路径 欧氏预筛+带宽DTW）")
    edge_rows, stats = calc()
    print("  " + stats)
    if dry:
        print("  [dryrun] 跳过写库，示例边：")
        for r in edge_rows[:3]:
            print("    %s -> %s dist=%.3f corr=%.2f win=%s~%s"
                  % (r["src"], r["dst"], r["dist"], r["corr"],
                     r["win_start"], r["win_end"]))
        _jr.finish("ok")  # X3 血缘（显式收尾）
        return
    cli = KgClient()
    try:
        n = cli.upsert_edge(S.LBL_SECURITY, "ts_code", S.LBL_SECURITY,
                            "ts_code", S.REL_SIMILAR_K, edge_rows)
    finally:
        cli.close()
    print("  写回 SIMILAR_K %d 边" % n)
    # X3 血缘：60 日对数路径 DTW 相似 → SIMILAR_K 边
    _jr.edge("neo4j:SIMILAR_K", "parquet:tdx/ts_daily", transform="dtw")  # X3
    _jr.rows_written = n  # X3
    _jr.finish("ok")  # X3 血缘（显式收尾）


if __name__ == "__main__":
    main()
