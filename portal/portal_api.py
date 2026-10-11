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
    _alerts_last_jsonl, _council_stats, _council_pending, _council_cache, _BTSEL_TAG)
import api_kg
from api_kg import _kg_card_fb_save, _kg_label_save
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
# K84 辩论室「你的插话已重测」横幅的时效窗口（秒）：
# 插话后该秒数内进详情页显示横幅；可用环境变量 DEBATE_RECENT_SEC 覆盖
DEBATE_RECENT_SEC = int(os.environ.get("DEBATE_RECENT_SEC") or 600)





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


def get_quotes():
    try:
        with open(os.path.join(STATION, "news_collector", "quotes_status.json"),
                  "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # 豁免（C2批1）：quotes_status.json 未生成属常态（首次运行前）
        return {"ok": None, "items": []}




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
_HERMES_FAB_HTML = ("<a id='_hermesfab' href='http://127.0.0.1:8787' "
                    "target='_blank' rel='noopener' "
                    "title='超级助手：对话即调度全部 AI 岗位' "
                    "style='position:fixed;right:18px;bottom:70px;width:46px;"
                    "height:46px;border-radius:50%;background:#1f6feb;"
                    "border:2px solid #58a6ff;font-size:22px;line-height:42px;"
                    "text-align:center;z-index:100;cursor:pointer;"
                    "user-select:none;text-decoration:none;"
                    "box-shadow:0 2px 12px rgba(31,111,235,.45)'>🦸</a>")










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

# 055-L3 短屏化：长页锚点条+分区折叠（h2>=6 折叠、>=4 锚点；flex/grid 守卫）
_SS_TAG = '<script src="/static/shortscreen.js" defer></script>'




















def _council_holdings(cs, opens):
    """K63 持仓管理明细：proposals 全量按 (sid,code) 取最新立案留痕
    ∩ 当前持仓 code 集，返回按指令严重度排序的明细行（不缓存，量小；
    props 行数=立案票数级，远小于 watch 表）。"""
    if not (cs or {}).get("d") or not opens:
        return []
    hmap = {}
    try:
        with open(os.path.join(QBT_DIR, "reports",
                               "council_proposals.jsonl"),
                  encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                k = (r.get("sid"), r.get("code"))
                if k in opens:
                    hmap[k] = r          # 后读覆盖 = 最新一夜
    except OSError:
        pass
    order = {"平仓预警": 0, "减仓预警": 1, "收紧盯盘": 2, "持有观察": 3}
    hold = [{"sid": k[0], "code": k[1], "name": r.get("name"),
             "verdict": (r.get("memo") or {}).get("verdict"),
             "act": (r.get("directive") or {}).get("act") or "—",
             "style": r.get("style") or "稳健",
             "net": (r.get("directive") or {}).get("net_stance"),
             "k": ((r.get("directive") or {}).get("sizing") or {}).get("k"),
             "vote": ((r.get("vote") or {}).get("verdict")),
             "d": r.get("d")} for k, r in hmap.items()]
    hold.sort(key=lambda x: (order.get(x["act"], 9), str(x["code"])))
    return hold




SCREEN_TTL = 60










def build_screen():
    """大屏 /api/screen 数据源（60s 缓存），全部只读产线快照：
    ① envs        四市场环境灯（rps_latest.json envs）
    ② strategies  每策略最新选股数 × 市场分布（cross_poll.build_cross）
    ③ pick_detail 最新一期选股明细（多策略命中数排序，前 80）
    ④ industry    A 股行业细分（九维 full 池行业计数 top12）
    ⑤ portfolio   统一虚拟账户（portfolio.json：两子账户净值/持仓）
    ⑥ books       既有账本成绩（sig4=positions.json / chanlun=chanlun_paper）
    单项失败即缺项，不拖垮整屏。"""
    now = time.time()
    if _screen_cache["d"] is not None and now - _screen_cache["t"] < SCREEN_TTL:
        return _screen_cache["d"]
    out = {"ts": time.strftime("%Y-%m-%d %H:%M:%S")}

    # ① 四市场环境灯
    rps = _read_json(os.path.join(REPORTS_DIR, "rps_latest.json"))
    envs = []
    for e in rps.get("envs") or []:
        lb = e.get("label") or ""
        mkt = ("CN" if lb.startswith("A股") else
               "US" if lb.startswith("美股") else
               "HK" if lb.startswith("港股") else
               "CRYPTO" if lb.startswith("加密") else None)
        if mkt:
            envs.append({"market": mkt, "score": e.get("score"),
                         "safe": e.get("safe"), "state": e.get("state"),
                         "breadth": e.get("breadth")})
    out["envs"] = envs
    out["rps_ts"] = rps.get("ts", "")

    # ②③ cross_poll 聚合：每策略选股×市场 + 明细
    strategies, detail = [], []
    try:
        if QBT_DIR not in sys.path:
            sys.path.insert(0, QBT_DIR)
        import cross_poll
        cross = cross_poll.build_cross()
        stocks = cross.get("stocks") or []
        for s in cross.get("strategies") or []:
            sid = s.get("id")
            hits = [st for st in stocks
                    if any(h.get("sid") == sid for h in st.get("hits") or [])]
            bm = {}
            for st in hits:
                bm[st["market"]] = bm.get(st["market"], 0) + 1
            strategies.append({"id": sid, "name": s.get("name"),
                               "icon": s.get("icon"), "badge": s.get("badge"),
                               "color": s.get("color"), "url": s.get("url"),
                               "markets": s.get("markets"),
                               "n": len(hits), "by_market": bm})
        for st in stocks:
            detail.append({"market": st["market"], "code": st["code"],
                           "name": st["name"], "n_hit": st.get("n_hit", 0),
                           "n_long": st.get("n_long", 0),
                           "state": st.get("state"),
                           "hits": [h.get("sid") for h in st.get("hits") or []]})
    except Exception:
        log_exc("_screen_page:cross_poll 聚合")  # C2批1
    out["strategies"] = strategies
    out["pick_detail"] = sorted(detail, key=lambda x: -x["n_hit"])[:80]

    # ④ A 股行业细分（九维 full 池）
    nd = _read_json(os.path.join(QBT_DIR, "data", "nine_dim_scores.json"))
    ind = {}
    for r in nd.get("rows") or []:
        if r.get("full") and r.get("industry"):
            ind[r["industry"]] = ind.get(r["industry"], 0) + 1
    out["industry"] = sorted(({"name": k, "n": v} for k, v in ind.items()),
                             key=lambda x: -x["n"])[:12]
    out["nd_ts"] = nd.get("generated_at", "")

    # ⑤ 统一虚拟账户（K44 每日 23:50 模拟建仓 + 择时择势自动平仓）
    #    K54-A1/A2/A3：份额净值 ps + 机构指标（SR/MaxDD/波动）+ 母基金视图
    pf = _read_json(os.path.join(REPORTS_DIR, "portfolio.json"))
    pfc = _read_json(os.path.join(QBT_DIR, "data", "portfolio_config.json"))
    pm = None
    try:
        if QBT_DIR not in sys.path:
            sys.path.insert(0, QBT_DIR)
        import perf_metrics as _pm
        pm = _pm
    except Exception:
        pm = None

    def _cps(c):
        return c.get("ps") if c.get("ps") else 1.0 + (c.get("ret") or 0) / 100.0

    accounts = {}
    fof_curves, fof_shares = {}, {}
    for sid, ac in (pf.get("accounts") or {}).items():
        curve = ac.get("nav_curve") or []
        closed = ac.get("closed") or []
        wins = [c for c in closed if (c.get("exit_pnl_pct") or 0) > 0]
        acc = (pfc.get("accounts") or {}).get(sid) or {}
        # K51-C 熔断状态（与引擎 daily_run 同口径：平仓后实时净值 vs 峰值）
        nav_now = (ac.get("cash") or 0) + sum(
            p["qty"] * (p.get("last_px") or p.get("entry_px") or 0)
            for p in (ac.get("open") or []))
        peak = max([c["nav"] for c in curve]
                   + [ac.get("capital") or 0, nav_now])
        dd = round((peak - nav_now) / peak * 100, 2) if peak > 0 else 0.0
        mdd_cfg = float(acc.get("max_drawdown_pct",
                                pfc.get("max_drawdown_pct", 10)))
        # K54-B4 三级回撤墙（与引擎 _dd_level 同口径：1 警戒/2 熔断/3 深回撤）
        dd_warn = float(acc.get("dd_warn_pct", pfc.get("dd_warn_pct", 5)))
        dd_cut = float(acc.get("dd_cut_pct", pfc.get("dd_cut_pct", 15)))
        dd_lv = (3 if (dd_cut > 0 and dd >= dd_cut)
                 else 2 if (mdd_cfg > 0 and dd >= mdd_cfg)
                 else 1 if (dd_warn > 0 and dd >= dd_warn) else 0)
        # K54-A2 机构指标（份额收益 ps 序列：申赎不污染曲线）
        m = {"sharpe": None, "sortino": None, "vol_ann": None,
             "max_dd_pct": None, "total_ret": None}
        if pm and len(curve) >= 2:
            try:
                m = pm.compute([{"d": c["d"], "nav": _cps(c)} for c in curve])
            except Exception:
                log_exc("_screen_page:perf_metrics.compute")  # C2批1
        # K51-B 平仓原因归类统计 + 平均持有交易日
        cats = {}
        for c in closed:
            cat = _pf_reason_cat(c.get("reason"))
            d = cats.setdefault(cat, {"n": 0, "pnl": 0.0})
            d["n"] += 1
            d["pnl"] += c.get("exit_pnl_pct") or 0
        for d in cats.values():
            d["pnl"] = round(d["pnl"] / d["n"], 2)
        holds = [c.get("hold_days") or 0 for c in closed]
        accounts[sid] = {
            "name": ac.get("name"), "icon": ac.get("icon"),
            "capital": ac.get("capital"), "cash": ac.get("cash"),
            "shares": ac.get("shares") or ac.get("capital"),
            "start_date": acc.get("start_date") or pfc.get("start_date") or "",
            "stop_loss_pct": acc.get("stop_loss_pct",
                                     pfc.get("stop_loss_pct", 8)),
            "take_profit_pct": acc.get("take_profit_pct",
                                       pfc.get("take_profit_pct", 15)),
            "max_pos": int(acc.get("max_pos", 20)),
            "max_drawdown_pct": mdd_cfg,
            "drawdown": dd, "dd_lv": dd_lv,
            "dd_review": ac.get("dd_review") or None,
            "close_reasons": cats,
            "avg_hold": (round(sum(holds) / len(holds), 1) if holds else None),
            "trades_recent": (ac.get("trades") or [])[-12:],
            "nav": curve[-1]["nav"] if curve else ac.get("capital"),
            "ret": curve[-1]["ret"] if curve else 0.0,
            "ps": round(_cps(curve[-1]), 4) if curve else 1.0,
            "sharpe": m["sharpe"], "sortino": m["sortino"],
            "vol_ann": m["vol_ann"], "maxdd_pct": m["max_dd_pct"],
            "today": (curve[-1]["ret"] - curve[-2]["ret"]
                      if len(curve) > 1 else
                      (curve[-1]["ret"] if curve else 0.0)),
            "n_open": len(ac.get("open") or []),
            "n_closed": len(closed),
            "win_rate": (round(len(wins) / len(closed), 3) if closed else None),
            "closed_avg_pnl": (round(
                sum(c.get("exit_pnl_pct") or 0 for c in closed) / len(closed), 2)
                if closed else None),
            "closed_recent": [{"name": c.get("name"), "market": c.get("market"),
                               "reason": c.get("reason"),
                               "pnl_pct": c.get("exit_pnl_pct"),
                               "exit_date": c.get("exit_date")}
                              for c in closed[-10:]],
            "open": sorted(ac.get("open") or [],
                           key=lambda p: -(p.get("pnl_pct") or 0)),
            "curve": [[c["d"], c["nav"]] for c in curve],
            "curve_ps": [[c["d"], round(_cps(c), 4)] for c in curve]}
        if curve:
            fof_curves[sid] = [{"d": c["d"], "ps": _cps(c)} for c in curve]
            fof_shares[sid] = ac.get("shares") or ac.get("capital") or 1_000_000
    # K54-A3 母基金（四账户份额加权 ps 曲线 + 机构指标）
    fof = None
    if pm and len(fof_curves) >= 2:
        try:
            ps_c = pm.fof_merge(fof_curves, fof_shares)
            fm = pm.compute([{"d": x["d"], "nav": x["ps"]} for x in ps_c])
            fof = {"curve_ps": [[x["d"], round(x["ps"], 4)] for x in ps_c],
                   "ps": round(ps_c[-1]["ps"], 4), "ret": fm["total_ret"],
                   "sharpe": fm["sharpe"], "maxdd_pct": fm["max_dd_pct"],
                   "vol_ann": fm["vol_ann"]}
        except Exception:
            fof = None
    out["portfolio"] = {"accounts": accounts, "ts": pf.get("ts", ""),
                        "hold_max": int(pfc.get("hold_max") or 20),
                        "fof": fof, "bac": pf.get("bac")}

    # ⑥ 既有账本成绩（独立账本，不入统一账户避免双记账）
    books = {}
    pos = _read_json(os.path.join(QBT_DIR, "positions.json"))  # runner 账本（根目录）
    if pos:
        o, c = pos.get("open") or [], pos.get("closed") or []
        books["sig4"] = {
            "name": "四策略信号账本", "n_pending": len(pos.get("pending") or []),
            "n_open": len(o), "n_closed": len(c),
            "open_avg_pnl": (round(sum(e.get("pnl") or 0 for e in o) / len(o), 4)
                             if o else None),
            "win_rate": (round(sum(1 for e in c if (e.get("pnl") or 0) > 0)
                               / len(c), 3) if c else None),
            "closed_avg_pnl": (round(sum(e.get("pnl") or 0 for e in c) / len(c), 4)
                               if c else None),
            "ts": pos.get("ts", "")}
    paper = _read_json(os.path.join(QBT_DIR, "data", "chanlun_paper.json"))
    if paper:
        o, c = paper.get("open") or [], paper.get("closed") or []
        recent = c[-50:]          # 近期战绩：最近 50 笔出场（月度口径月初恒空）
        books["chanlun"] = {
            "name": "缠论纸面仓", "n_open": len(o), "n_closed": len(c),
            "n_exit_recent": len(recent),
            "recent_avg_pnl": (round(sum(e.get("pnl") or 0 for e in recent)
                                     / len(recent), 4) if recent else None),
            "recent_win_rate": (round(
                sum(1 for e in recent if (e.get("pnl") or 0) > 0)
                / len(recent), 3) if recent else None),
            "ts": paper.get("updated_at", "")}
    out["books"] = books

    # ── K57 大屏 v2：每日流水 / 预警增量 / 缠论尾15票 / 当日买卖点 / 新鲜度 ──
    try:
        out["pulse"] = build_daily_pulse()
    except Exception:
        log_exc("_screen_page:build_daily_pulse")  # C2批1
        out["pulse"] = []
    try:
        out["alerts"] = (build_alerts().get("alerts") or [])
    except Exception:
        log_exc("_screen_page:build_alerts")  # C2批1
        out["alerts"] = []
    try:
        pool_path = os.path.join(QBT_DIR, "data", "chanlun_pool.jsonl")
        rows = []
        with open(pool_path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    try:
                        rows.append(json.loads(ln))
                    except Exception:
                        pass

        def _sig(r):
            return {"code": r.get("code"), "name": r.get("name"),
                    "market": r.get("market"), "period": r.get("period"),
                    "type": r.get("type"), "dt": r.get("dt"),
                    "detect": r.get("detect_date"),
                    "px": r.get("entry_px") or r.get("price"),
                    "npv": r.get("npv"), "tbe": r.get("tbe"),
                    "trend": r.get("trend"), "lag": r.get("lag")}

        days = sorted({r.get("detect_date") for r in rows
                       if r.get("detect_date")})
        sig_day = days[-1] if days else ""
        today_rows = [r for r in rows if r.get("detect_date") == sig_day]
        out["sig_day"] = sig_day
        out["signals"] = [_sig(r) for r in today_rows[:60]]
        out["fifteen"] = [_sig(r) for r in reversed(rows[-15:])]
    except Exception:
        log_exc("_screen_page:chanlun_pool 当日信号")  # C2批1
        out["sig_day"] = ""
        out["signals"] = []
        out["fifteen"] = []
    try:
        fr = data_freshness_bg(1800)
        rs = (fr or {}).get("rows") or []
        out["fresh"] = ({"ok": sum(1 for r in rs if r.get("status") == "ok"),
                         "total": len(rs)} if rs else None)
    except Exception:
        log_exc("_screen_page:freshness 新鲜度")  # C2批1
        out["fresh"] = None

    # ── 态势感知 ops：① 服务实时探活（60s 缓存内只探一轮）
    #    ② 数据链最新锚点距今 ③ 调度时刻表（前端算下一班倒计时） ──
    def _probe_ops():
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

        return [("门户API", http_ok("http://127.0.0.1:8091/health")),
                ("nginx网关", http_ok("http://127.0.0.1:8080/")),
                ("量化前端", http_ok("http://127.0.0.1:5175/")),
                ("QD桥接", tcp_ok(9103)),
                ("卡片回调", tcp_ok(9104))]

    try:
        out["ops"] = [{"name": n, "ok": ok} for n, ok in _probe_ops()]
    except Exception:
        log_exc("_screen_page:ops 服务探活")  # C2批1
        out["ops"] = []
    try:
        import datetime as _dt

        def _pts(x):
            s = str(x).strip()
            try:
                if "T" in s:
                    t = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
                    if t.tzinfo:
                        t = t.astimezone().replace(tzinfo=None)
                    return t
                return _dt.datetime.strptime(s[:16], "%Y-%m-%d %H:%M")
            except Exception:
                return None

        cand = [(p["ts"], _pts(p["ts"])) for p in out.get("pulse") or []
                if p.get("ts")]
        cand = [(s0, t) for s0, t in cand if t]
        if cand:
            s0, t = max(cand, key=lambda x: x[1])
            out["ops_meta"] = {
                "last_task": next(p["name"] for p in out["pulse"]
                                  if p.get("ts") == s0),
                "last_ts": s0,
                "last_ago": _pulse_ago(s0),
                "server_now": _dt.datetime.now().strftime("%H:%M:%S")}
    except Exception:
        log_exc("_screen_page:ops_meta 最近任务锚")  # C2批1
        out["ops_meta"] = None

    # ── K65 财经快讯 + Jev 判断：news_collector/news.db 只读
    #    hit=1 = 关键词命中（有影响）；jev 列 = 本地模型结构化判断
    #    ai_briefs = 每日 AI 晨报（总览+多空因素）──
    try:
        import sqlite3 as _sq
        import datetime as _dt

        _ndb = os.path.join(STATION, "news_collector", "news.db")
        _con = _sq.connect("file:%s?mode=ro" % _ndb.replace("\\", "/"),
                           uri=True, timeout=2)
        _c = _con.cursor()
        _since = (_dt.datetime.now() - _dt.timedelta(hours=24)
                  ).strftime("%Y-%m-%d %H:%M")
        rows = list(_c.execute(
            "SELECT ts, source, title, stocks, jev FROM news "
            "WHERE hit=1 AND ts >= ? ORDER BY ts DESC LIMIT 60",
            (_since,)))
        news, n_bull, n_bear, n_neu, scopes = [], 0, 0, 0, {}
        for ts, src, title, stocks, jev in rows:
            j = {}
            try:
                j = json.loads(jev) if jev else {}
            except Exception:
                j = {}
            b = j.get("bias")
            if b == "bull":
                n_bull += 1
            elif b == "bear":
                n_bear += 1
            elif b == "neutral":
                n_neu += 1
            sc = j.get("scope_cn") or "未分类"
            scopes[sc] = scopes.get(sc, 0) + 1
            news.append({"ts": ts, "source": src, "title": title,
                         "stocks": stocks if stocks and stocks != "[]" else "",
                         "bias": j.get("bias_cn") or "",
                         "conf": j.get("conf"),
                         "scope": j.get("scope_cn") or "",
                         "reason": j.get("reason") or ""})
        out["news"] = news
        out["news_stats"] = {"n": len(rows), "bull": n_bull, "bear": n_bear,
                             "neutral": n_neu,
                             "scopes": sorted(scopes.items(),
                                              key=lambda x: -x[1])[:6]}
        try:
            br = list(_c.execute(
                "SELECT date, ts, brief FROM ai_briefs "
                "ORDER BY ts DESC LIMIT 1"))
            if br:
                out["news_brief"] = {"date": br[0][0], "ts": br[0][1],
                                     "brief": (br[0][2] or "")[:900]}
        except Exception:
            pass
        _con.close()
    except Exception:
        log_exc("_screen_page:news.db 快讯/晨报")  # C2批1
        out["news"] = []
        out["news_stats"] = None
        out.setdefault("news_brief", None)

    # ── 利空消息警示（大屏侧）：预警池/持仓股近5日高置信利空消息 ──
    #    口径同预警池页 _pool_bear_news（scope='cn' 守卫降噪）；
    #    回测 v2：偏空 T+1 跑输全市场 ~0.5pp，只提示不拦截
    out["bear_pool"] = []
    try:
        from collections import deque as _dq
        cn_map = {}
        with open(os.path.join(QBT_DIR, "data", "chanlun_pool.jsonl"),
                  encoding="utf-8") as _pf:
            for _ln in _dq(_pf, maxlen=200):
                _ln = _ln.strip()
                if not _ln:
                    continue
                try:
                    _r = json.loads(_ln)
                except Exception:
                    continue
                if _r.get("code"):
                    cn_map[_r["code"]] = (_r.get("name") or "").strip()
        for _p in (paper or {}).get("open") or []:
            if _p.get("code"):
                cn_map.setdefault(_p["code"], (_p.get("name") or "").strip())
        if cn_map:
            out["bear_pool"] = _pool_bear_news(cn_map)
    except Exception:
        log_exc("_screen_page:bear_pool 利空警示")  # C2批1
        out["bear_pool"] = []

    # ── K61 投委会锚（罗盘迷你卡消费；态势带第⑥路走 alerts 同源）
    #    K63 扩展：持仓管理明细 hold + 指令分布 dist（管理面板消费）
    try:
        co = dict(_council_stats() or {})
        opens = set()
        for sid, ac in ((out.get("portfolio") or {}).get("accounts")
                        or {}).items():
            for p in ac.get("open") or []:
                if p.get("code"):
                    opens.add((sid, p.get("code")))
        co["hold"] = _council_holdings(co, opens)
        co["dist"] = {}
        for h in co["hold"]:
            co["dist"][h["act"]] = co["dist"].get(h["act"], 0) + 1
        out["council"] = co
    except Exception:
        log_exc("_screen_page:council 投委会锚")  # C2批1
        out["council"] = None

    _screen_cache.update(t=now, d=out)
    return out


# ── K45 策略卡「模拟建仓」配置端点：capital/start_date 即时生效 ──
_pf_lock = threading.Lock()


def _portfolio_set(rec):
    """POST /api/portfolio_set {sid, capital?, start_date?}
    写 data/portfolio_config.json 后立即跑 daily_run（同日幂等）：
    capital 变更平滑注资/撤资即时生效；start_date 账户级建仓时间闸门。"""
    sid = str(rec.get("sid") or "")
    with _pf_lock:
        led = _read_json(os.path.join(REPORTS_DIR, "portfolio.json"))
        if sid not in (led.get("accounts") or {}):
            return {"ok": False, "error": "未知策略账户 sid=%s" % sid}
        cfg_fp = os.path.join(QBT_DIR, "data", "portfolio_config.json")
        cfg = _read_json(cfg_fp)
        ac_cfg = (cfg.setdefault("accounts", {}).setdefault(
            sid, {"enabled": True, "capital": 1_000_000, "max_pos": 20}))
        # K46 撤销历史：先取变更前快照（入栈在确认 changed 后）
        prev = {"sid": sid, "capital": ac_cfg.get("capital"),
                "start_date": ac_cfg.get("start_date") or "",
                "stop_loss_pct": ac_cfg.get("stop_loss_pct"),
                "take_profit_pct": ac_cfg.get("take_profit_pct"),
                "max_pos": ac_cfg.get("max_pos"),
                "max_drawdown_pct": ac_cfg.get("max_drawdown_pct")}
        changed = []
        if rec.get("capital") is not None:
            try:
                cap = int(rec["capital"])
            except (TypeError, ValueError):
                return {"ok": False, "error": "capital 需为整数（元）"}
            if not 10_000 <= cap <= 100_000_000:
                return {"ok": False, "error": "capital 越界（1万~1亿）"}
            if cap != ac_cfg.get("capital"):
                ac_cfg["capital"] = cap
                changed.append("capital=%d" % cap)
        if rec.get("start_date") is not None:
            sd = str(rec["start_date"]).strip()
            if sd and (len(sd) != 10 or sd[4] != "-" or sd[7] != "-"
                       or not sd.replace("-", "").isdigit()):
                return {"ok": False, "error": "start_date 需为 YYYY-MM-DD 或空"}
            if sd != (ac_cfg.get("start_date") or ""):
                ac_cfg["start_date"] = sd
                changed.append("start_date=%s" % (sd or "(立即)"))
        # K50 止盈止损阈值（%相对成本价；0=停用；账户级优先全局回退）
        for k, lab in (("stop_loss_pct", "止损"), ("take_profit_pct", "止盈")):
            if rec.get(k) is not None:
                try:
                    v = round(float(rec[k]), 1)
                except (TypeError, ValueError):
                    return {"ok": False, "error": "%s 需为数值（%%）" % lab}
                if not 0 <= v <= 50:
                    return {"ok": False, "error": "%s 越界（0~50%%，0=停用）" % lab}
                if v != ac_cfg.get(k):
                    ac_cfg[k] = v
                    changed.append("%s=%.1f%%" % (lab, v))
        # K51-C 回撤熔断阈值（0=停用；账户级优先全局回退）
        if rec.get("max_drawdown_pct") is not None:
            try:
                v = round(float(rec["max_drawdown_pct"]), 1)
            except (TypeError, ValueError):
                return {"ok": False, "error": "熔断需为数值（%）"}
            if not 0 <= v <= 50:
                return {"ok": False, "error": "熔断越界（0~50%，0=停用）"}
            if v != ac_cfg.get("max_drawdown_pct"):
                ac_cfg["max_drawdown_pct"] = v
                changed.append("熔断=%.1f%%" % v)
        # K51-A 持仓数上限
        if rec.get("max_pos") is not None:
            try:
                v = int(rec["max_pos"])
            except (TypeError, ValueError):
                return {"ok": False, "error": "持仓上限需为整数"}
            if not 1 <= v <= 50:
                return {"ok": False, "error": "持仓上限越界（1~50）"}
            if v != ac_cfg.get("max_pos"):
                ac_cfg["max_pos"] = v
                changed.append("持仓上限=%d" % v)
        if not changed:
            return {"ok": True, "changed": [], "note": "无变更",
                    "account": _pf_summary(sid)}
        # K46 撤销历史：入栈变更前快照（仅记 API 变更；CLI --set 不入史）
        cfg.setdefault("_history", []).append(dict(
            ts=time.strftime("%Y-%m-%d %H:%M:%S"), **prev))
        cfg["_history"] = cfg["_history"][-20:]
        tmp = cfg_fp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        os.replace(tmp, cfg_fp)
        tail, err = _pf_apply(sid)
        if err:
            return {"ok": False, "error": err}
        return {"ok": True, "sid": sid, "changed": changed,
                "run_tail": tail, "account": _pf_summary(sid)}


def _pf_apply(sid):
    """K45/K46 配置生效公共段：立即跑一次跑批（同日幂等：只应用资金
    注资/撤资，不推进交易日）→ 清大屏缓存。返回 (run_tail, err)。"""
    try:
        r = subprocess.run(
            [sys.executable, "-X", "utf8", "portfolio.py"],
            cwd=QBT_DIR, capture_output=True, text=True,
            timeout=180, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            return None, "跑批失败 rc=%d: %s" % (
                r.returncode, (r.stdout or r.stderr or "")[-300:])
    except subprocess.TimeoutExpired:
        return None, "跑批超时（180s）"
    _screen_cache["t"] = 0.0              # 配置已变，清大屏缓存
    return (r.stdout or "").strip().splitlines()[-2:], None


def _portfolio_undo():
    """POST /api/portfolio_undo：弹栈最后一笔 API 配置变更并恢复
    （capital/start_date 回旧值，资金走平滑注资/撤资；连续撤销沿历史回退，
    历史弹空即止，无 redo）。"""
    with _pf_lock:
        cfg_fp = os.path.join(QBT_DIR, "data", "portfolio_config.json")
        cfg = _read_json(cfg_fp)
        hist = cfg.get("_history") or []
        if not hist:
            return {"ok": False, "error": "无可撤销的配置变更"}
        last = hist.pop()
        sid = last.get("sid") or ""
        ac_cfg = (cfg.get("accounts") or {}).get(sid)
        if not ac_cfg:
            return {"ok": False, "error": "历史条目账户 %s 已不存在" % sid}
        restored = []
        # K50 修复：只恢复并显示「值实际变化」的字段；
        # 快照值为 None 表示当时账户级未设置 → 删键回退全局默认
        if last.get("capital") is not None and \
                ac_cfg.get("capital") != last["capital"]:
            ac_cfg["capital"] = int(last["capital"])
            restored.append("资金=%d万" % (ac_cfg["capital"] // 10000))
        if last.get("start_date") is not None and \
                (ac_cfg.get("start_date") or "") != last["start_date"]:
            ac_cfg["start_date"] = last["start_date"]
            restored.append("起始日=%s" % (last["start_date"] or "(立即)"))
        for k, lab in (("stop_loss_pct", "止损"), ("take_profit_pct", "止盈"),
                       ("max_drawdown_pct", "熔断")):
            if last.get(k) is None:
                if k in ac_cfg:
                    del ac_cfg[k]
                    restored.append("%s=(默认)" % lab)
            elif ac_cfg.get(k) != last[k]:
                ac_cfg[k] = float(last[k])
                restored.append("%s=%.1f%%" % (lab, ac_cfg[k]))
        if last.get("max_pos") is not None and \
                ac_cfg.get("max_pos") != last["max_pos"]:
            ac_cfg["max_pos"] = int(last["max_pos"])
            restored.append("持仓上限=%d" % ac_cfg["max_pos"])
        if not restored:
            restored.append("（快照与当前一致）")
        cfg["_history"] = hist
        tmp = cfg_fp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        os.replace(tmp, cfg_fp)
        tail, err = _pf_apply(sid)
        if err:
            return {"ok": False, "error": err}
        return {"ok": True, "sid": sid, "restored": restored,
                "undo_ts": last.get("ts", ""), "n_left": len(hist),
                "run_tail": tail, "account": _pf_summary(sid)}


def _portfolio_close(rec):
    """POST /api/portfolio_close {sid, code?}
    K50-D 手动平仓：code 为空/all=清仓整个账户，否则平单只。
    按源快照最新价成交，reason=手动平仓（不挡该标的日后重建与否由
    rebuild 语义决定：手动平仓无 rebuild → 该标的不再自动重建）。"""
    sid = str(rec.get("sid") or "")
    code = str(rec.get("code") or "").strip()
    with _pf_lock:
        led = _read_json(os.path.join(REPORTS_DIR, "portfolio.json"))
        if sid not in (led.get("accounts") or {}):
            return {"ok": False, "error": "未知策略账户 sid=%s" % sid}
        arg = sid if (not code or code.lower() == "all") else "%s.%s" % (sid, code)
        try:
            r = subprocess.run(
                [sys.executable, "-X", "utf8", "portfolio.py", "--close", arg],
                cwd=QBT_DIR, capture_output=True, text=True,
                timeout=120, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "平仓超时（120s）"}
        out = (r.stdout or "").strip()
        if r.returncode != 0 or "无可平" in out or "不存在" in out:
            return {"ok": False, "error": out[-200:] or "rc=%d" % r.returncode}
        _screen_cache["t"] = 0.0
        return {"ok": True, "sid": sid, "code": code or None,
                "run_tail": out.splitlines()[-3:], "account": _pf_summary(sid)}


def _pf_reason_cat(r):
    """K51-B 平仓原因归类（复盘统计口径）：止损/止盈/闸门/翻空/离池/
    到期/手动/跟随源/其他。"""
    r = r or ""
    for pre, cat in (("止损", "止损"), ("止盈", "止盈"), ("环境闸门", "闸门"),
                     ("信号翻空", "翻空"), ("离池", "离池"), ("持有", "到期"),
                     ("手动", "手动"), ("源账本", "跟随源"), ("撤资", "其他")):
        if r.startswith(pre):
            return cat
    return "其他"


def _pf_csv(sid=""):
    """K51-B 交易明细 CSV：平仓+加减仓流水（按日期混排）+当前持仓快照。
    utf-8-sig 带 BOM（Excel 直开不乱码）。"""
    import csv
    import io
    led = _read_json(os.path.join(REPORTS_DIR, "portfolio.json"))
    rows = []
    for _sid, ac in (led.get("accounts") or {}).items():
        if sid and _sid != sid:
            continue
        for c in ac.get("closed") or []:
            rows.append([c.get("exit_date", ""), _sid, c.get("name", ""),
                         c.get("market", ""), c.get("reason", ""),
                         c.get("exit_px", ""), c.get("qty", ""),
                         round((c.get("qty") or 0) * (c.get("exit_px") or 0), 2),
                         c.get("exit_pnl_pct", ""), c.get("hold_days", "")])
        for t in ac.get("trades") or []:
            rows.append([t.get("d", ""), _sid, t.get("name", ""), "",
                         t.get("act", ""), t.get("px", ""), "",
                         t.get("amount", ""), t.get("pct", ""), ""])
    rows.sort(key=lambda r: r[0])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["日期", "账户", "标的", "市场", "动作", "价格",
                "数量", "金额/市值", "盈亏%/比例", "持有日"])
    w.writerows(rows)
    w.writerow([])
    w.writerow(["当前持仓", "", "", "", "", "", "", "", "", ""])
    w.writerow(["建仓日", "账户", "标的", "市场", "备注", "现价",
                "数量", "成本", "浮盈%", "持有日"])
    for _sid, ac in (led.get("accounts") or {}).items():
        if sid and _sid != sid:
            continue
        for p in ac.get("open") or []:
            w.writerow([p.get("entry_date", ""), _sid, p.get("name", ""),
                        p.get("market", ""), p.get("note", ""),
                        p.get("last_px", ""), p.get("qty", ""),
                        p.get("cost", ""), p.get("pnl_pct", ""),
                        p.get("hold_days", "")])
    return buf.getvalue().encode("utf-8-sig")


def _portfolio_adjust(rec):
    """POST /api/portfolio_adjust {sid, code, act:"add"|"trim", pct}
    K51-A 手动加/减仓：add=按现价加仓（规模=持仓市值×pct%）、
    trim=卖出持仓数量 pct%（≥100 全平）。subprocess 调引擎 CLI。"""
    sid = str(rec.get("sid") or "")
    code = str(rec.get("code") or "").strip()
    act = str(rec.get("act") or "").strip().lower()
    try:
        pct = float(rec.get("pct"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "pct 需为数值（%）"}
    if act not in ("add", "trim"):
        return {"ok": False, "error": "act 需为 add/trim"}
    if not 1 <= pct <= 100:
        return {"ok": False, "error": "pct 越界（1~100%）"}
    with _pf_lock:
        led = _read_json(os.path.join(REPORTS_DIR, "portfolio.json"))
        if sid not in (led.get("accounts") or {}):
            return {"ok": False, "error": "未知策略账户 sid=%s" % sid}
        if not any(p.get("code") == code for p in
                   (led["accounts"][sid].get("open") or [])):
            return {"ok": False, "error": "该账户无此持仓：%s" % code}
        try:
            r = subprocess.run(
                [sys.executable, "-X", "utf8", "portfolio.py", "--adjust",
                 "%s.%s,%s,%g" % (sid, code, act, pct)],
                cwd=QBT_DIR, capture_output=True, text=True,
                timeout=120, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "加减仓超时（120s）"}
        out = (r.stdout or "").strip()
        if r.returncode != 0 or ("不足" in out or "无可调" in out):
            return {"ok": False, "error": out[-200:] or "rc=%d" % r.returncode}
        _screen_cache["t"] = 0.0
        return {"ok": True, "sid": sid, "code": code, "act": act, "pct": pct,
                "run_tail": out.splitlines()[-2:], "account": _pf_summary(sid)}


def _portfolio_open(rec):
    """POST /api/portfolio_open {sid, code, amount?}
    K51-D 手动建仓：跨策略快照寻价按当前价成交；用户意志优先（不受
    闸门/熔断/起始日/拉黑限制），仅守现金与 max_pos；amount 缺省=等权。"""
    sid = str(rec.get("sid") or "")
    code = str(rec.get("code") or "").strip()
    amount = rec.get("amount")
    with _pf_lock:
        led = _read_json(os.path.join(REPORTS_DIR, "portfolio.json"))
        if sid not in (led.get("accounts") or {}):
            return {"ok": False, "error": "未知策略账户 sid=%s" % sid}
        if not code:
            return {"ok": False, "error": "code 不能为空"}
        if amount is not None:
            try:
                amount = round(float(amount), 2)
            except (TypeError, ValueError):
                return {"ok": False, "error": "amount 需为数值（元）"}
            if not 1_000 <= amount <= 10_000_000:
                return {"ok": False, "error": "amount 越界（1千~1千万）"}
        arg = "%s.%s" % (sid, code)
        if amount:
            arg += ",%d" % int(amount)
        try:
            r = subprocess.run(
                [sys.executable, "-X", "utf8", "portfolio.py", "--open", arg],
                cwd=QBT_DIR, capture_output=True, text=True,
                timeout=120, encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "建仓超时（120s）"}
        out = (r.stdout or "").strip()
        if r.returncode != 0 or "OPEN 手动" not in out:
            return {"ok": False, "error": out[-200:] or "rc=%d" % r.returncode}
        _screen_cache["t"] = 0.0
        return {"ok": True, "sid": sid, "code": code,
                "run_tail": out.splitlines()[-2:], "account": _pf_summary(sid)}




def _council_confirm(rec):
    """POST /api/council/confirm {sid, code, op:"confirm"|"dismiss"}
    K64d 半自动执行链（预警票）+ K67 系数建议票，人工确认后执行——
      · 平仓预警 → portfolio.py --close sid.code（全平）
      · 减仓预警 → --adjust sid.code,trim,50（减半）
      · 加仓建议（k≥1.1 激进同向）→ --adjust sid.code,add,pct
      · 减仓建议（k≤0.9 保守同向/偏空）→ --adjust sid.code,trim,pct
        （pct=|k-1|×100，入队时算好存票面）
    dismiss=忽略并出队。执行/忽略均从 pending 移除；铁律：无确认不动仓。"""
    sid = str(rec.get("sid") or "")
    code = str(rec.get("code") or "").strip()
    op = str(rec.get("op") or "").strip().lower()
    if op not in ("confirm", "dismiss"):
        return {"ok": False, "error": "op 需为 confirm/dismiss"}
    key = "%s|%s" % (sid, code)
    with _pf_lock:
        pend = _council_pending()
        item = pend.get(key)
        if not item:
            return {"ok": False, "error": "队列无此票：%s" % key}
        act = item.get("act") or ""
        res = {"ok": True, "op": op, "sid": sid, "code": code, "act": act}
        if op == "confirm":
            if act == "平仓预警":
                cmd = ["--close", "%s.%s" % (sid, code)]
            elif act in ("减仓预警", "减仓建议", "加仓建议"):
                try:
                    pct = int(item.get("pct")
                              or (50 if act == "减仓预警" else 10))
                except (TypeError, ValueError):
                    pct = 50 if act == "减仓预警" else 10
                pct = max(1, min(100, pct))
                cmd = ["--adjust", "%s.%s,%s,%d"
                       % (sid, code,
                          "add" if act == "加仓建议" else "trim", pct)]
            else:
                return {"ok": False, "error": "非可执行票：%s" % act}
            try:
                r = subprocess.run(
                    [sys.executable, "-X", "utf8", "portfolio.py"] + cmd,
                    cwd=QBT_DIR, capture_output=True, text=True,
                    timeout=120, encoding="utf-8", errors="replace")
            except subprocess.TimeoutExpired:
                return {"ok": False, "error": "执行超时（120s），票留队列"}
            out = (r.stdout or "").strip()
            bad = r.returncode != 0 or any(w in out for w in
                                           ("无可平", "不存在", "不足",
                                            "无可调"))
            if bad:
                return {"ok": False, "error": out[-200:] or "rc=%d"
                        % r.returncode}
            res["run_tail"] = out.splitlines()[-2:]
            res["account"] = _pf_summary(sid)
            _screen_cache["t"] = 0.0
        pend.pop(key, None)
        try:
            with open(os.path.join(REPORTS_DIR, "council_pending.json"),
                      "w", encoding="utf-8") as f:
                json.dump(pend, f, ensure_ascii=False, indent=1)
        except Exception as ex:
            res["warn"] = "队列回写失败：%s" % str(ex)[:80]
        return res


_SIMRUN = {"p": None,
           "log": os.path.join(r"C:\Temp", "portal_council_sim.log")}


def _council_simrun(rec):
    """POST /api/council/simrun {op:"start"|"status"}
    K69 模拟投委会预演：后台跑 council_sim.py（LLM 六视角辩论，每票
    约几十秒），start 立即返回不阻塞；status 轮询进程+日志尾部。"""
    op = str(rec.get("op") or "start").strip().lower()
    if op == "status":
        p = _SIMRUN.get("p")
        running = bool(p and p.poll() is None)
        tail = ""
        try:
            if os.path.exists(_SIMRUN["log"]):
                with open(_SIMRUN["log"], "rb") as f:
                    f.seek(0, 2)
                    sz = f.tell()
                    f.seek(max(0, sz - 3000))
                    tail = f.read().decode("utf-8", "replace").strip()[-800:]
        except Exception:
            pass
        return {"ok": True, "running": running, "tail": tail}
    if op != "start":
        return {"ok": False, "error": "op 需为 start/status"}
    p = _SIMRUN.get("p")
    if p and p.poll() is None:
        return {"ok": False, "error": "预演进行中，请稍候刷新"}
    try:
        with open(_SIMRUN["log"], "ab") as lf:
            lf.write(("\n===== %s sim council =====\n"
                      % time.strftime("%Y-%m-%d %H:%M:%S")).encode())
            _SIMRUN["p"] = subprocess.Popen(
                [sys.executable, "-X", "utf8", "council_sim.py"],
                stdout=lf, stderr=lf, cwd=QBT_DIR, creationflags=NO_WINDOW)
        return {"ok": True, "started": True, "pid": _SIMRUN["p"].pid}
    except Exception as ex:
        return {"ok": False, "error": str(ex)}


def _debate_flow(r):
    """K74 辩论时间线：五幕 + 老板插话轮 → [(who, txt, tag, cls)]
    cls: host 分析师蓝 / trader 操盘手黄 / vote 绿 / final 红 / human 紫"""
    flow = []
    m0 = r.get("memo") or {}
    mtxt = ["%s：%s" % (lab, m0[k])
            for k, lab in (("thesis", "论点"), ("risks", "风险"),
                           ("if_wrong", "错了怎么办")) if m0.get(k)]
    if mtxt:
        flow.append(("主持人 · 分析师纪要", "\n".join(mtxt),
                     "信度 %s" % m0.get("conviction", "?"), "host"))
    vt = r.get("vote") or {}
    dr = r.get("directive") or {}
    flow.append(("加权投票", "六视角加权 %+.1f → 票%s；指令 %s（系数 ×%.2f）"
                 % (vt.get("wsum") or 0, vt.get("verdict"),
                    dr.get("act") or "—",
                    (dr.get("sizing") or {}).get("k", 1.0)),
                 vt.get("verdict") or "", "vote"))
    for v in r.get("traders") or []:
        if v.get("answer"):
            flow.append(("%s · 质询" % v.get("name"), v["answer"],
                         "%+d" % (v.get("stance") or 0), "trader"))
    rb = (r.get("rebuttal") or {}).get("answer")
    if rb:
        flow.append(("主持人 · 分析师回应", rb, "", "host"))
    for v in r.get("traders") or []:
        if v.get("answer2"):
            st2, st1 = v.get("stance2"), (v.get("stance") or 0)
            tag = ("%+d→%+d" % (st1, st2)
                   if st2 is not None and st2 != st1
                   else "%+d" % (st2 if st2 is not None else st1))
            flow.append(("%s · 再表态" % v.get("name"), v["answer2"],
                         tag, "trader"))
    fin = r.get("final") or {}
    if fin.get("note"):
        flow.append(("终裁", fin["note"], fin.get("verdict") or "", "final"))
    for h in r.get("human") or []:
        flow.append(("老板 · 插话", h.get("text") or "",
                     (h.get("ts") or "")[:16], "human"))
        for rp in h.get("replies") or []:
            st3 = rp.get("stance3")
            tag3 = ("%+d 插话后" % st3 if st3 else
                    "±0 插话后" if st3 == 0 else "")
            flow.append(("%s · 回应老板" % rp.get("who"),
                         rp.get("answer") or "", tag3,
                         "trader" if "操盘手" in (rp.get("who") or "")
                         else "host"))
        f2 = h.get("final2") or {}
        if f2.get("note"):
            flow.append(("终裁 · 插话后重审", f2["note"],
                         f2.get("verdict") or "", "final2"))
    return flow


def _debate_say(rec):
    """POST /api/debate/say {p, text}
    K74 老板插话：主持人（代分析师团）+ 三位操盘手并发回应（LLM
    直调，约 10-20s），追加到该场预演留痕 human 轮。"""
    import time as _t
    from concurrent.futures import ThreadPoolExecutor
    simj = os.path.join(REPORTS_DIR, "council_sim_proposals.jsonl")
    try:
        with open(simj, encoding="utf-8") as f:
            props = [json.loads(x) for x in f if x.strip()]
    except Exception:
        return {"error": "尚无预演留痕，请先在 /sim 发起预演"}
    rev = props[::-1]
    try:
        i = int(rec.get("p"))
    except Exception:
        return {"error": "场次参数无效"}
    txt = str(rec.get("text") or "").strip()[:500]
    if not (0 <= i < len(rev)):
        return {"error": "场次不存在"}
    if not txt:
        return {"error": "发言不能为空"}
    r = rev[i]
    sys.path.insert(0, QBT_DIR)
    try:
        from council_sim import TRADER_SPECS
        from stock_council import _llm, _STANCE_RE
    except Exception as ex:
        return {"error": "引擎导入失败：%s" % str(ex)[:80]}
    vt, dr, fin = r.get("vote") or {}, r.get("directive") or {}, \
        r.get("final") or {}
    base = ("【标的】%s %s\n【分析师结论】票%s（加权 %+.1f），指令 %s，"
            "系数 ×%.2f\n【终裁】%s\n【老板（雇主）在辩论中插话】%s\n"
            "请以你的身份直接回应老板的观点：2-4 句中文，可以附和、可以"
            "坚持己见、可以提出折中，但要有依据。"
            % (r.get("code"), r.get("name") or "", vt.get("verdict"),
               vt.get("wsum") or 0, dr.get("act") or "—",
               (dr.get("sizing") or {}).get("k", 1.0),
               fin.get("verdict") or "—", txt))

    def _one(who, sysc):
        try:
            ans = (_llm([{"role": "system", "content": sysc},
                         {"role": "user", "content": base}])
                   .get("content") or "").strip()
        except Exception as ex:
            ans = "（回应失败：%s）" % str(ex)[:60]
        return {"who": who, "answer": ans}

    jobs = [("主持人（分析师团队）",
             "你是模拟投委会主持人，代表分析师团队回应老板的插话。"
             "诚实评估老板观点对结论的影响，不为迎合而改变立场。")]
    for rk, nm in (("aggr", "激进操盘手"), ("cons", "保守操盘手"),
                   ("neut", "中立操盘手")):
        jobs.append((nm, TRADER_SPECS[rk]["system"]))
    with ThreadPoolExecutor(max_workers=4) as ex:
        reps = list(ex.map(lambda j: _one(j[0], j[1]), jobs))
    # K75 插话后立场重测：操盘手回应自带 STANCE（人设指令），直接解析
    for rp in reps:
        if "操盘手" in rp["who"]:
            m = _STANCE_RE.search(rp["answer"])
            rp["stance3"] = (max(-2, min(2, int(m.group(1))))
                             if m else None)
    # K75 插话后终裁重测：老板的话是否改变终裁（预演层展示，不动真实链路）
    fin0 = r.get("final") or {}
    fin2 = {}
    try:
        u2 = ("【标的】%s %s\n【原分析师结论】票%s（%+.1f）→ 原终裁 %s\n"
              "【老板插话】%s\n【四方回应】%s\n"
              "请重审终裁：老板的观点是否成立？是否修正终裁立场或指令"
              "建议？2-4 句中文说明，最后单独一行 STANCE:n（-2..+2，"
              "插话后终裁立场）。"
              % (r.get("code"), r.get("name") or "",
                 vt.get("verdict"), vt.get("wsum") or 0,
                 fin0.get("verdict") or "—", txt,
                 "\n".join("· %s：%s" % (rp["who"],
                                         rp["answer"].replace("\n", " ")
                                         [:200]) for rp in reps)))
        a2 = (_llm([{"role": "system",
                     "content": ("你是模拟投委会终裁主持人，现在老板（雇主）"
                                 "插话后重审终裁。诚实评估：老板观点成立就"
                                 "吸收，不成立就维持并说明——不为迎合而改判，"
                                 "也不为姿态而固执。")},
                    {"role": "user", "content": u2}])
              .get("content") or "").strip()
        m2 = _STANCE_RE.search(a2)
        s2 = (max(-2, min(2, int(m2.group(1)))) if m2 else None)
        fin2 = {"stance": s2,
                "verdict": ("支持" if (s2 or 0) >= 1 else
                            "反对" if (s2 or 0) <= -1 else "中性"),
                "note": a2}
    except Exception as ex:
        fin2 = {"stance": None, "verdict": "重测失败", "note": str(ex)[:80]}
    # K118 写时合并：上方读全文件后经 LLM 并发（10-40s 窗口），期间
    # 重跑 append 的新行会被旧快照全量重写吞掉——改为独立锁文件内
    # 「重读最新 + 按 (code,d,ts) 锚点定位本场 + 追加本轮 + 整体写回」
    # （锁文件独立于数据文件：写回用 os.replace，数据文件本体不能持锁）
    new_human = {"text": txt, "ts": _t.strftime("%Y-%m-%d %H:%M:%S"),
                 "replies": reps, "final2": fin2}
    import msvcrt
    lk = open(simj + ".lock", "a+b")
    try:
        lk.seek(0)
        msvcrt.locking(lk.fileno(), msvcrt.LK_LOCK, 1)
        try:
            with open(simj, encoding="utf-8") as f:
                fresh = [json.loads(x) for x in f if x.strip()]
            tgt = None
            for x in fresh:
                if (x.get("code") == r.get("code")
                        and x.get("d") == r.get("d")
                        and x.get("ts") == r.get("ts")):
                    tgt = x
                    break
            if tgt is None:
                return {"error": "该场已被覆盖（可能发生了重跑），"
                                 "请刷新列表后重试"}
            tgt.setdefault("human", []).append(new_human)
            tmp = simj + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for x in fresh:
                    f.write(json.dumps(x, ensure_ascii=False) + "\n")
            os.replace(tmp, simj)
        finally:
            lk.seek(0)
            msvcrt.locking(lk.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        lk.close()
    return {"ok": True, "replies": reps}


_mkt_tbl = {"t": 0.0, "d": None}


def _portfolio_market(qs):
    """GET /api/portfolio_market?q= 关键词
    K52-D 手动建仓标的搜索：subprocess 拉引擎全表（300s 缓存），
    之后内存按 code/name 过滤（q<2 字符返回空，前端防抖后调用）。"""
    from urllib.parse import parse_qs as _pqc
    q = ((_pqc(qs).get("q") or [""])[0] or "").strip().lower()
    if len(q) < 2:
        return {"ok": True, "n_total": 0, "items": []}
    now = time.time()
    if _mkt_tbl["d"] is None or now - _mkt_tbl["t"] > 300:
        try:
            r = subprocess.run(
                [sys.executable, "-X", "utf8", "portfolio.py", "--market", ""],
                cwd=QBT_DIR, capture_output=True, text=True,
                timeout=120, encoding="utf-8", errors="replace")
            _mkt_tbl.update(t=now, d=json.loads((r.stdout or "[]").strip()))
        except Exception as e:
            return {"ok": False, "error": "行情快照加载失败：%s" % e}
    hits = [x for x in (_mkt_tbl["d"] or [])
            if q in (x.get("code") or "").lower()
            or q in (x.get("name") or "").lower()][:20]
    return {"ok": True, "n_total": len(_mkt_tbl["d"] or []), "items": hits}


def _pf_summary(sid):
    """配置生效后读回该账户摘要（现金/净值/持仓数）供前端反馈。"""
    led = _read_json(os.path.join(REPORTS_DIR, "portfolio.json"))
    ac = (led.get("accounts") or {}).get(sid) or {}
    curve = ac.get("nav_curve") or []
    return {"capital": ac.get("capital"), "cash": ac.get("cash"),
            "n_open": len(ac.get("open") or []),
            "nav": curve[-1]["nav"] if curve else ac.get("capital")}














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


# ── 策略资产注册表（策略中心/首页组合区/导航单一数据源，K36）──
# 上新三步（详见 /strategies 页 SOP）：strategies.json 加条目 →
# 策略页面挂路由 → 计划任务上线；本表驱动三处 UI，前端零改动。
STRATEGIES_FILE = os.path.join(STATION, "portal", "strategies.json")
_STRAT_MTIME = {"t": 0.0, "data": None}


def strategies_data():
    """注册表（mtime 缓存）+ 轻量实时补充：weak 在案数。"""
    try:
        mt = os.path.getmtime(STRATEGIES_FILE)
    except OSError:
        mt = 0.0
    if _STRAT_MTIME["data"] is None or mt != _STRAT_MTIME["t"]:
        reg = _read_json(STRATEGIES_FILE)
        _STRAT_MTIME.update(t=mt, data=reg)
    reg = _STRAT_MTIME["data"]
    # 深拷贝 metrics：否则 insert 会写进缓存里的共享 list，随调用次数累积重复
    items = [dict(s, metrics=list(s.get("metrics") or []))
             for s in (reg.get("strategies") or [])]
    # weak 在案数 = 状态流 jsonl 计数（在案即有跟踪流）
    try:
        act = len([f for f in os.listdir(os.path.join(STATION, "reports"))
                   if f.startswith("weak_track_") and f.endswith(".jsonl")])
    except OSError:
        act = None
    for s in items:
        if s.get("id") == "weak" and act is not None:
            s.setdefault("metrics", []).insert(0, {"k": "在案", "v": "%d 只" % act})
    return {"ts": time.strftime("%Y-%m-%d %H:%M"),
            "meta": reg.get("_meta") or {},
            "strategies": items}


# ── 个股分析（极简入口：一个输入框，后台跑 analyze_detail） ──
SA_JOBS = {}
SA_BUSY = {"v": False}
SA_HIST = os.path.join(STATION, "news_collector", "sa_history.jsonl")


def _sa_hist_append(rec):
    try:
        os.makedirs(os.path.dirname(SA_HIST), exist_ok=True)
        with open(SA_HIST, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _sa_hist_read(n=8):
    try:
        with open(SA_HIST, encoding="utf-8") as f:
            lines = [x for x in f.read().splitlines() if x.strip()]
        return [json.loads(x) for x in lines[-n:]][::-1]
    except Exception:
        log_exc("_sa_hist_read:sa_history.jsonl")  # C2批1
        return []








_TS_DAILY_DF = None


def _ts_daily_prepend(ts_code, first_dt, need):
    """tdx ts_daily 取 first_dt 之前的收盘价尾部 need 根（MA 历史兜底）。

    返回 [(YYYYMMDD, close)...] 升序；全量 df 进程级缓存（首读 ~1.5s）。
    """
    global _TS_DAILY_DF
    try:
        import pandas as pd
        if _TS_DAILY_DF is None:
            import glob as _glob
            fs = sorted(_glob.glob(os.path.join(
                STATION, "quant_backtest", "data", "tdx", "ts_daily",
                "*.parquet")))
            _TS_DAILY_DF = pd.concat(
                [pd.read_parquet(f, columns=["ts_code", "trade_date",
                                              "close"]) for f in fs],
                ignore_index=True)
        fd = first_dt.strftime("%Y%m%d")
        d = _TS_DAILY_DF[_TS_DAILY_DF["ts_code"] == ts_code]
        d = d[d["trade_date"].astype(str) < fd].sort_values("trade_date")
        d = d.tail(need)
        return list(zip(d["trade_date"].astype(str),
                        d["close"].astype(float)))
    except Exception:
        return []


def _kline_data(q, n=250):
    """个股 K 线（S1）：四市场日线尾部 n 根 + MA5/20/60/250 + A股图谱结构
    信号标注 + JEV 新闻 bias 标注。resolve → load_bars，纯查询。"""
    n = max(30, min(500, int(n or 250)))
    e = _bt_resolve(q)
    if not e:
        return {"ok": False, "err": "未识别「%s」" % q}
    name, code = e["name"], e.get("code", "")
    aliases = e.get("aliases") or []
    # A股 ts_code（结构信号标注与 MA 历史兜底共用）
    import re as _re_k
    m6 = _re_k.search(r"(\d{6})", code or "")
    ts_code = ""
    if m6:
        d6 = m6.group(1)
        ts_code = (d6 + ".SH" if d6[0] == "6" else d6 + ".SZ"
                   if d6[0] in "03" else d6 + ".BJ" if d6[0] in "48" else "")
    bars = []
    try:
        from datetime import datetime as _dt, timedelta as _td
        sys.path.insert(0, os.path.join(STATION, "quant_backtest"))
        import runner as _R
        import data_loader as _D
        # MA250 需视图外 ≥250 根历史：窗口 ≈ 视图 1.6 倍 + 420 自然日
        start = (_dt.now() - _td(days=int(n * 1.6) + 420)
                 ).strftime("%Y-%m-%d")
        for mk, sy, _nm in _R.resolve_watchlist(
                [{"name": name, "code": code, "aliases": aliases}]):
            df = _D.load_bars(mk, [sy], start, None).get(sy)
            if df is not None and len(df):
                # K152 排序强保证：parquet 原序不可控，统一按日期升序
                # （日/周/月三级别与 MA/MACD 全链路的顺序根基）
                bars = df.sort_index()
                break
    except Exception:
        log_exc("_kline_data:load_bars 日线加载 %s/%s" % (name, code))  # C2批1
        bars = []
    if bars is None or not len(bars):
        return {"ok": False, "err": "无日线数据（%s · %s）" % (name, code)}
    # MA 先全量算（保证切尾后头部均线完整），再取尾部 n 根。
    # 本地日线池短于 250+n 根时（ashare 池 2025-02 起），用 tdx ts_daily
    # 收盘价向前补（两源同口径不复权，实测逐日一致）
    closes = list(bars["close"])
    pre = []
    if ts_code and len(closes) - n < 250:
        pre = _ts_daily_prepend(ts_code, bars.index[0], 300)
        if pre:
            closes = [c for _dd, c in pre] + closes
    mas = {k: [None] * len(closes) for k in (5, 20, 60, 250)}
    for k in (5, 20, 60, 250):
        s = 0.0
        for i, c in enumerate(closes):
            s += c
            if i >= k:
                s -= closes[i - k]
            if i >= k - 1:
                mas[k][i] = round(s / k, 3)
    off = len(pre)
    out = []
    idxs = list(bars.index)
    for j, i in enumerate(idxs):
        out.append({"d": i.strftime("%Y-%m-%d"),
                    "o": round(float(bars.at[i, "open"]), 3),
                    "h": round(float(bars.at[i, "high"]), 3),
                    "l": round(float(bars.at[i, "low"]), 3),
                    "c": round(float(bars.at[i, "close"]), 3),
                    "v": float(bars.at[i, "volume"] or 0),
                    "m5": mas[5][j + off], "m20": mas[20][j + off],
                    "m60": mas[60][j + off], "m250": mas[250][j + off]})
    out = out[-n:]
    # A股：图谱结构信号标注（缠论买卖点，kg_portal 纯查询）
    marks = []
    if ts_code:
        try:
            import kg_portal as _KP
            st = _KP.stock(ts_code, days=n)
            for s in (st.get("structures") or [])[:40]:
                marks.append({"d": s.get("date") or "",
                              "t": s.get("type") or "",
                              "c": round(s.get("conf") or 0, 2)})
        except Exception:
            pass
    # JEV 新闻 bias 标注（按日期去重，同日多条取 conf 最高）
    news = {}
    for it in _bt_jev_news(name, aliases):
        d = (it.get("ts") or "")[:10]
        if d and (d not in news or it["conf"] > news[d]["c"]):
            news[d] = {"c": it["conf"], "b": it["bias_cn"]}
    # K136 笔中枢上墙：全量窗 P3 结构（chanlun_bi，四市场纯几何，
    # 不限 A股）；dt=原始 K 线日期，前端按视窗类目自动过滤
    # K139 周线级别：ISO 周一 groupby 重采样，dt=周内最后交易日
    # （与前端 _wk() 的 w.d 口径对齐），analyze 出 wbis/wpivots
    bis, pivots, wbis, wpivots = [], [], [], []
    mbis, mpivots, fx, wfx, mfx = [], [], [], [], []
    sgs, wsgs, msgs = [], [], []
    sigs, wsigs, msigs = [], [], []
    try:
        import pandas as pd
        from chanlun_bi import analyze as _cl3
        cl = _cl3([{"dt": i.strftime("%Y-%m-%d"),
                    "h": float(bars.at[i, "high"]),
                    "l": float(bars.at[i, "low"])}
                   for i in bars.index],
                  [float(bars.at[i, "close"]) for i in bars.index])
        # K141 笔段挂 d(dir 方向)/pp(涨跌幅%)，中枢挂 n(段数)
        bis = [{"s": b["s_dt"], "e": b["e_dt"], "sp": b["s_price"],
                "ep": b["e_price"], "d": b.get("dir", 0),
                "pp": round((b["e_price"] / b["s_price"] - 1) * 100, 1)
                if b["s_price"] else 0} for b in cl["bis"]]
        pivots = [{"s": p["s_dt"], "e": p["e_dt"], "zg": p["zg"],
                   "zd": p["zd"], "n": p.get("n_bi", 0)}
                  for p in cl["pivots"]]
        # K142 分型标注：P2 全量分型（顶/底），t=1 顶 / -1 底
        fx = [{"d": f["dt"], "p": f["price"],
               "t": 1 if f["type"] == "顶" else -1}
              for f in cl["fractals"]]
        # K149 线段上墙：破坏确认法段（nb=段内笔数）
        sgs = [{"s": b["s_dt"], "e": b["e_dt"], "sp": b["s_price"],
                "ep": b["e_price"], "d": b.get("dir", 0),
                "nb": b.get("n_bi", 0),
                "pp": round((b["e_price"] / b["s_price"] - 1) * 100, 1)
                if b["s_price"] else 0} for b in cl["segments"]]
        # K151 三类买卖点（引擎简化判据）透传；K154 sub 子类（1 类：
        # tbe 趋势背驰/pbe 盘整背驰，标注不拦截）
        sigs = [{"d": g["d"], "p": g["p"], "t": g["t"],
                 "sub": g.get("sub", "")}
                for g in cl["signals"]]
        keys = [i - pd.Timedelta(days=i.dayofweek) for i in bars.index]
        g = bars.groupby(keys)
        wdf = pd.DataFrame({"d": g.apply(lambda x: x.index[-1]),
                            "h": g["high"].max(), "l": g["low"].min(),
                            "c": g["close"].last()})
        cw = _cl3([{"dt": r["d"].strftime("%Y-%m-%d"),
                    "h": float(r["h"]), "l": float(r["l"])}
                   for _, r in wdf.iterrows()],
                  [float(r["c"]) for _, r in wdf.iterrows()])
        wbis = [{"s": b["s_dt"], "e": b["e_dt"], "sp": b["s_price"],
                 "ep": b["e_price"], "d": b.get("dir", 0),
                 "pp": round((b["e_price"] / b["s_price"] - 1) * 100, 1)
                 if b["s_price"] else 0} for b in cw["bis"]]
        wpivots = [{"s": p["s_dt"], "e": p["e_dt"], "zg": p["zg"],
                    "zd": p["zd"], "n": p.get("n_bi", 0)}
                   for p in cw["pivots"]]
        wfx = [{"d": f["dt"], "p": f["price"],
                "t": 1 if f["type"] == "顶" else -1}
               for f in cw["fractals"]]
        wsgs = [{"s": b["s_dt"], "e": b["e_dt"], "sp": b["s_price"],
                 "ep": b["e_price"], "d": b.get("dir", 0),
                 "nb": b.get("n_bi", 0),
                 "pp": round((b["e_price"] / b["s_price"] - 1) * 100, 1)
                 if b["s_price"] else 0} for b in cw["segments"]]
        wsigs = [{"d": g["d"], "p": g["p"], "t": g["t"],
                  "sub": g.get("sub", "")}
                 for g in cw["signals"]]
        # K140 月线级别：Period(M) 标量键 groupby（元组键会被 pandas
        # 当多列 key 误解析），dt=月内最后交易日（同周口径）
        gm = bars.groupby([i.to_period("M") for i in bars.index])
        mdf = pd.DataFrame({"d": gm.apply(lambda x: x.index[-1]),
                            "h": gm["high"].max(), "l": gm["low"].min(),
                            "c": gm["close"].last()})
        cmm = _cl3([{"dt": r["d"].strftime("%Y-%m-%d"),
                     "h": float(r["h"]), "l": float(r["l"])}
                    for _, r in mdf.iterrows()],
                   [float(r["c"]) for _, r in mdf.iterrows()])
        mbis = [{"s": b["s_dt"], "e": b["e_dt"], "sp": b["s_price"],
                 "ep": b["e_price"], "d": b.get("dir", 0),
                 "pp": round((b["e_price"] / b["s_price"] - 1) * 100, 1)
                 if b["s_price"] else 0} for b in cmm["bis"]]
        mpivots = [{"s": p["s_dt"], "e": p["e_dt"], "zg": p["zg"],
                    "zd": p["zd"], "n": p.get("n_bi", 0)}
                   for p in cmm["pivots"]]
        mfx = [{"d": f["dt"], "p": f["price"],
                "t": 1 if f["type"] == "顶" else -1}
               for f in cmm["fractals"]]
        msgs = [{"s": b["s_dt"], "e": b["e_dt"], "sp": b["s_price"],
                 "ep": b["e_price"], "d": b.get("dir", 0),
                 "nb": b.get("n_bi", 0),
                 "pp": round((b["e_price"] / b["s_price"] - 1) * 100, 1)
                 if b["s_price"] else 0} for b in cmm["segments"]]
        msigs = [{"d": g["d"], "p": g["p"], "t": g["t"],
                  "sub": g.get("sub", "")}
                 for g in cmm["signals"]]
    except Exception:
        log_exc("_kline_data:缠论多级别结构")  # C2批1
    # K172 P2 15 形态识别 + 信号日共振映射（四市场纯 OHLC 规则，
    # chanlun_pattern 自研引擎；ptmap 仅含结构信号日出现的形态）
    pats, ptmap = [], {}
    try:
        from chanlun_pattern import detect as _pdt, resonmap as _pres
        pbars = [{"dt": x["d"], "o": x["o"], "h": x["h"],
                  "l": x["l"], "c": x["c"]} for x in out]
        pats = _pdt(pbars)
        ptmap = _pres(pats, [g["d"] for g in sigs])
    except Exception:
        log_exc("_kline_data:chanlun_pattern 形态识别")  # C2批1
    return {"ok": True, "name": name, "code": code,
            "bars": out, "marks": marks, "news": news,
            "bis": bis, "pivots": pivots,
            "wbis": wbis, "wpivots": wpivots,
            "mbis": mbis, "mpivots": mpivots,
            "fx": fx, "wfx": wfx, "mfx": mfx,
            "sgs": sgs, "wsgs": wsgs, "msgs": msgs,
            "sigs": sigs, "wsigs": wsigs, "msigs": msigs,
            "patterns": pats, "ptmap": ptmap}


def _kline_qy(c):
    """个股季/年线 JSON（K200：K147-F 季/年线级别落地）。

    深历史：datamart daily 别名 = ts_daily（xtcloud 2023-07 起）+
    tushare 深历史 daily（by_date，1990 起，freshness 20230728）；
    按请求区间降级——end≤20230730 命中深表（实测单票 0.6s/2925 行），
    start≥20230731 命中 ts_daily。两段拼接 (code,date) 去重。
    服务端聚合季/年 OHLCV + MA + chanlun_bi 结构；仅 A 股（深源为
    tushare CN）。懒加载独立端点，默认 /api/kline 零改动零回归。"""
    e = _bt_resolve(c)
    if not e:
        return {"ok": False, "err": "未识别「%s」" % c}
    code = e.get("code", "")
    m6 = re.search(r"(\d{6})", code or "")
    if not m6:
        return {"ok": False, "err": "季/年线仅支持 A 股（深历史源为 CN）"}
    d6 = m6.group(1)
    ts_code = (d6 + ".SH" if d6[0] == "6" else d6 + ".SZ"
               if d6[0] in "03" else d6 + ".BJ" if d6[0] in "48" else "")
    if not ts_code:
        return {"ok": False, "err": "季/年线仅支持 A 股（北交所 %s 无深历史"
                                     "确认）" % d6}
    try:
        sys.path.insert(0, os.path.join(STATION, "quant_backtest"))
        import datamart as _dm
        d1 = _dm.read("daily", codes=[ts_code], start="19900101",
                      end="20230730")
        d2 = _dm.read("daily", codes=[ts_code], start="20230731")
    except Exception as ex:
        return {"ok": False, "err": "深历史读取失败 %s" % str(ex)[:80]}
    import pandas as _pd
    df = _pd.concat([x for x in (d1, d2) if x is not None and len(x)] ,
                    ignore_index=True)
    if df is None or not len(df):
        return {"ok": False, "err": "%s 无日线深历史" % ts_code}
    df = df.drop_duplicates("trade_date").sort_values("trade_date") \
        .reset_index(drop=True)
    if "close" not in df.columns or len(df) < 12:
        return {"ok": False, "err": "深历史不足（%d 根）" % len(df)}
    td = df["trade_date"].astype(str)
    dts = td.str.slice(0, 4) + "-" + td.str.slice(4, 6) + "-" + \
        td.str.slice(6, 8)

    def _agg(freq):
        if freq == "q":
            key = dts.str.slice(0, 4) + "Q" + (
                (dts.str.slice(5, 7).astype(int) - 1) // 3 + 1).astype(str)
        else:
            key = dts.str.slice(0, 4)
        g = df.assign(_k=key).groupby("_k", sort=True)
        out = []
        for k, x in g:
            out.append({"d": dts.iloc[x.index[-1]],
                        "w0": dts.iloc[x.index[0]],
                        "o": round(float(x["open"].iloc[0]), 2),
                        "h": round(float(x["high"].max()), 2),
                        "l": round(float(x["low"].min()), 2),
                        "c": round(float(x["close"].iloc[-1]), 2),
                        "v": float(x["volume"].sum()) if "volume"
                        in x.columns else 0.0})
        for kk in (5, 20, 60, 250):
            vals = [b["c"] for b in out]
            for i, b in enumerate(out):
                b["m%d" % kk] = (round(sum(vals[i - kk + 1:i + 1]) / kk, 3)
                                 if i >= kk - 1 else None)
        from chanlun_bi import analyze as _clq
        cl = _clq([{"dt": b["d"], "h": b["h"], "l": b["l"]} for b in out],
                  [b["c"] for b in out])
        bis = [{"s": b["s_dt"], "e": b["e_dt"], "sp": b["s_price"],
                "ep": b["e_price"], "d": b.get("dir", 0),
                "pp": round((b["e_price"] / b["s_price"] - 1) * 100, 1)
                if b["s_price"] else 0} for b in cl["bis"]]
        pivots = [{"s": p["s_dt"], "e": p["e_dt"], "zg": p["zg"],
                   "zd": p["zd"], "n": p.get("n_bi", 0)}
                  for p in cl["pivots"]]
        fxl = [{"d": f["dt"], "p": f["price"],
                "t": 1 if f["type"] == "顶" else -1}
               for f in cl["fractals"]]
        sgs = [{"s": b["s_dt"], "e": b["e_dt"], "sp": b["s_price"],
                "ep": b["e_price"], "d": b.get("dir", 0),
                "nb": b.get("n_bi", 0),
                "pp": round((b["e_price"] / b["s_price"] - 1) * 100, 1)
                if b["s_price"] else 0} for b in cl["segments"]]
        sigs = [{"d": g2["d"], "p": g2["p"], "t": g2["t"],
                 "sub": g2.get("sub", "")} for g2 in cl["signals"]]
        return {"bars": out, "bis": bis, "pivots": pivots, "fx": fxl,
                "sgs": sgs, "sigs": sigs, "marks": [], "news": {}}

    try:
        q = _agg("q")
        y = _agg("y")
    except Exception as ex:
        return {"ok": False, "err": "季/年结构计算失败 %s" % str(ex)[:80]}
    return {"ok": True, "name": e["name"], "code": code,
            "q": q, "y": y,
            "deep_span": "%s..%s" % (dts.iloc[0], dts.iloc[-1]),
            "n_deep": int(len(df))}


_CHIPS_FLOCK = threading.Lock()








def _chips_data(q, d, x=None):
    """个股筹码分布瀑布（S5）：cyq_chips 单日按需 + parquet 缓存。

    A股专属；x=当日收盘价（计算获利盘/平均成本，仅展示用途）。
    返回按价升序 rows=[[price,percent]...]（已滤 price<=0 垃圾行）。
    """
    import re as _re
    m6 = _re.search(r"(\d{6})", str(q or ""))
    ts_code = ""
    if m6:
        d6 = m6.group(1)
        ts_code = (d6 + ".SH" if d6[0] == "6" else d6 + ".SZ"
                   if d6[0] in "03" else d6 + ".BJ" if d6[0] in "48" else "")
    if not ts_code:
        return {"ok": False, "err": "筹码分布为 A 股专属"}
    dd = _re.sub(r"\D", "", str(d or ""))[:8]
    if len(dd) != 8:
        return {"ok": False, "err": "日期无效"}
    cdir = os.path.join(STATION, "quant_backtest", "data",
                        "tushare", "cyq_chips")
    cfile = os.path.join(cdir, ts_code.replace(".", "_") + ".parquet")
    rows = None
    try:
        import pandas as pd
        df = pd.read_parquet(cfile) if os.path.exists(cfile) else None
        if df is not None and len(df) and (df["trade_date"] == dd).any():
            rows = df[df["trade_date"] == dd]
        else:
            with _CHIPS_FLOCK:  # 在线拉取+落盘串行化（单用户门户足够）
                df2 = pd.read_parquet(cfile) if os.path.exists(cfile) else None
                if df2 is not None and len(df2) and \
                        (df2["trade_date"] == dd).any():
                    rows = df2[df2["trade_date"] == dd]  # 双检：等锁期间已被拉过
                else:
                    # 区间拉取（45天）：休市幻影K线回退最近交易日，
                    # 顺带预热缓存供 dataZoom 回滚复用
                    import datetime as _dt
                    d0 = _dt.datetime.strptime(dd, "%Y%m%d") - \
                        _dt.timedelta(days=45)
                    sys.path.insert(0, os.path.join(STATION,
                                                    "quant_backtest"))
                    import tushare_sync as _TS
                    data = _TS.ts_call("cyq_chips", {
                        "ts_code": ts_code,
                        "start_date": d0.strftime("%Y%m%d"),
                        "end_date": dd})
                    fields = data.get("fields") or []
                    fi = {f: i for i, f in enumerate(fields)}
                    ndf = pd.DataFrame(
                        [[str(r[fi["trade_date"]]), float(r[fi["price"]]),
                          float(r[fi["percent"]])]
                         for r in (data.get("items") or [])
                         if r[fi["price"]] is not None
                         and float(r[fi["price"]]) > 0],
                        columns=["trade_date", "price", "percent"])
                    base = df2 if df2 is not None else df
                    merged = pd.concat([t for t in (base, ndf)
                                        if t is not None and len(t)])
                    if len(merged):
                        merged = merged[merged["trade_date"].astype(str)
                                        <= dd]
                    if not len(merged):
                        return {"ok": False,
                                "err": "该日无筹码数据（%s）" % dd}
                    merged = merged.drop_duplicates(
                        ["trade_date", "price"]).sort_values(
                        ["trade_date", "price"])
                    os.makedirs(cdir, exist_ok=True)
                    merged.to_parquet(cfile, index=False)
                    last = str(merged["trade_date"].max())
                    rows = merged[merged["trade_date"].astype(str) == last]
                    dd = last  # 实际取到的交易日
        rr = sorted((round(float(p), 2), round(float(pc), 3))
                    for p, pc in zip(rows["price"], rows["percent"]))
        try:
            close = round(float(x), 2)
        except (TypeError, ValueError):
            close = None
        winner = avg = None
        if close and rr:
            winner = round(sum(pc for p, pc in rr if p <= close), 1)
            avg = round(sum(p * pc for p, pc in rr) / 100.0, 2)
        return {"ok": True, "code": ts_code,
                "d": "%s-%s-%s" % (dd[:4], dd[4:6], dd[6:8]),
                "close": close,
                "winner": winner, "avg": avg, "rows": rr}
    except Exception as ex:
        return {"ok": False, "err": "筹码获取失败：%s" % str(ex)[:120]}


def _sa_worker(jid, q):
    try:
        sys.path.insert(0, os.path.join(STATION, "news_collector"))
        import stock_analyze
        d = stock_analyze.analyze_detail(q)
        d["card"] = False
        if d.get("ok"):  # SYSTEM 上下文无 lark-cli 凭证：入队，由
            # 「AI个股分析轮询」任务(Administrator)每 5 分钟代推飞书卡
            try:
                rec = {k: d.get(k) for k in (
                    "name", "bias", "strength", "news_n", "px", "stat",
                    "outlook", "judged")}
                with open(os.path.join(STATION, "news_collector",
                                       "sa_push_queue.jsonl"), "a",
                          encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                d["card"] = "queued"
            except OSError:
                pass
        SA_JOBS[jid] = {"status": "done", "d": d}
        if d.get("ok"):
            _sa_hist_append(d)
    except Exception as ex:
        import traceback
        SA_JOBS[jid] = {"status": "done",
                        "d": {"ok": False, "msg": "分析异常：%s" % str(ex)[:200]}}
        try:  # 线程内异常落盘，便于排查门户任务上下文问题
            with open(os.path.join(STATION, "news_collector",
                                   "sa_page_error.log"), "a",
                      encoding="utf-8") as f:
                f.write("[%s] %s\n" % (q, traceback.format_exc()))
        except OSError:
            pass
    finally:
        SA_BUSY["v"] = False


















class Handler(api_data.Handler, api_chanlun_pages.Handler, api_backtest.Handler, api_kg.Handler, http.server.BaseHTTPRequestHandler):
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


    def _sa_page(self, q=""):
        import html as _h
        qe = _h.escape(q or "")
        hist = _sa_hist_read()
        hist_rows = "".join(
            "<tr><td style='padding:7px 10px;border-bottom:1px solid #21262d'>"
            "<a href='javascript:void(0)' onclick=\"go('%s')\" "
            "style='color:#58a6ff;text-decoration:none'>%s</a></td>"
            "<td style='padding:7px 10px;border-bottom:1px solid #21262d'>"
            "%s</td><td style='padding:7px 10px;border-bottom:1px solid "
            "#21262d;color:%s'>%s</td><td style='padding:7px 10px;"
            "border-bottom:1px solid #21262d;color:#8b949e'>%s</td></tr>" % (
                h["q"], h["name"],
                h["px"].split("（")[0],
                {"利多": "#f85149", "利空": "#3fb950",
                 "分歧": "#d29922"}.get(h["bias"], "#8b949e"),
                h["bias"], h["t"])
            for h in hist if h.get("ok"))
        body = """<!DOCTYPE html><html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>个股分析</title><style>
body{background:#0d1117;color:#e6edf3;font-family:-apple-system,
'Segoe UI','Microsoft YaHei',sans-serif;max-width:720px;margin:0 auto;
padding:24px 16px}
input{width:100%%;box-sizing:border-box;padding:14px 16px;font-size:17px;
border:1px solid #30363d;border-radius:10px;background:#161b22;
color:#e6edf3;outline:none}
input:focus{border-color:#58a6ff}
button{margin-top:12px;width:100%%;padding:14px;font-size:16px;
border:none;border-radius:10px;background:#238636;color:#fff;
cursor:pointer;font-weight:600}
button:disabled{background:#21262d;color:#8b949e;cursor:wait}
#st{color:#8b949e;font-size:13.5px;margin-top:12px;min-height:20px}
.rc{border:1px solid #30363d;border-radius:12px;padding:16px;
margin-top:16px;background:#161b22;display:none}
.k{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0}
.k div{flex:1;min-width:120px;border:1px solid #30363d;border-radius:9px;
padding:10px;text-align:center}
.k .l{color:#8b949e;font-size:12px}
.k .v{font-size:19px;font-weight:700;margin-top:3px}
h3{margin:16px 0 6px;font-size:14.5px;color:#e6edf3}
.small{font-size:12.5px;color:#8b949e;line-height:1.7}
.news{font-size:13px;color:#c9d1d9;line-height:1.8;
border-left:2px solid #30363d;padding-left:10px;margin:6px 0}
table{width:100%%;border-collapse:collapse;font-size:13.5px;margin-top:6px}
</style></head><body>
%s
<h2 style='font-size:20px'>🎯 个股分析</h2>
<p class='small'>输入任意股票 → 自动拉行情、近7天新闻，逐条 JEV 判断后给结论。<br>
支持中文名（英伟达）/ 美股（TSLA）/ A股（600519）/ 港股（00700）</p>
<input id='q' placeholder='输入股票名或代码，回车开始' maxlength='20' value='%s'>
<button id='go' onclick='go()'>开始分析</button>
<div id='st'></div>
<div class='rc' id='rc'></div>
<h3>🕘 最近分析（点击重新分析）</h3>
<table><tr style='color:#8b949e'><td style='padding:7px 10px'>股票</td>
<td style='padding:7px 10px'>现价</td><td style='padding:7px 10px'>倾向</td>
<td style='padding:7px 10px'>时间</td></tr>%s</table>
<p class='small' style='margin-top:18px'>约 1 分钟出结果（JEV 逐条判断 + AI 展望）·
结论同步推飞书卡片 · 纸面研究不构成投资建议</p>
<script>
function esc(s){return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;')}
var tmr=null;
function go(preset){
  var q=(preset||document.getElementById('q').value||'').trim();
  if(!q)return;
  document.getElementById('q').value=q;
  var b=document.getElementById('go');b.disabled=true;
  var st=document.getElementById('st'),rc=document.getElementById('rc');
  rc.style.display='none';
  var tips=['正在识别股票…','拉取实时行情…','检索近7天关联新闻…',
            'JEV 逐条判断中…','AI 生成展望…'];
  var i=0;st.textContent='⏳ '+tips[0];
  tmr=setInterval(function(){i=(i+1)%%tips.length;
    st.textContent='⏳ '+tips[i]},9000);
  fetch('/sa/start?q='+encodeURIComponent(q))
    .then(function(r){return r.json()})
    .then(function(j){
      if(j.error){st.textContent='❌ '+j.error;b.disabled=false;return}
      poll(j.job)});
  function poll(job){
    fetch('/sa/result?job='+job).then(function(r){return r.json()})
    .then(function(j){
      if(j.status!='done'){setTimeout(function(){poll(job)},3000);return}
      clearInterval(tmr);b.disabled=false;st.textContent='✅ 完成';
      if(!j.d.ok){st.textContent='❌ '+j.d.msg;return}
      var d=j.d,news=d.judged.map(function(x){
        return '<div class=news>['+esc(x.bias_cn)+' '+x.conf+'%%] '+
        esc(x.title)+'<br><span class=small>'+esc(x.reason)+'</span></div>'
      }).join('')||'<div class=small>（近7天无关联新闻）</div>';
      rc.innerHTML='<div class=k>'+
        '<div><div class=l>综合倾向</div><div class=v>'+esc(d.bias)+
        '</div></div><div><div class=l>信号强度</div><div class=v>'+
        (d.strength>0?'+':'')+d.strength+'</div></div><div>'+
        '<div class=l>关联新闻</div><div class=v>'+d.news_n+
        '</div></div></div>'+
        '<h3>行情 · '+esc(d.name)+'</h3><div class=small>'+esc(d.px)+
        '　|　'+esc(d.stat)+'</div>'+
        '<h3>未来展望（1-2 周）</h3><div class=news style=border-color:#238636>'+
        esc(d.outlook)+'</div><h3>新闻依据</h3>'+news;
      rc.style.display='block';rc.scrollIntoView({behavior:'smooth'});
      setTimeout(function(){location.reload()},60000);
    });
  }
}
document.getElementById('q').addEventListener('keydown',function(e){
  if(e.key==='Enter')go()});
var P=%s;
if(P){setTimeout(function(){go(P)},400)}
</script></body></html>""" % (pill_nav("sa"), qe, hist_rows, json.dumps(q))
        self._html(body)

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

    def _strategies_page(self):
        """策略中心（K36）：注册表全景记分卡——策略即资产，
        状态/指标/入口一页看全 + 上新三步 SOP。"""
        import html as _h
        e = _h.escape
        d = strategies_data()
        items = d["strategies"]
        n_live = sum(1 for s in items if s.get("status") == "live")
        n_shadow = sum(1 for s in items if s.get("status") == "shadow")
        n_tool = sum(1 for s in items if s.get("status") == "tool")
        # 状态 → 语义色（tokens.css 语义层，不硬编码 hex）
        ST = {"live": ("实盘", "var(--color-ok)"),
              "shadow": ("影子", "var(--color-warn)"),
              "tool": ("支撑", "var(--link)"),
              "paused": ("暂停", "var(--text-dim)")}
        # 生命周期带：研发 → 影子验证 → 决策链 → 复盘退役
        band = "".join(
            "<span style='display:inline-flex;align-items:center;"
            "gap:6px;padding:5px 12px;border:1px solid var(--border);"
            "border-radius:999px;font-size:12px;background:var(--bg-raised)"
            "'><b style='color:%s'>%s</b>"
            "<span style='color:var(--text-dim)'>%s</span></span>"
            "<span style='color:var(--text-dim);padding:0 4px'>→</span>"
            % (c, e(t), e(n)) for t, c, n in [
                ("影子验证", "var(--color-warn)", "%d 条在验" % n_shadow),
                ("决策链", "var(--color-ok)", "%d 条在产" % n_live),
                ("复盘退役", "var(--text-dim)", "结案入库")])
        cards = []
        for s in items:
            st = ST.get(s.get("status"), ("?", "var(--text-dim)"))
            mets = "".join(
                "<div style='display:flex;justify-content:space-between;"
                "gap:10px;font-size:12px;padding:3px 0'>"
                "<span style='color:var(--text-dim);flex-shrink:0'>%s</span>"
                "<span style='text-align:right'>%s</span></div>"
                % (e(m.get("k", "")), e(str(m.get("v", ""))))
                for m in (s.get("metrics") or []))
            links = []
            if s.get("href"):
                links.append("<a href='%s' style='color:var(--link);"
                             "text-decoration:none;font-size:12.5px'>"
                             "门户入口 →</a>" % e(s["href"]))
            if s.get("ext"):
                links.append("<a href='%s' target='_blank' rel='noopener' "
                             "style='color:var(--link);text-decoration:none;"
                             "font-size:12.5px'>外部系统 ↗</a>" % e(s["ext"]))
            task = ("<span style='font-size:11px;color:var(--text-dim)"
                    ";font-family:Consolas,monospace'>任务 %s</span>"
                    % e(s["task"])) if s.get("task") else ""
            cards.append(
                "<div style='background:var(--bg-raised);border:1px solid "
                "var(--border);border-radius:12px;padding:16px 18px;"
                "display:flex;flex-direction:column;gap:6px'>"
                "<div style='display:flex;align-items:center;gap:8px'>"
                "<span style='font-size:10.5px;padding:1px 8px;"
                "border:1px solid var(--border);border-radius:999px;"
                "color:var(--text-dim)'>%s</span>"
                "<span style='font-size:10.5px;padding:1px 8px;"
                "border-radius:999px;color:%s;background:var(--bg-surface)"
                ";border:1px solid var(--border)'>● %s</span></div>"
                "<div style='font-size:15px;font-weight:700'>%s</div>"
                "<div style='font-size:12px;color:var(--text-dim)'>%s</div>"
                "<div style='font-size:11.5px;color:%s'>%s</div>"
                "<div style='margin-top:4px'>%s</div>"
                "<div style='display:flex;justify-content:space-between;"
                "align-items:baseline;margin-top:auto;padding-top:8px;"
                "border-top:1px dashed var(--border-weak)'>"
                "<span style='display:flex;gap:12px'>%s</span>%s</div></div>"
                % (e(s.get("badge", "")), st[1], st[0], e(s["name"]),
                   e(s.get("tagline", "")), st[1], e(s.get("status_note", "")),
                   mets, "".join(links), task))
        nav = pill_nav("strategies")
        # K87 锦标赛交叉表：策略来源 × 六本账累计贡献（sids 汇总，
        # 数据 reports/trader_sim_eval.json；列序与 trader_sim.BOOKS 同步）
        _ev = _read_json(os.path.join(REPORTS_DIR, "trader_sim_eval.json"))
        sids = (_ev or {}).get("sids") or {}
        tourney_html = ""
        if sids:
            _BK6 = ["激进|积极", "激进|保守", "保守|积极",
                    "保守|保守", "中立|积极", "中立|保守"]

            def _cell(v):
                if v is None:
                    return ("<td style='text-align:right;padding:5px 8px;"
                            "color:var(--text-dim)'>—</td>")
                return ("<td style='text-align:right;padding:5px 8px;"
                        "color:%s'>%+.2f</td>"
                        % ("var(--color-up)" if v >= 0 else "var(--color-dn)",
                           v))
            _th = "".join(
                "<th style='text-align:right;padding:4px 8px;font-weight:400'>"
                "%s</th>" % bk.replace("|", "·") for bk in _BK6)
            _trs = []
            for sid, s in sorted(
                    sids.items(),
                    key=lambda kv: kv[1].get("total") or 0, reverse=True):
                _tot = s.get("total") or 0
                _trs.append(
                    "<tr style='border-top:1px solid var(--border-weak)'>"
                    "<td style='padding:5px 8px'><b>%s</b>"
                    "<span style='color:var(--text-dim);font-size:11px'> %s"
                    "</span></td>"
                    "<td style='text-align:right;padding:5px 8px'>%d</td>%s"
                    "<td style='text-align:right;padding:5px 8px;"
                    "font-weight:700;color:%s'>%+.2f</td>"
                    "<td style='padding:5px 8px;color:var(--text-dim);"
                    "font-size:11.5px'>%s</td></tr>"
                    % (e(s.get("label") or sid), e(sid), s.get("n", 0),
                       "".join(_cell(s.get(bk)) for bk in _BK6),
                       "var(--color-up)" if _tot >= 0 else "var(--color-dn)",
                       _tot, e((s.get("best_bk") or "").replace("|", "·"))))
            tourney_html = (
                "<div style='background:var(--bg-raised);border:1px solid "
                "var(--border);border-radius:12px;padding:16px 18px;"
                "margin-top:22px'>"
                "<div style='font-size:14px;font-weight:700;margin-bottom:4px'>"
                "♠ 操盘锦标赛 · 策略来源 × 六本账</div>"
                "<div style='font-size:12px;color:var(--text-dim);"
                "margin-bottom:10px'>投委会立案票经三操盘手（激进/保守/"
                "中立）× 两盘算（积极/保守）六账推演；单元格=该策略立案票"
                "在对应账本的累计贡献%%（中式红涨绿跌）· 数据 %s</div>"
                "<div style='overflow-x:auto'><table style='border-collapse:"
                "collapse;width:100%%;font-size:12.5px'>"
                "<tr style='color:var(--text-dim);font-size:11.5px'>"
                "<th style='text-align:left;padding:4px 8px;font-weight:400'>"
                "策略来源</th><th style='text-align:right;padding:4px 8px;"
                "font-weight:400'>票数</th>%s"
                "<th style='text-align:right;padding:4px 8px;font-weight:400'>"
                "合计</th><th style='text-align:left;padding:4px 8px;"
                "font-weight:400'>最佳账</th></tr>%s</table></div></div>"
                % (e((_ev or {}).get("ts", "")), _th, "".join(_trs)))
        body = (
            "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>策略中心 · 零界</title>"
            "<link rel='stylesheet' href='/static/tokens.css'></head>"
            "<body style='background:var(--bg-page);color:var(--text);"
            "font-family:\"Segoe UI\",\"Microsoft YaHei\",system-ui,sans-serif;"
            "margin:0;padding:20px 22px 40px;max-width:1180px'>"
            + nav + _HERMES_FAB_HTML +
            "<div style='display:flex;align-items:baseline;gap:14px;"
            "flex-wrap:wrap'><div style='font-size:22px;font-weight:800'>"
            "🎖 策略资产中心</div>"
            "<div style='color:var(--text-dim);font-size:12.5px'>"
            "策略即资产 · 注册表驱动 · 数据 %s</div></div>"
            "<div style='display:flex;align-items:center;flex-wrap:wrap;"
            "margin:14px 0 6px'>%s</div>"
            "<div style='color:var(--text-dim);font-size:12px;margin:4px 0 16px'>"
            "合计 <b style='color:var(--text)'>%d</b> 条：实盘 %d · "
            "影子验证 %d · 支撑工具 %d —— 每条策略都是一条产品线，"
            "记分卡管生命周期。</div>"
            "<div style='display:grid;gap:14px;grid-template-columns:"
            "repeat(auto-fill,minmax(320px,1fr))'>%s</div>"
            "<div style='background:var(--bg-surface);border:1px solid "
            "var(--border);border-radius:12px;padding:16px 18px;"
            "margin-top:22px'>"
            "<div style='font-size:14px;font-weight:700;margin-bottom:8px'>"
            "➕ 上新一条策略（三步，注册即上架）</div>"
            "<div style='font-size:12.5px;line-height:2;color:var(--text-dim)'>"
            "① <b style='color:var(--text)'>portal/strategies.json</b> 加一条："
            "id/名称/一句话价值/状态（live·shadow·tool·paused）/入口/记分卡要点"
            " —— 首页组合区、本页、导航自动同步，前端零改动<br>"
            "② 策略自身页面挂门户路由（portal_api.py 一行 + nginx.conf 一行）"
            "<br>③ 计划任务上线（照 deploy_weak_*.ps1 模板）→ 进 "
            "<a href='/sys-health' style='color:var(--link)'>系统自检</a>守护"
            "<br><span style='color:var(--text-dim)'>状态约定：影子=只标注不"
            "拦截，达标才转正（Jev 门判例：FOCUS 前向 &gt;+1pp 且 n≥30）</span>"
            "</div></div></body></html>"
            % (e(d["ts"]), band, len(items), n_live, n_shadow, n_tool,
               "".join(cards))
            + tourney_html)
        self._html(body)

    def _fin_page(self):
        import html as _h
        from urllib.parse import quote as _q
        e = _h.escape
        pre = _read_json(os.path.join(REPORTS_DIR,
                                      "premarket_latest.json"))
        snap = _read_json(os.path.join(REPORTS_DIR, "rps_latest.json"))
        qcfg = _read_json(os.path.join(STATION, "quant_backtest",
                                       "config.json"))
        rep = qcfg.get("report", {})

        # ── A 区 · 今日全景（默认展开，一屏）─────────────────
        # A1 查一只股票（表单 → /sa?q= 自动开跑）
        sa_blk = (
            "<form action='/sa' method='get' style='display:flex;gap:8px'>"
            "<input name='q' placeholder='股票名或代码：英伟达 / TSLA / "
            "600519 / 00700' maxlength='20' style='flex:1;padding:11px 14px;"
            "font-size:15px;border:1px solid #30363d;border-radius:10px;"
            "background:#161b22;color:#e6edf3;outline:none' autofocus>"
            "<button style='padding:11px 20px;font-size:14px;border:none;"
            "border-radius:10px;background:#238636;color:#fff;cursor:pointer;"
            "font-weight:600'>分析</button></form>"
            "<div class='dim' style='margin-top:6px'>行情 + 近7天 JEV 逐条"
            "判断 + 展望 · 约 1 分钟 · 结论推飞书</div>")

        # A2 今日市场一句话 → 升级为五维拼图（首屏可见结构）
        prob = pre.get("prob")
        vals = pre.get("vals", {})
        verdict = e(str(pre.get("verdict", "—")))
        prob_txt = ("%d%%" % prob) if prob is not None else "—"
        # 五维色块：正绿负红，分值即权重
        dim_colors = {"外围": "#58a6ff", "流动性": "#d2a8ff",
                      "政策": "#f778ba", "板块": "#ffa657", "情绪": "#3fb950"}
        dim_chips = "".join(
            "<span style='display:inline-flex;align-items:baseline;gap:3px;"
            "margin:3px 10px 3px 0;padding:4px 10px;border-radius:8px;"
            "background:#161b22;border:1px solid #30363d;font-size:12px'>"
            "<span style='color:%s'>%s</span>"
            "<b style='color:%s;font-size:13px'>%s</b></span>"
            % (dim_colors.get(k, "#8b949e"), e(str(k)),
               "#8b949e" if v is None else
               ("#f85149" if v >= 0 else "#3fb950"),
               "—" if v is None else "%+d" % int(v))
            for k, v in vals.items())
        # 板块结构：取正负最强各一
        l4 = pre.get("l4", [])
        if l4:
            best = max(l4, key=lambda x: x["score"])
            worst = min(l4, key=lambda x: x["score"])
            sector_note = ("<div style='margin-top:6px;font-size:11.5px;"
                          "color:#8b949e'>板块 <span style='color:#f85149'>%s +%d</span>"
                          " · <span style='color:#3fb950'>%s %+d</span></div>"
                          % (e(str(best.get("sector", ""))), best["score"],
                             e(str(worst.get("sector", ""))), worst["score"]))
        else:
            sector_note = ""
        # 流动性/外围摘要
        l1 = pre.get("l1", {})
        l3 = pre.get("l3", {})
        l1_note = ""
        if l1.get("score") is not None:
            l1_note = "外围 %+d" % l1["score"]
        l3_note = ""
        if l3.get("score") is not None:
            l3_note = "流动性 %+d" % l3["score"]
        fx = l1.get("fx", {})
        fx_note = ""
        if fx.get("val"):
            fx_note = " · 离岸人民币 %.4f (%s)" % (
                fx["val"], "升" if fx.get("pct", 0) > 0 else "贬")
        # 公告/研报摘要（TDX l6）
        l6 = pre.get("l6") or {}
        ann_note = ""
        an6 = l6.get("ann_stats")
        if an6 and an6.get("bear"):
            ann_note = " · ⚠ 利空公告 %d" % an6["bear"]
        elif an6:
            ann_note = " · 公告 %d" % an6["total"]
        rpt_note = ""
        rp6 = l6.get("rpt_stats")
        if rp6 and rp6.get("rated"):
            rpt_note = " · 研报 %d 篇（买入 %d）" % (rp6["rated"],
                                                      rp6["buy"])
        summary_line = ""
        if l1_note or l3_note:
            summary_line = ("<div style='margin-top:5px;font-size:11.5px;"
                           "color:#8b949e'>%s%s%s%s%s</div>"
                           % (l1_note, (" · " + l3_note) if l3_note else "",
                              fx_note, ann_note, rpt_note))
        pre_blk = (
            "<div style='font-size:18px;font-weight:700;margin:2px 0 4px'>"
            "%s <span style='font-size:13px;font-weight:400;color:#8b949e'>"
            "溢价 %s</span></div>"
            "<div style='margin:6px 0'>%s</div>%s%s"
            "<div style='margin-top:6px'><a href='/premarket'>盘前详情 →</a></div>"
            % (verdict, prob_txt, dim_chips, sector_note, summary_line))

        # A3 四市场闸门（今天能买吗）
        gates = []
        for ev in snap.get("envs", []):
            mk = _env_market_key(ev.get("label", ""))
            if not mk:
                continue
            safe = bool(ev.get("safe"))
            gates.append(
                "<a class='gate %s' href='/bt/m/%s'><b>%s</b>"
                "<span style='float:right;font-size:15px;font-weight:700'>"
                "%s</span><div style='margin-top:5px;font-size:12.5px;"
                "color:%s'>%s</div></a>"
                % ("g-on" if safe else "g-off", mk, MKT_LABEL[mk],
                   e(str(ev.get("score", "—"))),
                   "#3fb950" if safe else "#f85149",
                   "✅ 放行" if safe else "⛔ 否决"))
        gates_html = ("<div class='gates'>%s</div>" % "".join(gates)
                      if gates else "<div class='dim'>暂无快照</div>")

        # A4 今日机会（共振"买入" Top5）
        res = snap.get("res", [])
        buys = sorted((r for r in res if r.get("action") == "买入"),
                      key=lambda r: -(r.get("score") or 0))[:5]
        opp_rows = "".join(
            "<tr><td><a href='/kg/stock/%s'>%s</a></td>"
            "<td><span class='dim'>%s</span></td>"
            "<td><b>%d</b></td></tr>"
            % (e(str(r.get("code", ""))), e(str(r.get("name", ""))),
               e(str(r.get("market", ""))), round(r.get("score") or 0))
            for r in buys)
        opp_blk = (
            "<div class='dim' style='margin-bottom:4px'>全池 %d 只 · "
            "<span style='color:#f85149'>买入 %d</span></div>"
            "<table><tr><th>标的</th><th>市场</th><th>共振分</th></tr>"
            "%s</table>"
            "<div style='margin-top:6px'><a href='/bt-qpzm'>全部 →</a></div>"
            % (len(res), len(buys), opp_rows or
               "<tr><td colspan='3' class='dim'>今日无买入信号</td></tr>")
            ) if res else "<div class='dim'>暂无快照</div>"

        # ── B 区 · 策略动态（注册表驱动，新增策略零改）────────
        cross = {}
        try:
            sys.path.insert(0, os.path.join(STATION, "quant_backtest"))
            import cross_poll as _CP
            cross = _CP.build_cross() or {}
        except Exception:
            log_exc("_fin 策略动态:cross_poll")  # C2批1
            cross = {}
        n_per = cross.get("n_per", {})
        stocks = cross.get("stocks", [])
        n_multi = sum(1 for st in stocks if st.get("n_hit", 0) >= 2)
        n_mixed = sum(1 for st in stocks if st.get("state") == "mixed")
        n_long = sum(1 for st in stocks if st.get("state") == "long")
        strat_reg = cross.get("strategies", [])
        dyn_cards = "".join(
            "<a class='sstat' href='%s'><div class='st'>%s %s</div>"
            "<div class='sv'>%d 只命中</div>"
            "<div class='su'>→ 进入工作台</div></a>"
            % (s["url"], e(str(s.get("icon") or "🧪")),
               e(str(s.get("name", s["id"]))), int(n_per.get(s["id"], 0)))
            for s in strat_reg)
        cross_card = (
            "<a class='sstat' href='/bt-cross'><div class='st'>🎯 多策略共振"
            "</div><div class='sv'><span class='up'>%d</span> 多策略 · "
            "<span class='dn'>%d</span> 冲突</div>"
            "<div class='su'>全多 %d · → 共振看板</div></a>"
            % (n_multi, n_mixed, n_long))
        base_strats = "".join(
            "<a class='tp' href='/bt/strategy/%s'>🧪 %s</a>" % (sn, lb)
            for sn, lb in [("trend_follow", "趋势跟随"),
                           ("ma_cross", "均线交叉"),
                           ("rsi_reversal", "RSI 反转"),
                           ("boll_breakout", "布林突破")])
        strat_dyn = (
            "<div class='gates'>%s%s</div>"
            "<div class='dim' style='margin-top:8px'>策略卡数与口径随 "
            "cross_poll.STRATEGIES 注册表自动扩展 · 基础策略纵切：%s</div>"
            % (dyn_cards, cross_card, base_strats))

        # ── C 区 · 折叠收纳（默认收起，首屏不露）────────────
        # C1 盘前五维明细（A2 已展示摘要，此处放完整分布与镜像链接）
        vals_detail = pre.get("vals", {})
        vchips = "".join(
            "<span style='display:inline-block;margin:3px 8px 3px 0;"
            "padding:3px 10px;border-radius:9px;background:#161b22;"
            "border:1px solid #30363d;font-size:12.5px'>%s <b "
            "style='color:%s'>%s</b></span>"
            % (e(str(k)),
               "#8b949e" if v is None else
               ("#f85149" if v >= 0 else "#3fb950"),
               "—" if v is None else "%+d" % int(v))
            for k, v in sorted(vals_detail.items(),
                               key=lambda x: -abs(x[1])
                               if x[1] is not None else 0)
        ) or "<span class='dim'>暂无盘前数据</span>"
        pre_detail = vchips + (
            "<div style='margin-top:8px'><a href='/premarket'>🖥 完整盘前"
            "分析 →</a>%s</div>"
            % (("　·　<a href='%s' target='_blank'>📄 飞书镜像</a>"
                % e(rep.get("premarket_doc_url")))
               if rep.get("premarket_doc_url") else ""))

        # C2 工具与横切
        tools = "".join(
            "<a class='tp' href='%s'>%s</a>" % (href, lb) for lb, href in [
                ("🚦 明日信号", "/bt-signals"),
            ("💼 持仓跟踪", "/bt-positions"),
            ("📄 回测报告", "/bt-report"),
            ("📊 寻优榜单", "/bt-opt"),
            ("🔥 板块热力", "/bt-sectors"),
            ("⚖️ JEV 消息面", "/bt-jev"),
            ("🤖 AI 晨报", "/bt-brief"),
            ("🎯 选股中心", "/bt"),
            ("📊 数据仓健康", "/bt-data-health"),
        ])
        markets = "".join(
            "<a class='tp' href='/bt/m/%s'>%s</a>" % (mk, lb)
            for mk, lb in [("cn", "🇨🇳 A股"), ("us", "🇺🇸 美股"),
                           ("hk", "🇭🇰 港股"), ("crypto", "🪙 加密")])

        # C3 最近分析
        hist = _sa_hist_read(6)
        hist_rows = "".join(
            "<tr><td><a href='/sa?q=%s'>%s</a></td><td>%s</td>"
            "<td style='color:%s'>%s</td><td class='dim'>%s</td></tr>"
            % (_q(h["q"]), e(str(h.get("name", ""))),
               e(str(h.get("px", "")).split("（")[0]),
               {"利多": "#f85149", "利空": "#3fb950",
                "分歧": "#d29922"}.get(h.get("bias"), "#8b949e"),
               e(str(h.get("bias", ""))), e(str(h.get("t", ""))))
            for h in hist if h.get("ok"))
        hist_blk = ("<table><tr><th>股票</th><th>现价</th><th>倾向</th>"
                    "<th>时间</th></tr>%s</table>" % hist_rows
                    if hist_rows else "<div class='dim'>还没有分析记录"
                    "，从上面输入第一只吧</div>")

        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>金融驾驶舱</title>"
                + self._COCKPIT_CSS + "</head><body>"
                + pill_nav("fin")
                + "<h1>🏦 金融驾驶舱</h1><div class='dim'>今日全景 · "
                "策略动态 · 数据截至 %s</div>" % e(str(snap.get("latest", "")))
                # A 区：今日全景（一屏，默认展开）
                + "<div class='sec'><h2>🔍 分析一只股票</h2>" + sa_blk
                + "</div>"
                + "<div class='duo'>"
                + "<div class='sec'><h2>🌅 今日市场一句话</h2>" + pre_blk
                + "</div>"
                + "<div class='sec'><h2>💡 今日机会 · 买什么</h2>" + opp_blk
                + "</div></div>"
                + "<div class='sec'><h2>🚦 四市场闸门 · 今天能买吗</h2>"
                + gates_html + "</div>"
                # B 区：策略动态（一屏，默认展开）
                + "<div class='sec'><h2>🧭 策略动态 · 各方法论今天在干嘛"
                "</h2>" + strat_dyn + "</div>"
                # C 区：折叠收纳（默认收起）
                + "<details class='sec'><summary><h2>🌅 盘前五维明细</h2>"
                "<span class='dim'>评分分布 · 飞书镜像</span></summary>"
                "<div class='sec-body'>" + pre_detail + "</div></details>"
                + "<details class='sec'><summary><h2>🧰 工具与横切</h2>"
                "<span class='dim'>执行跟踪 · 市场全景 · 分析报告</span>"
                "</summary><div class='sec-body'>"
                + "<div class='dim' style='margin:6px 0 4px'>执行与跟踪："
                "</div>" + tools
                + "<div class='dim' style='margin:10px 0 4px'>市场全景："
                "</div>" + markets + "</div></details>"
                + "<details class='sec'><summary><h2>🕘 最近分析</h2>"
                "<span class='dim'>点击继续看</span></summary>"
                "<div class='sec-body'>" + hist_blk + "</div></details>"
                + "<div class='dim' style='margin-top:20px'>纸面研究"
                "不构成投资建议 · 共振与闸门数据每日 17:35 刷新 · 盘前"
                "工作日 08:30</div></body></html>")
        self._html(body)


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

    def _algo_activate(self, key):
        """卡密激活：合法且未激活的 key 写入 activated_keys（本机解锁）。
        返回 (ok, msg)。"""
        import json as _json
        import time as _t
        fp = os.path.join(ROOT, "licenses.json")
        lic = _read_json(fp) or {}
        key = (key or "").strip().upper()
        if not key:
            return False, "请输入卡密"
        today = _t.strftime("%Y-%m-%d")
        rec = next((r for r in lic.get("licenses", [])
                    if str(r.get("key", "")).upper() == key), None)
        if rec is None:
            return False, "卡密不存在"
        if str(rec.get("expiry", "")) < today:
            return False, "卡密已过期"
        act = lic.setdefault("activated_keys", [])
        if key in act:
            return True, "该卡已激活过（本机已解锁）"
        act.append(key)
        try:
            with open(fp, "w", encoding="utf-8") as f:
                _json.dump(lic, f, ensure_ascii=False, indent=2)
        except Exception:
            return False, "写入卡密库失败（文件被占用？）"
        return True, "激活成功 · 本机已解锁对应商品"

    def _algo_market_page(self, qs=None):
        """🛒 算法商品市场：本地算法商品化货架。数据源
        portal/algo_market.json（注册表存源，只读渲染）+
        portal/licenses.json（卡密库）。未订阅=试看模式（静态样例
        真实输出形态）；输入卡密激活后本机解锁。"""
        import html as _h
        e = _h.escape
        toast = ""
        # do_GET 传的是原始 query 串（字符串），内部直调可能传 dict——
        # 统一归一成 parse_qs dict（K-STOCK-122 修：此前 qs 字符串直接
        # .get 在带 ?key=/?id= 的 HTTP 请求上 AttributeError 打死连接，
        # 087 的卡密激活走浏览器一直 502/断连，仅直调冒烟未暴露）
        from urllib.parse import parse_qs as _aqs
        if isinstance(qs, str):
            qs = _aqs(qs)
        qs = qs or {}
        if qs.get("key"):
            ok, msg = self._algo_activate(qs["key"][0])
            toast = ("<div style='border:1px solid %s;border-radius:10px;"
                     "padding:10px 14px;margin:10px 0;font-size:13.5px'>%s%s"
                     "</div>" % ("#3fb950" if ok else "#f85149",
                                 "✅ " if ok else "❌ ", e(msg)))

        mkt = _read_json(os.path.join(ROOT, "algo_market.json")) or {}
        algos = mkt.get("algos", [])
        n_unlocked = sum(1 for a in algos if self._license_state(
            a.get("id", "")))
        # K-STOCK-122 岗位→算法互链（反向）：algo id → 在岗使用该算法的
        # 岗位名列表（源 ai_market.json algos 外键）；?id= 锚点定位高亮
        aim = _read_json(os.path.join(ROOT, "ai_market.json")) or {}
        users = {}
        for g in aim.get("groups", []):
            for a in g.get("ais", []):
                for i in a.get("algos", []) or []:
                    users.setdefault(i, []).append(
                        a.get("name", a.get("id", "")))
        anchor = (qs.get("id") or [""])[0]
        anchor_ok = anchor and any(a.get("id") == anchor for a in algos)

        def _algo_card(a):
            sid = a.get("id", "")
            unlocked = self._license_state(sid)
            if unlocked:
                badge = ("✅ 已解锁", "#3fb950")
            else:
                badge = ("👁 试看模式", "#d29922")
            hl = ("outline:2px solid #58a6ff;outline-offset:2px;"
                  if sid == anchor and anchor_ok else "")
            use_row = ("<div class='dim' style='font-size:11.5px;"
                       "margin-top:8px'>🤖 在岗使用：%s</div>"
                       % e("、".join(users.get(sid, [])))
                       if users.get(sid) else "")
            sm = a.get("sample", {}) or {}
            feats = "".join(
                "<span class='tp' style='cursor:default'>%s</span>"
                % e(f) for f in a.get("features", []))
            sub = a.get("subscription", {}) or {}
            sample_html = ""
            if sm:
                sample_html = (
                    "<details style='margin-top:10px'>"
                    "<summary style='cursor:pointer;font-size:13px;"
                    "color:#58a6ff'>👁 试看结果 · %s</summary>"
                    "<div class='dim' style='font-size:11.5px;margin:"
                    "6px 0 4px'>%s</div>"
                    "<pre style='background:#0d1117;border:1px solid "
                    "#30363d;border-radius:8px;padding:10px;font-size:"
                    "11.5px;overflow-x:auto;white-space:pre-wrap'>%s</pre>"
                    "</details>" % (e(sm.get("title", "样例输出")),
                                    e(sm.get("meta", "")),
                                    e(sm.get("body", ""))))
            cta = ("<a class='tp' href='%s'>进入 →</a>" % e(a.get("entry", "/"))
                   if unlocked else
                   "<span class='dim' style='font-size:12px'>订阅后开放"
                   "实时入口 · %s</span>" % e(sub.get("price_hint", "")))
            return ("<div id='algo-%s' style='border:1px solid %s;"
                    "border-radius:12px;"
                    "background:#161b22;padding:16px 18px;%s'>"
                    "<div style='display:flex;align-items:baseline;gap:8px'>"
                    "<span style='font-size:17px;font-weight:700'>%s %s"
                    "</span>"
                    "<span style='font-size:11px;color:#8b949e;border:1px "
                    "solid #30363d;border-radius:10px;padding:1px 8px'>%s"
                    "</span>"
                    "<span style='margin-left:auto;font-size:11.5px;color:"
                    "%s;border:1px solid %s;border-radius:12px;padding:"
                    "2px 10px'>%s</span></div>"
                    "<div class='dim' style='font-size:12.5px;margin:"
                    "8px 0 4px'>%s</div>"
                    "<div style='font-size:13px;margin-bottom:6px'>%s</div>"
                    "<div style='display:flex;gap:6px;flex-wrap:wrap'>%s"
                    "</div>"
                    "%s"
                    "%s"
                    "<div style='font-size:12.5px;margin-top:10px'>%s</div>"
                    "</div>"
                    % (e(sid), badge[1], hl,
                       e(a.get("icon", "🧩")),
                       e(a.get("name", "")), e(a.get("tier", "")),
                       badge[1], badge[1], badge[0],
                       e(a.get("tagline", "")), e(a.get("desc", "")),
                       feats, sample_html, use_row, cta))
        scroll_js = ""
        if anchor_ok:
            scroll_js = ("<script>var t=document.getElementById("
                         "'algo-%s');if(t){t.scrollIntoView({behavior:"
                         "'smooth',block:'center'});}</script>" % e(anchor))
        grid = "".join(
            "<div style='display:grid;grid-template-columns:repeat("
            "auto-fit,minmax(320px,1fr));gap:12px'>%s</div>"
            % "".join(_algo_card(a) for a in algos))
        act_form = (
            "<div class='sec'><h2>🔑 卡密激活（本机解锁）</h2>"
            "<div class='dim' style='font-size:12.5px;margin-bottom:8px'>"
            "输入卡密后本机立即解锁对应商品 · 卡密由管理员发放"
            "（portal/licenses.json 存源）· 试看模式下可查看每款算法的"
            "真实输出样例</div>"
            "<form method='get' action='/algo' style='display:flex;"
            "gap:8px'>"
            "<input name='key' placeholder='如 LTSO-XXXX-XXXX' style='"
            "background:#0d1117;border:1px solid #30363d;color:#e6edf3;"
            "border-radius:8px;padding:8px 12px;font-size:14px;min-width:"
            "240px'>"
            "<button type='submit' style='background:#238636;color:white;"
            "border:none;border-radius:8px;padding:8px 18px;font-size:"
            "14px;cursor:pointer'>激活</button></form></div>")
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>🛒 算法商品市场</title>"
                + self._COCKPIT_CSS + "</head><body>"
                + pill_nav("algo")
                + "<h1>🛒 算法商品市场</h1><div class='dim'>"
                + e(mkt.get("tagline", ""))
                + " · 共 %d 款商品 · 已解锁 %d 款 · 每款可试看真实输出样例"
                "</div>" % (len(algos), n_unlocked)
                + toast + grid + act_form + scroll_js
                + "<div class='dim' style='margin-top:20px'>商品源 portal/"
                "algo_market.json · 卡密库 portal/licenses.json · 内测"
                "参考价以正式发卡为准 · 不构成投资建议</div></body></html>")
        self._html(body)

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

    # ── L2 策略工作台：九维量化选股（影子评分，纯门户不推送） ──
    def _nine_dim_page(self):
        import html as _h
        e = _h.escape
        snap = _read_json(os.path.join(QBT_DIR, "data",
                                       "nine_dim_scores.json")) or {}
        ev = _read_json(os.path.join(QBT_DIR, "data",
                                     "nine_dim_pool_eval_cache.json")) or {}
        rbt = _read_json(os.path.join(QBT_DIR, "data",
                                      "nine_dim_replay_eval_cache.json")) or {}
        hist = []
        hp = os.path.join(QBT_DIR, "data", "nine_dim_history.jsonl")
        if os.path.exists(hp):
            try:
                with open(hp, encoding="utf-8") as f:
                    hist = [json.loads(l) for l in f if l.strip()][-15:]
            except Exception:
                log_exc("九维页:history 读取 %s" % hp)  # C2批1
                hist = []
        rows = snap.get("rows", [])
        env = snap.get("env") or {}
        wd = snap.get("wdelta") or {}

        def _chip(on, lb):
            c = "#3fb950" if on else "#30363d"
            return ("<span style='display:inline-block;padding:3px 10px;"
                    "border-radius:12px;font-size:12px;margin:0 6px 6px 0;"
                    "border:1px solid %s;color:%s'>%s</span>"
                    % (c, c if on else "#8b949e", lb))

        # ① 市场环境与动态调权
        if env:
            v1 = env.get("idx_above_ma20")
            env_line = ("科创50 <span style='color:%s'>%s</span> 20日线"
                        "（20日 %.2f%%）· 20日波动率分位 %s（≤30 视作低波动"
                        "，VIX 降级口径）· 两市成交额 %s 亿（≥10000 亿 +3）"
                        % ("#f85149" if v1 else "#3fb950",
                           "站上" if v1 else "跌破",
                           env.get("idx_month_chg") or 0,
                           "—" if env.get("vol_pct") is None
                           else env["vol_pct"],
                           "{:,}".format(int(env.get("amount_yi") or 0))))
            wdt = ("当前调权：<b>%s</b>" % e(json.dumps(
                       {k: "%+d" % v for k, v in wd.items()},
                       ensure_ascii=False))) if wd else \
                "当前无档位触发 · 按标准权重评分"
            env_blk = ("<div>%s</div><div class='dim' style='margin-top:6px'>"
                       "%s</div><div style='margin-top:8px'>%s</div>"
                       % (env_line, wdt,
                          _chip(env.get("bull"), "牛市档：板块+3 情绪+2")
                          + _chip(env.get("high_vol"),
                                  "高波动档：大盘-5 质量+5 动量-4")
                          + _chip(env.get("report_season"),
                                  "财报期档：盈利+3 技术-3")))
        else:
            env_blk = "<div class='dim'>暂无快照</div>"

        # ② 口径说明
        spec = (
            "<div class='dim' style='font-size:13px;line-height:2.0'>"
            "一句话：<b style='color:#e6edf3'>两段式九因子评分 —— 本地粗筛"
            "（RPS50&gt;80 + 成交额&gt;2亿 + 量能/布林动能）→ 候选池补齐 "
            "API 因子 → 78 分制评分，≥75 且完整覆盖入<b style='color:"
            "#f85149'>拥挤警报池</b>（回避名单，跟踪验证负收益即警报"
            "有效）</b><br>"
            "分段语义（K33 重定位，2026-10-02）：≥85 强警报 alert2 · "
            "75–85 警报 alert · 60–75 中段 mid（无信号，60–65 低波期有 "
            "beta 倾向非 alpha）· &lt;60 低位 low。原 ≥90 买入池退役"
            "（BUY_TH=90 仅作参照展示）<br>"
            "九因子（V3 结构）：公司质量15 · 盈利4 · 大盘环境7 · "
            "板块协同10 · 机构行为15 · 市场情绪8 · 动量资金4 · 估值安全10"
            " · 技术结构5（=78，总分=100×已得分/可判权重）；"
            "三档动态调权可叠加（上方状态卡）<br>"
            "V3 修订（2026-09-27，P2 回放 1565 样本逐维诊断驱动）："
            "移除北向连3日净买（陆股通净买 2024-08 停发后 tushare 字段"
            "语义已变成交额恒正，判据失真送分）、两市额≥万亿（99.6% 送分）、"
            "EPS上调≥1次（2.5% 可得=最大稀释源）、明弱实强（4.3% 可得）、"
            "量≥2倍（80% 与粗筛共线）；惩罚收紧：质量恶化&gt;35%、"
            "行业RPS&lt;50（旧阈值 51%/86% 样本误扣）<br>"
            "sv 豁免（V3 新增）：体系性缺失项（现金流字段/政策新闻覆盖前/"
            "历史大宗/融资明细）记 None 时从 cov 分母同步剔除——「体系没"
            "这个维度」不等于「样本覆盖不足」；个股级缺失（如 EV 缺）仍压 cov<br>"
            "V2 修订（2026-09-27）：移除年度预测上修（极稀有伪判据）；"
            "EPS上调≥1次(8分)、融资周增≥5%、券商覆盖≥3篇；"
            "北向连3日净买保留（后经 V3 确认字段失真移除）<br>"
            "不可判语义：无权限/无数据项记 <b>None 不计分不扣分</b>，总分 ="
            " 100×已得分/可判权重（cov=可判权重占比，&lt;0.75 不出分）<br>"
            "降级口径：机构调研接口无权限→机构行为=龙虎榜10+大宗5；情绪因子"
            "=新闻量周环比+JEV多空占比+券商覆盖（股吧/雪球/百度指数无源）；"
            "VIX→科创50已实现波动率分位；超预期概率→业绩预告预增；"
            "EV/EBITDA→总市值/EBITDA近似；行业分组用 stock_basic 行业"
            "（分位在候选池内近似）<br>"
            "影子纪律：纯门户展示不推送 · 入池日收盘价基准跟踪 r5/r20 · "
            "跨除权（缺口&gt;0.5%）剔除 · n&lt;10 不构成结论</div>")

        # ③ 评分榜单
        flbl = {"quality": "质", "eps": "盈", "env": "环", "sector": "板",
                "inst": "机", "sentiment": "情", "momentum": "动",
                "val": "估", "tech": "技"}
        full_rows = [r for r in rows if r.get("full")]
        part_rows = [r for r in rows if not r.get("full")]
        n_show = 0

        _BC = {"alert2": "#f85149", "alert": "#d29922",
               "mid": "#e3b341", "low": "#8b949e"}
        _BL = {"alert2": "强警报", "alert": "警报",
               "mid": "中段", "low": "低位", "na": "—"}

        def _row(i, r, partial=False):
            nonlocal n_show
            n_show += 1
            sc = r.get("score")
            bd = r.get("band") or ("na" if sc is None else "mid")
            hot = bd in ("alert", "alert2")
            fb = " ".join(
                "<span style='color:%s'>%s%d/%d</span>"
                % ("#f85149" if d["earned"] >= d["max"] * 0.99 else
                   "#3fb950" if d["earned"] < 0 else "#8b949e",
                   flbl.get(d["id"], "?"), d["earned"], d["max"])
                for d in r.get("detail", []))
            return ("<tr%s><td class='dim'>%d</td>"
                    "<td><a href='/kg/stock/%s'>%s</a><div class='dim'>%s"
                    "</div></td><td class='dim'>%s</td><td>%.1f</td>"
                    "<td style='color:%s'>%+.2f%%</td>"
                    "<td><b>%s</b>%s</td>"
                    "<td style='color:%s;font-size:12px'>%s</td>"
                    "<td class='dim'>%.2f</td>"
                    "<td class='dim' style='font-size:12px;white-space:"
                    "nowrap'>%s</td></tr>"
                    % (" style='background:#1c2128'" if hot else "",
                       i, e(str(r.get("code", ""))),
                       e(str(r.get("name", ""))), e(str(r.get("code", ""))),
                       e(str(r.get("industry", ""))),
                       float(r.get("rps50") or 0),
                       "#f85149" if (r.get("pct_chg") or 0) >= 0
                       else "#3fb950", float(r.get("pct_chg") or 0),
                       ("%.1f" % sc) if sc is not None else "未完整",
                       " ⚠" if hot else "",
                       _BC.get(bd, "#8b949e"), _BL.get(bd, "—"),
                       float(r.get("cov") or 0), fb))
        rank_rows = ""
        for i, r in enumerate(full_rows, 1):
            if n_show >= 40:
                break
            rank_rows += _row(i, r)
        for i, r in enumerate(part_rows[:10],
                              len(full_rows) + 1):
            if n_show >= 50:
                break
            rank_rows += _row(i, r, partial=True)
        rank_blk = (
            "<div class='dim' style='margin-bottom:4px'>候选池 %d 只 · "
            "完整评分 %d · ≥%d 拥挤警报 %d（K33：高分=回避位） · 漏斗：%s"
            " · 数据日 %s</div>"
            "<table><tr><th>#</th><th>标的</th><th>行业</th><th>RPS50"
            "</th><th>涨跌</th><th>总分</th><th>分段</th><th>cov</th>"
            "<th>九维 得分/满分（质盈环板机情动估技）</th></tr>%s</table>"
            % (snap.get("n_cand", 0), snap.get("n_full", 0),
               snap.get("alert_th", 75),
               snap.get("n_alert", snap.get("n_buy", 0)),
               " → ".join("%s %d" % (k, v)
                          for k, v in (snap.get("funnel") or {}).items()),
               e(str(snap.get("trade_date", ""))), rank_rows
               or "<tr><td colspan='9' class='dim'>暂无快照，每日 06:40 "
                  "刷新（NineDimScan）</td></tr>"))
        if not rows:
            rank_blk = "<div class='dim'>暂无快照，每日 06:40 刷新" \
                "（NineDimScan）</div>"

        # ④ 入池战绩
        def _stat_card(lb, st):
            if not st:
                return ("<div class='gate g-off'><b>%s</b><div class='dim'>"
                        "暂无成熟样本</div></div>" % lb)
            grey = st["n"] < 10
            col = "#8b949e" if grey else ("#f85149" if st["avg"] > 0
                                          else "#3fb950")
            return ("<div class='gate'><b>%s<span style='float:right;"
                    "color:%s'>%+.2f%%</span></b>"
                    "<div class='dim' style='margin-top:4px'>n=%d%s · "
                    "胜率 %.1f%%</div></div>"
                    % (lb, col, st["avg"], st["n"],
                       "（灰显：n<10）" if grey else "", st["win"]))
        perf_blk = ""
        if ev:
            perf_blk = (
                "<div class='dim' style='margin-bottom:6px'>累计入警报池 %d 只"
                " · 生成于 %s</div><div class='gates'>%s%s%s%s</div>"
                "<div class='dim' style='margin-top:6px'>%s</div>"
                % (ev.get("n_pool", 0), e(str(ev.get("generated_at", ""))),
                   _stat_card("r5 全体", (ev.get("r5") or {}).get("all")),
                   _stat_card("r5 ≥95分", (ev.get("r5") or {}).get("ge95")),
                   _stat_card("r20 全体", (ev.get("r20") or {}).get("all")),
                   _stat_card("r20 ≥95分",
                              (ev.get("r20") or {}).get("ge95")),
                   e(str(ev.get("note", "")))))
        else:
            perf_blk = "<div class='dim'>暂无入池，战绩随每日任务积累</div>"

        # ④b P2 历史回放（周频锚点 as-of 重放 · 影子）
        if rbt:
            blbl = {"ge90": "≥90", "85_90": "85–90", "80_85": "80–85",
                    "70_80": "70–80", "lt70": "&lt;70"}

            def _cell(o, k):
                v = (o or {}).get(k)
                return "—" if v is None else str(v)

            def _bt_row(w, name):
                st = ((rbt.get(w) or {}).get(name) or {})
                raw, thin = st.get("raw"), st.get("thinned")
                grey = not raw or raw.get("n", 0) < 10
                col = "#8b949e" if grey else (
                    "#f85149" if (raw.get("avg") or 0) > 0 else "#3fb950")
                return ("<tr%s><td class='dim'>%s</td><td>%s</td>"
                        "<td style='color:%s'>%s</td><td>%s</td>"
                        "<td>%s</td><td style='color:%s'>%s</td>"
                        "<td>%s</td><td>%s</td><td class='dim'>%s</td>"
                        "<td class='dim'>%s</td></tr>"
                        % (" class='dim'" if grey else "", w.upper(),
                           blbl.get(name, name), col,
                           _cell(raw, "n"), _cell(raw, "win"),
                           _cell(raw, "avg"), col,
                           _cell(raw, "exc"), _cell(raw, "p5"),
                           _cell(raw, "min"),
                           _cell(thin, "n"), _cell(thin, "avg")))

            bt_rows = "".join(
                _bt_row(w, b) for w in ("r20", "r5")
                for b in ("ge90", "85_90", "80_85", "70_80", "lt70"))
            worst = (((rbt.get("r20") or {}).get("ge90") or {})
                     .get("raw") or {}).get("worst") or []
            wrows = "".join(
                "<tr><td class='dim'>%s</td><td>%s<div class='dim'>%s</div>"
                "</td><td style='color:#3fb950'>%+.2f%%</td></tr>"
                % (e(str(x.get("anchor", ""))), e(str(x.get("name", ""))),
                   e(str(x.get("code", ""))), float(x.get("r20") or 0))
                for x in worst)
            worst_blk = ("<table><tr><th>锚点日</th><th>标的</th>"
                         "<th>r20 最差</th></tr>%s</table>" % wrows) \
                if wrows else "<div class='dim'>暂无 ≥90 成熟样本" \
                    "（≥90 阈值强选择性，历史入池极少属预期）</div>"
            arows = ""
            for a in reversed(rbt.get("by_anchor") or []):
                arows += (
                    "<tr><td class='dim'>%s</td><td>%s</td>"
                    "<td class='dim'>%s</td><td class='dim'>%s</td>"
                    "<td>%s</td><td><b>%s</b></td><td>%s</td><td>%s</td></tr>"
                    % (e(str(a.get("dt", ""))), _cell(a, "n_cand"),
                       _cell(a, "amt_yi"), _cell(a, "vol_pct"),
                       "牛市" if a.get("bull") else "—",
                       _cell(a, "n_full"), _cell(a, "ge90"),
                       _cell(a, "ge85")))
            v3_note = (
                "<div style='margin-bottom:8px;padding:8px 10px;"
                "border:1px solid #f85149;border-radius:6px;"
                "background:rgba(248,81,73,.08)'>"
                "<b style='color:#f85149'>⚠ K33 已重定位（2026-10-02）"
                "——总分反用为拥挤警报</b>。依据 V3 复评（51 锚点 / "
                "5479 样本 / 去重 4202）：分数与前向收益<b>单调负相关"
                "</b>——r20 均值 &lt;70 分 -9.2% → 70–80 分 -14.9% → "
                "75+ 分 -27.9% → 85+ 分 -71.5%，胜率 25%→21%→9%→0%；"
                "85+ 左尾（p5 -85.9%）全部为强动量拥挤票（存储双雄崩盘"
                "前夜 86 分、金诚信 92 分追顶 r20 -34%）。高分构成 = "
                "动量+机构+情绪+资金拥挤代理，与图谱拥挤度特征（K27）"
                "同构。<b>现行规则：≥75 入警报池跟踪（预期负收益 = 警报"
                "有效），85+ 强警报；BUY_TH=90 退役仅作参照</b>。65–75 "
                "观察池方案经离线网格否决（2024 段 70% 胜率系 n=10 小"
                "样本；60–65 低波 63% 胜率经超额检验拆穿为 beta 非alpha）。</div>")
            bt_blk = (
                "%s<div class='dim' style='margin-bottom:6px'>回放锚点 %d 个"
                "（周频 as-of 截断重放，bar/财报/龙虎/北向全部锚点日可见）"
                " · 样本行 %d · 基准 %s · 生成于 %s</div>"
                "<table><tr><th>窗口</th><th>分桶</th><th>n</th><th>胜率%%"
                "</th><th>均值%%</th><th>超额%%</th><th>p5</th><th>最低</th>"
                "<th>去重n</th><th>去重均值</th></tr>%s</table>"
                "<div class='dim' style='margin-top:10px'>≥90 分桶 r20 "
                "最差 5 条（左尾观察）：</div>%s"
                "<div class='dim' style='margin-top:10px'>逐锚点"
                "（新→旧）：</div>"
                "<table><tr><th>锚点日</th><th>候选池</th><th>两市额(亿)"
                "</th><th>波动分位</th><th>环境</th><th>完整评分</th>"
                "<th>≥90</th><th>≥85</th></tr>%s</table>"
                "<div class='dim' style='margin-top:6px'>%s</div>"
                % (v3_note, len(rbt.get("by_anchor") or []),
                   rbt.get("n_rows", 0),
                   e(str(rbt.get("bench", ""))),
                   e(str(rbt.get("generated_at", ""))), bt_rows, worst_blk,
                   arows, e(str(rbt.get("note", "")))))
        else:
            bt_blk = "<div class='dim'>回放未运行或结果未生成" \
                "（quant_backtest/nine_dim_backtest.py）</div>"

        # ⑤ 历史入池（K33 起为警报池；旧 ≥90 买入池行无 pool_type）
        if hist:
            hrows = ""
            for r in reversed(hist):
                r5 = r.get("r5")
                r20 = r.get("r20")
                pt = r.get("pool_type") or "buy_legacy"
                pt_html = ("<span style='color:#f85149'>警报</span>"
                           if pt == "alert"
                           else "<span class='dim'>买入(旧)</span>")
                hrows += (
                    "<tr><td class='dim'>%s</td><td>%s<div class='dim'>%s"
                    "</div></td><td><b>%.1f</b></td><td>%s</td>"
                    "<td>%.2f</td>"
                    "<td style='color:%s'>%s</td><td style='color:%s'>%s"
                    "</td><td class='dim'>%s</td></tr>"
                    % (e(str(r.get("dt", ""))), e(str(r.get("name", ""))),
                       e(str(r.get("code", ""))),
                       float(r.get("score") or 0), pt_html,
                       float(r.get("entry_px") or 0),
                       "#f85149" if (r5 or 0) >= 0 else "#3fb950",
                       "—" if r5 is None else "%+.2f%%%s" % (
                           r5, "" if r.get("mature5") else "(盯市)"),
                       "#f85149" if (r20 or 0) >= 0 else "#3fb950",
                       "—" if r20 is None else "%+.2f%%%s" % (
                           r20, "" if r.get("mature20") else "(盯市)"),
                       "⚠除权剔除" if r.get("adj") else ""))
            hist_blk = ("<table><tr><th>入池日</th><th>标的</th><th>入池分"
                        "</th><th>池类型</th><th>入池价</th><th>r5</th>"
                        "<th>r20</th><th>备注</th></tr>%s</table>"
                        "<div class='dim' style='margin-top:6px'>K33 起 ≥75 "
                        "入<b style='color:#f85149'>警报池</b>（回避名单，"
                        "跟踪负收益=警报有效）；「买入(旧)」为退役 ≥90 池"
                        "历史行，仅留档</div>" % hrows)
        else:
            hist_blk = ("<div class='dim'>暂无入池记录（警报池 ≥75 且 cov"
                        "≥0.75 才入；75+ 为罕见拥挤位，随每日任务积累）"
                        "</div>")

        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>九维选股 · 工作台</title>"
                + self._COCKPIT_CSS + "</head><body>"
                + pill_nav("9dim")
                + "<h1>🧮 九维量化选股 · 工作台</h1><div class='dim'>"
                "策略市场 · 9 因子 100 分制 · K33 重定位：≥75 拥挤警报池"
                "（回避位跟踪）· 影子观察不推送（快照 %s）</div>"
                % e(str(snap.get("generated_at", "—")))
                + "<details class='sec'><summary><h2>📖 模型说明书与口径</h2>"
                "<span class='dim'>点击展开</span></summary><div class='sec-body'>"
                + spec + "</div></details>"
                + "<details class='sec' open><summary><h2>🌐 市场环境与动态调权</h2>"
                "</summary><div class='sec-body'>" + env_blk + "</div></details>"
                + "<details class='sec' open><summary><h2>🎯 今日评分榜单（两段式候选池）</h2>"
                "</summary><div class='sec-body'>" + rank_blk + "</div></details>"
                + "<details class='sec'><summary><h2>📈 警报池战绩（影子验证）</h2>"
                "<span class='dim'>点击展开</span></summary><div class='sec-body'>"
                + perf_blk + "</div></details>"
                + "<details class='sec'><summary><h2>⏪ P2 历史回放（周频锚点重放 · 影子）</h2>"
                "<span class='dim'>点击展开</span></summary><div class='sec-body'>"
                + bt_blk + "</div></details>"
                + "<details class='sec'><summary><h2>🕘 历史入池（近 %d 条）</h2>"
                "<span class='dim'>点击展开</span></summary><div class='sec-body'>"
                % len(hist) + hist_blk + "</div></details>"
                + "<div class='dim' style='margin-top:20px'>纸面研究不构成"
                "投资建议 · 每日 06:40 NineDimScan 用前一交易日收盘数据"
                "评分 · K33 重定位后 ≥75 入警报池（回避位，跟踪负收益"
                "=警报有效）· P2 回放已验证（as-of 重放 51 锚点，调参"
                "需经确认）· P3 推送接入需警报池影子战绩达标后经确认"
                "启用</div>"
                "</body></html>")
        self._html(body)

    # ── 策略集市 · 多策略共振看板（L1 发现层，全下钻） ──
    def _cross_page(self):
        import html as _h
        import sys as _sy
        e = _h.escape
        if QBT_DIR not in _sy.path:
            _sy.path.insert(0, QBT_DIR)
        try:
            import cross_poll
            d = cross_poll.build_cross()
        except Exception as ex:
            self._html("<body>%s<h1>🎯 多策略共振</h1>"
                       "<div class='dim'>聚合器异常：%s</div></body>"
                       % (pill_nav("cross"), e(str(ex))))
            return

        mkt_q = ""
        try:
            from urllib.parse import urlparse, parse_qs
            mkt_q = (parse_qs(urlparse(self.path).query)
                     .get("m", [""])[0]).upper()
        except Exception:
            pass

        def _in_mkt(st):
            if not mkt_q:
                return True
            return str(st.get("market", "")).upper() == mkt_q

        def _hit_badges(st):
            sp = []
            for h in st["hits"]:
                dc = {"long": "#f85149", "short": "#3fb950",
                      "neutral": "#8b949e"}[h["dir"]]
                sp.append(
                    "<a href='%s' style='display:inline-block;margin:1px "
                    "3px 1px 0;padding:2px 8px;border-radius:10px;font-size:"
                    "12px;text-decoration:none;border:1px solid %s;color:%s' "
                    "title='下钻到 %s'>%s %s</a>"
                    % (e(h["url"]), dc, dc, e(h["label"]),
                       e(h["badge"]), e(h["label"])))
            return "".join(sp)

        def _stock_link(st):
            return ("<a href='/kg/stock/%s'><b>%s</b></a>"
                    "<div class='dim'>%s · %s</div>"
                    % (e(st["code"]), e(st["name"] or st["code"]),
                       e(st["code"]), e(st.get("market") or "")))

        stocks = [s for s in d["stocks"] if _in_mkt(s)]
        multi = [s for s in stocks if s["n_hit"] >= 2]
        mixed = [s for s in multi if s["state"] == "mixed"]
        longs = [s for s in multi
                 if s["state"] == "long" and s["n_long"] >= 2]
        shorts = [s for s in multi
                  if s["state"] == "short" and s["n_short"] >= 2]

        # ① 方向冲突榜（最高优先：持仓风险提示）
        tr_mixed = "".join(
            "<tr><td>%s</td><td>%s</td></tr>"
            % (_stock_link(s), _hit_badges(s)) for s in mixed) or (
            "<tr><td colspan='2' class='dim'>当前无多空分歧标的</td></tr>")
        blk_mixed = (
            "<details class='sec' open><summary><h2>⚠️ 方向冲突 · 多策略"
            "多空分歧（%d）</h2><span class='dim'>持仓重点排查</span>"
            "</summary><div class='sec-body'>"
            "<table class='sticky'><tr><th>标的</th>"
            "<th>各策略最新判断（点徽标下钻）</th></tr>%s</table>"
            "</div></details>" % (len(mixed), tr_mixed))

        # ② 共振做多榜
        tr_long = "".join(
            "<tr><td>%s</td><td><b style='color:#f85149'>%d 策略看多</b>"
            "</td><td>%s</td></tr>"
            % (_stock_link(s), s["n_long"], _hit_badges(s))
            for s in longs[:50]) or (
            "<tr><td colspan='3' class='dim'>当前无 ≥2 策略共振做多的标的"
            "</td></tr>")
        blk_long = (
            "<details class='sec' open><summary><h2>🔥 共振做多 · ≥2 策略"
            "同时看多（%d）</h2></summary><div class='sec-body'>"
            "<table class='sticky'><tr><th>标的</th><th>共振</th>"
            "<th>策略徽标（点徽标下钻）</th></tr>%s</table>"
            "</div></details>" % (len(longs), tr_long))

        # ③ 共振预警榜
        tr_short = "".join(
            "<tr><td>%s</td><td><b style='color:#3fb950'>%d 策略看空</b>"
            "</td><td>%s</td></tr>"
            % (_stock_link(s), s["n_short"], _hit_badges(s))
            for s in shorts[:30]) or (
            "<tr><td colspan='3' class='dim'>当前无 ≥2 策略共振看空的标的"
            "</td></tr>")
        blk_short = (
            "<details class='sec' open><summary><h2>🧊 共振预警 · ≥2 策略"
            "同时看空（%d）</h2></summary><div class='sec-body'>"
            "<table class='sticky'><tr><th>标的</th><th>共振</th>"
            "<th>策略徽标</th></tr>%s</table></div></details>"
            % (len(shorts), tr_short))

        # ④ 策略覆盖矩阵（点数字下钻到该交集明细）
        strats = d["strategies"]
        mtx = d["matrix"]
        mh = "".join("<th>%s</th>" % e(s["badge"]) for s in strats)
        mr = ""
        for a in strats:
            cells = ""
            for b in strats:
                n = mtx.get(a["id"], {}).get(b["id"], 0)
                if a["id"] == b["id"]:
                    cells += ("<td class='dim' style='text-align:center'>"
                              "%d</td>" % d["n_per"].get(a["id"], 0))
                elif n:
                    cells += ("<td style='text-align:center'>"
                              "<a href='/bt-cross?pair=%s,%s' "
                              "style='color:#58a6ff'><b>%d</b></a></td>"
                              % (a["id"], b["id"], n))
                else:
                    cells += "<td class='dim' style='text-align:center'>·</td>"
            mr += ("<tr><td><a href='%s' style='color:%s'>%s</a>"
                   "<div class='dim'>%s</div></td>%s</tr>"
                   % (a["url"], a["color"], e(a["name"]),
                      e(a["markets"]), cells))
        blk_mtx = (
            "<details class='sec'><summary><h2>📊 策略覆盖矩阵</h2>"
            "<span class='dim'>点击展开</span></summary><div class='sec-body'>"
            "<div class='dim' style='margin-bottom:6px'>对角线=该策略当前"
            "覆盖标的数 · 交叉格=两策略共同命中数（点击下钻看明细）· 矩阵"
            "随注册表自动扩展——新策略上架即多一行一列</div>"
            "<table><tr><th>策略</th>%s</tr>%s</table></div></details>"
            % (mh, mr))

        # ⑤ 两两交集明细（pair 下钻视图）
        blk_pair = ""
        try:
            from urllib.parse import urlparse as _up, parse_qs as _pq
            pair = (_pq(_up(self.path).query).get("pair", [""])[0])
        except Exception:
            pair = ""
        if pair and "," in pair:
            pa, pb = pair.split(",", 1)
            names = {s["id"]: s["name"] for s in strats}
            rows_p = "".join(
                "<tr><td>%s</td><td>%s</td></tr>"
                % (_stock_link(s), _hit_badges(s))
                for s in stocks
                if pa in {h["sid"] for h in s["hits"]}
                and pb in {h["sid"] for h in s["hits"]})
            blk_pair = (
                "<div class='sec'><h2>🔗 交集明细：%s × %s</h2>"
                "<table><tr><th>标的</th><th>命中明细</th></tr>%s</table>"
                "<div style='margin-top:8px'><a class='tp' href='/bt-cross'>"
                "← 返回全览</a></div></div>"
                % (e(names.get(pa, pa)), e(names.get(pb, pb)),
                   rows_p or "<tr><td colspan='2' class='dim'>无交集"
                   "</td></tr>"))

        # 市场筛选 tab
        mkts = sorted({str(s.get("market") or "").upper()
                       for s in d["stocks"] if s.get("market")})
        tabs = ("<a class='tp' href='/bt-cross'>%s全部</a>"
                % ("<b>" if not mkt_q else "")) + "".join(
            "<a class='tp' href='/bt-cross?m=%s'>%s%s%s</a>"
            % (e(m), "<b>" if mkt_q == m else "", e(m),
               "</b>" if mkt_q == m else "") for m in mkts)

        strat_cards = "".join(
            "<a class='gate' href='%s'><b style='color:%s'>%s"
            "<span style='float:right;font-size:12px;color:#8b949e'>%d 只"
            "</span></b><div class='dim' style='margin-top:6px'>覆盖 %s"
            "</div></a>"
            % (s["url"], s["color"], e(s["name"]),
               d["n_per"].get(s["id"], 0), e(s["markets"]))
            for s in strats)

        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>多策略共振 · 策略集市</title>"
                + self._COCKPIT_CSS + "</head><body>"
                + pill_nav("cross")
                + "<h1>🎯 多策略共振 · 策略集市</h1><div class='dim'>"
                "跨策略聚合发现层 · 同一标的被几个策略同时选中一眼可见 · "
                "数据截至 %s · 展示层聚合，不改任何策略口径</div>"
                "<div class='sec'><h2>🧺 上架策略（%d 个 · 注册表驱动，"
                "新策略注册即上架）</h2><div class='gates'>%s</div></div>"
                "<div class='sec'><h2>🌍 市场筛选</h2>%s</div>"
                % (e(d["generated_at"]), len(strats), strat_cards, tabs)
                + "<div class='duo'>" + blk_mixed + blk_long + "</div>"
                + blk_short + blk_mtx + blk_pair
                + "<div class='dim' style='margin-top:20px'>口径：方向语义"
                "三分（看多/看空/中性）· 缠论取近 90 天最新信号 · 九维≥80 "
                "入榜（观察工具口径，非买入建议）· 四策略≥3/4 持仓记看多 · "
                "聚合为纯读数，不推送不构成投资建议</div></body></html>")
        self._html(body)


    # ── K56-B4 数据大屏 /screen：四市场穿透视图（此前只有 /api/screen
    #    数据源、页面 404——导航「📺 数据大屏」按钮常年空指，本批补齐）──
    def _screen_page(self):
        import html as _h
        from urllib.parse import quote as _q
        e = _h.escape
        s = build_screen()
        _TB = ("border-collapse:collapse;width:100%;font-size:12.5px")
        _TD = ("padding:5px 10px;border-bottom:1px solid #21262d;"
               "color:#e6edf3;white-space:nowrap")

        def _sec(title, href, hint):
            return ("<div style='margin:14px 0 6px;display:flex;"
                    "align-items:baseline;gap:10px'>"
                    "<span style='font-size:14px;font-weight:700;"
                    "color:#e6edf3'>%s</span>"
                    "<a href='%s' style='font-size:11px;color:#58a6ff;"
                    "text-decoration:none'>进详情 →</a>"
                    "<span style='font-size:11px;color:#8b949e'>%s</span></div>"
                    % (title, href, hint))

        # ① 四市场环境灯（K56 穿透：每卡 🔍 直达底表数据行）
        _MKT = [("CN", "🇨🇳 A股", "daily"), ("US", "🇺🇸 美股", "us_daily"),
                ("HK", "🇭🇰 港股", "hk_daily"), ("CRYPTO", "🪙 加密", "crypto_daily")]
        env_by = {x.get("market"): x for x in s.get("envs") or []}
        cards = []
        for mk, label, tb in _MKT:
            x = env_by.get(mk) or {}
            sc = x.get("score")
            col = ("#3fb950" if x.get("safe")
                   else "#f85149" if sc is not None and sc < 40
                   else "#d29922")
            state = e(str(x.get("state") or "—"))
            cards.append(
                "<div style='flex:1;min-width:180px;background:#161b22;"
                "border:1px solid #30363d;border-radius:10px;padding:10px 14px'>"
                "<div style='font-size:14px;font-weight:700;color:#e6edf3'>%s"
                "<span style='cursor:pointer;margin-left:8px;font-size:12px' "
                "title='穿透看 %s 数据' onclick=\"tp_toggle('%s')\">🔍</span>"
                "<span style='font-size:11px;color:%s;margin-left:8px'>%s</span>"
                "</div><div style='margin-top:6px;font-size:12px;"
                "font-family:Consolas,monospace'>评分 <b style='color:%s'>%s</b>"
                "</div>%s"
                "<div id='tpr-%s' style='display:none'>"
                "<div id='tpc-%s' style='padding:6px 0 0'></div></div>"
                "</div>"
                % (label, tb, tb, col, state, col,
                   ("—" if sc is None else "%d" % sc),
                   ("<div style='margin-top:4px;font-size:11px;color:#8b949e'>"
                    "环境快照缺（待 rps 刷新）</div>" if not x else ""),
                   tb, tb))
        m_env = ("<div style='display:flex;gap:10px;flex-wrap:wrap'>%s</div>"
                 % "".join(cards))

        # ② 策略概览
        st_rows = []
        for st in s.get("strategies") or []:
            bm = st.get("by_market") or {}
            st_rows.append(
                "<tr><td style='%s'>%s <a href='%s' style='color:#58a6ff;"
                "text-decoration:none'>%s</a></td><td style='%s;color:%s'>%d</td>"
                "<td style='%s;color:#8b949e'>CN %d · US %d · HK %d · CR %d</td>"
                "</tr>"
                % (_TD, e(str(st.get("icon") or "")),
                   e(str(st.get("url") or "#")), e(str(st.get("name") or "")),
                   _TD, e(str(st.get("color") or "#e6edf3")),
                   st.get("n") or 0, _TD,
                   bm.get("CN", 0), bm.get("US", 0), bm.get("HK", 0),
                   bm.get("CRYPTO", 0)))
        m_st = (_sec("② 策略概览 · 选股×市场", "/bt-rps",
                     "每策略最新选股数与市场分布")
                + ("<table style='%s'><tr>%s</tr>%s</table>"
                   % (_TB,
                      "".join("<th style='%s;background:#21262d;text-align:"
                              "left'>%s</th>" % (_TD, x)
                              for x in ("策略", "选股", "市场分布")),
                      "".join(st_rows))
                   if st_rows else "<div style='color:#8b949e;font-size:12px'>"
                                   "无策略数据（cross_poll 待跑）</div>"))

        # ③ 选股明细 top40（代码→图谱下钻）
        pd_rows = []
        for p in (s.get("pick_detail") or [])[:40]:
            mk = e(str(p.get("market") or ""))
            code = e(str(p.get("code") or ""))
            pd_rows.append(
                "<tr><td style='%s;color:#8b949e'>%s</td>"
                "<td style='%s'><a href='/kg?c=%s' style='color:#58a6ff;"
                "text-decoration:none'>%s</a></td>"
                "<td style='%s'>%s</td><td style='%s;color:#d29922'>%d</td>"
                "<td style='%s;color:#8b949e;font-size:11px'>%s</td></tr>"
                % (_TD, mk, _TD, _q(str(p.get("code") or "")), code, _TD,
                   e(str(p.get("name") or "")), _TD, p.get("n_hit") or 0, _TD,
                   e(" ".join(str(h) for h in p.get("hits") or []))))
        m_pd = (_sec("③ 选股明细 · 多策略命中", "/kg", "点代码进图谱")
                + ("<div style='max-height:340px;overflow-y:auto'>"
                   "<table style='%s'><tr>%s</tr>%s</table></div>"
                   % (_TB,
                      "".join("<th style='%s;background:#21262d;text-align:"
                              "left'>%s</th>" % (_TD, x)
                              for x in ("市场", "代码", "名称", "命中", "策略")),
                      "".join(pd_rows))
                   if pd_rows else "<div style='color:#8b949e;font-size:12px'>"
                                   "本日无选股</div>"))

        # ④ 行业细分（top12 条形）
        ind = s.get("industry") or []
        mx = max((x["n"] for x in ind), default=0) or 1
        bars = "".join(
            "<div style='display:flex;align-items:center;gap:8px;"
            "margin:3px 0'><span style='width:110px;font-size:11.5px;"
            "color:#8b949e;overflow:hidden;text-overflow:ellipsis;"
            "white-space:nowrap'>%s</span><div style='height:12px;"
            "border-radius:3px;background:linear-gradient(90deg,#1f6feb,"
            "#388bfd);width:%d%%'></div><span style='font-size:11px;"
            "color:#e6edf3'>%d</span></div>"
            % (e(str(x.get("name") or "")), round(x.get("n", 0) * 100 / mx),
               x.get("n", 0))
            for x in ind)
        m_ind = (_sec("④ A股行业细分", "/sa", "九维 full 池行业计数")
                 + (bars or "<div style='color:#8b949e;font-size:12px'>"
                             "无行业数据（九维待跑）</div>"))

        # ⑤ 账户态势（四盘 + 母基金）
        pf = s.get("portfolio") or {}
        accs = pf.get("accounts") or {}
        ac_rows = []
        for sid, a in accs.items():
            ret = a.get("ret") or 0
            today = a.get("today") or 0
            dd = a.get("drawdown") or 0
            ddv = a.get("dd_lv") or 0
            dd_col = ("#f85149" if ddv >= 2 else "#d29922" if ddv == 1
                      else "#3fb950")
            ac_rows.append(
                "<tr><td style='%s'>%s %s</td>"
                "<td style='%s;color:%s'>%.2f%%</td>"
                "<td style='%s;color:%s'>%+.2f%%</td>"
                "<td style='%s;color:%s'>%.2f%%<span style='font-size:10px;"
                "margin-left:4px'>L%d</span></td>"
                "<td style='%s;color:#8b949e'>胜率 %s · 持/平 %d/%d</td>"
                "<td style='%s'>%s</td></tr>"
                % (_TD, e(str(a.get("icon") or "")), e(str(a.get("name") or sid)),
                   _TD, "#f85149" if ret >= 0 else "#3fb950", ret,
                   _TD, "#f85149" if today >= 0 else "#3fb950", today,
                   _TD, dd_col, dd, ddv,
                   _TD,
                   ("%.0f%%" % (a.get("win_rate") * 100))
                   if a.get("win_rate") is not None else "—",
                   a.get("n_open") or 0, a.get("n_closed") or 0,
                   _TD, e("SR %s" % ("%.2f" % a["sharpe"])
                          if a.get("sharpe") is not None else "—")))
        fof = pf.get("fof")
        fof_line = (""
                    if not fof else
                    "<div style='margin-top:6px;font-size:12px;"
                    "color:#8b949e'>母基金 ps %s · 累计 %s%% · SR %s · "
                    "MaxDD %s%%</div>"
                    % (fof.get("ps"), fof.get("ret"), fof.get("sharpe"),
                       fof.get("maxdd_pct")))
        m_pf = (_sec("⑤ 账户态势 · 模拟盘", "/bt-report", "点进回测报告")
                + (("<table style='%s'><tr>%s</tr>%s</table>"
                    % (_TB,
                       "".join("<th style='%s;background:#21262d;text-align:"
                               "left'>%s</th>" % (_TD, x)
                               for x in ("账户", "累计", "今日", "回撤",
                                         "胜率/持仓", "指标")),
                       "".join(ac_rows))
                    + fof_line) if ac_rows else
                   ("<div style='color:#8b949e;font-size:12px'>无账户数据"
                    "</div>" + fof_line)))

        # ⑥ 账本成绩
        books = s.get("books") or {}
        bk_rows = []
        for bid, b in books.items():
            bk_rows.append(
                "<tr><td style='%s'>%s</td><td style='%s'>%d / %d</td>"
                "<td style='%s;color:%s'>%s</td><td style='%s;color:#8b949e'>"
                "%s</td></tr>"
                % (_TD, e(str(b.get("name") or bid)),
                   _TD, b.get("n_open") or 0, b.get("n_closed") or 0,
                   _TD, "#3fb950",
                   ("%.0f%%" % ((b.get("recent_win_rate")
                                 or b.get("win_rate") or 0) * 100)),
                   _TD, e(str(b.get("ts") or ""))))
        m_bk = (_sec("⑥ 独立账本", "/bt-report", "不入统一账户")
                + ("<table style='%s'><tr>%s</tr>%s</table>"
                   % (_TB,
                      "".join("<th style='%s;background:#21262d;text-align:"
                              "left'>%s</th>" % (_TD, x)
                              for x in ("账本", "持仓/平仓", "胜率", "时点")),
                      "".join(bk_rows))
                   if bk_rows else "<div style='color:#8b949e;font-size:12px'>"
                                   "无账本</div>"))

        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<title>数据大屏 · 老田的AI助手</title></head><body "
                "style='background:#0d1117;margin:0;padding:12px 16px'>"
                + self._COCKPIT_CSS + self._KG_CSS
                + pill_nav("screen")
                + "<div style='color:#e6edf3;font-size:18px;font-weight:700;"
                  "margin:10px 0 2px'>📺 数据大屏 · 四市场穿透</div>"
                  "<div style='color:#8b949e;font-size:12px;margin-bottom:6px'>"
                  "A股 · 美股 · 港股 · 加密 —— 每张卡 🔍 直达底表数据行 · "
                  "快照 %s（环境 %s · 九维 %s）</div>"
                % (e(str(s.get("ts") or "")), e(str(s.get("rps_ts") or "")),
                   e(str(s.get("nd_ts") or "")))
                + _sec("① 四市场环境灯", "/bt-data-health", "🔍 穿透到底表")
                + m_env + m_st + m_pd + m_ind + m_pf + m_bk + _TP_JS
                + "<div style='color:#8b949e;font-size:11px;margin:16px 0'>"
                  "数据源 /api/screen（60s 缓存）· 穿透 /api/bridge/"
                  "table_preview（白名单 15 表）</div>"
                + "</body></html>")
        self._html(body)

    # ── K64 当日买卖点全量页（大屏"全屏 »"下钻目标） ──
    def _screen_sig_page(self):
        import html as _h
        from urllib.parse import quote as _q
        e = _h.escape
        s = build_screen()
        rows = s.get("signals") or []
        day = s.get("sig_day") or "—"

        def _f(v):
            return e(str(v)) if v is not None else "—"

        trs = []
        for r in rows:
            t = str(r.get("type") or "")
            strong = t in ("1买", "1卖") or (r.get("tbe") or 0) >= 80
            col = ("#f85149" if "买" in t else "#3fb950" if "卖" in t
                   else "#e6edf3")
            trs.append(
                "<tr data-t='%s' data-q='%s %s %s' style='cursor:pointer'"
                " onclick=\"location.href='/bt-linkage?code=%s'\""
                "%s><td><b style='color:%s'>%s%s</b></td>"
                "<td>%s <span class='dim num'>%s</span></td>"
                "<td class='num dim'>%s</td><td class='num dim'>%s</td>"
                "<td class='num dim'>%s</td><td class='num'>%s</td>"
                "<td class='num'>%s</td></tr>"
                % (e(t), e(str(r.get("name") or "")), e(str(r.get("code") or "")),
                   e(str(r.get("period") or "")), _q(str(r.get("code") or "")),
                   (" style='box-shadow:inset 2px 0 0 #f59e0b'" if strong
                    else ""), col, ("⚡" if strong else ""), e(t),
                   e(str(r.get("name") or "")), e(str(r.get("code") or "")),
                   _f(r.get("period")), _f(r.get("dt")), _f(r.get("detect")),
                   _f(r.get("px")), _f(r.get("npv"))))
        n_buy = sum(1 for r in rows if "买" in str(r.get("type")))
        n_sell = sum(1 for r in rows if "卖" in str(r.get("type")))
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta http-equiv='refresh' content='60'>"
                "<title>当日买卖点 · %s</title>"
                "<style>body{background:#0d1117;color:#c9d1d9;"
                "font-family:Microsoft YaHei,Arial;margin:0;padding:16px}"
                "table{border-collapse:collapse;width:100%%;font-size:13px}"
                "th{background:#21262d;text-align:left;padding:7px 10px;"
                "color:#8b949e;font-weight:600;position:sticky;top:0}"
                "td{padding:6px 10px;border-bottom:1px solid #21262d;"
                "white-space:nowrap}tr:hover td{background:#1c2128}"
                ".dim{color:#8b949e}.num{font-family:Consolas,monospace}"
                ".bar{display:flex;gap:10px;align-items:center;"
                "flex-wrap:wrap;margin:8px 0 12px}"
                ".chip{padding:4px 12px;border-radius:14px;cursor:pointer;"
                "border:1px solid #30363d;font-size:12.5px;background:#161b22}"
                ".chip.on{border-color:#58a6ff;color:#58a6ff}"
                "input{background:#161b22;border:1px solid #30363d;"
                "border-radius:6px;padding:6px 10px;color:#e6edf3;"
                "font-size:13px;width:180px}</style></head><body>"
                "<div style='display:flex;align-items:baseline;gap:14px'>"
                "<span style='font-size:19px;font-weight:700;color:#e6edf3'>"
                "⚡ 当日买卖点 · %s</span>"
                "<span style='font-size:12.5px;color:#8b949e'>检出 %d 条 · "
                "买 %d · 卖 %d · 60s 自动刷新 · 点击行进联动页</span></div>"
                "<div class='bar'>"
                "<span class='chip on' onclick=\"flt('')\">全部</span>"
                "<span class='chip' onclick=\"flt('买')\">买点</span>"
                "<span class='chip' onclick=\"flt('卖')\">卖点</span>"
                "<input placeholder='搜索 名称/代码/周期' oninput='flt(this.value)'"
                " id='q'></div>"
                "<table><tr><th>级别</th><th>标的</th><th>周期</th>"
                "<th>信号日</th><th>检出日</th><th>价</th><th>NPV</th>"
                "</tr>%s</table>"
                "<script>function flt(v){v=(v||'').trim();var t=v===''?'':"
                "(v==='买'||v==='卖'?v:'');var q=(t?'':v).toLowerCase();"
                "document.querySelectorAll('tr[data-t]').forEach(function(r){"
                "var ok=(!t||r.dataset.t.indexOf(t)>=0)&&(!q||r.dataset.q."
                "toLowerCase().indexOf(q)>=0);r.style.display=ok?'':'none'});"
                "document.querySelectorAll('.chip').forEach(function(c){"
                "c.classList.toggle('on',c.textContent.indexOf(v)===0&&v)});"
                "if(v!=='买'&&v!=='卖'&&v)document.getElementById('q').focus()"
                "}</script></body></html>"
                % (e(day), e(day), len(rows), n_buy, n_sell,
                   "".join(trs))).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)









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

    def _weak_cases_page(self):
        """📚 弱于板块案例库：在案状态流摘要 + 案例档案 + 最新扫描（纯读，
        不触发任何重算/拉取；数据源 reports/weak_track_*.jsonl、
        weak_cases/*、weak_scan_*.csv，均 weak 产线落盘产物）。"""
        import html as _h
        import json as _j
        import csv as _csv
        import glob as _g
        from datetime import datetime as _dt
        e = _h.escape
        REP = os.path.join(STATION, "reports")
        WC = os.path.join(REP, "weak_cases")

        def _last(fp):
            last, n = None, 0
            try:
                with open(fp, encoding="utf-8") as f:
                    for ln in f:
                        ln = ln.strip()
                        if not ln:
                            continue
                        try:
                            last = _j.loads(ln.lstrip("\ufeff"))
                            n += 1
                        except Exception:
                            pass
            except Exception:
                pass
            return last, n

        def _rev_md(c6):
            m = sorted(_g.glob(os.path.join(WC, "weak_review_%s_*.md" % c6)))
            return os.path.basename(m[-1]) if m else ""

        td = "border-collapse:collapse;font-size:13px"
        th = ("background:#161b22;border:1px solid #30363d;padding:4px 8px;"
              "text-align:left")
        tdd = "border:1px solid #30363d;padding:4px 8px"

        # ① 在案（活跃状态流）
        rows = []
        for fp in sorted(_g.glob(os.path.join(REP, "weak_track_*.jsonl"))):
            if ".closed_" in os.path.basename(fp):
                continue
            st, n = _last(fp)
            if not st:
                continue
            c6 = os.path.basename(fp)[len("weak_track_"):-len(".jsonl")]
            code = st.get("code", c6)
            ex = st.get("ex250")
            trig = st.get("trig") or []
            rev = _rev_md(c6)
            rows.append(
                "<tr><td style='%s'><b>%s</b></td><td style='%s'>%s · %s</td>"
                "<td style='%s'>%s</td><td style='%s'>%s</td>"
                "<td style='%s'>%s</td><td style='%s'>%d 次</td>"
                "<td style='%s'>%s%s</td></tr>" % (
                    tdd, e(code), tdd, e(str(st.get("px_d", "?"))[5:]),
                    st.get("px"), tdd,
                    ("%+.1fpp" % ex) if ex is not None else "—", tdd,
                    e("/".join(str(x).replace("（均线缠绕）", "·缠")[:8]
                               for x in (st.get("trend") or ["—", "—", "—"]))),
                    tdd,
                    ("<span style='color:#f85149'>⚠ %s</span>"
                     % e("；".join(trig))) if trig else "—", tdd, n, tdd,
                    ("<a href='/bt-weak-md?f=%s'>档案</a>" % e(rev))
                    if rev else "—",
                    " ｜ <a href='/bt-linkage?code=%s'>联动</a>" % e(code)))
        live_html = (
            "<table style='%s'><tr><th style='%s'>案例</th><th style='%s'>"
            "数据日·现价</th><th style='%s'>超额250</th><th style='%s'>趋势 "
            "个/板/沪</th><th style='%s'>触发</th><th style='%s'>运行</th>"
            "<th style='%s'>链接</th></tr>%s</table>" % (
                td, th, th, th, th, th, th, th,
                "".join(rows) or ("<tr><td style='%s' colspan='7'>无在案"
                                  "</td></tr>" % tdd))) if rows else \
            "<div style='color:#8b949e'>无在案案例</div>"

        # ② 已结案（closed 状态流 + close 报告）
        cl = sorted(_g.glob(os.path.join(REP, "weak_track_*.closed_*.jsonl")))
        cl_rows = []
        for fp in cl:
            base = os.path.basename(fp)
            c6 = base[len("weak_track_"):].split(".")[0]
            md = sorted(_g.glob(os.path.join(WC, "weak_close_%s_*.md" % c6)))
            cl_rows.append(
                "<li>%s — <a href='/bt-weak-md?f=%s'>结案报告</a></li>"
                % (e(c6), e(os.path.basename(md[-1]))) if md
                else "<li>%s</li>" % e(c6))
        closed_html = ("<ul style='font-size:13px'>%s</ul>"
                       % "".join(cl_rows)) if cl_rows else \
            "<div style='color:#8b949e'>尚无结案</div>"

        # ③ 最新扫描（重点候选 ★ 优先展示——二道筛 focus 列）
        scan_html = "<div style='color:#8b949e'>尚无扫描</div>"
        sc = sorted(_g.glob(os.path.join(REP, "weak_scan_*.csv")))
        if sc:
            fp = sc[-1]
            with open(fp, encoding="utf-8-sig") as f:
                hits = list(_csv.DictReader(f))
            n_focus = sum(1 for h in hits
                          if (h.get("focus") or "").strip())
            focus = [h for h in hits if (h.get("focus") or "").strip()]
            plain = [h for h in hits if not (h.get("focus") or "").strip()]

            def _fpri(foc):
                # 二道筛价值优先级：强转弱＞强失速＞深度垫底（与卡片一致）
                if "强转弱" in foc:
                    return 0
                if "强失速" in foc:
                    return 1
                return 2

            focus.sort(key=lambda h: (
                _fpri((h.get("focus") or "").strip()),
                float(h.get("ex") or 0)))   # 同级按超额最差
            top = (focus + plain)[:10]

            def _srow(h):
                foc = (h.get("focus") or "").strip()
                star = ("<span style='color:#e3a008'>★%s</span>"
                        % e(foc)) if foc else \
                       "<span style='color:#586e75'>—</span>"
                nm = e(h.get("name", ""))
                if foc.startswith("强转弱"):
                    nm = ("<span style='color:#f85149'>%s</span>" % nm)
                return ("<tr><td style='%s'>%s</td><td style='%s'>%s</td>"
                        "<td style='%s'>%s</td><td style='%s'>%s</td>"
                        "<td style='%s'>%.1f</td>"
                        "<td style='%s'>%.0f%%（%s）</td></tr>" % (
                            tdd, e(h.get("code", "")), tdd, nm,
                            tdd, e(h.get("industry", "")), tdd, star, tdd,
                            float(h.get("ex") or 0), tdd,
                            float(h.get("pct_in") or 0),
                            e(h.get("rank_in", ""))))

            thead = ("<tr><th style='%s'>代码</th><th style='%s'>名称</th>"
                     "<th style='%s'>行业</th><th style='%s'>重点</th>"
                     "<th style='%s'>超额pp</th>"
                     "<th style='%s'>板块内分位</th></tr>"
                     % (th, th, th, th, th, th))
            # 非重点参考小表（超额最差前 5）：主表被重点占满时保底可见
            plain_html = ""
            if plain and len(focus) >= 10:
                plain_html = (
                    "<div style='font-size:12px;color:#8b949e;margin:10px 0 "
                    "4px'>非重点参考（超额最差前 5）</div>"
                    "<table style='%s'>%s%s</table>" % (
                        td, thead, "".join(_srow(h) for h in plain[:5])))
            scan_html = (
                "<div style='font-size:12px;color:#8b949e;margin:4px 0'>"
                "%s ｜ 候选 %d 只 ｜ 重点 ★%d（二道筛）｜ %s</div>"
                "<table style='%s'>%s%s</table>"
                "<div style='font-size:12px;color:#8b949e'>重点候选优先（强"
                "转弱标红，余按超额最差补足），全量见 CSV</div>%s" % (
                    e(os.path.basename(fp)), len(hits), n_focus,
                    e(_dt.fromtimestamp(os.path.getmtime(fp)).strftime(
                        "%Y-%m-%d %H:%M")),
                    td, thead, "".join(_srow(h) for h in top), plain_html))

        self._html(
            # K-STOCK-052 界面修复：此前输出裸 HTML 片段（无 DOCTYPE/CSS 壳，
            # 浏览器渲染成白底裸内容=「还没有界面」）；补全页壳+域导航，
            # 与兄弟页（bt-qpzm/bt-9dim）同口径。
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,"
            "initial-scale=1'><title>📚 弱于板块案例库</title>"
            + self._COCKPIT_CSS + "</head><body>"
            + pill_nav("cases")
            + "<h1>📚 弱于板块案例库 <span style='font-size:14px;"
            "color:#8b949e'>weak 产线：扫描→建案→归因→跟踪→结案 · "
            "影子观察</span></h1>"
            "<h2 style='margin:14px 0 6px'>① 在案案例（每日 15:30 "
            "WeakTrackCheck 自动监测）</h2>%s"
            "<h2 style='margin:14px 0 6px'>② 已结案</h2>%s"
            "<h2 style='margin:14px 0 6px'>③ 最新信号2 扫描（每周一 15:35 "
            "WeakScan）</h2>%s"
            "<div style='color:#8b949e;font-size:12px;margin-top:16px;"
            "border-top:1px solid #30363d;padding-top:8px'>本页纯读展示，"
            "数据为产线落盘产物；仅影子复盘，不构成买卖依据。</div>"
            "</body></html>"
            % (live_html, closed_html, scan_html))

    def _weak_md(self, qp):
        """案例档案/结案报告 md 原文查看（文件名白名单防穿越）。"""
        import html as _h
        import re as _re
        f = ((qp.get("f") or [""])[0].strip())
        if not _re.fullmatch(r"weak_(review|close)_[A-Za-z0-9]+(_.+)?\.md", f):
            self.send_error(404)
            return
        fp = os.path.join(STATION, "reports", "weak_cases", f)
        if not os.path.exists(fp):
            self.send_error(404)
            return
        with open(fp, encoding="utf-8") as fh:
            txt = fh.read()
        self._html(
            "<div style='font-size:13px;margin-bottom:8px'>"
            "<a href='/bt-weak'>← 案例库</a> ｜ %s</div>"
            "<pre style='white-space:pre-wrap;font-size:13px;"
            "line-height:1.6'>%s</pre>" % (_h.escape(f), _h.escape(txt)))

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

    def _sa_start(self, q):
        q = (q or "").strip()[:20]
        if not q:
            self._json({"error": "请输入股票"}, 400)
            return
        if SA_BUSY["v"]:
            self._json({"error": "正在分析上一只，请稍候"}, 429)
            return
        import threading
        import uuid
        jid = uuid.uuid4().hex[:12]
        SA_BUSY["v"] = True
        SA_JOBS[jid] = {"status": "running"}
        threading.Thread(target=_sa_worker, args=(jid, q),
                         daemon=True).start()
        self._json({"job": jid})

    def _sa_result(self, jid):
        j = SA_JOBS.get(jid)
        if not j:
            self._json({"error": "job not found"}, 404)
            return
        self._json({"status": j["status"], "d": j.get("d")})



    @classmethod
    def _stock_valuation(cls, ts_code):
        """A股估值（tushare daily_basic 单股）：pe_ttm/总市值/换手率，
        portal 侧独立 1 日小缓存，命中零 API。"""
        cf = os.path.join(QBT_DIR, "data", "portal_stock_val_cache.json")
        cache = {}
        try:
            if time.time() - os.path.getmtime(cf) < 86400:
                cache = json.load(open(cf, encoding="utf-8"))
        except Exception:
            cache = {}
        if ts_code in cache:
            return cache[ts_code]
        val = None
        try:
            if QBT_DIR not in sys.path:
                sys.path.insert(0, QBT_DIR)
            import nine_dim as _ND
            rows, fields = _ND._ts(
                "daily_basic", ts_code=ts_code,
                fields="ts_code,trade_date,pe_ttm,total_mv,turnover_rate")
            i = {f: k for k, f in enumerate(fields)}
            if rows:
                val = {f: rows[0][i[f]] for f in fields if f != "ts_code"}
                cache[ts_code] = val
                try:
                    json.dump(cache, open(cf, "w", encoding="utf-8"),
                              ensure_ascii=False)
                except Exception:
                    pass
        except Exception:
            val = None
        return val

    @classmethod
    def _stock_fundamental(cls, code):
        """个股基本面核心指标 → (rows, note)；rows=[(标签,值)]。
        A股：估值（1日缓存）+ 财务（nine_dim._fina 5日缓存，ROE/毛利
        /增速/负债率）；港股：tushare 逐码 fina parquet 最新行；
        美股：财报库为数字代码体系（无字母 ticker 文件），暂缺。"""
        import re
        try:
            rows, note = [], ""
            c = str(code or "")
            m6 = re.search(r"\d{6}", c)
            mhk = re.search(r"hk(\d{1,5})", c, re.I)
            if m6:                       # A股（6 位连续数字，兼容
                c6 = m6.group(0)         # sh600519 / 600519.SH 等形态）
                ts_code = (c6 + ".SH" if c6[0] == "6" else
                           c6 + ".SZ" if c6[0] in "03" else
                           c6 + ".BJ" if c6[0] in "48" else "")
                if not ts_code:
                    return [], ""
                val = cls._stock_valuation(ts_code)
                if val:
                    try:
                        rows += [("估值", "PE(TTM)",
                                  "%.1f" % float(val["pe_ttm"]))
                                 if val.get("pe_ttm") else
                                 ("估值", "PE(TTM)", "—"),
                                 ("估值", "总市值(亿)", "%.0f" % (
                                     float(val["total_mv"]) / 1e4))
                                 if val.get("total_mv") else
                                 ("估值", "总市值(亿)", "—")]
                    except (TypeError, ValueError):
                        pass
                    rows.append(("估值", "换手率", _fmt_pct(
                        val.get("turnover_rate"))))
                if QBT_DIR not in sys.path:
                    sys.path.insert(0, QBT_DIR)
                import nine_dim as _ND
                fin = (_ND._fina([ts_code]) or {}).get(ts_code) or {}
                cur = fin.get("cur") or {}
                prv = fin.get("prev") or {}

                def _g2(k):
                    # K235 成长组：本期 + 前期对照（财报趋势一眼可见）
                    v = _fmt_pct(cur.get(k))
                    p = prv.get(k)
                    if v != "—" and p not in (None, ""):
                        try:
                            v += "（前值 %s）" % _fmt_pct(float(p))
                        except (TypeError, ValueError):
                            pass
                    return v
                _eb_s = "—"
                try:                      # tushare ebitda 单位：万元
                    _ebv = float(cur.get("ebitda") or 0)
                    if _ebv:
                        _eb_s = (("%.2f亿" % (_ebv / 1e4))
                                 if abs(_ebv) >= 1e4
                                 else "%.0f万" % _ebv)
                except (TypeError, ValueError):
                    pass
                # K235 扩容：(组, 标签, 值) 三元组——估值/盈利/成长/健康
                # 四组汇总；新增现金流覆盖(ocf_to_or)与 EBITDA
                rows += [("盈利", "ROE", _fmt_pct(cur.get("roe"))),
                         ("盈利", "毛利率", _fmt_pct(
                             cur.get("grossprofit_margin"))),
                         ("盈利", "现金流/营收", _fmt_pct(
                             cur.get("ocf_to_or"))),
                         ("盈利", "EBITDA", _eb_s),
                         ("成长", "营收增速", _g2("or_yoy")),
                         ("成长", "净利增速", _g2("netprofit_yoy")),
                         ("健康", "资产负债率", _fmt_pct(
                             cur.get("debt_to_assets")))]
                if cur.get("end_date"):
                    note = "财务报告期 %s · 估值 %s" % (
                        cur["end_date"], val.get("trade_date", "—")
                        if val else "—")
            elif mhk or re.fullmatch(r"\d{1,5}", c):   # 港股
                sym = (mhk.group(1) if mhk else c).zfill(5)
                p = os.path.join(QBT_DIR, "data", "tushare",
                                 "hk_fina_indicator", sym + "_HK.parquet")
                if not os.path.exists(p):
                    return [], "暂无基本面数据（港股财报库未覆盖 %s）" % sym
                import pandas as _pd
                df = _pd.read_parquet(p)
                r = df.sort_values("end_date").iloc[-1]
                rows = []
                try:
                    if r.get("pe_ttm"):
                        rows.append(("估值", "PE(TTM)",
                                     "%.1f" % float(r["pe_ttm"])))
                    if r.get("total_market_cap"):
                        rows.append(("估值", "总市值(亿)", "%.0f" % (
                            float(r["total_market_cap"]) / 1e8)))
                except (TypeError, ValueError):
                    pass
                rows += [("盈利", "ROE", _fmt_pct(r.get("roe_avg"))),
                         ("盈利", "毛利率", _fmt_pct(
                             r.get("gross_profit_ratio"))),
                         ("成长", "营收增速", _fmt_pct(
                             r.get("operate_income_yoy"))),
                         ("成长", "净利增速", _fmt_pct(
                             r.get("holder_profit_yoy"))),
                         ("健康", "资产负债率", _fmt_pct(
                             r.get("debt_asset_ratio")))]
                note = "港股 · %s（%s 财币）" % (
                    r.get("report_type") or "", r.get("currency") or "HKD")
            else:                        # 美股等：数字代码体系不覆盖
                return [], ("暂无基本面数据（美股财报库未覆盖字母"
                            "代码 %s）" % (c or "—"))
            return rows, note
        except Exception:
            return [], "基本面数据暂不可用"

    @staticmethod
    def _stock_notices(code):
        """A股公告（带原文 url）+ 研报（pdf_url）：tdx_info.db 直查。
        返回 (anns, rpts)；非 A 股或无库静默 ([], [])。"""
        import re
        import sqlite3
        anns, rpts = [], []
        try:
            m6 = re.search(r"\d{6}", code or "")
            if not m6:
                return [], []
            c6 = m6.group(0)
            ts_code = (c6 + ".SH" if c6[0] == "6" else
                       c6 + ".SZ" if c6[0] in "03" else
                       c6 + ".BJ" if c6[0] in "48" else "")
            if not ts_code:
                return [], []
            db = os.path.join(QBT_DIR, "data", "tdx", "tdx_info.db")
            if not os.path.exists(db):
                return [], []
            cn = sqlite3.connect(db, timeout=5)
            try:
                anns = [{"title": x[0], "date": x[1], "url": x[2]}
                        for x in cn.execute(
                            "SELECT title, notice_date, url FROM "
                            "stock_notice WHERE ts_code=? ORDER BY "
                            "notice_date DESC LIMIT 8", (ts_code,))]
                rpts = [{"name": x[0], "rating": x[1], "org": x[2],
                         "date": x[3], "url": x[4]}
                        for x in cn.execute(
                            "SELECT report_name, rating, org, pub_date, "
                            "pdf_url FROM stock_report WHERE ts_code=? "
                            "ORDER BY pub_date DESC LIMIT 5", (ts_code,))]
            finally:
                cn.close()
        except Exception:
            return [], []
        return anns, rpts






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









    def _bridge_page(self, qs):
        """K56-B1 投研驾驶舱 /bridge：顶板五仪——数据顶板（15 表+服务灯）/
        运行仪表（今日跑批+投委会）/预警仪（bad 任务+待跟进+警报池+需注意）/
        结构图台账（缠论池近 15 票）/账户态势（4 模拟盘）。服务端渲染，只读；
        每条数据绑下钻目的地（图即导航），横幅红黄绿分级提醒。"""
        import html as _h
        e = _h.escape
        s = bridge_summary()
        _TB = ("border-collapse:collapse;width:100%;font-size:12px;"
               "font-family:Consolas,monospace")
        _TD = "border:1px solid #30363d;padding:3px 8px;color:#e6edf3"
        _H2 = ("color:#e6edf3;font-size:13px;font-weight:600;"
               "margin:14px 0 6px;border-left:3px solid #58a6ff;"
               "padding-left:8px")

        def _sec(title, href, hint):
            return ("<div style='%s'>%s <a href='%s' style='color:var(--blu-500);"
                    "font-size:11px;text-decoration:none'>进详情 →</a>"
                    "<span style='color:var(--gray-5);font-size:11px;"
                    "margin-left:8px'>%s</span></div>"
                    % (_H2, title, href, hint))

        # ---- 舱头横幅：红黄绿分级 + 红黄计数（w=预热中）----
        warming = bool(s.get("warming"))
        lv = "w" if warming else s.get("level", "g")
        col, txt = {"r": ("#f85149", "红 · 今日必须处理"),
                    "y": ("#d29922", "黄 · 知晓即可"),
                    "g": ("#3fb950", "绿 · 安心"),
                    "w": ("#58a6ff", "⏳ 时点校对预热中")}[lv]
        reds = s.get("red_tables") or []
        lags = s.get("lag_tables") or []
        bad = s.get("tasks", {}).get("bad") or []
        detail = []
        if reds:
            detail.append("空表: " + ", ".join(str(x) for x in reds[:5]))
        if bad:
            detail.append("坏任务: " + ", ".join(
                str(b.get("name")) for b in bad[:5]))
        if lags:
            detail.append("滞后: " + ", ".join(str(x) for x in lags[:5]))
        detail_txt = ("15 表时点校对装载中（~100s）· 页面 30s 自动刷新"
                      if warming
                      else ("；".join(detail) if detail
                            else "各表在容忍内，无坏任务"))
        banner = ("<div style='border:1px solid %s;border-radius:10px;"
                  "padding:10px 14px;margin:10px 0;display:flex;"
                  "justify-content:space-between;align-items:center;"
                  "background:var(--gray-2)'>"
                  "<div><b style='color:%s;font-size:15px'>● %s</b>"
                  "<span style='color:var(--gray-5);font-size:12px;"
                  "margin-left:10px'>数据期望日 %s · 体检 %s · 聚合 %s</span>"
                  "<div style='color:var(--gray-5);font-size:11px;margin-top:3px'>%s</div></div>"
                  "<div style='font-size:13px;color:var(--gray-7);white-space:nowrap'>"
                  "🔴 %d &nbsp; 🟡 %d</div></div>"
                  % (col, col, txt, e(str(s.get("expect") or "?")),
                     e(str(s.get("health_ts") or "?")),
                     e(str(s.get("ts") or "?")),
                     e(detail_txt),
                     s.get("n_red", 0), s.get("n_yellow", 0)))

        # ---- 仪1 数据顶板：15 表新鲜度 + 六服务灯（🔍 穿透到数据行）----
        rows = ["<table style='%s'><tr>" % _TB
                + "".join("<th style='%s;background:var(--gray-3)'>%s</th>"
                          % (_TD, x)
                          for x in ("表", "市场", "最新", "状态"))
                + "</tr>"]
        if warming:
            rows.append("<tr><td style='%s;color:var(--blu-500)' colspan='4'>"
                        "⏳ 时点校对冷算中（约 100s）——完成后本页自动刷新，"
                        "其余四仪已可用</td></tr>" % _TD)
        for r in s.get("freshness", []):
            st = str(r.get("status") or "")
            sc = ("#3fb950" if st == "ok"
                  else ("#f85149" if st == "empty" else "#d29922"))
            tb = str(r.get("table") or "")
            rows.append(
                "<tr><td style='%s'>"
                "<span style='cursor:pointer;margin-right:6px' "
                "title='穿透看数据' onclick=\"tp_toggle('%s')\">🔍</span>"
                "<a href='/bt-data-health' "
                "style='color:var(--blu-500);text-decoration:none'>%s</a></td>"
                "<td style='%s;color:var(--gray-5)'>%s</td>"
                "<td style='%s'>%s</td>"
                "<td style='%s;color:%s'>%s</td></tr>"
                % (_TD, e(tb), e(tb), _TD,
                   e(_TP_MKT.get(tb, str(r.get("group") or ""))), _TD,
                   e(str(r.get("latest") or "—")), _TD, sc, e(st))
                + _tp_row(tb))
        rows.append("</table>")
        svcs = s.get("services") or {}
        lights = " ".join(
            "<a href='/sys-health' style='text-decoration:none;"
            "font-size:12px;color:%s;margin-right:10px'>●%s</a>"
            % ("#3fb950" if v else "#f85149", e(str(k)))
            for k, v in svcs.items()) or "<span style='color:var(--gray-5)'>—</span>"
        dk = s.get("docker") or {}
        _cs = s.get("collectors") or {}
        _csrc = _cs.get("sources") or {}
        _cdots = " ".join(
            "<span style='font-size:11px;color:%s;margin-right:6px'>"
            "●%s%s</span>"
            % ("#3fb950" if v.get("ok") else "#f85149", e(str(k)),
               (" +%s" % v["new"]) if v.get("new") else "")
            for k, v in _csrc.items())
        _cline = (("<div style='margin:4px 0 0;font-size:11px;"
                   "color:var(--gray-5)'>采集 %s%s · 末次 %s · 24h %s 条</div>"
                   % (_cdots,
                      "" if _cs.get("last_ok") else " · 采集异常!",
                      e(str(_cs.get("last_collect") or "?")),
                      e(str(_cs.get("news_24h") or "?"))))
                  if _cs else "")
        m1 = (banner
              + _sec("① 数据顶板 · 核心表新鲜度", "/bt-data-health",
                     "点表名进数据仓；点灯进系统体检")
              + "".join(rows)
              + "<div style='margin:8px 0'>%s"
                "<span style='color:var(--gray-5);font-size:11px;"
                "margin-left:10px'>docker %s 容器%s</span>%s</div>"
              % (lights, dk.get("total", "?"),
                 "" if dk.get("ai_portal") else " · ai_portal!", _cline))

        # ---- 仪2 运行仪表：今日跑批+投委会 ----
        runs = s.get("runs") or []
        m2 = (_sec("② 运行仪表 · 今日运行", "/council", "跑批+四会+扫描计数")
              + ("<div style='color:var(--gray-5);font-size:12px'>今日无运行记录"
                 "</div>" if not runs else
                 "<div style='font-size:12px;line-height:1.9;"
                 "font-family:Consolas,monospace'>%s</div>"
                 % "<br>".join(e(str(x)) for x in runs[:14]))
              + "<div style='color:var(--gray-5);font-size:11px'>"
                "缠论池今日新扫 %d 票</div>" % s.get("chanlun_today", 0))

        # ---- 仪3 预警仪：bad 任务+弱案例+警报池+需注意 ----
        w = s.get("weak") or {}
        al = ["<table style='%s'><tr>" % _TB
              + "".join("<th style='%s;background:var(--gray-3)'>%s</th>"
                        % (_TD, x)
                        for x in ("预警源", "数值", "下钻"))
              + "</tr>"]
        for label, val, href, vcol in (
                ("坏任务", "%d 个" % len(bad), "/sys-health",
                 "#f85149" if bad else "#3fb950"),
                ("弱于板块·待跟进", str(w.get("pending", "?")), "/bt-weak",
                 "#d29922" if w.get("pending") else "#3fb950"),
                ("弱于板块·在案", str(w.get("open", "?")), "/bt-weak",
                 "#8b949e"),
                ("九维警报池(≥75)", str(s.get("nine_alerts", 0)), "/bt-9dim",
                 "#d29922" if s.get("nine_alerts") else "#3fb950"),
                ("盯盘需注意", str(s.get("watch_note", 0)), "/council",
                 "#d29922" if s.get("watch_note") else "#3fb950")):
            al.append("<tr><td style='%s'>%s</td>"
                      "<td style='%s;color:%s;font-weight:600'>%s</td>"
                      "<td style='%s'><a href='%s' style='color:var(--blu-500);"
                      "text-decoration:none'>去处理 →</a></td></tr>"
                      % (_TD, e(label), _TD, vcol, e(str(val)), _TD, href))
        nl = s.get("nine_list") or []
        if nl:
            al.append("<tr><td style='%s' colspan='3'>警报池 top: %s</td></tr>"
                      % (_TD, " ".join(
                          "<a href='/kg?c=%s' style='color:var(--ylw-500);"
                          "text-decoration:none'>%s(%.0f)</a>"
                          % (e(str(x.get("code") or "")),
                             e(str(x.get("name") or x.get("code"))),
                             float(x.get("score") or 0))
                          for x in nl[:8])))
        al.append("</table>")
        m3 = (_sec("③ 预警仪 · 待处理与知晓", "/sys-health",
                   "红=今日必须处理；黄=知晓")
              + "".join(al))

        # ---- 仪4 结构图台账：缠论池近 15 票（图为哪个事件而画）----
        gl = ["<table style='%s'><tr>" % _TB
              + "".join("<th style='%s;background:var(--gray-3)'>%s</th>"
                        % (_TD, x)
                        for x in ("代码", "名称", "信号", "信号日", "发现日"))
              + "</tr>"]
        for g in s.get("graphs", []):
            gl.append("<tr><td style='%s'><a href='/kg?c=%s' "
                      "style='color:var(--blu-500);text-decoration:none'>%s</a></td>"
                      "<td style='%s'>%s</td><td style='%s;color:var(--ylw-500)'>%s</td>"
                      "<td style='%s'>%s</td><td style='%s'>%s</td></tr>"
                      % (_TD, e(str(g.get("code") or "")), e(str(g.get("code") or "")),
                         _TD, e(str(g.get("name") or "")), _TD,
                         e(str(g.get("type") or "")), _TD,
                         e(str(g.get("dt") or "")), _TD,
                         e(str(g.get("detect_date") or ""))))
        gl.append("</table>")
        m4 = (_sec("④ 结构图台账 · 缠论池近 15 票", "/bt-chanlun-pool",
                   "点代码进图谱溯源")
              + "".join(gl))

        # ---- 仪5 账户态势：4 模拟盘 ----
        ac = ["<table style='%s'><tr>" % _TB
              + "".join("<th style='%s;background:var(--gray-3)'>%s</th>"
                        % (_TD, x)
                        for x in ("账户", "持仓", "现金", "净值", "较昨日"))
              + "</tr>"]
        for a in s.get("accounts", []):
            nav = a.get("nav")
            ps = a.get("ps")
            pn = a.get("pnl_pct")
            pc = ("#3fb950" if (pn or 0) >= 0 else "#f85149"
                  if isinstance(pn, (int, float)) else "#8b949e")
            ac.append("<tr><td style='%s'><a href='/bt-report' "
                      "style='color:var(--blu-500);text-decoration:none'>%s %s</a></td>"
                      "<td style='%s'>%d</td><td style='%s'>%.0f</td>"
                      "<td style='%s'>%s</td><td style='%s;color:%s'>%s</td></tr>"
                      % (_TD, e(str(a.get("icon") or "")), e(str(a.get("name") or "")),
                         _TD, int(a.get("open") or 0), _TD,
                         float(a.get("cash") or 0), _TD,
                         e(("%.0f" % nav) if isinstance(nav, (int, float)) else "—"),
                         _TD, pc,
                         e(("%+.2f%%" % ((pn or 0) * 100))
                           if isinstance(pn, (int, float))
                           else (("ps %s" % ps) if ps else "—"))))
        ac.append("</table>")
        m5 = (_sec("⑤ 账户态势 · 4 模拟盘", "/bt-report", "点账户进报告")
              + "".join(ac))

        # ---- 仪6 决策层：四会决策流时间线（点条目展开观点原文）----
        dd = bridge_decisions()
        acts = bridge_actions_index()
        _ROLE = (("bull", "多头"), ("bear", "空头"), ("aggr", "激进"),
                 ("cons", "保守"), ("tech", "技术面"), ("senti", "情绪面"))
        tl = ([("🏛 立案", "#58a6ff", p.get("ts") or "", p)
               for p in dd["props"]]
              + [("👁 盯盘", "#d29922", w.get("ts") or "", w)
                 for w in dd["watches"]]
              + [("🧾 复盘", "#bc8cff", r.get("ts") or "", r)
                 for r in dd["reviews"]])
        tl.sort(key=lambda x: x[1], reverse=True)
        items = []
        for tag, tc, ts, r in tl[:24]:
            code = str(r.get("code") or "")
            nm = str(r.get("name") or code)
            hm = str(ts)[5:16] if ts else "?"
            if tag == "🏛 立案":
                memo = r.get("memo") or {}
                vd = str(memo.get("verdict") or "?")
                vc = {"支持": "#3fb950", "反对": "#f85149"}.get(vd, "#d29922")
                head = ("<b style='color:%s'>%s</b> · 分歧 %s ｜ %s"
                        % (vc, e(vd), e(str(r.get("dissent"))),
                           e(str(memo.get("thesis") or ""))))
                vrows = []
                for rk, rcn in _ROLE:
                    v = (r.get("views") or {}).get(rk) or {}
                    st = v.get("stance") or 0
                    stc = ("#3fb950" if st > 0
                           else "#f85149" if st < 0 else "#8b949e")
                    vrows.append(
                        "<div style='margin:3px 0'><b style='color:%s'>"
                        "%s %+d</b> <span style='color:var(--gray-7)'>%s</span></div>"
                        % (stc, rcn, int(st),
                           e(str(v.get("answer") or "").strip())))
                risks = "、".join(str(x) for x in (memo.get("risks") or []))
                body = ("%s<div style='color:var(--gray-5);font-size:11px;"
                        "margin-top:5px'>风险：%s ｜ 若看错：%s ｜ 信心 %s"
                        "/100 · 主观分歧 %s · 耗时 %.0fs</div>"
                        % ("".join(vrows), e(risks),
                           e(str(memo.get("if_wrong") or "")),
                           e(str(memo.get("conviction"))),
                           e(str(memo.get("dissent_hat"))),
                           float(r.get("elapsed_s") or 0)))
            elif tag == "👁 盯盘":
                j = r.get("judge") or {}
                lv = str(j.get("level") or "?")
                vc = "#d29922" if lv == "需注意" else "#3fb950"
                head = ("<b style='color:%s'>%s · %s</b> · 浮盈 %+.1f%%"
                        % (vc, e(lv), e(str(j.get("bias") or "")),
                           float(r.get("pnl_pct") or 0)))
                evs = "".join(
                    "<div style='color:var(--gray-5);font-size:11px'>· %s %s</div>"
                    % (e(str(x.get("ts") or ""))[:10],
                       e(str(x.get("text") or "")))
                    for x in (r.get("events") or [])[:3])
                body = "%s<div style='color:var(--gray-7)'>%s</div>" % (
                    evs, e(str(j.get("view") or "")))
            else:
                br = str(r.get("bear_right")
                         or (r.get("close") or {}).get("bear_right") or "?")
                at = str(r.get("attribution")
                         or (r.get("post") or {}).get("attribution") or "?")
                head = "<b style='color:#bc8cff'>空头%s · 归因%s</b>" % (
                    e(br), e(at))
                post = (r.get("post") or {})
                cf = str(r.get("counterfactual")
                         or post.get("counterfactual") or "")
                ls = (r.get("lessons") or post.get("lessons") or [])
                body = ("<div style='color:var(--gray-7)'>%s</div>%s"
                        % (e(cf),
                           "".join("<div style='color:var(--ylw-500);"
                                   "font-size:11px;margin-top:3px'>⚑ %s</div>"
                                   % e(str(x.get("text") or x
                                           if isinstance(x, dict) else x))
                                   for x in ls[:3])))
            a0 = acts.get(r.get("ts") or "")
            abg = ("<b style='color:%s;font-size:11px'>%s</b>"
                   % ({"agree": "#3fb950", "reject": "#f85149"}.get(
                       (a0 or {}).get("action"), "#d29922"),
                      {"agree": "✅已同意", "reject": "⛔已否决"}.get(
                          (a0 or {}).get("action"), "📝有备注"))) if a0 else ""
            items.append(
                "<details style='border-left:3px solid %s;background:var(--gray-2);"
                "padding:5px 10px;margin:4px 0;border-radius:6px'>"
                "<summary style='cursor:pointer'><span style='color:var(--gray-5);"
                "font-size:11px;font-family:Consolas,monospace'>%s</span> %s "
                "<a href='/kg?c=%s' style='color:var(--blu-500);text-decoration:"
                "none'>%s %s</a>%s <span style='font-size:12px;color:var(--gray-7)'>"
                "%s</span></summary><div style='padding:6px 4px 2px;"
                "font-size:12px;line-height:1.7'>%s</div></details>"
                % (tc, hm, tag, e(code), e(code), e(nm), abg, head, body))
        m6 = (_sec("⑥ 决策层 · 四会决策流", "/council",
                   "点条目展开六视角观点原文（立案→盯盘→复盘）")
              + ("".join(items)
                 or "<div style='color:var(--gray-5);font-size:12px'>"
                   "暂无决策记录（跑批后 23:50 自动产生）</div>"))

        # ---- 仪7 影子对照：分歧度×前向收益（转正门槛 n/30）----
        def _fmt_r(v):
            return ("待开市" if not isinstance(v, (int, float))
                    else "%+.1f%%" % (v * 100))
        g = dd["gate"] or {}
        sm = dd["samples"] or []
        bd = dd["by_dissent"] or []
        gs = str(g.get("status") or "样本积累中")
        gc = {"显著·建议转正评估": "#3fb950",
              "样本积累中": "#58a6ff"}.get(gs, "#d29922")
        mx = max([b.get("n") or 0 for b in bd] + [1])
        bars = "".join(
            "<div style='display:flex;align-items:center;gap:6px;"
            "margin:2px 0'><span style='width:56px;color:var(--gray-5);"
            "font-size:11px'>%s</span><div style='height:10px;"
            "background:var(--ylw-500);border-radius:3px;width:%dpx'></div>"
            "<span style='color:var(--gray-7);font-size:11px'>%d 票</span></div>"
            % (e(str(b.get("bucket"))), 6 + int(140 * (b.get("n") or 0) / mx),
               b.get("n") or 0) for b in bd)
        srows = "".join(
            "<tr><td style='%s'><a href='/kg?c=%s' style='color:var(--blu-500);"
            "text-decoration:none'>%s</a></td><td style='%s'>%s</td>"
            "<td style='%s'>%d</td><td style='%s'>%s</td><td style='%s'>%s"
            "</td><td style='%s'>%s</td></tr>"
            % (_TD, e(str(s.get("code") or "")), e(str(s.get("code") or "")),
               _TD, e(str(s.get("name") or "")), _TD,
               int(s.get("dissent") or 0), _TD, e(str(s.get("verdict") or "")),
               _TD, e(_fmt_r(s.get("r5"))), _TD, e(_fmt_r(s.get("r20"))))
            for s in sm[:12])
        pts = [s for s in sm if isinstance(s.get("r20"), (int, float))]
        scatter = ""
        if pts:
            dots = "".join(
                "<a href='/kg?c=%s'><circle cx='%d' cy='%d' r='5' "
                "fill='#58a6ff' opacity='0.85'><title>%s 分歧%d r20 "
                "%+.1f%%</title></circle></a>"
                % (e(str(s.get("code") or "")),
                   30 + int(3.0 * (s.get("dissent") or 0)),
                   150 - int(140 * max(-0.2, min(0.4, s.get("r20") or 0)) / 0.6),
                   e(str(s.get("name") or s.get("code"))),
                   int(s.get("dissent") or 0),
                   float(s.get("r20") or 0) * 100)
                for s in pts[:40])
            scatter = ("<svg width='360' height='165' style='background:"
                       "var(--gray-2);border-radius:8px;margin:6px 0'>"
                       "<line x1='30' y1='150' x2='335' y2='150' "
                       "stroke='#30363d'/><line x1='30' y1='10' x2='30' "
                       "y2='150' stroke='#30363d'/>%s<text x='338' y='162' "
                       "fill='#8b949e' font-size='10'>分歧度</text><text "
                       "x='6' y='16' fill='#8b949e' font-size='10'>r20</text>"
                       "</svg>" % dots)
        m7 = (_sec("⑦ 影子对照 · 分歧度×前向收益", "/kg/shadow",
                   "点票进图谱；开市后前向收益自然累积")
              + "<div style='font-size:12px;margin:4px 0'><b style='color:"
                "%s'>● %s</b> <span style='color:var(--gray-5)'>前向样本 %s/%s · "
                "更新 %s</span></div>" % (gc, e(gs), e(str(g.get("n"))),
                                          e(str(g.get("need"))),
                                          e(dd["shadow_updated"] or "?"))
              + "<table style='%s'><tr>" % _TB
              + "".join("<th style='%s;background:var(--gray-3)'>%s</th>"
                        % (_TD, x)
                        for x in ("票", "名称", "分歧", "结论", "r5", "r20"))
              + "</tr>%s</table>" % (srows
                                     or "<tr><td style='%s' colspan='6'>"
                                        "暂无在册提案</td></tr>" % _TD)
              + scatter + "<div style='margin-top:6px'>%s</div>" % bars)

        # ---- 仪8 指挥台：待确认队列三动作 + 快捷指令（两步确认闸门）----
        from urllib.parse import quote as _uq
        _BTN = ("display:inline-block;padding:3px 10px;margin:2px 3px;"
                "border-radius:12px;font-size:11px;text-decoration:none;"
                "border:1px solid %s;color:%s;background:#161b22")
        pend = [p for p in dd["props"]
                if (p.get("ts") or "") not in acts][:6]
        plines = []
        for p in pend:
            memo = p.get("memo") or {}
            vd = str(memo.get("verdict") or "?")
            vc = {"支持": "#3fb950", "反对": "#f85149"}.get(vd, "#d29922")
            key = _uq(str(p.get("ts") or ""))
            plines.append(
                "<div style='padding:6px 8px;margin:4px 0;background:var(--gray-2);"
                "border-radius:8px;display:flex;justify-content:space-between;"
                "align-items:center;flex-wrap:wrap;gap:6px'>"
                "<div style='font-size:12px'><a href='/kg?c=%s' style='"
                "color:var(--blu-500);text-decoration:none'>%s %s</a> "
                "<b style='color:%s'>%s</b> · 分歧 %s · %s</div>"
                "<div><a href='/bridge/act?cmd=agree:%s' style='%s'>"
                "✅ 同意</a><a href='/bridge/act?cmd=reject:%s' style='%s'>"
                "⛔ 否决</a><a href='/bridge/act?cmd=note:%s' style='%s'>"
                "📝 备注</a></div></div>"
                % (e(str(p.get("code") or "")), e(str(p.get("code") or "")),
                   e(str(p.get("name") or "")), vc, e(vd),
                   e(str(p.get("dissent"))), e(str(p.get("d"))),
                   key, _BTN % ("#3fb950", "#3fb950"),
                   key, _BTN % ("#f85149", "#f85149"),
                   key, _BTN % ("#d29922", "#d29922")))
        quick = "".join(
            "<a href='%s' style='%s'>%s</a>"
            % ((("/bridge/act?cmd=" + _uq(c)) if c else "/api/alert/push"),
               _BTN % ("#30363d", "#58a6ff"), lb)
            for lb, c in (("🔄 同步 tdx", "sync:tdx"),
                          ("🔄 同步 tushare", "sync:tushare"),
                          ("🔄 同步 kg", "sync:kg"),
                          ("🛰 缠论扫描", "scan:chanlun"),
                          ("🛰 弱案例扫描", "scan:weak"),
                          ("🛰 九维扫描", "scan:nine"),
                          ("📣 预警推送", None)))
        m8 = (_sec("⑧ 指挥台 · 待确认与快捷指令", "/council",
                   "两步确认闸门；资金动作走 /screen 既有确认流程")
              + ("".join(plines)
                 or "<div style='color:var(--gray-5);font-size:12px'>"
                   "待确认队列空（近 20 条备忘录均已处理）</div>")
              + "<div style='margin:10px 0 4px;color:var(--gray-5);font-size:11px'>"
                "快捷指令（观察类 · 点后确认执行）</div>" + quick
              + "<div style='margin-top:8px'><a href='/screen' style='"
                "display:inline-block;padding:6px 14px;background:var(--gray-2);"
                "border:1px solid var(--gray-4);border-radius:8px;color:var(--ylw-500);"
                "text-decoration:none;font-size:12px'>💰 资金动作 → 模拟仓"
                "操作台（建仓/平仓/加减仓，二次确认在彼处——舱的闸门纪律）"
                "</a></div>")

        content = ("%s%s%s%s%s%s%s%s%s%s"
                   % (m1, m2, m3, m4, m5, m6, m7, m8,
                      "<div style='color:var(--gray-5);font-size:11px;"
                      "margin:16px 0'>K56 全量交付 · 五仪+决策层+指挥台 · "
                      "数据穿透：点仪① 🔍 就地展开表内数据 · "
                      "API: /api/bridge/summary</div>", _TP_JS))
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                + ("<meta http-equiv='refresh' content='30'>" if warming else "")
                + "<title>驾驶舱 · 老田的AI助手</title></head><body "
                "style='background:var(--gray-0);margin:0;padding:12px 16px'>"
                + self._COCKPIT_CSS + self._KG_CSS
                + pill_nav("bridge")
                + "<div style='color:var(--gray-7);font-size:18px;"
                  "font-weight:700;margin:10px 0 2px'>🚀 投研驾驶舱</div>"
                  "<div style='color:var(--gray-5);font-size:12px;"
                  "margin-bottom:6px'>指挥大舱不是数据屏——数据到目的地、"
                  "预警不断提醒、红黄绿分级</div>"
                + content + "</body></html>")
        self._html(body)

    def _bridge_act_page(self, qs):
        """K56-B3 两步确认页 /bridge/act（零 JS 闸门）：confirm=1 执行
        并 3s 回舱；缺省黄页确认（note 先填备注表单）。资金动作不在
        白名单——这是指挥舱的闸门纪律。"""
        import html as _h
        from urllib.parse import parse_qs as _pqs, quote_plus as _qp
        e = _h.escape
        q = _pqs(qs or "")
        cmd = (q.get("cmd") or [""])[0]
        text = (q.get("text") or [""])[0]
        confirm = (q.get("confirm") or ["0"])[0] == "1"
        desc = bridge_act_desc(cmd, text)
        back = ("<a href='/bridge' style='color:var(--blu-500);"
                "text-decoration:none;font-size:12px'>← 返回驾驶舱</a>")
        if desc is None:
            body = ("<div style='color:var(--red-500);font-size:14px;"
                    "font-weight:700;margin:20px 0 8px'>⛔ 未知指令</div>"
                    "<div style='color:var(--gray-5);font-size:12px'>cmd=%s "
                    "不在指挥台白名单</div>%s"
                    % (e(cmd[:80] or "(空)"), back))
        elif confirm:
            r = bridge_act(cmd, text)
            col = "#3fb950" if r.get("ok") else "#f85149"
            body = ("<div style='color:%s;font-size:14px;font-weight:700;"
                    "margin:20px 0 8px'>%s %s</div>%s"
                    % (col, "✅" if r.get("ok") else "❌",
                       e(str(r.get("msg"))), back))
            head = ("<meta http-equiv='refresh' content='3;url=/bridge'>"
                    if r.get("ok") else "")
            self._html("<!DOCTYPE html><html><head><meta charset='utf-8'>%s"
                       "<title>指挥台 · 执行</title></head><body "
                       "style='background:var(--gray-0);margin:0;padding:12px 16px'>"
                       "%s%s</body></html>"
                       % (head, self._COCKPIT_CSS, body))
            return
        else:
            d, lv = desc
            note_form = ""
            if cmd.startswith("note:"):
                note_form = ("<form action='/bridge/act' method='get' "
                             "style='margin:10px 0'><input type='hidden' "
                             "name='cmd' value='%s'>"
                             "<input name='text' placeholder='备注内容"
                             "（≤200 字，将回流记忆闭环）' value='%s' "
                             "style='width:420px;padding:6px 8px;"
                             "background:var(--gray-0);color:var(--gray-7);"
                             "border:1px solid var(--gray-4);border-radius:6px'> "
                             "<button style='padding:6px 14px;background:"
                             "var(--gray-3);color:var(--blu-500);border:1px solid "
                             "var(--gray-4);border-radius:6px;cursor:pointer'>"
                             "下一步 →</button></form>"
                             % (e(cmd), e(text[:200])))
                go = ("<a href='/bridge/act?cmd=%s&text=%s&confirm=1' "
                      "style='display:inline-block;padding:6px 18px;"
                      "background:var(--gray-3);color:var(--grn-500);border:1px solid "
                      "var(--grn-500);border-radius:6px;text-decoration:none;"
                      "font-weight:600'>✅ 确认执行</a>" % (e(cmd), _qp(text[:200])))
            else:
                go = ("<a href='/bridge/act?cmd=%s&confirm=1' style='"
                      "display:inline-block;padding:6px 18px;background:"
                      "var(--gray-3);color:var(--grn-500);border:1px solid var(--grn-500);"
                      "border-radius:6px;text-decoration:none;font-weight:600'"
                      ">✅ 确认执行</a>" % e(cmd))
            body = ("<div style='color:var(--ylw-500);font-size:14px;"
                    "font-weight:700;margin:20px 0 8px'>⚠ 指挥台 · 执行确认"
                    "</div><div style='white-space:pre-wrap;color:var(--gray-7);"
                    "font-size:13px;line-height:1.8;background:var(--gray-2);"
                    "border:1px solid var(--gray-4);border-radius:8px;"
                    "padding:12px 14px;max-width:560px'>%s</div>%s"
                    "<div style='margin:12px 0'>%s &nbsp; %s</div>"
                    % (e(d), note_form, go, back))
        self._html("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                   "<title>指挥台 · 确认</title></head><body "
                   "style='background:var(--gray-0);margin:0;padding:12px 16px'>"
                   "%s%s</body></html>" % (self._COCKPIT_CSS, body))

    def _council_page(self, qs):
        """K55-C4 投委会留痕页：三张 jsonl 浏览+检索 + 影子统计/转正门槛
        + 记忆使用统计。服务端渲染，只读 reports 不写任何文件。"""
        import html as _h
        from urllib.parse import parse_qs as _pq
        e = _h.escape
        qp = _pq(qs)
        q = ((qp.get("q") or [""])[0] or "").strip().lower()

        def _jl(name):
            out = []
            try:
                with open(os.path.join(REPORTS_DIR, name),
                          encoding="utf-8") as f:
                    for line in f:
                        try:
                            out.append(json.loads(line))
                        except Exception:
                            continue
            except OSError:
                pass
            return out

        props = _jl("council_proposals.jsonl")
        watch = [r for r in _jl("council_watch.jsonl")
                 if r.get("type") == "watch"]
        revs = _jl("council_reviews.jsonl")
        stats = {}
        try:
            with open(os.path.join(REPORTS_DIR, "council_shadow_stats.json"),
                      encoding="utf-8") as f:
                stats = json.load(f)
        except Exception:
            pass

        def hit(r):
            return (not q) or q in str(r.get("code") or "").lower() \
                or q in str(r.get("name") or "").lower()

        def _vcol(v):
            if v == "支持":
                return "<span class='bdg g'>支持</span>"
            if v == "反对":
                return "<span class='bdg r'>反对</span>"
            return "<span class='bdg n'>%s</span>" % e(str(v or "中性"))

        def _pc(x):
            return ("%+.2f%%" % x) if x is not None else "—"

        def _pw(x):
            return ("%.1f%%" % x) if x is not None else "—"

        g, corr = stats.get("gate") or {}, stats.get("corr")
        gs = g.get("status") or "暂无统计（等待投委会运行）"
        gb = "g" if gs.startswith("显著") else \
            "n" if gs.startswith("不显著") else "y"
        cards = ("<div style='display:grid;grid-template-columns:repeat("
                 "auto-fit,minmax(150px,1fr));gap:10px'>"
                 "<div class='sstat'><span class='st'>立案记录</span>"
                 "<span class='sv'>%d</span><span class='su'>去重 %d 票</span></div>"
                 "<div class='sstat'><span class='st'>前向收益样本</span>"
                 "<span class='sv'>%s</span><span class='su'>转正门槛 30 票</span></div>"
                 "<div class='sstat'><span class='st'>分歧度-r20</span>"
                 "<span class='sv'>%s</span><span class='su'>%s</span></div>"
                 "<div class='sstat'><span class='st'>影子状态</span>"
                 "<span class='sv'>%s</span><span class='su'>只留痕不拦截</span></div>"
                 "</div>"
                 % (stats.get("n_prop_total") or 0, stats.get("n_uniq") or 0,
                    "%d/30" % (g.get("n") or 0),
                    ("r=%.2f t=%.1f" % (corr["r"], corr["t"])) if corr else "n/a",
                    ("n=%d（Pearson）" % corr["n"]) if corr else "样本积累中",
                    e(gs).replace("显著·建议转正评估（人工拍板）",
                                  "显著·建议转正评估")))
        method = ("<div class='sec'><h2>转正门槛（口径透明）</h2>"
                  "<div style='font-size:13.5px;line-height:1.8'>"
                  "影子期并行不打扰产线；累计 <b>≥30 票</b>对照「分歧度-前向收益」"
                  "相关性显著（|t|≥2 近似 5%%）才建议转正评估，人工拍板；"
                  "转正后仍只获<b>建仓 skip 建议权</b>，资金动作人工确认铁律不变。"
                  "前向收益 rN = 建仓后第 N 个交易日收盘 / 建仓日收盘 - 1"
                  "（N=5/20，假日停牌自然顺延累积）。n&lt;30 只记录不评判"
                  "（K33 影子纪律）。状态：<span class='bdg %s'>%s</span></div>"
                  "</div>" % (gb, e(gs)))
        bd_rows = "".join(
            "<tr><td>%s</td><td>%d</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (x["bucket"], x["n"], _pw(x.get("win5")),
               _pc(x.get("r5")), _pc(x.get("r20")))
            for x in (stats.get("by_dissent") or []))
        tbl_d = ("<div class='sec'><h2>分歧度分桶 × 前向收益</h2>"
                 "<table class='sticky'><tr><th>分歧度</th><th>n</th>"
                 "<th>r5 胜率</th><th>r5 均值</th><th>r20 均值</th></tr>%s"
                 "</table></div>" % bd_rows)
        bv_rows = "".join(
            "<tr><td>%s</td><td>%d</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (_vcol(x["verdict"]), x["n"], _pw(x.get("win5")),
               _pc(x.get("r5")), _pc(x.get("r20")))
            for x in (stats.get("by_verdict") or []))
        tbl_v = ("<div class='sec'><h2>备忘录结论 × 前向收益</h2>"
                 "<table class='sticky'><tr><th>结论</th><th>n</th>"
                 "<th>r5 胜率</th><th>r5 均值</th><th>r20 均值</th></tr>%s"
                 "</table></div>" % bv_rows)
        mem = stats.get("mem") or {}
        cited = "".join("<li>%s <span class='bdg y'>×%d</span></li>" % (e(t), n)
                        for t, n in (mem.get("cited_top") or [])) or \
            ("<li class='dim'>尚无引用——复盘会产出经验后自动检索注入立案会"
             "（月度按「被引用×事后有效」淘汰）</li>")
        mem_sec = ("<div class='sec'><h2>记忆池（%d 条经验 · 检索注入立案会）</h2>"
                   "<ul style='margin:4px 0 0 18px;line-height:2'>%s</ul></div>"
                   % (mem.get("pool") or 0, cited))
        samp = {"%s|%s|%s" % (s.get("sid"), s.get("code"), s.get("d")): s
                for s in (stats.get("samples") or [])}

        def _tk(code, name):
            tc = str(code or "")
            if tc.endswith((".SH", ".SZ", ".BJ")):
                return "<a href='/kg?c=%s'>%s %s</a>" % (e(tc), e(tc),
                                                          e(name or ""))
            return "%s %s" % (e(tc), e(name or ""))

        def _dcol(p):   # K62 影子管理指令 + 风格桶徽章
            dr = p.get("directive") or {}
            act, sty = dr.get("act") or "", p.get("style") or ""
            if not act and not sty:
                return "<span class='dim'>—</span>"
            cls = "r" if act in ("平仓预警", "减仓预警") else \
                "y" if act == "收紧盯盘" else "g"
            s = ("<span class='bdg %s'>%s</span>" % (cls, e(act))) if act else ""
            if sty:
                s += " <span class='bdg n'>%s桶</span>" % e(sty)
            return s

        def _kcol(p):   # K67 票/系数列：投票结论 + 仓位系数（放/缩着色）
            vt = p.get("vote") or {}
            k = ((p.get("directive") or {}).get("sizing") or {}).get("k")
            v = vt.get("verdict")
            if v is None and k is None:
                return "<span class='dim'>—</span>"
            kk = k if k is not None else 1.0
            cls = "up" if kk > 1.0 else ("dn" if kk < 1.0 else "")
            return "%s<span class='%s'>×%.1f</span>" % (
                ("票%s " % e(v)) if v else "", cls, kk)

        fp = [x for x in props if hit(x)][::-1][:50]
        rp = "".join(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td>%s</td><td>%s</td><td class='%s'>%s</td>"
            "<td class='%s'>%s</td>"
            "<td>%s</td><td>%s</td></tr>"
            % (p.get("d"), _tk(p.get("code"), p.get("name")),
               e(p.get("sid") or ""),
               p.get("dissent") if p.get("dissent") is not None else "—",
               _vcol((p.get("memo") or {}).get("verdict")),
               _kcol(p),
               _dcol(p),
               e(str((p.get("memo") or {}).get("conviction") or "—")),
               "up" if ((samp.get("%s|%s|%s" % (p.get("sid"), p.get("code"),
                                                p.get("d"))) or {}
                         ).get("r5") or 0) >= 0 else "dn",
               _pc((samp.get("%s|%s|%s" % (p.get("sid"), p.get("code"),
                                           p.get("d"))) or {}).get("r5")),
               "up" if ((samp.get("%s|%s|%s" % (p.get("sid"), p.get("code"),
                                                p.get("d"))) or {}
                         ).get("r20") or 0) >= 0 else "dn",
               _pc((samp.get("%s|%s|%s" % (p.get("sid"), p.get("code"),
                                           p.get("d"))) or {}).get("r20")),
               e(str((p.get("memo") or {}).get("thesis") or "")[:70]),
               e(str(((p.get("views") or {}).get("bear") or {})
                     .get("answer") or "")[:70]))
            for p in fp) or \
            "<tr><td colspan=12 class='dim'>暂无立案留痕</td></tr>"
        tbl_p = ("<div class='sec'><h2>🏛 立案备忘录（%d 条 · 新→旧）</h2>"
                 "<table class='sticky'><tr><th>日期</th><th>标的</th>"
                 "<th>账户</th><th>分歧</th><th>结论</th><th>票/系数</th>"
                 "<th>指令/桶</th>"
                 "<th>信心</th>"
                 "<th>r5</th><th>r20</th><th>核心逻辑</th><th>空头要点</th>"
                 "</tr>%s</table></div>" % (len([x for x in props if hit(x)]),
                                            rp))
        fw = [x for x in watch if hit(x)][::-1][:50]
        rw = "".join(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td></tr>"
            % (w.get("d"), _tk(w.get("code"), w.get("name")),
               "<span class='bdg n'>%s</span>" % e(w.get("style") or "稳健"),
               "<span class='bdg r'>需注意</span>"
               if (w.get("judge") or {}).get("level") == "需注意"
               else "<span class='bdg n'>无影响</span>",
               e(str((w.get("judge") or {}).get("bias") or "?")),
               e(str((w.get("judge") or {}).get("view") or "")[:90]))
            for w in fw) or \
            "<tr><td colspan=6 class='dim'>暂无盯盘留痕</td></tr>"
        tbl_w = ("<div class='sec'><h2>👁 盯盘留痕（%d 条 LLM 解读 · 新→旧）</h2>"
                 "<table class='sticky'><tr><th>日期</th><th>标的</th>"
                 "<th>桶</th><th>级别</th><th>偏性</th><th>解读</th></tr>%s"
                 "</table></div>"
                 % (len([x for x in watch if hit(x)]), rw))
        fr = [x for x in revs if hit(x)][::-1][:50]
        rr = "".join(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td class='%s'>%s</td>"
            "<td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (r.get("d"), _tk(r.get("code"), r.get("name")),
               e(r.get("sid") or ""),
               "up" if (r.get("ret_pct") or 0) >= 0 else "dn",
               _pc(r.get("ret_pct")),
               "<span class='bdg r'>对</span>"
               if r.get("bear_right") == "对" else
               "<span class='bdg g'>错</span>"
               if r.get("bear_right") == "错" else
               "<span class='bdg n'>%s</span>" % e(str(r.get("bear_right")
                                                       or "—")),
               e(str(r.get("attribution") or "—")),
               e(str(r.get("retrospect") or "")[:70]),
               e(str(((r.get("lessons") or [{}])[0].get("text")
                      or ""))[:70] if r.get("lessons") else "—"))
            for r in fr) or \
            "<tr><td colspan=8 class='dim'>暂无平仓复盘留痕</td></tr>"
        tbl_r = ("<div class='sec'><h2>🧾 平仓复盘（%d 条 · 新→旧）</h2>"
                 "<table class='sticky'><tr><th>平仓日</th><th>标的</th>"
                 "<th>账户</th><th>盈亏</th><th>空头</th><th>归因</th>"
                 "<th>回看</th><th>首条经验</th></tr>%s</table></div>"
                 % (len([x for x in revs if hit(x)]), rr))
        form = ("<form method='get' action='/council' "
                "style='display:flex;gap:6px;margin:4px 0 14px;flex-wrap:wrap;"
                "align-items:center'>"
                "<input name='q' value='%s' "
                "placeholder='按代码/名称检索三张留痕表' "
                "style='padding:7px 10px;border:1px solid var(--gray-4);"
                "border-radius:8px;background:var(--gray-0);color:var(--gray-7);"
                "font-size:13px;min-width:260px'>"
                "<button style='%s;cursor:pointer'>检索</button>%s</form>"
                % (e(q), _PILL,
                   (" <a href='/council' style='%s'>清除</a>" % _PILL)
                   if q else ""))
        # K64d 半自动执行链：待确认预警队列（人工确认→执行，忽略→出队）
        pend = _council_pending()

        def _prow(it):
            def _b(op, txt):
                return ("<button onclick=\"_cfn('%s','%s','%s',this)\" "
                        "style='%s;cursor:pointer'>%s</button>"
                        % (e(it.get("sid") or ""), e(it.get("code") or ""),
                           op, _PILL, txt))
            vt = it.get("vote") or {}
            act = it.get("act") or "?"
            k = it.get("k") or (it.get("sizing") or {}).get("k") or 1.0
            bc = "r" if act in ("平仓预警", "减仓预警") else \
                ("g" if act == "加仓建议" else "y")   # K67 建议票配色
            alab = act + (
                (" %+d%%" % it.get("pct"))
                if act == "加仓建议" and it.get("pct")
                else (" -%d%%" % it.get("pct"))
                if act == "减仓建议" and it.get("pct")
                else (" %d%%" % it.get("pct"))
                if act == "减仓预警" and it.get("pct") else "")
            return ("<tr><td>%s</td><td>%s</td><td>%s</td>"
                    "<td><span class='bdg %s'>%s</span></td><td>%s</td>"
                    "<td>%s</td><td style='white-space:nowrap'>%s %s</td>"
                    "</tr>"
                    % (e(it.get("d") or "—"),
                       _tk(it.get("code"), it.get("name")),
                       e(it.get("sid") or ""), bc, e(alab),
                       ("×%.1f" % k) if k else "—",
                       ("%s %+.1f" % (vt.get("verdict") or "—",
                                      vt.get("wsum") or 0))
                       if vt.get("wsum") is not None else "—",
                       _b("confirm", "✔ 确认执行"),
                       _b("dismiss", "✖ 忽略")))

        if pend:
            sec_p = ("<div class='sec'><h2>⚡ 待确认执行（%d 条 · K64d 预警"
                     " + K67 系数建议）</h2><div class='dim' style='margin:"
                     "0 0 8px;font-size:12.5px'><b>人工确认后才动仓</b>："
                     "平仓预警→全平 · 减仓预警→减半 · 加仓建议（k≥1.1 激进"
                     "同向）→add %+d%% · 减仓建议（k≤0.9 保守同向/偏空）→"
                     "trim 同比，pct=|k-1|×100；忽略=出队不动仓。</div>"
                     "<table class='sticky'><tr><th>预警日</th>"
                     "<th>标的</th><th>账户</th><th>指令</th>"
                     "<th>仓位系数</th><th>投票</th><th>操作</th></tr>%s"
                     "</table></div>"
                     % (len(pend),
                        next((x.get("pct") or 10 for x in pend.values()
                              if x.get("act") == "加仓建议"), 10),
                        "".join(_prow(x) for x in
                                sorted(pend.values(),
                                       key=lambda x: x.get("queued_at")
                                       or ""))))
        else:
            sec_p = ""
        content = ("<h1>🏛 多 Agent 投委会 · 影子留痕</h1>"
                   "<div class='dim' style='margin:2px 0 10px'>K55 六视角"
                   "（多头/空头/激进/保守/技术/情绪）→ 主持人备忘录 · 四会工作流"
                   "（立案/盯盘/平仓/复盘）· 三张表 append-only · LLM 永不碰交易 · "
                   "资金动作人工确认 · 统计更新 %s · "
                   "<a href='/sim' style='color:var(--blu-500)'>♠ 辩论结果交三操盘手"
                   "六本账模拟 →</a></div>%s%s%s%s%s%s%s%s%s%s"
                   % (e(stats.get("updated") or "—"), form, sec_p, cards,
                      method, tbl_d, tbl_v, mem_sec, tbl_p, tbl_w, tbl_r))
        cjs = ("<script>function _cfn(sid,code,op,btn){"
               "if(btn)btn.disabled=true;"
               "var m=op==='confirm'?"
               "'确认执行该预警？（将真实改动模拟盘账本：平仓/减仓）':"
               "'忽略该预警并出队？';"
               "if(!window.confirm(m)){if(btn)btn.disabled=false;return;}"
               "fetch('/api/council/confirm',{method:'POST',"
               "headers:{'Content-Type':'application/json'},"
               "body:JSON.stringify({sid:sid,code:code,op:op})})"
               ".then(function(r){return r.json();})"
               ".then(function(j){if(j.ok){alert('已'+(op==='confirm'?"
               "'执行':'忽略')+'：'+(j.act||''));location.reload();}"
               "else{alert('失败：'+(j.error||''));"
               "if(btn)btn.disabled=false;}})"
               ".catch(function(e){alert('网络错误：'+e);"
               "if(btn)btn.disabled=false;});}</script>")
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>投委会 · 影子留痕</title>"
                + self._COCKPIT_CSS + self._KG_CSS + "</head><body>"
                + pill_nav("council") + content + cjs + "</body></html>")
        self._html(body)

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

    def _debate_page(self, qs):
        """K74 模拟辩论室：五幕辩论回放 + 老板插话（人类参与轮）。"""
        import html as _h
        from urllib.parse import parse_qs as _pql
        e = _h.escape
        qp = _pql(qs or "")
        SIMJ = os.path.join(REPORTS_DIR, "council_sim_proposals.jsonl")
        props = []
        try:
            with open(SIMJ, encoding="utf-8") as f:
                props = [json.loads(x) for x in f if x.strip()]
        except Exception:
            pass
        # K91 同日同标的去重：jsonl 是 append-only，同场重跑产生多行
        # ——旧行占据列表卡片并虚高「场数」（600663 一日 4 行 → 显示
        # 4 张卡，点旧卡看不到插话反馈）。按 (code,d) 取最新行用于
        # 列表/详情显示；K96 统计口径改用全量行（插话跨重跑累计，
        # 重跑不吞掉旧场插话），历史行留在文件里不删
        allp = props
        _seen = {}
        for x in props:
            _seen[(x.get("code"), x.get("d"))] = x
        props = list(_seen.values())
        rev = props[::-1]
        SID_LABEL = {"qpzm": "青萍之末", "9dim": "九维评分",
                     "sig4": "四策略信号", "chanlun": "缠论结构",
                     "manual": "手动"}
        pv = (qp.get("p") or [""])[0]
        if pv.isdigit() and int(pv) < len(rev):
            # ── 单场：五幕时间线 + 插话框 ──
            idx = int(pv)
            r = rev[idx]
            flow = _debate_flow(r)
            hs = r.get("human") or []
            tvs = r.get("traders") or []

            def _stance_chart(rr):
                """K76 立场演化轨迹图：质询→再表态→插话N后，
                三操盘手实线 + 终裁虚线，变化点白圈高亮+增量标注。"""
                if not tvs:
                    return ""
                cols = ["质询", "再表态"] + \
                    ["插话%d后" % (i + 1) for i in range(len(hs))]
                ncol = len(cols)
                W, H = max(340, 90 + ncol * 96), 250
                L, Rm, T, B = 64, 26, 18, 46
                pw, ph = W - L - Rm, H - T - B

                def _y(v):
                    return T + ph * (2 - v) / 4.0

                def _x(i):
                    return L + pw * i / float(max(1, ncol - 1))

                # K79 最新插话轮即时反馈：DEBATE_RECENT_SEC（默认 10 分钟）
                # 内的插话触发结论横幅；最新列永远亮框突出（含脉冲光圈），
                # 与旧轮次拉开视觉差
                last_col = ncol - 1 if hs else -1
                recent = False
                if hs:
                    try:
                        recent = (time.time() - time.mktime(
                            time.strptime(hs[-1].get("ts") or "",
                                          "%Y-%m-%d %H:%M:%S"))) \
                            < DEBATE_RECENT_SEC
                    except Exception:
                        recent = False

                gl = "".join(
                    "<line x1='%d' y1='%.1f' x2='%d' y2='%.1f' "
                    "stroke='#30363d'%s/><text x='%d' y='%.1f' "
                    "fill='#8b949e' font-size='10' text-anchor='end'>"
                    "%+d</text>"
                    % (L, _y(v), W - Rm, _y(v),
                       " stroke-dasharray='4 3'" if v == 0 else "",
                       L - 6, _y(v) + 3, v)
                    for v in (2, 1, 0, -1, -2))
                cl = "".join(
                    "<text x='%.1f' y='%d' fill='%s' font-size='%s' "
                    "text-anchor='middle'%s%s>%s%s</text>"
                    % (_x(i), H - 26,
                       "#d2a8ff" if i == last_col else
                       ("#58a6ff" if i >= 2 else "#8b949e"),
                       "12.5" if i == last_col else "11",
                       " font-weight='600'" if i >= 2 else "",
                       " style='cursor:help'" if i >= 2 else "",
                       e(c + "·最新") if i == last_col else e(c),
                       ("<title>老板：%s</title>"
                        % e((hs[i - 2].get("text") or "")[:120]))
                       if i >= 2 and i - 2 < len(hs) else "")
                    for i, c in enumerate(cols))
                # K78 插话列竖向高亮带：默认可见的「此列为插话后重测」标记
                # K79 最新列加亮（.16）+ 紫虚线亮框，与旧轮次拉开视觉差
                bw = min(38.0, pw / max(1, ncol - 1) * 0.42)
                band = "".join(
                    "<rect x='%.1f' y='%d' width='%.1f' height='%d' "
                    "fill='%s'/>%s"
                    "<text x='%.1f' y='%d' font-size='11' "
                    "text-anchor='middle' style='cursor:help' "
                    "fill='#c58af9'>💬<title>老板插话后立场重测列"
                    "</title></text>"
                    % (_x(i) - bw, T, bw * 2, ph,
                       "rgba(197,138,249,.16)" if i == last_col
                       else "rgba(197,138,249,.08)",
                       ("<rect x='%.1f' y='%d' width='%.1f' height='%d' "
                        "fill='none' stroke='#c58af9' stroke-width='1' "
                        "stroke-dasharray='4 3' rx='5'/>"
                        % (_x(i) - bw, T, bw * 2, ph))
                       if i == last_col else "",
                       _x(i), T + 12)
                    for i in range(2, ncol))
                COLS = {"激进": "#f28b82", "保守": "#8ab4f8",
                        "中立": "#fdd663"}
                lines, dots, rings = [], [], []
                for v in tvs:
                    nm = v.get("name") or "?"
                    color = COLS.get(nm[:2], "#8b949e")
                    s0 = v.get("stance") or 0
                    s2 = v.get("stance2")
                    pts = [(s0, True),
                           (s2 if s2 is not None else s0, s2 is not None)]
                    for h in hs:
                        st3 = None
                        for rp in h.get("replies") or []:
                            if rp.get("who") == nm:
                                st3 = rp.get("stance3")
                        pts.append((st3 if st3 is not None
                                    else pts[-1][0], st3 is not None))
                    lines.append(
                        "<polyline points='%s' fill='none' stroke='%s' "
                        "stroke-width='2'/>"
                        % (" ".join("%.1f,%.1f" % (_x(i), _y(p[0]))
                                    for i, p in enumerate(pts)), color))
                    for i, p in enumerate(pts):
                        val, tested = p
                        prev = pts[i - 1][0] if i else val
                        moved = i > 0 and val != prev
                        if not tested:
                            dots.append(
                                "<circle cx='%.1f' cy='%.1f' r='3.5' "
                                "fill='#161b22' stroke='#8b949e' "
                                "stroke-width='1.2' style='cursor:help'>"
                                "<title>%s · %s："
                                "该轮未重测</title></circle>"
                                % (_x(i), _y(val), e(nm), e(cols[i])))
                            continue
                        tip = "%s · %s：%+d" % (nm, cols[i], val)
                        if moved:
                            src = "老板插话" if i >= 2 else "分析师回应"
                            tip += "（%s %+d→%+d）" % (src, prev, val)
                            dots.append(
                                "<text x='%.1f' y='%.1f' fill='%s' "
                                "font-size='9'>%+d</text>"
                                % (_x(i) + 6, _y(val) - 5, color,
                                   val - prev))
                            if i >= 2:
                                dots.append(
                                    "<text x='%.1f' y='%.1f' fill='#c58af9' "
                                    "font-size='8'>←插话</text>"
                                    % (_x(i) + 6, _y(val) + 12))
                        dots.append(
                            "<circle cx='%.1f' cy='%.1f' r='%d' fill='%s'"
                            "%s style='cursor:help'><title>%s</title>"
                            "</circle>"
                            % (_x(i), _y(val), 5 if moved else 3.5, color,
                               " stroke='#fff' stroke-width='1.5'"
                               if moved else "", e(tip)))
                        if i == last_col and tested:
                            rings.append(
                                "<circle cx='%.1f' cy='%.1f' r='9' fill="
                                "'none' stroke='#c58af9' stroke-width='1.5' "
                                "style='animation:k79p 1.8s ease-in-out "
                                "infinite'><title>最新插话后重测点"
                                "</title></circle>" % (_x(i), _y(val)))
                        if moved and i >= 2:  # K81 因果连线：💬→变化点
                            dots.append(
                                "<path d='M%.1f,%d Q%.1f,%.1f %.1f,%.1f' "
                                "fill='none' stroke='#c58af9' "
                                "stroke-width='1' stroke-dasharray='2 2' "
                                "opacity='.7' style='cursor:help'>"
                                "<title>老板这句话改变了%s的立场"
                                "（%+d→%+d）</title></path>"
                                "<polygon points='%.1f,%.1f %.1f,%.1f "
                                "%.1f,%.1f' fill='#c58af9' opacity='.7'/>"
                                % (_x(i), T + 20, _x(i) + 24,
                                   (T + 20 + _y(val)) / 2.0,
                                   _x(i) + 7, _y(val) - 10,
                                   e(nm), prev, val,
                                   _x(i) + 7, _y(val) - 2,
                                   _x(i) + 3.5, _y(val) - 10,
                                   _x(i) + 10.5, _y(val) - 10))
                fin = rr.get("final") or {}
                fpts = ([(fin.get("stance"), True)]
                        if fin.get("stance") is not None else [])
                # K79 修复 K76 列错位：每轮插话强制占一个槽位（无 final2
                # =该轮未重审，平移空心点），终裁虚线与列严格对齐
                for h in hs:
                    f2 = h.get("final2") or {}
                    if f2.get("stance") is not None:
                        fpts.append((f2.get("stance"), True))
                    else:
                        fpts.append((fpts[-1][0] if fpts else 0, False))
                if fpts:
                    lines.append(
                        "<polyline points='%s' fill='none' stroke='#d2a8ff' "
                        "stroke-width='2' stroke-dasharray='6 3'/>"
                        % (" ".join("%.1f,%.1f" % (_x(1 + i), _y(p[0]))
                                    for i, p in enumerate(fpts))))
                    for i, p in enumerate(fpts):
                        val, tested = p
                        prev = fpts[i - 1][0] if i else val
                        moved = i > 0 and val != prev
                        tip = "终裁 · %s：%+d" % (cols[1 + i], val)
                        if moved:
                            tip += "（老板插话 %+d→%+d）" % (prev, val)
                        dots.append(
                            "<circle cx='%.1f' cy='%.1f' r='%d' fill='%s' "
                            "stroke='%s'%s style='cursor:help'>"
                            "<title>%s</title></circle>"
                            % (_x(1 + i), _y(val), 5 if moved else 3.5,
                               "#d2a8ff" if tested else "#161b22",
                               "#fff" if tested else "#8b949e",
                               " stroke-width='1.5'" if moved else "",
                               e(tip)))
                        if tested and 1 + i == last_col:
                            rings.append(
                                "<circle cx='%.1f' cy='%.1f' r='9' fill="
                                "'none' stroke='#d2a8ff' stroke-width='1.5' "
                                "style='animation:k79p 1.8s ease-in-out "
                                "infinite'><title>终裁 · 最新插话后重审"
                                "</title></circle>"
                                % (_x(1 + i), _y(val)))
                        if moved and i >= 1:  # K81 终裁改判因果连线
                            dots.append(
                                "<path d='M%.1f,%d Q%.1f,%.1f %.1f,%.1f' "
                                "fill='none' stroke='#d2a8ff' "
                                "stroke-width='1' stroke-dasharray='2 2' "
                                "opacity='.7' style='cursor:help'>"
                                "<title>老板这句话改变了终裁立场"
                                "（%+d→%+d）</title></path>"
                                "<polygon points='%.1f,%.1f %.1f,%.1f "
                                "%.1f,%.1f' fill='#d2a8ff' opacity='.7'/>"
                                % (_x(1 + i), T + 20, _x(1 + i) - 24,
                                   (T + 20 + _y(val)) / 2.0,
                                   _x(1 + i) - 7, _y(val) - 10,
                                   prev, val,
                                   _x(1 + i) - 7, _y(val) - 2,
                                   _x(1 + i) - 10.5, _y(val) - 10,
                                   _x(1 + i) - 3.5, _y(val) - 10))
                legend = "".join(
                    "<span style='color:%s;font-size:11.5px;"
                    "margin-right:12px'>● %s</span>" % (c, n)
                    for n, c in list(COLS.items())
                    + [("终裁", "#d2a8ff")]) + \
                    "<span style='color:var(--gray-5);font-size:11.5px;" \
                    "margin-left:4px'>○ 未重测</span>"
                # K78 老板插话原文引文：默认可见，不依赖悬停
                quotes = "".join(
                    "<div style='color:var(--gray-5);font-size:11.5px;"
                    "margin-top:3px'>💬 插话%d（%s）「%s」</div>"
                    % (i + 1, (h.get("ts") or "")[5:16],
                       e((h.get("text") or "")[:60]))
                    for i, h in enumerate(hs))
                # K80 数值矩阵：重测结果数字直读（变化格紫粗、未测○、
                # 最新列浅紫底）——不用目测 Y 轴也能读出每轮重测值
                mth = ("padding:2px 9px;font-weight:600;color:#8b949e;"
                       "font-size:11px;text-align:center")
                mtd = ("padding:2px 9px;text-align:center;"
                       "font-size:12px;color:#c9d1d9")

                def _mtd(cell, j):
                    return ("<td style='%s%s'>%s</td>"
                            % (mtd,
                               ";background:rgba(197,138,249,.10)"
                               if j == last_col else "", cell))

                mrows = []
                for v in tvs:
                    nm2 = (v.get("name") or "")[:2]
                    cur = (v.get("stance2") if v.get("stance2") is not None
                           else (v.get("stance") or 0))
                    cells = ["%+d" % (v.get("stance") or 0), "%+d" % cur]
                    for h in hs:
                        st3 = None
                        for rp in h.get("replies") or []:
                            if rp.get("who") == v.get("name"):
                                st3 = rp.get("stance3")
                        if st3 is None:
                            cells.append("<span style='color:var(--gray-5)'>○"
                                         "</span>")
                        else:
                            cells.append(
                                ("<b style='color:#c58af9'>%+d</b>" % st3)
                                if st3 != cur else "%+d" % st3)
                            cur = st3
                    mrows.append(
                        "<tr><td style='%s;font-weight:600'>%s</td>%s</tr>"
                        % (mtd, e(nm2),
                           "".join(_mtd(c, j)
                                   for j, c in enumerate(cells))))
                pf = fin.get("stance")
                fcells = ["<span style='color:var(--gray-5)'>—</span>",
                          ("%+d" % pf) if pf is not None
                          else "<span style='color:var(--gray-5)'>—</span>"]
                for h in hs:
                    f2 = h.get("final2") or {}
                    fs = f2.get("stance")
                    if fs is None:
                        fcells.append("<span style='color:var(--gray-5)'>○"
                                      "</span>")
                    else:
                        fcells.append(
                            ("<b style='color:#d2a8ff'>%+d</b>" % fs)
                            if fs != pf else "%+d" % fs)
                        pf = fs
                matrix = ("<div style='margin-top:10px;overflow-x:auto'>"
                          "<table style='border-collapse:collapse;"
                          "border:1px solid var(--gray-4)'>"
                          "<tr><th style='%s'></th>%s</tr>%s"
                          "<tr><td style='%s;color:#d2a8ff;font-weight:600'"
                          ">终裁</td>%s</tr></table></div>"
                          % (mth,
                             "".join("<th style='%s'>%s</th>"
                                     % (mth, e(c)) for c in cols),
                             "".join(mrows), mtd,
                             "".join(_mtd(c, j)
                                     for j, c in enumerate(fcells))))
                # K79 即时反馈横幅：插话 10 分钟内，图顶直接给重测结论
                banner = ""
                if hs and recent:
                    pv = {v.get("name"):
                          (v.get("stance2") if v.get("stance2") is not None
                           else (v.get("stance") or 0)) for v in tvs}
                    for h in hs[:-1]:
                        for rp in h.get("replies") or []:
                            nm2 = rp.get("who") or ""
                            if "操盘手" in nm2 and \
                                    rp.get("stance3") is not None:
                                pv[nm2] = rp.get("stance3")
                    parts = []
                    for v in tvs:
                        nm2 = v.get("name")
                        st3 = None
                        for rp in hs[-1].get("replies") or []:
                            if rp.get("who") == nm2:
                                st3 = rp.get("stance3")
                        if st3 is None:
                            parts.append("%s 未重测" % nm2[:2])
                        elif st3 != pv.get(nm2):
                            parts.append(
                                "<b style='color:#c58af9'>%s %+d→%+d</b>"
                                % (nm2[:2], pv.get(nm2) or 0, st3))
                        else:
                            parts.append("%s 维持%+d" % (nm2[:2], st3))
                    f2v = ((hs[-1].get("final2") or {}).get("verdict")
                           or "—")
                    banner = ("<div style='margin:8px 0;padding:8px 12px;"
                              "background:rgba(197,138,249,.10);border:"
                              "1px solid #c58af9;border-radius:8px;"
                              "font-size:13px'>🎤 你的插话已重测：%s｜"
                              "终裁 %s</div>"
                              % ("、".join(parts), f2v))
                return ("<div class='sec'><h2>📈 立场演化轨迹"
                        "（插话重测可视化）</h2>%s"
                        "<svg width='%d' height='%d' style='background:"
                        "rgba(255,255,255,.03);border-radius:8px'>"
                        "<style>@keyframes k79p{0%%,100%%"
                        "{stroke-opacity:.15}50%%{stroke-opacity:.9}}"
                        "</style>%s%s%s%s%s%s"
                        "</svg><div style='margin-top:6px'>%s"
                        "<span style='color:var(--gray-5);font-size:11px;"
                        "margin-left:10px'>白圈=立场变化点</span>"
                        "<span style='color:#c58af9;font-size:11px;"
                        "margin-left:10px'>💬列=老板插话后重测</span>"
                        "</div>%s%s</div>"
                        % (banner, W, H, gl, band, cl, "".join(lines),
                           "".join(rings), "".join(dots), legend, quotes,
                           matrix))

            def _say_effect(rr):
                """K76 插话效果摘要：每轮说服了谁（方向）+ 终裁判定链。"""
                if not hs:
                    return ""
                prev = {v.get("name"):
                        (v.get("stance2") if v.get("stance2") is not None
                         else (v.get("stance") or 0)) for v in tvs}
                fin0 = (rr.get("final") or {}).get("verdict")
                last_fin = fin0
                out = []
                for i, h in enumerate(hs):
                    moved = []
                    for rp in h.get("replies") or []:
                        nm = rp.get("who") or ""
                        if "操盘手" not in nm:
                            continue
                        st3 = rp.get("stance3")
                        if st3 is not None and st3 != prev.get(nm):
                            moved.append("%s %+d→%+d" % (nm[:2],
                                                         prev.get(nm) or 0,
                                                         st3))
                            prev[nm] = st3
                    f2 = (h.get("final2") or {}).get("verdict")
                    judge = ("改判 %s→%s" % (last_fin, f2)
                             if f2 and f2 != last_fin
                             else "维持%s" % (f2 or "—"))
                    last_fin = f2 or last_fin
                    out.append("插话%d（%s）：说服 %s｜终裁%s"
                               % (i + 1, (h.get("ts") or "")[5:16],
                                  "、".join(moved) if moved else "无人",
                                  judge))
                return ("<div class='sec'><h2>🎯 插话效果</h2>%s</div>"
                        % "".join("<div style='padding:3px 0;font-size:"
                                   "13px'>%s</div>" % e(s) for s in out))

            LINE = {"host": "#8ab4f8", "trader": "#fdd663", "vote": "#7ee787",
                    "final": "#f28b82", "human": "#c58af9",
                    "final2": "#d2a8ff"}
            NAME = {"host": "🏛", "trader": "⚔", "vote": "🗳", "final": "⚖",
                    "human": "👤", "final2": "⚖✎"}
            cards = "".join(
                "<div style='margin:10px 0;padding:10px 14px;background:"
                "rgba(255,255,255,.04);border-left:3px solid %s;"
                "border-radius:6px'><div style='margin-bottom:2px'>"
                "<b style='font-size:13.5px'>%s %s</b>%s</div>"
                "<div style='white-space:pre-wrap;font-size:13px;"
                "line-height:1.75'>%s</div></div>"
                % (LINE.get(c, "#8b949e"), NAME.get(c, ""), e(who),
                   (" <span class='bdg %s'>%s</span>"
                    % ("g" if t.startswith(("+", "支持", "高"))
                       else ("r" if t.startswith(("-", "反对", "低"))
                             else "n"), e(t))) if t else "",
                   e(txt))
                for who, txt, t, c in flow)
            fin = r.get("final") or {}
            saybox = ("<div class='sec' id='saybox'><h2>🎤 老板插话</h2>"
                      "<div class='dim' style='margin:0 0 8px;font-size:12.5px'>"
                      "以雇主身份加入本场辩论——主持人（分析师团队）与三位"
                      "操盘手将各自回应你（约 10-20 秒）。</div>"
                      "<textarea id='sayta' rows='3' style='width:98%%;"
                      "background:var(--gray-0);color:var(--gray-7);border:1px solid "
                      "var(--gray-4);border-radius:6px;padding:8px;font-size:13px'"
                      " placeholder='例：我认为量能不足，等回踩 10 日线再谈"
                      "进场；或直接质询某位操盘手…'></textarea>"
                      "<div style='margin-top:6px'><button onclick=_say(this) "
                      "style='%s;cursor:pointer'>▶ 发言</button></div></div>"
                      % _PILL)
            sjs = ("<script>var P=%d;function _say(btn){var ta=document."
                   "getElementById('sayta');var t=ta.value.trim();"
                   "if(!t){alert('说点什么');return}btn.disabled=true;"
                   "btn.textContent='辩论中…';"
                   "fetch('/api/debate/say',{method:'POST',headers:{'Content-"
                   "Type':'application/json'},body:JSON.stringify({p:P,text:"
                   "t})}).then(function(r){return r.json()}).then(function(j)"
                   "{if(j.ok){location.reload()}else{alert(j.error||'失败');"
                   "btn.disabled=false;btn.textContent='▶ 发言'}}).catch("
                   "function(){alert('网络错误');btn.disabled=false;"
                   "btn.textContent='▶ 发言'})}</script>" % idx)
            vt = r.get("vote") or {}
            content = ("<h1>🎤 模拟辩论室 · %s %s</h1>"
                       "<div class='dim' style='margin:2px 0 10px'>%s 预演"
                       "（来源 %s）· 分析师票%s（%+.1f）→ 终裁 <b>%s</b>"
                       "　<a href='/sim' style='color:var(--blu-500)'>← 返回锦标赛"
                       "</a></div>%s%s%s%s"
                       % (e(r.get("code") or ""), e(r.get("name") or ""),
                          r.get("d") or "",
                          e(SID_LABEL.get(r.get("sid"), r.get("sid") or "—")),
                          vt.get("verdict"), vt.get("wsum") or 0,
                          e(fin.get("verdict") or "—"),
                          _stance_chart(r), _say_effect(r), cards, saybox))
            body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                    "<meta name='viewport' content='width=device-width,"
                    "initial-scale=1'><title>模拟辩论室 · %s</title>"
                    % e(r.get("code") or "")
                    + self._COCKPIT_CSS + "</head><body>"
                    + pill_nav("debate") + content + sjs + "</body></html>")
            self._html(body)
            return
        # ── 列表页：场次卡片 ──
        def _lchips(x):
            # K81 列表页跟随：插话后 stance3 紫段（与 /sim _srow 同源逻辑）
            hs = x.get("human") or []
            st3map = {}
            for h in hs:
                for rp in h.get("replies") or []:
                    w = rp.get("who") or ""
                    if "操盘手" in w and rp.get("stance3") is not None:
                        st3map[w] = rp.get("stance3")
            out = ""
            for v in (x.get("traders") or []):
                st = v.get("stance") or 0
                st2 = v.get("stance2")
                lab = "%s%+d" % ((v.get("name") or "")[:2], st)
                if st2 is not None and st2 != st:
                    lab += "→%+d" % st2
                seg = ""
                cur = st2 if st2 is not None else st
                st3 = st3map.get(v.get("name") or "")
                if st3 is not None and st3 != cur:
                    seg = "<b style='color:#c58af9'>→%+d</b>" % st3
                    cur = st3
                out += ("<span class='bdg %s'>%s%s</span> "
                        % ("g" if cur > 0
                           else ("r" if cur < 0 else "n"),
                           e(lab), seg))
            return out or "<span class='dim'>—</span>"

        rows = "".join(
            "<tr><td>%s</td><td><a href='/kg?c=%s'>%s %s</a></td><td>%s</td>"
            "<td>%s（w%+.1f）</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (x.get("d"), e(x.get("code") or ""), e(x.get("code") or ""),
               e(x.get("name") or ""),
               e(SID_LABEL.get(x.get("sid"), x.get("sid") or "—")),
               (x.get("vote") or {}).get("verdict"),
               (x.get("vote") or {}).get("wsum") or 0,
               _lchips(x),
               e((x.get("final") or {}).get("verdict") or "—"),
               ("<a href='/debate?p=%d' style='color:var(--blu-500)'>🎤%s "
                "进场辩论</a>"
                % (i, ("×%d" % len(x.get("human") or []))
                   if x.get("human") else "")))
            for i, x in enumerate(rev[:30]))
        if not rows:
            rows = ("<tr><td colspan=7 class='dim'>尚无预演场次——先到 "
                    "<a href='/sim' style='color:var(--blu-500)'>/sim</a> 点「预演"
                    "当日候选票」</td></tr>")
        # ── K85/K96 常驻统计：老板插话 → 终裁改判率 + 操盘手撬动率
        # （全量行累计：插话跨重跑累计；场级数字按 (code,d) 去重）
        # 口径：有效重测轮=该轮有 final2 且原终裁在；改判轮=final2
        # verdict ≠ 原终裁；操盘手撬动人次=重测立场（stance3）偏离其
        # 链式当前立场（初始=stance2，每轮重测后滚动）的人次，
        # 主持人回复 stance3=None 自动排除
        n_r = n_r2 = n_chg = 0
        n_tr3 = n_tr_move = 0
        _hk = set()  # 有插话的场
        _ck = set()  # 被撬动（终裁改判）的场
        for x in allp:
            hs = x.get("human") or []
            if not hs:
                continue
            _hk.add((x.get("code"), x.get("d")))
            fv = (x.get("final") or {}).get("verdict")
            sess_chg = False
            tcur = {t.get("name"): t.get("stance2")
                    for t in x.get("traders") or []}
            for h in hs:
                n_r += 1
                for rp in h.get("replies") or []:
                    s3 = rp.get("stance3")
                    nm = rp.get("who")
                    if s3 is None or nm not in tcur:
                        continue
                    cur = tcur[nm]
                    if cur is not None:
                        n_tr3 += 1
                        if s3 != cur:
                            n_tr_move += 1
                    tcur[nm] = s3
                f2 = (h.get("final2") or {}).get("verdict")
                if not f2 or not fv:
                    continue
                n_r2 += 1
                if f2 != fv:
                    n_chg += 1
                    sess_chg = True
            if sess_chg:
                _ck.add((x.get("code"), x.get("d")))
        n_h = len(_hk)
        n_sess_chg = len(_ck)
        rate = ("%.0f%%" % (100.0 * n_chg / n_r2)) if n_r2 else "—"
        trate = ("%.0f%%" % (100.0 * n_tr_move / n_tr3)) if n_tr3 else "—"
        hstat = ("<div class='dim' style='margin:2px 0 10px'>"
                 "📊 插话统计：%d 场预演 / %d 场有插话 · 插话 %d 轮"
                 "（有效重测 %d）· 终裁改判 "
                 "<b style='color:#c58af9'>%d 轮</b>（撬动 %d 场）· "
                 "改判率 <b style='color:#c58af9'>%s</b> · "
                 "操盘手撬动 <b style='color:#c58af9'>%d/%d 人次</b>"
                 "（<b style='color:#c58af9'>%s</b>）</div>"
                 % (len(props), n_h, n_r, n_r2, n_chg, n_sess_chg,
                    rate, n_tr_move, n_tr3, trate))
        content = ("<h1>🎤 模拟辩论室</h1>"
                   "<div class='dim' style='margin:2px 0 10px'>K74 分析师"
                   "与操盘手的五幕辩论场（预演留痕回放）+ <b>老板插话</b>"
                   "——以雇主身份入场，主持人与三位操盘手实时回应你。</div>"
                   "%s"
                   "<table class='sticky'><tr><th>辩论日</th><th>标的</th>"
                   "<th>来源</th><th>分析师票</th><th>操盘手（含演化）</th>"
                   "<th>终裁</th><th>进场</th></tr>%s</table>"
                   % (hstat, rows))
        body = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,"
                "initial-scale=1'><title>模拟辩论室</title>"
                + self._COCKPIT_CSS + "</head><body>"
                + pill_nav("debate") + content + "</body></html>")
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
