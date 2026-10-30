# -*- coding: utf-8 -*-
"""api_backtest 拆分 B7（2026-10-30 P75）：api_backtest_pages_a1.py。

自 api_backtest_pages 纯移动（函数体零改动，首参 self）。
"""

import json
import logging
import os
import sys

from api_common import (
    QBT_DIR, STATION, pill_nav)


def _bt_sectors(self):
        """板块热力页：news_collector/sectors.json（复盘任务落盘）。"""
        import html as _html
        data = {}
        try:
            with open(os.path.join(STATION, "news_collector",
                                   "sectors.json"), encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
        items = data.get("items") or []
        mx = max((abs(s["pct"]) for s in items), default=1.0) or 1.0
        rows = "".join(
            '<div style="display:flex;align-items:center;margin:3px 0">'
            '<div style="width:9em;overflow:hidden;text-overflow:ellipsis;'
            'white-space:nowrap;color:#c9d1d9;font-size:12px" title="%s">%s'
            '</div><div style="flex:1;background:#161b22;border-radius:4px;'
            'overflow:hidden"><div style="height:14px;width:%.1f%%;'
            'margin-left:%.1f%%;background:%s"></div></div>'
            '<div style="width:5em;text-align:right;font-size:12px;'
            'color:%s">%+.2f%%</div></div>'
            % (_html.escape(s["name"]), _html.escape(s["name"]),
               abs(s["pct"]) / mx * 50,
               50.0 if s["pct"] >= 0 else 50 - abs(s["pct"]) / mx * 50,
               "#f85149" if s["pct"] >= 0 else "#3fb950",
               "#f85149" if s["pct"] >= 0 else "#3fb950", s["pct"])
            for s in items)
        head = ("🔥 行业板块热力 · %s（%d 个：%d 涨 / %d 跌）" % (
            data.get("ts", "—"), data.get("count", len(items)),
            data.get("up", 0), (data.get("count", len(items))
                                - data.get("up", 0)))) if items else \
            "🔥 行业板块热力（暂无数据：交易日 15:30 复盘后生成）"
        # 暗色药丸导航（排除当前页 sectors）
        nav = pill_nav("sectors")
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>板块热力</title></head>"
                "<body style='background:#0d1117;color:#c9d1d9;"
                "font-family:Microsoft YaHei,Arial;margin:0;padding:20px'>"
                "<div style='max-width:760px;margin:0 auto'>"
                "<h2 style='font-size:18px'>%s</h2>%s%s"
                "<div style='color:#58606a;font-size:11px;margin-top:16px'>"
                "数据：东方财富/新浪 · 复盘任务落盘 · 不构成投资建议"
                "</div></div></body></html>"
                % (_html.escape(head), nav, rows)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _bt_report_page(self, qs):
        """研报全文页（本地结构化库直读，零外链）：盘前报告第七节标题下钻。
        u=pdf_url → report_texts.jsonl 记录 → 结构化头部 + 正文全文。"""
        import html as _h
        import sys as _sysr
        from urllib.parse import parse_qs, unquote as _uqr
        if QBT_DIR not in _sysr.path:
            _sysr.path.insert(0, QBT_DIR)
        body = [self._BT_CSS, "<h1>📄 券商研报 · 本地全文</h1>"]
        try:
            import report_fetch
            texts = report_fetch.load_texts()
        except Exception as ex:
            self._html("".join(body) + "<div class='err'>正文库不可用："
                       "%s</div>" % _h.escape(str(ex)[:120]))
            return
        u = _uqr((parse_qs(qs).get("u") or [""])[0]).strip()
        rec = texts.get(u)
        if not rec:
            self._html("".join(body) + "<div class='err'>该研报正文尚未"
                       "入库（盘前任务会自动抓取，明早可见；或稍后刷新"
                       "盘前报告触发）。</div><div class='dim'>url: %s</div>"
                       % _h.escape(u[:120]))
            return
        body.append(
            "<div class='card' style='margin-bottom:12px'>"
            "<div style='font-size:17px;font-weight:600'>%s</div>"
            "<div class='dim' style='margin-top:8px'>%s · %s · %s · %s"
            " · %d 页 · %s 字 · 入库 %s</div>%s"
            "<div class='dim' style='margin-top:6px'>来源 PDF：%s"
            "（已本地结构化，无需访问远端）</div></div>"
            "<div style='margin-top:10px'><a href='/premarket'>← 返回"
            "盘前报告</a> · <a href='/bt/analysts'>分析师图谱 →</a></div>"
            % (_h.escape(rec.get("title") or ""),
               _h.escape(rec.get("code") or ""),
               _h.escape(rec.get("org") or ""),
               _h.escape(rec.get("rating") or ""),
               _h.escape(rec.get("date") or ""),
               rec.get("pages") or 0, rec.get("chars") or 0,
               _h.escape(rec.get("fetched_at") or ""),
               (("<div style='margin-top:6px'>分析师：%s</div>" % " ｜ "
                 .join("%s（%s）<span class='dim'>%s %s</span>"
                       % (_h.escape(a.get("name") or ""),
                          _h.escape(a.get("org") or ""),
                          _h.escape(a.get("cert") or ""),
                          _h.escape(a.get("email") or ""))
                       for a in rec.get("analysts") or []))
                if rec.get("analysts") else ""),
               _h.escape(rec.get("url") or "")))
        pp = rec.get("post_perf") or {}
        if pp:
            segs = []
            for k, lab in (("d1", "1日"), ("d5", "5日"),
                           ("d10", "10日"), ("d20", "20日")):
                v = pp.get(k)
                segs.append("<span class='%s'>%s %s</span>"
                            % ("buy" if (v or 0) > 0 else (
                                "avoid" if (v or 0) < 0 else "dim"),
                               lab, ("%+.2f%%" % v) if v is not None else "—"))
            body.append(
                "<div class='card' style='margin-bottom:12px'>"
                "<b>股评后走势</b>（基准 %s 收盘 %.2f）：%s</div>"
                % (_h.escape(pp.get("base_date") or "?"),
                   pp.get("base_close") or 0, " ".join(segs)))
        # AI 语义结构化卡片（Ollama 抽取，JSON + 置信度）
        ai = rec.get("ai")
        if ai:
            bmap = {"bull": ("看多", "buy"), "bear": ("看空", "avoid"),
                    "neutral": ("中性", "dim")}
            blab, bcls = bmap.get(ai.get("bias"), ("中性", "dim"))
            body.append(
                "<div class='card' style='margin-bottom:12px'>"
                "<div style='display:flex;gap:10px;align-items:center;"
                "flex-wrap:wrap'><span class='%s'><b>%s</b></span>"
                "<span class='tag'>%s</span>"
                "<span class='dim'>置信度 %.0f%% · %s · 抽取 %s</span></div>"
                "<div style='margin-top:8px'>判断理由：%s</div>%s%s</div>"
                % (bcls, blab, _h.escape(ai.get("event_type") or ""),
                   (ai.get("conf") or 0) * 100,
                   _h.escape(ai.get("model") or ""),
                   _h.escape(ai.get("ts") or ""),
                   _h.escape(ai.get("reason") or ""),
                   ("<div style='margin-top:6px'>业绩亮点：%s</div>"
                    % _h.escape(ai.get("earnings"))
                    if ai.get("earnings") else ""),
                   ("<div style='margin-top:6px'>目标价："
                    "<b>%s</b></div>" % _h.escape(ai.get("target_price"))
                    if ai.get("target_price") else "")))
            eps = ai.get("eps_forecast") or []
            pts = ai.get("core_points") or []
            rks = ai.get("risks") or []
            if eps or pts or rks:
                li = ""
                if eps:
                    li += ("<div style='margin-top:8px'>📈 "
                           "<b>盈利预测</b><ul style='margin:4px 0 0 20px'>"
                           + "".join("<li>%s</li>" % _h.escape(x)
                                     for x in eps) + "</ul></div>")
                if pts:
                    li += ("<div style='margin-top:8px'>💡 "
                           "<b>核心观点</b><ul style='margin:4px 0 0 20px'>"
                           + "".join("<li>%s</li>" % _h.escape(x)
                                     for x in pts) + "</ul></div>")
                if rks:
                    li += ("<div style='margin-top:8px'>⚠️ "
                           "<b>风险提示</b><ul style='margin:4px 0 0 20px'>"
                           + "".join("<li>%s</li>" % _h.escape(x)
                                     for x in rks) + "</ul></div>")
                body.append("<div class='card' style='margin-bottom:12px'>"
                            "%s</div>" % li)
        # 原文折叠（AI 卡片优先阅读，原文保底可查）
        body.append(
            "<details><summary style='cursor:pointer;padding:6px 0'>"
            "📖 研报原文全文（%s 字）</summary>"
            "<div class='card' style='white-space:pre-wrap;font-size:14px;"
            "line-height:1.9;max-height:70vh;overflow:auto'>%s</div></details>"
            % (rec.get("chars") or 0, _h.escape(rec.get("text") or "(空正文)")))
        self._html("".join(body))


def _bt_ind_drill_page(self, qs):
        """行业下钻页：成分股当日涨跌全量 + 主力资金个股明细（全量条形）。
        供盘前/盘后报告行业名下钻（ind=行业名&date=YYYYMMDD）。"""
        from urllib.parse import parse_qs, unquote as _uq
        import html as _h
        qp = parse_qs(qs)
        ind = _uq((qp.get("ind") or [""])[0]).strip()[:24]
        date = _uq((qp.get("date") or [""])[0]).strip()
        body = [self._BT_CSS]
        sys.path.insert(0, os.path.join(STATION, "quant_backtest"))

        def _fail(msg):
            body.append("<h1>行业下钻</h1><div class='err'>%s</div>"
                        % _h.escape(msg))
            self._html("".join(body))

        try:
            import datamart as dm
            import premarket as pm
            sb = dm.read("stock_basic",
                         fields=["ts_code", "name", "industry"])
        except Exception as ex:
            _fail("数据仓不可用：%s" % str(ex)[:120])
            return
        if sb is None or sb.empty:
            _fail("stock_basic 为空")
            return
        sb = sb.dropna(subset=["industry"])
        sb = sb[sb["industry"].astype(str).str.len() > 0]
        if not ind:
            # 缺省：全行业索引（按成分家数降序，全部可点）
            grp = sb.groupby("industry")["ts_code"].count()
            links = "".join(
                "<a href='/bt/ind_drill?ind=%s' style='display:inline-block;"
                "margin:2px 6px'>%s<span class='dim'>(%d)</span></a>"
                % (_h.escape(k, quote=True), _h.escape(k), v)
                for k, v in grp.sort_values(ascending=False).items())
            body.append("<h1>行业下钻 · 全 %d 个行业</h1>"
                        "<div>点击行业查看成分股涨跌与主力资金明细："
                        "<div style='line-height:2'>%s</div></div>"
                        % (len(grp), links))
            self._html("".join(body))
            return
        grp = sb.groupby("industry")["ts_code"]
        if ind not in set(grp.groups):
            _fail("未找到行业「%s」（stock_basic.industry 口径）"
                  % _h.escape(ind))
            return
        codes = list(grp.get_group(ind))
        names = dict(zip(sb["ts_code"], sb["name"]))
        days = pm._closed_trade_days()
        d0 = date if len(date) == 8 and date.isdigit() else (
            days[-1] if days else "")
        if not d0:
            _fail("交易日历不可用")
            return
        try:
            df = dm.read("daily", start=d0, end=d0, codes=codes,
                         fields=["ts_code", "close", "pct_chg", "amount"])
            mf = dm.read("moneyflow", start=d0, end=d0, codes=codes,
                         fields=["ts_code", "net_mf_amount"])
        except Exception as ex:
            _fail("读取失败：%s" % str(ex)[:120])
            return
        if df is None or df.empty:
            _fail("%s 在 %s 无行情数据（日线未落仓？）"
                  % (_h.escape(ind), d0))
            return
        df = df.dropna(subset=["pct_chg"]).set_index("ts_code")
        mf_yi = {}
        if mf is not None and not mf.empty:
            mf_yi = {r["ts_code"]: float(r["net_mf_amount"]) / 1e4
                     for _, r in mf.iterrows()
                     if r.get("net_mf_amount") == r.get("net_mf_amount")}
        n_up = int((df["pct_chg"] > 0).sum())
        n_dn = int((df["pct_chg"] < 0).sum())
        n_fl = int((df["pct_chg"] == 0).sum())
        n_all = len(df)
        med = float(df["pct_chg"].median())
        total_mf = sum(mf_yi.values())
        nav = ("<div class='nav'><a href='/premarket'>← 盘前报告</a> · "
               "<a href='/afterhours'>盘后复盘</a> · "
               "<span>行业下钻</span></div>")
        # 涨跌分布比例条（涨红/平灰/跌绿）
        w = lambda k: k * 100.0 / max(n_all, 1)
        dist = ("<div style='display:flex;height:14px;border-radius:3px;"
                "overflow:hidden;margin:8px 0'>"
                "<span style='background:#f85149;width:%.1f%%'></span>"
                "<span style='background:#6e7681;width:%.1f%%'></span>"
                "<span style='background:#3fb950;width:%.1f%%'></span>"
                "</div><div><b class='x'>涨 %d</b> · 平 %d · "
                "<b style='color:#3fb950'>跌 %d</b> · 中位 "
                "<b style='color:%s'>%+.2f%%</b></div>"
                % (w(n_up), w(n_fl), w(n_dn), n_up, n_fl, n_dn,
                   "#f85149" if med > 0 else "#3fb950", med))
        # 成分股涨跌全量表（按涨幅降序，涨幅微条形）
        mxp = max(abs(float(v)) for v in df["pct_chg"]) or 1
        rows_px = "".join(
            "<tr><td><a href='/kg/stock/%s'>%s</a></td><td>%s</td>"
            "<td style='color:%s'><b>%+.2f%%</b></td>"
            "<td style='width:30%%'><span style='display:inline-block;"
            "height:8px;border-radius:2px;background:%s;width:%.0f%%'>"
            "</span></td><td class='dim'>%.1f 亿</td></tr>"
            % (c[:6], c[:6], _h.escape(names.get(c, "")),
               "#f85149" if p > 0 else ("#3fb950" if p < 0 else "#8b949e"),
               p, "#f85149" if p > 0 else "#3fb950",
               abs(p) * 100.0 / mxp,
               float(r["amount"]) / 1e5 if r.get("amount") == r.get(
                   "amount") else 0)
            for c, p, r in sorted(
                ((c, float(r["pct_chg"]), r)
                 for c, r in df.iterrows()), key=lambda x: -x[1]))
        # 主力资金个股明细（全量，正红负绿条形）
        mf_html = ""
        if mf_yi:
            mxa = max(abs(v) for v in mf_yi.values()) or 1
            mf_rows = "".join(
                "<tr><td><a href='/kg/stock/%s'>%s</a></td><td>%s</td>"
                "<td style='color:%s'><b>%+.2f 亿</b></td>"
                "<td style='width:30%%'><span style='display:inline-block;"
                "height:8px;border-radius:2px;background:%s;width:%.0f%%'>"
                "</span></td></tr>"
                % (c[:6], c[:6], _h.escape(names.get(c, "")),
                   "#f85149" if v > 0 else "#3fb950", v,
                   "#f85149" if v > 0 else "#3fb950",
                   abs(v) * 100.0 / mxa)
                for c, v in sorted(mf_yi.items(), key=lambda x: -x[1]))
            mf_html = (
                "<h2>主力资金 · 个股明细（%s）</h2>"
                "<div>行业合计 <b style='color:%s'>%+.2f 亿</b> · "
                "覆盖 %d/%d 只（moneyflow 净额，万元→亿）</div>"
                "<table><tr><th>代码</th><th>名称</th><th>净流入</th>"
                "<th>相对规模</th></tr>%s</table>"
                % (d0, "#f85149" if total_mf > 0 else "#3fb950",
                   total_mf, len(mf_yi), n_all, mf_rows))
        body.append(nav)
        body.append(
            "<h1>%s <span class='dim'>（%s · %d 只成分股）</span></h1>"
            % (_h.escape(ind), d0, n_all))
        body.append(dist)
        body.append(
            "<h2>成分股涨跌全景（按涨幅降序，全量）</h2>"
            "<table><tr><th>代码</th><th>名称</th><th>涨跌幅</th>"
            "<th>幅度</th><th>成交额</th></tr>%s</table>" % rows_px)
        body.append(mf_html)
        body.append(
            "<div class='dim'>口径：daily.pct_chg 收盘涨跌 · "
            "moneyflow.net_mf_amount 主力净额 · 与盘前/盘后报告同源 · "
            "涨红跌绿</div>")
        self._html("".join(body))


def _bt_lhb_page(self, qs):
        """龙虎榜下钻页：当日上榜全量（原因/买卖额/席位净额）+ 近60日
        上榜频次与日期 + 板块扎堆（关联炒作识别）。供盘前/盘后报告
        榜列下钻（date=YYYYMMDD）。"""
        from urllib.parse import parse_qs, unquote as _uq, quote as _qe
        import html as _h
        qp = parse_qs(qs)
        date = _uq((qp.get("date") or [""])[0]).strip()
        body = [self._BT_CSS]
        sys.path.insert(0, os.path.join(STATION, "quant_backtest"))

        def _fail(msg):
            body.append("<h1>龙虎榜</h1><div class='err'>%s</div>"
                        % _h.escape(msg))
            self._html("".join(body))

        try:
            import datamart as dm
        except Exception as ex:
            _fail("数据仓不可用：%s" % str(ex)[:120])
            return
        # 读取窗口：锚定 date（缺省今天）前 130 自然日 ≈ 88 交易日，
        # 覆盖近60日频次窗；避免全量扫（首载 14s → 秒级）
        import datetime as _dt
        try:
            _base = _dt.datetime.strptime(date, "%Y%m%d")
        except ValueError:
            _base = _dt.datetime.now()
        _ws = (_base - _dt.timedelta(days=130)).strftime("%Y%m%d")
        try:
            tl = dm.read("ts_top_list", start=_ws,
                         fields=["ts_code", "trade_date", "name",
                                 "pct_change", "net_amount", "l_buy",
                                 "l_sell", "reason"])
        except Exception as ex:
            _fail("ts_top_list 读取失败：%s" % str(ex)[:120])
            return
        if tl is None or tl.empty:
            _fail("ts_top_list 无数据（TDX 龙虎榜源待同步）")
            return
        tl = tl.copy()
        tl["d8"] = tl["trade_date"].astype(str).str[:8]
        all_days = sorted(tl["d8"].unique())
        d0 = date if date in all_days else all_days[-1]
        nav = ("<div class='nav'><a href='/premarket'>← 盘前报告</a> · "
               "<a href='/afterhours'>盘后复盘</a> · "
               "<span>龙虎榜下钻</span></div>")
        # 近60日窗口（按有数据交易日）：频次 + 每股上榜日期
        win = all_days[-60:]
        w60 = tl[tl["d8"].isin(win)]
        freq = w60.groupby("ts_code")["d8"].apply(
            lambda s: sorted(set(s), reverse=True))
        # 当日明细：同股多 reason 去重合并
        sub = tl[tl["d8"] == d0]
        recs = {}
        for r in sub.to_dict("records"):
            c = r["ts_code"]
            g = recs.setdefault(c, {
                "code": c, "name": r.get("name") or "",
                "pct": (float(r["pct_change"])
                        if r.get("pct_change") == r.get("pct_change")
                        else None),
                "buy": 0.0, "sell": 0.0, "net": 0.0,
                "reasons": []})
            for k, kk in (("buy", "l_buy"), ("sell", "l_sell"),
                          ("net", "net_amount")):
                v = r.get(kk)
                if v is not None and v == v:
                    g[k] += float(v)
            rs = str(r.get("reason") or "").strip()
            if rs and rs not in g["reasons"]:
                g["reasons"].append(rs)
        # 机构/陆股通席位净额 + 当日活跃坐席榜（ts_top_inst，exalter 粒度）
        inst = {}
        ti = None
        try:
            ti = dm.read("ts_top_inst", start=d0, end=d0,
                         fields=["ts_code", "exalter", "buy", "sell",
                                 "net_buy"])
            if ti is not None and not ti.empty:
                for r in ti.to_dict("records"):
                    if "机构" in str(r.get("exalter") or "") or \
                       "股通" in str(r.get("exalter") or ""):
                        v = r.get("net_buy")
                        if v is not None and v == v:
                            k = r["ts_code"]
                            inst[k] = inst.get(k, 0.0) + float(v)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
        # 板块（stock_basic.industry 静态映射 ~0.1s；bak_basic 按日读
        # 实测 8-9s 为本页首瓶颈，弃用；94% 覆盖与当日快照相当）
        ind = {}
        try:
            sb = dm.read("stock_basic",
                         fields=["ts_code", "industry"])
            if sb is not None and not sb.empty:
                ind = {r["ts_code"]: str(r.get("industry") or "").strip()
                       for r in sb.to_dict("records")
                       if r.get("industry")
                       and str(r.get("industry")).strip()}
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
        n = len(recs)
        tot_net = sum(v["net"] for v in recs.values()) / 1e8
        # 板块扎堆条形（关联炒作：同板块多票同日上榜）
        ind_n = {}
        for v in recs.values():
            i_ = ind.get(v["code"])
            if i_:
                ind_n[i_] = ind_n.get(i_, 0) + 1
        hot_html = ""
        if ind_n:
            mx = max(ind_n.values())
            hi = sorted(ind_n.items(), key=lambda x: -x[1])[:10]
            hot_html = (
                "<h2>板块扎堆（同日上榜关联）</h2><div class='dim'>"
                "同板块多票上榜 → 游资抱团方向</div>%s" % "".join(
                    "<div style='display:flex;align-items:center;"
                    "gap:6px;margin:2px 0'>"
                    "<span style='width:110px;overflow:hidden;text-overflow:"
                    "ellipsis;white-space:nowrap'>%s</span>"
                    "<span style='flex:1'><span style='display:inline-block;"
                    "height:12px;border-radius:2px;background:#f85149;"
                    "opacity:%.2f;width:%.0f%%'></span></span>"
                    "<span style='width:36px;text-align:right'>%d 票"
                    "</span></div>"
                    % (_h.escape(k), 0.5 + 0.5 * v / mx,
                       v * 100.0 / mx, v)
                    for k, v in hi if v >= 2))
        # 高频榜（近60日≥3次：游资反复做）
        freq_html = ""
        hf = [(c, len(ds)) for c, ds in freq.items() if len(ds) >= 3]
        if hf:
            hf.sort(key=lambda x: -x[1])
            nm = {r["ts_code"]: r.get("name") for _, r in
                  tl.dropna(subset=["name"]).drop_duplicates(
                      "ts_code").iterrows()} if "name" in tl.columns else {}
            freq_html = (
                "<h2>高频上榜（近 %d 日 ≥3 次，游资反复做）</h2>"
                "<table><tr><th>代码</th><th>名称</th><th>次数</th>"
                "<th>上榜日期（近5次）</th></tr>%s</table>"
                % (len(win), "".join(
                    "<tr><td><a href='/kg/stock/%s'>%s</a></td>"
                    "<td>%s</td><td><b class='buy'>%d</b></td>"
                    "<td class='dim'>%s</td></tr>"
                    % (c[:6], c[:6], _h.escape(str(nm.get(c, ""))), k,
                       "、".join(freq[c][:5]))
                    for c, k in hf[:12])))
        # 当日活跃坐席榜（ts_top_inst exalter 聚合，净额绝对值排序，
        # 坐席名可点击 → /bt/seat 坐席历史下钻）
        seat_html = ""
        if ti is not None and not ti.empty:
            ti2 = ti.copy()
            ti2["ex"] = ti2["exalter"].astype(str).str.strip()
            g = ti2.groupby("ex").agg(
                n=("ts_code", "nunique"), buy=("buy", "sum"),
                sell=("sell", "sum"), net=("net_buy", "sum"))
            g = g.reindex(g["net"].abs().sort_values(ascending=False).index)
            seat_html = (
                "<h2>当日活跃坐席（净额绝对值 Top 15，点击看席位上榜历史）"
                "</h2><table><tr><th>坐席</th><th>票数</th><th>买入(亿)</th>"
                "<th>卖出(亿)</th><th>净额(亿)</th></tr>%s</table>"
                % "".join(
                    "<tr><td style='max-width:330px;overflow:hidden;"
                    "text-overflow:ellipsis;white-space:nowrap'>"
                    "<a href='/bt/seat?ex=%s' title='%s'>%s</a></td>"
                    "<td><b class='buy'>%d</b></td><td class='dim'>%.2f</td>"
                    "<td class='dim'>%.2f</td>"
                    "<td style='color:%s'><b>%+.2f</b></td></tr>"
                    % (_qe(e_), _h.escape(e_), _h.escape(e_),
                       int(r["n"]), float(r["buy"]) / 1e8,
                       float(r["sell"]) / 1e8,
                       "#f85149" if r["net"] > 0 else "#3fb950",
                       float(r["net"]) / 1e8)
                    for e_, r in g.head(15).iterrows()))
        # 当日全量明细表（净额降序）
        rows = "".join(
            "<tr><td><a href='/kg/stock/%s'>%s</a></td><td>%s</td>"
            "<td style='color:%s'><b>%+.2f%%</b></td>"
            "<td class='dim'>%s</td><td class='dim'>%.2f</td>"
            "<td class='dim'>%.2f</td>"
            "<td style='color:%s'><b>%+.2f</b></td>"
            "<td style='color:%s'>%s</td>"
            "<td class='buy' title='%s'>%d 次</td>"
            "<td class='dim' title='%s'>%s</td></tr>"
            % (v["code"][:6], v["code"][:6], _h.escape(v["name"]),
               "#f85149" if (v["pct"] or 0) > 0 else
               ("#3fb950" if (v["pct"] or 0) < 0 else "#8b949e"),
               v["pct"] if v["pct"] is not None else 0.0,
               _h.escape(ind.get(v["code"]) or "—"),
               v["buy"] / 1e8, v["sell"] / 1e8,
               "#f85149" if v["net"] > 0 else "#3fb950",
               v["net"] / 1e8,
               "#f85149" if inst.get(v["code"], 0) > 0 else
               ("#3fb950" if inst.get(v["code"], 0) < 0 else "#8b949e"),
               ("%+.2f" % (inst[v["code"]] / 1e8))
               if v["code"] in inst else "—",
               _h.escape("；".join(v["reasons"])),
               len(freq.get(v["code"], [])),
               _h.escape("、".join(freq.get(v["code"], [])[:5])),
               "等 %d 次" % len(freq.get(v["code"], []))
               if len(freq.get(v["code"], [])) > 5 else
               ("、".join(freq.get(v["code"], [])[:5]) or "—"))
            for v in sorted(recs.values(), key=lambda x: -x["net"]))
        body.append(nav)
        body.append(
            "<h1>龙虎榜下钻 <span class='dim'>（%s）</span></h1>"
            "<div>上榜 <b class='buy'>%d 只</b> · 榜内净额合计 "
            "<b style='color:%s'>%+.2f 亿</b> · 机构/股通席位覆盖 "
            "%d 只</div>" % (d0, n,
                             "#f85149" if tot_net > 0 else "#3fb950",
                             tot_net, len(inst)))
        body.append(
            "<h2>当日上榜全量（按净额降序）</h2>"
            "<table><tr><th>代码</th><th>名称</th><th>涨幅</th>"
            "<th>板块</th><th>买入(亿)</th><th>卖出(亿)</th><th>净额(亿)"
            "</th><th>机构(亿)</th><th>近60日</th><th>上榜日期</th>"
            "</tr>%s</table>" % rows)
        body.append(seat_html)
        body.append(hot_html)
        body.append(freq_html)
        body.append(
            "<div class='dim'>口径：ts_top_list（TDX 龙虎榜，金额单位元，"
            "÷1e8 折亿）；机构/股通=ts_top_inst 席位名含「机构/股通」"
            "聚合；坐席=ts_top_inst exalter 席位明细（每票代表席位一行，"
            "非完整前五买卖席位）；近60日=有数据交易日滚动窗；同股多条"
            "上榜原因去重合并；涨红跌绿</div>")
        self._html("".join(body))
