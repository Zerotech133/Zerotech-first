# -*- coding: utf-8 -*-
"""kg 页族 misc（api_kg 拆分第二批：ask/lexicon/flow/label/card/shadow/map/company/page）。

纯搬运：原 Handler 方法体 self→h；零行为变化。
"""
import os
import sys
from api_common import (
    QBT_DIR,
    pill_nav,
    _BTSEL_TAG,
    _PILL, _PILL_ON,
    CANDLE_CN)
import logging

def _kg_ask_page(h):
    """K240 四期：图原生问答页（问句→实体链接→子图事实→生成+溯源）。"""
    body = """<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>图谱问答 · 图谱</title>{css}{nav}<style>
.qa{max-width:760px;margin:0 auto}
.qbox{display:flex;gap:8px;margin:12px 0}
.ans{background:var(--gray-2);border:1px solid var(--gray-4);border-radius:10px;
padding:14px;margin:10px 0;color:var(--gray-7);font-size:14px;line-height:1.7}
.src{margin-top:10px}
.src a{color:var(--blu-500);font-size:12.5px;text-decoration:none;margin-right:10px}
.dim2{color:var(--gray-5);font-size:11.5px;margin-top:8px}
</style></head><body>{nav}
<div class=qa><h1>💬 图谱问答</h1>
<div class='dim' style='margin:0 0 8px'>图原生问答：实体链接 → 子图检索 → 事实生成（可溯源，Ollama 本地）</div>
<form id=sf class=qbox>
<input id=qi class=kfind style='flex:1' placeholder='如：三花智控的传导链和派系共识？'
autocomplete=off><button class=kfbtn>提问</button></form>
<div id=out></div></div>
<script>
document.getElementById('sf').addEventListener('submit',function(ev){
  ev.preventDefault();var q=document.getElementById('qi').value.trim();
  if(!q)return;
  var out=document.getElementById('out');
  out.innerHTML='<div class=ans>⏳ 图谱检索与生成中（约 10-30 秒）…</div>';
  fetch('/kg/cask/'+encodeURIComponent(q))
.then(function(r){return r.json()}).then(function(d){
if(!d.ok){out.innerHTML='<div class=ans>❌ '+d.error+'</div>';return}
var h='<div class=ans>'+d.answer+
  '<div class=src>溯源：'+(d.sources||[]).map(function(s){
    var lk='<a href=/kg?q='+encodeURIComponent(s.name)+
      '>'+s.name+'</a>';
    // K262 对话出「点」：股票实体直达作战地图
    if((s.id||'').indexOf('Security|')===0){
      lk+=' <a href=/kg/battle?code='+encodeURIComponent(s.id.slice(9))+
        ' style="color:#7ee787">🗺 作战图</a>'}
    return lk}).join('')+
  '</div><div class=dim2>模型 '+d.model+
  ' · 答案基于图内事实，详细关系见 <a href=/kg/canvasv2>知识画布</a></div></div>';
out.innerHTML=h;});});
</script></body></html>""".replace("{css}", h._KG_CSS).replace(
        "{nav}", pill_nav("kgcanvas"))
    h._html(body)


def _kg_lexicon_page(h):
    """K263-W7 股吧词表维护：情绪词/同义归并在线编辑。

    保存到 quant_backtest/data/lexicon.json，guba_sentiment 按 mtime
    懒加载热生效（portal 长跑进程免重启）。同义归并候选来源：alias_audit
    周报（LLM 核准后人工终审，在此补录）。STOP/TAG_WORDS 两组本页只读
    （编辑场景少，仍走 guba_sentiment.py）。
    """
    import html as _html
    if QBT_DIR not in sys.path:
        sys.path.insert(0, QBT_DIR)
    import guba_sentiment as _gs
    _gs._apply_lexicon(force=True)   # 取当前生效值（含 json 覆盖）
    _panic = "\n".join(_gs.PANIC_WORDS)
    _hope = "\n".join(_gs.HOPE_WORDS)
    _alias = "\n".join("%s=%s" % kv for kv in _gs.WORD_ALIAS.items())
    _stop = "\n".join(sorted(_gs.STOP))
    body = """<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>股吧词表维护 · K263</title>{css}{nav}<style>
.lx{max-width:var(--page-w);margin:0 auto;padding:0 10px}
.lgrid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:12px}
@media(max-width:900px){.lgrid{grid-template-columns:1fr}}
.panel{background:var(--gray-2);border:1px solid var(--gray-4);border-radius:10px;padding:12px}
h3{font-size:14px;color:var(--gray-7);margin:2px 0 8px}
textarea{width:100%;min-height:180px;background:var(--gray-0);color:var(--gray-7);
border:1px solid var(--gray-4);border-radius:8px;padding:10px;font-size:13px;
line-height:1.8;font-family:inherit;box-sizing:border-box}
.sv{display:flex;gap:10px;align-items:center;margin:14px 0}
.sv button{background:#238636;border:none;color:#fff;border-radius:8px;
padding:8px 20px;font-size:13px;cursor:pointer}
#msg{font-size:12.5px;color:var(--gray-5)}
.dim{color:var(--gray-5);font-size:12.5px}
</style></head><body><div class='lx'>
<h1 style='font-size:19px'>📚 股吧词表维护 · K263</h1>
<div class='dim'>情绪词 / 同义归并在线编辑 → data/lexicon.json（guba_sentiment
按 mtime 懒加载，保存即热生效）。同义归并候选来自 alias_audit 周报人工终审。</div>
<div class='lgrid'>
 <div class='panel'><h3>恐慌词（{n_panic} 个，一行一词）</h3>
  <textarea id='panic'>{panic}</textarea></div>
 <div class='panel'><h3>乐观词（{n_hope} 个，一行一词）</h3>
  <textarea id='hope'>{hope}</textarea></div>
</div>
<div class='panel' style='margin-top:14px'><h3>同义归并 WORD_ALIAS
（{n_alias} 组，每行 变体=规范词；只并真同义，不并组合词/上下位词）</h3>
 <textarea id='alias' style='min-height:120px'>{alias}</textarea></div>
<div class='panel' style='margin-top:14px'><h3>停用词 STOP（{n_stop} 个，
一行一词；通用虚词+机构财务话语：关于/万元/同比/点评…。整帖机构噪声
由 NOISE_PAT 剔除，此处管散落词）</h3>
 <textarea id='stop' style='min-height:120px'>{stop}</textarea></div>
<div class='sv'><button onclick='save()'>💾 保存</button><span id='msg'>
本页保存后：股吧词云/情绪打分即时生效；文档词云 TAG_ALIAS 不在此页
（k266_m3_structure.py）。</span></div>
<div class='dim'>只读参考：事件契合 TAG_WORDS {n_tag} 组
（编辑 guba_sentiment.py）</div>
</div>
<script>
function lines(id){return document.getElementById(id).value.split('\\n')
 .map(s=>s.trim()).filter(Boolean)}
function save(){
 var panic=lines('panic'), hope=lines('hope'), stop=lines('stop'), alias={};
 lines('alias').forEach(l=>{var i=l.indexOf('=');
  if(i>0){var k=l.slice(0,i).trim(),v=l.slice(i+1).trim();
   if(k&&v&&k!==v)alias[k]=v}});
 if(!panic.length||!hope.length){alert('情绪词表不可为空');return}
 if(!stop.length){alert('停用词表不可为空');return}
 var msg=document.getElementById('msg');
 msg.textContent='保存中…';
 fetch('/kg/lexicon/save',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({panic:panic,hope:hope,stop:stop,word_alias:alias})})
 .then(r=>r.json()).then(d=>{msg.textContent=d.ok?
  '✅ 已保存：恐慌 '+d.panic+' · 乐观 '+d.hope+' · 归并 '+d.alias+' 组 · 停用 '+d.stop+'（热生效）':
  '❌ '+(d.error||'保存失败')})
 .catch(e=>{msg.textContent='❌ '+e});
}
</script></body></html>""".replace("{css}", h._KG_CSS).replace(
        "{nav}", pill_nav("kgcanvas")).replace(
        "{panic}", _html.escape(_panic)).replace(
        "{hope}", _html.escape(_hope)).replace(
        "{alias}", _html.escape(_alias)).replace(
        "{stop}", _html.escape(_stop)).replace(
        "{n_panic}", str(len(_gs.PANIC_WORDS))).replace(
        "{n_hope}", str(len(_gs.HOPE_WORDS))).replace(
        "{n_alias}", str(len(_gs.WORD_ALIAS))).replace(
        "{n_stop}", str(len(_gs.STOP))).replace(
        "{n_tag}", str(len(_gs.TAG_WORDS)))
    h._html(body)


def _kg_shadow_page(h, qs):
    """M3-W4 影子面板页：真假信号按日对照——信号当时可见（conf/q 三因子，
    pre 窗口无未来信息）vs 影子后验（hm_tag/post_hm10），口径全透明。"""
    import html as _h
    from urllib.parse import parse_qs as _pq
    import sys as _sy
    if QBT_DIR not in _sy.path:
        _sy.path.insert(0, QBT_DIR)
    import kg_portal as KP
    e = _h.escape
    qp = _pq(qs)
    days = max(7, min(365, int((qp.get("d") or ["60"])[0] or 60)))
    sh = KP.api("shadow", None, days)

    def pills():
        out = []
        for d in (30, 60, 90, 180):
            if d == days:
                out.append("<span style='%s'>%d天</span>" % (_PILL_ON, d))
            else:
                out.append("<a href='/kg/shadow?d=%d' style='%s'>"
                           "%d天</a>" % (d, _PILL, d))
        return "".join(out)

    if not sh.get("ok"):
        content = ("<div class='sec'><div class='dn'>%s</div></div>"
                   % e(sh.get("error", "")))
    else:
        t = sh["total"]
        cards = ("<div style='display:grid;grid-template-columns:repeat("
                 "auto-fit,minmax(150px,1fr));gap:10px'>"
                 "<div class='sstat'><span class='st'>结构信号</span>"
                 "<span class='sv'>%d</span><span class='su'>%s ~ %s</span></div>"
                 "<div class='sstat'><span class='st'>先行(资金>1亿)</span>"
                 "<span class='sv'>%d</span><span class='su'>q_hm=1 档</span></div>"
                 "<div class='sstat'><span class='st'>跟随确认</span>"
                 "<span class='sv' style='color:var(--red-500)'>%d</span>"
                 "<span class='su'>历史 ret5 +11.6%%</span></div>"
                 "<div class='sstat'><span class='st'>诱多警示</span>"
                 "<span class='sv' style='color:var(--grn-500)'>%d</span>"
                 "<span class='su'>历史 ret5 -4.2%%</span></div>"
                 "</div>" % (t["n"], t["d_min"], t["d_max"],
                             t["n_lead"] or 0, t["fc"] or 0, t["lw"] or 0))
        method = ("<div class='sec'><h2>口径透明（影子模式）</h2>"
                  "<div style='font-size:13.5px;line-height:1.8'>"
                  "<b>信号当时可见</b>：conf = 0.40·结构 + 0.35·佐证 + "
                  "0.25·资金，资金分 q_hm 只用信号前 10 日龙虎榜（pre 窗口，"
                  "不含未来信息，阶跃口径 &gt;1亿=1）；<b>影子后验</b>：信号后 "
                  "10 日游资净买 post_hm10 每日链回填，标注 hm_tag——"
                  "<span class='bdg g'>跟随确认</span>post10∈(0,1亿] 且 "
                  "pre≤0（纯跟随，非驱动）／<span class='bdg r'>诱多警示"
                  "</span>pre&gt;0 且 post&gt;0（先行+继续买）。"
                  "校准样本 n=9414（集中 2026-04~07 行情段）：游资是跟随确认"
                  "者而非驱动者，确认组正超额全部来自 pre≤0 子组。"
                  "<b style='color:var(--ylw-500)'>体制切换警讯</b>：近月确认样本 "
                  "ret5 全负——行情段外推局限，标注不等于收益承诺。</div></div>")
        bd = "".join(
            "<tr><td>%s</td><td>%d</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td>%.3f</td><td>%.2f</td></tr>"
            % (r["date"], r["n"],
               r["n_tag"] and
               "<b style='color:var(--gray-7)'>%d</b>" % r["n_tag"] or "0",
               r["fc"] and "<span class='bdg g'>%d</span>" % r["fc"] or "—",
               r["lw"] and "<span class='bdg r'>%d</span>" % r["lw"] or "—",
               r["n_lead"] and "<span class='bdg y'>%d</span>" % r["n_lead"]
               or "—", r["conf"] or 0, r["q_hm"] or 0)
            for r in sh["byday"]) or \
            "<tr><td colspan=8 class='dim'>窗口内无信号</td></tr>"
        byday = ("<div class='sec'><h2>按日对照（%d 个交易日，新→旧）</h2>"
                 "<table class='sticky'><tr><th>日期</th><th>信号</th>"
                 "<th>标注</th><th>确认</th><th>诱多</th><th>先行</th>"
                 "<th>avg conf</th><th>avg q_hm</th></tr>%s</table></div>"
                 % (len(sh["byday"]), bd))
        dd = "".join(
            "<tr><td>%s</td><td><a href='/kg?c=%s'>%s %s</a></td>"
            "<td><span class='bdg %s'>%s</span></td>"
            "<td>%.2f</td><td>%.1f</td>"
            "<td>%s</td><td class='%s'>%+.1f</td></tr>"
            % (r["date"], e(r["tc"]), e(r["tc"]), e(r["nm"] or ""),
               "g" if "买" in (r["type"] or "") else
               "r" if "卖" in (r["type"] or "") else "n",
               e(r["type"] or ""), r["conf"] or 0, r["qh"] or 0,
               ("<span class='bdg g'>确认</span>"
                if r["tag"] == "follow_confirm"
                else "<span class='bdg r'>诱多</span>"),
               "up" if (r["post"] or 0) > 0 else "dn",
               (r["post"] or 0) / 1e6)
            for r in sh["detail"]) or \
            "<tr><td colspan=7 class='dim'>窗口内无确认/诱多信号</td></tr>"
        detail = ("<div class='sec'><h2>真假信号明细（后验标注 · 新→旧，"
                  "上限 100 条）</h2><table class='sticky'>"
                  "<tr><th>日期</th><th>标的</th><th>结构</th>"
                  "<th colspan=2 style='text-align:center'>当时可见</th>"
                  "<th colspan=2 style='text-align:center'>影子后验</th></tr>"
                  "<tr><th></th><th></th><th></th><th>conf</th><th>q_hm</th>"
                  "<th>标注</th><th>post10 净买(百万)</th></tr>%s</table>"
                  "</div>" % dd)
        content = ("<h1>👁 影子面板 · 真假信号对照</h1>"
                   "<div class='dim' style='margin:2px 0 10px'>"
                   "M2-W4c 校准口径 · 窗口 %d 天 · %s</div>%s%s%s%s%s"
                   % (days, pills(), cards, method, byday, detail,
                      h._kg_resonance_block()))

    body = ("<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
            "<meta name='viewport' content='width=device-width,"
            "initial-scale=1'><title>影子面板 · 图谱</title>"
            + h._COCKPIT_CSS + h._KG_CSS + "</head><body>"
            + pill_nav("kgshadow") + content + "</body></html>")
    h._html(body)


