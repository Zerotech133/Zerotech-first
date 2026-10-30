# -*- coding: utf-8 -*-
"""api_backtest 拆分 B4（2026-10-30 P71）：回测页 B 族渲染（weak/cross/nine_dim/algo_market/fin/strategies/sa）。

自 portal/api_backtest.py Handler 方法纯移动（方法体零改动，签名不变，首参 self）。
"""
import json
import logging
import os
import sys

from api_common import (
    MKT_LABEL, QBT_DIR, REPORTS_DIR, ROOT, STATION, _env_market_key,
    _HERMES_FAB_HTML, _read_json, log_exc, pill_nav)

from api_backtest import _sa_hist_read, strategies_data


def _weak_cases_page(self):
        """📚 弱于板块案例库：在案状态流摘要 + 案例档案 + 最新扫描（纯读，
        不触发任何重算/拉取；数据源 reports/weak_track_*.jsonl、
        weak_cases/*、weak_scan_*.csv，均 weak 产线落盘产物）。"""
        import html as _h
        import json as _j
        import csv as _csv
        import glob as _g
        from datetime import datetime as _dt
        e = _h.escape
        REP = os.path.join(STATION, "reports")
        WC = os.path.join(REP, "weak_cases")

        def _last(fp):
            last, n = None, 0
            try:
                with open(fp, encoding="utf-8") as f:
                    for ln in f:
                        ln = ln.strip()
                        if not ln:
                            continue
                        try:
                            last = _j.loads(ln.lstrip("\ufeff"))
                            n += 1
                        except Exception as exc:
                            logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
            return last, n

        def _rev_md(c6):
            m = sorted(_g.glob(os.path.join(WC, "weak_review_%s_*.md" % c6)))
            return os.path.basename(m[-1]) if m else ""

        td = "border-collapse:collapse;font-size:13px"
        th = ("background:#161b22;border:1px solid #30363d;padding:4px 8px;"
              "text-align:left")
        tdd = "border:1px solid #30363d;padding:4px 8px"

        # ① 在案（活跃状态流）
        rows = []
        for fp in sorted(_g.glob(os.path.join(REP, "weak_track_*.jsonl"))):
            if ".closed_" in os.path.basename(fp):
                continue
            st, n = _last(fp)
            if not st:
                continue
            c6 = os.path.basename(fp)[len("weak_track_"):-len(".jsonl")]
            code = st.get("code", c6)
            ex = st.get("ex250")
            trig = st.get("trig") or []
            rev = _rev_md(c6)
            rows.append(
                "<tr><td style='%s'><b>%s</b></td><td style='%s'>%s · %s</td>"
                "<td style='%s'>%s</td><td style='%s'>%s</td>"
                "<td style='%s'>%s</td><td style='%s'>%d 次</td>"
                "<td style='%s'>%s%s</td></tr>" % (
                    tdd, e(code), tdd, e(str(st.get("px_d", "?"))[5:]),
                    st.get("px"), tdd,
                    ("%+.1fpp" % ex) if ex is not None else "—", tdd,
                    e("/".join(str(x).replace("（均线缠绕）", "·缠")[:8]
                               for x in (st.get("trend") or ["—", "—", "—"]))),
                    tdd,
                    ("<span style='color:#f85149'>⚠ %s</span>"
                     % e("；".join(trig))) if trig else "—", tdd, n, tdd,
                    ("<a href='/bt-weak-md?f=%s'>档案</a>" % e(rev))
                    if rev else "—",
                    " ｜ <a href='/bt-linkage?code=%s'>联动</a>" % e(code)))
        live_html = (
            "<table style='%s'><tr><th style='%s'>案例</th><th style='%s'>"
            "数据日·现价</th><th style='%s'>超额250</th><th style='%s'>趋势 "
            "个/板/沪</th><th style='%s'>触发</th><th style='%s'>运行</th>"
            "<th style='%s'>链接</th></tr>%s</table>" % (
                td, th, th, th, th, th, th, th,
                "".join(rows) or ("<tr><td style='%s' colspan='7'>无在案"
                                  "</td></tr>" % tdd))) if rows else \
            "<div style='color:#8b949e'>无在案案例</div>"

        # ② 已结案（closed 状态流 + close 报告）
        cl = sorted(_g.glob(os.path.join(REP, "weak_track_*.closed_*.jsonl")))
        cl_rows = []
        for fp in cl:
            base = os.path.basename(fp)
            c6 = base[len("weak_track_"):].split(".")[0]
            md = sorted(_g.glob(os.path.join(WC, "weak_close_%s_*.md" % c6)))
            cl_rows.append(
                "<li>%s — <a href='/bt-weak-md?f=%s'>结案报告</a></li>"
                % (e(c6), e(os.path.basename(md[-1]))) if md
                else "<li>%s</li>" % e(c6))
        closed_html = ("<ul style='font-size:13px'>%s</ul>"
                       % "".join(cl_rows)) if cl_rows else \
            "<div style='color:#8b949e'>尚无结案</div>"

        # ③ 最新扫描（重点候选 ★ 优先展示——二道筛 focus 列）
        scan_html = "<div style='color:#8b949e'>尚无扫描</div>"
        sc = sorted(_g.glob(os.path.join(REP, "weak_scan_*.csv")))
        if sc:
            fp = sc[-1]
            with open(fp, encoding="utf-8-sig") as f:
                hits = list(_csv.DictReader(f))
            n_focus = sum(1 for h in hits
                          if (h.get("focus") or "").strip())
            focus = [h for h in hits if (h.get("focus") or "").strip()]
            plain = [h for h in hits if not (h.get("focus") or "").strip()]

            def _fpri(foc):
                # 二道筛价值优先级：强转弱＞强失速＞深度垫底（与卡片一致）
                if "强转弱" in foc:
                    return 0
                if "强失速" in foc:
                    return 1
                return 2

            focus.sort(key=lambda h: (
                _fpri((h.get("focus") or "").strip()),
                float(h.get("ex") or 0)))   # 同级按超额最差
            top = (focus + plain)[:10]

            def _srow(h):
                foc = (h.get("focus") or "").strip()
                star = ("<span style='color:#e3a008'>★%s</span>"
                        % e(foc)) if foc else \
                       "<span style='color:#586e75'>—</span>"
                nm = e(h.get("name", ""))
                if foc.startswith("强转弱"):
                    nm = ("<span style='color:#f85149'>%s</span>" % nm)
                return ("<tr><td style='%s'>%s</td><td style='%s'>%s</td>"
                        "<td style='%s'>%s</td><td style='%s'>%s</td>"
                        "<td style='%s'>%.1f</td>"
                        "<td style='%s'>%.0f%%（%s）</td></tr>" % (
                            tdd, e(h.get("code", "")), tdd, nm,
                            tdd, e(h.get("industry", "")), tdd, star, tdd,
                            float(h.get("ex") or 0), tdd,
                            float(h.get("pct_in") or 0),
                            e(h.get("rank_in", ""))))

            thead = ("<tr><th style='%s'>代码</th><th style='%s'>名称</th>"
                     "<th style='%s'>行业</th><th style='%s'>重点</th>"
                     "<th style='%s'>超额pp</th>"
                     "<th style='%s'>板块内分位</th></tr>"
                     % (th, th, th, th, th, th))
            # 非重点参考小表（超额最差前 5）：主表被重点占满时保底可见
            plain_html = ""
            if plain and len(focus) >= 10:
                plain_html = (
                    "<div style='font-size:12px;color:#8b949e;margin:10px 0 "
                    "4px'>非重点参考（超额最差前 5）</div>"
                    "<table style='%s'>%s%s</table>" % (
                        td, thead, "".join(_srow(h) for h in plain[:5])))
            scan_html = (
                "<div style='font-size:12px;color:#8b949e;margin:4px 0'>"
                "%s ｜ 候选 %d 只 ｜ 重点 ★%d（二道筛）｜ %s</div>"
                "<table style='%s'>%s%s</table>"
                "<div style='font-size:12px;color:#8b949e'>重点候选优先（强"
                "转弱标红，余按超额最差补足），全量见 CSV</div>%s" % (
                    e(os.path.basename(fp)), len(hits), n_focus,
                    e(_dt.fromtimestamp(os.path.getmtime(fp)).strftime(
                        "%Y-%m-%d %H:%M")),
                    td, thead, "".join(_srow(h) for h in top), plain_html))

        self._html(
            # K-STOCK-052 界面修复：此前输出裸 HTML 片段（无 DOCTYPE/CSS 壳，
            # 浏览器渲染成白底裸内容=「还没有界面」）；补全页壳+域导航，
            # 与兄弟页（bt-qpzm/bt-9dim）同口径。
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,"
            "initial-scale=1'><title>📚 弱于板块案例库</title>"
            + self._COCKPIT_CSS + "</head><body>"
            + pill_nav("cases")
            + "<h1>📚 弱于板块案例库 <span style='font-size:14px;"
            "color:#8b949e'>weak 产线：扫描→建案→归因→跟踪→结案 · "
            "影子观察</span></h1>"
            "<h2 style='margin:14px 0 6px'>① 在案案例（每日 15:30 "
            "WeakTrackCheck 自动监测）</h2>%s"
            "<h2 style='margin:14px 0 6px'>② 已结案</h2>%s"
            "<h2 style='margin:14px 0 6px'>③ 最新信号2 扫描（每周一 15:35 "
            "WeakScan）</h2>%s"
            "<div style='color:#8b949e;font-size:12px;margin-top:16px;"
            "border-top:1px solid #30363d;padding-top:8px'>本页纯读展示，"
            "数据为产线落盘产物；仅影子复盘，不构成买卖依据。</div>"
            "</body></html>"
            % (live_html, closed_html, scan_html))


