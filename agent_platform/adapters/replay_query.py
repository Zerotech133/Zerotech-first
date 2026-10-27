# -*- coding: utf-8 -*-
"""M1 统一事件回放查询层（全只读）。

**与 event_replay.py 的分工**（两者互补，勿重复建设）：
- adapters/event_replay.py = 事件流内查询：canonical envelope 单流
  过滤/分页/排序/since-until（挂门户 /api/platform/replay）；
- 本模块 adapters/replay_query.py = **跨账本串线回放**：按
  trace_id / idempotency_key / target_id 把 events + idempotency
  账本 + approvals 审批凭据串成统一时间线，并附域内现状快照
  （挂门户 /api/unified/replay）。M1 验收"同一任务一个 owner/
  一个状态真源/一个幂等键"的回放闭环由此承担。

M1 验收目标：同一任务一个 owner、一个状态真源、一个幂等键——
本模块把散落各账本的轨迹按 trace_id / idempotency_key / target_id
串成统一时间线，实现"任何一次受控转移可回放"。

数据面（全只读，fail-visible）：
- events       event_adapter canonical 流（portal.db/events +
               llm_tool_audit.jsonl + push_flow.jsonl，只取摘要字段）
- idempotency  agent_platform/data/idempotency/idempotency.db
               （双轨起点尚未建库 → missing_source，不算失败）
- approvals    engine/approvals 审批注册表（target_id / trace_id）
- artifacts    engine/artifacts 产物登记仓（M3-P4b：登记/审批/
               supersede 链入时间线，target 命中时附 artifact_chain
               完整回放——artifact_id→hash→revision→负责人）
- 域现状       gzh / academy wiring 只读投影（target 匹配时附现状）

查询入口：
    replay(root, trace_id=..., idempotency_key=..., target_id=...)
    → {query, sources, timeline, current_state, verdict}

timeline 元素统一形如：
    {ts, source, kind, summary, ref, trace_id, idempotency_key}
按 ts 升序（跨源 naive/aware 混排时以字符串排序兜底，摘要层不
擅自补时区——M0 判例：naive 不补 UTC）。

CLI：
    python adapters/replay_query.py --trace <trace_id> [--root .]
    python adapters/replay_query.py --idem <idempotency_key> [--root .]
    python adapters/replay_query.py --target gzh:2026-10-17-22 [--root .]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_AGENT_PLATFORM = Path(__file__).resolve().parents[1]
REPO_ROOT = _AGENT_PLATFORM.parent
if str(_AGENT_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_AGENT_PLATFORM))

SCHEMA_VERSION = "1.1"
READ_ONLY = True

_IDEM_DB = _AGENT_PLATFORM / "data" / "idempotency" / "idempotency.db"
_APPROVALS_DB = _AGENT_PLATFORM / "data" / "approvals" / "approvals.db"
_APPROVALS_DB = _AGENT_PLATFORM / "data" / "approvals" / "approvals.db"
_ARTIFACTS_DB = _AGENT_PLATFORM / "data" / "artifacts" / "artifacts.db"
_APPROVALS_DB = _AGENT_PLATFORM / "data" / "approvals" / "approvals.db"


class _Modules:
    """懒加载共享模块（与 unified_projection 同款加载纪律）。"""

    _cache: Dict[str, Any] = {}

    @classmethod
    def get(cls, name: str, rel: str):
        if name not in cls._cache:
            import importlib.util

            spec = importlib.util.spec_from_file_location(name, _AGENT_PLATFORM / rel)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            cls._cache[name] = module
        return cls._cache[name]


# ----------------------------------------------------------------------
# 各账本只读取数
# ----------------------------------------------------------------------
def _load_events(root: Path) -> List[dict]:
    """event_adapter canonical 记录（只取摘要字段）。"""
    ea = _Modules.get("rq_event_adapter", "adapters/event_adapter.py")
    rep = ea.build_report(root)
    out = []
    for rec in rep.get("records", []) or []:
        if not isinstance(rec, dict):
            continue
        out.append(
            {
                "ts": rec.get("ts", ""),
                "source": "events:%s" % rec.get("source", ""),
                "kind": rec.get("kind", ""),
                "summary": "%s actor=%s object=%s"
                % (rec.get("kind", ""), rec.get("actor", ""), rec.get("object_id", "")),
                "ref": rec.get("event_id", ""),
                "trace_id": rec.get("trace_id", ""),
                "idempotency_key": rec.get("idempotency_key", ""),
            }
        )
    return out


def _load_idem_rows() -> List[dict]:
    """幂等账本记录；库未建（双轨起点）→ 空列表 + missing 标记由调用方处理。"""
    if not _IDEM_DB.exists():
        return []
    con = sqlite3.connect("file:%s?mode=ro" % _IDEM_DB.as_posix(), uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT record_id, workspace_id, scope, operation, idempotency_key,"
            " status, source_system, source_ref, trace_id, created_at, updated_at"
            " FROM idempotency_records ORDER BY created_at, rowid"
        ).fetchall()
        out = []
        for r in rows:
            keys = r.keys()
            out.append(
                {
                    "ts": r["created_at"],
                    "source": "idempotency",
                    "kind": "idem:%s" % r["status"],
                    "summary": "%s/%s key=%s src=%s:%s"
                    % (
                        r["scope"],
                        r["operation"],
                        r["idempotency_key"],
                        r["source_system"] if "source_system" in keys else "",
                        r["source_ref"],
                    ),
                    "ref": r["record_id"],
                    "trace_id": r["trace_id"] if "trace_id" in keys else "",
                    "idempotency_key": r["idempotency_key"],
                }
            )
        return out
    finally:
        con.close()


def _load_approvals() -> List[dict]:
    """审批凭据时间线（含 trace_id，旧库该列缺省空串）。

    只读纪律（2026-10-17 审查判例）：不得经 ApprovalStore 打开——其
    __init__ 含 mkdir/建表/列迁移写路径，库缺失时会静默建空
    approvals.db（approvals_registry.py 同判例，mode=ro 直连绕开）。
    """
    if not _APPROVALS_DB.exists():
        return []
    con = sqlite3.connect("file:%s?mode=ro" % _APPROVALS_DB.as_posix(), uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT approval_id, idempotency_key, target_id, from_status,"
            " to_status, decided_by, reason, decided_at, trace_id"
            " FROM approvals WHERE target_type='workitem'"
            " ORDER BY created_at, rowid"
        ).fetchall()
        return [
            {
                "ts": r["decided_at"],
                "source": "approvals",
                "kind": "approval:%s" % r["to_status"],
                "summary": "%s %s→%s by=%s%s"
                % (
                    r["target_id"],
                    r["from_status"],
                    r["to_status"],
                    r["decided_by"],
                    (" reason=%s" % r["reason"]) if r["reason"] else "",
                ),
                "ref": r["approval_id"],
                "trace_id": r["trace_id"] or "",
                "idempotency_key": r["idempotency_key"],
                "target_id": r["target_id"],
            }
            for r in rows
        ]
    finally:
        con.close()


def _artifact_target_candidates(art) -> List[str]:
    """产物 → 可命中的 target 候选（source_system 前缀 + source_ref /
    workitem_id；别名为空跳过）。"""
    cands: List[str] = []
    prefix = str(art.source_system or "").split(".")[0]
    if prefix and art.source_ref:
        cands.append("%s:%s" % (prefix, art.source_ref))
    if art.workitem_id:
        cands.append(art.workitem_id)
    return [c for c in dict.fromkeys(cands) if c]


def _ap_base(root: Path = None) -> Path:
    """agent_platform 基座探测：root=仓库根 → 其下 agent_platform；
    root=agent_platform 本身 → 原样（root=None 用模块缺省基准）。"""
    base = Path(root) if root else _AGENT_PLATFORM
    if (base / "agent_platform" / "data").is_dir():
        return base / "agent_platform"
    return base


def _artifacts_db(root: Path = None) -> Path:
    """T1：随 root 解析（root=None 用模块缺省基准）。"""
    return _ap_base(root) / "data" / "artifacts" / "artifacts.db"




def _load_artifacts(root: Path = None) -> List[dict]:
    """产物登记时间线（M3-P4b；mode=ro 直连，库未建 → 空列表）。

    只读纪律：不经 ArtifactStore——契约对象无 created_at（时间戳
    仅存库行），且 store 构造含建库写路径；mode=ro SQL 直取。
    """
    db = _artifacts_db(root)
    if not db.exists():
        return []
    con = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT artifact_id, kind, path, content_hash, fact_hash,"
            " revision, supersedes, workitem_id, source_system, source_ref,"
            " produced_by, trace_id, registered_at FROM artifacts"
            " ORDER BY registered_at, rowid"
        ).fetchall()
        out = []
        for r in rows:
            cands: List[str] = []
            prefix = str(r["source_system"] or "").split(".")[0]
            if prefix and r["source_ref"]:
                cands.append("%s:%s" % (prefix, r["source_ref"]))
            if r["workitem_id"]:
                cands.append(r["workitem_id"])
            cands = [c for c in dict.fromkeys(cands) if c]
            out.append(
                {
                    "ts": r["registered_at"],
                    "source": "artifacts",
                    "kind": "artifact:%s" % (r["kind"] or "unknown"),
                    "summary": "%s rev=%s hash=%s… by=%s%s"
                    % (
                        r["path"] or r["artifact_id"][:16],
                        r["revision"],
                        (r["content_hash"] or r["fact_hash"] or "")[:12],
                        r["produced_by"],
                        (" supersedes=%s" % r["supersedes"][:16])
                        if r["supersedes"]
                        else "",
                    ),
                    "ref": r["artifact_id"],
                    "trace_id": r["trace_id"] or "",
                    "idempotency_key": r["content_hash"] or r["fact_hash"],
                    "target_id": cands[0] if cands else "",
                    "target_aliases": cands,
                }
            )
        return out
    finally:
        con.close()


def _artifact_chain_of(artifact_ids: List[str], root: Path = None) -> List[dict]:
    """命中的 artifact 沿 supersede 链完整回放（read_only store）。"""
    db = _artifacts_db(root)
    if not db.exists() or not artifact_ids:
        return []
    from engine.artifacts import ArtifactStore

    store = ArtifactStore(db, read_only=True)
    seen: Dict[str, dict] = {}
    try:
        for aid in artifact_ids:
            for art in store.chain(aid):
                if art.artifact_id in seen:
                    continue
                seen[art.artifact_id] = {
                    "artifact_id": art.artifact_id,
                    "kind": art.kind,
                    "path": art.path,
                    "content_hash": (art.content_hash or art.fact_hash or "")[:16],
                    "revision": art.revision,
                    "produced_by": art.produced_by,
                    "supersedes": art.supersedes,
                    "source_system": art.source_system,
                    "source_ref": art.source_ref,
                }
        return seen.values() and list(seen.values())  # chain() 已旧→新有序
    finally:
        store.close()


def _domain_current_state(root: Path, target_id: str) -> Optional[dict]:
    """target 命中域前缀时，附域内现状快照（只读投影）。"""
    prefix, _, ref = target_id.partition(":")
    if prefix == "gzh":
        gw = _Modules.get("rq_gzh_wiring", "domains/gzh_wiring.py")
        items, artifacts = gw.project_workitems(root)
        for wi, art in zip(items, artifacts):
            if wi.workitem_id == target_id:
                return {
                    "domain": "gzh",
                    "status": wi.status,
                    "title": wi.title,
                    "artifact_hash": art.fact_hash,
                    "artifact_missing": art.missing,
                }
        return None
    if prefix == "academy":
        aw = _Modules.get("rq_academy_wiring", "domains/academy_wiring.py")
        for wi in aw.project_workitems(root):
            if wi.workitem_id == target_id:
                return {
                    "domain": "academy",
                    "status": wi.status,
                    "title": wi.title,
                    "stage_index": wi.payload.get("stage_index"),
                    "history_len": wi.payload.get("history_len"),
                }
        return None
    return None


# ----------------------------------------------------------------------
# 查询主入口
# ----------------------------------------------------------------------
def _sort_key(entry: dict):
    # 跨源 naive/aware 混排：字符串排序兜底，不补时区（M0 判例）
    return (str(entry.get("ts") or ""), str(entry.get("source") or ""))


def replay(
    root: Path = REPO_ROOT,
    trace_id: str = "",
    idempotency_key: str = "",
    target_id: str = "",
) -> dict:
    root = Path(root)
    if not (trace_id or idempotency_key or target_id):
        raise ValueError("trace_id / idempotency_key / target_id 至少给一个")

    findings: List[str] = []
    sources: Dict[str, str] = {}

    def _collect(loader, name):
        try:
            rows = loader()
            sources[name] = (
                "missing_source（双轨起点，账本未建库）"
                if name == "idempotency" and not rows and not _IDEM_DB.exists()
                else "ok"
                if rows
                else "empty"
            )
            return rows
        except Exception as exc:  # noqa: BLE001
            findings.append("[%s] 读取失败: %s: %s" % (name, type(exc).__name__, exc))
            sources[name] = "error"
            return []

    events = _collect(lambda: _load_events(root), "events")
    idem = _collect(_load_idem_rows, "idempotency")
    approvals = _collect(_load_approvals, "approvals")
    artifacts = _collect(lambda: _load_artifacts(root), "artifacts")

    all_rows = events + idem + approvals + artifacts

    def _hit(row: dict) -> bool:
        if trace_id and row.get("trace_id") == trace_id:
            return True
        if idempotency_key and row.get("idempotency_key") == idempotency_key:
            return True
        if target_id and (
            row.get("target_id") == target_id
            or target_id in (row.get("target_aliases") or [])
            or (
                row.get("source", "").startswith("events:")
                and target_id in str(row.get("summary", ""))
            )
        ):
            return True
        return False

    timeline = sorted([r for r in all_rows if _hit(r)], key=_sort_key)

    current_state = None
    if target_id:
        # M3-P4 判例：域现状投影异常降级留 finding，不拖垮整体回放
        try:
            current_state = _domain_current_state(root, target_id)
        except Exception as exc:  # noqa: BLE001
            findings.append("[domain_state] %s 投影失败: %s" % (target_id, exc))

    # M3-P4b：target 命中的产物沿 supersede 链完整回放
    artifact_chain: List[dict] = []
    if target_id:
        hit_aids = [r["ref"] for r in timeline if r.get("source") == "artifacts"]
        if hit_aids:
            try:
                artifact_chain = _artifact_chain_of(hit_aids, root)
            except Exception as exc:  # noqa: BLE001
                findings.append(
                    "[artifacts] 链回放失败: %s: %s" % (type(exc).__name__, exc)
                )

    # verdict：查询成功且有轨迹 → OK；各源可用但零命中 → EMPTY；
    # 有源 error → DEGRADED（仍返回已取到的部分）
    if sources.get("events") == "error" or sources.get("approvals") == "error":
        verdict = "DEGRADED"
    elif timeline:
        verdict = "OK"
    else:
        verdict = "EMPTY"

    return {
        "schema_version": SCHEMA_VERSION,
        "read_only": READ_ONLY,
        "query": {
            "trace_id": trace_id,
            "idempotency_key": idempotency_key,
            "target_id": target_id,
        },
        "sources": sources,
        "timeline": timeline,
        "current_state": current_state,
        "artifact_chain": artifact_chain,
        "verdict": verdict,
        "findings": findings,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M1 统一事件回放查询")
    ap.add_argument("--root", default=str(REPO_ROOT))
    ap.add_argument("--trace", default="")
    ap.add_argument("--idem", dest="idempotency_key", default="")
    ap.add_argument("--target", default="")
    args = ap.parse_args(argv)
    report = replay(
        Path(args.root),
        trace_id=args.trace,
        idempotency_key=args.idempotency_key,
        target_id=args.target,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
