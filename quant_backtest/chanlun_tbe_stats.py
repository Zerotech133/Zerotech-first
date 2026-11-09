# -*- coding: utf-8 -*-
"""K154 影子统计：chanlun_bi 1 类买卖点 tbe/pbe 分层对照报表。

口径（影子理论口径，与 chanlun_backtest.eval_samples 的 theo 同族）：
· 信号集 = 每标的一次性 analyze(bars, closes)（窗口 LIMIT_BARS）的全部
  b1/s1 信号（含 sub 子类），不做逐根截短重放（在线同码确定性引擎，
  笔终点可能后续修正的确认滞后不在本口径内——攒样本阶段够用）；
· 基准 = 信号日收盘（笔终点分型日=原始交易日，可定位 bars 索引）；
· dN 方向收益 = b1: c[i0+N]/c[i0]-1 / s1: 1-c[i0+N]/c[i0]，窗不足丢弃；
· 分层 = 子类(tbe/pbe) × 持有期(d1/d3/d5/d10/d20/d60) × 年度(d20)。

产出：
· data/chanlun_tbe_stats.json（groups 键向后兼容门户 /bt-chanlun 小卡）
· reports/chanlun_tbe_report.html（自包含报表，门户 /bt-tbe serve）
--limit N 试跑，默认全市场。
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from chanlun_bi import analyze  # noqa: E402

ASHARE = os.path.join(HERE, "data", "ashare")
OUT = os.path.join(HERE, "data", "chanlun_tbe_stats.json")
OUT_HTML = os.path.join(HERE, "reports", "chanlun_tbe_report.html")


def _atomic_write(path, text):
    """原子落盘（tempfile + os.replace），防门户并发读到半截文件。"""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fp:
        fp.write(text)
    os.replace(tmp, path)


LIMIT_BARS = 500
HOLDS = (1, 3, 5, 10, 20, 60)
LAB = {
    "b1_tbe": "买1 · 趋势背驰",
    "b1_pbe": "买1 · 盘整背驰",
    "s1_tbe": "卖1 · 趋势背驰",
    "s1_pbe": "卖1 · 盘整背驰",
}


def _q(vals, p):
    v = sorted(vals)
    if not v:
        return None
    i = min(len(v) - 1, max(0, int(round(p / 100.0 * (len(v) - 1)))))
    return round(v[i] * 100.0, 2)


def _med(v):
    v = sorted(v)
    return v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2.0


def _grp(vals, full=False):
    if not vals:
        return {"n": 0, "win": None, "avg": None, "med": None}
    g = {
        "n": len(vals),
        "win": round(sum(1 for x in vals if x > 0) * 100.0 / len(vals)),
        "avg": round(sum(vals) * 100.0 / len(vals), 2),
        "med": round(_med(vals) * 100.0, 2),
    }
    if full:
        g.update(
            {
                "p10": _q(vals, 10),
                "p25": _q(vals, 25),
                "p75": _q(vals, 75),
                "p90": _q(vals, 90),
            }
        )
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="试跑标的数上限")
    a = ap.parse_args()

    files = sorted(f for f in os.listdir(ASHARE) if f.endswith(".parquet"))
    if a.limit:
        files = files[: a.limit]
    # (t,sub) -> hold -> [方向收益]；(t,sub,year) -> d20 收益；
    # (t,sub,'YYYY-MM') -> d20 收益（K194 月度滚动）
    groups, by_year, by_month = {}, {}, {}
    n_sig = n_sym = 0
    for f in files:
        try:
            df = pd.read_parquet(os.path.join(ASHARE, f)).sort_index().tail(LIMIT_BARS)
            if len(df) < 60:
                continue

            def _dt(v):
                s = str(v)
                return s if "-" in s else "%s-%s-%s" % (s[:4], s[4:6], s[6:8])

            dts = [_dt(v) for v in df["trade_date"]]
            bars = [
                {"dt": dts[i], "h": float(r["high"]), "l": float(r["low"])}
                for i, (_, r) in enumerate(df.iterrows())
            ]
            closes = [float(r["close"]) for _, r in df.iterrows()]
            st = analyze(bars, closes)
        except Exception:
            continue
        idx = {b["dt"]: i for i, b in enumerate(bars)}
        hit = False
        for s in st["signals"]:
            if s["t"] not in ("b1", "s1"):
                continue
            i0 = idx.get(s["d"])
            if i0 is None or not closes[i0]:
                continue
            key = (s["t"], s.get("sub") or "pbe")
            g = groups.setdefault(key, {h: [] for h in HOLDS})
            for h in HOLDS:
                j = i0 + h
                if j < len(closes) and closes[j]:
                    r = closes[j] / closes[i0] - 1.0
                    g[h].append(r if s["t"] == "b1" else -r)
            j20 = i0 + 20
            if j20 < len(closes) and closes[j20]:
                r = closes[j20] / closes[i0] - 1.0
                by_year.setdefault(key + (s["d"][:4],), []).append(
                    r if s["t"] == "b1" else -r
                )
                by_month.setdefault(key + (s["d"][:7],), []).append(
                    r if s["t"] == "b1" else -r
                )
            n_sig += 1
            hit = True
        if hit:
            n_sym += 1

    def _k(k):
        return "%s_%s" % k

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "chanlun_bi 1类 tbe/pbe 影子分层（理论口径，"
        "基准=信号日收盘，无重放确认滞后）",
        "n_symbols": n_sym,
        "n_signals": n_sig,
        "groups": {
            _k(k): {"d5": _grp(v[5]), "d20": _grp(v[20])}
            for k, v in sorted(groups.items())
        },
        "curves": {
            _k(k): {("d%d" % h): _grp(v[h], full=(h == 20)) for h in HOLDS}
            for k, v in sorted(groups.items())
        },
        "by_year": {
            "%s_%s_%s" % (k[0], k[1], y): _grp(v)
            for k, v in sorted(by_year.items())
            for y in [k[2]]
        },
        "by_month": {
            "%s_%s_%s" % (k[0], k[1], m): _grp(v)
            for k, v in sorted(by_month.items())
            for m in [k[2]]
        },
    }
    _atomic_write(OUT, json.dumps(out, ensure_ascii=False, indent=1))
    _render_html(out)
    print("symbols=%d signals=%d -> %s + %s" % (n_sym, n_sig, OUT, OUT_HTML))


def _fmt(x, suf="%"):
    return "—" if x is None else "%s%s" % (x, suf)


def _fmt_avg(x):
    if x is None:
        return "—"
    return "<b style='color:%s'>%s%%</b>" % ("#f85149" if x > 0 else "#3fb950", x)


def _render_html(o):
    gen = (o.get("generated_at") or "")[:16].replace("T", " ")
    rows_main = ""
    for k, lab in LAB.items():
        g = (o.get("groups") or {}).get(k)
        if not g:
            continue
        d5, d20 = g.get("d5") or {}, g.get("d20") or {}
        rows_main += (
            "<tr><td style='text-align:left'><b>%s</b></td>"
            "<td>%s</td><td>%s · %s</td><td>%s</td><td>%s · %s</td></tr>"
            % (
                lab,
                _fmt(d5.get("n"), ""),
                _fmt_avg(d5.get("avg")),
                _fmt(d5.get("win")),
                _fmt(d20.get("n"), ""),
                _fmt_avg(d20.get("avg")),
                _fmt(d20.get("win")),
            )
        )
    rows_curve = ""
    for k, lab in LAB.items():
        c = (o.get("curves") or {}).get(k)
        if not c:
            continue
        tds = "".join(
            "<td>%s</td>" % _fmt_avg((c.get("d%d" % h) or {}).get("avg")) for h in HOLDS
        )
        ns = [(c.get("d%d" % h) or {}).get("n") for h in HOLDS]
        rows_curve += (
            "<tr><td style='text-align:left'><b>%s</b></td>%s"
            "<td class='dim'>%s</td></tr>" % (lab, tds, "~%s" % max(x or 0 for x in ns))
        )
    years = sorted({ky.rsplit("_", 1)[1] for ky in (o.get("by_year") or {})})
    rows_year = ""
    for k, lab in LAB.items():
        tds = ""
        for y in years:
            g = (o.get("by_year") or {}).get("%s_%s" % (k, y))
            if g and g.get("n"):
                tds += "<td class='dim'>%s<br>%s</td>" % (
                    g["n"],
                    _fmt_avg(g.get("avg")),
                )
            else:
                tds += "<td class='dim'>—</td>"
        rows_year += "<tr><td style='text-align:left'><b>%s</b></td>%s</tr>" % (
            lab,
            tds,
        )
    # K194 月度滚动：行=月份（升序），列=两子类（d20 n/avg），n<10 灰显
    months = sorted({ky.rsplit("_", 1)[1] for ky in (o.get("by_month") or {})})
    rows_month = ""
    for m in months:
        tds = ""
        for k, lab in LAB.items():
            g = (o.get("by_month") or {}).get("%s_%s" % (k, m))
            if g and g.get("n"):
                sty = "" if g["n"] >= 10 else " class='dim'"
                tds += "<td%s>%s<br>%s</td>" % (sty, g["n"], _fmt_avg(g.get("avg")))
            else:
                tds += "<td class='dim'>—</td>"
        rows_month += "<tr><td style='text-align:left'><b>%s</b></td>%s</tr>" % (m, tds)
    rows_dist = ""
    for k, lab in LAB.items():
        d20 = ((o.get("curves") or {}).get(k) or {}).get("d20") or {}
        if not d20.get("n"):
            continue
        rows_dist += (
            "<tr><td style='text-align:left'><b>%s</b></td>"
            "<td class='dim'>%s</td><td>%s</td><td>%s</td>"
            "<td><b>%s</b></td><td>%s</td><td>%s</td>"
            "<td class='dim'>%s%%</td><td class='dim'>%s%%</td></tr>"
            % (
                lab,
                d20.get("n"),
                _fmt(d20.get("p10")),
                _fmt(d20.get("p25")),
                _fmt(d20.get("med")),
                _fmt(d20.get("p75")),
                _fmt(d20.get("p90")),
                d20.get("win"),
                d20.get("avg"),
            )
        )
    html = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>买1/卖1 · 趋势背驰 vs 盘整背驰 — 影子分层报表</title>
<style>
body{background:#0d1117;color:#e6edf3;font:14px/1.7 -apple-system,
"Segoe UI","Microsoft YaHei",sans-serif;margin:0;padding:24px}
.wrap{max-width:1060px;margin:0 auto}
h1{font-size:20px;margin:0 0 4px}
h2{font-size:16px;margin:28px 0 8px;color:#58a6ff}
.meta{color:#8b949e;font-size:12px;margin-bottom:16px}
table{border-collapse:collapse;width:100%%;font-size:13px;
margin-bottom:6px}
th,td{border:1px solid #30363d;padding:6px 10px;text-align:center}
th{background:#161b22;color:#8b949e;font-weight:600}
tr:nth-child(even) td{background:#11161d}
.dim{color:#8b949e;font-size:12px}
.note{background:#161b22;border:1px solid #30363d;border-radius:10px;
padding:12px 16px;font-size:12.5px;color:#8b949e;line-height:1.9;
margin-top:28px}
.concl{background:#13261b;border:1px solid #238636;border-radius:10px;
padding:12px 16px;font-size:13px;line-height:1.9;margin-top:16px}
a{color:#58a6ff;text-decoration:none}
</style></head><body><div class="wrap">
<h1>📉 买1/卖1 · 趋势背驰(tbe) vs 盘整背驰(pbe) — 影子分层报表</h1>
<div class="meta">chanlun_bi 谱系（详情页 K 线菱形「·趋/·盘」同口径）·
%s 个标的 · %s 个信号 · 生成于 %s UTC ·
<a href="/bt-chanlun">← 返回缠论工作台</a></div>

<h2>① 主对照（方向收益：红=做多盈利侧 / 绿=亏损侧）</h2>
<table><tr><th>子类</th><th>d5 n</th><th>d5 avg / win</th>
<th>d20 n</th><th>d20 avg / win</th></tr>%s</table>

<h2>② 持有期曲线（avg%%，d1→d60 衰减路径）</h2>
<table><tr><th>子类</th><th>d1</th><th>d3</th><th>d5</th><th>d10</th>
<th>d20</th><th>d60</th><th>样本量</th></tr>%s</table>

<h2>③ 年度分层（d20：n / avg%%，时间稳定性）</h2>
<table><tr><th>子类</th>%s</tr>%s</table>

<h2>③·⑤ 月度滚动（d20：n / avg%%，月度稳定性 · n&lt;10 灰显 ·
早期月份样本受各标的本地历史长度与 tail(%d) 窗影响偏少）</h2>
<table><tr><th>月份</th><th>买1·盘整 pbe</th><th>买1·趋势 tbe</th></tr>%s</table>

<h2>④ d20 分布（分位数%%）</h2>
<table><tr><th>子类</th><th>n</th><th>P10</th><th>P25</th><th>P50</th>
<th>P75</th><th>P90</th><th>win</th><th>avg</th></tr>%s</table>

<div class="concl'><b>结论要点</b>（攒样本阶段，未启用任何拦截）：
① 买1 pbe 的 d20 显著优于 tbe——趋势背驰=下跌趋势中继背驰，反弹后常续趋势；盘背=震荡末端，均值回归更完整；② 卖1 做空两子类 d20 皆负（A 股做多惯性）；③ d5 各子类 win 偏高系理论口径确认滞后前视成分，议拦截前必须过可成交重放复核。</div>

<div class="note"><b>口径</b>：信号集=每标的一次性 analyze(bars, closes)（%d 根窗口）的全部 1 类信号；tbe=紧邻前序中枢与当前中枢同向排列且无重叠（趋势第二中枢后离开段），pbe=单中枢盘背；基准=信号日收盘（笔终点分型日）；方向收益 b1: c[i+N]/c[i]-1、s1: 取反；窗不足丢弃。理论口径（无逐根重放确认滞后），与 /bt-chanlun 样本统计面板的可成交口径互补。刷新：手跑
<code>chanlun_tbe_stats.py</code>（quant_backtest 目录）。</div>
</div></body></html>""" % (
        o.get("n_symbols", "—"),
        o.get("n_signals", "—"),
        gen,
        rows_main,
        rows_curve,
        "".join("<th>%s</th>" % y for y in years) or "<th>—</th>",
        rows_year,
        LIMIT_BARS,
        rows_month,
        rows_dist,
        LIMIT_BARS,
    )
    os.makedirs(os.path.dirname(OUT_HTML), exist_ok=True)
    _atomic_write(OUT_HTML, html)


if __name__ == "__main__":
    main()