def _cross_page(self):
        import html as _h
        import sys as _sy
        e = _h.escape
        if QBT_DIR not in _sy.path:
            _sy.path.insert(0, QBT_DIR)
        try:
            import cross_poll
            d = cross_poll.build_cross()
        except Exception as ex:
            self._html("<body>%s<h1>🎯 多策略共振</h1>"
                       "<div class='dim'>聚合器异常：%s</div></body>"
                       % (pill_nav("cross"), e(str(ex))))
            return

        mkt_q = ""
        try:
            from urllib.parse import urlparse, parse_qs
            mkt_q = (parse_qs(urlparse(self.path).query)
                     .get("m", [""])[0]).upper()
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)

        def _in_mkt(st):
            if not mkt_q:
                return True
            return str(st.get("market", "")).upper() == mkt_q

        def _hit_badges(st):
            sp = []
            for h in st["hits"]:
                dc = {"long": "#f85149", "short": "#3fb950",
                      "neutral": "#8b949e"}[h["dir"]]
                sp.append(
                    "<a href='%s' style='display:inline-block;margin:1px "
                    "3px 1px 0;padding:2px 8px;border-radius:10px;font-size:"
                    "12px;text-decoration:none;border:1px solid %s;color:%s' "
                    "title='下钻到 %s'>%s %s</a>"
                    % (e(h["url"]), dc, dc, e(h["label"]),
                       e(h["badge"]), e(h["label"])))
            return "".join(sp)

        def _stock_link(st):
            return ("<a href='/kg/stock/%s'><b>%s</b></a>"
                    "<div class='dim'>%s · %s</div>"
                    % (e(st["code"]), e(st["name"] or st["code"]),
                       e(st["code"]), e(st.get("market") or "")))

        stocks = [s for s in d["stocks"] if _in_mkt(s)]
        multi = [s for s in stocks if s["n_hit"] >= 2]
        mixed = [s for s in multi if s["state"] == "mixed"]
        longs = [s for s in multi
                 if s["state"] == "long" and s["n_long"] >= 2]
        shorts = [s for s in multi
                  if s["state"] == "short" and s["n_short"] >= 2]

        # ① 方向冲突榜（最高优先：持仓风险提示）
        tr_mixed = "".join(
            "<tr><td>%s</td><td>%s</td></tr>"
            % (_stock_link(s), _hit_badges(s)) for s in mixed) or (
            "<tr><td colspan='2' class='dim'>当前无多空分歧标的</td></tr>")
        blk_mixed = (
            "<details class='sec' open><summary><h2>⚠️ 方向冲突 · 多策略"
            "多空分歧（%d）</h2><span class='dim'>持仓重点排查</span>"
            "</summary><div class='sec-body'>"
            "<table class='sticky'><tr><th>标的</th>"
            "<th>各策略最新判断（点徽标下钻）</th></tr>%s</table>"
            "</div></details>" % (len(mixed), tr_mixed))

        # ② 共振做多榜
        tr_long = "".join(
            "<tr><td>%s</td><td><b style='color:#f85149'>%d 策略看多</b>"
            "</td><td>%s</td></tr>"
            % (_stock_link(s), s["n_long"], _hit_badges(s))
            for s in longs[:50]) or (
            "<tr><td colspan='3' class='dim'>当前无 ≥2 策略共振做多的标的"
            "</td></tr>")
        blk_long = (
            "<details class='sec' open><summary><h2>🔥 共振做多 · ≥2 策略"
            "同时看多（%d）</h2></summary><div class='sec-body'>"
            "<table class='sticky'><tr><th>标的</th><th>共振</th>"
            "<th>策略徽标（点徽标下钻）</th></tr>%s</table>"
            "</div></details>" % (len(longs), tr_long))

        # ③ 共振预警榜
        tr_short = "".join(
            "<tr><td>%s</td><td><b style='color:#3fb950'>%d 策略看空</b>"
            "</td><td>%s</td></tr>"
            % (_stock_link(s), s["n_short"], _hit_badges(s))
            for s in shorts[:30]) or (
            "<tr><td colspan='3' class='dim'>当前无 ≥2 策略共振看空的标的"
            "</td></tr>")
        blk_short = (
            "<details class='sec' open><summary><h2>🧊 共振预警 · ≥2 策略"
            "同时看空（%d）</h2></summary><div class='sec-body'>"
            "<table class='sticky'><tr><th>标的</th><th>共振</th>"
            "<th>策略徽标</th></tr>%s</table></div></details>"
            % (len(shorts), tr_short))

        # ④ 策略覆盖矩阵（点数字下钻到该交集明细）
        strats = d["strategies"]
        mtx = d["matrix"]
        mh = "".join("<th>%s</th>" % e(s["badge"]) for s in strats)
        mr = ""
        for a in strats:
            cells = ""
            for b in strats:
                n = mtx.get(a["id"], {}).get(b["id"], 0)
                if a["id"] == b["id"]:
                    cells += ("<td class='dim' style='text-align:center'>"
                              "%d</td>" % d["n_per"].get(a["id"], 0))
                elif n:
                    cells += ("<td style='text-align:center'>"
                              "<a href='/bt-cross?pair=%s,%s' "
                              "style='color:#58a6ff'><b>%d</b></a></td>"
                              % (a["id"], b["id"], n))
                else:
                    cells += "<td class='dim' style='text-align:center'>·</td>"
            mr += ("<tr><td><a href='%s' style='color:%s'>%s</a>"
                   "<div class='dim'>%s</div></td>%s</tr>"
                   % (a["url"], a["color"], e(a["name"]),
                      e(a["markets"]), cells))
        blk_mtx = (
            "<details class='sec'><summary><h2>📊 策略覆盖矩阵</h2>"
            "<span class='dim'>点击展开</span></summary><div class='sec-body'>"
            "<div class='dim' style='margin-bottom:6px'>对角线=该策略当前"
            "覆盖标的数 · 交叉格=两策略共同命中数（点击下钻看明细）· 矩阵"
            "随注册表自动扩展——新策略上架即多一行一列</div>"
            "<table><tr><th>策略</th>%s</tr>%s</table></div></details>"
            % (mh, mr))

        # ⑤ 两两交集明细（pair 下钻视图）
        blk_pair = ""
        try:
            from urllib.parse import urlparse as _up, parse_qs as _pq
            pair = (_pq(_up(self.path).query).get("pair", [""])[0])
        except Exception:
            pair = ""
        if pair and "," in pair:
            pa, pb = pair.split(",", 1)
            names = {s["id"]: s["name"] for s in strats}
            rows_p = "".join(
                "<tr><td>%s</td><td>%s</td></tr>"
                % (_stock_link(s), _hit_badges(s))
                for s in stocks
                if pa in {h["sid"] for h in s["hits"]}
                and pb in {h["sid"] for h in s["hits"]})
            blk_pair = (
                "<div class='sec'><h2>🔗 交集明细：%s × %s</h2>"
                "<table><tr><th>标的</th><th>命中明细</th></tr>%s</table>"
                "<div style='margin-top:8px'><a class='tp' href='/bt-cross'>"
                "← 返回全览</a></div></div>"
                % (e(names.get(pa, pa)), e(names.get(pb, pb)),
                   rows_p or "<tr><td colspan='2' class='dim'>无交集"
                   "</td></tr>"))

        # 市场筛选 tab
        mkts = sorted({str(s.get("market") or "").upper()
                       for s in d["stocks"] if s.get("market")})
        tabs = ("<a class='tp' href='/bt-cross'>%s全部</a>"
                % ("<b>" if not mkt_q else "")) + "".join(
            "<a class='tp' href='/bt-cross?m=%s'>%s%s%s</a>"
            % (e(m), "<b>" if mkt_q == m else "", e(m),
               "</b>" if mkt_q == m else "") for m in mkts)

        strat_cards = "".join(
            "<a class='gate' href='%s'><b style='color:%s'>%s"
            "<span style='float:right;font-size:12px;color:#8b949e'>%d 只"
            "</span></b><div class='dim' style='margin-top:6px'>覆盖 %s"
            "</div></a>"
            % (s["url"], s["color"], e(s["name"]),
               d["n_per"].get(s["id"], 0), e(s["markets"]))
            for s in strats)

        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>多策略共振 · 策略集市</title>"
                + self._COCKPIT_CSS + "</head><body>"
                + pill_nav("cross")
                + "<h1>🎯 多策略共振 · 策略集市</h1><div class='dim'>"
                "跨策略聚合发现层 · 同一标的被几个策略同时选中一眼可见 · "
                "数据截至 %s · 展示层聚合，不改任何策略口径</div>"
                "<div class='sec'><h2>🧺 上架策略（%d 个 · 注册表驱动，"
                "新策略注册即上架）</h2><div class='gates'>%s</div></div>"
                "<div class='sec'><h2>🌍 市场筛选</h2>%s</div>"
                % (e(d["generated_at"]), len(strats), strat_cards, tabs)
                + "<div class='duo'>" + blk_mixed + blk_long + "</div>"
                + blk_short + blk_mtx + blk_pair
                + "<div class='dim' style='margin-top:20px'>口径：方向语义"
                "三分（看多/看空/中性）· 缠论取近 90 天最新信号 · 九维≥80 "
                "入榜（观察工具口径，非买入建议）· 四策略≥3/4 持仓记看多 · "
                "聚合为纯读数，不推送不构成投资建议</div></body></html>")
        self._html(body)


