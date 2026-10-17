# -*- coding: utf-8 -*-
"""M1 业务域写侧接线 · GZH（自媒体）首发。

接线语义（与 M0 判例一致）：
- **状态真源不动**：gzh/drafts/index.json 仍是草稿发布审批态
  （draft/reviewed/published）的唯一真源；本模块不另建第二状态账本；
- **唯一写路径**：index 中 status 的受控转移（draft→reviewed→published）
  只能经 approve_transition() 完成，且必须先落 Approval 凭据；
  直接跳级（draft→published）拒绝——生成、审查、批准、发布分离；
- **无 Approval 不发布**：任何把 published 写进 index 的路径如果绕过
  本模块，回放审计（report 对比 approvals 账本）会显式暴露差异；
- **幂等**：审批键 gzh:<draft_id>:<from>→<to> 确定性生成，重复批准
  返回既有凭据（replayed=True），index 不重复变更；
- **投影可重建**：project_workitems() 输出 WorkItem+Artifact 投影
  （含 md 指纹），全部可从真源重建，不落第二份持久状态。

CLI：
    python domains/gzh_wiring.py list   [--root .] [--json]
    python domains/gzh_wiring.py report [--root .]
    python domains/gzh_wiring.py approve <draft_id> <to_status>
           --by <批准人> [--reason "..."] [--root .]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_AGENT_PLATFORM = Path(__file__).resolve().parents[1]
if str(_AGENT_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_AGENT_PLATFORM))

from contracts.objects import Approval, Artifact, WorkItem, fact_hash_of  # noqa: E402
from engine.approvals import ApprovalStore  # noqa: E402
from engine.artifacts import ArtifactStore  # noqa: E402
from adapters.credential_trace import cross_check, verdict_for  # noqa: E402
from engine.artifacts import ArtifactStore  # noqa: E402
from adapters.credential_trace import cross_check, verdict_for  # noqa: E402

SCHEMA_VERSION = "1.0"
DOMAIN = "gzh"
SOURCE = "gzh.drafts_index"

# GZH 域内原生状态机：draft（生成）→ reviewed（审查通过）→ published（批准发布）。
# published 终态；reviewed 可打回 draft（修改后重新审查）。
LEGAL_TRANSITIONS: Dict[str, List[str]] = {
    "draft": ["reviewed"],
    "reviewed": ["published", "draft"],
    "published": [],
}
TERMINAL_STATUSES = {"published"}


class GzhWiringError(Exception):
    """GZH 接线失败（显式错误，不静默降级）。"""


# ----------------------------------------------------------------------
# 真源读写
# ----------------------------------------------------------------------
def _index_path(root: Path) -> Path:
    return Path(root) / "gzh" / "drafts" / "index.json"


def load_index(root: Path) -> List[dict]:
    path = _index_path(root)
    if not path.exists():
        raise GzhWiringError("missing_source: %s 不存在" % path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise GzhWiringError("index.json 解析失败: %s" % exc)
    if not isinstance(raw, list):
        raise GzhWiringError("index.json 顶层必须是数组，实际 %s"
                             % type(raw).__name__)
    return [d for d in raw if isinstance(d, dict)]


def save_index(root: Path, items: List[dict]) -> None:
    """原子写回（与 gzh_writer.save_index 同格式：indent=1, ensure_ascii=False）。"""
    path = _index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def find_draft(root: Path, draft_id: str) -> dict:
    for d in load_index(root):
        if d.get("id") == draft_id:
            return d
    raise GzhWiringError("draft_id=%s 不在 index.json 中（无源 ID 不伪造）" % draft_id)


# ----------------------------------------------------------------------
# 只读投影：WorkItem + Artifact
# ----------------------------------------------------------------------
def project_workitems(root: Path) -> Tuple[List[WorkItem], List[Artifact]]:
    """index.json → 统一 WorkItem/Artifact 投影（可随时重建，不持久化）。"""
    root = Path(root)
    drafts_dir = root / "gzh" / "drafts"
    items, artifacts = [], []
    for d in load_index(root):
        draft_id = d.get("id", "")
        md_name = d.get("md", "")
        md_path = drafts_dir / md_name if md_name else None
        exists = bool(md_path) and md_path.exists()
        wi = WorkItem(
            workitem_id="gzh:%s" % draft_id,          # 从领域真源 ID 确定性派生
            domain=DOMAIN,
            source=SOURCE,
            source_ref=draft_id,
            title=d.get("title", ""),
            status=d.get("status", ""),               # 域内原生状态，原样保留
            owner="COMM1",
            payload={"col": d.get("col", ""), "date": d.get("date", ""),
                     "ts": d.get("ts", "")},
        )
        items.append(wi)
        if md_name:
            content = md_path.read_bytes() if exists else b""
            artifacts.append(Artifact(
                artifact_id="gzh-art:%s" % draft_id,
                workitem_id=wi.workitem_id,
                kind="md",
                path="gzh/drafts/%s" % md_name,
                fact_hash=fact_hash_of(content) if exists else "",
                missing=not exists,
            ))
    return items, artifacts


# ----------------------------------------------------------------------
# M3-P2 产物挂接：reviewed 转移自动登记 md artifact（登记不设门）
# ----------------------------------------------------------------------
def _artifacts_db(root: Path) -> Path:
    return Path(root) / "agent_platform" / "data" / "artifacts" / "artifacts.db"


def register_review_artifact(root: Path, draft_id: str,
                             artifact_store: ArtifactStore = None
                             ) -> Tuple[Optional[Artifact], bool]:
    """把草稿 md 登记为统一 Artifact（幂等，同 hash 去重）。

    判例「登记不设门」：登记是事实记录，不做任何状态校验、不落
    Approval——审查承诺由 approve_transition 的凭据承担。md 缺失时
    跳过（投影 missing 标记 + audit_report 已 fail-visible 暴露，
    此处不伪造空 hash 条目——「无源 ID 不伪造」）。
    崩溃语义：本函数幂等，approve_transition 在首次批准与 catch-up
    两条路径都调用，崩溃后重放零副作用。
    """
    root = Path(root)
    draft = find_draft(root, draft_id)
    md_name = draft.get("md", "")
    if not md_name:
        return None, False
    md_path = root / "gzh" / "drafts" / md_name
    if not md_path.exists():
        return None, False
    content = md_path.read_bytes()
    chash = fact_hash_of(content)
    own_store = artifact_store is None
    store = artifact_store or ArtifactStore(_artifacts_db(root))
    try:
        return store.record(Artifact(
            kind="md",
            path="gzh/drafts/%s" % md_name,
            fact_hash=chash,
            content_hash=chash,
            source_system="gzh.drafts",
            source_ref=draft_id,
            produced_by="COMM1",
        ))
    finally:
        if own_store:
            store.close()


# ----------------------------------------------------------------------
# 受控写路径：审批驱动的状态转移
# ----------------------------------------------------------------------
def _now_cn() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%m-%d %H:%M")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _md_content_hash(root: Path, draft_id: str) -> str:
    """当前 md 内容指纹（M3-P3 发布闸门绑定锚点）；缺失返回空。"""
    draft = find_draft(root, draft_id)
    md_name = draft.get("md", "")
    if not md_name:
        return ""
    md_path = Path(root) / "gzh" / "drafts" / md_name
    if not md_path.exists():
        return ""
    return fact_hash_of(md_path.read_bytes())


def approve_transition(root: Path, draft_id: str, to_status: str, by: str,
                       reason: str = "", store: ApprovalStore = None,
                       artifact_store: ArtifactStore = None
                       ) -> Tuple[Approval, dict, bool]:
    """批准并执行一次受控状态转移。

    返回 (approval, draft_after, replayed)。
    - 幂等快路径：目标状态已达成且账本有凭据 → 原样返回（replayed=True）；
    - 转移非法（未知状态/跳级/终态再转移）→ GzhWiringError，零写入；
    - 凭据已存在但真源滞后（凭据落库后、真源写入前崩溃的窗口）→
      replayed=True 且补齐真源写入（catch-up，保证恰好在源上生效一次）；
    - 首次批准 → 落 Approval 凭据后原子更新 index.status/ts；
    - M3-P2：draft→reviewed 达成（首次批准或 catch-up 补齐）时自动
      登记 md 为 Artifact（幂等登记不设门；artifact_store 可注入测试）。
    """
    if not by:
        raise GzhWiringError("批准人 by 必填（审批可追溯）")
    root = Path(root)
    draft = find_draft(root, draft_id)
    from_status = draft.get("status", "")

    own_store = store is None
    store = store or ApprovalStore(_AGENT_PLATFORM / "data" / "approvals"
                                   / "approvals.db")
    try:
        def _find(to: str):
            hits = [a for a in store.list(target_type="workitem")
                    if a.target_id == "gzh:%s" % draft_id
                    and a.to_status == to]
            return hits[-1] if hits else None  # 最新凭据（打回重批取新一轮）

        # 幂等快路径：目标已达成且有凭据 → 稳定 no-op
        if to_status == from_status:
            hit = _find(to_status)
            if hit:
                # M3-P3 批 A 发 B 拒绝：凭据绑定过内容指纹且真源已变更
                # → 原审批自动失效（总纲：审批绑定 revision 与
                # content_hash），须打回重批走新一轮，拒绝静默重放。
                if hit.content_hash:
                    cur = _md_content_hash(root, draft_id)
                    if cur and cur != hit.content_hash:
                        raise GzhWiringError(
                            "批 A 发 B 拒绝：凭据 %s 绑定 content_hash=%s…，"
                            "当前真源=%s…（内容已变更，原审批失效）——"
                            "打回 draft 修改后重新走门"
                            % (hit.approval_id[:12], hit.content_hash[:12],
                               cur[:12]))
                return hit, draft, True
            raise GzhWiringError(
                "draft %s 状态已为 %s 但账本无凭据：疑似绕过审批门修改真源，"
                "拒绝重复批准（先跑 report 审计）" % (draft_id, from_status))

        if from_status in TERMINAL_STATUSES:
            raise GzhWiringError("draft %s 已终态 published，禁止再转移" % draft_id)
        if to_status not in LEGAL_TRANSITIONS.get(from_status, []):
            raise GzhWiringError(
                "非法转移 %s→%s（合法：%s）；跳级违反生成/审查/批准/发布分离"
                % (from_status, to_status,
                   LEGAL_TRANSITIONS.get(from_status, [])))

        own = [a for a in store.list(target_type="workitem")
               if a.target_id == "gzh:%s" % draft_id]
        # catch-up / 轮次化判定（last_wiring_key 消费标记，devtask 判例，
        # 与 invest/devtask 统一；academy 用 history 痕迹系其域内有轨迹）：
        # - 最新凭据恰是本转移且真源未消费它（last_wiring_key 不等于该键）
        #   → 崩溃窗口 catch-up：补齐真源写入，重放返回，不新增轮次凭据；
        # - 最新凭据已消费但真源又回到 from（同转移反复：如二次打回）
        #   → 新轮次批准，落 #r{n+1} 新凭据留痕。
        prior_same = [a for a in own
                      if a.from_status == from_status
                      and a.to_status == to_status]
        if (prior_same
                and own[-1].idempotency_key == prior_same[-1].idempotency_key):
            last = own[-1]
            if draft.get("last_wiring_key") != last.idempotency_key:
                _apply_status(root, draft_id, to_status,
                              last.idempotency_key)
                if to_status == "reviewed":       # M3-P2 catch-up 补登记
                    register_review_artifact(root, draft_id, artifact_store)
                return last, find_draft(root, draft_id), True   # catch-up

        # 轮次化幂等键：打回后重批（draft→reviewed→draft→reviewed）是
        # 新的一轮批准，必须留新凭据（第二轮 decided_by/reason 可追溯），
        # 不得重放第一轮凭据——同轮重放仍由既有凭据幂等兜底。
        base = "gzh:%s:%s>%s" % (draft_id, from_status, to_status)
        key = (base if not prior_same
               else "%s#r%d" % (base, len(prior_same) + 1))
        approval, replayed = store.record(Approval(
            target_type="workitem",
            target_id="gzh:%s" % draft_id,
            from_status=from_status,
            to_status=to_status,
            decision="approved",
            decided_by=by,
            reason=reason,
            decided_at=_now_iso(),
            idempotency_key=key,
            content_hash=(_md_content_hash(root, draft_id)
                          if to_status == "published" else ""),  # M3-P3
        ))
        if not replayed:
            # 首次批准：落凭据成功后走唯一授权写路径
            _apply_status(root, draft_id, to_status, key)
            if to_status == "reviewed":           # M3-P2 自动登记产物
                register_review_artifact(root, draft_id, artifact_store)
            return approval, find_draft(root, draft_id), False
        # 防御分支：轮次化后 key 理论全新；并发窗口同 key 抢先落库时
        # 真源若滞后补齐，否则原样返回
        if draft.get("status") != to_status:
            _apply_status(root, draft_id, to_status, key)
            return approval, find_draft(root, draft_id), True
        return approval, draft, True
    finally:
        if own_store:
            store.close()


def _apply_status(root: Path, draft_id: str, to_status: str,
                  wiring_key: str = "") -> None:
    """唯一授权写路径：更新 index 中该草稿的 status/ts（原子写）。

    last_wiring_key 记录已消费的凭据键（catch-up 判据，devtask 判例回灌）。
    """
    items = load_index(root)
    for it in items:
        if it.get("id") == draft_id:
            it["status"] = to_status
            it["ts"] = _now_cn()
            if wiring_key:
                it["last_wiring_key"] = wiring_key
    save_index(root, items)


# ----------------------------------------------------------------------
# 审计：真源状态 vs 审批账本回放比对
# ----------------------------------------------------------------------
def audit_report(root: Path, store: ApprovalStore = None) -> dict:
    """发布审批态一致性审计。

    published/reviewed 必须有对应 Approval 凭据；缺失 = 有人绕过审批门
    改了 index（真源被污染），显式列出。
    """
    root = Path(root)
    own_store = store is None
    store = store or ApprovalStore(_AGENT_PLATFORM / "data" / "approvals"
                                   / "approvals.db")
    try:
        items, artifacts = project_workitems(root)
        approvals = store.list(target_type="workitem")
        violations = []
        for wi in items:
            if wi.status in ("reviewed", "published"):
                # 该状态必须存在一次 →status 的批准凭据，否则有人绕过审批门
                hit = [a for a in approvals
                       if a.target_id == wi.workitem_id
                       and a.to_status == wi.status]
                if not hit:
                        violations.append({
                            "draft_id": wi.source_ref,
                            "status": wi.status,
                            "finding": "状态=%s 但审批账本无对应凭据"
                                       % wi.status,
                        })
        missing_art = [a.path for a in artifacts if a.missing]
        # 交叉校验：凭据轮次 vs 消费标记（gzh=latest_only 标记域：
        # last_wiring_key 只存最新已消费键，旧轮次不可回溯故跳过）
        raw_items = {it.get("id", ""): it for it in load_index(root)}
        traces = {}
        for iid, it in raw_items.items():
            traces["gzh:%s" % iid] = {
                "statuses": [],
                "consumed_keys": {it.get("last_wiring_key")}
                if it.get("last_wiring_key") else set(),
            }
        gzh_creds = [a for a in approvals
                     if a.target_id.startswith("gzh:")]
        cross_v = cross_check("gzh", gzh_creds, traces,
                              {"gzh:%s" % iid: it.get("status", "")
                               for iid, it in raw_items.items()},
                              latest_only=True)
        return {
            "schema_version": SCHEMA_VERSION,
            "domain": DOMAIN,
            "workitem_count": len(items),
            "by_status": _by_status(items),
            "approval_count": len(approvals),
            "violations": violations,
            "cross_check": cross_v,
            "missing_artifacts": missing_art,
            "verdict": verdict_for(
                ([{"severity": "FAIL"}]
                 if violations or missing_art else []) + cross_v),
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
    ap = argparse.ArgumentParser(description="M1 GZH 域接线")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", parents=[common])
    sub.add_parser("report", parents=[common])
    p_ap = sub.add_parser("approve", parents=[common])
    p_ap.add_argument("draft_id")
    p_ap.add_argument("to_status", choices=["reviewed", "published", "draft"])
    p_ap.add_argument("--by", required=True)
    p_ap.add_argument("--reason", default="")

    args = ap.parse_args(argv)
    root = Path(args.root)

    if args.cmd == "list":
        items, artifacts = project_workitems(root)
        for wi, art in zip(items, artifacts):
            print("%-28s %-10s %-12s hash=%s%s" % (
                wi.workitem_id, wi.status, wi.title[:20],
                art.fact_hash[:12] if art.fact_hash else "-",
                " [正文缺失]" if art.missing else ""))
        return 0
    if args.cmd == "report":
        print(json.dumps(audit_report(root), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "approve":
        approval, after, replayed = approve_transition(
            root, args.draft_id, args.to_status, args.by, args.reason)
        print("[gzh_wiring] %s %s：%s→%s%s" % (
            args.draft_id, "重放" if replayed else "批准",
            approval.from_status, approval.to_status,
            "" if not replayed else "（凭据 %s 已存在，真源未重复变更）"
            % approval.approval_id[:12]))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
