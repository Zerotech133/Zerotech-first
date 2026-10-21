# -*- coding: utf-8 -*-
"""四个运维工具的 pytest 契约测试（只新增测试，不改被测代码）。

被测对象（一律用 subprocess 调用，禁止 import —— 它们都有
``if __name__ == "__main__"`` 与模块级副作用）::

    tools/verify_backups.py     备份校验：MANIFEST/字节数/sha256/孤儿/新鲜度，退出码 0/1/2
    tools/gen_health_card.py    健康卡：task_audit + backup + data + repo + docs 五块、17 项检查
    tools/gen_docs_index.py     DOCS_INDEX 生成与 --check
    tools/daily_ops.ps1         PowerShell 编排：只测退出码与 JSON 契约

隔离原则
--------
* 夹具全部落在 ``tmp_path``，不读不写真实 ``_vault``，不碰真实仓库状态；
* 需要“仓库”的测试只建空目录：git 探测失败走 ``available:false`` 分支，
  这正是被测工具“读不到 → 红”的契约路径，且不依赖宿主 git 状态；
* 不启服务、不连数据库、不访问网络。

运行（在仓库根）::

    "C:\\Program Files\\Python311\\python.exe" -B -m pytest \
        tools/tests/test_ops_tools.py -q -p no:cacheprovider
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent
REPO = TOOLS.parent
PYTHON = sys.executable

VERIFY = TOOLS / "verify_backups.py"
HEALTH = TOOLS / "gen_health_card.py"
DOCS = TOOLS / "gen_docs_index.py"
DAILY = TOOLS / "daily_ops.ps1"

PROJECT_DIRNAME = "老田的AI助手"


# ---------------------------------------------------------------------------
# 通用执行与断言辅助
# ---------------------------------------------------------------------------
def _run_py(script: Path, *args: str, timeout: float = 120.0) -> subprocess.CompletedProcess:
    """以当前解释器 + -B 跑一个工具；PYTHONUTF8 保证子进程 stdout 是 UTF-8。"""
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [PYTHON, "-B", str(script), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=str(REPO),
        env=env,
        check=False,
    )


def _json_stdout(proc: subprocess.CompletedProcess, label: str) -> dict:
    """解析 stdout 的单一 JSON；失败时把 stdout/stderr 片段带进断言信息。"""
    text = (proc.stdout or "").lstrip("\ufeff")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        pytest.fail(
            "%s: stdout 不是合法 JSON: %s\n"
            "--- exit=%s ---\nstdout[:2000]=%r\nstderr[:2000]=%r"
            % (label, exc, proc.returncode, text[:2000], (proc.stderr or "")[:2000])
        )
    assert isinstance(payload, dict), f"{label}: JSON 顶层应为对象，实际 {type(payload)!r}"
    return payload


def _sha256_upper(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def _manifest_line(name: str, data: bytes) -> str:
    """与 tools/backup_bundles.ps1 的 Add-ManifestEntry 完全同格式。"""
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return "%s  %s  %d bytes  sha256=%s" % (name, stamp, len(data), _sha256_upper(data))


def _today_name() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d")


def _make_vault(
    root: Path,
    files: dict[str, bytes],
    *,
    comments: bool = False,
) -> Path:
    """造一个 <root>/<today>/ 备份目录：实体文件 + MANIFEST.txt。

    comments=True 时按 backup_bundles.ps1 的真实产物追加
    ``# vault backup`` / ``# vault`` / ``# summary`` 三类注释行。
    """
    day = root / _today_name()
    day.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if comments:
        lines.append("# vault backup %s 03:15:00" % _today_name())
        lines.append("# vault %s" % day)
    for name, data in files.items():
        (day / name).write_bytes(data)
        lines.append(_manifest_line(name, data))
    if comments:
        lines.append(
            "# summary ok=%d failed=0 bytes=%d"
            % (len(files), sum(len(data) for data in files.values()))
        )
    (day / "MANIFEST.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return day


def _run_verify(vault: Path, *args: str) -> subprocess.CompletedProcess:
    return _run_py(VERIFY, "--root", str(vault), "--json", "--no-progress", *args)


def _run_health(repo: Path, vault: Path, *args: str) -> subprocess.CompletedProcess:
    return _run_py(
        HEALTH,
        "--repo",
        str(repo),
        "--vault-root",
        str(vault),
        "--task-audit-root",
        str(repo / "portal" / "data" / "task_exit_audit"),
        *args,
    )


def _make_health_repo(tmp_path: Path, name: str = "hc") -> tuple[Path, Path]:
    """健康卡用的“仓库 + 备份仓”骨架；仓库故意不是 git，走 repo 不可用分支。"""
    workspace = tmp_path / name
    repo = workspace / "repo"
    repo.mkdir(parents=True)
    return repo, workspace / "_vault"


# ---------------------------------------------------------------------------
# 1) verify_backups.py
# ---------------------------------------------------------------------------
def test_verify_backups_ok_three_entries(tmp_path: Path) -> None:
    """3 条清单、sha256 全对 → exit 0，summary 全 ok。"""
    vault = tmp_path / "vault"
    _make_vault(
        vault,
        {
            "aios-20261022.bundle": b"bundle-A" * 37,
            "workspace-docs-20261022.zip": b"zip-B" * 53,
            "res-downloader-20261022.bundle": b"bundle-C" * 11,
        },
    )

    proc = _run_verify(vault)
    assert proc.returncode == 0, proc.stderr
    report = _json_stdout(proc, "verify_backups/ok")

    assert report["schema"] == "vault-verify/1"
    assert report["result"] == "ok"
    assert report["exit_code"] == 0
    assert report["mode"] == "latest"
    assert report["problems"] == []
    assert report["sample"] is None
    assert report["summary"]["complete"] is True

    summary = report["summary"]
    assert summary["files"] == 3
    assert summary["ok"] == 3
    assert summary["mismatch"] == 0
    assert summary["missing"] == 0
    assert summary["unreadable"] == 0
    assert summary["unverified"] == 0
    assert summary["orphans"] == 0
    assert summary["parse_errors"] == 0

    entries = report["dirs"][0]["entries"]
    assert {entry["status"] for entry in entries} == {"ok"}
    assert all(entry["size_ok"] and entry["hash_ok"] for entry in entries)
    assert report["freshness"]["status"] == "ok"


def test_verify_backups_size_mismatch_exit1(tmp_path: Path) -> None:
    """清单字节数与实体不符 → exit 1，counts.mismatch=1，problems 点名 mismatch。"""
    vault = tmp_path / "vault"
    day = _make_vault(vault, {"a.bundle": b"A" * 10, "b.bundle": b"B" * 20})
    (day / "b.bundle").write_bytes(b"B" * 21)  # 大小变了（sha 也必然变）

    proc = _run_verify(vault)
    assert proc.returncode == 1, proc.stderr
    report = _json_stdout(proc, "verify_backups/mismatch")

    assert report["result"] == "failed"
    assert report["exit_code"] == 1
    assert report["summary"]["mismatch"] == 1
    assert report["summary"]["ok"] == 1
    bad = [entry for entry in report["dirs"][0]["entries"] if entry["name"] == "b.bundle"][0]
    assert bad["status"] == "mismatch"
    assert bad["size_ok"] is False
    assert bad["actual_size"] == 21 and bad["expected_size"] == 20
    assert any("mismatch" in problem for problem in report["problems"])


def test_verify_backups_missing_file_exit1(tmp_path: Path) -> None:
    """清单列了但磁盘没有 → exit 1，counts.missing=1，problems 点名 missing。"""
    vault = tmp_path / "vault"
    day = _make_vault(vault, {"a.bundle": b"A" * 10, "c.bundle": b"C" * 30})
    (day / "c.bundle").unlink()

    proc = _run_verify(vault)
    assert proc.returncode == 1, proc.stderr
    report = _json_stdout(proc, "verify_backups/missing")

    assert report["summary"]["missing"] == 1
    assert report["summary"]["ok"] == 1
    assert report["summary"]["orphans"] == 0
    bad = [entry for entry in report["dirs"][0]["entries"] if entry["name"] == "c.bundle"][0]
    assert bad["status"] == "missing"
    assert bad["actual_size"] is None
    assert any("missing" in problem for problem in report["problems"])


def test_verify_backups_orphan_file_exit1(tmp_path: Path) -> None:
    """磁盘上有、清单未列 → exit 1，counts.orphans=1，problems 点名 orphan。"""
    vault = tmp_path / "vault"
    day = _make_vault(vault, {"a.bundle": b"A" * 10})
    (day / "stray.bundle").write_bytes(b"not-in-manifest")

    proc = _run_verify(vault)
    assert proc.returncode == 1, proc.stderr
    report = _json_stdout(proc, "verify_backups/orphan")

    assert report["summary"]["orphans"] == 1
    assert report["summary"]["ok"] == 1
    orphans = report["dirs"][0]["orphans"]
    assert [item["name"] for item in orphans] == ["stray.bundle"]
    assert any("orphan" in problem for problem in report["problems"])


def test_verify_backups_comment_lines_are_not_parse_errors(tmp_path: Path) -> None:
    """MANIFEST 里的 # 注释行必须跳过（刚修的口径），不能算 parse error。"""
    vault = tmp_path / "vault"
    _make_vault(
        vault,
        {
            "a.bundle": b"A" * 8,
            "b.bundle": b"B" * 16,
            "c.bundle": b"C" * 32,
        },
        comments=True,
    )

    proc = _run_verify(vault)
    assert proc.returncode == 0, proc.stderr
    report = _json_stdout(proc, "verify_backups/comments")

    manifest = report["dirs"][0]["manifest"]
    assert manifest["parse_errors"] == []
    assert manifest["entries"] == 3
    assert report["summary"]["parse_errors"] == 0
    assert report["summary"]["files"] == 3
    assert report["summary"]["ok"] == 3
    assert report["problems"] == []
    assert report["result"] == "ok"
    assert report["exit_code"] == 0


@pytest.mark.parametrize("bad_date", ["1999-01-01", "2026-13-01", "not-a-date"])
def test_verify_backups_bad_date_exit2(tmp_path: Path, bad_date: str) -> None:
    """坏 --date（格式非法或目录不存在）→ exit 2（脚本自身错误）。"""
    vault = tmp_path / "vault"
    _make_vault(vault, {"a.bundle": b"A" * 10})

    proc = _run_verify(vault, "--date", bad_date)
    assert proc.returncode == 2, "stdout=%r stderr=%r" % (proc.stdout, proc.stderr)
    assert "ERROR" in proc.stderr


def test_verify_backups_missing_root_exit2(tmp_path: Path) -> None:
    """--root 指向不存在的目录 → exit 2。"""
    proc = _run_verify(tmp_path / "no_such_vault")
    assert proc.returncode == 2
    assert "ERROR" in proc.stderr
    assert "不存在" in proc.stderr or "not" in proc.stderr.lower()


# ---------------------------------------------------------------------------
# 2) gen_health_card.py
# ---------------------------------------------------------------------------
def _checks_by_id(card: dict) -> dict[str, dict]:
    return {check["id"]: check for check in card["checks"]}


def test_health_card_manifest_comments_not_malformed(tmp_path: Path) -> None:
    """回归：MANIFEST 含 # 注释行时 backup.manifest_malformed 必须为 0。

    旧口径把 backup_bundles.ps1 写的 ``# vault backup`` / ``# summary`` 当格式异常，
    会把完整备份误判成黄。本测试锁死“注释不是条目、不算 malformed”。
    """
    repo, vault = _make_health_repo(tmp_path)
    _make_vault(
        vault,
        {
            "aios-20261022.bundle": b"A" * 64,
            "workspace-docs-20261022.zip": b"Z" * 128,
            "zerobound-kg-20261022.bundle": b"K" * 256,
        },
        comments=True,
    )

    proc = _run_health(repo, vault)
    card = _json_stdout(proc, "health_card/comments")

    backup = card["backup"]
    assert backup["available"] is True
    assert backup["manifest_malformed"] == 0
    assert backup["manifest_malformed_samples"] == []
    assert backup["manifest_entries"] == 3
    assert backup["manifest_missing"] == 0
    assert backup["size_mismatch"] == 0

    integrity = _checks_by_id(card)["backup.integrity"]
    assert integrity["level"] == "green", integrity
    assert "格式异常" not in integrity["detail"]
    assert "manifest_malformed" in backup


