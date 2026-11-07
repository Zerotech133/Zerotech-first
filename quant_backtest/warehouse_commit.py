"""受控仓根内的文件提交、实际计量和跨进程发布保护。

plan.resource 必须为实际仓根绝对路径；逻辑来源保留在 plan.source。
read guard 覆盖文件枚举及物化，未接入该保护的消费者不在保证范围内。
"""

from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import uuid

import pandas as pd

from warehouse_runs import COUNT_FIELDS, TERMINAL
import logging


class PublicationPending(RuntimeError):
    """仓库存在未恢复的发布，消费者应显示待核验。"""


def _root(resource):
    path = Path(resource)
    if not path.is_absolute():
        raise ValueError("resource必须为受控仓根绝对路径")
    path = path.resolve()
    if not path.is_dir():
        raise ValueError("受控仓根不存在")
    return path


def _inside(root, value):
    path = Path(value).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("目标越出受控仓根")
    relative = path.relative_to(root)
    if any(part.startswith(".warehouse-publication") for part in relative.parts):
        raise ValueError("不能覆盖发布锁或意图标记")
    return path


_LOCK_OPEN_TIMEOUT_S = 5.0
_LOCK_OPEN_BACKOFF_S = 0.05


def _open_lock_descriptor(lock_path, flags, mode):
    """打开发布锁文件；瞬时 Errno 13 有界重试。

    2026-11-07 K-ORG-20261107-LOCKOPEN：两个平台分支（POSIX flock / Windows
    LockFileEx）的加锁都是**阻塞等待**语义，但打开锁文件的 os.open 写在等待之外，
    Windows 上并发创建/打开同一文件会瞬时 Errno 13，异常直接把"等一会儿"变成
    "发布失败"。与 tools/devtask.py::_tasks_file_lock 的正确形态对齐。

    上限 5s 后原样抛出：持续拒绝是真故障（权限/磁盘/路径），不能无限等成 hang。
    """
    deadline = time.monotonic() + _LOCK_OPEN_TIMEOUT_S
    while True:
        try:
            return os.open(lock_path, flags, mode)
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(_LOCK_OPEN_BACKOFF_S)


def _pending(root):
    directory = root / ".warehouse-publication" / "pending"
    if not directory.resolve().is_relative_to(root):
        raise ValueError("发布标记目录越出受控仓根")
    return directory


def _marker_path(root, commit_id):
    # RunStore的公共commit_id不是文件名，不能把外部标识直接拼接为路径。
    name = hashlib.sha256(commit_id.encode("utf-8")).hexdigest() + ".json"
    return _pending(root) / name


def _ensure_clear(root):
    directory = _pending(root)
    if directory.exists() and any(directory.iterdir()):
        raise PublicationPending("存在未恢复发布，仓库待核验")


