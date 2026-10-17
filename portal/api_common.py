# -*- coding: utf-8 -*-
"""P4 批 0：portal 共享底座（helper+方案A+状态锁/常量+导航族+tp共享+_sched判色）。
由批 0 施工脚本从 portal_api.py 结构边界剪切生成；唯一实例（锁/缓存单例）。
消费方必须显式 import（import * 不导出下划线名）。
"""
import datetime, glob, json, os, re, subprocess, sys, threading, threading as _th, time

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



# ===== P0-5 静默吞错落盘（2026-10-08 全检修复）=====
# 背景：全检报告 P0-5——258 处 `except Exception` 多为 pass，数据读取失败
# 被静默兜底，故障态看起来像正常态。本批先把最高频的 20 处读取/DB 兜底层
# （bridge_summary 12 + bridge_decisions 7 + _news_db 1）接入落盘日志，
# 只加日志不改兜底逻辑；其余 200+ 处后续分批跟进。
_ERROR_LOG = r"C:\Temp\portal_api_error.log"


# C6b（研发计划 v2）：日志按大小单代轮转阈值——access/error 两处同款
_ERROR_LOG_MAX = 50 * 1024 * 1024   # 50MB


# ===== P0-1b 写端点共享 token 鉴权（2026-10-08 全检修复）=====
# 威胁模型：8091 已绑 127.0.0.1（P0-1a），局域网攻击者只能经 ai-portal
# nginx(8080) 反代进入。nginx 统一注入 X-Portal-Proxy: 1 标记「来自反代」，
# 并以 X-Real-IP 传真实客户端 IP（nginx 覆写，客户端不可伪造）：
#   - 本机直连 8091（无标记）→ 本机可信域，放行（本机等价于能读 token 文件）
#   - 经反代且 X-Real-IP=127.0.0.1 → 本机浏览器，放行且页面注入 token
#   - 经反代且 X-Real-IP=局域网 → 变更类 POST 必须带匹配的 X-Portal-Token
# token 来源：环境变量 PORTAL_API_TOKEN 优先；否则 C:\Temp\portal_api_token
# （不落 portal/ 下——该目录整挂为 nginx web root，落盘即等于局域网可直接
# GET 下载，鉴权形同虚设）；两者皆无则首次自动生成 64 位 hex 落盘。
_PORTAL_TOKEN_FILE = r"C:\Temp\portal_api_token"


ROOT = os.path.dirname(os.path.abspath(__file__))      # ...\portal


STATION = os.path.dirname(ROOT)                        # E:\老田的AI助手


# ── 板块驾驶舱数据 helpers ─────────────────────────────
REPORTS_DIR = os.path.join(STATION, "quant_backtest", "reports")


QBT_DIR = os.path.join(STATION, "quant_backtest")   # 缠论 P1 数据底座模块


# ── 板块 IA（L0 领域入口板 → L1 领域专项 → L2 工具页）──────────
# K38 分类：第一行固定「首页 + 五领域」（当前领域高亮），第二行当前领域子页分组。
# 量化 19 页四组旅程式（盘前与驾驶/策略资产/实盘跟踪/研究与数据），
# 情报 9 页两组（读情报的「情报流」/ 做研究的「知识图谱」），其余领域平铺。
# 现有工具页 URL 全部不动（飞书卡片历史链接不破坏），只重组入口归属。
SECTIONS = [
    ("quant", "📈 量化", "/quant"),
    ("intel", "🕵️ 情报", "/intel"),
    ("creator", "🎬 创作", "/creator"),
    ("dev", "💻 开发", "/dev"),
    ("ops", "⚙️ 系统", "/ops"),
]