def test_health_card_missing_vault_unavailable_red(tmp_path: Path) -> None:
    """--vault-root 指向不存在目录 → backup.available=false、计数字段 null、overall 红。"""
    repo, vault = _make_health_repo(tmp_path, "hc_missing")
    assert not vault.exists()

    proc = _run_health(repo, vault)
    assert proc.returncode == 1, proc.stderr
    card = _json_stdout(proc, "health_card/missing_vault")

    assert card["status"] == "red"
    assert card["exit_code"] == 1
    backup = card["backup"]
    assert backup["available"] is False
    assert backup["reason"]
    for key in (
        "latest_date",
        "age_hours",
        "manifest_entries",
        "manifest_malformed",
        "manifest_missing",
        "size_mismatch",
        "manifest_bytes",
        "total_bytes",
        "total_human",
        "files_count",
    ):
        assert backup[key] is None, "%s 应为 null，实际 %r" % (key, backup[key])
    assert _checks_by_id(card)["backup.readable"]["level"] == "red"


def test_health_card_normal_fixture_json_contract(tmp_path: Path) -> None:
    """正常备份夹具 → 合法 JSON，含 schema/generated_at/status/checks 且三块齐全。"""
    repo, vault = _make_health_repo(tmp_path, "hc_ok")
    day = _make_vault(
        vault,
        {"a.bundle": b"A" * 100, "workspace-docs.zip": b"Z" * 50},
    )
    manifest_size = (day / "MANIFEST.txt").stat().st_size

    proc = _run_health(repo, vault)
    assert proc.returncode in (0, 1), proc.stderr
    card = _json_stdout(proc, "health_card/normal")

    assert card["schema"] == "governance_health_card/v1"
    dt.datetime.fromisoformat(card["generated_at"])
    assert card["status"] in {"green", "yellow", "red"}
    assert card["exit_code"] == (1 if card["status"] == "red" else 0)

    # 三块都在，且各自是 dict
    for block in ("task_audit", "backup", "repo"):
        assert isinstance(card[block], dict), block
        assert "available" in card[block]

    # 17 项检查是扁平列表，字段固定（新增 worktree TTL / 文档索引 / 数据根漂移后）
    assert isinstance(card["checks"], list) and card["checks"]
    for check in card["checks"]:
        assert set(check) == {"id", "level", "detail"}
        assert check["level"] in {"green", "yellow", "red"}
        assert isinstance(check["detail"], str) and check["detail"]
    assert "backup.readable" in _checks_by_id(card)
    assert "repo.readable" in _checks_by_id(card)
    assert "task_audit.readable" in _checks_by_id(card)

    backup = card["backup"]
    assert backup["available"] is True
    assert backup["latest_date"] == day.name
    assert backup["manifest_entries"] == 2
    assert backup["manifest_malformed"] == 0
    assert backup["manifest_missing"] == 0
    assert backup["size_mismatch"] == 0
    assert backup["files_count"] == 3  # 2 个实体 + MANIFEST.txt
    assert backup["manifest_bytes"] == 150
    assert backup["total_bytes"] == 150 + manifest_size


