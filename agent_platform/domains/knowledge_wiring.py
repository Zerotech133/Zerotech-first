"""M2-P2 统一记忆入口：候选知识评审流。

总纲红线代码化：「候选知识不能直接进入生产记忆」。

链路：
    knowledge_pending.jsonl（候选池，tools/knowledge_distill.py 产出）
      → promote()：Approval 门（凭据先落）→ MemoryStore share/knowledge
      → reject()：记录否决凭据，不写记忆
      → audit_report()：绕门审计 + 候选池现状

设计裁决：
- 提升必须 Approval（生产记忆写入=跨域承诺，M1 同款"凭据先落再写"）；
- 幂等键 knowledge:<hash>:promote（hash=候选内容指纹，同候选重复提升
  零副作用）；崩溃窗口 catch-up：凭据在而记忆缺 → 补写；
- MemoryStore 以 enforce=False 写入——授权由本模块的 Approval 门承担
  （门即 RBAC，避免双重授权语义混乱；直接绕过本模块写 share/knowledge
  会被审计判 bypass）；
- 候选池 append-only：提升/否决都不删候选行（池是原始观测）；
- 拒绝也是凭据（decision=rejected），审计可回放否决历史。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_DOMAINS_DIR = Path(__file__).resolve().parent
_AGENT_PLATFORM = _DOMAINS_DIR.parent
if str(_AGENT_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_AGENT_PLATFORM))

from contracts.objects import Approval        # noqa: E402
from engine.approvals import ApprovalStore    # noqa: E402
from engine.memory import MemoryStore         # noqa: E402

PENDING_FILE = _AGENT_PLATFORM / "data" / "knowledge_pending.jsonl"
MEMORY_BASE = _AGENT_PLATFORM / "data" / "memory"
APPROVALS_DB = _AGENT_PLATFORM / "data" / "approvals" / "approvals.db"

GNS_KNOWLEDGE = "share/knowledge"   # 生产知识 gns（跨域可读段）

_KNOWN_KINDS = {"lesson", "decision", "academy_research"}


class KnowledgeWiringError(Exception):
    """受控操作非法。"""


def candidate_hash(entry: Dict) -> str:
    """候选内容指纹：canonical(kind, dept, content) sha256 前 16 位。"""
    canonical = json.dumps(
        {"kind": entry.get("kind", ""), "dept": entry.get("dept", ""),
         "content": entry.get("content", "")},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


# ----------------------------------------------------------------------
def load_pending(root: Path = None) -> List[Dict]:
    """候选池只读加载（行号随行返回，供定位）。"""
    p = (Path(root) if root else _AGENT_PLATFORM) / "data" / \
        "knowledge_pending.jsonl"
    if not p.exists():
        return []
    out = []
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            out.append({"_line": i + 1, "_error": "json_invalid",
                        "_raw": line[:80]})
            continue
        entry["_line"] = i + 1
        entry["_hash"] = candidate_hash(entry)
        out.append(entry)
    return out


def _memory_has(store: MemoryStore, chash: str) -> bool:
    for rec in store.read("wiring", GNS_KNOWLEDGE, limit=10000):
        if rec.get("payload", {}).get("candidate_hash") == chash:
            return True
    return False


def _write_memory(root: Path, entry: Dict, approval: Approval) -> Dict:
    ms = MemoryStore((Path(root) if root else _AGENT_PLATFORM)
                     / "data" / "memory", enforce=False)
    payload = {"kind": entry.get("kind", ""),
               "dept": entry.get("dept", ""),
               "content": entry.get("content", ""),
               "src": entry.get("src", ""),
               "ts": entry.get("ts", ""),
               "candidate_hash": entry["_hash"],
               "approval_id": approval.approval_id,
               "promoted_by": approval.decided_by}
    rec = ms.write("wiring", GNS_KNOWLEDGE, payload)
    return rec


def promote(root: Path, chash: str, by: str, reason: str = "",
            store: ApprovalStore = None
            ) -> Tuple[Approval, Dict, bool]:
    """批准提升一条候选知识 → 生产记忆。

    返回 (approval, memory_record, replayed)。
    - 候选池无此 hash → KnowledgeWiringError（无源不伪造）；
    - 幂等重放：凭据在 + 记忆在 → 零副作用（replayed=True）；
    - 崩溃窗口 catch-up：凭据在 + 记忆缺 → 补写记忆；
    - kind 白名单校验（防池外杂音直入生产）。
    """
    if not by:
        raise KnowledgeWiringError("批准人 by 必填")
    root = Path(root) if root else _AGENT_PLATFORM
    entry = next((e for e in load_pending(root) if e.get("_hash") == chash),
                 None)
    if entry is None or "_error" in entry:
        raise KnowledgeWiringError("候选池无此知识：%s" % chash)
    if entry.get("kind") not in _KNOWN_KINDS:
        raise KnowledgeWiringError(
            "kind=%s 不在白名单 %s" % (entry.get("kind"),
                                      sorted(_KNOWN_KINDS)))
    if not entry.get("content"):
        raise KnowledgeWiringError("候选内容为空，拒绝提升")

    own = store is None
    store = store or ApprovalStore(APPROVALS_DB)
    try:
        key = "knowledge:%s:promote" % chash
        approval, replayed = store.record(Approval(
            target_type="memory",
            target_id="knowledge:%s" % chash,
            from_status="pending", to_status="promoted",
            decision="approved", decided_by=by, reason=reason,
            idempotency_key=key))
        ms = MemoryStore(root / "data" / "memory", enforce=False)
        if _memory_has(ms, chash):
            return approval, {}, True          # 全量重放（凭据+记忆都在）
        rec = _write_memory(root, entry, approval)   # 首次 / catch-up
        return approval, rec, False
    finally:
        if own:
            store.close()


def reject(root: Path, chash: str, by: str, reason: str = "",
           store: ApprovalStore = None) -> Tuple[Approval, bool]:
    """否决候选：记凭据不写记忆（append-only 否决历史）。"""
    if not by:
        raise KnowledgeWiringError("否决人 by 必填")
    root = Path(root) if root else _AGENT_PLATFORM
    entry = next((e for e in load_pending(root) if e.get("_hash") == chash),
                 None)
    if entry is None:
        raise KnowledgeWiringError("候选池无此知识：%s" % chash)
    own = store is None
    store = store or ApprovalStore(APPROVALS_DB)
    try:
        approval, replayed = store.record(Approval(
            target_type="memory",
            target_id="knowledge:%s" % chash,
            from_status="pending", to_status="rejected",
            decision="rejected", decided_by=by, reason=reason,
            idempotency_key="knowledge:%s:reject" % chash))
        return approval, replayed
    finally:
        if own:
            store.close()


# ----------------------------------------------------------------------
def audit_report(root: Path = None) -> Dict:
    """候选池现状 + 生产记忆绕门审计（全只读）。"""
    root = Path(root) if root else _AGENT_PLATFORM
    pending = load_pending(root)
    store = ApprovalStore(APPROVALS_DB)
    try:
        creds = {c.idempotency_key: c for c in store.list()
                 if c.target_id.startswith("knowledge:")}
    finally:
        store.close()

    ms = MemoryStore(root / "data" / "memory", enforce=False)
    memory_rows = ms.read("wiring", GNS_KNOWLEDGE, limit=10000)

    promoted_keys = {k for k in creds if k.endswith(":promote")}
    findings = []

    # ① 候选池逐条现状
    states = []
    for e in pending:
        if "_error" in e:
            states.append({"line": e["_line"], "state": "invalid_json"})
            findings.append("pending 行 %d JSON 损坏" % e["_line"])
            continue
        h = e["_hash"]
        if "knowledge:%s:promote" % h in promoted_keys:
            ok = _memory_has(ms, h)
            states.append({"line": e["_line"], "hash": h,
                           "state": "promoted" if ok else
                           "pending_execution"})
            if not ok:
                findings.append("凭据在而记忆缺（崩溃窗口）：%s" % h)
        elif "knowledge:%s:reject" % h in creds:
            states.append({"line": e["_line"], "hash": h,
                           "state": "rejected"})
        else:
            states.append({"line": e["_line"], "hash": h,
                           "state": "pending_review"})

    # ② 红线：生产记忆中出现无凭据的知识 = 绕门
    n_bypass = 0
    for rec in memory_rows:
        ch = rec.get("payload", {}).get("candidate_hash", "")
        if "knowledge:%s:promote" % ch not in promoted_keys:
            n_bypass += 1
            findings.append("bypass_promote: share/knowledge 无凭据条目"
                            " hash=%s" % (ch or "<missing>"))
    if n_bypass:
        verdict = "FAIL"
    elif any("崩溃窗口" in f for f in findings):
        verdict = "WARN"
    else:
        verdict = "PASS"

    return {
        "verdict": verdict,
        "pending_total": len(pending),
        "states": states,
        "memory_total": len(memory_rows),
        "bypass_count": n_bypass,
        "credentials": len(creds),
        "findings": findings,
        "read_only": True,
    }


# ----------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M2-P2 统一记忆入口")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_p = sub.add_parser("promote")
    p_p.add_argument("hash")
    p_p.add_argument("--by", required=True)
    p_p.add_argument("--reason", default="")
    p_r = sub.add_parser("reject")
    p_r.add_argument("hash")
    p_r.add_argument("--by", required=True)
    p_r.add_argument("--reason", default="")
    sub.add_parser("pending")
    sub.add_parser("report")
    args = ap.parse_args(argv)

    if args.cmd == "pending":
        for e in load_pending():
            print(json.dumps(e, ensure_ascii=False))
    elif args.cmd == "report":
        print(json.dumps(audit_report(), ensure_ascii=False, indent=1))
    elif args.cmd == "promote":
        ap_, rec, replayed = promote(_AGENT_PLATFORM, args.hash, args.by,
                                     args.reason)
        print(json.dumps({"approval_id": ap_.approval_id,
                          "replayed": replayed, "memory": rec},
                         ensure_ascii=False))
    else:
        ap_, replayed = reject(_AGENT_PLATFORM, args.hash, args.by,
                               args.reason)
        print(json.dumps({"approval_id": ap_.approval_id,
                          "replayed": replayed}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
