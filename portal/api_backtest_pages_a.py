# -*- coding: utf-8 -*-
"""api_backtest 拆分 B3（2026-10-30 P70）：回测页 A 族渲染（sectors/report/ind/lhb/seat/stock/jev/brief/news）。

自 portal/api_backtest.py Handler 方法纯移动（方法体零改动，签名不变，首参 self）；
依赖 api_common + api_backtest 的 _bt_jev_news/_bt_resolve（运行时导入，无循环）。
"""
import json
import logging
import os
import sys
import time

from api_common import (
    CANDLE_CN, QBT_DIR, ROOT, STATION, _matrix_cards, _read_json, pill_nav,
    _stock_chip_summary, _stock_sector_rows)

from api_backtest import _bt_jev_news, _bt_resolve


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


def _bt_seat_page(self, qs):
        """坐席（营业部/机构席位）下钻页：该坐席近60日上榜明细
        （日期/股票/买卖净额）+ 常做票。供龙虎榜页坐席名下钻
        （ex=<urlencoded 席位名>）。"""
        from urllib.parse import parse_qs, quote as _q, \
            unquote as _uq
        import html as _h
        qp = parse_qs(qs)
        ex = _uq((qp.get("ex") or [""])[0]).strip()[:80]
        body = [self._BT_CSS]
        sys.path.insert(0, os.path.join(STATION, "quant_backtest"))

        def _fail(msg):
            body.append("<h1>坐席</h1><div class='err'>%s</div>"
                        % _h.escape(msg))
            self._html("".join(body))

        if not ex:
            # K-STOCK-123 席位画像榜（无参入口）：读日更管线
            # portal/data/seat_profiles.json（seat_profile_daily.py 产出）
            prof = _read_json(os.path.join(ROOT, "data",
                                           "seat_profiles.json")) or {}
            seats = prof.get("seats", [])
            if not seats:
                _fail("席位画像未生成（portal/data/seat_profiles.json"
                      " 缺失）——日更管线 seat_profile_daily.py 每交易日"
                      " 23:50 产出，或手动：python quant_backtest/"
                      "seat_profile_daily.py")
                return
            def _wr_cell(s):
                wr = s.get("wr_win_rate")
                if wr is None:
                    return "<span class='dim'>n=%d</span>" % s.get("wr_n", 0)
                col = "#3fb950" if wr >= 0.5 else "#f85149"
                ar = s.get("wr_avg_ret")
                return ("<b style='color:%s'>%.1f%%</b> <span class='dim'>"
                        "(n=%d%s)</span>"
                        % (col, wr * 100, s.get("wr_n", 0),
                           " · 均向 %+.2f%%" % (ar * 100)
                           if ar is not None else ""))
            trs = "".join(
                "<tr><td><a href='/bt/seat?ex=%s'>%s</a>%s</td>"
                "<td><b class='buy'>%d</b></td><td>%d</td>"
                "<td style='color:%s'>%+.1f</td><td>%s</td>"
                "<td>%s</td><td class='dim'>%s</td></tr>"
                % (_h.escape(_q(ex2 := s.get("seat", ""))),
                   _h.escape(ex2[:26]),
                   " <span style='font-size:11px;color:#d29922'>⚡%s"
                   "</span>" % _h.escape(s.get("youzi", ""))
                   if s.get("youzi") else "",
                   s.get("n_days", 0), s.get("n_stocks", 0),
                   "#f85149" if s.get("net_yi", 0) > 0 else "#3fb950",
                   s.get("net_yi", 0.0), _h.escape(s.get("style", "—")),
                   _wr_cell(s),
                   _h.escape("、".join(
                       (t.get("name") or t.get("code", ""))
                       for t in (s.get("top_stocks") or [])[:3])
                       or "—"))
                for s in seats)
            body.append(
                "<div class='nav'><a href='/bt/lhb'>← 龙虎榜</a> · "
                "<span>席位画像</span></div>"
                "<h1>🎩 游资席位画像榜</h1>"
                "<div class='dim'>basis %s · 近 %d 个有数据交易日 · "
                "%d 席位（按上榜日数降序 top） · 点席位名下钻明细</div>"
                % (_h.escape(str(prof.get("basis", "?"))),
                   prof.get("window_days", 0), prof.get("n_seats", 0)))
            body.append(
                "<h2>画像总榜</h2>"
                "<table><tr><th>席位</th><th>上榜日</th><th>票数</th>"
                "<th>净额(亿)</th><th>风格</th><th>方向胜率</th>"
                "<th>常做票</th></tr>%s</table>"
                "<div class='dim' style='margin-top:10px'>%s</div>"
                % (trs, _h.escape(prof.get("note", ""))))
            self._html("".join(body))
            return
        try:
            import datamart as dm
        except Exception as e_:
            _fail("数据仓不可用：%s" % str(e_)[:120])
            return
        # 近130自然日窗口（同 lhb 页，覆盖60交易日）
        import datetime as _dt
        _ws = (_dt.datetime.now() - _dt.timedelta(days=130))\
            .strftime("%Y%m%d")
        try:
            ti = dm.read("ts_top_inst", start=_ws,
                         fields=["ts_code", "trade_date", "exalter",
                                 "buy", "sell", "net_buy"])
        except Exception as e_:
            _fail("ts_top_inst 读取失败：%s" % str(e_)[:120])
            return
        if ti is None or ti.empty:
            _fail("ts_top_inst 无数据（TDX 席位源待同步）")
            return
        ti = ti.copy()
        ti["d8"] = ti["trade_date"].astype(str).str[:8]
        rows_ = ti[ti["exalter"].astype(str).str.strip() == ex]
        if rows_.empty:
            _fail("坐席「%s」无上榜记录" % ex)
            return
        # 近60日窗口（按该表有数据交易日）
        win = set(sorted(ti["d8"].unique())[-60:])
        rows_ = rows_[rows_["d8"].isin(win)]
        if rows_.empty:
            _fail("坐席「%s」近 %d 个有数据交易日无上榜记录"
                  % (ex, len(win)))
            return
        # 票名（ts_top_list.name，同窗口）
        nm = {}
        try:
            tl = dm.read("ts_top_list", start=_ws,
                         fields=["ts_code", "name"])
            if tl is not None and not tl.empty:
                nm = tl.dropna(subset=["name"]).drop_duplicates(
                    "ts_code").set_index("ts_code")["name"].to_dict()
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)

        def _f(v):
            try:
                v = float(v)
                return v if v == v else 0.0
            except (TypeError, ValueError):
                return 0.0

        n_days = rows_["d8"].nunique()
        n_stk = rows_["ts_code"].nunique()
        tot_b = _f(rows_["buy"].sum()) / 1e8
        tot_s = _f(rows_["sell"].sum()) / 1e8
        tot_n = _f(rows_["net_buy"].sum()) / 1e8
        # 明细（日期降序，同日净额降序）
        rrs = sorted(rows_.to_dict("records"),
                     key=lambda r: (r["d8"], _f(r.get("net_buy"))),
                     reverse=True)
        det = "".join(
            "<tr><td class='dim'>%s</td>"
            "<td><a href='/kg/stock/%s'>%s</a></td><td>%s</td>"
            "<td class='dim'>%.2f</td><td class='dim'>%.2f</td>"
            "<td style='color:%s'><b>%+.2f</b></td></tr>"
            % (r["d8"], str(r["ts_code"])[:6], str(r["ts_code"])[:6],
               _h.escape(str(nm.get(r["ts_code"], "")) or "—"),
               _f(r.get("buy")) / 1e8, _f(r.get("sell")) / 1e8,
               "#f85149" if _f(r.get("net_buy")) > 0 else "#3fb950",
               _f(r.get("net_buy")) / 1e8)
            for r in rrs)
        # 常做票（≥2 日上榜：坐席常客/票席关联）
        cnt = rows_.groupby("ts_code")["d8"].nunique()
        reg = [(c, int(k),
                _f(rows_[rows_["ts_code"] == c]["net_buy"].sum()) / 1e8)
               for c, k in cnt.items() if k >= 2]
        reg.sort(key=lambda x: -x[2])
        reg_html = ""
        if reg:
            reg_html = (
                "<h2>常做票（近 %d 日 ≥2 次上榜，席位常客/关联）</h2>"
                "<table><tr><th>代码</th><th>名称</th><th>次数</th>"
                "<th>累计净额(亿)</th></tr>%s</table>"
                % (len(win), "".join(
                    "<tr><td><a href='/kg/stock/%s'>%s</a></td>"
                    "<td>%s</td><td><b class='buy'>%d</b></td>"
                    "<td style='color:%s'>%+.2f</td></tr>"
                    % (c[:6], c[:6],
                       _h.escape(str(nm.get(c, "")) or "—"), k,
                       "#f85149" if v > 0 else "#3fb950", v)
                    for c, k, v in reg[:12])))
        body.append(
            "<div class='nav'><a href='/bt/lhb'>← 龙虎榜</a> · "
            "<a href='/bt/seat'>席位画像榜</a> · "
            "<span>坐席下钻</span></div>"
            "<h1>坐席 <span class='dim'>(%s)</span></h1>"
            "<div>近 %d 个有数据交易日 · 上榜 <b class='buy'>%d 日</b> / "
            "<b class='buy'>%d 票</b> · 合计买 <b>%.2f 亿</b> / 卖 "
            "<b>%.2f 亿</b> · 净额 <b style='color:%s'>%+.2f 亿</b></div>"
            % (_h.escape(ex), len(win), n_days, n_stk, tot_b, tot_s,
               "#f85149" if tot_n > 0 else "#3fb950", tot_n))
        # K-STOCK-123 画像摘要行（命中日更画像则显示风格/胜率徽标）
        _prof = _read_json(os.path.join(ROOT, "data",
                                        "seat_profiles.json")) or {}
        _me = next((s for s in _prof.get("seats", [])
                    if s.get("seat") == ex), None)
        if _me:
            _wr = _me.get("wr_win_rate")
            _wr_txt = ("—" if _wr is None else
                       "<b style='color:%s'>%.1f%%</b> (n=%d)"
                       % ("#3fb950" if _wr >= 0.5 else "#f85149",
                          _wr * 100, _me.get("wr_n", 0)))
            body.append(
                "<div class='dim' style='margin-top:6px'>🎩 画像（%s "
                "· 近%d日榜）：%s风格 <b>%s</b> · 方向胜率 %s</div>"
                % (_h.escape(str(_prof.get("basis", "?"))),
                   _prof.get("window_days", 0),
                   "⚡<b>%s</b> · " % _h.escape(_me.get("youzi", ""))
                   if _me.get("youzi") else "",
                   _h.escape(_me.get("style", "—")), _wr_txt))
        body.append(
            "<h2>上榜明细（日期降序）</h2>"
            "<table><tr><th>日期</th><th>代码</th><th>名称</th>"
            "<th>买入(亿)</th><th>卖出(亿)</th><th>净额(亿)</th></tr>%s"
            "</table>" % det)
        body.append(reg_html)
        body.append(
            "<div class='dim'>口径：ts_top_inst 席位明细（每票取代表席位"
            "一行，非完整前五买卖席位）；金额单位元 ÷1e8 折亿；窗口=近 "
            "%d 个有数据交易日；涨红跌绿</div>" % len(win))
        self._html("".join(body))


