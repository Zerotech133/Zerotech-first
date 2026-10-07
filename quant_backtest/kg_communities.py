# -*- coding: utf-8 -*-
"""席位社区发现（G4：游资集团识别 + watchlist 候选）。

图构建：同日同标的上榜的席位对连边（共现权重=共同(标的,日)数），
w>=2 过滤单次噪音；Louvain 社区划分（networkx，seed 固定可复现）。

席位池（G4b 影子扩容）：词表席位（OCCUPIES 反查）+ ts_top_inst 高频
上榜未注册席位 top N（排除散户/拉萨/机构专用/沪深股通通道）。扩容席位
只进本模块分析层（影子），不写图、不改 CL-1 的 H2/画像口径——watchlist
产出即"新马甲候选"，人工审核后入 youzi_registry 词表（影子先行纪律）。
共现对与席位活跃度直读 ts_top_inst parquet（单底座单读层），注册映射
读图 OCCUPIES。

输出（data/kg_communities.json）：
  communities  按规模排序——id/name(注册游资名优先)/members/n_seats/
               n_pairs/w_total/members top
  watchlist    含注册游资的社区中未注册高共现席位 top10
  sensitivity  阈值敏感性——(days × min_w) 网格的社区数/模块度（build --sens）

用法：
  python kg_communities.py build            # 重建（默认 365 天窗口）
  python kg_communities.py build 90
  python kg_communities.py build --sens     # 附带敏感性网格
  python kg_communities.py stats
"""
import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from datetime import datetime, timedelta
from itertools import combinations

import networkx as nx

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tdx_read as tdxr  # noqa: E402
from kg_client import KgClient  # noqa: E402

OUT = os.path.join(HERE, "data", "kg_communities.json")
MIN_W = 2          # 共现边最低权重（单次共现=噪音）
MIN_SEATS = 3      # 社区最低席位数（<3 不成集团）
TOP_UNREG = 150    # 影子扩容：高频上榜未注册席位 top N
# 散户通道（拉萨系高共现是市场结构而非协同资金，社区发现聚焦真实游资联动）。
# 后 2 项 G4b-WL2 实证（20260930）：东财西藏系互联网通道与拉萨同源——
# 上榜日与拉萨系同框 82-84%、净买方向 54-55%、均额小一个数量级；
# 用全子串匹配避开"鞍山南"等误伤
RETAIL_EXCL = ("散户", "拉萨", "山南香曲东路", "昌都两江大道")
UNREG_EXCL = ("机构", "自然人", "中小投资者", "沪股通", "深股通",
              "总部", "高盛", "瑞银", "摩根大通",
              "中信证券股份有限公司上海分公司",
              "中国国际金融股份有限公司上海分公司")
# 通道型/特殊行席位（top_inst 的"机构专用/中小投资者/自然人"等特殊
# exalter 行 + 沪深股通通道）：高频高共现但非协同资金，不入影子池。
# 后 6 项为 G4b watchlist 人工审核结论（20260930）：券商总部（自营/托管）
# 与 QFII 外资通道席位大额净卖特征显著，非游资马甲，排除出 watchlist。
SENS_DAYS = (90, 180, 365)          # 敏感性网格：窗口
SENS_W = (2, 3)                     # 敏感性网格：共现阈值


def _is_retail(seat_name, reg_map):
    if any(k in seat_name for k in RETAIL_EXCL):
        return True
    for r in reg_map.get(seat_name) or []:
        if "散户" in r:
            return True
    return False


def _is_channel(seat_name):
    """通道型席位（机构专用/沪深股通）：高频高共现但非协同资金。"""
    return any(k in seat_name for k in UNREG_EXCL)


