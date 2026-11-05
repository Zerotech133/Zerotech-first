# -*- coding: utf-8 -*-
"""审计日志：商业动作追加 JSONL（可导出对账/合规）。

H1-6（2026-11-03）：审计 **哈希链** 防篡改。
- 每条新记录新增 `prev_hash`（上一条的 hash，首条为 ""）与 `hash`
  （sha256(prev_hash + 规范化 JSON)）；
- 历史遗留行（无 hash 字段）允许存在，但必须全部位于第一条带 hash 的记录之前；
- `append()` 以跨进程文件锁（旁路 `.lock`）包住「读 last_hash → 计算 → 追加 → fsync」，
  并发写不再分叉；已被 legacy 行污染（hash 段后又有无 hash 行）的账本拒绝追加；
- `verify()` 收集全部错误（不再首错即停），返回 checked/total/errors 便于运维定位；
- `migrate()`（CLI `audit-migrate`）备份原文件后重算整链并原子替换，用于历史账本迁移；
- `tail/export` 行为保持不变（兼容 billing/growth/followup）。
"""
import contextlib
import hashlib
import json
import os
import shutil
import time
from typing import Any, Dict, Iterator, List, Optional

from . import tenants

AUDIT_FILE = os.environ.get("COMMERCIAL_AUDIT") or os.path.join(tenants.BASE, "audit", "audit.jsonl")

GENESIS = ""
MAX_ERRORS = 20


def _lock_path(p: str) -> str:
    return p + ".lock"