def _nine_dim_page(self):
        import html as _h
        e = _h.escape
        snap = _read_json(os.path.join(QBT_DIR, "data",
                                       "nine_dim_scores.json")) or {}
        ev = _read_json(os.path.join(QBT_DIR, "data",
                                     "nine_dim_pool_eval_cache.json")) or {}
        rbt = _read_json(os.path.join(QBT_DIR, "data",
                                      "nine_dim_replay_eval_cache.json")) or {}
        hist = []
        hp = os.path.join(QBT_DIR, "data", "nine_dim_history.jsonl")
        if os.path.exists(hp):
            try:
                with open(hp, encoding="utf-8") as f:
                    hist = [json.loads(l) for l in f if l.strip()][-15:]
            except Exception:
                log_exc("九维页:history 读取 %s" % hp)  # C2批1
                hist = []
        rows = snap.get("rows", [])
        env = snap.get("env") or {}
        wd = snap.get("wdelta") or {}

        def _chip(on, lb):
            c = "#3fb950" if on else "#30363d"
            return ("<span style='display:inline-block;padding:3px 10px;"
                    "border-radius:12px;font-size:12px;margin:0 6px 6px 0;"
                    "border:1px solid %s;color:%s'>%s</span>"
                    % (c, c if on else "#8b949e", lb))

        # ① 市场环境与动态调权
        if env:
            v1 = env.get("idx_above_ma20")
            env_line = ("科创50 <span style='color:%s'>%s</span> 20日线"
                        "（20日 %.2f%%）· 20日波动率分位 %s（≤30 视作低波动"
                        "，VIX 降级口径）· 两市成交额 %s 亿（≥10000 亿 +3）"
                        % ("#f85149" if v1 else "#3fb950",
                           "站上" if v1 else "跌破",
                           env.get("idx_month_chg") or 0,
                           "—" if env.get("vol_pct") is None
                           else env["vol_pct"],
                           "{:,}".format(int(env.get("amount_yi") or 0))))
            wdt = ("当前调权：<b>%s</b>" % e(json.dumps(
                       {k: "%+d" % v for k, v in wd.items()},
                       ensure_ascii=False))) if wd else \
                "当前无档位触发 · 按标准权重评分"
            env_blk = ("<div>%s</div><div class='dim' style='margin-top:6px'>"
                       "%s</div><div style='margin-top:8px'>%s</div>"
                       % (env_line, wdt,
                          _chip(env.get("bull"), "牛市档：板块+3 情绪+2")
                          + _chip(env.get("high_vol"),
                                  "高波动档：大盘-5 质量+5 动量-4")
                          + _chip(env.get("report_season"),
                                  "财报期档：盈利+3 技术-3")))
        else:
            env_blk = "<div class='dim'>暂无快照</div>"

        # ② 口径说明
        spec = (
            "<div class='dim' style='font-size:13px;line-height:2.0'>"
            "一句话：<b style='color:#e6edf3'>两段式九因子评分 —— 本地粗筛"
            "（RPS50&gt;80 + 成交额&gt;2亿 + 量能/布林动能）→ 候选池补齐 "
            "API 因子 → 78 分制评分，≥75 且完整覆盖入<b style='color:"
            "#f85149'>拥挤警报池</b>（回避名单，跟踪验证负收益即警报"
            "有效）</b><br>"
            "分段语义（K33 重定位，2026-10-02）：≥85 强警报 alert2 · "
            "75–85 警报 alert · 60–75 中段 mid（无信号，60–65 低波期有 "
            "beta 倾向非 alpha）· &lt;60 低位 low。原 ≥90 买入池退役"
            "（BUY_TH=90 仅作参照展示）<br>"
            "九因子（V3 结构）：公司质量15 · 盈利4 · 大盘环境7 · "
            "板块协同10 · 机构行为15 · 市场情绪8 · 动量资金4 · 估值安全10"
            " · 技术结构5（=78，总分=100×已得分/可判权重）；"
            "三档动态调权可叠加（上方状态卡）<br>"
            "V3 修订（2026-09-27，P2 回放 1565 样本逐维诊断驱动）："
            "移除北向连3日净买（陆股通净买 2024-08 停发后 tushare 字段"
            "语义已变成交额恒正，判据失真送分）、两市额≥万亿（99.6% 送分）、"
            "EPS上调≥1次（2.5% 可得=最大稀释源）、明弱实强（4.3% 可得）、"
            "量≥2倍（80% 与粗筛共线）；惩罚收紧：质量恶化&gt;35%、"
            "行业RPS&lt;50（旧阈值 51%/86% 样本误扣）<br>"
            "sv 豁免（V3 新增）：体系性缺失项（现金流字段/政策新闻覆盖前/"
            "历史大宗/融资明细）记 None 时从 cov 分母同步剔除——「体系没"
            "这个维度」不等于「样本覆盖不足」；个股级缺失（如 EV 缺）仍压 cov<br>"
            "V2 修订（2026-09-27）：移除年度预测上修（极稀有伪判据）；"
            "EPS上调≥1次(8分)、融资周增≥5%、券商覆盖≥3篇；"
            "北向连3日净买保留（后经 V3 确认字段失真移除）<br>"
            "不可判语义：无权限/无数据项记 <b>None 不计分不扣分</b>，总分 ="
            " 100×已得分/可判权重（cov=可判权重占比，&lt;0.75 不出分）<br>"
            "降级口径：机构调研接口无权限→机构行为=龙虎榜10+大宗5；情绪因子"
            "=新闻量周环比+JEV多空占比+券商覆盖（股吧/雪球/百度指数无源）；"
            "VIX→科创50已实现波动率分位；超预期概率→业绩预告预增；"
            "EV/EBITDA→总市值/EBITDA近似；行业分组用 stock_basic 行业"
            "（分位在候选池内近似）<br>"
            "影子纪律：纯门户展示不推送 · 入池日收盘价基准跟踪 r5/r20 · "
            "跨除权（缺口&gt;0.5%）剔除 · n&lt;10 不构成结论</div>")

        # ③ 评分榜单
        flbl = {"quality": "质", "eps": "盈", "env": "环", "sector": "板",
                "inst": "机", "sentiment": "情", "momentum": "动",
                "val": "估", "tech": "技"}
        full_rows = [r for r in rows if r.get("full")]
        part_rows = [r for r in rows if not r.get("full")]
        n_show = 0

        _BC = {"alert2": "#f85149", "alert": "#d29922",
               "mid": "#e3b341", "low": "#8b949e"}
        _BL = {"alert2": "强警报", "alert": "警报",
               "mid": "中段", "low": "低位", "na": "—"}

        def _row(i, r, partial=False):
            nonlocal n_show
            n_show += 1
            sc = r.get("score")
            bd = r.get("band") or ("na" if sc is None else "mid")
            hot = bd in ("alert", "alert2")
            fb = " ".join(
                "<span style='color:%s'>%s%d/%d</span>"
                % ("#f85149" if d["earned"] >= d["max"] * 0.99 else
                   "#3fb950" if d["earned"] < 0 else "#8b949e",
                   flbl.get(d["id"], "?"), d["earned"], d["max"])
                for d in r.get("detail", []))
            return ("<tr%s><td class='dim'>%d</td>"
                    "<td><a href='/kg/stock/%s'>%s</a><div class='dim'>%s"
                    "</div></td><td class='dim'>%s</td><td>%.1f</td>"
                    "<td style='color:%s'>%+.2f%%</td>"
                    "<td><b>%s</b>%s</td>"
                    "<td style='color:%s;font-size:12px'>%s</td>"
                    "<td class='dim'>%.2f</td>"
                    "<td class='dim' style='font-size:12px;white-space:"
                    "nowrap'>%s</td></tr>"
                    % (" style='background:#1c2128'" if hot else "",
                       i, e(str(r.get("code", ""))),
                       e(str(r.get("name", ""))), e(str(r.get("code", ""))),
                       e(str(r.get("industry", ""))),
                       float(r.get("rps50") or 0),
                       "#f85149" if (r.get("pct_chg") or 0) >= 0
                       else "#3fb950", float(r.get("pct_chg") or 0),
                       ("%.1f" % sc) if sc is not None else "未完整",
                       " ⚠" if hot else "",
                       _BC.get(bd, "#8b949e"), _BL.get(bd, "—"),
                       float(r.get("cov") or 0), fb))
        rank_rows = ""
        for i, r in enumerate(full_rows, 1):
            if n_show >= 40:
                break
            rank_rows += _row(i, r)
        for i, r in enumerate(part_rows[:10],
                              len(full_rows) + 1):
            if n_show >= 50:
                break
            rank_rows += _row(i, r, partial=True)
        rank_blk = (
            "<div class='dim' style='margin-bottom:4px'>候选池 %d 只 · "
            "完整评分 %d · ≥%d 拥挤警报 %d（K33：高分=回避位） · 漏斗：%s"
            " · 数据日 %s</div>"
            "<table><tr><th>#</th><th>标的</th><th>行业</th><th>RPS50"
            "</th><th>涨跌</th><th>总分</th><th>分段</th><th>cov</th>"
            "<th>九维 得分/满分（质盈环板机情动估技）</th></tr>%s</table>"
            % (snap.get("n_cand", 0), snap.get("n_full", 0),
               snap.get("alert_th", 75),
               snap.get("n_alert", snap.get("n_buy", 0)),
               " → ".join("%s %d" % (k, v)
                          for k, v in (snap.get("funnel") or {}).items()),
               e(str(snap.get("trade_date", ""))), rank_rows
               or "<tr><td colspan='9' class='dim'>暂无快照，每日 06:40 "
                  "刷新（NineDimScan）</td></tr>"))
        if not rows:
            rank_blk = "<div class='dim'>暂无快照，每日 06:40 刷新" \
                "（NineDimScan）</div>"

        # ④ 入池战绩
        def _stat_card(lb, st):
            if not st:
                return ("<div class='gate g-off'><b>%s</b><div class='dim'>"
                        "暂无成熟样本</div></div>" % lb)
            grey = st["n"] < 10
            col = "#8b949e" if grey else ("#f85149" if st["avg"] > 0
                                          else "#3fb950")
            return ("<div class='gate'><b>%s<span style='float:right;"
                    "color:%s'>%+.2f%%</span></b>"
                    "<div class='dim' style='margin-top:4px'>n=%d%s · "
                    "胜率 %.1f%%</div></div>"
                    % (lb, col, st["avg"], st["n"],
                       "（灰显：n<10）" if grey else "", st["win"]))
        perf_blk = ""
        if ev:
            perf_blk = (
                "<div class='dim' style='margin-bottom:6px'>累计入警报池 %d 只"
                " · 生成于 %s</div><div class='gates'>%s%s%s%s</div>"
                "<div class='dim' style='margin-top:6px'>%s</div>"
                % (ev.get("n_pool", 0), e(str(ev.get("generated_at", ""))),
                   _stat_card("r5 全体", (ev.get("r5") or {}).get("all")),
                   _stat_card("r5 ≥95分", (ev.get("r5") or {}).get("ge95")),
                   _stat_card("r20 全体", (ev.get("r20") or {}).get("all")),
                   _stat_card("r20 ≥95分",
                              (ev.get("r20") or {}).get("ge95")),
                   e(str(ev.get("note", "")))))
        else:
            perf_blk = "<div class='dim'>暂无入池，战绩随每日任务积累</div>"

        # ④b P2 历史回放（周频锚点 as-of 重放 · 影子）
        if rbt:
            blbl = {"ge90": "≥90", "85_90": "85–90", "80_85": "80–85",
                    "70_80": "70–80", "lt70": "&lt;70"}

            def _cell(o, k):
                v = (o or {}).get(k)
                return "—" if v is None else str(v)

            def _bt_row(w, name):
                st = ((rbt.get(w) or {}).get(name) or {})
                raw, thin = st.get("raw"), st.get("thinned")
                grey = not raw or raw.get("n", 0) < 10
                col = "#8b949e" if grey else (
                    "#f85149" if (raw.get("avg") or 0) > 0 else "#3fb950")
                return ("<tr%s><td class='dim'>%s</td><td>%s</td>"
                        "<td style='color:%s'>%s</td><td>%s</td>"
                        "<td>%s</td><td style='color:%s'>%s</td>"
                        "<td>%s</td><td>%s</td><td class='dim'>%s</td>"
                        "<td class='dim'>%s</td></tr>"
                        % (" class='dim'" if grey else "", w.upper(),
                           blbl.get(name, name), col,
                           _cell(raw, "n"), _cell(raw, "win"),
                           _cell(raw, "avg"), col,
                           _cell(raw, "exc"), _cell(raw, "p5"),
                           _cell(raw, "min"),
                           _cell(thin, "n"), _cell(thin, "avg")))

            bt_rows = "".join(
                _bt_row(w, b) for w in ("r20", "r5")
                for b in ("ge90", "85_90", "80_85", "70_80", "lt70"))
            worst = (((rbt.get("r20") or {}).get("ge90") or {})
                     .get("raw") or {}).get("worst") or []
            wrows = "".join(
                "<tr><td class='dim'>%s</td><td>%s<div class='dim'>%s</div>"
                "</td><td style='color:#3fb950'>%+.2f%%</td></tr>"
                % (e(str(x.get("anchor", ""))), e(str(x.get("name", ""))),
                   e(str(x.get("code", ""))), float(x.get("r20") or 0))
                for x in worst)
            worst_blk = ("<table><tr><th>锚点日</th><th>标的</th>"
                         "<th>r20 最差</th></tr>%s</table>" % wrows) \
                if wrows else "<div class='dim'>暂无 ≥90 成熟样本" \
                    "（≥90 阈值强选择性，历史入池极少属预期）</div>"
            arows = ""
            for a in reversed(rbt.get("by_anchor") or []):
                arows += (
                    "<tr><td class='dim'>%s</td><td>%s</td>"
                    "<td class='dim'>%s</td><td class='dim'>%s</td>"
                    "<td>%s</td><td><b>%s</b></td><td>%s</td><td>%s</td></tr>"
                    % (e(str(a.get("dt", ""))), _cell(a, "n_cand"),
                       _cell(a, "amt_yi"), _cell(a, "vol_pct"),
                       "牛市" if a.get("bull") else "—",
                       _cell(a, "n_full"), _cell(a, "ge90"),
                       _cell(a, "ge85")))
            v3_note = (
                "<div style='margin-bottom:8px;padding:8px 10px;"
                "border:1px solid #f85149;border-radius:6px;"
                "background:rgba(248,81,73,.08)'>"
                "<b style='color:#f85149'>⚠ K33 已重定位（2026-10-02）"
                "——总分反用为拥挤警报</b>。依据 V3 复评（51 锚点 / "
                "5479 样本 / 去重 4202）：分数与前向收益<b>单调负相关"
                "</b>——r20 均值 &lt;70 分 -9.2% → 70–80 分 -14.9% → "
                "75+ 分 -27.9% → 85+ 分 -71.5%，胜率 25%→21%→9%→0%；"
                "85+ 左尾（p5 -85.9%）全部为强动量拥挤票（存储双雄崩盘"
                "前夜 86 分、金诚信 92 分追顶 r20 -34%）。高分构成 = "
                "动量+机构+情绪+资金拥挤代理，与图谱拥挤度特征（K27）"
                "同构。<b>现行规则：≥75 入警报池跟踪（预期负收益 = 警报"
                "有效），85+ 强警报；BUY_TH=90 退役仅作参照</b>。65–75 "
                "观察池方案经离线网格否决（2024 段 70% 胜率系 n=10 小"
                "样本；60–65 低波 63% 胜率经超额检验拆穿为 beta 非alpha）。</div>")
            bt_blk = (
                "%s<div class='dim' style='margin-bottom:6px'>回放锚点 %d 个"
                "（周频 as-of 截断重放，bar/财报/龙虎/北向全部锚点日可见）"
                " · 样本行 %d · 基准 %s · 生成于 %s</div>"
                "<table><tr><th>窗口</th><th>分桶</th><th>n</th><th>胜率%%"
                "</th><th>均值%%</th><th>超额%%</th><th>p5</th><th>最低</th>"
                "<th>去重n</th><th>去重均值</th></tr>%s</table>"
                "<div class='dim' style='margin-top:10px'>≥90 分桶 r20 "
                "最差 5 条（左尾观察）：</div>%s"
                "<div class='dim' style='margin-top:10px'>逐锚点"
                "（新→旧）：</div>"
                "<table><tr><th>锚点日</th><th>候选池</th><th>两市额(亿)"
                "</th><th>波动分位</th><th>环境</th><th>完整评分</th>"
                "<th>≥90</th><th>≥85</th></tr>%s</table>"
                "<div class='dim' style='margin-top:6px'>%s</div>"
                % (v3_note, len(rbt.get("by_anchor") or []),
                   rbt.get("n_rows", 0),
                   e(str(rbt.get("bench", ""))),
                   e(str(rbt.get("generated_at", ""))), bt_rows, worst_blk,
                   arows, e(str(rbt.get("note", "")))))
        else:
            bt_blk = "<div class='dim'>回放未运行或结果未生成" \
                "（quant_backtest/nine_dim_backtest.py）</div>"

        # ⑤ 历史入池（K33 起为警报池；旧 ≥90 买入池行无 pool_type）
        if hist:
            hrows = ""
            for r in reversed(hist):
                r5 = r.get("r5")
                r20 = r.get("r20")
                pt = r.get("pool_type") or "buy_legacy"
                pt_html = ("<span style='color:#f85149'>警报</span>"
                           if pt == "alert"
                           else "<span class='dim'>买入(旧)</span>")
                hrows += (
                    "<tr><td class='dim'>%s</td><td>%s<div class='dim'>%s"
                    "</div></td><td><b>%.1f</b></td><td>%s</td>"
                    "<td>%.2f</td>"
                    "<td style='color:%s'>%s</td><td style='color:%s'>%s"
                    "</td><td class='dim'>%s</td></tr>"
                    % (e(str(r.get("dt", ""))), e(str(r.get("name", ""))),
                       e(str(r.get("code", ""))),
                       float(r.get("score") or 0), pt_html,
                       float(r.get("entry_px") or 0),
                       "#f85149" if (r5 or 0) >= 0 else "#3fb950",
                       "—" if r5 is None else "%+.2f%%%s" % (
                           r5, "" if r.get("mature5") else "(盯市)"),
                       "#f85149" if (r20 or 0) >= 0 else "#3fb950",
                       "—" if r20 is None else "%+.2f%%%s" % (
                           r20, "" if r.get("mature20") else "(盯市)"),
                       "⚠除权剔除" if r.get("adj") else ""))
            hist_blk = ("<table><tr><th>入池日</th><th>标的</th><th>入池分"
                        "</th><th>池类型</th><th>入池价</th><th>r5</th>"
                        "<th>r20</th><th>备注</th></tr>%s</table>"
                        "<div class='dim' style='margin-top:6px'>K33 起 ≥75 "
                        "入<b style='color:#f85149'>警报池</b>（回避名单，"
                        "跟踪负收益=警报有效）；「买入(旧)」为退役 ≥90 池"
                        "历史行，仅留档</div>" % hrows)
        else:
            hist_blk = ("<div class='dim'>暂无入池记录（警报池 ≥75 且 cov"
                        "≥0.75 才入；75+ 为罕见拥挤位，随每日任务积累）"
                        "</div>")

        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>九维选股 · 工作台</title>"
                + self._COCKPIT_CSS + "</head><body>"
                + pill_nav("9dim")
                + "<h1>🧮 九维量化选股 · 工作台</h1><div class='dim'>"
                "策略市场 · 9 因子 100 分制 · K33 重定位：≥75 拥挤警报池"
                "（回避位跟踪）· 影子观察不推送（快照 %s）</div>"
                % e(str(snap.get("generated_at", "—")))
                + "<details class='sec'><summary><h2>📖 模型说明书与口径</h2>"
                "<span class='dim'>点击展开</span></summary><div class='sec-body'>"
                + spec + "</div></details>"
                + "<details class='sec' open><summary><h2>🌐 市场环境与动态调权</h2>"
                "</summary><div class='sec-body'>" + env_blk + "</div></details>"
                + "<details class='sec' open><summary><h2>🎯 今日评分榜单（两段式候选池）</h2>"
                "</summary><div class='sec-body'>" + rank_blk + "</div></details>"
                + "<details class='sec'><summary><h2>📈 警报池战绩（影子验证）</h2>"
                "<span class='dim'>点击展开</span></summary><div class='sec-body'>"
                + perf_blk + "</div></details>"
                + "<details class='sec'><summary><h2>⏪ P2 历史回放（周频锚点重放 · 影子）</h2>"
                "<span class='dim'>点击展开</span></summary><div class='sec-body'>"
                + bt_blk + "</div></details>"
                + "<details class='sec'><summary><h2>🕘 历史入池（近 %d 条）</h2>"
                "<span class='dim'>点击展开</span></summary><div class='sec-body'>"
                % len(hist) + hist_blk + "</div></details>"
                + "<div class='dim' style='margin-top:20px'>纸面研究不构成"
                "投资建议 · 每日 06:40 NineDimScan 用前一交易日收盘数据"
                "评分 · K33 重定位后 ≥75 入警报池（回避位，跟踪负收益"
                "=警报有效）· P2 回放已验证（as-of 重放 51 锚点，调参"
                "需经确认）· P3 推送接入需警报池影子战绩达标后经确认"
                "启用</div>"
                "</body></html>")
        self._html(body)