def test_health_card_task_audit_block_contract(tmp_path: Path) -> None:
    """造一份账本 + 摘要 + EXIT_SEMANTICS → task_audit 可读，7 项审计检查按契约出齐。"""
    repo, vault = _make_health_repo(tmp_path, "hc_audit")
    _make_vault(vault, {"a.bundle": b"A" * 16})

    module = repo / "agent_platform" / "tools" / "task_exit_audit.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(
        'EXIT_SEMANTICS = {0: "正常", 267011: "从未运行"}\n',
        encoding="utf-8",
    )

    audit = repo / "portal" / "data" / "task_exit_audit"
    audit.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    records = [
        {
            "at": stamp,
            "name": "task_ok",
            "state": "Ready",
            "last_result": 0,
            "exit_semantics": "正常",
            "last_run": stamp,
        },
        {
            "at": stamp,
            "name": "task_never",
            "state": "Ready",
            "last_result": 267011,
            "exit_semantics": "从未运行",
            "last_run": "",
        },
    ]
    (audit / "task_exit_ledger.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n",
        encoding="utf-8",
    )
    summary = {
        "generated_at": stamp,
        "total_scanned": 2,
        "ok": 1,
        "failed": 0,
        "disabled": 0,
        "never_run": 1,
        "failures": [],
    }
    (audit / "task_exit_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False), encoding="utf-8"
    )

    proc = _run_health(repo, vault)
    assert proc.returncode == 1, proc.stderr  # repo 仍不可读 → 整体红
    card = _json_stdout(proc, "health_card/audit")
    task_audit = card["task_audit"]

    assert task_audit["available"] is True
    assert task_audit["total_scanned"] == 2
    assert task_audit["ok"] == 1
    assert task_audit["never_run"] == 1
    assert task_audit["failed"] == 0
    assert task_audit["running"] == 0
    assert task_audit["counts_source"] == "ledger_latest_batch"
    assert task_audit["summary_available"] is True
    assert task_audit["summary_matches_ledger"] is True
    assert task_audit["summary_count_drift"] == {}

    checks = _checks_by_id(card)
    for check_id in (
        "task_audit.readable",
        "task_audit.failures",
        "task_audit.unknown",
        "task_audit.freshness",
        "task_audit.ledger_integrity",
        "task_audit.summary_sync",
        "task_audit.semantics",
        "backup.readable",
    ):
        assert checks[check_id]["level"] == "green", checks[check_id]
    assert checks["repo.readable"]["level"] == "red"
    assert card["status"] == "red"
    assert card["exit_code"] == 1


def test_health_card_out_not_directory_exit2(tmp_path: Path) -> None:
    """--out 指向一个已存在的文件 → 写盘失败，exit 2，且 stdout 不冒充成功卡片。"""
    repo, vault = _make_health_repo(tmp_path, "hc_out")
    _make_vault(vault, {"a.bundle": b"A" * 8})
    blocker = tmp_path / "hc_out_blocker"
    blocker.write_text("not a directory", encoding="utf-8")

    proc = _run_health(repo, vault, "--out", str(blocker))
    assert proc.returncode == 2, "stdout=%r\nstderr=%r" % (proc.stdout, proc.stderr)
    assert proc.stdout.strip() == ""


# ---------------------------------------------------------------------------
# 2b) gen_health_card.py · data.root_drift（两期 data_manifest.json 对比）
# ---------------------------------------------------------------------------
DATA_ROOT = r"C:\fixture\data"
DIRS_BASE = [("tdx", 400, 400_000), ("tushare", 500, 500_000), ("ashare", 100, 100_000)]
SHA_A = "a" * 64
SHA_B = "b" * 64


def _days_ago(days: int) -> str:
    return (dt.date.today() - dt.timedelta(days=days)).isoformat()


