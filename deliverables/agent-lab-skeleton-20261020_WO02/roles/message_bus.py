"""汇报总线（message_bus）骨架。

职责（对齐 README 角色表 #9 与 draft.json 契约）：
- 分层汇报路由：agent → platform → command → principal；
- 拦截越级直发与伪造署名，拒绝条目进死信（dlq）并附明确原因；
- 正常消息追加 jsonl 账本（只追加，不改写）；
- 状态点归集：OS 只收「谁·在干什么·干到哪·产出在哪」，不造数。

本模块为骨架（scaffold）：标注 TODO 处为业务策略占位，需按领域实现。
所有 I/O 仅用标准库（json/pathlib/threading）；第三方仅 fastapi。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from fastapi import FastAPI

# 汇报层级：基层 → 平台 → 中台 → 本人（与 roles/base_agent.py 保持同一契约）。
REPORT_LEVELS = ("agent", "platform", "command", "principal")

# 允许直发本人（level=principal）的席位白名单：
#   risk_guard    —— 风控预警（只预警不执行，白名单既有语义）
#   approval_gate —— 本人签准闭环（白名单既有语义）
#   tian_feng     —— 归口席扩员（老田 2026-10-20 裁定：方案B 白名单扩员）；
#                     仅阻断类可直发本人，分级判定在归口席（tian_feng），
#                     总线只校验消息类别与来源，不掌握分级语义。
# 其余席位（含监督席 zhuge_liang/zhao_yun）一律越级拦截入死信。
PRINCIPAL_WHITELIST = ("risk_guard", "approval_gate", "tian_feng")

# principal 层消息类别（可审计分类）：类别 → (subject 类别前缀, 允许发送该类别的席位)。
#
# 白名单扩员后 principal 通道同时承载预警、签准、整改工单与升级请示；
# 审计上必须能区分「归口派单（整改工单）」与「越级上报（升级请示）」，
# 否则无法分辨消息性质。故以 subject 类别前缀作为分类载体
# （Report 结构变更需版本化，前缀为等价机制；TODO(领域实现): 若 Report
# 增列 kind 字段，改为字段承载）。
PRINCIPAL_MESSAGE_CLASSES: Dict[str, tuple] = {
    "remediation_ticket": ("[整改工单]", ("tian_feng",)),
    "escalation_request": ("[升级请示]", ("tian_feng",)),
    "alert": ("[预警]", ("risk_guard",)),
    "approval": ("[待签]", ("approval_gate",)),
}

# 状态点归集字段：黑盒原则，只登记这六项，缺失如实存 None，不造数。
STATUS_POINT_FIELDS = ("agent", "platform", "doing", "progress", "artifact", "ts")

# 死信回捞许可名单：仅中台三席或本人可发起回捞。
# 与 PRINCIPAL_WHITELIST 无关：risk_guard/approval_gate 只放行 principal 层消息，
# 不拥有死信回捞权。
REQUEUE_AUTHORIZED = ("zhuge_liang", "tian_feng", "zhao_yun", "principal")

PathLike = Union[str, Path]


def classify_message_class(subject: str) -> str:
    """从 subject 前缀解析消息类别；无已登记前缀返回空串。

    前缀是本骨架的分类载体（Report 结构变更需版本化，见
    PRINCIPAL_MESSAGE_CLASSES 注释）。解析只认已登记前缀，
    未登记前缀不猜类别（返回空串，交由调用方按层级规则处理）。
    """
    text = str(subject or "")
    for name, (prefix, _allowed) in PRINCIPAL_MESSAGE_CLASSES.items():
        if text.startswith(prefix):
            return name
    return ""


class MessageBus:
    """汇报总线：席位注册、层级路由校验、死信与账本、状态点归集。"""

    def __init__(
        self,
        ledger_path: PathLike = "data/bus_ledger.jsonl",
        dlq_path: PathLike = "data/bus_dlq.jsonl",
    ) -> None:
        self.ledger_path = Path(ledger_path)
        self.dlq_path = Path(dlq_path)
        self._seats: Dict[str, str] = {}          # name -> level
        self._status_points: Dict[str, Dict[str, Any]] = {}  # agent -> 最新状态点
        self._lock = threading.Lock()

    # ---------------- 席位注册 ----------------

    def register(self, name: str, level: str) -> None:
        """注册席位（name, level）。重复注册以最后一次为准。

        TODO(领域实现): 席位身份凭证（防注册面伪造），当前骨架仅按名单校验。
        """
        if level not in REPORT_LEVELS:
            raise ValueError(f"非法席位层级: {level}（须为 {REPORT_LEVELS} 之一）")
        with self._lock:
            self._seats[name] = level

    def is_registered(self, name: str) -> bool:
        return name in self._seats

    # ---------------- 汇报路由 ----------------

    def route(self, report: Dict[str, Any], expected_target: str = "") -> Dict[str, Any]:
        """路由一条上行汇报。返回 {"ok": bool, "reason": str, ...}。

        校验顺序：
        a) source 必须已注册，伪署名进死信；
        b) report.level 必须是合法层级，且不得低于 source 自身 level（禁止向下汇报）；
        c) level=principal 直发仅允许白名单席位（risk_guard 预警 /
           approval_gate 签准 / tian_feng 归口席），其余席位一律进死信
           并明确拒绝（越级拦截）；
        c2) principal 消息必须带已登记的类别前缀（整改工单 / 升级请示 /
            预警 / 待签），且该类别允许本源席位发送——白名单扩员后
            principal 通道多类并存，须按类别可审计、可分辨性质；
        d) 通过校验的消息追加 jsonl 账本。
        """
        source = report.get("source")
        level = report.get("level")

        # a) 伪署名：未注册来源一律拒绝并进死信
        if not source or source not in self._seats:
            return self._to_dlq(report, expected_target, f"伪署名：来源席位未注册 source={source!r}")

        # b) 层级合法性 + 禁止向下汇报
        if level not in REPORT_LEVELS:
            return self._to_dlq(report, expected_target, f"非法汇报层级: {level!r}")
        source_level = self._seats[source]
        if REPORT_LEVELS.index(level) < REPORT_LEVELS.index(source_level):
            return self._to_dlq(
                report, expected_target,
                f"禁止向下汇报：source 层级 {source_level} 不得发往更低层级 {level}",
            )

        # c) 越级拦截：principal 直发仅白名单席位
        if level == "principal" and source not in PRINCIPAL_WHITELIST:
            return self._to_dlq(
                report, expected_target,
                f"越级拦截：席位 {source} 层级 {source_level} 不得直发 principal"
                f"（仅 {PRINCIPAL_WHITELIST} 允许）",
            )

        # c2) 类别校验：principal 消息须带已登记类别前缀，且类别允许本源席位
        message_class = classify_message_class(report.get("subject", ""))
        if level == "principal":
            if not message_class:
                return self._to_dlq(
                    report, expected_target,
                    f"principal 消息缺少类别前缀（可审计分类）："
                    f"须为 {sorted(PRINCIPAL_MESSAGE_CLASSES)} 之一",
                )
            allowed_sources = PRINCIPAL_MESSAGE_CLASSES[message_class][1]
            if source not in allowed_sources:
                return self._to_dlq(
                    report, expected_target,
                    f"消息类别越权：类别 {message_class} 仅允许席位 "
                    f"{allowed_sources} 发送，来源 {source}",
                )

        # d) 正常消息追加账本
        record = {
            "kind": "routed",
            "ts": time.time(),
            "expected_target": expected_target,
            "source": source,
            "source_level": source_level,
            # 消息类别随账本留痕，供按类型审计（整改工单 vs 升级请示）
            "message_class": message_class,
            "report": dict(report),
        }
        # TODO(领域实现): 实际投递到 expected_target（当前骨架只记账不投递）；
        #                 投递失败重试与死信回捞策略。
        self._append(self.ledger_path, record)
        return {"ok": True, "reason": "", "record_ts": record["ts"]}

    # ---------------- 状态点归集 ----------------

    def collect_status_point(self, point: Dict[str, Any]) -> Dict[str, Any]:
        """归集一条状态点：只登记六字段，缺失如实存 None，不造数。

        板块对 OS 是黑盒——只收「谁·在干什么·干到哪·产出在哪」，
        不解析板块内部语义，不造第二账。
        """
        normalized: Dict[str, Any] = {
            field: point.get(field) for field in STATUS_POINT_FIELDS
        }
        agent = normalized.get("agent")
        if not agent:
            # 无归属席位的状态点无法归集到任何人头上，如实拒绝（不代填 agent）。
            return {"ok": False, "reason": "状态点缺失 agent 字段，无法归集"}

        with self._lock:
            self._status_points[agent] = normalized
        return {"ok": True, "agent": agent}

    def board(self) -> List[Dict[str, Any]]:
        """归集视图：按 agent 保留最新一条状态点。"""
        with self._lock:
            # TODO(领域实现): 状态点过期判定与心跳断连标记（对接督办口径）。
            return [dict(p) for p in self._status_points.values()]

    # ---------------- 死信回捞 ----------------

    def dead_letters(self) -> List[Dict[str, Any]]:
        """当前死信副本（只读视图，改动返回值不影响死信文件）。"""
        with self._lock:
            return self._read_dlq_locked()

    def requeue_dead_letter(self, index: int, *, by: str) -> Dict[str, Any]:
        """回捞死信：取出第 index 条死信，重新走完整 route 校验。

        - by 仅限中台三席（zhuge_liang/tian_feng/zhao_yun）或 principal，
          其余（含 risk_guard/approval_gate 及未知席位）一律拒绝；
        - 回捞消息重新过 route 四道校验：伪署名、越级照拦，
          再次被拒则重新入死信（回捞仅人工触发，不自动重试，无死循环）；
        - 每次回捞（无论成败）写 bus_ledger.jsonl 审计行（动作=requeue、by、结果）。

        TODO(领域实现): 回捞操作者身份凭证校验（依赖席位凭证，见 register 的 TODO）。
        """
        if by not in REQUEUE_AUTHORIZED:
            result = {
                "ok": False,
                "reason": f"无权回捞：by={by!r} 不在许可名单 {REQUEUE_AUTHORIZED}",
            }
            return self._audit_requeue(index, by, result, report=None)

        with self._lock:
            entries = self._read_dlq_locked()
            valid = isinstance(index, int) and 0 <= index < len(entries)
            entry = entries.pop(index) if valid else None
            if valid:
                self._rewrite_dlq_locked(entries)

        if entry is None:
            result = {
                "ok": False,
                "reason": f"死信序号无效: index={index}（当前共 {len(entries)} 条）",
            }
            return self._audit_requeue(index, by, result, report=None)

        # 锁外重走完整 route 校验（route 内部自行加锁），
        # 被拒消息经 _to_dlq 重新入死信，不会自动再捞。
        route_result = self.route(entry["report"], entry.get("expected_target", ""))
        return self._audit_requeue(index, by, route_result, report=entry["report"])

    def _audit_requeue(self, index: int, by: str, result: Dict[str, Any],
                       report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """回捞审计：无论成败追加一行账本（动作=requeue、by、结果）。"""
        self._append(self.ledger_path, {
            "kind": "requeue",
            "ts": time.time(),
            "action": "requeue",
            "by": by,
            "dlq_index": index,
            "ok": bool(result.get("ok")),
            "reason": result.get("reason", ""),
            "report": dict(report) if report is not None else None,
        })
        out = dict(result)
        out["requeued_by"] = by
        out["dlq_index"] = index
        return out

    def _read_dlq_locked(self) -> List[Dict[str, Any]]:
        """读取死信文件为列表（调用方须已持有 self._lock）。"""
        if not self.dlq_path.exists():
            return []
        lines = self.dlq_path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def _rewrite_dlq_locked(self, entries: List[Dict[str, Any]]) -> None:
        """回捞取走条目后重写死信文件（调用方须已持有 self._lock）。"""
        text = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries)
        self.dlq_path.write_text(text, encoding="utf-8")

    # ---------------- 内部：jsonl 追加 ----------------

    def _append(self, path: Path, record: Dict[str, Any]) -> None:
        """追加一行 jsonl；目录不存在则创建。锁内执行保证追加原子性。"""
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _to_dlq(self, report: Dict[str, Any], expected_target: str,
                reason: str) -> Dict[str, Any]:
        """拒绝条目进死信，附明确拒绝原因。"""
        self._append(self.dlq_path, {
            "kind": "dlq",
            "ts": time.time(),
            "expected_target": expected_target,
            "reason": reason,
            "report": dict(report),
        })
        return {"ok": False, "reason": reason, "dlq": True}


def create_app(bus: MessageBus) -> FastAPI:
    """FastAPI 应用工厂。死信与账本路径由 bus 注入，默认落 data/。

    TODO(领域实现): 接入鉴权（各席位调用凭证），当前骨架端口只做结构验证。
    """
    app = FastAPI(title="os-legion-message-bus", version="0.1.0-skeleton")

    @app.post("/route")
    def route(payload: Dict[str, Any]) -> Dict[str, Any]:
        report = payload.get("report") or {}
        expected_target = payload.get("expected_target", "")
        return bus.route(report, expected_target)

    @app.post("/status")
    def status(point: Dict[str, Any]) -> Dict[str, Any]:
        return bus.collect_status_point(point)

    @app.get("/board")
    def board() -> Dict[str, Any]:
        return {"points": bus.board()}

    @app.get("/health")
    def health() -> Dict[str, Any]:
        return {"ok": True, "service": "message_bus",
                "ledger": str(bus.ledger_path), "dlq": str(bus.dlq_path)}

    return app
