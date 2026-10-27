# -*- coding: utf-8 -*-
"""verify: 黄忠验收守门技能（只读；缺件 fail-visible）。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()


def _main_repo() -> Path:
    try:
        r = subprocess.run(
            ["git", "-C", str(HERE.parent), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            common = Path(r.stdout.strip())
            if not common.is_absolute():
                common = (HERE.parent / common).resolve()
            return common.parent
    except Exception:  # noqa: BLE001
        pass
    return HERE.parents[3]


def main() -> int:
    repo = _main_repo()
    required = {
        "acceptance_tool": repo / "tools" / "huangzhong_accept.py",
        "baseline_tool": repo / "tools" / "test_baseline.py",
        "quality_gate": repo / "agent_platform" / "tools" / "quality_gate.py",
        "redteam_tool": repo / "tools" / "zhuge_redteam.py",
        "skill_doc": repo / "agent_platform" / "skills" / "huangzhong" / "SKILL.md",
        "charter": repo / "docs" / "plans" / "2026-10-28_黄忠验收守门官_charter_v1.md",
        "skills_doc": repo / "docs" / "plans" / "2026-10-28_黄忠技能清单与共享资能_v1.md",
    }
    checks = {}
    for name, path in required.items():
        checks[name] = {"ok": path.is_file(), "path": str(path)}
    warnings = []
    baseline = repo / "agent_platform" / "data" / "test_baseline" / "latest.json"
    checks["baseline_evidence"] = {"ok": baseline.is_file(), "path": str(baseline)}
    reports = sorted((repo / "work" / "huangzhong").glob("acceptance_*.json"))
    checks["acceptance_report"] = {
        "ok": bool(reports),
        "count": len(reports),
        "latest": str(reports[-1]) if reports else "",
    }
    if reports:
        try:
            d = json.loads(reports[-1].read_text(encoding="utf-8"))
            ok = (d.get("mode") == "observe" and d.get("blocking") is False
                  and d.get("verdict") in ("PASS", "REJECT", "OBSERVE"))
            checks["report_schema"] = {"ok": ok,
                                       "verdict": d.get("verdict"),
                                       "blocking": d.get("blocking")}
            if not ok:
                warnings.append("最新验收单 schema 不符（mode/blocking/verdict）")
        except (OSError, ValueError):
            checks["report_schema"] = {"ok": False}
            warnings.append("最新验收单不可解析")
    else:
        warnings.append("尚无验收单（先跑 tools/huangzhong_accept.py）")
    hard = [k for k, v in checks.items() if not v.get("ok") and k != "baseline_evidence"]
    out = {"ok": not hard, "skill": "huangzhong", "repo": str(repo),
           "checks": checks, "warnings": warnings, "failed": hard,
           "note": "只读自检；不写文件、不联网"}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