@contextlib.contextmanager
def _exclusive(p: str) -> Iterator[None]:
    """跨进程互斥（Windows msvcrt / POSIX fcntl），只锁审计写入段。

    P1（2026-11-04 代码审查）：原先 `_last_hash → hash → append` 无锁，
    两个进程同时 append 会取到同一 prev_hash 导致链分叉且不可恢复。
    """
    lock_p = _lock_path(p)
    os.makedirs(os.path.dirname(lock_p), exist_ok=True)
    f = open(lock_p, "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            f.seek(0)
            while True:
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:  # 锁被占用：LK_LOCK 自带重试，兜底再等
                    time.sleep(0.02)
        else:  # pragma: no cover - 生产为 Windows
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover
                import fcntl

                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    finally:
        f.close()


def _has_hashed_record(p: str) -> bool:
    """账本中是否已存在带 hash 的记录（用于 legacy 尾行 fail-closed 判定）。"""
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    if "hash" in json.loads(line):
                        return True
                except Exception:
                    continue
    except OSError:
        return False
    return False


def _path(path: Optional[str] = None) -> str:
    p = path or AUDIT_FILE
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def _canonical(rec: Dict[str, Any]) -> str:
    """规范化 JSON：排序键、紧凑分隔符；用于哈希计算。"""
    return json.dumps(rec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_record(rec: Dict[str, Any]) -> str:
    body = {k: v for k, v in rec.items() if k != "hash"}
    return hashlib.sha256((GENESIS + _canonical(body)).encode("utf-8")).hexdigest()


def _last_hash(p: str) -> str:
    """读取最后一条非空记录的 hash；文件不存在/为空 → GENESIS。

    只读文件尾部 8KB（审计记录通常 <2KB），避免每次 append 全量扫描；
    末行解析失败时回退整体读取以给出明确错误（fail-closed）。
    """
    if not os.path.exists(p):
        return GENESIS
    last = None
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return GENESIS
            f.seek(max(0, size - 8192))
            data = f.read()
        parts = [x for x in data.split(b"\n") if x.strip()]
        if parts:
            last = parts[-1].decode("utf-8", errors="replace").strip()
    except OSError:
        last = None
    if last is None:
        # 回退：整体读取（文件异常/超长记录）
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    last = line
    if last is None:
        return GENESIS
    try:
        rec = json.loads(last)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("审计文件末行不可解析，拒绝追加（需先修复）: %s" % exc)
    if "hash" not in rec and _has_hashed_record(p):
        raise RuntimeError(
            "审计文件末行为 legacy 记录（无 hash），但此前已存在哈希链记录；"
            "继续追加会造成链破坏。请先执行 `python -m commercial.cli audit-migrate` 迁移。")
    return str(rec.get("hash") or GENESIS)


def append(action: str, tenant: str = "", actor: str = "system", resource: str = "",
           result: str = "ok", detail: Optional[Dict[str, Any]] = None,
           path: Optional[str] = None) -> Dict[str, Any]:
    rec: Dict[str, Any] = {
        "ts": time.strftime("%F %T"),
        "tenant": tenant,
        "actor": actor,
        "action": action,
        "resource": resource,
        "result": result,
        "detail": detail or {},
    }
    p = _path(path)
    with _exclusive(p):
        rec["prev_hash"] = _last_hash(p)
        rec["hash"] = _hash_record(rec)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    return rec


def tail(n: int = 20, path: Optional[str] = None, tenant: Optional[str] = None) -> List[Dict[str, Any]]:
    p = _path(path)
    if not os.path.exists(p):
        return []
    out: List[Dict[str, Any]] = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if tenant and rec.get("tenant") != tenant:
                continue
            out.append(rec)
    return out[-n:]


def export(dest: str, path: Optional[str] = None) -> int:
    """导出全部审计为文件（企业版审计导出）。"""
    p = _path(path)
    if not os.path.exists(p):
        open(dest, "w", encoding="utf-8").close()
        return 0
    n = 0
    with open(p, "r", encoding="utf-8") as src, open(dest, "w", encoding="utf-8") as dst:
        for line in src:
            dst.write(line)
            n += 1
    return n


def verify(path: Optional[str] = None) -> Dict[str, Any]:
    """校验审计哈希链（全量，不首错即停）。

    返回：{ok, total, checked, hashed, legacy, last_hash, error, errors, path}
    - legacy = 第一条带 hash 记录之前的旧行数；
    - 若旧行出现在已哈希记录之后 → 链视为破坏（ok=False）；
    - errors 最多收集 MAX_ERRORS 条，便于定位多处问题。
    """
    p = path or AUDIT_FILE
    result: Dict[str, Any] = {"ok": True, "total": 0, "checked": 0, "hashed": 0, "legacy": 0,
                              "last_hash": "", "error": None, "errors": [],
                              "errors_truncated": False, "path": p}
    if not os.path.exists(p):
        return result
    prev_expected = GENESIS
    seen_hashed = False
    errors: List[str] = result["errors"]

    def _add_error(msg: str) -> None:
        if len(errors) < MAX_ERRORS:
            errors.append(msg)
        else:
            result["errors_truncated"] = True

    with open(p, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            result["total"] += 1
            try:
                rec = json.loads(line)
            except Exception as exc:  # noqa: BLE001
                _add_error("第 %d 行 JSON 解析失败: %s" % (lineno, exc))
                continue
            result["checked"] += 1
            if "hash" not in rec:
                if seen_hashed:
                    _add_error("第 %d 行缺少 hash（位于已哈希记录之后）" % lineno)
                else:
                    result["legacy"] += 1
                continue
            seen_hashed = True
            result["hashed"] += 1
            if rec.get("prev_hash", GENESIS) != prev_expected:
                _add_error("第 %d 行 prev_hash 不匹配（期望 %s…，实际 %s…）" % (
                    lineno, str(prev_expected)[:12], str(rec.get("prev_hash"))[:12]))
            elif _hash_record(rec) != rec.get("hash"):
                _add_error("第 %d 行 hash 不匹配（记录被篡改）" % lineno)
            prev_expected = rec["hash"]
            result["last_hash"] = rec["hash"]
    if errors:
        result["ok"] = False
        result["error"] = errors[0]
    return result


def migrate(path: Optional[str] = None) -> Dict[str, Any]:
    """把历史账本迁移为完整哈希链：备份 → 全链重算 → 原子替换 → 复核。

    - 任何一行 JSON 不可解析则中止（不改动原文件）；
    - 原文件全部记录按顺序重算 prev_hash/hash（含 legacy 与既有 hash 记录）；
    - 返回 {ok, migrated, backup, verify, error}。
    """
    p = _path(path)
    out: Dict[str, Any] = {"ok": False, "migrated": 0, "backup": None,
                           "verify": None, "error": None}
    if not os.path.exists(p):
        out.update(ok=True, verify=verify(p))
        return out
    with _exclusive(p):
        records: List[Dict[str, Any]] = []
        with open(p, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception as exc:  # noqa: BLE001
                    out["error"] = "第 %d 行 JSON 解析失败，拒绝迁移: %s" % (lineno, exc)
                    return out
                if not isinstance(rec, dict):
                    out["error"] = "第 %d 行不是 JSON 对象，拒绝迁移" % lineno
                    return out
                records.append(rec)
        backup = p + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(p, backup)
        prev = GENESIS
        lines: List[str] = []
        for rec in records:
            body = {k: v for k, v in rec.items() if k not in ("prev_hash", "hash")}
            body["prev_hash"] = prev
            body["hash"] = _hash_record(body)
            prev = body["hash"]
            lines.append(json.dumps(body, ensure_ascii=False))
        tmp = p + ".migrate.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    v = verify(p)
    out.update(ok=bool(v["ok"]), migrated=len(records), backup=backup, verify=v)
    if not v["ok"]:
        out["error"] = v.get("error")
    return out
