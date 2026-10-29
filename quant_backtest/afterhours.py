# -*- coding: utf-8 -*-
"""盘后复盘报告：当日收盘全景 · 赚钱效应复盘 · 预判对照。

与 premarket.py 同一数据仓口径（daily/index_daily/moneyflow/margin/
cb_daily + JEV news.db + TDX），复用其工具函数；A股配色（涨红跌绿）。
输出：reports/afterhours.html + afterhours_latest.json。
用法：python afterhours.py [--force]（门禁：最近收盘交易日=今日且已过15:05）"""

import argparse
import html
import json
import os
import subprocess
from datetime import datetime

import premarket as pm
import rps_resonance as RR
import logging

HERE = pm.HERE
REPORTS = pm.REPORTS

# 主要指数（数据仓 index_daily 均已覆盖）
_IDX = [
    ("000001.SH", "上证指数"),
    ("399001.SZ", "深证成指"),
    ("399006.SZ", "创业板指"),
    ("000688.SH", "科创50"),
    ("000016.SH", "上证50"),
    ("000905.SH", "中证500"),
    ("000852.SH", "中证1000"),
]


def index_close():
    """一、指数收盘全景：涨跌/振幅/日内位置/成交额。"""
    out = {"rows": []}
    try:
        import datamart as dm

        days = pm._closed_trade_days()
        if len(days) < 3:
            return out
        d0, d1 = days[-1], days[-2]
        df = dm.read(
            "index_daily",
            start=d1,
            end=d0,
            codes=[c for c, _ in _IDX],
            fields=[
                "ts_code",
                "trade_date",
                "close",
                "high",
                "low",
                "pre_close",
                "pct_chg",
                "amount",
            ],
        )
        if df is None or df.empty:
            return out
        df["d8"] = df["trade_date"].astype(str).str[:8]
        dict(_IDX)
        for code, name in _IDX:
            r0 = df[(df["ts_code"] == code) & (df["d8"] == d0)]
            r1 = df[(df["ts_code"] == code) & (df["d8"] == d1)]
            if r0.empty:
                continue
            hi = float(r0["high"].iloc[0])
            lo = float(r0["low"].iloc[0])
            cl = float(r0["close"].iloc[0])
            rng = hi - lo
            out["rows"].append(
                {
                    "name": name,
                    "close": round(cl, 2),
                    "pct": round(float(r0["pct_chg"].iloc[0]), 2),
                    "amp": round((hi / lo - 1) * 100, 2) if lo else 0,
                    "pos": round((cl - lo) / rng * 100, 0) if rng else 50,
                    "amt_yi": round(float(r0["amount"].iloc[0]) / 1e5),
                    "amt_pct": round(
                        (float(r0["amount"].iloc[0]) / float(r1["amount"].iloc[0]) - 1)
                        * 100,
                        1,
                    )
                    if not r1.empty and float(r1["amount"].iloc[0])
                    else None,
                }
            )
        out["date"] = d0
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/afterhours.py", exc)
    return out


def industry_perf():
    """四、行业涨跌榜：当日按行业聚合（涨幅中位/上涨占比），Top5/Bottom5。"""
    out = {}
    try:
        import datamart as dm

        days = pm._closed_trade_days()
        if not days:
            return out
        d0 = days[-1]
        df = dm.read("daily", start=d0, end=d0, fields=["ts_code", "pct_chg"])
        sb = dm.read("stock_basic", fields=["ts_code", "industry"])
        if df is None or df.empty or sb is None or sb.empty:
            return out
        m = df.merge(sb[["ts_code", "industry"]], on="ts_code", how="left")
        m = m[m["industry"].notna() & (m["industry"] != "")]
        g = m.groupby("industry")
        st = g["pct_chg"].agg(["median", "count"])
        st["up_r"] = g["pct_chg"].apply(lambda s: (s > 0).mean() * 100)
        st = st[st["count"] >= 8].sort_values("median", ascending=False)
        out["date"] = d0
        out["n_ind"] = int(len(st))

        def _rows(sub, asc):
            s = sub.sort_values("median", ascending=asc).head(5)
            return [
                {
                    "ind": i,
                    "med": round(float(r["median"]), 2),
                    "up_r": round(float(r["up_r"]), 1),
                    "n": int(r["count"]),
                }
                for i, r in s.iterrows()
            ]

        out["top"] = _rows(st, False)
        out["bottom"] = _rows(st, True)
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/afterhours.py", exc)
    return out


