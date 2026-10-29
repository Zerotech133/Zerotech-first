# -*- coding: utf-8 -*-
"""M2-P3 情报裁决域：血缘断链 → 情报决策清单（Agent 建议权 + 人工裁决权）。

背景（2026-10-17 架构裁决）：
- 数据血缘已入情报板块（/lineage + /api/kg/lineage），Hermes 挂
  lineage_breaks/lineage_trace 两工具；断链信号到情报决策之间缺一个
  「语义翻译层」——本域承接：Agent 溯源后起草裁决清单，老田批准生效。

链路：
    data/intel_verdicts.jsonl（裁决池，Agent 起草 = 原始观测）
      → adopt()：Approval 门（凭据先落）→ MemoryStore share/intel
      → reject()：记录否决凭据，不写生产记录
      → audit_report()：绕门审计 + 凭据轮次 vs 痕迹交叉校验

设计裁决（判例手册对齐）：
- 起草不需凭据（Agent 只有建议权没有执行权）；adopt/reject 必须
  Approval（对外情报动作 = 跨域承诺，M1「凭据先落再写」同款）；
- 幂等键 intel:<verdict_id>:adopt（同裁决重复生效零副作用）；
  崩溃窗口 catch-up：凭据在而生产记录缺 → 补写；
- 裁决修订 = 新条目（verdict_id 后缀 #r{n+1}，轮次化两派中取
  「凭据化新轮次」语义——旧条目 append-only 不改写，与判例手册
  「打回是否凭据化」一票决定：情报裁决修订必须留痕可回放）；
- 裁决池 append-only：adopt/reject 都不删池行（池是原始观测）；
- trace 域语义：share/intel 生产记录即痕迹，凭据轮次 vs 记录数
  交叉校验复用 adapters/credential_trace.cross_check。

空转状态（本骨架上线方式）：池为空 → report PASS（verdicts_total=0），
断链首次真实发生后 Agent 起草首条裁决即通电。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_DOMAINS_DIR = Path(__file__).resolve().parent
_AGENT_PLATFORM = _DOMAINS_DIR.parent
if str(_AGENT_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_AGENT_PLATFORM))

from contracts.objects import Approval  # noqa: E402
from engine.approvals import ApprovalStore  # noqa: E402
from engine.memory import MemoryStore  # noqa: E402
from adapters.credential_trace import cross_check, verdict_for  # noqa: E402

POOL_FILE = _AGENT_PLATFORM / "data" / "intel_verdicts.jsonl"
MEMORY_BASE = _AGENT_PLATFORM / "data" / "memory"
APPROVALS_DB = _AGENT_PLATFORM / "data" / "approvals" / "approvals.db"

GNS_INTEL = "share/intel"  # 生产情报裁决 gns（跨域可读段）

_KNOWN_DECISIONS = {"degrade", "withdraw", "watch"}
_KNOWN_BREAKS = {"stale", "broken", "lost", "missing"}

_REQUIRED_FIELDS = (
    "verdict_id",
    "table_urn",
    "job_run_id",
    "break_kind",
    "decision",
    "rationale",
)


class IntelWiringError(Exception):
    """受控操作非法。"""


def verdict_id_for(table_urn: str, job_run_id: str, rnd: int = 1) -> str:
    """裁决 ID 规约：<urn_safe>:<job_run_id>#r<n>（urn 中 /: 归一为 _）。"""
    urn_safe = "".join(c if c.isalnum() else "_" for c in str(table_urn))
    return "%s:%s#r%d" % (urn_safe, job_run_id, int(rnd))


# ----------------------------------------------------------------------
_POOL_PATH_NAME = ("data", "intel_verdicts.jsonl")


def pool_path(root: Path = None) -> Path:
    return (Path(root) if root else _AGENT_PLATFORM).joinpath(*_POOL_PATH_NAME)


def hash_entry(entry: Dict) -> str:
    """裁决条目内容指纹（M3-P3 同构）：canonical JSON → sha256 前 16 位。

    - 排除 content_hash 自身（防自引用）与 `_` 前缀测试辅助字段
      （load_pool 注入的 _line/_error 不参与指纹）；
    - 语义：draft 落池盖章 → adopt/reject 凭据绑定 → audit 复核现池行。
      池行 adopt 后被改写 = 凭据指纹对不上 = 裁决门旁路，audit 亮红。
    """
    body = {
        k: v
        for k, v in entry.items()
        if k != "content_hash" and not str(k).startswith("_")
    }
    payload = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def load_pool(root: Path = None) -> List[Dict]:
    """裁决池只读加载（行号随行返回，供定位）。"""
    p = pool_path(root)
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
            out.append({"_line": i + 1, "_error": "json_invalid", "_raw": line[:80]})
            continue
        entry["_line"] = i + 1
        out.append(entry)
    return out


