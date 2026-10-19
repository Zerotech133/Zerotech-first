# -*- coding: utf-8 -*-
"""K-STOCK-283 · 原型展示页生成器：bt2_prototype.html（静态自包含·暗色·红涨绿跌）"""
import json, os

BASE = r"C:/项目/老田的AI助手"
DL = os.path.join(BASE, "quant_backtest", "data", "daily_ledger")
OUT = os.path.join(DL, "bt2_prototype.html")

A = json.load(open(os.path.join(DL, "bt2_chanlun_wait.json"), encoding="utf-8"))
B = json.load(open(os.path.join(DL, "bt2_double_knife.json"), encoding="utf-8"))

def slim(x):
    return {
        "meta": x["meta"], "summary": x["summary"],
        "equity_curve": x["equity_curve"],
        "days": {d: {"pnl_pct": v["pnl_pct"], "n_pos": v["n_pos"], "cap": v["cap"],
                     "regime": v["regime"], "cash_pct": v["cash_pct"], "actions": v["actions"]}
                 for d, v in x["days"].items()},
        "holdings": x["holdings"], "trades": x["trades"],
    }

DATA = {"chanlun": slim(A), "dknife": slim(B)}
DATA_JS = json.dumps(DATA, ensure_ascii=False, separators=(",", ":"))

HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>283 回测框架 v2 原型 · 缠论等机会 × 量子双刀日内纪律</title>
<style>
:root{--bg:#0d1117;--panel:#161b22;--panel2:#1c2330;--bd:#2d3748;--tx:#e6edf3;--tx2:#8b949e;
--up:#e5534b;--dn:#3fb950;--gold:#d29922;--blue:#58a6ff;--purple:#bc8cff}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font:14px/1.6 "Segoe UI","Microsoft YaHei",sans-serif;padding:20px}
h1{font-size:20px;margin-bottom:4px} h1 .tag{color:var(--gold);font-size:13px;margin-left:8px}
.sub{color:var(--tx2);font-size:12px;margin-bottom:16px}
.panel{background:var(--panel);border:1px solid var(--bd);border-radius:10px;padding:16px;margin-bottom:16px}
.panel h2{font-size:15px;margin-bottom:10px;color:var(--blue)}
.tl-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.tl-card{background:var(--panel2);border:1px solid var(--bd);border-radius:8px;padding:12px}
.tl-card h3{font-size:14px;margin-bottom:8px}
.tl-card.c1 h3{color:var(--purple)} .tl-card.c2 h3{color:var(--gold)}
.tl-step{display:flex;gap:8px;margin-bottom:6px;font-size:12.5px;color:var(--tx2)}
.tl-step b{color:var(--tx);font-weight:600;white-space:nowrap}
.tl-step .no{background:#30363d;border-radius:50%;width:18px;height:18px;text-align:center;line-height:18px;font-size:11px;color:var(--tx);flex:none}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{padding:6px 10px;border-bottom:1px solid var(--bd);text-align:left}
th{color:var(--tx2);font-weight:500}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:14px}
.kpi{background:var(--panel2);border:1px solid var(--bd);border-radius:8px;padding:10px 12px}
.kpi .lb{font-size:11px;color:var(--tx2)} .kpi .v{font-size:18px;font-weight:700;margin-top:2px}
.up{color:var(--up)} .dn{color:var(--dn)} .gld{color:var(--gold)} .blu{color:var(--blue)}
.tabs{display:flex;gap:8px;margin-bottom:10px}
.tab{padding:5px 14px;border-radius:6px;border:1px solid var(--bd);background:var(--panel2);cursor:pointer;font-size:13px;color:var(--tx2)}
.tab.on{color:var(--tx);border-color:var(--gold);background:#2a2210}
#chart svg{width:100%;height:auto;display:block}
.cal{display:grid;grid-template-columns:repeat(7,1fr);gap:3px;max-width:560px}
.cal .cell{aspect-ratio:1;border-radius:4px;background:var(--panel2);font-size:10px;display:flex;align-items:center;justify-content:center;cursor:pointer;border:1px solid transparent;color:var(--tx2)}
.cal .cell:hover{border-color:var(--gold)}
.cal .hd{background:none;border:none;font-size:11px;color:var(--tx2)}
.cal .em{background:none;cursor:default}
.mlabel{font-size:12px;color:var(--gold);margin:10px 0 4px}
.day-detail{margin-top:12px;background:var(--panel2);border:1px solid var(--bd);border-radius:8px;padding:12px;font-size:12.5px;max-height:300px;overflow:auto}
.act{padding:2px 0;border-bottom:1px dashed #21262d}
.act .ty{display:inline-block;min-width:52px;font-weight:600}
.note{font-size:12px;color:var(--tx2);margin-top:6px}
.legend{display:flex;gap:16px;font-size:11px;color:var(--tx2);margin-top:6px}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.small{font-size:11px;color:var(--tx2)}
</style></head><body>
<h1>回测框架 v2 原型 <span class="tag">K-STOCK-283 · 真实数据 · 20251013→20261012 · 242 交易日</span></h1>
<div class="sub">两策略 = 同一引擎两组 BacktestConfig。老田 10-13 口径：缠论≠当天满仓（选出股票后<b>等机会</b>）；量子双刀 14:45 选股 / 14:55 只卖不买（盘中禁买入）/ 买入 T+1 开盘 / 次日早盘可卖。</div>

<div class="panel"><h2>① 执行时间轴（两策略对照）</h2><div class="tl-grid" id="timelines"></div></div>

<div class="panel"><h2>② BacktestConfig · 11 参数对照</h2><div id="cfg-table"></div>
<div class="note">参数全部配置化：改参数即改策略行为，无需改引擎代码。窗内的 trim/refill/skip 事件均逐笔落账。</div></div>

<div class="panel"><h2>③ 回测结果对照（真实数据）</h2>
<div class="kpis" id="kpis"></div>
<div id="chart"></div>
<div class="legend"><span><span class="sw" style="background:var(--purple)"></span>缠论等机会轮动</span>
<span><span class="sw" style="background:var(--gold)"></span>量子双刀日内纪律</span>
<span><span class="sw" style="background:#1f6feb55"></span>regime=high(80%)</span>
<span><span class="sw" style="background:#d2992233"></span>regime=mid(50%)</span>
<span><span class="sw" style="background:#21262d"></span>regime=low(30%)</span></div>
<div class="note" id="kpi-note"></div></div>

<div class="panel"><h2>④ 逐日日历复盘</h2>
<div class="tabs"><div class="tab on" data-k="chanlun">缠论等机会轮动</div><div class="tab" data-k="dknife">量子双刀日内纪律</div></div>
<div id="calendar"></div>
<div class="day-detail" id="day-detail"><span class="small">点击日历格看当日：信号→决策→成交逐笔</span></div>
<div class="note" id="trades-note"></div></div>

<script>
const DATA = __DATA__;
let CUR = "chanlun";
const TYPE_CN = {buy:"买入",sell:"卖出",trim:"减仓",skip:"放弃",note:"信号"};
const TY_COLOR = {buy:"var(--up)",sell:"var(--dn)",trim:"var(--gold)",skip:"var(--tx2)",note:"var(--blue)"};

// ① 时间轴
const TL = DATA.chanlun.meta.execution_timeline, TL2 = DATA.dknife.meta.execution_timeline;
function tlHtml(arr, cls, title){return `<div class="tl-card ${cls}"><h3>${title}</h3>`+arr.map((s,i)=>`<div class="tl-step"><span class="no">${i+1}</span><span>${s}</span></div>`).join("")+"</div>";}
document.getElementById("timelines").innerHTML = tlHtml(TL,"c1","🟣 缠论 · 等机会轮动（不满仓）")+tlHtml(TL2,"c2","🟡 量子双刀 · 日内纪律");

// ② 参数对照
const pa = DATA.chanlun.meta.config, pb = DATA.dknife.meta.config;
const keys = Object.keys(pa).filter(k=>!["name"].includes(k));
const CN = {strategy:"策略",d0:"窗口起点",d1:"窗口终点",buy_timing:"买入时点",sell_timing:"卖出时点",min_holding_days:"最短持有(日)",holding_max_days:"持有上限(日·0不限)",top_n:"持仓槽数/单日上限",slot_pct:"单票目标仓位",cap_mode:"总仓位模式",cap_fixed:"固定仓位",runup_cap:"追高帽(T+1开盘vs T收)",stop_pct:"灾难止损(0关)",rebalance_every:"定期再平衡(日·0关)",cost:"双边成本"};
function fmt(v){return typeof v==="number"&&!Number.isInteger(v)?v:(v===0.15?"15%":v);}
document.getElementById("cfg-table").innerHTML = "<table><tr><th>参数</th><th>🟣 缠论</th><th>🟡 量子双刀</th></tr>"+
 keys.map(k=>`<tr><td>${CN[k]||k}</td><td>${k==="slot_pct"?(pa[k]*100)+"%":k==="stop_pct"?(pa[k]===0?"关闭":pa[k]):pa[k]}</td><td>${k==="slot_pct"?(pb[k]*100)+"%":k==="stop_pct"?(pb[k]===0?"关闭":pb[k]):pb[k]}</td></tr>`).join("")+"</table>";

// ③ KPI
function kpiCards(s,c){const cls=v=>v>0?"up":"dn";return `
 <div class="kpi"><div class="lb">${c} 总收益</div><div class="v ${cls(s.total_return_pct)}">${s.total_return_pct}%</div></div>
 <div class="kpi"><div class="lb">最大回撤</div><div class="v ${s.max_drawdown_pct>-20?"up":"dn"}">${s.max_drawdown_pct}%</div></div>
 <div class="kpi"><div class="lb">Calmar</div><div class="v ${s.calmar>0?"up":"dn"}">${s.calmar??"-"}</div></div>
 <div class="kpi"><div class="lb">成交笔数 / 胜率</div><div class="v">${s.n_trades} / ${s.win_rate_pct??"-"}%</div></div>
 <div class="kpi"><div class="lb">平均仓位</div><div class="v gld">${s.avg_invested_pct}%</div></div>`;}
const sa=DATA.chanlun.summary, sb=DATA.dknife.summary;
document.getElementById("kpis").innerHTML = kpiCards(sa,"🟣缠论")+kpiCards(sb,"🟡双刀");
document.getElementById("kpi-note").innerHTML = `<b>如实声明</b>：缠论臂平均仓位仅 ${sa.avg_invested_pct}%——等机会制天然不满仓（${sa.n_trim} 次 regime 超帽减仓）；双刀臂平均仓位 ${sb.avg_invested_pct}%——池 76 只内双刀形态一年仅 ${DATA.dknife.summary.n_trades} 次出场机会（relaxed 档 33 只次信号），双刀的设计场景是<b>全市场 14:45 扫描</b>，包 C 接全市场数据后才有统计意义。Calmar 为空仓堆高，勿直接横比。`;

// 净值曲线 SVG
(function(){
  const ec=DATA.chanlun.equity_curve, n=ec.length, W=1060, H=260, L=46, R=10, T=10, Bm=22;
  const all=ec.map(e=>e.equity).concat(DATA.dknife.equity_curve.map(e=>e.equity));
  const mn=Math.min(...all)*0.995, mx=Math.max(...all)*1.005;
  const X=i=>L+(W-L-R)*i/(n-1), Y=v=>T+(H-T-Bm)*(1-(v-mn)/(mx-mn));
  let g="";
  // regime 背景
  for(let i=0;i<n;i++){const r=DATA.chanlun.days[ec[i].day].regime;
    const col=r==="high"?"#1f6feb55":r==="mid"?"#d2992233":"#21262d";
    const w=(W-L-R)/(n-1);
    g+=`<rect x="${X(i)-w/2}" y="${T}" width="${w+0.5}" height="${H-T-Bm}" fill="${col}"/>`;}
  const path=arr=>arr.map((e,i)=>`${i?"L":"M"}${X(i).toFixed(1)},${Y(e.equity).toFixed(1)}`).join("");
  let base=Y(1);
  g+=`<line x1="${L}" y1="${base}" x2="${W-R}" y2="${base}" stroke="#8b949e" stroke-dasharray="3,3" stroke-width="1"/>`;
  g+=`<path d="${path(DATA.dknife.equity_curve)}" fill="none" stroke="#d29922" stroke-width="1.6"/>`;
  g+=`<path d="${path(ec)}" fill="none" stroke="#bc8cff" stroke-width="1.8"/>`;
  // y 轴刻度
  for(let k=0;k<=4;k++){const v=mn+(mx-mn)*k/4;
    g+=`<text x="${L-6}" y="${Y(v)+4}" fill="#8b949e" font-size="10" text-anchor="end">${((v-1)*100).toFixed(0)}%</text>`;
    g+=`<line x1="${L}" y1="${Y(v)}" x2="${W-R}" y2="${Y(v)}" stroke="#21262d" stroke-width="0.5"/>`;}
  [0,Math.floor(n/3),Math.floor(2*n/3),n-1].forEach(i=>{
    g+=`<text x="${X(i)}" y="${H-6}" fill="#8b949e" font-size="10" text-anchor="middle">${ec[i].day.slice(4,6)}/${ec[i].day.slice(6,8)}</text>`;});
  document.getElementById("chart").innerHTML=`<svg viewBox="0 0 ${W} ${H}">${g}</svg>`;
})();

// ④ 日历
function monthOf(d){return d.slice(0,6);}
function drawCal(){
  const days=DATA[CUR].days, ks=Object.keys(days).sort();
  const byMonth={}; ks.forEach(d=>{(byMonth[monthOf(d)]=byMonth[monthOf(d)]||[]).push(d);});
  const WD=["一","二","三","四","五","六","日"];
  let h="";
  for(const m of Object.keys(byMonth).sort()){
    h+=`<div class="mlabel">${m.slice(0,4)}年${+m.slice(4)}月</div><div class="cal">`;
    h+=WD.map(w=>`<div class="cell hd">${w}</div>`).join("");
    const first=byMonth[m][0];
    const dt=new Date(+m.slice(0,4),+m.slice(4)-1,+first.slice(6));
    let pad=(dt.getDay()+6)%7;
    for(let i=0;i<pad;i++)h+=`<div class="cell em"></div>`;
    for(const d of byMonth[m]){
      const v=days[d].pnl_pct;
      const bg=v>0.005?`rgba(229,83,75,${Math.min(0.15+v/3,0.9)})`:v<-0.005?`rgba(63,185,80,${Math.min(0.15+Math.abs(v)/3,0.9)})`:"var(--panel2)";
      h+=`<div class="cell" style="background:${bg}" data-d="${d}" title="${d} ${v}%">${+d.slice(6)}</div>`;}
    h+="</div>";}
  document.getElementById("calendar").innerHTML=h;
  document.querySelectorAll("#calendar .cell[data-d]").forEach(c=>c.onclick=()=>showDay(c.dataset.d));
}
function showDay(d){
  const dd=DATA[CUR].days[d], hs=DATA[CUR].holdings[d]||[];
  let h=`<div><b>${d}</b> · 当日 <span class="${dd.pnl_pct>0?"up":"dn"}">${dd.pnl_pct>0?"+":""}${dd.pnl_pct}%</span> · 持仓 ${dd.n_pos} 只 · 现金 ${dd.cash_pct}% · regime=${dd.regime}（帽 ${Math.round(dd.cap*100)}%）</div>`;
  h+=(dd.actions||[]).map(a=>`<div class="act"><span class="ty" style="color:${TY_COLOR[a.type]||"var(--tx)"}">[${TYPE_CN[a.type]||a.type}]</span>${a.stock?a.stock+" ":""}${a.note}${a.pnl_pct!=null?` <span class="${a.pnl_pct>0?"up":"dn"}">(${a.pnl_pct>0?"+":""}${a.pnl_pct}%)</span>`:""}</div>`).join("")||'<div class="act small">无事件</div>';
  h+=`<div style="margin-top:8px" class="small">持仓明细（盈亏% / 权重%）：</div>`;
  h+=hs.slice(0,20).map(x=>`<span style="display:inline-block;margin:2px 6px 2px 0;font-size:11px" class="${x.pnl_pct>0?"up":"dn"}">${x.stock} ${x.pnl_pct>0?"+":""}${x.pnl_pct}% / ${x.wgt_pct}%</span>`).join("");
  if(hs.length>20)h+=`<span class="small">…共 ${hs.length} 只</span>`;
  document.getElementById("day-detail").innerHTML=h;
}
document.querySelectorAll(".tab").forEach(t=>t.onclick=()=>{
  document.querySelectorAll(".tab").forEach(x=>x.classList.remove("on"));
  t.classList.add("on"); CUR=t.dataset.k; drawCal();
  const tr=DATA[CUR].trades;
  document.getElementById("trades-note").innerHTML=`逐笔成交 ${tr.length} 笔 · 最近 8 笔：`+tr.slice(-8).reverse().map(x=>`<span style="margin-right:10px" class="${x.pnl==null?"":x.pnl>0?"up":"dn"}">${x.day.slice(5)} ${x.stock} ${x.side}${x.pnl!=null?" "+(x.pnl>0?"+":"")+x.pnl+"%":""}</span>`).join("");
});
drawCal();
document.querySelector(".tab").click();
</script></body></html>"""

html = HTML.replace("__DATA__", DATA_JS)
open(OUT, "w", encoding="utf-8").write(html)
print("HTML:", OUT, f"({os.path.getsize(OUT)//1024} KB)")
