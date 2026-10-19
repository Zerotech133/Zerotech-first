# -*- coding: utf-8 -*-
"""K-STOCK-293：bt2_view.html 当日详情重构——持仓池/候选池两表 + 评分/名称/量比/背驰可见。"""
P = r"C:/项目/老田的AI助手/portal/bt2_view.html"
s = open(P, encoding="utf-8").read()

old = '''function showDay(d){
  const dd=DATA.days[d],hs=DATA.holdings[d]||[];
  let h=`<div><b>${d}</b> · 当日 <span class="${dd.pnl_pct>0?"up":"dn"}">${dd.pnl_pct>0?"+":""}${dd.pnl_pct}%</span> · 持仓 ${dd.n_pos} 只 · 现金 ${dd.cash_pct}% · regime=${dd.regime}（帽 ${Math.round(dd.cap*100)}%）</div>`;
  h+=(dd.actions||[]).map(a=>`<div class="act"><span class="ty" style="color:${TY_COLOR[a.type]||"var(--tx)"}">[${TYPE_CN[a.type]||a.type}]</span>${a.stock?a.stock+" ":""}${a.note}${a.pnl_pct!=null?` <span class="${a.pnl_pct>0?"up":"dn"}">(${a.pnl_pct>0?"+":""}${a.pnl_pct}%)</span>`:""}</div>`).join("")||'<div class="act note">无事件</div>';
  h+=`<div style="margin-top:6px" class="note">持仓（盈亏%/权重%）：`+hs.slice(0,24).map(x=>`<span class="${x.pnl_pct>0?"up":"dn"}" style="margin-right:8px;font-size:11px">${x.stock} ${x.pnl_pct>0?"+":""}${x.pnl_pct}%/${x.wgt_pct}%</span>`).join("")+(hs.length>24?` …共${hs.length}只`:"")+"</div>";
  document.getElementById("day-detail").innerHTML=h;
}'''

new = '''const NM=s=>(DATA.meta.names||{})[s]||"";
const SC=x=>x==null?"-":x;
const scoreColor=v=>v==null?"var(--note)":(v>=70?"#e5534b":(v>=55?"#d29922":"var(--note)"));
function showDay(d){
  const dd=DATA.days[d],hs=(DATA.holdings[d]||[]).slice().sort((a,b)=>(b.score??-1)-(a.score??-1));
  const pt=(dd.pool_today||[]).slice().sort((a,b)=>(b.score??-1)-(a.score??-1));
  let h=`<div><b>${d}</b> · 当日 <span class="${dd.pnl_pct>0?"up":"dn"}">${dd.pnl_pct>0?"+":""}${dd.pnl_pct}%</span> · 持仓 ${dd.n_pos} 只 · 现金 ${dd.cash_pct}% · regime=${dd.regime}（帽 ${Math.round(dd.cap*100)}%） · 当日新信号 ${pt.length} 只</div>`;
  // ① 持仓池（评分降序）
  h+=`<table class="ptab"><tr><th>持仓池 ${hs.length} 只</th><th>名称</th><th>评分</th><th>盈亏%</th><th>权重%</th></tr>`+
    hs.map(x=>`<tr><td>${x.stock}</td><td class="note">${NM(x.stock)}</td><td style="color:${scoreColor(x.score)}">${SC(x.score)}</td><td class="${x.pnl_pct>0?"up":"dn"}">${x.pnl_pct>0?"+":""}${x.pnl_pct}</td><td>${x.wgt_pct}</td></tr>`).join("")+
    (hs.length?"":"<tr><td colspan=5 class=note>空仓</td></tr>")+"</table>";
  // ② 当日候选池（三买信号·评分降序）
  h+=`<table class="ptab"><tr><th>当日候选（三买信号 ${pt.length} 只·评分降序·次日开盘入场）</th><th>名称</th><th>评分</th><th>量比</th><th>背驰</th></tr>`+
    pt.map(x=>`<tr><td>${x.stock}</td><td class="note">${NM(x.stock)}</td><td style="color:${scoreColor(x.score)}">${SC(x.score)}</td><td>${x.vr==null?"-":x.vr.toFixed(2)}</td><td>${x.bei?"盘整背驰":x.bei2?"力度背驰":"-"}</td></tr>`).join("")+
    (pt.length?"":"<tr><td colspan=5 class=note>当日无新三买</td></tr>")+"</table>";
  // ③ 当日决策流水
  h+=(dd.actions||[]).map(a=>`<div class="act"><span class="ty" style="color:${TY_COLOR[a.type]||"var(--tx)"}">[${TYPE_CN[a.type]||a.type}]</span>${a.stock?a.stock+" "+NM(a.stock)+" ":""}${a.note}${a.pnl_pct!=null?` <span class="${a.pnl_pct>0?"up":"dn"}">(${a.pnl_pct>0?"+":""}${a.pnl_pct}%)</span>`:""}</div>`).join("")||'<div class="act note">无决策事件</div>';
  document.getElementById("day-detail").innerHTML=h;
}'''

assert s.count(old) == 1, "showDay anchor not found"
s = s.replace(old, new)

# 表格样式（暗色 tokens 对齐）
anchor = "/* ==== 293 候选/持仓表样式 ==== */"
if anchor not in s:
    css = anchor + '''
.ptab{width:100%;border-collapse:collapse;margin:8px 0 4px;font-size:11.5px}
.ptab th{color:var(--note);text-align:left;font-weight:500;padding:3px 8px;border-bottom:1px solid var(--bd,#2a2f3a)}
.ptab td{padding:3px 8px;border-bottom:1px solid rgba(255,255,255,.04)}
'''
    # 插入到第一个 </style> 前
    i = s.find("</style>")
    assert i > 0
    s = s[:i] + css + s[i:]

open(P, "w", encoding="utf-8").write(s)
print("bt2_view.html showDay rewritten OK")
