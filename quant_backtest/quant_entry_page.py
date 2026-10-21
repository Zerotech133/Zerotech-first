# -*- coding: utf-8 -*-
"""量化交易板块 · 入口生产页（WO-QT-20261021-087）：/quant-entry。

定位：老田 10-21 定版的板块入口——「一进场第一眼」= 四市场总表
（今日多空 · 策略货架 · 股票池 · Agent 席），点进行内席位门户
（真实在役路由）。与原型 v3（deliverables/2026-10-21_量化交易板块
全能力原型_v3.html）一一对应；原型是 Demo，本页是生产件。

设计纪律（对齐 WO-078 范式 + 老田收敛口径）：
· 纯渲染层——所有数字只读聚合盘上产物，单一口径源零漂移：
    - 股票池     ← portal/data/todays_picks.json（选票池权威实体）
    - 荀攸研究池 ← quant_backtest/reports/xunyou_pool_*.json（最新一份）
    - 策略货架   ← portal/strategies.json（郭嘉策略研究院记分卡）
    - 行情多空   ← quant_backtest/data/regime_center/snapshot_*.json
                   （最新快照）× todays_picks.strategies.qpzm（青萍闸门）
    - 美股池     ← quant_backtest/data/chanlun_pool_us.jsonl（行/标的/时点）
    - 币市仓     ← quant_backtest/data/crypto/*.parquet（标的数 + BTC 时点）
  本模块对上述产线文件零改动（grep 可证：无 open(...,"w")）。
· 缺源显式标「待接」不造数；多空灯双源合成口径（拍板默认②）：
    regime × 青萍一致才亮（偏多=红/偏空=绿，A股惯例涨红跌绿），
    分歧亮琥珀「多空分歧」，单源亮琥珀「单源」，无源灰「待判」。
· 影子期红线常驻：combo 只注册不执行；触发器未挂计划任务如实标注
  （系统级变更须五要素另批，与页面解耦）。
· 容错：任何数据源缺失/坏 JSON → 该格降级「待接」照常出页，不炸。
· 每请求纯读刷新（render_default，portal 路由用，廉价无状态）。

页面生成：write_html（CLI `python quant_entry_page.py [--out PATH]`）
或 render_default（portal /quant-entry 每请求同步刷新）。
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

CST = timezone(timedelta(hours=8))
REPORT_PATH = os.path.join(HERE, "reports", "quant_entry.html")

PICKS_PATH = os.path.join(ROOT, "portal", "data", "todays_picks.json")
STRATS_PATH = os.path.join(ROOT, "portal", "strategies.json")
REGIME_GLOB = os.path.join(HERE, "data", "regime_center", "snapshot_*.json")
XUNYOU_GLOB = os.path.join(HERE, "reports", "xunyou_pool_*.json")
US_POOL_PATH = os.path.join(HERE, "data", "chanlun_pool_us.jsonl")
CRYPTO_DIR = os.path.join(HERE, "data", "crypto")

# 影子期（WO-070 落地日 2026-10-21 起 14 天；D7=10-28 中间复核，
# D14=11-04 解锁评审+老田签准，本页只做展示）
SHADOW_START = "2026-10-21"
SHADOW_END = "2026-11-04"

# 席位门户：真实在役路由（不存在/未实跑的如实标注，不造链接）。
# 纪律：desc 只放职能描述，不放会漂移的数字——数字一律走 Table 行
# 的每请求纯读聚合（SEATS 是静态骨架，数字漂移=文案说谎）。
SEATS = [
    ("选票 · 荀攸", "六度选股推池 · 研究态 BLOCK_CUTOVER",
     "研究池无生产页（原型 Demo 承载）· 切流须老田签准", None),
    ("入池 · 郭嘉", "策略研究院 · 每日聚合入池",
     "/strategies 策略中心记分卡（60 交易日考核）", "/strategies"),
    ("盯盘 · 张郃", "G1 触发器 · 回踩/突破阈值",
     "/pool-watch 触发监控台（WO-078 生产页）", "/pool-watch"),
    ("投审 · 三席", "刘晔/满宠/蒋济会审 → 股票池 v2",
     "v2 账本未落盘（WO-083 建成未实跑）· 待首跑", None),
    ("模拟交易 · 交易所", "paper.db 账本 · combo 只注册",
     "/bt-positions 纸面持仓账本", "/bt-positions"),
    ("复盘 · 盘后", "daily_review 17:40 档 · a0 点评 · ROI",
     "产出归档 agent_platform/data/shadow/ · 无独立路由", None),
    ("订票 · 交易计划", "exit_rules 引擎在盘 · 计划流",
     "待建（阶段二）· 本页占位标注", None),
]

_CSS = """
  :root{--bg:#f5f6f8;--card:#fff;--line:#e3e6ea;--txt:#1c2330;--sub:#6b7686;
        --up:#d92b2b;--down:#0a9e5c;--accent:#2456c4;--warn:#b7791f;
        --navy:#16324f;--navy2:#185fa5;--teal:#0f6e56;--red:#a32d2d;
        --sb-bg:#fff7e8;--sb-line:#f0d9a8}
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:var(--bg);color:var(--txt);
       font:14px/1.6 "Microsoft YaHei","PingFang SC",sans-serif;padding:20px}
  .wrap{max-width:1280px;margin:0 auto}
  h1{font-size:20px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .sub{color:var(--sub);font-size:12px;margin:4px 0 14px;line-height:1.7}
  .exits{font-size:11px;color:var(--sub);margin-bottom:14px}
  .exits a{color:var(--accent);text-decoration:none;margin:0 5px}
  .badge{display:inline-block;font-size:11px;border-radius:4px;padding:1px 8px}
  .b-shadow{background:var(--sb-bg);color:var(--warn);border:1px solid var(--sb-line)}
  .b-off{background:#f1f2f4;color:var(--sub);border:1px solid var(--line)}
  .b-bear{background:#e1f5ee;color:var(--down)}
  .b-bull{background:#fdeaea;color:var(--up)}
  .b-wait{background:#f1f2f4;color:var(--sub)}
  .b-diverge{background:var(--sb-bg);color:var(--warn);border:1px solid var(--sb-line)}
  .lamp{display:inline-flex;align-items:center;gap:5px;font-size:12px}
  .lamp .dot{width:7px;height:7px;border-radius:50%;background:var(--down);
            display:inline-block}
  .lamp.up .dot{background:var(--up)}
  .lamp.warn .dot{background:var(--warn)}
  .lamp.off .dot{background:#b4b7bd}
  .panel{background:var(--card);border:1px solid var(--line);border-radius:10px;
         overflow:hidden;margin-bottom:14px}
  .panel h2{font-size:14px;padding:10px 14px;border-bottom:1px solid var(--line);
            display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .panel h2 .hint{font-weight:normal;font-size:11px;color:var(--sub);margin-left:auto}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th{background:#fafbfc;color:var(--sub);font-weight:normal;text-align:left;
     padding:8px 12px;border-bottom:1px solid var(--line);font-size:12px;
     white-space:nowrap}
  td{padding:10px 12px;border-bottom:1px solid #f0f2f5;vertical-align:top}
  tr:last-child td{border-bottom:none}
  td.mk{font-weight:bold;color:var(--navy);white-space:nowrap}
  .cell-sub{display:block;font-size:11px;color:var(--sub);margin-top:2px;
            line-height:1.5}
  td.go{white-space:nowrap;text-align:right}
  td.go a{font-size:12px;color:var(--accent);text-decoration:none}
  td.go a:hover{text-decoration:underline}
  td.go .none{font-size:12px;color:var(--sub)}
  .banner{background:var(--sb-bg);border:1px solid var(--sb-line);border-radius:10px;
          padding:10px 14px;font-size:13px;color:#7a5b17;margin-bottom:14px;
          line-height:1.7}
  .banner.blue{background:#e6f1fb;border-color:#a8cbe8;color:#0c447c}
  .seats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;padding:14px}
  .seat{border:1px solid var(--line);border-radius:10px;padding:12px}
  .seat.todo{border-style:dashed;background:#fafbfc}
  .seat .sn{font-size:13px;font-weight:bold;color:var(--navy)}
  .seat .sd{font-size:11px;color:var(--sub);margin-top:3px;line-height:1.55}
  .seat .sl{margin-top:6px;font-size:12px}
  .seat .sl a{color:var(--accent);text-decoration:none}
  .appendix{font-size:12px;color:var(--sub);margin:0 0 14px;line-height:1.9}
  .appendix b{color:var(--txt)}
  @media (max-width:900px){.seats{grid-template-columns:repeat(2,1fr)}}
"""


# ---------------------------------------------------------------- 数据层
def _load_json(path):
    """容错读 JSON：缺失/坏文件 → None（调用方降级「待接」）。"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _fmt_dt(ts):
    return str(ts or "").replace("T", " ")[:16]


def _mtime_dt(path):
    try:
        return datetime.fromtimestamp(os.path.getmtime(path))
    except Exception:
        return None


def load_regime():
    """最新 regime 快照（glob 取新）。返回 dict 或 None。"""
    import glob as _glob
    snaps = sorted(_glob.glob(REGIME_GLOB))
    for p in reversed(snaps):
        d = _load_json(p)
        if isinstance(d, dict) and d.get("regime"):
            d["_file"] = os.path.basename(p)
            return d
    return None


def load_picks():
    return _load_json(PICKS_PATH) or {}


def load_xunyou():
    """最新一份荀攸推池（按文件名日期排序取新）。"""
    import glob as _glob
    pools = sorted(_glob.glob(XUNYOU_GLOB))
    for p in reversed(pools):
        d = _load_json(p)
        if isinstance(d, dict) and isinstance(d.get("pool"), list):
            d["_file"] = os.path.basename(p)
            return d
    return None


def load_strategies():
    d = _load_json(STRATS_PATH)
    return (d or {}).get("strategies") or []


def us_pool_stats():
    """美股缠论池：行数 / 去重标的数 / 最新 mtime。"""
    n = 0
    codes = set()
    mtime = _mtime_dt(US_POOL_PATH)
    try:
        with open(US_POOL_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                n += 1
                try:
                    o = json.loads(line)
                    c = o.get("code") or o.get("symbol")
                    if c:
                        codes.add(c)
                except Exception:
                    pass
    except Exception:
        return None
    return {"rows": n, "codes": len(codes), "mtime": mtime}


def _last_trade_date(path):
    """读 parquet 最后一根交易日（YYYYMMDD）；文件缺失/读失败 → None。"""
    try:
        import pandas as pd
        d = pd.read_parquet(path, columns=["trade_date"])
        if d.empty:
            return None
        return str(d["trade_date"].max()).replace("-", "")[:8]
    except Exception:
        return None


def crypto_stats(now=None):
    """币市行情仓：parquet 标的数 + BTC 末根交易日 + 新鲜度。

    WO-QT-20261021-094：`CryptoSync` 直连 Binance 已停用（供给层已含
    CRYPTO 37 对，直连属双轨），本地 parquet 改由 `export_us_hk
    --market CRYPTO` 从供给层导出维护。**若连续不再导出就会停更**——
    停更必须看得见：此处显式判 stale（末根交易日距 now 超 3 个自然日），
    并把展示口径从「文件 mtime」换成「数据时点」，否则旧行情会被当实时读。
    """
    try:
        files = [f for f in os.listdir(CRYPTO_DIR)
                 if f.lower().endswith(".parquet")]
    except Exception:
        return None
    if not files:
        return None
    btc = os.path.join(CRYPTO_DIR, "BTC_USDT.parquet")
    td = _last_trade_date(btc)
    stale = True
    if td:
        try:
            d0 = datetime.strptime(td, "%Y%m%d").replace(tzinfo=CST)
            stale = ((now or datetime.now(CST)) - d0).days > 3
        except Exception:
            stale = True
    return {"n": len(files), "btc_mtime": _mtime_dt(btc),
            "btc_date": td, "stale": stale}


def regime_stale(snap, now):
    """快照 stale 判定：trade_date 距 now 超过 3 个自然日 → stale。"""
    if not snap:
        return True
    td = str(snap.get("trade_date") or "")
    try:
        d0 = datetime.strptime(td, "%Y%m%d").replace(tzinfo=CST)
    except Exception:
        return True
    return (now - d0).days > 3


# ---------------------------------------------------------------- 多空灯
def _qpzm_env(picks):
    """青萍环境闸门口径（todays_picks.strategies.qpzm.env_gate）。

    字段口径（盘上实证）：env_gate.score（0-100）+ env_gate.safe +
    env_gate.state（人读文本）。只认显式 score：
      score < 30 → bear（偏冷）；>= 60 → bull（偏暖）；中间 → neutral。
    返回 'bear' / 'bull' / 'neutral' / None（无源）。
    """
    st = ((picks or {}).get("strategies") or {}).get("qpzm") or {}
    gate = st.get("env_gate") or {}
    try:
        score = float(gate.get("score"))
    except (TypeError, ValueError):
        return None
    if score < 30:
        return "bear"
    if score >= 60:
        return "bull"
    return "neutral"


def _regime_side(snap):
    """regime → 多空侧：risk_off→bear，risk_on→bull，其他→None。"""
    if not snap:
        return None
    r = str(snap.get("regime") or "").lower()
    if r == "risk_off":
        return "bear"
    if r == "risk_on":
        return "bull"
    return None


def stance_lamp(snap, picks):
    """双源合成（拍板默认②）：regime × 青萍。

    返回 (css_cls, label, note)：
      cls: 'down' 偏空(绿) | 'up' 偏多(红) | 'warn' 分歧/单源 | 'off' 待判
    涨红跌绿（A股惯例）。
    """
    rg = _regime_side(snap)
    qp = _qpzm_env(picks)
    rg_note = ("regime %s（%s）" % (snap.get("regime"),
                                   snap.get("trade_date") or "?")
               if snap else "regime 无源")
    qp_note = {"bear": "青萍偏冷", "bull": "青萍偏暖",
               "neutral": "青萍中性", None: "青萍无源"}[qp]
    if rg and qp:
        if rg == qp and rg != "neutral":
            side = "down" if rg == "bear" else "up"
            label = "偏空" if rg == "bear" else "偏多"
            return side, label, rg_note + " × " + qp_note + " · 双源一致"
        if rg == "neutral" or qp == "neutral":
            return "warn", "多空分歧", rg_note + " × " + qp_note + " · 一源中性"
        return "warn", "多空分歧", rg_note + " × " + qp_note + " · 双源相反"
    if rg or qp:
        have = rg_note if rg else qp_note
        return "warn", "单源待判", have + " · 另一源无数据"
    return "off", "待判", "双源均无数据 · 灯不亮不造数"


# ---------------------------------------------------------------- 渲染
def _fmt_pct(x):
    try:
        return "%+.2f%%" % float(x)
    except (TypeError, ValueError):
        return "-"


def render_html(ctx, now=None):
    """纯函数渲染：聚合 ctx（load_context 产物）→ HTML 字符串。"""
    now = now or datetime.now(CST)
    picks = ctx.get("picks") or {}
    counts = picks.get("counts") or {}
    strats = ctx.get("strategies") or []
    live = sum(1 for s in strats if s.get("status") == "live")
    shadow = sum(1 for s in strats if s.get("status") == "shadow")
    tool = sum(1 for s in strats if s.get("status") == "tool")
    snap = ctx.get("regime")
    xq = ctx.get("xunyou") or {}
    us = ctx.get("us_pool") or {}
    crypto = ctx.get("crypto") or {}

    # —— A股多空灯（双源合成） ——
    cls, label, note = stance_lamp(snap, picks)
    if regime_stale(snap, now):
        note += " · ⚠ 快照 stale"
    cn_stance = ('<span class="lamp %s"><span class="dot"></span>'
                 '<b>%s</b></span><span class="cell-sub">%s</span>'
                 % (cls, label, note))

    # —— 股票池格 ——
    total = counts.get("total")
    t2 = counts.get("T2_high_value")
    if total:
        pool_cell = ("%s 只<span class=\"cell-sub\">T2 高价值 %s · T3 单源 %s · "
                     "scan_run_date %s</span>"
                     % (total, t2 if t2 is not None else "-",
                        counts.get("T3_single", "-"),
                        picks.get("scan_run_date") or "-"))
        if xq.get("pool") is not None:
            t1 = sum(1 for r in xq["pool"]
                     if r.get("tier") == "T1")
            pool_cell += ("<span class=\"cell-sub\">+ 荀攸研究池 %d 只"
                          "（T1 %d · %s）</span>"
                          % (len(xq["pool"]), t1,
                             xq.get("cutover_verdict") or "研究态"))
    else:
        pool_cell = ('<span class="badge b-wait">待接</span>'
                     '<span class="cell-sub">todays_picks.json 不可用</span>')

    # —— 策略货架格 ——
    if strats:
        strat_cell = ("%d 条上架<span class=\"cell-sub\">%d live · %d shadow · "
                      "%d tool（郭嘉策略研究院）</span>"
                      % (len(strats), live, shadow, tool))
    else:
        strat_cell = ('<span class="badge b-wait">待接</span>'
                      '<span class="cell-sub">strategies.json 不可用</span>')

    # —— 市场行 ——
    rows = []
    rows.append(
        "<tr><td class=\"mk\">A股</td><td>%s</td><td>%s</td><td>%s</td>"
        "<td>选票 · 入池 · 盯盘 · 投审<span class=\"cell-sub\">"
        "荀攸 / 郭嘉 / 张郃 / 刘晔·满宠·蒋济</span></td>"
        "<td class=\"go\"><a href=\"/pool-watch\">盯盘台 →</a></td></tr>"
        % (cn_stance, strat_cell, pool_cell))
    if us:
        us_stance = ('<span class="lamp off"><span class="dot"></span>'
                     '<b>待判</b></span><span class="cell-sub">'
                     '无 Regime 通道 · 灯不亮不造数</span>')
        rows.append(
            "<tr><td class=\"mk\">美股</td><td>%s</td>"
            "<td>缠论可复用 · 未登记<span class=\"cell-sub\">"
            "A 股登记策略 · 本市场无记分卡</span></td>"
            "<td>缠论池 %d 行<span class=\"cell-sub\">%d 标的 · %s · 未入池"
            "</span></td>"
            "<td>待建<span class=\"cell-sub\">WO-088 已探测 · 5924 只 52%% 过门槛"
            "· 无 Regime/舆情 · 待立项</span></td>"
            "<td class=\"go\"><span class=\"none\">探针已出 · 无页</span></td></tr>"
            % (us_stance, us.get("rows", 0), us.get("codes", 0),
               _fmt_dt(us.get("mtime")) or "-"))
    else:
        rows.append(
            "<tr><td class=\"mk\">美股</td>"
            "<td><span class=\"badge b-wait\">待判</span>"
            "<span class=\"cell-sub\">无 Regime 通道</span></td>"
            "<td><span class=\"badge b-wait\">待接</span></td>"
            "<td><span class=\"badge b-wait\">待接</span>"
            "<span class=\"cell-sub\">chanlun_pool_us.jsonl 不可用</span></td>"
            "<td><span class=\"badge b-wait\">待建</span></td>"
            "<td class=\"go\"><span class=\"none\">探针未立 · 无页</span></td></tr>")
    rows.append(
        "<tr><td class=\"mk\">港股</td>"
        "<td><span class=\"lamp off\"><span class=\"dot\"></span><b>待判</b>"
        "</span><span class=\"cell-sub\">无 Regime 通道 · 灯不亮不造数</span></td>"
        "<td><span class=\"badge b-wait\">待建</span>"
        "<span class=\"cell-sub\">缠论可复用 · 数据未达标</span></td>"
        "<td><span class=\"badge b-wait\">待接</span>"
        "<span class=\"cell-sub\">扫描已停（WO-095 10-21 16:10 任务停用）· 3408 只"
        " 0% 过 120 根门槛（中位 94 行）· 待情报补历史</span></td>"
        "<td><span class=\"badge b-wait\">待建</span>"
        "<span class=\"cell-sub\">数据达标后复跑再立项</span></td>"
        "<td class=\"go\"><span class=\"none\">探针已出 · 无页</span></td></tr>")
    if crypto:
        _c_stale = ("<b style=\"color:#c0392b\"> ⚠停更</b>"
                    if crypto.get("stale") else "")
        rows.append(
            "<tr><td class=\"mk\">虚拟币</td>"
            "<td><span class=\"lamp off\"><span class=\"dot\"></span><b>待判</b>"
            "</span><span class=\"cell-sub\">无 Regime 通道 · 7×24 节奏需单独立口径"
            "</span></td>"
            "<td><span class=\"badge b-wait\">待建</span>"
            "<span class=\"cell-sub\">行情仓已在 · 策略/风控/账本未立项</span></td>"
            "<td>行情仓 %d 标的<span class=\"cell-sub\">BTC_USDT %s%s · "
            "WO-090 评估中</span></td>"
            "<td><span class=\"badge b-wait\">待建</span>"
            "<span class=\"cell-sub\">源已收编供给层 · 直连停用（WO-094）"
            "</span></td>"
            "<td class=\"go\"><span class=\"none\">未立项 · 无页</span></td></tr>"
            % (crypto.get("n", 0), crypto.get("btc_date") or "-", _c_stale))
    else:
        rows.append(
            "<tr><td class=\"mk\">虚拟币</td>"
            "<td><span class=\"badge b-wait\">待判</span></td>"
            "<td><span class=\"badge b-wait\">待建</span></td>"
            "<td><span class=\"badge b-wait\">待接</span>"
            "<span class=\"cell-sub\">data/crypto 不可用</span></td>"
            "<td><span class=\"badge b-wait\">待建</span></td>"
            "<td class=\"go\"><span class=\"none\">未立项 · 无页</span></td></tr>")
    table_rows = "".join(rows)

    # —— 席位门户 ——
    seat_html = []
    for name, desc, link_text, href in SEATS:
        link = ('<a href="%s">%s</a>' % (href, link_text)
                if href else
                '<span style="color:var(--sub)">%s</span>' % link_text)
        seat_html.append(
            '<div class="seat%s"><div class="sn">%s</div>'
            '<div class="sd">%s</div><div class="sl">%s</div></div>'
            % (" todo" if not href else "", name, desc, link))
    seats_html = "".join(seat_html)

    banner = ('<div class="banner">⛔ <b>影子期红线（常驻）</b>：信号只落池'
              '不进 combo 账本、不产生真实委托；combo 只注册不执行'
              '（WO-070/078/083）｜ 触发器未挂计划任务（系统级变更须五要素'
              '另批，与页面解耦）｜ 解锁须 D7 复核（10-28）+ D14 评审'
              '（11-04）+ 老田签准。多空灯口径：regime × 青萍双源，一致才亮、'
              '分歧亮琥珀、单源亮琥珀、无源灰（涨红跌绿）。</div>')

    appendix = ('<div class="appendix">附录（不占主表）：'
                '<b>大宗交易</b>——黄金影子 AU.SHF 10-20 18:30 · 0 条目，'
                '待接；<b>外汇</b>——Turbo 选型未启动，待接。两个市场均无 '
                'Regime 通道，灯不亮、不造数。</div>')

    return ('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>量化交易板块 · 入口</title><style>%s</style></head>'
            '<body><div class="wrap">'
            '<h1>📈 量化交易板块 · 入口'
            '<span class="badge b-off">生产页 · WO-087</span>'
            '<span class="badge b-shadow">影子期 · %s 止</span></h1>'
            '<div class="sub">老田定版口径：一进场第一眼 = 四市场总表'
            '（今日多空 · 策略货架 · 股票池 · Agent 席）；深度点进席位门户。'
            '缺源显式标「待接」不造数；所有数字每请求纯读刷新盘上产物。'
            '生成于 %s</div>'
            '<div class="exits">出口：<a href="/portal">/portal 总入口</a>'
            '<a href="/intel">/intel 情报</a><a href="/org">/org 军团</a>'
            '<a href="http://127.0.0.1:8787">8787 飞书通讯</a>'
            '<a href="/ops-board">/ops-board 运维</a></div>'
            '%s'
            '<div class="panel"><h2>🧭 四市场总表'
            '<span class="hint">行=市场 · 一条独立 Turbo 数据通道 · '
            '每请求纯读刷新</span></h2>'
            '<table><tr><th>市场</th><th>今日多空</th><th>策略货架</th>'
            '<th>股票池</th><th>Agent 席</th><th></th></tr>%s</table></div>'
            '<div class="panel"><h2>🪑 席位门户'
            '<span class="hint">六能力 + 盯盘触发器 · 真实在役路由'
            '（虚线=未实跑/待建）</span></h2><div class="seats">%s</div></div>'
            '%s'
            '<div class="banner blue">📌 拍板状态（老田 10-21 收敛口径，'
            '3 点默认执行中）：① 市场优先级=A 股深挖 + 美股缠论池探针先行，'
            '币市只评估，外汇暂缓；② 多空灯=regime × 青萍双源合成（本页已实现）；'
            '③ Agent 独立编制，四席只管 A 股，投审聚合席默认「无名确定性引擎」'
            '（WO-091）。圈划异议前按默认执行。</div>'
            '</div></body></html>'
            % (_CSS, SHADOW_END, now.strftime("%Y-%m-%d %H:%M:%S"),
               banner, table_rows, seats_html, appendix))


def load_context():
    """聚合全部只读数据源（portal 路由每请求调用）。"""
    return {
        "picks": load_picks(),
        "strategies": load_strategies(),
        "regime": load_regime(),
        "xunyou": load_xunyou(),
        "us_pool": us_pool_stats(),
        "crypto": crypto_stats(),
    }


def render_default(now=None):
    """读真实产物渲染（portal 路由用）：纯读，失败由调用方降级。"""
    return render_html(load_context(), now=now)


def write_html(now=None, out_path=None):
    """落盘生成报告页（CLI 用），返回写入路径。"""
    html = render_default(now=now)
    out = out_path or REPORT_PATH
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    return out


if __name__ == "__main__":
    out = REPORT_PATH
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
    print(write_html(out_path=out))
