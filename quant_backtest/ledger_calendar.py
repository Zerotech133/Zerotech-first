# -*- coding: utf-8 -*-
"""P1 日历账本 · 生成器 v0（2026-11-11 深夜，老田「日历+溯源+证据」指令）。

每天一格：当日各手法的选择 → 次日真实走势 → 证据链（为什么选它）。
不管用多少种交易手法，全程可溯源（手法版本号 + 信号依据 + 事件缓存指针）。

数据源（全部现有，无需等 P1 账本）：
  · data/sig_events/sig_events_market_v3_*.json —— 每日三买/一买事件（选择依据）
  · data/ashare/*.parquet —— 次日真实走势（T+1 开盘买入口径）
  · 手法登记簿 data/p1_ledger/methods.json —— 版本时间线（不存在则全 v1.0）
输出：h5_daily/ledger_calendar.html（统一 ui_tokens 暗色设计）
"""
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8")

SIG = os.path.join(HERE, "data", "sig_events",
                   "sig_events_market_v3_20251111_20261111.json")
ASH = os.path.join(HERE, "data", "ashare")
OUT = os.path.join(HERE, "h5_daily", "ledger_calendar.html")
TOPN = 5          # 每日每手法展示前 N 选择
HOLD = 1          # 展示次日真实（T+1 开盘→收盘）


def load_closes():
    import pandas as pd
    cal = pd.read_parquet(os.path.join(ASH, "000001_SZ.parquet"),
                          columns=["trade_date"])["trade_date"].astype(str).tolist()
    closes = {}
    stocks = set()
    j = json.load(open(SIG, encoding="utf-8"))
    for e in j["events"]:
        stocks.add(e["stock"])
    for s in stocks:
        fp = os.path.join(ASH, s.replace(".", "_") + ".parquet")
        if os.path.exists(fp):
            b = pd.read_parquet(fp, columns=["trade_date", "open", "close"])
            closes[s] = dict(zip(b["trade_date"].astype(str),
                                 zip(b["open"].astype(float),
                                     b["close"].astype(float))))
    return cal, closes


def main():
    j = json.load(open(SIG, encoding="utf-8"))
    ev = j["events"]
    cal, closes = load_closes()
    cal_idx = {d: i for i, d in enumerate(cal)}

    # 每日三买 topN（按 score，手法=v3 评分，登记簿暂无→版本恒 v1.0）
    from collections import defaultdict
    byday = defaultdict(list)
    for e in ev:
        if e.get("type") != "3买" or e.get("score") is None:
            continue
        # day=选择日（当日盘中可见）；dt=信号结构日（回踩笔终点，可早于 day）
        byday[str(e["day"])].append(e)

    days = []
    for d in sorted(byday):
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
            if oc:
                o, c = oc
                if o:
                    ret = (c / o - 1) * 100
            picks.append({
                "code": e["stock"], "score": e["score"],
                "bei": bool(e.get("bei24")), "vr": e.get("vr"),
                "ret": ret, "dt_sig": d,
                "evidence": "三买事件｜选择日=%s 结构日dt=%s｜score=%d"
                            "（pos+vol+bei30 v1.0）｜bei24=%s｜vr=%.2f" % (
                    d, e.get("dt"), e["score"],
                    "Y" if e.get("bei24") else "N", e.get("vr") or 0),
            })
            if ret is not None:
                rets.append(ret)
        days.append({
            "day": d, "next": d1,
            "picks": picks,
            "mean_ret": sum(rets) / len(rets) if rets else None,
        })
    days = days[-90:]  # 近 90 个交易日

    # ---------- HTML（ui_tokens 统一风格）----------
    C = dict(bg="#0c1117", panel="#121924", line="#1f2835", tx="#e6edf3",
             tx2="#9aa7b8", tx3="#6b7787", acc="#4f8fe6", up="#e35d5d",
             dn="#3aa76d", warn="#e0b95c", tier="#e35d5d")

    def cell(p):
        r = p["ret"]
        col = C["tx3"] if r is None else (C["up"] if r > 0 else (C["dn"] if r < 0 else C["tx2"]))
        return ('<div class="pick" title="%s"><span class="nm">%s</span>'
                '<span class="sc">%.0f</span>'
                '<span class="rt" style="color:%s">%s</span></div>') % (
            p["evidence"].replace('"', "&quot;"), p["code"][-9:-3],
            p["score"], col, "—" if r is None else "%+.2f%%" % r)

    rows_html = []
    for d in days:
        mr = d["mean_ret"]
        mcol = C["tx3"] if mr is None else (C["up"] if mr > 0 else C["dn"])
        picks = "".join(cell(p) for p in d["picks"])
        rows_html.append(
            '<div class="day"><div class="dhead">'
            '<span class="dt">%s</span><span class="nx">→ %s 实况</span>'
            '<span class="mr" style="color:%s">%s</span></div>'
            '<div class="picks">%s</div></div>' % (
            d["day"][:4] + "-" + d["day"][4:6] + "-" + d["day"][6:],
            d["next"][4:6] + "-" + d["next"][6:], mcol,
            "—" if mr is None else "组合 %+.2f%%" % mr, picks))

    html = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>P1 日历账本 · 每日选择与次日实况</title><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:{bg};color:{tx};font:13px/1.6 "Segoe UI",system-ui,"Microsoft YaHei",sans-serif;padding:24px;max-width:1240px;margin:0 auto}}
h1{{font-size:18px}} .m{{color:{tx3};font-size:12px;margin:4px 0 14px}}
.day{{background:{panel};border:1px solid {line};border-radius:10px;padding:10px 12px;margin-bottom:8px}}
.dhead{{display:flex;gap:10px;align-items:baseline;margin-bottom:6px}}
.dt{{font-weight:600}} .nx{{color:{tx3};font-size:11.5px}} .mr{{margin-left:auto;font-weight:600}}
.picks{{display:flex;gap:6px;flex-wrap:wrap}}
.pick{{background:#0e141d;border:1px solid {line};border-radius:8px;padding:5px 9px;cursor:help}}
.nm{{font-weight:600;margin-right:6px}} .sc{{color:{tx2};font-size:11px;margin-right:6px}} .rt{{font-size:12px;font-weight:600}}
.note{{color:{tx2};font-size:11.5px;margin-top:12px}}
</style></head><body>
<h1>P1 日历账本 · 每日选择 → 次日实况</h1>
<div class="m">手法 v3（pos+vol+bei30）· 版本 v1.0 · 悬停每只票看证据链（信号 dt/评分分量/背驰/量比）·
数据源 sig_events_market_v3 + ashare 日线 · 生成 {gen}</div>
{rows}
<div class="note">溯源约定：每格=当日三买 top{n}（按评分）→ 次日开盘买入→收盘的实况；
手法变更后本页自动按 methods.json 登记簿分版本着色（登记簿上线前恒 v1.0）。
次日为当日事件缓存 dt 的下一交易日；「—」=次日无行情（停牌/待补）。</div>
</body></html>""".format(bg=C["bg"], panel=C["panel"], line=C["line"], tx=C["tx"],
                        tx2=C["tx2"], tx3=C["tx3"], rows="".join(rows_html),
                        gen=time.strftime("%Y-%m-%d %H:%M"), n=TOPN)
    open(OUT, "w", encoding="utf-8").write(html)
    print("日历账本 →", OUT, "（%d 个交易日）" % len(days))


if __name__ == "__main__":
    main()