DOMAIN_NAV_GROUPS = {
    "quant": (
        ("🌅 盘前与驾驶", (
            ("📺 数据大屏", "/screen", "screen"),
            ("🌅 盘前", "/premarket", "pre"),
            ("🌇 盘后复盘", "/afterhours", "afterhours"),
            ("💰 金融驾驶舱", "/fin", "fin"),
            ("🔍 个股分析", "/sa", "sa"),
            ("💬 查数", "/nl", "nl"))),
        ("🚀 指挥", (
            ("🚀 驾驶舱", "/bridge", "bridge"),
            ("🦸 超级助手", "/hermes/", "hermes"),
            ("🐴 CowAgent", "http://127.0.0.1:9899", "cowagent"),
            ("🧠 智策 OS", "http://127.0.0.1:5175", "zhice"),
            ("🤖 AI 市场", "/aim", "aim"),
            ("🛒 算法市场", "/algo", "algo"),
            ("🏛 组织架构 🚧", "/org", "org"))),
        ("🎖 策略资产", (
            ("🎖 策略中心", "/strategies", "strategies"),
            ("🌊 青萍之末 🚧", "/bt-qpzm", "qpzm"),
            ("🔔 订阅推送", "/bt/subscribe", "subscribe"),
            ("🧭 缠论", "/bt-chanlun", "chanlun"),
            ("🧪 TBE 分层", "/bt-tbe", "tbe"),
            ("⚔ 量子双刀", "/qd/", "qd"),
            ("🛟 预警池", "/bt-chanlun-pool", "chanlun-pool"),
            ("🔗 三线联动", "/bt-linkage", "linkage"),
            ("📚 弱于板块案例", "/bt-weak", "cases"),
            ("🧮 九维", "/bt-9dim", "9dim"),
            ("🎯 共振", "/bt-cross", "cross"),
            ("🏛 投委会", "/council", "council"),
            ("♠ 操盘锦标赛", "/sim", "sim"),
            ("🎤 辩论室", "/debate", "debate"))),
        ("🚦 实盘跟踪", (
            ("🎯 今日选股", "/picks.html", "picks"),
            ("🚦 信号", "/bt-signals", "signals"),
            ("💼 持仓", "/bt-positions", "positions"),
            ("🧾 模拟仓", "/paper", "paper"),
            ("🛒 策略市场 🚧", "/market-strategy", "market-strategy"),
            ("📊 绩效市场 🚧", "/market", "market"),
            ("📄 报告", "/bt-report", "report"),
            ("🎯 卖点研判", "/sellpoint", "sellpoint"))),
        ("🧰 研究与数据", (
            ("🎯 选股中心", "/bt", "bt"),
            ("⚔ 板块强弱", "/strong", "strong"),
            ("🌾 期货量化", "/fut", "fut"),
            ("📊 寻优", "/bt-opt", "opt"),
            ("🔥 板块", "/bt-sectors", "sectors"),
            ("🗄 数据仓", "/bt-data-health", "data-health"))),
    ),
    "intel": (
        ("📡 情报流", (
            ("🏠 情报工场", "/intel", "intel"),
            ("🤖 AI 晨报", "/bt-brief", "brief"),
            ("⚖️ JEV 判断", "/bt-jev", "jev"),
            ("🩺 四件套观测", "/stack", "jevstack"))),
        ("🕸 知识图谱", (
            ("🎯 知识画布", "/kg/canvasv2", "kgcanvas"),
            ("🗺 作战地图", "/kg/battle", "kgbattle"),
            ("📰 事件图谱", "/kg/events", "kgevents"),
            ("🕸 图谱探索", "/kg", "kg"),
            ("💬 图谱问答", "/kg/ask", "kgask"),
            ("💰 游资地图", "/kg/map", "kgmap"),
            ("🧨 板块工单", "/kg/flow", "kgflow"),
            ("🖊 画图标注", "/kg/label", "kglabel"),
            ("🃏 推演卡", "/kg/card", "kgcard"),
            ("👁 影子面板", "/kg/shadow", "kgshadow"),
            ("🏢 公司 360", "/kg/company", "kgcompany"),
            ("📚 图谱词条", "/kg/lexicon", "kglexicon"),
            ("📄 图谱文档", "/kg/doc", "kgdoc"))),
        ("💼 情报变现", (
            ("🛰 推送中台", "/push/", "pushhub"),)),
    ),
    "creator": ((None, (
        ("🎬 创作专项", "/creator", "creator"),
        ("📝 公众号草稿", "/gzh", "gzh"),
        ("🎞 短视频成片", "/vd", "vd"))),),
    "dev": (
        (None, (("💻 开发者专项", "/dev", "dev"),)),
        ("🎓 零界学院", (("🎓 零界学院", "/academy", "academy"),)),
    ),
    "ops": ((None, (
        ("🎛 OS 控制台", "/ops", "opshome"),
        ("⚡ 动作流中心", "/flow", "flow"),
        ("🛡 审计状态", "/audit", "audit"),
        ("🖥 系统自检", "/sys-health", "sys"))),),
}


SECTION_PAGES = {k: [it for _, its in groups for it in its]
                 for k, groups in DOMAIN_NAV_GROUPS.items()}


PAGE_SECTION = {"fin": "quant", "sa": "quant", "pre": "quant", "bt": "quant",
                "afterhours": "quant",
                "flow": "ops",
                "strategies": "quant", "screen": "quant",
                "qpzm": "quant", "9dim": "quant", "chanlun": "quant",
                "subscribe": "quant",
                "chanlun-pool": "quant", "cross": "quant",
                "data-health": "quant", "nl": "quant",
                "report": "quant", "signals": "quant", "positions": "quant",
                "opt": "quant", "sectors": "quant", "cases": "quant",
                "linkage": "quant", "council": "quant", "sim": "quant",
                "debate": "quant", "bridge": "quant", "fut": "quant",
                "sellpoint": "quant", "paper": "quant",
                "aim": "quant",
                "intel": "intel",
                "pushhub": "intel",
                "jev": "intel", "brief": "intel", "kg": "intel",
                "jevstack": "intel",
                "kgcanvas": "intel", "kgask": "intel",
                "kgmap": "intel", "kgshadow": "intel",
                "kgbattle": "intel",
                "kgevents": "intel",
                "kgflow": "intel", "kglabel": "intel", "kgcard": "intel",
                "gzh": "creator", "vd": "creator",
                "sys": "ops", "audit": "ops"}


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


DATA_SYNC_CMDS = {
    "tdx": ["C:\\Program Files\\Python311\\python.exe", "-X", "utf8",
            os.path.join(QBT_DIR, "tdx_sync.py")],      # 默认增量
    "tushare": ["C:\\Program Files\\Python311\\python.exe", "-X", "utf8",
                os.path.join(QBT_DIR, "tushare_sync.py"), "--incremental"],
    "kg": ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", os.path.join(STATION, "quant_backtest",
                                 "start_kg_sync.ps1")],
    "catalog": ["C:\\Program Files\\Python311\\python.exe", "-X", "utf8",
                os.path.join(QBT_DIR, "build_catalog.py")],
    "alertpush": ["C:\\Program Files\\Python311\\python.exe", "-X", "utf8",
                  os.path.join(STATION, "news_collector", "alert_push.py")],
}


# ── 数据底座：时点校对 + 手动同步（数据仓页按钮后端）────────
# src → 启动命令；同一 src 单实例（重复点击拒绝），日志 C:\Temp
DATA_SYNC_JOBS = {}                                  # src -> {p, log}


_DATA_LOCK = threading.Lock()


_FRESH_BG_LOCK = threading.Lock()


_FRESH_BG_ERROR = None


# ── K44 数据大屏：策略选股 × 市场 × 行业 × 账本成绩 一屏聚合 ──
_screen_cache = {"t": 0.0, "d": None}


_ENV_MKT = (("A股", "cn"), ("美股", "us"), ("港股", "hk"), ("加密", "crypto"))


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


