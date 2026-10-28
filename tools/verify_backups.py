#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读备份校验器 —— 校验 <workspace>/_vault 下 MANIFEST.txt 与实体文件是否一致。

设计约束
--------
* **只读**：本脚本只打开文件读取（open 'rb'）、只读目录、只写 stdout/stderr，
  不创建/修改/删除任何文件，不写缓存（配 `python -B` 运行）。
* 与 `tools/backup_bundles.ps1` 的产物格式严格对齐：
  `<文件名>  <yyyy-MM-dd HH:mm:ss>  <字节数> bytes  sha256=<64位大写>`
  MANIFEST 里的 `#` 开头行（`# vault backup ...` / `# summary ...`）一律跳过；
  现存 2026-10-22 的 MANIFEST 实测没有这些注释行，两种都要能吃。
* MANIFEST **不含自身**（刻意避免哈希自指），所以 ORPHAN 判定要显式排除它。

校验项（逐行）
--------------
1. 文件存在与否；
2. 字节数是否一致；
3. sha256 是否一致（700 MB 级文件耗时，支持 `--sample N` 抽样，默认全量）；
4. 反向：目录里有、MANIFEST 里没列 —— 孤儿文件；
5. 新鲜度：最新备份距今小时数（默认 >36h 警告、>72h 判失败）；
6. 追平：bundle 里记录的提交是否等于对应仓的 HEAD（见下）；
7. 应有产物覆盖：**最新日期目录**必须把 `backup_bundles.ps1` 的
   `$expectedArtifacts`（每个默认仓一个 `<label>-<yyyymmdd>.bundle`、
   `workspace-docs-<yyyymmdd>.zip`、`data_manifest.json`）**全部**登记进
   MANIFEST，缺一项即 problems；`entries` 为 0 也算失败。
   理由：备份脚本无论成败都无条件写 MANIFEST，所以「MANIFEST 存在」不等于
   「备成了」—— 旧版会让「0 条登记 + 四个 bundle 全丢」照样 exit 0。
   `--all` 的历史日期目录是快照，放宽；只对本次范围内最新那个目录强制。

追平检查（catchup）
-------------------
`backup_bundles.ps1` 只在"跑备份那一刻"把仓库打成 bundle，之后 HEAD 前进它
不会自动重备。本脚本用**只读**命令补上这个缺口：

* 对最新日期目录里的每个 `*.bundle`，用
  `git -C <repo> bundle list-heads <bundle>` 读取引用，再与
  `git -C <repo> rev-parse HEAD` 比较；
* 引用选择顺序：当前分支 `refs/heads/<branch>` → `refs/heads/master`
  → `refs/heads/main` → bundle 的 `HEAD` 行 → bundle 内唯一引用。
  后两者是为"分支改名 / 演练用旧引用 bundle"留的退路，会写进 `ref` 字段；
* `behind_commits` 用 `git rev-list --count <bundle_sha>..HEAD`；若
  bundle 的提交不在本仓对象库里（典型：该分支已被 rebase/force-push 掉，
  或 bundle 来自另一个仓），无法计算，记字符串 `"unknown"`，**不当作通过**；
* 任一仓 `match=false` 且 `behind_commits > 0` → 计入 problems，exit 1；
* 解析失败（bundle 被截断 / 打不开 / 解析不出引用）进 `catchup.unknown`，
  不伪装成 `match=true`；**默认计入 problems（exit 1）** —— restore 不了的
  bundle 不能判通过。`--lenient-catchup` 保留旧的「只 WARN、不改退出码」行为；
* 只判定"本次范围内最新那个日期目录"：默认就是最新备份；`--date` 判指定日；
  `--all` 仍只把最新一天当作当前备份，历史日期是快照，不按今天的 HEAD 判。
* 不传 `--repo` 时自动探测"本次 vault 的上级目录"里的四个仓（默认推导就是工作区；
  给了 `--root` 就只在那个夹具的上级目录里找，演练不会再去误比真实仓）；
  探测不到就打印一条 WARN，`catchup` 为空对象且不影响原有退出码。

退出码
------
0 = 全过（含"抽样未覆盖全部文件"但已知项全对）；
1 = 有不一致 / 缺失 / 孤儿 / 新鲜度超 72h / 无任何日期目录 /
    最新备份落后 HEAD（behind_commits > 0）/ 最新目录缺应有产物 /
    MANIFEST 登记 0 条 / catchup 有仓无法判定（除非 --lenient-catchup）；
2 = 脚本自身错误（参数、路径、意外异常）。

用法
----
    python -B tools/verify_backups.py                      # 最新日期目录，全量 sha256
    python -B tools/verify_backups.py --all --json         # 全部日期目录，JSON 到 stdout
    python -B tools/verify_backups.py --date 2026-10-22    # 指定日期目录
    python -B tools/verify_backups.py --sample 2           # 只对最大的 2 个文件算 sha256
    python -B tools/verify_backups.py --root D:\\fake_vault  # 指定 vault 根（演练用）
    python -B tools/verify_backups.py --repo C:\\项目\\zerobound-kg
    python -B tools/verify_backups.py --repo A --repo B    # --repo 可重复，只查指定仓
    python -B tools/verify_backups.py --lenient-catchup    # catchup unknown 只 WARN（旧行为）

输出约定
--------
* `--json` 时 **stdout 只有一份 JSON**（`ensure_ascii=True`，纯 ASCII，任何终端/日志都不乱码），
  人类可读摘要改走 **stderr**；
* 无 `--json` 时人类摘要走 stdout；
* 进度与警告**始终**走 stderr，因此 `--json > out.json` 永远拿到干净 JSON。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
import logging

SCHEMA = "vault-verify/1"
MANIFEST_NAME = "MANIFEST.txt"
DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CHUNK = 4 * 1024 * 1024  # 4 MiB/块，700 MB 约 180 次 read

