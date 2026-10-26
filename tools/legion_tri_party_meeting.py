# -*- coding: utf-8 -*-
"""三方会议阶段一：结构化会议、双观点、分歧与人工确认。

本模块只负责会议材料与状态，不负责派工、执行或交易：
- 与会席位固定为 A0 田丰、AI1 诸葛亮，以及用户确认席；
- 模型完整返回保存在观点的 ``full_text``，结构化字段只做展示索引；
- heartbeat/events 只写摘要，不写模型原文；
- 默认不自动派工，确认也不会触发任何工单动作；
- ``model_runner`` 是测试/离线注入接口，``dry_run`` 不调用模型。

默认数据目录：agent_platform/data/tri_party_meetings/<meeting_id>.json
可通过环境变量 LEGION_TRI_PARTY_MEETING_DIR 覆盖，便于测试和隔离运行。
"""
from __future__ import annotations

import inspect
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))
MEETING_DIR = Path(
    os.environ.get(
        "LEGION_TRI_PARTY_MEETING_DIR",
        str(ROOT / "agent_platform" / "data" / "tri_party_meetings"),
    )
)
SCHEMA_VERSION = "legion-tri-party-meeting-v1"
CONFIRMATION_STATUSES = frozenset(("pending", "confirmed", "rejected"))
VIEW_FIELDS = ("position", "conclusion", "evidence", "risks", "next_actions", "assumptions", "confidence")

_PARTICIPANTS = {
    "A0": {"agent_id": "A0", "name": "田丰", "role": "主持与总管席"},
    "AI1": {"agent_id": "AI1", "name": "诸葛亮", "role": "方法论研究席"},
}
_FIELD_ALIASES = {
    "position": ("position", "stance", "view", "观点", "立场"),
    "conclusion": ("conclusion", "verdict", "结论", "判断"),
    "evidence": ("evidence", "basis", "依据", "证据"),
    "risks": ("risks", "risk", "limitations", "限制", "风险"),
    "next_actions": ("next_actions", "next_steps", "actions", "后续动作", "下一步"),
    "assumptions": ("assumptions", "假设"),
    "confidence": ("confidence", "置信度", "信心"),
}
_FIELD_LABELS = {
    "position": "核心观点",
    "conclusion": "结论",
    "evidence": "依据/证据",
    "risks": "风险/限制",
    "next_actions": "后续动作",
    "assumptions": "关键假设",
    "confidence": "置信度",
}