# SCHED_S_* 调度器信息码（0x41300~）——非故障，按语义呈现
_SCHED_INFO_CODES = {
    267008: ("就绪", "#8b949e"),      # SCHED_S_TASK_READY
    267009: ("运行中", "#d29922"),    # SCHED_S_TASK_RUNNING
    267010: ("未排程", "#8b949e"),    # SCHED_S_TASK_NOT_SCHEDULED
    267011: ("尚未运行", "#8b949e"),  # SCHED_S_TASK_HAS_NOT_RUN（新任务首跑前常态）
    267012: ("不再排程", "#8b949e"),  # SCHED_S_TASK_NO_MORE_RUNS
    267013: ("未终止", "#d29922"),    # SCHED_S_TASK_NOT_TERMINATED
    267014: ("无触发器", "#f85149"),  # SCHED_S_TASK_NO_VALID_TRIGGERS（真问题）
}


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _env_market_key(label):
    for k, m in _ENV_MKT:
        if k in str(label):
            return m
    return None


def pill_nav(page=None):
    """K39 面包屑导航：🏠 首页 › 域 › 当前页 + 右侧「☰ 域内 ▾」折叠下拉。
    域内目录职责收归域首页（/quant 卡片区 / intel 工具场），
    子页顶部不再平铺胶囊——用户判例：一盘胶囊=复杂凌乱。"""
    sec = PAGE_SECTION.get(page, "quant")
    if sec not in SECTION_PAGES:
        sec = "quant"
    dom = {k: (lb, h) for k, lb, h in SECTIONS}[sec]
    cur = next(((lb, href) for lb, href, key in SECTION_PAGES[sec]
                if key == page), None)
    sep = "<span style='color:#8b949e;font-size:11px'>›</span>"
    crumb = ("<a href='/portal' style='%s'>🏛 门户</a>%s"
             "<a href='%s' style='%s'>%s</a>"
             % (_PILL, sep, dom[1], _PILL, dom[0]))
    if cur and cur[1] != dom[1]:
        crumb += "%s<span style='%s'>%s</span>" % (sep, _PILL_ON, cur[0])
    # 域内下拉：分组列表（组名 None 的领域不显示组头），当前页加粗
    def _grp(label, items):
        links = "".join(
            "<span style='color:#e6edf3;font-weight:600'>%s</span>" % lb
            if key == page else
            "<a href='%s' style='color:#58a6ff;text-decoration:none'>%s</a>"
            % (href, lb) for lb, href, key in items)
        head = ("<div style='color:#8b949e;font-size:10px;"
                "letter-spacing:1px;margin:5px 0 3px'>%s</div>" % label
                if label else "")
        return head + ("<div style='display:flex;flex-wrap:wrap;"
                       "gap:4px 14px;font-size:12.5px;line-height:2'>"
                       "%s</div>" % links)
    menu = "".join(_grp(lb, its) for lb, its in DOMAIN_NAV_GROUPS[sec])
    dd = ("<details style='position:relative;display:inline-block'>"
          "<summary style='%s;cursor:pointer;list-style:none;"
          "display:inline-flex;align-items:center;gap:5px'>☰ 域内导航 "
          "<span style='font-size:9px'>▾</span></summary>"
          "<div style='position:absolute;right:0;top:calc(100%% + 6px);"
          "z-index:60;background:#161b22;border:1px solid #30363d;"
          "border-radius:10px;padding:8px 14px 10px;min-width:320px;"
          "box-shadow:0 10px 28px rgba(0,0,0,.45)'>%s</div></details>"
          % (_PILL, menu))
    return ("<script src=\"/static/nav.js\" defer></script>"
            "<div style='display:flex;align-items:center;"
            "justify-content:space-between;gap:10px;flex-wrap:wrap;"
            "margin:0 0 18px'>"
            "<div style='display:flex;align-items:center;gap:7px;"
            "flex-wrap:wrap'>%s%s</div>%s</div>"
            % (_back_link_html(), crumb, dd))


def _tabs_html(tid, tabs, checked=0):
    """K-STOCK-030 通用 Tab 页组件（选股中心长页治理：市场维度点右侧
    Tab 切换，不再竖排全量展开）。tabs=[(label, content_html)]。
    无 JS 降级：noscript 时展开全部（style 由 JS 首次切换才生效，
    初始 pane 用内联 display 控制默认只显示 checked 页）。"""
    n = len(tabs)
    btns, panes = [], []
    for i, (lb, ct) in enumerate(tabs):
        on = (i == checked)
        btns.append("<button id='%s-b%d' onclick=\"_tabSw('%s',%d,%d)\" "
                    "style='%s%s'>%s</button>"
                    % (tid, i, tid, i, n, _TABBTN,
                       ";background:#58a6ff;color:#fff" if on else "",
                       lb))
        panes.append("<div id='%s-p%d' style='display:%s'>%s</div>"
                     % (tid, i, "block" if on else "none", ct))
    return (_TAB_JS
            + "<div style='display:flex;gap:6px;flex-wrap:wrap;margin:8px 0'>"
            + "".join(btns) + "</div>"
            + "".join(panes))


def _back_link_html():
    """K-STOCK-036 统一返回键单链接（模块级：pill_nav 与各产线页共用。
    有浏览历史 → history.back() 回真实来源页；无历史兜底 /portal 门户）"""
    return ("<a href='/portal' onclick=\"if(history.length>1){"
            "history.back();return false;}\" style='display:"
            "inline-block;padding:6px 14px;border:1px solid #30363d;"
            "border-radius:16px;color:#58a6ff;text-decoration:none;"
            "font-size:12.5px;background:#161b22'>← 返回</a>")


def log_exc(context):
    r"""把当前异常栈追加写入 C:\Temp\portal_api_error.log（与 access log
    同款追加写；自身失败绝不阻断业务，保持静默）。仅在 except 块内调用。"""
    import traceback
    try:
        _log_rotate(_ERROR_LOG)   # C6b：写前超限轮转
        with open(_ERROR_LOG, "a", encoding="utf-8", errors="ignore") as f:
            f.write("[%s] %s\n%s\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), context,
                traceback.format_exc()))
    except Exception:
        pass


