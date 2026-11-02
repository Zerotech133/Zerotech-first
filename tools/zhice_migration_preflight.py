#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""智策 DB 迁移链只读 preflight（ACL-104 第一阶段 / BUG-001 止血工具）。

目的
----
8105 重启/发布前阻断"DB 迁移链与仓库不一致"的部署炸弹：
- 静态比对外部 zhice 仓 `code/migrations/versions` 的迁移集合与 alembic heads
  是否为期望版本（默认 0049，单头）；要求 0046–0049 四个迁移存在且首尾成链；
- 可选 `--db`：只读查询 PG（默认 docker exec psql）当前 `alembic_version` 并与期望比对；
  不一致 → exit 1（阻断 8105 重启，直到 ACL-105 完成升级或人工处置）。

只读红线（本工具绝不执行以下动作）
----------------------------------
- 不 import / 不执行任何迁移模块（只做 `ast.parse` 静态读取）；
- 不执行 alembic / upgrade / downgrade；
- 不写 DB：DB 侧仅执行 `SELECT version_num FROM alembic_version`；
- 不启停服务、不写任何文件。

用法
----
    python -X utf8 tools/zhice_migration_preflight.py                # 仅静态检查
    python -X utf8 tools/zhice_migration_preflight.py --json         # 机器可读回执
    python -X utf8 tools/zhice_migration_preflight.py --db --json    # 追加只读 DB revision 比对
    python -X utf8 tools/zhice_migration_preflight.py --versions-dir D:\\tmp\\versions --expected 9999
    python -X utf8 tools/zhice_migration_preflight.py --require-range 0046-0050   # 演练缺失文件

退出码
------
    0 = 全部检查通过（静态；若 --db 则含 DB revision 一致）
    1 = preflight 失败（迁移缺口 / heads 不符 / DB 不一致 / DB 检查失败 / 目录不可读 /
        --require-range 值非法——全部 fail-visible，宁拦不错放）
    2 = argparse 用法错误（未知参数等）

环境变量
--------
    ZHICE_MIGRATIONS_DIR  覆盖默认迁移目录
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BEIJING = timezone(timedelta(hours=8))
REPO = Path(__file__).resolve().parents[1]
DEFAULT_VERSIONS_DIR = Path(os.environ.get(
    "ZHICE_MIGRATIONS_DIR",
    r"C:\项目\zhice-os-v2-windows-20260920\code\migrations\versions",
))
DEFAULT_EXPECTED = "0049"
DEFAULT_REQUIRE_RANGE = "0046-0049"
DEFAULT_DB_CONTAINER = "zhice-db"
DEFAULT_DB_NAME = "zhice_os_v2"
DEFAULT_DB_USER = "zhice"
DB_SQL = "SELECT version_num FROM alembic_version;"
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.-]+$")
_MIGRATION_FIELDS = ("revision", "down_revision", "branch_labels", "depends_on")


# --------------------------------------------------------------------------- #
# 基础
# --------------------------------------------------------------------------- #
def now_iso() -> str:
    return datetime.now(BEIJING).isoformat(timespec="seconds")


def _mtime_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, BEIJING).isoformat(timespec="seconds")


def _literal(node: ast.AST):
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError) as exc:
        raise ValueError("非字面量: %s" % exc)


def _as_list(value) -> list[str]:
    """revision/down_revision/branch_labels 统一成 list[str]。"""
    if value is None:
        return []
    if isinstance(value, (str, int)):
        return [str(value)]
    if isinstance(value, (tuple, list)):
        return [str(v) for v in value if v is not None]
    raise ValueError("未知迁移字段类型: %r" % (type(value),))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# 静态解析（只 ast.parse，不 import 迁移模块）
