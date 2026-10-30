# -*- coding: utf-8 -*-
"""api_backtest 拆分 B7（2026-10-30 P75）：api_backtest_pages_b2.py。

自 api_backtest_pages 纯移动（函数体零改动，首参 self）。
"""

import json
import logging
import os
import sys

from api_common import (
    MKT_LABEL, REPORTS_DIR, STATION, _env_market_key,
    _HERMES_FAB_HTML, _read_json, log_exc, pill_nav)

from api_backtest import _sa_hist_read, strategies_data

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
