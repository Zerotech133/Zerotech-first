# -*- coding: utf-8 -*-
"""S6 交付层：图 + 长报告 md + H5 + manifest。"""
import base64
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from . import config

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.facecolor"] = "#0d1117"
plt.rcParams["axes.facecolor"] = "#0d1117"
plt.rcParams["savefig.facecolor"] = "#0d1117"


def _b64(p):
    return base64.b64encode(open(p, "rb").read()).decode()


def chart_price(snap, events, outdir):
    daily = snap["raw"].get("quote")
    if daily is None or not len(daily):
        return None, []
    df = daily.tail(260).copy()
    df["dtime"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    fig = plt.figure(figsize=(8.0, 5.4), dpi=160)
    ax = fig.add_axes([0.10, 0.15, 0.86, 0.72])
    ax.plot(df["dtime"], df["close"], color="#f0a63a", lw=1.9)
    fig.canvas.draw()
    inv = fig.transFigure.inverted()
    hot = []
    ev = sorted(events, key=lambda e: e["date"])[-8:]
    for i, e in enumerate(ev, 1):
        d = pd.to_datetime(e["date"], errors="coerce")
        if pd.isna(d):
            continue
        row = df.iloc[(df["dtime"] - d).abs().argsort()[:1]]
        if not len(row):
            continue
        px, dt = float(row["close"].iloc[0]), row["dtime"].iloc[0]
        ax.scatter([dt], [px], s=130, color="#ff5c55", edgecolor="#fff", lw=1.2, zorder=5)
        ax.text(dt, px, str(i), color="#fff", fontsize=8, ha="center", va="center", zorder=6, fontweight="bold")
        disp = ax.transData.transform((mdates.date2num(dt), px))
        fx, fy = inv.transform(disp)
        hot.append({"id": str(i), "x": round(float(fx) * 100, 2), "y": round(float(1 - fy) * 100, 2),
                    "label": "%s %s" % (e["date"], e["title"][:24]), "event": e})
    ax.grid(alpha=0.13, ls="--", color="#6e7681")
    for s in ax.spines.values():
        s.set_color("#30363d")
    ax.tick_params(colors="#93a3b5", labelsize=8)
    ax.set_ylabel("收盘价（元）", color="#93a3b5", fontsize=9)
    ax.set_title("%s %s · 价格与事件（近一年）" % (snap.get("name", ""), snap["code"]),
                 color="#e6edf3", fontsize=12, loc="left", pad=8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    p = os.path.join(outdir, "chart_price.png")
    fig.savefig(p)
    plt.close(fig)
    return p, hot


def chart_fin(snap, outdir):
    fin = snap["raw"].get("financial") or {}
    inc = fin.get("income")
    if inc is None or not len(inc):
        return None
    annual = inc[inc["end_date"].str.endswith("1231")].tail(5)
    labels = [r["end_date"][:4] for _, r in annual.iterrows()]
    rev = [float(r["revenue"]) / 1e8 for _, r in annual.iterrows()]
    npf = [float(r.get("n_income_attr_p") or 0) / 1e8 for _, r in annual.iterrows()]
    fig = plt.figure(figsize=(8.0, 3.9), dpi=160)
    ax = fig.add_axes([0.10, 0.17, 0.85, 0.70])
    x = np.arange(len(labels))
    ax.bar(x, rev, color="#2e86c1", width=0.5)
    for i, v in enumerate(rev):
        ax.text(i, v + 0.4, "%.1f" % v, ha="center", fontsize=8, color="#9ecbff")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9, color="#c9d1d9")
    ax.set_ylabel("营收（亿元）", color="#93a3b5", fontsize=9)
    ax.grid(axis="y", alpha=0.13, ls="--", color="#6e7681")
    for s in ax.spines.values():
        s.set_color("#30363d")
    ax.tick_params(colors="#93a3b5", labelsize=7)
    ax2 = ax.twinx()
    ax2.plot(x, npf, color="#ff5c55", marker="o", lw=1.6, ms=4)
    for i, v in enumerate(npf):
        ax2.text(i, v + (0.12 if v >= 0 else -0.25), "%.2f" % v, ha="center", fontsize=7.5, color="#ff9c94",
                 va="bottom" if v >= 0 else "top")
    ax2.axhline(0, color="#6e7681", lw=0.6)
    ax2.set_ylabel("归母净利（亿元）", color="#93a3b5", fontsize=9)
    ax2.tick_params(colors="#93a3b5", labelsize=7)
    ax.set_title("营收与归母净利（近五个年度）", color="#e6edf3", fontsize=11.5, loc="left", pad=8)
    p = os.path.join(outdir, "chart_fin.png")
    fig.savefig(p)
    plt.close(fig)
    return p


def chart_peers(feat, outdir):
    peers = feat.get("peers") or []
    if len(peers) < 3:
        return None
    peers = [p for p in peers if p.get("ps_ttm")][:8]
    mine = feat["valuation"].get("ps_ttm")
    names = ["本公司"] + [p["code"][:6] for p in peers]
    vals = [mine] + [p["ps_ttm"] for p in peers]
    vals = [v if v is not None else 0 for v in vals]
    colors = ["#ff5c55"] + ["#8b949e"] * len(peers)
    fig = plt.figure(figsize=(8.0, 3.6), dpi=160)
    ax = fig.add_axes([0.14, 0.18, 0.82, 0.66])
    y = np.arange(len(names))
    ax.barh(y, vals, color=colors, height=0.55)
    for yy, v in zip(y, vals):
        ax.text(v + 0.1, yy, "%.1fx" % v, va="center", fontsize=8, color="#e6edf3")
    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=8.5, color="#c9d1d9")
    ax.set_xlabel("PS（TTM）", fontsize=9, color="#93a3b5")
    ax.grid(axis="x", alpha=0.13, ls="--", color="#6e7681")
    for s in ax.spines.values():
        s.set_color("#30363d")
    ax.tick_params(axis="x", colors="#93a3b5", labelsize=7)
    ax.set_title("同业 PS 对比（申万一级前 8 家）", color="#e6edf3", fontsize=11.5, loc="left", pad=8)
    p = os.path.join(outdir, "chart_peers.png")
    fig.savefig(p)
    plt.close(fig)
    return p