# --------------------------------------------------------------------------- #
def parse_migration(path: Path, with_sha: bool = False) -> dict:
    raw = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(raw, filename=str(path))
    values: dict = {}
    for node in tree.body:
        target_name = None
        value_node = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target_name = node.targets[0].id
            value_node = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            target_name = node.target.id
            value_node = node.value
        if target_name in _MIGRATION_FIELDS:
            values[target_name] = _literal(value_node)
    info = {
        "file": path.name,
        "size": path.stat().st_size,
        "mtime": _mtime_iso(path.stat().st_mtime),
        "revision": _as_list(values.get("revision")),
        "down_revision": _as_list(values.get("down_revision")),
        "branch_labels": _as_list(values.get("branch_labels")),
        "depends_on": _as_list(values.get("depends_on")),
        "has_downgrade": any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "downgrade"
            for node in tree.body
        ),
    }
    if len(info["revision"]) != 1:
        raise ValueError("revision 必须唯一非空（实际 %r）" % (info["revision"],))
    if with_sha:
        info["sha256"] = _sha256_file(path)
    return info


def read_migrations(versions_dir: Path, with_sha: bool = False) -> tuple[dict, list, int]:
    findings: list[dict] = []
    revisions: dict = {}
    files = sorted(p for p in versions_dir.glob("*.py") if p.is_file())
    for path in files:
        try:
            info = parse_migration(path, with_sha=with_sha)
        except (OSError, SyntaxError, ValueError) as exc:
            findings.append({"code": "migration_parse_error",
                             "detail": "%s: %s" % (path.name, exc)})
            continue
        rev = info["revision"][0]
        if rev in revisions:
            findings.append({"code": "duplicate_revision",
                             "detail": "revision=%s 同时出现在 %s 与 %s"
                                       % (rev, revisions[rev]["file"], info["file"])})
            continue
        revisions[rev] = info
    return revisions, findings, len(files)


def compute_heads(revisions: dict) -> list[str]:
    referenced: set[str] = set()
    for info in revisions.values():
        for down in info["down_revision"]:
            referenced.add(down)
    return sorted(rev for rev in revisions if rev not in referenced)


def parse_require_range(spec: str) -> list[str]:
    """'0046-0049' → ['0046','0047','0048','0049']（保留零填充宽度）。"""
    m = re.fullmatch(r"\s*(\d{3,6})\s*-\s*(\d{3,6})\s*", spec or "")
    if not m:
        raise ValueError("--require-range 需形如 0046-0049（实际 %r）" % (spec,))
    start_s, end_s = m.group(1), m.group(2)
    width = max(len(start_s), len(end_s))
    start, end = int(start_s), int(end_s)
    if start > end:
        raise ValueError("--require-range 起始 > 结束（%s）" % spec)
    if end - start > 200:
        raise ValueError("--require-range 跨度过大（%s）" % spec)
    return [str(n).zfill(width) for n in range(start, end + 1)]


