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
   manifest 行格式：`<name>  <ts>  <bytes> bytes  sha256=<hex>`；
   并用 `git bundle list-heads` + `git rev-parse HEAD` 检查备份包是否追平
   （落后 >0 黄；落后且备份 >24h 红；无法判定计 null，不误判绿）。
3. 仓库卫生：git status --porcelain 行数、git worktree list 数量、
   .worktrees/ 下的目录数（与 worktree list 交叉核对）。
4. worktree TTL（ROADMAP 规则 9）：git worktree list --porcelain 取关联工作树，
   用「分支是否已合并进 master/main」+「分支最后提交时间」判 7 天 TTL；
   已合并且 >7 天计 violation 判黄（不危险但违反规则），读不到红。
5. 文档索引新鲜度：子进程跑 `python -B tools/gen_docs_index.py --check`
   （20s 超时，另设 3s 设计预算）；退出 0 绿 / 1 黄（索引落后）/
   超时或退出 2 或脚本缺失红。差异行尽量解析，解析不了给原始输出前 200 字并标注未解析。

用法：
    python -B tools/gen_health_card.py
    python -B tools/gen_health_card.py --vault-root C:/项目/_vault
    python -B tools/gen_health_card.py --out C:/项目/_hc_probe

退出码：0=绿/黄（可容忍），1=红（有硬问题），2=脚本自身错误（参数/写盘）。
阈值：备份 >36h 黄 / >72h 红；审计 >24h 黄 / >72h 红；dirty>0 黄；
工作树目录与 git worktree 不一致黄；worktree 已合并且 >7 天黄；
文档索引落后黄；任一块不可用红；备份落后 HEAD >0 黄 /
落后且备份 >24h 红；追平无法判定计 null（不误判绿）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
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
# 备份仍在、只是旧：落后 HEAD 记黄；落后且超过 24h 未重备才红。
CATCHUP_RED_HOURS = 24.0
AUDIT_YELLOW_HOURS = 24.0
AUDIT_RED_HOURS = 72.0

# ROADMAP 规则 9：已合并且无未提交改动的工作树，合并后 7 天内必须 remove。
WORKTREE_TTL_DAYS = 7.0
WORKTREE_TTL_DETAIL_LIMIT = 5
WORKTREE_TTL_ITEM_LIMIT = 20
# 单次 git 调用上限；正常 worktree list/for-each-ref 都是毫秒级。
WORKTREE_TTL_GIT_TIMEOUT_SECONDS = 5.0
# 两项新增检查合计 3s 预算；超出按不可判定处理，绝不拖死健康卡。
WORKTREE_TTL_BUDGET_SECONDS = 3.0
WORKTREE_TTL_BRANCH_CANDIDATES = ("master", "main")

DOCS_INDEX_SCRIPT_REL = Path("tools") / "gen_docs_index.py"
DOCS_CHECK_TIMEOUT_SECONDS = 20.0
DOCS_CHECK_BUDGET_SECONDS = 3.0
DOCS_OUTPUT_EXCERPT_CHARS = 200
# gen_docs_index.py --check 的固定汇总行：
# 差异合计：新增 N / 删除 N / 标题变化 N / 日期变化 N / 节标题变化 N / 节说明变化 N
DOCS_TOTALS_RE = re.compile(
    r"差异合计：新增\s*(?P<added>\d+)\s*/\s*删除\s*(?P<removed>\d+)\s*/"
    r"\s*标题变化\s*(?P<title>\d+)\s*/\s*日期变化\s*(?P<date>\d+)\s*/"
    r"\s*节标题变化\s*(?P<heading>\d+)\s*/\s*节说明变化\s*(?P<note>\d+)"
)
DOCS_DIFF_LABELS = (
    ("added", "新增"),
    ("removed", "删除"),
    ("title", "标题变化"),
    ("date", "日期变化"),
    ("heading", "节标题变化"),
    ("note", "节说明变化"),
)

# 与 tools/backup_bundles.ps1 的 $repos 一一对应（label = bundle 文件名前缀）。
CATCHUP_REPO_DIRS: tuple[tuple[str, str], ...] = (
    ("aios", "老田的AI助手"),
    ("zerobound-kg", "zerobound-kg"),
    ("digital-legion", "digital-legion"),
    ("res-downloader", "res-downloader-py"),
)
CATCHUP_REF_CANDIDATES = ("refs/heads/master", "refs/heads/main", "HEAD")
SHA_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")

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
        "bundle_files": [],
        "sha256_verified": False,
        "catchup": _empty_catchup(),
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
        # 注释行：# 开头是备份脚本写的头/尾摘要（`# vault backup <time>` /
        # `# vault <dir>` / `# summary ok=..`），不是条目，不能算格式异常。
        # 与 tools/verify_backups.py 的口径保持一致（它同样跳过 # 行）。
        if stripped.startswith("#"):
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
    result["bundle_files"] = sorted(
        name for name in entries if name.lower().endswith(".bundle")
    )

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
# 3b) worktree TTL（ROADMAP 规则 9）
# ---------------------------------------------------------------------------
def _parse_worktree_porcelain(text: str) -> list[dict[str, Any]]:
    """解析 `git worktree list --porcelain`；空行分块，返回按出现顺序的条目。"""
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal current
        if current is not None:
            entries.append(current)
            current = None

    for line in text.splitlines():
        if not line.strip():
            flush()
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            flush()
            current = {
                "path": value,
                "head": None,
                "branch": None,
                "detached": False,
            }
            continue
        if current is None:
            continue
        if key == "HEAD":
            current["head"] = value
        elif key == "branch":
            current["branch"] = value
        elif key == "detached":
            current["detached"] = True
        elif key in ("bare", "locked", "prunable"):
            # 只记录存在性；locked/prunable 的说明文本不参与判定。
            current[key] = value or True
    flush()
    return entries


