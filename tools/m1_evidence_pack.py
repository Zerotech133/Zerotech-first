# -*- coding: utf-8 -*-
"""M1 信任链证据包聚合器（M1 evidence pack，只读一条命令）。

把分散的信任链证据聚合为一份可复核的 M1 evidence pack，供 2026-11-03 验收引用：

  python -X utf8 tools/m1_evidence_pack.py                    # 跑三工具 → deliverables/M1_evidence_pack_<date>.md/.json
  python -X utf8 tools/m1_evidence_pack.py --json             # 同上，stdout 输出机器可读 JSON
  python -X utf8 tools/m1_evidence_pack.py --markdown         # stdout 输出 Markdown（默认）
  python -X utf8 tools/m1_evidence_pack.py --out C:\\Temp\\pack --no-write
  # 离线/复算：直接复用已捕获的 JSON（不跑子进程；也用于单测 mock）
  python -X utf8 tools/m1_evidence_pack.py --kpi kpi.json --drill drill.json \
      --invariant invariant.json --gate-log gate.log --evidence-dir <dir>

输入（全部只读）：
  1) ``tools/trust_chain_kpi.py --json``（M1 信任链 KPI；M4 状态由其中 m4_signoff 派生）；
  2) ``tools/release_rollback_drill.py --json``（W1 发布回滚演练）；
  3) ``tools/worktree_invariant_check.py --json``（W2 工作树/abort 不变量）；
  4) 最新 fast gate 日志（``C:\\Temp\\test_gate_*.log`` 等，可选、advisory）；
  5) ``_vault/legion_ide/evidence/`` 最新 patchset/run artifact 列表（只读清单）；
  6) M4 approvals/dispatch 只读状态（经 KPI ``m4_signoff`` 检查派生，不代签）。

判定口径：
  - 必需判据 = M1-KPI / W1-DRILL / W2-INVARIANT；**任一 fail → pack fail**；
  - 无 fail 但有必需项 unknown（工具缺失/超时/输出非 JSON） → pack unknown；
  - 全 pass → pack pass；advisory 项（FAST-GATE/EVIDENCE/M4 派生）不参与总判定，
    但非 pass 项全部进「缺口清单」。
  - **失败项一律标 unknown，不伪造数值**：读不到就如实记录错误与路径。

退出码：``0`` pass；``1`` fail；``2`` unknown（含参数错误）。
安全边界：只读；不写 approval/任务板/tasks.json、不重启服务、不代签、不 promote artifact。
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SCHEMA = "m1-evidence-pack/v1"
REPO = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
DEFAULT_OUT_DIR = REPO / "deliverables"
DEFAULT_EVIDENCE_DIR = Path(r"C:\项目\_vault\legion_ide\evidence")
DEFAULT_GATE_LOG_DIR = Path(r"C:\Temp")
GATE_LOG_PATTERNS = ("test_gate_*.log", "gate_after_*.log", "audit_gate_*.log")
GATE_STALE_SECONDS = 24 * 3600
TOOL_TIMEOUT = 180.0
EVIDENCE_LIMIT = 8

STATUS_LABEL = {"pass": "PASS", "fail": "FAIL", "unknown": "UNKNOWN"}
TOOL_FILES = {
    "kpi": "trust_chain_kpi.py",
    "drill": "release_rollback_drill.py",
    "invariant": "worktree_invariant_check.py",
}
NOTES = (
    "只读聚合：不写 approval/任务板/tasks.json、不重启 8787/8103、不代签、不 promote artifact",
    "失败项（工具缺失/超时/输出非 JSON/文件不可读）一律标 unknown 并保留错误原文，不伪造数值",
    "M4 状态由 KPI m4_signoff 检查派生（只读计数），签字须 owner 本人完成",
    "fast gate 日志与 evidence 清单为 advisory：不参与总判定，但出现在缺口清单",
    "worktree 内运行时账本/配置以主仓为准（自动解析主仓并传 --repo 给 KPI/不变量工具）",
)


# ---------------------------------------------------------------------------
# 基础设施
# ---------------------------------------------------------------------------
def ensure_utf8_stdout() -> None:
    """Windows 控制台默认 cp936：统一 UTF-8，避免中文报告乱码/写失败。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - 非标准流
            pass


def resolve_main_repo(repo=None) -> Path:
    """把（可能的）linked worktree 解析回主仓根；失败则原样返回。"""
    repo = Path(repo or REPO)
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return repo
    if proc.returncode != 0:
        return repo
    for line in (proc.stdout or "").splitlines():
        if line.startswith("worktree "):
            candidate = Path(line[len("worktree "):].strip())
            if candidate.is_dir():
                return candidate
    return repo


def _run_git(repo: Path, *args) -> tuple[str, str]:
    """只读 git 调用，返回 (stdout, error)。"""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", "%s: %s" % (type(exc).__name__, str(exc)[:120])
    if proc.returncode != 0:
        return "", (proc.stderr or "").strip()[:200] or "git exit=%s" % proc.returncode
    return (proc.stdout or "").strip(), ""


