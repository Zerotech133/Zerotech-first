# -*- coding: utf-8 -*-
"""
P4 共振 v2 —— 把 v1 发现的"系统性情绪分歧 gap"变成可监测、可回测的信号
=====================================================================
只读原料：
  源1 v1 日序列  outputs/2026-11-04_P4三源共振v1.json（30 日，gap=雪球bull率−JEV bull率）
  源2 行情面     news_collector/news.db 表 quotes（个股快照，无指数）
                 → 变盘代理 = 自选池"市场日宽度"（每交易日 (code,day) 取最晚快照，
                   聚合 mean/median pct 与上涨家数占比 adv_ratio）

口径（照抄 v1 + v2 新增，诚实标注）：
  - 雪球 n<5 的日 bull 率噪声大（v1 口径）：gap 仍计入序列但标 noise，
    稳健统计另给 meaningful 子集（xq_n>=5）作参照。
  - z-score：全窗 z（in-sample，含前视，仅作 30 日体检用）+ 扩张窗 z（min 8 个有效 gap）。
  - 极端分歧日：|z_full|>1；收敛事件：前一日极端且当日 gap 跨越全窗均值（回落过均值）。
  - quotes 仅自选股（78 只；10-19 之后每日约 25 只×2 快照），非全市场，宽度为代理。
  - 样本仅 30 日 / 19 个交易日：一切回测结论都是点估计 + 样本不足标注，禁止过度宣称。

产物（只写 outputs）：
  outputs/2026-11-04_P4共振v2_变盘监测.html（自包含浅色 0 外链，纯 CSS 位置条）
  outputs/2026-11-04_P4共振v2_数据.json

quotes 只读（mode=ro URI），不写任何库文件；不 git 提交。
"""
import sqlite3
import json
import math
import os
import sys
import io
import statistics

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

V1_JSON = "C:/项目/outputs/2026-11-04_P4三源共振v1.json"
NEWS_DB = "C:/项目/老田的AI助手/news_collector/news.db"
OUT_DIR = "C:/项目/outputs"
HTML_OUT = os.path.join(OUT_DIR, "2026-11-04_P4共振v2_变盘监测.html")
JSON_OUT = os.path.join(OUT_DIR, "2026-11-04_P4共振v2_数据.json")

Z_BAND = 1.0        # |z|>1 → 极端分歧日
ROLL_MIN = 8        # 扩张窗 z 最少有效样本
FWD_HORIZONS = [1, 3, 5]


def ro(path):
    return sqlite3.connect("file:" + path + "?mode=ro", uri=True)


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def stdev(xs):
    return statistics.stdev(xs) if len(xs) >= 2 else None


def fmt(x, nd=3):
    return ("{:." + str(nd) + "f}").format(x) if x is not None else "—"


def pct_str(x, nd=1):
    return ("{:." + str(nd) + "f}%").format(x * 100) if x is not None else "—"


# ================================================================
# 1. v1 gap 序列 → 统计 / z-score / 极端日 / 收敛事件
# ================================================================
def load_gap_series():
    v1 = json.load(open(V1_JSON, encoding="utf-8"))
    rows = []
    for r in v1["days"]:
        gap = r.get("sentiment_gap")
        rows.append({
            "date": r["date"],
            "gap": gap,
            "xq_n": r["xq"]["total"],
            "jev_n": r["jev"]["total"],
            "xq_bull": r["xq"]["bull_rate"],
            "jev_bull": r["jev"]["bull_rate"],
            "noise": r["xq"]["total"] < 5,   # v1 口径：雪球 n<5 噪声日
        })
    return v1, rows


def gap_stats(rows):
    gs = [r["gap"] for r in rows if r["gap"] is not None]
    gm = [r["gap"] for r in rows if r["gap"] is not None and not r["noise"]]
    return {
        "n_days": len(rows),
        "n_gap_valid": len(gs),
        "n_noise": sum(1 for r in rows if r["gap"] is not None and r["noise"]),
        "n_gap_null": sum(1 for r in rows if r["gap"] is None),
        "mean": mean(gs), "stdev": stdev(gs),
        "min": min(gs) if gs else None, "max": max(gs) if gs else None,
        "meaningful_mean": mean(gm), "meaningful_stdev": stdev(gm),
        "meaningful_n": len(gm),
    }


