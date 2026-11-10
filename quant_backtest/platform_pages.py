# -*- coding: utf-8 -*-
"""平台化：把 scattered 在 reports/ 里的页面整理成一个「有门面、能互跳、标明数据时点」的独立平台。

解决三个真问题
--------------
1. **26 个页面是孤立的** —— 除缠论三页互跳外，盘前、持仓、选股中心、回测报告
   彼此没有链接，也没有统一入口。用户只能靠记文件名。
2. **页面带着服务端依赖** —— 17 个页面引 `/static/nav.js`、`/bt/strategy/*`、
   `/bt-signals`、`/bt-report/img/*`。这些是门户（127.0.0.1:8091 那一套）的路由，
   静态托管下全是死链，点下去要么 404 要么白屏。
3. **数据时点不写在页面上** —— opt.html 生成于 10-03、数据是 09-26 的；
   rps.html 是 09-24 的。点进去看到的是一个多月前的结论，而页面本身不提醒。

因此本模块做四件事
------------------
  · CATALOG：24 个页面的单一事实源（域名、标题、数据日来源、发布名）
  · staleness()：用交易日历算「数据距今几个交易日」，>3 标橙、>15 标红归档
  · patch()：剥死链 + 注入统一导航条（死链按 11-10 裁定整体删除）
  · build_home()：生成平台门面，按能力域分区，每卡带数据日徽标

用法
----
  python platform_pages.py --list     # 看清单与陈旧度
  python platform_pages.py --home     # 只生成门面 reports/platform_home.html
  python platform_pages.py --patch    # 只修补内页到 h5_daily/
  python platform_pages.py --all      # 门面 + 修补（h5_pack 调这个）
"""

import argparse
import glob
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

REPORTS = os.path.join(HERE, "reports")
DEST = os.path.join(HERE, "h5_daily")

import cn_trading_days as CAL  # noqa: E402

TODAY = date.today()

# ---------------------------------------------------------------- 能力域

DOMAINS = [
    ("structure", "缠论分析", "九态判定：笔 / 段 / 中枢 / 背驰 → 三买、二买、一买；"
                             "另有全市场画像与单票解剖",
     "每天 18:30 全量重算"),
    ("sector", "板块", "465 个板块的同一套结构，与个股口径同源才能交叉验证",
     "每天 18:30"),
    ("pick", "选股", "盘前、九维评分、RPS 共振、四市场选股中心",
     "盘前 08:20 · 选股中心每日"),
    ("backtest", "回测", "策略纵切、多市场回测、参数寻优、背驰分层",
     "按需 / 每周"),
    ("paper", "模拟", "纸面账本、四账户虚拟资金、每日进出流水",
     "手动跑 portfolio.py（当前无调度）"),
    ("data", "数据", "同步健康、列对账、盘后复盘",
     "每天 06:10 / 收盘后"),
    ("archive", "归档", "一次性复盘与历史报告，不再更新",
     "冻结"),
]

# ---------------------------------------------------------------- 页面清单
#
# asof 三种取法：
#   ("json", 前缀)  读 reports/<前缀>_*.json 最新一份的 asof 字段（结构类，权威）
#   ("meta",)       从页面正文提「生成于 / 更新于 YYYY-MM-DD」
#   ("mtime",)      文件的修改日期（兜底）
#
# out 是发布目录里的文件名；None 表示沿用源文件名。

