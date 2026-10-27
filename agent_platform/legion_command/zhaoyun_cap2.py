# -*- coding: utf-8 -*-
"""赵云能力二期：审批闭环 / 能力自检 / 模型路由 / 任务干预 / 记忆策展 / 使用留证。

定位：zhaoyun_drill（下钻+请求登记）之上的"审批执行 + 能力治理"层。
安全边界：
  · 审批（approve/reject）由**人在页面**操作（signer 必填，当前无公钥校验）；
  · 执行器只写"可逆/可审计"的落地文件：
      - memory_quarantine → data/zhaoyun_memory/quarantine.jsonl（不删原经验）
      - capability_learn   → data/zhaoyun_capabilities/pending_learn.jsonl（等生成器收录）
      - model_change       → data/zhaoyun_model_overrides.json（覆盖配置；载体重启后生效）
      - task_intervention  → data/zhaoyun_interventions.jsonl（建议台账，不自动改工单）
  · 不直接改 org.json / bundle / CAPABILITIES.json；每次审批落 approval_verdict 事件。
"""

from __future__ import annotations

import difflib
import json
import os
import sys
import time
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[1]
REPO = PLATFORM.parent
PROFILE = Path(os.path.expanduser("~")) / ".hermes" / "profiles" / "zhaoyun"
REQUESTS_KIND = (
    "memory_quarantine",
    "capability_learn",
    "model_change",
    "task_intervention",
)
_SUGGEST = {
    "doing_stalled": "催办：联系 owner 确认阻塞点；超 48h 建议拆单/补位",
    "review_stalled": "验收：指定验收人；48h 未验收应退回 doing",
    "unowned": "认领：补 owner（A0 归口派单），无人认领则回收",
    "worktree_missing": "回收：确认分支已合并后关闭工单（不要误删未合并工作）",
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _read_jsonl(path: Path, limit: int = 2000) -> list:
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines[-limit:]:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def _append_jsonl(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _requests_dir(platform: Path) -> Path:
    return Path(platform) / "data" / "zhaoyun_requests"


# ── 使用留证（drill-evidence）──────────────────────────────────────────────
def usage_log_path(platform: Path) -> Path:
    return Path(platform) / "data" / "zhaoyun_capability_usage.jsonl"


def log_usage(platform: Path, capability: str, source: str = "page") -> dict:
    rec = {"ts": _now(), "capability": str(capability), "source": str(source)}
    _append_jsonl(usage_log_path(platform), rec)
    return {"ok": True, "record": rec}


def last_used(platform: Path, capability: str) -> dict:
    rows = [
        r
        for r in _read_jsonl(usage_log_path(platform), limit=500)
        if r.get("capability") == capability
    ]
    return {"ok": True, "count": len(rows), "last": rows[-1] if rows else None}


# ── 能力自检 ───────────────────────────────────────────────────────────────
def selfaudit(platform: Path | None = None, profile: Path | None = None) -> dict:
    plat = Path(platform) if platform else PLATFORM
    sys.path.insert(0, str(plat / "tools"))
    try:
        from capability_self_audit import check  # noqa: WPS433

        return check(Path(profile) if profile else PROFILE)
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "error": "audit_import_failed",
            "detail": "%s: %s" % (type(e).__name__, str(e)[:120]),
            "drift": [],
        }


# ── 模型路由 ───────────────────────────────────────────────────────────────
def routing(platform: Path | None = None) -> dict:
    plat = Path(platform) if platform else PLATFORM
    sys.path.insert(0, str(plat / "tools"))
    try:
        from model_router import route, usage_summary  # noqa: WPS433
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "router_import_failed", "detail": str(e)[:120]}
    plans = {p: route(p) for p in ("governance", "deep-reasoning", "glm-quota")}
    return {
        "ok": True,
        "plans": plans,
        "usage": usage_summary(10),
        "note": "429 自动降级：deepseek-flash → v4 → 本地 9b；glm 通道有配额风险",
    }


# ── 任务干预建议（申请制，不自动改工单）────────────────────────────────────
def interventions(platform: Path | None = None) -> dict:
    plat = Path(platform) if platform else PLATFORM
    sys.path.insert(0, str(plat))
    try:
        from legion_command import zhaoyun_drill as zd  # noqa: WPS433

        tv = zd.tasks_view(plat)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "tasks_view_failed", "detail": str(e)[:120]}
    items = []
    for d in tv.get("deviations") or []:
        items.append(
            {
                "tid": d.get("tid"),
                "kind": d.get("kind"),
                "detail": d.get("detail"),
                "suggest": _SUGGEST.get(d.get("kind"), "人工复核"),
                "action": "task_intervention",
            }
        )
    return {
        "ok": True,
        "generated_at": _now(),
        "count": len(items),
        "items": items,
        "note": "建议只登记申请；不自动改 tasks.json",
    }


