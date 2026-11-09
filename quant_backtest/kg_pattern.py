# -*- coding: utf-8 -*-
"""K190-P1 · 16 K 线形态事件入图管线（chanlun_pattern.detect → Neo4j）。

事件化：形态命中 = MarketEvent(kind=pattern, type=形态名, pdir)，与
chanlun/volume/candle 事件同构。边：HAS_PATTERN（Security→事件）、
USES_TECH（事件→Technique，tech_name=形态名，与盘口 6 类并存）、
ON_DATE（事件→TradeDate）。

数据源：data/ashare/*.parquet（11 列 schema，与对账底册同源）。
用法：python kg_pattern.py build [--limit N] [--dryrun]
"""

import glob
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pandas as pd  # noqa: E402
import kg_schema as S  # noqa: E402
from chanlun_pattern import detect  # noqa: E402
from kg_client import KgClient  # noqa: E402

ASHARE = os.path.join(HERE, "data", "ashare")
BATCH = 5000


def _td8(v):
    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s.replace("-", "")[:8]


def build(cli=None, dryrun=False, limit=0):
    cli = cli or KgClient(retry=1)
    files = sorted(glob.glob(os.path.join(ASHARE, "*.parquet")))
    if limit:
        files = files[:limit]
    evs, edges_ev, edges_tech, edges_date = [], [], [], []
    techs = {}
    n_evt = 0
    t0 = datetime.now()

    def flush():
        nonlocal n_evt
        if not evs:
            return
        if not dryrun:
            for _e in evs:
                _e["data_source"] = "ashare_daily"  # F2-P2-b
            cli.upsert_nodes(S.LBL_MARKET_EVENT, ["evt_id"], evs)
            cli.upsert_edge(
                S.LBL_SECURITY,
                "ts_code",
                S.LBL_MARKET_EVENT,
                "evt_id",
                S.REL_HAS_PATTERN,
                edges_ev,
            )
            cli.upsert_nodes(S.LBL_TECHNIQUE, ["name"], list(techs.values()))
            cli.upsert_edge(
                S.LBL_MARKET_EVENT,
                "evt_id",
                S.LBL_TECHNIQUE,
                "name",
                S.REL_USES_TECH,
                edges_tech,
            )
            cli.upsert_edge(
                S.LBL_MARKET_EVENT,
                "evt_id",
                S.LBL_TRADE_DATE,
                "date",
                S.REL_ON_DATE,
                edges_date,
            )
        n_evt += len(evs)
        evs.clear()
        edges_ev.clear()
        edges_tech.clear()
        edges_date.clear()

    for fi, fp in enumerate(files, 1):
        code = os.path.basename(fp)[:-8].replace("_", ".")
        try:
            df = pd.read_parquet(fp)
        except Exception:
            continue
        need = {"trade_date", "open", "high", "low", "close"}
        if not need.issubset(df.columns) or len(df) < 60:
            continue
        df = (
            df.drop_duplicates("trade_date", keep="last")
            .sort_values("trade_date")
            .tail(500)
            .reset_index(drop=True)
        )
        bars = [
            {
                "dt": _td8(r["trade_date"]),
                "o": float(r["open"]),
                "h": float(r["high"]),
                "l": float(r["low"]),
                "c": float(r["close"]),
            }
            for _, r in df.iterrows()
        ]
        for p in detect(bars):
            d8 = p["dt"]
            eid = "%s:pattern:%s" % (code, d8)
            evs.append(
                {
                    "evt_id": eid,
                    "kind": "pattern",
                    "type": p["name"],
                    "pdir": p["dir"],
                    "date": d8,
                    "source": "chanlun_pattern",
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
            edges_ev.append(
                {
                    "src": code,
                    "dst": eid,
                    "validStart": d8,
                    "validEnd": S.VALID_END_OPEN,
                    "source": "chanlun_pattern",
                }
            )
            if p["name"] not in techs:
                techs[p["name"]] = {
                    "name": p["name"],
                    "family": "K线形态",
                    "kind": "pattern",
                }
            edges_tech.append(
                {
                    "src": eid,
                    "dst": p["name"],
                    "validStart": d8,
                    "validEnd": S.VALID_END_OPEN,
                    "source": "chanlun_pattern",
                }
            )
            edges_date.append(
                {
                    "src": eid,
                    "dst": d8,
                    "validStart": d8,
                    "validEnd": S.VALID_END_OPEN,
                    "source": "chanlun_pattern",
                }
            )
        if len(evs) >= BATCH:
            flush()
        if fi % 500 == 0:
            print(
                "progress %d/%d evt=%d %.0fs"
                % (fi, len(files), n_evt, (datetime.now() - t0).total_seconds()),
                flush=True,
            )
    flush()
    if dryrun:
        print("dryrun local pattern events: %d (未写图)" % n_evt)
        return {"events_local": n_evt, "techniques": len(techs)}
    with cli._session() as s:
        n_t = s.run("MATCH (t:Technique) RETURN count(t) AS c").single()["c"]
        n_p = s.run(
            "MATCH (:Security)-[r:HAS_PATTERN]->(:MarketEvent) RETURN count(r) AS c"
        ).single()["c"]
        by = s.run(
            "MATCH (:Security)-[:HAS_PATTERN]->(m:MarketEvent) "
            "RETURN m.type AS t, count(*) AS c ORDER BY c DESC "
            "LIMIT 6"
        ).data()
    print("pattern events: %d | Technique nodes: %d | top: %s" % (n_p, n_t, by))
    return {"events": n_p, "techniques": n_t}


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dryrun", action="store_true")
    a = ap.parse_args()
    cli = KgClient(retry=1)
    cli.ensure_schema()
    build(cli, dryrun=a.dryrun, limit=a.limit)


if __name__ == "__main__":
    main()