# 文件名用非贪婪 + 至少两个空格做分隔，因此文件名里带单个空格也能解析。
LINE_RE = re.compile(
    r"^(?P<name>.+?)\s{2,}"
    r"(?P<mtime>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s{2,}"
    r"(?P<size>\d+)\s+bytes\s{2,}"
    r"sha256=(?P<sha>[0-9A-Fa-f]{64})\s*$",
    re.IGNORECASE,
)
HEADER_RE = re.compile(
    r"^#\s*vault backup\s+(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
)

# 只要出现其中之一，本次判定就失败（exit 1）。
PROBLEM_STATUSES = {"mismatch", "missing", "unreadable"}
# 已知项全对，但抽样模式没跑 sha256。
UNVERIFIED_STATUS = "unverified"

# 与 tools/backup_bundles.ps1 的 $repos 一一对应：label 也是 bundle 文件名前缀。
DEFAULT_REPOS: tuple[tuple[str, str], ...] = (
    ("aios", "老田的AI助手"),
    ("zerobound-kg", "zerobound-kg"),
    ("digital-legion", "digital-legion"),
    ("res-downloader", "res-downloader-py"),
)
LABEL_BY_DIRNAME = {dirname: label for label, dirname in DEFAULT_REPOS}
# 与 tools/backup_bundles.ps1 的 $repos 同步（改那边必须同一批改这里）：
# label 决定 bundle 文件名前缀，期望产物清单从它推导 —— 不要在这里写死 4 个。
EXPECTED_BUNDLE_LABELS: tuple[str, ...] = tuple(label for label, _ in DEFAULT_REPOS)
# 与 tools/backup_bundles.ps1 的 $expectedArtifacts 同步：当天目录应有的另外两个产物。
WORKSPACE_DOCS_NAME = "workspace-docs-{stamp}.zip"
DATA_MANIFEST_NAME = "data_manifest.json"
# 期望引用的兜底顺序（当前分支会插到最前面，见 _pick_bundle_ref）。
REF_CANDIDATES = ("refs/heads/master", "refs/heads/main", "HEAD")
CATCHUP_GIT_TIMEOUT = 120.0
SHA_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")


# --------------------------------------------------------------- 小工具 -----
def _iso(dt: datetime) -> str:
    return dt.astimezone().isoformat(timespec="seconds")


def _mib(n: int) -> str:
    return "%.1f MiB" % (n / 1048576.0)


def _commas(n: int) -> str:
    return "{:,}".format(n)


def _p(msg: str) -> None:
    """进度/警告一律 stderr。"""
    print(msg, file=sys.stderr, flush=True)