@contextmanager
def publication_guard(resource, *, mode):
    """read 为共享锁，write 为排他锁；崩溃持有者由 OS 自动释放。

    read 必须在 with 内完成枚举和读取物化，不能返回惰性迭代器。
    Windows 走 LockFileEx，POSIX 走 fcntl.flock，两者语义等价
    （共享读/排他写、崩溃由内核或系统自动释放、read 拿到锁后再查 pending）。
    """
    if mode not in {"read", "write"}:
        raise ValueError("mode只允许read或write")
    root = _root(resource)
    if os.name != "nt":
        # POSIX：fcntl.flock 提供与 LockFileEx 等价的语义
        # （LOCK_SH 共享读 / LOCK_EX 排他写、进程崩溃由内核自动释放、read 锁定后再查 pending）。
        # 原先直接拒绝非 Windows 平台，会让 Linux 侧的数据仓消费者（freshness 等）全部 500。
        import fcntl

        lock_path = root / ".warehouse-publication.lock"
        if lock_path.is_symlink():
            raise ValueError("发布锁不可为符号链接")
        descriptor = _open_lock_descriptor(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        locked = False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX if mode == "write" else fcntl.LOCK_SH)
            locked = True
            # 与 Windows 分支一致：拿到共享锁之后再检查待恢复发布，避免枚举与发布竞态。
            if mode == "read":
                _ensure_clear(root)
            yield root
        finally:
            if locked:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/warehouse_commit.py", exc)
            os.close(descriptor)
        return

    import msvcrt
    from ctypes import wintypes

    class OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LockFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(OVERLAPPED),
    ]
    kernel.LockFileEx.restype = wintypes.BOOL
    kernel.UnlockFileEx.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(OVERLAPPED),
    ]
    kernel.UnlockFileEx.restype = wintypes.BOOL
    lock_path = root / ".warehouse-publication.lock"
    if lock_path.is_symlink():
        raise ValueError("发布锁不可为符号链接")
    descriptor = _open_lock_descriptor(
        lock_path, os.O_RDWR | os.O_CREAT | os.O_BINARY, 0o600
    )
    handle = msvcrt.get_osfhandle(descriptor)
    overlapped = OVERLAPPED()
    locked = False
    try:
        if not kernel.LockFileEx(
            handle, 2 if mode == "write" else 0, 0, 1, 0, ctypes.byref(overlapped)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        locked = True
        # 此检查在获取共享锁之后，避免发布与文件枚举间的竞态。
        if mode == "read":
            _ensure_clear(root)
        yield root
    finally:
        if locked:
            kernel.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlapped))
        os.close(descriptor)


def _sha(path):
    path = Path(path)
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _columns(value, name):
    if (
        not isinstance(value, (list, tuple))
        or not value
        or any(not isinstance(c, str) or not c for c in value)
    ):
        raise ValueError(f"{name}必须为明确列清单")
    if len(set(value)) != len(value):
        raise ValueError(f"{name}重复")
    return list(value)


def _validate_frame(frame, primary_key, business_columns):
    if not isinstance(frame, pd.DataFrame) or not frame.columns.is_unique:
        raise ValueError("数据必须为列名唯一的DataFrame")
    declared = []
    for columns, name in [
        (primary_key, "primary_key"),
        (business_columns, "business_columns"),
    ]:
        if columns:
            declared.extend(_columns(columns, name))
    if set(declared) - set(frame.columns):
        raise ValueError("声明主键或业务列不存在")
    if primary_key:
        keys = list(primary_key)
        if frame[keys].isna().any().any() or frame.duplicated(keys).any():
            raise ValueError("主键为空或重复，拒绝整批，不选择任意一行")


def count_changes(old, new, primary_key, *, business_columns):
    """按明确主键和业务列计量；技术时间列仅在明确声明时参与比较。

    未知资格保留 None；主键重复、空键或字段缺失拒绝整批。
    这是提交后的完整文件比较，新文件减少行不算新增或修订。
    """
    _validate_frame(old, primary_key, business_columns)
    _validate_frame(new, primary_key, business_columns)
    counts = dict(
        processed_rows=len(new),
        new_rows=None,
        revised_rows=None,
        duplicate_rows=None,
        rejected_rows=0,
        bytes_written=None,
        net_storage_bytes=None,
    )
    if not primary_key or not business_columns:
        return counts
    old_index = old.set_index(list(primary_key))
    new_index = new.set_index(list(primary_key))
    shared = new_index.index.intersection(old_index.index, sort=False)
    prior = old_index.loc[shared, list(business_columns)]
    current = new_index.loc[shared, list(business_columns)]
    # pandas NA、None、NaN 统一视为业务空值。
    equal = (prior.eq(current).fillna(False) | (prior.isna() & current.isna())).all(
        axis=1
    )
    counts.update(
        new_rows=len(new_index.index.difference(old_index.index)),
        revised_rows=int((~equal).sum()),
        duplicate_rows=int(equal.sum()),
    )
    return counts


def _events(store, run_id):
    result = []
    seq = 0
    while True:
        page = store.get_run(run_id, after_seq=seq, limit=500)
        result.extend(page["events"])
        if not page["has_more"]:
            return page, result
        seq = page["next_seq"]


