# -*- coding: utf-8 -*-
"""api_common 四组拆分：assets 组（2026-10-31 WO-COMMON-SPLIT-20261031）。

自 api_common.py 纯移动而来，函数体/常量零改动；对外仍可从
api_common 导入（兼容 re-export）。
"""
from __future__ import annotations

import os

from api_common import (
    QBT_DIR,
)


# ── K255 全局勾选回测 JS（pill_nav 注入，48 页共用）──────
_BTSEL_JS = '''
// ── K255 全局勾选回测：所有含股票链接的表格行自动加勾选框 + 浮动工具条 ──
(function(){
  if(window.__k255bt)return;window.__k255bt=1;
  function ready(f){if(document.readyState!=="loading")f();
    else document.addEventListener("DOMContentLoaded",f);}
  ready(function(){
    var trs=[];
    document.querySelectorAll("tr").forEach(function(tr){
      if(tr.querySelector("a[href*='/bt/stock?c=']")
         &&!tr.querySelector("._k255ck"))trs.push(tr);
    });
    if(!trs.length)return;
    var doneTbl=new Set();
    trs.forEach(function(tr){
      var tb=tr.closest("table");
      if(tb&&!doneTbl.has(tb)){doneTbl.add(tb);
        var hr=tb.querySelector("thead tr");
        if(!hr&&tb.rows.length&&tb.rows[0].cells.length
           &&tb.rows[0].cells[0].tagName==="TH")hr=tb.rows[0];
        if(hr){var th=document.createElement("th");th.textContent="选";
          hr.insertBefore(th,hr.cells[0]||null);}
      }
      if(tr.querySelector("th"))return;
      var td=document.createElement("td");
      var ck=document.createElement("input");
      ck.type="checkbox";ck.className="_k255ck";ck.checked=true;
      td.style.cssText="width:26px;text-align:center";
      td.appendChild(ck);tr.insertBefore(td,tr.cells[0]||null);
    });
    var bar=document.createElement("div");
    bar.style.cssText="position:fixed;right:18px;bottom:18px;z-index:9999;"
      +"display:flex;gap:8px;align-items:center;background:#161b22;"
      +"border:1px solid #30363d;border-radius:12px;padding:9px 12px;"
      +"box-shadow:0 10px 28px rgba(0,0,0,.45);font-size:12.5px;"
      +"color:#e6edf3;flex-wrap:wrap;max-width:92vw";
    var cnt=document.createElement("span");
    cnt.style.cssText="color:#8b949e";
    var sel=document.createElement("select");
    sel.style.cssText="background:#0d1117;color:#e6edf3;"
      +"border:1px solid #30363d;border-radius:8px;padding:4px 6px";
    [["5","持有5日"],["10","持有10日"],["20","持有20日"],
     ["5,10,20","5/10/20 全测"]].forEach(function(o){
      var op=document.createElement("option");
      op.value=o[0];op.textContent=o[1];sel.appendChild(op);});
    var bAll=document.createElement("button");bAll.textContent="全选";
    var bNon=document.createElement("button");bNon.textContent="清空";
    [bAll,bNon].forEach(function(b){
      b.style.cssText="background:#21262d;color:#c9d1d9;"
        +"border:1px solid #30363d;border-radius:8px;padding:4px 10px;"
        +"cursor:pointer";});
    var btn=document.createElement("button");var BTN="▶ 回测选中";
    btn.textContent=BTN;
    btn.style.cssText="background:#238636;color:#fff;border:none;"
      +"border-radius:8px;padding:5px 14px;cursor:pointer;font-weight:600";
    var msg=document.createElement("span");
    msg.style.cssText="color:#e3b341;max-width:300px";
    bar.appendChild(cnt);bar.appendChild(sel);bar.appendChild(bAll);
    bar.appendChild(bNon);bar.appendChild(btn);bar.appendChild(msg);
    document.body.appendChild(bar);
    function picked(){
      var out=[],seen={};
      document.querySelectorAll("._k255ck:checked").forEach(function(ck){
        var tr=ck.closest("tr");if(!tr)return;
        var a=tr.querySelector("a[href*='/bt/stock?c=']");if(!a)return;
        var c=a.getAttribute("href").split("/bt/stock?c=")[1]
              .split("&")[0].split("#")[0];
        if(c&&!seen[c]){seen[c]=1;out.push(c);}
      });
      return out;
    }
    function sync(){
      var n=picked().length;
      cnt.textContent=n?("已选 "+n+" 票"):"未选票";
    }
    document.addEventListener("change",function(ev){
      if(ev.target&&ev.target.classList
         &&ev.target.classList.contains("_k255ck"))sync();});
    bAll.onclick=function(){
      document.querySelectorAll("._k255ck").forEach(function(c){
        c.checked=true;});sync();};
    bNon.onclick=function(){
      document.querySelectorAll("._k255ck").forEach(function(c){
        c.checked=false;});sync();};
    function pc(x){return x==null?"—":(x>=0?"+":"")+(x*100).toFixed(2)+"%";}
    function cc(x){return x==null?"#8b949e":(x>=0?"#f85149":"#3fb950");}
    function show(rep){
      var m=rep.meta||{},b=(rep.batches||[])[0]||{},hs=b.holds||{};
      var note={empty:"⛔ 该日无买入/观察票",
        await_t1:"⏳ 信号日为数据尾，T+1 未开盘",
        no_bar:"该日无行情数据"}[b.note]||"";
      var rows="";
      Object.keys(hs).forEach(function(k){
        var h=hs[k];
        var ex="";
        if(h.n_unfillable||h.n_suspend_exit)
          ex="<div style='color:#8b949e;font-size:10.5px'>一字板 "
             +(h.n_unfillable||0)+" · 停牌 "+(h.n_suspend_exit||0)+"</div>";
        rows+="<tr><td>"+k+"日"+(h.partial
          ?"<span style='color:#8b949e'>(至今)</span>":"")+"</td>"
          +"<td style='color:"+cc(h.mean)+"'><b>"+pc(h.mean)+"</b></td>"
          +"<td style='color:"+cc(h.excess)+"'>"+pc(h.excess)+"</td>"
          +"<td>"+(h.win_rate!=null
            ?(h.win_rate*100).toFixed(1)+"%":"—")+"</td>"
          +"<td style='color:#8b949e'>"+(h.n_traded||0)+"/"
          +((h.n_traded||0)+(h.n_dropped||0))+ex+"</td></tr>";
      });
      var ov=document.createElement("div");
      ov.style.cssText="position:fixed;inset:0;background:rgba(0,0,0,.55);"
        +"z-index:10000;display:flex;align-items:center;"
        +"justify-content:center";
      var card=document.createElement("div");
      card.style.cssText="background:#161b22;border:1px solid #30363d;"
        +"border-radius:14px;padding:18px 22px;max-width:640px;"
        +"max-height:80vh;overflow:auto;color:#e6edf3;font-size:13px;"
        +"box-shadow:0 18px 50px rgba(0,0,0,.6)";
      card.innerHTML="<div style='display:flex;justify-content:space-between;"
        +"align-items:center;gap:16px'><b style='font-size:14.5px'>"
        +"🧪 自定义批次回测 · "+(m.generated_at||"")+"</b>"
        +"<span id='_k255x' style='cursor:pointer;color:#8b949e;"
        +"font-size:16px'>✕</span></div>"
        +"<div style='color:#8b949e;margin:6px 0 10px'>信号日 "
        +(b.date||"—")+" · 勾选 "+(b.n_picks||0)+" 票（买入 "
        +(b.n_buy||0)+"） · K254 口径：后复权+一字板剔除+停牌结算"
        +" · 基准=同日全体可交易等权</div>"
        +(note?"<div style='color:#e3b341;margin-bottom:8px'>"
          +note+"</div>":"")
        +"<table style='border-collapse:collapse;width:100%'><tr>"
        +"<th style='text-align:left;padding:4px 10px;border-bottom:1px "
        +"solid #30363d'>持有</th><th style='text-align:left;padding:4px "
        +"10px;border-bottom:1px solid #30363d'>收益</th><th style="
        +"'text-align:left;padding:4px 10px;border-bottom:1px solid "
        +"#30363d'>超额</th><th style='text-align:left;padding:4px 10px;"
        +"border-bottom:1px solid #30363d'>胜率</th><th style='text-align:"
        +"left;padding:4px 10px;border-bottom:1px solid #30363d'>成交/入选"
        +"</th></tr>"+rows+"</table>"
        +"<div style='color:#8b949e;margin-top:10px;font-size:11.5px'>"
        +"T+1 开盘等权买入 · 成本 0.15% 往返 · 逐日明细见 reports/"
        +"rps_custom_backtest.json</div>";
      ov.appendChild(card);document.body.appendChild(ov);
      document.getElementById("_k255x").onclick=function(){ov.remove();};
      ov.onclick=function(e){if(e.target===ov)ov.remove();};
    }
    btn.onclick=function(){
      var codes=picked();
      if(!codes.length){msg.textContent="未勾选";return;}
      btn.disabled=true;btn.textContent="提交中…";msg.textContent="";
      fetch("/api/batch-backtest",
        {method:"POST",headers:{"Content-Type":"application/json"},
         body:JSON.stringify({codes:codes,hold:sel.value})})
      .then(function(r){return r.json();})
      .then(function(j){
        if(!j.ok){msg.textContent=j.error||"提交失败";
          btn.disabled=false;btn.textContent=BTN;return;}
        var t0=Date.now();
        msg.textContent="已提交 "+j.n_codes+" 票 · 引擎加载行情…";
        var timer=setInterval(function(){
          fetch("/api/batch-backtest")
          .then(function(r){return r.json();})
          .then(function(s){
            if(s.report){clearInterval(timer);btn.disabled=false;
              btn.textContent=BTN;msg.textContent="";show(s.report);}
            else if(s.dead){clearInterval(timer);btn.disabled=false;
              btn.textContent=BTN;
              msg.textContent="回测进程退出，见 quant_backtest/logs/";}
            else if(Date.now()-t0>120000){clearInterval(timer);
              btn.disabled=false;btn.textContent=BTN;
              msg.textContent="超时（>120s），稍后刷新再看";}
            else{msg.textContent="回测中 "
              +Math.round((Date.now()-t0)/1000)+"s（加载行情约20s）";}
          }).catch(function(){});
        },3000);
      })
      .catch(function(){msg.textContent="网络错误";
        btn.disabled=false;btn.textContent=BTN;});
    };
    sync();
  });
})();
'''

