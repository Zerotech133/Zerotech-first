# -*- coding: utf-8 -*-
"""B5：clean clone/worktree 缺 IDE bundle 时的幂等 bootstrap（K-ORG-20261031-BUNDLEB5）。

背景
----
Phase2-B（K-ORG-20261031-BUNDLEP2B）已把 `portal/agent_ide_bundle.json` 去 tracked +
`.gitignore`：干净 clone/worktree **不会带**该生成物，而 `/ide` 页数据源、8103 独立 IDE
都依赖它。本工具负责"缺失时生成"，让干净环境自愈。

行为
----
- **幂等**：bundle 已存在且非空 → 一律不动（不重生成，避免覆盖本地新生成态）；
- **缺失才生成**：调用 `tools/build_agent_ide_bundle.py`（唯一的烘制真源），
  成功后可选复制到 `_vault/legion_ide/agent_ide_bundle.json` 部署副本；
- **失败可见**：生成器缺失/非零退出/产物仍缺失 → ``ok=False`` + error；
  部署副本复制失败 → ``ok=True`` + warning（repo 文件已够 8103/8734 兜底）；
- **不提交 git**：从不 `git add/commit`；不做任何索引/历史变更；
- **dry-run / --check**：只检测并报告将执行的动作，不写盘。

用法
----
    python -X utf8 tools/bundle_bootstrap.py --check            # 只检测：缺失 → exit 3
    python -X utf8 tools/bundle_bootstrap.py --dry-run --json   # 报告"将生成"，不写盘
    python -X utf8 tools/bundle_bootstrap.py                    # 缺失则生成 + 刷 _vault 副本
    python -X utf8 tools/bundle_bootstrap.py --no-deploy        # 只生成 repo 文件

集成：`tools/test_gate.ps1`（套件前）与 `ops/start_legion_ide.ps1`（启动前）调用。
边界：只写 repo 内 bundle 与显式 deploy 路径；测试用 `--deploy-path`/env 注入 tmp。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCHEMA = "bundle_bootstrap/v1"
BUNDLE_REL = Path("portal") / "agent_ide_bundle.json"
GENERATOR_REL = Path("tools") / "build_agent_ide_bundle.py"
DEPLOY_DEFAULT = Path(r"C:\项目\_vault\legion_ide\agent_ide_bundle.json")
DEPLOY_ENV = "DEVTOOL_IDE_BUNDLE_DEPLOY"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _sha256(p: Path) -> str:
    try:
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _file_info(p: Path) -> dict:
    if not p.is_file():
        return {"path": str(p), "exists": False, "bytes": 0, "sha256": ""}
    return {"path": str(p), "exists": True, "bytes": p.stat().st_size, "sha256": _sha256(p)}


def deploy_target(deploy_path: Path | str | None = None) -> Path:
    if deploy_path:
        return Path(deploy_path)
    return Path(os.environ.get(DEPLOY_ENV) or DEPLOY_DEFAULT)


def status(repo: Path | str = None, deploy_path: Path | str | None = None,
           generator: Path | str | None = None) -> dict:
    """只读状态：bundle / 生成器 / 部署副本。"""
    root = Path(repo) if repo else repo_root()
    gen = Path(generator) if generator else root / GENERATOR_REL
    return {
        "schema": SCHEMA + "/status",
        "repo": str(root),
        "bundle": _file_info(root / BUNDLE_REL),
        "generator": {"path": str(gen), "exists": gen.is_file()},
        "deploy": _file_info(deploy_target(deploy_path)),
    }


def bootstrap(repo: Path | str = None, *, dry_run: bool = False, deploy: bool = True,
              deploy_path: Path | str | None = None,
              generator: Path | str | None = None,
              runner=None, timeout: int = 180, quiet: bool = False) -> dict:
    """缺失时生成 bundle（幂等）；返回结构化结果，绝不 git add/commit。"""
    root = Path(repo) if repo else repo_root()
    bundle = root / BUNDLE_REL
    gen = Path(generator) if generator else root / GENERATOR_REL
    dep_path = deploy_target(deploy_path)
    out = {
        "schema": SCHEMA,
        "repo": str(root),
        "dry_run": bool(dry_run),
        "action": "",
        "ok": False,
        "generated": False,
        "bundle": None,
        "deploy": None,
        "generator": {"path": str(gen), "exists": gen.is_file()},
        "commands": [],
        "warnings": [],
        "error": "",
    }

    # 1) 已存在 → 幂等直接返回（不重生成、不覆盖）
    if bundle.is_file() and bundle.stat().st_size > 0:
        out.update(action="present", ok=True, bundle=_file_info(bundle))
        out["deploy"] = _file_info(dep_path)
        if deploy and not out["deploy"]["exists"]:
            out["warnings"].append("deploy_copy_missing（repo 文件在位，8103/8734 有兜底）")
        return out

    # 2) dry-run / check：不写盘
    if dry_run:
        out.update(action="would_generate", ok=True, bundle=_file_info(bundle))
        out["deploy"] = _file_info(dep_path)
        out["commands"] = [str(Path(sys.executable)), "-X", "utf8", str(gen)]
        return out

    # 3) 生成器缺失 → 失败可见
    if not gen.is_file():
        out.update(action="failed", error="generator_missing: %s" % gen)
        return out

    # 4) 调生成器（唯一烘制真源；cwd=repo 以解析相对路径）
    cmd = [str(Path(sys.executable)), "-X", "utf8", str(gen)]
    out["commands"].append(cmd)
    run = runner or subprocess.run
    try:
        r = run(cmd, cwd=str(root), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        out.update(action="failed", error="build_exception: %s: %s" % (type(exc).__name__, exc))
        return out
    rc = getattr(r, "returncode", 1)
    if rc != 0:
        tail = ((getattr(r, "stderr", "") or getattr(r, "stdout", "") or "").strip())[:200]
        out.update(action="failed", error="build_failed rc=%s: %s" % (rc, tail))
        return out
    if not (bundle.is_file() and bundle.stat().st_size > 0):
        out.update(action="failed", error="artifact_missing_after_build: %s" % bundle)
        return out

    out.update(action="generated", ok=True, generated=True, bundle=_file_info(bundle))

    # 5) 部署副本（失败只告警：repo 文件已够 8103/8734 兜底）
    if deploy:
        try:
            dep_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(bundle), str(dep_path))
            out["deploy"] = _file_info(dep_path)
        except OSError as exc:
            out["deploy"] = _file_info(dep_path)
            out["warnings"].append("deploy_copy_failed: %s: %s" % (type(exc).__name__, exc))
    else:
        out["deploy"] = _file_info(dep_path)

    if not quiet:
        out["stdout_tail"] = ((getattr(r, "stdout", "") or "").strip())[-300:]
    return out


def render_human(res: dict) -> str:
    b = res.get("bundle") or {}
    lines = ["== bundle bootstrap（Phase2-B B5；不提交 git）==",
             "repo: %s" % res.get("repo", ""),
             "bundle: exists=%s bytes=%s" % (b.get("exists"), b.get("bytes"))]
    if res.get("action"):
        lines.append("action: %s  ok=%s" % (res["action"], res["ok"]))
    if res.get("error"):
        lines.append("error: %s" % res["error"])
    for w in res.get("warnings") or []:
        lines.append("WARN: %s" % w)
    if res.get("dry_run"):
        lines.append("dry-run：未写盘")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="clean-clone IDE bundle 幂等 bootstrap（B5；不提交 git）")
    ap.add_argument("--repo", default=str(repo_root()))
    ap.add_argument("--check", action="store_true", help="只检测：缺失 exit 3")
    ap.add_argument("--dry-run", action="store_true", help="报告将生成，不写盘")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--no-deploy", action="store_true", help="只生成 repo 文件，不刷 _vault 副本")
    ap.add_argument("--deploy-path", default="")
    ap.add_argument("--generator", default="")
    args = ap.parse_args(argv)
    kw = dict(repo=args.repo, deploy_path=args.deploy_path or None,
              generator=args.generator or None)

    if args.check:
        res = status(**kw)
        present = bool(res["bundle"]["exists"])
        res["action"] = "present" if present else "missing"
        res["ok"] = present
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=1))
        else:
            print(render_human(res))
        return 0 if present else 3

    res = bootstrap(dry_run=args.dry_run, deploy=not args.no_deploy,
                    timeout=180, **kw)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(render_human(res))
    if res["ok"]:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
