# -*- coding: utf-8 -*-
"""
P4 三源共振分析 v1 —— 开源情报板块旗舰分析第一版
=================================================
只读分析：
  源1 雪球情绪   xq_status_classified (33,391) + xq_status 正文/时间
  源2 JEV 事件   news.hit=1 (18,488)，jev JSON
  源3 词云锚     WORDC1 top 词表 overlap_top20

口径（诚实标注）：
  - 雪球时间轴用 xq_status.created_at（实测 100% 有值；
    任务书所述 "91% 缺失" 与实测不符，属历史语料偏置：
    2017-2019 占 76%，近 30 天仅约 322 帖）。
    labeled_at 为 2026-10/11 批量打标时间，作时间轴会产生假峰，弃用。
  - 词频共振用 title/desc LIKE 粗匹配（非分词，仅 v1 快口径）。
  - sentiment_gap / volume_z 为 v1 简单可解释指标。

产物：
  outputs/2026-11-04_P4三源共振v1.html （自包含浅色 0 外链）
  outputs/2026-11-04_P4三源共振v1.json （日序列全数据）

两库一律 mode=ro 只读，不写任何库文件。
"""
import sqlite3
import json
import math
import os
import sys
import io
from datetime import date, timedelta

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# ---------------- 路径 ----------------
XQ_DB = "C:/项目/老田的AI助手/quant_backtest/data/xueqiu/xueqiu_info.db"
NEWS_DB = "C:/项目/老田的AI助手/news_collector/news.db"
WORD_JSON = "C:/项目/老田的AI助手/outputs/2026-11-04_WORDC1_top词表.json"
OUT_DIR = "C:/项目/outputs"
HTML_OUT = os.path.join(OUT_DIR, "2026-11-04_P4三源共振v1.html")
JSON_OUT = os.path.join(OUT_DIR, "2026-11-04_P4三源共振v1.json")

TOP_N_DAYS = 30          # 最近 N 个"有数据"的日
GAP_THRESHOLD = 0.3      # |sentiment_gap| 超此值 → 情绪背离日
PEARSON_WINDOW = 30      # 量能相关滚动窗（日）


def ro(path):
    return sqlite3.connect("file:" + path + "?mode=ro", uri=True)


# ================================================================
# 1. 雪球面：按日聚合（created_at → 日）
# ================================================================
def load_xq_daily():
    con = ro(XQ_DB)
    cur = con.cursor()
    # 日帖量 + sentiment 分布
    cur.execute("""
        SELECT substr(s.created_at,1,10) d,
               c.sentiment, COUNT(*) n
        FROM xq_status_classified c
        JOIN xq_status s ON s.status_id = c.status_id
        WHERE s.created_at IS NOT NULL AND s.created_at != ''
        GROUP BY d, c.sentiment
    """)
    daily = {}
    for d, sent, n in cur.fetchall():
        a = daily.setdefault(d, {"total": 0, "bullish": 0, "bear": 0, "neutral": 0})
        a["total"] += n
        if sent == "bullish":
            a["bullish"] += n
        elif sent == "bear":
            a["bear"] += n
        elif sent == "neutral":
            a["neutral"] += n

    # event_type 分布
    cur.execute("""
        SELECT substr(s.created_at,1,10) d, c.event_type, COUNT(*) n
        FROM xq_status_classified c
        JOIN xq_status s ON s.status_id = c.status_id
        WHERE s.created_at IS NOT NULL AND s.created_at != ''
        GROUP BY d, c.event_type
    """)
    for d, et, n in cur.fetchall():
        daily.setdefault(d, {"total": 0, "bullish": 0, "bear": 0, "neutral": 0})
        daily[d].setdefault("event_types", {})
        if et:
            daily[d]["event_types"][et] = daily[d]["event_types"].get(et, 0) + n

    con.close()
    # bull率 = bullish / (bullish+bear+neutral)（分母即 total，因值域仅这三种）
    for d, a in daily.items():
        t = a["total"] or 1
        a["bull_rate"] = a["bullish"] / t
        a["bear_rate"] = a["bear"] / t
    return daily


