#!/usr/bin/env python3
"""gen_health_card.py — 治理健康卡（只读，零第三方依赖）。

聚合三类治理信号，输出一张机器可消费的 JSON（默认 stdout）与可选的自包含
HTML（--out DIR）。本脚本只读：不扫描计划任务、不触发备份、不改仓库。

数据来源（均以实读为准，读不到就报 available=false，绝不静默当 0）：
1. task_exit_audit 的账本/摘要（默认 <repo>/portal/data/task_exit_audit/，
   路径与字段直接取自 agent_platform/tools/task_exit_audit.py）：
   账本 task_exit_ledger.jsonl 每条含 at/name/state/last_result/
   exit_semantics/last_run/next_run；摘要 task_exit_summary.json 含
   generated_at/total_scanned/ok/failed/disabled/never_run/failures。
   本卡按账本落盘时间切分扫描会话、取最后一个会话自行计数，并用摘要做
   一致性交叉核对；
   EXIT_SEMANTICS 通过只读 import 复用（禁止复制一份口径）。
2. 备份仓 <vault-root>/<yyyy-MM-dd>/MANIFEST.txt 与日期目录 mtime；
   manifest 行格式：`<name>  <ts>  <bytes> bytes  sha256=<hex>`。
3. 仓库卫生：git status --porcelain 行数、git worktree list 数量、
   .worktrees/ 下的目录数（与 worktree list 交叉核对）。

用法：
    python -B tools/gen_health_card.py
    python -B tools/gen_health_card.py --vault-root C:/项目/_vault
    python -B tools/gen_health_card.py --out C:/项目/_hc_probe

退出码：0=绿/黄（可容忍），1=红（有硬问题），2=脚本自身错误（参数/写盘）。
阈值：备份 >36h 黄 / >72h 红；审计 >24h 黄 / >72h 红；dirty>0 黄；
工作树目录与 git worktree 不一致黄；任一块不可用红。
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SCHEMA = "governance_health_card/v1"

# 与 task_exit_audit.py 的输出路径保持同源（该文件 DATA_DIR 的相对位置）。
AUDIT_MODULE_REL = Path("agent_platform") / "tools" / "task_exit_audit.py"
AUDIT_DATA_REL = Path("portal") / "data" / "task_exit_audit"
LEDGER_NAME = "task_exit_ledger.jsonl"
SUMMARY_NAME = "task_exit_summary.json"

BACKUP_YELLOW_HOURS = 36.0
BACKUP_RED_HOURS = 72.0
AUDIT_YELLOW_HOURS = 24.0
AUDIT_RED_HOURS = 72.0

UNKNOWN_SEMANTICS = "未知退出码"
NEVER_RUN_CODE = 267011
RUNNING_CODE = 267009
# 一次扫描写 42 条账本会跨秒（at 是每条记录落盘时刻）；以 120s 间隔切分扫描批次。
SCAN_SESSION_GAP_SECONDS = 120.0
KNOWN_TASK_STATES = {"ready", "running", "disabled", "queued"}
CONFLICT_CODES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}

DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MANIFEST_RE = re.compile(
    r"^(?P<name>.+?)\s{2,}(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
    r"\s+(?P<size>\d+)\s+bytes\s+sha256=(?P<sha>[0-9A-Fa-f]{64})\s*$"
)


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------
def _iso(value: dt.datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse_iso(value: Any) -> dt.datetime | None:
    """解析 ISO 8601；无时区按本机时区处理。解析失败返回 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
    return parsed.astimezone()


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value.strip())
    return None


def _age_hours(timestamp: dt.datetime | None, now: dt.datetime) -> float | None:
    if timestamp is None:
        return None
    return round((now - timestamp).total_seconds() / 3600.0, 2)