def git_info(repo: Path) -> dict:
    """只读 commit/branch 快照。"""
    repo = Path(repo)
    info = {"repo": str(repo), "head": "", "short": "", "branch": "", "error": ""}
    head, err = _run_git(repo, "rev-parse", "HEAD")
    if head:
        info["head"] = head
        info["short"] = head[:12]
    else:
        info["error"] = err
    branch, _ = _run_git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    info["branch"] = branch
    return info


def _read_text_any(path: Path) -> str:
    """多编码容错读取（gate 日志可能 UTF-8 BOM / UTF-16）。"""
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-16", "utf-8"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace")


def _norm_status(value) -> str:
    """把各工具的自有口径归一为 pass/fail/unknown（不认识的一律 unknown，不猜）。"""
    text = str(value or "").strip().lower()
    if text in ("pass", "passed", "ok", "true", "pass "):
        return "pass"
    if text in ("fail", "failed", "false"):
        return "fail"
    return "unknown"


def _criterion(cid: str, title: str, required: bool, status: str, detail: str,
               source: str = "", derived: bool = False) -> dict:
    return {
        "id": cid,
        "title": title,
        "required": bool(required),
        "status": _norm_status(status),
        "detail": str(detail or ""),
        "source": str(source or ""),
        "derived": bool(derived),
    }


# ---------------------------------------------------------------------------
# 输入 1~3：三个工具（子进程或已捕获 JSON 文件）
# ---------------------------------------------------------------------------
def _tool_extra_args(name: str, main_repo: Path, evidence_dir, gate_log) -> list[str]:
    """按工具支持面追加只读参数（账本/配置以主仓为准；evidence/gate 目录对齐）。"""
    extra: list[str] = []
    if name in ("kpi", "invariant"):
        extra += ["--repo", str(main_repo)]
    if name == "kpi":
        if evidence_dir:
            extra += ["--evidence-dir", str(evidence_dir)]
        if gate_log:
            extra += ["--gate-log-dir", str(Path(gate_log).parent)]
    return extra


def _run_tool(name: str, script: Path, extra_args: list[str],
              timeout: float, cwd: Path) -> dict:
    """子进程跑 ``<tool> --json``；退出码非 0 但 stdout 是合法 JSON 时仍视为采集成功。"""
    meta = {"name": name, "mode": "subprocess", "path": str(script), "report": None,
            "ok": False, "error": "", "exit_code": None, "duration_ms": 0,
            "stderr": ""}
    script = Path(script)
    if not script.is_file():
        meta["error"] = "工具不存在：%s" % script
        return meta
    cmd = [sys.executable, "-X", "utf8", str(script), "--json", *extra_args]
    started = time.perf_counter()
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        meta["duration_ms"] = int((time.perf_counter() - started) * 1000)
        meta["error"] = "工具超时（>%ss）：%s" % (timeout, script.name)
        return meta
    except OSError as exc:
        meta["duration_ms"] = int((time.perf_counter() - started) * 1000)
        meta["error"] = "工具无法执行：%s: %s" % (type(exc).__name__, str(exc)[:160])
        return meta
    meta["duration_ms"] = int((time.perf_counter() - started) * 1000)
    meta["exit_code"] = proc.returncode
    stderr = (proc.stderr or "").strip()
    meta["stderr"] = stderr[-400:]
    stdout = (proc.stdout or "").strip()
    if not stdout:
        meta["error"] = "工具无 JSON 输出（exit=%s）：%s" % (
            proc.returncode, stderr[-200:] or "-")
        return meta
    try:
        meta["report"] = json.loads(stdout)
    except ValueError as exc:
        meta["error"] = "工具 stdout 非 JSON（exit=%s）：%s: %s" % (
            proc.returncode, type(exc).__name__, str(exc)[:120])
        return meta
    meta["ok"] = True
    return meta


def _load_source(name: str, ref, *, main_repo: Path, timeout: float,
                 evidence_dir, gate_log) -> dict:
    """采集单个工具：``ref`` 为 None 跑子进程；否则当作已捕获 JSON 报告路径读取。"""
    if ref is None:
        script = Path(main_repo) / "tools" / TOOL_FILES[name]
        return _run_tool(name, script, _tool_extra_args(name, main_repo, evidence_dir, gate_log),
                         timeout, cwd=Path(main_repo))
    path = Path(ref)
    meta = {"name": name, "mode": "file", "path": str(path), "report": None,
            "ok": False, "error": "", "exit_code": None, "duration_ms": 0, "stderr": ""}
    if not path.is_file():
        meta["error"] = "报告文件不可读（不存在）：%s" % path
        return meta
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        meta["error"] = "报告文件不可读：%s: %s" % (type(exc).__name__, str(exc)[:160])
        return meta
    try:
        meta["report"] = json.loads(text)
    except ValueError as exc:
        meta["error"] = "报告文件非 JSON：%s（%s: %s）" % (
            path, type(exc).__name__, str(exc)[:120])
        return meta
    meta["ok"] = True
    return meta


