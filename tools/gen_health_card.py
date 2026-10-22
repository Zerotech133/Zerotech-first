#!/usr/bin/env python3
"""gen_health_card.py — 治理健康卡（只读，零第三方依赖）。

聚合五类治理信号，输出一张机器可消费的 JSON（默认 stdout）与可选的自包含
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
6. 数据根漂移：<vault-root>/<yyyy-MM-dd>/data_manifest.json（tools/gen_data_manifest.py
   的产物，含 total_files/total_bytes/dirs[]/catalog.sha256/root）按日期目录名倒序
   取最新两期，比对总文件数、总字节、顶层目录集合、catalog.json sha256 与数据根路径。
   只有一份清单 → available=false（不判绿）；两份都读不到/解析失败 → 红；
   文件数变化 >20% 或顶层目录数变化 → 升级为红（这个幅度通常是事故，不是日常增量）。
7. 明文私钥扫描（security.plaintext_keys）：只扫限定范围——<repo>/outputs、
   <repo>/tools、<repo>/portal 递归，加工作区根一级 *.pem/*.key/id_rsa*/.env*；
   跳过 .git/node_modules/.venv/.worktrees（另跳 __pycache__ 等编译缓存）。
   识别只看文件名与内容特征：*.pem/*.key/id_rsa*/.env* 内容含 PRIVATE KEY 字样，
   或任意文件内容含 PEM 私钥起始行；不解析密钥格式、不回显任何内容，命中只报
   「路径 + 字节数 + 是否被 git 跟踪」（ls-files --error-unmatch）。
   命中 ≥1 红；有目录/文件读不到黄（不判绿）；扫描 >3s 计 available=false 红。

8. 整理层新鲜度（data.curated_freshness）：<repo>/quant_backtest/data/tushare_curated/
   是 tools/refresh_curated.py 从 data/tushare/ 派生的整理层（51 张表 + 3 侧车），
   纯派生，自己不会跟着原始层变新。取整理层最旧 parquet 的 mtime 与原始层递归
   最新 parquet 的 mtime 比落后天数，另按 51 个数据集逐个比（报落后的数据集数量），
   判定取两者更差者。只看 mtime（scandir 目录项缓存，不打开文件、不读 footer，
   5s 预算）。落后 <=2 天绿 / >2 天黄 / >14 天红；整理层不存在或为空 →
   available=false 黄（不判绿）；原始层不存在/无 parquet/遍历超预算 →
   available=false 红。

用法：
    python -B tools/gen_health_card.py
    python -B tools/gen_health_card.py --vault-root C:/项目/_vault
    python -B tools/gen_health_card.py --out C:/项目/_hc_probe

退出码：0=绿/黄（可容忍），1=红（有硬问题），2=脚本自身错误（参数/写盘）。
阈值：备份 >36h 黄 / >72h 红；审计 >24h 黄 / >72h 红；dirty>0 黄；
工作树目录与 git worktree 不一致黄；worktree 已合并且 >7 天黄；
文档索引落后黄；任一块不可用红；备份落后 HEAD >0 黄 /
落后且备份 >24h 红；追平无法判定计 null（不误判绿）；数据根漂移只有一份清单黄
（不判绿）、两份都读不到红、文件数变化 >20% 或目录数变化红；
明文私钥命中 ≥1 红、有目录/文件读不到黄、扫描超 3s 红；
整理层落后 >2 天黄 / >14 天红、整理层缺失或为空黄（不判绿）、
原始层缺失或遍历超 5s 红。
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

# 数据根形态清单：备份脚本每天把它写进 <vault>/<yyyy-MM-dd>/，是「某一期」快照。
# <vault>/data_manifest.json 是 gen_data_manifest.py 的默认 --out 位置（下一次
# 默认生成就会覆盖），不是日期快照，不参与两期比对。
DATA_MANIFEST_NAME = "data_manifest.json"
# 文件数变化超过 20%（相对上一期）或顶层目录数变化 → 判红：这个幅度几乎只能是
# 事故（目录被清空/换盘/只挂上一半），而不是日常增量（日常量级是千分之几）。
DATA_DRIFT_FILE_RED_RATIO = 0.20
# detail 里最多点名几个增删目录 / 变化最大的目录。
DATA_DRIFT_DIR_LIMIT = 6
DATA_DRIFT_DIR_CHANGED_LIMIT = 3
# dates 里最多回显几个发现的日期目录。
DATA_DRIFT_DATE_LIMIT = 5

# --- 明文私钥扫描（security.plaintext_keys）---------------------------------
# 只扫限定范围，不做全盘扫：<repo>/outputs、<repo>/tools、<repo>/portal 递归，
# 加工作区根（<repo> 的父目录）一级的 *.pem / *.key / .env* 文件。
# 识别只看「文件名 + 内容特征」：不解析 PEM/DER、不回显任何文件内容。
PLAINTEXT_KEY_SCAN_REL_DIRS: tuple[Path, ...] = (
    Path("outputs"),
    Path("tools"),
    Path("portal"),
)
# 规定跳过的 4 类；另跳 python/工具链编译缓存——.pyc 是同一源码的副本，
# 不跳会把一个测试夹具按 .py/.pyc 重复计入（纯噪音，见 T9 报告边界说明）。
PLAINTEXT_KEY_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        ".worktrees",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
    }
)
# 名称命中：*.pem / *.key / id_rsa* / .env*（.env* 按需求单独点名）。
PLAINTEXT_KEY_NAME_SUFFIXES = (".pem", ".key")
# 名称命中的文件用弱特征（内容含 PRIVATE KEY 字样即可，PEM 头/主体都含）；
# 其余任意文件用强特征（必须出现 PEM 私钥起始行）。
PLAINTEXT_KEY_WEAK_MARKER = b"PRIVATE KEY"
PLAINTEXT_KEY_MARKER_RE = re.compile(
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)
# 扫描相位超时 → available=false + 红（已扫到的命中仍写进 detail）。
# 2026-10-22 从 3s 提到 12s：实测扫描 5,096 个文件 / 36 MiB 空载约 2s，
# 但整卡同时还要跑 catchup/worktree/docs，机器负载高时会越过 3s，
# 于是报出「扫描未完成」的红——那是**噪声**，不是私钥变多了。
# 判红的原因必须永远是「真发现了私钥」或「有些目录读不到」，不能是「跑太慢」。
PLAINTEXT_KEY_BUDGET_SECONDS = 12.0
# 单文件只读前 8 KiB：`-----BEGIN … PRIVATE KEY-----` 标记**一定在文件开头**
# （PEM 的头部就是第一行），读 tail 或读全文件都是浪费。2026-10-22 从 512 KiB
# 降到 8 KiB：同样 5,096 个文件的扫描从 >12s 降到 2s 以内，判据强度不变。
# 超过 8 KiB 的文件仍计入 oversized_skipped，detail 会明说"有内容没看到"。
PLAINTEXT_KEY_MAX_CONTENT_BYTES = 8 * 1024
# detail 里最多点名几条命中（其余折叠成计数，避免一行几十个路径）。
PLAINTEXT_KEY_DETAIL_LIMIT = 8
# git 跟踪判定：每条 ls-files 单独限时，整体再封一个 1.5s 顶（命中通常个位数）。
PLAINTEXT_KEY_GIT_TIMEOUT_SECONDS = 2.0
PLAINTEXT_KEY_GIT_BUDGET_SECONDS = 1.5
# 测试夹具识别：只写进 JSON 的 findings[].test_fixture 做 triage，
# 不改变判定（命中 ≥1 仍判红），避免「看着像夹具」成为漏判借口。
PLAINTEXT_KEY_FIXTURE_NAME_PREFIXES = ("test_",)
PLAINTEXT_KEY_FIXTURE_NAME_SUFFIXES = ("_test.py", "_selftest.py", "_test.ps1")
PLAINTEXT_KEY_FIXTURE_DIRS = frozenset({"tests", "test", "fixtures"})

# --- 整理层新鲜度（data.curated_freshness）-----------------------------------
# 两层结构：<RAW_REL> 是写入管线每天写的原始层（228,930 个 parquet），
# <CURATED_REL> 是 tools/refresh_curated.py（merge_parquet.py）产出的整理层
# （51 张表 + 3 个侧车，7.18 GiB，全量约 52 分钟）。整理层是**纯派生**的，
# 自己不会跟着原始层变新——没有这个检查，它一年前建的看起来也"很正常"。
CURATED_REL = Path("quant_backtest") / "data" / "tushare_curated"
RAW_REL = Path("quant_backtest") / "data" / "tushare"
# 落后 ≤2 天绿（跨一个周末不喊狼来了）；>2 天黄（该重建）；>14 天红
# （两周没重建，拿整理层得出的结论已经不可信）。
CURATED_YELLOW_LAG_DAYS = 2.0
CURATED_RED_LAG_DAYS = 14.0
# 实测：228,930 个文件名的 scandir 遍历 0.6–0.9s（只吃目录项缓存里的 mtime，
# 从不打开 parquet、不读 footer）。5s 上限是给冷缓存/杀软抖动的余量，
# 超了按不可判定处理，绝不拖死健康卡。
CURATED_SCAN_BUDGET_SECONDS = 5.0
CURATED_SCAN_DEADLINE_CHECK_EVERY = 4096
CURATED_DETAIL_DATASET_LIMIT = 5


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
# 2c) 数据根漂移（两期 data_manifest.json 对比）
# ---------------------------------------------------------------------------
def _delta_or_none(latest: Any, previous: Any) -> int | None:
    """新值 - 旧值；任一侧不是整数就返回 None（不拿 0 冒充差值为 0）。"""
    if not isinstance(latest, int) or not isinstance(previous, int):
        return None
    return latest - previous


def _norm_path_str(value: Any) -> str | None:
    """规范化路径比较口径：统一斜杠、去尾斜杠、忽略大小写；非字符串返回 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("\\", "/").rstrip("/")
    return text.casefold() or None


