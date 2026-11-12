# -*- coding: utf-8 -*-
"""后峰盘前策略简报 → 自包含 H5（可直接转发）。

## 为什么长这样

老田 2026-11-12 的要求：报告要能转发、要有券商研报的专业度。
券商盘前晨报的骨架是「结论先行 → 市场回顾 → 配置建议 → 个股 → 风险提示 → 免责」，
且每个数字都带参照物。本文件按这个骨架组织，但多两块券商没有、我们有的：

  · 筛选路径可追溯（共识池 → 剔除昨日涨停与龙虎榜 → 参与打分 → 入选 15，
    每一层剔了谁、为什么；各层数字一律由产物现算，注释里不写死示例值）
  · 信号台账的真实超额（历史批次跑下来到底赚没赚，不吹）

## 数据纪律

- 所有数字现算，模板里不写死任何会随日变化的量；
- 缺数据的板块显式写「没接上」，不静默拿旧值顶上；
- 涨红跌绿（A 股习惯）；
- 口径分歧处（如等权行业涨幅 vs 官方指数）当场注明，不混着比。

用法：
  python tools/preopen/build_preopen_report.py                  # 最近交易日
  python tools/preopen/build_preopen_report.py --date 20261111
  python tools/preopen/build_preopen_report.py --out x.html
"""
from __future__ import annotations

import argparse
import html
import io
import json
import os
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit_preopen as audit  # noqa: E402

DATA = audit.DATA
TS = audit.TS
ROOT = r"C:\项目\老田的AI助手"
DELIV = r"C:\项目\deliverables"
PICK_JSON = os.path.join(DELIV, "preopen_pick15_v2_adj_check_{day}.json")
LEDGER = os.path.join(ROOT, "portal", "daily_report", "cohorts", "status_latest.json")
SECTOR = os.path.join(ROOT, "portal", "daily_report", "lens", "sector_daily_synth.parquet")

INDEX_WANT = [("000001.SH", "上证指数"), ("399001.SZ", "深证成指"),
              ("399006.SZ", "创业板指"), ("000688.SH", "科创50"),
              ("000300.SH", "沪深300"), ("899050.BJ", "北证50")]


# ---------------------------------------------------------------------------
# 选票溯源（P1，2026-11-12）
# ---------------------------------------------------------------------------
# 简报最容易骗人的地方不是数字算错，是「这份名单什么时候算的」没有落款。
# 2026-11-12 判例：08:27 与 08:30 两次运行都 SkipPick=True，直接复用 11-11 的 json，
# 简报照样打 exec_day=20261112 —— 外观与当天新算完全一致，README 之外无从分辨。
# 所以：reused / picker_run_at 必须进报告，且读不到时写「未确认」，不能留白。
def read_provenance(run_json: str | None) -> dict:
    out = {"available": False, "reused": None, "picker_run_at": None,
           "picker_run_at_source": "unknown", "run_started": None,
           "launch_started": None, "note": "未提供本次运行记录（--run-json），来源未确认"}
    if not run_json or not os.path.exists(run_json):
        return out
    try:
        r = json.load(io.open(run_json, encoding="utf-8"))
    except (ValueError, OSError):
        return out
    pv = r.get("pick_provenance") or {}
    out.update({
        "available": True,
        "reused": pv.get("reused"),
        "picker_run_at": pv.get("picker_run_at"),
        "picker_run_at_source": pv.get("picker_run_at_source") or "unknown",
        "run_started": r.get("started"),
        "launch_started": (r.get("launch") or {}).get("started_at"),
        "data_day": r.get("data_day"),
        "exec_day": r.get("exec_day"),
        "note": None,
    })
    if out["reused"] is True:
        out["note"] = "本份名单为复用（本次运行未重算）"
    elif out["picker_run_at"] is None:
        out["note"] = "产物未自述计算时刻，来源未确认"
    return out



