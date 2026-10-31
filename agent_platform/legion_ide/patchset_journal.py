# -*- coding: utf-8 -*-
"""LDE patchset 事务磁盘 journal / 崩溃恢复（P0 事务崩溃一致性，2026-10-31）。

背景：IDE patchset confirm（`agent_platform/legion_ide/serve.py` 的
`/api/ide/patchset/confirm`）当前是「内存记清单 → 逐文件写 → 异常逆序回滚」。
进程若在「只写了一部分」时崩溃/被杀，内存清单随进程消失：重启后既没有回滚
依据、也没有审计痕迹，worktree 会长期停留在**部分落盘**的中间态。本模块把
一次 patchset 变成磁盘 write-ahead journal（WAL）：

  begin()          落盘事务意图：`<worktree>/.ide-patchset-journal/<patchset_id>/intent.json`
                   （原子写 + fsync + 目录 fsync），旧文件内容备份到同目录
                   `backup/*.bak`（内容 sha256 与 intent 对齐）；
  record_applied() 每成功写一个文件就原子更新 journal（applied 标记）；
  complete()       全部文件写完后标记事务完成（终态 completed）；
  abort()          放弃事务：按备份逆序回滚已落盘文件（终态 aborted）；
  recover()        启动时扫描未完成 journal，按 intent 回滚（新建文件删除 /
                   旧文件按备份恢复）；可选 resume=True 在「全部文件已按新内容
                   落盘」时前滚为 completed；坏 journal 隔离为 `<id>.corrupt`
                   并报告，不崩、不静默吞；
  prune()/status() 清理终态 journal / 只读盘点。

状态机：
  open → applying → completed        （正常提交）
                 ↘ aborted           （调用方放弃，含回滚）
                 ↘ recovered         （崩溃后被 recover 回滚）
  resume 前滚时：open/applying → completed（文件全部验证为新内容）。
  completed / aborted / recovered 为终态；begin 对重复 patchset_id 直接拒绝，
  不静默覆盖已有 journal。

安全边界（intent.json 是磁盘数据，恢复前一律视为不可信输入）：
  - journal 自身的写（intent/backup/prune/隔离）只在
    `<worktree>/.ide-patchset-journal/` 内；
  - 恢复/回滚只允许操作 worktree 内、intent 声明的相对路径：拒绝绝对路径、
    `..`、符号链接逃逸、journal 目录自身；越界条目记 unsafe_path 动作并跳过；
  - 恢复不改 git index、不碰服务进程、不做网络/外部通知。
  - 本模块**尚未接入 serve.py**（本次只交付模块+测试）；接线建议见文末。

并发口径：一个 patchset_id 一个 journal 目录；intent 更新是 tmp+fsync+os.replace
原子替换，单写者（服务内 patchset 已由工单锁串行）下安全；不提供跨进程锁。

恢复动作（recover().actions[].action）：
  restored            旧文件按备份恢复成功（事务前内容）；
  restored_missing    journal 记账为已写但文件缺失 → 按备份补回旧内容；
  deleted_created     新建文件被本事务写入 → 删除（回到「不存在」）；
  already_old         文件已是事务前内容 → 无需动作；
  absent              新建文件本来就不存在 → 无需动作；
  missing             事务前文件已被外部删除且本事务未记账 → 不复活；
  conflict            文件被第三方修改 → 保留现状（不覆盖）；
  backup_missing / backup_corrupt  备份缺失/损坏 → 保留现状并报告；
  unsafe_path         intent 路径越界 → 拒绝操作并报告；
  error               磁盘操作失败（stat/读写/删除）→ 保留现状并报告；
  corrupt             坏 journal（非法 JSON/结构/未知状态）→ 隔离为 <id>.corrupt；
  committed_forward   resume=True 且全部文件已验证为新内容 → 前滚 completed。

用法：
  from legion_ide import patchset_journal as J
  J.begin(worktree, "ps-xxx", [{"path": "agent_platform/a.py", "content": "..."}],
          task="WO-X", owner="DEV1")
  # 服务写盘（writer.write_file）：
  J.record_applied(worktree, "ps-xxx", "agent_platform/a.py")
  J.complete(worktree, "ps-xxx")
  # 进程启动：
  result = J.recover(worktree)        # actions = 回滚/隔离动作明细

后续接入 serve 建议（另单）：
  1. `/api/ide/patchset/confirm`：拿到 pending["files"] 后先 begin()，逐文件
     write_file 成功后 record_applied()；全部成功 complete()；异常分支
     abort(reason=...)（serve 现有的内存回滚可保留为快速路径，abort 兜底）；
  2. `create_server()`/8103 启动脚本：对已注册 worktree 逐个 recover()，把
     actions 写 dev_events（code_action）留痕；resume 建议显式关闭（保守回滚）；
  3. 每日对账：status() 汇总未完成/终态数量，prune(keep_seconds=7*86400)；
  4. `.ide-patchset-journal/` 是运行态目录，接线时加入 .gitignore（避免 worktree
     git status 噪声）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path, PurePosixPath

SCHEMA = "legion.ide.patchset_journal/v1"
JOURNAL_DIR = ".ide-patchset-journal"
BACKUP_DIR = "backup"
INTENT_FILE = "intent.json"
CORRUPT_SUFFIX = ".corrupt"
MAX_FILES = 20
MAX_FILE_BYTES = 1024 * 1024
PATCHSET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,79}$")
UNFINISHED_STATES = ("open", "applying")
TERMINAL_STATES = ("completed", "aborted", "recovered")


class JournalError(Exception):
    """journal 操作被拒绝（参数非法 / 状态不允许 / 路径越界 / journal 缺失）。"""


# ----------------------------------------------------------------------
# 基础工具：时间 / 原子写 / 路径围栏
# ----------------------------------------------------------------------
def _now() -> float:
    return time.time()


def _iso(ts=None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else _now()).astimezone() \
        .isoformat(timespec="seconds")


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fsync_dir(path: Path) -> None:
    """目录项 fsync（POSIX 有效；Windows 不支持目录 fsync → best-effort 静默跳过）。"""
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """tmp + fsync + os.replace 原子落盘（同目录 tmp，读者只会看到旧/新完整版本）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("%s.tmp-%d-%s" % (path.name, os.getpid(), uuid.uuid4().hex[:8]))
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _atomic_write_json(path: Path, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    _atomic_write_bytes(path, data)


def _safe_target(worktree: Path, rel) -> Path:
    """intent 里的相对路径 → worktree 内绝对路径；越界/绝对路径/journal 目录 → JournalError。"""
    raw = str(rel or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/"):
        raise JournalError("非法文件路径（须为 worktree 内相对路径）：%r" % rel)
    pure = PurePosixPath(raw)
    if pure.is_absolute() or ".." in pure.parts:
        raise JournalError("路径越界（拒绝 .. / 绝对路径）：%r" % rel)
    if pure.parts and pure.parts[0] == JOURNAL_DIR:
        raise JournalError("拒绝把 journal 目录自身当作事务文件：%r" % rel)
    wt = Path(worktree).resolve()
    candidate = (wt / str(pure)).resolve()
    try:
        candidate.relative_to(wt)
    except ValueError:
        raise JournalError("路径逃出 worktree：%r" % rel)
    return candidate


def _safe_join(base: Path, rel) -> Path | None:
    """journal 内部相对路径（backup 指针）安全拼接；非法返回 None。"""
    raw = str(rel or "").strip().replace("\\", "/")
    if not raw:
        return None
    pure = PurePosixPath(raw)
    if pure.is_absolute() or ".." in pure.parts:
        return None
    base_resolved = Path(base).resolve()
    candidate = (base_resolved / str(pure)).resolve()
    try:
        candidate.relative_to(base_resolved)
    except ValueError:
        return None
    return candidate


def _norm_rel(rel) -> str:
    return PurePosixPath(str(rel or "").strip().replace("\\", "/")).as_posix()


def _journal_dir(worktree, patchset_id) -> Path:
    pid = str(patchset_id or "").strip()
    if not PATCHSET_ID_RE.match(pid):
        raise JournalError("patchset_id 非法（1-80 位字母数字 _.-）：%r" % patchset_id)
    return Path(worktree) / JOURNAL_DIR / pid


def journal_root(worktree) -> Path:
    """返回 worktree 的 journal 根目录（不创建）。"""
    return Path(worktree) / JOURNAL_DIR


# ----------------------------------------------------------------------
# intent 读写
# ----------------------------------------------------------------------
def _load_intent(jdir: Path) -> dict:
    path = jdir / INTENT_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise JournalError("journal intent.json 不可读：%s（%s）" % (path, exc))
    try:
        doc = json.loads(text)
    except ValueError as exc:
        raise JournalError("journal intent.json 非法 JSON：%s（%s）" % (path, exc))
    if not isinstance(doc, dict) or not isinstance(doc.get("files"), list):
        raise JournalError("journal intent.json 结构非法（须为含 files 数组的对象）：%s" % path)
    return doc


def _save_intent(jdir: Path, doc: dict) -> None:
    doc["updated"] = _iso()
    doc["updated_ts"] = _now()
    _atomic_write_json(jdir / INTENT_FILE, doc)


def _find_entry(doc: dict, rel: str) -> dict:
    for entry in doc.get("files") or []:
        if isinstance(entry, dict) and _norm_rel(entry.get("path")) == rel:
            return entry
    raise JournalError("journal 中不存在该文件条目：%s" % rel)


def _load_backup(jdir: Path, entry: dict) -> bytes | None:
    backup_rel = entry.get("backup")
    if not backup_rel:
        return None
    path = _safe_join(jdir, backup_rel)
    if path is None or not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def _summary(doc: dict) -> dict:
    files = [entry for entry in doc.get("files") or [] if isinstance(entry, dict)]
    return {
        "patchset_id": str(doc.get("patchset_id") or ""),
        "state": str(doc.get("state") or ""),
        "task": str(doc.get("task") or ""),
        "owner": str(doc.get("owner") or ""),
        "created": str(doc.get("created") or ""),
        "updated": str(doc.get("updated") or ""),
        "files": len(files),
        "applied": sum(1 for entry in files if entry.get("applied")),
    }


# ----------------------------------------------------------------------
# begin：落盘事务意图 + 旧内容备份
# ----------------------------------------------------------------------
def begin(worktree, patchset_id, files, *, task="", owner="", message="") -> dict:
    """开启一次 patchset 事务 journal（原子写 intent + 旧内容备份）。

    files: [{"path": "worktree 内相对路径", "content": "新文本内容"}, ...]
    返回 {ok, patchset_id, state, journal_dir, files:[摘要]}。
    同一 patchset_id 已有 journal → JournalError（不静默覆盖）。
    """
    wt = Path(worktree).resolve()
    if not wt.is_dir():
        raise JournalError("worktree 不存在：%s" % wt)
    if not isinstance(files, list) or not (1 <= len(files) <= MAX_FILES):
        raise JournalError("files 须为 1–%d 项数组（实际 %s）"
                           % (MAX_FILES, len(files) if isinstance(files, list) else type(files).__name__))
    jdir = _journal_dir(wt, patchset_id)
    if (jdir / INTENT_FILE).exists():
        raise JournalError("journal 已存在（不静默覆盖）：%s" % jdir)

    entries: list[dict] = []
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise JournalError("files 项须为对象")
        rel = _norm_rel(item.get("path"))
        content = item.get("content", "")
        if not rel or rel == ".":
            raise JournalError("files 项缺 path")
        if not isinstance(content, str):
            raise JournalError("files 项 content 必须是字符串：%s" % rel)
        target = _safe_target(wt, rel)
        if rel in seen:
            raise JournalError("files 中 path 重复：%s" % rel)
        seen.add(rel)
        new_raw = content.encode("utf-8")
        if len(new_raw) > MAX_FILE_BYTES:
            raise JournalError("content 超过 %d KB：%s" % (MAX_FILE_BYTES // 1024, rel))
        try:
            old_raw = target.read_bytes() if target.is_file() else None
        except OSError as exc:
            raise JournalError("读取旧文件失败：%s（%s）" % (rel, exc))
        entries.append({
            "path": rel,
            "created": old_raw is None,
            # created=True 时 old_sha256 记为「空内容 sha256」——与 serve/writer
            # preview_file 的 old_sha256 口径一致，便于后续接线直接对齐。
            "old_sha256": _sha_bytes(old_raw or b""),
            "new_sha256": _sha_bytes(new_raw),
            "old_bytes": len(old_raw or b""),
            "new_bytes": len(new_raw),
            "backup": "",
            "applied": False,
            "applied_at": "",
        })

    # 先写备份、再原子写 intent：intent.json 出现即代表事务已完整可恢复。
    try:
        jdir.mkdir(parents=True, exist_ok=True)
        (jdir / BACKUP_DIR).mkdir(parents=True, exist_ok=True)
        for index, entry in enumerate(entries):
            if entry["created"]:
                continue
            target = _safe_target(wt, entry["path"])
            try:
                old_raw = target.read_bytes()
            except OSError as exc:
                raise JournalError("读取旧文件失败：%s（%s）" % (entry["path"], exc))
            if _sha_bytes(old_raw) != entry["old_sha256"]:
                raise JournalError("begin 期间旧文件被并发修改，拒绝开始事务：%s"
                                   % entry["path"])
            backup_rel = "%s/%04d.bak" % (BACKUP_DIR, index)
            _atomic_write_bytes(jdir / backup_rel, old_raw)
            entry["backup"] = backup_rel
        now = _now()
        doc = {
            "schema": SCHEMA,
            "patchset_id": str(patchset_id).strip(),
            "task": str(task or ""),
            "owner": str(owner or ""),
            "message": str(message or "")[:200],
            "state": "open",
            "created": _iso(now),
            "created_ts": now,
            "updated": _iso(now),
            "updated_ts": now,
            "completed_at": "",
            "completed_ts": 0.0,
            "aborted_at": "",
            "aborted_ts": 0.0,
            "abort_reason": "",
            "recovered_at": "",
            "recovered_ts": 0.0,
            "recover_mode": "",
            "files": entries,
        }
        _atomic_write_json(jdir / INTENT_FILE, doc)
    except Exception:
        # begin 失败不留半成品 journal（目录是本次新建的；不删 worktree 其它内容）。
        try:
            shutil.rmtree(jdir)
        except OSError:
            pass
        raise
    return {
        "ok": True,
        "patchset_id": doc["patchset_id"],
        "state": "open",
        "journal_dir": str(jdir),
        "files": [{key: entry[key] for key in
                   ("path", "created", "old_sha256", "new_sha256", "new_bytes")}
                  for entry in entries],
    }


# ----------------------------------------------------------------------
# record_applied / complete / abort
# ----------------------------------------------------------------------
def record_applied(worktree, patchset_id, path, *, sha256=None, verify=True) -> dict:
    """标记单个文件已按新内容落盘（默认再校验磁盘内容，防「没写就记账」）。

    幂等：已标记 applied 的条目重复调用直接返回 already=True。
    """
    jdir = _journal_dir(Path(worktree), patchset_id)
    doc = _load_intent(jdir)
    state = str(doc.get("state") or "")
    rel = _norm_rel(path)
    if state not in UNFINISHED_STATES:
        raise JournalError("journal 状态 %r 不允许 record_applied（须 open/applying）" % state)
    entry = _find_entry(doc, rel)
    if entry.get("applied"):
        return {"ok": True, "patchset_id": doc.get("patchset_id"), "state": state,
                "path": rel, "applied": True, "already": True,
                "applied_count": sum(1 for item in doc["files"] if item.get("applied")),
                "total": len(doc["files"])}
    if sha256 is not None and str(sha256) != entry.get("new_sha256"):
        raise JournalError("record_applied 传入 sha256 与 intent.new_sha256 不一致：%s" % rel)
    if verify:
        target = _safe_target(Path(worktree).resolve(), rel)
        try:
            current = target.read_bytes() if target.is_file() else None
        except OSError as exc:
            raise JournalError("record_applied 读取文件失败：%s（%s）" % (rel, exc))
        if current is None or _sha_bytes(current) != entry.get("new_sha256"):
            raise JournalError("record_applied 校验失败（磁盘内容 != intent.new_sha256）：%s" % rel)
    entry["applied"] = True
    entry["applied_at"] = _iso()
    doc["state"] = "applying"
    _save_intent(jdir, doc)
    return {"ok": True, "patchset_id": doc.get("patchset_id"), "state": "applying",
            "path": rel, "applied": True, "already": False,
            "applied_count": sum(1 for item in doc["files"] if item.get("applied")),
            "total": len(doc["files"])}


def complete(worktree, patchset_id, *, message="") -> dict:
    """事务提交：要求所有文件均已 applied；终态 completed（幂等）。"""
    jdir = _journal_dir(Path(worktree), patchset_id)
    doc = _load_intent(jdir)
    state = str(doc.get("state") or "")
    if state == "completed":
        return {"ok": True, "patchset_id": doc.get("patchset_id"), "state": "completed",
                "already": True, "files": [entry.get("path") for entry in doc["files"]]}
    if state not in UNFINISHED_STATES:
        raise JournalError("journal 状态 %r 不允许 complete" % state)
    missing = [entry.get("path") for entry in doc["files"] if not entry.get("applied")]
    if missing:
        raise JournalError("仍有未 applied 文件，不能 complete：%s" % ", ".join(missing[:5]))
    now = _now()
    doc["state"] = "completed"
    doc["completed_at"] = _iso(now)
    doc["completed_ts"] = now
    if message:
        doc["complete_message"] = str(message)[:200]
    _save_intent(jdir, doc)
    return {"ok": True, "patchset_id": doc.get("patchset_id"), "state": "completed",
            "already": False, "completed_at": doc["completed_at"],
            "files": [entry.get("path") for entry in doc["files"]]}


def abort(worktree, patchset_id, *, reason="", rollback=True) -> dict:
    """放弃事务：按备份逆序回滚已落盘文件，终态 aborted（幂等）。

    rollback=False 只记账不回滚（调用方自行保证已恢复，如内存回滚已成功）。
    已完成事务不允许 abort（JournalError）。
    """
    wt = Path(worktree).resolve()
    jdir = _journal_dir(wt, patchset_id)
    doc = _load_intent(jdir)
    state = str(doc.get("state") or "")
    if state in ("aborted", "recovered"):
        return {"ok": True, "patchset_id": doc.get("patchset_id"), "state": state,
                "already": True, "actions": []}
    if state == "completed":
        raise JournalError("已完成事务不能 abort（如需撤销请走 rollback 语义）")
    if state not in UNFINISHED_STATES:
        raise JournalError("journal 状态 %r 不允许 abort" % state)
    actions = _rollback_files(wt, doc) if rollback else []
    now = _now()
    doc["state"] = "aborted"
    doc["aborted_at"] = _iso(now)
    doc["aborted_ts"] = now
    doc["abort_reason"] = str(reason or "")[:300]
    doc["rollback_actions"] = actions
    _save_intent(jdir, doc)
    return {"ok": True, "patchset_id": doc.get("patchset_id"), "state": "aborted",
            "already": False, "aborted_at": doc["aborted_at"], "actions": actions}


# ----------------------------------------------------------------------
# 回滚原语（recover / abort 共用）
# ----------------------------------------------------------------------
def _rollback_one(jdir: Path, worktree: Path, entry: dict) -> dict:
    """按单条 intent 把目标文件恢复为事务前状态；返回动作（不抛异常，坏条目记 error）。"""
    rel = _norm_rel(entry.get("path"))
    try:
        target = _safe_target(worktree, rel)
    except JournalError as exc:
        return {"action": "unsafe_path", "path": rel, "detail": str(exc)}
    try:
        exists = target.exists()
        is_file = target.is_file()
    except OSError as exc:
        return {"action": "error", "path": rel, "detail": "stat 失败：%s" % exc}
    current_sha: str | None = None
    if exists and is_file:
        try:
            current_sha = _sha_bytes(target.read_bytes())
        except OSError as exc:
            return {"action": "error", "path": rel, "detail": "读取失败：%s" % exc}

    if entry.get("created"):
        if not exists:
            return {"action": "absent", "path": rel}
        if not is_file:
            return {"action": "conflict", "path": rel, "detail": "目标不是普通文件，保留现状"}
        if current_sha != entry.get("new_sha256"):
            return {"action": "conflict", "path": rel,
                    "detail": "新建文件内容非本事务写入，保留现状"}
        try:
            target.unlink()
        except OSError as exc:
            return {"action": "error", "path": rel, "detail": "删除失败：%s" % exc}
        return {"action": "deleted_created", "path": rel}

    old_sha = entry.get("old_sha256")
    if exists and is_file and current_sha == old_sha:
        return {"action": "already_old", "path": rel}
    backup = _load_backup(jdir, entry)
    if backup is None:
        return {"action": "backup_missing", "path": rel,
                "detail": "intent.backup 缺失或不可读，无法恢复旧内容"}
    if _sha_bytes(backup) != old_sha:
        return {"action": "backup_corrupt", "path": rel,
                "detail": "备份内容 sha256 与 intent.old_sha256 不一致"}
    if not exists:
        if not entry.get("applied"):
            return {"action": "missing", "path": rel,
                    "detail": "事务前文件已被外部删除（本事务未记账为已写），不复活"}
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_bytes(target, backup)
        except OSError as exc:
            return {"action": "error", "path": rel, "detail": "恢复失败：%s" % exc}
        return {"action": "restored_missing", "path": rel}
    if not is_file:
        return {"action": "conflict", "path": rel, "detail": "目标不是普通文件，保留现状"}
    if current_sha == entry.get("new_sha256"):
        try:
            _atomic_write_bytes(target, backup)
        except OSError as exc:
            return {"action": "error", "path": rel, "detail": "恢复失败：%s" % exc}
        try:
            restored_sha = _sha_bytes(target.read_bytes())
        except OSError:
            restored_sha = ""
        if restored_sha != old_sha:
            return {"action": "error", "path": rel,
                    "detail": "恢复后 sha256 校验不一致（磁盘异常）"}
        return {"action": "restored", "path": rel}
    return {"action": "conflict", "path": rel,
            "detail": "文件已被第三方修改（既非新内容也非旧内容），保留现状"}


def _rollback_files(worktree: Path, doc: dict) -> list[dict]:
    """逆序回滚一个 journal 声明的所有文件（幂等；每条动作都带 path）。"""
    jdir = _journal_dir(worktree, doc.get("patchset_id"))
    actions = []
    for entry in reversed(doc.get("files") or []):
        if not isinstance(entry, dict):
            actions.append({"action": "bad_entry", "path": "", "detail": "intent.files 项非对象"})
            continue
        actions.append(_rollback_one(jdir, worktree, entry))
    return actions


def _all_files_match_new(worktree: Path, doc: dict) -> bool:
    """resume 前滚判定：所有声明的文件当前磁盘内容 == new_sha256。"""
    entries = doc.get("files") or []
    if not entries:
        return False
    for entry in entries:
        try:
            target = _safe_target(worktree, _norm_rel(entry.get("path")))
        except JournalError:
            return False
        try:
            if not target.is_file():
                return False
            if _sha_bytes(target.read_bytes()) != entry.get("new_sha256"):
                return False
        except OSError:
            return False
    return True


# ----------------------------------------------------------------------
# recover：启动恢复
# ----------------------------------------------------------------------
def _quarantine_corrupt(jdir: Path) -> str:
    """把坏 journal 目录改名为 <name>.corrupt（保留证据、让后续 recover 幂等）。"""
    target = jdir.with_name(jdir.name + CORRUPT_SUFFIX)
    if target.exists():
        target = jdir.with_name("%s%s-%d" % (jdir.name, CORRUPT_SUFFIX, int(_now())))
    try:
        os.replace(jdir, target)
        return target.name
    except OSError:
        return ""


def recover(worktree, *, resume=False) -> dict:
    """扫描并处理 worktree 内未完成 journal。

    默认（resume=False）：未完成事务一律回滚到事务前状态（原子性优先）。
    resume=True：若声明的全部文件当前内容都已是新内容（崩溃在「写完全部、
    未 complete」窗口），则前滚为 completed；否则回滚。
    终态 journal 跳过（交给 prune）；坏 journal 隔离为 <id>.corrupt 并报告。
    返回 {ok, journal_root, actions, corrupt, skipped, counts}；重复调用幂等。
    """
    wt = Path(worktree).resolve()
    root = wt / JOURNAL_DIR
    result = {
        "ok": True,
        "journal_root": str(root),
        "actions": [],
        "corrupt": [],
        "skipped": [],
        "counts": {"scanned": 0, "recovered": 0, "committed_forward": 0,
                   "corrupt": 0, "skipped": 0},
    }
    if not root.is_dir():
        return result
    for jdir in sorted(root.iterdir(), key=lambda item: item.name):
        if not jdir.is_dir():
            continue
        if jdir.name.endswith(CORRUPT_SUFFIX):
            # 已隔离的历史坏 journal：只报告存在，不重复计数/不重复动作（幂等）。
            result["corrupt"].append(jdir.name)
            continue
        result["counts"]["scanned"] += 1
        try:
            doc = _load_intent(jdir)
        except JournalError as exc:
            quarantined = _quarantine_corrupt(jdir)
            result["actions"].append({"patchset_id": jdir.name, "action": "corrupt",
                                      "detail": str(exc)[:300],
                                      "quarantined": quarantined})
            result["corrupt"].append(quarantined or jdir.name)
            result["counts"]["corrupt"] += 1
            continue
        state = str(doc.get("state") or "")
        if state in TERMINAL_STATES:
            result["skipped"].append({"patchset_id": jdir.name, "state": state})
            result["counts"]["skipped"] += 1
            continue
        if state not in UNFINISHED_STATES:
            result["actions"].append({"patchset_id": jdir.name, "action": "corrupt",
                                      "detail": "未知 journal 状态：%r" % state,
                                      "quarantined": _quarantine_corrupt(jdir)})
            result["counts"]["corrupt"] += 1
            continue
        if resume and _all_files_match_new(wt, doc):
            now = _now()
            doc["state"] = "completed"
            doc["completed_at"] = _iso(now)
            doc["completed_ts"] = now
            doc["recover_mode"] = "resume_forward"
            _save_intent(jdir, doc)
            result["actions"].append({"patchset_id": jdir.name,
                                      "action": "committed_forward",
                                      "files": [entry.get("path") for entry in doc["files"]]})
            result["counts"]["committed_forward"] += 1
            continue
        actions = _rollback_files(wt, doc)
        now = _now()
        doc["state"] = "recovered"
        doc["recovered_at"] = _iso(now)
        doc["recovered_ts"] = now
        doc["recover_mode"] = "rollback" if not resume else "resume_fallback_rollback"
        doc["rollback_actions"] = actions
        _save_intent(jdir, doc)
        for action in actions:
            action["patchset_id"] = jdir.name
        result["actions"].extend(actions)
        result["counts"]["recovered"] += 1
    return result


# ----------------------------------------------------------------------
# prune / status
# ----------------------------------------------------------------------
def _journal_finished_ts(doc: dict) -> float:
    for key in ("completed_ts", "aborted_ts", "recovered_ts", "updated_ts"):
        try:
            value = float(doc.get(key) or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value:
            return value
    return 0.0


def prune(worktree, *, keep_seconds=0.0, dry_run=False) -> dict:
    """清理终态 journal（completed/aborted/recovered）与已隔离的 *.corrupt。

    未完成 journal 永不删除（留着给 recover）；keep_seconds>0 时只清更早的终态。
    dry_run=True 只报告不删除。返回 {ok, dry_run, removed, kept, counts}。
    """
    root = journal_root(worktree)
    result = {"ok": True, "dry_run": bool(dry_run), "journal_root": str(root),
              "removed": [], "kept": [],
              "counts": {"scanned": 0, "removed": 0, "kept": 0}}
    if not root.is_dir():
        return result
    now = _now()
    try:
        candidates = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise JournalError("journal 根目录不可读：%s（%s）" % (root, exc))
    for jdir in candidates:
        if not jdir.is_dir():
            continue
        result["counts"]["scanned"] += 1
        name = jdir.name
        state = "corrupt" if name.endswith(CORRUPT_SUFFIX) else ""
        finished_ts = 0.0
        if not state:
            try:
                doc = _load_intent(jdir)
            except JournalError:
                state = "corrupt"
            else:
                state = str(doc.get("state") or "")
                finished_ts = _journal_finished_ts(doc)
                if state not in TERMINAL_STATES:
                    result["kept"].append({"journal": name, "state": state,
                                           "reason": "unfinished"})
                    result["counts"]["kept"] += 1
                    continue
        if state == "corrupt" and not finished_ts:
            try:
                finished_ts = jdir.stat().st_mtime
            except OSError:
                finished_ts = now
        if keep_seconds and (now - finished_ts) < float(keep_seconds):
            result["kept"].append({"journal": name, "state": state, "reason": "within_keep_window"})
            result["counts"]["kept"] += 1
            continue
        if dry_run:
            result["removed"].append({"journal": name, "state": state})
            continue
        try:
            shutil.rmtree(jdir)
        except OSError as exc:
            result["kept"].append({"journal": name, "state": state,
                                   "reason": "remove_failed: %s" % exc})
            result["counts"]["kept"] += 1
            continue
        result["removed"].append({"journal": name, "state": state})
        result["counts"]["removed"] += 1
    return result


def status(worktree, patchset_id=None) -> dict:
    """只读盘点：全部 journal 摘要，或单个 journal 明细（含 files）。"""
    root = journal_root(worktree)
    if patchset_id is not None:
        jdir = _journal_dir(Path(worktree), patchset_id)
        doc = _load_intent(jdir)
        summary = _summary(doc)
        summary.update({
            "message": str(doc.get("message") or ""),
            "abort_reason": str(doc.get("abort_reason") or ""),
            "recover_mode": str(doc.get("recover_mode") or ""),
            "completed_at": str(doc.get("completed_at") or ""),
            "aborted_at": str(doc.get("aborted_at") or ""),
            "recovered_at": str(doc.get("recovered_at") or ""),
            "rollback_actions": doc.get("rollback_actions") or [],
            # files 保持「数量」口径，明细放 file_entries（避免同键双义）。
            "file_entries": [dict(entry) for entry in doc.get("files") or []
                             if isinstance(entry, dict)],
        })
        return {"ok": True, "journal_root": str(root), "journal": summary}
    result = {"ok": True, "journal_root": str(root), "counts": {},
              "journals": [], "corrupt": []}
    if not root.is_dir():
        return result
    try:
        candidates = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise JournalError("journal 根目录不可读：%s（%s）" % (root, exc))
    for jdir in candidates:
        if not jdir.is_dir():
            continue
        if jdir.name.endswith(CORRUPT_SUFFIX):
            result["corrupt"].append(jdir.name)
            continue
        try:
            doc = _load_intent(jdir)
        except JournalError:
            result["corrupt"].append(jdir.name)
            continue
        summary = _summary(doc)
        result["journals"].append(summary)
        state = summary["state"] or "unknown"
        result["counts"][state] = result["counts"].get(state, 0) + 1
    return result