def draft(root: Path, entry: Dict, by: str) -> Dict:
    """Agent 起草一条裁决（建议权，无凭据，append-only）。

    校验：必填字段 / decision 白名单 / break_kind 白名单 /
    verdict_id 池内唯一（修订走 #r{n+1} 新条目）。
    """
    if not by:
        raise IntelWiringError("起草人 by 必填")
    for f in _REQUIRED_FIELDS:
        if not entry.get(f):
            raise IntelWiringError("必填字段缺失：%s" % f)
    if entry["decision"] not in _KNOWN_DECISIONS:
        raise IntelWiringError(
            "decision=%s 不在白名单 %s" % (entry["decision"], sorted(_KNOWN_DECISIONS))
        )
    if entry["break_kind"] not in _KNOWN_BREAKS:
        raise IntelWiringError(
            "break_kind=%s 不在白名单 %s" % (entry["break_kind"], sorted(_KNOWN_BREAKS))
        )
    existing = {e.get("verdict_id") for e in load_pool(root) if "_error" not in e}
    if entry["verdict_id"] in existing:
        raise IntelWiringError(
            "verdict_id 已存在（修订走 #r{n+1} 新条目）：%s" % entry["verdict_id"]
        )
    rec = dict(entry)
    rec["drafted_by"] = by
    rec.setdefault("impact", [])
    rec["content_hash"] = hash_entry(rec)  # 落池盖章（P1-B 裁决门指纹）
    p = (Path(root) if root else _AGENT_PLATFORM) / "data" / "intel_verdicts.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    return rec


def _intel_has(store: MemoryStore, vid: str) -> bool:
    for rec in store.read("wiring", GNS_INTEL, limit=10000):
        if rec.get("payload", {}).get("verdict_id") == vid:
            return True
    return False


def _write_memory(root: Path, entry: Dict, approval: Approval) -> Dict:
    ms = MemoryStore(
        (Path(root) if root else _AGENT_PLATFORM) / "data" / "memory", enforce=False
    )
    payload = {
        k: entry.get(k)
        for k in (
            "verdict_id",
            "table_urn",
            "job_run_id",
            "break_kind",
            "decision",
            "impact",
            "rationale",
            "ts",
        )
    }
    payload["approval_id"] = approval.approval_id
    payload["adopted_by"] = approval.decided_by
    return ms.write("wiring", GNS_INTEL, payload)


def _pool_entry(pool: List[Dict], vid: str) -> Optional[Dict]:
    return next(
        (e for e in pool if "_error" not in e and e.get("verdict_id") == vid), None
    )


# ----------------------------------------------------------------------
# 断链自动起草（Agent 建议权的自动化入口；adopt 仍必须人工凭据）
# ----------------------------------------------------------------------
def _fetch_json(url: str, timeout: float = 15.0) -> Dict:
    """直连 localhost 取 JSON（绕代理，与门户探测同款）。"""
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _job_run_key(row: Dict) -> str:
    """断链作业指纹：sha1(table|job|step|started_at) 前 8 位。

    每次断链作业一个新裁决（修订轮次语义自然成立：新作业=新 jkey=新
    verdict_id；同作业重跑 auto_draft 幂等跳过，不产生 #r2 噪音）。
    """
    basis = "|".join(
        str(row.get(k, "")) for k in ("output_table", "job", "step", "started_at")
    )
    return hashlib.sha1(basis.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]


