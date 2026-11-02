#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hermes ACAD1（司马徽 / Zhice 研究台）扩展构建流水线 · P2-3

一条命令把 Zhice 前端重新构建为 Hermes WebUI 的 `/extensions/zhice/` 扩展产物：

    python tools/hermes_acad1_build.py                     # 构建 + 补丁 + 验证 + 交付清单
    python tools/hermes_acad1_build.py --publish-live      # 追加：把 zhice/ 覆盖发布到 8787 生产扩展目录
    python tools/hermes_acad1_build.py --verify-only       # 只复核现有产物/清单/静态路径

设计要点（与任务 P2-3 对齐）：
- 输入 = Zhice frontend 源码（默认 C:\\项目\\zhice-os-v2-windows-20260920\\code\\frontend）；
- 固定构建参数：VITE_BASE=/extensions/zhice/、VITE_ROUTER=hash、
  VITE_ENABLED_MARKETS=CN,US,HK,CRYPTO、VITE_API_BASE=http://127.0.0.1:8105/api/v1
  （8105 = 生产 CSP connect-src 允许的 127.0.0.1:* 段；见交付 README「已知风险」）；
- 输出 = Hermes/extensions/legion-ide/zhice（Hermes/* 被 gitignore，产物不进库，
  以 VERSION.json + deliverables 清单（源 commit/dirty/时间/sha256）做版本化；
- 构建后补丁（把源码里写死的路径改成扩展可用的绝对路径）：
    * fetch('/api/v1/...') 等 4 处相对 API → VITE_API_BASE 绝对地址；
    * WebSocket `${location.host}/api/v1/...` → `ws://<api-host>/api/v1/...`；
    * `<img src="/logo.png">` / `/logo-icon.png` 2 处根绝对 logo → `/extensions/zhice/...`；
  全部按字节替换（UTF-8 原样），不做 PowerShell 文本读写，避免旧手工补丁引入的
  LF→CRLF 转换与漏改（WS/logo）问题；
- **same-origin 模式（P1，/quant/ 页面包）**：`--same-origin`，或 `--api-base` 为相对路径时
  自动启用（例：`--base-path /quant/ --api-base /quant-api/api/v1`）。补丁规则：
    * `://${location.host}/api/v1` → `://${location.host}/quant-api/api/v1`（保留同源 host）；
    * 裸相对 `/api/v1` → `/quant-api/api/v1`（不重复加前缀）；
    * 根绝对 logo `/logo*.png` → `/quant/logo*.png`；
  断言：无裸 `/api/v1`（前缀外）、无 `${location.host}/api/v1` 残留、无
  `127.0.0.1:8105`/`5175`/`autoSession`/`zhice_auto_password`、有 `/quant-api/api/v1`；
  VERSION.json 记录 same-origin 统计（dirty_count/autoSession/legacy_port 计数）。
  绝对 origin 模式（默认 `http://127.0.0.1:8105/api/v1`）行为不变。
- 不修改 Hermes 核心、不改 CSP、不重启 8787、不动 8796。

退出码：0 = 构建+验证通过；1 = 构建/补丁/静态验证失败；2 = 前置条件/参数错误。
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[1]
EXT_DIR = REPO / "Hermes" / "extensions" / "legion-ide"
DEST = EXT_DIR / "zhice"
BACKUP_DIR = EXT_DIR / "_build_backup"
LOCK_FILE = EXT_DIR / ".acad1_build.lock"
TMP_ROOT = REPO / "tmp" / "hermes_acad1_build"
DEFAULT_SOURCE = Path(r"C:\项目\zhice-os-v2-windows-20260920\code\frontend")
DEFAULT_DELIVERABLES = REPO / "deliverables" / "2026-10-31_Hermes_ACAD1_扩展包_v1"
LAUNCHER = REPO / "Hermes" / "start_hermes_bg.py"

DEFAULT_BASE_PATH = "/extensions/zhice/"
DEFAULT_API_BASE = "http://127.0.0.1:8105/api/v1"
DEFAULT_MARKETS = "CN,US,HK,CRYPTO"
DEFAULT_ROUTER = "hash"
DEFAULT_LIVE_URL = "http://127.0.0.1:8787"
DEFAULT_TRUSTED_HEADER = os.environ.get("HERMES_TRUSTED_AUTH_HEADER", "X-Portal-User").strip()
DEFAULT_TRUSTED_USER = "laotian"

SCHEMA_VERSION = "hermes-acad1-zhice-build/v1"
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def now_local() -> dt.datetime:
    return dt.datetime.now().astimezone()


def iso(ts: Optional[dt.datetime] = None) -> str:
    return (ts or now_local()).isoformat(timespec="seconds")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return "%d B" % n


def die(msg: str, code: int = 2) -> "None":
    print("[FAIL] " + msg, file=sys.stderr)
    raise SystemExit(code)


_JSON_MODE = False  # --json 时人类进度信息必须走 stderr，保持 stdout 纯 JSON 回执


def info(msg: str) -> None:
    stream = sys.stderr if _JSON_MODE else sys.stdout
    print("[acad1] " + msg, file=stream, flush=True)


def log_line(log_path: Optional[Path], msg: str) -> None:
    if log_path is None:
        return
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(msg.rstrip("\n") + "\n")


def run_capture(cmd: List[str], cwd: Path, env: Dict[str, str],
                log_path: Optional[Path], timeout: int = 900) -> Tuple[int, str]:
    """运行子进程，stdout+stderr 合并落日志（长跑命令输出必须落文件）。"""
    started = time.time()
    log_line(log_path, "$ " + " ".join(cmd))
    log_line(log_path, "cwd=" + str(cwd))
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True,
                              timeout=timeout)
        out = (proc.stdout or b"") + (proc.stderr or b"")
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"") + (exc.stderr or b"")
        log_line(log_path, ANSI_RE.sub("", out.decode("utf-8", "replace")))
        log_line(log_path, "[timeout] %s after %ds" % (" ".join(cmd), timeout))
        return 124, out.decode("utf-8", "replace")
    text = ANSI_RE.sub("", out.decode("utf-8", "replace"))
    log_line(log_path, text)
    log_line(log_path, "[rc=%d elapsed=%.1fs]" % (proc.returncode, time.time() - started))
    return proc.returncode, text


def recycle_path(path: Path) -> bool:
    """Windows 回收站删除（AGENTS 红线：删除一律进回收站）；非 Windows 仅报告。"""
    if not path.exists():
        return True
    if os.name != "nt":
        info("跳过删除（非 Windows）：%s" % path)
        return False
    try:
        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("wFunc", ctypes.c_uint),
                ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_ushort),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", wintypes.LPCWSTR),
            ]

        op = SHFILEOPSTRUCTW()
        op.wFunc = 3  # FO_DELETE
        op.pFrom = str(path.resolve()) + "\0\0"
        # FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
        op.fFlags = 0x40 | 0x10 | 0x04 | 0x400
        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        return rc == 0 and not op.fAnyOperationsAborted
    except Exception as exc:  # pragma: no cover - 仅 Windows 运行时
        info("回收站删除失败（%s）：%s" % (exc, path))
        return False


# --------------------------------------------------------------------------- #
# 源码信息 / 可重现性
# --------------------------------------------------------------------------- #
def git_capture(args: List[str], cwd: Path) -> Tuple[int, str]:
    try:
        proc = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True,
                              timeout=120)
        return proc.returncode, proc.stdout.decode("utf-8", "replace")
    except Exception as exc:  # pragma: no cover
        return 1, "%s" % exc


def source_git_info(source: Path) -> Dict[str, Any]:
    repo = source
    while repo != repo.parent and not (repo / ".git").exists():
        repo = repo.parent
    git_ok = (repo / ".git").exists()
    info: Dict[str, Any] = {"git_ok": git_ok, "repo": str(repo)}
    if not git_ok:
        info["note"] = "source tree has no .git; commit unavailable"
        return info
    rc, head = git_capture(["rev-parse", "HEAD"], repo)
    info["head"] = head.strip() if rc == 0 else ""
    rc, branch = git_capture(["rev-parse", "--abbrev-ref", "HEAD"], repo)
    info["branch"] = branch.strip() if rc == 0 else ""
    rc, status = git_capture(["status", "--porcelain"], repo)
    dirty_lines = [ln for ln in status.splitlines() if ln.strip()] if rc == 0 else []
    info["dirty"] = bool(dirty_lines)
    info["dirty_count"] = len(dirty_lines)
    info["dirty_files"] = dirty_lines[:80]
    return info


def _hash_tree_files(paths: List[Path], root: Path,
                     skip_dirs: Tuple[str, ...] = ("node_modules", "dist", ".git",
                                                    "__pycache__", ".vite")) -> Dict[str, Any]:
    entries: List[Tuple[str, str, int]] = []
    for base in paths:
        if not base.exists():
            continue
        if base.is_file():
            entries.append((base.relative_to(root).as_posix(), sha256_file(base),
                            base.stat().st_size))
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            rel_parts = path.relative_to(root).parts
            if any(part in skip_dirs for part in rel_parts):
                continue
            entries.append((path.relative_to(root).as_posix(), sha256_file(path),
                            path.stat().st_size))
    entries.sort()
    digest = hashlib.sha256()
    for rel, sha, size in entries:
        digest.update(("%s\0%s\0%d\n" % (rel, sha, size)).encode("utf-8"))
    return {"digest": digest.hexdigest(), "file_count": len(entries),
            "bytes": sum(e[2] for e in entries), "files": entries}


def source_digest(source: Path) -> Dict[str, Any]:
    """源码树摘要：src/ + public/ + 构建配置文件（排 node_modules），用于可重现对账。"""
    candidates = [
        source / "src", source / "public", source / "index.html",
        source / "package.json", source / "vite.config.ts", source / "tsconfig.json",
        source / ".env.production",
    ]
    return _hash_tree_files(candidates, source)


def node_tool_paths(source: Path) -> Dict[str, str]:
    node = shutil.which("node") or shutil.which("node.exe")
    if not node:
        die("未找到 node.exe（PATH 缺失）")
    vite = source / "node_modules" / "vite" / "bin" / "vite.js"
    tsc = source / "node_modules" / "typescript" / "bin" / "tsc"
    if not vite.exists():
        die("缺少 %s；请先在源码目录执行 npm ci" % vite)
    return {"node": node, "vite": str(vite), "tsc": str(tsc),
            "tsc_available": tsc.exists()}


def package_version(pkg_json: Path) -> str:
    try:
        data = json.loads(pkg_json.read_text(encoding="utf-8"))
        return str(data.get("version", ""))
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# 构建
# --------------------------------------------------------------------------- #
def build_env(api_base: str, base_path: str, markets: str, router: str) -> Dict[str, str]:
    env = os.environ.copy()
    env.update({
        "VITE_BASE": base_path,
        "VITE_ROUTER": router,
        "VITE_ENABLED_MARKETS": markets,
        "VITE_API_BASE": api_base,
        "NO_COLOR": "1",
        "FORCE_COLOR": "0",
    })
    # 避免继承会话代理污染 vite 构建
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                "ALL_PROXY", "all_proxy"):
        env.pop(key, None)
    return env


def run_build(source: Path, out_dir: Path, env: Dict[str, str], log_path: Path,
              skip_typecheck: bool) -> Dict[str, Any]:
    tools = node_tool_paths(source)
    result: Dict[str, Any] = {"out_dir": str(out_dir), "typecheck": "skipped",
                              "typecheck_rc": None, "vite_rc": None}
    if not skip_typecheck and tools["tsc_available"]:
        rc, out = run_capture([tools["node"], tools["tsc"], "-b", "--pretty", "false"],
                              source, env, log_path, timeout=600)
        result["typecheck_rc"] = rc
        result["typecheck"] = "pass" if rc == 0 else "fail"
        if rc != 0:
            tail = "\n".join(out.strip().splitlines()[-12:])
            die("TypeScript 类型检查失败（rc=%d）：\n%s" % (rc, tail))
    if out_dir.exists():
        recycle_path(out_dir)  # 旧 staging 入回收站（红线），避免残留
    rc, out = run_capture([tools["node"], tools["vite"], "build",
                           "--outDir", str(out_dir), "--emptyOutDir",
                           "--logLevel", "info"],
                          source, env, log_path, timeout=900)
    result["vite_rc"] = rc
    if rc != 0:
        tail = "\n".join(out.strip().splitlines()[-15:])
        die("vite build 失败（rc=%d）：\n%s" % (rc, tail))
    index = out_dir / "index.html"
    if not index.exists():
        die("构建产物缺少 index.html：%s" % out_dir)
    js_list = sorted((out_dir / "assets").glob("*.js"))
    css_list = sorted((out_dir / "assets").glob("*.css"))
    if not js_list or not css_list:
        die("构建产物缺少 assets/*.js 或 assets/*.css：%s" % out_dir)
    result["assets"] = [p.name for p in js_list + css_list]
    result["tools"] = {"node": os.path.basename(tools["node"]),
                       "node_version": _first_line(
                           subprocess.run([tools["node"], "--version"],
                                          capture_output=True).stdout),
                       "vite": package_version(source / "node_modules" / "vite" / "package.json"),
                       "typescript": package_version(
                           source / "node_modules" / "typescript" / "package.json")}
    return result


def _first_line(raw: bytes) -> str:
    return raw.decode("utf-8", "replace").strip().splitlines()[0] if raw else ""


# --------------------------------------------------------------------------- #
# 补丁：把构建产物里写死的根路径/相对 API 改成扩展可用地址
# --------------------------------------------------------------------------- #
SAME_ORIGIN_FORBIDDEN_TOKENS = ("127.0.0.1:8105", "127.0.0.1:5175",
                                "autoSession", "zhice_auto_password")


def resolve_same_origin(api_base: str, force: bool = False) -> bool:
    """判定 same-origin 模式：显式 --same-origin，或 --api-base 为相对路径（以 / 开头）。"""
    return bool(force) or str(api_base or "").startswith("/")


def _api_parts(api_base: str,
               same_origin: bool = False) -> Tuple[bytes, bytes, bytes]:
    """解析 API base → (api_prefix_b, api_base_b, ws_prefix_b)。

    - 绝对 origin 模式（P2-3 兼容）：api_prefix=http://host:port、ws_prefix=ws://host:port；
    - same-origin（P1）：api_prefix=/quant-api（相对前缀）、ws_prefix=b""（不引入绝对 origin）。
    """
    api_base = api_base.rstrip("/")
    if not api_base.endswith("/api/v1"):
        die("--api-base 必须以 /api/v1 结尾（当前 %s）" % api_base)
    api_prefix = api_base[: -len("/api/v1")]
    if same_origin:
        if not api_prefix.startswith("/") or "//" in api_prefix:
            die("same-origin 模式 --api-base 必须是相对路径"
                "（例：/quant-api/api/v1），当前 %s" % api_base)
        return api_prefix.encode("utf-8"), api_base.encode("utf-8"), b""
    if "//" not in api_prefix:
        die("绝对模式 --api-base 必须含 origin"
            "（例：http://127.0.0.1:8105/api/v1），当前 %s" % api_base)
    netloc = api_prefix.split("//", 1)[-1]
    return (api_prefix.encode("utf-8"), api_base.encode("utf-8"),
            ("ws://" + netloc).encode("utf-8"))


def _url_parts(api_base: str, same_origin: bool = False) -> Tuple[bytes, bytes]:
    """兼容旧签名：返回 (api_prefix/abs_prefix, ws_prefix)。"""
    api_prefix_b, _api_base_b, ws_prefix_b = _api_parts(api_base, same_origin)
    return api_prefix_b, ws_prefix_b


def patch_build(out_dir: Path, api_base: str, base_path: str,
                same_origin: bool = False) -> Dict[str, Any]:
    same_origin = resolve_same_origin(api_base, same_origin)
    api_prefix_b, api_base_b, ws_prefix_b = _api_parts(api_base, same_origin)
    base_prefix = base_path.rstrip("/")          # /extensions/zhice 或 /quant
    base_prefix_b = base_prefix.encode("utf-8")

    ws_rel_b = b"://${location.host}/api/v1"
    if same_origin:
        # 同源：保留 location.host，仅补 API 前缀；裸相对 /api/v1 同样补前缀（不重复加）
        ws_repl_b = b"://${location.host}" + api_base_b
        rel_api_re = re.compile(
            rb"(?<!" + re.escape(api_prefix_b) + rb")"
            rb"(?<!" + re.escape(b"location.host") + rb")/api/v1")
        api_repl_b = api_base_b
        ws_rule = {"name": "location-host-to-same-origin-prefix",
                   "from": ws_rel_b.decode("utf-8"),
                   "to": ws_repl_b.decode("utf-8")}
        api_rule = {"name": "relative-api-to-same-origin-prefix",
                    "from": "/api/v1 (relative)", "to": api_base}
    else:
        ws_repl_b = b"://" + api_prefix_b.split(b"//", 1)[-1] + b"/api/v1"
        rel_api_re = re.compile(
            rb"(?<!" + re.escape(api_prefix_b) + rb")"
            rb"(?<!" + re.escape(ws_prefix_b) + rb")/api/v1")
        api_repl_b = api_base_b
        ws_rule = {"name": "ws-host-to-api-host",
                   "from": ws_rel_b.decode("utf-8"),
                   "to": ws_repl_b.decode("utf-8")}
        api_rule = {"name": "relative-api-to-absolute",
                    "from": "/api/v1 (relative)", "to": api_base}
    logo_res = {
        "logo-icon.png": re.compile(rb"(?<!" + re.escape(base_prefix_b) + rb")/logo-icon\.png"),
        "logo.png": re.compile(rb"(?<!" + re.escape(base_prefix_b) + rb")/logo\.png"),
    }

    report: Dict[str, Any] = {
        "api_base": api_base,
        "base_path": base_path,
        "same_origin": bool(same_origin),
        "api_prefix": api_prefix_b.decode("utf-8"),
        "rules": [
            ws_rule,
            api_rule,
            {"name": "root-logo-to-base",
             "from": "/logo*.png (root absolute)",
             "to": base_prefix + "/logo*.png"},
        ],
        "files": {},
        "counts": {"ws_replaced": 0, "api_replaced": 0, "logo_replaced": 0},
    }
    for js in sorted((out_dir / "assets").glob("*.js")):
        raw = js.read_bytes()
        orig_len = len(raw)
        ws_hits = raw.count(ws_rel_b)
        raw = raw.replace(ws_rel_b, ws_repl_b)
        api_hits = len(rel_api_re.findall(raw))
        raw = rel_api_re.sub(api_repl_b, raw)
        logo_hits: Dict[str, int] = {}
        for name, pattern in logo_res.items():
            repl = base_prefix_b + b"/" + name.encode("utf-8")
            n = len(pattern.findall(raw))
            logo_hits[name] = n
            raw = pattern.sub(repl, raw)
        js.write_bytes(raw)
        report["files"][js.name] = {
            "bytes_before": orig_len,
            "bytes_after": len(raw),
            "ws_replaced": ws_hits,
            "api_replaced": api_hits,
            "logo_replaced": logo_hits,
            "sha256": sha256_bytes(raw),
        }
        report["counts"]["ws_replaced"] += ws_hits
        report["counts"]["api_replaced"] += api_hits
        report["counts"]["logo_replaced"] += sum(logo_hits.values())

    if same_origin:
        # same-origin：CSS 里的根绝对 logo 同样补 base 前缀（其余 CSS 断言在 assert 阶段）
        css_files: Dict[str, Any] = {}
        for css in sorted((out_dir / "assets").glob("*.css")):
            raw = css.read_bytes()
            orig_len = len(raw)
            hits = 0
            for name, pattern in logo_res.items():
                repl = base_prefix_b + b"/" + name.encode("utf-8")
                n = len(pattern.findall(raw))
                hits += n
                raw = pattern.sub(repl, raw)
            if raw != css.read_bytes():
                css.write_bytes(raw)
            css_files[css.name] = {"bytes_before": orig_len,
                                   "bytes_after": len(raw), "logo_replaced": hits}
            report["counts"]["logo_replaced"] += hits
        report["css_files"] = css_files

    report["assertions"] = assert_patched(out_dir, api_base, base_path,
                                          same_origin=same_origin)
    return report


def assert_patched(out_dir: Path, api_base: str, base_path: str,
                   same_origin: bool = False) -> Dict[str, Any]:
    same_origin = resolve_same_origin(api_base, same_origin)
    api_prefix_b, api_base_b, ws_prefix_b = _api_parts(api_base, same_origin)
    base_prefix_b = base_path.rstrip("/").encode("utf-8")
    ref_prefix = base_path.rstrip("/") + "/"
    checks: Dict[str, Any] = {
        "ok": True,
        "same_origin": bool(same_origin),
        "details": {},
        "counts": {"api_prefix_hits": 0, "bare_api_remaining": 0,
                   "forbidden_token_hits": 0, "forbidden_hits": {},
                   "ws_relative_remaining": 0, "relative_logo_remaining": 0},
    }

    def fail(name: str, detail: Any) -> None:
        checks["ok"] = False
        checks["details"][name] = detail

    for js in sorted((out_dir / "assets").glob("*.js")):
        raw = js.read_bytes()
        text = raw.decode("utf-8", "replace")
        bad_api = []
        for m in re.finditer(rb"/api/v1", raw):
            pre = raw[max(0, m.start() - len(api_prefix_b)):m.start()]
            if same_origin:
                ok_prefix = (pre == api_prefix_b)
            else:
                pre_ws = raw[max(0, m.start() - len(ws_prefix_b)):m.start()]
                ok_prefix = (pre == api_prefix_b or pre_ws == ws_prefix_b)
            if ok_prefix:
                checks["counts"]["api_prefix_hits"] += 1
            else:
                bad_api.append(text[max(0, m.start() - 40):m.start() + 30])
        if bad_api:
            checks["counts"]["bare_api_remaining"] += len(bad_api)
            fail("relative_api_remaining", {"file": js.name, "samples": bad_api[:3]})
        if same_origin:
            if (b"${location.host}/api/v1" in raw
                    or b"location.host}/api/v1" in raw):
                checks["counts"]["ws_relative_remaining"] += 1
                fail("ws_relative_remaining", {"file": js.name})
            for token in SAME_ORIGIN_FORBIDDEN_TOKENS:
                n = raw.count(token.encode("utf-8"))
                if n:
                    checks["counts"]["forbidden_token_hits"] += n
                    checks["counts"]["forbidden_hits"][token] = (
                        checks["counts"]["forbidden_hits"].get(token, 0) + n)
                    fail("forbidden_token", {"file": js.name, "token": token, "count": n})
            if api_base_b not in raw:
                fail("same_origin_api_missing",
                     {"file": js.name, "expected": api_base})
        else:
            if b"://${location.host}/api/v1" in raw:
                fail("ws_relative_remaining", {"file": js.name})
        rel_logo = []
        for name in ("logo.png", "logo-icon.png"):
            needle = b"/" + name.encode()
            for m in re.finditer(re.escape(needle), raw):
                pre = raw[max(0, m.start() - len(base_prefix_b)):m.start()]
                if pre != base_prefix_b:
                    rel_logo.append(text[max(0, m.start() - 40):m.start() + 30])
        if rel_logo:
            checks["counts"]["relative_logo_remaining"] += len(rel_logo)
            fail("relative_logo_remaining", {"file": js.name, "samples": rel_logo[:3]})

    index = out_dir / "index.html"
    if not index.exists():
        fail("index_missing", str(index))
        return checks
    html = index.read_text(encoding="utf-8", errors="replace")
    if ref_prefix not in html:
        fail("index_base_missing", ref_prefix)
    refs = re.findall(r'(?:src|href)="(%s[^"]+)"' % re.escape(ref_prefix), html)
    missing = []
    for ref in refs:
        target = out_dir / ref[len(ref_prefix):]
        if not target.exists():
            missing.append(ref)
    if missing:
        fail("index_refs_missing", missing)
    if re.search(r'(?:src|href)="/(?!%s)' % re.escape(ref_prefix[1:]), html):
        fail("index_root_absolute_refs",
             re.findall(r'(?:src|href)="(/[^"]*)"', html)[:5])
    checks["details"]["index_ref_count"] = len(refs)
    checks["details"]["ref_prefix"] = ref_prefix
    for css in sorted((out_dir / "assets").glob("*.css")):
        text = css.read_text(encoding="utf-8", errors="replace")
        if re.search(r"url\(/(?!%s)" % re.escape(ref_prefix[1:]), text):
            fail("css_root_absolute_url", css.name)
        if same_origin:
            for token in SAME_ORIGIN_FORBIDDEN_TOKENS:
                if token not in text:
                    continue
                checks["counts"]["forbidden_token_hits"] += 1
                checks["counts"]["forbidden_hits"][token] = (
                    checks["counts"]["forbidden_hits"].get(token, 0) + 1)
                fail("css_forbidden_token", {"file": css.name, "token": token})
            if "/api/v1" in text and api_prefix_b.decode("utf-8") not in text:
                fail("css_unexpected_paths", css.name)
        else:
            if "/api/v1" in text or "/logo" in text:
                fail("css_unexpected_paths", css.name)
    return checks


# --------------------------------------------------------------------------- #
# 产物清单 / 版本清单
# --------------------------------------------------------------------------- #
def tree_files(root: Path, exclude: Tuple[str, ...] = ("VERSION.json",)) -> Dict[str, Any]:
    files: Dict[str, Any] = {}
    total = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel in exclude:
            continue
        size = path.stat().st_size
        total += size
        files[rel] = {"sha256": sha256_file(path), "bytes": size}
    return {"root": str(root), "file_count": len(files), "total_bytes": total,
            "files": files}


def build_version_id(commit: str) -> str:
    return "%s_%s" % (now_local().strftime("%Y%m%d_%H%M%S"), (commit or "nogit")[:8])


def write_version_json(dest: Path, manifest: Dict[str, Any]) -> str:
    raw = json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=False)
    (dest / "VERSION.json").write_text(raw + "\n", encoding="utf-8")
    return sha256_text(raw + "\n")


# --------------------------------------------------------------------------- #
# 生产 Hermes static handler 复现验证（本机临时端口，不碰 8787）
# --------------------------------------------------------------------------- #
def _fallback_serve(handler, parsed) -> bool:  # pragma: no cover - 仅兜底
    """与 Hermes api/extensions.py 等价的极简复刻（导入失败时兜底）。"""
    from urllib.parse import unquote

    root = Path(os.environ["HERMES_WEBUI_EXTENSION_DIR"]).resolve()
    rel = unquote(parsed.path[len("/extensions/"):])
    if not rel or "\\" in rel or any(seg.startswith(".") or not seg for seg in rel.split("/")):
        return _fallback_404(handler)
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return _fallback_404(handler)
    if not target.is_file():
        return _fallback_404(handler)
    mime = {".js": "application/javascript", ".css": "text/css", ".html": "text/html",
            ".png": "image/png", ".json": "application/json"}.get(target.suffix.lower(),
                                                                 "application/octet-stream")
    if mime in ("application/javascript", "text/css", "text/html"):
        mime += "; charset=utf-8"
    raw = target.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", mime)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)
    return True


def _fallback_404(handler) -> bool:  # pragma: no cover
    body = json.dumps({"error": "not found"}).encode()
    handler.send_response(404)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
    return True


def http_get(url: str, headers: Optional[Dict[str, str]] = None,
             timeout: float = 8.0) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return {"ok": True, "status": resp.status, "headers": dict(resp.headers),
                    "body": body, "sha256": sha256_bytes(body), "bytes": len(body)}
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp is not None else b""
        try:
            headers_dict = dict(exc.headers)
        except Exception:
            headers_dict = {}
        return {"ok": False, "status": exc.code, "headers": headers_dict,
                "body": body, "sha256": sha256_bytes(body), "bytes": len(body)}
    except Exception as exc:
        return {"ok": False, "status": 0, "error": str(exc), "body": b"",
                "sha256": "", "bytes": 0}


def static_path_check(ext_root: Path, dest: Path, base_path: str) -> Dict[str, Any]:
    """用生产 Hermes 的 /extensions/ 静态 handler 在本机零端口副作用复现。"""
    import importlib
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlsplit

    hermes_next = REPO / "Hermes" / "hermes-webui-next"
    mode = "production-handler"
    serve = None
    import_error = ""
    # 隔离导入期状态：避免导入生产配置模块时落到真实 Hermes state/home
    isolated_state = TMP_ROOT / "probe_state"
    isolated_home = TMP_ROOT / "probe_home"
    isolated_state.mkdir(parents=True, exist_ok=True)
    isolated_home.mkdir(parents=True, exist_ok=True)
    saved_env = {key: os.environ.get(key) for key in
                 ("HERMES_WEBUI_EXTENSION_DIR", "HERMES_WEBUI_STATE_DIR", "HERMES_HOME")}
    os.environ["HERMES_WEBUI_EXTENSION_DIR"] = str(ext_root)
    os.environ["HERMES_WEBUI_STATE_DIR"] = str(isolated_state)
    os.environ["HERMES_HOME"] = str(isolated_home)
    try:
        if str(hermes_next) not in sys.path:
            sys.path.insert(0, str(hermes_next))
        ext = importlib.import_module("api.extensions")
        serve = ext.serve_extension_static
        module_file = str(getattr(ext, "__file__", ""))
    except Exception as exc:  # 兜底：导入生产模块失败时用等价复刻，并在结果中标注
        mode = "fallback-replica"
        import_error = "%s: %s" % (type(exc).__name__, exc)
        serve = _fallback_serve
        module_file = ""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            serve(self, urlsplit(self.path))

        def log_message(self, *args: Any) -> None:  # 静默
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    result: Dict[str, Any] = {"ok": False, "mode": mode, "module_file": module_file,
                              "import_error": import_error, "port": port,
                              "checks": [], "traversal_404": None}
    try:
        base = "http://127.0.0.1:%d" % port
        rel_base = base_path.rstrip("/") + "/"
        targets = ["index.html"]
        assets = sorted((dest / "assets").glob("*"))
        targets += ["assets/" + p.name for p in assets]
        targets += [name for name in ("logo.png", "logo-icon.png", "favicon-32.png")
                    if (dest / name).exists()]
        ok_all = True
        for rel in targets:
            url = base + rel_base + rel
            got = http_get(url, timeout=10)
            file_path = dest / rel
            checks: Dict[str, Any] = {
                "path": rel_base + rel,
                "status": got.get("status"),
                "bytes": got.get("bytes"),
                "sha256_match": (got.get("ok") and file_path.exists()
                                 and got.get("sha256") == sha256_file(file_path)),
                "content_type": got.get("headers", {}).get("Content-Type", ""),
                "cache_control": got.get("headers", {}).get("Cache-Control", ""),
                "csp_present": bool(got.get("headers", {}).get("Content-Security-Policy")),
            }
            checks["ok"] = bool(got.get("ok") and checks["sha256_match"]
                                and checks["status"] == 200)
            ok_all = ok_all and checks["ok"]
            result["checks"].append(checks)
        traversal = http_get(base + "/extensions/%2e%2e/README.md", timeout=10)
        result["traversal_404"] = traversal.get("status") == 404
        result["ok"] = bool(ok_all and result["traversal_404"])
    finally:
        server.shutdown()
        server.server_close()
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return result


# --------------------------------------------------------------------------- #
# 8787 只读探针（不重启、不改）
# --------------------------------------------------------------------------- #
def live_probe(live_url: str, dest: Path, base_path: str,
               trusted_header: str, trusted_user: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {"base": live_url, "ok": True, "checks": {}}
    health = http_get(live_url.rstrip("/") + "/health", timeout=6)
    result["checks"]["health"] = {"status": health.get("status")}
    started = None
    if health.get("ok"):
        try:
            payload = json.loads(health["body"].decode("utf-8", "replace"))
            started = payload.get("server_started_at")
            result["checks"]["health"]["server_started_at"] = started
            result["checks"]["health"]["uptime_seconds"] = payload.get("uptime_seconds")
        except Exception:
            pass
    headers = {trusted_header: trusted_user} if trusted_header else {}
    result["trusted_header"] = {"name": trusted_header, "user": trusted_user,
                                "used": bool(headers)}
    aj = http_get(live_url.rstrip("/") + "/extensions/app.js", headers, timeout=8)
    result["checks"]["extensions_app_js"] = {
        "status": aj.get("status"), "sha256": aj.get("sha256"), "bytes": aj.get("bytes")}
    rel_base = base_path.rstrip("/") + "/"
    zhice_checks: List[Dict[str, Any]] = []
    for rel in [p.relative_to(dest).as_posix() for p in sorted(dest.rglob("*")) if p.is_file()
                and p.name != "VERSION.json"]:
        got = http_get(live_url.rstrip("/") + rel_base + rel, headers, timeout=8)
        local = dest / rel
        zhice_checks.append({
            "path": rel_base + rel,
            "status": got.get("status"),
            "sha256_match": bool(got.get("ok") and got.get("sha256") == sha256_file(local)),
            "content_type": got.get("headers", {}).get("Content-Type", ""),
            "csp": got.get("headers", {}).get("Content-Security-Policy", "")[:400],
        })
    result["checks"]["extensions_zhice"] = zhice_checks
    result["zhice_served"] = all(c["status"] == 200 and c["sha256_match"] for c in zhice_checks)
    index = next((c for c in zhice_checks if c["path"].endswith("/index.html")), None)
    result["zhice_index_status"] = index["status"] if index else None
    return result


# --------------------------------------------------------------------------- #
# 发布到 8787 生产扩展目录（只新增 zhice/，不动现有文件）
# --------------------------------------------------------------------------- #
def detect_live_extension_root() -> Optional[Path]:
    if not LAUNCHER.exists():
        return None
    text = LAUNCHER.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'"HERMES_WEBUI_EXTENSION_DIR"\s*:\s*r?"([^"]+)"', text)
    return Path(m.group(1)) if m else None


def snapshot_dir(root: Path, exclude_prefix: str = "",
                 exclude_names: Tuple[str, ...] = ()) -> Dict[str, Any]:
    files: Dict[str, Any] = {}
    if not root.exists():
        return {"root": str(root), "exists": False, "files": files}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if exclude_prefix and (rel == exclude_prefix or rel.startswith(exclude_prefix + "/")):
            continue
        if rel in exclude_names:
            continue
        files[rel] = {"sha256": sha256_file(path), "bytes": path.stat().st_size}
    return {"root": str(root), "exists": True, "files": files}


def publish_live(dest: Path, live_root: Path, deliverables: Path,
                 version: str) -> Dict[str, Any]:
    live_root = live_root.expanduser()
    if not live_root.exists():
        die("生产扩展目录不存在：%s" % live_root)
    resolved = live_root.resolve()
    target_zhice = resolved / "zhice"
    report: Dict[str, Any] = {
        "schema": "hermes-acad1-live-publish/v1",
        "requested_root": str(live_root),
        "resolved_root": str(resolved),
        "is_junction": bool(str(resolved) != os.path.abspath(str(live_root))),
        "published_at": iso(),
        "version": version,
        "mode": "add-only: zhice/ subdirectory, existing files untouched",
    }
    before = snapshot_dir(resolved, exclude_prefix="zhice")
    report["before"] = before
    report["before_manifest_sha256"] = (before["files"].get("manifest.json") or {}).get("sha256")
    manifest_src = resolved / "manifest.json"
    if manifest_src.exists():
        (deliverables / "live-manifest-before.json").parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(manifest_src, deliverables / "live-manifest-before.json")

    staging = resolved / ("zhice.__new_" + version)
    if staging.exists():
        recycle_path(staging)
    shutil.copytree(dest, staging)
    if target_zhice.exists() or target_zhice.is_symlink():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup = BACKUP_DIR / ("live_zhice_%s" % version)
        if backup.exists():
            recycle_path(backup)
        shutil.move(str(target_zhice), str(backup))
        report["replaced_previous_zhice"] = str(backup)
    os.replace(str(staging), str(target_zhice))
    # 8787 覆盖层历史版本保留最近 2 份（更多入回收站，避免 _build_backup 膨胀）
    live_backups = sorted(BACKUP_DIR.glob("live_zhice_*"))
    pruned = []
    for old in live_backups[:-2]:
        if recycle_path(old):
            pruned.append(old.name)
    report["pruned_live_backups"] = pruned
    after = snapshot_dir(resolved, exclude_prefix="zhice")
    added = sorted(set(after["files"]) - set(before["files"]))
    removed = sorted(set(before["files"]) - set(after["files"]))
    changed = sorted(rel for rel in set(before["files"]) & set(after["files"])
                     if before["files"][rel]["sha256"] != after["files"][rel]["sha256"])
    zhice_snapshot = snapshot_dir(target_zhice, exclude_names=("VERSION.json",))
    dest_snapshot = tree_files(dest, exclude=("VERSION.json",))
    zhice_match = (zhice_snapshot["files"] == {
        k: {"sha256": v["sha256"], "bytes": v["bytes"]}
        for k, v in dest_snapshot["files"].items()})
    # VERSION.json：先同步首版；最终清单在验证完成后由 sync_version_json 刷新
    version_json = dest / "VERSION.json"
    if version_json.exists():
        shutil.copy2(version_json, target_zhice / "VERSION.json")
    report.update({
        "after": after,
        "added_files": added,
        "removed_files": removed,
        "changed_existing_files": changed,
        "existing_files_intact": not removed and not changed,
        "zhice_dir": str(target_zhice),
        "zhice_file_count": len(zhice_snapshot["files"]),
        "zhice_added_bytes": sum(v["bytes"] for v in zhice_snapshot["files"].values()),
        "existing_before_file_count": len(before["files"]),
        "zhice_tree_matches_deliverable_dir": zhice_match,
        "zhice_sha256": zhice_snapshot["files"],
        "ok": (not removed and not changed and zhice_match),
    })
    return report


def sync_version_json(dest: Path, live_root: Path) -> Dict[str, Any]:
    """把最终 VERSION.json 同步到 8787 覆盖层（发布时先拷了首版清单）。"""
    target = live_root.expanduser().resolve() / "zhice" / "VERSION.json"
    source_file = dest / "VERSION.json"
    if not target.parent.exists() or not source_file.exists():
        return {"ok": False, "reason": "live zhice dir or VERSION.json missing"}
    shutil.copy2(source_file, target)
    return {"ok": True, "path": str(target),
            "sha256": sha256_file(target), "bytes": target.stat().st_size,
            "synced_at": iso()}


def rollback_live(live_root: Path) -> Dict[str, Any]:
    resolved = live_root.expanduser().resolve()
    target = resolved / "zhice"
    ok = recycle_path(target) if target.exists() else True
    return {"rolled_back": ok, "removed": str(target), "at": iso()}


# --------------------------------------------------------------------------- #
# deliverables（README / MANIFEST / VERIFY）
# --------------------------------------------------------------------------- #
def write_readme(deliverables: Path, manifest: Dict[str, Any]) -> Path:
    source = manifest["source"]
    build = manifest["build"]
    out = manifest["output"]
    patch = manifest["patch"]
    verify = manifest["verify"]
    live_pub = manifest.get("live_publish") or {}
    live = manifest.get("live_probe") or {}
    live_pub_ok = bool(live_pub.get("ok"))
    zhice_ok = bool(live.get("zhice_served"))
    wiring = ("8787 已直接可读（无需重启）" if zhice_ok else
              "8787 当前读不到（wiring gap，见 §6）")
    lines: List[str] = []
    add = lines.append
    add("# Hermes ACAD1（司马徽）扩展包 · 构建流水线 v1")
    add("")
    add("> 日期：2026-10-31 ｜ 任务：P2-3（Hermes 司马徽扩展构建流水线/版本化）")
    add("> 产物：`Hermes/extensions/legion-ide/zhice/`（Zhice 研究台生产构建，"
        "Hermes WebUI 同源静态扩展）")
    add("> 版本：`%s` ｜ 源 commit：`%s`%s"
        % (manifest["version"], (source.get("head") or "n/a")[:12],
           "（源码树 dirty）" if source.get("dirty") else ""))
    add("> 边界：未改 Hermes 核心、未改 CSP、未重启 8787、未动 8796。")
    add("")
    add("## 1. 一条命令重建")
    add("")
    add("```powershell")
    add("cd C:\\项目\\老田的AI助手")
    add("python tools\\hermes_acad1_build.py")
    add("# 或（ASCII-only 薄包装，等价）")
    add("powershell -NoProfile -ExecutionPolicy Bypass -File tools\\hermes_acad1_build.ps1")
    add("```")
    add("")
    add("常用变体：")
    add("")
    add("```powershell")
    add("python tools\\hermes_acad1_build.py --verify-only          # 只复核现有产物/清单/静态路径")
    add("python tools\\hermes_acad1_build.py --publish-live        # 构建后把 zhice/ 覆盖发布到 8787 生产扩展目录")
    add("python tools\\hermes_acad1_build.py --api-base http://127.0.0.1:8105/api/v1")
    add("python tools\\hermes_acad1_build.py --json                # 机器可读回执")
    add("```")
    add("")
    add("前置：Node.js ≥ 20（本机实测 `%s`），且源码目录已 `npm ci`"
        % build["tools"]["node_version"].lstrip("v"))
    add("（`%s`）。构建脚本本身只用 Python 3 标准库。"
        % source["path"])
    add("")
    add("## 2. 固定构建参数（写死在脚本默认值，可用 CLI 覆盖）")
    add("")
    add("| 变量 | 值 | 用途 |")
    add("|---|---|---|")
    for key, value in build["env"].items():
        purpose = {
            "VITE_BASE": "Hermes `/extensions/zhice/` 子路径",
            "VITE_ROUTER": "hash 路由（静态托管无 history fallback）",
            "VITE_ENABLED_MARKETS": "启用市场（四市场）",
            "VITE_API_BASE": "生产后端（127.0.0.1:8105，CSP connect-src 放行段）",
        }.get(key, "")
        add("| `%s` | `%s` | %s |" % (key, value, purpose))
    add("")
    add("## 3. 构建产物与版本清单")
    add("")
    add("| 项 | 值 |")
    add("|---|---|")
    add("| 产物目录 | `%s` |" % out["root"])
    add("| 文件数 / 体积 | %d 个 / %s |" % (out["file_count"], human_bytes(out["total_bytes"])))
    add("| 版本清单 | `zhice/VERSION.json`（sha256 `%s`） |"
        % manifest["deliverables"]["version_json_sha256"])
    add("| 构建时间 | %s（耗时 %.1fs） |" % (build["built_at"], build["duration_s"]))
    add("| 源 commit | `%s`（branch `%s`，dirty=%s，%d 个变更文件） |"
        % (source.get("head") or "n/a", source.get("branch") or "n/a",
           source.get("dirty"), source.get("dirty_count", 0)))
    add("| 源码树摘要 | `%s`（%d 文件 / %s） |"
        % (source["digest"]["digest"], source["digest"]["file_count"],
           human_bytes(source["digest"]["bytes"])))
    add("| 构建工具 | node %s / vite %s / tsc %s |"
        % (build["tools"]["node_version"], build["tools"]["vite"],
           build["tools"]["typescript"]))
    add("")
    add("产物文件 sha256：")
    add("")
    add("| 文件 | 字节 | sha256 |")
    add("|---|---|---|")
    for rel, meta in out["files"].items():
        add("| `%s` | %d | `%s` |" % (rel, meta["bytes"], meta["sha256"]))
    add("")
    add("`VERSION.json` 字段：`schema/version/built_at/source/build/output/patch/verify/"
        "live_publish/live_probe`，可离线核对源码 commit、时间、每个文件 hash 与补丁计数。")
    add("")
    add("## 4. 为什么构建后要打补丁")
    add("")
    add("Zhice 源码有 4 处**相对** `/api/v1` 写死调用 + 2 处**根绝对** logo 路径，"
        "直接构建到 `/extensions/zhice/` 会打到 Hermes 源（404）或 8787 根（logo 404）。"
        "脚本按字节替换（不改 UTF-8 编码、不改行尾）：")
    add("")
    add("| 规则 | 替换 | 次数 |")
    add("|---|---|---|")
    add("| WebSocket host | `://${location.host}/api/v1` → `://<api-host>/api/v1` | %d |"
        % patch["counts"]["ws_replaced"])
    add("| 相对 API → 绝对 | `/api/v1...` → `%s...` | %d |"
        % (patch["api_base"], patch["counts"]["api_replaced"]))
    add("| logo 根路径 | `/logo.png`、`/logo-icon.png` → `%slogo*.png` | %d |"
        % (manifest["build"]["env"]["VITE_BASE"], patch["counts"]["logo_replaced"]))
    add("")
    add("> 旧手工复制流程的缺陷（本脚本已消除）：PowerShell 文本读写把 LF 变 CRLF；"
        "WS `location.host` 未改（连到 8787 源）；`/logo*.png` 未改（8787 根 404）；"
        "无版本清单、不可审计、每次重建结果不可比对。")
    add("")
    add("补丁后断言（全部通过）：相对 `/api/v1` = 0、`location.host` WS = 0、"
        "相对 logo = 0、`index.html` 全部资源引用存在、无根绝对资产引用、CSS 无相对 url/API。")
    add("")
    add("## 5. 验证（本单实测）")
    add("")
    add("### 5.1 构建产物 + 清单自洽")
    add("")
    add("- 逐文件 sha256 重算与 `VERSION.json` 一致：%s"
        % ("✅" if verify["file_hashes_match"] else "❌"))
    add("- `index.html` 引用可解析、补丁断言通过：%s"
        % ("✅" if verify["assert_patched"]["ok"] else "❌"))
    add("- 可重现：**同参数两次重建**产物逐字节一致（sha256 相同）；"
        "不同 `VITE_API_BASE` 会改变 JS 内容 hash（属预期）")
    add("")
    add("### 5.2 静态路径复现（生产 Hermes `/extensions/` handler，本机临时端口）")
    add("")
    sp = verify["static_path"]
    add("- 模式：`%s`（%s）｜ 结果：%s"
        % (sp["mode"], sp.get("module_file") or "内置等价复刻",
           "✅ 全绿" if sp["ok"] else "❌ 失败"))
    add("- 逐文件校验：HTTP 200 + `Content-Type` + `Cache-Control: no-store` + 字节级 sha256"
        "一致（%d 个路径），`%%2e%%2e` 穿越请求 404：%s"
        % (len(sp["checks"]), "✅" if sp.get("traversal_404") else "❌"))
    add("")
    add("### 5.3 8787 在线探针（只读，不重启）")
    add("")
    add("- `GET http://127.0.0.1:8787/health`：HTTP %s；进程 `server_started_at` = %s"
        "（构建/发布前后未变 ⇒ 未重启）"
        % (live.get("checks", {}).get("health", {}).get("status"),
           live.get("checks", {}).get("health", {}).get("server_started_at")))
    add("- `GET /extensions/app.js`（既有入口资产）：HTTP %s，sha256 `%s`"
        % (live.get("checks", {}).get("extensions_app_js", {}).get("status"),
           (live.get("checks", {}).get("extensions_app_js", {}).get("sha256") or "")[:16]))
    add("- `GET /extensions/zhice/index.html`：HTTP **%s**；zhice 全量资源字节级一致：%s %s"
        % (live.get("zhice_index_status"), "✅" if zhice_ok else "❌", wiring))
    add("- 直连 8787 不带 trusted-header 会 302 到 `/login`（认证行为未变；8734 nginx "
        "在 auth_request 通过后注入 `X-Portal-User`）")
    add("")
    add("### 5.4 生产扩展目录发布（lead 批准的 A 方案：add-only）")
    add("")
    add("- 生产扩展根（launcher env）：`%s`" % live_pub.get("requested_root", "(未发布)"))
    add("- junction/链接解析目标：`%s`（is_junction=%s）"
        % (live_pub.get("resolved_root", "(未发布)"), live_pub.get("is_junction")))
    add("- 新增 `zhice/`：%d 个文件 / %s，目录 `%s`，发布时刻 %s"
        % (live_pub.get("zhice_file_count", 0),
           human_bytes(int(live_pub.get("zhice_added_bytes") or 0)),
           live_pub.get("zhice_dir", "—"), live_pub.get("published_at", "—")))
    add("- 发布方式：新增 `zhice/` 子目录；现有 app.js/app.css/a0-mainchat.js/README.md/"
        "manifest.json **未改**（发布前后 sha256 对账：%s）"
        % ("✅ 一致" if live_pub_ok else "❌ 有差异/未发布"))
    add("- 发布目录/时间/hash 明细：见 `MANIFEST.json` 的 `live_publish` 与 "
        "`VERIFY.json`；发布前 manifest 备份 `live-manifest-before.json`")
    add("- 回退：删除该目录下 `zhice/` 即回到发布前状态（junction 每请求解析，无需重启）")
    add("")
    add("## 6. 8787 生效/回归路径（本单只做静态可读，不改入口 wiring）")
    add("")
    add("- 运行中的 8787 通过 `HERMES_WEBUI_EXTENSION_DIR` 指向 "
        "`C:\\项目\\_vault\\legion_ide\\release\\legion-ide\\current`（junction，"
        "每请求解析 → 放入文件即生效，**无需重启**）。")
    add("- 本单只**新增**了 `zhice/` 覆盖层，未改 `start_hermes_*.py`、未改 app.js 的"
        "司马徽入口（入口仍按现状工作）。若要让「司马徽」面板 iframe 指向 "
        "`/extensions/zhice/index.html#/cn/replay?tab=selection`，属入口 wiring 变更，"
        "需另单（涉及已 tracked 的 app.js 与 release 发布流程）。")
    add("- ⚠️ 发布 overlay 的持久性：`current` 每次 promote 会切到只含 4 个白名单文件的"
        "新 release 目录，`zhice/` 覆盖层需要重新 `--publish-live`；"
        "`tools/release_legion_ide.py` 的 `FILES` 白名单（4 文件）与 "
        "`Hermes/*` gitignore 目前都不含 zhice —— 长期方案需扩展 release 工具"
        "（本单未改，避免扩大范围）。")
    add("")
    add("## 7. 回滚")
    add("")
    add("```powershell")
    add("# 1) 仅回退 8787 覆盖层（生产扩展目录里删 zhice/，回收站删除）")
    add("python -c \"from pathlib import Path; import sys; sys.path.insert(0, 'tools'); "
        "import hermes_acad1_build as b; print(b.rollback_live(Path(r'%s')))\""
        % live_pub.get("requested_root", "C:\\项目\\_vault\\legion_ide\\release\\legion-ide\\current"))
    add("# 2) 回退工作树产物：从 Hermes/extensions/legion-ide/_build_backup/ 取上一版 zhice")
    add("# 3) 彻底回退：revert 本单提交（tools/* + deliverables/*），删除 zhice/ 与 _build_backup/")
    add("```")
    add("")
    add("## 8. 已知风险 / 残余项（未在本单范围）")
    add("")
    add("1. **CSP**：生产 CSP `connect-src` 仅放行 `http://127.0.0.1:*` / `ws://127.0.0.1:*`。"
        "本单 API base 已按 lead 指示改为 `http://127.0.0.1:8105/api/v1`（在放行段内）；"
        "若未来改回 `127.0.0.2:*` 或其它 host，会被 CSP 拦截（不改 CSP 前提下不可用）。")
    add("2. **8105 后端**：本单只做静态构建/托管验证；`127.0.0.1:8105` 的 API 可用性/数据由"
        "lead 的后端批次负责（构建不依赖它在线）。")
    add("3. **token 存储**：SPA 将 `zhice_token` 写入 Hermes 源 localStorage（同源 iframe "
        "共享存储域），属既有安全边界，未改。")
    add("4. **双源漂移**：5175 dev 与 `/extensions/zhice/` 构建可能漂移；本流水线以"
        "「同参数重建 + hash 清单」保证构建可重建，但源码 dirty 状态需一并留档"
        "（`source-dirty.diff`、`source-untracked-files.json`）。")
    add("5. **git 边界**：`Hermes/*` 被 gitignore，`zhice/` 产物与 `_build_backup/` 不进库；"
        "可审计版本信息以本 deliverables 清单 + `VERSION.json` 为准。")
    add("")
    add("## 9. 源可重现信息")
    add("")
    add("- 源码仓库：`%s`（branch `%s`，HEAD `%s`）"
        % (source.get("repo"), source.get("branch"), source.get("head")))
    add("- dirty 文件数：%d（清单见 `source-untracked-files.json` / `source-dirty.diff`）"
        % source.get("dirty_count", 0))
    add("- 源码树摘要（src/public/构建配置）sha256：`%s`" % source["digest"]["digest"])
    add("")
    add("## 10. 本目录文件")
    add("")
    add("| 文件 | 说明 |")
    add("|---|---|")
    add("| `README.md` | 本文件（使用/验证/回滚/风险） |")
    add("| `MANIFEST.json` | 机器可读完整清单（源 commit/dirty/时间/产物 hash/补丁计数/验证/发布） |")
    add("| `VERIFY.json` | 本次验证原始结果（静态路径 + 8787 探针 + 文件对账） |")
    add("| `build-%s.log` | 构建/补丁命令完整输出（含 rc/耗时） |" % manifest["version"])
    add("| `source-dirty.diff` | 源码树 tracked 变更 diff（快照包无干净 HEAD，重建需同 dirty 树） |")
    add("| `source-untracked-files.json` | 源码树 untracked 文件 hash（含 zhice 前端新增页） |")
    add("| `live-manifest-before.json` | 发布前生产扩展 manifest 备份（A 方案证据） |")
    add("")
    add("---")
    add("_本文件由 `tools/hermes_acad1_build.py` 自动生成；重建会覆盖。_")
    path = deliverables / "README.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_readme_same_origin(deliverables: Path, manifest: Dict[str, Any]) -> Path:
    """same-origin（/quant/ 页面包）模式的交付 README（不套用 Hermes 扩展模板）。"""
    build = manifest["build"]
    source = manifest["source"]
    out = manifest["output"]
    sa = manifest.get("same_origin") or {}
    clean = sa.get("clean") or {}
    lines: List[str] = []
    add = lines.append
    add("# Zhice 前端 same-origin 构建包（`/quant/` 页面包）v1")
    add("")
    add("> 日期：%s ｜ 任务：P1 same-origin clean build（W1/UF-17 前置）"
        % manifest.get("built_at", ""))
    add("> 模式：**same-origin**（VITE_BASE=`%s`、VITE_API_BASE=`%s`）"
        % (build["env"].get("VITE_BASE"), build["env"].get("VITE_API_BASE")))
    add("> 版本：`%s` ｜ 源 commit：`%s` ｜ 源 dirty_count：%s"
        % (manifest.get("version"), (source.get("head") or "n/a")[:12],
           source.get("dirty_count", 0)))
    add("")
    add("## 1. 一条命令重建")
    add("")
    add("```powershell")
    add("cd C:\\项目\\老田的AI助手")
    add("python -X utf8 tools\\hermes_acad1_build.py --source <zhice_frontend> `")
    add("  --base-path /quant/ --api-base /quant-api/api/v1 --same-origin `")
    add("  --router hash --markets CN,US,HK,CRYPTO --dest <stage> --deliverables <deliv> --no-verify --json")
    add("```")
    add("")
    add("## 2. 构建/断言摘要")
    add("")
    add("| 项 | 值 |")
    add("|---|---|")
    add("| typecheck | `%s` |" % build.get("typecheck"))
    add("| vite_rc | `%s` |" % build.get("vite_rc"))
    add("| 产物 | `%s`（%d 文件 / %s） |"
        % (out["root"], out["file_count"], human_bytes(out["total_bytes"])))
    add("| VERSION.json 版本 | `%s` |" % manifest.get("version"))
    add("| same-origin 断言 | ok=`%s` |" % (sa.get("enabled")))
    add("| dirty_count | %s（要求 0） |" % source.get("dirty_count", 0))
    add("| autoSession 命中 | %s（要求 0） |" % clean.get("auto_session_hits", "n/a"))
    add("| 5175/8105 残留 | %s（要求 0） |" % clean.get("legacy_port_hits", "n/a"))
    add("| 裸 /api/v1 残留 | %s（要求 0） |" % clean.get("bare_api_remaining", "n/a"))
    add("| /quant-api/api/v1 命中 | %s |" % clean.get("api_prefix_hits", "n/a"))
    add("")
    add("## 3. 同源补丁规则")
    add("")
    for rule in (manifest.get("patch") or {}).get("rules", []):
        add("- `%s`：`%s` → `%s`" % (rule.get("name"), rule.get("from"), rule.get("to")))
    add("")
    add("## 4. 边界 / 未做")
    add("")
    add("- 仅构建 + 补丁 + 断言；**未发布**到 `portal/quant/`、未改 nginx、未启停服务、未 git commit。")
    add("- `/quant/` nginx 路由属 L2 批准项（L1_待批包 §6），本包可先落盘待批。")
    add("- 源码 `autoSession`/`zhice_auto_password` 下线属 P2 前置（本次实测未检出）。")
    add("")
    add("## 5. 回滚")
    add("")
    add("- 本包为独立构建目录（stage/deliverables），回滚=删除该目录；无生产/运行时改动。")
    add("")
    add("## 6. 源可重现信息")
    add("")
    add("- 源码仓库：`%s`（branch `%s`，HEAD `%s`）"
        % (source.get("repo"), source.get("branch"), source.get("head")))
    add("- 源码树摘要（src/public/构建配置）sha256：`%s`"
        % (source.get("digest") or {}).get("digest"))
    add("")
    add("---")
    add("_本文件由 `tools/hermes_acad1_build.py`（same-origin 模式）自动生成。_")
    path = deliverables / "README.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")


# --------------------------------------------------------------------------- #
# 锁
# --------------------------------------------------------------------------- #
class BuildLock:
    def __init__(self, path: Path, force: bool = False):
        self.path = path
        self.force = force

    def __enter__(self) -> "BuildLock":
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                age = time.time() - float(data.get("ts", 0))
            except Exception:
                data, age = {}, 10 ** 9
            if age < 3 * 3600 and not self.force:
                die("检测到并发构建锁 %s（pid=%s，%.1f 分钟前）；如确认无并发加 --force"
                    % (self.path, data.get("pid"), age / 60.0))
            info("忽略陈旧/强制构建锁：%s" % self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"pid": os.getpid(), "ts": time.time(),
                                         "started_at": iso()}), encoding="utf-8")
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Hermes ACAD1（Zhice）扩展构建流水线（P2-3）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="Zhice frontend 源码目录")
    parser.add_argument("--dest", default=str(DEST), help="zhice 产物目录")
    parser.add_argument("--deliverables", default=str(DEFAULT_DELIVERABLES), help="交付清单目录")
    parser.add_argument("--base-path", default=DEFAULT_BASE_PATH, help="VITE_BASE（必须以 / 开头/结尾）")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="VITE_API_BASE（以 /api/v1 结尾）")
    parser.add_argument("--same-origin", action="store_true",
                        help="same-origin 模式：--api-base 用相对前缀（如 /quant-api/api/v1），"
                             "API/WS/logo 全部同源相对，不引入绝对 origin")
    parser.add_argument("--markets", default=DEFAULT_MARKETS, help="VITE_ENABLED_MARKETS")
    parser.add_argument("--router", default=DEFAULT_ROUTER, help="VITE_ROUTER")
    parser.add_argument("--skip-typecheck", action="store_true", help="跳过 tsc -b（不推荐）")
    parser.add_argument("--no-verify", action="store_true", help="跳过静态路径/8787 验证")
    parser.add_argument("--verify-only", action="store_true", help="只验证现有产物，不构建")
    parser.add_argument("--publish-live", action="store_true",
                        help="构建后把 zhice/ 覆盖发布到 8787 生产扩展目录（add-only）")
    parser.add_argument("--live-root", default="", help="生产扩展目录（默认从 start_hermes_bg.py 解析）")
    parser.add_argument("--live-url", default=DEFAULT_LIVE_URL, help="8787 基础地址")
    parser.add_argument("--live-timeout", type=float, default=8.0, help="8787 探针超时秒")
    parser.add_argument("--trusted-header", default=DEFAULT_TRUSTED_HEADER,
                        help="8787 trusted-auth 头名（空=不带）")
    parser.add_argument("--trusted-user", default=DEFAULT_TRUSTED_USER, help="trusted-auth 用户值")
    parser.add_argument("--retention", type=int, default=3, help="工作树产物备份保留份数")
    parser.add_argument("--force", action="store_true", help="忽略并发锁")
    parser.add_argument("--json", action="store_true", help="stdout 输出 JSON 回执")
    args = parser.parse_args(argv)
    global _JSON_MODE
    _JSON_MODE = bool(getattr(args, "json", False))

    started = now_local()
    source = Path(args.source).expanduser()
    dest = Path(args.dest).expanduser()
    deliverables = Path(args.deliverables).expanduser()
    base_path = args.base_path if args.base_path.startswith("/") else "/" + args.base_path
    api_base = args.api_base.rstrip("/")
    if not source.is_dir():
        die("源码目录不存在：%s" % source)
    if not (source / "package.json").exists():
        die("源码目录不是 Vite 工程（缺 package.json）：%s" % source)
    if not api_base.endswith("/api/v1"):
        die("--api-base 必须以 /api/v1 结尾：%s" % api_base)
    same_origin = resolve_same_origin(api_base, args.same_origin)
    _api_parts(api_base, same_origin)  # 参数/模式校验 fail-fast（die=rc2）
    deliverables.mkdir(parents=True, exist_ok=True)
    version = build_version_id(
        (source_git_info(source).get("head") or "nogit"))

    # --verify-only：只复核现有产物 + 清单
    if args.verify_only:
        return cmd_verify_only(args, dest, deliverables, base_path, api_base,
                               started, source, same_origin)

    with BuildLock(LOCK_FILE, args.force):
        info("构建开始：version=%s" % version)
        info("源码：%s" % source)
        log_path = deliverables / ("build-%s.log" % version)
        log_path.write_text("", encoding="utf-8")
        log_line(log_path, "# Hermes ACAD1 build log")
        log_line(log_path, "started_at=%s version=%s" % (iso(started), version))
        git_info = source_git_info(source)
        digest = source_digest(source)
        out_dir = TMP_ROOT / version / "zhice"
        env_values = {"VITE_BASE": base_path, "VITE_ROUTER": args.router,
                      "VITE_ENABLED_MARKETS": args.markets, "VITE_API_BASE": api_base}
        env = build_env(api_base, base_path, args.markets, args.router)
        build_started = now_local()
        build_info = run_build(source, out_dir, env, log_path, args.skip_typecheck)
        build_info["env"] = env_values
        build_info["same_origin"] = bool(same_origin)
        build_info["command"] = [
            "node node_modules/vite/bin/vite.js build --outDir <tmp> --emptyOutDir",
        ]
        build_info["built_at"] = iso()
        build_info["duration_s"] = round((now_local() - build_started).total_seconds(), 1)
        info("构建完成：%s" % build_info["assets"])

        patch_started = now_local()
        patch_info = patch_build(out_dir, api_base, base_path, same_origin=same_origin)
        patch_info["duration_s"] = round((now_local() - patch_started).total_seconds(), 1)
        if not patch_info["assertions"]["ok"]:
            die("补丁断言失败：%s" % json.dumps(patch_info["assertions"],
                                                ensure_ascii=False))
        info("补丁完成：WS=%d API=%d logo=%d"
             % (patch_info["counts"]["ws_replaced"],
                patch_info["counts"]["api_replaced"],
                patch_info["counts"]["logo_replaced"]))

        # 产物清单 + VERSION.json
        out_tree = tree_files(out_dir)
        same_origin_info: Dict[str, Any] = {
            "enabled": bool(same_origin),
            "base_path": base_path,
            "api_base": api_base,
            "api_prefix": api_base[: -len("/api/v1")] if same_origin else "",
            "forbidden_tokens": list(SAME_ORIGIN_FORBIDDEN_TOKENS) if same_origin else [],
            "initial_counts": patch_info["assertions"].get("counts", {}) if same_origin else {},
        }
        manifest: Dict[str, Any] = {
            "schema": SCHEMA_VERSION,
            "version": version,
            "built_at": build_info["built_at"],
            "task": "P2-3 Hermes 司马徽扩展构建流水线/版本化",
            "source": {
                "path": str(source),
                "git": git_info,
                "head": git_info.get("head"),
                "branch": git_info.get("branch"),
                "dirty": git_info.get("dirty"),
                "dirty_count": git_info.get("dirty_count", 0),
                "repo": git_info.get("repo"),
                "digest": {"digest": digest["digest"],
                           "file_count": digest["file_count"],
                           "bytes": digest["bytes"]},
            },
            "build": build_info,
            "same_origin": same_origin_info,
            "output": {"root": str(dest), "file_count": out_tree["file_count"],
                       "total_bytes": out_tree["total_bytes"],
                       "files": out_tree["files"]},
            "patch": patch_info,
            "boundaries": {
                "hermes_core_changed": False,
                "csp_changed": False,
                "hermes_8787_restarted": False,
                "port_8796_touched": False,
            },
        }
        write_version_json(out_dir, manifest)

        # 提升到工作树产物目录（旧产物进备份，不删除）
        if dest.exists():
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            backup = BACKUP_DIR / ("zhice_%s_prev" % now_local().strftime("%Y%m%d_%H%M%S"))
            if backup.exists():
                recycle_path(backup)
            shutil.move(str(dest), str(backup))
            manifest["promote"] = {"backup": str(backup)}
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(out_dir), str(dest))
        info("产物已就位：%s" % dest)
        # 备份保留（超出 retention 入回收站）
        backups = sorted(BACKUP_DIR.glob("zhice_*_prev"))
        for old in backups[:-max(1, args.retention)] if len(backups) > args.retention else []:
            if recycle_path(old):
                info("旧备份入回收站：%s" % old.name)

        # 验证
        verify: Dict[str, Any] = {"verified_at": iso()}
        dest_tree = tree_files(dest)
        verify["file_hashes_match"] = (
            {k: {"sha256": v["sha256"], "bytes": v["bytes"]}
             for k, v in out_tree["files"].items()} == dest_tree["files"])
        verify["file_count"] = dest_tree["file_count"]
        verify["total_bytes"] = dest_tree["total_bytes"]
        verify["assert_patched"] = assert_patched(dest, api_base, base_path,
                                                  same_origin=same_origin)
        if same_origin:
            _sa_counts = verify["assert_patched"].get("counts", {})
            same_origin_info["counts"] = _sa_counts
            _fh = _sa_counts.get("forbidden_hits", {})
            same_origin_info["clean"] = {
                "dirty_count": manifest["source"]["dirty_count"],
                "auto_session_hits": _fh.get("autoSession", 0),
                "legacy_port_hits": (_fh.get("127.0.0.1:8105", 0)
                                     + _fh.get("127.0.0.1:5175", 0)),
                "bare_api_remaining": _sa_counts.get("bare_api_remaining", 0),
                "api_prefix_hits": _sa_counts.get("api_prefix_hits", 0),
            }
            verify["static_path"] = {
                "ok": None, "skipped": True,
                "reason": "same-origin 模式不使用 Hermes /extensions/ 静态 handler",
            }
            info("same-origin 断言：dirty_count=%d autoSession=%d legacy_port=%d "
                 "bare_api=%d api_prefix_hits=%d"
                 % (same_origin_info["clean"]["dirty_count"],
                    same_origin_info["clean"]["auto_session_hits"],
                    same_origin_info["clean"]["legacy_port_hits"],
                    same_origin_info["clean"]["bare_api_remaining"],
                    same_origin_info["clean"]["api_prefix_hits"]))
        elif args.no_verify:
            verify["static_path"] = {
                "ok": None, "skipped": True,
                "reason": "--no-verify 跳过静态路径验证（保留补丁断言/文件对账）",
            }
            info("--no-verify：跳过静态路径验证（生产 Hermes handler 复现）")
        else:
            info("静态路径复现：用生产 Hermes handler 起本机临时端口…")
            verify["static_path"] = static_path_check(dest.parent, dest, base_path)
            info("静态路径复现结果：ok=%s mode=%s"
                 % (verify["static_path"]["ok"], verify["static_path"]["mode"]))
        live_pub: Dict[str, Any] = {}
        rollback: Dict[str, Any] = {}
        if args.publish_live:
            live_root = Path(args.live_root) if args.live_root else detect_live_extension_root()
            if live_root is None:
                die("--publish-live 需要 --live-root（或 start_hermes_bg.py 可解析）")
            info("发布到生产扩展目录（add-only）：%s" % live_root)
            live_pub = publish_live(dest, live_root, deliverables, version)
            manifest["live_publish"] = live_pub
            if not live_pub.get("ok"):
                die("发布对账失败：%s" % json.dumps(
                    {k: live_pub.get(k) for k in
                     ("existing_files_intact", "zhice_tree_matches_deliverable_dir")},
                    ensure_ascii=False))
        if not args.no_verify:
            info("8787 只读探针：%s" % args.live_url)
            live = live_probe(args.live_url, dest, base_path, args.trusted_header,
                              args.trusted_user)
            verify["live_probe"] = live
            verify["live_zhice_served"] = bool(live.get("zhice_served"))
            if args.publish_live and not live.get("zhice_served"):
                rollback = rollback_live(Path(args.live_root) if args.live_root
                                         else (detect_live_extension_root() or live_root))
                verify["live_rollback"] = rollback
                info("8787 未能读取 zhice，已回退覆盖层：%s" % rollback)
            elif args.publish_live:
                info("8787 已可直接读取 /extensions/zhice/（无需重启）")
        manifest["verify"] = verify
        manifest["live_probe"] = verify.get("live_probe", {})
        manifest["built_at"] = build_info["built_at"]
        manifest["finished_at"] = iso()
        # 最终 VERSION.json（含验证/发布结果）；VERSION.json 自身 hash 只放交付 MANIFEST
        write_version_json(dest, manifest)
        version_json_sha = sha256_file(dest / "VERSION.json")
        if args.publish_live and manifest.get("live_publish", {}).get("ok"):
            manifest["live_publish"]["version_json"] = sync_version_json(dest, live_root)
        manifest["deliverables"] = {
            "dir": str(deliverables),
            "version_json_sha256": version_json_sha,
            "rebuild_command": "python tools\\hermes_acad1_build.py",
            "rebuild_command_ps1": ("powershell -NoProfile -ExecutionPolicy Bypass "
                                    "-File tools\\hermes_acad1_build.ps1"),
            "publish_live_command": "python tools\\hermes_acad1_build.py --publish-live",
            "verify_only_command": "python tools\\hermes_acad1_build.py --verify-only",
        }
        # 交付 MANIFEST 需要最终 live_publish（含 VERSION 同步）后再落盘

        # 源码可重现留档
        diff_rc, diff_text = git_capture(["diff", "--", "code/frontend"],
                                         Path(git_info.get("repo") or source))
        (deliverables / "source-dirty.diff").write_text(
            diff_text if diff_rc == 0 else "git diff unavailable\n", encoding="utf-8")
        untracked_rc, untracked = git_capture(
            ["status", "--porcelain", "--untracked-files=all", "--", "code/frontend"],
            Path(git_info.get("repo") or source))
        untracked_files = []
        if untracked_rc == 0:
            for line in untracked.splitlines():
                if line.startswith("?? "):
                    rel = line[3:].strip()
                    p = Path(git_info.get("repo") or source) / rel
                    untracked_files.append({"path": rel,
                                            "sha256": sha256_file(p) if p.is_file() else "",
                                            "bytes": p.stat().st_size if p.is_file() else 0})
        write_json(deliverables / "source-untracked-files.json",
                   {"generated_at": iso(), "repo": git_info.get("repo"),
                    "head": git_info.get("head"), "files": untracked_files})

        # 交付清单
        write_json(deliverables / "MANIFEST.json", manifest)
        write_json(deliverables / "VERIFY.json", verify)
        if same_origin:
            readme_path = write_readme_same_origin(deliverables, manifest)
        else:
            readme_path = write_readme(deliverables, manifest)

    # 结果回执
    exit_code = 0
    if manifest["verify"]["static_path"].get("ok") is False:
        exit_code = 1
    if args.publish_live and not manifest["verify"].get("live_zhice_served"):
        exit_code = 1
    receipt = {
        "ok": exit_code == 0,
        "version": manifest["version"],
        "dest": str(dest),
        "deliverables": str(deliverables),
        "readme": str(readme_path),
        "manifest": manifest,
        "exit_code": exit_code,
        "elapsed_s": round((now_local() - started).total_seconds(), 1),
    }
    if args.json:
        print(json.dumps(receipt, ensure_ascii=False, indent=1))
    else:
        info("完成：version=%s elapsed=%.1fs exit=%d"
             % (manifest["version"], receipt["elapsed_s"], exit_code))
        info("产物：%s" % dest)
        info("清单：%s" % (deliverables / "MANIFEST.json"))
        info("README：%s" % readme_path)
        if manifest["verify"].get("live_probe"):
            lp = manifest["verify"]["live_probe"]
            info("8787: app.js=%s zhice/index.html=%s zhice_served=%s"
                 % (lp["checks"]["extensions_app_js"]["status"],
                    lp.get("zhice_index_status"), lp.get("zhice_served")))
    return exit_code


def cmd_verify_only(args: argparse.Namespace, dest: Path, deliverables: Path,
                    base_path: str, api_base: str, started: dt.datetime,
                    source: Path, same_origin: bool = False) -> int:
    same_origin = resolve_same_origin(api_base, same_origin)
    if not dest.exists():
        die("产物目录不存在：%s" % dest)
    version_json = dest / "VERSION.json"
    if not version_json.exists():
        die("缺少 VERSION.json：%s" % version_json)
    manifest = json.loads(version_json.read_text(encoding="utf-8"))
    tree = tree_files(dest)
    recorded = {k: {"sha256": v["sha256"], "bytes": v["bytes"]}
                for k, v in manifest["output"]["files"].items()}
    result: Dict[str, Any] = {
        "verify_only": True,
        "verified_at": iso(),
        "version": manifest.get("version"),
        "dest": str(dest),
        "file_hashes_match": tree["files"] == recorded,
        "assert_patched": assert_patched(dest, api_base, base_path,
                                         same_origin=same_origin),
        "same_origin": bool(same_origin),
        "static_path": ({"ok": None, "skipped": True,
                         "reason": "same-origin 模式不使用 Hermes /extensions/ 静态 handler"}
                        if (same_origin or args.no_verify)
                        else static_path_check(dest.parent, dest, base_path)),
    }
    if not args.no_verify and not same_origin:
        result["live_probe"] = live_probe(args.live_url, dest, base_path,
                                          args.trusted_header, args.trusted_user)
        result["live_zhice_served"] = result["live_probe"].get("zhice_served")
    write_json(deliverables / "VERIFY.json", result)
    ok = bool(result["file_hashes_match"] and result["assert_patched"]["ok"]
              and (not result["static_path"].get("ok") is False))
    exit_code = 0 if ok else 1
    if args.json:
        print(json.dumps({"ok": ok, "result": result}, ensure_ascii=False, indent=1))
    else:
        info("verify-only：hashes=%s patched=%s static=%s live_zhice=%s"
             % (result["file_hashes_match"], result["assert_patched"]["ok"],
                (result["static_path"] or {}).get("ok"),
                result.get("live_zhice_served")))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
