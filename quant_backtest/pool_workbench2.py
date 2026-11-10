#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""pool_workbench2.py —— 选股工作台 v2（三级：档卡片 → 档列表 → 个股详情）

老田 2026-11-10 需求：pool_pc 的摆布看不出意图，改成
  1) 每档一张卡：该档总只数、其中沪深300成分几只、市值≥300亿几只、头部前几只；
  2) 点档 → 全列表（可排序、可搜索）；
  3) 点个股 → 详情抽屉：能收集到的所有关系——
     基础行情 / 缠论结构全字段 / 申万行业 / 同花顺概念板块 / 沪深300成分与权重 /
     陆股通 / 东财人气榜 / 龙虎榜 / RPS 强度榜 / 一年信号史 / 影子台账持仓 / 30日走势。

数据源（全部本地）：
  reports/pool_state_<asof>.json          档与个股记录
  data/tdx/ts_index_weight               沪深300成分+权重（000300.SH 最新一期）
  data/tdx/ts_index_member_all           申万 L1/L2/L3 行业
  data/tdx/ts_ths_member + ts_ths_index  同花顺概念板块归属（700xxx.TI → 名称）
  data/tdx/ts_hs_const                   陆股通成分（is_new=1）
  data/tdx/ts_dc_hot                     东财人气榜（最新一期 A股市场）
  data/tdx/ts_top_list                   龙虎榜（最新一期）
  reports/rps_latest.json                RPS 强度榜（tops.CN）
  data/sig_events/sig_events_market_v3_<d0>_<d1>.json  一年信号史
  data/shadow/<asof>.json                影子台账六腿持仓
  data/ashare/<code_file>.parquet        30 日收盘 sparkline