def check_static(revisions: dict, heads: list[str], expected: str,
                 required: list[str], parse_findings: list[dict],
                 warnings: list[dict]) -> list[dict]:
    findings = list(parse_findings)

    # down_revision 目标必须存在于目录内（None/根迁移除外）
    for rev, info in sorted(revisions.items()):
        for down in info["down_revision"]:
            if down not in revisions:
                findings.append({"code": "down_revision_missing",
                                 "detail": "revision=%s（%s）的 down_revision=%s 不在目录内"
                                           % (rev, info["file"], down)})

    if not revisions:
        findings.append({"code": "no_migrations", "detail": "目录内未解析到任何迁移文件"})
        return findings

    # 期望版本必须存在且为唯一 head
    if expected not in revisions:
        findings.append({"code": "expected_not_found",
                         "detail": "期望 head=%s 不在迁移目录（现有 head=%s）"
                                   % (expected, ",".join(heads) or "无")})
    if heads != [expected]:
        findings.append({"code": "head_mismatch",
                         "detail": "期望单头=%s，实际 heads=%s（目录 %d 个 revision）"
                                   % (expected, ",".join(heads) or "无", len(revisions))})

    # 必需版本存在 + 首尾成链
    missing = [r for r in required if r not in revisions]
    if missing:
        findings.append({"code": "required_missing",
                         "detail": "缺少必需迁移: %s" % ",".join(missing)})
    for prev, cur in zip(required, required[1:]):
        if cur not in revisions or prev not in revisions:
            continue
        downs = revisions[cur]["down_revision"]
        if downs != [prev]:
            findings.append({"code": "required_chain_break",
                             "detail": "%s.down_revision=%s，期望=%s（首尾成链）"
                                       % (cur, ",".join(downs) or "None", prev)})
    # 区间首迁移的 down 必须落在期望的上一版（存在即可，例如 0045）
    if required and required[0] in revisions:
        first_downs = revisions[required[0]]["down_revision"]
        if len(first_downs) != 1:
            findings.append({"code": "required_chain_break",
                             "detail": "%s.down_revision=%s，期望单一前驱"
                                       % (required[0], ",".join(first_downs) or "None")})
        elif first_downs[0] not in revisions:
            findings.append({"code": "required_chain_break",
                             "detail": "%s.down_revision=%s 不在目录内"
                                       % (required[0], first_downs[0])})

    # 文件名前缀与 revision 一致性（告警级；不改变 exit code，除非后续 --strict）
    for rev, info in sorted(revisions.items()):
        stem = Path(info["file"]).stem
        if not stem.startswith(rev):
            warnings.append({"code": "filename_revision_mismatch",
                             "detail": "%s: 文件名前缀 != revision=%s" % (info["file"], rev)})
    for rev in (expected, *required):
        info = revisions.get(rev)
        if info and info.get("branch_labels"):
            warnings.append({"code": "branch_labels_present",
                             "detail": "%s branch_labels=%s（多分支头需人工确认）"
                                       % (rev, ",".join(info["branch_labels"]))})
        if info and info.get("depends_on"):
            warnings.append({"code": "depends_on_present",
                             "detail": "%s depends_on=%s（跨链依赖需人工确认）"
                                       % (rev, ",".join(info["depends_on"]))})
        if info and not info.get("has_downgrade"):
            warnings.append({"code": "downgrade_missing",
                             "detail": "%s（%s）未检测到 downgrade()，ACL-105 回滚预案需人工确认"
                                       % (rev, info["file"])})
    return findings


# --------------------------------------------------------------------------- #
# 只读 DB revision
# --------------------------------------------------------------------------- #
def _validate_token(name: str, label: str) -> str:
    if not _SAFE_TOKEN.match(name or ""):
        raise ValueError("%s 含非法字符: %r" % (label, name))
    return name


def db_current_docker(container: str, db: str, user: str, timeout: float) -> dict:
    container = _validate_token(container, "--db-container")
    db = _validate_token(db, "--db-name")
    user = _validate_token(user, "--db-user")
    cmd = ["docker", "exec", container, "psql", "-U", user, "-d", db, "-t", "-A", "-c", DB_SQL]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError("docker exec psql rc=%d: %s"
                           % (proc.returncode, (proc.stderr or proc.stdout or "").strip()[:300]))
    rows = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    return {"mode": "docker", "container": container, "db": db, "user": user, "rows": rows}