def _short_exc(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text[:500]


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None, f"文件不存在: {path}"
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, _short_exc(exc)
    if not isinstance(payload, dict):
        return None, f"顶层不是 JSON 对象: {path}"
    return payload, None


def _load_audit_semantics(repo_root: Path) -> tuple[dict[int, str] | None, str | None]:
    """只读 import task_exit_audit 的 EXIT_SEMANTICS；禁止落 .pyc。"""
    module_path = repo_root / AUDIT_MODULE_REL
    if not module_path.is_file():
        return None, f"未找到审计模块: {module_path}"
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(
            "_health_card_task_exit_audit", str(module_path)
        )
        if spec is None or spec.loader is None:
            return None, "无法构造 import spec"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        raw = getattr(module, "EXIT_SEMANTICS", None)
        if not isinstance(raw, dict):
            return None, "审计模块缺少 EXIT_SEMANTICS"
        return {int(key): str(value) for key, value in raw.items()}, None
    except Exception as exc:  # import 失败不致命，退化判定并标黄
        return None, _short_exc(exc)
    finally:
        sys.dont_write_bytecode = previous


# ---------------------------------------------------------------------------
# 1) 计划任务审计
# ---------------------------------------------------------------------------
def _empty_task_audit(audit_root: Path) -> dict[str, Any]:
    return {
        "available": False,
        "reason": None,
        "root": str(audit_root),
        "ledger_path": str(audit_root / LEDGER_NAME),
        "summary_path": str(audit_root / SUMMARY_NAME),
        "ledger_lines": None,
        "ledger_records": None,
        "ledger_parse_errors": None,
        "ledger_parse_error_samples": [],
        "undated_records": None,
        "ledger_sessions": None,
        "last_scan_at": None,
        "age_hours": None,
        "clock_skew": None,
        "total_scanned": None,
        "ok": None,
        "failed": None,
        "disabled": None,
        "never_run": None,
        "running": None,
        "unknown_exit_codes": None,
        "unknown_states": None,
        "failures": None,
        "unknown_exit_names": [],
        "unknown_state_names": [],
        "counts_source": None,
        "summary_available": False,
        "summary_error": None,
        "summary_generated_at": None,
        "summary_time_delta_seconds": None,
        "summary_matches_ledger": None,
        "summary_count_drift": None,
        "semantics_source": None,
        "semantics_error": None,
        "thresholds_hours": {
            "yellow": AUDIT_YELLOW_HOURS,
            "red": AUDIT_RED_HOURS,
        },
    }


def collect_task_audit(
    audit_root: Path,
    now: dt.datetime,
    semantics: dict[int, str] | None,
    semantics_error: str | None,
) -> dict[str, Any]:
    result = _empty_task_audit(audit_root)
    result["semantics_source"] = (
        "import:" + str(AUDIT_MODULE_REL).replace("\\", "/")
        if semantics is not None
        else "ledger_text_fallback"
    )
    result["semantics_error"] = semantics_error

    summary, summary_error = _read_json(audit_root / SUMMARY_NAME)
    result["summary_available"] = summary is not None
    result["summary_error"] = summary_error
    if summary is not None:
        result["summary_generated_at"] = summary.get("generated_at")

    ledger_path = audit_root / LEDGER_NAME
    if not ledger_path.is_file():
        result["reason"] = f"账本不存在: {ledger_path}"
        return result

    records: list[tuple[dt.datetime, dict[str, Any]]] = []
    parse_errors: list[dict[str, Any]] = []
    undated = 0
    non_empty = 0
    try:
        with ledger_path.open("rb") as handle:
            for lineno, raw in enumerate(handle, 1):
                line = raw.strip()
                if not line:
                    continue
                non_empty += 1
                try:
                    text = line.decode("utf-8")
                except UnicodeDecodeError as exc:
                    parse_errors.append({"line": lineno, "error": _short_exc(exc)})
                    continue
                try:
                    record = json.loads(text)
                except json.JSONDecodeError as exc:
                    parse_errors.append({"line": lineno, "error": _short_exc(exc)})
                    continue
                if not isinstance(record, dict):
                    parse_errors.append({"line": lineno, "error": "记录不是 JSON 对象"})
                    continue
                at = _parse_iso(record.get("at"))
                if at is None:
                    undated += 1
                    continue
                records.append((at, record))
    except OSError as exc:
        result["reason"] = f"读取账本失败: {_short_exc(exc)}"
        return result

    result["ledger_lines"] = non_empty
    result["ledger_records"] = len(records)
    result["ledger_parse_errors"] = len(parse_errors)
    result["ledger_parse_error_samples"] = parse_errors[:10]
    result["undated_records"] = undated

    if not records:
        reason = "账本中没有带合法 at 的记录"
        if parse_errors:
            reason += f"（{len(parse_errors)} 行解析失败）"
        if undated:
            reason += f"（{undated} 条缺 at）"
        result["reason"] = reason
        return result

    # 一次扫描的 42 条记录可能跨秒落盘；按时间排序后以间隔切分扫描会话，
    # 取最后一个会话作为「最近一次扫描」，而不是按 at 精确相等分组。
    ordered = sorted(records, key=lambda item: item[0])
    sessions: list[list[tuple[dt.datetime, dict[str, Any]]]] = []
    for item in ordered:
        if (
            sessions
            and (item[0] - sessions[-1][-1][0]).total_seconds()
            <= SCAN_SESSION_GAP_SECONDS
        ):
            sessions[-1].append(item)
        else:
            sessions.append([item])
    batch = [record for _, record in sessions[-1]]
    latest_at = sessions[-1][-1][0]
    result["ledger_sessions"] = len(sessions)

    ok = failed = disabled = never_run = running = 0
    unknown_exit = 0
    unknown_states = 0
    failures: list[dict[str, Any]] = []
    unknown_exit_names: list[str] = []
    unknown_state_names: list[str] = []

    for record in batch:
        name = str(record.get("name") or "?")
        state = str(record.get("state") or "")
        state_key = state.lower()
        code = _as_int(record.get("last_result"))
        semantics_text = str(record.get("exit_semantics") or "")

        if state_key not in KNOWN_TASK_STATES:
            unknown_states += 1
            if len(unknown_state_names) < 20:
                unknown_state_names.append(f"{name}({state or '空'})")
        if code == RUNNING_CODE:
            running += 1

        if state_key == "disabled":
            disabled += 1
            continue
        if code == 0:
            ok += 1
            continue
        if code == NEVER_RUN_CODE:
            never_run += 1
            continue

        # 与 task_exit_audit 同口径：非 0、非 267011、非 Disabled 即失败
        failed += 1
        if len(failures) < 30:
            failures.append(
                {
                    "name": name,
                    "last_result": code if code is not None else record.get("last_result"),
                    "exit_semantics": semantics_text,
                    "last_run": record.get("last_run") or "",
                }
            )
        if semantics is not None:
            is_unknown = code not in semantics
        else:
            is_unknown = code is None or semantics_text in ("", UNKNOWN_SEMANTICS)
        if is_unknown:
            unknown_exit += 1
            if len(unknown_exit_names) < 20:
                unknown_exit_names.append(f"{name}({code})")

    result["available"] = True
    result["last_scan_at"] = _iso(latest_at)
    result["age_hours"] = _age_hours(latest_at, now)
    result["clock_skew"] = bool(
        result["age_hours"] is not None and result["age_hours"] < -0.1
    )
    result["total_scanned"] = len(batch)
    result["ok"] = ok
    result["failed"] = failed
    result["disabled"] = disabled
    result["never_run"] = never_run
    result["running"] = running
    result["unknown_exit_codes"] = unknown_exit
    result["unknown_states"] = unknown_states
    result["failures"] = failures
    result["unknown_exit_names"] = unknown_exit_names
    result["unknown_state_names"] = unknown_state_names
    result["counts_source"] = "ledger_latest_batch"

    if summary is not None:
        summary_at = _parse_iso(summary.get("generated_at"))
        delta = (
            abs((summary_at - latest_at).total_seconds())
            if summary_at is not None
            else None
        )
        result["summary_time_delta_seconds"] = round(delta, 1) if delta is not None else None
        drift: dict[str, dict[str, Any]] = {}
        for key, ledger_value in (
            ("total_scanned", len(batch)),
            ("ok", ok),
            ("failed", failed),
            ("disabled", disabled),
            ("never_run", never_run),
        ):
            if summary.get(key) != ledger_value:
                drift[key] = {"summary": summary.get(key), "ledger": ledger_value}
        result["summary_count_drift"] = drift
        result["summary_matches_ledger"] = bool(
            not drift and delta is not None and delta <= 120
        )
    return result


# ---------------------------------------------------------------------------
# 2) 备份
# ---------------------------------------------------------------------------
def _valid_date_dir(name: str) -> bool:
    if not DATE_DIR_RE.fullmatch(name):
        return False
    try:
        dt.date.fromisoformat(name)
    except ValueError:
        return False
    return True


def _empty_backup(vault_root: Path) -> dict[str, Any]:
    return {
        "available": False,
        "reason": None,
        "vault_root": str(vault_root),
        "latest_date": None,
        "dir": None,
        "dir_mtime": None,
        "manifest_path": None,
        "manifest_mtime": None,
        "age_hours": None,
        "clock_skew": None,
        "manifest_entries": None,
        "manifest_malformed": None,
        "manifest_malformed_samples": [],
        "manifest_missing": None,
        "size_mismatch": None,
        "missing_files": [],
        "size_mismatch_files": [],
        "manifest_bytes": None,
        "total_bytes": None,
        "total_human": None,
        "files_count": None,
        "sha256_verified": False,
        "thresholds_hours": {
            "yellow": BACKUP_YELLOW_HOURS,
            "red": BACKUP_RED_HOURS,
        },
    }


def collect_backup(vault_root: Path, now: dt.datetime) -> dict[str, Any]:
    result = _empty_backup(vault_root)
    try:
        if not vault_root.is_dir():
            result["reason"] = f"备份根不存在或不是目录: {vault_root}"
            return result
        candidates = [
            entry
            for entry in vault_root.iterdir()
            if entry.is_dir() and _valid_date_dir(entry.name)
        ]
    except OSError as exc:
        result["reason"] = f"读取备份根失败: {_short_exc(exc)}"
        return result

    if not candidates:
        result["reason"] = f"未找到 yyyy-MM-dd 备份目录: {vault_root}"
        return result

    latest = max(candidates, key=lambda entry: entry.name)
    result["latest_date"] = latest.name
    result["dir"] = str(latest)
    try:
        stat = latest.stat()
    except OSError as exc:
        result["reason"] = f"读取备份目录属性失败: {_short_exc(exc)}"
        return result
    mtime = dt.datetime.fromtimestamp(stat.st_mtime).astimezone()
    result["dir_mtime"] = _iso(mtime)
    result["age_hours"] = _age_hours(mtime, now)
    result["clock_skew"] = bool(
        result["age_hours"] is not None and result["age_hours"] < -0.1
    )

    manifest = latest / "MANIFEST.txt"
    result["manifest_path"] = str(manifest)
    if not manifest.is_file():
        result["reason"] = f"MANIFEST.txt 不存在: {manifest}"
        return result
    try:
        manifest_stat = manifest.stat()
        result["manifest_mtime"] = _iso(
            dt.datetime.fromtimestamp(manifest_stat.st_mtime).astimezone()
        )
        raw = manifest.read_bytes()
    except OSError as exc:
        result["reason"] = f"读取 MANIFEST.txt 失败: {_short_exc(exc)}"
        return result

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")

    entries: dict[str, int] = {}
    malformed: list[dict[str, Any]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        match = MANIFEST_RE.match(stripped)
        if match is None:
            malformed.append({"line": lineno, "text": stripped[:120]})
            continue
        name = match.group("name")
        if name in (".", "..") or "/" in name or "\\" in name:
            malformed.append({"line": lineno, "text": f"非法文件名 {name!r}"})
            continue
        if name in entries:
            malformed.append({"line": lineno, "text": f"重复条目 {name!r}"})
            continue
        entries[name] = int(match.group("size"))

    result["manifest_entries"] = len(entries)
    result["manifest_malformed"] = len(malformed)
    result["manifest_malformed_samples"] = malformed[:10]

    if not entries:
        result["reason"] = f"MANIFEST 无可解析条目: {manifest}"
        return result

    total_bytes = 0
    files_count = 0
    try:
        for path in latest.rglob("*"):
            if path.is_file():
                total_bytes += path.stat().st_size
                files_count += 1
    except OSError as exc:
        result["reason"] = f"统计备份体积失败: {_short_exc(exc)}"
        return result

    missing: list[str] = []
    mismatches: list[dict[str, Any]] = []
    for name, declared in entries.items():
        path = latest / name
        if not path.is_file():
            missing.append(name)
            continue
        try:
            actual = path.stat().st_size
        except OSError:
            missing.append(name)
            continue
        if actual != declared:
            mismatches.append({"name": name, "manifest": declared, "actual": actual})

    result["available"] = True
    result["manifest_bytes"] = sum(entries.values())
    result["manifest_missing"] = len(missing)
    result["size_mismatch"] = len(mismatches)
    result["missing_files"] = missing[:20]
    result["size_mismatch_files"] = mismatches[:20]
    result["total_bytes"] = total_bytes
    result["total_human"] = _human_bytes(total_bytes)
    result["files_count"] = files_count
    return result


# ---------------------------------------------------------------------------
# 3) 仓库卫生
# ---------------------------------------------------------------------------
def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _git_run(
    repo: Path, args: list[str], timeout: float = 60.0
) -> subprocess.CompletedProcess[bytes]:
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        [
            "git",
            "--no-optional-locks",
            "-c",
            "core.quotepath=false",
            "-C",
            str(repo),
            *args,
        ],
        capture_output=True,
        timeout=timeout,
        check=False,
        env=env,
    )


def _empty_repo(repo_root: Path) -> dict[str, Any]:
    return {
        "available": False,
        "reason": None,
        "repo_root": str(repo_root),
        "branch": None,
        "dirty_files": None,
        "staged_files": None,
        "untracked_files": None,
        "conflicted_files": None,
        "worktrees": None,
        "linked_worktrees": None,
        "worktrees_source": "git worktree list",
        "worktree_dirs": None,
        "worktree_dirs_consistent": None,
        "worktree_dir_exists": None,
        "worktrees_root": str(repo_root / ".worktrees"),
    }


def collect_repo(repo_root: Path, now: dt.datetime) -> dict[str, Any]:
    del now  # 保留统一签名；仓库块不依赖当前时间
    result = _empty_repo(repo_root)
    try:
        if not repo_root.is_dir():
            result["reason"] = f"仓库根不存在或不是目录: {repo_root}"
            return result
    except OSError as exc:
        result["reason"] = f"读取仓库根失败: {_short_exc(exc)}"
        return result

    try:
        probe = _git_run(repo_root, ["rev-parse", "--is-inside-work-tree"])
    except FileNotFoundError:
        result["reason"] = "找不到 git 可执行文件"
        return result
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["reason"] = f"git 探测失败: {_short_exc(exc)}"
        return result
    if probe.returncode != 0:
        result["reason"] = (
            "不是 git 工作树: " + (_decode(probe.stderr).strip()[:300] or "git 返回非零")
        )
        return result

    try:
        status = _git_run(repo_root, ["status", "--porcelain=v1"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["reason"] = f"git status 失败: {_short_exc(exc)}"
        return result
    if status.returncode != 0:
        result["reason"] = "git status 失败: " + (
            _decode(status.stderr).strip()[:300] or "git 返回非零"
        )
        return result

    lines = [line for line in _decode(status.stdout).splitlines() if line.strip()]
    result["dirty_files"] = len(lines)
    result["untracked_files"] = sum(1 for line in lines if line.startswith("??"))
    result["staged_files"] = sum(
        1 for line in lines if not line.startswith("??") and line[0] not in (" ", "?")
    )
    result["conflicted_files"] = sum(1 for line in lines if line[:2] in CONFLICT_CODES)

    try:
        worktree_output = _git_run(repo_root, ["worktree", "list", "--porcelain"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["reason"] = f"git worktree list 失败: {_short_exc(exc)}"
        return result
    if worktree_output.returncode != 0:
        result["reason"] = "git worktree list 失败: " + (
            _decode(worktree_output.stderr).strip()[:300] or "git 返回非零"
        )
        return result
    worktree_paths = [
        line[len("worktree ") :]
        for line in _decode(worktree_output.stdout).splitlines()
        if line.startswith("worktree ")
    ]
    result["worktrees"] = len(worktree_paths)
    result["linked_worktrees"] = max(0, len(worktree_paths) - 1)

    worktrees_root = repo_root / ".worktrees"
    result["worktree_dir_exists"] = worktrees_root.is_dir()
    if worktrees_root.is_dir():
        try:
            result["worktree_dirs"] = sum(
                1 for child in worktrees_root.iterdir() if child.is_dir()
            )
        except OSError as exc:
            result["reason"] = f"读取 .worktrees 失败: {_short_exc(exc)}"
            return result
    else:
        result["worktree_dirs"] = 0
    result["worktree_dirs_consistent"] = (
        result["worktree_dirs"] == result["linked_worktrees"]
    )

    try:
        branch = _git_run(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])
        if branch.returncode == 0:
            result["branch"] = _decode(branch.stdout).strip() or None
    except (OSError, subprocess.TimeoutExpired):
        result["branch"] = None

    result["available"] = True
    return result


# ---------------------------------------------------------------------------
# 状态判定
# ---------------------------------------------------------------------------
def _evaluate(card: dict[str, Any]) -> tuple[list[dict[str, str]], str, list[str]]:
    checks: list[dict[str, str]] = []

    def add(check_id: str, level: str, detail: str) -> None:
        checks.append({"id": check_id, "level": level, "detail": detail})

    audit = card["task_audit"]
    if not audit["available"]:
        add("task_audit.readable", "red", "计划任务审计不可用：" + (audit["reason"] or "未知原因"))
    else:
        add(
            "task_audit.readable",
            "green",
            f"账本可读，最后一个扫描会话 {audit['total_scanned']} 个任务 @ {audit['last_scan_at']}",
        )
        if audit["failed"]:
            add(
                "task_audit.failures",
                "red",
                f"{audit['failed']} 个任务非零退出（口径同 task_exit_audit，含运行中/强杀）",
            )
        else:
            add("task_audit.failures", "green", "无失败任务")

        unknown_total = (audit["unknown_exit_codes"] or 0) + (audit["unknown_states"] or 0)
        if unknown_total:
            add(
                "task_audit.unknown",
                "yellow",
                f"未知退出码 {audit['unknown_exit_codes']} 个 / 未知状态 {audit['unknown_states']} 个",
            )
        else:
            add("task_audit.unknown", "green", "未知退出码 0 / 未知状态 0")

        age = audit["age_hours"]
        if age is None:
            add("task_audit.freshness", "yellow", "无法计算最近扫描距今时长")
        elif age > AUDIT_RED_HOURS:
            add("task_audit.freshness", "red", f"审计已 {age:.1f}h 未更新（>{AUDIT_RED_HOURS:g}h）")
        elif age > AUDIT_YELLOW_HOURS:
            add(
                "task_audit.freshness",
                "yellow",
                f"审计已 {age:.1f}h 未更新（>{AUDIT_YELLOW_HOURS:g}h）",
            )
        else:
            add("task_audit.freshness", "green", f"审计距今 {age:.1f}h")

        if audit["clock_skew"]:
            add("task_audit.clock_skew", "yellow", "账本 at 在未来，可能存在时钟偏差")

        integrity_bits = []
        if audit["ledger_parse_errors"]:
            integrity_bits.append(f"{audit['ledger_parse_errors']} 行无法解析")
        if audit["undated_records"]:
            integrity_bits.append(f"{audit['undated_records']} 条缺 at")
        if integrity_bits:
            add("task_audit.ledger_integrity", "yellow", "账本不完整：" + "、".join(integrity_bits))
        else:
            add("task_audit.ledger_integrity", "green", "账本全部可解析")

        if not audit["summary_available"]:
            add(
                "task_audit.summary_sync",
                "yellow",
                "摘要不可读（仅账本）: " + (audit["summary_error"] or "未知原因"),
            )
        elif audit["summary_matches_ledger"]:
            add("task_audit.summary_sync", "green", "摘要与账本同步")
        else:
            bits = []
            if audit["summary_time_delta_seconds"] is not None:
                bits.append(f"时间差 {audit['summary_time_delta_seconds']:.0f}s")
            drift = audit["summary_count_drift"] or {}
            if drift:
                bits.append(
                    "计数漂移 "
                    + ", ".join(
                        f"{key}:摘要={value['summary']}/账本={value['ledger']}"
                        for key, value in list(drift.items())[:3]
                    )
                )
            add(
                "task_audit.summary_sync",
                "yellow",
                "账本与摘要不一致：" + ("；".join(bits) or "字段缺失"),
            )

        if audit["semantics_error"]:
            add(
                "task_audit.semantics",
                "yellow",
                "无法复用 task_exit_audit.EXIT_SEMANTICS，未知码退化判定: "
                + audit["semantics_error"],
            )
        else:
            add("task_audit.semantics", "green", "未知码判定复用 EXIT_SEMANTICS")

    backup = card["backup"]
    if not backup["available"]:
        add("backup.readable", "red", "备份不可用：" + (backup["reason"] or "未知原因"))
    else:
        add(
            "backup.readable",
            "green",
            f"最近备份 {backup['latest_date']}，清单 {backup['manifest_entries']} 条，{backup['total_human']}",
        )
        age = backup["age_hours"]
        if age is None:
            add("backup.freshness", "red", "无法计算备份距今时长")
        elif age > BACKUP_RED_HOURS:
            add("backup.freshness", "red", f"备份已 {age:.1f}h 未更新（>{BACKUP_RED_HOURS:g}h）")
        elif age > BACKUP_YELLOW_HOURS:
            add(
                "backup.freshness",
                "yellow",
                f"备份已 {age:.1f}h 未更新（>{BACKUP_YELLOW_HOURS:g}h）",
            )
        else:
            add("backup.freshness", "green", f"备份距今 {age:.1f}h")

        if backup["clock_skew"]:
            add("backup.clock_skew", "yellow", "备份目录 mtime 在未来，可能存在时钟偏差")
        if backup["manifest_missing"]:
            add(
                "backup.integrity",
                "red",
                f"{backup['manifest_missing']} 个清单文件不在目录中: "
                + "、".join(backup["missing_files"][:5]),
            )
        elif backup["manifest_malformed"] or backup["size_mismatch"]:
            bits = []
            if backup["manifest_malformed"]:
                bits.append(f"清单 {backup['manifest_malformed']} 行格式异常")
            if backup["size_mismatch"]:
                bits.append(f"{backup['size_mismatch']} 个文件大小与清单不符")
            add("backup.integrity", "yellow", "；".join(bits))
        else:
            add("backup.integrity", "green", "清单条目齐全、大小一致（未重算 sha256）")

    repo = card["repo"]
    if not repo["available"]:
        add("repo.readable", "red", "仓库卫生不可用：" + (repo["reason"] or "未知原因"))
    else:
        add(
            "repo.readable",
            "green",
            f"仓库可读 @ {repo['branch'] or '(detached)'}",
        )
        if repo["conflicted_files"]:
            add("repo.conflicts", "red", f"{repo['conflicted_files']} 个冲突文件")
        if repo["dirty_files"]:
            add(
                "repo.dirty",
                "yellow",
                f"{repo['dirty_files']} 个未提交变更（未跟踪 {repo['untracked_files']}）",
            )
        else:
            add("repo.dirty", "green", "工作区干净")
        if repo["worktree_dirs_consistent"]:
            add(
                "repo.worktrees",
                "green",
                f".worktrees 目录 {repo['worktree_dirs']} 与 git worktree 关联数 {repo['linked_worktrees']} 一致",
            )
        else:
            add(
                "repo.worktrees",
                "yellow",
                f".worktrees 目录 {repo['worktree_dirs']} 与 git worktree 关联数 {repo['linked_worktrees']} 不一致",
            )

    rank = {"green": 0, "yellow": 1, "red": 2}
    status = max((check["level"] for check in checks), key=lambda level: rank[level])
    reasons = [
        f"[{check['level']}] {check['detail']}"
        for check in checks
        if check["level"] != "green"
    ]
    return checks, status, reasons


def build_card(
    repo_root: Path,
    vault_root: Path,
    audit_root: Path,
    now: dt.datetime,
) -> dict[str, Any]:
    semantics, semantics_error = _load_audit_semantics(repo_root)
    card: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": _iso(now),
        "status": "green",
        "exit_code": 0,
        "reasons": [],
        "checks": [],
        "task_audit": collect_task_audit(audit_root, now, semantics, semantics_error),
        "backup": collect_backup(vault_root, now),
        "repo": collect_repo(repo_root, now),
        "notes": [
            "本脚本只读：不扫描/启停计划任务、不触发备份、不改仓库。",
            "dirty_files 含未跟踪文件；本脚本自身在提交前也会被计入。",
            "备份未重算 sha256（体积可达数百 MiB）；只核对清单条目、文件存在性与大小。",
            "task_audit 失败口径沿用其账本：Disabled 不计、267011 记未运行、其余非零（含 267009/267014）记失败。",
            "未知退出码同时计入 failed（沿用审计口径），此处单独列示以便区分「已知失败」与「未知退出码」。",
            f"阈值：备份 >{BACKUP_YELLOW_HOURS:g}h 黄 / >{BACKUP_RED_HOURS:g}h 红；"
            f"审计 >{AUDIT_YELLOW_HOURS:g}h 黄 / >{AUDIT_RED_HOURS:g}h 红；"
            "dirty>0 黄；工作树不一致黄；任一块不可用红。",
            "账本按落盘时间切分扫描会话，取最后一个会话；120s 内的连续记录视为同一次扫描。",
        ],
    }
    checks, status, reasons = _evaluate(card)
    card["checks"] = checks
    card["status"] = status
    card["reasons"] = reasons
    card["exit_code"] = 1 if status == "red" else 0
    return card


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def _human_bytes(count: int | None) -> str:
    if count is None:
        return "—"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(count)
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    if index == 0:
        return f"{int(value)} B"
    return f"{value:.1f} {units[index]}"


def _e(value: Any) -> str:
    if value is None:
        return "—"
    return html.escape(str(value), quote=True)


def _fmt_num(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _fmt_age(hours: float | None) -> str:
    if hours is None:
        return "—"
    if hours < 0:
        return f"未来 {-hours:.1f} 小时（时钟偏差?）"
    if hours < 1:
        return f"{hours * 60:.0f} 分钟前（{hours:.2f}h）"
    return f"{hours:.1f} 小时前"


def _kv(label: str, value: Any) -> str:
    return f'<div class="kv"><span>{_e(label)}</span><b>{_e(value)}</b></div>'


_CSS = """
:root{--bg:#f4f5f7;--card:#fff;--ink:#1c1f23;--muted:#66707a;--line:#e3e6ea;
--green:#1a7f37;--green-bg:#e8f5ec;--yellow:#8a5a00;--yellow-bg:#fff4d6;
--red:#b42318;--red-bg:#fde8e6}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 "Segoe UI","Microsoft YaHei",system-ui,sans-serif}
main{max-width:1100px;margin:32px auto;padding:0 20px 40px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;
padding:28px;box-shadow:0 1px 3px rgba(16,24,40,.06)}
.eyebrow{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
h1{font-size:26px;margin:8px 0 6px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.chip{font-size:13px;font-weight:700;padding:5px 12px;border-radius:999px;letter-spacing:.03em}
.chip.green{color:var(--green);background:var(--green-bg)}
.chip.yellow{color:var(--yellow);background:var(--yellow-bg)}
.chip.red{color:var(--red);background:var(--red-bg)}
.meta{color:var(--muted);font-size:13px;margin:0;word-break:break-all}
.reasons{margin:18px 0 0;padding:14px 16px;border:1px solid var(--line);border-radius:10px;background:#fafbfc}
.reasons h2{font-size:13px;margin:0 0 8px;color:var(--muted)}
.reasons ul{margin:0;padding-left:20px}
.reasons li{font-size:13px;margin:4px 0;word-break:break-word}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px;margin-top:24px}
.block{border:1px solid var(--line);border-radius:12px;padding:18px}
.block.full{grid-column:1/-1}
.block h2{font-size:16px;margin:0 0 4px}
.sub{color:var(--muted);font-size:12px;margin-bottom:10px;word-break:break-all}
.kv{display:flex;justify-content:space-between;gap:16px;padding:6px 0;
border-bottom:1px dashed var(--line);font-size:14px}
.kv:last-child{border-bottom:0}
.kv span{color:var(--muted);flex:none}
.kv b{font-variant-numeric:tabular-nums;text-align:right;word-break:break-word}
.unavailable{margin:10px 0;padding:12px;border-radius:8px;background:var(--red-bg);
color:var(--red);font-size:13px;word-break:break-word}
.note{margin:12px 0 0;padding:10px 12px;border-radius:8px;background:#fafbfc;
font-size:12px;color:var(--muted);word-break:break-word}
.checks{list-style:none;margin:10px 0 0;padding:0}
.checks li{display:flex;gap:10px;padding:8px 0;border-bottom:1px solid var(--line);font-size:13px}
.checks li:last-child{border-bottom:0}
.dot{width:9px;height:9px;border-radius:50%;margin-top:6px;flex:none}
.dot.green{background:var(--green)}.dot.yellow{background:#b7791f}.dot.red{background:var(--red)}
.checks code{color:var(--muted);font-size:12px;margin-right:6px}
details{margin-top:24px}
summary{cursor:pointer;color:var(--muted);font-size:13px}
pre{max-height:420px;overflow:auto;background:#0f1720;color:#d7e2ee;padding:16px;
border-radius:10px;font-size:12px;line-height:1.5}
footer{margin-top:22px;color:var(--muted);font-size:12px}
@media (max-width:600px){main{margin:16px auto}.card{padding:18px}h1{font-size:21px}}
"""


def _render_html(card: dict[str, Any]) -> str:
    status = card["status"]
    labels = {"green": "GREEN · 绿", "yellow": "YELLOW · 黄", "red": "RED · 红"}
    parts: list[str] = [
        "<!doctype html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="color-scheme" content="light">',
        f"<title>治理健康卡 · {_e(labels[status])} · {_e(card['generated_at'])}</title>",
        f"<style>{_CSS}</style>",
        "</head>",
        "<body>",
        "<main>",
        '<article class="card">',
        "<header>",
        '<div class="eyebrow">零界 · 治理健康卡</div>',
        f'<h1>总体状态 <span class="chip {status}">{_e(labels[status])}</span></h1>',
        f'<p class="meta">生成于 {_e(card["generated_at"])} · 退出码 {card["exit_code"]}'
        f' · 仓库 {_e(card["repo"]["repo_root"])}</p>',
        "</header>",
    ]

    reasons = card.get("reasons") or []
    if reasons:
        parts.append('<div class="reasons"><h2>为何不是绿</h2><ul>')
        parts.extend(f"<li>{_e(reason)}</li>" for reason in reasons)
        parts.append("</ul></div>")

    parts.append('<div class="grid">')

    audit = card["task_audit"]
    parts.append('<section class="block">')
    parts.append("<h2>计划任务审计</h2>")
    parts.append(f'<div class="sub">{_e(audit["ledger_path"])}</div>')
    if audit["available"]:
        parts.extend(
            [
                _kv("最近扫描", audit["last_scan_at"]),
                _kv("距今", _fmt_age(audit["age_hours"])),
                _kv("失败任务", _fmt_num(audit["failed"])),
                _kv("未知退出码", _fmt_num(audit["unknown_exit_codes"])),
                _kv("未知状态", _fmt_num(audit["unknown_states"])),
                _kv(
                    "运行中 / 未运行 / 禁用",
                    f"{_fmt_num(audit['running'])} / {_fmt_num(audit['never_run'])} / {_fmt_num(audit['disabled'])}",
                ),
                _kv("扫描总数", _fmt_num(audit["total_scanned"])),
            ]
        )
        if audit["failures"]:
            names = "、".join(str(item["name"]) for item in audit["failures"])
            parts.append(f'<p class="note">失败：{_e(names)}</p>')
    else:
        parts.append(f'<p class="unavailable">不可用：{_e(audit["reason"])}</p>')
        if audit["summary_available"]:
            parts.append('<p class="note">摘要可读，但账本不可用；本卡不据此判定健康。</p>')
    parts.append("</section>")

    backup = card["backup"]
    parts.append('<section class="block">')
    parts.append("<h2>备份</h2>")
    parts.append(f'<div class="sub">{_e(backup["manifest_path"] or backup["vault_root"])}</div>')
    if backup["available"]:
        integrity = (
            f"缺 {backup['manifest_missing']} / 格式异常 {backup['manifest_malformed']}"
            f" / 大小不符 {backup['size_mismatch']}"
        )
        parts.extend(
            [
                _kv("最近备份", backup["latest_date"]),
                _kv("距今", _fmt_age(backup["age_hours"])),
                _kv("MANIFEST 条目", _fmt_num(backup["manifest_entries"])),
                _kv("目录总体积（含清单）", backup["total_human"]),
                _kv("完整性", integrity),
                _kv("目录 mtime", backup["dir_mtime"]),
            ]
        )
        parts.append('<p class="note">未重算 sha256；只核对条目、存在性与大小。</p>')
    else:
        parts.append(f'<p class="unavailable">不可用：{_e(backup["reason"])}</p>')
    parts.append("</section>")

    repo = card["repo"]
    parts.append('<section class="block">')
    parts.append("<h2>仓库卫生</h2>")
    parts.append(f'<div class="sub">{_e(repo["repo_root"])}</div>')
    if repo["available"]:
        parts.extend(
            [
                _kv("dirty 文件", _fmt_num(repo["dirty_files"])),
                _kv("未跟踪", _fmt_num(repo["untracked_files"])),
                _kv("git worktree 数（含主树）", _fmt_num(repo["worktrees"])),
                _kv(".worktrees 目录数", _fmt_num(repo["worktree_dirs"])),
                _kv("关联工作树一致", "是" if repo["worktree_dirs_consistent"] else "否"),
                _kv("分支", repo["branch"]),
            ]
        )
    else:
        parts.append(f'<p class="unavailable">不可用：{_e(repo["reason"])}</p>')
    parts.append("</section>")

    parts.append('<section class="block full"><h2>判定明细</h2><ul class="checks">')
    for check in card["checks"]:
        parts.append(
            f'<li><span class="dot {check["level"]}"></span><div>'
            f'<code>{_e(check["id"])}</code>{_e(check["detail"])}</div></li>'
        )
    parts.append("</ul></section>")
    parts.append("</div>")

    raw_json = json.dumps(card, ensure_ascii=False, indent=2)
    parts.append(
        f'<details><summary>原始 JSON</summary><pre>{html.escape(raw_json)}</pre></details>'
    )
    parts.append(
        f'<footer>只读生成 · schema {_e(card["schema"])} · '
        "不修改仓库 / 计划任务 / 备份</footer>"
    )
    parts.extend(["</article>", "</main>", "</body>", "</html>"])
    return "\n".join(parts)


def _atomic_write_text(path: Path, text: str) -> None:
    fd, temp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _write_outputs(out_dir: Path, card: dict[str, Any]) -> str | None:
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        if not out_dir.is_dir():
            return f"输出路径不是目录: {out_dir}"
        json_text = json.dumps(card, ensure_ascii=False, indent=2) + "\n"
        _atomic_write_text(out_dir / "health_card.json", json_text)
        _atomic_write_text(out_dir / "health_card.html", _render_html(card))
        return None
    except OSError as exc:
        return f"无法写入输出目录 {out_dir}: {_short_exc(exc)}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_now(text: str) -> dt.datetime:
    parsed = _parse_iso(text)
    if parsed is None:
        raise argparse.ArgumentTypeError(f"无法解析时间: {text!r}")
    return parsed


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="治理健康卡（只读）：任务审计 + 备份 + 仓库卫生 → JSON/HTML",
        epilog=(
            "退出码：0=绿/黄，1=红，2=脚本自身错误。"
            f"阈值：备份 >{BACKUP_YELLOW_HOURS:g}h 黄 / >{BACKUP_RED_HOURS:g}h 红；"
            f"审计 >{AUDIT_YELLOW_HOURS:g}h 黄 / >{AUDIT_RED_HOURS:g}h 红。"
        ),
    )
    parser.add_argument("--repo", metavar="PATH", help="仓库根（默认脚本所在仓库）")
    parser.add_argument(
        "--vault-root", metavar="PATH", help="备份仓根（默认 <repo 的父目录>/_vault）"
    )
    parser.add_argument(
        "--task-audit-root",
        metavar="PATH",
        help="task_exit_audit 输出目录（默认 <repo>/portal/data/task_exit_audit）",
    )
    parser.add_argument(
        "--out", metavar="DIR", help="可选：写入 health_card.json + health_card.html"
    )
    parser.add_argument(
        "--now",
        metavar="ISO8601",
        type=_parse_now,
        help="用指定时间计算新鲜度（默认当前时间，测试用）",
    )
    args = parser.parse_args(argv)

    script_repo = Path(__file__).resolve().parents[1]
    repo_root = Path(args.repo).expanduser().resolve() if args.repo else script_repo
    vault_root = (
        Path(args.vault_root).expanduser().resolve()
        if args.vault_root
        else repo_root.parent / "_vault"
    )
    audit_root = (
        Path(args.task_audit_root).expanduser().resolve()
        if args.task_audit_root
        else repo_root / AUDIT_DATA_REL
    )
    now = args.now or dt.datetime.now().astimezone()

    try:
        card = build_card(repo_root, vault_root, audit_root, now)
    except Exception as exc:  # 兜底：脚本自身错误不给堆栈吓人
        print(f"[内部错误] {_short_exc(exc)}", file=sys.stderr)
        return 2

    if args.out:
        try:
            out_dir = Path(args.out).expanduser().resolve()
            error = _write_outputs(out_dir, card)
        except Exception as exc:  # 含路径解析失败；不给堆栈
            print(
                f"[错误] 无法写入输出目录 {args.out}: {_short_exc(exc)}",
                file=sys.stderr,
            )
            return 2
        if error:
            print(f"[错误] {error}", file=sys.stderr)
            return 2
        print(
            f"[ok] 已写入 {out_dir / 'health_card.json'} / {out_dir / 'health_card.html'}",
            file=sys.stderr,
        )

    print(json.dumps(card, ensure_ascii=False, indent=2))
    return 0 if card["status"] != "red" else 1


if __name__ == "__main__":
    sys.exit(main())
