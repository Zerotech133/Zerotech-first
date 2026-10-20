"""签准网关（approval_gate）。

职责：本人签准与审批账本。待签事项必须绑定材料指纹；只有老田本人
可闭环签准。红线：自报 USER/confirmed_by、会议 confirmed、integrity=valid、
门户 token 均不构成本人签准；本网关无任何自动放行路径。

红线（本模块全文件适用，写入代码注释并受其约束）：
    principal_proof 只是记录（原样透传落盘），不构成认证。
    本人身份核验方式待老田与安全席裁定；本模块不实现、不得实现
    任何认证判断或自动放行路径。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from roles.base_agent import BaseAgent

# 默认账本路径（相对工作目录）；测试场景经构造参数注入 tmp_path。
DEFAULT_LEDGER_PATH = Path("data") / "approval_ledger.jsonl"


class ApprovalGate(BaseAgent):
    def __init__(self, ledger_path: Optional[Path] = None, **kw: Any) -> None:
        super().__init__(name="approval_gate", role="guard:approval",
                         level="principal", **kw)
        self.ledger_path = Path(ledger_path) if ledger_path else DEFAULT_LEDGER_PATH
        # 单进程内互斥；跨进程并发需文件级锁。
        # TODO(领域实现): 文件级锁（如 flock/lockfile），当前仅保证单进程互斥。
        self._lock = threading.Lock()

    # ---- 材料指纹：确认"签的是哪份材料" ----

    @staticmethod
    def fingerprint(payload: Any) -> str:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # ---- 待签事项登记 ----

    def submit(self, item: Dict[str, Any]) -> str:
        """登记待签事项（含材料指纹），追加写入账本，返回 item_id。

        账本为不可变追加日志（jsonl）：每次 submit 追加一条
        status="pending" 条目，含 item_id / fingerprint / submitted_ts / ts。

        TODO(领域实现):
        - 事项分类（交易/生产变更/发布/派工）与对应审批五要素校验；
        - 逾期提醒与作废。
        """
        item_id = str(item.get("item_id") or uuid.uuid4().hex)
        now = time.time()
        entry: Dict[str, Any] = {
            "item_id": item_id,
            "fingerprint": self.fingerprint(item),
            "submitted_ts": now,
            "ts": now,
            "status": "pending",
        }
        with self._lock:
            self._append_locked(entry)
        return item_id

    # ---- 本人签准（强制人工） ----

    def approve(self, item_id: str, expected_fingerprint: str,
                principal_proof: Dict[str, Any]) -> Dict[str, Any]:
        """本人确认签准。

        锁内读账本 → 找该 item 最新条目 → 比对指纹 → 追加
        status="approved" 条目（principal_proof 原样透传落盘）。

        错误协议：
        - 事项不存在 → {"ok": False, "err": "item not found"}
        - 最新条目非 pending → {"ok": False, "err": "item not pending"}
        - 指纹不匹配 → {"ok": False, "err": "fingerprint conflict"}，不落盘

        红线：principal_proof 只是记录，不构成认证；本方法不做任何
        身份核验判断，核验方式待老田与安全席裁定。无自动放行路径。
        """
        return self._decide(item_id, expected_fingerprint, principal_proof,
                            status="approved")

    def reject(self, item_id: str, expected_fingerprint: str,
               principal_proof: Dict[str, Any]) -> Dict[str, Any]:
        """本人否决。与 approve 同构：锁内读—比对指纹—追加 status="rejected" 条目。

        红线同 approve：principal_proof 仅记录，不构成认证，无自动放行。
        """
        return self._decide(item_id, expected_fingerprint, principal_proof,
                            status="rejected")

    def _decide(self, item_id: str, expected_fingerprint: str,
                principal_proof: Dict[str, Any], status: str) -> Dict[str, Any]:
        """approve/reject 共用：锁内读账本、比对最新条目指纹、追加决定条目。"""
        with self._lock:
            entries = self._read_ledger()
            latest: Optional[Dict[str, Any]] = None
            for e in entries:
                if e.get("item_id") == item_id:
                    latest = e  # 追加日志：后写覆盖，取最后一条即最新
            if latest is None:
                return {"ok": False, "err": "item not found"}
            if latest.get("status") != "pending":
                return {"ok": False, "err": "item not pending"}
            if expected_fingerprint != latest.get("fingerprint"):
                # 指纹冲突：明确报错，不落盘，不得静默覆盖
                return {"ok": False, "err": "fingerprint conflict"}
            now = time.time()
            entry: Dict[str, Any] = {
                "item_id": item_id,
                "fingerprint": latest["fingerprint"],
                "status": status,
                "decided_ts": now,
                "ts": now,
                # 红线：principal_proof 原样透传仅作留痕，不构成认证，
                # 本模块不据此做任何放行判断。
                "principal_proof": principal_proof,
            }
            self._append_locked(entry)
            return {"ok": True, "entry": entry}

    # ---- 查询（只读） ----

    def ledger(self, since: Optional[float] = None) -> List[Dict[str, Any]]:
        """审批账本只读查询。since 给定时返回 ts >= since 的条目。

        TODO(领域实现): 账本存储与审计导出。
        """
        entries = self._read_ledger()
        if since is None:
            return entries
        return [e for e in entries if e.get("ts", 0.0) >= since]

    # ---- 账本 I/O（追加写；调用方须已持锁） ----

    def _append_locked(self, entry: Dict[str, Any]) -> None:
        """追加一条 jsonl 记录。账本只追加、不改写、不删除。

        TODO(领域实现): 跨进程文件级锁；当前依赖调用方持有的
        threading.Lock 保证单进程内互斥。
        """
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()

    def _read_ledger(self) -> List[Dict[str, Any]]:
        """读取全部账本条目（空文件/不存在返回空列表）。"""
        if not self.ledger_path.exists():
            return []
        entries: List[Dict[str, Any]] = []
        with self.ledger_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries
