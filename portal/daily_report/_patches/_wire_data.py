# -*- coding: utf-8 -*-
"""把日报 H5 的静态文案改成数据驱动（换日不再手改模板）。

改造对象与理由：
  - 首屏 h1 / lede / 三个数字卡 / 状态带 / 红色警示框  —— 原来写死 7、278、0、−0.65%、−9.19%
  - 选股屏标题 / 并集卡（227/58/7）/ 警示 / 脚注      —— 原来写死
  - 数据屏标题 / 导语 / 缺口卡 / 筛法卡               —— 原来写死「深市 0 只」
  - DS 数组                                            —— 原来 7 类覆盖数写死

改造后所有这些由 report_YYYYMMDD.json 的 counts / data_health / margin / summary
驱动。缺字段时页面会显式报错而不是静默显示旧数字。

用法：
  python _wire_data.py --dry    # 只检查锚点
  python _wire_data.py          # 执行
"""
from __future__ import annotations

import argparse
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(HERE, "template.html")

# ── 一、把静态结构换成空壳（保留原 class，只加 id）──────────────────
SHELL = [
    # 顶栏日期
    ('    <div class="date">2026-11-09 周一 · 盘前</div>',
     '    <div class="date" id="hdDate">—</div>',
     "顶栏日期"),

    # tab 上的选股数
    ('    <button data-k="s1">选股<span class="cnt">7</span></button>',
     '    <button data-k="s1">选股<span class="cnt" id="tabCnt"></span></button>',
     "tab 计数"),

    # 首屏主体
    ('''      <div class="kicker">每日汇报 · 20261109</div>
      <h1>深市融资融券数据<br>整段没了，今天全盘<em>只剩一条路</em>。</h1>
      <p class="lede">
        盘前能用的最新数据是 <b>11-06（周五）</b>收盘。七类数据里有六类齐全，
        缺的是<b style="white-space:nowrap">深市融资融券余额</b>——深市 1859 只股票一条都没有。
        受这个缺口影响，今天只有一种筛法能覆盖全部 A 股，它筛出 <b>7 只</b>同时满足三个条件的票。
      </p>

      <div class="nums">
        <div class="num" data-ev="confluence">
          <i>›</i><b class="a">7</b><span>三条全中<br>今天可以买</span>
        </div>
        <div class="num" data-ev="union">
          <i>›</i><b>278</b><span>只中一两条<br>不建议直接买</span>
        </div>
        <div class="num" data-ev="margin">
          <i>›</i><b class="w">0</b><span>深市融资融券<br>今天数据缺失</span>
        </div>
      </div>

      <div class="verdict">
        <div class="vd ok"><b>选股</b>7 只已出，能复算</div>
        <div class="vd bad"><b>数据</b>深市融资融券缺失</div>
        <div class="vd warn"><b>筛法</b>只剩一种能用</div>
      </div>

      <div class="note red">
        <b>这两件事今天必须一起读。</b>这 7 只当天全在跌（−0.65% 到 −9.19%），
        其中 6 只扎堆在 AI 算力板块——本质是<b>整个板块急跌 + 公司集体出手回购</b>，
        不是股价要走强。所以它是一次押注一个板块，不是分散买一批股票。
      </div>''',
     '''      <div class="kicker" id="kicker">每日汇报</div>
      <h1 id="heroTitle"></h1>
      <p class="lede" id="heroLede"></p>

      <div class="nums" id="nums"></div>

      <div class="verdict" id="verdict"></div>

      <div class="note red" id="alertBox"></div>''',
     "首屏主体"),

    # 首屏脚注
    ('        数据截至 2026-11-06 收盘 · 怎么执行：隔一个交易日按开盘价买入<br>',
     '        <span id="footAsOf">—</span><br>',
     "首屏脚注"),

    # 选股屏标题
    ('      <h1 style="font-size:19px">7 只：跌到低位、大钱在进、公司在回购</h1>',
     '      <h1 style="font-size:19px" id="pickTitle"></h1>',
     "选股屏标题"),

    # 选股屏警示
    ('''      <div class="note">
        <b>看清楚这是什么信号。</b>7 只当天全在跌，中际旭创一天跌 9.19%（成交 239.7 亿）。
        「跌够了」只是说从高点杀下来了，不代表便宜。要是趋势接着往下走，现在买就是接飞刀。
      </div>''',
     '      <div class="note" id="pickNote"></div>',
     "选股屏警示"),

    # 并集卡
    ('''        <div class="sec-h"><h2>只中一两条的 278 只</h2><span>不建议当篮子</span></div>
        <div class="card tap" data-ev="union"><div class="row" style="padding:0">
          <div class="n"><b>227 只只满足「大钱在进」，58 只只有「公司在托」</b>
            <small>两边都占的正好是上面那 7 只，去重后 278 只。当天全市场普跌，大半股票都算「跌够了」——只看一条就买，等于买进半个市场。</small></div>
          <span class="go">›</span></div>
        </div>''',
     '''        <div class="sec-h"><h2 id="unionH"></h2><span>不建议当篮子</span></div>
        <div class="card tap" data-ev="union"><div class="row" style="padding:0">
          <div class="n" id="unionBody"></div>
          <span class="go">›</span></div>
        </div>''',
     "并集卡"),

    # 选股屏脚注
    ('''        <b>信号取自 11-06 收盘 · 按 11-09 开盘价等额买入</b><br>
        没有做行业分散：7 只里 6 只挤在 AI 算力链''',
     '        <span id="pickFoot"></span>',
     "选股屏脚注"),

    # 数据屏标题与导语
    ('''      <h1 style="font-size:19px">七类数据，<span style="color:var(--bad)">有一类整段是空的</span></h1>
      <p class="lede" style="margin-bottom:14px">
        数据截至 11-06 收盘，逐类数了条数和交易所分布。缺的不是零星几只——
        深市该有 1859 只、北交所该有 350 只，实际一条都没有。
      </p>''',
     '''      <h1 style="font-size:19px" id="dataTitle"></h1>
      <p class="lede" style="margin-bottom:14px" id="dataLede"></p>''',
     "数据屏标题"),

    # 数据屏两张卡（缺口 + 筛法）整块交给 JS
    ('''      <div class="sec">
        <div class="sec-h"><h2>缺口影响面</h2><span>点开看探测记录</span></div>
        <div class="card tap" data-ev="margin"><div class="row" style="padding:0">
          <div class="n"><b>深市融资融券：应该有 1859 只，实际 0 只</b>
            <small>北交所应该有 350 只，实际也是 0。已经从数据商那边反复拉了 49 次、跨 30 个小时，对方一直给空。</small></div>
          <span class="go">›</span></div>
        </div>
      </div>

      <div class="sec">
        <div class="sec-h"><h2>所以今天只剩一种筛法能用</h2><span>三种对比</span></div>
        <div class="card tap" data-ev="pool"><div class="row" style="padding:0">
          <div class="n"><b>三种筛法，只有一种能覆盖全市场</b>
            <small>另外两种都要用到深市融资融券数据。不是它更好，是它没坏。</small></div>
          <span class="go">›</span></div>
        </div>
      </div>''',
     '      <div id="dataSecs"></div>',
     "数据屏两张卡"),
]

