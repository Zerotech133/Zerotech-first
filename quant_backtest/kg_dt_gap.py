# -*- coding: utf-8 -*-
"""榜后差额资金穿透夜批（KG-GAP P2-①）。

口径（2026-10-01 探测定案，53,509 行实证）：
· 榜后差额 gap = l_buy - l_sell（== net_amount，全表 0 误差恒等）
  = 龙虎榜买方前五合计 - 卖方前五合计，单位元；
· gap_ratio = gap / 当日总成交额 amount（失衡强度归一；预演：gap_ratio
  前 25% 组次日胜率 59.6% vs 全体 56.7%，单调有效）；
· 席位性质：ts_top_inst 明细按词表分类（优先级 youzi > quant > org >
  other；youzi 复用 kg_build_cl1.YOUZI，retail_cluster → retail）：
    quant  市场高共识量化席位（华鑫上海分 4,280 行/1,965 票、
           国信浙江互联网 3,459/781、东亚前海上海 1,123/665——
           高频复访同票，量化打板行为指纹）
    org    机构通道（机构专用/沪深股通/总部/外资行/中金上海/中信上海，
           口径对齐 kg_communities.UNREG_EXCL）
  每笔按五类净额（youzi/quant/org/retail/other）记录；
  dom_nature = |净额| 最大类；quant_ratio = quant 净额/|gap|；
  覆盖 93.8%（缺 BJ 2,772 + SH 567 行无席位明细，性质字段 null）；
· 次日穿透（K14 同口径，tdx ts_daily）：
    ret1   次日收盘收益 / oret1 次日开盘跳空（vs 上榜日收盘）
    pen    = 1 if gap 与 ret1 同向 else 0（榜后差额是否打进次日价格；
            最近一交易日事件 ret1 未到，pen=null 次日补齐）

写图（全量重算幂等，MERGE on evt_id；词表/口径更新自动回填）：
  MarketEvent(kind=topgap)  evt_id = ts_code:topgap:yyyymmdd
  Security -[HAS_TOPGAP]-> MarketEvent（新边型；MATCH 池外票静默跳过）

挂载：start_kg_sync.ps1 步骤 dt_gap（seat_winrate 后、kg_features 前，
守"图写入先于特征重算"纪律——deg_total 计入任意边型）。
展示：kg_portal 个股页 topgaps 块 + hm_profile 榜后差额段。

用法：
  python kg_dt_gap.py            # 全量重算入图
  python kg_dt_gap.py --dryrun   # 只算不写
"""
import argparse
import re
from datetime import datetime

import pandas as pd

import kg_schema as S
import tdx_read as tdxr
from kg_client import KgClient
import linreg  # X3 血缘登记（best-effort，不阻断）

# ── 量化席位词表 v1（市场高共识，regex 匹配 ts_top_inst.exalter）──
QUANT_SEATS = (
    r"^华鑫证券有限责任公司上海分公司$",
    r"^国信证券股份有限公司浙江互联网分公司$",
    r"^东亚前海证券有限责任公司上海分公司$",
)
ORG_PAT = ("机构专用|沪股通|深股通|总部|高盛|瑞银|摩根大通|"
           "中国国际金融股份有限公司上海分公司|"
           "中信证券股份有限公司上海分公司")
NATURES = ("youzi", "quant", "org", "retail", "other")


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _r2(v):
    return None if v is None or pd.isna(v) else round(float(v), 2)


def _r4(v):
    return None if v is None or pd.isna(v) else round(float(v), 4)


def _int(v):
    return None if v is None or pd.isna(v) else int(v)


def _nature_map(seats):
    """席位名列表 → {席位: 性质}。优先级 youzi > quant > org > other。"""
    from kg_build_cl1 import YOUZI
    yz = [(re.compile(p), kind) for _, pats, kind in YOUZI for p in pats]
    rx_q = re.compile("|".join(QUANT_SEATS))
    rx_o = re.compile(ORG_PAT)
    out = {}
    for s in seats:
        nat = None
        for rx, kind in yz:
            if rx.search(s):
                nat = "retail" if kind == "retail_cluster" else "youzi"
                break
        if nat is None:
            nat = "quant" if rx_q.search(s) else (
                "org" if rx_o.search(s) else "other")
        out[s] = nat
    return out