def _algo_market_page(self, qs=None):
        """🛒 算法商品市场：本地算法商品化货架。数据源
        portal/algo_market.json（注册表存源，只读渲染）+
        portal/licenses.json（卡密库）。未订阅=试看模式（静态样例
        真实输出形态）；输入卡密激活后本机解锁。"""
        import html as _h
        e = _h.escape
        toast = ""
        # do_GET 传的是原始 query 串（字符串），内部直调可能传 dict——
        # 统一归一成 parse_qs dict（K-STOCK-122 修：此前 qs 字符串直接
        # .get 在带 ?key=/?id= 的 HTTP 请求上 AttributeError 打死连接，
        # 087 的卡密激活走浏览器一直 502/断连，仅直调冒烟未暴露）
        from urllib.parse import parse_qs as _aqs
        if isinstance(qs, str):
            qs = _aqs(qs)
        qs = qs or {}
        if qs.get("key"):
            ok, msg = self._algo_activate(qs["key"][0])
            toast = ("<div style='border:1px solid %s;border-radius:10px;"
                     "padding:10px 14px;margin:10px 0;font-size:13.5px'>%s%s"
                     "</div>" % ("#3fb950" if ok else "#f85149",
                                 "✅ " if ok else "❌ ", e(msg)))

        mkt = _read_json(os.path.join(ROOT, "algo_market.json")) or {}
        algos = mkt.get("algos", [])
        n_unlocked = sum(1 for a in algos if self._license_state(
            a.get("id", "")))
        # K-STOCK-122 岗位→算法互链（反向）：algo id → 在岗使用该算法的
        # 岗位名列表（源 ai_market.json algos 外键）；?id= 锚点定位高亮
        aim = _read_json(os.path.join(ROOT, "ai_market.json")) or {}
        users = {}
        for g in aim.get("groups", []):
            for a in g.get("ais", []):
                for i in a.get("algos", []) or []:
                    users.setdefault(i, []).append(
                        a.get("name", a.get("id", "")))
        anchor = (qs.get("id") or [""])[0]
        anchor_ok = anchor and any(a.get("id") == anchor for a in algos)

        # ── P1 卡3：最近真实产出（统计面）────────────────────
        # 六款商品逐个接真实数据源，只读既有缓存/账本，不造第二本
        # 账；只展示数量/日期/状态等统计摘要，不展示任何明细信号/
        # 个股名单（明细面口径待拍板，不越权）。缺数据 → 灰
        # "暂无最近产出"，不假绿；日期缺失如实标"日期未知"。
        def _live_row(ok, txt, src):
            """统计面行：真投影（绿）或灰显（数据源不可达）。"""
            if ok and txt:
                return ("<div style='font-size:11.5px;margin-top:8px;"
                        "color:#3fb950'>📡 最近真实产出（统计面）：%s"
                        "</div>" % e(txt))
            return ("<div style='font-size:11.5px;margin-top:8px;"
                    "color:#8b949e'>📡 暂无最近产出（数据源：%s 不可达）"
                    "</div>" % e(src))

        def _live_quant_dual():
            # 与 /portal 工作台同源读法：portal/qd_scan_cache.json
            # （api_misc build_overview 同一文件），只取数量+时间戳。
            src = os.path.join(ROOT, "qd_scan_cache.json")
            try:
                qd = _read_json(src) or {}
                if not qd.get("ts"):
                    return _live_row(False, "", "portal/qd_scan_cache.json")
                txt = "%s · 扫描 %s 只 · 当前信号 %d · 近端信号 %d" % (
                    str(qd.get("ts")),
                    qd.get("scanned", "?"),
                    len(qd.get("signals_now") or []),
                    len(qd.get("signals_recent") or []))
                return _live_row(True, txt, "portal/qd_scan_cache.json")
            except Exception:
                log_exc("algo_live:quant_dual")
                return _live_row(False, "", "portal/qd_scan_cache.json")

        def _live_chanlun():
            # 缠论选股池：quant_backtest/data/chanlun_pool.jsonl
            # （sixring pool 环同源），只取最近批次 date+count。
            src = os.path.join(QBT_DIR, "data", "chanlun_pool.jsonl")
            try:
                last_d, n = "", 0
                with open(src, encoding="utf-8") as f:
                    for ln in f:
                        ln = ln.strip()
                        if not ln:
                            continue
                        try:
                            r = json.loads(ln)
                        except Exception:
                            continue
                        d = str(r.get("detect_date") or "")
                        if not d:
                            continue
                        if d > last_d:
                            last_d, n = d, 1
                        elif d == last_d:
                            n += 1
                if not last_d:
                    return _live_row(False, "",
                                     "quant_backtest/data/chanlun_pool.jsonl")
                txt = "最近批次 %s · 入池 %d 条" % (last_d, n)
                return _live_row(True, txt,
                                 "quant_backtest/data/chanlun_pool.jsonl")
            except Exception:
                log_exc("algo_live:chanlun")
                return _live_row(False, "",
                                 "quant_backtest/data/chanlun_pool.jsonl")

        def _live_riskguard():
            # RiskGuard 影子账本：portal/data/risk_feedback.jsonl
            # （risk_feedback_ledger.py LEDGER_PATH 同源），统计
            # 累计拦截数按 R1-R4 分档 + 最近日期。
            src = os.path.join(ROOT, "data", "risk_feedback.jsonl")
            try:
                cnt = {"R1": 0, "R2": 0, "R3": 0, "R4": 0}
                last_d = ""
                with open(src, encoding="utf-8") as f:
                    for ln in f:
                        ln = ln.strip()
                        if not ln:
                            continue
                        try:
                            r = json.loads(ln)
                        except Exception:
                            continue
                        if r.get("action") != "blocked":
                            continue
                        rule = str(r.get("rule") or "").split("-")[0]
                        if rule in cnt:
                            cnt[rule] += 1
                        d = str(r.get("date") or "")
                        if d > last_d:
                            last_d = d
                txt = ("累计拦截 %d（R1 %d · R2 %d · R3 %d · R4 %d）"
                       "· 最近 %s" % (sum(cnt.values()), cnt["R1"],
                                      cnt["R2"], cnt["R3"], cnt["R4"],
                                      last_d or "日期未知"))
                return _live_row(True, txt,
                                 "portal/data/risk_feedback.jsonl")
            except Exception:
                log_exc("algo_live:riskguard")
                return _live_row(False, "",
                                 "portal/data/risk_feedback.jsonl")

        def _live_jev():
            # JEV 消息面情绪标注：news_collector/news.db news 表
            # jev 列（api_common _pool_bear_news 同源读法），统计
            # 近7天已研判条数 + 多/空/中性分布。
            src = os.path.join(STATION, "news_collector", "news.db")
            try:
                import sqlite3 as _sq
                from datetime import datetime as _dt, timedelta as _td
                since = (_dt.now() - _td(days=7)).strftime("%Y-%m-%d %H:%M")
                db = _sq.connect(src)
                rows = db.execute(
                    "SELECT jev FROM news WHERE jev IS NOT NULL "
                    "AND created_at>=? LIMIT 20000", (since,)).fetchall()
                latest = db.execute(
                    "SELECT MAX(created_at) FROM news "
                    "WHERE jev IS NOT NULL").fetchone()[0]
                db.close()
                if not rows:
                    return _live_row(False, "", "news_collector/news.db")
                nb = ns = nn = 0
                for (jv,) in rows:
                    try:
                        b = (json.loads(jv) or {}).get("bias")
                    except Exception:
                        b = None
                    if b == "bull":
                        nb += 1
                    elif b == "bear":
                        ns += 1
                    elif b == "neutral":
                        nn += 1
                txt = ("近7天研判 %d 条 · 多 %d / 空 %d / 中性 %d · 最近 %s"
                       % (len(rows), nb, ns, nn, latest or "日期未知"))
                return _live_row(True, txt, "news_collector/news.db")
            except Exception:
                log_exc("algo_live:jev")
                return _live_row(False, "", "news_collector/news.db")

        def _live_tdx():
            # 回测引擎最近产出：quant_backtest/last_backtest.json
            # （runner.py 落盘、get_backtest 同源消费），只取时间戳
            # +策略/模式+标的数。BacktestEngine/T0Engine 具名产出
            # 未实证到，取回测域最近真实落盘为准。
            src = os.path.join(STATION, "quant_backtest",
                               "last_backtest.json")
            try:
                bt = _read_json(src) or {}
                if not bt.get("ts"):
                    return _live_row(False, "",
                                     "quant_backtest/last_backtest.json")
                txt = "%s · 策略 %s · 模式 %s · 标的 %d" % (
                    str(bt.get("ts")),
                    bt.get("strategy") or "未知策略",
                    bt.get("mode") or "未知模式",
                    len(bt.get("results") or []))
                return _live_row(True, txt,
                                 "quant_backtest/last_backtest.json")
            except Exception:
                log_exc("algo_live:tdx")
                return _live_row(False, "",
                                 "quant_backtest/last_backtest.json")

        def _live_score():
            # Score 多因子最近一批排名：quant_backtest/reports/
            # rps_latest.json（/fin 页同源消费），只取批次号+条数。
            src = os.path.join(REPORTS_DIR, "rps_latest.json")
            try:
                rps = _read_json(src) or {}
                if not rps.get("latest"):
                    return _live_row(False,
                                     "", "quant_backtest/reports/rps_latest.json")
                txt = "批次 %s · 评分个股 %d 条 · 生成于 %s" % (
                    rps.get("latest"), len(rps.get("res") or []),
                    str(rps.get("ts") or "日期未知"))
                return _live_row(True, txt,
                                 "quant_backtest/reports/rps_latest.json")
            except Exception:
                log_exc("algo_live:score")
                return _live_row(False,
                                 "", "quant_backtest/reports/rps_latest.json")

        _live = {
            "quant_dual": _live_quant_dual(),
            "chanlun_9dim": _live_chanlun(),
            "riskguard": _live_riskguard(),
            "jev_sentiment": _live_jev(),
            "tdx_backtest": _live_tdx(),
            "score_ranking": _live_score(),
        }

        def _algo_card(a):
            sid = a.get("id", "")
            unlocked = self._license_state(sid)
            if unlocked:
                badge = ("✅ 已解锁", "#3fb950")
            else:
                badge = ("👁 试看模式", "#d29922")
            hl = ("outline:2px solid #58a6ff;outline-offset:2px;"
                  if sid == anchor and anchor_ok else "")
            use_row = ("<div class='dim' style='font-size:11.5px;"
                       "margin-top:8px'>🤖 在岗使用：%s</div>"
                       % e("、".join(users.get(sid, [])))
                       if users.get(sid) else "")
            sm = a.get("sample", {}) or {}
            feats = "".join(
                "<span class='tp' style='cursor:default'>%s</span>"
                % e(f) for f in a.get("features", []))
            sub = a.get("subscription", {}) or {}
            sample_html = ""
            live_html = _live.get(sid, "")
            if sm:
                sample_html = (
                    "<details style='margin-top:10px'>"
                    "<summary style='cursor:pointer;font-size:13px;"
                    "color:#58a6ff'>👁 试看结果 · %s</summary>"
                    "<div class='dim' style='font-size:11.5px;margin:"
                    "6px 0 4px'>%s</div>"
                    "<pre style='background:#0d1117;border:1px solid "
                    "#30363d;border-radius:8px;padding:10px;font-size:"
                    "11.5px;overflow-x:auto;white-space:pre-wrap'>%s</pre>"
                    "</details>" % (e(sm.get("title", "样例输出")),
                                    e(sm.get("meta", "")),
                                    e(sm.get("body", ""))))
            cta = ("<a class='tp' href='%s'>进入 →</a>" % e(a.get("entry", "/"))
                   if unlocked else
                   "<span class='dim' style='font-size:12px'>订阅后开放"
                   "实时入口 · %s</span>" % e(sub.get("price_hint", "")))
            return ("<div id='algo-%s' style='border:1px solid %s;"
                    "border-radius:12px;"
                    "background:#161b22;padding:16px 18px;%s'>"
                    "<div style='display:flex;align-items:baseline;gap:8px'>"
                    "<span style='font-size:17px;font-weight:700'>%s %s"
                    "</span>"
                    "<span style='font-size:11px;color:#8b949e;border:1px "
                    "solid #30363d;border-radius:10px;padding:1px 8px'>%s"
                    "</span>"
                    "<span style='margin-left:auto;font-size:11.5px;color:"
                    "%s;border:1px solid %s;border-radius:12px;padding:"
                    "2px 10px'>%s</span></div>"
                    "<div class='dim' style='font-size:12.5px;margin:"
                    "8px 0 4px'>%s</div>"
                    "<div style='font-size:13px;margin-bottom:6px'>%s</div>"
                    "<div style='display:flex;gap:6px;flex-wrap:wrap'>%s"
                    "</div>"
                    "%s"
                    "%s"
                    "%s"
                    "<div style='font-size:12.5px;margin-top:10px'>%s</div>"
                    "</div>"
                    % (e(sid), badge[1], hl,
                       e(a.get("icon", "🧩")),
                       e(a.get("name", "")), e(a.get("tier", "")),
                       badge[1], badge[1], badge[0],
                       e(a.get("tagline", "")), e(a.get("desc", "")),
                       feats, sample_html, live_html, use_row, cta))
        scroll_js = ""
        if anchor_ok:
            scroll_js = ("<script>var t=document.getElementById("
                         "'algo-%s');if(t){t.scrollIntoView({behavior:"
                         "'smooth',block:'center'});}</script>" % e(anchor))
        grid = "".join(
            "<div style='display:grid;grid-template-columns:repeat("
            "auto-fit,minmax(320px,1fr));gap:12px'>%s</div>"
            % "".join(_algo_card(a) for a in algos))
        act_form = (
            "<div class='sec'><h2>🔑 卡密激活（本机解锁）</h2>"
            "<div class='dim' style='font-size:12.5px;margin-bottom:8px'>"
            "输入卡密后本机立即解锁对应商品 · 卡密由管理员发放"
            "（portal/licenses.json 存源）· 试看模式下可查看每款算法的"
            "真实输出样例</div>"
            "<form method='get' action='/algo' style='display:flex;"
            "gap:8px'>"
            "<input name='key' placeholder='如 LTSO-XXXX-XXXX' style='"
            "background:#0d1117;border:1px solid #30363d;color:#e6edf3;"
            "border-radius:8px;padding:8px 12px;font-size:14px;min-width:"
            "240px'>"
            "<button type='submit' style='background:#238636;color:white;"
            "border:none;border-radius:8px;padding:8px 18px;font-size:"
            "14px;cursor:pointer'>激活</button></form></div>")
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>🛒 算法商品市场</title>"
                + self._COCKPIT_CSS + "</head><body>"
                + pill_nav("algo")
                + "<h1>🛒 算法商品市场</h1><div class='dim'>"
                + e(mkt.get("tagline", ""))
                + " · 共 %d 款商品 · 已解锁 %d 款 · 每款可试看真实输出样例"
                "</div>" % (len(algos), n_unlocked)
                + toast + grid + act_form + scroll_js
                + "<div class='dim' style='margin-top:20px'>商品源 portal/"
                "algo_market.json · 卡密库 portal/licenses.json · 内测"
                "参考价以正式发卡为准 · 不构成投资建议</div></body></html>")
        self._html(body)