def _data_manifest(
    *,
    day: str,
    total_files: int,
    total_bytes: int,
    dirs: list[tuple[str, int, int]],
    catalog_sha: str | None = SHA_A,
    root: str = DATA_ROOT,
    partial: bool = False,
) -> dict:
    """与 tools/gen_data_manifest.py 同形状的清单（字段名照抄真实产物）。"""
    return {
        "generated_at": "%sT07:14:45+08:00" % day,
        "root": root,
        "total_files": total_files,
        "total_bytes": total_bytes,
        "dirs": [
            {
                "name": name,
                "files": files,
                "bytes": size,
                "latest_mtime": "%sT06:00:00+08:00" % day,
                "scanned": not partial,
            }
            for name, files, size in dirs
        ],
        "catalog": {
            "path": root + r"\catalog.json",
            "present": catalog_sha is not None,
            "size": 277915,
            "mtime": "%sT06:26:43+08:00" % day,
            "sha256": catalog_sha,
        },
        "schema": "data-manifest/v1",
        "scan": {
            "elapsed_sec": 1.45,
            "max_depth": 0,
            "time_budget_sec": 120.0,
            "partial": partial,
            "top_dirs": len(dirs),
            "root_files": {"files": 0, "bytes": 0, "latest_mtime": None},
            "errors": 0,
            "skipped_symlinks": 0,
            "truncated_dirs": 0,
        },
    }


def _write_data_manifests(vault: Path, days: dict[str, dict | str]) -> None:
    """把清单写进 <vault>/<yyyy-MM-dd>/data_manifest.json；值是字符串时按原文写（造坏 JSON）。"""
    for day, payload in days.items():
        target = vault / day
        target.mkdir(parents=True, exist_ok=True)
        text = (
            payload
            if isinstance(payload, str)
            else json.dumps(payload, ensure_ascii=False, indent=2)
        )
        (target / "data_manifest.json").write_text(text, encoding="utf-8")


def _drift_card(tmp_path: Path, name: str, days: dict[str, dict | str]) -> dict:
    """造两份（或一份）清单并跑一次健康卡，返回完整 card。"""
    repo, vault = _make_health_repo(tmp_path, name)
    vault.mkdir(parents=True, exist_ok=True)
    _write_data_manifests(vault, days)
    proc = _run_health(repo, vault)
    assert proc.returncode in (0, 1), proc.stderr
    return _json_stdout(proc, "health_card/" + name)


def test_data_root_drift_identical_manifests_is_green(tmp_path: Path) -> None:
    """两期清单完全一致 → data.root_drift 绿、status=consistent、计数相等。"""
    newer, older = _days_ago(1), _days_ago(2)
    card = _drift_card(
        tmp_path,
        "hc_drift_same",
        {
            older: _data_manifest(
                day=older, total_files=1000, total_bytes=1_000_000, dirs=DIRS_BASE
            ),
            newer: _data_manifest(
                day=newer, total_files=1000, total_bytes=1_000_000, dirs=DIRS_BASE
            ),
        },
    )

    drift = card["data"]["root_drift"]
    assert drift["available"] is True
    assert drift["status"] == "consistent"
    assert drift["level"] == "green"
    assert drift["manifests_found"] == 2
    assert drift["latest_date"] == newer
    assert drift["previous_date"] == older
    assert drift["counts"]["delta"] == {"files": 0, "bytes": 0, "dirs": 0}
    assert drift["catalog"]["status"] == "same"
    assert drift["root_changed"] is False
    assert drift["dirs_added"] == [] and drift["dirs_removed"] == []

    check = _checks_by_id(card)["data.root_drift"]
    assert check["level"] == "green", check
    assert "无漂移" in check["detail"]


def test_data_root_drift_small_change_is_yellow(tmp_path: Path) -> None:
    """+1% 文件 / +100KB → 黄，detail 点名变化最大的目录，不触发 20% 阈值。"""
    newer, older = _days_ago(1), _days_ago(2)
    card = _drift_card(
        tmp_path,
        "hc_drift_small",
        {
            older: _data_manifest(
                day=older, total_files=1000, total_bytes=1_000_000, dirs=DIRS_BASE
            ),
            newer: _data_manifest(
                day=newer,
                total_files=1010,
                total_bytes=1_100_000,
                dirs=[("tdx", 400, 400_000), ("tushare", 510, 600_000), ("ashare", 100, 100_000)],
            ),
        },
    )

    drift = card["data"]["root_drift"]
    assert drift["available"] is True
    assert drift["status"] == "drift"
    assert drift["level"] == "yellow"
    assert drift["counts"]["delta"]["files"] == 10
    assert drift["triggers"] == []
    assert drift["file_delta_ratio"] == 0.01
    assert drift["dirs_changed"][0]["name"] == "tushare"

    check = _checks_by_id(card)["data.root_drift"]
    assert check["level"] == "yellow", check
    assert "tushare 500→510" in check["detail"]


def test_data_root_drift_file_drop_over_threshold_is_red(tmp_path: Path) -> None:
    """文件数 -60%（>20%）→ 红，triggers 点名 file_ratio。"""
    newer, older = _days_ago(1), _days_ago(2)
    card = _drift_card(
        tmp_path,
        "hc_drift_big",
        {
            older: _data_manifest(
                day=older, total_files=1000, total_bytes=1_000_000, dirs=DIRS_BASE
            ),
            newer: _data_manifest(
                day=newer,
                total_files=400,
                total_bytes=400_000,
                dirs=[("tdx", 400, 400_000), ("tushare", 0, 0), ("ashare", 0, 0)],
            ),
        },
    )

    drift = card["data"]["root_drift"]
    assert drift["status"] == "major_drift"
    assert drift["level"] == "red"
    assert drift["triggers"] == ["file_ratio"]
    assert drift["counts"]["delta"]["files"] == -600
    assert drift["catalog"]["status"] == "same"

    check = _checks_by_id(card)["data.root_drift"]
    assert check["level"] == "red", check
    assert "剧烈漂移" in check["detail"]
    assert "-60.00%" in check["detail"]


