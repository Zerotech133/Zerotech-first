# -*- coding: utf-8 -*-
"""P4 批 0：portal 共享底座（helper+方案A+状态锁/常量+导航族+tp共享+_sched判色）。
由批 0 施工脚本从 portal_api.py 结构边界剪切生成；唯一实例（锁/缓存单例）。
消费方必须显式 import（import * 不导出下划线名）。
"""
import datetime
import glob  # noqa: F401 —— 基座候选组旧依赖，保留以兼容历史 dir(api_common)，勿删
import json
import os
import re  # noqa: F401 —— 基座候选组旧依赖，保留以兼容历史 dir(api_common)，勿删
import subprocess  # noqa: F401 —— 基座候选组旧依赖，保留以兼容历史 dir(api_common)，勿删
import sys  # noqa: F401 —— 基座候选组旧依赖，保留以兼容历史 dir(api_common)，勿删
import threading  # noqa: F401 —— 基座候选组旧依赖，保留以兼容历史 dir(api_common)，勿删
import threading as _th  # noqa: F401 —— 基座候选组旧依赖，保留以兼容历史 dir(api_common)，勿删
import time
import logging





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
# token 来源：环境变量 PORTAL_API_TOKEN 优先；其次 PORTAL_API_TOKEN_FILE 指定路径；
# 再优先本机私有区 `_vault\secrets\portal_api_token`（2026-10-26 R39 从 C:\Temp
# 迁移，见 _audit_20261025/exec/A3-execution-20261026.md），旧 C:\Temp 路径仅兼容
# 回退；两者皆无则首次自动生成 64 位 hex 落盘。
# （不落 portal/ 下——该目录整挂为 nginx web root，落盘即等于局域网可直接
# GET 下载，鉴权形同虚设）
_VAULT_SECRETS = r"C:\项目\_vault\secrets"
_PORTAL_TOKEN_FILE = (os.environ.get("PORTAL_API_TOKEN_FILE")
                      or next((p for p in (
                          os.path.join(_VAULT_SECRETS, "portal_api_token"),
                          r"C:\Temp\portal_api_token",
                      ) if os.path.isfile(p)),
                          os.path.join(_VAULT_SECRETS, "portal_api_token")))


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
    ("stock", "📊 个股分析", "/sa"),
    ("intel", "🕵️ 情报", "/intel"),
    ("creator", "🎬 创作", "/creator"),
    ("dev", "💻 开发", "/dev"),
    ("ops", "⚙️ 系统", "/ops"),
]