CATALOG = [
    # —— 缠论分析 ——
    dict(src="chanlun_tool.html", out="chanlun_tool.html", domain="structure",
         title="缠论分析工具", sub="全市场结构画像 + 单票判定链解剖（3961 只，可搜）",
         asof=("json", "pool_state"), primary=True),
    dict(src="pool_report_h5.html", out="pool_report_h5.html", domain="structure",
         title="结构日报 · 手机版", sub="五层判定 + 九态名单 + 逐票证据卡",
         asof=("json", "pool_state"), hero=True,
         hero_sub="五层门闸逐层看：每层放行多少只、名单是谁，每票带证据卡，"
                  "盘中叠实时价与收盘口径分开"),
    dict(src="pool_pc.html", out="pc.html", domain="structure",
         title="结构工作台 · PC", sub="三栏：选票 / 自绘 K 线与中枢 / 判据逐条",
         asof=("json", "pool_state"), hero=True,
         hero_sub="左栏选票，中间自绘 K 线与中枢，右栏判据逐条对错"),
    dict(src="pool_state.html", out="pool_state.html", domain="structure",
         title="结构池全景", sub="含中枢档 1005 只，全量九态明细",
         asof=("json", "pool_state")),

    # —— 板块 ——
    dict(src="sector_platform.html", out="sector_platform.html", domain="sector",
         title="板块操作平台", sub="465 板块位置 × 成分买点占比 × 结构优先排序",
         asof=("json", "sector_state"), primary=True, hero=True,
         hero_sub="先看板块自己的位置与强度，再看成分股里买点占比，"
                  "与个股层同源口径交叉验证"),

    # —— 选股 ——
    dict(src="pool_pc2.html", out="pool_pc2.html", domain="pick",
         title="选股工作台 v2", sub="三级下钻：档卡片 → 档内全列表 → 个股全部关系",
         asof=("json", "pool_state"), primary=True, hero=True,
         hero_sub="五档各多少只、沪深300 成分在池几只；点档看 top5 与全列表，"
                  "点个股看结构/行业/RPS/龙虎榜/信号史全部关系"),
    dict(src="premarket.html", out="premarket.html", domain="pick",
         title="盘前分析", sub="隔夜、缺口、行业资金、赚钱效应", asof=("meta",)),
    dict(src="bt_hub.html", out="bt_hub.html", domain="pick",
         title="选股中心", sub="四市场总览 + 策略货架 + RPS 共振", asof=("meta",)),
    dict(src="bt_m_cn.html", out="bt_m_cn.html", domain="pick",
         title="选股中心 · A 股", sub="", asof=("meta",)),
    dict(src="bt_m_us.html", out="bt_m_us.html", domain="pick",
         title="选股中心 · 美股", sub="", asof=("meta",)),
    dict(src="bt_m_hk.html", out="bt_m_hk.html", domain="pick",
         title="选股中心 · 港股", sub="", asof=("meta",)),
    dict(src="bt_m_crypto.html", out="bt_m_crypto.html", domain="pick",
         title="选股中心 · 加密", sub="", asof=("meta",)),
    dict(src="signals.html", out="signals.html", domain="pick",
         title="全池策略信号", sub="四策略在池内的当前信号与持仓标记", asof=("meta",)),
    dict(src="rps.html", out="rps.html", domain="pick",
         title="RPS 强度与共振", sub="相对价格强度排名", asof=("meta",)),

    # —— 回测 ——
    dict(src="report.html", out="report.html", domain="backtest",
         title="每日扫描 Top5 回测", sub="逐笔撮合明细 + 资金曲线", asof=("meta",)),
    dict(src="bt_strat_trend_follow.html", out="bt_strat_trend_follow.html",
         domain="backtest", title="策略纵切 · 趋势跟随", sub="", asof=("meta",)),
    dict(src="bt_strat_ma_cross.html", out="bt_strat_ma_cross.html",
         domain="backtest", title="策略纵切 · 均线交叉", sub="", asof=("meta",)),
    dict(src="bt_strat_rsi_reversal.html", out="bt_strat_rsi_reversal.html",
         domain="backtest", title="策略纵切 · RSI 反转", sub="", asof=("meta",)),
    dict(src="bt_strat_boll_breakout.html", out="bt_strat_boll_breakout.html",
         domain="backtest", title="策略纵切 · 布林突破", sub="", asof=("meta",)),
    dict(src="opt.html", out="opt.html", domain="backtest",
         title="全池参数寻优榜单", sub="夏普网格 + walk-forward 纪律门", asof=("meta",)),
    dict(src="chanlun_tbe_report.html", out="chanlun_tbe_report.html",
         domain="backtest", title="背驰分层影子报表", sub="趋势背驰 vs 盘整背驰",
         asof=("meta",)),
    dict(src="bt10m_sweep.html", out="bt10m_sweep.html", domain="backtest",
         title="降换手实验 · 卖出规则", sub="25 组变体：失效线 / 持有期 / 追高门槛 → 换手 6.9 倍、成本 2.1%",
         asof=("meta",), primary=True),
    dict(src="bt10m.html", out="bt10m.html", domain="backtest",
         title="1000 万 · 一年回测（旧口径）", sub="结论已被降换手实验推翻，留作失败样本",
         asof=("meta",)),

    # —— 模拟 ——
    dict(src="sim_accounts.html", out="sim_accounts.html", domain="paper",
         title="模拟交易 · 四本账对账", sub="四账户净值 / 持仓 / 平仓 / 跳过，含体检",
         asof=("jsonfile", "sim_accounts.json"), primary=True),
    dict(src="positions.html", out="positions.html", domain="paper",
         title="纸面持仓跟踪", sub="待买 / 持仓 / 已平 + 逐日浮盈轨迹", asof=("meta",)),

    # —— 数据 ——
    dict(src="afterhours.html", out="afterhours.html", domain="data",
         title="盘后复盘", sub="", asof=("meta",)),
    dict(src="col_audit_summary_LATEST.html", out="col_audit_summary.html",
         domain="data", title="数据源列对账", sub="每日 06:10 自动生成",
         asof=("meta",), latest_glob="col_audit_summary_*.html"),

    # —— 归档 ——
    dict(src="pool_watch.html", out="pool_watch.html", domain="archive",
         title="双池触发监控台", sub="许褚营 · 已停更", asof=("meta",)),
    dict(src="portal_stock_incident_20261005.html", out="incident_20261005.html",
         domain="archive", title="事故复盘 · 个股页连接重置", sub="K157 文案 %/ 未转义",
         asof=("meta",)),
]

