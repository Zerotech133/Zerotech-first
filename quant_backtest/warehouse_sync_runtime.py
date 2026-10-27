"""受控冻结采集框架；模块导入不读配置、不建立连接、不启动线程。"""

import copy
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

import pandas as pd

from warehouse_commit import commit_table, count_changes, publication_guard
from warehouse_runs import COUNT_FIELDS, RunBusy, RunStore, TERMINAL
from warehouse_update import build_plan, metadata_digest, plan_digest
import logging


ADAPTERS = {
    "tdx": ("tdx_parquet_v1", {"paged_full", "paged_incremental"}),
    "tushare": ("tushare_frame_v1", {"full_replace", "per_code_incremental"}),
    # 数据地基模块章程 §2（2026-10-16 收编）：雪球 L1 走受控框架。
    # snap/northbound 按日落片=full_replace，kline/forecast 逐码增量。
    "xueqiu": ("xueqiu_frame_v1", {"full_replace", "per_code_incremental"}),
}
PARAMS = {
    "code",
    "ts_code",
    "start_date",
    "end_date",
    "trade_date",
    "date",
    "exchange",
    "market",
    "list_status",
    "src",
    "lv",
    "period",
}
_claim_lock = threading.Lock()
_active_runs = set()


class ProviderError(RuntimeError):
    def __init__(self, kind):
        if kind not in {
            "connection_error",
            "permission_denied",
            "rate_limited",
            "parse_error",
        }:
            kind = "unknown"
        self.kind = kind
        super().__init__(kind)


class RegistrationFailed(RuntimeError):
    pass


def process_identity(pid):
    """父子进程共用 Windows FILETIME；读不到身份返回 None，不猜测。"""
    if os.name != "nt" or type(pid) is not int or pid <= 0:
        return None
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
        ctypes.POINTER(wintypes.FILETIME)
    ] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    created, exited, kern, user = (wintypes.FILETIME() for _ in range(4))
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        if not kernel.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kern),
            ctypes.byref(user),
        ):
            return None
        return "windows-filetime:" + str(
            (created.dwHighDateTime << 32) | created.dwLowDateTime
        )
    finally:
        kernel.CloseHandle(handle)


def current_process_identity():
    pid = os.getpid()
    created = process_identity(pid)
    if created is None:
        raise RuntimeError("无法读取当前进程身份")
    return pid, created


def probe_process(pid, created_at):
    """只用 Windows 内核证据判断身份；权限或身份读取失败保留 unknown。"""
    if (
        os.name != "nt"
        or type(pid) is not int
        or not 0 < pid <= 0xFFFFFFFF
        or not isinstance(created_at, str)
        or not re.fullmatch(r"windows-filetime:[1-9][0-9]{0,19}", created_at)
    ):
        return "unknown"
    expected = int(created_at.split(":", 1)[1])
    if expected > 0xFFFFFFFFFFFFFFFF:
        return "unknown"
    from ctypes import wintypes

    handle, kernel = None, None
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
            ctypes.POINTER(wintypes.FILETIME)
        ] * 4
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return "dead" if ctypes.get_last_error() == 87 else "unknown"
        created, exited, kern, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kern),
            ctypes.byref(user),
        ):
            return "unknown"
        actual = (created.dwHighDateTime << 32) | created.dwLowDateTime
        if actual != expected:
            return "dead"
        exit_code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return "unknown"
        return "alive" if exit_code.value == 259 else "dead"
    except Exception:
        return "unknown"
    finally:
        if handle and kernel:
            try:
                kernel.CloseHandle(handle)
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/warehouse_sync_runtime.py", exc)


class Heartbeat:
    def __init__(self, store, run_id, *, pid, created_at, clock=time.time, interval=10):
        if interval != 10:
            raise ValueError("生产心跳间隔必须为10秒")
        self.store, self.run_id, self.pid, self.created_at = (
            store,
            run_id,
            pid,
            created_at,
        )
        self.clock, self.interval = clock, interval
        self.error, self.stop = None, threading.Event()
        self.thread = None

    def tick(self):
        try:
            self.store.heartbeat(
                self.run_id, pid=self.pid, created_at=self.created_at, now=self.clock()
            )
        except Exception as exc:
            self.error = exc
            self.stop.set()

    def check(self):
        if self.error is not None:
            raise RegistrationFailed("heartbeat_registration_failed") from self.error

    def start(self):
        self.tick()
        self.check()

        def loop():
            while not self.stop.wait(self.interval):
                self.tick()

        self.thread = threading.Thread(
            target=loop, daemon=True, name="warehouse-heartbeat"
        )
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=1)