def test_data_root_drift_dir_count_change_is_red(tmp_path: Path) -> None:
    """总量不变、顶层目录 2 → 3 → 红，detail 点名新增目录。"""
    newer, older = _days_ago(1), _days_ago(2)
    card = _drift_card(
        tmp_path,
        "hc_drift_dirs",
        {
            older: _data_manifest(
                day=older,
                total_files=900,
                total_bytes=900_000,
                dirs=[("tdx", 400, 400_000), ("tushare", 500, 500_000)],
            ),
            newer: _data_manifest(
                day=newer,
                total_files=900,
                total_bytes=900_000,
                dirs=[("tdx", 400, 400_000), ("tushare", 500, 500_000), ("cnopendata", 0, 0)],
            ),
        },
    )

    drift = card["data"]["root_drift"]
    assert drift["level"] == "red"
    assert drift["triggers"] == ["dir_count"]
    assert drift["dirs_added"] == ["cnopendata"]
    check = _checks_by_id(card)["data.root_drift"]
    assert check["level"] == "red", check
    assert "cnopendata" in check["detail"]


def test_data_root_drift_catalog_swap_is_yellow(tmp_path: Path) -> None:
    """总量不动但 catalog.json 的 sha256 变了 → 黄（被换）。"""
    newer, older = _days_ago(1), _days_ago(2)
    card = _drift_card(
        tmp_path,
        "hc_drift_catalog",
        {
            older: _data_manifest(
                day=older, total_files=1000, total_bytes=1_000_000, dirs=DIRS_BASE
            ),
            newer: _data_manifest(
                day=newer,
                total_files=1000,
                total_bytes=1_000_000,
                dirs=DIRS_BASE,
                catalog_sha=SHA_B,
            ),
        },
    )

    drift = card["data"]["root_drift"]
    assert drift["level"] == "yellow"
    assert drift["catalog"]["status"] == "changed"
    check = _checks_by_id(card)["data.root_drift"]
    assert check["level"] == "yellow", check
    assert "已被换" in check["detail"]


def test_data_root_drift_single_manifest_unavailable_yellow(tmp_path: Path) -> None:
    """只有一份清单 → available=false、计数全 null、黄（不是绿）。"""
    newer = _days_ago(1)
    card = _drift_card(
        tmp_path,
        "hc_drift_single",
        {newer: _data_manifest(day=newer, total_files=1000, total_bytes=1_000_000, dirs=DIRS_BASE)},
    )

    drift = card["data"]["root_drift"]
    assert drift["available"] is False
    assert drift["status"] == "insufficient_history"
    assert drift["level"] == "yellow"
    assert drift["manifests_found"] == 1
    for side in drift["counts"].values():
        assert set(side.values()) == {None}, side

    check = _checks_by_id(card)["data.root_drift"]
    assert check["level"] == "yellow", check
    assert "至少要两份" in check["detail"]


def test_data_root_drift_both_unreadable_is_red(tmp_path: Path) -> None:
    """两份清单都解析失败 → available=false + 红。"""
    card = _drift_card(
        tmp_path,
        "hc_drift_broken",
        {_days_ago(1): "{broken", _days_ago(2): "{broken"},
    )

    drift = card["data"]["root_drift"]
    assert drift["available"] is False
    assert drift["status"] == "unreadable"
    assert drift["level"] == "red"
    for side in drift["counts"].values():
        assert set(side.values()) == {None}, side

    check = _checks_by_id(card)["data.root_drift"]
    assert check["level"] == "red", check
    assert "都读不到" in check["detail"]


def test_data_root_drift_no_manifest_is_red(tmp_path: Path) -> None:
    """备份根下一个 data_manifest.json 都没有 → 红（这项检查要能被发现停摆）。"""
    repo, vault = _make_health_repo(tmp_path, "hc_drift_none")
    vault.mkdir(parents=True, exist_ok=True)

    card = _json_stdout(_run_health(repo, vault), "health_card/no_manifest")
    drift = card["data"]["root_drift"]
    assert drift["available"] is False
    assert drift["status"] == "no_manifest"
    assert drift["level"] == "red"
    assert _checks_by_id(card)["data.root_drift"]["level"] == "red"


