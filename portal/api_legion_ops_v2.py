# -*- coding: utf-8 -*-
"""军团作战大屏数据源 v2（K-ORG-121 L3+ · 下钻/派工/复盘三端点）

GET /api/legion/ops          编制墙+活动流+工单（v1 保持）
GET /api/legion/agent?id=X    下钻：单成员详情（档案/最近活动/名下工单/复盘）
POST /api/legion/dispatch     派工：转调 8093 task/create + 账本归因 + 影子期护栏
GET /api/legion/review?tid=X  复盘：工单执行是否正常（状态链/时间线/产出核验）
"""
import json
import time
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import hmac
import os

ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(ROOT / "tools"))
from legion_pulse import heartbeat, recent
from academy_research import create_topic, advance_topic, summary as academy_summary
from legion_tri_party_meeting import (MeetingError, create_meeting, list_meetings,
                                      load_meeting, update_confirmation)
from legion_meeting_room import (RoomError, create_room, load_room, list_rooms,
                                 post_message, board_add_card, board_update_card,
                                 board_resolve_card)
from api_legion_ops import build_roster, build_feed, build_tasks  # v3 H2 路由用
import logging

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    from portal.safe_urlopen import safe_urlopen


ORG = ROOT / "agent_platform" / "registry" / "org.json"
HR = ROOT / "agent_platform" / "hr"
WORKTREES = ROOT / ".worktrees"

DEPT_NAME = {}
# 2026-10-26 R39：token 优先读本机私有区 _vault\secrets（从 C:\Temp 迁移），
# 旧 C:\Temp 路径仅兼容回退；LEGION_TOKEN_FILE 环境变量仍最高优先。
_R39_SECRETS_DIR = r"C:\项目\_vault\secrets"
TOKEN_FILE = Path(os.environ.get("LEGION_TOKEN_FILE")
                  or next((p for p in (
                      os.path.join(_R39_SECRETS_DIR, "portal_api_token"),
                      r"C:\\Temp\\portal_api_token",
                  ) if os.path.isfile(p)),
                      os.path.join(_R39_SECRETS_DIR, "portal_api_token")))

def _load_token():
    try:
        value = TOKEN_FILE.read_text(encoding="utf-8").strip()
        return value
    except OSError:
        return os.environ.get("PORTAL_API_TOKEN", "").strip()


# 信任的门户页面来源（仅用于回显 CORS 头；8080 页面直连 8096 的降级路径）
CORS_ALLOWED_ORIGINS = ("http://localhost:8080", "http://127.0.0.1:8080")

# 本机可信域：环回 + docker bridge 网关。ai-portal nginx 在 bridge 网络，
# 本机来源经 8080 反代时 nginx 所见 remote_addr 被 SNAT 成 172.17.0.1；
# 局域网客户端经 DNAT 保留真实源 IP，不可能呈现为网关 IP，故判定安全。
# 若 docker 网络变更（网关非 172.17.0.1），此处须同步（实测校准）。
LOCAL_IPS = ("127.0.0.1", "::1", "::ffff:127.0.0.1", "172.17.0.1")


from legion_write_guard import write_context_allowed


def authz_ip_allowed(client_ip: str) -> bool:
    """纯函数：来源 IP 是否属本机可信域（可测试的最小鉴权判据）。"""
    return (client_ip or "").strip() in LOCAL_IPS


def confirmation_provenance_note(note: str, client_ip: str, via_proxy: bool, token_matched: bool) -> str:
    """纯函数：把确认署名的来源属性并入 note 落账。
    confirmed_by 是前端自报署名，不是可信身份认证；此处如实记录来源链。"""
    base = str(note or "").strip()
    tag = "[署名来源:自报|IP:%s|经反代:%s|鉴权:%s]" % (
        client_ip or "unknown",
        "是" if via_proxy else "否",
        "token" if token_matched else "本机可信域",
    )
    return (base + " " + tag).strip()[:500]


