# -*- coding: utf-8 -*-
"""M1 业务域写侧接线 · Academy（研究院）。

领域语义（真源 = agent_platform/data/academy/research_topics.json）：
- 阶段机 8 段线性：intake→hypothesis→evidence→validation→review→
  joint_review→decision→closed（M0 判例：阶段机独立，不与 project
  phase / 任务 status 混同）；
- topic.history 追加式（{at, stage, by, note}），是域内轨迹真源；
- worker_plan.mode=controlled_shadow：阶段由对应 Agent 推进。

接线语义（与 GZH 同款六铁律，适配阶段机）：
- **状态真源不动**：research_topics.json 仍是阶段唯一真源，不建第二账本；
- **单一写路径**：阶段变更只能经本模块 move_stage()（自由推进/打回，
  记 history）或 approve_transition()（受门转移，先落 Approval 凭据）；
- **两道审批门**：→decision（结题裁决）与 →closed（课题关闭）必须持
  Approval 凭据——研究结论与课题关闭是用户级承诺，生成/审查/批准分离；
- **禁止跳级**：阶段只能前进一步或退回一步；closed 终态冻结；
- **幂等**：受门转移键 academy:<topic_id>:<from>→<to>，重放零副作用，
  崩溃窗口 catch-up；
- **时间格式**：域内 created/updated/history.at 均为 naive 本地格式，
  接线层保持同格式（M0 判例：naive 不补 UTC）。

CLI：
    python domains/academy_wiring.py list    [--root .]
    python domains/academy_wiring.py report  [--root .]
    python domains/academy_wiring.py move <topic_id> <stage> --by <agent> [--note ...]
    python domains/academy_wiring.py approve <topic_id> <decision|closed> --by <人> [--reason ...]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_AGENT_PLATFORM = Path(__file__).resolve().parents[1]
if str(_AGENT_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_AGENT_PLATFORM))

from contracts.objects import Approval, WorkItem  # noqa: E402
from engine.approvals import ApprovalStore  # noqa: E402
from adapters.credential_trace import cross_check, verdict_for  # noqa: E402

SCHEMA_VERSION = "1.0"
DOMAIN = "academy"
SOURCE = "academy.research_topics"

ACADEMY_STAGES = [
    "intake",
    "hypothesis",
    "evidence",
    "validation",
    "review",
    "joint_review",
    "decision",
    "closed",
]
APPROVAL_GATED_TO = {"decision", "closed"}
TERMINAL_STATUSES = {"closed"}

# 阶段只能前进一步或退回一步（打回）；decision→closed 受门；
# decision 可打回 joint_review 重审；closed 终态。
LEGAL_TRANSITIONS: Dict[str, List[str]] = {
    "intake": ["hypothesis"],
    "hypothesis": ["intake", "evidence"],
    "evidence": ["hypothesis", "validation"],
    "validation": ["evidence", "review"],
    "review": ["validation", "joint_review"],
    "joint_review": ["review", "decision"],
    "decision": ["joint_review", "closed"],
    "closed": [],
}


class AcademyWiringError(Exception):
    """Academy 接线失败（显式错误，不静默降级）。"""


# ----------------------------------------------------------------------
# 真源读写
# ----------------------------------------------------------------------
def _topics_path(root: Path) -> Path:
    return Path(root) / "agent_platform" / "data" / "academy" / "research_topics.json"


def load_topics(root: Path) -> List[dict]:
    path = _topics_path(root)
    if not path.exists():
        raise AcademyWiringError("missing_source: %s 不存在" % path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise AcademyWiringError("research_topics.json 解析失败: %s" % exc)
    if not isinstance(raw, list):
        raise AcademyWiringError("research_topics.json 顶层必须是数组")
    return [t for t in raw if isinstance(t, dict)]


def save_topics(root: Path, topics: List[dict]) -> None:
    """原子写回（保持既有 indent=2 风格）。"""
    path = _topics_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(topics, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def find_topic(root: Path, topic_id: str) -> dict:
    for t in load_topics(root):
        if t.get("topic_id") == topic_id:
            return t
    raise AcademyWiringError("topic_id=%s 不在 research_topics.json 中" % topic_id)


def _now_naive() -> str:
    """域内时间格式：naive 本地（与既有 history.at/updated 一致）。"""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _append_history(topic: dict, stage: str, by: str, note: str) -> None:
    topic.setdefault("history", []).append(
        {"at": _now_naive(), "stage": stage, "by": by, "note": note}
    )
    topic["updated"] = _now_naive()


# ----------------------------------------------------------------------
# 只读投影：WorkItem
# ----------------------------------------------------------------------
def project_workitems(root: Path) -> List[WorkItem]:
    topics = load_topics(root)
    items = []
    for t in topics:
        stage = t.get("status", "")
        items.append(
            WorkItem(
                workitem_id="academy:%s" % t.get("topic_id", ""),
                domain=DOMAIN,
                source=SOURCE,
                source_ref=t.get("topic_id", ""),
                title=t.get("title", ""),
                status=stage,  # 阶段机原生值，原样保留
                owner=t.get("owner", ""),
                payload={
                    "direction": t.get("direction", ""),
                    "sponsor": t.get("sponsor", ""),
                    "stage_index": ACADEMY_STAGES.index(stage)
                    if stage in ACADEMY_STAGES
                    else None,
                    "worker_mode": (t.get("worker_plan") or {}).get("mode", ""),
                    "history_len": len(t.get("history", []) or []),
                },
            )
        )
    return items


# ----------------------------------------------------------------------
# 自由推进 / 打回（非受门阶段，单一写路径 + 域内 history）
# ----------------------------------------------------------------------
def move_stage(
    root: Path, topic_id: str, to_stage: str, by: str, note: str = ""
) -> dict:
    """自由推进或打回一个阶段（目标不在审批门内）。

    返回 topic（转移后）。非法转移/目标受门/终态 → AcademyWiringError。
    """
    if not by:
        raise AcademyWiringError("操作人 by 必填（轨迹可追溯）")
    if to_stage in APPROVAL_GATED_TO:
        raise AcademyWiringError(
            "→%s 是审批门阶段，必须走 approve（凭据先行）" % to_stage
        )
    if to_stage not in ACADEMY_STAGES:
        raise AcademyWiringError("未知阶段 %s（合法：%s）" % (to_stage, ACADEMY_STAGES))
    root = Path(root)
    topic = find_topic(root, topic_id)
    from_stage = topic.get("status", "")
    if from_stage in TERMINAL_STATUSES:
        raise AcademyWiringError("topic %s 已终态 closed，禁止再转移" % topic_id)
    if to_stage not in LEGAL_TRANSITIONS.get(from_stage, []):
        raise AcademyWiringError(
            "非法转移 %s→%s（合法：%s）；阶段只能前进一步或退回一步"
            % (from_stage, to_stage, LEGAL_TRANSITIONS.get(from_stage, []))
        )
    topic["status"] = to_stage
    _append_history(topic, to_stage, by, note or "stage move")
    save_topics(root, _reload_and_replace(root, topic_id, topic))
    return find_topic(root, topic_id)


def _reload_and_replace(root: Path, topic_id: str, new_topic: dict) -> List[dict]:
    topics = load_topics(root)
    for i, t in enumerate(topics):
        if t.get("topic_id") == topic_id:
            topics[i] = new_topic
            break
    return topics


# ----------------------------------------------------------------------
# 受门转移：结题 / 关闭（Approval 凭据先行）
# ----------------------------------------------------------------------
def approve_transition(
    root: Path,
    topic_id: str,
    to_status: str,
    by: str,
    reason: str = "",
    store: ApprovalStore = None,
) -> Tuple[Approval, dict, bool]:
    """批准并执行受门阶段转移（→decision / →closed）。

    返回 (approval, topic_after, replayed)。语义与 GZH 同款：
    幂等快路径 → 合法性校验 → 落凭据 → 改真源（崩溃窗口 catch-up）。
    """
    if not by:
        raise AcademyWiringError("批准人 by 必填（审批可追溯）")
    if to_status not in APPROVAL_GATED_TO:
        raise AcademyWiringError(
            "%s 不是受门阶段（仅 %s 需 Approval）；自由推进走 move"
            % (to_status, sorted(APPROVAL_GATED_TO))
        )
    root = Path(root)
    topic = find_topic(root, topic_id)
    from_stage = topic.get("status", "")

    own_store = store is None
    store = store or ApprovalStore(
        _AGENT_PLATFORM / "data" / "approvals" / "approvals.db"
    )
    try:

        def _find(to: str) -> Optional[Approval]:
            hits = [
                a
                for a in store.list(target_type="workitem")
                if a.target_id == "academy:%s" % topic_id and a.to_status == to
            ]
            return hits[-1] if hits else None  # 最新凭据（打回重审取新一轮）

        # 幂等快路径：目标已达成且有凭据 → 稳定 no-op
        if to_status == from_stage:
            hit = _find(to_status)
            if hit:
                return hit, topic, True
            raise AcademyWiringError(
                "topic %s 阶段已为 %s 但账本无凭据：疑似绕过审批门修改真源，"
                "拒绝重复批准（先跑 report 审计）" % (topic_id, from_stage)
            )

        if from_stage in TERMINAL_STATUSES:
            raise AcademyWiringError("topic %s 已终态 closed，禁止再转移" % topic_id)
        if to_status not in LEGAL_TRANSITIONS.get(from_stage, []):
            raise AcademyWiringError(
                "非法转移 %s→%s（合法：%s）；阶段只能前进一步或退回一步"
                % (from_stage, to_status, LEGAL_TRANSITIONS.get(from_stage, []))
            )

        own = [
            a
            for a in store.list(target_type="workitem")
            if a.target_id == "academy:%s" % topic_id
        ]
        # catch-up：最新凭据恰是本次转移、且该转移从未落过 history
        # （凭据落库后、真源写入前崩溃的窗口）→ 补齐真源写入，重放返回。
        # history 已有 to_status 痕迹 = 凭据曾生效（之后可能被打回），
        # 不得 catch-up——打回重批交给下方轮次化键处理。多轮后再遇
        # 崩溃窗口会降级为新增 #r{n} 凭据（状态仍正确，账本略冗余）。
        applied = {h.get("stage") for h in topic.get("history", [])}
        if (
            own
            and own[-1].to_status == to_status
            and own[-1].from_status == from_stage
            and to_status not in applied
        ):
            topic = _apply_stage(
                root, topic_id, to_status, by, reason or "approval catch-up"
            )
            return own[-1], topic, True

        # 轮次化幂等键：受门阶段被打回重批（如 decision→joint_review→
        # decision）是新一轮批准，必须留新凭据（decided_by/reason 可追溯），
        # 不得重放第一轮凭据——同轮重放仍由既有凭据幂等兜底。
        n = sum(
            1 for a in own if a.from_status == from_stage and a.to_status == to_status
        )
        base = "academy:%s:%s>%s" % (topic_id, from_stage, to_status)
        key = base if n == 0 else "%s#r%d" % (base, n + 1)
        approval, replayed = store.record(
            Approval(
                target_type="workitem",
                target_id="academy:%s" % topic_id,
                from_status=from_stage,
                to_status=to_status,
                decision="approved",
                decided_by=by,
                reason=reason,
                decided_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                idempotency_key=key,
            )
        )
        if not replayed:
            topic = _apply_stage(root, topic_id, to_status, by, reason)
            return approval, topic, False
        # 重放：真源若滞后（崩溃窗口）补齐，否则原样返回
        if topic.get("status") != to_status:
            topic = _apply_stage(
                root, topic_id, to_status, by, reason or "approval catch-up"
            )
            return approval, topic, True
        return approval, topic, True
    finally:
        if own_store:
            store.close()


def _apply_stage(root: Path, topic_id: str, to_stage: str, by: str, note: str) -> dict:
    """唯一授权写路径：更新真源阶段 + 追加 history（原子写）。"""
    topic = find_topic(root, topic_id)
    topic["status"] = to_stage
    _append_history(topic, to_stage, by, note or "approval transition")
    topics = _reload_and_replace(root, topic_id, topic)
    save_topics(root, topics)
    return find_topic(root, topic_id)


# ----------------------------------------------------------------------
# 审计：受门阶段 vs 审批账本回放比对
# ----------------------------------------------------------------------
def audit_report(root: Path, store: ApprovalStore = None) -> dict:
    """结题/关闭一致性审计。

    status∈{decision, closed} 的课题必须有对应 Approval 凭据；缺失 =
    有人绕过审批门改了真源，显式列出。同时校验阶段值合法。
    另含「凭据轮次 vs 痕迹」交叉校验（adapters.credential_trace）：
    死凭据 / 崩溃窗口 / 轮次超额（trace 域全量校验）。
    """
    root = Path(root)
    own_store = store is None
    store = store or ApprovalStore(
        _AGENT_PLATFORM / "data" / "approvals" / "approvals.db"
    )
    try:
        items = project_workitems(root)
        approvals = store.list(target_type="workitem")
        violations = []
        for wi in items:
            if wi.status not in ACADEMY_STAGES:
                violations.append(
                    {
                        "topic_id": wi.source_ref,
                        "status": wi.status,
                        "finding": "阶段值不在阶段机内",
                    }
                )
                continue
            if wi.status in APPROVAL_GATED_TO:
                hit = [
                    a
                    for a in approvals
                    if a.target_id == wi.workitem_id and a.to_status == wi.status
                ]
                if not hit:
                    violations.append(
                        {
                            "topic_id": wi.source_ref,
                            "status": wi.status,
                            "finding": "受门阶段=%s 但审批账本无凭据" % wi.status,
                        }
                    )
        # 交叉校验：凭据轮次 vs history 痕迹（academy=trace 域）
        topics = {t.get("topic_id", ""): t for t in load_topics(root)}
        traces = {}
        for tid, t in topics.items():
            traces["academy:%s" % tid] = {
                "statuses": [
                    h.get("stage")
                    for h in (t.get("history") or [])
                    if isinstance(h, dict)
                ],
                "consumed_keys": set(),
            }
        academy_creds = [a for a in approvals if a.target_id.startswith("academy:")]
        cross_v = cross_check(
            "academy",
            academy_creds,
            traces,
            {"academy:%s" % tid: t.get("status", "") for tid, t in topics.items()},
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "domain": DOMAIN,
            "workitem_count": len(items),
            "by_status": _by_status(items),
            "approval_count": len(approvals),
            "violations": violations,
            "cross_check": cross_v,
            "verdict": verdict_for(
                ([{"severity": "FAIL"}] if violations else []) + cross_v
            ),
        }
    finally:
        if own_store:
            store.close()


def _by_status(items: List[WorkItem]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for wi in items:
        out[wi.status] = out.get(wi.status, 0) + 1
    return out


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M1 Academy 域接线")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", parents=[common])
    sub.add_parser("report", parents=[common])
    p_mv = sub.add_parser("move", parents=[common])
    p_mv.add_argument("topic_id")
    p_mv.add_argument(
        "to_stage", choices=[s for s in ACADEMY_STAGES if s not in APPROVAL_GATED_TO]
    )
    p_mv.add_argument("--by", required=True)
    p_mv.add_argument("--note", default="")
    p_ap = sub.add_parser("approve", parents=[common])
    p_ap.add_argument("topic_id")
    p_ap.add_argument("to_stage", choices=sorted(APPROVAL_GATED_TO))
    p_ap.add_argument("--by", required=True)
    p_ap.add_argument("--reason", default="")

    args = ap.parse_args(argv)
    root = Path(args.root)

    if args.cmd == "list":
        for wi in project_workitems(root):
            print(
                "%-32s %-12s %-24s idx=%s"
                % (
                    wi.workitem_id,
                    wi.status,
                    wi.title[:24],
                    wi.payload.get("stage_index"),
                )
            )
        return 0
    if args.cmd == "report":
        print(json.dumps(audit_report(root), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "move":
        topic = move_stage(root, args.topic_id, args.to_stage, args.by, args.note)
        print(
            "[academy_wiring] %s 推进 →%s（history %d 条）"
            % (args.topic_id, topic["status"], len(topic.get("history", [])))
        )
        return 0
    if args.cmd == "approve":
        approval, topic, replayed = approve_transition(
            root, args.topic_id, args.to_stage, args.by, args.reason
        )
        print(
            "[academy_wiring] %s %s：%s→%s%s"
            % (
                args.topic_id,
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
