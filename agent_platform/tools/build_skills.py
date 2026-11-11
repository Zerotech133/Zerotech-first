# -*- coding: utf-8 -*-
"""生成赵云 Agent Skills（14 能力卡 → SKILL.md + scripts/verify.py）。

用法：
    python agent_platform/tools/build_skills.py            # 生成/刷新 agent_platform/skills/
    python agent_platform/tools/build_skills.py --check    # 只检查是否与源一致（CI 用）

设计：
  · 源数据 = ~/.hermes/profiles/zhaoyun/skills/CAPABILITIES.json（或 --caps 指定）；
  · 每个技能目录：SKILL.md（Agent Skills 规范 frontmatter）+ scripts/verify.py；
  · verify.py 只做**只读/低风险**检查（存在性、语法编译、只读命令、selftest），
    不做写入、不联网；任务型探针用只读查询替代；
  · 生成 registry.json 作为索引（进 git，可评审）；运行态验证结果落 data/skills_verify.json。
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[1]
REPO = PLATFORM.parent
SKILLS = PLATFORM / "skills"
DEFAULT_CAPS = (
    Path.home() / ".hermes" / "profiles" / "zhaoyun" / "skills" / "CAPABILITIES.json"
)

SPECS = {
    "task-exit-audit": {
        "exists": ["agent_platform/tools/task_exit_audit.py"],
        "compile": ["agent_platform/tools/task_exit_audit.py"],
    },
    "health-card": {
        "exists": ["tools/gen_health_card.py"],
        "compile": ["tools/gen_health_card.py"],
    },
    "task-launch-probe": {
        "run": [["schtasks", "/query", "/fo", "LIST"]],
        "note": "探针本身有建删任务副作用，verify 用 schtasks 只读查询替代",
    },
    "backup-bundles": {
        "exists": ["tools/backup_bundles.ps1"],
        "dirs": ["_vault/backups"],
    },
    "worktree-hygiene": {
        "dirs": [".worktrees"],
        "run": [["git", "worktree", "list"]],
    },
    "docs-archive": {
        "dirs": ["docs"],
    },
    "experience-write": {
        "dirs": ["~/.hermes/profiles/zhaoyun/experience"],
    },
    "mutual-review": {
        "exists": ["tools/devtask.py"],
        "dirs": ["deliverables"],
    },
    "lookahead-guard": {
        "exists": ["agent_platform/engine/lookahead_guard.py"],
        "compile": ["agent_platform/engine/lookahead_guard.py"],
        "run": [["python", "agent_platform/engine/lookahead_guard.py", "--selftest"]],
    },
    "council-hall": {
        "dirs": ["docs/legion"],
        "note": "只产建议/证据，verify 检查议会材料目录存在",
    },
    "harness-runner": {
        "exists": ["ops/governance_clock.py", "agent_platform/legion_command/serve.py"],
        "note": "执行体=DSH 会话；verify 检查调度器与命令台存在",
    },
    "llm-deepseek": {
        "exists": ["agent_platform/engine/deepseek.py"],
        "compile": ["agent_platform/engine/deepseek.py"],
        "key_check": ["_vault/secrets/deepseek_api_key.txt"],
    },
    "governance-ai": {
        "exists": ["agent_platform/tools/gov_ai.py"],
        "compile": ["agent_platform/tools/gov_ai.py"],
    },
    "case-sign-chain": {
        "exists": ["tools/gov_ai_case_bridge.py"],
        "dirs": ["_vault/sign_requests"],
    },
}

VERIFY_TEMPLATE = '''# -*- coding: utf-8 -*-
"""verify: {name}（只读；由 build_skills.py 生成，勿手改）"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()

