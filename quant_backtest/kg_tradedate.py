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

前瞻日历（2026-10-09，K267）：市场事实日之外，补 trade_cal 权威
日历的前瞻开市日（最大事实日之后 ~90 自然日，source=cal_forward）：
  · NEXT_TRADE_DAY 链通到未来（复市首日/次个交易日导航不再踩空）
  · 滞后计算按交易日口径（kg_objectview 徽章）需要「今天是否开市」
  · ON_DATE 挂边仍只对市场事实日做（前瞻日无事件可挂）
  · 自愈清理排除前瞻日（重查同源），旧节点不误删

用法：
  python kg_tradedate.py            # 全量同步（幂等，重跑零写）
  python kg_tradedate.py --stat     # 只看对账统计
"""
import argparse
from datetime import datetime, timedelta

import kg_schema as S
from kg_client import KgClient
import linreg  # X3 血缘登记（best-effort，不阻断）

SOURCE = "graph_dates"
SOURCE_FWD = "cal_forward"          # 前瞻日历（trade_cal 权威）
FORWARD_DAYS = 90                   # 前瞻自然日窗


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _dow(yyyymmdd):
    """周一=1 … 周日=7。"""
    d = datetime(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:]))
    return d.isoweekday()


def _forward_days(fact_max):
    """trade_cal 前瞻开市日：最大事实日之后 ~90 自然日（K267）。

    失败静默返回 []（前瞻是增强不是依赖——断源时图回退纯事实口径）。
    """
    try:
        import datamart as dm
        start = (datetime.strptime(fact_max, "%Y%m%d")
                 + timedelta(days=1)).strftime("%Y%m%d")
        end = (datetime.now() + timedelta(days=FORWARD_DAYS)) \
            .strftime("%Y%m%d")
        df = dm.read("trade_cal", start=start, end=end,
                     fields=["cal_date", "is_open"])
        if df is None or df.empty or "cal_date" not in df.columns:
            return []
        sub = df[df["is_open"] == 1] if "is_open" in df.columns else df
        return sorted(str(v)[:8] for v in sub["cal_date"]
                      if str(v)[:8].isdigit())
    except Exception:
        return []


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
        # K267 前瞻：trade_cal 开市日（仅建节点+NEXT 链，不挂事件）
        fwd = _forward_days(ds[-1])
        all_days = sorted(set(ds) | set(fwd))
        # 自愈：清理旧口径（含 Event/news 日期）留下的非交易日节点
        # （前瞻日历内的不删——它不是事实日但属于权威日历）
        old = [r["d"] for r in s.run(
            "MATCH (d:TradeDate) RETURN d.date AS d").data()]
        stale = [d for d in old if d not in set(all_days)]
        if stale:
            s.run("UNWIND $stale AS x MATCH (d:TradeDate {date:x}) "
                  "DETACH DELETE d", stale=stale).consume()
            print("清理非交易日 TradeDate %d 个（news 日历口径遗留）"
                  % len(stale))
    print("交易日 %d 个（%s ~ %s，含前瞻 %d）"
          % (len(all_days), all_days[0], all_days[-1], len(fwd)))

    # ① TradeDate 节点（前瞻日 source=cal_forward 区分血缘）
    rows = [{"date": d, "dow": _dow(d), "updated_at": ts,
             "source": (SOURCE_FWD if d in set(fwd) else SOURCE)}
            for d in all_days]
    cli.upsert_nodes(S.LBL_TRADE_DATE, ["date"], rows)

    # ② NEXT_TRADE_DAY 链（相邻交易日 N 节点 N-1 边，链通未来）
    nxt = [{"src": all_days[i], "dst": all_days[i + 1],
            "validStart": all_days[i],
            "validEnd": S.VALID_END_OPEN, "source": SOURCE,
            "updated_at": ts}
           for i in range(len(all_days) - 1)]
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
    _jr.finish("ok")  # X3 血缘（显式收尾）


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
