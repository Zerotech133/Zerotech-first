"""M2-P1 六域证据收编扫描器（真源只读，只写证据仓）。

逐域扫描现有事实源，把当前可观测状态固化为 Evidence 记录：
    gzh      → drafts/index.json + md 指纹（artifact_fingerprint）
    academy  → research_topics.json 阶段机现状（stage_observation）
    personal → schedules.json 排程现状（config_observation）
    dev      → TaskStore 任务现状（status_observation）
    invest   → runs_index.json 决策指纹（decision_fingerprint）
    pitch    → decks.json（绿地域，账本空则 0 条，fail-visible）
    shadow   → shadow_ledger.jsonl 逐条轨迹（shadow_trajectory；
               观察点判例：轨迹是观测不是决策，登记不设 Approval 门）
    shadow   → shadow_ledger.jsonl 逐条轨迹（shadow_trajectory；
               观察点判例：轨迹是观测不是决策，登记不设 Approval 门）

纪律：
- 真源零写入（扫描只读；唯一写目标 = evidence.db）；
- 幂等：同 fact_hash 重放零副作用，重复扫描 0 新增；
- 真源变化 → 新 fact_hash → append-only 新观测行（历史保留）；
- 缺文件 fail-visible 记 finding，不伪造空证据；
- 记录证据不设 Approval 门（观测非承诺，凭据不泛滥判例）。

CLI：``python adapters/evidence_collect.py collect [--domains gzh,dev]``
     ``python adapters/evidence_collect.py report``
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List

_ADAPTERS_DIR = Path(__file__).resolve().parent
_AGENT_PLATFORM = _ADAPTERS_DIR.parent
if str(_AGENT_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_AGENT_PLATFORM))

from contracts import Evidence                    # noqa: E402
from engine.evidence import EvidenceStore, fact_hash  # noqa: E402

EVIDENCE_DB = _AGENT_PLATFORM / "data" / "evidence" / "evidence.db"

ALL_DOMAINS = ["gzh", "academy", "personal", "dev", "invest", "pitch",
               "shadow", "artifact", "paper"]


# ----------------------------------------------------------------------
# 逐域收集器：返回 (List[Evidence], findings)
# ----------------------------------------------------------------------
def _collect_gzh(root: Path):
    evs, findings = [], []
    idx = root / "gzh" / "drafts" / "index.json"
    if not idx.exists():
        return evs, ["gzh: drafts/index.json 缺失，跳过该域"]
    for item in json.loads(idx.read_text(encoding="utf-8")):
        md = root / "gzh" / "drafts" / item.get("md", "")
        fp = ""
        if item.get("md") and md.exists():
            import hashlib
            fp = hashlib.sha256(md.read_bytes()).hexdigest()[:16]
        else:
            findings.append("gzh: %s md 缺失，指纹留空（不伪造）"
                            % item.get("id"))
        evs.append(Evidence(
            source="gzh.drafts", source_ref="gzh:%s" % item.get("id", ""),
            statement="status=%s md_sha256=%s" % (item.get("status", ""), fp),
            payload={"domain": "gzh", "kind": "artifact_fingerprint",
                     "title": item.get("title", "")}))
    return evs, findings


def _collect_academy(root: Path):
    evs, findings = [], []
    p = root / "agent_platform" / "data" / "academy" / "research_topics.json"
    if not p.exists():
        return evs, ["academy: research_topics.json 缺失，跳过该域"]
    for t in json.loads(p.read_text(encoding="utf-8")):
        evs.append(Evidence(
            source="academy.research_topics",
            source_ref="academy:%s" % t.get("topic_id", ""),
            statement="status=%s history_len=%d"
                      % (t.get("status", ""), len(t.get("history", []))),
            payload={"domain": "academy", "kind": "stage_observation",
                     "title": t.get("title", "")}))
    return evs, findings


def _collect_personal(root: Path):
    evs, findings = [], []
    p = root / "agent_platform" / "data" / "schedules.json"
    if not p.exists():
        return evs, ["personal: schedules.json 缺失，跳过该域"]
    data = json.loads(p.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else data.get("schedules", [])
    for s in items:
        evs.append(Evidence(
            source="personal.schedules",
            source_ref="sched:%s" % s.get("id", ""),
            statement="enabled=%s title=%s"
                      % (s.get("enabled"), s.get("title", "")),
            payload={"domain": "personal", "kind": "config_observation"}))
    return evs, findings


def _collect_dev(root: Path):
    evs, findings = [], []
    base = root / "agent_platform" / "data" / "org" / "tasks"
    if not base.exists():
        return evs, ["dev: org/tasks 缺失，跳过该域"]
    from engine.tasks import TaskStore
    for t in TaskStore(base).ls():
        evs.append(Evidence(
            source="dev.tasks", source_ref="dev:%s" % t.get("task_id", ""),
            statement="status=%s title=%s"
                      % (t.get("status", ""), t.get("title", "")),
            payload={"domain": "dev", "kind": "status_observation"}))
    return evs, findings


def _collect_invest(root: Path):
    evs, findings = [], []
    p = root / "agent_platform" / "data" / "invest" / "runs_index.json"
    if not p.exists():
        return evs, ["invest: runs_index.json 缺失，跳过该域"]
    data = json.loads(p.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else data.get("runs", [])
    for r in items:
        evs.append(Evidence(
            source="invest.runs_index",
            source_ref="invest:%s" % r.get("run_id", r.get("id", "")),
            statement="status=%s decision_sha256=%s"
                      % (r.get("status", ""), r.get("decision_sha256", "")),
            payload={"domain": "invest", "kind": "decision_fingerprint"}))
    return evs, findings


def _collect_pitch(root: Path):
    evs, findings = [], []
    p = root / "agent_platform" / "data" / "pitch" / "decks.json"
    if not p.exists():
        # 绿地域：账本未建属正常（首个 deck 才落盘），非异常
        return evs, []
    data = json.loads(p.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else data.get("decks", [])
    for d in items:
        art = d.get("artifact") or {}
        evs.append(Evidence(
            source="pitch.decks", source_ref="pitch:%s" % d.get("deck_id", ""),
            statement="status=%s artifact_sha256=%s"
                      % (d.get("status", ""), art.get("sha256", "")),
            payload={"domain": "pitch", "kind": "artifact_fingerprint",
                     "title": d.get("title", "")}))
    return evs, findings


def _collect_shadow(root: Path):
    """shadow 轨迹收编：shadow_ledger.jsonl 逐条 → Evidence。

    观察点判例（M2 验收遗留）：轨迹是客观观测（LLM 曾给出什么建议、
    解析是否成功），不是决策也不是承诺——登记不设 Approval 门。
    幂等键来自 statement（含 run_id/parse_ok），重放零副作用；
    坏行 fail-visible 记 finding，不伪造、不静默跳过。
    """
    evs, findings = [], []
    p = root / "agent_platform" / "data" / "shadow" / "shadow_ledger.jsonl"
    if not p.exists():
        return evs, ["shadow: shadow_ledger.jsonl 缺失，跳过该域"]
    for lineno, line in enumerate(
            p.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            if not isinstance(rec, dict):
                raise ValueError("not an object")
        except (json.JSONDecodeError, ValueError):
            findings.append(
                "shadow: 第 %d 行非法 JSON，跳过（不伪造）" % lineno)
            continue
        date = str(rec.get("date", ""))
        symbol = str(rec.get("symbol", ""))
        run_id = str(rec.get("run_id", ""))
        evs.append(Evidence(
            source="shadow.ledger",
            source_ref="shadow:%s:%s" % (date, symbol),
            statement="status=%s run_id=%s rec=%s parse_ok=%s degraded=%s "
                      "price_at_decision=%s"
                      % (rec.get("status", ""), run_id,
                         rec.get("recommendation"), rec.get("parse_ok"),
                         rec.get("degraded"), rec.get("price_at_decision")),
            payload={"domain": "shadow", "kind": "shadow_trajectory",
                     "date": date, "symbol": symbol, "run_id": run_id}))
    return evs, findings


def _collect_artifact(root: Path, explicit: bool = False):
    """产物登记观测（M3-P4b）：artifacts.db 逐条 → Evidence。

    登记行为本身是客观观测（M3-P1 判例：登记不设门）；supersede/
    revision 一并留痕，形成「登记→证据仓」二级沉淀。幂等来自
    statement（含 artifact_id/content_hash），重放零副作用。

    explicit：点名 artifact 域时缺失要 fail-visible（留 finding）；
    全域 collect 缺失属双轨起点正常态，静默空表。
    """
    evs, findings = [], []
    db = root / "agent_platform" / "data" / "artifacts" / "artifacts.db"
    if not db.exists():
        if explicit:
            return evs, ["artifact: artifacts.db 缺失（尚无登记），跳过该域"]
        return evs, findings
    con = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT artifact_id, kind, path, content_hash, fact_hash,"
            " revision, supersedes, source_system, source_ref, produced_by,"
            " registered_at FROM artifacts ORDER BY registered_at, rowid"
        ).fetchall()
    finally:
        con.close()
    for r in rows:
        evs.append(Evidence(
            source="artifacts.registry",
            source_ref="artifact:%s" % r["artifact_id"],
            statement="kind=%s path=%s hash=%s rev=%s supersedes=%s "
                      "src=%s ref=%s by=%s"
                      % (r["kind"], r["path"],
                         (r["content_hash"] or r["fact_hash"] or "")[:16],
                         r["revision"], r["supersedes"] or "-",
                         r["source_system"], r["source_ref"],
                         r["produced_by"]),
            payload={"domain": "artifact", "kind": "artifact_observation",
                     "artifact_id": r["artifact_id"],
                     "source_system": r["source_system"],
                     "revision": r["revision"]}))
    return evs, findings


def _collect_paper(root: Path):
    """paper 事件观测（M4-P2）：paper_events.jsonl 逐条 → Evidence。

    绿地域：账本未建属正常起点（首个 ai_operator paper 事件才落盘），
    不进 findings。statement 含 fact_hash/decision_id 指纹，可对账。
    """
    evs, findings = [], []
    p = root / "agent_platform" / "data" / "invest" / "paper_events.jsonl"
    if not p.exists():
        return evs, findings
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError as exc:
            findings.append("paper: 第 %d 行解析失败: %s" % (i, exc))
            continue
        evs.append(Evidence(
            source="invest.paper_events",
            source_ref="paper:%s" % r.get("trace_id", ""),
            statement="env=%s action=%s code=%s fact_hash=%s decision_id=%s"
                      % (r.get("environment", ""), r.get("action", ""),
                         r.get("code", ""), r.get("fact_hash", ""),
                         r.get("decision_id", "")),
            payload={"domain": "paper", "kind": "paper_event_fingerprint",
                     "idempotency_key": r.get("idempotency_key", ""),
                     "proposed_real": bool(r.get("proposed_real"))}))
    return evs, findings


_COLLECTORS = {"gzh": _collect_gzh, "academy": _collect_academy,
               "personal": _collect_personal, "dev": _collect_dev,
               "invest": _collect_invest, "pitch": _collect_pitch,
               "shadow": _collect_shadow, "artifact": _collect_artifact,
               "paper": _collect_paper}


# ----------------------------------------------------------------------
def collect(root: Path, domains: List[str] = None,
            store: EvidenceStore = None) -> Dict:
    """六域收编：真源只读扫描 → 证据仓。返回收编报告。"""
    root = Path(root)
    domains = domains or ALL_DOMAINS
    own = store is None
    store = store or EvidenceStore(EVIDENCE_DB)
    report: Dict = {"domains": {}, "findings": [], "new": 0, "replayed": 0}
    try:
        for dom in domains:
            fn = _COLLECTORS.get(dom)
            if fn is None:
                report["findings"].append("unknown domain: %s" % dom)
                continue
            if dom == "artifact":
                # 点名 artifact 且登记仓缺失 → fail-visible；全域静默
                evs, findings = _collect_artifact(
                    root, explicit=(domains is not None
                                    and domains != ALL_DOMAINS))
            else:
                evs, findings = fn(root)
            report["findings"].extend(findings)
            n_new = n_re = 0
            for ev in evs:
                _, replayed = store.record(ev)
                if replayed:
                    n_re += 1
                else:
                    n_new += 1
            report["domains"][dom] = {"collected": len(evs),
                                      "new": n_new, "replayed": n_re}
            report["new"] += n_new
            report["replayed"] += n_re
        report["verdict"] = "PASS"
        return report
    finally:
        if own:
            store.close()


def report(root: Path) -> Dict:
    """证据仓现状（只读摘要 + 收编 dry-run 计数）。"""
    root = Path(root)
    out: Dict = {"ledger_exists": EVIDENCE_DB.exists()}
    if out["ledger_exists"]:
        store = EvidenceStore(EVIDENCE_DB)
        try:
            out["stats"] = store.stats()
        finally:
            store.close()
    else:
        out["stats"] = {"total": 0, "by_domain": {}, "by_kind": {}}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="M2-P1 六域证据收编")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_c = sub.add_parser("collect")
    p_c.add_argument("--root", default=".")
    p_c.add_argument("--domains", default="")
    sub.add_parser("report").add_argument("--root", default=".")
    args = ap.parse_args(argv)

    if args.cmd == "collect":
        doms = [d for d in args.domains.split(",") if d] or None
        rep = collect(Path(args.root), domains=doms)
    else:
        rep = report(Path(args.root))
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