def _main_repo():
    # 在 worktree 里运行时，verify 应针对主仓（部署态），而不是临时 worktree
    try:
        r = subprocess.run(["git", "-C", str(HERE.parent), "rev-parse",
                            "--git-common-dir"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=30)
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

def resolve(p):
    p = str(p)
    if p.startswith("~"):
        return Path(p).expanduser()
    if p.startswith("_vault/") or p.startswith("deliverables/"):
        return PROJECT / p
    return REPO / p

def check_file(checks, name, path):
    p = resolve(path)
    checks.append({{"check": "exists:" + name, "ok": p.exists(), "detail": str(p)}})
    return p.exists()

def check_compile(checks, name, path):
    p = resolve(path)
    if not p.exists():
        checks.append({{"check": "compile:" + name, "ok": False, "detail": "missing"}})
        return False
    try:
        compile(p.read_text(encoding="utf-8", errors="replace"), str(p), "exec")
        checks.append({{"check": "compile:" + name, "ok": True, "detail": ""}})
        return True
    except Exception as e:  # noqa: BLE001
        checks.append({{"check": "compile:" + name, "ok": False, "detail": repr(e)[:160]}})
        return False

def check_dir(checks, name, path):
    p = resolve(path)
    checks.append({{"check": "dir:" + name, "ok": p.is_dir(), "detail": str(p)}})
    return p.is_dir()

def check_run(checks, name, argv):
    argv = [sys.executable if a == "python" else a for a in argv]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=90)
        ok = r.returncode == 0
        checks.append({{"check": "run:" + name, "ok": ok,
                       "detail": ((r.stderr or r.stdout or "")[:120])}})
        return ok
    except Exception as e:  # noqa: BLE001
        checks.append({{"check": "run:" + name, "ok": False, "detail": repr(e)[:120]}})
        return False

def check_key(checks, name, path):
    p = resolve(path)
    ok = p.exists() or bool(os.environ.get("DEEPSEEK_API_KEY"))
    checks.append({{"check": "key:" + name, "ok": ok, "detail": str(p)}})
    return ok