def _fin_page(self):
        import html as _h
        from urllib.parse import quote as _q
        e = _h.escape
        pre = _read_json(os.path.join(REPORTS_DIR,
                                      "premarket_latest.json"))
        snap = _read_json(os.path.join(REPORTS_DIR, "rps_latest.json"))
        qcfg = _read_json(os.path.join(STATION, "quant_backtest",
                                       "config.json"))
        rep = qcfg.get("report", {})

        # ── A 区 · 今日全景（默认展开，一屏）─────────────────
        # A1 查一只股票（表单 → /sa?q= 自动开跑）
        sa_blk = (
            "<form action='/sa' method='get' style='display:flex;gap:8px'>"
            "<input name='q' placeholder='股票名或代码：英伟达 / TSLA / "
            "600519 / 00700' maxlength='20' style='flex:1;padding:11px 14px;"
            "font-size:15px;border:1px solid #30363d;border-radius:10px;"
            "background:#161b22;color:#e6edf3;outline:none' autofocus>"
            "<button style='padding:11px 20px;font-size:14px;border:none;"
            "border-radius:10px;background:#238636;color:#fff;cursor:pointer;"
            "font-weight:600'>分析</button></form>"
            "<div class='dim' style='margin-top:6px'>行情 + 近7天 JEV 逐条"
            "判断 + 展望 · 约 1 分钟 · 结论推飞书</div>")

        # A2 今日市场一句话 → 升级为五维拼图（首屏可见结构）
        prob = pre.get("prob")
        vals = pre.get("vals", {})
        verdict = e(str(pre.get("verdict", "—")))
        prob_txt = ("%d%%" % prob) if prob is not None else "—"
        # 五维色块：正绿负红，分值即权重
        dim_colors = {"外围": "#58a6ff", "流动性": "#d2a8ff",
                      "政策": "#f778ba", "板块": "#ffa657", "情绪": "#3fb950"}
        dim_chips = "".join(
            "<span style='display:inline-flex;align-items:baseline;gap:3px;"
            "margin:3px 10px 3px 0;padding:4px 10px;border-radius:8px;"
            "background:#161b22;border:1px solid #30363d;font-size:12px'>"
            "<span style='color:%s'>%s</span>"
            "<b style='color:%s;font-size:13px'>%s</b></span>"
            % (dim_colors.get(k, "#8b949e"), e(str(k)),
               "#8b949e" if v is None else
               ("#f85149" if v >= 0 else "#3fb950"),
               "—" if v is None else "%+d" % int(v))
            for k, v in vals.items())
        # 板块结构：取正负最强各一
        l4 = pre.get("l4", [])
        if l4:
            best = max(l4, key=lambda x: x["score"])
            worst = min(l4, key=lambda x: x["score"])
            sector_note = ("<div style='margin-top:6px;font-size:11.5px;"
                          "color:#8b949e'>板块 <span style='color:#f85149'>%s +%d</span>"
                          " · <span style='color:#3fb950'>%s %+d</span></div>"
                          % (e(str(best.get("sector", ""))), best["score"],
                             e(str(worst.get("sector", ""))), worst["score"]))
        else:
            sector_note = ""
        # 流动性/外围摘要
        l1 = pre.get("l1", {})
        l3 = pre.get("l3", {})
        l1_note = ""
        if l1.get("score") is not None:
            l1_note = "外围 %+d" % l1["score"]
        l3_note = ""
        if l3.get("score") is not None:
            l3_note = "流动性 %+d" % l3["score"]
        fx = l1.get("fx", {})
        fx_note = ""
        if fx.get("val"):
            fx_note = " · 离岸人民币 %.4f (%s)" % (
                fx["val"], "升" if fx.get("pct", 0) > 0 else "贬")
        # 公告/研报摘要（TDX l6）
        l6 = pre.get("l6") or {}
        ann_note = ""
        an6 = l6.get("ann_stats")
        if an6 and an6.get("bear"):
            ann_note = " · ⚠ 利空公告 %d" % an6["bear"]
        elif an6:
            ann_note = " · 公告 %d" % an6["total"]
        rpt_note = ""
        rp6 = l6.get("rpt_stats")
        if rp6 and rp6.get("rated"):
            rpt_note = " · 研报 %d 篇（买入 %d）" % (rp6["rated"],
                                                      rp6["buy"])
        summary_line = ""
        if l1_note or l3_note:
            summary_line = ("<div style='margin-top:5px;font-size:11.5px;"
                           "color:#8b949e'>%s%s%s%s%s</div>"
                           % (l1_note, (" · " + l3_note) if l3_note else "",
                              fx_note, ann_note, rpt_note))
        pre_blk = (
            "<div style='font-size:18px;font-weight:700;margin:2px 0 4px'>"
            "%s <span style='font-size:13px;font-weight:400;color:#8b949e'>"
            "溢价 %s</span></div>"
            "<div style='margin:6px 0'>%s</div>%s%s"
            "<div style='margin-top:6px'><a href='/premarket'>盘前详情 →</a></div>"
            % (verdict, prob_txt, dim_chips, sector_note, summary_line))

        # A3 四市场闸门（今天能不能新建仓）
        # K-STOCK-403：三态渲染（放行/否决/缺维降权）
        # + 整条横幅容器。语义与个股信号分离：
        #   闸门答「今天能不能新建仓」，信号答「这只票买不买」。
        #   缺维（safe=None，feeder 无源）必须区别于否决——不误杀（api.py safe 三值语义）。
        gates = []
        any_off = any_na = False
        for ev in snap.get("envs", []):
            mk = _env_market_key(ev.get("label", ""))
            if not mk:
                continue
            raw = ev.get("safe")
            if raw is None:
                cls, state = "g-na", "— 缺维降权"
                any_na = True
            elif raw:
                cls, state = "g-on", "✅ 放行建仓"
            else:
                cls, state = "g-off", "⛔ 否决建仓"
                any_off = True
            gates.append(
                "<a class='gate %s' href='/bt/m/%s'><b>%s</b>"
                "<span class='g-state' style='float:right;font-size:14px;"
                "font-weight:700'>%s</span>"
                "<div style='margin-top:5px;font-size:12.5px;"
                "color:var(--text-dim,#8b949e)'>环境分%s</div></a>"
                % (cls, mk, MKT_LABEL[mk], state, e(str(ev.get("score", "—")))))
        if any_na and not any_off:
            bar_cls = "gb-na"
            verdict = "部分市场数据缺维（不误杀，可建仓但无闸门保护）"
        elif any_off:
            bar_cls = "gb-off"
            verdict = "存在否决市场 —— 今日<b>暂停新建仓</b>，已持仓不受影响"
        else:
            bar_cls = "gb-on"
            verdict = "全市场放行 —— 今日<b>允许新建仓</b>"
        gates_html = (
            "<div class='gatebar %s'><p class='gb-title'>"
            "🚦 市场闸门 · 今天能不能新建仓</p>"
            "<p class='gb-note'>%s<br>闸门只管「现在能不能新建仓」；"
            "下方个股信号是另一层判断，"
            "<b>两者不冲突</b>——"
            "闸门否决时，下方仍会显示观察名单"
            "（仅供研究，不构成建仓指令）。</p>"
            "<div class='gates'>%s</div></div>"
            % (bar_cls, verdict, "".join(gates))
            if gates else "<div class='dim'>暂无快照</div>")

        # A4 今日机会（共振"买入" Top5）
        res = snap.get("res", [])
        buys = sorted((r for r in res if r.get("action") == "买入"),
                      key=lambda r: -(r.get("score") or 0))[:5]
        opp_rows = "".join(
            "<tr><td><a href='/kg/stock/%s'>%s</a></td>"
            "<td><span class='dim'>%s</span></td>"
            "<td><b>%d</b></td></tr>"
            % (e(str(r.get("code", ""))), e(str(r.get("name", ""))),
               e(str(r.get("market", ""))), round(r.get("score") or 0))
            for r in buys)
        opp_blk = (
            "<div class='dim' style='margin-bottom:4px'>全池 %d 只 · "
            "<span style='color:#f85149'>买入 %d</span></div>"
            "<table><tr><th>标的</th><th>市场</th><th>共振分</th></tr>"
            "%s</table>"
            "<div style='margin-top:6px'><a href='/bt-qpzm'>全部 →</a></div>"
            % (len(res), len(buys), opp_rows or
               "<tr><td colspan='3' class='dim'>今日无买入信号</td></tr>")
            ) if res else "<div class='dim'>暂无快照</div>"

        # ── B 区 · 策略动态（注册表驱动，新增策略零改）────────
        cross = {}
        try:
            sys.path.insert(0, os.path.join(STATION, "quant_backtest"))
            import cross_poll as _CP
            cross = _CP.build_cross() or {}
        except Exception:
            log_exc("_fin 策略动态:cross_poll")  # C2批1
            cross = {}
        n_per = cross.get("n_per", {})
        stocks = cross.get("stocks", [])
        n_multi = sum(1 for st in stocks if st.get("n_hit", 0) >= 2)
        n_mixed = sum(1 for st in stocks if st.get("state") == "mixed")
        n_long = sum(1 for st in stocks if st.get("state") == "long")
        strat_reg = cross.get("strategies", [])
        dyn_cards = "".join(
            "<a class='sstat' href='%s'><div class='st'>%s %s</div>"
            "<div class='sv'>%d 只命中</div>"
            "<div class='su'>→ 进入工作台</div></a>"
            % (s["url"], e(str(s.get("icon") or "🧪")),
               e(str(s.get("name", s["id"]))), int(n_per.get(s["id"], 0)))
            for s in strat_reg)
        cross_card = (
            "<a class='sstat' href='/bt-cross'><div class='st'>🎯 多策略共振"
            "</div><div class='sv'><span class='up'>%d</span> 多策略 · "
            "<span class='dn'>%d</span> 冲突</div>"
            "<div class='su'>全多 %d · → 共振看板</div></a>"
            % (n_multi, n_mixed, n_long))
        base_strats = "".join(
            "<a class='tp' href='/bt/strategy/%s'>🧪 %s</a>" % (sn, lb)
            for sn, lb in [("trend_follow", "趋势跟随"),
                           ("ma_cross", "均线交叉"),
                           ("rsi_reversal", "RSI 反转"),
                           ("boll_breakout", "布林突破")])
        strat_dyn = (
            "<div class='gates'>%s%s</div>"
            "<div class='dim' style='margin-top:8px'>策略卡数与口径随 "
            "cross_poll.STRATEGIES 注册表自动扩展 · 基础策略纵切：%s</div>"
            % (dyn_cards, cross_card, base_strats))

        # ── C 区 · 折叠收纳（默认收起，首屏不露）────────────
        # C1 盘前五维明细（A2 已展示摘要，此处放完整分布与镜像链接）
        vals_detail = pre.get("vals", {})
        vchips = "".join(
            "<span style='display:inline-block;margin:3px 8px 3px 0;"
            "padding:3px 10px;border-radius:9px;background:#161b22;"
            "border:1px solid #30363d;font-size:12.5px'>%s <b "
            "style='color:%s'>%s</b></span>"
            % (e(str(k)),
               "#8b949e" if v is None else
               ("#f85149" if v >= 0 else "#3fb950"),
               "—" if v is None else "%+d" % int(v))
            for k, v in sorted(vals_detail.items(),
                               key=lambda x: -abs(x[1])
                               if x[1] is not None else 0)
        ) or "<span class='dim'>暂无盘前数据</span>"
        pre_detail = vchips + (
            "<div style='margin-top:8px'><a href='/premarket'>🖥 完整盘前"
            "分析 →</a>%s</div>"
            % (("　·　<a href='%s' target='_blank'>📄 飞书镜像</a>"
                % e(rep.get("premarket_doc_url")))
               if rep.get("premarket_doc_url") else ""))

        # C2 工具与横切
        tools = "".join(
            "<a class='tp' href='%s'>%s</a>" % (href, lb) for lb, href in [
                ("🚦 明日信号", "/bt-signals"),
            ("💼 持仓跟踪", "/bt-positions"),
            ("📄 回测报告", "/bt-report"),
            ("📊 寻优榜单", "/bt-opt"),
            ("🔥 板块热力", "/bt-sectors"),
            ("⚖️ JEV 消息面", "/bt-jev"),
            ("🤖 AI 晨报", "/bt-brief"),
            ("🎯 选股中心", "/bt"),
            ("📊 数据仓健康", "/bt-data-health"),
        ])
        markets = "".join(
            "<a class='tp' href='/bt/m/%s'>%s</a>" % (mk, lb)
            for mk, lb in [("cn", "🇨🇳 A股"), ("us", "🇺🇸 美股"),
                           ("hk", "🇭🇰 港股"), ("crypto", "🪙 加密")])

        # C3 最近分析
        hist = _sa_hist_read(6)
        hist_rows = "".join(
            "<tr><td><a href='/sa?q=%s'>%s</a></td><td>%s</td>"
            "<td style='color:%s'>%s</td><td class='dim'>%s</td></tr>"
            % (_q(h["q"]), e(str(h.get("name", ""))),
               e(str(h.get("px", "")).split("（")[0]),
               {"利多": "#f85149", "利空": "#3fb950",
                "分歧": "#d29922"}.get(h.get("bias"), "#8b949e"),
               e(str(h.get("bias", ""))), e(str(h.get("t", ""))))
            for h in hist if h.get("ok"))
        hist_blk = ("<table><tr><th>股票</th><th>现价</th><th>倾向</th>"
                    "<th>时间</th></tr>%s</table>" % hist_rows
                    if hist_rows else "<div class='dim'>还没有分析记录"
                    "，从上面输入第一只吧</div>")

        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>金融驾驶舱</title>"
                + self._COCKPIT_CSS + "</head><body>"
                + pill_nav("fin")
                + "<h1>🏦 金融驾驶舱</h1><div class='dim'>今日全景 · "
                "策略动态 · 数据截至 %s</div>" % e(str(snap.get("latest", "")))
                # A 区：今日全景（一屏，默认展开）
                + "<div class='sec'><h2>🔍 分析一只股票</h2>" + sa_blk
                + "</div>"
                + gates_html
                + "<div class='duo'>"
                + "<div class='sec'><h2>🌅 今日市场一句话</h2>" + pre_blk
                + "</div>"
                + "<div class='sec'><h2>💡 今日机会 · 买什么</h2>" + opp_blk
                + "</div></div>"
                # B 区：策略动态（一屏，默认展开）
                + "<div class='sec'><h2>🧭 策略动态 · 各方法论今天在干嘛"
                "</h2>" + strat_dyn + "</div>"
                # C 区：折叠收纳（默认收起）
                + "<details class='sec'><summary><h2>🌅 盘前五维明细</h2>"
                "<span class='dim'>评分分布 · 飞书镜像</span></summary>"
                "<div class='sec-body'>" + pre_detail + "</div></details>"
                + "<details class='sec'><summary><h2>🧰 工具与横切</h2>"
                "<span class='dim'>执行跟踪 · 市场全景 · 分析报告</span>"
                "</summary><div class='sec-body'>"
                + "<div class='dim' style='margin:6px 0 4px'>执行与跟踪："
                "</div>" + tools
                + "<div class='dim' style='margin:10px 0 4px'>市场全景："
                "</div>" + markets + "</div></details>"
                + "<details class='sec'><summary><h2>🕘 最近分析</h2>"
                "<span class='dim'>点击继续看</span></summary>"
                "<div class='sec-body'>" + hist_blk + "</div></details>"
                + "<div class='dim' style='margin-top:20px'>纸面研究"
                "不构成投资建议 · 共振与闸门数据每日 17:35 刷新 · 盘前"
                "工作日 08:30</div></body></html>")
        self._html(body)


