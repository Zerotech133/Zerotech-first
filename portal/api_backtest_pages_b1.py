# -*- coding: utf-8 -*-
"""api_backtest 拆分 B7（2026-10-30 P75）：api_backtest_pages_b1.py。

自 api_backtest_pages 纯移动（函数体零改动，首参 self）。
"""

import json
import logging
import os

from api_common import (
    QBT_DIR, REPORTS_DIR, ROOT, STATION, _read_json, log_exc, pill_nav)


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