def _unit(run, unit_id):
    matches = [u["plan"] for u in run["units"] if u["plan"]["unit_id"] == unit_id]
    if len(matches) != 1:
        raise ValueError("单元不属于冻结计划")
    return matches[0]


def _unrecorded(run, unit_ids):
    if any(
        u["result"] is not None
        for u in run["units"]
        if u["plan"]["unit_id"] in unit_ids
    ):
        raise ValueError("冻结单元已有结果，不可再次发布")


def _receipt_ids(events, table, unit_ids):
    return [
        e["payload"]["receipt_id"]
        for e in events
        if e["kind"] == "source_receipt"
        and e["payload"]["table"] == table
        and e["payload"]["unit_id"] in unit_ids
    ]


def _result(
    counts, events, receipt_ids, commit_id, *, status="succeeded", recovered=False
):
    receipts = [
        e["payload"]
        for e in events
        if e["kind"] == "source_receipt" and e["payload"]["receipt_id"] in receipt_ids
    ]
    result = dict(
        execution_status=status,
        receipt_ids=receipt_ids,
        commit_id=commit_id,
        error_kind=None,
        verification={"status": "unknown"} if recovered else None,
        **counts,
    )
    for key in ("provider", "channel", "source_id"):
        values = {receipt[key] for receipt in receipts}
        result[key] = values.pop() if len(values) == 1 else None
    return result


def _temporary(target):
    if not target.parent.is_dir():
        target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix="." + target.name + ".", suffix=".staged", dir=target.parent
    )
    os.close(descriptor)
    return Path(name)


def _sync(path):
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def _marker(root, intent, run_id, store):
    directory = _pending(root)
    if not directory.resolve().is_relative_to(root):
        raise ValueError("发布标记目录越出受控仓根")
    directory.mkdir(parents=True, exist_ok=True)
    target = _marker_path(root, intent["commit_id"])
    stage = _temporary(target)
    try:
        stage.write_text(
            json.dumps(
                dict(run_id=run_id, store_path=store.path, intent=intent),
                ensure_ascii=False,
                allow_nan=False,
            ),
            encoding="utf-8",
        )
        _sync(stage)
        os.replace(stage, target)
    finally:
        stage.unlink(missing_ok=True)
    return target


def _complete(intent, *, recovered=False):
    result = {
        k: intent[k]
        for k in (
            "commit_id",
            "table",
            "receipt_ids",
            "counts",
            "files",
            "manifest",
            "unit_id",
        )
        if k in intent
    }
    if recovered:
        result.update(recovered=True, verification={"status": "unknown"})
    return result


def _begin(store, run_id, root, intent, now):
    # 先持久化阻断标记，再登记意图，关闭event成功后立即崩溃的读空窗。
    marker = _marker(root, intent, run_id, store)
    try:
        store.event(run_id, "commit_intent", intent, now=now)
    except BaseException:
        # 若数据库事件是否成功也无法读取，保守保留标记；不误放行。
        try:
            _, events = _events(store, run_id)
            exists = any(
                e["kind"] == "commit_intent"
                and e["payload"]["commit_id"] == intent["commit_id"]
                for e in events
            )
        except Exception:
            exists = True
        if not exists:
            marker.unlink(missing_ok=True)
        raise
    return marker


def _record_results(store, run_id, intent, events, now, *, recovered=False):
    files = intent.get("files")
    unit_ids = (
        list(dict.fromkeys(f["unit_id"] for f in files))
        if files
        else [intent["unit_id"]]
    )
    existing = {
        u["plan"]["unit_id"]
        for u in store.get_run(run_id)["units"]
        if u["result"] is not None
    }
    for unit_id in unit_ids:
        if unit_id in existing:
            continue
        counts = (
            _sum_counts([f["counts"] for f in files if f["unit_id"] == unit_id])
            if files
            else intent["counts"]
        )
        receipts = _receipt_ids(events, intent["table"], {unit_id})
        receipts = [rid for rid in receipts if rid in intent["receipt_ids"]]
        store.record_unit(
            run_id,
            unit_id,
            _result(counts, events, receipts, intent["commit_id"], recovered=recovered),
            now=now,
        )


