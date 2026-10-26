# -*- coding: utf-8 -*-
"""OSLINK-W4 归口契约实现（隔离树 / 合成数据）。

对应契约：`docs/plans/2026-10-25_OSLINK-W4_司马懿到田丰归口契约v0.1_草案.md` v1.0
（2026-10-26 12:20 老田代 A0 签认）；归属单 `K-QUANT-20261026-OSLEG-IMPL`。

边界（契约 §七，代码不提供绕过口）：
- 不做任何网络 IO，不启动 8795；`channel` 只是标签（inbox / http8795），不建立连接；
- 不真投递：`deliver()` 只写 delivered_* 字段，不发送任何消息；
- 不产生审批/交易副作用：本模块不 import 交易/审批链；`RouteOutbox.trading_permitted()` 恒 False。

状态机（契约 §四）::

    submitted → accepted → consumed(处理中) → answered → delivered
        ↘ rejected(reason 必填)              ↘ expired(无消费者 ≥ T_EXPIRE)

关联规则（契约 §五）：
- 答复必须回引原 `report_id`，否则拒收且 `linked=false`，不写 answered；
- `trace_id` 贯穿提交/受理/消费/答复/投递，缺失或不一致 → 该环 `status=unknown`，
  禁止回填默认值；
- inbox 与 http8795 通道**不共享 msg_id**（通道参与幂等命名空间），但共享
  `report_id`/`trace_id`，`receipt(report_id=...)` 聚合两条通道全部记录。

幂等与拒接重投（§六 N2 + §十 签认）：
- 同通道同体重复提交 → 返回原 msg_id，`duplicate=true`，不产生第二条记录；
- 被拒接的单允许重投，但必须**新 msg_id**、保留原 report_id/trace_id、不复活旧单
  （旧单终态 rejected 不变，新单带 `retry_of` 指向旧单）。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

T_EXPIRE_SEC = 30 * 60  # 契约 §十：30 分钟，自 submitted_at 起算；≥ 阈值即 expired

CHANNEL_INBOX = "inbox"
CHANNEL_HTTP = "http8795"
CHANNELS = (CHANNEL_INBOX, CHANNEL_HTTP)

STATE_SUBMITTED = "submitted"
STATE_ACCEPTED = "accepted"
STATE_CONSUMED = "consumed"
STATE_ANSWERED = "answered"
STATE_DELIVERED = "delivered"
STATE_REJECTED = "rejected"
STATE_EXPIRED = "expired"
STATES = (STATE_SUBMITTED, STATE_ACCEPTED, STATE_CONSUMED, STATE_ANSWERED,
          STATE_DELIVERED, STATE_REJECTED, STATE_EXPIRED)

# alive = 还没到终态（expired/rejected/delivered 不算活着）
_ALIVE_STATES = (STATE_SUBMITTED, STATE_ACCEPTED, STATE_CONSUMED, STATE_ANSWERED)


class ContractViolation(ValueError):
    """契约违例（调用方应按失败关闭处理，不得静默降级）。"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _digest(payload: object) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