# ---------------------------------------------------------------------------
# 输入 4：fast gate 日志（advisory）
# ---------------------------------------------------------------------------
def _discover_gate_log(gate_dir: Path) -> Path | None:
    candidates: list[Path] = []
    for pattern in GATE_LOG_PATTERNS:
        candidates.extend(Path(p) for p in glob.glob(str(Path(gate_dir) / pattern)))
    if not candidates:
        return None
    try:
        return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]
    except OSError:
        return None


def _parse_gate_text(text: str) -> dict:
    """从 gate 日志正文提取判定与 pytest 计数（只认显式 GATE PASS/FAIL，不猜）。"""
    result = "unknown"
    if "GATE PASS" in text:
        result = "pass"
    elif "GATE FAIL" in text:
        result = "fail"
    summary = ""
    for line in text.splitlines():
        if "GATE PASS" in line or "GATE FAIL" in line:
            summary = line.strip()
    counters = {}
    for key, pattern in (("passed", r"(\d+)\s+passed"),
                         ("failed", r"(\d+)\s+failed"),
                         ("skipped", r"(\d+)\s+skipped"),
                         ("errors", r"(\d+)\s+errors?")):
        counters[key] = sum(int(x) for x in re.findall(pattern, text))
    return {"result": result, "summary": summary, "pytest": counters}


def collect_gate(gate_log=None, now: dt.datetime | None = None,
                 gate_dir: Path | None = None) -> dict:
    """采集 fast gate 日志（显式路径优先；否则在 gate 目录里取最新一份）。"""
    now = now or dt.datetime.now().astimezone()
    gate_dir = Path(gate_dir or DEFAULT_GATE_LOG_DIR)
    meta = {"path": "", "explicit": bool(gate_log), "found": False, "dir": str(gate_dir),
            "mtime": "", "age_hours": None, "stale": False, "result": "unknown",
            "summary": "", "pytest": {}, "error": ""}
    path = Path(gate_log) if gate_log else _discover_gate_log(gate_dir)
    if path is None:
        meta["error"] = "无 fast gate 日志可读（%s；可选输入，不阻塞）" % gate_dir
        return meta
    meta["path"] = str(path)
    try:
        text = _read_text_any(path)
        mtime = path.stat().st_mtime
    except OSError as exc:
        meta["error"] = "gate 日志不可读：%s: %s" % (type(exc).__name__, str(exc)[:160])
        return meta
    meta["found"] = True
    parsed = _parse_gate_text(text)
    meta.update(parsed)
    meta["mtime"] = dt.datetime.fromtimestamp(mtime).astimezone().isoformat(timespec="seconds")
    age_hours = max(0.0, (now.timestamp() - mtime) / 3600.0)
    meta["age_hours"] = round(age_hours, 1)
    meta["stale"] = age_hours > GATE_STALE_SECONDS / 3600.0
    if not meta["error"] and meta["result"] == "unknown":
        meta["error"] = "gate 日志未见 GATE PASS/GATE FAIL（可能执行中/截断），不猜"
    return meta


# ---------------------------------------------------------------------------
# 输入 5：evidence 目录清单（advisory，只读）
# ---------------------------------------------------------------------------
def collect_evidence(evidence_dir=None, now: dt.datetime | None = None,
                     limit: int = EVIDENCE_LIMIT) -> dict:
    """列出 evidence 目录最新 patchset/run artifact（只读；缺目录 → unknown）。"""
    now = now or dt.datetime.now().astimezone()
    directory = Path(evidence_dir or DEFAULT_EVIDENCE_DIR)
    meta = {"dir": str(directory), "exists": directory.is_dir(), "counts": {},
            "latest": [], "error": "", "read_errors": []}
    if not meta["exists"]:
        meta["error"] = "evidence 目录不存在：%s" % directory
        return meta
    try:
        patchsets = list(directory.glob("patchset-*.json"))
        runs = list(directory.glob("run-*.json"))
    except OSError as exc:
        meta["error"] = "evidence 目录不可读：%s: %s" % (type(exc).__name__, str(exc)[:160])
        return meta
    meta["counts"] = {"patchset": len(patchsets), "run": len(runs),
                      "total": len(patchsets) + len(runs)}
    entries: list[dict] = []
    for kind, files in (("patchset", patchsets), ("run", runs)):
        for path in files:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            entry = {"kind": kind, "file": path.name,
                     "mtime": dt.datetime.fromtimestamp(mtime).astimezone().isoformat(timespec="seconds"),
                     "age_hours": round(max(0.0, (now.timestamp() - mtime) / 3600.0), 1),
                     "bytes": path.stat().st_size}
            if kind == "patchset":
                try:
                    record = json.loads(path.read_text(encoding="utf-8", errors="replace"))
                except (OSError, ValueError) as exc:
                    meta["read_errors"].append("%s: %s" % (path.name, type(exc).__name__))
                    record = None
                if isinstance(record, dict):
                    entry.update({
                        "run_id": record.get("run_id"),
                        "task": record.get("task"),
                        "ok": record.get("ok"),
                        "finalized_at": record.get("finalized_at"),
                        "signature_present": bool(str(record.get("signature") or "").strip()),
                    })
                else:
                    entry["signature_present"] = False
            entries.append(entry)
    entries.sort(key=lambda item: item["mtime"], reverse=True)
    meta["latest"] = entries[:limit]
    return meta


