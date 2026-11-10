# -*- coding: utf-8 -*-
"""每日结构化汇报 H5（移动端 · 分屏 · 可下钻）—— 缠论口径。

给飞书 / 微信转发用，单文件，不是长图：
  · 首屏只放全市场结构 + 五档数量 + 今日要点，一屏装得下（390×844 实测）；
  · 名单页按档位切换，一只一行；
  · 点任意一只进详情页：近 60 日走势上叠中枢上下沿，再逐条列判据。

为什么详情页不画均线：缠论看的是价格相对中枢的位置，不是均线关系。
所以图上只画两样东西——收盘价与中枢 [ZD, ZG]。价格在两条灰线之间就是在
枢内震荡，站上去就是离开上沿，掉下来就是离开下沿。

不引任何外部资源（无 CDN、无字体、无图表库），断网也能开；数据全部内联。

用法:
  python pool_report_h5.py                  # 读最近 pool_state_*.json
  python pool_report_h5.py --in X.json --out Y.html
"""

import argparse
import glob
import html
import json
import os
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, "reports")
ASHARE = os.path.join(HERE, "data", "ashare")

# 九态
STAGE_LABEL = {
    "三买待": "三买待", "三买": "三买", "二买": "二买", "一买": "一买",
    "背驰": "背驰", "中枢": "中枢", "三卖": "三卖",
    "离开段": "离开段", "中枢下": "中枢下",
}
STAGE_DESC = {
    "三买待": "离开上沿，等回踩不破 ZG",
    "三买": "回踩不破 ZG，向上趋势确认",
    "二买": "一买后首回调不破极值",
    "一买": "向下线段终结·底背驰",
    "背驰": "顶背驰，上涨结构走完",
    "中枢": "枢内震荡，等方向",
    "三卖": "跌破下沿反抽不过",
    "离开段": "已远离上沿，追高区",
    "中枢下": "向下离开中枢",
}
STAGE_COLOR = {
    # 2026-11-10 UI 重调：档位色板统一走 ui_tokens（全站单一事实源）。
    # 原值：一买洋红、背驰绿 —— 与全站九态色板不一致，作废（2026-11-10 UI 重调）。
    "三买待": "#e0874f", "三买": "#e35d5d", "二买": "#d9a13b", "一买": "#c9b23f",
    "背驰": "#b07fd8", "中枢": "#6f87a8", "三卖": "#3aa76d",
    "离开段": "#4fa8a0", "中枢下": "#8090a4",
}
# 首屏五张卡
CARD_STAGES = ["三买待", "三买", "二买", "一买", "背驰"]

# 可操作五层：与 pool_state.py --judge 的分层、飞书卡片的语言一致。
# 九态回答「结构是什么」，五层回答「现在能不能动手」——卡片走五层，
# 页面必须能给同一批票，否则点开找不到卡片上那句话说的是谁。
LAYER_META = (
    ("B", "B 可介入", "三买守位在手边（≤8%），跌破守位即认输", "#e35d5d"),
    ("A", "A 今日突破", "刚越中枢上沿，等回踩，不追", "#e8590c"),
    ("E", "E 贴沿未过", "紧贴上沿未过，埋伏不是买", "#e0b95c"),
)
LAYER_DESC, LAYER_COLOR = {}, {}
BAR_N = 60