DOMAIN_NAV_GROUPS = {
    "stock": (
        ("📊 分析工作台", (
            ("🧪 新版工作台", "/sa/workbench", "sa-workbench"),
            ("🔍 单股分析", "/sa", "sa"),
            ("🧾 事件复核", "/sa#review", "sareview"),
            ("📚 方法说明", "/sa#method", "samethod"),
            ("🗄️ 数据口径", "/sa#data", "sadata"))),
        ("🧩 产品与开源", (
            ("📖 开源边界", "/sa#opensource", "saopensource"),
            ("💼 商业化路线", "/sa#commercial", "sacommercial"))),
    ),
    "quant": (
        ("🌅 盘前与驾驶", (
            ("📺 数据大屏", "/screen", "screen"),
            ("🌅 盘前", "/premarket", "pre"),
            ("🌇 盘后复盘", "/afterhours", "afterhours"),
            ("💰 金融驾驶舱", "/fin", "fin"),
            ("🎯 量化驾驶舱", "/quant-drill", "quantdrill"),
            ("💬 查数", "/nl", "nl"))),
        ("🚀 指挥", (
            ("🚀 驾驶舱", "/bridge", "bridge"),
            ("🦸 超级助手", "/hermes/", "hermes"),
            ("🐴 CowAgent", "http://127.0.0.1:9899", "cowagent"),
            ("🧠 智策 OS", "/quant/", "zhice"),
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
            ("🏠 情报工作台", "/intel", "intel"),
            ("📰 新闻证据流", "/intel/stream", "intelstream"),
            ("🤖 AI 晨报", "/bt-brief", "brief"),
            ("⚖️ JEV 判断", "/bt-jev", "jev"),
            ("🩺 四件套观测", "/stack", "jevstack"))),
        ("🧪 雪球舆情", (
            ("📰 情报日报", "/xq_intel.html", "xqintel"),
            ("📊 数据日报", "/data_daily.html", "dailydata"),
            ("📰 新闻情报", "/news_intel.html", "newsintel"),)),
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
        ("🧬 数据血缘", (
            ("🧬 血缘总览", "/lineage.html", "lineage"),)),
        ("💼 情报变现", (
            ("🛰 推送中台", "/push/", "pushhub"),
            ("📨 通知指挥", "/notify-command", "notifycmd"),
            ("📋 运维对账", "/ops-board", "opsboard"),
            ("🔐 我的订阅", "/my-subscription", "mysub"),
            ("📊 订阅运营", "/sub-admin", "subadmin"),)),
    ),
    "creator": ((None, (
        ("🎬 创作专项", "/creator", "creator"),
        ("📝 公众号草稿", "/gzh", "gzh"),
        ("🎞 短视频成片", "/vd", "vd"))),),
    "dev": (
        (None, (("💻 开发者专项", "/dev", "dev"),
                ("🎖 研发军团", "/dev_legion_v2.html", "devlegionv2"))),
        ("🎓 零界学院", (("🎓 零界学院", "/academy", "academy"),)),
    ),
    "ops": (
        ("🛰 数据运营", (
            ("🎛 数据指挥台", "/dataops", "dataops"),
            ("🗄 数据仓", "/dw.html", "dw"),
            ("📚 语料知识库", "/kb_corpus.html", "kbcorpus"),
            ("📥 PDF知识入库", "/kb_ingest.html", "kbingest"),
            ("📑 研报知识库", "/kb_reports.html", "kbreports"),
            ("📊 策略库·选股", "/selection.html", "selection"),
            ("🃏 学习卡", "/kb_cards.html", "kbcards"),
            ("🧾 学习卡审核", "/card_review.html", "cardreview"), ("📚 学习项目", "/learning_projects.html", "learningprojects"),
            ("🛡 H14影子盘", "/h14_shadow.html", "h14shadow"),
            ("🪞 影子盘总览", "/shadow.html", "shadow"),
            ("📅 每日筛选", "/screening.html", "screening"),
            ("📈 趋势模板选股", "/minervini.html", "minervini"),)),
        ("🪖 军团", (
            ("🏛 组织架构 🚧", "/org", "org"),
            ("✅ 审批注册表", "/approvals", "approvals"),
            ("🧩 能力看板", "/capability", "capability"),
            ("📈 投研绩效", "/perf", "perf"),
            ("🛡 审计状态", "/audit", "audit"),)),
        ("⚙️ 系统运维", (
            ("🖥 个人 OS 入口", "/os-entry", "os-entry"),
            ("🖥 指挥大屏", "/os-board", "os-board"),
            ("📚 学习成长", "/learn", "learn"),
            ("🛰 助理雷达", "/radar", "radar"),
            ("📊 方法库", "/methods", "methods"),
            ("📊 数据仪表盘", "/dashboard", "dashboard"),
            ("🎛 OS 控制台", "/ops", "opshome"),
            ("⚡ 动作流中心", "/flow", "flow"),
            ("🖥 系统自检", "/sys-health", "sys"),)),
    ),
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
                "intel": "intel", "intelstream": "intel", "lineage": "intel",
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






















# ── K44 数据大屏：策略选股 × 市场 × 行业 × 账本成绩 一屏聚合 ──
_screen_cache = {"t": 0.0, "d": None}


_ENV_MKT = (("A股", "cn"), ("美股", "us"), ("港股", "hk"), ("加密", "crypto"))








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
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_common.py", exc)


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
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_common.py", exc)
    return tok


def _load_cainiao_env(path=None):
    """X1（2026-10-09）：加载菜鸟 OAuth2 本地凭据文件（KEY=VALUE 行）注入
    os.environ.setdefault——已有环境变量优先，文件不覆盖。默认路径（2026-10-26
    R39 迁移后）：`_vault\\secrets\\cainiao_oauth2.env` → 旧
    `C:\\Temp\\cainiao_oauth2.env` 兼容回退
    （与 _PORTAL_TOKEN_FILE 同一安全口径：portal/ 目录整挂 nginx web root，
    落盘即等于局域网可直接 GET 下载，凭据绝不能放这里）。

    B5 白名单化（2026-10-21）：仅键名 strip 后以 "CAINIAO_" 前缀开头的
    行允许 setdefault；其余键（如 PORTAL_SUB_ADMIN_KEY 等任意键）跳过
    并计数，防止凭据文件被塞入无关键后经此文件静默开启闸门。取证出处：
    docs/plans/2026-10-20_待核项只读取证_2026-10-20.md:144-161 实证现
    文件仅含 3 个 CAINIAO_* 键 → 白名单化对生产零行为变化。

    返回统计 dict（只含键名，任何情况下不含值）：
    {"loaded": 注入键名列表, "skipped": 被跳过键名列表,
     "missing": 文件是否缺失 bool}
    文件缺失（OSError）时静默 no-op（closed on failure，与既有口径一致），
    返回 missing=True。"""
    if path is None:
        # 2026-10-26 R39：优先本机私有区（从 C:\Temp 迁移），旧路径兼容回退。
        path = next((p for p in (
            os.path.join(_VAULT_SECRETS, "cainiao_oauth2.env"),
            r"C:\Temp\cainiao_oauth2.env",
        ) if os.path.isfile(p)), r"C:\Temp\cainiao_oauth2.env")
    loaded = []
    skipped = []
    missing = False
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                if not k.startswith("CAINIAO_"):
                    skipped.append(k)  # 只记键名，绝不落值
                    continue
                if k not in os.environ:
                    os.environ[k] = v.strip()
                    loaded.append(k)
    except OSError:
        # 文件缺失 = 未配置菜鸟对接，cainiao_auth 走默认/degraded；
        # closed on failure：静默 no-op，不抛不记值。
        missing = True
    return {"loaded": loaded, "skipped": skipped, "missing": missing}












# ==== 批5 修复提级：run/MKT_LABEL/_pool_bear_news（跨域共享）====

















# ==== 批7 前哨：C 类提级 CACHE_TTL/build_alerts/_fmt_pct（拍板#2）====

















# ── K41 个股基本面 / 公告研报取数（_bt_stock_page 专用 helper，批7拍板#2 提级顶层）──
def _fmt_pct(x):
    try:
        return "%.1f%%" % float(x)
    except (TypeError, ValueError):
        return "—"


# ==== 批7 前哨连锁：_alerts_last_jsonl/_council_stats（build_alerts 依赖）====



def _council_ts_iso(rows, last_day):
    """投委会最新业务日行的 ts → +08:00 ISO；naive 按北京时间理解；坏值/缺失 ''。

    2026-10-26 OS-FIX-MEASURE2：原 `_council_stats()["ts"]` 取
    council_shadow_stats.json 的 `updated`（与 `d` 不同日，dated() 恒
    invalid_anchor/unproven）。生产者完成时间应来自留痕行自身的 ts：
    取 d == last_day 的行里最大的 ts。任一字段不可信 → ""（调用方维持 unproven）。
    """
    best = None
    for row in rows:
        if not isinstance(row, dict) or row.get("d") != last_day:
            continue
        raw = str(row.get("ts") or "").strip()
        if not raw:
            continue
        try:
            moment = datetime.datetime.fromisoformat(raw)
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=datetime.timezone(
                datetime.timedelta(hours=8)))
        if best is None or moment > best:
            best = moment
    if best is None:
        return ""
    return best.astimezone(datetime.timezone(
        datetime.timedelta(hours=8))).isoformat(timespec="seconds")


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
                        except Exception as exc:
                            logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_common.py", exc)
            return out
        except OSError:
            return []

    props = _rows(os.path.join(rd, "council_proposals.jsonl"))
    watch = _rows(os.path.join(rd, "council_watch.jsonl"))
    revs = _rows(os.path.join(rd, "council_reviews.jsonl"))
    ds = [r.get("d") for r in props + watch + revs if r.get("d")]
    last = max(ds) if ds else ""
    # 生产者完成时间戳（2026-10-26 OS-FIX-MEASURE2）：最新业务日行的 ts，
    # 归一为 +08:00 ISO；取代旧的 shadow stats.updated（非生产者完成时间）。
    latest_ts = _council_ts_iso(props + watch + revs, last)
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
           "ts": latest_ts}
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


