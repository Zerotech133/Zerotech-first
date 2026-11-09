# -*- coding: utf-8 -*-
"""池状态看板：pool_state.py 的可视化姊妹页（/pool-state 静态页）。

定位：pool_state 扫描结果（reports/pool_state_<数据日>.json）的纯渲染层。
不做任何计算、不拉行情、不改池——只把 JSON 渲染成一页能看的表。

设计纪律（与 pool_watch_page.py 对齐）：
· 纯渲染：阶段判定/阈值全部在 pool_state.py，本文件零重算。
· 诚实展示：数据基准日、扫描统计、被剔除的票数全部如实标注；
  池子未经回测这一条常驻页脚，不许在本页伪装成「信号」。
· 涨红跌绿（A股惯例）。
· 容错：JSON 缺失/损坏 → 空态页照常出，不炸。

用法:
  python pool_state_page.py                 # 读最近一份 pool_state_*.json
  python pool_state_page.py --in X.json     # 指定输入
  python pool_state_page.py --open          # 生成后打开
"""

import argparse
import glob
import html
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, "reports")

STAGE_META = {
    "B1": ("蓄势", "临近爆发，盯突破", "#b7791f", "#fff7e8"),
    "B2": ("突破", "已放量破前高", "#d92b2b", "#fdeaea"),
    "B3": ("加速", "主升中，盯衰竭", "#8a5fc4", "#f3eefb"),
    "C1": ("衰竭", "破位/冲高回落", "#0a9e5c", "#e8f6f0"),
}
BUCKET_ORDER = ["微盘<20", "小盘20-50", "中盘50-200", "大盘200-1k", "超大盘>1k", "未知"]

_CSS = """
  :root{--bg:#f5f6f8;--card:#fff;--line:#e3e6ea;--txt:#1c2330;--sub:#6b7686;
        --up:#d92b2b;--down:#0a9e5c;--accent:#2456c4;--warn:#b7791f;--cool:#8a5fc4}
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:var(--bg);color:var(--txt);
       font:14px/1.6 "Microsoft YaHei","PingFang SC",sans-serif;padding:20px}
  .wrap{max-width:1360px;margin:0 auto}
  h1{font-size:20px;display:flex;align-items:center;gap:10px}
  .sub{color:var(--sub);font-size:12px;margin:4px 0 16px}
  .cards{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:14px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
  .card .k{font-size:12px;color:var(--sub)}
  .card .v{font-size:26px;font-weight:700;line-height:1.2;margin-top:4px}
  .card .note{font-size:11px;color:var(--sub);margin-top:2px}
  .panel{background:var(--card);border:1px solid var(--line);border-radius:10px;
         padding:0;margin-bottom:14px;overflow:hidden}
  .panel h2{font-size:14px;padding:12px 16px;border-bottom:1px solid var(--line);
            display:flex;align-items:center;gap:8px}
  .panel h2 .hint{font-weight:normal;font-size:11px;color:var(--sub);margin-left:auto}
  .dot{width:8px;height:8px;border-radius:50%;display:inline-block}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th{background:#fafbfc;color:var(--sub);font-weight:normal;text-align:left;
     padding:9px 10px;border-bottom:1px solid var(--line);white-space:nowrap;
     position:sticky;top:0;z-index:2}
  td{padding:8px 10px;border-bottom:1px solid #f0f2f5;white-space:nowrap}
  tr:hover td{background:#f8faff}
  .up{color:var(--up);font-weight:bold}
  .down{color:var(--down);font-weight:bold}
  .code{font-family:Consolas,monospace;color:var(--sub)}
  .nm{font-weight:600}
  .cond{display:inline-flex;gap:3px}
  .cond i{width:15px;height:15px;border-radius:3px;background:#eef1f5;color:#a6afbd;
          font-style:normal;font-size:9px;display:flex;align-items:center;
          justify-content:center;font-family:Consolas,monospace}
  .cond i.on{background:#2456c4;color:#fff}
  .tag{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px}
  .bar-row{display:flex;align-items:center;gap:10px;padding:6px 16px}
  .bar-row .lb{width:96px;font-size:12px;color:var(--sub);flex:none}
  .bar-row .bar{height:18px;border-radius:4px;background:#eef1f5;flex:1;position:relative}
  .bar-row .fill{display:block;height:100%;border-radius:4px;background:var(--accent);opacity:.75}
  .bar-row .num{width:44px;text-align:right;font-size:12px;font-weight:600;flex:none}
  .chips{display:flex;gap:8px;align-items:center;padding:10px 16px;border-bottom:1px solid var(--line);
         flex-wrap:wrap}
  .chip{padding:4px 12px;border:1px solid var(--line);border-radius:14px;font-size:12px;
        cursor:pointer;background:#fff;color:var(--sub);user-select:none}
  .chip.on{background:#2456c4;color:#fff;border-color:#2456c4}
  .foot{color:var(--sub);font-size:11px;line-height:1.9;padding:10px 2px}
  .warn{background:#fff8e6;border:1px solid #f0d9a8;color:#8a6212;border-radius:8px;
        padding:10px 14px;font-size:12px;margin-bottom:14px}
  .empty{padding:28px;text-align:center;color:var(--sub)}
"""