def premarket_review(ic):
    """七、盘前预判复盘：最近盘前报告的 verdict/prob vs 当日上证实际。"""
    out = {}
    try:
        p = os.path.join(REPORTS, "premarket_latest.json")
        if not os.path.exists(p):
            return out
        pre = json.load(open(p, encoding="utf-8"))
        sh = next((r for r in ic.get("rows", []) if r.get("name") == "上证指数"), None)
        if not sh:
            return out
        ov = pre.get("overall")
        out["pre_ts"] = pre.get("ts", "—")
        out["overall"] = ov
        out["prob"] = pre.get("prob")
        out["verdict"] = pre.get("verdict", "—")
        out["sh_pct"] = sh["pct"]
        if ov is None:
            out["hit"] = None
        elif ov >= 10:
            out["hit"] = sh["pct"] > 0
        elif ov <= -10:
            out["hit"] = sh["pct"] < 0
        else:  # 中性：涨跌±0.5%内算贴合
            out["hit"] = abs(sh["pct"]) <= 0.5
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/afterhours.py", exc)
    return out


def run(push=False):
    ic = index_close()
    ip = industry_perf()
    mo = pm.money_effect()
    mg = pm.margin_sentiment()
    cb = pm.cb_sentiment()
    l4f = pm.industry_flow()
    try:
        l5 = pm.layer5_sentiment({})
    except Exception:
        l5 = {}
    rv = premarket_review(ic)
    sh = next((r for r in ic.get("rows", []) if r.get("name") == "上证指数"), {})
    data = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "ic": ic,
        "ip": ip,
        "money": mo,
        "margin": mg,
        "cb": cb,
        "l4f": l4f,
        "l5": l5,
        "review": rv,
        "sh": sh,
    }
    with open(
        os.path.join(REPORTS, "afterhours_latest.json"), "w", encoding="utf-8"
    ) as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    write_page(data)
    if push:
        doc_url = _doc_sync(build_doc_md(data))
        push_card(data, doc_url)
    print(
        "AFTERHOURS %s 上证 %+.2f%% 成交 %s 亿 涨停 %s"
        % (
            ic.get("date", "?"),
            sh.get("pct", 0) or 0,
            (mo.get("amt_yi") or "—"),
            mo.get("limit_up", "—"),
        )
    )
    return data


def _cls(v):
    """A股口径：涨红跌绿。"""
    return "buy" if v > 0 else ("avoid" if v < 0 else "")