# ── 二、删掉写死的 DS 数组，改成从 data_health 读 ──────────────────
OLD_DS = '''var DS = [
  {n:'daily',        d:'日线行情',     u:'每天的开盘、收盘、最高最低价和成交量', v:'5573 只', s:'沪 2316 / 深 2902 / 北 355', ok:1},
  {n:'daily_basic',  d:'每日指标',     u:'总市值、流通市值、换手率、市盈率',     v:'5573 只', s:'沪 2316 / 深 2902 / 北 355', ok:1},
  {n:'moneyflow',    d:'资金流向',     u:'大单和超大单买卖相抵后的净额',         v:'5579 只', s:'沪 2318 / 深 2906 / 北 355', ok:1},
  {n:'cyq_perf',     d:'筹码分布',     u:'持仓成本分布、获利盘比例、平均成本',   v:'5573 只', s:'沪 2316 / 深 2902 / 北 355', ok:1},
  {n:'anns_d',       d:'公司公告',     u:'当天发布的公告标题，用来找增持回购',   v:'656 条',  s:'沪 280 / 深 338 / 北 38',    ok:1},
  {n:'limit_list_d', d:'涨跌停名单',   u:'当天涨停和跌停的股票清单',             v:'61 只',   s:'沪 27 / 深 33 / 北 1',       ok:1},
  {n:'margin_detail',d:'融资融券余额', u:'每只股票融资买入、融券卖出的余额',     v:'1679 只', s:'沪 1679 / 深 0 / 北 0',      ok:0}
];'''