def _portal_token():
    tok = (os.environ.get("PORTAL_API_TOKEN") or "").strip()
    if not tok:
        try:
            with open(_PORTAL_TOKEN_FILE, "r", encoding="utf-8") as f:
                tok = f.read().strip()
        except OSError:
            tok = ""
    if not tok:
        import secrets
        tok = secrets.token_hex(32)
        try:
            with open(_PORTAL_TOKEN_FILE, "w", encoding="utf-8") as f:
                f.write(tok)
        except OSError:
            pass  # 落盘失败则仅本次进程内存可用，下次启动重新生成
    return tok


def _load_cainiao_env():
    """X1（2026-10-09）：加载菜鸟 OAuth2 本地凭据文件（KEY=VALUE 行）注入
    os.environ.setdefault——已有环境变量优先，文件不覆盖。文件放
    C:\\Temp（与 _PORTAL_TOKEN_FILE 同一安全口径：portal/ 目录整挂
    nginx web root，落盘即等于局域网可直接 GET 下载，凭据绝不能放这里）。
    """
    path = r"C:\Temp\cainiao_oauth2.env"
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
    except OSError:
        pass  # 文件缺失 = 未配置菜鸟对接，cainiao_auth 走默认/degraded


def _log_rotate(fp):
    r"""C6b 日志轮转：fp 超 50MB → 先删旧 .1 → 当前改名为 .1（单代轮转，
    保留最近两段历史，防止 C:\Temp 被无限增长日志吃满磁盘）。
    每次写入前调用；stat/remove/rename 任一失败都静默放过，绝不阻断
    业务或日志写入本身。返回 True 表示发生了轮转。"""
    try:
        if os.path.getsize(fp) <= _ERROR_LOG_MAX:
            return False
        bak = fp + ".1"
        if os.path.exists(bak):
            os.remove(bak)
        os.replace(fp, bak)
        return True
    except OSError:
        return False


def data_freshness_bg(ttl_sec=1800):
    """K56-B1 驾驶舱取数策略：缓存热同步返回；过期有旧值先返旧值并后台
    刷新；从未算过（重启后 ~100s 预热窗）也只后台算并返回 None——
    任何请求都秒回，调用方对 None 显示「预热中」。"""
    import sys as _sy
    import time as _tt
    if QBT_DIR not in _sy.path:
        _sy.path.insert(0, QBT_DIR)
    import datamart as _dm
    c = _dm._fresh_cache
    if c["data"] is not None and _tt.time() - c["ts"] < ttl_sec:
        return c["data"]
    if _FRESH_BG_LOCK.acquire(blocking=False):
        def refresh():
            global _FRESH_BG_ERROR
            try:
                _dm.freshness_report(ttl_sec=ttl_sec)
                _FRESH_BG_ERROR = None
            except Exception as exc:
                _FRESH_BG_ERROR = str(exc)
                log_exc("data_freshness_bg")
            finally:
                _FRESH_BG_LOCK.release()
        try:
            _th.Thread(target=refresh, daemon=True).start()
        except Exception:
            _FRESH_BG_LOCK.release()
            raise
    if c["data"] is None:
        return None
    return dict(c["data"], cache_stale=True, refreshing=_FRESH_BG_LOCK.locked(),
                refresh_error=_FRESH_BG_ERROR)


def data_sync_start(src):
    """手动触发同步（tdx/tushare/kg/catalog）。单实例 + 后台运行。"""
    if src not in DATA_SYNC_CMDS:
        return {"error": "bad src"}
    if src in {'tdx', 'tushare'}:
        # 新手动入口需先展示实际表/市场/范围；旧GET不能越过确认与持久化锁。
        return dict(started=False,pid=None,running=False,exit=None,tail='',run_id=None,
                    error='请在每日更新工作台预览范围并确认运行')
    with _DATA_LOCK:
        job = DATA_SYNC_JOBS.get(src)
        if job and job["p"].poll() is None:
            return {"error": "already running", "running": True}
        cmd = DATA_SYNC_CMDS[src]
        log = os.path.join(r"C:\Temp", "portal_data_%s.log" % src)
        try:
            with open(log, "ab") as lf:        # append：保留历史尾部
                lf.write(("\n===== %s manual sync =====\n"
                          % time.strftime("%Y-%m-%d %H:%M:%S")).encode())
                p = subprocess.Popen(cmd, stdout=lf, stderr=lf,
                                     cwd=QBT_DIR,
                                     creationflags=getattr(
                                         subprocess, "CREATE_NO_WINDOW", 0))
            DATA_SYNC_JOBS[src] = {"p": p, "log": log}
            return {"started": True, "pid": p.pid}
        except Exception as ex:
            return {"error": str(ex)}


def _tp_row(table):
    """穿透展开行（隐藏，点 🔍 填充）：跨 colspan 由调用方包 <tr>。"""
    return ("<tr id='tpr-%s' style='display:none'>"
            "<td colspan='5' id='tpc-%s' style='padding:0 8px 8px 8px'></td>"
            "</tr>" % (table, table))


def _sched_rc_html(tstat):
    """计划任务状态串 → (显示文本, rc 判色 HTML)。

    267011 等信息码不是故障（新注册任务到点前 result 即 267011），
    误标红会把正常态当告警；last=11/30/1999 是 .NET DateTime.MinValue
    的本地化显示，规范为「尚未运行」。
    """
    if not tstat or tstat == "任务状态查询失败":
        return (tstat or "任务状态查询失败",
                "<b style='color:#8b949e'>状态未知</b>")
    if "not registered" in tstat:
        return tstat, "<b style='color:#8b949e'>未注册</b>"
    txt = tstat
    m = re.search(r"last=(\S+)(?:\s+(\S+))?", txt)
    _ym = re.search(r"(\d{4})", m.group(1)) if m else None
    if _ym and int(_ym.group(1)) < 2000:  # DateTime.MinValue 本地化假日期
        txt = txt[:m.start()] + "last=尚未运行" + txt[m.end():]
    m = re.search(r"result=(\d+)", txt)
    rc = int(m.group(1)) if m else None
    if rc == 0:
        return txt, "<b style='color:#3fb950'>正常</b>"
    if rc in _SCHED_INFO_CODES:
        label, color = _SCHED_INFO_CODES[rc]
        return txt, "<b style='color:%s'>%s</b>" % (color, label)
    return txt, ("<b style='color:#f85149'>rc=%s 异常</b>"
                 % (rc if rc is not None else "?"))


NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW（run 同族随行）
# ==== 批5 修复提级：run/MKT_LABEL/_pool_bear_news（跨域共享）====

def run(cmd, timeout=30):
    # K-STOCK-098 #3 防回归：10-09 曾有调用方算出 timeout=-711s 直接炸
    # TimeoutExpired（deadline-start 计算错，旧版已重构消失）。此处钳制
    # 任何调用方传入的非正 timeout，防同类回归再入。
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        timeout = 30
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=NO_WINDOW)
        return r.stdout.decode("utf-8", "replace")
    except Exception:
        log_exc("run() 子进程失败: %s" % (cmd[:2] if isinstance(cmd, list) else cmd))  # C2批1
        return ""


MKT_LABEL = {"cn": "A股", "us": "美股", "hk": "港股", "crypto": "加密货币"}


def _pool_bear_news(codes_names, days=5, min_conf=0.75, limit=12):
    """预警池利空消息警示（news.db 只读）：近 days 天 bias=bear 且
    置信≥min_conf、可归属到池内个股的消息。

    依据（docs/ops/portal_llm观察记录.md 回测 v2）：偏空消息 T+1 跑输全市场 ~0.5pp，
    「避雷」方向有效 —— 只提示不拦截、不用于增强买入。
    归属三路：① stocks 列真 ts_code ② 标题前缀代码 [600853 龙建股份]
    / [个股研报·org] 300059 ③ 股票名出现在标题。
    返回 [{ts, code, name, title, conf, reason}]，新→旧。"""
    import sqlite3
    from datetime import datetime as _dt, timedelta as _td
    if not codes_names:
        return []
    since = (_dt.now() - _td(days=days)).strftime("%Y-%m-%d %H:%M")
    c6 = {c.split(".")[0]: c for c in codes_names}
    name2code = {n: c for c, n in codes_names.items() if n and len(n) >= 2}
    out, seen = [], set()
    try:
        db = sqlite3.connect(os.path.join(STATION, "news_collector",
                                          "news.db"))
        rows = db.execute(
            "SELECT created_at, title, stocks, jev FROM news "
            "WHERE jev IS NOT NULL AND created_at>=? "
            "ORDER BY id DESC LIMIT 4000", (since,)).fetchall()
        db.close()
    except Exception:
        log_exc("_pool_bear_news:news.db 查询")  # C2批1
        return out
    for ca, title, stocks, jev in rows:
        if not title:
            continue
        try:
            jv = json.loads(jev)
        except Exception:
            continue
        if jv.get("bias") != "bear" or (jv.get("conf") or 0) < min_conf:
            continue
        if jv.get("scope") != "cn":
            # 只取 A 股域消息：宏观/港股/商品域新闻按名归属噪声大
            # （实测：新世界发展≠新世界、金龙指数提及比亚迪、
            #  法国政局挂到金正大），警示层宁可漏报不可误报
            continue
        hit = None
        try:
            lst = json.loads(stocks) if stocks else []
        except Exception:
            lst = []
        for x in lst if isinstance(lst, list) else []:
            if isinstance(x, str) and x in codes_names:
                hit = x
                break
        if hit is None:
            m = re.match(r"^\[(\d{6})[\s·\]]", title)
            if not m:
                m = re.match(r"^\[[^\]]+\]\s*(\d{6})\s", title)
            if m and m.group(1) in c6:
                hit = c6[m.group(1)]
        if hit is None:
            for n, c in name2code.items():
                if n in title:
                    hit = c
                    break
        if hit is None:
            continue
        key = (hit, title[:40])
        if key in seen:
            continue
        seen.add(key)
        out.append({"ts": (ca or "")[:16], "code": hit,
                    "name": codes_names.get(hit, ""),
                    "title": title[:60],
                    "conf": round((jv.get("conf") or 0) * 100),
                    "reason": (jv.get("reason") or "")[:40]})
        if len(out) >= limit:
            break
    return out


_SEC_CACHE = {}
# ==== 批6 随行提级：CANDLE_CN/_matrix_cards/_stock_chip_summary/_stock_sector_rows ====
# ── K239 个股所属板块联动（tdx 板块定义/成分 + 板块指数涨跌） ──
_SEC_CACHE = {"ts": 0.0, "rev": None, "rps": None, "daily": None}


CANDLE_CN = {"early_seal": "早盘封板", "seal": "封板", "late_seal": "尾盘封板",
             "broken": "炸板", "late_surge": "尾盘抢筹", "late_dump": "尾盘砸盘"}


