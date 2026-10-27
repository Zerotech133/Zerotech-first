# -*- coding: utf-8 -*-
"""stock_card.py — 全站统一股票详情卡（K-STOCK-051，046 §5.1 一致性规范落地）

五区固定（唯一样式，全站所有股票代码统一链接到此）：
  ① 行情区   实时快照(news_collector/quotes) + 盘后K线事件(KG HAS_CANDLE) + 涨跌停(HAS_LIMIT)
  ② 信号区   KG 缠论结构(HAS_STRUCTURE, conf+hm_tag) + 模拟仓该票挂单/止损
  ③ 账本区   paper.db 多账本持仓并列（qty/avg_cost/last/pnl/stop_price）
  ④ 事件区   KG 驱动事件(DRIVE) + 榜后差额(TOPGAP) + 十大流通股东 + 游资(OCCUPIES)
  ⑤ 新闻区   news.db news.stocks 关联新闻（ts_code 精确匹配）
  ⑥ 时空区   五日演化天轴（K-STOCK-055 L2）：逐日迷你卡（涨跌/信号/盘后/涨停/
             榜差额/游资/账本动作）+ 演化结论（区间累计/信号日数/账本跟进）

设计纪律：
- 版面只用 /static/tokens.css 令牌（var(--xxx)），零硬编码色值
- 每区独立 try 降级：单区数据源挂掉不拖垮整卡（全站投影纪律）
- 代码格式统一：对外一律 ts_code（600853.SH）；内部转换 quotes 前缀码(sh603580)
- 只读：paper.db / news.db 均以 mode=ro 打开
"""

import html
import os
import sqlite3
from datetime import datetime
import logging

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEWS_DB = os.path.join(STATION, "news_collector", "news.db")
PAPER_DB = os.path.join(STATION, "k_stock", "data", "paper.db")
QBT_DIR = os.path.join(STATION, "quant_backtest")


def _esc(v):
    return html.escape(str(v if v is not None else "—"))


def _pct_cls(v):
    """中式配色：涨红跌绿（--color-up/--color-dn）。"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    return "up" if v > 0 else ("dn" if v < 0 else "")


def _fmt_pct(v):
    try:
        v = float(v)
        return f"{v:+.2f}%"
    except (TypeError, ValueError):
        return "—"


_NAT = {
    "youzi": "游资",
    "quant": "量化",
    "org": "机构",
    "retail": "散户",
    "other": "普通",
}


def _amt(v):
    """金额（元）→ 亿/万 可读串（对齐 KG 事件页口径）。"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    if abs(v) >= 1e8:
        return f"{v / 1e8:+.2f}亿" if v < 0 else f"{v / 1e8:.2f}亿"
    return f"{v / 1e4:+.0f}万" if v < 0 else f"{v / 1e4:.0f}万"