_JS = """
function filterBucket(b, el){
  document.querySelectorAll('.chip').forEach(c=>c.classList.remove('on'));
  el.classList.add('on');
  document.querySelectorAll('tbody tr').forEach(tr=>{
    tr.style.display = (b==='ALL' || tr.dataset.bucket===b) ? '' : 'none';
  });
}
"""


def _esc(s):
    return html.escape("" if s is None else str(s))


def _pct(v, digits=1, sign=False):
    if v is None:
        return "—"
    x = v * 100
    s = f"{x:+.{digits}f}%" if sign else f"{x:.{digits}f}%"
    return s


def _cls(v, invert=False):
    if v is None:
        return ""
    if v > 0:
        return "down" if invert else "up"
    if v < 0:
        return "up" if invert else "down"
    return ""


COND_KEYS = ["A", "N", "C", "S", "M"]
COND_TITLE = {
    "A": "站上MA60",
    "N": "靠近120日高",
    "C": "波动收缩",
    "S": "缩量",
    "M": "均线粘合",
}


def _cond_badges(hits):
    on = set(hits or "")
    out = []
    for k in COND_KEYS:
        cls = "on" if k in on else ""
        out.append(f'<i class="{cls}" title="{COND_TITLE[k]}">{k}</i>')
    return '<span class="cond">' + "".join(out) + "</span>"


def _row(r, stage):
    pct = r.get("pct")
    score = r.get("score") or 0
    extra = ""
    if stage == "B2":
        extra = f'<td class="{_cls(pct)}">{_pct(pct/100 if pct is not None else None, 2, True)}</td>'
    elif stage == "C1":
        extra = f'<td style="color:#0a9e5c">{_esc(r.get("c1") or "—")}</td>'
    else:
        extra = f'<td class="{_cls(r.get("ret20"))}">{_pct(r.get("ret20"), 1, True)}</td>'

    return (
        f'<tr data-bucket="{_esc(r.get("bucket"))}">'
        f'<td class="code">{_esc(r.get("code"))}</td>'
        f'<td class="nm">{_esc(r.get("name"))}</td>'
        f'<td style="text-align:right">{r.get("close", 0):.2f}</td>'
        f'<td style="text-align:right">{r.get("mv_yi") or 0:.1f}</td>'
        f'<td style="text-align:right">{_pct(r.get("dist_hi120"))}</td>'
        f'<td style="text-align:right">{r.get("contract") or 0:.2f}</td>'
        f'<td style="text-align:right">{r.get("volr") or 0:.2f}</td>'
        f'<td style="text-align:right">{_pct(r.get("spread"))}</td>'
        f'<td style="text-align:right">{r.get("amt20_yi") or 0:.2f}</td>'
        f"{extra}"
        f'<td style="text-align:right;font-weight:700">{score:.1f}</td>'
        f"<td>{_cond_badges(r.get('hits'))}</td>"
        "</tr>"
    )


