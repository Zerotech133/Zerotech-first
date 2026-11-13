# -*- coding: utf-8 -*-
"""verifier_registry/v1 + quarantine_registry/v1 —— 验证器登记与整批隔离。

解决的问题
----------
1. 「验证器通过」这件事目前没有账：夹具跑完只有一个 JSON 回执，三个月后
   回答不了「当时按哪版判据判的、那版判据改过没有」。本模块按 rules_version
   追溯，幂等键 (guard_id, rules_version, exec_day)，重复跑只 REPLAYED。
2. 「挂进事件账」照 attach_drift 的纪律：**目标日没有既有事件就不凭空开一天
   流程账**。11-13 是抑制日、没有盘前事件，本轮 attach 只能 dry-run 并明确
   报 NO_EVENT_FOR_DAY，不能为让链路变绿造一天事件。
3. 今天 v1 quarantine 是一次性文件（一个 JSON 描述 6 条 BUY）。训练集消费方
   没有统一入口判断「这批能不能用」。本模块把它固化为「整批隔离」机制：
   登记批次 + 消费方过滤，历史流水按 append-only 纪律一个字节都不改。

落盘边界
--------
· registry.jsonl / quarantine_registry.jsonl 是新建账本，默认放仓外
  C:/项目/deliverables/verifier/（索引/账本类文件不进 public 仓）。
· 事件账 var/preopen/events/ 是生产账本：只有 --apply 才写，默认 dry-run。
· 不改任何生产脚本、不写账户/流水/抑制令、不联网。

用法：
    sync                         从契约/探测回执生成登记记录（--apply 才落账）
    query --rules-version V      按口径版本追溯
    attach --day YYYYMMDD        挂事件账（--apply 才写，默认 dry-run）
    quarantine --ingest PATH     吸收一次性 quarantine 文件
    quarantine --check --rules-version v1 --day YYYYMMDD
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from verifier_contract import (  # noqa: E402
    EXEC_DAY, ROOT, RULES_VERSION as CONTRACT_VERSION,
    build_guards, fixtures_for,
)

OUT_DIR = ROOT.parent / "deliverables" / "verifier"
REGISTRY = OUT_DIR / "registry.jsonl"
QUARANTINE = OUT_DIR / "quarantine_registry.jsonl"
EVENT_LOG_PATH = ROOT / "tools" / "preopen" / "event_log.py"
LEGACY_QUARANTINE = ROOT / "var" / "audit" / "paper_trade_v1_quarantine_20261113.json"
CONTRACT_REPORT = OUT_DIR / "verifier_contract_回填结果_20261113.json"
PROBE_REPORT = OUT_DIR / "shortcut_probe_20261113.json"

REGISTRY_VERSION = "verifier_registry/v1"
QUARANTINE_VERSION = "quarantine_registry/v1"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _append_jsonl(path: Path, rec: dict, key_fields: tuple) -> str:
    """append-only + 幂等。同键返回 REPLAYED，不新增行。"""
    rows = _read_jsonl(path)
    key = tuple(rec.get(k) for k in key_fields)
    for r in rows:
        if tuple(r.get(k) for k in key_fields) == key:
            return "REPLAYED"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return "APPENDED"


def contract_hash(guard_id: str) -> str:
    """夹具定义 + 契约版本的指纹：判据一改，hash 就变，可追溯。"""
    cases = fixtures_for(guard_id)
    payload = {
        "contract_version": CONTRACT_VERSION,
        "guard_id": guard_id,
        "cases": [(c.kind, c.id, c.expect_pass, c.note) for c in cases],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


# ── 验证器登记 ──────────────────────────────────────────────────────────
def build_records(exec_day: str = EXEC_DAY) -> list[dict]:
    contract = json.loads(CONTRACT_REPORT.read_text(encoding="utf-8"))
    probe = json.loads(PROBE_REPORT.read_text(encoding="utf-8"))
    by_guard = {g["guard_id"]: g for g in contract["guards"]}
    hits_by_gate = {}
    for r in probe["probes"]:
        hits_by_gate.setdefault(r["gate"], []).append(r["id"] if r["hit"] else None)
    recs = []
    for g in build_guards():
        gid = g["guard_id"]
        c = by_guard.get(gid, {})
        gate = "G0" if gid.startswith("G0") else ("G1" if gid.startswith("G1") else "MUT")
        recs.append({
            "schema": REGISTRY_VERSION,
            "record_id": "%s|%s|%s" % (gid, CONTRACT_VERSION, exec_day),
            "guard_id": gid,
            "subject": g["subject"],
            "rules_version": CONTRACT_VERSION,
            "exec_day": exec_day,
            "contract_verdict": c.get("verdict", "UNKNOWN"),
            "evidence_level": g["evidence_level"],
            "fixture_kinds": c.get("fixture_kinds", []),
            "no_op_survived": c.get("no_op_survived", None),
            "shortcut_cases": c.get("shortcuts", []),
            "shortcut_hits": [x for x in hits_by_gate.get(gate, []) if x],
            "contract_hash": contract_hash(gid),
            "evidence_path": str(CONTRACT_REPORT),
            "created_at": _now(),
        })
    return recs


def sync(apply: bool = False) -> dict:
    recs = build_records()
    actions = []
    for r in recs:
        if apply:
            act = _append_jsonl(REGISTRY, r,
                                ("guard_id", "rules_version", "exec_day"))
        else:
            rows = _read_jsonl(REGISTRY)
            act = ("REPLAYED" if any(
                (x.get("guard_id"), x.get("rules_version"), x.get("exec_day")) ==
                (r["guard_id"], r["rules_version"], r["exec_day"]) for x in rows)
                else "WOULD_APPEND")
        actions.append({"guard_id": r["guard_id"], "action": act,
                        "contract_hash": r["contract_hash"]})
    return {"mode": "apply" if apply else "dry-run", "records": actions}


def query(rules_version: str | None = None) -> list[dict]:
    rows = _read_jsonl(REGISTRY)
    if rules_version:
        rows = [r for r in rows if r.get("rules_version") == rules_version]
    return rows


# ── 挂事件账（照 attach_drift 纪律，不凭空开流程账） ──────────────────────
def attach_to_event(day: str, apply: bool = False) -> dict:
    evlog = _load(EVENT_LOG_PATH, "vr_event_log")
    ev = evlog.load(day)
    recs = build_records(day)
    if not ev:
        return {
            "mode": "apply" if apply else "dry-run", "day": day,
            "status": "NO_EVENT_FOR_DAY",
            "action": "none",
            "detail": "目标日没有既有事件；事后记录不能凭空开一天流程账"
                      "（同 attach_drift）。记录只留在 registry，等该日事件存在后再挂。",
            "n_records": len(recs),
            "codepoints": [ord(c) for c in day],
        }
    existing = {x.get("record_id") for x in (ev.get("verifier") or [])}
    fresh = [r for r in recs if r["record_id"] not in existing]
    if not apply:
        return {"mode": "dry-run", "day": day, "status": "WOULD_ATTACH",
                "action": "none", "n_fresh": len(fresh),
                "n_existing": len(existing), "n_records": len(recs)}
    if not fresh:
        return {"mode": "apply", "day": day, "status": "REPLAYED",
                "action": "none", "n_fresh": 0, "n_existing": len(existing),
                "n_records": len(recs)}
    ev.setdefault("verifier", []).extend(fresh)
    ev["verifier_appended_at"] = _now()
    p = Path(evlog._p(day))
    p.write_text(json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")
    evlog._rebuild_index()
    return {"mode": "apply", "day": day, "status": "ATTACHED",
            "action": "appended", "n_fresh": len(fresh), "n_records": len(recs),
            "event_path": str(p)}


# ── 整批隔离 ────────────────────────────────────────────────────────────
def legacy_to_batch(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    day = str(raw.get("exec_day") or EXEC_DAY)
    rules = "v1"
    return {
        "schema": QUARANTINE_VERSION,
        "quarantine_id": "%s|%s|%s" % (rules, day, "quant_backtest/data/paper/trades.jsonl"),
        "rules_version": rules,
        "exec_day": day,
        "scope": "whole_batch",
        "source": raw.get("source", "quant_backtest/data/paper/trades.jsonl"),
        "reason": raw.get("reason", ""),
        "training_usable": False,
        "n_records": int(raw.get("n_records") or len(raw.get("records") or [])),
        "fill_bias_vs_decision_time_pct_mean": raw.get(
            "fill_bias_vs_decision_time_pct_mean"),
        "required_action": raw.get("required_action", ""),
        "evidence_path": str(path),
        "supersedes": "paper_trade_sample_quarantine/v1",
        "created_at": _now(),
    }


def ingest(path: Path, apply: bool = False) -> dict:
    batch = legacy_to_batch(path)
    if not apply:
        rows = _read_jsonl(QUARANTINE)
        act = ("REPLAYED" if any(x.get("quarantine_id") == batch["quarantine_id"]
                                 for x in rows) else "WOULD_APPEND")
    else:
        act = _append_jsonl(QUARANTINE, batch, ("quarantine_id",))
    return {"mode": "apply" if apply else "dry-run", "action": act,
            "batch": batch}


def is_quarantined(rules_version: str, day: str | None = None,
                   code: str | None = None) -> bool:
    """训练集消费方唯一入口。整批隔离：命中 rules_version + (可选) day 即排除。"""
    for b in _read_jsonl(QUARANTINE):
        if b.get("rules_version") != rules_version:
            continue
        if b.get("training_usable") is not False:
            continue
        if b.get("scope") == "whole_batch":
            if day is None or str(b.get("exec_day")) == str(day):
                return True
    return False


def filter_records(records: list[dict], rules_version: str | None = None,
                   day: str | None = None) -> tuple[list[dict], list[dict]]:
    """消费方过滤，不改历史流水。返回 (kept, excluded)。"""
    kept, excluded = [], []
    for r in records:
        rv = str(r.get("rules_version") or r.get("rules") or rules_version or "")
        d = str(r.get("day") or day or "")
        if rv and is_quarantined(rv, d or None, r.get("code")):
            excluded.append(r)
        else:
            kept.append(r)
    return kept, excluded


# ── CLI ────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="验证器登记 / 整批隔离")
    ap.add_argument("cmd", choices=["sync", "query", "attach", "quarantine"])
    ap.add_argument("--apply", action="store_true", help="真正落账（默认 dry-run）")
    ap.add_argument("--day", default=EXEC_DAY)
    ap.add_argument("--rules-version", default=None)
    ap.add_argument("--ingest", default=None)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    if args.cmd == "sync":
        rep = sync(args.apply)
    elif args.cmd == "query":
        rows = query(args.rules_version)
        rep = {"mode": "query", "rules_version": args.rules_version,
               "n": len(rows), "rows": rows}
        for r in rows:
            print("%-28s %-12s %s" % (r["guard_id"], r["contract_verdict"],
                                      r["contract_hash"]))
    elif args.cmd == "attach":
        rep = attach_to_event(args.day, args.apply)
    else:
        p = Path(args.ingest) if args.ingest else LEGACY_QUARANTINE
        rep = ingest(p, args.apply)
        if args.check:
            rv = args.rules_version or "v1"
            rep["check"] = {"rules_version": rv, "day": args.day,
                            "is_quarantined": is_quarantined(rv, args.day)}
    if args.cmd != "query":
        print(json.dumps(rep, ensure_ascii=False, indent=1)[:2400])
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