def _pct_text(delta: int | None, previous: int | None) -> str | None:
    if delta is None or not isinstance(previous, int) or previous == 0:
        return None
    return "%+.2f%%" % (delta / previous * 100.0)


def _signed_bytes(value: int | None) -> str:
    if value is None:
        return "—"
    if value == 0:
        return "0 B"
    return ("+" if value > 0 else "-") + _human_bytes(abs(value))


def _manifest_dir_entry(entry: Any) -> tuple[str, dict[str, Any]] | None:
    """归一化 dirs[] 的一条；name 缺失/不是字符串返回 None（由调用方计为读不懂）。

    字段名容忍两种口径：files（gen_data_manifest.py 实际产物）/ file_count
    （任务书里的叫法），避免上游改名直接把整项判成不可读。
    """
    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    files = _as_int(entry.get("files"))
    if files is None:
        files = _as_int(entry.get("file_count"))
    return name.strip(), {
        "files": files,
        "bytes": _as_int(entry.get("bytes")),
        # scanned=false 只在扫描撞时间预算时出现（该目录文件数/字节为 null）。
        "scanned": entry.get("scanned") is not False,
    }


def _load_data_manifest(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """读一份 data_manifest.json 并归一化成可比对的最小结构。

    结构不认识就返回 None + 原因（调用方判红），绝不把读不懂的清单当「一致」。
    """
    payload, error = _read_json(path)
    if payload is None:
        return None, error

    raw_dirs = payload.get("dirs")
    if not isinstance(raw_dirs, list):
        return None, f"不是 data_manifest（缺 dirs 数组）: {path}"

    dirs: dict[str, dict[str, Any]] = {}
    unparsed = 0
    for entry in raw_dirs:
        normalized = _manifest_dir_entry(entry)
        if normalized is None:
            unparsed += 1
            continue
        name, info = normalized
        dirs.setdefault(name, info)

    scan = payload.get("scan") if isinstance(payload.get("scan"), dict) else {}
    root_files = scan.get("root_files") if isinstance(scan.get("root_files"), dict) else {}

    total_files = _as_int(payload.get("total_files"))
    total_bytes = _as_int(payload.get("total_bytes"))
    totals_source = "declared"
    if total_files is None or total_bytes is None:
        # 兼容只给 dirs 的旧口径：能加就加；有条目未扫到（files/bytes 为 null）
        # 就加不出总量 —— 直接判不可读，不做「部分之和冒充总量」。
        part_files = [info["files"] for info in dirs.values()]
        part_bytes = [info["bytes"] for info in dirs.values()]
        if any(not isinstance(value, int) for value in part_files + part_bytes):
            return None, f"清单缺总量字段，且 dirs 有条目未扫到，总量不可用: {path}"
        if total_files is None:
            total_files = sum(part_files) + (_as_int(root_files.get("files")) or 0)
            totals_source = "derived"
        if total_bytes is None:
            total_bytes = sum(part_bytes) + (_as_int(root_files.get("bytes")) or 0)
            totals_source = "derived"

    raw_catalog = payload.get("catalog") if isinstance(payload.get("catalog"), dict) else {}
    catalog_declared = isinstance(payload.get("catalog"), dict)
    sha = raw_catalog.get("sha256")
    if not isinstance(sha, str) or not SHA_RE.fullmatch(sha.strip()):
        sha = None
    present: bool | None
    if isinstance(raw_catalog.get("present"), bool):
        present = bool(raw_catalog.get("present"))
    elif catalog_declared:
        present = True
    else:
        # 整段 catalog 缺失：不是「catalog.json 不存在」，是「清单没记」。
        present = None

    partial = bool(scan.get("partial")) or unparsed > 0
    if any(info["scanned"] is False for info in dirs.values()):
        partial = True

    return {
        "path": str(path),
        "generated_at": payload.get("generated_at"),
        "schema": payload.get("schema"),
        "root": payload.get("root"),
        "total_files": total_files,
        "total_bytes": total_bytes,
        "totals_source": totals_source,
        "dir_count": len(dirs),
        "dirs": dirs,
        "dirs_unparsed": unparsed,
        "partial": partial,
        "catalog": {
            "present": present,
            "size": _as_int(raw_catalog.get("size")),
            "sha256": sha.strip().lower() if sha else None,
        },
    }, None


def _discover_data_manifests(
    vault_root: Path,
) -> tuple[list[dict[str, Any]], int, str | None]:
    """按日期目录名倒序返回含 data_manifest.json 的期次（新→旧）。

    只认 <vault>/<yyyy-MM-dd>/data_manifest.json。返回 (期次列表, 扫过的日期目录数, 错误)。
    """
    try:
        if not vault_root.is_dir():
            return [], 0, f"备份根不存在或不是目录: {vault_root}"
        date_dirs = sorted(
            (
                entry
                for entry in vault_root.iterdir()
                if entry.is_dir() and _valid_date_dir(entry.name)
            ),
            key=lambda entry: entry.name,
            reverse=True,
        )
    except OSError as exc:
        return [], 0, f"读取备份根失败: {_short_exc(exc)}"

    found: list[dict[str, Any]] = []
    for entry in date_dirs:
        manifest = entry / DATA_MANIFEST_NAME
        if manifest.is_file():
            found.append({"date": entry.name, "path": manifest})
    return found, len(date_dirs), None


def _empty_data_root_drift(vault_root: Path) -> dict[str, Any]:
    return {
        "available": False,
        "reason": None,
        "vault_root": str(vault_root),
        "manifest_name": DATA_MANIFEST_NAME,
        "date_dirs": None,
        "manifests_found": None,
        "dates": [],
        "latest_date": None,
        "previous_date": None,
        "manifests": [],
        "counts": {
            "latest": {"files": None, "bytes": None, "dirs": None},
            "previous": {"files": None, "bytes": None, "dirs": None},
            "delta": {"files": None, "bytes": None, "dirs": None},
        },
        "file_delta_ratio": None,
        "dirs_added": [],
        "dirs_removed": [],
        "dirs_changed": [],
        "roots": {"latest": None, "previous": None},
        "root_changed": None,
        "catalog": {
            "latest": {"present": None, "size": None, "sha256": None},
            "previous": {"present": None, "size": None, "sha256": None},
            "changed": None,
            "status": None,
        },
        "triggers": [],
        "level": None,
        "status": None,
        "thresholds": {"file_red_ratio": DATA_DRIFT_FILE_RED_RATIO},
    }


def _catalog_change(latest: dict[str, Any], previous: dict[str, Any]) -> str:
    """catalog.json 的两期关系：same / changed / absent_both / unknown。"""
    latest_sha, previous_sha = latest.get("sha256"), previous.get("sha256")
    if latest_sha and previous_sha:
        return "same" if latest_sha == previous_sha else "changed"
    latest_present, previous_present = latest.get("present"), previous.get("present")
    if latest_present is False and previous_present is False:
        return "absent_both"
    if latest_present is not None and previous_present is not None:
        return "changed" if latest_present != previous_present else "unknown"
    return "unknown"


def collect_data_root_drift(vault_root: Path) -> dict[str, Any]:
    """比对 _vault 下最近两份 <date>/data_manifest.json，判断数据根是否漂移。

    判不了就说判不了：只有一份清单、扫描未完成（scan.partial）、清单读不到，
    一律走 available=false 且计数置 null，绝不把「没得比」当「一致」。
    """
    result = _empty_data_root_drift(vault_root)
    found, date_dirs, discover_error = _discover_data_manifests(vault_root)
    result["date_dirs"] = date_dirs
    result["manifests_found"] = len(found)
    result["dates"] = [item["date"] for item in found[:DATA_DRIFT_DATE_LIMIT]]

    if discover_error:
        result["reason"] = discover_error
        result["status"] = "unreadable"
        result["level"] = "red"
        return result
    if not found:
        result["reason"] = (
            "未找到任何 %s（扫过 %s 个日期目录），数据根形态没有被记录"
            % (DATA_MANIFEST_NAME, date_dirs)
        )
        result["status"] = "no_manifest"
        result["level"] = "red"
        return result

    result["latest_date"] = found[0]["date"]
    if len(found) < 2:
        result["reason"] = "只有 1 份 %s（%s），至少要两份才能比对漂移" % (
            DATA_MANIFEST_NAME,
            found[0]["date"],
        )
        result["status"] = "insufficient_history"
        result["level"] = "yellow"
        return result

    result["previous_date"] = found[1]["date"]
    loaded: list[dict[str, Any]] = []
    for item in found[:2]:
        manifest, load_error = _load_data_manifest(item["path"])
        loaded.append(
            {
                "date": item["date"],
                "path": str(item["path"]),
                "manifest": manifest,
                "error": load_error,
            }
        )
        result["manifests"].append(
            {
                "date": item["date"],
                "path": str(item["path"]),
                "readable": manifest is not None,
                "error": load_error,
                "generated_at": manifest["generated_at"] if manifest else None,
            }
        )

    unreadable = [
        "%s（%s）" % (item["date"], item["error"] or "原因未知")
        for item in loaded
        if item["manifest"] is None
    ]
    if unreadable:
        result["status"] = "unreadable"
        result["level"] = "red"
        scope = (
            "最近两份清单都读不到/解析失败"
            if len(unreadable) == len(loaded)
            else "有一份清单读不到，两期无法比对"
        )
        result["reason"] = scope + "：" + "；".join(unreadable)
        return result

    latest: dict[str, Any] = loaded[0]["manifest"]  # type: ignore[assignment]
    previous: dict[str, Any] = loaded[1]["manifest"]  # type: ignore[assignment]

    incomplete = []
    for item in loaded:
        manifest = item["manifest"]
        if manifest["partial"]:
            detail = "scan.partial=true" if manifest["dirs_unparsed"] == 0 else "有目录条目读不懂"
            incomplete.append("%s（%s）" % (item["date"], detail))
    if incomplete:
        result["status"] = "incomplete_scan"
        result["level"] = "yellow"
        result["reason"] = (
            "清单记录的扫描不完整：" + "、".join(incomplete) + "，总量与目录集合不可比"
        )
        return result

    latest_dirs: dict[str, dict[str, Any]] = latest["dirs"]
    previous_dirs: dict[str, dict[str, Any]] = previous["dirs"]
    added = sorted(set(latest_dirs) - set(previous_dirs))
    removed = sorted(set(previous_dirs) - set(latest_dirs))

    changed_dirs: list[dict[str, Any]] = []
    for name in sorted(set(latest_dirs) & set(previous_dirs)):
        new_info = latest_dirs[name]
        old_info = previous_dirs[name]
        if new_info["files"] == old_info["files"] and new_info["bytes"] == old_info["bytes"]:
            continue
        changed_dirs.append(
            {
                "name": name,
                "files": {"previous": old_info["files"], "latest": new_info["files"]},
                "bytes_delta": _delta_or_none(new_info["bytes"], old_info["bytes"]),
            }
        )

    def _magnitude(item: dict[str, Any]) -> int:
        files = item.get("files") or {}
        delta = _delta_or_none(files.get("latest"), files.get("previous"))
        if delta is None:
            delta = item.get("bytes_delta") if isinstance(item.get("bytes_delta"), int) else None
        return -1 if delta is None else abs(delta)

    changed_dirs.sort(key=_magnitude, reverse=True)

    latest_files, latest_bytes = latest["total_files"], latest["total_bytes"]
    previous_files, previous_bytes = previous["total_files"], previous["total_bytes"]
    latest_dir_count, previous_dir_count = latest["dir_count"], previous["dir_count"]

    result["counts"] = {
        "latest": {"files": latest_files, "bytes": latest_bytes, "dirs": latest_dir_count},
        "previous": {
            "files": previous_files,
            "bytes": previous_bytes,
            "dirs": previous_dir_count,
        },
        "delta": {
            "files": _delta_or_none(latest_files, previous_files),
            "bytes": _delta_or_none(latest_bytes, previous_bytes),
            "dirs": _delta_or_none(latest_dir_count, previous_dir_count),
        },
    }
    result["dirs_added"] = added
    result["dirs_removed"] = removed
    result["dirs_changed"] = changed_dirs[:DATA_DRIFT_DIR_LIMIT]

    catalog_status = _catalog_change(latest["catalog"], previous["catalog"])
    result["catalog"] = {
        "latest": latest["catalog"],
        "previous": previous["catalog"],
        "changed": catalog_status == "changed",
        "status": catalog_status,
    }

    latest_root, previous_root = latest["root"], previous["root"]
    result["roots"] = {"latest": latest_root, "previous": previous_root}
    root_changed = bool(
        _norm_path_str(latest_root)
        and _norm_path_str(previous_root)
        and _norm_path_str(latest_root) != _norm_path_str(previous_root)
    )
    result["root_changed"] = root_changed

    ratio: float | None = None
    file_surge = False
    if isinstance(previous_files, int) and isinstance(latest_files, int):
        if previous_files == 0:
            # 分母为 0：0 → 非 0 的变化率是无穷大，用标志位判红（不写 inf 进 JSON）。
            file_surge = latest_files != 0
        else:
            ratio = abs(latest_files - previous_files) / previous_files
            file_surge = ratio > DATA_DRIFT_FILE_RED_RATIO
    dir_count_changed = (
        isinstance(latest_dir_count, int)
        and isinstance(previous_dir_count, int)
        and latest_dir_count != previous_dir_count
    )
    result["file_delta_ratio"] = round(ratio, 6) if ratio is not None else None
    result["triggers"] = [
        name
        for name, flag in (("file_ratio", file_surge), ("dir_count", dir_count_changed))
        if flag
    ]

    changed = bool(
        latest_files != previous_files
        or latest_bytes != previous_bytes
        or latest_dir_count != previous_dir_count
        or added
        or removed
        or catalog_status == "changed"
        or root_changed
    )

    result["available"] = True
    if changed:
        major = bool(result["triggers"])
        result["status"] = "major_drift" if major else "drift"
        result["level"] = "red" if major else "yellow"
    elif catalog_status == "unknown":
        # 总量与目录集合都一致，但 catalog 指纹读不到：「一致」只能说到这一步。
        result["status"] = "catalog_unknown"
        result["level"] = "yellow"
    else:
        result["status"] = "consistent"
        result["level"] = "green"
    return result


def _catalog_drift_text(catalog: dict[str, Any]) -> str:
    status = catalog.get("status")
    latest = catalog.get("latest") or {}
    previous = catalog.get("previous") or {}
    new_sha = _short_sha(latest.get("sha256"))
    old_sha = _short_sha(previous.get("sha256"))
    if status == "same":
        return "catalog.json sha256 未变（%s）" % new_sha
    if status == "changed" and latest.get("sha256") and previous.get("sha256"):
        size_delta = _delta_or_none(latest.get("size"), previous.get("size"))
        extra = "，大小 %s 字节" % _signed_bytes(size_delta) if size_delta else ""
        return "catalog.json 已被换（%s → %s%s）" % (old_sha, new_sha, extra)
    if status == "changed" and latest.get("present") and previous.get("present") is False:
        return "catalog.json 新出现（%s），上期没有" % new_sha
    if status == "changed" and previous.get("present") and latest.get("present") is False:
        return "catalog.json 消失了（上期 %s，本期没有）" % old_sha
    if status == "changed":
        return "catalog.json 指纹变了（%s → %s）" % (old_sha, new_sha)
    if status == "absent_both":
        return "catalog.json 两期都不存在"
    return "catalog.json 指纹无法判定（清单缺 sha256）"


def _data_root_drift_detail(drift: dict[str, Any]) -> str:
    status = drift.get("status")
    if not drift.get("available"):
        tail = "；不判绿" if drift.get("level") == "yellow" else ""
        return "数据根漂移不可判定：" + (drift.get("reason") or "未知原因") + tail

    counts = drift.get("counts") or {}
    latest = counts.get("latest") or {}
    previous = counts.get("previous") or {}
    delta = counts.get("delta") or {}
    span = "%s → %s" % (drift.get("previous_date"), drift.get("latest_date"))
    catalog_text = _catalog_drift_text(drift.get("catalog") or {})
    roots = drift.get("roots") or {}
    if drift.get("root_changed"):
        root_text = "数据根路径变化：%s → %s" % (roots.get("previous"), roots.get("latest"))
    elif roots.get("latest"):
        root_text = "数据根路径未变"
    else:
        root_text = "数据根路径未记录"

    if status == "consistent":
        return "数据根无漂移：%s 文件 %s、%s、目录 %s 一致；%s；%s" % (
            span,
            _fmt_num(latest.get("files")),
            _human_bytes(latest.get("bytes")),
            _fmt_num(latest.get("dirs")),
            catalog_text,
            root_text,
        )

    pct = _pct_text(delta.get("files"), previous.get("files"))
    if delta.get("files") == 0:
        file_text = "文件 %s（未变）" % _fmt_num(latest.get("files"))
    else:
        file_text = "文件 %s → %s（%s%s）" % (
            _fmt_num(previous.get("files")),
            _fmt_num(latest.get("files")),
            _signed_int(delta.get("files")),
            "，" + pct if pct else "",
        )
    if delta.get("bytes") == 0:
        bytes_text = "字节 %s（未变）" % _human_bytes(latest.get("bytes"))
    else:
        bytes_text = "字节 %s（%s → %s）" % (
            _signed_bytes(delta.get("bytes")),
            _human_bytes(previous.get("bytes")),
            _human_bytes(latest.get("bytes")),
        )
    if delta.get("dirs") == 0:
        dir_text = "目录 %s（未变）" % _fmt_num(latest.get("dirs"))
    else:
        dir_text = "目录 %s → %s（%s）" % (
            _fmt_num(previous.get("dirs")),
            _fmt_num(latest.get("dirs")),
            _signed_int(delta.get("dirs")),
        )

    bits = ["%s %s" % (span, file_text), bytes_text, dir_text]
    added = drift.get("dirs_added") or []
    if added:
        bits.append(
            "新增目录 %s%s"
            % (
                "、".join(added[:DATA_DRIFT_DIR_LIMIT]),
                " 等 %d 个" % len(added) if len(added) > DATA_DRIFT_DIR_LIMIT else "",
            )
        )
    removed = drift.get("dirs_removed") or []
    if removed:
        bits.append(
            "删除目录 %s%s"
            % (
                "、".join(removed[:DATA_DRIFT_DIR_LIMIT]),
                " 等 %d 个" % len(removed) if len(removed) > DATA_DRIFT_DIR_LIMIT else "",
            )
        )
    changed_dirs = drift.get("dirs_changed") or []
    if changed_dirs:
        shown = []
        for item in changed_dirs[:DATA_DRIFT_DIR_CHANGED_LIMIT]:
            files = item.get("files") or {}
            shown.append(
                "%s %s→%s"
                % (item.get("name"), _fmt_num(files.get("previous")), _fmt_num(files.get("latest")))
            )
        bits.append("文件数变化最大的目录 " + "、".join(shown))
    bits.extend([catalog_text, root_text])

    if status == "major_drift":
        triggers = []
        if "file_ratio" in (drift.get("triggers") or []):
            triggers.append(
                "文件数变化 %s 超过 %g%% 阈值"
                % (pct or "上期为 0", DATA_DRIFT_FILE_RED_RATIO * 100)
            )
        if "dir_count" in (drift.get("triggers") or []):
            triggers.append(
                "顶层目录数 %s → %s"
                % (_fmt_num(previous.get("dirs")), _fmt_num(latest.get("dirs")))
            )
        return "数据根剧烈漂移（判红）：" + "；".join(bits) + "；触发：" + "、".join(triggers)
    if status == "catalog_unknown":
        return "数据根总量一致（%s 文件 %s、%s、目录 %s），但 %s；不判绿" % (
            span,
            _fmt_num(latest.get("files")),
            _human_bytes(latest.get("bytes")),
            _fmt_num(latest.get("dirs")),
            catalog_text,
        )
    return "数据根漂移（判黄）：" + "；".join(bits)


# ---------------------------------------------------------------------------
# 2d) 整理层新鲜度（data.curated_freshness）
# ---------------------------------------------------------------------------
def _empty_curated_freshness(curated_root: Path, raw_root: Path) -> dict[str, Any]:
    return {
        "available": False,
        "reason": None,
        "status": None,
        "level": None,
        "curated_root": str(curated_root),
        "raw_root": str(raw_root),
        "curated_files": None,
        "curated_oldest": None,
        "curated_newest": None,
        "raw_files": None,
        "raw_newest": None,
        "lag_days": None,
        "datasets_compared": None,
        "datasets_without_raw": None,
        "stale_datasets": None,
        "expired_datasets": None,
        "worst_dataset_lag_days": None,
        "stale_dataset_names": [],
        "scan_seconds": None,
        "timed_out": False,
        "thresholds": {
            "yellow_lag_days": CURATED_YELLOW_LAG_DAYS,
            "red_lag_days": CURATED_RED_LAG_DAYS,
            "budget_seconds": CURATED_SCAN_BUDGET_SECONDS,
        },
        "suggested_command": (
            "python -B tools/refresh_curated.py --dry-run "
            "→ python -B tools/refresh_curated.py（暂存不替换）"
        ),
    }


def _mtime_iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return _iso(dt.datetime.fromtimestamp(timestamp).astimezone())


def _scan_raw_parquet_mtimes(
    raw_root: Path, deadline: float
) -> tuple[dict[str, float], float | None, int, bool]:
    """遍历原始层，返回（每个一级目录下最新 parquet mtime, 全局最新, 文件数, 超时）。

    只 scandir + 目录项缓存里的 mtime：不打开 parquet、不读 footer、不解析行数。
    数据集键取 raw_root 下的第一级目录名（与整理层的 <数据集>.parquet 同名）。
    原始层根下散落的 parquet 只计入全局最新，不冒充数据集。
    """
    per_dataset: dict[str, float] = {}
    newest: float | None = None
    count = 0
    timed_out = False
    judged = 0
    stack: list[tuple[str, str]] = [(str(raw_root), "")]
    while stack:
        # 目录级判一次（原始层 56 个一级目录，这次 monotonic 调用可忽略），
        # 目录内部每 4096 个目录项再判一次，避免单个巨型目录拖过预算。
        if time.monotonic() > deadline:
            timed_out = True
            break
        directory, dataset = stack.pop()
        try:
            entries = os.scandir(directory)
        except OSError:
            # 单个目录读不到不判死：少算一个数据集，比整项不可判定更接近事实。
            continue
        with entries:
            for entry in entries:
                judged += 1
                if (
                    judged % CURATED_SCAN_DEADLINE_CHECK_EVERY == 0
                    and time.monotonic() > deadline
                ):
                    timed_out = True
                    break
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append((entry.path, dataset or entry.name))
                        continue
                    if not entry.name.endswith(".parquet"):
                        continue
                    stat = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                count += 1
                mtime = stat.st_mtime
                if newest is None or mtime > newest:
                    newest = mtime
                if not dataset:
                    continue
                current = per_dataset.get(dataset)
                if current is None or mtime > current:
                    per_dataset[dataset] = mtime
        if timed_out:
            break
    return per_dataset, newest, count, timed_out


def _curated_lag_days(raw_mtime: float | None, curated_mtime: float | None) -> float | None:
    """原始层最新写入 − 整理层产物时间，单位天。负数=整理层比原始层还新。"""
    if raw_mtime is None or curated_mtime is None:
        return None
    return round((raw_mtime - curated_mtime) / 86400.0, 2)


def collect_curated_freshness(curated_root: Path, raw_root: Path) -> dict[str, Any]:
    """量整理层落后原始层多少天：整理层是纯派生的，不会自己变新。

    口径（两套一起给，判定取更差的那个）：
    * 简化口径：整理层最旧 parquet mtime vs 原始层递归最新 parquet mtime；
    * 逐数据集：51 个数据集各自「本数据集整理产物 mtime vs 原始层同名目录最新」，
      报落后的数据集数量（能指出是哪几张表旧了）。
    判定：落后 ≤2 天绿；>2 天黄（该重建）；>14 天红（结论不可信）。
    整理层不存在 → available=false + 黄（不判绿）；原始层不存在/扫不出 parquet
    或遍历超预算 → available=false + 红。
    """
    result = _empty_curated_freshness(curated_root, raw_root)
    started = time.monotonic()
    deadline = started + CURATED_SCAN_BUDGET_SECONDS

    if not raw_root.is_dir():
        result["reason"] = f"原始层不存在或不是目录: {raw_root}"
        result["status"] = "raw_missing"
        result["level"] = "red"
        return result
    if not curated_root.is_dir():
        result["reason"] = (
            f"整理层不存在（{curated_root}）：它是 tools/refresh_curated.py 的派生产物，"
            "原始层不受影响，但读整理层的人手里没有新数据"
        )
        result["status"] = "missing"
        result["level"] = "yellow"
        return result

    # 整理层只有 50 来个条目，单独扫：不要为它递归，也别把侧车文件当表。
    curated_mtimes: dict[str, float] = {}
    try:
        with os.scandir(curated_root) as entries:
            for entry in entries:
                if not entry.name.endswith(".parquet"):
                    continue
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    curated_mtimes[entry.name[: -len(".parquet")]] = entry.stat(
                        follow_symlinks=False
                    ).st_mtime
                except OSError:
                    continue
    except OSError as exc:
        result["reason"] = f"读不到整理层目录: {_short_exc(exc)}"
        result["status"] = "unreadable"
        result["level"] = "yellow"
        return result

    if not curated_mtimes:
        result["reason"] = (
            f"整理层目录里没有 parquet（{curated_root}）：重建没落盘或只落了侧车"
        )
        result["status"] = "empty"
        result["level"] = "yellow"
        return result

    per_dataset, raw_newest, raw_files, timed_out = _scan_raw_parquet_mtimes(
        raw_root, deadline
    )
    result["scan_seconds"] = round(time.monotonic() - started, 3)
    if timed_out:
        result["timed_out"] = True
        result["reason"] = (
            f"遍历原始层超过 {CURATED_SCAN_BUDGET_SECONDS:g}s 预算，结果不采信"
        )
        result["status"] = "timeout"
        result["level"] = "red"
        return result
    if raw_newest is None or not raw_files:
        result["raw_files"] = raw_files
        result["reason"] = f"原始层没有任何 parquet，无从比对（{raw_root}）"
        result["status"] = "raw_empty"
        result["level"] = "red"
        return result

    curated_oldest = min(curated_mtimes.values())
    curated_newest = max(curated_mtimes.values())
    lag_days = _curated_lag_days(raw_newest, curated_oldest)

    compared: list[tuple[float, str]] = []
    without_raw: list[str] = []
    for name, mtime in curated_mtimes.items():
        raw_mtime = per_dataset.get(name)
        dataset_lag = _curated_lag_days(raw_mtime, mtime)
        if dataset_lag is None:
            without_raw.append(name)
            continue
        compared.append((dataset_lag, name))
    compared.sort(key=lambda item: item[0], reverse=True)

    stale = [(lag, name) for lag, name in compared if lag > CURATED_YELLOW_LAG_DAYS]
    expired = [(lag, name) for lag, name in compared if lag > CURATED_RED_LAG_DAYS]
    worst_dataset_lag = compared[0][0] if compared else None

    if not compared:
        # 一个同名数据集都对不上：两层结构脱节（原始层被改名/清空过）。
        # 这时简化口径可能仍然"看起来不落后"，恰恰是最危险的假绿，所以判红。
        result["curated_files"] = len(curated_mtimes)
        result["raw_files"] = raw_files
        result["datasets_compared"] = 0
        result["datasets_without_raw"] = len(without_raw)
        result["lag_days"] = lag_days
        result["reason"] = (
            "整理层 %d 个数据集在原始层里找不到任何同名目录（两层结构对不上），"
            "不能只靠总体 mtime 判绿" % len(curated_mtimes)
        )
        result["status"] = "no_matching_dataset"
        result["level"] = "red"
        return result

    # 判定取「简化口径」与「最差数据集」两者中更差的一个：简化口径能抓到
    # 「原始层新增了数据集、整理层根本没有」，逐数据集能抓到「个别表很久没重建」。
    candidates = [value for value in (lag_days, worst_dataset_lag) if value is not None]
    worst_lag = max(candidates)

    result.update(
        {
            "available": True,
            "curated_files": len(curated_mtimes),
            "curated_oldest": _mtime_iso(curated_oldest),
            "curated_newest": _mtime_iso(curated_newest),
            "raw_files": raw_files,
            "raw_newest": _mtime_iso(raw_newest),
            "lag_days": lag_days,
            "datasets_compared": len(compared),
            "datasets_without_raw": len(without_raw),
            "stale_datasets": len(stale),
            "expired_datasets": len(expired),
            "worst_dataset_lag_days": worst_dataset_lag,
            "stale_dataset_names": [name for _, name in stale][:CURATED_DETAIL_DATASET_LIMIT],
        }
    )
    if worst_lag > CURATED_RED_LAG_DAYS:
        result["status"] = "expired"
        result["level"] = "red"
    elif worst_lag > CURATED_YELLOW_LAG_DAYS:
        result["status"] = "stale"
        result["level"] = "yellow"
    else:
        result["status"] = "fresh"
        result["level"] = "green"
    return result


def _curated_lag_text(lag_days: float | None) -> str:
    if lag_days is None:
        return "—"
    if lag_days <= 0:
        return "0 天（原始层最新写入不晚于整理层，当前不落后）"
    return "%.2f 天" % lag_days


def _curated_freshness_detail(block: dict[str, Any]) -> str:
    if not block:
        return "整理层新鲜度检查未运行（data 块缺失）"
    if not block.get("available"):
        text = "整理层新鲜度不可判定：" + (block.get("reason") or "未知原因")
        if block.get("level") == "yellow":
            # 缺件不等于坏掉：判黄不判绿，并把重建命令直接给出来。
            text += "；不判绿"
            if block.get("status") in ("missing", "empty"):
                text += "；建议：" + str(block.get("suggested_command"))
        return text

    bits = [
        "整理层最旧 %s / 最新 %s（%s 个 parquet）"
        % (
            block.get("curated_oldest"),
            block.get("curated_newest"),
            _fmt_num(block.get("curated_files")),
        ),
        "原始层最新 %s（递归 %s 个 parquet）"
        % (block.get("raw_newest"), _fmt_num(block.get("raw_files"))),
        "落后 " + _curated_lag_text(block.get("lag_days")),
    ]
    compared = block.get("datasets_compared")
    stale = block.get("stale_datasets")
    if compared is not None:
        dataset_text = "逐数据集 %s/%s 落后（>%g 天）" % (
            _fmt_num(stale),
            _fmt_num(compared),
            CURATED_YELLOW_LAG_DAYS,
        )
        names = block.get("stale_dataset_names") or []
        if names:
            dataset_text += "：" + "、".join(names)
            if (stale or 0) > len(names):
                dataset_text += " 等 %d 个" % stale
        bits.append(dataset_text)
    if block.get("datasets_without_raw"):
        bits.append("原始层缺对应目录 %s 个" % _fmt_num(block.get("datasets_without_raw")))

    status = block.get("status")
    if status == "expired":
        head = "整理层已明显过期（判红，拿它得出的结论不可信）："
        tail = "；建议：%s，校验通过后由人决定是否 --replace" % block.get("suggested_command")
    elif status == "stale":
        head = "整理层落后（判黄，该重建了）："
        tail = "；建议：%s" % block.get("suggested_command")
    else:
        head = "整理层不落后："
        # 绿也给一条节奏建议：本项一旦转黄就是"该重建了"，提前把命令写在这里。
        tail = "；建议保持每周一次重建（先 python -B tools/refresh_curated.py --dry-run；见 SOP §1.5）"
    return head + "；".join(bits) + tail


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
        "dirty_expired": None,
        "unknown": None,
        "status": None,
        "level": None,
        "expired": [],
        "dirty_expired_worktrees": [],
        "unknown_worktrees": [],
        "items": [],
    }


