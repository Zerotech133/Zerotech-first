# -*- coding: utf-8 -*-
"""api_backtest 拆分 B2（2026-10-30 P69）：回测页 HTML builder（fut / sellpoint）。

自 portal/api_backtest.py 纯移动；仅依赖 os 与 api_common 的 STATION/log_exc。
"""
import os

from api_common import STATION, log_exc

def fut_page_html():
    """K162-E F4 期货量化面板：核心池行情快照 + 策略×品种回测矩阵。

    直读 tools/out 最新 fut_scan_*.json（fut_scan.py F1+F2 快照）与
    fut_strat_*.json（fut_backtest.py F3 回测对照），服务端渲染；
    任一缺失给出生成指引。数据由计划任务 FutQuant（每日 09:30）刷新。"""
    import glob as _glob
    import json as _json

    def _latest(pat):
        c = sorted(_glob.glob(os.path.join(STATION, "tools", "out", pat)))
        if not c:
            return None, None
        try:
            with open(c[-1], encoding="utf-8") as f:
                return c[-1], _json.load(f)
        except Exception:
            log_exc("fut_page:_latest 快照解析 %s" % c[-1])  # C2批1
            return c[-1], None

    scan_p, scan = _latest("fut_scan_*.json")
    strat_p, strat = _latest("fut_strat_*.json")
    wfp, wfdata = _latest("fut_walkforward_*.json")
    cnp, cnd = _latest("cn_fut_backtest_*.json")
    if not scan and not strat:
        return ("<div class='sec'><h2>🌾 期货量化</h2><div class='dim'>"
                "快照未生成：计划任务 FutQuant 每日 09:30 自动拉起"
                "（或手跑 tools/fut_scan.py 与 tools/fut_backtest.py）"
                "。</div></div>")

    STRATS = ['rsi', 'bollinger', 'macd', 'ema_cross', 'supertrend',
              'donchian', 'rsi_pullback', 'keltner_breakout', 'triple_ema']
    parts = ["<h1>🌾 期货量化 · 核心宏观池</h1>"]

    # ① 行情快照
    if scan:
        pool = scan.get("core_pool") or []
        rows = ""
        for r in sorted(pool, key=lambda x: -(x.get("change_pct") or -999)):
            if r.get("error"):
                rows += ("<tr><td>%s</td><td colspan='6' class='dim'>%s"
                         "</td></tr>" % (r["symbol"], r["error"][:60]))
                continue
            pos = r.get("52w_pos")
            pos_h = ("—" if pos is None else
                     "<span style='color:%s'>%.0f%%</span>"
                     % ("#f85149" if pos >= 0.7 else
                        "#d29922" if pos >= 0.3 else "#3fb950", pos * 100))
            chg = r.get("change_pct")
            price = r.get("price")
            rows += ("<tr><td><b>%s</b></td><td>%s</td><td>%s</td>"
                     "<td style='color:%s'>%+.2f%%</td><td>%s</td><td>%s</td>"
                     "<td>%s</td></tr>"
                     % (r["symbol"], r["name_cn"],
                        "%.2f" % price if price else "—",
                        "#f85149" if (chg or 0) > 0 else "#3fb950", chg or 0,
                        "%.2f" % r["52w_high"] if r.get("52w_high") else "—",
                        "%.2f" % r["52w_low"] if r.get("52w_low") else "—",
                        pos_h))
        parts.append(
            "<div class='sec'><h2>① 核心池行情（14 品种）</h2>"
            "<table class='sticky'><tr><th>代码</th><th>品种</th>"
            "<th>最新价</th><th>涨跌</th><th>52周高</th><th>52周低</th>"
            "<th>52周位置</th></tr>%s</table>"
            "<div class='dim' style='margin-top:4px'>快照 %s · 52周位置："
            "≥70%% 高位（红）/ 30~70 中位 / ≤30%% 贴地（绿）· "
            "来源 yahoo_price 期货连续合约</div></div>"
            % (rows, (scan.get("ts") or "")[:16].replace("T", " ")))

    # ② 策略×品种矩阵
    if strat and strat.get("results"):
        results = strat["results"]
        mrows = ""
        n_beat = n_wf = n_ok = 0
        for r in results:
            if r.get("error"):
                mrows += ("<tr><td>%s %s</td><td colspan='11' class='dim'>%s"
                          "</td></tr>" % (r["symbol"], r["name_cn"],
                                          r["error"][:60]))
                continue
            n_ok += 1
            m = {x["strategy"]: x.get("total_return_pct")
                 for x in (r.get("ranking") or [])}
            bh = r.get("buy_and_hold_pct")
            top = m.get(r.get("winner") or "")
            if top is not None and bh is not None and top > bh:
                n_beat += 1
            wf = r.get("wf") or {}
            sc = wf.get("robustness_score")
            if sc is not None and sc >= 0.5:
                n_wf += 1
            cells = ""
            for s in STRATS:
                v = m.get(s)
                if v is None:
                    cells += "<td class='dim'>—</td>"
                else:
                    better = (bh is not None and v > bh)
                    cells += ("<td><span style='color:%s'>%+.1f</span></td>"
                              % ("#f85149" if better else "#3fb950", v))
            wf_h = ("—" if wf.get("error") or sc is None else
                    ("<span style='color:%s'>%.2f</span> <span class='dim'>"
                     "%s</span>" % ("#3fb950" if (sc or 0) >= 0.8 else
                                    "#d29922" if (sc or 0) >= 0.5 else
                                    "#f85149", sc,
                                    (wf.get("verdict") or "").split(
                                        "—")[0].strip())))
            mrows += ("<tr><td><b>%s</b></td><td>%s</td><td class='yl'>%s"
                      "</td>%s<td class='dim'>%s</td><td>%s</td></tr>"
                      % (r["symbol"], r["name_cn"], r.get("winner") or "—",
                         cells, ("%+.1f%%" % bh) if bh is not None else "—",
                         wf_h))
        parts.append(
            "<div class='sec'><h2>② 策略×品种回测矩阵（2y 日线，9 策略）"
            "</h2>"
            "<div style='overflow-x:auto'><table class='sticky'><tr><th>代码</th><th>品种</th><th>冠军"
            "</th>%s<th>B&amp;H</th><th>WF 稳健</th></tr>%s</table></div>"
            "<div class='dim' style='margin-top:4px'>数字=策略总收益%%，"
            "<span style='color:#f85149'>红</span>=跑赢买入持有 / "
            "<span style='color:#3fb950'>绿</span>=跑输；WF 稳健=样本外/"
            "样本内收益比（≥0.8 稳健绿 / ≥0.5 中等黄 / 低=过拟合红，"
            "0 交易折会虚高需交叉看笔数）；窗口内策略跑赢 %d/%d · "
            "WF≥0.5 %d/%d · 回测快照 %s · 生成工具 tools/fut_backtest.py"
            "</div></div>"
            % ("".join("<th>%s</th>" % s for s in STRATS), mrows,
               n_beat, n_ok, n_wf, n_ok,
               (strat.get("ts") or "")[:16].replace("T", " ")))
    # ③ 策略稳健性汇总榜（K171 F4 对接：fut_walkforward.py 产物）
    # 注意用 wfdata：② 块循环里 wf 已被 per-symbol 子对象遮蔽
    if wfdata and wfdata.get("strategy_robustness_rank"):
        wrows = ""
        n_pass = 0
        for x in wfdata["strategy_robustness_rank"]:
            rob = x.get("robust_avg")
            of = x.get("OF") or 0
            passed = rob is not None and rob >= 0.8 and of == 0
            if passed:
                n_pass += 1
            oos_n = x.get("oos_n") or 0
            oos_avg = x.get("oos_avg_test_pct")
            oos_h = ("—" if not oos_n else
                     "%+.2f%% (n=%d)" % (oos_avg, oos_n))
            rob_h = ("<span style='color:%s'><b>%.2f</b></span>"
                     % ("#3fb950" if (rob or 0) >= 0.8 else
                        "#d29922" if (rob or 0) >= 0.5 else
                        "#f85149", rob)) if rob is not None else "—"
            badge = (" <span style='color:#3fb950'>← 放行</span>"
                     if passed else "")
            wrows += ("<tr><td><b>%s</b>%s</td><td>%s</td><td>%d</td>"
                      "<td>%d</td><td>%d</td><td>%d</td>"
                      "<td class='dim'>%s</td></tr>"
                      % (x.get("strategy"), badge, rob_h,
                         x.get("R") or 0, x.get("M") or 0, x.get("W") or 0,
                         of, oos_h))
        parts.append(
            "<div class='sec'><h2>③ 策略稳健性汇总"
            "（walk-forward 3 折 70/30）</h2>"
            "<table class='sticky'><tr><th>策略</th><th>rob 均值</th>"
            "<th>R</th><th>M</th><th>W</th><th>OF</th>"
            "<th>OOS 有交易均值</th></tr>%s</table>"
            "<div class='dim' style='margin-top:4px'>robustness=样本外/"
            "样本内收益比（≥0.8 稳健绿 / ≥0.5 中等黄 / 低=过拟合红）；"
            "R/M/W/OF=13 品种各判定档计数；放行=%d 策略"
            "（rob≥0.8 且零过拟合）· 有效 %d/%d 次 · 快照 %s · "
            "生成工具 tools/fut_walkforward.py"
            "</div></div>"
            % (wrows, n_pass, wfdata.get("ok_runs") or 0,
               wfdata.get("runs") or 0,
               (wfdata.get("ts") or "")[:16].replace("T", " ")))
    elif scan or strat:
        _err = ""
        if wfp:
            try:
                with open(wfp, encoding="utf-8") as f:
                    _json.load(f)
            except Exception as e:
                _err = repr(e)  # 豁免（C2批1）：err 已回显页面 dim 文案，非静默
        parts.append(
            "<div class='sec'><h2>③ 策略稳健性汇总</h2><div class='dim'>"
            "fut_walkforward 快照未生成（wfp=%s err=%s）：手跑 "
            "tools/fut_walkforward.py（单段收益榜≠稳健性榜）</div></div>"
            % (wfp or "None", _err or "none"))
    # ④ 国内主力连续矩阵（K181 F4 对接：cn_fut_backtest.py 产物）
    if cnd and cnd.get("results"):
        bysym = {}
        order = []
        for r in cnd["results"]:
            s = r.get("symbol") or "?"
            if s not in bysym:
                bysym[s] = {"name": r.get("name_cn") or s, "cells": {}}
                order.append(s)
            bysym[s]["cells"][r.get("strategy")] = r
        VT = {"ROBUST": "#3fb950", "MODERATE": "#58a6ff",
              "WEAK": "#d29922", "OVERFIT": "#f85149"}

        def _cn_cell(r):
            if not r or r.get("skipped"):
                return "<td class='dim'>—</td>"
            v = r.get("verdict") or "—"
            col = VT.get(v, "inherit")
            return ("<td><span style='color:%s'>%s</span> "
                    "<span class='dim'>%.2f</span></td>"
                    % (col, v, r.get("robustness") or 0))

        crows = ""
        n_rob = 0
        for s in order:
            e = bysym[s]["cells"].get("ema_cross") or {}
            rr = bysym[s]["cells"].get("rsi") or {}
            crows += ("<tr><td><b>%s</b></td><td>%s</td>%s%s</tr>"
                      % (s, bysym[s]["name"], _cn_cell(e), _cn_cell(rr)))
        n_rob = sum(1 for r in cnd["results"] if r.get("verdict") == "ROBUST")
        parts.append(
            "<div class='sec'><h2>④ 国内主力连续矩阵"
            "（%d 品种 × 放行策略）</h2>"
            "<table class='sticky'><tr><th>代码</th><th>品种</th>"
            "<th>ema_cross</th><th>rsi</th></tr>%s</table>"
            "<div class='dim' style='margin-top:4px'>tushare 主力连续 2y"
            "（fut_mapping 换月拼接，未复权）· 3 折 walk-forward 同口径 · "
            "ROBUST %d/%d 项 · 费用口径 0.15%%/次 · 快照 %s · "
            "手动刷新：tools/cn_fut_backtest.py"
            "</div></div>"
            % (len(order), crows, n_rob, len(cnd["results"]),
               (cnd.get("ts") or "")[:16].replace("T", " ")))
    elif scan or strat:
        parts.append(
            "<div class='sec'><h2>④ 国内主力连续矩阵</h2><div class='dim'>"
            "cn_fut_backtest 快照未生成：手跑 tools/cn_fut_backtest.py"
            "（tushare 五所主力连续）</div></div>")
    parts.append(
        "<div class='dim' style='margin-top:10px'>刷新链：计划任务 "
        "FutQuant 每日 09:30 → tools/fut_scan.py（行情快照）+ "
        "tools/fut_backtest.py（回测对照）→ tools/out/fut_*.json · "
        "研究快照不构成投资建议</div>")
    return "".join(parts)


