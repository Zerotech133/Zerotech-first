#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""组合 Q2 rollback 静态 dry-run（WO-Q2-CLOSE-20261104）。

只做**静态**检查并输出演练证据模板；**绝不执行回滚**、不改服务、不写生产数据。
检查项（不通过 → blocking，rc=1）：
  1. manifest 可读/可解析（YAML）；
  2. `status.rollback.steps` 存在且 >= 4 步（Q2 回滚字段口径）；
  3. 步骤含可执行锚点（`git revert` + 验证命令 `composition.py`/`validate`）；
  4. `last_drilled` 字段存在（pending 允许，只记录当前值）；
  5. 目标声明提交可达（git log manifest/registry → cat-file 校验）；
  6. `composition.py validate <composition>` 可跑且 rc=0（--skip-validate 可跳过）；
  7. worktree 脏文件状态记录（info，不阻断）。

`--execute` 恒拒绝（rc=2）：真实演练需 D1+Lead 批准后另单执行，本工具不代做。

用法：
  python -X utf8 tools/q2_rollback_dryrun.py --composition lusu-d1-flow --json
  python -X utf8 tools/q2_rollback_dryrun.py --manifest <path> --skip-validate --json
"""
from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except Exception:  # noqa: BLE001
    yaml = None

REPO = Path(__file__).resolve().parents[1]
COMPOSITIONS_DIR = REPO / "compositions"
DEFAULT_REGISTRY = COMPOSITIONS_DIR / "registry.json"

SCHEMA = "zerobound/q2_rollback_dryrun/v1"
EVIDENCE_SCHEMA = "zerobound/q2_rollback_drill_evidence/v1"
MIN_STEPS = 4


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _git(repo, *args):
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except (OSError, subprocess.TimeoutExpired) as exc:  # noqa: BLE001
        return -1, "", "%s: %s" % (type(exc).__name__, exc)


def _load_manifest(path):
    if yaml is None:
        return None, "缺少 PyYAML，无法解析 manifest"
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return None, "YAML 解析失败：%s" % exc
    if not isinstance(data, dict):
        return None, "manifest 顶层不是对象"
    return data, ""


def _check(name, ok, detail, **extra):
    item = {"name": name, "ok": ok, "detail": str(detail)}
    item.update(extra)
    return item


def run_dry(composition="lusu-d1-flow", manifest_path=None, registry_path=None,
            repo=None, skip_validate=False):
    """静态 dry-run：返回报告 dict；不修改任何文件/不执行回滚。"""
    repo = Path(repo or REPO).resolve()
    manifest_path = Path(manifest_path) if manifest_path else (
        COMPOSITIONS_DIR / composition / "manifest.yaml"
    )
    registry_path = Path(registry_path) if registry_path else DEFAULT_REGISTRY
    checks, blocking = [], []

    def add(name, ok, detail, **extra):
        item = _check(name, ok, detail, **extra)
        checks.append(item)
        if ok is False:
            blocking.append(name)
        return item

    data, err = _load_manifest(manifest_path)
    add("manifest_parsed", bool(data) and not err,
        {"path": str(manifest_path), "error": err or None})
    add("registry_present", registry_path.is_file(), {"path": str(registry_path)})

    rollback = {}
    if isinstance(data, dict):
        status = data.get("status") if isinstance(data.get("status"), dict) else {}
        rollback = status.get("rollback") if isinstance(status.get("rollback"), dict) else {}
    steps = rollback.get("steps") if isinstance(rollback.get("steps"), list) else []
    add("rollback_steps", len(steps) >= MIN_STEPS,
        "steps=%d (require >=%d)" % (len(steps), MIN_STEPS))
    steps_text = "\n".join(str(s) for s in steps)
    add("rollback_commands",
        ("git revert" in steps_text) and (("composition.py" in steps_text) or ("validate" in steps_text)),
        "需要含 git revert + validate 锚点")
    add("last_drilled_recorded", "last_drilled" in rollback,
        "last_drilled=%r" % (rollback.get("last_drilled"),))

    # git 静态核验：目标声明提交可达（只读）
    rel_manifest = None
    rel_registry = None
    try:
        rel_manifest = str(manifest_path.resolve().relative_to(repo)).replace("\\", "/")
        rel_registry = str(registry_path.resolve().relative_to(repo)).replace("\\", "/")
    except ValueError:
        rel_manifest = rel_registry = None
    rc, out, git_err = _git(repo, "rev-parse", "--is-inside-work-tree")
    if rc == 0 and out == "true" and rel_manifest:
        rc, out, _ = _git(repo, "log", "-n", "5", "--format=%H", "--",
                          rel_manifest, rel_registry or rel_manifest)
        commits = [line.strip() for line in out.splitlines() if line.strip()]
        reachable = []
        for sha in commits:
            rrc, _, _ = _git(repo, "cat-file", "-e", sha + "^{commit}")
            reachable.append({"commit": sha, "reachable": rrc == 0})
        add("target_commits", bool(reachable) and all(c["reachable"] for c in reachable),
            {"commits": reachable})
        rc, status_out, _ = _git(repo, "status", "--porcelain", "--",
                                rel_manifest, rel_registry or rel_manifest)
        dirty = [line for line in status_out.splitlines() if line.strip()]
        add("worktree_state", None,
            {"dirty_declaration_files": dirty,
             "note": "info only：脏文件不阻断 dry-run，但真实回滚前应 clean"})
    else:
        add("target_commits", None,
            "skipped: repo=%s 非 git 工作树或路径不在仓库内（err=%s）"
            % (repo, git_err or "not-a-git-repo"))

    if skip_validate:
        add("composition_validate", None, "skipped by --skip-validate")
    else:
        tools = repo / "tools" / "composition.py"
        if not tools.is_file():
            add("composition_validate", False, "缺少 %s，无法跑 validate" % tools)
        else:
            try:
                r = subprocess.run(
                    [sys.executable, "-X", "utf8", str(tools), "validate",
                     str(manifest_path.parent)],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=120, cwd=str(repo),
                )
                tail = ((r.stdout or r.stderr or "").strip().splitlines() or [""])[-1]
                add("composition_validate", r.returncode == 0,
                    {"rc": r.returncode, "tail": tail[:200]})
            except (OSError, subprocess.TimeoutExpired) as exc:  # noqa: BLE001
                add("composition_validate", False,
                    "validate 执行失败：%s" % type(exc).__name__)

    planned_commands = [
        "git revert %s  # 或按 status.rollback 步 1 精确 checkout 声明文件"
        % ("<declaration-commit>",),
        "python -X utf8 tools/composition.py validate compositions/%s" % composition,
        "python -X utf8 tools/q2_probe.py --composition %s --json" % composition,
        "python -m pytest portal/tests/test_api_d1_*.py -q",
    ]
    evidence_template = {
        "schema": EVIDENCE_SCHEMA,
        "composition": composition,
        "dry_run_checked_at": _now(),
        "mode": "static-dry-run",
        "executor": "",
        "approved_by": "",
        "approval_ref": "",
        "started_at": "",
        "finished_at": "",
        "steps_done": [{"step": i + 1, "ok": None, "evidence": ""}
                       for i in range(len(steps))],
        "post_check": {"probe_ok": None, "validate_rc": None,
                       "d1_unit_tests": None},
        "rollback_triggered": False,
        "production_rollback_executed": False,
        "notes": "仅演练证据模板；真实演练须 D1+Lead 批准并回填（本工具不执行回滚）",
    }
    return {
        "schema": SCHEMA,
        "composition": composition,
        "checked_at": _now(),
        "dry_run": True,
        "executed": False,
        "mode": "static",
        "manifest": str(manifest_path),
        "registry": str(registry_path),
        "rollback": {"steps": steps, "last_drilled": rollback.get("last_drilled")},
        "checks": checks,
        "blocking": blocking,
        "ok": not blocking,
        "planned_commands": planned_commands,
        "evidence_template": evidence_template,
        "residuals": ["真实回滚演练未执行（pending；需 D1+Lead 批准，见 Q2 证据文档 R6）"],
    }


def render_markdown(rep):
    lines = [
        "# Q2 rollback 静态 dry-run（不执行回滚）",
        "",
        "- 组合：%s｜检查时间：%s｜ok：%s"
        % (rep["composition"], rep["checked_at"], rep["ok"]),
        "- dry_run=%s executed=%s mode=%s"
        % (rep["dry_run"], rep["executed"], rep["mode"]),
        "",
        "## 检查项",
        "",
        "| 检查 | ok | 详情 |",
        "|---|---|---|",
    ]
    for c in rep["checks"]:
        lines.append("| %s | %s | %s |" % (c["name"], c["ok"], str(c["detail"])[:160]))
    lines += ["", "## 回滚步骤（来自 manifest status.rollback）", ""]
    for i, step in enumerate(rep["rollback"]["steps"], 1):
        lines.append("%d. %s" % (i, step))
    lines += ["", "## 残余", ""] + ["- " + r for r in rep["residuals"]]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="组合 Q2 rollback 静态 dry-run（不执行回滚）")
    ap.add_argument("--composition", default="lusu-d1-flow")
    ap.add_argument("--manifest", default="")
    ap.add_argument("--registry", default="")
    ap.add_argument("--repo", default="")
    ap.add_argument("--skip-validate", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--execute", action="store_true",
                    help="恒拒绝：本工具只做静态 dry-run，不执行真实回滚")
    a = ap.parse_args(argv)
    if yaml is None:
        print("rollback_dryrun_error: 缺少 PyYAML，无法解析 manifest", file=sys.stderr)
        return 2
    if a.execute:
        print("rollback_dryrun_error: 拒绝执行——本工具仅静态 dry-run，不执行回滚"
              "（真实演练需 D1+Lead 批准并另单执行）", file=sys.stderr)
        return 2
    rep = run_dry(composition=a.composition,
                  manifest_path=a.manifest or None,
                  registry_path=a.registry or None,
                  repo=a.repo or None,
                  skip_validate=a.skip_validate)
    text = json.dumps(rep, ensure_ascii=False, indent=2)
    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    if a.markdown:
        print(render_markdown(rep))
    elif a.json:
        print(text)
    else:
        print("composition=%s ok=%s blocking=%s steps=%d last_drilled=%r"
              % (rep["composition"], rep["ok"], rep["blocking"],
                 len(rep["rollback"]["steps"]), rep["rollback"]["last_drilled"]))
        for c in rep["checks"]:
            print("  %-24s ok=%-5s %s" % (c["name"], c["ok"], str(c["detail"])[:120]))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