def _worktree_has_changes(
    path_str: str, next_timeout
) -> tuple[bool | None, str | None]:
    """判断一个关联工作树是否还带着未提交改动。

    规则 9 的原文是「分支已合并**并且无未提交改动**的工作树，合并后 7 天内
    必须 remove」。只判「已合并 + 超期」会把还带着未提交工作的树报成“该回收”，
    所以超期候选必须实测脏不脏。

    返回 (是否脏, 失败原因)：无法判定时返回 (None, 原因)，**绝不冒充干净**。
    """
    raw = (path_str or "").strip().strip('"')
    if not raw:
        return None, "工作树路径为空"
    path = Path(raw)
    if not path.is_dir():
        return None, f"工作树目录不存在: {raw}"
    try:
        proc = _git_run(path, ["status", "--porcelain"], timeout=next_timeout())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"git status 失败: {_short_exc(exc)}"
    if proc.returncode != 0:
        detail = _decode(proc.stderr).strip()[:200] or "git 返回非零"
        return None, "git status 失败: " + detail
    return bool(_decode(proc.stdout).strip()), None


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
        dirty_expired: list[dict[str, Any]] = []
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
            item_dirty: bool | None = None

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
                    if age_days > WORKTREE_TTL_DAYS:
                        # 规则 9 还要求「无未提交改动」——超期候选实测脏不脏，
                        # 脏的树不能建议回收（它的改动还没进任何提交）。
                        dirty, dirty_reason = _worktree_has_changes(
                            str(entry.get("path") or ""), next_timeout
                        )
                        if dirty is None:
                            unknown_reason = dirty_reason or "无法读取工作树状态"
                            status = "unknown"
                        elif dirty:
                            status = "expired_dirty"
                            item_dirty = True
                        else:
                            status = "expired"
                    else:
                        status = "merged_fresh"
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
            if item_dirty is not None:
                item["uncommitted_changes"] = item_dirty
            if unknown_reason is not None:
                item["unknown_reason"] = unknown_reason
                unknown.append(item)
            elif status == "expired":
                expired.append(item)
            elif status == "expired_dirty":
                dirty_expired.append(item)
            if len(items) < WORKTREE_TTL_ITEM_LIMIT:
                items.append(item)

        result["worktrees_total"] = len(entries)
        result["linked_worktrees"] = len(linked)
        result["merged_worktrees"] = merged_count
        result["violations"] = len(expired)
        result["dirty_expired"] = len(dirty_expired)
        result["unknown"] = len(unknown)
        result["expired"] = expired[:WORKTREE_TTL_DETAIL_LIMIT]
        result["dirty_expired_worktrees"] = dirty_expired[:WORKTREE_TTL_DETAIL_LIMIT]
        result["unknown_worktrees"] = unknown[:WORKTREE_TTL_DETAIL_LIMIT]
        result["items"] = items
        result["available"] = True
        if expired:
            result["status"] = "expired"
            result["level"] = "yellow"
        elif dirty_expired:
            # 已合并 + 超期，但仍带着未提交改动：不能建议 remove（规则 9 的
            # 前提是「无未提交改动」），必须人先决定这些改动去哪。判黄不判绿。
            result["status"] = "expired_dirty"
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
    dirty = ttl.get("dirty_expired_worktrees") or []
    parts: list[str] = []
    if expired:
        bits = [
            "%s（%s，%.1f 天）"
            % (item["path"], item.get("branch") or "?", item.get("age_days") or 0.0)
            for item in expired
        ]
        text = "%d 个已合并且超过 %g 天未清理：%s" % (
            ttl.get("violations") or 0,
            days,
            "、".join(bits),
        )
        hidden = (ttl.get("violations") or 0) - len(bits)
        if hidden > 0:
            text += "；另有 %d 个未列出" % hidden
        parts.append(text)
    if dirty:
        # 规则 9 的前提是「无未提交改动」。这些树已合并、已超期，但还带着
        # 没进任何提交的改动，直接 remove 会丢工作 —— 必须人来决定去向。
        dbits = [
            "%s（%s，%.1f 天）"
            % (item["path"], item.get("branch") or "?", item.get("age_days") or 0.0)
            for item in dirty
        ]
        text = "%d 个已合并且超过 %g 天，但仍持有未提交改动，不可自动回收：%s" % (
            ttl.get("dirty_expired") or 0,
            days,
            "、".join(dbits),
        )
        hidden = (ttl.get("dirty_expired") or 0) - len(dbits)
        if hidden > 0:
            text += "；另有 %d 个未列出" % hidden
        parts.append(text)
    detail = "；".join(parts)
    if ttl.get("unknown"):
        detail += "；另有 %d 个无法判定" % ttl["unknown"]
    if expired:
        return detail + "（规则 9，需 git worktree remove）"
    return detail + "（规则 9 前提是「无未提交改动」：先决定这些改动的去向，再回收）"


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
# 明文私钥扫描（security.plaintext_keys）
# ---------------------------------------------------------------------------
# 只看「文件名 + 内容特征」，不解析密钥格式、不打印文件内容；命中只回显
# 「路径 + 字节数 + 是否被 git 跟踪」。判定口径在 _evaluate 里，采集侧只报事实。
def _empty_plaintext_keys(repo_root: Path) -> dict[str, Any]:
    return {
        "available": False,
        "reason": None,
        "repo_root": str(repo_root),
        "workspace_root": str(repo_root.parent),
        "scan_dirs": [str(repo_root / rel) for rel in PLAINTEXT_KEY_SCAN_REL_DIRS],
        "missing_dirs": [],
        "scanned_files": 0,
        "scanned_bytes": 0,
        "oversized_skipped": 0,
        "findings": [],
        "skipped_dirs": [],
        "unreadable_files": [],
        "git_checked": False,
        "budget_seconds": PLAINTEXT_KEY_BUDGET_SECONDS,
        "max_content_bytes": PLAINTEXT_KEY_MAX_CONTENT_BYTES,
        "elapsed_ms": None,
        "over_budget": None,
    }