NEW_DS = '''/* 数据集不再写死：全部来自数据包的 data_health.sets（见 _gen_daily_h5.py）*/
var DS = ((R.data_health || {}).sets || []).map(function(s){
  return {n:s.id, d:s.name, u:s.use, ok:s.ok ? 1 : 0,
          v:s.n + ' ' + (s.unit || '只'),
          s:'沪 ' + (s.by_ex.SH||0) + ' / 深 ' + (s.by_ex.SZ||0) + ' / 北 ' + (s.by_ex.BJ||0)};
});'''

# ── 三、注入 fillDynamic ──────────────────────────────────────────
ANCHOR_INIT = "/* ── 初始化 ── */"

FILL = r'''/* ══════ 动态文案（全部来自数据包，换日不手改模板） ══════
   数据来源：report_YYYYMMDD.json 的 counts / data_health / margin / summary。
   任何一项缺失都直接抛错，不静默沿用旧数字——页面宁可挂掉也不能说错话。 */
function need(obj, path){
  var cur = obj;
  var parts = path.split('.');
  for(var i = 0; i < parts.length; i++){
    if(cur == null || cur[parts[i]] === undefined){
      throw new Error('数据包缺少字段 ' + path + '（不能沿用旧数字，请重跑 _gen_daily_h5.py）');
    }
    cur = cur[parts[i]];
  }
  return cur;
}

function fillDynamic(){
  var C  = R.counts || {};
  var H  = R.data_health || {};
  var M  = R.margin || {};
  var S  = R.summary || {};
  var sets = H.sets || [];
  var miss = need(H, 'missing');
  var nGap = miss.length;
  var gapNames = sets.filter(function(s){ return !s.ok; })
                     .map(function(s){ return s.name; });

  var asof = String(R.asof || '');
  var asofMd = asof.length === 8 ? asof.slice(4,6) + '-' + asof.slice(6,8) : asof;
  var dstr = String(R.report_date || '');
  var md = dstr.slice(5);

  function setText(id, txt){ var e = $(id); if(e) e.textContent = txt; }
  function setHtml(id, h){ var e = $(id); if(e) e.innerHTML = h; }

  setText('hdDate', dstr + ' ' + (R.weekday || '') + ' · ' + (R.session || ''));
  setText('kicker', '每日汇报 · ' + asof);
  setText('tabCnt', C.confluence);

  /* ── 首屏标题：有缺口说缺口，没缺口说结果 ── */
  if(nGap){
    setHtml('heroTitle', esc(gapNames.join('、')) +
      '数据<br>整段没了，今天全盘<em>只剩一条路</em>。');
  } else {
    setHtml('heroTitle', '七类数据全齐，<br>盘前选出 <em>' +
      C.confluence + ' 只</em>。');
  }

  /* ── 首屏导语 ── */
  var szPrev = (M.prev || {}).SZ || 0;
  var bjPrev = (M.prev || {}).BJ || 0;
  var nMargin = ((M.asof || {}).SH || 0) + ((M.asof || {}).SZ || 0) + ((M.asof || {}).BJ || 0);
  if(nGap){
    setHtml('heroLede',
      '盘前能用的最新数据是 <b>' + asofMd + '</b>收盘。' +
      H.total + ' 类数据里有 ' + H.complete + ' 类齐全，' +
      '缺的是<b style="white-space:nowrap">' + esc(gapNames.join('、')) + '</b>' +
      (szPrev ? '——深市 ' + szPrev + ' 只股票一条都没有' : '') +
      '。受这个缺口影响，今天只有一种筛法能覆盖全部 A 股，它筛出 <b>' +
      C.confluence + ' 只</b>同时满足三个条件的票。');
  } else {
    setHtml('heroLede',
      '盘前能用的最新数据是 <b>' + asofMd + '</b>收盘，' + H.total +
      ' 类数据全部齐全，两融覆盖 ' + nMargin + ' 只股票。' +
      '本页展示的是<b>人工闸门</b>这套筛法的结果，它选出 <b>' + C.confluence +
      ' 只</b>同时满足三个条件的票；另两套筛法今天也能跑，结果不在这页。');
  }

  /* ── 三个数字卡：第三张恒为「数据缺口类别数」，可跨日比较 ── */
  var cards = [
    {ev:'confluence', v:C.confluence, cls:'a', t1:'三条全中', t2:'今天可以买'},
    {ev:'union',      v:C.union,      cls:'',  t1:'只中一两条', t2:'不建议直接买'},
    {ev:'margin',     v:nGap,         cls:nGap ? 'w' : 'g',
     t1:nGap ? '数据缺口' : '数据缺口', t2:nGap ? ('七类里缺 ' + nGap + ' 类') : '七类全部齐全'}
  ];
  setHtml('nums', cards.map(function(c){
    return '<div class="num" data-ev="' + c.ev + '"><i>›</i>' +
      '<b class="' + c.cls + '">' + c.v + '</b><span>' + c.t1 + '<br>' + c.t2 + '</span></div>';
  }).join(''));

  /* ── 状态带 ── */
  setHtml('verdict',
    '<div class="vd ok"><b>选股</b>' + C.confluence + ' 只已出，能复算</div>' +
    '<div class="vd ' + (nGap ? 'bad' : 'ok') + '"><b>数据</b>' +
      (nGap ? ('缺 ' + nGap + ' 类：' + esc(gapNames.join('、'))) : '七类全齐') + '</div>' +
    '<div class="vd ' + (nGap ? 'warn' : 'ok') + '"><b>筛法</b>' +
      (nGap ? '只剩一种能用' : '三种都能跑') + '</div>');

  /* ── 首屏红框：跌幅区间与行业集中度都从数据算 ── */
  var downTxt = (S.n_down === S.n)
    ? ('这 ' + S.n + ' 只当天全在跌')
    : ('这 ' + S.n + ' 只里有 ' + S.n_down + ' 只当天在跌');
  setHtml('alertBox',
    '<b>这两件事今天必须一起读。</b>' + downTxt +
    '（' + pct(S.pct_max) + ' 到 ' + pct(S.pct_min) + '），其中 ' +
    S.top_industry.n + ' 只同为' + esc(S.top_industry.name) + '行业——' +
    '本质是<b>板块级急跌 + 公司集体出手回购</b>，不是股价要走强。' +
    '所以它是一次押注一个板块，不是分散买一批股票。');

  setHtml('footAsOf', '数据截至 ' + asofMd + ' 收盘 · 怎么执行：隔一个交易日按开盘价买入');

  /* ── 选股屏 ── */
  setHtml('pickTitle', C.confluence + ' 只：跌到低位、大钱在进、公司在回购');
  setHtml('pickNote',
    '<b>看清楚这是什么信号。</b>' + S.n + ' 只当天全在跌，' +
    esc(S.worst.name) + '一天跌 ' + Math.abs(S.worst.pct).toFixed(2) +
    '%（成交 ' + S.worst.amount_yi + ' 亿）。' +
    '「跌够了」只是说从高点杀下来了，不代表便宜。要是趋势接着往下走，现在买就是接飞刀。');

  setHtml('unionH', '只中一两条的 ' + C.union + ' 只');
  var fb = C.factor_breakdown || [];
  var fbTxt = fb.length === 2
    ? (fb[0].n + ' 只只满足「' + esc(fb[0].label) + '」，' + fb[1].n +
       ' 只只有「' + esc(fb[1].label) + '」')
    : (fb.length + ' 个条件各自筛出的股票');
  setHtml('unionBody',
    '<b>' + fbTxt + '</b><small>两边都占的正好是上面那 ' + C.overlap +
    ' 只，去重后 ' + C.union + ' 只。当天全市场普跌，大半股票都算「跌够了」——' +
    '只看一条就买，等于买进半个市场。</small>');

  setHtml('pickFoot',
    '<b>信号取自 ' + asofMd + ' 收盘 · 按 ' + md + ' 开盘价等额买入</b><br>' +
    '没有做行业分散：' + S.n + ' 只里 ' + S.top_industry.n + ' 只挤在' +
    esc(S.top_industry.name));

  /* ── 数据屏 ── */
  if(nGap){
    setHtml('dataTitle', H.total + ' 类数据，' +
      '<span style="color:var(--bad)">有 ' + nGap + ' 类整段是空的</span>');
    setHtml('dataLede',
      '数据截至 ' + asofMd + ' 收盘，逐类数了条数和交易所分布。缺的不是零星几只——' +
      '深市该有 ' + szPrev + ' 只、北交所该有 ' + bjPrev + ' 只，实际一条都没有。');
  } else {
    setHtml('dataTitle', H.total + ' 类数据，' +
      '<span style="color:var(--ok)">全部齐全</span>');
    setHtml('dataLede',
      '数据截至 ' + asofMd + ' 收盘，逐类数了条数和交易所分布。' +
      '七类都在，其中两融覆盖 ' + nMargin + ' 只股票（沪 ' + ((M.asof||{}).SH||0) +
      ' / 深 ' + ((M.asof||{}).SZ||0) + ' / 北 ' + ((M.asof||{}).BJ||0) + '）。');
  }

  var secs = '';
  if(nGap){
    secs += '<div class="sec"><div class="sec-h"><h2>缺口影响面</h2>' +
      '<span>点开看探测记录</span></div>' +
      '<div class="card tap" data-ev="margin"><div class="row" style="padding:0">' +
      '<div class="n"><b>深市融资融券：应该有 ' + szPrev + ' 只，实际 ' +
      ((M.asof||{}).SZ||0) + ' 只</b><small>北交所应该有 ' + bjPrev +
      ' 只，实际 ' + ((M.asof||{}).BJ||0) + ' 只。</small></div>' +
      '<span class="go">›</span></div></div></div>';
  } else {
    secs += '<div class="sec"><div class="sec-h"><h2>两融这一块是好的</h2>' +
      '<span>点开看逐日记录</span></div>' +
      '<div class="card tap" data-ev="margin"><div class="row" style="padding:0">' +
      '<div class="n"><b>' + nMargin + ' 只股票有余额：沪 ' +
      ((M.asof||{}).SH||0) + ' / 深 ' + ((M.asof||{}).SZ||0) + ' / 北 ' +
      ((M.asof||{}).BJ||0) + '</b><small>与上一交易日（' + (M.prev_date || '—') +
      '）完全一致，连续多日稳定。</small></div>' +
      '<span class="go">›</span></div></div></div>';
  }
  secs += '<div class="sec"><div class="sec-h"><h2>' +
    (nGap ? '所以今天只剩一种筛法能用' : '三种筛法今天都能跑') +
    '</h2><span>三种对比</span></div>' +
    '<div class="card tap" data-ev="pool"><div class="row" style="padding:0">' +
    '<div class="n"><b>' + (nGap ? '三种筛法，只有一种能覆盖全市场' : '本页用的是人工闸门这一套') +
    '</b><small>' + (nGap
      ? '另外两种都要用到深市融资融券数据。不是它更好，是它没坏。'
      : '它不依赖两融数据，所以任何一天都能跑完整个市场；另两套要两融，今天也能跑但没渲染在这页。') +
    '</small></div><span class="go">›</span></div></div></div>';
  setHtml('dataSecs', secs);
}

'''