def _sha(path):
    path = Path(path)
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path, fallback):
    path = Path(path)
    return (
        json.loads(path.read_text(encoding="utf-8-sig"))
        if path.exists()
        else copy.deepcopy(fallback)
    )


def read_local_state(source, *, data_root, resource):
    """重新读取受控本地资格与仓快照；不接受计划文件中的 current_*。"""
    from warehouse_metadata import snapshot

    root, data_root = Path(resource).resolve(), Path(data_root).resolve()
    catalog, registry, baseline = snapshot(
        data_root, data_root / "warehouse_registry.json"
    )
    if Path(registry["resource"][source]).resolve() != root:
        raise ValueError("执行仓根与共享快照不符")
    return {"catalog": catalog, "registry": registry, "baseline": baseline}


def _registry_rows(registry):
    rows, assets = {}, set()
    for row in registry.get("physical_tables", []):
        if row["asset_id"] in assets:
            raise ValueError("重复物理asset_id")
        assets.add(row["asset_id"])
        for table in row["physical_tables"]:
            if table in rows:
                raise ValueError("重复物理表")
            rows[table] = row
    return rows


def _target(root, unit):
    relative = unit["scope"]["relative_target"]
    if not isinstance(relative, str) or "\\" in relative:
        raise ValueError("目标必须为仓内相对路径")
    path = (root / relative).resolve()
    if (
        not path.is_relative_to(root)
        or path.parent != (root / unit["table"]).resolve()
        or path.suffix != ".parquet"
        or path.name.startswith(".")
    ):
        raise ValueError("目标不属于冻结表目录")
    return path