def write_page(d):
    parts = []
    sh = d["sh"]
    mo = d["money"]
    parts.append(
        "<h1>🌇 盘后复盘 <span class='dim'>%s</span></h1>"
        "<div class='env'>%s 收盘 <b style='font-size:22px'>%s</b>"
        "（<b class='%s'>%+.2f%%</b>）· 两市成交 <b>%s 亿</b>%s · "
        "涨停 <b class='buy'>%s</b> / 跌停 <b class='avoid'>%s</b>"
        "%s</div>"
        % (
            d["ts"],
            sh.get("close", "—"),
            sh.get("pct", 0) or 0,
            _cls(sh.get("pct", 0) or 0),
            sh.get("pct", 0) or 0,
            mo.get("amt_yi", "—"),
            "（环比 %+.1f%%）" % mo["amt_pct"] if mo.get("amt_pct") is not None else "",
            mo.get("limit_up", "—"),
            mo.get("limit_dn", "—"),
            " · 情绪 %s" % mo["th_label"] if mo.get("th_label") else "",
        )
    )
    # 一、指数收盘全景
    ic = d["ic"]
    if ic.get("rows"):
        trs = "".join(
            "<tr><td>%s</td><td>%s</td>"
            "<td class='%s'>%+.2f%%</td><td>%.2f%%</td><td>%d%%</td>"
            "<td class='dim'>%s 亿%s</td></tr>"
            % (
                r["name"],
                r["close"],
                _cls(r["pct"]),
                r["pct"],
                r["amp"],
                r["pos"],
                r["amt_yi"],
                "（%+.1f%%）" % r["amt_pct"] if r.get("amt_pct") is not None else "",
            )
            for r in ic["rows"]
        )
        parts.append(
            "<h2>一、指数收盘全景（%s）</h2><table><tr><th>指数</th>"
            "<th>收盘</th><th>涨跌</th><th>振幅</th><th>日内位置</th>"
            "<th>成交额</th></tr>%s</table>"
            "<div class='dim'>日内位置=收盘在当日高低区间的百分位"
            "（100%%=收在最高）；成交额环比为昨日对比</div>"
            % (ic.get("date", "—"), trs)
        )
    # 二、量能与资金
    mo_vol = []
    if mo.get("amt_yi"):
        mo_vol.append(
            "两市成交 <b>%s 亿</b>（环比 %+.1f%%）"
            % (mo["amt_yi"], mo.get("amt_pct") or 0)
        )
    for k, lb in (("amt_ma5", "5日均"), ("amt_ma20", "20日均")):
        if d["money"].get(k):
            mo_vol.append("%s %s 亿" % (lb, mo[k]))
    if mo.get("vol_state"):
        mo_vol.append(
            "<b class='%s'>%s</b>（vs 5日均 %+.1f%%）"
            % (
                _cls(
                    1
                    if mo["vol_state"] == "放量"
                    else (-1 if mo["vol_state"] == "缩量" else 0)
                ),
                mo["vol_state"],
                mo.get("vol_pct", 0),
            )
        )
    mg = d.get("margin") or {}
    if mg:
        mo_vol.append(
            "两融 <b>%s 亿</b>（5日 %+.0f 亿 · 近3日 %s 亿 · "
            "T+1，%s）"
            % (
                mg.get("rzye_yi", "—"),
                mg.get("chg5_yi", 0),
                " / ".join("%+d" % x for x in mg.get("chg_recent", [])),
                mg.get("date", "—"),
            )
        )
    parts.append(
        "<h2>二、量能与资金</h2><div class='env'>%s</div>" % " · ".join(mo_vol)
        if mo_vol
        else ""
    )
    l4f = d.get("l4f") or {}
    if l4f.get("date"):

        def _ind_a(ind):
            return "<a href='/bt/ind_drill?ind=%s&date=%s'>%s ↘</a>" % (
                html.escape(ind, quote=True),
                l4f["date"],
                html.escape(ind),
            )

        fr = [("净流入", r, "buy") for r in l4f.get("top", [])] + [
            ("净流出", r, "avoid") for r in l4f.get("bottom", [])
        ]
        trs = "".join(
            "<tr><td>%s</td><td>%s</td><td class='%s'>%+.1f 亿</td>"
            "<td class='dim'>%d 家</td></tr>"
            % (tag, _ind_a(r["ind"]), c, r["yi"], r["n"])
            for tag, r, c in fr
        )
        parts.append(
            "<h2>三、行业主力资金（%s）· 全市场净额 %+.1f 亿</h2>"
            "<table><tr><th>方向</th><th>行业</th><th>主力净额</th>"
            "<th>家数</th></tr>%s</table>" % (l4f["date"], l4f.get("total_yi", 0), trs)
        )
    # 四、赚钱效应复盘
    if mo.get("date"):
        ex = [
            "炸板率 <b class='%s'>%s%%</b>（炸 %d / 触板 %d）"
            % (
                "avoid" if (mo.get("broke_rate") or 0) >= 30 else "buy",
                mo.get("broke_rate", 0),
                mo.get("broke_n", 0),
                mo.get("broke_n", 0) + mo.get("limit_up", 0),
            )
        ]
        if mo.get("ylu_avg") is not None:
            ex.append(
                "昨涨停今表现 <b class='%s'>%+.2f%%</b>（中位 "
                "%+.2f%%）" % (_cls(mo["ylu_avg"]), mo["ylu_avg"], mo["ylu_med"])
            )
        if mo.get("promote_rate") is not None:
            ex.append("晋级率 <b>%s%%</b>" % mo["promote_rate"])
        if mo.get("th_label") is not None:
            ex.append(
                "温度计（60日分位）：涨停 P%d · 炸板 P%d → 情绪 "
                "<b class='%s'>%s</b>"
                % (
                    mo["th_lu"],
                    mo["th_br"],
                    "buy"
                    if mo["th_label"] in ("亢奋", "回暖")
                    else ("avoid" if mo["th_label"] in ("冰点", "转弱") else ""),
                    mo["th_label"],
                )
            )
        cb = d.get("cb") or {}
        if cb.get("date"):
            ex.append(
                "转债等权 <b class='%s'>%+.2f%%</b> · 上涨占比 "
                "%s%% · 成交 %s 亿%s"
                % (
                    _cls(cb.get("eq_pct") or 0),
                    cb.get("eq_pct", 0),
                    cb.get("up_ratio", 0),
                    cb.get("amt_yi", "—"),
                    "（vs 5日均 %+.1f%%）" % cb["amt_vs_ma5"]
                    if cb.get("amt_vs_ma5") is not None
                    else "",
                )
            )
        parts.append(
            "<h2>四、赚钱效应复盘（%s）</h2><div class='env'>"
            "%s</div>" % (mo["date"], " · ".join(ex))
        )
        # 连板天梯
        lad = mo.get("ladder") or []
        if lad:
            ltrs = "".join(
                "<tr><td><b class='buy'>%d 板</b></td><td>%d 家</td>"
                "<td>%s</td></tr>"
                % (
                    lv["k"],
                    lv["n"],
                    " · ".join(
                        "%s（%+.2f%%，%s亿）"
                        % (pm._stock_a(t["code"], t["name"]), t["pct"], t["amt_yi"])
                        for t in lv["top"]
                    ),
                )
                for lv in lad[:8]
            )
            parts.append(
                "<h2>五、连板天梯</h2><table><tr><th>板高</th><th>家数"
                "</th><th>代表个股（额Top3，点击下钻）</th></tr>%s</table>" % ltrs
            )
        trs = "".join(
            "<tr><td>%s</td><td>%s</td><td class='buy'>%+.2f%%</td>"
            "<td>%d 连板</td>%s%s%s<td class='dim'>%s 亿</td></tr>"
            % (
                pm._stock_a(t["code"]),
                html.escape(t["name"]),
                t["pct"],
                t["streak"],
                pm._style_td(t),
                pm._dim_tds(t, mo["date"]),
                pm._tv_tds(t),
                t["amt_yi"],
            )
            for t in mo.get("top", [])
        )
        if trs:
            parts.append(
                "<h2>六、涨停标的（额Top5）</h2><table><tr><th>代码</th>"
                "<th>名称</th><th>涨幅</th><th>连板</th><th>方式</th>"
                "<th>板块</th><th>总市值(亿)</th><th>龙虎榜</th>"
                "<th>换手/量比</th><th>成交额</th></tr>%s</table>" % trs
            )
    # 七、行业涨跌榜
    ip = d.get("ip") or {}
    if ip.get("top"):
        irows = [("领涨", r, _cls(r["med"])) for r in ip["top"]] + [
            ("领跌", r, _cls(r["med"])) for r in ip["bottom"]
        ]
        trs = "".join(
            "<tr><td>%s</td><td>%s</td><td class='%s'>%+.2f%%</td>"
            "<td class='dim'>涨%s%% · %d家</td></tr>"
            % (
                tag,
                "<a href='/bt/ind_drill?ind=%s&date=%s'>%s ↘</a>"
                % (
                    html.escape(r["ind"], quote=True),
                    ip.get("date", ""),
                    html.escape(r["ind"]),
                ),
                c,
                r["med"],
                r["up_r"],
                r["n"],
            )
            for tag, r, c in irows
        )
        parts.append(
            "<h2>七、行业涨跌榜（%s · %d 个行业 · 中位数口径）</h2>"
            "<table><tr><th>方向</th><th>行业</th><th>涨幅中位</th>"
            "<th>上涨占比</th></tr>%s</table>"
            % (ip.get("date", "—"), ip.get("n_ind", 0), trs)
        )
    # 八、盘前预判复盘
    rv = d.get("review") or {}
    if rv:
        hit = rv.get("hit")
        parts.append(
            "<h2>八、盘前预判复盘</h2><div class='env'>盘前（%s）：<b>"
            "%s</b> · 开盘溢价概率 %s%%<br>实际：上证 <b class='%s'>"
            "%+.2f%%</b> → %s</div>"
            % (
                rv.get("pre_ts", "—"),
                rv.get("verdict", "—"),
                rv.get("prob", "—"),
                _cls(rv.get("sh_pct") or 0),
                rv.get("sh_pct") or 0,
                "<b class='buy'>✓ 命中</b>"
                if hit
                else "<b class='avoid'>✗ 偏差</b>"
                if hit is not None
                else "—",
            )
        )
    parts.append(
        "<div class='dim'>口径与数据源同盘前报告（daily/index_daily/"
        "moneyflow/margin/cb_daily 数据仓现算 + JEV/TDX）；涨停判定阈值 "
        "10%%/20%%/30%%（ST 5%%，幅值上限 31.5%%，新股剔除）；"
        "每日 09:10（北京）/ 03:10（巴黎）自动生成；"
        "本报告为研究工具，不构成投资建议</div>"
    )
    with open(os.path.join(REPORTS, "afterhours.html"), "w", encoding="utf-8") as f:
        f.write(RR._page("盘后复盘", RR._top_nav("afterhours") + "".join(parts)))
    print("PAGE afterhours.html")


