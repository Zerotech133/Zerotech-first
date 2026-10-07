# -*- coding: utf-8 -*-
"""K235 · 二期实体解析：tushare namechange → Security.aliases 别名入图。

金融 KG 二期（finance_kg_plan）：曾用名/别名是实体链指与画布搜索的
消歧地基——「G三花/三花股份 → 002050.SZ 三花智控」类历史名直接可搜。

口径：
· tushare pro.namechange 分页全量（单页 1 万截断，offset 循环拉全）；
· 聚合：每 ts_code 的历史 name 去重集合 − 现用名（start_date 最新者）
  = aliases（列表属性，Neo4j 原生 LIST）；
· 只更新图内已存在的 Security（退市/未上市标的不上图）；
· 幂等：SET n.aliases 整体覆盖（重跑安全）；
· 报告 data/namechange_sync_last.json（原子写）。

用法：python namechange_sync.py [--dryrun]
"""
import json
import os
import sys
import tempfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

OUT = os.path.join(HERE, "data", "namechange_sync_last.json")


def _load_all():
    """tushare namechange 分页全量。"""
    import tushare as ts
    from dotenv import load_dotenv
    load_dotenv(os.path.join(HERE, ".env.local"))
    pro = ts.pro_api(os.getenv("TUSHARE_TOKEN"))
    fields = "ts_code,name,start_date,end_date,change_reason"
    frames, offset = [], 0
    while True:
        df = pro.namechange(fields=fields, offset=offset)
        if df is None or df.empty:
            break
        frames.append(df)
        if len(df) < 10000:
            break
        offset += len(df)
    import pandas as pd
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def run(dryrun=False):
    df = _load_all()
    if df.empty:
        print("namechange 拉取为空")
        return
    df["ts_code"] = df["ts_code"].astype(str)
    # 现用名 = start_date 最大那条的 name
    df = df.sort_values(["ts_code", "start_date"])
    cur = df.groupby("ts_code").tail(1).set_index("ts_code")["name"].to_dict()
    aliases = {}
    for tc, g in df.groupby("ts_code"):
        hist = sorted(set(g["name"].astype(str)) - {cur.get(tc, "")})
        if hist:
            aliases[tc] = hist
    from kg_client import KgClient
    cli = KgClient(retry=1)
    with cli._session() as s:
        gset = {r["c"] for r in s.run(
            "MATCH (n:Security) RETURN n.ts_code AS c").data()}
    hits = {tc: a for tc, a in aliases.items() if tc in gset}
    n_alias_total = sum(len(a) for a in hits.values())
    print("namechange %d 行 / %d 标的有别名 / 图内命中 %d 标的 / "
          "别名共 %d 个" % (len(df), len(aliases), len(hits),
                            n_alias_total))
    if dryrun:
        cli.close()
        return
    # 批量 SET aliases（UNWIND，覆盖式幂等）
    rows = [{"tc": tc, "al": a} for tc, a in hits.items()]
    with cli._session() as s:
        for i in range(0, len(rows), 1000):
            s.run("UNWIND $rows AS r "
                  "MATCH (n:Security {ts_code: r.tc}) "
                  "SET n.aliases = r.al", rows=rows[i:i + 1000])
    cli.close()
    res = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rows": len(df), "n_codes_with_alias": len(hits),
        "n_alias_total": n_alias_total,
        "updated": len(rows),
    }
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OUT), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print("落盘: %s（updated %d）" % (OUT, len(rows)))


def main():
    dry = "--dryrun" in sys.argv
    run(dryrun=dry)


if __name__ == "__main__":
    main()