def _d0(days):
    return (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")


def _load(days):
    """读席位明细 parquet + 注册映射（OCCUPIES 反查）。"""
    cli = KgClient(retry=1)
    with cli._session() as s:
        reg = {}
        for r in s.run(
                "MATCH (h:HotMoney)-[:OCCUPIES]->(st:Seat) "
                "RETURN st.name AS nm, collect(h.name) AS hs").data():
            for h in r["hs"]:
                reg.setdefault(r["nm"], []).append(h)
    cli.close()
    df = tdxr.read_range(
        "ts_top_inst", start=_d0(days),
        columns=["ts_code", "trade_date", "exalter", "buy", "sell", "net_buy"])
    if df.empty:
        raise SystemExit("ts_top_inst 窗口内为空，先跑 tdx 同步")
    df["trade_date"] = df["trade_date"].astype(str).str[:8]
    df = df.dropna(subset=["exalter"])
    return df, reg


def _activity(df):
    """席位活跃度：n=上榜(标的,日)数，net=窗口净买（元）。"""
    g = (df.drop_duplicates(["exalter", "ts_code", "trade_date"])
           .groupby("exalter")
           .agg(n=("trade_date", "nunique"), net=("net_buy", "sum")))
    return g["n"].to_dict(), g["net"].to_dict()


def _universe(df, reg, top_unreg=TOP_UNREG):
    """席位池：词表命中席位 + 高频上榜未注册席位（通道/散户名排除）。

    返回 (universe_set, n_unreg)。"""
    n_days, _ = _activity(df)
    reg_seats = {s for s in n_days if s in reg}
    unreg = [(s, n) for s, n in n_days.items()
             if s not in reg and not _is_channel(s)
             and not _is_retail(s, reg)]
    top = {s for s, _ in sorted(unreg, key=lambda x: -x[1])[:top_unreg]}
    return reg_seats | top, len(top)


def _pairs(df, universe):
    """共现对计数：(席位a<席位b) → 共同(标的,日)数。"""
    d = df[df["exalter"].isin(universe)]
    cnt = Counter()
    for _, seats in d.groupby(["ts_code", "trade_date"])["exalter"]:
        ss = sorted(set(seats))
        if len(ss) >= 2:
            for p in combinations(ss, 2):
                cnt[p] += 1
    return cnt


def _graph(cnt, reg, min_w):
    """w>=min_w 连边 + 散户通道排除；孤立席位不入图（无共现即无集团）。"""
    G = nx.Graph()
    n_retail = 0
    for (a, b), w in cnt.items():
        if w < min_w:
            continue
        if _is_retail(a, reg) or _is_retail(b, reg):
            n_retail += 1
            continue
        G.add_edge(a, b, weight=w)
    return G, n_retail


def _communities(G, reg, n_days):
    """Louvain 划分 → 社区列表 + watchlist 候选。"""
    comms = nx.community.louvain_communities(
        G, weight="weight", seed=42)
    out, wl_raw = [], []
    for i, members in enumerate(sorted(
            comms, key=len, reverse=True)):
        ms = sorted(members)
        if len(ms) < MIN_SEATS:
            continue
        regs = []
        for m in ms:
            if m in reg:
                regs.extend(reg[m])
        # 社区名：注册游资优先（取社区内共现度最高注册者的第一名）
        deg = G.degree(ms, weight="weight")
        reg_m = [m for m in ms if m in reg]
        if reg_m:
            anchor = max(reg_m, key=lambda m: deg[m])
            name = (reg[anchor][0] + " 集团")
        else:
            anchor = max(ms, key=lambda m: deg[m])
            name = anchor[:14] + "…集团" if len(anchor) > 14 else \
                anchor + " 集团"
        n_pairs = sum(1 for _ in G.subgraph(ms).edges())
        mem = sorted(
            [{"seat": m, "reg": (reg.get(m) or [None])[0],
              "n": n_days.get(m, 0),
              "deg": int(deg[m])} for m in ms],
            key=lambda x: -x["deg"])
        out.append({
            "id": i, "name": name, "anchor": anchor,
            "has_reg": bool(reg_m), "n_seats": len(ms),
            "n_pairs": n_pairs, "w_total": int(sum(
                d["weight"] for _, _, d in G.subgraph(ms).edges(data=True))),
            "members": mem,
        })
        # watchlist：含注册游资的社区 → 未注册成员
        if reg_m:
            for m in ms:
                if m not in reg and n_days.get(m, 0) >= 5:
                    wl_raw.append({"seat": m, "comm": name,
                                   "n": n_days[m], "deg": int(deg[m])})
    watchlist = sorted(wl_raw, key=lambda x: (-x["deg"], -x["n"]))[:10]
    return out, watchlist


def sensitivity(df, reg, days=SENS_DAYS, ws=SENS_W, top_unreg=TOP_UNREG):
    """阈值敏感性：窗口 × 共现阈值网格 → 社区数/模块度/最大社区占比。

    回答 G4 遗留问题——"一张网无分化"是结构事实还是长窗口+低阈值的
    人造结果。席位池固定（365 天窗口口径）只滚窗口，隔离池变量。"""
    universe, n_unreg = _universe(df, reg, top_unreg)
    rows = []
    for d in days:
        dfw = df[df["trade_date"] >= _d0(d)]
        cnt = _pairs(dfw, universe)
        for w in ws:
            G, _ = _graph(cnt, reg, w)
            if G.number_of_edges() == 0:
                rows.append({"days": d, "min_w": w, "n_nodes": 0,
                             "n_edges": 0, "n_comms": 0, "modularity": None,
                             "top_share": 0.0})
                continue
            part = nx.community.louvain_communities(
                G, weight="weight", seed=42)
            comms = [c for c in part if len(c) >= MIN_SEATS]
            mod = nx.community.modularity(G, part)
            n_nodes = G.number_of_nodes()
            top_share = (max(len(c) for c in comms) / n_nodes
                         if comms and n_nodes else 0.0)
            rows.append({"days": d, "min_w": w, "n_nodes": n_nodes,
                         "n_edges": G.number_of_edges(),
                         "n_comms": len(comms),
                         "modularity": round(float(mod), 4),
                         "top_share": round(top_share, 3)})
    return {"n_unreg_pool": n_unreg, "grid": rows}


def build(days=365, sens=False):
    df, reg = _load(days)
    n_days, _ = _activity(df)
    universe, n_unreg = _universe(df, reg)
    cnt = _pairs(df, universe)
    G, n_retail = _graph(cnt, reg, MIN_W)

    out, watchlist = _communities(G, reg, n_days)
    res = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "days": days, "min_w": MIN_W,
        "top_unreg": TOP_UNREG,
        "n_seats_total": len(universe),
        "n_unreg_pool": n_unreg,
        "n_seats_graph": G.number_of_nodes(),
        "n_edges": G.number_of_edges(),
        "n_retail_pairs_excl": n_retail,
        "n_communities": len(out),
        "communities": out,
        "watchlist": watchlist,
    }
    if sens:
        res["sensitivity"] = sensitivity(df, reg)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OUT), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    return res