# ── 记忆策展建议（去重/低置信/隔离候选）──────────────────────────────────
def memory_curation(profile: Path | None = None) -> dict:
    prof = Path(profile) if profile else PROFILE
    idx = _read_jsonl(prof / "experience" / "_index.jsonl")
    cues = [
        (str(e.get("entry_id") or ""), str(e.get("cue") or ""))
        for e in idx
        if e.get("entry_id")
    ]
    groups = []
    used = set()
    for i in range(len(cues)):
        for j in range(i + 1, len(cues)):
            a, b = cues[i], cues[j]
            if a[0] in used or b[0] in used:
                continue
            ratio = difflib.SequenceMatcher(None, a[1], b[1]).ratio()
            if ratio >= 0.75 and a[1] and b[1]:
                groups.append(
                    {
                        "kind": "duplicate",
                        "entry_ids": [a[0], b[0]],
                        "ratio": round(ratio, 2),
                        "detail": "cue 高度相似，建议合并/保留一条",
                    }
                )
                used.update([a[0], b[0]])
    for e in idx:
        conf = e.get("confidence")
        try:
            c = float(conf)
        except (TypeError, ValueError):
            continue
        if e.get("entry_id") and c < 0.7:
            groups.append(
                {
                    "kind": "low_confidence",
                    "entry_ids": [e["entry_id"]],
                    "confidence": c,
                    "detail": "置信度偏低，建议复核后降权/隔离",
                }
            )
    return {
        "ok": True,
        "generated_at": _now(),
        "entries": len(idx),
        "suggestions": groups,
        "count": len(groups),
        "note": "策展建议只读；隔离须提交 memory_quarantine 申请并经审批",
    }


# ── 审批闭环 ───────────────────────────────────────────────────────────────
def _emit_verdict(rid: str, decision: str, signer: str, detail: str) -> dict:
    try:
        sys.path.insert(0, str(REPO / "tools"))
        import events  # noqa: WPS433

        return events.emit(
            kind="approval_verdict",
            task="ZHAOYUN",
            agent="ZHAOYUN",
            detail=("治理请求 %s %s by %s: %s" % (rid, decision, signer, detail))[:300],
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__}


def _execute(platform: Path, kind: str, payload: dict, rid: str, signer: str) -> dict:
    base = {"kind": kind, "applied": False}
    if kind == "memory_quarantine":
        ids = [str(x) for x in (payload.get("entry_ids") or []) if x]
        if not ids:
            return {**base, "error": "no_entry_ids"}
        path = Path(platform) / "data" / "zhaoyun_memory" / "quarantine.jsonl"
        for eid in ids:
            _append_jsonl(
                path,
                {
                    "ts": _now(),
                    "entry_id": eid,
                    "request_id": rid,
                    "approved_by": signer,
                },
            )
        return {
            **base,
            "applied": True,
            "target": str(path),
            "count": len(ids),
            "note": "已隔离（原经验库不删，可审计）",
        }
    if kind == "capability_learn":
        name = str((payload or {}).get("name") or "").strip()
        if not name:
            return {**base, "error": "no_name"}
        path = Path(platform) / "data" / "zhaoyun_capabilities" / "pending_learn.jsonl"
        _append_jsonl(
            path,
            {
                "ts": _now(),
                "request_id": rid,
                "approved_by": signer,
                "capability": payload,
            },
        )
        return {
            **base,
            "applied": True,
            "target": str(path),
            "note": "已登记待收录；CAPABILITIES.json 由生成器下一轮纳入",
        }
    if kind == "model_change":
        seat = str((payload or {}).get("seat") or "").strip()
        cand = str((payload or {}).get("candidate") or "").strip()
        if not seat or not cand:
            return {**base, "error": "no_seat_or_candidate"}
        path = Path(platform) / "data" / "zhaoyun_model_overrides.json"
        data = _read_json(path) or {}
        data.setdefault("overrides", []).append(
            {
                "ts": _now(),
                "request_id": rid,
                "approved_by": signer,
                "seat": seat,
                "candidate": cand,
                "status": "approved",
            }
        )
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        return {
            **base,
            "applied": True,
            "target": str(path),
            "note": "已写覆盖配置；载体重启后生效（不自动重启）",
        }
    if kind == "task_intervention":
        path = Path(platform) / "data" / "zhaoyun_interventions.jsonl"
        _append_jsonl(
            path,
            {
                "ts": _now(),
                "request_id": rid,
                "approved_by": signer,
                "payload": payload,
            },
        )
        return {
            **base,
            "applied": True,
            "target": str(path),
            "note": "已登记干预建议；不自动改工单",
        }
    return {**base, "error": "bad_kind"}


def approve(
    platform: Path | None = None,
    rid: str = "",
    decision: str = "",
    signer: str = "",
    note: str = "",
) -> dict:
    plat = Path(platform) if platform else PLATFORM
    rid = str(rid or "").strip()
    decision = str(decision or "").strip().lower()
    signer = str(signer or "").strip()
    if decision not in ("approve", "reject"):
        return {"ok": False, "error": "bad_decision"}
    if not signer:
        return {"ok": False, "error": "signer_required"}
    d = _requests_dir(plat)
    path = d / (rid + ".json")
    rec = _read_json(path)
    if not isinstance(rec, dict):
        return {"ok": False, "error": "request_not_found", "id": rid}
    if rec.get("status") != "pending_human":
        return {"ok": False, "error": "already_decided", "status": rec.get("status")}
    applied = {"applied": False}
    if decision == "approve":
        applied = _execute(
            plat, str(rec.get("kind") or ""), rec.get("payload") or {}, rid, signer
        )
        if applied.get("error"):
            return {"ok": False, "error": "execute_failed", "detail": applied}
    rec.update(
        {
            "status": "approved" if decision == "approve" else "rejected",
            "decided_by": signer,
            "decided_at": _now(),
            "decision_note": str(note or "")[:400],
            "applied": bool(applied.get("applied")),
            "execute_result": applied,
        }
    )
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    ev = _emit_verdict(
        rid,
        decision,
        signer,
        ("applied=%s %s" % (rec["applied"], applied.get("note") or "")),
    )
    return {"ok": True, "request": rec, "event": ev}


def overrides(platform: Path | None = None) -> dict:
    plat = Path(platform) if platform else PLATFORM
    data = _read_json(plat / "data" / "zhaoyun_model_overrides.json") or {}
    return {"ok": True, "overrides": data.get("overrides") or []}
