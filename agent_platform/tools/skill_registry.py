# -*- coding: utf-8 -*-
"""技能注册表：加载/校验/执行 verify（只读）+ 运行态结果持久化。

CLI:
  python agent_platform/tools/skill_registry.py --json          # 列出技能+最近验证
  python agent_platform/tools/skill_registry.py --verify-all    # 跑全部 verify
  python agent_platform/tools/skill_registry.py --verify NAME   # 单个
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[1]
REPO = PLATFORM.parent
SKILLS = PLATFORM / "skills"
REGISTRY = SKILLS / "registry.json"
STATE = PLATFORM / "data" / "skills_verify.json"
TIMEOUT = 120


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def load_registry() -> dict:
    reg = _read_json(REGISTRY) or {}
    if not reg.get("skills"):
        return {
            "schema": "zhaoyun_skills/v1",
            "total": 0,
            "skills": [],
            "error": "registry_missing",
        }
    return reg


def state() -> dict:
    return _read_json(STATE) or {
        "schema": "zhaoyun_skill_verify/v1",
        "updated_at": "",
        "items": {},
    }


def _save_state(items: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(
        json.dumps(
            {
                "schema": "zhaoyun_skill_verify/v1",
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "items": items,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )


def get_skill(name: str) -> dict | None:
    for s in load_registry().get("skills") or []:
        if s.get("name") == name:
            return s
    return None


def _verify_script(name: str) -> Path:
    """只允许 skills/<name>/scripts/verify.py（防路径穿越/任意脚本）。"""
    if not name or "/" in name or "\\" in name or ".." in name:
        raise ValueError("bad_name")
    p = (SKILLS / name / "scripts" / "verify.py").resolve()
    base = SKILLS.resolve()
    if not str(p).startswith(str(base) + os.sep):
        raise ValueError("path_outside_skills")
    if not p.exists():
        raise FileNotFoundError(str(p))
    return p


def _clean_env() -> dict:
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper()
        not in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "HTTP_PROXY",
            "HTTPS_PROXY",
        )
    }
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["ZHAOYUN_VERIFY"] = "1"
    return env


def verify(name: str, timeout: int = TIMEOUT) -> dict:
    t0 = time.time()
    try:
        script = _verify_script(name)
        r = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_clean_env(),
        )
        ok = r.returncode == 0
        detail = ((r.stderr or r.stdout or "").strip().splitlines() or [""])[-1][:300]
    except subprocess.TimeoutExpired:
        ok, detail = False, "timeout after %ss" % timeout
    except (OSError, ValueError) as e:
        ok, detail = False, "%s: %s" % (type(e).__name__, str(e)[:160])
    rec = {
        "name": name,
        "ok": bool(ok),
        "detail": detail,
        "elapsed_ms": int((time.time() - t0) * 1000),
        "verified_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    items = state().get("items") or {}
    items[name] = rec
    _save_state(items)
    return rec


def verify_all(timeout: int = TIMEOUT) -> dict:
    reg = load_registry()
    results = [verify(s["name"], timeout) for s in reg.get("skills") or []]
    return {
        "ok": all(r["ok"] for r in results),
        "total": len(results),
        "failed": [r["name"] for r in results if not r["ok"]],
        "results": results,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def list_skills(with_state: bool = True) -> dict:
    reg = load_registry()
    st = (state().get("items") or {}) if with_state else {}
    items = []
    for s in reg.get("skills") or []:
        item = dict(s)
        item["last_verified"] = st.get(s["name"])
        items.append(item)
    return {
        "ok": bool(reg.get("skills")),
        "schema": reg.get("schema"),
        "total": len(items),
        "skills": items,
        "manual_count": sum(1 for s in items if s.get("manual")),
        "state_updated_at": state().get("updated_at"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="赵云技能注册表")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verify-all", action="store_true")
    ap.add_argument("--verify", default="")
    args = ap.parse_args(argv)
    if args.verify_all:
        rep = verify_all()
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0 if rep["ok"] else 1
    if args.verify:
        rep = verify(args.verify)
        print(json.dumps(rep, ensure_ascii=False))
        return 0 if rep["ok"] else 1
    print(json.dumps(list_skills(), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