def calc(verbose=True):
    """返回 (evs, edges, stats)，不写库。"""
    tl = tdxr.read_range("ts_top_list")
    if tl.empty:
        raise SystemExit("ts_top_list 为空，先跑 TdxSync")
    ti = tdxr.read_range("ts_top_inst", columns=[
        "ts_code", "trade_date", "exalter", "net_buy"])
    if verbose:
        print("  ts_top_list %d 行（%s~%s）· ts_top_inst %d 行"
              % (len(tl), tl["trade_date"].min(), tl["trade_date"].max(),
                 len(ti)))

    # ── 席位性质分类 + (票,日) 五类净额 ──
    nmap = _nature_map(sorted(ti["exalter"].unique()))
    ti["nat"] = ti["exalter"].map(nmap)
    agg = ti.groupby(["ts_code", "trade_date", "nat"])["net_buy"].sum() \
            .unstack("nat", fill_value=0.0)
    for c in NATURES:
        if c not in agg.columns:
            agg[c] = 0.0
    agg = agg[list(NATURES)].reset_index()
    na = agg[list(NATURES)].abs()
    agg["dom_nature"] = na.idxmax(axis=1).where(na.sum(axis=1) > 0, None)
    if verbose:
        dist = pd.Series(nmap).value_counts().to_dict()
        print("  席位分类 %d 个（%s）" % (len(nmap), dist))

    # ── 榜单合并：gap / gap_ratio / 性质字段 ──
    tl["trade_date"] = tl["trade_date"].astype(str)
    m = tl.merge(agg, on=["ts_code", "trade_date"], how="left")
    m["gap"] = m["net_amount"]
    m["gap_ratio"] = (m["gap"] / m["amount"]).where(m["amount"] > 0)
    m["quant_ratio"] = (m["quant"] / m["gap"].abs()).where(m["gap"] != 0)

    # ── 次日穿透（ts_daily，K14 同口径）──
    dd = tdxr.read_range("ts_daily", codes=set(tl["ts_code"]),
                         columns=["ts_code", "trade_date", "close", "open"])
    dd["trade_date"] = dd["trade_date"].astype(str)
    dd = dd.sort_values(["ts_code", "trade_date"])
    g = dd.groupby("ts_code")
    dd["ret1"] = g["close"].shift(-1) / dd["close"] - 1.0
    dd["oret1"] = g["open"].shift(-1) / dd["close"] - 1.0
    m = m.merge(dd[["ts_code", "trade_date", "ret1", "oret1"]],
                on=["ts_code", "trade_date"], how="left")
    ok = m["ret1"].notna() & m["gap"].notna() & (m["gap"] != 0)
    m["pen"] = None
    m.loc[ok, "pen"] = ((m.loc[ok, "gap"] > 0)
                        == (m.loc[ok, "ret1"] > 0)).astype(int)
    if verbose:
        v = m[ok]
        hi = v[v["gap_ratio"] >= v["gap_ratio"].quantile(0.75)]
        print("  穿透：有效 %d 笔 · pen 胜率 %.1f%% · gap_ratio 前25%%组 "
              "%.1f%% · 主导分布 %s"
              % (len(v), 100.0 * v["pen"].astype(float).mean(),
                 100.0 * hi["pen"].astype(float).mean(),
                 v["dom_nature"].value_counts().head(4).to_dict()))

    # ── 事件行（to_dict 防止 itertuples 改名列陷阱，kg_bclass 判例）──
    evs, edges = [], []
    ts = _now()
    for r in m.to_dict("records"):
        tc, d = r["ts_code"], r["trade_date"]
        eid = "%s:topgap:%s" % (tc, d)
        reason = (r.get("reason") or "").strip()
        evs.append({
            "evt_id": eid, "kind": "topgap", "date": d,
            "name": r.get("name"), "close": _r2(r.get("close")),
            "pct_chg": _r2(r.get("pct_change")), "amount": _r2(r.get("amount")),
            "gap": _r2(r.get("gap")), "l_buy": _r2(r.get("l_buy")),
            "l_sell": _r2(r.get("l_sell")),
            "gap_ratio": _r4(r.get("gap_ratio")),
            "reason": reason[:64] if reason else None,
            "yz_net": _r2(r.get("youzi")), "qt_net": _r2(r.get("quant")),
            "og_net": _r2(r.get("org")), "rt_net": _r2(r.get("retail")),
            "ot_net": _r2(r.get("other")),
            "dom_nature": r.get("dom_nature"),
            "quant_ratio": _r4(r.get("quant_ratio")),
            "ret1": _r4(r.get("ret1")), "oret1": _r4(r.get("oret1")),
            "pen": _int(r.get("pen")),
            "source": "ts_top_list", "updated_at": ts})
        edges.append({"src": tc, "dst": eid, "validStart": d,
                      "validEnd": S.VALID_END_OPEN, "source": "ts_top_list",
                      "updated_at": ts})

    v = m[ok]
    hi = v[v["gap_ratio"] >= v["gap_ratio"].quantile(0.75)]
    stats = ("topgap %d 事件/边（%s~%s）· 性质覆盖 %.1f%% · pen 有效 %d "
             "胜率 %.1f%% · gap_ratio 前25%%组 %.1f%% · 量化主导 %d 笔"
             % (len(evs), tl["trade_date"].min(), tl["trade_date"].max(),
                100.0 * m["dom_nature"].notna().mean(), len(v),
                100.0 * v["pen"].astype(float).mean(),
                100.0 * hi["pen"].astype(float).mean(),
                int((v["dom_nature"] == "quant").sum())))
    return evs, edges, stats


