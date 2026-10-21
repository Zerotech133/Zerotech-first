# -*- coding: utf-8 -*-
"""军团指挥界面套件 HTTP 服务（WO-LEGION-S22）。

端口 8796（独立于 8795 门面）。端点（全部只读 GET，除会终指令）：
  GET /api/snapshot        —— org/hr/tasks/schedules 聚合快照（沙盘数据源）
  GET /api/health          —— 服务与健康
  GET /                    —— 指挥界面（index.html，沙盘+会议室+工作台三视图）
  POST /api/dispatch       —— 会终指令：写军团门面 inbox（走 S21 投递协议）

纪律：快照端点只读；dispatch 是唯一写路径且只写 bridge_inbox inbox/
（田丰登记去重的既有机制），不直改 org/tasks/任何生产状态。
"""

from __future__ import annotations

import argparse
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

_PLATFORM = Path(__file__).resolve().parents[1]
if str(_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_PLATFORM))

from gateway import inbox_gateway, llm_gateway  # noqa: E402  S21 门面复用
from legion_command.snapshot import build_snapshot  # noqa: E402

_STATIC = Path(__file__).resolve().parent / "static"
_KIND_MAP = {"work": "task_request", "trade": "task_request", "study": "question"}

# 参会席位 → 模型绑定（10-51 异族裁决 + org.json 主事实源；未列出席位默认本地 9b）
AGENT_BINDING = {
    "A0": "glm/glm-5.3",
    "AI1": "openai/gpt-6.0",
    "R2": "ollama/qwen3.5:9b",
    "OPT1": "glm/glm-4.6",
    "ST1": "glm/glm-5.3",
    "ARCH1": "glm/glm-5.3",
    "SEC2": "glm/glm-5.3",
    "LEG1": "glm/glm-5.3",
    "DEV2": "ollama/qwen3.6:35b-a3b",
}
_DEFAULT_BINDING = "ollama/qwen3.5:9b"