# 历史清单留档（判例 UI-1 排查记录）。2026-11-10 起判定口径改为：
# 静态平台只服务相对文件 —— 任何以 / 开头的绝对路径链接/图片必然 404
# （旧清单式判断漏了裸 '/bt'、'/' 等短路径，判例 UI-5），统一按 _is_dead_href。
DEAD_ROUTES = ("/static/", "/bt/", "/bt-report/", "/bt-signals", "/api/",
               "/portal", "/intel", "/org", "/ops-board", "/kg/", "/pool-watch",
               "/screen", "/premarket", "/afterhours", "/fin", "/quant", "/nl",
               "/bridge", "/hermes", "/aim", "/algo", "/strategies", "/fut",
               "/strong", "/paper", "/market", "/council", "/sim", "/debate",
               "/sellpoint", "/qd", "/picks.html")


# ---------------------------------------------------------------- 数据时点

def _load_json_asof(prefix):
    """读 reports/<prefix>_*.json 里最新一份的数据基准日。

    字段名两个源不一致，必须都试（同族坑：pct/lpct、pool_state/sector_state）：
        pool_state_*.json    → data_asof = "20261106"   （run_at 是生成时刻，不是数据日）
        sector_state_*.json  → asof      = "20261106"
    只看文件名会把它当成当天的，因为盘后重算会把旧数据日的文件写到今天。
    """
    files = sorted(glob.glob(os.path.join(REPORTS, prefix + "_2*.json")))
    if not files:
        return None
    try:
        with open(files[-1], encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    return _asof_of_obj(d)


META_RE = re.compile(r"(?:生成于|更新于|账本更新于|数据日|基准)\s*[:：]?\s*"
                     r"(20\d\d)-(\d\d)-(\d\d)")


def _meta_asof(txt):
    m = META_RE.search(txt)
    if m:
        return "-".join(m.groups())
    return None


def _asof_of_obj(d):
    """从 json 对象里按优先级取数据日。字段名各源不一致，必须都试。"""
    for key in ("asof", "data_asof", "date", "trade_date", "last_nav_date"):
        v = d.get(key)
        if not v:
            continue
        v = str(v)
        m = re.match(r"^(\d{4})(\d{2})(\d{2})$", v)
        if m:
            return "%s-%s-%s" % m.groups()
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", v)
        if m:
            return "-".join(m.groups())
    return None


def resolve_asof(item, path):
    kind = item.get("asof") or ("mtime",)
    if kind[0] == "json":
        v = _load_json_asof(kind[1])
        if v:
            return v, "数据"
    # ("jsonfile", "xxx.json")：读一个固定名字的 json（页面自己写的侧车文件），
    # 用于「数据日不在 pool_state 里」的页（如模拟盘账本的最后一根净值点）。
    if kind[0] == "jsonfile":
        p = os.path.join(REPORTS, kind[1])
        try:
            with open(p, encoding="utf-8") as f:
                v = _asof_of_obj(json.load(f))
        except (OSError, ValueError):
            v = None
        if v:
            return v, "数据"
    try:
        txt = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return None, "?"
    if kind[0] == "meta":
        v = _meta_asof(txt)
        if v:
            return v, "生成"
        v = _meta_asof(txt)  # 再试一次（有些页面写在 title 里）
        if v:
            return v, "生成"
    return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d"), "文件"


def trading_days_between(d0, d1):
    """d0 → d1 之间的交易日数（不含 d0）。d1 缺省今天。"""
    if not d0:
        return None
    cur, n = d1, 0
    while cur > d0 and n < 400:
        if CAL.is_trading_day(cur):
            n += 1
        cur -= timedelta(days=1)
    return n


def staleness(asof):
    """→ (交易日差, 级别, 文案)。级别：ok / warn / old / frozen"""
    if not asof:
        return None, "warn", "数据日未知"
    try:
        d0 = datetime.strptime(asof, "%Y-%m-%d").date()
    except ValueError:
        return None, "warn", "数据日无法解析"
    gap = trading_days_between(d0, TODAY)
    if gap is None:
        return None, "warn", "无法判断"
    if gap <= 1:
        return gap, "ok", "最新"
    if gap <= 3:
        return gap, "ok", "%-d 个交易日前" % gap
    if gap <= 15:
        return gap, "warn", "%d 个交易日前" % gap
    return gap, "old", "%d 个交易日未更新" % gap


# ---------------------------------------------------------------- 修补

NAV_CSS = """
<style id="pf-nav-css">
#pf-nav{position:sticky;top:0;z-index:9999;display:flex;align-items:center;gap:10px;
 flex-wrap:wrap;padding:8px 12px;margin:0 0 10px;background:#11161d;
 border-bottom:1px solid #232a34;font:13px/1.5 "Segoe UI",system-ui,-apple-system,"Microsoft YaHei",sans-serif;
 color:#c8d2df}
#pf-nav a{color:#8fb4e8;text-decoration:none;white-space:nowrap}
#pf-nav a:hover{text-decoration:underline}
#pf-nav .pf-sep{color:#4a5568}
#pf-nav .pf-title{color:#e6edf3;font-weight:600;flex:1 1 auto;min-width:0;
 overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#pf-nav .pf-badge{font-size:11.5px;padding:2px 8px;border-radius:10px;white-space:nowrap;
 background:#1c2735;color:#9fb0c4;border:1px solid #2b3949}
#pf-nav .pf-ok{background:#14261b;color:#7fd18b;border-color:#22452e}
#pf-nav .pf-warn{background:#2a2312;color:#e0b95c;border-color:#4a3d1c}
#pf-nav .pf-old{background:#2b1618;color:#e8807f;border-color:#4d2528}
/* 同域兄弟页：此前只有面包屑，从一页去同域另一页必须先回门面 */
#pf-nav .pf-sibs{display:flex;gap:5px;flex-wrap:wrap;order:3;flex:1 0 100%;
 padding-top:7px;margin-top:5px;border-top:1px solid #1c232d}
#pf-nav .pf-sib{font-size:12px;padding:2px 9px;border-radius:11px;
 background:#161d27;border:1px solid #232d3a;color:#93a3b6}
#pf-nav .pf-sib:hover{color:#cfe0f5;border-color:#31414f;text-decoration:none}
#pf-nav .pf-sib.on{background:#1b2c40;border-color:#33506e;color:#a8cdf0}
@media (max-width:640px){#pf-nav{font-size:12px;padding:7px 10px}
 #pf-nav .pf-sub{display:none}
 #pf-nav .pf-sibs{gap:4px;padding-top:6px}
 #pf-nav .pf-sib{font-size:11.5px;padding:2px 7px}}
</style>
"""


def _siblings(item):
    """同域的其他可见页（不含自己），供导航条做同级切换。

    标签取标题第一段；同域里第一段撞名的（如「选股中心」×5）退到第二段，
    于是得到 选股中心 / A 股 / 美股 / 港股 / 加密。
    """
    peers = [x for x in CATALOG
             if not x.get("hidden") and x["domain"] == item["domain"]
             and x["out"] != item["out"]]
    heads = [p["title"].partition(" · ")[0] for p in peers]
    out = []
    for p, h in zip(peers, heads):
        _, _, tail = p["title"].partition(" · ")
        out.append((p["out"], (tail or h) if heads.count(h) > 1 else h))
    return out


def nav_bar(item, asof, level, note, back="index.html"):
    cls = {"ok": "pf-ok", "warn": "pf-warn", "old": "pf-old"}.get(level, "pf-badge")
    dom = dict((k, (t, d)) for k, t, d, _ in DOMAINS)
    dom_title = dom.get(item["domain"], ("", ""))[0]
    parts = [
        '<div id="pf-nav">',
        '<a href="%s">← 平台</a>' % back,
        '<span class="pf-sep">/</span>',
        '<span class="pf-sub" style="color:#8a93a3">%s</span>' % dom_title if dom_title else "",
        '<span class="pf-sep pf-sub">/</span>' if dom_title else "",
        '<span class="pf-title">%s</span>' % item["title"],
        '<span class="pf-badge %s" title="数据基准日">%s · %s</span>'
        % (cls, asof or "?", note),
    ]
    sibs = _siblings(item)
    if sibs:
        parts.append('<span class="pf-sibs">')
        for href, label in sibs[:8]:
            parts.append('<a class="pf-sib" href="%s">%s</a>' % (href, label))
        parts.append("</span>")
    parts.append("</div>")
    return "".join(parts)


def strip_nav_js(txt):
    """删掉 /static/nav.js —— 静态托管下它必然 404，且会拖慢首屏。"""
    before = txt
    txt = re.sub(r'<script[^>]+src=["\']/static/nav\.js["\'][^>]*>\s*</script>\s*',
                 "", txt, flags=re.I)
    return txt, before != txt


# 标签体匹配：引号内允许出现 `>`（旧实现 [^>]* 被 onclick 里的 history.length>1
# 截断，标签后半截变成可见正文 —— bt_hub/premarket/positions 三页泄漏裸代码，
# 判例 UI-1，2026-11-10）。引号外不允许 `<` 与 `>`。
_ATTR_SAFE = r'(?:(?:[^>"\'<]|"[^"]*"|\'[^\']*\')*)'
ANCHOR_RE = re.compile(r'<a\b(' + _ATTR_SAFE + r')>(.*?)</a>', re.S | re.I)
TAG_RE = re.compile(r'<img\b(' + _ATTR_SAFE + r')>', re.I)
SRC_HREF_RE = re.compile(r'(?:href|src)=(["\'])(.*?)\1', re.I)
DIV_TAG_RE = re.compile(r'<(/?)div\b[^>]*>', re.I)


def _is_dead_href(href):
    """静态平台只服务相对文件：以 / 开头的绝对路径一律判死链。"""
    if not href:
        return False
    return href.strip().startswith("/")


def _cut_block(txt, s):
    """从 txt[s:] 的第一个 <div> 开始做标签配对，返回整个块的结束下标。"""
    depth = 0
    for t in DIV_TAG_RE.finditer(txt, s):
        if t.group(1):
            depth -= 1
            if depth == 0:
                return t.end()
        else:
            depth += 1
    return -1


PORTAL_BREAD_RE = re.compile(
    r"<div\s+style=[\"']display:flex;\s*align-items:center;\s*"
    r"justify-content:space-between", re.I)
NAV_DIV_RE = re.compile(r"<div\s+class=[\"']nav[\"'][^>]*>", re.I)


def _strip_portal_blocks(txt):
    """整块删除页面自带的门户导航：面包屑（含「域内导航」折叠菜单）与死链 nav 条。

    这两个块里全是指向门户服务的链接，pf-nav 已经承担全部站内跳转，
    留着只是视觉噪声（判例 UI-1 附带发现：pick 域一屏三条导航）。
    """
    n = 0
    while True:
        m = PORTAL_BREAD_RE.search(txt)
        if not m:
            break
        e = _cut_block(txt, m.start())
        if e < 0:
            break
        txt = txt[:m.start()] + txt[e:]
        n += 1
    return txt, n


def _remove_nav_blocks(txt):
    """删掉 class=nav 的块中已经不含活链的（防止误删含合法相对链接的导航）。"""
    n = 0
    out, pos = [], 0
    for m in NAV_DIV_RE.finditer(txt):
        s = m.start()
        if s < pos:
            continue
        e = _cut_block(txt, s)
        if e < 0:
            continue
        block = txt[s:e]
        has_live = False
        for a in ANCHOR_RE.finditer(block):
            h = SRC_HREF_RE.search(a.group(1))
            if h and not _is_dead_href(h.group(2)):
                has_live = True
                break
        out.append(txt[pos:s])
        if has_live:
            pos = s  # 保留整块
        else:
            pos = e  # 全是死链，删
            n += 1
    out.append(txt[pos:])
    return "".join(out), n


def soften_dead_links(txt):
    """死链处理（2026-11-10 老田裁定：导航死链整块删，数据死链去壳留文）。

    步骤：① 整块删页面自带的门户面包屑（含「域内导航」折叠菜单）；
    ② 引号感知地处理指向 DEAD_ROUTES 的 <a>（旧实现 [^>] 被 onclick
    属性里的 `>` 截断，标签后半截泄漏成可见正文 —— 判例 UI-1）：
    去壳保留内文，股票名等数据不能丢（判例 UI-4）；
    ③ 删死链 <img>；④ 删已被去壳后不含活链的 class=nav 死链条。
    返回 (txt, 处理的 a 数, 删除的 img 数)。
    """
    txt, _n_bread = _strip_portal_blocks(txt)
    n_a = [0]

    def rep(m):
        h = SRC_HREF_RE.search(m.group(1))
        if not (h and _is_dead_href(h.group(2))):
            return m.group(0)
        n_a[0] += 1
        inner = m.group(2)
        t = re.sub(r"<[^>]+>", "", inner).strip()
        # 装饰性入口（箭头引导、进入模块、见模块页）删干净；
        # 数据文本（股票名等）去壳保留 —— 判例 UI-4。
        if re.match(r"^[←↓→↑]", t) or "见模块页" in t or t == "进入模块 →":
            return ""
        return inner

    txt = ANCHOR_RE.sub(rep, txt)

    n_img = [0]

    def img_rep(m):
        h = SRC_HREF_RE.search(m.group(1))
        if h and _is_dead_href(h.group(2)):
            n_img[0] += 1
            return ""
        return m.group(0)

    txt = TAG_RE.sub(img_rep, txt)

    txt, _n_nav = _remove_nav_blocks(txt)
    return txt, n_a[0], n_img[0]


def inject(txt, bar, css=NAV_CSS):
    """导航条插在 <body> 之后；顺手把 CSS 塞进 </head> 前。"""
    if "<body" in txt:
        i = txt.index(">", txt.index("<body")) + 1
        txt = txt[:i] + bar + txt[i:]
    else:
        txt = bar + txt
    if "</head>" in txt:
        txt = txt.replace("</head>", css + "</head>", 1)
    else:
        txt = css + txt
    return txt


def patch_one(item):
    """修一份内页 → (out_path, 统计)。失败抛异常由上层收集。"""
    src = os.path.join(REPORTS, item["src"])
    if item.get("latest_glob"):
        cand = sorted(glob.glob(os.path.join(REPORTS, item["latest_glob"])))
        if not cand:
            raise FileNotFoundError(item["latest_glob"])
        src = cand[-1]
    if not os.path.exists(src):
        raise FileNotFoundError(item["src"])

    txt = open(src, encoding="utf-8", errors="replace").read()
    asof, asof_kind = resolve_asof(item, src)
    gap, level, note = staleness(asof)
    if item["domain"] == "archive":
        level, note = "old", "归档 · 不再更新"

    txt, nav_removed = strip_nav_js(txt)
    txt, dead_a, dead_img = soften_dead_links(txt)
    txt = inject(txt, nav_bar(item, asof, level, note))

    out = os.path.join(DEST, item["out"])
    os.makedirs(DEST, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(txt)
    return out, dict(asof=asof, gap=gap, level=level, note=note,
                     nav_removed=nav_removed, dead_a=dead_a, dead_img=dead_img)


# ---------------------------------------------------------------- 门面

HOME_CSS = """
<style>
*{box-sizing:border-box}
body{margin:0;background:#0b0f14;color:#e6edf3;
 font:14px/1.65 "Segoe UI",system-ui,-apple-system,"Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:26px 16px 70px}
h1{font-size:20px;margin:0 0 4px;letter-spacing:.2px}
.lead{color:#8a93a3;font-size:12.5px;margin:0 0 18px;line-height:1.7}
.lead b{color:#c8d2df;font-weight:600}
.tstamp{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 22px}
.tchip{font-size:11.5px;padding:3px 9px;border-radius:11px;background:#141b24;
 border:1px solid #222b36;color:#9fb0c4}
.tchip.ok{background:#14261b;border-color:#22452e;color:#7fd18b}
.tchip.warn{background:#2a2312;border-color:#4a3d1c;color:#e0b95c}
.tchip.old{background:#2b1618;border-color:#4d2528;color:#e8807f}
.sec{margin:26px 0 8px;display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;
 border-bottom:1px solid #1d2530;padding-bottom:7px}
.sec h2{font-size:15px;margin:0;letter-spacing:.3px}
.sec .d{color:#7b8698;font-size:11.5px}
.sec .c{color:#5a6577;font-size:11px;margin-left:auto}
/* HERO：第一屏四张核心大卡——进门先回答「今天该看什么」 */
.hero-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin:18px 0 6px}
.card.hero{padding:16px 18px;border-radius:12px;background:#121a25;
 border:1px solid #2a3648;min-height:118px}
.card.hero:hover{background:#17212e;border-color:#3a4c66}
.card.hero .t{font-size:17px}
.card.hero .s{font-size:12.5px;color:#8fa0b5;margin-top:5px}
.card.hero .guide{font-size:12px;color:#a8b8cc;margin-top:8px;line-height:1.6;
 padding:7px 10px;border-radius:7px;background:#0e1520;border-left:2px solid #3a5a86}
.card.hero .m{margin-top:10px}
.card.hero .dt{font-size:11.5px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(238px,1fr));gap:10px}
.card{display:block;padding:12px 13px;border-radius:9px;background:#111720;
 border:1px solid #1e2733;text-decoration:none;color:inherit;transition:.14s;position:relative}
.card:hover{background:#151d28;border-color:#2f3d4f;transform:translateY(-1px)}
.card .t{font-size:14px;font-weight:600;color:#e6edf3;display:flex;gap:6px;align-items:center}
.card .s{font-size:11.5px;color:#7b8698;margin-top:3px;line-height:1.5}
.card .m{display:flex;gap:6px;align-items:center;margin-top:8px}
.card .dt{font-size:11px;padding:1.5px 7px;border-radius:9px;background:#1a222c;color:#8fa0b5}
.card .dt.ok{background:#14261b;color:#7fd18b}
.card .dt.warn{background:#2a2312;color:#e0b95c}
.card .dt.old{background:#2b1618;color:#e8807f}
.flag{font-size:10px;padding:1px 6px;border-radius:4px;background:#2b2f1a;color:#cfc06a;
 border:1px solid #4a4526}
.foot{margin-top:34px;padding-top:14px;border-top:1px solid #1d2530;
 color:#6b7787;font-size:11.5px;line-height:1.9}
.foot a{color:#8fb4e8;text-decoration:none}
.foot a:hover{text-decoration:underline}
.note{margin:14px 0 0;padding:11px 13px;border-radius:8px;background:#101821;
 border-left:3px solid #38506b;color:#94a3b8;font-size:12px;line-height:1.75}
@media (max-width:520px){.grid{grid-template-columns:1fr 1fr;gap:8px}
 .hero-grid{grid-template-columns:1fr}
 .card{padding:10px}.card .t{font-size:13px}
 .card .s{font-size:11px;display:-webkit-box;-webkit-line-clamp:1;
  -webkit-box-orient:vertical;overflow:hidden}
 .sec .d{display:none}}
</style>
"""


def collect():
    """跑一遍 CATALOG，算每页的 asof 与陈旧度。"""
    rows = []
    for item in CATALOG:
        if item.get("hidden"):
            continue
        src = os.path.join(REPORTS, item["src"])
        if item.get("latest_glob"):
            cand = sorted(glob.glob(os.path.join(REPORTS, item["latest_glob"])))
            src = cand[-1] if cand else src
        if not os.path.exists(src):
            rows.append(dict(item=item, missing=True, asof=None, gap=None,
                             level="warn", note="未生成"))
            continue
        asof, _kind = resolve_asof(item, src)
        gap, level, note = staleness(asof)
        if item["domain"] == "archive":
            level, note = "old", "归档"
        rows.append(dict(item=item, missing=False, asof=asof, gap=gap,
                         level=level, note=note,
                         size=os.path.getsize(src)))
    return rows


def build_home(rows=None):
    rows = rows or collect()
    by_dom = {}
    for r in rows:
        by_dom.setdefault(r["item"]["domain"], []).append(r)

    live = [r for r in rows if not r["missing"]]
    worst = max(live, key=lambda r: (r["gap"] or 0)) if live else None
    ok_n = sum(1 for r in live if r["level"] == "ok")

    h = ['<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         '<title>分析台 · 独立版</title>', HOME_CSS, '</head><body><div class="wrap">']

    h.append('<h1>分析台</h1>')
    h.append('<div class="lead">自建、不依赖外部客户端。数据自己同步 · 缠论结构自己算 · '
             '回测自己跑 · 判据带时点可回放。<br>'
             '<b>本页每个入口都标了数据基准日</b>——橙色是超过 3 个交易日、红色是超过 15 个，'
             '别把旧结论当今天的。</div>')

    # 时点总览条
    h.append('<div class="tstamp">')
    h.append('<span class="tchip">共 %d 个入口 · %d 个数据最新</span>' % (len(live), ok_n))
    if worst:
        h.append('<span class="tchip %s">最旧：%s %s</span>'
                 % (worst["level"], worst["item"]["title"], worst["note"]))
    h.append('<span class="tchip">生成 %s</span>' % datetime.now().strftime("%m-%d %H:%M"))
    h.append('</div>')

    # HERO 第一屏：四张核心大卡（hero=True），进门先回答「今天该看什么」。
    # 大卡也是 class="card"，sharecode 注入与 h5_pack 门面卡片自检都兼容。
    heroes = [r for r in rows if r["item"].get("hero") and not r["missing"]]
    if heroes:
        h.append('<div class="sec"><h2>今天看什么</h2>'
                 '<span class="d">四个核心工作台，点进去就是当天的活</span></div>')
        h.append('<div class="hero-grid">')
        for r in heroes:
            it = r["item"]
            h.append('<a class="card hero" href="%s">'
                     '<div class="t">%s</div>'
                     '<div class="s">%s</div>'
                     '<div class="guide">%s</div>'
                     '<div class="m"><span class="dt %s">%s · %s</span></div></a>'
                     % (it["out"], it["title"], it["sub"] or "&nbsp;",
                        it.get("hero_sub") or "&nbsp;",
                        r["level"], r["asof"] or "?", r["note"]))
        h.append('</div>')

    for key, title, desc, cadence in DOMAINS:
        items = [r for r in (by_dom.get(key) or [])
                 if not r["item"].get("hero")]  # hero 页已在第一屏，不重复
        if not items:
            continue
        h.append('<div class="sec"><h2>%s</h2><span class="d">%s</span>'
                 '<span class="c">%s</span></div>' % (title, desc, cadence))
        h.append('<div class="grid">')
        for r in items:
            it = r["item"]
            if r["missing"]:
                h.append('<span class="card" style="opacity:.45">'
                         '<div class="t">%s</div><div class="s">尚未生成</div></span>'
                         % it["title"])
                continue
            flag = '<span class="flag">主入口</span>' if it.get("primary") else ""
            h.append('<a class="card" href="%s">'
                     '<div class="t">%s%s</div>'
                     '<div class="s">%s</div>'
                     '<div class="m"><span class="dt %s">%s · %s</span></div></a>'
                     % (it["out"], it["title"], flag, it["sub"] or "&nbsp;",
                        r["level"], r["asof"] or "?", r["note"]))
        h.append('</div>')

    h.append('<div class="note"><b>读这页要知道的三件事</b><br>'
             '① 结构类页面（结构日报 / 工作台 / 板块平台）的基准是<b>最近一个收盘日</b>，'
             '盘中会叠实时价，页内已把两个时点分开写。<br>'
             '② 选股、回测、模拟各条线的更新节奏不同（盘前 08:20 · 按需 · 手动），'
             '卡片上的日期是那页数据自己的，不是本页生成时间。'
             '<b>模拟域当前没有计划任务在跑</b>，账本停在卡片写的那个日期。<br>'
             '③ 旧门户（127.0.0.1:8091）的入口链接已按 11-10 裁定整体移除，'
             '站内跳转全部走每页顶部的导航条。</div>')

    h.append('<div class="foot">数据源：tushare / tdx / 雪球 三源同步；'
             '交易日历双源交叉验证（2026 全年 0 差异）。<br>'
             '本平台为纸面研究，不构成投资建议。</div>')
    h.append('</div>')

    # 门面成了根入口后，两件事必须在这里兜住：
    #   ① 旧深链（飞书卡片里的 ?stage=三买、?code=xxx、?sector=xxx）不能落到首页就停住，
    #      按参数直接转发到对应页面，query 原样带过去；
    #   ② 门面的卡片是静态 href，点进去会丢掉 sharecode —— 线上是**逐请求**校验，
    #      丢一次就是 403「链接不完整」。所以进页面就把 sharecode 补到每张卡上。
    h.append("""<script>
(function(){
  var qs = location.search.replace(/^\\?/, '');
  if(!qs) return;
  var p = new URLSearchParams(qs);

  var sc = p.get('sharecode');
  if(sc){
    var cards = document.querySelectorAll('a.card[href]');
    for(var i=0;i<cards.length;i++){
      var a = cards[i], h = a.getAttribute('href');
      if(!h || h.indexOf('sharecode=') >= 0) continue;
      a.setAttribute('href', h + (h.indexOf('?')<0 ? '?' : '&')
                     + 'sharecode=' + encodeURIComponent(sc));
    }
  }

  var to = null;
  if(p.get('sector')) to = 'sector_platform.html';
  else if(p.get('code') || p.get('stage')) to = 'pool_report_h5.html';
  if(to) location.replace(to + '?' + qs);
})();
</script>""")
    h.append('</body></html>')

    out = os.path.join(REPORTS, "platform_home.html")
    html = "".join(h)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    # 门面同时作为发布根的 index.html —— 平台的第一眼是「有哪些分析台」，
    # 而不是直接掉进某一天的名单。结构日报仍在 pool_report_h5.html。
    os.makedirs(DEST, exist_ok=True)
    with open(os.path.join(DEST, "index.html"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write(html)
    return out, rows


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="列清单与陈旧度")
    ap.add_argument("--home", action="store_true", help="生成门面")
    ap.add_argument("--patch", action="store_true", help="修补内页到 h5_daily/")
    ap.add_argument("--all", action="store_true", help="门面 + 修补")
    a = ap.parse_args()
    if not any((a.list, a.home, a.patch, a.all)):
        ap.print_help()
        return 0

    rows = collect()
    if a.list:
        print("%-40s %6s %8s %-14s %s"
              % ("发布名", "KB", "数据日", "陈旧度", "标题"))
        for r in rows:
            it = r["item"]
            if r["missing"]:
                print("%-40s %6s %8s %-14s %s" % (it["out"], "-", "-", "未生成", it["title"]))
                continue
            print("%-40s %6d %8s %-14s %s"
                  % (it["out"], r["size"] // 1024, r["asof"], r["note"], it["title"]))

    if a.home or a.all:
        out, _ = build_home(rows)
        print("[home] %s  %.1f KB" % (out, os.path.getsize(out) / 1024.0))

    if a.patch or a.all:
        os.makedirs(DEST, exist_ok=True)
        fails = []
        for item in CATALOG:
            try:
                out, st = patch_one(item)
                print("[patch] %-34s %s  %s" % (item["out"], st["asof"] or "?",
                                                st["note"]))
            except Exception as e:  # noqa: BLE001
                fails.append((item["src"], str(e)))
        if fails:
            print("[fail] %d 个页面修补失败：" % len(fails))
            for s, e in fails:
                print("   %s → %s" % (s, e))
            return 1
        print("[patch] 完成 %d 个页面 → %s" % (len(CATALOG), DEST))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
