"""Dependency-free HTTP read-only BFF."""
from __future__ import annotations
import json
import uuid
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse
from .repository import RegistryDataError, RegistryReader

def _health_snapshot(value):
    """Normalize probe output while preserving legacy fields."""
    if not isinstance(value, dict):
        return {"state": "unknown", "endpoint": None, "reachable": None, "latency_ms": None, "checked_at": None, "last_heartbeat_at": None, "detail": None, "error": {"kind": "invalid_payload", "message": "Health payload is not an object"}, "source": "unavailable", "quality": "probe_failed"}
    snapshot = {key: value.get(key) for key in ("state", "endpoint", "reachable", "latency_ms", "checked_at", "last_heartbeat_at", "detail", "error", "source", "quality")}
    snapshot.setdefault("last_heartbeat_at", None)
    return snapshot


class AdminRequestHandler(BaseHTTPRequestHandler):
    reader = RegistryReader()
    server_version = "LegionAdminReadOnly/1.0"
    def _send(self, status, code, data=None, message="", source="agent_platform"):
        observed_at = self.reader._now()
        payload = {"code": code, "data": data, "message": message, "request_id": uuid.uuid4().hex, "generated_at": observed_at, "observed_at": observed_at, "source": source}
        body = json.dumps(payload, ensure_ascii=False, default=lambda value: value.isoformat() if isinstance(value, (date, datetime)) else str(value)).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        parsed = urlparse(self.path); path = unquote(parsed.path.rstrip("/")); query = parse_qs(parsed.query)
        try:
            if path in ("/api/v1/org/tree", "/api/admin/org/tree"):
                include = query.get("include_health", ["false"])[0].lower() == "true"
                tree = self.reader.organization_tree(include, query.get("q", [None])[0])
                is_partial = tree.get("source", {}).get("quality") == "partial"
                self._send(206 if is_partial else 200, "PARTIAL" if is_partial else "OK", tree, source=tree.get("source", {}).get("registry", "registry")); return
            if path == "/api/v1/health/summary":
                tree = self.reader.organization_tree(True)
                states = [node.get("live_state", "unknown") for dept in tree["children"] for node in dept.get("children", [])]
                partial = tree.get("source", {}).get("quality") == "partial"
                summary = {"agents": len(states), "online": states.count("online"), "offline": states.count("offline"), "degraded": states.count("degraded"), "unknown": states.count("unknown"), "source": "agent_health", "quality": "partial" if partial else "live", "health_snapshot": {"state_counts": {"online": states.count("online"), "offline": states.count("offline"), "degraded": states.count("degraded"), "unknown": states.count("unknown")}, "source": "agent_health", "quality": "partial" if partial else "live"}}
                self._send(206 if partial else 200, "PARTIAL" if partial else "OK", summary, source="agent_health"); return
            prefix = "/api/v1/agents/" if path.startswith("/api/v1/agents/") else "/api/admin/agents/"
            if path.startswith(prefix):
                parts = [x for x in path[len(prefix):].split("/") if x]
                if len(parts) == 1: self._send(200, "OK", self.reader.agent_detail(parts[0])); return
                if len(parts) == 2 and parts[1] == "health": self._send(200, "OK", _health_snapshot(self.reader.health(parts[0]))); return
            if path.startswith("/api/v1/departments/"):
                dept_id = path.split("/")[-1]; tree = self.reader.organization_tree(False); found = next((d for d in tree["children"] if d["dept_id"] == dept_id), None)
                if found is None: self._send(404, "NOT_FOUND", None, "Department does not exist")
                else: self._send(200, "OK", found)
                return
            self._send(404, "NOT_FOUND", None, "Resource does not exist")
        except KeyError: self._send(404, "NOT_FOUND", None, "Agent does not exist")
        except ValueError as exc: self._send(400, "INVALID_ARGUMENT", None, str(exc))
        except RegistryDataError as exc: self._send(502, "SOURCE_INVALID", None, str(exc), source="registry/hr")
        except Exception: self._send(500, "API_UNAVAILABLE", None, "Unable to read admin resource")
    def _readonly(self): self._send(405, "READ_ONLY", None, "This service exposes read-only resources")
    do_POST = _readonly; do_PUT = _readonly; do_PATCH = _readonly; do_DELETE = _readonly
    def log_message(self, format, *args): return

def create_server(host="127.0.0.1", port=8088, reader=None):
    if reader is not None: AdminRequestHandler.reader = reader
    return ThreadingHTTPServer((host, port), AdminRequestHandler)

if __name__ == "__main__": create_server().serve_forever()
