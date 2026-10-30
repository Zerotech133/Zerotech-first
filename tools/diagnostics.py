# -*- coding: utf-8 -*-
"""O7 Diagnostics：一键生成脱敏诊断包（zip）。

包含（均为只读采集）：
  meta.json     版本/commit/分支/脏文件数/Python/OS/环境变量"是否存在"（不含值）
  ports.json    关键端口探测 + 端口注册表数量
  health.json   关键 HTTP 端点状态/耗时
  quality.json  quality_scale_check --json 结果
  tests.json    最近一次 e2e artifact 摘要 + 最近夜跑 baseline 摘要
  logs/*.log    允许清单内的日志尾部（默认 200 行，已脱敏）
  README.txt    说明与采集时间

用法：
  python -X utf8 tools/diagnostics.py                 # 输出到 agent_platform/data/dev/diagnostics/
  python -X utf8 tools/diagnostics.py --out D:\\diag   # 指定目录
  python -X utf8 tools/diagnostics.py --no-logs       # 不含日志
  python -X utf8 tools/diagnostics.py --json          # 只打印摘要
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import io
import json
import os
import re
import socket
import subprocess
import sys
import time
import zipfile
from datetime import datetime

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    from tools.safe_urlopen import safe_urlopen

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(REPO, "agent_platform", "data", "dev", "diagnostics")

KEY_PORTS = [8091, 8734, 8796, 8795, 8787, 8096, 8094, 8080, 8101]
HEALTH_URLS = {
    "portal_api_8091": "http://127.0.0.1:8091/health",
    "portal_8734": "http://127.0.0.1:8734/",
    "legion_8796_mcp": "http://127.0.0.1:8796/api/zhaoyun/mcp",
    "gateway_8795": "http://127.0.0.1:8795/health",
    "hermes_8787": "http://127.0.0.1:8787/",
}
LOGS = {
    "portal_api_task": r"C:\Temp\portal_api_task.log",
    "legion_command_8796": r"C:\Temp\legion_command_8796.log",
    "legion_gateway_8795": r"C:\Temp\legion_gateway_8795.log",
    "feishu_bot": r"C:\aios\portal\logs\feishu_bot.log",
    "feishu_bot_heartbeat": r"C:\aios\portal\logs\feishu_bot_heartbeat.txt",
}
ENV_KEYS = ["TUSHARE_MCP_TOKEN", "GLM_API_KEY", "DEEPSEEK_API_KEY",
            "OPENAI_API_KEY", "FEISHU_APP_SECRET", "DSH_OTEL_ENABLED"]

REDACT_PATTERNS = [
    re.compile(r"(?i)\b(token|api[_-]?key|secret|password|authorization)(\s*[:=]\s*)([^\s\"',;}]{4,})"),
    re.compile(r"sk-[A-Za-z0-9]{10,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}"),
]
MAX_LOG_LINES = 200
MAX_LOG_BYTES = 256 * 1024


def redact_text(text):
    out = str(text or "")
    for pat in REDACT_PATTERNS:
        if pat.pattern.startswith("(?i)\\b(token"):
            out = pat.sub(lambda m: m.group(1) + m.group(2) + "<redacted>", out)
        else:
            out = pat.sub("<redacted>", out)
    return out


def tail_file(path, lines=MAX_LOG_LINES, max_bytes=MAX_LOG_BYTES):
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as f:
            data = f.read(max_bytes)
        return "\n".join(data.splitlines()[-lines:])
    except OSError as e:
        return "<unavailable: %s>" % e


def probe_port(port, timeout=0.3):
    t0 = time.time()
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return {"port": int(port), "up": True, "ms": round((time.time() - t0) * 1000, 1)}
    except OSError:
        return {"port": int(port), "up": False, "ms": None}


def probe_http(url, timeout=3):
    t0 = time.time()
    try:
        with safe_urlopen(url, timeout=timeout) as r:
            body = r.read(2048)
            return {"url": url, "status": r.status, "ms": round((time.time() - t0) * 1000, 1),
                    "bytes_sample": len(body)}
    except Exception as e:  # noqa: BLE001
        return {"url": url, "status": None, "ms": round((time.time() - t0) * 1000, 1),
                "error": redact_text("%s: %s" % (type(e).__name__, str(e)[:200]))}


def _git(args):
    try:
        p = subprocess.run(["git"] + args, cwd=REPO, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
        return p.stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def registry_count():
    try:
        path = os.path.join(REPO, "tools", "check_ports.py")
        spec = importlib.util.spec_from_file_location("check_ports_diag", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return len(getattr(mod, "REGISTRY", {}) or {})
    except Exception:  # noqa: BLE001
        return None


def _safe_json_load(path):
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def latest_e2e():
    files = glob.glob(os.path.join(REPO, "agent_platform", "data", "dev", "e2e", "*.json"))
    if not files:
        return None
    p = max(files, key=os.path.getmtime)
    d = _safe_json_load(p) or {}
    return {"file": os.path.basename(p), "rc": d.get("rc"), "summary": d.get("summary")}


def latest_night():
    files = glob.glob(os.path.join(REPO, "agent_platform", "data", "dev", "test_baseline", "*.json"))
    if not files:
        return None
    p = max(files, key=os.path.getmtime)
    d = _safe_json_load(p) or {}
    return {"file": os.path.basename(p), "totals": d.get("totals"), "suites": [
        {"suite": s.get("suite"), "rc": s.get("rc"), "passed": s.get("passed"),
         "failed": s.get("failed"), "errors": s.get("errors")}
        for s in (d.get("suites") or [])]}


def collect(with_logs=True):
    payload = {}
    env_presence = {k: bool(os.environ.get(k)) for k in ENV_KEYS}
    payload["meta.json"] = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "repo": REPO,
        "git_head": _git(["rev-parse", "--short", "HEAD"]),
        "git_branch": _git(["branch", "--show-current"]),
        "git_dirty_files": len([x for x in _git(["status", "--porcelain"]).splitlines() if x.strip()]),
        "python": sys.version.split()[0],
        "os": os.name,
        "env_presence": env_presence,
        "registry_ports": registry_count(),
    }
    payload["ports.json"] = {"key_ports": [probe_port(p) for p in KEY_PORTS]}
    payload["health.json"] = {k: probe_http(u) for k, u in HEALTH_URLS.items()}
    q = subprocess.run([sys.executable, "-X", "utf8", "tools/quality_scale_check.py", "--json"],
                       cwd=REPO, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    try:
        payload["quality.json"] = json.loads(q.stdout[q.stdout.find("{"):])
    except ValueError:
        payload["quality.json"] = {"error": "quality_scale_check parse failed", "rc": q.returncode}
    payload["tests.json"] = {"latest_e2e": latest_e2e(), "latest_night_baseline": latest_night()}
    payload["README.txt"] = (
        "AIOS diagnostics bundle\n"
        "generated_at: %s\n"
        "含 meta/ports/health/quality/tests 与允许清单日志尾部；token/key 等已脱敏。\n"
        "不含凭据值、不含 _vault 内容、不修改任何服务。\n" % payload["meta.json"]["generated_at"])
    if with_logs:
        for name, path in LOGS.items():
            payload["logs/%s.log" % name] = redact_text(tail_file(path))
    return payload


def write_zip(out_dir, payload, prefix="diagnostics"):
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(out_dir, "%s_%s.zip" % (prefix, stamp))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in payload.items():
            data = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2)
            z.writestr(name, data)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成脱敏诊断包（O7）")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--no-logs", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    payload = collect(with_logs=not args.no_logs)
    path = write_zip(args.out, payload)
    summary = {"zip": path, "size_bytes": os.path.getsize(path),
               "meta": payload["meta.json"], "health": payload["health.json"]}
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("diagnostics: %s (%d bytes)" % (path, os.path.getsize(path)))
        for k, v in payload["health.json"].items():
            print("  %-20s %s" % (k, v.get("status")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