class MeetingError(ValueError):
    """会议输入、模型输出或状态更新不符合契约。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return "tpm-%s-%s" % (datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S"), uuid.uuid4().hex[:8])


def _validate_id(meeting_id: str) -> str:
    value = str(meeting_id or "").strip()
    if not value or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,119}", value):
        raise MeetingError("meeting_id 必须是 2-120 位安全标识")
    return value


def _path(meeting_id: str, data_dir: Path | None = None) -> Path:
    return (data_dir or MEETING_DIR) / (_validate_id(meeting_id) + ".json")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MeetingError("会议记录不可读: %s" % exc) from exc
    if not isinstance(value, dict):
        raise MeetingError("会议记录必须是 JSON 对象")
    return value


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value


def _list_value(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [_scalar(item) for item in value]
    if isinstance(value, tuple):
        return [_scalar(item) for item in value]
    text = str(value).strip()
    if not text:
        return []
    rows = [re.sub(r"^[-*•①②③④⑤]\s*", "", row.strip()) for row in re.split(r"[\n；;]", text)]
    return [row for row in rows if row]


def _lookup(source: Mapping[str, Any], field: str) -> Any:
    for key in _FIELD_ALIASES[field]:
        if key in source:
            return source[key]
    return None


def _heading_sections(text: str) -> dict[str, Any]:
    """从非 JSON 的模型文本中提取常见中文标题，提取失败不影响原文保存。"""
    found: dict[str, Any] = {}
    aliases = {alias: field for field, keys in _FIELD_ALIASES.items() for alias in keys}
    header = r"(?im)^\s*(?:#{1,4}\s*)?(%s)\s*[:：]?\s*$" % "|".join(re.escape(x) for x in aliases)
    matches = list(re.finditer(header, text))
    for index, match in enumerate(matches):
        field = aliases[match.group(1)]
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end():end].strip()
        if body:
            found[field] = body
    return found


def _coerce_model_output(raw: Any) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """返回 (完整原文, 结构化候选字段, 模型元数据)。"""
    meta: dict[str, Any] = {}
    if isinstance(raw, Mapping):
        meta = {k: _scalar(v) for k, v in raw.items() if k not in VIEW_FIELDS and k not in _FIELD_ALIASES}
        text_value = raw.get("full_text", raw.get("raw_text", raw.get("content", raw.get("text"))))
        if text_value is None:
            text_value = json.dumps(raw, ensure_ascii=False)
        full_text = str(text_value)
        structured = dict(raw)
        return full_text, structured, meta
    if raw is None:
        raise MeetingError("模型返回为空")
    full_text = str(raw)
    structured: dict[str, Any] = {}
    try:
        decoded = json.loads(full_text)
    except (TypeError, ValueError):
        decoded = None
    if isinstance(decoded, Mapping):
        structured = dict(decoded)
    else:
        structured = _heading_sections(full_text)
    return full_text, structured, meta


def _normalize_view(agent_id: str, raw: Any, model_meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    full_text, source, output_meta = _coerce_model_output(raw)
    view: dict[str, Any] = {
        "agent_id": agent_id,
        "name": _PARTICIPANTS[agent_id]["name"],
        "role": _PARTICIPANTS[agent_id]["role"],
        "status": "completed",
        "position": None,
        "conclusion": None,
        "evidence": [],
        "risks": [],
        "next_actions": [],
        "assumptions": [],
        "confidence": None,
        "full_text": full_text,
        "model": dict(model_meta or {}),
    }
    for field in VIEW_FIELDS:
        value = _lookup(source, field)
        if field in ("evidence", "risks", "next_actions", "assumptions"):
            view[field] = _list_value(value)
        else:
            view[field] = _scalar(value)
    if output_meta:
        view["model"].update(output_meta)
    return view


def _prompt(subject: str, context: str, agent_id: str) -> str:
    role = _PARTICIPANTS[agent_id]
    return (
        "你是数字军团三方会议的%s（%s，工号%s）。\n"
        "会议主题：%s\n"
        "输入材料：\n%s\n\n"
        "请独立形成观点，不代替用户确认，不派工，不声称执行了外部动作。"
        "请优先按 JSON 输出字段 position、conclusion、evidence、risks、next_actions、"
        "assumptions、confidence；所有字段都应基于输入材料，未知内容明确写未知。"
        % (role["name"], role["role"], agent_id, subject, context.strip() or "（未提供额外材料）")
    )


def _invoke_runner(runner: Any, agent_id: str, prompt: str, meeting: Mapping[str, Any]) -> tuple[Any, dict[str, Any]]:
    if isinstance(runner, Mapping):
        if agent_id not in runner:
            raise MeetingError("注入模型缺少席位: %s" % agent_id)
        return runner[agent_id], {}
    if not callable(runner):
        raise MeetingError("model_runner 必须是 mapping 或 callable")
    try:
        params = inspect.signature(runner).parameters
        positional = [p for p in params.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        raw = runner(agent_id, prompt) if len(positional) < 3 else runner(agent_id, prompt, meeting)
    except (TypeError, ValueError):
        raw = runner(agent_id, prompt)
    return raw, {}


def _default_runner(agent_id: str, prompt: str) -> tuple[Any, dict[str, Any]]:
    try:
        import agent_coder
        result = agent_coder.chat_auto_meta(agent_id, {"label": "tri-party-meeting:%s" % agent_id, "prompt": prompt})
    except Exception as exc:
        raise MeetingError("%s 模型调用失败: %s" % (agent_id, exc)) from exc
    return result.content, {"channel": result.channel, "requested": result.requested, "latency_s": result.latency_s}


def _compare_value(value: Any) -> str:
    if isinstance(value, list):
        return "；".join(str(x) for x in value)
    return "" if value is None else str(value).strip()


def decompose_disagreements(views: Mapping[str, Mapping[str, Any]] | list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """把 A0/AI1 结构化字段差异转换为可直接展示的分歧项。"""
    if isinstance(views, Mapping):
        left, right = views.get("A0", {}), views.get("AI1", {})
    else:
        by_id = {str(v.get("agent_id")): v for v in views}
        left, right = by_id.get("A0", {}), by_id.get("AI1", {})
    result = []
    for field in VIEW_FIELDS:
        a0, ai1 = _compare_value(left.get(field)), _compare_value(right.get(field))
        if not a0 and not ai1:
            continue
        if a0 == ai1:
            continue
        result.append({
            "dimension": field,
            "label": _FIELD_LABELS[field],
            "a0": a0 or "未提供",
            "ai1": ai1 or "未提供",
            "kind": "information_gap" if not a0 or not ai1 else "substantive",
            "requires_user_confirmation": True,
        })
    if not result and _compare_value(left.get("full_text")) != _compare_value(right.get("full_text")):
        result.append({
            "dimension": "overall",
            "label": "整体文本",
            "a0": _compare_value(left.get("full_text")) or "未提供",
            "ai1": _compare_value(right.get("full_text")) or "未提供",
            "kind": "substantive",
            "requires_user_confirmation": True,
        })
    return result


def _audit(kind: str, meeting: Mapping[str, Any], detail: str, *, enabled: bool) -> None:
    if not enabled:
        return
    meeting_id = meeting.get("meeting_id", "")
    summary = str(detail).replace("\n", " ").strip()[:180]
    try:
        from legion_pulse import heartbeat
        heartbeat("TRI_PARTY_MEETING", kind, summary, {"meeting_id": meeting_id})
    except Exception:
        pass
    try:
        from events import emit
        # 2026-10-19 判例：用户确认不是田丰直批（approval_verdict 是 K-ORG-054 直批灰度
        # 统计口径），改用独立 confirmation_verdict，防止晨报直批计数被会议确认污染。
        event_kind = "confirmation_verdict" if kind == "confirmation_updated" else "approval_request"
        emit(event_kind, task=meeting_id, agent="A0/AI1", detail=summary)
    except Exception:
        pass


def create_meeting(
    subject: str,
    context: str = "",
    *,
    meeting_id: str | None = None,
    model_runner: Any = None,
    dry_run: bool = False,
    save: bool = True,
    data_dir: Path | str | None = None,
    metadata: Mapping[str, Any] | None = None,
    trace_id: str | None = None,
    request_id: str | None = None,
    correlation_id: str | None = None,
    idempotency_key: str | None = None,
    audit: bool = True,
) -> dict[str, Any]:
    """创建一场会议并生成 A0/AI1 双观点。

    ``model_runner`` 注入约定：mapping 的键为 A0/AI1，值为模型原文或结构化字典；
    callable 可接收 ``(agent_id, prompt)`` 或 ``(agent_id, prompt, meeting)``。
    ``dry_run=True`` 使用稳定占位文本，不导入模型、不写 heartbeat/events。
    """
    title = str(subject or "").strip()
    if not title:
        raise MeetingError("会议主题不能为空")
    if not isinstance(context, str):
        raise MeetingError("会议材料必须是字符串")
    mid = _validate_id(meeting_id or _new_id())
    now = _now()
    meeting: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "meeting_id": mid,
        "created_at": now,
        "updated_at": now,
        "status": "pending_confirmation",
        "subject": title,
        "context": context,
        "participants": [dict(_PARTICIPANTS["A0"]), dict(_PARTICIPANTS["AI1"]),
                         {"agent_id": "USER", "name": "用户", "role": "确认席"}],
        "trace_id": trace_id or "tri-party-%s" % uuid.uuid4().hex[:12],
        "request_id": request_id or "req-%s" % uuid.uuid4().hex[:12],
        "correlation_id": correlation_id or "corr-%s" % uuid.uuid4().hex[:12],
        "idempotency_key": idempotency_key,
        "execution_policy": {
            "auto_dispatch": False,
            "dispatch_status": "not_requested",
            "requires_user_confirmation": True,
        },
        "confirmation": {"status": "pending", "confirmed_by": None, "confirmed_at": None, "note": ""},
        "views": {},
        "disagreements": [],
        "run": {"mode": "dry_run" if dry_run else "model", "model_called": False},
        "metadata": dict(metadata or {}),
    }
    for agent_id in ("A0", "AI1"):
        prompt = _prompt(title, context, agent_id)
        if dry_run:
            raw = json.dumps({
                "position": "dry-run 占位观点（未调用模型）",
                "conclusion": "需用户确认",
                "evidence": ["dry-run 未提供模型证据"],
                "risks": ["当前为离线演练，不可据此执行外部动作"],
                "next_actions": ["用户确认后再进入后续阶段"],
                "assumptions": [],
                "confidence": None,
            }, ensure_ascii=False)
            model_meta = {"channel": "dry-run"}
        else:
            if model_runner is None:
                raw, model_meta = _default_runner(agent_id, prompt)
            else:
                raw, model_meta = _invoke_runner(model_runner, agent_id, prompt, meeting)
            meeting["run"]["model_called"] = True
        meeting["views"][agent_id] = _normalize_view(agent_id, raw, model_meta)
    meeting["disagreements"] = decompose_disagreements(meeting["views"])
    if save:
        _write_json(_path(mid, Path(data_dir) if data_dir else None), meeting)
    _audit("created", meeting, "%s 创建；A0/AI1 双观点已生成，等待用户确认" % title,
           enabled=audit and save and not dry_run)
    return meeting


def load_meeting(meeting_id: str, *, data_dir: Path | str | None = None) -> dict[str, Any]:
    return _read_json(_path(meeting_id, Path(data_dir) if data_dir else None))


def update_confirmation(
    meeting_id: str,
    status: str,
    confirmed_by: str,
    *,
    note: str = "",
    data_dir: Path | str | None = None,
    audit: bool = True,
) -> dict[str, Any]:
    """更新用户确认状态；永不创建工单或触发派工。"""
    if status not in CONFIRMATION_STATUSES:
        raise MeetingError("confirmation status 必须是 pending/confirmed/rejected")
    by = str(confirmed_by or "").strip()
    if not by:
        raise MeetingError("confirmed_by 不能为空")
    meeting = load_meeting(meeting_id, data_dir=data_dir)
    timestamp = _now()
    meeting["confirmation"] = {"status": status, "confirmed_by": by,
                                "confirmed_at": timestamp, "note": str(note or "")[:500]}
    meeting["status"] = {"pending": "pending_confirmation", "confirmed": "confirmed", "rejected": "rejected"}[status]
    meeting["updated_at"] = timestamp
    meeting.setdefault("execution_policy", {}).update({
        "auto_dispatch": False,
        "dispatch_status": "not_requested",
        "requires_user_confirmation": status != "confirmed",
    })
    _write_json(_path(meeting_id, Path(data_dir) if data_dir else None), meeting)
    _audit("confirmation_updated", meeting, "%s 用户确认状态=%s（不自动派工）" % (meeting_id, status), enabled=audit)
    return meeting


confirm_meeting = update_confirmation


def list_meetings(*, data_dir: Path | str | None = None) -> list[dict[str, Any]]:
    directory = Path(data_dir) if data_dir else MEETING_DIR
    if not directory.exists():
        return []
    rows = []
    for path in sorted(directory.glob("*.json"), key=lambda item: item.name, reverse=True):
        try:
            row = _read_json(path)
        except MeetingError:
            continue
        rows.append({
            "meeting_id": row.get("meeting_id"),
            "subject": row.get("subject"),
            "status": row.get("status"),
            "confirmation": row.get("confirmation"),
            "disagreement_count": len(row.get("disagreements") or []),
            "created_at": row.get("created_at"),
        })
    return rows


__all__ = [
    "CONFIRMATION_STATUSES",
    "MEETING_DIR",
    "MeetingError",
    "SCHEMA_VERSION",
    "confirm_meeting",
    "create_meeting",
    "decompose_disagreements",
    "list_meetings",
    "load_meeting",
    "update_confirmation",
]
