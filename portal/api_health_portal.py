# -*- coding: utf-8 -*-
"""门户健康/观测族 Mixin 与 helper（A5 B2，2026-10-31 关羽）。

纯移动自 portal_api.py：
- Handler 方法：_sys_health / _api_ctl_alert
- 模块函数：_sub_status_view / _sub_status_key_ok / _freshness_warmup / mask_open_id
（_sub_status_* / _freshness_warmup 由 portal_api 重新 import，既有调用/测试不变）

经 portal_api 基座多继承并入。
"""
import hmac
import json
import logging
import os
import re
import threading
import time

from api_common import ROOT, STATION, data_freshness_bg, pill_nav

try:  # 与 portal_api 原样一致的导入兜底（包内/脚本两种入口）
    from safe_urlopen import safe_urlopen
except ImportError:  # pragma: no cover
    from portal.safe_urlopen import safe_urlopen



# ── 健康/观测共享状态（B2b 从 portal_api 移入；_lock 与 portal_api 共享同一对象）──
_lock = threading.Lock()
_hcache = {"t": 0.0, "data": None}
_jcache = {"t": 0.0, "data": None}
_JEV_PORTS = [("gate", 9101), ("review", 9102), ("qd bridge", 9103),
              ("card", 9104)]
_JEV_LOG = os.path.join(ROOT, "..", "jev-eval", "logs", "jev-stack.log")
_JEV_DLQ = os.path.join(ROOT, "..", "jev-eval",
                        "push_dead_letter.jsonl")


def _probe_service(spec):
    """探测单个服务；spec=(name, port, http_path[, optional[, state]])。

    2026-11-02：已退役服务（state="retired"，如 W3 退役的 9104 card）直接返回
    ok=True/optional=True，不建 TCP（原先 2s 超时拖慢 /api/health 冷请求）。
    """
    name, port, http_path = spec[:3]
    optional = bool(spec[3]) if len(spec) > 3 else False
    state = spec[4] if len(spec) > 4 else ""
    if state == "retired":
        return {"name": name, "port": port, "ok": True, "optional": True,
                "state": "retired", "ms": 0}
    import socket as _sk
    import time as _time
    t0 = _time.time()
    try:
        if http_path:
            from urllib.request import urlopen
            ok = urlopen("http://127.0.0.1:%d%s" % (port, http_path), timeout=2.5).status == 200
        else:
            s = _sk.create_connection(("127.0.0.1", port), timeout=2)
            s.close()
            ok = True
    except Exception:
        ok = False
    return {"name": name, "port": port, "ok": ok, "optional": optional,
            "ms": int((_time.time() - t0) * 1000)}


