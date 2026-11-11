# -*- coding: utf-8 -*-
"""verify: governance-ai（只读；由 build_skills.py 生成，勿手改）"""

import json
import os
import subprocess
import sys
from pathlib import Path
import logging

HERE = Path(__file__).resolve()


def _main_repo():
    # 在 worktree 里运行时，verify 应针对主仓（部署态），而不是临时 worktree
    try:
        r = subprocess.run(
            ["git", "-C", str(HERE.parent), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
        if r.returncode == 0 and r.stdout.strip():
            common = Path(r.stdout.strip())
            if not common.is_absolute():
                common = (HERE.parent / common).resolve()
            return common.parent
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/skills/governance-ai/scripts/verify.py", exc)
    p = HERE.parent
    while p.name and not (p / "agent_platform").is_dir():
        p = p.parent
    return p


REPO = _main_repo()
PROJECT = REPO.parent


def resolve(p):
    p = str(p)
    if p.startswith("~"):
        return Path(p).expanduser()
    if p.startswith("_vault/") or p.startswith("deliverables/"):
        return PROJECT / p
    return REPO / p


def check_file(checks, name, path):
    p = resolve(path)
    checks.append({"check": "exists:" + name, "ok": p.exists(), "detail": str(p)})
    return p.exists()


def check_compile(checks, name, path):
    p = resolve(path)
    if not p.exists():
        checks.append({"check": "compile:" + name, "ok": False, "detail": "missing"})
        return False
    try:
        compile(p.read_text(encoding="utf-8", errors="replace"), str(p), "exec")
        checks.append({"check": "compile:" + name, "ok": True, "detail": ""})
        return True
    except Exception as e:  # noqa: BLE001
        checks.append(
            {"check": "compile:" + name, "ok": False, "detail": repr(e)[:160]}
        )
        return False


def check_dir(checks, name, path):
    p = resolve(path)
    checks.append({"check": "dir:" + name, "ok": p.is_dir(), "detail": str(p)})
    return p.is_dir()


def check_run(checks, name, argv):
    argv = [sys.executable if a == "python" else a for a in argv]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=90)
        ok = r.returncode == 0
        checks.append(
            {
                "check": "run:" + name,
                "ok": ok,
                "detail": ((r.stderr or r.stdout or "")[:120]),
            }
        )
        return ok
    except Exception as e:  # noqa: BLE001
        checks.append({"check": "run:" + name, "ok": False, "detail": repr(e)[:120]})
        return False


def check_key(checks, name, path):
    p = resolve(path)
    ok = p.exists() or bool(os.environ.get("DEEPSEEK_API_KEY"))
    checks.append({"check": "key:" + name, "ok": ok, "detail": str(p)})
    return ok


def main():
    checks = []
    ok = True
    ok = (
        check_file(
            checks, "agent_platform/tools/gov_ai.py", "agent_platform/tools/gov_ai.py"
        )
        and ok
    )
    ok = (
        check_compile(
            checks, "agent_platform/tools/gov_ai.py", "agent_platform/tools/gov_ai.py"
        )
        and ok
    )
    print(
        json.dumps(
            {"ok": ok, "skill": "governance-ai", "checks": checks}, ensure_ascii=False
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