def _plaintext_key_display_path(path: Path, repo_root: Path) -> str:
    """命中路径：仓内给相对 POSIX 路径，仓外（工作区根）给绝对路径。"""
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return str(path)


def _plaintext_key_name_rule(name: str) -> str | None:
    """按文件名判定「弱特征」候选：*.pem / *.key、id_rsa*、.env*。"""
    lower = name.lower()
    if lower.endswith(PLAINTEXT_KEY_NAME_SUFFIXES):
        return "name"
    if lower.startswith(".env"):
        return "env"
    if lower.startswith("id_rsa"):
        return "id_rsa"
    return None


def _looks_like_test_fixture(path: Path) -> bool:
    """triage 用：test_* / *_test.py / _selftest.py 或 tests/fixtures 目录下的命中。"""
    name = path.name.lower()
    if any(name.startswith(prefix) for prefix in PLAINTEXT_KEY_FIXTURE_NAME_PREFIXES):
        return True
    if any(name.endswith(suffix) for suffix in PLAINTEXT_KEY_FIXTURE_NAME_SUFFIXES):
        return True
    return any(
        parent.name.lower() in PLAINTEXT_KEY_FIXTURE_DIRS
        for parent in list(path.parents)[:4]
    )


def _scan_for_plaintext_keys(repo_root: Path, result: dict[str, Any]) -> None:
    """限定范围扫描：只累计计数/命中/跳过项，判定留给 _evaluate。"""
    deadline = time.monotonic() + PLAINTEXT_KEY_BUDGET_SECONDS
    findings: list[dict[str, Any]] = result["findings"]
    skipped_dirs: list[str] = result["skipped_dirs"]
    unreadable_files: list[str] = result["unreadable_files"]
    timeout_reason = "扫描超时（>%gs，已扫到的命中仍列在 detail）" % (
        PLAINTEXT_KEY_BUDGET_SECONDS
    )

    def _mark_timeout() -> bool:
        if time.monotonic() <= deadline:
            return False
        result["available"] = False
        result["reason"] = timeout_reason
        return True

    def _display(path: Path) -> str:
        return _plaintext_key_display_path(path, repo_root)

    def _record(path: Path, size: int, rule: str) -> None:
        findings.append(
            {
                "path": _display(path),
                "bytes": size,
                # 只报「是否被 git 跟踪」；内容一个字节都不落进 JSON。
                "in_git": None,
                "rule": rule,
                "test_fixture": _looks_like_test_fixture(path),
            }
        )

    def _inspect(path: Path, size: int) -> None:
        rule = _plaintext_key_name_rule(path.name)
        if rule is None and size > PLAINTEXT_KEY_MAX_CONTENT_BYTES:
            result["oversized_skipped"] += 1
            return
        try:
            with path.open("rb") as handle:
                content = handle.read(PLAINTEXT_KEY_MAX_CONTENT_BYTES + 1)
        except OSError:
            unreadable_files.append(_display(path))
            return
        result["scanned_files"] += 1
        result["scanned_bytes"] += len(content)
        if rule is not None:
            # 名称命中：内容含 PRIVATE KEY 字样即算（真 PEM 的头和主体都含）。
            if PLAINTEXT_KEY_WEAK_MARKER in content:
                _record(path, size, rule)
            return
        if PLAINTEXT_KEY_MARKER_RE.search(content):
            _record(path, size, "marker")

    def _walk(root: Path) -> bool:
        """递归扫一个限定目录；False = 已超时中止。"""
        stack = [root]
        while stack:
            current = stack.pop()
            if _mark_timeout():
                return False
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if _mark_timeout():
                            return False
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if entry.name in PLAINTEXT_KEY_SKIP_DIRS:
                                    continue
                                stack.append(Path(entry.path))
                            elif entry.is_file(follow_symlinks=False):
                                _inspect(
                                    Path(entry.path),
                                    entry.stat(follow_symlinks=False).st_size,
                                )
                        except OSError:
                            # 单个条目 stat 失败：记文件级不可读，不中断整目录
                            unreadable_files.append(_display(Path(entry.path)))
            except OSError:
                # 目录读不到（ACL 拒绝等）：记下来，最后判黄而不是判绿
                skipped_dirs.append(_display(current))
        return True

    for rel_dir in PLAINTEXT_KEY_SCAN_REL_DIRS:
        root = repo_root / rel_dir
        try:
            exists = root.is_dir()
        except OSError:
            exists = False
        if not exists:
            result["missing_dirs"].append(_display(root))
            continue
        if not _walk(root):
            return

    # 工作区根一级：只看候选名（*.pem/*.key/id_rsa*/.env*），不递归、不读其它文件。
    workspace = repo_root.parent
    if workspace == repo_root:
        return
    try:
        with os.scandir(workspace) as entries:
            for entry in entries:
                if _mark_timeout():
                    return
                if _plaintext_key_name_rule(entry.name) is None:
                    continue
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    _inspect(Path(entry.path), entry.stat(follow_symlinks=False).st_size)
                except OSError:
                    unreadable_files.append(_display(Path(entry.path)))
    except OSError:
        skipped_dirs.append(_display(workspace))