def _draft_for_break(root: Path, row: Dict, downstream: List[str], by: str) -> Dict:
    """由单条断链行构造并落池一条裁决（纯函数，可测）。"""
    urn = row.get("output_table", "")
    jkey = _job_run_key(row)
    status = str(row.get("status", ""))
    kind = (
        "lost"
        if status == "lost"
        else "broken"
        if status in ("fail", "error")
        else "stale"
    )
    decision = "watch" if kind == "stale" else "degrade"
    vid = verdict_id_for(urn, jkey)
    return draft(
        root,
        {
            "verdict_id": vid,
            "table_urn": urn,
            "job_run_id": jkey,
            "break_kind": kind,
            "decision": decision,
            "impact": list(downstream)[:5],
            "rationale": "自动起草：最近一次作业 %s/%s 状态 %s（%s），"
            "下游在册产物 %d 项"
            % (
                row.get("job", "?"),
                row.get("step", "?"),
                status or "?",
                row.get("started_at", "?"),
                len(downstream),
            ),
            "job": row.get("job", ""),
            "step": row.get("step", ""),
            "started_at": row.get("started_at", ""),
        },
        by=by,
    )


def auto_draft_from_lineage(
    root: Path = None,
    api_base: str = "http://127.0.0.1:8091",
    by: str = "agent-hermes",
    timeout: float = 15.0,
) -> Dict:
    """断链 → 裁决池自动起草（编排任务/Hermes 可直调）。

    - 数据源：8091 /api/kg/lineage?view=breaks（fail-visible：网络/接口
      异常返回 {"ok": False, "error": ...}，不抛不吞）；
    - 断链行（status!=ok）+ 新鲜度 stale 行（P1-A：status=ok 但数据太老
      = 沉默断链，API 附 freshness 字段时纳入，kind=stale→watch）；
    - 影响面：逐断链表查 view=upstream 取 DISTINCT output_table（以该表
      为输入的产出边 = 真实下游受损面）；
    - 幂等：同 (table, job_run_key) 已在池 → 跳过（重跑零副作用）。
    """
    root = Path(root) if root else _AGENT_PLATFORM
    try:
        rep = _fetch_json("%s/api/kg/lineage?view=breaks" % api_base, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "drafted": [],
            "skipped": 0,
            "error": "lineage API 不可达: %s" % exc,
        }
    if not isinstance(rep, dict) or "data" not in rep:
        return {
            "ok": False,
            "drafted": [],
            "skipped": 0,
            "error": "lineage API 返回异常: %s"
            % json.dumps(rep, ensure_ascii=False)[:200],
        }
    body = rep.get("data", {})
    broken = list(body.get("broken", []))
    # P1-A：新鲜度 stale 的 ok 行（沉默断链）也进裁决池（fail-open：
    # API 未附 freshness 字段时行为与旧版完全一致）
    stale_ok = [
        r
        for r in body.get("all_latest", [])
        if r.get("freshness") == "stale" and r.get("status") == "ok"
    ]
    broken_total = len(broken)
    stale_total = len(stale_ok)
    existing = {e.get("verdict_id") for e in load_pool(root) if "_error" not in e}
    drafted, skipped, errors = [], 0, []
    for row in broken + stale_ok:
        urn = row.get("output_table", "")
        vid = verdict_id_for(urn, _job_run_key(row))
        if vid in existing:
            skipped += 1
            continue
        try:
            down = _fetch_json(
                "%s/api/kg/lineage?view=upstream&table=%s"
                % (api_base, urllib.parse.quote(urn)),
                timeout=timeout,
            )
            impact = sorted(
                {
                    r.get("output_table", "")
                    for r in down.get("data", [])
                    if r.get("output_table")
                }
            )
            rec = _draft_for_break(root, row, impact, by=by)
            drafted.append(rec)
            existing.add(rec["verdict_id"])
        except Exception as exc:  # noqa: BLE001
            errors.append("%s: %s" % (urn or "?", exc))
    out = {
        "ok": not errors,
        "drafted": drafted,
        "skipped": skipped,
        "broken_total": broken_total,
        "stale_total": stale_total,
    }
    if errors:
        out["errors"] = errors
    return out


