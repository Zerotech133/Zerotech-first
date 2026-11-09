# -*- coding: utf-8 -*-
"""给日报接「买入后表现」一列。

背景：日报原来只报信号日当天涨跌（如 -9.19%），那是买入前的事。
跨日实测 auto 池 1 日超额 +0.02pp（≈抛硬币）、20 日 +2.00pp（胜率 74%），
只报当日会把慢因子误判成死因子。这一列就是把「买入之后」的数摆出来。
"""
import io, sys

P = "template.html"
s = io.open(P, encoding="utf-8", newline="").read()
ori = s
dry = "--dry" in sys.argv

def rep(old, new, tag):
    global s
    n = s.count(old)
    assert n == 1, "[%s] 锚点命中 %d 次（必须 1）: %r" % (tag, n, old[:70])
    s = s.replace(old, new, 1)
    assert new in s, "[%s] 替换后新串不存在" % tag
    print("  OK  %-16s %d -> %d 字" % (tag, len(old), len(new)))

print("── 1 选股屏加跟踪卡容器")
rep('      <div id="stkList"></div>\n',
    '      <div id="holdCard"></div>\n\n      <div id="stkList"></div>\n',
    "holdCard")

print("── 2 个股卡右侧：有跟踪就显示买入后收益")
rep(
"""      +   '<div class="rt"><b class="'+c+'">'+pct(s.pct,true)+'</b>'
      +     '<small>大单净买 '+money(s.net_mf_wan)+'</small></div>'""",
"""      +   (HOLD_ROW[s.code]
            ? '<div class="rt"><b class="'+cls(HOLD_ROW[s.code].ret)+'">'
              + pct(HOLD_ROW[s.code].ret*100,true)+'</b>'
              + '<small>买入后'+dcn(HD.day_index)+'天</small></div>'
            : '<div class="rt"><b class="'+c+'">'+pct(s.pct,true)+'</b>'
              + '<small>大单净买 '+money(s.net_mf_wan)+'</small></div>')""",
    "stk-row")

print("── 3 注入跟踪数据与 renderHold")
rep(
"""function renderStocks(){""",
"""/* ── 持仓跟踪 ──
   原来只报信号日当天涨跌，那是「买入前」的事。同一批票按信号买入后赚多少，
   才是能评价选股对不对的数。实测同一因子 1 日超额 +0.02pp、20 日 +2.00pp，
   只报当日会把慢因子误判成死因子。数据来自 hold_track.py。 */
var HD = R.hold || null;
var HOLD_ROW = {};
if(HD && HD.batches){
  HD.batches.forEach(function(b){
    (b.rows || []).forEach(function(r){
      if(!HOLD_ROW[r.code]) HOLD_ROW[r.code] = r;
    });
  });
}
var _DC = ['零','一','二','三','四','五','六','七','八','九','十'];
function dcn(n){ return (n >= 0 && n <= 10) ? _DC[n] : String(n); }
function holdWhen(){
  if(!HD) return '';
  return '第 ' + dcn(HD.day_index) + ' 天' +
    (HD.partial ? '（盘中 ' + HD.as_of + '）' : '（已收盘）');
}

function renderHold(){
  var box = $('holdCard'); if(!box) return;
  if(!HD || !HD.batches || !HD.batches.length){ box.innerHTML = ''; return; }
  var b = HD.batches[0], B = HD.bench || {};
  var nowTxt = b.ret > 0 ? '赚' : (b.ret < 0 ? '亏' : '平');
  var exTxt  = b.excess > 0
    ? ('跑赢 ' + Math.abs(b.excess*100).toFixed(1) + ' 个百分点')
    : (b.excess < 0
        ? ('跑输 ' + Math.abs(b.excess*100).toFixed(1) + ' 个百分点')
        : '基本持平');
  box.innerHTML =
      '<div class="card tap" data-ev="hold"><div class="row" style="padding:0">'
    +   '<div class="n">'
    +     '<b>买入后平均' + nowTxt + ' <span class="' + cls(b.ret) + '">'
    +       Math.abs(b.ret*100).toFixed(1) + '%</span></b>'
    +     '<small>' + holdWhen() + ' · ' + fmtDate(HD.entry_date) + ' 开盘价买入</small>'
    +     '<small style="color:var(--faint)">同期两融标的平均 '
    +       pct((B.ret||0)*100, true) + '，' + exTxt + '；'
    +       b.win + ' 只里 ' + b.win + ' 只跑赢</small>'
    +   '</div><span class="go">›</span>'
    + '</div></div>';
}

function renderStocks(){""",
    "renderHold")

print("── 4 初始化调用")
rep("renderStocks(); renderDS(); renderChain();",
    "renderStocks(); renderHold(); renderDS(); renderChain();",
    "init-call")

