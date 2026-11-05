# -*- coding: utf-8 -*-
"""xtcloud 长尾表远端真实水位探针（2026-11-05）。

背景：/api/data/status 的 latest 字段对 14 张表返回 null；且多数表不支持
start_date 过滤（实测 ts_cctv_news 传任何日期参数都返回 2020 年首行）。
唯一可靠办法：按 offset 取表尾 N 行，读日期列最大值。

用途：判定"本地滞后是漏采还是上游停更"，决定补拉还是定性。
"""
import io
import json
import os
import re
import sys

import requests

B = "http://rookie.xtcloud-tech.com:48088"
S = requests.Session()
S.trust_env = False

# 水位语义优先级：ann_date/pub_time 这类"发布日"比 end_date 更贴更新节奏
CAND = (
    "trade_date", "ann_date", "pub_time", "publish_date", "report_date",
    "surv_date", "ipo_date", "issue_date", "date", "end_date", "list_date",
)

TABLES = [
    "ts_pledge_detail", "ts_fund_manager", "ts_stk_managers", "ts_cb_price_chg",
    "ts_ths_member", "tdx_sector_daily", "ts_cb_issue", "ts_hk_tradecal",
    "ts_us_tradecal", "ts_fut_weekly_monthly", "ts_index_weight", "ts_news",
    "ts_stk_week_month_adj", "ts_stk_weekly_monthly",
]


def dmax(values):
    best = None
    for v in values:
        s = str(v)[:10].replace("-", "").replace("/", "")[:8]
        if len(s) == 8 and s.isdigit() and (best is None or s > best):
            best = s
    return best


def tail_date(tbl, total, n=300):
    d = S.get(B + "/api/data/table/" + tbl,
              params={"offset": max(0, total - n), "limit": n}, timeout=90).json()
    cols = d.get("columns") or []
    rows = d.get("rows") or []
    dc = next((c for c in CAND if c in cols), None)
    if not dc or not rows:
        return None, dc, len(rows)
    i = cols.index(dc)
    return dmax([r[i] for r in rows]), dc, len(rows)


def main():
    st = S.get(B + "/api/data/status", timeout=90).json()
    meta = {t["table"]: t for t in st["tables"]}
    out = []
    for tbl in TABLES:
        m = meta.get(tbl)
        if not m:
            print("%-24s 远端无此表" % tbl)
            out.append({"table": tbl, "remote": "NOT_EXIST"})
            continue
        total = int(m.get("rows") or 0)
        try:
            td, dc, got = tail_date(tbl, total)
        except Exception as e:  # noqa: BLE001
            print("%-24s ERR %s" % (tbl, str(e)[:60]))
            out.append({"table": tbl, "remote": "ERR"})
            continue
        print("%-24s rows=%-9d dcol=%-12s 尾部最大=%s（取样 %d）" % (tbl, total, dc, td, got))
        out.append({"table": tbl, "rows": total, "dcol": dc, "tail_max": td,
                    "latest_field": m.get("latest")})
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                     "reports", "data_hub", "xtcloud_tail_probe_20261105.json")
    with io.open(p, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("written", os.path.normpath(p))


if __name__ == "__main__":
    sys.exit(main())