def _matrix_cards(cross_data, cross_hit, sig_states, sig_err, items):
    """个股全景页「方法论矩阵」卡片组（纯函数：数据进 → 卡片 HTML 列表出）。

    A 段：遍历 cross_poll.STRATEGIES 注册表逐策略出卡——命中显示
    label+方向色（long→buy / short→avoid / neutral→watch），未命中
    mute 半透明；新增策略只改 cross_poll.STRATEGIES，本函数零改。
    B 段：本地附加卡（实时 4 策略持仓 → 锚点④ / JEV → ⑤ / AI → ⑥），
    不属于 cross 聚合口径，故不进注册表。

    参数：cross_data=build_cross() 聚合结果（含 strategies 注册表镜像）、
    cross_hit=该票在 stocks[] 的条目或 None、sig_states={sn: 多头|空头|
    空仓}、sig_err=实时计算失败原因、items=JEV 已判断新闻列表。
    """
    import html as _h
    e = _h.escape
    mcards = []
    _dir_cls = {"long": "buy", "short": "avoid", "neutral": "watch"}
    hits_by_sid = {h["sid"]: h for h in (cross_hit or {}).get("hits", [])}
    for s in cross_data.get("strategies", []):
        hit = hits_by_sid.get(s["id"])
        icon = s.get("icon") or "🧪"
        if hit:
            extra = (e(str(hit["dt"])) + " · " if hit.get("dt") else "")
            mcards.append(
                "<a class='mcard' href='%s'>"
                "<div class='mt'>%s %s</div>"
                "<div class='mv %s'>%s</div>"
                "<div class='mu'>%s→ 工作台</div></a>"
                % (s["url"], icon, e(str(s["name"])),
                   _dir_cls.get(hit["dir"], ""), e(str(hit["label"])),
                   extra))
        else:
            mcards.append(
                "<a class='mcard mute' href='%s'>"
                "<div class='mt'>%s %s</div>"
                "<div class='mv'>未命中</div>"
                "<div class='mu'>→ 工作台</div></a>"
                % (s["url"], icon, e(str(s["name"]))))
    # B1. 4 策略实时持仓（实时计算，比快照口径更细 → 锚点 ④）
    if sig_states:
        _nl = sum(1 for v in sig_states.values() if v == "多头")
        _cls = "buy" if _nl >= 3 else ("avoid" if _nl == 0 else "")
        mcards.append(
            "<a class='mcard' href='#sig4'>"
            "<div class='mt'>📈 实时持仓 · 4策略</div>"
            "<div class='mv %s'>%d/4 多头</div>"
            "<div class='mu'>↓ 见下文 ④</div></a>" % (_cls, _nl))
    else:
        mcards.append(
            "<a class='mcard mute' href='#sig4'>"
            "<div class='mt'>📈 实时持仓 · 4策略</div>"
            "<div class='mv'>%s</div>"
            "<div class='mu'>↓ 见下文 ④</div></a>" % (sig_err or "—"))
    # B2. JEV 消息面（→ 锚点 ⑤）
    if items:
        _n_bull = sum(1 for it in items if it["bias_cn"] == "利多")
        _n_bear = sum(1 for it in items if it["bias_cn"] == "利空")
        _dom = ("利多" if _n_bull > _n_bear else
                "利空" if _n_bear > _n_bull else "分歧")
        _cls = ("buy" if _dom == "利多" else
                "avoid" if _dom == "利空" else "watch")
        mcards.append(
            "<a class='mcard' href='#jev'>"
            "<div class='mt'>⚖️ JEV 消息面</div>"
            "<div class='mv %s'>%s · %d 条</div>"
            "<div class='mu'>↓ 见下文 ⑤</div></a>"
            % (_cls, _dom, len(items)))
    else:
        mcards.append(
            "<a class='mcard mute' href='/bt-jev'>"
            "<div class='mt'>⚖️ JEV 消息面</div>"
            "<div class='mv'>近7天无判断</div>"
            "<div class='mu'>→ JEV 主页</div></a>")
    # B3. AI 深度分析（→ 锚点 ⑥）
    mcards.append(
        "<a class='mcard' href='#ai'>"
        "<div class='mt'>🤖 AI 深度分析</div>"
        "<div class='mv'>即时跑 · 2-4 分钟</div>"
        "<div class='mu'>↓ 见下文 ⑥</div></a>")
    return mcards


def _stock_chip_summary(code, px):
    """个股筹码摘要（K239 结论卡用）：只读本地 cyq_chips parquet 最新
    交易日（不在线拉，避免页首阻塞），avg=筹码加权均成本，
    winner=现价下方筹码占比。返回 {d, avg, winner, pos} 或 None。"""
    import re as _re_chip
    m6 = _re_chip.search(r"(\d{6})", str(code or ""))
    if not m6:
        return None
    d6 = m6.group(1)
    ts_code = (d6 + ".SH" if d6[0] == "6" else d6 + ".SZ"
               if d6[0] in "03" else d6 + ".BJ" if d6[0] in "48" else "")
    if not ts_code:
        return None
    cfile = os.path.join(STATION, "quant_backtest", "data", "tushare",
                         "cyq_chips", ts_code.replace(".", "_") + ".parquet")
    try:
        import pandas as _pd_chip
        if not os.path.exists(cfile):
            return None
        df = _pd_chip.read_parquet(cfile)
        if df is None or not len(df):
            return None
        last = str(df["trade_date"].max())
        rows = df[df["trade_date"].astype(str) == last]
        pr = [(float(p), float(pc)) for p, pc in
              zip(rows["price"], rows["percent"]) if float(p) > 0]
        if not pr:
            return None
        avg = round(sum(p * pc for p, pc in pr) / 100.0, 2)
        winner = pos = None
        try:
            close = round(float(px), 2)
        except (TypeError, ValueError):
            close = None
        if close:
            winner = round(sum(pc for p, pc in pr if p <= close), 1)
            pos = round((close / avg - 1) * 100, 1) if avg else None
        return {"d": "%s-%s-%s" % (last[:4], last[4:6], last[6:8]),
                "avg": avg, "winner": winner, "pos": pos}
    except Exception:
        return None


