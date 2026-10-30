# -*- coding: utf-8 -*-
"""8001 策略预警影子服务（alerts_shadow，K-DASH-002 立项恢复实体）。

定位（影子只读，与 RiskGuard 影子同家族）:
    只读投影 8091 /api/alerts 聚合快照 + alert_watch 五路规则元数据，
    对外提供 /alerts/status、/alerts/ui、/api/health 三个只读端点。
    不做任何交易/写库/推送动作；推送链仍在 alert_push/alert_watch。

契约（portal/index.html 消费实证）:
    GET /alerts/status -> {"ok":true,"running":bool,"rule_count":int,
                           "trading_day":bool,...}  4s 超时，CORS 放行
    GET /alerts/ui     -> 极简状态页（门户 yaOpen 打开）
    GET /api/health    -> 8791 聚合器兼容 {"port":8001,"ok":true}

running 语义（不假绿）:
    running = 数据源 8091 /api/alerts 最近一次拉取成功且未超 90s。
    数据源不可达时 running=false 并带 degraded 说明，绝不伪装在线。

启动: python tools/alerts_shadow.py [--port 8001]（日志落 logs/）。
自启: 计划任务 AlertShadowBoot（开机触发）+ 交易时段重试，见 K-DASH-002。
"""
import argparse
import datetime as dt
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import logging
# safe_path=true 时脚本目录不入 sys.path：把本树根加入 path（helper 同目录）
import sys as _sys
from pathlib import Path as _Path
for _p in _Path(__file__).resolve().parents:
    if (_p / "safe_urlopen.py").exists():
        _sys.path.insert(0, str(_p))
        break

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    try:
        from safe_urlopen import safe_urlopen
    except ImportError:  # 作为包/仓库根导入时
        from tools.safe_urlopen import safe_urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)
PORT_DEFAULT = 8001
SOURCE_URL = "http://127.0.0.1:8091/api/alerts"

# 与 news_collector/alert_watch.py RULES 对齐（五路）
RULE_NAMES = ["盘前倾向", "缠论信号", "九维警报池", "弱于板块在案", "共振做多"]

CACHE_TTL = 30.0        # 数据源快照缓存秒数
SOURCE_STALE_S = 90.0   # 超过此时长视为数据源失联 -> running=false

_lock = threading.Lock()
_cache = {"ts": 0.0, "ok": False, "alerts": None, "err": None}


def _fetch_source():
    """拉 8091 /api/alerts，带 30s 缓存；失败落 err 不抛。"""
    now = time.time()
    with _lock:
        if now - _cache["ts"] < CACHE_TTL and _cache["ts"] > 0:
            return dict(_cache)
    entry = {"ts": now, "ok": False, "alerts": None, "err": None}
    try:
        req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "alerts-shadow/1"})
        with safe_urlopen(req, timeout=5) as r:
            entry["alerts"] = json.loads(r.read().decode("utf-8"))
        entry["ok"] = True
    except Exception as e:
        entry["err"] = "%s: %s" % (type(e).__name__, str(e)[:120])
    with _lock:
        # 失败时保留上一次成功快照的时间戳供 stale 判断
        if not entry["ok"] and _cache.get("ok") and now - _cache["ts"] < SOURCE_STALE_S:
            entry["alerts"] = _cache["alerts"]
            entry["stale_reuse"] = True
        _cache.clear()
        _cache.update(entry)
    return dict(entry)


def _is_trading_day(today=None):
    """保守交易日判断：仅排除周末（法定节假日不识别，宁可不亮也不误判）。

    声明：此为粗口径，节假日会误报 trading_day=true，UI 已注明。
    """
    d = today or dt.date.today()
    return d.weekday() < 5


def status_payload():
    """组装 /alerts/status 主文档（只读，无副作用）。"""
    src = _fetch_source()
    fresh = src["ok"] or (src.get("alerts") is not None
                          and time.time() - src["ts"] < SOURCE_STALE_S)
    return {
        "ok": True,
        "service": "alerts_shadow",
        "role": "shadow-readonly",          # 影子只读，非交易引擎
        "running": bool(fresh and src["ok"]),
        "rule_count": len(RULE_NAMES),
        "rules": RULE_NAMES,
        "trading_day": _is_trading_day(),
        "source": {
            "url": SOURCE_URL,
            "reachable": src["ok"],
            "fetched_at": dt.datetime.fromtimestamp(
                src["ts"], dt.timezone(dt.timedelta(hours=8))).isoformat() if src["ts"] else None,
            "error": src["err"],
        },
        "degraded": (not src["ok"]),
        "degraded_note": ("8091 /api/alerts 不可达，影子降级（不假绿）" if not src["ok"] else None),
        "ts": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(),
    }


