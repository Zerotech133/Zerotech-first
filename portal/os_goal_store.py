# -*- coding: utf-8 -*-
"""OS 事项账本 v1.1（G0：append-only 事件账本 + 哈希链 + 投影）

设计（对应 docs/specs/OS_GOAL_ITEM_OUTCOME_v1_1.md 与合同判据）：
- 单一事实源：`portal/data/os_goals/events.jsonl`，只追加、不修改、不删除；
- 每事件：schema_version/seq/event_id/type/at/actor/refs/payload/prev_hash/hash；
- 哈希链：hash = sha256(canonical(事件去掉 hash))；篡改/换位/补写必断链；
- 单写者：文件锁（Windows msvcrt / 其它平台 fcntl）；写前校验 seq 连续；
- 幂等：变更类命令带 idempotency_key，重复键 → DuplicateKey（API 409）；
- 投影：内存字典由事件重放生成，可随时重建；API 只读投影，不直接读 JSONL。
- 范围：G0 只有 Goal/Item/Outcome/Citation；授权、工单、执行、验收在 G1+ 接入。

测试与手工验证均可用 base_dir 指向临时目录；生产默认 portal/data/os_goals。
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional
import logging

try:  # 组合判据（同目录模块）
    import os_goal_contract as C
except ImportError:  # 以包方式导入时
    from . import os_goal_contract as C  # type: ignore

BEIJING = timezone(timedelta(hours=8))
DEFAULT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "os_goals"
)
LEDGER = "events.jsonl"
LOCKFILE = ".lock"


class LedgerError(RuntimeError):
    """账本损坏/读取失败（fail-visible）。"""


def _now() -> str:
    return datetime.now(BEIJING).isoformat(timespec="seconds")


_LOCK_TIMEOUT_S = 10.0


@contextmanager
def _file_lock(path: str) -> Iterator[None]:
    """跨进程单写锁（Windows msvcrt.locking；失败回退进程内锁）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # 2026-11-07 K-ORG-20261107-LOCKOPEN：open 与加锁共用同一个 deadline。
    # 原先 os.open 写在等待逻辑之外，Windows 上并发创建/打开同一文件会瞬时
    # Errno 13（共享模式竞争 / 安全软件持有扫描句柄，inbox_gateway 实测约
    # 16%），异常直接抛给调用方 → 看到的是"账本写入失败"，而加锁侧明明会等
    # 10s。同一函数里两套等待语义，与 tools/devtask.py::_tasks_file_lock 的
    # 正确形态（open 也在重试循环内）不一致。超时后仍按原异常类型抛出。
    deadline = time.time() + _LOCK_TIMEOUT_S
    fd = None
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_RDWR)
            break
        except PermissionError:
            if time.time() > deadline:
                raise
            time.sleep(0.05)
    locked = False
    try:
        try:
            import msvcrt

            # 复用上面同一个 deadline：open 与加锁共享 10s 总预算，
            # 否则 open 重试 + 加锁重试会变成 20s，上层超时口径漂移。
            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError:
                    if time.time() > deadline:
                        raise LedgerError("获取账本写锁超时：%s" % path)
                    time.sleep(0.05)
        except ImportError:  # 非 Windows：回退进程内锁
            locked = True
        yield
    finally:
        if locked:
            try:
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except (ImportError, OSError) as exc:
                logging.getLogger(__name__).warning("silent-except %s: %s", "portal/os_goal_store.py", exc)
        os.close(fd)