def _stock_sector_rows(code, limit=4):
    """个股 → 所属板块行（K239 板块联动）：[{sector, idx, chg1, chg5,
    rps50}...]，按 RPS50 降序取前 limit。

    数据源（全部进程缓存 1 小时）：
      tdx_sector_def（板块定义，排除 stat/region）+ tdx_sector_member
      （成分，con_code=6 位纯码）→ 个股倒排；
      reports/sector_effect.json → 板块 RPS50；
      tdx_sector_daily → 板块指数当日/5 日涨跌。
    异常/无数据返回 []（页面安全降级，不阻塞个股页）。"""
    import time as _t_sec
    m6 = "".join(ch for ch in str(code or "") if ch.isdigit())[-6:]
    if len(m6) != 6:
        return []
    now = _t_sec.time()
    try:
        if _SEC_CACHE["rev"] is None or now - _SEC_CACHE["ts"] > 3600:
            import pandas as _pd_sec
            import glob as _glob_sec
            tdx = os.path.join(STATION, "quant_backtest", "data", "tdx")

            def _rp(tbl, cols=None):
                parts = sorted(_glob_sec.glob(
                    os.path.join(tdx, tbl, "part_*.parquet")))
                if not parts:
                    return _pd_sec.DataFrame()
                return _pd_sec.concat(
                    [_pd_sec.read_parquet(p, columns=cols) for p in parts],
                    ignore_index=True)

            seg = _rp("tdx_sector_def", ["code", "name", "seg"])
            seg = seg[~seg["seg"].isin({"stat", "region"})] \
                if len(seg) else seg
            name2idx = {str(n): str(c) for c, n in
                        zip(seg["code"], seg["name"])} if len(seg) else {}
            mem = _rp("tdx_sector_member", ["block_name", "con_code"])
            rev = {}
            if len(mem):
                for b, cc in zip(mem["block_name"], mem["con_code"]):
                    ic = name2idx.get(str(b))
                    if not ic:
                        continue          # 只认有指数的板块（881 行业等）
                    rev.setdefault(str(cc).zfill(6), []).append(
                        (str(b), ic))
            eff = _read_json(os.path.join(STATION, "quant_backtest",
                                          "reports",
                                          "sector_effect.json")) or {}
            rps = {s.get("sector"): s.get("rps50")
                   for s in (eff.get("sectors") or []) if s.get("sector")}
            daily = _rp("tdx_sector_daily",
                        ["code", "trade_date", "close"])
            _SEC_CACHE.update({"rev": rev, "rps": rps,
                               "daily": daily if len(daily) else None,
                               "ts": now})
        rev, rps, daily = (_SEC_CACHE["rev"], _SEC_CACHE["rps"],
                           _SEC_CACHE["daily"])
        hits = (rev or {}).get(m6) or []
        if not hits:
            return []
        rows = []
        for bname, ic in hits:
            chg1 = chg5 = None
            if daily is not None:
                sub = daily[daily["code"].astype(str) == str(ic)]
                if len(sub) >= 2:
                    sub = sub.sort_values("trade_date")
                    cl = sub["close"].astype(float).tolist()
                    if cl[-1]:
                        chg1 = (cl[-1] / cl[-2] - 1) * 100 if cl[-2] else None
                        chg5 = ((cl[-1] / cl[-6] - 1) * 100
                                if len(cl) >= 6 and cl[-6] else None)
            rows.append({"sector": bname, "idx": ic, "chg1": chg1,
                         "chg5": chg5, "rps50": rps.get(bname)})
        rows = [r for r in rows
                if r["chg1"] is not None or r["rps50"] is not None]
        rows.sort(key=lambda r: (r["rps50"] if r["rps50"] is not None
                                 else -1), reverse=True)
        return rows[:limit]
    except Exception:
        return []


# ==== 批7 前哨：C 类提级 CACHE_TTL/build_alerts/_fmt_pct（拍板#2）====

CACHE_TTL = 20


