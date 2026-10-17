# -*- coding: utf-8 -*-
"""AI 工作站 · 推送中台（127.0.0.1:8097）——消息触达底座，商业化入口。

板块定位（2026-10-14 产品化）：独立板块（横切能力，内容供给方挂导航）：
  总览（KPI + 内容源健康 + 底座能力盘点）｜推送管理（本页原生）
  ｜知识库联动（iframe 8099）｜定时发布（iframe 8095）｜商业化·底座（租户/套餐/配额/API Key）

管辖范围（推送内容的执行者仍是既有管道，本平台做 配置+触发+统计）：
  A. 日报类计划任务（时间点可改/立即执行/状态）：
     金融新闻日报 08:30 news_digest.py      每日新闻分析摘要
     AI工作站统一日报 03:00 daily_report.py  统一日报
     AI工作站预警推送晨 07:10 alert_push morning  盘前预警
     AI工作站预警推送 16:05 alert_push eod        盘后预警全量
  B. 订阅推送（quant_backtest/subscribe.py 引擎）：
     subscriptions.json 5 路订阅的 enabled / receivers / 试推（--test force）
     subscribe_state.json 今日推送计数；每日 ≤3 推熔断（引擎内置）
  C. 接收人（receivers.json）增删改——订阅按名字解析，缺省回落 news push_targets
  D. 统计：今日推送汇总（订阅+KM 库+日报任务运行）

安全：仅绑回环；任务名白名单；HH:MM/sub_id 正则；powershell 子进程
（Get/Set/Start-ScheduledTask，已实测 2026-10-14）；原子写；body 上限；RLock。
保活：并入计划任务「AI工作站CowAgent看门狗」（ops/keepalive_9899.py）。
"""
import argparse
import hmac
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
STATION = os.path.dirname(HERE)
WORK = os.path.join(HERE, "work")
SUB_PATH = os.path.join(STATION, "quant_backtest", "subscriptions.json")
RECV_PATH = os.path.join(STATION, "quant_backtest", "receivers.json")
STATE_PATH = os.path.join(STATION, "quant_backtest", "outbox", "subscribe_state.json")
NEWS_CFG = os.path.join(STATION, "news_collector", "news_config.json")
KM_STATS = os.path.join(WORK, "km_stats.jsonl")
SUB_PY = os.path.join(STATION, "quant_backtest", "subscribe.py")
TEST_LOG = os.path.join(WORK, "push_admin_test.log")
BIZ_PATH = os.path.join(WORK, "commercial.json")
SCHEDULE_PATH = os.path.join(WORK, "schedule.json")  # K-PUSH-003 静默时段配置

# 内容源模块（推送中台的内容供给方），总览页做健康探针
SOURCES = [("推送中台（本服务）", 8097), ("知识库联动", 8099), ("定时发布", 8095)]
PLANS = ["internal", "trial", "standard", "pro"]

PORT = 8097
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
SID_RE = re.compile(r"^[A-Za-z0-9_]{1,60}$")
BODY_CAP = 65536

# 可管理时间点/立即执行的任务白名单（只动这几个既有任务，不建不删）
TASK_MANAGEABLE = ["金融新闻日报", "AI工作站统一日报",
                   "AI工作站预警推送晨", "AI工作站预警推送"]
# 只读展示的生态任务
TASK_READONLY = ["TodaysPicks", "自选股行情快照",
                 "AI工作站预警监听", "AI工作站持仓异动监控"]

_lock = threading.RLock()
_run_lock = threading.Lock()
_state = {"last_tick": 0.0, "running": False,
          "last_test": None, "task_cache": (0.0, None)}


# ---------- 基础设施 ----------

def atomic_write_json(path, obj):
    import tempfile
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def log(msg):
    line = "[%s] %s\n" % (datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    try:
        with open(os.path.join(WORK, "push_admin.out"), "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
    print(line, end="", flush=True)


def heartbeat_ok():
    return time.time() - _state["last_tick"] < 45


# ---------- powershell 计划任务层（白名单） ----------

PS_QUERY = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    "$names = %s;"
    "$out = foreach ($n in $names) {"
    " $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue;"
    " if ($t) { $i = Get-ScheduledTaskInfo -TaskName $n;"
    "  [pscustomobject]@{name=$n; state=[string]$t.State;"
    "  at=$t.Triggers[0].StartBoundary; last=[string]$i.LastRunTime;"
    "  result=$i.LastTaskResult} } };"
    "$out | ConvertTo-Json -Compress"
) % ("@('" + "','".join(TASK_MANAGEABLE + TASK_READONLY) + "')")


def ps_run(cmd, timeout=60):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                       capture_output=True, timeout=timeout)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("gbk", "replace")


def tasks_snapshot(force=False):
    """全部管辖任务状态（60s TTL 缓存）。"""
    now = time.time()
    with _lock:
        ts, cached = _state["task_cache"]
        if cached and not force and now - ts < 60:
            return cached
    rc, out, err = ps_run(PS_QUERY)
    rows = {}
    if rc == 0 and out.strip():
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
        for d in data:
            at = d.get("at") or ""
            rows[d["name"]] = {
                "state": d.get("state"),
                "time": at[11:16] if len(at) >= 16 else "",
                "last": (d.get("last") or "")[:16],
                "result": d.get("result"),
            }
    with _lock:
        _state["task_cache"] = (now, rows)
    return rows


def ps_set_task_time(name, hhmm):
    if name not in TASK_MANAGEABLE:
        return False, "任务不在白名单：%s" % name
    if not TIME_RE.match(hhmm):
        return False, "时间格式非法（HH:MM）"
    cmd = ("$t = New-ScheduledTaskTrigger -Daily -At '%s'; "
           "Set-ScheduledTask -TaskName '%s' -Trigger $t | Out-Null" % (hhmm, name))
    rc, _, err = ps_run(cmd, timeout=90)
    with _lock:
        _state["task_cache"] = (0.0, None)
    log("[task] %s -> %s rc=%d" % (name, hhmm, rc))
    return rc == 0, ("" if rc == 0 else err.strip()[:200])


def ps_run_task(name):
    if name not in TASK_MANAGEABLE:
        return False, "任务不在白名单：%s" % name
    cmd = "Start-ScheduledTask -TaskName '%s'" % name
    rc, _, err = ps_run(cmd, timeout=30)
    log("[task-run] %s rc=%d" % (name, rc))
    return rc == 0, ("" if rc == 0 else err.strip()[:200])


# ---------- 订阅 / 接收人 ----------

def load_subs():
    return _read_json(SUB_PATH, {"subscriptions": []}).get("subscriptions", [])


def save_subs(subs):
    atomic_write_json(SUB_PATH, {"subscriptions": subs})


def load_receivers():
    return _read_json(RECV_PATH, {"receivers": []}).get("receivers", [])


def save_receivers(recvs):
    atomic_write_json(RECV_PATH, {"receivers": recvs})


def news_targets():
    return _read_json(NEWS_CFG, {}).get("push_targets", [])


def sub_state():
    return _read_json(STATE_PATH, {})


def load_biz():
    """商业化底座配置（租户/套餐/配额/API Key），原子读，缺省内部自用。"""
    try:
        with open(BIZ_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"tenant": "老田工作室", "plan": "internal",
                "quota_daily": 0, "apikey": "", "note": ""}


def save_biz(biz):
    tmp = BIZ_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(biz, f, ensure_ascii=False, indent=1)
    os.replace(tmp, BIZ_PATH)


