# -*- coding: utf-8 -*-
"""devtask — 多 Agent 协作开发调度器（K-STOCK-104 方案丙）。

一单一线程：每个任务单 = 独立 git worktree + 独立分支，worker 互不踩脚。

安全提示：
  不要用 `--help` 建单；帮助请用 `devtask.py --help` 或
  `devtask.py <命令> --help`。`--help` 不是 TID，会被 TID 校验拒绝，
  不会生成 aborted 空单。

命令：
  new <TID> <一句话描述> [--project <pid>] [--dept <部门>] [--acceptance "<命令串>"]
                            [--approver <席位>] [--expected-output <格式>]
                            [--boundaries <禁止动什么>] [--effort <预算档>]
                            [--deps <TID1,TID2>]
                           建任务单+worktree+分支 task/<TID>；--dept 声明部门单，
                           命中研发试点门（agent_platform/config/dev_owner_gate.json）
                           时按 phase=warn 只告警 / phase=block 拒收，且拒收发生在
                           建 worktree 之前（不落空 worktree）（WO-LEGION-DEVEXEC-20261027）；
                           --approver 记研发域验收署名（charter dev-authority-v1）；
                           --expected-output/--boundaries/--effort/--deps 为任务契约 2.0
                           可选字段（O2；落账不做强校验，供编排器/评审使用）。
  list [--project <pid>] [--strict]
                           任务单总览（谁在干什么/分支/进度）；--strict 附跑
                           audit-worktrees，有违规则 rc=1（默认行为不变）
  audit-worktrees [--json] [--quiet] [--repo <path>]
                           W2-3 校验：doing/review 单必须有 .worktrees/<TID> 目录
                           （且在 git worktree list 在册）+ task/<TID> 分支；
                           不满足 → 逐项清单 + rc=1；只读，不删/不移 worktree。
  port <服务名>            申请分配一个未注册端口（并提示写回 REGISTRY）
  merge <TID> [--l2-ack --l2-reason "五要素"] [--keep-worktree] [--train-force]
                                    过评审门（Mixin/导航/端口/明文凭据 + L2 危险操作门
                                    + 组合/内核门：compositions/** 漂移与内核契约改动
                                    必须带 ADR-号，P1.2 ADR-015
                                    + merge train 门：窗口 16:30–17:00 / 21:40–22:10；
                                    禁并期/窗外只提交不合，文档/组合声明旁路随时，W1/UF-09）
                                    后合并回 master；成功后**自动回收 worktree+分支**（仅当干净），
                                    --keep-worktree=跳过自动回收（保留待人工处理）；
                                    --l2-ack=人工确认豁免 L2 命中（K-STOCK-416，须带 --l2-reason）；
                                    --train-force=merge train 紧急放行（仅回滚/紧急；打印
                                    留痕警告并写工单 note/approval_verdict 事件，须 24h 复盘）
  board [out.html]         生成看板 HTML（默认 docs/dev/devboard.html）
  drop <TID>               放弃任务（留原因，清 worktree）
  prune [--dry-run|--apply] [--all|--merged|--worktrees] [--repo <path>]
                           仓治理（EXEC-R11）：删已合并 task/* 分支 + 回收 done/aborted
                           且干净的 worktree。默认 dry-run 只打印；--apply 才执行；
                           --repo 仅供单测/演练指向临时仓（不碰真仓）。

依赖：git（Windows 原生，无 tmux）；评审门工具在 tools/ 下。
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
import sys

try:  # 2026-10-26 K-ORG-20261026-ENCPROT: non-UTF8 console printing CJK/checkmark
    # raises UnicodeEncodeError and can crash merge/hook paths; degrade to UTF-8 output.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 - non-TTY/old interpreter: ignore
    pass
import tempfile
import time
from contextlib import contextmanager

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WT_DIR = os.path.join(REPO, ".worktrees")
TASKS = os.path.join(WT_DIR, "tasks.json")
PROJECTS = os.path.join(REPO, "agent_platform", "registry", "projects.json")
ORG_JSON = os.path.join(REPO, "agent_platform", "registry", "org.json")
OWNER_DOMAIN = os.path.join(REPO, "agent_platform", "registry",
                            "owner_domain.json")
# 2026-10-27 WO-LEGION-DEVEXEC-20261027：研发试点门配置。phase=warn 只告警不拒收；
# 改 phase=block 后，显式声明 --dept dev 且缺合法 owner/--acceptance 的单在建
# worktree 之前拒收（不留空 worktree）。配置文件缺失/损坏一律降级 warn（fail-safe）。
DEV_GATE = os.path.join(REPO, "agent_platform", "config", "dev_owner_gate.json")
# 2026-10-27 WO-LEGION-DEVEXEC3-20261027：dev 单路由规则（老田 10-27 批，A0 执行）。
# 新研发需求优先派 ARCH1/组长，不直接落 DSH/治理席；本配置给试点门提供 dev_seats
# 口径（owner 非研发席 → warn 告警，phase=block 时拒收）。配置缺失/坏 JSON 不启用。
DEV_ROUTING = os.path.join(REPO, "agent_platform", "config", "dev_routing_rules.json")
# 2026-10-27 WO-AUTHORITY-B2：研发域最高权限 charter（老田 10-27 批，L1）。
# approval.mode=warn 时合并缺 ARCH1 署名只告警；mode=block 时拒收。
# 配置缺失/坏 JSON 一律降级 warn（fail-safe，不误拒研发单）。
DEV_AUTHORITY = os.path.join(REPO, "agent_platform", "config",
                             "dev_authority_charter.json")
SCAN_SECRETS = os.path.join(REPO, "tools", "scan_secrets.py")
# 2026-10-26 EXEC-R33：凭据扫描基线豁免表。存在才传给扫描器；缺失则回退旧行为
# （不带 --baseline，全部未豁免命中照旧拒绝合并 = fail-closed）。
SECRETS_BASELINE = os.path.join(REPO, "tools", "scan_secrets_allowlist.tsv")
SECRET_SCAN_BATCH = 50


CPT_DB = os.path.join(REPO, "agent_platform", "data", "checkpoints.db")
_CKPT = None


def _checkpointer():
    """K-ORG-090/G4：工单 checkpoint 惰性单例——第一次用到才连 db，
    避免每次 CLI 调用（list/board 等不挂线的命令）都开连接。"""
    global _CKPT
    if _CKPT is None:
        sys.path.insert(0, os.path.join(REPO, "agent_platform"))
        from engine.checkpoints import Checkpointer
        _CKPT = Checkpointer(CPT_DB)
    return _CKPT


def _ckpt(tid, node_key, owner, state):
    """挂线快照（K-ORG-090/G4）：DEVTASK_CHECKPOINT=0 时跳过（回退开关）；
    任何异常只 WARN 不阻断——devtask 是产码主链，挂线零行为差异。"""
    if os.environ.get("DEVTASK_CHECKPOINT", "") == "0":
        return
    try:
        _checkpointer().save_task_node(tid, node_key, owner, state)
    except Exception as e:  # noqa: BLE001  fail-open：快照失败不挡工单
        print("[WARN] checkpoint 写入失败（不阻断主流程）：%s" % e)


def _load_projects():
    """K-ORG-057：项目线注册表（缺文件/坏 JSON 一律降级为空，不挡道）。"""
    try:
        return json.load(open(PROJECTS, encoding="utf-8")).get("projects", [])
    except Exception:  # noqa: BLE001
        return []


def _resolve_project(pid):
    """校验项目线：未注册/已归档 → 拒收（判据3：跨线工单必须显式声明合法 project）。"""
    for pr in _load_projects():
        if pr.get("project_id") == pid:
            if pr.get("status", "active") != "active":
                raise SystemExit("项目线 %s 已归档（archived）——新工单拒收，请选活跃线或先在组织图恢复" % pid)
            return pr
    raise SystemExit("项目线 %s 未注册——拒收。先在组织图部门卡「+ 子项目」注册，"
                     "或用已注册线：%s" % (pid, ", ".join(
                         p.get("project_id", "?") for p in _load_projects()) or "（无）"))


def _attribution_agent():
    """事件归因 agent：AGENT_NAME > DEVTOOL_AGENT > devtask。"""
    return (os.environ.get("AGENT_NAME") or os.environ.get("DEVTOOL_AGENT")
            or "devtask").strip()


def _emit(kind, task, detail=""):
    """事件回流（K-STOCK-114c）：merge/guard 动作自动进平台事件层，失败不挡道。"""
    try:
        sys.path.insert(0, os.path.join(REPO, "tools"))
        from events import emit
        emit(kind, task=task, agent=_attribution_agent(), detail=detail)
    except Exception:  # noqa: BLE001  事件层永不阻塞调度器
        pass
    # 飞书卡尽力而为：合并→回执卡；守卫失败→告警卡（任一失败静默）
    try:
        if kind in ("merge_done", "guard_fail"):
            from notify_devops import card_event
            from feishu_card_v2 import send_card
            send_card(card_event(
                "merge" if kind == "merge_done" else "blocked",
                task, detail))
    except Exception:  # noqa: BLE001
        pass


def _materialize_branch_files(branch, files):
    """worktree 缺失时的回退：把分支改动文件落到临时目录（调用方负责清理）。

    仅供扫描器按路径模式读取**分支内容**；删除文件/子模块取不到 blob 时跳过。
    """
    root = tempfile.mkdtemp(prefix="devtask_secret_")
    for rel in files:
        r = subprocess.run(["git", "show", "%s:%s" % (branch, rel)],
                           cwd=REPO, capture_output=True)
        if r.returncode != 0:
            continue
        dest = os.path.join(root, rel)
        parent = os.path.dirname(dest)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(dest, "wb") as f:
            f.write(r.stdout)
    return root


def _secret_scan_gate(branch, worktree=""):
    """第五道守卫：明文凭据扫描（只扫本分支相对 master 的改动文件）。

    merge 时分支内容已 commit、暂存区为空，扫描器默认的 ``git diff --cached``
    模式会永远返回干净，故这里显式取 ``master...<branch>`` 的文件清单分批传入。
    优先以该工单的 worktree 为扫描根（分支检出内容）；worktree 缺失时从分支
    blob 临时落盘回退，避免误扫 master 侧的旧内容。

    扫描器缺失 → 跳过并告警（fail-open，不阻断合并）；任一未豁免命中 →
    返回失败，由 merge 走既有 guard_fail 通道阻断。输出只透传扫描器已脱敏
    报告，绝不回显凭据明文。

    2026-10-26 EXEC-R33：若 tools/scan_secrets_allowlist.tsv 存在，分批调用会追加
    ``--baseline <allowlist>`` —— 已归因基线只备案、不误拒（避免「分支只是改到
    历史基线行」被第五道门挡）；真正**新增**的未豁免命中仍 rc≠0 → fail-closed
    拒绝合并。豁免表缺失时自动回退旧行为（不带 baseline）。豁免条数随结果返回，
    便于 merge 输出审计。
    """
    if not os.path.isfile(SCAN_SECRETS):
        print("  [WARN] 明文凭据：扫描器不存在，跳过（fail-open，不阻断合并）：%s"
              % SCAN_SECRETS)
        return True, "扫描器不存在，已跳过（fail-open）"
    try:
        # -z：避免中文/特殊字符路径被 git 转义成带引号的字面量。
        out = _git("diff", "--name-only", "-z", "master...%s" % branch)
    except SystemExit as e:
        return False, "无法获取分支改动清单：%s" % e
    if "\0" in out:
        files = [p for p in out.split("\0") if p.strip()]
    else:  # 兼容测试桩/旧 git 输出：非 -z 时的逐行清单
        files = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if not files:
        return True, "无改动文件，视为通过（空改动）"
    cleanup = None
    scan_cwd = worktree or ""
    if scan_cwd and not os.path.isabs(scan_cwd):
        scan_cwd = os.path.join(REPO, scan_cwd)
    if not (scan_cwd and os.path.isdir(scan_cwd)):
        scan_cwd = _materialize_branch_files(branch, files)
        cleanup = scan_cwd
    failures = []
    # 2026-10-26 EXEC-R33：基线豁免表存在才启用；统计条数供审计输出。
    baseline = SECRETS_BASELINE if os.path.isfile(SECRETS_BASELINE) else ""
    baseline_n = 0
    if baseline:
        try:
            with open(baseline, encoding="utf-8") as bf:
                baseline_n = sum(1 for ln in bf
                                 if ln.strip()
                                 and not ln.lstrip().startswith("#")
                                 and ln.split("\t")[0].strip() != "key")
        except OSError:
            baseline, baseline_n = "", 0
    try:
        for start in range(0, len(files), SECRET_SCAN_BATCH):
            batch = files[start:start + SECRET_SCAN_BATCH]
            try:
                cmd = [sys.executable, SCAN_SECRETS]
                if baseline:
                    cmd += ["--baseline", baseline]
                cmd += ["--"] + batch
                r = subprocess.run(
                    cmd,
                    cwd=scan_cwd, capture_output=True, text=True,
                    encoding="utf-8", errors="replace")
            except OSError as e:
                failures.append((start, -1, "扫描器启动失败：%s" % e))
                continue
            if r.returncode != 0:
                failures.append((start, r.returncode,
                                 (r.stdout or r.stderr).strip()))
    finally:
        if cleanup:
            shutil.rmtree(cleanup, ignore_errors=True)
    if not failures:
        if baseline:
            return True, ("%d 个改动文件，无新增未豁免命中"
                          "（基线豁免 %d 条已应用）" % (len(files), baseline_n))
        return True, "%d 个改动文件，无未豁免命中" % len(files)
    total = (len(files) + SECRET_SCAN_BATCH - 1) // SECRET_SCAN_BATCH
    summary = "未豁免命中（%d/%d 批退出码非 0）" % (len(failures), total)
    if baseline:
        summary += " [基线豁免 %d 条已应用]" % baseline_n
    reports = "\n".join(o for _, _, o in failures if o)
    hits = [ln.strip() for ln in reports.splitlines()
            if ln.strip().startswith("HIT ")]
    if hits:  # 首条命中置于摘要，确保 guard_fail 的 120 字截断仍可见
        summary += "；" + hits[0][:180]
    return False, (summary + "\n" + reports).strip()


# 2026-10-25 B5/P1-07（审计 RC-04）：守卫子进程强制 UTF-8 环境。Windows 下
# 子进程 stdout 被管道重定向时按 locale(GBK) 编码，守卫里的 ❌/✓ 会
# UnicodeEncodeError 崩溃 → 评审门在干净 shell 假红。守卫脚本自身另有
# sys.stdout.reconfigure(utf-8) 兜底，两处都加 = 双保险。
GUARD_ENV = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")


def _ui_audit_gate(branch, worktree=""):
    """UI/UX 静态审计（warn-only，永不阻断合并）。

    只扫分支变更的 .html/.js/.css，报告硬编码 hex/内联 style/a11y/状态/动效指标；
    warn 只提示，不改变 merge 结果（2026-10-27 Week4）。
    """
    try:
        r = subprocess.run(["git", "-C", REPO, "diff", "--name-only",
                            "master...%s" % branch], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        rels = [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]
        base = Path(worktree) if worktree else Path(REPO)
        files = [base / rel for rel in rels
                 if rel.lower().endswith((".html", ".js", ".css")) and (base / rel).is_file()]
        if not files:
            return True, "无前端变更"
        warn = info = 0
        codes = []
        for f in files:
            rr = subprocess.run([sys.executable,
                                 os.path.join(REPO, "tools", "ui_ux_audit.py"),
                                 str(f), "--json"],
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", env=GUARD_ENV)
            try:
                data = json.loads(rr.stdout or "[]")
            except Exception:
                data = []
            for item in data if isinstance(data, list) else []:
                warn += int(item.get("warn") or 0)
                info += int(item.get("info") or 0)
                for fnd in item.get("findings", []):
                    if fnd.get("level") == "warn":
                        codes.append(fnd.get("code"))
        detail = ("扫描 %d 文件：warn=%d info=%d%s"
                  % (len(files), warn, info,
                     ("；" + ",".join(sorted(set(codes))[:4])) if codes else ""))
        return True, detail + "（warn-only，不阻断）"
    except Exception as exc:  # noqa: BLE001 —— 审计异常不得反噬合并
        return True, "审计异常（不阻断）：%s" % str(exc)[:120]

# 2026-10-30 P77：数据线已重建 registry（metadata code=0），移除已知告警豁免 → 元数据门全阻断。
KNOWN_META_WARN_ONLY: set[str] = set()


def _syntax_gate(branch, worktree=""):
    """语法门（阻断）：改动文件的 SyntaxError/SyntaxWarning；--repo 指向分支工作树。"""
    base = worktree or REPO
    script = os.path.join(REPO, "tools", "check_syntax.py")
    if not os.path.isfile(script):
        return True, "跳过（工具未安装）"
    r = subprocess.run([sys.executable, script, "--repo", base, "--json", "--fail-on-warning"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=GUARD_ENV, timeout=300)
    try:
        d = json.loads(r.stdout or "{}")
        detail = "扫描 %d / 错误 %d / 告警 %d" % (
            d.get("scanned", 0), d.get("error_count", 0), d.get("warning_count", 0))
        if r.returncode == 0:
            return True, detail
        extra = ""
        for x in (d.get("errors") or [])[:2]:
            extra += " %s:%s" % (x.get("file"), str(x.get("err"))[:60])
        for x in (d.get("warnings") or [])[:2]:
            extra += " WARN:%s:%s" % (x.get("file"), str(x.get("warn"))[:60])
        return False, detail + extra
    except Exception:  # noqa: BLE001
        return r.returncode == 0, (r.stdout or r.stderr or "").strip()[:160]


def _metadata_gate(branch, worktree=""):
    """元数据一致性门（阻断）：catalog × registry × lifecycle × store。

    已知数据线待办（registry_catalog_sha_mismatch）为已知告警豁免；
    数据线 registry 重建后移除 KNOWN_META_WARN_ONLY 中的条目（恢复全阻断）。
    """
    script = os.path.join(REPO, "tools", "datawarehouse", "metadata_consistency.py")
    if not os.path.isfile(script):
        return True, "跳过（工具未安装）"
    r = subprocess.run([sys.executable, script, "--json"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=GUARD_ENV, timeout=120)
    try:
        d = json.loads(r.stdout or "{}")
        detail = "catalog v%s / %d 表 / registry %d 物理" % (
            d.get("catalog_version"), d.get("tables", 0), d.get("physical_count", 0))
        issues = [str(x) for x in (d.get("issues") or [])]
        known = [x for x in issues if x in KNOWN_META_WARN_ONLY]
        blocking = [x for x in issues if x not in KNOWN_META_WARN_ONLY]
        # 2026-10-30 P77 起 KNOWN_META_WARN_ONLY 为空 → 任何 issue 均阻断。
        if r.returncode == 0:
            return True, detail
        if known and not blocking:
            return True, detail + "；已知数据线待办（仅告警）：" + ",".join(known[:3])
        return False, detail + "；" + ";".join(blocking[:3])
    except Exception:  # noqa: BLE001
        return r.returncode == 0, (r.stdout or r.stderr or "").strip()[:160]


def _import_boundary_gate(branch, worktree=""):
    """导入边界门（阻断）：业务模块 import 必须仍在单测白名单内（osleg logging 类回归）。"""
    base = worktree or REPO
    script = os.path.join(REPO, "tools", "check_import_boundaries.py")
    if not os.path.isfile(script):
        return True, "跳过（工具未安装）"
    r = subprocess.run([sys.executable, script, "--repo", base, "--json"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=GUARD_ENV, timeout=120)
    try:
        d = json.loads(r.stdout or "{}")
        checked = len(d.get("checked") or [])
        if r.returncode == 0:
            return True, "检查 %d 条边界" % checked
        return False, ";".join((d.get("issues") or [])[:3])
    except Exception:  # noqa: BLE001
        return r.returncode == 0, (r.stdout or r.stderr or "").strip()[:160]



def _nav_gate(branch, worktree=""):
    """分支感知的导航门（WO-NAVROOT-DEDUP-20261031）。

    此前 ``unify_nav.py --check`` 固定验 master 树（与 P103 前的 mixin 门同病）：
    分支内新增静态页未带 nav.js / nav.js 与 api_common 数据漂移，合并门看不到。
    改为传 ``--root <worktree>`` 在分支树内校验（缺 worktree 时回退 REPO）。
    """
    root = worktree if (worktree and os.path.isdir(worktree)) else REPO
    r = subprocess.run([sys.executable,
                        os.path.join(REPO, "tools", "unify_nav.py"),
                        "--check", "--root", root],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env=GUARD_ENV)
    return r.returncode == 0, (r.stdout or r.stderr or "").strip()[-200:]


def _mixin_gate(branch, worktree=""):
    """分支感知的 Mixin 门（2026-10-30 P103）。

    此前固定扫描 master 树：并行线先后增/删 Handler 方法会互相误卡
    （P98 新增 → P99 移除 → 并行线补回基线，连续三次基线热修）。
    改为在分支 worktree 内校验（基线也取分支文件，显式改基线在评审 diff 可见）。
    """
    root = worktree if (worktree and os.path.isdir(worktree)) else REPO
    r = subprocess.run([sys.executable,
                        os.path.join(REPO, "tools", "_mixin_integrity.py"),
                        "--check", "--root", root],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env=GUARD_ENV)
    return r.returncode == 0, (r.stdout or r.stderr or "").strip()[-200:]


GATES = [("Mixin方法集", _mixin_gate),
         ("导航统一", _nav_gate),
         ("端口注册", [sys.executable,
                       os.path.join(REPO, "tools", "check_ports.py"),
                       "--strict"]),  # 2026-10-26 WO-UPGRADE-20261026：合并门
         # 必须走 strict，否则非 strict 恒 rc=0 = 假门（审计 B-02）。strict 语义
         # 已拆：只拦「未登记监听/端口冲突」，在册服务离线不判负（check_ports.py）。
         ("UI/UX审计", _ui_audit_gate),
         ("明文凭据", _secret_scan_gate),
         ("语法门", _syntax_gate),
         ("导入边界", _import_boundary_gate),
         ("元数据一致性", _metadata_gate)]


def _security_warn_gate(branch, worktree=""):
    """安全扫描（告警模式）：只扫分支变更文件，high 只提示不阻断。"""
    try:
        r = subprocess.run(["git", "-C", REPO, "diff", "--name-only",
                            "master...%s" % branch], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        rels = [x.strip() for x in r.stdout.splitlines() if x.strip()]
        base = Path(worktree) if worktree else Path(REPO)
        paths = []
        for rel in rels:
            f = base / rel
            if f.exists() and f.suffix.lower() in (".py", ".js", ".ts", ".json", ".yaml",
                                                   ".yml", ".env", ".ps1", ".bat"):
                paths.append(str(f))
        if not paths:
            print("  %-8s - 无文本变更可扫" % "安全扫描")
            return
        r = subprocess.run([sys.executable, os.path.join(REPO, "tools", "security_gate.py"),
                            "scan", "--path"] + paths + ["--json"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=GUARD_ENV)
        data = {}
        for ln in (r.stdout or "").splitlines():
            if ln.strip().startswith("{"):
                data = json.loads(r.stdout)
                break
        high = int(data.get("high") or 0)
        total = int(data.get("total") or 0)
        mark = "⚠" if high else "✓"
        print("  %-8s %s high=%d total=%d（告警模式，不阻断）"
              % ("安全扫描", mark, high, total))
        if high:
            for f in (data.get("findings") or [])[:10]:
                if f.get("severity") == "high":
                    print("    [high] %s:%s %s" % (f.get("file"), f.get("line"), f.get("rule")))
    except Exception as e:  # noqa: BLE001
        print("  %-8s ? 扫描失败（忽略，不阻断）：%s" % ("安全扫描", str(e)[:120]))


def _gitleaks_warn_gate(branch, worktree=""):
    """gitleaks 第二道凭据扫描（告警模式，2026-10-27 P0 质量门升级）。

    与 _security_warn_gate 互补：自研扫描器管历史/批量口径，gitleaks 管真实凭据面；
    只告警不阻断（先并行观察，稳定后再升级为阻断门）。gitleaks 未安装时静默跳过。
    """
    script = Path(REPO) / "tools" / "secret_scan_gitleaks.py"
    if not script.exists():
        return
    base = Path(worktree) if worktree else Path(REPO)
    try:
        r = subprocess.run([sys.executable, str(script), "--diff", "master...%s" % branch,
                            "--worktree", str(base), "--json"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=GUARD_ENV, timeout=600)
    except Exception as e:  # noqa: BLE001 - 告警门不得阻断合并
        print("  %-8s ! gitleaks 执行异常：%s" % ("gitleaks", str(e)[:100]))
        return
    try:
        data = json.loads(r.stdout or "{}")
    except ValueError:
        print("  %-8s ! gitleaks 输出不可解析（跳过）" % ("gitleaks",))
        return
    n = int(data.get("findings") or 0)
    if r.returncode == 2:
        print("  %-8s - 未安装/不可用（跳过，warn-only）" % ("gitleaks",))
    elif n:
        print("  %-8s ! 发现 %d 处疑似凭据（warn-only，不阻断）：%s"
              % ("gitleaks", n, str(data.get("sample") or "")[:180]))
    else:
        print("  %-8s ✓ 0 泄露" % ("gitleaks",))


def _ocr_warn_gate(branch, worktree=""):
    """OCR 语义审查（告警模式，第 5 道门；默认关，DEVTOOL_OCR_GATE=1 开启）。

    2026-10-27 关羽线：阿里开源 Open Code Review（确定性工程 × Agent 混合）。
    纪律：**只告警不阻断**（连续 2 周假阳性率 <10% 再议 block）；
    默认关闭——每次审查消耗数万~数十万 token，只对"待合并分支 diff"按需开。
    任一环节失败（未安装/超时/JSON 坏）都只打印问号，绝不影响合并。
    """
    if str(os.environ.get("DEVTOOL_OCR_GATE", "")).strip().lower() not in ("1", "true", "yes", "on"):
        print("  %-8s - 未启用（DEVTOOL_OCR_GATE=1 开启，warn 不阻断）" % "OCR语义审查")
        return
    exe = shutil.which("ocr")
    if not exe:
        print("  %-8s ? 未安装 ocr（npm i -g @alibaba-group/open-code-review），忽略" % "OCR语义审查")
        return
    out_json = os.path.join(tempfile.gettempdir(), "ocr_merge_%s.json" % branch.replace("/", "_"))
    try:
        cwd = worktree if worktree and os.path.isdir(worktree) else REPO
        r = subprocess.run(
            [exe, "review", "--from", "master", "--to", branch,
             "--format", "json", "--output", out_json],
            cwd=cwd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=240)
        if not os.path.isfile(out_json):
            print("  %-8s ? OCR 未产出 JSON（rc=%s），忽略：%s"
                  % ("OCR语义审查", r.returncode, ((r.stderr or r.stdout) or "")[-160:]))
            return
        data = json.load(open(out_json, encoding="utf-8"))
        if str(data.get("status") or "").lower() == "skipped":
            print("  %-8s - 跳过（%s；warn 不阻断）"
                  % ("OCR语义审查", data.get("message") or "无待审项"))
            return
        summary = data.get("summary") or {}
        comments = data.get("comments") or []
        sev = {}
        for c in comments:
            k = str(c.get("severity") or "?")
            sev[k] = sev.get(k, 0) + 1
        print("  %-8s ⚠ comments=%d %s tokens=%s elapsed=%s（warn 不阻断，JSON=%s）"
              % ("OCR语义审查", len(comments),
                 " ".join("%s=%d" % (k, v) for k, v in sorted(sev.items())),
                 summary.get("total_tokens", "?"), summary.get("elapsed", "?"), out_json))
        for c in comments[:8]:
            print("    [%s/%s] %s:%s %s"
                  % (c.get("severity"), c.get("category"), c.get("path"),
                     c.get("start_line") or c.get("end_line") or "?",
                     str(c.get("content") or "").replace("\n", " ")[:120]))
    except Exception as e:  # noqa: BLE001 - warn 门绝不阻断合并
        print("  %-8s ? OCR 审查失败（忽略，不阻断）：%s" % ("OCR语义审查", str(e)[:160]))



def _visual_warn_gate(branch, worktree=""):
    """组件视觉基线门（warn-only，第六道；绝不阻断合并）。

    检查 UI Kit 资产/示例页/截图与 manifest hash 是否一致；若分支改了
    UI Kit 但没更新基线，只打印告警。失败/缺 manifest 也放行。
    """
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(REPO, "tools", "ui_visual_gate.py"),
             "--branch", branch, "--warn-only", "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        data = json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}
        warns = data.get("warnings") or []
        if warns:
            print("  %-8s ⚠ %s（warn-only，不阻断）"
                  % ("组件视觉基线", "；".join(str(w) for w in warns[:3])))
        else:
            print("  %-8s ✓ 基线资产一致" % "组件视觉基线")
    except Exception as exc:  # noqa: BLE001 - warn 门绝不阻断
        print("  %-8s ? 检查失败（忽略，不阻断）：%s"
              % ("组件视觉基线", str(exc)[:120]))


def _git(*args, cwd=REPO, timeout=None):
    try:
        r = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        # 统一走 SystemExit：调用方都按"git 失败"处理，不额外引入异常类型
        raise SystemExit("git %s 超时（%ss）" % (" ".join(args), timeout))
    if r.returncode != 0:
        raise SystemExit("git %s 失败: %s" % (" ".join(args),
                                             (r.stderr or r.stdout).strip()))
    return r.stdout.strip()


def _heal_trailing_commas(text):
    """移除 JSON 中对象/数组末尾的尾逗号（引号感知，K-ORG-149/151/152 三现自愈）。

    并行会话手工写 tasks.json 常留 ``"note": "...",\n }`` 尾逗号 → JSONDecodeError
    使 ``new/merge`` 崩溃。仅当逗号在字符串外、且其后非空白字符是 ``}`` 或 ``]``
    才移除；字符串内的 ``,`` 不动（esc 处理 ``\\`` 转义）。
    """
    out = []
    i = 0
    n = len(text)
    in_str = False
    esc = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1  # 尾逗号：跳过不输出
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _load_tasks_json():
    """读 tasks.json 并自愈尾逗号；非尾逗号类结构损坏照常抛错（fail-visible）。

    自愈只移除尾逗号，不写盘——本仓任何写路径（new/review/merge/drop）都会经
    ``_save`` 原子固化；只读路径（list/board）无需回写，避免再引发写竞争。
    """
    with open(TASKS, encoding="utf-8") as f:
        raw = f.read()
    try:
        return json.loads(raw)
    except ValueError:
        healed = _heal_trailing_commas(raw)
        if healed == raw:
            raise
        try:
            return json.loads(healed)
        except ValueError:
            raise


def _tasks():
    if os.path.exists(TASKS):
        return _load_tasks_json()
    return {}


def _lock_fd(fd):
    """跨平台非阻塞独占锁；被占用时抛 OSError，由 _tasks_file_lock 重试。

    2026-11-07 K-RELEASE-20261106：原实现无条件 ``import msvcrt``，Linux 生产机
    （CentOS 7）上 ``cmd_new`` / ``_save`` 必崩 ModuleNotFoundError，19 个
    devtask 回归用例在服务器全挂。按 os.name 分派：Windows 保留 msvcrt.locking
    字节锁，POSIX 用 fcntl.flock；两者均在进程退出/fd 关闭时由 OS 自动释放。
    """
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_fd(fd):
    """释放 _lock_fd 加的锁（失败不影响主流程；close(fd) 亦会兜底释放）。"""
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def _tasks_file_lock(timeout_s=20.0):
    """tasks.json 读-改-写互斥（2026-10-30 P45；2026-11-07 跨平台化）。

    门禁并行后多会话可同时 merge 并各自 _save；原「读盘→合并→写盘」在并发
    窗口内可能丢字段。锁由进程退出/崩溃时 OS 自动释放，无 O_EXCL+unlink 的
    残留锁竞态（实证：Windows 上 unlink 会被并发读句柄阻塞，导致锁永久残留、
    后来者超时）。
    """
    os.makedirs(WT_DIR, exist_ok=True)
    path = os.path.join(WT_DIR, "tasks.json.lock")
    deadline = time.time() + timeout_s
    fd = None
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_RDWR)
        except OSError:
            fd = None
        if fd is not None:
            try:
                _lock_fd(fd)
                break
            except OSError:
                try:
                    os.close(fd)
                except OSError:
                    pass
                fd = None
        if time.time() >= deadline:
            raise TimeoutError("tasks.json 锁等待超时（%s）" % path)
        time.sleep(0.02)
    try:
        yield
    finally:
        try:
            _unlock_fd(fd)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass


def _save(tasks, *, drop_keys=(), changed=None):
    """原子保存并保留并发进程已登记的工单。

    ``changed``：本进程实际改动的键集合；只写这些键（见 2026-10-26 并发漂移修复）。
    ``changed=None`` 保留整包覆盖语义（兼容内部调用）。
    2026-10-30 P45：读-改-写整段加文件名锁（门禁并行后多会话同时 merge/_save）。
    """
    os.makedirs(WT_DIR, exist_ok=True)
    with _tasks_file_lock():
        current = {}
        if os.path.exists(TASKS):
            try:
                current = _load_tasks_json()
            except (OSError, ValueError):
                current = {}
        merged = dict(current)
        if changed is None:
            merged.update(tasks)
        else:
            for key in changed:
                if key in tasks:
                    merged[key] = tasks[key]
                else:
                    merged.pop(key, None)
        for key in drop_keys:
            merged.pop(key, None)
        fd, tmp = tempfile.mkstemp(prefix="tasks.", suffix=".tmp", dir=WT_DIR)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(merged, f, ensure_ascii=False, indent=1)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, TASKS)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)


_EV_RE = re.compile(r"#ev:(commit|file|trace):([^\s，。；,;]+)")


def _ev_precheck(desc, tasks):
    """K-ORG-090/T2：开单 desc 中 #ev 证据引用预检（087/T2 spec 模板侧）。
    commit→git cat-file -e 校验 sha；file@sha→cat-file -e sha:path；
    trace→tasks.json 在册。只 WARN 不拦单（fail-open）——坏引用到产码侧
    由 agent_coder.resolve_evidence fail-visible 标注，此处提前一层暴露给开单人。"""
    warns = []
    for kind, ref in _EV_RE.findall(desc):
        if kind == "commit":
            r = subprocess.run(["git", "cat-file", "-e", ref + "^{commit}"],
                               cwd=REPO, capture_output=True)
            if r.returncode != 0:
                warns.append("#ev:commit:%s 不在仓库（检查 sha）" % ref)
        elif kind == "file":
            path, _, sha = ref.rpartition("@")  # rpartition 返回 (head, sep, tail)：head=路径 tail=sha
            if not sha or not path:
                warns.append("#ev:file:%s 格式应为 路径@sha" % ref)
                continue
            r = subprocess.run(["git", "cat-file", "-e", "%s:%s" % (sha, path)],
                               cwd=REPO, capture_output=True)
            if r.returncode != 0:
                warns.append("#ev:file:%s@%s 该提交下不存在" % (path, sha))
        else:  # trace
            if ref not in tasks:
                warns.append("#ev:trace:%s 不在工单账本" % ref)
    return warns


_VAGUE_RE = re.compile(r"正确处理|合理|酌情")


def _ears_lint(desc):
    """K-ORG-080/N5：spec 验收判据 EARS 化 lint（模板见 knowledge_docs/spec_ears_template.md）。
    仅当 desc 含「## 验收判据」段时检查（无段=旧式单，直接跳过返回 []）：
      ① 判据行（"- WHEN" 开头）不足 3 条；
      ② 判据行含模糊词（正确处理/合理/酌情/句尾「等」）→ 逐行 WARN 不可断言；
      ③ 有验收判据段但缺「## 非目标」段（防 scope 蔓延）；
      ④ 缺「## QA1 判读指令」段（QA1 逐条判定依赖，K-ORG-071/072）。
    只 WARN 不拦单（fail-open，与 _ev_precheck 同款）。"""
    if "## 验收判据" not in desc:
        return []
    warns = []
    m = re.search(r"## 验收判据[^\n]*\n(.*?)(?=\n## |\Z)", desc, re.S)
    body = m.group(1) if m else ""
    crit = [ln for ln in body.splitlines()
            if ln.lstrip().startswith("- WHEN")]
    if len(crit) < 3:
        warns.append("判据不足 3 条（N5 模板建议）")
    for ln in crit:
        if _VAGUE_RE.search(ln) or ln.rstrip().endswith("等"):
            warns.append("判据不可断言（模糊词）：%s" % ln.strip()[:60])
    if "## 非目标" not in desc:
        warns.append("有「## 验收判据」段但缺「## 非目标」段（防 scope 蔓延）")
    if "## QA1 判读指令" not in desc:
        warns.append("缺「## QA1 判读指令」段（QA1 逐条判定依赖，K-ORG-071/072）")
    return warns


def _load_owner_domain():
    """读合法 owner 取值域登记表（registry/owner_domain.json）。

    缺文件/坏 JSON 一律降级为内置默认（标签 ``[UNOWNED]``，外部工具代号不视为合法）
    ——登记表坏了只让降级打标签这条路退化，建单本身不受影响。
    """
    try:
        d = json.load(open(OWNER_DOMAIN, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        d = {}
    if not isinstance(d, dict):
        d = {}
    if not isinstance(d.get("external_tools"), dict):
        d["external_tools"] = {}
    if not isinstance(d.get("unowned_tag"), str) or not d["unowned_tag"]:
        d["unowned_tag"] = "[UNOWNED]"
    # D5-C：profile 席位（不占 org.json 编制的合法 owner 代号）
    if not isinstance(d.get("profile_seats"), dict):
        d["profile_seats"] = {"seats": []}
    if not isinstance(d["profile_seats"].get("seats"), list):
        d["profile_seats"]["seats"] = []
    return d


def _profile_seat_members(domain=None):
    """D5-C：合法 owner 的 profile 席位代号（不占 org.json 编制）。

    治理席以 profile 角色存在（如 ZHAOYUN），不进 org.json 编制表，但仍需能作为
    工单 owner 落账、被统计识别——否则每单都被判非法打 ``[UNOWNED]``。本函数只读
    ``owner_domain.json`` 的 ``profile_seats.seats``（大小写/空白归一），不改
    org.json 派生口径（``_owner_domain_members`` 仍为纯 org 成员池）。
    """
    domain = domain if isinstance(domain, dict) else _load_owner_domain()
    seats = ((domain.get("profile_seats") or {}).get("seats") or [])
    out = set()
    for s in seats:
        if isinstance(s, str) and s.strip():
            out.add(s.strip().upper())
    return out


def _owner_domain_members():
    """合法 owner 成员池＝org.json 各部门 ``agents`` ∪ 各部门 ``lead`` ∪ ``chief.agent_id``。

    动态派生而非硬编码白名单：org.json 加人即刻生效，改代码零成本（工单 WO-TF-03
    验收判据 4）。

    **不可判定时返回 None（不是空集）**——判据是「**部门层贡献 0 个成员**」，而不是
    「总池为空」。理由：``chief.agent_id`` 是 org.json 里的常量（不论部门是否有人都
    恒在），故「池里只剩 chief」**恒等价于部门层一个人都没派出来**。这与「缺
    ``departments`` 键」是**同一个观测特征**（只捞得到 chief、域规模=1，看着像读到
    了东西，实为部分可用，比完全读不出更危险），必须同判不可判定，不能一个判非法
    一个判不可判定——否则读取侧的临时故障/未建全的部门层会让**全量新建单被批量误打**
    ``[UNOWNED]`` 标签。org.json 缺失 / 坏 JSON / 缺 ``departments`` / 部门层全空，
    一律按「读不到权威域」处理。调用方据此跳过打标签、只留痕（见 ``_owner_check``
    与 ``cmd_new``）。
    """
    try:
        org = json.load(open(ORG_JSON, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None                      # 文件缺失或坏 JSON → 不可判定
    if not isinstance(org, dict) or "departments" not in org:
        return None                      # 结构异常/无 departments → 不可判定
    members = set()

    def _add(v):
        if isinstance(v, str) and v.strip():
            members.add(v.strip().upper())

    for dept in org.get("departments") or []:
        if not isinstance(dept, dict):
            continue
        for a in dept.get("agents") or []:
            _add(a)
        _add(dept.get("lead"))
    if not members:
        # 部门层零成员（含 departments=[]、各部 agents/lead 全空）→ 不可判定。
        # 与「缺 departments 键」同判：二者观测特征一致（只剩 chief 常量）。
        return None
    _add((org.get("chief") or {}).get("agent_id"))
    return members


def _owner_check(owner):
    """owner 落账取值域校验（WO-TF-03：老田裁定「先立取值域，再校验」）。

    合法域＝org.json 动态成员池 ∪ 已裁定合法的外部工具代号 ∪ **profile 席位代号**
    （D5-C：治理席不占编，见 ``_profile_seat_members``）——``external_tools.status``
    为 ``legal`` 时其 ``observed`` 才计入；只接受 ``legal``（工单用词），当前是
    ``pending_ruling``，一律不算合法（外部代号是否合法属后续裁定，本单不预断）。

    返回 ``(owner_ok, 取值域登记表, 合法域规模)``，``owner_ok`` 三态：
      * ``True``  —— 取值域可判定且owner 在域内：正常放行；
      * ``False`` —— 取值域可判定且owner ∉ 域（含 owner 为空）：非法，调用方降级打标签；
      * ``None``  —— **取值域不可判定**（org.json 缺失/坏 JSON/无 departments/空池）：
        无法判定 ≠ 非法，调用方**不判非法、不打标签**，仅留痕 ``owner_domain_unavailable=1``。
        例外：owner 命中本地 ``profile_seats`` 时代码已知其合法（部分可用），放行并计入规模。
    """
    domain = _load_owner_domain()
    profile = _profile_seat_members(domain)
    who = (owner or "").strip().upper()
    members = _owner_domain_members()
    if members is None:
        # org 不可判定：不下"非法"结论；但本地 profile 席位登记表仍是权威，
        # 命中即可判定为合法（否则治理席在 org 读不到时会被误标 [UNOWNED]）。
        if who and who in profile:
            return True, domain, len(profile)
        return None, domain, 0            # 不可判定：不下非法结论
    members |= profile
    ext = domain.get("external_tools") or {}
    if str(ext.get("status", "")).strip().lower() == "legal":
        for c in ext.get("observed") or []:
            if isinstance(c, str) and c.strip():
                members.add(c.strip().upper())
    return bool(who) and who in members, domain, len(members)


def _load_dev_gate():
    """读研发试点门配置（agent_platform/config/dev_owner_gate.json）。

    缺文件/坏 JSON/结构非法一律降级为默认 ``{"phase": "warn", "dept": "dev"}``
    —— 门配置损坏只能退回「只告警」，绝不能因此把研发单全量拒收（fail-safe）。
    ``phase`` 仅认 ``warn`` / ``block``（大小写与空白归一），其它字面量按 warn，
    避免 ``"BLOCK "``/笔误字面量被静默当成拒收（假红）或被静默忽略（假绿）。
    """
    try:
        gate = json.load(open(DEV_GATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001  缺文件/坏 JSON/编码异常都降级默认
        gate = {}
    if not isinstance(gate, dict):
        gate = {}
    if not isinstance(gate.get("dept"), str) or not gate["dept"].strip():
        gate["dept"] = "dev"
    phase = str(gate.get("phase") or "").strip().lower()
    gate["phase"] = phase if phase in ("warn", "block") else "warn"
    return gate


def _load_dev_routing():
    """读 dev 单路由规则（agent_platform/config/dev_routing_rules.json）。

    缺文件/坏 JSON/结构非法 → 返回 ``{"dev_dept": "dev", "dev_seats": []}``：
    ``dev_seats`` 为空即**不启用** owner 路由校验（向后兼容，旧调用/旧环境行为不变）。
    """
    try:
        routing = json.load(open(DEV_ROUTING, encoding="utf-8"))
    except Exception:  # noqa: BLE001  缺文件/坏 JSON/编码异常都降级为空规则
        routing = {}
    if not isinstance(routing, dict):
        routing = {}
    dept = routing.get("dev_dept")
    if not isinstance(dept, str) or not dept.strip():
        dept = "dev"
    seats = routing.get("dev_seats")
    norm = []
    if isinstance(seats, list):
        for s in seats:
            if isinstance(s, str) and s.strip() and s.strip() not in norm:
                norm.append(s.strip())
    return {"dev_dept": dept, "dev_seats": norm}


def _is_routing_exempt(desc):
    """路由规则例外判据（2026-10-27 老田批规则文档 §三）。

    例外只有三类：A0/老田显式指派、跨部门治理任务、研发部请求支援。
    为避免靠关键词猜语义，例外单必须在 desc 显式写 **``路由例外=``** 标记
    （如 ``路由例外=A0指派`` / ``路由例外=跨部门治理`` / ``路由例外=研发部请求支援``）；
    未写标记的一律按正常研发单判。规则文档：docs/plans/2026-10-27_研发单路由规则_ARCH1优先_v1.md。
    """
    return "路由例外=" in str(desc or "")


def _load_dev_authority():
    """读取研发域最高权限 charter 的审批段（缺文件/坏 JSON → fail-safe warn）。"""
    try:
        cfg = json.load(open(DEV_AUTHORITY, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    approval = cfg.get("approval")
    if not isinstance(approval, dict):
        approval = {}
    mode = str(approval.get("mode") or "warn").strip().lower()
    approval["mode"] = mode if mode in ("warn", "block") else "warn"
    approval.setdefault("approver", "ARCH1")
    if not isinstance(approval.get("alternates"), list):
        approval["alternates"] = ["DEV1"]
    cfg["approval"] = approval
    return cfg


def _approval_check(task, auth=None):
    """研发域合并审批判定（纯函数）：返回 (warnings, blocked)。

    - 非 dev 单不适用；
    - dev 单缺 `approver`（或不在授权人/代行人集合）→ 告警；mode=block 时拒收；
    - 自审自批（approver == owner）→ 告警；mode=block 时拒收。
    """
    auth = auth if isinstance(auth, dict) else _load_dev_authority()
    approval = auth.get("approval") if isinstance(auth.get("approval"), dict) else {}
    if str((task or {}).get("dept") or "").strip().lower() != "dev":
        return [], False
    allowed = {str(approval.get("approver") or "ARCH1").strip().upper()}
    for a in approval.get("alternates") or []:
        if isinstance(a, str) and a.strip():
            allowed.add(a.strip().upper())
    approver = str(task.get("approver") or "").strip().upper()
    owner = str(task.get("owner") or "").strip().upper()
    warnings = []
    if not approver:
        warnings.append("缺 ARCH1 验收署名（--approver）")
    elif approver not in allowed:
        warnings.append("approver=%s 不在授权集合（%s）"
                        % (approver, ",".join(sorted(allowed))))
    elif owner and owner == approver:
        warnings.append("自审自批：approver 与 owner 同为 %s" % approver)
    blocked = str(approval.get("mode") or "warn").lower() == "block" and bool(warnings)
    return warnings, blocked


def _dev_gate_check(dept, owner_ok, acceptance, gate=None, owner=None,
                    routing=None, desc=None):
    """研发试点门判定（纯函数：无 IO、无副作用，``gate/routing=None`` 时才读配置）。

    ``dept``：开单时显式声明的部门代号（``None`` = 旧式未声明单，不进门）；
    ``owner_ok``：``_owner_check`` 三态（True 合法 / False 非法 / None 不可判定）；
    ``acceptance``：``--acceptance`` 验收命令串（None/空白 = 未提供）；
    ``gate``：门配置，缺省读 ``_load_dev_gate()``；
    ``owner``：开单人（``AGENT_NAME``），用于 dev 路由规则校验；
    ``routing``：路由规则，缺省读 ``_load_dev_routing()``；
    ``desc``：工单描述，用于识别 ``路由例外=`` 例外标记（规则文档 §三）。

    返回 ``(warnings, blocked)``：
      * 仅当 ``dept`` 命中 ``gate["dept"]``（默认 ``dev``，大小写/空白归一）时生效，
        其它部门一律 ``([], False)`` —— 非研发单不受试点门影响；
      * ``owner_ok is False`` → 告警「owner 不在合法域」（``None`` = 取值域不可判定，
        不是非法，与 WO-TF-03 三态裁定一致，不告警不拒收）；
      * 无验收命令 → 告警「缺验收命令」；
      * 路由规则：``owner`` 非空且不在 ``routing["dev_seats"]``，且 desc 未带
        ``路由例外=`` 标记 → 告警「owner 不在研发部（路由规则：新 dev 需求派 ARCH1/组长）」；
      * ``phase == "block"`` 且（owner 非法 或 缺验收命令 或 路由违规）
        → ``blocked=True``，调用方必须在建 worktree **之前**拒收（避免留空 worktree）。
    """
    gate = gate if isinstance(gate, dict) else _load_dev_gate()
    target = str(gate.get("dept") or "dev").strip().lower()
    if (dept or "").strip().lower() != target:
        return [], False
    has_acceptance = bool((acceptance or "").strip())
    warnings = []
    if owner_ok is False:
        warnings.append("owner 不在合法域")
    if not has_acceptance:
        warnings.append("缺验收命令")
    routing = routing if isinstance(routing, dict) else _load_dev_routing()
    dev_dept = str(routing.get("dev_dept") or "dev").strip().lower()
    dev_seats = {s.strip().upper() for s in (routing.get("dev_seats") or [])
                 if isinstance(s, str) and s.strip()}
    owner_name = (owner or "").strip().upper()
    routing_violated = False
    if (dev_seats and owner_name and dev_dept == target
            and owner_name not in dev_seats and not _is_routing_exempt(desc)):
        warnings.append("owner 不在研发部（路由规则：新 dev 需求派 ARCH1/组长）")
        routing_violated = True
    phase = str(gate.get("phase") or "warn").strip().lower()
    blocked = (phase == "block"
               and (owner_ok is False or not has_acceptance or routing_violated))
    return warnings, blocked


# ===== 2026-10-31 WO-WORKTREE-MECH-20261031：worktree 收口机制 =====
# 背景：创建速率 ~4 棵/小时 > 清理速率（10-31 已 60+ 棵）。三条闸：
#   1) merge 成功即回收 worktree+分支（--keep-worktree 可跳过，脏树保留）
#   2) prune 输出 age>TTL(72h) 标 [TTL-RED]（只标注，不自动删）
#   3) devtask new 并发上限（默认 60，env DEVTOOL_MAX_WORKTREES；>=80% WARN，>=上限硬拒）
WT_TTL_HOURS_DEFAULT = 72.0
WT_TTL_ENV = "DEVTOOL_WT_TTL_HOURS"
MAX_WORKTREES_DEFAULT = 65
MAX_WORKTREES_ENV = "DEVTOOL_MAX_WORKTREES"
KEEP_WORKTREE_ENV = "DEVTOOL_KEEP_WORKTREE"


def _ttl_hours():
    """TTL 阈值（小时）：env DEVTOOL_WT_TTL_HOURS 可调，非法/非正回落默认 72。"""
    raw = str(os.environ.get(WT_TTL_ENV) or "").strip()
    try:
        v = float(raw) if raw else WT_TTL_HOURS_DEFAULT
    except (TypeError, ValueError):
        v = WT_TTL_HOURS_DEFAULT
    return v if v > 0 else WT_TTL_HOURS_DEFAULT


def _worktree_age_hours(path, task=None):
    """worktree 年龄（小时）：优先 tasks.json ``created``，缺失回退目录 mtime；取不到→None。"""
    created = str((task or {}).get("created") or "").strip()
    ts = None
    if created:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                ts = time.mktime(time.strptime(created[:len(time.strftime(fmt))], fmt))
                break
            except ValueError:
                continue
    if ts is None:
        try:
            ts = os.path.getmtime(path)
        except OSError:
            return None
    return max(0.0, (time.time() - ts) / 3600.0)


def _format_prune_wt(it, apply_=False):
    """prune 单行渲染：DRY/APPLY/SKIP + [TTL-RED] + age 标注（只读格式器，便于单测）。"""
    if it.get("skip"):
        tag = "SKIP"
    else:
        tag = "APPLY" if apply_ else "DRY"
    prefix = "  [%s]" % tag
    if it.get("ttl_red"):
        prefix += "[TTL-RED]"
    age = it.get("age_hours")
    suffix = ("（age=%.0fh）" % age) if age is not None else ""
    if it.get("skip"):
        return "%s %s  # %s%s" % (prefix, it["path"], it["reason"], suffix)
    return '%s git worktree remove "%s"  # %s%s' % (prefix, it["path"], it["reason"], suffix)


def _worktree_cap():
    """并发上限：env DEVTOOL_MAX_WORKTREES 可调，非法/非正回落默认 60。"""
    raw = str(os.environ.get(MAX_WORKTREES_ENV) or "").strip()
    try:
        cap = int(float(raw)) if raw else MAX_WORKTREES_DEFAULT
    except (TypeError, ValueError):
        cap = MAX_WORKTREES_DEFAULT
    return cap if cap > 0 else MAX_WORKTREES_DEFAULT


def _concurrency_verdict(count, cap):
    """ok / warn（>=80%）/ block（>=上限）。纯函数，便于单测。"""
    if not cap or cap <= 0:
        return "ok"
    if count >= cap:
        return "block"
    if count >= cap * 0.8:
        return "warn"
    return "ok"


def _active_worktree_count(repo=None):
    """当前在册任务 worktree 数（不含主树与空壳登记）；git 不可用返回 0（不拦单）。"""
    repo = repo or REPO
    try:
        wts = _prune_worktrees(repo)
    except SystemExit:
        return 0
    except Exception:  # noqa: BLE001
        return 0
    # 空壳登记（`.git` 指针已失）不该挤占并发名额：它既跑不了活，也无法回收出磁盘，
    # 只会让闸门长期虚高。见 _wt_pointer_ok 的判例说明。
    return len([w for w in wts
                if not _prune_same_path(w["path"], repo) and not w.get("dead")])


def _enforce_worktree_cap():
    """devtask new 前置并发闸：达上限硬拒、达 80% 只告警。"""
    cap, cur = _worktree_cap(), _active_worktree_count()
    verdict = _concurrency_verdict(cur, cap)
    if verdict == "block":
        raise SystemExit(
            "并发 worktree 数 %d 已达上限 %d（%s 可调）——先收口再开单：\n"
            "  python tools/devtask.py prune --dry-run --all   # 看清单（含 TTL-RED）\n"
            "  python tools/devtask.py prune --apply           # 授权会话回收"
            % (cur, cap, MAX_WORKTREES_ENV))
    if verdict == "warn":
        print("[warn] worktree 并发 %d/%d 已达 80%%：merge 后自动回收 + 每日 prune 收口"
              % (cur, cap), file=sys.stderr)


def _should_keep_worktree(flags):
    """--keep-worktree 或 env DEVTOOL_KEEP_WORKTREE=1 → 不自动回收。"""
    if "--keep-worktree" in flags:
        return True
    return str(os.environ.get(KEEP_WORKTREE_ENV) or "").strip().lower() in ("1", "true", "yes")


def _recycle_worktree(tid, task):
    """merge 成功后回收 worktree+分支；任何失败只返回原因，**不阻断合并**。"""
    wt = str((task or {}).get("worktree") or "").strip()
    br = str((task or {}).get("branch") or "").strip()
    if not wt or not br:
        return False, "无 worktree/分支登记"
    if not os.path.isdir(wt):
        return False, "worktree 目录不存在（可能已回收）"
    if not _wt_pointer_ok(wt):
        # 空壳目录：`.git` 指针没了，此时 `git status` 会向上读到主仓状态，
        # 把主树的未提交改动误当成这个 worktree 的脏（2026-11-08 踩过）。
        try:
            _git("worktree", "prune")
        except SystemExit:
            pass
        return False, "worktree 登记已死（.git 缺失），已 prune 解注册；残留目录需人工清理"
    try:
        st = _git("status", "--porcelain", cwd=wt)
    except SystemExit as e:
        return False, "git status 失败：%s" % str(e)[:120]
    dirty = [x for x in (st or "").splitlines() if x.strip()]
    if dirty:
        return False, "worktree 脏（%d 项未提交），保留待 owner 处理" % len(dirty)
    try:
        _git("worktree", "remove", wt, timeout=300)
    except SystemExit as e:
        # 删大目录超时/失败后，git 可能已经删掉 `.git` 指针却留下整棵树。
        # 这种壳不能再当"活 worktree"留在台账里（会被计入并发、且下次 status 假脏）。
        if not _wt_pointer_ok(wt):
            try:
                _git("worktree", "prune")
            except SystemExit:
                pass
            freed = _prune_rmtree(wt)
            return False, ("worktree 删到一半（指针已失），已解注册；"
                           "残留目录%s（%s）" % ("已清" if freed else "未清", str(e)[:80]))
        return False, "git worktree remove 失败：%s" % str(e)[:120]
    try:
        _git("branch", "-d", br)
    except SystemExit as e:
        return True, "worktree 已回收；分支删除失败（保留）：%s" % str(e)[:120]
    return True, "worktree + 分支已回收"


_TID_MAX_LEN = 64


def _validate_tid(tid):
    """校验新建工单的 TID，必须发生在任何副作用之前。

    历史事故：``devtask.py new --help`` 曾把 ``--help`` 当 TID 建单，留下
    ``--help-aborted-*`` 空单。这里同时挡住空串、选项形态、路径分隔符、
    空白/控制字符、``..``、超长等非法值；合法值保持原样返回。
    """
    if not isinstance(tid, str) or not tid:
        raise SystemExit("非法 TID：TID 不能为空")
    if tid != tid.strip():
        raise SystemExit("非法 TID %r：不能包含首尾空白" % tid)
    if len(tid) > _TID_MAX_LEN:
        raise SystemExit("非法 TID %r：长度不能超过 %d 字符" % (tid, _TID_MAX_LEN))
    if tid[0] in ("-", "."):
        raise SystemExit("非法 TID %r：不能以 '-' 或 '.' 开头" % tid)
    if ".." in tid or tid.endswith("."):
        raise SystemExit("非法 TID %r：不能包含 '..' 或以 '.' 结尾" % tid)
    if any(ch in "/\\:*?\"<>|" or ch.isspace() or ord(ch) < 32 for ch in tid):
        raise SystemExit("非法 TID %r：不能包含路径分隔符、空白或控制字符" % tid)
    if not all(ch.isalnum() or ch in "-_." for ch in tid):
        raise SystemExit("非法 TID %r：只允许字母/数字/中文、'_'、'.'、'-'" % tid)
    return tid


def cmd_new(tid=None, *desc):
    # 2026-11-06 FIX-DEVTASK-HELP：TID 校验必须先于 worktree cap/写盘/建分支。
    tid = _validate_tid(tid)
    # 2026-10-31 WO-WORKTREE-MECH：并发上限闸（在任何副作用之前，达上限直接拒单）
    _enforce_worktree_cap()
    # K-ORG-057：--project <pid> 可选参数；缺省归 general 线（显式提示，不拒收——
    # 保兼容；判据3的「拒收」落在「声明了但不存在的线」上）
    desc = list(desc)
    project = "general"
    if "--project" in desc:
        i = desc.index("--project")
        if i + 1 >= len(desc):
            raise SystemExit("--project 需要跟项目线 id（如 --project quant）")
        project = desc.pop(i + 1)
        desc.pop(i)
        _resolve_project(project)  # 未注册/已归档 → SystemExit 拒收
    elif desc:
        print("[warn] 未声明 --project，归入 general 线（建议显式带线："
              "new %s --project <pid> ...）" % tid)
    # 2026-10-27 WO-LEGION-DEVEXEC-20261027：--dept / --acceptance 可选，与 --project
    # 同风格从 desc 弹出；缺省 dept=None 时**完全不进研发试点门**（旧调用行为不变）。
    dept = None
    if "--dept" in desc:
        i = desc.index("--dept")
        if i + 1 >= len(desc):
            raise SystemExit("--dept 需要跟部门代号（如 --dept dev）")
        dept = desc.pop(i + 1)
        desc.pop(i)
    acceptance = None
    if "--acceptance" in desc:
        i = desc.index("--acceptance")
        if i + 1 >= len(desc):
            raise SystemExit("--acceptance 需要跟验收命令串"
                             "（如 --acceptance \"python -m pytest -q\"）")
        acceptance = desc.pop(i + 1)
        desc.pop(i)
    # 2026-10-27 WO-AUTHORITY-B2：研发域最高权限 charter 的验收署名（可选）。
    approver = None
    if "--approver" in desc:
        i = desc.index("--approver")
        if i + 1 >= len(desc):
            raise SystemExit("--approver 需要跟署名席位（如 --approver ARCH1）")
        approver = desc.pop(i + 1)
        desc.pop(i)
    # 2026-10-27 WO-HARNESS-O12（O2 任务契约 2.0）：可选契约字段，从 desc 弹出。
    # 与 --dept 同风格，缺省不落字段（旧调用字段集不变）。
    def _pop_opt(name):
        if name in desc:
            i = desc.index(name)
            if i + 1 >= len(desc):
                raise SystemExit("%s 需要跟取值" % name)
            val = desc.pop(i + 1)
            desc.pop(i)
            return val
        return None

    expected_output = _pop_opt("--expected-output")
    boundaries = _pop_opt("--boundaries")
    effort = _pop_opt("--effort")
    deps_raw = _pop_opt("--deps")
    # 2026-10-27 WO-AUTHORITY-D2：charter approval.mode=block 后，dev 单若未显式
    # 给 --approver，自动落 charter 默认署名（ARCH1），避免误建无署名单卡死在合并门。
    if dept is not None and not approver:
        _auth = _load_dev_authority()
        approver = str((_auth.get("approval") or {}).get("approver") or "").strip() or None
        if approver:
            print("[info] dev 单未给 --approver，按 charter 自动落默认署名：%s" % approver)
    desc = " ".join(desc) or "(无描述)"
    tasks = _tasks()
    if tid in tasks:
        raise SystemExit("工单 %s 已存在" % tid)
    # K-ORG-090/T2：#ev 引用预检（只 WARN 不拦单，fail-open）
    for w in _ev_precheck(desc, tasks):
        print("[warn] 证据引用预检: %s（不拦单；产码侧 resolve_evidence 会 fail-visible 标注）" % w)
    # K-ORG-080/N5：EARS 判据 lint（只 WARN 不拦单，fail-open）
    for w in _ears_lint(desc):
        print("[warn] EARS lint: %s" % w)
    # WO-TF-03：owner 取值域校验。老田裁定①「非法 owner 降级不拒写」——非法时给
    # desc 追加 [UNOWNED] 标签（已有则不重复追加）并经既有 task_start 事件留痕；
    # 合法 owner 行为与现状完全一致（不加标签、不留痕、字段不变）。
    # 取值域不可判定（owner_ok 为 None，org.json 缺失/坏/无 departments）时**不判非法、
    # 不打标签**——读不到权威域不等于域内无人，误标会批量污染新建单，仅留痕计数。
    #
    # 2026-10-27 WO-LEGION-DEVEXEC-20261027：owner 预判与研发试点门**前置到
    # `git worktree add` 之前** —— block 模式拒收时不落空 worktree（旧顺序为建完
    # worktree 才发现 owner 非法，拒收会留一个空 worktree 需人工清）。
    # 2026-10-27 WO-LEGION-P0WRAP：DEVTOOL_AGENT 归因强制 —— owner 取值链
    # AGENT_NAME > DEVTOOL_AGENT > unknown-agent（AGENTS §1「领单必设」三环境变量）；
    # 两者皆缺时事件 detail 带 attribution_missing=1（可统计、可追责，不静默）。
    _agent_name = os.environ.get("AGENT_NAME")
    _devtool_agent = os.environ.get("DEVTOOL_AGENT")
    attribution_missing = not (_agent_name or _devtool_agent)
    owner = _agent_name or _devtool_agent or "unknown-agent"
    owner_ok, domain, domain_size = _owner_check(owner)
    if owner_ok is False:
        tag = domain["unowned_tag"]
        if tag not in desc:
            desc = ("%s %s" % (desc, tag)).strip()
        print("[warn] owner=%s 不在合法取值域（域内 %d 员）——降级标注 %s，"
              "不拒单；责任部门待人工补登" % (owner, domain_size, tag),
              file=sys.stderr)
    elif owner_ok is None:
        print("[warn] 取值域不可判定（org.json 缺失/坏 JSON/无 departments）——"
              "不判非法、不打标签，仅留痕；owner=%s 原样落账" % owner,
              file=sys.stderr)
    # 研发试点门：只在显式声明 --dept 时启用（旧调用不进门 = 行为不变）。
    # warn 模式把告警打 stderr 后照常建单；block 模式缺合法 owner/验收命令**前置**
    # 拒收（SystemExit，此时尚未 worktree add，故不会留空 worktree）。
    gate_warnings = []
    gate_applies = False
    if dept is not None:
        gate = _load_dev_gate()
        routing = _load_dev_routing()
        gate_applies = (dept or "").strip().lower() == str(
            gate.get("dept") or "dev").strip().lower()
        gate_warnings, gate_blocked = _dev_gate_check(
            dept, owner_ok, acceptance, gate=gate, owner=owner, routing=routing,
            desc=desc)
        if gate_blocked:
            raise SystemExit(
                "研发试点门（phase=block）拒收 dept=%s：%s——未创建 worktree；"
                "补合法 owner / --acceptance 验收命令后重试"
                % (dept, "；".join(gate_warnings) or "gate 未过"))
        for w in gate_warnings:
            print("[warn] dev-gate: %s（phase=%s，只告警不拒收）"
                  % (w, gate.get("phase", "warn")), file=sys.stderr)
    branch = "task/" + tid
    wt = os.path.join(WT_DIR, tid)
    _git("worktree", "add", wt, "-b", branch)
    rec = {"desc": desc, "branch": branch, "worktree": wt,
           "status": "doing", "created": time.strftime("%F %T"),
           "project": project,
           "owner": owner}
    # 显式声明 --dept 的单才落 dept/acceptance_cmd 新字段（旧调用字段集不变——
    # WO-TF-03「合法路径字段与现状一致」回归用例锁定；acceptance 缺省落 null）。
    if dept is not None:
        rec["dept"] = dept
        rec["acceptance_cmd"] = acceptance
        # 仅 dev/声明 dept 的单一并落验收署名（旧调用字段集不变）。
        if approver:
            rec["approver"] = approver
        # O2 任务契约 2.0：可选字段（不填不落，保持旧调用字段集不变）
        if expected_output:
            rec["expected_output"] = expected_output
        if boundaries:
            rec["boundaries"] = boundaries
        if effort:
            rec["effort"] = effort
        if deps_raw:
            rec["deps"] = [x.strip() for x in str(deps_raw).replace("，", ",").split(",")
                           if x.strip()]
    tasks[tid] = rec
    _save(tasks, changed=(tid,))
    _ckpt(tid, "kai", tasks[tid]["owner"],
          {"desc": desc, "branch": branch, "worktree": wt,
           "project": project})
    # 留痕走既有 task_start 事件（不新造 kind：events.KINDS 是白名单，未注册 kind
    # 会被 emit 静默吞掉反而丢痕）。标记前置，保证 detail 截断后仍可检出。
    if owner_ok is None:
        detail = ("owner_domain_unavailable=1 owner=%s · %s（owner=%s·project=%s）"
                  % (owner, desc, owner, project))
    elif owner_ok:
        detail = "%s（owner=%s·project=%s）" % (desc, owner, project)
    else:
        detail = ("owner_degraded=1 owner=%s domain_size=%d · %s（owner=%s·project=%s）"
                  % (owner, domain_size, desc, owner, project))
    if attribution_missing:
        detail = "attribution_missing=1 · " + detail
    if gate_applies:
        # 研发试点单：detail 前缀标 [dev-gate]（截断后仍可检出）；warn 告警摘要前置，
        # 保证大 desc 截断也不会把试点门告警吃掉。
        gate_note = "[dev-gate]"
        if gate_warnings:
            gate_note += " " + "；".join(gate_warnings)
        detail = gate_note + " · " + detail
    _emit("task_start", tid, detail)
    print("工单 %s 已开：%s（分支 %s）owner=%s project=%s%s"
          % (tid, desc, branch, tasks[tid]["owner"], project,
             " dept=%s" % dept if dept is not None else ""))
    print("→ Agent 请 cd %s 内干活，完工跑 devtask.py merge %s" % (wt, tid))
    return 0


def _ahead_behind(branch):
    try:
        ab = _git("rev-list", "--left-right", "--count",
                  "master..." + branch)
        behind, ahead = ab.split()
        return int(ahead), int(behind)
    except SystemExit:
        return 0, 0


def cmd_list(*args):
    # K-ORG-057：--project <pid> 按线过滤；project 列入表
    # 2026-11-01 WO-DEVTASK-WTCHECK：--strict 附跑 W2-3 worktree 校验（默认行为不变）
    strict = "--strict" in args
    proj_filter = None
    args = [a for a in list(args) if a != "--strict"]
    if "--project" in args:
        i = args.index("--project")
        proj_filter = args[i + 1] if i + 1 < len(args) else None
        args = args[:i] + args[i + 2:]
    tasks = _tasks()
    if proj_filter:
        tasks = {k: v for k, v in tasks.items()
                 if v.get("project", "general") == proj_filter}
        print("（过滤：project=%s）" % proj_filter)
    print("%-10s %-8s %-10s %-20s %-24s %s" %
          ("工单", "状态", "项目线", "owner/分支", "描述", "ahead/behind·末提交"))
    for tid, t in sorted(tasks.items()):
        # 兼容历史/外部登记项：缺少分支时仍展示工单信息，不做 Git 统计。
        if "branch" not in t:
            print("%-10s %-8s %-10s %-20s %-24s %s"
                  % (tid, t.get("status", "?"), t.get("project", "general")[:8],
                     t.get("owner", "历史项")[:8] + "/历史项",
                     t.get("desc", "")[:22], "无分支"))
            continue
        ahead, behind = _ahead_behind(t["branch"])
        try:
            last = _git("log", "-1", "--format=%ar", t["branch"])
        except SystemExit:
            last = "?"
        print("%-10s %-8s %-10s %-20s %-24s +%d/-%d · %s"
              % (tid, t["status"], t.get("project", "general")[:8],
                 t.get("owner", "?")[:8] + "/" + t["branch"][5:15],
                 t.get("desc", "")[:22], ahead, behind, last))
    if not tasks:
        print("（无任务单——先 devtask.py new）")
    if strict:
        _viol, _active = _audit_worktrees()
        if _viol:
            print("STRICT FAIL：%d 张 active 单缺 worktree/分支（明细：devtask.py "
                  "audit-worktrees）" % len(_viol))
            return 1
        print("STRICT PASS：%d 张 active 单 worktree/分支齐全" % _active)
    return 0
# ===== 2026-11-01 WO-DEVTASK-WTCHECK-20261031：W2-3 worktree 校验 =====
# 规则来源：v2 计划 W2-3「一单一 owner；新单未建 worktree 不得 doing」。
# 校验对象：status ∈ doing/review 的 active 单（done/aborted 历史项不校验，不追溯旧账）。
# 三条规则：R1 目录存在（worktree 字段或 .worktrees/<TID>）；R2 在 git worktree list 在册；
#          R3 分支存在（branch 字段或 task/<TID>）。任一不满足即违规。
# 只读：仅 `git worktree list` / `git branch` 与读 tasks.json，不创建/删除/移动任何 worktree。
WTCHECK_ACTIVE_STATES = ("doing", "review")


def _worktree_violations(tasks, wt_dir, registered, branches):
    """W2-3 校验核心（纯函数，便于单测）。

    :param tasks: {tid: task_dict}
    :param wt_dir: 目标仓 .worktrees 目录
    :param registered: 已注册 worktree 绝对路径归一集合（normcase(abspath)）
    :param branches: 本地分支名集合
    :return: 违规清单 [{tid,status,owner,dir,branch,checks,missing}]
    """
    out = []
    for tid, t in sorted((tasks or {}).items()):
        t = t or {}
        st = str(t.get("status") or "")
        if st not in WTCHECK_ACTIVE_STATES:
            continue
        wt = str(t.get("worktree") or "").strip() or os.path.join(wt_dir, tid)
        br = str(t.get("branch") or "").strip() or ("task/" + tid)
        checks = {
            "dir": os.path.isdir(wt),
            "registered": os.path.normcase(os.path.abspath(wt)) in registered,
            "branch": br in branches,
        }
        if all(checks.values()):
            continue
        out.append({
            "tid": tid, "status": st, "owner": str(t.get("owner") or "?"),
            "dir": wt, "branch": br, "checks": checks,
            "missing": [k for k, v in checks.items() if not v],
        })
    return out


def _tasks_at(repo):
    """读目标仓 .worktrees/tasks.json（沿用尾逗号自愈；缺失/坏 JSON → 空）。"""
    p = os.path.join(repo, ".worktrees", "tasks.json")
    try:
        return _prune_load_tasks(p)
    except (OSError, ValueError):
        return {}


def _resolve_main_repo(repo=None):
    """定位主仓：在任务 worktree 内调用时回退到 git-common-dir 的父目录。

    背景：devtask 的 REPO 取自脚本自身路径，在 `.worktrees/<TID>` 里跑时
    REPO=worktree 根，`.worktrees/tasks.json` 不存在 → list/audit 会误报「无单」。
    本函数只读 git，不改任何状态；解析失败则原样返回。
    """
    repo = repo or REPO
    common = ""
    for extra in (["--path-format=absolute"], []):
        try:
            r = _prune_git(repo, "rev-parse", *extra, "--git-common-dir")
            lines = (r.stdout or "").strip().splitlines()
            common = lines[0].strip() if lines else ""
        except SystemExit:
            common = ""
        if common:
            break
    if common:
        if not os.path.isabs(common):
            common = os.path.abspath(os.path.join(repo, common))
        if os.path.isdir(common):
            parent = os.path.dirname(os.path.normpath(common))
            if (os.path.isdir(os.path.join(parent, ".worktrees"))
                    or os.path.isdir(os.path.join(parent, "portal"))):
                return parent
    return repo


def _audit_worktrees(repo=None):
    """跑一次 W2-3 校验（只读）→ (violations, active_count)。"""
    repo = _resolve_main_repo(repo)
    registered, branches = set(), set()
    try:
        for w in _prune_worktrees(repo):
            registered.add(os.path.normcase(os.path.abspath(w["path"])))
    except SystemExit:
        pass
    try:
        out = _prune_git(repo, "branch", "--format=%(refname:short)").stdout
        branches = {ln.strip() for ln in out.splitlines() if ln.strip()}
    except SystemExit:
        pass
    tasks = _tasks_at(repo)
    active = [t for t in tasks.values()
              if str((t or {}).get("status") or "") in WTCHECK_ACTIVE_STATES]
    return _worktree_violations(tasks, os.path.join(repo, ".worktrees"),
                                registered, branches), len(active)


def cmd_audit_worktrees(*args):
    """audit-worktrees [--json] [--quiet] [--repo <path>]：W2-3 校验（只读、不修不删）。

    rc=0 全合规；rc=1 存在违规（清单已打印，供 owner 二选一：重建 worktree / abort 收口）。
    """
    argv = list(args)
    repo = REPO
    if "--repo" in argv:
        i = argv.index("--repo")
        if i + 1 >= len(argv):
            raise SystemExit("--repo 需要跟仓库路径")
        repo = os.path.abspath(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    as_json = "--json" in argv
    quiet = "--quiet" in argv
    for f in argv:
        if f.startswith("--") and f not in ("--json", "--quiet"):
            raise SystemExit("audit-worktrees 未知参数：%s（支持 --json/--quiet/--repo）" % f)
    repo = _resolve_main_repo(repo)
    viol, active = _audit_worktrees(repo)
    if as_json:
        print(json.dumps({"repo": repo, "active": active,
                          "violations": viol, "ok": not viol,
                          "rc": 1 if viol else 0}, ensure_ascii=False, indent=1))
        return 1 if viol else 0
    if not quiet:
        print("== devtask audit-worktrees（W2-3）==")
        print("仓库：%s" % repo)
        print("active（doing/review）= %d；违规 = %d" % (active, len(viol)))
        for v in viol:
            miss = {"dir": "目录缺失", "registered": "未在 git worktree list 在册",
                    "branch": "分支缺失"}.get
            reasons = "、".join(miss(k, k) for k in v["missing"])
            print("  [VIOL] %-42s status=%-6s owner=%-10s %s"
                  % (v["tid"], v["status"], v["owner"], reasons))
            print("         期望：%s（分支 %s）" % (v["dir"], v["branch"]))
            print("         修法：devtask.py new %s 重建 worktree，或 devtask.py drop %s 收口"
                  % (v["tid"], v["tid"]))
    if viol:
        print("汇总：FAIL —— %d 张 active 单无 worktree/分支（只读校验，未修改任何 worktree）"
              % len(viol))
        return 1
    print("汇总：PASS —— active 单全部具备 worktree + 分支")
    return 0



def cmd_port(name):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from check_ports import REGISTRY, tcp_up, listening_ports
    used = set(REGISTRY) | set(listening_ports())
    cand = next(p for p in range(8100, 9999)
                if p not in used and not tcp_up(p))
    alloc = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "ports_allocated.json")
    reg = json.load(open(alloc, encoding="utf-8")) if os.path.exists(
        alloc) else {}
    if name in reg:
        print("%s 已分配：%d" % (name, reg[name]))
        return 0
    reg[name] = cand
    json.dump(reg, open(alloc, "w", encoding="utf-8"), ensure_ascii=False,
              indent=1)
    print("端口分配：%s → %d" % (name, cand))
    print("⚠ 立即写回 tools/check_ports.py REGISTRY（同一提交）："
          '%d: "%s",' % (cand, name))
    return 0


def _l2_exempt(tid, t, reason):
    """K-STOCK-416：--l2-ack 豁免落痕——approvals.db 审批单 + 返回 approval_id。

    五要素（命令全文+影响面+不可逆点+替代方案+回滚预案）由 --l2-reason
    带入，缺reason视为不合规（仍留痕但 note 标注，供审计翻旧账）。
    审批库异常 fail-open（WARN 不阻断）——人工已确认，note 侧仍留痕。
    """
    approver = os.environ.get("AGENT_NAME", "human")
    try:
        sys.path.insert(0, os.path.join(REPO, "agent_platform"))
        from engine.approvals import ApprovalStore
        from contracts.objects import Approval
        store = ApprovalStore(os.path.join(
            REPO, "agent_platform", "data", "approvals", "approvals.db"))
        try:
            head = _git("rev-parse", t["branch"])
            ap = Approval(
                target_type="devtask_l2_exemption",
                target_id=tid,
                from_status="review",
                to_status="merged",
                decision="approved",
                decided_by=approver,
                reason=reason or "(未填五要素——不合规豁免，审计应翻旧账)",
                idempotency_key="devtask_l2:%s:%s" % (tid, head),
                content_hash=head)
            ap, replayed = store.record(ap)
            return ap.approval_id, replayed
        finally:
            store.close()
    except Exception as e:  # noqa: BLE001  审批库不可用不阻断人工已确认的合并
        print("[WARN] 审批单写入失败（不阻断合并）：%s" % e)
        return "(审批库异常:%s)" % e, False


IDE_BUNDLE_REL = "portal/agent_ide_bundle.json"


# W5 P1-6 Phase2（K-ORG-20261031-BUNDLEP2B，2026-10-31）：bundle 生成物不再 tracked。
# 重建后只刷新 _vault 部署副本（8103/独立 IDE 数据源），绝不 git add / commit；
# 历史提交保留，git show 仍可追溯。旧 `_ide_bundle_commit_args`（pathspec 收尾提交）
# 在 Phase2 一并移除——不再存在「每 merge 一条 bundle 提交」的代码路径。
IDE_BUNDLE_DEPLOY = r"C:\项目\_vault\legion_ide\agent_ide_bundle.json"


def _bundle_deploy_target():
    """部署副本路径（DEVTOOL_IDE_BUNDLE_DEPLOY 可覆盖，供测试/异机使用）。"""
    return os.environ.get("DEVTOOL_IDE_BUNDLE_DEPLOY") or IDE_BUNDLE_DEPLOY


def _refresh_bundle_deploy_copy(repo=None, deploy=None):
    """把 portal/agent_ide_bundle.json 复制到 _vault 部署副本（失败不抛）。

    返回 ``{"ok","src","dst","bytes","error"}``。纯文件复制（不 git add/commit）；
    供 devtask merge 收尾与单测复用。
    """
    src = Path(repo or REPO) / IDE_BUNDLE_REL
    dst = Path(deploy or _bundle_deploy_target())
    try:
        if not src.is_file():
            return {"ok": False, "src": str(src), "dst": str(dst),
                    "bytes": 0, "error": "src_missing"}
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
        return {"ok": True, "src": str(src), "dst": str(dst),
                "bytes": dst.stat().st_size, "error": ""}
    except OSError as e:
        return {"ok": False, "src": str(src), "dst": str(dst), "bytes": 0,
                "error": ("%s: %s" % (type(e).__name__, e))[:160]}


# W5 P1-6 Phase1（2026-10-31）：bundle 自动重建去噪——只有本次 merge 触及
# agent 源（org.json / team_* / hr/ / prompts/）才重烘；其余 merge 跳过。
# 依据：W5 报告实测 8 日 852/3,896 = 21.87% 提交是 bundle 收尾，其中多数仅
# 时间戳/工单计数抖动。
# W5 Phase2（K-ORG-20261031-BUNDLEP2B）：进一步去 tracked——生成物 untracked/ignored，
# 重建仅刷新 _vault 部署副本，不产生任何 git 提交；历史提交保留可追溯。
BUNDLE_SOURCE_EXACT = ("agent_platform/registry/org.json",)
BUNDLE_SOURCE_PREFIXES = (
    "agent_platform/registry/team_",
    "agent_platform/hr/",
    "agent_platform/prompts/",
)


def _bundle_sources_changed(changed_files):
    """本次 merge 改动是否触及 agent 源（决定 IDE bundle 是否重烘+提交）。"""
    for raw in changed_files or []:
        f = str(raw or "").replace("\\", "/")
        while f.startswith("./"):
            f = f[2:]
        f = f.lstrip("/")
        if not f:
            continue
        if f in BUNDLE_SOURCE_EXACT:
            return True
        if any(f.startswith(prefix) for prefix in BUNDLE_SOURCE_PREFIXES):
            return True
    return False


def _merge_changed_files(tid):
    """merge 前计算 task/<tid> 相对 master 的改动文件（失败返回 []，保守跳过重烘）。"""
    try:
        tools_dir = os.path.dirname(os.path.abspath(__file__))
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        import merge_gated as _mg
        return _mg.branch_changed_files(tid)
    except Exception as exc:  # noqa: BLE001
        print("  IDE bundle   ⚠ 改动文件计算失败（按源未变跳过）：%s" % str(exc)[:120])
        return []


MERGE_LOCK_NAME = "devtask_merge.lock"
MERGE_LOCK_STALE_S = 1800


def _merge_lock_path():
    git_dir = os.path.join(REPO, ".git")
    return os.path.join(git_dir if os.path.isdir(git_dir) else REPO, MERGE_LOCK_NAME)


def _pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        # Windows 的 os.kill(pid, 0) 会走 TerminateProcess（sig=0 会杀进程！），
        # 不能用来探活；改用 OpenProcess + GetExitCodeProcess。
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return k32.GetLastError() == 5  # 5=ACCESS_DENIED：进程存在但无权查
            try:
                code = ctypes.c_ulong()
                ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
                return bool(ok) and code.value == 259  # STILL_ACTIVE
            finally:
                k32.CloseHandle(h)
        except Exception:  # noqa: BLE001
            return True  # 保守：不误收活锁
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:  # noqa: BLE001  权限/平台异常按“可能活着”保守处理
        return True


def _acquire_merge_lock(tid, timeout_s=None, path=None):
    """获取仓库级合并锁（文件 O_EXCL + pid/超时）。

    串行化 `git merge` 临界区，避免并行会话 HEAD/index 竞争（今日多次
    update_ref failed / MERGE_HEAD exists / unmerged files 的根因）。
    陈旧锁（pid 已死或超过 STALE_S）可回收；等待超时则 fail-visible 返回 None，
    调用方拒绝合并（不冒进）。返回锁文件路径或 None。
    """
    path = path or _merge_lock_path()
    if timeout_s is None:
        try:
            timeout_s = float(os.environ.get("DEVTOOL_MERGE_LOCK_TIMEOUT", "600"))
        except ValueError:
            timeout_s = 600.0
    start = time.time()
    waited = False
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            meta = {}
            try:
                with open(path, encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, ValueError):
                meta = {}
            age = time.time() - float(meta.get("start") or 0)
            if (not _pid_alive(meta.get("pid"))) or age > MERGE_LOCK_STALE_S:
                try:
                    os.unlink(path)
                    print("[merge-lock] 回收陈旧锁（pid=%s age=%.0fs）"
                          % (meta.get("pid"), age))
                    continue
                except OSError:
                    pass
            if time.time() - start > timeout_s:
                print("[merge-lock] 等待超时（%.0fs），未执行合并（防 HEAD 竞争）；"
                      "锁持有者 pid=%s tid=%s" % (timeout_s, meta.get("pid"),
                                                  meta.get("tid")), file=sys.stderr)
                return None
            if not waited:
                print("[merge-lock] 仓库合并锁被占用（pid=%s tid=%s），等待…"
                      % (meta.get("pid"), meta.get("tid")))
                waited = True
            time.sleep(2)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(),
                       "host": os.environ.get("COMPUTERNAME", ""),
                       "tid": tid, "start": time.time()}, f, ensure_ascii=False)
        print("[merge-lock] 已获取（pid=%s tid=%s）" % (os.getpid(), tid))
        return path


def _release_merge_lock(path):
    """释放本进程持有的锁；非本进程持有则不动（防误删）。"""
    if not path:
        return False
    try:
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
        if int(meta.get("pid") or -1) != os.getpid():
            return False
        os.unlink(path)
        print("[merge-lock] 已释放")
        return True
    except (OSError, ValueError):
        return False


MERGE_RACE_MARKERS = (
    "cannot lock ref",
    "update_ref failed",
    "unable to update local ref",
    "MERGE_HEAD exists",
    "unmerged files",
)


def _is_merge_head_race(msg: str) -> bool:
    """判断 git merge 失败是否属于并行会话 HEAD/index 竞态（可安全重试）。"""
    m = (msg or "").lower()
    return any(k.lower() in m for k in MERGE_RACE_MARKERS)


def _dirty_overlap_guard(tid, branch):
    """脏文件快速失败（2026-10-30 P49）。

    背景：并行会话在 master 工作区留下的未提交文件，若与本单改动路径重叠，
    `git merge` 最后一步才报 "local changes would be overwritten"，此前静默
    白跑全部测试门（历史实测 B4 因此停摆 17h+）。本守卫在门禁之前快速失败并
    列出重叠文件，避免盲等。

    返回 (ok, overlap_paths)。
    """
    try:
        changed = set()
        out = _git("diff", "--name-only", "master...%s" % branch)
        for ln in (out or "").splitlines():
            if ln.strip():
                changed.add(ln.strip())
        if not changed:
            return True, []
        st = subprocess.run(["git", "-C", REPO, "status", "--porcelain"],
                            capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        if st.returncode != 0:
            return True, []  # 状态不可得时不阻断（fail-open，仅此守卫）
        dirty = set()
        for ln in (st.stdout or "").splitlines():
            if not ln.strip():
                continue
            path = ln[3:].strip().strip('"').replace("\\", "/")
            if " -> " in path:  # rename：two-sided
                path = path.split(" -> ")[-1].strip().strip('"')
            dirty.add(path)
        return (not (changed & dirty)), sorted(changed & dirty)
    except SystemExit:
        return True, []


def _abort_half_merge():
    """清理 git 半合并状态（MERGE_HEAD 存在说明 merge 已改索引但提交失败）。

    2026-10-30 P48：钩子拒提交后 devtask 抛错会残留 MERGE_HEAD，后续所有
    merge 都会失败，必须兜底 abort。
    """
    try:
        if not os.path.exists(os.path.join(REPO, ".git", "MERGE_HEAD")):
            return
        _git("merge", "--abort")
        print("[merge] 已清理半合并状态（merge --abort），可重跑")
    except SystemExit:
        pass
    except OSError:
        pass


def _merge_with_retry(tid, branch, desc, tries=3, sleep_fn=None, git_fn=None,
                      emit_fn=None):
    """HEAD 竞态自动 abort+退避重试；真实冲突/其它错误立即抛出不重试。"""
    import time as _time
    sleep_fn = sleep_fn or _time.sleep
    git_fn = git_fn or _git
    emit_fn = emit_fn or _emit
    last = None
    for i in range(1, max(1, tries) + 1):
        try:
            git_fn("merge", "--no-ff", branch, "-m",
                   "merge task/%s: %s" % (tid, desc))
            return True
        except SystemExit as e:
            last = e
            msg = str(e)
            if not _is_merge_head_race(msg):
                _abort_half_merge()
                raise
            # 清理半合并状态（含 index.lock 由 git 自行处理；无合并时 abort 报错忽略）
            try:
                _git("merge", "--abort")
            except SystemExit:
                pass
            if i < max(1, tries):
                print("[merge-retry] 检测到 HEAD/index 竞态，%d/%d 重试（退避 %ds）"
                      % (i, tries, 5 * i))
                try:
                    emit_fn("guard_fail", tid,
                            "merge HEAD race，自动重试 %d/%d" % (i, tries))
                except Exception:  # noqa: BLE001
                    pass
                sleep_fn(5 * i)
    raise last if last else SystemExit("merge failed")


def _auto_rebuild_ide_bundle(tid, changed_files=None):
    """merge 后按需重建 portal/agent_ide_bundle.json（失败只告警，不影响合并）。

    W5 Phase1 门槛（保留）：仅本次 merge 触及 agent 源才重建，其余 merge 跳过。
    W5 Phase2（K-ORG-20261031-BUNDLEP2B）：bundle 生成物已 untracked + ignored；
    重建后**只刷新 _vault 部署副本，不 git add / 不 commit**（bundle 提交 0 增长）。
    dry-run：``DEVTOOL_IDE_BUNDLE_DRY_RUN=1`` 只打印决策，不重建、不复制。
    """
    try:
        if changed_files is None:
            changed_files = _merge_changed_files(tid)
        if not _bundle_sources_changed(changed_files):
            print("  IDE bundle   - 源未变，跳过重建（W5 Phase1 门槛）")
            return
        if os.environ.get("DEVTOOL_IDE_BUNDLE_DRY_RUN") == "1":
            print("  IDE bundle   [dry-run] 源变（%d 文件）→ 将重建并刷新部署副本；"
                  "不提交（Phase2）" % len(changed_files or []))
            return
        tool = os.path.join(REPO, "tools", "build_agent_ide_bundle.py")
        r = subprocess.run([sys.executable, "-X", "utf8", tool], cwd=REPO,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=GUARD_ENV, timeout=180)
        if r.returncode != 0:
            print("  IDE bundle   ⚠ 重建失败（不阻断）：%s" % (r.stderr or r.stdout).strip()[:160])
            return
        dep = _refresh_bundle_deploy_copy(REPO)
        if dep["ok"]:
            print("  IDE bundle   ✓ 已重建（生成物 untracked/ignored，不产生提交）；"
                  "部署副本已刷新（%d bytes）" % dep["bytes"])
        else:
            print("  IDE bundle   ⚠ 已重建，但部署副本刷新失败（不阻断）：%s" % dep["error"])
    except Exception as e:  # noqa: BLE001
        print("  IDE bundle   ⚠ 重建异常（不阻断）：%s" % str(e)[:160])


def _gate_script_for(tid):
    """返回任务 worktree 内的 test_gate.ps1；缺失返回空串。

    2026-10-30 branch-gate：质量闸必须在**待合并分支/worktree**跑。
    旧实现固定跑 REPO 根（master）→ master 上一旦有失败测试，新分支即使修好
    也会被旧失败挡住，形成“修不了也合不了”的死锁。
    """
    wt = os.path.join(WT_DIR, tid)
    if not os.path.isdir(wt):
        return ""
    script = os.path.join(wt, "tools", "test_gate.ps1")
    return script if os.path.isfile(script) else ""


def _quality_gate(tid):
    """合并前测试门（P0#1 内联，2026-10-29 WO-GUANYU-P37）。

    口径同 tools/merge_gated.py：纯文档/配置变更跳过，其余在**任务 worktree**跑
    `tools/test_gate.ps1 -Suite fast`（portal+k_stock），新增失败即拒绝。
    （2026-10-30：从 master 级改为分支级，防既有失败死锁。）
    显式绕过：DEVTOOL_MERGE_GATE=0（仅回滚/紧急，回单注明"绕过质量闸"）。
    """
    if os.environ.get("DEVTOOL_MERGE_GATE") == "0":
        return True, "已显式绕过（DEVTOOL_MERGE_GATE=0）"
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    try:
        import merge_gated as _mg
    except ImportError:
        return False, "质量闸组件缺失（tools/merge_gated.py）"
    try:
        files = _mg.branch_changed_files(tid)
    except Exception as exc:  # noqa: BLE001
        return False, "改动文件计算异常：%r" % exc
    if _mg.is_docs_only(files):
        return True, "纯文档/配置变更（跳过测试）"
    # 2026-10-31 H5-a（K-ORG-20261031-MERGEGATE，审计 v2）：gate/baseline master 钉扎。
    # 分支不得改 quality gate 脚本（改动即拒）；baseline 仅允许 ratchet（只减 known）。
    try:
        pin_ok, pin_detail = _mg.gate_pin_check(tid)
    except Exception as exc:  # noqa: BLE001 —— 钉扎异常按拒绝（fail-visible）
        return False, "H5 钉扎校验异常：%r" % exc
    if not pin_ok:
        return False, pin_detail
    print("  质量闸   … %s" % pin_detail)
    gate_script = _gate_script_for(tid)
    if not gate_script:
        return False, "任务 worktree 不可用（缺 tools/test_gate.ps1），拒绝在 master 上跑分支级闸"
    print("  质量闸   … 在任务 worktree 跑 test_gate -Suite fast（分支级，约 4~5 分钟）")
    rc = _mg.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                  "-File", gate_script,
                  "-Suite", "fast"], timeout=1800)
    if rc != 0:
        return False, "test_gate fast rc=%d（日志 %s）" % (rc, _mg.LOG)

    # 2026-10-29 门禁盲区（实证：tools 测试失败仍被 fast 放行）：
    # 本单变更了 tools/tests/test_*.py 时，在任务 worktree 内先跑过这些文件。
    changed_tools_tests = [f for f in files
                           if f.startswith("tools/tests/test_") and f.endswith(".py")]
    if changed_tools_tests:
        wt = os.path.join(WT_DIR, tid)
        if not os.path.exists(os.path.join(wt, ".git")):
            subprocess.run(["git", "-C", REPO, "worktree", "add", wt, "task/%s" % tid],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if not os.path.exists(os.path.join(wt, ".git")):
            return False, "任务 worktree 不可用，无法运行变更的 tools 测试：%s" % wt
        print("  质量闸   … 跑本单变更的 tools 测试（%d 个）" % len(changed_tools_tests))
        p = subprocess.run([sys.executable, "-m", "pytest", *changed_tools_tests,
                            "-q", "--timeout=120", "-p", "no:cacheprovider"],
                           cwd=wt, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if p.returncode != 0:
            tail = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()[-6:]
            return False, "变更的 tools 测试未过 rc=%d：%s" % (
                p.returncode, " | ".join(tail)[:300])
    # 2026-10-30 P41 / 2026-10-31 H5-b：通知 pre-merge-commit 钩子跳过重复测试——
    # 不再设裸 env（可被伪造跳过）；改为写 TID+HEAD 绑定的一次性 token，钩子校验后删除。
    try:
        import merge_preflight as _mp
        head = subprocess.run(
            ["git", "-C", REPO, "rev-parse", "refs/heads/task/%s" % tid],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace").stdout.strip()
        tok = _mp.write_preflight_token(tid, head)
        print("  质量闸   … H5 preflight token 已写：%s（钩子校验后删除）" % tok)
    except Exception as exc:  # noqa: BLE001 —— token 失败=钩子重跑测试（安全，不阻断合并）
        print("  质量闸   ⚠ H5 token 写入失败（钩子将重跑测试）：%s" % str(exc)[:160])
    return True, "fast 门通过（%d 文件；钩子凭一次性 token 跳过重复测试）" % len(files)


# ---- 组合门 + 内核守卫（P1.2，ADR-015 §2.4/§7）----
# 规格：docs/specs/2026-11-02_composition_gate_devtask_v1.md
# 条件门：仅当改动命中 compositions/**（plan/validate）或内核契约 artifact（kernel_guard）
# 才实际动作；工具/输出异常一律拒（fail-visible，不回落到 master 跑）。
def _branch_tip_commit_msg(tid, branch=""):
    """任务分支 tip 提交信息（kernel_guard --commit-msg 用；读不到返回空串）。"""
    wt = os.path.join(WT_DIR, tid)
    ref = branch or ("task/%s" % tid)
    try:
        r = subprocess.run(["git", "-C", wt, "log", "-1", "--format=%B", ref],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        return (r.stdout or "").strip()
    except Exception:  # noqa: BLE001 —— 读不到=空串，由门统一判拒
        return ""


def _run_branch_tool(tid, tool_rel, args, timeout=600):
    """在任务 worktree 内跑分支级工具 → (proc, error)。

    组合/kernel 门必须校验**待合并分支**内容：composition.py / kernel_guard.py 的
    ROOT 由 __file__ 定位，在 master 的 tools/ 跑会读 master。因此与 _quality_gate
    的 _gate_script_for 同口径调用 <worktree>/tools/*.py；worktree/工具缺失 → error
    （fail-visible，禁止回落到 master 假放行）。
    """
    wt = os.path.join(WT_DIR, tid)
    tool = os.path.join(wt, "tools", tool_rel)
    if not os.path.isfile(tool):
        return None, "任务 worktree 缺 tools/%s（拒绝在 master 上跑分支级门）" % tool_rel
    try:
        proc = subprocess.run([sys.executable, "-X", "utf8", tool, *args],
                              cwd=wt, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except Exception as exc:  # noqa: BLE001 —— 超时/环境异常按拒收
        return None, "运行 tools/%s 异常：%r" % (tool_rel, exc)
    return proc, ""


def _composition_kernel_gate(tid, branch, changed):
    """组合门 + 内核守卫（P1.2 条件门）：返回 (ok, detail)。只读，不写 master。

    口径（ADR-015 + 规格 §一）：
      - compositions/** 改动 → composition.py plan --strict：漂移(non-empty findings)即拒；
      - 变更的 manifest → composition.py validate：errors 即拒，warnings 只打印；
      - 内核契约 artifact 改动 → kernel_guard.py --files ... --commit-msg <分支 tip 信息>：
        无 ADR-号即拒；
      - 工具/ worktree 缺失、输出非 JSON、进程异常 → 拒（fail-closed/fail-visible）。
    """
    files = [str(f).replace("\\", "/") for f in (changed or []) if str(f).strip()]
    if not files:
        return True, "无改动文件（跳过）"
    # 应急通道与既有质量闸同口径（规格 §三 #8）：DEVTOOL_MERGE_GATE=0 仅回滚/紧急，
    # 回单注明"绕过质量闸+组合/内核门"，24h 内补 ADR（ADR-015 §7）。
    if os.environ.get("DEVTOOL_MERGE_GATE") == "0":
        return True, ("DEVTOOL_MERGE_GATE=0 → 显式跳过组合/内核门"
                      "（仅回滚/紧急，须回单留痕 + 24h 补 ADR）")
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    try:
        import merge_preflight as _mp
    except Exception as exc:  # noqa: BLE001 —— 组件缺失=拒（fail-visible）
        return False, "组合/内核门组件缺失（tools/merge_preflight.py）：%r" % exc
    return _mp.composition_kernel_check(
        files,
        lambda tool_rel, cargs: _run_branch_tool(tid, tool_rel, cargs),
        _branch_tip_commit_msg(tid, branch))


# ---- merge train 门（W1/UF-09，ADGS v1.1 §七；复用 tools/merge_train.py 单一口径）----
# 主窗口 16:30–17:00 / 次窗口 21:40–22:10；禁并期 09:15–15:05、17:10–18:40、
# 20:00–20:20、21:20–21:40；文档/组合声明（docs/**、deliverables/**、compositions/**、
# 任意 *.md）旁路随时允许。只读判定、无副作用；now 参数仅供单测注入。
def _merge_train_gate(tid, changed, force=False, now=None):
    """merge train 门：返回 (ok, detail)。

    force=True 仅由 cmd_merge 的 `--train-force` 显式紧急放行触发（调用方负责打印
    留痕警告、写工单 note 与 approval_verdict 事件）；now 为 ISO 时间串（测试注入，
    None=当前 Asia/Shanghai 时间）。工具缺失/判定异常=拒（fail-visible）。
    """
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    try:
        import merge_train as _mt
    except Exception as exc:  # noqa: BLE001 —— 组件缺失=拒（fail-visible）
        return False, "merge train 组件缺失（tools/merge_train.py）：%r" % exc
    try:
        payload = _mt.evaluate(_mt.parse_now(now), files=list(changed or []),
                               force=bool(force))
    except Exception as exc:  # noqa: BLE001 —— 判定异常=拒（fail-visible）
        return False, "merge train 判定异常：%r" % exc
    if payload.get("allowed"):
        return True, payload.get("reason") or "merge train 放行"
    return False, payload.get("reason") or "窗口外/禁并期禁止合并"


_MERGE_TIMINGS = []


@contextmanager
def _timed(name):
    """分段计时上下文（2026-10-30 P42：定位 merge 各门耗时）。"""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        _MERGE_TIMINGS.append((name, time.perf_counter() - t0))


def _print_dirty_board(max_age_hours=4.0):
    """合并成功后打印脏文件看板（P51，2026-10-30）。

    目的：把「并行会话留下未提交文件」的 owner/年龄直接带到合并输出里，
    减少停摆后的跨会话沟通成本。纯提示，不阻断；DEVTOOL_NO_DIRTY_BOARD=1 关闭。
    """
    if os.environ.get("DEVTOOL_NO_DIRTY_BOARD") == "1":
        return
    try:
        tools_dir = os.path.dirname(os.path.abspath(__file__))
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        import check_dirty as _cd
        rows = _cd.build_rows()
    except Exception:  # noqa: BLE001 —— 看板失败不影响合并
        return
    if not rows:
        print("  [脏文件看板] 工作区干净（无未提交跟踪文件）")
        return
    overdue = [r for r in rows if (r.get("age_hours") or 0) > max_age_hours]
    print("  [脏文件看板] %d 个未提交文件（>%.0fh %d 个）；owner≈文件最近提交者"
          % (len(rows), max_age_hours, len(overdue)))
    for r in rows[:8]:
        age = r.get("age_hours") or 0
        flag = "!" if age > max_age_hours else " "
        print("    %s %5.1fh  %-46s owner≈%s"
              % (flag, age, r.get("path"), r.get("owner_hint") or "?"))
    if len(rows) > 8:
        print("    … 其余 %d 个：python tools/check_dirty.py --strict"
              % (len(rows) - 8))
    if overdue:
        print("  协议：>2h 提醒 owner；>4h 工单注明；>8h 升级 A0")


def _print_merge_timings(tid):
    """打印并落盘分段耗时（供持续定位静态门开销）。"""
    if not _MERGE_TIMINGS:
        return
    total = sum(v for _, v in _MERGE_TIMINGS)
    print("== 分段耗时（段内合计 %.1fs）==" % total)
    for name, sec in _MERGE_TIMINGS:
        print("  %-16s %7.1fs" % (name, sec))
    try:
        import json as _json
        out = {
            "tid": tid,
            "total_s": round(total, 1),
            "segments": [{"name": n, "s": round(v, 2)} for n, v in _MERGE_TIMINGS],
        }
        fp = os.path.join(os.environ.get("TEMP", r"C:\Temp"),
                          "devtask_merge_timing_%s.json" % tid)
        with open(fp, "w", encoding="utf-8") as f:
            _json.dump(out, f, ensure_ascii=False, indent=1)
        print("  timing json → %s" % fp)
    except OSError:
        pass


def cmd_merge(tid, *flags):
    _t_entry = time.perf_counter()
    # 2026-10-31 P110：主仓 core.bare 自愈守卫。外部误改 core.bare=true 会让
    # git worktree merge 报 "must be run in a work tree"，先修复再走门禁。
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from git_worktree_guard import ensure_worktree as _ensure_wt
        if not _ensure_wt():
            print("❌ 主仓 core.bare 异常且自动修复失败，拒绝合并")
            return 1
    except Exception as _e:  # noqa: BLE001
        print("[warn] core.bare 守卫不可用（跳过）：%s" % _e)
    # 2026-10-30 P45：门禁（静态门/质量闸）在 worktree/分支上运行、不写 master，
    # 不再全程持合并锁 → 多会话门禁可并行；串行锁仅覆盖 git merge 提交临界区
    # （见 _acquire_merge_lock + _merge_with_retry，防 HEAD/index 竞争）。
    # K-STOCK-416：--l2-ack / --l2-reason 人工豁免通道（五要素审批协议）
    ack = "--l2-ack" in flags
    reason = ""
    if "--l2-reason" in flags:
        i = flags.index("--l2-reason")
        if i + 1 >= len(flags):
            raise SystemExit("--l2-reason 需要跟五要素说明文本"
                             "（命令全文+影响面+不可逆点+替代方案+回滚预案）")
        reason = flags[i + 1]
    # 2026-10-31 WO-WORKTREE-MECH：--keep-worktree 跳过合并后自动回收
    keep_worktree = _should_keep_worktree(flags)
    # W1/UF-09 merge train：--train-force 显式紧急放行（不静默，失败/放行均留痕）
    train_force = "--train-force" in flags
    tasks = _tasks()
    t = tasks.get(tid)
    if not t:
        raise SystemExit("无此工单")
    # 2026-10-27 WO-AUTHORITY-B2：研发域最高权限 charter 的合并审批（ARCH1 署名）。
    # mode=warn 只告警（灰度）；mode=block 拒收。非 dev 单不适用。
    appr_cfg = _load_dev_authority()
    appr_warnings, appr_blocked = _approval_check(t, appr_cfg)
    for w in appr_warnings:
        print("[warn] dev-approval: %s（mode=%s，只告警不拒收）"
              % (w, appr_cfg["approval"].get("mode", "warn")),
              file=sys.stderr)
    if appr_blocked:
        t["status"] = "review"
        _save(tasks, changed=(tid,))
        _emit("guard_fail", tid,
              "研发域审批门拒收：%s" % "；".join(appr_warnings))
        raise SystemExit("研发域审批门（mode=block）拒收 %s：%s"
                         "——补 --approver 或修正署名后重试"
                         % (tid, "；".join(appr_warnings)))
    # 防呆（K-ORG-139 / P0-1）：分支无提交即拒绝合并，保留 worktree。
    # 亲历事故：K-ORG-137 在 worktree 改了文件但未 commit 就跑 merge——
    # 分支指向 master 使 `git merge --no-ff` 空转（Already up to date）；
    # 旧版随后 --force 清场直接删除未提交改动，修复内容全丢。
    ahead, _ = _ahead_behind(t["branch"])
    if ahead == 0:
        print("== 评审门 ==")
        print("  ✗ 防呆：分支 %s 无提交（master..%s = 0）——未 commit 即 merge "
              "会空转（无实际合并内容）" % (t["branch"], t["branch"]))
        print("  已拒绝合并，worktree 保留。请在 worktree 内先 commit，再重跑 merge。")
        t["status"] = "review"
        _save(tasks, changed=(tid,))
        _emit("guard_fail", tid, "分支无提交（master..%s=0），防呆拒绝合并，worktree 保留"
              % t["branch"])
        return 1
    _MERGE_TIMINGS.clear()
    _MERGE_TIMINGS.append(("准备段(入口→评审门)", max(0.0, time.perf_counter() - _t_entry)))
    _dirty_ok, _overlap = _dirty_overlap_guard(tid, t["branch"])
    if not _dirty_ok:
        print("== 评审门 ==")
        print("  %-8s ✗ master 工作区有 %d 个未提交文件与本单改动重叠（并行会话 WIP）"
              % ("脏文件", len(_overlap)))
        for f in _overlap[:10]:
            print("      - %s" % f)
        print("  处置：① 等该文件 owner 提交/清理；② 窄单策略：从当前 master 另起分支并动态排除"
              "这些文件；③ 禁止 checkout/覆盖他人 WIP。")
        print("门未过，拒绝合并（快速失败，未进入测试门）。")
        t["status"] = "review"
        _save(tasks, changed=(tid,))
        _emit("guard_fail", tid,
              "脏文件冲突（%d 个，快速失败）：%s" % (len(_overlap), ",".join(_overlap[:5])))
        return 1
    print("== 评审门 ==")
    for name, cmd in GATES:
        _g0 = time.perf_counter()
        if callable(cmd):
            ok, out = cmd(t["branch"], t.get("worktree"))
        else:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               env=GUARD_ENV)
            ok = r.returncode == 0
            out = (r.stdout or r.stderr).strip()
        _MERGE_TIMINGS.append((name, time.perf_counter() - _g0))
        if ok:
            print("  %-8s ✓%s" % (name, ("  " + out) if out else ""))
            continue
        out = (out or "").strip()
        print("  %-8s ✗ %s" % (name, out[:160]))
        print("门未过，拒绝合并。修完重跑。")
        t["status"] = "review"
        _save(tasks, changed=(tid,))
        _emit("guard_fail", tid, "门 %s 未过：%s" % (name, out[:120]))
        return 1
    with _timed("security_warn"):
        _security_warn_gate(t["branch"], t.get("worktree"))
    with _timed("gitleaks"):
        _gitleaks_warn_gate(t["branch"], t.get("worktree"))
    with _timed("ocr_warn"):
        _ocr_warn_gate(t["branch"], t.get("worktree"))
    with _timed("visual_warn"):
        _visual_warn_gate(t["branch"], t.get("worktree"))
    # K-STOCK-416：L2 宿主机级危险操作门（代码强制层，判例 K-STOCK-413）
    _g0 = time.perf_counter()
    r = subprocess.run([sys.executable,
                        os.path.join(REPO, "tools", "_l2_guard.py"),
                        t["branch"]],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    _MERGE_TIMINGS.append(("L2门", time.perf_counter() - _g0))
    if r.returncode == 0:
        print("  %-8s %s" % ("L2危险", "✓"))
    else:
        detail = (r.stdout or r.stderr).strip()[:400]
        print("  %-8s ✗ %s" % ("L2危险", detail))
        if r.returncode == 2 and ack:
            approval_id, replayed = _l2_exempt(tid, t, reason)
            note = ("[K-STOCK-416 L2豁免] approval_id=%s decided_by=%s "
                    "reason=%s%s"
                    % (approval_id, os.environ.get("AGENT_NAME", "human"),
                       reason or "(未填)", "（重放既有审批单）" if replayed else ""))
            t["note"] = ((t.get("note", "") + "；" + note).strip("；")
                         if t.get("note") else note)
            print("  L2危险   ⚠ 人工豁免已留痕：%s" % approval_id)
            _emit("approval_verdict", tid, note)
        else:
            if r.returncode != 2:
                print("  （L2 守卫自身异常按拒绝处理——fail-visible，"
                      "不静默放行；修完重跑）")
            print("门未过，拒绝合并。")
            t["status"] = "review"
            _save(tasks, changed=(tid,))
            _emit("guard_fail", tid, "L2 门未过（K-STOCK-416）：%s" % detail[:120])
            return 1
    # W5 P1-6 Phase1：merge 前抓取本单改动文件（merge 后 merge-base 会退化，
    # 故必须在合并前算），供 IDE bundle 决定是否重烘+提交。
    _bundle_changed = _merge_changed_files(tid)
    _qg0 = time.perf_counter()
    qg_ok, qg_detail = _quality_gate(tid)
    _MERGE_TIMINGS.append(("质量闸", time.perf_counter() - _qg0))
    if qg_ok:
        print("  %-8s ✓ %s" % ("质量闸", qg_detail))
    else:
        print("  %-8s ✗ %s" % ("质量闸", qg_detail[:200]))
        print("门未过，拒绝合并。修完重跑。")
        t["status"] = "review"
        _save(tasks, changed=(tid,))
        _emit("guard_fail", tid, "质量闸未过：%s" % qg_detail[:120])
        return 1
    # W1/UF-09（ADGS §七）：merge train 门——窗口/禁并期/文档组合旁路；失败语义与质量闸同构。
    _mt0 = time.perf_counter()
    mt_ok, mt_detail = _merge_train_gate(tid, _bundle_changed, force=train_force)
    _MERGE_TIMINGS.append(("merge train", time.perf_counter() - _mt0))
    if mt_ok:
        if train_force:
            print("  %-8s ⚠ %s" % ("merge train", mt_detail))
            print("  ⚠ --train-force 紧急放行：须在工单回单留痕（五要素）+ 24h 复盘；"
                  "禁止日常绕过（ADGS §七/§十）")
            _force_note = "[W1/UF-09 merge-train force] %s" % mt_detail
            if reason:
                _force_note += "；reason=%s" % reason
            t["note"] = ((t.get("note", "") + "；" + _force_note).strip("；")
                         if t.get("note") else _force_note)
            _emit("approval_verdict", tid, _force_note)
        else:
            print("  %-8s ✓ %s" % ("merge train", mt_detail))
    else:
        print("  %-8s ✗ %s" % ("merge train", mt_detail[:200]))
        print("门未过，拒绝合并。窗口：主 16:30–17:00、次 21:40–22:10；"
              "文档/组合声明随时可合；紧急请 --train-force（留痕）后重跑。")
        t["status"] = "review"
        _save(tasks, changed=(tid,))
        _emit("guard_fail", tid, "merge train 未过：%s" % mt_detail[:120])
        return 1
    # P1.2（ADR-015）：组合门 + 内核守卫——分支级只读校验；失败语义与质量闸同构。
    _ck0 = time.perf_counter()
    ck_ok, ck_detail = _composition_kernel_gate(tid, t["branch"], _bundle_changed)
    _MERGE_TIMINGS.append(("组合/内核门", time.perf_counter() - _ck0))
    if ck_ok:
        print("  %-8s ✓ %s" % ("组合/内核", ck_detail))
    else:
        print("  %-8s ✗ %s" % ("组合/内核", ck_detail[:200]))
        print("门未过，拒绝合并。修完重跑。")
        t["status"] = "review"
        _save(tasks, changed=(tid,))
        _emit("guard_fail", tid, "组合/内核门未过：%s" % ck_detail[:120])
        return 1
    _lk0 = time.perf_counter()
    lock = _acquire_merge_lock(tid)
    _MERGE_TIMINGS.append(("锁等待", time.perf_counter() - _lk0))
    if lock is None:
        return 1
    # 2026-10-30 P48：钩子 merge_preflight 也检查同一把锁；导出父锁 token 让它
    # 识别"父进程已持锁"直接放行（否则钩子拒绝提交、git 留半合并状态）。
    os.environ["AIOS_MERGE_LOCK_TOKEN"] = str(lock)
    try:
        _gm0 = time.perf_counter()
        _merge_with_retry(tid, t["branch"], t["desc"])
        _MERGE_TIMINGS.append(("git merge(含钩子)", time.perf_counter() - _gm0))
        # 合并后不自动删除 worktree/分支：删除不可逆，只提示、留给人确认执行。
        t["status"] = "done"
        t["merged"] = time.strftime("%F %T")
        _save(tasks, changed=(tid,))
        _ckpt(tid, "merge", t.get("owner", "devtask"), {"status": "done"})
        _emit("merge_done", tid, "守卫全绿（含明文凭据扫描），已合并 master")
        if str(t.get("dept") or "").strip().lower() == "dev":
            _emit("review_verdict", tid,
                  "approver=%s merged=1" % (t.get("approver") or "MISSING"))
        print("工单 %s 已合并 ✓" % tid)
        _bd0 = time.perf_counter()
        _auto_rebuild_ide_bundle(tid, _bundle_changed)
        _MERGE_TIMINGS.append(("IDE bundle", time.perf_counter() - _bd0))
    finally:
        _release_merge_lock(lock)
    if t.get("worktree"):
        if keep_worktree:
            print("→ --keep-worktree：保留 worktree %s 待人工处理" % t["worktree"])
        else:
            _r_ok, _r_note = _recycle_worktree(tid, t)
            if _r_ok:
                print("→ 自动回收：%s（--keep-worktree 可跳过）" % _r_note)
                _emit("merge_done", tid, "worktree 自动回收：%s" % _r_note)
            else:
                print("→ 自动回收跳过：%s（合并已成功，不影响 master）" % _r_note)
    _print_dirty_board()
    _print_merge_timings(tid)
    return 0


def cmd_drop(tid, *reason):
    tasks = _tasks()
    t = tasks.pop(tid, None)
    if not t:
        raise SystemExit("无此工单")
    try:
        wt = t.get("worktree")
        if wt:
            _git("worktree", "remove", wt, "--force")
        br = t.get("branch")
        if br:
            _git("branch", "-D", br)
    except SystemExit:
        pass
    t["status"] = "aborted"
    t["abort_reason"] = " ".join(reason) or "(未填)"
    _abort_key = tid + "-aborted-" + time.strftime("%H%M%S")
    tasks[_abort_key] = t
    _save(tasks, changed=(_abort_key,), drop_keys=(tid,))
    print("工单 %s 已放弃：%s" % (tid, t["abort_reason"]))
    return 0


def cmd_board(out=None):
    out = out or os.path.join(REPO, "docs", "dev", "devboard.html")
    tasks = _tasks()
    rows = ""
    lamp = {"doing": "#3fb950", "review": "#d29922", "done": "#58a6ff",
            "aborted": "#8b949e"}
    for tid, t in sorted(tasks.items(),
                         key=lambda kv: (kv[1]["status"], kv[0])):
        ahead, behind = _ahead_behind(t["branch"]) if t.get("branch") else (0, 0)
        rows += ("<tr><td><b>%s</b></td><td><span style='color:%s'>●"
                 "</span> %s</td><td>%s</td><td>%s</td><td>+%d/-%d</td>"
                 "<td>%s</td></tr>"
                 % (tid, lamp.get(t["status"], "#888"), t["status"],
                    t.get("owner", "?"), t.get("desc", ""), ahead, behind,
                    t.get("merged", t.get("created", ""))))
    html = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>研发部看板</title>"
            "<script src='/static/nav.js' defer></script>"
            "<style>body{background:#0d1117;color:#e6edf3;"
            "font-family:'Segoe UI','Microsoft YaHei',sans-serif;"
            "padding:24px}table{border-collapse:collapse;width:100%%;"
            "font-size:13px}th,td{border:1px solid #21262d;"
            "padding:8px 12px;text-align:left}th{background:#1c2129;"
            "color:#8b949e}h1{font-size:19px}</style></head><body>"
            "<h1>💻 研发部看板 · 多 Agent 分工与进展</h1>"
            "<p style='color:#8b949e;font-size:12px'>数据源 "
            ".worktrees/tasks.json · 生成 %s · 刷新跑 "
            "devtask.py board</p>"
            "<table><tr><th>工单</th><th>状态</th><th>owner</th>"
            "<th>描述</th><th>ahead/behind</th><th>时间</th></tr>%s"
            "</table></body></html>"
            % (time.strftime("%F %T"), rows))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w", encoding="utf-8").write(html)
    print("看板已生成：%s（%d 单）" % (out, len(tasks)))
    return 0


# ── prune（EXEC-R11，2026-10-25）────────────────────────────────────────────
# 仓治理：① 删已合并进 master 的本地 task/* 分支（git branch -d，绝不用 -D）；
# ② 回收 .worktrees/<TID>（分支已合并 + tasks.json 状态 done/aborted + 工作树干净）。
# 默认 dry-run 只打印计划，不发任何删除命令；--apply 才执行。执行权留给人：
# 并行会话仍在写 worktree，误删不可逆（AGENTS §7「删前确认」判例）。
PRUNE_PROTECTED = ("master", "main")
_PRUNE_FLAGS = ("--apply", "--dry-run", "--merged", "--worktrees", "--all")


def _prune_git(repo, *args, check=False, timeout=None):
    """prune 专用 git 调用（独立于 _git：需支持 --repo 指向临时演练仓）。

    `timeout` 是必需的逃生口：`git worktree remove` 在 Windows 上删大目录可能
    跑十几分钟，没有超时就会把整个 prune 钉死（2026-11-08 实测卡满 400s）。
    超时不抛异常，返回 rc=124 的假 CompletedProcess，由调用方按失败处理并走
    兜底路径 —— 抛异常会让"已删一半"的状态没人收尾。
    """
    args = list(args)
    try:
        r = subprocess.run(["git"] + args, cwd=repo, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           env=GUARD_ENV, timeout=timeout)
    except subprocess.TimeoutExpired:
        r = subprocess.CompletedProcess(
            ["git"] + args, 124, stdout="",
            stderr="timeout after %ss: git %s" % (timeout, " ".join(args)))
    if check and r.returncode != 0:
        raise SystemExit("git %s 失败: %s"
                         % (" ".join(args), (r.stderr or r.stdout).strip()))
    return r


def _prune_remove_worktree(repo, path, timeout=600):
    """回收一个 worktree，返回 (ok, note)。两段式，专治"删到一半"。

    ① `git worktree remove`（**不带 --force**，脏树由 git 自己拒）；
    ② 若超时/失败，但 `.git` 指针已经消失 —— 这是 Windows 删大目录的典型
       中间态 —— 则 `git worktree prune` 解注册，再清残留目录。

    对并发闸而言**解注册就已经收口**（闸数的是 `git worktree list`），
    清目录只是把磁盘拿回来，所以②即使清不干净也算成功，note 里会写明。

    timeout 默认 600s 是实测定的：2026-11-08 五个 worktree 逐个删，
    耗时 186s / 225s / 228s / 251s —— 原先 180s 的口径**必然**误判成超时。
    """
    r = _prune_git(repo, "worktree", "remove", path, timeout=timeout)
    if r.returncode == 0:
        return True, "git worktree remove 完成"
    err = (r.stderr or r.stdout or "").strip().replace("\n", " ")[:160]
    if _wt_pointer_ok(path):
        # 指针还在 → 是真的删不动（被占用/权限/树不干净），保持原样交人工
        return False, "git worktree remove 失败：%s" % err
    pr = _prune_git(repo, "worktree", "prune", timeout=60)
    if pr.returncode != 0:
        return False, ("指针已失但 prune 失败：%s"
                       % (pr.stderr or pr.stdout).strip()[:120])
    freed = _prune_rmtree(path)
    return True, "指针已失 → 已解注册；残留目录清理%s（%s）" % (
        "完成" if freed else "未完成，需人工", err)


def _prune_load_tasks(tasks_path):
    """读目标仓 .worktrees/tasks.json（沿用尾逗号自愈）。"""
    if not os.path.exists(tasks_path):
        return {}
    with open(tasks_path, encoding="utf-8") as f:
        raw = f.read()
    try:
        return json.loads(raw)
    except ValueError:
        healed = _heal_trailing_commas(raw)
        if healed == raw:
            raise
        return json.loads(healed)


def _prune_same_path(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _wt_pointer_ok(path):
    """worktree 目录里是否还带 `.git` 指针 —— 判断"活着"的唯一可信依据。

    2026-11-08 判例（K-ORG-20261108-OVWSPLIT）：Windows 上 `git worktree remove`
    先删 `.git` 指针、再删树，超时被杀会留下"指针没了、树还在"的空壳；这种壳
    仍会出现在 `git worktree list` 里。

    **不能用 `git -C <path> status` 判活**：指针缺失时 git 会沿父目录一路向上，
    命中主仓的 `.git`，于是返回**主树的**状态——看起来像"这个 worktree 刚被人
    改脏了"，实际它早已是空壳。同理也不能据此判"脏"来决定是否保留。
    """
    return os.path.exists(os.path.join(path, ".git"))


def _wt_is_dead(item, repo):
    """在册但已是空壳：目录还在，`.git` 指针已失（bare / 主树除外）。"""
    if item.get("bare") or not item.get("exists"):
        return False
    if _prune_same_path(item["path"], repo):
        return False
    return not _wt_pointer_ok(item["path"])


def _prune_rmtree(path):
    """尽力删掉一个残留目录（清只读位后重试），返回是否已不存在。

    只用于**已确认 `.git` 指针缺失**的空壳：库内内容早已不在版本控制视野里，
    删它属于清盘，不涉及任何未提交的受控文件。
    """
    if not os.path.isdir(path):
        return True

    def _onexc(func, p, exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass

    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=_onexc)
        else:  # pragma: no cover - 3.11 及以下
            shutil.rmtree(path, onerror=lambda f, p, e: _onexc(f, p, e[1]))
    except OSError:
        pass
    return not os.path.isdir(path)


def _prune_worktrees(repo):
    """git worktree list --porcelain → [{path, branch, detached, bare, exists, dead}]。"""
    out = _prune_git(repo, "worktree", "list", "--porcelain", check=True).stdout
    items = []
    for line in out.splitlines():
        line = line.rstrip()
        if line.startswith("worktree "):
            items.append({"path": line[len("worktree "):].strip(), "branch": None,
                          "detached": False, "bare": False})
        elif items and line.startswith("branch refs/heads/"):
            items[-1]["branch"] = line[len("branch refs/heads/"):].strip()
        elif items and line == "detached":
            items[-1]["detached"] = True
        elif items and line == "bare":
            items[-1]["bare"] = True
    for it in items:
        it["exists"] = os.path.isdir(it["path"])
        it["dead"] = _wt_is_dead(it, repo)
    return items


def _prune_find_task(tasks, tid, branch, wt_path):
    """按 TID / branch / worktree 绝对路径三路匹配工单（aborted 会改名留存）。"""
    if tid in tasks:
        return tid, tasks[tid]
    for key, val in tasks.items():
        if branch and val.get("branch") == branch:
            return key, val
        if wt_path and val.get("worktree") and _prune_same_path(val["worktree"], wt_path):
            return key, val
    return None, None


def _prune_plan(repo, do_branches=True, do_worktrees=True):
    """只读计算 prune 计划；任何分支下都不发删除命令。"""
    wt_dir = os.path.join(repo, ".worktrees")
    tasks = _prune_load_tasks(os.path.join(wt_dir, "tasks.json"))
    cur = _prune_git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    merged = set()
    m_out = _prune_git(repo, "branch", "--merged", "master",
                       "--format=%(refname:short)").stdout
    for name in m_out.splitlines():
        name = name.strip().lstrip("* ").strip()
        if name:
            merged.add(name)
    wts = _prune_worktrees(repo)
    checked_out = {}
    for w in wts:
        if w["branch"] and not w["bare"] and not _prune_same_path(w["path"], repo):
            checked_out[w["branch"]] = w["path"]
    plan = {"repo": repo, "current": cur, "worktrees": [], "branches": [],
            "stale": []}
    # ① worktree：只认 .worktrees/ 下且在 git worktree list 在册的目录
    if do_worktrees and os.path.isdir(wt_dir):
        listed = {os.path.normcase(os.path.abspath(w["path"])): w for w in wts}
        for entry in sorted(os.listdir(wt_dir)):
            path = os.path.join(wt_dir, entry)
            if not os.path.isdir(path):
                continue
            w = listed.get(os.path.normcase(os.path.abspath(path)))
            tid, branch, tv = entry, None, None
            reasons = []
            if w is None:
                reasons.append("不在 git worktree list（残留目录，需人工核实）")
            elif w["bare"]:
                reasons.append("bare worktree")
            elif w["detached"]:
                reasons.append("detached HEAD（非任务分支检出）")
            elif not w["branch"]:
                reasons.append("分支未知")
            else:
                branch = w["branch"]
            if branch:
                if branch not in merged:
                    reasons.append("分支未合并 master（%s）" % branch)
                if branch == cur:
                    reasons.append("分支是当前检出分支")
                tk, tv = _prune_find_task(tasks, tid, branch, path)
                if tv is None:
                    reasons.append("tasks.json 无对应工单（状态未知）")
                elif tv.get("status", "?") not in ("done", "aborted"):
                    reasons.append("任务状态=%s（非 done/aborted）"
                                   % tv.get("status", "?"))
            if w is not None and w.get("dead"):
                # 这里**绝不能**跑 git status：指针缺失时它会向上命中主仓 .git，
                # 返回主树状态，看起来像"worktree 脏了"（2026-11-08 实际误判过）。
                reasons.append("登记已死（.git 指针缺失，目录为空壳）")
            elif w is not None and w["exists"]:
                st = _prune_git(path, "status", "--porcelain")
                if st.returncode != 0:
                    reasons.append("git status 失败（%s）"
                                   % (st.stderr or "").strip()[:60])
                elif st.stdout.strip():
                    reasons.append("工作树脏（%d 个未提交项）"
                                   % len(st.stdout.strip().splitlines()))
            elif w is not None:
                reasons.append("目录不存在（陈旧登记）")
            plan["worktrees"].append({
                "tid": tid, "path": path, "branch": branch,
                "status": (tv or {}).get("status"), "action": "worktree-remove",
                "skip": bool(reasons),
                "reason": "；".join(reasons) if reasons
                else "分支已合并 + 任务 %s + 工作树干净"
                     % (tv or {}).get("status", "?")})
            # 2026-10-31 WO-WORKTREE-MECH：TTL 红灯（只标注，不改变 skip/删除判定）
            _age = _worktree_age_hours(path, tv)
            plan["worktrees"][-1]["age_hours"] = _age
            plan["worktrees"][-1]["ttl_red"] = bool(_age is not None and _age > _ttl_hours())
    # ② 分支：本地 task/* 且已合并 master（当前分支/他人检出排除）
    if do_branches:
        wt_by_branch = {it["branch"]: it for it in plan["worktrees"] if it["branch"]}
        for name in sorted(merged):
            if not name.startswith("task/"):
                continue
            if name in PRUNE_PROTECTED or name == cur:
                continue
            holder = wt_by_branch.get(name)
            if name in checked_out and holder is None:
                plan["branches"].append({
                    "branch": name, "action": "delete-branch", "skip": True,
                    "reason": "分支被 worktree 占用：%s（--worktrees 未选中）"
                              % checked_out[name]})
            elif holder is not None and holder["skip"]:
                plan["branches"].append({
                    "branch": name, "action": "delete-branch", "skip": True,
                    "reason": "分支被 worktree 占用且该 worktree 拒绝回收：%s"
                              % holder["path"]})
            elif holder is not None:
                plan["branches"].append({
                    "branch": name, "action": "delete-branch", "skip": False,
                    "reason": "已合并 master（随 worktree 回收后删除）"})
            else:
                plan["branches"].append({
                    "branch": name, "action": "delete-branch", "skip": False,
                    "reason": "已合并 master"})
    # ③ 陈旧登记：目录已不存在，或目录还在但 `.git` 指针已失（空壳）
    for w in wts:
        if _prune_same_path(w["path"], repo):
            continue
        if not os.path.isdir(w["path"]) or w.get("dead"):
            plan["stale"].append(w["path"])
    return plan


def _prune_apply(repo, plan):
    """执行计划：先回收 worktree（两段式，见 _prune_remove_worktree），再删分支。"""
    rc = 0
    for it in plan["worktrees"]:
        if it["skip"]:
            continue
        ok, note = _prune_remove_worktree(repo, it["path"])
        if ok:
            print("  [DONE] %s → %s" % (it["path"], note))
        else:
            rc = 1
            print("  [FAIL] %s → %s" % (it["path"], note))
    for it in plan["branches"]:
        if it["skip"]:
            continue
        r = _prune_git(repo, "branch", "-d", it["branch"])
        if r.returncode == 0:
            print("  [DONE] git branch -d %s" % it["branch"])
        else:
            rc = 1
            print("  [FAIL] git branch -d %s → %s"
                  % (it["branch"], (r.stderr or r.stdout).strip()[:160]))
    if plan["stale"]:
        r = _prune_git(repo, "worktree", "prune")
        if r.returncode == 0:
            print("  [DONE] git worktree prune（清理 %d 条陈旧登记）"
                  % len(plan["stale"]))
    return rc


def cmd_prune(*args):
    """prune：仓治理（默认 dry-run，--apply 才执行）。"""
    flags = [a for a in args if a.startswith("--")]
    unknown = [f for f in flags if f not in _PRUNE_FLAGS and f != "--repo"]
    if unknown:
        raise SystemExit("prune 未知参数：%s（支持 --dry-run/--apply/--merged/"
                         "--worktrees/--all/--repo <path>）" % " ".join(unknown))
    repo = REPO
    if "--repo" in args:
        i = list(args).index("--repo")
        if i + 1 >= len(args):
            raise SystemExit("--repo 需要跟仓库路径（仅演练/单测用）")
        repo = os.path.abspath(args[i + 1])
    _prune_git(repo, "rev-parse", "--git-dir", check=True)
    only_merged, only_wts = "--merged" in args, "--worktrees" in args
    if "--all" in args or not (only_merged or only_wts):
        do_branches = do_worktrees = True
    else:
        do_branches, do_worktrees = only_merged, only_wts
    apply_ = "--apply" in args
    plan = _prune_plan(repo, do_branches=do_branches, do_worktrees=do_worktrees)
    print("== devtask prune（%s）==" % ("APPLY" if apply_ else "dry-run"))
    print("仓库：%s（当前分支 %s）" % (repo, plan["current"]))
    wts_do = [x for x in plan["worktrees"] if not x["skip"]]
    wts_skip = [x for x in plan["worktrees"] if x["skip"]]
    brs_do = [x for x in plan["branches"] if not x["skip"]]
    brs_skip = [x for x in plan["branches"] if x["skip"]]
    if do_worktrees:
        print("-- worktree 回收：候选 %d / 拒绝 %d --"
              % (len(wts_do), len(wts_skip)))
        for it in wts_do:
            print(_format_prune_wt(it, apply_))
        for it in wts_skip:
            print(_format_prune_wt(it, False))
    if do_branches:
        print("-- 分支删除：候选 %d / 拒绝 %d --"
              % (len(brs_do), len(brs_skip)))
        for it in brs_do:
            print("  [%s] git branch -d %s  # %s"
                  % ("APPLY" if apply_ else "DRY", it["branch"], it["reason"]))
        for it in brs_skip:
            print("  [SKIP] git branch -d %s  # %s"
                  % (it["branch"], it["reason"]))
    if plan["stale"]:
        print("-- 陈旧 worktree 登记 %d 条（--apply 时 git worktree prune）--"
              % len(plan["stale"]))
        for p in plan["stale"]:
            print("  [STALE] %s" % p)
    print("汇总：worktree 候选 %d / 分支候选 %d；拒绝 worktree %d / 分支 %d"
          % (len(wts_do), len(brs_do), len(wts_skip), len(brs_skip)))
    # 2026-10-31 WO-WORKTREE-MECH：TTL 红灯汇总（仅标注，不自动删）
    _ttl_red = [x for x in plan["worktrees"] if x.get("ttl_red")]
    if _ttl_red:
        print("TTL 红灯：%d 个 worktree age>%gh（[TTL-RED] 仅标注，需人工收口；"
              "DEVTOOL_WT_TTL_HOURS 可调）" % (len(_ttl_red), _ttl_hours()))
    if not apply_:
        print("→ dry-run：未执行任何删除。确认后在有权限会话跑 "
              "`python tools/devtask.py prune --apply`")
        return 0
    rc = _prune_apply(repo, plan)
    print("→ apply 完成（rc=%d）；建议重跑 --dry-run 复核剩余项" % rc)
    return rc


def cmd_sandbox(*args):
    """devtask sandbox --cmd ...：在 Docker 沙箱执行命令（只读仓库/network none）。

    P0⑧ 收口（WO-SANDBOX-WIRE-20261027）：不改变现有 devtask 命令行为，只提供 opt-in 开关。
    """
    import argparse

    ap = argparse.ArgumentParser(prog="devtask.py sandbox",
                                 description="Docker 沙箱执行（仓库只读/network none）")
    ap.add_argument("--cmd", required=True, help="容器内 shell 命令")
    ap.add_argument("--image", default="")
    ap.add_argument("--network", default="none", choices=["none", "bridge"])
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--memory", default="512m", help="容器内存上限（默认 512m）")
    ap.add_argument("--cpus", default="1.0", help="CPU 配额（默认 1.0 核）")
    ap.add_argument("--pids-limit", type=int, default=256, help="进程数上限（默认 256）")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(list(args))

    tools_dir = os.path.join(REPO, "agent_platform", "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    try:
        from agent_sandbox import DEFAULT_IMAGE, run_cmd
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": "agent_sandbox_import_failed: %s" % exc},
                         ensure_ascii=False))
        return 2
    if a.network == "bridge":
        print("[warn] --network bridge 会放开容器网络；出口白名单未实现，仅限临时排障",
              file=sys.stderr)
    out = run_cmd(a.cmd, image=a.image or DEFAULT_IMAGE, network=a.network,
                  timeout=a.timeout, dry_run=a.dry_run,
                  memory=a.memory, cpus=a.cpus, pids_limit=a.pids_limit)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out.get("ok") else 1


def _attribution_gate(argv):
    """CLI `new` 强制署名（2026-10-29 归因整改）。

    AGENT_NAME/DEVTOOL_AGENT 皆缺 → 拒绝建单（避免再产生 attribution_missing=1）；
    例外显式 `--allow-unattributed`（仅迁移/应急，事件仍带 attribution_missing 标记）。
    返回 (rc, 清理后的 argv)；rc!=0 表示拒绝。
    """
    if not argv or argv[0] != "new":
        return 0, argv
    rest = [x for x in argv if x != "--allow-unattributed"]
    allow = len(rest) != len(argv)
    if allow or os.environ.get("AGENT_NAME") or os.environ.get("DEVTOOL_AGENT"):
        return 0, rest
    print("[attribution] 拒绝建单：请先设置 AGENT_NAME 或 DEVTOOL_AGENT"
          "（AGENTS §1 开发身份三环境变量）；应急可加 --allow-unattributed",
          file=sys.stderr)
    return 2, rest


def _git_health_autofix(repo=None):
    """2026-10-31 P140：主仓 .git/config 被外部改成 bare=true 时，任何 devtask 命令
    启动即自愈（此前只在 merge 门检查，`devtask new/list/prune` 在事故期间直接不可用）。
    返回 True=健康/已修复；False=修复失败（仅告警，不阻断其余命令）。"""
    repo = Path(repo or REPO)
    dotgit = repo / ".git"
    if not dotgit.exists():
        return True
    def _inside():
        try:
            r = subprocess.run(["git", "--git-dir=.git", "rev-parse",
                                "--is-inside-work-tree"], cwd=str(repo),
                               capture_output=True, text=True, timeout=15)
            return r.stdout.strip() == "true"
        except Exception:  # noqa: BLE001
            return False
    if _inside():
        return True
    try:
        subprocess.run(["git", "--git-dir=.git", "config", "core.bare", "false"],
                       cwd=str(repo), capture_output=True, text=True, timeout=15)
    except Exception as e:  # noqa: BLE001
        print("[warn] git core.bare 自愈执行失败：%s" % e)
        return False
    ok = _inside()
    if ok:
        print("[warn] 检测到主仓 core.bare=true（外部误改），已自动修复为 false", file=sys.stderr)
    else:
        print("[warn] 主仓 core.bare 异常且自动修复失败（请人工检查 .git/config）", file=sys.stderr)
    return ok


def main():
    a = sys.argv[1:]
    # 2026-11-02 P1.2 / 2026-11-06：--help/-h 只读打印用法，绝不进入命令分发。
    # 兼容 ``new --help`` / ``list -h`` 等子命令后置帮助，避免被 cmd_new 当成 TID
    # 误建空单（历史上已产生多个 --help-aborted-* 脏单）。
    if (a and a[0] == "help") or any(x in ("-h", "--help") for x in a):
        print(__doc__)
        return 0
    _git_health_autofix()
    if not a:
        print(__doc__)
        return 0
    rc, a = _attribution_gate(a)
    if rc:
        return rc
    c, rest = a[0], a[1:]
    return {"new": lambda: cmd_new(*rest),
            "list": lambda: cmd_list(*rest),
            "port": lambda: cmd_port(*rest),
            "merge": lambda: cmd_merge(*rest),
            "drop": lambda: cmd_drop(*rest),
            "prune": lambda: cmd_prune(*rest),
            "audit-worktrees": lambda: cmd_audit_worktrees(*rest),
            "sandbox": lambda: cmd_sandbox(*rest),
            "board": cmd_board}[c]()


if __name__ == "__main__":
    sys.exit(main())
