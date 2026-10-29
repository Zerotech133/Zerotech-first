# -*- coding: utf-8 -*-
"""
publish_schedule_server.py — picks 定时发布管理页 + 本地调度器（127.0.0.1:8095）

定位（2026-10-14）：
    团队审计 P0：触发层未闭合——平台自动化每日只 16:40 一次，schtasks 被安全
    黑名单拦。本服务提供「可手动配置的定时触发」：页面增删时间点，调度线程
    到点自动跑一键链（sync+hash+多文件线上比对），滞后即飞书红卡告警。
    deploy 仍是 agent 侧动作：页面滞后时提示「对 agent 说：重新发布 picks」。

设计：
    - 独立进程，零侵入 portal_api（不重启核心服务）；仅绑定 127.0.0.1
    - 调度线程每 15s tick，命中启用的时间点（HH:MM 精确匹配 + 当日去重）
      即在工作线程跑 publish_watchdog.inspect()（含红卡/绿卡/旗标全逻辑）
    - 配置 portal/work/publish_schedule.json（已被 .gitignore 覆盖）
      {"enabled": true, "times": ["16:45", "23:45"], "updated_at": ...}
    - 运行态 portal/work/publish_schedule_state.json（fired 去重 + 最近结果）
    - 页面：浅色单页，5s 轮询 /api/state；立即巡检 / 增删时间 / 启停调度

用法：
    python publish_schedule_server.py           # 前台跑
    python publish_schedule_server.py --bg      # 脱离会话后台驻留（重启机器后需重跑）
    python publish_schedule_server.py --port 8095
退出码：0 正常停止；非 0 启动失败（端口占用等）。
零第三方依赖，仅标准库。
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

WORK = os.path.join(HERE, "work")
CONFIG_PATH = os.path.join(WORK, "publish_schedule.json")
STATE_PATH = os.path.join(WORK, "publish_schedule_state.json")
WLOG_PATH = os.path.join(WORK, "publish_watchdog.log")

DEFAULT_TIMES = ["16:45", "23:45"]
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

_lock = threading.RLock()  # 配置/状态写锁（RLock：save_state 可能在持锁路径内被调用）
_run_lock = threading.Lock()  # 巡检串行化（同一时刻只跑一条链）
_state = {"last_run": None, "running": False, "fired": {}, "last_tick": 0.0}


# ---------- 配置与状态 ----------


def load_config():
    cfg = {
        "enabled": True,
        "times": list(DEFAULT_TIMES),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d.get("times"), list):
            cfg["times"] = sorted({t for t in d["times"] if TIME_RE.match(str(t))})
        cfg["enabled"] = bool(d.get("enabled", True))
    except (OSError, ValueError) as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/publish_schedule_server.py", exc)
    return cfg


def atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(path), prefix=os.path.basename(path) + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def save_config(cfg):
    cfg["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    atomic_write_json(CONFIG_PATH, cfg)


def save_state():
    atomic_write_json(STATE_PATH, _state)


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d.get("fired"), dict):
            _state["fired"] = d["fired"]
        if d.get("last_run"):
            _state["last_run"] = d["last_run"]
    except (OSError, ValueError) as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/publish_schedule_server.py", exc)


# ---------- 巡检执行 ----------


def run_inspection(trigger):
    """跑一键链（复用看门狗全逻辑：告警/旗标/恢复）。串行化，返回退出码。"""
    with _run_lock:
        _state["running"] = True
        t0 = time.time()
        try:
            import publish_watchdog

            rc = publish_watchdog.inspect()
        except Exception as e:  # noqa: BLE001
            rc = -1
            publish_watchdog.log("[scheduler] 巡检异常: %r" % e)
        _state["running"] = False
        _state["last_run"] = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "trigger": trigger,
            "rc": rc,
            "elapsed": round(time.time() - t0, 1),
            "result": {0: "ok", 2: "lag", -1: "error"}.get(rc, "fail(rc=%d)" % rc),
        }
        save_state()
        return rc


def scheduler_loop():
    while True:
        _state["last_tick"] = time.time()  # QA N1：真实心跳，不再硬编码存活
        try:
            cfg = load_config()
            if cfg["enabled"]:
                now = time.localtime()
                hhmm = time.strftime("%H:%M", now)
                day = time.strftime("%Y-%m-%d", now)
                if hhmm in cfg["times"]:
                    key = "%s %s" % (day, hhmm)
                    with _lock:
                        fired = key in _state["fired"]
                    if not fired:
                        with _lock:
                            _state["fired"][key] = {"ts": time.strftime("%H:%M:%S")}
                            if len(_state["fired"]) > 60:  # 只留最近 60 条去重键
                                for k in sorted(_state["fired"])[:-60]:
                                    _state["fired"].pop(k, None)
                            save_state()
                        threading.Thread(
                            target=run_inspection,
                            args=("schedule " + key,),
                            daemon=True,
                        ).start()
        except Exception as e:  # noqa: BLE001
            try:
                import publish_watchdog

                publish_watchdog.log("[scheduler] tick 异常: %r" % e)
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "portal/publish_schedule_server.py", exc)
        time.sleep(15)


def next_fire(cfg):
    """展示用：下一次触发时间（今天剩余的最早时点，否则明天第一个时点）。"""
    if not cfg["enabled"] or not cfg["times"]:
        return None
    now = time.localtime()
    hhmm_now = time.strftime("%H:%M", now)
    later = sorted(t for t in cfg["times"] if t > hhmm_now)
    if later:
        return time.strftime("%Y-%m-%d ", now) + later[0]
    return (
        time.strftime("%Y-%m-%d", time.localtime(time.time() + 86400))
        + " "
        + cfg["times"][0]
    )


# ---------- HTTP ----------


def tail_watchdog_log(n=15):
    try:
        with open(WLOG_PATH, encoding="utf-8", errors="replace") as f:
            return f.readlines()[-n:]
    except OSError:
        return []


def api_state():
    cfg = load_config()
    flag = {}
    try:
        with open(os.path.join(WORK, "DEPLOY_PENDING.json"), encoding="utf-8") as f:
            flag = json.load(f)
    except (OSError, ValueError) as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/publish_schedule_server.py", exc)
    return {
        "config": cfg,
        "next_fire": next_fire(cfg),
        # QA N1：调度线程 30s 内有过 tick 才算存活（tick 周期 15s）
        "scheduler_alive": (time.time() - _state.get("last_tick", 0)) < 30,
        "running": _state["running"],
        "last_run": _state["last_run"],
        "deploy_pending": flag or None,
        "log_tail": [ln.rstrip("\n") for ln in tail_watchdog_log()],
    }


PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>picks 定时发布管理</title>
<style>
:root{--bg:#0d1117;--card:#161b22;--ink:#e6edf3;--dim:#8b949e;--line:#30363d;
--blue:#58a6ff;--green:#3fb950;--red:#f85149;--amber:#d29922}
*{box-sizing:border-box}
body{margin:0;font-family:"Microsoft YaHei",system-ui,sans-serif;background:var(--bg);color:var(--ink)}
.wrap{max-width:860px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:20px;margin:0 0 4px}
.sub{color:var(--dim);font-size:13px;margin-bottom:20px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.card .k{font-size:12px;color:var(--dim);margin-bottom:6px}
.card .v{font-size:15px;font-weight:600;word-break:break-all}
.v.ok{color:var(--green)}.v.bad{color:var(--red)}.v.warn{color:var(--amber)}
.panel{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
.panel h2{font-size:15px;margin:0 0 12px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:normal;font-size:12px}
button{border:1px solid var(--line);background:var(--card);color:var(--ink);
border-radius:8px;padding:6px 14px;font-size:13px;cursor:pointer}
button:hover{border-color:var(--blue);color:var(--blue)}
button.primary{background:var(--blue);border-color:var(--blue);color:#fff}
button.primary:hover{opacity:.9;color:#fff}
button.danger:hover{border-color:var(--red);color:var(--red)}
input[type=text]{border:1px solid var(--line);border-radius:8px;padding:7px 10px;
font-size:14px;width:110px;margin-right:8px}
.row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.log{background:#0f1420;color:#c8d3e0;font-family:Consolas,monospace;font-size:12px;
border-radius:8px;padding:12px;max-height:260px;overflow:auto;white-space:pre-wrap}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:600}
.b-ok{background:#12261e;color:var(--green)}.b-bad{background:#2d1416;color:var(--red)}
.b-off{background:#21262d;color:var(--dim)}
.note{font-size:12px;color:var(--dim);margin-top:8px;line-height:1.7}
.pending{background:#221c0d;border:1px solid #4d3f14;border-radius:8px;padding:10px 12px;
font-size:13px;margin-bottom:12px;line-height:1.8}
.err{color:var(--red);font-size:13px;margin-top:8px;min-height:1em}
</style></head><body><div class="wrap">
<h1>picks 定时发布管理</h1>
<div class="sub">本地调度器 127.0.0.1:8095 · 到点自动跑一键链（快照刷新 → hash → 线上比对），滞后发飞书红卡</div>
<div id="pending"></div>
<div class="cards">
  <div class="card"><div class="k">调度器</div><div class="v" id="sched">-</div></div>
  <div class="card"><div class="k">下次触发</div><div class="v" id="next">-</div></div>
  <div class="card"><div class="k">最近巡检</div><div class="v" id="lastrun">-</div></div>
  <div class="card"><div class="k">线上状态</div><div class="v" id="online">-</div></div>
</div>
<div class="panel">
  <h2>定时计划</h2>
  <div class="row" style="margin-bottom:12px">
    <button class="primary" id="btn-toggle">启停</button>
    <input type="text" id="newtime" placeholder="HH:MM" maxlength="5">
    <button id="btn-add">添加时间</button>
    <button id="btn-run">立即巡检</button>
    <span class="err" id="err"></span>
  </div>
  <table><thead><tr><th>时间点</th><th>操作</th></tr></thead>
  <tbody id="times"></tbody></table>
  <div class="note">到点执行内容 = publish_picks.py --check-online（快照刷新 + 双文件线上比对 + 状态留痕 + 滞后飞书告警）。deploy 由 WorkBuddy agent 侧完成：滞后时对 agent 说「重新发布 picks」。</div>
</div>
<div class="panel">
  <h2>看门狗日志（尾部 15 行）</h2>
  <div class="log" id="log">加载中…</div>
</div>
</div>
<script>
const $=id=>document.getElementById(id);
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
async function act(body){
  $('err').textContent='';
  const r=await fetch('/api/act',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d=await r.json();
  if(!d.ok){$('err').textContent=d.error||'操作失败';}
  load();return d;
}
async function load(){
  try{
    const d=await(await fetch('/api/state')).json();
    $('sched').textContent=d.scheduler_alive?('运行中'+(d.config.enabled?'（已启用）':'（已暂停）')):'未运行';
    $('sched').className='v '+(d.config.enabled?'ok':'warn');
    $('next').textContent=d.next_fire||'—';
    const lr=d.last_run;
    if(lr){$('lastrun').textContent=lr.result+' · '+lr.ts;
      $('lastrun').className='v '+(lr.rc===0?'ok':(lr.rc===2?'bad':'warn'));}
    else $('lastrun').textContent='尚未运行';
    const p=d.deploy_pending;
    $('pending').innerHTML=p?('<div class="pending"><b>线上滞后待发布</b>（'+esc((p.mismatched||[]).join(', '))+'，'+esc(p.ts)+'）。'
      +'处理：对 WorkBuddy agent 说「<b>重新发布 picks</b>」，完成后点「立即巡检」确认。</div>'):'';
    $('online').textContent=p?'滞后待发布':(d.last_run&&d.last_run.rc===0?'一致':'待巡检');
    $('online').className='v '+(p?'bad':(d.last_run&&d.last_run.rc===0?'ok':'warn'));
    $('btn-toggle').textContent=d.config.enabled?'暂停调度':'恢复调度';
    $('times').innerHTML=d.config.times.map(t=>
      '<tr><td><b>'+esc(t)+'</b></td><td><button class="danger" onclick="act({action:\\'remove\\',time:\\''+t+'\\'})">删除</button></td></tr>').join('')
      ||'<tr><td colspan="2" style="color:var(--dim)">无定时（请添加）</td></tr>';
    $('log').textContent=(d.log_tail||[]).join('\\n')||'（暂无日志）';
  }catch(e){$('sched').textContent='无法连接 8095';$('sched').className='v bad';}
}
$('btn-add').onclick=()=>act({action:'add',time:$('newtime').value.trim()});
$('btn-toggle').onclick=()=>act({action:'toggle'});
$('btn-run').onclick=()=>act({action:'run_now'});
$('newtime').addEventListener('keydown',e=>{if(e.key==='Enter')$('btn-add').click()});
load();setInterval(load,5000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 安静模式
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/state":
            with _lock:
                self._json(api_state())
        elif path == "/health":
            self._json({"ok": True, "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/api/act":
            self._json({"error": "not found"}, 404)
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            # QA N2：拒绝负值与超大 body，防连接挂起/内存放大
            if not (0 < n <= 65536):
                self._json({"ok": False, "error": "bad Content-Length"}, 400)
                return
            req = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, OSError):
            self._json({"ok": False, "error": "bad request body"}, 400)
            return
        action = req.get("action")
        with _lock:
            cfg = load_config()
            if action == "add":
                t = str(req.get("time", "")).strip()
                if not TIME_RE.match(t):
                    self._json({"ok": False, "error": "时间格式须为 HH:MM（如 16:45）"})
                    return
                if t not in cfg["times"]:
                    cfg["times"] = sorted(cfg["times"] + [t])
                    save_config(cfg)
                self._json({"ok": True, "config": cfg})
            elif action == "remove":
                t = str(req.get("time", ""))
                cfg["times"] = [x for x in cfg["times"] if x != t]
                save_config(cfg)
                self._json({"ok": True, "config": cfg})
            elif action == "toggle":
                cfg["enabled"] = not cfg["enabled"]
                save_config(cfg)
                self._json({"ok": True, "config": cfg})
            elif action == "run_now":
                if _state["running"]:
                    self._json({"ok": False, "error": "已有巡检在跑，请稍候"})
                    return
                threading.Thread(
                    target=run_inspection, args=("manual",), daemon=True
                ).start()
                self._json({"ok": True, "started": True})
            else:
                self._json({"ok": False, "error": "unknown action"}, 400)


def main():
    parser = argparse.ArgumentParser(description="picks 定时发布管理页 + 调度器")
    parser.add_argument("--port", type=int, default=8095)
    parser.add_argument(
        "--bg",
        action="store_true",
        help="脱离会话后台驻留（DETACHED_PROCESS，日志到 work/schedule_server.out）",
    )
    args = parser.parse_args()

    if args.bg:
        out = open(os.path.join(WORK, "schedule_server.out"), "ab")
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--port", str(args.port)],
            cwd=HERE,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=out,
            creationflags=subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        print(
            "[ok] 已后台驻留：http://127.0.0.1:%d/（日志 work/schedule_server.out）"
            % args.port
        )
        return 0

    load_state()
    os.makedirs(WORK, exist_ok=True)
    cfg = load_config()
    if not os.path.exists(CONFIG_PATH):
        save_config(cfg)
    threading.Thread(target=scheduler_loop, daemon=True).start()
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as e:
        print("[失败] 端口 %d 占用: %s" % (args.port, e), file=sys.stderr)
        return 1
    print("[ok] picks 定时发布管理页：http://127.0.0.1:%d/" % args.port)
    print("[ok] 定时计划：%s（enabled=%s）" % (cfg["times"], cfg["enabled"]))
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