def source_health():
    """探各内容源 /health（1.5s 超时，挂了不影响本接口其余字段）。"""
    out = []
    for name, port in SOURCES:
        ok = False
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d/health" % port, timeout=1.5) as r:
                ok = bool(json.loads(r.read().decode("utf-8")).get("ok"))
        except OSError:
            pass
        out.append({"name": name, "port": port, "ok": ok})
    return out


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def km_today_sent():
    n = 0
    try:
        with open(KM_STATS, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("date") == today_str():
                    n += r.get("sent_ok", 0)
    except OSError:
        pass
    return n


def test_sub(sid):
    """试推订阅（subscribe.py --test force：忽略 enabled/cap，真实发送）。"""
    if not SID_RE.match(sid or "") or sid not in {s["id"] for s in load_subs()}:
        return {"ok": False, "error": "订阅 id 非法：%s" % sid}
    if _state["running"]:
        return {"ok": False, "error": "已有试推在进行"}
    _state["running"] = True

    def worker():
        os.makedirs(WORK, exist_ok=True)
        try:
            with open(TEST_LOG, "ab") as out:
                out.write(("\n=== test %s %s ===\n" % (sid, datetime.now().isoformat(timespec="seconds"))).encode())
                out.flush()
                p = subprocess.Popen(
                    [sys.executable, "-X", "utf8", SUB_PY, "--test", sid],
                    cwd=os.path.dirname(SUB_PY),
                    stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
                _state["last_test"] = {"id": sid, "ts": datetime.now().isoformat(timespec="seconds"),
                                       "status": "running"}
                try:
                    rc = p.wait(timeout=180)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
                    rc = -9
            tail = ""
            try:
                with open(TEST_LOG, encoding="utf-8", errors="replace") as f:
                    tail = f.read().splitlines()[-1]
            except OSError:
                pass
            _state["last_test"] = {"id": sid, "ts": datetime.now().isoformat(timespec="seconds"),
                                   "status": "done", "rc": rc, "tail": tail}
            log("[test] %s rc=%d %s" % (sid, rc, tail))
        except Exception as e:  # noqa: BLE001
            _state["last_test"] = {"id": sid, "status": "error", "error": str(e)}
        finally:
            _state["running"] = False
    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True}


# ---------- HTTP ----------

def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        if p == "/api/state":
            subs = load_subs()
            recvs = load_receivers()
            st = sub_state()
            today = today_str()
            tasks = tasks_snapshot()
            manageable = [{"name": n, **tasks.get(n, {}), "manage": True}
                          for n in TASK_MANAGEABLE]
            readonly = [{"name": n, **tasks.get(n, {}), "manage": False}
                        for n in TASK_READONLY if n in tasks]
            sub_rows = []
            sub_sent_today = 0
            for s in subs:
                ss = st.get(s["id"], {})
                cnt = ss.get("count", 0) if ss.get("day") == today else 0
                sub_sent_today += cnt
                sub_rows.append({"id": s["id"], "name": s.get("name"),
                                 "kind": s.get("kind"), "when": s.get("when"),
                                 "receivers": s.get("receivers") or [],
                                 "note": s.get("note", ""),
                                 "enabled": s.get("enabled", True),
                                 "today_sent": cnt,
                                 "last_ts": ss.get("last_ts", "")})
            km_sent = km_today_sent()
            tasks_ran_today = sum(
                1 for t in (manageable + readonly)
                if (t.get("last") or "").startswith(today))
            try:  # 统一发送流水（商业化计费依据）
                sys.path.insert(0, STATION)
                import push_flow
                flow = push_flow.stats(7)
            except Exception:
                flow = {"by_day": [], "today_by_kind": {}}
            try:  # K-PUSH-002：调优闭环 + 分享令牌概览（读侧降级为空）
                import tune_gate
                import share_tokens
                import sys as _sys
                _sys.path.insert(0, STATION)
                import feedback_flow
                tune = {"stats": feedback_flow.stats(7),
                        "requests": tune_gate.list_requests(limit=20),
                        "vip": tune_gate.vip_overrides()}
            except Exception:
                tune = {"stats": {"by_day": [], "today_by_stage": {}},
                        "requests": [], "vip": {}}
            try:
                import share_tokens as _st
                share = _st.stats(7)
            except Exception:
                share = {"by_day": [], "today_visit_by_kind": {}}
            self._json({
                "heartbeat": heartbeat_ok(),
                "tasks": manageable + readonly,
                "subs": sub_rows,
                "receivers": recvs,
                "news_targets": news_targets(),
                "today": {"sub_sent": sub_sent_today, "km_sent": km_sent,
                          "tasks_ran": tasks_ran_today,
                          "total": sub_sent_today + km_sent},
                "last_test": _state.get("last_test"),
                "running": _state["running"],
                "sources": source_health(),
                "biz": load_biz(),
                "flow": flow,
                "flowmx": flow_matrix(),   # K-PUSH-003：今日动线矩阵（源/触点/闸门/拦截）
                "actmx": _actions_stats(),   # K-PUSH-004：按钮归档注册表 + 审计
                "tune": tune,   # K-PUSH-002：调优闭环概览
                "share": share,   # K-PUSH-002：分享令牌概览
                "shadow": _shadow_stats()})  # P3：FOCUS_CN 影子组毕业统计（300s 缓存）
        elif p == "/api/open/stats":
            # 开放 API（只读）——商业化接入点：X-API-Key 头或 ?key= 均可
            biz = load_biz()
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            key = (self.headers.get("X-API-Key")
                   or (q.get("key", [""])[0] or ""))
            if not biz.get("apikey") or not hmac.compare_digest(
                    str(key), str(biz["apikey"])):
                return self._json(
                    {"ok": False, "error": "invalid api key"}, 403)
            try:
                sys.path.insert(0, STATION)
                import push_flow
                flow = push_flow.stats(7)
            except Exception:
                flow = {"by_day": [], "today_by_kind": {}}
            subs = load_subs()
            self._json({
                "ok": True,
                "tenant": biz.get("tenant", ""),
                "plan": biz.get("plan", "internal"),
                "quota_daily": biz.get("quota_daily", 0),
                "sent_today": sum(flow.get("today_by_kind", {}).values()),
                "flow": flow,
                "subs": [{"id": s["id"], "name": s.get("name"),
                          "kind": s.get("kind"),
                          "enabled": s.get("enabled", True)} for s in subs],
                "receivers": len(load_receivers()),
                "generated_at": datetime.now().isoformat(timespec="seconds")})
        elif p == "/health":
            self._json({"ok": True, "ts": datetime.now().isoformat(timespec="seconds")})
        elif p == "/":
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        if p == "/api/send":
            # K-PUSH-003 推送中台统一入口：闸门（静默/配额）→ 直发 → 统一流水
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if not (0 < n <= BODY_CAP):
                    return self._json({"ok": False, "error": "body 大小非法"}, 400)
                req = json.loads(self.rfile.read(n).decode("utf-8"))
            except (ValueError, OSError) as e:
                return self._json({"ok": False, "error": "请求体解析失败: %s" % e}, 400)
            try:
                self._json(api_send(req))
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "error": "%s" % e}, 500)
            return
        if p != "/api/act":
            return self._json({"ok": False, "error": "not found"}, 404)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if not (0 < n <= BODY_CAP):
                return self._json({"ok": False, "error": "body 大小非法"}, 400)
            req = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, OSError) as e:
            return self._json({"ok": False, "error": "请求体解析失败: %s" % e}, 400)
        try:
            self._json(dispatch(req))
        except Exception as e:  # noqa: BLE001
            self._json({"ok": False, "error": "%s" % e}, 500)


# ---------- K-PUSH-003 统一发送闸门 ----------

def load_schedule():
    """静默时段配置。默认空 = 不拦任何推送（P1 安全默认）。"""
    try:
        with open(SCHEDULE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"silent_start": "", "silent_end": ""}


def _in_silent_window(now=None):
    s = load_schedule()
    a, b = (s.get("silent_start") or "").strip(), (s.get("silent_end") or "").strip()
    if not a or not b:
        return False
    cur = (now or datetime.now()).strftime("%H:%M")
    if a <= b:                      # 同日窗口，如 12:00-13:30
        return a <= cur < b
    return cur >= a or cur < b      # 跨午夜窗口，如 23:00-07:00


def _flow_gate_deny(kind, target, why):
    """闸门拦截也落流水（ok=false，可追溯）。why 并入 kind（record 无 note 位）。"""
    try:
        sys.path.insert(0, STATION)
        import push_flow
        push_flow.record("%s|deny:%s" % (kind or "hub", why)[:80],
                         "hub-gate", target, 1, False)
    except Exception:
        pass