# ---------------------------------------------------------------------------
# 3) gen_docs_index.py
# ---------------------------------------------------------------------------
def _write_doc(path: Path, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# " + title + "\n\n正文。\n", encoding="utf-8")


def _make_docs_workspace(tmp_path: Path, name: str = "docs_ws") -> Path:
    """假工作区：老田的AI助手/docs/{plans,audit,ops} + 根 .md + 既有 DOCS_INDEX.md。

    预置一份最小索引（真实工作区本来就长期存在 DOCS_INDEX.md）。这不是为了
    掩盖问题：工具对“首次从零生成”确实不自洽（首轮产物不含自己的行，紧接着
    --check 返回 1），该缺陷在交付报告里单独说明；这里覆盖的是文档承诺的
    “既有索引 → 生成/校验/幂等”契约。
    """
    workspace = tmp_path / name
    project = workspace / PROJECT_DIRNAME
    for parts in (("docs", "plans"), ("docs", "audit"), ("docs", "ops")):
        (project.joinpath(*parts)).mkdir(parents=True, exist_ok=True)
    (project / "deliverables").mkdir(parents=True, exist_ok=True)
    (workspace / "deliverables").mkdir(parents=True, exist_ok=True)

    _write_doc(project / "docs" / "plans" / "2026-10-01_方案.md", "方案一")
    _write_doc(project / "docs" / "audit" / "2026-10-02_审计.md", "审计一")
    _write_doc(project / "docs" / "ops" / "2026-10-03_操作单.md", "操作单一")
    _write_doc(workspace / "README.md", "根 README")

    # 预置索引的标题必须与生成后一致（工具读首行 # 作为标题），只让生成时间被改写。
    (workspace / "DOCS_INDEX.md").write_text(
        "# 文档索引 DOCS_INDEX\n"
        "\n"
        "- 生成时间：2026-10-01 00:00（脚本自动生成，手工编辑请写在「人工备注」段）\n",
        encoding="utf-8",
        newline="\n",
    )
    return workspace


def _run_docs(workspace: Path, *args: str) -> subprocess.CompletedProcess:
    return _run_py(DOCS, "--workspace", str(workspace), *args)


def _generate_index(workspace: Path) -> bytes:
    proc = _run_docs(workspace)
    assert proc.returncode == 0, "生成失败: %r" % (proc.stdout, proc.stderr)
    index = workspace / "DOCS_INDEX.md"
    assert index.is_file()
    return index.read_bytes()


def test_docs_index_generate_then_check_is_clean(tmp_path: Path) -> None:
    """生成后立刻 --check 应一致 → exit 0。"""
    workspace = _make_docs_workspace(tmp_path)

    first = _generate_index(workspace)
    assert first.startswith("# 文档索引 DOCS_INDEX".encode("utf-8"))
    assert not first.startswith(b"\xef\xbb\xbf")  # UTF-8 无 BOM
    assert b"\r\n" not in first  # LF 换行

    proc = _run_docs(workspace, "--check")
    assert proc.returncode == 0, "stdout=%r\nstderr=%r" % (proc.stdout, proc.stderr)
    assert "一致" in proc.stdout


def test_docs_index_check_detects_new_doc(tmp_path: Path) -> None:
    """索引生成后新增一份文档 → --check exit 1，并报“新增”。"""
    workspace = _make_docs_workspace(tmp_path, "docs_ws_drift")
    _generate_index(workspace)

    new_doc = (
        workspace
        / PROJECT_DIRNAME
        / "docs"
        / "plans"
        / "2026-10-04_新方案.md"
    )
    _write_doc(new_doc, "新方案")

    proc = _run_docs(workspace, "--check")
    assert proc.returncode == 1, "stdout=%r\nstderr=%r" % (proc.stdout, proc.stderr)
    assert "新增" in proc.stdout
    assert "2026-10-04_新方案.md" in proc.stdout


def test_docs_index_generation_is_byte_idempotent(tmp_path: Path) -> None:
    """连续两次生成字节完全相同（幂等）。

    唯一的时间戳精确到分钟；若测试恰好从 :45 之后起步，先等到下一分钟再跑，
    避免两次生成跨分钟造成假红。
    """
    workspace = _make_docs_workspace(tmp_path, "docs_ws_idem")
    if dt.datetime.now().second >= 45:
        time.sleep(61 - dt.datetime.now().second)

    first = _generate_index(workspace)
    second = _generate_index(workspace)
    assert first == second


# ---------------------------------------------------------------------------
# 4) daily_ops.ps1（PowerShell 编排，只测退出码 / JSON 契约）
# ---------------------------------------------------------------------------
def _powershell_exe() -> str:
    exe = shutil.which("powershell.exe") or shutil.which("powershell")
    if not exe:
        pytest.skip("找不到 powershell.exe，跳过 daily_ops 契约测试")
    return exe


def _run_daily(repo: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            _powershell_exe(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(DAILY),
            "-Repo",
            str(repo),
            "-Json",
            *extra,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        cwd=str(REPO),
        check=False,
    )


def test_daily_ops_red_contract(tmp_path: Path) -> None:
    """健康卡红（假仓库不可读）+ 备份 ok → 总判定红、exit 1、stdout 单一 JSON。

    -SkipParity：本用例只验健康卡+备份两块的契约；文档一致性块在
    test_daily_ops_parity_block 里单独验（夹具里没有 deliverables 双根，
    不跳过会让 parity 工具 exit 2 把整体抬到 2）。
    """
    workspace = tmp_path / "daily_red"
    repo = workspace / "repo"
    repo.mkdir(parents=True)
    # daily_ops 把备份仓固定推导为 <repo 的父目录>/_vault
    _make_vault(workspace / "_vault", {"aios-20261022.bundle": b"B" * 96})

    proc = _run_daily(repo, "-SkipParity")
    assert proc.returncode == 1, "stdout=%r\nstderr=%r" % (proc.stdout[:2000], proc.stderr[:2000])
    payload = _json_stdout(proc, "daily_ops/red")

    assert payload["schema"] == "daily_ops/v1"
    assert payload["status"] == "red"
    assert payload["exit_code"] == 1
    assert payload["skip_parity"] is True
    assert payload["parity"] is None, "跳过时 parity 原始 JSON 必须为 null"
    assert payload["summary"]["parity"]["skipped"] is True
    assert payload["summary"]["health"]["status"] == "red"
    assert payload["summary"]["health"]["usable"] is True
    assert payload["summary"]["health"]["exit_code"] == 1
    assert payload["summary"]["backup"]["result"] == "ok"
    assert payload["summary"]["backup"]["usable"] is True
    assert payload["summary"]["backup"]["exit_code"] == 0
    assert payload["summary"]["unable"] == []
    # 合并后的 JSON 里带两份原始工具输出
    assert payload["health_card"]["status"] == "red"
    assert payload["backup_verify"]["summary"]["ok"] == 1


def test_daily_ops_parity_block(tmp_path: Path) -> None:
    """文档一致性块：双根同名一致 → consistent；healthy 时它不改变红判定。"""
    workspace = tmp_path / "daily_parity"
    repo = workspace / "repo"
    repo.mkdir(parents=True)
    _make_vault(workspace / "_vault", {"aios-20261022.bundle": b"B" * 96})

    a_root = workspace / "deliverables"
    b_root = repo / "deliverables"
    for root in (a_root, b_root):
        root.mkdir(parents=True, exist_ok=True)
        (root / "same-doc.md").write_text("# 同一份\n", encoding="utf-8")

    proc = _run_daily(repo, "-ParityA", str(a_root), "-ParityB", str(b_root))
    payload = _json_stdout(proc, "daily_ops/parity")
    assert payload["skip_parity"] is False
    assert payload["summary"]["parity"]["skipped"] is False
    assert payload["summary"]["parity"]["verdict"] == "consistent"
    assert payload["summary"]["parity"]["intersection"] == 1
    assert payload["summary"]["parity"]["diverged"] == 0
    assert payload["summary"]["parity"]["yellow"] is False
    assert payload["parity"]["verdict"] == "consistent"

    # 制造漂移：B 侧追加一行 → drifted 变黄，但不把整体判成 error
    (b_root / "same-doc.md").write_text("# 同一份\n\n新增一行\n", encoding="utf-8")
    proc = _run_daily(repo, "-ParityA", str(a_root), "-ParityB", str(b_root))
    payload = _json_stdout(proc, "daily_ops/parity-drift")
    assert payload["summary"]["parity"]["verdict"] == "drifted"
    assert payload["summary"]["parity"]["diverged"] == 1
    assert payload["summary"]["parity"]["yellow"] is True
    assert payload["status"] != "error", "漂移是黄，不能升级成工具错误"


def test_daily_ops_tool_error_contract(tmp_path: Path) -> None:
    """备份仓不存在 → verify_backups exit 2 → 总判定 error、exit 2（fail-closed）。"""
    workspace = tmp_path / "daily_error"
    repo = workspace / "repo"
    repo.mkdir(parents=True)
    assert not (workspace / "_vault").exists()

    proc = _run_daily(repo, "-SkipParity")
    assert proc.returncode == 2, "stdout=%r\nstderr=%r" % (proc.stdout[:2000], proc.stderr[:2000])
    payload = _json_stdout(proc, "daily_ops/error")

    assert payload["schema"] == "daily_ops/v1"
    assert payload["status"] == "error"
    assert payload["exit_code"] == 2
    assert payload["summary"]["backup"]["exit_code"] == 2
    assert payload["summary"]["backup"]["usable"] is False
    assert payload["summary"]["unable"], "应记录“无法判定”原因"
    assert any("exit 2" in item for item in payload["summary"]["unable"])


def test_daily_ops_rejects_log_outside_workspace(tmp_path: Path) -> None:
    """-Log 只允许写工作区根：指向临时目录必须 exit 2 且不落文件。"""
    workspace = tmp_path / "daily_log"
    repo = workspace / "repo"
    repo.mkdir(parents=True)
    _make_vault(workspace / "_vault", {"a.bundle": b"A" * 8})
    log_path = tmp_path / "outside.log"

    proc = _run_daily(repo, "-Log", str(log_path))
    assert proc.returncode == 2, "stdout=%r\nstderr=%r" % (proc.stdout[:2000], proc.stderr[:2000])
    assert not log_path.exists()
