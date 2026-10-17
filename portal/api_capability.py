# -*- coding: utf-8 -*-
"""M3-P5 能力看板只读 mixin（挂 portal_api.Handler 继承链）。

GET /api/capability/board
    → 聚合统一产物登记仓（artifacts.db）+ 审批注册表（approvals.db）
    的只读投影，回答总纲验收句五问的「看板版」：
    来源（source_system/source_ref）、审查（凭据 content_hash 绑定率）、
    版本（revision/supersede 链组）、负责人（produced_by）、覆盖
    （各产线登记/凭据计数）。

只读纪律（沿 replay_query._load_artifacts 判例）：
- mode=ro SQL 直连，不经 Store 构造（建库写副作用）——库未建时
  返回 ledger_exists=false 的空投影而非报错（看板是观测面）；
- 无写路径、无缓存（低频直读）；fail loud 500 由 _json 上层兜底。

凭据 content_hash 绑定率（M3-P3 口径）：
- bound    = content_hash != ''（新口径凭据，批 A 发 B 可检测）；
- legacy   = content_hash == ''（旧凭据跳过校验，零迁移不回填）；
- rate     = bound / total，legacy 不计入分母扣减——率只描述
  新口径渗透度，看板注释注明 legacy 属固有历史而非缺陷。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

# 产线覆盖清单（source_system / target_type 前缀 → 展示名）。
# 新域上线在此追加一行即可，缺位计 0——看板宁可显示 0 也不静默漏行。
PRODUCTION_LINES = [
    ("gzh", "自媒体产线"),
    ("pitch", "路演 PPT 产线"),
    ("dev", "软件产线"),
    ("skill", "Skill 产线"),
    ("intel", "情报裁决产线"),
]


def _ap_db(rel: str) -> Path:
    return _REPO_ROOT / "agent_platform" / "data" / rel


def _ro_rows(db: Path, sql: str, args=()):
    """mode=ro 直连取行（库未建 → None，上层给 ledger_exists=false）。"""
    if not db.exists():
        return None
    con = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True,
                          timeout=30)
    con.row_factory = sqlite3.Row
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def _artifact_projection(limit_recent: int = 30) -> dict:
    rows = _ro_rows(
        _ap_db("artifacts/artifacts.db"),
        "SELECT artifact_id, kind, path, content_hash, fact_hash,"
        " revision, supersedes, workitem_id, source_system, source_ref,"
        " produced_by, trace_id, registered_at"
        " FROM artifacts ORDER BY registered_at, rowid")
    if rows is None:
        return {"ledger_exists": False, "total": 0, "by_source": {},
                "by_kind": {}, "superseded_count": 0,
                "recent": [], "supersede_chains": []}
    arts = [dict(r) for r in rows]
    by_source, by_kind = {}, {}
    for a in arts:
        src = a["source_system"] or "(unspecified)"
        by_source[src] = by_source.get(src, 0) + 1
        by_kind[a["kind"]] = by_kind.get(a["kind"], 0) + 1

    # supersede 链组：沿 supersedes 指针从链尾（无人指向它）回溯。
    # 只输出 length>1 的链（单条目无版本演进，不值得占看板）。
    superseded_by = {}  # old_id -> [new_id, ...]
    for a in arts:
        if a["supersedes"]:
            superseded_by.setdefault(a["supersedes"], []).append(
                a["artifact_id"])
    heads = [a["artifact_id"] for a in arts
             if a["artifact_id"] not in superseded_by]
    chains = []
    for head in heads:
        chain, seen, cur = [], set(), head
        while cur and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            prev = next((a["supersedes"] for a in arts
                         if a["artifact_id"] == cur), "")
            cur = prev or None
        if len(chain) > 1:
            chains.append(list(reversed(chain)))  # 旧→新（与 store.chain 同口径）
    chains.sort(key=len, reverse=True)

    recent = list(reversed(arts[-limit_recent:]))
    return {
        "ledger_exists": True,
        "total": len(arts),
        "by_source": dict(sorted(by_source.items())),
        "by_kind": dict(sorted(by_kind.items())),
        "superseded_count": sum(1 for a in arts if a["supersedes"]),
        "recent": [
            {"artifact_id": a["artifact_id"], "kind": a["kind"],
             "path": a["path"],
             "content_hash": (a["content_hash"] or a["fact_hash"] or ""),
             "revision": a["revision"], "supersedes": a["supersedes"],
             "workitem_id": a["workitem_id"],
             "source_system": a["source_system"],
             "source_ref": a["source_ref"],
             "produced_by": a["produced_by"],
             "trace_id": a["trace_id"],
             "registered_at": a["registered_at"]}
            for a in recent],
        "supersede_chains": chains,
    }


def _approval_projection() -> dict:
    rows = _ro_rows(
        _ap_db("approvals/approvals.db"),
        "SELECT approval_id, target_type, target_id, from_status,"
        " to_status, decision, decided_by, trace_id, content_hash,"
        " decided_at FROM approvals ORDER BY decided_at, rowid")
    if rows is None:
        return {"ledger_exists": False, "total": 0,
                "content_hash_bound": 0, "content_hash_binding_rate": 0.0,
                "legacy_unbound": 0, "by_target_type": {},
                "by_decision": {}, "recent": []}
    aps = [dict(r) for r in rows]
    by_type, by_decision, by_prefix = {}, {}, {}
    bound = 0
    for r in aps:
        by_type[r["target_type"]] = by_type.get(r["target_type"], 0) + 1
        by_decision[r["decision"]] = by_decision.get(r["decision"], 0) + 1
        # 产线归属：target_type 精确匹配 + target_id 'domain:ref' 前缀
        # 兜底（30 条 workitem 型凭据的产线归属全靠后者）
        prefix = str(r["target_id"]).partition(":")[0]
        by_prefix[prefix] = by_prefix.get(prefix, 0) + 1
        if r["content_hash"]:
            bound += 1
    total = len(aps)
    return {
        "ledger_exists": True,
        "total": total,
        "content_hash_bound": bound,
        "content_hash_binding_rate": round(bound / total, 4) if total else 0.0,
                "legacy_unbound": total - bound,
                "by_target_type": dict(sorted(by_type.items())),
                "by_target_prefix": dict(sorted(by_prefix.items())),
        "by_decision": dict(sorted(by_decision.items())),
        "recent": [
            {"approval_id": r["approval_id"],
             "target_type": r["target_type"], "target_id": r["target_id"],
             "from_status": r["from_status"], "to_status": r["to_status"],
             "decision": r["decision"], "decided_by": r["decided_by"],
             "content_hash": r["content_hash"],
             "trace_id": r["trace_id"], "decided_at": r["decided_at"]}
            for r in reversed(aps[-30:])],
    }


def build_capability_board() -> dict:
    """看板聚合主入口（供 mixin 与测试共用）。"""
    artifacts = _artifact_projection()
    approvals = _approval_projection()

    # 产线覆盖：登记仓按 source_system 首段前缀计数；凭据按
    # target_type 精确匹配 ∪ target_id 'domain:ref' 前缀兜底
    # （workitem 型凭据的产线归属全靠后者）。
    src_counts = {}
    for k, v in artifacts.get("by_source", {}).items():
        src_counts[k.split(".")[0]] = src_counts.get(k.split(".")[0], 0) + v
    type_counts = approvals.get("by_target_type", {})
    prefix_counts = approvals.get("by_target_prefix", {})
    lines = []
    for prefix, label in PRODUCTION_LINES:
        ap_n = max(type_counts.get(prefix, 0), prefix_counts.get(prefix, 0))
        lines.append({
            "prefix": prefix, "label": label,
            "artifact_count": src_counts.get(prefix, 0),
            "approval_count": ap_n,
            "registered": bool(src_counts.get(prefix) or ap_n),
        })
    return {
        "read_only": True,
        "generated_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "artifacts": artifacts,
        "approvals": approvals,
        "production_lines": lines,
        "note": {
            "content_hash": "凭据绑定率 = content_hash 非空占比；"
                            "legacy_unbound 为旧口径存量（跳过校验，"
                            "不回填不迁移），属固有历史而非缺陷",
            "supersede_chains": "仅展示长度>1 的版本链（有演进才成链）",
        },
    }


class Handler:
    """能力看板 mixin（模块名绑定：portal_api 里 import api_capability）。"""

    def _capability_board(self):
        self._json(build_capability_board())
