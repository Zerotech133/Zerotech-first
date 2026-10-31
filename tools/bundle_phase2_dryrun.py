# -*- coding: utf-8 -*-
"""K-ORG-20261031-BUNDLEP2：bundle Phase2（去 tracked）离线 dry-run 规划器。

背景
----
`portal/agent_ide_bundle.json` 是 `tools/build_agent_ide_bundle.py` 的生成物，当前仍被 git
tracked；每个 merge 后由 post-merge 钩子/`devtask.py` 自动重烘并提交 → 8 日窗口内
bundle 自动提交约 900/3928 ≈ 22.9%（W5 报告：852/3896=21.87%）。W5 Phase1（源变才提交）已落地；
Phase2 目标：**bundle 提交 0 增长**（去 tracked），但**需要一次受控窗口**。

Phase2-B 落地后（K-ORG-20261031-BUNDLEP2B）：本脚本额外报告 `implementation`
（B1 ignore / B2 untracked / B3 rebake 无 checkout / B4 devtask 不提交）与各步骤 `landed` 状态。

本脚本只做**离线只读分析 + 步骤规划**，绝不执行：
- 不 `git rm`/不改索引、不改 `.gitignore`、不改 tracked 文件；
- 不改 nginx/容器/挂载、不切生产、不重启服务、不写任何被分析文件；
- 输出 Phase2 步骤清单、消费方清单、回滚清单、L2 门禁状态（默认 blocked）。

用法
----
    python -X utf8 tools/bundle_phase2_dryrun.py                # 人读摘要
    python -X utf8 tools/bundle_phase2_dryrun.py --json         # 机器可读（唯一稳定契约）
    python -X utf8 tools/bundle_phase2_dryrun.py --json --l2-approved   # 仅标记，不执行
    python -X utf8 tools/bundle_phase2_dryrun.py --json --require-approved  # 未批准 → exit 2

边界：离线只读；docker 探测失败时降级为 unknown（不阻塞分析）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_DEFAULT = Path(__file__).resolve().parents[1]
BUNDLE_REL = "portal/agent_ide_bundle.json"
NGINX_REL = "portal/nginx.conf"
GITIGNORE_REL = ".gitignore"
REBAKE_REL = "ops/rebake_ide_bundle.ps1"
DEVTASK_REL = "tools/devtask.py"
BUILD_REL = "tools/build_agent_ide_bundle.py"
IDE_SERVE_REL = "agent_platform/legion_ide/serve.py"
IDE_HTML_REL = "portal/ide.html"
START_IDE_REL = "ops/start_legion_ide.ps1"
TEST_GATE_REL = "tools/test_gate.ps1"
STATIC_NGINX_TEST_REL = "portal/tests/test_nginx_auth_request.py"
VAULT_BUNDLE = Path(r"C:\项目\_vault\legion_ide\agent_ide_bundle.json")
CONTAINER_NAME = "ai-portal"
SCHEMA = "bundle_phase2_dryrun/v1"

# Phase2-B（推荐）：原地去 tracked（nginx 零改动，解耦仅靠 .gitignore + 生成态文件）
OPTION_B = "B_untrack_in_place"
# Phase2-A（W5 原文）：独立 artifact + nginx alias 到 _vault（需容器挂载 + reload = L2）
OPTION_A = "A_vault_alias_mount"


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 20) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "EXEC_ERROR: %s" % exc


def git(repo: Path, *args: str) -> tuple[int, str]:
    return _run(["git", "-C", str(repo), *args])


def sha256_file(p: Path) -> str:
    try:
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def _grep_lines(p: Path, pattern: str, limit: int = 6) -> list[int]:
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rx = re.compile(pattern)
    return [i for i, line in enumerate(text.splitlines(), 1) if rx.search(line)][:limit]


def _snapshot(paths: list[Path]) -> dict:
    """记录文件 size/mtime/sha256，用于自证 dry-run 零写入。"""
    snap = {}
    for p in paths:
        try:
            st = p.stat()
            snap[str(p)] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
                            "sha256": sha256_file(p) if p.is_file() else ""}
        except OSError:
            snap[str(p)] = {"missing": True}
    return snap


def _docker_mounts(container: str, mode: str) -> dict:
    if mode == "off":
        return {"available": False, "reason": "docker probe disabled"}
    rc, out = _run(["docker", "inspect", container, "--format", "{{json .Mounts}}"], timeout=20)
    if rc != 0:
        return {"available": False, "reason": "docker inspect failed rc=%d" % rc,
                "error": out.strip()[:200]}
    try:
        mounts = json.loads(out.strip())
    except ValueError:
        return {"available": False, "reason": "docker inspect output not JSON"}
    sources = [str(m.get("Source") or "") for m in mounts if isinstance(m, dict)]
    def _under(prefix: str) -> bool:
        pl = prefix.replace("\\", "/").lower().rstrip("/")
        for s in sources:
            sl = s.replace("\\", "/").lower().rstrip("/")
            if sl == pl or sl.startswith(pl + "/"):
                return True
        return False
    return {
        "available": True,
        "sources": sources,
        "portal_mounted": any(s.replace("\\", "/").lower().endswith("/portal") for s in sources),
        "vault_legion_ide_mounted": _under(r"C:\项目\_vault\legion_ide"),
        "bundle_visible_by_uri": any(s.replace("\\", "/").lower().endswith("/portal") for s in sources),
    }


def _tracked_state(repo: Path) -> dict:
    bundle = repo / BUNDLE_REL
    rc_ls, ls_out = git(repo, "ls-files", "--error-unmatch", BUNDLE_REL)
    rc_ign, ign_out = git(repo, "check-ignore", "-v", BUNDLE_REL)
    rc_st, st_out = git(repo, "status", "--porcelain", "--", BUNDLE_REL)
    rc_last, last_out = git(repo, "log", "-1", "--format=%h %ci %s", "--", BUNDLE_REL)
    return {
        "path": str(bundle),
        "exists": bundle.is_file(),
        "size_bytes": bundle.stat().st_size if bundle.is_file() else 0,
        "sha256": sha256_file(bundle),
        "mtime": dt.datetime.fromtimestamp(bundle.stat().st_mtime).astimezone().isoformat(timespec="seconds")
        if bundle.is_file() else "",
        "tracked": rc_ls == 0,
        "ignored": rc_ign == 0,
        "ignore_rule": (ign_out.strip().splitlines()[0] if ign_out.strip() else ""),
        "worktree_dirty": bool(st_out.strip()),
        "dirty_line": st_out.strip()[:200],
        "last_commit": last_out.strip()[:300],
    }


def _churn(repo: Path, days: int) -> dict:
    since = "%d days ago" % days
    rc_b, b_out = git(repo, "log", "--since=" + since, "--oneline", "--", BUNDLE_REL)
    rc_t, t_out = git(repo, "log", "--since=" + since, "--oneline")
    rc_m, m_out = git(repo, "log", "--since=" + since, "--oneline", "--merges")
    bundle_commits = len([x for x in b_out.splitlines() if x.strip()]) if rc_b == 0 else -1
    total_commits = len([x for x in t_out.splitlines() if x.strip()]) if rc_t == 0 else -1
    merges = len([x for x in m_out.splitlines() if x.strip()]) if rc_m == 0 else -1
    ratio = (bundle_commits / total_commits) if total_commits > 0 else None
    # 最近 8 个 bundle 提交消息（证明"auto rebuild"占绝对多数）
    rc_r, r_out = git(repo, "log", "-8", "--format=%h %s", "--", BUNDLE_REL)
    recent = [x.strip() for x in r_out.splitlines() if x.strip()] if rc_r == 0 else []
    auto = [x for x in recent if "auto rebuild agent_ide_bundle" in x]
    return {
        "window_days": days,
        "bundle_commits": bundle_commits,
        "total_commits": total_commits,
        "merge_commits": merges,
        "ratio": round(ratio, 4) if ratio is not None else None,
        "recent_bundle_commits": recent,
        "recent_auto_rebuild_share": ("%d/%d" % (len(auto), len(recent))) if recent else "",
    }


def _consumers(repo: Path, mounts: dict) -> list[dict]:
    """消费方清单：Phase2 若破坏任一项必须显式处理/回滚。"""
    out: list[dict] = []

    def add(cid, kind, rel, pattern, detail):
        p = repo / rel
        lines = _grep_lines(p, pattern) if p.is_file() else []
        out.append({"id": cid, "kind": kind, "path": rel, "exists": p.is_file(),
                    "found": bool(lines), "lines": lines, "detail": detail})

    add("nginx_location", "server", NGINX_REL, r"location = /agent_ide_bundle\.json",
        "8734 同源入口：精确 location + auth_request + try_files $uri（从容器 html 根=portal 目录取文件）")
    add("frontend_fetch", "frontend", IDE_HTML_REL, r'fetch\("/agent_ide_bundle\.json"',
        "IDE 页同源 fetch；Phase2 后仍须 200 且内容为最新生成物")
    add("devtask_autorebuild", "tooling", DEVTASK_REL, r"_auto_rebuild_ide_bundle|BUNDLE_SOURCE",
        "Phase1：仅 agent 源变更才重建+paths 提交；Phase2 需改为重建但不提交")
    add("rebake_script", "tooling", REBAKE_REL, r"agent_ide_bundle\.json",
        "post-merge 钩子：build→复制 vault 部署副本；Phase2-B 起不再 git checkout 还原/不提交")
    add("generator", "tooling", BUILD_REL, r"portal\", \"agent_ide_bundle\.json\"|OUT =",
        "生成器：OUT=portal/agent_ide_bundle.json（含 generated 时间戳，故每次内容会变）")
    add("ide_serve_8103", "server", IDE_SERVE_REL, r"DEFAULT_BUNDLE|agent_ide_bundle\.json",
        "8103 独立 IDE：优先读 _vault 部署副本（已解耦），repo 文件仅兜底")
    add("start_ide_script", "tooling", START_IDE_REL, r"agent_ide_bundle\.json|Resolve-BundlePath",
        "8103 启动器：Resolve-BundlePath 优先 _vault 副本")
    add("static_nginx_test", "test", STATIC_NGINX_TEST_REL, r"agent_ide_bundle\.json",
        "静态断言 nginx location 存在且 auth_request —— Phase2-A 改 nginx 必须同步此测试")
    out.append({
        "id": "vault_deploy_copy",
        "kind": "artifact",
        "path": str(VAULT_BUNDLE),
        "exists": VAULT_BUNDLE.is_file(),
        "found": VAULT_BUNDLE.is_file(),
        "lines": [],
        "detail": "8103/独立 IDE 的部署副本；应比 tracked 版更新（post-merge 钩子每 merge 刷新）",
        "sha256": sha256_file(VAULT_BUNDLE),
        "size_bytes": VAULT_BUNDLE.stat().st_size if VAULT_BUNDLE.is_file() else 0,
    })
    out.append({
        "id": "nginx_container_mount",
        "kind": "server",
        "path": CONTAINER_NAME,
        "exists": mounts.get("available", False),
        "found": bool(mounts.get("portal_mounted")),
        "lines": [],
        "detail": "nginx 在容器内：portal 目录是 ro bind mount（html 根）；_vault 是否可见决定 Phase2-A 可行性",
        "vault_legion_ide_mounted": bool(mounts.get("vault_legion_ide_mounted")),
        "mount_probe": mounts,
    })
    return out


def _options(repo: Path, tracked: dict, mounts: dict) -> list[dict]:
    vault_visible = bool(mounts.get("vault_legion_ide_mounted"))
    return [
        {
            "id": OPTION_B,
            "title": "原地去 tracked：生成物保留在 portal/（nginx 零改动）",
            "recommended": True,
            "feasible": true_path(repo),
            "l2_required": False,
            "why": [
                "portal/ 目录已是 ai-portal 容器 ro bind mount（html 根），生成文件留在 portal/ 即可被 8734 直接 serve，无需改 nginx/挂载",
                "git rm --cached + .gitignore 后：bundle 提交 0 增长、工作树 status 干净（ignored）、历史提交不删",
                "8103 早已优先读 _vault 部署副本，无需变更",
            ],
            "tradeoffs": [
                "repo 工作树仍会有一个 75 万字节的生成文件（untracked/ignored，不随 git 分发）",
                "干净 clone/worktree 缺文件 → 需 bootstrap 兜底（test_gate/start_legion_ide 幂等生成）",
            ],
        },
        {
            "id": OPTION_A,
            "title": "独立 artifact：nginx alias 指向 _vault/legion_ide/agent_ide_bundle.json",
            "recommended": False,
            "feasible": bool(tracked.get("exists")) and vault_visible,
            "l2_required": True,
            "why": ["产物彻底离开 repo 目录（W5 原文方案）"],
            "blockers": [] if vault_visible else [
                "ai-portal 容器未挂载 C:\\项目\\_vault\\legion_ide（实测仅挂 portal/ 与 nginx.conf）→ nginx alias 到该路径会 404",
                "补齐需 docker 重建容器（8734 入口重启）= 比本 Phase2 目标更重的 L2",
            ],
            "tradeoffs": [
                "需要 nginx conf + 容器挂载 + reload 三处变更（L2 五要素窗口）",
                "静态测试 portal/tests/test_nginx_auth_request.py 需同步",
            ],
        },
    ]


def true_path(repo: Path) -> bool:
    """Option B 可行性：只要求是 git 仓库且 tracked 文件存在（原地去索引即可）。"""
    rc, _ = git(repo, "rev-parse", "--is-inside-work-tree")
    return rc == 0


def _plan_b(tracked: dict) -> list[dict]:
    return [
        {
            "id": "B1",
            "title": "追加 .gitignore 规则（先 ignore，后去索引）",
            "l2": False,
            "commands": ['Add-Content .gitignore "`nportal/agent_ide_bundle.json"'],
            "verify": ["git check-ignore -v portal/agent_ide_bundle.json → 命中"],
            "rollback": ["从 .gitignore 删除该行"],
            "executed": False,
        },
        {
            "id": "B2",
            "title": "git rm --cached（只出索引，不删工作树文件）",
            "l2": False,
            "commands": ["git rm --cached portal/agent_ide_bundle.json"],
            "verify": ["git ls-files 不再包含该路径", "文件仍在 portal/ 且被 ignore"],
            "rollback": ["git add -f portal/agent_ide_bundle.json 并提交（恢复 tracked）"],
            "executed": False,
            "warning": "dry-run 绝不执行；本脚本只输出命令文本",
        },
        {
            "id": "B3",
            "title": "rebake 脚本去掉 tracked 恢复动作",
            "l2": False,
            "commands": ["编辑 ops/rebake_ide_bundle.ps1：删除末尾 `git checkout -- portal/agent_ide_bundle.json`（保留 build + 复制部署副本）"],
            "verify": ["grep 不到 checkout 恢复行", "手动跑 rebake 后 portal/ 生成态文件 mtime 更新、git status 干净"],
            "rollback": ["git checkout -- ops/rebake_ide_bundle.ps1"],
            "executed": False,
        },
        {
            "id": "B4",
            "title": "devtask 自动重烘改为「重建+刷新部署副本、不提交」",
            "l2": False,
            "commands": ["编辑 tools/devtask.py::_auto_rebuild_ide_bundle：保留源变更判定与 DEVTOOL_IDE_BUNDLE_DRY_RUN，删除 git add/commit 段，改为调用 build + 复制 _vault 副本"],
            "verify": ["tools/tests/test_auto_rebuild_ide_bundle.py 更新后全绿", "merge 后 git status 无 bundle 变更、0 bundle 提交"],
            "rollback": ["git checkout -- tools/devtask.py tools/tests/test_auto_rebuild_ide_bundle.py"],
            "executed": False,
        },
        {
            "id": "B5",
            "title": "bootstrap 兜底：干净 clone/worktree 缺 bundle 时自动生成",
            "l2": False,
            "commands": ["tools/test_gate.ps1 开头加：若 portal/agent_ide_bundle.json 缺失 → python tools/build_agent_ide_bundle.py（幂等）", "ops/start_legion_ide.ps1 同上（或复用 rebake 逻辑）"],
            "verify": ["临时重命名 bundle 后跑 gate/start → 自动重建且 exit 正常"],
            "rollback": ["git checkout -- tools/test_gate.ps1 ops/start_legion_ide.ps1"],
            "executed": False,
        },
        {
            "id": "B6",
            "title": "首个生成态 + 双面验证（非 L2：不重启、不 reload）",
            "l2": False,
            "commands": ["python -X utf8 tools/build_agent_ide_bundle.py",
                         "robocopy/复制 portal/agent_ide_bundle.json → C:\\项目\\_vault\\legion_ide\\agent_ide_bundle.json"],
            "verify": [
                "8734（登录态）GET /agent_ide_bundle.json → 200 且 sha256 == 生成文件",
                "8103 GET /agent_ide_bundle.json → 200 且 sha256 == _vault 副本",
                "git status --porcelain → 无 bundle 行（ignored）；git ls-files → 无该路径",
            ],
            "rollback": ["git add -f portal/agent_ide_bundle.json && commit（恢复 tracked）"],
            "executed": False,
        },
        {
            "id": "B7",
            "title": "观测验收（2 日）",
            "l2": False,
            "commands": ["git log --since='2 days ago' --oneline -- portal/agent_ide_bundle.json  → 期望空",
                         "git log --since='2 days ago' --oneline --grep='auto rebuild agent_ide_bundle' → 期望空"],
            "verify": ["bundle 提交 0 增长；8734/8103 数据一致且为最新；IDE 页工单卡数据新鲜度由 post-merge rebake 保证"],
            "rollback": ["按 B2 回滚恢复 tracked 即可"],
            "executed": False,
        },
    ]


def analyze(repo: Path | str = REPO_DEFAULT, days: int = 8, docker_mode: str = "auto",
            docker_info: dict | None = None, l2_approved: bool = False,
            now: str | None = None) -> dict:
    """离线只读分析（唯一稳定契约）。不写任何文件、不动 git 索引。"""
    repo = Path(repo)
    candidate = [repo / BUNDLE_REL, repo / GITIGNORE_REL, repo / NGINX_REL,
                 repo / REBAKE_REL, repo / DEVTASK_REL, repo / BUILD_REL,
                 VAULT_BUNDLE]
    before = _snapshot(candidate)

    rc_repo, _ = git(repo, "rev-parse", "--is-inside-work-tree")
    if rc_repo != 0:
        return {"schema": SCHEMA, "ok": False, "error": "not_a_git_repo", "repo": str(repo),
                "execution": {"dry_run": True, "executed": False}}

    tracked = _tracked_state(repo)
    mounts = docker_info if docker_info is not None else _docker_mounts(CONTAINER_NAME, docker_mode)
    consumers = _consumers(repo, mounts)
    options = _options(repo, tracked, mounts)
    option_b = next(o for o in options if o["id"] == OPTION_B)
    plan = _plan_b(tracked)
    after = _snapshot(candidate)

    writes = sorted([p for p in before if before[p] != after.get(p)])
    rebake_text = (repo / REBAKE_REL).read_text(encoding="utf-8", errors="replace") \
        if (repo / REBAKE_REL).is_file() else ""
    devtask_text = (repo / DEVTASK_REL).read_text(encoding="utf-8", errors="replace") \
        if (repo / DEVTASK_REL).is_file() else ""
    implementation = {
        "b1_gitignore": bool(tracked["ignored"]),
        "b2_untracked": not tracked["tracked"],
        "b3_rebake_no_checkout": ("git -C $Repo checkout --" not in rebake_text),
        # 注释里可能提到旧函数名；判定标准=没有定义/调用点 + 有部署副本刷新助手
        "b4_devtask_no_commit": ("def _ide_bundle_commit_args" not in devtask_text
                                 and "_ide_bundle_commit_args(" not in devtask_text
                                 and "_refresh_bundle_deploy_copy" in devtask_text),
        "deploy_copy_helper": "_refresh_bundle_deploy_copy" in devtask_text,
    }
    implementation["done"] = all(implementation[k] for k in
                                ("b1_gitignore", "b2_untracked", "b3_rebake_no_checkout",
                                 "b4_devtask_no_commit"))
    landed_map = {"B1": implementation["b1_gitignore"],
                  "B2": implementation["b2_untracked"],
                  "B3": implementation["b3_rebake_no_checkout"],
                  "B4": implementation["b4_devtask_no_commit"]}
    for step in plan:
        step["landed"] = bool(landed_map.get(step["id"], False))
    blockers = []
    if not tracked["ignored"]:
        blockers.append("W5-P2-1: portal/agent_ide_bundle.json 尚未 ignore（Phase2-B1 需先加）")
    if tracked["tracked"]:
        blockers.append("W5-P2-2: bundle 仍 tracked（Phase2-B2 git rm --cached 未执行；本 dry-run 不执行）")
    if not mounts.get("vault_legion_ide_mounted"):
        blockers.append("PHASE2-A 不可行：ai-portal 容器未挂载 _vault/legion_ide（alias 会 404）")
    if not l2_approved:
        blockers.append("L2 未批准：Phase2 生产窗口/提交执行 blocked（本 dry-run 不执行任何变更）")

    return {
        "schema": SCHEMA,
        "ok": True,
        "generated_at": now or dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "repo": str(repo),
        "dry_run": {
            "executed": False,
            "commands_executed": [],
            "mutating_commands_in_plan_not_executed": ["git rm --cached", "git add/commit", "nginx reload", "docker restart"],
            "self_check": {
                "no_writes": not writes,
                "writes_detected": writes,
                "snapshot_paths": [str(x) for x in candidate],
            },
        },
        "implementation": implementation,
        "phase1_status": {
            "devtask_source_gate_present": (repo / DEVTASK_REL).is_file()
            and bool(_grep_lines(repo / DEVTASK_REL, r"_bundle_sources_changed")),
            "note": "W5 Phase1 已落地（源变才提交）；Phase2 目标=0 增长",
        },
        "churn": _churn(repo, days),
        "tracked": tracked,
        "vault_copy": {
            "path": str(VAULT_BUNDLE),
            "exists": VAULT_BUNDLE.is_file(),
            "size_bytes": VAULT_BUNDLE.stat().st_size if VAULT_BUNDLE.is_file() else 0,
            "sha256": sha256_file(VAULT_BUNDLE),
            "same_as_repo_copy": sha256_file(VAULT_BUNDLE) == tracked["sha256"] if VAULT_BUNDLE.is_file() else False,
            "note": "部署副本由 post-merge 钩子刷新；与 repo tracked 版不同=正常（repo 版被 checkout 还原）",
        },
        "consumers": consumers,
        "docker": mounts,
        "options": options,
        "recommended_option": OPTION_B,
        "phase2_plan": plan,
        "l2": {
            "approved": bool(l2_approved),
            "status": "approved" if l2_approved else "blocked",
            "required_for": [OPTION_A],
            "not_required_for": [OPTION_B],
            "note": "Phase2-B（推荐）为仓库/生成物变更，不需要重启服务或 reload nginx；Phase2-A 需容器挂载+nginx reload=L2",
        },
        "execution": {
            "dry_run_only": True,
            "status": "blocked" if not l2_approved else "plan_ready",
            "reason": "" if l2_approved else "L2 未批准 / 未进入受控窗口",
            "next": "Lead 排期 → 按 phase2_plan 执行（B1-B7）→ 观测 2 日 → 回填 W5 报告",
        },
        "blockers": blockers,
        "rollback": {
            "B_tracked_restore": ["git revert <phase2 commit>", "或 git add -f portal/agent_ide_bundle.json && git commit"],
            "B_ignore_restore": ["删除 .gitignore 中 portal/agent_ide_bundle.json 行"],
            "A_full": ["恢复 portal/nginx.conf（git checkout）", "docker compose 回滚挂载并重启 ai-portal（L2）", "git add -f bundle 恢复 tracked"],
            "guarantee": "历史提交不删；bundle 内容可由 tools/build_agent_ide_bundle.py 重生成",
        },
    }


def render_human(res: dict) -> str:
    if not res.get("ok"):
        return "[bundle-phase2] 分析失败: %s" % res.get("error")
    t = res["tracked"]
    ch = res["churn"]
    lines = [
        "== bundle Phase2 离线 dry-run（只读；未执行任何变更）==",
        "bundle: tracked=%s ignored=%s size=%s sha=%s" % (t["tracked"], t["ignored"], t["size_bytes"], t["sha256"][:16]),
        "churn(%sd): bundle提交=%s / 总提交=%s = %s；近期 auto rebuild 占比=%s" % (
            ch["window_days"], ch["bundle_commits"], ch["total_commits"],
            ch["ratio"], ch["recent_auto_rebuild_share"]),
        "vault 副本: exists=%s same_as_repo=%s" % (res["vault_copy"]["exists"], res["vault_copy"]["same_as_repo_copy"]),
        "消费方: %s" % ", ".join("%s=%s" % (c["id"], "OK" if c["found"] else "MISS") for c in res["consumers"]),
        "推荐方案: %s（l2_required=%s）" % (res["recommended_option"],
                                        next(o["l2_required"] for o in res["options"] if o["id"] == res["recommended_option"])),
        "备选: %s feasible=%s（blockers: %s）" % (
            res["options"][1]["id"], res["options"][1]["feasible"],
            "; ".join(res["options"][1].get("blockers") or []) or "-"),
        "L2 门禁: %s（%s）" % (res["l2"]["status"], "未批准=只出计划" if not res["l2"]["approved"] else "已标记批准"),
        "Phase2-B 实现: done=%s (B1..B4=%s)" % (
            res["implementation"]["done"],
            ",".join("%s=%s" % (k, res["implementation"][k]) for k in
                     ("b1_gitignore", "b2_untracked", "b3_rebake_no_checkout", "b4_devtask_no_commit"))),
        "自我校验零写入: %s" % res["dry_run"]["self_check"]["no_writes"],
        "blockers:",
    ]
    lines += ["  - " + b for b in res["blockers"]]
    lines.append("Phase2-B 步骤（未执行）:")
    for s in res["phase2_plan"]:
        lines.append("  [%s] %s%s%s" % (s["id"], s["title"], "（L2）" if s["l2"] else "",
                                        "（已落地）" if s.get("landed") else ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="bundle Phase2 离线 dry-run（只读，不执行变更）")
    ap.add_argument("--repo", default=str(REPO_DEFAULT))
    ap.add_argument("--days", type=int, default=8)
    ap.add_argument("--docker", choices=["auto", "off"], default="auto")
    ap.add_argument("--json", action="store_true", help="输出 JSON（唯一稳定契约）")
    ap.add_argument("--l2-approved", action="store_true", help="仅标记 L2 已批准（不执行）")
    ap.add_argument("--require-approved", action="store_true", help="未批准时 exit 2")
    ap.add_argument("--now", default="", help="固定 generated_at（测试用）")
    args = ap.parse_args(argv)
    res = analyze(repo=args.repo, days=args.days, docker_mode=args.docker,
                  l2_approved=args.l2_approved, now=args.now or None)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        print(render_human(res))
    if args.require_approved and res["l2"]["status"] != "approved":
        return 2
    return 0 if res.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