class Handler(BaseHTTPRequestHandler):
    server_version = "legion-command/1.0"

    def _send_json(self, obj, status=200):
        import json
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, ctype: str):
        if not path.exists():
            self._send_json({"ok": False, "error": "not_found"}, 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        import re
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            self._send_file(_STATIC / "index.html", "text/html; charset=utf-8")
            return
        m = re.match(r"^/static/([a-z_]+\.html)$", path)
        if m and re.fullmatch(r"[a-z_]+", m.group(1).rsplit(".", 1)[0]):
            self._send_file(_STATIC / m.group(1), "text/html; charset=utf-8")
            return
        if path == "/api/snapshot":
            self._send_json(build_snapshot())
            return
        if path == "/api/evolution":
            self._send_json(self._evolution())
            return
        if path == "/api/health":
            self._send_json({"ok": True, "service": "legion-command", "version": "1.0"})
            return
        self._send_json({"ok": False, "error": "not_found: %s" % path}, 404)

    @staticmethod
    def _evolution():
        """进化层只读视图：实验卡清单 + 经验库五域统计（不读不写经验正文）。"""
        import json as _json
        exp_dir = _PLATFORM / "data" / "experiments"
        cards = []
        if exp_dir.exists():
            for p in sorted(exp_dir.glob("*.json"))[-20:]:
                try:
                    d = _json.loads(p.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    continue
                cards.append({"card_id": d.get("card_id"), "hypothesis": str(d.get("hypothesis", ""))[:60],
                              "status": d.get("status"), "arms": list((d.get("arms") or {}).keys())})
        lib = Path.home() / ".hermes" / "profiles" / "zhaoyun" / "experience"
        domains = {}
        index_rows = []
        idx = lib / "_index.jsonl"
        if idx.exists():
            for line in idx.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip():
                    try:
                        index_rows.append(_json.loads(line))
                    except ValueError:
                        continue
        for d in ("stock_screen", "execution", "tooling", "riskguard", "supervision"):
            domains[d] = {
                "entries": sum(1 for r in index_rows if r.get("domain") == d),
                "has_dir": (lib / d).exists(),
                "readonly": d == "riskguard",
            }
        return {"ok": True, "experiments": cards, "experience_domains": domains,
                "entries_total": len(index_rows),
                "note": "只读视图；经验入库须签字（Z4）"}

    def do_POST(self):
        import json
        path = urlparse(self.path).path
        if path == "/api/chat":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "bad_request"}, 400)
                return
            agent = str(req.get("agent", "")).strip()
            if not agent or not agent.replace("_", "").isalnum():
                self._send_json({"ok": False, "error": "bad_agent"}, 400)
                return
            binding = AGENT_BINDING.get(agent, _DEFAULT_BINDING)
            if binding.startswith("openai/"):
                # openai 通道未接（engine 无该 provider，勿让 Ollama 兜底报 404 误导）
                self._send_json({"schema_version": "1.0", "ok": False,
                                 "kind": "llm_chat", "data": {},
                                 "error": {"code": "channel_not_ready",
                                           "message": "openai 通道待 Q12 OPENAI_API_KEY 注入"},
                                 "degraded": False, "latency_ms": 0,
                                 "ts": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"),
                                 "agent": agent, "binding": binding})
                return
            env = llm_gateway.chat(
                binding=binding,
                system=str(req.get("system", ""))[:4000],
                user=str(req.get("user", ""))[:8000])
            # 云端限额/故障 → 会议展示层显式降级本地 9b（标 degraded + 原因，
            # 不静默；判读席位语义在 engine 层不变，此处仅会议通道可用性兜底）
            if not env.get("ok") and binding.startswith("glm/"):
                fallback = llm_gateway.chat(
                    binding="ollama/qwen3.5:9b",
                    system=str(req.get("system", ""))[:4000],
                    user=str(req.get("user", ""))[:8000])
                if fallback.get("ok"):
                    fallback["degraded"] = True
                    fallback["fallback_from"] = binding
                    fallback["fallback_reason"] = (env.get("error") or {}).get("message", "cloud unavailable")
                    env = fallback
            env["agent"] = agent
            env["binding"] = env.get("data", {}).get("binding_resolved", binding)
            self._send_json(env)
            return
        if path != "/api/dispatch":
            self._send_json({"ok": False, "error": "not_found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_json({"ok": False, "error": "bad_request"}, 400)
            return
        otype = req.get("type", "work")
        kind = _KIND_MAP.get(otype)
        if kind is None:
            self._send_json({"ok": False, "error": "bad_type: %s" % otype}, 400)
            return
        payload = {
            "command": "legion_meeting_dispatch",
            "order_type": otype,
            "title": str(req.get("title", ""))[:120],
            "attendees": req.get("attendees", []),
            "host": "laotian",
            "note": str(req.get("note", ""))[:400],
        }
        env = inbox_gateway.submit(kind, payload, source="warroom")
        self._send_json(env)


def build_server(host="127.0.0.1", port=8796):
    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None):
    ap = argparse.ArgumentParser(description="legion command console")
    ap.add_argument("--port", type=int, default=8796)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args(argv)
    # 端口占用自愈：上一次实例退出后 TIME_WAIT 未释放时 bind 会失败，
    # 重试 3 次（每次 2s），仍失败则明确报错退出（不静默死掉）
    srv = None
    for attempt in range(1, 4):
        try:
            srv = build_server(args.host, args.port)
            break
        except OSError as exc:
            print("[legion-command] bind %s:%d failed (attempt %d/3): %s"
                  % (args.host, args.port, attempt, exc), file=__import__("sys").stderr)
            __import__("time").sleep(2)
    if srv is None:
        print("[legion-command] giving up: port %d unavailable" % args.port,
              file=__import__("sys").stderr)
        return 3
    print("legion-command serving on %s:%d" % (args.host, args.port), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