def _table(rows, stage):
    if not rows:
        return '<div class="empty">该阶段今日为空</div>'
    head_extra = {"B2": "当日涨幅", "C1": "衰竭原因"}.get(stage, "20日涨幅")
    th = (
        "<thead><tr>"
        "<th>代码</th><th>名称</th><th style='text-align:right'>现价</th>"
        "<th style='text-align:right'>流通市值(亿)</th>"
        "<th style='text-align:right'>距前高</th>"
        "<th style='text-align:right'>收缩</th><th style='text-align:right'>缩量</th>"
        "<th style='text-align:right'>粘合</th><th style='text-align:right'>20日均额(亿)</th>"
        f"<th style='text-align:right'>{head_extra}</th>"
        "<th style='text-align:right'>强度</th><th>条件</th>"
        "</tr></thead>"
    )
    body = "<tbody>" + "".join(_row(r, stage) for r in rows) + "</tbody>"
    return f"<table>{th}{body}</table>"


def build(data):
    asof = data.get("data_asof") or "—"
    meta = data.get("meta") or {}
    buckets = data.get("buckets") or {}
    all_rows = [r for st in ("B1", "B2", "B3", "C1") for r in buckets.get(st, [])]

    n_b1 = len(buckets.get("B1", []))
    n_b2 = len(buckets.get("B2", []))
    n_b3 = len(buckets.get("B3", []))
    n_c1 = len(buckets.get("C1", []))

    judged = meta.get("judged") or 0
    ma60_on = meta.get("ma60_on") or 0
    ma60_txt = f"{ma60_on}/{judged} = {ma60_on/judged*100:.1f}%" if judged else "—"
    width_note = "弱势" if judged and ma60_on / judged < 0.4 else "中性" if judged and ma60_on / judged < 0.6 else "强势"

    # 市值分档分布（按全部四档合并）
    bc = {}
    for r in all_rows:
        bc[r.get("bucket")] = bc.get(r.get("bucket"), 0) + 1
    total = max(1, len(all_rows))
    bars = []
    for k in BUCKET_ORDER:
        v = bc.get(k, 0)
        if v == 0 and k == "未知":
            continue
        w = v / total * 100
        bars.append(
            f'<div class="bar-row"><span class="lb">{_esc(k)}</span>'
            f'<span class="bar"><span class="fill" style="width:{w:.1f}%"></span></span>'
            f'<span class="num">{v}</span></div>'
        )

    chips = ['<span class="chip on" onclick="filterBucket(\'ALL\',this)">全部</span>']
    for k in BUCKET_ORDER:
        if bc.get(k):
            chips.append(f'<span class="chip" onclick="filterBucket(\'{_esc(k)}\',this)">{_esc(k)}</span>')

    def panel(st, limit=None):
        label, desc, color, bg = STAGE_META[st]
        rows = buckets.get(st, [])
        if limit:
            rows = rows[:limit]
        hint = f"{len(buckets.get(st, []))} 只"
        return (
            f'<div class="panel"><h2>'
            f'<span class="dot" style="background:{color}"></span>'
            f"{st} {label}"
            f'<span style="color:#6b7686;font-weight:normal;font-size:12px">· {desc}</span>'
            f'<span class="hint">{hint}</span></h2>'
            f"{_table(rows, st)}</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>股票池状态看板 · {_esc(asof)}</title>
<style>{_CSS}</style><script>{_JS}</script></head>
<body><div class="wrap">
<h1>股票池状态看板<span class="tag" style="background:#eef1f4;color:#6b7686">
数据基准 {_esc(asof)}</span></h1>
<div class="sub">扫描 {meta.get("files", "—")} 个行情文件 / 可判 {judged} 只 /
剔除 {meta.get("skipped", "—")}（ST · 低价 · 20日均额&lt;{meta.get("min_amt", "—")}亿 · 次新） /
耗时 {meta.get("elapsed", "—")}s ｜ 生成于 {_esc(data.get("run_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}</div>

<div class="cards">
  <div class="card"><div class="k">B1 蓄势</div><div class="v" style="color:#b7791f">{n_b1}</div>
    <div class="note">临近爆发 · 盯突破</div></div>
  <div class="card"><div class="k">B2 突破</div><div class="v" style="color:#d92b2b">{n_b2}</div>
    <div class="note">已放量破 120 日高</div></div>
  <div class="card"><div class="k">B3 加速</div><div class="v" style="color:#8a5fc4">{n_b3}</div>
    <div class="note">主升中 · 盯衰竭</div></div>
  <div class="card"><div class="k">C1 衰竭</div><div class="v" style="color:#0a9e5c">{n_c1}</div>
    <div class="note">破位 / 冲高回落</div></div>
  <div class="card"><div class="k">池内合计</div><div class="v">{len(all_rows)}</div>
    <div class="note">站上MA60 占比 {ma60_txt.split('=')[-1].strip() if '=' in ma60_txt else '—'}（{width_note}）</div></div>
</div>

<div class="warn">
<b>这不是信号，是巡检清单。</b> 池子的阶段判定尚未做过历史回测——进池后 20/60 日的收益、
胜率、与随机对照的差，一个数都还没有。在补上这一步之前，它只能告诉你「今天该盯哪几只」，
不能告诉你「买哪只」。
</div>

<div class="panel"><h2><span class="dot" style="background:#2456c4"></span>市值分档
  <span class="hint">B1+B2+B3+C1 合计 {len(all_rows)} 只</span></h2>
  {''.join(bars) if bars else '<div class="empty">无数据</div>'}
</div>

<div class="chips">{''.join(chips)}</div>

{panel("B1")}
{panel("B2")}
{panel("B3")}
{panel("C1")}

<div class="foot">
口径：<b>A</b> 站上MA60 ｜ <b>N</b> 距 120 日高 ≥ -15% ｜ <b>C</b> 波动收缩(amp10/amp60 ≤ 0.90) ｜
<b>S</b> 缩量(vol5/vol50 ≤ 0.90) ｜ <b>M</b> 均线粘合(MA5/10/20 收敛 ≤ 5%)。<br>
B1 = N 必中 且 五条中 ≥ 4 条 且 非下跌(ret20 ≥ -10%, 收盘 &gt; MA20×0.97)。<br>
B2 = 收盘 ≥ 前 120 日高 + 量 ≥ 1.5×前 20 日均量 + 当日涨幅 ≥ 3%。
B3 = 20 日涨幅 ≥ 25% + 收盘 ≥ MA20×1.08 + 距前高 ≥ -15%。<br>
C1 = 破 MA10 拐头 / 高位冲高回落 / 3 日量价背离，且距前高 ≥ -15%。<br>
剔除：ST 与退市整理、股价 &lt; 2 元、20 日均成交额 &lt; 门槛、上市不足 250 个交易日。<br>
涨红跌绿。数据源：data/ashare 日线（tushare）+ data/tdx/ts_daily_basic 流通市值。
</div>

</div></body></html>"""


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default=None, help="输入 pool_state_*.json")
    ap.add_argument("--out", dest="out", default=os.path.join(REPORTS, "pool_state.html"))
    ap.add_argument("--open", action="store_true", help="生成后打开")
    args = ap.parse_args()

    src = args.inp
    if not src:
        fs = sorted(glob.glob(os.path.join(REPORTS, "pool_state_*.json")))
        if not fs:
            print("[pool_state_page] 找不到 pool_state_*.json，先跑 pool_state.py --scan --save")
            return 1
        src = fs[-1]

    with open(src, encoding="utf-8") as f:
        data = json.load(f)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(build(data))
    print(f"[pool_state_page] {src} -> {args.out}")
    if args.open:
        os.startfile(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