def build_alerts():
    """量化域首页「今日预警速览」数据源：盘前/缠论/九维/weak/共振。
    全部读产线落盘快照（与各专项页同源同口径），单项失败即缺项不拖垮整体。
    弱于板块口径=页面口径（排除 .closed_ 且末行可解析），非注册表口径。"""
    out = []
    # ① 盘前倾向（premarket_latest.json：verdict + 六度分值 + 溢价概率）
    pre = _read_json(os.path.join(REPORTS_DIR, "premarket_latest.json"))
    if pre.get("verdict"):
        vals = pre.get("vals") or {}
        l6 = pre.get("l6") or {}
        an6 = l6.get("ann_stats") or {}
        rp6 = l6.get("rpt_stats") or {}
        six = [{"k": k, "v": vals.get(k)} for k in
               ("外围", "政策", "流动性", "板块", "情绪")]
        six.append({"k": "公告研报", "v": None,
                    "n": (an6.get("total") or 0) + (rp6.get("total") or 0)})
        out.append({"name": "盘前倾向",
                    "value": str(pre["verdict"]).split("（")[0],
                    "sub": "开盘溢价概率 %s%%" % pre.get("prob", "—"),
                    "href": "/premarket", "delta": None,
                    "six": six, "overall": pre.get("overall")})
    # ② 缠论预警池（chanlun_pool.jsonl：最新检出日 买/卖 计数 + 较前日差）
    try:
        rows = []
        with open(os.path.join(QBT_DIR, "data", "chanlun_pool.jsonl"),
                  encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rows.append(json.loads(ln))
                except Exception:
                    pass
        days = sorted({r.get("detect_date", "") for r in rows
                       if r.get("detect_date")})
        if days:
            def _nb(dd):
                return sum(1 for r in rows if r.get("detect_date") == dd
                           and str(r.get("type", "")).endswith("买"))
            last = days[-1]
            ns = sum(1 for r in rows if r.get("detect_date") == last
                     and str(r.get("type", "")).endswith("卖"))
            out.append({"name": "缠论信号",
                        "value": "买 %d · 卖 %d" % (_nb(last), ns),
                        "sub": "检出日 %s" % last,
                        "href": "/bt-chanlun-pool",
                        "delta": (_nb(last) - _nb(days[-2]))
                        if len(days) > 1 else None})
    except Exception:
        log_exc("build_alerts:chanlun_pool.jsonl")  # C2批1
    # ③ 九维警报池（nine_dim_scores.json：n_alert 阈值入池数）
    snap = _read_json(os.path.join(QBT_DIR, "data", "nine_dim_scores.json"))
    if snap:
        out.append({"name": "九维警报池",
                    "value": "%s 只" % snap.get("n_alert",
                                                snap.get("n_buy", 0)),
                    "sub": "综合分 ≥%s 入池 · %s" % (
                        snap.get("alert_th", 75),
                        snap.get("trade_date", "—")),
                    "href": "/bt-9dim", "delta": None})
    # ④ 弱于板块在案（weak_track_*.jsonl 排除结案流）
    try:
        n_act = sum(
            1 for fp in glob.glob(os.path.join(
                STATION, "reports", "weak_track_*.jsonl"))
            if ".closed_" not in os.path.basename(fp)
            and _alerts_last_jsonl(fp) is not None)
        out.append({"name": "弱于板块在案", "value": "%d 案" % n_act,
                    "sub": "扫描→建案→跟踪→复评→结案",
                    "href": "/bt-weak", "delta": None})
    except Exception:
        log_exc("build_alerts:weak_track 在案统计")  # C2批1
    # ⑤ 共振做多（cross_poll.build_cross 内置 60s 缓存；口径=共振页 longs）
    try:
        if QBT_DIR not in sys.path:
            sys.path.insert(0, QBT_DIR)
        import cross_poll
        stocks = cross_poll.build_cross().get("stocks", [])
        n_long = sum(1 for s in stocks if s.get("n_hit", 0) >= 2
                     and s.get("state") == "long"
                     and s.get("n_long", 0) >= 2)
        out.append({"name": "共振做多", "value": "%d 只" % n_long,
                    "sub": "多策略信号共振 · 买入级",
                    "href": "/bt-cross", "delta": None})
    except Exception:
        log_exc("build_alerts:cross_poll 共振")  # C2批1
    # ⑥ 投委会（K61：最近跑批夜三会计数 + 影子转正进度，态势带卡；
    #    K62：管理预警计数 + 风格分桶分布上屏；K64d：待确认队列计数）
    try:
        cs = _council_stats()
        if cs["d"]:
            warn = (" · ⚠预警 %d" % cs["n_warn"]) if cs.get("n_warn") \
                else ""
            pend = (" · 待确认 %d" % cs["n_pend"]) if cs.get("n_pend") \
                else ""
            sty = cs.get("styles") or {}
            sty_s = ""
            if sty:
                sty_s = " · 桶 %s/%s" % (sty.get("激进", 0),
                                         sty.get("稳健", 0))
            out.append({"name": "🏛 投委会",
                        "value": "立案 %d" % cs["n_prop"],
                        "sub": "盯盘需注意 %d · 复盘 %d · 影子 %d/%d%s%s%s" % (
                            cs["n_watch"], cs["n_rev"],
                            cs["shadow_n"], cs["shadow_need"],
                            warn, pend, sty_s),
                        "href": "/council", "delta": None})
    except Exception:
        log_exc("build_alerts:council_stats")  # C2批1
    return {"ts": time.strftime("%Y-%m-%d %H:%M"), "alerts": out}


# ── K41 个股基本面 / 公告研报取数（_bt_stock_page 专用 helper，批7拍板#2 提级顶层）──
def _fmt_pct(x):
    try:
        return "%.1f%%" % float(x)
    except (TypeError, ValueError):
        return "—"


# ==== 批7 前哨连锁：_alerts_last_jsonl/_council_stats（build_alerts 依赖）====

# ── K39 今日预警速览：各策略产线最新预警值聚合 ──────────────
def _alerts_last_jsonl(fp):
    """JSONL 末行解析（weak 在案口径：末行可解析才计入）。"""
    try:
        last = ""
        with open(fp, encoding="utf-8") as f:
            for ln in f:
                last = ln
        last = last.strip()
        return json.loads(last) if last else None
    except Exception:
        # 豁免（C2批1）：口径即「末行可解析才计入」（weak 在案口径注释）
        return None


def _council_stats():
    """K61 投委会大屏锚：最近跑批夜三会计数 + 影子转正进度。
    全部读 jsonl/json 落盘锚（60s TTL，大屏轮询零重量级）；
    「最近夜」= 三张留痕表最大 d（23:50 跑批属前夜，白天看仍是当期）。"""
    now = time.time()
    if _council_cache["d"] is not None and now - _council_cache["t"] < 55:
        return _council_cache["d"]
    rd = os.path.join(QBT_DIR, "reports")

    def _rows(fp):
        try:
            out = []
            with open(fp, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if ln:
                        try:
                            out.append(json.loads(ln))
                        except Exception:
                            pass
            return out
        except OSError:
            return []

    props = _rows(os.path.join(rd, "council_proposals.jsonl"))
    watch = _rows(os.path.join(rd, "council_watch.jsonl"))
    revs = _rows(os.path.join(rd, "council_reviews.jsonl"))
    ds = [r.get("d") for r in props + watch + revs if r.get("d")]
    last = max(ds) if ds else ""
    # K62：当期 (sid,code) 最新指令（预警类计数）+ 风格分桶分布
    latest = {}
    for r in props:
        k = (r.get("sid"), r.get("code"))
        latest[k] = (r.get("directive") or {}).get("act") or ""
    n_warn = sum(1 for a in latest.values()
                 if a in ("平仓预警", "减仓预警"))
    styles = {}
    smap = {}
    for r in props:
        if r.get("code"):
            smap[r["code"]] = r.get("style") or "稳健"
    for s in smap.values():
        styles[s] = styles.get(s, 0) + 1
    st = _read_json(os.path.join(rd, "council_shadow_stats.json"))
    gate = (st or {}).get("gate") or {}
    res = {"d": last,
           "n_prop": len({(r.get("sid"), r.get("code")) for r in props
                          if r.get("d") == last}),
           "n_watch": sum(1 for r in watch if r.get("d") == last
                          and r.get("type") == "watch"),
           "n_rev": sum(1 for r in revs if r.get("d") == last),
           "n_total": len(props), "n_warn": n_warn, "styles": styles,
           "n_pend": len(_council_pending()),     # K64d 待人工确认队列
           "shadow_n": gate.get("n", 0), "shadow_need": gate.get("need", 30),
           "gate": gate.get("status", ""),
           "ts": (st or {}).get("updated", "")}
    _council_cache.update(t=now, d=res)
    return res

# ==== 批7 前哨连锁第二轮：_council_pending(def)/_council_cache ====
def _council_pending():
    """K64d 读投委会待确认队列 council_pending.json（dict，sid|code 键）。"""
    try:
        with open(os.path.join(REPORTS_DIR, "council_pending.json"),
                  encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}
_council_cache = {"t": 0.0, "d": None}

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
