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
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import secrets

_PLATFORM = Path(__file__).resolve().parents[1]
if str(_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_PLATFORM))

from legion_ide import rw_guard as G  # noqa: E402
from legion_ide import writer as W  # noqa: E402
from legion_ide import exec_runner as X  # noqa: E402
from legion_ide import undo_store as U  # noqa: E402
import logging
# safe_path=true 时脚本目录不入 sys.path：把本树根加入 path（helper 同目录）
import sys as _sys
from pathlib import Path as _Path
for _p in _Path(__file__).resolve().parents:
    if (_p / "safe_urlopen.py").exists():
        _sys.path.insert(0, str(_p))
        break

# tools 在部分 Python 环境会被 zhice 的常规 tools 包遮蔽（namespace 包不敌
# 常规包），WMI 启动又没有 PYTHONPATH。这里按文件路径直接加载仓库内的
# safe_urlopen，保证 8103 独立启动不依赖调用方环境。
try:
    try:
        from safe_urlopen import safe_urlopen
    except ImportError:  # 作为包/仓库根导入时
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
_PATCHSETS: dict[str, dict] = {}
_PATCHSET_TTL_S = 600
_CONFIRMED_PATCHSETS: dict[str, dict] = {}
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

        def _html(self, body: bytes, session_token: str | None = None,
                  extra_cookies: list | None = None) -> None:
            headers = []
            if session_token:
                headers.append(("Set-Cookie", (
                    "ide_session=%s; HttpOnly; SameSite=Strict; Path=/"
                    % session_token
                )))
            for c in extra_cookies or []:
                headers.append(("Set-Cookie", c))
            self._send(200, body, _HTML_CT, headers)

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
            return G.check_access(dict(self.headers), required_roles=G.write_roles())

        def _owner_or_err(self, data: dict):
            expected = G.session_owner(dict(self.headers))
            if not expected:
                return None, G.err("no_session", "缺少有效 IDE 会话")
            supplied = str(
                data.get("owner") or self.headers.get("X-IDE-Owner") or ""
            ).strip()
            if supplied and supplied != expected:
                return None, G.err(
                    "owner_mismatch", "owner 与会话身份不一致（禁止自报）"
                )
            return expected, None

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
            if path == "/health":
                path = "/api/health"  # 便捷别名（监控统一）
            if path in ("/", "/ide.html"):
                ide = repo / "portal" / "ide.html"
                if not ide.is_file():
                    return self._json(503, {"ok": False, "error": "ide_html_missing"})
                return self._html(ide.read_bytes())
            if path == "/rw":
                page = static_dir / "ide_rw.html"
                if not page.is_file():
                    return self._json(503, {"ok": False, "error": "rw_page_missing"})
                # 2026-10-31 RWHOLE：直连 /rw fail-closed——本地 token 或门户身份
                # 必须至少有一个；未配置 token 且无身份 → 403，空串绝不作为凭证。
                _qs_rw = parse_qs(urlparse(self.path).query)
                _provided = str((_qs_rw.get("token") or [""])[0] or "").strip()
                if not _provided:
                    _provided = str(G.parse_cookies(
                        self.headers.get("Cookie") or "").get(
                            "ide_local_token") or "").strip()
                if not _provided:
                    _provided = str(self.headers.get("X-IDE-Token") or "").strip()
                _identity = G.auth_identity(dict(self.headers))
                _has_identity = bool((_identity or {}).get("sub"))
                _lt = G.local_token()
                if not _lt and not _has_identity:
                    return self._json(403, G.err(
                        "identity_required",
                        "直连 /rw 需本地 token（先配置 IDE_LOCAL_TOKEN / "
                        "--local-token-file）或门户登录（zb_session）",
                    ))
                if _lt and not _has_identity and not (
                        _provided and secrets.compare_digest(_provided, _lt)):
                    return self._json(401, G.err(
                        "local_token_required",
                        "直连 /rw 需本地 token（token 查询参数）或门户登录",
                    ))
                token, _owner = G.new_session(headers=dict(self.headers))
                # A1：带 ?token=... 或 X-IDE-Token 时种 ide_local_token cookie（启用本地准入后写/执行需要）
                qs = parse_qs(urlparse(self.path).query)
                want = str((qs.get("token") or [""])[0] or "").strip()
                hdr = str(self.headers.get("X-IDE-Token") or "").strip()
                lt = G.local_token()
                cap_want = str((qs.get("cap") or [""])[0] or "").strip()
                extras = []
                if lt and (want == lt or hdr == lt):
                    extras.append("ide_local_token=%s; HttpOnly; SameSite=Strict; Path=/" % lt)
                if cap_want:
                    extras.append("zbcap=" + cap_want + "; HttpOnly; SameSite=Strict; Path=/")
                return self._html(page.read_bytes(), token, extras)
            if path == "/agent_ide_bundle.json":
                bundle = (
                    Path(bundle_path)
                    if bundle_path
                    else (repo / "portal" / "agent_ide_bundle.json")
                )
                if not bundle.is_file():
                    return self._json(503, {"ok": False, "error": "bundle_missing"})
                return self._send(200, bundle.read_bytes(), _JSON_CT)
            if path == "/api/ide/version":
                try:
                    from ide_release_info import build_info
                    info = build_info()
                    return self._json(200 if info.get("ok") else 503, info)
                except Exception as e:  # noqa: BLE001
                    return self._json(500, {"ok": False, "error": "release_info_failed",
                                            "detail": "%s: %s" % (type(e).__name__, str(e)[:160])})
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
            if path == "/api/ide/session":
                info = G.session_info(dict(self.headers))
                if not info:
                    return self._json(403, G.err("no_session", "缺少有效 IDE 会话"))
                return self._json(200, {
                    "ok": True,
                    "owner": info.get("owner") or "",
                    "roles": list(info.get("roles") or []),
                    "created": info.get("created") or 0,
                })
            if path == "/api/ide/scope":
                denied = self._rw_denied()
                if denied:
                    return self._json(403, denied)
                return self._json(200, {"ok": True, "items": G.list_scope(repo)})
            if path == "/api/ide/tree":
                denied = self._rw_denied()
                if denied:
                    return self._json(403, denied)
                qs = parse_qs(parsed.query)
                task = (qs.get("task") or [""])[0].strip()
                rel = (qs.get("path") or ["."])[0].strip() or "."
                wt, error = G.resolve_worktree(repo, task)
                if error:
                    return self._json(400, error)
                if rel in ("", ".", "/"):
                    base = wt  # noqa: F841 - 既有未使用变量
                    names = list(G.ALLOWED_TOP)
                    items = []
                    for name in names:
                        d = (wt / name)
                        if d.is_dir():
                            items.append({"name": name, "path": name, "type": "dir"})
                    return self._json(200, {"ok": True, "path": ".", "items": items})
                rel = rel.replace("\\", "/").strip("/")
                if not rel or ".." in rel.split("/"):
                    return self._json(400, G.err("bad_path", "非法目录路径"))
                target = (wt / rel).resolve()
                wt_resolved = wt.resolve()
                if not target.is_relative_to(wt_resolved):
                    return self._json(400, G.err("escape", "路径越出 worktree"))
                parts = target.relative_to(wt_resolved).parts
                if not parts or parts[0] not in G.ALLOWED_TOP:
                    return self._json(400, G.err("scope_denied", "只允许受控目录"))
                if any(p.lower() in G.BLOCKED_PARTS for p in parts):
                    return self._json(400, G.err("blocked_path", "路径含禁止目录"))
                if not target.is_dir():
                    return self._json(400, G.err("not_dir", "不是目录：%s" % rel))
                items = []
                for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                    if entry.name in G.BLOCKED_PARTS or entry.name.startswith("."):
                        continue
                    if len(items) >= 500:
                        break
                    try:
                        r = entry.relative_to(wt).as_posix()
                    except ValueError:
                        continue
                    items.append({
                        "name": entry.name, "path": r,
                        "type": "dir" if entry.is_dir() else "file",
                        "size": entry.stat().st_size if entry.is_file() else 0,
                    })
                return self._json(200, {"ok": True, "path": rel, "items": items})
            if path == "/api/ide/lock":
                denied = self._rw_denied()
                if denied:
                    return self._json(403, denied)
                task = (parse_qs(parsed.query).get("task") or [""])[0].strip()
                wt, error = G.resolve_worktree(repo, task)
                if error:
                    return self._json(400, error)
                return self._json(200, {"ok": True, "task": task,
                                        **G.lock_info(wt)})
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
                if error.get("error") in (
                    "rw_disabled", "no_session", "bad_origin", "owner_mismatch",
                )
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
                "/api/ide/git",
                "/api/ide/undo",
                "/api/ide/test",
                "/api/ide/patchset/preview",
                "/api/ide/patchset/confirm",
                "/api/ide/patchset/rollback",
            ):
                return self._json(
                    404, {"ok": False, "error": "not_found", "path": path}
                )
            data, error = self._body_json()
            if error:
                return self._json(400, error)
            # Phase A：能力令牌先于 worktree 解析（避免无 cap 探测任务/先报 worktree）
            if (path in ("/api/ide/write/preview", "/api/ide/write/confirm",
                         "/api/ide/undo", "/api/ide/exec", "/api/ide/test")
                    or path.startswith("/api/ide/patchset/")):
                _ct = str(data.get("task") or "")
                _scope = ("ide:exec:" if path in ("/api/ide/exec", "/api/ide/test")
                          else "ide:write:") + _ct
                cap_err = G.check_cap(dict(self.headers), _scope, _ct)
                if cap_err:
                    return self._json(403, cap_err)
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
                return self._json(self._reject_code(error), error)
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
            if path == "/api/ide/git":
                action = str(data.get("action") or "diff").strip().lower()
                rel = str(data.get("path") or "").strip()
                if action in ("add", "commit") and not rel:
                    return self._json(400, G.err("path_required", "需要文件路径"))
                if rel:
                    _target, perr = G.safe_path(wt, rel)
                    if perr:
                        return self._json(400, perr)
                if action == "diff":
                    args = ["diff", "--cached", "--"] + ([rel] if rel else [])
                elif action == "add":
                    args = ["add", "--", rel]
                elif action == "commit":
                    message = str(data.get("message") or "").strip()[:500]
                    if not message:
                        return self._json(400, G.err("message_required", "需要提交说明"))
                    args = ["commit", "-m", message, "--", rel]
                else:
                    return self._json(400, G.err("bad_action", "仅 diff/add/commit"))
                env = dict(os.environ)
                env.update({"GIT_PAGER": "cat", "GIT_TERMINAL_PROMPT": "0"})
                try:
                    proc = subprocess.run(
                        ["git", "-C", str(wt), *args],
                        capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=60, env=env,
                    )
                    out = ((proc.stdout or "") + (proc.stderr or ""))[:256 * 1024]
                    ok = proc.returncode == 0
                except (OSError, subprocess.TimeoutExpired) as exc:
                    return self._json(500, G.err("git_failed", str(exc)[:200]))
                G.audit(
                    repo, "code_action", data.get("task"), owner,
                    "git %s %s -> %s" % (action, rel, "ok" if ok else "fail"),
                )
                return self._json(200, {
                    "ok": bool(ok), "action": action, "path": rel,
                    "exit_code": proc.returncode, "output": out,
                })
            if path == "/api/ide/write":
                return self._json(400, G.err(
                    "write_requires_preview",
                    "写文件已改为两阶段：先 /api/ide/write/preview，再 /api/ide/write/confirm",
                ))
            if path == "/api/ide/write/preview":
                cap_err = G.check_cap(dict(self.headers),
                                      "ide:write:" + str(data.get("task") or ""),
                                      str(data.get("task") or ""))
                if cap_err:
                    return self._json(403, cap_err)
                res = W.preview_file(wt, data.get("path"), data.get("content", ""))
                if not res.get("ok"):
                    return self._json(400, res)
                _prune_previews()
                preview_id = uuid.uuid4().hex
                old_content = None
                if not res.get("created"):
                    _old = W.read_file(wt, res["path"])
                    if _old.get("ok"):
                        old_content = _old.get("content")
                _PREVIEWS[preview_id] = {
                    "ts": time.time(),
                    "task": str(data.get("task") or ""),
                    "owner": owner,
                    "path": res["path"],
                    "content": data.get("content", ""),
                    "old_sha256": res["old_sha256"],
                    "old_content": old_content,
                    "new_sha256": res.get("new_sha256"),
                    "created": bool(res.get("created")),
                }
                _prune_previews()
                G.audit(repo, "code_action", data.get("task"), owner,
                        "preview %s +%dB" % (res["path"], res["bytes"]))
                return self._json(200, {**res, "preview_id": preview_id,
                                        "expires_in_s": _PREVIEW_TTL_S})
            if path == "/api/ide/write/confirm":
                preview_id = str(data.get("preview_id") or "").strip()
                pending = _PREVIEWS.get(preview_id)
                _ctask = str((pending or {}).get("task") or data.get("task") or "")
                cap_err = G.check_cap(dict(self.headers), "ide:write:" + _ctask, _ctask)
                if cap_err:
                    return self._json(403, cap_err)
                if not pending:
                    return self._json(400, G.err("preview_missing", "预览不存在或已过期"))
                if (pending.get("owner") != owner
                        or pending.get("task") != str(data.get("task") or "")):
                    return self._json(409, G.err("preview_owner_mismatch", "预览不属于当前工单/会话"))
                res = W.write_file(wt, pending["path"], pending["content"],
                                   expected_sha256=pending["old_sha256"])
                if not res.get("ok"):
                    return self._json(409 if res.get("error") == "stale_preview" else 400, res)
                if (not pending.get("created")
                        and pending.get("old_content") is not None):
                    U.save(
                        pending["task"], pending["path"],
                        pending["old_content"],
                        res.get("sha256") or pending.get("new_sha256") or "",
                        owner,
                    )
                _PREVIEWS.pop(preview_id, None)
                G.audit(repo, "code_action", data.get("task"), owner,
                        "confirm %s -> ok" % pending["path"])
                return self._json(200, {**res, "confirmed": True})
            if path == "/api/ide/undo":
                rel = str(data.get("path") or "").strip()
                task = str(data.get("task") or "")
                cap_err = G.check_cap(dict(self.headers), "ide:write:" + task, task)
                if cap_err:
                    return self._json(403, cap_err)
                rec = U.get(task, rel)
                if not rec:
                    return self._json(404, G.err("undo_missing", "没有可撤销的写入"))
                current = W.read_file(wt, rel)
                if not current.get("ok"):
                    return self._json(400, current)
                if current.get("sha256") != rec.get("new_sha"):
                    U.pop(task, rel)
                    return self._json(409, G.err("undo_stale", "文件已被再次修改，拒绝回滚"))
                res = W.write_file(wt, rel, rec["old_content"])
                if not res.get("ok"):
                    return self._json(400, res)
                U.pop(task, rel)
                G.audit(repo, "code_action", data.get("task"), owner,
                        "undo %s -> ok" % rel)
                return self._json(200, {**res, "undone": True,
                                        "restored_sha256": res.get("sha256")})
            if path == "/api/ide/patchset/preview":
                files = data.get("files")
                if not isinstance(files, list) or not (2 <= len(files) <= 20):
                    return self._json(400, G.err("bad_files", "files 须为 2–20 项数组"))
                now = time.time()
                for k in [k for k, v in _PATCHSETS.items() if now - v.get("ts", 0) > _PATCHSET_TTL_S]:
                    _PATCHSETS.pop(k, None)
                items = []
                seen = set()
                for item in files:
                    if not isinstance(item, dict):
                        return self._json(400, G.err("bad_files", "files 项须为对象"))
                    rel = str(item.get("path") or "").strip()
                    if not rel or rel in seen:
                        return self._json(400, G.err("bad_path", "path 缺失或重复：%s" % rel))
                    seen.add(rel)
                    res = W.preview_file(wt, rel, item.get("content", ""))
                    if not res.get("ok"):
                        return self._json(400, res)
                    old_content = None
                    if not res.get("created"):
                        old = W.read_file(wt, rel)
                        if old.get("ok"):
                            old_content = old.get("content")
                    items.append({
                        "path": rel, "created": bool(res.get("created")),
                        "old_sha256": res.get("old_sha256"),
                        "new_sha256": res.get("new_sha256"),
                        "bytes": res.get("bytes"),
                        "diff": res.get("diff"), "old_content": old_content,
                        "content": item.get("content", ""),
                    })
                pid = "ps-" + uuid.uuid4().hex[:12]
                _PATCHSETS[pid] = {
                    "ts": time.time(), "task": str(data.get("task") or ""),
                    "owner": owner, "files": items,
                    "message": str(data.get("message") or "")[:200],
                }
                G.audit(repo, "code_action", data.get("task"), owner,
                        "patchset preview %d files" % len(items))
                return self._json(200, {
                    "ok": True, "patchset_id": pid,
                    "files": [{k: v for k, v in f.items() if k not in ("content", "old_content")}
                              for f in items],
                })
            if path == "/api/ide/patchset/confirm":
                pid = str(data.get("patchset_id") or "").strip()
                pending = _PATCHSETS.get(pid)
                if not pending:
                    return self._json(400, G.err("patchset_missing", "patchset 不存在或已过期"))
                task = str(data.get("task") or "")
                if pending.get("owner") != owner or pending.get("task") != task:
                    return self._json(409, G.err("patchset_owner_mismatch", "patchset 不属于当前工单/会话"))
                applied = []
                try:
                    for f in pending["files"]:
                        res = W.write_file(wt, f["path"], f["content"],
                                           expected_sha256=f["old_sha256"])
                        if not res.get("ok"):
                            raise RuntimeError("write_failed:%s:%s" % (f["path"], res.get("error")))
                        applied.append(f)
                except Exception as exc:  # noqa: BLE001
                    rolled = []
                    for f in reversed(applied):
                        try:
                            if f.get("created"):
                                pth, _e = G.safe_path(wt, f["path"])
                                if pth.is_file():
                                    pth.unlink()
                            elif f.get("old_content") is not None:
                                W.write_file(wt, f["path"], f["old_content"])
                            rolled.append(f["path"])
                        except Exception:  # noqa: BLE001
                            pass
                    G.audit(repo, "code_action", task, owner,
                            "patchset confirm FAIL %s rolled=%s" % (str(exc)[:160], ",".join(rolled)))
                    return self._json(409, {"ok": False, "error": "patchset_apply_failed",
                                            "detail": str(exc)[:300], "rolled_back": rolled})
                _PATCHSETS.pop(pid, None)
                _CONFIRMED_PATCHSETS[pid] = {"ts": time.time(), "task": task,
                                             "owner": owner, "files": applied}
                for f in applied:
                    if not f.get("created") and f.get("old_content") is not None:
                        U.save(task, f["path"], f["old_content"], f.get("new_sha256") or "", owner)
                run_id = "patchset-" + (task or "none") + "-" + uuid.uuid4().hex[:8]
                try:
                    from legion_command import evidence as EV
                    EV.new_run(run_id, task=task, agent=owner, kind="patchset")
                    EV.append(run_id, "patchset_confirm", {
                        "files": [{"path": f["path"], "created": f["created"],
                                   "old_sha256": f["old_sha256"], "new_sha256": f["new_sha256"]}
                                  for f in applied],
                        "message": pending.get("message") or "",
                    })
                    EV.finalize(run_id, mode="patchset", ok=True, task=task)
                except Exception:  # noqa: BLE001
                    pass
                G.audit(repo, "code_action", task, owner,
                        "patchset confirm %d files ok" % len(applied))
                return self._json(200, {"ok": True, "patchset_id": pid,
                                        "files": [f["path"] for f in applied],
                                        "evidence_run_id": run_id})
            if path == "/api/ide/patchset/rollback":
                pid = str(data.get("patchset_id") or "").strip()
                task = str(data.get("task") or "")
                pending = _PATCHSETS.get(pid)
                if pending:
                    if pending.get("owner") != owner or pending.get("task") != task:
                        return self._json(409, G.err("patchset_owner_mismatch",
                                                     "patchset 不属于当前工单/会话"))
                    _PATCHSETS.pop(pid, None)
                    G.audit(repo, "code_action", task, owner, "patchset cancel %s" % pid)
                    return self._json(200, {"ok": True, "patchset_id": pid,
                                            "cancelled": True, "restored": []})
                confirmed = _CONFIRMED_PATCHSETS.get(pid)
                if not confirmed:
                    return self._json(400, G.err("patchset_missing", "patchset 不存在或已过期"))
                if confirmed.get("owner") != owner or confirmed.get("task") != task:
                    return self._json(409, G.err("patchset_owner_mismatch",
                                                 "patchset 不属于当前工单/会话"))
                restored = []
                for f in reversed(confirmed["files"]):
                    try:
                        if f.get("created"):
                            pth, _e = G.safe_path(wt, f["path"])
                            if pth.is_file():
                                pth.unlink()
                        else:
                            cur = W.read_file(wt, f["path"])
                            if not cur.get("ok"):
                                return self._json(409, G.err("rollback_failed", "读取失败：%s" % f["path"]))
                            if cur.get("sha256") != (f.get("new_sha256") or ""):
                                return self._json(409, G.err("rollback_stale",
                                                             "文件已再次修改，拒绝回滚：%s" % f["path"]))
                            if f.get("old_content") is not None:
                                W.write_file(wt, f["path"], f["old_content"])
                        restored.append(f["path"])
                    except Exception as exc:  # noqa: BLE001
                        return self._json(500, G.err("rollback_failed", "%s: %s" % (f["path"], str(exc)[:160])))
                _CONFIRMED_PATCHSETS.pop(pid, None)
                run_id = "patchset-rollback-" + (task or "none") + "-" + uuid.uuid4().hex[:8]
                try:
                    from legion_command import evidence as EV
                    EV.new_run(run_id, task=task, agent=owner, kind="patchset")
                    EV.append(run_id, "patchset_rollback", {"restored": restored})
                    EV.finalize(run_id, mode="rollback", ok=True, task=task)
                except Exception:  # noqa: BLE001
                    pass
                G.audit(repo, "code_action", task, owner,
                        "patchset rollback %d files" % len(restored))
                return self._json(200, {"ok": True, "patchset_id": pid,
                                        "restored": restored, "evidence_run_id": run_id})
            if path == "/api/ide/test":
                _ttask = str(data.get("task") or "")
                _cmd = str(data.get("command") or "python -m pytest -q .")
                try:
                    _to = int(data.get("timeout") or X.DEFAULT_TIMEOUT)
                except (TypeError, ValueError):
                    _to = X.DEFAULT_TIMEOUT
                _to = max(1, min(_to, 600))
                _res = X.run(wt, _cmd, _to)
                _ok = bool(_res.get("ok") and _res.get("success"))
                _out = str(_res.get("output") or _res.get("stdout") or "")[:20000]
                _run_id = "task-" + _ttask + "-" + uuid.uuid4().hex[:8]
                try:
                    from legion_command import evidence as _EV
                    _EV.new_run(_run_id, task=_ttask, agent="LDE", kind="test")
                    _EV.append(_run_id, "test_result", {
                        "ok": _ok, "exit_code": _res.get("exit_code"),
                        "command": _cmd[:500],
                        "output_sha256": __import__("hashlib").sha256(
                            _out.encode("utf-8", "replace")).hexdigest(),
                        "output_tail": _out[-4000:],
                    })
                    _EV.finalize(_run_id, mode="test", ok=_ok,
                                 task=_ttask, run_id=_run_id)
                except Exception:  # noqa: BLE001
                    pass
                G.audit(repo, "code_observation", _ttask, owner,
                        "test %s -> %s" % (_cmd[:80], "ok" if _ok else "fail"))
                return self._json(200 if _ok else 400, {
                    "ok": _ok, "success": _ok, "exit_code": _res.get("exit_code"),
                    "error": _res.get("error"), "evidence_run_id": _run_id,
                    "output_tail": _out[-4000:],
                })
            _etask = str(data.get("task") or "")
            cap_err = G.check_cap(dict(self.headers), "ide:exec:" + _etask, _etask)
            if cap_err:
                return self._json(403, cap_err)
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
    ap.add_argument("--local-token-file", default="")
    ap.add_argument("--cap-enabled", action="store_true")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--portal", default=DEFAULT_PORTAL)
    ap.add_argument("--bundle", default="")
    ap.add_argument("--health", action="store_true")
    ap.add_argument("--state-dir", default="",
                    help="LDE session/undo 持久化目录（默认空=纯内存，测试友好）")
    ap.add_argument(
        "--rw", action="store_true", help="开启受控写/执行（等价 IDE_RW=on；默认只读）"
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    if getattr(args, "local_token_file", ""):
        os.environ["IDE_LOCAL_TOKEN_FILE"] = str(args.local_token_file)
    if getattr(args, "cap_enabled", False):
        os.environ["LEGION_CAP_ENABLED"] = "1"
    if args.state_dir:
        os.environ["LEGION_IDE_STATE_DIR"] = args.state_dir
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
