# -*- coding: utf-8 -*-
"""门户页面渲染族 Mixin（A5 B1，2026-10-31 关羽）。

纯移动自 portal_api.py（do_GET 页面渲染方法），行为不变：
_linkage_page / _linkage_png / _nl_page / _html / _html_open / _html_more /
_html_file / _hub_market_tabs。

经 portal_api 基座多继承并入（class Handler(..., api_pages_portal.Handler, ...)）。
"""
import json
import logging
import os

from api_common import QBT_DIR, STATION, _BTSEL_TAG, _tabs_html, pill_nav
from api_misc import _SS_TAG


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