def push_to_graph(cli=None, dryrun=False):
    """K190-P2：json 集团快照入图——Seat-[IN_GROUP {role}]->EXP_Community。

    json 为唯一真相源（Louvain 快照）；社区节点 upsert by name；
    members 角色为 member，watchlist 席位角色为 watch。
    只增不改不删：图内历史社区名漂移（旧快照残留）由人工确认后清理。
    """
    cli = cli or KgClient(retry=1)
    from kg_schema import (LBL_EXP_COMMUNITY, LBL_SEAT, REL_IN_GROUP,
                           VALID_END_OPEN)
    with open(OUT, encoding="utf-8-sig") as f:
        cm = __import__("json").load(f)
    vstart = (cm.get("generated_at") or
              datetime.now().strftime("%Y-%m-%d"))[:10]
    snap = cm.get("generated_at", "")
    nodes, edges, names = [], [], set()
    for c in cm.get("communities") or []:
        name = c["name"]
        nodes.append({"name": name,
                      "n_seats": c.get("n_seats") or len(c.get("members")
                                                          or []),
                      "w_total": c.get("w_total") or 0, "snapshot": snap})
        names.add(name)
        for m in c.get("members") or []:
            edges.append({"src": m["seat"], "dst": name,
                          "validStart": vstart, "validEnd": VALID_END_OPEN,
                          "role": "member", "source": "kg_communities"})
    for w in cm.get("watchlist") or []:
        seat = (w or {}).get("seat")
        comm = (w or {}).get("comm")
        if not seat or not comm:
            continue
        if comm not in names:
            nodes.append({"name": comm, "n_seats": 0, "w_total": 0,
                          "snapshot": snap})
            names.add(comm)
        edges.append({"src": seat, "dst": comm,
                      "validStart": vstart, "validEnd": VALID_END_OPEN,
                      "role": "watch", "source": "kg_communities_watch"})
    print("push: %d communities / %d member edges" % (len(nodes),
                                                      len(edges)))
    if dryrun:
        return {"nodes": len(nodes), "edges": len(edges)}
    # K190-P2：Louvain 365 天席位池 ⊃ 图内 Seat 集——缺失席位先实体化
    # （name 即锚点，幂等 upsert），否则 IN_GROUP 边 MATCH 不到静默跳过
    seat_nodes = [{"name": src} for src in sorted({e["src"] for e in edges})]
    cli.upsert_nodes(LBL_SEAT, ["name"], seat_nodes)
    cli.upsert_nodes(LBL_EXP_COMMUNITY, ["name"], nodes)
    cli.upsert_edge(LBL_SEAT, "name", LBL_EXP_COMMUNITY, "name",
                    REL_IN_GROUP, edges)
    with cli._session() as s:
        n = s.run("MATCH (:Seat)-[r:IN_GROUP]->(:EXP_Community) "
                  "RETURN count(r) AS c").single()["c"]
    print("graph IN_GROUP edges: %d" % n)
    return {"nodes": len(nodes), "edges": len(edges)}


