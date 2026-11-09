# -*- coding: utf-8 -*-
"""CL-1（M1）盘面事件入图：游资席位 + 缠论结构 + 量能事件。

情报学定位：K线/量能是市场博弈的"痕迹"，痕迹入图成为证据网络。
  · HotMoney（游资/资金主体，含拉萨天团散户集群）
  · Seat（券商营业部席位）  HotMoney -[OCCUPIES]-> Seat
  · Seat -[TRADED_ON {buy/sell/net_buy(元)}]-> Security（龙虎榜，按日聚合买卖两侧）
  · MarketEvent（kind=chanlun 缠论结构 | volume 量能事件）
    Security -[HAS_STRUCTURE]-> MarketEvent / -[HAS_VOL_EVENT]-> MarketEvent

数据源：
  · data/tdx/ts_top_inst（龙虎榜席位明细，3 年）
  · data/chanlun_pool.jsonl（缠论信号池 9426 条）
  · data/tdx/ts_daily（近 250 交易日量比分位）

时序铁律：所有边 validStart/validEnd；在市/事件关联 validEnd=9999-12-31。
克制原则：Seat 仅入游资词表命中席位（非全量席位），控制图密度。

口径警示：龙虎榜只记录上榜日席位买卖，卖出侧样本偏多——
TRADED_ON.net_buy 是"观察窗内上榜净额"，不等于该主体盈亏。

用法：
  python kg_build_cl1.py                # 全量同步（幂等）
  python kg_build_cl1.py --dryrun       # 只算规模不入图
  python kg_build_cl1.py --hm 拉萨      # 抽检游资归因链
  python kg_build_cl1.py --sample 000001.SZ   # 抽检个股盘面事件
"""

import argparse
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import kg_schema as S
import tdx_read as tdxr
import linreg  # X3 血缘登记（best-effort，不阻断）

POOL_F = os.path.join(HERE, "data", "chanlun_pool.jsonl")
YOUZI_F = os.path.join(HERE, "data", "youzi_registry.parquet")

# ── 游资词表 v1（2025-12 名单，regex 匹配 ts_top_inst.exalter）──
# (主体名, 席位 pattern 列表, kind)
YOUZI = [
    (
        "章建平(章盟主)",
        [
            r"(国泰君安|国泰海通).*上海江苏路",
            r"(国泰君安|国泰海通).*宁波彩虹北路",
            r"(国泰君安|国泰海通).*海阳西路",
        ],
        "hot_money",
    ),
    ("赵强(赵老哥)", [r"银河.*绍兴", r"浙商.*绍兴"], "hot_money"),
    ("方新侠", [r"中信证券.*西安朱雀大街", r"兴业.*陕西分"], "hot_money"),
    ("孙国栋(孙哥)", [r"中信证券.*上海溧阳路"], "hot_money"),
    ("陈小群", [r"银河.*大连黄河路", r"东亚前海.*苏州留园"], "hot_money"),
    ("炒股养家", [r"华鑫.*(红宝石路|宁波|宛平南路)"], "hot_money"),
    ("作手新一", [r"(国泰君安|国泰海通).*南京太平南路"], "hot_money"),
    ("92科比", [r"(国泰君安|国泰海通).*泰州鼓楼南路"], "hot_money"),
    (
        "小鳄鱼",
        [
            r"东方证券.*源深路",
            r"南京证券.*大钟亭",
            r"长江.*世纪大道",
            r"平安.*杭州曙光路",
        ],
        "hot_money",
    ),
    ("佛山无影脚", [r"光大.*佛山(绿景路|季华六路)"], "hot_money"),
    ("瑞鹤仙", [r"宜昌(新世纪|解放路|东山大道)"], "hot_money"),
    ("宁波桑田路", [r"国盛.*宁波桑田路"], "hot_money"),
    ("呼家楼", [r"中信证券.*上海凯滨路", r"中信建投.*北京中信大厦"], "hot_money"),
    ("粉葛", [r"东亚前海.*深圳"], "hot_money"),
    ("六一中路", [r"招商.*福州六一中路", r"华泰.*东丽"], "hot_money"),
    ("叶子阿猪", [r"(国泰君安|国泰海通).*上海中山东路"], "hot_money"),
    ("上塘路", [r"财通.*杭州上塘路"], "hot_money"),
    ("毛老板", [r"方正.*乐山龙游路"], "hot_money"),
    # G4b watchlist 审核入表（20260930）：大额净买主导（单日均额
    # +1251万/+1697万）+ 与章建平/小鳄鱼/国泰海通系马甲高频同框；
    # 独立主体不预设集团归属（与小鳄鱼、章建平均有共现）
    ("开源西安系", [r"开源证券.*西安(西大街|太华路)"], "hot_money"),
    # G4b-WL2 二轮审核入表（20260930）：持续大额净买（1095 日 +35.5亿，
    # 单日均额 +686~703万，净买方向 57-58%）推翻上轮"均额小"犹豫点；
    # 与开源西安系 381 次/章建平 308 次实质共现——独立主体不预设集团归属
    ("国信浙江互联网", [r"国信证券.*浙江互联网"], "hot_money"),
    ("拉萨天团(散户)", [r"东方财富.*拉萨"], "retail_cluster"),
]

