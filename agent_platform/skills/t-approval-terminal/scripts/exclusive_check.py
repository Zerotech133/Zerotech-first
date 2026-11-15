# -*- coding: utf-8 -*-
"""verify: t-approval-terminal 独占性与焊点核验（只读，手写，不被 build_skills.py 覆盖）。

三组断言：
  A. 独占：本能力只挂在 ZHAOYUN 席位（赵云能力卡），任何其他 profile 的能力卡出现同名即失败；
  B. 在册：agent_platform/skills/registry.json 中该条 owner=ZHAOYUN 且唯一；
  C. 焊点：做T 决策端（t-engine）与 iPhone 审批执行端（terminal）及其焊点测试真实存在。

只读、不联网、不写盘；输出 JSON；退出码 0=全通过 / 1=有失败。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SKILL = "t-approval-terminal"
OWNER = "ZHAOYUN"
HERE = Path(__file__).resolve()


def _main_repo() -> Path:
    """worktree 内运行时也应针对主仓（部署态）。"""
    try:
        r = subprocess.run(
            ["git", "-C", str(HERE.parent), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if r.returncode == 0 and r.stdout.strip():
            common = Path(r.stdout.strip())
            if not common.is_absolute():
                common = (HERE.parent / common).resolve()
            return common.parent
    except Exception:  # noqa: BLE001
        pass
    p = HERE.parent
    while p.name and not (p / "agent_platform").is_dir():
        p = p.parent
    return p


REPO = _main_repo()
PROJECT = REPO.parent
PROFILES = Path(os.path.expanduser("~")) / ".hermes" / "profiles"


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def check_owner_seat(checks: list) -> bool:
    """A：只在 zhaoyun profile 的能力卡里出现。"""
    hits = []
    if PROFILES.is_dir():
        for prof in sorted(PROFILES.iterdir()):
            caps = _read_json(prof / "skills" / "CAPABILITIES.json")
            if not isinstance(caps, dict):
                continue
            names = {
                str(c.get("name"))
                for c in (caps.get("capabilities") or [])
                if isinstance(c, dict)
            }
            if SKILL in names:
                hits.append(prof.name)
    ok = hits == ["zhaoyun"]
    checks.append(
        {
            "check": "exclusive:%s" % OWNER,
            "ok": ok,
            "detail": "挂载席位=%s（期望仅 ['zhaoyun']）" % (hits or "无"),
        }
    )
    return ok


def check_registry(checks: list) -> bool:
    """B：注册表在册且 owner=ZHAOYUN，且同名条目唯一。"""
    reg = _read_json(REPO / "agent_platform" / "skills" / "registry.json") or {}
    rows = [s for s in (reg.get("skills") or []) if str(s.get("name")) == SKILL]
    one = len(rows) == 1
    owner_ok = bool(rows) and str(rows[0].get("owner")) == OWNER
    checks.append(
        {
            "check": "registry:%s" % SKILL,
            "ok": one and owner_ok,
            "detail": "条目数=%d owner=%s"
            % (len(rows), rows[0].get("owner") if rows else "missing"),
        }
    )
    return one and owner_ok


def check_seam(checks: list) -> bool:
    """C：做T 决策端 + iPhone 审批执行端 + 焊点测试三件套存在。"""
    targets = {
        "terminal_gateway": PROJECT / "terminal" / "gateway" / "app.py",
        "terminal_models": PROJECT / "terminal" / "gateway" / "models.py",
        "tengine_signal_api": PROJECT / "t-engine" / "tengine" / "output" / "signal_api.py",
        "seam_test": PROJECT / "integration" / "test_seam.py",
    }
    ok = True
    for name, p in targets.items():
        exists = p.exists()
        checks.append({"check": "seam:%s" % name, "ok": exists, "detail": str(p)})
        ok = ok and exists
    return ok


def main() -> int:
    checks = []
    results = [check_owner_seat(checks), check_registry(checks), check_seam(checks)]
    ok = all(results)
    print(
        json.dumps(
            {
                "ok": ok,
                "skill": SKILL,
                "owner": OWNER,
                "exclusive": results[0],
                "checks": checks,
            },
            ensure_ascii=False,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