def _kg_label_page(h):
    """v2.5 画图标注工具（K21-A ②）：半自动标注产训练数据。

    系统预填算法结构（笔/中枢/买卖点，来自 /api/chanlun/structure
    = chanlun_signal.analyze 同源），人只改错处：拖笔端点、改枢边界、
    增删买卖点；保存时后端算 diff（=算法系统性偏差定位）落
    data/labels/chanlun_labels.jsonl（金标签，远期 LightGBM/E3）。
    """
    htm = ("<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
           "<meta name='viewport' content='width=device-width,"
           "initial-scale=1><title>画图标注 · 缠论金标签</title>"
           + h._COCKPIT_CSS
           + "<style>.tbtn{display:inline-block;padding:5px 11px;"
             "border:1px solid var(--gray-4);border-radius:14px;"
             "background:var(--gray-2);color:var(--gray-5);cursor:pointer;"
             "font-size:12.5px;margin:0 3px 4px 0;user-select:none}"
             ".tbtn.on{color:var(--gray-7);border-color:var(--blu-500);"
             "font-weight:600;background:#1f3a5f}"
             ".tbtn.buy{color:var(--red-500)}.tbtn.sell{color:var(--grn-500)}"
             "input,select{background:var(--gray-0);color:var(--gray-7);"
             "border:1px solid var(--gray-4);border-radius:6px;"
             "padding:5px 8px;font-size:13px}"
             "#wrap{margin:10px 0}#cv{width:100%;height:660px;"
             "background:var(--gray-0);border:1px solid var(--gray-4);"
             "border-radius:8px;cursor:crosshair;display:block}"
             "#st{margin-left:10px;font-size:13px;color:var(--gray-5)}"
             "</style></head><body>" + pill_nav("kglabel")
           + "<h1>🖊 画图标注 · 缠论结构金标签</h1>"
           "<div class='dim'>系统预填算法结构（笔橙 / 中枢紫 / 买卖点"
           "箭头），你只改错处：拖端点修笔、拖边改中枢、点图放买卖点、"
           "Delete 删选中、Ctrl+Z 撤销 · 保存即算 diff（算法 vs 人工）"
           "落 data/labels/chanlun_labels.jsonl · 滚轮缩放 / 空白拖动"
           "平移</div>"
           "<div style='margin:10px 0'>"
           "<input id='code' value='301216.SZ' size='12' "
           "placeholder='代码如 301216.SZ'> "
           "<select id='period'><option>1d</option>"
           "<option selected>30m</option><option>5m</option></select> "
           "<input id='limit' value='480' size='6' title='装载根数'> "
           "<span class='tbtn on' id='bLoad' "
           "onclick='load()'>🔄 加载</span> │ "
           "<span class='tbtn on' data-tool='select'>☝ 拖动/改</span>"
           "<span class='tbtn' data-tool='bi'>✏ 画笔</span>"
           "<span class='tbtn' data-tool='pivot'>▭ 中枢</span>"
           "<span class='tbtn buy' data-bs='1买'>1买</span>"
           "<span class='tbtn buy' data-bs='2买'>2买</span>"
           "<span class='tbtn buy' data-bs='3买'>3买</span>"
           "<span class='tbtn sell' data-bs='1卖'>1卖</span>"
           "<span class='tbtn sell' data-bs='2卖'>2卖</span>"
           "<span class='tbtn sell' data-bs='3卖'>3卖</span> │ "
           "<span class='tbtn' onclick='undo()'>↩ 撤销</span>"
           "<span class='tbtn' onclick='delSel()'>🗑 删除选中</span>"
           "<span class='tbtn on' onclick='save()'>💾 保存</span>"
           "<span id='st'></span></div>"
           "<div id='wrap'><canvas id='cv'></canvas></div>"
           "<div class='dim' id='tip'></div>")
    from kg.kg_js import _KG_LABEL_JS  # noqa: E402  (拆分收尾：常量已迁 kg_js)
    htm += "<script>\n" + _KG_LABEL_JS + "</script></body></html>"
    h._html(htm)


def _kg_card_page(h):
    """K29 推演卡网页版 + 三键校对反馈。

    数据与 CLI 同源（chanlun_card.card：三级联立 + 三灯 + 分类应对
    + 二买分档 + 区间套），零新计算；每级卡尾三键「对/偏/错」→
    POST /api/chanlun/card/feedback → card_feedback.jsonl（幂等键
    code+period+last_dt，改判覆盖）。只读渲染，反馈不回写任何计算。
    """
    from urllib.parse import parse_qs as _pqc
    import html as _h
    qs = h.path.split("?", 1)[1] if "?" in h.path else ""
    qp = _pqc(qs)
    code = (qp.get("code") or [""])[0].strip()
    e = _h.escape

    def _flag(v, lab):
        if v is None:
            return ""
        return (" <b style='color:%s'>%s</b>"
                % ("#3fb950" if v else "#8b949e", lab if v else lab + "✗"))

    def _trend_html(t):
        if not t:
            return "趋势 —"
        nm = {"up": "↑ 上升", "down": "↓ 下降"}.get(t.get("state"),
                                                    "→ 震荡")
        sa = ""
        if t.get("state") == "up" and t.get("short_adjust"):
            sa = " · 长升短调✓"
        hh, hl = t.get("hh"), t.get("hl")
        rng = ("（HH %s / HL %s）"
               % (e("%.2f" % hh) if hh is not None else "—",
                  e("%.2f" % hl) if hl is not None else "—"))
        return "趋势 %s%s%s" % (nm, sa, rng)

    def _lvl_block(p, v, cd_code):
        lab = {"1d": "日线", "30m": "30 分钟", "5m": "5 分钟"}[p]
        if not v:
            return ("<div class='sec'><h3>%s</h3>"
                    "<div class='mute'>数据不可用 / 笔不足</div></div>"
                    % lab)
        last_dt = str((v.get("range") or ["", ""])[1])
        rows = []
        sl = v.get("sig_labels") or []
        window = list(zip((v.get("signals_recent") or [])[-5:],
                          sl[-5:], strict=False))
        for s, lab_txt in window:
            flags = (_flag(s.get("bei"), "背") + _flag(s.get("bei2"), "背2")
                     + _flag(s.get("mbei"), "m背") + _flag(s.get("tbe"), "趋")
                     + _flag(s.get("vv"), "量"))
            rows.append("<tr><td>%s</td><td>%s</td><td>%s</td>"
                        "<td>%s</td><td>%s</td></tr>"
                        % (e(str(s.get("dt"))), e(str(s.get("type"))),
                           e("%.2f" % s["price"]) if s.get("price") else "—",
                           e(str(lab_txt or "")), flags))
        pv = v.get("pivot")
        pv_html = ("中枢 %.2f–%.2f（%s，%s→%s）"
                   % (pv["zg"], pv["zd"], e(str(pv.get("state") or "—")),
                      e(str(pv.get("s_dt"))), e(str(pv.get("e_dt"))))
                   if pv else "无中枢")
        lb = v.get("last_bi") or {}
        fr = v.get("last_fractal")
        an = v.get("anchors") or {}
        sig_tbl = ("<table class='t'><tr><th>时间</th><th>信号</th>"
                   "<th>价</th><th>细分</th><th>影子</th></tr>%s</table>"
                   % "".join(rows)) if rows else \
                 "<div class='mute'>窗口内无信号</div>"
        fb = ("<div class='fb' data-p='%s' data-d='%s'>"
              "这级推演："
              "<button class='fb-b' data-v='right'>对</button>"
              "<button class='fb-b' data-v='partial'>偏</button>"
              "<button class='fb-b' data-v='wrong'>错</button>"
              "<span class='fb-msg'></span></div>"
              % (p, e(last_dt)))
        return (
            "<div class='sec'><h3>%s <span class='mute'>%s – %s"
            " · %d 根</span></h3>"
            "<div>%s · 现价 <b>%.3f</b> · MA20 %s · MA60 %s</div>"
            "<div>末笔%s %s→%s（%s → %s）%s</div>"
            "<div>%s · 锚8高 %s · 锚8低 %s</div>%s%s</div>"
            % (lab, e(str((v.get("range") or ["", ""])[0])),
               e(str((v.get("range") or ["", ""])[1])),
               v.get("bars_n") or 0,
               _trend_html(v.get("trend")),
               v.get("last_px") or 0,
               e("%.3f" % v["ma20"]) if v.get("ma20") else "—",
               e("%.3f" % v["ma60"]) if v.get("ma60") else "—",
               "↑" if (lb.get("dir") == "上") else "↓",
               e(str(lb.get("s_dt"))), e(str(lb.get("e_dt"))),
               e("%.2f" % lb["s_price"]) if lb.get("s_price") else "—",
               e("%.2f" % lb["e_price"]) if lb.get("e_price") else "—",
               (" · 末分型 %s@%s（%s）"
                % (e(str(fr["type"])), e("%.2f" % fr["price"]),
                   e(str(fr["dt"]))) if fr else ""),
               pv_html,
               e("%.2f" % an["high_8"]) if an.get("high_8") else "—",
               e("%.2f" % an["low_8"]) if an.get("low_8") else "—",
               sig_tbl, fb))

    htm = ("<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
           "<meta name='viewport' content='width=device-width,"
           "initial-scale=1'><title>推演卡 · 缠论</title>"
           + h._COCKPIT_CSS
           + "<style>.sec{background:var(--gray-2);border:1px solid var(--gray-4);"
             "border-radius:8px;padding:12px 14px;margin:10px 0}"
             ".sec h3{margin:0 0 8px;font-size:15px}"
             ".sec div{font-size:13px;margin:3px 0;color:var(--gray-6)}"
             "table.t{border-collapse:collapse;margin:8px 0;width:100%}"
             "table.t th,table.t td{border:1px solid var(--gray-3);"
             "padding:4px 8px;font-size:12.5px;text-align:left}"
             ".fb{margin-top:8px;padding-top:8px;"
             "border-top:1px dashed var(--gray-4)}"
             ".fb-b{margin:0 4px;padding:4px 14px;border-radius:12px;"
             "border:1px solid var(--gray-4);background:var(--gray-0);"
             "color:var(--gray-5);cursor:pointer;font-size:12.5px}"
             ".fb-b[data-v=right]{color:var(--grn-500)}"
             ".fb-b[data-v=partial]{color:var(--ylw-500)}"
             ".fb-b[data-v=wrong]{color:var(--red-500)}"
             ".fb-b:disabled{opacity:.45;cursor:default}"
             ".fb-msg{margin-left:8px;font-size:12px;color:var(--gray-5)}"
             ".kp li{margin:4px 0;font-size:13px}</style></head><body>"
           + pill_nav("kgcard")
           + "<h2 style='margin:14px 4px'>🃏 三级联立推演卡"
             " · 三键校对</h2>"
           + "<form action='/kg/card' method='get' "
             "style='margin:8px 4px'>"
             "<input name='code' value='%s' placeholder="
             "'600337.SH' style='width:130px;padding:6px 8px'> "
             "<button>查看</button> "
             "<span class='mute'>每级卡尾【对/偏/错】校对落 "
             "card_feedback.jsonl（改判覆盖）</span></form>"
           % e(code))
    if not code:
        htm += ("<div class='sec'>输入代码开始（如 600337.SH / "
                "301216.SZ）</div></body></html>")
        h._html(htm)
        return
    try:
        import sys as _syc
        if QBT_DIR not in _syc.path:
            _syc.path.insert(0, QBT_DIR)
        from chanlun_card import card as _card
        cd = _card(code)
    except Exception as ex:
        htm += ("<div class='sec'>推演卡计算失败：%s</div>"
                "</body></html>" % e(str(ex)[:120]))
        h._html(htm)
        return
    kp = cd.get("key_point") or {}
    htm += ("<div class='sec'><h3>三灯（#8 关键点就绪度）</h3>"
            "<ul class='kp'>"
            "<li>空间：%s</li><li>结构：%s</li><li>动能：%s</li>"
            "<li><b>%s</b></li></ul></div>"
            % (e(str((kp.get("pivot_bottom") or {}).get("detail") or "—")),
               e(str((kp.get("sub_done") or {}).get("detail") or "—")),
               e(str((kp.get("ma_ok") or {}).get("detail") or "—")),
               e(str(kp.get("verdict") or "—"))))
    cls = cd.get("classify") or []
    if cls:
        htm += ("<div class='sec'><h3>分类应对（锚位 if/else）</h3>"
                "<ul class='kp'>%s</ul></div>"
                % "".join("<li>%s</li>" % e(x) for x in cls))
    tb = cd.get("two_buy") or []
    if tb:
        htm += ("<div class='sec'><h3>二买分档</h3><ul class='kp'>%s"
                "</ul></div>" % "".join(
                    "<li>%s</li>" % e(str(t)) for t in tb))
    qj = cd.get("qjt") or []
    if qj:
        htm += ("<div class='sec'><h3>区间套（v2-d）</h3><ul class='kp'>"
                "%s</ul></div>" % "".join(
                    "<li>%s</li>" % e(q.get("note") or "") for q in qj))
    for p in ("1d", "30m", "5m"):
        htm += _lvl_block(p, (cd.get("levels") or {}).get(p), code)
    from kg.kg_js import _KG_CARD_JS  # noqa: E402  (同上)
    htm += _KG_CARD_JS + "</body></html>"
    h._html(htm)


def _kg_flow_page(h):
    """SF 板块异动工单看板（六步数据流第⑥步）。

    数据源（kg_sector_flow 每日链产出，页面只读不重算）：
    quant_backtest/reports/sector_flow_latest.json（当日工单快照，
    含 cross_check 交叉验证）+ sector_flow_hist.jsonl（触发历史）。
    规则：同行业同日 ≥5 股上龙虎榜 + 游资净买同向 ≥4（250 日定标）。"""
    import html as _h
    import json as _jf
    e = _h.escape
    doc = {}
    try:
        with open(os.path.join(QBT_DIR, "reports",
                               "sector_flow_latest.json"),
                  encoding="utf-8") as f:
            doc = _jf.load(f)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_kg.py", exc)
    hist = []
    try:
        with open(os.path.join(QBT_DIR, "reports",
                               "sector_flow_hist.jsonl"),
                  encoding="utf-8") as f:
            for line in f:
                try:
                    hist.append(_jf.loads(line))
                except Exception:
                    continue
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_kg.py", exc)
    hist = hist[-30:]                      # 近 30 条触发记录

    if not doc:
        h._html("<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
                   "<title>板块异动工单</title>" + h._COCKPIT_CSS
                   + "</head><body>" + pill_nav("kgflow")
                   + "<h1>🧨 板块异动工单</h1><div class='sec'>"
                   "尚无工单快照——kg_sector_flow 夜链首跑后展示"
                   "</div></body></html>")
        return

    tickets = doc.get("tickets") or []
    rule = doc.get("rule") or {}

    def _bd(bd):
        """技术共振 dict → 着色 spans（主口径红/绿、dt 复盘灰）。"""
        if not bd:
            return "<span class='dim'>—</span>"
        g = lambda k: bd.get(k) or 0        # noqa: E731
        return ("结构买 <b style='color:var(--red-500)'>%d</b> / "
                "卖 <b style='color:var(--grn-500)'>%d</b>"
                "<span class='dim'>（信号日口径 买%d/卖%d）</span>"
                " · 量能 %d · 手法 %d"
                % (g("结构买"), g("结构卖"), g("结构买dt"),
                   g("结构卖dt"), g("量能"), g("手法")))

    # 顶部统计四格
    n_stk = sum(t.get("n_listed") or 0 for t in tickets)
    h30 = sum(h.get("n") or 0 for h in hist)
    stats = ("<div style='display:grid;grid-template-columns:repeat("
             "auto-fit,minmax(150px,1fr));gap:10px'>"
             "<div class='sstat'><span class='st'>当日工单</span>"
             "<span class='sv'>%d</span><span class='su'>%s</span></div>"
             "<div class='sstat'><span class='st'>上榜股合计</span>"
             "<span class='sv'>%d</span><span class='su'>触发行业内"
             "</span></div>"
             "<div class='sstat'><span class='st'>近30次触发</span>"
             "<span class='sv'>%d</span><span class='su'>hist 追加行"
             "</span></div>"
             "<div class='sstat'><span class='st'>规则阈值</span>"
             "<span class='sv' style='font-size:18px'>%s/%s</span>"
             "<span class='su'>上榜数/游资同向</span></div></div>"
             % (doc.get("n_tickets") or 0, doc.get("date") or "—",
                n_stk, h30, rule.get("n_min"), rule.get("m_min")))

    # 逐单卡片
    cards = ""
    for t in tickets:
        cx = t.get("cross") or {}
        bd = _bd(cx.get("tech_breakdown"))
        seat_rows = "".join(
            "<div style='font-size:13px'>· %s ×%d"
            "<span class='dim'>（%s）</span></div>"
            % (e(sx.get("seat") or ""), sx.get("n") or 0,
               e("、".join(sx.get("stocks") or [])))
            for sx in (cx.get("seat_x") or []))
        if not seat_rows:
            seat_rows = "<div class='dim' style='font-size:13px'>" \
                        "无跨票席位（无协同操盘证据）</div>"
        rows = "".join(
            "<tr><td><a href='/kg?c=%s'>%s</a></td><td>%s</td>"
            "<td style='text-align:right;color:%s'>%+.0f万</td>"
            "<td style='text-align:center'>%s</td><td class='dim'>%s"
            "</td></tr>"
            % (e(s.get("tc") or ""), e(s.get("tc") or ""),
               e(s.get("nm") or ""),
               "#f85149" if (s.get("yz_w") or 0) > 0
               else "#3fb950" if (s.get("yz_w") or 0) < 0
               else "#8b949e",
               s.get("yz_w") or 0,
               "✓" if s.get("pen") else "—",
               e(s.get("reason") or ""))
            for s in t.get("stocks") or [])
        cards += ("<div class='sec'><h2>[%s] %d 股 · 游资%s同向 %d · "
                  "差额 %+.2f亿</h2>"
                  "<div style='font-size:13px;margin-bottom:8px'>"
                  "穿透 %d/%d · 连续 %d · 跨票席位 %d · 共振 %s</div>"
                  "%s<table><tr><th>代码</th><th>名称</th>"
                  "<th style='text-align:right'>游资净买</th>"
                  "<th>次日穿透</th><th>上榜原因</th></tr>%s</table>"
                  "</div>"
                  % (e(t.get("industry") or ""),
                     t.get("n_listed") or 0,
                     e(t.get("yz_dir") or ""),
                     t.get("n_yz_same") or 0,
                     t.get("gap_sum") or 0,
                     t.get("n_pen") or 0, t.get("n_listed") or 0,
                     cx.get("cont_n") or 0,
                     len(cx.get("seat_x") or []), bd,
                     seat_rows, rows or ""))
    if not cards:
        cards = ("<div class='sec'><h2>当日无触发</h2>"
                 "<div class='dim'>%s 无达阈值行业（≥%s 股上榜 + "
                 "游资同向 ≥%s）——安静日</div></div>"
                 % (doc.get("date") or "—", rule.get("n_min"),
                    rule.get("m_min")))

    hrows = "".join(
        "<tr><td>%s</td><td style='text-align:right'>%d</td>"
        "<td class='dim'>%s</td></tr>"
        % (h.get("date") or "", h.get("n") or 0,
           e("、".join(h.get("inds") or [])))
        for h in reversed(hist))
    htab = ("<div class='sec'><h2>📜 触发历史<span class='dim' "
            "style='font-size:12px'>（近 %d 次运行，hist.jsonl）"
            "</span></h2><table><tr><th>日期</th><th>单数</th>"
            "<th>行业</th></tr>%s</table></div>"
            % (len(hist), hrows or "<tr><td colspan='3' class='dim'>"
               "尚无历史</td></tr>"))

    h._html(
        "<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
        "<meta name='viewport' content='width=device-width,"
        "initial-scale=1'><title>板块异动工单 · 六步数据流</title>"
        + h._COCKPIT_CSS + "</head><body>" + pill_nav("kgflow")
        + "<h1>🧨 板块异动工单</h1><div class='dim'>同行业同日 "
        "≥%s 股上龙虎榜 + 游资净买同向 ≥%s → 工单（250 日回放定标 "
        "日均 0.35 单）· 生成于 %s · <a href='/kg'>图谱探索</a> / "
        "<a href='/kg/map'>游资地图</a></div>%s%s%s</body></html>"
        % (rule.get("n_min"), rule.get("m_min"),
           e(doc.get("generated_at") or "—"), stats, cards, htab))


