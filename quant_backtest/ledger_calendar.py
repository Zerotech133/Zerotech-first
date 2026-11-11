# -*- coding: utf-8 -*-
"""P1 日历账本 v1：横排日历 + 点击详情弹层（完整证据链）· 2026-11-11 深夜。

老田 v0 验收反馈：「这个版面看详情看不好，还是横着来，一天一个格，
点进去看详情：哪个板块、中了什么因子、完整证据链」。

v1 改动（对 v0 的三处翻新）：
  · 横排：一天一格，横向滚动，最近 60 个交易日；
  · 点击格/票 → 详情弹层：所属板块（成分快照反查+板块 r5 强弱）、
    因子命中（bei/bei2/bei24/tbe24/mbei/vr/位置）、证据链五问；
  · 分组键用选择日 day（v0 误用结构日 dt 的教训保留在案）。

数据：sig_events + ashare + sector_member_snap（票→板块）+ sector_state（板块 r5）。
输出：h5_daily/ledger_calendar.html（ui_tokens 同源色板）。
"""
import glob
import json
import os
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8")

SIG = os.path.join(HERE, "data", "sig_events",
                   "sig_events_market_v3_20251111_20261111.json")
ASH = os.path.join(HERE, "data", "ashare")
SNAP = sorted(glob.glob(os.path.join(HERE, "data", "sector_member_snap",
                                     "member_*.parquet")))
SEC = sorted(glob.glob(os.path.join(HERE, "reports", "sector_state_2*.json")))
OUT = os.path.join(HERE, "h5_daily", "ledger_calendar.html")
TOPN = 5
NDAYS = 0  # 0=全窗（v2 四级下钻需要全部数据）


def build_ctx():
    import pandas as pd
    sec2name, sec_r5, code2secs = {}, {}, {}
    if SNAP and SEC:
        df = pd.read_parquet(SNAP[-1])
        inv = df.groupby("con_code")["ts_code"].apply(list).to_dict()
        ss = json.load(open(SEC[-1], encoding="utf-8"))
        sec2name = {s["code"]: s.get("name") for s in ss.get("sectors", [])
                    if s.get("code")}
        sec_r5 = {s["code"]: s.get("r5") for s in ss.get("sectors", [])}
        code2secs = {c: [(sec2name.get(t, t), sec_r5.get(t))
                         for t in ts if sec2name.get(t)]
                     for c, ts in inv.items()}
    cal = pd.read_parquet(os.path.join(ASH, "000001_SZ.parquet"),
                          columns=["trade_date"])["trade_date"].astype(str).tolist()
    closes, pct = {}, {}
    stocks = set()
    j = json.load(open(SIG, encoding="utf-8"))
    for e in j["events"]:
        stocks.add(e["stock"])
    for s in stocks:
        fp = os.path.join(ASH, s.replace(".", "_") + ".parquet")
        if os.path.exists(fp):
            b = pd.read_parquet(fp, columns=["trade_date", "open", "close",
                                             "pct_chg"])
            closes[s] = dict(zip(b["trade_date"].astype(str),
                                 zip(b["open"].astype(float),
                                     b["close"].astype(float))))
            pct[s] = dict(zip(b["trade_date"].astype(str),
                              b["pct_chg"].astype(float)))
    return cal, closes, pct, code2secs


def main():
    j = json.load(open(SIG, encoding="utf-8"))
    ev = j["events"]
    cal, closes, pct, code2secs = build_ctx()
    cal_idx = {d: i for i, d in enumerate(cal)}

    byday = defaultdict(list)
    for e in ev:
        if e.get("type") == "3买" and e.get("score") is not None:
            byday[str(e["day"])].append(e)

    days = []
    for d in sorted(byday)[-NDAYS:]:
        rows = sorted(byday[d], key=lambda e: -e["score"])[:TOPN]
        i0 = cal_idx.get(d)
        if i0 is None or i0 + 1 >= len(cal):
            continue
        d1 = cal[i0 + 1]
        picks, rets = [], []
        for e in rows:
            m = closes.get(e["stock"]) or {}
            oc = m.get(d1)
            ret = None
            if oc and oc[0]:
                ret = (oc[1] / oc[0] - 1) * 100
            day_pct = (pct.get(e["stock"]) or {}).get(d)
            zg, zd, price = e.get("zg"), e.get("zd"), e.get("price")
            pos_pct = (price / zg - 1) * 100 if (zg and price) else None
            secs = (code2secs.get(e["stock"]) or [])[:8]
            picks.append({
                "code": e["stock"], "score": e["score"],
                "ret": ret, "day_pct": day_pct,
                "factors": {
                    "bei": bool(e.get("bei")), "bei2": bool(e.get("bei2")),
                    "bei24": bool(e.get("bei24")),
                    "tbe24": bool(e.get("tbe24")), "mbei": bool(e.get("mbei")),
                    "vr": e.get("vr"), "pos_pct": pos_pct,
                    "zg": zg, "zd": zd, "price": price,
                },
                "sectors": [{"name": n, "r5": r} for n, r in secs],
                "dt": e.get("dt"), "day": d,
            })
            if ret is not None:
                rets.append(ret)
        days.append({"day": d, "next": d1, "picks": picks,
                     "mean": sum(rets) / len(rets) if rets else None})

    payload = json.dumps(days, ensure_ascii=False)
    gen_ts = time.strftime("%Y-%m-%d %H:%M")
    html_tmpl = open(os.path.join(HERE, "tools",
                                   "_ledger_tmpl.html"), encoding="utf-8").read()
    html = html_tmpl.replace("__GEN__", gen_ts).replace("__DATA__", payload)
    open(OUT, "w", encoding="utf-8").write(html)
    print("日历账本 v1 →", OUT, "（%d 日 × %d 票/日，含板块映射）" % (len(days), TOPN))


if __name__ == "__main__":
    main()
