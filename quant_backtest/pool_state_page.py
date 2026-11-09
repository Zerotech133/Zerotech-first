# -*- coding: utf-8 -*-
"""缠论结构池看板：pool_state.py 的渲染层（/pool-state 静态页）。

定位：只把 reports/pool_state_<数据日>.json 渲染成一页，零重算、零行情。
阶段判定、中枢、买卖点、背驰全部来自 pool_state.py（及其下游 chanlun_signal）。

设计纪律（与 pool_watch_page.py 对齐）：
· 纯渲染：一个指标都不在这里算。
· 诚实展示：数据基准日、剔除明细、中枢是否已升级，全部如实标注；
  「池子未经回测」常驻页首，不许伪装成信号。
· 涨红跌绿（A股惯例）。买点侧用暖色，卖点/下跌侧用绿，震荡用灰。
· 容错：JSON 缺失/损坏 → 空态页照常出。

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

# 九态：名称、一句话定义、主色、底色
STAGE_META = {
    "三买待": ("三买待", "离开上沿，等回踩不破 ZG", "#b7791f", "#fff7e8"),
    "三买":   ("三买",   "回踩不破 ZG，向上趋势确认", "#d92b2b", "#fdeaea"),
    "二买":   ("二买",   "一买后首回调不破极值", "#e07b39", "#fdf0e8"),
    "一买":   ("一买",   "向下线段终结·底背驰", "#c04a8a", "#fbeaf3"),
    "背驰":   ("背驰",   "顶背驰，上涨结构走完", "#0a9e5c", "#e8f6f0"),
    "中枢":   ("中枢",   "枢内震荡，等它选方向", "#5a6b82", "#eef1f5"),
    "三卖":   ("三卖",   "跌破下沿反抽不过", "#0a9e5c", "#e8f6f0"),
    "离开段": ("离开段", "已远离上沿，追高区", "#8a7a5c", "#f5f1ea"),
    "中枢下": ("中枢下", "向下离开中枢", "#0a9e5c", "#e8f6f0"),
}
# 页面主表只放这六档（下跌侧三档只报数量，避免淹没信号）
SHOW_STAGES = ["三买待", "三买", "二买", "一买", "背驰", "中枢"]
CARD_STAGES = ["三买待", "三买", "二买", "一买", "背驰"]
# 卡片空间窄，用短语；面板标题用 STAGE_META 里的完整描述
CARD_DESC = {
    "三买待": "离开上沿，等回踩",
    "三买": "回踩不破 ZG",
    "二买": "一买后回调不破",
    "一买": "下跌段终结·底背驰",
    "背驰": "顶背驰，结构走完",
}
BUCKET_ORDER = ["微盘<20", "小盘20-50", "中盘50-200", "大盘200-1k", "超大盘>1k", "未知"]

_CSS = """
  :root{--bg:#f5f6f8;--card:#fff;--line:#e3e6ea;--txt:#1c2330;--sub:#6b7686;
        --up:#d92b2b;--down:#0a9e5c;--accent:#2456c4;--warn:#b7791f;--cool:#8a5fc4}
  *{margin:0;padding:0;box-sizing:border-box}
  body{background:var(--bg);color:var(--txt);
       font:14px/1.6 "Microsoft YaHei","PingFang SC",sans-serif;padding:20px}
  .wrap{max-width:1400px;margin:0 auto}
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
     padding:9px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
  td{padding:8px 10px;border-bottom:1px solid #f0f2f5;white-space:nowrap}
  tr:hover td{background:#f8faff}
  .up{color:var(--up);font-weight:bold}
  .down{color:var(--down);font-weight:bold}
  .code{font-family:Consolas,monospace;color:var(--sub)}
  .nm{font-weight:600}
  .tag{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px}
  .bar-row{display:flex;align-items:center;gap:10px;padding:6px 16px}
  .bar-row .lb{width:64px;font-size:12px;color:var(--sub);flex:none}
  .bar-row .bar{height:18px;border-radius:4px;background:#eef1f5;flex:1;position:relative}
  .bar-row .fill{display:block;height:100%;border-radius:4px;background:var(--accent);opacity:.75}
  .bar-row .num{width:52px;text-align:right;font-size:12px;font-weight:600;flex:none}
  .chips{display:flex;gap:8px;align-items:center;padding:10px 16px;border-bottom:1px solid var(--line);
         flex-wrap:wrap}
  .chip{padding:4px 12px;border:1px solid var(--line);border-radius:14px;font-size:12px;
        cursor:pointer;background:#fff;color:var(--sub);user-select:none}
  .chip.on{background:#2456c4;color:#fff;border-color:#2456c4}
  .foot{color:var(--sub);font-size:11px;line-height:1.9;padding:10px 2px}
  .warn{background:#fff8e6;border:1px solid #f0d9a8;color:#8a6212;border-radius:8px;
        padding:10px 14px;font-size:12px;margin-bottom:14px}
  .empty{padding:28px;text-align:center;color:var(--sub)}
  /* 中枢位置条：左右各 20% 留白，中段 60% 是 [ZD,ZG]，方块是现价 */
  .pbar{position:relative;width:88px;height:10px;background:#f7f9fc;
        border:1px solid #dfe5ee;border-radius:5px;
        display:inline-block;vertical-align:middle}
  .pbar .zg{position:absolute;left:20%;right:20%;top:-1px;bottom:-1px;
            background:#c3cfe0;border-radius:2px}
  .pbar .px{position:absolute;top:-3px;width:8px;height:14px;border-radius:2px;
            background:#2456c4;transform:translateX(-4px);
            box-shadow:0 0 0 1px #fff}
  .pbar .px.hi{background:#d92b2b}
  .pbar .px.lo{background:#0a9e5c}
  .dash{color:#a6afbd}
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
    return f"{x:+.{digits}f}%" if sign else f"{x:.{digits}f}%"


def _cls(v):
    if v is None:
        return ""
    return "up" if v > 0 else "down" if v < 0 else ""


def _pos_bar(r):
    """中枢位置条：ZD~ZG 映到 20%~80%，现价超出则钳到两端并变色。"""
    zg, zd, close = r.get("zg"), r.get("zd"), r.get("close")
    if not zg or not zd or not close or zg <= zd:
        return '<span class="dash">—</span>'
    if close > zg:
        left, cls = 80, "hi"
    elif close < zd:
        left, cls = 20, "lo"
    else:
        left, cls = 20 + (close - zd) / (zg - zd) * 60, ""
    return (f'<span class="pbar" title="中枢 {zd:.2f}~{zg:.2f} · 现价 {close:.2f}">'
            f'<i class="zg"></i><b class="px {cls}" style="left:{left:.0f}%"></b></span>')


def _sig(r):
    t = r.get("sig_type")
    if not t:
        return '<span class="dash">—</span>'
    age = r.get("sig_age")
    fresh = r.get("sig_fresh")
    color = "#1c2330" if fresh else "#a6afbd"
    s = f'<span style="color:{color}">{_esc(t)}</span>'
    if age is not None:
        s += f'<span class="dash"> {age}日</span>'
    return s


def _note(r):
    """备注：背驰标志 / 已兑现 / 枢已升级 / 末笔未走完。"""
    out = []
    if r.get("piv_over"):
        out.append('<span class="tag" style="background:#fdeaea;color:#b23b3b">枢已升级</span>')
    if r.get("sig_far"):
        out.append('<span class="tag" style="background:#f5f1ea;color:#8a7a5c">已兑现</span>')
    if r.get("bi_live"):
        out.append('<span class="tag" style="background:#eef1f5;color:#5a6b82">末笔未走完</span>')
    bei = r.get("bei") or ""
    if bei:
        out.append(f'<span style="color:#6b7686;font-size:12px">{_esc(bei)}</span>')
    return " ".join(out) or '<span class="dash">—</span>'


def _row(r, stage):
    pct = r.get("pct")
    trend = r.get("trend") or "—"
    tcolor = "#d92b2b" if trend == "上涨" else "#0a9e5c" if trend == "下跌" else "#5a6b82"
    bi = r.get("bi_dir")
    barrow = "↑" if bi == 1 else "↓" if bi == -1 else "—"
    return (
        f'<tr data-bucket="{_esc(r.get("bucket"))}">'
        f'<td class="code">{_esc(r.get("code"))}</td>'
        f'<td class="nm">{_esc(r.get("name")) or "—"}</td>'
        f'<td style="text-align:right">{r.get("close") or 0:.2f}</td>'
        f'<td style="text-align:right" class="{_cls(pct)}">{_pct(pct, 2, True)}</td>'
        f'<td style="text-align:right">{r.get("mv_yi") or 0:.0f}亿</td>'
        f'<td style="color:{tcolor}">{trend}</td>'
        f'<td style="text-align:right;font-family:Consolas,monospace">'
        f'{r.get("zd") or 0:.2f}~{r.get("zg") or 0:.2f}</td>'
        f'<td>{_pos_bar(r)}</td>'
        f'<td style="text-align:right" class="{_cls(r.get("dist_zg"))}">'
        f'{_pct(r.get("dist_zg"), 1, True)}</td>'
        f'<td style="text-align:right">{r.get("piv_n_bi") or 0}</td>'
        f'<td style="text-align:center">{barrow}</td>'
        f'<td>{_sig(r)}</td>'
        f'<td>{_note(r)}</td>'
        f'</tr>')


def _panel(stage, rows, total):
    name, desc, color, bg = STAGE_META[stage]
    if not rows:
        body = '<div class="empty">该档今日为空</div>'
    else:
        head = ('<tr><th>代码</th><th>名称</th><th style="text-align:right">现价</th>'
                '<th style="text-align:right">涨幅</th><th style="text-align:right">流通市值</th>'
                '<th>走势</th><th style="text-align:right">中枢 ZD~ZG</th><th>位置</th>'
                '<th style="text-align:right">距ZG</th><th style="text-align:right">枢笔</th>'
                '<th style="text-align:center">末笔</th><th>最近信号</th><th>备注</th></tr>')
        body = f"<table><thead>{head}</thead><tbody>" + \
               "".join(_row(r, stage) for r in rows) + "</tbody></table>"
    return (f'<div class="panel"><h2><span class="dot" style="background:{color}"></span>'
            f'{name} <span style="font-weight:normal;color:#6b7686;font-size:12px">'
            f'{desc}</span><span class="hint">共 {total} 只，列前 {len(rows)}</span></h2>'
            f'{body}</div>')


def _bar(label, n, tot, color="#2456c4"):
    w = (n / tot * 100) if tot else 0
    return (f'<div class="bar-row"><span class="lb">{label}</span>'
            f'<span class="bar"><i class="fill" style="width:{w:.1f}%;'
            f'background:{color}"></i></span><span class="num">{n}</span></div>')


def build(src):
    with open(src, encoding="utf-8") as f:
        j = json.load(f)
    meta = j.get("meta") or {}
    asof = j.get("data_asof") or "—"
    buckets = j.get("buckets") or {}
    run_at = j.get("run_at") or ""

    judged = meta.get("judged") or 0
    all_rows = [r for st in SHOW_STAGES for r in buckets.get(st, [])]

    # 走势类型 / 中枢位置分布：优先用 meta 里的全市场统计（judged 只）
    # 若用 all_rows 汇总，得到的是「六档内部」的分布，不是全市场，会误导。
    tre = meta.get("trend_dist") or {}
    pos = meta.get("pos_dist") or {}
    if not tre:
        for r in all_rows:
            tre[r.get("trend") or "—"] = tre.get(r.get("trend") or "—", 0) + 1
    if not pos:
        for r in all_rows:
            pos[r.get("pos") or "—"] = pos.get(r.get("pos") or "—", 0) + 1
    dist_tot = judged or len(all_rows)

    counts = meta.get("stage_counts") or {}

    cards = []
    for st in CARD_STAGES:
        name, desc, color, _ = STAGE_META[st]
        n = counts.get(st, len(buckets.get(st, [])))
        cards.append(f'<div class="card"><div class="k">{name}</div>'
                     f'<div class="v" style="color:{color}">{n}</div>'
                     f'<div class="note">{CARD_DESC.get(st, desc)}</div></div>')

    n_sell = counts.get("三卖", len(buckets.get("三卖", [])))
    n_off = counts.get("离开段", len(buckets.get("离开段", [])))
    n_dn = counts.get("中枢下", len(buckets.get("中枢下", [])))

    panels = "".join(
        _panel(st, buckets.get(st, []), counts.get(st, len(buckets.get(st, []))))
        for st in SHOW_STAGES)

    chips = ['<span class="chip on" onclick="filterBucket(\'ALL\',this)">全部</span>']
    cnt = {}
    for r in all_rows:
        b = r.get("bucket") or "未知"
        cnt[b] = cnt.get(b, 0) + 1
    for b in BUCKET_ORDER:
        if cnt.get(b):
            chips.append(f'<span class="chip" onclick="filterBucket(\'{b}\',this)">'
                         f'{b} · {cnt[b]}</span>')

    ex = (f"剔除：指数 {meta.get('ex_idx', 0)} · 北交所 {meta.get('ex_bj', 0)} · "
          f"ST/退 {meta.get('ex_st', 0)} · 股价&lt;2元 {meta.get('ex_low', 0)} · "
          f"20日均额&lt;{meta.get('min_amt', 0.5)}亿 {meta.get('ex_amt', 0)} · "
          f"数据不新鲜 {meta.get('ex_stale', 0)} · 次新/坏文件 {meta.get('ex_bad', 0)}")

    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>缠论结构池 · {asof}</title><style>{_CSS}</style></head><body><div class="wrap">
<h1>缠论结构池<span style="font-size:13px;font-weight:normal;color:#6b7686">
走势类型 · 中枢 · 三类买卖点 · 背驰</span></h1>
<div class="sub">结构基准 <b>{asof}</b> · 操作级别 日线 · 窗口 {meta.get('lookback', 500)} 根 ·
扫描 {meta.get('files', 0)} 文件 / 可判 {judged} 只 · 生成于 {_esc(run_at)}
（引擎 chanlun_signal，非形态打分）</div>

<div class="warn"><b>这是结构清单，不是选股器——回放已经做完了。</b>
2024-05→2026-11、124 个截面、48.3 万个「截面×个股」观测，T+1 开盘入场、同日全样本为基准：
<b>九个档位没有一个跑出稳定正超额</b>。按市值层内比较，九态 60 日超额全部落在 ±40bp 内，
没有一个 t 值显著为正。<b>三买 −146bp（聚类 t=−3.03）、三买待 −284bp（t=−3.67）——等回踩确认再买是负分。</b>
一买 +103bp，但 t=1.47，且靠 2024 单年（2025 年 −164bp）。中枢档 75% 是「级别升级·判定作废」，真震荡那部分 +3bp。
它现在只回答一件事：<b>这些票各自站在中枢的哪个位置、属于哪一类买卖点</b>。明细见 <code>pool_replay.py --report</code>。</div>

<div class="cards">{''.join(cards)}</div>

<div class="panel"><h2><span class="dot" style="background:#2456c4"></span>全市场结构分布
<span class="hint">分母 {dist_tot} 只（可判样本）· 下跌侧不进上方卡片：三卖 {n_sell} · 离开段 {n_off} · 中枢下 {n_dn}</span></h2>
{_bar("上涨", tre.get("上涨", 0), dist_tot, "#d92b2b")}
{_bar("下跌", tre.get("下跌", 0), dist_tot, "#0a9e5c")}
{_bar("盘整", tre.get("盘整", 0), dist_tot, "#5a6b82")}
{_bar("上沿外", pos.get("上沿外", 0), dist_tot, "#d92b2b")}
{_bar("枢内", pos.get("枢内", 0), dist_tot, "#5a6b82")}
{_bar("下沿外", pos.get("下沿外", 0), dist_tot, "#0a9e5c")}
</div>

<div class="panel"><div class="chips">{''.join(chips)}</div></div>

{panels}

<div class="foot">
<b>九态判据（全部由结构推出，不用均线）</b><br>
<b>三买待</b> = 收盘在 ZG 上方 8% 内 + 末笔向上 + 无新鲜卖点 + 不在下跌趋势。三类买点的「将买未买」。<br>
<b>三买</b> = 回踩低点 &gt; ZG 已确认，且收盘仍站 ZG 之上、未涨超 15%（涨超即「已兑现」）。<br>
<b>二买</b> = 一类买点后首回调不破极值，收盘仍守信号价。<b>一买</b> = 向下线段终结 + 段内动量衰减（底背驰）。<br>
<b>背驰</b> = 新鲜的一卖/二卖。三卖<b>不算</b>衰竭——它是下跌延续，下跌趋势里每个中枢都会出一次。<br>
<b>中枢</b> = 收盘 ∈ [ZD,ZG]；中枢延伸超 9 笔或区间宽超 30% 即「枢已升级」，ZG/ZD 不再作操作边界，
此时一律归中枢，不判上下沿。<br>
<b>三卖 / 离开段 / 中枢下</b> = 下跌侧与追高区，不进买点池。<br><br>
{ex}<br>
ZG/ZD 取中枢最初三笔的重叠区间，后续笔只延伸不修正，故延伸后会与真实震荡区有偏差——
页面「中枢 ZD~ZG」列给的是缠论定义值，判断边界可信度请看「枢已升级」标记。<br>
涨红跌绿。判定口径以 pool_state.py 为准，本页零重算。
</div>
</div><script>{_JS}</script></body></html>"""


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default=None)
    ap.add_argument("--out", dest="out", default=os.path.join(REPORTS, "pool_state.html"))
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()

    src = args.src
    if not src:
        fs = sorted(glob.glob(os.path.join(REPORTS, "pool_state_*.json")))
        if not fs:
            print("[page] 无 pool_state_*.json，先跑 pool_state.py --scan --save")
            return 1
        src = fs[-1]

    html_txt = build(src)
    os.makedirs(REPORTS, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html_txt)
    print(f"[page] {args.out}  ({len(html_txt)} bytes, 源 {os.path.basename(src)})")

    if args.open:
        try:
            os.startfile(args.out)  # noqa: S606
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
