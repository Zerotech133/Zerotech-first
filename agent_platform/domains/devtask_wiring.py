# -*- coding: utf-8 -*-
"""M1 软件开发域接线 · devtask 工单线（与 dev_wiring.py 职责切分）。

**双线互引声明（并行撞车协议）**：开发域有两条独立真源——
- 部门任务单线（TaskStore，data/org/tasks/*.jsonl）：domains/dev_wiring.py
  接线（done 验收门，2026-10-17 14:05 交付）；
- **本模块**：devtask 工单线（.worktrees/tasks.json，tools/devtask.py
  「一单一线程 worktree」驱动，208 工单真账本）。
两线真源不同、状态机不同、互不侵入；审批前缀 devtask: 与 dev: 错开。

领域语义（本模块）：
- **状态真源不动**：.worktrees/tasks.json（dict，键=工单 ID）仍是工单
  生命周期唯一真源；不建第二账本；
- **机器通道不动**：tools/devtask.py 是既有合法写路径——cmd_new(doing)/
  cmd_merge(三守卫门→done)/cmd_drop(aborted)。接线不侵入、不重造；
- **人工通道互补**：提供三条带凭据的受控转移（devtask 没有的治理动作）：
  1. `approve <tid> aborted`  弃单门（doing/review→aborted）：放弃工单
     =放弃已投入的 worktree/分支，必须持 Approval 凭据、留 reason；
  2. `approve <tid> doing`    打回返工（review→doing）：评审未过退回，
     凭据留痕（谁、何时、为何打回）；
  3. `approve <tid> done`     合并漂移修复（review→done）：K-ORG-064
     场景——代码已实际进 master 但状态卡 review，批准对齐现实；
     前提=工单有 merged 时间戳或 git 中存在 merge task/<tid> 提交，
     无合并证据拒绝（防止跳过三守卫用审批门洗白）；
     例外：非 git 工作流单（无 branch/worktree/merged 痕迹）无「合并」
     概念，走人工审查确认通道——审批凭据即审计证据（2026-10-17 判例：
     K-PUSH-005~008 推送域单，强制合并证据属范畴错误）；
- **六铁律**（与 GZH/Academy/Invest 同款）：先落 Approval 凭据再改真源、
  幂等键 devtask:<tid>:<from>→<to>（轮次化 #r{n+1}，Invest 判例）、
  崩溃窗口 catch-up、终态冻结（done/aborted）、无源 ID 不伪造；
- **审计分类学**（devtask 域特色——done 主通道是机器三守卫，不做
  「状态⇒凭据」强制，改查漂移与存量）：
  - violations·merged_but_review：有合并证据但状态卡 review（可修复漂移）；
  - findings·legacy_done：无 merged 且无 git 证据的 done 存量（grandfather）；
  - findings·review_pending / unapproved_drops / approved_drops 统计。

CLI：
    python domains/devtask_wiring.py list     [--root .] [--active]
    python domains/devtask_wiring.py report   [--root .] [--no-git]
    python domains/devtask_wiring.py approve  <tid> <aborted|doing|done>
           --by <批准人> [--reason "..."] [--root .]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_AGENT_PLATFORM = Path(__file__).resolve().parents[1]
if str(_AGENT_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_AGENT_PLATFORM))
_TOOLS_DIR = _AGENT_PLATFORM.parent / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from contracts.objects import Approval, Artifact, WorkItem  # noqa: E402
from engine.approvals import ApprovalStore  # noqa: E402

try:  # 写侧语义校验（M6：tasks.json 接 validate_json）
    from validate_json import check_tasks_doc  # noqa: E402
except ImportError as _exc:  # fail-open 必配诊断（判例）
    check_tasks_doc = None  # type: ignore[assignment]
    print(
        "[devtask_wiring] 警告：tools/validate_json.py 不可导入（%s），"
        "写侧语义校验降级为仅 loads 校验" % _exc,
        file=sys.stderr,
    )
from adapters.credential_trace import cross_check, verdict_for  # noqa: E402
import logging

SCHEMA_VERSION = "1.0"
DOMAIN = "devtask"
SOURCE = "devtask.worktrees_tasks"

# devtask 原生状态机：doing（施工）→ review（评审/卡单）→ done（合并清场）；
# doing/review 可 aborted（弃单受门）；review 可打回 doing（受门留痕）；
# done/aborted 终态冻结。
LEGAL_TRANSITIONS: Dict[str, List[str]] = {
    "doing": ["aborted"],
    "review": ["done", "doing", "aborted"],
    "done": [],
    "aborted": [],
}
TERMINAL_STATUSES = {"done", "aborted"}
MERGE_MARKER = "merge task/"  # cmd_merge 的 git 提交标记


class DevtaskWiringError(Exception):
    """devtask 接线失败（显式错误，不静默降级）。"""


# ----------------------------------------------------------------------
# 真源读写
# ----------------------------------------------------------------------
def _tasks_path(root: Path) -> Path:
    return Path(root) / ".worktrees" / "tasks.json"


def load_tasks(root: Path) -> Dict[str, dict]:
    path = _tasks_path(root)
    if not path.exists():
        raise DevtaskWiringError("missing_source: %s 不存在" % path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise DevtaskWiringError("tasks.json 解析失败: %s" % exc)
    if not isinstance(raw, dict):
        raise DevtaskWiringError(
            "tasks.json 顶层必须是 dict（键=工单 ID），实际 %s" % type(raw).__name__
        )
    # 值非 dict 的条目：早期实现静默丢弃（`if isinstance(v, dict)`），
    # 结果「工单凭空消失」而调用方毫无感知——比报错更危险（判例：
    # 静默降级比显式失败更容易被当成正常态）。改为显式告警后仍跳过：
    # 降级不崩（读侧要为看板/巡检供数），但证据留在 stderr。
    dropped = [k for k, v in raw.items() if not isinstance(v, dict)]
    if dropped:
        print(
            "[devtask_wiring] 警告：tasks.json 有 %d 条非 dict 工单已跳过：%s"
            % (len(dropped), dropped[:10]),
            file=sys.stderr,
        )
    return {k: v for k, v in raw.items() if isinstance(v, dict)}


def save_tasks(root: Path, tasks: Dict[str, dict]) -> None:
    """原子写回（保持 devtask indent=1 风格）。

    **写前语义校验（M6：接 validate_json profile=tasks，增量口径）**：
    只校验本次**新增/变更**的工单，存量 grandfather 不重判——真源里有
    历史遗留的 WARN 级瑕疵（手写近似时间 `2026-10-17 20:1x`、推送域
    无 branch 的空壳完工单），若全量判 ERROR 会让写入方寸步难行，
    「存量瑕疵阻断新增」属范畴错误。谁改谁负责：改到哪条就校验哪条。
    ERROR（缺 status/created、status 非法、值非 dict）→ 拒写；
    WARN → 放行并打 stderr（降级）。
    """
    path = _tasks_path(root)
    if check_tasks_doc is not None:
        try:
            disk = load_tasks(root)
        except DevtaskWiringError:
            disk = {}  # 首次创建/真源不可读：全量视为新增
        changed = {tid: t for tid, t in tasks.items() if disk.get(tid) != t}
        removed = [tid for tid in disk if tid not in tasks]
        if removed:
            print(
                "[devtask_wiring] 警告：本次写回将删除 %d 条工单：%s"
                % (len(removed), removed[:10]),
                file=sys.stderr,
            )
        if changed:
            errors, warns = check_tasks_doc(changed)
            for w in warns:
                print("[devtask_wiring] WARN %s" % w, file=sys.stderr)
            if errors:
                raise DevtaskWiringError(
                    "tasks.json 写前校验失败（%d 条变更中 %d 项结构性错误），"
                    "已拒写：%s" % (len(changed), len(errors), "；".join(errors[:5]))
                )
    path.parent.mkdir(parents=True, exist_ok=True)
    # P2 清仓（2026-10-18）：tmp 名加 pid+时间戳唯一化——固定 ".tmp" 名在
    # 并发 read-modify-write 窗口会被另一进程截断/顶替（A 写一半 B 重开同
    # 名 tmp），os.replace 换上的可能是别人的半截内容。
    tmp = "%s.tmp.%d.%s" % (path, os.getpid(), datetime.now().strftime("%H%M%S%f"))
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except PermissionError as exc:
        # Windows 目标被占用（并发读句柄/杀软扫描）→ 原子替换失败：清掉
        # 本进程残留 tmp，包装为显式域错误（不裸 traceback，调用方按
        # DevtaskWiringError 统一处理）。
        try:
            os.unlink(tmp)
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/domains/devtask_wiring.py", exc)
        raise DevtaskWiringError(
            "tasks.json 写回失败（目标被占用 PermissionError，稍后重试）: %s" % exc
        ) from exc


def find_task(root: Path, tid: str) -> dict:
    t = load_tasks(root).get(tid)
    if t is None:
        raise DevtaskWiringError("tid=%s 不在 tasks.json 中（无源 ID 不伪造）" % tid)
    return t


def _now_naive() -> str:
    """域内时间格式：naive 本地（与 devtask created/merged 一致）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------------------