class Handler:  # Mixin 壳：经基座多继承并入

    def _sys_health(self):
        """系统自检页：服务实时探活 + ops/health_latest.json 夜间快照。"""
        import html as _html
        import socket as _sk

        def http_ok(url):
            try:
                return safe_urlopen(url, timeout=2.5).status == 200
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
                 ("智策API(8105)", lambda: http_ok(
                     "http://127.0.0.1:8105/api/v1/health")),
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
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal_api.py", exc)
        blocks = ["<h3 style='color:#8b949e;font-size:13px'>实时探活"
                  "（打开本页即测）</h3>" + rows]
        if snap.get("deprecated"):
            # R2 收口（2026-10-18）：health_latest.json 已退役为弃用标记，
            # 不再渲染冻结的陈旧快照，明示后继数据源（执行健康总线）。
            blocks.append(
                "<h3 style='color:#8b949e;font-size:13px;margin-top:18px'>"
                "自检快照已退役（%s）</h3>"
                "<div style='background:#161b22;border:1px solid #21262d;"
                "border-radius:6px;padding:12px;font-size:13px;"
                "line-height:2'>陈旧第二本账已收口（K-STOCK-349）。"
                "计划任务执行面健康请读执行健康总线：<br>"
                "<span style='color:#58a6ff'>%s</span></div>"
                % (_html.escape(str(snap.get("deprecated_at", "—"))),
                   _html.escape(str(snap.get("successor",
                       "GET http://127.0.0.1:8091/api/execution/health")))))
        elif snap:
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
                          "margin-top:16px'>暂无自检快照（快照链已退役，"
                          "执行面健康读 GET /api/execution/health）"
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

    def _api_ctl_alert(self, raw):
        """K-ORG-113 Hermes alerts 连接器写面（body JSON 转调）。

        body: {action: ack|ignore|ack_all, alert_id?, caller?}
        业务复用 feishu_control.ctl_http → handle_ctl 的 ctl:alert_*
        分支——与飞书控制卡按钮同一条代码路径、同一落点
        （v2_alert + gate_ignored.json + control_ops.jsonl）。
        本机放行/局域网 X-Portal-Token（_MUTATION_POSTS 闸门）。
        """
        try:
            rec = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            self._json({"ok": False, "error": "bad json"}, 400)
            return
        action = str(rec.get("action") or "").strip()
        alert_id = str(rec.get("alert_id") or "").strip()
        operator = str(rec.get("caller") or "").strip() or "hermes-console"
        if action not in ("ack", "ignore", "ack_all"):
            self._json({"ok": False,
                        "error": "action 非法（ack|ignore|ack_all）"}, 400)
            return
        if action in ("ack", "ignore") and not alert_id:
            self._json({"ok": False, "error": "alert_id is required"}, 400)
            return
        import feishu_control
        ok, text = feishu_control.ctl_http(action, alert_id, operator)
        self._json({"ok": ok, "text": text, "operator": operator}, 200 if ok else 400)


    def _api_health(self):
        """P1 总览门户健康聚合（JSON）：七口探活（端口判活=判例3 正源）
        + PID 侧写（netstat，S4U 下 Win32_Process 不可信）+ 五守护任务
        schtasks 状态。60s TTL 缓存，探测全部线程池并发。"""
        import socket as _sk
        import subprocess as _sp
        from concurrent.futures import ThreadPoolExecutor

        with _lock:
            if (_hcache["data"] is not None
                    and time.time() - _hcache["t"] <= 60):
                self._json(_hcache["data"])
                return

        def probe(spec):
            return _probe_service(spec)

        # W3 清债（2026-10-25）：9104 card 已按 W3 回执退役（按需拉起，
        # OPTIONAL_OFFLINE）；不再作为整体健康阻断，仅保留观测位。
        _spec = [("portal api", 8091, "/health"),
                 ("push hub", 8097, None),
                 ("hermes", 8787, None),
                 ("gate", 9101, None),
                 ("review", 9102, None),
                 ("qd bridge", 9103, None),
                 ("card", 9104, None, True, "retired")]
        with ThreadPoolExecutor(max_workers=7) as _ex:
            services = list(_ex.map(probe, _spec))

        # PID 侧写：一次 netstat 全量滤七口（judge 判例3：S4U 会话
        # Win32_Process born/CommandLine 查询 DENIED，端口侧写唯一可信）
        # ⚠️ Hidden 会话链路判例：subprocess.run+text 对 netstat 偶发
        # stdout=None（TypeError 被吞）；Popen+bytes+容错解码稳定。
        def _run_bytes(argv):
            try:
                p = _sp.Popen(argv, stdout=_sp.PIPE, stderr=_sp.PIPE)
                so, _ = p.communicate(timeout=10)
                # 中文系统 netstat/schtasks 输出为 OEM 936(GBK)；
                # utf-8 解码会毁掉中文列名（「上次结果」→乱码→pick 失配）
                return (so or b"").decode("gbk", "replace")
            except Exception:
                return ""

        pid_by_port = {}
        for ln in _run_bytes(["netstat", "-ano", "-p", "tcp"]).splitlines():
            m = re.match(r"\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)",
                         ln)
            if m:
                pid_by_port.setdefault(int(m.group(1)),
                                       int(m.group(2)))
        for s in services:
            s["pid"] = pid_by_port.get(s["port"])

        # 五守护任务单查并行（csv /v 单任务输出小；中文系统列名本地化，
        # 用子串双名兼容，拿不到的字段置 "?" 不阻塞主流程）
        _TASKS = ["JevStack", "JevBackfill", "JevFeed-Daily",
                  "TushareSync", "HermesWebUI-Guard"]

        def task_one(tn):
            info = {"name": tn, "state": "?", "last_result": "?",
                    "last_run": "?", "next_run": "?"}
            try:
                import csv as _csv
                import io as _io
                out = _run_bytes(["schtasks", "/query", "/tn", "\\" + tn,
                                  "/fo", "csv", "/v"])
                lines = [x for x in out.splitlines() if x.strip()]
                if len(lines) >= 2:
                    rows = list(_csv.reader(_io.StringIO("\n".join(lines))))
                    d = dict(zip(rows[0], rows[1]))

                    def pick(*keys):
                        for k in keys:
                            for hk in d:
                                if k in hk:
                                    return d[hk]
                        return "?"

                    # schtasks /v 双语列名（服务会话中文/交互会话英文，
                    # 实测抓取 2026-10-17）：Status=「模式」(值:就绪)；
                    # 「登录状态」=Logon Mode、「计划任务状态」=Enabled——
                    # 勿用「状态」子串（撞「登录状态」→显示交互方式/后台方式）
                    info.update({
                        "state": pick("Status", "模式"),
                        "last_result": pick("Last Result", "上次结果"),
                        "last_run": pick("Last Run Time", "上次运行时间"),
                        "next_run": pick("Next Run Time", "下次运行时间"),
                    })
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "portal_api.py", exc)
            return info

        with ThreadPoolExecutor(max_workers=5) as _ex:
            tasks = list(_ex.map(task_one, _TASKS))

        data = {"ok": all(s["ok"] or s.get("optional") for s in services),
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "services": services, "tasks": tasks}
        with _lock:
            _hcache["t"] = time.time()
            _hcache["data"] = data
        self._json(data)

    def _api_jev_stack(self):
        """P2 四件套观测页聚合（JSON）：四服务探活+PID+业务指标
        （/metrics 解析 jev_db_queries/jev_calls + created 算存活）、
        jev-stack.log 换血时间轴（尾 40 行）、死信队列统计+最近样本。
        30s TTL。"""
        import socket as _sk
        from concurrent.futures import ThreadPoolExecutor

        with _lock:
            if (_jcache["data"] is not None
                    and time.time() - _jcache["t"] <= 30):
                self._json(_jcache["data"])
                return

        def _sub_out(argv):
            import subprocess as _sp
            try:
                p = _sp.Popen(argv, stdout=_sp.PIPE, stderr=_sp.PIPE)
                so, _ = p.communicate(timeout=10)
                return (so or b"").decode("gbk", "replace")
            except Exception:
                return ""

        # PID 侧写（判例3：端口判活唯一可信）
        pid_by_port = {}
        for ln in _sub_out(["netstat", "-ano", "-p", "tcp"]).splitlines():
            m = re.match(r"\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)",
                         ln)
            if m:
                pid_by_port.setdefault(int(m.group(1)),
                                       int(m.group(2)))

        def probe(svc):
            name, port = svc
            out = {"name": name, "port": port, "up": False,
                   "pid": pid_by_port.get(port), "metrics": {}}
            try:
                s = _sk.create_connection(("127.0.0.1", port), timeout=2)
                s.close()
                out["up"] = True
            except Exception:
                return out
            try:
                body = safe_urlopen("http://127.0.0.1:%d/metrics" % port,
                                  timeout=4).read().decode("utf-8",
                                                           "replace")
                met = out["metrics"]
                for ln in body.splitlines():
                    m2 = re.match(r'^(jev_db_queries_total|jev_calls_total)'
                                  r'\{[^}]*ok="(\d)"\}\s+([\d.eE+]+)', ln)
                    if m2:
                        key = ("db_ok" if m2.group(1).startswith("jev_db")
                               else "call_ok") if m2.group(2) == "1" else (
                          "db_err" if m2.group(1).startswith("jev_db")
                          else "call_err")
                        met[key] = float(m2.group(3))
                    m3 = re.match(r'^jev_db_queries_created\{[^}]*ok="1"\}'
                                  r'\s+([\d.eE+]+)', ln)
                    if m3:
                        # created 时间戳 = 进程业务起点 → 存活时长
                        alive = time.time() - float(m3.group(1))
                        if alive > 0:
                            met["alive_s"] = int(alive)
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "portal_api.py", exc)
            return out

        with ThreadPoolExecutor(max_workers=4) as _ex:
            services = list(_ex.map(probe, _JEV_PORTS))

        # 换血时间轴（尾 40 行）
        restarts = []
        try:
            with open(_JEV_LOG, encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-40:]
            for ln in tail:
                m4 = re.match(r"\[([\d\- :]+)\]\s+(force-restart:.+|"
                              r"pulled up:.+)", ln.strip())
                if m4:
                    kind = ("blood" if "force-restart" in m4.group(2)
                            else "pull")
                    restarts.append({"ts": m4.group(1), "kind": kind,
                                     "detail": m4.group(2)})
            restarts.reverse()  # 最新在前
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal_api.py", exc)

        # 死信队列：总数 + 最近 8 条（err 截断，防大 JSON）
        dlq = {"count": 0, "recent": []}
        try:
            with open(_JEV_DLQ, encoding="utf-8", errors="replace") as f:
                lines = [x for x in f if x.strip()]
            dlq["count"] = len(lines)
            for ln in lines[-8:]:
                try:
                    d = json.loads(ln)
                    dlq["recent"].append({
                        "ts": d.get("ts", ""),
                        "level": d.get("level", ""),
                        "title": d.get("title", "")[:40],
                        "urgent_p": (d.get("verdict") or {}
                                     ).get("urgent_p"),
                        "reason": d.get("reason", ""),
                        "final": d.get("final", False),
                        "err": (d.get("err") or "")[:120]})
                except Exception as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "portal_api.py", exc)
            dlq["recent"].reverse()
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal_api.py", exc)

        data = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "services": services, "restarts": restarts, "dlq": dlq}
        with _lock:
            _jcache["t"] = time.time()
            _jcache["data"] = data
        self._json(data)