def db_current_dsn(dsn: str, timeout: float) -> dict:
    """psycopg2/psycopg 只读连接；只执行一条 SELECT。依赖不可用=失败（fail-visible）。"""
    conn = None
    try:
        try:
            import psycopg2  # type: ignore
            conn = psycopg2.connect(dsn, connect_timeout=int(timeout))
            conn.set_session(readonly=True, autocommit=True)
        except ImportError:
            import psycopg  # type: ignore
            conn = psycopg.connect(dsn, connect_timeout=int(timeout), autocommit=True)
            try:
                conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            except Exception:  # noqa: BLE001 - 兼容性降级：仍只执行 SELECT，不写库
                pass
        cur = conn.cursor()
        cur.execute(DB_SQL)
        rows = [str(r[0]).strip() for r in cur.fetchall() if r and str(r[0]).strip()]
        cur.close()
        return {"mode": "dsn", "rows": rows}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def check_db(args, expected: str) -> dict:
    requested = bool(args.db)
    payload = {"requested": requested, "ok": None, "current": None,
               "mode": None, "error": None, "rows": []}
    if not requested:
        return payload
    try:
        if args.db_dsn:
            payload["mode"] = "dsn"
            info = db_current_dsn(args.db_dsn, args.db_timeout)
        else:
            payload["mode"] = "docker"
            info = db_current_docker(args.db_container, args.db_name,
                                     args.db_user, args.db_timeout)
        rows = info.get("rows") or []
        payload["rows"] = rows
        payload["current"] = rows[0] if len(rows) == 1 else None
        payload["mode"] = info.get("mode")
    except Exception as exc:  # noqa: BLE001 - fail-visible：任何 DB 侧失败都必须显式失败
        payload["ok"] = False
        payload["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:400])
        return payload
    if len(rows) != 1:
        payload["ok"] = False
        payload["error"] = "alembic_version 行数=%d（期望 1 行，当前=%r）" % (len(rows), rows)
        return payload
    payload["ok"] = rows[0] == expected
    if not payload["ok"]:
        payload["error"] = "DB current=%s，期望=%s" % (rows[0], expected)
    return payload


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="zhice_migration_preflight.py",
        description="智策 DB 迁移链只读 preflight（不 import 迁移模块/不执行 alembic/不写 DB）",
    )
    ap.add_argument("--versions-dir", default=str(DEFAULT_VERSIONS_DIR),
                    help="迁移目录（默认 %(default)s；env ZHICE_MIGRATIONS_DIR 可覆盖）")
    ap.add_argument("--expected", default=DEFAULT_EXPECTED,
                    help="期望的单头 revision（默认 %(default)s）")
    ap.add_argument("--require-range", default=DEFAULT_REQUIRE_RANGE,
                    help="必需迁移区间 a-b（默认 %(default)s）")
    ap.add_argument("--db", action="store_true",
                    help="追加只读 DB revision 比对（docker exec psql 或 --db-dsn）")
    ap.add_argument("--db-container", default=DEFAULT_DB_CONTAINER, help="docker 容器名")
    ap.add_argument("--db-name", default=DEFAULT_DB_NAME, help="数据库名")
    ap.add_argument("--db-user", default=DEFAULT_DB_USER, help="数据库用户（只读 SELECT）")
    ap.add_argument("--db-dsn", default="", help="可选 DSN（需 psycopg2/psycopg；默认走 docker exec）")
    ap.add_argument("--db-timeout", type=float, default=15.0, help="DB 检查超时秒（默认 15）")
    ap.add_argument("--sha", action="store_true", help="输出必需/期望迁移文件的 sha256")
    ap.add_argument("--json", action="store_true", help="机器可读 JSON 输出（stdout 仅 JSON）")
    return ap