def _strategies_page(self):
        """策略中心（K36）：注册表全景记分卡——策略即资产，
        状态/指标/入口一页看全 + 上新三步 SOP。"""
        import html as _h
        e = _h.escape
        d = strategies_data()
        items = d["strategies"]
        n_live = sum(1 for s in items if s.get("status") == "live")
        n_shadow = sum(1 for s in items if s.get("status") == "shadow")
        n_tool = sum(1 for s in items if s.get("status") == "tool")
        # 状态 → 语义色（tokens.css 语义层，不硬编码 hex）
        ST = {"live": ("实盘", "var(--color-ok)"),
              "shadow": ("影子", "var(--color-warn)"),
              "tool": ("支撑", "var(--link)"),
              "paused": ("暂停", "var(--text-dim)")}
        # 生命周期带：研发 → 影子验证 → 决策链 → 复盘退役
        band = "".join(
            "<span style='display:inline-flex;align-items:center;"
            "gap:6px;padding:5px 12px;border:1px solid var(--border);"
            "border-radius:999px;font-size:12px;background:var(--bg-raised)"
            "'><b style='color:%s'>%s</b>"
            "<span style='color:var(--text-dim)'>%s</span></span>"
            "<span style='color:var(--text-dim);padding:0 4px'>→</span>"
            % (c, e(t), e(n)) for t, c, n in [
                ("影子验证", "var(--color-warn)", "%d 条在验" % n_shadow),
                ("决策链", "var(--color-ok)", "%d 条在产" % n_live),
                ("复盘退役", "var(--text-dim)", "结案入库")])
        cards = []
        for s in items:
            st = ST.get(s.get("status"), ("?", "var(--text-dim)"))
            mets = "".join(
                "<div style='display:flex;justify-content:space-between;"
                "gap:10px;font-size:12px;padding:3px 0'>"
                "<span style='color:var(--text-dim);flex-shrink:0'>%s</span>"
                "<span style='text-align:right'>%s</span></div>"
                % (e(m.get("k", "")), e(str(m.get("v", ""))))
                for m in (s.get("metrics") or []))
            links = []
            if s.get("href"):
                links.append("<a href='%s' style='color:var(--link);"
                             "text-decoration:none;font-size:12.5px'>"
                             "门户入口 →</a>" % e(s["href"]))
            if s.get("ext"):
                links.append("<a href='%s' target='_blank' rel='noopener' "
                             "style='color:var(--link);text-decoration:none;"
                             "font-size:12.5px'>外部系统 ↗</a>" % e(s["ext"]))
            task = ("<span style='font-size:11px;color:var(--text-dim)"
                    ";font-family:Consolas,monospace'>任务 %s</span>"
                    % e(s["task"])) if s.get("task") else ""
            cards.append(
                "<div style='background:var(--bg-raised);border:1px solid "
                "var(--border);border-radius:12px;padding:16px 18px;"
                "display:flex;flex-direction:column;gap:6px'>"
                "<div style='display:flex;align-items:center;gap:8px'>"
                "<span style='font-size:10.5px;padding:1px 8px;"
                "border:1px solid var(--border);border-radius:999px;"
                "color:var(--text-dim)'>%s</span>"
                "<span style='font-size:10.5px;padding:1px 8px;"
                "border-radius:999px;color:%s;background:var(--bg-surface)"
                ";border:1px solid var(--border)'>● %s</span></div>"
                "<div style='font-size:15px;font-weight:700'>%s</div>"
                "<div style='font-size:12px;color:var(--text-dim)'>%s</div>"
                "<div style='font-size:11.5px;color:%s'>%s</div>"
                "<div style='margin-top:4px'>%s</div>"
                "<div style='display:flex;justify-content:space-between;"
                "align-items:baseline;margin-top:auto;padding-top:8px;"
                "border-top:1px dashed var(--border-weak)'>"
                "<span style='display:flex;gap:12px'>%s</span>%s</div></div>"
                % (e(s.get("badge", "")), st[1], st[0], e(s["name"]),
                   e(s.get("tagline", "")), st[1], e(s.get("status_note", "")),
                   mets, "".join(links), task))
        cards_by = {}
        for _s, _c in zip(items, cards):
            cards_by.setdefault(_s.get("status"), []).append(_c)
        _group_meta = [
            ("live", "✅ 实盘 · 在产", True),
            ("shadow", "🟡 影子验证 · 只标注不拦截", False),
            ("tool", "⚙️ 支撑工具", False),
            ("paused", "⏸ 暂停", False),
        ]
        groups_html = ""
        for _key, _title, _open in _group_meta:
            _cards = "".join(cards_by.get(_key, []))
            if not _cards:
                continue
            _count = len(cards_by.get(_key, []))
            _head = ("<div class='strat-group-head'><h3>%s</h3>"
                     "<span>%d 条</span></div>" % (_title, _count))
            if _open:
                groups_html += ("<section class='strat-group' data-status='%s'>%s"
                                "<div class='dw-grid'>%s</div></section>"
                                % (_key, _head, _cards))
            else:
                groups_html += ("<details class='strat-group' data-status='%s'>"
                                "<summary>%s<span class='strat-open'>展开</span></summary>"
                                "<div class='dw-grid'>%s</div></details>"
                                % (_key, _head, _cards))
        if not groups_html:
            groups_html = "<div class='empty'>暂无策略资产</div>"
        nav = pill_nav("strategies")
        # K87 锦标赛交叉表：策略来源 × 六本账累计贡献（sids 汇总，
        # 数据 reports/trader_sim_eval.json；列序与 trader_sim.BOOKS 同步）
        _ev = _read_json(os.path.join(REPORTS_DIR, "trader_sim_eval.json"))
        sids = (_ev or {}).get("sids") or {}
        tourney_html = ""
        if sids:
            _BK6 = ["激进|积极", "激进|保守", "保守|积极",
                    "保守|保守", "中立|积极", "中立|保守"]

            def _cell(v):
                if v is None:
                    return ("<td style='text-align:right;padding:5px 8px;"
                            "color:var(--text-dim)'>—</td>")
                return ("<td style='text-align:right;padding:5px 8px;"
                        "color:%s'>%+.2f</td>"
                        % ("var(--color-up)" if v >= 0 else "var(--color-dn)",
                           v))
            _th = "".join(
                "<th style='text-align:right;padding:4px 8px;font-weight:400'>"
                "%s</th>" % bk.replace("|", "·") for bk in _BK6)
            _trs = []
            for sid, s in sorted(
                    sids.items(),
                    key=lambda kv: kv[1].get("total") or 0, reverse=True):
                _tot = s.get("total") or 0
                _trs.append(
                    "<tr style='border-top:1px solid var(--border-weak)'>"
                    "<td style='padding:5px 8px'><b>%s</b>"
                    "<span style='color:var(--text-dim);font-size:11px'> %s"
                    "</span></td>"
                    "<td style='text-align:right;padding:5px 8px'>%d</td>%s"
                    "<td style='text-align:right;padding:5px 8px;"
                    "font-weight:700;color:%s'>%+.2f</td>"
                    "<td style='padding:5px 8px;color:var(--text-dim);"
                    "font-size:11.5px'>%s</td></tr>"
                    % (e(s.get("label") or sid), e(sid), s.get("n", 0),
                       "".join(_cell(s.get(bk)) for bk in _BK6),
                       "var(--color-up)" if _tot >= 0 else "var(--color-dn)",
                       _tot, e((s.get("best_bk") or "").replace("|", "·"))))
            tourney_html = (
                "<div style='background:var(--bg-raised);border:1px solid "
                "var(--border);border-radius:12px;padding:16px 18px;"
                "margin-top:22px'>"
                "<div style='font-size:14px;font-weight:700;margin-bottom:4px'>"
                "♠ 操盘锦标赛 · 策略来源 × 六本账</div>"
                "<div style='font-size:12px;color:var(--text-dim);"
                "margin-bottom:10px'>投委会立案票经三操盘手（激进/保守/"
                "中立）× 两盘算（积极/保守）六账推演；单元格=该策略立案票"
                "在对应账本的累计贡献%%（中式红涨绿跌）· 数据 %s</div>"
                "<div style='overflow-x:auto'><table style='border-collapse:"
                "collapse;width:100%%;font-size:12.5px'>"
                "<tr style='color:var(--text-dim);font-size:11.5px'>"
                "<th style='text-align:left;padding:4px 8px;font-weight:400'>"
                "策略来源</th><th style='text-align:right;padding:4px 8px;"
                "font-weight:400'>票数</th>%s"
                "<th style='text-align:right;padding:4px 8px;font-weight:400'>"
                "合计</th><th style='text-align:left;padding:4px 8px;"
                "font-weight:400'>最佳账</th></tr>%s</table></div></div>"
                % (e((_ev or {}).get("ts", "")), _th, "".join(_trs)))
        body = (
            "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>策略中心 · 零界</title>"
            "<link rel='stylesheet' href='/static/tokens.css'>"
            "<link rel='stylesheet' href='/static/deep_shell.css'></head>"
            "<body class='dw-page strategies-page'>"
            + nav + _HERMES_FAB_HTML +
            "<header class='dw-hero'><div class='dw-brand-block'>"
            "<div class='dw-brand'><div class='dw-logo'>策</div><div>"
            "<div class='dw-eyebrow'>STRATEGY ASSETS · 注册表驱动</div>"
            "<h1 class='dw-title'>策略资产中心 <span>K-STOCK · STRATEGY REGISTRY</span></h1>"
            "</div></div>"
            "<p class='dw-lead'>策略即资产：状态、指标、入口与生命周期一页看全；数据快照 %s。</p>"
            "<div class='dw-tags'><span class='dw-tag live'>注册表驱动</span>"
            "<span class='dw-tag'>%d 条策略</span><span class='dw-tag warn'>影子验证 %d</span>"
            "<span class='dw-tag'>实盘 %d</span></div></div>"
            "<div class='dw-hero-side'><div class='dw-kpis'>"
            "<div class='dw-kpi'><div class='k'>策略资产</div><div class='v'>%d</div></div>"
            "<div class='dw-kpi'><div class='k'>实盘</div><div class='v'>%d</div></div>"
            "<div class='dw-kpi'><div class='k'>影子验证</div><div class='v'>%d</div></div>"
            "<div class='dw-kpi'><div class='k'>支撑工具</div><div class='v'>%d</div></div>"
            "</div></div></header>"
            "<div class='dw-context'><div class='dw-crumb'>零界 <span>/</span> 量化 <span>/</span> <b>策略资产中心</b></div>"
            "<div class='dw-context-meta'><span class='dw-dot'></span> 注册表驱动 <span>·</span> 策略即资产</div></div>"
            "<div style='display:flex;align-items:center;flex-wrap:wrap;"
            "margin:14px 0 6px'>%s</div>"
            "<div style='color:var(--text-dim);font-size:12px;margin:4px 0 16px'>"
            "策略资产记分卡 · 状态与生命周期以注册表为准；"
            "每条策略都是一条产品线。</div>"
            "%s"
            "<details class='strat-sop'><summary>➕ 上新一条策略（三步，注册即上架）</summary>"
            "<div class='strat-sop-body'>"
            "<div style='font-size:12.5px;line-height:2;color:var(--text-dim)'>"
            "① <b style='color:var(--text)'>portal/strategies.json</b> 加一条："
            "id/名称/一句话价值/状态（live·shadow·tool·paused）/入口/记分卡要点"
            " —— 首页组合区、本页、导航自动同步，前端零改动<br>"
            "② 策略自身页面挂门户路由（portal_api.py 一行 + nginx.conf 一行）"
            "<br>③ 计划任务上线（照 deploy_weak_*.ps1 模板）→ 进 "
            "<a href='/sys-health' style='color:var(--link)'>系统自检</a>守护"
            "<br><span style='color:var(--text-dim)'>状态约定：影子=只标注不"
            "</div></details>"
            "<footer class='dw-footer'><div class='dw-footer-left'>"
            "<span class='dw-dot'></span><span>策略资产中心 · 注册表驱动 · 只读研究</span></div>"
            "<div>门户 8734 · <a href='/strategies'>↻ 刷新</a></div></footer>"
            "</body></html>"
            % (e(d["ts"]), len(items), n_shadow, n_live, len(items), n_live,
               n_shadow, n_tool, band, groups_html)
            + tourney_html)
        self._html(body)


