# -*- coding: utf-8 -*-
"""赵云 · 治理席前端视图聚合（只读，供 8796 `/api/zhaoyun`）。

数据源（全部只读，不写任何真源）：
  · 身份/定位：本模块常量（与 docs/legion 身份对照表一致）
  · 能力卡：<profile>/skills/CAPABILITIES.json
  · 判例库：engine.experience_store.public_summary（默认 Z4 私有库；可传 root 供测试）
  · 治理 AI 建议：<platform>/data/governance_ai/advice/<date>/{anomaly_digest,priority_advice,case_draft}.json
  · 时钟：<platform>/data/governance_clock_state.json + governance_clock.lock
  · 保活：C:\\Temp\\governance_keepalive.log 最后一行（读不到则留空）

失败关闭：单个数据源读不到 → 对应字段为空 + note，不影响其余；整体异常由 serve 返回 500。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import Optional

IDENTITY = {
    "name": "赵云",
    "seat": "ZHAOYUN（治理席）",
    "role": "治理面：扫描/收口/健康卡/AI 归纳建议/判例沉淀",
    "compilation": "profile 角色，不占 org.json 编制（D5-C）",
    "boundary": "只产建议与证据；不裁决、不自动执行、不持私钥",
    "r2_note": "R2（风控席）是 org 编制成员，与 ZHAOYUN 是两个 id，报表按 id 区分",
}


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _mtime_iso(path: Path) -> str:
    try:
        return dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone() \
            .isoformat(timespec="seconds")
    except OSError:
        return ""


def _capabilities(profile: Path) -> dict:
    data = _read_json(profile / "skills" / "CAPABILITIES.json") or {}
    caps = data.get("capabilities") or []
    rows = [{"name": str(c.get("name")), "what": str(c.get("what") or ""),
             "boundary": str(c.get("boundary") or ""),
             "status": str(c.get("status") or "unknown")}
            for c in caps if isinstance(c, dict)]
    return {"total": int(data.get("total") or len(rows)),
            "ready": int(data.get("ready") or sum(1 for r in rows if r["status"] == "ready")),
            "pending_key": int(data.get("pending_key") or 0),
            "items": rows}


def _experience(experience_root: Optional[Path]) -> dict:
    try:
        from engine import experience_store as S
        summary = S.public_summary(root=experience_root)
    except Exception as exc:  # noqa: BLE001 —— 只读视图 fail-soft
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
                "entries_total": 0, "verified_total": 0, "recent": []}
    return {"ok": True, **summary}


def _latest_advice(platform: Path, now: float) -> dict:
    root = platform / "data" / "governance_ai" / "advice"
    actions = ("anomaly_digest", "priority_advice", "case_draft")
    out: dict = {}
    for action in actions:
        cands = sorted(root.glob("*/%s.json" % action),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not cands:
            out[action] = {"exists": False, "items": []}
            continue
        path = cands[0]
        data = _read_json(path) or {}
        items = [{"id": it.get("id"), "title": str(it.get("title") or it.get("cue") or "")[:60],
                  "confidence": it.get("confidence"), "evidence": (it.get("evidence") or [])[:3]}
                 for it in (data.get("items") or []) if isinstance(it, dict)]
        out[action] = {
            "exists": True, "status": data.get("status"),
            "generated_at": data.get("generated_at"),
            "input_digest": str(data.get("input_digest") or "")[:12],
            "model": data.get("model"), "items": items,
            "validator": data.get("validator") or {},
            "path": str(path), "updated_at": _mtime_iso(path),
        }
    return out


def _clock(platform: Path) -> dict:
    lock = _read_json(platform / "data" / "governance_clock.lock")
    state = _read_json(platform / "data" / "governance_clock_state.json") or {}
    pid = lock.get("pid") if isinstance(lock, dict) else None
    alive = False
    if pid:
        try:
            os.kill(int(pid), 0)          # 只探测存在，不发信号（Windows 上 os.kill(pid,0) 亦可用）
            alive = True
        except (OSError, ValueError):
            alive = False
    anchors = None
    try:
        import sys
        ops = str(platform.parent / "ops")
        if ops not in sys.path:
            sys.path.insert(0, ops)
        import governance_clock as GC  # noqa: WPS433
        anchors = list(GC.parse_anchors(GC.DEFAULT_ANCHORS))
    except Exception:  # noqa: BLE001
        anchors = None
    return {"pid": pid, "alive": alive,
            "started_at": (lock or {}).get("started_at"),
            "last_tick": state.get("last_tick_iso"),
            "fired_today": [k for k in (state.get("fired") or {}) if k],
            "anchors": anchors}


def _keepalive() -> dict:
    log = Path(r"C:\Temp\governance_keepalive.log")
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return {"available": False, "last": ""}
    return {"available": True, "last": (lines[-1] if lines else "")[:200],
            "lines": len(lines)}


def build_view(platform: Path, profile: Optional[Path] = None,
               experience_root: Optional[Path] = None,
               now: Optional[float] = None) -> dict:
    """构建赵云页只读视图（可测试：platform/profile/experience_root 均可注入）。"""
    platform = Path(platform)
    profile = Path(profile) if profile else Path.home() / ".hermes" / "profiles" / "zhaoyun"
    now_ts = float(now if now is not None else time.time())
    caps = _capabilities(profile)
    exp = _experience(experience_root)
    advice = _latest_advice(platform, now_ts)
    clock = _clock(platform)
    keepalive = _keepalive()
    return {
        "ok": True,
        "generated_at": dt.datetime.fromtimestamp(now_ts).astimezone()
                        .isoformat(timespec="seconds"),
        "identity": IDENTITY,
        "capabilities": caps,
        "experience": exp,
        "advice": advice,
        "clock": clock,
        "keepalive": keepalive,
        "sign_chain": {
            "desc": "AI draft → 待签 payload → 本人签（Ed25519）→ 公钥验签 → Z4 supervision",
            "private_key_holder": "老田（Agent 不持私钥、不代签）",
            "strict_env": "EVOLVE_PRINCIPAL_PUBKEY_FILE + EVOLVE_SIGNATURE_STRICT=1",
            "verified_total": exp.get("verified_total", 0),
            "entries_total": exp.get("entries_total", 0),
        },
        "note": "只读视图；AI 建议均为 draft，需人评审/人签；不写任何真源",
    }
