# -*- coding: utf-8 -*-
"""许褚营·双池触发监控台（WO-QT-20261020-078）：/pool-watch 生产页。

定位：WO-070 张郃触发器（pool_watch）的可视化姊妹页，与 /bt-signals
并列（双池口径：todays_picks=选票池权威实体，/bt-signals=人工观察名单）。

设计纪律（原型 v1 老田拍板"按计划开发"）：
· 纯渲染层——阈值/监控集口径全部复用 pool_watch 模块常量与
  load_watchlist（单一口径源，零造轮子）；触发事件只读
  signal_pool.read_events；不 import 消费端/engine（影子期铁律，
  grep 可证）；本模块对上述产线文件零改动。
· 默认只展示监控集（tier∈{T1,T2} 且含买入类 source，同触发器口径）；
  其余池内票收进「未纳监控」折叠区（诚实展示，不冒充监控对象）。
· 影子期红线常驻：信号只落池不进 combo；触发器挂载状态如实标注
  「未挂计划任务」（系统级变更须五要素另批，与页面解耦）。
· 容错：todays_picks.json 缺失/坏 JSON → 空态照常出页；
  signal_pool.jsonl 缺失/坏行 → 「暂无触发事件」，不炸。
· 涨红跌绿（A股惯例）；signal_id 展示与 WO-071 契约一致。

页面生成：write_html（CLI `python pool_watch_page.py [--out PATH]`）
或 render_default（portal 路由每请求同步刷新，廉价纯读渲染）。
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pool_watch  # noqa: E402
import signal_pool  # noqa: E402

CST = timezone(timedelta(hours=8))
REPORT_PATH = os.path.join(HERE, "reports", "pool_watch.html")

# 影子期（WO-070 落地日 2026-10-21 起 14 天；D7 中间复核 10-28 由
# 066/R3 线另行盯守，本页只做展示）
SHADOW_START = "2026-10-21"
SHADOW_END = "2026-11-04"

DIR_LABEL = {"pull": "回踩", "break": "突破"}
MAX_EVENT_ROWS = 20  # 事件流最多展示最近 20 条（append-only 全量在池）

_CSS = """
  :root{--bg:#0d1117;--card:#131a24;--line:#1f2937;--txt:#e6edf3;--sub:#7b8698;
        --up:#e3554f;--down:#3fa96a;--accent:#5b9bd5;--warn:#e0b95c;--cool:#a98fd6;
        --sb-bg:#2a2312;--sb-line:#4a3d1c}
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:var(--bg);color:var(--txt);
       font:14px/1.6 "Microsoft YaHei","PingFang SC",sans-serif;padding:20px}
  .wrap{max-width:1180px;margin:0 auto}
  h1{font-size:20px;display:flex;align-items:center;gap:10px}
  .sub{color:var(--sub);font-size:12px;margin:4px 0 16px}
  .cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:14px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;
        padding:14px 16px}
  .card .k{font-size:12px;color:var(--sub)}
  .card .v{font-size:22px;font-weight:bold;margin-top:2px}
  .card .note{font-size:11px;color:var(--sub);margin-top:2px}
  .badge{display:inline-block;font-size:11px;border-radius:4px;padding:1px 8px}
  .b-shadow{background:var(--sb-bg);color:var(--warn);border:1px solid var(--sb-line)}
  .b-off{background:#161d27;color:var(--sub);border:1px solid var(--line)}
  .panel{background:var(--card);border:1px solid var(--line);border-radius:10px;
         overflow:hidden;margin-bottom:14px}
  .panel h2{font-size:14px;padding:10px 14px;border-bottom:1px solid var(--line);
            display:flex;align-items:center;gap:8px}
  .panel h2 .hint{font-weight:normal;font-size:11px;color:var(--sub);margin-left:auto}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th{background:#0f1620;color:var(--sub);font-weight:normal;text-align:left;
     padding:7px 10px;border-bottom:1px solid var(--line);font-size:12px;
     white-space:nowrap}
  td{padding:8px 10px;border-bottom:1px solid #1f2937;white-space:nowrap}
  tr:hover td{background:#182231}
  .up{color:var(--up);font-weight:bold}
  .down{color:var(--down);font-weight:bold}
  .pill{display:inline-block;font-size:11px;border-radius:10px;padding:1px 9px}
  .p-buy{background:#2b1618;color:var(--up)}
  .p-watch{background:#1f2937;color:var(--sub)}
  .p-hit{background:#101d2e;color:var(--accent);font-weight:bold}
  .p-reg{background:#2b1618;color:#c0392b;border:1px dashed #e8807f}
  .p-rest{background:#161d27;color:#7b8698}
  .sid{font-family:Consolas,monospace;font-size:11px;color:var(--sub)}
  .filter{padding:8px 14px;border-bottom:1px solid var(--line)}
  .filter a{font-size:12px;color:var(--sub);text-decoration:none;
            border:1px solid var(--line);border-radius:12px;padding:2px 12px;
            cursor:pointer;margin-right:6px}
  .filter a.act{background:var(--accent);color:#fff;border-color:var(--accent)}
  tr.rest{display:none}
  body.show-rest tr.rest{display:table-row}
  .banner{background:var(--sb-bg);border:1px solid var(--sb-line);border-radius:10px;
          padding:10px 14px;font-size:13px;color:#7a5b17;margin-bottom:14px}
  .empty{padding:14px;color:var(--sub);font-size:13px}
"""


def load_picks(pool_path=None):
    """读 todays_picks.json 顶层（容错：缺失/坏 JSON → 空字典）。"""
    try:
        with open(pool_path or pool_watch.POOL_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _fmt_px(x):
    return "%.2f" % float(x)


def _rest_rows(picks, watch_codes):
    """未纳监控行：池内但不在监控集（T3/无买入源）——诚实折叠展示。"""
    rows = []
    for st in picks.get("stocks") or []:
        if st.get("code") in watch_codes:
            continue
        rows.append(
            {
                "code": st.get("code") or "?",
                "name": st.get("name") or st.get("code") or "?",
                "tier": st.get("tier") or "-",
                "close": st.get("close"),
                "pct": st.get("pct_chg"),
            }
        )
    return rows


def render_html(picks, events, now=None):
    """纯函数渲染：todays_picks dict + 触发事件 list → HTML 字符串。"""
    now = now or datetime.now(CST)
    today_s = now.strftime("%Y-%m-%d")
    # 监控集：从内存 picks 复算，口径逐字段委托 pool_watch（TIER_ALLOWED
    # + pick_entry 买入类基准），与触发器 load_watchlist 同口径零漂移
    watch = []
    for st in picks.get("stocks") or []:
        picked = pool_watch.pick_entry(st.get("sources"))
        if st.get("tier") not in pool_watch.TIER_ALLOWED or not picked:
            continue
        entry_px, reg, dt = picked
        watch.append(
            {
                "code": st["code"],
                "name": st.get("name") or st["code"],
                "tier": st.get("tier"),
                "entry_px": float(entry_px),
                "reg": reg,
                "dt": dt,
                "n_hit": st.get("n_hit"),
                "close": st.get("close"),
                "pct": st.get("pct_chg"),
            }
        )
    watch_codes = {w["code"] for w in watch}
    rest = _rest_rows(picks, watch_codes)

    ev_today = [
        e for e in events if str(e.get("triggered_at") or "").startswith(today_s)
    ]
    hit_codes = {e.get("stock") for e in ev_today}
    events_sorted = sorted(
        events, key=lambda e: str(e.get("triggered_at") or ""), reverse=True
    )[:MAX_EVENT_ROWS]

    # —— 状态卡 ——
    picks_bad = not picks
    cards = """
    <div class="cards">
      <div class="card"><div class="k">监控集（选票池 T1/T2·买入源）</div>
        <div class="v">%d <span style="font-size:12px;color:var(--sub)">只</span></div>
        <div class="note">池总量 %s · 未纳监控 %d 只（折叠区）</div></div>
      <div class="card"><div class="k">今日触发事件</div>
        <div class="v" style="color:var(--accent)">%d <span style="font-size:12px;color:var(--sub)">条</span></div>
        <div class="note">累计落池 %d 条（append-only）</div></div>
      <div class="card"><div class="k">触发器状态</div>
        <div class="v" style="font-size:15px;color:var(--warn)">未挂计划任务</div>
        <div class="note">系统级变更，须五要素审批后挂载（与页面解耦）</div></div>
      <div class="card" style="background:var(--sb-bg);border-color:var(--sb-line)">
        <div class="k">影子期 <span class="badge b-shadow">只报不执行</span></div>
        <div class="v">%s <span style="font-size:12px;color:var(--sub)">止</span></div>
        <div class="note">%s 起 14 天 · 期间信号不入 combo</div></div>
    </div>""" % (
        len(watch),
        picks.get("counts", {}).get("total", "-") if picks else "-",
        len(rest),
        len(ev_today),
        len(events),
        SHADOW_END,
        SHADOW_START,
    )

    # —— 影子期红线（常驻） ——
    banner = (
        '<div class="banner">⛔ <b>影子期红线（常驻）</b>：触发信号'
        "只落 signal_pool.jsonl（append-only），<b>不进 combo 账本、"
        "不产生真实委托</b>；解锁须 D7 复核+老田签准。回踩线="
        "<b>entry_px×%s</b> · 突破线=<b>entry_px×%s</b> · 30min 轮询 · "
        "4h 冷却 · reg=down 突破只标记不报买。姊妹页："
        '<a href="/bt-signals">/bt-signals 观察名单信号</a></div>'
        % (_fmt_px(pool_watch.PULL_FACTOR), _fmt_px(pool_watch.BREAK_FACTOR))
    )

    # —— 事件流 ——
    if events_sorted:
        rows = []
        for e in events_sorted:
            ev = e.get("evidence") or {}
            d = e.get("direction")
            delta = None
            try:
                delta = (
                    float(ev.get("trigger_price")) / float(ev.get("entry_px")) - 1
                ) * 100
            except Exception:
                delta = None
            rfo = (
                ' <span class="pill p-reg">reg_flag_only·只标记</span>'
                if e.get("reg_flag_only")
                else ""
            )
            rows.append(
                '<tr><td>%s</td><td class="sid">%s</td>'
                '<td>%s %s</td><td><span class="pill p-buy">%s·%s</span>%s</td>'
                '<td>%s</td><td>%s</td><td class="%s">%s%%</td>'
                '<td>%s</td><td><span class="pill p-watch">影子期·仅落池</span></td></tr>'
                % (
                    str(e.get("triggered_at") or "").replace("T", " "),
                    e.get("signal_id") or "-",
                    e.get("stock") or "-",
                    e.get("name") or "",
                    DIR_LABEL.get(d, d or "-"),
                    e.get("action") or "BUY",
                    rfo,
                    _fmt_px(ev.get("trigger_price", 0)),
                    _fmt_px(ev.get("entry_px", 0)),
                    "up" if (delta or 0) > 0 else "down",
                    (("%+.1f") % delta) if delta is not None else "-",
                    ("reg=%s" % ev.get("reg")) if ev.get("reg") else "-",
                )
            )
        events_html = (
            "<table><tr><th>时间</th><th>signal_id</th><th>标的</th>"
            "<th>方向</th><th>触发价</th><th>参考买</th><th>偏离</th>"
            "<th>reg</th><th>处置</th></tr>%s</table>" % "".join(rows)
        )
    else:
        events_html = (
            '<div class="empty">暂无触发事件（触发器未挂计划任务，'
            "或影子期静默；挂载后事件在此实时滚动）</div>"
        )
    events_panel = (
        '<div class="panel"><h2>⚡ 触发事件流'
        '<span class="hint">张郃产生 · signal_id 幂等轮次 · '
        "最近 %d 条</span></h2>%s</div>" % (MAX_EVENT_ROWS, events_html)
    )

    # —— 监控集表 ——
    if watch:
        wrows = []
        for w in watch:
            pull = float(w["entry_px"]) * pool_watch.PULL_FACTOR
            brk = float(w["entry_px"]) * pool_watch.BREAK_FACTOR
            pct = w.get("pct")
            hit = w["code"] in hit_codes
            wrows.append(
                '<tr><td>%s %s</td><td class="%s">%s</td><td>%s</td>'
                '<td>%s</td><td class="down">%s</td><td class="up">%s</td>'
                "<td>%s</td><td>%s</td><td>%s</td></tr>"
                % (
                    w["code"],
                    w["name"],
                    "up" if (pct or 0) > 0 else "down",
                    (("%+.2f%%") % pct) if pct is not None else "-",
                    w["tier"],
                    _fmt_px(w["entry_px"]),
                    _fmt_px(pull),
                    _fmt_px(brk),
                    (
                        '<span class="pill p-reg">down</span>'
                        if w.get("reg") == "down"
                        else (w.get("reg") or "-")
                    ),
                    w.get("dt") or "-",
                    (
                        '<span class="pill p-hit">今日已触发</span>'
                        if hit
                        else '<span class="pill p-watch">监控中</span>'
                    ),
                )
            )
        watch_html = (
            "<table><tr><th>标的</th><th>现价</th><th>tier</th>"
            "<th>参考买 entry_px</th><th>回踩线</th><th>突破线</th>"
            "<th>reg</th><th>信号日</th><th>状态</th></tr>%s</table>" % "".join(wrows)
        )
    else:
        watch_html = '<div class="empty">监控集为空——%s</div>' % (
            "选票池数据不可用（todays_picks.json 缺失或坏 JSON）"
            if picks_bad
            else "池内无 T1/T2 且含买入源的票"
        )

    # —— 未纳监控折叠区 ——
    if rest:
        rrows = []
        for r in rest[:80]:
            pct = r.get("pct")
            rrows.append(
                '<tr class="rest"><td>%s %s</td><td>%s</td>'
                '<td class="%s">%s</td><td>%s</td>'
                '<td><span class="pill p-rest">未纳监控</span></td></tr>'
                % (
                    r["code"],
                    r["name"],
                    _fmt_px(r["close"]) if r.get("close") else "-",
                    "up" if (pct or 0) > 0 else "down",
                    (("%+.2f%%") % pct) if pct is not None else "-",
                    r["tier"],
                )
            )
        rest_html = (
            '<div class="filter">'
            '<a id="tg" onclick="var b=document.body;'
            "b.classList.toggle('show-rest');"
            "this.classList.toggle('act');"
            "this.textContent=b.classList.contains('show-rest')"
            '?「收起」:「展开未纳监控」">展开未纳监控 %d 只</a>'
            '<span style="font-size:11px;color:var(--sub)">'
            "T3/无买入源票不属触发器监控对象，仅列示</span></div>"
            "<table><tr><th>标的</th><th>现价</th><th>涨跌</th>"
            "<th>tier</th><th>状态</th></tr>%s</table>" % (len(rest), "".join(rrows))
        )
    else:
        rest_html = ""

    picks_panel = (
        '<div class="panel"><h2>📋 选票池触发监控'
        '<span class="hint">监控集口径同触发器（pool_watch.'
        "load_watchlist）· 池印记 scan_run_date=%s</span></h2>"
        "%s%s</div>" % (picks.get("scan_run_date") or "-", watch_html, rest_html)
    )

    return (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>许褚营 · 双池触发监控台</title><style>%s</style></head>"
        '<body><div class="wrap">'
        "<h1>🛡️ 许褚营 · 双池触发监控台"
        '<span class="badge b-off">生产页 · WO-078</span>'
        '<span class="badge b-shadow">影子期</span></h1>'
        '<div class="sub">许褚（盯盘域老大）→ 张郃（池内票触发执行体）｜ '
        "数据源：todays_picks.json（选票池权威实体）× signal_pool.jsonl"
        "（触发事件账）｜ 生成于 %s</div>"
        "%s%s%s%s"
        "</div></body></html>"
        % (
            _CSS,
            now.strftime("%Y-%m-%d %H:%M:%S"),
            cards,
            banner,
            events_panel,
            picks_panel,
        )
    )


def render_default(now=None):
    """读真实产物渲染（portal 路由用）：纯读，失败由调用方降级。"""
    picks = load_picks()
    events = signal_pool.read_events()
    return render_html(picks, events, now=now)


def write_html(now=None, pool_path=None, pool_jsonl=None, out_path=None):
    """落盘生成报告页（CLI 用），返回写入路径。"""
    picks = load_picks(pool_path) if pool_path else load_picks()
    events = signal_pool.read_events(path=pool_jsonl)
    html = render_html(picks, events, now=now)
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