def _fmt(v, suffix=""):
    if v is None:
        return "—"
    if isinstance(v, float):
        return ("%.2f" % v) + suffix
    return str(v) + suffix


def build_report(ctx):
    _m, snap, feat, vd, _ev = ctx["meta"], ctx["snapshot"], ctx["features"], ctx["verdict"], ctx["events"]
    tech, val, fnd = feat["technical"], feat["valuation"], feat["fundamental"]
    mf, chips = feat.get("moneyflow", {}), feat.get("chips", {})
    L = []
    A = L.append
    A("# %s（%s）个股全维分析" % (snap.get("name", ""), snap["code"]))
    A("")
    A("> 生成：%s ｜ 数据 as-of：%s（盘中 %s）｜ 流水线：analysis %s ｜ 研究分析，不构成投资建议"
      % (ctx["now"], snap["as_of"], ctx["intraday"] or "无", config.PROMPT_VERSION))
    A("")
    A("## 一、结论摘要")
    A("")
    A("**研究评级：%s**（置信度 %.2f，时间窗 %s）" % (vd["rating"], vd["confidence"], vd["horizon"]))
    A("")
    A(vd["thesis"])
    A("")
    if vd.get("key_signals"):
        A("关键信号：" + "；".join(vd["key_signals"]))
        A("")
    A("| 项 | 值 |")
    A("|---|---|")
    A("| 评级 | %s |" % vd["rating"])
    A("| 综合评分（规则） | %s |" % vd.get("score"))
    A("| 触发条件 | %s |" % "；".join(vd["triggers"]))
    A("| 失效条件 | %s |" % "；".join(vd["invalidations"]))
    A("")
    A("## 二、公司画像")
    A("")
    b = snap.get("basic") or {}
    A("- 名称/代码：%s / %s（%s，%s）" % (b.get("name", ""), snap["code"], b.get("market", ""), b.get("industry", "")))
    A("- 行业（申万一级）：%s ｜ 特殊状态：%s" % (val.get("industry") or "—", "、".join(fnd.get("special_status") or []) or "无"))
    A("- 市值：%s 亿元 ｜ 数据覆盖：%.1f%%（%s）" % (_fmt(val.get("market_cap_yi")), snap.get("coverage", 0),
                                            "缺 " + "、".join(snap.get("required_missing")) if snap.get("required_missing") else "必需域齐全"))
    ex = feat.get("extras") or {}
    if ex.get("mainbz"):
        mb = ex["mainbz"]
        A("- 主营构成（%s）：%s" % (mb.get("end_date"), "；".join(
            "%s %.1f%%" % (it["item"], it["share"]) for it in mb.get("items", [])[:4])))
    A("")
    A("## 三、财务")
    A("")
    A("| 指标 | 值 |")
    A("|---|---|")
    A("| 最新报告期 | %s |" % _fmt(fnd.get("latest_end")))
    A("| 营收（最新期） | %s 亿元 |" % _fmt(fnd.get("revenue_latest_yi")))
    A("| 归母净利（最新期） | %s 亿元 |" % _fmt(fnd.get("net_profit_latest_yi")))
    A("| 营收 TTM | %s 亿元 |" % _fmt(fnd.get("revenue_ttm_yi")))
    A("| 营收同比 | %s%% |" % _fmt(fnd.get("revenue_yoy")))
    A("| 净利同比 | %s%% |" % _fmt(fnd.get("net_profit_yoy")))
    _nr = fnd.get("nonrecurring") or {}
    if _nr.get("profit_dedt_yi") is not None:
        A("| 扣非归母净利 | %s 亿元 |" % _fmt(_nr["profit_dedt_yi"]))
    if _nr.get("extra_item_yi") is not None:
        A("| 非经常性损益 | %s 亿元（占归母 %s%%） |" % (_fmt(_nr["extra_item_yi"]), _fmt(round((_nr.get("ratio") or 0) * 100, 1))))
    if _nr.get("dt_yoy") is not None:
        A("| 扣非净利同比 | %s%% |" % _fmt(_nr["dt_yoy"]))
    A("| 毛利率 | %s%% |" % _fmt(fnd.get("gross_margin")))
    A("| 资产负债率 | %s%% |" % _fmt(fnd.get("debt_ratio")))
    A("| 归母净资产 | %s 亿元 |" % _fmt(fnd.get("net_assets_yi")))
    A("| 经营现金流（最近年度） | %s 亿元 |" % _fmt(fnd.get("ocf_annual_yi")))
    A("| 合同负债 | %s 亿元 |" % _fmt(fnd.get("contract_liab_yi")))
    A("| 商誉 | %s 亿元 |" % _fmt(fnd.get("goodwill_yi")))
    A("")
    if ex.get("forecast"):
        fc = ex["forecast"]
        A("- **业绩预告**（%s）：%s，净利润 %s ~ %s 亿元" % (fc.get("ann_date"), fc.get("type"),
                                                        _fmt(fc.get("net_min_yi")), _fmt(fc.get("net_max_yi"))))
    if ex.get("express"):
        ep = ex["express"]
        A("- **业绩快报**（%s）：营收 %s 亿元，净利 %s 亿元" % (ep.get("ann_date"), _fmt(ep.get("revenue_yi")), _fmt(ep.get("n_income_yi"))))
    A("")
    A("## 四、估值（含同业）")
    A("")
    A("| 指标 | 值 |")
    A("|---|---|")
    A("| PE(TTM) | %s |" % _fmt(val.get("pe_ttm")))
    A("| PB | %s |" % _fmt(val.get("pb")))
    A("| PS(TTM) | %s |" % _fmt(val.get("ps_ttm")))
    if val.get("ps_pct_hist") is not None:
        A("| PS 历史分位（近5年） | %s%% |" % _fmt(val.get("ps_pct_hist")))
    if val.get("hist_temp") is not None:
        A("| 估值历史温度（PE/PB/PS 均值） | %s%% |" % _fmt(val.get("hist_temp")))
    A("| 同业 PS 中位 | %s |" % _fmt(val.get("peer_ps_median")))
    A("| 同业 PS 分位 | %s |" % _fmt(val.get("peer_percentile")))
    _lag = (snap.get("data_lag") or {})
    if _lag.get("trade_days"):
        A("| 数据时效 | as-of %s，落后 %d 个交易日（告警） |" % (snap.get("as_of"), _lag["trade_days"]))
    A("")
    if feat.get("peers"):
        A("| 同业样本 | PS(TTM) | 市值(亿) |")
        A("|---|---|---|")
        for p in feat["peers"][:8]:
            A("| %s | %s | %s |" % (p["code"], _fmt(p.get("ps_ttm")), _fmt(p.get("market_cap_yi"))))
        A("")
    if ex.get("consensus"):
        c = ex["consensus"]
        A("**一致预期**：近 180 天 %d 篇研报；最新（%s，%s）评级 %s；最近预测 EPS %s、净利 %s 亿元。" % (
            c.get("reports_180d", 0), c.get("latest_date"), c.get("latest_org"), c.get("latest_rating") or "—",
            _fmt(c.get("eps_last")), _fmt(c.get("np_last_yi"))))
        A("")
    A("## 五、交易面与资金筹码")
    A("")
    A("| 指标 | 值 |")
    A("|---|---|")
    A("| 最新价 | %s |" % _fmt(tech.get("price")))
    A("| 当日涨跌 | %s%% |" % _fmt(tech.get("pct_1d")))
    A("| 20 日 / 60 日 / 250 日收益 | %s%% / %s%% / %s%% |" % (_fmt(tech.get("ret_20d")), _fmt(tech.get("ret_60d")), _fmt(tech.get("ret_250d"))))
    A("| 250 日分位 | %s%% |" % _fmt(tech.get("pct_rank_250d")))
    A("| MA20 / MA60 | %s / %s |" % (_fmt(tech.get("ma20")), _fmt(tech.get("ma60"))))
    A("| 主力净额（5 日 / 20 日） | %s 万 / %s 万 |" % (_fmt(mf.get("main_net_5d_wan")), _fmt(mf.get("main_net_20d_wan"))))
    A("| 两融余额 | %s 亿 |" % _fmt(mf.get("margin_balance_yi")))
    A("| 获利盘 / 平均成本 | %s%% / %s |" % (_fmt(chips.get("winner_rate")), _fmt(chips.get("avg_cost"))))
    A("")
    A("关键位：" + "；".join("%s %s（%s）" % (x["type"], x["price"], x.get("label", "")) for x in vd["levels"][:8]))
    A("")
    ha = ex.get("holder_actions") or {}
    if ex.get("lhb"):
        A("- **龙虎榜**：近 90 天 %d 次%s" % (ex["lhb"].get("count_90d", 0),
                                          ("；席位净额 %s 万元" % _fmt(ex["lhb"].get("seat_net_wan"))) if ex["lhb"].get("seat_net_wan") is not None else ""))
    if ha.get("holdertrade"):
        A("- **重要股东增减持**（近一年）：" + "；".join("%s %s %s%%" % (x["ann_date"], x["holder"][:12],
                                                                ("+" if x.get("in_de") == "IN" else "-") + str(x.get("ratio") or 0))
                                                for x in ha["holdertrade"][:4]))
    if ha.get("repurchase"):
        A("- **回购**：%s（%s）" % (ha["repurchase"].get("proc"), ha["repurchase"].get("ann_date")))
    if ha.get("pledge_ratio") is not None:
        A("- **质押比例**：%s%%" % ha["pledge_ratio"])
    if ha.get("float_next_180d"):
        A("- **未来 180 天解禁**：" + "；".join("%s %.1f%%" % (x["date"], x.get("ratio") or 0) for x in ha["float_next_180d"]))
    if ex.get("surveys"):
        A("- **机构调研**：近一年 %d 次，最近 %s" % (ex["surveys"].get("count_365d", 0), ex["surveys"].get("last_date")))
    if ex.get("interaction"):
        A("- **互动问答**：累计 %d 条（近 180 天 %d 条）" % (ex["interaction"].get("total", 0), ex["interaction"].get("recent_180d", 0)))
    A("")
    A("## 六、事件（近两年关键公告）")
    A("")
    if ctx["events"]:
        A("| 日期 | 类别 | 标题 | 复核 |")
        A("|---|---|---|---|")
        for e in ctx["events"][:15]:
            flag = {1: "✓通过", -1: "✗驳回"}.get(e.get("reviewed"), "待复核")
            cat = e.get("category_final") or e["category"]
            A("| %s | %s | %s | %s |" % (e["date"], cat, e["title"][:72], flag))
        rev = [e for e in ctx["events"] if e.get("reviewed") in (1, -1)]
        A("")
        A("复核状态：已复核 %d / %d 条（通过 %d，驳回 %d）；未复核事件仅作线索。" % (
            len(rev), len(ctx["events"]),
            sum(1 for e in rev if e.get("reviewed") == 1), sum(1 for e in rev if e.get("reviewed") == -1)))
    else:
        A("（事件库为空：公告未抓取或未命中关键类别）")
    A("")
    A("## 七、AI 研判（模型生成，仅供参考）")
    A("")
    views = ctx.get("views") or {}
    if views.get("channels"):
        for ch in views["channels"]:
            A("- **%s**（%s）：%s" % (ch["channel"], ch.get("model", ""), (ch.get("output") or "")[:500]))
        A("")
        if views.get("issues"):
            A("**事实校验问题**：" + "；".join(views["issues"]))
        A("")
    else:
        A("（本次未运行 AI 通道）")
    A("## 八、情景与观察清单")
    A("")
    A("| 情景 | 概率 | 触发 | 含义 |")
    A("|---|---|---|---|")
    for s in vd["scenarios"]:
        A("| %s | %.0f%% | %s | %s |" % (s["name"], s["probability"] * 100, s["trigger"], s["meaning"]))
    A("")
    A("观察清单：")
    for w in vd["watch"]:
        A("- %s ｜ %s" % (w["date"], w["item"]))
    A("")
    A("## 九、风险")
    A("")
    if vd["risks"]:
        for i, r in enumerate(vd["risks"], 1):
            A("%d. %s" % (i, r))
    else:
        A("（规则未识别到显著风险，仍需关注数据时效与事件不确定性）")
    A("")
    A("## 十、数据口径与质量门")
    A("")
    A("| 数据域 | as-of | 状态 | 行数 |")
    A("|---|---|---|---|")
    for k, d in snap["domains"].items():
        A("| %s | %s | %s | %s |" % (k, d.get("as_of") or "—", d.get("status"), d.get("rows", 0)))
    A("")
    for g in ("G1", "G2", "G3", "G4"):
        gate = (ctx.get("gates") or {}).get(g) or {}
        A("- **%s**：%s（score=%s）%s" % (g, "通过" if gate.get("passed") else "不通过",
                                        gate.get("score"), "；" + "；".join(gate.get("issues", [])) if gate.get("issues") else ""))
    A("")
    A("> 口径提醒：财务数据以最新报告期为准；估值/行情为 as-of 日收盘；事件为公告规则分类，✓/✗ 为人工复核结果，未复核仅作线索。")
    return "\n".join(L)