def zscore_series(rows, st):
    """全窗 z（in-sample）+ 扩张窗 z（min ROLL_MIN 个有效 gap）。"""
    m, sd = st["mean"], st["stdev"]
    seen, run_sum, run_sq = 0, 0.0, 0.0
    for r in rows:
        z_full = z_roll = None
        if r["gap"] is not None and sd and sd > 1e-12:
            z_full = (r["gap"] - m) / sd
            seen += 1
            run_sum += r["gap"]
            run_sq += r["gap"] ** 2
            if seen >= ROLL_MIN:
                rm = run_sum / seen
                rv = run_sq / seen - rm ** 2
                rsd = math.sqrt(max(rv, 0.0) * seen / (seen - 1))
                z_roll = (r["gap"] - rm) / rsd if rsd > 1e-12 else 0.0
        r["z_full"] = z_full
        r["z_roll"] = z_roll
        r["extreme"] = z_full is not None and abs(z_full) > Z_BAND


def find_convergence(rows, st):
    """收敛事件：前一日 |z_full|>1（极端带），当日 gap 跨越全窗均值（回落过均值）。"""
    events = []
    for i in range(1, len(rows)):
        prev, cur = rows[i - 1], rows[i]
        if prev["gap"] is None or cur["gap"] is None:
            continue
        if not prev["extreme"]:
            continue
        m = st["mean"]
        if (prev["gap"] - m) * (cur["gap"] - m) <= 0:  # 跨越均值线
            events.append({
                "date": cur["date"],
                "prev_date": prev["date"],
                "prev_gap": prev["gap"], "prev_z": prev["z_full"],
                "gap": cur["gap"], "z": cur["z_full"],
                "pullback": abs(prev["gap"] - cur["gap"]),
                "noise": cur["noise"] or prev["noise"],
                "xq_n": cur["xq_n"], "jev_n": cur["jev_n"],
            })
    return events


# ================================================================
# 2. quotes → 自选池市场日宽度
# ================================================================
def load_breadth():
    con = ro(NEWS_DB)
    cur = con.cursor()
    cur.execute("""
        SELECT substr(ts,1,10) d, code, ts, pct
        FROM quotes
        WHERE ts IS NOT NULL AND ts != '' AND pct IS NOT NULL
        ORDER BY code, ts
    """)
    last = {}  # (d, code) -> pct（取最晚快照 = 该股当日收盘代理）
    all_codes = set()
    for d, code, _ts, pct in cur.fetchall():
        last[(d, code)] = pct
        all_codes.add(code)
    con.close()

    by_day = {}
    for (d, code), pct in last.items():
        by_day.setdefault(d, []).append(pct)
    breadth = []
    for d in sorted(by_day):
        pcts = by_day[d]
        n = len(pcts)
        adv = sum(1 for p in pcts if p > 0)
        dec = sum(1 for p in pcts if p < 0)
        breadth.append({
            "date": d, "n_codes": n,
            "adv_ratio": adv / n,          # 上涨家数占比（宽度主指标）
            "adv": adv, "dec": dec, "flat": n - adv - dec,
            "mean_pct": mean(pcts), "median_pct": statistics.median(pcts),
        })
    return breadth, len(all_codes)


# ================================================================
# 3. 变盘窗口回测：收敛事件日 vs 非收敛日
# ================================================================
def fwd_metrics(breadth, i, h):
    """t=i 后 h 个交易日的宽度变化；j 越界返回 None。"""
    j = i + h
    if j >= len(breadth):
        return None
    b0, bj = breadth[i]["adv_ratio"], breadth[j]["adv_ratio"]
    diffs = [breadth[s]["adv_ratio"] - breadth[s - 1]["adv_ratio"]
             for s in range(i + 1, j + 1)]
    flips = sum(1 for a, b in zip(diffs, diffs[1:]) if a * b < 0)
    return {
        "h": h, "dB": bj - b0, "abs_dB": abs(bj - b0),
        "flips": flips,
        "b_from": b0, "b_to": bj,
        "mean_pct_from": breadth[i]["mean_pct"],
        "mean_pct_to": breadth[j]["mean_pct"],
    }


def backtest(breadth, rows, events):
    bidx = {b["date"]: i for i, b in enumerate(breadth)}
    ev_dates = {e["date"] for e in events}
    extreme_dates = {r["date"] for r in rows if r["extreme"]}

    def day_group(d):
        if d in ev_dates:
            return "event"
        if d in extreme_dates:
            return "extreme_only"   # 极端但未收敛（信息组，样本极小）
        return "baseline"

    per_day, detail = [], {"event": [], "extreme_only": [], "baseline": []}
    for i, b in enumerate(breadth):
        d = b["date"]
        g = day_group(d)
        rec = {"date": d, "group": g, "windows": {}}
        for h in FWD_HORIZONS:
            fm = fwd_metrics(breadth, i, h)
            rec["windows"][h] = fm
            if fm:
                detail[g].append({"date": d, **fm})
        per_day.append(rec)

    def agg(g):
        out = {"n_days": sum(1 for r in per_day if r["group"] == g)}
        for h in FWD_HORIZONS:
            obs = [x for x in detail[g] if x["h"] == h]
            out[f"h{h}"] = {
                "n_obs": len(obs),
                "mean_abs_dB": mean([x["abs_dB"] for x in obs]),
                "mean_flips": mean([x["flips"] for x in obs]),
            }
        return out

    return per_day, {g: agg(g) for g in ("event", "extreme_only", "baseline")}