def _kg_map_page(h, qs):
    """M3-W3 游资地图页：19 主体全景 + 共现联动 + 动向流 / 单主体画像。
    数据层 kg_portal.hotmoney_map / hm_profile（TRADED_ON 纯聚合）。"""
    import html as _h
    from urllib.parse import parse_qs as _pq, quote as _qt
    import sys as _sy
    if QBT_DIR not in _sy.path:
        _sy.path.insert(0, QBT_DIR)
    import kg_portal as KP
    e = _h.escape

    def hmlink(nm):
        return ("<a href='/kg/map?hm=%s'>%s</a>" % (_qt(nm), e(nm)))

    def slink(tc, nm):
        return "<a href='/kg?c=%s'>%s %s</a>" % (e(tc), e(tc), e(nm or ""))

    qp = _pq(qs)
    hm = (qp.get("hm") or [""])[0].strip()
    days = max(7, min(365, int((qp.get("d") or ["90"])[0] or 90)))
    streamed = False  # U4-A：全景分支流式（骨架先行，慢数据后发）

    def pills(d_list, base):
        out = []
        sep = "&" if "?" in base else "?"
        for d in d_list:
            if d == days:
                out.append("<span style='%s'>%d天</span>" % (_PILL_ON, d))
            else:
                out.append("<a href='%s%sd=%d' style='%s'>%d天</a>"
                           % (base, sep, d, _PILL, d))
        return "".join(out)

    back = ("<div style='margin:0 0 10px'><a href='/kg/map' "
            "style='%s'>← 地图全景</a> <a href='/kg' style='%s'>"
            "图谱探索</a></div>" % (_PILL, _PILL))

    if hm:  # ── 单主体画像 ──
        pr = KP.api("profile", hm, days)
        title = "%s 席位画像" % hm
        if not pr.get("ok"):
            content = ("<div class='sec'><div class='dn'>%s</div></div>"
                       % e(pr.get("error", "")))
        else:
            rl = pr["run_len"]
            _wr = pr.get("win_rate")
            _wr_h = (" · 次日胜率 <b class='%s'>%.0f%%</b>（%d 笔）"
                     % ("up" if _wr["rate"] >= 0.5 else "dn",
                        _wr["rate"] * 100, _wr["n"])) if _wr else ""
            head = (
                "<h1>💰 %s</h1>"
                "<div class='dim' style='margin:2px 0 8px'>窗口 %d 天 · "
                "%d 席位 · %d 笔上榜 · 净买合计 <b class='%s'>%+.0f 百万"
                "</b> · top5 集中度 %.0f%%%s</div>"
                "<div style='display:flex;gap:8px;flex-wrap:wrap'>%s</div>"
                % (e(pr["name"]), pr["window_days"], pr["seats_n"],
                   pr["trades_n"],
                   "up" if (pr["net_buy_total"] or 0) > 0 else "dn",
                   (pr["net_buy_total"] or 0) / 1e6,
                   pr["concentration"] * 100, _wr_h,
                   pills((90, 180, 365), "/kg/map?hm="
                         + _qt(pr["name"]))))
            style = ("<div class='sec'><h2>持有风格</h2>"
                     "<table><tr><th>连续上榜段均值</th><td>%.1f 天</td>"
                     "<th>中位</th><td>%d 天</td><th>最长</th><td>%d 天"
                     "</td><th>段数</th><td>%d</td></tr></table>"
                     "<div class='dim' style='margin-top:4px'>口径：同一"
                     "标的相邻上榜日 ≤3 日历日算同一波（节假日断段，"
                     "倾向低估）</div></div>"
                     % (rl["avg"], rl["median"], rl["max"], rl["n_runs"]))

            def pref_bars(rows, key_n="n", with_net=False):
                mx = max([r[key_n] for r in rows] or [1])
                out = []
                for r in rows:
                    net = ""
                    if with_net:
                        v = r.get("net") or 0
                        net = (" <span class='%s'>%+.0fM</span>"
                               % ("up" if v > 0 else "dn", v / 1e6))
                    out.append(
                        "<div style='display:flex;align-items:center;"
                        "gap:8px;margin:4px 0'><span style='width:96px;"
                        "flex:none;font-size:13px' title='%s'>%s</span>"
                        "<span class='hb' style='width:%dpx'></span>"
                        "<span class='dim'>%d</span>%s</div>"
                        % (e(r["k"]), e(r["k"]),
                           max(4, int(r[key_n] * 150 / mx)),
                           r[key_n], net))
                return "".join(out) or "<div class='dim'>无数据</div>"

            ind_html = ("<div class='sec'><h2>行业偏好（上榜次数）</h2>%s"
                        "</div>" % pref_bars(pr["industry_pref"],
                                             with_net=True))
            area_html = ("<div class='sec'><h2>区域偏好</h2>%s</div>"
                         % pref_bars(pr["area_pref"]))
            mxm = max([m["n"] for m in pr["monthly"]] or [1])
            mon = "".join(
                "<div style='display:inline-block;text-align:center;"
                "margin:2px 5px'><span class='hb' style='width:14px;"
                "height:%dpx'></span><div class='dim' style='font-size:"
                "11px'>%s<br>%d</div></div>"
                % (max(3, m["n"] * 40 / mxm), m["ym"], m["n"])
                for m in pr["monthly"]) or \
                "<div class='dim'>窗口内无上榜</div>"
            mon_html = ("<div class='sec'><h2>月度频次</h2>%s</div>" % mon)
            tops = "".join(
                "<tr><td>%s</td><td>%d</td><td class='%s'>%+.0f</td>"
                "<td>%s</td></tr>"
                % (slink(r["tc"], r["nm"]), r["n"],
                   "up" if (r["net"] or 0) > 0 else "dn",
                   (r["net"] or 0) / 1e6, r["last"])
                for r in pr["top_securities"]) or \
                "<tr><td colspan=4 class='dim'>窗口内无交易</td></tr>"
            top_html = ("<div class='sec'><h2>Top 标的</h2><table class="
                        "'sticky'><tr><th>标的</th><th>次数</th>"
                        "<th>净买(百万)</th><th>最近</th></tr>%s</table>"
                        "</div>" % tops)
            # P2-① 榜后差额参与：主体席位同票同日上榜事件的资金穿透
            tg = pr.get("topgap") or {}
            _NAT = {"youzi": ("y", "游资"), "quant": ("b", "量化"),
                    "org": ("n", "机构"), "retail": ("r", "散户"),
                    "other": ("", "普通")}
            _tg_rows = []
            for r in (tg.get("rows") or []):
                nc = _NAT.get(r.get("nat") or "other", ("", "普通"))
                _pen = r.get("pen")
                _gap = r.get("gap")
                _gap_s = ("—" if _gap is None else
                          ("%.2f亿" % (_gap / 1e8) if abs(_gap) >= 1e8
                           else "%.0f万" % (_gap / 1e4)))
                _ret = r.get("ret1")
                _tg_rows.append(
                    "<tr><td>%s</td><td>%s</td><td class='%s'>%s</td>"
                    "<td>%s</td><td><span class='bdg %s'>%s</span></td>"
                    "<td class='%s'>%s</td><td>%s</td></tr>"
                    % (r.get("d"), slink(r["tc"], r.get("nm")),
                       "up" if (_gap or 0) > 0 else "dn", _gap_s,
                       ("%.1f%%" % (100 * r["gr"]))
                       if r.get("gr") is not None else "—",
                       nc[0], nc[1],
                       ("up" if (_ret or 0) > 0 else "dn"),
                       ("—" if _ret is None else "%+.2f%%" % (_ret * 100)),
                       ("<span class='bdg g'>穿透</span>" if _pen == 1
                        else ("<span class='bdg r'>未穿</span>"
                              if _pen == 0 else "—"))))
            _pr_rate = tg.get("pen_rate")
            _pr_h = (" · 穿透率 <b class='%s'>%.0f%%</b>"
                     % ("up" if _pr_rate >= 0.5 else "dn",
                        _pr_rate * 100)) if _pr_rate is not None else ""
            tg_html = ("<div class='sec'><h2>榜后差额参与</h2>"
                       "<div class='dim' style='margin:2px 0 6px'>其席位"
                       "上榜日的前五买卖差 · 近 %d 笔%s</div>"
                       "<table class='sticky'><tr><th>日期</th><th>标的"
                       "</th><th>差额</th><th>占成交额</th><th>主导性质"
                       "</th><th>次日</th><th>穿透</th></tr>%s</table>"
                       "</div>"
                       % (tg.get("n", 0), _pr_h,
                          "".join(_tg_rows) or
                          "<tr><td colspan=7 class='dim'>窗口内无上榜"
                          "事件</td></tr>"))
            content = (back + head + style
                       + "<div class='duo'>%s<div>%s</div></div>"
                       % (ind_html, mon_html + area_html + top_html)
                       + tg_html)
    else:  # ── 地图全景 ──
        # U4-A 流式骨架：head+导航+Tab 框架先 flush（浏览器即刻有反馈，
        # 冷缓存 Cypher 10~30s 不再白屏），数据算完再续发三个 tabc
        h._html_open(
            "<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
            "<meta name='viewport' content='width=device-width,"
            "initial-scale=1'><title>游资地图</title>"
            + h._COCKPIT_CSS + h._KG_CSS + "</head><body>"
            + pill_nav("kgmap")
            + "<h1>💰 游资地图</h1><div class='dim' style='margin:2px 0 "
              "8px'>TRADED_ON 纯聚合 · 窗口 %d 天 · 窗口切换 %s</div>"
              % (days, pills((30, 90, 180), "/kg/map"))
            + "<div class='tabwrap'><div class='tabs' style='pointer-"
              "events:none;opacity:.65'>"
              "<button class='on' onclick=\"_tab(this,'kgt-ov')\">"
              "🧭 集团总览</button>"
              "<button onclick=\"_tab(this,'kgt-net')\">🕸 席位网络"
              "</button>"
              "<button onclick=\"_tab(this,'kgt-geo')\">🗺 地图"
              "</button>"
              "<button onclick=\"_tab(this,'kgt-det')\">📋 主体明细"
              "</button></div>"
            + "<div id='kgmap-loading' class='sec' style='margin-top:"
              "16px;padding:52px 17px;text-align:center'>"
              "<div style='font-size:30px;line-height:1'>⏳</div>"
              "<div class='dim' style='margin-top:12px'>正在聚合席位"
              "数据 · Neo4j 共现大查询<br>冷缓存首次约 10~30 秒，完成后"
              "自动展开</div></div>")
        streamed = True
        mp = KP.api("map", None, days)
        title = "游资地图"
        if not mp.get("ok"):
            content = (
                "<script>var _t=document.querySelector('.tabs');"
                "if(_t){_t.style.pointerEvents='';_t.style.opacity='';}"
                "var _l=document.getElementById('kgmap-loading');"
                "if(_l&&_l.parentNode)_l.parentNode.removeChild(_l);"
                "</script><div class='sec'><div class='dn'>%s</div>"
                "</div></div>" % e(mp.get("error", "")))
        else:
            rows = "".join(
                "<tr><td>%s</td><td>%d</td><td>%d</td><td>%d</td>"
                "<td class='%s'>%+.0f</td><td>%s</td></tr>"
                % (hmlink(r["name"]), r["seats"], r["n"], r["nsec"],
                   "up" if (r["net"] or 0) > 0 else "dn",
                   (r["net"] or 0) / 1e6, r["last"] or "—")
                for r in mp["roster"]) or ""
            roster = ("<div class='sec'><h2>主体全景（%d 家，按净买排）"
                      "</h2><table class='sticky'><tr><th>主体</th>"
                      "<th>席位</th><th>笔数</th><th>标的数</th>"
                      "<th>净买(百万)</th><th>最近</th></tr>%s</table>"
                      "<div class='dim' style='margin-top:4px'>点击主体"
                      "名进入席位风格画像；拉萨天团为散户通道汇总，"
                      "共现与榜位解读需区别对待</div></div>"
                      % (len(mp["roster"]), rows))
            pairs = "".join(
                "<div style='margin:6px 0'>%s <span class='dim'>×</span> %s"
                " <span class='bdg n'>%d 次</span> <span class='dim'>%s"
                "</span></div>"
                % (hmlink(p["a"]), hmlink(p["b"]), p["n"],
                   " ".join("<a href='/kg?c=%s'>%s</a>" % (e(t), e(t))
                            for t in p["tcs"][:2]))
                for p in mp["pairs"]) or \
                "<div class='dim'>窗口内无共现</div>"
            pair_html = ("<div class='sec'><h2>共现联动（同日同标的）</h2>"
                         "%s</div>" % pairs)
            # ── P3-c 交替接力（先后上榜≠同日共现，互补口径）──
            alt_html = ""
            ap = KP.api("altpairs", None, days)
            if ap.get("ok") and ap["pairs"]:
                def _pcell(p):
                    if p["a"] == p["b"]:
                        return ("%s <span class='bdg'>自对</span>"
                                % hmlink(p["a"]))
                    return ("%s <span class='dim'>→</span> %s"
                            % (hmlink(p["a"]), hmlink(p["b"])))
                arows = "".join(
                    "<tr><td>%s</td><td><b>%d</b></td><td>%d</td>"
                    "<td>%d → %d</td><td>%s</td></tr>"
                    % (_pcell(p), p["alt"], p["nsec"], p["ab"],
                       p["ba"],
                       " ".join("<a href='/kg?c=%s'>%s</a>" % (e(t), e(t))
                                for t in p["tcs"][:2]))
                    for p in ap["pairs"]) or (
                    "<tr><td colspan=5 class='dim'>窗口内无交替"
                    "模式</td></tr>")
                alt_html = (
                    "<div class='sec'><h2>交替接力（隔日先后上榜）</h2>"
                    "<table><tr><th>席位对</th><th>交替</th>"
                    "<th>标的数</th><th>a先→b先</th><th>标的</th>"
                    "</tr>%s</table><div class='dim' style='"
                    "margin-top:4px'>窗口 %d 天内同标的先后上榜"
                    "（间隔≤%d 自然日、非同日）；a先→b先=两方向次数，"
                    "不对称=固定方向接力（后者接前者的货）；自对=同席"
                    "位反复上榜同一标的</div></div>"
                    % (arows, ap.get("window_days") or days,
                       ap.get("gap") or 7))
            else:
                alt_html = ("<div class='sec'><h2>交替接力（隔日先后"
                            "上榜）</h2><div class='dim'>%s</div></div>"
                            % e(ap.get("error") or "窗口内无交替模式"))
            flow = "".join(
                "<tr><td>%s</td><td>%s</td><td>%s</td>"
                "<td class='%s'>%+.1f</td></tr>"
                % (r["date"], hmlink(r["hm"]),
                   slink(r["tc"], r["nm"]),
                   "up" if (r["net"] or 0) > 0 else "dn",
                   (r["net"] or 0) / 1e6)
                for r in mp["flow"]) or \
                "<tr><td colspan=4 class='dim'>窗口内无动向</td></tr>"
            flow_html = ("<div class='sec'><h2>近期动向流</h2><table "
                         "class='sticky'><tr><th>日期</th><th>主体</th>"
                         "<th>标的</th><th>净买(百万)</th></tr>%s</table>"
                         "</div>" % flow)
            # ── G4 游资集团（Louvain 社区发现） ──
            comm_html = ""
            cm = KP.api("communities")
            if cm.get("ok"):
                cards = []
                for c in cm["top"]:
                    mrows = "".join(
                        "<tr><td title='%s'>%s</td><td>%s</td>"
                        "<td>%d</td><td class='bdg n'>%d</td></tr>"
                        % (e(m["seat"]), e(m["seat"][:22]),
                           hmlink(m["reg"]) if m["reg"]
                           else "<span class='dim'>—</span>",
                           m["n"], m["deg"])
                        for m in c["members"]) or \
                        "<tr><td colspan=4 class='dim'>无成员</td></tr>"
                    cards.append(
                        "<div class='sec'><h2>🧩 %s "
                        "<span class='bdg n'>%d 席位</span> "
                        "<span class='bdg'>共现 %d</span></h2><table "
                        "class='sticky'><tr><th>席位</th><th>注册主体"
                        "</th><th>上榜次数</th><th>共现度</th></tr>%s"
                        "</table></div>"
                        % (e(c["name"]), c["n_seats"], c["w_total"],
                           mrows))
                grp_head = ("<div class='sec'><h2>游资集团</h2><div "
                            "class='dim' style='margin:2px 0 4px'>"
                            "Louvain 社区发现 · 同日同标的共现 ≥%d 次"
                            "连边 · 席位池=词表 %d+影子扩容 %d"
                            "（top_inst 高频未注册，散户/机构/沪深"
                            "股通通道已排除 %d 对）· %d/%d 席位入图 "
                            "%d 边 → %d 集团 · 生成 %s</div></div>"
                            % (cm.get("min_w", 2),
                               cm.get("n_seats_total", 0)
                               - cm.get("n_unreg_pool", 0),
                               cm.get("n_unreg_pool", 0),
                               cm.get("n_retail_pairs_excl", 0),
                               cm.get("n_seats_graph", 0),
                               cm.get("n_seats_total", 0),
                               cm.get("n_edges", 0),
                               cm.get("n_communities", 0),
                               e(cm.get("generated_at", ""))))
                grp = "".join(cards) or \
                    "<div class='sec'><div class='dim'>窗口内无 ≥3 席位" \
                    "的社区</div></div>"
                wl = cm.get("watchlist") or []
                if wl:
                    wl_rows = "".join(
                        "<tr><td>%s</td><td>%s</td><td>%d</td>"
                        "<td class='bdg n'>%d</td></tr>"
                        % (e(w["seat"]), e(w["comm"]), w["n"], w["deg"])
                        for w in wl)
                    wl_html = ("<div class='sec'><h2>名单外席位候选"
                               "（与注册游资同社区·高频）</h2><table "
                               "class='sticky'><tr><th>席位</th><th>"
                               "所属集团</th><th>上榜次数</th><th>共现度"
                               "</th></tr>%s</table></div>" % wl_rows)
                else:
                    wl_html = ("<div class='sec'><h2>名单外席位候选"
                               "</h2><div class='dim'>当前为空——窗口"
                               "内未发现与注册游资同社区的高频未注册"
                               "席位</div></div>")
                comm_html = grp_head + grp + wl_html
            # ── KG-V4 集团总览层（G6 超节点）+ KG-V1 席位网络画布 ──
            # mapgraph 先跑（Cypher 结果 10min TTL 缓存），overview 复用
            net_html = ""
            mg = KP.api("mapgraph")
            if mg.get("ok"):
                net_html = h._kg_net_html(mg)
            ov_html = ""
            ov = KP.api("overview")
            if ov.get("ok"):
                ov_html = h._kg_overview_html(ov)
            # ── P3-d 交易日事件面（TradeDate 一等节点 × 事件透视）──
            td = (qp.get("td") or [""])[0].strip()
            tc = KP.api("tradecal", td or None, 5)
            if tc.get("ok"):
                _DW = "一二三四五六日"
                trows = "".join(
                    "<tr><td><a href='/kg/map?td=%s'>%s</a></td>"
                    "<td class='dim'>周%s</td><td><b>%d</b></td>"
                    "<td>%s</td></tr>"
                    % (e(tdr["date"]), e(tdr["date"]),
                       _DW[(tdr["dow"] or 1) - 1], tdr["n"],
                       " ".join("<span class='bdg n'>%s %d</span>"
                                % (e(k), v)
                                for k, v in sorted(
                                    tdr["by_kind"].items(),
                                    key=lambda x: -x[1])))
                    for tdr in tc["days"]) or \
                    "<tr><td colspan=4 class='dim'>图内无事件</td></tr>"
                _np = (("<a href='/kg/map?td=%s'>← 前一日</a>"
                        % e(tc["prev"])) if tc["prev"]
                       else "<span class='dim'>← 前一日</span>")
                _nn = (("<a href='/kg/map?td=%s'>后一日 →</a>"
                        % e(tc["next"])) if tc["next"]
                       else "<span class='dim'>后一日 →</span>")
                tc_html = (
                    "<div class='sec'><h2>📅 交易日事件面（截至 %s）"
                    "</h2><table><tr><th>交易日</th><th>周</th>"
                    "<th>事件</th><th>按类</th></tr>%s</table>"
                    "<div class='dim' style='margin-top:4px'>%s · %s · "
                    "<a href='/kg/map'>回最新</a>（交易日历取自图内"
                    "TradeDate 链，事件按 ON_DATE 归日）</div></div>"
                    % (e(tc["date"]), trows, _np, _nn))
            else:
                tc_html = ("<div class='sec'><h2>📅 交易日事件面</h2>"
                           "<div class='dim'>%s</div></div>"
                           % e(tc.get("error", "")))
            # ── P2-③ 空间层地图（ECharts geo：席位城市净买气泡 +
            # 标的注册地省份 choropleth；GeoJSON 本地托管零新库）──
            geo_html = ""
            gj = KP.api("geo", None, days)
            if gj.get("ok"):
                import json as _jsg
                byday = gj.get("byday") or []
                gtmax = max(len(byday) - 1, 0)
                grw = "".join(
                    "<tr><td>%s</td><td>%d</td><td>%d</td>"
                    "<td class='%s'>%+.2f亿</td>"
                    "<td class='%s'>%s <span class='dim' "
                    "style='font-size:11px'>%s</span></td></tr>"
                    % (e(r["city"]), r["n_seat"], r["n"],
                       "up" if r["net"] > 0 else "dn", r["net"] / 1e8,
                       ("up" if (r["wr"] or 0) >= 55
                        else ("dn" if r["wr"] is not None
                              and r["wr"] <= 45 else "")),
                       r["wr_disp"], r["wr_n_disp"])
                    for r in gj["cities"])
                geo_html = (
                    "<div class='sec'><h2>🗺 游资地理分布</h2>"
                    "<div class='dim' style='margin:2px 0 2px'>"
                    "底图色深=标的注册地数量 · 气泡=窗口 %d 天席位城市"
                    "净买（红买绿卖，大小∝|净买|，可滚轮缩放） · 城市"
                    "由营业部名提取，未解析席位 %d 个</div>"
                    "<div style='margin:2px 0 6px'>"
                    "<span id='gcm-net' onclick='_gcm(0)' "
                    "style='display:inline-block;padding:2px 12px;"
                    "border:1px solid var(--gray-4);border-radius:11px;"
                    "cursor:pointer;font-size:12px;background:var(--blu-600);"
                    "color:var(--gray-7)'>净买卖色</span> "
                    "<span id='gcm-wr' onclick='_gcm(1)' "
                    "style='display:inline-block;padding:2px 12px;"
                    "border:1px solid var(--gray-4);border-radius:11px;"
                    "cursor:pointer;font-size:12px;color:var(--gray-7)'>"
                    "胜率着色</span> "
                    "<span id='gcnote' class='dim' "
                    "style='font-size:12px'>胜率 40 绿 → 55 黄 → "
                    "70 红 · 气泡大小仍=|净买| · 灰=该城无胜率席位 · "
                    "排行列=全历史口径，着色随滑杆按窗口活跃席位"
                    "重算</span></div>"
                    "<div style='margin:2px 0 6px'>"
                    "<span id='gtp' onclick='_gp()' "
                    "style='display:inline-block;padding:2px 10px;"
                    "border:1px solid var(--gray-4);border-radius:11px;"
                    "cursor:pointer;font-size:12px;color:var(--gray-7)'>"
                    "▶ 播放</span> "
                    "<input type='range' id='gts' min='0' max='%d' "
                    "value='%d' oninput='_gt()' style='vertical-align:"
                    "middle;width:340px'> "
                    "<span id='gtl' style='font-size:12px;"
                    "color:var(--gray-7)'></span> "
                    "<span class='dim' style='font-size:12px'>· 滑杆="
                    "按交易日累计净买重渲（KG-V3 口径），着色随当前"
                    "模式</span></div>"
                    "<div id='kggeo' style='height:560px'></div>"
                    "<h2 style='margin-top:12px'>城市排行（按|净买|）"
                    "</h2><table><tr><th>城市</th><th>席位数</th>"
                    "<th>上榜笔数</th><th>净买</th><th>历史胜率"
                    "</th></tr>%s</table></div>"
                    "<script src='/static/echarts.min.js'></script>"
                    "<script>(function(){var CT=%s,PV=%s,BD=%s,"
                    "_gmode=0;"
                    "fetch('/static/china_geo.json')"
                    ".then(function(r){return r.json()})"
                    ".then(function(geo){"
                    "if(!window.echarts){var e0=document.getElementById"
                    "('kggeo');e0.innerHTML='<div class=dim>ECharts "
                    "未加载</div>';return}"
                    "echarts.registerMap('china',geo);"
                    "var el=document.getElementById('kggeo');"
                    "var ec=echarts.init(el);"
                    "var mx=1;CT.forEach(function(c)"
                    "{mx=Math.max(mx,Math.abs(c.net))});"
                    "var _gmx=mx;"
                    "ec.setOption({animation:false,"
                    "tooltip:{trigger:'item',formatter:function(p){"
                    "if(p.seriesType==='map')return p.name+'<br>注册"
                    "标的 '+(p.value==null?'—':p.value)+' 家';"
                    "var d=p.data;return d.name+'<br>席位 '+d.ns+"
                    "' 家 · 上榜 '+d.n+' 笔<br>净买 '+(d.net/1e8)"
                    ".toFixed(2)+' 亿<br>历史胜率 '+d.wrDisp+"
                    "(d.wrNDisp&&d.wrNDisp!='—'?'（'+d.wrNDisp+'）'"
                    ":'')+'<br>窗口胜率 '+(d.wr2!=null?d.wr2+'%%'+"
                    "(d.wrn2?'（'+d.wrn2+' 笔）':''):'—')},"
                    "legend:{data:['注册地分布','席位净买'],"
                    "textStyle:{color:'#8b949e'},top:2},"
                    "visualMap:{min:0,max:PV.length?PV[0].n:1,"
                    "left:8,bottom:40,seriesIndex:0,calculable:true,"
                    "text:['多','少'],textStyle:{color:'#8b949e',"
                    "fontSize:10},inRange:{color:['#14202e',"
                    "'#274b6d','#58a6ff']}},"
                    "geo:{map:'china',roam:true,zoom:1.2,"
                    "itemStyle:{areaColor:'#0d1520',"
                    "borderColor:'#30363d'},emphasis:{label:{color:"
                    "'#e6edf3'},itemStyle:{areaColor:'#20344d'}}},"
                    "series:[{name:'注册地分布',type:'map',geoIndex:0,"
                    "data:PV.map(function(p){return{name:p.name,"
                    "value:p.n}})},"
                    "{name:'席位净买',type:'effectScatter',"
                    "coordinateSystem:'geo',zlevel:2,"
                    "rippleEffect:{scale:2.6,brushType:'stroke'},"
                    "symbolSize:function(v){return 8+26*Math.min(1,"
                    "Math.abs(v[2])/_gmx)},"
                    "label:{show:true,position:'right',"
                    "color:'#e6edf3',fontSize:10,formatter:'{b}'},"
                    "data:CT.map(function(c){return{name:c.city,"
                    "value:c.coord.concat([c.net]),ns:c.n_seat,"
                    "n:c.n,net:c.net,wr:c.wr,wrDisp:c.wr_disp,"
                    "wrNDisp:c.wr_n_disp,itemStyle:{color:"
                    "c.net>=0?'#f85149':'#3fb950'}}})}]}} ,true);"
                    "window._gec=ec;"
                    "window.wrColor=function(w){if(w==null)"
                    "return '#8b949e';var t=(w-40)/30;if(t<0)t=0;"
                    "if(t>1)t=1;var a=[63,185,80],b=[210,153,34],"
                    "c2=[248,81,73];if(t<0.5){var k=t*2;"
                    "return 'rgb('+Math.round(a[0]+(b[0]-a[0])*k)+"
                    "','+Math.round(a[1]+(b[1]-a[1])*k)+','+"
                    "Math.round(a[2]+(b[2]-a[2])*k)+')'}"
                    "var k2=(t-0.5)*2;return 'rgb('+"
                    "Math.round(b[0]+(c2[0]-b[0])*k2)+','+"
                    "Math.round(b[1]+(c2[1]-b[1])*k2)+','+"
                    "Math.round(b[2]+(c2[2]-b[2])*k2)+')'};"
                    "window._gdata=function(){var s=document."
                    "getElementById('gts'),l=document."
                    "getElementById('gtl');if(!s||!window._gec)"
                    "return;var t=BD.length?Math.min(+s.value,"
                    "BD.length-1):-1,acc={},i,k,a;for(i=0;i<=t;"
                    "i++){var cd=BD[i].cities;for(k in cd){"
                    "a=acc[k];if(a){a.net+=cd[k].net;a.n+=cd[k].n;"
                    "if(cd[k].wr!=null){a.wr=cd[k].wr;"
                    "a.wrn=cd[k].wrn}}else acc[k]={net:cd[k].net,"
                    "n:cd[k].n,wr:cd[k].wr,wrn:cd[k].wrn}}}"
                    "var m2=1;for(k in acc){a=Math.abs(acc[k].net);"
                    "if(a>m2)m2=a}_gmx=m2;if(l)l.textContent="
                    "BD.length?('截至 '+BD[t].d+' 累计'):'';"
                    "window._gec.setOption({series:[{},{data:"
                    "CT.map(function(c){a=acc[c.city];var net=a?"
                    "a.net:0;return{name:c.city,value:c.coord."
                    "concat([net]),ns:c.n_seat,n:a?a.n:0,net:net,"
                    "wr:c.wr,wrDisp:c.wr_disp,wrNDisp:c.wr_n_disp,"
                    "wr2:(a&&a.wr!=null)?a.wr:null,"
                    "wrn2:(a&&a.wrn)?a.wrn:0,"
                    "itemStyle:{color:_gmode?wrColor("
                    "(a&&a.wr!=null)?a.wr:null):"
                    "(net>=0?'#f85149':'#3fb950')}}})}]})};"
                    "window._gcm=function(m){var bN=document."
                    "getElementById('gcm-net'),bW=document."
                    "getElementById('gcm-wr');if(m){bW.style."
                    "background='#1f6feb';bN.style.background=''}"
                    "else{bN.style.background='#1f6feb';bW.style."
                    "background=''}window._gdata()};"
                    "window._gt=function(){window._gdata()};"
                    "var _gpt=null;window._gp=function(){var b="
                    "document.getElementById('gtp');if(_gpt){"
                    "clearInterval(_gpt);_gpt=null;b.textContent="
                    "'▶ 播放';return}var s=document."
                    "getElementById('gts');if(!s||!BD.length)"
                    "return;b.textContent='⏸ 暂停';_gpt=setInterval("
                    "function(){if(+s.value>=+s.max){clearInterval("
                    "_gpt);_gpt=null;b.textContent='▶ 播放';return}"
                    "s.value=(+s.value)+1;window._gdata()},350)};"
                    "window._gdata();"
                    "window.addEventListener('resize',function()"
                    "{ec.resize()})})"
                    ".catch(function(){var el=document.getElementById"
                    "('kggeo');if(el)el.innerHTML='<div class=dim>"
                    "地图边界数据加载失败</div>'});})()</script>"
                    % (days, gj.get("unresolved") or 0,
                       gtmax, gtmax, grw,
                       _jsg.dumps(gj["cities"], ensure_ascii=False),
                       _jsg.dumps(gj["provs"], ensure_ascii=False),
                       _jsg.dumps(byday, ensure_ascii=False)))
            else:
                geo_html = ("<div class='sec'><h2>🗺 游资地理分布</h2>"
                            "<div class='dim'>%s</div></div>"
                            % e(gj.get("error", "")))
            content = (
                "<script>var _t=document.querySelector('.tabs');"
                "if(_t){_t.style.pointerEvents='';_t.style.opacity='';}"
                "var _l=document.getElementById('kgmap-loading');"
                "if(_l&&_l.parentNode)_l.parentNode.removeChild(_l);"
                "</script>"
                "<div class='tabc on' id='kgt-ov'>%s%s%s</div>"
                "<div class='tabc' id='kgt-net'>%s%s%s</div>"
                "<div class='tabc' id='kgt-geo'>%s</div>"
                "<div class='tabc' id='kgt-det'>"
                "<div class='duo'>%s<div>%s</div></div></div></div>"
                % (ov_html, comm_html, tc_html,
                   net_html, pair_html, alt_html, geo_html,
                   roster, flow_html))

    if streamed:
        # U4-A 流式收尾：骨架已在响应头阶段发出，这里只续发正文
        h._html_more(content + _BTSEL_TAG + "</body></html>")
    else:
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>" + e(title) + "</title>"
                + h._COCKPIT_CSS + h._KG_CSS + "</head><body>"
                + pill_nav("kgmap") + content + "</body></html>")
        h._html(body)