def adopt(
    root: Path, vid: str, by: str, reason: str = "", store: ApprovalStore = None
) -> Tuple[Approval, Dict, bool]:
    """批准生效一条裁决 → 生产记录 share/intel。

    返回 (approval, memory_record, replayed)。
    - 池无此 verdict_id → IntelWiringError（无源不伪造）；
    - 幂等重放：凭据在 + 记录在 → 零副作用（replayed=True）；
    - 崩溃窗口 catch-up：凭据在 + 记录缺 → 补写记录。
    """
    if not by:
        raise IntelWiringError("批准人 by 必填")
    root = Path(root) if root else _AGENT_PLATFORM
    entry = _pool_entry(load_pool(root), vid)
    if entry is None:
        raise IntelWiringError("裁决池无此 verdict_id：%s" % vid)

    own = store is None
    store = store or ApprovalStore(APPROVALS_DB)
    try:
        # P1-B：凭据绑定内容指纹（旧条目无 hash 则补算现值，等价锚定）
        entry_hash = entry.get("content_hash") or hash_entry(entry)
        approval, replayed = store.record(
            Approval(
                target_type="workitem",
                target_id="intel:%s" % vid,
                from_status="pending",
                to_status="adopted",
                decision="approved",
                decided_by=by,
                reason=reason,
                content_hash=entry_hash,
                idempotency_key="intel:%s:adopt" % vid,
            )
        )
        ms = MemoryStore(root / "data" / "memory", enforce=False)
        if _intel_has(ms, vid):
            return approval, {}, True  # 全量重放
        rec = _write_memory(root, entry, approval)  # 首次 / catch-up
        return approval, rec, False
    finally:
        if own:
            store.close()


def reject(
    root: Path, vid: str, by: str, reason: str = "", store: ApprovalStore = None
) -> Tuple[Approval, bool]:
    """否决裁决：记凭据不写生产记录（append-only 否决历史）。"""
    if not by:
        raise IntelWiringError("否决人 by 必填")
    root = Path(root) if root else _AGENT_PLATFORM
    entry = _pool_entry(load_pool(root), vid)
    if entry is None:
        raise IntelWiringError("裁决池无此 verdict_id：%s" % vid)
    own = store is None
    store = store or ApprovalStore(APPROVALS_DB)
    try:
        entry_hash = entry.get("content_hash") or hash_entry(entry)
        approval, replayed = store.record(
            Approval(
                target_type="workitem",
                target_id="intel:%s" % vid,
                from_status="pending",
                to_status="rejected",
                decision="rejected",
                decided_by=by,
                reason=reason,
                content_hash=entry_hash,
                idempotency_key="intel:%s:reject" % vid,
            )
        )
        return approval, replayed
    finally:
        if own:
            store.close()


# ----------------------------------------------------------------------
def audit_report(root: Path = None) -> Dict:
    """裁决池现状 + 生产记录绕门审计 + 凭据轮次交叉校验（全只读）。"""
    root = Path(root) if root else _AGENT_PLATFORM
    pool = load_pool(root)
    store = ApprovalStore(root / "data" / "approvals" / "approvals.db")
    try:
        creds = [c for c in store.list() if c.target_id.startswith("intel:")]
    finally:
        store.close()
    cred_keys = {c.idempotency_key for c in creds}

    ms = MemoryStore(root / "data" / "memory", enforce=False)
    memory_rows = ms.read("wiring", GNS_INTEL, limit=10000)

    findings: List[str] = []

    # ① 裁决池逐条现状
    states = []
    for e in pool:
        if "_error" in e:
            states.append({"line": e["_line"], "state": "invalid_json"})
            findings.append("pool 行 %d JSON 损坏" % e["_line"])
            continue
        vid = e.get("verdict_id", "")
        cred_adopt = next(
            (c for c in creds if c.idempotency_key == "intel:%s:adopt" % vid), None
        )
        cred_reject = next(
            (c for c in creds if c.idempotency_key == "intel:%s:reject" % vid), None
        )
        # P1-B 裁决门旁路检测：凭据绑定指纹 vs 现池行重算指纹。
        # 池行 adopt/reject 后被改写 = 凭据锚的是 A 实际是 B = 亮红。
        if (cred_adopt or cred_reject) is not None:
            cred = cred_adopt or cred_reject
            if cred.content_hash:
                now_hash = hash_entry(e)
                if now_hash != cred.content_hash:
                    findings.append(
                        "pool_tampered: %s 池行指纹 %s 与凭据 %s 不一致"
                        "（adopt/reject 后池行被改写，裁决门旁路）"
                        % (vid, now_hash, cred.content_hash)
                    )
        if "intel:%s:adopt" % vid in cred_keys:
            ok = _intel_has(ms, vid)
            states.append(
                {
                    "line": e["_line"],
                    "verdict_id": vid,
                    "state": "adopted" if ok else "pending_execution",
                }
            )
            if not ok:
                findings.append("凭据在而生产记录缺（崩溃窗口）：%s" % vid)
        elif "intel:%s:reject" % vid in cred_keys:
            states.append({"line": e["_line"], "verdict_id": vid, "state": "rejected"})
        else:
            states.append(
                {"line": e["_line"], "verdict_id": vid, "state": "pending_review"}
            )

    # ② 红线：生产记录中出现无凭据的裁决 = 绕门
    n_bypass = 0
    adopted_vids = []
    for rec in memory_rows:
        vid = rec.get("payload", {}).get("verdict_id", "")
        adopted_vids.append(vid)
        if "intel:%s:adopt" % vid not in cred_keys:
            n_bypass += 1
            findings.append(
                "bypass_adopt: share/intel 无凭据条目"
                " verdict_id=%s" % (vid or "<missing>")
            )

    # ③ 交叉校验（trace 域语义：生产记录即痕迹，adopted 凭据逐张对账）
    traces: Dict[str, Dict] = {}
    for vid in adopted_vids:
        t = traces.setdefault(
            "intel:%s" % vid, {"statuses": [], "consumed_keys": set()}
        )
        t["statuses"].append("adopted")
    cross_v = cross_check("intel", creds, traces, {tid: "adopted" for tid in traces})

    gate_fail = bool(findings) or n_bypass > 0
    verdict = verdict_for(([{"severity": "FAIL"}] if gate_fail else []) + cross_v)
    return {
        "verdict": verdict,
        "verdicts_total": len(pool),
        "states": states,
        "memory_total": len(memory_rows),
        "bypass_count": n_bypass,
        "credentials": len(creds),
        "findings": findings,
        "cross_check": cross_v,
        "read_only": True,
    }


