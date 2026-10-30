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

from api_common import STATION, data_freshness_bg, pill_nav

try:  # 与 portal_api 原样一致的导入兜底（包内/脚本两种入口）
    from safe_urlopen import safe_urlopen
except ImportError:  # pragma: no cover
    from portal.safe_urlopen import safe_urlopen


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