def _worktree_display_path(path: str, repo_root: Path) -> str:
    """尽量显示相对 repo_root 的 posix 路径；跨盘等无法相对时回退原路径。"""
    try:
        relative = os.path.relpath(path, str(repo_root))
    except (OSError, ValueError):
        return path.replace("\\", "/")
    return Path(relative).as_posix()


def _empty_worktree_ttl(repo_root: Path) -> dict[str, Any]:
    return {
        "available": False,
        "reason": None,
        "repo_root": str(repo_root),
        "target_ref": None,
        "age_source": "branch_commit_time",
        "threshold_days": WORKTREE_TTL_DAYS,
        "duration_seconds": None,
        "budget_seconds": WORKTREE_TTL_BUDGET_SECONDS,
        "main_worktree": None,
        "worktrees_total": None,
        "linked_worktrees": None,
        "merged_worktrees": None,
        "violations": None,
        "unknown": None,
        "status": None,
        "level": None,
        "expired": [],
        "unknown_worktrees": [],
        "items": [],
    }


def _branch_commit_times(
    repo_root: Path, timeout: float
) -> tuple[dict[str, int] | None, str | None]:
    """一次性取所有本地分支的 tip 提交时间（committerdate unix 秒）。

    选「分支最后提交时间」而不是目录 mtime：worktree 目录 mtime 会因增删
    顶层文件而漂移（哪怕没有提交），无法代表「合并后过了多久」。
    """
    try:
        proc = _git_run(
            repo_root,
            ["for-each-ref", "--format=%(refname:short)\t%(committerdate:unix)", "refs/heads"],
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"git for-each-ref 失败: {_short_exc(exc)}"
    if proc.returncode != 0:
        return None, "git for-each-ref 失败: " + (
            _decode(proc.stderr).strip()[:300] or "git 返回非零"
        )
    refs: dict[str, int] = {}
    for line in _decode(proc.stdout).splitlines():
        name, sep, raw = line.partition("\t")
        if not sep or not name:
            continue
        try:
            refs[name] = int(raw.strip())
        except ValueError:
            continue
    return refs, None


def collect_worktree_ttl(repo_root: Path, now: dt.datetime) -> dict[str, Any]:
    """统计「已合并进 master/main + 分支最后提交 >7 天」的关联工作树。

    只统计 `git worktree list` 中除主树（第一条）外的关联工作树；
    detached HEAD / 分支引用缺失 / 取不到提交时间的树计 unknown，不冒充绿。
    """
    result = _empty_worktree_ttl(repo_root)
    started = time.monotonic()
    deadline = started + WORKTREE_TTL_BUDGET_SECONDS

    def next_timeout() -> float:
        """把剩余预算分给每一次 git 调用，避免 4-5 次调用各自超时拖过 3s。"""
        remaining = deadline - time.monotonic()
        if remaining <= 0.05:
            raise subprocess.TimeoutExpired("git", WORKTREE_TTL_BUDGET_SECONDS)
        return min(WORKTREE_TTL_GIT_TIMEOUT_SECONDS, remaining)

    try:
        if not repo_root.is_dir():
            result["reason"] = f"仓库根不存在或不是目录: {repo_root}"
            return result

        try:
            probe = _git_run(
                repo_root,
                ["rev-parse", "--is-inside-work-tree"],
                timeout=next_timeout(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["reason"] = f"git 探测失败: {_short_exc(exc)}"
            return result
        if probe.returncode != 0:
            result["reason"] = "不是 git 工作树: " + (
                _decode(probe.stderr).strip()[:300] or "git 返回非零"
            )
            return result

        target_ref: str | None = None
        for candidate in WORKTREE_TTL_BRANCH_CANDIDATES:
            probe = _git_run(
                repo_root,
                ["rev-parse", "--verify", "--quiet", f"refs/heads/{candidate}"],
                timeout=next_timeout(),
            )
            if probe.returncode == 0:
                target_ref = candidate
                break
        if target_ref is None:
            result["reason"] = (
                "找不到本地分支 master/main，无法判定工作树是否已合并"
            )
            return result
        result["target_ref"] = target_ref

        try:
            listing = _git_run(
                repo_root,
                ["worktree", "list", "--porcelain"],
                timeout=next_timeout(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["reason"] = f"git worktree list 失败: {_short_exc(exc)}"
            return result
        if listing.returncode != 0:
            result["reason"] = "git worktree list 失败: " + (
                _decode(listing.stderr).strip()[:300] or "git 返回非零"
            )
            return result

        try:
            merged_proc = _git_run(
                repo_root,
                [
                    "branch",
                    "--merged",
                    target_ref,
                    "--format=%(refname:short)",
                ],
                timeout=next_timeout(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["reason"] = f"git branch --merged 失败: {_short_exc(exc)}"
            return result
        if merged_proc.returncode != 0:
            result["reason"] = "git branch --merged 失败: " + (
                _decode(merged_proc.stderr).strip()[:300] or "git 返回非零"
            )
            return result

        refs, refs_error = _branch_commit_times(repo_root, next_timeout())
        if refs is None:
            result["reason"] = refs_error or "无法读取分支提交时间"
            return result

        entries = _parse_worktree_porcelain(_decode(listing.stdout))
        if not entries:
            result["reason"] = "git worktree list 未返回任何工作树"
            return result

        # 第一条是主树；规则 9 只约束可回收的关联工作树。
        main_entry = entries[0]
        linked = entries[1:]
        result["main_worktree"] = _worktree_display_path(
            str(main_entry.get("path") or ""), repo_root
        )

        merged_set = {
            line.strip()
            for line in _decode(merged_proc.stdout).splitlines()
            if line.strip()
        }
        now_timestamp = now.timestamp()
        expired: list[dict[str, Any]] = []
        unknown: list[dict[str, Any]] = []
        items: list[dict[str, Any]] = []
        merged_count = 0

        for entry in linked:
            raw_branch = str(entry.get("branch") or "")
            branch = (
                raw_branch[len("refs/heads/") :]
                if raw_branch.startswith("refs/heads/")
                else (raw_branch or None)
            )
            display = _worktree_display_path(str(entry.get("path") or ""), repo_root)
            merged: bool | None = None
            age_days: float | None = None
            last_commit_at: str | None = None
            status = "unknown"
            unknown_reason: str | None = None

            if entry.get("detached") or not branch:
                unknown_reason = "detached HEAD（无分支可判）"
            elif branch not in refs:
                unknown_reason = "分支引用不存在或读不到提交时间"
            else:
                merged = branch in merged_set
                timestamp = refs[branch]
                last_commit_at = _iso(
                    dt.datetime.fromtimestamp(timestamp).astimezone()
                )
                age_days = round((now_timestamp - timestamp) / 86400.0, 2)
                if merged:
                    merged_count += 1
                    status = "expired" if age_days > WORKTREE_TTL_DAYS else "merged_fresh"
                else:
                    status = "unmerged"

            item: dict[str, Any] = {
                "path": display,
                "branch": branch,
                "head": _short_sha(entry.get("head")),
                "merged": merged,
                "age_days": age_days,
                "last_commit_at": last_commit_at,
                "status": status,
            }
            if unknown_reason is not None:
                item["unknown_reason"] = unknown_reason
                unknown.append(item)
            elif status == "expired":
                expired.append(item)
            if len(items) < WORKTREE_TTL_ITEM_LIMIT:
                items.append(item)

        result["worktrees_total"] = len(entries)
        result["linked_worktrees"] = len(linked)
        result["merged_worktrees"] = merged_count
        result["violations"] = len(expired)
        result["unknown"] = len(unknown)
        result["expired"] = expired[:WORKTREE_TTL_DETAIL_LIMIT]
        result["unknown_worktrees"] = unknown[:WORKTREE_TTL_DETAIL_LIMIT]
        result["items"] = items
        result["available"] = True
        if expired:
            result["status"] = "expired"
            result["level"] = "yellow"
        elif unknown:
            result["status"] = "unknown"
            # 未知态单列、判黄：不危险，但也不能冒充绿。
            result["level"] = "yellow"
        else:
            result["status"] = "current"
            result["level"] = "green"
    except FileNotFoundError:
        result["reason"] = "找不到 git 可执行文件"
        return result
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["reason"] = f"git 探测失败: {_short_exc(exc)}"
        return result
    finally:
        result["duration_seconds"] = round(time.monotonic() - started, 3)

    elapsed = result["duration_seconds"] or 0.0
    if elapsed > WORKTREE_TTL_BUDGET_SECONDS:
        result["reason"] = (
            f"检查耗时 {elapsed:.2f}s 超出 {WORKTREE_TTL_BUDGET_SECONDS:g}s 预算，结果不采信"
        )
        result["available"] = False
        result["status"] = None
        result["level"] = None
        result["worktrees_total"] = None
        result["linked_worktrees"] = None
        result["merged_worktrees"] = None
        result["violations"] = None
        result["unknown"] = None
        result["expired"] = []
        result["unknown_worktrees"] = []
        result["items"] = []
    return result


# ---------------------------------------------------------------------------
# 3c) 文档索引新鲜度（gen_docs_index.py --check）
# ---------------------------------------------------------------------------
def _empty_docs_index(repo_root: Path) -> dict[str, Any]:
    script = repo_root / DOCS_INDEX_SCRIPT_REL
    return {
        "available": False,
        "reason": None,
        "repo_root": str(repo_root),
        "script": str(script),
        "command": None,
        "exit_code": None,
        "status": None,
        "level": None,
        "parsed": False,
        "diffs": None,
        "diffs_total": None,
        "duration_seconds": None,
        "timeout_seconds": DOCS_CHECK_TIMEOUT_SECONDS,
        "budget_seconds": DOCS_CHECK_BUDGET_SECONDS,
        "timed_out": False,
        "output_excerpt": "",
        "output_truncated": False,
    }


def _docs_diff_text(diffs: dict[str, int]) -> str:
    parts = [
        f"{label} {diffs.get(key, 0)}" for key, label in DOCS_DIFF_LABELS
    ]
    return " / ".join(parts)


def collect_docs_index(repo_root: Path) -> dict[str, Any]:
    """用子进程跑 `gen_docs_index.py --check`；0=绿、1=黄、其余/超时=红。"""
    result = _empty_docs_index(repo_root)
    script = repo_root / DOCS_INDEX_SCRIPT_REL
    result["script"] = str(script)
    if not script.is_file():
        result["reason"] = f"找不到索引脚本: {script}"
        return result

    command = [sys.executable, "-B", str(script), "--check"]
    result["command"] = command
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            timeout=DOCS_CHECK_TIMEOUT_SECONDS,
            check=False,
            cwd=str(repo_root),
            env=env,
        )
    except subprocess.TimeoutExpired:
        result["duration_seconds"] = round(time.monotonic() - started, 3)
        result["timed_out"] = True
        result["reason"] = f"gen_docs_index.py --check 超时（>{DOCS_CHECK_TIMEOUT_SECONDS:g}s）"
        return result
    except (OSError, ValueError) as exc:
        result["duration_seconds"] = round(time.monotonic() - started, 3)
        result["reason"] = f"无法执行 gen_docs_index.py --check: {_short_exc(exc)}"
        return result

    elapsed = round(time.monotonic() - started, 3)
    result["duration_seconds"] = elapsed
    result["exit_code"] = proc.returncode
    stdout = _decode(proc.stdout)
    stderr = _decode(proc.stderr)
    excerpt = stdout.strip()
    if not excerpt:
        excerpt = stderr.strip()
    result["output_excerpt"] = excerpt[:DOCS_OUTPUT_EXCERPT_CHARS]
    result["output_truncated"] = len(excerpt) > DOCS_OUTPUT_EXCERPT_CHARS

    match = DOCS_TOTALS_RE.search(stdout)
    if match is not None:
        diffs = {key: int(value) for key, value in match.groupdict().items()}
        result["parsed"] = True
        result["diffs"] = diffs
        result["diffs_total"] = sum(diffs.values())

    if elapsed > DOCS_CHECK_BUDGET_SECONDS:
        result["reason"] = (
            f"--check 耗时 {elapsed:.2f}s 超出 {DOCS_CHECK_BUDGET_SECONDS:g}s 预算，结果不采信"
        )
        # 不可判定时计数一律置 null，不用半截结果冒充可用。
        result["parsed"] = False
        result["diffs"] = None
        result["diffs_total"] = None
        return result
    if proc.returncode == 0:
        result["available"] = True
        result["status"] = "fresh"
        result["level"] = "green"
        return result
    if proc.returncode == 1:
        # 退出 1 是「索引落后」的业务信号，不是读不到：不判红，交给人重生成。
        result["available"] = True
        result["status"] = "stale"
        result["level"] = "yellow"
        return result

    result["reason"] = f"gen_docs_index.py --check 退出码 {proc.returncode}"
    if result["output_excerpt"]:
        result["reason"] += "；输出：" + result["output_excerpt"]
    return result


# ---------------------------------------------------------------------------
# 2b) 备份是否追平 HEAD（bundle 与仓库 HEAD 对比，只读）
# ---------------------------------------------------------------------------
def _short_sha(sha: Any) -> str:
    return sha[:8] if isinstance(sha, str) and sha else "-"


def _empty_catchup() -> dict[str, Any]:
    return {
        "available": False,
        "complete": False,
        "reason": None,
        "checked_date": None,
        "status": None,
        "level": None,
        "behind_count": None,
        "unknown_count": None,
        "max_behind_commits": None,
        "backup_age_hours": None,
        "thresholds_hours": {"red": CATCHUP_RED_HOURS},
        "repos": [],
        "entries": [],
        "behind": [],
        "unknown": [],
    }


def _discover_catchup_repos(repo_root: Path) -> list[dict[str, Any]]:
    """四个备份仓固定在工作区（repo_root 的父目录）下；缺目录也要列出来。

    缺目录不能静默略过：略过会让"3 个仓追平"冒充"全部追平"。缺的那个会以
    unknown 计入，从而让 catchup 不判绿。
    """
    workspace = repo_root.parent
    repos: list[dict[str, Any]] = []
    for label, dirname in CATCHUP_REPO_DIRS:
        path = workspace / dirname
        if label == "aios" and not path.is_dir() and repo_root.name in (
            "老田的AI助手",
            "aios",
        ):
            path = repo_root
        repos.append({"label": label, "path": path, "exists": path.is_dir()})
    return repos


def _bundle_for_label(bundle_files: list[str], label: str) -> str | None:
    prefix = label + "-"
    for name in bundle_files:
        if name.startswith(prefix) and name.lower().endswith(".bundle"):
            return name
    return None


def _parse_bundle_heads(text: str) -> dict[str, str]:
    heads: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or not SHA_RE.match(parts[0]):
            continue
        heads[" ".join(parts[1:])] = parts[0].lower()
    return heads


def _pick_bundle_ref(
    heads: dict[str, str], branch: str | None
) -> tuple[str | None, str | None, str | None]:
    """当前分支 → master → main → HEAD → 唯一引用；返回 (ref, sha, note)。"""
    candidates: list[str] = []
    if branch and branch not in ("HEAD", ""):
        candidates.append("refs/heads/" + branch)
        candidates.extend(CATCHUP_REF_CANDIDATES)
    else:
        # detached HEAD：bundle 的 HEAD 行才是备份时真正检出的提交。
        candidates.append("HEAD")
        candidates.extend(
            ref for ref in CATCHUP_REF_CANDIDATES if ref != "HEAD"
        )
    seen: set[str] = set()
    for ref in candidates:
        if ref in seen:
            continue
        seen.add(ref)
        if ref in heads:
            return ref, heads[ref], None
    if len(heads) == 1:
        ref, sha = next(iter(heads.items()))
        return ref, sha, f"bundle 缺少期望分支引用，退化为唯一引用 {ref}"
    return (
        None,
        None,
        "bundle 缺少期望分支引用（现有: %s）"
        % (", ".join(sorted(heads)) or "none"),
    )


def collect_catchup(
    repo_root: Path, backup: dict[str, Any], now: dt.datetime
) -> dict[str, Any]:
    """用最新备份里的 bundle 逐仓比较 HEAD；结果可供 _evaluate 判黄/红。"""
    del now  # 落后时长用备份目录 age_hours，不另算当前时间
    result = _empty_catchup()
    result["backup_age_hours"] = backup.get("age_hours")
    if not backup.get("available"):
        result["reason"] = "备份不可用：" + (backup.get("reason") or "未知原因")
        return result
    bundle_files = backup.get("bundle_files") or []
    if not bundle_files:
        result["reason"] = "最新 MANIFEST 没有 *.bundle 条目"
        return result
    if shutil.which("git") is None:
        result["reason"] = "找不到 git 可执行文件"
        return result

    latest_dir = Path(backup["dir"]) if backup.get("dir") else None
    repos = _discover_catchup_repos(repo_root)
    if latest_dir is None:
        result["reason"] = "无法定位最新备份目录"
        return result
    result["checked_date"] = backup.get("latest_date")
    result["repos"] = [
        {
            "label": spec["label"],
            "path": str(spec["path"]),
            "exists": spec["exists"],
        }
        for spec in repos
    ]

    for spec in repos:
        label = spec["label"]
        repo_path: Path = spec["path"]
        entry: dict[str, Any] = {
            "label": label,
            "date": backup.get("latest_date"),
            "repo": str(repo_path),
            "bundle": None,
            "bundle_name": None,
            "ref": None,
            "ref_note": None,
            "bundle_sha": None,
            "bundle_sha_short": None,
            "head_sha": None,
            "head_sha_short": None,
            "match": None,
            "behind_commits": "unknown",
            "status": "unknown",
            "reason": None,
        }
        branch: str | None = None

        if not spec["exists"]:
            entry["reason"] = f"仓目录不存在: {repo_path}"
        else:
            try:
                probe = _git_run(repo_path, ["rev-parse", "--is-inside-work-tree"])
            except (OSError, subprocess.TimeoutExpired) as exc:
                entry["reason"] = f"git 探测失败: {_short_exc(exc)}"
            else:
                if probe.returncode != 0:
                    entry["reason"] = "不是 git 工作树: " + (
                        _decode(probe.stderr).strip()[:200] or "git 返回非零"
                    )
                else:
                    try:
                        head_proc = _git_run(repo_path, ["rev-parse", "HEAD"])
                        branch_proc = _git_run(
                            repo_path, ["rev-parse", "--abbrev-ref", "HEAD"]
                        )
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        entry["reason"] = f"git rev-parse 失败: {_short_exc(exc)}"
                    else:
                        if head_proc.returncode == 0:
                            lines = _decode(head_proc.stdout).strip().splitlines()
                            if lines:
                                entry["head_sha"] = lines[0].lower()
                                entry["head_sha_short"] = _short_sha(lines[0])
                        else:
                            entry["reason"] = "git rev-parse HEAD 失败: " + (
                                _decode(head_proc.stderr).strip()[:200]
                                or "git 返回非零"
                            )
                        if branch_proc.returncode == 0:
                            branch = _decode(branch_proc.stdout).strip() or None

        bundle_name = _bundle_for_label(bundle_files, label)
        if bundle_name is None:
            if entry["reason"] is None:
                entry["reason"] = f"MANIFEST 里没有 {label}-*.bundle"
        else:
            bundle_path = latest_dir / bundle_name
            entry["bundle_name"] = bundle_name
            entry["bundle"] = str(bundle_path)
            if not bundle_path.is_file():
                if entry["reason"] is None:
                    entry["reason"] = "MANIFEST 列了 bundle 但文件不存在"
            elif entry["reason"] is None:
                try:
                    heads_proc = _git_run(
                        repo_path, ["bundle", "list-heads", str(bundle_path)]
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    entry["reason"] = f"git bundle list-heads 失败: {_short_exc(exc)}"
                else:
                    if heads_proc.returncode != 0:
                        entry["reason"] = "git bundle list-heads 失败: " + (
                            _decode(heads_proc.stderr).strip()[:240]
                            or "git 返回非零"
                        )
                    else:
                        heads = _parse_bundle_heads(_decode(heads_proc.stdout))
                        if not heads:
                            entry["reason"] = "bundle 里解析不到任何引用"
                        else:
                            ref, bundle_sha, ref_note = _pick_bundle_ref(
                                heads, branch
                            )
                            entry["ref"] = ref
                            entry["ref_note"] = ref_note
                            entry["bundle_sha"] = bundle_sha
                            entry["bundle_sha_short"] = _short_sha(bundle_sha)
                            if bundle_sha is None or entry["head_sha"] is None:
                                entry["reason"] = ref_note or "缺少 bundle/HEAD 的 SHA"
                            elif bundle_sha == entry["head_sha"]:
                                entry["match"] = True
                                entry["behind_commits"] = 0
                                entry["status"] = "current"
                            else:
                                entry["match"] = False
                                exists = _git_run(
                                    repo_path,
                                    ["cat-file", "-e", bundle_sha + "^{commit}"],
                                )
                                if exists.returncode != 0:
                                    entry["reason"] = (
                                        "bundle 提交 %s 不在本仓对象库，无法计算落后数"
                                        % _short_sha(bundle_sha)
                                    )
                                else:
                                    count_proc = _git_run(
                                        repo_path,
                                        ["rev-list", "--count", bundle_sha + "..HEAD"],
                                    )
                                    if count_proc.returncode != 0:
                                        entry["reason"] = (
                                            "git rev-list --count 失败: "
                                            + (
                                                _decode(count_proc.stderr).strip()[:200]
                                                or "git 返回非零"
                                            )
                                        )
                                    else:
                                        try:
                                            count = int(
                                                _decode(count_proc.stdout)
                                                .strip()
                                                .splitlines()[0]
                                            )
                                        except (ValueError, IndexError):
                                            entry["reason"] = "git rev-list 输出无法解析"
                                        else:
                                            entry["behind_commits"] = count
                                            if count > 0:
                                                entry["status"] = "behind"
                                            else:
                                                # SHA 不同但计数为 0：不是"落后"
                                                # （典型是 bundle 比 HEAD 还新/已分叉），
                                                # 记 unknown，绝不冒充 current。
                                                entry["status"] = "unknown"
                                                entry["reason"] = (
                                                    "bundle 提交与 HEAD 不同且 "
                                                    "rev-list 计数为 0（非落后/可能分叉）"
                                                )

        if entry["status"] == "behind":
            result["behind"].append(entry)
        elif entry["status"] == "unknown":
            result["unknown"].append(entry)
        result["entries"].append(entry)

    behind_counts = [
        entry["behind_commits"]
        for entry in result["behind"]
        if isinstance(entry["behind_commits"], int)
    ]
    result["behind_count"] = len(result["behind"])
    result["unknown_count"] = len(result["unknown"])
    result["max_behind_commits"] = max(behind_counts) if behind_counts else None
    result["complete"] = bool(result["entries"]) and not result["unknown"]

    age = backup.get("age_hours")
    if result["behind"]:
        result["status"] = "behind"
        result["level"] = (
            "red"
            if isinstance(age, (int, float)) and age > CATCHUP_RED_HOURS
            else "yellow"
        )
        # 已经判出"落后"就是可判定；即使另有仓 unknown，这项也要报黄/红。
        result["available"] = True
    elif result["unknown"] or not result["entries"]:
        # 没有落后、但还有仓判不了 → 整项计 null，绝不冒充绿。
        result["status"] = "unknown"
        result["level"] = None
        result["available"] = False
        if not result["reason"]:
            determinable = [
                entry
                for entry in result["entries"]
                if entry["status"] in ("current", "behind")
            ]
            result["reason"] = (
                "部分仓无法判定" if determinable else "所有仓都无法判定"
            )
    else:
        result["status"] = "current"
        result["level"] = "green"
        result["available"] = True
    return result


def _catchup_behind_text(catchup: dict[str, Any]) -> str:
    parts = []
    for entry in catchup.get("behind") or []:
        parts.append(
            "%s 落后 %s 个提交（backup %s < head %s）"
            % (
                entry["label"],
                entry["behind_commits"],
                entry["bundle_sha_short"],
                entry["head_sha_short"],
            )
        )
    unknown = catchup.get("unknown") or []
    if unknown:
        parts.append("另有 %d 个仓无法判定" % len(unknown))
    return "；".join(parts) or "无法判定"


def _worktree_ttl_detail(ttl: dict[str, Any]) -> str:
    days = ttl.get("threshold_days") or WORKTREE_TTL_DAYS
    expired = ttl.get("expired") or []
    bits = [
        "%s（%s，%.1f 天）"
        % (item["path"], item.get("branch") or "?", item.get("age_days") or 0.0)
        for item in expired
    ]
    detail = "%d 个已合并且超过 %g 天未清理：%s" % (
        ttl.get("violations") or 0,
        days,
        "、".join(bits),
    )
    hidden = (ttl.get("violations") or 0) - len(bits)
    if hidden > 0:
        detail += "；另有 %d 个未列出" % hidden
    if ttl.get("unknown"):
        detail += "；另有 %d 个无法判定" % ttl["unknown"]
    return detail + "（规则 9，需 git worktree remove）"


def _worktree_ttl_unknown_detail(ttl: dict[str, Any]) -> str:
    unknown = ttl.get("unknown_worktrees") or []
    bits = [
        "%s（%s）" % (item["path"], item.get("unknown_reason") or "未知")
        for item in unknown
    ]
    detail = "%d 个关联工作树无法判定是否已合并/年龄：%s" % (
        ttl.get("unknown") or 0,
        "、".join(bits),
    )
    hidden = (ttl.get("unknown") or 0) - len(bits)
    if hidden > 0:
        detail += "；另有 %d 个未列出" % hidden
    return detail + "；不判绿"


def _docs_index_detail(freshness: dict[str, Any]) -> str:
    if freshness.get("status") == "stale":
        if freshness.get("parsed"):
            diffs = freshness.get("diffs") or {}
            return (
                "DOCS_INDEX 落后：%s（差异合计 %d 处）；"
                "运行 python -B tools/gen_docs_index.py 重生成"
                % (_docs_diff_text(diffs), freshness.get("diffs_total") or 0)
            )
        excerpt = (freshness.get("output_excerpt") or "（无输出）").replace("\n", " ")
        return "DOCS_INDEX 落后（退出码 1，未解析）；原始输出前 200 字：%s" % excerpt
    duration = freshness.get("duration_seconds")
    suffix = "，%.2fs" % duration if isinstance(duration, (int, float)) else ""
    return "DOCS_INDEX 与生成结果一致（退出码 0%s）" % suffix


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

        catchup = backup.get("catchup") or {}
        if catchup.get("available"):
            if catchup.get("status") == "current":
                add(
                    "backup.catchup",
                    "green",
                    "%d 个仓的 bundle 均追平 HEAD"
                    % len(catchup.get("entries") or []),
                )
            elif catchup.get("status") == "behind":
                level = catchup.get("level")
                if level not in ("yellow", "red"):
                    level = "yellow"
                detail = _catchup_behind_text(catchup)
                age = catchup.get("backup_age_hours")
                if level == "red" and isinstance(age, (int, float)):
                    detail = "备份已 %.1fh 未更新且落后：%s" % (age, detail)
                add("backup.catchup", level, detail)
            # status == unknown：故意不新增 check。该项计 null，不误判绿。

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

    ttl = repo.get("worktree_ttl") or {}
    if not ttl.get("available"):
        add(
            "repo.worktree_ttl",
            "red",
            "worktree TTL 不可用：" + (ttl.get("reason") or "未知原因"),
        )
    elif ttl.get("violations"):
        add("repo.worktree_ttl", "yellow", _worktree_ttl_detail(ttl))
    elif ttl.get("unknown"):
        add("repo.worktree_ttl", "yellow", _worktree_ttl_unknown_detail(ttl))
    else:
        add(
            "repo.worktree_ttl",
            "green",
            "关联工作树 %d 个，无「已合并且超过 %g 天」项（年龄按分支最后提交时间）"
            % (
                ttl.get("linked_worktrees") or 0,
                ttl.get("threshold_days") or WORKTREE_TTL_DAYS,
            ),
        )

    docs_freshness = (card.get("docs") or {}).get("index_freshness") or {}
    if not docs_freshness.get("available"):
        add(
            "docs.index_freshness",
            "red",
            "文档索引检查不可用：" + (docs_freshness.get("reason") or "未知原因"),
        )
    elif docs_freshness.get("status") == "stale":
        add("docs.index_freshness", "yellow", _docs_index_detail(docs_freshness))
    elif docs_freshness.get("status") == "fresh":
        add("docs.index_freshness", "green", _docs_index_detail(docs_freshness))
    else:
        add(
            "docs.index_freshness",
            "yellow",
            "文档索引检查状态未知：" + str(docs_freshness.get("status") or "null"),
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
    backup = collect_backup(vault_root, now)
    backup["catchup"] = collect_catchup(repo_root, backup, now)
    repo_block = collect_repo(repo_root, now)
    repo_block["worktree_ttl"] = collect_worktree_ttl(repo_root, now)
    card: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": _iso(now),
        "status": "green",
        "exit_code": 0,
        "reasons": [],
        "checks": [],
        "task_audit": collect_task_audit(audit_root, now, semantics, semantics_error),
        "backup": backup,
        "repo": repo_block,
        "docs": {"index_freshness": collect_docs_index(repo_root)},
        "notes": [
            "本脚本只读：不扫描/启停计划任务、不触发备份、不改仓库。",
            "dirty_files 含未跟踪文件；本脚本自身在提交前也会被计入。",
            "备份未重算 sha256（体积可达数百 MiB）；只核对清单条目、文件存在性与大小。",
            "备份追平：用 git bundle list-heads 把 bundle 内分支 SHA 与各仓 HEAD 比较；"
            "落后 >0 黄、落后且 >24h 未重备红；无法判定计 null，不误判绿。",
            "task_audit 失败口径沿用其账本：Disabled 不计、267011 记未运行、其余非零（含 267009/267014）记失败。",
            "未知退出码同时计入 failed（沿用审计口径），此处单独列示以便区分「已知失败」与「未知退出码」。",
            f"阈值：备份 >{BACKUP_YELLOW_HOURS:g}h 黄 / >{BACKUP_RED_HOURS:g}h 红；"
            "备份落后 HEAD >0 黄 / 落后且 >24h 红；"
            f"审计 >{AUDIT_YELLOW_HOURS:g}h 黄 / >{AUDIT_RED_HOURS:g}h 红；"
            "dirty>0 黄；工作树不一致黄；任一块不可用红。",
            "账本按落盘时间切分扫描会话，取最后一个会话；120s 内的连续记录视为同一次扫描。",
            f"worktree TTL（规则 9）：只算关联工作树（排除主树），"
            f"「已合并进 master/main + 分支最后提交 >{WORKTREE_TTL_DAYS:g} 天」黄；"
            "年龄取分支 tip 的 committerdate（目录 mtime 会被增删文件带偏）；"
            "detached/分支缺失计未知并判黄，git 不可用或读不到判红。",
            f"文档索引：子进程跑 python -B tools/gen_docs_index.py --check，"
            f"超时 {DOCS_CHECK_TIMEOUT_SECONDS:g}s、设计预算 {DOCS_CHECK_BUDGET_SECONDS:g}s；"
            "退出 0 绿 / 1 黄（索引落后）/ 超时或退出 2 或脚本缺失红；"
            "两条新检查合计目标 <3s，超预算按不可判定处理。",
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
        catchup = backup.get("catchup") or {}
        if catchup.get("available"):
            if catchup.get("status") == "current":
                catchup_text = "%d 个仓均追平 HEAD（%s）" % (
                    len(catchup["entries"]),
                    ", ".join(
                        "%s=%s" % (entry["label"], entry["head_sha_short"])
                        for entry in catchup["entries"]
                    ),
                )
            elif catchup.get("status") == "behind":
                catchup_text = _catchup_behind_text(catchup)
            else:
                catchup_text = "无法判定"
        else:
            catchup_text = "无法判定"
        parts.extend(
            [
                _kv("最近备份", backup["latest_date"]),
                _kv("距今", _fmt_age(backup["age_hours"])),
                _kv("MANIFEST 条目", _fmt_num(backup["manifest_entries"])),
                _kv("目录总体积（含清单）", backup["total_human"]),
                _kv("完整性", integrity),
                _kv("追平 HEAD", catchup_text),
                _kv("目录 mtime", backup["dir_mtime"]),
            ]
        )
        parts.append('<p class="note">未重算 sha256；只核对条目、存在性与大小。</p>')
        if not catchup.get("available"):
            parts.append(
                f'<p class="note">追平检查不可用：{_e(catchup.get("reason"))}</p>'
            )
        elif catchup.get("status") == "unknown":
            parts.append('<p class="note">部分仓无法判定，本项不计绿。</p>')
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
    ttl = repo.get("worktree_ttl") or {}
    if ttl.get("available"):
        if ttl.get("violations"):
            ttl_text = "超期 %d 个" % ttl["violations"]
        elif ttl.get("unknown"):
            ttl_text = "无法判定 %d 个" % ttl["unknown"]
        else:
            ttl_text = "无超期（关联树 %d 个）" % (ttl.get("linked_worktrees") or 0)
        parts.append(f'<p class="note">worktree TTL：{_e(ttl_text)}</p>')
    else:
        parts.append(
            f'<p class="note">worktree TTL 不可用：{_e(ttl.get("reason"))}</p>'
        )
    parts.append("</section>")

    freshness = (card.get("docs") or {}).get("index_freshness") or {}
    parts.append('<section class="block">')
    parts.append("<h2>文档索引</h2>")
    parts.append(f'<div class="sub">{_e(freshness.get("script"))}</div>')
    if freshness.get("available"):
        parts.append(
            _kv(
                "状态",
                "落后（需重生成）"
                if freshness.get("status") == "stale"
                else "一致",
            )
        )
        if freshness.get("parsed") and freshness.get("diffs") is not None:
            parts.append(_kv("差异", _docs_diff_text(freshness["diffs"])))
        parts.append(
            _kv(
                "退出码 / 耗时",
                "%s / %ss"
                % (freshness.get("exit_code"), freshness.get("duration_seconds")),
            )
        )
    else:
        parts.append(
            f'<p class="unavailable">不可用：{_e(freshness.get("reason"))}</p>'
        )
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
            f"备份落后 HEAD >0 黄 / 落后且 >{CATCHUP_RED_HOURS:g}h 红；"
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