def sellpoint_page_html():
    """K208 SellPoint AI 卖点研判面板（影子信息层）。

    直读 quant_backtest/data/sellpoint_cards.jsonl（sellpoint_ai.py 产出：
    缠论标准卖点 1卖/2卖/3卖 + 本地 qwen3.5:9b 研判卡 强度/建议/风险），
    服务端渲染；空态给生成指引。数据由每日计划任务 SellPointAI 刷新。"""
    import glob as _glob
    import html as _html
    import json as _json

    def _esc(s):
        return _html.escape(str(s if s is not None else ""))

    cards = []
    cands = sorted(_glob.glob(os.path.join(
        STATION, "quant_backtest", "data", "sellpoint_cards.jsonl")))
    if cands:
        try:
            with open(cands[-1], encoding="utf-8") as f:
                for ln in f:
                    try:
                        cards.append(_json.loads(ln))
                    except Exception:
                        continue
        except Exception:
            cards = []
    if not cards:
        return ("<h1>🎯 SellPoint AI · 卖点研判</h1><div class='sec'>"
                "<div class='dim'>研判卡未生成：跑 quant_backtest/"
                "sellpoint_ai.py --once（或等每日计划任务 SellPointAI）"
                "。</div></div>")

    n = len(cards)
    n_tbe = sum(1 for c in cards if c.get("tbe") is True)
    n_held = sum(1 for c in cards if c.get("held"))
    n_ok = sum(1 for c in cards if c.get("parse_ok"))
    by_agent = {}
    for c in cards:
        an = c.get("agent_name") or "legacy_cons"
        by_agent[an] = by_agent.get(an, 0) + 1
    by_type = {}
    for c in cards:
        by_type[c.get("type") or "?"] = by_type.get(c.get("type") or "?", 0) + 1
    by_str = {}
    for c in cards:
        by_str[c.get("strength") or "?"] = by_str.get(
            c.get("strength") or "?", 0) + 1
    last_ts = (cards[-1].get("ts") or "")[:16].replace("T", " ")
    _SC = {"强": "#f85149", "中": "#d29922", "弱": "#3fb950"}
    _AC = {"卖出": "#f85149", "清仓": "#f85149", "离场": "#f85149",
           "减仓": "#d29922", "止盈": "#d29922", "回避": "#d29922",
           "观望": "#8b949e", "持有": "#3fb950"}
    parts = ["<h1>🎯 SellPoint AI · 卖点研判 <span class='dim'>(多Agent影子)</span></h1>",
             "<div class='sec'><div style='display:flex;gap:26px;flex-wrap:wrap;"
             "font-size:13px'>",
             "<span>研判卡 <b style='color:#e6edf3'>%d</b></span>" % n,
             "<span>1卖tbe <b style='color:#f85149'>%d</b></span>" % n_tbe,
             "<span>持仓命中 <b style='color:#d29922'>%d</b></span>" % n_held,
             "<span>解析成功 <b style='color:#3fb950'>%d</b></span>" % n_ok,
             "<span class='dim'>Agent %s</span>" % " ".join(
                 "%s %d" % (k, v) for k, v in sorted(by_agent.items())),
             "<span class='dim'>类型 %s ｜ 强度 %s</span>" % (
                 " ".join("%s %d" % (k, v) for k, v in sorted(by_type.items())),
                 " ".join("%s %d" % (k, v) for k, v in sorted(by_str.items()))),
             "<span class='dim'>最近生成 %s</span>" % _esc(last_ts),
             "</div></div>",
             "<div class='sec'><table style='border-collapse:collapse;"
             "width:100%;font-size:12.5px'>",
             "<tr style='color:#8b949e;text-align:left'>"
             "<th style='padding:6px 8px'>标的</th>"
             "<th style='padding:6px 8px'>Agent·卖点</th>"
             "<th style='padding:6px 8px'>信号日</th>"
             "<th style='padding:6px 8px'>强度</th>"
             "<th style='padding:6px 8px'>建议</th>"
             "<th style='padding:6px 8px'>理由</th>"
             "<th style='padding:6px 8px'>风险</th>"
             "<th style='padding:6px 8px'>时序</th>"
             "<th style='padding:6px 8px'>生成</th></tr>"]
    for c in reversed(cards[-60:]):
        st = c.get("strength") or "?"
        ac = c.get("action") or "—"
        held_txt = ("持有 %.0f日 %+.1f%%" % (
            c.get("paper_days") or 0, (c.get("paper_pnl") or 0) * 100)
            if c.get("held") else "<span class='dim'>—</span>")
        tbe_badge = ("<span style='color:#f85149;font-weight:600'>tbe</span>"
                     if c.get("tbe") is True else "")
        reason = (c.get("reason") or "").replace("。", "。 ")
        an = c.get("agent_name") or "legacy_cons"
        timing = c.get("timing")
        if timing:
            timing_txt = "%s%s" % (_esc(timing),
                                   ((" ・%s" % held_txt) if c.get("held") else ""))
        else:
            # 历史 legacy 卡无 timing：降级显示持仓浮盈
            timing_txt = held_txt
        parts.append(
            "<tr style='border-top:1px solid #21262d'>"
            "<td style='padding:7px 8px;white-space:nowrap'><b>%s</b> %s</td>"
            "<td style='padding:7px 8px;white-space:nowrap'><b>%s</b> %s %s</td>"
            "<td style='padding:7px 8px;white-space:nowrap' class='dim'>%s"
            "<span class='dim'>(检 %s)</span></td>"
            "<td style='padding:7px 8px;font-weight:600;color:%s'>%s</td>"
            "<td style='padding:7px 8px;font-weight:600;color:%s'>%s</td>"
            "<td style='padding:7px 8px;max-width:260px'>%s</td>"
            "<td style='padding:7px 8px;max-width:200px' class='dim'>%s</td>"
            "<td style='padding:7px 8px;white-space:nowrap' class='dim'>%s</td>"
            "<td style='padding:7px 8px;white-space:nowrap' class='dim'>%s</td>"
            "</tr>" % (
                _esc(c.get("code")), _esc(c.get("name")),
                _esc(an), _esc(c.get("type")), tbe_badge,
                _esc(c.get("dt")), _esc(c.get("detect_date")),
                _SC.get(st, "#8b949e"), _esc(st),
                _AC.get(ac, "#8b949e"), _esc(ac),
                _esc(reason[:110] + ("…" if len(reason) > 110 else "")),
                _esc((c.get("risk") or "")[:70]),
                timing_txt,
                _esc((c.get("ts") or "")[5:16].replace("T", " "))))
    parts.append("</table><div class='dim' style='margin-top:8px'>"
                 "口径：缠论标准卖点（1卖=趋势背驰 / 2卖=次高点 / 3卖=中枢破坏）。"
                 "多 Agent：每个 agent=独立投研机构（不同卖法/persona/动作偏置/择时），"
                 "各自研判同一批信号 → 锦标赛对照找最优，为模型训练攒数据。"
                 "研判=本地 qwen3.5:9b 影子信息层，只记不推不拦截，不构成交易依据。"
                 "范围：1卖 全量 + 2卖/3卖 持仓命中（config sellpoint."
                 "include_all_types 扩容）。</div></div>")
    return "".join(parts)