def _sa_page(self, q=""):
        # K-STOCK-ANALYSIS-P0F：/sa 升级为「个股全维分析」页（静态文件 + /api/analysis/* 流水线）
        import os as _os
        _p = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "stock_analysis.html")
        if _os.path.exists(_p):
            try:
                return self._html(open(_p, encoding="utf-8").read())
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
        import html as _h
        qe = _h.escape(q or "")
        hist = _sa_hist_read()
        hist_rows = "".join(
            "<tr><td style='padding:7px 10px;border-bottom:1px solid #21262d'>"
            "<a href='javascript:void(0)' onclick=\"go('%s')\" "
            "style='color:#58a6ff;text-decoration:none'>%s</a></td>"
            "<td style='padding:7px 10px;border-bottom:1px solid #21262d'>"
            "%s</td><td style='padding:7px 10px;border-bottom:1px solid "
            "#21262d;color:%s'>%s</td><td style='padding:7px 10px;"
            "border-bottom:1px solid #21262d;color:#8b949e'>%s</td></tr>" % (
                h["q"], h["name"],
                h["px"].split("（")[0],
                {"利多": "#f85149", "利空": "#3fb950",
                 "分歧": "#d29922"}.get(h["bias"], "#8b949e"),
                h["bias"], h["t"])
            for h in hist if h.get("ok"))
        body = """<!DOCTYPE html><html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>个股分析</title><style>
body{background:#0d1117;color:#e6edf3;font-family:-apple-system,
'Segoe UI','Microsoft YaHei',sans-serif;max-width:720px;margin:0 auto;
padding:24px 16px}
input{width:100%%;box-sizing:border-box;padding:14px 16px;font-size:17px;
border:1px solid #30363d;border-radius:10px;background:#161b22;
color:#e6edf3;outline:none}
input:focus{border-color:#58a6ff}
button{margin-top:12px;width:100%%;padding:14px;font-size:16px;
border:none;border-radius:10px;background:#238636;color:#fff;
cursor:pointer;font-weight:600}
button:disabled{background:#21262d;color:#8b949e;cursor:wait}
#st{color:#8b949e;font-size:13.5px;margin-top:12px;min-height:20px}
.rc{border:1px solid #30363d;border-radius:12px;padding:16px;
margin-top:16px;background:#161b22;display:none}
.k{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0}
.k div{flex:1;min-width:120px;border:1px solid #30363d;border-radius:9px;
padding:10px;text-align:center}
.k .l{color:#8b949e;font-size:12px}
.k .v{font-size:19px;font-weight:700;margin-top:3px}
h3{margin:16px 0 6px;font-size:14.5px;color:#e6edf3}
.small{font-size:12.5px;color:#8b949e;line-height:1.7}
.news{font-size:13px;color:#c9d1d9;line-height:1.8;
border-left:2px solid #30363d;padding-left:10px;margin:6px 0}
table{width:100%%;border-collapse:collapse;font-size:13.5px;margin-top:6px}
</style></head><body>
%s
<h2 style='font-size:20px'>🎯 个股分析</h2>
<p class='small'>输入任意股票 → 自动拉行情、近7天新闻，逐条 JEV 判断后给结论。<br>
支持中文名（英伟达）/ 美股（TSLA）/ A股（600519）/ 港股（00700）</p>
<input id='q' placeholder='输入股票名或代码，回车开始' maxlength='20' value='%s'>
<button id='go' onclick='go()'>开始分析</button>
<div id='st'></div>
<div class='rc' id='rc'></div>
<h3>🕘 最近分析（点击重新分析）</h3>
<table><tr style='color:#8b949e'><td style='padding:7px 10px'>股票</td>
<td style='padding:7px 10px'>现价</td><td style='padding:7px 10px'>倾向</td>
<td style='padding:7px 10px'>时间</td></tr>%s</table>
<p class='small' style='margin-top:18px'>约 1 分钟出结果（JEV 逐条判断 + AI 展望）·
结论同步推飞书卡片 · 纸面研究不构成投资建议</p>
<script>
function esc(s){return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;')}
var tmr=null;
function go(preset){
  var q=(preset||document.getElementById('q').value||'').trim();
  if(!q)return;
  document.getElementById('q').value=q;
  var b=document.getElementById('go');b.disabled=true;
  var st=document.getElementById('st'),rc=document.getElementById('rc');
  rc.style.display='none';
  var tips=['正在识别股票…','拉取实时行情…','检索近7天关联新闻…',
            'JEV 逐条判断中…','AI 生成展望…'];
  var i=0;st.textContent='⏳ '+tips[0];
  tmr=setInterval(function(){i=(i+1)%%tips.length;
    st.textContent='⏳ '+tips[i]},9000);
  fetch('/sa/start?q='+encodeURIComponent(q))
    .then(function(r){return r.json()})
    .then(function(j){
      if(j.error){st.textContent='❌ '+j.error;b.disabled=false;return}
      poll(j.job)});
  function poll(job){
    fetch('/sa/result?job='+job).then(function(r){return r.json()})
    .then(function(j){
      if(j.status!='done'){setTimeout(function(){poll(job)},3000);return}
      clearInterval(tmr);b.disabled=false;st.textContent='✅ 完成';
      if(!j.d.ok){st.textContent='❌ '+j.d.msg;return}
      var d=j.d,news=d.judged.map(function(x){
        return '<div class=news>['+esc(x.bias_cn)+' '+x.conf+'%%] '+
        esc(x.title)+'<br><span class=small>'+esc(x.reason)+'</span></div>'
      }).join('')||'<div class=small>（近7天无关联新闻）</div>';
      rc.innerHTML='<div class=k>'+
        '<div><div class=l>综合倾向</div><div class=v>'+esc(d.bias)+
        '</div></div><div><div class=l>信号强度</div><div class=v>'+
        (d.strength>0?'+':'')+d.strength+'</div></div><div>'+
        '<div class=l>关联新闻</div><div class=v>'+d.news_n+
        '</div></div></div>'+
        '<h3>行情 · '+esc(d.name)+'</h3><div class=small>'+esc(d.px)+
        '　|　'+esc(d.stat)+'</div>'+
        '<h3>未来展望（1-2 周）</h3><div class=news style=border-color:#238636>'+
        esc(d.outlook)+'</div><h3>新闻依据</h3>'+news;
      rc.style.display='block';rc.scrollIntoView({behavior:'smooth'});
      setTimeout(function(){location.reload()},60000);
    });
  }
}
document.getElementById('q').addEventListener('keydown',function(e){
  if(e.key==='Enter')go()});
var P=%s;
if(P){setTimeout(function(){go(P)},400)}
</script></body></html>""" % (pill_nav("sa"), qe, hist_rows, json.dumps(q))
        self._html(body)