# ---------------------------------------------------------------------------
# 采集
# ---------------------------------------------------------------------------
def collect(day: str) -> dict:
    cal = audit.trade_cal()
    idx = cal.index(day) if day in cal else len(cal) - 1
    prev = cal[idx - 1] if idx > 0 else None
    d: dict = {"day": day, "prev": prev}
    # 执行日：数据日之后的第一个交易日。报告是「给哪天开盘用」的，
    # 顶栏必须写执行日，否则 11-12 早上发的报告写成 11-11 会让人以为是昨天的东西。
    later = [x for x in cal if x > day]
    d["exec_day"] = later[0] if later else None

    # 指数
    ix = audit.load_day("tdx/ts_index_daily", day)
    rows = []
    for code, name in INDEX_WANT:
        r = ix[ix.ts_code == code]
        if len(r):
            x = r.iloc[0]
            rows.append({"code": code, "name": name, "close": float(x["close"]),
                         "pct": float(x["pct_chg"]) if pd.notna(x.get("pct_chg")) else None})
    d["index"] = rows

    # 全市场广度
    st = audit.load_day("tdx/ts_daily", day)
    st["pct"] = (st["close"] / st["pre_close"] - 1) * 100
    d["breadth"] = {
        "n": int(len(st)),
        "up": int((st.pct > 0).sum()),
        "dn": int((st.pct < 0).sum()),
        "flat": int((st.pct == 0).sum()),
        "amount_yi": round(float(pd.to_numeric(st.amount, errors="coerce").sum()) / 1e5, 0),
        "median_pct": round(float(st.pct.median()), 2),
    }
    lim = audit.limit_counts(st)
    d["limit"] = {"up": lim["up"], "dn": lim["dn"]}
    if prev:
        pv = audit.load_day("tdx/ts_daily", prev)
        pl = audit.limit_counts(pv)
        d["limit"]["prev_up"] = pl["up"]
        d["limit"]["prev_dn"] = pl["dn"]
        pv["pct"] = (pv["close"] / pv["pre_close"] - 1) * 100
        d["breadth"]["prev_up"] = int((pv.pct > 0).sum())
        d["breadth"]["prev_amount_yi"] = round(
            float(pd.to_numeric(pv.amount, errors="coerce").sum()) / 1e5, 0)

        # 情绪接力：昨日涨停这批票今天怎么样了（券商盘前必写的一项）
        prev_up_codes = set(pl["up_codes"])
        if prev_up_codes:
            cur = st[st.ts_code.isin(prev_up_codes)].copy()
            cur["pct"] = (cur["close"] / cur["pre_close"] - 1) * 100
            cur_up = set(lim["up_codes"])
            d["relay"] = {
                "n": int(len(cur)),
                "avg_pct": round(float(cur.pct.mean()), 2) if len(cur) else None,
                "win_rate": round(float((cur.pct > 0).mean()) * 100, 0) if len(cur) else None,
                "again": len(set(cur.ts_code) & cur_up),
                "red": int((cur.pct < 0).sum()),
            }

    # 行业强弱（自合成等权）
    if os.path.exists(SECTOR):
        sc = pd.read_parquet(SECTOR)
        sc["trade_date"] = sc.trade_date.astype(str)
        cur = sc[sc.trade_date == day].sort_values("ret", ascending=False)
        if not len(cur):
            # 文件在、目标日没有 = 上游没产出。旧代码照样塞一个空 dict，
            # derive() 里 s["top"][0] 直接 IndexError，整份报告不出（11-12 判例）。
            # 非必需数据块只能让本节降级，不能干掉整份报告。
            d["sector_missing"] = (
                "行业强弱数据最新只到 %s，未接上数据日 %s；本节略过"
                "（上游未产出，不是本地漏跑，也不是架构停更）"
                % (str(sc["trade_date"].max()) if len(sc) else "无任何数据", day))
        else:
            d["sector"] = {
                "n": int(len(cur)),
                "top": [{"name": r.sector_name, "ret": round(float(r.ret), 2),
                         "up_ratio": round(float(r.up_ratio) * 100, 0)}
                        for r in cur.head(5).itertuples()],
                "bottom": [{"name": r.sector_name, "ret": round(float(r.ret), 2),
                            "up_ratio": round(float(r.up_ratio) * 100, 0)}
                           for r in cur.tail(3).itertuples()],
                "up_n": int((cur.ret > 0).sum()),
                "note": "等权口径，非市值加权，只用于行业间强弱，不与申万官方指数比绝对水平",
            }

    # 精选名单
    fp = PICK_JSON.format(day=day)
    if os.path.exists(fp):
        pk = json.load(io.open(fp, encoding="utf-8"))
        d["picks"] = pk.get("top15_adj") or []
        d["picks_meta"] = {
            "pool_n": pk.get("pool_n"), "package_asof": pk.get("package_asof"),
            "excluded_limit_up": pk.get("excluded_limit_up") or [],
            "toplist": pk.get("toplist_filter") or {},
            "score_mode": pk.get("score_mode"),
            "refine_date": pk.get("refine_date"),
            # 抽屉里要展示动量名次（0~1 的池内百分位）。打分表在 full_rated，
            # 只给 r10（原始 10 日动量）会与「按名次计分」的文案自相矛盾。
            "full_rated": pk.get("full_rated") or [],
        }
    else:
        d["picks"] = []
        d["picks_meta"] = {"missing": "未找到 %s 的精选产物" % os.path.basename(fp)}

    # 信号台账
    if os.path.exists(LEDGER):
        lg = json.load(io.open(LEDGER, encoding="utf-8"))
        d["ledger"] = {
            "today": lg.get("today"), "run_at": lg.get("run_at"),
            "price_ok": lg.get("price_ok", True),
            "cohorts": [{"pool": c.get("pool"), "asof": c.get("asof"),
                         "n": c.get("n"), "day_index": c.get("day_index"),
                         "excess": c.get("excess")}
                        for c in (lg.get("cohorts") or [])],
            "rolling": lg.get("rolling") or {},
        }
    else:
        d["ledger"] = None

    # 数据体检
    d["audit"] = audit.audit(day)
    return d