# ----------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M2-P3 情报裁决域")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_d = sub.add_parser("draft")
    p_d.add_argument("--verdict-id", required=True)
    p_d.add_argument("--table", required=True, help="表 URN")
    p_d.add_argument("--job-run", required=True)
    p_d.add_argument("--break-kind", required=True, choices=sorted(_KNOWN_BREAKS))
    p_d.add_argument("--decision", required=True, choices=sorted(_KNOWN_DECISIONS))
    p_d.add_argument("--impact", default="", help="逗号分隔下游清单")
    p_d.add_argument("--rationale", required=True)
    p_d.add_argument("--by", required=True)
    p_a = sub.add_parser("adopt")
    p_a.add_argument("verdict_id")
    p_a.add_argument("--by", required=True)
    p_a.add_argument("--reason", default="")
    p_r = sub.add_parser("reject")
    p_r.add_argument("verdict_id")
    p_r.add_argument("--by", required=True)
    p_r.add_argument("--reason", default="")
    sub.add_parser("pool")
    sub.add_parser("report")
    p_ad = sub.add_parser("auto-draft")
    p_ad.add_argument("--api-base", default="http://127.0.0.1:8091")
    p_ad.add_argument("--by", default="agent-hermes")
    args = ap.parse_args(argv)

    if args.cmd == "pool":
        for e in load_pool():
            print(json.dumps(e, ensure_ascii=False))
    elif args.cmd == "auto-draft":
        print(
            json.dumps(
                auto_draft_from_lineage(
                    _AGENT_PLATFORM, api_base=args.api_base, by=args.by
                ),
                ensure_ascii=False,
                indent=1,
            )
        )
    elif args.cmd == "report":
        print(json.dumps(audit_report(), ensure_ascii=False, indent=1))
    elif args.cmd == "draft":
        rec = draft(
            _AGENT_PLATFORM,
            {
                "verdict_id": args.verdict_id,
                "table_urn": args.table,
                "job_run_id": args.job_run,
                "break_kind": args.break_kind,
                "decision": args.decision,
                "impact": [x for x in args.impact.split(",") if x],
                "rationale": args.rationale,
            },
            args.by,
        )
        print(json.dumps(rec, ensure_ascii=False))
    elif args.cmd == "adopt":
        ap_, rec, replayed = adopt(
            _AGENT_PLATFORM, args.verdict_id, args.by, args.reason
        )
        print(
            json.dumps(
                {"approval_id": ap_.approval_id, "replayed": replayed, "memory": rec},
                ensure_ascii=False,
            )
        )
    else:
        ap_, replayed = reject(_AGENT_PLATFORM, args.verdict_id, args.by, args.reason)
        print(
            json.dumps(
                {"approval_id": ap_.approval_id, "replayed": replayed},
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