def build_doc_md(d):
    """盘后复盘完整 markdown（飞书镜像文档用）。"""
    _sh, mo, ic = d["sh"], d["money"], d["ic"]
    L = [
        "# 🌇 盘后复盘 · %s" % ic.get("date", "—"),
        "",
        "> 生成 %s · 数据仓现算 · 研究工具不构成投资建议" % d["ts"],
        "",
        "## 一、指数收盘全景",
        "",
        "| 指数 | 收盘 | 涨跌 | 振幅 | 日内位置 | 成交额 |",
        "|---|---|---|---|---|---|",
    ]
    for r in ic.get("rows", []):
        L.append(
            "| %s | %s | %+.2f%% | %.2f%% | %d%% | %s 亿%s |"
            % (
                r["name"],
                r["close"],
                r["pct"],
                r["amp"],
                r["pos"],
                r["amt_yi"],
                "（%+.1f%%）" % r["amt_pct"] if r.get("amt_pct") is not None else "",
            )
        )
    L += ["", "## 二、量能与资金", ""]
    if mo.get("amt_yi"):
        L.append(
            "- 两市成交 **%s 亿**（环比 %+.1f%% · 5日均 %s · "
            "20日均 %s · %s）"
            % (
                mo["amt_yi"],
                mo.get("amt_pct") or 0,
                mo.get("amt_ma5", "—"),
                mo.get("amt_ma20", "—"),
                mo.get("vol_state", "—"),
            )
        )
    mg = d.get("margin") or {}
    if mg:
        L.append(
            "- 两融 **%s 亿**（5日 %+.0f 亿 · 近3日 %s 亿 · T+1，%s）"
            % (
                mg.get("rzye_yi", "—"),
                mg.get("chg5_yi", 0),
                " / ".join("%+d" % x for x in mg.get("chg_recent", [])),
                mg.get("date", "—"),
            )
        )
    l4f = d.get("l4f") or {}
    if l4f.get("date"):
        L += [
            "",
            "### 行业主力资金（%s · 全市场净额 %+.1f 亿）"
            % (l4f["date"], l4f.get("total_yi", 0)),
            "",
            "- 净流入："
            + "、".join("%s %+.1f亿" % (r["ind"], r["yi"]) for r in l4f.get("top", [])),
            "- 净流出："
            + "、".join(
                "%s %+.1f亿" % (r["ind"], r["yi"]) for r in l4f.get("bottom", [])
            ),
        ]
    L += [
        "",
        "## 三、赚钱效应复盘（%s）" % mo.get("date", "—"),
        "",
        "- 涨停 **%s** / 跌停 %s · 上涨 %s / 下跌 %s"
        % (
            mo.get("limit_up", "—"),
            mo.get("limit_dn", "—"),
            mo.get("up", "—"),
            mo.get("dn", "—"),
        ),
        "- 炸板率 **%s%%**（炸 %d / 触板 %d）· 20cm 占比 %s%%"
        % (
            mo.get("broke_rate", 0),
            mo.get("broke_n", 0),
            mo.get("broke_n", 0) + mo.get("limit_up", 0),
            mo.get("cm20_share", 0),
        ),
    ]
    if mo.get("ylu_avg") is not None:
        L.append(
            "- 昨涨停今表现 %+.2f%%（中位 %+.2f%%）· 晋级率 %s%%"
            % (mo["ylu_avg"], mo["ylu_med"], mo.get("promote_rate", 0))
        )
    if mo.get("th_label") is not None:
        L.append(
            "- 温度计（60日分位）：涨停 P%d · 炸板 P%d → 情绪 **%s**"
            % (mo["th_lu"], mo["th_br"], mo["th_label"])
        )
    cb = d.get("cb") or {}
    if cb.get("date"):
        L.append(
            "- 转债等权 %+.2f%% · 上涨占比 %s%% · 成交 %s 亿%s"
            % (
                cb.get("eq_pct", 0),
                cb.get("up_ratio", 0),
                cb.get("amt_yi", "—"),
                "（vs 5日均 %+.1f%%）" % cb["amt_vs_ma5"]
                if cb.get("amt_vs_ma5") is not None
                else "",
            )
        )
    lad = mo.get("ladder") or []
    if lad:
        L += ["", "## 四、连板天梯", ""]
        for lv in lad[:8]:
            L.append(
                "- **%d 板**（%d 家）：%s"
                % (
                    lv["k"],
                    lv["n"],
                    "、".join(
                        "%s %+.2f%%（%s亿）" % (t["name"], t["pct"], t["amt_yi"])
                        for t in lv["top"]
                    ),
                )
            )
    ip = d.get("ip") or {}
    if ip.get("top"):
        L += [
            "",
            "## 五、行业涨跌榜（中位数口径，%d 行业）" % ip.get("n_ind", 0),
            "",
            "- 领涨："
            + "、".join("%s %+.2f%%" % (r["ind"], r["med"]) for r in ip["top"]),
            "- 领跌："
            + "、".join("%s %+.2f%%" % (r["ind"], r["med"]) for r in ip["bottom"]),
        ]
    rv = d.get("review") or {}
    if rv:
        hit = rv.get("hit")
        L += [
            "",
            "## 六、盘前预判复盘",
            "",
            "- 盘前（%s）：%s · 溢价概率 %s%%｜实际：上证 %+.2f%% → **%s**"
            % (
                rv.get("pre_ts", "—"),
                rv.get("verdict", "—"),
                rv.get("prob", "—"),
                rv.get("sh_pct") or 0,
                "✓ 命中" if hit else ("✗ 偏差" if hit is not None else "—"),
            ),
        ]
    L += [
        "",
        "---",
        "*数据源：数据仓(daily/index_daily/moneyflow/margin/cb_daily) "
        "+ JEV/TDX · 与门户 /afterhours 同源 · 涨停判定阈值 10%/20%/30%"
        "（ST 5%，上限 31.5%，新股剔除）*",
    ]
    return "\n".join(L)