# ---------------------------------------------------------------------------
# 派生判断（结论要落在数字上，不写空话）
# ---------------------------------------------------------------------------
def derive(d: dict) -> dict:
    out: dict = {"headline": "", "points": []}
    b, lim = d["breadth"], d["limit"]
    idx = {x["name"]: x for x in d["index"]}

    cyb = idx.get("创业板指", {}).get("pct")
    sh = idx.get("上证指数", {}).get("pct")
    if cyb is not None and sh is not None:
        gap = cyb - sh
        style = "成长明显强于权重" if gap > 2 else ("成长略强" if gap > 0.5
                                                 else ("权重更强" if gap < -0.5 else "风格均衡"))
        out["points"].append(
            "**风格**：创业板指 %+.2f%%、上证 %+.2f%%，相差 %.2f 个百分点，%s。"
            % (cyb, sh, gap, style))

    up_ratio = b["up"] / max(b["n"], 1) * 100
    out["points"].append(
        "**广度**：%d 只上涨 / %d 只下跌（上涨占比 %.0f%%），涨跌中位数 %+.2f%%；"
        "涨停 %d 家、跌停 %d 家（前一日同口径 %s / %s）。"
        % (b["up"], b["dn"], up_ratio, b["median_pct"], lim["up"], lim["dn"],
           lim.get("prev_up", "-"), lim.get("prev_dn", "-")))

    amt = b["amount_yi"]
    prev_amt = b.get("prev_amount_yi")

    def wan(x):
        return ("%.2f 万亿" % (x / 10000)) if x >= 10000 else ("%.0f 亿元" % x)

    if prev_amt:
        delta = (amt / prev_amt - 1) * 100
        out["points"].append(
            "**量能**：全市场成交 %s，较前一日 %s%%（%s）。"
            % (wan(amt), "%+.1f" % delta, wan(prev_amt)))

    if d.get("sector") and d["sector"]["top"] and d["sector"]["bottom"]:
        s = d["sector"]
        t = s["top"][0]
        out["points"].append(
            "**行业**：%d 个申万一级行业中 %d 个上涨；领涨 %s（%+.2f%%，成分股 %.0f%% 上涨），"
            "最弱 %s（%+.2f%%）。"
            % (s["n"], s["up_n"], t["name"], t["ret"], t["up_ratio"],
               s["bottom"][0]["name"], s["bottom"][0]["ret"]))
    elif d.get("sector_missing"):
        out["points"].append("**行业**：%s" % d["sector_missing"])

    picks = d.get("picks") or []
    if picks:
        from collections import Counter
        ind = Counter(p.get("industry") or "其他" for p in picks)
        top_ind, top_n = ind.most_common(1)[0]
        out["points"].append(
            "**名单**：%d 只入选，行业上最集中是%s（%d 只），共覆盖 %d 个行业。"
            % (len(picks), top_ind, top_n, len(ind)))

    lg = d.get("ledger") or {}
    ro = (lg.get("rolling") or {})
    if ro:
        seg = []
        for k in ("1", "3"):
            v = ro.get(k)
            if v:
                seg.append("持有 %s 日 %+.2f 个百分点（胜率 %.0f%%，样本 %d）"
                           % (k, v.get("mean_pp", 0), v.get("win", 0), v.get("n", 0)))
        if seg:
            out["points"].append("**台账**：已结算批次超额 —— " + "；".join(seg) + "。")
        dg = ro.get("_degrade") or {}
        if dg.get("n"):
            out["points"].append(
                "**降级线**：最近 %d 个已结算样本，超额均值 %+.2f 个百分点，"
                "距降级阈值（%.1f）还有 %.2f 个百分点。"
                % (dg["n"], float(dg.get("mean_pp") or 0),
                   float(dg.get("line_value") or -1.0),
                   abs(float(dg.get("mean_pp") or 0) + 1.0)))

    # 标题：取风格 + 名单主线，不喊口号
    parts = []
    if idx.get("创业板指", {}).get("pct") is not None and idx.get("上证指数", {}).get("pct") is not None:
        g = idx["创业板指"]["pct"] - idx["上证指数"]["pct"]
        parts.append("成长领涨" if g > 2 else ("大小盘均衡" if abs(g) <= 1 else "权重占优"))
    if picks:
        from collections import Counter
        ind = Counter(p.get("industry") or "其他" for p in picks)
        parts.append("名单偏向" + ind.most_common(1)[0][0])
    out["headline"] = "，".join(parts) if parts else "盘前策略简报"
    return out


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def cls(v: float | None) -> str:
    if v is None:
        return ""
    return "up" if v > 0 else ("dn" if v < 0 else "flat")


def arrow(v: float | None) -> str:
    if v is None:
        return ""
    return "▲" if v > 0 else ("▼" if v < 0 else "—")


