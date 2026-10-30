# -*- coding: utf-8 -*-
"""门户页面渲染族 Mixin（A5 B1/B1b，2026-10-31 关羽）。

纯移动自 portal_api.py（do_GET 页面渲染方法），行为不变：
_linkage_page / _linkage_png / _nl_page / _html / _html_open / _html_more /
_html_file / _hub_market_tabs / _portal_page（B1b 解耦后移入）。

经 portal_api 基座多继承并入（class Handler(..., api_pages_portal.Handler, ...)）。
"""
import json
import logging
import os
import time

import org_command_api
from api_common import (QBT_DIR, REPORTS_DIR, ROOT, STATION, _BTSEL_TAG,
                        _read_json, _tabs_html, log_exc, pill_nav)
from api_misc import _SS_TAG

# K-STOCK-101：Hermes 30s TTL 探活缓存（B1b 从 portal_api 移入）
_HERMES_UP_CACHE = (0.0, False)


class Handler:  # Mixin 壳：经基座多继承并入

    def _html(self, body):
        # 055-L4 全站令牌前置：tokens.css 只含 :root 变量（零元素规则），
        # 对未用令牌的页面无副作用；已带链接的页不重复注入
        if "</head>" in body and "static/tokens.css" not in body:
            body = body.replace(
                "</head>",
                '<link rel="stylesheet" href="/static/tokens.css"></head>', 1)
        b = body.encode("utf-8")
        b += _BTSEL_TAG.encode("utf-8")   # K255 勾选回测 JS（幂等守卫 window.__k255bt）
        b += _SS_TAG.encode("utf-8")      # 055-L3 短屏化 JS（锚点条+折叠，幂等守卫 window.__SS__）
        b += self._token_tag().encode("utf-8")  # P0-1b 本机页面注入 token fetch 拦截
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _html_open(self, chunk):
        """U4-A 流式响应：先发响应头+首段并 flush（不设 Content-Length，
        HTTP/1.0 读至连接关闭，浏览器渐进渲染骨架），后续 wfile.write 续发。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(chunk.encode("utf-8"))
        self.wfile.flush()

    def _html_more(self, chunk):
        """U4-A 流式续发（客户端中断时静默收尾，避免线程栈噪音）。"""
        try:
            self.wfile.write(chunk.encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal_api.py", exc)

    def _html_file(self, path, nav_key=None):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                    "<title>暂无报告</title><style>body{background:#0d1117;"
                    "color:#8b949e;font-family:'Microsoft YaHei';display:flex;"
                    "align-items:center;justify-content:center;height:100vh}"
                    "</style></head><body>暂无回测报告（运行 runner.py 后生成）"
                    "</body></html>").encode("utf-8")
        # 静态页按需注入板块导航（nav_key 显式传入才注入；
        # 页面已自带导航则跳过。runner 等脚本重新生成文件后注入依然生效）
        if nav_key and b"<body" in body:
            try:
                t = body.decode("utf-8", "ignore")
                if "首页" not in t:
                    i = t.find("<body")
                    i = t.find(">", i) + 1
                    t = (t[:i] + "<div style='padding:12px 14px 0'>"
                         + pill_nav(nav_key) + "</div>" + t[i:])
                    body = t.encode("utf-8")
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "portal_api.py", exc)
        # 058 全站统一：文件页同样幂等前置 tokens.css（:root 变量零副作用；
        # 生成器重写文件后注入依然生效——与 _html 出口同构）
        try:
            t = body.decode("utf-8", "ignore")
            if "</head>" in t and "static/tokens.css" not in t:
                t = t.replace(
                    "</head>",
                    '<link rel="stylesheet" href="/static/tokens.css"></head>',
                    1)
                body = t.encode("utf-8")
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal_api.py", exc)
        # K-STOCK-182 包2.3 窄屏表格横滚：<760px 宽表格块级+容器内横滚，
        # 页面级零溢出（宪法「移动端可看不溢出」判据；生成器重写后依然生效）
        try:
            t = body.decode("utf-8", "ignore")
            if "</head>" in t and "static/mobile_table.css" not in t:
                t = t.replace(
                    "</head>",
                    '<link rel="stylesheet" href="/static/mobile_table.css"></head>',
                    1)
                body = t.encode("utf-8")
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal_api.py", exc)
        # K-STOCK-164 全站短屏化：报告外壳统一注入 shortscreen.js
        # （锚点条+自动 details 折叠；<4 个标题的短页其内部自动跳过；
        #   生成器重写文件后注入依然生效——与上方 tokens.css 同构）
        try:
            t = body.decode("utf-8", "ignore")
            if ("</head>" in t and "static/shortscreen.js" not in t
                    and b"<body" in body):
                t = t.replace(
                    "</head>",
                    '<script src="/static/shortscreen.js" defer></script></head>',
                    1)
                body = t.encode("utf-8")
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal_api.py", exc)
        # K-STOCK-170 包2.1 首屏结论条：报告页 3 秒定则落地（宪法 6.2）
        # （.env/.kpi 结论候选克隆到顶部+买卖计数徽标；无候选短页自动跳过）
        try:
            t = body.decode("utf-8", "ignore")
            if ("</head>" in t and "static/firstscreen.js" not in t
                    and b"<body" in body):
                t = t.replace(
                    "</head>",
                    '<script src="/static/firstscreen.js" defer></script></head>',
                    1)
                body = t.encode("utf-8")
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal_api.py", exc)
        body += _BTSEL_TAG.encode("utf-8")   # K255 静态报告页同样注入
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _hub_market_tabs(self):
        """K-STOCK-030 长页治理：hub 四市场模块段（4 张 .module 卡竖排
        + 跨市场RPS/策略库尾区）切成 Tab——点右侧市场切换不滚动。
        按市场标签锚点切分（加密卡与尾区在同一切分段，锚点法才切得开）。"""
        import re as _re_m
        try:
            hub = open(os.path.join(STATION, "quant_backtest", "reports",
                                    "bt_hub.html"), encoding="utf-8").read()
        except OSError:
            return ""
        p0 = hub.find("<h2>四市场模块")
        p1 = hub.find("</body>")
        if p0 < 0:
            return ""
        body = hub[p0:p1 if p1 > p0 else len(hub)]
        # 样式（去全局 body 样式防污染）
        s0, s1 = hub.find("<style"), hub.find("</style>")
        style = ""
        if 0 <= s0 < s1:
            style = _re_m.sub(r"body\{[^}]*\}", "", hub[s0:s1 + 8])
        anchors = [("🇨🇳 A股", "<b>🇨🇳 A股 模块</b>"),
                   ("🇺🇸 美股", "<b>🇺🇸 美股 模块</b>"),
                   ("🇭🇰 港股", "<b>🇭🇰 港股 模块</b>"),
                   ("🪙 加密", "<b>🪙 加密 模块</b>")]
        # 锚点定位；缺任一市场则整体降级为原竖排块
        pos = []
        for lb, a in anchors:
            i = body.find(a)
            if i < 0:
                return ("<div class='sec'><h2>🎯 选股中心 · 四市场模块"
                        "</span></h2>" + style + body + "</div>")
            pos.append(i)
        tail_i = body.find("跨市场 RPS 最强")
        tabs = []
        for k, (lb, _) in enumerate(anchors):
            end = pos[k + 1] if k + 1 < len(anchors) else (
                tail_i if tail_i > pos[k] else len(body))
            tabs.append((lb, body[pos[k]:end]))
        rest = body[tail_i:] if tail_i > pos[-1] else ""
        head = ("<div class='sec'><h2>🎯 选股中心 · 四市场模块"
                "<span class='dim' style='font-weight:400;font-size:12px'>"
                "（四法门共振 · 自量化中心迁入 · 点市场标签切换）"
                "</span></h2>" + style)
        return head + _tabs_html("hubmkt", tabs) + rest + "</div>"

    def _linkage_page(self, qs):
        """🔗 三线联动：个股 × TDX 880xxx 板块 × 大盘指数，三周期归一化叠加。

        数据与 quant_backtest/linkage.py 完全同源；PNG 缺失或 ?refresh=1
        时同步重算（tushare 分钟远程拉取，约 20-60s）；?tail=1 先补
        tdx_sector_min 分钟尾巴再重算（全局动作，每日一次足够）。"""
        from urllib.parse import parse_qs as _pq
        import re as _re
        import sys as _sys
        qp = _pq(qs)
        code = (qp.get("code") or ["301216.SZ"])[0].strip()[:12]
        if not _re.fullmatch(r"[0-9A-Za-z._/]+", code):
            code = "301216.SZ"
        block = (qp.get("block") or [""])[0].strip()[:8] or None
        block_name = (qp.get("block_name") or [""])[0].strip()[:12] or None
        index = (qp.get("index") or [""])[0].strip()[:12] or None
        refresh = (qp.get("refresh") or [""])[0] == "1"
        tail = (qp.get("tail") or [""])[0] == "1"
        if QBT_DIR not in _sys.path:
            _sys.path.insert(0, QBT_DIR)
        err, png_dir = "", ""
        try:
            import linkage_plot as _lp
            png_dir = _lp.OUT_DIR
            if tail:
                _lp.LK.refresh_min_tail()
                refresh = True
            if refresh or any(not os.path.exists(os.path.join(
                    png_dir, "%s_%s.png" % (code, p)))
                    for p in _lp.LK.PERIODS):
                _lp.gen_all(code, block=block, block_name=block_name,
                            index=index)
        except Exception as ex:
            err = str(ex)[:160]
        import html as _h
        e = _h.escape
        rows, base_q = [], {"code": code}
        if block:
            base_q["block"] = block
        if block_name:
            base_q["block_name"] = block_name
        if index:
            base_q["index"] = index
        from urllib.parse import urlencode as _ue
        bqe = e(_ue(base_q))
        pt = {"1d": "日线", "30m": "30分钟", "5m": "5分钟"}
        for p in ("1d", "30m", "5m"):
            png = os.path.join(png_dir, "%s_%s.png" % (code, p)) \
                if png_dir else ""
            sc = {}
            if png and os.path.exists(png):
                try:
                    with open(os.path.splitext(png)[0] + ".json",
                              encoding="utf-8") as f:
                        sc = json.load(f)
                except Exception:
                    sc = {}
                v = int(os.path.getmtime(png))
                lp = sc.get("last_pct", {})
                blk = (sc.get("meta", {}).get("block") or {})
                sigs = sc.get("signals") or []
                if sigs:
                    sig_html = "<div style='margin:4px 0 2px'>五信号：" + \
                        " ".join(
                            "<span style='background:#3d2e00;color:#e3a008;"
                            "padding:2px 8px;border-radius:4px;font-size:12px'>"
                            "⚑%s %s</span><span style='color:#8b949e;"
                            "font-size:11px'> %s</span>"
                            % (e(s.get("tag", "?")), e(s.get("name", "")),
                               e(s.get("note", ""))) for s in sigs) + "</div>"
                else:
                    sig_html = ("<div style='color:#586e75;font-size:12px;"
                                "margin:4px 0 2px'>五信号：无</div>")
                info = ("板块 %s %s（%s）｜基准 %s=0%% ｜ 截至: 个股 %s · "
                        "板块 %s · 大盘 %s ｜ 末值: 个股 %s%% · 板块 %s%% · "
                        "大盘 %s%% ｜ 现价 %s" % (
                            blk.get("name", "—"), blk.get("code", "—"),
                            blk.get("how", "—"),
                            sc.get("base_dt", "—"),
                            (sc.get("meta", {}).get("stock") or {}
                             ).get("last", "—"), blk.get("last", "—"),
                            (sc.get("meta", {}).get("index") or {}
                             ).get("last", "—"),
                            lp.get("stock"), lp.get("block"),
                            lp.get("index"), sc.get("stock_last_close")))
                rows.append(
                    "<h2 style='margin:18px 0 6px'>%s（%d 根）</h2>"
                    "<div style='color:#8b949e;font-size:12px;margin-bottom:"
                    "6px'>%s</div>%s<img src='/bt-linkage-png?code=%s&period=%s"
                    "&v=%d' style='max-width:100%%;border:1px solid #30363d;"
                    "border-radius:6px' alt='%s %s'>" % (
                        pt.get(p, p), sc.get("n", 0), e(info), sig_html,
                        e(code), p, v,
                        e(code), pt.get(p, p)))
            else:
                rows.append("<h2 style='margin:18px 0 6px'>%s</h2>"
                            "<div style='color:#f85149'>图未生成%s</div>" % (
                                pt.get(p, p),
                                "（生成失败：%s）" % e(err) if err else ""))
        err_banner = ("<div style='color:#f85149;background:#3d1e20;padding:"
                      "8px 12px;border-radius:6px;margin:8px 0'>生成异常：%s"
                      "</div>" % e(err)) if err else ""
        self._html(
            # 058 补标准页壳（此前裸片段→浏览器白底，与全站割裂）
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,"
            "initial-scale=1'><title>🔗 三线联动 · 零界</title>"
            + self._COCKPIT_CSS + "</head><body>"
            + pill_nav("linkage")
            + "<h1>🔗 三线联动复盘 <span style='font-size:14px;color:#8b949e'>"
            "大盘 × 板块(TDX 880xxx) × 个股 · 影子观察</span></h1>"
            "<form method='get' action='/bt-linkage' style='display:flex;gap:"
            "8px;flex-wrap:wrap;margin:10px 0;align-items:center'>"
            "<input name='code' value='%s' placeholder='代码 如 301216.SZ' "
            "style='width:130px'><input name='block' value='%s' placeholder="
            "'板块码 可选 如 881019'><input name='index' value='%s' "
            "placeholder='指数 默认 000001.SH' style='width:130px'>"
            "<button>查看/生成</button></form>"
            "<div style='font-size:13px;margin:6px 0'>"
            "<a href='/bt-linkage?%s&refresh=1'>♻ 重新生成</a> ｜ "
            "<a href='/bt-linkage?%s&tail=1'>⇩ 补板块分钟尾巴并重算</a>"
            "（生成约 20-60s，页面会等待）</div>%s%s"
            "<div style='color:#8b949e;font-size:12px;margin-top:16px;"
            "line-height:1.7'>口径：个股/大盘=chanlun_data.load_kline（CN "
            "tushare 分钟重采样）；板块=TDX 880xxx（tdx_sector_daily 日线 / "
            "tdx_sector_min 1 分钟重采样）；个股→板块=ts_stock_basic 行业名"
            "匹配（seg 优先 industry881&gt;industry_l1&gt;old_industry，可显"
            "式指定）；对齐=个股时间轴前向填充；归一化=公共首根 0%%。"
            "仅影子复盘，不构成买卖依据。</div></body></html>" % (
                e(code), e(block or ""), e(index or ""), bqe, bqe, err_banner,
                "".join(rows)))

    def _linkage_png(self, qp):
        """联动 PNG 白名单直出（code 白名单字符 + period 三选一）。"""
        import re as _re
        code = (qp.get("code") or [""])[0].strip()[:12]
        period = (qp.get("period") or [""])[0].strip()
        if not _re.fullmatch(r"[0-9A-Za-z._/]+", code) or \
                period not in ("1d", "30m", "5m"):
            self.send_error(404)
            return
        import sys as _sys
        if QBT_DIR not in _sys.path:
            _sys.path.insert(0, QBT_DIR)
        try:
            import linkage_plot as _lp
            fp = os.path.join(_lp.OUT_DIR, "%s_%s.png" % (code, period))
        except Exception:
            self.send_error(404)
            return
        if not os.path.exists(fp):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Cache-Control", "max-age=300")
        self.send_header("Content-Length", str(os.path.getsize(fp)))
        self.end_headers()
        with open(fp, "rb") as f:
            self.wfile.write(f.read())

    def _nl_page(self, qs):
        """P-E 自然语言查数页：输入问句 → multi_agent 多 agent 答案。
        页面加载只读缓存（read_only）；按钮 force=1 触发现算（10-30s）。
        specialist 调用链 <details> 下钻。
        K-STOCK-055b 回炉对齐全站扁平风格（老田反馈：与平台整体
        颜色/风格/布局不一致）：弃渐变按钮/英雄区/彩色徽章/spinner，
        回归 GitHub Dark 家族语言——h1 22px + .dim 说明行 + .sec/.card
        边框卡 + 实心绿主按钮(#238636) + 灰字来源行（与 /sa 家族一致）。"""
        import html as _he
        from urllib.parse import parse_qs as _pqs, unquote as _uq, quote as _uq2
        _qv = _uq((_pqs(qs).get("q") or [""])[0])
        _esc = _he.escape(_qv, quote=True)
        _NL_CSS = """<style>
.nl-search{display:flex;gap:10px;align-items:stretch;max-width:640px;margin-top:12px}
.nl-search input{flex:1;min-width:0;padding:12px 14px;font-size:14px;font-family:inherit;
color:var(--text,#e6edf3);background:var(--bg-raised,#161b22);
border:1px solid var(--border,#30363d);border-radius:10px;outline:none}
.nl-search input:focus{border-color:var(--link,#58a6ff)}
.nl-search input::placeholder{color:var(--text-dim,#8b949e)}
.nl-go{flex:none;padding:0 20px;font-size:14px;font-weight:600;font-family:inherit;
color:#fff;background:#238636;border:none;border-radius:10px;cursor:pointer}
.nl-go:hover{background:#2ea043}
.nl-chips{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px}
.nl-chips a{display:inline-block;padding:4px 12px;border:1px solid var(--border,#30363d);
border-radius:14px;font-size:12.5px;color:var(--text-dim,#8b949e);
background:var(--bg-raised,#161b22);text-decoration:none}
.nl-chips a:hover{color:var(--link,#58a6ff);border-color:var(--link,#58a6ff)}
.nl-ans pre{white-space:pre-wrap;word-break:break-word;margin:6px 0 0;
font-size:13.5px;line-height:1.75;font-family:inherit}
.nl-spec{margin-top:10px}
.nl-spec>summary{cursor:pointer;color:var(--link,#58a6ff);font-size:13px}
.nl-spec .sbody{margin-top:6px}
.nl-spec pre{white-space:pre-wrap;word-break:break-word;margin:0;
font-size:12.5px;line-height:1.7;font-family:inherit}
.nl-call{margin-top:6px;font-size:12px;color:var(--text-dim,#8b949e);
font-family:Consolas,monospace;line-height:1.6}
.nl-call b{color:var(--text,#e6edf3);font-weight:600}
</style>"""
        # K-STOCK-057 补全标准页壳（此前裸片段→浏览器白底，风格与全站割裂）
        _SHELL_HEAD = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                       "<meta name='viewport' content='width=device-width,"
                       "initial-scale=1'><title>💬 查数 · 青萍之末</title>"
                       + self._COCKPIT_CSS + "</head><body>")
        _SHELL_TAIL = "</body></html>"
        body = [_SHELL_HEAD, pill_nav("nl"), _NL_CSS,
                "<h1>💬 自然语言查数</h1>",
                "<div class='dim'>本地 qwen3.5:9b 多 agent（router → "
                "specialists → synth）· 信息层只读 · 当日缓存复用 · "
                "不外发数据</div>",
                "<div class='nl-search'>"
                "<input id='q' placeholder='如 688411最近怎么样 / 1卖趋势背驰的左尾 / "
                "预警池最新10条信号'"
                " onkeydown=\"if(event.key==='Enter')_go()\"%s>"
                "<button class='nl-go' onclick='_go()'>查数</button></div>"
                "<div class='nl-chips'>"
                "<span class='dim'>试一试：</span>"
                "<a href='/nl?q=%s'>688411 最近怎么样</a>"
                "<a href='/nl?q=%s'>1卖趋势背驰的左尾</a>"
                "<a href='/nl?q=%s'>预警池最新10条信号</a></div>"
                % (" value=\"" + _esc + "\"" if _qv else "",
                   _uq2("688411最近怎么样"),
                   _uq2("1卖趋势背驰的左尾"),
                   _uq2("预警池最新10条信号"))]
        # K-STOCK-055 修遗留 bug：_go 从未定义（旧版点「查数」为空操作）
        body.append("<script>function _go(){var v=document."
                    "getElementById('q').value.trim();if(v){location='/nl?q='"
                    "+encodeURIComponent(v);}}</script>")
        if not _qv:
            body.append("<div class='card dim' style='margin-top:20px'>"
                        "输入问句后回车，router 按 sub_q 分发给 specialist，"
                        "synth 汇总成一段答案；带缓存当日复用，强制重算约 10-30s。</div>")
            self._html("".join(body) + "</body></html>")
            return
        body.append(
            "<h2>当前问句</h2><div class='card'>%s</div>"
            "<div style='margin-top:12px'>"
            "<button onclick='_nlForce()'>🔄 强制重算（10-30s）</button></div>"
            "<div id='nlout' class='dim' style='margin-top:12px'></div>")
        body.append(
            "<script>var _nlq=" + json.dumps(_qv, ensure_ascii=False) + ";" + r"""
function _esc(s){return (s||'').replace(/</g,'&lt;')}
function _wait(t){document.getElementById('nlout').textContent=t}
function _nlShow(j){
 var o=document.getElementById('nlout');
 if(!j.ok){o.innerHTML='<div class="card" style="color:var(--color-err,#f85149)">失败：'
  +_esc(j.err||'未知')+'</div>';return;}
 if(j.empty){o.innerHTML='<div class="card dim">'+_esc(j.msg)+'</div>';return;}
 var src='来源:multi_agent'+(j.cached?' · 缓存':' · 现算 '+(j.elapsed_s||'?')+'s')
  +(j.degraded?' · ⚠降级拼接':'');
 var h='<div class="sec"><div class=dim>'+src+'</div>'
  +'<pre class=nl-ans>'+_esc(j.answer)+'</pre></div>';
 if(j.specialists&&j.specialists.length){
  h+='<h2 style="margin-top:18px">下钻 · '+j.specialists.length
   +' 条 specialist 调用链</h2>';
  j.specialists.forEach(function(s){
   h+='<details class=nl-spec><summary>【'+_esc(s.specialist)+'】'
    +'<span class=dim>'+_esc(s.sub_q)+'</span></summary>'
    +'<div class="card sbody"><pre>'+_esc(s.answer)+'</pre>';
   if(s.calls&&s.calls.length){
    s.calls.forEach(function(c){
     h+='<div class=nl-call>· <b>'+_esc(c.tool)+'</b> '+_esc(JSON.stringify(c.args))
      +'<br>→ '+_esc(c.res)+'</div>';});}
   h+='</div></details>';});}
 o.innerHTML=h;}
_wait('查数中…（router → specialists → synth，当日缓存命中则秒回）');
fetch('/api/nlquery?q='+encodeURIComponent(_nlq))
 .then(function(r){return r.json()}).then(_nlShow);
function _nlForce(){_wait('强制重算中…（绕过缓存，约 10-30s）');
 fetch('/api/nlquery?q='+encodeURIComponent(_nlq)+'&force=1')
 .then(function(r){return r.json()}).then(_nlShow);}
</script>""")
        body.append("<div class='dim' style='margin-top:26px'>"
                    "信息层查数 · 不改信号/推送/拦截口径 · 不构成投资建议</div>")
        self._html("".join(body) + "</body></html>")

    def _portal_page(self):
        """🏛 门户框架页：全站分区卡片总览——第一屏「我的入口」
        （Hermes 超级助手 + 田丰 · 参谋长），下接 AI 市场/选股中心/行情
        工作台/情报线/实盘跟踪/创作与运维，每卡带一句话说明。"""
        import html as _h
        import urllib.request as _u
        e = _h.escape

        # 轻量统计（缺省安全降级）
        rps = _read_json(os.path.join(REPORTS_DIR, "rps_latest.json")) or {}
        res = rps.get("res", [])
        qd = _read_json(os.path.join(ROOT, "qd_scan_cache.json")) or {}
        # Hermes 在线探活（1.5s 超时）+ 30s TTL 缓存（K-STOCK-101：
        # 原 /portal 每请求同步探活白等最多 1.5s，缓存后 30s 一探）
        global _HERMES_UP_CACHE
        now = time.time()
        if now - _HERMES_UP_CACHE[0] > 30:
            hermes_up = False
            try:
                _op = _u.build_opener(_u.ProxyHandler({}))
                _op.open("http://127.0.0.1:8787/", timeout=1.5)
                hermes_up = True
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "portal_api.py", exc)
            _HERMES_UP_CACHE = (now, hermes_up)
        else:
            hermes_up = _HERMES_UP_CACHE[1]

        # K-ORG-190 / 软件工作台 P1：参谋长卡片真状态灯（三态不假绿）。
        # 探针判例只 import 复用 org_command_api（不改其文件）：
        #   probe=agent_platform/data/shadow/chief_last_run.log（每日 17:35
        #   AgentChiefPush 落盘），mtime 距今 ≤26h 绿 / 超期黄 / 缺失灰。
        # 三态判定单源自 org_command_api._heartbeat_state；本函数只补
        # mtime 时间文本（green/stale 分支文件必存在，getmtime 安全）。
        _chief_hb = dict(org_command_api.CHIEF_DEFAULT_HEARTBEAT)
        try:
            _chief_state = org_command_api._heartbeat_state(
                {"heartbeat": _chief_hb}, STATION)
        except Exception:
            _chief_state = None  # 判定异常按灰态诚实降级，绝不假绿
        _chief_probe = os.path.join(STATION, _chief_hb.get("probe", ""))
        if _chief_state == "green":
            _chief_light = (
                "<span style='float:right;color:#3fb950;font-size:13px'>"
                "● 昨日值班完成（%s）</span>" % e(time.strftime(
                    "%H:%M", time.localtime(os.path.getmtime(_chief_probe)))))
        elif _chief_state == "stale":
            _chief_light = (
                "<span style='float:right;color:#d29922;font-size:13px'>"
                "● 值班日志过期（最近 %s）</span>" % e(time.strftime(
                    "%Y-%m-%d %H:%M",
                    time.localtime(os.path.getmtime(_chief_probe)))))
        else:
            # stale_after 非法/判定异常/文件缺失（no_ledger）→ 诚实灰
            _chief_light = (
                "<span style='float:right;color:#8b949e;font-size:13px'>"
                "○ 值班日志未建立（17:35 首跑后自动点亮）</span>")

        def _card(href, border, icon, name, desc, stat, ext=False):
            tgt = " target='_blank'" if ext else ""
            return ("<a href='%s'%s style='display:block;border:1px "
                    "solid %s;border-radius:12px;background:#161b22;"
                    "padding:15px 17px;text-decoration:none;"
                    "color:#e6edf3'>"
                    "<div style='font-size:16px;font-weight:700'>%s %s"
                    "<span style='float:right;color:%s;font-size:12px'>"
                    "进入 →</span></div>"
                    "<div class='dim' style='font-size:12.5px;"
                    "margin:6px 0 8px'>%s</div>"
                    "<div style='font-size:13px'><b>%s</b></div></a>"
                    % (href, tgt, border, icon, name, border, desc, stat))

        def _zone(title, cards):
            return ("<h2 style='margin:20px 0 10px;font-size:17px'>%s</h2>"
                    "<div style='display:grid;grid-template-columns:repeat("
                    "auto-fit,minmax(270px,1fr));gap:12px'>%s</div>"
                    % (title, cards))

        st = ("● 在线" if hermes_up else "● 离线",
              "#3fb950" if hermes_up else "#f85149")
        hero = (
            "<div style='display:grid;grid-template-columns:repeat("
            "auto-fit,minmax(320px,1fr));gap:14px;margin-top:14px'>"
            "<a href='/hermes/' target='_blank' style='display:"
            "block;border:2px solid #58a6ff;border-radius:14px;background:"
            "#161b22;padding:20px 22px;text-decoration:none;color:#e6edf3'>"
            "<div style='font-size:20px;font-weight:700'>🦸 Hermes 超级助手"
            "<span style='float:right;color:%s;font-size:13px'>%s</span>"
            "</div><div class='dim' style='font-size:13px;margin:8px 0'>"
            "<b style='color:#e6edf3'>我的主入口</b> · 对话即调度：通过它"
            "指挥 A0 与全部 AI 岗位 · 执行/记忆/插件网关</div>"
            "<div style='font-size:14px'><b>经门户直达 /hermes/</b>"
            " <span class='dim'>← 点卡片直接进入对话</span></div></a>"
            "<a href='/fin' style='display:block;border:2px solid #d29922;"
            "border-radius:14px;background:#161b22;padding:20px 22px;"
            "text-decoration:none;color:#e6edf3'>"
            "<div style='font-size:20px;font-weight:700'>🧠 田丰 · 参谋长"
            "%s</div><div class='dim' style='font-size:"
            "13px;margin:8px 0'>AI 组织架构顶点 · 影子日报点评 · 任务派单"
            " · 汇报直达你（human）</div><div style='font-size:14px'>"
            "<b>驾驶舱 /fin</b> · 汇报三通道：日报/P0 即时/P1 一小时</div>"
            "</a></div>"
            % (st[1], st[0], _chief_light))
        # K-STOCK-181: 原 genexpr 形态 `sum(1 for _a in ... if self._license_state(...))`
        # 线上稳定 TypeError(missing algo_id) 而直调同函数 OK——genexpr 闭包自变量
        # 解析玄学，改显式循环（LOAD_FAST 直取 self），一并 try 兜底单条坏数据
        _algo_rows = (_read_json(os.path.join(
            ROOT, "algo_market.json")) or {}).get("algos", [])
        _n_unlocked = 0
        for _a in _algo_rows:
            try:
                if self._license_state(_a.get("id", "")):
                    _n_unlocked += 1
            except TypeError as _te:
                log_exc("portal-page license-state TypeError on %r: %r" % (_a, _te))
        zones = "".join([
            _zone("🛒 AI 市场 · 算法商品 + 聘请 AI 做商业化", "".join([
                _card("/algo", "#3fb950", "🛒", "算法商品市场",
                      "六大核心算法商品化：量子双刀/RiskGuard/缠论九维/"
                      "JEV 情绪/TDX 回测/Score 排序 · 每款可试看真实"
                      "输出样例 · 卡密订阅本机解锁",
                      "%d 款商品 · 已解锁 %d" % (len(_algo_rows), _n_unlocked)),
                _card("/aim", "#d29922", "🤖", "AI 市场货架",
                      "11 个可聘岗位：A0 + 投研部五岗 + 专项 AI · 本地模型"
                      "零 token 成本", "在岗 11 · 按岗聘请"),
                _card("/org", "#8b949e", "🏛", "组织架构",
                      "田丰 · 参谋长 → 投研部 alpha 团队 → 汇报 human · "
                      "树形架构图 · 对话统一走超级助手", "org.json 实时"),
            ])),
            _zone("🎯 选股中心 · 三产线", "".join([
                _card("/bt-qpzm", "#58a6ff", "🌊", "青萍之末",
                      "四市场自选池四维共振 · 环境闸门一票否决 · "
                      "主页驾驶舱+五子页联动",
                      "全池 %d 只 · 买入 %d" % (
                          len(res),
                          sum(1 for r in res if "买入" in str(
                              r.get("action", ""))))),
                _card("/bt-chanlun", "#e3b341", "🧭", "缠论",
                      "多级别结构信号 · 三类买卖点 · 预警池盯市",
                      "每日盘后扫描"),
                _card("/qd/", "#3fb950", "⚔", "量子双刀",
                      "尾盘策略 14:40 扫描 → 14:55 执行 · 八重过滤+"
                      "Regime（研究态）",
                      "当日 %d · 近5日 %d" % (
                          len(qd.get("signals_now", [])),
                          len(qd.get("signals_recent", [])))),
                _card("/bt", "#8b949e", "🗂", "产线总目录",
                      "三产线汇总入口 · 策略中心",
                      "枢纽页"),
            ])),
            _zone("📈 行情工作台", "".join([
                _card("/screen", "#58a6ff", "📺", "数据大屏",
                      "全市场实时行情总览", "每日 09:30 起"),
                _card("/premarket", "#f85149", "🌅", "盘前快报",
                      "开盘前资讯与准备", "每日早间"),
                _card("/afterhours", "#d29922", "🌇", "盘后复盘",
                      "收盘后复盘与对照", "每日盘后"),
                _card("/sa", "#3fb950", "🔍", "个股 AI 分析",
                      "任一标的 AI 展望 · 四市场", "点入即用"),
                _card("/nl", "#8b949e", "💬", "查数",
                      "自然语言查行情数据", "点入即用"),
            ])),
            _zone("📡 情报线 · 图谱工具群", "".join([
                _card("/intel", "#58a6ff", "🏠", "情报工场",
                      "情报线总入口", "枢纽页"),
                _card("/kg", "#3fb950", "🕸", "图谱探索",
                      "游资/个股/板块知识图谱", "点入即用"),
                _card("/kg/map", "#d29922", "💰", "游资地图",
                      "游资席位踪迹与风格", "点入即用"),
                _card("/kg/ask", "#e3b341", "💬", "图谱问答",
                      "自然语言查图谱", "点入即用"),
                _card("/bt-brief", "#8b949e", "🤖", "AI 晨报",
                      "工作日 09:00 飞书推送", "订阅即享"),
            ])),
            _zone("🚦 实盘跟踪", "".join([
                _card("/bt-signals", "#f85149", "🚦", "明日信号",
                      "策略明日待执行信号", "每日刷新"),
                _card("/bt-positions", "#58a6ff", "💼", "持仓跟踪",
                      "纸面持仓盯市", "每日刷新"),
                _card("/paper", "#3fb950", "🧾", "模拟仓",
                      "模拟盘前端（K279 上线）", "实时"),
                _card("/bt-report", "#d29922", "📄", "回测报告",
                      "策略历史回测对照", "按次生成"),
                _card("/sellpoint", "#e3b341", "🎯", "卖点研判",
                      "缠论卖点 + AI 研判卡", "每日刷新"),
            ])),
            _zone("🎬 创作与 🎛 运维", "".join([
                _card("/creator", "#8b949e", "🎬", "创作专项",
                      "公众号草稿 / 短视频成片", "工作台"),
                _card("/ops", "#8b949e", "🎛", "OS 控制台",
                      "系统运维面板", "运维"),
                _card("/sys-health", "#3fb950", "🖥", "系统自检",
                      "服务探活与夜间快照", "实时"),
            ])),
        ])
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>🏛 总入口 · 门户</title>"
                + self._COCKPIT_CSS + "</head><body>"
                + pill_nav("portal")
                + "<h1>🏛 总入口 · 我的门户</h1><div class='dim'>"
                "哪里是什么 · 我的入口在哪里 · 一页说清全站</div>"
                + "<h2 style='margin:18px 0 0;font-size:17px'>🚪 我的入口"
                "</h2>" + hero + zones
                + "<div class='dim' style='margin-top:20px'>门户即框架："
                "新产线/新 AI 上架改对应注册表或本页卡片即可 · "
                "8080 旧首页（QuantDinger SPA）保留互不影响</div>"
                # K-ORG-190 数据口径小字（数据一条链纪律）：本页状态/清单
                # 均为注册表只读投影，不另立第二本账
                + "<div class='dim' style='margin-top:8px;font-size:11.5px'>"
                "数据口径：参谋长状态=chief_last_run.log 水位 · "
                "技能清单=skill_registry 同源投影（K-ORG-190）</div>"
                "</body></html>")
        self._html(body)
