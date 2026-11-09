# -*- coding: utf-8 -*-
"""重写两融抽屉：原内容是 11-06 08:39 的快照（全篇建立在「深市 0 只」上），
两融数据在 09:00 补齐后这段已经与事实相反。改为按 R.margin 渲染。"""
import io
import sys

TPL = r"C:/项目/老田的AI助手/portal/daily_report/template.html"

NEW = """EV.margin = {t:'两融这一块到底怎么样', h:function(){
  var M = R.margin || {}, H = R.data_health || {};
  var miss = H.missing || [];
  var broken = miss.indexOf('margin_detail') >= 0;
  var a = M.asof || {}, p = M.prev || {};
  var tot = function(x){ return (x.SH||0) + (x.SZ||0) + (x.BJ||0); };
  var ad = String(R.asof || '');
  var asofMd = ad.length === 8 ? ad.slice(4,6) + '-' + ad.slice(6,8) : ad;

  var h = '<h3>数据日（' + asofMd + '）</h3>'
    + '<div class="kv"><span>沪市</span><b>' + (a.SH||0) + ' 只</b></div>'
    + '<div class="kv"><span>深市</span><b>' + (a.SZ||0) + ' 只</b></div>'
    + '<div class="kv"><span>北交所</span><b>' + (a.BJ||0) + ' 只</b></div>'
    + '<div class="kv"><span>合计（只算股票，不含 ETF）</span><b>' + tot(a) + ' 只</b></div>';

  if(broken){
    h += '<div class="note red"><b>这一天有缺口。</b>'
      + '深市应该有 ' + (p.SZ||0) + ' 只，实际 ' + (a.SZ||0) + ' 只；'
      + '北交所应该有 ' + (p.BJ||0) + ' 只，实际 ' + (a.BJ||0) + ' 只。'
      + '凡是依赖两融的筛法，当天都跑不了。</div>';
  } else {
    h += '<div class="note blue"><b>这一天是齐的。</b>'
      + '和上一交易日（' + (M.prev_date || '—') + '）逐项完全一致：'
      + '深市 ' + (a.SZ||0) + '、沪市 ' + (a.SH||0) + '、北交所 ' + (a.BJ||0) + '。'
      + '两融标的本来就是一批固定名单，连续多日不变是正常状态，不是没更新。</div>';
  }

  var hist = M.history || [];
  if(hist.length){
    h += '<h3>近 ' + hist.length + ' 个交易日</h3>'
      + '<table><thead><tr><th>交易日</th><th>深市</th><th>沪市</th><th>北交所</th></tr></thead><tbody>';
    for(var i = 0; i < hist.length; i++){
      var x = hist[i], bx = x.by_ex || {};
      h += '<tr><td>' + fmtDate(x.date) + '</td>'
        + '<td' + (bx.SZ ? '' : ' style="color:var(--bad)"') + '>' + (bx.SZ||0) + '</td>'
        + '<td' + (bx.SH ? '' : ' style="color:var(--bad)"') + '>' + (bx.SH||0) + '</td>'
        + '<td' + (bx.BJ ? '' : ' style="color:var(--bad)"') + '>' + (bx.BJ||0) + '</td></tr>';
    }
    h += '</tbody></table>';
  }

  h += '<div class="note"><b>口径提醒。</b>两融有两套数：含 ETF 的全量（4488 条）'
    + '和只算股票（3888 条）。本页统一用只算股票那套；'
    + '旧文档里写过的「深市 2119 只」是全量口径，两个数不能直接比。</div>';
  return h;
}};

"""

src = io.open(TPL, encoding="utf-8", newline="").read()
i = src.find("EV.margin = {")
j = src.find("EV.pool = {")
assert i > 0 and j > i, "定位失败 i=%d j=%d" % (i, j)
old = src[i:j]
assert "深市融资融券为什么是空的" in old, "定位到的块不对"
assert old.count("#ffff") == 0
out = src[:i] + NEW + src[j:]
assert "EV.margin = {t:'两融这一块到底怎么样'" in out
assert out.count("EV.margin = {") == 1
io.open(TPL, "w", encoding="utf-8", newline="").write(out)
print("replaced %d -> %d bytes (block %d -> %d)"
      % (len(src.encode()), len(out.encode()), len(old), len(NEW)))