# ---------------------------------------------------------------------------
# 判据：三工具 + M4 派生
# ---------------------------------------------------------------------------
def _source_failure_detail(name: str, src: dict) -> str:
    return "%s 采集失败：%s" % (name, src.get("error") or "未知错误（不伪造）")


def _kpi_criterion(src: dict) -> dict:
    """M1 信任链 KPI 判据（必需）。"""
    if not src.get("ok"):
        return _criterion("M1-KPI", "M1 信任链 KPI（trust_chain_kpi）", True, "unknown",
                          _source_failure_detail("KPI", src), src.get("path", ""))
    report = src.get("report")
    if not isinstance(report, dict):
        return _criterion("M1-KPI", "M1 信任链 KPI（trust_chain_kpi）", True, "unknown",
                          "KPI 报告非 JSON 对象（不伪造）", src.get("path", ""))
    verdict = str(report.get("verdict") or "").strip().lower()
    if verdict not in ("pass", "fail", "unknown"):
        passed = report.get("passed")
        verdict = "pass" if passed is True else ("fail" if passed is False else "unknown")
        detail = "KPI 报告缺 verdict，按 passed=%r 归一（不猜）" % (passed,)
    else:
        counts = report.get("counts") or {}
        detail = "verdict=%s pass=%s fail=%s unknown=%s（共 %s 项）" % (
            verdict, counts.get("pass"), counts.get("fail"), counts.get("unknown"),
            counts.get("total"))
    return _criterion("M1-KPI", "M1 信任链 KPI（trust_chain_kpi）", True, verdict,
                      detail, src.get("path", ""))


def _drill_criterion(src: dict) -> dict:
    """W1 发布回滚演练判据（必需）。"""
    if not src.get("ok"):
        return _criterion("W1-DRILL", "W1 发布回滚演练（release_rollback_drill）", True,
                          "unknown", _source_failure_detail("回滚演练", src), src.get("path", ""))
    report = src.get("report")
    if not isinstance(report, dict):
        return _criterion("W1-DRILL", "W1 发布回滚演练（release_rollback_drill）", True,
                          "unknown", "演练报告非 JSON 对象（不伪造）", src.get("path", ""))
    ok = report.get("ok")
    status = "pass" if ok is True else ("fail" if ok is False else "unknown")
    summary = report.get("summary") or {}
    detail = "ok=%r 场景 %s/%s · 检查失败 %s" % (
        ok, summary.get("scenarios_passed"), summary.get("scenarios_total"),
        summary.get("checks_failed"))
    if report.get("error"):
        detail += " · error=%s" % str(report.get("error"))[:160]
    return _criterion("W1-DRILL", "W1 发布回滚演练（release_rollback_drill）", True,
                      status, detail, src.get("path", ""))


def _invariant_criterion(src: dict) -> dict:
    """W2 工作树/abort 不变量判据（必需）。"""
    if not src.get("ok"):
        return _criterion("W2-INVARIANT", "W2 工作树/abort 不变量（worktree_invariant_check）",
                          True, "unknown", _source_failure_detail("不变量检查", src),
                          src.get("path", ""))
    report = src.get("report")
    if not isinstance(report, dict):
        return _criterion("W2-INVARIANT", "W2 工作树/abort 不变量（worktree_invariant_check）",
                          True, "unknown", "不变量报告非 JSON 对象（不伪造）", src.get("path", ""))
    verdict = str(report.get("verdict") or "").strip()
    status = {"PASS": "pass", "FAIL": "fail"}.get(verdict.upper(), "unknown")
    counts = report.get("counts") or {}
    detail = "verdict=%s 阻断违规=%s 已知欠账=%s 工单=%s" % (
        verdict or "-", counts.get("violations"), counts.get("known_debt"),
        counts.get("tasks_total"))
    return _criterion("W2-INVARIANT", "W2 工作树/abort 不变量（worktree_invariant_check）",
                      True, status, detail, src.get("path", ""))


def _find_kpi_check(kpi_report, cid: str):
    if not isinstance(kpi_report, dict):
        return None
    for check in kpi_report.get("checks") or []:
        if isinstance(check, dict) and str(check.get("id")) == cid:
            return check
    return None