def main():
    checks = []
    ok = True
{body}
    print(json.dumps({{"ok": ok, "skill": "{name}", "checks": checks}}, ensure_ascii=False))
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
'''


def _body(spec: dict) -> str:
    out = []
    for p in spec.get("exists") or []:
        out.append("    ok = check_file(checks, %r, %r) and ok" % (p, p))
    for p in spec.get("compile") or []:
        out.append("    ok = check_compile(checks, %r, %r) and ok" % (p, p))
    for p in spec.get("dirs") or []:
        out.append("    ok = check_dir(checks, %r, %r) and ok" % (p, p))
    for argv in spec.get("run") or []:
        out.append(
            "    ok = check_run(checks, %r, %r) and ok" % (" ".join(argv), list(argv))
        )
    for p in spec.get("key_check") or []:
        out.append("    ok = check_key(checks, %r, %r) and ok" % (p, p))
    note = spec.get("note")
    if note:
        out.append(
            "    checks.append({'check': 'note', 'ok': True, 'detail': %r})" % note
        )
    if not out:
        out.append(
            "    checks.append({'check': 'manual', 'ok': True, 'detail': '人工核验能力'})"
        )
    return "\n".join(out)


def _frontmatter(cap: dict) -> str:
    meta = {
        "owner": "ZHAOYUN",
        "version": "1.0.0",
        "risk": "read-only",
        "legacy_capability": cap["name"],
        "verify": "scripts/verify.py",
    }
    lines = (
        [
            "---",
            "name: %s" % cap["name"],
            "description: %s"
            % json.dumps(str(cap.get("what") or "")[:1000], ensure_ascii=False),
            "license: internal",
            "compatibility: Windows, Python 3.12, local-only, no-network-verify",
            "metadata:",
        ]
        + ["  %s: %s" % (k, json.dumps(v, ensure_ascii=False)) for k, v in meta.items()]
        + [
            "allowed-tools: [read-file, run-python]",
            "---",
            "",
        ]
    )
    return "\n".join(lines)


def _skill_md(cap: dict, spec: dict) -> str:
    parts = [
        _frontmatter(cap),
        "# %s" % cap["name"],
        "",
        "## 是什么",
        str(cap.get("what") or ""),
        "",
        "## 怎么跑",
        "```",
        str(cap.get("how") or ""),
        "```",
        "",
        "## 边界",
        str(cap.get("boundary") or ""),
        "",
        "## 验证",
        "```",
        "python agent_platform/skills/%s/scripts/verify.py" % cap["name"],
        "```",
        "",
    ]
    if spec.get("note"):
        parts += ["## 备注", spec["note"], ""]
    return "\n".join(parts) + "\n"


def build(caps_path: Path = DEFAULT_CAPS) -> dict:
    data = json.loads(caps_path.read_text(encoding="utf-8-sig"))
    caps = [c for c in data.get("capabilities") or [] if c.get("name")]
    SKILLS.mkdir(parents=True, exist_ok=True)
    index = []
    for cap in caps:
        name = str(cap["name"])
        spec = SPECS.get(name) or {"note": "未登记 verify 规格，默认存在性检查"}
        d = SKILLS / name
        (d / "scripts").mkdir(parents=True, exist_ok=True)
        (d / "references").mkdir(exist_ok=True)
        (d / "SKILL.md").write_text(_skill_md(cap, spec), encoding="utf-8")
        (d / "scripts" / "verify.py").write_text(
            VERIFY_TEMPLATE.format(name=name, body=_body(spec)), encoding="utf-8"
        )
        (d / "references" / "README.md").write_text(
            "# %s 参考材料\n\n- 能力卡源：`~/.hermes/profiles/zhaoyun/skills/CAPABILITIES.json`\n"
            "- 状态：%s\n- 边界：%s\n" % (name, cap.get("status"), cap.get("boundary")),
            encoding="utf-8",
        )
        index.append(
            {
                "name": name,
                "path": "agent_platform/skills/%s/SKILL.md" % name,
                "version": "1.0.0",
                "status": str(cap.get("status") or "unknown"),
                "risk": "read-only",
                "owner": "ZHAOYUN",
                "allowed_tools": ["read-file", "run-python"],
                "verify_cmd": "python agent_platform/skills/%s/scripts/verify.py"
                % name,
                "legacy_capability": name,
                "manual": bool(
                    spec.get("note")
                    and not (
                        spec.get("exists")
                        or spec.get("run")
                        or spec.get("dirs")
                        or spec.get("compile")
                        or spec.get("key_check")
                    )
                ),
            }
        )
    registry = {
        "schema": "zhaoyun_skills/v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": str(caps_path),
        "total": len(index),
        "skills": index,
        "note": "Agent Skills 规范：name/description/allowed-tools/scripts；verify 只读",
    }
    (SKILLS / "registry.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return registry


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成赵云 Agent Skills")
    ap.add_argument("--caps", default=str(DEFAULT_CAPS))
    ap.add_argument("--check", action="store_true", help="只检查 registry 与能力数一致")
    args = ap.parse_args(argv)
    if args.check:
        reg = json.loads((SKILLS / "registry.json").read_text(encoding="utf-8"))
        caps = json.loads(Path(args.caps).read_text(encoding="utf-8-sig"))
        want = len([c for c in caps.get("capabilities") or [] if c.get("name")])
        missing = [
            s["name"]
            for s in reg.get("skills") or []
            if not (SKILLS / s["name"] / "SKILL.md").exists()
        ]
        ok = (
            reg.get("total") == want
            and len(reg.get("skills") or []) == want
            and not missing
        )
        print(
            json.dumps(
                {"ok": ok, "total": want, "missing": missing}, ensure_ascii=False
            )
        )
        return 0 if ok else 1
    reg = build(Path(args.caps))
    print("生成 %d 个技能 → %s" % (reg["total"], SKILLS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
