/* 自动生成：tools/export_nav.py · 源=api_common.py DOMAIN_NAV_GROUPS
   勿手改本文件——改 api_common.py 导航数据后重跑导出器。*/
(function(){
/* 幂等护栏（2026-10-19 审计加固）：页面双引入 <script src=nav.js> 时
   （f7170225 实录缺陷：两个顶栏叠加），第二个 IIFE 直接返回。
   标志落 documentElement 属性而非 var——跨 IIFE 实例必须可见。 */
if(document.documentElement.getAttribute('data-wb-nav')==='1')return;
document.documentElement.setAttribute('data-wb-nav','1');
var NAV={"sections": [["quant", "📈 量化", "/quant"], ["intel", "🕵️ 情报", "/intel"], ["creator", "🎬 创作", "/creator"], ["dev", "💻 开发", "/dev"], ["ops", "⚙️ 系统", "/ops"]], "groups": {"quant": [["🌅 盘前与驾驶", [["📺 数据大屏", "/screen", "screen"], ["🌅 盘前", "/premarket", "pre"], ["🌇 盘后复盘", "/afterhours", "afterhours"], ["💰 金融驾驶舱", "/fin", "fin"], ["🎯 量化驾驶舱", "/quant-drill", "quantdrill"], ["🔍 个股分析", "/sa", "sa"], ["💬 查数", "/nl", "nl"]]], ["🚀 指挥", [["🚀 驾驶舱", "/bridge", "bridge"], ["🦸 超级助手", "/hermes/", "hermes"], ["🐴 CowAgent", "http://127.0.0.1:9899", "cowagent"], ["🧠 智策 OS", "http://127.0.0.1:5175", "zhice"], ["🤖 AI 市场", "/aim", "aim"], ["🛒 算法市场", "/algo", "algo"], ["🏛 组织架构 🚧", "/org", "org"]]], ["🎖 策略资产", [["🎖 策略中心", "/strategies", "strategies"], ["🌊 青萍之末 🚧", "/bt-qpzm", "qpzm"], ["🔔 订阅推送", "/bt/subscribe", "subscribe"], ["🧭 缠论", "/bt-chanlun", "chanlun"], ["🧪 TBE 分层", "/bt-tbe", "tbe"], ["⚔ 量子双刀", "/qd/", "qd"], ["🛟 预警池", "/bt-chanlun-pool", "chanlun-pool"], ["🔗 三线联动", "/bt-linkage", "linkage"], ["📚 弱于板块案例", "/bt-weak", "cases"], ["🧮 九维", "/bt-9dim", "9dim"], ["🎯 共振", "/bt-cross", "cross"], ["🏛 投委会", "/council", "council"], ["♠ 操盘锦标赛", "/sim", "sim"], ["🎤 辩论室", "/debate", "debate"]]], ["🚦 实盘跟踪", [["🎯 今日选股", "/picks.html", "picks"], ["🚦 信号", "/bt-signals", "signals"], ["💼 持仓", "/bt-positions", "positions"], ["🧾 模拟仓", "/paper", "paper"], ["🛒 策略市场 🚧", "/market-strategy", "market-strategy"], ["📊 绩效市场 🚧", "/market", "market"], ["📄 报告", "/bt-report", "report"], ["🎯 卖点研判", "/sellpoint", "sellpoint"]]], ["🧰 研究与数据", [["🎯 选股中心", "/bt", "bt"], ["⚔ 板块强弱", "/strong", "strong"], ["🌾 期货量化", "/fut", "fut"], ["📊 寻优", "/bt-opt", "opt"], ["🔥 板块", "/bt-sectors", "sectors"], ["🗄 数据仓", "/bt-data-health", "data-health"]]]], "intel": [["📡 情报流", [["🏠 情报工作台", "/intel", "intel"], ["📰 新闻证据流", "/intel/stream", "intelstream"], ["🤖 AI 晨报", "/bt-brief", "brief"], ["⚖️ JEV 判断", "/bt-jev", "jev"], ["🩺 四件套观测", "/stack", "jevstack"]]], ["🧪 雪球舆情", [["📰 情报日报", "/xq_intel.html", "xqintel"], ["📊 数据日报", "/data_daily.html", "dailydata"], ["📰 新闻情报", "/news_intel.html", "newsintel"]]], ["🕸 知识图谱", [["🎯 知识画布", "/kg/canvasv2", "kgcanvas"], ["🗺 作战地图", "/kg/battle", "kgbattle"], ["📰 事件图谱", "/kg/events", "kgevents"], ["🕸 图谱探索", "/kg", "kg"], ["💬 图谱问答", "/kg/ask", "kgask"], ["💰 游资地图", "/kg/map", "kgmap"], ["🧨 板块工单", "/kg/flow", "kgflow"], ["🖊 画图标注", "/kg/label", "kglabel"], ["🃏 推演卡", "/kg/card", "kgcard"], ["👁 影子面板", "/kg/shadow", "kgshadow"], ["🏢 公司 360", "/kg/company", "kgcompany"], ["📚 图谱词条", "/kg/lexicon", "kglexicon"], ["📄 图谱文档", "/kg/doc", "kgdoc"]]], ["🧬 数据血缘", [["🧬 血缘总览", "/lineage.html", "lineage"]]], ["💼 情报变现", [["🛰 推送中台", "/push/", "pushhub"], ["📨 通知指挥", "/notify-command", "notifycmd"], ["📋 运维对账", "/ops-board", "opsboard"], ["🔐 我的订阅", "/my-subscription", "mysub"], ["📊 订阅运营", "/sub-admin", "subadmin"]]]], "creator": [[null, [["🎬 创作专项", "/creator", "creator"], ["📝 公众号草稿", "/gzh", "gzh"], ["🎞 短视频成片", "/vd", "vd"]]]], "dev": [[null, [["💻 开发者专项", "/dev", "dev"]]], ["🎓 零界学院", [["🎓 零界学院", "/academy", "academy"]]]], "ops": [["🛰 数据运营", [["🎛 数据指挥台", "/dataops", "dataops"], ["🗄 数据仓", "/dw.html", "dw"]]], ["🪖 军团", [["🏛 组织架构 🚧", "/org", "org"], ["✅ 审批注册表", "/approvals", "approvals"], ["🧩 能力看板", "/capability", "capability"], ["📈 投研绩效", "/perf", "perf"], ["🛡 审计状态", "/audit", "audit"]]], ["⚙️ 系统运维", [["🖥 个人 OS 入口", "/os-entry", "os-entry"], ["🖥 指挥大屏", "/os-board", "os-board"], ["📚 学习成长", "/learn", "learn"], ["🛰 助理雷达", "/radar", "radar"], ["📊 方法库", "/methods", "methods"], ["📊 数据仪表盘", "/dashboard", "dashboard"], ["🎛 OS 控制台", "/ops", "opshome"], ["⚡ 动作流中心", "/flow", "flow"], ["🖥 系统自检", "/sys-health", "sys"]]]]}};
window.__WB_NAV=NAV;
var CSS='/static/nav.css';
if(!document.querySelector('link[data-wb-nav-css]')){
 var lk=document.createElement('link');lk.rel='stylesheet';lk.href=CSS;lk.setAttribute('data-wb-nav-css','1');document.head.appendChild(lk);
}
function grp(label,items,cur){
 var h='';
 if(label)h+='<div class="wb-nav-group-label">'+label+'</div>';
 h+='<div class="wb-nav-group-links">';
 items.forEach(function(it){
  var on=cur&&it[2]===cur;
  h+=on?'<span class="wb-nav-current">'+it[0]+'</span>':'<a class="wb-nav-link" href="'+it[1]+'">'+it[0]+'</a>';
 });
 return h+'</div>';
}
document.addEventListener('DOMContentLoaded',function(){
 var path=location.pathname.replace(/\.html$/,'');
 var cur=null,sec=NAV.sections[0][0];
 NAV.sections.forEach(function(s){
  (NAV.groups[s[0]]||[]).forEach(function(g){
   g[1].forEach(function(it){
    var p=it[1].replace('http://127.0.0.1','').replace(/\.html$/,'');
    if(p===path||p+'/'===path+'/'||(p!=='/'&&path.indexOf(p)===0&&it[1].charAt(0)==='/')){cur=it[2];sec=s[0];}
   });
  });
 });
 var bar=document.createElement('div');bar.className='wb-nav';
 var todayOn=(path==='/'||path==='/home_v3.html');
 bar.innerHTML='<a class="wb-nav-pill wb-nav-today'+(todayOn?' is-active':'')+'" href="/">🏠 今日</a>';
 NAV.sections.forEach(function(s){
  var on=cur&&sec===s[0];
  var dd='<details class="wb-nav-details"><summary class="wb-nav-pill'+(on?' is-active':'')+'">'+s[1]+'<span class="wb-nav-caret"> ▾</span></summary><div class="wb-nav-dropdown">'+(NAV.groups[s[0]]||[]).map(function(g){return grp(g[0],g[1],cur);}).join('')+'</div></details>';
  bar.innerHTML+=dd;
 });
 if(path!=='/'&&path!=='/index.html'){
  var backBtn=document.createElement('a');
  backBtn.href='javascript:void(0)';
  backBtn.title='返回上一页';
  backBtn.className='wb-nav-back';
  backBtn.textContent='← 返回';
  backBtn.addEventListener('click',function(){
   if(history.length>1){history.back();}else{location.href='/';}});
  bar.insertBefore(backBtn,bar.firstChild);
  var sep0=document.createElement('span');
  sep0.className='wb-nav-sep';
  sep0.textContent='|';
  bar.insertBefore(sep0,bar.firstChild);
 }
 var skip=document.querySelector('.wb-skip');
 if(skip&&skip.parentNode===document.body){document.body.insertBefore(bar,skip.nextSibling);}
 else{document.body.insertBefore(bar,document.body.firstChild);}
 if(!document.querySelector('link[data-wb-palette-css]')){
  var pl=document.createElement('link');pl.rel='stylesheet';pl.href='/static/ui_palette.css';pl.setAttribute('data-wb-palette-css','1');document.head.appendChild(pl);
 }
 if(!document.querySelector('script[data-wb-palette]')){
  var ps=document.createElement('script');ps.src='/static/ui_palette.js';ps.defer=true;ps.setAttribute('data-wb-palette','1');document.head.appendChild(ps);
 }
 if(!document.querySelector('link[data-wb-shell-css]')){
  var sl=document.createElement('link');sl.rel='stylesheet';sl.href='/static/page_shell.css';sl.setAttribute('data-wb-shell-css','1');document.head.appendChild(sl);
 }
 if(!document.querySelector('script[data-wb-shell]')){
  var ss=document.createElement('script');ss.src='/static/page_shell.js';ss.defer=true;ss.setAttribute('data-wb-shell','1');document.head.appendChild(ss);
 }
});
})();
