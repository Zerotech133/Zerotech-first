# -*- coding: utf-8 -*-
"""P3-d TradeDate 一等节点：交易日历 + 事件挂日（ON_DATE）。

情报学定位：日期原本只是边上的 validStart 字符串与事件属性，"某日
发生了什么"只能按属性扫全库。TradeDate 一等节点后：
  · (MarketEvent/Event)-[ON_DATE]->(TradeDate) 一跳取某日全事件
  · TradeDate-[NEXT_TRADE_DAY]->TradeDate 交易日链（前后导航）
  · dow 属性区分周内节奏（周一效应/周五效应查证入口）

数据源：图自洽的**市场事实**口径——distinct(MarketEvent.date)
∪ distinct(TRADED_ON.validStart)，不依赖外部日历（ts_daily 里有但
图内无交易的日期对图无意义，不入图）。news/Event 不参与日历生成：
新闻发布日≠交易日（周末/节假日公告大量存在），Event 只在已有交易
日上挂 ON_DATE，非交易日新闻保留 date 属性不入链。sync 自愈：清除
不在新口径内的历史 TradeDate 节点（含其 ON_DATE/NEXT 边）。

用法：
  python kg_tradedate.py            # 全量同步（幂等，重跑零写）
  python kg_tradedate.py --stat     # 只看对账统计
"""
import argparse
from datetime import datetime

import kg_schema as S
from kg_client import KgClient
import linreg  # X3 血缘登记（best-effort，不阻断）

SOURCE = "graph_dates"


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _dow(yyyymmdd):
    """周一=1 … 周日=7。"""
    d = datetime(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:]))
    return d.isoweekday()


def sync(cli):
    _jr = linreg.JobRun("kg_daily_sync", "tradedate").__enter__()  # X3 血缘
    ts = _now()
    with cli._session() as s:
        # 市场事实口径：行情/榜单事件的日期才是交易日（news 周末也有）
        ds = sorted(set(
            [r["d"] for r in s.run(
                "MATCH (m:MarketEvent) WHERE m.date IS NOT NULL "
                "RETURN DISTINCT m.date AS d").data()]
            + [r["d"] for r in s.run(
                "MATCH ()-[t:TRADED_ON]->() "
                "RETURN DISTINCT t.validStart AS d").data()]))
        # 自愈：清理旧口径（含 Event/news 日期）留下的非交易日节点
        old = [r["d"] for r in s.run(
            "MATCH (d:TradeDate) RETURN d.date AS d").data()]
        stale = [d for d in old if d not in set(ds)]
        if stale:
            s.run("UNWIND $stale AS x MATCH (d:TradeDate {date:x}) "
                  "DETACH DELETE d", stale=stale).consume()
            print("清理非交易日 TradeDate %d 个（news 日历口径遗留）"
                  % len(stale))
    print("交易日 %d 个（%s ~ %s）" % (len(ds), ds[0], ds[-1]))

    # ① TradeDate 节点
    rows = [{"date": d, "dow": _dow(d), "updated_at": ts} for d in ds]
    cli.upsert_nodes(S.LBL_TRADE_DATE, ["date"], rows)

    # ② NEXT_TRADE_DAY 链（相邻交易日 N 节点 N-1 边）
    nxt = [{"src": ds[i], "dst": ds[i + 1], "validStart": ds[i],
            "validEnd": S.VALID_END_OPEN, "source": SOURCE,
            "updated_at": ts}
           for i in range(len(ds) - 1)]
    cli.upsert_edge(S.LBL_TRADE_DATE, "date", S.LBL_TRADE_DATE, "date",
                    S.REL_NEXT_TD, nxt)

    # ③ ON_DATE 挂边：按日分批服务端 MERGE（幂等，重跑零写）
    with cli._session() as s:
        for d in ds:
            for lbl in ("MarketEvent", "Event"):
                s.run(
                    "MATCH (m:%s {date:$d}) " % lbl
                    + "WHERE NOT (m)-[:ON_DATE]->() "
                    "MATCH (td:TradeDate {date:$d}) "
                    "MERGE (m)-[r:ON_DATE {validStart:$d}]->(td) "
                    "SET r.validEnd=$ve, r.source=$src, r.updated_at=$ts",
                    d=d, ve=S.VALID_END_OPEN, src=SOURCE, ts=ts).consume()

    # 对账输出
    # X3 血缘：图自洽日历 → TradeDate/NEXT_TRADE_DAY/ON_DATE
    _jr.edge("neo4j:TradeDate", "neo4j:MarketEvent", transform="cal")  # X3
    _jr.edge("neo4j:TradeDate", "neo4j:TRADED_ON")  # X3
    _jr.edge("neo4j:NEXT_TRADE_DAY", "neo4j:TradeDate")  # X3
    _jr.edge("neo4j:ON_DATE", "neo4j:TradeDate")  # X3
    _jr.rows_written = len(ds) + len(nxt)  # X3
    stat(cli)
    _jr.__exit__(None, None, None)  # X3 血缘


def stat(cli):
    with cli._session() as s:
        n_td = s.run("MATCH (d:TradeDate) RETURN count(d) AS c") \
            .single()["c"]
        n_on = s.run("MATCH ()-[r:ON_DATE]->() RETURN count(r) AS c") \
            .single()["c"]
        n_next = s.run("MATCH ()-[r:NEXT_TRADE_DAY]->() "
                       "RETURN count(r) AS c").single()["c"]
        # MarketEvent 必须全部挂日（缺挂=bug）；Event 未挂=非交易日
        # 新闻（周末/节假日公告），按设计预期非零，仅对账展示
        n_miss = s.run(
            "MATCH (m:MarketEvent) WHERE m.date IS NOT NULL "
            "AND NOT (m)-[:ON_DATE]->() RETURN count(m) AS c") \
            .single()["c"]
        n_news_off = s.run(
            "MATCH (e:Event) WHERE e.date IS NOT NULL "
            "AND NOT (e)-[:ON_DATE]->() RETURN count(e) AS c") \
            .single()["c"]
        print("TradeDate %d / ON_DATE %d / NEXT_TRADE_DAY %d / "
              "MarketEvent 缺挂 %d（应为 0）/ 非交易日新闻 %d（不入链）"
              % (n_td, n_on, n_next, n_miss, n_news_off))
        return {"td": n_td, "on": n_on, "miss": n_miss,
                "news_off": n_news_off, "next": n_next}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stat", action="store_true")
    a = ap.parse_args()
    cli = KgClient(retry=1)
    try:
        if a.stat:
            stat(cli)
        else:
            sync(cli)
    finally:
        cli.close()


if __name__ == "__main__":
    main()