CSS = """
:root{
  --paper:#f4f5f7; --card:#fff; --ink:#16181d; --ink2:#4a5160; --ink3:#8b93a1;
  --line:#e4e7ec; --line2:#eef0f3;
  --brand:#1a4fd6; --brandsoft:#eef2fd;
  --up:#d92b2b; --dn:#0a8f4b; --warn:#b26a00; --ok:#0a8f4b;
}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--paper);color:var(--ink);
  font:15px/1.72 -apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:760px;margin:0 auto;background:var(--card);min-height:100vh;
  box-shadow:0 0 0 1px var(--line)}
.topbar{display:flex;align-items:center;justify-content:space-between;
  padding:11px 20px;border-bottom:1px solid var(--line);background:#fbfcfd}
.topbar .brand{font-size:13px;font-weight:700;letter-spacing:.5px;color:var(--brand)}
.topbar .brand i{font-style:normal;color:var(--ink3);font-weight:400;margin-left:6px;font-size:11.5px}
.topbar .dt{font-size:11.5px;color:var(--ink3);font-variant-numeric:tabular-nums}

header{padding:26px 20px 18px;border-bottom:1px solid var(--line)}
.kicker{font-size:11.5px;color:var(--ink3);letter-spacing:1.6px;margin-bottom:9px}
header h1{font-size:25px;line-height:1.38;letter-spacing:-.3px;font-weight:700}
header .sub{margin-top:9px;font-size:13px;color:var(--ink2);line-height:1.7}
.meta{display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:13px;font-size:11.5px;color:var(--ink3)}
.meta b{color:var(--ink2);font-weight:600;font-variant-numeric:tabular-nums}

.nums{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line);
  border-bottom:1px solid var(--line)}
.nums .c{background:#fff;padding:13px 8px 11px;text-align:center}
.nums .c b{display:block;font-size:19px;font-variant-numeric:tabular-nums;letter-spacing:-.5px}
.nums .c span{display:block;font-size:10.5px;color:var(--ink3);margin-top:3px}
.nums .c em{display:block;font-style:normal;font-size:10px;color:var(--ink3);margin-top:1px}
.up{color:var(--up)}.dn{color:var(--dn)}.flat{color:var(--ink3)}

section{padding:20px 20px 6px}
h2{font-size:15.5px;font-weight:700;letter-spacing:.2px;display:flex;align-items:baseline;gap:8px;
  margin-bottom:12px}
h2 i{font-style:normal;font-size:11px;color:var(--brand);font-weight:600;
  background:var(--brandsoft);padding:2px 7px;border-radius:3px;letter-spacing:.5px}
h2 small{font-size:11.5px;color:var(--ink3);font-weight:400;margin-left:auto}

ol.pts{list-style:none;counter-reset:p}
ol.pts li{counter-increment:p;position:relative;padding-left:26px;margin-bottom:11px;
  font-size:13.8px;line-height:1.8;color:var(--ink2)}
ol.pts li::before{content:counter(p);position:absolute;left:0;top:3px;width:17px;height:17px;
  background:var(--brandsoft);color:var(--brand);border-radius:50%;font-size:10.5px;
  font-weight:700;display:flex;align-items:center;justify-content:center;font-variant-numeric:tabular-nums}
ol.pts b{color:var(--ink);font-weight:600}

table{width:100%;border-collapse:collapse;font-size:13px;margin:2px 0 6px}
th{font-size:11px;color:var(--ink3);font-weight:500;text-align:right;padding:7px 6px;
  border-bottom:1px solid var(--line);white-space:nowrap}
th:first-child,td:first-child{text-align:left}
td{padding:8px 6px;border-bottom:1px solid var(--line2);text-align:right;
  font-variant-numeric:tabular-nums;color:var(--ink2)}
td.nm{color:var(--ink);font-weight:600}
tr:last-child td{border-bottom:none}
td .code{display:block;font-size:10px;color:var(--ink3);font-weight:400;margin-top:1px}

.funnel{display:flex;align-items:stretch;gap:1px;margin:6px 0 12px;background:var(--line);
  border:1px solid var(--line);border-radius:8px;overflow:hidden}
.funnel .f{flex:1;background:#fff;padding:11px 6px;text-align:center}
.funnel .f b{display:block;font-size:17px;font-variant-numeric:tabular-nums}
.funnel .f span{display:block;font-size:10.5px;color:var(--ink3);margin-top:2px}
.funnel .f.drop b{color:var(--ink3);font-size:14px}

.card{background:#fbfcfd;border:1px solid var(--line);border-radius:9px;padding:12px 14px;
  margin:10px 0;font-size:12.8px;line-height:1.8;color:var(--ink2)}
.card b{color:var(--ink)}
.card.warn{background:#fffaf0;border-color:#f3e3c3;border-left:3px solid var(--warn)}
.card.info{background:var(--brandsoft);border-color:#d9e2fb;border-left:3px solid var(--brand)}

details{border-bottom:1px solid var(--line2);padding:9px 0}
details summary{cursor:pointer;font-size:13px;color:var(--ink);font-weight:600;
  list-style:none;display:flex;align-items:center;gap:8px}
details summary::-webkit-details-marker{display:none}
details summary::before{content:"＋";color:var(--brand);font-size:12px;flex:none}
details[open] summary::before{content:"－"}
details .body{padding:9px 0 3px 20px;font-size:12.5px;color:var(--ink2);line-height:1.85}
details .body .kv2{display:flex;flex-wrap:wrap;gap:4px 16px;margin-top:6px;
  font-size:11.5px;color:var(--ink3)}
details .body .kv2 b{color:var(--ink2);font-variant-numeric:tabular-nums}

.disc{padding:18px 20px 30px;border-top:1px solid var(--line);background:#fafbfc}
.disc p{font-size:10.8px;color:var(--ink3);line-height:1.85;margin-bottom:7px}
.disc b{color:var(--ink2)}
.foot{padding:0 20px 24px;background:#fafbfc;font-size:10.5px;color:#a8afba}
@media(max-width:430px){
  header h1{font-size:21px}
  .nums{grid-template-columns:repeat(2,1fr)}
  section{padding:16px 15px 4px}
  .topbar{padding:10px 15px}
  header{padding:20px 15px 15px}
  table{font-size:12.2px}
  td,th{padding:7px 4px}
}
"""


