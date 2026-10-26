# -*- coding: utf-8 -*-
"""OSLINK-W4 通道①：同进程 inbox 适配器（离线 / 隔离根 / 不接生产）。

对应契约：`docs/plans/2026-10-25_OSLINK-W4_司马懿到田丰归口契约v0.1_草案.md` v1.0
（2026-10-26 12:20 老田代 A0 签认）；归属单 `K-QUANT-20261026-OSLEG-CH1`。

本模块把契约层 `OslegInbox`（w4-* 归口账）与军团同进程入箱门面
`agent_platform.gateway.inbox_gateway`（lg-* 入箱账）成对落账：两侧状态机各自
不变，本模块只做「入箱 + 映射 + 证据门槛 + 状态回写」，不实现第二套语义。

- 命名空间：gateway 生成 `lg-*`，契约层生成 `w4-*`，两账不共享 msg_id；
  `report_id`/`trace_id` 由生产方给出、经映射账共享；通道标签恒为 `inbox`。
- 业务体：payload 原样交给 gateway，不注入、不改写、不裁剪；两账关联关系
  单独落隔离根映射账，可审计、可按幂等键重放。
- 证据门槛：完全复用 gateway 原实现（不放松、不绕过、不改 gateway 源码）——
  `consume` 必须带 `answered_by` + `answer_ref`；`deliver` 必须带 `channel` +
  `delivered_ref`，且此前必须有答复戳记。本模块只做只读预检与失败关闭，
  缺证据一律 `{"ok": False, ...}`，不推进任何一侧状态。
- `reject`/`expire` 是契约层动作，gateway 无对应语义：只推进契约层账，
  双账视图用 `divergences` 明示差异，不伪造 gateway 戳记。
- 隔离红线：gateway store 恒定绑定隔离根下的 `gateway_store/`（或显式传入的
  隔离目录），拒绝指向真实 `agent_platform/bridge_inbox`；本模块无网络 IO，
  不启动服务、不真投递、不产生审批/交易副作用。

隔离根目录结构（全部在构造参数 `<osleg_root>` 之下）::

    <osleg_root>/osleg/                       契约层账（w4-*.json）
    <osleg_root>/gateway_store/               同进程入箱账（inbox/、processed/）
    <osleg_root>/inbox_channel/links.json     双账映射（原子写）
    <osleg_root>/inbox_channel/idem.json      显式幂等键 -> gateway_msg_id（原子写）

进程内 API 均返回 dict；失败一律 `{"ok": False, ...}` 失败关闭（不抛业务异常）。
`msg_id` 参数可传 `lg-*` 或 `w4-*`，模块按映射账解析。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from quant_backtest.agent_bridge import osleg_contract as oc
from quant_backtest.agent_bridge.osleg_contract import OslegInbox

__all__ = [
    "CHANNEL",
    "KIND_TO_GATEWAY",
    "ChannelViolation",
    "InboxChannelBridge",
]

CHANNEL = oc.CHANNEL_INBOX

# gateway 投递层 kind 词表（inbox_gateway.INBOX_KINDS）只认 task_request/question/
# feedback 三种；W4 业务 kind 必须显式登记才能入箱。未登记的 kind 一律失败关闭，
# 不猜测、不降级（映射只影响投递层 kind，契约层仍保留原始业务 kind）。
KIND_TO_GATEWAY = {
    "stock_consult_result": "question",
    "risk_verdict": "task_request",
    "task_request": "task_request",
    "question": "question",
    "feedback": "feedback",
}
GATEWAY_KINDS = ("task_request", "question", "feedback")

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_GATEWAY = None


class ChannelViolation(ValueError):
    """通道适配违例（隔离红线 / 映射账损坏；调用方按失败关闭处理）。"""


# ---------------------------------------------------------------- 环境定位
def _platform_dir() -> Path:
    """定位仓库内 agent_platform 目录（from quant_backtest/agent_bridge/...）。"""
    return Path(__file__).resolve().parents[2] / "agent_platform"


def _real_bridge_inbox() -> Path:
    """真实 bridge_inbox（生产入箱目录，本模块任何情况下不得写入）。"""
    return _platform_dir() / "bridge_inbox"


def _is_within(child, parent) -> bool:
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def _import_gateway():
    """延迟 import 同进程 inbox 门面；只接受隔离树内这一份，拒绝同名其它包。"""
    global _GATEWAY
    if _GATEWAY is not None:
        return _GATEWAY
    platform_dir = _platform_dir()
    target = platform_dir / "gateway" / "inbox_gateway.py"
    if not target.is_file():
        raise ChannelViolation("未找到同进程 inbox 门面：%s" % target)
    if str(platform_dir) not in sys.path:
        # gateway 包内部用绝对名导入 gateway.envelope，故需平台根在 sys.path。
        sys.path.insert(0, str(platform_dir))
    try:
        from gateway import inbox_gateway
    except Exception as exc:
        raise ChannelViolation("import gateway.inbox_gateway 失败：%s" % exc) from exc
    module_file = Path(str(getattr(inbox_gateway, "__file__", "") or "")).resolve()
    if platform_dir.resolve() not in module_file.parents:
        raise ChannelViolation(
            "gateway.inbox_gateway 不在预期隔离树：%s" % module_file)
    _GATEWAY = inbox_gateway
    return inbox_gateway


# ---------------------------------------------------------------- 小工具
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _payload_digest(payload) -> str:
    try:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ChannelViolation("payload 不可 JSON 序列化：%s" % exc) from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _gateway_error(env) -> str:
    error = env.get("error") or {}
    return str(error.get("code") or "gateway_failed")


class InboxChannelBridge:
    """W4 契约层 + 同进程 inbox 的双账适配器（离线、隔离根内自洽）。

    构造即建立隔离根目录、延迟导入 gateway 并把 gateway store 绑定到本隔离根，
    绝不使用 gateway 全局默认 store（真实 bridge_inbox）。
    """

    def __init__(self, osleg_root, gateway_store_root=None, clock=None) -> None:
        self.root = Path(osleg_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.osleg_store_root = self.root / "osleg"
        self.osleg = OslegInbox(self.osleg_store_root, clock=clock)
        self._meta_dir = self.root / "inbox_channel"
        self._meta_dir.mkdir(parents=True, exist_ok=True)
        self._links_path = self._meta_dir / "links.json"
        self._idem_path = self._meta_dir / "idem.json"
        self._gateway = _import_gateway()

        store = Path(gateway_store_root) if gateway_store_root is not None \
            else self.root / "gateway_store"
        store = store.resolve()
        if store.exists() and not store.is_dir():
            raise ChannelViolation("gateway store 必须是目录：%s" % store)
        self.gateway_store_root = store
        self._bind_gateway_store()

    # ---------------------------------------------------------- gateway 绑定
    def _bind_gateway_store(self) -> None:
        """每个动作前强制绑定隔离 store，杜绝误落真实 bridge_inbox。"""
        store = Path(self.gateway_store_root).resolve()
        real = _real_bridge_inbox().resolve()
        if _is_within(store, real) or _is_within(real, store):
            raise ChannelViolation("gateway store 不得指向真实 bridge_inbox：%s" % store)
        self._gateway.set_store(store)

    # ---------------------------------------------------------- 映射账
    @staticmethod
    def _read_json_map(path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ChannelViolation("映射账不可读：%s（%s）" % (path.name, exc)) from exc
        if not isinstance(data, dict):
            raise ChannelViolation("映射账结构损坏：%s" % path.name)
        return data

    @staticmethod
    def _write_json(path: Path, data: dict) -> None:
        tmp = path.with_name(".%s.tmp-%s" % (path.name, uuid.uuid4().hex[:8]))
        try:
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1),
                encoding="utf-8")
            os.replace(tmp, path)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

    def _ledger(self, path: Path):
        """读映射账：返回 (dict, None) 或 (None, 失败 dict)。"""
        try:
            return self._read_json_map(path), None
        except ChannelViolation as exc:
            return None, self._fail("ledger_error", "ledger_unreadable",
                                    ledger=path.name, detail=str(exc))

    @staticmethod
    def _find_by_w4(links: dict, w4_msg_id: str):
        for link in links.values():
            if isinstance(link, dict) and link.get("w4_msg_id") == w4_msg_id:
                return link
        return None

    def _resolve(self, msg_id: str):
        """解析 lg-*/w4-* -> 映射条目；返回 (entry, None) 或 (None, 失败 dict)。"""
        raw = str(msg_id or "").strip()
        if not raw:
            return None, self._fail("bad_id", "msg_id_required")
        links, fail = self._ledger(self._links_path)
        if fail is not None:
            return None, fail
        entry = None
        if raw.startswith("lg-"):
            entry = links.get(raw)
        elif raw.startswith("w4-"):
            entry = self._find_by_w4(links, raw)
        if entry is None:
            entry = links.get(raw) or self._find_by_w4(links, raw)
        if not isinstance(entry, dict):
            return None, self._fail("not_found", "msg_not_in_channel_ledger",
                                    msg_id=raw)
        return entry, None

    @staticmethod
    def _same_submission(entry: dict, kind: str, digest: str, source: str,
                         report_id: str, trace_id: str) -> bool:
        """同幂等键回放一致性：业务同源才回放，同键异体一律冲突。"""
        return (
            str(entry.get("kind") or "") == kind
            and str(entry.get("source") or "") == source
            and str(entry.get("report_id") or "") == report_id
            and str(entry.get("trace_id") or "") == trace_id
            and str(entry.get("payload_digest") or "") == digest
        )

    def _find_business_duplicate(self, links: dict, kind: str, digest: str,
                                 source: str, report_id: str, trace_id: str):
        """业务同体重放查找（契约层摘要幂等口径）：原单非 rejected 才算重复。"""
        for link in links.values():
            if not isinstance(link, dict):
                continue
            if not self._same_submission(link, kind, digest, source, report_id,
                                         trace_id):
                continue
            rcpt = self.osleg.receipt(msg_id=str(link.get("w4_msg_id") or ""))
            if rcpt.get("ok") and rcpt.get("state") != oc.STATE_REJECTED:
                return link
        return None

    def _make_entry(self, gateway_msg_id: str, w4_msg_id: str, kind: str,
                    gateway_kind: str, digest: str, source: str, report_id: str,
                    trace_id: str, idempotency_key: str) -> dict:
        return {
            "gateway_msg_id": gateway_msg_id,
            "w4_msg_id": w4_msg_id,
            "kind": kind,
            "gateway_kind": gateway_kind,
            "source": source,
            "report_id": report_id,
            "trace_id": trace_id,
            "channel": CHANNEL,
            "payload_digest": digest,
            "idempotency_key": idempotency_key,
            "linked_at": _now_iso(),
        }

    def _save_link(self, entry: dict):
        links, fail = self._ledger(self._links_path)
        if fail is not None:
            return fail
        links[entry["gateway_msg_id"]] = entry
        try:
            self._write_json(self._links_path, links)
        except OSError as exc:
            return self._fail("ledger_error", "mapping_write_failed", detail=str(exc))
        return None

    def _save_idem(self, idempotency_key: str, gateway_msg_id: str):
        if not idempotency_key:
            return None
        idem, fail = self._ledger(self._idem_path)
        if fail is not None:
            return fail
        idem[idempotency_key] = gateway_msg_id
        try:
            self._write_json(self._idem_path, idem)
        except OSError as exc:
            return self._fail("ledger_error", "idempotency_write_failed",
                              detail=str(exc))
        return None

    # ---------------------------------------------------------- 结果构造
    @staticmethod
    def _fail(status: str, error: str, **extra) -> dict:
        out = {"ok": False, "status": status, "error": error, "channel": CHANNEL}
        out.update(extra)
        return out

    def _ok_result(self, entry: dict, rcpt=None, gateway=None, **extra) -> dict:
        out = {
            "ok": True,
            "channel": CHANNEL,
            "gateway_msg_id": str(entry.get("gateway_msg_id") or ""),
            "w4_msg_id": str(entry.get("w4_msg_id") or ""),
            "msg_id": str(entry.get("w4_msg_id") or ""),
            "report_id": str(entry.get("report_id") or ""),
            "trace_id": str(entry.get("trace_id") or ""),
        }
        if isinstance(rcpt, dict) and rcpt.get("ok"):
            state = rcpt.get("state") or "unknown"
            out.update({
                "state": state,
                "status": rcpt.get("status") or state,
                "linked": bool(rcpt.get("linked")),
                "alive": bool(rcpt.get("alive")),
                "duplicate": bool(rcpt.get("duplicate")),
            })
        if gateway is not None:
            out["gateway"] = gateway
        out.update(extra)
        return out

    def _gateway_view(self, gateway_msg_id: str) -> dict:
        """gateway 账只读视图（调用前必须已绑定隔离 store）。"""
        env = self._gateway.receipt(gateway_msg_id)
        if not env.get("ok"):
            return {"ok": False, "msg_id": gateway_msg_id,
                    "error": _gateway_error(env)}
        data = env.get("data") or {}
        record = data.get("record") or {}
        return {
            "ok": True,
            "msg_id": str(data.get("id") or gateway_msg_id),
            "state": str(data.get("state") or "not_found"),
            "answered": bool(data.get("answered")),
            "delivered": bool(data.get("delivered")),
            "kind": str(record.get("kind") or ""),
            "source": str(record.get("source") or ""),
            "payload": record.get("payload") or {},
            "answered_by": str(record.get("answered_by") or ""),
            "answer_ref": str(record.get("answer_ref") or ""),
            "channel": str(record.get("channel") or ""),
            "delivered_ref": str(record.get("delivered_ref") or ""),
        }

    def _precheck_w4(self, entry: dict, expected_states, trace_id):
        """只读预检契约层状态/trace，减少半写；不替代任何一侧的写入门槛。"""
        rcpt = self.osleg.receipt(msg_id=entry["w4_msg_id"])
        if not rcpt.get("ok"):
            return self._fail("not_found", "w4_msg_not_found",
                              msg_id=str(entry.get("w4_msg_id") or ""))
        trace = str(trace_id or "").strip()
        if not trace:
            return self._fail("unknown", "trace_id_missing",
                              msg_id=entry["w4_msg_id"])
        if trace != str(rcpt.get("trace_id") or ""):
            return self._fail("unknown", "trace_mismatch",
                              msg_id=entry["w4_msg_id"])
        if expected_states and rcpt.get("state") not in expected_states:
            return self._fail("bad_state", "state=%s" % rcpt.get("state"),
                              msg_id=entry["w4_msg_id"])
        return None

    # ---------------------------------------------------------- 生产方
    def submit(self, kind: str, payload: dict, source: str, report_id: str,
               trace_id: str, idempotency_key: str = "") -> dict:
        """双账入箱：gateway 拿 lg-*、契约层拿 w4-*，共享 report_id/trace_id。

        同幂等键（或同体）重放返回同一对 msg_id，不新增映射；被拒接旧单的重投
        按契约 §十 两侧都必须是新单，gateway 内容幂等回放旧 lg-* 时用派生幂等键
        补一条新入箱记录。
        """
        kind = str(kind or "").strip()
        source = str(source or "").strip()
        report_id = str(report_id or "").strip()
        trace_id = str(trace_id or "").strip()
        idem = str(idempotency_key or "").strip()

        if not kind:
            return self._fail("bad_kind", "kind_required")
        if not isinstance(payload, dict):
            return self._fail("bad_payload", "payload_must_be_object")
        if not source:
            return self._fail("bad_source", "source_required")
        if not report_id:
            return self._fail("bad_report_id", "report_id_required")
        if not trace_id:
            # 契约 §六 N5：trace_id 缺失即契约违例，禁止回填默认值，不落任何账。
            return self._fail("unknown", "trace_id_missing")
        if idem and not _ID_RE.match(idem):
            return self._fail("bad_idempotency_key",
                              "idempotency_key_must_match_regex")
        try:
            digest = _payload_digest(payload)
        except ChannelViolation:
            return self._fail("bad_payload", "payload_not_json_serializable")

        gateway_kind = KIND_TO_GATEWAY.get(kind)
        if gateway_kind is None and kind in GATEWAY_KINDS:
            gateway_kind = kind
        if gateway_kind is None:
            return self._fail("kind_not_bridged",
                              "kind_not_in_gateway_vocabulary", kind=kind)

        # 显式幂等键预检：同键异体在落任何一侧账之前拒绝，防偷换报告。
        idem_map, fail = self._ledger(self._idem_path)
        if fail is not None:
            return fail
        if idem:
            prev_gw = idem_map.get(idem)
            if prev_gw:
                links, fail = self._ledger(self._links_path)
                if fail is not None:
                    return fail
                prev = links.get(prev_gw)
                if not isinstance(prev, dict):
                    return self._fail("ledger_error", "idempotency_mapping_missing",
                                      idempotency_key=idem)
                if not self._same_submission(prev, kind, digest, source,
                                             report_id, trace_id):
                    return self._fail("idempotency_conflict",
                                      "idempotency_key_used_for_different_content",
                                      idempotency_key=idem)

        # 业务同体重放（契约层摘要幂等口径）：回放同一对 msg_id，不新进入箱、
        # 不新增映射；换了显式幂等键也不得为同一业务提交制造第二对账。
        links, fail = self._ledger(self._links_path)
        if fail is not None:
            return fail
        duplicate = self._find_business_duplicate(links, kind, digest, source,
                                                  report_id, trace_id)
        if duplicate is not None:
            w4_env = self.osleg.submit(kind, payload, source, report_id, trace_id,
                                       channel=CHANNEL)
            if not w4_env.get("ok"):
                return self._fail(str(w4_env.get("status") or "w4_error"),
                                  str(w4_env.get("error") or "osleg_submit_failed"),
                                  w4=w4_env)
            if str(w4_env["msg_id"]) != str(duplicate.get("w4_msg_id") or ""):
                return self._fail("ledger_error", "business_duplicate_diverged",
                                  msg_id=str(duplicate.get("w4_msg_id") or ""))
            self._bind_gateway_store()
            return self._ok_result(
                duplicate,
                self.osleg.receipt(msg_id=str(duplicate.get("w4_msg_id") or "")),
                gateway=self._gateway_view(
                    str(duplicate.get("gateway_msg_id") or "")),
                replayed=True,
                retry_of="",
                kind=kind,
                gateway_kind=gateway_kind,
                source=source,
            )

        # 1) gateway 入箱（真实幂等/门槛由 inbox_gateway 负责）
        self._bind_gateway_store()
        gw_env = self._gateway.submit(gateway_kind, payload, source=source,
                                      idempotency_key=idem or None)
        if not gw_env.get("ok"):
            return self._fail("gateway_error", _gateway_error(gw_env),
                              gateway=gw_env)
        gw_data = gw_env.get("data") or {}
        gateway_msg_id = str(gw_data.get("id") or "")
        if not gateway_msg_id:
            return self._fail("gateway_error", "gateway_msg_id_missing",
                              gateway=gw_env)

        # 2) 契约层归口单（w4-*），两账共享 report_id/trace_id
        w4_env = self.osleg.submit(kind, payload, source, report_id, trace_id,
                                   channel=CHANNEL)
        if not w4_env.get("ok"):
            # 前置校验已覆盖契约层全部拒绝理由；走到这里按半写失败上报，不掩盖。
            return self._fail(str(w4_env.get("status") or "w4_error"),
                              str(w4_env.get("error") or "osleg_submit_failed"),
                              gateway_msg_id=gateway_msg_id, partial=True,
                              w4=w4_env)
        w4_msg_id = str(w4_env["msg_id"])

        links, fail = self._ledger(self._links_path)
        if fail is not None:
            return self._fail("ledger_error", "ledger_unreadable",
                              gateway_msg_id=gateway_msg_id,
                              w4_msg_id=w4_msg_id, partial=True)
        entry = links.get(gateway_msg_id)
        created = False
        if isinstance(entry, dict) and entry.get("w4_msg_id") != w4_msg_id:
            # 旧 lg-* 已绑旧 w4-*（旧单被拒接后的重投，或同体不同报告）：
            # 两侧都必须是新单，故用派生幂等键补一条新入箱记录。
            retry_key = "derived-" + w4_msg_id
            self._bind_gateway_store()
            gw_retry = self._gateway.submit(gateway_kind, payload, source=source,
                                            idempotency_key=retry_key)
            if not gw_retry.get("ok"):
                return self._fail("gateway_error", _gateway_error(gw_retry),
                                  w4_msg_id=w4_msg_id,
                                  existing_gateway_msg_id=gateway_msg_id,
                                  partial=True, gateway=gw_retry)
            gateway_msg_id = str((gw_retry.get("data") or {}).get("id") or "")
            if not gateway_msg_id or links.get(gateway_msg_id) is not None:
                return self._fail("mapping_conflict", "derived_gateway_id_conflict",
                                  w4_msg_id=w4_msg_id, partial=True)
            entry = None
        if entry is None:
            entry = self._make_entry(gateway_msg_id, w4_msg_id, kind, gateway_kind,
                                     digest, source, report_id, trace_id, idem)
            fail = self._save_link(entry)
            if fail is not None:
                fail.update({"gateway_msg_id": gateway_msg_id,
                             "w4_msg_id": w4_msg_id, "partial": True})
                return fail
            created = True
        idem_saved = self._save_idem(idem, gateway_msg_id)
        if idem_saved is not None:
            idem_saved.update({"gateway_msg_id": gateway_msg_id,
                               "w4_msg_id": w4_msg_id, "partial": True})
            return idem_saved

        replayed = not created
        return self._ok_result(
            entry, self.osleg.receipt(msg_id=w4_msg_id),
            gateway=self._gateway_view(gateway_msg_id),
            replayed=replayed,
            retry_of=str(w4_env.get("retry_of") or ""),
            kind=kind,
            gateway_kind=gateway_kind,
            source=source,
        )

    # ---------------------------------------------------------- 归口方 A0
    def accept(self, msg_id: str, accepted_by: str, trace_id: str) -> dict:
        """归口受理 submitted -> accepted（gateway 无受理语义，只写契约层）。"""
        entry, fail = self._resolve(msg_id)
        if fail is not None:
            return fail
        pre = self._precheck_w4(entry, (oc.STATE_SUBMITTED,), trace_id)
        if pre is not None:
            return pre
        env = self.osleg.accept(entry["w4_msg_id"], accepted_by, trace_id)
        if not env.get("ok"):
            return self._fail(str(env.get("status") or "w4_error"),
                              str(env.get("error") or "w4_accept_failed"),
                              msg_id=entry["w4_msg_id"])
        self._bind_gateway_store()
        return self._ok_result(entry, self.osleg.receipt(msg_id=entry["w4_msg_id"]),
                               gateway=self._gateway_view(entry["gateway_msg_id"]))

    def consume(self, msg_id: str, consumed_by: str, answered_by: str,
                answer_ref: str, trace_id: str) -> dict:
        """消费受理（处理中）：先经 gateway 落 answered_by/answer_ref 证据，
        再把契约层 submitted/accepted -> consumed。

        gateway 证据门槛（answered_by + answer_ref 必填）不放松；缺任一即失败关闭，
        两账均不推进。
        """
        answered_by = str(answered_by or "").strip()
        answer_ref = str(answer_ref or "").strip()
        if not answered_by or not answer_ref:
            return self._fail("bad_answer_evidence",
                              "answered_by_and_answer_ref_required")
        if not str(consumed_by or "").strip():
            return self._fail("bad_actor", "consumed_by_required")
        entry, fail = self._resolve(msg_id)
        if fail is not None:
            return fail
        pre = self._precheck_w4(entry, (oc.STATE_ACCEPTED,), trace_id)
        if pre is not None:
            return pre

        self._bind_gateway_store()
        gw_env = self._gateway.consume(entry["gateway_msg_id"],
                                       answered_by=answered_by,
                                       answer_ref=answer_ref)
        if not gw_env.get("ok"):
            return self._fail("gateway_error", _gateway_error(gw_env),
                              msg_id=entry["w4_msg_id"], gateway=gw_env)
        if (gw_env.get("data") or {}).get("state") == "not_found":
            return self._fail("not_found", "gateway_msg_not_found",
                              msg_id=entry["w4_msg_id"])
        w4_env = self.osleg.consume(entry["w4_msg_id"], consumed_by, trace_id)
        if not w4_env.get("ok"):
            return self._fail(str(w4_env.get("status") or "w4_error"),
                              str(w4_env.get("error") or "w4_consume_failed"),
                              msg_id=entry["w4_msg_id"], partial=True,
                              gateway=self._gateway_view(entry["gateway_msg_id"]))
        return self._ok_result(entry, self.osleg.receipt(msg_id=entry["w4_msg_id"]),
                               gateway=self._gateway_view(entry["gateway_msg_id"]))

    def answer(self, msg_id: str, answered_by: str, text: str,
               report_id: str, trace_id: str) -> dict:
        """答复：契约层 consumed -> answered（必须回引原 report_id）。

        gateway 侧必须先有 answered 戳记（answered_by/answer_ref 由 consume 落账）；
        缺证据或答复主体与 gateway 证据不一致一律失败关闭，不写 answered。
        """
        answered_by = str(answered_by or "").strip()
        report_id = str(report_id or "").strip()
        if not answered_by:
            return self._fail("bad_actor", "answered_by_required")
        if not report_id:
            # 契约 §六 N4：答复缺 report_id -> 拒收，linked=false，不写 answered。
            return self._fail(oc.STATE_REJECTED, "report_id_missing", linked=False)
        if not isinstance(text, str) or not text.strip():
            return self._fail("bad_text", "answer_text_required")
        entry, fail = self._resolve(msg_id)
        if fail is not None:
            return fail
        pre = self._precheck_w4(entry, (oc.STATE_CONSUMED,), trace_id)
        if pre is not None:
            return pre
        if report_id != str(entry.get("report_id") or ""):
            return self._fail(oc.STATE_REJECTED, "report_id_mismatch",
                              linked=False, msg_id=entry["w4_msg_id"])

        self._bind_gateway_store()
        gw_view = self._gateway_view(entry["gateway_msg_id"])
        if not gw_view.get("ok"):
            return self._fail("gateway_error", str(gw_view.get("error") or
                                                   "gateway_receipt_failed"),
                              msg_id=entry["w4_msg_id"], gateway=gw_view)
        if not gw_view.get("answered"):
            return self._fail("gateway_evidence_missing",
                              "gateway_answered_evidence_required",
                              msg_id=entry["w4_msg_id"], gateway=gw_view)
        if str(gw_view.get("answered_by") or "") != answered_by:
            return self._fail("actor_mismatch",
                              "answered_by_mismatch_with_gateway_evidence",
                              msg_id=entry["w4_msg_id"], gateway=gw_view)

        env = self.osleg.answer(entry["w4_msg_id"], answered_by, text,
                                report_id, trace_id)
        if not env.get("ok"):
            return self._fail(str(env.get("status") or "w4_error"),
                              str(env.get("error") or "w4_answer_failed"),
                              msg_id=entry["w4_msg_id"])
        return self._ok_result(entry, self.osleg.receipt(msg_id=entry["w4_msg_id"]),
                               gateway=gw_view)

    def deliver(self, msg_id: str, channel: str, delivered_ref: str,
                trace_id: str) -> dict:
        """投递落账：先经 gateway.deliver 落 channel/delivered_ref 证据
        （gateway 门槛：必须先 answered），再把契约层 answered -> delivered。

        channel 仅是标签，不发送任何消息。
        """
        channel = str(channel or "").strip()
        delivered_ref = str(delivered_ref or "").strip()
        if not channel or not delivered_ref:
            return self._fail("bad_delivery_evidence",
                              "channel_and_delivered_ref_required")
        entry, fail = self._resolve(msg_id)
        if fail is not None:
            return fail
        pre = self._precheck_w4(entry, (oc.STATE_ANSWERED,), trace_id)
        if pre is not None:
            return pre

        self._bind_gateway_store()
        gw_env = self._gateway.deliver(entry["gateway_msg_id"], channel=channel,
                                       delivered_ref=delivered_ref)
        if not gw_env.get("ok"):
            # 含 not_answered / bad_delivery_evidence：gateway 真实门槛原样透出。
            return self._fail("gateway_error", _gateway_error(gw_env),
                              msg_id=entry["w4_msg_id"], gateway=gw_env)
        if (gw_env.get("data") or {}).get("state") == "not_found":
            return self._fail("not_found", "gateway_msg_not_found",
                              msg_id=entry["w4_msg_id"])
        w4_env = self.osleg.deliver(entry["w4_msg_id"], channel, delivered_ref,
                                    trace_id)
        if not w4_env.get("ok"):
            return self._fail(str(w4_env.get("status") or "w4_error"),
                              str(w4_env.get("error") or "w4_deliver_failed"),
                              msg_id=entry["w4_msg_id"], partial=True,
                              gateway=self._gateway_view(entry["gateway_msg_id"]))
        return self._ok_result(entry, self.osleg.receipt(msg_id=entry["w4_msg_id"]),
                               gateway=self._gateway_view(entry["gateway_msg_id"]))

    def reject(self, msg_id: str, reason: str, rejected_by: str,
               trace_id: str = "") -> dict:
        """归口拒接（契约层动作；gateway 账无拒接语义，保持 inbox）。"""
        entry, fail = self._resolve(msg_id)
        if fail is not None:
            return fail
        env = self.osleg.reject(entry["w4_msg_id"], reason, rejected_by, trace_id)
        if not env.get("ok"):
            return self._fail(str(env.get("status") or "w4_error"),
                              str(env.get("error") or "w4_reject_failed"),
                              msg_id=entry["w4_msg_id"])
        self._bind_gateway_store()
        gateway_view = self._gateway_view(entry["gateway_msg_id"])
        return self._ok_result(
            entry, self.osleg.receipt(msg_id=entry["w4_msg_id"]),
            gateway=gateway_view,
            gateway_state=str(gateway_view.get("state") or "unknown"),
            replayed=bool(env.get("replayed")),
            reason=str(env.get("reason") or ""),
            gateway_gap="reject_not_projected_to_gateway",
        )

    def expire(self, now=None) -> dict:
        """无消费者超时：只扫契约层（gateway 无超时语义，保持 inbox）。"""
        env = self.osleg.expire(now=now)
        return {
            "ok": True,
            "channel": CHANNEL,
            "expired": list(env.get("expired") or []),
            "t_expire_sec": env.get("t_expire_sec") or 0,
            "gateway_gap": "expire_not_projected_to_gateway",
        }

    # ---------------------------------------------------------- 双账回执
    def _divergences(self, entry: dict, gateway: dict, osleg_receipt: dict):
        """双账对账差异（空列表=共享标识/证据链自洽）。"""
        out = []
        if not isinstance(gateway, dict) or not gateway.get("ok"):
            out.append("gateway_receipt_failed")
            return out
        if gateway.get("state") == "not_found":
            out.append("gateway_record_missing")
        if str(entry.get("report_id") or "") != str(osleg_receipt.get("report_id") or ""):
            out.append("report_id_mismatch")
        if str(entry.get("trace_id") or "") != str(osleg_receipt.get("trace_id") or ""):
            out.append("trace_id_mismatch")
        state = str(osleg_receipt.get("state") or "")
        answered = bool(gateway.get("answered"))
        delivered = bool(gateway.get("delivered"))
        if state in (oc.STATE_ANSWERED, oc.STATE_DELIVERED) and not answered:
            out.append("gateway_answer_evidence_missing")
        if state == oc.STATE_DELIVERED and not delivered:
            out.append("gateway_delivery_evidence_missing")
        if answered and state not in (oc.STATE_CONSUMED, oc.STATE_ANSWERED,
                                      oc.STATE_DELIVERED):
            out.append("w4_state_behind_gateway_answer")
        if delivered and state != oc.STATE_DELIVERED:
            out.append("w4_state_behind_gateway_delivery")
        return out

    def receipt_both(self, gateway_msg_id: str = "", w4_msg_id: str = "",
                     report_id: str = "") -> dict:
        """双账对账视图：gateway receipt + 契约层 receipt + 映射。

        单条：传 gateway_msg_id 或 w4_msg_id 任一；state/status/linked/alive 口径
        直接取契约层 receipt（与契约 §四/§五 一致）。
        聚合：传 report_id，按契约 §五-3 聚合该报告下的全部记录，并附 paired
        gateway 账视图；`divergences` 非空表示两账不一致。
        """
        gateway_msg_id = str(gateway_msg_id or "").strip()
        w4_msg_id = str(w4_msg_id or "").strip()
        report_id = str(report_id or "").strip()
        if not gateway_msg_id and not w4_msg_id and not report_id:
            return self._fail("bad_receipt_key",
                              "gateway_msg_id_or_w4_msg_id_or_report_id_required")
        self._bind_gateway_store()
        if report_id:
            return self._receipt_aggregate(report_id)

        probe = gateway_msg_id or w4_msg_id
        entry, _ = self._resolve(probe)
        if entry is None and w4_msg_id and gateway_msg_id:
            entry, _ = self._resolve(w4_msg_id)
        if isinstance(entry, dict):
            gateway = self._gateway_view(entry["gateway_msg_id"])
            osleg_receipt = self.osleg.receipt(msg_id=entry["w4_msg_id"])
            if not osleg_receipt.get("ok"):
                return self._fail("ledger_diverged",
                                  "w4_record_missing_while_mapped",
                                  linked=False, alive=False,
                                  gateway=gateway, mapping=entry)
            divergences = self._divergences(entry, gateway, osleg_receipt)
            return {
                "ok": True,
                "channel": CHANNEL,
                "mapped": True,
                "gateway_msg_id": entry["gateway_msg_id"],
                "w4_msg_id": entry["w4_msg_id"],
                "msg_id": entry["w4_msg_id"],
                "report_id": str(entry.get("report_id") or ""),
                "trace_id": str(entry.get("trace_id") or ""),
                "state": osleg_receipt.get("state") or "unknown",
                "status": osleg_receipt.get("status")
                          or osleg_receipt.get("state") or "unknown",
                "linked": bool(osleg_receipt.get("linked")),
                "alive": bool(osleg_receipt.get("alive")),
                "duplicate": bool(osleg_receipt.get("duplicate")),
                "gateway_state": str(gateway.get("state") or "unknown"),
                "gateway": gateway,
                "osleg": osleg_receipt,
                "mapping": entry,
                "consistent": not divergences,
                "divergences": divergences,
            }
        return self._receipt_unmapped(probe)

    def _receipt_unmapped(self, probe: str) -> dict:
        """映射账里没有的记录：按前缀查单账，明示未映射（不伪装成功）。"""
        if probe.startswith("w4-"):
            osleg_receipt = self.osleg.receipt(msg_id=probe)
            if osleg_receipt.get("ok"):
                return {
                    "ok": True,
                    "channel": CHANNEL,
                    "mapped": False,
                    "gateway_msg_id": "",
                    "w4_msg_id": probe,
                    "report_id": str(osleg_receipt.get("report_id") or ""),
                    "trace_id": str(osleg_receipt.get("trace_id") or ""),
                    "state": osleg_receipt.get("state") or "unknown",
                    "status": osleg_receipt.get("status")
                              or osleg_receipt.get("state") or "unknown",
                    "linked": bool(osleg_receipt.get("linked")),
                    "alive": bool(osleg_receipt.get("alive")),
                    "duplicate": bool(osleg_receipt.get("duplicate")),
                    "gateway_state": "",
                    "gateway": None,
                    "osleg": osleg_receipt,
                    "mapping": None,
                    "consistent": False,
                    "divergences": ["mapping_missing"],
                }
        else:
            gateway = self._gateway_view(probe)
            if gateway.get("ok") and gateway.get("state") != "not_found":
                return {
                    "ok": True,
                    "channel": CHANNEL,
                    "mapped": False,
                    "gateway_msg_id": probe,
                    "w4_msg_id": "",
                    "report_id": "",
                    "trace_id": "",
                    "state": "unknown",
                    "status": "unknown",
                    "linked": False,
                    "alive": False,
                    "duplicate": False,
                    "gateway_state": str(gateway.get("state") or "unknown"),
                    "gateway": gateway,
                    "osleg": None,
                    "mapping": None,
                    "consistent": False,
                    "divergences": ["mapping_missing"],
                }
        return self._fail("not_found", "msg_not_found", msg_id=probe, mapped=False)

    def _receipt_aggregate(self, report_id: str) -> dict:
        osleg_receipt = self.osleg.receipt(report_id=report_id)
        if not osleg_receipt.get("ok"):
            return self._fail("not_found", "report_not_found",
                              report_id=report_id)
        links, fail = self._ledger(self._links_path)
        if fail is not None:
            return fail
        paired = [link for link in links.values()
                  if isinstance(link, dict)
                  and str(link.get("report_id") or "") == report_id]
        paired.sort(key=lambda link: str(link.get("linked_at") or ""))
        gateway_views = {link["gateway_msg_id"]: self._gateway_view(link["gateway_msg_id"])
                         for link in paired}
        records = {str(rec.get("msg_id") or ""): rec
                   for rec in (osleg_receipt.get("records") or [])}
        divergences = []
        gateway_ids = []
        for link in paired:
            gateway_ids.append(str(link.get("gateway_msg_id") or ""))
            w4_id = str(link.get("w4_msg_id") or "")
            w4_record = records.get(w4_id)
            if w4_record is None:
                divergences.append("w4_record_missing:%s" % w4_id)
                continue
            divergences.extend(self._divergences(
                link, gateway_views[link["gateway_msg_id"]], w4_record))
        channel_records = osleg_receipt.get("records") or []
        return {
            "ok": True,
            "channel": CHANNEL,
            "report_id": report_id,
            "w4_msg_ids": list(osleg_receipt.get("msg_ids") or []),
            "gateway_msg_ids": gateway_ids,
            "channels": list(osleg_receipt.get("channels") or []),
            "total": int(osleg_receipt.get("total") or 0),
            "states": [str(rec.get("state") or "") for rec in channel_records],
            "trace_ids": list(osleg_receipt.get("trace_ids") or []),
            "trace_consistent": bool(osleg_receipt.get("trace_consistent")),
            "linked": bool(osleg_receipt.get("linked")),
            "alive": bool(osleg_receipt.get("alive")),
            "duplicate": bool(osleg_receipt.get("duplicate")),
            "gateway": gateway_views,
            "osleg": osleg_receipt,
            "mappings": paired,
            "mapped_count": len(paired),
            "consistent": not divergences,
            "divergences": divergences,
        }