def run(args) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []
    payload: dict = {
        "tool": "zhice_migration_preflight",
        "version": "1.0",
        "generated_at": now_iso(),
        "read_only": True,
        "versions_dir": str(Path(args.versions_dir)),
        "expected": args.expected,
        "require_range": args.require_range,
        "migration_files": 0,
        "parsed": 0,
        "head": None,
        "heads": [],
        "revisions": {},
        "required": [],
        "db": {"requested": False},
        "findings": [],
        "warnings": [],
        "ok": False,
        "rc": 1,
    }
    try:
        required = parse_require_range(args.require_range)
    except ValueError as exc:
        payload["findings"] = [{"code": "bad_require_range", "detail": str(exc)}]
        return payload
    payload["required"] = required

    versions_dir = Path(args.versions_dir)
    if not versions_dir.is_dir():
        payload["findings"] = [{"code": "versions_dir_missing",
                                "detail": "迁移目录不存在或不是目录: %s" % versions_dir}]
        return payload

    revisions, parse_findings, file_count = read_migrations(versions_dir, with_sha=args.sha)
    heads = compute_heads(revisions)
    payload["migration_files"] = file_count
    payload["parsed"] = len(revisions)
    payload["heads"] = heads
    payload["head"] = heads[0] if len(heads) == 1 else None
    payload["revisions"] = {
        rev: {
            "file": info["file"],
            "down_revision": info["down_revision"],
            "size": info["size"],
            "mtime": info["mtime"],
            "has_downgrade": bool(info.get("has_downgrade")),
            **({"sha256": info["sha256"]} if info.get("sha256") else {}),
        }
        for rev, info in sorted(revisions.items())
    }
    errors.extend(check_static(revisions, heads, args.expected, required,
                               parse_findings, warnings))

    db_payload = check_db(args, args.expected)
    payload["db"] = db_payload
    if db_payload["requested"]:
        if db_payload["ok"] is True:
            pass
        elif db_payload["error"] and str(db_payload["error"]).startswith("DB current="):
            errors.append({"code": "db_revision_mismatch", "detail": db_payload["error"]})
        else:
            errors.append({"code": "db_check_failed",
                           "detail": db_payload["error"] or "DB 检查未返回明确结果"})

    payload["findings"] = errors
    payload["warnings"] = warnings
    payload["ok"] = not errors
    payload["rc"] = 0 if payload["ok"] else 1
    return payload


def render_human(payload: dict) -> str:
    lines: list[str] = []
    add = lines.append
    add("== zhice migration preflight（只读）==")
    add("time       : %s" % payload["generated_at"])
    add("versions   : %s" % payload["versions_dir"])
    add("expected   : %s  require=%s" % (payload["expected"], payload["require_range"]))
    db = payload.get("db") or {}
    add("migrations : files=%d parsed=%d heads=%s"
        % (payload["migration_files"], payload["parsed"], ",".join(payload["heads"]) or "无"))
    if db.get("requested"):
        add("db         : mode=%s current=%s ok=%s%s"
            % (db.get("mode"), db.get("current"), db.get("ok"),
               (" error=%s" % db.get("error")) if db.get("error") and not db.get("ok") else ""))
    else:
        add("db         : 未检查（--db 开启只读比对）")
    # 必需链
    req = payload.get("required") or []
    chain = []
    revs = payload.get("revisions") or {}
    for r in req:
        info = revs.get(r)
        chain.append("%s%s" % (r, "(缺失)" if info is None else "→%s" % (",".join(info["down_revision"]) or "None")))
    add("require    : %s" % "  ".join(chain))
    if payload.get("warnings"):
        add("warnings   :")
        for w in payload["warnings"]:
            add("  ⚠ %s: %s" % (w["code"], w["detail"]))
    if payload["findings"]:
        add("findings   :")
        for f in payload["findings"]:
            add("  ✗ %s: %s" % (f["code"], f["detail"]))
        add("PREFLIGHT FAIL（rc=1，阻断 8105 重启/升级前检查）")
    else:
        add("PREFLIGHT PASS（rc=0）")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = run(args)
    except Exception as exc:  # noqa: BLE001 - fail-visible：内部异常也必须非 0
        payload = {"tool": "zhice_migration_preflight", "version": "1.0",
                   "generated_at": now_iso(), "read_only": True,
                   "versions_dir": str(args.versions_dir), "expected": args.expected,
                   "require_range": args.require_range, "migration_files": 0,
                   "parsed": 0, "head": None, "heads": [], "revisions": {},
                   "required": [], "db": {"requested": bool(args.db)}, "findings":
                   [{"code": "internal_error",
                     "detail": "%s: %s" % (type(exc).__name__, str(exc)[:400])}],
                   "warnings": [], "ok": False, "rc": 1}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_human(payload))
    return int(payload.get("rc", 1))


if __name__ == "__main__":
    sys.exit(main())