def _fill_git_state(repo_root: Path, findings: list[dict[str, Any]]) -> bool:
    """给命中补 in_git（True/False/None）；只读 git，失败一律留 None=未知。"""
    if not findings:
        return False
    deadline = time.monotonic() + PLAINTEXT_KEY_GIT_BUDGET_SECONDS
    try:
        probe = _git_run(
            repo_root,
            ["rev-parse", "--is-inside-work-tree"],
            timeout=PLAINTEXT_KEY_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if probe.returncode != 0 or _decode(probe.stdout).strip() != "true":
        return False
    checked = False
    for finding in findings:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        rel = str(finding.get("path") or "")
        if not rel or Path(rel).is_absolute():
            continue  # 仓外文件不做 git 判定，保持 None
        try:
            proc = _git_run(
                repo_root,
                ["ls-files", "--error-unmatch", "--", rel],
                timeout=max(0.2, min(PLAINTEXT_KEY_GIT_TIMEOUT_SECONDS, remaining)),
            )
        except (OSError, subprocess.SubprocessError):
            continue
        checked = True
        if proc.returncode == 0:
            finding["in_git"] = True
        elif proc.returncode == 1:
            finding["in_git"] = False
        # 其它退出码（非仓库/异常）保持 None，不冒充「未跟踪」
    return checked


def collect_plaintext_keys(repo_root: Path) -> dict[str, Any]:
    """明文私钥采集：范围/识别/性能上限见模块 docstring 第 7 条。"""
    started = time.monotonic()
    result = _empty_plaintext_keys(repo_root)
    try:
        if not repo_root.is_dir():
            result["reason"] = f"仓库根不存在或不是目录: {repo_root}"
            return result
    except OSError as exc:
        result["reason"] = "读取仓库根失败: " + _short_exc(exc)
        return result
    result["available"] = True
    try:
        _scan_for_plaintext_keys(repo_root, result)
    except Exception as exc:  # 兜底：体检项不许把整张卡带崩，读不到就判不可用（红）
        result["available"] = False
        result["reason"] = "扫描异常: " + _short_exc(exc)
    result["git_checked"] = _fill_git_state(repo_root, result["findings"])
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000.0, 1)
    result["over_budget"] = result["elapsed_ms"] > PLAINTEXT_KEY_BUDGET_SECONDS * 1000.0
    return result


