# -*- coding: utf-8 -*-
"""双轨幂等 Hook（M0 P4）。

用途：Paper Bridge 与 run_shadow 在不动旧幂等链的前提下，并行写入统一
IdempotencyStore（scope 区分来源）。此阶段旧链仍是权威：

  - paper_bridge: events trace 查重 + orders.ref 引擎查重（权威）
  - run_shadow:   shadow_ledger.jsonl date+symbol+done 查重（权威）

纪律：
  - fail-open：幂等账本任何异常都降级为 outcome=unavailable，绝不阻断旧链；
    每次降级记录 last_error 供巡检暴露；
  - 死信：claimed attempt 超过 stale_seconds 未回写终态，视为持有者已死，
    下次 claim 自动死信化并开新 attempt（进程崩溃恢复路径）；
  - IDEMPOTENCY_DISABLED=1 可整体停用（冒烟/测试用）。
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import logging

DEFAULT_DB = (
    Path(__file__).resolve().parent.parent / "data" / "idempotency" / "idempotency.db"
)


def _load_store_class():
    """按文件路径显式加载 idempotency.py（tools/ 与 agent_platform/ 双场景可用）。"""
    module_path = Path(__file__).resolve().parent / "idempotency.py"
    spec = importlib.util.spec_from_file_location("_idem_engine", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.IdempotencyStore


class DualTrackHook:
    """双轨幂等写入口。所有方法都不抛异常。"""

    def __init__(
        self, scope: str, db_path=None, stale_seconds: float = 3600.0, enabled=None
    ):
        self.scope = scope
        self.db_path = Path(db_path or os.environ.get("IDEMPOTENCY_DB") or DEFAULT_DB)
        self.stale_seconds = stale_seconds
        if enabled is None:
            self.enabled = os.environ.get("IDEMPOTENCY_DISABLED") != "1"
        else:
            self.enabled = bool(enabled)
        self._store = None
        self.last_error = None

    def _store_or_none(self):
        if self._store is None:
            self._store = _load_store_class()(self.db_path)
        return self._store

    def _fail_open(self, exc: Exception) -> dict:
        self.last_error = "%s: %s" % (type(exc).__name__, exc)
        return {"outcome": "unavailable", "error": self.last_error}

    def claim(self, operation: str, key: str, payload) -> dict:
        """认领。返回 outcome ∈ claimed/completed/in_progress/conflict/
        unavailable/disabled。"""
        if not self.enabled:
            return {"outcome": "disabled"}
        try:
            return self._store_or_none().claim(
                self.scope,
                operation,
                key,
                payload=payload,
                stale_seconds=self.stale_seconds,
            )
        except Exception as exc:  # noqa: BLE001  fail-open 纪律
            return self._fail_open(exc)

    def complete(
        self, attempt_id: str, ok: bool, result_ref: str = "", error: str = ""
    ):
        """回写终态。attempt_id 为空（unavailable/disabled 场景）直接忽略。"""
        if not self.enabled or not attempt_id:
            return None
        try:
            return self._store_or_none().complete(
                attempt_id, result_ref=result_ref, ok=ok, error=error
            )
        except Exception as exc:  # noqa: BLE001  fail-open 纪律
            return self._fail_open(exc)

    def close(self) -> None:
        if self._store is not None:
            try:
                self._store.close()
            except Exception as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/engine/idem_hook.py", exc)
            self._store = None