def _kg_company_page(h, qs):
    """KG-O 公司本体全景页：本体论视角看一家公司的全貌——Company
    为中心，六扇区（股东/高管/业务产品/行业归属含同业/游资/盘面
    事件）中心辐射点线边（G6 v5 本地托管 /static/g6.min.js）+
    四直方图（ECharts：实体类型分布/按日事件计数/游资净买/结构
    conf 分布）+ 关系统计表（主端/关系/对端/强度/次数，点行高亮
    该关系类型的边——理念移植自 知识图谱/_study/studio 千江图谱
    工作室 StatisticsPanel）。数据层 kg_portal.company_360。"""
    import html as _h
    import json as _json
    from urllib.parse import parse_qs as _pq, quote as _qt
    import sys as _sy
    if QBT_DIR not in _sy.path:
        _sy.path.insert(0, QBT_DIR)
    import kg_portal as KP
    e = _h.escape
    qp = _pq(qs)
    code = (qp.get("c") or [""])[0].strip()
    days = max(30, min(365, int((qp.get("d") or ["90"])[0] or 90)))
    per = (qp.get("p") or [""])[0].strip()

    def _fmt(v, suf=""):
        if v is None:
            return "—"
        try:
            fv = float(v)
            return ("%s%s" % (_fmtn(fv), suf)) if fv == fv else "—"
        except (TypeError, ValueError):
            return e(str(v))

    def _fmtn(f):
        return ("%d" % f) if f == int(f) else ("%.2f" % f)

    # 无代码 / 未找到：表单页
    if not code:
        body = ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset="
                "utf-8'><title>公司本体全景 · 图谱探索</title>"
                + h._COCKPIT_CSS + h._KG_CSS + "</head><body>"
                + pill_nav("kg")
                + "<div class='sec'><h2>🏢 公司本体全景</h2>"
                  "<div class='dim' style='margin:4px 0 10px'>以公司"
                  "为本体中心（本体论/三元组/点线边），股东 · 高管 · "
                  "业务产品 · 行业同业 · 游资 · 盘面事件六扇区辐射 + "
                  "实体直方图落地。</div>"
                  "<form action='/kg/company' method='get' style="
                  "'display:flex;gap:8px'><input class='kfind' name='c' "
                  "placeholder='ts_code，如 600519.SH 或 002743.SZ'>"
                  "<button class='kfbtn'>🏢 打开全景</button></form>"
                + "</body></html>")
        h._html(body)
        return
    d = KP.api("company360", code, days, per=per or None)
    if not d.get("ok"):
        body = ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset="
                "utf-8'><title>公司本体全景 · 图谱探索</title>"
                + h._COCKPIT_CSS + h._KG_CSS + "</head><body>"
                + pill_nav("kg")
                + "<div class='sec'><h2>🏢 公司本体全景</h2>"
                  "<div class='dn'>" + e(d.get("error", "查询失败"))
                  + "</div>"
                  "<form action='/kg/company' method='get' style="
                  "'display:flex;gap:8px;margin-top:10px'>"
                  "<input class='kfind' name='c' placeholder="
                  "'ts_code，如 600519.SH'>"
                  "<button class='kfbtn'>🏢 打开全景</button></form>"
                  "</div></body></html>")
        h._html(body)
        return

    b = d["basic"]
    payload = _json.dumps(d, ensure_ascii=False, separators=(",", ":"))
    title = "%s 公司本体全景" % (b.get("name") or code)
    pills = "".join(
        "<span style='%s'>%d天</span>" % (_PILL_ON, dd) if dd == days
        else "<a href='/kg/company?c=%s&d=%d' style='%s'>%d天</a>"
        % (e(code), dd, _PILL, dd) for dd in (90, 180, 365))
    hp = d.get("hist", {}).get("hold_periods") or []
    if len(hp) > 1:
        per0 = d.get("per") or ""
        pl = lambda pp: (pp[:4], pp[4:6]) if len(pp) == 8 else (
            pp[:4], pp[5:7])
        pills += ("<span class='dim' style='margin-left:6px'>股东报告期"
                  "：</span>" + "".join(
            "<span style='%s'>%s-%s</span>" % (_PILL_ON, *pl(pp))
            if pp == per0 else
            "<a href='/kg/company?c=%s&d=%d&p=%s' style='%s'>%s-%s</a>"
            % ((e(code), days, e(pp), _PILL) + pl(pp)) for pp in hp))
    chips = "".join(
        "<span class='bdg%s'>%s</span>" % (cls, txt) for cls, txt in (
            ("", "行业 %s" % e(b.get("industry") or "—")),
            ("", "申万 %s" % e(b.get("sw_l1") or "—")),
            ("", "区域 %s" % e(b.get("area") or "—")),
            ("", "上市 %s" % e(b.get("list_date") or "—")),
            (" y", "董事长 %s" % e(b.get("chairman") or "—")),
            ("", "总经理 %s" % e(b.get("manager") or "—")),
            ("", "员工 %s" % _fmt(b.get("employees"))),
            ("", "注册资本 %s 万" % _fmt(b.get("reg_capital")))))
    len(d.get("hist", {}).get("day_events") or [])
    head = (
        "<div class='sec'><h2>🏢 公司本体全景 · %s <span class='dim' "
        "style='font-weight:400;font-size:13px'>%s</span></h2>"
        "<div style='margin:4px 0 6px'>%s</div>"
        "<div class='dim' style='margin:2px 0 6px'>⇄ "
        "<a href='/kg?c=%s'>图谱视角 →</a> · "
        "<a href='/kg/stock/%s'>量化全景（K线/时间轴/AI）→</a> · "
        "窗口 %d 天 · 三元组 %d 组（点%d 边%d）</div>"
        "<div style='display:flex;gap:14px;align-items:center;"
        "flex-wrap:wrap'><div>%s</div>"
        "<form action='/kg/company' method='get' style='display:flex;"
        "gap:6px;margin:0'><input class='kfind' name='c' value='%s' "
        "style='min-width:140px;width:180px' placeholder='ts_code'>"
        "<button class='kfbtn'>切换</button></form></div></div>\n"
        "<div class='sec'><div id='c360lg' style='margin:2px 0 6px;"
        "display:flex;gap:8px;flex-wrap:wrap;align-items:center'>"
        "</div><div style='display:flex;gap:10px;align-items:stretch'>"
        "<div id='c360g' style='flex:1.7;min-width:0;height:620px;"
        "border:1px solid var(--gray-4);border-radius:8px;background:"
        "var(--gray-0)'></div>"
        "<div id='c360d' style='flex:1;min-width:260px;max-width:340px;"
        "border:1px solid var(--gray-4);border-radius:8px;background:var(--gray-0);"
        "padding:10px 12px;overflow:auto'></div></div>"
        "<div id='c360tip' style='display:none;position:fixed;z-index:"
        "98;background:var(--gray-2);border:1px solid var(--gray-4);border-radius:"
        "8px;padding:8px 10px;font-size:12px;color:var(--gray-7);pointer-"
        "events:none;max-width:340px;box-shadow:0 4px 14px rgba(0,0,"
        "0,.5)'></div></div>\n"
        "<div class='sec'><h2>📊 实体直方图（数据落地）</h2>"
        "<div class='dim' style='margin:2px 0 6px'>左上：本体六扇区"
        "实体类型分布 · 右上：按日盘面事件计数 · 左下：游资净买分布"
        "（红=净买 / 绿=净卖）· 右下：结构信号置信度分布<br>"
        "点击柱子=反筛图谱（命中实体保留、其余压暗，再点同柱取消；"
        "可与关系筛选叠加）</div>"
        "<div style='display:grid;grid-template-columns:1fr 1fr;gap:"
        "10px'><div id='h_type' style='height:230px;border:1px solid "
        "var(--gray-4);border-radius:8px;background:var(--gray-0)'></div>"
        "<div id='h_day' style='height:230px;border:1px solid var(--gray-4);"
        "border-radius:8px;background:var(--gray-0)'></div>"
        "<div id='h_hm' style='height:230px;border:1px solid var(--gray-4);"
        "border-radius:8px;background:var(--gray-0)'></div>"
        "<div id='h_conf' style='height:230px;border:1px solid var(--gray-4);"
        "border-radius:8px;background:var(--gray-0)'></div></div></div>\n"
        "<div class='sec'><h2>🔗 关系统计（主端 · 关系 · 对端 · 强度"
        " · 次数）</h2><div class='dim' style='margin:2px 0 6px'>对标 "
        "graph-studio StatisticsPanel——点行=高亮该关系类型的边并压暗"
        "无关节点（再点还原）；扇区图例点按=隐藏/显示整扇区</div>"
        "<table class='sticky' id='c360rt'><tr><th>主端</th><th>关系"
        "</th><th>对端</th><th>强度</th><th>次数</th><th>备注</th>"
        "</tr></table></div>\n"
        "<div class='sec'><h2>📝 公司底档</h2><div class='dim' style="
        "'line-height:1.8'><b style='color:var(--gray-7)'>%s</b><br>主营："
        "%s<br>%s</div></div>\n"
        "<script src='/static/g6.min.js'></script>\n"
        "<script src='/static/echarts.min.js'></script>\n<script>\n")
    head = head % (
        e(b.get("name") or code), e(code), chips, e(code),
        _qt(b.get("name") or code), days, len(d["rel_stats"]),
        len(d["nodes"]), len(d["edges"]), pills, e(code),
        e(b.get("company") or "—"),
        e(b.get("main_business") or "—"),
        e(b.get("introduction") or "—"))
    tail = r'''(function(){
var D=__P__;
var SEC={security:{n:'标的',c:'#58a6ff'},holder:{n:'股东',c:'#d29922'},
  person:{n:'高管',c:'#a371f7'},product:{n:'业务/产品',c:'#3fb950'},
  taxonomy:{n:'行业/同业/概念',c:'#79c0ff'},hotmoney:{n:'游资',c:'#f778ba'},
  event:{n:'盘面事件',c:'#ffa657'}};
var ORDER=['security','holder','person','product','taxonomy','hotmoney',
  'event'];
var CUT={holder:10,person:6,product:8,taxonomy:9,hotmoney:9,event:13,
  security:22};
var CO=null,hidden={},RELSEL=null,RELROW=-1,SELID=null,HSEL=null,
  HKEY=null,HLAB='',graph=null;
D.nodes.forEach(function(n){if(n.sector==='company')CO=n;});
var mw={};
D.nodes.forEach(function(n){if(n.sector==='company')return;
  mw[n.sector]=Math.max(mw[n.sector]||1,n.w||1);});
var mew=1;
D.edges.forEach(function(x){if((x.w||1)>mew)mew=x.w||1;});
var secOf={};
D.nodes.forEach(function(n){secOf[n.id]=n.sector;});
function cut(s,n){s=String(s==null?'':s);return s.length>n?s.slice(0,n)+'…':s;}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,
  function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function sizeOf(n){if(n.sector==='company')return 56;
  if(n.sector==='security')return 34;
  return Math.round(13+22*Math.sqrt((n.w||1)/(mw[n.sector]||1)));}
function edgeColor(x){var st=secOf[x.s]||'',tt=secOf[x.t]||'';
  if(x.rel==='OF_COMPANY')return '#e6edf3';
  if(x.rel==='DRIVE')return '#8b949e';
  return (SEC[tt]&&SEC[tt].c)||'#58a6ff';}
// ── 中心辐射·扇区分组定位（确定性极坐标，无布局引擎）──
function buildPos(){
  var box=document.getElementById('c360g');
  var W=box.clientWidth||900,H=box.clientHeight||620;
  var cx=W/2,cy=H/2,R=Math.min(W,H)/2-28;
  var rings=[[0.36,5],[0.56,7],[0.78,10]];
  var byS={},pos={};
  D.nodes.forEach(function(n){if(n.sector==='company')return;
(byS[n.sector]=byS[n.sector]||[]).push(n);});
  var live=ORDER.filter(function(s){return byS[s]&&byS[s].length;});
  var NA=live.length||1,a0=-Math.PI/2,span=2*Math.PI/NA,gap=span*0.14;
  live.forEach(function(s,ai){
var arr=byS[s],ac=a0+span*ai+span/2,idx=0;
rings.forEach(function(rg){
  var take=arr.slice(idx,idx+rg[1]);idx+=rg[1];
  var m=take.length;if(!m)return;
  var half=Math.max(span-gap,0.25)/2;
  take.forEach(function(n,i){
    var t=(m===1)?0.5:i/(m-1);
    var ang=ac-half+t*half*2;
    pos[n.id]=[cx+Math.cos(ang)*R*rg[0],cy+Math.sin(ang)*R*rg[0]];
  });});});
  pos[CO.id]=[cx,cy];
  return pos;}
function relOK(x){return !hidden[secOf[x.s]]&&!hidden[secOf[x.t]];}
function nodesBuild(pos){
  var ns=[];
  D.nodes.forEach(function(n){
if(hidden[n.sector]&&n.sector!=='company')return;
var p=pos[n.id];if(!p)return;
var dimmed=(RELSEL&&!hitRel(n.id))||(HSEL&&!hitH(n.id))||(SELID
  &&SELID!==n.id&&!adjOf(SELID,n.id));
var isCo=n.sector==='company';
ns.push({id:n.id,type:(n.kind==='src'?'rect':'circle'),style:{
  x:p[0],y:p[1],size:sizeOf(n),
  fill:(SEC[n.sector]&&SEC[n.sector].c)||'#21262d',
  fillOpacity:isCo?0.95:(dimmed?0.12:(n.past?0.38:
    (n.kind==='src'?0.55:0.85))),
  stroke:SELID===n.id?'#e6edf3':(isCo?'#58a6ff':'#30363d'),
  lineWidth:SELID===n.id?3:(isCo?2.5:1.5),
  lineDash:n.past?[3,3]:null,
  labelText:cut(n.label,CUT[n.sector]||10),
  labelFill:isCo?'#e6edf3':'#8b949e',
  labelFontSize:isCo?13:10,labelPlacement:'bottom',
  labelWordSpacing:1}});});
  return ns;}
function edgesBuild(){
  var es=[];
  D.edges.forEach(function(x,i){
if(!relOK(x))return;
var on=(!RELSEL||x.rel===RELSEL)&&(!HSEL||(HSEL[x.s]||HSEL[x.t]));
es.push({id:'L'+i,source:x.s,target:x.t,style:{
  stroke:on?edgeColor(x):'#30363d',
  strokeOpacity:on?(x.rel==='DRIVE'?0.55:0.5):0.05,
  lineWidth:(on?1:0.5)+(on?4*Math.sqrt((x.w||1)/mew):0),
  lineDash:(x.rel==='DRIVE')?[4,3]:null}});});
  return es;}
function redraw(){if(!graph)return;
  graph.setData({nodes:nodesBuild(buildPos()),edges:edgesBuild()});
  graph.render();}
// ── 邻接与关系命中（选中/筛选联动）──
var adj={},byRel={};
D.edges.forEach(function(x){
  (adj[x.s]=adj[x.s]||{})[x.t]=1;(adj[x.t]=adj[x.t]||{})[x.s]=1;
  (byRel[x.rel]=byRel[x.rel]||{})[x.s]=1;(byRel[x.rel]=byRel[x.rel]||{})[x.t]=1;});
function adjOf(a,b){return !a||!b||(adj[a]&&adj[a][b]);}
function hitRel(id){return !RELSEL||(byRel[RELSEL]&&byRel[RELSEL][id]);}
function hitH(id){return !HSEL||id===CO.id||!!HSEL[id];}
// ── 图例（扇区开关）──
function legend(){
  var cnt={};
  D.nodes.forEach(function(n){cnt[n.sector]=(cnt[n.sector]||0)+1;});
  var h=ORDER.filter(function(s){return cnt[s];}).map(function(s){
var off=hidden[s];
return '<button onclick="window._c3sec(\''+s+'\')" style="cursor:'
  +'pointer;background:var(--gray-3);border:1px solid var(--gray-4);border-radius:'
  +'16px;font-size:12px;padding:3px 10px;color:'+(off?'#484f58'
  :(SEC[s].c))+';text-decoration:'+(off?'line-through':'none')+'">'
  +'<span style="display:inline-block;width:9px;height:9px;border-'
  +'radius:50%;background:'+(SEC[s].c)+';opacity:'+(off?0.25:1)
  +';margin-right:5px;vertical-align:-1px"></span>'+SEC[s].n+' '
  +cnt[s]+'</button>';}).join('');
  var h2='<span class="dim" style="font-size:12px">中心=公司本体 · 节点'
+'大小∝扇区内权重 · 边色=对端扇区 · 虚线=事件来源（DRIVE）'
+' · 高管虚线圆=历任</span>';
  if(RELSEL)h2+=' <button onclick="window._c3rel(\'\')" style="cursor:'
+'pointer;background:var(--gray-3);border:1px solid var(--red-500);border-radius:'
+'16px;font-size:12px;padding:3px 10px;color:var(--red-500)">✕ 清除关系筛选: '
+esc(RELSEL)+'</button>';
  if(HSEL)h2+=' <button onclick="window._c3hs()" style="cursor:pointer;'
+'background:var(--gray-3);border:1px solid var(--ylw-500);border-radius:16px;'
+'font-size:12px;padding:3px 10px;color:var(--ylw-500)">✕ 清除直方图筛选: '
+esc(HLAB)+'</button>';
  document.getElementById('c360lg').innerHTML=h+' '+h2;}
// ── 详情面板 ──
function hrefsOf(n){
  var out=[];
  if(n.sector==='security')
out.push(['图谱视角','/kg?c='+encodeURIComponent(D.code)],
         ['量化全景','/kg/stock/'+encodeURIComponent(
           D.basic.name||D.code)]);
  if(n.sector==='hotmoney')
out.push(['游资画像','/kg/map?hm='+encodeURIComponent(n.label)]);
  if(n.props&&n.props['同业标的']){
var nm=String(n.props['同业标的']).split(' ').slice(1).join(' ');
if(nm)out.push(['量化全景','/kg/stock/'+encodeURIComponent(nm)]);}
  if(n.sector==='taxonomy'&&/^(sh|sz|bj)/.test(String(n.id)))
out.push(['图谱视角','/kg?c='+encodeURIComponent(n.id)]);
  return out;}
function detailOf(n){
  var rows='';
  if(n.props)Object.keys(n.props).forEach(function(k){
var v=n.props[k];if(v==null||v==='')return;
rows+='<tr><td class="dim" style="white-space:nowrap">'+esc(k)
  +'</td><td>'+esc(String(v).slice(0,80))+'</td></tr>';});
  var lk=hrefsOf(n).map(function(x){
return '<a href="'+x[1]+'" style="display:inline-block;margin:4px 8px '
  +'0 0;font-size:12px;color:var(--blu-500)">'+x[0]+' →</a>';}).join('');
  return '<div style="font-weight:600;font-size:14px;margin:2px 0 6px">'
+'<span style="display:inline-block;width:11px;height:11px;border-'
+'radius:50%;background:'+((SEC[n.sector]||{}).c||'var(--gray-5)')
+';margin-right:6px"></span>'+esc(n.label)+'</div>'
+'<div class="dim" style="font-size:12px;margin-bottom:6px">扇区：'
+((SEC[n.sector]||{}).n||n.sector)+(n.kind==='src'?'（事件来源）':'')
+'</div><table class="sticky">'+(rows
  ||'<tr><td class="dim">无附加属性</td></tr>')+'</table>'+lk;}
function detailDefault(){
  var cnt={};
  D.nodes.forEach(function(n){if(n.sector!=='company')
cnt[n.sector]=(cnt[n.sector]||0)+1;});
  var t=ORDER.filter(function(s){return cnt[s];}).map(function(s){
return '<tr><td><span style="display:inline-block;width:8px;height:8px;'
  +'border-radius:50%;background:'+SEC[s].c+';margin-right:5px">'
  +'</span>'+SEC[s].n+'</td><td>'+cnt[s]+'</td></tr>';}).join('');
  document.getElementById('c360d').innerHTML=
'<div style="font-weight:600;font-size:13px;margin:2px 0 6px">'
+esc(D.basic.company||D.basic.name||D.code)+' · 全景摘要</div>'
+'<div class="dim" style="font-size:12px;line-height:1.7">节点 '
+D.nodes.length+' · 边 '+D.edges.length+' · 窗口 '
+D.window_days+' 天<br>单击节点=详情+跳转 · 点图例=隐藏扇区<br>'
+'点关系统计行=高亮该关系 · 点直方图柱=反筛</div>'
+'<table class="sticky" style="margin-top:6px"><tr><th>扇区</th>'
+'<th>实体</th></tr>'+t+'</table>'
+'<div class="dim" style="font-size:12px;margin-top:8px;line-height:1.7">'
+esc(String(D.basic.introduction||D.basic.main_business||'').slice(0,180))
+(String(D.basic.introduction||D.basic.main_business||'').length>180
  ?'…':'')+'</div>';}
// ── 关系统计表 ──
function relTable(){
  var tb=document.getElementById('c360rt');
  tb.querySelectorAll('tr').forEach(function(tr,i){if(i)tr.remove();});
  D.rel_stats.forEach(function(r,i){
var tr=document.createElement('tr');
tr.style.cursor='pointer';
tr.innerHTML='<td>'+esc(r.src)+'</td><td><span style="font-family:'
  +'monospace;color:var(--blu-500)">'+esc(r.rel)+'</span></td><td>'+esc(r.dst)
  +'</td><td class="bdg n">'+esc(r.strength)+'</td><td>'+r.count
  +'</td><td class="dim">'+esc(r.note||'')+'</td>';
tr.onclick=function(){window._c3rel(r.rel,i);};
if(i===RELROW)tr.style.background='#161b22';
tb.appendChild(tr);});}
// ── 交互入口 ──
window._c3sec=function(s){hidden[s]=!hidden[s];legend();redraw();};
window._c3rel=function(rel,i){
  if(!rel||RELSEL===rel){RELSEL=null;RELROW=-1;}
  else{RELSEL=rel;RELROW=(i==null?-1:i);}
  legend();relTable();redraw();};
window._c3hs=function(){HSEL=null;HKEY=null;HLAB='';hsPaint();legend();
  redraw();};
// ── G6 画布 ──
var tip=document.getElementById('c360tip');
function tipShow(html,x,y){tip.innerHTML=html;tip.style.display='block';
  tip.style.left=Math.min(x+14,innerWidth-360)+'px';
  tip.style.top=Math.max(8,y-14)+'px';}
try{
  graph=new G6.Graph({container:'c360g',autoFit:'view',animation:false,
behaviors:['drag-canvas','zoom-canvas','drag-element']});
  graph.setData({nodes:nodesBuild(buildPos()),edges:edgesBuild()});
  graph.on('node:click',function(ev){var id=ev.itemId||ev.target.id;
if(!id)return;
var n=null;D.nodes.forEach(function(x){if(x.id===id)n=x;});
if(!n)return;
LASTC=Date.now();
SELID=(SELID===id)?null:id;
if(SELID)document.getElementById('c360d').innerHTML=detailOf(n);
else detailDefault();
redraw();});
  graph.on('node:mouseenter',function(ev){var id=ev.itemId||ev.target.id;
var n=null;D.nodes.forEach(function(x){if(x.id===id)n=x;});
if(!n)return;
var h='<b style="font-size:13px">'+esc(n.label)+'</b><br>'
  +'<span style="color:var(--gray-5)">'+((SEC[n.sector]||{}).n||n.sector)
  +(n.kind==='src'?'（事件来源）':'')+'</span>';
if(n.props)Object.keys(n.props).slice(0,5).forEach(function(k){
  var v=n.props[k];if(v==null||v==='')return;
  h+='<br>'+esc(k)+'：'+esc(String(v).slice(0,44));});
var xy=ev.client||{x:ev.clientX,y:ev.clientY};
tipShow(h,xy.x,xy.y);});
  graph.on('node:mousemove',function(ev){
var x=ev.client?ev.client.x:ev.clientX,y=ev.client?ev.client.y:ev.clientY;
tip.style.left=Math.min(x+14,innerWidth-360)+'px';
tip.style.top=Math.max(8,y-40)+'px';});
  graph.on('node:mouseleave',function(){tip.style.display='none';});
  graph.render();
  // ── hover/click 兜底：G6 事件桥只认事件层 canvas 上的 PointerEvent
  // （pointermove 无包装直 tap 且 copyMouseData 期望 federated 属性、
  // pointerdown/up 绑 domElement），CDP/自动化与部分环境只发原生
  // MouseEvent 走不通 → 容器级原生监听 + getCanvasByClient 几何命中
  // 补位；与 G6 真实交互幂等共存（LASTC 60ms 去重 + 拖拽位移抑制）──
  try{
var gbox=document.getElementById('c360g');
var HOVN=null,HOVH='',LASTC=0,PD=null;
function hitNode(e){
  var p=null;
  try{var a=graph.getCanvasByClient([e.clientX,e.clientY]);
    p={x:(a&&a.x!=null)?a.x:a[0],y:(a&&a.y!=null)?a.y:a[1]};}
  catch(_){p=null;}
  if(!p)return null;
  var pos=buildPos(),hit=null;
  D.nodes.forEach(function(x){
    if(hidden[x.sector]&&x.sector!=='company')return;
    var q=pos[x.id];if(!q)return;
    var m=sizeOf(x)/2+8,dx=p.x-q[0],dy=p.y-q[1];
    if(dx*dx+dy*dy<=m*m)hit=x;});
  return hit;}
gbox.addEventListener('pointermove',function(e){
  var hit=hitNode(e);
  if(!hit){
    if(HOVN){HOVN=null;tip.style.display='none';}
    return;}
  if(HOVN!==hit.id){
    var h='<b style="font-size:13px">'+esc(hit.label)+'</b><br>'
      +'<span style="color:var(--gray-5)">'
      +((SEC[hit.sector]||{}).n||hit.sector)
      +(hit.kind==='src'?'（事件来源）':'')+'</span>';
    if(hit.props)Object.keys(hit.props).slice(0,5).forEach(function(k){
      var v=hit.props[k];if(v==null||v==='')return;
      h+='<br>'+esc(k)+'：'+esc(String(v).slice(0,44));});
    HOVN=hit.id;HOVH=h;}
  tipShow(HOVH,e.clientX,e.clientY);},false);
gbox.addEventListener('pointerleave',function(){
  if(HOVN){HOVN=null;tip.style.display='none';}},false);
// click 兜底：无 PointerEvent 环境的原生 click 复刻 node:click；
// PD 位移>8 抑制拖画布/拖节点松手误触
gbox.addEventListener('pointerdown',function(e){
  PD={x:e.clientX,y:e.clientY};},false);
gbox.addEventListener('click',function(e){
  var ok=(Date.now()-LASTC>=60);
  if(ok&&PD&&(Math.abs(e.clientX-PD.x)+Math.abs(e.clientY-PD.y))>8)
    ok=false;
  PD=null;
  if(!ok)return;
  var n=hitNode(e);if(!n)return;
  LASTC=Date.now();
  SELID=(SELID===n.id)?null:n.id;
  if(SELID)document.getElementById('c360d').innerHTML=detailOf(n);
  else detailDefault();
  redraw();},false);
  }catch(e2){}
}catch(err){
  document.getElementById('c360g').innerHTML='<div style="color:var(--red-500);'
+'padding:20px;font-size:13px">G6 画布初始化失败：'+esc(err.message)
+'</div>';}
legend();relTable();detailDefault();
// ── ECharts 四直方图（点击柱=反筛图谱：命中实体保留、其余压暗，
// 再点同柱取消；选中柱加白描边；与关系筛选/节点选中叠加）──
var AX={axisLine:{lineStyle:{color:'#30363d'}},axisLabel:{color:'#8b949e',
  fontSize:11},splitLine:{lineStyle:{color:'#21262d'}}};
var CH={},HB={};window._c3CH=CH;
function mk(id,builder){try{var c=echarts.init(document.getElementById(id));
  CH[id]=c;c.setOption(builder(-1));}catch(e){
  document.getElementById(id).innerHTML='<div style="color:var(--gray-5);'
+'padding:20px;font-size:12px">图表初始化失败</div>';}}
function hsel(key,set,lab){
  if(HKEY===key){HKEY=null;HSEL=null;HLAB='';}
  else{HKEY=key;HSEL=set;HLAB=lab;}
  hsPaint();legend();redraw();}
function hsPaint(){['h_type','h_day','h_hm','h_conf'].forEach(function(id){
  if(!CH[id]||!HB[id])return;var sel=-1;
  if(HKEY&&HKEY.slice(0,id.length)===id&&HKEY.charAt(id.length)===':')
sel=parseInt(HKEY.slice(id.length+1),10);
  try{CH[id].setOption(HB[id](sel));}catch(_){}});}
var TCD=(D.hist.type_counts||[]).slice().reverse();
HB.h_type=function(sel){return {backgroundColor:'transparent',
  grid:{left:8,right:16,top:28,bottom:8,containLabel:true},
  tooltip:{trigger:'axis'},
  title:{text:'实体类型分布（六扇区）',textStyle:{color:'#8b949e',
  fontSize:12},left:8,top:4},
  xAxis:Object.assign({type:'value',minInterval:1},AX),
  yAxis:Object.assign({type:'category',data:TCD.map(function(x){
return (SEC[x[0]]||{}).n||x[0];})},AX),
  series:[{type:'bar',barMaxWidth:22,
data:TCD.map(function(x,i){return {value:x[1],itemStyle:{
  color:(SEC[x[0]]||{}).c||'#58a6ff',borderColor:'#e6edf3',
  borderWidth:i===sel?2:0}};})}]};};
mk('h_type',HB.h_type);
CH.h_type.on('click',function(p){var i=p.dataIndex,x=TCD[i];if(!x)return;
  var set={};D.nodes.forEach(function(n){if(n.sector===x[0])set[n.id]=1;});
  hsel('h_type:'+i,set,((SEC[x[0]]||{}).n||x[0])+' '+x[1]);});
var DAYE=D.hist.day_events||[];
HB.h_day=function(sel){return {backgroundColor:'transparent',
  grid:{left:8,right:16,top:28,bottom:8,containLabel:true},
  tooltip:{trigger:'axis'},
  title:{text:'按日盘面事件计数（'+D.window_days+'天 · '
+DAYE.length+' 个事件日）',textStyle:{color:'#8b949e',fontSize:12},
left:8,top:4},
  xAxis:Object.assign({type:'category',data:DAYE.map(function(x){
return x[0].slice(5);})},AX),
  yAxis:Object.assign({type:'value',minInterval:1},AX),
  series:[{type:'bar',barMaxWidth:26,
data:DAYE.map(function(x,i){return {value:x[1],itemStyle:{color:
  '#ffa657',borderColor:'#e6edf3',borderWidth:i===sel?2:0}};})}]};};
mk('h_day',HB.h_day);
CH.h_day.on('click',function(p){var i=p.dataIndex,x=DAYE[i];if(!x)return;
  var set={};D.nodes.forEach(function(n){
if(n.props&&n.props['日期']===x[0])set[n.id]=1;});
  hsel('h_day:'+i,set,'事件日 '+x[0].slice(5)+' · '+x[1]+'条');});
var HMN=D.hist.hm_net||[];
HB.h_hm=function(sel){return {backgroundColor:'transparent',
  grid:{left:8,right:16,top:28,bottom:8,containLabel:true},
  tooltip:{trigger:'axis',
  valueFormatter:function(v){return v+' 百万';}},
  title:{text:'游资净买分布（百万 · 红=净买/绿=净卖）',textStyle:{color:
'#8b949e',fontSize:12},left:8,top:4},
  xAxis:Object.assign({type:'category',data:HMN.map(function(x){
return x.k;})},AX),
  yAxis:Object.assign({type:'value'},AX),
  series:[{type:'bar',barMaxWidth:26,
data:HMN.map(function(x,i){return {value:x.net,itemStyle:{color:
  x.net>=0?'#f85149':'#3fb950',borderColor:'#e6edf3',
  borderWidth:i===sel?2:0}};})}]};};
mk('h_hm',HB.h_hm);
CH.h_hm.on('click',function(p){var i=p.dataIndex,x=HMN[i];if(!x)return;
  var set={};D.nodes.forEach(function(n){
if(n.sector==='hotmoney'&&n.label===x.k)set[n.id]=1;});
  hsel('h_hm:'+i,set,'游资 '+x.k);});
var CFB=D.hist.conf_bins||[];
var CB_LO=[0,0.2,0.4,0.6,0.8],CB_HI=[0.2,0.4,0.6,0.8,1.01];
HB.h_conf=function(sel){return {backgroundColor:'transparent',
  grid:{left:8,right:16,top:28,bottom:8,containLabel:true},
  tooltip:{trigger:'axis'},
  title:{text:'结构信号置信度分布（n='+D.hist.conf_n+'）',
  textStyle:{color:'#8b949e',fontSize:12},left:8,top:4},
  xAxis:Object.assign({type:'category',data:CFB.map(function(x){
return x.label;})},AX),
  yAxis:Object.assign({type:'value',minInterval:1},AX),
  series:[{type:'bar',barMaxWidth:42,
data:CFB.map(function(x,i){return {value:x.n,itemStyle:{color:[
  '#1f6feb','#388bfd','#58a6ff','#79c0ff','#a5d6ff'][i]||'#58a6ff',
  borderColor:'#e6edf3',borderWidth:i===sel?2:0}};})}]};};
mk('h_conf',HB.h_conf);
CH.h_conf.on('click',function(p){var i=p.dataIndex,x=CFB[i];if(!x)return;
  var lo=(CB_LO[i]==null?0:CB_LO[i]),hi=(CB_HI[i]==null?1.01:CB_HI[i]);
  var set={};D.nodes.forEach(function(n){
var c=n.props&&n.props['置信度'];
if(typeof c==='number'&&c>=lo&&c<hi)set[n.id]=1;});
  hsel('h_conf:'+i,set,'conf '+x.label);});
})();'''
    tail = tail.replace("__P__", payload)
    body = ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset="
            "utf-8'><meta name='viewport' content='width=device-width,"
            "initial-scale=1'><title>" + e(title) + " · 图谱探索"
            "</title>" + h._COCKPIT_CSS + h._KG_CSS + "</head>"
            "<body>" + pill_nav("kg") + head + tail + "\n</script>\n"
            "</body></html>")
    h._html(body)