def build_h5(ctx, outdir):
    css = open(os.path.join(config.PKG, "templates", "h5_style.css"), encoding="utf-8").read()
    js = open(os.path.join(config.PKG, "templates", "h5_app.js"), encoding="utf-8").read()
    snap, feat, vd = ctx["snapshot"], ctx["features"], ctx["verdict"]
    tech, val, fnd = feat["technical"], feat["valuation"], feat["fundamental"]
    images = {}
    for key in ("price", "fin", "peers"):
        p = ctx["charts"].get(key)
        if p and os.path.exists(p):
            images[key] = _b64(p)

    # sheets + judgments
    sheet, judgments = {}, []
    jrows = [
        ("j1", "💰", "估值：%s" % ("PS %.2fx" % val["ps_ttm"] if val.get("ps_ttm") else "PE/PB 参考"),
         "同业分位 %s" % _fmt(val.get("peer_percentile")), [
             ("市值", "%s 亿元" % _fmt(val.get("market_cap_yi"))),
             ("PE(TTM)", _fmt(val.get("pe_ttm"))), ("PB", _fmt(val.get("pb"))),
             ("PS(TTM)", _fmt(val.get("ps_ttm"))),
             ("PS 历史分位（近5年）", "%s%%" % _fmt(val.get("ps_pct_hist"))),
             ("估值历史温度", "%s%%" % _fmt(val.get("hist_temp"))),
             ("同业 PS 中位", _fmt(val.get("peer_ps_median"))),
             ("同业 PS 分位", _fmt(val.get("peer_percentile")))], "估值只是结果，需结合盈利与事件判断；同业分位与自身历史分位互为参照。"),
        ("j2", "📊", "业绩：%s" % ("亏损" if (fnd.get("net_profit_latest_yi") or 0) < 0 else "盈利"),
         "营收同比 %s%%" % _fmt(fnd.get("revenue_yoy")), [
             ("最新报告期", _fmt(fnd.get("latest_end"))),
             ("营收", "%s 亿元" % _fmt(fnd.get("revenue_latest_yi"))),
             ("归母净利", "%s 亿元" % _fmt(fnd.get("net_profit_latest_yi"))),
             ("扣非净利", "%s 亿元" % _fmt((fnd.get("nonrecurring") or {}).get("profit_dedt_yi"))),
             ("非经常性损益", "%s 亿元（占 %s%%）" % (
                 _fmt((fnd.get("nonrecurring") or {}).get("extra_item_yi")),
                 _fmt(round(((fnd.get("nonrecurring") or {}).get("ratio") or 0) * 100, 1)))),
             ("营收 TTM", "%s 亿元" % _fmt(fnd.get("revenue_ttm_yi"))),
             ("毛利率", "%s%%" % _fmt(fnd.get("gross_margin"))),
             ("资产负债率", "%s%%" % _fmt(fnd.get("debt_ratio")))], "业绩是慢变量，决定估值中枢；扣非口径优先。"),
        ("j3", "📈", "技术面：250 日分位 %s%%" % _fmt(tech.get("pct_rank_250d")),
         "20 日 %s%% / 60 日 %s%%" % (_fmt(tech.get("ret_20d")), _fmt(tech.get("ret_60d"))), [
             ("现价", _fmt(tech.get("price"))), ("MA20", _fmt(tech.get("ma20"))), ("MA60", _fmt(tech.get("ma60"))),
             ("250 日高/低", "%s / %s" % (_fmt(tech.get("high_250d")), _fmt(tech.get("low_250d")))),
             ("成交额（20 日均）", "%s 万手" % _fmt(tech.get("vol_20d_wan"))),
             ("缠论", "有中枢" if (tech.get("chanlun") or {}).get("pivots") else "未取到")], "技术面只定节奏，不定价值。"),
        ("j4", "💵", "资金：主力 20 日 %s 万" % _fmt(feat.get("moneyflow", {}).get("main_net_20d_wan")),
         "获利盘 %s%%" % _fmt(feat.get("chips", {}).get("winner_rate")), [
             ("主力 5 日净额", "%s 万" % _fmt(feat.get("moneyflow", {}).get("main_net_5d_wan"))),
             ("主力 20 日净额", "%s 万" % _fmt(feat.get("moneyflow", {}).get("main_net_20d_wan"))),
             ("两融余额", "%s 亿" % _fmt(feat.get("moneyflow", {}).get("margin_balance_yi"))),
             ("获利盘", "%s%%" % _fmt(feat.get("chips", {}).get("winner_rate"))),
             ("平均成本", _fmt(feat.get("chips", {}).get("avg_cost")))], "资金是情绪的温度计。"),
        ("j5", "🎯", "结论：%s" % vd["rating"], "置信度 %.2f ｜ %s" % (vd["confidence"], vd["horizon"]),
         [("评级", vd["rating"]), ("评分", _fmt(vd.get("score"))),
          ("触发", "；".join(vd["triggers"])), ("失效", "；".join(vd["invalidations"]))], vd["thesis"]),
    ]
    for jid, icon, title, sub, rows, read in jrows:
        judgments.append({"id": jid, "icon": icon, "title": title, "sub": sub,
                          "tone": {"j1": "green", "j2": "blue", "j3": "amber", "j4": "purple", "j5": "red"}[jid]})
        sheet[jid] = {"kicker": "核心判断", "title": title, "rows": rows, "read": read, "note": "analysis 自动生成"}

    # 价格热点
    price_hot = []
    for h in ctx.get("hotspots") or []:
        e = h.get("event") or {}
        sid = "p" + h["id"]
        sheet[sid] = {"kicker": e.get("date", ""), "title": e.get("title", "")[:60],
                      "rows": [("类别", e.get("category", "")), ("日期", e.get("date", "")),
                               ("原文", e.get("url", "")[:80])],
                      "read": (e.get("title") or "") + "（规则分类，未人工复核）", "note": "事件库"}
        price_hot.append({"id": h["id"], "x": h["x"], "y": h["y"], "label": h.get("label", "")})

    # 时间线
    def _tag(e):
        flag = {1: "✓", -1: "✗"}.get(e.get("reviewed"), "")
        return (e.get("category_final") or e["category"]) + flag

    timeline = [{"d": e["date"], "t": e["title"][:40], "tag": _tag(e), "body": e["title"]}
                for e in ctx["events"][:14]]

    # tabs
    def kv(rows):
        return [{"k": k, "v": v, "n": ""} for k, v in rows]
    tabs = [
        {"id": "fin", "name": "财务", "rows": kv([
            ("最新报告期", _fmt(fnd.get("latest_end"))), ("营收", "%s 亿" % _fmt(fnd.get("revenue_latest_yi"))),
            ("归母净利", "%s 亿" % _fmt(fnd.get("net_profit_latest_yi"))),
            ("扣非净利", "%s 亿" % _fmt((fnd.get("nonrecurring") or {}).get("profit_dedt_yi"))),
            ("非经常性损益", "%s 亿（占 %s%%）" % (
                _fmt((fnd.get("nonrecurring") or {}).get("extra_item_yi")),
                _fmt(round(((fnd.get("nonrecurring") or {}).get("ratio") or 0) * 100, 1)))),
            ("营收 TTM", "%s 亿" % _fmt(fnd.get("revenue_ttm_yi"))),
            ("营收同比", "%s%%" % _fmt(fnd.get("revenue_yoy"))), ("毛利率", "%s%%" % _fmt(fnd.get("gross_margin"))),
            ("资产负债率", "%s%%" % _fmt(fnd.get("debt_ratio"))), ("净资产", "%s 亿" % _fmt(fnd.get("net_assets_yi"))),
            ("合同负债", "%s 亿" % _fmt(fnd.get("contract_liab_yi"))), ("经营现金流(年)", "%s 亿" % _fmt(fnd.get("ocf_annual_yi")))]),
         "read": "财务数据来自最新报告期；扣非口径优先。TTM 为最近年报+最新中报-上年中报。", "chart": "fin", "cap": "图：营收与净利"},
        {"id": "value", "name": "估值", "rows": kv([
            ("PE(TTM)", _fmt(val.get("pe_ttm"))), ("PB", _fmt(val.get("pb"))), ("PS(TTM)", _fmt(val.get("ps_ttm"))),
            ("同业 PS 中位", _fmt(val.get("peer_ps_median"))), ("同业 PS 分位", _fmt(val.get("peer_percentile"))),
            ("市值", "%s 亿" % _fmt(val.get("market_cap_yi")))]),
         "read": "同业样本为申万一级成分股（前 8 家）。", "chart": "peers", "cap": "图：同业 PS 对比"},
        {"id": "trade", "name": "交易", "rows": kv([
            ("现价", _fmt(tech.get("price"))), ("20 日", "%s%%" % _fmt(tech.get("ret_20d"))),
            ("60 日", "%s%%" % _fmt(tech.get("ret_60d"))), ("250 日分位", "%s%%" % _fmt(tech.get("pct_rank_250d"))),
            ("MA20/60", "%s / %s" % (_fmt(tech.get("ma20")), _fmt(tech.get("ma60")))),
            ("主力 20 日", "%s 万" % _fmt(feat.get("moneyflow", {}).get("main_net_20d_wan"))),
            ("获利盘", "%s%%" % _fmt(feat.get("chips", {}).get("winner_rate")))]),
         "read": "资金/筹码为 as-of 日口径。", "chart": None, "cap": None},
        {"id": "event", "name": "事件", "rows": kv([(e["date"], "%s%s｜%s" % ("%s " % {1: "✓", -1: "✗"}.get(e.get("reviewed"), "") if e.get("reviewed") in (1, -1) else "",
                                                     e.get("category_final") or e["category"], e["title"][:30])) for e in ctx["events"][:8]] or [("—", "事件库为空")]),
         "read": "事件为公告规则分类；✓/✗ 为人工复核结果，未复核仅作线索。", "chart": None, "cap": None},
    ]

    # 扩展数据（sync 缓存）
    ex = feat.get("extras") or {}
    if ex.get("consensus"):
        c = ex["consensus"]
        tabs[1]["rows"] += kv([("一致预期(180d)", "%d 篇" % c.get("reports_180d", 0)),
                               ("最新评级", c.get("latest_rating") or "—"),
                               ("预测 EPS", _fmt(c.get("eps_last")))])
    ex_rows = []
    if ex.get("lhb"):
        ex_rows.append(("龙虎榜(90d)", "%d 次" % ex["lhb"].get("count_90d", 0)))
    ha = ex.get("holder_actions") or {}
    if ha.get("holdertrade"):
        ex_rows.append(("股东增减持", "；".join("%s %s%%" % (x["ann_date"][:10], x.get("ratio") or 0) for x in ha["holdertrade"][:3])))
    if ha.get("pledge_ratio") is not None:
        ex_rows.append(("质押比例", "%s%%" % ha["pledge_ratio"]))
    if ha.get("float_next_180d"):
        ex_rows.append(("未来解禁", "；".join("%s %.1f%%" % (x["date"], x.get("ratio") or 0) for x in ha["float_next_180d"])))
    if ha.get("repurchase"):
        ex_rows.append(("回购", ha["repurchase"].get("proc") or ""))
    if ex.get("surveys"):
        ex_rows.append(("机构调研", "近一年 %d 次" % ex["surveys"].get("count_365d", 0)))
    if ex.get("interaction"):
        ex_rows.append(("互动问答", "累计 %d 条" % ex["interaction"].get("total", 0)))
    if ex.get("mainbz"):
        ex_rows.append(("主营构成", "；".join("%s %.0f%%" % (i["item"], i["share"]) for i in ex["mainbz"].get("items", [])[:3])))
    if ex.get("forecast"):
        ex_rows.append(("业绩预告", ex["forecast"].get("type") or ""))
    if ex_rows:
        tabs.append({"id": "ext", "name": "扩展", "rows": kv(ex_rows),
                     "read": "来自 sync 数据层（一致预期/龙虎榜/股东行为/调研/主营/预告）。", "chart": None, "cap": None})
    scen = [{"id": s["id"], "letter": s["id"].replace("s", ""), "title": s["name"],
             "sub": "概率 %.0f%% ｜ %s" % (s["probability"] * 100, s["trigger"]),
             "tone": {"sA": "green", "sB": "blue", "sC": "red", "sD": "amber"}.get(s["id"], "blue")} for s in vd["scenarios"]]
    for s in vd["scenarios"]:
        sheet[s["id"]] = {"kicker": "情景", "title": s["name"], "rows": [
            ("概率", "%.0f%%" % (s["probability"] * 100)), ("触发", s["trigger"]), ("含义", s["meaning"]),
            ("观察", "；".join(s.get("watch") or []))], "read": s["meaning"], "note": "规则生成，非预测"}
    risks = [{"t": "风险 %d" % i, "b": r} for i, r in enumerate(vd["risks"], 1)] or [{"t": "数据不足", "b": "规则未识别显著风险"}]
    watch = [{"t": w["date"], "b": w["item"]} for w in vd["watch"]]
    levels = {}
    for lv in vd["levels"]:
        levels.setdefault(lv["type"], []).append((str(lv["price"]), lv.get("label", "")))
    lv_p = "".join('<div class="lvi"><span class="chip p">%s</span><span class="n">%s</span></div>' % (p, n) for p, n in levels.get("pressure", []))
    lv_s = "".join('<div class="lvi"><span class="chip s">%s</span><span class="n">%s</span></div>' % (p, n) for p, n in levels.get("support", []))
    lv_m = "".join('<div class="lvi"><span class="chip m">%s</span><span class="n">%s</span></div>' % (p, n) for p, n in levels.get("pivot", []))
    lv_html = ('<div class="lvrow"><div class="tag">压力</div><div>%s</div></div>'
               '<div class="lvrow"><div class="tag">支撑</div><div>%s</div></div>'
               '<div class="lvrow"><div class="tag">中枢</div><div>%s</div></div>') % (lv_p or "—", lv_s or "—", lv_m or "—")

    views = ctx.get("views") or {}
    ai_rows = "".join('<div class="kvr"><div class="k">%s</div><div class="v">%s</div><div class="n">%s</div></div>'
                      % (ch["channel"], (ch.get("output") or "")[:80].replace("<", "&lt;"), ch.get("model", ""))
                      for ch in views.get("channels", [])) or '<div class="kvr"><div class="k">未运行</div><div class="v">—</div><div class="n"></div></div>'
    ai_issues = "；".join(views.get("issues") or []) or "无"

    three = ("<li data-n=\"1\"><b>%s</b>：%s</li>" % (vd["rating"], vd["thesis"][:190]))
    three += "<li data-n=\"2\"><b>估值</b>：PS %s，同业分位 %s；PE %s / PB %s。</li>" % (
        _fmt(val.get("ps_ttm")), _fmt(val.get("peer_percentile")), _fmt(val.get("pe_ttm")), _fmt(val.get("pb")))
    three += "<li data-n=\"3\"><b>关键位与观察</b>：%s；观察：%s。</li>" % (
        "；".join("%s %s" % (l["type"], l["price"]) for l in vd["levels"][:4]),
        "；".join(w["item"][:26] for w in vd["watch"][:3]))
    copy_text = ("【%s %s 个股分析】评级：%s（置信度 %.2f）\n%s\n数据 as-of：%s；研究分析，不构成投资建议。"
                 % (snap.get("name", ""), snap["code"], vd["rating"], vd["confidence"], vd["thesis"][:260], snap["as_of"]))

    data = {"sheet": sheet, "judgments": judgments, "price_hot": price_hot, "case_hot": [], "timeline": timeline,
            "tabs": tabs, "scenarios": scen, "levels": {}, "risks": risks, "watch": watch, "case_sheet": {}}

    body = f"""
<header class="topbar"><div class="in"><span class="nm">{snap.get('name','')}</span><span class="cd">{snap['code']}</span>
<span class="pill wait">{val.get('industry') or 'A股'}</span><span style="margin-left:auto;color:var(--dim2);font-size:11px">{snap['as_of']}</span></div><div class="prog"><i id="progBar"></i></div></header>
<nav class="nav"><div class="in">
<button data-target="overview">概览</button><button data-target="chart">一图看懂</button><button data-target="events">事件</button>
<button data-target="data">数据</button><button data-target="scenario">情景</button><button data-target="ai">AI 研判</button><button data-target="conclusion">结论 · 风险</button>
</div></nav>
<div class="wrap">
<section id="overview">
  <div class="hero"><h1>{snap.get('name','')} · 个股全维分析</h1>
  <div class="sub">{val.get('industry') or ''} ｜ {fnd.get('latest_end') or ''} 财务 ｜ 流水线 analysis {config.PROMPT_VERSION}</div>
  <div class="price"><b>{_fmt(tech.get('price'))}</b><span class="chg">{(tech.get('pct_1d') or 0):+.2f}%</span><span class="lab">as-of {snap['as_of']} 收盘</span></div>
  <div class="metrics">
    <div class="metric"><div class="k">总市值</div><div class="v">{_fmt(val.get('market_cap_yi'))} 亿</div><div class="s">{snap['code']}</div></div>
    <div class="metric"><div class="k">PS(TTM)</div><div class="v">{_fmt(val.get('ps_ttm'))}</div><div class="s">同业分位 {_fmt(val.get('peer_percentile'))}</div></div>
    <div class="metric"><div class="k">营收 TTM</div><div class="v">{_fmt(fnd.get('revenue_ttm_yi'))} 亿</div><div class="s">同比 {_fmt(fnd.get('revenue_yoy'))}%</div></div>
    <div class="metric"><div class="k">数据覆盖</div><div class="v">{snap.get('coverage',0):.0f}%</div><div class="s">{'缺' + '、'.join(snap.get('required_missing')) if snap.get('required_missing') else '必需域齐全'}</div></div>
  </div>
  <div class="three"><h3>三句话看懂</h3><ul>{three}</ul></div>
  <div class="btnrow"><button class="btn primary" id="copyBtn">📋 复制要点</button><a class="btn" href="report.md">看长报告 ›</a></div></div>
  <div class="sec-h"><h2>五条核心判断</h2><span>点开看数据</span></div>
  {"".join('<div class="jcard" data-tone="%s" data-sheet="%s"><div class="ic">%s</div><div class="tx"><div class="t">%s</div><div class="s">%s</div></div><div class="ar">›</div></div>' % (j['tone'], j['id'], j['icon'], j['title'], j['sub']) for j in judgments)}
</section>
<section id="chart">
  <div class="sec-h"><h2>一图看懂</h2><span>点图上编号</span></div>
  <div class="figcard zoomable"><img src="data:image/png;base64,{images.get('price','')}" alt="price">
  <div class="hint">点编号看事件 · 点空白放大</div>
  {"".join('<button class="hot" style="left:%s%%;top:%s%%" data-sheet="p%s" aria-label="%s">%s</button>' % (h['x'], h['y'], h['id'], h['label'], h['id']) for h in price_hot)}
  </div><div class="figcap">近一年价格与事件（事件为公告规则分类）</div>
  <div class="one">一句话：{vd['thesis'][:160]}</div>
</section>
<section id="events"><div class="sec-h"><h2>事件时间轴</h2><span>点开看细节</span></div><div class="tl">
{"".join('<details class="tli"><summary><div class="row1"><span class="d">%s</span><span class="t">%s</span><span class="tag">%s</span></div></summary><div class="body">%s</div></details>' % (t['d'], t['t'], t['tag'], t['body']) for t in timeline)}
</div></section>
<section id="data"><div class="sec-h"><h2>数据全景</h2><span>4 个维度</span></div>
<div class="tabs">{"".join('<button data-tab="%s" class="%s">%s</button>' % (t['id'], 'on' if i == 0 else '', t['name']) for i, t in enumerate(tabs))}</div>
{"".join('<div class="tabp %s" id="tab-%s"><div class="kv">%s</div><div class="one">%s</div></div>' % ('on' if i == 0 else '', t['id'], "".join('<div class="kvr"><div class="k">%s</div><div class="v">%s</div><div class="n">%s</div></div>' % (r['k'], r['v'], r['n']) for r in t['rows']), t['read']) for i, t in enumerate(tabs))}
</section>
<section id="scenario"><div class="sec-h"><h2>四种情景</h2><span>点开看触发</span></div>
{"".join('<div class="scard" data-tone="%s" data-sheet="%s"><div class="l">%s</div><div><div class="t">%s</div><div class="s">%s</div></div><div class="ar">›</div></div>' % (s['tone'], s['id'], s['letter'], s['title'], s['sub']) for s in scen)}
<div class="sec-h"><h2>关键位</h2><span>结构参考</span></div><div class="levels">{lv_html}</div></section>
<section id="ai"><div class="sec-h"><h2>AI 研判</h2><span>模型生成，非事实</span></div><div class="kv">{ai_rows}</div>
<div class="one"><b>事实校验</b>：{ai_issues}</div></section>
<section id="conclusion"><div class="sec-h"><h2>结论与观察清单</h2><span>研究口径</span></div>
<div class="big"><div class="lb">研究评级</div><div class="t">{vd['rating']}</div><div class="b">{vd['thesis']}<br>置信度 {vd['confidence']} ｜ 时间窗 {vd['horizon']} ｜ <b>研究分析，不构成投资建议</b></div></div>
<div class="sec-h"><h2>观察清单</h2><span>按时间</span></div>
{"".join('<div class="watch"><div class="d">%s</div><div class="b">%s</div></div>' % (w['t'], w['b']) for w in watch)}
<div class="sec-h"><h2>主要风险</h2><span>%d 条</span></div>
{"".join('<div class="risk"><div class="t">%s</div><div class="b">%s</div></div>' % (r['t'], r['b']) for r in risks)}</section>
<section id="method"><div class="sec-h"><h2>数据口径与方法</h2><span>可回溯</span></div><div class="foot">
<b>数据</b>：as-of {snap['as_of']}，覆盖 {snap.get('coverage',0):.1f}%；事件来自公告规则分类。<br>
<b>方法</b>：analysis 流水线（S1 快照/S2 事件/S3 特征/S4 研判/S5 结论/S6 交付）；规则评级，非投资建议。<br>
<b>口径提醒</b>：财务为最新报告期；估值/行情为收盘口径；AI 内容为模型生成。研究分析，不构成投资建议。
<div class="kb"><span>analysis v{config.PROMPT_VERSION}</span><span>tushare</span><span>事件库</span><span>质量门 G1-G4</span></div></div></section>
</div>
<div class="fab" id="fab">↑</div><div class="toast" id="toast"></div><div class="backdrop" id="backdrop"></div>
<div class="sheet" id="sheet"><div class="grip"></div><button class="x" id="sheetClose">×</button><div class="in" id="sheetIn"></div></div>
<div class="lb" id="lb"><img id="lbImg" alt=""><button class="cl" onclick="document.getElementById('lb').classList.remove('on');document.body.style.overflow=''">×</button></div>
"""
    html = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>%s %s · 个股全维分析</title>
