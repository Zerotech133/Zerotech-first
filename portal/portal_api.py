# -*- coding: utf-8 -*-
"""OS 控制台 API 桥（纯标准库）— 127.0.0.1:8091

聚合工作站全量状态供门户页面消费：
  /api/overview  容器矩阵 + 计划任务 + 备份 + 告警 + 磁盘 + 关键端口
  /health        存活探测（watchdog/巡检用）
数据带 20s TTL 缓存：首次聚合约 5-15s（docker/PS 子进程），之后毫秒级。
退出码约定：端口被占用（已有实例）→ 静默 exit 0，配合计划任务重复触发自愈。
"""

# ==== P4 批0：api_common 显式导入（禁 import *，下划线名不导出）====
from api_common import (
    ROOT,
    STATION,
    _PORTAL_TOKEN_FILE,
    _portal_token,
    _load_cainiao_env,
    _ERROR_LOG,
    _ERROR_LOG_MAX,
    _log_rotate,
    log_exc,
    SECTIONS,
    DOMAIN_NAV_GROUPS,
    SECTION_PAGES,
    PAGE_SECTION,
    _PILL,
    _PILL_ON,
    _TABBTN,
    _TAB_JS,
    _back_link_html,
    _tabs_html,
    pill_nav,
    REPORTS_DIR,
    QBT_DIR,
    _screen_cache,
    DATA_SYNC_JOBS,
    _DATA_LOCK,
    DATA_SYNC_CMDS,
    _FRESH_BG_LOCK,
    _FRESH_BG_ERROR,
    data_freshness_bg,
    _TP_JS,
    _TP_MKT,
    _tp_row,
    data_sync_start,
    _read_json,
    _ENV_MKT,
    _env_market_key,
    _SCHED_INFO_CODES,
    _sched_rc_html,
    run, MKT_LABEL, _pool_bear_news, NO_WINDOW,
    CANDLE_CN, _matrix_cards, _stock_chip_summary, _stock_sector_rows,
    _SEC_CACHE, CACHE_TTL, build_alerts, _fmt_pct,
    _alerts_last_jsonl, _council_stats, _council_pending, _council_cache, _BTSEL_TAG, _LABELS_FP)
import api_kg
from api_kg import _kg_card_fb_save, _kg_label_save
from api_backtest import strategies_data
# ==== 批0 导入结束 ====
# ==== P4 批1：api_data 显式导入 ====
import api_data  # 模块名绑定：Handler 多继承基类引用 api_data.Handler
from api_data import (
    _HEALTH_CACHE,
    _HEALTH_LOCK,
    _KG_HEALTH_CACHE,
    _KG_HEALTH_LOCK,
    data_health_bg,
    data_health_kg_bg,
    data_freshness,
    _WAREHOUSE_SERVICES,
    _WAREHOUSE_SERVICES_LOCK,
    warehouse_update_services,
    warehouse_legacy_status,
    data_sync_status,
    _news_db)
# ==== 批1 导入结束 ====
# ==== P4 批2：api_bridge 显式导入 ====
import api_bridge  # 模块名绑定（from-import 不绑模块名，批1教训）
from api_bridge import (
    bridge_table_preview,
    bridge_summary,
    bridge_decisions,
    BRIDGE_SCAN_TASKS,
    bridge_actions_index,
    bridge_act,
    bridge_act_desc)
# ==== 批2 导入结束 ====
# ==== P4 批5：api_chanlun_pages 显式导入 ====
import api_portfolio  # 模块名绑定（多继承基类引用）
from api_portfolio import (
    _debate_say, _portfolio_set, _council_confirm, _portfolio_undo,
    _debate_flow, _pf_csv, _portfolio_adjust, _portfolio_open,
    _council_holdings, _council_simrun, _portfolio_close,
    _portfolio_market, _pf_apply, _pf_reason_cat, _pf_summary)
from api_screen import build_screen, _kline_data, _kline_qy, _chips_data, get_quotes
import api_screen  # 模块名绑定（多继承基类引用）
import api_chanlun_pages  # 模块名绑定（多继承基类引用）
from api_chanlun_pages import (
    _chanlun_pool_push)
# ==== 批5 导入结束 ====
# ==== P4 批6：api_backtest 显式导入 ====
import api_backtest  # 模块名绑定（多继承基类引用）
from api_backtest import (
    get_backtest,
    fut_page_html,
    sellpoint_page_html,
    _BT_CUSTOM_BATCHES,
    _BT_CUSTOM_REPORT,
    _BT_CUSTOM_STATE,
    _BT_CUSTOM_LOG,
    _bt_norm_code,
    _bt_custom_start,
    _bt_custom_status,
    _pulse_exit_line,
    _pulse_fresh,
    _pulse_ago,
    build_daily_pulse,
    _bt_resolve,
    _bt_jev_news)
# ==== 批6 导入结束 ====





import glob
import hmac
import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time

PORT = 8091
JEV = os.path.join(STATION, "jev-eval")
BACKUPS = os.path.join(STATION, "backups")
MIRROR = r"D:\backups_mirror"
TASKS = ["AI工作站每日备份", "AI工作站告警转发", "JevFOCUS首期报告",
         "ZhiceWatchdog", "金融新闻采集", "金融新闻日报", "金融新闻AI晨报",
         "金融新闻收盘复盘", "自选股行情快照", "公众号选题撰文",
         "金融量化每日扫描", "ChanlunScan",
         "金融量化季度调参"]





_PORTAL_TOKEN = _portal_token()




_load_cainiao_env()

# 变更类 POST 端点（逐一对照 do_POST 路由表；/feishu/event 为飞书回调，
# 不走 portal token，改走 X-Lark-Signature 验签，见 _api_feishu_event）
_MUTATION_POSTS = frozenset((
    "/api/data/update-plan",
    "/api/data/update",
    "/api/portfolio_subscribe",
    "/api/portfolio_hire",
    "/api/trader_fire",   # K-STOCK-122 投研团队：组合解聘交易员
    "/chanlun-anno",
    "/api/chanlun/label/save",
    "/api/report_push",
    "/api/sub_toggle",
    "/api/sub_del",
    "/api/sub_test",
    "/api/recv_add",
    "/kg/canvas_edit",
    "/kg/doc/upload",
    "/kg/lexicon/save",
    "/api/chanlun/card/feedback",
    "/api/chanlun/pool/push",
    "/api/portfolio_set",
    "/api/portfolio_undo",
    "/api/portfolio_close",
    "/api/portfolio_adjust",
    "/api/portfolio_open",
    "/api/council/confirm",
    "/api/council/simrun",
    "/api/debate/say",
    "/api/batch-backtest",
    "/api/paper_order",   # K-STOCK v0.2 模拟仓下单（BUY/SELL/双刀执行）
    "/api/paper_stop",    # K-STOCK v0.2 模拟仓止损挂/解
    "/api/paper_hire",    # K-STOCK-042 A3 雇佣交互（配置面：只改 hires.json）
    "/api/manager_hire",  # K-STOCK-137d S-b2 经理雇佣（子进程 manager.py hire）
    "/api/manager_fire",  # K-STOCK-137d S-b2 经理解雇（子进程 manager.py fire）
))

# C1a（研发计划 v2）：GET 形式但触发后台动作/子进程的端点——比照变更类
# POST 待遇纳入鉴权。此前局域网攻击者可直接 GET 触发数据同步/预警推送/
# 个股分析子进程（资源滥用 + 伪造飞书推送）。本机判定链复用既有机制：
# 回环直连 8091 或本机浏览器（X-Real-IP=127.0.0.1）直接放行，页面使用
# 不受影响；局域网来源须带 X-Portal-Token（与 POST 同一 token）。
_ACTION_GETS = frozenset((
    "/api/data/sync",    # 手动触发数据同步（data_sync_start 起后台子进程）
    "/api/alert/push",   # 手动触发飞书预警推送（data_sync_start("alertpush")）
    "/sa/start",         # 个股分析任务启动（起后台分析子进程）
))


_ACCESS_LOG = r"C:\Temp\portal_api_access.log"





# ===== P1-7 nlquery force=1 频控（2026-10-08 全检修复）=====
# force=1 关闭只读保护、直接触发 multi_agent LLM 现算（10-30s 任务），
# 未鉴权场景可被无限刷（LLM DoS + prompt injection 攻击面）。
# 简易内存频控：每 IP 每分钟 ≤3 次，超限 429。纯标准库、进程内计数
# （重启清零；多线程下计数近似准确，频控场景足够）。
_NLQ_FORCE_MAX_PER_MIN = 3
_NLQ_FORCE_HITS = {}   # ip -> [timestamp, ...]

_lock = threading.Lock()
_cache = {"t": 0.0, "data": None}




def tcp_ok(port, t=0.5):
    # 双栈探测：vite 等开发服务器可能只绑 IPv6 ::1
    for host in ("127.0.0.1", "::1"):
        try:
            s = socket.create_connection((host, port), timeout=t)
            s.close()
            return True
        except Exception:
            continue  # 豁免（C2批1）：双栈探测逐栈尝试，::1 失败切 127.0.0.1 属正常
    return False


def get_containers():
    out = run(["docker", "ps", "-a", "--format", "{{json .}}"], timeout=25)
    groups = {}
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            c = json.loads(line)
        except Exception:
            continue  # 豁免（C2批1）：docker ps 输出坏行跳过，正常容错
        name = c.get("Names", "")
        if name.startswith("quantdinger"):
            g = "QuantDinger"
        elif name.startswith("newquantsystem"):
            g = "NewQuantSystem v2"
        elif name.startswith("zhice"):
            g = "智策 OS"
        else:
            g = "其他服务"
        groups.setdefault(g, []).append((name, c.get("State", "")))
    res = {}
    for g, items in groups.items():
        running = sum(1 for _, st in items if st == "running")
        bad = [n for n, st in items if st != "running"]
        res[g] = {"running": running, "total": len(items), "exited": bad[:6]}
    return res


PS_TASKS = (
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    "$n=@(" + ",".join("'%s'" % t for t in TASKS) + ");"
    "Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue | ForEach-Object {"
    "$i = $_ | Get-ScheduledTaskInfo;"
    "[pscustomobject]@{name=$_.TaskName; state=[string]$_.State;"
    "last=[int]$i.LastTaskResult; lastrun=[string]$i.LastRunTime;"
    "next=[string]$i.NextRunTime} | ConvertTo-Json -Compress}"
)


def get_tasks():
    out = run(["powershell", "-NoProfile", "-Command", PS_TASKS], timeout=45)
    tasks = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                tasks.append(json.loads(line))
            except Exception:
                pass  # 豁免（C2批1）：schtasks 输出坏行跳过，正常容错
    return tasks


def get_backups():
    res = {"latest": None, "failed": None, "recent": [], "mirror": os.path.exists(MIRROR)}
    try:
        dirs = sorted((d for d in os.listdir(BACKUPS)
                       if os.path.isdir(os.path.join(BACKUPS, d))
                       and re.match(r"^\d{4}-\d{2}-\d{2}_", d)), reverse=True)
        res["recent"] = dirs[:3]
        if dirs:
            res["latest"] = dirs[0]
            log = os.path.join(BACKUPS, dirs[0], "backup.log")
            if os.path.exists(log):
                with open(log, "r", encoding="utf-8", errors="replace") as f:
                    lines = [l.rstrip() for l in f if l.strip()]
                done = [l for l in lines if "DONE" in l]
                if done:
                    res["failed"] = done[-1].split("failed=")[-1].strip()
    except Exception:
        log_exc("get_backups:备份目录读取")  # C2批1
    return res


def get_alerts():
    res = {"pending": 0, "acks": [], "sent": 0}
    try:
        res["pending"] = len(glob.glob(os.path.join(JEV, "alerts", "*.flag")))
    except Exception:
        log_exc("get_alerts:alerts 目录 glob")  # C2批1
    p = os.path.join(JEV, "backup_card_acks.jsonl")
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = [l for l in f if l.strip()]
        for l in lines[-6:]:
            try:
                j = json.loads(l)
                res["acks"].append({"ts": j.get("ts", ""),
                                    "id": j.get("alert_id", ""),
                                    "action": j.get("action", "")})
            except Exception:
                pass  # 豁免（C2批1）：ack 行坏行跳过，正常容错
    fl = os.path.join(JEV, "alerts", "forward.log")
    if os.path.exists(fl):
        with open(fl, "r", encoding="utf-8", errors="replace") as f:
            res["sent"] = sum(1 for l in f if "SENT" in l)
    return res


def get_disks():
    out = {}
    for letter in ("E:\\", "D:\\", "C:\\"):
        try:
            u = shutil.disk_usage(letter)
            out[letter[0]] = {"free_gb": round(u.free / 2 ** 30, 1),
                              "total_gb": round(u.total / 2 ** 30, 1)}
        except Exception:
            pass  # 豁免（C2批1）：盘符可能不存在（无 D 盘等），属常态
    return out


def get_news():
    res = {"ok": None}
    try:
        p = os.path.join(STATION, "news_collector", "status.json")
        with open(p, "r", encoding="utf-8") as f:
            s = json.load(f)
        res = {"ok": bool(s.get("last_ok")),
               "last_collect": s.get("last_collect", ""),
               "news_24h": s.get("news_24h", 0),
               "news_total": s.get("news_total", 0),
               "push_last_run": s.get("push_last_run", 0),
               "sources": s.get("sources", {}),
               "updated": s.get("updated", "")}
    except Exception:
        log_exc("get_news:news_collector/status.json")  # C2批1
    try:
        with open(os.path.join(STATION, "news_collector", "ai_brief.json"),
                  "r", encoding="utf-8") as f:
            res["ai"] = json.load(f)
    except Exception:
        res["ai"] = None  # 豁免（C2批1）：AI 晨报未生成属常态，缺文件≠故障
    try:
        with open(os.path.join(STATION, "news_collector", "ai_review.json"),
                  "r", encoding="utf-8") as f:
            res["review"] = json.load(f)
    except Exception:
        res["review"] = None  # 豁免（C2批1）：AI 复盘未生成属常态
    return res






def get_gzh():
    try:
        with open(os.path.join(STATION, "gzh", "drafts", "index.json"),
                  "r", encoding="utf-8") as f:
            items = json.load(f)
        return {"drafts": items[:8]}
    except Exception:
        log_exc("get_gzh:gzh/drafts/index.json")  # C2批1
        return {"drafts": []}


def get_video():
    try:
        with open(os.path.join(STATION, "video", "output", "index.json"),
                  "r", encoding="utf-8") as f:
            items = json.load(f)
        return {"videos": items[:8]}
    except Exception:
        log_exc("get_video:video/output/index.json")  # C2批1
        return {"videos": []}


def build_overview():
    services = [
        {"name": "智策前端", "port": 5175},
        {"name": "智策 API", "port": 8102},
        {"name": "卡片回调", "port": 9104},
        {"name": "Ollama", "port": 11434},
    ]
    for s in services:
        s["ok"] = tcp_ok(s["port"])
    return {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "containers": get_containers(),
        "tasks": get_tasks(),
        "backups": get_backups(),
        "alerts": get_alerts(),
        "disks": get_disks(),
        "services": services,
        "news": get_news(),
        "quotes": get_quotes(),
        "backtest": get_backtest(),
        "gzh": get_gzh(),
        "video": get_video(),
    }






# K-STOCK-037 全站超级助手浮动图标（非 _COCKPIT_CSS 页的内联版）
_HERMES_UP_CACHE = (0.0, False)  # (ts, up) 30s TTL 探活缓存 K-STOCK-101










# 055-L3 短屏化：长页锚点条+分区折叠（h2>=6 折叠、>=4 锚点；flex/grid 守卫）
_SS_TAG = '<script src="/static/shortscreen.js" defer></script>'




















































































def _freshness_warmup():
    """K56-B1 启动预热：后台线程算一次 freshness（冷算 ~100s），
    驾驶舱/数据仓首个访问者不再等冷启动。"""
    data_freshness_bg(ttl_sec=1800)




import os as _os
import threading as _th
# PORTAL_NO_WARMUP=1：跳过启动预热（测试环境用——真实 datamart.read
# 冷算 ~100s，会与测试的 sys.modules 补丁窗口产生线程竞态）。
if _os.environ.get("PORTAL_NO_WARMUP") != "1":
    _th.Thread(target=_freshness_warmup, daemon=True).start()












































def build_brief():
    """首页四板块卡的动态小字（轻量，直接读快照+news.db 计数）。"""
    pre = _read_json(os.path.join(REPORTS_DIR, "premarket_latest.json"))
    snap = _read_json(os.path.join(REPORTS_DIR, "rps_latest.json"))
    buy = [r for r in snap.get("res", [])
           if r.get("action") == "买入"]
    nw = _news_db()
    # G8 来源评级→门户红点：断流表数 / catalog 滞后在首页情报卡冒红/琥珀点
    # （纯文件读，与 /kg 折叠面板同一产物 kg_source_rating.json，单一数据源）
    rt = _read_json(os.path.join(QBT_DIR, "data",
                                 "kg_source_rating.json"))
    st = rt.get("staleness") or {}
    return {
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "fin": {"verdict": pre.get("verdict"), "prob": pre.get("prob"),
                "buy": len(buy), "latest": snap.get("latest", "")},
        "intel": {"today": nw["today"], "hit": nw["hit"], **nw["dist"]},
        "kg": {"stale": len(st.get("stale_tables") or []),
               "catalog_stale": bool(st.get("catalog_stale")),
               "catalog_age_days": st.get("catalog_age_days"),
               "ts": rt.get("ts")},
    }






















































