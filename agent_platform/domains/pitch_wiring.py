# -*- coding: utf-8 -*-
"""M1 路演 PPT 域接线（域 #4）· decks 受控状态机。

绿地域：仓内无既有 PPT 真源，本模块建立初始账本
agent_platform/data/pitch/decks.json（新建真源，不属第二主控）。

架构依据（docs/audit/2026-10-17_老田个人AI组一次成型总体架构.md）：
- 流程：商业目标 → 受众 → 证据包 → 叙事 → PPT → 事实审查
  → 田丰审阅 → 用户批准 → 演示/交付；
- 红线：PPT 必须区分 **事实 / 假设 / 预测 / 待补证**，
  禁止编造客户、订单、收入和政策数据。

状态机（六段，对齐架构流程）：
    outline(叙事) → drafted(初稿) → fact_check(事实审查)
    → reviewed(田丰审阅) → approved(用户批准) → delivered(交付)
允许 ±1 步（打回）；approved/delivered 终态冻结（delivered 终态，
approved 可打回 reviewed）。

门（凭据不泛滥，承 Academy 判例）：
    →approved 必须 Approval（用户级承诺）
    →delivered 必须 Approval（对外交付）
    其余阶段走 move_stage 单一写路径 + 域内 history。

claims 纪律：进入 fact_check 阶段要求 deck.claims 非空，每条
{text, kind ∈ fact|assumption|forecast|pending}——四分类是架构
红线，未声明即不放进事实审查；claims 原样保存，本模块不校验真伪
（那是 Evidence 体系职责）。

Artifact：deck.artifact 为可选文件路径；存在时记 sha256 指纹
（对齐 GZH md 指纹判例），缺失显式 missing 不伪造。

CLI：add / list / show / move / approve / report
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

_AGENT_PLATFORM = Path(__file__).resolve().parents[1]
if str(_AGENT_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_AGENT_PLATFORM))

from contracts.objects import Approval, Artifact, fact_hash_of  # noqa: E402
from engine.approvals import ApprovalStore  # noqa: E402
from engine.artifacts import ArtifactStore  # noqa: E402
from adapters.credential_trace import cross_check, verdict_for  # noqa: E402

DECKS_FILE = _AGENT_PLATFORM / "data" / "pitch" / "decks.json"


def _decks_file(root=None) -> Path:
    """T1 尾巴收口：路径函数化（root 显式注入供测试/tmp 隔离）。
    缺省分支运行时读模块属性 DECKS_FILE——兼容既有测试直接
    setattr(pw.DECKS_FILE, tmp) 的 patch 面（闭包定值会绕过 patch，判例重演）。"""
    if root is not None:
        return Path(root) / "data" / "pitch" / "decks.json"
    return DECKS_FILE


STAGES = ("outline", "drafted", "fact_check", "reviewed", "approved", "delivered")
LEGAL_TRANSITIONS = {
    # 前进 ±1 步；approved 可打回 reviewed；delivered 终态
    "outline": ["drafted"],
    "drafted": ["fact_check", "outline"],
    "fact_check": ["reviewed", "drafted"],
    "reviewed": ["approved", "fact_check"],
    "approved": ["delivered", "reviewed"],
    "delivered": [],
}
GATED = {"approved", "delivered"}  # →这些状态必须凭据
CLAIM_KINDS = ("fact", "assumption", "forecast", "pending")


def _db_path() -> Path:
    return _AGENT_PLATFORM / "data" / "approvals" / "approvals.db"


class PitchWiringError(Exception):
    """受控转移非法（未知阶段/跳级/终态/claims 纪律违反）。"""


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+08:00")


def _now_cn() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


def load_decks(root=None) -> List[dict]:
    try:
        rows = json.loads(_decks_file(root).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    return rows if isinstance(rows, list) else []


def save_decks(rows: List[dict], root=None) -> None:
    f = _decks_file(root)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(f)


def find_deck(deck_id: str) -> Optional[dict]:
    for d in load_decks():
        if d.get("deck_id") == deck_id:
            return d
    return None


def _file_fingerprint(rel: str):
    """artifact 指纹：存在记 sha256，缺失显式 missing（不伪造）。"""
    if not rel:
        return {"path": "", "exists": False, "sha256": ""}
    p = _AGENT_PLATFORM.parent / rel if not Path(rel).is_absolute() else Path(rel)
    if p.exists():
        data = p.read_bytes()
        return {
            "path": rel,
            "exists": True,
            "sha256": hashlib.sha256(data).hexdigest()[:16],
            "bytes": len(data),
        }
    return {"path": rel, "exists": False, "sha256": ""}


def _artifacts_db() -> Path:
    return _AGENT_PLATFORM / "data" / "artifacts" / "artifacts.db"


def _deck_content_hash(deck_id: str) -> str:
    """当前 deck 产物指纹（M3-P3 门绑定锚点）；无文件返回空不伪造。"""
    deck = find_deck(deck_id)
    if deck is None:
        return ""
    rel = deck.get("artifact", "")
    if not rel:
        return ""
    p = _AGENT_PLATFORM.parent / rel if not Path(rel).is_absolute() else Path(rel)
    if not p.exists():
        return ""
    return fact_hash_of(p.read_bytes())


def register_deck_artifact(
    deck_id: str, artifact_store: ArtifactStore = None
) -> Tuple[Optional[Artifact], bool]:
    """M3-P2：deck 产物文件存在时登记为统一 Artifact（幂等不设门）。

    登记点：drafted（初稿产物诞生）/ delivered（交付达成，首次或
    catch-up 补齐）。文件未指定或缺失 → 跳过不伪造（对齐投影
    _file_fingerprint 的 missing 判例）。kind 按后缀映射，缺省
    "deck"。同内容重复登记=重放零副作用；deck 改版后二次交付走
    store 去重产生新行（supersedes 由调用方按需补链，登记层不强制）。
    """
    deck = find_deck(deck_id)
    if deck is None:
        raise PitchWiringError("deck %s 不存在" % deck_id)
    rel = deck.get("artifact", "")
    if not rel:
        return None, False
    p = _AGENT_PLATFORM.parent / rel if not Path(rel).is_absolute() else Path(rel)
    if not p.exists():
        return None, False
    chash = fact_hash_of(p.read_bytes())
    suffix = p.suffix.lstrip(".").lower()
    kind = suffix if suffix in ("pptx", "ppt", "pdf", "md", "html") else "deck"
    own_store = artifact_store is None
    store = artifact_store or ArtifactStore(_artifacts_db())
    try:
        return store.record(
            Artifact(
                kind=kind,
                path=rel,
                fact_hash=chash,
                content_hash=chash,
                source_system="pitch.decks",
                source_ref=deck_id,
                produced_by="COMM1",
            )
        )
    finally:
        if own_store:
            store.close()


def _artifacts_db() -> Path:
    return _AGENT_PLATFORM / "data" / "artifacts" / "artifacts.db"


def add_deck(
    deck_id: str, title: str, audience: str = "", goal: str = "", artifact: str = ""
) -> dict:
    """登记新 deck（账本真源初始化，无门——登记≠承诺）。"""
    if not deck_id or not title:
        raise PitchWiringError("deck_id 与 title 必填")
    if find_deck(deck_id):
        raise PitchWiringError("deck %s 已存在（id 唯一）" % deck_id)
    deck = {
        "deck_id": deck_id,
        "title": title,
        "audience": audience,
        "goal": goal,
        "status": "outline",
        "claims": [],  # [{text, kind}] 四分类声明
        "artifact": artifact,  # 相对仓库根路径或空
        "created": _now_cn(),
        "history": [
            {
                "at": _now_cn(),
                "action": "create",
                "by": "system",
                "note": "registered at outline",
            }
        ],
    }
    rows = load_decks()
    rows.append(deck)
    save_decks(rows)
    return deck


def set_claims(deck_id: str, claims: List[dict]) -> dict:
    """四分类声明（覆盖式更新；每条 kind 必须合法）。"""
    for c in claims:
        if c.get("kind") not in CLAIM_KINDS:
            raise PitchWiringError(
                "claim kind 须为 %s，收到 %r" % (CLAIM_KINDS, c.get("kind"))
            )
        if not str(c.get("text", "")).strip():
            raise PitchWiringError("claim.text 必填")
    rows = load_decks()
    deck = next((d for d in rows if d.get("deck_id") == deck_id), None)
    if deck is None:
        raise PitchWiringError("deck %s 不存在" % deck_id)
    deck["claims"] = [{"text": c["text"], "kind": c["kind"]} for c in claims]
    save_decks(rows)
    return deck


def _reload_and_replace(deck_id: str, updated: dict) -> List[dict]:
    rows = load_decks()
    return [updated if d.get("deck_id") == deck_id else d for d in rows]


def _append_history(deck: dict, action: str, by: str, note: str) -> None:
    deck.setdefault("history", []).append(
        {"at": _now_cn(), "action": action, "by": by, "note": note}
    )


def move_stage(
    deck_id: str,
    to_stage: str,
    by: str,
    note: str = "",
    artifact_store: ArtifactStore = None,
) -> dict:
    """自由推进（非受门转移）：±1 步，追加 history。

    M3-P2：推进到 drafted 达成时自动登记 deck 产物文件（存在才登记，
    幂等不设门；artifact_store 可注入测试）。
    """
    if not by:
        raise PitchWiringError("操作人 by 必填")
    deck = find_deck(deck_id)
    if deck is None:
        raise PitchWiringError("deck %s 不存在" % deck_id)
    from_status = deck.get("status", "")
    if from_status in ("delivered",):
        raise PitchWiringError("deck %s 已终态 delivered，禁止再转移" % deck_id)
    if to_stage not in LEGAL_TRANSITIONS.get(from_status, []):
        raise PitchWiringError(
            "非法转移 %s→%s（合法：%s）"
            % (from_status, to_stage, LEGAL_TRANSITIONS.get(from_status))
        )
    if to_stage in GATED:
        raise PitchWiringError(
            "→%s 是受门转移，须走 approve_transition（用户级承诺）" % to_stage
        )
    if to_stage == "fact_check" and not deck.get("claims"):
        raise PitchWiringError(
            "claims 为空：进入事实审查前必须声明事实/假设/预测/待补证"
            "（架构红线，禁止编造）"
        )
    deck["status"] = to_stage
    _append_history(deck, "move:%s" % to_stage, by, note or "stage move")
    save_decks(_reload_and_replace(deck_id, deck))
    if to_stage == "drafted":  # M3-P2 自动登记产物
        register_deck_artifact(deck_id, artifact_store)
    return find_deck(deck_id)


def approve_transition(
    deck_id: str,
    to_status: str,
    by: str,
    reason: str = "",
    store: ApprovalStore = None,
    artifact_store: ArtifactStore = None,
) -> Tuple[dict, bool]:
    """受门转移（→approved / →delivered）：凭据先落，再改真源。

    M3-P2：delivered 达成（首次或 catch-up 补齐）时自动登记 deck
    产物文件（存在才登记，幂等不设门）。
    """
    if not by:
        raise PitchWiringError("批准人 by 必填（审批可追溯）")
    deck = find_deck(deck_id)
    if deck is None:
        raise PitchWiringError("deck %s 不存在" % deck_id)
    from_status = deck.get("status", "")
    if to_status not in GATED:
        raise PitchWiringError(
            "→%s 不是受门状态（受门：%s）" % (to_status, sorted(GATED))
        )
    if to_status not in LEGAL_TRANSITIONS.get(from_status, []):
        raise PitchWiringError(
            "非法转移 %s→%s（合法：%s）；跳级违反生成/审查/批准/交付分离"
            % (from_status, to_status, LEGAL_TRANSITIONS.get(from_status, []))
        )

    own_store = store is None
    store = store or ApprovalStore(_db_path())
    try:
        own = [
            a
            for a in store.list(target_type="workitem")
            if a.target_id == "pitch:%s" % deck_id
        ]
        # M3-P3 批 A 发 B 拒绝（delivered 前置校验）：上一道门（approved）
        # 凭据绑定过内容指纹，且当前 deck 内容已变更 → 原审批失效，
        # 须打回 reviewed 重新走门；拒绝在旧批准上直接交付新内容。
        if to_status == "delivered":
            approved_creds = [
                a for a in own if a.to_status == "approved" and a.content_hash
            ]
            cur_hash = _deck_content_hash(deck_id)
            if (
                approved_creds
                and cur_hash
                and approved_creds[-1].content_hash != cur_hash
            ):
                raise PitchWiringError(
                    "批 A 发 B 拒绝：approved 凭据 %s 绑定 content_hash=%s…，"
                    "当前 deck=%s…（内容已变更，原审批失效）——打回 reviewed"
                    " 修改后重新走门"
                    % (
                        approved_creds[-1].approval_id[:12],
                        approved_creds[-1].content_hash[:12],
                        cur_hash[:12],
                    )
                )
        # catch-up：最新凭据恰是本转移、且该转移从未落过 history
        # （凭据落库后、真源写入前崩溃的窗口）→ 补执行，重放返回。
        # history 已有 approve:%s 痕迹 = 凭据曾生效（之后可能被打回），
        # 不得 catch-up——打回重批交给下方轮次化键处理（academy 同款：
        # 打回走自由 move 不落凭据，own[-1] 启发式不可靠）。
        applied = {h.get("action") for h in deck.get("history", [])}
        prior_same = [
            a for a in own if a.from_status == from_status and a.to_status == to_status
        ]
        if (
            prior_same
            and own[-1].idempotency_key == prior_same[-1].idempotency_key
            and ("approve:%s" % to_status) not in applied
        ):
            deck2 = find_deck(deck_id)
            if deck2.get("status") != to_status:
                deck2["status"] = to_status
                _append_history(
                    deck2, "approve:%s" % to_status, by, "catch-up（凭据已落补执行）"
                )
                save_decks(_reload_and_replace(deck_id, deck2))
                if to_status == "delivered":  # M3-P2 catch-up 补登记
                    register_deck_artifact(deck_id, artifact_store)
            return find_deck(deck_id), True

        # 轮次化幂等键：approved→reviewed 打回后重批（reviewed→approved）
        # 是新一轮批准，必须留新凭据（decided_by/reason 可追溯），不得
        # 重放第一轮凭据——同轮重放仍由既有凭据幂等兜底。
        base = "pitch:%s:%s>%s" % (deck_id, from_status, to_status)
        key = base if not prior_same else "%s#r%d" % (base, len(prior_same) + 1)
        approval, replayed = store.record(
            Approval(
                target_type="workitem",
                target_id="pitch:%s" % deck_id,
                from_status=from_status,
                to_status=to_status,
                decision="approved",
                decided_by=by,
                reason=reason,
                decided_at=_now_iso(),
                idempotency_key=key,
                content_hash=_deck_content_hash(deck_id),  # M3-P3 批准时点绑定
            )
        )
        if replayed:
            # 防御分支：轮次化后 key 理论全新；并发窗口同 key 抢先落库时
            # 真源若滞后补齐，否则原样返回
            deck2 = find_deck(deck_id)
            if deck2.get("status") != to_status:
                deck2["status"] = to_status
                _append_history(
                    deck2, "approve:%s" % to_status, by, "catch-up（凭据已落补执行）"
                )
                save_decks(_reload_and_replace(deck_id, deck2))
            return find_deck(deck_id), True

        deck = find_deck(deck_id)
        deck["status"] = to_status
        _append_history(deck, "approve:%s" % to_status, by, reason or to_status)
        save_decks(_reload_and_replace(deck_id, deck))
        if to_status == "delivered":  # M3-P2 自动登记产物
            register_deck_artifact(deck_id, artifact_store)
        return find_deck(deck_id), False
    finally:
        if own_store:
            store.close()


def projection() -> dict:
    """只读投影：deck + claims 四分类计数 + artifact 指纹。"""
    items = []
    for d in load_decks():
        counts = {k: 0 for k in CLAIM_KINDS}
        for c in d.get("claims", []):
            if c.get("kind") in counts:
                counts[c["kind"]] += 1
        items.append(
            {
                "deck_id": d.get("deck_id", ""),
                "title": d.get("title", ""),
                "audience": d.get("audience", ""),
                "status": d.get("status", ""),
                "claims_total": sum(counts.values()),
                "claims_by_kind": counts,
                "artifact": _file_fingerprint(d.get("artifact", "")),
                "history_len": len(d.get("history", [])),
                "created": d.get("created", ""),
            }
        )
    return {"domain": "pitch", "count": len(items), "decks": items}


def audit_report() -> dict:
    """越门审计：账本凭据回放 vs 真源现状。"""
    decks = {d.get("deck_id"): d for d in load_decks()}
    store = ApprovalStore(_db_path())
    try:
        creds = [c for c in store.list() if c.target_id.startswith("pitch:")]
    finally:
        store.close()
    violations = []
    checks = []
    for c in creds:
        did = c.target_id.split(":", 1)[-1]
        deck = decks.get(did)
        if deck is None:
            violations.append(
                {
                    "kind": "credential_target_missing",
                    "approval_id": c.approval_id,
                    "deck_id": did,
                }
            )
            continue
        acts = [
            h.get("action", "") for h in deck.get("history", []) if isinstance(h, dict)
        ]
        acts = [
            h.get("action", "") for h in deck.get("history", []) if isinstance(h, dict)
        ]
        ok = (
            deck.get("status") == c.to_status
            or (
                # 轮次语义：该轮 approve 痕迹存在且其后有 move（合法打回）；
                # 仅状态被改而无 move 痕迹 = 绕门，仍报 mismatch
                "approve:%s" % c.to_status in acts
                and any(
                    a.startswith("move:")
                    for a in acts[
                        len(acts) - acts[::-1].index("approve:%s" % c.to_status) :
                    ]
                )
            )
            or (
                # 轮次语义：该轮 approve 痕迹存在且其后有 move（合法打回）；
                # 仅状态被改而无 move 痕迹 = 绕门，仍报 mismatch
                "approve:%s" % c.to_status in acts
                and any(
                    a.startswith("move:")
                    for a in acts[
                        len(acts) - acts[::-1].index("approve:%s" % c.to_status) :
                    ]
                )
            )
        )
        checks.append(
            {
                "approval_id": c.approval_id,
                "deck_id": did,
                "transition": "%s>%s" % (c.from_status, c.to_status),
                "decided_by": c.decided_by,
                "state": "consistent" if ok else "status_mismatch",
            }
        )
        if not ok:
            violations.append(
                {
                    "kind": "status_mismatch",
                    "deck_id": did,
                    "ledger_to": c.to_status,
                    "actual": deck.get("status"),
                }
            )
    # 交叉校验：凭据轮次 vs history 痕迹（pitch=trace 域，action 即痕迹）
    traces = {}
    for did, d in decks.items():
        sts = []
        for h in d.get("history") or []:
            act = h.get("action", "") if isinstance(h, dict) else ""
            if act.startswith(("approve:", "move:")):
                sts.append(act.split(":", 1)[1])
        traces["pitch:%s" % did] = {"statuses": sts, "consumed_keys": set()}
    cross_v = cross_check(
        "pitch",
        creds,
        traces,
        {"pitch:%s" % did: d.get("status", "") for did, d in decks.items()},
    )
    return {
        "domain": "pitch",
        "deck_count": len(decks),
        "credentials": checks,
        "violations": violations,
        "cross_check": cross_v,
        "verdict": verdict_for(
            ([{"severity": "FAIL"}] if violations else []) + cross_v
        ),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M1 路演 PPT 域接线")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("report")
    p_add = sub.add_parser("add")
    p_add.add_argument("deck_id")
    p_add.add_argument("title")
    p_add.add_argument("--audience", default="")
    p_add.add_argument("--goal", default="")
    p_add.add_argument("--artifact", default="")
    p_show = sub.add_parser("show")
    p_show.add_argument("deck_id")
    p_move = sub.add_parser("move")
    p_move.add_argument("deck_id")
    p_move.add_argument("to_stage")
    p_move.add_argument("--by", required=True)
    p_move.add_argument("--note", default="")
    p_ap = sub.add_parser("approve")
    p_ap.add_argument("deck_id")
    p_ap.add_argument("to_status", choices=sorted(GATED))
    p_ap.add_argument("--by", required=True)
    p_ap.add_argument("--reason", default="")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        print(json.dumps(projection(), ensure_ascii=False, indent=2))
    elif args.cmd == "report":
        print(json.dumps(audit_report(), ensure_ascii=False, indent=2))
    elif args.cmd == "add":
        print(
            json.dumps(
                add_deck(
                    args.deck_id, args.title, args.audience, args.goal, args.artifact
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.cmd == "show":
        d = find_deck(args.deck_id)
        if d is None:
            raise SystemExit("deck 不存在")
        print(json.dumps(d, ensure_ascii=False, indent=2))
    elif args.cmd == "move":
        print(
            json.dumps(
                move_stage(args.deck_id, args.to_stage, args.by, args.note),
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.cmd == "approve":
        deck, replayed = approve_transition(
            args.deck_id, args.to_status, args.by, args.reason
        )
        print(
            json.dumps(
                {"deck": deck, "replayed": replayed}, ensure_ascii=False, indent=2
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
