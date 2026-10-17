# -*- coding: utf-8 -*-
"""M1 业务域写侧接线 · Invest（股票投研）。

领域语义（真源分工）：
- **执行底账不动**：agent_platform/data/checkpoints.db（runs/checkpoints 表）
  是 run_invest 执行链的既有真源（running/done/failed + 节点状态快照），
  本模块只读（sqlite mode=ro URI），绝不写；
- **审批态真源补齐**：data/invest/runs_index.json 是投研 run 审批态
  （drafted/reviewed/adopted）的唯一真源——投研域此前只有执行态没有
  审批态承载物，本文件是补齐真源而非第二账本（checkpoints 记执行，
  runs_index 记采纳，职责分离）；
- **无源不伪造**：登记 register_run 只接受 checkpoints.db 中 status=done
  且存在 d1_decision 决策快照的 run；查不到/未完成/无决策一律显式拒绝；
- **决策指纹**：登记时对 D1 决策全文算 sha256；审计时重算比对——
  登记 run 后决策被重跑覆盖/篡改会显式暴露（指纹漂移）；
- **状态机**：drafted（研究完成已登记）→ reviewed（复核通过）→
  adopted（采纳，用户级承诺）；reviewed 可打回 drafted；adopted 终态；
- **六铁律**（与 GZH/Academy 同款）：单一写路径（状态变更只能经
  approve_transition，先落 Approval 凭据）、禁止跳级、终态冻结、
  幂等键 invest:<run_id>:<from>→<to>、崩溃窗口 catch-up、
  审计回放比对（reviewed/adopted 必须有凭据）。

CLI：
    python domains/invest_wiring.py list     [--root .]
    python domains/invest_wiring.py report   [--root .]
    python domains/invest_wiring.py register <run_id> [--root .]
    python domains/invest_wiring.py approve  <run_id> <reviewed|adopted|drafted>
           --by <批准人> [--reason "..."] [--root .]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_AGENT_PLATFORM = Path(__file__).resolve().parents[1]
if str(_AGENT_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_AGENT_PLATFORM))

from contracts.objects import Approval, Artifact, WorkItem, fact_hash_of  # noqa: E402
from engine.approvals import ApprovalStore  # noqa: E402
from adapters.credential_trace import cross_check, verdict_for  # noqa: E402

SCHEMA_VERSION = "1.0"
DOMAIN = "invest"
SOURCE = "invest.runs_index"

# 投研域审批态状态机：drafted（登记）→ reviewed（复核）→ adopted（采纳）。
# adopted 终态；reviewed 可打回 drafted（补充研究后重新复核）。
LEGAL_TRANSITIONS: Dict[str, List[str]] = {
    "drafted": ["reviewed"],
    "reviewed": ["adopted", "drafted"],
    "adopted": [],
}
TERMINAL_STATUSES = {"adopted"}
AUDIT_GATED_STATUSES = {"reviewed", "adopted"}   # 这些状态必须有凭据
DECISION_NODE = "d1_decision"                    # D1 最终决策 checkpoint 节点


class InvestWiringError(Exception):
    """Invest 接线失败（显式错误，不静默降级）。"""


# ----------------------------------------------------------------------
# 真源路径与读写
# ----------------------------------------------------------------------
def _index_path(root: Path) -> Path:
    return Path(root) / "agent_platform" / "data" / "invest" / "runs_index.json"


def _checkpoints_path(root: Path) -> Path:
    return Path(root) / "agent_platform" / "data" / "checkpoints.db"


def load_index(root: Path) -> List[dict]:
    path = _index_path(root)
    if not path.exists():
        return []                       # 尚无登记：空审批态（合法初态）
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise InvestWiringError("runs_index.json 解析失败: %s" % exc)
    if not isinstance(raw, list):
        raise InvestWiringError("runs_index.json 顶层必须是数组，实际 %s"
                                % type(raw).__name__)
    return [r for r in raw if isinstance(r, dict)]


def save_index(root: Path, entries: List[dict]) -> None:
    """原子写回（indent=2，与 academy 真源同风格）。"""
    path = _index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def find_entry(root: Path, run_id: str) -> dict:
    for r in load_index(root):
        if r.get("run_id") == run_id:
            return r
    raise InvestWiringError("run_id=%s 未登记（先 register，无源 ID 不伪造）"
                            % run_id)


# ----------------------------------------------------------------------
# 执行底账只读访问（checkpoints.db 绝不写）
# ----------------------------------------------------------------------
def _readonly_conn(db_path: Path) -> sqlite3.Connection:
    if not Path(db_path).exists():
        raise InvestWiringError("missing_source: %s 不存在" % db_path)
    return sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)


def _iso(epoch: Optional[float]) -> str:
    if not epoch:
        return ""
    return datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


def fetch_run(root: Path, run_id: str) -> dict:
    """从 checkpoints.db 只读取 run 摘要 + D1 决策全文 + 指纹。

    run 不存在 / 未完成 / 无 d1_decision 决策快照 → 显式拒绝（无源不伪造）。
    """
    conn = _readonly_conn(_checkpoints_path(root))
    try:
        row = conn.execute(
            "SELECT stock, started_at, finished_at, status FROM runs"
            " WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise InvestWiringError(
                "run_id=%s 在 checkpoints.db 无源（无源 ID 不伪造）" % run_id)
        stock, started_at, finished_at, status = row
        if status != "done":
            raise InvestWiringError(
                "run_id=%s 执行状态=%s（仅 done 可登记）" % (run_id, status))
        srow = conn.execute(
            "SELECT state_json FROM checkpoints"
            " WHERE run_id=? AND node_key=?", (run_id, DECISION_NODE)).fetchone()
        if not srow:
            raise InvestWiringError(
                "run_id=%s 无 %s 决策快照，拒绝登记（无源不伪造）"
                % (run_id, DECISION_NODE))
        decision = (json.loads(srow[0]) or {}).get("output", "")
        if not decision:
            raise InvestWiringError(
                "run_id=%s 的 %s 决策快照 output 为空，拒绝登记"
                % (run_id, DECISION_NODE))
        return {
            "run_id": run_id,
            "stock": stock or "",
            "started_at": _iso(started_at),
            "finished_at": _iso(finished_at),
            "run_status": status,
            "decision": decision,
            "decision_hash": fact_hash_of(decision.encode("utf-8")),
            "decision_chars": len(decision),
        }
    finally:
        conn.close()


def current_decision_hash(root: Path, run_id: str) -> str:
    """审计用：重算 checkpoints.db 当前决策指纹（漂移检测基准）。"""
    conn = _readonly_conn(_checkpoints_path(root))
    try:
        srow = conn.execute(
            "SELECT state_json FROM checkpoints"
            " WHERE run_id=? AND node_key=?", (run_id, DECISION_NODE)).fetchone()
        if not srow:
            return ""
        decision = (json.loads(srow[0]) or {}).get("output", "")
        return fact_hash_of(decision.encode("utf-8")) if decision else ""
    finally:
        conn.close()


# ----------------------------------------------------------------------
# 登记：执行完成 → 审批态初始（幂等）
# ----------------------------------------------------------------------
def register_run(root: Path, run_id: str) -> Tuple[dict, bool]:
    """把一个 done 且有 D1 决策的 run 登记进审批态真源（初始 drafted）。

    返回 (entry, replayed)。幂等：已登记 run_id 原样返回（replayed=True），
    且重放时校验决策指纹一致——漂移显式报错（run 被重跑覆盖的信号）。
    """
    run = fetch_run(root, run_id)           # 无源/未完成/无决策在这里拒绝
    root = Path(root)
    entries = load_index(root)
    for e in entries:
        if e.get("run_id") == run_id:
            if e.get("decision_hash") != run["decision_hash"]:
                raise InvestWiringError(
                    "run %s 已登记但决策指纹漂移（登记后 D1 决策被改动/重跑"
                    "覆盖），先跑 report 审计" % run_id)
            return e, True
    entry = {
        "run_id": run_id,
        "stock": run["stock"],
        "status": "drafted",
        "decision_hash": run["decision_hash"],
        "decision_chars": run["decision_chars"],
        "run_started_at": run["started_at"],
        "run_finished_at": run["finished_at"],
        "registered_at": _now_iso(),
        "ts": _now_iso(),
    }
    entries.append(entry)
    save_index(root, entries)
    return entry, False


# ----------------------------------------------------------------------
# 只读投影：WorkItem + Artifact
# ----------------------------------------------------------------------
def project_workitems(root: Path) -> Tuple[List[WorkItem], List[Artifact]]:
    """runs_index → 统一 WorkItem/Artifact 投影（可随时重建，不持久化）。"""
    root = Path(root)
    items, artifacts = [], []
    for e in load_index(root):
        run_id = e.get("run_id", "")
        wi = WorkItem(
            workitem_id="invest:%s" % run_id,      # 从真源 ID 确定性派生
            domain=DOMAIN,
            source=SOURCE,
            source_ref=run_id,
            title="选股决策 %s" % e.get("stock", ""),
            status=e.get("status", ""),            # 域内原生状态，原样保留
            owner="D1",
            payload={"stock": e.get("stock", ""),
                     "decision_hash": e.get("decision_hash", ""),
                     "decision_chars": e.get("decision_chars", 0),
                     "run_finished_at": e.get("run_finished_at", "")},
        )
        items.append(wi)
        artifacts.append(Artifact(
            artifact_id="invest-art:%s" % run_id,
            workitem_id=wi.workitem_id,
            kind="decision",
            path="agent_platform/data/checkpoints.db#%s" % DECISION_NODE,
            fact_hash=e.get("decision_hash", ""),
            missing=not current_decision_hash(root, run_id),
        ))
    return items, artifacts


# ----------------------------------------------------------------------
# 受控写路径：审批驱动的状态转移
# ----------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def approve_transition(root: Path, run_id: str, to_status: str, by: str,
                       reason: str = "", store: ApprovalStore = None
                       ) -> Tuple[Approval, dict, bool]:
    """批准并执行一次受控状态转移。

    返回 (approval, entry_after, replayed)。语义与 GZH 同款：
    - 幂等快路径：目标状态已达成且账本有凭据 → 原样返回（replayed=True）；
    - 转移非法（未知状态/跳级/终态再转移）→ InvestWiringError，零写入；
    - 凭据已存在但真源滞后（崩溃窗口）→ replayed=True 且补齐真源写入；
    - 首次批准 → 落 Approval 凭据后原子更新 runs_index.status/ts。
    """
    if not by:
        raise InvestWiringError("批准人 by 必填（审批可追溯）")
    root = Path(root)
    entry = find_entry(root, run_id)
    from_status = entry.get("status", "")

    own_store = store is None
    store = store or ApprovalStore(_AGENT_PLATFORM / "data" / "approvals"
                                   / "approvals.db")
    try:
        tid = "invest:%s" % run_id

        def _own() -> List[Approval]:
            # list 按 created_at,rowid 排序 = 审批账本发生序（append-only）
            return [a for a in store.list(target_type="workitem")
                    if a.target_id == tid]

        def _find(to: str) -> Optional[Approval]:
            hits = [a for a in _own() if a.to_status == to]
            return hits[0] if hits else None

        # 幂等快路径：目标已达成且有凭据 → 稳定 no-op
        if to_status == from_status:
            hit = _find(to_status)
            if hit:
                return hit, entry, True
            raise InvestWiringError(
                "run %s 状态已为 %s 但账本无凭据：疑似绕过审批门修改真源，"
                "拒绝重复批准（先跑 report 审计）" % (run_id, from_status))

        if from_status in TERMINAL_STATUSES:
            raise InvestWiringError(
                "run %s 已终态 adopted，禁止再转移" % run_id)
        if to_status not in LEGAL_TRANSITIONS.get(from_status, []):
            raise InvestWiringError(
                "非法转移 %s→%s（合法：%s）；跳级违反研究/复核/采纳分离"
                % (from_status, to_status,
                   LEGAL_TRANSITIONS.get(from_status, [])))

        own = _own()
        # catch-up / 轮次化判定（last_wiring_key 消费标记，devtask 判例）：
        # - 最新凭据恰是本转移且真源未消费它（last_wiring_key 不等于该键）
        #   → 崩溃窗口 catch-up：补齐真源写入，重放返回；
        # - 最新凭据已消费但真源又回到 from（同转移反复：如二次打回）
        #   → 新轮次批准，落 #r{n+1} 新凭据留痕。
        base = "invest:%s:%s>%s" % (run_id, from_status, to_status)
        prior_same = [a for a in own
                      if a.from_status == from_status
                      and a.to_status == to_status]
        if (prior_same
                and own[-1].idempotency_key == prior_same[-1].idempotency_key):
            last = own[-1]
            if entry.get("last_wiring_key") != last.idempotency_key:
                _apply_status(root, run_id, to_status,
                              last.idempotency_key)
                return last, find_entry(root, run_id), True     # catch-up

        # 轮次化幂等键：打回后重批（如 reviewed→drafted→reviewed）是
        # 新的一轮批准，必须留新凭据（第二轮 decided_by/reason 可追溯），
        # 不得重放第一轮凭据——同轮重放仍由既有凭据幂等兜底。
        key = (base if not prior_same
               else "%s#r%d" % (base, len(prior_same) + 1))
        approval, replayed = store.record(Approval(
            target_type="workitem",
            target_id=tid,
            from_status=from_status,
            to_status=to_status,
            decision="approved",
            decided_by=by,
            reason=reason,
            decided_at=_now_iso(),
            idempotency_key=key,
        ))
        if not replayed:
            _apply_status(root, run_id, to_status, key)
            return approval, find_entry(root, run_id), False
        # 防御分支：轮次化后 key 理论全新；并发窗口同 key 抢先落库时
        # 真源若滞后补齐，否则原样返回
        if entry.get("status") != to_status:
            _apply_status(root, run_id, to_status, key)
            return approval, find_entry(root, run_id), True
        return approval, entry, True
    finally:
        if own_store:
            store.close()


def _apply_status(root: Path, run_id: str, to_status: str,
                  wiring_key: str = "") -> None:
    """唯一授权写路径：更新 index 中该 run 的 status/ts（原子写）。

    last_wiring_key 记录已消费的凭据键（catch-up 判据，devtask 判例回灌）。
    """
    entries = load_index(root)
    for e in entries:
        if e.get("run_id") == run_id:
            e["status"] = to_status
            e["ts"] = _now_iso()
            if wiring_key:
                e["last_wiring_key"] = wiring_key
    save_index(root, entries)


# ----------------------------------------------------------------------
# 审计：审批态真源 vs 执行底账 vs 审批账本 三方比对
# ----------------------------------------------------------------------
def audit_report(root: Path, store: ApprovalStore = None) -> dict:
    """投研审批态一致性审计。

    四类违规显式列出：
    1. 伪造登记：index 中 run 在 checkpoints.db 无源；
    2. 越门状态：status∈{reviewed, adopted} 但审批账本无凭据；
    3. 指纹漂移：登记后 D1 决策被重跑覆盖/篡改（sha256 比对）；
    4. 决策缺失：checkpoints.db 已无 d1_decision 快照（artifact missing）。
    """
    root = Path(root)
    own_store = store is None
    store = store or ApprovalStore(_AGENT_PLATFORM / "data" / "approvals"
                                   / "approvals.db")
    try:
        entries = load_index(root)
        approvals = store.list(target_type="workitem")
        violations = []
        missing_art = []
        for e in entries:
            run_id = e.get("run_id", "")
            status = e.get("status", "")
            live_hash = current_decision_hash(root, run_id)
            if not live_hash:
                violations.append({
                    "run_id": run_id, "status": status,
                    "finding": "执行底账无 %s 决策快照（run 被删/损坏？）"
                               % DECISION_NODE,
                })
                missing_art.append("agent_platform/data/checkpoints.db#%s:%s"
                                   % (DECISION_NODE, run_id))
                continue
            if e.get("decision_hash") != live_hash:
                violations.append({
                    "run_id": run_id, "status": status,
                    "finding": "决策指纹漂移：登记后 D1 决策被改动/重跑覆盖",
                })
            if status in AUDIT_GATED_STATUSES:
                hit = [a for a in approvals
                       if a.target_id == "invest:%s" % run_id
                       and a.to_status == status]
                if not hit:
                    violations.append({
                        "run_id": run_id, "status": status,
                        "finding": "状态=%s 但审批账本无对应凭据" % status,
                    })
        # 交叉校验：凭据轮次 vs 消费标记（invest=latest_only 标记域）
        traces = {}
        for e in entries:
            rid = e.get("run_id", "")
            traces["invest:%s" % rid] = {
                "statuses": [],
                "consumed_keys": {e.get("last_wiring_key")}
                if e.get("last_wiring_key") else set(),
            }
        cross_v = cross_check(
            "invest", [a for a in approvals
                       if a.target_id.startswith("invest:")], traces,
            {"invest:%s" % e.get("run_id", ""): e.get("status", "")
             for e in entries},
            latest_only=True)
        return {
            "schema_version": SCHEMA_VERSION,
            "domain": DOMAIN,
            "workitem_count": len(entries),
            "by_status": _by_status(entries),
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


def _by_status(entries: List[dict]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for e in entries:
        out[e.get("status", "")] = out.get(e.get("status", ""), 0) + 1
    return out


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M1 Invest 域接线")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", parents=[common])
    sub.add_parser("report", parents=[common])
    p_rg = sub.add_parser("register", parents=[common])
    p_rg.add_argument("run_id")
    p_ap = sub.add_parser("approve", parents=[common])
    p_ap.add_argument("run_id")
    p_ap.add_argument("to_status",
                      choices=["reviewed", "adopted", "drafted"])
    p_ap.add_argument("--by", required=True)
    p_ap.add_argument("--reason", default="")

    args = ap.parse_args(argv)
    root = Path(args.root)

    if args.cmd == "list":
        items, artifacts = project_workitems(root)
        for wi, art in zip(items, artifacts):
            print("%-24s %-9s %-22s hash=%s%s" % (
                wi.workitem_id, wi.status, wi.title[:22],
                art.fact_hash[:12] if art.fact_hash else "-",
                " [决策缺失]" if art.missing else ""))
        return 0
    if args.cmd == "report":
        print(json.dumps(audit_report(root), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "register":
        entry, replayed = register_run(root, args.run_id)
        print("[invest_wiring] %s %s：stock=%s drafted（决策指纹 %s）" % (
            args.run_id, "已登记（重放）" if replayed else "登记",
            entry.get("stock"), entry.get("decision_hash", "")[:12]))
        return 0
    if args.cmd == "approve":
        approval, entry, replayed = approve_transition(
            root, args.run_id, args.to_status, args.by, args.reason)
        print("[invest_wiring] %s %s：%s→%s%s" % (
            args.run_id, "重放" if replayed else "批准",
            approval.from_status, approval.to_status,
            "" if not replayed else "（凭据 %s 已存在，真源未重复变更）"
            % approval.approval_id[:12]))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