_BTSEL_TAG = (
    "<script>" + _BTSEL_JS + "</script>")

_PILL = ("display:inline-block;padding:6px 13px;border:1px solid #30363d;"
         "border-radius:16px;color:#58a6ff;text-decoration:none;"
         "font-size:12.5px;background:#161b22")

_PILL_ON = _PILL + ";color:#e6edf3;border-color:#58a6ff;font-weight:600"

_CARD_FB_FP = os.path.join(QBT_DIR, "data", "labels",
                           "card_feedback.jsonl")

_LABELS_FP = os.path.join(QBT_DIR, "data", "labels",
                          "chanlun_labels.jsonl")

_TABBTN = ("padding:5px 14px;border:1px solid #30363d;border-radius:14px;"
           "background:#161b22;color:#58a6ff;font-size:12.5px;cursor:pointer")

_TAB_JS = ("<script>function _tabSw(t,i,n){for(var j=0;j<n;j++){"
           "document.getElementById(t+'-b'+j).style.background='#161b22';"
           "document.getElementById(t+'-b'+j).style.color='#58a6ff';"
           "document.getElementById(t+'-p'+j).style.display='none';}"
           "document.getElementById(t+'-b'+i).style.background='#58a6ff';"
           "document.getElementById(t+'-b'+i).style.color='#fff';"
           "document.getElementById(t+'-p'+i).style.display='block';}"
           "</script>")