def _tasks_raw() -> dict:
    """工单真源：.worktrees/tasks.json（8093 同源）。"""
    try:
        return json.loads((WORKTREES / "tasks.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _member_map() -> dict:
    org = json.loads(ORG.read_text(encoding="utf-8"))
    m = {}
    c = org.get("chief", {})
    m[c.get("agent_id", "A0")] = {"dept": "chief", "dept_name": "参谋长", "title": "参谋长"}
    for d in org.get("departments", []):
        for aid in d.get("agents", []):
            m[aid] = {"dept": d.get("dept"), "dept_name": d.get("name", ""), "title": d.get("name", "")}
    return m


def _hr_of(aid: str) -> dict:
    """HR 档案聚合（resume 平铺 + appointment json/yaml 双兼容）。"""
    out = {}
    d = HR / aid
    if not d.exists():
        return out
    try:
        for line in (d / "resume.yaml").read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("name:"): out["name"] = s.split(":", 1)[1].strip()
            elif s.startswith("kind:"): out["kind"] = s.split(":", 1)[1].strip()
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_legion_ops_v2.py", exc)
    try:
        txt = (d / "appointment.yaml").read_text(encoding="utf-8").strip()
        if txt.startswith("{"):
            a = json.loads(txt)
        else:
            # 兼容真实 YAML 的平铺 appointment.yaml；不引入不必要的 PyYAML 依赖。
            a = {}
            for raw in txt.splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                key, value = line.split(":", 1)
                a[key.strip()] = value.strip().strip('"').strip("'")
        out.update({"rank": a.get("rank"), "title": a.get("title"), "dept": a.get("dept"),
                    "mode": a.get("mode", ""), "onboarded": a.get("onboarded", ""),
                    "endpoint": a.get("endpoint", "")})
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_legion_ops_v2.py", exc)
    return out


def _resume_editable(aid: str) -> dict:
    """读 resume.yaml 可编辑三件：经验/技能/自述。"""
    out = {"experience": "", "skills": "", "self_statement": ""}
    f = HR / aid / "resume.yaml"
    if not f.exists():
        return out
    section, buf = None, []
    lines = f.read_text(encoding="utf-8").splitlines()
    def flush(sec, rows):
        if sec == "experience" and rows:
            out["experience"] = " | ".join(r.strip("- ").strip() for r in rows)[:300]
        elif sec == "skills" and rows:
            out["skills"] = rows[0].split(":", 1)[-1].strip("[]' \"")if ":" in rows[0] else rows[0].strip()
        elif sec == "self_statement" and rows:
            out["self_statement"] = rows[0].split(":", 1)[-1].strip() if ":" in rows[0] else rows[0]
    for ln in lines:
        s = ln.strip()
        if s.startswith("experience:"):
            section = "experience"
            buf = []
        elif s.startswith("skills:"):
            section = "skills"
            buf = [s]
        elif s.startswith("self_statement:"):
            section = "self_statement"
            buf = [s]
        elif section == "experience" and s.startswith("- "):
            buf.append(s)
        elif section in ("experience", "skills", "self_statement") and s and not s.startswith("- ") and ":" in s and not s.startswith(("experience", "skills", "self_statement")):
            flush(section, buf)
            section = None
    flush(section, buf)
    if isinstance(out["skills"], list):
        out["skills"] = ", ".join(str(x) for x in out["skills"])
    return out


def agent_update(payload: dict) -> dict:
    """老田/参谋部给成员赋属性：写回 HR 档案（resume.yaml 三件）+ 心跳归因。"""
    aid = str(payload.get("agent") or "").strip()
    if not aid or not (HR / aid).exists():
        return {"ok": False, "err": f"成员 {aid} 无档案"}
    keys = ("experience", "skills", "self_statement")
    vals = {k: str(payload.get(k) or "").strip() for k in keys}
    if not any(vals.values()):
        return {"ok": False, "err": "至少填一项"}
    f = HR / aid / "resume.yaml"
    text = f.read_text(encoding="utf-8")
    import re as _re
    # skills / self_statement 原地替换；experience 追加到段尾
    if vals["skills"]:
        skills_line = "skills: [" + ", ".join(x.strip() for x in vals["skills"].replace("，", ",").split(",") if x.strip()) + "]"
        if _re.search(r"(?m)^skills:.*$", text):
            text = _re.sub(r"(?m)^skills:.*$", skills_line, text)
        else:
            text += "\n" + skills_line
    if vals["self_statement"]:
        if _re.search(r"(?m)^self_statement:.*$", text):
            text = _re.sub(r"(?m)^self_statement:.*$", vals["self_statement"], text)
        else:
            text += "\nself_statement: " + vals["self_statement"]
    if vals["experience"]:
        line = "  - {org: 老田亲授属性, role: " + vals["experience"][:80] + ", span: 2026.10~, kpi: 属性由指挥台赋予}"
        if _re.search(r"(?m)^experience:", text):
            text = _re.sub(r"(?m)^(experience:.*)$", r"\1" + line, text, count=1)
        else:
            text += "\nexperience://n" + line
    f.write_text(text, encoding="utf-8")
    heartbeat(aid, "attr_granted", "；".join(v for v in vals.values() if v)[:80], {"by": "laotian@legion-ops"})
    heartbeat("HR_ONBOARD", "attr_update", f"{aid} 属性更新")
    return {"ok": True, "agent": aid}


def agent_detail(aid: str) -> dict:
    mm = _member_map().get(aid, {})
    hr = _hr_of(aid)
    acts = [r for r in recent(60 * 24 * 14) if r.get("caller") == aid]  # 14 天
    tasks = _tasks_raw()
    mine = [{"tid": k, "desc": str(v.get("desc", ""))[:50], "status": v.get("status")}
            for k, v in tasks.items() if isinstance(v, dict) and (v.get("owner") == aid or aid in str(v.get("desc", "")))]
    ed = _resume_editable(aid)
    return {"agent_id": aid, "name": hr.get("name", aid), "dept": mm.get("dept_name") or hr.get("dept", "?"),
            "title": hr.get("title") or mm.get("title", ""), "rank": hr.get("rank", "?"),
            "kind": hr.get("kind", "staff"), "mode": hr.get("mode", ""),
            "onboarded": hr.get("onboarded", ""), "state": "active" if acts else "idle",
            "last_act": acts[-1] if acts else None,
            "acts_14d": len(acts), "recent_acts": acts[-10:][::-1],
            "editable": ed,
            "tasks": mine}


SCHED_FILE = ROOT / "agent_platform" / "data" / "schedules.json"


def schedules_op(payload: dict) -> dict:
    """排程管理（K-ORG-122）：add/list/delete。"""
    op = payload.get("op", "list")
    try:
        rows = json.loads(SCHED_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        rows = []
    if op == "list":
        return {"ok": True, "schedules": rows}
    if op == "add":
        aid = str(payload.get("agent") or "").strip()
        desc = str(payload.get("desc") or "").strip()
        cron = str(payload.get("cron") or "").strip()
        if not (aid and desc and cron):
            return {"ok": False, "err": "agent/desc/cron 必填（cron: daily 09:30 | weekly MON 09:30 | once 2026-10-20 09:00）"}
        item = {"id": f"sch-{int(time.time())}", "agent": aid, "desc": desc, "cron": cron,
                "weekdays_only": bool(payload.get("weekdays_only", True)),
                "enabled": True, "created": time.strftime("%Y-%m-%d %H:%M")}
        rows.append(item)
        SCHED_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        heartbeat("LEGION_SCHED", "add", f"{aid} {cron} {desc[:40]}")
        return {"ok": True, "schedule": item}
    if op == "delete":
        sid = str(payload.get("id") or "")
        rows = [r for r in rows if r.get("id") != sid]
        SCHED_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "deleted": sid}
    return {"ok": False, "err": f"未知 op: {op}"}


def dispatch(payload: dict) -> dict:
    """派工：8093 转调（K-ORG-112 配方：HTTP 转调+fail-visible+caller 归因）。"""
    aid = str(payload.get("agent") or "").strip()
    desc = str(payload.get("desc") or "").strip()
    if not aid or not desc:
        return {"ok": False, "err": "agent 与 desc 必填"}
    if payload.get("cron"):
        return schedules_op({"op": "add", **payload})
    # 影子期护栏（LCS-1/117 纪律）：影子成员只落模拟，不产码
    hr = _hr_of(aid)
    shadow = hr.get("mode") == "shadow"
    full = f"[{aid}] {desc}" + ("（影子期：只落盘分析，不产码不拦截）" if shadow else "")
    body = json.dumps({"desc": full, "caller": "legion-ops",
                       "agent": aid, "worker": bool(payload.get("worker", False)),
                       "acceptance": payload.get("acceptance", [])}).encode()
    try:
        req = urllib.request.Request("http://127.0.0.1:8093/api/task/create", data=body,
                                     headers={"Content-Type": "application/json"})
        with safe_urlopen(req, timeout=30) as r:
            res = json.loads(r.read())
    except Exception as e:
        heartbeat("LEGION_OPS", "dispatch_fail", f"{aid} 派工失败: {type(e).__name__}")
        return {"ok": False, "err": f"8093 不可达: {e}"}
    heartbeat("LEGION_OPS", "dispatch", f"→ {aid}: {desc[:40]}", {"to": aid})
    heartbeat(aid, "assigned", desc[:60], {"via": "legion-ops"})
    return {"ok": True, "task": res, "shadow": shadow,
            "worker": bool(payload.get("worker", False))}


def tri_party_meeting_create(payload: dict) -> dict:
    """创建三方会议；API 默认 dry-run，显式 dry_run=false 才调用模型。"""
    subject = str(payload.get("subject") or payload.get("title") or "").strip()
    if not subject:
        return {"ok": False, "err": "subject 必填"}
    try:
        result = create_meeting(
            subject,
            str(payload.get("context") or ""),
            meeting_id=payload.get("meeting_id"),
            dry_run=bool(payload.get("dry_run", True)),
            trace_id=payload.get("trace_id"),
            request_id=payload.get("request_id"),
            correlation_id=payload.get("correlation_id"),
            idempotency_key=payload.get("idempotency_key"),
            metadata=payload.get("metadata"),
        )
    except MeetingError as exc:
        return {"ok": False, "err": str(exc)}
    return {"ok": True, "meeting": result}


def tri_party_meeting_detail(meeting_id: str) -> dict:
    try:
        return {"ok": True, "meeting": load_meeting(meeting_id)}
    except MeetingError as exc:
        return {"ok": False, "err": str(exc)}


def tri_party_meeting_confirm(payload: dict, *, client_ip: str = "",
                              via_proxy: bool = False, token_matched: bool = False) -> dict:
    by = str(payload.get("confirmed_by") or "").strip()
    if not by:
        return {"ok": False, "err": "confirmed_by 必填（操作者自报署名，将随来源属性落账）"}
    note = confirmation_provenance_note(str(payload.get("note") or ""), client_ip, via_proxy, token_matched)
    try:
        result = update_confirmation(
            str(payload.get("meeting_id") or ""),
            str(payload.get("status") or ""),
            by,
            note=note,
        )
    except MeetingError as exc:
        return {"ok": False, "err": str(exc)}
    return {"ok": True, "meeting": result}


def _room_detail(room_id: str) -> dict:
    try:
        return {"ok": True, "room": load_room(room_id)}
    except RoomError as exc:
        return {"ok": False, "err": str(exc)}


def room_create(payload: dict) -> dict:
    try:
        return {"ok": True, "room": create_room(
            str(payload.get("subject") or ""),
            str(payload.get("goal") or ""),
            created_by=str(payload.get("created_by") or ""),
            room_id=payload.get("room_id"),
        )}
    except RoomError as exc:
        return {"ok": False, "err": str(exc)}


def room_post(payload: dict, *, client_ip: str = "", via_proxy: bool = False) -> dict:
    try:
        return {"ok": True, "room": post_message(
            str(payload.get("room_id") or ""),
            str(payload.get("author") or ""),
            str(payload.get("kind") or ""),
            str(payload.get("text") or ""),
            refs=payload.get("refs"),
            provenance="model" if payload.get("provenance") == "model" else "manual",
            rec_ip=client_ip, rec_via_proxy=via_proxy,
        )}
    except RoomError as exc:
        return {"ok": False, "err": str(exc)}


def room_board(payload: dict) -> dict:
    op = str(payload.get("op") or "add")
    try:
        if op == "add":
            return {"ok": True, "room": board_add_card(
                str(payload.get("room_id") or ""),
                str(payload.get("kind") or ""),
                str(payload.get("text") or ""),
                author=str(payload.get("author") or ""),
                refs=payload.get("refs"),
            )}
        if op == "update":
            return {"ok": True, "room": board_update_card(
                str(payload.get("room_id") or ""),
                str(payload.get("card_id") or ""),
                author=str(payload.get("author") or ""),
                text=payload.get("text"),
                status=payload.get("status"),
                refs=payload.get("refs"),
            )}
        if op == "resolve":
            return {"ok": True, "room": board_resolve_card(
                str(payload.get("room_id") or ""),
                str(payload.get("card_id") or ""),
                author=str(payload.get("author") or ""),
            )}
        return {"ok": False, "err": "op 必须是 add/update/resolve"}
    except RoomError as exc:
        return {"ok": False, "err": str(exc)}


def review_task(tid: str) -> dict:
    """复盘：工单执行是否正常。"""
    t = _tasks_raw().get(tid)
    if not t:
        return {"ok": False, "err": f"工单 {tid} 不存在"}
    status = t.get("status")
    created = t.get("created", "")
    # 正常性判据（v1 简明版）
    findings = []
    if status == "done":
        findings.append("✅ 已完成")
    elif status == "review":
        findings.append("🟡 待复审")
    elif status == "doing":
        try:
            h = (time.time() - time.mktime(time.strptime(created[:19], "%Y-%m-%dT%H:%M:%S"))) / 3600
            findings.append(f"🟢 进行中 {h:.1f}h" + ("（>72h 需催办）" if h > 72 else ""))
        except Exception:
            findings.append("🟢 进行中")
    else:
        findings.append(f"⚪ {status}")
    if t.get("branch"):
        findings.append(f"分支 {t['branch']}")
    if not t.get("updated") and status != "doing":
        findings.append("⚠️ 无更新时间戳")
    return {"ok": True, "tid": tid, "desc": t.get("desc", ""), "status": status,
            "owner": t.get("owner", ""), "created": created, "updated": t.get("updated", ""),
            "branch": t.get("branch", ""), "worktree": t.get("worktree", ""),
            "findings": findings}


class H(BaseHTTPRequestHandler):
    def _via_proxy(self):
        """是否经本门户 nginx 反代：nginx 统一注入 X-Portal-Proxy: 1（客户端不可伪造）。"""
        return (self.headers.get("X-Portal-Proxy") or "").strip() == "1"

    def _client_ip(self):
        """采信规则（对齐 api_common P0-1b 模型）：仅当请求确经本门户 nginx 反代时
        才采信 X-Real-IP（nginx 覆写为真实 remote_addr）；直连只认 TCP 对端地址，
        不采信客户端自报头。修复 P0：原实现无条件信任 X-Real-IP，可自报 127.0.0.1 越权。"""
        if self._via_proxy():
            return (self.headers.get("X-Real-IP") or self.client_address[0] or "").strip()
        return (self.client_address[0] or "").strip()

    def _token_ok(self):
        token = _load_token()
        if not token:
            return False
        got = (self.headers.get("X-Portal-Token") or "").strip()
        return bool(got) and hmac.compare_digest(got, token)

    def _authorized_mutation(self):
        """写鉴权：先校验浏览器写上下文（跨站 Origin/Referer 拒绝，防 CSRF），
        再按本机可信域/token 判定。上下文守卫内联，不新增 Handler 方法（只改不扩）。"""
        if not write_context_allowed(
                self.headers.get("Origin") or "",
                self.headers.get("Sec-Fetch-Site") or "",
                self.headers.get("Referer") or ""):
            return False
        return authz_ip_allowed(self._client_ip()) or self._token_ok()

    def _authorized_private_read(self):
        """私人数据（三方会议记录）读鉴权：规则同写鉴权。
        修复 P0：会议列表/详情此前无任何鉴权即可读。"""
        return self._authorized_mutation()

    def _cors_origin(self):
        """修复 P0：Access-Control-Allow-Origin 不再通配 *。
        仅显式回显门户白名单来源（legion.html 经 /legion-api/ 同源调用本不需要 CORS；
        直连 8096 的降级路径来自 8080 页面）。其余来源不下发 CORS 头。"""
        origin = (self.headers.get("Origin") or "").strip()
        if origin in CORS_ALLOWED_ORIGINS:
            return origin
        return None

    def _send(self, obj, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        cors = self._cors_origin()
        if cors:
            self.send_header("Access-Control-Allow-Origin", cors)
        self.end_headers()
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/api/legion/agent":
            self._send(agent_detail((q.get("id") or ["?"])[0]))
        elif u.path == "/api/legion/review":
            self._send(review_task((q.get("tid") or [""])[0]))
        else:
            self._send({"ts": time.strftime("%Y-%m-%d %H:%M:%S")})

    def do_POST(self):
        if not self._authorized_mutation():
            self._send({"ok": False, "err": "mutation unauthorized"}, 403)
            return
        if self.path == "/api/legion/schedules":
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
                self._send(schedules_op(body))
            except Exception as e:
                self._send({"ok": False, "err": str(e)}, 400)
        elif self.path == "/api/legion/agent/update":
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
                self._send(agent_update(body))
            except Exception as e:
                self._send({"ok": False, "err": str(e)}, 400)
        elif self.path == "/api/legion/dispatch":
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
                self._send(dispatch(body))
            except Exception as e:
                self._send({"ok": False, "err": str(e)}, 400)
        elif self.path == "/api/legion/academy/topics":
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
                if body.get("op") == "advance":
                    result = advance_topic(body.get("topic_id", ""), body.get("stage", ""), body.get("by", "ACAD1"), body.get("note", ""), body.get("decision", ""))
                else:
                    result = create_topic(body)
                self._send(result, 200 if result.get("ok") else 400)
            except Exception as e:
                self._send({"ok": False, "err": str(e)}, 400)
        elif self.path == "/api/legion/tri-party/meeting":
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
                self._send(tri_party_meeting_create(body))
            except Exception as e:
                self._send({"ok": False, "err": str(e)}, 400)
        elif self.path == "/api/legion/tri-party/confirm":
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
                self._send(tri_party_meeting_confirm(
                    body,
                    client_ip=self._client_ip(),
                    via_proxy=self._via_proxy(),
                    token_matched=self._token_ok(),
                ))
            except Exception as e:
                self._send({"ok": False, "err": str(e)}, 400)
        elif self.path == "/api/legion/room/create":
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
                self._send(room_create(body))
            except Exception as e:
                self._send({"ok": False, "err": str(e)}, 400)
        elif self.path == "/api/legion/room/post":
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
                self._send(room_post(body, client_ip=self._client_ip(), via_proxy=self._via_proxy()))
            except Exception as e:
                self._send({"ok": False, "err": str(e)}, 400)
        elif self.path == "/api/legion/room/board":
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
                self._send(room_board(body))
            except Exception as e:
                self._send({"ok": False, "err": str(e)}, 400)
        else:
            self._send({"err": "not found"}, 404)

    def log_message(self, *a):
        pass


class H2(H):
    """v3 主服务类：页面 + ops/agent/review/reviews/dispatch/tri-party 同 8096。
    定义于模块级以便测试可在临时端口拉起同一套真实路由。"""

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path in ("/", "/legion.html"):
            try:
                html = (ROOT / "portal" / "legion.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(html)
            except Exception as e:
                self._send({"err": str(e)}, 500)
            return
        if u.path == "/api/legion/reviews/today":
            base = ROOT / "agent_platform" / "data" / "daily_review"
            day_dir = None
            for d in sorted(base.glob("2*"), reverse=True):
                if (d / "_summary.json").exists():
                    day_dir = d
                    break
            reviews = {}
            if day_dir:
                for f in day_dir.glob("*.md"):
                    aid = f.stem
                    q1 = q2 = q3 = "—"
                    for ln in f.read_text(encoding="utf-8").splitlines():
                        if "①" in ln:
                            q1 = ln.split("：", 1)[-1].strip()
                        elif "②" in ln:
                            q2 = ln.split("：", 1)[-1].strip()
                        elif "③" in ln:
                            q3 = ln.split("：", 1)[-1].strip()
                    reviews[aid] = {"agent_id": aid, "q1": q1, "q2": q2, "q3": q3, "has_task": "✅" in q1}
            self._send({"day": day_dir.name if day_dir else None, "reviews": reviews})
            return
        if u.path == "/api/legion/academy/topics":
            self._send(academy_summary())
            return
        if u.path == "/api/legion/ops":
            roster = build_roster()
            org = json.loads((ROOT / "agent_platform" / "registry" / "org.json").read_text(encoding="utf-8"))
            academy = next((d for d in org.get("departments", []) if d.get("dept") == "academy"), None)
            self._send({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "roster": roster, "feed": build_feed()[-40:],
                        "tasks": build_tasks(),
                        "academy": academy or {},
                        "academy_language": (academy or {}).get("research_language", {}),
                        "academy_directions": (academy or {}).get("directions", [])})
        elif u.path == "/api/legion/agent":
            self._send(agent_detail((q.get("id") or ["?"])[0]))
        elif u.path == "/api/legion/schedules":
            self._send(schedules_op({"op": "list"}))
        elif u.path == "/api/legion/review":
            self._send(review_task((q.get("tid") or [""])[0]))
        elif u.path == "/api/legion/tri-party/meetings":
            if not self._authorized_private_read():
                self._send({"ok": False, "err": "read unauthorized"}, 403)
                return
            self._send({"ok": True, "meetings": list_meetings()})
        elif u.path == "/api/legion/tri-party/meeting":
            if not self._authorized_private_read():
                self._send({"ok": False, "err": "read unauthorized"}, 403)
                return
            self._send(tri_party_meeting_detail((q.get("meeting_id") or [""])[0]))
        elif u.path == "/api/legion/room/list":
            if not self._authorized_private_read():
                self._send({"ok": False, "err": "read unauthorized"}, 403)
                return
            self._send({"ok": True, "rooms": list_rooms()})
        elif u.path == "/api/legion/room":
            if not self._authorized_private_read():
                self._send({"ok": False, "err": "read unauthorized"}, 403)
                return
            self._send(_room_detail((q.get("room_id") or [""])[0]))
        else:
            self._send({"err": "not found"}, 404)


if __name__ == "__main__":
    # v3 主入口：页面 + ops/agent/review/reviews/dispatch 五端点同 8096
    print("legion ops api v3 :8096 (page/ops/agent/review/reviews/dispatch)")
    srv = ThreadingHTTPServer(("127.0.0.1", 8096), H2)
    srv.serve_forever()