产出：reports/pool_pc2.html（单文件，静态，无外部依赖）
用法：python pool_workbench2.py [--asof 20261110]
"""
import argparse
import glob
import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, "reports")
DATA = os.path.join(HERE, "data")
TDX = os.path.join(DATA, "tdx")

TIERS = [
    ("三买", "趋势确认：中枢上沿回踩不破，核心买档"),
    ("二买", "左侧转右侧：一买后回调不破低点"),
    ("一买", "左侧抄底：下跌末端首次背驰企稳"),
    ("三买待", "接近三买条件，待触发确认"),
    ("中枢", "震荡区间内，位置观察"),
    ("离开段", "正在离开中枢，方向待定"),
    ("中枢下", "中枢下方弱势，等企稳信号"),
    ("背驰", "动能衰竭预警：涨多减速"),
    ("三卖", "中枢下破，规避档"),
]


def _parts(d):
    return sorted(glob.glob(os.path.join(TDX, d, "part_*.parquet")))


def load_pool(asof):
    f = os.path.join(REPORTS, "pool_state_%s.json" % asof)
    d = json.load(open(f, encoding="utf-8"))
    assert d.get("data_asof") == asof, "pool_state data_asof=%s ≠ %s" % (
        d.get("data_asof"), asof)
    return d["buckets"]


def load_hs300():
    """沪深300：000300.SH 最新一期成分 + 权重。"""
    fs = _parts("ts_index_weight")
    if not fs:
        return {}, ""
    df = pd.concat([pd.read_parquet(f, columns=["index_code", "con_code",
                                                "trade_date", "weight"])
                    for f in fs], ignore_index=True)
    df = df[df["index_code"] == "000300.SH"]
    if df.empty:
        return {}, ""
    last_day = str(df["trade_date"].max())
    df = df[df["trade_date"].astype(str) == last_day]
    return {r.con_code: round(float(r.weight), 3) for r in df.itertuples()}, last_day


def load_sw_industry():
    """申万 L1/L2/L3 → code: [l1, l2, l3]。"""
    fs = _parts("ts_index_member_all")
    if not fs:
        return {}
    df = pd.read_parquet(fs[-1], columns=["l1_name", "l2_name", "l3_name",
                                          "ts_code"])
    df = df[df["out_date"].isna()] if "out_date" in df.columns else df
    m = {}
    for r in df.itertuples():
        cur = m.get(r.ts_code)
        trip = [r.l1_name, r.l2_name, r.l3_name]
        if cur is None or (trip[2] and not cur[2]):
            m[r.ts_code] = trip
    return m


def load_ths_boards(pool_codes):
    """同花顺概念板块：700xxx.TI → 名称（ts_ths_index），成分（ts_ths_member）。
    只反查池内个股，控制体积。"""
    idx_fs = _parts("ts_ths_index")
    names = {}
    if idx_fs:
        dfi = pd.concat([pd.read_parquet(f, columns=["ts_code", "name"])
                         for f in idx_fs], ignore_index=True)
        names = dict(zip(dfi["ts_code"], dfi["name"]))
    mem_fs = _parts("ts_ths_member")
    pool = set(pool_codes)
    out = {}
    if mem_fs:
        for f in mem_fs:
            df = pd.read_parquet(f, columns=["ts_code", "con_code"])
            df = df[df["con_code"].isin(pool)]
            for r in df.itertuples():
                nm = names.get(r.ts_code, r.ts_code)
                out.setdefault(r.con_code, set()).add(nm)
    return {k: sorted(v) for k, v in out.items()}


def load_hsgt():
    fs = _parts("hs_const") or _parts("ts_hs_const")
    if not fs:
        return set()
    df = pd.concat([pd.read_parquet(f, columns=["ts_code", "is_new"])
                    for f in fs], ignore_index=True)
    df = df[df["is_new"] == 1]
    return set(df["ts_code"])


def load_hot():
    fs = _parts("ts_dc_hot")
    if not fs:
        return {}, ""
    df = pd.concat([pd.read_parquet(f, columns=["trade_date", "data_type",
                                                "ts_code", "rank"])
                    for f in fs], ignore_index=True)
    df = df[(df["data_type"] == "A股市场")]
    if df.empty:
        return {}, ""
    last_day = str(df["trade_date"].max())
    df = df[df["trade_date"].astype(str) == last_day]
    df = df.sort_values("rank")
    return {r.ts_code: int(r.rank) for r in df.itertuples()}, last_day


def load_top_list():
    fs = _parts("ts_top_list")
    if not fs:
        return {}, ""
    df = pd.read_parquet(fs[-1], columns=["ts_code", "trade_date", "name",
                                          "net_amount", "reason"])
    out = {}
    for r in df.itertuples():
        out.setdefault(r.ts_code, []).append(
            {"day": str(r.trade_date), "net_wan": round(float(r.net_amount) / 1e4),
             "reason": r.reason})
    return out, str(df["trade_date"].max()) if len(df) else ""


def load_rps():
    f = os.path.join(REPORTS, "rps_latest.json")
    if not os.path.exists(f):
        return {}
    d = json.load(open(f, encoding="utf-8"))
    out = {}
    for r in (d.get("tops") or {}).get("CN", []):
        out[r["sym"]] = {"r50": r.get("r50"), "r120": r.get("r120"),
                         "r250": r.get("r250"), "mix": r.get("mix")}
    return out


def load_events(d0, d1, pool_codes):
    """一年信号史：池内个股 → {type 计数, 最近 8 条}。"""
    f = os.path.join(DATA, "sig_events",
                     "sig_events_market_v3_%s_%s.json" % (d0, d1))
    if not os.path.exists(f):
        return {}, ""
    d = json.load(open(f, encoding="utf-8"))
    pool = set(pool_codes)
    cnt, recent = {}, {}
    for e in d.get("events", []):
        s = e.get("stock")
        if s not in pool:
            continue
        c = cnt.setdefault(s, {})
        c[e["type"]] = c.get(e["type"], 0) + 1
        sc = e.get("score")
        rl = recent.setdefault(s, [])
        if len(rl) < 8:
            rl.append([str(e.get("day")), e["type"],
                       round(float(sc), 1) if sc is not None else None])
    for s in recent:
        recent[s].sort(key=lambda x: -int(x[0]))
    return {"cnt": cnt, "recent": recent}, "%s~%s" % (d0, d1)


def load_shadow(asof):
    f = os.path.join(DATA, "shadow", "%s.json" % asof)
    if not os.path.exists(f):
        return {}
    d = json.load(open(f, encoding="utf-8"))
    legs = d.get("legs", d)
    out = {}
    for lname, lv in legs.items():
        if not isinstance(lv, dict):
            continue
        for p in lv.get("holdings") or []:
            s = p.get("stock")
            if s:
                out.setdefault(s, []).append(
                    {"leg": lname, "wgt": p.get("wgt_pct"), "pnl": p.get("pnl_pct")})
    return out


def load_spark(codes, n=30):
    """30 日收盘序列（复权口径用原始 close，仅走势参考）。"""
    out = {}
    for s in codes:
        f = os.path.join(DATA, "ashare", "%s_%s.parquet" % (
            s.split(".")[0], s.split(".")[1]))
        if not os.path.exists(f):
            continue
        try:
            df = pd.read_parquet(f, columns=["trade_date", "close"])
        except Exception:
            continue
        tail = df.sort_values("trade_date").tail(n)
        out[s] = [round(float(v), 2) for v in tail["close"]]
    return out


def compact(rec):
    """池记录 → 紧凑字典（详情页需要的全字段）。"""
    return {
        "code": rec["code"], "name": rec["name"],
        "close": round(float(rec["close"]), 2), "pct": round(float(rec["pct"]) * 100, 2),
        "mv": round(float(rec["mv_yi"]), 1), "amt": round(float(rec.get("amt20_yi") or 0), 2),
        "bucket": rec.get("bucket"), "stage": rec.get("stage"), "pos": rec.get("pos"),
        "zg": rec.get("zg"), "zd": rec.get("zd"),
        "dist_zg": round(rec["dist_zg"] * 100, 1) if rec.get("dist_zg") is not None else None,
        "trend": rec.get("trend"), "bi_dir": rec.get("bi_dir"),
        "n_piv": rec.get("n_piv"), "piv_solid": rec.get("piv_solid"),
        "piv_wide": rec.get("piv_wide"),
        "sig_type": rec.get("sig_type"), "sig_dt": rec.get("sig_dt"),
        "sig_price": rec.get("sig_price"), "sig_age": rec.get("sig_age"),
        "sig_fresh": rec.get("sig_fresh"),
        "sig_gain": round(rec["sig_gain"] * 100, 1) if rec.get("sig_gain") is not None else None,
        "sig_note": rec.get("sig_note"), "bei": rec.get("bei"),
        "score": round(float(rec.get("score") or 0), 2),
    }


def build_html(asof, payload):
    js = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>选股工作台 v2 · %s</title>
<style>
:root{--bg:#f5f6f8;--card:#fff;--line:#e3e6ea;--tx:#1c2330;--tx2:#68738a;
--buy:#c0392b;--sell:#1e8e4e;--acc:#2456c8;--chip:#eef2f8}
*{box-sizing:border-box;margin:0;padding:0}
body{font:14px/1.55 "Microsoft YaHei",system-ui,sans-serif;background:var(--bg);color:var(--tx)}
header{background:var(--card);border-bottom:1px solid var(--line);padding:14px 22px;display:flex;align-items:baseline;gap:18px;flex-wrap:wrap}
header h1{font-size:20px}header .sub{color:var(--tx2);font-size:13px}
.gbar{display:flex;gap:10px;margin-left:auto;flex-wrap:wrap}
.gbar b{background:var(--chip);border-radius:6px;padding:4px 10px;font-size:13px;font-weight:600}
main{max-width:1280px;margin:18px auto;padding:0 16px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.tcard{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;cursor:pointer;transition:box-shadow .15s}
.tcard:hover{box-shadow:0 3px 12px rgba(30,50,90,.12)}
.tcard h3{font-size:16px;display:flex;align-items:center;gap:8px}
.tcard h3 .n{font-size:22px;color:var(--acc)}
.tcard .desc{color:var(--tx2);font-size:12.5px;margin:2px 0 8px}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
.chips span{background:var(--chip);color:var(--tx);font-size:12px;border-radius:5px;padding:2px 8px}
.top5{display:flex;flex-direction:column;gap:4px}
.top5 .s{display:flex;justify-content:space-between;gap:8px;padding:4px 8px;border-radius:6px;font-size:13px}
.top5 .s:hover{background:var(--chip)}
.top5 .s b{font-weight:600}.top5 .s i{font-style:normal;color:var(--tx2);font-size:12px}
.more{color:var(--acc);font-size:12.5px;margin-top:8px;display:inline-block}
/* 列表层 */
#listView{display:none}
.lv-head{display:flex;align-items:center;gap:14px;margin:6px 0 12px;flex-wrap:wrap}
.lv-head h2{font-size:18px}.lv-head .desc{color:var(--tx2);font-size:13px}
.lv-head input{padding:6px 10px;border:1px solid var(--line);border-radius:6px;width:220px;font-size:13px}
.lv-head select{padding:6px;border:1px solid var(--line);border-radius:6px}
.lv-head button{padding:6px 14px;border:1px solid var(--line);background:var(--card);border-radius:6px;cursor:pointer}
table{width:100%%;border-collapse:collapse;background:var(--card);border-radius:10px;overflow:hidden;border:1px solid var(--line)}
th{background:#eef1f5;text-align:left;padding:8px 10px;font-size:12.5px;color:var(--tx2);cursor:pointer;white-space:nowrap}
td{padding:7px 10px;border-top:1px solid var(--line);font-size:13px;white-space:nowrap}
tr.r:hover{background:#f2f6fd;cursor:pointer}
.up{color:var(--buy)}.dn{color:var(--sell)}
.tag{display:inline-block;border-radius:4px;padding:1px 6px;font-size:11.5px;background:var(--chip)}
.tag.hs{background:#fdf1e2;color:#a05a00}.tag.gp{background:#e8f3ec;color:#1e6e3d}
/* 详情层 */
#detail{position:fixed;top:0;right:-560px;width:560px;height:100vh;background:var(--card);border-left:1px solid var(--line);box-shadow:-6px 0 24px rgba(20,30,60,.18);transition:right .2s;overflow-y:auto;z-index:50;padding:18px 22px}
#detail.open{right:0}
#detail h2{font-size:19px;display:flex;gap:10px;align-items:baseline}
#detail h2 .pct{font-size:16px}
#detail .close{position:absolute;top:14px;right:18px;font-size:20px;cursor:pointer;color:var(--tx2)}
.sec{margin-top:14px;border-top:1px dashed var(--line);padding-top:10px}
.sec h4{font-size:13px;color:var(--acc);margin-bottom:6px}
.kv{display:grid;grid-template-columns:repeat(auto-fill,minmax(165px,1fr));gap:4px 12px;font-size:13px}
.kv div span{color:var(--tx2);margin-right:6px}
.evs{font-size:12.5px;color:var(--tx2);margin-top:4px}
.badges{display:flex;gap:6px;flex-wrap:wrap}
.badges .b{border-radius:5px;padding:2px 8px;font-size:12px;background:var(--chip)}
.badges .b.hs{background:#fdf1e2;color:#a05a00}.badges .b.gp{background:#e8f3ec;color:#1e6e3d}
.badges .b.hot{background:#fdeaea;color:#b03030}
canvas{background:#fafbfc;border:1px solid var(--line);border-radius:6px}
.back{font-size:13px;color:var(--acc);cursor:pointer}
#mask{position:fixed;inset:0;background:rgba(0,0,0,.25);display:none;z-index:40}
</style></head><body>
<header><h1>选股工作台 v2</h1><span class="sub" id="hdSub"></span>
<div class="gbar" id="gbar"></div></header>
<main>
  <div id="cardView"><div class="grid" id="cards"></div></div>
  <div id="listView">
    <div class="lv-head"><span class="back" onclick="showCards()">← 返回档卡片</span>
      <h2 id="lvTitle"></h2><span class="desc" id="lvDesc"></span>
      <input id="lvSearch" placeholder="搜索代码/名称" oninput="renderList()">
      <select id="lvSort" onchange="renderList()">
        <option value="score">按评分</option><option value="mv">按市值</option>
        <option value="pct">按涨幅</option><option value="amt">按20日成交额</option>
      </select>
    </div>
    <div id="lvTable"></div>
  </div>
</main>
<div id="mask" onclick="closeDetail()"></div>
<div id="detail"><span class="close" onclick="closeDetail()">✕</span><div id="dBody"></div></div>
<script id="DATA" type="application/json">%s</script>
<script>
const D = JSON.parse(document.getElementById("DATA").textContent);
const TIER_DESC = %s;
const IDX = {};
D.tiers.forEach(t => t.stocks.forEach(s => { IDX[s.code] = s; s._tier = t.name; }));
const fmt = v => v==null ? "—" : v;
const pctCls = v => v>0 ? "up" : (v<0 ? "dn" : "");
const pctS = v => v==null ? "—" : (v>0?"+":"") + v + "%%";
document.getElementById("hdSub").textContent =
  "数据日 " + D.asof + " · 生成 " + D.gen_at + " · 全池 " + D.total + " 只 · 沪深300成分权重日 " + D.hs_day;
document.getElementById("gbar").innerHTML =
  D.tiers.map(t => "<b>" + t.name + " " + t.stocks.length + "</b>").join("") +
  "<b>沪深300成分在池 " + D.n_hs + "</b>";

/* ── 层1：档卡片 ── */
function showCards(){document.getElementById("cardView").style.display="";
  document.getElementById("listView").style.display="none";window.scrollTo(0,0);}
function renderCards(){
  document.getElementById("cards").innerHTML = D.tiers.map((t,i)=>{
    const buy = ["三买","二买","一买","三买待"].includes(t.name);
    const top = t.stocks.slice().sort((a,b)=>b.score-a.score).slice(0,5);
    return '<div class="tcard" onclick="openList('+i+')">' +
      "<h3>" + t.name + ' <span class="n">' + t.stocks.length + "</span> 只</h3>" +
      '<div class="desc">' + TIER_DESC[t.name] + "</div>" +
      '<div class="chips">' +
        '<span>沪深300成分 ' + t.n_hs + "</span>" +
        "<span>市值≥300亿 " + t.n_mv300 + "</span>" +
        "<span>市值100~300亿 " + t.n_mv100 + "</span>" +
        "<span>人气榜TOP100 " + t.n_hot + "</span>" +
        (t.n_lhb ? "<span>龙虎榜 " + t.n_lhb + "</span>" : "") + "</div>" +
      '<div class="top5">' + top.map(s =>
        '<div class="s" onclick="event.stopPropagation();openDetail(\\'' + s.code + "\\')\">" +
        "<b>" + s.name + ' <span style="color:var(--tx2);font-size:11px">' + s.code + "</span></b>" +
        "<i>" + s.sig_type + " · 分 " + s.score + " · " + pctS(s.pct) + "</i></div>").join("") +
      "</div>" +
      '<span class="more">查看全部 ' + t.stocks.length + " 只 →</span></div>";
  }).join("");
}

/* ── 层2：档列表 ── */
let curTier = -1;
function openList(i){curTier=i;
  const t=D.tiers[i];
  document.getElementById("lvTitle").textContent = t.name + "（" + t.stocks.length + " 只）";
  document.getElementById("lvDesc").textContent = TIER_DESC[t.name];
  document.getElementById("lvSearch").value="";
  document.getElementById("cardView").style.display="none";
  document.getElementById("listView").style.display="";
  renderList();window.scrollTo(0,0);}
function renderList(){
  const t=D.tiers[curTier]; if(!t)return;
  const q=document.getElementById("lvSearch").value.trim().toLowerCase();
  const key=document.getElementById("lvSort").value;
  let rows=t.stocks.filter(s=>!q || s.code.toLowerCase().includes(q) || s.name.includes(q));
  rows=rows.slice().sort((a,b)=>(b[key]||-1e9)-(a[key]||-1e9));
  const th=[["name","名称"],["code","代码"],["sig_type","信号"],["score","评分"],["close","收盘"],["pct","涨幅%%"],["mv","市值(亿)"],["amt","20日额(亿)"],["hs","沪深300"],["hot","人气榜"]];
  document.getElementById("lvTable").innerHTML = "<table><tr>" +
    th.map(h=>"<th>"+h[1]+"</th>").join("")+"</tr>" +
    rows.map(s=>'<tr class="r" onclick="openDetail(\\'' + s.code + "\\')\">" +
      "<td><b>"+s.name+"</b></td><td>"+s.code+"</td><td>"+s.sig_type+"</td>" +
      "<td>"+s.score+"</td><td>"+fmt(s.close)+"</td>" +
      '<td class="'+pctCls(s.pct)+'">'+pctS(s.pct)+"</td>" +
      "<td>"+fmt(s.mv)+"</td><td>"+fmt(s.amt)+"</td>" +
      "<td>"+(s.hs?"✓ "+s.hs+"%%":"")+"</td>" +
      "<td>"+(s.hot?"#"+s.hot:"")+"</td></tr>").join("") + "</table>";
}

/* ── 层3：个股详情 ── */
function openDetail(code){
  const s=IDX[code], rel=D.detail[code]; if(!s||!rel)return;
  s.tier = s._tier;
  const kv=(arr)=>'<div class="kv">'+arr.map(x=>"<div><span>"+x[0]+"</span>"+x[1]+"</div>").join("")+"</div>";
  let h = "<h2>"+s.name+' <span style="color:var(--tx2);font-size:13px">'+s.code+"</span>" +
    ' <span class="pct '+pctCls(s.pct)+'">'+pctS(s.pct)+"</span></h2>" +
    "<div class='badges' style='margin-top:6px'>" +
      "<span class='b'>"+s.tier+"</span>" +
      (s.hs?"<span class='b hs'>沪深300 · 权重 "+s.hs+"%%</span>":"") +
      (s.hsgt?"<span class='b gp'>陆股通标的</span>":"") +
      (s.hot?"<span class='b hot'>东财人气榜 #"+s.hot+"</span>":"") + "</div>";
  h += '<div class="sec"><h4>基础行情</h4>' + kv([
    ["收盘", s.close], ["20日成交额", fmt(s.amt)+" 亿"],
    ["总市值", fmt(s.mv)+" 亿"], ["市值档", fmt(s.bucket)],
    ["信号类型", fmt(s.sig_type)], ["评分", s.score]]) + "</div>";
  h += '<div class="sec"><h4>缠论结构</h4>' + kv([
    ["阶段", fmt(s.stage)+" · "+fmt(s.pos)],
    ["中枢上沿 zg", fmt(s.zg)], ["中枢下沿 zd", fmt(s.zd)],
    ["距上沿", s.dist_zg==null?"—":s.dist_zg+"%%"],
    ["趋势", fmt(s.trend)], ["当前笔方向", s.bi_dir==1?"向上":(s.bi_dir==-1?"向下":"—")],
    ["历史中枢数", fmt(s.n_piv)], ["中枢实心/扩宽", (s.piv_solid?"实心":"空心")+(s.piv_wide?" / 扩宽":"")],
    ["信号日期", fmt(s.sig_dt)], ["信号价", fmt(s.sig_price)],
    ["信号年龄", s.sig_age==null?"—":s.sig_age+" 个交易日"],
    ["新鲜度", s.sig_fresh?"新鲜":"已老化"],
    ["信号至今涨幅", s.sig_gain==null?"—":s.sig_gain+"%%"],
    ["信号备注", fmt(s.sig_note)], ["背驰", fmt(s.bei)]]) + "</div>";
  h += '<div class="sec"><h4>30 日走势</h4><canvas id="spk" width="500" height="90"></canvas></div>';
  h += '<div class="sec"><h4>行业与板块</h4>';
  h += "<div style='font-size:13px;margin-bottom:6px'>申万："+
    (rel.sw?rel.sw.join(" / "):"—")+"</div>";
  h += "<div class='badges'>" + (rel.boards.length ?
    rel.boards.map(b=>"<span class='b'>"+b+"</span>").join("") : "<span class='b'>无同花顺概念记录</span>") + "</div></div>";
  h += '<div class="sec"><h4>强度与热度</h4>';
  h += rel.rps ? kv([["RPS 50日", rel.rps.r50],["RPS 120日",rel.rps.r120],
      ["RPS 250日", rel.rps.r250],["RPS 综合",rel.rps.mix]]) :
    "<div class='evs'>未进 RPS 强度榜（tops.CN）</div>";
  h += "</div>";
  h += '<div class="sec"><h4>龙虎榜</h4>' + (rel.lhb.length ?
    rel.lhb.map(x=>"<div class='evs'>"+x.day+" · 净买 "+x.net_wan+" 万 · "+x.reason+"</div>").join("") :
    "<div class='evs'>近期无龙虎榜记录</div>") + "</div>";
  h += '<div class="sec"><h4>一年信号史（'+rel.ev_span+'）</h4>' + kv(
    Object.entries(rel.ev_cnt).map(([k,v])=>[k, v+" 次"])) +
    (rel.ev_recent.length ? '<div class="evs">最近：'+rel.ev_recent.map(e=>e[0].slice(4,6)+"/"+e[0].slice(6)+" "+e[1]).join(" · ")+"</div>" : "") + "</div>";
  h += '<div class="sec"><h4>影子台账持仓</h4>' + (rel.shadow.length ?
    rel.shadow.map(x=>"<div class='evs'>"+x.leg+" · 仓位 "+x.wgt+"%% · 浮盈 "+x.pnl+"%%</div>").join("") :
    "<div class='evs'>六腿均未持有</div>") + "</div>";
  document.getElementById("dBody").innerHTML=h;
  document.getElementById("detail").classList.add("open");
  document.getElementById("mask").style.display="block";
  drawSpark(rel.spark);
}
function drawSpark(arr){
  const c=document.getElementById("spk"); if(!c||!arr||arr.length<2)return;
  const ctx=c.getContext("2d"),W=c.width,H=c.height,pad=6;
  const mn=Math.min(...arr),mx=Math.max(...arr),sp=(mx-mn)||1;
  const up=arr[arr.length-1]>=arr[0];
  ctx.clearRect(0,0,W,H);
  ctx.beginPath();
  arr.forEach((v,i)=>{const x=pad+i*(W-2*pad)/(arr.length-1),
    y=H-pad-(v-mn)/sp*(H-2*pad); i?ctx.lineTo(x,y):ctx.moveTo(x,y);});
  ctx.strokeStyle=up?"#c0392b":"#1e8e4e";ctx.lineWidth=1.6;ctx.stroke();
  ctx.font="11px sans-serif";ctx.fillStyle="#68738a";
  ctx.fillText("30日前 "+arr[0],pad,12);
  ctx.fillText("今 "+arr[arr.length-1],W-70,12);
}
function closeDetail(){document.getElementById("detail").classList.remove("open");
  document.getElementById("mask").style.display="none";}
renderCards();
</script></body></html>""" % (asof, js,
        json.dumps({k: v for k, v in TIERS}, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default=None, help="数据日，默认取最新 pool_state")
    a = ap.parse_args()
    if not a.asof:
        fs = sorted(glob.glob(os.path.join(REPORTS, "pool_state_*.json")))
        a.asof = os.path.basename(fs[-1])[11:19]
    asof = a.asof
    buckets = load_pool(asof)
    tiers, pool_codes = [], []
    for name, _ in TIERS:
        for r in buckets.get(name, []):
            pool_codes.append(r["code"])
    hs300, hs_day = load_hs300()
    sw = load_sw_industry()
    boards = load_ths_boards(pool_codes)
    hsgt = load_hsgt()
    hot, hot_day = load_hot()
    lhb, lhb_day = load_top_list()
    rps = load_rps()
    d0 = str(int(asof) - 10000)
    evs, ev_span = load_events(d0, asof, pool_codes)
    shadow = load_shadow(asof)
    spark = load_spark(pool_codes)

    tiers, detail, n_hs = [], {}, 0
    for name, _ in TIERS:
        recs = [compact(r) for r in buckets.get(name, [])]
        for s in recs:
            s["hs"] = hs300.get(s["code"])
            s["hot"] = hot.get(s["code"])
            s["lhb"] = s["code"] in lhb
            code = s["code"]
            detail[code] = {
                "sw": sw.get(code),
                "boards": boards.get(code, [])[:15],
                "rps": rps.get(code),
                "lhb": lhb.get(code, []),
                "ev_cnt": evs["cnt"].get(code, {}),
                "ev_recent": evs["recent"].get(code, []),
                "ev_span": ev_span,
                "shadow": shadow.get(code, []),
                "spark": spark.get(code, []),
            }
            if s["hs"]:
                n_hs += 1
        tiers.append({
            "name": name, "stocks": recs,
            "n_hs": sum(1 for s in recs if s["hs"]),
            "n_mv300": sum(1 for s in recs if (s["mv"] or 0) >= 300),
            "n_mv100": sum(1 for s in recs if 100 <= (s["mv"] or 0) < 300),
            "n_hot": sum(1 for s in recs if s["hot"]),
            "n_lhb": sum(1 for s in recs if s["lhb"]),
        })
    import datetime
    payload = {"asof": asof, "gen_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
               "total": sum(len(t["stocks"]) for t in tiers), "tiers": tiers,
               "detail": detail, "n_hs": n_hs, "hs_day": hs_day,
               "hot_day": hot_day, "lhb_day": lhb_day}
    html = build_html(asof, payload)
    out = os.path.join(REPORTS, "pool_pc2.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print("[ok] %s  %.1f MB  池 %d 只 / 沪深300成分 %d / 板块关系 %d / 事件史 %d / spark %d"
          % (out, os.path.getsize(out) / 1048576, payload["total"], n_hs,
             len(boards), len(evs["cnt"]), len(spark)))


if __name__ == "__main__":
    main()