def _m4_criterion(kpi_src: dict) -> dict:
    """M4 四方签字判据（由 KPI m4_signoff 派生；advisory，不重复计入总判定）。"""
    if not kpi_src.get("ok") or not isinstance(kpi_src.get("report"), dict):
        return _criterion("M4-SIGNOFF", "M4 四方签字（只读派生）", False, "unknown",
                          "KPI 报告缺失，无法派生 M4 状态（不伪造；不代签）",
                          kpi_src.get("path", ""), derived=True)
    check = _find_kpi_check(kpi_src.get("report"), "m4_signoff")
    if check is None:
        return _criterion("M4-SIGNOFF", "M4 四方签字（只读派生）", False, "unknown",
                          "KPI 报告缺 m4_signoff 检查（不伪造）", kpi_src.get("path", ""),
                          derived=True)
    evidence = check.get("evidence") or {}
    missing = evidence.get("missing") or []
    detail = str(check.get("detail") or "")
    if missing and "missing=" not in detail:
        detail += " · missing=%s" % ",".join(str(x) for x in missing)
    return _criterion("M4-SIGNOFF", "M4 四方签字（只读派生）", False,
                      check.get("status"), detail, kpi_src.get("path", ""), derived=True)


def _gate_criterion(gate: dict) -> dict:
    """最新 fast gate（advisory）。"""
    if not gate.get("found"):
        return _criterion("FAST-GATE", "最新 fast gate 日志", False, "unknown",
                          gate.get("error") or "无 gate 日志", gate.get("path", ""))
    detail = "result=%s age=%sh%s" % (
        gate.get("result"), gate.get("age_hours"),
        "（过期，仅供参考）" if gate.get("stale") else "")
    if gate.get("summary"):
        detail += " · %s" % gate.get("summary")[:160]
    return _criterion("FAST-GATE", "最新 fast gate 日志", False,
                      gate.get("result"), detail, gate.get("path", ""))


def _evidence_criterion(evidence: dict) -> dict:
    """evidence 目录最新 artifact（advisory）。"""
    if not evidence.get("exists"):
        return _criterion("EVIDENCE", "legion_ide evidence 最新 artifact", False, "unknown",
                          evidence.get("error") or "evidence 目录不存在",
                          evidence.get("dir", ""))
    counts = evidence.get("counts") or {}
    latest = evidence.get("latest") or []
    latest_patchset = next((item for item in latest if item.get("kind") == "patchset"), None)
    status = "pass"
    detail = "patchset=%s run=%s 最新=%s" % (
        counts.get("patchset"), counts.get("run"),
        latest[0]["file"] if latest else "无")
    if not latest:
        status = "fail"
        detail = "evidence 目录为空（patchset=0 run=0）"
    elif latest_patchset is not None and latest_patchset.get("signature_present") is False:
        status = "fail"
        detail = "最新 patchset 缺 signature：%s" % latest_patchset.get("file")
    if evidence.get("read_errors"):
        detail += " · 读失败=%s" % "; ".join(evidence["read_errors"][:2])
    return _criterion("EVIDENCE", "legion_ide evidence 最新 artifact", False, status,
                      detail, evidence.get("dir", ""))


def _services_from_kpi(kpi_src: dict) -> dict:
    """从 KPI 报告派生服务版本/健康（只读；缺报告 → available=false，不伪造）。"""
    report = kpi_src.get("report")
    if not kpi_src.get("ok") or not isinstance(report, dict):
        return {"available": False, "detail": "KPI 报告缺失，服务版本无法聚合（不伪造）",
                "lde_8103": {}, "hermes_8787": {}, "release": {}, "patchset": {}}
    services: dict = {"available": True, "detail": ""}
    version_check = _find_kpi_check(report, "ide_version_8103") or {}
    version_ev = version_check.get("evidence") or {}
    services["lde_8103"] = {
        "status": version_check.get("status"),
        "version": version_ev.get("version"),
        "commit": version_ev.get("commit"),
        "all_match": version_ev.get("all_match"),
    }
    health_ev = (_find_kpi_check(report, "ide_health_8103") or {}).get("evidence") or {}
    services["lde_8103"]["health_ok"] = health_ev.get("body_ok")
    services["lde_8103"]["rw_enabled"] = health_ev.get("rw_enabled")
    hermes_check = _find_kpi_check(report, "hermes_sessions_8787") or {}
    services["hermes_8787"] = {"status": hermes_check.get("status"),
                               "detail": hermes_check.get("detail")}
    release_check = _find_kpi_check(report, "release_artifact_drift") or {}
    release_ev = release_check.get("evidence") or {}
    services["release"] = {
        "status": release_check.get("status"),
        "pointer_version": release_ev.get("pointer_version"),
        "junction_version": release_ev.get("junction_version"),
        "drift": release_ev.get("drift"),
        "junction_target_exists": release_ev.get("junction_target_exists"),
    }
    patchset_check = _find_kpi_check(report, "patchset_evidence") or {}
    patchset_ev = patchset_check.get("evidence") or {}
    services["patchset"] = {
        "status": patchset_check.get("status"),
        "file": patchset_ev.get("file"),
        "signature_verified": patchset_ev.get("signature_verified"),
        "age_hours": patchset_ev.get("age_hours"),
    }
    return services


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------
def _build_criteria(kpi_src: dict, drill_src: dict, invariant_src: dict,
                    gate: dict, evidence: dict) -> list[dict]:
    return [
        _kpi_criterion(kpi_src),
        _drill_criterion(drill_src),
        _invariant_criterion(invariant_src),
        _m4_criterion(kpi_src),
        _gate_criterion(gate),
        _evidence_criterion(evidence),
    ]