def _kg_page(h, qs):
    """M3-W2 情报探索页：图谱概览 / 个股全景 / 游资归因（服务端渲染，
    数据层 quant_backtest/kg_portal.py 纯查询）。"""
    import html as _h
    from urllib.parse import parse_qs as _pq, quote as _qt
    import sys as _sy
    if QBT_DIR not in _sy.path:
        _sy.path.insert(0, QBT_DIR)
    import kg_portal as KP
    e = _h.escape
    qp = _pq(qs)
    code = (qp.get("c") or [""])[0].strip()
    hm = (qp.get("hm") or [""])[0].strip()
    q = (qp.get("q") or [""])[0].strip()
    days = max(7, min(365, int((qp.get("d") or ["120"])[0] or 120)))

    def tcls(t):
        return ("g" if "买" in t else "r" if "卖" in t else
                "y" if "背驰" in t else "n")

    def tagb(tag):
        if tag == "follow_confirm":
            return "<span class='bdg g'>跟随确认</span>"
        if tag == "lure_warn":
            return "<span class='bdg r'>诱多警示</span>"
        return ""

    def stock_link(tc, nm):
        return "<a href='/kg?c=%s'>%s %s</a>" % (e(tc), e(tc), e(nm or ""))

    # ── 搜索框（所有视图常驻）──
    search = ("<form action='/kg' method='get' style='margin:0 0 16px;"
              "display:flex;gap:8px'><input class='kfind' name='q' "
              "placeholder='代码 / 名称，如 002743 或 富煌' value='%s'>"
              "<button class='kfbtn'>🔍 探索</button></form>" % e(q))

    content = ""
    title = "图谱探索"

    # ── ① 搜索结果 ──
    if q:
        lk = KP.api("lookup", q)
        if not lk.get("ok"):
            content = "<div class='sec'><div class='dn'>%s</div></div>" \
                % e(lk.get("error", "查询失败"))
        else:
            rows = "".join(
                "<div style='margin:6px 0'>%s <span class='dim'>%s</span></div>"
                % (stock_link(m["tc"], m["nm"]), e(m["ind"] or ""))
                for m in lk["matches"]) or \
                "<div class='dim'>无匹配标的</div>"
            content = ("<div class='sec'><h2>搜索 “%s” · %d 个匹配</h2>%s</div>"
                       % (e(q), len(lk["matches"]), rows))
            if len(lk["matches"]) == 1:  # 唯一命中直接进个股视图
                code = lk["matches"][0]["tc"]

    # ── ② 个股全景 ──
    if code:
        st = KP.api("stock", code, days)
        tr = KP.api("trace", code, min(days, 90))
        title = "%s 图谱" % (st.get("basic", {}).get("name") or code)
        if not st.get("ok"):
            content += ("<div class='sec'><div class='dn'>%s</div></div>"
                        % e(st.get("error", "")))
        else:
            b = st["basic"]
            # 窗口切换
            pills = "".join(
                "<span style='%s'>%d天</span>" % (_PILL_ON, d) if d == days
                else "<a href='/kg?c=%s&d=%d' style='%s'>%d天</a>"
                % (e(code), d, _PILL, d) for d in (30, 60, 120, 250))
            # S3 双向跳转：图谱视角 ⇄ 量化全景（K线/策略/时间轴/AI）
            content += (
                "<div class='dim' style='margin:4px 0 8px'>⇄ "
                "<a href='/kg/stock/%s'>量化全景（K线 / 策略信号 / "
                "全事件时间轴 / AI 分析）→</a> · <a href='/kg/company"
                "?c=%s'>🏢 公司本体全景（六扇区点线边 + 直方图）→"
                "</a></div>"
                % (_qt(b.get("name") or code), e(code)))
            # L2 Object View 扇区体检条：声明式装配（kg_objectview），
            # 复用已取的 st 零额外查询；新增扇区=配置加一行此条自动带出
            try:
                import kg_objectview as _OV
                ov = _OV.assemble(st)
                _GCLS = {"ok": "g", "stale": "r", "empty": "",
                         "static": "y"}
                _FQLB = {"D": "日频", "E": "触发式", "Q": "季频",
                         "M": "月频", "static": "静态"}
                chips = "".join(
                    "<span class='bdg %s' style='margin:2px 6px 2px 0'"
                    " title=\"%s · 信源 %s · %s 档 · %s\">%s · %d · %s"
                    "%s</span>"
                    % (_GCLS.get(s["grade"], ""),
                       e(s["note"] or s["title"]),
                       e(s["source"] or "—"),
                       s["trust"] or "—",
                       _FQLB.get(s["freq"], s["freq"]),
                       e(s["title"]), s["n"],
                       s["fresh"] or "—",
                       ("（滞后%d%s）"
                        % (s["lag"], "交易日" if s["lag_unit"] == "td"
                           else "天")
                        if s["lag"] is not None else ""))
                    for s in ov["sectors"])
                content += (
                    "<div class='sec'><h2>Object View · 扇区体检</h2>"
                    "<div style='line-height:2.1'>%s</div>"
                    "<div class='dim'>徽章=扇区状态（绿=新鲜 红=连续型"
                    "断流 灰=无数据 黄=静态）· 数字=观测时点/滞后天 · "
                    "悬停看信源信任档。触发式扇区（龙虎榜/涨跌停类）"
                    "无数据≠断流。</div></div>" % chips)
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_kg.py", exc)
            # ACH 三假设条
            ach = tr.get("ach", {}) if tr.get("ok") else {}
            bars = "".join(
                "<div class='dim' style='display:flex;align-items:center;"
                "gap:8px;margin:4px 0'><span style='width:86px;"
                "flex:none'>%s</span><span class='hb' style='width:%dpx'>"
                "</span><b style='color:var(--gray-7)'>%.2f</b></div>"
                % (e(h), int(v * 180), v)
                for h, v in (ach.get("scores") or {}).items())
            h3 = ach.get("h3") or {}
            ach_html = ("<div class='sec'><h2>ACH 竞争性假设（窗口 %d 天）</h2>"
                        "<div style='margin:6px 0 10px;font-size:14px'>"
                        "<span class='bdg y'>收敛</span>%s</div>%s"
                        "<div class='dim'>H1 实质证据 %d 组 · H2 游资净买 "
                        "%.0f 百万（跟随确认 %d / 诱多 %d）· H3 量能 %d / "
                        "信号 %d（无佐证 %d）</div></div>"
                        % (tr.get("window_days", 0), e(ach.get("verdict", "—")),
                           bars, len(ach.get("h1_evidence") or []),
                           (ach.get("h2_net_total") or 0) / 1e6,
                           (ach.get("hm_tags") or {}).get("follow_confirm", 0),
                           (ach.get("hm_tags") or {}).get("lure_warn", 0),
                           h3.get("vol_events", 0), h3.get("signals", 0),
                           h3.get("no_evidence", 0)))
            # 结构信号时间轴
            tli = "".join(
                "<div class='ti'><span class='bdg %s'>%s</span>"
                "<b>%s</b> <span class='dim'>conf %.2f · 结构%.1f 佐证%.1f "
                "资金%.1f%s</span> %s</div>"
                % (tcls(s["type"] or ""), e(s["type"] or ""), s["date"],
                   s["conf"] or 0, s["q_struct"] or 0, s["q_evid"] or 0,
                   s["q_hm"] or 0,
                   " · 实证佐证×%d" % s["cc"] if s.get("cc") else "",
                   tagb(s.get("hm_tag")))
                for s in st["structures"]) or \
                "<div class='dim'>窗口内无结构信号</div>"
            tl_html = ("<div class='sec'><h2>结构事件时间轴（缠论，%d 天）"
                       "</h2><div class='tl'>%s</div></div>"
                       % (days, tli))
            # 游资痕迹 + 量能（右列）
            hmr = "".join(
                "<tr><td><a href='/kg/map?hm=%s'>%s</a></td><td>%d</td>"
                "<td class='%s'>%+.0f</td><td>%s</td></tr>"
                % (_qt(r["hm"]), e(r["hm"]), r["n"],
                   "up" if (r["net"] or 0) > 0 else "dn",
                   (r["net"] or 0) / 1e6, r["last"])
                for r in st["hotmoney"]) or \
                ("<tr><td colspan=4 class='dim'>窗口内无游资上榜痕迹"
                 "（龙虎榜仅上榜日披露）</td></tr>")
            volr = "".join(
                "<tr><td><span class='bdg %s'>%s</span></td><td>%d 次</td></tr>"
                % (tcls(v["type"] or ""), e(v["type"] or ""), v["n"])
                for v in st["vol_events"]) or \
                "<tr><td colspan=2 class='dim'>无量能事件</td></tr>"
            cc = st.get("candle_cov") or {}
            if st.get("candles"):
                def _regtag(r):
                    # P3-b 监管映射：拉抬/打压类手法带警示徽章
                    # （title=细则条款描述，空 reg=常规无关联不显示）
                    if not r.get("reg"):
                        return ""
                    return (" <span class='bdg' style='border:1px solid "
                            "var(--red-500);color:var(--red-500)' title='%s'>⚖ %s</span>"
                            % (e(r.get("tech") or ""), e(r["reg"])))
                cd = "".join(
                    "<tr><td>%s</td><td><span class='bdg %s'>%s</span>%s</td>"
                    "<td>%s</td><td class='%s'>%+.2f%%</td><td>%s</td></tr>"
                    % (r["date"],
                       "g" if "seal" in (r["type"] or "") and r["type"] != \
                       "broken" else ("r" if r["type"] in ("broken",
                       "late_dump") else "y"),
                       CANDLE_CN.get(r["type"], r["type"] or ""),
                       _regtag(r),
                       (r["mtime"] or "")[:2] + ":" + (r["mtime"] or "")[2:]
                       if r["mtime"] else "—",
                       "up" if (r["pct"] or 0) > 0 else "dn",
                       (r["pct"] or 0) * 100,
                       ("触板%d分" % r["n_touch"]) if r.get("n_touch")
                       else ("尾盘量%d%%" % round((r.get("tail_vol") or 0)
                       * 100)))
                    for r in st["candles"])
            elif cc.get("covered"):
                cd = ("<tr><td colspan=5 class='dim'>"
                      "分钟库覆盖内 · 窗口内无盘口事件（无异动）</td></tr>")
            else:
                cd = ("<tr><td colspan=5 class='dim'>"
                      "⚠ 分钟库未覆盖此标的——盘口域仅覆盖分钟库 "
                      "%d 只标的（非全市场），此处为无数据而非无异动"
                      "</td></tr>" % cc.get("n_cov", 0))
            cd_html = ("<div class='sec'><h2>盘口事件（分时粒度，"
                       "封板/炸板/尾盘）</h2><table class='sticky'>"
                       "<tr><th>日期</th><th>类型</th><th>首触时刻</th>"
                       "<th>收盘涨幅</th><th>特征</th></tr>%s</table>"
                       "</div>" % cd)
            # B 类：涨跌停名单（连板/封单/炸板）+ 十大流通股东
            lmr = "".join(
                "<tr><td>%s</td><td><span class='bdg %s'>%s</span></td>"
                "<td>%d 板</td><td>%s</td><td class='%s'>%+.2f%%</td>"
                "<td>%s</td></tr>"
                % (r["date"],
                   "g" if (r.get("ltp") or "U") == "U" else
                   ("y" if (r.get("ltp") or "") == "Z" else "b"),
                   "涨停" if (r.get("ltp") or "U") == "U" else
                   ("炸板" if (r.get("ltp") or "") == "Z" else "跌停"),
                   r.get("lt") or 1,
                   ("%.0f万" % ((r.get("lamt") or 0) / 1e4))
                   if r.get("lamt") else "—",
                   "up" if (r.get("pct") or 0) > 0 else "dn",
                   r.get("pct") or 0,
                   ("炸板%d次" % r["ot"]) if r.get("ot") else "封住")
                for r in (st.get("limits") or [])) or \
                ("<tr><td colspan=6 class='dim'>窗口内无涨跌停上榜记录"
                 "</td></tr>")
            lm_html = ("<div class='sec'><h2>涨跌停名单（连板强度/封单）"
                       "</h2><table><tr><th>日期</th><th>方向</th>"
                       "<th>连板</th><th>封单额</th><th>收盘涨幅</th>"
                       "<th>炸板</th></tr>%s</table></div>" % lmr)
            # P2-① 榜后差额（前五买卖差×席位性质×次日穿透，kg_dt_gap）
            _NAT = {"youzi": ("y", "游资"), "quant": ("b", "量化"),
                    "org": ("n", "机构"), "retail": ("r", "散户"),
                    "other": ("", "普通")}

            def _amt(v):
                return ("—" if v is None else
                        ("%.2f亿" % (v / 1e8) if abs(v) >= 1e8
                         else "%.0f万" % (v / 1e4)))

            def _pc1(v):   # 占比 0.053 → 5.3%
                return "—" if v is None else "%.1f%%" % (v * 100)

            def _pc2(v):   # 收益 0.012 → +1.20%
                return "—" if v is None else "%+.2f%%" % (v * 100)

            _tg_rows = []
            for r in (st.get("topgaps") or []):
                nc = _NAT.get(r.get("nat") or "other", ("", "普通"))
                _pen = r.get("pen")
                _tg_rows.append(
                    "<tr><td>%s</td><td class='%s'>%s</td><td>%s</td>"
                    "<td><span class='bdg %s'>%s</span></td><td>%s</td>"
                    "<td class='%s'>%s</td><td>%s</td>"
                    "<td class='dim'>%s</td></tr>"
                    % (r.get("date"),
                       "up" if (r.get("gap") or 0) > 0 else "dn",
                       _amt(r.get("gap")), _pc1(r.get("gr")),
                       nc[0], nc[1], _pc1(r.get("qr")),
                       ("up" if (r.get("ret1") or 0) > 0 else "dn"),
                       _pc2(r.get("ret1")),
                       ("<span class='bdg g'>穿透</span>" if _pen == 1
                        else ("<span class='bdg r'>未穿</span>"
                              if _pen == 0 else "—")),
                       e((r.get("reason") or "")[:22])))
            tg_html = ("<div class='sec'><h2>榜后差额（前五买卖差 × "
                       "席位性质 × 次日穿透）</h2><table class='sticky'>"
                       "<tr><th>日期</th><th>差额</th><th>占成交额</th>"
                       "<th>主导性质</th><th>量化占比</th><th>次日收益"
                       "</th><th>穿透</th><th>上榜原因</th></tr>%s"
                       "</table><div class='dim' style='margin-top:4px'>"
                       "差额=买方前五-卖方前五（元）；穿透=差额方向与次日"
                       "收盘同向（近端事件次日未到显示 —）</div></div>"
                       % ("".join(_tg_rows) or
                          "<tr><td colspan=8 class='dim'>窗口内无龙虎榜"
                          "上榜记录</td></tr>"))
            # P2-② K线相似（60日对数路径 DTW top-5 + 席位双碰线索）
            _sm_rows = "".join(
                "<tr><td>%s</td><td>%.3f</td>"
                "<td class='%s'>%.0f%%</td><td class='dim'>%s~%s</td>"
                "</tr>"
                % (stock_link(r.get("tc") or "", r.get("nm") or ""),
                   r.get("dist") or 0,
                   "up" if (r.get("corr") or 0) > 0 else "dn",
                   100 * (r.get("corr") or 0),
                   r.get("ws"), r.get("we"))
                for r in (st.get("similar") or [])) or \
                ("<tr><td colspan=4 class='dim'>暂无相似形态数据"
                 "（次新股/窗口不足 60 交易日）</td></tr>")
            _ss_rows = "".join(
                "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
                % (e((r.get("seat") or "")[:30]),
                   stock_link(r.get("tc") or "", r.get("nm") or ""),
                   r.get("d1"), r.get("d2"))
                for r in (st.get("simseats") or []))
            _ss_html = (""
                        if not _ss_rows else
                        ("<h2 style='margin-top:12px'>手法复现线索"
                         "（同席位近期双碰）</h2><table><tr><th>席位"
                         "</th><th>相似票</th><th>碰本票</th>"
                         "<th>碰相似票</th></tr>%s</table>" % _ss_rows))
            sim_html = ("<div class='sec'><h2>K线相似（60日形态 DTW "
                        "top-5）</h2><table><tr><th>标的</th>"
                        "<th>DTW 距离</th><th>同步度</th><th>窗口"
                        "</th></tr>%s</table><div class='dim' "
                        "style='margin-top:4px'>特征=60日收盘对数路径"
                        "（起点归零）；欧氏预筛+带宽DTW；同步度=窗口内"
                        "相关系数</div>%s</div>"
                        % (_sm_rows, _ss_html))
            hdr = "".join(
                "<tr><td>%s</td><td>%s</td><td>%.2f%%</td><td>%s</td></tr>"
                % (r["period"], e(r["name"] or ""), r["ratio"] or 0,
                   ("%.0f万股" % ((r["amt"] or 0) / 1e4))
                   if r.get("amt") else "—")
                for r in (st.get("holders") or [])) or \
                ("<tr><td colspan=4 class='dim'>暂无流通股东结构数据"
                 "（图内 Security 未覆盖或未披露）</td></tr>")
            hd_html = ("<details class='sec'><summary><h2>十大流通股东"
                       "</h2><span class='dim'>机构持仓 · 与龙虎榜对偶"
                       "（谁在持有 vs 谁在交易）</span></summary>"
                       "<table><tr><th>报告期</th><th>机构</th>"
                       "<th>占流通比</th><th>持股</th></tr>%s</table>"
                       "</details>" % hdr)
            # B 类：筹码分布摘要（cyq_perf 特征文件，一行式）
            cy = st.get("cyq") or {}
            if cy.get("date"):
                wr = cy.get("winner_rate")
                ch = cy.get("wr_chg20")
                chs = ("（20日 %+.1fpp）" % ch) if ch is not None else ""
                cy_html = ("<div class='sec'><h2>筹码快照</h2>"
                           "<div style='line-height:1.9'>"
                           "<b>%s</b> · 获利盘 <b>%.1f%%</b>"
                           "<span class='%s'>%s</span> · "
                           "平均成本 %.2f · 成本带宽 %.1f%%"
                           "（85-15 分位/中位，越窄越集中）</div></div>"
                           % (cy["date"], wr,
                              "up" if (ch or 0) > 0 else
                              ("dn" if (ch or 0) < 0 else ""),
                              chs, cy.get("weight_avg") or 0,
                              100 * (cy.get("cost_span") or 0)))
            else:
                cy_html = ""
            right = ("<div class='sec'><h2>游资痕迹（窗口内龙虎榜净买）</h2>"
                     "<table><tr><th>主体</th><th>次数</th><th>净买(百万)"
                     "</th><th>最近</th></tr>%s</table></div>"
                     "<div class='sec'><h2>量能事件</h2><table>%s</table></div>"
                     % (hmr, volr)) + cd_html + lm_html + tg_html \
                     + sim_html + cy_html + hd_html
            # DRIVE 证据组 + 正向传导链（折叠区）
            dgr = "".join(
                "<div style='margin:5px 0'><span class='bdg %s'>%s</span>"
                "<span class='dim'>×%d %s</span></div>"
                % ("n" if d["baseline"] else "y", e(d["grp"]), d["n"],
                   "（高密度基线组，非实质证据）" if d["baseline"]
                   else " ".join(e(t) for t in d["texts"][:2]))
                for d in st["drive_groups"]) or \
                "<div class='dim'>窗口内无外部事件传导</div>"
            fwt = "".join(
                "<div class='ti'><span class='bdg %s'>%s</span><b>%s</b>%s</div>"
                % (tcls(ev["type"] or ""), e(ev["type"] or ""), ev["date"],
                   "".join("<div class='src'>← [%s lag=%dd p=%.2f] %s</div>"
                           % (e(s["grp"]) + ("·基线" if s["baseline"] else ""),
                              s["lag"] or 0, s["p"] or 0, e(s["text"] or ""))
                           for s in ev["sources"]))
                for ev in (tr.get("fwd_tree") or [])) or \
                "<div class='dim'>窗口内无盘面事件</div>"
            chain = ("<details class='sec'><summary><h2>事件传导链</h2>"
                     "<span class='dim'>盘面事件 ← DRIVE 来源树</span>"
                     "</summary><div class='sec-body'>%s"
                     "<h2 style='margin-top:14px'>传导链（时间倒序）</h2>"
                     "<div class='tl'>%s</div></div></details>"
                     % (dgr, fwt))
            head = ("<h1>%s <span class='dim'>%s · %s · %s</span></h1>"
                    "<div class='dim' style='margin:2px 0 8px'>%s</div>"
                    "<div style='display:flex;gap:8px;flex-wrap:wrap'>%s</div>"
                    % (e(b.get("name") or code), e(code),
                       e(b.get("industry") or "-"), e(b.get("area") or "-"),
                       e(b.get("company") or ""), pills))
            duo = ("<div class='duo'>%s<div>%s%s</div></div>"
                   % (tl_html, right, chain))
            content += head + ach_html + duo

    # ── ③ 游资归因视图 ──
    elif hm:
        hd = KP.api("hotmoney", hm, 180)
        title = "%s 游资归因" % hm
        if not hd.get("ok"):
            content = ("<div class='sec'><div class='dn'>%s</div></div>"
                       % e(hd.get("error", "")))
        else:
            top = "".join(
                "<tr><td>%s</td><td>%d</td><td class='%s'>%+.0f</td>"
                "<td>%s</td></tr>"
                % (stock_link(r["tc"], r["nm"]), r["n"],
                   "up" if (r["net"] or 0) > 0 else "dn",
                   (r["net"] or 0) / 1e6, r["last"])
                for r in hd["top_securities"]) or \
                "<tr><td colspan=4 class='dim'>窗口内无交易</td></tr>"
            mv = "".join(
                "<tr><td>%s</td><td>%s</td><td class='%s'>%+.1f</td></tr>"
                % (r["date"], stock_link(r["tc"], r["nm"]),
                   "up" if (r["net"] or 0) > 0 else "dn",
                   (r["net"] or 0) / 1e6)
                for r in hd["recent_moves"][:15]) or ""
            seats = "".join("<span class='tp' style='color:var(--gray-5)'>%s</span>"
                            % e(s) for s in hd["seats"][:8])
            content = (
                "<h1>💰 %s</h1><div class='dim' style='margin:2px 0 8px'>"
                "近 180 天 · %d 席位 · %d 笔上榜 · 净买合计 "
                "<b class='%s'>%+.0f 百万</b>（简版视图，完整画像见 W3）</div>"
                "<div style='margin:0 0 4px'>%s</div>"
                "<div class='duo'><div class='sec'><h2>Top 标的（净买）</h2>"
                "<table class='sticky'><tr><th>标的</th><th>次数</th>"
                "<th>净买(百万)</th><th>最近</th></tr>%s</table></div>"
                "<div class='sec'><h2>近期动向</h2><table class='sticky'>"
                "<tr><th>日期</th><th>标的</th><th>净买(百万)</th></tr>%s"
                "</table></div></div>"
                % (e(hm), hd["seats_n"], hd.get("trades_n", 0),
                   "up" if (hd.get("net_buy_total") or 0) > 0 else "dn",
                   (hd.get("net_buy_total") or 0) / 1e6, seats, top, mv))

    # ── ④ 默认首页：图谱概览 + 近期确认信号 ──
    if not content:
        ss = KP.api("stats")
        sg = KP.api("signals", 14)
        if not ss.get("ok"):
            content = ("<div class='sec'><div class='dn'>%s</div></div>"
                       % e(ss.get("error", "Neo4j 不可达")))
        else:
            n, edg, fr, tg = (ss["nodes"], ss["edges"],
                              ss["freshness"], ss["hm_tags"])
            cards = "".join(
                "<div class='sstat'><span class='st'>%s</span>"
                "<span class='sv'>%s</span><span class='su'>%s</span></div>"
                % (lb, e(str(v)), su)
                for lb, v, su in [
                    ("标的", n.get("Security", 0), "Security"),
                    ("盘面事件", n.get("MarketEvent", 0),
                     "缠论 %d + 量能 %d"
                     % (edg.get("HAS_STRUCTURE", 0),
                        edg.get("HAS_VOL_EVENT", 0))),
                    ("外部事件", n.get("Event", 0),
                     "DRIVE 传导 %d" % edg.get("DRIVE", 0)),
                    ("游资主体", n.get("HotMoney", 0),
                     "席位 %d · 龙虎榜 %d 笔"
                     % (n.get("Seat", 0), edg.get("TRADED_ON", 0))),
                    ("跟随确认", tg.get("follow_confirm", 0),
                     "诱多警示 %d" % tg.get("lure_warn", 0)),
                    ("保鲜时点", fr.get("me", "-"),
                     "事件流 %s / 龙虎榜 %s"
                     % (fr.get("ee", "-"), fr.get("te", "-")))])
            sgr = "".join(
                "<tr><td>%s</td><td>%s</td><td><span class='bdg %s'>%s"
                "</span></td><td>%s</td><td>%.2f</td></tr>"
                % (s["date"], stock_link(s["tc"], s["nm"]),
                   "g" if s["tag"] == "follow_confirm" else "r",
                   e(s["type"] or ""), tagb(s["tag"]), s["conf"] or 0)
                for s in (sg.get("signals") or [])) or \
                "<tr><td colspan=5 class='dim'>近 14 天无确认/诱多信号" \
                "（M2-W4c 口径：post10∈(0,1亿] 且 pre≤0）</td></tr>"
            # G7 来源评级（Admiralty A-F + 断流红点，折叠面板）
            src = KP.api("sources")
            src_html = ""
            if src.get("ok"):
                GC = {"A": "g", "B": "b", "C": "y", "D": "r",
                      "E": "r", "F": "n"}
                GN = {"A": "完全可靠", "B": "通常可靠", "C": "相当可靠",
                      "D": "不常可靠", "E": "不可靠", "F": "无法判断"}
                srows = "".join(
                    "<tr><td><span class='bdg %s' title='%s'>%s</span></td>"
                    "<td>%s</td><td class='dim'>%s</td><td>%s</td></tr>"
                    % (GC.get(s["grade"], "n"),
                       GN.get(s["grade"], ""), s["grade"],
                       e(s["family"]),
                       e((s["basis"] or "")[:40]),
                       ("<span class='bdg e'>断流 %d 表</span>"
                        % len(s["stale"])) if s["stale"] else "✓")
                    for s in src["sources"])
                cat = (" · <span class='bdg e'>catalog 滞后 %sd</span>"
                       % src["catalog_age_days"]) \
                    if src.get("catalog_stale") else ""
                src_html = (
                    "<details class='sec'><summary>📋 来源可靠性评级"
                    "（Admiralty · %s%s）</summary><table class='sticky'>"
                    "<tr><th>档</th><th>源族</th><th>依据</th>"
                    "<th>断流</th></tr>%s</table></details>"
                    % (e(src.get("ts") or "-"), cat, srows))
            content = (
                "<h1>🕸 知识图谱探索</h1><div class='dim' style='margin:2px 0 "
                "10px'>五元本体 + 时序边 + 情报元属性 · 纯查询不写库</div>"
                "<div style='display:grid;grid-template-columns:repeat("
                "auto-fit,minmax(170px,1fr));gap:10px'>%s</div>"
                "<div class='sec'><h2>近 14 天游资确认/诱多信号</h2>"
                "<div class='dim' style='margin-bottom:4px'>校准口径：跟随"
                "确认 post10∈(0,1亿]且pre≤0（历史 ret5 +11.6%%）/ 诱多 "
                "pre&gt;0 且 post&gt;0（-4.2%%）；近月样本全负，体制切换"
                "警讯见计划文档</div><table class='sticky'><tr><th>日期</th>"
                "<th>标的</th><th>结构</th><th>标注</th><th>conf</th></tr>"
                "%s</table></div>%s" % (cards, sgr, src_html))

    # 标题用拼接而非 % 格式化：content 内含字面量 %（如 +11.6%）
    body = ("<!DOCTYPE html><html><head><meta charset='utf-8'><link rel='stylesheet' href='/static/tokens.css'>"
            "<meta name='viewport' content='width=device-width,"
            "initial-scale=1'><title>" + e(title) + " · 图谱探索</title>"
            + h._COCKPIT_CSS + h._KG_CSS + "</head><body>"
            + pill_nav("kg") + search + content
            + "</body></html>")
    h._html(body)
