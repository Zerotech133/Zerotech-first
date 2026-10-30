# -*- coding: utf-8 -*-
"""军团 Agent IDE · 独立形态服务（legion_ide）· v2（R0+R1+R2）。

原有（只读）：GET / → portal/ide.html；/agent_ide_bundle.json；/api/health；
              POST /ollama/api/chat 代理门户（可降级）。
新增（默认 `IDE_RW=off`，需显式开启）：
  GET  /rw                     施工台页面（受控编辑 + 白名单执行）
  GET  /api/ide/scope          可写范围（doing/review 的 task worktree）
  POST /api/ide/read           读文件（作用域内）
  POST /api/ide/write          写文件（作用域 + 工单锁 + 审计）
  POST /api/ide/exec           执行白名单命令（作用域 + 工单锁 + 审计）
  GET  /api/ide/audit?task=    本单编辑/执行历史（events 只读）
  POST/DELETE /api/ide/lock    获取/释放工单锁
安全：仅 127.0.0.1；/ 与 /rw 种 HttpOnly 会话 cookie；写/执行要求同源 + cookie；
      路径白名单（agent_platform/ops/docs/tools/portal）、禁 .git/_vault/密钥后缀；
      命令白名单 + 超时 + 输出上限 + 环境净化；动作落 dev_events（code_action/observation）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_PLATFORM = Path(__file__).resolve().parents[1]
if str(_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_PLATFORM))

from legion_ide import rw_guard as G  # noqa: E402
from legion_ide import writer as W  # noqa: E402
from legion_ide import exec_runner as X  # noqa: E402
import logging

# tools 在部分 Python 环境会被 zhice 的常规 tools 包遮蔽（namespace 包不敌
# 常规包），WMI 启动又没有 PYTHONPATH。这里按文件路径直接加载仓库内的
# safe_urlopen，保证 8103 独立启动不依赖调用方环境。
try:
    from tools.safe_urlopen import safe_urlopen
except ImportError:  # pragma: no cover - 仅在异常环境触发
    import importlib.util as _ilu
    _safe_path = Path(__file__).resolve().parents[2] / "tools" / "safe_urlopen.py"
    _spec = _ilu.spec_from_file_location("legion_safe_urlopen", _safe_path)
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    safe_urlopen = _mod.safe_urlopen

VERSION = "2.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8103
DEFAULT_PORTAL = "http://127.0.0.1:8091"
DEFAULT_BUNDLE = Path(r"C:\项目\_vault\legion_ide\agent_ide_bundle.json")
REPO = Path(__file__).resolve().parents[2]
MAX_BODY = 1024 * 1024
_JSON_CT = "application/json; charset=utf-8"
_HTML_CT = "text/html; charset=utf-8"
_EXEC_RESULTS: dict[str, dict] = {}
_EXEC_RESULTS_TTL_S = 3600
_EXEC_RESULTS_MAX = 100
_PREVIEWS: dict[str, dict] = {}
_PREVIEW_TTL_S = 600
_PREVIEW_MAX = 20


def _prune_previews() -> None:
    now = time.time()
    for key in [k for k, v in _PREVIEWS.items()
                if now - float(v.get("ts") or 0) > _PREVIEW_TTL_S]:
        _PREVIEWS.pop(key, None)
    while len(_PREVIEWS) > _PREVIEW_MAX:
        _PREVIEWS.pop(next(iter(_PREVIEWS)))


def _remember_exec(run_id: str, result: dict) -> None:
    """内存执行结果只用于回执：限 TTL、限条数，避免长期运行泄漏。"""
    now = time.time()
    for key in [k for k, v in _EXEC_RESULTS.items()
                if now - float(v.get("ts") or 0) > _EXEC_RESULTS_TTL_S]:
        _EXEC_RESULTS.pop(key, None)
    _EXEC_RESULTS[run_id] = {"ts": now, **result}
    while len(_EXEC_RESULTS) > _EXEC_RESULTS_MAX:
        _EXEC_RESULTS.pop(next(iter(_EXEC_RESULTS)))


def _resource(path: Path) -> dict:
    try:
        st = path.stat()
    except OSError:
        return {"exists": False, "bytes": 0, "mtime": None}
    return {
        "exists": True,
        "bytes": st.st_size,
        "mtime": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime)),
    }


def resolve_bundle(repo: Path, bundle: str | None = None) -> Path:
    if bundle:
        return Path(bundle)
    if DEFAULT_BUNDLE.is_file():
        return DEFAULT_BUNDLE
    return Path(repo) / "portal" / "agent_ide_bundle.json"


def build_health(
    repo: Path, portal_url: str, port: int, bundle_path: Path | None = None
) -> dict:
    repo = Path(repo)
    ide = repo / "portal" / "ide.html"
    bundle = (
        Path(bundle_path)
        if bundle_path
        else (repo / "portal" / "agent_ide_bundle.json")
    )
    return {
        "ok": True,
        "service": "legion-ide",
        "version": VERSION,
        "pid": os.getpid(),
        "bind": "%s:%s" % (DEFAULT_HOST, port),
        "ide_html": _resource(ide),
        "bundle": _resource(bundle),
        "bundle_source": str(bundle),
        "rw_enabled": G.rw_enabled(),
        "portal_proxy": {"url": portal_url, "reachable": _portal_alive(portal_url)},
        "note": "只读施工台 + 受控写/执行（IDE_RW=off 时只读）",
    }


def _portal_alive(url: str, timeout: float = 0.6) -> bool:
    base = url.rstrip("/")
    for path in ("/api/health", "/"):
        try:
            with safe_urlopen(base + path, timeout=timeout) as r:
                return 200 <= r.status < 500
        except urllib.error.HTTPError:
            return True
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return False


def make_handler(repo: Path, portal_url: str, bundle_path: Path | None = None):
    repo = Path(repo)
    static_dir = Path(__file__).resolve().parent / "static"

    class Handler(BaseHTTPRequestHandler):
        server_version = "LegionIDE/" + VERSION

        def log_message(self, fmt, *args):
            pass

        def _send(
            self,
            status: int,
            body: bytes,
            ctype: str,
            extra_headers: list | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in extra_headers or []:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(
            self, status: int, obj: dict, extra_headers: list | None = None
        ) -> None:
            self._send(
                status,
                json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                _JSON_CT,
                extra_headers,
            )

        def _html(self, body: bytes) -> None:
            cookie = (
                "ide_session=%s; HttpOnly; SameSite=Strict; Path=/" % G.session_token()
            )
            self._send(200, body, _HTML_CT, [("Set-Cookie", cookie)])

        def _body_json(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_BODY:
                return None, G.err("bad_content_length", "请求体缺失或超限")
            try:
                return json.loads(self.rfile.read(length).decode("utf-8")), None
            except (ValueError, UnicodeDecodeError):
                return None, G.err("bad_json", "请求体不是合法 JSON")

        def _rw_denied(self):
            if not G.rw_enabled():
                return G.err("rw_disabled", "IDE_RW=off：写/执行未开启（只读模式）")
            return G.check_access(dict(self.headers))

        def _owner_or_err(self, data: dict):
            owner = str(
                data.get("owner") or self.headers.get("X-IDE-Owner") or ""
            ).strip()
            if not owner:
                return None, G.err("owner_required", "缺少 owner 标识")
            return owner, None

        def _lock_required(self, wt: Path, owner: str):
            info = G.lock_info(wt)
            if not info.get("locked"):
                return G.err("lock_required", "先获取工单锁（POST /api/ide/lock）")
            if info.get("owner") != owner:
                return G.err("locked", "工单锁属于他人（%s）" % info.get("owner"), info)
            return None

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            if path in ("/", "/ide.html"):
                ide = repo / "portal" / "ide.html"
                if not ide.is_file():
                    return self._json(503, {"ok": False, "error": "ide_html_missing"})
                return self._html(ide.read_bytes())
            if path == "/rw":
                page = static_dir / "ide_rw.html"
                if not page.is_file():
                    return self._json(503, {"ok": False, "error": "rw_page_missing"})
                return self._html(page.read_bytes())
            if path == "/agent_ide_bundle.json":
                bundle = (
                    Path(bundle_path)
                    if bundle_path
                    else (repo / "portal" / "agent_ide_bundle.json")
                )
                if not bundle.is_file():
                    return self._json(503, {"ok": False, "error": "bundle_missing"})
                return self._send(200, bundle.read_bytes(), _JSON_CT)
            if path == "/api/health":
                return self._json(
                    200,
                    build_health(
                        repo,
                        portal_url,
                        self.server.server_port,
                        Path(bundle_path) if bundle_path else None,
                    ),
                )
            if path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            if path == "/api/ide/scope":
                denied = self._rw_denied()
                if denied:
                    return self._json(403, denied)
                return self._json(200, {"ok": True, "items": G.list_scope(repo)})
            if path == "/api/ide/audit":
                denied = self._rw_denied()
                if denied:
                    return self._json(403, denied)
                task = (parse_qs(parsed.query).get("task") or [""])[0].strip()
                return self._json(200, {"ok": True, "events": self._audit_read(task)})
            return self._json(404, {"ok": False, "error": "not_found", "path": path})

        def _audit_read(self, task: str) -> list:
            try:
                tools = str(repo / "tools")
                if tools not in sys.path:
                    sys.path.insert(0, tools)
                import events as E  # noqa: WPS433

                rows = E.read(limit=300)
            except Exception:  # noqa: BLE001
                return []
            out = [
                r for r in rows if r.get("kind") in ("code_action", "code_observation")
            ]
            if task:
                out = [r for r in out if r.get("task") == task]
            return out[-100:]

        def _ctx(self, data: dict):
            denied = self._rw_denied()
            if denied:
                return None, denied
            wt, error = G.resolve_worktree(repo, data.get("task"))
            if error:
                return None, error
            return wt, None

        def _reject_code(self, error: dict) -> int:
            return (
                403
                if error.get("error") in ("rw_disabled", "no_session", "bad_origin")
                else 400
            )

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/ollama/api/chat":
                return self._proxy_chat()
            if path not in (
                "/api/ide/read",
                "/api/ide/write",
                "/api/ide/write/preview",
                "/api/ide/write/confirm",
                "/api/ide/exec",
                "/api/ide/lock",
            ):
                return self._json(
                    404, {"ok": False, "error": "not_found", "path": path}
                )
            data, error = self._body_json()
            if error:
                return self._json(400, error)
            wt, error = self._ctx(data)
            if error:
                return self._json(self._reject_code(error), error)
            if path == "/api/ide/read":
                res = W.read_file(wt, data.get("path"))
                G.audit(
                    repo,
                    "code_action",
                    data.get("task"),
                    "IDE",
                    "read %s -> %s"
                    % (data.get("path"), "ok" if res.get("ok") else res.get("error")),
                )
                return self._json(200 if res.get("ok") else 400, res)
            owner, error = self._owner_or_err(data)
            if error:
                return self._json(400, error)
            if path == "/api/ide/lock":
                res = G.acquire_lock(wt, owner)
                G.audit(
                    repo,
                    "code_action",
                    data.get("task"),
                    owner,
                    "lock -> %s" % ("ok" if res.get("ok") else res.get("error")),
                )
                return self._json(200 if res.get("ok") else 409, res)
            locked = self._lock_required(wt, owner)
            if locked:
                return self._json(409, locked)
            if path == "/api/ide/write":
                return self._json(400, G.err(
                    "write_requires_preview",
                    "写文件已改为两阶段：先 /api/ide/write/preview，再 /api/ide/write/confirm",
                ))
            if path == "/api/ide/write/preview":
                res = W.preview_file(wt, data.get("path"), data.get("content", ""))
                if not res.get("ok"):
                    return self._json(400, res)
                _prune_previews()
                preview_id = uuid.uuid4().hex
                _PREVIEWS[preview_id] = {
                    "ts": time.time(),
                    "task": str(data.get("task") or ""),
                    "owner": owner,
                    "path": res["path"],
                    "content": data.get("content", ""),
                    "old_sha256": res["old_sha256"],
                }
                _prune_previews()
                G.audit(repo, "code_action", data.get("task"), owner,
                        "preview %s +%dB" % (res["path"], res["bytes"]))
                return self._json(200, {**res, "preview_id": preview_id,
                                        "expires_in_s": _PREVIEW_TTL_S})
            if path == "/api/ide/write/confirm":
                preview_id = str(data.get("preview_id") or "").strip()
                pending = _PREVIEWS.get(preview_id)
                if not pending:
                    return self._json(400, G.err("preview_missing", "预览不存在或已过期"))
                if (pending.get("owner") != owner
                        or pending.get("task") != str(data.get("task") or "")):
                    return self._json(409, G.err("preview_owner_mismatch", "预览不属于当前工单/会话"))
                res = W.write_file(wt, pending["path"], pending["content"],
                                   expected_sha256=pending["old_sha256"])
                if not res.get("ok"):
                    return self._json(409 if res.get("error") == "stale_preview" else 400, res)
                _PREVIEWS.pop(preview_id, None)
                G.audit(repo, "code_action", data.get("task"), owner,
                        "confirm %s -> ok" % pending["path"])
                return self._json(200, {**res, "confirmed": True})
            try:
                timeout_s = int(data.get("timeout") or X.DEFAULT_TIMEOUT)
            except (TypeError, ValueError):
                timeout_s = X.DEFAULT_TIMEOUT
            timeout_s = max(1, min(timeout_s, 600))
            res = X.run(wt, data.get("command"), timeout_s)
            run_id = uuid.uuid4().hex[:12]
            _remember_exec(run_id, res)
            G.audit(
                repo,
                "code_observation",
                data.get("task"),
                owner,
                "exec %r exit=%s"
                % (str(data.get("command"))[:160], res.get("exit_code")),
            )
            return self._json(200 if res.get("ok") else 400, {**res, "run_id": run_id})

        def do_DELETE(self):
            parsed = urlparse(self.path)
            if parsed.path != "/api/ide/lock":
                return self._json(404, {"ok": False, "error": "not_found"})
            denied = self._rw_denied()
            if denied:
                return self._json(403, denied)
            q = parse_qs(parsed.query)
            task = (q.get("task") or [""])[0].strip()
            owner = (q.get("owner") or [""])[0].strip()
            wt, error = G.resolve_worktree(repo, task)
            if error:
                return self._json(400, error)
            if not owner:
                return self._json(400, G.err("owner_required", "缺少 owner"))
            res = G.release_lock(wt, owner)
            G.audit(repo, "code_action", task, owner, "unlock")
            return self._json(200 if res.get("ok") else 409, res)

        def _proxy_chat(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_BODY:
                return self._json(413, {"ok": False, "error": "bad_content_length"})
            body = self.rfile.read(length)
            ctype = self.headers.get("Content-Type") or _JSON_CT
            req = urllib.request.Request(
                portal_url.rstrip("/") + "/ollama/api/chat",
                data=body,
                headers={"Content-Type": ctype},
                method="POST",
            )
            try:
                with safe_urlopen(req, timeout=180) as r:
                    return self._send(r.status, r.read(), _JSON_CT)
            except urllib.error.HTTPError as exc:
                return self._send(exc.code, exc.read(), _JSON_CT)
            except (urllib.error.URLError, OSError) as exc:
                return self._json(
                    503,
                    {
                        "ok": False,
                        "error": "portal_proxy_unavailable",
                        "portal": portal_url,
                        "detail": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
                    },
                )

    return Handler


def create_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    repo: Path | None = None,
    portal_url: str = DEFAULT_PORTAL,
    bundle_path: Path | None = None,
) -> ThreadingHTTPServer:
    handler = make_handler(Path(repo) if repo else REPO, portal_url, bundle_path)
    return ThreadingHTTPServer((host, int(port)), handler)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="军团 Agent IDE · 独立形态服务 v2")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--portal", default=DEFAULT_PORTAL)
    ap.add_argument("--bundle", default="")
    ap.add_argument("--health", action="store_true")
    ap.add_argument(
        "--rw", action="store_true", help="开启受控写/执行（等价 IDE_RW=on；默认只读）"
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    if args.rw:
        # WMI 拉起不继承调用方 env（实测教训）：显式参数比环境变量可靠
        os.environ[G.RW_ENV] = "on"
    repo = Path(args.repo)
    bundle = resolve_bundle(repo, args.bundle or None)
    if args.health:
        print(
            json.dumps(
                build_health(repo, args.portal, args.port, bundle), ensure_ascii=False
            )
        )
        return 0
    httpd = create_server(
        args.host, args.port, repo=repo, portal_url=args.portal, bundle_path=bundle
    )
    print(
        "[legion-ide] listening http://%s:%s/  rw=%s  bundle=%s"
        % (args.host, httpd.server_port, G.rw_enabled(), bundle),
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/legion_ide/serve.py", exc)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