class OslegInbox:
    """隔离树上的 W4 归口契约存储；不触网、不真投递、无交易/审批副作用。"""

    def __init__(self, root: str | Path, clock=None) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._clock = clock or _utcnow
        self._records: dict[str, dict] = {}
        self._idem: dict[str, str] = {}

    # ---------------------------------------------------------- 内部
    def _now(self) -> datetime:
        dt = self._clock()
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    def _persist(self, rec: dict) -> None:
        target = self.root / ("%s.json" % rec["msg_id"])
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(target)

    def _fail(self, msg_id: str, status: str, error: str, **extra) -> dict:
        out = {"ok": False, "msg_id": msg_id, "status": status, "error": error}
        out.update(extra)
        return out

    def _get(self, msg_id: str) -> dict | None:
        return self._records.get(str(msg_id or "").strip())

    def _check_trace(self, rec: dict, trace_id: str, msg_id: str):
        """trace_id 贯穿校验：缺失/不一致 → status=unknown（不回填默认值）。"""
        if not str(trace_id or "").strip():
            return self._fail(msg_id, "unknown", "trace_id_missing")
        if str(trace_id) != rec["trace_id"]:
            return self._fail(msg_id, "unknown", "trace_mismatch")
        return None

    @staticmethod
    def _linked(rec: dict, report_id: str = "") -> bool:
        """答复是否回引原报告（report_id 相等且 trace 一致）。"""
        if not rec.get("answer_report_id"):
            return False
        if str(rec["answer_report_id"]) != str(rec.get("report_id") or ""):
            return False
        if str(rec.get("answer_trace_id") or "") != str(rec.get("trace_id") or ""):
            return False
        if report_id and str(rec.get("report_id") or "") != str(report_id):
            return False
        return True

    def _summary(self, rec: dict) -> dict:
        return {
            "msg_id": rec["msg_id"],
            "channel": rec["channel"],
            "kind": rec["kind"],
            "state": rec["state"],
            "status": rec["state"],
            "report_id": rec["report_id"],
            "trace_id": rec["trace_id"],
            "alive": rec["state"] in _ALIVE_STATES,
            "duplicate": int(rec.get("duplicates") or 0) > 0,
            "linked": self._linked(rec),
            "retry_of": rec.get("retry_of") or "",
            "submitted_at": rec["submitted_at"],
            "accepted_by": rec.get("accepted_by") or "",
            "answered_by": rec.get("answered_by") or "",
            "delivered_channel": rec.get("delivered_channel") or "",
        }

    # ---------------------------------------------------------- 生产方
    def submit(self, kind: str, payload: dict, source: str, report_id: str,
               trace_id: str, channel: str = CHANNEL_INBOX) -> dict:
        """提交消息（§三）。trace_id 缺失 = 契约违例，status=unknown。"""
        kind = str(kind or "").strip()
        source = str(source or "").strip()
        report_id = str(report_id or "").strip()
        trace_id = str(trace_id or "").strip()
        channel = str(channel or "").strip() or CHANNEL_INBOX
        if not kind:
            raise ContractViolation("kind 必填")
        if not isinstance(payload, dict):
            raise ContractViolation("payload 必须是对象")
        if not source:
            raise ContractViolation("source 必填")
        if not report_id:
            raise ContractViolation("report_id 必填")
        if channel not in CHANNELS:
            raise ContractViolation("未登记通道：%s" % channel)
        if not trace_id:
            # §六 N5：禁止回填默认值，明确标记契约违例
            return self._fail("", "unknown", "trace_id_missing")
        idem_key = _digest({
            "channel": channel, "kind": kind, "payload": payload,
            "source": source, "report_id": report_id, "trace_id": trace_id,
        })
        hit = self._idem.get(idem_key)
        retry_of = ""
        if hit is not None:
            old = self._records.get(hit)
            if old is not None and old["state"] != STATE_REJECTED:
                old["duplicates"] = int(old.get("duplicates") or 0) + 1
                self._persist(old)
                return {"ok": True, "msg_id": old["msg_id"],
                        "state": old["state"], "status": old["state"],
                        "replayed": True, "duplicate": True,
                        "channel": old["channel"], "retry_of": ""}
            # §十 签认：被拒接的单允许重投，但必须新 msg_id，不复活旧单
            retry_of = hit
        now = self._now()
        msg_id = "w4-" + uuid.uuid4().hex[:12]
        rec = {
            "msg_id": msg_id,
            "kind": kind,
            "payload": payload,
            "source": source,
            "channel": channel,
            "report_id": report_id,
            "trace_id": trace_id,
            "state": STATE_SUBMITTED,
            "submitted_at": _iso(now),
            "submitted_epoch": now.timestamp(),
            "duplicates": 0,
            "retry_of": retry_of,
        }
        self._records[msg_id] = rec
        self._idem[idem_key] = msg_id
        self._persist(rec)
        return {"ok": True, "msg_id": msg_id, "state": STATE_SUBMITTED,
                "status": STATE_SUBMITTED, "replayed": False,
                "duplicate": False, "channel": channel, "retry_of": retry_of}

    # ---------------------------------------------------------- 归口方 A0
    def accept(self, msg_id: str, accepted_by: str, trace_id: str) -> dict:
        """归口受理：submitted → accepted（写 accepted_by）。"""
        rec = self._get(msg_id)
        if rec is None:
            return self._fail(msg_id, "not_found", "msg_not_found")
        err = self._check_trace(rec, trace_id, rec["msg_id"])
        if err is not None:
            return err
        if rec["state"] != STATE_SUBMITTED:
            return self._fail(rec["msg_id"], "bad_state",
                              "state=%s" % rec["state"])
        if not str(accepted_by or "").strip():
            return self._fail(rec["msg_id"], "bad_actor", "accepted_by_required")
        rec["accepted_by"] = str(accepted_by).strip()
        rec["accepted_at"] = _iso(self._now())
        rec["state"] = STATE_ACCEPTED
        self._persist(rec)
        return {"ok": True, "msg_id": rec["msg_id"], "state": STATE_ACCEPTED,
                "status": STATE_ACCEPTED, "accepted_by": rec["accepted_by"]}

    def consume(self, msg_id: str, consumed_by: str, trace_id: str) -> dict:
        """消费受理：accepted → consumed（处理中）。"""
        rec = self._get(msg_id)
        if rec is None:
            return self._fail(msg_id, "not_found", "msg_not_found")
        err = self._check_trace(rec, trace_id, rec["msg_id"])
        if err is not None:
            return err
        if rec["state"] != STATE_ACCEPTED:
            return self._fail(rec["msg_id"], "bad_state",
                              "state=%s" % rec["state"])
        if not str(consumed_by or "").strip():
            return self._fail(rec["msg_id"], "bad_actor", "consumed_by_required")
        rec["consumed_by"] = str(consumed_by).strip()
        rec["consumed_at"] = _iso(self._now())
        rec["state"] = STATE_CONSUMED
        self._persist(rec)
        return {"ok": True, "msg_id": rec["msg_id"], "state": STATE_CONSUMED,
                "status": STATE_CONSUMED, "consumed_by": rec["consumed_by"]}

    def answer(self, msg_id: str, answered_by: str, text: str,
               report_id: str, trace_id: str) -> dict:
        """答复：consumed → answered；必须回引原 report_id（§五-1）。"""
        rec = self._get(msg_id)
        if rec is None:
            return self._fail(msg_id, "not_found", "msg_not_found")
        err = self._check_trace(rec, trace_id, rec["msg_id"])
        if err is not None:
            return err
        if rec["state"] != STATE_CONSUMED:
            return self._fail(rec["msg_id"], "bad_state",
                              "state=%s" % rec["state"])
        if not str(report_id or "").strip():
            # §六 N4：答复缺 report_id → 拒收，linked=false，不写 answered
            return self._fail(rec["msg_id"], STATE_REJECTED,
                              "report_id_missing", linked=False)
        if str(report_id) != rec["report_id"]:
            return self._fail(rec["msg_id"], STATE_REJECTED,
                              "report_id_mismatch", linked=False)
        text = str(text or "").strip()
        if not text:
            return self._fail(rec["msg_id"], "bad_text", "answer_text_required")
        if not str(answered_by or "").strip():
            return self._fail(rec["msg_id"], "bad_actor", "answered_by_required")
        rec["answer_text"] = text
        rec["answered_by"] = str(answered_by).strip()
        rec["answered_at"] = _iso(self._now())
        rec["answer_report_id"] = rec["report_id"]
        rec["answer_trace_id"] = rec["trace_id"]
        rec["state"] = STATE_ANSWERED
        self._persist(rec)
        return {"ok": True, "msg_id": rec["msg_id"], "state": STATE_ANSWERED,
                "status": STATE_ANSWERED, "linked": True,
                "answer_report_id": rec["answer_report_id"]}

    def deliver(self, msg_id: str, channel: str, delivered_ref: str,
                trace_id: str) -> dict:
        """投递落账：answered → delivered；channel 仅标签，不发送。"""
        rec = self._get(msg_id)
        if rec is None:
            return self._fail(msg_id, "not_found", "msg_not_found")
        err = self._check_trace(rec, trace_id, rec["msg_id"])
        if err is not None:
            return err
        if rec["state"] != STATE_ANSWERED:
            return self._fail(rec["msg_id"], "bad_state",
                              "state=%s" % rec["state"])
        channel = str(channel or "").strip()
        if not channel:
            return self._fail(rec["msg_id"], "bad_channel",
                              "deliver_channel_required")
        rec["delivered_channel"] = channel
        rec["delivered_ref"] = str(delivered_ref or "").strip()
        rec["delivered_at"] = _iso(self._now())
        rec["state"] = STATE_DELIVERED
        self._persist(rec)
        return {"ok": True, "msg_id": rec["msg_id"], "state": STATE_DELIVERED,
                "status": STATE_DELIVERED,
                "delivered_channel": channel}

    def reject(self, msg_id: str, reason: str, rejected_by: str,
               trace_id: str = "") -> dict:
        """归口拒接：reason 必填；重复拒接幂等（只回放，不改写）。"""
        rec = self._get(msg_id)
        if rec is None:
            return self._fail(msg_id, "not_found", "msg_not_found")
        reason = str(reason or "").strip()
        if rec["state"] == STATE_REJECTED:
            return {"ok": True, "msg_id": rec["msg_id"],
                    "state": STATE_REJECTED, "status": STATE_REJECTED,
                    "replayed": True, "reason": rec.get("rejected_reason") or ""}
        if not reason:
            return self._fail(rec["msg_id"], "reason_required",
                              "reject_reason_required")
        if str(trace_id or "").strip():
            err = self._check_trace(rec, trace_id, rec["msg_id"])
            if err is not None:
                return err
        if rec["state"] not in (STATE_SUBMITTED, STATE_ACCEPTED,
                                STATE_CONSUMED, STATE_ANSWERED):
            return self._fail(rec["msg_id"], "bad_state",
                              "state=%s" % rec["state"])
        rec["rejected_reason"] = reason
        rec["rejected_by"] = str(rejected_by or "a0").strip()
        rec["rejected_at"] = _iso(self._now())
        rec["state"] = STATE_REJECTED
        self._persist(rec)
        return {"ok": True, "msg_id": rec["msg_id"], "state": STATE_REJECTED,
                "status": STATE_REJECTED, "replayed": False,
                "reason": reason}

    # ---------------------------------------------------------- 系统
    def expire(self, now: datetime | None = None) -> dict:
        """无消费者超时：submitted 且 now - submitted_at ≥ T_EXPIRE → expired。

        签认口径：已受理（accepted/consumed/answered/delivered）不误过期。
        """
        current = now if now is not None else self._now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        expired = []
        for rec in self._records.values():
            if rec["state"] != STATE_SUBMITTED:
                continue
            if current.timestamp() - float(rec["submitted_epoch"]) >= T_EXPIRE_SEC:
                rec["state"] = STATE_EXPIRED
                rec["expired_at"] = _iso(current)
                self._persist(rec)
                expired.append(rec["msg_id"])
        return {"ok": True, "expired": expired, "t_expire_sec": T_EXPIRE_SEC}

    # ---------------------------------------------------------- 回执
    def receipt(self, msg_id: str = "", report_id: str = "") -> dict:
        """回执：单条 msg_id 或按 report_id 聚合两条通道（§五-3）。"""
        msg_id = str(msg_id or "").strip()
        report_id = str(report_id or "").strip()
        if msg_id:
            rec = self._get(msg_id)
            if rec is None:
                return {"ok": False, "status": "not_found",
                        "error": "msg_not_found", "msg_id": msg_id}
            out = self._summary(rec)
            out["ok"] = True
            out["answer_text"] = rec.get("answer_text") or ""
            return out
        if report_id:
            rows = [rec for rec in self._records.values()
                    if rec["report_id"] == report_id]
            traces = sorted({rec["trace_id"] for rec in rows})
            answered = [rec for rec in rows
                        if rec["state"] in (STATE_ANSWERED, STATE_DELIVERED)]
            return {
                "ok": True,
                "report_id": report_id,
                "msg_ids": [rec["msg_id"] for rec in rows],
                "channels": sorted({rec["channel"] for rec in rows}),
                "records": [self._summary(rec) for rec in rows],
                "total": len(rows),
                "trace_ids": traces,
                "trace_consistent": bool(traces) and len(traces) == 1,
                "linked": bool(answered) and all(self._linked(rec) for rec in answered),
                "alive": any(rec["state"] in _ALIVE_STATES for rec in rows),
                "duplicate": any(int(rec.get("duplicates") or 0) > 0
                                 for rec in rows),
            }
        raise ContractViolation("receipt 需要 msg_id 或 report_id")