def attach_event_market(events, breadth):
    """事件日（可能是非交易日）→ 对齐到 ≤事件日的最近交易日，取当时宽度。"""
    bdates = [b["date"] for b in breadth]
    for e in events:
        aligned = None
        for d in bdates:
            if d <= e["date"]:
                aligned = d
            else:
                break
        e["market_date"] = aligned
        if aligned:
            b = next(b for b in breadth if b["date"] == aligned)
            e["breadth_then"] = {
                "adv_ratio": b["adv_ratio"], "mean_pct": b["mean_pct"],
                "n_codes": b["n_codes"],
                "same_day": aligned == e["date"],
            }
        else:
            e["breadth_then"] = None


# ================================================================
# 4. JSON 产物
# ================================================================
def dump_json(st, rows, events, breadth, n_codes, back, v1_meta):
    payload = {
        "meta": {
            "date": "2026-11-04",
            "task": "P4 共振 v2 —— gap 均值回复监测 + 变盘窗口回测",
            "v1_source": V1_JSON,
            "window": f'{v1_meta["window_start"]} ~ {v1_meta["window_end"]}（{v1_meta["window_days"]} 日）',
            "gap_def": "雪球bull率 − JEV bull率（v1 口径；雪球 n<5 日标 noise）",
            "z_def": {
                "z_full": "全窗 in-sample z（含前视，仅 30 日体检用）",
                "z_roll": f"扩张窗 z（min {ROLL_MIN} 个有效 gap）",
                "band": f"|z_full|>{Z_BAND:g} → 极端分歧日；收敛事件=前日极端且当日 gap 跨越全窗均值",
            },
            "breadth_def": "quotes 每交易日 (code,day) 取最晚快照 → 上涨家数占比 adv_ratio（自选池代理，非全市场）",
            "quotes_universe": f"全部出现过的自选股 {n_codes} 只；10-19 前高频快照、之后每日约 25 只",
            "limits": [
                "gap 序列仅 30 日、其中有效 gap 约 20+ 个且部分为雪球 n<5 噪声日",
                "z_full 为 in-sample 统计（含前视），不可直接当实盘信号",
                "quotes 仅自选股（后期每日 25 只），宽度非全市场宽度；无指数行情",
                "收敛事件个数为个位数量级，回测只能给点估计，无法做显著性检验",
                "交易日仅 19 个，h=5 窗口在序列末端会被截断",
            ],
        },
        "gap_stats": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in st.items()},
        "z_series": [
            {
                "date": r["date"], "gap": r["gap"],
                "z_full": round(r["z_full"], 4) if r["z_full"] is not None else None,
                "z_roll": round(r["z_roll"], 4) if r["z_roll"] is not None else None,
                "extreme": r["extreme"], "noise": r["noise"],
                "xq_n": r["xq_n"], "jev_n": r["jev_n"],
            }
            for r in rows
        ],
        "events": [
            {
                **e,
                "prev_gap": e["prev_gap"], "prev_z": round(e["prev_z"], 4),
                "gap": e["gap"], "z": round(e["z"], 4),
                "pullback": round(e["pullback"], 4),
                "breadth_then": (
                    {k: (round(v, 4) if isinstance(v, float) else v)
                     for k, v in e["breadth_then"].items()}
                    if e["breadth_then"] else None
                ),
            }
            for e in events
        ],
        "breadth_daily": [
            {k: (round(v, 4) if isinstance(v, float) else v) for k, v in b.items()}
            for b in breadth
        ],
        "backtest_summary": back["agg"],
        "backtest_per_day": [
            {
                "date": r["date"], "group": r["group"],
                "windows": {
                    str(h): (
                        {k: (round(v, 4) if isinstance(v, float) else v)
                         for k, v in r["windows"][h].items()}
                        if r["windows"][h] else None
                    )
                    for h in FWD_HORIZONS
                },
            }
            for r in back["per_day"]
        ],
    }
    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("[JSON] →", JSON_OUT)
    return payload