<style>%s</style></head><body>
<script>window.__COPY_TEXT__ = %s;</script>
%s
<script>%s</script></body></html>""" % (snap.get("name", ""), snap["code"], css, json.dumps(copy_text, ensure_ascii=False), body,
                                        js.replace("__DATA__", json.dumps(data, ensure_ascii=False)))
    p = os.path.join(outdir, "h5.html")
    open(p, "w", encoding="utf-8").write(html)
    return p


def render_all(ctx, outdir):
    os.makedirs(outdir, exist_ok=True)
    charts = {}
    p, hot = chart_price(ctx["snapshot"], ctx["events"], outdir)
    if p:
        charts["price"] = p
        ctx["hotspots"] = hot
    charts["fin"] = chart_fin(ctx["snapshot"], outdir)
    charts["peers"] = chart_peers(ctx["features"], outdir)
    ctx["charts"] = charts
    report = build_report(ctx)
    rp = os.path.join(outdir, "report.md")
    open(rp, "w", encoding="utf-8").write(report)
    hp = build_h5(ctx, outdir)
    manifest = {
        "meta": ctx["meta"], "rating": ctx["verdict"]["rating"],
        "coverage": ctx["snapshot"].get("coverage"), "gates": ctx.get("gates"),
        "artifacts": {"report": os.path.basename(rp), "h5": os.path.basename(hp),
                      "charts": [os.path.basename(v) for v in charts.values() if v]},
    }
    mp = os.path.join(outdir, "manifest.json")
    open(mp, "w", encoding="utf-8").write(json.dumps(manifest, ensure_ascii=False, indent=1))
    return {"report": rp, "h5": hp, "manifest": mp, "charts": charts}