def _bt_stock_page(self, qs):
        """个股全景页：行情 + 4策略信号 + 共振分解 + JEV下钻 + AI展望。"""
        from urllib.parse import parse_qs, unquote as _uq
        import html as _h
        qp = parse_qs(qs)
        q = _uq((qp.get("c") or qp.get("q") or [""])[0]).strip()[:24]
        body = [self._BT_CSS]
        if not q:
            body.append(self._bt_stock_form())
            self._html("".join(body))
            return
        e = _bt_resolve(q)
        if not e:
            body.append(self._bt_stock_form(
                "未识别「%s」。支持：中文名 / 英文代码 / 6位A股 / 5位港股"
                "（如 英伟达 · TSLA · 600519 · 00700）" % _h.escape(q)))
            self._html("".join(body))
            return
        name, code = e["name"], e.get("code", "")
        aliases = e.get("aliases") or []
        mkt_key = ("crypto" if "/" in code else "us" if code.startswith("gb_")
                   else "hk" if code.startswith("rt_hk") else "cn")
        mkt_label = {"cn": "A股", "us": "美股", "hk": "港股",
                     "crypto": "加密"}[mkt_key]
        # 市场子导航
        nav = ["<div class='nav'><a href='/bt'>🎯 选股中心</a>"]
        for k, lb in (("cn", "🇨🇳 A股"), ("us", "🇺🇸 美股"),
                      ("hk", "🇭🇰 港股"), ("crypto", "🪙 加密")):
            nav.append(("<span>%s</span>" % lb) if k == mkt_key
                       else "<a href='/bt/m/%s'>%s</a>" % (k, lb))
        body.append("".join(nav) + "</div>")
        body.append(self._back_bar_html())
        body.append("<h1>%s <span class='dim'>(%s · %s)</span></h1>"
                    % (_h.escape(name), _h.escape(code), mkt_label))
        # 🎯 综合研判 + 💼 操作建议（个股缠论日线结构 × 大盘环境，
        # 四市场通用——结构引擎市场无关；数据不足不渲染）
        try:
            import sys as _s9
            if r'C:\项目\老田的AI助手\quant_backtest' not in _s9.path:
                _s9.path.insert(0, r'C:\项目\老田的AI助手\quant_backtest')
            import chanlun_market as _CM9
            _sv = _CM9.stock_view(code)
            _lv9 = _CM9.market_brief()
            _vd9 = _CM9.verdict(_lv9)
        except Exception:
            _sv = None
        if _sv and _sv.get("ok"):
            _trc9 = {"上升": "#f85149", "下降": "#3fb950"}.get(
                _sv.get("trend"), "#d29922")
            _ph9 = []
            try:
                _pmap9 = {"cn": "chanlun_pool.jsonl",
                          "us": "chanlun_pool_us.jsonl",
                          "hk": "chanlun_pool_hk.jsonl"}
                _pf9 = _pmap9.get(mkt_key)
                if _pf9:
                    import re as _r9
                    _pdir9 = (r"C:\项目\老田的AI助手\quant_backtest\data")
                    _prows9 = [json.loads(_l) for _l in open(
                        os.path.join(_pdir9, _pf9), encoding="utf-8")
                        if _l.strip()]
                    _cands9 = {code, code.replace("gb_", ""),
                               code.replace("rt_hk", "")}
                    _m9 = _r9.search(r"(\d{6})", code)
                    if _m9:
                        _b69 = _m9.group(1)
                        _cands9 |= {_b69, "%s.SH" % _b69, "%s.SZ" % _b69,
                                    "%s.BJ" % _b69}
                    _ph9 = [r for r in _prows9
                            if r.get("code") in _cands9][-2:]
            except Exception:
                _ph9 = []
            _pool9 = ""
            if _ph9:
                _pool9 = " ｜ 池内信号：" + "、".join(
                    "<b>%s</b>(%s)" % (r.get("type"),
                                       (str(r.get("dt")) or "")[5:])
                    for r in _ph9)
            elif _sv.get("sig_txt"):
                _pool9 = " ｜ 最近信号 <b>%s</b>" % _sv["sig_txt"]
            _zg9, _zd9 = _sv.get("zg"), _sv.get("zd")
            _zone9 = ""
            if _zg9 and _zd9:
                _zone9 = " · 日线中枢 [%.2f, %.2f]" % (_zd9, _zg9)
            body.append(
                "<div class='sec'><h2>🎯 综合研判"
                "<span class='dim' style='font-size:12px;margin-left:8px'>"
                "个股缠论日线结构 × 大盘环境 · 引擎实时</span></h2>"
                "<div style='line-height:1.9'>"
                "走势：<b style='color:%s'>%s</b> · 现价 <b>%.2f</b> %s%s%s"
                "<br><span class='dim'>观察：%s ｜ 大盘环境：%s</span>"
                "%s</div></div>"
                % (_trc9, _sv.get("trend") or "—", _sv.get("px") or 0,
                   _sv.get("pos") or "—", _zone9, _pool9,
                   _sv.get("tip") or "—", _vd9[0],
                   (("<details style='margin-top:8px'><summary style="
                     "'cursor:pointer'>📈 缠论结构图（15:35 盘后生成）"
                     "</summary><img src='/chanlun-png?c=%s' "
                     "loading='lazy' style='max-width:100%%' "
                     "onerror=\"this.parentElement.style.display='none'\">"
                     "</details>") % code) if mkt_key == "cn" else ""))
            # 💼 操作建议：个股关键位观察 + 大盘仓位档（结构化派生）
            try:
                _tp9 = _CM9.trade_plan(_lv9)
                _dyn9 = _CM9.dynamic_pos(_lv9, write=False)
            except Exception:
                _tp9, _dyn9 = None, None
            if _tp9:
                _zd9v = _zd9 if isinstance(_zd9, float) else None
                if _sv.get("pos") == "中枢下方" and _zd9v:
                    _buy9 = "收复 <b>%.2f</b>（中枢下沿=重回中枢）再看上沿" % _zd9v
                    _sell9 = "反抽不回 <b>%.2f</b>=三卖，离场观望" % _zd9v
                elif _zd9v:
                    _buy9 = "回踩不破 <b>%.2f</b>（中枢上沿=三买观察）分批参与" % _zg9
                    _sell9 = "失守 <b>%.2f</b>（中枢下沿）=结构走坏离场" % _zd9v
                else:
                    _buy9, _sell9 = _sv.get("tip") or "—", "—"
                # 仓位管理三行：分批阶梯/调仓触发（大盘 POS_MGMT 同源）
                # + 池内信号联动提示（个股级增量维度）
                _mg9 = _tp9.get("mgmt") or {}
                _sig9 = ""
                if _ph9:
                    _lt9 = _ph9[-1].get("type") or ""
                    _ld9 = (str(_ph9[-1].get("dt")) or "")[5:]
                    if "卖" in _lt9:
                        _sig9 = ("<br>⚠️ 池内信号联动：<b>%s</b>(%s) 在身——"
                                 "加仓需等反向信号确认，反抽注意减"
                                 % (_lt9, _ld9))
                    elif "买" in _lt9:
                        _sig9 = ("<br>✅ 池内信号联动：<b>%s</b>(%s)——"
                                 "回踩关键位可按分批节奏参与" % (_lt9, _ld9))
                body.append(
                    "<div class='sec'><h2>💼 操作建议"
                    "<span class='dim' style='font-size:12px;margin-left:8px'>"
                    "研究视角 · 非投资建议</span></h2>"
                    "<div style='line-height:1.9'>"
                    "🎯 买点观察：%s<br>"
                    "🛑 卖点防守：%s<br>"
                    "📊 仓位参考：大盘当前 <b>%s</b> ｜ 单票上限 %s"
                    "<br>📈 加仓节奏：%s"
                    "<br>🔄 调仓触发：%s%s"
                    "</div></div>"
                    % (_buy9, _sell9,
                       (_dyn9 or {}).get("name") or "—",
                       _mg9.get("cap") or "—",
                       _mg9.get("steps") or "—",
                       _mg9.get("rebalance") or "—", _sig9))
        # K235 结论置顶 → K239 扩容为「操作面板」：目标价体系 +
        # 操作方案（贴线做T/破线走人）+ 平均筹码 + 所属板块联动
        if mkt_key == "cn":
            try:
                _stp = self._chanlun_sl_tp(code)
            except Exception:
                _stp = None
            if _stp:
                _px = _stp.get("px")

                def _pv(base):
                    try:
                        v = (float(base) / float(_px) - 1) * 100
                        return "<span class='dim'>%+.1f%%</span>" % v
                    except Exception:
                        return ""

                # 📋 操作方案：多级别中枢参数 → 贴线做T/破线走人提示
                _zd, _zg = _stp.get("dzd"), _stp.get("dzg")
                if _px and _zg and _px > _zg:
                    _plan = ("日线3买强结构：回踩 <b>%.2f</b> 不破可低吸"
                             "做 T；<b style='color:#f85149'>跌破 %.2f"
                             "（中枢上沿=结构失效）坚决走</b>"
                             % (_zg, _stp["stop"]))
                elif _zd and _zg:
                    _plan = ("日线中枢 %.2f~%.2f：贴 <b>%.2f</b> 下沿"
                             "低吸、<b>%.2f</b> 上沿减仓做 T；<b style="
                             "'color:#f85149'>跌破 %.2f（最近笔低点）"
                             "坚决走</b>"
                             % (_zd, _zg, _zd, _zg, _stp["stop"]))
                else:
                    _plan = ("以 <b style='color:#f85149'>%.2f</b> 为"
                             "纪律线，跌破坚决走" % _stp["stop"])
                # 💰 平均筹码（本地 cyq_chips 最新日，不在线拉不阻塞）
                _chip = _stock_chip_summary(code, _px)
                _chip_html = ""
                if _chip and _chip.get("avg"):
                    _pos_s = ""
                    if _chip.get("pos") is not None:
                        _pc = "#f85149" if _chip["pos"] >= 0 else "#3fb950"
                        _pos_s = ("，现价在均成本<b style='color:%s'>%s%%"
                                  "</b>" % (_pc, "%+.1f" % _chip["pos"]))
                    _win_s = ""
                    if _chip.get("winner") is not None:
                        _win_s = " · 获利盘 <b>%s%%</b>" % ("%.0f"
                                                            % _chip["winner"])
                    _chip_html = ("　💰 <b>平均筹码</b> %.2f%s%s<span "
                                  "class='dim' style='font-size:11.5px'>"
                                  "（%s 筹码口径）</span>"
                                  % (_chip["avg"], _pos_s, _win_s,
                                     _chip["d"]))
                # 🏛 所属板块联动（tdx 板块指数当日/5日涨跌 + RPS）
                _sec_html = ""
                try:
                    _secs = _stock_sector_rows(code)
                except Exception:
                    _secs = []
                if _secs:
                    _parts = []
                    for _r in _secs:
                        _c1 = "—" if _r["chg1"] is None \
                            else "%+.1f%%" % _r["chg1"]
                        _cc = "#f85149" if (_r["chg1"] or 0) >= 0 \
                            else "#3fb950"
                        _c5 = "—" if _r["chg5"] is None \
                            else "%+.1f%%" % _r["chg5"]
                        _rp = "—" if _r["rps50"] is None \
                            else "%d" % round(_r["rps50"])
                        _parts.append(
                            "%s <b style='color:%s'>%s</b>"
                            "<span class='dim' style='font-size:11px'>"
                            "（5日 %s · RPS %s）</span>"
                            % (_h.escape(str(_r["sector"])), _cc, _c1,
                               _c5, _rp))
                    _sec_html = ("　🏛 <b>所属板块</b> " + " ｜ ".join(
                        _parts))
                body.append(
                    "<div class='card' style='border-color:#58a6ff;"
                    "line-height:2.0'>"
                    "🎯 <b>结论速览</b>　🛑 止损 <b style='color:#f85149'>"
                    "%.2f</b> %s<span class='dim'>（%s）</span> ｜ 🎯 "
                    "目标1 <b style='color:#3fb950'>%.2f</b> %s ｜ 目标2 "
                    "<b style='color:#3fb950'>%.2f</b> %s<span class='dim'>"
                    "（%s）</span>"
                    "<br>📋 <b>操作方案</b> %s"
                    "%s%s　<span class='dim'>四维共振结论见③</span>"
                    "<div style='margin-top:6px'><a class='kpb' href="
                    "'/bt/analyst-report?c=%s' style='text-decoration:"
                    "none;display:inline-block'>🌊 青萍之末分析师报告"
                    "（图文·可一键发飞书）</a></div></div>"
                    % (_stp["stop"], _pv(_stp["stop"]),
                       _h.escape(str(_stp.get("basis") or "")),
                       _stp["tp1"], _pv(_stp["tp1"]),
                       _stp["tp2"], _pv(_stp["tp2"]),
                       _h.escape(str(_stp.get("tpb") or "")),
                       _plan, _chip_html, _sec_html,
                       _h.escape(str(code))))
        # K77: 缠论结构图跟随个股（A股专属：引擎只跑全A）
        if mkt_key == "cn":
            body.append(self._chanlun_stock_sec(code))

        # ── 数据收集（提前，供方法论矩阵 + 详情区块共用）──────────
        e = _h.escape

        # 行情
        qt = None
        try:
            sys.path.insert(0, os.path.join(STATION, "news_collector"))
            import stock_analyze
            qt = stock_analyze.fetch_quote(code)
        except Exception:
            qt = None

        # 共振快照（rr = 本票在自选池的共振结果）
        snap, rr = {}, None
        try:
            snap = json.load(open(os.path.join(
                STATION, "quant_backtest", "reports", "rps_latest.json"),
                encoding="utf-8"))
            rr = next((x for x in snap.get("res", [])
                       if x.get("code") == code or x.get("name") == name),
                      None)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)

        # 4 策略信号状态（实时算；供矩阵计数 + 详情表）
        SIG4_LIST = (("trend_follow", "趋势跟随"),
                     ("ma_cross", "均线交叉"),
                     ("rsi_reversal", "RSI反转"),
                     ("boll_breakout", "布林突破"))
        sig_states = {}   # sn -> "多头"|"空头"|"空仓"|"—"
        df = None         # K235: 预置 None（import 失败时行情卡量比安全降级）
        sig_err = ""
        try:
            from datetime import datetime as _dt, timedelta as _td
            sys.path.insert(0, os.path.join(STATION, "quant_backtest"))
            import runner as _R
            import data_loader as _D
            import strategies as _S
            start = (_dt.now() - _td(days=130)).strftime("%Y-%m-%d")
            df = None
            for _mk, _sy, _nm in _R.resolve_watchlist(
                    [{"name": name, "code": code, "aliases": aliases}]):
                df = _D.load_bars(_mk, [_sy], start, None).get(_sy)
                if df is not None:
                    break
            if df is None or len(df) < 55:
                sig_err = "无足够历史数据"
            else:
                for sn, _cn in SIG4_LIST:
                    try:
                        sv = int(_S.REGISTRY[sn](
                            df, dict(_R.CFG["strategies"].get(sn, {}))
                            ).values[-1])
                        sig_states[sn] = ("多头" if sv > 0 else
                                          "空头" if sv < 0 else "空仓")
                    except Exception:
                        sig_states[sn] = "—"
        except Exception as ex:
            import traceback
            try:
                with open(r"C:\Temp\bt_stock_err.log", "a",
                          encoding="utf-8") as f:
                    f.write(time.strftime("%m-%d %H:%M:%S ") + q + "\n")
                    traceback.print_exc(file=f)
            except OSError as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
            sig_err = "技术引擎暂不可用（%s）" % e(str(ex)[:60])

        # 多策略共振（cross_poll 注册表驱动聚合：各策略对该票的命中、
        # 方向、工作台 URL 全部来自注册表——新增策略改 cross_poll 即可，
        # 本页矩阵自动跟随，零改这里）
        cross_data, cross_hit = {"strategies": []}, None
        try:
            sys.path.insert(0, os.path.join(STATION, "quant_backtest"))
            import cross_poll as _CP
            cross_data = _CP.build_cross() or {"strategies": []}
            nc = _CP.norm_code(code)
            cross_hit = next((st for st in cross_data.get("stocks", [])
                              if st.get("code") == nc), None)
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)

        # JEV 关联新闻
        items = _bt_jev_news(name, aliases)

        # 图谱面数据（S2 时间轴 + S3 图谱面共用，A股一次查询；
        # 美股/港股/加密无图谱，st_kg=None → 各模块显示不适用说明）
        st_kg, _er = None, {"n": 0, "bias_stat": {}, "events": []}
        _kg_ts = ""
        import re as _re_kg
        _m6 = _re_kg.search(r"(\d{6})", code or "")
        if _m6:
            _d6 = _m6.group(1)
            _kg_ts = (_d6 + ".SH" if _d6[0] == "6" else _d6 + ".SZ"
                      if _d6[0] in "03" else _d6 + ".BJ"
                      if _d6[0] in "48" else "")
        if _kg_ts:
            try:
                if QBT_DIR not in sys.path:
                    sys.path.insert(0, QBT_DIR)
                import kg_portal as _KP
                _r = _KP.stock(_kg_ts, days=90)
                if _r and _r.get("ok", True):
                    st_kg = _r
            except Exception:
                st_kg = None
            try:
                from event_graph import events_for_stock as _egf
                _er = _egf(_kg_ts, days=30)
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)

        # ── 渲染 ────────────────────────────────────────────
        # ① 行情（新浪快照；K235 补全基础行情：昨收/开盘/高低/涨跌幅/
        # 成交量/成交额 + A股换手率(daily_basic 1日缓存)/量比(5日均量)）
        if qt and qt.get("price"):
            pct = qt.get("pct")
            pc = ("buy" if (pct or 0) > 0 else "avoid"
                  if (pct or 0) < 0 else "")
            prev_c = None
            if pct is not None and qt["price"]:
                try:
                    prev_c = qt["price"] / (1 + pct / 100.0)
                except (TypeError, ZeroDivisionError):
                    prev_c = None
            # A股换手率：daily_basic 估值缓存（1日）
            tr_val = None
            if mkt_key == "cn":
                try:
                    import re as _re6
                    _m6q = _re6.search(r"(\d{6})", code or "")
                    if _m6q:
                        _tsc = (_m6q.group(1) + ".SH"
                                if _m6q.group(1)[0] == "6" else
                                _m6q.group(1) + ".SZ"
                                if _m6q.group(1)[0] in "03" else "")
                        if _tsc:
                            _val = self._stock_valuation(_tsc)
                            tr_val = (_val or {}).get("turnover_rate")
                except Exception:
                    tr_val = None
            # 量比：今日成交量 / 前5日均量（同源 df，单 defend）
            lb = None
            try:
                if (df is not None and len(df) > 6 and qt.get("vol")
                        and "volume" in df.columns):
                    v5 = df["volume"].iloc[-6:-1].mean()
                    if v5 and v5 > 0:
                        lb = qt["vol"] / v5
            except Exception:
                lb = None

            def _fv(x, nd=2):
                return ("%.*f" % (nd, x)) if isinstance(
                    x, (int, float)) and x == x else "—"
            _vol = qt.get("vol")
            _vol_s = (("%.0f万手" % (_vol / 1e6)) if _vol and _vol >= 1e8
                      else (("%.0f手" % (_vol / 100)) if _vol else "—"))
            _amt = qt.get("amount")
            _amt_s = (("%.2f亿" % (_amt / 1e8)) if _amt and _amt >= 1e8
                      else (("%.0f万" % (_amt / 1e4)) if _amt else "—"))

            def _ic(lb_, v, cls_=""):
                return ("<div style='display:inline-block;margin:4px 22px "
                        "6px 0'><div class='dim' style='font-size:11px'>%s"
                        "</div><b style='font-size:15px' class='%s'>%s</b>"
                        "</div>" % (lb_, cls_, v))
            body.append(
                "<h2>① 行情</h2><div class='card'>"
                "现价 <b style='font-size:22px'>%s</b>%s"
                "<div style='margin-top:4px'>%s%s%s%s%s%s%s%s</div>"
                "<div class='dim'>%s</div></div>"
                % (qt["price"],
                   (" · <b class='%s' style='font-size:16px'>%+.2f%%</b>"
                    % (pc, pct)) if pct is not None else "",
                   _ic("昨收", _fv(prev_c)), _ic("开盘", _fv(qt.get("open"))),
                   _ic("最高", _fv(qt.get("high"))),
                   _ic("最低", _fv(qt.get("low"))),
                   _ic("成交量", _vol_s), _ic("成交额", _amt_s),
                   _ic("换手率", _fv(tr_val) + ("%" if tr_val else ""),
                       "buy" if (tr_val or 0) >= 5 else ""),
                   _ic("量比", _fv(lb),
                       "buy" if (lb or 0) >= 2 else
                       "avoid" if (lb or 0) > 0 and lb <= 0.5 else ""),
                   _h.escape(str(qt.get("ts") or ""))))
        else:
            body.append("<h2>① 行情</h2><div class='card dim'>暂无实时"
                        "行情（加密或行情源无此代码）</div>")
        # ①+ K 线图（S1）：四市场日线 + MA5/20/60/250 + 成交量副图 +
        # A股缠论结构信号点 + JEV 新闻色点 + 筹码瀑布（S5，A股专属，
        # 右侧联动可见区最后一日）；周期切换 60/120/250（前端切片零
        # 重取）。异步 /api/kline 取数不阻塞首屏
        body.append(
            "<style>.kpb{background:#161b22;border:1px solid #30363d;"
            "border-radius:8px;color:#8b949e;font-size:12px;padding:4px 12px;"
            "cursor:pointer;font-family:inherit;margin-right:4px}"
            ".kpb.on{background:#0f141c;color:#e6edf3;font-weight:600;"
            "border-color:#58a6ff}</style>"
            "<h2>①+ K 线 <span class='dim' style='font-weight:400;"
            "font-size:12px'>（日/周/月线 · MA5/20/60/250 · MACD 副图"
            " · 成交量 · 筹码瀑布（A股·右侧） · 缠论结构信号点 + JEV"
            " 新闻色点 · K157 全A实证：s1卖后20/60日均价涨5.5%%/9.9%%"
            " 勿独立减仓）</span></h2>"
            "<details style='margin:-4px 0 6px'><summary class='dim' "
            "style='cursor:pointer;font-size:12px;user-select:none'>"
            "📐 图例与信号判据全量说明（点开）</summary><div class='dim'"
            " style='font-size:12px;line-height:1.8;margin:4px 0 0'>"
            "MA5/20/60/250 随级别重算 · MACD(12,26,9) 副图随级别重算，"
            "副图圆点=金叉(绿)/死叉(红) · 灰折线=笔（悬浮显向/幅）"
            " · 金粗线=线段（悬浮显向/幅/笔数） · 黄带=笔中枢 ZD-ZG"
            "（随级别：日/周/月/季/年线，色深=段数，悬浮显值）· 红倒三角"
            "=顶分型 · 绿三角=底分型（分型+笔联动：仅显示成笔分型=笔端点，"
            "白描边高亮，未成笔分型不再淡化显示）· 菱形标字=三类买卖点"
            "（简化判据：1=背驰〔幅衰减或MACD面积萎缩，趋=趋势背驰/盘="
            "盘整背驰〕 · 2=回抽不新极 · 3=沿外回踩；悬停信号点显「形态"
            "共振」=该信号日同现的经典K线形态（P2 15形态自研引擎，锤头/"
            "吞没/启明星等）；b1盘整背驰双窗微正+0.5%%、趋势背驰更弱）"
            "</div></details>"
            "<div class='card' style='padding:10px'>"
            "<div style='margin-bottom:8px'>"
            "<button class='kpb' data-n='60'>60</button>"
            "<button class='kpb' data-n='120'>120</button>"
            "<button class='kpb on' data-n='250'>250</button>"
            "<span class='dim' style='margin:0 4px'>根</span>"
            "<button class='kpb on' data-m='d'>日线</button>"
            "<button class='kpb' data-m='w'>周线</button>"
            "<button class='kpb' data-m='m'>月线</button>"
            "<button class='kpb' data-m='q'>季线</button>"
            "<button class='kpb' data-m='y'>年线</button>"
            "<span class='dim' style='margin-left:8px' id='kmsg'>"
            "K线加载中…</span></div>"
            "<div style='display:flex;gap:6px;align-items:stretch'>"
            "<div id='kx' style='height:432px;flex:1 1 auto;min-width:0'>"
            "</div><div id='kxc' style='height:432px;width:210px;"
            "flex:0 0 210px;display:none'></div></div></div>"
            "<script src='/static/echarts.min.js'></script><script>"
            "var _kc=%s,_kb=null,_ke=null,_kds=[],_kmk={},_knw={},"
            "_kpt={},"
            "_kcc=null,_kcm={},_kcd='',_kmode='d',_kwb=null,_kmb=null,"
            "_kqs=null,_kys=null,_kmsgbase='',"
            "_kn=250;"
            "function _wkkey(ds){var t=new Date(ds+'T00:00:00');"
            "var k=new Date(t);k.setDate(t.getDate()-((t.getDay()+6)%%7));"
            "return k.getFullYear()+'-'+('0'+(k.getMonth()+1)).slice(-2)"
            "+'-'+('0'+k.getDate()).slice(-2)}"
            "function _wk(){if(_kwb)return _kwb;var bs=_kb.bars,g={},ks=[],i;"
            "for(i=0;i<bs.length;i++){var r=bs[i],k=_wkkey(r.d);"
            "if(!g[k]){g[k]={d:r.d,w0:r.d,o:r.o,h:r.h,l:r.l,c:r.c,v:r.v};"
            "ks.push(k)}"
            "else{var w=g[k];w.d=r.d;w.c=r.c;w.v+=r.v;"
            "if(r.h>w.h)w.h=r.h;if(r.l<w.l)w.l=r.l}}"
            "ks.sort();"
            "var wb=ks.map(function(k){return g[k]});"
            "var cl=wb.map(function(r){return r.c});"
            "[5,20,60,250].forEach(function(K){var s=0;"
            "for(i=0;i<wb.length;i++){s+=cl[i];if(i>=K)s-=cl[i-K];"
            "wb[i]['m'+K]=i>=K-1?Math.round(s/K*1000)/1000:null}});"
            "_kwb={bars:wb,marks:_kb.marks||[],news:_kb.news||{},"
            "bis:_kb.wbis||[],pivots:_kb.wpivots||[],"
            "fx:_kb.wfx||[],sgs:_kb.wsgs||[],"
            "sigs:_kb.wsigs||[]};"
            "return _kwb}"
            "function _mokey(ds){return ds.slice(0,7)+'-01'}"
            "function _mo(){if(_kmb)return _kmb;var bs=_kb.bars,g={},"
            "ks=[],i;"
            "for(i=0;i<bs.length;i++){var r=bs[i],k=_mokey(r.d);"
            "if(!g[k]){g[k]={d:r.d,w0:r.d,o:r.o,h:r.h,l:r.l,c:r.c,v:r.v};"
            "ks.push(k)}"
            "else{var w=g[k];w.d=r.d;w.c=r.c;w.v+=r.v;"
            "if(r.h>w.h)w.h=r.h;if(r.l<w.l)w.l=r.l}}"
            "ks.sort();"
            "var mb=ks.map(function(k){return g[k]});"
            "var cl=mb.map(function(r){return r.c});"
            "[5,20,60,250].forEach(function(K){var s=0;"
            "for(i=0;i<mb.length;i++){s+=cl[i];if(i>=K)s-=cl[i-K];"
            "mb[i]['m'+K]=i>=K-1?Math.round(s/K*1000)/1000:null}});"
            "_kmb={bars:mb,marks:_kb.marks||[],news:_kb.news||{},"
            "bis:_kb.mbis||[],pivots:_kb.mpivots||[],"
            "fx:_kb.mfx||[],sgs:_kb.msgs||[],"
            "sigs:_kb.msigs||[]};"
            "return _kmb}"
            "function _kfmt(p){var r=_kds[p.dataIndex];if(!r)return '';"
            "return (_kmode!=='d'&&r.w0?(r.w0+' ~ '+r.d):r.d)+'<br>开 '+r.o+' 收 '+r.c+' 高 '+r.h+' 低 '+r.l"
            "+'<br>量 '+(r.v>=1e8?(r.v/1e8).toFixed(2)+'亿':"
            "(r.v/1e4).toFixed(0)+'万')+'<br>MA5 '+r.m5+' · MA20 '+r.m20"
            "+' · MA60 '+r.m60+' · MA250 '+(r.m250==null?'—':r.m250)"
            "+'<br>DIF '+(r.dif!=null?r.dif:'—')+' · DEA '"
            "+(r.dea!=null?r.dea:'—')+' · 柱 '+(r.mac!=null?r.mac:'—')}"
            "function _macd(bs){var e12=null,e26=null,dea=null,"
            "out=[],i;for(i=0;i<bs.length;i++){var c=bs[i].c;"
            "e12=e12==null?c:e12*11/13+c*2/13;"
            "e26=e26==null?c:e26*25/27+c*2/27;"
            "var dif=e12-e26;dea=dea==null?dif:dea*8/10+dif*2/10;"
            "out.push({dif:Math.round(dif*1000)/1000,"
            "dea:Math.round(dea*1000)/1000,"
            "mac:Math.round((dif-dea)*2*1000)/1000})}return out}"
            "function _krender(n){if(!_ke||!_kb)return;_kn=n;"
            "var src=_kmode==='w'?_wk():_kmode==='m'?_mo()"
            ":_kmode==='q'?_kqs:_kmode==='y'?_kys:_kb;"
            "var b=src.bars;"
            "var md=_macd(b);"
            "_kds=b.slice(Math.max(0,b.length-n));"
            "_kds.forEach(function(r,i){var m=md[md.length-_kds.length+i];"
            "if(m){r.dif=m.dif;r.dea=m.dea;r.mac=m.mac}});"
            "_kds.forEach(function(r,i){if(!i)return;var p=_kds[i-1];"
            "if(p.dif==null||p.dea==null)return;"
            "if(p.dif<=p.dea&&r.dif>r.dea)r.xg=1;"
            "else if(p.dif>=p.dea&&r.dif<r.dea)r.xd=1});"
            "var dk={},dw=null;_kds.forEach(function(r){dk[r.d]=r});"
            "if(_kmode!=='d'){var kf=_kmode==='w'?_wkkey:_mokey;"
            "dw={};b.forEach(function(r){dw[kf(r.d)]=r})}"
            "_kmk={};(src.marks||[]).forEach(function(m){"
            "var r=_kmode!=='d'?dw[(_kmode==='w'?_wkkey:_mokey)(m.d)]:dk[m.d];"
            "if(r)_kmk[r.d]=m});"
            "_knw={};var nw=src.news||{};"
            "Object.keys(nw).forEach(function(dd){"
            "var r=_kmode!=='d'?dw[(_kmode==='w'?_wkkey:_mokey)(dd)]:dk[dd];"
            "if(r)_knw[r.d]=nw[dd]});"
            "var up='#f85149',dn='#3fb950',mp=[],np=[];"
            "var bl=[],za=[];(src.bis||[]).forEach(function(x){"
            "if(dk[x.s]&&dk[x.e])bl.push([{coord:[x.s,x.sp],"
            "name:(x.d>=0?'↑ ':'↓ ')+(x.pp!=null?x.pp:'')+'%%'},"
            "{coord:[x.e,x.ep]}])});"
            "(src.pivots||[]).forEach(function(z){"
            "if(dk[z.s]||dk[z.e]){var s0=dk[z.s]?z.s:_kds[0].d,"
            "e0=dk[z.e]?z.e:_kds[_kds.length-1].d;"
            "var nn=z.n||0,aa=nn>=9?0.26:nn>=5?0.18:0.10,"
            "bb=nn>=9?0.85:nn>=5?0.65:0.45;"
            "za.push([{xAxis:s0,yAxis:z.zd,"
            "name:'ZD '+z.zd+' ~ ZG '+z.zg+(nn?' · '+nn+' 段':''),"
            "itemStyle:{color:'rgba(210,153,34,'+aa+')',"
            "borderColor:'rgba(210,153,34,'+bb+')',borderWidth:1}},"
            "{xAxis:e0,yAxis:z.zg}])}});"
            "var fxs=[];var bset={};(src.bis||[]).forEach("
            "function(x){bset[x.s]=1;bset[x.e]=1});"
            "(src.fx||[]).forEach(function(f){"
            "if(dk[f.d]&&bset[f.d])fxs.push({d:f.d,p:f.p,t:f.t,u:1})});"
            "var sbl=[];(src.sgs||[]).forEach(function(x){"
            "if(dk[x.s]&&dk[x.e])sbl.push([{coord:[x.s,x.sp],"
            "name:(x.d>=0?'↑ ':'↓ ')+(x.pp!=null?x.pp:'')"
            "+'%% · '+(x.nb||'?')+'笔'},{coord:[x.e,x.ep]}])});"
            "var sgn={b1:'买1',b2:'买2',b3:'买3',"
            "s1:'卖1',s2:'卖2',s3:'卖3'};"
            "var spts=[];(src.sigs||[]).forEach(function(g){"
            "if(dk[g.d])spts.push({d:g.d,p:g.p,"
            "n:(sgn[g.t]||g.t)+(g.sub==='tbe'?'·趋':"
            "g.sub==='pbe'?'·盘':''),"
            "b:g.t[0]==='b'?1:0})});"
            "_kds.forEach(function(r){var m=_kmk[r.d];"
            "if(m){var buy=(m.t||'').indexOf('买')>=0;"
            "mp.push({value:[r.d,buy?r.l*0.985:r.h*1.015],"
            "itemStyle:{color:buy?up:dn},label:{show:true,"
            "formatter:(m.t||'').slice(0,4),fontSize:9,"
            "position:buy?'bottom':'top'}})}"
            "var j=_knw[r.d];"
            "if(j){np.push({value:[r.d,j.b==='利多'?r.h*1.045:r.l*0.955],"
            "symbol:'diamond',symbolSize:9,"
            "itemStyle:{color:j.b==='利多'?'#d29922':'#58a6ff'}})}});"
            "_ke.setOption({animation:false,"
            "tooltip:{trigger:'axis',axisPointer:{type:'cross'},"
            "formatter:function(ps){var h='';ps.forEach(function(p){"
            "if(p.seriesType==='candlestick')h=_kfmt(p);"
            "else if(p.seriesName==='信号'){var m=_kmk[_kds[p.dataIndex].d]||{};"
            "h+='<br>结构 '+(m.t||'')+' conf '+(m.c!=null?m.c:'');"
            "var pd=_kds[p.dataIndex]?_kds[p.dataIndex].d:'';"
            "if(_kpt[pd])h+='<br><span style=\\'color:#d29922\\'>形态共振 "
            "'+_kpt[pd]+'</span>'}"
            "else if(p.seriesName==='JEV'){var j=_knw[_kds[p.dataIndex].d]||{};"
            "h+='<br>JEV '+(j.b||'')+' '+(j.c!=null?j.c+'%%':'')}});return h}},"
            "axisPointer:{link:{xAxisIndex:'all'}},"
            "grid:[{left:56,right:16,top:14,height:216},"
            "{left:56,right:16,top:246,height:44},"
            "{left:56,right:16,top:302,height:64}],"
            "xAxis:[{type:'category',data:_kds.map(function(r){return r.d}),"
            "boundaryGap:true,axisLine:{lineStyle:{color:'#30363d'}},"
            "axisLabel:{color:'#8b949e'}},"
            "{type:'category',gridIndex:1,data:_kds.map(function(r)"
            "{return r.d}),axisLabel:{show:false},axisTick:{show:false}},"
            "{type:'category',gridIndex:2,data:_kds.map(function(r)"
            "{return r.d}),axisLabel:{show:false},axisTick:{show:false}}],"
            "yAxis:[{scale:true,axisLabel:{color:'#8b949e'},"
            "splitLine:{lineStyle:{color:'#161b22'}}},"
            "{gridIndex:1,axisLabel:{show:false},splitLine:{show:false},"
            "axisLabel2:{show:false}},"
            "{gridIndex:2,axisLabel:{show:false},splitLine:{show:false}}],"
            "dataZoom:[{type:'inside',xAxisIndex:[0,1,2],start:0,end:100},"
            "{type:'slider',xAxisIndex:[0,1,2],bottom:2,height:16,"
            "borderColor:'#30363d',textStyle:{color:'#8b949e'}}],"
            "series:["
            "{name:'K',type:'candlestick',data:_kds.map(function(r)"
            "{return [r.o,r.c,r.l,r.h]}),itemStyle:{color:up,color0:dn,"
            "borderColor:up,borderColor0:dn},"
            "markArea:{itemStyle:{color:"
            "'rgba(210,153,34,0.10)',borderColor:"
            "'rgba(210,153,34,0.45)',borderWidth:1},"
            "label:{show:false},emphasis:{label:{show:true,"
            "color:'#e3b341',fontWeight:600}},data:za},"
            "markPoint:{symbol:'triangle',"
            "label:{show:false},silent:true,data:fxs.map("
            "function(f){return{coord:[f.d,f.p],"
            "symbolSize:7,"
            "symbolRotate:f.t>0?180:0,"
            "symbolOffset:f.t>0?[0,-5]:[0,5],"
            "itemStyle:{color:f.t>0?'#f85149':'#3fb950',"
            "borderColor:'#e6edf3',borderWidth:1,"
            "opacity:1}}})},"
            "markLine:{symbol:'none',"
            "lineStyle:{color:'#8b949e',width:1},"
            "label:{show:false},emphasis:{label:{show:true,"
            "color:'#e6edf3',fontWeight:600}},data:bl}},"
            "{name:'线段',type:'line',data:[],"
            "markLine:{symbol:'none',"
            "lineStyle:{color:'#e3b341',width:2},"
            "label:{show:false},emphasis:{label:{show:true,"
            "color:'#e3b341',fontWeight:600}},data:sbl}},"
            "{name:'三类点',type:'line',data:[],"
            "markPoint:{symbol:'diamond',symbolSize:9,"
            "silent:true,data:spts.map(function(g){"
            "var c=g.b?'#f85149':'#3fb950';"
            "return{coord:[g.d,g.p],name:g.n,"
            "label:{show:true,formatter:'{b}',"
            "position:g.b?'bottom':'top',"
            "color:c,fontSize:10,fontWeight:600},"
            "itemStyle:{color:c}}})}},"
            "{name:'MA5',type:'line',data:_kds.map(function(r){return r.m5}),"
            "showSymbol:false,lineStyle:{width:1,color:'#d29922'}},"
            "{name:'MA20',type:'line',data:_kds.map(function(r){return r.m20}),"
            "showSymbol:false,lineStyle:{width:1,color:'#58a6ff'}},"
            "{name:'MA60',type:'line',data:_kds.map(function(r){return r.m60}),"
            "showSymbol:false,lineStyle:{width:1,color:'#bc8cff'}},"
            "{name:'MA250',type:'line',data:_kds.map(function(r){return r.m250}),"
            "showSymbol:false,lineStyle:{width:1.2,color:'#ffa657'},"
            "connectNulls:true},"
            "{name:'信号',type:'scatter',data:mp,"
            "label:{color:'#e6edf3'},z:5},"
            "{name:'JEV',type:'scatter',data:np,z:5},"
            "{name:'量',type:'bar',xAxisIndex:1,yAxisIndex:1,"
            "data:_kds.map(function(r){return r.v}),"
            "itemStyle:{color:function(p){var r=_kds[p.dataIndex];"
            "return r.c>=r.o?up:dn}}},"
            "{name:'MACD',type:'bar',xAxisIndex:2,yAxisIndex:2,"
            "data:_kds.map(function(r){return r.mac}),"
            "itemStyle:{color:function(p){return p.value>=0?up:dn}},"
            "markPoint:{symbol:'circle',symbolSize:7,"
            "label:{show:false},silent:true,data:_kds.filter("
            "function(r){return r.xg||r.xd}).map(function(r){"
            "return{coord:[r.d,r.dif],"
            "itemStyle:{color:r.xg?dn:up}}})}},"
            "{name:'DIF',type:'line',xAxisIndex:2,yAxisIndex:2,"
            "showSymbol:false,lineStyle:{width:1,color:'#d29922'},"
            "data:_kds.map(function(r){return r.dif})},"
            "{name:'DEA',type:'line',xAxisIndex:2,yAxisIndex:2,"
            "showSymbol:false,lineStyle:{width:1,color:'#58a6ff'},"
            "data:_kds.map(function(r){return r.dea})}]},true);_kchips()}"
            "function _kday(){if(!_kds.length)return null;"
            "var o=(_ke.getOption().dataZoom||[])[0]||{};"
            "var en=(o.end==null?100:o.end);"
            "var i=Math.min(_kds.length-1,Math.max(0,"
            "Math.ceil(en/100*_kds.length)-1));return _kds[i]}"
            "function _kchips(){var r=_kday();if(!r)return;"
            "if(r.d===_kcd)return;_kcd=r.d;"
            "if(Object.prototype.hasOwnProperty.call(_kcm,r.d)){"
            "var c0=_kcm[r.d];if(c0)_kdraw(r,c0);return}"
            "_kcm[r.d]=null;"
            "fetch('/api/chips?c='+encodeURIComponent(_kc)+'&d='+r.d+'&x='+r.c)"
            ".then(function(t){return t.json()})"
            ".then(function(j){_kcm[r.d]=j;if(r.d===_kcd)_kdraw(r,j)})"
            ".catch(function(){_kcm[r.d]={ok:false}})}"
            "function _kfix(){setTimeout(function(){"
            "if(_ke)_ke.resize();if(_kcc)_kcc.resize()},30)}"
            "function _kdraw(r,j){var el=document.getElementById('kxc');"
            "if(!j||!j.ok||!j.rows||!j.rows.length){"
            "if(el.style.display!=='none'){el.style.display='none';"
            "_kfix()}return}"
            "el.style.display='block';_kfix();"
            "if(!_kcc)_kcc=echarts.init(el);"
            "var rows=j.rows,up='#f85149',bl='#58a6ff',mx=0,i,step=1;"
            "for(i=0;i<rows.length;i++)if(rows[i][1]>mx)mx=rows[i][1];"
            "for(i=1;i<rows.length;i++){step=rows[i][0]-rows[i-1][0];"
            "if(step>0)break}"
            "var xm=Math.max(1,Math.ceil(mx*1.15));"
            "_kcc.setOption({animation:false,"
            "title:{text:'筹码瀑布 '+((j.d||r.d)+'').slice(5)+' · 获利 '"
            "+(j.winner==null?'—':j.winner)+'%%'"
            "+(j.avg!=null?' · 均成本 '+j.avg:''),right:2,top:0,"
            "textStyle:{color:'#8b949e',fontSize:11,fontWeight:400}},"
            "grid:{left:46,right:8,top:22,bottom:10},"
            "xAxis:{type:'value',max:xm,axisLabel:{show:false},"
            "splitLine:{show:false},axisLine:{show:false},"
            "axisTick:{show:false}},"
            "yAxis:{type:'value',scale:true,"
            "axisLabel:{color:'#8b949e',fontSize:10},splitLine:{show:false}},"
            "series:["
            "{type:'custom',data:rows,z:2,renderItem:function(params,api){"
            "var price=api.value(0),pct=api.value(1);"
            "var p0=api.coord([0,price]),p1=api.coord([pct,price]);"
            "var h=Math.max(1.5,Math.min(api.size([0,step])[1]*0.92,14));"
            "return {type:'rect',shape:{x:p0[0],y:p1[1]-h/2,"
            "width:Math.max(0.5,p1[0]-p0[0]),height:h},"
            "style:{fill:price<=r.c?up:bl,opacity:.9}}}},"
            "{type:'line',data:[[0,r.c],[xm,r.c]],showSymbol:false,"
            "silent:true,z:3,lineStyle:{color:'#e6edf3',width:1,"
            "type:'dashed'}}]},true);_ksync()}"
            "function _ksync(){if(!_ke||!_kcc)return;var e=null;"
            "try{var s=_ke.getModel().getComponent('yAxis',0).axis.scale;"
            "e=s._extent||(s.getExtent?s.getExtent():null)}catch(ex){}"
            "if(e&&isFinite(e[0])&&isFinite(e[1])&&e[1]>e[0])"
            "_kcc.setOption({yAxis:{min:e[0],max:e[1]}})}"
            "function _kqyfetch(done){fetch('/api/kline_qy?c='+"
            "encodeURIComponent(_kc)).then(function(r){return r.json()})"
            ".then(function(j){if(!j.ok){document.getElementById('kmsg')"
            ".textContent=j.err||'季/年加载失败';return}"
            "_kqs=j.q;_kys=j.y;"
            "document.getElementById('kmsg').textContent="
            "'季/年就绪 · 深历史 '+(j.n_deep||0)+' 根日线';done()})"
            ".catch(function(){document.getElementById('kmsg')"
            ".textContent='季/年加载失败'})}"
            "function _kload(){"
            "fetch('/api/kline?c='+encodeURIComponent(_kc))"
            ".then(function(r){return r.json()}).then(function(j){"
            "var m=document.getElementById('kmsg');"
            "if(!j.ok){m.textContent=j.err||'无K线数据';return}"
            "_kb=j;var nn=Object.keys(j.news||{}).length;"
            "_kpt=j.ptmap||{};"
            "m.textContent=j.name+' · '+j.bars.length+'根'"
            "+((j.marks||[]).length?(' · 结构信号'+j.marks.length+'点'):'')"
            "+((j.bis||[]).length?(' · 笔'+j.bis.length):'')"
            "+((j.pivots||[]).length?(' · 中枢'+j.pivots.length):'')"
            "+((j.sgs||[]).length?(' · 段'+j.sgs.length):'')"
            "+((j.fx||[]).length?(' · 分型'+j.fx.length):'')"
            "+(nn?(' · JEV色点'+nn+'日'):'');"
            "_kmsgbase=m.textContent;"
            "_ke=echarts.init(document.getElementById('kx'));"
            "_ke.on('dataZoom',function(){_kchips();_ksync()});"
            "window.addEventListener('resize',function(){if(_ke)_ke.resize();"
            "if(_kcc)_kcc.resize()});"
            "_krender(250);"
            "document.querySelectorAll('.kpb').forEach(function(btn){"
            "btn.onclick=function(){"
            "if(btn.dataset.m){document.querySelectorAll('.kpb[data-m]')"
            ".forEach(function(x){x.classList.remove('on')});"
            "_kmode=btn.dataset.m;_kcd='';"
            "if((_kmode==='q'&&!_kqs)||(_kmode==='y'&&!_kys)){"
            "document.getElementById('kmsg').textContent='季/年加载中…';"
            "btn.classList.add('on');_kqyfetch(function(){_krender(_kn)});"
            "return}"
            "if(_kmode==='q'||_kmode==='y'){document.getElementById('kmsg')"
            ".textContent='季/年就绪 · '+((_kmode==='q'?_kqs:_kys)"
            ".bars.length)+' 根'}"
            "else{document.getElementById('kmsg').textContent=_kmsgbase}}"
            "else{document.querySelectorAll('.kpb[data-n]')"
            ".forEach(function(x){x.classList.remove('on')});"
            "_kn=parseInt(btn.dataset.n,10)}"
            "btn.classList.add('on');_krender(_kn)}})})"
            ".catch(function(ex){document.getElementById('kmsg').textContent="
            "'K线加载失败'})}"
            "_kload();</script>" % json.dumps(code, ensure_ascii=False))
        # ── K41 基本面（估值·盈利·成长·健康；K235 扩容为分组汇总，
        # (组,标签,值) 三元组 · 增速带前值对照；A/HK/US 三市场降级安全） ──
        frows, fnote = self._stock_fundamental(code)
        if frows:
            _gorder = []
            for g, _lb, _v in frows:
                if g not in _gorder:
                    _gorder.append(g)
            _pil = ("<div style='display:inline-block;margin:4px 24px 6px 0'>"
                    "<div class='dim' style='font-size:11px'>%s</div>"
                    "<b style='font-size:16px;font-family:Consolas,"
                    "'Microsoft YaHei',monospace'>%s</b></div>")
            body.append(
                "<h2>🏛 基本面 · 估值 / 盈利 / 成长 / 财务健康</h2>"
                "<div class='card'>"
                + "".join(
                    "<div style='margin:2px 0'><span class='dim' style="
                    "'display:inline-block;width:56px;font-size:12px;"
                    "vertical-align:top;padding-top:8px'>%s</span>%s</div>"
                    % (g, "".join(_pil % (lb, v)
                                  for gg, lb, v in frows if gg == g))
                    for g in _gorder)
                + ("<div class='dim' style='margin-top:6px'>%s · "
                   "数据源 tushare / TDX（财务 5 日缓存 · 估值 1 日缓存）"
                   "</div>" % e(fnote) if fnote else "")
                + "</div>")
        else:
            body.append("<h2>🏛 基本面</h2><div class='card dim'>%s</div>"
                        % e(fnote or "暂无基本面数据"))

        # ② 方法论矩阵（注册表驱动，点卡下钻；卡片构建抽为纯函数
        # _matrix_cards，供单元测试直接覆盖）
        mcards = _matrix_cards(cross_data, cross_hit, sig_states,
                               sig_err, items)
        body.append(
            "<h2>② 方法论矩阵 · 一只票 × 各方法论"
            "<span style='font-size:11px;color:#58a6ff;border:1px solid "
            "#58a6ff;border-radius:10px;padding:1px 8px;margin-left:8px;"
            "vertical-align:2px'>🌊 青萍之末产线</span>"
            " <span class='dim' "
            "style='font-weight:400'>（点卡下钻 · 策略卡随 cross_poll "
            "注册表自动扩展）</span></h2>"
            "<div class='mgrid'>%s</div>" % "".join(mcards))

        # ③ 共振分解（rps_latest 快照；rr/snap 已在数据收集阶段加载）
        # K235 扩容：四维丢分明细——每维得分/权重/对综合贡献/丢分缺失点
        if rr:
            cls = ("buy" if "买入" in rr["action"] else
                   "watch" if "观察" in rr["action"] else "avoid")
            _w = rr.get("w") or {}

            def _row(dim, cn, val, note):
                w = _w.get(dim)
                contrib = (val * w) if (w and val is not None) else 0
                st = ("<b>%.0f</b>" % val) if val is not None else \
                     "<span class='dim'>缺维</span>"
                return ("<tr><td>%s</td><td>%s</td><td class='dim'>%s</td>"
                        "<td>%s</td><td class='dim'>%s</td></tr>"
                        % (cn, st,
                           ("%.0f%%" % (w * 100)) if w else "—",
                           ("+%.1f 分" % contrib) if contrib else "—",
                           note))
            _nlong = sum(1 for v in sig_states.values() if v == "多头")
            _weak = "、".join(
                "%s%s" % (cn_, st_) for sn_, cn_ in SIG4_LIST
                for st_ in [sig_states.get(sn_)] if st_ and st_ != "多头") \
                or "4 策略全多头"
            _r_note = ("%s口径%s" % (rr.get("rps_label") or "",
                                     " · 前 15%%" if (rr.get("rps") or 0)
                                     >= 85 else ""))
            _e_note = ("市场闸门放行" if (rr.get("env") or 0) >= 50 and
                       rr.get("env") is not None else "市场闸门偏冷区间")
            _rows = "".join([
                _row("tech", "技术", rr["tech"],
                     "%d/4 策略多头（弱：%s）" % (_nlong, _weak)),
                _row("jev", "JEV消息面", rr.get("jev"),
                     ("近7天 %d 条已判断新闻" % (rr.get("jev_n") or 0))
                     if rr.get("jev") is not None
                     else "近7天无已判断新闻（25% 权重未计入·降权）"),
                _row("rps", "RPS动量", rr.get("rps"),
                     _r_note if rr.get("rps") is not None
                     else "无排名数据（缺维降权）"),
                _row("env", "环境闸门", rr.get("env"),
                     _e_note if rr.get("env") is not None
                     else "闸门数据缺失（缺维降权）")])
            body.append(
                "<h2>③ 共振分解（%s 更新 · 四维缺维降权）"
                "<span style='font-size:11px;color:#58a6ff;border:1px solid "
                "#58a6ff;border-radius:10px;padding:1px 8px;margin-left:8px;"
                "vertical-align:2px'>🌊 青萍之末产线</span>"
                "<span style='float:right;font-size:12px;font-weight:400'>"
                "<a href='/bt-qpzm'>→ 青萍之末工作台</a></span></h2><div "
                "class='card'>综合 <b class='%s' style='font-size:20px'>"
                "%d</b> · <b class='%s'>%s</b> · 维度 %s<table><tr><th>"
                "维度</th><th>得分</th><th>权重</th><th>贡献</th><th>丢分/"
                "缺失点</th></tr>%s</table></div>"
                % (snap.get("latest", ""), cls, rr["score"], cls,
                   rr["action"], rr["dims"], _rows))
        else:
            body.append("<h2>③ 共振分解"
                        "<span style='font-size:11px;color:#58a6ff;border:"
                        "1px solid #58a6ff;border-radius:10px;padding:1px "
                        "8px;margin-left:8px;vertical-align:2px'>🌊 青萍之末"
                        "产线</span></h2><div class='card dim'>不在"
                        "自选池（共振仅覆盖自选标的），全市场 RPS 见"
                        "对应市场页 · <a href='/bt-qpzm'>青萍之末 →</a>"
                        "</div>")

        # ④ 策略信号（数据来自上方 sig_states；点策略名进纵切页）
        body.append(
            "<h2 id='sig4'>④ 策略信号（4 策略当前持仓状态）"
            "<span style='font-size:11px;color:#58a6ff;border:1px solid "
            "#58a6ff;border-radius:10px;padding:1px 8px;margin-left:8px;"
            "vertical-align:2px'>🌊 青萍之末产线</span>"
            "<span style='float:right;font-size:12px;font-weight:400'>"
            "点策略名 → 纵切页</span></h2>")
        sig_rows = []
        for sn, cn in SIG4_LIST:
            st = sig_states.get(sn)
            if st == "多头":
                bd = "<span class='b-long'>多头</span>"
            elif st == "空头":
                bd = "<span class='b-short'>空头</span>"
            elif st == "空仓":
                bd = "<span class='b-flat'>空仓</span>"
            else:
                bd = "<span class='b-flat'>—</span>"
            sig_rows.append("<tr><td><a href='/bt/strategy/%s'>%s</a></td>"
                            "<td>%s</td></tr>" % (sn, e(cn), bd))
        body.append("<table>%s</table>" % (
            "".join(sig_rows) if sig_states else
            "<tr><td class='dim'>%s</td></tr>" % (sig_err or "—")))

        # ⑤ 全事件时间轴（S2）：JEV判断 + 公告/事件 + 缠论结构 +
        # 盘口异动 + 涨跌停 合并单条倒序时间线（近30天，每项带徽章/
        # 置信度/一句话依据）—— 替代原⑤散卡片与⑤+散卡片两块
        _es = _er.get("bias_stat") or {}
        _nev = round(_es.get("net") or 0, 2)
        _egc = ("buy" if _nev > 0.5 else "avoid" if _nev < -0.5 else "")
        _BCN = {"bull": "利多", "bear": "利空", "neutral": "中性",
                None: "未判"}
        from datetime import datetime as _dtn, timedelta as _tdn
        _cut = (_dtn.now() - _tdn(days=30)).strftime("%Y-%m-%d")

        def _dn(ds):
            # 图谱日期多为 YYYYMMDD 裸串：统一转 YYYY-MM-DD，保证
            # 30天窗口字符串比较正确与展示格式一致
            ds = ds or ""
            if len(ds) == 8 and ds.isdigit():
                return "%s-%s-%s" % (ds[:4], ds[4:6], ds[6:])
            return ds
        _tl = []
        for it in items:                    # JEV 判断新闻（近7天）
            _tl.append({"d": (it.get("ts") or "")[:10],
                        "c": ("g" if it["bias_cn"] == "利多" else
                              "r" if it["bias_cn"] == "利空" else "n"),
                        "b": "%s %d%%" % (it["bias_cn"], it["conf"]),
                        "t": it["title"],
                        "s": ((it["reason"] or "")[:66] or "—")
                        + " · JEV判断"})
        _n_rpt = 0
        for _ev in (_er.get("events") or []):   # 公告/事件（30天）
            _b = _ev.get("bias")
            _is_rpt = _ev.get("kind") == "report"
            if _is_rpt:
                _n_rpt += 1
            _tl.append({
                "d": (_ev.get("ts") or "")[:10],
                "c": ("g" if _b == "bull" else
                      "r" if _b == "bear" else "n"),
                "b": ("%s %d%%" % (_BCN.get(_b, "未判"),
                                   round((_ev.get("conf") or 0) * 100))
                      if _is_rpt else _BCN.get(_b, "未判")),
                "t": (_ev.get("text") or "")[:90],
                "s": (_ev.get("tag") or "—")
                + (" · 研报AI" if _is_rpt else " · 事件图谱")})
        if st_kg:
            for s in (st_kg.get("structures") or []):  # 缠论结构信号
                _ty = s.get("type") or ""
                _tl.append({"d": _dn(s.get("date")),
                            "c": ("g" if "买" in _ty else
                                  "r" if "卖" in _ty else "y"),
                            "b": _ty or "结构",
                            "t": "缠论%s · conf %.2f" % (
                                _ty, s.get("conf") or 0),
                            "s": "结构%.1f 佐证%.1f 资金%.1f%s · 图谱"
                            % (s.get("q_struct") or 0,
                               s.get("q_evid") or 0, s.get("q_hm") or 0,
                               (" · 实证×%d" % s["cc"])
                               if s.get("cc") else "")})
            for r in (st_kg.get("candles") or []):     # 盘口异动
                _ty = r.get("type") or ""
                _tl.append({"d": _dn(r.get("date")),
                            "c": ("g" if "seal" in _ty and _ty != "broken"
                                  else "r" if _ty in ("broken", "late_dump")
                                  else "y"),
                            "b": CANDLE_CN.get(_ty, _ty or "盘口"),
                            "t": "盘口%s · 收盘%+.2f%%" % (
                                CANDLE_CN.get(_ty, _ty),
                                (r.get("pct") or 0) * 100),
                            "s": (("触板%d分" % r["n_touch"])
                                  if r.get("n_touch")
                                  else ("尾盘量%d%%" % round(
                                      (r.get("tail_vol") or 0) * 100))
                                  if r.get("tail_vol") else "分时特征")
                            + " · 图谱"})
            for r in (st_kg.get("limits") or []):      # 涨跌停
                _lt = r.get("ltp") or "U"     # U涨停/D跌停/Z炸板（K159）
                _lab = ("涨停" if _lt == "U" else
                        "炸板" if _lt == "Z" else "跌停")
                _tl.append({"d": _dn(r.get("date")),
                            "c": "g" if _lt == "U" else
                            ("y" if _lt == "Z" else "b"),
                            "b": ("涨停%d板" % (r.get("lt") or 1))
                            if _lt == "U" else _lab,
                            "t": "%s · %+.2f%%" % (
                                _lab, (r.get("pct") or 0) * 100),
                            "s": ("封单%.0f万" % ((r.get("lamt") or 0) / 1e4)
                                  + ((" · 炸板%d次" % r["ot"])
                                     if r.get("ot") else ""))
                            + " · 图谱"})
        _tl = [x for x in _tl if x["d"] and x["d"] >= _cut]
        _tl.sort(key=lambda x: x["d"], reverse=True)
        _src = []
        if st_kg:
            _src.append("图谱结构/盘口/涨跌停")
        if _er.get("n"):
            _src.append("公告事件" + ("（含研报AI %d条）" % _n_rpt
                                     if _n_rpt else ""))
        _src.append("JEV判断")
        body.append(self._KG_CSS)
        body.append(
            "<h2 id='jev'>⑤ 全事件时间轴 <span class='dim' "
            "style='font-weight:400;font-size:12px'>（近30天 %d 条 · "
            "事件图谱多空净（时衰加权） <b class='%s'>%+.2f</b> · 来源：%s）"
            "</span></h2>"
            % (len(_tl), _egc, _nev, " / ".join(_src)))
        if _tl:
            body.append("<div class='tl'>%s</div>" % "".join(
                "<div class='ti'><span class='bdg %s'>%s</span>%s"
                "<div class='src'>%s · %s</div></div>"
                % (x["c"], e(x["b"]), e(x["t"][:100]), e(x["s"]), x["d"])
                for x in _tl[:80]))
        else:
            body.append("<div class='card dim'>近30天无事件（JEV判断 / "
                        "公告 / 结构信号 / 盘口 / 涨跌停）</div>")
        body.append("<div class='dim' style='margin-top:6px'>"
                    "<a href='/bt-jev'>全部 JEV 判断 →</a></div>")

        # （⑤+ 事件图谱与 ⑤ JEV 散卡片已合并为上方 ⑤ 全事件时间轴，
        #  数据收集提前至 st_kg/_er 区；此处不再单独渲染）

        # ── K41 公告与研报（tdx_info.db 直查 · 原文链接下钻；A 股） ──
        anns, rpts = self._stock_notices(code)
        if anns:
            body.append(
                "<h2>📰 公告与研报 <span class='dim' style='font-weight:"
                "400;font-size:12px'>（可下钻原文 · 公告近 8 条%s）"
                "</span></h2><div class='card'>"
                % (" · 研报近 %d 条" % len(rpts) if rpts else "")
                + "".join(
                    "<div style='margin:6px 0;font-size:13px'>"
                    "<span class='dim'>%s</span> <a href='%s' target="
                    "'_blank' style='color:#58a6ff'>%s ↗</a></div>"
                    % (e(str(a["date"] or "")[:10]), e(a["url"] or "#"),
                       e(a["title"] or "")) for a in anns)
                + ("<div class='dim' style='margin:10px 0 4px'>── 券商"
                   "研报 ──</div>"
                   + "".join(
                       "<div style='margin:6px 0;font-size:13px'>"
                       "<span class='dim'>%s · %s</span> <b>%s</b> "
                       "<a href='%s' target='_blank' style='color:#58a6ff'>"
                       "%s ↗</a></div>"
                       % (e(str(r["date"] or "")[:10]), e(r["org"] or ""),
                          e(r["rating"] or ""), e(r["url"] or "#"),
                          e(r["name"] or "")) for r in rpts)
                   if rpts else "")
                + "</div>")

        # ── K235 公告后验（JEV 佐证）+ 量能事件时间点（A股；均基于
        # 同源 df 130 日线，data 缺失时整块安全跳过） ──
        # ① 公告日 → 当日/3日/5日收盘涨跌：公告到底是利好还是利空，
        #   用后验数据说话；② 巨量(≥2×5日均量)/地量(≤0.5×)交易日明细
        _pv_rows, _ve_rows = [], []

        def _fs(x):
            # K-STOCK-029 修复：_fs 曾定义在下方 anns 条件块内，港股等
            # anns 为空而量能事件非空的路径在 genexpr 里 NameError 崩页
            return ("<span class='%s'>%+.1f%%</span>"
                    % ("up" if x > 0 else "dn", x)) \
                if x is not None else "<span class='dim'>—</span>"
        if (anns and df is not None and len(df) > 10
                and "close" in df.columns):
            try:
                _dts = [str(x)[:10] for x in df.index]
                _cl = df["close"].tolist()

                for a in anns:
                    d = str(a.get("date") or "")[:10].replace("-", "")
                    d = (d[:4] + "-" + d[4:6] + "-" + d[6:]) \
                        if len(d) == 8 else d
                    if len(d) != 10:
                        continue
                    i0 = next((i for i, x in enumerate(_dts) if x >= d),
                              None)      # 公告日≥d 首个交易日（顺延）
                    if i0 is None or i0 < 1 or not _cl[i0 - 1]:
                        continue
                    base = _cl[i0 - 1]

                    def _cg(i2):
                        return ((_cl[i2] / base - 1) * 100
                                if i2 < len(_cl) and _cl[i2] else None)
                    _pv_rows.append(
                        "<tr><td class='dim' style='padding:3px 8px 3px 0'>"
                        "%s</td><td style='padding:3px 8px 3px 0'>%s</td>"
                        "<td style='padding:3px 8px'>%s</td>"
                        "<td style='padding:3px 8px'>%s</td>"
                        "<td style='padding:3px 8px'>%s</td></tr>"
                        % (d, e((a.get("title") or "")[:28]),
                           _fs(_cg(i0)), _fs(_cg(i0 + 2)), _fs(_cg(i0 + 4))))
            except Exception:
                _pv_rows = []
        if df is not None and len(df) > 10 and "volume" in df.columns:
            try:
                import numpy as _np2
                _v = df["volume"]
                _v5 = _v.rolling(5).mean()
                _pc = df["close"].pct_change() * 100
                for i in range(len(df)):
                    r = _v5.iloc[i]
                    vi = _v.iloc[i]
                    if _np2.isnan(vi) or not r or r <= 0:
                        continue
                    mult = vi / r
                    tag = ("巨量" if mult >= 2 else
                           "地量" if mult <= 0.5 else None)
                    if tag:
                        _ve_rows.append((str(df.index[i])[:10], tag,
                                         mult, _pc.iloc[i]))
            except Exception:
                _ve_rows = []
        if _pv_rows or _ve_rows:
            _pv_html = _ve_html = ""
            if _pv_rows:
                _pv_html = (
                    "<div style='margin:6px 0 2px'><b>公告后验</b> "
                    "<span class='dim' style='font-size:11px'>（JEV 佐证"
                    " · 公告日 → 当日 / 3日 / 5日 收盘涨跌 · 红涨绿跌）"
                    "</span></div><table style='border-collapse:collapse;"
                    "font-size:13px'><tr class='dim'><td style='padding:"
                    "3px 8px 3px 0'>公告日</td><td style='padding:3px "
                    "8px 3px 0'>标题</td><td style='padding:3px 8px'>"
                    "当日</td><td style='padding:3px 8px'>3日</td><td "
                    "style='padding:3px 8px'>5日</td></tr>"
                    + "".join(_pv_rows) + "</table>")
            if _ve_rows:
                _ve_rows = _ve_rows[-8:][::-1]     # 最近 8 次，最新在前
                _ve_html = (
                    "<div style='margin:12px 0 2px'><b>量能事件时间点"
                    "</b> <span class='dim' style='font-size:11px'>"
                    "（巨量 ≥2× / 地量 ≤0.5× 5日均量 · 近 8 次 · 聚合"
                    "计数见下方图谱面）</span></div><table style='border-"
                    "collapse:collapse;font-size:13px'><tr class='dim'>"
                    "<td style='padding:3px 8px 3px 0'>日期</td><td "
                    "style='padding:3px 8px 3px 0'>类型</td><td style="
                    "'padding:3px 8px'>量/均量</td><td style='padding:"
                    "3px 8px'>当日涨跌</td></tr>"
                    + "".join(
                        "<tr><td class='dim' style='padding:3px 8px 3px 0'>"
                        "%s</td><td style='padding:3px 8px 3px 0'><span "
                        "class='bdg %s'>%s</span></td><td style='padding:"
                        "3px 8px'>%.1f×</td><td style='padding:3px 8px'>"
                        "%s</td></tr>"
                        % (d_, "y" if t_ == "巨量" else "n", t_, m_,
                           _fs(p_))
                        for d_, t_, m_, p_ in _ve_rows)
                    + "</table>")
            body.append(
                "<h2>🔎 后验与量能 <span class='dim' style='font-weight:"
                "400;font-size:12px'>（公告效验 · 量能拐点 · 同源 130 日"
                "线计算）</span></h2><div class='card'>"
                + _pv_html + _ve_html + "</div>")

        # ⑤++/S3 图谱关联面：游资痕迹 + 量能事件 + 筹码摘要 + 十大
        # 流通股东（A股图谱专属；美股/港股/加密显示明确不适用说明，
        # 不再静默空白）。/kg/stock 图谱视角与此页双向跳转
        if st_kg:
            from urllib.parse import quote as _qt3
            hmr = "".join(
                "<tr><td><a href='/kg/map?hm=%s'>%s</a></td><td>%d</td>"
                "<td class='%s'>%+.1f亿</td><td class='dim'>%s</td></tr>"
                % (_qt3(r["hm"]), e(r["hm"]), r["n"],
                   "up" if (r["net"] or 0) > 0 else "dn",
                   (r["net"] or 0) / 1e8, _dn(r["last"])[:10])
                for r in (st_kg.get("hotmoney") or [])[:12]) or \
                ("<tr><td colspan=4 class='dim'>窗口内无游资上榜痕迹"
                 "（龙虎榜仅上榜日披露）</td></tr>")
            volr = "".join(
                "<tr><td><span class='bdg y'>%s</span></td><td>%d 次</td>"
                "</tr>"
                % (e(v["type"] or ""), v["n"])
                for v in (st_kg.get("vol_events") or [])[:6]) or \
                "<tr><td colspan=2 class='dim'>无量能事件</td></tr>"
            cyq = st_kg.get("cyq") or {}
            _bh = st_kg.get("basic") or {}
            _cyq_html = ""
            if cyq.get("winner_rate") is not None:
                _wr = cyq["winner_rate"]
                _wr20 = cyq.get("wr_chg20")
                _cyq_html = (
                    "<tr><td>获利盘</td><td><b class='%s'>%.1f%%</b>%s"
                    "</td><td>成本跨度 %.2f · 加权成本 %.2f（%s）</td>"
                    "</tr>"
                    % ("up" if _wr >= 50 else "dn", _wr,
                       (" · 20日%+.1f" % _wr20) if _wr20 is not None
                       else "", cyq.get("cost_span") or 0,
                       cyq.get("weight_avg") or 0,
                       _dn(cyq.get("date"))))
            _hd = {}
            for r in (st_kg.get("holders") or []):
                if r.get("period") not in _hd:
                    _hd[r.get("period")] = []
                if len(_hd[r.get("period")]) < 10:
                    _hd[r.get("period")].append(r)
            _hp = max(_hd) if _hd else None
            hld = "".join(
                "<tr><td>%s</td><td>%s%%</td><td class='dim'>%s</td></tr>"
                % (e(r["name"]),
                   ("%.2f" % r["ratio"]) if r.get("ratio") is not None
                   else "—",
                   ("%.0f万股" % ((r.get("amt") or 0) / 1e4))
                   if r.get("amt") else "—")
                for r in (_hd.get(_hp) or [])) or \
                "<tr><td colspan=3 class='dim'>无股东数据</td></tr>"
            body.append(
                "<h2>⑤++ 图谱关联面 <span class='dim' style='font-weight:"
                "400;font-size:12px'>（近90天 · %s%s · <a href='/kg?c="
                "%s'>图谱视角 →</a> · <a href='/kg/company?c=%s'>🏢 公司"
                "本体全景 →</a>）</span></h2>"
                "<div class='duo'><div><h3>游资席位痕迹</h3><table "
                "class='sticky'><tr><th>席位</th><th>次数</th><th>净买"
                "</th><th>最近</th></tr>%s</table><h3 style='margin-top:"
                "14px'>量能事件</h3><table><tr><th>类型</th><th>频次"
                "</th></tr>%s</table></div><div><h3>筹码与股东</h3>"
                "<table>%s<tr><td>行业</td><td>%s</td><td>%s · 上市%s"
                "</td></tr></table><h3 style='margin-top:14px'>十大流通"
                "股东（%s）</h3><table><tr><th>股东</th><th>占比</th>"
                "<th>持股</th></tr>%s</table></div></div>"
                % (e((_bh.get("sw_l1") or _bh.get("industry") or "—")),
                   (" · " + e(_bh.get("company") or ""))
                   if _bh.get("company") else "",
                   _qt3(_kg_ts), _qt3(_kg_ts), hmr, volr, _cyq_html,
                   e(_bh.get("area") or "—"),
                   e(_bh.get("industry") or "—"),
                   e((_bh.get("list_date") or "")[:4] or "—"),
                   e(_dn(_hp) or "—"), hld))
        else:
            body.append(
                "<h2>⑤++ 图谱关联面</h2><div class='card dim'>"
                "图谱面（游资席位 / 缠论结构 / 盘口 / 股东 / 筹码）为 "
                "A 股专属——龙虎榜、公告、股东库仅覆盖 A 股；本标的为 "
                "%s，K 线与 JEV 新闻时间轴不受影响</div>" % mkt_label)

        # ⑤++ 深度研判（P-B2：事实包+本地LLM，按钮触发+当日缓存）
        body.append(
            "<h2 id='ra'>⑤++ 深度研判 <span class='dim' "
            "style='font-size:12px'>（事实包→本地LLM·≤220字·"
            "含操作建议白名单）</span></h2>"
            "<button onclick=\"_ra()\">🤖 深度研判（约10-90s）</button>"
            "<div id='raout' class='dim' style='margin-top:10px'></div>"
            "<script>var _rac=%s;"
            "function _raShow(j){document.getElementById('raout').innerHTML="
            "'<div class=card><pre style=\"white-space:pre-wrap;margin:0\">'"
            "+j.text+'</pre><div class=dim style=margin-top:6px>来源:"
            "'+j.source+(j.model?(' · '+j.model):'')+'</div></div>';}"
            "fetch('/api/research?c='+encodeURIComponent(_rac))"
            ".then(function(r){return r.json()}).then(function(j){"
            "if(j&&j.ok&&j.text&&!j.empty){_raShow(j)}});"
            "function _ra(){var o=document.getElementById('raout');"
            "o.innerHTML='研判生成中…（事实包→本地LLM，10-90s）';"
            "fetch('/api/research?c='+encodeURIComponent(_rac)"
            "+'&force=1').then(function(r){return r.json()})"
            ".then(function(j){if(!j.ok){o.innerHTML='失败：'"
            "+(j.err||'未知');return;}_raShow(j)});}"
            "</script>" % json.dumps(code, ensure_ascii=False))

        # ⑥ AI 深度分析（复用 /sa 任务：行情+新闻+JEV+Ollama 展望）
        body.append(
            "<h2 id='ai'>⑥ AI 深度分析</h2>"
            "<button onclick=\"_go()\">🤖 生成深度分析（约2-4分钟）"
            "</button><div id='saout' class='dim' style='margin-top:10px'"
            "></div><script>var _q=%s;"
            "function _go(){var o=document.getElementById('saout');"
            "o.innerHTML='分析中…（行情+关联新闻+JEV+展望）';"
            "fetch('/sa/start?q='+encodeURIComponent(_q)).then(function(r)"
            "{return r.json()}).then(function(j){if(j.error){o.innerHTML="
            "j.error;return;}_poll(j.job)});}"
            "function _poll(id){fetch('/sa/result?job='+id).then(function(r)"
            "{return r.json()}).then(function(j){if(j.status!=='done'){"
            "setTimeout(function(){_poll(id)},3000);return;}_render(j.d)});}"
            "function _render(d){if(!d||!d.ok){document.getElementById("
            "'saout').innerHTML='分析失败：'+(d&&d.msg||'未知');return;}"
            "var h='<div class=card><b>'+d.name+'</b> 综合倾向 <b>'+d.bias"
            "+'</b> · 信号 '+(d.strength>0?'+':'')+d.strength+' · 现价 '+d.px"
            "+'<br>'+d.stat+'<br><br>'+d.outlook+'</div>';"
            "if(d.judged){h+='<div class=dim style=margin-top:8px>JEV 依据:"
            "</div>';d.judged.forEach(function(x){h+='<div class=card>'+"
            "(x.title||'').slice(0,46)+' <b>['+x.bias_cn+']</b></div>'});}"
            "document.getElementById('saout').innerHTML=h;}"
            "</script>" % json.dumps(name, ensure_ascii=False))
        body.append("<div class='dim' style='margin-top:26px'>选股中心 · "
                    "纸面研究不构成投资建议</div>")
        self._html("".join(body))