def _sum_counts(items):
    return {
        key: sum(item[key] for item in items)
        if all(item.get(key) is not None for item in items)
        else None
        for key in COUNT_FIELDS
    }


def commit_frame(
    frame, target, *, store, run_id, unit_id, primary_key, business_columns, now
):
    """同目录暂存、验证、意图、原子替换、完成登记；空响应保留旧文件。"""
    run, events = _events(store, run_id)
    root = _root(run["plan"]["resource"])
    unit = _unit(run, unit_id)
    _unrecorded(run, {unit_id})
    target = _inside(root, target)
    if target.suffix != ".parquet":
        raise ValueError("commit_frame只接受parquet目标")
    _validate_frame(frame, primary_key, business_columns)
    if run["status"] in TERMINAL:
        raise ValueError("终态不能执行新提交")
    receipts = _receipt_ids(events, unit["table"], {unit_id})
    with publication_guard(root, mode="write"):
        _ensure_clear(root)
        # 等待排他锁期间另一调用可能已完成该单元，锁前快照不可用于发布。
        run, events = _events(store, run_id)
        unit = _unit(run, unit_id)
        _unrecorded(run, {unit_id})
        if run["status"] in TERMINAL:
            raise ValueError("终态不能执行新提交")
        receipts = _receipt_ids(events, unit["table"], {unit_id})
        if frame.empty:
            counts = dict.fromkeys(COUNT_FIELDS, 0)
            if not primary_key or not business_columns:
                for key in ("new_rows", "revised_rows", "duplicate_rows"):
                    counts[key] = None
            result = _result(counts, events, receipts, None, status="source_empty")
            store.record_unit(run_id, unit_id, result, now=now)
            return result
        old = pd.read_parquet(target) if target.exists() else frame.iloc[:0]
        counts = count_changes(
            old, frame, primary_key, business_columns=business_columns
        )
        old_sha = _sha(target)
        old_size = target.stat().st_size if target.exists() else 0
        stage = _temporary(target)
        intent_recorded = False
        try:
            frame.to_parquet(stage, index=False)
            _sync(stage)
            staged = pd.read_parquet(stage)
            _validate_frame(staged, primary_key, business_columns)
            if not staged.equals(frame.reset_index(drop=True)):
                raise ValueError("Parquet暂存回读与输入不符")
            counts.update(
                bytes_written=stage.stat().st_size,
                net_storage_bytes=stage.stat().st_size - old_size,
            )
            intent = dict(
                commit_id=uuid.uuid4().hex,
                unit_id=unit_id,
                table=unit["table"],
                receipt_ids=receipts,
                target=str(target),
                staged_path=str(stage),
                old_sha256=old_sha,
                new_sha256=_sha(stage),
                counts=counts,
            )
            if _sha(target) != old_sha:
                raise ValueError("旧文件指纹变化")
            marker = _begin(store, run_id, root, intent, now)
            intent_recorded = True
            os.replace(stage, target)
            if _sha(target) != intent["new_sha256"]:
                raise ValueError("发布后的文件指纹不符")
            store.event(run_id, "commit_complete", _complete(intent), now=now)
            result = _result(counts, events, receipts, intent["commit_id"])
            store.record_unit(run_id, unit_id, result, now=now)
            marker.unlink()
            return result
        finally:
            # 登记意图后保留可核验的stage，恢复绝不自行重写它。
            if not intent_recorded:
                stage.unlink(missing_ok=True)