def _summary(criteria: list[dict]) -> dict:
    required = [c for c in criteria if c["required"]]
    counts = {
        "pass": sum(1 for c in criteria if c["status"] == "pass"),
        "fail": sum(1 for c in criteria if c["status"] == "fail"),
        "unknown": sum(1 for c in criteria if c["status"] == "unknown"),
        "total": len(criteria),
        "required_total": len(required),
        "required_pass": sum(1 for c in required if c["status"] == "pass"),
        "required_fail": sum(1 for c in required if c["status"] == "fail"),
        "required_unknown": sum(1 for c in required if c["status"] == "unknown"),
    }
    return counts


def build_pack(kpi=None, drill=None, invariant=None, gate_log=None, evidence_dir=None,
               repo=None, main_repo=None, now: dt.datetime | None = None,
               tool_timeout: float = TOOL_TIMEOUT) -> dict:
    """聚合生成 M1 evidence pack（只读；不写任何文件）。

    ``kpi``/``drill``/``invariant``：None=跑对应工具子进程；否则为已捕获 JSON 路径。
    ``gate_log``：None=自动发现最新 gate 日志；``evidence_dir``：None=默认生产 evidence 目录。
    任何输入失败/缺失 → 对应判据 unknown（附错误原文），不伪造 pass/fail。
    """
    started = time.perf_counter()
    now = now or dt.datetime.now().astimezone()
    repo = Path(repo or REPO)
    need_subprocess = any(ref is None for ref in (kpi, drill, invariant))
    main = Path(main_repo) if main_repo else (
        resolve_main_repo(repo) if need_subprocess else repo)
    kpi_src = _load_source("kpi", kpi, main_repo=main, timeout=tool_timeout,
                           evidence_dir=evidence_dir, gate_log=gate_log)
    drill_src = _load_source("drill", drill, main_repo=main, timeout=tool_timeout,
                             evidence_dir=evidence_dir, gate_log=gate_log)
    invariant_src = _load_source("invariant", invariant, main_repo=main, timeout=tool_timeout,
                                 evidence_dir=evidence_dir, gate_log=gate_log)
    gate = collect_gate(gate_log, now=now,
                        gate_dir=(Path(gate_log).parent if gate_log else None))
    evidence = collect_evidence(evidence_dir, now=now)
    services = _services_from_kpi(kpi_src)
    m4 = _m4_criterion(kpi_src)
    services["m4"] = {"status": m4["status"], "detail": m4["detail"]}
    criteria = _build_criteria(kpi_src, drill_src, invariant_src, gate, evidence)
    counts = _summary(criteria)
    required = [c for c in criteria if c["required"]]
    required_fails = [c["id"] for c in required if c["status"] == "fail"]
    required_unknowns = [c["id"] for c in required if c["status"] == "unknown"]
    verdict = "fail" if required_fails else ("unknown" if required_unknowns else "pass")
    gaps = [{"id": c["id"], "status": c["status"], "required": c["required"],
             "detail": c["detail"]} for c in criteria if c["status"] != "pass"]
    return {
        "schema": SCHEMA,
        "generated_at": now.isoformat(timespec="seconds"),
        "date": now.strftime("%Y%m%d"),
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "repo": str(repo),
        "main_repo": str(main),
        "commit": git_info(repo),
        "host": {"os": os.name, "python": sys.version.split()[0]},
        "verdict": verdict,
        "passed": verdict == "pass",
        "verdict_reasons": {
            "required_fail": required_fails,
            "required_unknown": required_unknowns,
            "advisory_not_pass": [c["id"] for c in criteria
                                  if not c["required"] and c["status"] != "pass"],
        },
        "counts": counts,
        "criteria": criteria,
        "gaps": gaps,
        "services": services,
        "sources": {
            "kpi": _source_meta(kpi_src),
            "drill": _source_meta(drill_src),
            "invariant": _source_meta(invariant_src),
            "gate": gate,
            "evidence": evidence,
        },
        "notes": list(NOTES),
    }


def _source_meta(src: dict) -> dict:
    """采集元信息（只留可复核字段；报告正文不重复塞进 sources）。"""
    report = src.get("report")
    schema = report.get("schema") if isinstance(report, dict) else None
    return {
        "name": src.get("name"),
        "mode": src.get("mode"),
        "path": src.get("path"),
        "ok": bool(src.get("ok")),
        "error": src.get("error") or "",
        "exit_code": src.get("exit_code"),
        "duration_ms": src.get("duration_ms"),
        "schema": schema,
    }