def qualify_execution_plan(plan, *, registry):
    """纯元数据资格门，供主计划门串行接入；不信任客户端 executable 位。"""
    if (
        plan["source"] not in ADAPTERS
        or plan["unsupported"]
        or not plan["units"]
        or plan.get("retry_of")
    ):
        raise ValueError("unsupported_execution_plan")
    rows, targets, units = _registry_rows(registry), set(), set()
    adapter_name, allowed = ADAPTERS[plan["source"]]
    operations = {}
    for operation in plan["operations"]:
        table = operation["table"]
        if table in operations or operation["operation"] not in allowed:
            raise ValueError("unsupported_operation")
        operations[table] = operation["operation"]
    root = Path(plan["resource"]).resolve()
    for unit in plan["units"]:
        row = rows.get(unit["table"])
        if not row or unit["table"] not in operations or unit["unit_id"] in units:
            raise ValueError("单元未获冻结资格")
        units.add(unit["unit_id"])
        adapter = row.get("execution_adapter", {})
        if (
            row.get("qualification_status") != "qualified"
            or row.get("supported_scope") != "frozen_units_v1"
            or adapter.get("name") != adapter_name
            or adapter.get("version") != 1
            or operations[unit["table"]] not in adapter.get("operations", [])
            or any(
                not isinstance(row.get(k), list)
                or not row[k]
                or any(not isinstance(c, str) or not c for c in row[k])
                or len(row[k]) != len(set(row[k]))
                for k in ("primary_key", "business_columns")
            )
            or any(
                not isinstance(row.get(k), str) or not row[k]
                for k in ("provider", "channel", "source_id")
            )
        ):
            raise ValueError("registry_execution_qualification_unknown")
        api_name = adapter.get("api_name", unit["table"])
        if (
            not isinstance(api_name, str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", api_name)
            or plan["source"] == "tdx"
            and api_name != unit["table"]
        ):
            raise ValueError("接口映射不合格")
        from warehouse_update import PROVIDER_CHANNELS  # 章程 §2 唯一登记处

        expected_channel = PROVIDER_CHANNELS[plan["source"]]
        if row["channel"] not in expected_channel:
            raise ValueError("source_mapping_mismatch")
        scope = unit["scope"]
        if (
            set(scope)
            != {"params", "relative_target", "page_size", "max_pages", "write_mode"}
            or not isinstance(scope["params"], dict)
            or set(scope["params"]) - PARAMS
            or scope["write_mode"] not in {"replace", "merge"}
            or type(scope["page_size"]) is not int
            or not 1 <= scope["page_size"] <= adapter.get("max_page_size", 0)
            or type(scope["max_pages"]) is not int
            or not 1 <= scope["max_pages"] <= adapter.get("max_pages", 0)
        ):
            raise ValueError("冻结请求范围不合格")
        if operations[unit["table"]] == "per_code_incremental" and (
            unit["kind"] != "code"
            or not scope["params"].get("ts_code")
            or scope["write_mode"] != "merge"
        ):
            raise ValueError("逐码范围未冻结")
        if unit["kind"] not in {"table", "code", "date"}:
            raise ValueError("分钟或窗口尚未接管")
        target = _target(root, unit)
        if target in targets:
            raise ValueError("多个单元不能覆盖同一文件")
        targets.add(target)
    if set(operations) != {u["table"] for u in plan["units"]}:
        raise ValueError("操作和单元未完全对齐")
    if plan["source"] == "tdx" and any(
        sum(u["table"] == table for u in plan["units"]) != 1 for table in operations
    ):
        raise ValueError("TDX目前仅接管单表单分片布局")
    return True


def _result(row, status, *, receipts=None, error=None):
    result = dict.fromkeys(COUNT_FIELDS)
    result.update(
        execution_status=status,
        provider=row.get("provider"),
        channel=row.get("channel"),
        source_id=row.get("source_id"),
        receipt_ids=receipts or [],
        commit_id=None,
        error_kind=error,
        verification={"status": "unknown"},
    )
    if status == "source_empty":
        result.update(dict.fromkeys(COUNT_FIELDS, 0))
    return result


def _all_events(store, run_id):
    events, cursor = [], 0
    while True:
        snapshot = store.get_run(run_id, after_seq=cursor, limit=500)
        events.extend(snapshot["events"])
        if not snapshot["has_more"]:
            return events
        cursor = snapshot["next_seq"]


def _unit_receipts(store, run_id, unit_id):
    return [
        e["payload"]["receipt_id"]
        for e in _all_events(store, run_id)
        if e["kind"] == "source_receipt" and e["payload"]["unit_id"] == unit_id
    ]


class _SessionStore:
    """登记故障后阻止后续提交消费者的所有新登记，保留其未恢复意图。"""

    def __init__(self, store, heartbeat):
        self.store, self.heartbeat = store, heartbeat
        self.path = store.path

    def get_run(self, *args, **kw):
        self.heartbeat.check()
        return self.store.get_run(*args, **kw)

    def event(self, *args, **kw):
        self.heartbeat.check()
        try:
            return self.store.event(*args, **kw)
        except Exception as exc:
            raise RegistrationFailed("event_registration_failed") from exc

    def record_unit(self, *args, **kw):
        self.heartbeat.check()
        try:
            return self.store.record_unit(*args, **kw)
        except Exception as exc:
            raise RegistrationFailed("unit_registration_failed") from exc


def _iso(clock):
    return datetime.fromtimestamp(clock(), timezone.utc).isoformat()


def _pull_unit(unit, row, *, store, run_id, request, heartbeat, clock):
    frames, receipts, seen = [], [], set()
    for page in range(unit["scope"]["max_pages"]):
        heartbeat.check()
        params = dict(
            unit["scope"]["params"],
            offset=page * unit["scope"]["page_size"],
            limit=unit["scope"]["page_size"],
        )
        requested = _iso(clock)
        kind, error, rows, cols, source_time = "unknown", None, None, None, None
        try:
            request_unit = dict(
                copy.deepcopy(unit),
                api_name=row["execution_adapter"].get("api_name", unit["table"]),
            )
            response = request(request_unit, copy.deepcopy(params))
            if not isinstance(response, dict):
                raise ProviderError("parse_error")
            kind = response.get("result_kind", "unknown")
            rows, cols, source_time = (
                response.get("rows"),
                response.get("columns"),
                response.get("source_time"),
            )
            if kind not in {"authorized_data", "authorized_empty"}:
                error = kind
            elif (
                not isinstance(rows, list)
                or not isinstance(cols, list)
                or any(not isinstance(c, str) or not c for c in cols)
                or len(cols) != len(set(cols))
                or any(not isinstance(r, list) or len(r) != len(cols) for r in rows)
                or len(rows) > unit["scope"]["page_size"]
                or (kind == "authorized_empty") != (len(rows) == 0)
            ):
                kind, error, rows = "parse_error", "response_contract_invalid", None
        except ProviderError as exc:
            kind, error = exc.kind, exc.kind
        except Exception:
            kind, error = "connection_error", "request_failed"
        receipt_id = uuid.uuid4().hex
        payload = dict(
            receipt_id=receipt_id,
            run_id=run_id,
            unit_id=unit["unit_id"],
            table=unit["table"],
            provider=row["provider"],
            channel=row["channel"],
            source_id=row["source_id"],
            api_name=row["execution_adapter"].get("api_name", unit["table"]),
            request_scope=params,
            requested_at=requested,
            received_at=_iso(clock),
            source_time=source_time,
            result_rows=len(rows) if rows is not None else None,
            result_kind=kind,
            error_kind=error,
        )
        store.event(run_id, "source_receipt", payload, now=clock())
        receipts.append(receipt_id)
        if error:
            return None, _result(row, "failed", receipts=receipts, error=error)
        if rows:
            signature = metadata_digest(rows)
            if signature in seen:
                return None, _result(
                    row, "rejected", receipts=receipts, error="repeated_page"
                )
            seen.add(signature)
            frame = pd.DataFrame(rows, columns=cols)
            if any(
                c not in frame for c in row["primary_key"] + row["business_columns"]
            ):
                return None, _result(
                    row, "rejected", receipts=receipts, error="required_columns_missing"
                )
            for code_key in ("code", "ts_code"):
                if code_key in params and (
                    code_key not in frame
                    or any(
                        str(value) != str(params[code_key]) for value in frame[code_key]
                    )
                ):
                    return None, _result(
                        row,
                        "rejected",
                        receipts=receipts,
                        error="source_scope_mismatch",
                    )
            date_col = row.get("date_column")
            for parameter in ("start_date", "end_date", "trade_date"):
                if parameter not in params:
                    continue
                if not date_col or date_col not in frame:
                    return None, _result(
                        row,
                        "rejected",
                        receipts=receipts,
                        error="date_scope_unverifiable",
                    )
                values = (
                    frame[date_col]
                    .astype(str)
                    .str.replace("-", "", regex=False)
                    .str.slice(0, 8)
                )
                bound = str(params[parameter]).replace("-", "")
                outside = (
                    (values < bound).any()
                    if parameter == "start_date"
                    else (values > bound).any()
                    if parameter == "end_date"
                    else (values != bound).any()
                )
                if not values.str.fullmatch(r"\d{8}").all() or outside:
                    return None, _result(
                        row,
                        "rejected",
                        receipts=receipts,
                        error="source_scope_mismatch",
                    )
            frames.append(frame)
        store.event(
            run_id,
            "progress",
            dict(
                stage="fetching",
                table=unit["table"],
                unit_id=unit["unit_id"],
                page=page + 1,
                processed_rows=sum(len(f) for f in frames),
                total_rows=None,
            ),
            now=clock(),
        )
        if len(rows) < unit["scope"]["page_size"]:
            if not frames:
                return None, _result(row, "source_empty", receipts=receipts)
            return pd.concat(frames, ignore_index=True), None
    return None, _result(
        row, "failed", receipts=receipts, error="frozen_page_limit_reached"
    )


def _execute_table(
    table,
    units,
    row,
    *,
    root,
    store,
    run_id,
    request,
    heartbeat,
    clock,
    operation,
    frozen_files,
):
    collected, outcomes = [], []
    for unit in units:
        target = _target(root, unit)
        if operation == "per_code_incremental" and not target.exists():
            outcomes.append(
                (
                    unit,
                    _result(row, "no_local_file", error="initialization_unsupported"),
                )
            )
            continue
        frame, outcome = _pull_unit(
            unit,
            row,
            store=store,
            run_id=run_id,
            request=request,
            heartbeat=heartbeat,
            clock=clock,
        )
        if outcome is not None:
            outcomes.append((unit, outcome))
        else:
            collected.append((unit, frame, target))
    for unit, outcome in outcomes:
        store.record_unit(run_id, unit["unit_id"], outcome, now=clock())
    if any(result["execution_status"] != "source_empty" for _, result in outcomes):
        for unit, frame, target in collected:
            ids = _unit_receipts(store, run_id, unit["unit_id"])
            store.record_unit(
                run_id,
                unit["unit_id"],
                _result(row, "blocked", receipts=ids, error="table_batch_failed"),
                now=clock(),
            )
        return
    if not collected:
        return
    heartbeat.check()
    stages = []
    with publication_guard(root, mode="read"):
        manifest = _json(root / "_manifest.json", {})
        manifest_sha = _sha(root / "_manifest.json")
        existing = {
            p.resolve()
            for p in (root / table).glob("*.parquet")
            if not p.name.startswith(".")
        }
        actual_files = {
            str(p): {"bytes": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns}
            for p in existing
        }
        expected_files = {
            str((root.parent / relative).resolve()): record
            for relative, record in frozen_files.items()
        }
        if actual_files != expected_files:
            raise ValueError("采集期间冻结文件基线发生变化")
        replacing = {
            target
            for unit, frame, target in collected
            if unit["scope"]["write_mode"] == "replace"
        }
        if replacing and existing - {target for unit, frame, target in collected}:
            raise ValueError("整表替换未冻结全部旧文件")
        snapshots = []
        for unit, frame, target in collected:
            old = pd.read_parquet(target) if target.exists() else frame.iloc[:0]
            if unit["scope"]["write_mode"] == "merge":
                # 明确登记主键，以新来源覆写重叠键；拒绝新响应本身的重复键。
                if frame.duplicated(row["primary_key"]).any():
                    raise ValueError("源响应主键重复")
                frame = pd.concat([old, frame], ignore_index=True).drop_duplicates(
                    row["primary_key"], keep="last"
                )
            snapshots.append(
                (
                    unit,
                    frame,
                    target,
                    old,
                    _sha(target),
                    target.stat().st_size if target.exists() else 0,
                )
            )
    loose_stages = []
    try:
        for unit, frame, target, old, old_sha, old_size in snapshots:
            heartbeat.check()
            target.parent.mkdir(parents=True, exist_ok=True)
            counts = count_changes(
                old, frame, row["primary_key"], business_columns=row["business_columns"]
            )
            stage = target.with_name(
                "." + target.name + ".runtime-" + uuid.uuid4().hex + ".tmp"
            )
            loose_stages.append(stage)
            frame.to_parquet(stage, index=False)
            counts.update(
                bytes_written=stage.stat().st_size,
                net_storage_bytes=stage.stat().st_size - old_size,
            )
            stages.append(
                dict(
                    target=str(target),
                    staged_path=str(stage),
                    old_sha256=old_sha,
                    new_sha256=_sha(stage),
                    unit_id=unit["unit_id"],
                    counts=counts,
                    primary_key=row["primary_key"],
                    business_columns=row["business_columns"],
                )
            )
        manifest[table] = dict(
            manifest.get(table, {}),
            done=True,
            run_id=run_id,
            updated_at=_iso(clock),
            last_frozen_units=[u["unit_id"] for u in units],
        )
        heartbeat.check()
        commit_table(
            stages,
            {
                "target": str(root / "_manifest.json"),
                "old_sha256": manifest_sha,
                "content": manifest,
            },
            store=store,
            run_id=run_id,
            table=table,
            now=clock(),
        )
    finally:
        # 只清理已证明未进入持久意图的本次暂存；登记库不可读时保留证据。
        try:
            owned = {
                f["staged_path"]
                for e in _all_events(store, run_id)
                if e["kind"] == "commit_intent"
                for f in e["payload"].get("files", [])
            }
        except Exception:
            owned = None
        if owned is not None:
            for stage in loose_stages:
                if str(stage) not in owned:
                    stage.unlink(missing_ok=True)


def run_frozen_plan(
    plan,
    *,
    store,
    entry,
    request_id,
    resource,
    state_reader,
    request,
    run_id=None,
    clock=time.time,
    identity=current_process_identity,
    heartbeat_interval=10,
    validator=None,
):
    """共同入口；生产默认使用主计划门，测试可注入可信资格门和供应响应。"""
    plan = copy.deepcopy(plan)
    root = Path(resource).resolve()
    if entry not in {"cli", "manual", "scheduled"} or plan.get(
        "plan_digest"
    ) != plan_digest(plan):
        raise ValueError("entry或计划摘要无效")
    if not root.is_absolute() or Path(plan["resource"]).resolve() != root:
        raise ValueError("计划资源与执行仓根不一致")
    if run_id is None:
        run_id = store.create_run(plan, entry=entry, request_id=request_id, now=clock())
    with _claim_lock:
        prior = store.get_run(run_id)
        if prior["plan"] != plan:
            raise ValueError("预登记计划不匹配")
        if prior["status"] in TERMINAL:
            return dict(
                run_id=run_id, status=prior["status"], exit_code=prior["exit_code"]
            )
        if run_id in _active_runs:
            raise RunBusy("同进程任务已在执行")
        pid, created_at = identity()
        store.claim(run_id, pid=pid, created_at=created_at, now=clock())
        _active_runs.add(run_id)
    heartbeat = Heartbeat(
        store,
        run_id,
        pid=pid,
        created_at=created_at,
        clock=clock,
        interval=heartbeat_interval,
    )
    protected = _SessionStore(store, heartbeat)
    try:
        heartbeat.start()
        try:
            state = state_reader()
            if validator is None:
                from warehouse_update import validate_plan

                validator = validate_plan
            validator(
                plan,
                current_baseline=state["baseline"],
                current_registry=state["registry"],
            )
            qualify_execution_plan(plan, registry=state["registry"])
            for unit in plan["units"]:
                meta = state["catalog"]["tables"].get(unit["table"], {})
                from warehouse_update import PROVIDER_CHANNELS  # 章程 §2

                source_channels = PROVIDER_CHANNELS[plan["source"]]
                if (
                    meta.get("source") not in source_channels
                    or meta.get("layout") == "sqlite"
                    or not meta.get("store")
                    or Path(meta["store"]).resolve() != root / unit["table"]
                ):
                    raise ValueError("物理来源或存储布局未接管")
                if plan["source"] == "tdx":
                    files = state["baseline"]["tables"][unit["table"]]["files"]
                    expected = {
                        str((root.parent / relative).resolve()) for relative in files
                    }
                    if expected - {str(_target(root, unit))}:
                        raise ValueError("TDX多分片历史仓尚未接管")
        except RegistrationFailed:
            raise
        except Exception:
            for unit in plan["units"]:
                protected.record_unit(
                    run_id,
                    unit["unit_id"],
                    _result({}, "unsupported", error="execution_qualification_failed"),
                    now=clock(),
                )
            store.finish(run_id, "blocked", exit_code=4, now=clock())
            return dict(run_id=run_id, status="blocked", exit_code=4)
        rows = _registry_rows(state["registry"])
        for op in plan["operations"]:
            table_units = [u for u in plan["units"] if u["table"] == op["table"]]
            row = dict(
                rows[op["table"]],
                date_column=state["catalog"]["tables"][op["table"]].get("date_col"),
            )
            try:
                _execute_table(
                    op["table"],
                    table_units,
                    row,
                    root=root,
                    store=protected,
                    run_id=run_id,
                    request=request,
                    heartbeat=heartbeat,
                    clock=clock,
                    operation=op["operation"],
                    frozen_files=state["baseline"]["tables"][op["table"]]["files"],
                )
            except RegistrationFailed:
                raise
            except Exception:
                snapshot = store.get_run(run_id)
                for item in snapshot["units"]:
                    if item["plan"]["table"] == op["table"] and item["result"] is None:
                        protected.record_unit(
                            run_id,
                            item["plan"]["unit_id"],
                            _result(
                                rows[op["table"]],
                                "failed",
                                receipts=_unit_receipts(
                                    protected, run_id, item["plan"]["unit_id"]
                                ),
                                error="table_execution_failed",
                            ),
                            now=clock(),
                        )
        heartbeat.check()
        final = store.get_run(run_id)
        successes = sum(
            u["result"]["execution_status"] in {"succeeded", "source_empty"}
            for u in final["units"]
        )
        failed = len(final["units"]) - successes
        status = (
            "partial" if failed and successes else "failed" if failed else "succeeded"
        )
        code = 2 if status == "partial" else 1 if status == "failed" else 0
        if status == "succeeded":
            protected.event(run_id, "progress", {"stage": "verifying"}, now=clock())
        store.finish(run_id, status, exit_code=code, now=clock())
        return dict(run_id=run_id, status=status, exit_code=code)
    except Exception:
        # 无法登记终态时保留活锁，由真实进程身份恢复；绝不再发请求或写数据。
        try:
            store.finish(run_id, "failed", exit_code=1, now=clock())
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/warehouse_sync_runtime.py", exc)
        return dict(run_id=run_id, status="failed", exit_code=1)
    finally:
        heartbeat.close()
        with _claim_lock:
            _active_runs.discard(run_id)


def run_collector_cli(source, args, *, warehouse_root, here, request):
    """计划只按受控digest目录解析；普通CLI先预览并登记，未接管范围不执行。"""
    data_root, root = Path(here).resolve() / "data", Path(warehouse_root).resolve()
    if getattr(args, "dryrun", False):
        return 0  # 不加载凭据、不调用供应接口、不创建任务库或发布锁。
    plan_id = getattr(args, "plan_id", None)
    if plan_id and not re.fullmatch(r"[0-9a-f]{64}", plan_id):
        raise ValueError("plan-id必须为SHA256摘要")
    root.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    state_reader = lambda: read_local_state(source, data_root=data_root, resource=root)
    state = state_reader()
    if plan_id:
        path = data_root / "warehouse_plans" / (plan_id + ".json")
        if (
            not path.resolve().is_relative_to(data_root)
            or path.resolve().parent != (data_root / "warehouse_plans").resolve()
            or path.is_symlink()
        ):
            raise ValueError("计划引用不在受控目录")
        plan = _json(path, None)
        if (
            not isinstance(plan, dict)
            or plan.get("plan_digest") != plan_id
            or plan.get("source") != source
        ):
            raise ValueError("受控计划缺失或来源不符")
    else:
        if getattr(args, "run_id", None):
            raise ValueError("预登记run-id必须同时指定plan-id")
        plan = build_plan(
            source,
            getattr(args, "market", "all"),
            datetime.now().astimezone().date().isoformat(),
            now=datetime.now().astimezone(),
            catalog=state["catalog"],
            registry=state["registry"],
            baseline=state["baseline"],
            selected_tables=[
                t.strip() for t in getattr(args, "tables", "").split(",") if t.strip()
            ]
            or None,
        )
        if plan["resource"] is None:
            plan["resource"] = str(root)
        if (
            getattr(args, "batch", None)
            or getattr(args, "missing_keys_json", None)
            or getattr(args, "include_rtmin", False)
        ):
            plan["unsupported"].append(
                {"table": None, "reason": "legacy_batch_not_integrated"}
            )
        plan["plan_digest"] = plan_digest(plan)
    store = RunStore(data_root / "warehouse_runs.sqlite3")
    result = run_frozen_plan(
        plan,
        store=store,
        entry=args.entry,
        request_id=getattr(args, "request_id", None) or uuid.uuid4().hex,
        resource=root,
        state_reader=state_reader,
        request=request,
        run_id=getattr(args, "run_id", None),
    )
    print(json.dumps(result, ensure_ascii=False))
    return result["exit_code"]