def _reconfigure_streams() -> None:
    """Windows 控制台默认 GBK：中文进度会炸，统一切 UTF-8 输出。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError) as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "tools/verify_backups.py", exc)


def default_root() -> Path:
    """与 backup_bundles.ps1 同一套推导：<本文件>/.. = repo，再上一级 = workspace。"""
    here = Path(__file__).resolve()
    repo = here.parent.parent
    return repo.parent / "_vault"


# --------------------------------------------------------------- 解析 ------
def parse_manifest(path: Path) -> dict:
    """解析 MANIFEST.txt。返回 entries / parse_errors / header_time。"""
    entries: list[dict] = []
    errors: list[dict] = []
    seen: dict[str, int] = {}
    header_time = None

    # utf-8-sig：PS 5.1 的 Set-Content -Encoding UTF8 会写 BOM，必须吃掉。
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.rstrip("\r\n")
            if not line.strip():
                continue
            if line.lstrip().startswith("#"):
                m = HEADER_RE.match(line.strip())
                if m:
                    try:
                        header_time = datetime.strptime(
                            m.group("ts"), "%Y-%m-%d %H:%M:%S"
                        ).astimezone()
                    except ValueError as exc:
                        logging.getLogger(__name__).warning("silent-except %s: %s", "tools/verify_backups.py", exc)
                continue
            m = LINE_RE.match(line)
            if not m:
                errors.append(
                    {"line": lineno, "reason": "unparsable", "text": line[:200]}
                )
                continue
            name = m.group("name").strip()
            if name in seen:
                errors.append(
                    {
                        "line": lineno,
                        "reason": "duplicate",
                        "name": name,
                        "first_line": seen[name],
                    }
                )
                continue
            seen[name] = lineno
            entries.append(
                {
                    "name": name,
                    "line": lineno,
                    "mtime_text": m.group("mtime"),
                    "size": int(m.group("size")),
                    "sha256": m.group("sha").upper(),
                }
            )
    return {"entries": entries, "parse_errors": errors, "header_time": header_time}


def sha256_file(path: Path) -> tuple[str, int, float]:
    """流式算 sha256，返回 (大写 hex, 字节数, 耗时秒)。"""
    digest = hashlib.sha256()
    total = 0
    started = time.perf_counter()
    with path.open("rb", buffering=0) as fh:
        while True:
            block = fh.read(CHUNK)
            if not block:
                break
            digest.update(block)
            total += len(block)
    return digest.hexdigest().upper(), total, time.perf_counter() - started


# ------------------------------------------------------------- 校验主体 -----
def list_date_dirs(root: Path) -> list[tuple[str, Path]]:
    """只认名字严格等于 yyyy-MM-dd 的子目录；designs / wt-preserve 之类一律忽略。"""
    found: list[tuple[str, Path]] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if not DATE_DIR_RE.match(child.name):
            continue
        try:
            datetime.strptime(child.name, "%Y-%m-%d")
        except ValueError:
            continue
        found.append((child.name, child))
    found.sort(key=lambda item: item[0])
    return found


def expected_artifacts(day: str) -> list[str]:
    """由日期目录名 `<yyyy-MM-dd>` 反推当天应有的全部产物。

    与 `tools/backup_bundles.ps1` 的 `$expectedArtifacts` 对齐：
    每个默认仓一个 `<label>-<yyyymmdd>.bundle`，外加
    `workspace-docs-<yyyymmdd>.zip` 和 `data_manifest.json`。
    目录名不是合法日期 → 返回空表（无从推断，不做覆盖断言）。
    """
    try:
        stamp = datetime.strptime(day, "%Y-%m-%d").strftime("%Y%m%d")
    except ValueError:
        return []
    names = ["%s-%s.bundle" % (label, stamp) for label in EXPECTED_BUNDLE_LABELS]
    names.append(WORKSPACE_DOCS_NAME.format(stamp=stamp))
    names.append(DATA_MANIFEST_NAME)
    return names


def pick_sample(entries: list[dict], sample: int | None) -> set[str]:
    """抽样规则：按字节数从大到小取前 N 个（大文件风险最高，且确定性可复现）。"""
    if sample is None or sample >= len(entries):
        return {e["name"] for e in entries}
    ordered = sorted(entries, key=lambda e: (-e["size"], e["name"]))
    return {e["name"] for e in ordered[:sample]}


def verify_dir(
    day: str,
    day_dir: Path,
    sample: int | None,
    progress: bool,
) -> dict:
    manifest_path = day_dir / MANIFEST_NAME
    result: dict = {
        "date": day,
        "path": str(day_dir),
        "manifest": {
            "path": str(manifest_path),
            "present": manifest_path.is_file(),
            "entries": 0,
            "parse_errors": [],
            "header_time": None,
        },
        "entries": [],
        "orphans": [],
        # 应有产物（F6）：required 由目录名反推，missing = 未登记进 MANIFEST 的。
        "expected": {"required": [], "missing": []},
        "counts": {
            "listed": 0,
            "ok": 0,
            "mismatch": 0,
            "missing": 0,
            "unreadable": 0,
            "unverified": 0,
            "orphans": 0,
            "parse_errors": 0,
        },
        "bytes": {"listed": 0, "hashed": 0},
        "newest_mtime_epoch": None,
        "newest_mtime_source": None,
    }

    # 目录里所有实体文件（含子目录？vault 目录是平的，这里只认顶层文件）。
    present: dict[str, os.stat_result] = {}
    for child in sorted(day_dir.iterdir(), key=lambda p: p.name):
        if child.is_file():
            try:
                present[child.name] = child.stat()
            except OSError:
                continue

    # 新鲜度取"目录内最新一次写盘"，MANIFEST 自身也算（它就是那天最后落盘的）。
    for name, st in present.items():
        if result["newest_mtime_epoch"] is None or st.st_mtime > result["newest_mtime_epoch"]:
            result["newest_mtime_epoch"] = st.st_mtime
            result["newest_mtime_source"] = name

    # 应有产物（F6）：先把期望清单按目录名算出来，两个分支都要用它。
    expected = expected_artifacts(day)
    result["expected"]["required"] = expected

    if not manifest_path.is_file():
        # 没有 MANIFEST 就没有"应该有什么"的判据：直接判失败，不再猜孤儿。
        result["manifest"]["present"] = False
        result["expected"]["missing"] = list(expected)
        result["counts"]["orphans"] = len(present)
        result["orphans"] = [
            {"name": n, "status": "orphan", "size": st.st_size}
            for n, st in sorted(present.items())
        ]
        return result

    parsed = parse_manifest(manifest_path)
    entries = parsed["entries"]
    result["manifest"]["entries"] = len(entries)
    result["manifest"]["parse_errors"] = parsed["parse_errors"]
    result["manifest"]["header_time"] = (
        _iso(parsed["header_time"]) if parsed["header_time"] else None
    )
    result["counts"]["parse_errors"] = len(parsed["parse_errors"])

    mstat = present.get(MANIFEST_NAME)
    if mstat is not None:
        if (
            result["newest_mtime_epoch"] is None
            or mstat.st_mtime > result["newest_mtime_epoch"]
        ):
            result["newest_mtime_epoch"] = mstat.st_mtime
            result["newest_mtime_source"] = MANIFEST_NAME

    to_hash = pick_sample(entries, sample)
    listed_names = {e["name"] for e in entries}
    total = len(entries)
    # 覆盖判定看的是「MANIFEST 登记了没有」，不是「磁盘上有没有」：
    # 磁盘上有但没登记 = 孤儿（另一条 problems），这里只抓「根本没备」。
    result["expected"]["missing"] = [n for n in expected if n not in listed_names]

    for index, entry in enumerate(entries, start=1):
        name = entry["name"]
        expected_size = entry["size"]
        expected_sha = entry["sha256"]
        result["bytes"]["listed"] += expected_size
        rec: dict = {
            "name": name,
            "status": "ok",
            "expected_size": expected_size,
            "actual_size": None,
            "size_ok": None,
            "expected_sha256": expected_sha,
            "actual_sha256": None,
            "hash_ok": None,
            "hash_skipped": name not in to_hash,
            "mtime": None,
            "elapsed_s": None,
        }

        st = present.get(name)
        if st is None:
            rec["status"] = "missing"
            result["entries"].append(rec)
            if progress:
                _p("[%d/%d] MISSING  %s" % (index, total, name))
            continue

        rec["actual_size"] = st.st_size
        rec["size_ok"] = st.st_size == expected_size
        rec["mtime"] = _iso(datetime.fromtimestamp(st.st_mtime))
        if (
            result["newest_mtime_epoch"] is None
            or st.st_mtime > result["newest_mtime_epoch"]
        ):
            result["newest_mtime_epoch"] = st.st_mtime
            result["newest_mtime_source"] = name

        if rec["hash_skipped"]:
            rec["status"] = UNVERIFIED_STATUS if rec["size_ok"] else "mismatch"
            result["entries"].append(rec)
            if progress:
                _p(
                    "[%d/%d] %-8s %s  (sha256 skipped, --sample)"
                    % (index, total, rec["status"], name)
                )
            continue

        if progress:
            _p(
                "[%d/%d] hashing %s  %s ..."
                % (index, total, name, _mib(expected_size))
            )
        try:
            digest, read_bytes, elapsed = sha256_file(day_dir / name)
            rec["actual_sha256"] = digest
            rec["elapsed_s"] = round(elapsed, 3)
            rec["hash_ok"] = digest == expected_sha
            result["bytes"]["hashed"] += read_bytes
            if rec["size_ok"] and rec["hash_ok"]:
                rec["status"] = "ok"
            else:
                rec["status"] = "mismatch"
            if progress:
                rate = (
                    "%s/s" % _mib(read_bytes / elapsed) if elapsed > 0 else "n/a"
                )
                _p(
                    "        -> %-9s size=%s hash=%s  %.2fs  %s"
                    % (
                        rec["status"],
                        "ok" if rec["size_ok"] else "BAD",
                        "ok" if rec["hash_ok"] else "BAD",
                        elapsed,
                        rate,
                    )
                )
        except OSError as exc:
            rec["status"] = "unreadable"
            rec["error"] = "%s: %s" % (type(exc).__name__, exc)
            if progress:
                _p("[%d/%d] UNREADABLE %s  %s" % (index, total, name, rec["error"]))
        result["entries"].append(rec)

    # 孤儿：存在但 MANIFEST 没列；MANIFEST 自己按设计不入册，排除。
    for name in sorted(present):
        if name == MANIFEST_NAME or name in listed_names:
            continue
        result["orphans"].append(
            {"name": name, "status": "orphan", "size": present[name].st_size}
        )

    for rec in result["entries"]:
        result["counts"][rec["status"]] = result["counts"].get(rec["status"], 0) + 1
    result["counts"]["orphans"] = len(result["orphans"])
    result["counts"]["listed"] = total
    return result


# ------------------------------------------------------------- 追平 HEAD -----
def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _short_sha(sha: str | None) -> str:
    return sha[:8] if isinstance(sha, str) and sha else "-"


def _git_run(
    repo: Path, args: list[str], timeout: float = CATCHUP_GIT_TIMEOUT
) -> subprocess.CompletedProcess[bytes]:
    """只读 git 调用：--no-optional-locks + 禁止交互，绝不写仓库。"""
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


def resolve_repos(
    explicit: list[str] | None, workspace: Path | None = None
) -> tuple[list[dict], str | None]:
    """解析 --repo；不传则探测 workspace 里的四仓。返回 (specs, warning)。

    `workspace` 由 `--root` 的上级目录传入：演练用的假 vault 不会再去比对真实
    仓（否则 --root 演练必然把 catchup 判成 unknown，F7 之后等于必然红）。
    """
    if shutil.which("git") is None:
        return [], "找不到 git 可执行文件，跳过追平检查（catchup 为空）"
    if explicit:
        specs: list[dict] = []
        for raw in explicit:
            path = Path(raw).expanduser()
            try:
                path = path.resolve()
            except OSError as exc:
                specs.append(
                    {
                        "label": path.name or raw,
                        "path": path,
                        "explicit": True,
                        "error": "%s: %s" % (type(exc).__name__, exc),
                    }
                )
                continue
            specs.append(
                {
                    "label": LABEL_BY_DIRNAME.get(path.name, path.name),
                    "path": path,
                    "explicit": True,
                    "error": None,
                }
            )
        return specs, None

    workspace = workspace or default_root().parent
    specs = []
    for label, dirname in DEFAULT_REPOS:
        path = workspace / dirname
        if path.is_dir():
            specs.append(
                {"label": label, "path": path, "explicit": False, "error": None}
            )
    if not specs:
        return [], (
            "未在 %s 下探测到备份四仓中的任何一个，跳过追平检查（catchup 为空）"
            % workspace
        )
    return specs, None


def _find_bundle(entries: list[dict], label: str) -> str | None:
    prefix = label + "-"
    candidates = sorted(
        entry["name"]
        for entry in entries
        if entry["name"].startswith(prefix) and entry["name"].lower().endswith(".bundle")
    )
    return candidates[0] if candidates else None


def _parse_bundle_heads(text: str) -> dict[str, str]:
    """把 `git bundle list-heads` 的 `<sha> <ref>` 行解析成 {ref: sha}。"""
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
    """按 当前分支 → master → main → HEAD → 唯一引用 选一个引用。

    返回 (ref, sha, note)。note 非空表示用了兜底，会写进 JSON 方便追责。
    """
    candidates: list[str] = []
    if branch and branch not in ("HEAD", ""):
        candidates.append("refs/heads/" + branch)
        candidates.extend(REF_CANDIDATES)
    else:
        # detached HEAD：bundle 的 HEAD 行才是备份时真正检出的提交。
        candidates.append("HEAD")
        candidates.extend(ref for ref in REF_CANDIDATES if ref != "HEAD")
    seen: set[str] = set()
    for ref in candidates:
        if ref in seen:
            continue
        seen.add(ref)
        if ref in heads:
            return ref, heads[ref], None
    if len(heads) == 1:
        ref, sha = next(iter(heads.items()))
        return ref, sha, "bundle 缺少期望分支引用，退化为唯一引用 %s" % ref
    return (
        None,
        None,
        "bundle 缺少期望分支引用（现有: %s）" % (", ".join(sorted(heads)) or "none"),
    )


def _empty_catchup(enabled: bool, checked_date: str | None, note: str | None) -> dict:
    return {
        "enabled": enabled,
        "checked_date": checked_date,
        "status": "disabled" if not enabled else "unknown",
        "repos": [],
        "entries": [],
        "behind": [],
        "unknown": [],
        "max_behind_commits": None,
        "complete": False,
        "note": note,
    }


def collect_catchup(
    day: str,
    day_dir: Path,
    entries: list[dict],
    specs: list[dict],
) -> dict:
    """逐仓比较 bundle 内记录的分支 SHA 与仓的 HEAD（只读）。"""
    result = _empty_catchup(bool(specs), day, None)
    if not specs:
        return result

    for spec in specs:
        repo_path: Path = spec["path"]
        label: str = spec["label"]
        repo_info = {
            "label": label,
            "path": str(repo_path),
            "explicit": bool(spec.get("explicit")),
            "is_git": False,
            "branch": None,
            "head_sha": None,
            "reason": None,
        }
        entry = {
            "label": label,
            "date": day,
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

        # 1) 仓本身能否读、HEAD 是什么
        if spec.get("error"):
            entry["reason"] = "无法解析 repo 路径: %s" % spec["error"]
        elif not repo_path.is_dir():
            entry["reason"] = "repo 路径不存在或不是目录: %s" % repo_path
        else:
            try:
                probe = _git_run(repo_path, ["rev-parse", "--is-inside-work-tree"])
            except FileNotFoundError:
                entry["reason"] = "找不到 git 可执行文件"
            except (OSError, subprocess.TimeoutExpired) as exc:
                entry["reason"] = "git 探测失败: %s: %s" % (type(exc).__name__, exc)
            else:
                if probe.returncode != 0:
                    entry["reason"] = "不是 git 工作树: " + (
                        _text(probe.stderr).strip()[:200] or "git 返回非零"
                    )
                else:
                    repo_info["is_git"] = True
                    try:
                        head_proc = _git_run(repo_path, ["rev-parse", "HEAD"])
                        branch_proc = _git_run(
                            repo_path, ["rev-parse", "--abbrev-ref", "HEAD"]
                        )
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        entry["reason"] = "git rev-parse 失败: %s: %s" % (
                            type(exc).__name__,
                            exc,
                        )
                    else:
                        if head_proc.returncode == 0:
                            head = _text(head_proc.stdout).strip().splitlines()
                            if head:
                                entry["head_sha"] = head[0].lower()
                                entry["head_sha_short"] = _short_sha(head[0])
                                repo_info["head_sha"] = entry["head_sha"]
                        else:
                            entry["reason"] = "git rev-parse HEAD 失败: " + (
                                _text(head_proc.stderr).strip()[:200] or "git 返回非零"
                            )
                        if branch_proc.returncode == 0:
                            branch = _text(branch_proc.stdout).strip()
                            if branch:
                                repo_info["branch"] = branch
        repo_info["reason"] = entry["reason"]
        result["repos"].append(repo_info)

        # 2) 找到该仓对应的 bundle
        bundle_name = _find_bundle(entries, label)
        if bundle_name is None:
            if entry["reason"] is None:
                entry["reason"] = "MANIFEST 里没有 %s-*.bundle" % label
        else:
            bundle_path = day_dir / bundle_name
            entry["bundle_name"] = bundle_name
            entry["bundle"] = str(bundle_path)
            if not bundle_path.is_file():
                if entry["reason"] is None:
                    entry["reason"] = "MANIFEST 列了 bundle 但文件不存在"
            elif entry["reason"] is None:
                # 3) 只读 list-heads + 比较
                try:
                    heads_proc = _git_run(
                        repo_path, ["bundle", "list-heads", str(bundle_path)]
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    entry["reason"] = "git bundle list-heads 失败: %s: %s" % (
                        type(exc).__name__,
                        exc,
                    )
                else:
                    if heads_proc.returncode != 0:
                        entry["reason"] = "git bundle list-heads 失败: " + (
                            _text(heads_proc.stderr).strip()[:240] or "git 返回非零"
                        )
                    else:
                        heads = _parse_bundle_heads(_text(heads_proc.stdout))
                        if not heads:
                            entry["reason"] = "bundle 里解析不到任何引用"
                        else:
                            ref, bundle_sha, ref_note = _pick_bundle_ref(
                                heads, repo_info["branch"]
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
                                        "（需先 fetch 该 bundle）" % _short_sha(bundle_sha)
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
                                                _text(count_proc.stderr).strip()[:200]
                                                or "git 返回非零"
                                            )
                                        )
                                    else:
                                        try:
                                            count = int(
                                                _text(count_proc.stdout).strip().splitlines()[0]
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
    result["max_behind_commits"] = max(behind_counts) if behind_counts else None
    result["complete"] = bool(result["entries"]) and not result["unknown"]
    if result["behind"]:
        result["status"] = "behind"
    elif result["unknown"]:
        result["status"] = "unknown"
    elif result["entries"]:
        result["status"] = "current"
    if result["unknown"]:
        result["note"] = "%d 个仓无法判定" % len(result["unknown"])
    return result


def _catchup_line(catchup: dict) -> str:
    """人类摘要用的一行（多个仓用 ; 分隔）。"""
    if not catchup.get("enabled"):
        return "not checked (%s)" % (
            catchup.get("note") or "no repo detected or git unavailable"
        )
    entries = catchup.get("entries") or []
    if not entries:
        return "not checked (%s)" % (catchup.get("note") or "no bundle")
    parts: list[str] = []
    for entry in entries:
        label = entry["label"]
        if entry["status"] == "behind":
            parts.append(
                "%s behind %s commits (backup %s < head %s)"
                % (
                    label,
                    entry["behind_commits"],
                    entry["bundle_sha_short"],
                    entry["head_sha_short"],
                )
            )
        elif entry["status"] == "current":
            parts.append(
                "%s current (backup %s == head %s)"
                % (label, entry["bundle_sha_short"], entry["head_sha_short"])
            )
        else:
            parts.append(
                "%s UNKNOWN (%s)" % (label, entry.get("reason") or "无法判定")
            )
    return "; ".join(parts)


def compute_freshness(
    dirs: list[dict], now: datetime, warn_hours: float, fail_hours: float
) -> dict:
    """最新一份备份距今多久。判据：最新日期目录里最大的文件 mtime（含 MANIFEST）。"""
    reference = None
    source = None
    newest_date = None
    for item in dirs:
        epoch = item.get("newest_mtime_epoch")
        if epoch is None:
            continue
        if reference is None or epoch > reference:
            reference = epoch
            source = item.get("newest_mtime_source")
            newest_date = item["date"]

    if reference is None:
        return {
            "newest_date": newest_date,
            "reference_time": None,
            "reference_source": None,
            "age_hours": None,
            "thresholds": {"warn_hours": warn_hours, "fail_hours": fail_hours},
            "status": "fail",
            "note": "no file with a usable mtime under any date folder",
        }

    ref_dt = datetime.fromtimestamp(reference).astimezone()
    age_hours = (now - ref_dt).total_seconds() / 3600.0
    if age_hours >= fail_hours:
        status = "fail"
    elif age_hours >= warn_hours:
        status = "warn"
    else:
        status = "ok"
    return {
        "newest_date": newest_date,
        "reference_time": _iso(ref_dt),
        "reference_source": source,
        "age_hours": round(age_hours, 2),
        "thresholds": {"warn_hours": warn_hours, "fail_hours": fail_hours},
        "status": status,
    }


# ------------------------------------------------------------------ 输出 -----
def emit_human(report: dict, stream) -> None:
    out = stream.write
    bar = "=" * 72
    out("%s\n=== vault verify ===\n" % bar)
    out("root        : %s\n" % report["root"])
    out(
        "mode        : %s (%d date folder(s): %s)\n"
        % (
            report["mode"],
            len(report["dirs"]),
            ", ".join(d["date"] for d in report["dirs"]) or "-",
        )
    )
    out(
        "hash scope  : %s\n"
        % (
            "full (all files)"
            if report["sample"] is None
            else "--sample %d (largest first)" % report["sample"]
        )
    )
    out("time        : %s\n" % report["generated_at"])

    for item in report["dirs"]:
        out("\n[%s] %s\n" % (item["date"], item["path"]))
        man = item["manifest"]
        if not man["present"]:
            out("  manifest  : MISSING  %s\n" % man["path"])
        else:
            out(
                "  manifest  : %d entr(y/ies), %d parse error(s)\n"
                % (man["entries"], len(man["parse_errors"]))
            )
            exp = item.get("expected") or {}
            required = exp.get("required") or []
            missing = exp.get("missing") or []
            if required:
                detail = "" if not missing else ", MISSING: " + ", ".join(missing)
                out(
                    "  expected  : %d/%d artifact(s) registered%s\n"
                    % (len(required) - len(missing), len(required), detail)
                )
        for err in man["parse_errors"]:
            out("  [parse]   line %s: %s\n" % (err.get("line"), err.get("reason")))
        for rec in item["entries"]:
            if rec["status"] == "ok":
                detail = "sha256 match"
            elif rec["status"] == UNVERIFIED_STATUS:
                detail = "size ok, sha256 NOT verified"
            elif rec["status"] == "missing":
                detail = "listed but file not found"
            elif rec["status"] == "unreadable":
                detail = rec.get("error", "unreadable")
            else:
                detail = "size %s / sha256 %s" % (
                    "ok" if rec["size_ok"] else "BAD (%s != %s)"
                    % (_commas(rec["actual_size"] or 0), _commas(rec["expected_size"])),
                    "ok" if rec["hash_ok"] else ("not checked" if rec["hash_ok"] is None else "BAD"),
                )
            out("  [%-10s] %-32s %s\n" % (rec["status"], rec["name"], detail))
        for orph in item["orphans"]:
            out(
                "  [orphan    ] %-32s %s bytes, not listed in MANIFEST\n"
                % (orph["name"], _commas(orph["size"]))
            )
        counts = item["counts"]
        out(
            "  counts    : listed=%d ok=%d mismatch=%d missing=%d unreadable=%d "
            "unverified=%d orphans=%d\n"
            % (
                counts["listed"],
                counts["ok"],
                counts["mismatch"],
                counts["missing"],
                counts["unreadable"],
                counts["unverified"],
                counts["orphans"],
            )
        )
        out(
            "  bytes     : %s listed, %s hashed\n"
            % (_commas(item["bytes"]["listed"]), _commas(item["bytes"]["hashed"]))
        )

    fresh = report["freshness"]
    if fresh["age_hours"] is None:
        out(
            "\nfreshness   : UNKNOWN (%s)  => %s\n"
            % (fresh.get("note", "-"), fresh["status"].upper())
        )
    else:
        out(
            "\nfreshness   : newest %s, %.2f h ago (%s, from %s)  "
            "warn>=%gh fail>=%gh  => %s\n"
            % (
                fresh["newest_date"],
                fresh["age_hours"],
                fresh["reference_time"],
                fresh["reference_source"],
                fresh["thresholds"]["warn_hours"],
                fresh["thresholds"]["fail_hours"],
                fresh["status"].upper(),
            )
        )

    summary = report["summary"]
    out("catchup     : %s\n" % _catchup_line(report.get("catchup") or {}))
    out("\nsummary     : %d file(s): %d ok, %d mismatch, %d missing, %d unreadable, "
        "%d unverified; %d orphan(s); %d parse error(s)\n"
        % (
            summary["files"],
            summary["ok"],
            summary["mismatch"],
            summary["missing"],
            summary["unreadable"],
            summary["unverified"],
            summary["orphans"],
            summary["parse_errors"],
        ))
    if report["problems"]:
        out("problems    :\n")
        for problem in report["problems"]:
            out("  - %s\n" % problem)
    out(
        "result      : %s (exit %d)  elapsed %.2fs\n"
        % (
            "OK" if report["exit_code"] == 0 else "FAILED",
            report["exit_code"],
            summary["elapsed_s"],
        )
    )


def emit_json(report: dict) -> None:
    # ensure_ascii=True：纯 ASCII，GBK 控制台 / 任意日志都不会乱码。
    sys.stdout.write(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=False))
    sys.stdout.write("\n")
    sys.stdout.flush()


# ------------------------------------------------------------------- 主流程 ---
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="verify_backups.py",
        description=(
            "只读校验 _vault 下的 MANIFEST.txt 与备份文件"
            "（存在/字节数/sha256/孤儿/新鲜度/bundle 是否追平 HEAD）"
        ),
        epilog="退出码：0=全过，1=不一致/缺失/落后 HEAD，2=脚本错误",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--root",
        default=None,
        help="vault 根目录；默认 <脚本>/../../_vault（即 workspace\\_vault）",
    )
    ap.add_argument("--date", default=None, help="只校验指定日期目录，如 2026-10-22")
    ap.add_argument("--all", action="store_true", help="校验全部日期目录")
    ap.add_argument(
        "--sample",
        type=int,
        default=None,
        metavar="N",
        help="只对最大的 N 个文件算 sha256（存在性与字节数仍然全查）",
    )
    ap.add_argument(
        "--repo",
        action="append",
        default=None,
        metavar="PATH",
        help=(
            "追平检查用的仓根，可重复；默认自动探测工作区里的四个仓"
            "（老田的AI助手/zerobound-kg/digital-legion/res-downloader-py）"
        ),
    )
    ap.add_argument("--json", action="store_true", help="stdout 输出机器可读 JSON（摘要改走 stderr）")
    ap.add_argument(
        "--lenient-catchup",
        action="store_true",
        help=(
            "catchup 无法判定的仓只 WARN、不计入 problems/退出码"
            "（旧行为；默认计入 problems → exit 1）"
        ),
    )
    ap.add_argument(
        "--no-progress", action="store_true", help="关闭 stderr 进度输出"
    )
    ap.add_argument(
        "--warn-hours", type=float, default=36.0, help="新鲜度警告阈值（小时），默认 36"
    )
    ap.add_argument(
        "--fail-hours", type=float, default=72.0, help="新鲜度失败阈值（小时），默认 72"
    )
    return ap


def run(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    now = datetime.now().astimezone()
    progress = not args.no_progress

    if args.sample is not None and args.sample < 1:
        _p("ERROR: --sample 必须是 >=1 的整数")
        return 2
    if args.warn_hours < 0 or args.fail_hours < 0 or args.warn_hours > args.fail_hours:
        _p("ERROR: 需要 0 <= --warn-hours <= --fail-hours")
        return 2
    if args.date and args.all:
        _p("ERROR: --date 与 --all 不能同时使用")
        return 2

    root = Path(args.root).expanduser() if args.root else default_root()
    try:
        root = root.resolve()
    except OSError as exc:
        _p("ERROR: 无法解析 --root %r: %s" % (args.root, exc))
        return 2
    if not root.is_dir():
        _p("ERROR: vault 根不存在或不是目录: %s" % root)
        return 2

    date_dirs = list_date_dirs(root)
    if args.date:
        date_dirs = [item for item in date_dirs if item[0] == args.date]
        if not date_dirs:
            _p("ERROR: 找不到日期目录 %s（%s 下没有这个目录）" % (args.date, root))
            return 2
        mode = "date"
    elif args.all:
        mode = "all"
    else:
        mode = "latest"
        date_dirs = date_dirs[-1:]

    if progress:
        _p("vault root : %s" % root)
        _p(
            "scope      : %s -> %s"
            % (mode, ", ".join(d for d, _ in date_dirs) if date_dirs else "(empty)")
        )

    dir_reports: list[dict] = []
    for day, day_dir in date_dirs:
        dir_reports.append(
            verify_dir(day, day_dir, args.sample, progress)
        )

    freshness = compute_freshness(dir_reports, now, args.warn_hours, args.fail_hours)
    # 新鲜度算完就把内部字段摘掉，JSON 里只留对外契约。
    for item in dir_reports:
        item.pop("newest_mtime_epoch", None)
        item.pop("newest_mtime_source", None)

    # 追平检查只针对本次范围内最新那个日期目录（默认就是最新备份）。
    repo_specs, repo_warning = resolve_repos(args.repo, root.parent)
    if repo_warning:
        _p("WARN: " + repo_warning)
    latest_report = dir_reports[-1] if dir_reports else None
    if not repo_specs:
        # 向后兼容：探测不到仓时 catchup 就是空对象，绝不改变原有退出码。
        catchup = {}
    elif latest_report is None:
        catchup = _empty_catchup(True, None, "没有任何日期目录，追平检查未执行")
    elif not latest_report["manifest"]["present"]:
        catchup = _empty_catchup(
            True,
            latest_report["date"],
            "最新日期目录没有 MANIFEST.txt，追平检查未执行",
        )
    else:
        catchup = collect_catchup(
            latest_report["date"],
            Path(latest_report["path"]),
            latest_report["entries"],
            repo_specs,
        )
    catchup_unknown = catchup.get("unknown") or []
    if catchup_unknown:
        _p(
            "WARN: catchup 有 %d 个仓无法判定（bundle 可能被截断/损坏，restore 未验证）：%s%s"
            % (
                len(catchup_unknown),
                "; ".join(
                    "%s (%s)" % (entry["label"], entry.get("reason") or "无法判定")
                    for entry in catchup_unknown
                )[:600],
                "  [--lenient-catchup：只告警，不计入 problems]"
                if args.lenient_catchup
                else "  [默认计入 problems → exit 1]",
            )
        )

    problems: list[str] = []
    totals = {
        "files": 0,
        "ok": 0,
        "mismatch": 0,
        "missing": 0,
        "unreadable": 0,
        "unverified": 0,
        "orphans": 0,
        "parse_errors": 0,
    }
    for item in dir_reports:
        for rec in item["entries"]:
            totals["files"] += 1
            totals[rec["status"]] = totals.get(rec["status"], 0) + 1
            if rec["status"] in PROBLEM_STATUSES:
                if rec["status"] == "mismatch":
                    problems.append(
                        "%s/%s: content mismatch (size %s, sha256 %s)"
                        % (
                            item["date"],
                            rec["name"],
                            "ok" if rec["size_ok"] else "expected %s got %s"
                            % (_commas(rec["expected_size"]), _commas(rec["actual_size"] or 0)),
                            "ok" if rec["hash_ok"] else ("skipped" if rec["hash_ok"] is None else "differs"),
                        )
                    )
                elif rec["status"] == "missing":
                    problems.append(
                        "%s/%s: listed in MANIFEST but file is missing"
                        % (item["date"], rec["name"])
                    )
                else:
                    problems.append(
                        "%s/%s: unreadable (%s)"
                        % (item["date"], rec["name"], rec.get("error", "I/O error"))
                    )
        totals["orphans"] += len(item["orphans"])
        for orph in item["orphans"]:
            problems.append(
                "%s/%s: orphan file (%s bytes) not listed in MANIFEST"
                % (item["date"], orph["name"], _commas(orph["size"]))
            )
        totals["parse_errors"] += len(item["manifest"]["parse_errors"])
        for err in item["manifest"]["parse_errors"]:
            problems.append(
                "%s/MANIFEST.txt line %s: %s"
                % (item["date"], err.get("line"), err.get("reason"))
            )
        if not item["manifest"]["present"]:
            problems.append(
                "%s: MANIFEST.txt is missing, nothing to verify against" % item["date"]
            )

    # 应有产物覆盖（F6）：MANIFEST 存在 ≠ 备成了 —— backup_bundles.ps1 无论成败都
    # 无条件写 MANIFEST，所以「0 条登记」「四个 bundle 全丢」必须在退出码上变红。
    # --all 的历史日期目录是快照，放宽；只对本次范围内最新那个目录强制。
    if latest_report is not None and latest_report["manifest"]["present"]:
        day_label = latest_report["date"]
        if latest_report["manifest"]["entries"] == 0:
            problems.append(
                "%s: MANIFEST.txt registers 0 entries — a backup that wrote nothing "
                "must not pass" % day_label
            )
        for name in latest_report["expected"]["missing"]:
            problems.append(
                "%s/%s: expected artifact is not registered in MANIFEST.txt "
                "(backup_bundles.ps1 $expectedArtifacts)" % (day_label, name)
            )

    if freshness["status"] == "fail":
        problems.append(
            "freshness: newest backup is %s h old (fail threshold %s h)"
            % (
                "n/a" if freshness["age_hours"] is None else freshness["age_hours"],
                args.fail_hours,
            )
        )
    elif freshness["status"] == "warn":
        _p(
            "WARN: 最新备份已 %.2f 小时，超过警告阈值 %g 小时（未到失败阈值 %g 小时）"
            % (freshness["age_hours"], args.warn_hours, args.fail_hours)
        )

    # 无法判定同样是硬判定（F7）：bundle 被截断/打不开/解析不出引用 → status=unknown。
    # 旧版只 WARN → exit 0，等于「restore 不了的 bundle 判通过」；
    # --lenient-catchup 退回旧行为（只告警）。
    if catchup_unknown and not args.lenient_catchup:
        for entry in catchup_unknown:
            problems.append(
                "catchup: %s/%s cannot be verified (%s) — an unreadable or truncated "
                "bundle cannot be restored"
                % (
                    entry.get("date") or "?",
                    entry.get("bundle_name") or entry.get("label") or "?",
                    entry.get("reason") or "unknown",
                )
            )

    # 落后是硬判定：bundle 明确落后 >0 个提交 → problems + exit 1。
    for entry in catchup.get("behind") or []:
        count = entry.get("behind_commits")
        if isinstance(count, int) and count > 0:
            problems.append(
                "catchup: %s/%s is behind HEAD by %d commit(s) "
                "(backup %s < head %s, ref %s)"
                % (
                    entry["date"],
                    entry["bundle_name"],
                    count,
                    entry["bundle_sha_short"],
                    entry["head_sha_short"],
                    entry.get("ref") or "?",
                )
            )

    if not date_dirs:
        problems.append("no yyyy-MM-dd date folder found under %s" % root)

    exit_code = 1 if problems else 0
    report = {
        "schema": SCHEMA,
        "generated_at": _iso(now),
        "root": str(root),
        "mode": mode,
        "sample": args.sample,
        "thresholds": {
            "warn_hours": args.warn_hours,
            "fail_hours": args.fail_hours,
        },
        "tool": {
            "script": str(Path(__file__).resolve()),
            "python": sys.version.split()[0],
            "platform": sys.platform,
        },
        "dirs": dir_reports,
        "freshness": freshness,
        "catchup": catchup,
        "summary": {
            **totals,
            "elapsed_s": round(time.perf_counter() - started, 2),
            "bytes_listed": sum(d["bytes"]["listed"] for d in dir_reports),
            "bytes_hashed": sum(d["bytes"]["hashed"] for d in dir_reports),
            "complete": args.sample is None,
        },
        "problems": problems,
        "result": "ok" if exit_code == 0 else "failed",
        "exit_code": exit_code,
    }

    if args.json:
        emit_json(report)
        emit_human(report, sys.stderr)
    else:
        emit_human(report, sys.stdout)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    _reconfigure_streams()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse: --help=0, 参数错误=2
        return int(exc.code or 0)

    try:
        return run(args)
    except KeyboardInterrupt:
        _p("ERROR: 被中断（KeyboardInterrupt）")
        return 2
    except Exception as exc:  # noqa: BLE001
        _p("ERROR: 未预期异常：%s: %s" % (type(exc).__name__, exc))
        traceback.print_exc(file=sys.stderr)
        if getattr(args, "json", False):
            emit_json(
                {
                    "schema": SCHEMA,
                    "result": "error",
                    "exit_code": 2,
                    "error": "%s: %s" % (type(exc).__name__, exc),
                    "generated_at": _iso(datetime.now().astimezone()),
                }
            )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