def _newest_judge():
    """取最新一份池判据（A/B/C/D/E 五层），只认行情时点是今天的。

    隔夜残留的 judge 挂上页面，会出现「今天 B 档三只」其实是上周五的名单。
    宁可不出五层（页面退回纯九态），也不出过期情报。
    """
    try:
        fs = sorted(glob.glob(os.path.join(REPORTS, "pool_judge_*.json")))
    except OSError:
        return None
    if not fs:
        return None
    try:
        with open(fs[-1], encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    live = str(d.get("live_at") or "")[:10]
    if live and live != date.today().isoformat():
        return None
    return d


def load_kline(code, n=BAR_N):
    """近 n 日收盘与最高价；缺文件返回 None。"""
    fn = code.replace(".", "_") + ".parquet"
    p = os.path.join(ASHARE, fn)
    if not os.path.exists(p):
        return None
    try:
        df = pd.read_parquet(p, columns=["trade_date", "close", "high"])
    except Exception:
        return None
    if len(df) < n:
        return None
    df = df.sort_values("trade_date").tail(n)
    c = df["close"].to_numpy(float)
    h = df["high"].to_numpy(float)

    def _r(arr):
        return [None if not np.isfinite(x) else round(float(x), 2) for x in arr]

    return {"c": _r(c), "h": _r(h),
            "d0": int(df["trade_date"].iloc[0]),
            "d1": int(df["trade_date"].iloc[-1])}


def _p(v, d=1, sign=False):
    if v is None:
        return "—"
    x = v * 100
    return f"{x:+.{d}f}%" if sign else f"{x:.{d}f}%"


def _ev(label, actual, rule, ok):
    return {"l": label, "a": actual, "r": rule, "p": bool(ok)}


def _fetch_px(codes):
    """补取实时价。

    pool_live_*.json 的 groups 只装「异动档」（上离枢 / 三买守稳 之类，
    几十只），而本页要展示 335 只——大半不在其中。不补的话，副标题挂着
    「实时 10:20」，列表里那些行其实是 11-06 的收盘价，位置标签也跟着错。

    分批 200 只：`hq.sinajs.cn/list=` 是 GET，一次塞几百只超长会被截断，
    而且少掉的 key 不报错，只静默丢票。
    """
    root = os.path.dirname(HERE)
    p = os.path.join(root, "news_collector")
    if p not in sys.path:
        sys.path.insert(0, p)
    from quote_snapshot import fetch_quotes

    out, seen = {}, set()
    uniq = [c for c in dict.fromkeys(codes)]
    for i in range(0, len(uniq), 200):
        try:
            got = fetch_quotes(uniq[i:i + 200])
        except Exception as e:
            print(f"[h5] 取价批 {i // 200 + 1} 失败: {str(e)[:60]}")
            continue
        for q in got:
            c = str(q.get("code") or "")
            c = c[2:].upper() + "." + c[:2].upper() if c[:2] in ("sh", "sz", "bj") else c.upper()
            if q.get("price") and c not in seen:
                seen.add(c)
                out[c] = (float(q["price"]), q.get("pct"))
    return out


def _lpos(px, zd, zg, tol=0.003):
    """实时价相对中枢的位置（与 pool_state._lpos 同口径）。"""
    if not zd or not zg:
        return "无枢"
    if px > zg * (1 + tol):
        return "上沿外"
    if px < zd * (1 - tol):
        return "下沿外"
    return "枢内"


def build_evidence(r):
    """按状态生成判据清单：每条给「实测 / 规矩 / 是否成立」。"""
    st = r.get("stage")
    close = r.get("close") or 0
    zd = r.get("zd")
    zg = r.get("zg")
    pn = r.get("piv_n_bi") or 0
    ev = []

    if zd and zg:
        width = (zg - zd) / zd
        ev.append(_ev("中枢区间", f"{zd:.2f} ~ {zg:.2f}（宽 {width*100:.1f}%）",
                      "三笔重叠定 ZD/ZG", True))
        ev.append(_ev("现价位置",
                      f"{close:.2f}，距 ZG {_p(r.get('dist_zg'), 1, True)}"
                      + (f"（结构日收盘在{r['pos']}）"
                         if r.get("lpos") and r.get("pos")
                         and r["lpos"] != r["pos"] else ""),
                      "在上沿外 / 枢内 / 下沿外",
                      (r.get("lpos") or r.get("pos")) == "上沿外"))
        ev.append(_ev("中枢延伸", f"{pn} 笔",
                      "≤9 笔本级别有效，超则升级",
                      pn <= 9))
    else:
        ev.append(_ev("中枢", "未构成", "至少三笔重叠", False))

    ev.append(_ev("走势类型", r.get("trend") or "—",
                  "末两中枢同向不重叠=趋势", (r.get("trend_dir") or 0) != 0))
    bi = r.get("bi_dir")
    ev.append(_ev("末笔方向",
                  ("向上" if bi == 1 else "向下" if bi == -1 else "—")
                  + ("（未走完）" if r.get("bi_live") else ""),
                  "三买待要求末笔向上", bi == 1))

    if r.get("sig_type"):
        age = r.get("sig_age")
        fresh = "新鲜" if r.get("sig_fresh") else "已过期"
        ev.append(_ev("最近买卖点",
                      f"{r['sig_type']} @{r.get('sig_price') or 0:.2f}"
                      f"（{age} 日前·{fresh}）",
                      "60 个交易日内有效", bool(r.get("sig_fresh"))))
        g = r.get("sig_gain")
        if g is not None:
            ok = -0.05 <= g <= 0.15
            ev.append(_ev("买点后表现", _p(g, 1, True),
                          "跌超 5% 认输 / 涨超 15% 算已兑现", ok))
    else:
        ev.append(_ev("最近买卖点", "无", "—", False))

    bei = r.get("bei") or ""
    if bei:
        ev.append(_ev("背驰标记", bei, "力度或面积衰减", True))
    if r.get("piv_over"):
        ev.append(_ev("中枢级别", "已升级", "延伸超 9 笔或区间宽超 30%", True))
    return ev


def _svg(r, k):
    """走势图：收盘线 + 中枢上下沿两条灰线 + 现价点。"""
    if not k or not k.get("c"):
        return '<div class="nodata">无走势数据</div>'
    c = [x for x in k["c"] if x is not None]
    if not c:
        return '<div class="nodata">无走势数据</div>'
    # 实时价接在最后一根收盘之后：结构只到 11-06，价格是当下，
    # 两者时点不同，图上必须分开——所以最后一个点单独标注，不混进收盘线。
    lpx = r.get("lpx")
    if lpx:
        c = list(c) + [float(lpx)]
    zd, zg = r.get("zd"), r.get("zg")
    vals = list(c) + [v for v in (zd, zg) if v]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.12 or 0.1
    lo, hi = lo - pad, hi + pad
    W, H, PL, PR, PT, PB = 320, 150, 6, 44, 8, 16
    iw, ih = W - PL - PR, H - PT - PB

    def X(i):
        return PL + i / max(len(c) - 1, 1) * iw

    def Y(v):
        return PT + (hi - v) / (hi - lo) * ih

    pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(c))
    out = [f'<svg viewBox="0 0 {W} {H}" class="kline">']
    # 中枢区间带 + 上下沿
    if zd and zg:
        y_zg, y_zd = Y(zg), Y(zd)
        out.append(f'<rect x="{PL}" y="{y_zg:.1f}" width="{iw}" height="{max(y_zd-y_zg,1):.1f}" '
                   f'fill="#7b8698" opacity="0.10"/>')
        for y, lab, col in ((y_zg, f"ZG {zg:.2f}", "#c9b184"), (y_zd, f"ZD {zd:.2f}", "#c9b184")):
            out.append(f'<line x1="{PL}" y1="{y:.1f}" x2="{PL+iw}" y2="{y:.1f}" '
                       f'stroke="{col}" stroke-width="1" stroke-dasharray="4 3"/>')
            out.append(f'<text x="{PL+iw+4}" y="{y+3:.1f}" font-size="9" fill="{col}">{lab}</text>')
    out.append(f'<polyline points="{pts}" fill="none" stroke="#5b9bd5" stroke-width="1.6"/>')
    # 现价
    py = Y(c[-1])
    # 标签避让：实时价常常就贴在 ZG/ZD 上，同一位置画两行字会叠成一团
    ty = py + 3
    for ref in (Y(zg) if zg else None, Y(zd) if zd else None):
        if ref is not None and abs(ty - ref) < 11:
            ty = ref + 13 if ty >= ref else ref - 5
    ty = min(max(ty, PT + 8), H - PB - 2)
    out.append(f'<circle cx="{X(len(c)-1):.1f}" cy="{py:.1f}" r="3.2" fill="#e35d5d"/>')
    out.append(f'<text x="{PL+iw+4}" y="{ty:.1f}" font-size="9" fill="#e35d5d" '
               f'font-weight="bold">{c[-1]:.2f}</text>')
    out.append(f'<text x="{PL}" y="{H-4}" font-size="8" fill="#7b8698">{k["d0"]}</text>')
    out.append(f'<text x="{PL+iw-34}" y="{H-4}" font-size="8" fill="#7b8698">'
               f'{"实时" if lpx else k["d1"]}</text>')
    out.append("</svg>")
    return "".join(out)


def _trend_text(data, counts, tre, posd):
    """今日要点：从结构数字直接生成，不编故事。"""
    judged = data.get("meta", {}).get("judged") or 0
    up, dn = tre.get("上涨", 0), tre.get("下跌", 0)
    below = posd.get("下沿外", 0)
    n3p = counts.get("三买待", 0)
    n3 = counts.get("三买", 0)
    nbei = counts.get("背驰", 0)

    if not judged:
        return "今日无有效样本。"
    below_share = below / judged * 100
    mood = "弱势" if below_share > 50 else "中性" if below_share > 35 else "偏强"
    s = ("<b>回放已做完</b>（2024-05→2026-11，48.3 万观测）：九态无一跑出稳定正超额。"
         "三买 −146bp（t=−3.03）、三买待 −284bp（t=−3.67）——"
         "<b>等回踩确认再买是负分</b>；一买 +103bp 但 t=1.47，只靠 2024 一年。")
    s += (f" 今日 {judged} 只，下跌趋势 {dn}、枢下 {below}（{below_share:.0f}%），"
          f"结构{mood}。")
    if n3p:
        s += f" 三买待 {n3p} 只，位置在「等回踩」，但上面那行说这位置不赚钱。"
    else:
        s += " 今日无三买待：没票处在「离开上沿、等回踩」。"
    return s


