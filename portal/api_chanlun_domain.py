# -*- coding: utf-8 -*-
"""缠论新域 4 页（I1b §3.3）· 复用智测 L1 tokens + L2 nav + L3 sec 范式。

边界（施工单 §6）
────────────────
· 不做动作位（I3）、不做 today.json/radar.json 聚合接口（I2）、不迁老 8 页、
  不修 /chanlun-learn-report/ 404（I4）、不引入 SPA / Node 构建链。
· 页面只读现有产物，不写任何文件、不接受 qs 输入（qs 保留给未来，但当前不用）。

判定纪律（I1a 步 3 的验证闸门，老田 2026-11-15 口径）
──────────────────────────────────────────────────────
「写不出一句话说明数据链路缺的是判定，不是页面」——本模块严格照此执行：
  · /chanlun/  与 /chanlun/radar **有判定**：直接复用 I1a 步 3 的纯函数
    `chanlun_pool_conclusion()` / `brief_conclusion()`，结论里每个数字都与页面
    明细同源（不前端自算、不复制数字）。
  · /chanlun/action 与 /chanlun/learn **只有状态数据、无判定输出**：
    chanlun_paper.json 是影子账本流水、chanlun_learn/*.jsonl 是声明与后验记录，
    两者都不产出「今天该做什么」。这两页**不调用 conclusion()**，改用
    `sec("数据状态与边界")` 如实写明「本页无管线判定，不产出结论」。
    这是如实停在缺判定处，不是 UI 没做完。

asof 纪律（施工单 §1.1）：数据日一律来自数据自身声明的日期字段
（detect_date / ts / found_day / declared_at / verify_dt），绝不用文件 mtime。
生成时刻单独显示，两者不混。
"""
import html as _h
import json
import os
import re
import sqlite3
import time

import ui_parts
from api_common import STATION, QBT_DIR, pill_nav

_KEY_BY_PATH = {
    "/chanlun/": "cl-today",
    "/chanlun/radar": "cl-radar",
    "/chanlun/action": "cl-action",
    "/chanlun/learn": "cl-learn",
}

_TITLES = {
    "cl-today": "📌 今日结论",
    "cl-radar": "🛟 预警池",
    "cl-action": "🧾 影子仓",
    "cl-learn": "📖 判据账本",
}

_POOL = os.path.join(QBT_DIR, "data", "chanlun_pool.jsonl")
_PAPER = os.path.join(QBT_DIR, "data", "chanlun_paper.json")
_NEWS_DB = os.path.join(STATION, "news_collector", "news.db")
_LEARN_DIR = os.path.join(QBT_DIR, "data", "chanlun_learn")
_LEARN_FILES = ("ledger.jsonl", "czsc_ledger.jsonl", "verify.jsonl")