def api_send(req):
    """/api/send 统一入口：静默（critical 豁免）→ 配额 → 直发 → 流水。

    feishu_client.send_msg(via_hub=True) 全站自动过闸；hub 不可达时调用方
    回落原直发路径（通知不丢）。闸内转发必须 via_hub=False 防环。
    """
    content = req.get("content")
    msg_type = req.get("msg_type") or "text"
    rid = req.get("receive_id")
    rtype = req.get("receive_id_type") or "open_id"
    kind = (req.get("kind") or "").strip() or None
    critical = bool(req.get("critical"))
    if not content or not rid:
        return {"ok": False, "error": "missing content/receive_id"}

    # 闸 1：静默时段（预警类豁免——风险提示不可吞）
    if not critical and _in_silent_window():
        _flow_gate_deny(kind, rid, "silent window")
        return {"ok": False, "error": "silent window"}

    # 闸 2：套餐配额（internal 永不拦，承 push_flow.quota_check 判例）
    try:
        sys.path.insert(0, STATION)
        import push_flow
        plan, quota, sent, blocked = push_flow.quota_check()
        if blocked:
            _flow_gate_deny(kind, rid, "quota %d/%d" % (sent, quota))
            return {"ok": False,
                    "error": "quota block: plan=%s sent=%d quota=%d" % (
                        plan, sent, quota)}
    except Exception:
        pass  # 配额判定异常不拦发送（fail-safe）

    # 闸 3（K-PUSH-004）：卡片按钮注册校验——未注册/禁用动作的按钮，
    # warn 模式仅记观察流水，enforce 模式拒发（按钮统一归档的强制入口）
    if msg_type == "interactive":
        try:
            import card_actions
            card = json.loads(content) if isinstance(content, str) else content
            ok3, why3, bad = card_actions.check_card(card)
            if not ok3:
                _flow_gate_deny(kind, rid, "unregistered-btn %s" % ",".join(bad)[:40])
                return {"ok": False, "error": why3, "via": "hub"}
        except Exception:
            pass  # 解析异常不拦发送（fail-safe）

    # 直发（via_hub=False 防环；kind 透传让流水归准）
    news_dir = os.path.join(STATION, "news_collector")
    if news_dir not in sys.path:
        sys.path.insert(0, news_dir)
    from feishu_client import send_msg
    ok, why = send_msg(content, msg_type, rid, rtype, kind=kind,
                       via_hub=False)
    return {"ok": bool(ok), "why": why, "via": "hub"}


# ---------- K-PUSH-003 动线总览数据 ----------

def _actions_stats():
    """K-PUSH-004：按钮归档数据（读侧降级为空，页面不崩）。"""
    try:
        import card_actions
        return card_actions.stats()
    except Exception:
        return {"actions": [], "total": 0, "enforce": False, "events": []}


def _kind_group(k):
    k = str(k or "")
    if "news" in k:
        return "新闻资讯"
    if "daily_report" in k or k == "daily:report":
        return "日报任务"
    if "alert" in k:
        return "预警推送"
    if k.startswith("subscribe:"):
        return "订阅引擎"
    if "km" in k:
        return "知识库KM"
    if "jev" in k:
        return "盯盘JEV"
    if "bot" in k or "push_admin" in k or "tune" in k:
        return "调优/bot"
    if "send_feishu" in k:
        return "文本通道"
    return "其他"


def _target_group(t):
    t = str(t or "")
    if t.startswith("ou_50a95455"):
        return "个人"
    if t.startswith("oc_6b97"):
        return "家人群"
    if t.startswith("oc_16e"):
        return "量化群"
    if "," in t:
        return "多目标"
    if t.startswith("oc_"):
        return "其他群"
    return t[:12] or "?"


_shadow_cache = {"t": 0.0, "data": None}