# ================================================================
# 2. JEV 面：news.hit=1，ts → 日，解析 jev JSON
# ================================================================
def load_news_daily():
    con = ro(NEWS_DB)
    cur = con.cursor()
    cur.execute("""
        SELECT substr(ts,1,10) d, jev
        FROM news
        WHERE hit=1 AND jev IS NOT NULL AND LENGTH(jev) > 5
          AND ts IS NOT NULL AND ts != ''
    """)
    daily = {}
    for d, jev_raw in cur.fetchall():
        try:
            j = json.loads(jev_raw)
        except Exception:
            continue
        a = daily.setdefault(d, {"total": 0, "bull": 0, "bear": 0, "neutral": 0,
                                 "scopes_bear": {}, "scopes_bull": {}})
        a["total"] += 1
        bias = j.get("bias")
        if bias in ("bull", "bear", "neutral"):
            a[bias] += 1
        sc = j.get("scope_cn") or j.get("scope") or None
        if sc:
            if bias == "bear":
                a["scopes_bear"][sc] = a["scopes_bear"].get(sc, 0) + 1
            elif bias == "bull":
                a["scopes_bull"][sc] = a["scopes_bull"].get(sc, 0) + 1
    con.close()
    for d, a in daily.items():
        t = a["total"] or 1
        a["bull_rate"] = a["bull"] / t
        a["bear_rate"] = a["bear"] / t
    return daily