# ---------------------------------------------------------------------------
# 渲染 / 落盘
# ---------------------------------------------------------------------------
def _md_cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def _status_label(status) -> str:
    return STATUS_LABEL.get(str(status), "UNKNOWN")


def render_markdown(pack: dict) -> str:
    """Markdown 证据包（评审/日报可直接引用）。"""
    commit = pack.get("commit") or {}
    counts = pack.get("counts") or {}
    services = pack.get("services") or {}
    sources = pack.get("sources") or {}
    lines = [
        "# M1 信任链证据包（M1 evidence pack）",
        "",
        "- 生成时间：%s" % pack.get("generated_at"),
        "- 仓库：`%s` · 主仓 `%s`" % (pack.get("repo"), pack.get("main_repo")),
        "- commit：`%s`（%s）" % (commit.get("short") or "-", commit.get("branch") or "-"),
        "- 主机：os=%s · python=%s" % (
            (pack.get("host") or {}).get("os"), (pack.get("host") or {}).get("python")),
        "",
        "**判定：%s**（必需判据 通过 %s / 失败 %s / 未知 %s，共 %s 项）" % (
            str(pack.get("verdict") or "unknown").upper(),
            counts.get("required_pass"), counts.get("required_fail"),
            counts.get("required_unknown"), counts.get("required_total")),
        "",
        "## 判据表",
        "",
        "| # | 判据 | 必需 | 状态 | 来源 | 摘要 |",
        "|---|---|---|---|---|---|",
    ]
    for index, item in enumerate(pack.get("criteria") or [], 1):
        lines.append("| %d | %s | %s | %s | %s | %s |" % (
            index, _md_cell(item.get("title")), "是" if item.get("required") else "否",
            _status_label(item.get("status")),
            _md_cell(Path(str(item.get("source"))).name if item.get("source") else "-"),
            _md_cell(item.get("detail"))))
    lines += ["", "## 服务版本 / 健康（只读派生）", ""]
    if services.get("available"):
        lde = services.get("lde_8103") or {}
        hermes = services.get("hermes_8787") or {}
        release = services.get("release") or {}
        patchset = services.get("patchset") or {}
        rows = [
            ("8103 LDE 版本", lde.get("version")),
            ("8103 commit", lde.get("commit")),
            ("8103 version all_match", lde.get("all_match")),
            ("8103 health ok / rw_enabled", "%s / %s" % (lde.get("health_ok"), lde.get("rw_enabled"))),
            ("8787 Hermes 会话门", hermes.get("status")),
            ("发布指针 / junction 版本", "%s / %s" % (release.get("pointer_version"),
                                                    release.get("junction_version"))),
            ("发布 drift", release.get("drift")),
            ("最新 patchset evidence", patchset.get("file")),
            ("patchset signature_verified", patchset.get("signature_verified")),
            ("M4 四方签字（派生）", (services.get("m4") or {}).get("status")),
        ]
        lines += ["| 项 | 值 |", "|---|---|"]
        for label, value in rows:
            lines.append("| %s | %s |" % (_md_cell(label), _md_cell(value)))
    else:
        lines.append("> %s" % (services.get("detail") or "KPI 报告缺失，无法聚合服务版本"))
    lines += ["", "## 缺口清单（%d）" % len(pack.get("gaps") or []), ""]
    gaps = pack.get("gaps") or []
    if gaps:
        lines += ["| # | 判据 | 必需 | 状态 | 说明 |", "|---|---|---|---|---|"]
        for index, gap in enumerate(gaps, 1):
            lines.append("| %d | %s | %s | %s | %s |" % (
                index, _md_cell(gap.get("id")), "是" if gap.get("required") else "否",
                _status_label(gap.get("status")), _md_cell(gap.get("detail"))))
    else:
        lines.append("无缺口（全部判据 PASS）。")
    evidence = sources.get("evidence") or {}
    lines += ["", "## evidence 目录最新 artifact（只读清单）", ""]
    if evidence.get("exists"):
        lines.append("- 目录：`%s` · patchset=%s / run=%s" % (
            evidence.get("dir"), (evidence.get("counts") or {}).get("patchset"),
            (evidence.get("counts") or {}).get("run")))
        lines.append("")
        lines += ["| 文件 | 类型 | mtime | age(h) | 说明 |", "|---|---|---|---|---|"]
        for item in evidence.get("latest") or []:
            note = ""
            if item.get("kind") == "patchset":
                note = "task=%s ok=%s signature=%s" % (
                    item.get("task"), item.get("ok"), item.get("signature_present"))
            lines.append("| %s | %s | %s | %s | %s |" % (
                _md_cell(item.get("file")), _md_cell(item.get("kind")),
                _md_cell(item.get("mtime")), _md_cell(item.get("age_hours")),
                _md_cell(note)))
    else:
        lines.append("> %s" % (evidence.get("error") or "evidence 目录不存在"))
    lines += ["", "## 输入源采集", "",
              "| 源 | 模式 | 状态 | 耗时(ms) | 路径/说明 |", "|---|---|---|---|---|"]
    for key in ("kpi", "drill", "invariant"):
        meta = sources.get(key) or {}
        note = meta.get("error") or ""
        lines.append("| %s | %s | %s | %s | %s |" % (
            _md_cell(key), _md_cell(meta.get("mode")),
            "OK" if meta.get("ok") else "FAIL",
            _md_cell(meta.get("duration_ms")),
            _md_cell(meta.get("path") if not note else "%s（%s）" % (meta.get("path"), note))))
    gate = sources.get("gate") or {}
    lines.append("| fast_gate | file | %s | - | %s |" % (
        _status_label(gate.get("result")),
        _md_cell(gate.get("path") if gate.get("found") else gate.get("error"))))
    lines += ["", "## 说明", ""]
    for note in pack.get("notes") or []:
        lines.append("- %s" % note)
    lines.append("")
    return "\n".join(lines)