class Handler(api_data.Handler, api_chanlun_pages.Handler, api_backtest.Handler, api_kg.Handler, api_bridge.Handler, api_portfolio.Handler, api_screen.Handler, http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # K82 访问日志：一行式留痕（判断「用户是否看过页面」的唯一取证
        # 通道）；独立文件，不与 stderr 错误日志混写；失败不阻断请求。
        # C6b：写前按大小轮转（>50MB → .1），防止无限增长吃满磁盘
        if "/favicon" in fmt:
            return
        try:
            _log_rotate(_ACCESS_LOG)
            with open(_ACCESS_LOG, "a",
                      encoding="utf-8", errors="ignore") as f:
                f.write("%s %s %s\n" % (
                    time.strftime("%Y-%m-%d %H:%M:%S"),
                    self.client_address[0], fmt % args))
        except Exception:
            pass

    def _is_local_req(self):
        """P0-1b：请求是否来自本机可信域（判定规则见模块头注释）。"""
        if not (self.headers.get("X-Portal-Proxy") or "").strip():
            return True   # 无反代标记 = 本机直连 8091（已绑回环）
        rip = (self.headers.get("X-Real-IP") or "").strip()
        return rip in ("127.0.0.1", "::1")

    def _check_portal_token(self):
        """P0-1b：变更类 POST 鉴权。本机放行；反代+局域网须 token 常量比对。"""
        if self._is_local_req():
            return True
        # --- X1 菜鸟登录校验（cainiao_auth.py，2026-10-09 接线）---
        # 机器通道（X-Portal-Token 常量比对）/ Bearer 菜鸟 accessToken 远程
        # 校验（60s 进程内缓存）；菜鸟不可达按 CAINIAO_AUTH_MODE 降级
        # （默认 degraded=放行，保产线）。未命中则落到原常量比对，兼容。
        import cainiao_auth
        if cainiao_auth.verify_request(self.headers, self.client_address[0],
                                       portal_token=_PORTAL_TOKEN).ok:
            return True
        got = (self.headers.get("X-Portal-Token") or "").strip()
        return bool(got) and hmac.compare_digest(got, _PORTAL_TOKEN)

    def _token_tag(self):
        """P0-1b 前端联动：仅对本机来源页面注入 token + fetch 拦截，
        页面 JS 发 POST 自动带 X-Portal-Token；局域网来源页面不注入，
        攻击者从页面拿不到 token（token 不经局域网分发）。"""
        if not self._is_local_req():
            return ""
        return (
            '<script>window.__PORTAL_TOKEN=%s;</script>'
            '<script>(function(){var T=window.__PORTAL_TOKEN||"";'
            'if(!T)return;var of=window.fetch;'
            'window.fetch=function(u,o){o=o||{};o.headers=o.headers||{};'
            'if((o.method||"GET").toUpperCase()==="POST"'
            '&&!o.headers["X-Portal-Token"])o.headers["X-Portal-Token"]=T;'
            'return of.call(this,u,o);};})();</script>'
            % json.dumps(_PORTAL_TOKEN))

    def _json(self, obj, code=200):
        # NaN/Inf 递归清洗→null：Python json.dumps 默认输出 NaN 字面量（非标准
        # JSON），浏览器 JSON.parse 直接 SyntaxError→前端渲染链全断（2026-10-06
        # 大屏警告条实案，九维断更日统计值出 NaN）。清洗后前端 .get 容忍 null。
        def _san(o):
            if isinstance(o, float):
                return None if (o != o or o in (float("inf"), float("-inf"))) else o
            if isinstance(o, dict):
                return {k: _san(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [_san(v) for v in o]
            return o
        body = json.dumps(_san(obj), ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body):
        # 055-L4 全站令牌前置：tokens.css 只含 :root 变量（零元素规则），
        # 对未用令牌的页面无副作用；已带链接的页不重复注入
        if "</head>" in body and "static/tokens.css" not in body:
            body = body.replace(
                "</head>",
                '<link rel="stylesheet" href="/static/tokens.css"></head>', 1)
        b = body.encode("utf-8")
        b += _BTSEL_TAG.encode("utf-8")   # K255 勾选回测 JS（幂等守卫 window.__k255bt）
        b += _SS_TAG.encode("utf-8")      # 055-L3 短屏化 JS（锚点条+折叠，幂等守卫 window.__SS__）
        b += self._token_tag().encode("utf-8")  # P0-1b 本机页面注入 token fetch 拦截
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _html_open(self, chunk):
        """U4-A 流式响应：先发响应头+首段并 flush（不设 Content-Length，
        HTTP/1.0 读至连接关闭，浏览器渐进渲染骨架），后续 wfile.write 续发。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(chunk.encode("utf-8"))
        self.wfile.flush()

    def _html_more(self, chunk):
        """U4-A 流式续发（客户端中断时静默收尾，避免线程栈噪音）。"""
        try:
            self.wfile.write(chunk.encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def _html_file(self, path, nav_key=None):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                    "<title>暂无报告</title><style>body{background:#0d1117;"
                    "color:#8b949e;font-family:'Microsoft YaHei';display:flex;"
                    "align-items:center;justify-content:center;height:100vh}"
                    "</style></head><body>暂无回测报告（运行 runner.py 后生成）"
                    "</body></html>").encode("utf-8")
        # 静态页按需注入板块导航（nav_key 显式传入才注入；
        # 页面已自带导航则跳过。runner 等脚本重新生成文件后注入依然生效）
        if nav_key and b"<body" in body:
            try:
                t = body.decode("utf-8", "ignore")
                if "首页" not in t:
                    i = t.find("<body")
                    i = t.find(">", i) + 1
                    t = (t[:i] + "<div style='padding:12px 14px 0'>"
                         + pill_nav(nav_key) + "</div>" + t[i:])
                    body = t.encode("utf-8")
            except Exception:
                pass
        # 058 全站统一：文件页同样幂等前置 tokens.css（:root 变量零副作用；
        # 生成器重写文件后注入依然生效——与 _html 出口同构）
        try:
            t = body.decode("utf-8", "ignore")
            if "</head>" in t and "static/tokens.css" not in t:
                t = t.replace(
                    "</head>",
                    '<link rel="stylesheet" href="/static/tokens.css"></head>',
                    1)
                body = t.encode("utf-8")
        except Exception:
            pass
        body += _BTSEL_TAG.encode("utf-8")   # K255 静态报告页同样注入
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)



    # ── L1 金融驾驶舱（任务导向：查一只股票 / 今天能买吗 / 买什么） ──
    _COCKPIT_CSS = """<link rel='stylesheet' href='/static/tokens.css'>
<style>
body{background:var(--bg-page,#0d1117);color:var(--text,#e6edf3);font-family:-apple-system,
'Segoe UI','Microsoft YaHei',sans-serif;max-width:1200px;margin:0 auto;
padding:22px 16px}
h1{font-size:22px;margin:2px 0 0}
a{color:var(--link,#58a6ff);text-decoration:none}
.dim{color:var(--text-dim,#8b949e);font-size:12px;line-height:1.7}
.sec{border:1px solid var(--border,#30363d);border-radius:12px;padding:15px 17px;
background:var(--bg-surface,#0f141c);margin-top:14px}
.sec h2{font-size:15px;margin:0 0 4px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
td,th{padding:8px 10px;border-bottom:1px solid var(--border-weak,#21262d);text-align:left}
th{color:var(--text-dim,#8b949e);font-weight:500;font-size:12px}
.gates{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:8px}
.gate{border:1px solid var(--border,#30363d);border-radius:10px;padding:12px 13px;
background:var(--bg-raised,#161b22);display:block;color:var(--text,#e6edf3)}
.g-on{border-color:var(--color-ok,#2ea043)}.g-off{border-color:var(--color-err,#f85149)}
.tp{display:inline-block;margin:4px 6px 0 0;padding:7px 13px;
border:1px solid var(--border,#30363d);border-radius:16px;font-size:12.5px;
background:var(--bg-raised,#161b22)}
.doc{display:inline-block;margin:7px 12px 0 0;font-size:13.5px}
/* 可折叠区块 */
details.sec{padding:0;overflow:hidden}
details.sec>summary{cursor:pointer;padding:13px 17px;list-style:none;
display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;user-select:none}
details.sec>summary::-webkit-details-marker{display:none}
details.sec>summary::before{content:"\\25B6";font-size:10px;color:var(--text-dim,#8b949e);
transition:transform .15s;flex:none;margin-top:2px}
details.sec[open]>summary::before{transform:rotate(90deg)}
details.sec>summary h2{display:inline;font-size:15px}
details.sec>.sec-body{padding:0 17px 15px;border-top:1px solid var(--border-weak,#21262d)}
/* 双列网格 */
@media(min-width:960px){.duo{display:grid;grid-template-columns:1fr 1fr;
gap:14px;align-items:start}}
/* 吸顶表头 */
	table.sticky th{position:sticky;top:0;background:var(--bg-surface,#0f141c);z-index:1}
	/* 策略动态卡（B 区：注册表驱动，新增策略零改） */
	.sstat{display:block;border:1px solid var(--border,#30363d);border-radius:10px;
	padding:11px 13px;background:var(--bg-raised,#161b22);color:var(--text,#e6edf3)}
	.sstat:hover{border-color:var(--link,#58a6ff)}
	.sstat .st{font-size:12px;color:var(--text-dim,#8b949e);margin-bottom:3px}
	.sstat .sv{font-size:17px;font-weight:700;line-height:1.3}
	.sstat .su{font-size:11px;color:var(--text-dim,#8b949e);margin-top:4px}
	/* 中式红涨绿跌（2026-10-01 全站统一） */
	.up{color:var(--color-up,#f85149)}.dn{color:var(--color-dn,#3fb950)}.mu{color:var(--color-warn,#d29922)}
	/* 分组 pill 导航 */
	.navgrp{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 18px}
	.navgrp .gl{font-size:11px;color:var(--text-dim,#8b949e);padding:6px 0}
	/* 长页切割 Tab（U3）：一屏一任务 */
	.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-top:16px;
	position:sticky;top:0;background:var(--bg-page,#0d1117);z-index:5;padding:6px 0 0}
	.tabs button{background:var(--bg-raised,#161b22);border:1px solid var(--border,#30363d);
	border-radius:10px 10px 0 0;color:var(--text-dim,#8b949e);font-size:13px;
	padding:8px 16px;cursor:pointer;font-family:inherit}
	.tabs button:hover{color:var(--text,#e6edf3);border-color:var(--link,#58a6ff)}
	.tabs button.on{background:var(--bg-surface,#0f141c);color:var(--text,#e6edf3);font-weight:600}
	.tabc{display:none}
	.tabc.on{display:block}
	/* U4-C 表头排序指示 */
	th.sortable{cursor:pointer;user-select:none}
	th.sortable:hover{color:var(--link,#58a6ff)}
	th[data-sc='a']:after{content:' \2191';font-size:10px;color:var(--link,#58a6ff)}
	th[data-sc='d']:after{content:' \2193';font-size:10px;color:var(--link,#58a6ff)}
	/* U4-D 窄屏表格横滚（桌面 overflow 保持默认，不破坏 sticky 吸顶） */
	.tblx{overflow-x:visible}
	@media(max-width:820px){.tblx{overflow-x:auto;
	-webkit-overflow-scrolling:touch}}
	/* U4-F 返回顶部 */
	#_topbtn{position:fixed;right:18px;bottom:18px;width:42px;height:42px;
	border-radius:50%;border:1px solid var(--border,#30363d);background:var(--bg-raised,#161b22);
	color:var(--text-dim,#8b949e);font-size:15px;cursor:pointer;display:none;z-index:99;
	box-shadow:0 2px 10px rgba(0,0,0,.4)}
	#_topbtn.show{display:block}
	#_topbtn:hover{color:var(--text,#e6edf3);border-color:var(--link,#58a6ff)}
	/* K-STOCK-037 全站超级助手浮动图标（右下角，与回到顶部按钮上下错开） */
	#_hermesfab{position:fixed;right:18px;bottom:70px;width:46px;height:46px;
	border-radius:50%;background:var(--accent,#1f6feb);border:2px solid var(--link,#58a6ff);
	font-size:22px;line-height:42px;text-align:center;z-index:100;
	cursor:pointer;user-select:none;text-decoration:none;
	box-shadow:0 2px 12px rgba(31,111,235,.45)}
	#_hermesfab:hover{background:#388bfd}
	</style>
	<script>
	window._tab=function(btn,id){var w=btn.closest('.tabwrap');
	w.querySelectorAll('.tabs button').forEach(function(b){
	b.classList.toggle('on',b===btn);});
	w.querySelectorAll('.tabc').forEach(function(c){
	c.classList.toggle('on',c.id===id);});
	try{window.dispatchEvent(new Event('resize'));}catch(e){}
	/* U4-B：Tab 状态入 hash（replaceState 不污染后退栈，保留 G6 的 g=） */
	try{var h=(location.hash||'').replace(/^#/,'')
	.replace(/tab=[^&]*/g,'').replace(/&+/g,'&')
	.replace(/^&|&$/g,'');
	history.replaceState(null,'',h?'#'+h+'&tab='+id:'#tab='+id);
	}catch(e){}};
	window._tabTo=function(id){var c=document.getElementById(id);
	if(!c||c.classList.contains('on'))return;
	var w=c.closest('.tabwrap');if(!w)return;
	var btns=w.querySelectorAll('.tabs button');
	for(var i=0;i<btns.length;i++){var oc=btns[i].getAttribute('onclick')||'';
	if(oc.indexOf("'"+id+"'")>=0){btns[i].click();return;}}};
	/* U4 通用初始化：hash 恢复 Tab / 表头排序 / 表格横滚包裹 / 返回顶部 */
	window._u4init=function(){
	try{var m=/(?:^|&)tab=([^&]+)/.exec((location.hash||'').replace(/^#/,''));
	if(m&&window._tabTo)window._tabTo(decodeURIComponent(m[1]));}catch(e){}
	try{var ts=document.querySelectorAll('table');
	for(var i=0;i<ts.length;i++){var t=ts[i];
	if(t.parentNode.className==='tblx')continue;
	var w=document.createElement('div');w.className='tblx';
	t.parentNode.insertBefore(w,t);w.appendChild(t);}}catch(e){}
	try{var ts=document.querySelectorAll('table');
	for(var i=0;i<ts.length;i++){var t=ts[i],r0=t.rows[0];
	if(!r0||!r0.cells.length||r0.cells[0].tagName!=='TH')continue;
	var bad=0;
	for(var j=0;j<r0.cells.length;j++)
	if((r0.cells[j].colSpan||1)>1)bad=1;
	if(bad)continue;
	for(var j=0;j<r0.cells.length;j++){var th=r0.cells[j];
	th.className=(th.className?th.className+' ':'')+'sortable';
	th.title='点击排序';
	th.addEventListener('click',function(){
	var th=this,tb=th.closest('table');
	var ti=Array.prototype.indexOf.call(th.parentNode.cells,th);
	var asc=!(tb.getAttribute('data-sci')==String(ti)
	&&tb.getAttribute('data-sc')==='a');
	var rows=[];
	for(var r=1;r<tb.rows.length;r++)rows.push(tb.rows[r]);
	var val=function(row){
	var s=(row.cells[ti]?row.cells[ti].textContent:'').trim()
	.replace(/[,\uFF0C\s%+\u2212\u2013\u2014-]/g,'');
	var u=/\u4EBF/.test(s)?1e8:(/\u4E07/.test(s)?1e4:1);
	s=s.replace(/\u4EBF|\u4E07/g,'');
	var n=parseFloat(s);
	return{n:isNaN(n)?null:n*u,s:s};};
	rows.sort(function(a,b){var x=val(a),y=val(b);
	if(x.n!==null&&y.n!==null)return asc?x.n-y.n:y.n-x.n;
	return asc?x.s.localeCompare(y.s,'zh')
	:y.s.localeCompare(x.s,'zh');});
	var host=tb.tBodies[0]||tb;
	for(var k=0;k<rows.length;k++)host.appendChild(rows[k]);
	tb.setAttribute('data-sci',ti);
	tb.setAttribute('data-sc',asc?'a':'d');
	var h0=tb.rows[0];
	for(var q=0;q<h0.cells.length;q++)
	h0.cells[q].removeAttribute('data-sc');
	th.setAttribute('data-sc',asc?'a':'d');});}
	}}catch(e){}
	try{if(!document.getElementById('_topbtn')){
	var b=document.createElement('button');
	b.id='_topbtn';b.type='button';b.title='返回顶部';
	b.appendChild(document.createTextNode('\u25B2'));
	b.onclick=function(){window.scrollTo({top:0,behavior:'smooth'});};
	document.body.appendChild(b);
	window.addEventListener('scroll',function(){
	b.className=(window.scrollY||document.documentElement.scrollTop)
	>600?'show':'';},{passive:true});}}catch(e){}
	/* K-STOCK-037 超级助手浮动图标：全站右下角常驻 */
	try{if(!document.getElementById('_hermesfab')){
	var f=document.createElement('a');
	f.id='_hermesfab';f.href='http://127.0.0.1:8787';f.target='_blank';
	f.rel='noopener';f.title='超级助手：对话即调度全部 AI 岗位';
	f.appendChild(document.createTextNode('\U0001F9B8'));
	document.body.appendChild(f);}}catch(e){}};
	document.addEventListener('DOMContentLoaded',window._u4init);
	</script>"""




    def _hub_market_tabs(self):
        """K-STOCK-030 长页治理：hub 四市场模块段（4 张 .module 卡竖排
        + 跨市场RPS/策略库尾区）切成 Tab——点右侧市场切换不滚动。
        按市场标签锚点切分（加密卡与尾区在同一切分段，锚点法才切得开）。"""
        import re as _re_m
        try:
            hub = open(os.path.join(STATION, "quant_backtest", "reports",
                                    "bt_hub.html"), encoding="utf-8").read()
        except OSError:
            return ""
        p0 = hub.find("<h2>四市场模块")
        p1 = hub.find("</body>")
        if p0 < 0:
            return ""
        body = hub[p0:p1 if p1 > p0 else len(hub)]
        # 样式（去全局 body 样式防污染）
        s0, s1 = hub.find("<style"), hub.find("</style>")
        style = ""
        if 0 <= s0 < s1:
            style = _re_m.sub(r"body\{[^}]*\}", "", hub[s0:s1 + 8])
        anchors = [("🇨🇳 A股", "<b>🇨🇳 A股 模块</b>"),
                   ("🇺🇸 美股", "<b>🇺🇸 美股 模块</b>"),
                   ("🇭🇰 港股", "<b>🇭🇰 港股 模块</b>"),
                   ("🪙 加密", "<b>🪙 加密 模块</b>")]
        # 锚点定位；缺任一市场则整体降级为原竖排块
        pos = []
        for lb, a in anchors:
            i = body.find(a)
            if i < 0:
                return ("<div class='sec'><h2>🎯 选股中心 · 四市场模块"
                        "</span></h2>" + style + body + "</div>")
            pos.append(i)
        tail_i = body.find("跨市场 RPS 最强")
        sec_head = body[:min(pos)]
        tabs = []
        for k, (lb, _) in enumerate(anchors):
            end = pos[k + 1] if k + 1 < len(anchors) else (
                tail_i if tail_i > pos[k] else len(body))
            tabs.append((lb, body[pos[k]:end]))
        rest = body[tail_i:] if tail_i > pos[-1] else ""
        head = ("<div class='sec'><h2>🎯 选股中心 · 四市场模块"
                "<span class='dim' style='font-weight:400;font-size:12px'>"
                "（四法门共振 · 自量化中心迁入 · 点市场标签切换）"
                "</span></h2>" + style)
        return head + _tabs_html("hubmkt", tabs) + rest + "</div>"

    def _qpzm_page(self, sub=""):
        """K-STOCK-032 多页面联动：主页=驾驶舱一屏（""），子页=
        res 共振信号 / rps RPS榜 / hub 四市场模块 / batch 批次回测 /
        spec 说明书——子页顶部页面级导航条互相联动。"""
        import html as _h
        e = _h.escape
        snap = _read_json(os.path.join(REPORTS_DIR, "rps_latest.json"))
        res = snap.get("res", [])

        # ① 环境闸门（一票否决层）
        gates = []
        for ev in snap.get("envs", []):
            mk = _env_market_key(ev.get("label", ""))
            if not mk:
                continue
            safe = bool(ev.get("safe"))
            gates.append(
                "<a class='gate %s' href='/bt/m/%s'><b>%s</b>"
                "<span style='float:right;font-size:16px;font-weight:700'>"
                "%s</span><div class='dim' style='margin-top:6px'>%s</div>"
                "<div style='margin-top:6px;font-size:12.5px;color:%s'>%s"
                "</div></a>"
                % ("g-on" if safe else "g-off", mk, MKT_LABEL[mk],
                   e(str(ev.get("score", "—"))),
                   e(str(ev.get("state", ""))),
                   "#3fb950" if safe else "#f85149",
                   "✅ 放行 · 可开新仓" if safe else "⛔ 收紧 · 一票否决"))
        gates_html = ("<div class='gates'>%s</div>" % "".join(gates)
                      if gates else "<div class='dim'>暂无快照</div>")

        # ② 自选池共振信号（按 建议 → 共振分 排序，全量展示）
        # K-STOCK-031：全市场混排一张表 → 按 A股/美股/港股/虚拟币
        # 四市场 Tab 切换（长页治理续，与 RPS 榜/hub 模块同款交互）
        act_c = {"买入": "#f85149", "观察": "#d29922", "回避": "#3fb950"}
        order = {"买入": 0, "观察": 1, "回避": 2}

        def _base(r):
            return str(r.get("action", "")).replace("（环境闸门）", "")

        rows = sorted(res, key=lambda r: (order.get(_base(r), 3),
                                          -(r.get("score") or 0)))
        n_b = sum(1 for r in res if _base(r) == "买入")
        n_w = sum(1 for r in res if _base(r) == "观察")
        n_a = len(res) - n_b - n_w

        def _res_row(r):
            dims = " · ".join(
                "%s%s" % (lb, ("—" if r.get(k) is None
                               else "%d" % round(r[k])))
                for k, lb in (("tech", "技"), ("jev", "J"), ("rps", "R"),
                              ("env", "环")))
            return (
                "<tr><td><a href='/kg/stock/%s'>%s</a><div class='dim'>"
                "%s</div></td>"
                "<td style='color:%s;white-space:nowrap'>%s</td>"
                "<td><b>%d</b></td><td class='dim' style='font-size:12px;"
                "white-space:nowrap'>%s</td><td>%s</td>"
                "<td class='dim'>%s</td></tr>"
                % (e(str(r.get("code", ""))), e(str(r.get("name", ""))),
                   e(str(r.get("code", ""))),
                   act_c.get(_base(r), "#8b949e"),
                   e(str(r.get("action", "—"))),
                   round(r.get("score") or 0), dims,
                   ("%.2f" % r["px"]) if r.get("px") else "—",
                   e(str(r.get("date", "")))))

        _res_th = ("<tr><th>标的</th><th>建议</th><th>共振分</th>"
                   "<th>四维得分</th><th>现价</th><th>日期</th></tr>")
        _res_mkt = [("🇨🇳 A股", "CN"), ("🇺🇸 美股", "US"),
                    ("🇭🇰 港股", "HK"), ("🪙 虚拟币", "CRYPTO")]
        if res:
            res_tabs = []
            for lb, mk in _res_mkt:
                mrows = [r for r in rows if str(r.get("market", "")) == mk]
                mtrs = "".join(_res_row(r) for r in mrows) or \
                    ("<tr><td colspan='6' class='dim'>本市场今日暂无"
                     "（每日 17:35 刷新）</td></tr>")
                res_tabs.append((lb, "<table>%s%s</table>" % (_res_th, mtrs)))
            res_blk = (
                "<div class='dim' style='margin-bottom:4px'>全池 %d 只 · "
                "<span style='color:#f85149'>买入 %d</span> · <span style="
                "'color:#d29922'>观察 %d</span> · <span style='color:#3fb950'>"
                "回避 %d</span></div>"
                % (len(res), n_b, n_w, n_a))
            res_blk += _tabs_html("ressig", res_tabs)
            res_blk += (
                "<div class='dim' style='margin-top:6px'>四维：技=4策略多头占比"
                "±MA20修正 · J=近7天关联新闻多空 · R=全市场RPS · 环=市场闸门分"
                "；score≥70 买入 / 60-69 观察 / 其余回避；闸门收紧强制「回避（"
                "环境闸门）」；缺维自动降权（如无JEV：技.467 / R.333 / 环.20）"
                "</div>")
        else:
            res_blk = "<div class='dim'>暂无快照</div>"

        # ③ 全市场 RPS 强势榜（策略的选股漏斗出口）
        # K-STOCK-030：四市场竖排改 Tab 切换（长页治理——点右侧标签，
        # 不再往下滚四张表）
        top_lbl = {"CN": "🇨🇳 A股", "US": "🇺🇸 美股", "HK": "🇭🇰 港股",
                   "CRYPTO": "🪙 加密"}
        top_mkt = {"CN": "cn", "US": "us", "HK": "hk", "CRYPTO": "crypto"}
        _mk_order = ["CN", "US", "HK", "CRYPTO"]
        top_tabs = []
        for mk in _mk_order:
            trows = (snap.get("tops") or {}).get(mk)
            if not trows:
                continue
            trs = ""
            for t in trows[:8]:
                chg = t.get("chg20") or 0
                trs += (
                    "<tr><td>%s<div class='dim'>%s</div></td><td>%s</td>"
                    "<td><b>%.1f</b></td><td style='color:%s'>%+.1f%%</td>"
                    "</tr>"
                    % (e(str(t.get("sym", ""))), e(str(t.get("name", ""))),
                       ("%.2f" % t["px"]) if t.get("px") else "—",
                       float(t.get("mix") or 0),
                       "#f85149" if chg >= 0 else "#3fb950", float(chg)))
            top_tabs.append((
                top_lbl.get(mk, mk),
                "<div style='margin-top:4px'><b><a href='/bt/m/%s'>%s 强势"
                "榜 →</a></b><table><tr><th>代码 / 名称</th><th>现价</th>"
                "<th>RPS复合</th><th>20日</th></tr>%s</table></div>"
                % (top_mkt.get(mk, "cn"), e(top_lbl.get(mk, mk)), trs)))
        if top_tabs:
            top_blk = _tabs_html("rpsrank", top_tabs)
        else:
            top_blk = "<div class='dim'>暂无快照</div>"

        # ④ 策略说明书
        spec = (
            "<div class='dim' style='font-size:13px;line-height:2.1'>"
            "一句话：<b style='color:#e6edf3'>全市场动量分层 → RPS 强度筛选"
            " → 自选池四维共振打分 → 环境闸门一票否决</b><br>"
            "共振公式：技术×0.35 + JEV消息面×0.25 + RPS×0.25 + 环境×0.15"
            "（缺维按比例自动降权）<br>"
            "RPS 口径：全市场 N 日涨幅百分位(0-100)；A股 50/120/250 复合"
            "(0.4/0.3/0.3)，美股/加密 RPS50，港股 RPS50+120<br>"
            "数据：A股 tushare 日线 · 美股/港股/加密 zhice-db 全市场 · "
            "每日 17:35 刷新<br>"
            "与原版差异：原版四维为 技术.35/板块.30/RPS.20/环境.15，本站以"
            " JEV 消息面替代板块维度；方法论借鉴 D:\\青萍之末2，只读借鉴，"
            "数据与实现独立</div>")

        # ⑤ 验证与操作（纸面链路）
        tools = "".join(
            "<a class='tp' href='%s'>%s</a>" % (href, lb) for lb, href in [
                ("🚦 明日信号", "/bt-signals"),
                ("💼 持仓跟踪（纸面）", "/bt-positions"),
                ("📊 寻优榜单", "/bt-opt"),
                ("📄 回测报告", "/bt-report"),
                ("🧪 基础策略库", "/bt"),
            ])

        # ── K-STOCK-032 多页面联动（老田：不要长图，拆多页互相跳转）──
        # 主页=驾驶舱一屏（闸门+统计+模块入口卡）；共振/RPS/四市场模块/
        # 批次回测/说明书各自独立子页；子页顶部页面级导航条联动。
        _SUBS = [("🌊 工作台", "/bt-qpzm", ""),
                 ("🎯 共振信号", "/bt-qpzm/res", "res"),
                 ("📈 RPS 强势榜", "/bt-qpzm/rps", "rps"),
                 ("🗂 四市场模块", "/bt-qpzm/hub", "hub"),
                 ("📊 批次回测", "/bt-qpzm/batch", "batch"),
                 ("📖 策略说明书", "/bt-qpzm/spec", "spec")]

        def _shell(title, cur, inner):
            nav = "".join(
                ("<span style='%s'>%s</span>" % (_PILL_ON, lb)) if key == cur
                else ("<a href='%s' style='%s'>%s</a>" % (href, _PILL, lb))
                for lb, href, key in _SUBS)
            # K-STOCK-036：返回键置前 + 页面级联动导航条
            nav = self._back_link_html() + nav
            # 注意：title 走纯拼接不进 % 模板（% 与 + 混用坑第四次变体）
            return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                    "<meta name='viewport' content='width=device-width,"
                    "initial-scale=1'><title>" + title
                    + " · 青萍之末</title>"
                    + self._COCKPIT_CSS + "</head><body>"
                    + pill_nav("qpzm")
                    + "<div style='display:flex;gap:6px;flex-wrap:wrap;"
                    "margin:12px 0'>" + nav + "</div>"
                    + "<h1>" + title + "</h1><div class='dim'>数据截至 "
                    + e(str(snap.get("latest", "—"))) + "（"
                    + e(str(snap.get("ts", ""))) + "）</div>"
                    + inner
                    + "<div class='dim' style='margin-top:20px'>纸面研究不"
                    "构成投资建议 · 方法论源自青萍之末2（RPS：全市场N日涨幅"
                    "百分位；共振：四维加权、缺维降权、环境一票否决）"
                    "</div></body></html>")

        def _card(href, border, icon, name, desc, stat):
            return ("<a href='%s' style='display:block;border:1px "
                    "solid %s;border-radius:12px;background:#161b22;"
                    "padding:16px 18px;text-decoration:none;"
                    "color:#e6edf3'>"
                    "<div style='font-size:17px;font-weight:700'>"
                    "%s %s</div>"
                    "<div class='dim' style='font-size:12.5px;"
                    "margin:6px 0 10px'>%s</div>"
                    "<div style='font-size:13px'><b>%s</b>"
                    "<span style='float:right;color:%s;font-size:"
                    "12.5px'>进入 →</span></div></a>"
                    % (href, border, icon, name, desc, stat, border))

        if sub == "res":
            inner = ("<div class='sec'><h2>🎯 自选池共振信号</h2>" + res_blk
                     + "</div>")
            self._html(_shell("🌊 共振信号 · 四市场", "res", inner))
            return
        if sub == "rps":
            inner = ("<div class='sec'><h2>📈 全市场 RPS 强势榜"
                     "（选股漏斗）</h2>" + top_blk + "</div>")
            self._html(_shell("📈 RPS 强势榜 · 四市场", "rps", inner))
            return
        if sub == "hub":
            inner = self._hub_market_tabs()
            self._html(_shell("🗂 四市场模块 · 自选池共振", "hub", inner))
            return
        if sub == "batch":
            inner = self._qpzm_batch_blk(snap)
            self._html(_shell("📊 每日批次回测", "batch", inner))
            return
        if sub == "spec":
            inner = "<div class='sec'><h2>📖 策略说明书</h2>" + spec + "</div>"
            self._html(_shell("📖 策略说明书", "spec", inner))
            return

        # 主页 = 驾驶舱一屏：闸门 → 核心统计 → 模块入口卡（联动枢纽）
        hub_txt = "跨市场 RPS · 策略库 · 策略工具"
        cards = "".join([
            _card("/bt-qpzm/res", "#f85149", "🎯", "共振信号",
                  "自选池四维共振 · 建议→共振分排序 · 四市场 Tab",
                  "买入 %d · 观察 %d · 回避 %d" % (n_b, n_w, n_a)),
            _card("/bt-qpzm/rps", "#58a6ff", "📈", "RPS 强势榜",
                  "策略的选股漏斗出口 · 四市场各 Top8",
                  "全市场 N 日涨幅百分位"),
            _card("/bt-qpzm/hub", "#e3b341", "🗂", "四市场模块",
                  "四法门共振按市场分域 · 同源市场分域页",
                  hub_txt),
            _card("/bt-qpzm/batch", "#3fb950", "📊", "每日批次回测",
                  "K238 每日选股批次的历史回放对照",
                  "随每日任务积累"),
            _card("/bt-qpzm/spec", "#8b949e", "📖", "策略说明书",
                  "共振公式 · RPS 口径 · 与原版差异",
                  "评分权重一览"),
        ])
        inner = (
            # ② 工具链统一紧贴标题
            "<div style='display:flex;gap:6px;flex-wrap:wrap;"
            "margin:10px 0'>" + tools + "</div>"
            # ③ 环境闸门（一票否决层，决策第一眼）
            + "<div class='sec'><h2>🚦 环境闸门 · 一票否决层</h2>"
            + gates_html + "</div>"
            # 核心统计条
            + "<div class='sec'><h2>🎯 自选池速览</h2><div class='dim' "
            "style='font-size:15px'>全池 <b>%d</b> 只 · "
            "<span style='color:#f85149'>买入 <b>%d</b></span> · "
            "<span style='color:#d29922'>观察 <b>%d</b></span> · "
            "<span style='color:#3fb950'>回避 <b>%d</b></span>"
            "（明细见「共振信号」子页）</div></div>"
            % (len(res), n_b, n_w, n_a)
            # 模块入口卡（联动枢纽）
            + "<h2 style='margin:16px 0 10px'>🧭 板块入口 · 多页联动</h2>"
            + "<div style='display:grid;grid-template-columns:repeat("
            "auto-fit,minmax(260px,1fr));gap:12px'>" + cards + "</div>")
        self._html(_shell("🌊 青萍之末 · 共振选股工作台", "", inner))

    # ── K-STOCK-035 总入口门户：哪里是什么、我的入口在哪里 ──
    # ── K-STOCK-113 开发指挥台：dev_events.jsonl 只读投影 ──────────
    _DEV_EVENTS = os.path.join(ROOT, "data", "dev_events.jsonl")
    _DEV_KINDS = ("task_start", "commit", "blocked", "merge_done", "guard_fail")

    def _devops_data(self):
        """今日开发态势：四类事件计数 + 活跃 Agent + 工单台账 + 最近事件。"""
        day = time.strftime("%Y-%m-%d")
        evs = []
        try:
            with open(self._DEV_EVENTS, "r", encoding="utf-8") as f:
                for ln in f:
                    try:
                        evs.append(json.loads(ln))
                    except Exception:
                        continue
        except OSError:
            pass
        today = [e for e in evs if e.get("day") == day]
        by_kind = {k: 0 for k in self._DEV_KINDS}
        agents = {}
        tasks = {}
        for e in today:
            by_kind[e.get("kind", "?")] = by_kind.get(e.get("kind", "?"), 0) + 1
            if e.get("agent"):
                agents[e["agent"]] = agents.get(e["agent"], 0) + 1
            t = e.get("task") or "(未编号)"
            st = tasks.setdefault(t, {"events": 0, "last": "", "last_kind": "",
                                      "last_detail": "", "last_ide": "",
                                      "last_model": ""})
            st["events"] += 1
            st["last"] = e.get("ts", "")
            st["last_kind"] = e.get("kind", "")
            st["last_detail"] = e.get("detail", "")
            st["last_ide"] = e.get("ide", "")
            st["last_model"] = e.get("model", "")
        return {"ok": True, "day": day, "total": len(today),
                "by_kind": by_kind, "agents": agents,
                "tasks": tasks, "recent": today[-30:][::-1]}

    def _devops_page(self):
        """开发指挥台页（108 原型落地版·只读）：四类计数 + 工单表 + 事件流。"""
        import html as _h
        e = _h.escape
        cards = "".join(
            "<div style='background:var(--bg-surface);border:1px solid var(--border);"
            "border-radius:10px;padding:12px 15px;'>"
            "<div style='font-size:11px;color:var(--text-dim);'>%s</div>"
            "<div style='font-size:22px;font-weight:700;margin-top:4px;' id='c-%s'>—</div></div>"
            % (label, key)
            for key, label in [("task_start", "🚀 建单"), ("commit", "📝 提交"),
                               ("merge_done", "✅ 合并"), ("blocked", "⛔ 卡壳")])
        html = ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<title>开发指挥台 · 零界</title>"
                "<link rel='stylesheet' href='/static/tokens.css'>"
                "<style>body{font-family:'Segoe UI','Microsoft YaHei',system-ui,sans-serif;"
                "background:var(--bg-page);color:var(--text);}"
                "main{max-width:1080px;margin:0 auto;padding:0 22px 46px;}"
                "table{width:100%%;border-collapse:collapse;font-size:12px;margin-top:8px;}"
                "th{color:var(--text-dim);text-align:left;padding:7px 9px;"
                "border-bottom:1px solid var(--border);font-weight:600;}"
                "td{padding:7px 9px;border-bottom:1px solid var(--border-weak);}"
                ".num{font-family:Consolas,monospace;}"
                ".bdg{font-size:10.5px;padding:2px 9px;border-radius:999px;border:1px solid var(--border);}"
                "</style></head><body>"
                "<header style='display:flex;justify-content:space-between;align-items:center;"
                "padding:20px 48px 10px;max-width:1080px;margin:0 auto;'>"
                "<div><h1 style='font-size:17px;'>🏭 开发指挥台</h1>"
                "<div style='font-size:10.5px;color:var(--text-dim);letter-spacing:2px;'>"
                "DEVOPS · K-STOCK-113 · 事件自动回流</div></div>"
                "<div style='display:flex;gap:14px;'>"
                "<a href='/audit-ai' style='font-size:12px;color:var(--color-ok);'>"
                "🛡 安全审计 AI</a>"
                "<a href='/' style='font-size:12px;'>← 返回首页</a></div></header><main>"
                "<div id='sum' style='display:grid;grid-template-columns:repeat(4,1fr);gap:10px;'>"
                + cards + "</div>"
                "<h2 style='font-size:14px;margin:22px 0 4px;'>在办工单</h2>"
                "<div style='background:var(--bg-raised);border:1px solid var(--border);"
                "border-radius:12px;padding:6px 14px 10px;'>"
                "<table><tr><th>工单</th><th>IDE / 模型</th><th>事件数</th><th>最近事件</th><th>最近动态</th><th>时间</th></tr>"
                "<tbody id='tasks'><tr><td class='dim'>加载中…</td></tr></tbody></table></div>"
                "<h2 style='font-size:14px;margin:22px 0 4px;'>Agent 工时榜 <span class='dim' "
                "style='font-size:11px;'>今日事件数排行</span></h2>"
                "<div id='board' style='background:var(--bg-raised);border:1px solid var(--border);"
                "border-radius:12px;padding:10px 16px;font-size:12px;'></div>"
                "<h2 style='font-size:14px;margin:22px 0 4px;'>事件流 <span class='dim' "
                "style='font-size:11px;'>最近 30 条 · 30s 自动刷新</span></h2>"
                "<div id='feed' style='background:var(--bg-surface);border:1px solid var(--border);"
                "border-radius:12px;padding:10px 16px;font-size:12px;line-height:2;'></div>"
                "</main><script>"
                "const KD={task_start:['🚀 建单','#58a6ff'],commit:['📝 提交','#8b949e'],"
                "blocked:['⛔ 卡壳','#f85149'],merge_done:['✅ 合并','#3fb950'],guard_fail:['🛑 守卫','#d29922']};"
                "async function load(){try{const r=await fetch('/api/devops',{cache:'no-store'});"
                "if(!r.ok)return;const d=await r.json();"
                "document.getElementById('c-task_start').textContent=d.by_kind.task_start||0;"
                "document.getElementById('c-commit').textContent=d.by_kind.commit||0;"
                "document.getElementById('c-merge_done').textContent=d.by_kind.merge_done||0;"
                "document.getElementById('c-blocked').textContent=d.by_kind.blocked||0;"
                "const es=Object.entries(d.tasks||{}).sort((a,b)=>(b[1].last||'').localeCompare(a[1].last||''));"
                "document.getElementById('tasks').innerHTML=es.map(([t,s])=>"
                "\"<tr><td class='num'>\"+t+\"</td>\""
                "+\"<td class='num' style='font-size:11px;'>\""
                "+((s.last_ide||s.last_model)?(s.last_ide||'—')+'<br><span class=dim>'+(s.last_model||'—')+'</span>':'<span class=dim>—</span>')+\"</td>\""
                "+\"<td class='num'>\"+s.events+\"</td>\""
                "+\"<td><span class='bdg'>\"+((KD[s.last_kind]||[s.last_kind])[0])+\"</span></td>\""
                "+\"<td style='max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'>\""
                "+(s.last_detail||'')+\"</td>\""
                "+\"<td class='num dim'>\"+(s.last||'').slice(11)+\"</td></tr>\").join('')"
                "||\"<tr><td class='dim'>今日暂无工单事件</td></tr>\";"
                "const ae=Object.entries(d.agents||{}).sort((a,b)=>b[1]-a[1]);"
                "const mx=ae.length?ae[0][1]:1;"
                "document.getElementById('board').innerHTML=ae.map(([a,c])=>"
                "\"<div style='display:flex;align-items:center;gap:10px;margin:5px 0;'>\""
                "+\"<span class='num' style='min-width:110px;'>\"+a+\"</span>\""
                "+\"<div style='flex:1;height:9px;border-radius:5px;background:var(--bg-surface);'>\""
                "+\"<div style='width:\"+Math.round(c*100/mx)+\"%;height:100%;border-radius:5px;"
                "background:linear-gradient(90deg,var(--blu-600),var(--link));'></div></div>\""
                "+\"<span class='num'>\"+c+\" 事件</span></div>\").join('')"
                "||\"<span class='dim'>今日暂无 Agent 事件</span>\";"
                "document.getElementById('feed').innerHTML=(d.recent||[]).map(x=>{const k=KD[x.kind]||[x.kind,'#8b949e'];"
                "return \"<div><span class='num dim'>\"+(x.ts||'').slice(11)+\"</span> \"+"
                "\"<b style='color:\"+k[1]+\"'>\"+k[0]+\"</b> \"+"
                "\"<span class='num'>\"+(x.task||'')+(x.agent?' · '+x.agent:'')+\"</span> \"+"
                "((x.ide||x.model)?\"<span class='bdg num' style='font-size:10px;'>\"+(x.ide||'?')+(x.model?' / '+x.model:'')+\"</span> \":\"\")+"
                "\"<span class='dim'>\"+(x.detail||'')+\"</span></div>\";}).join('')"
                "||\"<span class='dim'>暂无事件——提交一次代码试试（post-commit 自动上报）</span>\";"
                "}catch(e){}}load();setInterval(load,30000);</script></body></html>")
        self._html(html)

    # ── K-STOCK-114e 独立安全审计 AI ─────────────────────────────
    _AUDIT_SYS = (
        "你是零界平台的独立安全审计官，与开发团队完全独立、只对安全负责。"
        "审计对象：代码 diff、实现方案、接口设计。"
        "审计口径：注入/XSS/越权/密钥泄露/路径穿越/竞态/资源泄漏/逻辑后门/"
        "数据校验缺失/危险调用。输出纪律："
        "①先给结论（安全/有风险/高危）②按严重度列问题：位置+成因+利用场景+修复建议 "
        "③没有问题就明说没有，不凑数④简洁，不重复代码。称呼对方为「开发侧」。")

    _AUDIT_CFG_PATH = os.path.join(ROOT, "audit_ai_config.json")

    @classmethod
    def _audit_cfg(cls):
        """审计模型配置：audit_ai_config.json（不入库）> 环境变量 > 本机 Ollama。
        json: {"api":"openai","base":"https://…/v1","key":"sk-…","model":"…"}"""
        try:
            with open(cls._AUDIT_CFG_PATH, encoding="utf-8") as f:
                c = json.load(f)
            if c.get("base") and c.get("key") and c.get("model"):
                c.setdefault("api", "openai")
                return c
        except Exception:
            pass
        return {"api": "ollama",
                "model": os.environ.get("AUDIT_MODEL", "qwen3.5:9b")}

    _AUDIT_ZHIPU_BASE = "https://open.bigmodel.cn/api/paas/v4"
    _AUDIT_ZHIPU_MODEL = "glm-4.5-flash"

    def _audit_backends(self):
        """云边协同回退链：外部网关 → 本地 Ollama → 智谱（密钥见配置/env）。"""
        chain = []
        cfg = self._audit_cfg()
        if cfg["api"] == "openai":
            chain.append(("gateway", cfg["base"], cfg["key"], cfg["model"]))
        chain.append(("ollama", "http://127.0.0.1:11434/api/chat", None,
                      cfg.get("model") if cfg["api"] != "openai" else "qwen3.5:9b"))
        zk = cfg.get("zhipu_key") or os.environ.get("ZHIPUAI_API_KEY", "")
        if zk:
            chain.append(("zhipu", self._AUDIT_ZHIPU_BASE, zk, self._AUDIT_ZHIPU_MODEL))
        return chain

    def _audit_chat(self, messages, timeout=150):
        """按回退链依次尝试审计模型（网关→本地→智谱），返回 (回复, 后端名)。"""
        import urllib.request as _u
        opener = _u.build_opener(_u.ProxyHandler({}))
        last_err = None
        for name, base, key, model in self._audit_backends():
            try:
                body = json.dumps({"model": model, "stream": False,
                                   "messages": messages}).encode("utf-8")
                headers = {"Content-Type": "application/json"}
                if name == "zhipu":
                    url = base.rstrip("/") + "/chat/completions"
                    headers["Authorization"] = "Bearer " + key
                elif name == "gateway":
                    url = base.rstrip("/") + "/chat/completions"
                    headers["Authorization"] = "Bearer " + key
                else:
                    url = base
                req = _u.Request(url, data=body, headers=headers)
                with opener.open(req, timeout=timeout) as resp:
                    j = json.loads(resp.read().decode("utf-8"))
                if name == "ollama":
                    text = j.get("message", {}).get("content", "")
                else:
                    if j.get("error"):
                        raise RuntimeError("网关错误：%s" % json.dumps(
                            j["error"], ensure_ascii=False)[:150])
                    text = (j.get("choices") or [{}])[0].get(
                        "message", {}).get("content", "")
                if text:
                    return text, "%s/%s" % (name, model)
                raise RuntimeError("空回复")
            except Exception as _e:
                last_err = "%s(%s): %s" % (name, model, str(_e)[:100])
        raise RuntimeError("全部后端不可达——" + str(last_err)[:300])

    def _api_audit_chat(self, raw):
        """POST {messages:[{role,content}...], preset?:'last_commit'} → {ok,reply}。
        preset=last_commit：服务端抓最近提交 diff 注入为审计素材。"""
        try:
            req = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            self._json({"ok": False, "error": "body 非 JSON"}, 400)
            return
        msgs = [{"role": "system", "content": self._AUDIT_SYS}]
        hist = req.get("messages") or []
        for m in hist[-12:]:  # 独立会话历史（客户端持有，限 12 轮防炸上下文）
            if isinstance(m, dict) and m.get("role") in ("user", "assistant") \
                    and m.get("content"):
                msgs.append({"role": m["role"],
                             "content": str(m["content"])[:6000]})
        if req.get("preset") == "last_commit":
            try:
                d = subprocess.run(
                    ["git", "show", "--format=commit %h %s", "--unified=3",
                     "HEAD"], cwd=ROOT, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=15)
                msgs.append({"role": "user", "content":
                             "审计最近一次提交：\n```\n%s\n```"
                             % (d.stdout or "(空 diff)")[:12000]})
            except Exception as _ge:
                msgs.append({"role": "user",
                             "content": "抓取最近提交失败：%s" % _ge})
        try:
            reply, backend = self._audit_chat(msgs)
            self._json({"ok": True, "reply": reply, "backend": backend})
        except Exception as _oe:
            self._json({"ok": False,
                        "error": "审计模型不可达：%s" % str(_oe)[:160]}, 502)

    def _audit_ai_page(self):
        """独立安全审计 AI 对话页（114e）：会话客户端持有，独立模型。"""
        mdl = self._audit_cfg()["model"]
        html = ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<title>🛡 安全审计 AI · 零界</title>"
                "<link rel='stylesheet' href='/static/tokens.css'>"
                "<style>body{font-family:'Segoe UI','Microsoft YaHei',system-ui,"
                "sans-serif;background:var(--bg-page);color:var(--text);}"
                "main{max-width:900px;margin:0 auto;padding:0 20px 40px;}"
                "#log{background:var(--bg-raised);border:1px solid var(--border);"
                "border-radius:12px;padding:14px 18px;min-height:300px;"
                "max-height:56vh;overflow-y:auto;font-size:13px;line-height:1.8;}"
                ".u{margin:8px 0;padding:8px 12px;border-radius:10px;"
                "background:var(--bg-surface);border:1px solid var(--border);}"
                ".a{margin:8px 0;padding:8px 12px;border-radius:10px;"
                "background:rgba(63,185,80,.06);border:1px solid rgba(63,185,80,.35);}"
                ".a b{color:var(--color-ok);}"
                "textarea{width:100%%;box-sizing:border-box;min-height:70px;"
                "background:var(--bg-surface);color:var(--text);"
                "border:1px solid var(--border);border-radius:10px;padding:10px;"
                "font-size:13px;font-family:inherit;resize:vertical;}"
                "button{padding:8px 18px;border-radius:9px;border:1px solid var(--border);"
                "background:var(--bg-raised);color:var(--text);cursor:pointer;font-size:13px;}"
                "button.pri{border-color:var(--link);color:var(--link);}"
                "button:hover{border-color:var(--link);}"
                ".num{font-family:Consolas,monospace;}</style></head><body>"
                "<header style='display:flex;justify-content:space-between;align-items:center;"
                "padding:20px 48px 10px;max-width:900px;margin:0 auto;'>"
                "<div><h1 style='font-size:17px;'>🛡 安全审计 AI</h1>"
                "<div style='font-size:10.5px;color:var(--text-dim);letter-spacing:2px;'>"
                "SECURITY AUDITOR · 独立对话 · 独立模型 <span class='num'>"
                + mdl + "</span></div></div>"
                "<a href='/devops' style='font-size:12px;'>← 指挥台</a></header><main>"
                "<div id='log'><div class='dim'>开发侧你好。贴代码让我审，或点「审计最近提交」。"
                "我只对安全负责：注入/越权/密钥泄露/危险调用/逻辑后门……有问题直说，没问题也直说。</div></div>"
                "<div style='margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;'>"
                "<button onclick='auditCommit()' class='pri'>🔍 审计最近提交</button>"
                "<button onclick='document.getElementById(\"log\").innerHTML=log0;"
                "hist=[]'>🧹 清空会话</button></div>"
                "<div style='margin-top:10px;'>"
                "<textarea id='inp' placeholder='贴代码或描述要审的实现……'></textarea></div>"
                "<div style='margin-top:8px;display:flex;gap:8px;justify-content:flex-end;'>"
                "<button class='pri' onclick='send()'>发送 →</button></div>"
                "<div class='dim' id='st' style='font-size:11px;margin-top:8px;'>就绪</div>"
                "</main><script>"
                "const log0=document.getElementById('log').innerHTML;let hist=[];"
                "function esc(s){return String(s??'').replace(/[&<>]/g,"
                "c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}"
                "function push(cls,who,txt){const d=document.createElement('div');"
                "d.className=cls;d.innerHTML='<b>'+who+'</b><br>'+txt;"
                "const lg=document.getElementById('log');lg.appendChild(d);"
                "lg.scrollTop=lg.scrollHeight;return d;}"
                "async function call(payload,tag){"
                "const st=document.getElementById('st');st.textContent='审计官思考中…';"
                "try{const r=await fetch('/api/audit_chat',{method:'POST',"
                "headers:{'Content-Type':'application/json'},"
                "body:JSON.stringify(Object.assign({messages:hist},payload))});"
                "const d=await r.json();if(!d.ok)throw new Error(d.error||'HTTP '+r.status);"
                "hist.push({role:'assistant',content:d.reply});"
                "push('a','🛡 审计官 <span class=dim>('+tag+(d.backend?' · '+d.backend:'')+')</span>',"
                "esc(d.reply).replace(/\\n/g,'<br>'));"
                "}catch(e){push('a','🛡 审计官','<span style=\\'color:var(--color-err)\\'>'+"
                "esc(String(e))+'</span>');}"
                "st.textContent='就绪';}"
                "function send(){const i=document.getElementById('inp');"
                "const v=i.value.trim();if(!v)return;i.value='';"
                "hist.push({role:'user',content:v});"
                "push('u','👤 开发侧',esc(v).replace(/\\n/g,'<br>'));call({},'对话');}"
                "async function auditCommit(){push('u','👤 开发侧','🔍 请求审计最近一次提交');"
                "await call({preset:'last_commit'},'最近提交');}"
                "document.getElementById('inp').addEventListener('keydown',"
                "e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey))send();});"
                "</script></body></html>")
        self._html(html)

    def _portal_page(self):
        """🏛 门户框架页：全站分区卡片总览——第一屏「我的入口」
        （Hermes 超级助手 + A0 参谋长），下接 AI 市场/选股中心/行情
        工作台/情报线/实盘跟踪/创作与运维，每卡带一句话说明。"""
        import html as _h
        import urllib.request as _u
        e = _h.escape

        # 轻量统计（缺省安全降级）
        rps = _read_json(os.path.join(REPORTS_DIR, "rps_latest.json")) or {}
        res = rps.get("res", [])
        qd = _read_json(os.path.join(ROOT, "qd_scan_cache.json")) or {}
        # Hermes 在线探活（1.5s 超时）+ 30s TTL 缓存（K-STOCK-101：
        # 原 /portal 每请求同步探活白等最多 1.5s，缓存后 30s 一探）
        global _HERMES_UP_CACHE
        now = time.time()
        if now - _HERMES_UP_CACHE[0] > 30:
            hermes_up = False
            try:
                _op = _u.build_opener(_u.ProxyHandler({}))
                _op.open("http://127.0.0.1:8787/", timeout=1.5)
                hermes_up = True
            except Exception:
                pass
            _HERMES_UP_CACHE = (now, hermes_up)
        else:
            hermes_up = _HERMES_UP_CACHE[1]

        def _card(href, border, icon, name, desc, stat, ext=False):
            tgt = " target='_blank'" if ext else ""
            return ("<a href='%s'%s style='display:block;border:1px "
                    "solid %s;border-radius:12px;background:#161b22;"
                    "padding:15px 17px;text-decoration:none;"
                    "color:#e6edf3'>"
                    "<div style='font-size:16px;font-weight:700'>%s %s"
                    "<span style='float:right;color:%s;font-size:12px'>"
                    "进入 →</span></div>"
                    "<div class='dim' style='font-size:12.5px;"
                    "margin:6px 0 8px'>%s</div>"
                    "<div style='font-size:13px'><b>%s</b></div></a>"
                    % (href, tgt, border, icon, name, border, desc, stat))

        def _zone(title, cards):
            return ("<h2 style='margin:20px 0 10px;font-size:17px'>%s</h2>"
                    "<div style='display:grid;grid-template-columns:repeat("
                    "auto-fit,minmax(270px,1fr));gap:12px'>%s</div>"
                    % (title, cards))

        st = ("● 在线" if hermes_up else "● 离线",
              "#3fb950" if hermes_up else "#f85149")
        hero = (
            "<div style='display:grid;grid-template-columns:repeat("
            "auto-fit,minmax(320px,1fr));gap:14px;margin-top:14px'>"
            "<a href='http://127.0.0.1:8787' target='_blank' style='display:"
            "block;border:2px solid #58a6ff;border-radius:14px;background:"
            "#161b22;padding:20px 22px;text-decoration:none;color:#e6edf3'>"
            "<div style='font-size:20px;font-weight:700'>🦸 Hermes 超级助手"
            "<span style='float:right;color:%s;font-size:13px'>%s</span>"
            "</div><div class='dim' style='font-size:13px;margin:8px 0'>"
            "<b style='color:#e6edf3'>我的主入口</b> · 对话即调度：通过它"
            "指挥 A0 与全部 AI 岗位 · 执行/记忆/插件网关</div>"
            "<div style='font-size:14px'><b>http://127.0.0.1:8787</b>"
            " <span class='dim'>← 点卡片直接进入对话</span></div></a>"
            "<a href='/fin' style='display:block;border:2px solid #d29922;"
            "border-radius:14px;background:#161b22;padding:20px 22px;"
            "text-decoration:none;color:#e6edf3'>"
            "<div style='font-size:20px;font-weight:700'>🧠 A0 参谋长"
            "<span style='float:right;color:#3fb950;font-size:13px'>"
            "● 每日 17:35 值班</span></div><div class='dim' style='font-size:"
            "13px;margin:8px 0'>AI 组织架构顶点 · 影子日报点评 · 任务派单"
            " · 汇报直达你（human）</div><div style='font-size:14px'>"
            "<b>驾驶舱 /fin</b> · 汇报三通道：日报/P0 即时/P1 一小时</div>"
            "</a></div>"
            % (st[1], st[0]))
        zones = "".join([
            _zone("🛒 AI 市场 · 算法商品 + 聘请 AI 做商业化", "".join([
                _card("/algo", "#3fb950", "🛒", "算法商品市场",
                      "六大核心算法商品化：量子双刀/RiskGuard/缠论九维/"
                      "JEV 情绪/TDX 回测/Score 排序 · 每款可试看真实"
                      "输出样例 · 卡密订阅本机解锁",
                      "%d 款商品 · 已解锁 %d" % (
                          len((_read_json(os.path.join(
                              ROOT, "algo_market.json")) or {})
                              .get("algos", [])),
                          sum(1 for _a in (_read_json(os.path.join(
                              ROOT, "algo_market.json")) or {})
                              .get("algos", [])
                              if self._license_state(_a.get("id", ""))))),
                _card("/aim", "#d29922", "🤖", "AI 市场货架",
                      "11 个可聘岗位：A0 + 投研部五岗 + 专项 AI · 本地模型"
                      "零 token 成本", "在岗 11 · 按岗聘请"),
                _card("/org", "#8b949e", "🏛", "组织架构",
                      "A0 参谋长 → 投研部 alpha 团队 → 汇报 human · "
                      "树形架构图 · 对话统一走超级助手", "org.json 实时"),
            ])),
            _zone("🎯 选股中心 · 三产线", "".join([
                _card("/bt-qpzm", "#58a6ff", "🌊", "青萍之末",
                      "四市场自选池四维共振 · 环境闸门一票否决 · "
                      "主页驾驶舱+五子页联动",
                      "全池 %d 只 · 买入 %d" % (
                          len(res),
                          sum(1 for r in res if "买入" in str(
                              r.get("action", ""))))),
                _card("/bt-chanlun", "#e3b341", "🧭", "缠论",
                      "多级别结构信号 · 三类买卖点 · 预警池盯市",
                      "每日盘后扫描"),
                _card("/qd/", "#3fb950", "⚔", "量子双刀",
                      "尾盘策略 14:40 扫描 → 14:55 执行 · 八重过滤+"
                      "Regime（研究态）",
                      "当日 %d · 近5日 %d" % (
                          len(qd.get("signals_now", [])),
                          len(qd.get("signals_recent", [])))),
                _card("/bt", "#8b949e", "🗂", "产线总目录",
                      "三产线汇总入口 · 策略中心",
                      "枢纽页"),
            ])),
            _zone("📈 行情工作台", "".join([
                _card("/screen", "#58a6ff", "📺", "数据大屏",
                      "全市场实时行情总览", "每日 09:30 起"),
                _card("/premarket", "#f85149", "🌅", "盘前快报",
                      "开盘前资讯与准备", "每日早间"),
                _card("/afterhours", "#d29922", "🌇", "盘后复盘",
                      "收盘后复盘与对照", "每日盘后"),
                _card("/sa", "#3fb950", "🔍", "个股 AI 分析",
                      "任一标的 AI 展望 · 四市场", "点入即用"),
                _card("/nl", "#8b949e", "💬", "查数",
                      "自然语言查行情数据", "点入即用"),
            ])),
            _zone("📡 情报线 · 图谱工具群", "".join([
                _card("/intel", "#58a6ff", "🏠", "情报工场",
                      "情报线总入口", "枢纽页"),
                _card("/kg", "#3fb950", "🕸", "图谱探索",
                      "游资/个股/板块知识图谱", "点入即用"),
                _card("/kg/map", "#d29922", "💰", "游资地图",
                      "游资席位踪迹与风格", "点入即用"),
                _card("/kg/ask", "#e3b341", "💬", "图谱问答",
                      "自然语言查图谱", "点入即用"),
                _card("/bt-brief", "#8b949e", "🤖", "AI 晨报",
                      "工作日 09:00 飞书推送", "订阅即享"),
            ])),
            _zone("🚦 实盘跟踪", "".join([
                _card("/bt-signals", "#f85149", "🚦", "明日信号",
                      "策略明日待执行信号", "每日刷新"),
                _card("/bt-positions", "#58a6ff", "💼", "持仓跟踪",
                      "纸面持仓盯市", "每日刷新"),
                _card("/paper", "#3fb950", "🧾", "模拟仓",
                      "模拟盘前端（K279 上线）", "实时"),
                _card("/bt-report", "#d29922", "📄", "回测报告",
                      "策略历史回测对照", "按次生成"),
                _card("/sellpoint", "#e3b341", "🎯", "卖点研判",
                      "缠论卖点 + AI 研判卡", "每日刷新"),
            ])),
            _zone("🎬 创作与 🎛 运维", "".join([
                _card("/creator", "#8b949e", "🎬", "创作专项",
                      "公众号草稿 / 短视频成片", "工作台"),
                _card("/ops", "#8b949e", "🎛", "OS 控制台",
                      "系统运维面板", "运维"),
                _card("/sys-health", "#3fb950", "🖥", "系统自检",
                      "服务探活与夜间快照", "实时"),
            ])),
        ])
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>🏛 总入口 · 门户</title>"
                + self._COCKPIT_CSS + "</head><body>"
                + pill_nav("portal")
                + "<h1>🏛 总入口 · 我的门户</h1><div class='dim'>"
                "哪里是什么 · 我的入口在哪里 · 一页说清全站</div>"
                + "<h2 style='margin:18px 0 0;font-size:17px'>🚪 我的入口"
                "</h2>" + hero + zones
                + "<div class='dim' style='margin-top:20px'>门户即框架："
                "新产线/新 AI 上架改对应注册表或本页卡片即可 · "
                "8080 旧首页（QuantDinger SPA）保留互不影响</div>"
                "</body></html>")
        self._html(body)

    # ── K-STOCK-033 AI 市场：聘请 AI 做商业化（货架页） ──
    def _aim_page(self):
        """🤖 AI 市场：可聘 AI 岗位货架。数据源 portal/ai_market.json
        （注册表存源，本页只读渲染）；卡样式与 /bt 总目录同源。"""
        import html as _h
        e = _h.escape
        mkt = _read_json(os.path.join(ROOT, "ai_market.json")) or {}
        groups = mkt.get("groups", [])
        # K-STOCK-122 岗位→算法互链：algo id → 商品卡（icon/name），
        # 岗位卡渲染「所用算法」芯片，点击跳 /algo 锚点定位
        algo_mkt = _read_json(os.path.join(ROOT, "algo_market.json")) or {}
        amap = {a.get("id", ""): a
                for a in algo_mkt.get("algos", []) if a.get("id")}

        def _ai_card(a):
            badge = a.get("badge", "可聘")
            bc = a.get("badge_color", "#58a6ff")
            algo_chips = "".join(
                "<a class='tp' href='/algo?id=%s' style='text-decoration:"
                "none;font-size:11.5px'>%s %s</a>"
                % (e(i), e(amap.get(i, {}).get("icon", "🧩")),
                   e(amap.get(i, {}).get("name", i)))
                for i in a.get("algos", []) or [] if i in amap)
            algo_row = ("<div style='display:flex;gap:6px;flex-wrap:wrap;"
                        "margin-top:8px'>%s</div>" % algo_chips
                        if algo_chips else "")
            return (
                "<div style='border:1px solid #30363d;border-radius:12px;"
                "background:#161b22;padding:16px 18px'>"
                "<div style='display:flex;align-items:baseline;gap:8px'>"
                "<span style='font-size:17px;font-weight:700'>%s %s</span>"
                "<span style='margin-left:auto;font-size:11.5px;color:%s;"
                "border:1px solid %s;border-radius:12px;padding:2px 10px'>%s"
                "</span></div>"
                "<div style='font-size:13px;margin:8px 0 4px'>%s</div>"
                "<div class='dim' style='font-size:12px;margin-bottom:6px'>%s"
                "</div>"
                "<div class='dim' style='font-size:11.5px'>⚙ %s</div>"
                "<div style='font-size:12.5px;margin:10px 0;display:flex;"
                "gap:6px;flex-wrap:wrap'>"
                "<span class='dim'>📌 %s</span><span class='dim'>·</span>"
                "<span class='dim'>🤝 %s</span></div>"
                "%s"
                "<a class='tp' href='%s'>聘请 →</a></div>"
                % (e(a.get("icon", "🤖")), e(a.get("name", "")), bc, bc,
                   e(badge), e(a.get("role", "")),
                   e(a.get("skills", "")), e(a.get("model", "")),
                   e(a.get("status", "")), e(a.get("hire", "")),
                   algo_row, e(a.get("entry", "/"))))
        secs = "".join(
            "<div class='sec'><h2>%s</h2>"
            "<div style='display:grid;grid-template-columns:repeat("
            "auto-fit,minmax(280px,1fr));gap:12px'>%s</div></div>"
            % (e(g.get("name", "")),
               "".join(_ai_card(a) for a in g.get("ais", [])))
            for g in groups)

        # K-STOCK-034 超级助手结合：Hermes（8787）= AI 组织架构总入口，
        # 顶部枢纽卡 + org.json 实时组织架构条
        org = _read_json(os.path.join(
            STATION, "agent_platform", "registry", "org.json")) or {}
        dept_chips = "".join(
            "<span class='tp' style='cursor:default'>%s：%s</span>"
            % (e(d.get("name", d.get("dept", ""))),
               e("、".join(d.get("agents", [])) or "扩编中"))
            for d in org.get("departments", []))
        chief = org.get("chief", {})
        hub_card = (
            "<div class='sec'><h2>🦸 超级助手 · AI 组织架构总入口</h2>"
            "<div style='display:grid;grid-template-columns:repeat("
            "auto-fit,minmax(300px,1fr));gap:12px'>"
            "<a href='http://127.0.0.1:8787' target='_blank' style='display:"
            "block;border:1px solid #58a6ff;border-radius:12px;"
            "background:#161b22;padding:16px 18px;text-decoration:none;"
            "color:#e6edf3'>"
            "<div style='font-size:17px;font-weight:700'>🦸 Hermes 超级助手"
            "<span style='float:right;color:#3fb950;font-size:12px'>● 在线"
            " · 进入对话 →</span></div>"
            "<div class='dim' style='font-size:12.5px;margin:6px 0 10px'>"
            "与超级助手对话即可调度下方全部 AI 岗位 · 执行/记忆/插件网关"
            "</div>"
            "<div style='font-size:13px'><b>http://127.0.0.1:8787</b></div>"
            "</a>"
            "<div style='border:1px solid #30363d;border-radius:12px;"
            "background:#161b22;padding:16px 18px'>"
            "<div style='font-size:15px;font-weight:700'>🏛 组织架构"
            "<span class='dim' style='font-size:11.5px;margin-left:8px'>"
            "汇报对象：人（%s）</span></div>"
            "<div style='font-size:13px;margin:8px 0'>🧠 A0 参谋长"
            "<span class='dim'>（%s · %s）</span></div>"
            "<div style='display:flex;gap:6px;flex-wrap:wrap'>%s</div>"
            "<div class='dim' style='font-size:11.5px;margin-top:8px'>"
            "📡 汇报三通道：日报 17:35 · P0 即时 · P1 一小时内</div>"
            "</div></div></div>"
            % (e(str(org.get("report_to", "human"))),
               e(str(chief.get("role_profile", "chief_of_staff"))),
               e(str(chief.get("llm_binding", ""))), dept_chips))
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>🤖 AI 市场</title>"
                + self._COCKPIT_CSS + "</head><body>"
                + pill_nav("aim")
                + "<h1>🤖 AI 市场</h1><div class='dim'>"
                + e(mkt.get("tagline", ""))
                + " · 聘请 AI 岗位投入商业化项目 · 本地模型驱动成本可控</div>"
                + hub_card + secs
                + "<div class='dim' style='margin-top:20px'>聘请方式："
                "「在岗」岗位向 A0 参谋长提任务单（飞书日报通道）· "
                "「即用」专项 AI 点入即用 · 货架源 portal/ai_market.json"
                "（上新改注册表即可）· 不构成投资建议</div></body></html>")
        self._html(body)

    # ── K-STOCK-087 算法商品市场（/algo）：试看结果 + 本地卡密订阅解锁 ──
    def _license_state(self, algo_id):
        """订阅状态判定：activated_keys 中任一有效卡（未到期）覆盖
        algo_id → True。卡密库 portal/licenses.json 存源。"""
        import time as _t
        lic = _read_json(os.path.join(ROOT, "licenses.json")) or {}
        act = set(lic.get("activated_keys", []))
        today = _t.strftime("%Y-%m-%d")
        for rec in lic.get("licenses", []):
            if rec.get("key") not in act:
                continue
            if str(rec.get("expiry", "")) < today:
                continue
            cov = rec.get("algos", "all")
            if cov == "all" or algo_id in cov:
                return True
        return False



    # ── K-STOCK-037 组织架构图页（/org）：树形岗位图 · 对话统一走超级助手 ──
    def _org_page(self):
        """🏛 组织架构：human → A0 参谋长 → 部门 → 岗位 树形图。
        数据源 agent_platform/registry/{org,team_*}.json 与
        portal/ai_market.json（注册表存源，本页只读渲染）。
        岗位不设独立对话窗口——对话统一走 🦸 超级助手
        （全站右下角浮动图标 / http://127.0.0.1:8787）。"""
        import html as _h
        e = _h.escape
        org = _read_json(os.path.join(
            STATION, "agent_platform", "registry", "org.json")) or {}
        mkt = _read_json(os.path.join(ROOT, "ai_market.json")) or {}
        face = {}
        for _g in mkt.get("groups", []):
            for _a in _g.get("ais", []):
                face[_a.get("id", "")] = _a
        team_map = {}
        for _d in org.get("departments", []):
            _dk = _d.get("dept", "")
            _tj = {}
            for _cand in [_dk] + list(_d.get("teams") or []):
                _tj = _read_json(os.path.join(
                    STATION, "agent_platform", "registry",
                    "team_%s.json" % _cand)) or {}
                if _tj:
                    break
            if _tj:
                team_map[_dk] = _tj

        def _agent_card(ag):
            fid = face.get(ag.get("agent_id", ""), {}) or {}
            icon = fid.get("icon", "🤖")
            name = fid.get("name") or ag.get("agent_id", "?")
            tags = []
            if ag.get("is_debate_node"):
                tags.append("↔ 辩论 %d 轮" % (ag.get("max_debate_rounds") or 0))
            if ag.get("level") == 2:
                tags.append("⚖ L2 汇总")
            tag_html = "".join(
                "<span style='margin-left:auto;font-size:10.5px;"
                "color:#d29922;border:1px solid #d29922;border-radius:10px;"
                "padding:1px 8px'>%s</span>" % e(t) for t in tags)
            tools = "、".join(ag.get("tools") or []) or "—"
            up = "、".join(ag.get("parent_node_keys") or [])
            up_html = ("<div class='dim' style='font-size:11.5px;"
                       "margin-top:6px'>↑ 上游：%s</div>" % e(up) if up else "")
            return (
                "<div style='border:1px solid #30363d;border-radius:12px;"
                "background:#161b22;padding:14px 16px'>"
                "<div style='display:flex;align-items:baseline;gap:8px'>"
                "<span style='font-size:16px;font-weight:700'>%s %s</span>%s"
                "</div>"
                "<div style='font-size:12.5px;margin:6px 0 2px'>%s</div>"
                "<div class='dim' style='font-size:11.5px'>%s · %s · "
                "🛠 %s</div>%s</div>"
                % (e(icon), e(name), tag_html,
                   e(fid.get("role") or ag.get("role_profile", "")),
                   e(str(ag.get("role_profile", ""))),
                   e(str(ag.get("llm_binding", ""))), e(tools), up_html))

        def _dept_block(d):
            dept = d.get("dept", "")
            planned = d.get("status") == "planned"
            tj = team_map.get(dept) or {}
            cards = "".join(_agent_card(a) for a in tj.get("agents", []))
            if planned:
                inner = ("<div class='dim' style='font-size:12.5px;"
                         "padding:8px 0'>🚧 扩编中（K-ORG-001 P2 预留："
                         "拉代码·优化代码·训练模型，待投研部影子验收后"
                         "启动）</div>")
            elif cards:
                inner = ("<div style='display:grid;grid-template-columns:"
                         "repeat(auto-fit,minmax(250px,1fr));gap:10px'>"
                         + cards + "</div>")
            else:
                inner = "<div class='dim'>暂无编制数据</div>"
            team_line = ""
            if tj:
                team_line = ("<span class='dim' style='font-size:11.5px;"
                             "margin-left:8px'>团队 %s</span>"
                             % e(str(tj.get("team", ""))))
            return ("<div style='margin:10px 0 0 26px;border-left:2px solid "
                    "#30363d;padding-left:16px'>"
                    "<div style='font-size:15px;font-weight:700'>📁 %s%s"
                    "</div>"
                    "<div class='dim' style='font-size:12px;margin:2px 0 8px'>"
                    "%s</div>%s</div>"
                    % (e(str(d.get("name", dept))), team_line,
                       e(str(d.get("charter", ""))), inner))

        chief = org.get("chief", {})
        rep = org.get("reporting", {})
        sev = rep.get("severity", {})
        ch_status = ("骨架已立（9b 判读验证通过）"
                     if chief.get("status") == "skeleton"
                     else str(chief.get("status", "")))

        def _arrow(label):
            return ("<div class='dim' style='margin:4px 0 4px 26px;"
                    "font-size:12px'>↓ %s</div>" % e(label))

        dept_html = "".join(_dept_block(d)
                            for d in org.get("departments", []))
        tree = (
            "<div class='sec'>"
            "<div style='border:2px solid #8b949e;border-radius:12px;"
            "background:#161b22;padding:12px 16px;display:inline-block'>"
            "👤 %s（老板 · 汇报对象）</div>"
            % e(str(org.get("report_to", "human")))
            + _arrow("汇报三通道：📡 日报 %s · 🔴 P0 %s · 🟡 P1 %s"
                     % (rep.get("daily_push", "17:35"),
                        sev.get("P0", "即时"), sev.get("P1", "1h")))
            + "<div style='border:2px solid #d29922;border-radius:12px;"
              "background:#161b22;padding:12px 16px;display:inline-block'>"
              "<b style='font-size:16px'>🧠 A0 参谋长</b>"
              "<span class='dim' style='font-size:12px;margin-left:8px'>"
              "%s · %s · %s</span></div>"
              % (e(str(chief.get("role_profile", ""))),
                 e(str(chief.get("llm_binding", ""))), e(ch_status))
            + _arrow("直辖部门 %d 个" % len(org.get("departments", [])))
            + dept_html + "</div>")
        banner = (
            "<div class='sec' style='border-color:#58a6ff'>"
            "🦸 <b>对话统一入口</b>：各岗位均<b>不设独立对话窗口</b>——"
            "通过超级助手直接对话即可调度全部岗位（全站右下角浮动图标，"
            "或 <a href='http://127.0.0.1:8787' target='_blank' "
            "rel='noopener'>127.0.0.1:8787</a>）· 聘请/派单走 "
            "<a href='/aim'>AI 市场货架</a></div>")
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>🏛 组织架构</title>"
                + self._COCKPIT_CSS + "</head><body>"
                + pill_nav("org")
                + "<h1>🏛 组织架构</h1><div class='dim'>"
                "human → A0 参谋长 → 部门 → 岗位 · 注册表实时渲染"
                "（agent_platform/registry）</div>"
                + banner + tree
                + "<div class='dim' style='margin-top:20px'>数据源："
                "org.json（组织顶点）· team_invest.json（alpha 执行拓扑）· "
                "ai_market.json（岗位名/图标，与 AI 市场货架同源）· "
                "上新岗位改注册表即可，页面零改</div></body></html>")
        self._html(body)

    # ── K238 每日选股批次回测（挂在自选池共振信号后面） ──
    def _qpzm_batch_blk(self, snap):
        import html as _h
        e = _h.escape
        rep = _read_json(os.path.join(REPORTS_DIR,
                                      "rps_batch_backtest.json")) or {}
        batches = rep.get("batches") or []
        meta = rep.get("meta") or {}

        def _pc(x, bold=False):
            if x is None:
                return "<span class='dim'>—</span>"
            c = "#f85149" if x >= 0 else "#3fb950"
            s = "%+.2f%%" % (x * 100)
            return ("<b style='color:%s'>%s</b>" % (c, s) if bold
                    else "<span style='color:%s'>%s</span>" % (c, s))

        def _hrow(h, n):
            if not h:
                return ("<td class='dim'>—</td><td class='dim'>—</td>"
                        "<td class='dim'>—</td>")
            lab = "%d日%s" % (n, "（至今）" if h.get("partial") else "")
            return ("<td>%s<div class='dim' style='font-size:11px'>%s · "
                    "成交 %d</div></td><td>%s</td><td>%s</td>"
                    % (e(lab), "胜率 " + (("%.1f%%" % (h["win_rate"] * 100))
                                          if h.get("win_rate") is not None
                                          else "—"),
                       h.get("n_traded") or 0,
                       _pc(h.get("mean"), bold=True),
                       _pc(h.get("excess"))))

        # ── K251 逐日盯表明细（每天的收益全部展开，不打包） ──
        def _daily_tbl(dl):
            if not dl or not (dl.get("k")):
                return "<div class='dim'>无逐日数据</div>"
            ks = dl.get("k") or []
            dts = dl.get("date") or []
            arr_day = dl.get("day") or []
            arr_cum = dl.get("cum") or []
            arr_ex = dl.get("excess") or []
            tr = ""
            for idx, k in enumerate(ks):
                d8 = str(dts[idx]) if idx < len(dts) else ""
                d10 = ("%s-%s-%s" % (d8[:4], d8[4:6], d8[6:8])
                       if len(d8) == 8 else d8)
                tr += ("<tr><td class='dim'>D%d</td><td class='dim'>%s</td>"
                       "<td>%s</td><td>%s</td><td>%s</td></tr>"
                       % (k, e(d10),
                          _pc(arr_day[idx] if idx < len(arr_day) else None),
                          _pc(arr_cum[idx] if idx < len(arr_cum) else None,
                              bold=True),
                          _pc(arr_ex[idx] if idx < len(arr_ex) else None)))
            lab_cum = "累计净%s" % ("（至今）" if dl.get("partial") else "")
            return ("<table><tr><th>持有</th><th>日期</th><th>当日</th>"
                    "<th>%s</th><th>超额</th></tr>%s</table>"
                    % (lab_cum, tr))

        if not batches:
            body = ("<div class='dim'>暂无批次回测数据（每日 17:35 跑批后"
                    "自动生成，或手动执行 qpzm_daily_batch.py --all）</div>")
        else:
            # ① 当日批次卡（batches 倒序首行 = 最新批次）
            cur = batches[0]
            note = {"empty": "⛔ 环境闸门收紧，当日 CN 无买入/观察批次"
                             "（回避票不计入批次回测）",
                    "await_t1": "⏳ 信号日为数据尾，待 T+1 开盘进场",
                    "no_bar": "该日无行情数据"}.get(cur.get("note"))
            src_chip = ("<span style='font-size:11px;border:1px solid %s;"
                        "color:%s;border-radius:10px;padding:1px 8px;"
                        "margin-left:8px'>%s</span>"
                        % (("#58a6ff", "#58a6ff", "产线口径")
                           if cur.get("source") == "live"
                           else ("#8b949e", "#8b949e", "复算口径·无JEV")))
            if note:
                card = ("<div style='margin-bottom:6px'><b>%s</b> 批次：%s%s"
                        "</div>" % (e(str(cur.get("date"))), note,
                                    src_chip))
            else:
                card = ("<div style='margin-bottom:6px'><b>%s</b> 批次 · "
                        "<span style='color:#f85149'>买入 %d</span> · "
                        "<span style='color:#d29922'>观察 %d</span>%s</div>"
                        "<table><tr><th>持有</th><th>批次均值</th><th>超额"
                        "</th></tr>%s</table>"
                        % (e(str(cur.get("date"))), cur.get("n_buy") or 0,
                           cur.get("n_obs") or 0, src_chip,
                           "".join("<tr>%s</tr>" % _hrow(
                               cur.get("holds", {}).get(str(n)), n)
                               for n in (5, 10, 20))))
                # K251 逐日盯市（当日批次全程展开）
                dl = cur.get("daily")
                if dl:
                    card += ("<div class='dim' style='margin:10px 0 3px'>"
                             "📅 逐日盯市 · T+1 开盘进场起 · 成交 %d 票%s"
                             "</div>%s"
                             % (dl.get("n_traded") or 0,
                                "（至今）" if dl.get("partial") else "",
                                _daily_tbl(dl)))
            # ② 历史批次表（日期倒序，最近 30 行；每行可展开逐日明细）
            rows = ""
            for b in batches[:30]:
                hs = b.get("holds") or {}
                rows += (
                    "<tr><td><b>%s</b><div class='dim' style='font-size:11px'>"
                    "%s</div></td><td class='dim'>%d / %d</td><td>%s</td>"
                    "<td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                    "</tr>"
                    % (e(str(b.get("date"))),
                       "产线" if b.get("source") == "live" else "复算",
                       b.get("n_buy") or 0, b.get("n_obs") or 0,
                       _pc((hs.get("5") or {}).get("mean")),
                       _pc((hs.get("5") or {}).get("excess")),
                       _pc((hs.get("10") or {}).get("mean")),
                       _pc((hs.get("10") or {}).get("excess")),
                       _pc((hs.get("20") or {}).get("mean")),
                       _pc((hs.get("20") or {}).get("excess"))))
                # K251 逐日明细折叠行（点开即展开每天的收益）
                dl = b.get("daily")
                if dl:
                    rows += ("<tr><td colspan='8' style='padding:0 4px 4px'>"
                             "<details><summary class='dim' style='cursor:"
                             "pointer;font-size:12px'>📅 逐日明细</summary>"
                             "%s</details></td></tr>" % _daily_tbl(dl))
            tbl = ("<table><tr><th>批次日</th><th>买/观</th><th>5日均</th>"
                   "<th>5日超额</th><th>10日均</th><th>10日超额</th>"
                   "<th>20日均</th><th>20日超额</th></tr>%s</table>"
                   % (rows or "<tr><td colspan='8' class='dim'>暂无历史批次"
                      "</td></tr>"))
            body = (card + tbl
                    + "<div class='dim' style='margin-top:6px'>口径：%s · "
                    "共 %d 个批次 · 生成于 %s</div>"
                    % (e(str(meta.get("design", ""))),
                       meta.get("n_batches") or len(batches),
                       e(str(meta.get("generated_at", "—")))))
        return ("<div class='sec'><h2>📊 每日批次回测</h2>" + body + "</div>")














    # ── K35 三线联动复盘（大盘-板块-个股，影子观察）──
    def _linkage_page(self, qs):
        """🔗 三线联动：个股 × TDX 880xxx 板块 × 大盘指数，三周期归一化叠加。

        数据与 quant_backtest/linkage.py 完全同源；PNG 缺失或 ?refresh=1
        时同步重算（tushare 分钟远程拉取，约 20-60s）；?tail=1 先补
        tdx_sector_min 分钟尾巴再重算（全局动作，每日一次足够）。"""
        from urllib.parse import parse_qs as _pq
        import re as _re
        import sys as _sys
        qp = _pq(qs)
        code = (qp.get("code") or ["301216.SZ"])[0].strip()[:12]
        if not _re.fullmatch(r"[0-9A-Za-z._/]+", code):
            code = "301216.SZ"
        block = (qp.get("block") or [""])[0].strip()[:8] or None
        block_name = (qp.get("block_name") or [""])[0].strip()[:12] or None
        index = (qp.get("index") or [""])[0].strip()[:12] or None
        refresh = (qp.get("refresh") or [""])[0] == "1"
        tail = (qp.get("tail") or [""])[0] == "1"
        if QBT_DIR not in _sys.path:
            _sys.path.insert(0, QBT_DIR)
        err, png_dir = "", ""
        try:
            import linkage_plot as _lp
            png_dir = _lp.OUT_DIR
            if tail:
                _lp.LK.refresh_min_tail()
                refresh = True
            if refresh or any(not os.path.exists(os.path.join(
                    png_dir, "%s_%s.png" % (code, p)))
                    for p in _lp.LK.PERIODS):
                _lp.gen_all(code, block=block, block_name=block_name,
                            index=index)
        except Exception as ex:
            err = str(ex)[:160]
        import html as _h
        e = _h.escape
        rows, base_q = [], {"code": code}
        if block:
            base_q["block"] = block
        if block_name:
            base_q["block_name"] = block_name
        if index:
            base_q["index"] = index
        from urllib.parse import urlencode as _ue
        bqe = e(_ue(base_q))
        pt = {"1d": "日线", "30m": "30分钟", "5m": "5分钟"}
        for p in ("1d", "30m", "5m"):
            png = os.path.join(png_dir, "%s_%s.png" % (code, p)) \
                if png_dir else ""
            sc = {}
            if png and os.path.exists(png):
                try:
                    with open(os.path.splitext(png)[0] + ".json",
                              encoding="utf-8") as f:
                        sc = json.load(f)
                except Exception:
                    sc = {}
                v = int(os.path.getmtime(png))
                lp = sc.get("last_pct", {})
                blk = (sc.get("meta", {}).get("block") or {})
                sigs = sc.get("signals") or []
                if sigs:
                    sig_html = "<div style='margin:4px 0 2px'>五信号：" + \
                        " ".join(
                            "<span style='background:#3d2e00;color:#e3a008;"
                            "padding:2px 8px;border-radius:4px;font-size:12px'>"
                            "⚑%s %s</span><span style='color:#8b949e;"
                            "font-size:11px'> %s</span>"
                            % (e(s.get("tag", "?")), e(s.get("name", "")),
                               e(s.get("note", ""))) for s in sigs) + "</div>"
                else:
                    sig_html = ("<div style='color:#586e75;font-size:12px;"
                                "margin:4px 0 2px'>五信号：无</div>")
                info = ("板块 %s %s（%s）｜基准 %s=0%% ｜ 截至: 个股 %s · "
                        "板块 %s · 大盘 %s ｜ 末值: 个股 %s%% · 板块 %s%% · "
                        "大盘 %s%% ｜ 现价 %s" % (
                            blk.get("name", "—"), blk.get("code", "—"),
                            blk.get("how", "—"),
                            sc.get("base_dt", "—"),
                            (sc.get("meta", {}).get("stock") or {}
                             ).get("last", "—"), blk.get("last", "—"),
                            (sc.get("meta", {}).get("index") or {}
                             ).get("last", "—"),
                            lp.get("stock"), lp.get("block"),
                            lp.get("index"), sc.get("stock_last_close")))
                rows.append(
                    "<h2 style='margin:18px 0 6px'>%s（%d 根）</h2>"
                    "<div style='color:#8b949e;font-size:12px;margin-bottom:"
                    "6px'>%s</div>%s<img src='/bt-linkage-png?code=%s&period=%s"
                    "&v=%d' style='max-width:100%%;border:1px solid #30363d;"
                    "border-radius:6px' alt='%s %s'>" % (
                        pt.get(p, p), sc.get("n", 0), e(info), sig_html,
                        e(code), p, v,
                        e(code), pt.get(p, p)))
            else:
                rows.append("<h2 style='margin:18px 0 6px'>%s</h2>"
                            "<div style='color:#f85149'>图未生成%s</div>" % (
                                pt.get(p, p),
                                "（生成失败：%s）" % e(err) if err else ""))
        err_banner = ("<div style='color:#f85149;background:#3d1e20;padding:"
                      "8px 12px;border-radius:6px;margin:8px 0'>生成异常：%s"
                      "</div>" % e(err)) if err else ""
        self._html(
            # 058 补标准页壳（此前裸片段→浏览器白底，与全站割裂）
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,"
            "initial-scale=1'><title>🔗 三线联动 · 零界</title>"
            + self._COCKPIT_CSS + "</head><body>"
            + pill_nav("linkage")
            + "<h1>🔗 三线联动复盘 <span style='font-size:14px;color:#8b949e'>"
            "大盘 × 板块(TDX 880xxx) × 个股 · 影子观察</span></h1>"
            "<form method='get' action='/bt-linkage' style='display:flex;gap:"
            "8px;flex-wrap:wrap;margin:10px 0;align-items:center'>"
            "<input name='code' value='%s' placeholder='代码 如 301216.SZ' "
            "style='width:130px'><input name='block' value='%s' placeholder="
            "'板块码 可选 如 881019'><input name='index' value='%s' "
            "placeholder='指数 默认 000001.SH' style='width:130px'>"
            "<button>查看/生成</button></form>"
            "<div style='font-size:13px;margin:6px 0'>"
            "<a href='/bt-linkage?%s&refresh=1'>♻ 重新生成</a> ｜ "
            "<a href='/bt-linkage?%s&tail=1'>⇩ 补板块分钟尾巴并重算</a>"
            "（生成约 20-60s，页面会等待）</div>%s%s"
            "<div style='color:#8b949e;font-size:12px;margin-top:16px;"
            "line-height:1.7'>口径：个股/大盘=chanlun_data.load_kline（CN "
            "tushare 分钟重采样）；板块=TDX 880xxx（tdx_sector_daily 日线 / "
            "tdx_sector_min 1 分钟重采样）；个股→板块=ts_stock_basic 行业名"
            "匹配（seg 优先 industry881&gt;industry_l1&gt;old_industry，可显"
            "式指定）；对齐=个股时间轴前向填充；归一化=公共首根 0%%。"
            "仅影子复盘，不构成买卖依据。</div></body></html>" % (
                e(code), e(block or ""), e(index or ""), bqe, bqe, err_banner,
                "".join(rows)))

    def _linkage_png(self, qp):
        """联动 PNG 白名单直出（code 白名单字符 + period 三选一）。"""
        import re as _re
        code = (qp.get("code") or [""])[0].strip()[:12]
        period = (qp.get("period") or [""])[0].strip()
        if not _re.fullmatch(r"[0-9A-Za-z._/]+", code) or \
                period not in ("1d", "30m", "5m"):
            self.send_error(404)
            return
        import sys as _sys
        if QBT_DIR not in _sys.path:
            _sys.path.insert(0, QBT_DIR)
        try:
            import linkage_plot as _lp
            fp = os.path.join(_lp.OUT_DIR, "%s_%s.png" % (code, period))
        except Exception:
            self.send_error(404)
            return
        if not os.path.exists(fp):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Cache-Control", "max-age=300")
        self.send_header("Content-Length", str(os.path.getsize(fp)))
        self.end_headers()
        with open(fp, "rb") as f:
            self.wfile.write(f.read())



    # K40 情报工场卡片板 CSS（与 L0 首页 .dm 同语言：图标+定位句+微指标+下钻）
    _INTEL_CARD_CSS = (
        "<style>"
        ".iboard{display:grid;grid-template-columns:"
        "repeat(auto-fill,minmax(300px,1fr));gap:14px;margin:16px 0 22px}"
        ".idm{display:flex;flex-direction:column;gap:8px;background:#161b22;"
        "border:1px solid #30363d;border-radius:14px;padding:18px 20px 15px;"
        "min-height:150px;text-decoration:none;color:#e6edf3;"
        "transition:border-color .2s}"
        "a.idm:hover{border-color:#58a6ff}"
        ".idm .ihead{display:flex;align-items:center;gap:12px}"
        ".idm .iic{width:42px;height:42px;border-radius:11px;"
        "border:1px solid #30363d;background:#0d1117;display:grid;"
        "place-items:center;font-size:21px;flex-shrink:0}"
        ".idm .it{font-size:15.5px;font-weight:700;display:flex;"
        "align-items:center}"
        ".idm .is{font-size:10.5px;color:#8b949e;letter-spacing:.5px;"
        "margin-top:2px}"
        ".idm .idesc{font-size:12px;color:#8b949e;line-height:1.65}"
        ".idm .ims{display:flex;flex-wrap:wrap;gap:5px 13px;"
        "font-size:11.5px;color:#8b949e;font-family:Consolas,"
        "'Microsoft YaHei',monospace}"
        ".idm .ims b{color:#e6edf3;font-weight:600}"
        ".idm .ifoot{display:flex;align-items:center;"
        "justify-content:space-between;margin-top:auto;padding-top:10px;"
        "border-top:1px dashed #21262d}"
        ".idm .ist{display:flex;align-items:center;gap:7px;"
        "font-size:11.5px;color:#8b949e}"
        ".idm .ienter{font-size:12px;color:#58a6ff;font-weight:600}"
        ".itag{font-size:10px;padding:1px 8px;border-radius:999px;"
        "border:1px dashed #30363d;color:#8b949e;font-weight:400;"
        "margin-left:7px}"
        ".idot{width:8px;height:8px;border-radius:50%;background:#3fb950;"
        "display:inline-block;flex-shrink:0}"
        ".idot.off{background:#f85149}"
        ".itools{display:flex;flex-wrap:wrap;gap:5px 12px;"
        "font-size:12.5px;line-height:1.9}"
        ".itools a{color:#58a6ff;text-decoration:none;"
        "border-bottom:1px dotted #30363d}"
        "</style>")

    # ── L1 情报工场 v2（K40 四卡生命周期：收集→证据流→图谱探索→飞书输出） ──
    def _intel_page(self):
        import html as _h
        e = _h.escape
        nw = _news_db()
        ncfg = _read_json(os.path.join(STATION, "news_collector",
                                       "news_config.json"))
        qcfg = _read_json(os.path.join(STATION, "quant_backtest",
                                       "config.json"))
        rep = qcfg.get("report", {})
        cst = _read_json(os.path.join(STATION, "news_collector",
                                      "status.json")) or {}

        # ① 今日情报流统计 + 双向条
        d = nw["dist"]
        tot = (d.get("bull", 0) + d.get("bear", 0) + d.get("neutral", 0)) or 1
        bar = ("<div style='display:flex;height:14px;border-radius:7px;"
               "overflow:hidden;max-width:420px;margin:10px 0 4px'>"
               "<i style='width:%.0f%%;background:#f85149'></i>"
               "<i style='width:%.0f%%;background:#8b949e'></i>"
               "<i style='width:%.0f%%;background:#3fb950'></i></div>"
               "<div class='dim'>利多 %d · 中性 %d · 利空 %d（今日已判 "
               "%d 条）</div>"
               % (d.get("bull", 0) * 100.0 / tot,
                  d.get("neutral", 0) * 100.0 / tot,
                  d.get("bear", 0) * 100.0 / tot,
                  d.get("bull", 0), d.get("neutral", 0), d.get("bear", 0),
                  tot))
        flow = ("<div style='font-size:15px;margin-top:6px'>今日采集 "
                "<b>%d</b> 条 · 关键词命中 <b>%d</b> 条 · 已判 <b>%d</b> 条"
                "</div>%s" % (nw["today"], nw["hit"], tot, bar))

        # ② 四卡板（K40 生命周期：收集→证据流→图谱→飞书输出）
        srcs = cst.get("sources", {}) or {}
        n_ok = sum(1 for v in srcs.values() if v.get("ok"))
        wl = ncfg.get("watchlist", []) or []
        card_collect = (
            "<a class='idm' href='/ops'>"
            "<div class='ihead'><div class='iic'>🛰</div><div>"
            "<div class='it'>开源情报收集</div>"
            "<div class='is'>源 · OSINT 采集与监控</div></div></div>"
            "<div class='idesc'>%d 源新闻采集、%d 标的 watchlist 监控、"
            "来源新鲜度——情报从这里进来。</div>"
            "<div class='ims'><span>24h <b>%s</b></span>"
            "<span>源 <b>%d/%d</b></span><span>监控 <b>%d</b></span></div>"
            "<div class='ifoot'><span class='ist'><i class='idot%s'>"
            "</i>采集于 %s</span><span class='ienter'>采集管道 →</span>"
            "</div></a>"
            % (len(srcs), len(wl), cst.get("news_24h", "—"),
               n_ok, len(srcs), len(wl),
               "" if cst.get("last_ok") else " off",
               e(str(cst.get("last_collect", "—")))))
        card_flow = (
            "<a class='idm' href='/bt-jev'>"
            "<div class='ihead'><div class='iic'>📡</div><div>"
            "<div class='it'>证据流（读）</div>"
            "<div class='is'>今日判断了什么 · 依据是什么</div></div></div>"
            "<div class='idesc'>AI 晨报 + JEV 逐条判断（含置信度），"
            "分析过程的实锤与证据点。</div>"
            "<div class='ims'><span>今日 <b>%d</b></span>"
            "<span>利多 <b>%d</b></span><span>利空 <b>%d</b></span></div>"
            "<div class='ifoot'><span class='ist'><i class='idot'></i>"
            "关键词命中 %d 条</span><span class='ienter'>晨报 · JEV →"
            "</span></div></a>"
            % (nw["today"], d.get("bull", 0), d.get("bear", 0),
               nw["hit"]))
        card_kg = (
            "<div class='idm'>"
            "<div class='ihead'><div class='iic'>🕸</div><div>"
            "<div class='it'>知识图谱<span class='itag'>探索</span>"
            "</div><div class='is'>分析过程的工具 · 看细节的地方"
            "</div></div></div>"
            "<div class='idesc'>知识图谱是探索过程的一件工具，"
            "不是情报的全部——从这里下钻每只股票的基本面 / 技术面 / "
            "信息面，以及公告新闻原文。</div>"
            "<div class='itools'>"
            "<a href='/kg'>图谱探索</a><a href='/kg/map'>游资地图</a>"
            "<a href='/kg/flow'>板块工单</a><a href='/kg/label'>画图标注"
            "</a><a href='/kg/card'>推演卡</a>"
            "<a href='/kg/shadow'>影子面板</a></div>"
            "<div class='ifoot'><span class='ist'>六件套 · 行式下钻"
            "</span><span class='ienter'>逐件进入 ↗</span></div></div>")
        docs = [("📰 金融新闻日报", ncfg.get("doc_url")),
                ("⚖️ JEV 判断记录", ncfg.get("jev_doc_url")),
                ("🌅 盘前分析镜像", rep.get("premarket_doc_url")),
                ("📄 回测报告文档", rep.get("doc_url"))]
        doc_html = "".join(
            "<a class='doc' href='%s' target='_blank'>%s ↗</a>"
            % (e(str(u)), lb) for lb, u in docs if u) or \
            "<div class='dim'>未配置文档 URL</div>"
        # ── K42 预警实时值（与 /api/alerts 同源；cross_poll 60s 缓存）
        try:
            _al_map = {a["name"]: a for a in
                       (build_alerts().get("alerts") or [])}
        except Exception:
            _al_map = {}

        def _av(nm):
            a = _al_map.get(nm)
            return a["value"] if a else "—"

        _lst = _read_json(os.path.join(
            STATION, "news_collector", "outbox", "alert_push_last.json"))
        _push_ms = ("<div class='ims'>"
                    "<span>缠论 <b>%s</b></span><span>九维 <b>%s</b></span>"
                    "<span>weak <b>%s</b></span><span>共振 <b>%s</b></span>"
                    "</div>" % (_av("缠论信号"), _av("九维警报池"),
                                _av("弱于板块在案"), _av("共振做多")))
        if _lst.get("ts"):
            _push_ms += ("<div class='ims'><span>上次推送 <b>%s · %s</b>"
                         " %s</span><span>推了 <b>%s 路</b></span></div>"
                         % (e(_lst["ts"]),
                            "晨" if _lst.get("mode") == "morning" else "盘后",
                            "✅" if _lst.get("ok") else "❌",
                            _lst.get("n", "—")))
        else:
            _push_ms += ("<div class='ims'><span>上次推送 <b>—</b>"
                         "</span></div>")
        card_push = (
            "<div class='idm'>"
            "<div class='ihead'><div class='iic'>📨</div><div>"
            "<div class='it'>飞书预警输出<span class='itag'>独立板块"
            "</span></div><div class='is'>发 · 定时定量往外推送"
            "</div></div></div>"
            "<div class='idesc'>把其他板块的数据抽出来，每天定时定量"
            "往外发送——晨 7:10 / 盘后 16:05 定时 + 每 30 分钟阈值监听"
            "即时推（个人 + 家人群），情报工作流的出口。</div>"
            + _push_ms +
            "<div class='itools'>"
            "<a href='/api/alert/push'>📨 立即推送全量</a>"
            "<a href='/quant'>📊 预警速览 →</a></div>"
            "<div class='ifoot'><span class='ist'>"
            "<i class='idot%s'></i>%s</span><span class='ienter'>"
            "文档枢纽 ↓</span></div>%s</div>"
            % (" off" if not (_lst.get("ts") and _lst.get("ok")) else "",
               "推送通道" if _lst.get("ok") else "未推送",
               doc_html))

        # ③ 简表：今日证据流（最近 8 条 + 多空条）
        rows8 = "".join(
            "<div style='margin:8px 0;font-size:13px'>"
            "<span style='color:%s;font-weight:600'>%s %d%%</span>"
            "<span class='dim'> · %s · %s</span> %s</div>"
            % ("#f85149" if x["bias"] == "bull" else
               "#3fb950" if x["bias"] == "bear" else "#8b949e",
               e(x["bias_cn"]), round((x["conf"] or 0) * 100),
               e(x["scope_cn"]), e(x["ts"]), e(x["title"]))
            for x in nw["recent"][:8]) or \
            "<div class='dim'>今日暂无已判断新闻</div>"
        more = ("<div style='margin-top:6px'><a href='/bt-jev'>"
                "全部 JEV 判断（可下钻新闻依据）→</a></div>")

        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>情报工场</title>"
                + self._COCKPIT_CSS + self._INTEL_CARD_CSS + "</head><body>"
                + pill_nav("intel")
                + "<h1>🕵️ 情报工场</h1><div class='dim'>开源情报从这"
                "进来 · 证据在这沉淀 · 结论从这推走 · 更新于 %s</div>"
                % e(time.strftime("%Y-%m-%d %H:%M"))
                + "<div class='iboard'>%s%s%s%s</div>"
                % (card_collect, card_flow, card_kg, card_push)
                + "<div class='sec'><h2>📡 今日证据流</h2>"
                "<div class='dim' style='margin-bottom:6px'>最近已判断"
                " 8 条 · <a href='/bt-brief'>🤖 AI 晨报</a> · "
                "<a href='/bt-jev'>⚖️ JEV 判断</a></div>"
                + flow + rows8 + more + "</div>"
                + "</body></html>")
        self._html(body)





    @classmethod

    @classmethod

    @staticmethod






    def _analyst_report_page(self, qs):
        """K244 青萍之末分析师报告页：/bt/analyst-report?c=sz000993

        事实聚合（行情/共振/缠论/筹码/板块/公告后验/量能事件/龙虎榜）
        → 本地 Ollama 大白话报告 → 一键推飞书。生成较慢（10~20s），
        前端异步拉 /api/analyst-report?c=，避免页首阻塞。"""
        from urllib.parse import parse_qs, unquote as _uq
        import html as _h
        qp = parse_qs(qs)
        q = _uq((qp.get("c") or [""])[0]).strip()[:24]
        e = _h.escape
        body = [self._COCKPIT_CSS, "</div>" if False else "",
                "<style>.arb{background:#161b22;border:1px solid #30363d;"
                "border-radius:8px;color:#8b949e;font-size:12px;"
                "padding:4px 12px;cursor:pointer;font-family:inherit}"
                ".arb.on{background:#0f141c;color:#e6edf3;font-weight:600;"
                "border-color:#58a6ff}"
                "#ar-md h2{font-size:16px;margin:16px 0 8px;"
                "color:#e6edf3;border-left:3px solid #58a6ff;"
                "padding-left:8px}"
                "#ar-md p{margin:8px 0;line-height:1.9;font-size:14px}"
                "#ar-md li{margin:6px 0;line-height:1.8}"
                ".ar-spin{display:inline-block;width:14px;height:14px;"
                "border:2px solid #30363d;border-top-color:#58a6ff;"
                "border-radius:50%;animation:ars 1s linear infinite;"
                "vertical-align:-2px;margin-right:6px}"
                "@keyframes ars{to{transform:rotate(360deg)}}</style>",
                pill_nav("qpzm")]
        if not q:
            body.append("<h1>🌊 青萍之末分析师报告</h1>"
                        "<div class='dim'>从个股页进入，或直接访问 "
                        "<code>/bt/analyst-report?c=sz000993</code></div>")
            self._html("".join(body))
            return
        body.append(
            "<h1>🌊 青萍之末分析师报告 · <span class='dim'>%s</span></h1>"
            "<div style='margin-bottom:10px'>"
            "<a class='arb' href='/kg/stock/%s'>↩ 回个股页</a> "
            "<button class='arb' onclick='_arGen()'>🔄 重新生成</button> "
            "<button class='arb on' id='ar-push' onclick='_arPush()'>"
            "📨 一键发送飞书</button> "
            "<span class='dim' id='ar-msg' style='margin-left:8px'>"
            "正在聚合事实并生成报告…</span></div>"
            "<div id='ar-md' class='card' style='line-height:1.9;"
            "font-size:14px'><div class='dim'><span class='ar-spin'></span>"
            "青萍之末分析师正在读数据（约 10~20 秒）…</div></div>"
            "<script>var _ARC=%s;function _arMd(t){"
            "var h=0,out='';t.split(/\\n/).forEach(function(ln){"
            "ln=ln.replace(/^\\s+/,'');if(!ln){out+='<p></p>';return}"
            "ln=ln.replace(/^###\\s*/,'').replace(/^##\\s*/,'')"
            ".replace(/^#\\s*/,'');"
            "var em=ln.match(/^([\\u{1F300}-\\u{1FAFF}\\u2600-\\u27BF]+)\\s*/u);"
            "var ic=em?em[1]:'';var tx=em?ln.slice(em[0].length):ln;"
            "tx=tx.replace(/\\*\\*(.+?)\\*\\*/g,'<b>$1</b>')"
            ".replace(/\\*(.+?)\\*/g,'<i>$1</i>')"
            ".replace(/`(.+?)`/g,\"<code>$1</code>\");"
            "var isH=/'^[^\\uff1a:]{2,14}[\\uff1a:]?\\$/.test(ic)||ic;"
            "if(ic){out+='<h2>'+ic+' '+tx+'</h2>'}else{out+='<p>'+tx+'</p>'}"
            "});return out}"
            "function _arLoad(){fetch('/api/analyst-report?c='+"
            "encodeURIComponent(_ARC)).then(function(r){return r.json()})"
            ".then(function(j){document.getElementById('ar-md').innerHTML="
            "j.ok?_arMd(j.md):('<div class=err>'+(j.msg||'生成失败')"
            "+'</div>');document.getElementById('ar-msg').textContent="
            "j.ok?('✅ 报告就绪 · '+(j.llm_ok?'AI 生成':'模板兜底')"
            "+' · '+j.ts):''}).catch(function(x){"
            "document.getElementById('ar-msg').textContent='生成失败：'+x})}"
            "function _arGen(){document.getElementById('ar-md').innerHTML="
            "'<div class=dim><span class=ar-spin></span>重新生成中…</div>';"
            "document.getElementById('ar-msg').textContent="
            "'正在聚合事实并生成报告…';_arLoad()}"
            "function _arPush(){var m=document.getElementById('ar-msg');"
            "var b=document.getElementById('ar-push');b.disabled=true;"
            "m.textContent='📨 正在推送飞书…';"
            "fetch('/api/report_push?c='+encodeURIComponent(_ARC),"
            "{method:'POST'}).then(function(r){return r.json()})"
            ".then(function(j){m.textContent=(j.ok?'✅ ':'⚠️ ')"
            "+(j.msg||'');b.disabled=false}).catch(function(x){"
            "m.textContent='推送失败：'+x;b.disabled=false})}"
            "_arLoad();</script>"
            % (e(q), e(q), json.dumps(q.replace("\"", ""))))
        self._html("".join(body))

    def _api_analyst_report(self, qs):
        """GET /api/analyst-report?c= → 生成报告 JSON（前端异步用）。"""
        from urllib.parse import parse_qs, unquote as _uq
        q = _uq((parse_qs(qs).get("c") or [""])[0]).strip()[:24]
        if not q:
            self._json({"ok": False, "msg": "缺少 code 参数"})
            return
        try:
            if os.path.join(STATION, "portal") not in sys.path:
                sys.path.insert(0, os.path.join(STATION, "portal"))
            import portal_report as _PR
            rep = _PR.build_report(self, q)
            self._json({"ok": True, "md": rep["md"],
                        "llm_ok": rep["llm_ok"], "ts": rep["ts"],
                        "name": rep.get("name"),
                        "px": rep.get("px")})
        except Exception as ex:
            import traceback
            try:
                with open(r"C:\Temp\analyst_report_err.log", "a",
                          encoding="utf-8") as f:
                    f.write(time.strftime("%m-%d %H:%M:%S ") + q + "\n")
                    traceback.print_exc(file=f)
            except OSError:
                pass
            self._json({"ok": False, "msg": "报告生成异常：%s"
                        % str(ex)[:150]})

    def _api_report_push(self, qs):
        """POST /api/report_push?c= → 生成报告并推送飞书。"""
        from urllib.parse import parse_qs, unquote as _uq
        q = _uq((parse_qs(qs).get("c") or [""])[0]).strip()[:24]
        if not q:
            self._json({"ok": False, "msg": "缺少 code 参数"})
            return
        try:
            if os.path.join(STATION, "portal") not in sys.path:
                sys.path.insert(0, os.path.join(STATION, "portal"))
            import portal_report as _PR
            ok, msg = _PR.push_report(self, q)
            self._json({"ok": bool(ok), "msg": msg})
        except Exception as ex:
            self._json({"ok": False, "msg": "推送异常：%s"
                        % str(ex)[:150]})

    # ---------------------------------------------------- K245 订阅推送
    def _sub_engine(self):
        """惰性 import 订阅引擎（quant_backtest/subscribe.py）。"""
        qb = os.path.join(STATION, "quant_backtest")
        if qb not in sys.path:
            sys.path.insert(0, qb)
        import subscribe
        return subscribe

    def _sub_data(self):
        """读订阅清单 + 接收人 + 状态（门户展示用）。"""
        import json as _j
        qb = os.path.join(STATION, "quant_backtest")

        def _rd(fn, dflt):
            try:
                with open(os.path.join(qb, fn), encoding="utf-8") as f:
                    return _j.load(f)
            except Exception:
                return dflt
        subs = (_rd("subscriptions.json", {}) or {}).get("subscriptions") or []
        recvs = (_rd("receivers.json", {}) or {}).get("receivers") or []
        st = _rd(os.path.join("outbox", "subscribe_state.json"), {})
        return subs, recvs, st

    def _sub_write(self, fn, obj):
        import json as _j
        qb = os.path.join(STATION, "quant_backtest")
        tmp = os.path.join(qb, fn + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            _j.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(qb, fn))

    def _subscribe_page(self, qs):
        """GET /bt/subscribe → 订阅管理页（K245）。"""
        import html as _h
        subs, recvs, st = self._sub_data()
        KIND_LABEL = {"stock_report": "个股报告", "daily_batch": "每日选股批次",
                      "alert_signal": "预警信号", "quote_move": "自选股异动"}
        whn = {"intraday": "盘中", "eod": "盘后"}
        today = time.strftime("%Y-%m-%d")
        rows = []
        for s in subs:
            sid = s.get("id", "")
            kind = KIND_LABEL.get(s.get("kind"), s.get("kind", "—"))
            when_s = " + ".join(whn.get(w, w) for w in (s.get("when") or []))
            recv = "、".join(s.get("receivers") or []) or "（默认）"
            sst = st.get(sid) or {}
            last = sst.get("last_ts") or "—"
            cnt = sst.get("count", 0) if sst.get("day") == today else 0
            on = s.get("enabled", True)
            rows.append(
                "<tr>"
                "<td><b>%s</b><br><span class='dim'>%s</span></td>"
                "<td>%s</td><td>%s</td><td>%s</td>"
                "<td>%s<br><span class='dim'>今日推 %d / 上限 3</span></td>"
                "<td><label class='sw'><input type='checkbox' %s "
                "onchange=\"subToggle('%s',this.checked)\"><span></span></label>"
                "</td>"
                "<td><button class='mini' onclick=\"subTest('%s')\">试推</button>"
                "<button class='mini danger' onclick=\"subDel('%s')\">删</button>"
                "</td></tr>" % (
                    _h.escape(s.get("name", sid)), _h.escape(sid),
                    _h.escape(kind), _h.escape(when_s), _h.escape(recv),
                    _h.escape(last), cnt, "checked" if on else "",
                    sid, sid, sid))

        recv_rows = []
        for r in recvs:
            recv_rows.append(
                "<tr><td><b>%s</b></td><td>%s</td>"
                "<td><code>%s</code></td><td>%s</td></tr>"
                % (_h.escape(r.get("name", "")), _h.escape(r.get("type", "")),
                   _h.escape(r.get("id", "") or ""),
                   _h.escape(r.get("note", ""))))

        css = """<style>
.sb-wrap{max-width:1100px;margin:0 auto;padding:18px}
.sb-wrap h1{font-size:20px;margin:0 0 4px}
.sb-sub{color:#8b949e;font-size:13px;margin-bottom:16px}
.sb-card{background:#0d1117;border:1px solid #30363d;border-radius:10px;
padding:16px 18px;margin-bottom:16px}
.sb-card h2{font-size:15px;margin:0 0 12px;color:#e6edf3}
.sb-card table{width:100%;border-collapse:collapse;font-size:13px}
.sb-card th,.sb-card td{text-align:left;padding:9px 8px;
border-bottom:1px solid #21262d;vertical-align:middle}
.sb-card th{color:#8b949e;font-weight:500;font-size:12px}
.dim{color:#8b949e;font-size:11px}
.mini{background:#161b22;border:1px solid #30363d;color:#e6edf3;
border-radius:6px;padding:3px 10px;font-size:12px;cursor:pointer;
margin-right:5px;font-family:inherit}
.mini:hover{border-color:#58a6ff}
.mini.danger:hover{border-color:#f85149;color:#f85149}
.sw{position:relative;display:inline-block;width:40px;height:22px}
.sw input{display:none}
.sw span{position:absolute;inset:0;background:#3a4459;border-radius:11px;
transition:.2s;cursor:pointer}
.sw span:before{content:"";position:absolute;width:16px;height:16px;left:3px;
top:3px;background:#fff;border-radius:50%;transition:.2s}
.sw input:checked+span{background:#238636}
.sw input:checked+span:before{transform:translateX(18px)}
code{background:#161b22;padding:1px 5px;border-radius:4px;font-size:11px}
.sb-recv input,.sb-recv select{background:#0d1117;border:1px solid #30363d;
color:#e6edf3;border-radius:6px;padding:5px 8px;font-size:12px;margin-right:6px}
.sb-toast{position:fixed;top:20px;right:20px;background:#1f2937;color:#fff;
padding:10px 16px;border-radius:8px;font-size:13px;opacity:0;
transition:.3s;z-index:9}
.sb-toast.show{opacity:1}
</style>"""

        html = (self._COCKPIT_CSS + css + "<div class='sb-wrap'>"
                + pill_nav("qpzm")
                + "<h1>🔔 订阅推送</h1>"
                "<div class='sb-sub'>自选订阅 · 飞书自动推送 · 盘中定时 + 盘后汇总"
                " · 青萍之末 · 零界</div>"
                "<div class='sb-card'><h2>订阅清单</h2>"
                "<table><thead><tr><th>订阅</th><th>类型</th><th>时机</th>"
                "<th>接收人</th><th>最近推送</th><th>启用</th><th>操作</th>"
                "</tr></thead><tbody>"
                + ("\n".join(rows) or "<tr><td colspan='7'>（暂无订阅）</td></tr>")
                + "</tbody></table>"
                "<p class='dim' style='margin-top:10px'>※ 盘中每 30 分钟扫描，"
                "仅推「有变化」的；盘后 15:30 汇总全量。每订阅每日最多推 3 次"
                "（防轰炸）。</p></div>"
                "<div class='sb-card'><h2>接收人名单</h2>"
                "<table><thead><tr><th>名称</th><th>类型</th><th>ID</th>"
                "<th>备注</th></tr></thead><tbody>"
                + ("\n".join(recv_rows) or "<tr><td colspan='4'>（暂无）</td></tr>")
                + "</tbody></table>"
                "<div class='sb-recv' style='margin-top:14px'>"
                "<input id='rn' placeholder='名称，如 张三' style='width:140px'>"
                "<select id='rt'><option value='open_id'>open_id（个人）</option>"
                "<option value='chat_id'>chat_id（群）</option>"
                "<option value='email'>email</option>"
                "<option value='mobile'>mobile</option></select>"
                "<input id='ri' placeholder='接收人 ID（ou_xxx / oc_xxx）' "
                "style='width:280px'>"
                "<button class='mini' onclick='recvAdd()'>＋ 添加接收人</button>"
                "</div>"
                "<p class='dim' style='margin-top:8px'>※ 为后续「他人飞书付费订阅」"
                "预留：添加对方 open_id 后，可在订阅里指定推给他。</p></div>"
                "<div class='sb-toast' id='sb-toast'></div>"
                "<script>"
                "function toast(m){var t=document.getElementById('sb-toast');"
                "t.textContent=m;t.classList.add('show');"
                "setTimeout(function(){t.classList.remove('show')},2400)}"
                "function api(u,b){return fetch(u,{method:b?'POST':'GET'})"
                ".then(function(r){return r.json()})"
                ".catch(function(e){toast('请求失败');throw e})}"
                "function subToggle(id,on){api('/api/sub_toggle?id='+"
                "encodeURIComponent(id)+'&enabled='+(on?1:0),true)"
                ".then(function(d){toast(d.ok?(on?'已启用 ':'已停用 ')+id:"
                "'操作失败');if(!d.ok)location.reload()})"
                ".catch(function(){location.reload()})}"
                "function subTest(id){toast('正在试推 '+id+'（报告类需 15-40s）…');"
                "api('/api/sub_test?id='+encodeURIComponent(id),true)"
                ".then(function(d){toast(d.msg||(d.ok?'推送成功':'推送失败'))})}"
                "function subDel(id){if(!confirm('删除订阅 '+id+'？'))return;"
                "api('/api/sub_del?id='+encodeURIComponent(id),true)"
                ".then(function(d){toast(d.ok?'已删除':'删除失败');"
                "if(d.ok)location.reload()})}"
                "function recvAdd(){var n=document.getElementById('rn').value.trim();"
                "var t=document.getElementById('rt').value;"
                "var i=document.getElementById('ri').value.trim();"
                "if(!n||!i){toast('名称和 ID 都要填');return}"
                "api('/api/recv_add?name='+encodeURIComponent(n)+'&type='+"
                "encodeURIComponent(t)+'&id='+encodeURIComponent(i),true)"
                ".then(function(d){toast(d.ok?'已添加':'添加失败');"
                "if(d.ok)location.reload()})}"
                "</script></div></body></html>")
        self._html(html)

    def _api_sub_toggle(self, qs):
        from urllib.parse import parse_qs
        p = parse_qs(qs)
        sid = (p.get("id") or [""])[0]
        en = (p.get("enabled") or ["1"])[0] in ("1", "true", "True")
        d = self._sub_data()[0]
        obj = {"subscriptions": d}
        hit = False
        for s in obj["subscriptions"]:
            if s.get("id") == sid:
                s["enabled"] = en
                hit = True
        if not hit:
            self._json({"ok": False, "msg": "订阅不存在"})
            return
        self._sub_write("subscriptions.json", obj)
        self._json({"ok": True, "enabled": en})

    def _api_sub_del(self, qs):
        from urllib.parse import parse_qs
        sid = (parse_qs(qs).get("id") or [""])[0]
        d = self._sub_data()[0]
        keep = [s for s in d if s.get("id") != sid]
        if len(keep) == len(d):
            self._json({"ok": False, "msg": "订阅不存在"})
            return
        self._sub_write("subscriptions.json", {"subscriptions": keep})
        self._json({"ok": True})

    def _api_sub_test(self, qs):
        """POST /api/sub_test?id= → 调订阅引擎试推一条。"""
        from urllib.parse import parse_qs
        sid = (parse_qs(qs).get("id") or [""])[0]
        if not sid:
            self._json({"ok": False, "msg": "缺少 id"})
            return
        try:
            eng = self._sub_engine()
            res = eng.run_scan("intraday", only_sub=sid, force=True)
            st, _ = (res.get(sid) or ("no_result", {}))
            ok = st == "ok"
            self._json({"ok": ok, "msg": ("📨 已推送 %s" % sid) if ok
                        else ("未推送：%s" % st)})
        except Exception as ex:
            self._json({"ok": False, "msg": "试推异常：%s" % str(ex)[:150]})

    def _api_recv_add(self, qs):
        from urllib.parse import parse_qs, unquote as _uq
        p = parse_qs(qs)
        name = _uq((p.get("name") or [""])[0]).strip()
        typ = (p.get("type") or ["open_id"])[0]
        rid = _uq((p.get("id") or [""])[0]).strip()
        if not name or not rid:
            self._json({"ok": False, "msg": "名称和 ID 必填"})
            return
        import json as _j
        qb = os.path.join(STATION, "quant_backtest")
        try:
            with open(os.path.join(qb, "receivers.json"), encoding="utf-8") as f:
                obj = _j.load(f)
        except Exception:
            obj = {"receivers": []}
        rs = obj.get("receivers") or []
        for r in rs:
            if r.get("name") == name:
                r["type"], r["id"] = typ, rid
                break
        else:
            rs.append({"name": name, "type": typ, "id": rid})
        obj["receivers"] = rs
        self._sub_write("receivers.json", obj)
        self._json({"ok": True})

    def _api_feishu_event(self, raw):
        """K250 飞书事件回调入口（POST /feishu/event）。

        飞书会往这里推两类东西：
          1. url_verification 握手：需原样回 {"challenge": ...} 才算校验通过；
          2. card.action.trigger：卡片按钮被点 → 执行动作 → **就地返回**一张
             新卡（飞书支持「回调响应体即新卡」，无需另调发消息接口，最快）。

        解密：若飞书后台启用了 Encrypt Key，body 是 {"encrypt": "..."}，
        用 AES-256-CBC 解密（key=sha256(encrypt_key)，IV=密文前 16 字节）。
        未配置 encrypt_key 时明文直取。

        安全：P0-1b（2026-10-08）起——配置了 Encrypt Key 则强制
        X-Lark-Signature 验签；未配置则保持现状并在访问日志打 warning。
        叠加 8091 已绑 127.0.0.1（P0-1a），局域网伪造须经 nginx，风险降级。
        """
        import hashlib
        import base64
        body = {}
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            self._json({"code": 0}, 200)
            return

        # ── 验签（P0-1b 配套，2026-10-08：飞书回调不走 portal token）──
        # 配置了 Encrypt Key → 强制 X-Lark-Signature 校验（飞书 v2 事件签名：
        # sha256(timestamp + nonce + encrypt_key + 原始 body)）；
        # 未配置 → 无共享密钥无法验签，保持现状，但在访问日志打 warning 留痕。
        _ek = self._feishu_encrypt_key()
        if _ek:
            _ts = (self.headers.get("X-Lark-Request-Timestamp") or "").strip()
            _nc = (self.headers.get("X-Lark-Request-Nonce") or "").strip()
            _sig = (self.headers.get("X-Lark-Signature") or "").strip()
            _calc = hashlib.sha256((_ts + _nc + _ek
                                    + raw.decode("utf-8", "replace"))
                                   .encode("utf-8")).hexdigest()
            if not _sig or not hmac.compare_digest(_sig, _calc):
                self._json({"code": 0, "msg": "invalid signature"}, 403)
                return
        else:
            self.log_message(
                "WARNING feishu/event FEISHU_ENCRYPT_KEY 未配置，回调未验签"
                "（叠加 8091 绑回环后局域网须经 nginx 才可达，风险已降级）")

        # ── 解密（如启用）────────────────────────────────
        if body.get("encrypt"):
            enc_key = self._feishu_encrypt_key()
            if not enc_key:
                self._json({"code": 0, "msg": "encrypted but no key"}, 200)
                return
            try:
                from cryptography.hazmat.primitives.ciphers import (
                    Cipher, algorithms, modes)
                data = base64.b64decode(body["encrypt"])
                iv, ct = data[:16], data[16:]
                key = hashlib.sha256(enc_key.encode("utf-8")).digest()
                dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
                pt = dec.update(ct) + dec.finalize()
                pt = pt[:-pt[-1]]                     # 去 PKCS7 padding
                body = json.loads(pt.decode("utf-8"))
            except Exception as ex:
                self._json({"code": 0, "msg": "decrypt fail %s"
                            % str(ex)[:80]}, 200)
                return

        # ── 1) URL 握手 ──────────────────────────────────
        if body.get("type") == "url_verification" or body.get("challenge"):
            self._json({"challenge": body.get("challenge", "")}, 200)
            return

        # ── 2) 卡按钮回调 ────────────────────────────────
        try:
            header = body.get("header") or {}
            et = header.get("event_type") or body.get("type") or ""
            ev = body.get("event") or body
            if et in ("card.action.trigger", "card_action_trigger") \
                    or ev.get("action"):
                self._handle_card_action(ev)
                return
        except Exception as ex:
            self._json({"code": 0, "msg": "handle fail %s"
                        % str(ex)[:100]}, 200)
            return
        # 其它事件：静默 200，避免飞书重推
        self._json({"code": 0}, 200)

    def _feishu_encrypt_key(self):
        """读飞书 Encrypt Key（~/.lark-cli/config.json 或 env）。

        A4（2026-10-07）：回调验签应用与推送应用解耦——apps 数组里取
        **第一个带 encryptKey 的应用**（推送 bot apps[0] 无 key 不受影响，
        新建回调应用 apps[1] 携带 key 生效）。
        """
        ek = os.environ.get("FEISHU_ENCRYPT_KEY")
        if ek:
            return ek
        try:
            cfg = os.path.join(os.path.expanduser("~"), ".lark-cli",
                               "config.json")
            with open(cfg, encoding="utf-8") as f:
                apps = json.load(f).get("apps") or []
            for a in apps:
                k = a.get("encryptKey") or a.get("encrypt_key")
                if k and k != "EMPTY":
                    return k
        except Exception:
            pass
        return None

    def _handle_card_action(self, ev):
        """卡按钮被点 → 执行动作 → 回卡（作为 HTTP 响应体）。"""
        act = (ev.get("action") or {})
        val = act.get("value") or {}
        if isinstance(val, str):
            try:
                val = json.loads(val)
            except Exception:
                val = {}
        # 点击者 open_id（用于把订阅指向本人）
        operator = None
        try:
            op = ev.get("operator") or {}
            operator = op.get("open_id") or op.get("user_id") or None
        except Exception:
            operator = None
        qb = os.path.join(STATION, "quant_backtest")
        if qb not in sys.path:
            sys.path.insert(0, qb)
        import subscribe_action as _SA
        ok, msg, card = _SA.subscribe_from_card(val, operator=operator)
        if card:
            # 飞书卡回调：响应体直接当「更新后卡片」。
            # 用 cover/toast 结构让飞书就地更新并弹提示。
            self._json({
                "toast": {"type": "success" if ok else "info",
                          "content": msg},
                "card": {"type": "raw", "data": card},
            }, 200)
        else:
            self._json({"toast": {"type": "error", "content": msg}}, 200)





    def _back_link_html(self):
        """统一返回键（Handler 侧别名，委托模块级实现——个股页等
        既有调用点不改）。"""
        return _back_link_html()

    def _back_bar_html(self):
        """统一返回条（个股全景页等用）：_back_link_html 包容器。
        兜底已从 /bt 升级为 /portal（K-STOCK-035 门户架构）。"""
        return ("<div style='margin:0 0 10px'>" + self._back_link_html()
                + "</div>")



    def _nl_page(self, qs):
        """P-E 自然语言查数页：输入问句 → multi_agent 多 agent 答案。
        页面加载只读缓存（read_only）；按钮 force=1 触发现算（10-30s）。
        specialist 调用链 <details> 下钻。
        K-STOCK-055b 回炉对齐全站扁平风格（老田反馈：与平台整体
        颜色/风格/布局不一致）：弃渐变按钮/英雄区/彩色徽章/spinner，
        回归 GitHub Dark 家族语言——h1 22px + .dim 说明行 + .sec/.card
        边框卡 + 实心绿主按钮(#238636) + 灰字来源行（与 /sa 家族一致）。"""
        import html as _he
        from urllib.parse import parse_qs as _pqs, unquote as _uq, quote as _uq2
        _qv = _uq((_pqs(qs).get("q") or [""])[0])
        _esc = _he.escape(_qv, quote=True)
        _NL_CSS = """<style>
.nl-search{display:flex;gap:10px;align-items:stretch;max-width:640px;margin-top:12px}
.nl-search input{flex:1;min-width:0;padding:12px 14px;font-size:14px;font-family:inherit;
color:var(--text,#e6edf3);background:var(--bg-raised,#161b22);
border:1px solid var(--border,#30363d);border-radius:10px;outline:none}
.nl-search input:focus{border-color:var(--link,#58a6ff)}
.nl-search input::placeholder{color:var(--text-dim,#8b949e)}
.nl-go{flex:none;padding:0 20px;font-size:14px;font-weight:600;font-family:inherit;
color:#fff;background:#238636;border:none;border-radius:10px;cursor:pointer}
.nl-go:hover{background:#2ea043}
.nl-chips{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px}
.nl-chips a{display:inline-block;padding:4px 12px;border:1px solid var(--border,#30363d);
border-radius:14px;font-size:12.5px;color:var(--text-dim,#8b949e);
background:var(--bg-raised,#161b22);text-decoration:none}
.nl-chips a:hover{color:var(--link,#58a6ff);border-color:var(--link,#58a6ff)}
.nl-ans pre{white-space:pre-wrap;word-break:break-word;margin:6px 0 0;
font-size:13.5px;line-height:1.75;font-family:inherit}
.nl-spec{margin-top:10px}
.nl-spec>summary{cursor:pointer;color:var(--link,#58a6ff);font-size:13px}
.nl-spec .sbody{margin-top:6px}
.nl-spec pre{white-space:pre-wrap;word-break:break-word;margin:0;
font-size:12.5px;line-height:1.7;font-family:inherit}
.nl-call{margin-top:6px;font-size:12px;color:var(--text-dim,#8b949e);
font-family:Consolas,monospace;line-height:1.6}
.nl-call b{color:var(--text,#e6edf3);font-weight:600}
</style>"""
        # K-STOCK-057 补全标准页壳（此前裸片段→浏览器白底，风格与全站割裂）
        _SHELL_HEAD = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                       "<meta name='viewport' content='width=device-width,"
                       "initial-scale=1'><title>💬 查数 · 青萍之末</title>"
                       + self._COCKPIT_CSS + "</head><body>")
        _SHELL_TAIL = "</body></html>"
        body = [_SHELL_HEAD, pill_nav("nl"), _NL_CSS,
                "<h1>💬 自然语言查数</h1>",
                "<div class='dim'>本地 qwen3.5:9b 多 agent（router → "
                "specialists → synth）· 信息层只读 · 当日缓存复用 · "
                "不外发数据</div>",
                "<div class='nl-search'>"
                "<input id='q' placeholder='如 688411最近怎么样 / 1卖趋势背驰的左尾 / "
                "预警池最新10条信号'"
                " onkeydown=\"if(event.key==='Enter')_go()\"%s>"
                "<button class='nl-go' onclick='_go()'>查数</button></div>"
                "<div class='nl-chips'>"
                "<span class='dim'>试一试：</span>"
                "<a href='/nl?q=%s'>688411 最近怎么样</a>"
                "<a href='/nl?q=%s'>1卖趋势背驰的左尾</a>"
                "<a href='/nl?q=%s'>预警池最新10条信号</a></div>"
                % (" value=\"" + _esc + "\"" if _qv else "",
                   _uq2("688411最近怎么样"),
                   _uq2("1卖趋势背驰的左尾"),
                   _uq2("预警池最新10条信号"))]
        # K-STOCK-055 修遗留 bug：_go 从未定义（旧版点「查数」为空操作）
        body.append("<script>function _go(){var v=document."
                    "getElementById('q').value.trim();if(v){location='/nl?q='"
                    "+encodeURIComponent(v);}}</script>")
        if not _qv:
            body.append("<div class='card dim' style='margin-top:20px'>"
                        "输入问句后回车，router 按 sub_q 分发给 specialist，"
                        "synth 汇总成一段答案；带缓存当日复用，强制重算约 10-30s。</div>")
            self._html("".join(body) + "</body></html>")
            return
        body.append(
            "<h2>当前问句</h2><div class='card'>%s</div>"
            "<div style='margin-top:12px'>"
            "<button onclick='_nlForce()'>🔄 强制重算（10-30s）</button></div>"
            "<div id='nlout' class='dim' style='margin-top:12px'></div>")
        body.append(
            "<script>var _nlq=" + json.dumps(_qv, ensure_ascii=False) + ";" + r"""
function _esc(s){return (s||'').replace(/</g,'&lt;')}
function _wait(t){document.getElementById('nlout').textContent=t}
function _nlShow(j){
 var o=document.getElementById('nlout');
 if(!j.ok){o.innerHTML='<div class="card" style="color:var(--color-err,#f85149)">失败：'
  +_esc(j.err||'未知')+'</div>';return;}
 if(j.empty){o.innerHTML='<div class="card dim">'+_esc(j.msg)+'</div>';return;}
 var src='来源:multi_agent'+(j.cached?' · 缓存':' · 现算 '+(j.elapsed_s||'?')+'s')
  +(j.degraded?' · ⚠降级拼接':'');
 var h='<div class="sec"><div class=dim>'+src+'</div>'
  +'<pre class=nl-ans>'+_esc(j.answer)+'</pre></div>';
 if(j.specialists&&j.specialists.length){
  h+='<h2 style="margin-top:18px">下钻 · '+j.specialists.length
   +' 条 specialist 调用链</h2>';
  j.specialists.forEach(function(s){
   h+='<details class=nl-spec><summary>【'+_esc(s.specialist)+'】'
    +'<span class=dim>'+_esc(s.sub_q)+'</span></summary>'
    +'<div class="card sbody"><pre>'+_esc(s.answer)+'</pre>';
   if(s.calls&&s.calls.length){
    s.calls.forEach(function(c){
     h+='<div class=nl-call>· <b>'+_esc(c.tool)+'</b> '+_esc(JSON.stringify(c.args))
      +'<br>→ '+_esc(c.res)+'</div>';});}
   h+='</div></details>';});}
 o.innerHTML=h;}
_wait('查数中…（router → specialists → synth，当日缓存命中则秒回）');
fetch('/api/nlquery?q='+encodeURIComponent(_nlq))
 .then(function(r){return r.json()}).then(_nlShow);
function _nlForce(){_wait('强制重算中…（绕过缓存，约 10-30s）');
 fetch('/api/nlquery?q='+encodeURIComponent(_nlq)+'&force=1')
 .then(function(r){return r.json()}).then(_nlShow);}
</script>""")
        body.append("<div class='dim' style='margin-top:26px'>"
                    "信息层查数 · 不改信号/推送/拦截口径 · 不构成投资建议</div>")
        self._html("".join(body) + "</body></html>")


    def _sys_health(self):
        """系统自检页：服务实时探活 + ops/health_latest.json 夜间快照。"""
        import html as _html
        import socket as _sk
        import urllib.request as _u

        def http_ok(url):
            try:
                return _u.urlopen(url, timeout=2.5).status == 200
            except Exception:
                return False

        def tcp_ok(port):
            try:
                s = _sk.create_connection(("127.0.0.1", port), timeout=2)
                s.close()
                return True
            except Exception:
                return False

        # K-STOCK-101 探活并发化：原串行 5 探针最坏 12s 白等，
        # 线程池并发后最坏 = 单探针超时 2.5s
        from concurrent.futures import ThreadPoolExecutor
        _spec = [("门户API(8091)", lambda: http_ok(
                     "http://127.0.0.1:8091/health")),
                 ("nginx(8080)", lambda: http_ok("http://127.0.0.1:8080/")),
                 ("量化前端(5175)", lambda: http_ok(
                     "http://127.0.0.1:5175/")),
                 ("QD桥接(9103)", lambda: tcp_ok(9103)),
                 ("卡片回调(9104)", lambda: tcp_ok(9104))]
        with ThreadPoolExecutor(max_workers=5) as _ex:
            _res = list(_ex.map(lambda kv: (kv[0], kv[1]()), _spec))
        probes = _res
        rows = "".join(
            "<div style='display:flex;justify-content:space-between;"
            "background:#161b22;border:1px solid #21262d;border-radius:6px;"
            "padding:8px 12px;margin:4px 0;font-size:13px'>"
            "<span style='color:#c9d1d9'>%s</span>"
            "<span style='color:%s;font-weight:600'>%s</span></div>"
            % (_html.escape(n), "#3fb950" if ok else "#f85149",
               "✓ 在线" if ok else "✗ 下线")
            for n, ok in probes)

        snap = {}
        try:
            with open(os.path.join(STATION, "ops", "health_latest.json"),
                      encoding="utf-8") as f:
                snap = json.load(f)
        except Exception:
            pass
        blocks = ["<h3 style='color:#8b949e;font-size:13px'>实时探活"
                  "（打开本页即测）</h3>" + rows]
        if snap:
            tk = snap.get("tasks") or {}
            dk = snap.get("docker") or {}
            d = snap.get("data") or {}
            lines = ["容器 %s 个 · ai-portal %s" % (
                        dk.get("total", "?"),
                        "✓" if dk.get("ai_portal") else "<b style='color:#f85149'>✗</b>"),
                     "计划任务 %d 个 · 异常 %d" % (
                        tk.get("total", 0), len(tk.get("bad") or [])),
                     "数据链：24h新闻 %s · 板块 %s · 账本 待买%d/持%d/平%d · 最新快照 %s" % (
                        d.get("news_24h", "?"), d.get("sectors_count", "?"),
                        d.get("pending", 0), d.get("open", 0),
                        d.get("closed", 0), d.get("latest_snapshot", "?"))]
            for iss in (snap.get("issues") or [])[:6]:
                lines.append("<div style='color:#f85149'>⚠ %s</div>"
                             % _html.escape(str(iss)))
            blocks.append(
                "<h3 style='color:#8b949e;font-size:13px;margin-top:18px'>"
                "最近自检快照 · %s（每晚 21:00 随统一日报更新）</h3>"
                "<div style='background:#161b22;border:1px solid #21262d;"
                "border-radius:6px;padding:12px;font-size:13px;"
                "line-height:2'>%s</div>"
                % (_html.escape(str(snap.get("ts", "—"))),
                   "<br>".join(lines)))
        else:
            blocks.append("<div style='color:#8b949e;font-size:12px;"
                          "margin-top:16px'>暂无自检快照（每晚 21:00 "
                          "统一日报生成，或手动运行 ops/health_check.py）"
                          "</div>")
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><meta http-equiv='refresh' content='60'>"
                "<title>系统自检</title></head>"
                "<body style='background:#0d1117;color:#c9d1d9;"
                "font-family:Microsoft YaHei,Arial;margin:0;padding:20px'>"
                "<div style='max-width:760px;margin:0 auto'>"
                "<h2 style='font-size:18px'>🖥 系统自检</h2>%s%s"
                "</div></body></html>"
                % (pill_nav("sys"), "".join(blocks))).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)



    def _send_media(self, abspath, ctype):
        """小文件直发（mp4/wav/srt/json 预览用，无 Range，成片 <5MB）。"""
        try:
            with open(abspath, "rb") as f:
                data = f.read()
        except Exception:
            self._json({"error": "file not found"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _gzh_list(self):
        """公众号草稿库页：gzh/drafts（列表 + 全文预览链接）。"""
        import html as _html
        items = []
        try:
            with open(os.path.join(STATION, "gzh", "drafts", "index.json"),
                      encoding="utf-8") as f:
                items = json.load(f)
        except Exception:
            pass
        ST = {"draft": ("草稿", "#9db1d4"), "reviewed": ("已审", "#e6a23c"),
              "published": ("已发", "#f85149")}
        from urllib.parse import quote as _q
        rows = "".join(
            "<tr><td><a href='/gzh/view/%s' style='color:#58a6ff;"
            "text-decoration:none'>%s</a></td><td>%s</td><td>%s</td>"
            "<td>%s</td><td style='color:%s'>%s</td></tr>" % (
                _q(e.get("md", "") + ".preview.html"),
                _html.escape(e.get("title", e.get("id", ""))),
                _html.escape(e.get("col", "")),
                _html.escape(str(e.get("date", ""))),
                _html.escape(str(e.get("ts", ""))),
                ST.get(e.get("status"), ("?", "#9db1d4"))[1],
                ST.get(e.get("status"), ("?", "#9db1d4"))[0])
            for e in items) or "<tr><td colspan=5>—</td></tr>"
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>公众号草稿库</title>"
                "<link rel='stylesheet' href='/static/tokens.css'></head>"
                "<body style='background:#0d1117;color:#c9d1d9;"
                "font-family:Microsoft YaHei,Arial;margin:0;padding:20px'>"
                "<div style='max-width:860px;margin:0 auto'>%s"
                "<h2 style='font-size:18px'>📚 公众号草稿库"
                "<span style='color:#58606a;font-size:12px'>（每日 09:30 "
                "选题成文 · 点击标题阅读全文）</span></h2>"
                "<table style='border-collapse:collapse;width:100%%;"
                "font-size:13px'>"
                "<tr><th style='padding:6px 10px;text-align:left;"
                "color:#8b949e'>标题</th><th style='padding:6px 10px;"
                "color:#8b949e'>栏目</th><th style='padding:6px 10px;"
                "color:#8b949e'>日期</th><th style='padding:6px 10px;"
                "color:#8b949e'>生成</th><th style='padding:6px 10px;"
                "color:#8b949e'>状态</th></tr>%s</table>"
                "<div style='color:#58606a;font-size:11px;margin-top:16px'>"
                "内容生产域 · 飞书审阅后标记已审/已发</div>"
                "</div></body></html>" % (pill_nav("gzh"), rows)
                ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _vd_list(self):
        """短视频成片库页：video/output（内嵌 video 预览 + 字幕/脚本）。"""
        import html as _html
        from urllib.parse import quote as _q
        items = []
        try:
            with open(os.path.join(STATION, "video", "output", "index.json"),
                      encoding="utf-8") as f:
                items = json.load(f)
        except Exception:
            pass
        secs = []
        for e in items:
            d = _q(e.get("dir", ""))
            secs.append("""
<div style='background:#161b22;border:1px solid #21262d;border-radius:10px;
padding:14px;margin:12px 0'>
<div style='display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px'>
<b style='font-size:14px'>%s</b>
<span style='color:#8b949e;font-size:12px'>%s · %s 秒 · %s 场景 · %s MB</span></div>
<video controls preload='metadata' src='/vd/file/%s/video.mp4'
style='width:100%%;max-width:640px;margin-top:10px;border-radius:8px;
background:#000'></video>
<div style='margin-top:8px;font-size:12px'>
<a href='/vd/file/%s/subs.srt' style='color:#58a6ff;text-decoration:none'>📝 字幕 srt</a>
<span style='color:#30363d'> │ </span>
<a href='/vd/file/%s/script.json' style='color:#58a6ff;text-decoration:none'>📜 脚本 json</a>
<span style='color:#30363d'> │ </span>
<a href='/vd/file/%s/video.mp4' style='color:#58a6ff;text-decoration:none'>⬇ 下载 mp4</a>
</div></div>""" % (_html.escape(e.get("title", e.get("id", ""))),
                    _html.escape(str(e.get("ts", ""))),
                    _html.escape(str(e.get("duration", "?"))),
                    _html.escape(str(e.get("scenes", "?"))),
                    _html.escape(str(e.get("size_mb", "?"))), d, d, d, d))
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>短视频成片库</title>"
                "<link rel='stylesheet' href='/static/tokens.css'></head>"
                "<body style='background:#0d1117;color:#c9d1d9;"
                "font-family:Microsoft YaHei,Arial;margin:0;padding:20px'>"
                "<div style='max-width:860px;margin:0 auto'>%s"
                "<h2 style='font-size:18px'>🎬 短视频成片库"
                "<span style='color:#58606a;font-size:12px'>（全自动成片 · "
                "浏览器内直接预览）</span></h2>%s"
                "<div style='color:#58606a;font-size:11px;margin-top:16px'>"
                "内容生产域 · pyttsx3 配音 + ffmpeg 合成</div>"
                "</div></body></html>" % (pill_nav("vd"),
                "".join(secs) or
                "<div style='color:#8b949e'>暂无成片</div>")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    _KG_CSS = """<style>
.tl{position:relative;margin:12px 0 0;padding-left:20px}
.tl::before{content:'';position:absolute;left:5px;top:6px;bottom:6px;
width:2px;background:#30363d}
.ti{position:relative;margin:0 0 13px}
.ti::before{content:'';position:absolute;left:-19px;top:7px;width:8px;
height:8px;border-radius:50%;background:#58a6ff}
.bdg{display:inline-block;padding:1.5px 9px;border-radius:10px;
font-size:11px;font-weight:600;margin-right:6px}
.bdg.g{background:#2d1416;color:#f85149;border:1px solid #5a2326}
.bdg.b{background:#0d1d33;color:#58a6ff;border:1px solid #1f3a5f}
.bdg.r{background:#12261e;color:#3fb950;border:1px solid #1f4d2b}
.bdg.e{background:#2d1416;color:#f85149;border:1px solid #5a2326}
.bdg.y{background:#2a2111;color:#d29922;border:1px solid #4d3a13}
.bdg.n{background:#161b22;color:#8b949e;border:1px solid #30363d}
.hb{height:10px;border-radius:5px;background:#1f6feb;display:inline-block;
vertical-align:middle}
.src{border-left:2px solid #30363d;margin:4px 0 0 10px;padding:2px 0 2px 12px;
font-size:12px;color:#8b949e}
.kfind{padding:8px 12px;background:#161b22;border:1px solid #30363d;
border-radius:8px;color:#e6edf3;font-size:13.5px;width:250px}
.kfbtn{padding:8px 14px;background:#1f6feb;border:none;border-radius:8px;
color:#fff;font-size:13.5px;cursor:pointer}
</style>"""












    def _sim_page(self, qs):
        """K68 三操盘手模拟锦标赛页：六本账（3 操盘手 × 2 盘算）真实日K
        买卖点战绩榜，对照买入持有基准。        只读 reports 两个 json。"""
        import html as _h

        def e(x):  # K-STOCK-057 防 float/None 穿透（json 字段类型不稳，escape 只吃 str）
            if x is None:
                return ""
            if not isinstance(x, str):
                x = str(x)
            return _h.escape(x)

        ev, sim = {}, {}
        try:
            with open(os.path.join(REPORTS_DIR, "trader_sim_eval.json"),
                      encoding="utf-8") as f:
                ev = json.load(f)
        except Exception:
            pass
        try:
            with open(os.path.join(REPORTS_DIR, "trader_sim.json"),
                      encoding="utf-8") as f:
                sim = json.load(f)
        except Exception:
            pass
        BOOKS = ["激进|积极", "激进|保守", "保守|积极",
                 "保守|保守", "中立|积极", "中立|保守"]
        # K70 策略来源标签（与 stock_council.SID_DESC 同步）
        SID_LABEL = {"qpzm": "青萍之末", "9dim": "九维评分",
                     "sig4": "四策略信号", "chanlun": "缠论结构",
                     "manual": "手动"}

        def _sl(sid):
            return SID_LABEL.get(sid or "", sid or "—")

        def _pc(x):
            return ("%+.2f" % x) if x is not None else "—"

        def _cell(r, bk):
            x = (r.get("books") or {}).get(bk)
            if not x:
                return "<td class='dim'>—</td>"
            tip = "%s · 出场 %s · 仓位 ×%s" % (
                "/".join(x.get("reason") or ["—"]),
                x.get("exit_d") or "—",
                x.get("size_eff") or "—")
            return ("<td class='%s' title='%s'>%s</td>"
                    % ("up" if (x.get("contrib") or 0) >= 0 else "dn",
                       e(tip), _pc(x.get("contrib"))))

        # 六本账汇总卡
        ag = ev.get("books") or {}
        cards = "".join(
            "<div class='sstat'><span class='st'>%s</span>"
            "<span class='sv'>%s</span><span class='su'>n=%d 胜率 %.0f%% "
            "α %+.2f</span></div>"
            % (e(bk), _pc((ag.get(bk) or {}).get("avg_contrib")),
               (ag.get(bk) or {}).get("n") or 0,
               (ag.get(bk) or {}).get("winrate") or 0.0,
               (ag.get(bk) or {}).get("avg_alpha") or 0.0)
            for bk in BOOKS)
        cards = ("<div style='display:grid;grid-template-columns:repeat("
                 "auto-fit,minmax(150px,1fr));gap:10px'>%s</div>" % cards)
        # K70 策略来源 × 操盘手 交叉表（复盘归因：哪个策略的票在哪本账好）
        sids = ev.get("sids") or {}
        srs = "".join(
            "<tr><td><span title='%s'>%s</span></td><td>%d</td>%s"
            "<td class='%s'>%+.2f</td><td><span class='bdg n'>%s</span>"
            "</td></tr>"
            % (e(sid), e((s.get("label") or _sl(sid))),
               s.get("n") or 0,
               "".join("<td class='%s'>%s</td>"
                       % ("up" if (s.get(bk) or 0) >= 0 else "dn",
                          _pc(s.get(bk)) if s.get(bk) is not None else
                          "<span class='dim'>—</span>")
                       for bk in BOOKS),
               "up" if (s.get("total") or 0) >= 0 else "dn",
               s.get("total") or 0.0, e(s.get("best_bk") or "—"))
            for sid, s in sorted(sids.items(),
                                 key=lambda kv: -(kv[1].get("total")
                                                  or 0))) or \
            ("<tr><td colspan=10 class='dim'>暂无策略来源汇总——入场后"
             "自动按策略分组</td></tr>")
        tbl_sid = ("<div class='sec'><h2>📡 策略来源 × 操盘手（复盘归因 · "
                   "哪个策略的票交给哪位操盘手最赚）</h2>"
                   "<table class='sticky'><tr><th>策略来源</th><th>票数</th>"
                   "<th>激进·积极</th><th>激进·保守</th><th>保守·积极</th>"
                   "<th>保守·保守</th><th>中立·积极</th><th>中立·保守</th>"
                   "<th>合计贡献</th><th>最佳账</th></tr>%s</table>"
                   "<div class='dim' style='font-size:12.5px;margin-top:6px'>"
                   "行=选股策略（青萍之末/缠论/九维…），列=六本账累计贡献度"
                   "——留痕全链带来源标签，供复盘归因与 AI 再训练取数。</div>"
                   "</div>" % srs)
        # 战绩明细（新→旧）
        rows = sorted(ev.get("rows") or [],
                      key=lambda r: r.get("entry_d") or "", reverse=True)[:60]
        trs = "".join(
            "<tr><td>%s</td><td><a href='/kg?c=%s'>%s %s</a></td>"
            "<td title='%s'>%s</td><td>%s@%s</td>%s<td class='%s'>%s</td>"
            "<td><span class='bdg n'>%s</span></td></tr>"
            % (e(str(r.get("pkey") or "").split("|")[-1]),
               e(r.get("code") or ""), e(r.get("code") or ""),
               e(r.get("name") or ""), e(r.get("sid") or ""),
               e(r.get("sid_label") or _sl(r.get("sid"))),
               e(r.get("entry_d") or "—"), e(r.get("entry") or "—"),
               "".join(_cell(r, bk) for bk in BOOKS),
               "up" if (r.get("baseline") or 0) >= 0 else "dn",
               _pc(r.get("baseline")), e(r.get("best") or "—"))
            for r in rows) or \
            ("<tr><td colspan=12 class='dim'>暂无入场——立案票在 pending 等"
             "日K，或投票反对未接单</td></tr>")
        tbl = ("<div class='sec'><h2>🏆 战绩榜（每票 × 六本账贡献度 · "
               "对照买入持有基准 · 新→旧）</h2>"
               "<table class='sticky'><tr><th>立案日</th><th>标的</th>"
               "<th>策略来源</th><th>入场</th><th>激进·积极</th>"
               "<th>激进·保守</th><th>保守·积极</th><th>保守·保守</th>"
               "<th>中立·积极</th><th>中立·保守</th><th>基准</th>"
               "<th>最佳</th></tr>%s</table>"
               "<div class='dim' style='font-size:12.5px;margin-top:6px'>"
               "单元格=贡献度（收益率×仓位系数×出仓比例），悬停看离场理由"
               "与实际仓位；基准=同窗买入持有。</div></div>" % trs)
        # 持仓明细（六本账在仓）
        pos_rows = []
        for bk in BOOKS:
            for pk, p in ((sim.get("books") or {}).get(bk)
                          or {}).get("pos", {}).items():
                pos_rows.append((bk, p))
        pos_rows.sort(key=lambda x: x[1].get("entry_d") or "", reverse=True)
        pr = "".join(
            "<tr><td>%s</td><td><a href='/kg?c=%s'>%s %s</a></td>"
            "<td title='%s'>%s</td><td>%s@%s</td><td>×%.2f</td>"
            "<td>%s（%s）</td><td>%s</td><td>%d/%d</td><td>%s</td></tr>"
            % (e(bk), e(p.get("code") or ""), e(p.get("code") or ""),
               e(p.get("name") or ""), e(p.get("sid") or ""),
               _sl(p.get("sid")),
               e(p.get("entry_d") or ""), e(p.get("entry") or ""),
               p.get("size_eff") or 0,
               e(p.get("stop") or "—"), e(p.get("stop_rule") or "—"),
               e(str(p.get("tp") or "—")), p.get("n_days") or 0,
               p.get("max_days") or 0, e(p.get("last_d") or "—"))
            for bk, p in pos_rows) or \
            "<tr><td colspan=9 class='dim'>六本账当前空仓</td></tr>"
        tbl_pos = ("<div class='sec'><h2>📒 在仓明细（%d 笔 · 止损线观点线"
                   "优先）</h2><table class='sticky'><tr><th>账本</th>"
                   "<th>标的</th><th>来源</th><th>入场</th><th>仓位</th>"
                   "<th>止损</th><th>止盈</th><th>天数</th><th>最新K</th>"
                   "</tr>%s</table></div>" % (len(pos_rows), pr))
        # 待入场 + 跳过
        pend = ev.get("pending") or {}
        tpr = "".join(
            "<tr><td>%s</td><td>%s %s</td><td title='%s'>%s</td>"
            "<td>×%.1f</td><td>%+d</td>"
            "<td><span class='bdg y'>等日K</span></td></tr>"
            % (e(t.get("d") or "—"), e(t.get("code") or ""),
               e(t.get("name") or ""), e(t.get("sid") or ""),
               _sl(t.get("sid")), t.get("k") or 1.0, t.get("net") or 0)
            for t in sorted(pend.values(),
                            key=lambda x: x.get("d") or "", reverse=True)) or \
            "<tr><td colspan=6 class='dim'>无待入场</td></tr>"
        skp = ev.get("skipped") or {}
        tsk = "".join(
            "<tr><td>%s</td><td>%s %s</td><td class='dim'>%s</td></tr>"
            % (e(s.get("d") or "—"), e(s.get("code") or ""),
               e(s.get("name") or ""), e(s.get("why") or ""))
            for s in sorted(skp.values(),
                            key=lambda x: x.get("d") or "", reverse=True))
        sec_p = ("<div class='sec'><h2>⏳ 待入场（%d 票）</h2>"
                 "<table class='sticky'><tr><th>辩论日</th><th>标的</th>"
                 "<th>账户</th><th>系数</th><th>净多空</th><th>状态</th>"
                 "</tr>%s</table></div>" % (len(pend), tpr))
        if skp:
            sec_p += ("<div class='sec'><h2>🚫 未接单（%d 票）</h2>"
                      "<table class='sticky'><tr><th>辩论日</th><th>标的</th>"
                      "<th>原因</th></tr>%s</table></div>"
                      % (len(skp), tsk))
        method = ("<div class='sec'><h2>管线与口径（用户愿景 · 全留痕可回放）"
                  "</h2><div style='font-size:13.5px;line-height:1.9'>"
                  "选股策略（青萍之末/缠论/九维…）出票（<b>来源标签 sid"
                  "+中文名全链贯穿</b>：辩论留痕/模拟留痕/六本账/买卖点均"
                  "可追溯，供复盘归因与 AI 再训练取数）→ 投委会六视角辩论"
                  "（基本"
                  "面在前）→ 结果交三位操盘手：<b>激进</b>（同向放大 ×1.2）/"
                  "<b>保守</b>（同向缩小 ×0.8）/<b>中立</b>（×1.0，K66 口径"
                  "执行层落地）× 两种盘算：<b>积极</b>（满基数 · TP 半止盈+"
                  "追踪 8%% · 40日）/<b>保守</b>（半基数 · TP 全出落袋 · 20日）"
                  "→ 模拟建仓（辩论日次一交易日开盘，反对票不接单）→ 真实日K"
                  "做买卖点（止损跳空按开盘/盘中按止损价；止损线=观点线 ML/RL "
                  "优先、无效兜底激进-6%%/保守-8%%/中立按桶）→ 每票×每账评价"
                  "贡献度，对照买入持有基准。<b>铁律：只写 reports 两个 json"
                  "，绝不碰真实账本。</b>夜链 23:50 随投委会自动推进，统计"
                  "更新 %s。</div></div>" % e(ev.get("ts") or "—"))
        # K69 模拟投委会（预演层）：预演留痕 + 真实对照打分
        score = {}
        try:
            with open(os.path.join(REPORTS_DIR, "council_sim_score.json"),
                      encoding="utf-8") as f:
                score = json.load(f)
        except Exception:
            pass
        sim_props = []
        try:
            with open(os.path.join(REPORTS_DIR,
                                   "council_sim_proposals.jsonl"),
                      encoding="utf-8") as f:
                for line in f:
                    try:
                        sim_props.append(json.loads(line))
                    except Exception:
                        continue
        except OSError:
            pass
        cmp_ = {"%s|%s|%s" % (x.get("sid"), x.get("code"), x.get("d")): x
                for x in (score.get("rows") or [])}

        def _vc(v):
            return {"支持": "<span class='bdg g'>支持</span>",
                    "反对": "<span class='bdg r'>反对</span>"}.get(
                v, "<span class='bdg n'>%s</span>" % e(str(v or "中性")))

        sim_cards = ("<div style='display:grid;grid-template-columns:repeat("
                     "auto-fit,minmax(150px,1fr));gap:10px'>"
                     "<div class='sstat'><span class='st'>预演票数</span>"
                     "<span class='sv'>%d</span><span class='su'>独立留痕"
                     " sim=True</span></div>"
                     "<div class='sstat'><span class='st'>真实对齐</span>"
                     "<span class='sv'>%s</span><span class='su'>按账·票·日"
                     " 三键对齐</span></div>"
                     "<div class='sstat'><span class='st'>verdict 一致率"
                     "</span><span class='sv'>%s</span><span class='su'>"
                     "预演vs真实结论</span></div>"
                     "<div class='sstat'><span class='st'>平均|Δ系数|</span>"
                     "<span class='sv'>%s</span><span class='su'>K66 仓位系数"
                     "偏差</span></div></div>"
                     % (len(sim_props), score.get("n_pairs") or 0,
                        ("%.0f%%" % score["verdict_agree_rate"])
                        if score.get("verdict_agree_rate") is not None else "—",
                        ("%.2f" % score["avg_abs_dk"])
                        if score.get("avg_abs_dk") is not None else "—"))

        def _srow(i, x):
            key = "%s|%s|%s" % (x.get("sid"), x.get("code"), x.get("d"))
            c = cmp_.get(key) or {}
            vt = x.get("vote") or {}
            dr = x.get("directive") or {}
            k = (dr.get("sizing") or {}).get("k")
            k = 1.0 if k is None else k
            ra = c.get("role_agree") or {}
            n_agree = sum(1 for v in ra.values() if v)
            if c:
                real = "%s <span class='%s'>Δ%+.2f</span>" % (
                    _vc(c.get("real_verdict")),
                    "up" if (c.get("d_k") or 0) >= 0 else "dn",
                    c.get("d_k") or 0)
                roles = "%d/6" % n_agree
            else:
                real = "<span class='dim'>待真实会</span>"
                roles = "<span class='dim'>—</span>"
            tvs = ""
            # K80 插话后立场重测跟随到预演表：取各操盘手最新 stance3
            hs = x.get("human") or []
            st3map = {}
            for h in hs:
                for rp in h.get("replies") or []:
                    w = rp.get("who") or ""
                    if "操盘手" in w and rp.get("stance3") is not None:
                        st3map[w] = rp.get("stance3")
            for v in (x.get("traders") or []):
                st = v.get("stance") or 0
                st2 = v.get("stance2")
                lab = "%s%+d" % ((v.get("name") or "")[:2], st)
                if st2 is not None and st2 != st:      # K73 立场演化高亮
                    lab += "→%+d" % st2
                seg = ""
                st3 = st3map.get(v.get("name") or "")
                cur = st2 if st2 is not None else st
                if st3 is not None and st3 != cur:     # K80 插话后段（紫）
                    seg = "<b style='color:#c58af9'>→%+d</b>" % st3
                    cur = st3
                tvs += ("<span class='bdg %s' title='%s'>%s%s</span> "
                        % ("g" if cur > 0
                           else ("r" if cur < 0 else "n"),
                           e((v.get("answer") or "")[:120].replace("\n",
                                                                   " ")),
                           e(lab), seg))
            if not tvs:
                tvs = "<span class='dim'>—</span>"
            fin = x.get("final") or {}
            fv = fin.get("verdict")
            fbadge = (("<span class='bdg %s' title='%s'>终裁·%s</span>"
                       % ("g" if fv == "支持" else
                          ("r" if fv == "反对" else "n"),
                          e((fin.get("note") or "")[:200]
                            .replace("\n", " ")), e(fv)))
                      if fv else "<span class='dim'>—</span>")
            # K80 插话后终裁重审跟随（verdict 变了才亮紫）
            if hs:
                f2v = (hs[-1].get("final2") or {}).get("verdict")
                if f2v and f2v != fv:
                    fbadge += (" <span class='bdg n' style='border-color:"
                               "#c58af9;color:#c58af9' title='老板插话后"
                               "终裁重审'>插话后·%s</span>" % e(f2v))
            # K73 完整辩论流水（会议记录式，行下方展开）
            flow = []
            m0 = (x.get("memo") or {})
            mtxt = []
            for _k, _lab in (("thesis", "论点"), ("risks", "风险"),
                             ("if_wrong", "错了怎么办")):
                if m0.get(_k):
                    mtxt.append("%s：%s" % (_lab, m0[_k]))
            if mtxt:
                flow.append(("主持人·分析师纪要", "\n".join(mtxt),
                             "信度 %s" % m0.get("conviction", "?")))
            for v in (x.get("traders") or []):
                if v.get("answer"):
                    flow.append(("%s·质询" % v.get("name"),
                                 v["answer"], "%+d" % (v.get("stance") or 0)))
            rb = (x.get("rebuttal") or {}).get("answer") or ""
            if rb:
                flow.append(("主持人·分析师回应", rb, ""))
            for v in (x.get("traders") or []):
                if v.get("answer2"):
                    flow.append(("%s·再表态" % v.get("name"), v["answer2"],
                                 "%+d" % (v.get("stance2")
                                          if v.get("stance2") is not None
                                          else (v.get("stance") or 0))))
            if fin.get("note"):
                flow.append(("终裁", fin["note"],
                             fin.get("verdict") or ""))
            dhtml = ""
            if flow:
                dhtml = ("<tr class='drow'><td colspan=10 "
                         "style='padding:0;border-top:none'>"
                         "<details style='margin:0 0 2px 8px'><summary "
                         "style='cursor:pointer;color:#8ab4f8;font-size:"
                         "12px'>📜 完整辩论流水（%d 轮发言）</summary>"
                         "<div style='max-height:420px;overflow:auto;"
                         "font-size:12.5px;line-height:1.7'>%s</div>"
                         "</details></td></tr>"
                         % (len(flow), "".join(
                             "<div style='margin:4px 0;padding:6px 10px;"
                             "background:rgba(255,255,255,.04);"
                             "border-left:2px solid %s'><b>%s</b>%s"
                             "<div style='white-space:pre-wrap'>%s</div>"
                             "</div>"
                             % ("#f28b82" if "终裁" in who
                                else ("#fdd663" if "操盘手" in who
                                      else "#8ab4f8"),
                                e(who),
                                (" <span class='bdg n'>%s</span>" % e(tag))
                                if tag else "",
                                e(txt))
                             for who, txt, tag in flow)))
            return ("<tr><td>%s</td><td><a href='/kg?c=%s'>%s %s</a> "
                    "<a href='/debate?p=%d' title='进场辩论/插话'"
                    " style='font-size:11.5px'>🎤%s</a></td>"
                    "<td>%s</td><td>%s（w%+.1f）</td>"
                    "<td><span class='bdg n'>%s</span></td>"
                    "<td class='%s'>×%.2f</td><td>%s</td><td>%s</td>"
                    "<td>%s</td><td>%s</td></tr>%s"
                    % (x.get("d"), e(x.get("code") or ""),
                       e(x.get("code") or ""), e(x.get("name") or ""), i,
                       ("×%d" % len(hs)) if hs else "",
                       e(x.get("sid") or ""), _vc(vt.get("verdict")),
                       vt.get("wsum") or 0, e(dr.get("act") or "—"),
                       "up" if k > 1.0 else ("dn" if k < 1.0 else ""),
                       k, tvs, fbadge, real, roles, dhtml))

        sp_rows = "".join(_srow(i, x)
                          for i, x in enumerate(sim_props[::-1][:20])) or \
            ("<tr><td colspan=10 class='dim'>尚无预演——点右上按钮对当日候选"
             "票先辩一遍</td></tr>")
        sec_sim = ("<div class='sec'><h2>🎭 模拟投委会 · 预演层"
                   "<button onclick='_simrun(this)' style='%s;cursor:pointer;"
                   "margin-left:12px;font-size:12.5px'>▶ 预演当日候选票"
                   "</button></h2>"
                   "<div class='dim' style='margin:0 0 8px;font-size:12.5px'>"
                   "真实投委会（夜链 23:50）<b>开会前</b>三幕预演：①六视角"
                   "分析师辩论（LLM+投票+K66 系数，零口径漂移）②<b>操盘手"
                   "质询</b>——激进/保守/中立三位 AI 操盘手带着自己账本处境"
                   "入场交锋 ③<b>终裁</b>——主持人综合两方给终版立场；独立"
                   "留痕不进真实链路，真实会后自动对照打分。</div>%s"
                   "<table class='sticky'><tr><th>辩论日</th><th>标的</th>"
                   "<th>账户</th><th>预演票</th><th>指令</th><th>预演系数"
                   "</th><th>操盘手质询</th><th>终裁</th><th>真实对照</th>"
                   "<th>视角一致</th></tr>%s</table>"
                   "</div>" % (_PILL, sim_cards, sp_rows))
        sjs = ("<script>function _simrun(btn){btn.disabled=true;"
               "fetch('/api/council/simrun',{method:'POST',"
               "headers:{'Content-Type':'application/json'},body:'{}'})"
               ".then(function(r){return r.json();})"
               ".then(function(j){if(j.ok){alert('预演已启动：后台六视角"
               "辩论（每票约几十秒），稍后刷新本页看结果');}"
               "else{alert('失败：'+(j.error||''));btn.disabled=false;}})"
               ".catch(function(e){alert('网络错误：'+e);"
               "btn.disabled=false;});}</script>")
        # K71 取舍决策（仓位固定·有取有舍·事后观察）
        dcs = (ev.get("decisions") or {})
        dagg = dcs.get("agg") or {}
        tips = dcs.get("tips") or []
        sw = sum(a.get("swap_n") or 0 for a in dagg.values())
        swok = sum(a.get("swap_ok") or 0 for a in dagg.values())
        sk = sum(a.get("skip_n") or 0 for a in dagg.values())
        skok = sum(a.get("skip_ok") or 0 for a in dagg.values())

        def _rate(ok, n):
            return ("%.0f%%" % (100.0 * ok / n)) if n else "—"

        dcards = ("<div style='display:grid;grid-template-columns:repeat("
                  "auto-fit,minmax(170px,1fr));gap:10px'>"
                  "<div class='sstat'><span class='st'>换仓决策</span>"
                  "<span class='sv'>%d 笔</span><span class='su'>正确率 %s"
                  "（新票＞被挤票假想）</span></div>"
                  "<div class='sstat'><span class='st'>放弃决策</span>"
                  "<span class='sv'>%d 笔</span><span class='su'>正确率 %s"
                  "（假想收益≤0）</span></div>"
                  "<div class='sstat'><span class='st'>仓位约束</span>"
                  "<span class='sv'>≤100%%</span><span class='su'>每账固定"
                  "资金池·单票≤100%%</span></div></div>"
                  % (sw, _rate(swok, sw), sk, _rate(skok, sk)))

        def _drow(x):
            act = x.get("action")
            if act == "换仓":
                detail = ("%s → <a href='/kg?c=%s'>%s</a>（让位 @%s，"
                          "%+.2f%%）"
                          % (e(x.get("victim_code") or ""),
                             e(x.get("victim_code") or ""),
                             e(x.get("victim_name") or ""),
                             e(x.get("victim_exit") or "—"),
                             x.get("victim_ret") or 0))
                after = ("假想 %+.2f%% vs 新票 %+.2f%%"
                         % (x.get("victim_hyp") or 0,
                            x.get("new_ret") or 0)
                         if x.get("victim_hyp") is not None
                         else "观察中")
            else:
                detail = "%s（余仓 %.2f · %s）" % (
                    e(x.get("note") or ""), x.get("free") or 0,
                    e(x.get("size_eff") or 0))
                after = ("假想 %+.2f%%" % x.get("new_hyp")
                         if x.get("new_hyp") is not None else "观察中")
            c = x.get("correct")
            badge = ("<span class='bdg g'>✓ 对</span>" if c
                     else ("<span class='bdg r'>✗ 错</span>"
                           if c is False
                           else "<span class='bdg n'>观察中</span>"))
            return ("<tr><td>%s</td><td>%s</td><td><span class='bdg %s'>"
                    "%s</span></td>"
                    "<td><a href='/kg?c=%s'>%s %s</a></td>"
                    "<td>%s</td><td class='dim' style='font-size:12px'>%s"
                    "</td><td>%s</td></tr>"
                    % (x.get("d"), e(x.get("bk") or ""),
                       "y" if act == "换仓" else "n", e(act or ""),
                       e(x.get("new_code") or ""), e(x.get("new_code") or ""),
                       e(x.get("new_name") or ""), detail, after, badge))

        dtrs = "".join(_drow(x) for x in (dcs.get("rows") or [])[:30]) or \
            ("<tr><td colspan=7 class='dim'>暂无取舍决策——每账仓位 ≤100%%，"
             "满仓来新票才触发换仓/放弃</td></tr>")
        tip_html = ("".join("<div style='margin-top:6px'>⚠ %s</div>"
                            % e(t) for t in tips)
                    if tips else "")
        sec_dec = ("<div class='sec'><h2>⚔️ 取舍决策 · 事后观察（仓位固定"
                   "→有取有舍→评对错→供进化）</h2>%s%s"
                   "<table class='sticky' style='margin-top:10px'>"
                   "<tr><th>日期</th><th>账本</th><th>动作</th><th>新票</th>"
                   "<th>取舍</th><th>事后对照</th><th>对错</th></tr>%s"
                   "</table>"
                   "<div class='dim' style='font-size:12.5px;margin-top:6px'>"
                   "每本账一个固定资金池（总仓位 ≤100%%，单票 ≤100%%）：满仓"
                   "来新票时——<b>激进</b>挤掉最弱持仓（浮亏最深）换仓；"
                   "<b>保守</b>不挤仓放弃新票；<b>中立</b>只挤「过半时间窗"
                   "且浮亏」者。事后自动对照：换仓对=新票实际收益＞被挤票"
                   "假想收益；放弃对=新票假想收益≤0。正确率连续偏低会给"
                   "出进化方向提示（≥5 样本才评）。</div>%s</div>"
                   % (dcards, tip_html, dtrs, ""))
        # K88 预演六本账：预演票喂独立账，vs_real=预演质量用钱度量
        evb = {}
        try:
            with open(os.path.join(REPORTS_DIR,
                                   "trader_sim_simbooks_eval.json"),
                      encoding="utf-8") as f:
                evb = json.load(f)
        except Exception:
            pass
        sec_preset = ""
        if evb:
            vs = evb.get("vs_real") or {}
            vb = vs.get("books") or {}

            def _bc(bk, key):
                x = (vb.get(bk) or {}).get(key) or 0.0
                return ("<td class='%s'>%+.2f</td>"
                        % ("up" if x >= 0 else "dn", x))
            btrs = "".join(
                "<tr><td>%s</td><td>%d</td>%s%s%s</tr>"
                % (e(bk), (vb.get(bk) or {}).get("n") or 0,
                   _bc(bk, "sim"), _bc(bk, "real"), _bc(bk, "diff"))
                for bk in BOOKS)
            vd = vs.get("verdict") or {}
            _so = "；".join(vs.get("sim_only") or [])
            _ro = "；".join(vs.get("real_only") or [])
            sec_preset = (
                "<div class='sec'><h2>🎬 预演六本账 · vs 真实账（预演质量"
                "的真金白银度量）</h2>"
                "<div class='dim' style='font-size:12.5px;margin:4px 0 8px'>"
                "预演票（模拟投委会留痕）喂独立六账：同样操盘规则、独立"
                "文件对（trader_sim_simbooks*.json），与真实账零干扰；预演"
                "早真会一步建仓——同票贡献差=领先一步的价值；裁决一致率低"
                "=预演与真会漂移大，喂账成绩仅供参考。数据 %s</div>"
                "<table class='sticky'><tr><th>账本</th><th>同票数</th>"
                "<th>预演均贡献</th><th>真实均贡献</th><th>差值</th></tr>%s"
                "</table>"
                "<div class='dim' style='font-size:12.5px;margin-top:6px'>"
                "预演票 %d · 真实票 %d · 同票 %d · 裁决一致率 %.0f%%（%d/%d）"
                "%s%s · 预演待入场 %d 票</div></div>"
                % (e(evb.get("ts") or "—"), btrs,
                   vs.get("n_sim") or 0, vs.get("n_real") or 0,
                   vs.get("n_both") or 0, vd.get("pct") or 0.0,
                   vd.get("agree") or 0, vd.get("n") or 0,
                   (" ｜预演独接：%s" % e(_so)) if _so else "",
                   (" ｜真实独接：%s" % e(_ro)) if _ro else "",
                   len(evb.get("pending") or {})))
        content = ("<h1>♠ 三操盘手模拟锦标赛 · 六本账</h1>"
                   "<div class='dim' style='margin:2px 0 10px'>K68 影子"
                   "模拟层：选股策略出票（来源标签全链贯穿）→ 分析师团队"
                   "六视角辩论 → 激进/保守/中立 × 积极/保守 六本账 → 真实"
                   "日K买卖点 → 战绩评价（贡献度+基准α）</div>"
                   "%s%s%s%s%s%s%s%s%s"
                   % (cards, tbl_sid, sec_dec, sec_preset, sec_sim, sec_p,
                      tbl, tbl_pos, method))
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>操盘手锦标赛 · 六本账</title>"
                + self._COCKPIT_CSS + self._KG_CSS + "</head><body>"
                + pill_nav("sim") + content + sjs + "</body></html>")
        self._html(body)











    # ---------------- K-STOCK 模拟仓写路径（v0.2 平台化 2026-10-09） ----------------
    # 设计：portal_api 保持纯标准库——引擎调用一律 subprocess CLI（py311 绝对
    # 路径，带 pandas；与 07:05 automation 同一口径）。每次下单一个干净子进程，
    # 门户进程零 pandas / 零第二写连接 / 零共享账本状态。鉴权：端点已入
    # _MUTATION_POSTS（本机放行；局域网与脚本须 X-Portal-Token，同全站口径）。
    _PAPER_PY = r"C:\Program Files\Python311\python.exe"
    _PAPER_DIR = r"E:\老田的AI助手\k_stock\paper"
    _PAPER_QD_LOCK = threading.Lock()   # 双刀执行互斥（防连点并发落两套单）

    def _paper_run_json(self, cmd, timeout=35):
        """跑子进程并解析 stdout 尾部 JSON（CLI --json 模式单行输出）。

        🔴 中文 Windows 坑：子进程管道默认按系统 locale（GBK）编码 stdout，
        门户侧按 UTF-8 解码必花 → 强制子进程 PYTHONIOENCODING=utf-8。
        """
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
        out = (p.stdout or b"").decode("utf-8", "replace").strip()
        err = (p.stderr or b"").decode("utf-8", "replace").strip()
        if out:
            try:
                return json.loads(out.splitlines()[-1])
            except Exception:
                pass
        return {"ok": False,
                "error": err.splitlines()[-1] if err else (out[-200:] or f"exit={p.returncode}")}

    def _paper_desk(self):
        """交易台动态块：AI / 双刀最近决策产物（纯标准库 glob+json 只读）。"""
        out = {}
        d = r"E:\老田的AI助手\k_stock\data"
        try:
            qd = sorted(glob.glob(os.path.join(d, "poc_qd_*.json")))
            try:  # 与下单防重同口径：查账本（文件存在≠已执行）
                import sqlite3 as _sq3
                con = _sq3.connect(
                    "file:E:/老田的AI助手/k_stock/data/paper.db?mode=ro", uri=True)
                try:
                    out["qd_ran_today"] = bool(con.execute(
                        "SELECT COUNT(*) FROM orders "
                        "WHERE ref LIKE 'poc_qd_%' AND account_id='main' AND ts LIKE ?",
                        (time.strftime("%Y-%m-%d") + "%",)).fetchone()[0])
                finally:
                    con.close()
            except Exception:
                out["qd_ran_today"] = False
            if qd:
                f = qd[-1]
                m = {"file": os.path.basename(f),
                     "ts": time.strftime("%Y-%m-%d %H:%M",
                                         time.localtime(os.path.getmtime(f)))}
                try:
                    with open(f, encoding="utf-8") as fh:
                        items = json.load(fh)
                    m["signals"] = [{"stock": it.get("stock"),
                                     "name": (it.get("_meta") or {}).get("name"),
                                     "close": (it.get("_meta") or {}).get("close")}
                                    for it in items]
                except Exception:
                    m["signals"] = []
                out["qd_last_run"] = m
            ai = [x for x in glob.glob(os.path.join(d, "poc_*.json"))
                  if not os.path.basename(x).startswith("poc_qd_")]
            if ai:
                f = max(ai, key=os.path.getmtime)
                out["ai_last_run"] = {
                    "file": os.path.basename(f),
                    "ts": time.strftime("%Y-%m-%d %H:%M",
                                        time.localtime(os.path.getmtime(f)))}
        except Exception as e:  # desk 是增强信息，失败不拖垮 status
            out["error"] = repr(e)
        return out

    def _api_paper_hire(self, raw):
        """A3 雇佣交互（K-STOCK-042 配置面）：只改 hires.json 注册表，
        落单仍只在 07:05 automation 单写入口——门户永不直接触发交易。
        子进程走 trainee.py hire/fire（单写入口），JSON 管道同 paper 模式。"""
        try:
            rec = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            self._json({"ok": False, "error": "bad json"}, 400)
            return
        action = str(rec.get("action") or "").strip().lower()
        trader = str(rec.get("trader") or "").strip().lower()
        source = str(rec.get("source") or "").strip().lower()
        if action not in ("hire", "fire"):
            self._json({"ok": False, "error": "action 须为 hire/fire"}, 400)
            return
        if not re.match(r"^[a-z][a-z0-9_]{0,31}$", trader):
            self._json({"ok": False, "error": "trader 格式非法"}, 400)
            return
        if source not in ("qd", "qpzm", "ai_team"):
            self._json({"ok": False, "error": "source 须为 qd/qpzm/ai_team"}, 400)
            return
        cmd = [self._PAPER_PY, os.path.join(self._PAPER_DIR, "trainee.py"),
               action, trader, "--source", source, "--json"]
        if action == "hire" and rec.get("note"):
            cmd += ["--note", str(rec["note"])[:80]]
        r = self._paper_run_json(cmd, timeout=35)
        self._json(r, 200 if r.get("ok") else 409)

    def _api_paper_hires(self):
        """雇佣关系投影（纯只读：hires.json + trainees/*.json，无子进程）。"""
        base = self._PAPER_DIR
        try:
            hp = os.path.join(base, "hires.json")
            hires = []
            if os.path.exists(hp):
                with open(hp, encoding="utf-8") as f:
                    d = json.load(f)
                hires = d.get("hires", []) if isinstance(d, dict) else []
            faces = {}
            tdir = os.path.join(base, "trainees")
            for fn in sorted(glob.glob(os.path.join(tdir, "*.json"))):
                try:
                    with open(fn, encoding="utf-8") as f:
                        x = json.load(f)
                    faces[x.get("id")] = {"id": x.get("id"), "name": x.get("name"),
                                          "status": x.get("status"),
                                          "style": x.get("style")}
                except Exception:
                    continue
            for h in hires:
                f = faces.get(h.get("trader")) or {}
                h["trader_name"] = f.get("name")
                h["trader_status"] = f.get("status")
            self._json({"ok": True, "hires": hires,
                        "traders": sorted(faces.values(),
                                          key=lambda v: str(v.get("name")))})
        except Exception as e:
            self._json({"ok": False, "error": repr(e)}, 500)

    # ---------------- K-STOCK-137d S-b2 经理实体接线（纯增量，复刻 trainee 模式） ----------------

    def _manager_run_json(self, cmd, timeout=35):
        """manager.py --json 专用子进程封装（K-STOCK-137d）。
        与 _paper_run_json 的差异：manager.py hire/fire 成功输出为
        indent=1 多行 JSON 数组 [rec]、拒绝时单行 {"ok": false}（exit 2）
        ——取「整段 stdout json.loads」；拒绝 JSON 原样透传（错误消息
        直达前端，与 CLI exit 2 口径一致）。"""
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
        except Exception as e:
            return {"ok": False, "error": repr(e)}
        out = (p.stdout or b"").decode("utf-8", "replace").strip()
        err = (p.stderr or b"").decode("utf-8", "replace").strip()
        try:
            d = json.loads(out)
        except Exception:
            d = None
        if isinstance(d, dict):
            return d                     # 拒绝（exit 2）：{"ok": false, error} 原样透传
        if isinstance(d, list) and d and isinstance(d[0], dict):
            rec = dict(d[0])             # hire/fire 成功：[rec]（indent=1 多行）
            rec["ok"] = True
            return rec
        return {"ok": False,
                "error": err.splitlines()[-1] if err else (out[-200:] or f"exit={p.returncode}")}

    def _api_manager_roster(self):
        """K-STOCK-137d 经理名册投影（纯只读：managers/*.json +
        manager_hires.json + models.json 系数计价，无子进程）。"""
        base = self._PAPER_DIR
        try:
            cat = None
            try:  # 模型系数表（同 manager._hire_price 口径：基价×coef_vs_glm52）
                with open(os.path.join(base, "models.json"), encoding="utf-8") as f:
                    cat = json.load(f)
            except Exception:
                cat = None
            profs = []
            for fn in sorted(glob.glob(os.path.join(base, "managers", "*.json"))):
                try:
                    with open(fn, encoding="utf-8") as f:
                        p = json.load(f)
                except Exception:
                    continue
                try:
                    coef = next((m.get("coef_vs_glm52", 1.0)
                                 for m in (cat or {}).get("models", [])
                                 if m.get("model") == p.get("model")), None)
                    p["hire_price_cny"] = (round(p.get("hire_base_price_cny", 99) * coef)
                                           if coef is not None else None)
                except Exception:
                    p["hire_price_cny"] = None
                p["active_hires"] = []
                profs.append(p)
            hp = os.path.join(base, "manager_hires.json")
            if os.path.exists(hp):
                with open(hp, encoding="utf-8") as f:
                    d = json.load(f)
                for h in ((d.get("hires") or []) if isinstance(d, dict) else []):
                    if h.get("status") != "active":
                        continue
                    for p in profs:
                        if p.get("id") == h.get("manager"):
                            p["active_hires"].append(
                                {"source": h.get("source"),
                                 "hire_price_cny": h.get("hire_price_cny"),
                                 "hired_at": h.get("hired_at"), "note": h.get("note")})
            self._json({"ok": True, "managers": profs})
        except Exception as e:
            self._json({"ok": False, "error": repr(e)}, 500)

    def _api_manager_hire(self, raw):
        """K-STOCK-137d 雇佣经理：子进程 manager.py hire --json（单写入口）。
        exit 2 拒绝 JSON 原样透传：HTTP 200 带 ok:false（错误消息直达前端）。"""
        try:
            rec = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            self._json({"ok": False, "error": "bad json"}, 400)
            return
        mid = str(rec.get("mid") or "").strip()
        source = str(rec.get("source") or "").strip()
        if not re.match(r"^[a-z0-9_]{1,32}$", mid):
            self._json({"ok": False, "error": "mid 格式非法"}, 400)
            return
        if not re.match(r"^[A-Za-z0-9_\u4e00-\u9fff-]{1,32}$", source):
            self._json({"ok": False, "error": "source 格式非法"}, 400)
            return
        cmd = [self._PAPER_PY, os.path.join(self._PAPER_DIR, "manager.py"),
               "hire", mid, source, "--json"]
        if rec.get("note"):
            cmd += ["--note", str(rec["note"])[:200]]
        self._json(self._manager_run_json(cmd, timeout=35))

    def _api_manager_fire(self, raw):
        """K-STOCK-137d 解雇经理：子进程 manager.py fire --json（单写入口）。"""
        try:
            rec = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            self._json({"ok": False, "error": "bad json"}, 400)
            return
        mid = str(rec.get("mid") or "").strip()
        source = str(rec.get("source") or "").strip()
        if not re.match(r"^[a-z0-9_]{1,32}$", mid):
            self._json({"ok": False, "error": "mid 格式非法"}, 400)
            return
        if not re.match(r"^[A-Za-z0-9_\u4e00-\u9fff-]{1,32}$", source):
            self._json({"ok": False, "error": "source 格式非法"}, 400)
            return
        cmd = [self._PAPER_PY, os.path.join(self._PAPER_DIR, "manager.py"),
               "fire", mid, source, "--json"]
        self._json(self._manager_run_json(cmd, timeout=35))

    def _api_paper_order(self, raw):
        try:
            rec = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            self._json({"ok": False, "error": "bad json"}, 400)
            return
        mode = str(rec.get("mode") or "manual").strip()
        try:
            if mode == "double_knife":
                # 真幂等：查账本今天是否已有双刀落单（文件存在≠已执行，
                # dry-run 也产 picks 文件）；并发防重：进程内互斥锁。
                if not self._PAPER_QD_LOCK.acquire(blocking=False):
                    self._json({"ok": False,
                                "error": "双刀执行进行中，请勿重复提交"}, 409)
                    return
                try:
                    import sqlite3 as _sq3
                    _db = "E:/老田的AI助手/k_stock/data/paper.db"
                    con = _sq3.connect(f"file:{_db}?mode=ro", uri=True)
                    try:
                        n = con.execute(
                            "SELECT COUNT(*) FROM orders "
                            "WHERE ref LIKE 'poc_qd_%' AND account_id='main' AND ts LIKE ?",
                            (time.strftime("%Y-%m-%d") + "%",)).fetchone()[0]
                    finally:
                        con.close()
                    if n:
                        self._json({"ok": False,
                                    "error": "今日双刀信号已执行过（账本已有落单）"}, 409)
                        return
                    r = self._paper_run_json(
                        [self._PAPER_PY,
                         os.path.join(self._PAPER_DIR, "qd_adapter.py"),
                         "--json"], timeout=60)
                finally:
                    self._PAPER_QD_LOCK.release()
            else:
                side = str(rec.get("side") or "").upper()
                stock = str(rec.get("stock") or "").strip().upper()
                if not re.match(r"^\d{6}\.(SH|SZ|BJ)$", stock):
                    self._json({"ok": False,
                                "error": "股票代码格式：6位数字.SH/.SZ/.BJ"}, 400)
                    return
                if side == "BUY":
                    qty = rec.get("qty")
                    if not isinstance(qty, int) or qty <= 0 or qty % 100:
                        self._json({"ok": False,
                                    "error": "qty 须为 100 的整数倍股数"}, 400)
                        return
                elif side == "SELL":
                    if not rec.get("all") and not rec.get("qty"):
                        self._json({"ok": False,
                                    "error": "SELL 需要 qty 或 all=true"}, 400)
                        return
                else:
                    self._json({"ok": False, "error": "side 须为 BUY/SELL"}, 400)
                    return
                argv = ["order", "--side", side, "--stock", stock]
                if side == "SELL" and rec.get("all"):
                    argv.append("--all")
                elif rec.get("qty"):
                    argv += ["--qty", str(rec["qty"])]
                if rec.get("price"):
                    argv += ["--price", str(rec["price"])]
                if rec.get("stop_price"):
                    argv += ["--stop-price", str(rec["stop_price"])]
                r = self._paper_run_json(
                    [self._PAPER_PY, os.path.join(self._PAPER_DIR, "cli.py")] + argv)
        except subprocess.TimeoutExpired:
            self._json({"ok": False, "error": "引擎子进程执行超时"}, 504)
            return
        except Exception as e:
            self._json({"ok": False, "error": repr(e)}, 500)
            return
        self._json(r, 200 if r.get("ok") else 400)

    def _api_paper_stop(self, raw):
        try:
            rec = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            self._json({"ok": False, "error": "bad json"}, 400)
            return
        stock = str(rec.get("stock") or "").strip().upper()
        if not re.match(r"^\d{6}\.(SH|SZ|BJ)$", stock):
            self._json({"ok": False,
                        "error": "股票代码格式：6位数字.SH/.SZ/.BJ"}, 400)
            return
        if not rec.get("clear") and rec.get("stop_price") is None:
            self._json({"ok": False, "error": "需要 stop_price 或 clear=true"}, 400)
            return
        argv = ["order", "--side", "STOP", "--stock", stock]
        if rec.get("clear"):
            argv.append("--clear")
        else:
            argv += ["--stop-price", str(rec["stop_price"])]
        try:
            r = self._paper_run_json(
                [self._PAPER_PY, os.path.join(self._PAPER_DIR, "cli.py")] + argv)
        except Exception as e:
            self._json({"ok": False, "error": repr(e)}, 500)
            return
        self._json(r, 200 if r.get("ok") else 400)


    def do_POST(self):
        """v2.5：标注保存（首个 POST 端点，其余页面均为只读 GET）。"""
        path = self.path.split("?", 1)[0]
        # P0-1b：变更类 POST 鉴权闸门（本机放行；局域网须 X-Portal-Token）。
        # /feishu/event 不在 _MUTATION_POSTS，单独走验签（_api_feishu_event）。
        if path in _MUTATION_POSTS and not self._check_portal_token():
            self._json({"error": "forbidden: 缺失或不匹配的 X-Portal-Token"
                        "（本机浏览器经门户页面操作会自动携带；脚本调用请带"
                        " header X-Portal-Token，token 见 PORTAL_API_TOKEN"
                        " 或 C:\\Temp\\portal_api_token）"}, 403)
            return
        try:
            if path == "/chanlun-anno":
                # K77 缠论图手工标注增删（body 已在 _chanlun_anno 读）
                self._chanlun_anno(self.path.split("?", 1)[1]
                                   if "?" in self.path else "")
                return
            ln = int(self.headers.get("Content-Length") or 0)
            if path in {'/api/data/update-plan','/api/data/update'}:
                if ln < 0:
                    self._json({'error':'正文长度不合法'},400)
                    return
                if ln > 65536:
                    self._json({'error':'请求正文超过64KiB'},413)
                    return
                raw = self.rfile.read(ln) if ln else b''
                self._warehouse_update_request('POST',path,raw=raw)
                return
            raw = self.rfile.read(ln) if ln > 0 else b""
            if path == "/api/audit_chat":
                # K-STOCK-114e 独立安全审计 AI 对话（独立模型绑定 AUDIT_MODEL）
                self._api_audit_chat(raw)
                return
            if path == "/api/paper_order":
                # K-STOCK v0.2 模拟仓下单（manual BUY/SELL 或 double_knife 执行）
                self._api_paper_order(raw)
                return
            if path == "/api/paper_stop":
                # K-STOCK v0.2 模拟仓止损挂/解
                self._api_paper_stop(raw)
                return
            if path == "/api/paper_hire":
                # K-STOCK-042 A3 雇佣交互（配置面：hire/fire 只改注册表）
                self._api_paper_hire(raw)
                return
                return
            if path == "/api/manager_hire":
                # K-STOCK-137d S-b2 经理雇佣（子进程 manager.py hire --json）
                self._api_manager_hire(raw)
                return
            if path == "/api/manager_fire":
                # K-STOCK-137d S-b2 经理解雇（子进程 manager.py fire --json）
                self._api_manager_fire(raw)
                return
            if path == "/feishu/event":
                # K250 飞书事件回调（卡按钮点击 / url_verification 握手）
                self._api_feishu_event(raw)
                return
            if path == "/kg/doc/upload":
                # K266 M2 文档上传入口：body=文件字节，query name=文件名。
                # 后缀白名单 png/jpg/jpeg/pdf；落 inbox/docs/pending/
                # up_<时间戳>_<安全名>，由 k266_doc_ingest.py 统一摄取
                # （sha256 去重 → pypdf/OCR 文本化 → doc_texts.jsonl）。
                from urllib.parse import parse_qs as _pqu
                qs = _pqu(self.path.split("?", 1)[1]) \
                    if "?" in self.path else {}
                name = (qs.get("name") or [""])[0].strip()
                name = re.sub(r"[^\w.\-\u4e00-\u9fff]", "_", name)[:80]
                if not name:
                    self._json({"error": "缺 name 参数"}, 400)
                    return
                if not name.lower().endswith(
                        (".png", ".jpg", ".jpeg", ".pdf", ".txt", ".md")):
                    self._json({"error": "仅支持 png/jpg/jpeg/pdf/txt/md"}, 400)
                    return
                if not raw:
                    self._json({"error": "空文件"}, 400)
                    return
                pend = os.path.join(STATION, "inbox", "docs", "pending")
                os.makedirs(pend, exist_ok=True)
                fn = "up_%s_%s" % (time.strftime("%Y%m%d_%H%M%S"), name)
                with open(os.path.join(pend, fn), "wb") as f:
                    f.write(raw)
                self._json({"ok": True, "saved": fn, "size": len(raw)})
                return
            if path == "/kg/lexicon/save":
                # K263-W7 词表保存：三组可编辑（panic/hope/word_alias），
                # stop/tag_words 保持现值（页面只读）；原子写 lexicon.json，
                # guba_sentiment mtime 懒加载热生效
                if QBT_DIR not in sys.path:
                    sys.path.insert(0, QBT_DIR)
                import guba_sentiment as _gs
                _gs._apply_lexicon(force=True)
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    self._json({"error": "bad json"}, 400)
                    return
                panic = sorted({str(w).strip() for w in rec.get("panic", [])
                                if str(w).strip()})
                hope = sorted({str(w).strip() for w in rec.get("hope", [])
                               if str(w).strip()})
                # stop 可选：不传保持现值；传了则全量替换（空表拒绝）
                stop = sorted({str(w).strip() for w in rec.get("stop", _gs.STOP)
                               if str(w).strip()})
                alias = {}
                for k, v in (rec.get("word_alias") or {}).items():
                    k, v = str(k).strip(), str(v).strip()
                    if k and v and k != v:
                        alias[k] = v
                if not panic or not hope or not stop:
                    self._json({"error": "情绪词/停用词表不可为空"}, 400)
                    return
                out = {"stop": stop, "panic": panic, "hope": hope,
                       "tag_words": _gs.TAG_WORDS, "word_alias": alias}
                fp = _gs._LEX_FP
                tmp = fp + ".tmp"
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(out, f, ensure_ascii=False, indent=1)
                os.replace(tmp, fp)
                self._json({"ok": True, "panic": len(panic),
                            "hope": len(hope), "alias": len(alias),
                            "stop": len(stop)})
                return
            if path == "/api/chanlun/label/save":
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    # 豁免（C2批1）：显式 400 回客户端，非静默吞错；不落盘
                    # 防止攻击者用畸形 body 刷错误日志
                    self._json({"error": "bad json"}, 400)
                    return
                self._json(_kg_label_save(rec))
            elif path == "/api/report_push":
                # K244 分析师报告一键推飞书（生成 + push_feishu）
                self._api_report_push(self.path.split("?", 1)[1]
                                      if "?" in self.path else "")
            elif path == "/api/sub_toggle":
                # K245 订阅启用/停用
                self._api_sub_toggle(self.path.split("?", 1)[1]
                                     if "?" in self.path else "")
            elif path == "/api/sub_del":
                # K245 删除订阅
                self._api_sub_del(self.path.split("?", 1)[1]
                                  if "?" in self.path else "")
            elif path == "/api/sub_test":
                # K245 试推某订阅（force 忽略开关）
                self._api_sub_test(self.path.split("?", 1)[1]
                                   if "?" in self.path else "")
            elif path == "/api/recv_add":
                # K245 添加/更新接收人
                self._api_recv_add(self.path.split("?", 1)[1]
                                   if "?" in self.path else "")
            elif path == "/kg/canvas_edit":
                # K231 画布编辑器写入（op=save 建实体/改属性 | op=link 连边）
                # 白名单 + upsert 幂等 + canvas_edit_log.jsonl 审计在数据层
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    # 豁免（C2批1）：显式 400 回客户端，非静默吞错；不落盘
                    # 防止攻击者用畸形 body 刷错误日志
                    self._json({"error": "bad json"}, 400)
                    return
                if QBT_DIR not in sys.path:
                    sys.path.insert(0, QBT_DIR)
                import kg_portal
                if rec.get("op") == "save":
                    self._json(kg_portal.canvas_save(
                        rec.get("label"), rec.get("key"),
                        rec.get("props")))
                elif rec.get("op") == "link":
                    self._json(kg_portal.canvas_link(
                        rec.get("src"), rec.get("dst"),
                        rec.get("rel"), rec.get("props")))
                else:
                    self._json({"error": "未知 op"}, 400)
            elif path == "/api/chanlun/card/feedback":
                # K29 推演卡三键校对反馈（对/偏/错）
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    # 豁免（C2批1）：显式 400 回客户端，非静默吞错；不落盘
                    # 防止攻击者用畸形 body 刷错误日志
                    self._json({"error": "bad json"}, 400)
                    return
                self._json(_kg_card_fb_save(rec))
            elif path == "/api/chanlun/pool/push":
                # 工作台手动推池：分析台检出信号 → 缠论信号池（source=manual）
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    # 豁免（C2批1）：显式 400 回客户端，非静默吞错；不落盘
                    # 防止攻击者用畸形 body 刷错误日志
                    self._json({"error": "bad json"}, 400)
                    return
                self._json(_chanlun_pool_push(rec))
            elif path == "/api/portfolio_set":
                # K45 策略卡「模拟建仓」：capital/start_date 即时生效
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    # 豁免（C2批1）：显式 400 回客户端，非静默吞错；不落盘
                    # 防止攻击者用畸形 body 刷错误日志
                    self._json({"error": "bad json"}, 400)
                    return
                self._json(_portfolio_set(rec))
            elif path == "/api/portfolio_undo":
                # K46 撤销上一笔配置变更（弹栈恢复）
                self._json(_portfolio_undo())
            elif path == "/api/portfolio_close":
                # K50-D 手动平仓（单只/清仓）
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    # 豁免（C2批1）：显式 400 回客户端，非静默吞错；不落盘
                    # 防止攻击者用畸形 body 刷错误日志
                    self._json({"error": "bad json"}, 400)
                    return
                self._json(_portfolio_close(rec))
            elif path == "/api/portfolio_adjust":
                # K51-A 手动加/减仓
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    # 豁免（C2批1）：显式 400 回客户端，非静默吞错；不落盘
                    # 防止攻击者用畸形 body 刷错误日志
                    self._json({"error": "bad json"}, 400)
                    return
                self._json(_portfolio_adjust(rec))
            elif path == "/api/portfolio_open":
                # K51-D 手动建仓
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    # 豁免（C2批1）：显式 400 回客户端，非静默吞错；不落盘
                    # 防止攻击者用畸形 body 刷错误日志
                    self._json({"error": "bad json"}, 400)
                    return
                self._json(_portfolio_open(rec))
            elif path == "/api/council/confirm":
                # K64d 投委会预警半自动执行：人工确认→平仓/减仓，忽略→出队
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    # 豁免（C2批1）：显式 400 回客户端，非静默吞错；不落盘
                    # 防止攻击者用畸形 body 刷错误日志
                    self._json({"error": "bad json"}, 400)
                    return
                self._json(_council_confirm(rec))
            elif path == "/api/council/simrun":
                # K69 模拟投委会预演：后台启动/轮询（start/status）
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    # 豁免（C2批1）：显式 400 回客户端，非静默吞错；不落盘
                    # 防止攻击者用畸形 body 刷错误日志
                    self._json({"error": "bad json"}, 400)
                    return
                self._json(_council_simrun(rec))
            elif path == "/api/debate/say":
                # K74 老板插话：主持人+三操盘手 LLM 回应，追加留痕
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    # 豁免（C2批1）：显式 400 回客户端，非静默吞错；不落盘
                    # 防止攻击者用畸形 body 刷错误日志
                    self._json({"error": "bad json"}, 400)
                    return
                self._json(_debate_say(rec))
            elif path == "/api/portfolio_subscribe":
                # K-STOCK-122 M3 订阅/退订：{strategy, action}
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    self._json({"error": "bad json"}, 400)
                    return
                try:
                    import subscriptions as _sub
                    self._json(_sub.subscribe(
                        str(rec.get("strategy") or ""),
                        str(rec.get("action") or "toggle")))
                except Exception as _e:
                    self._json({"ok": False, "error": repr(_e)}, 500)
            elif path == "/api/portfolio_hire":
                # K-STOCK-122 M4 分支①：组合聘交易员 {trader}
                # 真雇佣走 k_stock/paper/portfolio.py（hires.json 落
                # source=<pf_id> 条目 + live/training 校验）；再绑定组合侧。
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    self._json({"error": "bad json"}, 400)
                    return
                try:
                    import subprocess
                    _pfpy = (r"C:\Program Files\Python311\python.exe"
                             if os.path.exists(r"C:\Program Files"
                                               r"\Python311\python.exe")
                             else sys.executable)
                    _script = os.path.join(
                        os.path.dirname(os.path.abspath(__file__)),
                        os.pardir, "k_stock", "paper", "portfolio.py")
                    r = subprocess.run(
                        [_pfpy, "-X", "utf8", _script,
                         "hire", "--pf", "pf_001",
                         "--trader", str(rec.get("trader") or ""), "--json"],
                        capture_output=True, timeout=60)
                    out = r.stdout.decode("utf-8", "replace").strip()
                    data = json.loads(out.splitlines()[-1]) if out else {}
                    if data.get("ok"):
                        import subscriptions as _sub
                        _sub.bind_trader(str(rec.get("trader") or ""))
                        self._json({"ok": True, "note": data.get("note"),
                                    "hired": True})
                    else:
                        self._json({"ok": False,
                                    "error": data.get("error") or out[-200:]
                                    or "hire 失败"})
                except Exception as _e:
                    self._json({"ok": False, "error": repr(_e)}, 500)
            elif path == "/api/trader_fire":
                # K-STOCK-122 投研团队：组合解聘交易员 {trader}
                # 真解雇走 trainee.py fire（hires.json 行 status=ended）；
                # 组合侧解绑同步执行。
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    self._json({"error": "bad json"}, 400)
                    return
                _tid = str(rec.get("trader") or "")
                try:
                    import subprocess
                    _pfpy = (r"C:\Program Files\Python311\python.exe"
                             if os.path.exists(r"C:\Program Files"
                                               r"\Python311\python.exe")
                             else sys.executable)
                    _script = os.path.join(
                        os.path.dirname(os.path.abspath(__file__)),
                        os.pardir, "k_stock", "paper", "trainee.py")
                    r = subprocess.run(
                        [_pfpy, "-X", "utf8", _script,
                         "fire", _tid, "--source", "pf_001", "--json"],
                        capture_output=True, timeout=60)
                    out = r.stdout.decode("utf-8", "replace").strip()
                    data = json.loads(out.splitlines()[-1]) if out else {}
                    if data.get("ok"):
                        # K-STOCK-122 审查修复：仅当解聘的正是当前绑定
                        # 交易员时才解绑——此前无条件 unbind，解聘旁观者
                        # （如 taishan）会误清 chaser 的组合绑定。
                        import subscriptions as _sub
                        _pf_now = {}
                        for _p in (_sub.portfolios().get("portfolios") or []):
                            if _p.get("id") == "pf_001":
                                _pf_now = _p
                                break
                        if (_pf_now.get("trader") or "") == _tid:
                            _sub.unbind_trader()
                        self._json({"ok": True, "fired": _tid})
                    else:
                        self._json({"ok": False,
                                    "error": data.get("error") or out[-200:]
                                    or "fire 失败"})
                except Exception as _e:
                    self._json({"ok": False, "error": repr(_e)}, 500)
            elif path == "/api/batch-backtest":
                # K255 勾选股票自定义批次回测：写批次 JSONL → 引擎子进程异步跑
                try:
                    rec = json.loads(raw.decode("utf-8"))
                except Exception:
                    # 豁免（C2批1）：显式 400 回客户端，非静默吞错；不落盘
                    # 防止攻击者用畸形 body 刷错误日志
                    self._json({"error": "bad json"}, 400)
                    return
                self._json(_bt_custom_start(rec))
            else:
                self.send_error(404)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        raw = self.path
        path = raw.split("?", 1)[0]
        qs = raw.split("?", 1)[1] if "?" in raw else ""
        if path == '/api/data/daily' or path.startswith('/api/data/runs/'):
            self._warehouse_update_request('GET',path,qs)
            return
        # C1a：GET 动作端点鉴权闸门（与写端点同规则）。本机（回环直连/
        # 本机浏览器）放行；反代+局域网须 X-Portal-Token，缺失/不匹配
        # 返回 403，防止匿名 GET 触发后台子进程与飞书推送。
        if path in _ACTION_GETS and not self._check_portal_token():
            self._json({"error": "forbidden: 缺失或不匹配的 X-Portal-Token"
                        "（本机浏览器经门户页面操作直接放行；脚本调用请带"
                        " header X-Portal-Token，token 见 PORTAL_API_TOKEN"
                        " 或 C:\\Temp\\portal_api_token）"}, 403)
            return
        if path == "/":
            # 根路径引导：8091 是 API+页面后端，首页在 nginx 8080——
            # 直接访问 8091/ 曾得 404 白页误判「网络不通」，302 到真首页
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:8080/")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/api/overview":
            with _lock:
                now = time.time()
                if _cache["data"] is None or now - _cache["t"] > CACHE_TTL:
                    _cache["data"] = build_overview()
                    _cache["t"] = now
                data = _cache["data"]
            self._json(data)
        elif path == "/health":
            self._json({"ok": True, "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        elif path == "/api/audit_status":
            # M6 最小接线（2026-10-08 定位拍板 C 混合态）：审计线状态面，
            # 只读、无鉴权、全体 unverified（拍板原文，鉴权/对账/调度缓行）。
            # 数据层 quant_backtest/ai_operator/portal_status.py（docs 台账投影，
            # CLI 可自测 python -m ai_operator.portal_status）。
            import sys as _sysa
            if QBT_DIR not in _sysa.path:
                _sysa.path.insert(0, QBT_DIR)
            try:
                from ai_operator.portal_status import status as _aud
                self._json(_aud())
            except Exception as _e:  # 数据层异常也如实暴露（fail loud）
                self._json({"ok": False, "verified": False,
                            "error": repr(_e)}, 500)
        elif path == "/api/paper_hires":
            # K-STOCK-042 A3 雇佣关系投影（纯只读）
            self._api_paper_hires()
        elif path == "/api/manager_roster":
            # K-STOCK-137d S-b2 经理名册投影（纯只读：managers/*.json +
            # manager_hires.json + models.json 系数计价）
            self._api_manager_roster()
        elif path == "/api/paper_status":
            # K-STOCK 模拟仓只读投影（2026-10-09 前端规划 P2）：
            # 数据层 k_stock/paper/portal_status.py（纯标准库 sqlite ro，零 pandas），
            # CLI 自测 python k_stock/paper/portal_status.py。
            import sys as _sysp
            _ksdir = r"E:\老田的AI助手\k_stock"
            if _ksdir not in _sysp.path:
                _sysp.path.insert(0, _ksdir)
            try:
                from paper.portal_status import status as _paper
                data = _paper()
                data["desk"] = self._paper_desk()   # v0.2 交易台动态块（AI/双刀最近产物）
                try:  # K-STOCK-045e G3：US 独立账本投影（USD 原币；失败降级不拖主投影）
                    from paper.portal_status import us_status as _us
                    data["us"] = _us()
                except Exception as _e2:
                    data["us"] = {"ok": False, "error": repr(_e2)}
                try:  # 077q：选票通道（pick_bridge）投影（10-16 实弹；失败降级）
                    from paper.portal_status import pick_status as _pick
                    data["pick"] = _pick()
                except Exception as _e3:
                    data["pick"] = {"ok": False, "error": repr(_e3)}
                try:  # 077w：端3 评判 Agent（judge）投影（失败降级）
                    from paper.portal_status import judge_status as _judge
                    data["judge"] = _judge()
                except Exception as _e4:
                    data["judge"] = {"ok": False, "error": repr(_e4)}
                try:  # 077w：端1 策略源影子路由建议（strategist，失败降级）
                    from paper.portal_status import strategist_status as _strat
                    data["strategist"] = _strat()
                except Exception as _e5:
                    data["strategist"] = {"ok": False, "error": repr(_e5)}
                try:  # 077w K-STOCK-101 M3：商业页面投影（tiers+traders 战绩）
                    from paper.portal_status import market_status as _mkt
                    data["market"] = _mkt()
                except Exception as _e6:
                    data["market"] = {"ok": False, "error": repr(_e6)}
                self._json(data)
            except Exception as _e:  # 数据层异常也如实暴露（fail loud）
                self._json({"ok": False, "error": repr(_e)}, 500)
        elif path == "/api/paper_history":
            # K-STOCK v0.2 历史回看（只读）：?day=YYYY-MM-DD，缺省=最新快照日
            import sys as _sysp
            _ksdir = r"E:\老田的AI助手\k_stock"
            if _ksdir not in _sysp.path:
                _sysp.path.insert(0, _ksdir)
            try:
                from urllib.parse import parse_qs as _pqs
                _q = _pqs(qs)
                from paper.portal_status import history as _hist, snapshot_days as _days
                _days_l = _days()
                _day = (_q.get("day") or [""])[0].strip() \
                    or (_days_l[-1] if _days_l else "")
                self._json(_hist(_day))
            except Exception as _e:
                self._json({"ok": False, "error": repr(_e)}, 500)
        elif path == "/api/strategy_market":
            # K-STOCK-122 M1 策略市场：12 策略卡全量（strategies.json 直读）
            try:
                import strategy_market as _sm
                self._json(_sm.market())
            except Exception as _e:
                self._json({"ok": False, "error": repr(_e)}, 500)
        elif path == "/api/market_performance":
            # K-STOCK-132：绩效账本只读投影；缺失/损坏明确返回，不降级成空数据。
            try:
                import market_performance as _mp
                self._json(_mp.project())
            except FileNotFoundError as _e:
                self._json({"ok": False, "error_code": "ledger_unavailable",
                            "error": str(_e)}, 503)
            except ValueError as _e:
                self._json({"ok": False, "error_code": "ledger_invalid",
                            "error": str(_e)}, 500)
            except Exception as _e:
                self._json({"ok": False, "error_code": "ledger_error",
                            "error": repr(_e)}, 500)
        elif path == "/api/portfolios":
            # K-STOCK-122 M3 组合列表（订阅集，纯只读）
            try:
                import subscriptions as _sub
                self._json(_sub.portfolios())
            except Exception as _e:
                self._json({"ok": False, "error": repr(_e)}, 500)
        elif path == "/api/portfolio_status":
            # K-STOCK-122 模拟仓侧「我的组合」投影（只读）：
            # 组合策略集 + 在职交易员 + 其 pending/持仓 + 最新净值
            try:
                import json as _j2
                import sqlite3 as _sq2
                subs_p = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "data", "strategy_subscriptions.json")
                hires_p = os.path.join(
                    r"E:\老田的AI助手\k_stock\paper", "hires.json")
                pf = {}
                if os.path.exists(subs_p):
                    with open(subs_p, encoding="utf-8") as _f:
                        for _p in (_j2.load(_f).get("portfolios") or []):
                            if _p.get("id") == "pf_001":
                                pf = _p
                                break
                traders = []
                if os.path.exists(hires_p):
                    with open(hires_p, encoding="utf-8") as _f:
                        traders = [h.get("trader") for h in
                                   (_j2.load(_f).get("hires") or [])
                                   if h.get("source") == "pf_001"
                                   and h.get("status") == "active"]
                con = _sq2.connect(r"E:\老田的AI助手\k_stock\data\paper.db")
                con.row_factory = _sq2.Row
                pend, poss, eq = [], [], None
                try:
                    for t in traders:
                        pend += [dict(r) for r in con.execute(
                            "SELECT stock,side,qty,plan_date,status,ref FROM "
                            "orders WHERE account_id=? AND ref LIKE "
                            "'poc_pf_001%' AND status='pending' "
                            "ORDER BY id DESC LIMIT 20", (t,))]
                        poss += [dict(r) for r in con.execute(
                            "SELECT stock,qty,avg_cost FROM positions "
                            "WHERE account_id=?", (t,))]
                        r = con.execute(
                            "SELECT date,total FROM equity_marks WHERE "
                            "account_id=? ORDER BY date DESC LIMIT 1",
                            (t,)).fetchone()
                        if r:
                            eq = {"account": t, "date": r["date"],
                                  "total": r["total"]}
                finally:
                    con.close()
                self._json({"ok": True, "portfolio": {
                    "id": pf.get("id"), "name": pf.get("name"),
                    "strategies": pf.get("strategies") or [],
                    "trader": pf.get("trader"), "note": pf.get("note")},
                    "traders": traders, "pending": pend,
                    "positions": poss, "equity": eq})
            except Exception as _e:
                self._json({"ok": False, "error": repr(_e)}, 500)
        elif path == "/api/trader_roster":
            # K-STOCK-122 投研团队：操盘手团队名册（trainee.py board 成绩
            # + hires.json 在职标记 + 经理等级标签），纯只读。
            try:
                import json as _j3
                hires_p = os.path.join(
                    r"E:\老田的AI助手\k_stock\paper", "hires.json")
                r = self._paper_run_json(
                    [self._PAPER_PY,
                     os.path.join(self._PAPER_DIR, "trainee.py"),
                     "board", "--json"], timeout=90)
                active = set()
                if os.path.exists(hires_p):
                    with open(hires_p, encoding="utf-8") as _f:
                        active = {h.get("trader") for h in
                                  (_j3.load(_f).get("hires") or [])
                                  if h.get("source") == "pf_001"
                                  and h.get("status") == "active"}
                _GRADE = {"live": "正式", "training": "实习",
                          "design": "储备", "ops": "运营台"}
                rows = []
                for t in (r.get("rows") or []):
                    t = dict(t)
                    t["grade"] = _GRADE.get(t.get("status"), "储备")
                    t["hired_pf"] = t.get("id") in active
                    rows.append(t)
                rows.sort(key=lambda x: (not x.get("hired_pf"),
                                         x.get("status") != "live",
                                         -(x.get("cum_pct") or 0)))
                self._json({"ok": True, "rows": rows})
            except Exception as _e:
                self._json({"ok": False, "error": repr(_e)}, 500)
        elif path == "/api/trader_detail":
            # K-STOCK-122 投研团队：交易员下钻——成绩行 + 净值曲线 + 近期成交
            from urllib.parse import parse_qs as _pqs4
            _tid = (_pqs4(qs).get("id") or [""])[0].strip()
            try:
                import json as _j4
                import sqlite3 as _sq3
                r = self._paper_run_json(
                    [self._PAPER_PY,
                     os.path.join(self._PAPER_DIR, "trainee.py"),
                     "board", "--json"], timeout=90)
                row = next((x for x in (r.get("rows") or [])
                            if x.get("id") == _tid), None)
                if not row:
                    self._json({"ok": False, "error": "no trader: %s" % _tid})
                    return
                con = _sq3.connect(
                    r"E:\老田的AI助手\k_stock\data\paper.db")
                con.row_factory = _sq3.Row
                eq, recent, n_filled = [], [], 0
                try:
                    eq = [dict(x) for x in con.execute(
                        "SELECT date,total FROM equity_marks WHERE "
                        "account_id=? ORDER BY date DESC LIMIT 15",
                        (_tid,))][::-1]
                    recent = [dict(x) for x in con.execute(
                        "SELECT stock,side,qty,filled_price,fill_date "
                        "FROM orders WHERE account_id=? AND "
                        "status='filled' ORDER BY id DESC LIMIT 8",
                        (_tid,))]
                    n_filled = con.execute(
                        "SELECT COUNT(*) FROM orders WHERE account_id=? "
                        "AND status='filled'", (_tid,)).fetchone()[0]
                finally:
                    con.close()
                self._json({"ok": True, "trader": row, "equity": eq,
                            "recent": recent, "filled_total": n_filled})
            except Exception as _e:
                self._json({"ok": False, "error": repr(_e)}, 500)
        elif path == "/api/review_minutes":
            # K-STOCK-122 M5 复盘会纪要投影（只读）：?date=YYYY-MM-DD 缺省最新
            try:
                from urllib.parse import parse_qs as _pqs3
                _d = (_pqs3(qs).get("date") or [""])[0].strip()
                import glob as _glob
                mdir = os.path.join(
                    r"E:\老田的AI助手\k_stock\data", "review_minutes")
                fp = (os.path.join(mdir, "%s.json" % _d) if _d
                      else max(_glob.glob(os.path.join(mdir, "*.json")),
                               default=None))
                if not fp or not os.path.exists(fp):
                    self._json({"ok": False, "error": "无纪要文件"})
                    return
                with open(fp, encoding="utf-8") as _f:
                    self._json(json.load(_f))
            except Exception as _e:
                self._json({"ok": False, "error": repr(_e)}, 500)
        elif path == "/api/strategy_detail":
            # K-STOCK-122 M2 下钻一层：?id=xxx 静态档案+动态命中样本
            from urllib.parse import parse_qs as _pqs2
            _sid = (_pqs2(qs).get("id") or [""])[0].strip()
            try:
                import strategy_market as _sm
                self._json(_sm.detail(_sid),
                           200 if _sid else 400)
            except Exception as _e:
                self._json({"ok": False, "error": repr(_e)}, 500)
        elif path == "/api/trainee_board":
            # K-STOCK v0.4 交易员成绩单（A1）：trainee.scoreboard 需 pandas（行情湖估值），
            # 门户保持纯标准库 → 子进程 CLI --json（同 _paper_run_json 模式，UTF-8 管道）。
            try:
                r = self._paper_run_json(
                    [self._PAPER_PY, os.path.join(self._PAPER_DIR, "trainee.py"),
                     "board", "--json"], timeout=90)
                self._json(r)
            except Exception as _e:
                self._json({"ok": False, "error": repr(_e)}, 500)
        elif path.startswith("/static/"):
            # KG-V1 静态资源（白名单）：本地 echarts / 全站 tokens.css
            fn = path.split("/", 2)[2]
            _CT = {"echarts.min.js": "application/javascript",
                   "g6.min.js": "application/javascript",
                   "nav.js": "application/javascript",
                   "tokens.css": "text/css",
                   "shortscreen.js": "application/javascript",
                   "cainiao.js": "application/javascript",
                   "china_geo.json": "application/json"}
            if fn in _CT:
                fp = os.path.join(ROOT, "static", fn)
                if os.path.exists(fp):
                    self.send_response(200)
                    self.send_header(
                        "Content-Type",
                        _CT[fn] + "; charset=utf-8")
                    self.send_header("Cache-Control", "max-age=86400")
                    self.send_header("Content-Length",
                                     str(os.path.getsize(fp)))
                    self.end_headers()
                    with open(fp, "rb") as f:
                        self.wfile.write(f.read())
                    return
            self.send_error(404)
        elif path == "/api/brief":
            # 首页四板块卡的动态小字（盘前倾向/机会数/今日情报数）
            self._json(build_brief())
        elif path == "/api/strategies":
            # 策略资产注册表（K36）：首页组合区/策略中心同源
            self._json(strategies_data())
        elif path == "/api/alerts":
            # K39 今日预警速览：各策略产线最新预警值聚合（量化域首页）
            self._json(build_alerts())
        elif path == "/api/screen":
            # K44 数据大屏：策略×市场×行业×账本成绩 聚合
            self._json(build_screen())
        elif path == "/api/portfolio_csv":
            # K51-B 交易明细导出 CSV（?sid= 单账户，缺省全部）
            from urllib.parse import parse_qs as _pqc
            sid = (_pqc(qs).get("sid") or [""])[0]
            body = _pf_csv(sid)
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition",
                             'attachment; filename="portfolio_trades_%s.csv"'
                             % (sid or "all"))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/portfolio_market":
            # K52-D 手动建仓标的搜索（?q= code/name 关键词）
            self._json(_portfolio_market(qs))
        elif path == "/api/chanlun/kline":
            # 缠论 P1 数据底座：多周期 K 线 JSON（1d/60m/30m）
            from urllib.parse import parse_qs as _pqk
            qp = _pqk(qs)
            import sys as _sysk
            if QBT_DIR not in _sysk.path:
                _sysk.path.insert(0, QBT_DIR)
            from chanlun_data import load_kline
            self._json(load_kline(
                (qp.get("code") or [""])[0],
                (qp.get("period") or ["1d"])[0],
                (qp.get("limit") or ["300"])[0]))
        elif path == "/api/chanlun/structure":
            # 缠论 P2+P3：包含处理+分型+笔/线段/中枢+三类买卖点（复用 P1 数据底座）
            from urllib.parse import parse_qs as _pqst
            qp2 = _pqst(qs)
            import sys as _sysst
            if QBT_DIR not in _sysst.path:
                _sysst.path.insert(0, QBT_DIR)
            from chanlun_data import load_kline as _lk
            from chanlun_signal import analyze as _az
            r2 = _lk((qp2.get("code") or [""])[0],
                     (qp2.get("period") or ["1d"])[0],
                     (qp2.get("limit") or ["500"])[0])
            if not r2.get("ok"):
                self._json(r2)
            else:
                st = _az(r2["bars"])
                r2["fractals"] = st["fractals"]
                r2["merged_n"] = st["merged_n"]
                r2["bis"] = st["bis"]
                r2["segments"] = st["segments"]
                r2["pivots"] = st["pivots"]
                r2["signals"] = st["signals"]
                # P7：日线信号 best-effort 附 60m 次级别共振影子标注
                # （缺数据 mt=None；60m/30m 请求不做下钻）
                if r2.get("period") == "1d" and st["signals"]:
                    try:
                        from chanlun_mtf import confluence as _cf
                        rs = _lk(r2.get("code"), "60m", 1000)
                        sub_bars = rs["bars"] if rs.get("ok") else []
                        # P8-b：大盘环境（CN 上证 / CRYPTO BTC；US/HK 无指数）
                        from chanlun_flow import REGIME_INDEX as _RIDX, \
                            regime_at as _reg
                        idx_bars = []
                        if r2.get("market") in _RIDX:
                            ri = _lk(_RIDX[r2["market"]], "1d", 500)
                            idx_bars = ri["bars"] if ri.get("ok") else []
                        for _s in st["signals"]:
                            _s["mt"], _s["mtv"] = _cf(
                                st["bis"], _s, sub_bars)
                            if idx_bars:
                                _s["reg"], _s["reg_ev"] = _reg(
                                    r2["market"], _s["dt"], idx_bars)
                    except Exception as _e:
                        r2["mt_warn"] = str(_e)[:100]
                self._json(r2)
        elif path == "/api/chanlun/backtest":
            # 缠论 P4：三类买卖点事件统计 + 策略回测（理论价口径）
            from urllib.parse import parse_qs as _pqst
            qp3 = _pqst(qs)
            import sys as _sysst
            if QBT_DIR not in _sysst.path:
                _sysst.path.insert(0, QBT_DIR)
            from chanlun_data import load_kline as _lk3
            from chanlun_backtest import analyze as _azbt
            r3 = _lk3((qp3.get("code") or [""])[0],
                      (qp3.get("period") or ["1d"])[0],
                      (qp3.get("limit") or ["300"])[0])
            if not r3.get("ok"):
                self._json(r3)
            else:
                st3 = _azbt(r3["bars"])
                r3.pop("bars", None)
                r3.pop("fractals", None)
                r3.pop("bis", None)
                r3.pop("segments", None)
                r3.pop("pivots", None)
                r3.pop("signals", None)
                r3["n_signals"] = len(st3["signals"])
                r3["events"] = st3["events"]
                r3["by_type"] = st3["by_type"]
                r3["stats"] = st3["stats"]
                self._json(r3)
        elif path == "/kg":
            # M3-W2 情报探索页（HTML，服务端渲染：概览/个股全景/游资归因）
            self._kg_page(qs)
        elif path == "/kg/company":
            # KG-O 公司本体全景页（中心辐射六扇区 + 直方图 + 关系统计）
            self._kg_company_page(qs)
        elif path == "/kg/map":
            # M3-W3 游资地图页（全景/共现/动向流 + 席位风格画像）
            self._kg_map_page(qs)
        elif path == "/kg/shadow":
            # M3-W4 影子面板页（真假信号按日对照，口径透明）
            self._kg_shadow_page(qs)
        elif path == "/kg/flow":
            # SF 板块异动工单看板（六步数据流第⑥步，读 reports 只读）
            self._kg_flow_page()
        elif path == "/kg/label":
            # v2.5 画图标注工具（半自动：系统预填算法结构，人只改错处）
            self._kg_label_page()
        elif path == "/kg/card":
            # K29 推演卡网页版 + 三键校对反馈（对/偏/错）
            self._kg_card_page()
        elif path == "/kg/canvas":
            # K226 知识画布（实体搜索→径向扩散，人/地/事/物/组织五类）
            self._kg_canvas_page()
        elif path == "/kg/canvasv2":
            # K234 画布 v2（AntV G6 力导向，独立探索板块，一期方案落地）
            self._kg_canvas_v2_page()
        elif path == "/kg/ask":
            # K240 图原生问答页
            self._kg_ask_page()
        elif path == "/kg/battle":
            # K262 作战地图（时序画布+批量体检）
            self._kg_battle_page()
        elif path == "/kg/doc":
            # K266 M4 文档智能中心（词云三跳+上传）
            self._kg_doc_page(qs)
        elif path == "/kg/lexicon":
            # K263-W7 股吧词表维护（情绪词/同义归并 → lexicon.json 热生效）
            self._kg_lexicon_page()
        elif path == "/kg/events":
            # K266 事件图谱查询面板（态势感知 + 市场Tab + G6 + 下钻原文）
            self._kg_events_page(qs)
        elif path == "/kg/events/situ" or path.startswith(
                ("/kg/events/graph", "/kg/events/list",
                 "/kg/events/search", "/kg/events/doc/")):
            # K266 事件图谱 JSON 端点（数据层 kg_events_portal.py，纯查询）
            from urllib.parse import parse_qs as _pqev, unquote as _uqev
            import sys as _syev
            if QBT_DIR not in _syev.path:
                _syev.path.insert(0, QBT_DIR)
            import kg_events_portal as _KE
            if path == "/kg/events/situ":
                self._json(_KE.api("situ"))
            elif path.startswith("/kg/events/doc/"):
                self._json(_KE.api("doc", _uqev(path[len("/kg/events/doc/"):])))
            else:
                self._json(_KE.api(path.split("/")[3], None, {
                    k: v[0] for k, v in _pqev(qs).items() if v}))
        elif path == "/static/g6.min.js":
            # K234 G6 本地静态（零 CDN 依赖）
            import os as _os6
            fp = _os6.path.join(_os6.path.dirname(
                _os6.path.abspath(__file__)), "static", "g6.min.js")
            try:
                with open(fp, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type",
                                 "application/javascript; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except OSError:
                self._json({"error": "static not found"}, 404)
        elif path == "/bridge":
            # K56-B1 投研驾驶舱：顶板五仪（数据/运行/预警/图台账/账户）
            self._bridge_page(qs)
        elif path == "/bridge/act":
            # K56-B3 指挥台两步确认页（白名单动作：三动作/同步/扫描）
            self._bridge_act_page(qs)
        elif path == "/council":
            # K55-C4 投委会留痕页（三张 jsonl 浏览+检索+影子统计/转正门槛）
            self._council_page(qs)
        elif path == "/sim":
            # K68 三操盘手模拟锦标赛（六本账战绩榜，只读 reports）
            self._sim_page(qs)
        elif path == "/debate":
            # K74 模拟辩论室：五幕辩论回放 + 老板插话
            self._debate_page(qs)
        elif path == "/api/chanlun/label/load":
            # v2.5 读已存标注（同 code+period 最新一条，供回填续标）
            from urllib.parse import parse_qs as _pql
            qp = _pql(qs)
            code = (qp.get("code") or [""])[0]
            period = (qp.get("period") or [""])[0]
            hit = None
            try:
                with open(_LABELS_FP, encoding="utf-8") as f:
                    for line in f:
                        try:
                            r = json.loads(line)
                        except Exception:
                            continue
                        if r.get("code") == code and r.get("period") == period:
                            hit = r          # 取最后一条
            except OSError:
                pass
            self._json(hit or {"none": True})
        elif path.startswith("/kg/stock/") and "format=json" not in qs:
            # K-STOCK-051 统一股票详情卡（046 §5.1 五区制，唯一样式）：
            # 浏览器默认 HTML；?format=json 走下方 JSON 端点保兼容既有调用方
            from urllib.parse import unquote as _uqsc
            _parts_sc = path.split("/", 3)
            _code_sc = _uqsc(_parts_sc[3]) if len(_parts_sc) > 3 and _parts_sc[3] else ""
            if not _code_sc:
                self._json({"ok": False, "error": "missing code"})
            else:
                try:
                    import stock_card as _stock_card
                    self._html(_stock_card.render_card(_code_sc))
                except Exception as _esc_err:  # noqa: BLE001
                    self._json({"ok": False, "error": f"stock_card: {_esc_err}"})
        elif path in ("/kg/stats", "/kg/sources", "/kg/overview",
                      "/kg/ontology") or path.startswith(
                ("/kg/stock/", "/kg/hotmoney/", "/kg/trace/",
                 "/kg/objectview/",                   # L2 扇区体检 JSON
                 "/kg/company360/", "/kg/csearch/", "/kg/cexpand/", "/kg/chist/",
                 "/kg/cask/",
                 "/kg/timeline/", "/kg/batch/",   # K262 作战地图
                 "/kg/records/",                   # K262 战绩层（四源聚合）
                 "/kg/guba/",                      # K263 股吧舆论
                 "/kg/gx/",                        # K261 L2 图分析
                 "/kg/chist/")):
            # M3-W1 知识图谱 JSON 端点（数据层 quant_backtest/kg_portal.py，纯查询不写库）
            #   /kg/stats /kg/sources /kg/overview /kg/stock/{code} /kg/hotmoney/{name} /kg/trace/{code}?days=N
            #   K226 画布：/kg/csearch/{q} /kg/cexpand/{Label|key}
            from urllib.parse import parse_qs as _pqkg, unquote as _uqkg
            import sys as _sykg
            if QBT_DIR not in _sykg.path:
                _sykg.path.insert(0, QBT_DIR)
            import kg_portal
            if path == "/kg/stats":
                self._json(kg_portal.api("stats"))
            elif path == "/kg/sources":
                # G7 来源评级（Admiralty A-F + 断流红点）
                self._json(kg_portal.api("sources"))
            elif path == "/kg/overview":
                # KG-V4 集团总览层（G6 画布数据：超节点+桥接边）
                self._json(kg_portal.api("overview"))
            else:
                parts = path.split("/", 3)   # ["", "kg", kind, arg]
                qp = _pqkg(qs)
                days = (qp.get("days") or [None])[0]
                per = (qp.get("per") or [None])[0]
                # arg 段可缺省（/kg/ontology 等无参 kind，越界即崩线程断连）
                self._json(kg_portal.api(
                    parts[2], _uqkg(parts[3]) if len(parts) > 3 else None,
                    days, per))
        elif path == "/fin":
            # L1 金融驾驶舱（任务导向：查股票/闸门/今日机会/工具）
            self._fin_page()
        elif path == "/strategies":
            # 策略中心（K36）：注册表全景记分卡 + 生命周期 + 上新 SOP
            self._strategies_page()
        elif path == "/intel":
            # L1 情报驾驶舱（今日情报流/JEV 多空/文档枢纽）
            self._intel_page()
        elif path == "/gzh":
            # 公众号草稿库（内容生产域）
            self._gzh_list()
        elif path.startswith("/gzh/view/"):
            # 草稿全文预览（gzh_writer 生成的 preview.html，白名单防穿越）
            import re as _re2
            from urllib.parse import unquote as _uq
            fn = _uq(path[len("/gzh/view/"):])
            if _re2.match(r"^[^/\\]+\.preview\.html$", fn):
                self._html_file(os.path.join(STATION, "gzh", "drafts", fn))
            else:
                self._json({"error": "bad file"}, 400)
        elif path == "/vd":
            # 短视频成片库（内容生产域）
            self._vd_list()
        elif path.startswith("/vd/file/"):
            # 成片附件（mp4/wav/srt/json，白名单防穿越）
            import re as _re2
            from urllib.parse import unquote as _uq
            rest = _uq(path[len("/vd/file/"):])
            m = _re2.match(r"^([^/\\]+)/(video\.mp4|audio\.wav|"
                           r"subs\.srt|script\.json)$", rest)
            if not m:
                self._json({"error": "bad file"}, 400)
                return
            ct = {"video.mp4": "video/mp4", "audio.wav": "audio/wav",
                  "subs.srt": "application/octet-stream; charset=utf-8",
                  "script.json": "application/json; charset=utf-8"}[m.group(2)]
            self._send_media(os.path.join(STATION, "video", "output",
                                          m.group(1), m.group(2)), ct)
        elif path == "/sa":
            # 个股分析极简页（一个输入框 → 行情+新闻+JEV+展望；
            # ?q= 预填并自动开始，供金融驾驶舱/首页直达）
            from urllib.parse import parse_qs, unquote as _uq
            q = (parse_qs(qs).get("q") or [""])[0]
            self._sa_page(_uq(q))
        elif path == "/sa/start":
            from urllib.parse import parse_qs, unquote as _uq
            q = (parse_qs(qs).get("q") or [""])[0]
            self._sa_start(_uq(q))
        elif path == "/sa/result":
            from urllib.parse import parse_qs
            jid = (parse_qs(qs).get("job") or [""])[0]
            self._sa_result(jid)
        elif path == "/bt-signals":
            # 飞书卡片「明日信号」按钮目标页：全池 4 策略持仓信号
            self._html_file(os.path.join(STATION, "quant_backtest",
                                         "reports", "signals.html"),
                            nav_key="signals")
        elif path == "/bt-positions":
            # 盘前作战计划卡「持仓跟踪」按钮目标页：纸面持仓账本
            self._html_file(os.path.join(STATION, "quant_backtest",
                                         "reports", "positions.html"),
                            nav_key="positions")
        elif path == "/bt-sectors":
            self._bt_sectors()
        elif path == "/bt-jev":
            self._bt_jev()
        elif path == "/bt-brief":
            self._bt_brief()
        elif path == "/sys-health":
            # 系统自检页（实时探活 + 夜间快照）
            self._sys_health()
        elif path == "/bt-opt":
            # 飞书卡片「寻优榜单」按钮目标页：全池参数寻优 Top5
            self._html_file(os.path.join(STATION, "quant_backtest",
                                         "reports", "opt.html"),
                            nav_key="opt")
        elif path in ("/qd", "/qd/"):
            # K-STOCK-027 影子层/展示层分离：渲染委托 qd_view.py（纯函数，
            # 可离线单测 python qd_view.py），本分支只做 I/O 副作用——
            # 读扫描缓存 + 无缓存时后台 spawn venv 扫描（stdout 落文件）
            import subprocess as _sp
            import qd_view as _qd_view
            cache_p = os.path.join(ROOT, "qd_scan_cache.json")
            log_p = os.path.join(ROOT, "qd_scan.log")
            snap_qd = _read_json(cache_p) if os.path.exists(cache_p) else None
            if snap_qd is None:
                # 首次：后台拉起扫描（venv 解释器；长跑纪律输出落文件）
                VENV_PY = ("C:/Users/Administrator/.workbuddy/binaries/python/"
                           "envs/default/Scripts/python.exe")
                SCAN_PY = os.path.join(ROOT, "qd_scanner.py")
                if not os.path.exists(log_p) or time.time() - os.path.getmtime(log_p) > 1800:
                    with open(log_p, "ab") as lf:
                        _sp.Popen([VENV_PY, SCAN_PY], stdout=lf, stderr=lf,
                                  stdin=_sp.DEVNULL,
                                  creationflags=0x00000008 | 0x00000200)
                self._html(_qd_view.render_placeholder(log_p, self._COCKPIT_CSS))
                return
            # K-STOCK-028：当日基础数据快照（qd_daily_snapshot.py 14:55 产出）
            # 传入渲染——尾盘候选行据此补齐名称/涨跌/市值/换手/技术状态
            base_p = os.path.join(ROOT, "qd_daily_data", "snap_%s.json"
                                  % time.strftime("%Y%m%d"))
            base_qd = _read_json(base_p) if os.path.exists(base_p) else None
            self._html(_qd_view.render_workspace(snap_qd, self._COCKPIT_CSS,
                                                 base_qd))
        elif path == "/bt-news":
            self._bt_news(qs)
        elif path == "/bt":
            # K237 选股中心总目录：三产线选股中心导航——青萍之末的池子
            # 已迁 /bt-qpzm（共振选股工作台，四市场模块卡同源注入），
            # 本页只做产线导航与池子摘要
            import html as _h_bt
            e = _h_bt.escape
            snap = _read_json(os.path.join(REPORTS_DIR,
                                           "rps_latest.json")) or {}
            # K-STOCK-022b：双刀卡片动态统计（读 /qd/ 同源扫描缓存，缺省空）
            qd_snap = _read_json(os.path.join(ROOT, "qd_scan_cache.json"))
            res = snap.get("res", [])

            def _act(r):
                return str(r.get("action", "")).replace("（环境闸门）", "")
            n = len(res)
            nb = sum(1 for r in res if _act(r) == "买入")
            nw = sum(1 for r in res if _act(r) == "观察")

            def _card(href, border, icon, name, desc, stat):
                return ("<a href='%s' style='display:block;border:1px "
                        "solid %s;border-radius:12px;background:var(--gray-2);"
                        "padding:16px 18px;text-decoration:none;"
                        "color:var(--gray-7)'>"
                        "<div style='font-size:17px;font-weight:700'>"
                        "%s %s</div>"
                        "<div class='dim' style='font-size:12.5px;"
                        "margin:6px 0 10px'>%s</div>"
                        "<div style='font-size:13px'><b>%s</b>"
                        "<span style='float:right;color:%s;font-size:"
                        "12.5px'>进入选股中心 →</span></div></a>"
                        % (href, border, icon, name, desc, stat, border))
            cards = "".join([
                _card("/bt-qpzm", "#58a6ff", "🌊", "青萍之末 · 选股中心",
                      "四市场自选池 · 四法门共振评分（技术.35/JEV.25/"
                      "RPS.25/环境.15）· 环境闸门一票否决",
                      "%d 只在池 · 买入 %d · 观察 %d" % (n, nb, nw)),
                _card("/bt-chanlun", "#e3b341", "🧭", "缠论 · 选股中心",
                      "多级别结构信号 · 三类买卖点 · 预警池 · 关键位标注"
                      "专项", "结构信号每日盘后生成"),
                _card("/qd/", "#3fb950", "⚔", "量子双刀 · 选股中心",
                      "双日十字星+MACD 多头 · 八重过滤+Regime 分层"
                      "（研究态：T-009 回测验证中）",
                      "当日信号 %d · 近5日 %d" % (
                          len((qd_snap or {}).get("signals_now", [])),
                          len((qd_snap or {}).get("signals_recent", [])))),
                # K-STOCK-033：AI 市场联动卡（聘请 AI 做商业化）
                _card("/aim", "#d29922", "🤖", "AI 市场",
                      "可聘 AI 岗位货架 · A0 参谋长 + 投研部 alpha 团队"
                      "（T1/T2/N1/F1/D1）+ 专项 AI · 本地模型零 token 成本",
                      "11 个岗位在岗 · 按岗聘请"),
            ])
            body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                    "<meta name='viewport' content='width=device-width,"
                    "initial-scale=1'><title>选股中心 · 多产线</title>"
                    + self._COCKPIT_CSS + "</head><body>"
                    + pill_nav("bt")
                    + "<h1>🎯 选股中心 · 多产线</h1><div class='dim'>"
                    "多 Agent 平台：各产线出的票归各自选股中心 · 快照 "
                    + e(str(snap.get("latest", "—"))) + "</div>"
                    + "<div style='display:grid;grid-template-columns:"
                    "repeat(auto-fit,minmax(280px,1fr));gap:12px;"
                    "margin:16px 0'>" + cards + "</div>"
                    + "<div class='dim' style='margin-top:10px'>历史量化"
                    "工具（四策略回测/寻优/板块/报告）保留于上方导航与各"
                    "产线工作台内 · 纸面研究不构成投资建议</div>"
                    "</body></html>")
            self._html(body)
        elif path.startswith("/bt/m/"):
            # 市场分域页（A股/美股/港股/加密，五块结构）
            # K237：跑批产物内旧称「量化中心」动态更名「选股中心」
            fn = {"cn": "bt_m_cn.html", "us": "bt_m_us.html",
                  "hk": "bt_m_hk.html", "crypto": "bt_m_crypto.html"}.get(
                      path[len("/bt/m/"):])
            if fn:
                fp = os.path.join(STATION, "quant_backtest",
                                  "reports", fn)
                try:
                    html_m = open(fp, encoding="utf-8").read().replace(
                        "量化中心", "选股中心")
                    self._html(html_m)
                except OSError:
                    self._html_file(fp)
            else:
                self._json({"error": "bad market"}, 400)
        elif path == "/premarket":
            # 盘前六层分析（tushare+JEV+zhice-db）
            self._html_file(os.path.join(
                STATION, "quant_backtest", "reports", "premarket.html"))
        elif path == "/bt-tbe":
            # K154 tbe/pbe 影子分层完整报表（chanlun_tbe_stats.py 生成）
            self._html_file(os.path.join(
                STATION, "quant_backtest", "reports",
                "chanlun_tbe_report.html"))
        elif path == "/bt-stock-incident":
            # 事故复盘：/bt/stock K157 文案 %/ 未转义连接重置（2026-10-05）
            self._html_file(os.path.join(
                STATION, "quant_backtest", "reports",
                "portal_stock_incident_20261005.html"))
        elif path == "/col-audit":
            # K176 数据源列对账摘要（source_col_audit 防线报告，取最新）
            _cands = glob.glob(os.path.join(
                STATION, "quant_backtest", "reports",
                "col_audit_summary_*.html"))
            _latest = max(_cands, key=os.path.getmtime, default=None)
            if _latest:
                self._html_file(_latest)
            else:
                self._html(
                    "<body>%s<h1>📋 列对账</h1><div class='dim'>摘要不存在"
                    "——先跑 quant_backtest/source_col_audit.py"
                    "</div></body>" % pill_nav())
        elif path == "/fut":
            # K162-E F4 期货量化面板（FutQuant 每日 09:30 刷新数据）
            # K-STOCK-057 壳对齐全站 tokens（弃手写暗色 body）
            self._html(
                "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>期货量化</title>"
                + self._COCKPIT_CSS + "</head><body>"
                "<div style='max-width:1180px;margin:0 auto'>"
                + pill_nav("fut") + fut_page_html()
                + "</div></body></html>")
        elif path == "/sellpoint":
            # K208 SellPoint AI 卖点研判面板（SellPointAI 每日刷新数据）
            # K-STOCK-057 壳对齐全站 tokens（弃手写暗色 body）
            self._html(
                "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>SellPoint AI</title>"
                + self._COCKPIT_CSS + "</head><body>"
                "<div style='max-width:1180px;margin:0 auto'>"
                + pill_nav("sellpoint") + sellpoint_page_html()
                + "</div></body></html>")
        elif path == "/deliverables":
            # K-STOCK-057 对外快照档案（X5 公网快照落盘 deliverables/）：
            # 只读索引 + ?f= 单文件原文（basename 防穿越，仅 md/txt）
            import glob as _glob
            import html as _hes
            from urllib.parse import parse_qs as _pqs2, quote as _urlq

            def _he_es(s):
                return _hes.escape(s or "")

            ddir = os.path.join(os.path.dirname(ROOT), "deliverables")
            _fn = (_pqs2(qs).get("f") or [""])[0] if qs else ""
            if _fn:
                _bn = os.path.basename(_fn)
                if not _bn.endswith((".md", ".txt")):
                    return self._json({"error": "only md/txt"}, 403)
                _fp = os.path.join(ddir, _bn)
                if not os.path.isfile(_fp):
                    return self._json({"error": "not found"}, 404)
                with open(_fp, encoding="utf-8", errors="replace") as f:
                    return self._html(
                        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                        "<meta name='viewport' content='width=device-width,"
                        "initial-scale=1'><title>📄 " + _he_es(_bn)
                        + " · 青萍之末</title>"
                        + self._COCKPIT_CSS + "</head><body>"
                        + pill_nav("deliverables")
                        + "<h1>📄 %s</h1>"
                        "<pre style='white-space:pre-wrap;font-size:13px;"
                        "line-height:1.75'>%s</pre></body></html>"
                        % (_he_es(_bn),
                           _he_es(f.read())))
            files = []
            for fp in sorted(_glob.glob(os.path.join(ddir, "*.md")),
                             reverse=True):
                try:
                    st = os.stat(fp)
                    files.append((os.path.basename(fp), st.st_size,
                                  time.strftime("%Y-%m-%d %H:%M",
                                                time.localtime(st.st_mtime))))
                except OSError:
                    continue
            rows = "".join(
                "<tr><td><a href='/deliverables?f=%s'>%s</a></td>"
                "<td class='num'>%.1f KB</td><td class='dim'>%s</td></tr>"
                % (_urlq(n), _he_es(n), sz / 1024, mt)
                for n, sz, mt in files)
            self._html(
                "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>📦 对外快照 · 青萍之末</title>"
                + self._COCKPIT_CSS + "</head><body>"
                + pill_nav("deliverables")
                + "<h1>📦 对外快照档案</h1>"
                "<div class='dim'>X5 公网快照落盘（deliverables/）· "
                "只读 · 共 %d 份</div>"
                "<div class='sec' style='margin-top:12px'>"
                "<table><tr><th>文件</th><th>大小</th><th>时间</th></tr>%s"
                "</table></div></body></html>"
                % (len(files), rows or
                    "<tr><td colspan='3' class='dim'>"
                    "暂无快照</td></tr>"))
        elif path == "/fut-strat":
            # K161-B F3 期货核心池策略×品种回测对照（fut_backtest.py 生成）
            import glob as _glob
            cands = sorted(_glob.glob(os.path.join(
                STATION, "tools", "out", "fut_strat_*.html")))
            if cands:
                self._html_file(cands[-1])
            else:
                self._json({"error": "fut_strat 报表未生成，先跑 "
                                     "tools/fut_backtest.py"}, 404)
        elif path == "/afterhours":
            # 盘后复盘（收盘全景/赚钱效应/预判对照）
            self._html_file(os.path.join(
                STATION, "quant_backtest", "reports", "afterhours.html"))
        elif path.startswith("/bt/strategy/"):
            # 策略库纵切页（宽度/自选状态/回测/寻优）
            sn = path[len("/bt/strategy/"):]
            if sn in ("trend_follow", "ma_cross", "rsi_reversal",
                      "boll_breakout"):
                self._html_file(os.path.join(
                    STATION, "quant_backtest", "reports",
                    "bt_strat_%s.html" % sn))
            else:
                self._json({"error": "bad strategy"}, 400)
        elif path == "/api/research":
            # P-B2 个股按需深调：默认读当日缓存；force=1 重算（阻塞 10-90s）
            from urllib.parse import parse_qs as _pqr
            qp3 = _pqr(qs)
            import sys as _sysr
            if QBT_DIR not in _sysr.path:
                _sysr.path.insert(0, QBT_DIR)
            from research_agent import research_for_stock
            _rc = (qp3.get("c") or [""])[0]
            if not _rc:
                self._json({"ok": False, "err": "missing c"}, 400)
                return
            _rf = (qp3.get("force") or ["0"])[0] == "1"
            try:
                self._json(research_for_stock(
                    _rc, force=_rf, read_only=not _rf))
            except Exception as _re3:
                log_exc("GET /api/research")  # C2批1
                self._json({"ok": False, "err": str(_re3)[:200]}, 500)
        elif path == "/api/nlquery":
            # P-E 多 agent 自然语言查数：默认读当日缓存；force=1 现算（10-30s）
            from urllib.parse import parse_qs as _pqn, unquote as _uqn
            qp_nl = _pqn(qs)
            import sys as _sysn
            if QBT_DIR not in _sysn.path:
                _sysn.path.insert(0, QBT_DIR)
            from multi_agent import answer as _ma_answer
            _q = _uqn((qp_nl.get("q") or [""])[0])
            if not _q:
                self._json({"ok": False, "err": "missing q"}, 400)
                return
            _nf = (qp_nl.get("force") or ["0"])[0] == "1"
            # P1-7：force=1 频控（每 IP 每分钟 ≤3 次，超限 429）
            if _nf:
                _ip = self.client_address[0]
                _now = time.time()
                _hits = [t for t in _NLQ_FORCE_HITS.get(_ip, [])
                         if _now - t < 60]
                if len(_hits) >= _NLQ_FORCE_MAX_PER_MIN:
                    self._json({"ok": False, "err": "force=1 触发频控："
                                "每 IP 每分钟最多 %d 次，请稍后再试"
                                % _NLQ_FORCE_MAX_PER_MIN}, 429)
                    return
                _hits.append(_now)
                _NLQ_FORCE_HITS[_ip] = _hits
            try:
                self._json(_ma_answer(_q, force=_nf, read_only=not _nf))
            except Exception as _nle:
                log_exc("GET /api/nlquery")  # C2批1
                self._json({"ok": False, "err": str(_nle)[:200]}, 500)
        elif path == "/nl":
            # P-E 自然语言查数页：输入问句 → multi_agent 多 agent 答案
            self._nl_page(qs)
        elif path == "/bt/analyst-report":
            # K244 青萍之末分析师报告页（事实聚合 + LLM 大白话 + 飞书一键）
            self._analyst_report_page(qs)
        elif path == "/bt/subscribe":
            # K245 订阅推送管理页（订阅清单 + 接收人 + 试推）
            self._subscribe_page(qs)
        elif path == "/paper":
            # K-STOCK 模拟仓前端（K279 产出 paper.html；nginx 8080 同文件
            # 静态直出，此处读盘直出保双口径一致——K-STOCK-042 修正过时占位）
            _pf = os.path.join(ROOT, "paper.html")
            if os.path.exists(_pf):
                try:
                    with open(_pf, encoding="utf-8") as f:
                        self._html(f.read())
                except Exception as e:   # 读盘异常兜底占位，不让线程裸断连
                    self._html("<!DOCTYPE html><html><head><meta charset="
                               "'utf-8'></head><body>" + pill_nav("paper") +
                               "<div class='sec'>前端文件读取异常：%s</div>"
                               "</body></html>" % e)
            else:
                self._html("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                           "<title>模拟仓 · 建设中</title>" + self._COCKPIT_CSS
                           + "</head><body>" + pill_nav("paper")
                           + "<h1>🧾 模拟仓（K-STOCK 线）</h1><div class='sec'>"
                           "建设中：数据层已就绪（/api/paper_status）。</div>"
                           "</body></html>")
        elif path == "/bt/stock":
            # 个股全景页（行情+策略信号+共振分解+JEV下钻+AI展望）
            self._bt_stock_page(qs)
        elif path == "/bt/report":
            # 研报本地全文页（结构化库直读，零外链）
            self._bt_report_page(qs)
        elif path == "/bt/analysts":
            # 分析师知识图谱页（分析师→个股→评级记录）
            self._bt_analysts_page()
        elif path == "/bt/ind_drill":
            # 行业下钻页：成分股涨跌全量 + 主力资金个股明细
            self._bt_ind_drill_page(qs)
        elif path == "/bt/lhb":
            # 龙虎榜下钻页：当日上榜全量 + 频次 + 板块扎堆
            self._bt_lhb_page(qs)
        elif path == "/bt/seat":
            # 坐席下钻页：席位近60日上榜明细 + 常做票
            self._bt_seat_page(qs)
        elif path == "/api/audit_recent":
            # K-STOCK-114e 快捷审计素材：最近一次提交 diff（截断 12k）
            try:
                r = subprocess.run(
                    ["git", "log", "-1", "--format=%h %s%n%an %ad",
                     "--date=iso"], cwd=ROOT, capture_output=True,
                    text=True, encoding="utf-8", errors="replace", timeout=10)
                d = subprocess.run(
                    ["git", "show", "--format=", "--unified=3", "HEAD"],
                    cwd=ROOT, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=15)
                diff = (d.stdout or "")[:12000]
                self._json({"ok": True, "head": (r.stdout or "").strip(),
                            "diff": diff,
                            "truncated": len(d.stdout or "") > 12000})
            except Exception as _ae:
                self._json({"ok": False, "error": repr(_ae)}, 500)
        elif path == "/audit-ai":
            # K-STOCK-114e 独立安全审计 AI 对话页
            self._audit_ai_page()
        elif path == "/api/devops":
            # K-STOCK-113 开发指挥台数据口：dev_events.jsonl 只读投影
            self._json(self._devops_data())
        elif path == "/devops":
            # K-STOCK-113 开发指挥台页：多 IDE 统一调度态势（只读）
            self._devops_page()
        elif path == "/portal":
            # K-STOCK-035 总入口门户：哪里是什么、我的入口在哪里
            self._portal_page()
        elif path == "/org":
            self._org_page()
        elif path == "/aim":
            # K-STOCK-033 AI 市场：聘请 AI 岗位货架（ai_market.json 存源）
            self._aim_page()
        elif path == "/algo":
            # K-STOCK-087 算法商品市场：试看样例+本地卡密订阅解锁
            self._algo_market_page(qs)
        elif path == "/bt-qpzm" or path.startswith("/bt-qpzm/"):
            # 策略市场 1 号工作台：青萍之末 · 共振选股
            # K-STOCK-032 多页面联动：/bt-qpzm 主页驾驶舱 +
            # /bt-qpzm/{res,rps,hub,batch,spec} 子页
            self._qpzm_page(path[len("/bt-qpzm"):].strip("/"))
        elif path == "/bt-chanlun":
            # 策略市场 2 号：缠论 · 结构交易（筹备中占位页）
            self._chanlun_page()
        elif path == "/bt-chanlun-pool":
            # 缠论扫描 → 预警池 → 模拟交易（影子观察；?m=US|HK 切市场池链）
            self._chanlun_pool_page(qs)
        elif path == "/chanlun-png":
            # K72: 当日缠论结构图 PNG（{c}_1d_*.png 最新 / 现场兜底生成）
            self._chanlun_png(qs)
        elif path == "/chanlun-anno":
            # 缠论图手工标注（GET 列表 / POST 增删，出图时叠加）
            self._chanlun_anno(qs)
        elif path == "/chanlun-anno-report":
            # K77b 标注总结报告（动态：标注×信号×筹码×结构图）
            self._chanlun_anno_report()
        elif path == "/bt-linkage":
            # 大盘-板块-个股 三线联动复盘（TDX 880xxx 板块口径，影子观察）
            self._linkage_page(qs)
        elif path == "/bt-linkage-png":
            from urllib.parse import parse_qs as _pql
            self._linkage_png(_pql(qs))
        elif path == "/bt-weak":
            # 弱于板块案例库（weak 产线：扫描→建案→归因→跟踪→结案）
            self._weak_cases_page()
        elif path == "/bt-weak-md":
            from urllib.parse import parse_qs as _pqw
            self._weak_md(_pqw(qs))
        elif path == "/bt-9dim":
            # 策略市场：九维量化选股（影子评分，纯门户不推送）
            self._nine_dim_page()
        elif path == "/bt-cross":
            # 策略集市 · 多策略共振看板（跨策略聚合发现层，全下钻）
            self._cross_page()
        elif path == "/api/kline":
            # S1 个股K线 JSON（四市场日线+MA+A股结构信号+JEV标注）
            from urllib.parse import parse_qs as _pqk, unquote as _uqk
            qp = _pqk(qs)
            try:
                self._json(_kline_data(
                    _uqk((qp.get("c") or [""])[0]).strip()[:24],
                    (qp.get("n") or ["250"])[0]))
            except Exception as ex:
                log_exc("GET /api/kline")  # C2批1
                self._json({"ok": False, "err": str(ex)[:120]}, 500)
        elif path == "/api/kline_qy":
            # K200 季/年线 JSON（深历史 datamart 两段拼接 + 服务端聚合结构；
            # 懒加载独立端点，前端点击季/年按钮才拉取）
            from urllib.parse import parse_qs as _pqkq, unquote as _uqkq
            qp = _pqkq(qs)
            try:
                self._json(_kline_qy(
                    _uqkq((qp.get("c") or [""])[0]).strip()[:24]))
            except Exception as ex:
                log_exc("GET /api/kline_qy")  # C2批1
                self._json({"ok": False, "err": str(ex)[:120]}, 500)
        elif path == "/api/analyst-report":
            # K244 青萍之末分析师报告（事实聚合 + LLM 大白话，异步用）
            self._api_analyst_report(qs)
        elif path == "/api/chips":
            # S5 筹码分布瀑布 JSON（cyq_chips 单日按需+parquet 缓存）
            from urllib.parse import parse_qs as _pqc, unquote as _uqc
            qp = _pqc(qs)
            try:
                self._json(_chips_data(
                    _uqc((qp.get("c") or [""])[0]).strip()[:24],
                    (qp.get("d") or [""])[0],
                    (qp.get("x") or [""])[0]))
            except Exception as ex:
                log_exc("GET /api/chips")  # C2批1
                self._json({"ok": False, "err": str(ex)[:120]}, 500)
        elif path == "/api/bridge/summary":
            # K56-B1 驾驶舱聚合（五仪数据一次拉齐，只读）
            try:
                self._json(bridge_summary())
            except Exception as ex:
                log_exc("GET /api/bridge/summary")  # C2批1
                self._json({"error": str(ex)}, 500)
        elif path == "/api/bridge/table_preview":
            # K56 数据穿透：白名单表最新 n 行（驾驶舱/数据仓 🔍 展开）
            from urllib.parse import parse_qs as _pqs2
            _q2 = _pqs2(qs or "")
            _tb2 = (_q2.get("table") or [""])[0].strip()[:40]
            try:
                _nn2 = int((_q2.get("n") or ["8"])[0])
            except Exception:
                _nn2 = 8
            self._json(bridge_table_preview(_tb2, _nn2))
        elif path == "/api/data/freshness":
            # 数据底座时点校对（表/最新/期望/滞后/状态）
            try:
                self._json(data_freshness())
            except Exception as ex:
                log_exc("GET /api/data/freshness")  # C2批1
                self._json({"error": str(ex)}, 500)
        elif path == "/api/data/sync":
            # 手动触发同步（数据仓页按钮）：?src=tdx|tushare|kg|catalog
            from urllib.parse import parse_qs as _pqs
            src = (_pqs(qs).get("src") or [""])[0]
            self._json(data_sync_start(src))
        elif path == "/api/data/job":
            # 同步任务轮询：运行中? + 日志尾部
            from urllib.parse import parse_qs as _pqj
            src = (_pqj(qs).get("src") or [""])[0]
            self._json(data_sync_status(src))
        elif path == "/api/batch-backtest":
            # K255 自定义批次回测轮询：报告就绪则整体带出（前端 3s 轮询）
            self._json(_bt_custom_status())
        elif path == "/api/alert/push":
            # 手动触发飞书预警推送（/intel 卡「立即推送」按钮，零JS）
            # 复用 data_sync 单实例后台模式；触发后跳回 /intel 看状态
            r = data_sync_start("alertpush")
            if "error" in r and r.get("error") == "bad src":
                self._json(r, 500)
            else:
                self.send_response(302)
                self.send_header("Location", "/intel")
                self.send_header("Content-Length", "0")
                self.end_headers()
        elif path == "/screen":
            # K56-B4 数据大屏：统一 serve screen.html 文件版（与 nginx 8080
            # 同源；旧内嵌 _screen_page 保留作文件缺失时的兜底）
            try:
                _sp = os.path.join(os.path.dirname(
                    os.path.abspath(__file__)), "screen.html")
                with open(_sp, "r", encoding="utf-8") as _sf:
                    self._html(_sf.read())
            except Exception:
                log_exc("GET /screen 文件版读取失败，兜底内嵌渲染")  # C2批1
                self._screen_page()
        elif path == "/screen-sig":
            # K64 当日买卖点全量页（大屏预警面板"全屏 »"下钻）
            self._screen_sig_page()
        elif path == "/bt-data-health":
            # 数据仓健康盘点：覆盖度/更新时间/缺口，一眼看清地基
            self._data_health_page()
        elif path == "/bt-rps":
            # 旧入口 → A股市场页（保留飞书旧卡片按钮可用）
            self.send_response(301)
            self.send_header("Location", "/bt/m/cn")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path.startswith("/bt-report/img/"):
            # 报告页图表 PNG（matplotlib 生成，白名单防穿越）
            import re as _re2
            fn = path[len("/bt-report/img/"):]
            if _re2.fullmatch(r"[0-9A-Za-z_.\-]+\.png", fn):
                p = os.path.join(STATION, "quant_backtest", "reports",
                                 "charts", fn)
                if os.path.isfile(p):
                    with open(p, "rb") as f:
                        body = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Cache-Control", "max-age=86400")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
            self._json({"error": "not found"}, 404)
        elif path == "/bt-report":
            # 飞书卡片「回测报告」按钮目标页：quant_backtest/reports/report.html
            self._html_file(os.path.join(STATION, "quant_backtest",
                                         "reports", "report.html"),
                            nav_key="report")
        elif path.startswith("/bt-report/"):
            # 按次快照页（卡片按钮直达该次报告）：白名单字符校验防路径穿越
            fn = path[len("/bt-report/"):]
            import re as _re
            if _re.fullmatch(r"[0-9A-Za-z_.\-]+\.html", fn):
                self._html_file(os.path.join(STATION, "quant_backtest",
                                             "reports", "snap", fn))
            else:
                self._json({"error": "bad name"}, 400)
        else:
            self._json({"error": "not found"}, 404)


if __name__ == "__main__":
    try:
        # P0-1a（2026-10-08 全检修复）：绑定收紧为 127.0.0.1。
        # 原 0.0.0.0 使局域网任意设备可直连 8091 调全部写端点（与 SYSTEM
        # 计划任务叠加构成 LAN→SYSTEM 提权链）。收绑定后局域网只能经
        # ai-portal nginx(8080) 反代进入；Docker Desktop 的
        # host.docker.internal 由用户态代理转发，宿主机侧仍落在 127.0.0.1，
        # 故 nginx.conf 无需改动反代目标（nginx.conf 中已注明此机制）。
        # 生效需重启 portal API；重启后验证：
        #   docker exec ai-portal curl -s -o /dev/null -w '%{http_code}' \
        #     http://host.docker.internal:8091/health   # 期望 200
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        raise SystemExit(0)  # 已有实例在跑，静默退场（计划任务重复触发场景）

    def _warm_freshness():
        # 预热数据时点（15 表现算 ~25-45s）：避免重启后首个用户请求
        # 撞上冷算，超过 nginx /api/ 40s 代理超时
        try:
            _freshness_warmup()
            print("[warmup] freshness ok", flush=True)
        except Exception as ex:
            log_exc("warmup:freshness 预热失败")  # C2批1
            print("[warmup] freshness fail:", ex, flush=True)

    threading.Thread(target=_warm_freshness, daemon=True).start()
    srv.serve_forever()