def stats():
    try:
        with open(OUT, encoding="utf-8-sig") as f:
            d = json.load(f)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "generated_at": d["generated_at"],
            "days": d["days"], "min_w": d.get("min_w", 2),
            "n_seats_total": d["n_seats_total"],
            "n_unreg_pool": d.get("n_unreg_pool", 0),
            "n_seats_graph": d["n_seats_graph"], "n_edges": d["n_edges"],
            "n_retail_pairs_excl": d.get("n_retail_pairs_excl", 0),
            "n_communities": d["n_communities"],
            "top": [{"name": c["name"], "n_seats": c["n_seats"],
                     "n_pairs": c["n_pairs"], "w_total": c["w_total"],
                     "has_reg": c["has_reg"],
                     "members": c["members"][:16]}
                    for c in d["communities"][:8]],
            "watchlist": d["watchlist"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "stats", "push"])
    ap.add_argument("days", nargs="?", type=int, default=365)
    ap.add_argument("--sens", action="store_true",
                    help="build 附带阈值敏感性网格")
    ap.add_argument("--dryrun", action="store_true",
                    help="push 只统计不写图")
    a = ap.parse_args()
    if a.cmd == "build":
        r = build(a.days, sens=a.sens)
        print("communities built: seats %d/%d（未注册影子 %d）edges %d "
              "-> %d groups (min_seats=%d)" % (
                  r["n_seats_graph"], r["n_seats_total"],
                  r["n_unreg_pool"], r["n_edges"],
                  r["n_communities"], MIN_SEATS))
        for c in r["communities"][:8]:
            print("  [%s] %-16s seats=%-3d w=%-5d %s" % (
                "R" if c["has_reg"] else " ", c["name"], c["n_seats"],
                c["w_total"], "·".join(
                    m["reg"] or m["seat"][:8] for m in c["members"][:4])))
        print("watchlist candidates: %d" % len(r["watchlist"]))
        for w in r["watchlist"][:5]:
            print("  · %s (%s) n=%d deg=%d" % (
                w["seat"][:24], w["comm"], w["n"], w["deg"]))
        if "sensitivity" in r:
            print("sensitivity（days × min_w → 社区数/模块度/最大占比）:")
            for g in r["sensitivity"]["grid"]:
                print("  %dd w%d: nodes=%d edges=%d comms=%d "
                      "mod=%s top=%s" % (
                          g["days"], g["min_w"], g["n_nodes"], g["n_edges"],
                          g["n_comms"], g["modularity"], g["top_share"]))
    elif a.cmd == "push":
        cli = KgClient(retry=1)
        cli.ensure_schema()
        push_to_graph(cli, dryrun=a.dryrun)
    else:
        print(json.dumps(stats(), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
