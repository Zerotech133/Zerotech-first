# -*- coding: utf-8 -*-
"""M1 信任链 KPI / 证据只读检查（fail-soft；不写账本、不重启服务、不代签）。

一条命令输出 M1 信任链判据状态，供 2026-11-03 验收与每日检查：

  python -X utf8 tools/trust_chain_kpi.py            # Markdown 摘要（默认）
  python -X utf8 tools/trust_chain_kpi.py --json     # JSON 摘要（机器可读）
  python -X utf8 tools/trust_chain_kpi.py --json --timeout 2 \
      --m4-since 2026-10-31T00:00:00
  # 在 worktree 内跑（账本/配置以主仓为准）：
  python -X utf8 tools/trust_chain_kpi.py --json --repo "C:\\项目\\老田的AI助手"

判据（全部只读；单检查不可达/超时/数据源缺失 → status=unknown，不阻塞其它检查）：
  1 auth_surfaces        凭证入口恰好 5 面、无第 6（复用 tools/check_auth_surfaces.py check()）
  2 ide_health_8103      8103  /api/health = 200 且 body.ok=true
  3 ide_rw_anon_8103     8103  无 token 直连 /rw = 401（或 403 identity）
  4 ide_patchset_no_cap  8103  无会话/无 cap POST /api/ide/patchset/preview = 4xx fail-closed
                         注：8103 的 cap 门在会话之后，匿名请求实际先报 no_session；
                         工具接受 fail-closed 错误码并显式记录实际码（cap_required_observed）
  5 hermes_sessions_8787 8787  无 cookie /api/sessions = 401（SSO 生效）
  6 ide_version_8103     8103  /api/ide/version = 200 且 all_match=true
  7 release_artifact_drift
                         current.json 指针 ↔ current junction drift=false
                         （复用 tools/release_legion_ide.py status()）
  8 patchset_evidence    最新 patchset-*.json 存在且 signature 非空；
                         可读 cap_master.key 时追加 HMAC-SHA256 校验（只读，不落盘）
  9 m4_signoff           只读 approvals.jsonl 的 approve 计数 + dispatch accepted/done，
                         输出 missing 四方（QA1/GUANYU/VERIFY1/A0·老田）；不代签
 10 fast_gate            最近 fast gate 日志 PASS/FAIL（advisory，不参与总体判定）

总体判定：required 检查有 fail → fail；无 fail 但有 unknown → unknown；全 pass → pass。
退出码：0=pass；1=fail；2=unknown（无 fail 但有未判定项）。

只读边界（硬约束）：
  - 不写 approvals.jsonl / dispatch / 任务板 / evidence，不点卡、不代签；
  - 不发 POST 写请求：唯一的非 GET 探针是 patchset/preview（无会话时在认证门即被 403 拒绝，
    不进入写/执行路径）；
  - 不重启任何服务、不切 junction、不发布 artifact。
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import glob
import hashlib
import hmac
import http.client
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

SCHEMA = "trust_chain_kpi/v1"
REPO = Path(__file__).resolve().parents[1]
DEFAULT_TIMEOUT = 3.0
DEFAULT_M4_SINCE = "2026-10-31T00:00:00"
M4_PARTIES = ("QA1", "GUANYU", "VERIFY1")
A0_PARTY = "A0/老田"
LDE_BASE = "http://127.0.0.1:8103"
HERMES_BASE = "http://127.0.0.1:8787"
DEFAULT_RELEASE_ROOT_FALLBACK = Path(r"C:\项目\_vault\legion_ide\release\legion-ide")
DEFAULT_EVIDENCE_DIR = Path(r"C:\项目\_vault\legion_ide\evidence")
DEFAULT_CAP_KEY = Path(r"C:\项目\_vault\legion_ide\cap_master.key")
DEFAULT_GATE_LOG_DIR = Path(r"C:\Temp")
GATE_LOG_PATTERNS = ("test_gate_*.log", "gate_after_*.log", "audit_gate_*.log")
GATE_STALE_SECONDS = 24 * 3600
# 无会话/无 cap 的 patchset 写面必须 4xx fail-closed。8103 的 cap 门在会话之后：
# 匿名请求先被会话门拦住（no_session），拿到会话但无 cap 才报 cap_required。
FAIL_CLOSED_CAP_CODES = {
    "cap_required", "cap_invalid", "cap_missing", "cap_expired", "cap_scope_mismatch",
    "no_session", "rw_disabled", "identity_required",
}
CATEGORY_TRUST_CHAIN = "trust_chain"
CATEGORY_SIGNOFF = "signoff"
CATEGORY_ADVISORY = "advisory"


@dataclasses.dataclass
class Ctx:
    """一次 KPI 采集的只读上下文（路径/超时集中注入，便于单测完全离线）。"""

    repo: Path
    timeout: float
    lde_base: str
    hermes_base: str
    release_root: Path
    evidence_dir: Path
    cap_key_path: Path
    approvals_path: Path
    dispatch_path: Path
    gate_log_dir: Path
    m4_since: str
    now: dt.datetime


# ---------------------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------------------
def _load_module(path: Path, name: str):
    """按文件路径加载工具模块（tools/ 不是 package，统一走 importlib）。

    注意：exec 前先注册到 sys.modules——被加载模块若用 dataclass +
    ``from __future__ import annotations``，dataclass 会按 ``cls.__module__``
    查 sys.modules，缺注册会 AttributeError。
    """
    path = Path(path)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("无法加载模块: %s" % path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


def _default_release_root(repo: Path) -> Path:
    """与 tools/release_legion_ide.py 的 DEFAULT_RELEASE_ROOT 保持单一来源。"""
    try:
        mod = _load_module(Path(repo) / "tools" / "release_legion_ide.py",
                           "kpi_release_legion_ide")
        return Path(getattr(mod, "DEFAULT_RELEASE_ROOT", DEFAULT_RELEASE_ROOT_FALLBACK))
    except Exception:  # noqa: BLE001 - 读取失败用常量兜底，不阻塞采集
        return DEFAULT_RELEASE_ROOT_FALLBACK


def _http(base: str, method: str, path: str,
          payload: Optional[dict] = None,
          timeout: float = DEFAULT_TIMEOUT) -> dict:
    """只读 HTTP 探针：不跟随重定向、不持有 cookie，返回精确状态码。"""
    parsed = urlparse(base)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    conn = None
    try:
        if parsed.scheme == "https":
            conn = http.client.HTTPSConnection(host, port, timeout=timeout)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
        headers = {"Accept": "application/json", "Connection": "close"}
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        text = raw.decode("utf-8", errors="replace")
        data = None
        if text.strip():
            try:
                data = json.loads(text)
            except ValueError:
                data = None
        return {"reachable": True, "status": int(resp.status), "json": data,
                "snippet": text[:240].replace("\n", " "), "error": ""}
    except Exception as exc:  # noqa: BLE001 - 不可达/超时一律 fail-soft → unknown
        return {"reachable": False, "status": None, "json": None, "snippet": "",
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    finally:
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass


def _result(cid: str, title: str, status: str, detail: str,
            evidence: Optional[dict] = None, *,
            required: bool = True,
            category: str = CATEGORY_TRUST_CHAIN) -> dict:
    return {"id": cid, "title": title, "status": status, "detail": detail,
            "evidence": evidence or {}, "required": bool(required),
            "category": category}


def _unreachable(cid: str, title: str, url: str, probe: dict) -> dict:
    return _result(cid, title, "unknown", "不可达/超时（不阻塞其它检查）：%s · %s"
                   % (url, probe.get("error") or "-"),
                   {"url": url, "reachable": False, "error": probe.get("error") or ""})


def _body_json(probe: dict) -> dict:
    data = probe.get("json")
    return data if isinstance(data, dict) else {}


def _error_code(probe: dict) -> str:
    return str(_body_json(probe).get("error") or "")


# ---------------------------------------------------------------------------
# 1) 凭证入口 = 5，无第 6
# ---------------------------------------------------------------------------
def check_auth_surfaces(ctx: Ctx) -> dict:
    cid, title = "auth_surfaces", "凭证入口=5、无第 6"
    checker = Path(ctx.repo) / "tools" / "check_auth_surfaces.py"
    try:
        report = _load_module(checker, "kpi_check_auth_surfaces").check()
    except Exception as exc:  # noqa: BLE001 - 检查器不可用 → unknown
        return _result(cid, title, "unknown", "检查器不可用（fail-soft）：%s: %s"
                       % (type(exc).__name__, str(exc)[:160]),
                       {"checker": str(checker)})
    ok = bool(report.get("ok"))
    ev = {k: report.get(k) for k in
          ("count", "problems", "decision", "sso", "surfaces", "error") if k in report}
    detail = "count=%s ok=%s decision=%s" % (
        report.get("count"), ok, report.get("decision") or "-")
    if not ok:
        detail += " problems=%s" % (report.get("problems") or report.get("error") or "-")
    return _result(cid, title, "pass" if ok else "fail", detail, ev)


# ---------------------------------------------------------------------------
# 2) 8103 /api/health
# ---------------------------------------------------------------------------
def check_ide_health_8103(ctx: Ctx) -> dict:
    cid, title = "ide_health_8103", "8103 /api/health=200"
    url = ctx.lde_base.rstrip("/") + "/api/health"
    probe = _http(ctx.lde_base, "GET", "/api/health", timeout=ctx.timeout)
    if not probe["reachable"]:
        return _unreachable(cid, title, url, probe)
    body = _body_json(probe)
    ok = probe["status"] == 200 and body.get("ok") is True
    ev = {"url": url, "status": probe["status"], "body_ok": body.get("ok"),
          "pid": body.get("pid"), "rw_enabled": body.get("rw_enabled")}
    return _result(cid, title, "pass" if ok else "fail",
                   "status=%s body.ok=%s rw_enabled=%s"
                   % (probe["status"], body.get("ok"), body.get("rw_enabled")), ev)


# ---------------------------------------------------------------------------
# 3) 8103 无 token /rw
# ---------------------------------------------------------------------------
def check_ide_rw_anon(ctx: Ctx) -> dict:
    cid, title = "ide_rw_anon_8103", "8103 无 token /rw=401/403（fail-closed）"
    url = ctx.lde_base.rstrip("/") + "/rw"
    probe = _http(ctx.lde_base, "GET", "/rw", timeout=ctx.timeout)
    if not probe["reachable"]:
        return _unreachable(cid, title, url, probe)
    code = _error_code(probe)
    ok = probe["status"] in (401, 403)
    detail = "status=%s error=%s" % (probe["status"], code or "-")
    if probe["status"] == 200:
        detail += "（直连 /rw 未做认证，裸奔）"
    elif not ok:
        detail += "（预期 401 或 403 identity）"
    return _result(cid, title, "pass" if ok else "fail", detail,
                   {"url": url, "status": probe["status"], "error": code,
                    "expected": "401 或 403 identity"})


# ---------------------------------------------------------------------------
# 4) 8103 无会话/无 cap patchset
# ---------------------------------------------------------------------------
def check_ide_patchset_no_cap(ctx: Ctx) -> dict:
    cid, title = "ide_patchset_no_cap_8103", "8103 无会话/无 cap patchset=4xx fail-closed"
    url = ctx.lde_base.rstrip("/") + "/api/ide/patchset/preview"
    probe = _http(ctx.lde_base, "POST", "/api/ide/patchset/preview",
                  payload={"task": "__m1_kpi_readonly_probe__"}, timeout=ctx.timeout)
    if not probe["reachable"]:
        return _unreachable(cid, title, url, probe)
    status, code = probe["status"], _error_code(probe)
    fail_closed = status in (401, 403) and (not code or code in FAIL_CLOSED_CAP_CODES)
    cap_required = code == "cap_required"
    ev = {"url": url, "status": status, "error": code, "fail_closed": fail_closed,
          "cap_required_observed": cap_required,
          "accepted_fail_closed_codes": sorted(FAIL_CLOSED_CAP_CODES)}
    if fail_closed:
        note = ""
        if not cap_required:
            note = "；cap 门在会话之后，匿名请求先报 %s（仍为 fail-closed）" % (code or "-")
        detail = "status=%s error=%s%s" % (status, code or "-", note)
        return _result(cid, title, "pass", detail, ev)
    detail = "status=%s error=%s（预期 401/403 fail-closed，实际未拒绝）" % (status, code or "-")
    return _result(cid, title, "fail", detail, ev)


# ---------------------------------------------------------------------------
# 5) 8787 /api/sessions（SSO 生效）
# ---------------------------------------------------------------------------
def check_hermes_sessions_8787(ctx: Ctx) -> dict:
    cid, title = "hermes_sessions_8787", "8787 无 cookie /api/sessions=401（SSO 生效）"
    url = ctx.hermes_base.rstrip("/") + "/api/sessions"
    probe = _http(ctx.hermes_base, "GET", "/api/sessions", timeout=ctx.timeout)
    if not probe["reachable"]:
        return _unreachable(cid, title, url, probe)
    code = _error_code(probe)
    snippet = str(probe.get("snippet") or "")
    ok = probe["status"] == 401
    detail = "status=%s error=%s" % (probe["status"], code or snippet[:60] or "-")
    if probe["status"] == 200:
        detail += "（未登录可直接读会话，SSO 未生效）"
    elif not ok:
        detail += "（预期 401）"
    return _result(cid, title, "pass" if ok else "fail", detail,
                   {"url": url, "status": probe["status"], "snippet": snippet[:160]})


# ---------------------------------------------------------------------------
# 6) 8103 /api/ide/version
# ---------------------------------------------------------------------------
def check_ide_version(ctx: Ctx) -> dict:
    cid, title = "ide_version_8103", "8103 /api/ide/version=200 且 all_match=true"
    url = ctx.lde_base.rstrip("/") + "/api/ide/version"
    probe = _http(ctx.lde_base, "GET", "/api/ide/version", timeout=ctx.timeout)
    if not probe["reachable"]:
        return _unreachable(cid, title, url, probe)
    body = _body_json(probe)
    ok = (probe["status"] == 200 and body.get("ok") is True
          and body.get("all_match") is True)
    ev = {"url": url, "status": probe["status"], "version": body.get("version"),
          "commit": body.get("commit"), "all_match": body.get("all_match")}
    return _result(cid, title, "pass" if ok else "fail",
                   "status=%s version=%s all_match=%s"
                   % (probe["status"], body.get("version") or "-", body.get("all_match")), ev)


# ---------------------------------------------------------------------------
# 7) 发布 artifact 指针 ↔ junction drift
# ---------------------------------------------------------------------------
def _load_release_module(ctx: Ctx):
    """返回 (module, error)；加载失败由调用方转 unknown。"""
    path = Path(ctx.repo) / "tools" / "release_legion_ide.py"
    try:
        return _load_module(path, "kpi_release_legion_ide_status"), ""
    except Exception as exc:  # noqa: BLE001
        return None, "release_legion_ide.py 不可用：%s: %s" % (
            type(exc).__name__, str(exc)[:160])


def check_release_artifact_drift(ctx: Ctx) -> dict:
    cid, title = "release_artifact_drift", "current.json 指针↔junction drift=false"
    mod, err = _load_release_module(ctx)
    if mod is None:
        return _result(cid, title, "unknown", err, {"error": err})
    try:
        st = mod.status(Path(ctx.release_root))
    except Exception as exc:  # noqa: BLE001
        return _result(cid, title, "unknown", "status() 不可读（fail-soft）：%s: %s"
                       % (type(exc).__name__, str(exc)[:160]),
                       {"release_root": str(ctx.release_root)})
    pointer = str(st.get("pointer_version") or "")
    junction = st.get("junction") or {}
    drift = bool(st.get("drift"))
    j_ver = str(junction.get("version") or "")
    j_exists = bool(junction.get("exists"))
    j_target_ok = bool(junction.get("target_exists"))
    ok = bool(pointer) and not drift and j_exists and j_target_ok and j_ver == pointer
    ev = {"release_root": str(ctx.release_root), "pointer_version": pointer or None,
          "junction_version": j_ver or None, "junction_target": junction.get("target"),
          "junction_exists": j_exists, "junction_target_exists": j_target_ok,
          "drift": drift}
    detail = "pointer=%s junction=%s drift=%s target_exists=%s" % (
        pointer or "-", j_ver or "-", drift, j_target_ok)
    return _result(cid, title, "pass" if ok else "fail", detail, ev)


# ---------------------------------------------------------------------------
# 8) patchset evidence（存在 + signature，可选 HMAC 校验）
# ---------------------------------------------------------------------------
def _verify_evidence_signature(rec: dict, key_path: Path) -> tuple:
    """返回 (verified, note)：true/false/unknown（key 不可读时绝不伪造结论）。"""
    try:
        key = Path(key_path).read_bytes().strip()
    except OSError as exc:
        return "unknown", "cap key 不可读（%s）" % type(exc).__name__
    if not key:
        return "unknown", "cap key 为空"
    try:
        body = json.dumps({k: v for k, v in rec.items() if k != "signature"},
                          ensure_ascii=False, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as exc:
        return "unknown", "evidence 无法序列化（%s）" % type(exc).__name__
    expected = hmac.new(key, body, hashlib.sha256).hexdigest()
    present = str(rec.get("signature") or "")
    return ("true" if hmac.compare_digest(expected, present) else "false"), ""


def check_patchset_evidence(ctx: Ctx) -> dict:
    cid, title = "patchset_evidence", "最新 patchset-* evidence 存在且带 signature"
    directory = Path(ctx.evidence_dir)
    if not directory.is_dir():
        return _result(cid, title, "unknown", "evidence 目录不存在：%s" % directory,
                       {"evidence_dir": str(directory), "dir_exists": False})
    try:
        files = sorted(Path(p) for p in directory.glob("patchset-*.json"))
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError as exc:
        return _result(cid, title, "unknown", "evidence 目录不可读：%s: %s"
                       % (type(exc).__name__, str(exc)[:160]),
                       {"evidence_dir": str(directory)})
    if not files:
        return _result(cid, title, "fail", "无 patchset-*.json evidence：%s" % directory,
                       {"evidence_dir": str(directory), "count": 0})
    latest = None
    record: object = None
    read_errors = []
    for cand in files[:3]:
        try:
            record = json.loads(cand.read_text(encoding="utf-8"))
            latest = cand
            break
        except (OSError, ValueError) as exc:
            read_errors.append("%s: %s" % (cand.name, type(exc).__name__))
    if latest is None:
        return _result(cid, title, "fail", "最新 evidence 不可读（可能正被并发写入）：%s"
                       % "; ".join(read_errors),
                       {"evidence_dir": str(directory), "read_errors": read_errors})
    if not isinstance(record, dict):
        return _result(cid, title, "fail", "最新 evidence 非 JSON 对象：%s" % latest.name,
                       {"file": str(latest)})
    mtime = latest.stat().st_mtime
    age_hours = max(0.0, (ctx.now.timestamp() - mtime) / 3600.0)
    signature = str(record.get("signature") or "").strip()
    ev = {"evidence_dir": str(directory), "file": str(latest),
          "mtime": dt.datetime.fromtimestamp(mtime).astimezone().isoformat(timespec="seconds"),
          "age_hours": round(age_hours, 1), "run_id": record.get("run_id"),
          "task": record.get("task"), "ok": record.get("ok"),
          "finalized_at": record.get("finalized_at"),
          "signature_present": bool(signature), "signature_verified": "not_checked"}
    if not signature:
        return _result(cid, title, "fail",
                       "最新 patchset evidence 缺 signature：%s" % latest.name, ev)
    verified, note = _verify_evidence_signature(record, Path(ctx.cap_key_path))
    ev["signature_verified"] = verified
    if verified == "false":
        detail = "signature 校验失败（内容与 HMAC 不符）：%s" % latest.name
        return _result(cid, title, "fail", detail, ev)
    detail = "%s signature ✔ verified=%s%s age=%.1fh" % (
        latest.name, verified, ("（%s）" % note) if note else "", age_hours)
    return _result(cid, title, "pass", detail, ev)


# ---------------------------------------------------------------------------
# 9) M4 签字（只读，不代签）
# ---------------------------------------------------------------------------
def _load_jsonl(path: Path) -> tuple:
    rows = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [], "%s: %s" % (type(exc).__name__, str(exc)[:120])
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows, ""


def check_m4_signoff(ctx: Ctx) -> dict:
    cid, title = "m4_signoff", "M4 四方签字（只读，不代签）"
    approvals, appr_err = _load_jsonl(Path(ctx.approvals_path))
    dispatch, disp_err = _load_jsonl(Path(ctx.dispatch_path))
    if appr_err and disp_err:
        return _result(cid, title, "unknown", "审批/派单台账均不可读：%s；%s"
                       % (appr_err, disp_err),
                       {"approvals": str(ctx.approvals_path),
                        "dispatch": str(ctx.dispatch_path)},
                       category=CATEGORY_SIGNOFF)
    approves = [r for r in approvals if isinstance(r, dict)
                and str(r.get("ts") or "") >= ctx.m4_since
                and str(r.get("action") or "") == "approve"
                and "m4" in str(r.get("task") or "").lower()]
    done = set()
    for row in dispatch:
        if not isinstance(row, dict):
            continue
        if "M4" not in str(row.get("task") or ""):
            continue
        if str(row.get("status") or "") not in ("accepted", "done"):
            continue
        target = str(row.get("to") or "")
        for party in M4_PARTIES:
            if party.lower() in target.lower():
                done.add(party)
    missing = [party for party in M4_PARTIES if party not in done]
    if not approves:
        missing.append(A0_PARTY)
    passed = not missing
    ev = {"since": ctx.m4_since,
          "approvals_count": len(approves),
          "operators": sorted({str(r.get("operator") or "") for r in approves}),
          "dispatch_done": sorted(done), "missing": missing,
          "approvals_error": appr_err, "dispatch_error": disp_err,
          "note": "只读计数；签字须 owner 本人完成，本工具不代签"}
    detail = "approve=%d dispatch_done=%s missing=%s" % (
        len(approves), ",".join(sorted(done)) or "-", ",".join(missing) or "-")
    return _result(cid, title, "pass" if passed else "fail", detail, ev,
                   category=CATEGORY_SIGNOFF)


# ---------------------------------------------------------------------------
# 10) fast gate 最近结果（advisory）
# ---------------------------------------------------------------------------
def _read_text_any(path: Path) -> str:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-16", "utf-8"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace")


def check_fast_gate(ctx: Ctx) -> dict:
    cid, title = "fast_gate", "最近 fast gate 结果（可选）"
    candidates = []
    for pattern in GATE_LOG_PATTERNS:
        candidates.extend(glob.glob(str(Path(ctx.gate_log_dir) / pattern)))
    try:
        files = sorted({Path(p) for p in candidates},
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        files = []
    if not files:
        return _result(cid, title, "unknown", "无 gate 日志可读（可选检查，不阻塞）",
                       {"gate_log_dir": str(ctx.gate_log_dir)}, required=False,
                       category=CATEGORY_ADVISORY)
    latest = files[0]
    try:
        text = _read_text_any(latest)
        mtime = latest.stat().st_mtime
    except OSError as exc:
        return _result(cid, title, "unknown", "gate 日志不可读：%s: %s"
                       % (type(exc).__name__, str(exc)[:160]),
                       {"log": str(latest)}, required=False,
                       category=CATEGORY_ADVISORY)
    if "GATE PASS" in text:
        gate_status = "pass"
    elif "GATE FAIL" in text:
        gate_status = "fail"
    else:
        gate_status = "unknown"
    age_hours = max(0.0, (ctx.now.timestamp() - mtime) / 3600.0)
    stale = age_hours > GATE_STALE_SECONDS / 3600.0
    ev = {"log": str(latest),
          "mtime": dt.datetime.fromtimestamp(mtime).astimezone().isoformat(timespec="seconds"),
          "age_hours": round(age_hours, 1), "stale": stale, "result": gate_status}
    detail = "%s result=%s age=%.1fh%s" % (
        latest.name, gate_status, age_hours, "（过期，仅供参考）" if stale else "")
    return _result(cid, title, gate_status, detail, ev, required=False,
                   category=CATEGORY_ADVISORY)


# ---------------------------------------------------------------------------
# 采集 / 渲染 / CLI
# ---------------------------------------------------------------------------
_CHECKS = (
    ("auth_surfaces", "check_auth_surfaces"),
    ("ide_health_8103", "check_ide_health_8103"),
    ("ide_rw_anon_8103", "check_ide_rw_anon"),
    ("ide_patchset_no_cap_8103", "check_ide_patchset_no_cap"),
    ("hermes_sessions_8787", "check_hermes_sessions_8787"),
    ("ide_version_8103", "check_ide_version"),
    ("release_artifact_drift", "check_release_artifact_drift"),
    ("patchset_evidence", "check_patchset_evidence"),
    ("m4_signoff", "check_m4_signoff"),
    ("fast_gate", "check_fast_gate"),
)


def _resolve_check(name: str) -> Callable[[Ctx], dict]:
    fn = globals().get(name)
    if not callable(fn):
        raise RuntimeError("未注册检查: %s" % name)
    return fn


def collect(repo: Optional[Path] = None, *, timeout: Optional[float] = None,
            lde_base: Optional[str] = None, hermes_base: Optional[str] = None,
            release_root: Optional[Path] = None, evidence_dir: Optional[Path] = None,
            cap_key_path: Optional[Path] = None,
            approvals_path: Optional[Path] = None,
            dispatch_path: Optional[Path] = None,
            gate_log_dir: Optional[Path] = None,
            m4_since: Optional[str] = None,
            now: Optional[dt.datetime] = None,
            checks: Optional[list] = None) -> dict:
    """执行全部只读检查并返回报告（任何单检查异常都转 unknown，不中断）。"""
    repo = Path(repo or REPO)
    ctx = Ctx(
        repo=repo,
        timeout=float(DEFAULT_TIMEOUT if timeout is None else timeout),
        lde_base=str(lde_base or LDE_BASE).rstrip("/"),
        hermes_base=str(hermes_base or HERMES_BASE).rstrip("/"),
        release_root=Path(release_root) if release_root else _default_release_root(repo),
        evidence_dir=Path(evidence_dir) if evidence_dir else DEFAULT_EVIDENCE_DIR,
        cap_key_path=Path(cap_key_path) if cap_key_path else DEFAULT_CAP_KEY,
        approvals_path=(Path(approvals_path) if approvals_path
                        else repo / "agent_platform" / "data" / "approvals.jsonl"),
        dispatch_path=(Path(dispatch_path) if dispatch_path
                       else repo / "work" / "tianfeng_dispatch.jsonl"),
        gate_log_dir=Path(gate_log_dir) if gate_log_dir else DEFAULT_GATE_LOG_DIR,
        m4_since=str(m4_since or DEFAULT_M4_SINCE),
        now=now or dt.datetime.now().astimezone(),
    )
    entries = (list(checks) if checks is not None
               else [(cid, _resolve_check(fn)) for cid, fn in _CHECKS])
    started = time.time()
    results = []
    for cid, fn in entries:
        t0 = time.perf_counter()
        try:
            res = fn(ctx)
            if not isinstance(res, dict):
                raise TypeError("检查返回值必须是 dict，实际 %s" % type(res).__name__)
            res.setdefault("id", cid)
            res.setdefault("title", cid)
            res.setdefault("status", "unknown")
            res.setdefault("detail", "")
            res.setdefault("evidence", {})
            res.setdefault("required", True)
            res.setdefault("category", CATEGORY_TRUST_CHAIN)
        except Exception as exc:  # noqa: BLE001 - 检查级 fail-soft
            res = _result(cid, cid, "unknown", "检查异常（fail-soft）：%s: %s"
                          % (type(exc).__name__, str(exc)[:200]))
        res["duration_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        results.append(res)
    required = [c for c in results if c.get("required")]
    fails = [c["id"] for c in required if c.get("status") == "fail"]
    unknowns = [c["id"] for c in required if c.get("status") == "unknown"]
    passes = [c["id"] for c in required if c.get("status") == "pass"]
    verdict = "fail" if fails else ("unknown" if unknowns else "pass")
    report = {
        "schema": SCHEMA,
        "generated_at": ctx.now.astimezone().isoformat(timespec="seconds"),
        "repo": str(repo),
        "verdict": verdict,
        "passed": verdict == "pass",
        "counts": {"pass": len(passes), "fail": len(fails),
                   "unknown": len(unknowns), "total": len(required),
                   "checks_total": len(results)},
        "failing": fails,
        "unknown": unknowns,
        "checks": results,
        "note": ("只读 KPI：unknown=服务不可达/超时或数据源缺失，不阻塞其它检查；"
                 "不写 approval/任务板、不重启服务、不代签。"),
    }
    report["duration_ms"] = round((time.time() - started) * 1000, 1)
    return report


_STATUS_LABEL = {"pass": "PASS", "fail": "FAIL", "unknown": "UNKNOWN"}
_DATA_SOURCES = (
    ("8103 LDE", "http://127.0.0.1:8103（/api/health、/rw、/api/ide/version、patchset/preview）"),
    ("8787 Hermes", "http://127.0.0.1:8787（/api/sessions）"),
    ("凭证入口", "tools/check_auth_surfaces.py → config/auth_surfaces.json"),
    ("发布单元", "tools/release_legion_ide.py status() → _vault\\legion_ide\\release\\legion-ide"),
    ("证据", "patchset-*.json → _vault\\legion_ide\\evidence（+ cap_master.key HMAC）"),
    ("M4 台账", "agent_platform/data/approvals.jsonl、work/tianfeng_dispatch.jsonl"),
    ("fast gate", "C:\\Temp\\test_gate_*.log / gate_after_*.log / audit_gate_*.log"),
)


def _md_cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def render_markdown(report: dict) -> str:
    counts = report.get("counts") or {}
    lines = [
        "# M1 信任链 KPI · 只读快照",
        "",
        "> verdict: **%s** · generated_at %s · required 通过 %s / 失败 %s / 未知 %s（共 %s 项）"
        % (str(report.get("verdict") or "?").upper(), report.get("generated_at") or "-",
           counts.get("pass"), counts.get("fail"), counts.get("unknown"), counts.get("total")),
        "> 只读：不写 approval/任务板、不重启服务、不代签；unknown=不可达/超时，不阻塞其它检查。",
        "",
        "| # | 类别 | 检查 | 状态 | 摘要 |",
        "|---|---|---|---|---|",
    ]
    for index, check in enumerate(report.get("checks") or [], 1):
        lines.append("| %d | %s | %s | %s | %s |" % (
            index, _md_cell(str(check.get("category") or "-")),
            _md_cell(str(check.get("title") or check.get("id") or "-")),
            _STATUS_LABEL.get(str(check.get("status")), "UNKNOWN"),
            _md_cell(str(check.get("detail") or ""))))
    issues = [c for c in report.get("checks") or []
              if str(c.get("status")) in ("fail", "unknown")]
    lines.append("")
    if issues:
        lines.append("## 未决 / 未判定明细")
        lines.append("")
        for check in issues:
            lines.append("- **%s**（%s，%s）：%s" % (
                check.get("id"), _STATUS_LABEL.get(str(check.get("status")), "UNKNOWN"),
                "required" if check.get("required") else "optional",
                check.get("detail") or "-"))
    else:
        lines.append("无未决项。")
    lines.extend(["", "## 数据源", ""])
    for name, source in _DATA_SOURCES:
        lines.append("- %s：%s" % (name, source))
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="M1 信任链 KPI/证据只读检查（fail-soft；不写账本/不重启服务）")
    fmt = parser.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
    fmt.add_argument("--markdown", action="store_true", help="输出 Markdown（默认）")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help="HTTP 探针超时秒（默认 %s）" % DEFAULT_TIMEOUT)
    parser.add_argument("--repo", default="",
                        help="主仓根（默认脚本所在仓库；worktree 内跑时指主仓）")
    parser.add_argument("--release-root", default="", help="release artifact 根目录")
    parser.add_argument("--evidence-dir", default="", help="legion_ide evidence 目录")
    parser.add_argument("--cap-key", default="", help="evidence HMAC 校验用 cap_master.key 路径")
    parser.add_argument("--approvals", default="", help="approvals.jsonl 路径")
    parser.add_argument("--dispatch", default="", help="tianfeng_dispatch.jsonl 路径")
    parser.add_argument("--gate-log-dir", default="", help="fast gate 日志目录")
    since_default = DEFAULT_M4_SINCE
    parser.add_argument("--m4-since", default=since_default,
                        help="M4 approve 统计起点（ISO，默认 %s）" % since_default)
    parser.add_argument("--out", default="",
                        help="可选：同时把报告写入此文件（只写该文件，不写账本）")
    args = parser.parse_args(argv)
    report = collect(
        repo=args.repo or None,
        timeout=args.timeout,
        release_root=args.release_root or None,
        evidence_dir=args.evidence_dir or None,
        cap_key_path=args.cap_key or None,
        approvals_path=args.approvals or None,
        dispatch_path=args.dispatch or None,
        gate_log_dir=args.gate_log_dir or None,
        m4_since=args.m4_since,
    )
    text = (json.dumps(report, ensure_ascii=False, indent=1)
            if args.json else render_markdown(report))
    print(text)
    if args.out:
        try:
            target = Path(args.out)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text + ("\n" if not text.endswith("\n") else ""),
                              encoding="utf-8")
        except OSError as exc:
            print("[warn] --out 写入失败（不影响判定）：%s" % exc, file=sys.stderr)
    verdict = str(report.get("verdict") or "unknown")
    return 0 if verdict == "pass" else (1 if verdict == "fail" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
