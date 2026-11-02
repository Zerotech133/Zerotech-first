# -*- coding: utf-8 -*-
"""军团作战大屏数据源（K-ORG-121 L3）：GET /api/legion/ops
三源合一：编制墙(org.json+hr) + 实时活动(llm_tool_audit) + 工单(tasks.json)
挂载：portal/api_legion_ops.py → nginx 反代 /api/legion/ops
"""
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys_path = ROOT / "tools"
import sys
sys.path.insert(0, str(sys_path))
from legion_pulse import recent
import logging

ORG = ROOT / "agent_platform" / "registry" / "org.json"
HR = ROOT / "agent_platform" / "hr"
TASKS = ROOT / ".worktrees" / "tasks.json"


def build_roster() -> dict:
    org = json.loads(ORG.read_text(encoding="utf-8"))
    acts = {}
    for r in recent(60 * 48):  # 48h 活动聚合
        acts[r.get("caller", "?")] = r.get("ts", "")
    now = time.mktime(time.localtime())
    members = []
    def state_of(agent_id, last_ts):
        if not last_ts:
            return "idle"          # 无心跳=待命
        try:
            t = time.mktime(time.strptime(last_ts, "%Y-%m-%dT%H:%M:%S"))
            return "active" if now - t < 3600 else ("warm" if now - t < 86400 else "idle")
        except Exception:
            return "idle"
    # chief
    c = org.get("chief", {})
    members.append({"id": c.get("agent_id", "A0"), "name": c.get("name", "田丰"), "dept": "chief",
                    "title": "参谋长", "state": state_of("A0", acts.get("A0"))})
    for d in org.get("departments", []):
        for aid in d.get("agents", []):
            # 名字从 HR 档案
            nm = aid
            try:
                for line in (HR / aid / "resume.yaml").read_text(encoding="utf-8").splitlines():
                    if line.startswith("cn_name:"):
                        nm = line.split(":", 1)[1].strip()
                        break
                    if line.startswith("name:"):
                        nm = line.split(":", 1)[1].strip()
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "portal/api_legion_ops.py", exc)
            members.append({"id": aid, "name": nm, "dept": d.get("dept", "?"),
                            "title": d.get("name", ""), "state": state_of(aid, acts.get(aid))})
    # v4: 组织架构树（K-ORG-123）
    org_raw = json.loads(ORG.read_text(encoding="utf-8"))
    tree = {"name": org_raw.get("chief", {}).get("name", "田丰"), "id": "A0", "dept": "参谋长", "children": []}
    for d in org_raw.get("departments", []):
        lead = d.get("lead") or "—"
        node = {"name": d.get("name", d.get("dept")), "id": lead, "dept": d.get("dept"), "lead": lead,
                "n": len(d.get("agents", [])), "children": [
                    {"name": next((m["name"] for m in members if m["id"] == a), a), "id": a, "dept": d.get("dept"), "state": next((m["state"] for m in members if m["id"] == a), "idle")}
                    for a in d.get("agents", [])]}
        tree["children"].append(node)
    return {"members": members, "n": len(members),
            "n_active": sum(1 for m in members if m["state"] == "active"),
            "tree": tree}


def build_feed() -> list:
    return recent(60 * 6)[::-1]  # 近6h，新在后


CORS_ALLOWED_ORIGINS = (
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "http://127.0.0.1:8734",
)


def cors_origin(origin: str | None) -> str | None:
    """仅回显白名单来源；陌生来源不下发 CORS 头（UF-12：通配 * 下线）。"""
    return origin if origin in CORS_ALLOWED_ORIGINS else None


def build_tasks() -> list:
    try:
        t = json.loads(TASKS.read_text(encoding="utf-8"))
        out = []
        for k, v in t.items():
            if isinstance(v, dict) and v.get("status") in ("doing", "review"):
                out.append({"id": k, "desc": str(v.get("desc", ""))[:40], "status": v.get("status")})
        return out
    except Exception:
        return []


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "roster": build_roster(), "feed": build_feed()[-40:],
                              "tasks": build_tasks()}, ensure_ascii=False)
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        origin = cors_origin(self.headers.get("Origin"))
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(payload.encode("utf-8"))

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", 8096), H)
    print("legion ops api :8096")
    srv.serve_forever()