_UI_HTML = """<!doctype html><html lang="zh"><meta charset="utf-8">
<title>策略预警影子 · 8001</title>
<style>body{font-family:system-ui,sans-serif;max-width:680px;margin:40px auto;padding:0 16px;color:#222}
h1{font-size:20px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:6px 10px;text-align:left;font-size:14px}
.ok{color:#0a7d33}.bad{color:#b3261e}.mono{font-family:Consolas,monospace;font-size:13px}</style>
<h1>策略预警影子服务（alerts_shadow · 8001）</h1>
<p>影子只读投影：8091 /api/alerts 快照 + alert_watch 五路规则元数据。不做交易、不写库、不推送。</p>
<div id="s">加载中…</div>
<script>
fetch('/alerts/status',{cache:'no-store'}).then(r=>r.json()).then(d=>{
  const st=d.running?'<span class="ok">在线</span>':'<span class="bad">离线/降级</span>';
  let rows='<table><tr><th>项</th><th>值</th></tr>'
   +'<tr><td>running</td><td>'+st+'</td></tr>'
   +'<tr><td>rule_count</td><td>'+d.rule_count+'</td></tr>'
   +'<tr><td>规则</td><td class="mono">'+d.rules.join(' · ')+'</td></tr>'
   +'<tr><td>trading_day（仅排周末，节假日不识别）</td><td>'+(d.trading_day?'是':'否')+'</td></tr>'
   +'<tr><td>数据源</td><td class="mono">'+d.source.url+' → '+(d.source.reachable?'可达':'<span class="bad">'+(d.source.error||'不可达')+'</span>')+'</td></tr>'
   +'<tr><td>ts</td><td class="mono">'+d.ts+'</td></tr></table>';
  if(d.degraded) rows+='<p class="bad">'+d.degraded_note+'</p>';
  document.getElementById('s').innerHTML=rows;
}).catch(e=>document.getElementById('s').innerHTML='<p class="bad">status 拉取失败: '+e+'</p>');
</script></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "alerts-shadow/1.0"

    def _send(self, code, body, ctype):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")  # 门户 hostname 自适应跨域（实证契约）
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/alerts/status" or path == "/api/health":
            payload = status_payload()
            if path == "/api/health":  # 8791 聚合器兼容口径
                payload = {"port": PORT_DEFAULT, "name": "quantapi-shadow",
                           "ok": True, "running": payload["running"]}
            self._send(200, json.dumps(payload, ensure_ascii=False),
                       "application/json; charset=utf-8")
        elif path == "/alerts/ui":
            self._send(200, _UI_HTML, "text/html; charset=utf-8")
        else:
            self._send(404, json.dumps({"ok": False, "error": "not found",
                                        "routes": ["/alerts/status", "/alerts/ui", "/api/health"]},
                                       ensure_ascii=False),
                       "application/json; charset=utf-8")

    def log_message(self, fmt, *args):  # 日志落 stdout（由启动方重定向文件）
        sys.stdout.write("%s %s\n" % (dt.datetime.now().isoformat(timespec="seconds"),
                                      fmt % args))
        sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser(description="8001 策略预警影子服务（只读）")
    ap.add_argument("--port", type=int, default=PORT_DEFAULT)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    try:
        srv = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as e:
        # 计划任务每 30 分钟重试拉起：端口已占用=已有实例在跑，正常退出 code 0（幂等）
        if getattr(e, "winerror", None) == 10048 or "Only one usage" in str(e) or e.errno in (48, 98, 10048):
            print("[alerts-shadow] port %d already in use -> instance running, exit 0" % args.port,
                  flush=True)
            return
        raise
    print("[alerts-shadow] serving http://%s:%d (shadow-readonly, source=%s)"
          % (args.host, args.port, SOURCE_URL), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/alerts_shadow.py", exc)
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
