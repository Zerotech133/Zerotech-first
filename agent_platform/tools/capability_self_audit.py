# -*- coding: utf-8 -*-
"""能力自检（capability-self-audit）：核对 CAPABILITIES.json 与真实工具/新鲜度，漂移即报警。

检查项：
  1. 每条能力的 `how` 里引用的脚本/路径是否真实存在；
  2. status=ready 但引用脚本缺失 → drift（纸面能力）；
  3. CAPABILITIES.json 的 generated_at 超过 --max-age-hours → stale（能力卡过期）；
  4. 缺失/空能力名 → bad_entry。

只读；结果落 dev_events(patrol_verdict)；退出码 0 无漂移 / 1 有漂移 / 2 自身错误。
环境：CAPABILITY_AUDIT=off 跳过。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[1]
REPO = PLATFORM.parent
PROFILE = Path(os.path.expanduser("~")) / ".hermes" / "profiles" / "zhaoyun"
KILL_ENV = "CAPABILITY_AUDIT"
KILL_VALUES = ("off", "0", "false", "no")
MAX_AGE_HOURS = 48


def _registry_result():
    """优先用 Agent Skills 注册表做真实 verify；不可用时返回 None（回退旧逻辑）。"""
    try:
        sys.path.insert(0, str(PLATFORM / "tools"))
        from skill_registry import list_skills, verify_all  # noqa: WPS433
        rep = verify_all()
        drift = [{"kind": "verify_failed", "capability": r["name"],
                  "detail": str(r.get("detail") or "")[:160]}
                 for r in rep.get("results") or [] if not r.get("ok")]
        warnings = [{"kind": "manual", "capability": s.get("name"),
                     "detail": "需人工核验（无自动 verify）"}
                    for s in list_skills().get("skills") or [] if s.get("manual")]
        return {"drift": drift, "warnings": warnings,
                "total": rep.get("total", 0), "failed": rep.get("failed") or []}
    except Exception:  # noqa: BLE001
        return None


def kill_switch_on(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(KILL_ENV) or "").strip().lower() in KILL_VALUES


def _parse_age_hours(ts: str) -> float:
    try:
        t = dt.datetime.fromisoformat(str(ts))
    except ValueError:
        return -1.0
    if t.tzinfo is None:
        t = t.astimezone()
    return (dt.datetime.now().astimezone() - t).total_seconds() / 3600.0


def check(profile: Path | None = None, max_age_hours: float = MAX_AGE_HOURS,
          use_registry: bool | None = None) -> dict:
    prof = Path(profile) if profile else PROFILE
    if use_registry is None:
        try:
            use_registry = prof.resolve() == PROFILE.resolve()
        except OSError:
            use_registry = False
    path = prof / "skills" / "CAPABILITIES.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": "capabilities_unreadable",
                "detail": type(exc).__name__, "drift": []}
    caps = [c for c in (data.get("capabilities") or []) if isinstance(c, dict)]
    age = _parse_age_hours(data.get("generated_at") or "")
    stale = age < 0 or age > max_age_hours
    reg = _registry_result() if use_registry else None
    if reg is not None:
        drift = list(reg["drift"])
        warnings = list(reg["warnings"])
        if stale:
            drift.append({"kind": "stale", "generated_at": data.get("generated_at"),
                          "age_hours": round(age, 1) if age >= 0 else None,
                          "max_age_hours": max_age_hours,
                          "detail": "能力卡生成时间过旧或缺失"})
        return {"ok": not drift, "generated_at": data.get("generated_at") or "",
                "total": int(data.get("total") or len(caps)),
                "ready": int(data.get("ready") or 0),
                "stale": stale, "max_age_hours": max_age_hours,
                "drift": drift, "warnings": warnings, "source": "skills_registry",
                "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    drift = []
    warnings = []
    for c in caps:
        name = str(c.get("name") or "")
        how = str(c.get("how") or "")
        status = str(c.get("status") or "unknown")
        if not name:
            drift.append({"kind": "bad_entry", "detail": "能力名为空"})
            continue
        m = re.search(r"([A-Za-z0-9_./\\-]+\.(?:py|ps1|html))", how)
        if not m:
            if status == "ready":
                warnings.append({"kind": "no_ref", "capability": name,
                                 "detail": "ready 能力未给出可核验脚本路径（建议补 how）"})
            continue
        ref = m.group(1)
        cand = Path(ref) if os.path.isabs(ref) else (REPO / ref)
        if not cand.exists():
            alt = PLATFORM / ref
            if not alt.exists():
                drift.append({"kind": "missing_ref", "capability": name,
                              "ref": ref, "status": status,
                              "detail": "引用脚本不存在（纸面能力）"})
    age = _parse_age_hours(data.get("generated_at") or "")
    stale = age < 0 or age > max_age_hours
    if stale:
        drift.append({"kind": "stale", "generated_at": data.get("generated_at"),
                      "age_hours": round(age, 1) if age >= 0 else None,
                      "max_age_hours": max_age_hours,
                      "detail": "能力卡生成时间过旧或缺失"})
    return {"ok": not drift, "generated_at": data.get("generated_at") or "",
            "total": int(data.get("total") or len(caps)),
            "ready": int(data.get("ready") or 0),
            "stale": stale, "max_age_hours": max_age_hours,
            "drift": drift, "warnings": warnings,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def emit_audit(rep: dict) -> dict:
    try:
        sys.path.insert(0, str(REPO / "tools"))
        import events  # noqa: WPS433
        bad = len(rep.get("drift") or [])
        return events.emit(kind="patrol_verdict", task="ZHAOYUN", agent="ZHAOYUN",
                           detail=("capability_audit ok=%s drift=%d total=%d"
                                   % (rep.get("ok"), bad, rep.get("total", 0)))[:300])
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="能力自检：能力卡 vs 真实工具/新鲜度")
    ap.add_argument("--profile", default=str(PROFILE))
    ap.add_argument("--max-age-hours", type=float, default=MAX_AGE_HOURS)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if kill_switch_on():
        if not args.quiet:
            print(json.dumps({"ok": False, "skipped": "kill_switch"}, ensure_ascii=False))
        return 0
    try:
        rep = check(Path(args.profile), args.max_age_hours)
        rep["audit"] = emit_audit(rep)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": type(e).__name__,
                          "detail": str(e)[:200]}, ensure_ascii=False))
        return 2
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
    elif not args.quiet:
        print("能力自检 | total=%s ready=%s stale=%s drift=%d warnings=%d"
              % (rep.get("total"), rep.get("ready"), rep.get("stale"),
                 len(rep.get("drift") or []), len(rep.get("warnings") or [])))
        for d in rep.get("drift") or []:
            print("[漂移]", d.get("kind"), d.get("capability") or "", d.get("detail") or "")
        for w in rep.get("warnings") or []:
            print("[提示]", w.get("kind"), w.get("capability") or "", w.get("detail") or "")
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