def _pc_ratio(v, signed=True):
    """比率 0.012 → +1.20%（ret1/gr 存比率，_fmt_pct 直用会差 100 倍）。"""
    try:
        v = float(v) * 100
        return f"{v:+.2f}%" if signed else f"{v:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_vol(v):
    """volume（手）→ 万手。"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{v / 1e4:.1f}万手" if v >= 1e4 else f"{v:.0f}手"


def _prefix_code(ts_code):
    """600853.SH -> sh600853（quotes/paper 前缀码）。"""
    try:
        num, suf = ts_code.split(".")
        return {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(suf.upper(), "sh") + num
    except ValueError:
        return ts_code


def _num6(ts_code):
    return ts_code.split(".")[0] if "." in ts_code else ts_code


def normalize_code(raw):
    """入口归一：接受 600853.SH / 600853 / sh600853 → 统一 ts_code。
    6 位纯数字按主板规则猜市场：6=SH，0/3=SZ，4/8=BJ。"""
    raw = (raw or "").strip()
    if "." in raw:
        return raw.upper()
    low = raw.lower()
    if low[:2] in ("sh", "sz", "bj") and len(low) >= 8:
        return f"{low[2:8]}.{low[:2].upper()}"
    if len(raw) == 6 and raw.isdigit():
        # K-STOCK-400：920 段=北交所（同 quote_snapshot.normalize_code 根修）
        suf = (
            "SH"
            if raw[0] == "6"
            else ("BJ" if raw[0] in "48" or raw[:2] == "92" else "SZ")
        )
        return f"{raw}.{suf}"
    return raw.upper()


def _ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


# ---------------- ① 行情区 ----------------


def _sec_quote(ts_code):
    out = {"quote": None, "candles": [], "limits": []}
    try:
        con = _ro(NEWS_DB)
        row = con.execute(
            "SELECT name,ts,price,pct,open,high,low,vol,amount FROM quotes "
            "WHERE code=? ORDER BY created_at DESC LIMIT 1",
            (_prefix_code(ts_code),),
        ).fetchone()
        if row:
            out["quote"] = dict(
                zip(
                    [
                        "name",
                        "ts",
                        "price",
                        "pct",
                        "open",
                        "high",
                        "low",
                        "vol",
                        "amount",
                    ],
                    row,
                )
            )
        con.close()
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/stock_card.py", exc)
    try:
        if QBT_DIR not in __import__("sys").path:
            __import__("sys").path.insert(0, QBT_DIR)
        import kg_portal

        d = kg_portal.stock(ts_code, 120)
        if d.get("ok"):
            out["candles"] = (d.get("candles") or [])[:10]
            out["limits"] = (d.get("limits") or [])[:8]
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/stock_card.py", exc)
    return out


def _render_quote(ts_code, q, candles, limits, vr=None):
    h = ['<section class="card"><h3>① 行情</h3>']
    if q:
        h.append(
            f'<div class="qline"><span class="qname">{_esc(q["name"])}</span>'
            f'<span class="qprice {_pct_cls(q["pct"])}">{_esc(q["price"])}</span>'
            f'<span class="qpct {_pct_cls(q["pct"])}">{_fmt_pct(q["pct"])}</span>'
            f'<span class="qt">快照 {_esc((q["ts"] or "")[:16])}</span></div>'
            f'<div class="qgrid">'
            f"<span>开 {_esc(q['open'])}</span><span>高 {_esc(q['high'])}</span>"
            f"<span>低 {_esc(q['low'])}</span><span>量 {_esc(q['vol'])}</span>"
            f"<span>额 {_esc(q['amount'])}</span>"
            + (f"<span>量能 {vr:.1f}×</span>" if vr else "")
            + "</div>"
        )
    else:
        h.append('<div class="empty">quotes 暂无该票快照</div>')
    if limits:
        h.append(
            '<div class="mini-h">涨跌停（近 8 次）</div><ul class="mini">'
            + "".join(
                f'<li>{_esc(r["date"])} <b class="{_pct_cls(r.get("pct"))}">'
                f"{_fmt_pct(r.get('pct'))}</b> 连{_esc(r.get('lt'))} "
                f"封单{_esc(r.get('lamt'))} 炸板{_esc(r.get('ot'))}</li>"
                for r in limits
            )
            + "</ul>"
        )
    if candles:
        h.append(
            '<div class="mini-h">盘后 K 线事件（近 10）</div><ul class="mini">'
            + "".join(
                f"<li>{_esc(r['date'])} {_esc(r.get('type'))} "
                f'<span class="{_pct_cls(r.get("pct"))}">{_fmt_pct(r.get("pct"))}</span>'
                + (f" {_esc(r.get('tech'))}" if r.get("tech") else "")
                + (f" [{_esc(r.get('reg'))}]" if r.get("reg") else "")
                + "</li>"
                for r in candles
            )
            + "</ul>"
        )
    h.append(
        f'<div class="deep">深度：'
        f'<a href="/bt/stock?c={_esc(_num6(ts_code))}">量化全景 →</a>　'
        f'<a href="/kg?c={_esc(ts_code)}">图谱视角 →</a>　'
        f'<a href="/sa/start?q={_esc(_num6(ts_code))}">个股分析 →</a></div>'
    )
    h.append("</section>")
    return "".join(h)


# ---------------- ② 信号区 ----------------


def _render_basic(ts_code):
    """② 基本面 · 基本情况（K-STOCK-384）：复用 api_screen Handler 的
    _stock_fundamental（估值/盈利/成长/健康四组·classmethod 类直调，
    382 装饰器修复后才可用），四组栅格布局，每区 try 降级。"""
    h = ['<section class="card"><h3>② 基本面 · 基本情况</h3>']
    try:
        from api_screen import Handler as _SH

        rows, note = _SH._stock_fundamental(ts_code)
    except Exception:  # noqa: BLE001
        rows, note = [], "基本面数据暂不可用"
    if not rows:
        h.append(f'<div class="empty">{_esc(note or "暂无基本面数据")}</div>')
    else:
        groups = {}
        for g, k, v in rows:
            groups.setdefault(g, []).append((k, v))
        parts = []
        for g in ("估值", "盈利", "成长", "健康"):
            items = groups.get(g)
            if not items:
                continue
            cells = "".join(
                f'<div class="bsi"><span class="bsk">{_esc(k)}</span>'
                f'<span class="bsv">{_esc(v)}</span></div>'
                for k, v in items
            )
            parts.append(
                f'<div class="bsg"><div class="bsgh">{_esc(g)}</div>{cells}</div>'
            )
        h.append('<div class="bsgrid">' + "".join(parts) + "</div>")
        if note:
            h.append(f'<div class="t" style="margin-top:8px">{_esc(note)}</div>')
    h.append("</section>")
    return "".join(h)


def _render_signals(kg_data, paper_orders):
    h = ['<section class="card"><h3>③ 信号</h3>']
    st = kg_data.get("structures") or []
    if st:
        h.append(
            '<div class="mini-h">缠论结构（KG，近 60）</div>'
            '<table class="tbl"><tr><th>日期</th><th>类型</th><th>conf</th>'
            "<th>游资标签</th><th>互证</th></tr>"
            + "".join(
                f"<tr><td>{_esc(r.get('date'))}</td><td>{_esc(r.get('type'))}</td>"
                f"<td>{_esc(round(r['conf'], 2) if r.get('conf') is not None else '—')}</td>"
                f"<td>{_esc(r.get('hm_tag'))}</td><td>{_esc(r.get('cc'))}</td></tr>"
                for r in st[:15]
            )
            + "</table>"
        )
    else:
        h.append('<div class="empty">KG 暂无结构信号</div>')
    if paper_orders:
        h.append(
            '<div class="mini-h">模拟仓挂单/成交（该票）</div><ul class="mini">'
            + "".join(
                f"<li>{_esc(r[0])} {_esc(r[1])} {_esc(r[2])} 股 "
                f'@{_esc(r[3])} <span class="t">{_esc(r[4])}</span></li>'
                for r in paper_orders[:10]
            )
            + "</ul>"
        )
    h.append("</section>")
    return "".join(h)


# ---------------- ③ 账本区 ----------------


def _paper_ledger(ts_code):
    """多账本持仓并列：positions(现持) + 最近快照 pnl。"""
    out = {"pos": [], "snap": [], "err": None}
    n6 = _num6(ts_code)
    try:
        con = _ro(PAPER_DB)
        out["pos"] = con.execute(
            "SELECT account_id,qty,avg_cost,strategy,stop_price,updated_at "
            "FROM positions WHERE stock LIKE ? ORDER BY account_id",
            (f"%{n6}%",),
        ).fetchall()
        latest = con.execute("SELECT max(date) FROM positions_snapshot").fetchone()[0]
        if latest:
            out["snap"] = con.execute(
                "SELECT account_id,qty,avg_cost,last,market_value,pnl,strategy "
                "FROM positions_snapshot WHERE date=? AND stock LIKE ? "
                "ORDER BY account_id",
                (latest, f"%{n6}%"),
            ).fetchall()
            out["snap_date"] = latest
        con.close()
    except Exception as e:  # noqa: BLE001
        out["err"] = str(e)
    return out


def _render_ledger(pl):
    h = ['<section class="card"><h3>④ 账本</h3>']
    if pl.get("err"):
        h.append(f'<div class="empty">paper.db 不可读：{_esc(pl["err"])}</div>')
    elif not pl["pos"] and not pl["snap"]:
        h.append('<div class="empty">各模拟盘均未持有该票</div>')
    else:
        h.append(
            '<table class="tbl"><tr><th>账本</th><th>持仓</th><th>成本</th>'
            "<th>现价</th><th>市值</th><th>盈亏</th><th>止损</th><th>策略</th></tr>"
        )
        snaps = {s[0]: s for s in pl["snap"]}
        for aid, qty, cost, strategy, stop, upd in pl["pos"]:
            s = snaps.get(aid)
            last = s[3] if s else None
            pnl = s[5] if s else None
            h.append(
                f"<tr><td><b>{_esc(aid)}</b></td><td>{_esc(qty)}</td>"
                f"<td>{_esc(round(cost, 3) if cost else '—')}</td>"
                f"<td>{_esc(last)}</td><td>{_esc(s[4] if s else '—')}</td>"
                f'<td class="{_pct_cls(pnl)}">{_esc(pnl)}</td>'
                f"<td>{_esc(stop)}</td><td>{_esc(strategy)}</td></tr>"
            )
        h.append("</table>")
        if pl.get("snap_date"):
            h.append(f'<div class="t">快照日 {_esc(pl["snap_date"])}</div>')
    h.append("</section>")
    return "".join(h)


# ---------------- ④ 事件区 ----------------


def _render_events(kg_data):
    h = ['<section class="card"><h3>⑤ 事件</h3>']
    drive = kg_data.get("drive_groups") or []
    if drive:
        h.append(
            '<div class="mini-h">驱动事件组</div><ul class="mini">'
            + "".join(
                f"<li><b>{_esc(r.get('grp'))}</b> ×{_esc(r.get('n'))} "
                f"（最近 {_esc(r.get('last'))}）{_esc('；'.join(r.get('texts') or []))}</li>"
                for r in drive[:8]
            )
            + "</ul>"
        )
    hm = kg_data.get("hm") or kg_data.get("hotmoney") or []
    if hm:
        h.append(
            '<div class="mini-h">游资席位（近 120 日）</div><ul class="mini">'
            + "".join(
                f"<li>{_esc(r.get('hm'))} ×{_esc(r.get('n'))} "
                f'净买 <span class="{_pct_cls(r.get("net"))}">{_esc(r.get("net"))}</span>'
                f"（{_esc(r.get('last'))}）</li>"
                for r in hm[:8]
            )
            + "</ul>"
        )
    holders = kg_data.get("holders") or []
    if holders:
        h.append(
            '<div class="mini-h">十大流通股东（近 3 期）</div><ul class="mini">'
            + "".join(f"<li>{_esc(r)}</li>" for r in holders[:10])
            + "</ul>"
        )
    tg = kg_data.get("topgaps") or []
    if tg:
        h.append(
            '<div class="mini-h">榜后差额（前五买卖差 × 席位性质 '
            "× 次日穿透 · 近 8）</div>"
        )
        h.append(
            '<table class="tbl"><tr><th>日期</th><th>差额</th>'
            "<th>占成交额</th><th>主导性质</th><th>量化占比</th>"
            "<th>次日收益</th><th>穿透</th><th>上榜原因</th></tr>"
            + "".join(
                f"<tr><td>{_esc(r.get('date'))}</td>"
                f'<td class="{_pct_cls(r.get("gap"))}">{_esc(_amt(r.get("gap")))}</td>'
                f"<td>{_esc(_pc_ratio(r.get('gr'), signed=False))}</td>"
                f"<td>{_esc(_NAT.get(r.get('nat') or 'other', '普通'))}</td>"
                f"<td>{_esc(_pc_ratio(r.get('qr'), signed=False))}</td>"
                f'<td class="{_pct_cls(r.get("ret1"))}">'
                f"{_esc(_pc_ratio(r.get('ret1')))}</td>"
                f"<td>{'✅' if r.get('pen') == 1 else ('—' if r.get('pen') is None else '✗')}</td>"
                f'<td class="t">{_esc((r.get("reason") or "")[:20])}</td></tr>'
                for r in tg[:8]
            )
            + "</table>"
        )
        h.append(
            '<div class="t">差额=买方前五-卖方前五；穿透=差额方向与'
            "次日收盘同向（近端事件次日未到为 —）· 与 KG 事件页同口径"
            "</div>"
        )
    if not (drive or hm or holders or tg):
        h.append('<div class="empty">KG 暂无事件记录</div>')
    h.append("</section>")
    return "".join(h)


# ---------------- ⑤ 新闻区 ----------------


def _news_list(ts_code, limit=15):
    try:
        con = _ro(NEWS_DB)
        rows = con.execute(
            "SELECT ts,title,source,jev FROM news "
            "WHERE stocks LIKE ? ORDER BY id DESC LIMIT ?",
            (f'%"{ts_code}"%', limit),
        ).fetchall()
        con.close()
        return rows
    except Exception:  # noqa: BLE001
        return []


def _render_news(ts_code, rows):
    h = ['<section class="card"><h3>⑥ 新闻</h3>']
    if not rows:
        h.append(
            f'<div class="empty">近库 {_esc(ts_code)} 无关联新闻'
            f"（采集库 27,809 条中带标的关联 3,537 条）</div>"
        )
    else:
        h.append('<ul class="news">')
        for ts, title, source, jev in rows:
            jev_tag = (
                f'<span class="jev {_pct_cls(1 if jev == "利多" else (-1 if jev == "利空" else 0))}">{_esc(jev)}</span>'
                if jev
                else ""
            )
            h.append(
                f'<li><span class="t">{_esc((ts or "")[:10])}</span> '
                f'{_esc(title)} <span class="t">[{_esc(source)}]</span> {jev_tag}</li>'
            )
        h.append("</ul>")
    h.append("</section>")
    return "".join(h)


# ---------------- ⑥ 时空区（K-STOCK-055 L2：五日演化天轴） ----------------

_WEEK = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _norm_date(v):
    """任意日期形态 → YYYY-MM-DD（20261009 / '2026-10-09' / '2026-10-09 15:00'）。"""
    s = str(v or "").strip().replace("-", "").replace("/", "")[:8]
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return ""


def _bucket_by_date(rows, date_key="date"):
    b = {}
    for r in rows or []:
        g = r if isinstance(r, dict) else {}
        d = _norm_date(g.get(date_key))
        if d:
            b.setdefault(d, []).append(g)
    return b


def _pq_path(ts_code):
    try:
        num, suf = ts_code.split(".")
    except ValueError:
        return ""
    return os.path.join(
        STATION, "quant_backtest", "data", "ashare", f"{num}_{suf.upper()}.parquet"
    )


def _timeline_data(ts_code, kg_data, paper_orders):
    """近 5 个交易日切片：行情(parquet 逐日) + 信号/盘后/涨跌停/榜(KG 逐日)
    + 账本动作(orders 逐日) + 游资(hotmoney 聚合 last 归位)。"""
    days = []
    pq = _pq_path(ts_code)
    if pq and os.path.exists(pq):
        try:
            import pandas as pd

            df = pd.read_parquet(pq).tail(11)
            vols = (
                [None if pd.isna(v) else float(v) for v in df["volume"]]
                if "volume" in df
                else []
            )
            for i, r in enumerate(df.to_dict("records")):
                pct = r.get("pct_chg")
                vol = vols[i] if i < len(vols) else None
                vr = None
                if vol:
                    prev = [v for v in vols[max(0, i - 5) : i] if v]
                    if len(prev) >= 3:
                        vr = vol / (sum(prev) / len(prev))
                days.append(
                    {
                        "date": _norm_date(r.get("trade_date")),
                        "pct": None if pd.isna(pct) else float(pct),
                        "close": None if pd.isna(r.get("close")) else float(r["close"]),
                        "vol": vol,
                        "vr": vr,
                    }
                )
        except Exception:  # noqa: BLE001
            days = []
    days = days[-5:]
    # parquet 缺失时兜底：用 KG 盘后事件日期（无逐日 pct）
    if not days:
        cand = sorted(
            {_norm_date(c.get("date")) for c in (kg_data.get("candles") or [])},
            reverse=True,
        )[:5]
        days = [
            {"date": d, "pct": None, "close": None, "vol": None, "vr": None}
            for d in reversed(cand)
        ]
    sig_b = _bucket_by_date(kg_data.get("structures"))
    can_b = _bucket_by_date(kg_data.get("candles"))
    lim_b = _bucket_by_date(kg_data.get("limits"))
    tg_b = _bucket_by_date(kg_data.get("topgaps"))
    ord_b = {}
    for d, side, qty, price, status in paper_orders or []:
        dn = _norm_date(d)
        if dn:
            ord_b.setdefault(dn, []).append(
                {"side": side, "qty": qty, "price": price, "status": status}
            )
    hm_last = set()
    for r in kg_data.get("hotmoney") or []:
        dn = _norm_date(r.get("last"))
        if dn:
            hm_last.add(dn)
    for drow in days:
        d = drow["date"]
        drow["sigs"] = sig_b.get(d, [])
        drow["candles"] = can_b.get(d, [])
        drow["limits"] = lim_b.get(d, [])
        drow["gaps"] = tg_b.get(d, [])
        drow["orders"] = ord_b.get(d, [])
        drow["hm"] = d in hm_last
    # 演化结论
    out = {
        "days": days,
        "sig_days": sum(1 for x in days if x["sigs"]),
        "lt_days": sum(1 for x in days if x["limits"]),
        "followed": any(
            o["side"] and "buy" in str(o["side"]).lower()
            for x in days
            for o in x["orders"]
        ),
        "cum": None,
        "d0": None,
        "d1": None,
    }
    if len(days) >= 2 and days[0]["close"] and days[-1]["close"]:
        out["cum"] = (days[-1]["close"] / days[0]["close"] - 1) * 100
        out["d0"], out["d1"] = days[0]["date"], days[-1]["date"]
    return out


def _tl_conclusion(tl):
    if not tl["days"]:
        return "暂无可切片的交易日数据"
    seg = []
    if tl["cum"] is not None:
        seg.append(
            f"近 {len(tl['days'])} 日（{tl['d0'][5:]} → {tl['d1'][5:]}）"
            f'累计 <b class="{_pct_cls(tl["cum"])}">{_fmt_pct(tl["cum"])}</b>'
        )
    seg.append(f"信号日 {tl['sig_days']}/{len(tl['days'])}")
    if tl["lt_days"]:
        seg.append(f"涨停 {tl['lt_days']} 次")
    seg.append(
        '账本跟进：<b class="up">✔ 已有买单</b>'
        if tl["followed"]
        else '账本跟进：<span class="t">✖ 未跟进</span>'
    )
    verdict = ""
    if tl["sig_days"] >= 2 and (tl["cum"] or 0) > 0:
        verdict = "信号密集且股价走强，演化偏多——盯账本是否兑现"
    elif tl["sig_days"] >= 1 and (tl["cum"] or 0) > 0:
        verdict = "信号出现后走强，观察是否延续"
    elif tl["sig_days"] >= 1:
        verdict = "信号后回落，防假信号（核对 conf 与互证）"
    elif (tl["cum"] or 0) > 0:
        verdict = "无信号驱动的跟随上涨，谨慎追高"
    else:
        verdict = "无信号驱动，随市波动"
    return f'{" · ".join(seg)}<br><span class="t">演化：{verdict}</span>'


def _render_timeline(ts_code, tl):
    h = ['<section class="card"><h3>⑦ 时空 · 五日演化</h3>']
    if not tl["days"]:
        h.append('<div class="empty">行情湖暂无该票日线（A股行情湖覆盖）</div>')
    else:
        h.append('<div class="tlrow">')
        for x in tl["days"]:
            tags = []
            for s in x["sigs"][:2]:
                conf = f" {round(s['conf'], 2)}" if s.get("conf") is not None else ""
                tags.append(f"✦{_esc(s.get('type'))}{conf}")
            for c in x["candles"][:1]:
                tech = f"·{_esc(c['tech'])}" if c.get("tech") else ""
                tags.append(f"◈{_esc(c.get('type'))}{tech}")
            for lm in x["limits"][:1]:
                tags.append(
                    f"⬆涨停连{_esc(lm.get('lt'))}"
                    if (lm.get("lt") or 0) > 0
                    else "⬆涨停"
                )
            for g in x["gaps"][:1]:
                _gt = f"榜后差额 {_amt(g.get('gap'))}"
                if g.get("pen") == 1:
                    _gt += "·穿透"
                tags.append(_gt)
            if x["hm"]:
                tags.append("游资活跃")
            for o in x["orders"][:2]:
                side = "买" if "buy" in str(o["side"]).lower() else "卖"
                tags.append(
                    f'<b class="{"up" if side == "买" else "dn"}">'
                    f"账本{side}{_esc(o['qty'])}股</b>"
                )
            extra = (
                len(x["sigs"])
                + len(x["candles"])
                + len(x["limits"])
                + len(x["orders"])
                - 6
            )
            tag_html = "<br>".join(tags)
            if extra > 0:
                tag_html += f'<br><span class="t">+{extra} 条</span>'
            try:
                wd = _WEEK[datetime.strptime(x["date"], "%Y-%m-%d").weekday()]
            except ValueError:
                wd = ""
            vr = x.get("vr")
            vol_txt = _fmt_vol(x.get("vol")) if x.get("vol") else ""
            if vr is not None:
                vcls = "up" if vr >= 2 else ("t" if vr <= 0.6 else "dc-close")
                vhtml = (
                    f'<div class="dc-vol {vcls}">量 {vol_txt} · 量能 {vr:.1f}×</div>'
                    if vol_txt
                    else f'<div class="dc-vol {vcls}">量能 {vr:.1f}×</div>'
                )
            elif vol_txt:
                vhtml = f'<div class="dc-vol dc-close">量 {vol_txt}</div>'
            else:
                vhtml = ""
            h.append(
                f'<div class="daycard">'
                f'<div class="dc-date">{_esc(x["date"][5:])} {wd}</div>'
                f'<div class="dc-pct {_pct_cls(x["pct"])}">{_fmt_pct(x["pct"])}</div>'
                f'<div class="dc-close">收 {_esc(round(x["close"], 2) if x["close"] else "—")}</div>'
                f"{vhtml}"
                f'<div class="dc-tags">{tag_html or "—"}</div></div>'
            )
        h.append("</div>")
    h.append(f'<div class="tlconcl">{_tl_conclusion(tl)}</div>')
    h.append("</section>")
    return "".join(h)


# ---------------- 页面组装 ----------------

_STYLE = """
  body { background:var(--bg-page); color:var(--text); margin:0; }
  a { color:var(--link); text-decoration:none; }
  a:hover { text-decoration:underline; }
  .scwrap { max-width: var(--page-w); margin: 0 auto; padding: 0 26px 48px; }
  .schead { display:flex; align-items:center; gap:12px; padding:20px 0 12px; flex-wrap:wrap; }
  .schead h1 { font-size:18px; }
  .schead .code { font-family:Consolas,monospace; color:var(--text-dim); }
  .schead .nav { margin-left:auto; font-size:12.5px; }
  .card { background:var(--bg-raised); border:1px solid var(--border); border-radius:14px;
          padding:18px 20px; margin-top:14px; }
  .card h3 { font-size:13px; color:var(--text-dim); font-weight:600; margin-bottom:10px;
             letter-spacing:1px; }
  .qline { display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; }
  .qname { font-size:17px; font-weight:700; }
  .qprice { font-size:26px; font-weight:800; font-family:Consolas,monospace; }
  .qpct { font-size:16px; font-weight:700; font-family:Consolas,monospace; }
  .qt { color:var(--text-dim); font-size:11.5px; margin-left:auto; }
  .qgrid { display:flex; gap:16px; margin-top:8px; color:var(--text-dim);
           font-family:Consolas,monospace; font-size:12.5px; flex-wrap:wrap; }
  .up { color:var(--color-up); } .dn { color:var(--color-dn); }
  .bsgrid { display:flex; gap:18px; flex-wrap:wrap; }
  .bsg { flex:1 1 180px; min-width:170px; }
  .bsgh { font-size:11px; color:var(--text-dim); letter-spacing:1px;
          margin-bottom:6px; border-bottom:1px solid var(--border-weak);
          padding-bottom:4px; }
  .bsi { display:flex; justify-content:space-between; gap:8px;
         font-size:12.5px; padding:3px 0; }
  .bsk { color:var(--text-dim); }
  .bsv { font-family:var(--font-mono); }
  .mini-h { margin:12px 0 6px; font-size:12px; color:var(--text-dim); }
  ul.mini { list-style:none; font-size:12.5px; line-height:1.9; }
  ul.mini li { border-bottom:1px dashed var(--border-weak); padding:2px 0; }
  ul.news { list-style:none; font-size:13px; line-height:1.8; }
  ul.news li { padding:4px 0; border-bottom:1px dashed var(--border-weak); }
  .jev { margin-left:8px; font-size:11px; }
  .tbl { width:100%; border-collapse:collapse; font-size:12.5px; }
  .tbl th,.tbl td { text-align:left; padding:5px 8px; border-bottom:1px solid var(--border-weak); }
  .tbl th { color:var(--text-dim); font-weight:600; font-size:11.5px; }
  .tbl td { font-family:Consolas,"Microsoft YaHei",monospace; }
  .empty { color:var(--text-dim); font-size:12.5px; padding:6px 0; }
  .t { color:var(--text-dim); font-size:11.5px; }
  .deep { margin-top:12px; padding-top:10px; border-top:1px dashed var(--border-weak);
          font-size:12.5px; }
  .deep a { margin-right:6px; }
  .tlrow { display:flex; gap:10px; overflow-x:auto; }
  .daycard { flex:1 1 0; min-width:128px; border:1px solid var(--border-weak);
             border-radius:10px; padding:10px 12px; background:var(--bg-surface); }
  .dc-date { font-size:11.5px; color:var(--text-dim); }
  .dc-pct { font-size:19px; font-weight:800; font-family:Consolas,monospace; margin:2px 0; }
  .dc-close { font-size:12px; font-family:Consolas,monospace; color:var(--text-dim); }
  .dc-vol { font-size:12px; font-family:var(--font-mono); color:var(--text-dim); }
  .dc-tags { margin-top:6px; font-size:11.5px; line-height:1.8;
             border-top:1px dashed var(--border-weak); padding-top:6px; }
  .tlconcl { margin-top:10px; font-size:12.5px; line-height:1.9;
             border-top:1px dashed var(--border-weak); padding-top:8px; }