def commit_table(staged_files, manifest_update, *, store, run_id, table, now):
    """验证冻结清单后逐个替换，manifest最后，complete在write保护内。

    每项包含target/staged_path/old_sha256/new_sha256/unit_id/counts；
    primary_key/business_columns为明确资格，未提供时业务计量必须为None。
    manifest_update包含target/old_sha256/content，content为JSON对象。
    """
    run, events = _events(store, run_id)
    root = _root(run["plan"]["resource"])
    if (
        run["status"] in TERMINAL
        or not isinstance(staged_files, (list, tuple))
        or not staged_files
    ):
        raise ValueError("终态或空提交清单")
    if not isinstance(manifest_update, dict) or not isinstance(
        manifest_update.get("content"), dict
    ):
        raise ValueError("manifest必须包含新JSON内容")
    manifest_target = _inside(root, manifest_update["target"])
    if manifest_target.suffix != ".json" or "old_sha256" not in manifest_update:
        raise ValueError("manifest目标或预期旧指纹不合法")
    encoded = json.dumps(
        manifest_update["content"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    with publication_guard(root, mode="write"):
        _ensure_clear(root)
        run, events = _events(store, run_id)
        if run["status"] in TERMINAL:
            raise ValueError("终态不能执行新提交")
        if _sha(manifest_target) != manifest_update["old_sha256"]:
            raise ValueError("manifest旧指纹不符")
        files, targets = [], {manifest_target}
        for supplied in staged_files:
            if (
                not isinstance(supplied, dict)
                or not {
                    "target",
                    "staged_path",
                    "old_sha256",
                    "new_sha256",
                    "unit_id",
                    "counts",
                }
                <= supplied.keys()
            ):
                raise ValueError("分片必须声明完整事实")
            target, stage = (
                _inside(root, supplied["target"]),
                _inside(root, supplied["staged_path"]),
            )
            if (
                target.suffix != ".parquet"
                or target in targets
                or target == stage
                or target.parent != stage.parent
            ):
                raise ValueError("分片目标重复或暂存不在同目录")
            targets.add(target)
            if _unit(run, supplied["unit_id"])["table"] != table:
                raise ValueError("分片不属于本表冻结单元")
            _unrecorded(run, {supplied["unit_id"]})
            if (
                _sha(target) != supplied["old_sha256"]
                or _sha(stage) != supplied["new_sha256"]
                or not stage.is_file()
            ):
                raise ValueError("分片前后指纹不符")
            new = pd.read_parquet(stage)
            old = pd.read_parquet(target) if target.exists() else new.iloc[:0]
            counts = count_changes(
                old,
                new,
                supplied.get("primary_key"),
                business_columns=supplied.get("business_columns"),
            )
            counts.update(
                bytes_written=stage.stat().st_size,
                net_storage_bytes=stage.stat().st_size
                - (target.stat().st_size if target.exists() else 0),
            )
            if supplied["counts"] != counts:
                raise ValueError("分片计量与真实数据不符")
            _sync(stage)
            files.append(
                dict(
                    target=str(target),
                    staged_path=str(stage),
                    old_sha256=supplied["old_sha256"],
                    new_sha256=supplied["new_sha256"],
                    unit_id=supplied["unit_id"],
                    counts=counts,
                )
            )
        if any(Path(f["staged_path"]) in targets for f in files):
            raise ValueError("暂存路径不能覆盖其他冻结目标")
        if len({f["staged_path"] for f in files}) != len(files):
            raise ValueError("暂存文件重复")
        manifest_stage = _temporary(manifest_target)
        recorded = False
        try:
            manifest_stage.write_bytes(encoded)
            _sync(manifest_stage)
            if (
                json.loads(manifest_stage.read_text("utf-8"))
                != manifest_update["content"]
            ):
                raise ValueError("manifest回读不符")
            manifest = dict(
                target=str(manifest_target),
                old_sha256=manifest_update["old_sha256"],
                new_sha256=_sha(manifest_stage),
            )
            # manifest字节归属最后分片单元，防止单元汇总少算实际发布量。
            files[-1]["counts"]["bytes_written"] += manifest_stage.stat().st_size
            files[-1]["counts"]["net_storage_bytes"] += (
                manifest_stage.stat().st_size
                - (manifest_target.stat().st_size if manifest_target.exists() else 0)
            )
            counts = _sum_counts([f["counts"] for f in files])
            receipts = _receipt_ids(events, table, {f["unit_id"] for f in files})
            intent = dict(
                commit_id=uuid.uuid4().hex,
                table=table,
                receipt_ids=receipts,
                files=files,
                manifest=manifest,
                counts=counts,
            )
            marker = _begin(store, run_id, root, intent, now)
            recorded = True
            for file in files:
                os.replace(file["staged_path"], file["target"])
            os.replace(manifest_stage, manifest_target)
            if any(_sha(f["target"]) != f["new_sha256"] for f in files + [manifest]):
                raise ValueError("发布后清单指纹不符")
            store.event(run_id, "commit_complete", _complete(intent), now=now)
            _record_results(store, run_id, intent, events, now)
            marker.unlink()
            return _result(counts, events, receipts, intent["commit_id"])
        finally:
            if not recorded:
                manifest_stage.unlink(missing_ok=True)


def recover_commits(store, run_id):
    """仅核对已发布的新版本，不写数据、不重新claim、不重复计量。"""
    run, events = _events(store, run_id)
    root = _root(run["plan"]["resource"])
    results = []
    with publication_guard(root, mode="write"):
        # 锁内重新取快照，不能用排队前的完成事实作裁定。
        run, events = _events(store, run_id)
        completes = {
            e["payload"]["commit_id"]: e["payload"]
            for e in events
            if e["kind"] == "commit_complete"
        }
        for event in events:
            if event["kind"] != "commit_intent":
                continue
            intent = event["payload"]
            files = intent.get("files") or [intent]
            completed = completes.get(intent["commit_id"])
            unit_ids = {f["unit_id"] for f in files}
            missing = [
                u
                for u in run["units"]
                if u["plan"]["unit_id"] in unit_ids and u["result"] is None
            ]
            marker = _marker_path(root, intent["commit_id"])
            conflicts = [
                u
                for u in run["units"]
                if u["plan"]["unit_id"] in unit_ids
                and u["result"] is not None
                and u["result"]["commit_id"] != intent["commit_id"]
            ]
            if conflicts:
                # 同一冻结单元不可借用另一提交的结果清除待核验标记。
                if not marker.exists():
                    _marker(root, intent, run_id, store)
                results.append(
                    dict(
                        commit_id=intent["commit_id"],
                        status="unknown",
                        verification={"status": "unknown"},
                        reason="unit_commit_conflict",
                    )
                )
                continue
            # 已完成历史提交可以被后续合法版本替换，不重新阻断新版本。
            if completed and not missing and not marker.exists():
                results.append(
                    dict(
                        commit_id=intent["commit_id"],
                        status="complete",
                        verification={"status": "unknown"},
                    )
                )
                continue
            entries = files + ([intent["manifest"]] if intent.get("manifest") else [])
            all_new = True
            for entry in entries:
                target = _inside(root, entry["target"])
                if _sha(target) != entry["new_sha256"]:
                    all_new = False
                if "staged_path" in entry:
                    stage = _inside(root, entry["staged_path"])
                    if stage == target or stage.parent != target.parent:
                        raise ValueError("恢复暂存路径不合法")
                    if stage.exists() and _sha(stage) != entry["new_sha256"]:
                        all_new = False
            status = "unknown"
            if all_new:
                recovering_missing = (
                    completed and not completed.get("recovered") and bool(missing)
                )
                if recovering_missing:
                    store.event(
                        run_id,
                        "commit_recovery",
                        _complete(intent, recovered=True),
                        now=time.time(),
                    )
                if not completed:
                    store.event(
                        run_id,
                        "commit_complete",
                        _complete(intent, recovered=True),
                        now=time.time(),
                    )
                elif completed.get("recovered") and missing:
                    # 相同事件幂等重用，公共event仍检查新资源持有者，不能直接补unit绕过。
                    store.event(run_id, "commit_complete", completed, now=time.time())
                if missing:
                    _record_results(
                        store, run_id, intent, events, time.time(), recovered=True
                    )
                marker.unlink(missing_ok=True)
                status = (
                    "recovered" if not completed or recovering_missing else "complete"
                )
            if status == "unknown" and not marker.exists():
                _marker(root, intent, run_id, store)
            results.append(
                dict(
                    commit_id=intent["commit_id"],
                    status=status,
                    verification={"status": "unknown"},
                )
            )
    return results