# 穿透预览共享 JS：/bridge 仪① 与数据仓时点表同款（点 🔍 就地展开数据行）
_TP_JS = """
<script>
function tp_toggle(t){
  var row = document.getElementById('tpr-' + t);
  if(!row) return;
  var cell = document.getElementById('tpc-' + t);
  if(row.style.display === 'none'){
    row.style.display = '';
    if(!cell.dataset.loaded){
      cell.innerHTML = '<span style="color:#8b949e;font-size:12px">'
        + '⟳ 穿透读取中…</span>';
      fetch('/api/bridge/table_preview?table=' + t + '&n=8')
        .then(function(r){ return r.json(); })
        .then(function(j){
          cell.dataset.loaded = '1';
          if(j.error){
            cell.innerHTML = '<span style="color:#f85149;font-size:12px">❌ '
              + j.error + '</span>'; return; }
          if(!j.rows || !j.rows.length){
            cell.innerHTML = '<span style="color:#8b949e;font-size:12px">'
              + (j.note || '无样本') + '</span>'; return; }
          var h = '<div style="margin:4px 0;max-width:100%;overflow-x:auto">'
            + '<div style="font-size:11px;color:#8b949e;margin-bottom:3px">'
            + '⇩ 穿透样本 · ' + j.table + ' · 数据停在 '
            + (j.latest || '—') + ' · 最新 ' + j.rows.length + ' 行</div>'
            + '<table style="border-collapse:collapse;font-size:11px;'
            + 'font-family:Consolas,monospace;white-space:nowrap">'
            + '<tr>' + j.cols.map(function(c){
                return '<th style="padding:3px 10px;background:#21262d;'
                  + 'color:#8b949e;text-align:left;font-weight:600">' + c
                  + '</th>'; }).join('') + '</tr>';
          j.rows.forEach(function(r){
            h += '<tr>' + j.cols.map(function(c){
              var v = r[c]; if(v == null) v = '—';
              return '<td style="padding:3px 10px;border-top:1px solid #21262d;'
                + 'color:#e6edf3">' + v + '</td>'; }).join('') + '</tr>'; });
          h += '</table></div>';
          cell.innerHTML = h;
        })
        .catch(function(ex){
          cell.innerHTML = '<span style="color:#f85149;font-size:12px">❌ '
            + ex + '</span>'; });
    }
  } else { row.style.display = 'none'; }
}
</script>"""

# 表名 → 市场徽章（用户点名四市场：A股/美股/港股/加密；信息流/财报按用途）
_TP_MKT = {"daily": "A股", "index_daily": "A股", "moneyflow": "A股",
           "margin": "A股", "hk_daily": "港股", "us_daily": "美股",
           "cb_daily": "可转债", "crypto_daily": "加密",
           "news": "新闻", "notice": "公告", "report": "研报",
           "income": "财报", "balancesheet": "财报", "cashflow": "财报",
           "fina_indicator": "财报"}

# ==== 批7-BT 随行：_HERMES_FAB_HTML（strategies 页共用悬浮卡）====
_HERMES_FAB_HTML = ("<a id='_hermesfab' href='/hermes/' "
                    "target='_blank' rel='noopener' "
                    "title='超级助手：对话即调度全部 AI 岗位' "
                    "style='position:fixed;right:18px;bottom:70px;width:46px;"
                    "height:46px;border-radius:50%;background:#1f6feb;"
                    "border:2px solid #58a6ff;font-size:22px;line-height:42px;"
                    "text-align:center;z-index:100;cursor:pointer;"
                    "user-select:none;text-decoration:none;"
                    "box-shadow:0 2px 12px rgba(31,111,235,.45)'>🦸</a>")