def _freshness_warmup():
    """K56-B1 启动预热：后台线程算一次 freshness（冷算 ~100s），
    驾驶舱/数据仓首个访问者不再等冷启动。"""
    data_freshness_bg(ttl_sec=1800)

def _sub_status_key_ok(got_key):
    """可选 key 闸判定：PORTAL_SUB_STATUS_KEY 未配置/空串 → 不设闸（True，
    行为与加固前一致，既有调用方零破坏）；配置后要求请求头 X-Portal-Key
    常量相等（缺失/不等 → False → 401）。"""
    want = (os.environ.get("PORTAL_SUB_STATUS_KEY") or "").strip()
    if not want:
        return True
    got = (got_key or "").strip()
    if not got:
        return False
    return hmac.compare_digest(got.encode("utf-8"), want.encode("utf-8"))

def _sub_status_view(summary):
    """把 subscription_store 摘要压成客户面白名单视图（字段最小化）。

    - 只回当前档位（优先覆盖中的 active 行，否则按 end 降序首行）的
      plan_id/plan_name/start/end/status，以及本人当日额度余量
      （today_used_by_prefix + 仅本人档位的 daily_quota_by_plan）。
    - open_id 只回掩码，禁止明文；display_name、内部 reason、
      plan 目录全表、生成时点、任何金额类字段一律不透出。
    - 上游 error 归并为 {"error": "store_unavailable"}（异常细节不外泄）。
    """
    if not isinstance(summary, dict) or summary.get("error"):
        return {"error": "store_unavailable"}
    subs = [r for r in (summary.get("subscriptions") or [])
            if isinstance(r, dict)]
    cur = next((r for r in subs if r.get("status") == "active"), None)
    if cur is None and subs:
        cur = subs[0]
    cur = cur or {}
    pid = str(cur.get("plan_id") or "")
    names = summary.get("plan_name_by_id") or {}
    quotas = summary.get("daily_quota_by_plan") or {}
    used = summary.get("today_used_by_prefix") or {}
    view = {
        "ok": True,
        "open_id": mask_open_id(summary.get("open_id")),
        "plan_id": pid,
        "plan_name": str(names.get(pid) or "") if isinstance(names, dict) else "",
        "start": str(cur.get("start") or ""),
        "end": str(cur.get("end") or ""),
        "status": str(cur.get("status") or ""),
        "today_used_by_prefix": dict(used) if isinstance(used, dict) else {},
        "daily_quota_by_plan": ({pid: quotas[pid]}
                                if pid and isinstance(quotas, dict)
                                and pid in quotas else {}),
    }
    return view

def mask_open_id(open_id):
    """open_id 掩码纯函数：保留前 8 位 + "**"；过短 id 降级前 4/前 2 位；
    空入参回 "**"（与 feishu_bot_ws.mask_open_id 同规则）。"""
    s = str(open_id or "")
    if len(s) > 8:
        return s[:8] + "**"
    if len(s) > 4:
        return s[:4] + "**"
    if s:
        return s[:2] + "**"
    return "**"