# ── 兼容 re-export（2026-10-31 WO-COMMON-SPLIT-20261031）──────────────
# 被拆分到 api_common_<group>.py 的名称继续从 api_common 可见，
# 保证 83 处既有 import 站点零改动（模块内显式 as 别名 = 显式再导出）。
from api_common_assets import (
    _BTSEL_JS as _BTSEL_JS,
    _BTSEL_TAG as _BTSEL_TAG,
    _CARD_FB_FP as _CARD_FB_FP,
    _HERMES_FAB_HTML as _HERMES_FAB_HTML,
    _LABELS_FP as _LABELS_FP,
    _PILL as _PILL,
    _PILL_ON as _PILL_ON,
    _TABBTN as _TABBTN,
    _TAB_JS as _TAB_JS,
    _TP_JS as _TP_JS,
    _TP_MKT as _TP_MKT,
)
from api_common_alert import (
    ALERT_FRESH_WINDOW as ALERT_FRESH_WINDOW,
    WEEKLY_SOURCE_MAX_AGE as WEEKLY_SOURCE_MAX_AGE,
    _alert_acceptable_days as _alert_acceptable_days,
    _alert_business_day as _alert_business_day,
    _alert_prev_weekday as _alert_prev_weekday,
    _alert_source_health as _alert_source_health,
    _alerts_last_jsonl as _alerts_last_jsonl,
    _read_weak_scan_marker as _read_weak_scan_marker,
    build_alerts as build_alerts,
)
from api_common_stock import (
    CACHE_TTL as CACHE_TTL,
    CANDLE_CN as CANDLE_CN,
    MKT_LABEL as MKT_LABEL,
    _SEC_CACHE as _SEC_CACHE,
    _matrix_cards as _matrix_cards,
    _pool_bear_news as _pool_bear_news,
    _stock_chip_summary as _stock_chip_summary,
    _stock_sector_rows as _stock_sector_rows,
)
from api_common_sched import (
    DATA_SYNC_CMDS as DATA_SYNC_CMDS,
    DATA_SYNC_JOBS as DATA_SYNC_JOBS,
    NO_WINDOW as NO_WINDOW,
    _DATA_LOCK as _DATA_LOCK,
    _FRESH_BG_ERROR as _FRESH_BG_ERROR,
    _FRESH_BG_LOCK as _FRESH_BG_LOCK,
    _SCHED_INFO_CODES as _SCHED_INFO_CODES,
    _log_rotate as _log_rotate,
    _sched_rc_html as _sched_rc_html,
    _tp_row as _tp_row,
    data_freshness_bg as data_freshness_bg,
    data_sync_start as data_sync_start,
    run as run,
)
