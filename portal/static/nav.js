/* 自动生成：tools/export_nav.py · 源=portal_api.py DOMAIN_NAV_GROUPS
   勿手改本文件——改 portal_api.py 导航数据后重跑导出器。*/
(function(){
var NAV={"sections": [["quant", "📈 量化", "/quant"], ["intel", "🕵️ 情报", "/intel"], ["creator", "🎬 创作", "/creator"], ["dev", "💻 开发", "/dev"], ["ops", "⚙️ 系统", "/ops"]], "groups": {"quant": [["🌅 盘前与驾驶", [["📺 数据大屏", "/screen", "screen"], ["🛰 大屏信号", "/screen/sig", "screensig"], ["🌅 盘前", "/premarket", "pre"], ["🌇 盘后复盘", "/afterhours", "afterhours"], ["🏠 驾驶舱", "/fin", "fin"], ["🔍 个股分析", "/sa", "sa"], ["💬 查数", "/nl", "nl"]]], ["🚀 指挥", [["🗺 门户", "/portal", "portal"], ["🚀 驾驶舱", "/bridge", "bridge"], ["⚡ 驾驶舱行动", "/bridge/act", "bridgeact"], ["🦸 超级助手", "http://127.0.0.1:8787", "hermes"], ["🐴 CowAgent", "http://127.0.0.1:9899", "cowagent"], ["🧠 智策 OS", "http://127.0.0.1:5175", "zhice"], ["🤖 AI 市场", "/aim", "aim"], ["🛒 算法市场", "/algo", "algo"], ["🏛 组织架构", "/org", "org"]]], ["🎖 策略资产", [["🎖 策略中心", "/strategies", "strategies"], ["🌊 青萍之末", "/bt-qpzm", "qpzm"], ["🔔 订阅推送", "/bt/subscribe", "subscribe"], ["🧭 缠论", "/bt-chanlun", "chanlun"], ["⚔ 量子双刀", "/qd/", "qd"], ["🛟 预警池", "/bt-chanlun-pool", "chanlun-pool"], ["🔗 三线联动", "/bt-linkage", "linkage"], ["📚 弱于板块案例", "/bt-weak", "cases"], ["🧮 九维", "/bt-9dim", "9dim"], ["🎯 共振", "/bt-cross", "cross"], ["🏛 投委会", "/council", "council"], ["♠ 操盘锦标赛", "/sim", "sim"], ["🎤 辩论室", "/debate", "debate"]]], ["🚦 实盘跟踪", [["🚦 信号", "/bt-signals", "signals"], ["💼 持仓", "/bt-positions", "positions"], ["🧾 模拟仓", "/paper", "paper"], ["🛒 策略市场", "/market-strategy", "market-strategy"], ["📊 绩效市场", "/market", "market"], ["📄 报告", "/bt-report", "report"], ["🎯 卖点研判", "/sellpoint", "sellpoint"]]], ["🧰 研究与数据", [["🎯 选股中心", "/bt", "bt"], ["🌾 期货量化", "/fut", "fut"], ["📊 寻优", "/bt-opt", "opt"], ["🔥 板块", "/bt-sectors", "sectors"], ["🗄 数据仓", "/bt-data-health", "data-health"]]]], "intel": [["📡 情报流", [["🏠 情报工场", "/intel", "intel"], ["🤖 AI 晨报", "/bt-brief", "brief"], ["⚖️ JEV 判断", "/bt-jev", "jev"]]], ["🕸 知识图谱", [["🎯 知识画布", "/kg/canvasv2", "kgcanvas"], ["🗺 作战地图", "/kg/battle", "kgbattle"], ["📰 事件图谱", "/kg/events", "kgevents"], ["🕸 图谱探索", "/kg", "kg"], ["💬 图谱问答", "/kg/ask", "kgask"], ["💰 游资地图", "/kg/map", "kgmap"], ["🧨 板块工单", "/kg/flow", "kgflow"], ["🖊 画图标注", "/kg/label", "kglabel"], ["🃏 推演卡", "/kg/card", "kgcard"], ["👁 影子面板", "/kg/shadow", "kgshadow"], ["🏢 公司 360", "/kg/company", "kgcompany"], ["📚 图谱词条", "/kg/lexicon", "kglexicon"], ["📄 图谱文档", "/kg/doc", "kgdoc"]]]], "creator": [[null, [["🎬 创作专项", "/creator", "creator"], ["📝 公众号草稿", "/gzh", "gzh"], ["🎞 短视频成片", "/vd", "vd"]]]], "dev": [[null, [["💻 开发者专项", "/dev", "dev"]]]], "ops": [[null, [["🎛 OS 控制台", "/ops", "opshome"], ["🛡 审计状态", "/audit", "audit"], ["🖥 系统自检", "/sys-health", "sys"]]]]}};
var PILL='display:inline-block;padding:6px 13px;border:1px solid #30363d;border-radius:999px;color:#8b949e;font-size:12.5px;text-decoration:none;background:#0d1117';
var PILL_ON=PILL.slice(0,-1)+';color:#e6edf3;border-color:#58a6ff;font-weight:600';
function grp(label,items,cur){
 var h='';
 if(label)h+='<div style="color:#8b949e;font-size:10px;letter-spacing:1px;margin:5px 0 3px">'+label+'</div>';
 h+='<div style="display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12.5px;line-height:2">';
 items.forEach(function(it){
  var on=cur&&it[2]===cur;
  h+=on?'<span style="color:#e6edf3;font-weight:600">'+it[0]+'</span>':'<a href="'+it[1]+'" style="color:#58a6ff;text-decoration:none">'+it[0]+'</a>';
 });
 return h+'</div>';
}
document.addEventListener('DOMContentLoaded',function(){
 var path=location.pathname.replace(/\.html$/,'');
 var cur=null,sec=NAV.sections[0][0];
 NAV.sections.forEach(function(s){
  (NAV.groups[s[0]]||[]).forEach(function(g){
   g[1].forEach(function(it){
    var p=it[1].replace('http://127.0.0.1','');
    if(p===path||p+'/'===path+'/'||(p!=='/'&&path.indexOf(p)===0&&it[1].charAt(0)==='/')){cur=it[2];sec=s[0];}
   });
  });
 });
 var bar=document.createElement('div');
 bar.style.cssText='position:sticky;top:0;z-index:99;display:flex;align-items:center;gap:7px;flex-wrap:wrap;padding:9px 14px;background:rgba(13,17,23,.92);border-bottom:1px solid #30363d;backdrop-filter:blur(6px)';
 var FLOW=[['🤖 晨报','/bt-brief'],['🌅 盘前','/premarket'],['🚦 信号','/bt-signals'],['📺 大屏','/screen'],['🌇 盘后','/afterhours'],['🧾 模拟仓','/paper']];
 var flow='<span style="color:#d29922;font-size:11px;letter-spacing:1px;font-weight:600">⚡ 动线</span>';
 FLOW.forEach(function(f){
  var on=path===f[1];
  flow+='<a href="'+f[1]+'" style="'+(on?PILL_ON.replace('#58a6ff','#d29922'):PILL)+'">'+f[0]+'</a>';
 });
 bar.innerHTML=flow+'<span style="color:#30363d;margin:0 4px">|</span><a href="/portal" style="'+PILL+'">🏛 门户</a>';
 NAV.sections.forEach(function(s){
  var on=cur&&sec===s[0];
  var dd='<details style="position:relative;display:inline-block"><summary style="'+(on?PILL_ON:PILL)+'";cursor:pointer;list-style:none">'+s[1]+'<span style="font-size:9px"> ▾</span></summary><div style="position:absolute;left:0;top:calc(100% + 6px);z-index:100;background:#161b22;border:1px solid #30363d;border-radius:10px;padding:8px 14px 10px;min-width:320px;box-shadow:0 10px 28px rgba(0,0,0,.45)">'+(NAV.groups[s[0]]||[]).map(function(g){return grp(g[0],g[1],cur);}).join('')+'</div></details>';
  bar.innerHTML+=dd;
 });
 document.body.insertBefore(bar,document.body.firstChild);
});
})();