def render(d: dict) -> str:
    dv = derive(d)
    # 三态，不是布尔：true=本次复用 / false=本次重算 / unknown=来源未确认。
    # 把 unknown 压成 false 就是「不知道」被写成「是新的」，这正是要防的。
    pvv = d.get("provenance") or {}
    prov_reused_attr = ("true" if pvv.get("reused") is True
                        else "false" if pvv.get("reused") is False
                        else "unknown")
    day = d["day"]
    daycn = "%s月%s日" % (day[4:6], day[6:])
    ex_day = d.get("exec_day") or day
    excn = "%s年%s月%s日" % (ex_day[:4], ex_day[4:6], ex_day[6:])
    b, lim = d["breadth"], d["limit"]
    idx = d["index"]
    P = []
    A = P.append

    A("<!DOCTYPE html><html lang=\"zh-CN\"")
    # 机器可读的溯源：写在 <html> 属性上，扫一遍文件就能验「这份是不是复用的」，
    # 不用渲染不用 OCR。人眼看的版本在同一页的 meta / 提示卡 / 页脚（三处一致）。
    A(" data-picker-run-at=\"%s\" data-reused=\"%s\" data-run-started=\"%s\">"
      % (esc(pvv.get("picker_run_at") or ""), prov_reused_attr,
         esc(pvv.get("run_started") or "")))
    A("<head><meta charset=\"utf-8\">")
    A("<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">")
    A("<title>后峰盘前简报 %s</title>" % excn)
    A("<meta name=\"description\" content=\"后峰盘前策略简报，%s开盘参考，基于%s收盘数据\"%s>"
      % (excn, daycn, ""))
    A("<style>%s</style></head><body><div class=\"wrap\">" % CSS)

    # 顶栏：日期是「这份报告给哪天用」，不是数据日
    A("<div class=\"topbar\"><div class=\"brand\">后峰<i>· 盘前策略简报</i></div>"
      "<div class=\"dt\">%s 盘前</div></div>" % excn)

    # 报头
    A("<header>")
    A("<div class=\"kicker\">PRE-OPEN BRIEFING · %s</div>" % ex_day)
    A("<h1>%s</h1>" % esc(dv["headline"]))
    A("<div class=\"sub\">基于 %s 收盘数据筛选，供 <b>%s 开盘</b>执行参考。"
      "结论先行，全部数字可追溯到当日行情。</div>"
      % (daycn, "%s月%s日" % (ex_day[4:6], ex_day[6:])))
    A("<div class=\"meta\">")
    A("<span>数据基准 <b>%s</b></span>" % day)
    if d.get("picks_meta", {}).get("package_asof"):
        A("<span>共识池 <b>%s</b></span>" % d["picks_meta"]["package_asof"])
    A("<span>标的池 <b>%s</b> 只</span>" % (d["picks_meta"].get("pool_n") or "-"))
    A("<span>入选 <b>%d</b> 只</span>" % len(d.get("picks") or []))
    A("<span>生成 <b>%s</b></span>" % datetime.now().strftime("%m-%d %H:%M"))
    # 选票时刻必须和「生成时刻」并排：生成 08:30 而名单算于昨天 09:36，
    # 只看生成时间会以为名单是刚算的。这一格是 P1 的核心，别删。
    if pvv.get("picker_run_at"):
        A("<span>选票 <b>%s %s</b></span>"
          % (esc(str(pvv["picker_run_at"])[5:16]),
             "复用" if pvv.get("reused") is True else "重算"))
    else:
        A("<span>选票 <b>来源未确认</b></span>")
    A("</div></header>")

    # 关键数字
    A("<div class=\"nums\">")
    for code, nm in [("上证指数", "上证"), ("创业板指", "创业板")]:
        x = next((i for i in idx if i["name"] == code), None)
        if x:
            A("<div class=\"c\"><b class=\"%s\">%s%.2f%%</b><span>%s</span>"
              "<em>%.2f</em></div>" % (cls(x["pct"]), arrow(x["pct"]), abs(x["pct"]), nm, x["close"]))
    A("<div class=\"c\"><b class=\"up\">%d</b><span>涨停家数</span><em>前一日 %s</em></div>"
      % (lim["up"], lim.get("prev_up", "-")))
    amt_txt, amt_unit = (("%.2f" % (b["amount_yi"] / 10000), "万亿")
                         if b["amount_yi"] >= 10000 else
                         ("%.0f" % b["amount_yi"], "亿元"))
    A("<div class=\"c\"><b>%s</b><span>成交额（%s）</span><em>涨 %d / 跌 %d</em></div>"
      % (amt_txt, amt_unit, b["up"], b["dn"]))
    A("</div>")

    # 一、核心观点
    A("<section><h2><i>01</i>核心观点<small>每条都带数字，可自行复核</small></h2>")
    A("<ol class=\"pts\">")
    for p in dv["points"]:
        s = esc(p)
        # 只放行 **加粗**，其余保持纯文本
        while "**" in s:
            s = s.replace("**", "<b>", 1)
            if "**" in s:
                s = s.replace("**", "</b>", 1)
        A("<li>%s</li>" % s)
    A("</ol></section>")

    # 二、昨日市场
    A("<section><h2><i>02</i>昨日市场<small>%s 收盘</small></h2>" % daycn)
    A("<table><thead><tr><th>指数</th><th>收盘</th><th>涨跌幅</th></tr></thead><tbody>")
    for x in idx:
        A("<tr><td class=\"nm\">%s</td><td>%.2f</td><td class=\"%s\">%s %+.2f%%</td></tr>"
          % (esc(x["name"]), x["close"] or 0, cls(x["pct"]), arrow(x["pct"]), x["pct"] or 0))
    A("</tbody></table>")

    # 情绪接力：昨日涨停这批票今天怎么样（判断接力意愿，比涨停家数更有信息量）
    rl = d.get("relay")
    if rl and rl.get("n"):
        lianban = rl["again"] / rl["n"] * 100
        A("<div class=\"card\">昨日涨停 <b>%d</b> 只，今日平均 <b class=\"%s\">%+.2f%%</b>；"
          "其中 <b>%d</b> 只继续封板（连板率 <b>%.0f%%</b>），<b>%d</b> 只收绿，"
          "上涨占比 <b>%.0f%%</b>。%s</div>"
          % (rl["n"], cls(rl.get("avg_pct")), rl.get("avg_pct") or 0, rl["again"], lianban,
             rl["red"], rl.get("win_rate") or 0,
             "接力意愿强，情绪处于扩散阶段。" if lianban >= 25 else
             ("接力一般，情绪偏存量博弈。" if lianban >= 12 else
              "接力弱，涨停股次日普遍承压，谨防高位标的。")))
    if d.get("sector") and d["sector"]["top"] and d["sector"]["bottom"]:
        s = d["sector"]
        A("<table style=\"margin-top:14px\"><thead><tr><th>行业（申万一级）</th>"
          "<th>涨跌幅</th><th>成分上涨占比</th></tr></thead><tbody>")
        A("<tr><td colspan=\"3\" style=\"text-align:left;color:#8b93a1;font-size:11px;"
          "padding:6px;border-bottom:1px solid var(--line2)\">领涨 5</td></tr>")
        for r in s["top"]:
            A("<tr><td class=\"nm\">%s</td><td class=\"%s\">%+.2f%%</td><td>%.0f%%</td></tr>"
              % (esc(r["name"]), cls(r["ret"]), r["ret"], r["up_ratio"]))
        A("<tr><td colspan=\"3\" style=\"text-align:left;color:#8b93a1;font-size:11px;"
          "padding:6px;border-bottom:1px solid var(--line2)\">滞后 3</td></tr>")
        for r in s["bottom"]:
            A("<tr><td class=\"nm\">%s</td><td class=\"%s\">%+.2f%%</td><td>%.0f%%</td></tr>"
              % (esc(r["name"]), cls(r["ret"]), r["ret"], r["up_ratio"]))
        A("</tbody></table>")
        A("<div class=\"card\">行业涨跌幅为 <b>成分股等权均值</b>，不是市值加权，"
          "与申万官方指数存在系统性差异（等权偏小盘）。此处只用于判断"
          "<b>行业之间谁强谁弱</b>与上涨广度，不与官方指数比绝对水平。</div>")
    else:
        A("<div class=\"card warn\">%s</div>"
          % esc(d.get("sector_missing") or "行业强弱数据未接上本次数据日，本节略过。"))
    A("</section>")

    # 三、今日名单
    picks = d.get("picks") or []
    A("<section><h2><i>03</i>今日名单<small>%d 只</small></h2>" % len(picks))
    # 复用的显式提示：数字没错、时点错了才是错的。不标出来，转发出去的人
    # 会把它当成「今天早上刚算的名单」——2026-11-12 就是这个情形。
    if pvv.get("reused") is True:
        A("<div class=\"card warn\">本份名单为<b>复用</b>：算于 <b>%s</b>，本次运行未重算。"
          "名单本身有效，但它绑定的是那个时点的池与行情；之后若有新数据或口径调整，"
          "这里不会体现。</div>" % esc(pvv.get("picker_run_at") or "未知时刻"))
    elif not pvv.get("available"):
        A("<div class=\"card warn\">选票来源<b>未确认</b>：本次未提供运行记录，"
          "无法判断名单是刚算的还是沿用的旧文件 —— 请核对产物时间。</div>")
    if picks:
        A("<table><thead><tr><th>标的</th><th>行业</th><th>评分</th>"
          "<th>昨收</th><th>昨日涨幅</th><th>10日动量</th></tr></thead><tbody>")
        for r in picks:
            A("<tr><td class=\"nm\">%s<span class=\"code\">%s</span></td><td>%s</td>"
              "<td>%.3f</td><td>%.2f</td><td class=\"%s\">%+.2f%%</td><td class=\"%s\">%+.2f%%</td></tr>"
              % (esc(r.get("name")), esc(r.get("ts_code")), esc(r.get("industry") or "-"),
                 float(r.get("score") or 0), float(r.get("close_last") or 0),
                 cls(r.get("chg_last")), float(r.get("chg_last") or 0),
                 cls(r.get("r10")), float(r.get("r10") or 0)))
        A("</tbody></table>")

        # 打分构成（券商不给，我们给，可复核）
        # 权重与「本票取值」必须分开：旧版把四个因子取值排在「四项加权」后面，
        # 读者会当成权重；且文案说动量按名次计，展示的却是原始 10 日动量。
        rank_map = {x.get("ts_code"): x.get("mom_rank")
                    for x in ((d.get("picks_meta") or {}).get("full_rated") or [])}
        A("<div style=\"margin-top:12px\">")
        for i, r in enumerate(picks, 1):
            rk = rank_map.get(r.get("ts_code"))
            A("<details><summary>%d. %s · 评分拆解</summary><div class=\"body\">"
              % (i, esc(r.get("name"))))
            A("评分权重（固定，全池一致）：量能健康 <b>0.45</b>、收涨确认 <b>0.35</b>、"
              "动量名次 <b>0.10</b>、均线趋势 <b>0.10</b>（取值 0/1/2 折半计入）；"
              "流动性 <b>0.00</b>，等于不参与打分。")
            A("<div style=\"margin-top:6px\">本票取值：量能健康 <b>%.2f</b>、"
              "收涨确认 <b>%.2f</b>、均线趋势 <b>%s</b>、动量名次 <b>%s</b>"
              "（池内百分位，0 最低、1 最高）。</div>"
              % (float(r.get("vol_h") or 0), float(r.get("confirm") or 0),
                 esc(r.get("trend")),
                 ("%.3f" % float(rk)) if rk is not None else "未记录"))
            A("<div class=\"kv2\">")
            A("<span>量能倍数 <b>%.2f</b></span>" % float(r.get("vr") or 0))
            A("<span>后复权10日动量 <b>%+.2f%%</b></span>" % float(r.get("r10") or 0))
            A("<span>不复权对照 <b>%+.2f%%</b></span>" % float(r.get("r10_raw") or 0))
            A("<span>除权扭曲 <b>%.2f pp</b></span>" % abs(float(r.get("adj_div") or 0)))
            A("</div>")
            A("<div style=\"margin-top:6px;color:#666\">量能倍数 = 当日成交量 ÷ 前 20 日均量，"
              "不是分时口径的「量比」；落在 0.8~2.5 记满分，超出按相邻档折半。</div>")
            A("</div></details>")
        A("</div>")
        A("<div class=\"card info\">评分只用于<b>同池内排序</b>，不是收益预测。"
          "权重按日频横截面分层实测调整过（量能健康与收涨确认为正贡献，"
          "流动性分层实测无区分度、权重置 0），但样本期有限，"
          "仍需以台账实际超额为准。</div>")
    else:
        A("<div class=\"card warn\">本日未找到精选产物（%s）。"
          "缺产物不等于没有信号，是筛选环节没跑通 —— 需先补跑再出简报。</div>"
          % esc(d.get("picks_meta", {}).get("missing", "")))
    A("</section>")

    # 四、筛选路径
    pm = d.get("picks_meta") or {}
    if pm.get("pool_n"):
        tl = pm.get("toplist") or {}
        ex = pm.get("excluded_limit_up") or []
        A("<section><h2><i>04</i>筛选路径<small>每一层都可追溯</small></h2>")
        A("<div class=\"funnel\">")
        A("<div class=\"f\"><b>%d</b><span>共识池</span></div>" % pm["pool_n"])
        A("<div class=\"f drop\"><b>−%d</b><span>昨日涨停</span></div>" % len(ex))
        A("<div class=\"f drop\"><b>−%d</b><span>龙虎榜</span></div>" % len(tl.get("dropped") or []))
        A("<div class=\"f\"><b>%d</b><span>参与打分</span></div>"
          % (pm["pool_n"] - len(ex) - len(tl.get("dropped") or [])))
        A("<div class=\"f\"><b>%d</b><span>入选</span></div>" % len(picks))
        A("</div>")
        if ex:
            A("<div class=\"card\">昨日涨停剔除：<b>%s</b>。判据是当日封板（最高价等于收盘价）"
              "且涨幅超 9%%，与首页按收盘价触板统计的涨停家数不是同一个口径；"
              "理由是次日开盘追价不可控，不是不看好。</div>" % esc("、".join(ex)))
        if tl.get("dropped"):
            names = "、".join("%s %s" % (x.get("ts_code"), x.get("name")) for x in tl["dropped"])
            A("<div class=\"card\">龙虎榜剔除：<b>%s</b>。口径为 T 日或 T-1 日上榜即剔，"
              "任意方向。全市场当日上榜 %s 只。%s</div>"
              % (esc(names), tl.get("n_hits_all", "-"),
                 ("该日龙虎榜本地尚未落盘，已" + esc(tl.get("fallback_note", "").replace("本地缺 20261111，", "")))
                 if tl.get("source") == "source_fallback" else ""))
        A("</section>")

    # 五、信号台账
    lg = d.get("ledger")
    A("<section><h2><i>05</i>信号台账<small>历史批次跑成什么样</small></h2>")
    if lg:
        cs = lg.get("cohorts") or []
        if cs:
            A("<table><thead><tr><th>池</th><th>信号日</th><th>只数</th>"
              "<th>已跟踪</th><th>累计超额</th></tr></thead><tbody>")
            for c in cs:
                ex_ = c.get("excess")
                small = (c.get("n") or 0) < 10
                A("<tr><td class=\"nm\">%s<span class=\"code\">%s</span></td><td>%s</td>"
                  "<td>%s</td><td>%s 日</td><td class=\"%s\">%s</td></tr>"
                  % ("主选" if c.get("pool") == "main" else "自动",
                     "样本不足" if small else "",
                     esc(c.get("asof")), esc(c.get("n")), esc(c.get("day_index") or "-"),
                     cls(None if ex_ is None else ex_ * 100),
                     "—" if ex_ is None else "%+.2f%%" % (ex_ * 100)))
            A("</tbody></table>")
            A("<div class=\"card warn\">上表是<b>已发生</b>的成绩，不是预期。"
              "超额为同窗口相对两融池等权基准的差，未扣交易成本；"
              "扣 0.3%% 成本后普遍再低一档。<b>负超额说明该批次没跑赢基准</b>，"
              "照实列出。</div>")
    else:
        A("<div class=\"card warn\">台账未接上（cohorts/status_latest.json 不存在），"
          "本节省略 —— 不给旧数字充数。</div>")
    A("</section>")

    # 六、数据体检
    au = d.get("audit") or {}
    A("<section><h2><i>06</i>数据体检<small>出报告前先证明数据能用</small></h2>")
    tagmap = {"ok": "已到", "pending": "按频率未到", "warn": "降级", "missed": "缺失",
              "empty_local": "本地无"}
    A("<table><thead><tr><th>数据源</th><th>最新日</th><th>状态</th></tr></thead><tbody>")
    for s in (au.get("sources") or []):
        A("<tr><td class=\"nm\">%s<span class=\"code\">%s</span></td><td>%s</td>"
          "<td class=\"%s\">%s</td></tr>"
          % (esc(s["name"]), esc(s.get("use") or ""), esc(s.get("max_date") or "-"),
             "dn" if s["state"] == "ok" else ("flat" if s["state"] == "pending" else "up"),
             tagmap.get(s["state"], s["state"])))
    A("</tbody></table>")
    stt = au.get("structure") or {}
    if stt.get("rows"):
        A("<div class=\"card\">日线体检：<b>%d</b> 行；价格自相矛盾 <b>%d</b> 条；"
          "涨跌幅越界 <b>%d</b> 条；区间 <b>%s%%~%s%%</b>；"
          "前收盘与上一交易日对不上 <b>%s</b> 条（含除权与新股）。</div>"
          % (stt["rows"], stt.get("bad_high", 0) + stt.get("bad_low", 0),
             stt.get("pct_extreme", 0), stt.get("pct_min"), stt.get("pct_max"),
             stt.get("pre_close_gap", "-")))
    cc = (au.get("limit") or {}).get("cross_check")
    if cc:
        lag_txt = ("%s" % cc["lag_days"]) if cc.get("lag_days") is not None else "若干"
        if cc.get("official_empty"):
            A("<div class=\"card\">交易所涨跌停名单 %s 当日为 <b>0 行</b>"
              "（上游给空或尚未落盘），本日不做交叉比对；"
              "首页涨停家数按自算口径，只用于判断情绪。</div>" % esc(cc["day"]))
        elif not cc.get("is_target_day", True):
            A("<div class=\"card\">涨跌停交叉验证用的是<b>历史日 %s</b>"
              "（距本次数据日 %s 个交易日）：交易所名单 %d 家，自算 %d 家，"
              "自算独有 %d 家。这是口径核对，<b>不是当日问题</b>，不影响今天的名单。</div>"
              % (esc(cc["day"]), esc(lag_txt), cc["official_up"],
                 cc["mine_up"], cc["mine_only"]))
        else:
            miss = cc["official_only"]
            A("<div class=\"card\">涨跌停自算与交易所名单交叉验证（%s）：官方 %d 家，"
              "自算 %d 家，%s自算独有 %d 家（新股/边界）。%s</div>"
              % (cc["day"], cc["official_up"], cc["mine_up"],
                 "官方独有 <b>%d</b> 家（自算漏判），" % miss if miss else "官方涨停<b>无一遗漏</b>，",
                 cc["mine_only"],
                 "自算<b>未漏判</b>官方涨停，可用于盘前情绪判断。"
                 if miss == 0 else "自算存在漏判，情绪数字仅供参考。"))
        A("<div class=\"card info\">两个涨停口径各管一件事：首页「涨停家数」按收盘价触板自算，"
          "用来看情绪；筛选路径里剔除的「昨日涨停」按封板且涨幅超 9% 判定，"
          "用来避开次日开盘追价。数字不必相等。</div>")
    if au.get("verdict") == "warn":
        A("<div class=\"card warn\">本次体检有 %d 项告警，照实列出：<br>%s</div>"
          % (len(au.get("warns") or []),
             "<br>".join("· " + esc(w) for w in (au.get("warns") or []))))
    A("</section>")

    # 七、风险与边界
    A("<section><h2><i>07</i>风险与边界<small>这一节不省略</small></h2>")
    A("<ol class=\"pts\">")
    A("<li><b>执行时点</b>：名单由 %s 收盘数据选出，<b>次日开盘</b>才是口径内的买入时点；"
      "盘中追高不在建议范围内。</li>" % daycn)
    A("<li><b>共识池滞后</b>：标的池来自 %s 的策略推送包，盘前不再更新；"
      "池外个股无论多强都进不了本名单。</li>" % esc(pm.get("package_asof") or "-"))
    A("<li><b>单日信号</b>：本名单是单日横截面结果，未做组合优化、行业中性、"
      "也未考虑冲击成本与容量，不适合直接当作组合权重。</li>")
    A("<li><b>历史不代表未来</b>：台账里的超额是回看口径，样本期短，"
      "且已出现负超额批次；把它当业绩承诺是不对的。</li>")
    A("<li><b>数据口径</b>：行业涨跌为等权；涨跌停为按板块限制自算（10/20/30%%）；"
      "两融与官方涨跌停名单按交易所频率 T+1 才有。</li>")
    A("</ol></section>")

    # 免责
    A("<div class=\"disc\">")
    A("<p><b>性质说明：</b>本简报是内部量化研究输出，<b>不是证券研究报告</b>，"
      "不由持牌证券分析师撰写，不构成任何投资建议、要约或承诺。</p>")
    A("<p><b>数据来源：</b>交易所公开行情、上市公司法定披露信息及内部加工的衍生指标。"
      "数据经自动化流程处理，可能存在遗漏或误差，使用前请以交易所与上市公司公告为准。</p>")
    A("<p><b>利益冲突：</b>本简报编制方可能持有文中提及证券，"
      "且不承担因此产生的任何责任。历史表现不预示未来收益，投资决策及后果由使用者自行承担。</p>")
    A("<p><b>转发与引用：</b>本简报可<b>全文转发</b>给内部同事与合作伙伴。"
      "请勿截图断章、删去风险提示与免责声明，或改动其中任何数字与结论 —— "
      "报告的价值在于口径可追溯，掐掉口径的片段会误导他人。</p>")
    A("</div>")
    _p_tag = ("复用" if pvv.get("reused") is True
              else "重算" if pvv.get("reused") is False else None)
    A("<div class=\"foot\">后峰 · 盘前策略简报　|　数据基准 %s　|　生成 %s"
      "　|　选票 %s%s</div>"
      % (day, datetime.now().strftime("%Y-%m-%d %H:%M"),
         esc(pvv.get("picker_run_at") or "来源未确认"),
         ("（%s）" % _p_tag) if _p_tag else ""))
    A("</div></body></html>")
    return "\n".join(P)


def main() -> int:
    ap = argparse.ArgumentParser(description="后峰盘前简报 → H5")
    ap.add_argument("--date", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--run-json", default=None,
                    help="本次运行的 preopen_run_latest.json；用于读取选票溯源。"
                         "缺省时报告标「来源未确认」，不假装是当天新算的")
    a = ap.parse_args()
    cal = audit.trade_cal()
    tmax, _ = audit.max_date("tdx/ts_daily", "trade_date")
    day = a.date or tmax
    if not day:
        print("无可用的个股日线，终止")
        return 2
    d = collect(day)
    d["provenance"] = read_provenance(a.run_json)
    html_txt = render(d)
    # 文件名用「执行日」：老田早上转出去的是"今天开盘用"的那份
    tag = d.get("exec_day") or day
    out = a.out or os.path.join(DELIV, "preopen_report_%s.html" % tag)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    io.open(out, "w", encoding="utf-8", newline="").write(html_txt)
    print("WROTE %s (%d bytes) data_day=%s exec_day=%s picks=%d"
          % (out, len(html_txt), day, tag, len(d.get("picks") or [])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