print("── 5 抽屉：跟踪卡全文")
rep(
"""EV.signals = {""",
"""EV.hold = {t:'买入后表现', h:function(){
  if(!HD) return '<p style="color:var(--dim)">这一版没有跟踪数据。</p>';
  var B = HD.bench || {};
  var h = '<h1 style="font-size:19px;margin-bottom:2px">买入之后赚了多少</h1>'
    + '<div style="color:var(--dim);font-size:12.5px;margin-bottom:16px">'
    + fmtDate(HD.entry_date) + ' 用开盘价买入，' + holdWhen() + '</div>';

  HD.batches.forEach(function(b){
    var ttl = b.key === 'main' ? '本页主选' : (b.key === 'auto' ? 'auto 生产池' : b.label);
    h += '<h3>' + esc(ttl) + '（' + b.n_priced + ' 只有价）</h3>';
    h += '<div class="nums" style="margin-bottom:12px">'
      + '<div class="num" style="cursor:default"><b class="' + cls(b.ret) + '">'
      +   pct(b.ret*100, true) + '</b><span>买入后<br>平均收益</span></div>'
      + '<div class="num" style="cursor:default"><b class="' + cls(b.excess) + '">'
      +   (b.excess>0?'+':'') + (b.excess*100).toFixed(1) + 'pp</b>'
      +   '<span>比同期两融<br>标的多赚</span></div>'
      + '<div class="num" style="cursor:default"><b>' + b.win + '/' + b.n_priced + '</b>'
      +   '<span>跑赢同期<br>基准的只数</span></div>'
      + '</div>';
    h += '<table><thead><tr><th>名称</th><th>入场价</th><th>现价</th>'
      +  '<th>收益</th><th>超额</th></tr></thead><tbody>';
    b.rows.forEach(function(r){
      h += '<tr><td>' + esc(r.name) + '<br>'
        +  '<span style="color:var(--faint);font-size:10.5px">' + esc(r.code) + '</span></td>'
        + '<td>' + r.entry + '</td><td>' + r.now + '</td>'
        + '<td class="' + cls(r.ret) + '">' + pct(r.ret*100, true) + '</td>'
        + '<td class="' + cls(r.excess) + '">' + (r.excess>0?'+':'')
        + (r.excess*100).toFixed(2) + 'pp</td></tr>';
    });
    h += '</tbody></table>';
  });

  h += '<h3>这几个数怎么算的</h3>'
    + '<div style="font-size:12px;color:var(--dim);line-height:1.9">'
    + '入场价＝信号日之后第一个交易日的开盘价；收益＝现价 ÷ 入场价 − 1。<br>'
    + '基准＝' + esc(B.label || '两融标的池') + '（' + (B.n||0) + ' 只）同一天开盘买入到现在的平均，'
    + '不是全市场——选股池本来只从两融标的里选，用全市场比会混进风格差。<br>'
    + '超额＝这批票的平均收益 − 基准。当天买的票都受同一个大盘影响，'
    + '相减之后剩下的才是选股本身带来的那部分。<br>'
    + '<span style="color:var(--faint)">数据源：tushare rt_k 实时快照 '
    + esc(HD.as_of) + '；口径实现在 portal/daily_report/hold_track.py</span></div>';
  return h;
}};

EV.signals = {""",
    "EV.hold")

print("── 6 个股抽屉加买入后一段")
rep(
"""    h += '<h3>三条为什么成立</h3>';""",
"""    if(HOLD_ROW[s.code]){
      var hr = HOLD_ROW[s.code];
      h += '<h3>买入之后</h3>';
      h += '<div style="font-size:12.5px;color:var(--dim);line-height:1.9;margin-bottom:14px">'
        + '按信号在 ' + fmtDate(HD.entry_date) + ' 开盘 ' + hr.entry + ' 买入，'
        + '现在 ' + hr.now + '，' + (hr.ret>=0?'赚':'亏') + ' <b style="display:inline" class="'
        + cls(hr.ret) + '">' + Math.abs(hr.ret*100).toFixed(2) + '%</b>。'
        + '同期两融标的平均 ' + pct((HD.bench.ret||0)*100, true) + '，'
        + '所以' + (hr.excess>=0?'跑赢 ':'跑输 ')
        + '<b style="display:inline" class="' + cls(hr.excess) + '">'
        + Math.abs(hr.excess*100).toFixed(2) + ' 个百分点</b>。<br>'
        + '<span style="color:var(--faint)">这是买入后的数，跟下面「三条为什么成立」里的'
        + '信号日涨跌不是一回事，别混着看。</span></div>';
    }

    h += '<h3>三条为什么成立</h3>';""",
    "stk-drawer")

if dry:
    print("\n[dry] 未写盘。原 %d 字 → 新 %d 字" % (len(ori), len(s)))
else:
    if s == ori:
        raise SystemExit("没有产生任何改动")
    io.open(P, "w", encoding="utf-8", newline="").write(s)
    print("\n写盘 %s：%d → %d 字" % (P, len(ori), len(s)))