def _shadow_stats():
    """P3 观测面板：FOCUS_CN 影子组毕业统计（调 jev-eval CLI，300s 缓存）。

    subprocess 判例：Popen+bytes+手动 decode；-X utf8 强制管道输出 utf-8
    （Windows 管道默认 GBK，ensure_ascii=False 的中文/数学负号会炸）。
    """
    if (_shadow_cache["data"] is not None
            and time.time() - _shadow_cache["t"] <= 300):
        return _shadow_cache["data"]
    jev_dir = os.path.join(STATION, "jev-eval")
    try:
        p = subprocess.Popen(
            [sys.executable, "-X", "utf8", "jev_graduation_status.py",
             "--json"],
            cwd=jev_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        so, _ = p.communicate(timeout=30)
        data = json.loads(so.decode("utf-8", "replace"))
        _shadow_cache["t"] = time.time()
        _shadow_cache["data"] = data
        return data
    except Exception:
        return _shadow_cache["data"]  # 降级：无数据/旧数据


def flow_matrix():
    """今日动线矩阵：源归组计数 / 触点归组计数 / 拦截明细（真实流水聚合）。"""
    import datetime as dt
    today = dt.date.today().isoformat()
    flow_path = os.path.join(WORK, "push_flow.jsonl")
    src, dst, deny = {}, {}, []
    try:
        with open(flow_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("date") != today:
                    continue
                c = int(r.get("count", 1))
                ok = bool(r.get("ok", True))
                k = str(r.get("kind", ""))
                src[_kind_group(k)] = src.get(_kind_group(k), 0) + c
                dst[_target_group(r.get("target", ""))] = \
                    dst.get(_target_group(r.get("target", "")), 0) + c
                if not ok or "deny" in k:
                    deny.append({"ts": r.get("ts", "")[-8:], "kind": k[:40],
                                 "target": _target_group(r.get("target", "")),
                                 "note": "拦截" if "deny" in k else "发送失败"})
    except OSError:
        pass
    import push_flow
    try:
        plan, daily, sent, blocked = push_flow.quota_check()
        quota = {"plan": plan, "daily": daily, "sent": sent, "blocked": blocked}
    except Exception:
        quota = {}
    return {"sources": src, "targets": dst, "deny": deny[-8:],
            "gate": {"silent": load_schedule(), "hub": "on", "quota": quota}}


def dispatch(req):
    act = req.get("action")

    if act == "toggle_action":   # K-PUSH-004：按钮归档启停
        import card_actions
        on = card_actions.toggle(req.get("id"), bool(req.get("enabled")))
        return {"ok": True, "enabled": on}

    if act == "set_action_enforce":   # K-PUSH-004：未注册按钮 warn/enforce
        import card_actions
        return {"ok": True, "enforce": card_actions.set_enforce(
            bool(req.get("on")))}

    if act == "toggle_sub":
        subs = load_subs()
        for s in subs:
            if s["id"] == req.get("id"):
                s["enabled"] = not s.get("enabled", True)
        save_subs(subs)
        return {"ok": True}

    if act == "set_sub_receivers":
        sid = req.get("id")
        names = req.get("receivers") or []
        # 严格按 receivers.json 校验（订阅引擎 _resolve_targets 只认这份名单，
        # 不在名单里的名字会被静默丢弃——宁可在这里拒绝也不静默）
        known = {r["name"] for r in load_receivers()}
        bad = [n for n in names if n not in known]
        if bad:
            return {"ok": False, "error":
                    "接收人不在 receivers.json（先在接收人表添加或点同步）：%s" % bad}
        subs = load_subs()
        for s in subs:
            if s["id"] == sid:
                s["receivers"] = names
        save_subs(subs)
        return {"ok": True}

    if act == "sync_receivers_from_news":
        recvs = load_receivers()
        names = {r["name"] for r in recvs}
        added = []
        for t in news_targets():
            if t.get("name") and t["name"] not in names:
                recvs.append({"name": t["name"], "type": t["type"],
                              "id": t["id"], "note": "同步自 news push_targets"})
                added.append(t["name"])
        save_receivers(recvs)
        return {"ok": True, "added": added}

    if act == "add_receiver":
        r = req.get("receiver") or {}
        if not r.get("name") or not r.get("id") or \
                r.get("type") not in ("chat_id", "open_id"):
            return {"ok": False, "error": "需含 name/id/type(chat_id|open_id)"}
        recvs = load_receivers()
        if any(x["name"] == r["name"] for x in recvs):
            return {"ok": False, "error": "接收人已存在：%s" % r["name"]}
        recvs.append({"name": r["name"], "type": r["type"], "id": r["id"],
                      "note": r.get("note", "")})
        save_receivers(recvs)
        return {"ok": True}

    if act == "remove_receiver":
        name = req.get("name")
        using = [s["id"] for s in load_subs() if name in (s.get("receivers") or [])]
        if using:
            return {"ok": False, "error": "仍被订阅引用：%s" % using}
        recvs = load_receivers()
        recvs[:] = [r for r in recvs if r["name"] != name]
        save_receivers(recvs)
        return {"ok": True}

    if act == "set_task_time":
        ok, err = ps_set_task_time(req.get("name") or "", req.get("time") or "")
        return {"ok": ok, **({"error": err} if err else {})}

    if act == "set_biz":
        biz = load_biz()
        plan = req.get("plan") or biz.get("plan") or "internal"
        if plan not in PLANS:
            return {"ok": False, "error": "未知套餐：%s" % plan}
        try:
            quota = int(req.get("quota_daily", biz.get("quota_daily", 0)))
        except (TypeError, ValueError):
            return {"ok": False, "error": "日配额必须是非负整数"}
        if quota < 0 or quota > 100000:
            return {"ok": False, "error": "日配额超出范围（0-100000）"}
        biz["plan"] = plan
        biz["quota_daily"] = quota
        biz["tenant"] = (req.get("tenant") or biz.get("tenant") or "").strip()[:40]
        biz["note"] = (req.get("note") or biz.get("note") or "").strip()[:200]
        if not biz.get("apikey"):
            import secrets
            biz["apikey"] = "pk_" + secrets.token_hex(16)
        save_biz(biz)
        return {"ok": True, "biz": biz}

    if act == "regen_apikey":
        import secrets
        biz = load_biz()
        biz["apikey"] = "pk_" + secrets.token_hex(16)
        save_biz(biz)
        return {"ok": True, "biz": biz}

    if act == "run_task":
        ok, err = ps_run_task(req.get("name") or "")
        return {"ok": ok, **({"error": err} if err else {})}

    if act == "test_sub":
        return test_sub(req.get("id") or "")

    # ---------- K-PUSH-002：调优闸门 + 分享令牌（tune_gate/share_tokens） ----------
    if act == "tune_submit":
        # 受理一条调优请求（bot 指令之外的第二入口：API/管理台测试）
        import tune_gate
        if not (req.get("text") or "").strip():
            return {"ok": False, "error": "text 必填（客户反馈原文）"}
        rid = tune_gate.submit(req["text"],
                               customer=req.get("customer") or "API提交",
                               plan=req.get("plan") or "internal",
                               source=req.get("source") or "text")
        return {"ok": True, "req_id": rid}

    if act == "tune_approve":
        import tune_gate
        return tune_gate.approve(req.get("req_id") or "",
                                 operator=req.get("operator") or "8097管理台",
                                 note=req.get("note") or "")

    if act == "tune_reject":
        import tune_gate
        r = tune_gate.reject(req.get("req_id") or "",
                             operator=req.get("operator") or "8097管理台",
                             reason=req.get("reason") or "")
        return r

    if act == "tune_deploy":
        import tune_gate
        return tune_gate.deploy(req.get("req_id") or "",
                                operator=req.get("operator") or "8097管理台",
                                params=req.get("params"))

    if act == "tune_list":
        import tune_gate
        return {"ok": True,
                "requests": tune_gate.list_requests(req.get("status"))}

    if act == "share_gen":
        # 签发一枚分享 token（订阅卡片深链用；也可手动签发测试）
        import share_tokens
        tok = share_tokens.gen(code=req.get("code") or "",
                               kind=req.get("kind") or "manual",
                               ttl_hours=int(req.get("ttl_hours") or 72))
        if not tok:
            return {"ok": False, "error": "share_secret 不可用（见 work/）"}
        return {"ok": True, "token": tok}

    return {"ok": False, "error": "unknown action: %s" % act}


# ---------- 页面 ----------

PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>AI 工作站 · 推送中台</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#0d1117;--card:#161b22;--ink:#e6edf3;--dim:#8b949e;--line:#30363d;--blue:#58a6ff;--green:#3fb950;--red:#f85149;--amber:#d29922}
body{margin:0;font-family:"Microsoft YaHei",system-ui,sans-serif;background:var(--bg);color:var(--ink)}
.wrap{max-width:1100px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:20px;margin:0 0 4px}.sub{color:var(--dim);font-size:12px;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:14px}
.card h2{font-size:14px;margin:0 0 10px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-weight:normal;font-size:12px}
button{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:6px;padding:4px 10px;font-size:12px;cursor:pointer}
button:hover{border-color:var(--blue)}button.primary{background:#1f6feb;color:#fff;border-color:#1f6feb}
button.danger{color:var(--red)}button:disabled{opacity:.5;cursor:default}
input,select{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:6px;padding:5px 8px;font-size:12px}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.kpi{background:#0f141c;border:1px solid var(--line);border-radius:8px;padding:10px 12px}
.kpi b{font-size:22px;display:block}.kpi span{font-size:11px;color:var(--dim)}
.tag{display:inline-block;background:#1c2a44;color:var(--blue);border-radius:4px;padding:1px 6px;font-size:11px;margin:1px}
.ok{color:var(--green)}.err{color:var(--red)}.warn{color:var(--amber)}.dim{color:var(--dim)}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:8px}
input[type=time]{width:90px}
#msg{position:fixed;top:12px;right:12px;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 14px;font-size:13px;display:none;box-shadow:0 4px 14px rgba(0,0,0,.45);max-width:420px;z-index:9}
.nav{position:fixed;left:0;top:0;bottom:0;width:190px;background:#0f141c;border-right:1px solid var(--line);color:#c9d1d9;padding:18px 12px;box-sizing:border-box;display:flex;flex-direction:column;gap:4px;z-index:2}
.brand{font-size:15px;font-weight:bold;color:#e6edf3;margin:0 4px 14px;line-height:1.35}
.brand span{display:block;color:var(--blue);font-size:12px;font-weight:normal}
.navi{padding:8px 10px;border-radius:8px;cursor:pointer;font-size:13px;color:#c9d1d9}
.navi:hover{background:#21262d}
.navi.on{background:#1f6feb;color:#fff}
.slogan{margin-top:auto;padding:0 6px;font-size:11px;color:#8b949e;line-height:1.7}
.main{margin-left:190px;padding:20px 22px 60px;max-width:1060px}
.sec iframe{width:100%;height:calc(100vh - 60px);border:1px solid var(--line);border-radius:10px;background:var(--bg)}
.key{font-family:Consolas,monospace;background:#21262d;border-radius:6px;padding:2px 8px;font-size:12px}
.trend{display:flex;align-items:flex-end;gap:6px;height:90px;margin:6px 0 10px}
.trend .bar{flex:1;background:var(--blue);border-radius:4px 4px 0 0;min-height:3px;position:relative;opacity:.85}
.trend .bar span{position:absolute;bottom:-18px;left:0;right:0;text-align:center;font-size:10px;color:var(--dim)}
.trend .bar em{position:absolute;top:-16px;left:0;right:0;text-align:center;font-size:11px;font-style:normal;color:var(--ink)}
.flowgrid{display:grid;grid-template-columns:1fr 330px 1fr;gap:14px}
.frow{display:flex;align-items:center;gap:8px;margin:7px 0}
.fname{flex:0 0 120px;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ftrack{flex:1;background:#0f141c;border:1px solid var(--line);border-radius:5px;height:16px;overflow:hidden}
.fbar{height:100%;background:var(--blue);border-radius:4px;min-width:4px}
.frow b{flex:0 0 36px;text-align:right;font-size:13px}
.frow.dst .fbar{background:var(--green)}
</style></head><body>
<aside class="nav">
<div class="brand">🛰 推送中台<span>情报变现 · 消息触达底座</span></div>
<a class="navi on" data-s="overview" onclick="show('overview')">总览</a>
<a class="navi" data-s="flow" onclick="show('flow')">动线总览</a>
<a class="navi" data-s="obs" onclick="show('obs')">🔍 观测</a>
<a class="navi" data-s="btns" onclick="show('btns')">按钮归档</a>
<a class="navi" data-s="push" onclick="show('push')">推送管理</a>
<a class="navi" data-s="km" onclick="show('km')">知识库联动</a>
<a class="navi" data-s="picks" onclick="show('picks')">定时发布</a>
<a class="navi" data-s="biz" onclick="show('biz')">商业化 · 底座</a>
<div class="slogan">软硬一体 · 云边协同<br>定位：能力平台<br>127.0.0.1 · 仅本机访问</div>
</aside>
<main class="main">

<div id="sec-overview" class="sec">
<h1>总览</h1><div class="sub" id="sub">加载中…</div>
<div class="card"><h2>今日推送</h2><div class="kpis" id="kpis"></div></div>
<div class="card"><h2>近 7 天发送趋势（统一流水 · 计费依据）</h2><div class="trend" id="trend"></div><table id="flowkinds"></table></div>
<div class="card"><h2>内容源健康（内容供给方 → 本中台 → 飞书群/个人）</h2><table id="srcs"></table></div>
<div class="card"><h2>底座能力清单（商业化盘点）</h2><table>
<tr><td>✅ 内容供给</td><td>选股看板 · 知识库增量 · 盯盘异动 · 新闻分析 —— 4 类内容源已接入</td></tr>
<tr><td>✅ 渠道触达</td><td>飞书群 / 飞书个人（chat_id / open_id 注册表统一管理）</td></tr>
<tr><td>✅ 触达策略</td><td>定时推送（计划任务 + 每库排程）· 盘中变化推 · 每日熔断防轰炸 · 试推验证</td></tr>
<tr><td>✅ 用量统计</td><td>今日推送 KPI · 订阅计数 · KM 发送流水（km_stats.jsonl）</td></tr>
<tr><td>🟡 计量计费</td><td>统一发送流水表（计费依据）—— 规划中，下一步接入订阅引擎发送点</td></tr>
<tr><td>🟡 租户与开放</td><td>租户 / 套餐 / 日配额 / API Key 已预留（「商业化·底座」页可配）—— 多租户隔离与开放 API 规划中</td></tr>
<tr><td>⬜ 增值渠道</td><td>短信 / 邮件通道 —— 规划中</td></tr>
</table></div>
</div>

<div id="sec-flow" class="sec" style="display:none">
<h1>动线总览</h1><div class="sub">今日全部出站消息动线：源 → HUB 统一闸门 → 飞书触点（push_flow 真实流水聚合，5 秒自动刷新）</div>
<div class="card"><h2>今日链路拓扑（桑基 · 带宽 = 条数占比）</h2><div id="sankey"></div></div>
<div class="flowgrid">
<div class="card"><h2>📥 源（谁在推）</h2><div id="fmsrc"></div></div>
<div class="card"><h2>🛰 HUB 统一闸门</h2><div id="fmgate"></div></div>
<div class="card"><h2>📤 触点（推给谁）</h2><div id="fmdst"></div></div>
</div>
<div class="card"><h2>今日拦截 / 失败明细（闸门拦截同样落流水，可追溯）</h2><table id="fmdeny"></table></div>
</div>

<div id="sec-obs" class="sec" style="display:none">
<h1>观测</h1><div class="sub">P3 增量：FOCUS_CN 影子组毕业进度 + 守护触发器状态 —— 影子转正纪律与守护覆盖一眼可见</div>
<div class="card"><h2>FOCUS_CN 影子组（2026-10-16 起 · CN 相对临界 quality_p≤0.53 · 只积累不拦截，转正需人工）</h2><div id="shadowbox"><span class=dim>加载中…（jev-eval 毕业统计，首次 5-10s）</span></div></div>
<div class="card"><h2>守护触发器状态（next 触发 + 24h 轴 · 数据源 8091 /api/health，经门户 :8080/push/ 访问可见）</h2><div id="guardbox"><span class=dim>加载中…</span></div></div>
</div>

<div id="sec-btns" class="sec" style="display:none">
<h1>按钮归档</h1><div class="sub">交互动作统一注册表 —— 全站按钮/指令/H5 动作单一事实源；未注册动作默认 warn 观察，开启 enforce 后 HUB 拒发；每次触发全审计（action_log.jsonl）</div>
<div class="card"><h2>注册表（<span id="acttotal">0</span> 项） <button id="enfbtn" onclick="toggleEnforce()"></button></h2>
<table id="acttbl"></table></div>
<div class="card"><h2>触发审计流水（who · when · card · result）</h2><table id="actlog"></table></div>
</div>

<div id="sec-push" class="sec" style="display:none">

<div class="card"><h2>今日推送总览</h2><div class="kpis" id="kpis2"></div></div>

<div class="card"><h2>日报任务（时间点 = Windows 计划任务，改后即时生效）</h2>
<table id="tasks"></table></div>

<div class="card"><h2>订阅推送（盘中变化推 · 盘后全量 · 每日 ≤3 推熔断）</h2>
<table id="subs"></table></div>

<div class="card"><h2>接收人（receivers.json——订阅按名字解析） <button onclick="act({action:'sync_receivers_from_news'}).then(d=>{if(d.ok){msg('已同步，新增：'+(d.added.join(',')||'无'));refresh()}})">从推送目标一键同步</button></h2>
<table id="recvs"></table>
<div class="row">
<input id="r_name" placeholder="名称（如：量化群）" style="width:140px">
<input id="r_id" placeholder="oc_（群）/ ou_（个人）开头 ID" style="width:340px">
<select id="r_type"><option value="chat_id">群</option><option value="open_id">个人</option></select>
<input id="r_note" placeholder="备注（可空）" style="width:160px">
<button class="primary" onclick="addRecv()">添加</button></div></div>

<div class="card"><h2>试推日志</h2><pre id="tlog" style="font-size:11px;color:var(--dim);white-space:pre-wrap"></pre></div>
</div>

<div id="sec-km" class="sec" style="display:none"><iframe id="f_km" data-src="http://127.0.0.1:8099/" title="知识库联动"></iframe></div>
<div id="sec-picks" class="sec" style="display:none"><iframe id="f_picks" data-src="http://127.0.0.1:8095/" title="定时发布"></iframe></div>

<div id="sec-biz" class="sec" style="display:none">
<h1>商业化 · 底座</h1><div class="sub">定位：AI 工作站的消息触达底座 —— 对内是运营中枢，对外是可售卖的推送 SaaS 底座（随硬件预装 = 获客入口）</div>
<div class="card"><h2>租户与套餐</h2>
<div class="row">租户 <input id="b_tenant" style="width:150px"> 套餐
<select id="b_plan"><option value="internal">internal 内部自用</option><option value="trial">trial 试用</option><option value="standard">standard 标准</option><option value="pro">pro 专业</option></select>
日配额 <input id="b_quota" type="number" min="0" style="width:90px"> 条
<button class="primary" onclick="saveBiz()">保存</button>
<button onclick="if(confirm('重置 API Key？旧 Key 立即失效'))act({action:'regen_apikey'}).then(d=>{if(d.ok){msg('API Key 已重置');refresh()}})">重置 API Key</button></div>
<table id="bizinfo"></table></div>
<div class="card"><h2>套餐与变现路线</h2><table>
<tr><th>套餐</th><th>定位</th><th>内容</th></tr>
<tr><td><b>internal</b></td><td>内部自用（当前）</td><td>全功能，不计量</td></tr>
<tr><td><b>trial</b></td><td>获客试用</td><td>随 ARM 服务器 / AI 工作站预装开通，7 天全功能体验</td></tr>
<tr><td><b>standard</b></td><td>首期收费</td><td>3 个群 + 定时摘要 + 每日统计，月订阅</td></tr>
<tr><td><b>pro</b></td><td>进阶</td><td>多库多渠道 + 盯盘异动实时推 + API 接入，月订阅 + 条数包</td></tr>
</table>
<div class="row dim" style="font-size:12px">变现钩子：① 随硬件预装开通（获客入口）② 月度订阅（standard / pro）③ 按量条数包 ④ 增值渠道（短信 / 邮件）。配额与流水是计费依据——统一流水表接入后即可计量开单。</div></div>
</div>

</main><div id="msg"></div>
<script>
const $=id=>document.getElementById(id);
function msg(t,bad){const m=$('msg');m.textContent=t;m.style.display='block';m.style.color=bad?'var(--red)':'var(--green)';setTimeout(()=>m.style.display='none',5000)}
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
async function act(body){try{const r=await fetch('api/act',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();if(!d.ok)msg(d.error||'失败',true);return d}catch(e){msg('请求失败：'+e,true);return{ok:false}}}
const OK=['金融新闻日报','AI工作站统一日报','AI工作站预警推送晨','AI工作站预警推送'];
function inSilent(s){const a=String(s.silent_start||'').trim(),b=String(s.silent_end||'').trim();if(!a||!b)return false;const c=new Date().toTimeString().slice(0,5);return a<=b?(a<=c&&c<b):(c>=a||c<b)}
function fbars(o,cls){const ks=Object.keys(o||{}),m=Math.max(1,...ks.map(k=>o[k]));
 return ks.length?ks.map(k=>'<div class="frow '+(cls||'')+'"><span class=fname title="'+esc(k)+'">'+esc(k)+'</span><div class=ftrack><div class=fbar style="width:'+Math.max(4,Math.round(o[k]/m*100))+'%"></div></div><b>'+o[k]+'</b></div>').join(''):'<span class=dim>今日暂无发送</span>'}
function renderFlow(mx){if(!mx)return;
renderSankey(mx);
$('fmsrc').innerHTML=fbars(mx.sources);
$('fmdst').innerHTML=fbars(mx.targets,'dst');
const g=mx.gate||{},s=g.silent||{},q=g.quota||{};
const sil=s.silent_start&&s.silent_end,inS=inSilent(s);
$('fmgate').innerHTML='<table>'
 +'<tr><th>闸门</th><td><span class=ok>● 运行中</span> <span class=dim>8097 · /api/send 统一收口（卡片+文本全自动过闸）</span></td></tr>'
 +'<tr><th>静默时段</th><td>'+(sil?esc(s.silent_start)+' – '+esc(s.silent_end)+(inS?' <span class=warn>（当前静默中 · critical 直通）</span>':' <span class=ok>（当前放行）</span>'):'<span class=dim>未配置 = 不拦（P1 安全默认）</span>')+'</td></tr>'
 +'<tr><th>今日配额</th><td>'+(q.plan?esc(q.plan)+' · 已发 <b>'+q.sent+'</b>'+(q.daily>0?' / '+q.daily+' 条':'（internal 不限）')+(q.blocked?' <span class=err>已达额拦截</span>':''):'<span class=dim>—</span>')+'</td></tr>'
 +'<tr><th>回落</th><td class=dim>hub 不可达时自动回落直发 · 通知不丢（fail-safe）</td></tr></table>';
const dn=mx.deny||[];
$('fmdeny').innerHTML='<thead><tr><th>时间</th><th>kind</th><th>触点</th><th>结果</th></tr></thead><tbody>'
 +(dn.length?dn.map(x=>'<tr><td class=dim>'+esc(x.ts)+'</td><td>'+esc(x.kind)+'</td><td>'+esc(x.target)+'</td><td><span class=err>'+esc(x.note)+'</span></td></tr>').join(''):'<tr><td colspan=4 class=dim>今日无拦截 / 失败记录</td></tr>')+'</tbody>';}
/* ── P3 观测：桑基拓扑（源→HUB→触点，纯 SVG 手绘无依赖） ── */
function renderSankey(mx){
 const el=$('sankey');if(!el)return;
 const S=(mx&&mx.sources)||{},T=(mx&&mx.targets)||{};
 if(!Object.keys(S).length&&!Object.keys(T).length){el.innerHTML='<span class=dim>今日暂无真实发送（流水只记真实发送，跳过/熔断不记）</span>';return;}
 const sk=Object.entries(S).sort((a,b)=>b[1]-a[1]),tk=Object.entries(T).sort((a,b)=>b[1]-a[1]);
 const sT=sk.reduce((a,x)=>a+x[1],0)||1,tT=tk.reduce((a,x)=>a+x[1],0)||1;
 const H=Math.max(220,Math.max(sk.length,tk.length)*30+56),pt=26,ih=H-pt-10;
 const W=800,lw=200,rw=200,x0=lw,x1=W-rw,hx=x0+80,hw=x1-x0-160;
 let g='<rect x="'+hx+'" y="'+pt+'" width="'+hw+'" height="'+ih+'" rx="4" fill="#1f6feb22" stroke="#58a6ff" stroke-opacity=".45"/>'
  +'<text x="'+(hx+hw/2)+'" y="'+(pt+15)+'" text-anchor="middle" font-size="12" fill="#58a6ff">HUB 统一闸门 :8097</text>'
  +'<text x="'+(hx+hw/2)+'" y="'+(pt+31)+'" text-anchor="middle" font-size="11" fill="#8b949e">今日出站 '+Math.max(sT,1)+' 条</text>';
 let sy=pt,hy=pt,ty=pt,lb='';
 sk.forEach(x=>{const h=Math.max(3,x[1]/sT*ih);
  g+='<rect x="'+(x0-12)+'" y="'+sy+'" width="12" height="'+h+'" rx="2" fill="#58a6ff" opacity=".85"/>';
  lb+='<text x="'+(x0-18)+'" y="'+(sy+h/2+4)+'" text-anchor="end" font-size="11" fill="#c9d1d9">'+esc(String(x[0]).slice(0,12))+' <tspan fill="#8b949e">'+x[1]+'</tspan></text>';
  const my=hy+h/2;
  g+='<path d="M'+x0+' '+sy+' C'+hx+' '+sy+' '+hx+' '+my+' '+hx+' '+my+'" stroke="#58a6ff" stroke-opacity=".3" stroke-width="'+h+'" fill="none"/>';
  sy+=h+6;hy+=h;});
 ty=pt;hy=pt;
 tk.forEach(x=>{const h=Math.max(3,x[1]/tT*ih);
  g+='<rect x="'+x1+'" y="'+ty+'" width="12" height="'+h+'" rx="2" fill="#3fb950" opacity=".85"/>';
  lb+='<text x="'+(x1+18)+'" y="'+(ty+h/2+4)+'" font-size="11" fill="#c9d1d9">'+esc(String(x[0]).slice(0,12))+' <tspan fill="#8b949e">'+x[1]+'</tspan></text>';
  const my=hy+h/2,ex=hx+hw;
  g+='<path d="M'+ex+' '+my+' C'+ex+' '+my+' '+x1+' '+ty+' '+x1+' '+ty+'" stroke="#3fb950" stroke-opacity=".3" stroke-width="'+h+'" fill="none"/>';
  ty+=h+6;hy+=h;});
 el.innerHTML='<svg viewBox="0 0 '+W+' '+H+'" style="width:100%;height:auto">'+g+lb+'</svg>';
}
/* ── P3 观测：FOCUS_CN 影子组面板（数据源 /api/state.shadow ← jev-eval 毕业统计） ── */
function renderShadow(sh){
 const el=$('shadowbox');if(!el)return;
 if(!sh){el.innerHTML='<span class=dim>影子组统计不可用（jev-eval 调用降级，稍后自动重试）</span>';return;}
 const cn=(sh.domains||{}).CN;
 if(!cn){el.innerHTML='<span class=dim>无 CN 域数据（影子组尚未产生判定记录）</span>';return;}
 const n=cn.n_eff||0,N=30,LEFT=Math.max(0,N-n),pct=Math.min(100,Math.round(n/N*100));
 const pp=v=>v==null?'—':(v>0?'+':'')+v+'pp';
 el.innerHTML='<div style="display:flex;gap:14px;flex-wrap:wrap;align-items:flex-start">'
 +'<div class="kpi" style="min-width:150px"><b>'+n+' <span style="font-size:12px;color:var(--dim)">/ '+N+'</span></b><span>影子样本 n_eff（唯一 ts_code）</span></div>'
 +'<div class="kpi" style="min-width:150px"><b style="color:'+(cn.cluster_mean_pp>1?'var(--green)':'var(--amber)')+'">'+pp(cn.cluster_mean_pp)+'</b><span>聚类均值（转正门槛 &gt;+1pp）</span></div>'
 +'<div class="kpi" style="min-width:150px"><b>'+pp(cn.focus_pass_gap_pp)+'</b><span>FOCUS−PASS 差（辅助证据）</span></div>'
 +'</div><div style="margin-top:10px;background:#0f141c;border:1px solid var(--line);border-radius:8px;height:14px;overflow:hidden"><div style="width:'+pct+'%;height:100%;background:linear-gradient(90deg,#1f6feb,#58a6ff)"></div></div>'
 +'<div class=dim style="font-size:11px;margin-top:6px">quality_p 刻度：CN 域相对临界 ≤0.53（triples.jsonl 30 分位标定） · '
 +(LEFT?'转正还差 <b style="color:var(--amber)">'+LEFT+'</b> 个标的（n_eff≥30 且聚类&gt;+1pp 后人工转正，不自动上生产）':'<span class=ok>样本量已达标</span>，待聚类达标后人工转正')
 +' · '+esc(cn.state||'')+' · 窗口 '+esc((sh.sample&&sh.sample.window)||'—')+' · 计算于 '+esc(String(sh.computed_at||'').replace('T',' '))+'</div>';
}
/* ── P3 观测：守护触发器（8091 /api/health 同源；直连 8097 时降级提示） ── */
var _gT=0;
function loadGuard(){
 const el=$('guardbox');if(!el)return;
 if(el.dataset.done&&Date.now()-_gT<60000)return;
 fetch('/api/health').then(r=>r.json()).then(h=>{
  _gT=Date.now();el.dataset.done=1;
  const ts=(h.tasks||[]).filter(t=>/Guard|JevStack|TushareSync/.test(t.name));
  const hm=s=>{const m=/([0-9]{1,2}):([0-9]{2})/.exec(String(s||''));return m?(+m[1])*60+(+m[2]):null};
  const dots=ts.map(t=>{const nx=hm(t.next_run),lx=hm(t.last_run),a=[];
   if(nx!=null)a.push('<div title="'+esc(t.name)+' 下次 '+esc(t.next_run)+'" style="position:absolute;left:'+(nx/1440*100)+'%;top:-3px;width:8px;height:8px;border-radius:50%;background:#58a6ff;box-shadow:0 0 4px #58a6ff88"></div>');
   if(lx!=null)a.push('<div title="'+esc(t.name)+' 上次 '+esc(t.last_run)+'" style="position:absolute;left:'+(lx/1440*100)+'%;top:4px;width:6px;height:6px;border-radius:50%;background:#3fb950"></div>');
   return a.join('')}).join('');
  el.innerHTML='<table><thead><tr><th>守护</th><th>状态</th><th>上次运行</th><th>下次运行</th><th>上次结果</th></tr></thead><tbody>'
   +ts.map(t=>'<tr><td><b>'+esc(t.name)+'</b></td><td>'+(t.state==='就绪'||t.state==='Ready'?'<span class=ok>就绪</span>':esc(t.state))+'</td><td class=dim>'+esc(t.last_run||'-')+'</td><td class=dim>'+esc(t.next_run||'-')+'</td><td>'+(t.last_result==='0'?'<span class=ok>✔ 0</span>':'<span class=err>✘ '+esc(t.last_result)+'</span>')+'</td></tr>').join('')
   +'</tbody></table>'
   +'<div class=dim style="font-size:11px;margin:12px 0 4px">今日 24h 触发轴（<span style="color:#58a6ff">●</span> 下次触发 · <span style="color:#3fb950">●</span> 上次运行）：</div>'
   +'<div style="position:relative;height:14px;background:#0f141c;border:1px solid var(--line);border-radius:7px">'
   +'<div style="position:absolute;left:25%;top:0;bottom:0;border-left:1px dashed #30363d"></div><div style="position:absolute;left:50%;top:0;bottom:0;border-left:1px dashed #30363d"></div><div style="position:absolute;left:75%;top:0;bottom:0;border-left:1px dashed #30363d"></div>'+dots+'</div>'
   +'<div class=dim style="font-size:10px;display:flex;justify-content:space-between"><span>0点</span><span>6点</span><span>12点</span><span>18点</span><span>24点</span></div>'
  /* 2026-10-17 翻案：旧「白天裸奔」为误判——schtasks CLI 建的任务 Repetition 无
     Duration 节点 = 无限期重复（非"当天止"）。实测白天 09:42 JevStack 正常轮转。
     改为动态停摆判定：next_run 落后当前 >2h 才是真停摆（JevStack/Guard 的 next
     恒在 10/30 分钟内的未来，仅跨天任务 next 可能落凌晨，2h 阈值不误伤）。 */
  +(function(){
   const now=new Date(),cm=now.getHours()*60+now.getMinutes();
   const stale=ts.filter(function(t){const nx=hm(t.next_run);return nx!=null&&cm-nx>120}).map(function(t){return t.name});
   return stale.length
    ?'<div class=warn style="font-size:11px;margin-top:8px">⚠ 触发停摆：'+stale.map(esc).join(' / ')+'（下次触发已落后当前 2h+，检查 schtasks 触发器）</div>'
    :'<div class=ok style="font-size:11px;margin-top:8px">✅ 全天守护健康：JevStack 每 10 分钟、HermesWebUI-Guard 每 30 分钟无限重复触发（实测白天正常轮转）</div>';
  })();
 }).catch(()=>{el.innerHTML='<span class=dim>守护数据不可用：需经门户 <a href="http://127.0.0.1:8080/push/" target=_blank>:8080/push/</a> 同源访问（直连 8097 时无 /api/health）</span>'});
}
function renderActions(ax){if(!ax)return;
$('acttotal').textContent=ax.total||0;window._enf=!!ax.enforce;
const eb=$('enfbtn');if(eb)eb.textContent=ax.enforce?'关闭 enforce（回 warn 观察）':'开启 enforce（未注册按钮拒发）';
const kd={card:'卡片按钮',h5:'H5 动作',cmd:'文本指令'};
$('acttbl').innerHTML='<thead><tr><th>action_id</th><th>名称</th><th>归属域</th><th>类型</th><th>回调</th><th>今日</th><th>状态</th><th>操作</th></tr></thead><tbody>'
 +(ax.actions||[]).map(a=>'<tr><td class=key>'+esc(a.id)+'</td><td>'+esc(a.name)+'</td><td>'+esc(a.domain)+'</td>'
  +'<td><span class=tag>'+esc(kd[a.kind]||a.kind)+'</span></td><td class=dim style="font-size:11px">'+esc(a.handler)+'</td><td><b>'+(a.today||0)+'</b></td>'
  +'<td>'+(a.enabled?'<span class=ok>启用</span>':'<span class=err>禁用</span>')+'</td>'
  +'<td><button onclick="act({action:\\'toggle_action\\',id:\\''+esc(a.id)+'\\',enabled:'+(a.enabled?'false':'true')+'}).then(refresh)">'+(a.enabled?'禁用':'启用')+'</button></td></tr>').join('')
 +'</tbody>';
$('actlog').innerHTML='<thead><tr><th>时间</th><th>动作</th><th>操作者</th><th>关联单/卡</th><th>结果</th></tr></thead><tbody>'
 +((ax.events||[]).length?ax.events.map(e=>'<tr><td class=dim>'+esc((e.ts||'').slice(5))+'</td><td class=key>'+esc(e.action)+'</td><td class=dim>'+esc(String(e.who||'').slice(0,14))+'</td><td class=dim>'+esc(e.card)+'</td><td>'+esc(e.result)+'</td></tr>').join(''):'<tr><td colspan=5 class=dim>暂无触发记录（按钮/指令触发后在此审计）</td></tr>')+'</tbody>';}
async function toggleEnforce(){const d=await act({action:'set_action_enforce',on:!window._enf});if(!d.ok){msg('切换失败',true);return}refresh();}
async function refresh(){try{const d=await(await fetch('api/state')).json();
$('sub').textContent='服务 '+(d.heartbeat?'✅':'❌ 心跳丢失')+(d.running?' · 试推进行中…':'');
const kpiHtml='<div class=kpi><b>'+d.today.total+'</b><span>今日推送合计</span></div>'
 +'<div class=kpi><b>'+d.today.sub_sent+'</b><span>订阅推送（今日次数）</span></div>'
 +'<div class=kpi><b>'+d.today.km_sent+'</b><span>知识库卡片（今日）</span></div>'
 +'<div class=kpi><b>'+d.today.tasks_ran+'</b><span>日报任务已运行</span></div>';
$('kpis').innerHTML=kpiHtml;$('kpis2').innerHTML=kpiHtml;
$('srcs').innerHTML='<thead><tr><th>模块</th><th>端口</th><th>健康</th></tr></thead><tbody>'
 +(d.sources||[]).map(s=>'<tr><td>'+esc(s.name)+'</td><td class=dim>'+s.port+'</td><td>'
  +(s.ok?'<span class=ok>● 在线</span>':'<span class=err>● 离线（看门狗 5 分钟内自动拉起）</span>')+'</td></tr>').join('')+'</tbody>';
const b=d.biz||{};
if(!$('b_tenant').value){$('b_tenant').value=b.tenant||'';$('b_plan').value=b.plan||'internal';$('b_quota').value=b.quota_daily||0;}
$('bizinfo').innerHTML='<tr><th>API Key</th><td>'+(b.apikey?'<span class=key>'+esc(b.apikey)+'</span> <span class=dim>（开放 API 接入预留，勿外传）</span>':'<span class=dim>未生成（保存套餐时自动生成）</span>')+'</td></tr>'
 +'<tr><th>开放 API（只读）</th><td><span class=key>GET /api/open/stats</span> 请求头 <span class=key>X-API-Key: &lt;上方 Key&gt;</span>，返回今日用量 / 7 天趋势 / 订阅与接收人概览</td></tr>'
 +'<tr><th>当日用量 / 配额</th><td><b>'+d.today.total+'</b> / '+(b.quota_daily>0?b.quota_daily+' 条':'不限（internal）')+(d.flow&&d.flow.today_by_kind?' <span class=dim>（流水真实发送 '+Object.values(d.flow.today_by_kind).reduce((a,c)=>a+c,0)+' 条）</span>':'')+'</td></tr>';
const fl=d.flow||{},bd=fl.by_day||[],mx=Math.max(1,...bd.map(x=>x.count));
$('trend').innerHTML=bd.map(x=>'<div class="bar" style="height:'+Math.max(3,Math.round(x.count/mx*70))+'px" title="'+esc(x.date)+'：'+x.count+' 条"><em>'+(x.count||'')+'</em><span>'+esc(x.date.slice(5))+'</span></div>').join('');
const tk=fl.today_by_kind||{};
$('flowkinds').innerHTML='<thead><tr><th>今日分类（kind）</th><th>条数</th></tr></thead><tbody>'
 +(Object.keys(tk).length?Object.keys(tk).map(k=>'<tr><td>'+esc(k)+'</td><td><b>'+tk[k]+'</b></td></tr>').join(''):'<tr><td colspan=2 class=dim>今日暂无真实发送（流水只记真实发送，跳过/熔断不记）</td></tr>')+'</tbody>';
renderFlow(d.flowmx);
renderActions(d.actmx);
$('tasks').innerHTML='<thead><tr><th>任务</th><th>时间点</th><th>状态</th><th>上次运行</th><th>结果</th><th>操作</th></tr></thead><tbody>'+
 d.tasks.map(t=>'<tr><td><b>'+esc(t.name)+'</b>'+(t.manage?'':' <span class=dim>（只读）</span>')+'</td>'
 +'<td>'+(t.manage?'<input type=time value="'+esc(t.time||'')+'" onchange="setTime(\\''+esc(t.name)+'\\',this.value)">':esc(t.time||'-'))+'</td>'
 +'<td>'+(t.state==='Ready'?'<span class=ok>就绪</span>':t.state==='Running'?'<span class=ok>运行中</span>':esc(t.state||'?'))+'</td>'
 +'<td>'+esc(t.last||'-')+'</td>'
 +'<td>'+(t.result===0||t.result==='0'?'<span class=ok>✔ 0</span>':t.result?'<span class=err>✘ '+esc(t.result)+'</span>':'-')+'</td>'
 +'<td>'+(t.manage?'<button onclick="if(confirm(\\'立即执行 '+esc(t.name)+' ？将真实推送内容到对应接收人\\'))act({action:\\'run_task\\',name:\\''+esc(t.name)+'\\'}).then(refresh)">立即执行</button>':'')+'</td></tr>').join('');
const recvNames=d.receivers.map(r=>r.name);window._recvNames=recvNames.join(',');
$('subs').innerHTML='<thead><tr><th>订阅</th><th>触发</th><th>接收人</th><th>今日</th><th>操作</th></tr></thead><tbody>'+
 d.subs.map(s=>'<tr><td><b>'+esc(s.name||s.id)+'</b>'+(s.enabled?'':' <span class=warn>停用</span>')+'<br><span class=dim style="font-size:11px">'+esc(s.note||s.id)+'</span></td>'
 +'<td>'+(s.when||[]).map(w=>'<span class=tag>'+(w==='intraday'?'盘中':'盘后')+'</span>').join('')+'</td>'
 +'<td>'+(s.receivers.length?s.receivers.map(r=>'<span class=tag'+(recvNames.includes(r)?'':' style="background:#33270e;color:var(--amber)"')+'>'+esc(r)+'</span>').join(''):'<span class=dim>缺省（个人+家人群）</span>')+'</td>'
 +'<td><b>'+s.today_sent+'</b></td>'
 +'<td><button onclick="if(confirm(\\'试推将真实发送该订阅内容到其接收人，继续？\\')){act({action:\\'test_sub\\',id:\\''+s.id+'\\'}).then(refresh)}">试推</button> '
 +'<button onclick="act({action:\\'toggle_sub\\',id:\\''+s.id+'\\'}).then(refresh)">'+(s.enabled?'停用':'启用')+'</button> '
 +'<button onclick="editRecv(\\''+s.id+'\\')">改接收人</button></td></tr>').join('');
$('recvs').innerHTML='<thead><tr><th>名称</th><th>类型</th><th>ID</th><th>备注</th><th>操作</th></tr></thead><tbody>'+
 d.receivers.map(r=>'<tr><td><b>'+esc(r.name)+'</b></td><td>'+(r.type==='chat_id'?'群':'个人')+'</td><td class=dim style="font-size:11px">'+esc(r.id)+'</td><td class=dim>'+esc(r.note||'')+'</td>'
 +'<td><button class=danger onclick="if(confirm(\\'删除接收人 '+esc(r.name)+' ？\\'))act({action:\\'remove_receiver\\',name:\\''+esc(r.name)+'\\'}).then(refresh)">删除</button></td></tr>').join('')
 ||'<tr><td colspan=5 class=dim>空</td></tr>';
const lt=d.last_test;$('tlog').textContent=lt?('最近试推：'+lt.id+' @ '+lt.ts+' 状态='+lt.status+(lt.rc!=null?(' rc='+lt.rc):'')+(lt.tail?('\\n'+lt.tail):'')+(lt.error?('\\n错误：'+lt.error):'')):'尚无试推记录';
renderShadow(d.shadow);loadGuard();}catch(e){$('sub').textContent='状态获取失败：'+e}}
async function setTime(name,v){if(!v)return msg('时间不能为空',true);const d=await act({action:'set_task_time',name:name,time:v});if(d.ok){msg('已改为 '+v+'（即时生效）');refresh()}}
async function addRecv(){const d=await act({action:'add_receiver',receiver:{name:$('r_name').value.trim(),id:$('r_id').value.trim(),type:$('r_type').value,note:$('r_note').value.trim()}});
 if(d.ok){msg('接收人已添加（可在订阅里勾选）');['r_name','r_id','r_note'].forEach(i=>$(i).value='');refresh()}}
function editRecv(sid){const v=prompt('输入接收人名称，逗号分隔（可多选）。留空=缺省（个人+家人群）：\\n可选：'+(window._recvNames||''),'');if(v===null)return;act({action:'set_sub_receivers',id:sid,receivers:v.split(',').map(s=>s.trim()).filter(Boolean)}).then(refresh);}
function show(s){document.querySelectorAll('.navi').forEach(a=>a.classList.toggle('on',a.dataset.s===s));
 document.querySelectorAll('.sec').forEach(el=>el.style.display=el.id==='sec-'+s?'':'none');
 if(s==='km'||s==='picks'){const f=$('f_'+s);if(f&&!f.src)f.src=f.dataset.src;}}
async function saveBiz(){const d=await act({action:'set_biz',tenant:$('b_tenant').value.trim(),plan:$('b_plan').value,quota_daily:parseInt($('b_quota').value||'0',10)});
 if(d.ok){msg('已保存'+(d.biz&&d.biz.apikey?'，API Key 已生成':''));refresh()}}
refresh();setInterval(refresh,5000);
</script></body></html>"""


def main():
    parser = argparse.ArgumentParser(description="推送管理中心")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--bg", action="store_true")
    args = parser.parse_args()

    if args.bg:
        # K271 死亡实验判例（2026-10-16 换机实证）：DETACHED + 文件句柄重定向
        # → server 1-2 分钟静默死（launcher 退出后继承句柄失效，缓冲 flush 炸
        # 解释器，stderr 同坏=零 traceback；watchdog 日志 10-14 起 4 分钟 4 次
        # spawn 的僵尸循环即此）。勿改回文件重定向；观测走 /health +
        # watchdog_9899.log 的 spawn 记录。
        import subprocess as sp
        sp.Popen([sys.executable, os.path.abspath(__file__), "--port", str(args.port)],
                 cwd=HERE, stdin=sp.DEVNULL, stdout=sp.DEVNULL, stderr=sp.DEVNULL,
                 creationflags=sp.DETACHED_PROCESS | sp.CREATE_NEW_PROCESS_GROUP)
        print("[ok] 已后台驻留：http://127.0.0.1:%d/" % args.port)
        return 0

    os.makedirs(WORK, exist_ok=True)
    threading.Thread(target=scheduler_loop, daemon=True).start()
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as e:
        print("[失败] 端口 %d 占用: %s" % (args.port, e), file=sys.stderr)
        return 1
    print("[ok] 推送管理中心：http://127.0.0.1:%d/" % args.port)
    srv.serve_forever()
    return 0


def scheduler_loop():
    """心跳 + 任务快照预热。"""
    while True:
        try:
            with _lock:
                _state["last_tick"] = time.time()
            tasks_snapshot()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(15)


if __name__ == "__main__":
    sys.exit(main())
