# -*- coding: utf-8 -*-
"""11-05 L2 窗口只读预检器 v1（task-14 / WO-L2-PRECHECK-20261104）。

为 2026-11-05 06:00 L2 窗口的 05:30 逐项确认提供 **go/no-go 预检**：
只检查、不修改；供 Lead/ARCH1 在窗口前直接运行，减少漏项。

覆盖项（A1–A7；与 `docs/ops/2026-11-03_11-05_L2窗口执行组织单_v1.md` 对齐）：
  A1  8091 `/health` 可达 + 端口属主 pid + 进程/代码新鲜度（HTTP 只读）
  A2  `portal/dw.html`（webroot 现页） vs `portal/static/dw.html`（候选新页）
      size/sha256/title 对比 + 容器内实际挂载 `/dw.html` 的 sha（docker exec）
  A3  Hermes 8787 `/health` 可达（A3 窗口只复核；只读 HTTP）
  A4  TDX 三表 `stock_news/stock_notice/stock_report` 最新日期与 stale 天数
      （`quant_backtest/data/tdx/tdx_info.db`，sqlite mode=ro）
  A5  `\\JevQueueRefresh` / `\\JevCalibDaily` 注册状态 + 注册前置包（schtasks 只读）
  A6  `portal/nginx.conf` 的 `/bt-jev`、`/api/intel-graph` auth_request；
      `/intel-dossier`、`/api/jev-stack` handler 存在性（文本只读）
  A7  `quant_backtest/start_kg_sync.ps1` registry_align 链尾步骤（文本只读 + 可选 kg 日志尾）

判定与退出码：
  - 每项 `ready | blocked | unknown` + evidence + notes；
  - 任一 `blocked`/`unknown` → rc=2；全部 `ready` → rc=0；
  - `--skip-network` 时 A1/A2/A3 的网络判定为 `unknown`（rc=2，仅离线排查用）；
  - **不打印 token/凭据/密钥**：HTTP 只记录状态码/长度/摘要，配置只记录指令/行号。

用法：
  python -X utf8 -B tools/l2_window_precheck.py                 # 文本（A1-A7）
  python -X utf8 -B tools/l2_window_precheck.py --json
  python -X utf8 -B tools/l2_window_precheck.py --md --out C:\\Temp\\l2_precheck.md
  python -X utf8 -B tools/l2_window_precheck.py --skip-network  # 离线（网络项 unknown）
  # 路径/网络全部可注入（测试/异地排查）：
  python -X utf8 -B tools/l2_window_precheck.py --repo-root C:\\aios \
      --base-8091 http://127.0.0.1:8091 --base-8734 http://127.0.0.1:8734 \
      --tdx-db C:\\aios\\quant_backtest\\data\\tdx\\tdx_info.db \
      --tasks-fixture C:\\Temp\\tasks_fixture.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
READY = "ready"
BLOCKED = "blocked"
UNKNOWN = "unknown"
STATUS_MARK = {READY: "READY", BLOCKED: "BLOCKED", UNKNOWN: "UNKNOWN"}
PS_EXE = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
TDX_TABLES = (("stock_news", "pub_date"), ("stock_notice", "notice_date"),
              ("stock_report", "pub_date"))
JEV_TASKS = (r"\JevQueueRefresh", r"\JevCalibDaily")
CONTAINER = "ai-portal"
CONTAINER_DW = "/usr/share/nginx/html/dw.html"
NOW_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
               "%Y-%m-%d")
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _ok(ident: str, title: str, reason: str, evidence: dict | None = None,
        notes: list[str] | None = None) -> dict:
    return {"id": ident, "title": title, "status": READY, "reason": reason,
            "evidence": evidence or {}, "notes": notes or []}


def _blocked(ident: str, title: str, reason: str, evidence: dict | None = None,
             notes: list[str] | None = None) -> dict:
    return {"id": ident, "title": title, "status": BLOCKED, "reason": reason,
            "evidence": evidence or {}, "notes": notes or []}


def _unknown(ident: str, title: str, reason: str, evidence: dict | None = None,
             notes: list[str] | None = None) -> dict:
    return {"id": ident, "title": title, "status": UNKNOWN, "reason": reason,
            "evidence": evidence or {}, "notes": notes or []}


# --------------------------------------------------------------------------- #
# 通用工具（只读）
# --------------------------------------------------------------------------- #
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def file_meta(path: Path, with_title: bool = False) -> dict:
    """只读文件元信息：exists/size/mtime/sha256/title（HTML 取第一个 title）。"""
    path = Path(path)
    meta = {"path": str(path), "exists": path.is_file(), "size": None,
            "mtime": "", "sha256": None, "title": None}
    if not meta["exists"]:
        return meta
    try:
        st = path.stat()
        meta["size"] = int(st.st_size)
        meta["mtime"] = datetime.fromtimestamp(st.st_mtime).strftime(
            "%Y-%m-%d %H:%M:%S")
        meta["sha256"] = sha256_file(path)
        if with_title:
            text = read_text_safe(path)
            if text is not None:
                match = TITLE_RE.search(text)
                meta["title"] = (match.group(1).strip()[:120]
                                 if match else None)
    except OSError as exc:
        meta["error"] = str(exc)[:200]
    return meta


def read_text_safe(path: Path, limit: int = 4 * 1024 * 1024) -> str | None:
    try:
        if not Path(path).is_file():
            return None
        if Path(path).stat().st_size > limit:
            return None
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def parse_now(value: str) -> datetime:
    text = str(value or "").strip()
    for fmt in NOW_FORMATS:
        try:
            return datetime.strptime(
                text[:10] if fmt == "%Y-%m-%d" else text[:19], fmt)
        except ValueError:
            continue
    raise ValueError("--now 格式非法（YYYY-MM-DD[ HH:MM:SS]）：%r" % value)


def parse_day(value) -> date | None:
    text = str(value or "").strip()
    if len(text) < 8:
        return None
    head = text[:10]
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(head if fmt == "%Y-%m-%d" else text[:8],
                                     fmt).date()
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
# HTTP（只读 GET；不保留正文，避免凭据泄漏）
# --------------------------------------------------------------------------- #
def http_get(url: str, timeout: float = 5.0) -> dict:
    result = {"url": url, "status": None, "ok": False, "healthy": None,
              "body_len": 0, "body_sha256": None, "error": ""}
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            result["status"] = int(getattr(resp, "status", 0) or 0)
            result["ok"] = 200 <= result["status"] < 400
            result["body_len"] = len(body)
            result["body_sha256"] = sha256_bytes(body)
            try:
                obj = json.loads(body.decode("utf-8", errors="replace"))
                if isinstance(obj, dict) and "ok" in obj:
                    result["healthy"] = bool(obj.get("ok"))
            except (ValueError, UnicodeError):
                result["healthy"] = None
    except urllib.error.HTTPError as exc:
        result["status"] = int(getattr(exc, "code", 0) or 0)
        result["error"] = "HTTP %s" % result["status"]
    except (urllib.error.URLError, OSError, ValueError) as exc:
        result["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
    return result


def container_dw_meta(timeout: float = 5.0) -> dict:
    """只读读取 nginx 容器内实际挂载的 dw.html sha256/size（不跟随 HTTP 302）。"""
    result = {"container": CONTAINER, "path": CONTAINER_DW,
              "sha256": None, "size": None, "error": ""}
    try:
        proc = subprocess.run(
            ["docker", "exec", CONTAINER, "sha256sum", CONTAINER_DW],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().replace("\n", " ")
            result["error"] = "docker exec sha256sum rc=%s: %s" % (
                proc.returncode, detail[:160])
            return result
        match = re.search(r"\b([0-9a-fA-F]{64})\b", proc.stdout)
        if not match:
            result["error"] = "docker exec sha256sum 输出无法解析"
            return result
        result["sha256"] = match.group(1).lower()
        stat = subprocess.run(
            ["docker", "exec", CONTAINER, "stat", "-c", "%s", CONTAINER_DW],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace")
        if stat.returncode == 0 and stat.stdout.strip().isdigit():
            result["size"] = int(stat.stdout.strip())
    except (OSError, subprocess.SubprocessError) as exc:
        result["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
    return result


def _run_ps(command: str, timeout: float = 10.0) -> dict:
    try:
        proc = subprocess.run([PS_EXE, "-NoProfile", "-NonInteractive",
                               "-Command", command],
                              capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace")
        return {"rc": proc.returncode, "stdout": proc.stdout,
                "stderr": proc.stderr}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"rc": -1, "stdout": "", "stderr": str(exc)[:200]}


def listener_pid(port: int, timeout: float = 10.0) -> dict:
    """只读查监听端口属主 pid（AGENTS：S4U/Session0 只信端口判活）。"""
    cmd = (
        "$c = Get-NetTCPConnection -LocalPort %d -State Listen "
        "-ErrorAction SilentlyContinue | Select-Object -First 1; "
        "if ($null -eq $c) { 'NO_LISTENER' } else { "
        "$procId = $c.OwningProcess; "
        "Write-Output ('pid=' + $procId) }" % int(port))
    out = _run_ps(cmd, timeout)
    pid = None
    match = re.search(r"pid=(\d+)", out["stdout"] or "")
    if match:
        pid = int(match.group(1))
    return {"port": int(port), "pid": pid,
            "listening": pid is not None,
            "error": "" if pid is not None else (out["stderr"] or "").strip()[:160]}


def process_info(pid: int, timeout: float = 10.0) -> dict:
    """只读查进程名/创建时间（S4U 进程可能取不到 CreationDate，fail-soft）。"""
    cmd = (
        "$p = Get-CimInstance Win32_Process -Filter \"ProcessId=%d\" "
        "-ErrorAction SilentlyContinue; "
        "if ($null -eq $p) { 'NO_PROCESS' } else { "
        "Write-Output ('name=' + $p.Name); "
        "Write-Output ('created=' + $p.CreationDate) }" % int(pid))
    out = _run_ps(cmd, timeout)
    name = None
    created = None
    for line in (out["stdout"] or "").splitlines():
        line = line.strip()
        if line.startswith("name="):
            name = line[5:].strip()
        elif line.startswith("created="):
            created = line[8:].strip()
    created_ts = None
    if created:
        for fmt in ("%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S",
                    "%Y/%m/%d %H:%M:%S"):
            try:
                created_ts = datetime.strptime(created, fmt).timestamp()
                break
            except ValueError:
                continue
    return {"pid": int(pid), "available": name is not None or created is not None,
            "name": name, "created": created, "created_ts": created_ts,
            "stderr": (out["stderr"] or "").strip()[:160]}


def query_task(name: str, timeout: float = 10.0) -> dict:
    """只读查询计划任务（schtasks /Query /FO CSV /NH）；不创建/修改。"""
    try:
        proc = subprocess.run(
            ["schtasks", "/Query", "/TN", name, "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        return {"name": name, "state": UNKNOWN, "rc": -1,
                "error": str(exc)[:160], "next_run": "", "task_status": ""}
    stdout = (proc.stdout or "").strip()
    if proc.returncode == 0 and stdout:
        fields = [x.strip('"') for x in stdout.splitlines()[0].split('","')]
        fields = [f.strip('"') for f in fields]
        next_run = fields[1] if len(fields) > 1 else ""
        task_status = fields[2] if len(fields) > 2 else ""
        return {"name": name, "state": "registered", "rc": 0, "error": "",
                "next_run": next_run, "task_status": task_status,
                "raw_fields": len(fields)}
    if proc.returncode == 1:
        return {"name": name, "state": "not_registered", "rc": 1, "error": "",
                "next_run": "", "task_status": ""}
    return {"name": name, "state": UNKNOWN, "rc": proc.returncode,
            "error": (proc.stderr or "").strip()[:160] or "unexpected rc",
            "next_run": "", "task_status": ""}


# --------------------------------------------------------------------------- #
# nginx 块解析（只读文本）
# --------------------------------------------------------------------------- #
def find_location_block(text: str, token: str) -> dict:
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("location"):
            continue
        if token not in stripped:
            continue
        depth = 0
        started = False
        block_lines = []
        for cursor in range(idx, min(len(lines), idx + 200)):
            current = lines[cursor]
            block_lines.append(current)
            depth += current.count("{") - current.count("}")
            if "{" in current:
                started = True
            if started and depth <= 0:
                break
        return {"found": True, "line": idx + 1,
                "block": "\n".join(block_lines)}
    return {"found": False, "line": 0, "block": ""}


def portal_py_has_def(portal_dir: Path, needle: str) -> dict:
    for path in sorted(Path(portal_dir).glob("*.py")):
        text = read_text_safe(path)
        if text is None:
            continue
        idx = text.find(needle)
        if idx >= 0:
            return {"found": True, "file": path.name,
                    "line": text.count("\n", 0, idx) + 1}
    return {"found": False, "file": "", "line": 0}


# --------------------------------------------------------------------------- #
# A1–A7 checks
# --------------------------------------------------------------------------- #
def check_a1(cfg: dict) -> dict:
    ident, title = "A1", "8091 health/pid/代码新鲜度"
    evidence = {"http": None, "listener": None, "process": None, "code": None,
                "code_fresh": None}
    code = file_meta(Path(cfg["portal_api"]))
    evidence["code"] = {"path": code["path"], "exists": code["exists"],
                        "mtime": code["mtime"]}
    if cfg["skip_network"]:
        return _unknown(ident, title, "网络检查被 --skip-network 跳过（8091 /health 未探）",
                        evidence)
    health = http_get(cfg["base_8091"].rstrip("/") + "/health", cfg["timeout"])
    evidence["http"] = health
    if not health["ok"] or health["status"] != 200:
        return _blocked(ident, title,
                        "8091 /health 不可达或非 200（status=%s error=%s）"
                        % (health["status"], health["error"] or "-"), evidence)
    listener = listener_pid(8091)
    evidence["listener"] = listener
    if listener["pid"] is None:
        return _blocked(ident, title, "8091 无监听进程（端口判活失败）", evidence)
    proc = process_info(listener["pid"])
    evidence["process"] = {"pid": proc["pid"], "name": proc["name"],
                           "created": proc["created"]}
    notes = []
    fresh = None
    if proc["created_ts"] is not None and code["mtime"]:
        code_mtime = datetime.strptime(code["mtime"],
                                       "%Y-%m-%d %H:%M:%S").timestamp()
        fresh = proc["created_ts"] > code_mtime
        evidence["code_fresh"] = fresh
        evidence["code_mtime_ts"] = code_mtime
        if not fresh:
            return _blocked(ident, title,
                            "8091 进程启动时间早于 portal_api.py mtime（旧进程带旧码，"
                            "按 AGENTS 红线需换血）", evidence)
    else:
        notes.append("进程 CreationDate 不可读（S4U/Session0 常见），按端口判活；"
                     "代码新鲜度无法机器判定")
    if health["healthy"] is False:
        return _blocked(ident, title, "/health 返回 ok=false（服务自报异常）",
                        evidence, notes)
    return _ok(ident, title,
               "8091 /health 200%s，pid=%s（%s）"
               % ("/ok=true" if health["healthy"] else "",
                  listener["pid"], proc["name"] or "?"),
               evidence, notes)


def check_a2(cfg: dict) -> dict:
    ident, title = "A2", "dw.html 现页 vs 候选新页 + 容器实际挂载 sha"
    portal_dir = Path(cfg["portal_dir"])
    deployed = file_meta(portal_dir / "dw.html", with_title=True)
    candidate = file_meta(portal_dir / "static" / "dw.html", with_title=True)
    evidence = {"deployed": deployed, "candidate": candidate,
                "container": None, "swap_needed": None}
    if not deployed["exists"]:
        return _blocked(ident, title,
                        "部署 webroot 的 portal/dw.html 缺失（docker bind mount 源）",
                        evidence)
    if not candidate["exists"]:
        return _blocked(ident, title,
                        "候选新页 portal/static/dw.html 缺失（A2 无页可换）",
                        evidence)
    evidence["swap_needed"] = deployed["sha256"] != candidate["sha256"]
    if not candidate["title"]:
        return _blocked(ident, title,
                        "候选新页无合法 <title>（非 HTML/损坏）", evidence)
    if cfg["skip_network"]:
        return _unknown(ident, title,
                        "容器 sha 检查被 --skip-network 跳过（ai-portal:/dw.html 未探）",
                        evidence)
    container = container_dw_meta(cfg["timeout"])
    evidence["container"] = container
    if container["error"]:
        return _unknown(ident, title,
                        "容器内 /dw.html sha 读取失败（%s）→ 05:30 人工复核"
                        % container["error"], evidence)
    container_sha = container["sha256"]
    if container_sha == deployed["sha256"]:
        return _ok(ident, title,
                   "容器 webroot 现页与仓库旧页一致（%sB/%s…）；窗口动作=换页"
                   % (deployed["size"], (deployed["sha256"] or "")[:12]),
                   evidence,
                   ["候选新页 %sB/%s… 已就绪"
                    % (candidate["size"], (candidate["sha256"] or "")[:12])])
    if container_sha == candidate["sha256"]:
        return _ok(ident, title,
                   "候选新页已在容器 webroot（A2 可视为 no-op/仅复核）", evidence)
    return _blocked(ident, title,
                    "容器内 /dw.html sha 与仓库旧页/候选页均不一致（container=%s… vs "
                    "deployed=%s… / candidate=%s…）→ 疑似 bind mount/副本漂移，"
                    "先确认 webroot 源"
                    % ((container_sha or "")[:12],
                       (deployed["sha256"] or "")[:12],
                       (candidate["sha256"] or "")[:12]),
                    evidence)


def check_a3(cfg: dict) -> dict:
    ident, title = "A3", "Hermes 8787 /health（窗口只复核）"
    url = cfg["base_8787"].rstrip("/") + "/health"
    evidence = {"http": None}
    if cfg["skip_network"]:
        return _unknown(ident, title, "网络检查被 --skip-network 跳过", evidence)
    health = http_get(url, cfg["timeout"])
    evidence["http"] = health
    if health["ok"] and health["status"] == 200:
        return _ok(ident, title, "Hermes /health 200", evidence)
    if health["status"] in (401, 403):
        return _unknown(ident, title,
                        "Hermes /health 返回 %s（需认证）→ 05:30 人工复核"
                        % health["status"], evidence)
    return _blocked(ident, title,
                    "Hermes 8787 /health 不可达/非 200（status=%s error=%s）"
                    % (health["status"], health["error"] or "-"), evidence)


def check_a4(cfg: dict) -> dict:
    ident, title = "A4", "TDX 三表最新日期/stale 天数"
    db_path = Path(cfg["tdx_db"])
    evidence = {"db": {"path": str(db_path), "exists": db_path.is_file(),
                       "size": None},
                "tables": {}, "threshold_days": cfg["tdx_max_stale_days"],
                "stale_days": None, "latest_date": None}
    if not db_path.is_file():
        return _blocked(ident, title, "TDX 库不存在：%s" % db_path, evidence)
    evidence["db"]["size"] = db_path.stat().st_size
    now = cfg["now"]
    max_dates = []
    try:
        conn = sqlite3.connect(
            "file:%s?mode=ro" % db_path.as_posix(), uri=True, timeout=30)
        conn.execute("PRAGMA query_only=1")
        try:
            existing = {str(r[0]) for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            for table, date_col in TDX_TABLES:
                if table not in existing:
                    evidence["tables"][table] = {"exists": False}
                    continue
                cols = [str(r[1]) for r in conn.execute(
                    'PRAGMA table_info("%s")' % table)]
                if date_col not in cols:
                    evidence["tables"][table] = {"exists": True,
                                                 "missing_column": date_col}
                    continue
                row = conn.execute(
                    'SELECT count(*), min("%s"), max("%s") FROM "%s"'
                    % (date_col, date_col, table)).fetchone()
                parsed = parse_day(row[2])
                evidence["tables"][table] = {
                    "exists": True, "rows": int(row[0]),
                    "min_date": str(row[1] or ""), "max_date": str(row[2] or ""),
                    "max_date_parsed": parsed.isoformat() if parsed else None}
                if parsed is not None:
                    max_dates.append(parsed)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return _blocked(ident, title, "TDX 库只读查询失败：%s" % exc, evidence)
    missing = [t for t, meta in evidence["tables"].items()
               if not meta.get("exists") or meta.get("missing_column")]
    if missing:
        return _blocked(ident, title, "TDX 三表缺失/缺列：%s" % ",".join(missing),
                        evidence)
    if not max_dates:
        return _blocked(ident, title, "TDX 三表无有效日期（空表/格式异常）", evidence)
    latest = max(max_dates)
    stale = (now.date() - latest).days
    evidence["latest_date"] = latest.isoformat()
    evidence["stale_days"] = stale
    if stale > int(cfg["tdx_max_stale_days"]):
        return _blocked(ident, title,
                        "TDX 三表 stale %d 天（最新 %s，阈值 %d 天）→ 需 A4 窗口同步"
                        % (stale, latest.isoformat(), cfg["tdx_max_stale_days"]),
                        evidence)
    return _ok(ident, title, "TDX 三表最新 %s（stale=%d 天，阈值 %d）"
               % (latest.isoformat(), stale, cfg["tdx_max_stale_days"]),
               evidence)


def load_tasks_fixture(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if "tasks" in data and isinstance(data["tasks"], dict):
        data = data["tasks"]
    return data


def check_a5(cfg: dict) -> dict:
    ident, title = "A5", "Jev 两任务注册状态 + 注册前置包"
    evidence = {"tasks": {}, "prereqs": {}, "fixture": cfg["tasks_fixture"],
                "already_registered": False}
    fixture = None
    if cfg["tasks_fixture"]:
        fixture = load_tasks_fixture(Path(cfg["tasks_fixture"]))
        if fixture is None:
            return _unknown(ident, title,
                            "--tasks-fixture 无法解析（需 JSON 对象）", evidence)
    for name in JEV_TASKS:
        if fixture is not None:
            item = fixture.get(name) or fixture.get(name.lstrip("\\"))
            if isinstance(item, dict):
                state = item.get("state") or (
                    "registered" if item.get("registered") else
                    "not_registered")
                entry = {"name": name, "state": state,
                         "next_run": str(item.get("next_run") or ""),
                         "task_status": str(item.get("status") or "")}
            elif isinstance(item, bool):
                entry = {"name": name,
                         "state": "registered" if item else "not_registered",
                         "next_run": "", "task_status": ""}
            else:
                entry = {"name": name, "state": UNKNOWN,
                         "error": "fixture 缺该任务键"}
        else:
            entry = query_task(name)
        evidence["tasks"][name] = entry
    states = [meta.get("state") for meta in evidence["tasks"].values()]
    if any(state == UNKNOWN for state in states):
        return _unknown(ident, title, "任务状态查询失败/未知（schtasks 异常）", evidence)
    path_specs = {
        "register_script": Path(cfg["register_script"]),
        "refresh_tool": Path(cfg["refresh_tool"]),
        "calib_tool": Path(cfg["calib_tool"]),
        "news_db": Path(cfg["news_db"]),
        "audit_db": Path(cfg["audit_db"]),
        "l2_packet": Path(cfg["l2_packet"]),
    }
    missing = []
    for key, path in path_specs.items():
        exists = path.is_file()
        evidence["prereqs"][key] = {"path": str(path), "exists": exists}
        if not exists:
            missing.append(key)
    if all(state == "registered" for state in states):
        evidence["already_registered"] = True
        if missing:
            return _blocked(ident, title,
                            "任务已在册但前置文件缺失：%s" % ",".join(missing),
                            evidence,
                            ["窗口内只做验证；不要重复注册（注册脚本需 -Force 才覆盖）"])
        return _ok(ident, title, "两任务已在册（窗口只做验证，不重复注册）",
                   evidence)
    if all(state == "not_registered" for state in states):
        if missing:
            return _blocked(ident, title,
                            "两任务未注册且注册前置缺失（等 21:40 合并 WO-JEV-OPS）：%s"
                            % ",".join(missing), evidence,
                            ["合并后本项应转 ready；05:30 以当时结果为准"])
        return _ok(ident, title, "两任务未注册，注册前置包齐备（窗口动作=注册）",
                   evidence)
    return _unknown(ident, title, "两任务注册状态不一致", evidence)


def check_a6(cfg: dict) -> dict:
    ident, title = "A6", "nginx auth_request + portal_api handler 存在性"
    evidence = {"nginx_conf": {"path": str(cfg["nginx_conf"])},
                "bt_jev": None, "intel_graph": None,
                "handlers": {"intel_dossier": None, "jev_stack": None}}
    nginx_text = read_text_safe(Path(cfg["nginx_conf"]))
    if nginx_text is None:
        return _blocked(ident, title, "nginx.conf 不可读：%s" % cfg["nginx_conf"],
                        evidence)
    bt_block = find_location_block(nginx_text, "/bt-jev")
    api_block = find_location_block(nginx_text, "intel-graph")
    bt_auth = bool(bt_block["found"] and "auth_request" in bt_block["block"])
    api_auth = bool(api_block["found"] and "auth_request" in api_block["block"])
    evidence["bt_jev"] = {"location_found": bt_block["found"],
                          "location_line": bt_block["line"],
                          "auth_request": bt_auth}
    evidence["intel_graph"] = {"location_found": api_block["found"],
                               "location_line": api_block["line"],
                               "auth_request": api_auth}
    portal_api_text = read_text_safe(Path(cfg["portal_api"]))
    route_text = read_text_safe(Path(cfg["route_table"]))
    if portal_api_text is None or route_text is None:
        return _blocked(ident, title, "portal_api.py / route_table.py 不可读",
                        evidence)
    dossier_found = "/intel-dossier" in portal_api_text
    evidence["handlers"]["intel_dossier"] = {"found": dossier_found,
                                             "file": "portal_api.py"}
    jev_route = "/api/jev-stack" in route_text
    jev_def = portal_py_has_def(Path(cfg["portal_dir"]), "def _api_jev_stack")
    evidence["handlers"]["jev_stack"] = {
        "route_found": jev_route, "handler_found": jev_def["found"],
        "handler_file": jev_def["file"], "handler_line": jev_def["line"]}
    blockers = []
    if not bt_auth:
        blockers.append("/bt-jev 无 auth_request")
    if not api_auth:
        blockers.append("/api/intel-graph 无 auth_request")
    if not dossier_found:
        blockers.append("/intel-dossier handler 缺失")
    if not (jev_route and jev_def["found"]):
        blockers.append("/api/jev-stack handler 缺失")
    if blockers:
        notes = []
        if bt_auth and api_auth:
            notes.append("A6 窗口动作已具备（auth_request 就位）")
        else:
            notes.append("A6 窗口动作=给 /bt-jev、/api/intel-graph 加 auth_request"
                         "（改后需 nginx -t + reload，属 L2）")
        return _blocked(ident, title, "；".join(blockers), evidence, notes)
    return _ok(ident, title, "auth_request 与 handler 均就位", evidence)


def check_a7(cfg: dict) -> dict:
    ident, title = "A7", "KgDaily 链尾 registry_align 步骤"
    script_path = Path(cfg["kg_sync_script"])
    evidence = {"script": {"path": str(script_path), "exists": script_path.is_file()},
                "registry_align_tool": {
                    "path": str(cfg["registry_align"]), "exists": None},
                "chain_step": None, "kg_log_tail": None}
    if not script_path.is_file():
        return _blocked(ident, title, "start_kg_sync.ps1 不存在：%s" % script_path,
                        evidence)
    text = read_text_safe(script_path)
    if text is None:
        return _blocked(ident, title, "start_kg_sync.ps1 不可读", evidence)
    has_align = "registry_align.py" in text
    has_exit_line = "registry_align exit=" in text
    has_ok = "kg_sync OK" in text
    align_pos = text.find("registry_align exit=")
    ok_pos = text.rfind("kg_sync OK")
    order_ok = align_pos >= 0 and ok_pos >= 0 and align_pos < ok_pos
    tool_path = Path(cfg["registry_align"])
    tool_exists = tool_path.is_file()
    evidence["registry_align_tool"]["exists"] = tool_exists
    evidence["chain_step"] = {"registry_align_mentioned": has_align,
                              "exit_line": has_exit_line,
                              "kg_sync_ok": has_ok,
                              "kg_sync_ok_after_align": order_ok}
    log_path = Path(cfg["kg_log"])
    if log_path.is_file():
        tail_text = read_text_safe(log_path)
        if tail_text:
            tail_lines = tail_text.splitlines()[-60:]
            last_ok = next((ln.strip() for ln in reversed(tail_lines)
                            if "kg_sync OK" in ln), "")
            last_align = next((ln.strip() for ln in reversed(tail_lines)
                               if "registry_align" in ln), "")
            evidence["kg_log_tail"] = {"path": str(log_path),
                                       "last_kg_sync_ok": last_ok[:160],
                                       "last_registry_align": last_align[:200]}
    if not has_align:
        return _blocked(ident, title,
                        "start_kg_sync.ps1 未包含 registry_align 链尾步骤",
                        evidence)
    if not (has_exit_line and order_ok):
        return _blocked(ident, title,
                        "registry_align 步骤存在但不在 kg_sync OK 之前/缺 exit 留痕",
                        evidence)
    if not tool_exists:
        return _blocked(ident, title,
                        "registry_align.py 工具缺失：%s" % tool_path, evidence)
    return _ok(ident, title,
               "registry_align 链尾步骤就位（kg_sync OK 之前），日志尾可见留痕",
               evidence)


CHECK_ORDER = ("A1", "A2", "A3", "A4", "A5", "A6", "A7")
CHECK_FUNCS = {"A1": check_a1, "A2": check_a2, "A3": check_a3,
               "A4": check_a4, "A5": check_a5, "A6": check_a6, "A7": check_a7}


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def build_report(cfg: dict, checks: list[dict]) -> dict:
    summary = {READY: 0, BLOCKED: 0, UNKNOWN: 0}
    for check in checks:
        summary[check["status"]] = summary.get(check["status"], 0) + 1
    if summary[BLOCKED]:
        overall = BLOCKED
    elif summary[UNKNOWN]:
        overall = UNKNOWN
    else:
        overall = READY
    return {
        "tool": "l2_window_precheck",
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "as_of": cfg["now"].strftime("%Y-%m-%d %H:%M:%S"),
        "read_only": True,
        "target": "2026-11-05 06:00 L2 window precheck (05:30 confirmation)",
        "network_checked": not cfg["skip_network"],
        "summary": {**summary, "total": len(checks)},
        "overall": overall,
        "rc": 0 if overall == READY else 2,
        "checks": checks,
        "config": {
            "repo_root": str(cfg["repo_root"]),
            "base_8091": cfg["base_8091"],
            "base_8734": cfg["base_8734"],
            "base_8787": cfg["base_8787"],
            "tdx_db": str(cfg["tdx_db"]),
            "nginx_conf": str(cfg["nginx_conf"]),
            "tasks_fixture": str(cfg["tasks_fixture"] or ""),
        },
        "warnings": [],
    }


def render_text(rep: dict) -> str:
    lines = [
        "11-05 L2 窗口只读预检（A1–A7 go/no-go）· %s"
        % ("含网络" if rep["network_checked"] else "离线（--skip-network）"),
        "as_of: %s ｜ overall: %s ｜ ready %d / blocked %d / unknown %d"
        % (rep["as_of"], rep["overall"].upper(), rep["summary"][READY],
           rep["summary"][BLOCKED], rep["summary"][UNKNOWN]),
    ]
    for check in rep["checks"]:
        lines.append("[%s] %s %s — %s"
                     % (STATUS_MARK[check["status"]], check["id"],
                        check["title"], check["reason"]))
        ev = check["evidence"]
        if check["id"] == "A1" and ev.get("listener"):
            lines.append("    pid=%s ｜ http=%s ｜ code_mtime=%s ｜ fresh=%s"
                         % (ev["listener"].get("pid"),
                            (ev.get("http") or {}).get("status"),
                            (ev.get("code") or {}).get("mtime"),
                            ev.get("code_fresh")))
        elif check["id"] == "A2":
            dep = ev.get("deployed") or {}
            cand = ev.get("candidate") or {}
            container = ev.get("container") or {}
            lines.append("    deployed=%sB/%s… ｜ candidate=%sB/%s… ｜ "
                         "container=%sB/%s… ｜ swap_needed=%s"
                         % (dep.get("size"), (dep.get("sha256") or "")[:10],
                            cand.get("size"), (cand.get("sha256") or "")[:10],
                            container.get("size"),
                            (container.get("sha256") or "")[:10],
                            ev.get("swap_needed")))
        elif check["id"] == "A4":
            for table, meta in (ev.get("tables") or {}).items():
                lines.append("    %s: rows=%s max=%s stale=%s"
                             % (table, meta.get("rows"),
                                meta.get("max_date"), ev.get("stale_days")))
        elif check["id"] == "A5":
            for name, meta in (ev.get("tasks") or {}).items():
                lines.append("    %s: %s next=%s"
                             % (name, meta.get("state"),
                                meta.get("next_run") or "-"))
            missing = [k for k, v in (ev.get("prereqs") or {}).items()
                       if not v.get("exists")]
            if missing:
                lines.append("    缺前置: %s" % ",".join(missing))
        elif check["id"] == "A6":
            lines.append("    /bt-jev auth=%s ｜ intel-graph auth=%s ｜ "
                         "handlers=%s"
                         % ((ev.get("bt_jev") or {}).get("auth_request"),
                            (ev.get("intel_graph") or {}).get("auth_request"),
                            ",".join("%s:%s" % (k, v.get("found") if k ==
                                                "intel_dossier" else
                                                (v.get("route_found") and
                                                 v.get("handler_found")))
                                     for k, v in
                                     (ev.get("handlers") or {}).items())))
        elif check["id"] == "A7":
            step = ev.get("chain_step") or {}
            log = ev.get("kg_log_tail") or {}
            lines.append("    chain_step=%s ｜ log_tail_ok=%s"
                         % (step, bool(log.get("last_kg_sync_ok"))))
        for note in check["notes"]:
            lines.append("    note: %s" % note)
    lines.append("")
    lines.append("结论: %s（rc=%d）"
                 % ("全部 ready，可进入 05:30 逐项确认" if rep["rc"] == 0 else
                    "存在 blocked/unknown，先按各分项处置（A5 待合并、A4/A6 待窗口动作等）",
                    rep["rc"]))
    return "\n".join(lines)


def render_md(rep: dict) -> str:
    lines = [
        "# 11-05 L2 窗口只读预检（A1–A7 go/no-go）",
        "",
        "- 生成时间：%s ｜ as_of：%s" % (rep["generated_at"], rep["as_of"]),
        "- 网络检查：%s ｜ 只读：%s" % (
            "是" if rep["network_checked"] else "否（--skip-network）",
            rep["read_only"]),
        "- **overall：%s**（ready %d / blocked %d / unknown %d，rc=%d）"
        % (rep["overall"].upper(), rep["summary"][READY],
           rep["summary"][BLOCKED], rep["summary"][UNKNOWN], rep["rc"]),
        "",
        "## 一、逐项结果",
        "",
        "| 项 | 标题 | 状态 | 结论/原因 |",
        "|---|---|---|---|",
    ]
    for check in rep["checks"]:
        lines.append("| %s | %s | **%s** | %s |"
                     % (check["id"], check["title"],
                        STATUS_MARK[check["status"]],
                        str(check["reason"]).replace("|", "\\|")))
    lines += ["", "## 二、证据明细", ""]
    for check in rep["checks"]:
        lines.append("### %s %s" % (check["id"], check["title"]))
        lines.append("")
        lines.append("- 状态：**%s**" % STATUS_MARK[check["status"]])
        lines.append("- 原因：%s" % check["reason"])
        evidence = check["evidence"]
        if check["id"] == "A1":
            http = evidence.get("http") or {}
            listener = evidence.get("listener") or {}
            proc = evidence.get("process") or {}
            code = evidence.get("code") or {}
            lines.append("- HTTP：status=%s body_len=%s healthy=%s"
                         % (http.get("status"), http.get("body_len"),
                            http.get("healthy")))
            lines.append("- pid：%s（进程名 %s，创建 %s）"
                         % (listener.get("pid"), proc.get("name") or "?",
                            proc.get("created") or "不可读"))
            lines.append("- 代码：%s mtime=%s ｜ 新鲜=%s"
                         % (code.get("path"), code.get("mtime"),
                            evidence.get("code_fresh")))
        elif check["id"] == "A2":
            deployed = evidence.get("deployed") or {}
            candidate = evidence.get("candidate") or {}
            container = evidence.get("container") or {}
            lines.append("- 现页 `portal/dw.html`：%sB / sha256 `%s…` / title 《%s》"
                         % (deployed.get("size"),
                            (deployed.get("sha256") or "")[:16],
                            deployed.get("title") or "-"))
            lines.append("- 候选 `portal/static/dw.html`：%sB / sha256 `%s…` / title 《%s》"
                         % (candidate.get("size"),
                            (candidate.get("sha256") or "")[:16],
                            candidate.get("title") or "-"))
            lines.append("- 容器 `ai-portal:/usr/share/nginx/html/dw.html`："
                         "bytes=%s sha256 `%s…` error=`%s`"
                         % (container.get("size"),
                            (container.get("sha256") or "")[:16],
                            container.get("error") or "-"))
            lines.append("- 是否需换页（swap_needed）：%s"
                         % evidence.get("swap_needed"))
        elif check["id"] == "A3":
            http = evidence.get("http") or {}
            lines.append("- HTTP：status=%s body_len=%s"
                         % (http.get("status"), http.get("body_len")))
        elif check["id"] == "A4":
            lines.append("- 库：`%s`（%sB）"
                         % (evidence.get("db", {}).get("path"),
                            evidence.get("db", {}).get("size")))
            lines += ["| 表 | rows | min | max |", "|---|---|---|---|"]
            for table, meta in (evidence.get("tables") or {}).items():
                lines.append("| `%s` | %s | %s | %s |"
                             % (table, meta.get("rows"), meta.get("min_date"),
                                meta.get("max_date")))
            lines.append("- 最新交易日：%s ｜ stale=%s 天（阈值 %s）"
                         % (evidence.get("latest_date"),
                            evidence.get("stale_days"),
                            evidence.get("threshold_days")))
        elif check["id"] == "A5":
            for name, meta in (evidence.get("tasks") or {}).items():
                lines.append("- `%s`：%s ｜ next=%s ｜ status=%s"
                             % (name, meta.get("state"),
                                meta.get("next_run") or "-",
                                meta.get("task_status") or "-"))
            for key, meta in (evidence.get("prereqs") or {}).items():
                lines.append("- 前置 `%s`：exists=%s"
                             % (key, meta.get("exists")))
        elif check["id"] == "A6":
            lines.append("- `/bt-jev` location=%s auth_request=%s"
                         % ((evidence.get("bt_jev") or {}).get("location_found"),
                            (evidence.get("bt_jev") or {}).get("auth_request")))
            lines.append("- `/api/intel-graph` location=%s auth_request=%s"
                         % ((evidence.get("intel_graph") or {}).get("location_found"),
                            (evidence.get("intel_graph") or {}).get("auth_request")))
            handlers = evidence.get("handlers") or {}
            lines.append("- handler `/intel-dossier`：%s"
                         % (handlers.get("intel_dossier") or {}).get("found"))
            lines.append("- handler `/api/jev-stack`：route=%s def=%s（%s）"
                         % ((handlers.get("jev_stack") or {}).get("route_found"),
                            (handlers.get("jev_stack") or {}).get("handler_found"),
                            (handlers.get("jev_stack") or {}).get("handler_file")))
        elif check["id"] == "A7":
            step = evidence.get("chain_step") or {}
            lines.append("- 链尾步骤：registry_align 提及=%s / exit 留痕=%s / 在 "
                         "kg_sync OK 之前=%s"
                         % (step.get("registry_align_mentioned"),
                            step.get("exit_line"),
                            step.get("kg_sync_ok_after_align")))
            lines.append("- registry_align.py：exists=%s"
                         % (evidence.get("registry_align_tool") or {}).get("exists"))
            log = evidence.get("kg_log_tail") or {}
            if log:
                lines.append("- kg 日志尾：`%s`"
                             % (log.get("last_kg_sync_ok") or "-"))
                lines.append("- registry_align 留痕：`%s`"
                             % (log.get("last_registry_align") or "-"))
        for note in check["notes"]:
            lines.append("- note：%s" % note)
        lines.append("")
    lines += ["## 三、边界", "",
              "- 本器**只读**：HTTP GET / schtasks 查询 / sqlite `mode=ro` / 文本读取；"
              "不改 nginx/任务/服务/配置，不启停，不跑 L2；",
              "- 不输出 token/凭据/密钥，仅状态码/长度/摘要/文件名/行号；",
              "- blocked/unknown 只代表“窗口前置尚未满足或不可机器判定”，"
              "是否继续由 05:30 逐项人工确认决定。",
              ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def resolve_cfg(args) -> dict:
    repo = Path(args.repo_root).resolve() if args.repo_root else REPO
    portal = Path(args.portal_dir) if args.portal_dir else repo / "portal"
    now = parse_now(args.now) if args.now else datetime.now()
    cfg = {
        "repo_root": repo,
        "portal_dir": portal,
        "nginx_conf": Path(args.nginx_conf) if args.nginx_conf
        else portal / "nginx.conf",
        "portal_api": Path(args.portal_api) if args.portal_api
        else portal / "portal_api.py",
        "route_table": Path(args.route_table) if args.route_table
        else portal / "route_table.py",
        "tdx_db": Path(args.tdx_db) if args.tdx_db
        else repo / "quant_backtest" / "data" / "tdx" / "tdx_info.db",
        "kg_sync_script": Path(args.kg_sync_script) if args.kg_sync_script
        else repo / "quant_backtest" / "start_kg_sync.ps1",
        "registry_align": Path(args.registry_align) if args.registry_align
        else repo / "tools" / "datawarehouse" / "registry_align.py",
        "register_script": Path(args.register_script) if args.register_script
        else repo / "ops" / "register_jev_tasks_20261105.ps1",
        "refresh_tool": Path(args.refresh_tool) if args.refresh_tool
        else repo / "tools" / "jev_queue_refresh.py",
        "calib_tool": Path(args.calib_tool) if args.calib_tool
        else repo / "tools" / "jev_calib_runner.py",
        "news_db": Path(args.news_db) if args.news_db
        else repo / "news_collector" / "news.db",
        "audit_db": Path(args.audit_db) if args.audit_db
        else repo / "agent_platform" / "data" / "jev_audit" / "jev_audit.db",
        "l2_packet": Path(args.l2_packet) if args.l2_packet
        else repo / "docs" / "ops" / "2026-11-03_JEV复核与校准L2申请单_v1.md",
        "kg_log": Path(args.kg_log) if args.kg_log
        else Path(r"C:\Temp\kg_sync_task.log"),
        "base_8091": args.base_8091,
        "base_8734": args.base_8734,
        "base_8787": args.base_8787,
        "skip_network": bool(args.skip_network),
        "timeout": float(args.timeout),
        "now": now,
        "tasks_fixture": args.tasks_fixture or "",
        "tdx_max_stale_days": int(args.tdx_max_stale_days),
    }
    return cfg


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - 非 TTY / 旧解释器忽略
        pass
    ap = argparse.ArgumentParser(
        description="11-05 L2 窗口只读预检（A1–A7 go/no-go，只检查不修改）")
    ap.add_argument("--repo-root", default="",
                    help="仓库根（默认本文件上两级；窗口内= C:\\aios）")
    ap.add_argument("--portal-dir", default="", help="portal 目录（默认 <repo>/portal）")
    ap.add_argument("--nginx-conf", default="", help="nginx.conf 路径")
    ap.add_argument("--portal-api", default="", help="portal_api.py 路径")
    ap.add_argument("--route-table", default="", help="route_table.py 路径")
    ap.add_argument("--tdx-db", default="", help="tdx_info.db 路径")
    ap.add_argument("--kg-sync-script", default="", help="start_kg_sync.ps1 路径")
    ap.add_argument("--registry-align", default="", help="registry_align.py 路径")
    ap.add_argument("--register-script", default="", help="Jev 注册脚本路径")
    ap.add_argument("--refresh-tool", default="", help="jev_queue_refresh.py 路径")
    ap.add_argument("--calib-tool", default="", help="jev_calib_runner.py 路径")
    ap.add_argument("--news-db", default="", help="news.db 路径")
    ap.add_argument("--audit-db", default="", help="jev_audit.db 路径")
    ap.add_argument("--l2-packet", default="", help="JEV L2 申请单 md 路径")
    ap.add_argument("--kg-log", default="", help="kg_sync_task.log 路径")
    ap.add_argument("--base-8091", default="http://127.0.0.1:8091",
                    help="8091 base URL（默认 127.0.0.1:8091）")
    ap.add_argument("--base-8734", default="http://127.0.0.1:8734",
                    help="8734 base URL（默认 127.0.0.1:8734）")
    ap.add_argument("--base-8787", default="http://127.0.0.1:8787",
                    help="8787 base URL（默认 127.0.0.1:8787）")
    ap.add_argument("--skip-network", action="store_true",
                    help="跳过全部 HTTP（A1/A2/A3 → unknown，rc=2）")
    ap.add_argument("--timeout", type=float, default=5.0,
                    help="HTTP/子进程超时秒（默认 5）")
    ap.add_argument("--now", default="",
                    help="as-of 时间（默认本机当前；测试用 YYYY-MM-DD HH:MM:SS）")
    ap.add_argument("--tdx-max-stale-days", type=int, default=1,
                    help="A4 允许的 stale 天数阈值（默认 1）")
    ap.add_argument("--tasks-fixture", default="",
                    help="A5 任务状态 fixture JSON（测试/异地；不传则 schtasks 查询）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--md", action="store_true", help="输出 Markdown")
    ap.add_argument("--out", default="", help="把所选格式写入文件（原子写）")
    args = ap.parse_args(argv)

    if args.json and args.md:
        print("[FAIL] --json 与 --md 只能二选一", file=sys.stderr)
        return 2
    if args.timeout <= 0:
        print("[FAIL] --timeout 必须 > 0", file=sys.stderr)
        return 2
    if args.tdx_max_stale_days < 0:
        print("[FAIL] --tdx-max-stale-days 不能为负", file=sys.stderr)
        return 2
    try:
        cfg = resolve_cfg(args)
    except ValueError as exc:
        print("[FAIL] %s" % exc, file=sys.stderr)
        return 2
    checks = []
    for ident in CHECK_ORDER:
        try:
            checks.append(CHECK_FUNCS[ident](cfg))
        except Exception as exc:  # noqa: BLE001 - 单项异常不得吞掉整体报告
            checks.append(_unknown(ident, ident, "检查执行异常：%s: %s"
                                   % (type(exc).__name__, str(exc)[:160])))
    rep = build_report(cfg, checks)
    if args.json:
        text = json.dumps(rep, ensure_ascii=False, indent=2) + "\n"
    elif args.md:
        text = render_md(rep) + "\n"
    else:
        text = render_text(rep) + "\n"
    if args.out:
        try:
            _atomic_write(Path(args.out), text)
        except OSError as exc:
            print("[FAIL] --out 写入失败：%s" % exc, file=sys.stderr)
            return 2
    sys.stdout.write(text)
    return rep["rc"]


if __name__ == "__main__":
    sys.exit(main())