"""


def render_card(ts_code, days=120):
    """统一详情卡 HTML（浏览器主形态）。接受 600853.SH / 600853 / sh600853。"""
    ts_code = normalize_code(ts_code)
    # KG 主数据（信号/事件/盘后/涨跌停）——Neo4j 偶发抖动，失败重试一次
    kg_data = {}
    name = ""
    import time as _time

    for _attempt in range(2):
        try:
            if QBT_DIR not in __import__("sys").path:
                __import__("sys").path.insert(0, QBT_DIR)
            import kg_portal

            d = kg_portal.stock(ts_code, days)
            if d.get("ok"):
                kg_data = d
                # basic 兼容两种形态：dict（kg_portal 现状）或 list[dict]
                basic = kg_data.get("basic") or {}
                if isinstance(basic, list):
                    basic = basic[0] if basic else {}
                if basic.get("name"):
                    name = basic["name"]
                break
        except Exception:  # noqa: BLE001
            if _attempt == 0:
                _time.sleep(0.6)
    if not name:
        name = ""  # 落到 quote 兜底（quote_info 随后统一取）
    quote_info = _sec_quote(ts_code)
    if not name and quote_info.get("quote"):
        name = quote_info["quote"].get("name") or ts_code
    if not name:
        name = ts_code

    # paper：挂单/成交（该票）——天轴需要逐日归位，多取一些
    paper_orders = []
    try:
        con = _ro(PAPER_DB)
        paper_orders = con.execute(
            "SELECT date(ts),side,qty,coalesce(filled_price,manual_price,price_base,0),status "
            "FROM orders WHERE stock LIKE ? ORDER BY id DESC LIMIT 30",
            (f"%{_num6(ts_code)}%",),
        ).fetchall()
        con.close()
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/stock_card.py", exc)

    now = datetime.now().strftime("%F %T")
    tl = _timeline_data(ts_code, kg_data, paper_orders)
    _vr = (
        tl["days"][-1].get("vr")
        if tl.get("days") and tl["days"][-1].get("vr")
        else None
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_esc(name)} {_esc(ts_code)} · 统一详情卡</title>
<link rel="stylesheet" href="/static/tokens.css">
<script src="/static/nav.js" defer></script>
<link rel="icon" href="data:,">
<style>{_STYLE}</style>
</head>
<body>
<div class="scwrap">
  <div class="schead">
    <h1>{_esc(name)}</h1><span class="code">{_esc(ts_code)}</span>
    <span class="nav">
      <a href="/">← 工作站</a>　
      <a href="/bt/stock?c={_esc(_num6(ts_code))}">量化全景</a>　
      <a href="/kg?c={_esc(ts_code)}">图谱视角</a>　
      <a href="/paper">模拟仓</a>
    </span>
  </div>
  <div class="t" style="padding:0 0 4px">统一详情卡（七区制）· 生成 {now} · 数据源：KG/news_collector/paper.db/行情湖</div>
  {_render_quote(ts_code, quote_info.get("quote"), quote_info.get("candles"), quote_info.get("limits"), _vr)}
  {_render_basic(ts_code)}
  {_render_signals(kg_data, paper_orders)}
  {_render_ledger(_paper_ledger(ts_code))}
  {_render_events(kg_data)}
  {_render_news(ts_code, _news_list(ts_code))}
  {_render_timeline(ts_code, tl)}
</div>
</body>
</html>"""
