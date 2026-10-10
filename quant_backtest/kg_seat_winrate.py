# -*- coding: utf-8 -*-
"""席位历史胜率夜批（KG-GAP P1-2，K13 后续）。

口径：TRADED_ON 上榜日净买方向 × 次一交易日收盘收益
  dir  = sign(net_buy)          （净买>0 看多 / <0 看空 / =0 剔除）
  ret1 = 次一交易日 close/close - 1（tdx ts_daily，T+1 无未来函数）
  win  = dir * ret1 > 0
衡量的是"上榜行为对次日方向的判断力"，非真实盈亏（成交价未知）。

写回（幂等，每跑全量重算）：
  Seat     : wr_n / wr_win_rate / wr_avg_ret / wr_updated
  HotMoney : 同口径主体级聚合（经 OCCUPIES 汇总其全部席位笔级数据）

挂载：start_kg_sync.ps1 步骤 seat_winrate（bclass_sync 后、kg_features 前，
守"图写入先于特征重算"纪律）。展示：hm_profile 摘要 + KG-V6 _prof 弹窗。
"""
from datetime import datetime

import pandas as pd

import kg_schema as S
import tdx_read as tdxr
from kg_client import KgClient
import linreg  # X3 血缘登记（best-effort，不阻断）


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def calc(verbose=True):
    """返回 (seat_rows, hm_rows, stats_str)，不写库。"""
    cli = KgClient()
    try:
        with cli._session() as s:
            td = pd.DataFrame([dict(r) for r in s.run(
                "MATCH (st:Seat)-[t:TRADED_ON]->(sec:Security) "
                "RETURN st.name AS seat, sec.ts_code AS tc, "
                "t.validStart AS d, t.net_buy AS nb")])
            occ = [dict(r) for r in s.run(
                "MATCH (h:HotMoney)-[:OCCUPIES]->(st:Seat) "
                "RETURN h.name AS hm, st.name AS seat")]
    finally:
        cli.close()
    if td.empty:
        raise SystemExit("TRADED_ON 为空，先跑 kg_build_cl1.py")
    if verbose:
        print("  TRADED_ON %d 笔（%s~%s）· 席位 %d · 主体映射 %d"
              % (len(td), td["d"].min(), td["d"].max(),
                 td["seat"].nunique(), len(occ)))

    # ── 次日收益：ts_daily 全覆盖龙虎榜窗口（列裁剪 ts_code/trade_date/close）
    dd = tdxr.read_range(
        "ts_daily", start=td["d"].min(),
        codes=set(td["tc"]), columns=["ts_code", "trade_date", "close"])
    if dd.empty:
        raise SystemExit("ts_daily 为空，先跑 tdx 同步")
    dd["d"] = dd["trade_date"].astype(str).str[:8]
    dd = dd.sort_values(["ts_code", "d"])
    dd["ret1"] = (dd.groupby("ts_code")["close"].shift(-1)
                  / dd["close"] - 1.0)
    if verbose:
        print("  ts_daily %d 行（%s~%s）· 涉及 %d 票"
              % (len(dd), dd["d"].min(), dd["d"].max(),
                 dd["ts_code"].nunique()))

    # ── 笔级对齐（左连接，无次日/停牌缺 ret1 自然剔除）
    td = td[td["nb"] != 0].copy()
    td["dir"] = (td["nb"] > 0).astype(int) * 2 - 1
    td = td.merge(dd[["ts_code", "d", "ret1"]],
                  left_on=["tc", "d"], right_on=["ts_code", "d"],
                  how="left")
    miss = int(td["ret1"].isna().sum())
    td = td[td["ret1"].notna()].copy()
    td["win"] = td["dir"] * td["ret1"] > 0
    td["dr"] = td["dir"] * td["ret1"]
    if verbose:
        print("  有效笔 %d（剔除 net=0/无次日 %d）· 整体胜率 %.1f%%"
              % (len(td), miss, 100.0 * td["win"].mean()))

    # ── Seat 聚合
    g = td.groupby("seat").agg(n=("win", "size"), wins=("win", "sum"),
                               dr=("dr", "mean"))
    ts = _now()
    seat_rows = [{"name": k, "wr_n": int(r.n),
                  "wr_win_rate": round(float(r.wins) / r.n, 3),
                  "wr_avg_ret": round(float(r.dr), 4), "wr_updated": ts}
                 for k, r in g.iterrows()]

    # ── HotMoney 主体聚合（席位→主体汇总笔级）
    s2h = {r["seat"]: r["hm"] for r in occ}
    td["hm"] = td["seat"].map(s2h)
    gh = td[td["hm"].notna()].groupby("hm").agg(
        n=("win", "size"), wins=("win", "sum"), dr=("dr", "mean"))
    hm_rows = [{"name": k, "wr_n": int(r.n),
                "wr_win_rate": round(float(r.wins) / r.n, 3),
                "wr_avg_ret": round(float(r.dr), 4), "wr_updated": ts}
               for k, r in gh.iterrows()]

    g2 = g.assign(rate=g["wins"] / g["n"]).sort_values(
        "rate", ascending=False)
    top = ["%s %.0f%%(%d)" % (k, 100 * r.rate, r.n)
           for k, r in g2.head(3).iterrows()]
    stats = ("席位胜率 %d 席（笔级 %d 有效/%d 总）· 主体 %d · "
             "整体 %.1f%% · top3: %s"
             % (len(seat_rows), len(td), len(td) + miss, len(hm_rows),
                100.0 * td["win"].mean(), "; ".join(top)))
    return seat_rows, hm_rows, stats


def main():
    _jr = linreg.JobRun("kg_daily_sync", "seat_winrate").__enter__()  # X3 血缘
    print("[seat_winrate] 席位历史胜率重算（上榜净买×次日收益）")
    seat_rows, hm_rows, stats = calc()
    cli = KgClient()
    try:
        n1 = cli.upsert_nodes(S.LBL_SEAT, ["name"], seat_rows)
        n2 = cli.upsert_nodes(S.LBL_HOTMONEY, ["name"], hm_rows)
    finally:
        cli.close()
    print("  写回 Seat %d · HotMoney %d" % (n1, n2))
    print("  " + stats)
    # X3 血缘：上榜净买×次日收益 → 胜率属性回写
    _jr.edge("neo4j:TRADED_ON.win_rate", "neo4j:TRADED_ON", transform="winrate")  # X3
    _jr.edge("neo4j:TRADED_ON.win_rate", "parquet:tdx/ts_daily", transform="winrate")  # X3
    _jr.rows_written = n1 + n2  # X3
    _jr.finish("ok")  # X3 血缘（显式收尾，异常路径靠 __del__ 记 lost）


if __name__ == "__main__":
    main()
