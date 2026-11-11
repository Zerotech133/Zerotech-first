# -*- coding: utf-8 -*-
"""api_backtest 拆分 B7（2026-10-30 P75）：api_backtest_pages_a2.py。

自 api_backtest_pages 纯移动（函数体零改动，首参 self）。
"""

import json
import logging
import os
import sys
import time

from api_common import (
    QBT_DIR, ROOT, STATION, _read_json, pill_nav)

if os.path.join(STATION, "tools") not in sys.path:
    sys.path.insert(0, os.path.join(STATION, "tools"))
from jev_signal import event_type_tier, jev_signal_score


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
                "event_type": str(j.get("event_type") or ""),
                "tier": event_type_tier(j.get("event_type")),
                "signal": jev_signal_score(j),
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

        # 块5 事件类型分层分布（近7天：strong/weak/unknown 三桶，按 bias 堆叠，点击下钻）
        tbuckets = {t: {"bull": 0, "bear": 0, "neutral": 0}
                    for t in ("strong", "weak", "unknown")}
        for x in week:
            tk = x["tier"] if x["tier"] in tbuckets else "unknown"
            tbuckets[tk][x["bias"] if x["bias"] in tbuckets[tk] else
                           "neutral"] += 1
        tpeak = max((sum(v.values()) for v in tbuckets.values()), default=1) or 1
        _TIER_LABEL = {"strong": "强信号", "weak": "弱信号", "unknown": "未分层"}
        tier_cols = []
        for tk in ("strong", "weak", "unknown"):
            v = tbuckets[tk]
            tot = sum(v.values())
            segs = ""
            for b, col in (("neutral", "#8b949e"), ("bear", "#3fb950"),
                           ("bull", "#f85149")):  # 堆叠：中在下、空、多在上
                if v[b]:
                    segs += ("<div style='width:100%%;height:%.0f%%;"
                             "background:%s'></div>" % (v[b] / tpeak * 100, col))
            tier_cols.append(
                "<div data-dj='1' role='button' tabindex='0' style='%s;flex:1;display:flex;flex-direction:"
                "column;align-items:center;gap:2px;border-radius:4px;"
                "padding:2px 0'%s>"
                "<div style='height:60px;width:70%%;display:flex;"
                "flex-direction:column-reverse;justify-content:flex-start;"
                "align-items:stretch;border-radius:2px'>%s</div>"
                "<div style='font-size:10px;color:%s'>%s</div>"
                "<div style='font-size:9px;color:#58606a'>%d</div></div>"
                % (_CLICK, _ON % "{'tier':'%s'}" % tk,
                   segs, "#c9d1d9" if tot else "#30363d",
                   _TIER_LABEL[tk], tot))
        blk5 = ("<div style='font-size:13px;color:#c9d1d9;margin:18px 0 6px'>"
                "▍事件类型分层分布（近7天 · 竖条内自下而上：中性/利空/利多 · "
                "点击下钻）</div>"
                "<div style='display:flex;gap:3px;align-items:flex-end'>%s</div>"
                % "".join(tier_cols)) if week else ""

        # 块4 强信号要闻榜（近7天 · 事件类型分层：strong 类型优先，按 signal 排序，利空在前）
        def _wt(items, bias):
            sel = sorted((x for x in items
                          if x["bias"] == bias and x["tier"] == "strong"),
                         key=lambda x: -abs(x["signal"]))[:5]
            return [("<div style='margin:4px 0;font-size:12.5px'>"
                     "<span style='color:%s;font-weight:700'>%s "
                     "<span style='color:#58a6ff'>强信号</span></span>"
                     "<span style='color:#58606a'> · %s</span> %s</div>"
                     % ("#3fb950" if bias == "bear" else "#f85149",
                        "利空" if bias == "bear" else "利多",
                        _SC.get(x["scope"], x["scope"]),
                        _html.escape(x["title"][:40]))) for x in sel]
        hot = _wt(week, "bear") + _wt(week, "bull")
        blk4 = ("<div style='font-size:13px;color:#c9d1d9;margin:18px 0 6px'>"
                "▍强信号要闻（近7天 · 事件类型分层）" + ("".join(hot)
                if hot else "<div style='color:#58606a;font-size:12px;"
                            "margin:18px 0'>（近7天无强信号类型判断）</div>"))

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
                    "<span style='color:%s;font-weight:700' title='conf 已证伪（AUC 0.414 反相关：高 conf 反而更可能错）· 百分比仅原始留痕，不参与决策'>%s %d%%⚠</span>"
                    "<span style='color:#58606a'> · %s · %s</span> "
                    "<a href='/jev_target.html#%s' style='color:#58a6ff' title='JEV 标的下钻：三面依据一页出'>⇱</a> <a href='/kg/stock/%s' style='color:#58a6ff'>%s</a>"
                    "</div>"
                    % ("#3fb950" if _b == "bear" else "#f85149",
                       "利空" if _b == "bear" else "利多",
                       round((x.get("conf") or 0) * 100),
                       x.get("tag") or "研报",
                       (x.get("ts") or "")[:10],
                       _code, _code,
                       _html.escape((x.get("text") or "")[:44])))
            if rows6:
                blk6 = ("<div style='font-size:13px;color:#c9d1d9;"
                        "margin:18px 0 6px'>▍研报 AI 观点（近30天 %d 条 · "
                        "本地 Ollama 语义抽取 · 已并入事件图谱共振净分 · "
                        "conf 已证伪仅供参考 · "
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
            "conf": round(x["conf"] * 100), "tier": x["tier"], "scope": x["scope"],
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
            " if(f.tier&&x.tier!=f.tier)continue;\n"
            " if(f.bucket!=null){if(11-x.h!=f.bucket)continue}\n"
            " else if(f.since){if(x.h>=f.since/2)continue}\n"
            " rows.push(x)}\n"
            "var head='（该筛选下暂无新闻）';\n"
            "if(rows.length){head='下钻依据 · '+rows.length+' 条';rows.reverse()}\n"
            "var html='<div style=\"color:#8b949e;font-size:11px;"
            "margin-bottom:4px\">'+head+'</div>';\n"
            "for(var x of rows){var c=x.bias=='bull'?'#f85149':"
            "(x.bias=='bear'?'#3fb950':'#8b949e');\n"
            " var tag=x.tier=='strong'?'<span style=\"color:#58a6ff\"> 强信号</span>':'';\n"
            " html+='<div style=\"margin:6px 0;font-size:13px\">"
            "<span style=\"color:'+c+';font-weight:700\">'+x.bias_cn+tag"
            "+'</span><span style=\"color:#58606a\"> · '+x.scope_cn"
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