def output_paths(out_dir, date=None) -> tuple[Path, Path]:
    """``<out>/M1_evidence_pack_<date>.md/.json``（date 缺省用今天 YYYYMMDD）。"""
    stamp = str(date).strip() if date else dt.date.today().strftime("%Y%m%d")
    directory = Path(out_dir)
    return (directory / ("M1_evidence_pack_%s.md" % stamp),
            directory / ("M1_evidence_pack_%s.json" % stamp))


def write_pack(pack: dict, out_dir=None) -> dict:
    """把证据包写入 markdown + json（只写这两个文件，不写其它任何东西）。"""
    md_path, json_path = output_paths(out_dir or DEFAULT_OUT_DIR, pack.get("date"))
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(pack, ensure_ascii=False, indent=1) + "\n",
                         encoding="utf-8")
    md_path.write_text(render_markdown(pack) + "\n", encoding="utf-8")
    return {"markdown": str(md_path), "json": str(json_path)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="M1 信任链证据包聚合器（只读；生成 deliverables/M1_evidence_pack_<date>.md/.json）")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="stdout 输出 JSON（文件仍会生成）")
    output.add_argument("--markdown", action="store_true", help="stdout 输出 Markdown（默认）")
    parser.add_argument("--out", default="", help="输出目录（默认 <repo>/deliverables）")
    parser.add_argument("--no-write", action="store_true", help="只打印，不写 deliverables 文件")
    parser.add_argument("--kpi", default="", help="复用已捕获 KPI JSON（缺省跑 trust_chain_kpi.py）")
    parser.add_argument("--drill", default="", help="复用已捕获 drill JSON（缺省跑 release_rollback_drill.py）")
    parser.add_argument("--invariant", default="",
                        help="复用已捕获 invariant JSON（缺省跑 worktree_invariant_check.py）")
    parser.add_argument("--gate-log", default="", help="显式 fast gate 日志路径（缺省自动发现最新）")
    parser.add_argument("--evidence-dir", default="", help="legion_ide evidence 目录（只读清单）")
    parser.add_argument("--main-repo", default="",
                        help="主仓根（worktree 内运行时账本/配置以主仓为准；默认自动解析）")
    parser.add_argument("--timeout", type=float, default=TOOL_TIMEOUT,
                        help="单工具子进程超时秒（默认 %s）" % TOOL_TIMEOUT)
    args = parser.parse_args(argv)
    ensure_utf8_stdout()
    try:
        pack = build_pack(
            kpi=(args.kpi or None),
            drill=(args.drill or None),
            invariant=(args.invariant or None),
            gate_log=(args.gate_log or None),
            evidence_dir=(args.evidence_dir or None),
            main_repo=(args.main_repo or None),
            tool_timeout=float(args.timeout),
        )
    except Exception as exc:  # noqa: BLE001 - 参数/环境异常也要给出结构化报告
        pack = {
            "schema": SCHEMA,
            "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "date": dt.date.today().strftime("%Y%m%d"),
            "verdict": "unknown",
            "passed": False,
            "error": "%s: %s" % (type(exc).__name__, str(exc)[:300]),
            "criteria": [], "gaps": [], "counts": {},
        }
    written = {}
    if not args.no_write and pack.get("criteria"):
        try:
            written = write_pack(pack, args.out or DEFAULT_OUT_DIR)
        except OSError as exc:
            print("[warn] 证据包落盘失败（不影响判定）：%s" % exc, file=sys.stderr)
    if args.json:
        print(json.dumps(pack, ensure_ascii=False, indent=1))
    else:
        print(render_markdown(pack) if pack.get("criteria") else
              "M1 evidence pack 生成失败（unknown）：%s" % pack.get("error"))
    if written:
        print("[m1-evidence-pack] 已写入 %s / %s" % (
            written.get("markdown"), written.get("json")), file=sys.stderr)
    verdict = str(pack.get("verdict") or "unknown")
    return 0 if verdict == "pass" else (1 if verdict == "fail" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
