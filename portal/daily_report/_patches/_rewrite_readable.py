# -*- coding: utf-8 -*-
"""把每日汇报 H5 的文案从「内部工作稿」改写为「外部人能读」。

设计原则（三层信息结构，不是降级）：
  第一层 结论 —— 一句人话，不含任何代号
  第二层 解释 —— 为什么，用生活语言说因果
  第三层 证据 —— 原始字段名、阈值、脚本路径，全保留，但只出现在抽屉里

用法：
  python _rewrite_readable.py --dry   # 只统计每条锚点命中数，不改文件
  python _rewrite_readable.py         # 执行改写，逐条断言 count==1
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(HERE, "template.html")

# (锚点原文, 替换后, 说明)
REPL = [
    # ══════ 头部 meta ══════
    ('<meta name="description" content="2026-11-09 盘前：七个数据集六个完整，深市两融整段归零。'
     '全盘选股只能走 gate 池，出 7 只双因子共振。点开每条都能看到原始数值与出处。">',
     '<meta name="description" content="2026-11-09 盘前：七类数据有六类齐全，深市融资融券余额整段缺失。'
     '今天只有一种筛法能覆盖全市场，筛出 7 只。点开每条都能看到原始数值和出处。">',
     "meta.description 去术语"),

    ('<meta property="og:description" content="7 只双因子共振 / 278 只宽口径并集 / 0 只深市两融。'
     '三分屏 + 点击下钻，证据可逐条复核。">',
     '<meta property="og:description" content="7 只三条全中 / 278 只只中一两条 / 0 只深市两融。'
     '四屏分开看，每条都能点开核对。">',
     "og.description 去术语"),

    # ══════ 屏1 结论 ══════
    ('<div class="kicker">DAILY BRIEF · 20261109</div>',
     '<div class="kicker">每日汇报 · 20261109</div>',
     "结论屏 kicker 去英文"),

    ('        盘前最新收盘 <b>11-06（周五）</b>。七个数据集六个完整，\n'
     '        <b style="white-space:nowrap">深市两融 0 只</b>——SENT 类口径全部不可用。\n'
     '        唯一能跑完全市场的 gate 池出了 <b>7 只双因子共振</b>。',
     '        盘前能用的最新数据是 <b>11-06（周五）</b>收盘。七类数据里有六类齐全，\n'
     '        缺的是<b style="white-space:nowrap">深市融资融券余额</b>——深市 1859 只股票一条都没有。\n'
     '        受这个缺口影响，今天只有一种筛法能覆盖全部 A 股，它筛出 <b>7 只</b>同时满足三个条件的票。',
     "结论屏导语"),

    ('<i>\u203a</i><b class="a">7</b><span>双因子共振<br>今日可执行</span>',
     '<i>\u203a</i><b class="a">7</b><span>三条全中<br>今天可以买</span>',
     "首数卡 1"),

    ('<i>\u203a</i><b>278</b><span>宽口径并集<br>不建议直接买</span>',
     '<i>\u203a</i><b>278</b><span>只中一两条<br>不建议直接买</span>',
     "首数卡 2"),

    ('<i>\u203a</i><b class="w">0</b><span>深市两融<br>今日数据缺口</span>',
     '<i>\u203a</i><b class="w">0</b><span>深市两融<br>今天数据缺失</span>',
     "首数卡 3"),

    ('<div class="vd ok"><b>选股</b>7 只已出，可复算</div>',
     '<div class="vd ok"><b>选股</b>7 只已出，能复算</div>',
     "状态带 1"),

    ('<div class="vd warn"><b>口径</b>gate 非最优</div>',
     '<div class="vd warn"><b>筛法</b>只剩一种能用</div>',
     "状态带 3"),

    ('        <b>这两件事今天必须一起读。</b>7 只当日涨幅全部为负（−0.65% ~ −9.19%），\n'
     '        其中 6 只属于 AI 算力链——性质是<b>板块级急跌 + 集体回购护盘</b>，\n'
     '        不是强势突破。它是一次板块押注，不是分散组合。',
     '        <b>这两件事今天必须一起读。</b>这 7 只当天全在跌（−0.65% 到 −9.19%），\n'
     '        其中 6 只扎堆在 AI 算力板块——本质是<b>整个板块急跌 + 公司集体出手回购</b>，\n'
     '        不是股价要走强。所以它是一次押注一个板块，不是分散买一批股票。',
     "结论屏红框"),

    ('          <div class="n"><b>四屏结构 · 一屏一件事</b>\n'
     '            <small>结论 → 选股 → 数据 → 系统。不做长图，逐条可下钻。</small></div>',
     '          <div class="n"><b>四屏各说一件事</b>\n'
     '            <small>先看结论，再看买了什么，然后看数据靠不靠谱、系统有没有毛病。'
     '每一条都能点开看原始数字。</small></div>',
     "怎么读卡"),

    # ══════ 屏2 选股 ══════
    ('<div class="kicker">PICKS · 双因子共振</div>',
     '<div class="kicker">今天选出来的股票</div>',
     "选股屏 kicker"),

    ('<h1 style="font-size:19px">7 只：砸到低位、资金净流入、公司回购</h1>',
     '<h1 style="font-size:19px">7 只：跌到低位、大钱在进、公司在回购</h1>',
     "选股屏标题"),

    ('        三个条件同时成立才算共振。点任一只，逐条看数值对不对得上。',
     '        下面三个条件必须同时成立才会被选进来。点任意一只，能看到它每一条为什么算过。',
     "选股屏导语"),

    ('<b style="color:var(--tx)">① 60 日低位</b> 收盘 ≤ 前 60 日最低 × 1.05<br>',
     '<b style="color:var(--tx)">① 跌够了</b>　今天收盘价，离最近 60 天的最低价不到 5%<br>',
     "条件一"),

    ('<b style="color:var(--tx)">② 资金净流入</b> net_mf_amount ÷ circ_mv &gt; 0<br>',
     '<b style="color:var(--tx)">② 大钱在进</b>　今天大单买卖相抵是净买入，不是净卖出<br>',
     "条件二"),

    ('<b style="color:var(--tx)">③ 公告护盘</b> 当日公告含 增持 / 回购 / 举牌',
     '<b style="color:var(--tx)">③ 公司在托</b>　当天发公告：股东增持、公司回购、或被举牌',
     "条件三"),

    ('<div style="font-size:11.5px;color:var(--dim);margin-top:7px;line-height:1.75">',
     '<div style="font-size:12px;color:var(--dim);margin-top:8px;line-height:1.9">',
     "条件块字号"),

    ('      <div id="stkList"></div>',
     '      <div style="font-size:10.5px;color:var(--faint);margin:-3px 2px 12px;line-height:1.65">\n'
     '        技术口径：① 收盘 ÷ 前 60 日最低 ≤ 1.05　② 主力净额 ÷ 流通市值 &gt; 0　'
     '③ 公告标题命中「增持 / 回购 / 举牌」\n'
     '      </div>\n\n'
     '      <div id="stkList"></div>',
     "条件块技术脚注"),

    ('        <b>看清楚这是什么信号。</b>7 只全部当日下跌，中际旭创单日 −9.19%（成交 239.7 亿）。\n'
     '        「低位」是高位股杀下来的低位，不是估值低位。趋势继续下行就是接飞刀。',
     '        <b>看清楚这是什么信号。</b>7 只当天全在跌，中际旭创一天跌 9.19%（成交 239.7 亿）。\n'
     '        「跌够了」只是说从高点杀下来了，不代表便宜。要是趋势接着往下走，现在买就是接飞刀。',
     "选股屏警示"),

    ('          <div class="n"><b>227 只来自资金因子 + 58 只来自公告因子</b>\n'
     '            <small>重叠 7 只后合计 278。当日普跌，几乎大半市场都"在低位"。</small></div>',
     '          <div class="n"><b>227 只只满足「大钱在进」，58 只只有「公司在托」</b>\n'
     '            <small>两边都占的正好是上面那 7 只，去重后 278 只。当天全市场普跌，'
     '大半股票都算「跌够了」——只看一条就买，等于买进半个市场。</small></div>',
     "并集卡"),

    ('        <b>信号日 11-06 收盘 · 执行 11-09 开盘等权</b><br>\n'
     '        未做行业中性化：7 只里 6 只集中 AI 算力链',
     '        <b>信号取自 11-06 收盘 · 按 11-09 开盘价等额买入</b><br>\n'
     '        没有做行业分散：7 只里 6 只挤在 AI 算力链',
     "选股屏脚注"),

    # ══════ 屏3 数据 ══════
    ('<div class="kicker">DATA · 盘前到底有什么</div>',
     '<div class="kicker">盘前手里到底有什么数据</div>',
     "数据屏 kicker"),

    ('<h1 style="font-size:19px">七个数据集，<span style="color:var(--bad)">一个整段是空的</span></h1>',
     '<h1 style="font-size:19px">七类数据，<span style="color:var(--bad)">有一类整段是空的</span></h1>',
     "数据屏标题"),

    ('        11-06 收盘数据，逐集核覆盖与交易所分布。缺的不是零星几只——\n'
     '        深市 1859 只、北交所 350 只，一条都没有。',
     '        数据截至 11-06 收盘，逐类数了条数和交易所分布。缺的不是零星几只——\n'
     '        深市该有 1859 只、北交所该有 350 只，实际一条都没有。',
     "数据屏导语"),

    ('          <div class="n"><b>两融深市：1859 只 → 0 只</b>\n'
     '            <small>北交所 350 → 0。已探测 49 轮 / 跨约 30 小时，tushare 侧仍返回 0。</small></div>',
     '          <div class="n"><b>深市融资融券：应该有 1859 只，实际 0 只</b>\n'
     '            <small>北交所应该有 350 只，实际也是 0。已经从数据商那边反复拉了 49 次、'
     '跨 30 个小时，对方一直给空。</small></div>',
     "缺口卡"),

    ('        <div class="sec-h"><h2>所以今天只能走 gate 池</h2><span>三池对比</span></div>\n'
     '        <div class="card tap" data-ev="pool"><div class="row" style="padding:0">\n'
     '          <div class="n"><b>gate 放行 · wf 拦截 · auto 拦截</b>\n'
     '            <small>gate 不是"更优口径"，是唯一不残缺的选择。</small></div>',
     '        <div class="sec-h"><h2>所以今天只剩一种筛法能用</h2><span>三种对比</span></div>\n'
     '        <div class="card tap" data-ev="pool"><div class="row" style="padding:0">\n'
     '          <div class="n"><b>三种筛法，只有一种能覆盖全市场</b>\n'
     '            <small>另外两种都要用到深市两融数据。不是它更好，是它没坏。</small></div>',
     "筛法卡"),

    ('        <b>另有一处数据边界要知道。</b>板块类因子（BOARD_B01）今天也用不了：\n'
     '        sector_daily 只到 10-12，sector_members 只有 10-22 单日快照。',
     '        <b>还有一处边界：行业板块数据也断了。</b>板块行情只更新到 10-12，\n'
     '        板块成分股只有 10-22 一天快照——所以今天也没法按行业挑股。',
     "板块缺口"),

    ('<div class="foot"><b>核验方式：直读 parquet，逐集 count + 交易所 groupby</b></div>',
     '<div class="foot"><b>核验方式：直接读原始数据文件，逐类数条数、按交易所分组</b></div>',
     "数据屏脚注"),

    # ══════ 屏4 系统 ══════
    ('<div class="kicker">SYSTEM · 链路与风控</div>',
     '<div class="kicker">系统状态 · 数据通不通</div>',
     "系统屏 kicker"),

    ('      <h1 style="font-size:19px">晨链和风控正常，<br>采集和基建各有异常</h1>',
     '      <h1 style="font-size:19px">选股和风控正常，<br>抓数据和底层服务出了故障</h1>',
     "系统屏标题"),

    ('        选股能不能信，取决于链路通不通。以下为 11-09 07:45 简报快照。',
     '        这 7 只能不能信，先看数据管道通不通。下面是 11-09 早上 7:45 的体检结果。',
     "系统屏导语"),

    ('        <div class="sec-h"><h2>组合信号 149 条</h2><span>点开看前 10</span></div>\n'
     '        <div class="card tap" data-ev="signals"><div class="row" style="padding:0">\n'
     '          <div class="n"><b>MRVI 97 分领跑，其后 9 只均 87 分</b>\n'
     '            <small>conf=0.70 · 与本次 7 只共振不是同一口径，不要混读。</small></div>',
     '        <div class="sec-h"><h2>另一套模型也给了信号</h2><span>149 条，点开看前 10</span></div>\n'
     '        <div class="card tap" data-ev="signals"><div class="row" style="padding:0">\n'
     '          <div class="n"><b>打分最高的是 MRVI（97 分），其后 9 只都是 87 分</b>\n'
     '            <small>这是另一套模型的判断，和本页这 7 只不是一套逻辑，别混着看。</small></div>',
     "信号卡"),

    ('        <div class="sec-h"><h2>风控水位</h2><span>RiskGuard 影子</span></div>\n'
     '        <div class="card tap" data-ev="risk"><div class="row" style="padding:0">\n'
     '          <div class="n"><b>R1 放行 16959 / 拦截 234</b>\n'
     '            <small>市场级闸 pass · 最新 score 0.2216</small></div>',
     '        <div class="sec-h"><h2>风控今天怎么判的</h2><span>只看不改，影子模式</span></div>\n'
     '        <div class="card tap" data-ev="risk"><div class="row" style="padding:0">\n'
     '          <div class="n"><b>17193 次下单请求，放行 16959 次、拦下 234 次</b>\n'
     '            <small>大盘这一关：允许交易。风险分 0.22（0 最安全，1 最危险）。</small></div>',
     "风控卡"),

    ('        <div class="sec-h"><h2>数据血缘</h2><span>24 表在册</span></div>\n'
     '        <div class="card tap" data-ev="lineage"><div class="row" style="padding:0">\n'
     '          <div class="n"><b>断链 0 · 陈旧 4</b>\n'
     '            <small>corpus 四张表已 6 个交易日未更新（kb_cleaned / index / normalized / rag）</small></div>',
     '        <div class="sec-h"><h2>数据表体检</h2><span>24 张在册</span></div>\n'
     '        <div class="card tap" data-ev="lineage"><div class="row" style="padding:0">\n'
     '          <div class="n"><b>0 张断供，4 张超过 6 个交易日没更新</b>\n'
     '            <small>都是知识库用的那四张（清理文本 / 索引 / 归一化 / 向量），不影响今天选股。</small></div>',
     "体检卡"),

    # ══════ 数据集数组：加用途字段、交易所改中文 ══════
    ("""var DS = [
  {n:'daily',        d:'日线行情', v:'5573 只', s:'SZ 2902 / SH 2316 / BJ 355', ok:1},
  {n:'daily_basic',  d:'每日指标', v:'5573 只', s:'SZ 2902 / SH 2316 / BJ 355', ok:1},
  {n:'moneyflow',    d:'资金流向', v:'5579 只', s:'SZ 2906 / SH 2318 / BJ 355', ok:1},
  {n:'cyq_perf',     d:'筹码分布', v:'5573 只', s:'SZ 2902 / SH 2316 / BJ 355', ok:1},
  {n:'anns_d',       d:'公告',     v:'656 条',  s:'SZ 338 / SH 280 / BJ 38',    ok:1},
  {n:'limit_list_d', d:'涨跌停',   v:'61 只',   s:'SZ 33 / SH 27 / BJ 1',       ok:1},
  {n:'margin_detail',d:'两融明细', v:'1679 只', s:'SH 1679 / SZ 0 / BJ 0',      ok:0}
];""",
     """var DS = [
  {n:'daily',        d:'日线行情',     u:'每天的开盘、收盘、最高最低价和成交量', v:'5573 只', s:'沪 2316 / 深 2902 / 北 355', ok:1},
  {n:'daily_basic',  d:'每日指标',     u:'总市值、流通市值、换手率、市盈率',     v:'5573 只', s:'沪 2316 / 深 2902 / 北 355', ok:1},
  {n:'moneyflow',    d:'资金流向',     u:'大单和超大单买卖相抵后的净额',         v:'5579 只', s:'沪 2318 / 深 2906 / 北 355', ok:1},
  {n:'cyq_perf',     d:'筹码分布',     u:'持仓成本分布、获利盘比例、平均成本',   v:'5573 只', s:'沪 2316 / 深 2902 / 北 355', ok:1},
  {n:'anns_d',       d:'公司公告',     u:'当天发布的公告标题，用来找增持回购',   v:'656 条',  s:'沪 280 / 深 338 / 北 38',    ok:1},
  {n:'limit_list_d', d:'涨跌停名单',   u:'当天涨停和跌停的股票清单',             v:'61 只',   s:'沪 27 / 深 33 / 北 1',       ok:1},
  {n:'margin_detail',d:'融资融券余额', u:'每只股票融资买入、融券卖出的余额',     v:'1679 只', s:'沪 1679 / 深 0 / 北 0',      ok:0}
];""",
     "DS 数组"),

    ("""    h += '<div class="card tap" data-ev="ds:'+x.n+'"><div class="row" style="padding:0">'
      + '<div class="n"><b>'+x.d+' <code style="font-size:10px">'+x.n+'</code></b>'
      +   '<small>'+x.v+' · '+x.s+'</small></div>'""",
     """    h += '<div class="card tap" data-ev="ds:'+x.n+'"><div class="row" style="padding:0">'
      + '<div class="n"><b>'+x.d+'</b>'
      +   '<small>'+x.u+'</small>'
      +   '<small style="color:var(--faint)">'+x.v+' · '+x.s+'</small></div>'""",
     "renderDS 渲染"),

    # ══════ 链路数组 ══════
    ("""var CHAIN = [
  {n:'晨链', d:'05:50 起四环递进，07:30 出组合信号', s:'ok',  t:'正常'},
  {n:'风控环', d:'19:10 影子日跑；周日 08:19 R3 周批', s:'ok', t:'正常'},
  {n:'采集环', d:'GubaCrawl 失败 + 4 个 TdxSync 任务查询不到', s:'bad', t:'异常'},
  {n:'基础设施', d:'KgDataDashboard 任务查询失败', s:'bad', t:'异常'}
];""",
     """var CHAIN = [
  {n:'早间选股流水线', d:'05:50 开始跑，07:30 出结果', s:'ok',  t:'正常'},
  {n:'风险控制', d:'每天 19:10 跑一遍，周日早上再跑一次周度', s:'ok', t:'正常'},
  {n:'数据抓取', d:'股吧数据抓取失败，另有 4 个行情同步任务今天没跑成', s:'bad', t:'异常'},
  {n:'底层服务', d:'数据看板服务连不上', s:'bad', t:'异常'}
];""",
     "CHAIN 数组"),

    # ══════ 抽屉标题 ══════
    ("EV.confluence = {t:'7 只共振 · 逐只数值'", "EV.confluence = {t:'7 只逐个核对'", "t.confluence"),
    ("EV.union = {t:'并集 278 只 · 分布'", "EV.union = {t:'278 只都是什么'", "t.union"),
    ("EV.margin = {t:'两融缺口 · 探测记录'", "EV.margin = {t:'深市两融为什么是空的'", "t.margin"),
    ("EV.pool = {t:'三池对比 · 为什么只能走 gate'", "EV.pool = {t:'三种筛法为什么只剩一种'", "t.pool"),
    ("EV.howto = {t:'四屏结构 · 阅读顺序'", "EV.howto = {t:'这份汇报怎么看'", "t.howto"),
    ("EV.signals = {t:'组合信号 149 条'", "EV.signals = {t:'另一套模型的 149 条信号'", "t.signals"),
    ("EV.risk = {t:'风控水位 · RiskGuard'", "EV.risk = {t:'风控怎么判的'", "t.risk"),
    ("EV.lineage = {t:'数据血缘 · 体检'", "EV.lineage = {t:'24 张数据表体检'", "t.lineage"),
    ("  EV['ds:'+x.n] = {t:x.d+' · '+x.n, h:function(){",
     "  EV['ds:'+x.n] = {t:x.d+'（'+x.n+'）', h:function(){", "t.ds"),
    ("  EV['chain:'+x.n] = {t:'链路 · '+x.n, h:function(){",
     "  EV['chain:'+x.n] = {t:x.n, h:function(){", "t.chain"),

    # ══════ confluence 表 ══════
    ("  var h = '<h3>三个条件同时成立的 7 只</h3><table><thead><tr>'\n"
     "    + '<th>代码/名称</th><th>涨跌</th><th>收盘÷60日低</th><th>净额÷流通市值</th></tr></thead><tbody>';",
     "  var h = '<h3>三条全中的 7 只</h3><table><thead><tr>'\n"
     "    + '<th>名称</th><th>当天涨跌</th><th>比60天最低高</th><th>大单净买占比</th></tr></thead><tbody>';",
     "confluence 表头"),

    ("      + '<td>'+(s.ratio!=null?s.ratio.toFixed(3):'—')+'</td>'",
     "      + '<td>'+(s.ratio!=null?((s.ratio-1)*100).toFixed(1)+'%':'—')+'</td>'",
     "confluence 比值改百分比"),

    ("    + '<div class=\"note blue\"><b>判据阈值。</b>收盘÷60日低 ≤ 1.05、净额÷流通市值 &gt; 0、'\n"
     "    + '当日公告含增持/回购/举牌。三条全绿才算共振，任一条不成立即剔除。</div>'",
     "    + '<div class=\"note blue\"><b>怎么算过。</b>收盘价不超过 60 天最低价的 1.05 倍、'\n"
     "    + '大单净买入为正、当天公告里有增持或回购或举牌。三条同时成立才留下，一条不成立就剔除。</div>'",
     "confluence 阈值说明"),

    # ══════ union ══════
    ("    + '<div class=\"kv\"><span>因子归属</span><b>CAP_C02 227 + NEWS_N01 58 − 重叠 7</b></div>';",
     "    + '<div class=\"kv\"><span>怎么来的</span><b>大钱在进 227 + 公司在托 58 − 都有 7</b></div>';",
     "union 因子归属"),

    ("    + '<div class=\"note\"><b>为什么不建议当篮子。</b>当日市场普跌，大量个股处在 60 日低位；'\n"
     "    + '而「资金净流入」这个门槛只要求为正——实测正值量级仅 0.02%~0.37%，'\n"
     "    + '几乎不构成二次筛选。278 只相当于筛出了五分之一个市场。</div>';",
     "    + '<div class=\"note\"><b>为什么不建议当篮子。</b>当天全市场普跌，一大堆股票都算「跌够了」；'\n"
     "    + '而「大钱在进」这条只要净额为正就算过——实测正的那点量只有 0.02%~0.37%，'\n"
     "    + '基本等于没筛。278 只就是五分之一的 A 股。</div>';",
     "union 说明"),

    # ══════ margin ══════
    ("  return '<h3>缺的是什么（仅股票口径）</h3>'",
     "  return '<h3>缺的到底是什么</h3>'", "margin 小标题"),

    ("    + '<div class=\"kv\"><span>11-06 两融非空</span><b>1679 只 · 全 SH</b></div>'\n"
     "    + '<div class=\"kv\"><span>深市 / 北交所</span><b style=\"color:var(--bad)\">0 / 0</b></div>'",
     "    + '<div class=\"kv\"><span>11-06 有数据的</span><b>1679 只 · 全是沪市</b></div>'\n"
     "    + '<div class=\"kv\"><span>深市 / 北交所</span><b style=\"color:var(--bad)\">0 / 0</b></div>'",
     "margin kv"),

    ("    + '<table><thead><tr><th>交易日</th><th>SZ</th><th>SH</th><th>BJ</th></tr></thead><tbody>'",
     "    + '<table><thead><tr><th>交易日</th><th>深市</th><th>沪市</th><th>北交所</th></tr></thead><tbody>'",
     "margin 表头"),

    ("    + '<h3>已经排除本地原因</h3>'",
     "    + '<h3>为什么能确定不是我们这边的问题</h3>'", "margin 小标题2"),

    # ══════ pool ══════
    ("  return '<h3>三套活池</h3><table><thead><tr><th>池</th><th>因子</th><th>依赖两融</th><th>11-06</th></tr></thead><tbody>'",
     "  return '<h3>三种筛法</h3><table><thead><tr><th>筛法</th><th>靠什么筛</th><th>要两融数据</th><th>今天结果</th></tr></thead><tbody>'",
     "pool 表头"),

    ("    + '<div class=\"note red\"><b>gate 不是「更好的口径」。</b>'\n"
     "    + 'pool_promoted.json 的组合层 OOS 检验把 CAP_C02 判为 rejected（理由：组合层 OOS 收益不为正）。'\n"
     "    + '它之所以被选中，纯粹是因为不含两融因子、在两融缺失这天仍能跑完整个市场。'\n"
     "    + '本次结果的有效性弱于已通过组合层检验的口径。</div>';",
     "    + '<div class=\"note red\"><b>它不是「更好的筛法」。</b>'\n"
     "    + '这套组合在历史检验里被判过不通过（理由：放到样本外，组合收益不为正）。'\n"
     "    + '今天之所以用它，纯粹是因为它不依赖两融数据，在两融缺失这天还能跑完整个市场。'\n"
     "    + '所以这次结果的可信度，低于那些通过了历史检验的筛法。</div>';",
     "pool 红框"),

    # ══════ 个股抽屉 ══════
    ("      + '<div class=\"num\" style=\"cursor:default\"><b>'+s.ratio.toFixed(3)+'</b><span>收盘 ÷ 60日低</span></div>'",
     "      + '<div class=\"num\" style=\"cursor:default\"><b>'+((s.ratio-1)*100).toFixed(1)+'%</b>"
     "<span>比 60 天最低<br>高多少</span></div>'",
     "个股卡比值"),

    ("      + '<div class=\"num\" style=\"cursor:default\"><b>'+money(s.net_mf_wan)+'</b><span>主力净流入</span></div>'",
     "      + '<div class=\"num\" style=\"cursor:default\"><b>'+money(s.net_mf_wan)+'</b><span>大单净买入</span></div>'",
     "个股卡净流入"),

    ("    h += '<h3>三条判据逐条核</h3>';",
     "    h += '<h3>三条为什么成立</h3>';", "个股判据标题"),

    ("      + '<div><b>① 60 日低位</b>收盘 '+s.close+' ÷ 60日最低 '+s.lo60+' = <b style=\"display:inline\">'\n"
     "      + s.ratio.toFixed(3)+'</b> ≤ 1.05 → 成立<br>'\n"
     "      + '<span style=\"color:var(--faint);font-size:11px\">60 日最低出现在 '+s.lo60_date+'</span></div></div>';",
     "      + '<div><b>① 跌够了</b>收盘 '+s.close+'，60 天里最低 '+s.lo60+'，'\n"
     "      + '现在比最低价高 <b style=\"display:inline\">'+((s.ratio-1)*100).toFixed(1)+'%</b>'\n"
     "      + '（不到 5% 就算过）<br>'\n"
     "      + '<span style=\"color:var(--faint);font-size:11px\">最低价出现在 '+s.lo60_date+'</span></div></div>';",
     "个股判据一"),

    ("      + '<div><b>② 主力资金净流入</b>净额 '+money(s.net_mf_wan)+' ÷ 流通市值 '\n"
     "      + (s.circ_mv_yi!=null?s.circ_mv_yi.toFixed(1)+' 亿':'—')+' = <b style=\"display:inline\">+'\n"
     "      + (s.mnn*100).toFixed(4)+'%</b> &gt; 0 → 成立</div></div>';",
     "      + '<div><b>② 大钱在进</b>今天大单净买入 '+money(s.net_mf_wan)+'，'\n"
     "      + '占流通市值 <b style=\"display:inline\">+'+(s.mnn*100).toFixed(4)+'%</b>'\n"
     "      + '（为正就算过；流通市值 '+(s.circ_mv_yi!=null?s.circ_mv_yi.toFixed(1)+' 亿':'—')+'）</div></div>';",
     "个股判据二"),

    ("      + '<div><b>③ 公告护盘</b>当日公告命中「增持/回购/举牌」'\n"
     "      + (s.anns.length?('共 '+s.anns.length+' 条'):'')+' → 成立</div></div>';",
     "      + '<div><b>③ 公司在托</b>当天公告里有「增持 / 回购 / 举牌」'\n"
     "      + (s.anns.length?('，共 '+s.anns.length+' 条'):'')+'</div></div>';",
     "个股判据三"),

    ("    h += '<h3>行情明细</h3>'", "    h += '<h3>当天行情</h3>'", "个股行情标题"),
    ("'<div class=\"kv\"><span>换手率</span>", "'<div class=\"kv\"><span>换手率</span>", "占位"),

    ("    h += '<h3>近 40 日收盘（黄点 = 区间最低）</h3>'",
     "    h += '<h3>最近 40 天收盘价（黄点 = 这段时间最低）</h3>'",
     "个股 spark 标题"),

    ("    } else { h += '<div class=\"src\">当日无命中公告。</div>'; }",
     "    } else { h += '<div class=\"src\">当天没有匹配的公告。</div>'; }",
     "个股无公告"),

    # ══════ ds 抽屉 ══════
    ("    var h = '<h3>11-06 覆盖</h3>'", "    var h = '<h3>11-06 覆盖情况</h3>'", "ds 小标题"),
    ("      + '<div class=\"kv\"><span>判定</span><b style=\"color:'+(x.ok?'var(--ok)':'var(--bad)')+'\">'",
     "      + '<div class=\"kv\"><span>结论</span><b style=\"color:'+(x.ok?'var(--ok)':'var(--bad)')+'\">'",
     "ds 判定"),
    ("      h += '<div class=\"note red\"><b>深市 + 北交所整段为空。</b>'\n"
     "        + '这不是「少了几只」，是深市个股一条都没有。所有依赖两融的因子（SENT 族）今天全部不可用。</div>';",
     "      h += '<div class=\"note red\"><b>深市和北交所整段是空的。</b>'\n"
     "        + '不是「少了几只」，是深市个股一条都没有。所有要用两融数据的筛法，今天全部用不了。</div>';",
     "ds 缺口说明"),
    ("      h += '<div class=\"note blue\"><b>覆盖完整。</b>四张行情/资金/筹码表的股票数在 5573~5579 之间，'\n"
     "        + '差异来自停牌与当日无成交，属正常范围。</div>';",
     "      h += '<div class=\"note blue\"><b>这类数据是全的。</b>四张行情/资金/筹码表的股票数在 5573~5579 之间，'\n"
     "        + '差的那几只来自停牌和当天没成交，属正常。</div>';",
     "ds 完整说明"),
    ("    h += '<h3>核验方式</h3><div class=\"src\">'\n"
     "      + '直读 parquet，<code>groupby(trade_date).size()</code> + '\n"
     "      + '<code>groupby(交易所)</code>，不经聚合层。</div>';",
     "    h += '<h3>怎么核的</h3><div class=\"src\">'\n"
     "      + '直接读原始数据文件，按交易日数条数、按交易所分组，'\n"
     "      + '不经过任何汇总层。<br><code>'+x.n+'</code></div>';",
     "ds 核验方式"),

    # ══════ chain 抽屉 ══════
    ("      h += '<div class=\"note\"><b>异常不等于数据错。</b>'\n"
     "        + '这里的 fail 大多是「计划任务查询不到」或「最近一次执行失败」，'\n"
     "        + '需要和当日实际产物比对才能定级。本日选股用的是已落盘的 parquet，不受此影响。</div>';",
     "      h += '<div class=\"note\"><b>报异常，不等于数据是错的。</b>'\n"
     "        + '这里多数是「定时任务查不到状态」或「上一次执行失败」，'\n"
     "        + '要跟当天实际落下来的文件比对才能定级。今天选股用的是已经存好的数据文件，不受影响。</div>';",
     "chain 异常说明"),
    ("    h += '<h3>来源</h3><div class=\"src\"><code>deliverables/daily_brief/brief_20261109.md</code><br>'\n"
     "      + '07:45 由 chain_watch 生成，本页只做投影不改写。</div>';",
     "    h += '<h3>来源</h3><div class=\"src\"><code>deliverables/daily_brief/brief_20261109.md</code><br>'\n"
     "      + '07:45 自动生成，本页只如实转述，不做改动。</div>';",
     "chain 来源"),

    # ══════ signals 抽屉 ══════
    ("  var h = '<h3>前 10 条（共 149 条）</h3><table><thead><tr><th>标的</th><th>方向</th><th>score</th><th>conf</th></tr></thead><tbody>';",
     "  var h = '<h3>前 10 条（共 149 条）</h3><table><thead><tr><th>标的</th><th>方向</th>"
     "<th>打分</th><th>把握度</th></tr></thead><tbody>';",
     "signals 表头"),
    ("    + '<div class=\"note\"><b>不要和 7 只共振混读。</b>'\n"
     "    + '这 149 条是策略组合层的信号（07:30 生成），与盘前因子选股的 7 只是两套独立口径：'\n"
     "    + '因子不同、池不同、执行假设也不同。重合个股需要单独比对。</div>'",
     "    + '<div class=\"note\"><b>别和上面那 7 只混着看。</b>'\n"
     "    + '这 149 条来自另一套组合策略（07:30 生成），和本页盘前选的 7 只是两条独立的路：'\n"
     "    + '用的指标不同、股票范围不同、买卖假设也不同。哪些重合要单独比。</div>'",
     "signals 说明"),

    # ══════ risk 抽屉 ══════
    ("  return '<h3>四条规则（影子模式）</h3><table><thead><tr><th>规则</th><th>放行</th><th>拦截</th><th>最新 score</th></tr></thead><tbody>'",
     "  return '<h3>四条风控规则（只看不改）</h3><table><thead><tr><th>规则</th><th>放行</th><th>拦下</th><th>风险分</th></tr></thead><tbody>'",
     "risk 表头"),
    ("    + '<tr><td>R1 市场质量</td>", "    + '<tr><td>R1 大盘质量</td>", "risk R1"),
    ("    + '<tr><td>R2 流动性冲击</td>", "    + '<tr><td>R2 流动性冲击</td>", "占位2"),
    ("    + '<div class=\"note\"><b>R2 / R4 放行数与拦截数都是 0，不等于「安全」。</b>'\n"
     "    + '影子模式下这两条规则当天没有被触发过，样本为 0 时任何「通过」判断都没意义。'\n"
     "    + 'R1 拦截 234 属于正常水位，市场级闸 pass。</div>';",
     "    + '<div class=\"note\"><b>R2 / R4 放行和拦下都是 0，不等于「安全」。</b>'\n"
     "    + '这两条规则当天压根没被触发过，样本是 0 的时候，「通过」这个结论没有意义。'\n"
     "    + 'R1 拦下 234 次属于正常水平，大盘这一关是放行的。</div>';",
     "risk 说明"),

    # ══════ lineage 抽屉 ══════
    ("    + '<div class=\"kv\"><span>在册表</span><b>24</b></div>'\n"
     "    + '<div class=\"kv\"><span>断链</span><b style=\"color:var(--ok)\">0</b></div>'\n"
     "    + '<div class=\"kv\"><span>陈旧</span><b style=\"color:var(--warn)\">4</b></div>'",
     "    + '<div class=\"kv\"><span>登记在册的数据表</span><b>24</b></div>'\n"
     "    + '<div class=\"kv\"><span>断供的</span><b style=\"color:var(--ok)\">0</b></div>'\n"
     "    + '<div class=\"kv\"><span>该更新没更新的</span><b style=\"color:var(--warn)\">4</b></div>'",
     "lineage kv"),
    ("    + '<h3>陈旧明细（均 6 个交易日未更新）</h3>'",
     "    + '<h3>这 4 张是哪些（都已 6 个交易日没动）</h3>'", "lineage 小标题"),
    ("    + '<div class=\"note\"><b>这 4 张和今天的选股无关。</b>'\n"
     "    + 'corpus 是知识库语料链，陈旧影响的是检索侧，不影响行情/资金/公告三类因子数据。</div>';",
     "    + '<div class=\"note\"><b>这 4 张和今天选股没关系。</b>'\n"
     "    + '它们是知识库的语料链，停更影响的是「查资料」那一侧，不影响行情、资金、公告这三类选股数据。</div>';",
     "lineage 说明"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    with open(TPL, "r", encoding="utf-8", newline="") as f:
        src = f.read()

    problems = []
    plan = []
    seen = {}
    for old, new, tag in REPL:
        if old == new:
            continue
        n = src.count(old)
        if n != 1:
            problems.append((tag, n, old[:70]))
        else:
            plan.append((old, new, tag))
            seen[tag] = n

    print("=" * 66)
    print("锚点检查：共 %d 条规则，唯一命中 %d 条" % (len(REPL), len(plan)))
    if problems:
        print("-" * 66)
        print("!! 以下锚点命中数不为 1，需人工修正：")
        for tag, n, head in problems:
            print("   [%d 次] %s  <= %r" % (n, tag, head))
    print("=" * 66)

    if a.dry:
        return 1 if problems else 0

    if problems:
        print("存在异常锚点，未改动文件。")
        return 2

    out = src
    for old, new, tag in plan:
        assert out.count(old) == 1, "替换时锚点已不唯一: %s" % tag
        out = out.replace(old, new, 1)
        assert new in out, "替换后新串不存在: %s" % tag

    # 全局禁词自检：正文层不应再出现这些内部符号
    banned_in_body = ["net_mf_amount", "circ_mv &gt; 0", "conf=0.70",
                      "GubaCrawl", "TdxSync", "KgDataDashboard", "RiskGuard 影子"]
    for b in banned_in_body:
        cnt = out.count(b)
        print("  禁词 %-18s 剩余 %d" % (b, cnt))

    with open(TPL + ".new", "w", encoding="utf-8", newline="") as f:
        f.write(out)

    print("已写出 %s（%d -> %d 字节）" % (TPL + ".new",
                                     len(src.encode()), len(out.encode())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