# ── 数据读取（只读；异常由调用方降级为「不编造」） ──────────────────
def _load_pool():
    rows = []
    with open(_POOL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except (ValueError, TypeError):
                continue
    return rows


def _load_paper():
    try:
        with open(_PAPER, encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _load_brief_rows():
    db = sqlite3.connect(_NEWS_DB)
    try:
        out = {}
        for tbl in ("ai_briefs", "ai_reviews"):
            try:
                out[tbl] = db.execute(
                    "SELECT ts, model, ok, brief FROM %s "
                    "ORDER BY ts DESC LIMIT 1" % tbl).fetchone()
            except sqlite3.Error:
                out[tbl] = None
        return out.get("ai_briefs"), out.get("ai_reviews")
    finally:
        db.close()


def _day_of(v):
    """把数据自身声明的日期归一成 YYYY-MM-DD；取不到返回 None（不猜）。"""
    s = str(v or "").strip()[:10]
    if len(s) == 8 and s.isdigit():
        return s[:4] + "-" + s[4:6] + "-" + s[6:8]
    if len(s) == 10 and re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    return None


def _load_learn():
    """账本状态：每个文件行数 + 数据自身声明的最新日期（不用 mtime）。"""
    out = []
    for name in _LEARN_FILES:
        rec = {"file": name, "rows": 0, "day": None}
        try:
            with open(os.path.join(_LEARN_DIR, name), encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    rec["rows"] += 1
                    try:
                        d = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    for k in ("declared_at", "found_day", "verify_dt", "dt"):
                        day = _day_of(d.get(k))
                        if day and (rec["day"] is None or day > rec["day"]):
                            rec["day"] = day
        except OSError:
            rec["missing"] = True
        out.append(rec)
    return out


# ── 页面片段 ────────────────────────────────────────────────────
def _e(v):
    return _h.escape(str(v if v is not None else "—"))


def _gen_note():
    return '<span class="ui-gen">生成 %s</span>' % _e(
        time.strftime("%Y-%m-%d %H:%M"))


def _links_bar():
    items = (("/chanlun/", "📌 今日结论"), ("/chanlun/radar", "🛟 预警池"),
             ("/chanlun/action", "🧾 影子仓"), ("/chanlun/learn", "📖 判据账本"))
    return ui_parts.sec(
        "域内入口", body='<div style="display:flex;flex-wrap:wrap;gap:10px">'
        + "".join('<a href="%s" style="color:#58a6ff;text-decoration:none;'
                  'padding:8px 12px;border:1px solid #30363d;'
                  'border-radius:8px">%s</a>' % (href, label)
                  for href, label in items)
        + "</div>")


def _source_note(lines):
    return ui_parts.sec(
        "数字出处", body='<div style="font-size:12px;line-height:2;'
        'color:#8b949e">' + "<br>".join(_e(x) for x in lines) + "</div>")


def _table(headers, rows):
    th = "".join("<th style='text-align:left;padding:6px 10px;"
                 "border-bottom:1px solid #30363d'>%s</th>" % _e(x)
                 for x in headers)
    trs = "".join(
        "<tr>" + "".join("<td style='padding:6px 10px;border-bottom:"
                         "1px solid #21262d'>%s</td>" % _e(c) for c in r)
        + "</tr>" for r in rows)
    # 注意：字符串里有 CSS 的 100%，用 % 格式化时必须写成 100%%，
    # 否则 ValueError: unsupported format character ';'（首次落地实测踩中）。
    return ("<table style='width:100%%;border-collapse:collapse;"
            "font-size:12px'><thead><tr>%s</tr></thead><tbody>%s</tbody></table>"
            % (th, trs))


# ── 四页渲染 ────────────────────────────────────────────────────
def _page_today():
    try:
        rows = _load_pool()
        paper = _load_paper()
        brief, review = _load_brief_rows()
    except Exception:
        return _degraded("读取缠论池 / 晨报产物失败")
    last = max((r.get("detect_date") or "" for r in rows), default="")
    day = [r for r in rows if r.get("detect_date") == last]
    strong = [r for r in day
              if r.get("type") == "1卖" and r.get("tbe") is True]
    shield = sum(1 for r in day if r.get("tradable") is False)
    # 判定直接复用 I1a 步 3 的纯函数，不在这里重写口径、不前端自算
    from api_chanlun_pages import chanlun_pool_conclusion
    from api_backtest_pages_a2 import brief_conclusion
    parts = [chanlun_pool_conclusion(last, rows, paper, strong),
             brief_conclusion(brief, review)]
    kpis = ui_parts.sec(
        "今日水位", note=_gen_note(),
        body='<div class="ui-kpi-row">'
        + ui_parts.kpi("当日入池", len(day))
        + ui_parts.kpi("强预警 1卖·趋✓", len(strong))
        + ui_parts.kpi("涨停屏蔽", shield)
        + ui_parts.kpi("影子持仓", len(paper.get("open") or []))
        + "</div>")
    src = [
        "chanlun_pool.jsonl 共 %d 行；最新检出日 %s；当日 %d 行"
        % (len(rows), last or "未知", len(day)),
        "强预警 = 当日 type=1卖 且 tbe=true 共 %d 条（下方逐条）" % len(strong),
        "涨停屏蔽 = 当日 tradable=false 共 %d 条" % shield,
        "chanlun_paper.json：open %d 笔 / closed %d 笔"
        % (len(paper.get("open") or []), len(paper.get("closed") or [])),
        "晨报 / 复盘结论直接引用 news.db 的【一句话总览】/【大盘复盘】判定句",
    ]
    detail = _table(("代码", "名称", "类型", "趋✓", "可成交", "检出日"),
                    [(r.get("code"), r.get("name"), r.get("type"),
                      r.get("tbe"), r.get("tradable"), r.get("detect_date"))
                     for r in strong[:10]])
    return "".join(parts) + kpis + ui_parts.sec("强预警明细（前 10 条）",
                                                body=detail) + _source_note(src) + _links_bar()


def _page_radar():
    try:
        rows = _load_pool()
        paper = _load_paper()
    except Exception:
        return _degraded("读取缠论池产物失败")
    last = max((r.get("detect_date") or "" for r in rows), default="")
    day = [r for r in rows if r.get("detect_date") == last]
    strong = [r for r in day
              if r.get("type") == "1卖" and r.get("tbe") is True]
    from api_chanlun_pages import chanlun_pool_conclusion
    detail = _table(("代码", "名称", "类型", "趋✓", "可成交", "检出日"),
                    [(r.get("code"), r.get("name"), r.get("type"),
                      r.get("tbe"), r.get("tradable"), r.get("detect_date"))
                     for r in strong[:20]])
    types = {}
    for r in day:
        k = str(r.get("type") or "—")
        types[k] = types.get(k, 0) + 1
    dist = _table(("类型", "条数"),
                  sorted(types.items(), key=lambda x: -x[1])[:12])
    src = ["chanlun_pool.jsonl 共 %d 行；最新检出日 %s；当日 %d 行"
           % (len(rows), last or "未知", len(day)),
           "强预警 %d 条（上方结论与下方明细同一批 rows）" % len(strong),
           "模拟持仓 %d / 已平 %d（chanlun_paper.json）"
           % (len(paper.get("open") or []), len(paper.get("closed") or []))]
    return (chanlun_pool_conclusion(last, rows, paper, strong)
            + ui_parts.sec("强预警明细（最多 20 条）", body=detail)
            + ui_parts.sec("当日类型分布", body=dist)
            + _source_note(src)
            + ui_parts.sec("完整老页", body='<a href="/bt-chanlun-pool" '
                           'style="color:#58a6ff">/bt-chanlun-pool（含全部字段与图）</a>')
            + _links_bar())


def _page_action():
    """⚠ 无判定：影子账本只有流水，不产出「今天该做什么」。不调用 conclusion()。"""
    try:
        paper = _load_paper()
        learn = _load_learn()
    except Exception:
        return _degraded("读取影子账本产物失败")
    opens = paper.get("open") or []
    closed = paper.get("closed") or []
    recent = list(closed[-10:]) or list(opens[-10:])
    rows = []
    for r in recent:
        if isinstance(r, dict):
            rows.append((r.get("code"), r.get("name"), r.get("type"),
                         r.get("buy_date") or r.get("open_date"),
                         r.get("sell_date") or r.get("close_date"),
                         r.get("pnl") or r.get("ret")))
        else:
            rows.append((str(r)[:60], "—", "—", "—", "—", "—"))
    state = (
        '<div class="ui-conc-text">影子仓当前 <b>%d</b> 笔持仓 / '
        '<b>%d</b> 笔已平；<b>本页无管线判定，不产出结论</b>——'
        'chanlun_paper.json 是流水账本，不含「今天该做什么」的判定输出。'
        '动作位（观察/影子仓/忽略记因）属 I3，需先有动作流水文件与口径。'
        % (len(opens), len(closed)))
    return (ui_parts.sec("数据状态与边界",
                         note=ui_parts.asof_badge(None) + _gen_note(),
                         body=state)
            + ui_parts.sec("最近 10 笔", body=_table(
                ("代码", "名称", "类型", "开仓", "平仓", "收益"), rows))
            + _source_note([
                "chanlun_paper.json：open %d 笔 / closed %d 笔" % (len(opens), len(closed)),
                "账本数据文件 %d 个：%s" % (len(learn), ", ".join(
                    x["file"] for x in learn)),
                "asof 显示「数据日未知」：账本流水未声明可比对的数据日，按 §1.1 不用 mtime 顶替",
            ])
            + ui_parts.sec("完整老页", body='<a href="/paper" '
                           'style="color:#58a6ff">/paper（模拟仓）</a> · '
                           '<a href="/bt-chanlun-pool" style="color:#58a6ff">'
                           '/bt-chanlun-pool（影子池链）</a>')
            + _links_bar())


def _page_learn():
    """⚠ 无判定：判据账本有声明与后验记录，但学习回路报告页未接入（I4）。"""
    try:
        learn = _load_learn()
    except Exception:
        return _degraded("读取判据账本产物失败")
    days = [x["day"] for x in learn if x.get("day")]
    day = max(days) if days else None
    n_led = next((x["rows"] for x in learn if x["file"] == "ledger.jsonl"), 0)
    n_ver = next((x["rows"] for x in learn if x["file"] == "verify.jsonl"), 0)
    state = (
        '<div class="ui-conc-text">判据账本共 <b>%d</b> 条声明 / '
        '<b>%d</b> 条后验记录，最新数据日 <b>%s</b>；'
        '<b>本页无管线判定，不产出结论</b>——账本记录的是历史声明与后验收益，'
        '不是今日预警判定；学习回路报告页未接入（I4，施工单 §6 明确不做）。'
        % (n_led, n_ver, day or "未知"))
    return (ui_parts.sec("数据状态与边界",
                         note=ui_parts.asof_badge(day) + _gen_note(),
                         body=state)
            + ui_parts.sec("账本明细", body=_table(
                ("文件", "行数", "数据自身声明的最新日", "缺失"),
                [(x["file"], x["rows"], x["day"] or "未声明",
                  "是" if x.get("missing") else "否") for x in learn]))
            + _source_note([
                "数据日来自账本行内字段 declared_at / found_day / verify_dt / dt（不用 mtime）",
                "报告页 /chanlun-learn-report/ 现状 404 属 I4，本域不修、不假装已接入",
            ])
            + ui_parts.sec("现有产物", body='<a href="/chanlun-learn-report/'
                           'chanlun-learn-report.html" style="color:#58a6ff">'
                           '缠论学习环运行报告 · L1-L4（静态产物）</a>')
            + _links_bar())


def _degraded(reason):
    """读盘失败：200 + 明确「页面不编造结论」，不编日期不编数字。"""
    return (ui_parts.sec("数据状态与边界",
                         note=ui_parts.asof_badge(None),
                         body='<div class="ui-conc-text">%s；'
                              '<b>本页不编造结论</b>。</div>' % _e(reason))
            + _links_bar())


_RENDER = {"cl-today": _page_today, "cl-radar": _page_radar,
           "cl-action": _page_action, "cl-learn": _page_learn}


def render_page(key):
    """返回完整 HTML bytes。key 不在表内时抛 KeyError（由 handle_get 兜 404）。"""
    title = _TITLES[key]
    main = _RENDER[key]()
    # 标题用拼接而非 %s：这段是纯 + 拼接，写 %s 会原样输出（首次落地实测踩中）。
    doc = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
           "<meta name='viewport' content='width=device-width,initial-scale=1'>"
           "<title>缠论 · " + _e(title) + "</title>"
           "<link rel='stylesheet' href='/static/tokens.css'>"
           + ui_parts.style_block() + "</head>"
           "<body style='background:#0d1117;color:#c9d1d9;"
           "font-family:Microsoft YaHei,Arial;margin:0;padding:20px'>"
           "<div style='max-width:980px;margin:0 auto'>"
           "<h2 style='font-size:18px'>🧭 缠论 · " + _e(title) + "</h2>"
           + pill_nav(key) + main
           + "<div style='color:#58606a;font-size:11px;margin-top:22px'>"
             "缠论新域 · 举证不裁决 · 影子观察 · 不构成投资建议</div>"
           "</div></body></html>")
    return doc.encode("utf-8")


def _send(handler, code, body, ctype="text/html; charset=utf-8"):
    handler.send_response(code)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def handle_get(handler, path, qs=""):
    """巨石转发入口。只认 4 条精确路由 + /chanlun 的 302；其余 404。

    qs 当前不使用（不接受用户输入，避免把查询串变成新的数据口径）。
    """
    if path == "/chanlun":
        handler.send_response(302)
        handler.send_header("Location", "/chanlun/")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return
    key = _KEY_BY_PATH.get(path)
    if key is None:
        _send(handler, 404, ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                             "</head><body>缠论新域无此页面：<code>%s</code>"
                             "</body></html>" % _e(path)).encode("utf-8"))
        return
    try:
        body = render_page(key)
    except Exception:
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'></head>"
                "<body>" + _degraded("页面渲染异常") + "</body></html>"
                ).encode("utf-8")
    _send(handler, 200, body)