_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def _pack_idx(lst):
    """板块下标压成定长 base36（每个 2 字符）。

    「个股→板块」这张表 5,595 只票 × 最多 8 个板块，直接塞数字数组要多占
    60KB。465 个板块 < 36²=1296，两位够用——所以这里断言着，将来板块数
    越过 1295 会直接报错，而不是静默截断出错的板块名。
    """
    for i in lst:
        if not 0 <= i < 1296:
            raise ValueError("板块下标越界：%d（base36 两位上限 1295）" % i)
    return "".join(_B36[i // 36] + _B36[i % 36] for i in lst)


def _load_sector_map(asof):
    """(板块清单, 个股→板块下标) —— 个股页反查「它在哪个板块、那个板块什么结构」。

    只认**同一个交易日**的板块落盘。板块结构和个股结构不是同一天时，并排
    显示就是在制造「板块三买 + 个股三卖」这种根本没同时发生过的共振。所以
    asof 对不上就整块不要，宁可空着，也不给一个看起来有理、其实是错配的结论。

    反向索引的取舍：行业（I）全留，一只票通常 1~2 个；概念每票中位 9 个、
    最多 48 个，全留会把「所属板块」变成一堵墙。只取成分数最少的 4 个——
    小概念更精准，CPO 比「融资融券」有信息量。
    """
    p = os.path.join(REPORTS, "sector_state_%s.json" % asof)
    if not os.path.exists(p):
        return [], {}
    try:
        with open(p, encoding="utf-8") as f:
            sj = json.load(f)
    except Exception as e:
        print(f"[h5] 板块落盘读不了：{e}")
        return [], {}
    if str(sj.get("asof") or "") != str(asof):
        print(f"[h5] 板块落盘 asof={sj.get('asof')} 与个股 asof={asof} 不同天，"
              f"不挂所属板块（跨天配对会造出假共振）")
        return [], {}

    rows = [r for r in (sj.get("sectors") or []) if r.get("code")]
    at = {r["code"]: i for i, r in enumerate(rows)}
    secL = [{"c": r["code"], "n": r.get("name") or "",
             "t": "I" if (r.get("type") or "") == "I" else "N",
             "st": r.get("stage") or "", "po": r.get("pos") or "",
             "r20": r.get("r20"), "m": r.get("n_mem") or 999}
            for r in rows]

    fs = sorted(glob.glob(os.path.join(HERE, "data", "tdx", "ts_ths_member",
                                       "part_*.parquet")))
    if not fs:
        return secL, {}
    try:
        mem = pd.concat([pd.read_parquet(f, columns=["ts_code", "con_code"])
                         for f in fs], ignore_index=True)
    except Exception as e:
        print(f"[h5] 成分表读不了：{e}")
        return secL, {}
    mem = mem[mem["ts_code"].astype(str).isin(at)]
    out = {}
    for con, sub in mem.groupby("con_code"):
        ind, con_ = [], []
        for sc in sub["ts_code"].astype(str):
            i = at.get(sc)
            if i is None:
                continue
            if secL[i]["t"] == "I":
                ind.append(i)
            else:
                con_.append((secL[i]["m"], i))
        con_.sort()
        lst = ind[:4] + [i for _, i in con_[:4]]
        if lst:
            out[str(con)] = _pack_idx(lst)
    return secL, out


_BUILD = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>缠论结构日报 {asof}</title><style>
*{{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}}
body{{background:#0d1117;color:#e6edf3;
     font:14px/1.5 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
     max-width:430px;margin:0 auto;min-height:100vh}}
/* PC 上手机版两侧全空（判例 UI-审计5）：≥768px 放宽到 660px 浮起，
   手机端不受影响；再宽就破坏竖屏卡片流，不跟 */
@media (min-width:768px){{
  html{{background:#070a0f}}
  body{{max-width:660px;box-shadow:0 0 44px rgba(0,0,0,.5);
       border-left:1px solid #1d2530;border-right:1px solid #1d2530}}}}
.screen{{display:none;min-height:100vh;padding:10px 14px 4px}}
.screen.on{{display:block}}
.hd{{display:flex;align-items:baseline;gap:8px;margin-bottom:2px}}
.hd h1{{font-size:18px;font-weight:700}}
.hd .d{{font-size:11px;color:#7b8698}}
.sub{{font-size:11px;color:#7b8698;margin-bottom:10px}}
.hero{{background:linear-gradient(135deg,#18243a,#1d2c47);border-radius:12px;
      padding:12px 14px;color:#fff;margin-bottom:10px}}
.hero .t{{font-size:11px;opacity:.75}}
.hero .m{{font-size:15px;font-weight:700;margin:3px 0 8px}}
.hb{{display:flex;gap:6px;margin-top:6px}}
.hb div{{flex:1;text-align:center;background:rgba(255,255,255,0.045);border-radius:7px;padding:5px 2px}}
.hb .n{{font-size:15px;font-weight:700}}
.hb .l{{font-size:10px;opacity:.7}}
.cards{{display:grid;grid-template-columns:repeat(5,1fr);gap:6px;margin-bottom:10px}}
.card{{background:#131a24;border-radius:9px;padding:8px 3px;text-align:center;
      box-shadow:0 1px 2px rgba(20,30,50,.05)}}
.card .k{{font-size:10px;color:#7b8698;white-space:nowrap}}
.card .v{{font-size:19px;font-weight:700;line-height:1.25}}
.brief{{background:#2a2312;border:1px solid #4a3d1c;border-radius:9px;
       padding:8px 11px;font-size:11px;line-height:1.56;color:#7a5a12;margin-bottom:9px}}
.sec{{font-size:11px;color:#7b8698;margin:2px 2px 6px;display:flex;justify-content:space-between}}
/* 板块平台入口：整个站只有这一条路通向板块页（个股页 → 所属板块 那条要先进详情，
   首页进来的人看不到）。线上是两个文件同目录部署，跳转带 sharecode。 */
.sgo{{background:#131a24;border-radius:11px;padding:11px 13px;margin-bottom:9px;
     display:flex;align-items:center;justify-content:space-between;
     font-size:12.5px;color:#1e2a3a;box-shadow:0 1px 2px rgba(20,30,50,.05);cursor:pointer}}
.sgo .m{{font-size:10.5px;color:#7b8698;margin-top:2px}}
.sgo .ar{{color:#b8c0cc;font-size:15px}}
.row{{background:#131a24;border-radius:9px;padding:7px 11px;margin-bottom:6px;
     display:flex;align-items:center;gap:9px;box-shadow:0 1px 2px rgba(20,30,50,.05)}}
.row .l{{flex:1;min-width:0}}
.row .nm{{font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.row .mt{{font-size:10.5px;color:#7b8698;margin-top:2px}}
.row .r{{text-align:right;flex:none}}
.row .px{{font-size:14px;font-weight:700}}
.row .dg{{font-size:10.5px;color:#7b8698}}
.up{{color:#e35d5d}}.down{{color:#3aa76d}}
.arw{{color:#2b3949;font-size:16px;flex:none}}
.sech{{font-size:11px;color:#7b8698;margin:13px 2px 6px;font-weight:600}}
.sech .warn{{font-weight:400;color:#b5761a;margin-left:6px}}
.secw{{background:#131a24;border-radius:11px;overflow:hidden;
      box-shadow:0 1px 2px rgba(20,30,50,.05)}}
.srow{{display:flex;align-items:center;gap:7px;padding:9px 12px;
      border-bottom:1px solid #0d1117;text-decoration:none;color:inherit}}
.srow:last-child{{border:0}}
.srow .sn{{flex:1;min-width:0;font-size:12.5px;white-space:nowrap;
          overflow:hidden;text-overflow:ellipsis}}
.srow .tt{{font-size:9px;padding:1px 4px;border-radius:3px;background:#0d1117;
          color:#7b8698;flex:none}}
.srow .st2{{font-size:11px;flex:none}}
.srow .sr{{font-size:11px;color:#7b8698;flex:none;font-variant-numeric:tabular-nums}}
.srow .ar{{color:#2b3949;font-size:15px;flex:none}}
.srow:active{{background:#0f1620}}
.foot{{font-size:10px;color:#7b8698;line-height:1.6;margin-top:5px}}
details.rule{{background:#131a24;border-radius:9px;padding:9px 11px;font-size:11px;
             line-height:1.8;color:#7b8698;margin-top:10px}}
details.rule summary{{cursor:pointer;color:#4a5568;font-weight:600;list-style:none}}
details.rule summary::-webkit-details-marker{{display:none}}
details.rule summary:after{{content:" ⌄";color:#7b8698}}
details.rule[open] summary:after{{content:" ⌃"}}
.back{{display:flex;align-items:center;gap:6px;margin-bottom:10px;font-size:13px;color:#5b9bd5}}
.back span{{font-size:17px}}
.dt-hd{{background:#131a24;border-radius:11px;padding:13px 14px;margin-bottom:9px}}
.dt-hd .nm{{font-size:19px;font-weight:700}}
.dt-hd .cd{{font-size:11px;color:#7b8698;margin-top:2px}}
.dt-hd .px{{font-size:27px;font-weight:700;margin-top:5px;line-height:1.15}}
.dt-hd .ch{{font-size:12px;font-weight:600;margin-left:7px}}
.tag{{display:inline-block;padding:1px 8px;border-radius:9px;font-size:10.5px;
     margin-top:6px;margin-right:5px}}
.kline{{width:100%;height:auto;display:block}}
.kbox{{background:#131a24;border-radius:11px;padding:9px 6px 4px;margin-bottom:9px}}
.nodata{{padding:24px;text-align:center;color:#7b8698;font-size:12px}}
.more{{background:#131a24;border-radius:11px;padding:11px 13px;font-size:12.5px;
      color:#7b8698;margin-bottom:8px;display:flex;justify-content:space-between;
      align-items:center;cursor:pointer}}
.more b{{color:#e6edf3;font-weight:600;font-size:13px}}
.more .ar{{transition:transform .18s;display:inline-block}}
.more.on .ar{{transform:rotate(180deg)}}
.evs{{background:#131a24;border-radius:11px;overflow:hidden;margin-bottom:9px}}
.ev{{padding:9px 12px;border-bottom:1px solid #0d1117}}
.ev:last-child{{border-bottom:none}}
.ev .top{{display:flex;align-items:center;gap:7px}}
.ev .lb{{font-size:12px;font-weight:600;flex:1}}
.badge{{font-size:10px;padding:1px 7px;border-radius:8px;flex:none}}
.badge.y{{background:#101d2e;color:#5b9bd5}}
.badge.n{{background:#0d1117;color:#7b8698}}
.ev .ac{{font-size:11px;color:#4a5568;margin-top:3px;word-break:break-all}}
.ev .ru{{font-size:10px;color:#7b8698;margin-top:1px}}
</style></head><body>

<div class="screen on" id="s-home">
  <div class="hd"><h1>缠论结构日报</h1><span class="d">{asof}</span></div>
  <div class="sub">{sub_line}</div>

  <div class="sgo" onclick="goSectorHome()">
    <div><div>板块操作平台</div>
      <div class="m">市 → 板块 → 个股 · 板块位置 × 成分买点占比</div></div>
    <span class="ar">›</span>
  </div>

  <div class="hero">
    <div class="t">全市场结构</div>
    <div class="m">{mood}</div>
    <div class="hb">
      <div><div class="n">{n_up}</div><div class="l">上涨走势</div></div>
      <div><div class="n">{n_dn}</div><div class="l">下跌走势</div></div>
      <div><div class="n">{n_below}</div><div class="l">枢下</div></div>
    </div>
  </div>

  <div class="cards">{cards}</div>

  <div class="brief">{brief}</div>

  <div class="sec"><span>今日判断</span><span>{layer_note}</span></div>
  <div id="rows-home"></div>

  <div class="more" id="more-tog" onclick="toggleMore()">
    <span><b>结构全貌（九态）</b> <span id="more-sub">按结构分类，不是买入清单</span></span>
    <span class="ar">⌄</span>
  </div>
  <div id="rows-more" style="display:none"></div>

  <details class="rule"><summary>口径与剔除规则</summary>
    <b>三买待</b>＝收盘在 ZG 上方 8% 内＋末笔向上＋无新鲜卖点＋不在下跌趋势。<br>
    <b>三买</b>＝回踩低点&gt;ZG 已确认，未涨超 15%。<b>二买</b>＝一买后回调不破极值。<br>
    <b>一买</b>＝向下线段终结·动量衰减（底背驰）。<br>
    <b>背驰</b>＝新鲜的一卖/二卖。三卖不算衰竭，那是下跌延续。<br>
    中枢延伸超 9 笔或区间宽超 30% 即「已升级」，ZG/ZD 不再作操作边界。<br>
    已剔除 ST/退市、股价&lt;{minp}元、20 日均额&lt;{minamt}亿、次新、数据不新鲜、指数、北交所。<br>
    <b>回放结论（2026-11-09）</b>：124 个截面、48.3 万观测，T+1 开盘入场、同日全样本为基准。
    按市值层内比较，九态 60 日超额全部落在 ±40bp 内、无一个 t 值显著为正 → 结构本身不提供选股超额。<br>
    三买 −146bp（t=−3.03）、三买待 −284bp（t=−3.67，逐日等权）；
    一买 +103bp（t=1.47，2024 年 +493bp、2025 年 −164bp，单年现象）；
    中枢档 75% 是「级别升级·判定作废」，真震荡那部分 +3bp，等于零。<br>
    <b>所属板块</b>只挂同一交易日的：板块结构和个股结构不是同一天时，这一栏
    整块不显示——跨天配对会造出「板块三买 + 个股三卖」这种没同时发生过的共振。
    一只票最多列 4 个行业 + 4 个概念（按成分数从小到大），不是全部标签。
    这一栏是<b>位置描述</b>，不是看涨依据：板块层回放（2.2 年 4.7 万观测）里买点类
    20 日超额 <b>−13bp</b>（t=−0.58），强度分层<b>没有单调性</b>。<br>
    涨红跌绿。生成于 {run_at}。
  </details>
  <div class="foot">{engine_note}</div>
</div>

<div class="screen" id="s-list">
  <div class="back" onclick="go('s-home')"><span>‹</span>返回</div>
  <div class="hd"><h1 id="l-title">名单</h1><span class="d" id="l-count"></span></div>
  <div class="sub" id="l-desc"></div>
  <div id="rows-list"></div>
</div>

<div class="screen" id="s-detail">
  <div id="d-body"></div>
</div>

<script>
var D = {data_js};
var ORDER = {order_js};
var FROM = {{kind:'home', st:null}};   // 详情页的返回目标，见 go()

function go(id){{
  // 返回目标记在 FROM 里：从首页点进详情、从名单点进详情、从板块页跳进来，
  // 三条来路的"返回"去的不是一个地方。原来详情页的返回条是写死的「返回名单」，
  // 从首页点一只票再点它，会落到一个从没打开过、标题为空的名单页。
  if(id==='s-home') FROM = {{kind:'home', st:null}};
  document.querySelectorAll('.screen').forEach(function(s){{s.classList.remove('on')}});
  document.getElementById(id).classList.add('on');
  window.scrollTo(0,0);
}}

function findRow(code){{
  for(var st in D){{
    if(!Array.isArray(D[st])) continue;
    for(var i=0;i<D[st].length;i++) if(D[st][i].code===code) return D[st][i];
  }}
  return null;
}}

function rowHtml(r){{
  var c = r.pct>0?'up':(r.pct<0?'down':'');
  /* 盘中跌破守位：这一行原来是「距ZG +5.4%」，看着一切正常，
     但档位是结构日收盘判的。破位改成红字「破守位 −2.1%」，两种信息不能并排装没事。 */
  var dg, dgLb, dgc;
  if(r.broke!=null){{ dgLb='破守位'; dg=(r.broke*100).toFixed(1)+'%'; dgc='down'; }}
  else {{ dgLb='距ZG'; dg = r.dist_zg===null?'—':(r.dist_zg*100).toFixed(1)+'%';
          dgc = r.dist_zg>0?'up':(r.dist_zg<0?'down':''); }}
  return '<div class="row" onclick="goDetail(\\''+r.code+'\\')">'
    + '<div class="l"><div class="nm">'+r.name
    + (r.broke!=null?' <span style="font-size:10px;color:#e8807f">盘中失守</span>':'')+'</div>'
    + '<div class="mt">'+r.code+' · '+(r.mv_yi||0).toFixed(0)+'亿 · '
    + (r.trend||'')+' · 枢'+ (r.piv_n_bi||0) +'笔</div></div>'
    + '<div class="r"><div class="px '+c+'">'+r.close.toFixed(2)
    + '<span style="font-size:11px"> '+(r.pct>0?'+':'')+(r.pct*100).toFixed(2)+'%</span></div>'
    + '<div class="dg">'+dgLb+' <span class="'+dgc+'">'+dg+'</span></div></div>'
    + '<div class="arw">›</div></div>';
}}

function isLayer(st){{
  for(var k in D.layerMap) if(D.layerMap[k]===st) return true;
  return false;
}}

function rowHome(st){{
  var arr = D[st]||[];
  return '<div class="row" onclick="goList(\\''+st+'\\')" style="padding:11px 13px">'
    + '<div class="l"><div class="nm" style="font-size:13px">'+st+'</div>'
    + '<div class="mt">'+ (D.desc[st]||'') +'</div></div>'
    + '<div class="r"><div class="px" style="color:'+(D.color[st]||'#e6edf3')+'">'
    + (D.counts[st]||arr.length) +' 只</div></div>'
    + '<div class="arw">›</div></div>';
}}

function renderHome(){{
  /* 五层（今日判断）直接铺开，九态收进折叠区——九态是结构全貌，
     一屏装不下，但它不是每天要看的那一眼。 */
  D.layerMap = D.layerMap || {{}};
  var h='', m='';
  ORDER.forEach(function(st){{
    if(!((D[st]||[]).length)) return;
    if(isLayer(st)) h += rowHome(st); else m += rowHome(st);
  }});
  document.getElementById('rows-home').innerHTML = h
    || '<div class="nodata">五层全空，今天没有可操作位置</div>';
  document.getElementById('rows-more').innerHTML = m;
}}

function toggleMore(){{
  var m = document.getElementById('rows-more');
  var t = document.getElementById('more-tog');
  var open = m.style.display === 'none';
  m.style.display = open ? 'block' : 'none';
  t.classList.toggle('on', open);
}}

/* 深链参数只解析一次：飞书卡片、板块页都往这里带参数。 */
var Q = (function(){{
  var s = (location.search||'').replace(/^\\?/,'')
        || (location.hash||'').replace(/^#/,'');
  var o = {{}};
  if(!s) return o;
  s.split('&').forEach(function(kv){{
    if(!kv) return;
    var i = kv.indexOf('='); if(i<0) return;
    o[decodeURIComponent(kv.slice(0,i))] = decodeURIComponent(kv.slice(i+1));
  }});
  return o;
}})();

function eh(s){{
  return String(s==null?'':s).replace(/[&<>"]/g, function(m){{
    return {{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[m];
  }});
}}

/* 站内跳转必须把 sharecode 带过去。
   线上站点是**逐请求**校验 sharecode 的：不带就 403，而且不是 404 ——
   浏览器会显示一张「链接不完整」的说明页。这个坑在本地 file:// 完全复现不出来，
   因为 file:// 没有鉴权层；只有发布出去才看得见。所以统一走 withSC() 拼链接，
   首次进来时把 code 记进 sessionStorage，后面哪个链接漏了也能补上。 */
var SC = (function(){{
  var m = (location.search||'').match(/[?&]sharecode=([^&#]+)/);
  if(m){{ try{{ sessionStorage.setItem('pool_sc', m[1]); }}catch(e){{}} return m[1]; }}
  try{{ return sessionStorage.getItem('pool_sc') || ''; }}catch(e){{ return ''; }}
}})();
function withSC(u){{
  if(!SC) return u;                       // 本地 file:// 打开：原样返回
  var i = u.indexOf('#'), hash = i>=0 ? u.slice(i) : '';
  if(i>=0) u = u.slice(0,i);
  if(/[?&]sharecode=/.test(u)) return u + hash;
  return u + (u.indexOf('?')>=0 ? '&' : '?') + 'sharecode=' + encodeURIComponent(SC) + hash;
}}

function unpackIdx(s){{
  var A = "0123456789abcdefghijklmnopqrstuvwxyz", o = [];
  for(var i=0;i+1<s.length;i+=2)
    o.push(A.indexOf(s.charAt(i))*36 + A.indexOf(s.charAt(i+1)));
  return o;
}}

/* 所属板块：个股页 → 板块页。
   给的是「这只票在哪些板块里、那些板块现在什么结构」——同一只票在行业板块
   是三买、在某个概念里是三卖，这种情况很常见，只看个股结构看不到。 */
function sectorBlock(code, name){{
  var of = (D.sectorOf||{{}})[code], SL = D.sectorL||[];
  if(!of || !of.length) return '';
  if(typeof of === 'string') of = unpackIdx(of);
  if(!of || !of.length) return '';
  var h = '<div class="sech">所属板块 · 点进去看板块结构'
        + '<span class="warn">位置描述，不是看涨依据</span></div><div class="secw">';
  for(var i=0;i<of.length;i++){{
    var s = SL[of[i]]; if(!s) continue;
    var col = D.color[s.st] || '#7b8698';
    h += '<a class="srow" href="' + withSC('sector_platform.html?sector='
       + encodeURIComponent(s.c)
       + '&from=' + encodeURIComponent(code)
       + '&fromn=' + encodeURIComponent(name||'')) + '">'
       + '<span class="tt">' + (s.t==='I'?'行业':'概念') + '</span>'
       + '<span class="sn">' + eh(s.n) + '</span>'
       + '<span class="st2" style="color:' + col + '">' + eh(s.st||'—') + '</span>'
       + '<span class="sr">' + (s.r20==null?'—'
           :((s.r20>0?'+':'')+(s.r20*100).toFixed(1)+'%')) + '</span>'
       + '<span class="ar">›</span></a>';
  }}
  return h + '</div>';
}}

/* 从板块页点过来、但这只票没内嵌进本页——必须说清楚是「没画进来」，
   不是「没判过」。这两件事在结论上差得很远。 */
function showMiss(code){{
  var a = (D.all||{{}})[code];
  CUR_CODE = code;
  var h = backBar()
        + '<div class="dt-hd"><div class="nm">' + eh(a && a.nm ? a.nm : code) + '</div>'
        + '<div class="cd">' + eh(code) + ' · 本页没有内嵌它的走势与证据</div></div>';
  if(a){{
    var sn = (D.shown||{{}})[a.st] || 0;
    h += '<div class="kbox"><div class="nodata">它在 <b>' + eh(a.st) + '</b> 档，'
       + '是这一档的第 <b>' + a.k + '</b> 只（该档共 ' + a.n + ' 只）。<br>'
       + '本页每档只内嵌前 ' + sn + ' 只，所以没把它画进来——'
       + '<b>不是没判过，是没画进这一页</b>。<br>'
       + '完整明细在 reports/pool_state_*.json。</div></div>'
       + '<div class="secw"><a class="srow" href="#" onclick="goList(\\'' + a.st
       + '\\');return false"><span class="sn">去看「' + eh(a.st) + '」这一档的列表</span>'
       + '<span class="ar">›</span></a></div>';
  }} else {{
    h += '<div class="kbox"><div class="nodata">当日池子没有判过这只票。'
       + '可能是停牌、成交额低于门槛，或上市不满所需的 K 线根数。</div></div>';
  }}
  document.getElementById('d-body').innerHTML = h;
  go('s-detail');
}}

function goList(st){{
  FROM = {{kind:'list', st: st}};
  document.getElementById('l-title').textContent = st;
  document.getElementById('l-desc').textContent = D.desc[st]||'';
  var arr = D[st]||[];
  var tot = D.counts[st]||arr.length, sn = (D.shown&&D.shown[st])||arr.length;
  document.getElementById('l-count').textContent =
    '共 '+tot+' 只' + (sn<tot ? '（本页列前 '+sn+' 只，全量见 pool_state_*.json）' : '');
  document.getElementById('rows-list').innerHTML = arr.length
    ? arr.map(rowHtml).join('')
    : '<div class="nodata">该档今日为空</div>';
  go('s-list');
}}

var CUR_CODE = null;

/* 详情页顶部那条返回：来路不同，去向不同。 */
function backBar(){{
  if(Q.ref)
    return '<div class="back" onclick="goSectorBack()"><span>&#8249;</span>返回 '
      + eh(Q.sname || '板块') + '</div>';
  if(FROM.kind==='list' && FROM.st)
    return '<div class="back" onclick="goList(\\'' + FROM.st
      + '\\')"><span>&#8249;</span>返回名单</div>';
  return '<div class="back" onclick="go(\\'s-home\\')"><span>&#8249;</span>返回</div>';
}}

/* 从板块页点进来的，返回箭头要指回那个板块——不然用户退出去只剩首页，
   早上看到的「PCB 概念四只逆势买点」这条线索就断了。 */
function goSectorHome(){{
  location.href = withSC('sector_platform.html');
}}

function goSectorBack(){{
  if(!Q.ref){{ go('s-home'); return; }}
  var nm = '';
  var r = CUR_CODE && findRow(CUR_CODE);
  if(r) nm = r.name || '';
  location.href = withSC('sector_platform.html?sector=' + encodeURIComponent(Q.ref)
    + '&from=' + encodeURIComponent(CUR_CODE||'')
    + '&fromn=' + encodeURIComponent(nm));
}}

function goDetail(code){{
  var r = findRow(code);
  if(!r){{ return showMiss(code); }}
  CUR_CODE = code;
  var c = r.pct>0?'up':(r.pct<0?'down':'');
  var h = backBar();
  h += '<div class="dt-hd"><div class="nm">'+r.name+'</div>'
    + '<div class="cd">'+r.code+' · '+(r.mv_yi||0).toFixed(0)+'亿 · '+(r.trend||'')+'</div>'
    + '<div class="px '+c+'">'+r.close.toFixed(2)
    + '<span class="ch">'+(r.pct>0?'+':'')+(r.pct*100).toFixed(2)+'%</span></div>'
    + '<div><span class="tag" style="background:'+(D.color[r.stage]||'#1f2937')+'18;color:'
    + (D.color[r.stage]||'#7b8698')+'">'+r.stage+'</span>'
    + '<span class="tag" style="background:#0d1117;color:#7b8698">'
    + (r.lpos||r.pos||'')+(r.lpos&&r.pos&&r.lpos!==r.pos?'·结构日'+r.pos:'')+'</span>'
    + (r.lpx?'<span class="tag" style="background:#2b1618;color:#e8807f">实时价</span>':'')
    + (r.broke!=null?'<span class="tag" style="background:#e8807f;color:#fff">盘中失守 '
       +(r.broke*100).toFixed(1)+'%</span>':'')
    + (r.piv_over?'<span class="tag" style="background:#2b1618;color:#e8807f">枢已升级</span>':'')
    + '</div></div>';
  h += '<div class="kbox">'+ (r.svg||'<div class="nodata">无走势数据</div>') +'</div>';
  h += '<div class="evs">';
  (r.ev||[]).forEach(function(e){{
    h += '<div class="ev"><div class="top"><div class="lb">'+e.l+'</div>'
      + '<div class="badge '+(e.p?'y':'n')+'">'+(e.p?'成立':'未成立')+'</div></div>'
      + '<div class="ac">'+e.a+'</div><div class="ru">规矩：'+e.r+'</div></div>';
  }});
  h += '</div>';
  h += sectorBlock(code, r.name);
  h += '<div class="foot">图上灰带为中枢 [ZD, ZG]，虚线为上下沿；蓝线为收盘价，红点为现价'
    + (r.lpx?'（末点为实时价，结构只到 '+ (r.struct_asof||'上一交易日') +' 收盘）':'') + '。'
    + '价格在带内＝枢内震荡，站上上沿＝离开上沿，跌破下沿＝向下离开。</div>';
  document.getElementById('d-body').innerHTML = h;
  go('s-detail');
}}

renderHome();
/* 深链：飞书卡片带 ?layer=B 或 ?code=600419.SH 或 ?code=X&ref=885959.TI。
   卡片说「B 可介入 3 只」，点进去就该是那三只，不再让人在首页自己翻。
   带 code 时不再先验 findRow——不在内嵌范围内也要给出交代（showMiss），
   静默停在首页等于告诉用户「没这只票」。 */
(function(){{
  if(Q.code) {{ goDetail(Q.code); return; }}
  var st = (Q.layer && D.layerMap && D.layerMap[Q.layer]) || Q.stage;
  if(st && D[st]) goList(st);
}})();
</script></body></html>"""


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default=None)
    ap.add_argument("--live", dest="live", default="auto",
                    help="叠加实时价：auto=用今天的 pool_live_*.json（有则用）；off=不叠")
    ap.add_argument("--per-stage", dest="per_stage", type=int, default=80,
                    help="每档最多嵌多少只明细（全量在 pool_state_*.json 里，"
                         "手机上嵌全量会让页面到 3MB 打不开）")
    ap.add_argument("--out", dest="out", default=os.path.join(REPORTS, "pool_report_h5.html"))
    args = ap.parse_args()

    src = args.src
    if not src:
        fs = sorted(glob.glob(os.path.join(REPORTS, "pool_state_*.json")))
        if not fs:
            print("[h5] 无 pool_state_*.json，先跑 pool_state.py --scan --save")
            return 1
        src = fs[-1]

    with open(src, encoding="utf-8") as f:
        j = json.load(f)
    meta = j.get("meta") or {}
    asof = j.get("data_asof") or "—"
    counts = meta.get("stage_counts") or {}
    tre = meta.get("trend_dist") or {}
    posd = meta.get("pos_dist") or {}
    judged = meta.get("judged") or 0
    lookback = meta.get("lookback", 500)

    # ── 实时叠加 ─────────────────────────────────────────
    # 只叠价格，不动结构：笔与中枢要收盘价确认，盘中重算会造出假笔假中枢。
    # 页面因此有两个时点：结构基准（最近交易日收盘）+ 价格时点（被点到的时刻）。
    lv, lmap = None, {}
    if args.live != "off":
        lf = sorted(glob.glob(os.path.join(REPORTS, "pool_live_*.json")))
        if lf:
            with open(lf[-1], encoding="utf-8") as f:
                cand = json.load(f)
            # 只认当天的：否则明早直接跑，会把昨天 14:55 的价当"实时"发出去
            if str(cand.get("live_at") or "")[:10] == date.today().isoformat():
                lv = cand
                for grp in (lv.get("groups") or {}).values():
                    for r in grp:
                        if r.get("lpx"):
                            lmap[r["code"]] = (float(r["lpx"]), r.get("lpct"))
            else:
                print(f"[h5] 最新实时文件 {os.path.basename(lf[-1])} 的报价时点 "
                      f"{cand.get('live_at')} 不是今天，跳过不叠（避免把隔夜价标成实时）")
        elif args.live not in ("auto", "off"):
            print(f"[h5] --live {args.live} 指定了文件但读不到")

    # 分档数据 + 详情（含 SVG 与证据）
    data = {}
    shown = {}
    allidx = {}
    order = [st for st in CARD_STAGES if (counts.get(st) or len(j["buckets"].get(st, [])))]

    # 页面展示的每一只都要有当下价。pool_live 只覆盖异动档，剩下的在这里补——
    # 否则「实时 10:20」这个标题对大部分行是假的，位置标签也跟着错。
    px_clock = ""      # 补取成功时的钟点；空表示个股价全部来自 pool_live 快照
    if args.live != "off":
        need = [r["code"] for st in order + ["中枢"]
                for r in j["buckets"].get(st, [])[:args.per_stage]]
        miss = [c for c in dict.fromkeys(need) if c not in lmap]
        if miss:
            got = _fetch_px(miss)
            lmap.update(got)
            # 补取价是**此刻**拉的，与 pool_live 快照未必同一分钟。两者混在
            # 一个「实时 HH:MM」里，读者会以为个股价就是那个时刻的。
            if got:
                px_clock = datetime.now().strftime("%H:%M")
            print(f"[h5] 补取实时价 {len(got)}/{len(miss)} 只")

    def _live_px(r):
        px, pct = r.get("lpx"), r.get("lpct")
        if px is None and r["code"] in lmap:
            px, pct = lmap[r["code"]]
        return px, pct

    def _guard_px(r, st):
        """守位。三买看信号中枢上沿、三买待看末中枢上沿，五层（B/E）自带 guard。
        一买/二买/背驰的止损位不是同一个概念，这里不替它生造。"""
        if st in ("三买", "三买待"):
            return (r.get("sig_zg") if st == "三买" else r.get("zg")) or r.get("zg")
        return r.get("guard")

    def _broke(r, st):
        """盘中跌破守位的幅度（负数）。结构日收盘判的档位 + 当下价格，两者要当场对齐。"""
        px, _ = _live_px(r)
        g = _guard_px(r, st)
        try:
            if px and g and float(px) < float(g):
                return round(float(px) / float(g) - 1, 4)
        except (TypeError, ValueError):
            pass
        return None

    def _mkrow(r, st, extra=None):
        """一行明细：叠实时价 → 画走势 → 出证据卡。九态与五层共用。"""
        rr = dict(r)
        rr["stage"] = r.get("stage") or st
        rr["struct_asof"] = asof
        if extra:
            rr.update(extra)
        # 实时价：judge 行自带 lpx/lpct，九态行要查 lmap（pool_live + 补取）
        px, pct = _live_px(r)
        if px:
            rr["lpx"], rr["lpct"] = px, pct or 0.0
            # close 一并替换，否则「现价」与「距ZG」「判据实测值」各说各话
            rr["close"] = px
            rr["pct"] = (pct or 0) / 100.0
            if rr.get("zg"):
                rr["dist_zg"] = px / rr["zg"] - 1
            # 位置按实时价重算：只换 close 不换 pos，详情页会出现
            # 「现价 19.05 / 距 ZG +4.3% / 位置：枢内」这种自相矛盾的标签
            rr["lpos"] = _lpos(px, rr.get("zd"), rr.get("zg"))
            # 档位是结构日收盘判的，价格是当下的。盘中破位不标出来，
            # 「三买 · 守位在手」和现价破位就会并排放着，读者只能二选一相信。
            b = _broke(r, st)
            if b is not None:
                rr["broke"] = b
                rr["guard_px"] = float(_guard_px(r, st))
        rr["svg"] = _svg(rr, load_kline(r["code"]) if st != "中枢" else None)
        rr["ev"] = build_evidence(rr)
        return rr

    for st in order + ["中枢"]:
        full = j["buckets"].get(st, [])
        rows = [_mkrow(r, st) for r in full[:args.per_stage]]
        data[st] = rows
        shown[st] = len(rows)
        # 没内嵌进本页的留一条索引：从板块页点过来时，要说得清「它在哪一档、
        # 排第几、这页为什么没它」，而不是让它看起来像没判过。
        for k in range(args.per_stage, len(full)):
            allidx[full[k]["code"]] = {"st": st, "n": len(full), "k": k + 1,
                                       "nm": full[k].get("name") or ""}

    # ── 可操作五层（A/B/E）：与飞书卡片同一套语言 ────────────────
    # 卡片说「B 可介入 3 只」，点开就必须是这 3 只。九态是结构描述、
    # 五层是操作依据，两个视角并存：首页先给五层，往下才是九态全貌。
    jd = _newest_judge()
    layer_order, layer_map, layer_counts = [], {}, {}
    _jt = ""        # 五层判据的盘中时点（judge 的 live_at）；没有五层就留空
    if jd:
        for key, label, desc, clr in LAYER_META:
            full = jd.get(key) or []
            items = full[:args.per_stage]
            if not items:
                continue
            data[label] = [
                _mkrow(it, label, {"layer": key, "stage": label,
                                   "volr": it.get("volr"),
                                   "guard": it.get("guard")})
                for it in items]
            layer_counts[label] = (jd.get("counts") or {}).get(key, len(data[label]))
            shown[label] = len(data[label])
            layer_order.append(label)
            layer_map[key] = label
            LAYER_DESC[label] = desc
            # 五层和九态不是同一批判据：九态来自结构日收盘，五层来自最近一次
            # 盘中 judge（可能晚一天）。时点不写在每行（会把说明挤成两行），
            # 统一挂在「今日判断」那行的小字上。
            _jt = str(jd.get("live_at") or "")[11:16]
            LAYER_COLOR[label] = clr
            # 五层优先于九态：同一只票若两边都被截断，给人看「B 可介入第 7 只」
            # 比「三买第 41 只」有用
            for k in range(args.per_stage, len(full)):
                allidx[full[k]["code"]] = {"st": label, "n": len(full), "k": k + 1,
                                           "nm": full[k].get("name") or ""}

    # 卡片
    cards = "".join(
        f'<div class="card"><div class="k">{STAGE_LABEL[st]}</div>'
        f'<div class="v" style="color:{STAGE_COLOR[st]}">'
        f'{counts.get(st, len(j["buckets"].get(st, [])))}</div></div>'
        for st in CARD_STAGES)

    below = posd.get("下沿外", 0)
    share = below / judged * 100 if judged else 0
    mood = "弱势" if share > 50 else "中性" if share > 35 else "偏强"

    # 副标题：一屏高度寸土寸金，实时信息塞进原来那行，不另起一块。
    # 结构日一定要写出来：盘中这页是「结构 11-06 收盘 + 价格 11-09 14:03」两截拼的，
    # 只写「实时 14:03」会让人以为整页数字都是 14:03 的。
    s_lbl = (f"{asof[4:6]}-{asof[6:8]}" if len(str(asof)) == 8 else str(asof))
    # 按**全量**档内数，不是按本页内嵌的前 80 只——副标题说「盘中失守 3」，
    # 实际有 4 只，就是同一类半真话。
    broke_n = sum(1 for st in order + ["中枢"]
                  for r in j["buckets"].get(st, []) if _broke(r, st) is not None)
    if jd:
        for _k, _lb, _d, _c in LAYER_META:
            broke_n += sum(1 for r in (jd.get(_k) or []) if _broke(r, _lb) is not None)
    lv_clock = ""
    if lv:
        lv_clock = str(lv.get("live_at") or "")[11:16]
        _tail = (f'涨{lv.get("up", 0)} / 跌{lv.get("down", 0)}'
                 f' · 涨停{lv.get("limit_up", 0)}')
        # 个股价与市场统计可能不同分钟：价是本页当场补取的，市场统计来自
        # pool_live 快照（15 分钟一档）。同分钟才合成一个「实时」，否则拆开写，
        # 免得「实时 14:46」被读成列表里那些价格也是 14:46 的。
        # 拆开写之后这行必然两行，所以自己换行——让浏览器自动断，会把「涨停」
        # 拆成「涨 / 停 62」（390px 实测），读着像错字。
        if px_clock and px_clock != lv_clock:
            sub_line = (f"结构 {s_lbl} · 可判 {judged} 只 · "
                        f'<b style="color:#e35d5d">价 {px_clock}</b>'
                        f'<br><span style="color:#7b8698">市场 {lv_clock} · </span>'
                        + _tail)
        else:
            sub_line = (f"结构 {s_lbl} · 可判 {judged} 只 · "
                        f'<b style="color:#e35d5d">实时 {lv_clock}</b> ' + _tail)
        if broke_n:
            sub_line += f' · <b style="color:#e8807f">盘中失守 {broke_n}</b>'
    else:
        sub_line = (f"结构 {s_lbl} 收盘 · 窗口 {lookback} 根 · 可判 {judged} 只")

    # 「今日判断」那行的小字：一句话交代两个时点，比在每行重复一遍省地方
    layer_note = (f"五层 {_jt} 判 · 九态 {s_lbl} 收盘" if _jt
                  else f"九态 {s_lbl} 收盘 · 点档位看名单")

    order_all = layer_order + order
    secL, sec_of = _load_sector_map(asof)
    if secL:
        print(f"[h5] 所属板块：{len(secL)} 个板块 / {len(sec_of)} 只票有归属")
    data_js = json.dumps({
        **{st: data.get(st, []) for st in order_all + ["中枢"]},
        "counts": {**counts, **layer_counts},
        "shown": shown,
        "desc": {**STAGE_DESC, **LAYER_DESC},
        "color": {**STAGE_COLOR, **LAYER_COLOR},
        "layerMap": layer_map,
        "sectorL": secL,
        "sectorOf": sec_of,
        "all": allidx,
    }, ensure_ascii=False, separators=(",", ":"))

    html_txt = _BUILD.format(
        asof=asof, lookback=lookback, judged=judged, sub_line=sub_line,
        layer_note=layer_note,
        mood=mood, n_up=tre.get("上涨", 0), n_dn=tre.get("下跌", 0), n_below=below,
        cards=cards, brief=_trend_text(j, counts, tre, posd),
        minp=2.0, minamt=meta.get("min_amt", 0.5),
        run_at=html.escape(j.get("run_at") or ""),
        engine_note="引擎 chanlun_signal，非形态打分。"
                    "本页零重算，判定口径以 pool_state.py 为准。",
        data_js=data_js, order_js=json.dumps(order_all, ensure_ascii=False),
    )

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html_txt)
    print(f"[h5] {args.out}  ({len(html_txt)} bytes, 源 {os.path.basename(src)}, "
          f"{sum(len(v) for v in data.values())} 只明细)")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