def _bt_stock_form(self, msg=""):
        return ("<h1>🔍 个股全景</h1>"
                "<div class='dim' style='margin-bottom:10px'>%s</div>"
                "<input id='q' placeholder='名称或代码，如 英伟达 / TSLA / "
                "600519 / 00700' onkeydown=\"if(event.key==='Enter')_go()\">"
                "<button style='margin-left:8px' onclick='_go()'>查看"
                "</button><script>function _go(){var v=document."
                "getElementById('q').value.trim();if(v){location='/bt/"
                "stock?c='+encodeURIComponent(v)}}</script>" % msg)


def _bt_jev(self):
        """JEV 新闻判断图表页：news.db jev 列近 7 天。
        总览三卡 + 市场分组双向条 + 24h 净倾向时间轴 + 高置信要闻榜。"""
        import html as _html
        import sqlite3 as _sq
        from datetime import datetime as _dt, timedelta as _td
        from datetime import timezone as _tz
        cst = _tz(_td(hours=8))
        now = _dt.now(cst)
        rows = []
        try:
            db = _sq.connect(os.path.join(STATION, "news_collector", "news.db"))
            db.row_factory = _sq.Row
            rows = db.execute(
                "SELECT ts, title, jev FROM news "
                "WHERE jev IS NOT NULL AND jev!='' "
                "ORDER BY ts DESC LIMIT 3000").fetchall()
            db.close()
        except Exception:
            rows = []

        _BJ = {"bull": "利多", "bear": "利空", "neutral": "中性"}
        _SC = {"cn": "A股", "hk": "港股", "us": "美股",
               "commodity": "商品", "crypto": "加密", "macro": "宏观"}
        parsed = []
        for r in rows:
            try:
                j = json.loads(r["jev"])
                t = _dt.strptime(r["ts"], "%Y-%m-%d %H:%M").replace(tzinfo=cst)
            except Exception:
                continue
            parsed.append({
                "ts": t, "ts_s": (r["ts"] or "")[-5:], "title": r["title"] or "",
                "bias": j.get("bias", "?"), "bias_cn": _BJ.get(j.get("bias")),
                "conf": float(j.get("conf") or 0),
                "scope": j.get("scope") or "?",
                "scope_cn": _SC.get(j.get("scope")) or (j.get("scope") or "?")})

        def _cnt(items, b):
            return sum(1 for x in items if x["bias"] == b)

        _CLICK = "cursor:pointer;transition:border-color .12s;" \
                  "border:1px solid #30363d"  # 可下钻元素基础样式
        _SEL = "this.parentElement.querySelectorAll('[data-dj]')"
        _ON = ("onclick=\"for(var e of %s)e.style.borderColor="
               "'#30363d';this.style.borderColor='#58a6ff';dj(%s)\""
               % (_SEL, "%s"))

        day = [x for x in parsed if now - x["ts"] <= _td(hours=24)]
        week = parsed

        # 块1 总览三卡（近24h / 近7天，点击下钻该方向新闻）
        def _card(label, n, color, arg):
            return ("<div data-dj='1' role='button' tabindex='0' style='%s;flex:1;background:#161b22;"
                    "border-radius:8px;padding:14px;text-align:center'%s>"
                    "<div style='font-size:26px;font-weight:700;color:%s'>%d"
                    "</div><div style='font-size:12px;color:#8b949e'>%s ▾</div>"
                    "</div>" % (_CLICK, _ON % arg, color, n, label))
        blk1 = ("<div style='font-size:12px;color:#8b949e;margin:14px 0 6px'>"
                "近 24h（%d 条已判）</div><div style='display:flex;gap:10px'>"
                + _card("利多", _cnt(day, "bull"), "#f85149",
                        "{'bias':'bull','since':24}")
                + _card("利空", _cnt(day, "bear"), "#3fb950",
                        "{'bias':'bear','since':24}")
                + _card("中性", _cnt(day, "neutral"), "#8b949e",
                        "{'bias':'neutral','since':24}")
                + "</div><div style='font-size:12px;color:#8b949e;margin:12px 0"
                  " 6px'>近 7 天（%d 条已判）</div><div style='display:flex;"
                  "gap:10px'>"
                + _card("利多", _cnt(week, "bull"), "#f85149",
                        "{'bias':'bull'}")
                + _card("利空", _cnt(week, "bear"), "#3fb950",
                        "{'bias':'bear'}")
                + _card("中性", _cnt(week, "neutral"), "#8b949e",
                        "{'bias':'neutral'}")
                + "</div>")

        # 块2 市场分组双向条（近7天：左利空红、右利多绿；整行点击下钻）
        scopes = {}
        for x in week:
            scopes.setdefault(x["scope"], []).append(x)
        mx = max((max(_cnt(v, "bull"), _cnt(v, "bear"))
                  for v in scopes.values()), default=1) or 1
        bar_rows = []
        for sc in ("cn", "us", "hk", "commodity", "crypto", "macro"):
            v = scopes.get(sc) or []
            if not v:
                continue
            nb, nr = _cnt(v, "bull"), _cnt(v, "bear")
            arg = "{'scope':'%s'}" % sc
            bar_rows.append(
                "<div data-dj='1' role='button' tabindex='0' style='%s;display:flex;align-items:center;"
                "margin:4px 0;border-radius:6px;padding:2px 4px'%s>"
                '<div style="width:5em;color:#c9d1d9;font-size:12px">%s ▾</div>'
                '<div style="flex:1;display:flex;background:#161b22;'
                'border-radius:4px;overflow:hidden">'
                '<div style="width:50%%;display:flex;justify-content:flex-end">'
                '<div style="height:14px;width:%.1f%%;background:#3fb950"></div>'
                '</div><div style="width:50%%"><div style="height:14px;'
                'width:%.1f%%;background:#f85149"></div></div></div>'
                '<div style="width:7em;text-align:right;font-size:12px">'
                '<span style="color:#3fb950">%d空</span> '
                '<span style="color:#f85149">%d多</span></div></div>'
                % (_CLICK, _ON % arg, _SC.get(sc, sc), nr / mx * 100,
                   nb / mx * 100, nr, nb))
        blk2 = ("<div style='font-size:13px;color:#c9d1d9;margin:18px 0 6px'>"
                "▍按市场分组（近7天 · 左空右多 · 点击下钻）</div>" + "".join(bar_rows)
                if bar_rows else "")

        # 块3 24h 净倾向时间轴（2h 桶：利多-利空）
        buckets = [0] * 12
        for x in day:
            h = int((now - x["ts"]).total_seconds() // 7200)
            if 0 <= h < 12:
                buckets[11 - h] += 1 if x["bias"] == "bull" else (
                    -1 if x["bias"] == "bear" else 0)
        peak = max((abs(v) for v in buckets), default=1) or 1
        cols = []
        for i, v in enumerate(buckets):
            hpx = abs(v) / peak * 34
            col = "#f85149" if v > 0 else ("#3fb950" if v < 0 else "#30363d")
            cols.append(
                "<div data-dj='1' role='button' tabindex='0' style='%s;flex:1;display:flex;flex-direction:"
                "column;align-items:center;gap:2px;border-radius:4px;"
                "padding:2px 0'%s>"
                "<div style='height:34px;display:flex;align-items:flex-end'>"
                "<div style='width:14px;height:%dpx;background:%s;"
                "border-radius:2px'></div></div>"
                "<div style='font-size:10px;color:%s'>%s</div></div>"
                % (_CLICK, _ON % "{'bucket':%d}" % i, max(hpx, 2), col, col,
                   ("+%d" % v) if v > 0 else (str(v) if v else "-")))
        labels = ["%d时" % ((now - _td(hours=(11 - i) * 2)).hour)
                  for i in range(12)]
        blk3 = ("<div style='font-size:13px;color:#c9d1d9;margin:18px 0 6px'>"
                "▍净倾向时间轴（近24h · 2h 桶，多-空）</div>"
                "<div style='display:flex;gap:3px;align-items:flex-end'>"
                + "".join(
                    "<div style='flex:1;text-align:center'>%s</div>" % c
                    for c in cols)
                + "</div><div style='display:flex;gap:3px'>"
                + "".join(
                    "<div style='flex:1;text-align:center;font-size:9px;"
                    "color:#58606a;overflow:hidden'>%s</div>" % lb
                    for lb in labels)
                + "</div>") if day else ""

        # 块5 置信度分布直方图（近7天，10% 分桶，竖条内多/空/中堆叠，点击下钻）
        cbuckets = {}  # (lo%) -> {"bull":n,"bear":n,"neutral":n}
        for x in week:
            pc = int(x["conf"] * 100)
            lo = min(max((pc // 10) * 10, 40), 90)
            cbuckets.setdefault(lo, {"bull": 0, "bear": 0, "neutral": 0})
            cbuckets[lo][x["bias"] if x["bias"] in cbuckets[lo] else
                          "neutral"] += 1
        cpeak = max((sum(v.values()) for v in cbuckets.values()), default=1) or 1
        hist_cols = []
        for lo in range(40, 100, 10):
            v = cbuckets.get(lo) or {"bull": 0, "bear": 0, "neutral": 0}
            tot = sum(v.values())
            segs = ""
            for b, col in (("neutral", "#8b949e"), ("bear", "#3fb950"),
                           ("bull", "#f85149")):  # 堆叠：中在下、空、多在上
                if v[b]:
                    segs += ("<div style='width:100%%;height:%.0f%%;"
                             "background:%s'></div>" % (v[b] / cpeak * 100, col))
            hist_cols.append(
                "<div id='h%d' data-dj='1' role='button' tabindex='0' style='%s;flex:1;display:flex;flex-direction:"
                "column;align-items:center;gap:2px;border-radius:4px;"
                "padding:2px 0'%s>"
                "<div style='height:60px;width:70%%;display:flex;"
                "flex-direction:column-reverse;justify-content:flex-start;"
                "align-items:stretch;border-radius:2px'>%s</div>"
                "<div style='font-size:10px;color:%s'>%s</div>"
                "<div style='font-size:9px;color:#58606a'>%d-%d%%</div></div>"
                % (lo, _CLICK, _ON % "{'cmin':%d,'cmax':%d}" % (lo, lo + 10),
                   segs, "#c9d1d9" if tot else "#30363d",
                   str(tot) if tot else "-", lo, lo + 9))
        blk5 = ("<div style='font-size:13px;color:#c9d1d9;margin:18px 0 6px'>"
                "▍置信度分布直方图（近7天 · 竖条内自下而上：中性/利空/利多 · "
                "点击下钻）</div>"
                "<div style='display:flex;gap:3px;align-items:flex-end'>%s</div>"
                % "".join(hist_cols)) if week else ""

        # 块4 高置信要闻榜（近7天 conf≥0.7，利空在前）
        def _wt(items, bias):
            sel = sorted((x for x in items
                          if x["bias"] == bias and x["conf"] >= 0.7),
                         key=lambda x: -x["conf"])[:5]
            return [("<div style='margin:4px 0;font-size:12.5px'>"
                     "<span style='color:%s;font-weight:700'>%s %d%%</span>"
                     "<span style='color:#58606a'> · %s</span> %s</div>"
                     % ("#3fb950" if bias == "bear" else "#f85149",
                        "利空" if bias == "bear" else "利多",
                        round(x["conf"] * 100),
                        _SC.get(x["scope"], x["scope"]),
                        _html.escape(x["title"][:40]))) for x in sel]
        hot = _wt(week, "bear") + _wt(week, "bull")
        blk4 = ("<div style='font-size:13px;color:#c9d1d9;margin:18px 0 6px'>"
                "▍高置信要闻（近7天 · 置信度≥70%）</div>" + "".join(hot)
                if hot else "<div style='color:#58606a;font-size:12px;"
                            "margin:18px 0'>（暂无置信度≥70%的判断）</div>")

        # 块6 研报 AI 观点（report_events.jsonl，本地 Ollama 语义抽取，
        # 已并入事件图谱与个股时衰共振净分）
        blk6 = ""
        try:
            _rp = []
            with open(os.path.join(QBT_DIR, "data", "semantica",
                                   "report_events.jsonl"),
                      encoding="utf-8") as _fo:
                for _ln in _fo:
                    try:
                        _e = json.loads(_ln)
                    except Exception:
                        continue
                    if _e.get("kind") == "report" and _e.get("bias"):
                        _rp.append(_e)
            _cut30 = (now - _td(days=30)).strftime("%Y-%m-%d")
            _rp = [x for x in _rp if (x.get("ts") or "")[:10] >= _cut30]
            _rp.sort(key=lambda x: (x.get("ts") or ""), reverse=True)
            rows6 = []
            for x in _rp[:8]:
                _b = x.get("bias")
                _code = ((x.get("stocks") or [""])[0] or "")[:6]
                rows6.append(
                    "<div style='margin:4px 0;font-size:12.5px'>"
                    "<span style='color:%s;font-weight:700'>%s %d%%</span>"
                    "<span style='color:#58606a'> · %s · %s</span> "
                    "<a href='/kg/stock/%s' style='color:#58a6ff'>%s</a>"
                    "</div>"
                    % ("#3fb950" if _b == "bear" else "#f85149",
                       "利空" if _b == "bear" else "利多",
                       round((x.get("conf") or 0) * 100),
                       x.get("tag") or "研报",
                       (x.get("ts") or "")[:10],
                       _code, _html.escape((x.get("text") or "")[:44])))
            if rows6:
                blk6 = ("<div style='font-size:13px;color:#c9d1d9;"
                        "margin:18px 0 6px'>▍研报 AI 观点（近30天 %d 条 · "
                        "本地 Ollama 语义抽取 · 已并入事件图谱共振净分 · "
                        "点击进个股全景）</div>" % len(_rp)) + "".join(rows6)
        except Exception:
            blk6 = ""

        head = ("⚖️ JEV 新闻判断 · %s（累计已判 %d 条 · 24h %d 条）"
                % (now.strftime("%m-%d %H:%M"), len(week), len(day)))
        nav = pill_nav("jev")
        # 下钻数据 + 明细面板（点击图表元素展示背后的具体新闻依据）
        dj_data = json.dumps([{
            "t": x["ts_s"], "h": int((now - x["ts"]).total_seconds() // 7200),
            "title": x["title"][:64], "bias": x["bias"], "bias_cn": x["bias_cn"],
            "conf": round(x["conf"] * 100), "scope": x["scope"],
            "scope_cn": x["scope_cn"]} for x in week], ensure_ascii=False)
        dj_panel = ("<div id='dj-panel' style='display:none;background:#161b22;"
                    "border:1px solid #58a6ff;border-radius:8px;padding:12px;"
                    "margin:14px 0'></div>")
        dj_js = (
            "<script>var DJ=__DATA__;\n"
            "function dj(f){var p=document.getElementById('dj-panel'),rows=[];\n"
            "for(var x of DJ){\n"
            " if(f.bias&&x.bias!=f.bias)continue;\n"
            " if(f.scope&&x.scope!=f.scope)continue;\n"
            " if(f.cmin!=null&&(x.conf<f.cmin||x.conf>=f.cmax))continue;\n"
            " if(f.bucket!=null){if(11-x.h!=f.bucket)continue}\n"
            " else if(f.since){if(x.h>=f.since/2)continue}\n"
            " rows.push(x)}\n"
            "var head='（该筛选下暂无新闻）';\n"
            "if(rows.length){head='下钻依据 · '+rows.length+' 条';rows.reverse()}\n"
            "var html='<div style=\"color:#8b949e;font-size:11px;"
            "margin-bottom:4px\">'+head+'</div>';\n"
            "for(var x of rows){var c=x.bias=='bull'?'#f85149':"
            "(x.bias=='bear'?'#3fb950':'#8b949e');\n"
            " html+='<div style=\"margin:6px 0;font-size:13px\">"
            "<span style=\"color:'+c+';font-weight:700\">'+x.bias_cn+' '"
            "+x.conf+'%</span><span style=\"color:#58606a\"> · '+x.scope_cn"
            "+' · '+x.t+'</span> '+x.title+'</div>'}\n"
            "p.style.display='block';p.innerHTML=html;"
            "p.scrollIntoView({block:'nearest'})}\n"
            "</script>").replace("__DATA__", dj_data)
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>JEV判断</title></head>"
                "<body style='background:#0d1117;color:#c9d1d9;"
                "font-family:Microsoft YaHei,Arial;margin:0;padding:20px'>"
                "<div style='max-width:760px;margin:0 auto'>"
                "<h2 style='font-size:18px'>%s</h2>%s%s%s%s%s%s%s%s"
                "<div style='color:#58606a;font-size:11px;margin-top:16px'>"
                "数据：news.db jev 列 · 研报观点 report_events.jsonl"
                " · JEV(TypeSafe) 结构化判断随速报累积"
                " · 点击图表元素下钻查看依据 · 不构成投资建议"
                "</div></div>%s</body></html>"
                % (_html.escape(head), nav, blk1, blk2, dj_panel, blk3,
                   blk5, blk4, blk6, dj_js)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _bt_brief(self):
        """AI 晨报/复盘直达页：news.db ai_briefs / ai_reviews 最新各一条。"""
        import sqlite3 as _sq
        import html as _html
        blocks = []
        try:
            db = _sq.connect(os.path.join(STATION, "news_collector",
                                           "news.db"))
            for tag, tbl in (("🤖 AI 晨报", "ai_briefs"),
                             ("📊 收盘复盘", "ai_reviews")):
                try:
                    r = db.execute(
                        "SELECT ts, model, ok, brief FROM %s "  # nosec B608 - 内部白名单标识符/值已参数化（已复核）
                        "ORDER BY ts DESC LIMIT 1" % tbl).fetchone()
                except Exception:
                    r = None
                if r:
                    blocks.append(
                        "<h3 style='color:#8b949e;font-size:13px'>%s · %s ·"
                        " %s%s</h3><pre style='white-space:pre-wrap;"
                        "font-family:inherit;font-size:13px;color:#c9d1d9;"
                        "background:#161b22;padding:12px;border-radius:8px;"
                        "border:1px solid #21262d'>%s</pre>"
                        % (tag, _html.escape(r[0] or ""),
                           _html.escape(r[1] or "?"),
                           "" if r[2] else "（降级：模型未参与）",
                           _html.escape(r[3] or "")))
            db.close()
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "api_backtest.py", exc)
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>AI 晨报</title></head>"
                "<body style='background:#0d1117;color:#c9d1d9;"
                "font-family:Microsoft YaHei,Arial;margin:0;padding:20px'>"
                "<div style='max-width:760px;margin:0 auto'>"
                "<h2 style='font-size:18px'>🤖 AI 金融晨报 / 📊 收盘复盘</h2>%s%s"
                "<div style='color:#58606a;font-size:11px;margin-top:16px'>"
                "信息采集域 · 本地 Ollama 客观归纳 · 不构成投资建议"
                "</div></div></body></html>"
                % (pill_nav("brief"), "".join(blocks) or
                   "<div style='color:#8b949e'>暂无记录（每日 08:00 晨报 / "
                   "交易日 15:30 复盘后生成）</div>")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _bt_news(self, qs):
        """该股相关新闻页：?name=股名（近 7 天标题/正文命中，news.db）。"""
        import sqlite3 as _sq
        from urllib.parse import parse_qs as _pq, unquote as _uq
        import html as _html
        try:
            nm = _uq(_pq(qs).get("name", [""])[0]).strip()[:40]
        except Exception:
            nm = ""
        items = []
        err = ""
        if nm:
            try:
                since = time.strftime("%Y-%m-%d",
                                      time.localtime(time.time() - 7 * 86400))
                dbn = _sq.connect(os.path.join(
                    STATION, "news_collector", "news.db"))
                rows = dbn.execute(
                    "SELECT ts, title, substr(content,1,180) FROM news "
                    "WHERE (title LIKE ? OR content LIKE ?) AND ts >= ? "
                    "ORDER BY ts DESC LIMIT 40",
                    ("%" + nm + "%", "%" + nm + "%", since)).fetchall()
                dbn.close()
                items = rows
            except Exception as e:
                err = str(e)[:120]
        lis = "".join(
            '<div style="margin:10px 0;padding:10px 12px;'
            'background:#161b22;border:1px solid #21262d;border-radius:8px">'
            '<div style="color:#8b949e;font-size:11px">%s</div>'
            '<div style="color:#c9d1d9;font-size:14px;margin:4px 0">%s</div>'
            '<div style="color:#8b949e;font-size:12px">%s…</div></div>'
            % (_html.escape(r[0]), _html.escape(r[1]),
               _html.escape((r[2] or "").strip()))
            for r in items)
        head = ("📰 相关新闻 · %s（近 7 天 %d 条）" % (nm, len(items))) if nm \
            else "📰 相关新闻（缺少 ?name= 参数）"
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>相关新闻</title></head>"
                "<body style='background:#0d1117;color:#c9d1d9;"
                "font-family:Microsoft YaHei,Arial;margin:0;padding:20px'>"
                "<div style='max-width:760px;margin:0 auto'>"
                "<h2 style='font-size:18px'>%s</h2>%s%s"
                "<div style='color:#58606a;font-size:11px;margin-top:16px'>"
                "信息采集域 · news.db · 不构成投资建议</div></div></body>"
                "</html>" % (
                    _html.escape(head), lis,
                    ("<div style='color:#f85149'>%s</div>" % _html.escape(err))
                    if err else "")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