def _cli_error_brief(out):
    """把 lark-cli 的 JSON 错误压成一行可读摘要（鉴权/权限/参数）。

    历史教训（2026-10-25 K-ORG-20261025-DOCSYNC）：原来只打印 out[:120]
    原文，token_missing 与 app_scope_not_applied 都被截成看似同类的
    validation 报错，排查时白走一大圈。摘要只取 type/subtype/code/message，
    既短又保留病因。
    """
    text = (out or "").strip()
    try:
        payload = json.loads(text)
        err = payload.get("error") or {}
        kind = err.get("type") or "?"
        subtype = err.get("subtype") or "?"
        code = err.get("code")
        head = "%s/%s" % (kind, subtype)
        if code:
            head += "(%s)" % code
        return "%s %s" % (head, str(err.get("message") or "")[:160])
    except Exception:
        return text.replace("\n", " ")[:200]


def _doc_sync(md):
    """镜像复盘到飞书文档（token 独立于盘前，防互相覆盖）：
    无则创建，有则整体覆盖；失败不影响推卡。"""
    exe = pm._lark_exe()
    if not exe:
        print("DOC_SYNC SKIP no lark-cli")
        return None
    # 2026-10-25 SEC 收口（EXEC-R16）：doc URL 读 env/本地私有覆盖，见 config_local.py
    from config_local import load_config, save_report_url

    cfg = load_config()
    doc = (cfg.get("report") or {}).get("afterhours_doc_url", "")
    tmp = os.path.join(pm.HERE, "_aft_doc.md")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(md)
        if doc:
            r = subprocess.run(
                [
                    exe,
                    "docs",
                    "+update",
                    "--as",
                    "user",
                    "--doc",
                    doc,
                    "--command",
                    "overwrite",
                    "--doc-format",
                    "markdown",
                    "--content",
                    "@./_aft_doc.md",
                    "--format",
                    "json",
                ],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=pm.HERE,
            )
            out = (r.stdout or "") + (r.stderr or "")
            if '"ok": true' in out or '"ok":true' in out:
                print("DOC_SYNC UPDATE OK")
                return doc
            print("DOC_SYNC UPDATE FAIL, keep stored url:", _cli_error_brief(out))
            return doc
        r = subprocess.run(
            [
                exe,
                "docs",
                "+create",
                "--as",
                "user",
                "--title",
                "🌇 盘后复盘（每日自动更新）",
                "--doc-format",
                "markdown",
                "--content",
                "@./_aft_doc.md",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=pm.HERE,
        )
        out = (r.stdout or "") + (r.stderr or "")
        try:
            j = json.loads(r.stdout or "{}")
            doc = (j.get("data") or {}).get("document") or {}
            url = doc.get("url") or (j.get("data") or {}).get("url") or ""
        except Exception:
            url = ""
        if url:
            # 2026-10-25 SEC 收口（EXEC-R16）：写私有覆盖，不回写 tracked config.json
            save_report_url("afterhours_doc_url", url)
            print("DOC_SYNC CREATE OK", url[:60])
            return url
        print("DOC_SYNC FAIL", out[:200])
        return None
    except Exception as e:
        print("DOC_SYNC ERR", str(e)[:120])
        return None
    finally:
        try:
            os.remove(tmp)
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/afterhours.py", exc)


def push_card(d, doc_url=None):
    """飞书卡片：数据自包含摘要（手机端可读），附镜像文档链接。"""
    import runner

    sh, mo, ic = d["sh"], d["money"], d["ic"]
    idx_line = " · ".join(
        "%s%+.2f%%" % (r["name"].replace("指数", ""), r["pct"])
        for r in ic.get("rows", [])
        if r.get("pct") is not None
    )[:180]
    lines = ["**%s**｜%s" % (ic.get("date", "—"), idx_line or "—")]
    if mo.get("amt_yi"):
        lines.append(
            "两市成交 **%s 亿**（%+.1f%% · %s）"
            % (mo["amt_yi"], mo.get("amt_pct") or 0, mo.get("vol_state", "—"))
        )
    lad = mo.get("ladder") or []
    top_bd = (
        (" · 最高 %d 板（%s）" % (lad[0]["k"], lad[0]["top"][0]["name"])) if lad else ""
    )
    lines.append(
        "涨停 **%s** / 跌停 %s · 炸板率 %s%%%s"
        % (
            mo.get("limit_up", "—"),
            mo.get("limit_dn", "—"),
            mo.get("broke_rate", 0),
            top_bd,
        )
    )
    if mo.get("th_label") is not None:
        lines.append(
            "温度计 P%d/P%d → 情绪 **%s**" % (mo["th_lu"], mo["th_br"], mo["th_label"])
        )
    mg = d.get("margin") or {}
    if mg:
        lines.append(
            "两融 %s 亿（5日 %+.0f 亿）"
            % (mg.get("rzye_yi", "—"), mg.get("chg5_yi", 0))
        )
    ip = d.get("ip") or {}
    if ip.get("top"):
        lines.append(
            "领涨：%s"
            % "、".join("%s%+.2f%%" % (r["ind"], r["med"]) for r in ip["top"][:3])
        )
    rv = d.get("review") or {}
    if rv:
        hit = rv.get("hit")
        lines.append(
            "盘前预判复盘：%s → **%s**"
            % (
                rv.get("verdict", "—"),
                "✓ 命中" if hit else ("✗ 偏差" if hit is not None else "—"),
            )
        )
    links = []
    if doc_url:
        links.append("[📄 飞书镜像报告](%s)" % doc_url)
    md_els = [
        {
            "tag": "markdown",
            "content": "**上证 %+.2f%%（%s）**\n%s"
            % (sh.get("pct", 0) or 0, sh.get("close", "—"), "\n".join(lines)),
        }
    ]
    if links:
        md_els.append({"tag": "markdown", "content": " · ".join(links)})
    md_els.append(
        {
            "tag": "markdown",
            "text_size": "notation",
            "content": "<font color='grey'>数据仓现算 · "
            "T+1 早 09:10（北京）自动生成 · %s</font>" % d["ts"],
        }
    )
    card = {
        "schema": "2.0",
        "config": {"update_multi": True, "width_mode": "default"},
        "header": {
            "title": {"tag": "plain_text", "content": "🌇 盘后复盘 · T日收盘"},
            "subtitle": {
                "tag": "plain_text",
                "content": "上证 %+.2f%% · 成交 %s 亿 · "
                "涨停 %s"
                % (
                    sh.get("pct", 0) or 0,
                    mo.get("amt_yi", "—"),
                    mo.get("limit_up", "—"),
                ),
            },
            "template": "turquoise",
        },
        "body": {"elements": md_els},
    }
    ok = runner.push_feishu(json.dumps(card, ensure_ascii=False), card=True)
    print("PUSH", ok)


def main():
    """每日任务入口：幂等补生成。
    日线 T+1 落仓（时点不定），故任务挂每日早晨，靠门禁自适应：
    - 数据未就绪（index_daily 未到最近收盘日）→ skip，次日自动补
    - 最近收盘日已生成过 → skip
    --force 无视门禁强制重生成。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="跳过门禁强制重生成")
    ap.add_argument(
        "--push", action="store_true", help="生成后推送飞书（卡片+镜像文档）"
    )
    a = ap.parse_args()
    days = pm._closed_trade_days()
    if not days:
        print("AFTERHOURS skip：交易日历不可用")
        return
    latest = None
    try:
        with open(
            os.path.join(REPORTS, "afterhours_latest.json"), encoding="utf-8"
        ) as f:
            latest = (json.load(f).get("ic") or {}).get("date")
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/afterhours.py", exc)
    if not a.force:
        if latest == days[-1]:
            print("AFTERHOURS skip：%s 复盘已生成" % latest)
            return
        ic = index_close()
        if ic.get("date") != days[-1]:
            print(
                "AFTERHOURS skip：日线未落仓（最新 %s，待 %s）"
                % (ic.get("date", "无"), days[-1])
            )
            return
    run(push=a.push)


if __name__ == "__main__":
    main()
