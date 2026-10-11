# -*- coding: utf-8 -*-
"""P4 批 0：portal 共享底座（helper+方案A+状态锁/常量+导航族+tp共享+_sched判色）。
由批 0 施工脚本从 portal_api.py 结构边界剪切生成；唯一实例（锁/缓存单例）。
消费方必须显式 import（import * 不导出下划线名）。
"""
import datetime, json, os, subprocess, threading as _th, time


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
            ("🛰 大屏信号", "/screen/sig", "screensig"),
            ("🌅 盘前", "/premarket", "pre"),
            ("🌇 盘后复盘", "/afterhours", "afterhours"),
            ("🏠 驾驶舱", "/fin", "fin"),
            ("🔍 个股分析", "/sa", "sa"),
            ("💬 查数", "/nl", "nl"))),
        ("🚀 指挥", (
            ("🗺 门户", "/portal", "portal"),
            ("🚀 驾驶舱", "/bridge", "bridge"),
            ("⚡ 驾驶舱行动", "/bridge/act", "bridgeact"),
            ("🦸 超级助手", "http://127.0.0.1:8787", "hermes"),
            ("🐴 CowAgent", "http://127.0.0.1:9899", "cowagent"),
            ("🧠 智策 OS", "http://127.0.0.1:5175", "zhice"),
            ("🤖 AI 市场", "/aim", "aim"),
            ("🛒 算法市场", "/algo", "algo"),
            ("🏛 组织架构", "/org", "org"))),
        ("🎖 策略资产", (
            ("🎖 策略中心", "/strategies", "strategies"),
            ("🌊 青萍之末", "/bt-qpzm", "qpzm"),
            ("🔔 订阅推送", "/bt/subscribe", "subscribe"),
            ("🧭 缠论", "/bt-chanlun", "chanlun"),
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
            ("🚦 信号", "/bt-signals", "signals"),
            ("💼 持仓", "/bt-positions", "positions"),
            ("🧾 模拟仓", "/paper", "paper"),
            ("🛒 策略市场", "/market-strategy", "market-strategy"),
            ("📊 绩效市场", "/market", "market"),
            ("📄 报告", "/bt-report", "report"),
            ("🎯 卖点研判", "/sellpoint", "sellpoint"))),
        ("🧰 研究与数据", (
            ("🎯 选股中心", "/bt", "bt"),
            ("🌾 期货量化", "/fut", "fut"),
            ("📊 寻优", "/bt-opt", "opt"),
            ("🔥 板块", "/bt-sectors", "sectors"),
            ("🗄 数据仓", "/bt-data-health", "data-health"))),
    ),
    "intel": (
        ("📡 情报流", (
            ("🏠 情报工场", "/intel", "intel"),
            ("🤖 AI 晨报", "/bt-brief", "brief"),
            ("⚖️ JEV 判断", "/bt-jev", "jev"))),
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
    ),
    "creator": ((None, (
        ("🎬 创作专项", "/creator", "creator"),
        ("📝 公众号草稿", "/gzh", "gzh"),
        ("🎞 短视频成片", "/vd", "vd"))),),
    "dev": ((None, (("💻 开发者专项", "/dev", "dev"),)),),
    "ops": ((None, (
        ("🎛 OS 控制台", "/ops", "opshome"),
        ("🛡 审计状态", "/audit", "audit"),
        ("🖥 系统自检", "/sys-health", "sys"))),),
}


SECTION_PAGES = {k: [it for _, its in groups for it in its]
                 for k, groups in DOMAIN_NAV_GROUPS.items()}


PAGE_SECTION = {"fin": "quant", "sa": "quant", "pre": "quant", "bt": "quant",
                "afterhours": "quant",
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
                "jev": "intel", "brief": "intel", "kg": "intel",
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
    """把当前异常栈追加写入 C:\Temp\portal_api_error.log（与 access log
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
    """C6b 日志轮转：fp 超 50MB → 先删旧 .1 → 当前改名为 .1（单代轮转，
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


