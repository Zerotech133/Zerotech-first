# -*- coding: utf-8 -*-
"""看护驾驶舱入口（/api/monitor/guard/*）——独立文件，portal_api.py 只加早分派。

GET（只读）：
  /api/monitor/guard/summary   四端口探活 + /ready 数据面 + 看门狗日志尾 + 任务快照
  /api/monitor/guard/log?lines=40   看门狗日志尾行（默认 40，上限 200）

数据来源（全部只读，不写任何状态）：
  - 端口探活：socket connect（8091/8095/8097/8099/9899）
  - /ready：HTTP GET，200 → ready 详情；404 → 无该端点；5xx → 数据面故障
  - 看门狗日志：C:\\Temp\\watchdog_9899.log 尾部行（只读）
  - 任务快照：不直接查 schtasks（慢且编码坑多），从日志 mtime + 端口存活推断
"""
import json
import os
import socket
import time
import urllib.request
from urllib.parse import parse_qs

PORTAL_DIR = os.path.dirname(os.path.abspath(__file__))
WD_LOG = r"C:\Temp\watchdog_9899.log"

# (端口, 名称, 描述, ready_path|None)
SERVICES = [
    (8091, "portal-api", "门户 API（本服务）", "/health"),
    (8095, "picks-sched", "picks 定时发布", None),
    (8097, "push-admin", "推送中台", "/ready"),
    (8099, "km-hub", "知识库中枢", None),
    (9899, "cowagent", "CowAgent console", None),
]

# 看门狗日志里的动作行特征（复述判据：只认这几种形态，不猜）
REVIVE_MARK = ("DOWN x2 -> spawn", "REVIVED", "REVIVE_FAIL", "REVIVE_UNCONFIRMED",
               "SPAWN_FAILED", "REVIVE_FAIL")


def _json(handler, obj, status=200):
    try:
        handler._json(obj, status)
    except Exception:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)


def _probe_port(port, timeout=0.8):
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def _probe_ready(port, path, timeout=3.0):
    """200 → (True, body_json|None)；404 → (None, 'no_endpoint')；否则 (False, err)。"""
    if not path:
        return None, "tcp_only"
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return True, json.loads(raw)
            except ValueError:
                return True, None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, "no_endpoint"
        return False, "http_%d" % e.code
    except Exception as e:
        return False, "%s" % type(e).__name__


def _wd_log_tail(lines):
    try:
        with open(WD_LOG, encoding="utf-8", errors="replace") as f:
            all_lines = f.read().splitlines()
    except OSError:
        return None
    return all_lines[-lines:] if len(all_lines) > lines else all_lines


def _stat_age(path):
    try:
        return round(time.time() - os.stat(path).st_mtime, 0)
    except OSError:
        return None


def handle_get(handler, path, qs):
    if path == "/api/monitor/guard/summary":
        q = _qs(qs)
        loglines = 40
        try:
            loglines = max(1, min(int(q.get("loglines", 40) or 40), 200))
        except ValueError:
            pass
        services = []
        for port, name, desc, ready_path in SERVICES:
            alive = _probe_port(port)
            ready_ok, ready_detail = _probe_ready(port, ready_path)
            services.append({
                "port": port, "name": name, "desc": desc,
                "alive": alive, "ready": ready_ok, "ready_detail": ready_detail,
            })
        log_age = _stat_age(WD_LOG)
        return _json(handler, {
            "ok": True,
            "services": services,
            "watchdog_log": {"path": WD_LOG, "mtime_age_s": log_age,
                             "tail": _wd_log_tail(loglines)},
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
    if path == "/api/monitor/guard/log":
        q = _qs(qs)
        try:
            lines = max(1, min(int(q.get("lines", 40) or 40), 200))
        except ValueError:
            lines = 40
        return _json(handler, {
            "ok": True, "path": WD_LOG,
            "mtime_age_s": _stat_age(WD_LOG),
            "tail": _wd_log_tail(lines),
        })
    return _json(handler, {"ok": False, "error": "not_found", "path": path}, 404)


def _qs(qs):
    parsed = parse_qs(qs or "", keep_blank_values=True)
    return {k: v[0] if v else "" for k, v in parsed.items()}