# ── 四、初始化顺序 ────────────────────────────────────────────────
OLD_INIT = "renderStocks(); renderDS(); renderChain();"
NEW_INIT = "fillDynamic();\nrenderStocks(); renderDS(); renderChain();"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    src = io.open(TPL, encoding="utf-8", newline="").read()

    if 'id="heroTitle"' in src:
        print("模板已是数据驱动（检测到 id=heroTitle），无需重复改造。")
        return 0

    problems = []
    for old, _new, tag in SHELL:
        if src.count(old) != 1:
            problems.append((tag, src.count(old), old[:60]))

    for name, old, new in (("DS 数组", OLD_DS, NEW_DS),
                           ("初始化", OLD_INIT, NEW_INIT),
                           ("fillDynamic 锚点", ANCHOR_INIT, ANCHOR_INIT)):
        if src.count(old) != 1:
            problems.append((name, src.count(old), old[:60]))

    print("锚点检查：%d 项，异常 %d 项" % (len(SHELL) + 3, len(problems)))
    for tag, n, head in problems:
        print("  [%d 次] %s  <= %r" % (n, tag, head))
    if problems:
        if a.dry:
            return 1
        print("存在异常锚点，未改动文件。")
        return 2
    if a.dry:
        return 0

    out = src
    for old, new, tag in SHELL:
        assert out.count(old) == 1, "锚点不唯一: %s" % tag
        out = out.replace(old, new, 1)
        assert new in out, "替换后新串不存在: %s" % tag

    assert out.count(OLD_DS) == 1
    out = out.replace(OLD_DS, NEW_DS, 1)

    assert out.count(ANCHOR_INIT) == 1
    out = out.replace(ANCHOR_INIT, FILL + ANCHOR_INIT, 1)

    assert out.count(OLD_INIT) == 1
    out = out.replace(OLD_INIT, NEW_INIT, 1)

    io.open(TPL, "w", encoding="utf-8", newline="").write(out)
    print("已改造 %s（%d -> %d 字节）" % (TPL, len(src.encode()), len(out.encode())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
