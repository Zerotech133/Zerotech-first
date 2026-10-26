# -*- coding: utf-8 -*-
"""赵云 · 能力下钻与调整请求（治理席，只读为主 + 请求型动作）。

页面 /zhaoyun 的每个环节都可"下转"：
  · 能力卡 → 单能力详证：how/boundary/status/证据文件是否存在/最近治理事件；
  · 任务偏差 → 正在执行/待评审/未认领/超时 stalled、worktree 缺失等偏差标注；
  · 记忆审计 → 经验库条目清单 + 已有隔离区；可提交"隔离申请"（不直接删）；
  · 模型信息 → 当前 ZHAOYUN/R2 绑定 + 可用模型（DSH 载体/Gateway 口径）；
  · 学习新能力 → 提案登记（须老田审批后由能力卡生成器收录）。

调整类动作（模型切换/学习/记忆删减）**一律先写请求**到
`agent_platform/data/zhaoyun_requests/<id>.json`，状态 pending_human，
并落 dev_events(approval_request)；**本模块绝不自动改真源**。

只读失败关闭：单源读不到 → ok=False + error，不影响其它段。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

PROFILE = Path(os.path.expanduser("~")) / ".hermes" / "profiles" / "zhaoyun"
PLATFORM = Path(__file__).resolve().parents[1]


def _skill_info(name: str) -> dict:
    """技能注册表元信息 + 最近 verify（注册表缺失时返回空）。"""
    try:
        sys.path.insert(0, str(PLATFORM / "tools"))
        from skill_registry import get_skill, state  # noqa: WPS433
        return {"skill": get_skill(name),
                "last_verified": (state().get("items") or {}).get(name)}
    except Exception:  # noqa: BLE001
        return {}
REQUESTS_KIND = ("memory_quarantine", "capability_learn", "model_change",
                 "task_intervention")
GAPS = [
    {"name": "model-control", "what": "查看/切换席位模型（申请制）",
     "why": "此前只有 DSH 会话载体，无切换入口"},
    {"name": "capability-learning", "what": "新能力提案 → 老田审批 → 能力卡收录",
     "why": "学习不是自动改 CAPABILITIES.json，须人审"},
    {"name": "memory-review", "what": "记忆/经验审计、隔离、删减（可逆）",
     "why": "经验库只读展示，缺少删减/隔离通道"},
    {"name": "task-deviation", "what": "执行中/未执行任务偏差巡检与调整申请",
     "why": "任务账本只有计数，缺 stalled/未认领/worktree 缺失标注"},
    {"name": "request-approval", "what": "治理动作 申请→审批→执行 闭环",
     "why": "本次先登记请求，审批执行通道后续接审批卡"},
    {"name": "capability-self-audit", "what": "能力卡完整性/新鲜度自检",
     "why": "CAPABILITIES.json 有没有漂移需每日核对"},
]


def _repo(platform: Path) -> Path:
    return Path(platform).resolve().parent


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _read_jsonl(path: Path, limit: int = 400) -> list:
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


def _iso(ts: float) -> str:
    try:
        return dt.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")
    except (OSError, ValueError):
        return ""


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


# ── 能力卡下钻 ──────────────────────────────────────────────────────────────
def capabilities_detail(platform: Path, profile: Path | None = None) -> dict:
    platform = Path(platform)
    prof = Path(profile) if profile else PROFILE
    data = _read_json(prof / "skills" / "CAPABILITIES.json") or {}
    caps = data.get("capabilities") or []
    repo = _repo(platform)
    recent = [e for e in _read_jsonl(repo / "portal" / "data" / "dev_events.jsonl")
              if str(e.get("agent")) in ("ZHAOYUN", "R2")]
    items = []
    for c in caps:
        if not isinstance(c, dict):
            continue
        how = str(c.get("how") or "")
        m = re.search(r"([A-Za-z0-9_./\\-]+\.(?:py|ps1))", how)
        ref = m.group(1) if m else ""
        ref_ok = None
        ref_mtime = ""
        if ref:
            cand = Path(ref) if os.path.isabs(ref) else (repo / ref)
            if cand.exists():
                ref_ok = True
                ref_mtime = _iso(cand.stat().st_mtime)
            else:
                ref_ok = False
        info = _skill_info(str(c.get("name")))
        items.append({
            "name": str(c.get("name")), "what": str(c.get("what") or ""),
            "how": how, "boundary": str(c.get("boundary") or ""),
            "status": str(c.get("status") or "unknown"),
            "how_ref": ref, "how_ref_exists": ref_ok, "how_ref_mtime": ref_mtime,
            "skill": info.get("skill"), "last_verified": info.get("last_verified"),
            "recent_events": [{"ts": e.get("ts"), "kind": e.get("kind"),
                               "task": e.get("task"), "detail": str(e.get("detail") or "")[:80]}
                              for e in recent[-3:]],
        })
    return {"ok": True, "generated_at": data.get("generated_at") or "",
            "total": int(data.get("total") or len(items)),
            "ready": int(data.get("ready") or sum(1 for x in items if x["status"] == "ready")),
            "items": items, "gaps": GAPS,
            "note": "能力详证为只读；新增能力须提案→老田审批→生成器收录"}


# ── 任务偏差 ────────────────────────────────────────────────────────────────
def _age_h(created: str) -> float:
    try:
        t = dt.datetime.strptime(str(created), "%Y-%m-%d %H:%M:%S")
        return max(0.0, (dt.datetime.now() - t).total_seconds() / 3600.0)
    except ValueError:
        return 0.0


def tasks_view(platform: Path) -> dict:
    repo = _repo(platform)
    data = _read_json(repo / ".worktrees" / "tasks.json")
    if not isinstance(data, dict):
        return {"ok": False, "error": "tasks_unreadable"}
    doing, review, other = [], [], 0
    deviations = []
    for tid, t in data.items():
        if not isinstance(t, dict):
            continue
        st = str(t.get("status") or "")
        row = {"tid": tid, "status": st, "owner": t.get("owner") or "",
               "desc": str(t.get("desc") or "")[:80],
               "created": t.get("created") or "",
               "age_hours": round(_age_h(t.get("created") or ""), 1),
               "unowned": ("[UNOWNED]" in str(t.get("desc") or "")
                           or str(t.get("owner") or "") in ("", "unknown-agent")),
               "worktree": t.get("worktree") or "",
               "worktree_missing": bool(t.get("worktree")) and not Path(t["worktree"]).exists()}
        if st == "doing":
            doing.append(row)
        elif st == "review":
            review.append(row)
        else:
            other += 1
        if row["unowned"] and st in ("doing", "review"):
            deviations.append({"kind": "unowned", "tid": tid,
                               "detail": "工单无明确 owner，执行责任不清"})
        if st == "doing" and row["age_hours"] > 24:
            deviations.append({"kind": "doing_stalled", "tid": tid,
                               "detail": "doing 已 %.1fh（>24h）无收口动作" % row["age_hours"]})
        if st == "review" and row["age_hours"] > 48:
            deviations.append({"kind": "review_stalled", "tid": tid,
                               "detail": "review 已 %.1fh（>48h）待验收" % row["age_hours"]})
        if row["worktree_missing"] and st in ("doing", "review"):
            deviations.append({"kind": "worktree_missing", "tid": tid,
                               "detail": "worktree 路径不存在（可能已被回收）"})
    return {"ok": True, "generated_at": _now(),
            "counts": {"doing": len(doing), "review": len(review),
                       "other": other, "deviations": len(deviations)},
            "doing": sorted(doing, key=lambda r: -r["age_hours"])[:40],
            "review": sorted(review, key=lambda r: -r["age_hours"])[:40],
            "deviations": deviations[:40],
            "note": "只读 + 偏差标注；调整须走工单/审批，不自动改账本"}


# ── 记忆审计 ────────────────────────────────────────────────────────────────
def memory_view(platform: Path, profile: Path | None = None) -> dict:
    platform = Path(platform)
    prof = Path(profile) if profile else PROFILE
    idx = _read_jsonl(prof / "experience" / "_index.jsonl")
    items = []
    for e in idx:
        items.append({"entry_id": e.get("entry_id") or e.get("id") or "",
                      "domain": e.get("domain") or "",
                      "cue": str(e.get("cue") or "")[:100],
                      "confidence": e.get("confidence"),
                      "signed_by": e.get("signed_by") or "",
                      "verified": e.get("verified"),
                      "written_at": e.get("written_at") or ""})
    qpath = platform / "data" / "zhaoyun_memory" / "quarantine.jsonl"
    quarantined = _read_jsonl(qpath)
    mem_dir = platform / "data" / "memory"
    streams = []
    if mem_dir.is_dir():
        for p in sorted(mem_dir.glob("agent__ZHAOYUN__*.jsonl")):
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            streams.append({"name": p.name, "entries": len(lines),
                            "last": (lines[-1][:160] if lines else "")})
    return {"ok": True, "generated_at": _now(),
            "entries_total": len(items), "items": items[-200:],
            "quarantined_total": len(quarantined),
            "quarantined": quarantined[-50:], "memory_streams": streams,
            "note": "只读清单；隔离/删减必须提交申请并等老田审批（不直接删真源）"}


# ── 模型信息 ────────────────────────────────────────────────────────────────
def model_view(platform: Path) -> dict:
    repo = _repo(platform)
    bundle = _read_json(repo / "portal" / "agent_ide_bundle.json") or {}
    agents = bundle.get("agents") or {}
    known = []
    try:
        sys.path.insert(0, str(platform))
        from engine import deepseek  # noqa: WPS433
        known = list(getattr(deepseek, "KNOWN_MODELS", []) or [])
    except Exception:  # noqa: BLE001
        known = []
    rows = []
    for aid in ("ZHAOYUN", "R2"):
        c = agents.get(aid) or {}
        rows.append({"seat": aid, "cn_name": c.get("cn_name") or "",
                     "llm": c.get("llm") or "",
                     "role": c.get("role_cn") or "",
                     "in_headcount": aid != "ZHAOYUN"})
    return {"ok": True, "generated_at": _now(), "seats": rows,
            "known_models": known,
            "candidate_models": ["deepseek/deepseek-flash",
                                 "deepseek/deepseek-v4-pro",
                                 "ollama/qwen3.5:9b"],
            "note": "模型信息只读；切换模型须提交申请→老田审批→改 org/bundle 真源"}


# ── 请求（调整动作统一走这里）─────────────────────────────────────────────
def requests_view(platform: Path) -> dict:
    d = Path(platform) / "data" / "zhaoyun_requests"
    items = []
    if d.is_dir():
        for p in sorted(d.glob("ZYREQ-*.json"), reverse=True)[:100]:
            r = _read_json(p)
            if isinstance(r, dict):
                items.append({"id": r.get("id"), "kind": r.get("kind"),
                              "status": r.get("status"), "reason": r.get("reason"),
                              "created_at": r.get("created_at")})
    return {"ok": True, "pending": sum(1 for x in items if x.get("status") == "pending_human"),
            "items": items}


def write_request(platform: Path, kind: str, payload: dict, reason: str = "",
                  requested_by: str = "ZHAOYUN") -> dict:
    if kind not in REQUESTS_KIND:
        return {"ok": False, "error": "bad_kind", "allowed": list(REQUESTS_KIND)}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "bad_payload"}
    d = Path(platform) / "data" / "zhaoyun_requests"
    d.mkdir(parents=True, exist_ok=True)
    rid = "ZYREQ-%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), uuid.uuid4().hex[:6])
    rec = {"id": rid, "kind": kind, "status": "pending_human",
           "requested_by": requested_by, "reason": str(reason or "")[:400],
           "payload": payload, "created_at": _now(),
           "note": "只登记请求，不自动生效；须老田审批（approval_request 事件）"}
    (d / (rid + ".json")).write_text(json.dumps(rec, ensure_ascii=False, indent=1),
                                     encoding="utf-8")
    _emit(rid, kind, reason)
    return {"ok": True, "request": rec}


def _emit(rid: str, kind: str, reason: str) -> dict:
    try:
        repo = Path(__file__).resolve().parents[2]
        tools = str(repo / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import events  # noqa: WPS433
        return events.emit(kind="approval_request", task="ZHAOYUN", agent="ZHAOYUN",
                           detail=("治理请求 %s kind=%s reason=%s"
                                   % (rid, kind, str(reason or "")[:120]))[:300])
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__}