VOL_WINDOW = 250  # 量能事件观察窗（交易日）
VOL_HUGE = 3.0  # 巨量：vol/vol_ma20 >= 3
VOL_TINY = 0.4  # 地量：vol/vol_ma20 <= 0.4


def _now():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


# ── ① 游资词表落盘 ────────────────────────────────────────────
def save_registry():
    df = pd.DataFrame(
        [{"name": n, "kind": k, "patterns": "|".join(p)} for n, p, k in YOUZI]
    )
    df.to_parquet(YOUZI_F, index=False)
    print("  词表落盘: %s（%d 主体）" % (os.path.basename(YOUZI_F), len(df)))


# ── ② 龙虎榜席位 → HotMoney/Seat/OCCUPIES/TRADED_ON ──────────
def build_seats():
    """返回 (hm_nodes, seat_nodes, occ_edges, traded_edges, stats_str)"""
    df = tdxr.read_range(
        "ts_top_inst",
        columns=["ts_code", "trade_date", "exalter", "side", "buy", "sell", "net_buy"],
    )
    if df.empty:
        raise SystemExit("ts_top_inst 为空，先跑 tdx 同步")
    df["trade_date"] = df["trade_date"].astype(str).str[:8]

    # 词表匹配：exalter → 主体（一席多主时取首个并告警）
    seat2hm, amb = {}, set()
    for name, pats, _ in YOUZI:
        rx = re.compile("|".join(pats))
        for seat in df["exalter"].dropna().unique():
            if rx.search(seat):
                if seat in seat2hm and seat2hm[seat] != name:
                    amb.add(seat)
                seat2hm.setdefault(seat, name)

    hit = df[df["exalter"].isin(seat2hm)].copy()
    hit["hm"] = hit["exalter"].map(seat2hm)

    # 按 (席位, 标的, 日) 聚合买卖两侧 → 净额一条（MERGE 键防互覆盖）
    agg = hit.groupby(["exalter", "ts_code", "trade_date"], as_index=False).agg(
        buy=("buy", "sum"), sell=("sell", "sum"), net_buy=("net_buy", "sum")
    )

    # HotMoney 节点（词表全量，含 0 命中主体）
    seat_cnt = hit.groupby("hm")["exalter"].nunique()
    stock_cnt = hit.groupby("hm")["ts_code"].nunique()
    hm_nodes = [
        {
            "name": n,
            "kind": k,
            "ver": "youzi_v1_202512",
            "n_seats": int(seat_cnt.get(n, 0)),
            "n_stocks": int(stock_cnt.get(n, 0)),
            "source": "youzi_registry",
            "updated_at": _now(),
        }
        for n, _, k in YOUZI
    ]

    # Seat 节点 + OCCUPIES（validStart=该席位最早出现日）
    g = hit.groupby("exalter")["trade_date"]
    seat_nodes, occ_edges = [], []
    for seat, dates in g:
        hm = seat2hm[seat]
        seat_nodes.append(
            {
                "name": seat,
                "hm": hm,
                "first_seen": dates.min(),
                "last_seen": dates.max(),
                "n_days": int(dates.nunique()),
                "source": "ts_top_inst",
                "updated_at": _now(),
            }
        )
        occ_edges.append(
            {
                "src": hm,
                "dst": seat,
                "validStart": dates.min(),
                "validEnd": S.VALID_END_OPEN,
                "source": "youzi_registry",
                "updated_at": _now(),
            }
        )

    # TRADED_ON（元口径；观察窗偏差见模块 docstring）
    traded_edges = [
        {
            "src": r.exalter,
            "dst": r.ts_code,
            "validStart": r.trade_date,
            "validEnd": S.VALID_END_OPEN,
            "buy": float(r.buy),
            "sell": float(r.sell),
            "net_buy": float(r.net_buy),
            "source": "ts_top_inst",
            "updated_at": _now(),
        }
        for r in agg.itertuples()
    ]

    miss = [n for n, _, _ in YOUZI if n not in set(hit["hm"])]
    stats = "席位明细 %d 行（%s~%s），词表命中席位 %d / 主体 %d/%d" % (
        len(df),
        df["trade_date"].min(),
        df["trade_date"].max(),
        len(seat_nodes),
        hit["hm"].nunique(),
        len(YOUZI),
    )
    if miss:
        stats += "；未命中: %s" % ",".join(miss)
    if amb:
        stats += "；一席多主(取首): %d" % len(amb)
    return hm_nodes, seat_nodes, occ_edges, traded_edges, stats