# 合并证据（投影/审计/漂移判定共用）
# ----------------------------------------------------------------------
def is_git_workflow(task: dict) -> bool:
    """工单是否属于 git 工作流（有 branch/worktree/merged 任一痕迹）。

    非 git 工作流单（如推送域直接落生产文件的工作）没有「合并」概念，
    review→done 走人工审查确认通道（审批凭据 decided_by/reason 全留痕，
    防洗白由审批账本本身兜底），不强制合并证据——要求合并证据属范畴错误。
    """
    return bool(task.get("branch") or task.get("worktree") or task.get("merged"))


def merge_evidence(root: Path, tid: str, task: dict, check_git: bool = True) -> bool:
    """工单是否已有合并进 master 的证据。

    基准一：tasks.json 的 merged 时间戳（cmd_merge 成功后写入）；
    基准二（可选增强）：git log 全分支存在 `merge task/<tid>` 提交
    （devtask_audit.is_merged 同款语义）。二者任一成立即有证据。
    """
    if task.get("merged"):
        return True
    if not check_git:
        return False
    try:
        # ★锚定防洗白（K-ORG-064 判例同步：devtask_audit.is_merged 同款）：
        # --grep 是子串匹配，裸 "merge task/<TID>" 会前缀撞号——tid=K-ORG-15
        # 命中 "merge task/K-ORG-155: ..."（真仓实测 4 条误命中），approve done
        # 的合并证据门被前缀工单的 merge 提交误开。cmd_merge 标题格式恒为
        # "merge task/<TID>: ..."，-F 固定串 + 冒号锚定（-F 防 tid 含正则元字符）。
        r = subprocess.run(
            [
                "git",
                "log",
                "--all",
                "-F",
                "--grep=%s%s:" % (MERGE_MARKER, tid),
                "--oneline",
            ],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        return r.returncode == 0 and bool(r.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False  # 非 git 环境/超时：无 git 证据（保守）


# ----------------------------------------------------------------------
# 只读投影：WorkItem + Artifact
# ----------------------------------------------------------------------
def project_workitems(
    root: Path, check_git: bool = True
) -> Tuple[List[WorkItem], List[Artifact]]:
    """tasks.json → 统一 WorkItem/Artifact 投影（可随时重建，不持久化）。"""
    root = Path(root)
    items, artifacts = [], []
    for tid, t in sorted(load_tasks(root).items()):
        status = t.get("status", "")
        wi = WorkItem(
            workitem_id="devtask:%s" % tid,  # 从真源 ID 确定性派生
            domain=DOMAIN,
            source=SOURCE,
            source_ref=tid,
            title=str(t.get("desc", "") or tid)[:80],
            status=status,  # 域内原生状态，原样保留
            owner=t.get("owner", ""),
            payload={
                "project": t.get("project", ""),
                "branch": t.get("branch", ""),
                "created": t.get("created", ""),
                "merged": t.get("merged", ""),
                "abort_reason": t.get("abort_reason", ""),
            },
        )
        items.append(wi)
        worktree = t.get("worktree", "")
        wt_exists = bool(worktree) and (root / worktree).exists()
        artifacts.append(
            Artifact(
                artifact_id="devtask-art:%s" % tid,
                workitem_id=wi.workitem_id,
                kind="worktree",
                path=t.get("branch", ""),
                fact_hash="",  # 产物在 git，git 本身是账本
                # 在办工单（doing/review）worktree 必须在场；终态已清场不检查
                missing=(status in ("doing", "review"))
                and bool(worktree)
                and not wt_exists,
            )
        )
    return items, artifacts


# ----------------------------------------------------------------------
# 受控写路径：审批驱动的状态转移（人工通道）
# ----------------------------------------------------------------------
def approve_transition(
    root: Path,
    tid: str,
    to_status: str,
    by: str,
    reason: str = "",
    store: ApprovalStore = None,
    check_git: bool = True,
) -> Tuple[Approval, dict, bool]:
    """批准并执行一次受控状态转移（aborted / doing / done）。

    返回 (approval, task_after, replayed)。六铁律语义与 Invest 同款。
    done 前置校验：必须有合并证据（merged 时间戳或 git merge 提交）。
    """
    if not by:
        raise DevtaskWiringError("批准人 by 必填（审批可追溯）")
    root = Path(root)
    task = find_task(root, tid)
    from_status = task.get("status", "")

    own_store = store is None
    store = store or ApprovalStore(
        _AGENT_PLATFORM / "data" / "approvals" / "approvals.db"
    )
    try:
        tid_full = "devtask:%s" % tid

        def _own() -> List[Approval]:
            # list 按 created_at,rowid 排序 = 审批账本发生序（append-only）
            return [
                a for a in store.list(target_type="workitem") if a.target_id == tid_full
            ]

        def _find(to: str) -> Optional[Approval]:
            hits = [a for a in _own() if a.to_status == to]
            return hits[0] if hits else None

        # 幂等快路径：目标已达成且有凭据 → 稳定 no-op
        if to_status == from_status:
            hit = _find(to_status)
            if hit:
                return hit, task, True
            raise DevtaskWiringError(
                "工单 %s 状态已为 %s 但账本无凭据：疑似绕过审批门修改真源，"
                "拒绝重复批准（先跑 report 审计）" % (tid, from_status)
            )

        if from_status in TERMINAL_STATUSES:
            raise DevtaskWiringError(
                "工单 %s 已终态 %s，禁止再转移" % (tid, from_status)
            )
        if to_status not in LEGAL_TRANSITIONS.get(from_status, []):
            raise DevtaskWiringError(
                "非法转移 %s→%s（合法：%s）"
                % (from_status, to_status, LEGAL_TRANSITIONS.get(from_status, []))
            )
        if (
            to_status == "done"
            and is_git_workflow(task)
            and not merge_evidence(root, tid, task, check_git=check_git)
        ):
            raise DevtaskWiringError(
                "工单 %s 无合并证据（无 merged 时间戳且 git 无 %s%s 提交）："
                "approve done 只用于修复「已合并但状态卡 review」的漂移，"
                "未合并请走 devtask merge 三守卫" % (tid, MERGE_MARKER, tid)
            )
        if to_status == "aborted" and not reason:
            raise DevtaskWiringError("弃单必须给 reason（放弃的是已投入工作）")

        # catch-up / 轮次化判定（依赖真源消费标记 last_wiring_key）：
        # - 最新凭据恰是本转移且真源未消费它（last_wiring_key 不等于该键）
        #   → 崩溃窗口 catch-up：补齐真源写入，重放返回；
        # - 最新凭据已消费但真源又回到 from（打回返工后重批，devtask 特有
        #   的「同转移反复」场景）→ 新轮次批准，落 #r{n+1} 新凭据留痕。
        own = _own()
        base = "devtask:%s:%s>%s" % (tid, from_status, to_status)
        prior_same = [
            a for a in own if a.from_status == from_status and a.to_status == to_status
        ]
        if prior_same and own[-1].idempotency_key == prior_same[-1].idempotency_key:
            last = own[-1]
            if task.get("last_wiring_key") != last.idempotency_key:
                _apply_status(root, tid, to_status, reason, last.idempotency_key)
                return last, find_task(root, tid), True  # catch-up

        # 轮次化幂等键：打回后重批是新的一轮批准，留新凭据（Invest 判例）
        key = base if not prior_same else "%s#r%d" % (base, len(prior_same) + 1)
        approval, replayed = store.record(
            Approval(
                target_type="workitem",
                target_id=tid_full,
                from_status=from_status,
                to_status=to_status,
                decision="approved",
                decided_by=by,
                reason=reason,
                decided_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                idempotency_key=key,
            )
        )
        if not replayed:
            _apply_status(root, tid, to_status, reason, key)
            return approval, find_task(root, tid), False
        # 防御分支：轮次化后 key 理论全新；并发窗口同 key 抢先落库时
        # 真源若滞后补齐，否则原样返回
        if task.get("status") != to_status:
            _apply_status(root, tid, to_status, reason, key)
            return approval, find_task(root, tid), True
        return approval, task, True
    finally:
        if own_store:
            store.close()


def _apply_status(
    root: Path, tid: str, to_status: str, reason: str = "", wiring_key: str = ""
) -> None:
    """唯一授权写路径：更新 tasks.json 中该工单的 status（原子写）。

    aborted 补 abort_reason（取审批 reason）；done 补 merged（漂移修复
    对齐现实）；doing 打回清 abort_reason（返工重新计数）；
    last_wiring_key 记录已消费的凭据键（catch-up 判据，见 approve_transition）。
    """
    tasks = load_tasks(root)
    t = tasks.get(tid)
    if t is None:
        raise DevtaskWiringError("tid=%s 已从 tasks.json 消失（并发删除？）" % tid)
    t["status"] = to_status
    if wiring_key:
        t["last_wiring_key"] = wiring_key
    if to_status == "aborted":
        t["abort_reason"] = reason or t.get("abort_reason") or "approval drop"
    if to_status == "done" and not t.get("merged"):
        t["merged"] = _now_naive()
    if to_status == "doing":
        t.pop("abort_reason", None)
    save_tasks(root, tasks)


# ----------------------------------------------------------------------
# 审计：漂移检测 + 存量分类（devtask 域特色）
# ----------------------------------------------------------------------
def audit_report(
    root: Path, store: ApprovalStore = None, check_git: bool = True
) -> dict:
    """工单生命周期审计（分类学见模块 docstring）。"""
    root = Path(root)
    own_store = store is None
    store = store or ApprovalStore(
        _AGENT_PLATFORM / "data" / "approvals" / "approvals.db"
    )
    try:
        tasks = load_tasks(root)
        approvals = [
            a
            for a in store.list(target_type="workitem")
            if a.target_id.startswith("devtask:")
        ]
        approved_drops = {a.target_id for a in approvals if a.to_status == "aborted"}
        violations = []
        legacy_done = []
        review_pending = []
        unapproved_drops = []
        for tid, t in sorted(tasks.items()):
            status = t.get("status", "")
            if status == "review":
                review_pending.append(tid)
                if merge_evidence(root, tid, t, check_git=check_git):
                    violations.append(
                        {
                            "tid": tid,
                            "status": status,
                            "finding": "已合并但状态卡 review（漂移），"
                            "走 approve %s done 修复" % tid,
                        }
                    )
            elif status == "done":
                if not t.get("merged") and not merge_evidence(
                    root, tid, t, check_git=check_git
                ):
                    legacy_done.append(tid)  # 存量 grandfather
            elif status == "aborted":
                if "devtask:%s" % tid not in approved_drops:
                    unapproved_drops.append(tid)
        # doing/review 的 worktree 缺失是在办隐患
        items, artifacts = project_workitems(root, check_git=False)
        missing_wt = [a.workitem_id for a in artifacts if a.missing]
        # 交叉校验：凭据轮次 vs 消费标记（devtask=latest_only 标记域）
        traces = {}
        for tid, t in tasks.items():
            traces["devtask:%s" % tid] = {
                "statuses": [],
                "consumed_keys": {t.get("last_wiring_key")}
                if t.get("last_wiring_key")
                else set(),
            }
        cross_v = cross_check(
            "devtask",
            approvals,
            traces,
            {"devtask:%s" % tid: t.get("status", "") for tid, t in tasks.items()},
            latest_only=True,
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "domain": DOMAIN,
            "workitem_count": len(tasks),
            "by_status": _by_status(tasks),
            "approval_count": len(approvals),
            "violations": violations,
            "missing_worktrees": missing_wt,
            "findings": {
                "legacy_done": legacy_done,
                "review_pending": review_pending,
                "unapproved_drops": unapproved_drops,
                "approved_drops": sorted(approved_drops),
            },
            "cross_check": cross_v,
            "verdict": verdict_for(
                ([{"severity": "FAIL"}] if violations or missing_wt else []) + cross_v
            ),
        }
    finally:
        if own_store:
            store.close()


def _by_status(tasks: Dict[str, dict]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for t in tasks.values():
        out[t.get("status", "")] = out.get(t.get("status", ""), 0) + 1
    return out


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M1 devtask 工单线接线")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_ls = sub.add_parser("list", parents=[common])
    p_ls.add_argument(
        "--active", action="store_true", help="只看 doing/review 在办工单"
    )
    p_rp = sub.add_parser("report", parents=[common])
    p_rp.add_argument(
        "--no-git", action="store_true", help="跳过 git merge 提交校验（非 git 环境）"
    )
    p_ap = sub.add_parser("approve", parents=[common])
    p_ap.add_argument("tid")
    p_ap.add_argument("to_status", choices=["aborted", "doing", "done"])
    p_ap.add_argument("--by", required=True)
    p_ap.add_argument("--reason", default="")
    p_ap.add_argument(
        "--no-git", action="store_true", help="跳过 git merge 提交校验（非 git 环境）"
    )

    args = ap.parse_args(argv)
    root = Path(args.root)

    if args.cmd == "list":
        items, artifacts = project_workitems(root, check_git=False)
        for wi, art in zip(items, artifacts):
            if args.active and wi.status not in ("doing", "review"):
                continue
            print(
                "%-28s %-8s %-14s %s%s"
                % (
                    wi.workitem_id,
                    wi.status,
                    (wi.payload.get("project") or "-")[:14],
                    wi.title[:36],
                    " [worktree缺失]" if art.missing else "",
                )
            )
        return 0
    if args.cmd == "report":
        print(
            json.dumps(
                audit_report(root, check_git=not args.no_git),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.cmd == "approve":
        approval, task, replayed = approve_transition(
            root,
            args.tid,
            args.to_status,
            args.by,
            args.reason,
            check_git=not args.no_git,
        )
        print(
            "[devtask_wiring] %s %s：%s→%s%s"
            % (
                args.tid,
                "重放" if replayed else "批准",
                approval.from_status,
                approval.to_status,
                ""
                if not replayed
                else "（凭据 %s 已存在，真源未重复变更）" % approval.approval_id[:12],
            )
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