def _git_state_text(in_git: Any) -> str:
    if in_git is True:
        return "已跟踪"
    if in_git is False:
        return "未跟踪"
    return "未知"


def _plaintext_keys_detail(keys: dict[str, Any]) -> str:
    """detail 只写路径 / 字节数 / git 状态；绝不写文件内容。"""
    findings = keys.get("findings") or []
    skipped_dirs = keys.get("skipped_dirs") or []
    unreadable_files = keys.get("unreadable_files") or []
    bits: list[str] = []
    if not keys.get("available"):
        bits.append("明文私钥扫描未完成：" + (keys.get("reason") or "未知原因"))
    if findings:
        shown = findings[:PLAINTEXT_KEY_DETAIL_LIMIT]
        hidden = len(findings) - len(shown)
        items = "、".join(
            "%s（%s，git:%s）"
            % (item["path"], _human_bytes(item["bytes"]), _git_state_text(item.get("in_git")))
            for item in shown
        )
        bits.append(
            "发现 %d 个明文私钥%s：%s"
            % (len(findings), "（另有 %d 个未列出）" % hidden if hidden > 0 else "", items)
        )
    elif not keys.get("available"):
        bits.append(
            "已扫 %d 个文件（%s），扫描不完整，不能判定无明文私钥"
            % (
                keys.get("scanned_files") or 0,
                _human_bytes(keys.get("scanned_bytes") or 0),
            )
        )
    else:
        bits.append(
            "已扫 %d 个文件（%s），未见明文私钥"
            % (
                keys.get("scanned_files") or 0,
                _human_bytes(keys.get("scanned_bytes") or 0),
            )
        )
    if skipped_dirs:
        bits.append(
            "%d 个目录读不到：%s" % (len(skipped_dirs), "、".join(skipped_dirs[:3]))
        )
    if unreadable_files:
        bits.append(
            "%d 个文件读不到：%s"
            % (len(unreadable_files), "、".join(unreadable_files[:3]))
        )
    if skipped_dirs or unreadable_files:
        bits.append("有内容没看到，不判绿")
    if keys.get("oversized_skipped"):
        bits.append(
            "%d 个 >%s 的文件未读内容"
            % (
                keys["oversized_skipped"],
                _human_bytes(PLAINTEXT_KEY_MAX_CONTENT_BYTES),
            )
        )
    return "；".join(bits)


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

    data_drift = (card.get("data") or {}).get("root_drift") or {}
    if not data_drift:
        add("data.root_drift", "red", "数据根漂移检查未运行（data 块缺失）")
    else:
        # level 由采集侧给全（绿/黄/红）；缺失按红兜底，绝不冒充绿。
        drift_level = data_drift.get("level")
        add(
            "data.root_drift",
            drift_level if drift_level in ("green", "yellow", "red") else "red",
            _data_root_drift_detail(data_drift),
        )

    curated = (card.get("data") or {}).get("curated_freshness") or {}
    if not curated:
        add("data.curated_freshness", "red", "整理层新鲜度检查未运行（data 块缺失）")
    else:
        # 同上：等级由采集侧给全；采集侧没给等级就是不可判定，按红兜底。
        curated_level = curated.get("level")
        add(
            "data.curated_freshness",
            curated_level if curated_level in ("green", "yellow", "red") else "red",
            _curated_freshness_detail(curated),
        )

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
    elif ttl.get("dirty_expired"):
        # 超期但仍持有未提交改动：判黄（不是绿，规则 9 没满足；也不是红，
        # 因为没有东西坏掉）。detail 明确写"不可自动回收"，避免照抄 remove。
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

    keys = (card.get("security") or {}).get("plaintext_keys") or {}
    if not keys:
        add("security.plaintext_keys", "red", "明文私钥扫描未运行（security 块缺失）")
    else:
        if not keys.get("available") or keys.get("findings"):
            # 扫描没跑完（超时/异常）或命中 ≥1：都判红，绝不当绿。
            key_level = "red"
        elif keys.get("skipped_dirs") or keys.get("unreadable_files"):
            # 有目录/文件读不到：等于没看全，不判绿。
            key_level = "yellow"
        else:
            key_level = "green"
        add("security.plaintext_keys", key_level, _plaintext_keys_detail(keys))

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
    curated_root: Path | None = None,
    raw_root: Path | None = None,
) -> dict[str, Any]:
    semantics, semantics_error = _load_audit_semantics(repo_root)
    backup = collect_backup(vault_root, now)
    backup["catchup"] = collect_catchup(repo_root, backup, now)
    repo_block = collect_repo(repo_root, now)
    repo_block["worktree_ttl"] = collect_worktree_ttl(repo_root, now)
    curated_path = curated_root if curated_root is not None else repo_root / CURATED_REL
    raw_path = raw_root if raw_root is not None else repo_root / RAW_REL
    card: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": _iso(now),
        "status": "green",
        "exit_code": 0,
        "reasons": [],
        "checks": [],
        "task_audit": collect_task_audit(audit_root, now, semantics, semantics_error),
        "backup": backup,
        "data": {
            "root_drift": collect_data_root_drift(vault_root),
            "curated_freshness": collect_curated_freshness(curated_path, raw_path),
        },
        "repo": repo_block,
        "docs": {"index_freshness": collect_docs_index(repo_root)},
        "security": {"plaintext_keys": collect_plaintext_keys(repo_root)},
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
            "数据根漂移：按日期目录名倒序取 _vault 下最新两份 <date>/data_manifest.json，"
            "比对总文件数/总字节/顶层目录集合/catalog.json sha256/数据根路径；"
            f"文件数变化 >{DATA_DRIFT_FILE_RED_RATIO * 100:g}% 或顶层目录数变化判红；"
            "只有一份清单、清单读不到、扫描未完成（scan.partial）都计 available=false"
            "（计数 null，不判绿；前两种除「只有一份」外均判红）。",
            "<vault>/data_manifest.json（gen_data_manifest.py 的默认 --out）不是日期快照，"
            "不参与比对，避免拿「实时文件」冒充历史。",
            "明文私钥扫描（security.plaintext_keys）：只扫 <repo>/outputs、<repo>/tools、"
            "<repo>/portal 递归 + 工作区根一级 *.pem/*.key/id_rsa*/.env*；"
            "跳过 .git/node_modules/.venv/.worktrees（另跳 __pycache__ 等编译缓存，"
            "避免 .pyc 把同一测试夹具重复计入）。只看文件名与内容特征，不解析密钥、"
            "不回显任何文件内容：命中只报「路径 + 字节数 + 是否被 git 跟踪」。",
            "明文私钥判定：命中 ≥1 红；扫描范围内有目录/文件读不到黄（不判绿）；"
            "扫描相位 >%gs 计 available=false 并判红（已扫到的命中仍列出）；"
            "单文件最多读 %s，更大的文件记 oversized_skipped 不读内容。"
            % (PLAINTEXT_KEY_BUDGET_SECONDS, _human_bytes(PLAINTEXT_KEY_MAX_CONTENT_BYTES)),
            "整理层新鲜度（data.curated_freshness）：整理层是纯派生产物，不会跟着原始层变新。"
            "口径=整理层最旧 parquet mtime vs 原始层递归最新 parquet mtime，另按 51 个数据集"
            "逐个比一遍（报落后的数据集数量），判定取两者中更差的一个；只看 mtime，"
            "不打开 parquet、不读 footer。"
            f"落后 <={CURATED_YELLOW_LAG_DAYS:g} 天绿 / >{CURATED_YELLOW_LAG_DAYS:g} 天黄 / "
            f">{CURATED_RED_LAG_DAYS:g} 天红（结论不可信）。",
            "整理层不可判定：整理层不存在/为空 → available=false + 黄（不判绿，计数 null）；"
            "原始层不存在、原始层没有 parquet、或遍历超 "
            f"{CURATED_SCAN_BUDGET_SECONDS:g}s 预算 → available=false + 红。"
            "重建命令见 tools/refresh_curated.py（--replace 会永久删除旧整理层，须先校验通过）。",
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