# ================================================================
# 5. HTML 产物（自包含浅色 0 外链，gap 时间线用纯 CSS 位置条）
# ================================================================
def build_html(payload, st, rows, events, breadth, back, n_codes):
    m = payload["meta"]
    gs = [r["gap"] for r in rows if r["gap"] is not None]
    lo = min(min(gs), st["mean"] - st["stdev"]) - 0.06
    hi = max(max(gs), st["mean"] + st["stdev"]) + 0.06

    def y(v):  # 值 → 纵向位置（百分比，上小下大翻转）
        return (hi - v) / (hi - lo) * 100

    n = len(rows)
    band_top, band_h = y(st["mean"] + st["stdev"]), y(st["mean"] - st["stdev"]) - y(st["mean"] + st["stdev"])
    ev_dates = {e["date"] for e in events}

    # ---- 纯 CSS 位置条：每日 gap 相对均值的偏移条（直接锚定在 .gchart 坐标系）----
    bars = []
    for i, r in enumerate(rows):
        left = i / n * 100
        if r["gap"] is None:
            bars.append(f'<div class="gnull" style="left:{left:.2f}%">–</div>'
                        f'<div class="glbl" style="left:{left:.2f}%">{r["date"][5:]}</div>')
            continue
        top = min(y(r["gap"]), y(st["mean"]))
        hgt = abs(y(r["gap"]) - y(st["mean"]))
        cls = []
        if r["date"] in ev_dates:
            cls.append("conv")
        elif r["gap"] >= st["mean"]:
            cls.append("pos")
        else:
            cls.append("neg")
        if r["extreme"]:
            cls.append("ext")
        if r["noise"]:
            cls.append("noise")
        tip = (f'{r["date"]}  gap={r["gap"]:+.3f}  z={r["z_full"]:+.2f}'
               f'  雪球n={r["xq_n"]} JEVn={r["jev_n"]}'
               f'{"  [噪声日]" if r["noise"] else ""}'
               f'{"  [极端]" if r["extreme"] else ""}'
               f'{"  [收敛事件]" if r["date"] in ev_dates else ""}')
        marker = (f'<i class="evdot" style="left:{left + 100 / n * 0.31:.2f}%;top:{y(r["gap"]):.1f}%" '
                  f'title="{tip} 收敛事件"></i>') if r["date"] in ev_dates else ""
        bar = (f'<div class="gbar {" ".join(cls)}" style="left:{left:.2f}%;width:{100/n*0.62:.2f}%;'
               f'top:{top:.2f}%;height:{max(hgt, 0.4):.2f}%" title="{tip}"></div>{marker}'
               f'<div class="glbl" style="left:{left:.2f}%">{r["date"][5:]}</div>')
        bars.append(bar)

    # ---- 宽度日条（adv_ratio）----
    bbars = []
    for b in breadth:
        hgt = b["adv_ratio"] * 100
        c = "up" if b["adv_ratio"] >= 0.5 else "dn"
        tip = (f'{b["date"]}  上涨占比={b["adv_ratio"]:.2f}（{b["adv"]}/{b["n_codes"]}）'
               f'  均值pct={b["mean_pct"]:+.2f}%  中位={b["median_pct"]:+.2f}%')
        bbars.append(
            f'<div class="bcol"><div class="bbar {c}" style="height:{hgt:.0f}%" title="{tip}"></div>'
            f'<div class="blbl">{b["date"][5:]}</div></div>')

    # ---- 收敛事件卡 ----
    cards = []
    for e in events:
        bt = e["breadth_then"]
        if bt:
            bt_html = (f'当时宽度（{"当日" if bt["same_day"] else "前一交易日 " + e["market_date"]}）：'
                       f'上涨占比 <b>{bt["adv_ratio"]:.2f}</b>，'
                       f'均值 pct <b>{bt["mean_pct"]:+.2f}%</b>，n={bt["n_codes"]} 只')
        else:
            bt_html = "事件日在行情覆盖之前，无宽度数据"
        noise_tag = '<span class="tag warn">含噪声日(n&lt;5)</span>' if e["noise"] else '<span class="tag ok">双源样本足</span>'
        cards.append(f'''
      <div class="evcard">
        <div class="evhead"><b>{e["date"]}</b> {noise_tag}
          <span class="tag">前极值 {e["prev_date"]}：gap {e["prev_gap"]:+.3f}（z={e["prev_z"]:+.2f}）</span></div>
        <div class="evbody">回落幅度 <b>{e["pullback"]:.3f}</b>（{e["prev_gap"]:+.3f} → {e["gap"]:+.3f}，跨越均值 {st["mean"]:+.3f}）
          ｜ 雪球 n={e["xq_n"]}、JEV n={e["jev_n"]}<br>{bt_html}</div>
      </div>''')
    if not cards:
        cards = ['<div class="evcard">窗口内未检出收敛事件（前日极端且当日跨越均值的组合未出现）。</div>']

    # ---- 回测对比表 ----
    def agg_cell(g, h, key, nd=3):
        a = back["agg"][g][f"h{h}"]
        if a["n_obs"] == 0:
            return "—"
        return f'{a[key]:.{nd}f} <small>(n={a["n_obs"]})</small>'

    def bt_rows():
        out = ""
        for g, label in (("event", "收敛事件日"), ("extreme_only", "仅极端未收敛日"), ("baseline", "非收敛日（基线）")):
            a = back["agg"][g]
            out += f'''
        <tr class="{'ev' if g == 'event' else ''}">
          <td class="left">{label}</td><td>{a["n_days"]}</td>
          <td>{agg_cell(g, 1, "mean_abs_dB")}</td><td>{agg_cell(g, 3, "mean_abs_dB")}</td><td>{agg_cell(g, 5, "mean_abs_dB")}</td>
          <td>{agg_cell(g, 1, "mean_flips", 2)}</td><td>{agg_cell(g, 3, "mean_flips", 2)}</td><td>{agg_cell(g, 5, "mean_flips", 2)}</td>
        </tr>'''
        return out

    # 逐事件明细
    ev_detail = ""
    for r in back["per_day"]:
        if r["group"] != "event":
            continue
        cells = []
        for h in FWD_HORIZONS:
            w = r["windows"][h]
            cells.append(f'{w["abs_dB"]:+.3f}/{w["flips"]}' if w else "—")
        ev_detail += (f'<tr><td>{r["date"]}</td><td>{cells[0]}</td>'
                      f'<td>{cells[1]}</td><td>{cells[2]}</td></tr>')
    if not ev_detail:
        ev_detail = '<tr><td colspan="4">无（收敛事件日均不在行情覆盖内或无前向窗）</td></tr>'

    n_ext = sum(1 for r in rows if r["extreme"])
    noise_n = st["n_noise"]

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>P4 共振 v2 · gap 均值回复监测与变盘回测 · 2026-11-04</title>
<style>
  :root {{
    --bg:#f7f8fa; --card:#fff; --ink:#1c2330; --sub:#5a6474; --line:#e3e7ee;
    --pos:#2b8a3e; --neg:#c92a2a; --ext:#e8590c; --conv:#1971c2; --band:#eef3fb;
    --up:#d9480f; --dn:#2b8a3e; --warnbg:#fff9db; --warnb:#f0c000;
  }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:var(--bg); color:var(--ink);
         font:14px/1.65 "Microsoft YaHei","PingFang SC",system-ui,sans-serif;
         padding:28px 20px 60px; }}
  .wrap {{ max-width:1080px; margin:0 auto; }}
  h1 {{ font-size:22px; }} h1 .v {{ color:var(--sub); font-size:13px; font-weight:normal; }}
  .subline {{ color:var(--sub); font-size:12.5px; margin:6px 0 22px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
           padding:18px 20px; margin-bottom:18px; }}
  h2 {{ font-size:16px; margin-bottom:4px; }}
  .note {{ color:var(--sub); font-size:12.5px; margin-bottom:12px; }}
  .kpis {{ display:flex; gap:14px; flex-wrap:wrap; }}
  .kpi {{ flex:1; min-width:150px; background:#fbfcfe; border:1px solid var(--line);
          border-radius:8px; padding:10px 14px; }}
  .kpi .k {{ color:var(--sub); font-size:12px; }}
  .kpi .v {{ font-size:20px; font-weight:600; margin-top:2px; }}
  .kpi .s {{ font-size:11px; color:var(--sub); }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th, td {{ padding:6px 8px; border-bottom:1px solid var(--line); text-align:center; }}
  th {{ background:#f2f4f8; font-weight:600; font-size:12px; color:var(--sub); }}
  td.left {{ text-align:left; }} tr.ev td {{ background:#eef6ff; }}
  small {{ color:var(--sub); font-size:11px; }}

  /* gap 时间线：纯 CSS 位置条 */
  .gchart {{ position:relative; height:320px; background:#fbfcfe; border:1px solid var(--line);
             border-radius:8px; margin-bottom:34px; }}
  .gband {{ position:absolute; left:0; right:0; background:var(--band);
            border-top:1px dashed #b9c8e4; border-bottom:1px dashed #b9c8e4; }}
  .gmean {{ position:absolute; left:0; right:0; border-top:2px solid #4263eb; z-index:2; }}
  .gmean span {{ position:absolute; right:4px; top:-20px; font-size:11px; color:#4263eb; }}
  .gbar {{ position:absolute; z-index:3; border-radius:2px; min-height:1px; }}
  .gbar.pos {{ background:var(--pos); opacity:.75; }}
  .gbar.neg {{ background:var(--neg); opacity:.75; }}
  .gbar.noise {{ opacity:.28; }}
  .gbar.ext {{ box-shadow:0 0 0 1.5px var(--ext); }}
  .gbar.conv {{ background:var(--conv); opacity:.9; }}
  .evdot {{ position:absolute; z-index:4; width:11px; height:11px; border-radius:50%;
            border:2.5px solid var(--conv); background:#fff; transform:translate(-50%,-50%); }}
  .gnull {{ position:absolute; top:48%; width:3%; text-align:center; color:#b6bec9; font-size:10px; }}
  .glbl {{ position:absolute; bottom:-30px; transform:rotate(-60deg); transform-origin:left top;
           font-size:9px; color:var(--sub); white-space:nowrap; }}
  .legend {{ display:flex; gap:16px; flex-wrap:wrap; font-size:12.5px; color:var(--sub); margin-top:8px; }}
  .dot {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:5px; vertical-align:-1px; }}

  /* 宽度条 */
  .bchart {{ display:flex; align-items:flex-end; gap:3px; height:150px; padding-top:6px; position:relative; }}
  .bcol {{ flex:1; display:flex; flex-direction:column; height:100%; min-width:0; position:relative; }}
  .bbar {{ width:100%; border-radius:2px 2px 0 0; position:absolute; bottom:16px; left:0; }}
  .bbar.up {{ background:var(--up); opacity:.8; }}
  .bbar.dn {{ background:var(--dn); opacity:.8; }}
  .blbl {{ position:absolute; bottom:0; width:100%; text-align:center; font-size:8.5px; color:var(--sub); }}
  .midline {{ position:absolute; left:0; right:0; bottom:calc(16px + 50%); border-top:1px dashed #adb5bd; }}

  /* 事件卡 */
  .evcard {{ border:1px solid var(--line); border-left:4px solid var(--conv); border-radius:8px;
             padding:10px 14px; margin-bottom:10px; background:#fbfdff; }}
  .evhead {{ font-size:13.5px; margin-bottom:4px; display:flex; gap:10px; flex-wrap:wrap; align-items:center; }}
  .evbody {{ font-size:12.5px; color:var(--sub); }}
  .tag {{ font-size:11px; border:1px solid var(--line); border-radius:4px; padding:1px 6px; color:var(--sub); }}
  .tag.warn {{ background:var(--warnbg); border-color:var(--warnb); }}
  .tag.ok {{ background:#ebfbee; border-color:#a9e34b; }}

  .warnbox {{ background:var(--warnbg); border:1px solid var(--warnb); border-radius:8px;
              padding:10px 14px; font-size:12.5px; color:#664d03; margin-bottom:14px; }}
  .caveat {{ font-size:12.5px; color:var(--sub); }}
  .caveat li {{ margin:4px 0 4px 18px; }}
  footer {{ text-align:center; color:var(--sub); font-size:12px; margin-top:8px; }}
</style>
</head>
<body>
<div class="wrap">

<header>
  <h1>P4 共振 v2 <span class="v">gap 均值回复监测 × 变盘窗口回测 · 2026-11-04 · 开源情报 G 车道</span></h1>
  <div class="subline">输入：v1 30 日 gap 序列（雪球bull率−JEV bull率）× quotes 自选池日宽度（{breadth and breadth[0]["date"]} ~ {breadth and breadth[-1]["date"]}，{len(breadth)} 个交易日，{n_codes} 只自选股）
  　|　z 带：|z_full|&gt;{Z_BAND:g} 极端；收敛=前日极端且当日跨越均值</div>
</header>

<div class="card">
  <h2>KPI 总览</h2>
  <div class="kpis">
    <div class="kpi"><div class="k">gap 均值 / σ（全窗 in-sample）</div>
      <div class="v">{fmt(st["mean"], 3)} / {fmt(st["stdev"], 3)}</div>
      <div class="s">有效 gap {st["n_gap_valid"]}/{st["n_days"]} 日，其中噪声日 {noise_n}</div></div>
    <div class="kpi"><div class="k">极端分歧日（|z|&gt;1）</div>
      <div class="v">{n_ext}</div>
      <div class="s">最负 {fmt(st["min"], 3)} / 最正 {fmt(st["max"], 3)}</div></div>
    <div class="kpi"><div class="k">收敛事件</div>
      <div class="v">{len(events)}</div>
      <div class="s">前日极端 → 当日跨越均值</div></div>
    <div class="kpi"><div class="k">宽度交易日 / 自选池</div>
      <div class="v">{len(breadth)} / {n_codes}</div>
      <div class="s">10-19 后每日约 25 只（自选池代理）</div></div>
    <div class="kpi"><div class="k">meaningful 子集均值（xq n≥5）</div>
      <div class="v">{fmt(st["meaningful_mean"], 3)}</div>
      <div class="s">n={st["meaningful_n"]}，σ={fmt(st["meaningful_stdev"], 3)}</div></div>
  </div>
</div>

<div class="card">
  <h2>gap 时间线与均值 ±1σ 信号带（纯 CSS 位置条）</h2>
  <div class="note">条 = 当日 gap 相对全窗均值（蓝线 {st["mean"]:+.3f}）的偏移；浅蓝带 = ±1σ（{st["mean"] - st["stdev"]:+.3f} ~ {st["mean"] + st["stdev"]:+.3f}）。
        橙描边 = 极端日（|z|&gt;1）；蓝实条+圆点 = 收敛事件；浅色条 = 雪球 n&lt;5 噪声日（v1 口径）。悬停看数值。</div>
  <div class="gchart">
    <div class="gband" style="top:{band_top:.2f}%;height:{band_h:.2f}%"></div>
    <div class="gmean" style="top:{y(st["mean"]):.2f}%"><span>均值 {st["mean"]:+.3f}</span></div>
    {''.join(bars)}
  </div>
  <div class="legend">
    <span><span class="dot" style="background:var(--pos)"></span>gap&gt;均值</span>
    <span><span class="dot" style="background:var(--neg)"></span>gap&lt;均值</span>
    <span><span class="dot" style="box-shadow:0 0 0 1.5px var(--ext);background:#fff"></span>极端 |z|&gt;1</span>
    <span><span class="dot" style="background:var(--conv)"></span>收敛事件</span>
    <span>浅色 = 噪声日（雪球 n&lt;5）</span>
    <span>z_full 为全窗 in-sample（含前视，体检用）</span>
  </div>
</div>

<div class="card">
  <h2>收敛事件清单</h2>
  <div class="note">定义：前一日 |z_full|&gt;{Z_BAND:g}（极端带），当日 gap 跨越全窗均值。"当时宽度"对齐到 ≤事件日的最近交易日。</div>
  {''.join(cards)}
</div>

<div class="card">
  <h2>自选池市场日宽度（变盘代理）</h2>
  <div class="note">每交易日 (code,日) 取最晚快照；柱高 = 上涨家数占比 adv_ratio，虚线 = 0.50。悬停看均值/中位 pct 与家数。
        <b>非全市场</b>：{n_codes} 只自选股，10-19 后每日仅约 25 只，宽度代表性有限。</div>
  <div class="bchart"><div class="midline"></div>{''.join(bbars)}</div>
</div>

<div class="card">
  <h2>变盘窗口回测：收敛日 vs 非收敛日</h2>
  <div class="warnbox">样本量警告：窗口仅 {st["n_days"]} 日 / {len(breadth)} 个交易日，收敛事件 {len(events)} 个（落在行情覆盖内的更少）。
      下表全部为<b>点估计</b>，未做显著性检验，n 为实际可观测窗数（序列末端 h=5 被截断）。禁止据此宣称"gap 收敛预测变盘"。</div>
  <table>
    <tr><th rowspan="2">组</th><th rowspan="2">日数</th>
        <th colspan="3">后续 |Δ宽度|（adv_ratio）</th><th colspan="3">后续方向翻转次数</th></tr>
    <tr><th>+1日</th><th>+3日</th><th>+5日</th><th>+1日</th><th>+3日</th><th>+5日</th></tr>
    {bt_rows()}
  </table>
  <div class="note" style="margin-top:10px">逐收敛事件明细（|ΔB|/翻转数）：</div>
  <table>
    <tr><th>事件日</th><th>+1日</th><th>+3日</th><th>+5日</th></tr>
    {ev_detail}
  </table>
</div>

<div class="card">
  <h2>v2 → v3 展望</h2>
  <ul class="caveat">
    <li><b>更长窗口</b>：news/JEV 覆盖自 2026-07-19 起（v1 只取了 30 日），v3 可把 gap 序列拉长到 ~3.5 个月，
        收敛事件样本或可翻数倍，才够做 bootstrap / 置信区间。</li>
    <li><b>指数行情接入</b>：当前无指数行情，宽度只是 {n_codes} 只自选股代理；接入指数（或全市场快照）后，
        "变盘"可直接用指数日收益率定义，替代 adv_ratio 日差。</li>
    <li><b>真滚动 z</b>：z_full 含前视；v3 用纯扩张窗/固定窗滚动 z 落地成可实盘监测的信号带，并加报警阈值。</li>
    <li><b>滞后互相关</b>：gap 极值 → 市场宽度的 lead-lag（v1/v2 均未做），是"分歧是否领先变盘"的关键检验。</li>
  </ul>
</div>

<div class="card">
  <h2>口径与样本量诚实说明</h2>
  <ul class="caveat">
    <li><b>gap 口径</b>：雪球bull率 − JEV bull率（v1 原样继承）；雪球 n&lt;5 的日 bull 率噪声大（v1 口径），
        本版仍计入序列但降透明度标注，并给 meaningful 子集（n={st["meaningful_n"]}）参照。</li>
    <li><b>z_full 前视</b>：用全窗均值/σ 标准化（in-sample），"极端/收敛"标注含未来信息，仅作 30 日体检；
        z_roll（扩张窗，min {ROLL_MIN}）已写入 JSON 供 v3 取用。</li>
    <li><b>宽度代理</b>：quotes 无指数行情；(code,日) 取最晚快照作收盘代理；10-19 前为高频快照期、之后每日约 25 只 ×2 次，
        宽度对"市场"的代表性弱，且成分中途变化（{n_codes} → 25）本身会造成宽度跳变。</li>
    <li><b>回测样本</b>：{len(breadth)} 个交易日，收敛事件 {len(events)} 个且未必全落在行情窗内；
        所有对比只有点估计意义，结论方向不可外推。</li>
    <li><b>只读声明</b>：v1 JSON 与 quotes（mode=ro）只读，产物仅写 outputs；未 git 提交。</li>
  </ul>
</div>

<footer>P4 共振 v2 · resonance_v2.py · 产物：2026-11-04_P4共振v2_变盘监测.html / 2026-11-04_P4共振v2_数据.json</footer>
</div>
</body>
</html>'''
    with open(HTML_OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print("[HTML] →", HTML_OUT)


# ================================================================
# 主流程
# ================================================================
def main():
    print("[1/6] 读 v1 日序列 ...")
    v1, rows = load_gap_series()
    st = gap_stats(rows)
    print(f"      {st['n_days']} 日，有效 gap {st['n_gap_valid']}，噪声日 {st['n_noise']}")
    print(f"      gap mean={fmt(st['mean'])} sd={fmt(st['stdev'])} "
          f"min={fmt(st['min'])} max={fmt(st['max'])}")

    print("[2/6] z-score（全窗 + 扩张窗）...")
    zscore_series(rows, st)

    print("[3/6] 收敛事件识别 ...")
    events = find_convergence(rows, st)
    print(f"      极端日 {sum(1 for r in rows if r['extreme'])} 个，收敛事件 {len(events)} 个")
    for e in events:
        print(f"      {e['date']}  {e['prev_gap']:+.3f}(z={e['prev_z']:+.2f}) -> {e['gap']:+.3f}  回落 {e['pullback']:.3f}"
              f"{'  [含噪声日]' if e['noise'] else ''}")

    print("[4/6] quotes → 自选池日宽度 ...")
    breadth, n_codes = load_breadth()
    print(f"      {len(breadth)} 个交易日（{breadth[0]['date']} ~ {breadth[-1]['date']}），"
          f"自选池 {n_codes} 只")

    attach_event_market(events, breadth)

    print("[5/6] 变盘窗口回测 ...")
    per_day, agg = backtest(breadth, rows, events)
    back = {"per_day": per_day, "agg": agg}
    for g in ("event", "extreme_only", "baseline"):
        a = agg[g]
        print(f"      [{g}] n={a['n_days']}  "
              f"h3 |ΔB|={fmt(a['h3']['mean_abs_dB'])}(n={a['h3']['n_obs']})  "
              f"h3 flips={fmt(a['h3']['mean_flips'], 2)}")

    print("[6/6] 写产物 ...")
    payload = dump_json(st, rows, events, breadth, n_codes, back, v1["meta"])
    build_html(payload, st, rows, events, breadth, back, n_codes)

    print("\n===== v2 摘要 =====")
    print("gap: mean=%.3f sd=%.3f (有效 %d/%d, 噪声 %d)" % (
        st["mean"], st["stdev"], st["n_gap_valid"], st["n_days"], st["n_noise"]))
    print("极端日:", [r["date"] for r in rows if r["extreme"]])
    print("收敛事件:", [(e["date"], round(e["pullback"], 3)) for e in events])
    ev = agg["event"]; bl = agg["baseline"]
    print(f"h=3 |Δ宽度|: 收敛日 {fmt(ev['h3']['mean_abs_dB'])} (n={ev['h3']['n_obs']}) vs "
          f"基线 {fmt(bl['h3']['mean_abs_dB'])} (n={bl['h3']['n_obs']})")


if __name__ == "__main__":
    main()