def main():
    _jr = linreg.JobRun("kg_daily_sync", "dt_gap").__enter__()  # X3 血缘
    ap = argparse.ArgumentParser()
    ap.add_argument("--dryrun", action="store_true")
    a = ap.parse_args()
    print("[dt_gap] 榜后差额资金穿透重算（ts_top_list × top_inst × ts_daily）")
    evs, edges, stats = calc()
    if a.dryrun:
        print("  dryrun：跳过写库")
        print("  " + stats)
        _jr.finish("ok")  # X3 血缘（显式收尾）
        return
    cli = KgClient()
    try:
        # OPS-1004 降噪：只保留图内 Security 存在的标的。可转债/B股/
        # 北交所/未入图次新上榜时 upsert_edge 的 src 匹配不到 → 边被
        # 静默跳过 → 孤儿事件天天触发 kg_check orphan_evt 红项
        # （消费端 kg_portal/sector_flow 均经 Security 关联，缺边事件
        # 本就不可见，过滤不丢信息；次新在 Security 补建后随窗口补入）。
        with cli._session() as s:
            have = set(x["c"] for x in s.run(
                "MATCH (sec:Security) RETURN sec.ts_code AS c").data())
        n0 = len(evs)
        evs = [e for e in evs if e["evt_id"].split(":")[0] in have]
        edges = [e for e in edges if e["src"] in have]
        if n0 - len(evs):
            print("  过滤非 Security 标的 %d 条（转债/B股/北交所/次新）"
                  % (n0 - len(evs)))
        n1 = cli.upsert_nodes(S.LBL_MARKET_EVENT, ["evt_id"], evs)
        n2 = cli.upsert_edge(S.LBL_SECURITY, "ts_code", S.LBL_MARKET_EVENT,
                             "evt_id", S.REL_HAS_TOPGAP, edges)
        with cli._session() as s:
            n = s.run("MATCH ()-[r:%s]->() RETURN count(r) AS c"
                      % S.REL_HAS_TOPGAP).single()["c"]
    finally:
        cli.close()
    print("  写入 MarketEvent %d / HAS_TOPGAP 边 %d（图内现存 %d）"
          % (n1, n2, n))
    print("  " + stats)
    # X3 血缘：榜后差额穿透 → HAS_TOPGAP 边
    _jr.edge("neo4j:HAS_TOPGAP", "parquet:ts_top_list", transform="penetration")  # X3
    _jr.rows_written = n1 + n2  # X3
    _jr.finish("ok")  # X3 血缘（显式收尾）


if __name__ == "__main__":
    main()