def _signed_int(value: Any) -> str:
    if not isinstance(value, int):
        return "—"
    return ("+%d" % value) if value > 0 else str(value)


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

    drift = (card.get("data") or {}).get("root_drift") or {}
    parts.append('<section class="block">')
    parts.append("<h2>数据根漂移</h2>")
    parts.append(f'<div class="sub">{_e(drift.get("vault_root"))}</div>')
    if drift.get("available"):
        counts = drift.get("counts") or {}
        latest_counts = counts.get("latest") or {}
        previous_counts = counts.get("previous") or {}
        delta = counts.get("delta") or {}
        verdicts = {
            "consistent": "无漂移",
            "drift": "漂移（黄）",
            "major_drift": "剧烈漂移（红）",
            "catalog_unknown": "总量一致，catalog 指纹不可判定",
        }
        parts.extend(
            [
                _kv(
                    "对比期",
                    "%s → %s" % (drift.get("previous_date"), drift.get("latest_date")),
                ),
                _kv(
                    "文件数",
                    "%s → %s（%s）"
                    % (
                        _fmt_num(previous_counts.get("files")),
                        _fmt_num(latest_counts.get("files")),
                        _signed_int(delta.get("files")),
                    ),
                ),
                _kv(
                    "总字节",
                    "%s → %s（%s）"
                    % (
                        _human_bytes(previous_counts.get("bytes")),
                        _human_bytes(latest_counts.get("bytes")),
                        _signed_bytes(delta.get("bytes")),
                    ),
                ),
                _kv(
                    "目录数",
                    "%s → %s（%s）"
                    % (
                        _fmt_num(previous_counts.get("dirs")),
                        _fmt_num(latest_counts.get("dirs")),
                        _signed_int(delta.get("dirs")),
                    ),
                ),
                _kv("catalog.json", _catalog_drift_text(drift.get("catalog") or {})),
                _kv("判定", verdicts.get(str(drift.get("status")), str(drift.get("status")))),
            ]
        )
        added = drift.get("dirs_added") or []
        removed = drift.get("dirs_removed") or []
        if added or removed:
            parts.append(
                '<p class="note">新增目录：%s；删除目录：%s</p>'
                % (_e("、".join(added) or "—"), _e("、".join(removed) or "—"))
            )
    else:
        parts.append(f'<p class="unavailable">不可判定：{_e(drift.get("reason"))}</p>')
        if drift.get("dates"):
            parts.append(
                '<p class="note">已发现的清单期次：%s（需要连续两份才能比对）</p>'
                % _e("、".join(str(item) for item in drift["dates"]))
            )
    parts.append("</section>")

    curated = (card.get("data") or {}).get("curated_freshness") or {}
    parts.append('<section class="block">')
    parts.append("<h2>整理层新鲜度</h2>")
    parts.append(f'<div class="sub">{_e(curated.get("curated_root"))}</div>')
    if curated.get("available"):
        verdicts = {
            "fresh": "不落后",
            "stale": "该重建了（黄）",
            "expired": "已过期（红）",
        }
        parts.extend(
            [
                _kv("整理层最旧", curated.get("curated_oldest")),
                _kv("整理层最新", curated.get("curated_newest")),
                _kv("原始层最新", curated.get("raw_newest")),
                _kv("落后", _curated_lag_text(curated.get("lag_days"))),
                _kv(
                    "落后数据集",
                    "%s / %s（阈值 %g 天）"
                    % (
                        _fmt_num(curated.get("stale_datasets")),
                        _fmt_num(curated.get("datasets_compared")),
                        CURATED_YELLOW_LAG_DAYS,
                    ),
                ),
                _kv(
                    "判定",
                    verdicts.get(str(curated.get("status")), str(curated.get("status"))),
                ),
            ]
        )
        names = curated.get("stale_dataset_names") or []
        if names:
            parts.append('<p class="note">落后的数据集：%s</p>' % _e("、".join(names)))
    else:
        parts.append(f'<p class="unavailable">不可判定：{_e(curated.get("reason"))}</p>')
        parts.append(
            '<p class="note">建议：%s</p>' % _e(curated.get("suggested_command"))
        )
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

    keys = (card.get("security") or {}).get("plaintext_keys") or {}
    parts.append('<section class="block">')
    parts.append("<h2>明文私钥扫描</h2>")
    parts.append(
        '<div class="sub">%s（+ 工作区根一级 *.pem/*.key/id_rsa*/.env*）</div>'
        % _e("、".join(keys.get("scan_dirs") or []))
    )
    if keys.get("available") is False:
        parts.append(f'<p class="unavailable">不可用：{_e(keys.get("reason"))}</p>')
    else:
        parts.append(_kv("命中", "%d 个明文私钥" % len(keys.get("findings") or [])))
        for item in (keys.get("findings") or [])[:PLAINTEXT_KEY_DETAIL_LIMIT]:
            parts.append(
                '<p class="note">%s（%s，git:%s）</p>'
                % (
                    _e(item.get("path")),
                    _e(_human_bytes(item.get("bytes"))),
                    _e(_git_state_text(item.get("in_git"))),
                )
            )
        parts.append(
            _kv(
                "已扫文件 / 字节",
                "%s / %s"
                % (
                    _fmt_num(keys.get("scanned_files")),
                    _human_bytes(keys.get("scanned_bytes")),
                ),
            )
        )
        if keys.get("elapsed_ms") is not None:
            parts.append(
                _kv(
                    "耗时 / 预算",
                    "%sms / %ss"
                    % (keys.get("elapsed_ms"), keys.get("budget_seconds")),
                )
            )
    if keys.get("skipped_dirs"):
        parts.append(
            '<p class="note">读不到的目录：%s</p>'
            % _e("、".join(keys["skipped_dirs"][:5]))
        )
    if keys.get("unreadable_files"):
        parts.append(
            '<p class="note">读不到的文件：%s</p>'
            % _e("、".join(keys["unreadable_files"][:5]))
        )
    parts.append(
        '<p class="note">只报路径 / 字节数 / git 状态，不回显任何文件内容。</p>'
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
            f"整理层落后 >{CURATED_YELLOW_LAG_DAYS:g} 天黄 / >{CURATED_RED_LAG_DAYS:g} 天红。"
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
        "--curated-root",
        metavar="PATH",
        help="整理层目录（默认 <repo>/quant_backtest/data/tushare_curated）",
    )
    parser.add_argument(
        "--raw-root",
        metavar="PATH",
        help="原始层目录（默认 <repo>/quant_backtest/data/tushare）",
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
    curated_root = (
        Path(args.curated_root).expanduser().resolve() if args.curated_root else None
    )
    raw_root = Path(args.raw_root).expanduser().resolve() if args.raw_root else None

    try:
        card = build_card(repo_root, vault_root, audit_root, now, curated_root, raw_root)
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