class OsGoalStore:
    def __init__(self, base_dir: Optional[str] = None) -> None:
        self.base_dir = base_dir or os.environ.get("OS_GOAL_DATA_DIR") or DEFAULT_DIR
        os.makedirs(self.base_dir, exist_ok=True)
        self.events_path = os.path.join(self.base_dir, LEDGER)
        self.lock_path = os.path.join(self.base_dir, LOCKFILE)
        self._mu = threading.RLock()
        self._events: List[Dict[str, Any]] = []
        self._goals: Dict[str, Dict[str, Any]] = {}
        self._items: Dict[str, Dict[str, Any]] = {}
        self._outcomes: List[Dict[str, Any]] = []
        self._first_conf: Dict[str, str] = {}
        self._idem: Dict[str, str] = {}
        self._load()

    # ------------------------------------------------------------ 读取/投影
    def _load(self) -> None:
        if not os.path.isfile(self.events_path):
            return
        with open(self.events_path, encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise LedgerError("第 %d 行不是合法 JSON：%s" % (lineno, exc))
                self._events.append(ev)
        try:
            self._replay()
        except (C.ContractError, LedgerError):
            raise
        except Exception as exc:  # pragma: no cover - 防御
            raise LedgerError("账本重放失败：%r" % exc)

    def _replay(self) -> None:
        self._goals, self._items, self._outcomes = {}, {}, []
        self._first_conf, self._idem = {}, {}
        prev = None
        for expect, ev in enumerate(self._events, 1):
            errs = C.validate_envelope(ev, prev, expect)
            if errs:
                raise LedgerError(
                    "账本损坏（seq=%r）：%s" % (ev.get("seq"), "；".join(errs))
                )
            prev = ev["hash"]
            self._apply(ev)

    def _apply(self, ev: Dict[str, Any]) -> None:
        payload, refs, etype = ev["payload"], ev["refs"], ev["type"]
        if etype == "goal.created":
            C.check_goal(payload, ev["actor"])
            self._goals[payload["goal_id"]] = dict(payload)
        elif etype == "goal.updated":
            gid = payload.get("goal_id")
            goal = self._goals.get(gid)
            if goal is None:
                raise C.ContractError("目标不存在：%r" % gid)
            C.check_goal_update(payload, goal)
            for a in goal["assumptions"]:
                if str(a.get("id")) == str(payload["assumption_id"]):
                    a["status"] = payload["status"]
        elif etype == "item.created":
            gid = refs.get("goal_id")
            C.check_item(payload, self._goals.get(gid))
            if payload["item_id"] in self._items:
                raise C.ContractError("item_id 重复：%r" % payload["item_id"])
            self._items[payload["item_id"]] = {
                "payload": dict(payload),
                "goal_id": gid,
                "seq": ev["seq"],
            }
        elif etype == "outcome.produced":
            iid = refs.get("item_id")
            item = self._items.get(iid)
            C.check_outcome(
                payload, item["payload"] if item else None, self._first_conf
            )
            self._outcomes.append(
                {"item_id": iid, "seq": ev["seq"], "payload": dict(payload)}
            )
        elif etype == "item.cancelled":
            iid = refs.get("item_id")
            if iid not in self._items:
                raise C.ContractError("事项不存在：%r" % iid)
            self._items[iid]["cancelled"] = True
        if ev.get("idempotency_key"):
            self._idem[str(ev["idempotency_key"])] = ev.get("event_id", "")

    # ------------------------------------------------------------ 写入
    def _append(
        self,
        etype: str,
        refs: Dict[str, Any],
        payload: Dict[str, Any],
        actor: Optional[Dict[str, Any]] = None,
        idem: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._mu, _file_lock(self.lock_path):
            if idem:
                key = str(idem)
                if key in self._idem:
                    raise C.DuplicateKey("重复提交：idempotency_key 已存在（%s）" % key)
            seq = len(self._events) + 1
            ev = {
                "schema_version": C.SCHEMA_VERSION,
                "seq": seq,
                "event_id": "EVT-OSG0-%s-%03d" % (time.strftime("%Y%m%d"), seq),
                "type": etype,
                "at": _now(),
                "actor": actor or {"kind": "user", "id": "laotian"},
                "refs": refs or {},
                "payload": payload,
                "prev_hash": self._events[-1]["hash"] if self._events else None,
            }
            if idem:
                ev["idempotency_key"] = str(idem)
            ev["hash"] = C.event_hash(ev)
            self._apply(ev)  # 先校验再生效；失败不落盘
            line = json.dumps(ev, ensure_ascii=False) + "\n"
            with open(self.events_path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
            self._events.append(ev)
            return ev

    def _next_id(self, prefix: str, existing) -> str:
        day = time.strftime("%Y%m%d")
        for n in range(1, 10000):
            cand = "%s-%s-%04d" % (prefix, day, n)
            if cand not in existing:
                return cand
        raise LedgerError("%s id 空间用尽" % prefix)

    # ------------------------------------------------------------ 领域操作
    def append_event(self, etype, refs, payload, actor=None, idem=None):
        """通用追加（G1 流程事件；语义校验由 os_software_flow 负责）。"""
        if etype not in C.EVENT_TYPES:
            raise C.ContractError("未知事件类型：%r" % etype)
        return self._append(etype, refs, payload, actor=actor, idem=idem)

    def events_for(self, item_id):
        """取某事项相关事件（按 seq 升序，含目标级事件）。"""
        item = self._items.get(item_id) or {}
        gid = item.get("goal_id")
        return [
            ev
            for ev in self._events
            if ev["refs"].get("item_id") == item_id
            or ev["payload"].get("item_id") == item_id
            or (
                gid
                and (
                    ev["refs"].get("goal_id") == gid
                    or ev["payload"].get("goal_id") == gid
                )
            )
        ]

    def create_goal(
        self,
        statement: str,
        target_user: str,
        assumptions: List[Dict[str, Any]],
        success_signals: List[str],
        idem: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {
            "goal_id": self._next_id("GOL", self._goals),
            "statement": statement,
            "target_user": target_user,
            "assumptions": assumptions,
            "success_signals": success_signals,
        }
        return self._append("goal.created", {}, payload, idem=idem)

    def update_assumption(
        self,
        goal_id: str,
        assumption_id: str,
        status: str,
        evidence_refs: Optional[List[str]] = None,
        reason: str = "",
        idem: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {
            "goal_id": goal_id,
            "assumption_id": assumption_id,
            "status": status,
            "evidence_refs": evidence_refs or [],
            "reason": reason,
        }
        return self._append("goal.updated", {}, payload, idem=idem)

    def create_item(
        self,
        goal_id: str,
        title: str,
        why_now: str,
        outcome_kind: str,
        attempt_no: int = 1,
        idem: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {
            "item_id": self._next_id("ITM", self._items),
            "title": title,
            "why_now": why_now,
            "outcome_kind": outcome_kind,
            "attempt_no": attempt_no,
        }
        return self._append("item.created", {"goal_id": goal_id}, payload, idem=idem)

    def produce_outcome(
        self,
        item_id: str,
        outcome_kind: str,
        version: str,
        content_ref: str,
        sha256: str,
        citations: List[Dict[str, Any]],
        evidence_refs: Optional[List[str]] = None,
        idem: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {
            "outcome_kind": outcome_kind,
            "version": version,
            "content_ref": content_ref,
            "sha256": sha256,
            "citations": citations,
            "evidence_refs": evidence_refs or [],
        }
        return self._append(
            "outcome.produced", {"item_id": item_id}, payload, idem=idem
        )

    # ------------------------------------------------------------ 查询
    def list_goals(self) -> List[Dict[str, Any]]:
        out = []
        for gid, g in self._goals.items():
            items = [
                i["payload"]["item_id"]
                for i in self._items.values()
                if i["goal_id"] == gid
            ]
            out.append({**g, "items": items})
        return out

    def get_goal(self, goal_id: str) -> Dict[str, Any]:
        goal = self._goals.get(goal_id)
        if goal is None:
            raise KeyError("目标不存在：%r" % goal_id)
        items = [
            self._item_view(iid)
            for iid, it in self._items.items()
            if it["goal_id"] == goal_id
        ]
        return {**goal, "items": items}

    def _item_view(self, item_id: str) -> Dict[str, Any]:
        it = self._items[item_id]
        outs = [o["payload"] for o in self._outcomes if o["item_id"] == item_id]
        return {
            **it["payload"],
            "goal_id": it["goal_id"],
            "cancelled": bool(it.get("cancelled")),
            "outcomes": outs,
        }

    def get_item(self, item_id: str) -> Dict[str, Any]:
        if item_id not in self._items:
            raise KeyError("事项不存在：%r" % item_id)
        return self._item_view(item_id)

    def evidence_bundle(self, item_id: str) -> Dict[str, Any]:
        item = self.get_item(item_id)
        related = [
            ev
            for ev in self._events
            if ev["refs"].get("item_id") == item_id
            or ev["payload"].get("item_id") == item_id
            or ev["refs"].get("goal_id") == item.get("goal_id")
            or ev["payload"].get("goal_id") == item.get("goal_id")
        ]
        # 判据报告以整本账为准（避免子集序号不连续）；明细只取该事项相关事件
        return {
            "item": item,
            "report": C.evaluate(self._events),
            "events": related,
            "event_count": len(related),
        }

    def report(self) -> Dict[str, Any]:
        return C.evaluate(self._events)

    def health(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "data_dir": self.base_dir,
            "events": len(self._events),
            "goals": len(self._goals),
            "items": len(self._items),
            "outcomes": len(self._outcomes),
            "last_hash": self._events[-1]["hash"] if self._events else None,
        }