# ================================================================
# 3. 词云锚：overlap_top20 双源按日 LIKE 粗匹配
# ================================================================
def esc(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def load_word_daily(overlap_words):
    xq_con = ro(XQ_DB)
    n_con = ro(NEWS_DB)
    xq_daily = {w: {} for w in overlap_words}
    n_daily = {w: {} for w in overlap_words}

    for w in overlap_words:
        e = esc(w)
        like = "%" + e + "%"
        # 雪球：title/desc LIKE
        for d, n in xq_con.execute(
            "SELECT substr(created_at,1,10) d, COUNT(*) FROM xq_status "
            "WHERE created_at IS NOT NULL AND created_at != '' "
            "AND (title LIKE ? ESCAPE '\\' OR \"desc\" LIKE ? ESCAPE '\\') "
            "GROUP BY d",
            (like, like),
        ):
            if d:
                xq_daily[w][d] = n
        # 新闻：title/content LIKE（hit=1 限定，与事件流同口径）
        for d, n in n_con.execute(
            "SELECT substr(ts,1,10) d, COUNT(*) FROM news "
            "WHERE hit=1 AND ts IS NOT NULL AND ts != '' "
            "AND title LIKE ? ESCAPE '\\' "
            "GROUP BY d",
            (like,),
        ):
            if d:
                n_daily[w][d] = n
    xq_con.close()
    n_con.close()
    return xq_daily, n_daily


# ================================================================
# 统计工具
# ================================================================
def zscore(xs):
    m = sum(xs) / len(xs) if xs else 0.0
    v = sum((x - m) ** 2 for x in xs) / len(xs) if xs else 0.0
    sd = math.sqrt(v)
    if sd < 1e-12:
        return [0.0] * len(xs)
    return [(x - m) / sd for x in xs]


def pearson(a, b):
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    if n < 3:
        return None
    za, zb = zscore(a), zscore(b)
    num = sum(x * y for x, y in zip(za, zb))
    den = math.sqrt(sum(x * x for x in za) * sum(y * y for y in zb))
    if den < 1e-12:
        return None
    return num / den


def fmt(x, nd=3):
    return ("{:." + str(nd) + "f}").format(x) if x is not None else "—"


# ================================================================
# 主流程
# ================================================================
def main():
    words_meta = json.load(open(WORD_JSON, encoding="utf-8"))
    overlap_top20 = [w["word"] for w in words_meta["overlap_top20"]]
    top5_words = overlap_top20[:5]

    print("[1/5] 雪球日聚合 ...")
    xq = load_xq_daily()
    print("      雪球有数据天数:", len(xq))

    print("[2/5] JEV 日聚合 ...")
    nw = load_news_daily()
    print("      JEV 有数据天数:", len(nw))

    print("[3/5] 词云锚 20 词双源日频（LIKE 粗匹配）...")
    wxq, wnw = load_word_daily(overlap_top20)

    # ---------- 日并集 & 30 个"双源至少一源有数据"的最近日 ----------
    all_days = sorted(set(xq) | set(nw))
    # 共同分析窗：news 覆盖 2026-07-19 起；雪球近窗稀疏
    # 取最近 TOP_N_DAYS 个（双源合计 total>0 的日）
    days = [d for d in all_days if (xq.get(d, {}).get("total", 0) + nw.get(d, {}).get("total", 0)) > 0]
    days = sorted(days)[-TOP_N_DAYS:]
    d0, d1 = days[0], days[-1]

    # ---------- 日序列 ----------
    series = []
    for d in days:
        xa = xq.get(d, {})
        na = nw.get(d, {})
        et_sorted = sorted(xa.get("event_types", {}).items(), key=lambda kv: -kv[1])
        sb = sorted(na.get("scopes_bear", {}).items(), key=lambda kv: -kv[1])[:3]
        sl = sorted(na.get("scopes_bull", {}).items(), key=lambda kv: -kv[1])[:3]
        gap = None
        if xa.get("total", 0) > 0 and na.get("total", 0) > 0:
            gap = xa["bull_rate"] - na["bull_rate"]
        row = {
            "date": d,
            "xq": {
                "total": xa.get("total", 0),
                "bullish": xa.get("bullish", 0),
                "bear": xa.get("bear", 0),
                "neutral": xa.get("neutral", 0),
                "bull_rate": round(xa["bull_rate"], 4) if xa.get("total") else None,
                "bear_rate": round(xa["bear_rate"], 4) if xa.get("total") else None,
                "top_event_types": et_sorted[:5],
            },
            "jev": {
                "total": na.get("total", 0),
                "bull": na.get("bull", 0),
                "bear": na.get("bear", 0),
                "neutral": na.get("neutral", 0),
                "bull_rate": round(na["bull_rate"], 4) if na.get("total") else None,
                "bear_rate": round(na["bear_rate"], 4) if na.get("total") else None,
                "top_scopes_bear": sb,
                "top_scopes_bull": sl,
            },
            "sentiment_gap": round(gap, 4) if gap is not None else None,
        }
        # 词频
        row["words"] = {
            w: {"xq": wxq[w].get(d, 0), "news": wnw[w].get(d, 0)} for w in overlap_top20
        }
        series.append(row)

    # ---------- 量能相关（30 日窗，标准化后 Pearson）----------
    xs = [r["xq"]["total"] for r in series]
    ns = [r["jev"]["total"] for r in series]
    vol_corr = pearson(xs, ns)

    # 滚动 7 日符号一致性反转检测（v1 简化：逐日量比变化方向不一致 → 反转点）
    rev_flags = [False] * len(series)
    for i in range(1, len(series)):
        dx = xs[i] - xs[i - 1]
        dn = ns[i] - ns[i - 1]
        if dx * dn < 0:  # 一升一降
            rev_flags[i] = True

    # 共振/背离日标注
    flags = []
    for i, r in enumerate(series):
        why = []
        if r["sentiment_gap"] is not None and abs(r["sentiment_gap"]) > GAP_THRESHOLD:
            why.append("情绪分歧 |gap|>0.3")
        if rev_flags[i]:
            why.append("量能方向反转")
        if why:
            flags.append({"date": r["date"], "reasons": why})
    flag_dates = {f["date"] for f in flags}

    # ---------- 情绪分歧 Top3 ----------
    gaps = [(r["date"], r["sentiment_gap"],
             r["xq"]["bull_rate"], r["jev"]["bull_rate"],
             r["xq"]["total"], r["jev"]["total"])
            for r in series if r["sentiment_gap"] is not None]
    # 分歧日需要雪球样本量 ≥5 才有意义（近窗雪球日量个位数）
    gaps_meaningful = [g for g in gaps if g[4] >= 5]
    top_gap = sorted(gaps_meaningful or gaps, key=lambda g: -abs(g[1]))[:3]

    # ---------- 共振词 Top5 双源合计 ----------
    word_top5 = []
    for w in top5_words:
        xt = sum(wxq[w].get(d, 0) for d in days)
        nt = sum(wnw[w].get(d, 0) for d in days)
        word_top5.append({"word": w, "xq_30d": xt, "news_30d": nt})

    # ---------- 写 JSON ----------
    payload = {
        "meta": {
            "date": "2026-11-04",
            "task": "P4 三源共振分析 v1",
            "window_days": len(days),
            "window_start": d0,
            "window_end": d1,
            "xq_time_basis": "xq_status.created_at（实测 100% 有值；任务书所述 91% 缺失与实测不符，"
                             "系历史语料偏置 2017-2019 占 76%；labeled_at 为批量打标时间，弃用）",
            "news_time_basis": "news.ts（hit=1，覆盖 2026-07-19 ~ 2026-11-04）",
            "word_match": "title/desc LIKE 粗匹配（雪球 title+desc；新闻仅 title，hit=1 口径）",
            "metrics": {
                "sentiment_gap": "雪球bull率 - JEV bull率",
                "volume_z": "双源日量 zscore 后 Pearson（整窗），阈 |gap|>0.3 或量能方向反转 → 标注",
                "gap_threshold": GAP_THRESHOLD,
                "pearson_window": PEARSON_WINDOW,
            },
            "limits": [
                "雪球近 30 窗日帖量稀疏（日均约 10 帖），bull 率日噪声大，样本 <5 的日情绪分歧仅供参考",
                "LIKE 粗匹配含子串误命中（如 '基金' 命中 '公积金'），词频为上界估计",
                "v1 指标刻意简单（gap/相关系数），未做滞后互相关与显著性检验",
            ],
        },
        "volume_corr_pearson": round(vol_corr, 4) if vol_corr is not None else None,
        "days": series,
        "flags": flags,
        "top_gap_days": [
            {"date": g[0], "gap": g[1], "xq_bull_rate": g[2], "jev_bull_rate": g[3],
             "xq_total": g[4], "jev_total": g[5]} for g in top_gap
        ],
        "word_top5": word_top5,
        "overlap_top20": overlap_top20,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("[4/5] JSON →", JSON_OUT)

    # ---------- HTML ----------
    build_html(payload, series, flag_dates, vol_corr, top_gap, word_top5, d0, d1)
    print("[5/5] HTML →", HTML_OUT)

    # 控制台摘要
    print("\n===== 摘要 =====")
    print("窗口:", d0, "→", d1, f"（{len(days)} 天）")
    print("量能 Pearson:", fmt(vol_corr))
    print("分歧 Top3:")
    for g in top_gap:
        print(" ", g[0], "gap=", round(g[1], 3),
              "xq_bull=", g[2], "jev_bull=", g[3],
              "xq_n=", g[4], "jev_n=", g[5])


# ================================================================
# HTML 渲染（自包含浅色，纯 CSS 柱/线，0 外链）
# ================================================================
def build_html(payload, series, flag_dates, vol_corr, top_gap, word_top5, d0, d1):
    m = payload["meta"]

    # ---- 双源量能柱状 ----
    vmax = max(max(r["xq"]["total"] for r in series),
               max(r["jev"]["total"] for r in series)) or 1
    bars = []
    for r in series:
        xh = r["xq"]["total"] / vmax * 100
        nh = r["jev"]["total"] / vmax * 100
        hl = ' class="flag"' if r["date"] in flag_dates else ""
        bars.append(f'''
        <div class="day{hl and ' flagged' or ''}"{hl}>
          <div class="bwrap">
            <div class="bar xq" style="height:{xh:.1f}%"><span>{r['xq']['total'] or ''}</span></div>
            <div class="bar nw" style="height:{nh:.1f}%"><span>{r['jev']['total'] or ''}</span></div>
          </div>
          <div class="dlabel">{r['date'][5:]}</div>
        </div>''')

    # ---- bull 率分歧折线（纯 CSS 点线）----
    pts_xq, pts_nw, pts_gap = [], [], []
    n = len(series)
    for i, r in enumerate(series):
        x = i / max(n - 1, 1) * 100
        xb = r["xq"]["bull_rate"]
        nb = r["jev"]["bull_rate"]
        if xb is not None:
            y = 100 - xb * 100
            pts_xq.append((x, y, r["date"], xb, r["xq"]["total"]))
        if nb is not None:
            y = 100 - nb * 100
            pts_nw.append((x, y, r["date"], nb, r["jev"]["total"]))

    def line_svg(pts, color, label):
        if not pts:
            return ""
        svg_pts = " ".join(f'{p[0]:.2f},{p[1]:.2f}' for p in pts)
        dots = "".join(
            f'<circle cx="{p[0]:.2f}" cy="{p[1]:.2f}" r="2.6" fill="{color}">'
            f'<title>{p[2]} {label} {p[3]:.2f} (n={p[4]})</title></circle>'
            for p in pts
        )
        return f'<polyline points="{svg_pts}" fill="none" stroke="{color}" stroke-width="1.8"/>{dots}'

    # ---- 共振词对照表 ----
    wrows = ""
    for w in word_top5:
        seq = [r["words"][w["word"]] for r in series]
        xseq = [s["xq"] for s in seq]
        nseq = [s["news"] for s in seq]
        wc = pearson(xseq, nseq)
        # 迷你双柱
        mx = max(xseq + nseq) or 1
        mini = "".join(
            f'<div class="mday"><i style="height:{s["xq"]/mx*28:.0f}px"></i>'
            f'<b style="height:{s["news"]/mx*28:.0f}px"></b></div>'
            for s in seq
        )
        wrows += f'''
        <tr>
          <td class="w">{w['word']}</td>
          <td>{w['xq_30d']}</td><td>{w['news_30d']}</td>
          <td>{fmt(wc)}</td>
          <td><div class="mini">{mini}</div></td>
        </tr>'''

    # ---- 分歧 Top3 ----
    gaprows = ""
    for g in top_gap:
        seq_row = next(r for r in series if r["date"] == g[0])
        et = "、".join(f"{k} {v}" for k, v in seq_row["xq"]["top_event_types"][:3]) or "—"
        sb = "、".join(f"{k} {v}" for k, v in seq_row["jev"]["top_scopes_bear"][:3]) or "—"
        gaprows += f'''
        <tr{' class="flag"' if g[0] in flag_dates else ''}>
          <td>{g[0]}</td>
          <td>{fmt(g[2])} <small>(n={g[4]})</small></td>
          <td>{fmt(g[3])} <small>(n={g[5]})</small></td>
          <td class="{'neg' if g[1] < 0 else 'pos'}">{fmt(g[1])}</td>
          <td class="small">{et}</td>
          <td class="small">{sb}</td>
        </tr>'''

    # ---- 标注日列表 ----
    flist = "".join(
        f'<li><b>{f["date"]}</b> — {"；".join(f["reasons"])}</li>' for f in payload["flags"]
    ) or "<li>窗口内无触发标注的日期</li>"

    # 逐日表（全部 30 日）
    drows = ""
    for r in series:
        hl = ' class="flag"' if r["date"] in flag_dates else ""
        et = "、".join(k for k, _ in r["xq"]["top_event_types"][:3]) or "—"
        sb = "、".join(k for k, _ in r["jev"]["top_scopes_bear"][:3]) or "—"
        sl = "、".join(k for k, _ in r["jev"]["top_scopes_bull"][:3]) or "—"
        drows += f'''
        <tr{hl}>
          <td>{r['date']}</td>
          <td>{r['xq']['total']}</td>
          <td>{fmt(r['xq']['bull_rate'])}</td>
          <td>{fmt(r['xq']['bear_rate'])}</td>
          <td>{et}</td>
          <td>{r['jev']['total']}</td>
          <td>{fmt(r['jev']['bull_rate'])}</td>
          <td>{fmt(r['jev']['bear_rate'])}</td>
          <td class="small">{sl}</td>
          <td class="small">{sb}</td>
          <td>{fmt(r['sentiment_gap'])}</td>
        </tr>'''

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>P4 三源共振分析 v1 · 2026-11-04</title>
<style>
  :root {{
    --bg:#f7f8fa; --card:#ffffff; --ink:#1c2330; --sub:#5a6474;
    --line:#e3e7ee; --xq:#e8590c; --nw:#1971c2; --gapneg:#c92a2a; --gappos:#2b8a3e;
    --flag:#fff9db; --flagb:#f0c000;
  }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:var(--bg); color:var(--ink);
         font:14px/1.65 "Microsoft YaHei","PingFang SC",system-ui,sans-serif;
         padding:28px 20px 60px; }}
  .wrap {{ max-width:1060px; margin:0 auto; }}
  header {{ margin-bottom:22px; }}
  h1 {{ font-size:22px; letter-spacing:.5px; }}
  h1 .v {{ color:var(--sub); font-size:13px; font-weight:normal; }}
  .subline {{ color:var(--sub); font-size:12.5px; margin-top:6px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
           padding:18px 20px; margin-bottom:18px; }}
  h2 {{ font-size:16px; margin-bottom:4px; }}
  .note {{ color:var(--sub); font-size:12.5px; margin-bottom:12px; }}
  .kpis {{ display:flex; gap:14px; flex-wrap:wrap; margin-top:10px; }}
  .kpi {{ flex:1; min-width:150px; background:#fbfcfe; border:1px solid var(--line);
          border-radius:8px; padding:10px 14px; }}
  .kpi .k {{ color:var(--sub); font-size:12px; }}
  .kpi .v {{ font-size:20px; font-weight:600; margin-top:2px; }}
  .legend {{ display:flex; gap:18px; font-size:12.5px; color:var(--sub); margin:6px 0 10px; }}
  .dot {{ display:inline-block; width:10px; height:10px; border-radius:2px;
          margin-right:5px; vertical-align:-1px; }}

  /* 量能柱 */
  .chart {{ display:flex; align-items:flex-end; gap:2px; height:190px; padding:4px 0 0; }}
  .day {{ flex:1; display:flex; flex-direction:column; height:100%; min-width:0; }}
  .day.flagged {{ background:var(--flag); border-radius:4px; }}
  .bwrap {{ display:flex; align-items:flex-end; gap:1px; height:150px; flex:1; }}
  .bar {{ flex:1; border-radius:2px 2px 0 0; position:relative; min-height:1px; }}
  .bar.xq {{ background:var(--xq); opacity:.85; }}
  .bar.nw {{ background:var(--nw); opacity:.85; }}
  .bar span {{ position:absolute; top:-16px; left:50%; transform:translateX(-50%);
               font-size:9px; color:var(--sub); white-space:nowrap; }}
  .dlabel {{ font-size:9px; color:var(--sub); text-align:center; padding-top:4px;
             transform:rotate(-60deg); transform-origin:left top; white-space:nowrap;
             height:34px; }}

  /* 折线 */
  .linesvg {{ width:100%; height:240px; background:#fbfcfe; border:1px solid var(--line);
              border-radius:8px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th, td {{ padding:6px 8px; border-bottom:1px solid var(--line); text-align:center; }}
  th {{ background:#f2f4f8; font-weight:600; font-size:12px; color:var(--sub); }}
  td.w {{ font-weight:600; }}
  td.left {{ text-align:left; }}
  tr.flag td {{ background:var(--flag); }}
  td.neg {{ color:var(--gapneg); font-weight:600; }}
  td.pos {{ color:var(--gappos); font-weight:600; }}
  .small {{ font-size:11.5px; color:var(--sub); }}
  ul.flags {{ list-style:none; }}
  ul.flags li {{ padding:3px 0; border-bottom:1px dashed var(--line); font-size:13px; }}
  .mini {{ display:flex; align-items:flex-end; gap:1px; height:30px; justify-content:center; }}
  .mday {{ display:flex; align-items:flex-end; gap:1px; height:30px; }}
  .mday i {{ display:block; width:3px; background:var(--xq); border-radius:1px 1px 0 0; }}
  .mday b {{ display:block; width:3px; background:var(--nw); border-radius:1px 1px 0 0; }}
  .caveat {{ font-size:12.5px; color:var(--sub); }}
  .caveat li {{ margin:4px 0 4px 18px; }}
  footer {{ text-align:center; color:var(--sub); font-size:12px; margin-top:8px; }}
</style>
</head>
<body>
<div class="wrap">

<header>
  <h1>P4 三源共振分析 <span class="v">v1 · 2026-11-04 · 开源情报 E 车道</span></h1>
  <div class="subline">雪球情绪（33,391 帖分类）× JEV 事件流（hit=1，18,488 条）× 词云锚（overlap_top20）
  　|　窗口 {d0} → {d1}（{len(series)} 个有数据日）</div>
</header>

<div class="card">
  <h2>KPI 总览</h2>
  <div class="kpis">
    <div class="kpi"><div class="k">双源量能 Pearson（整窗）</div>
      <div class="v">{fmt(vol_corr)}</div></div>
    <div class="kpi"><div class="k">窗口雪球帖量合计</div>
      <div class="v">{sum(r['xq']['total'] for r in series)}</div></div>
    <div class="kpi"><div class="k">窗口 JEV 事件合计</div>
      <div class="v">{sum(r['jev']['total'] for r in series)}</div></div>
    <div class="kpi"><div class="k">情绪背离日数（|gap|&gt;0.3）</div>
      <div class="v">{len([f for f in payload['flags'] if any('情绪' in x for x in f['reasons'])])}</div></div>
    <div class="kpi"><div class="k">量能方向反转日数</div>
      <div class="v">{len([f for f in payload['flags'] if any('反转' in x for x in f['reasons'])])}</div></div>
  </div>
</div>

<div class="card">
  <h2>30 日双源量能对照</h2>
  <div class="note">左柱 雪球日帖量 / 右柱 JEV 日事件量；黄底 = 触发共振/背离标注的日期</div>
  <div class="legend">
    <span><span class="dot" style="background:var(--xq)"></span>雪球帖量</span>
    <span><span class="dot" style="background:var(--nw)"></span>JEV 事件量</span>
    <span><span class="dot" style="background:var(--flagb)"></span>标注日</span>
  </div>
  <div class="chart">{''.join(bars)}</div>
</div>

<div class="card">
  <h2>bull 率分歧时间线</h2>
  <div class="note">上=雪球 bull 率，下=JEV bull 率；两线张口即情绪分歧。悬停看数值与样本量。</div>
  <svg class="linesvg" viewBox="0 0 100 100" preserveAspectRatio="none">
    {line_svg(pts_xq, '#e8590c', '雪球bull率')}
    {line_svg(pts_nw, '#1971c2', 'JEV bull率')}
  </svg>
  <div class="legend" style="margin-top:8px">
    <span><span class="dot" style="background:#e8590c"></span>雪球 bull 率</span>
    <span><span class="dot" style="background:#1971c2"></span>JEV bull 率</span>
    <span style="color:var(--sub)">纵轴 0~1，n&lt;5 的雪球日噪声大</span>
  </div>
</div>

<div class="card">
  <h2>情绪分歧最大的 3 天</h2>
  <div class="note">gap = 雪球bull率 − JEV bull率；仅列雪球 n≥5 的日（否则降级取全量）。
        附当日雪球主要 event_type 与 JEV 利空 Top scope，供归因。</div>
  <table>
    <tr><th>日期</th><th>雪球 bull 率</th><th>JEV bull 率</th><th>gap</th>
        <th>雪球主要事件</th><th>JEV 利空 Top scope</th></tr>
    {gaprows}
  </table>
</div>

<div class="card">
  <h2>共振词 Top5 双源对照（overlap_top20 前五）</h2>
  <div class="note">窗口内 LIKE 粗匹配计数（雪球 title+desc / 新闻 title，hit=1）；
        迷你柱为逐日双源频次。相关列 = 双源日频 Pearson。</div>
  <table>
    <tr><th>词</th><th>雪球 30 日</th><th>新闻 30 日</th><th>日频相关</th><th>逐日形态</th></tr>
    {wrows}
  </table>
</div>

<div class="card">
  <h2>共振 / 背离日标注</h2>
  <ul class="flags">{flist}</ul>
</div>

<div class="card">
  <h2>逐日明细（{len(series)} 日全量）</h2>
  <table>
    <tr><th>日期</th><th>雪球帖</th><th>xq bull</th><th>xq bear</th><th>雪球事件</th>
        <th>JEV 量</th><th>jev bull</th><th>jev bear</th><th>JEV 利好 scope</th>
        <th>JEV 利空 scope</th><th>gap</th></tr>
    {drows}
  </table>
</div>

<div class="card">
  <h2>口径与限制（诚实条款）</h2>
  <ul class="caveat">
    <li><b>雪球时间轴</b>：实测 <code>created_at</code> 100% 有值（任务书所述 91% 缺失与实测不符）。
        但语料偏历史（2017-2019 占 76%），近 30 窗日帖量中位数个位数；
        <code>labeled_at</code> 为 2026-10/11 批量打标时间，作时间轴会产生假峰，已弃用。</li>
    <li><b>降级口径</b>：若个别 status 无 created_at，本版直接剔除（不参与任何聚合）。</li>
    <li><b>词频共振</b>：title/desc LIKE 子串粗匹配，含误命中（如 "基金"⊂"公积金"），
        计数为上界估计；新闻侧仅匹配 title（content 过长，v1 性能取舍）。</li>
    <li><b>指标 v1</b>：sentiment_gap 与整窗 Pearson 为简单可解释指标，
        未做滞后互相关、显著性检验与样本量加权；雪球 n&lt;5 的日 bull 率噪声极大。</li>
    <li><b>两库只读</b>（mode=ro URI），未写入任何库文件。</li>
  </ul>
</div>

<footer>P4 三源共振 v1 · resonance_v1.py · 产物：2026-11-04_P4三源共振v1.html / .json</footer>
</div>
</body>
</html>'''

    with open(HTML_OUT, "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    main()