# ── ③ 缠论信号 → MarketEvent(kind=chanlun) + HAS_STRUCTURE ───
def build_chanlun():
    events, edges = [], []
    seen = set()
    with io.open(POOL_F, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            s = json.loads(line)
            code, dt = s.get("code"), (s.get("dt") or "").replace("-", "")
            if not code or not dt:
                continue
            evt_id = "%s:%s:%s" % (code, s.get("type"), dt)
            if evt_id in seen:  # 同日同型信号去重（保留最后）
                events = [e for e in events if e["evt_id"] != evt_id]
                edges = [e for e in edges if e["dst"] != evt_id]
            seen.add(evt_id)
            events.append(
                {
                    "evt_id": evt_id,
                    "kind": "chanlun",
                    "type": s.get("type"),
                    "date": dt,
                    "detect_date": (s.get("detect_date") or "").replace("-", ""),
                    "lag": s.get("lag"),
                    "price": s.get("price"),
                    "entry_px": s.get("entry_px"),
                    "bei": bool(s.get("bei")),
                    "bei2": bool(s.get("bei2")),
                    "vv": bool(s.get("vv")),
                    "reg": s.get("reg"),
                    "period": s.get("period"),
                    # v4 灰度层（K25）：判别字段入图，证据数组留 jsonl；
                    # 保留 None/False/True 三态（不可判≠判否），灰度子集
                    # 用 WHERE gray_v IS NOT NULL 圈定——历史行无值自然排除
                    "div_area": s.get("div_area"),
                    "div_dif": s.get("div_dif"),
                    "pv_state": s.get("pv_state"),
                    "pv_dir": s.get("pv_dir"),
                    "conf": s.get("conf"),
                    "gray_v": s.get("gray_v"),
                    "source": "chanlun_pool",
                    "updated_at": _now(),
                }
            )
            edges.append(
                {
                    "src": code,
                    "dst": evt_id,
                    "validStart": dt,
                    "validEnd": S.VALID_END_OPEN,
                    "source": "chanlun_pool",
                    "updated_at": _now(),
                }
            )
    return events, edges


# ── ④ 量能事件 → MarketEvent(kind=volume) + HAS_VOL_EVENT ────
def build_volume():
    start = (datetime.now() - timedelta(days=420)).strftime("%Y%m%d")
    df = tdxr.read_range(
        "ts_daily", start=start, columns=["ts_code", "trade_date", "vol"]
    )
    if df.empty:
        raise SystemExit("ts_daily 近一年无数据")
    df["trade_date"] = df["trade_date"].astype(str).str[:8]
    win = sorted(df["trade_date"].unique())[-VOL_WINDOW:]
    df = df[df["trade_date"].isin(win)].sort_values(["ts_code", "trade_date"])
    df["vol_ma20"] = df.groupby("ts_code")["vol"].transform(
        lambda s: s.rolling(20, min_periods=20).mean()
    )
    df = df.dropna(subset=["vol_ma20"])
    df["ratio"] = df["vol"] / df["vol_ma20"]

    huge = df[df["ratio"] >= VOL_HUGE]
    tiny = df[(df["ratio"] <= VOL_TINY) & (df["ratio"] > 0)]
    events, edges = [], []
    for sub, typ in ((huge, "巨量"), (tiny, "地量")):
        for r in sub.itertuples():
            evt_id = "%s:vol:%s" % (r.ts_code, r.trade_date)
            events.append(
                {
                    "evt_id": evt_id,
                    "kind": "volume",
                    "type": typ,
                    "date": r.trade_date,
                    "vol": float(r.vol),
                    "vol_ma20": round(float(r.vol_ma20), 2),
                    "vol_ratio": round(float(r.ratio), 3),
                    "source": "ts_daily",
                    "updated_at": _now(),
                }
            )
            edges.append(
                {
                    "src": r.ts_code,
                    "dst": evt_id,
                    "validStart": r.trade_date,
                    "validEnd": S.VALID_END_OPEN,
                    "source": "ts_daily",
                    "updated_at": _now(),
                }
            )
    stats = "窗口 %s~%s（%d 交易日）：巨量 %d / 地量 %d（阈值 ≥%.1f / ≤%.1f）" % (
        win[0],
        win[-1],
        len(win),
        len(huge),
        len(tiny),
        VOL_HUGE,
        VOL_TINY,
    )
    return events, edges, stats


# ── 主流程 ────────────────────────────────────────────────────
def build(dryrun=False):
    _jr = linreg.JobRun("kg_daily_sync", "cl1_sync").__enter__()  # X3 血缘
    print("=== CL-1 盘面事件入图 %s ===" % datetime.now().strftime("%F %T"))

    print("[1/4] 游资词表…")
    save_registry()

    print("[2/4] 龙虎榜席位归因…")
    hm, seats, occ, traded, s1 = build_seats()
    print("  %s" % s1)
    print(
        "  HotMoney %d / Seat %d / OCCUPIES %d / TRADED_ON %d"
        % (len(hm), len(seats), len(occ), len(traded))
    )

    print("[3/4] 缠论结构…")
    ev1, e1 = build_chanlun()
    print("  MarketEvent(chanlun) %d / HAS_STRUCTURE %d" % (len(ev1), len(e1)))

    print("[4/4] 量能事件…")
    ev2, e2, s2 = build_volume()
    print("  %s" % s2)
    print("  MarketEvent(volume) %d / HAS_VOL_EVENT %d" % (len(ev2), len(e2)))

    if dryrun:
        print("DRYRUN 结束，未写库。")
        _jr.finish("ok")  # X3 血缘（显式收尾）
        return

    from kg_client import KgClient

    cli = KgClient()
    cli.ensure_schema()
    # X3 血缘边：三段产物各自登记（输入→输出，写图前落账）
    _jr.edge("neo4j:HotMoney", "parquet:tdx/ts_top_inst", transform="regex_match")
    _jr.edge("neo4j:Seat", "parquet:tdx/ts_top_inst", transform="regex_match")
    _jr.edge("neo4j:OCCUPIES", "parquet:youzi_registry", transform="regex_match")
    _jr.edge("neo4j:TRADED_ON", "parquet:tdx/ts_top_inst", transform="agg")
    _jr.edge("neo4j:MarketEvent", "jsonl:chanlun_pool", transform="chanlun")
    _jr.edge("neo4j:HAS_STRUCTURE", "jsonl:chanlun_pool")
    _jr.edge("neo4j:MarketEvent", "parquet:tdx/ts_daily", transform="vol_ratio")
    _jr.edge("neo4j:HAS_VOL_EVENT", "parquet:tdx/ts_daily")
    _jr.rows_written = (
        len(hm)
        + len(seats)
        + len(occ)
        + len(traded)
        + len(ev1)
        + len(e1)
        + len(ev2)
        + len(e2)
    )  # X3 血缘
    print("[落库] …")
    cli.upsert_nodes(S.LBL_HOTMONEY, ["name"], hm)
    cli.upsert_nodes(S.LBL_SEAT, ["name"], seats)
    cli.upsert_edge(S.LBL_HOTMONEY, "name", S.LBL_SEAT, "name", S.REL_OCCUPIES, occ)
    cli.upsert_edge(
        S.LBL_SEAT, "name", S.LBL_SECURITY, "ts_code", S.REL_TRADED_ON, traded
    )
    _ds_map = {"chanlun": "tdx_quote", "volume": "tdx_quote"}  # F2-P2-b
    for _e in ev1 + ev2:
        _e["data_source"] = _ds_map.get(_e.get("kind"), "unknown")
    cli.upsert_nodes(S.LBL_MARKET_EVENT, ["evt_id"], ev1 + ev2)
    cli.upsert_edge(
        S.LBL_SECURITY, "ts_code", S.LBL_MARKET_EVENT, "evt_id", S.REL_HAS_STRUCTURE, e1
    )
    cli.upsert_edge(
        S.LBL_SECURITY, "ts_code", S.LBL_MARKET_EVENT, "evt_id", S.REL_HAS_VOL_EVENT, e2
    )
    print("  全部落库完成")
    cli.close()
    print("=== CL-1 完成 ===")
    _jr.finish("ok")  # X3 血缘（显式收尾）


# ── 抽检 ──────────────────────────────────────────────────────
def sample_hm(key):
    """游资归因链：HotMoney → Seat → TRADED_ON → Security Top。"""
    from kg_client import KgClient

    cli = KgClient()
    with cli._session() as s:
        rows = list(
            s.run(
                "MATCH (h:HotMoney) WHERE h.name CONTAINS $k "
                "MATCH (h)-[o:OCCUPIES]->(st:Seat) "
                "RETURN h.name AS hm, st.name AS seat, st.n_days AS nd, "
                "o.validStart AS vs ORDER BY nd DESC LIMIT 20",
                k=key,
            )
        )
        print("=== 游资 '%s'：席位占用（%d）===" % (key, len(rows)))
        for r in rows:
            print(
                "  %-16s %s  上榜%d日  since %s"
                % (r["hm"], r["seat"], r["nd"], r["vs"])
            )
        if not rows:
            cli.close()
            return
        hm = rows[0]["hm"]
        rows = list(
            s.run(
                "MATCH (h:HotMoney {name: $hm})-[:OCCUPIES]->(st:Seat)"
                "-[t:TRADED_ON]->(sec:Security) "
                "RETURN sec.ts_code AS tc, sec.name AS nm, count(t) AS n, "
                "sum(t.net_buy) AS net ORDER BY n DESC LIMIT 15",
                hm=hm,
            )
        )
        print("  → 交易标的 Top15（上榜次数 / 累计净买亿元）:")
        for r in rows:
            print(
                "    %-11s %-10s %3d 次  净买 %.2f 亿"
                % (r["tc"], r["nm"], r["n"], (r["net"] or 0) / 1e8)
            )
    cli.close()


def sample_stock(ts_code):
    """个股盘面事件：缠论结构 + 量能事件时间线。"""
    from kg_client import KgClient

    cli = KgClient()
    with cli._session() as s:
        rows = list(
            s.run(
                "MATCH (sec:Security {ts_code: $c})"
                "-[r:HAS_STRUCTURE|HAS_VOL_EVENT]->(e:MarketEvent) "
                "RETURN e.date AS d, e.kind AS k, e.type AS t, "
                "e.vol_ratio AS vr, e.lag AS lag, e.reg AS reg, e.bei AS bei "
                "ORDER BY d DESC LIMIT 30",
                c=ts_code,
            )
        )
        print("=== %s 盘面事件（最新 %d 条）===" % (ts_code, len(rows)))
        for r in rows:
            extra = (
                ("ratio=%.2f" % r["vr"])
                if r["k"] == "volume"
                else ("lag=%s reg=%s bei=%s" % (r["lag"], r["reg"], r["bei"]))
            )
            print("  %s  [%s/%s]  %s" % (r["d"], r["k"], r["t"], extra))
        seats = list(
            s.run(
                "MATCH (sec:Security {ts_code: $c})<-[t:TRADED_ON]-(st:Seat)"
                "<-[:OCCUPIES]-(h:HotMoney) "
                "RETURN h.name AS hm, count(t) AS n, sum(t.net_buy) AS net "
                "ORDER BY n DESC LIMIT 10",
                c=ts_code,
            )
        )
        if seats:
            print("  龙虎榜游资印记（上榜次数 / 累计净买亿元）:")
            for r in seats:
                print(
                    "    %-16s %3d 次  净买 %.2f 亿"
                    % (r["hm"], r["n"], (r["net"] or 0) / 1e8)
                )
    cli.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dryrun", action="store_true")
    ap.add_argument("--hm", help="抽检游资归因链（名称关键词）")
    ap.add_argument("--sample", help="抽检个股盘面事件（ts_code）")
    args = ap.parse_args()
    if args.hm:
        sample_hm(args.hm)
    elif args.sample:
        sample_stock(args.sample)
    else:
        build(dryrun=args.dryrun)


if __name__ == "__main__":
    main()
