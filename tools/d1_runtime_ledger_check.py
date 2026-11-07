#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""D1 P0-05 runtime-ledger readiness checker (read-only).

Validates the three runtime books required by P0-05 without fabricating data:

* ``agent_platform/data/d1_decisions.jsonl``
* ``agent_platform/data/meetings/d1/*.json``
* ``agent_platform/data/training/episodes.jsonl``

It also traces the available decision -> handoff -> watch plan -> trigger ->
confirmation chain and compares local counts with the live 8091 API.  Empty or
missing books are reported as ``data_unavailable``; they are never filled with
samples.

Usage::

    python -X utf8 tools/d1_runtime_ledger_check.py
    python -X utf8 tools/d1_runtime_ledger_check.py --json --out deliverables/.../P0-05_readiness.json
    python -X utf8 tools/d1_runtime_ledger_check.py --require-data

Exit codes: 0 structural pass, 2 structural errors, 4 --require-data incomplete.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "d1_p0_05_runtime_ledger_check/v1"
DECISION_SCHEMA = "d1_decision/v1"
HANDOFF_SCHEMA = "d1_handoff/v2"
MEETING_SCHEMA = "d1_meeting/v1"
EPISODE_SCHEMA = "training_episode/v1"
DECISION_ID_RE = re.compile(r"^d1-\d{14}-[0-9a-f]{6}$")
HANDOFF_ID_RE = re.compile(r"^d1h-\d{14}-[0-9a-f]{6}$")
EPISODE_ID_RE = re.compile(r"^ep-\d{8}-[0-9A-F]{8}$")
TIERS = {"STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"}
DECISION_REQUIRED = ("decision_id", "code", "decided_at", "decided_by", "tier", "schema", "paper_only")
HANDOFF_REQUIRED = ("decision_id", "receipt_id", "schema", "paper_only", "watch_plans")

DECISIONS = ROOT / "agent_platform" / "data" / "d1_decisions.jsonl"
HANDOFFS = ROOT / "agent_platform" / "data" / "d1_handoffs.jsonl"
MEETINGS_DIR = ROOT / "agent_platform" / "data" / "meetings" / "d1"
EPISODES = ROOT / "agent_platform" / "data" / "training" / "episodes.jsonl"
WATCH_PLANS = ROOT / "agent_platform" / "data" / "watch_plans.jsonl"
WATCH_TRIGGERS = ROOT / "agent_platform" / "data" / "watch_triggers.jsonl"
PAPER_STATE = ROOT / "agent_platform" / "data" / "paper" / "state.json"
PAPER_LEDGER = ROOT / "agent_platform" / "data" / "paper" / "paper_ledger.jsonl"
MAX_ERRORS = 10


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _read_jsonl(path: Path) -> dict:
    out = {"path": str(path), "exists": path.exists(), "rows": [], "invalid": 0, "errors": []}
    if not path.exists():
        return out
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:  # noqa: BLE001
        out["read_error"] = f"{type(exc).__name__}: {exc}"[:300]
        return out
    for line_no, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception as exc:  # noqa: BLE001
            out["invalid"] += 1
            if len(out["errors"]) < MAX_ERRORS:
                out["errors"].append({"line": line_no, "code": "bad_json", "error": str(exc)[:200]})
            continue
        if not isinstance(row, dict):
            out["invalid"] += 1
            if len(out["errors"]) < MAX_ERRORS:
                out["errors"].append({"line": line_no, "code": "not_object"})
            continue
        out["rows"].append(row)
    return out


def _read_json_dir(path: Path) -> dict:
    out = {"path": str(path), "exists": path.exists(), "rows": [], "invalid": 0, "errors": []}
    if not path.exists():
        return out
    for fp in sorted(path.glob("*.json")):
        try:
            doc = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            out["invalid"] += 1
            if len(out["errors"]) < MAX_ERRORS:
                out["errors"].append({"file": fp.name, "code": "bad_json", "error": str(exc)[:200]})
            continue
        if not isinstance(doc, dict):
            out["invalid"] += 1
            if len(out["errors"]) < MAX_ERRORS:
                out["errors"].append({"file": fp.name, "code": "not_object"})
            continue
        out["rows"].append(doc)
    return out


def _check_decisions(decisions: dict) -> tuple[dict, list[str]]:
    errors: list[str] = []
    ids = Counter()
    tier_counts = Counter()
    bad_exec = 0
    for i, row in enumerate(decisions["rows"]):
        for key in DECISION_REQUIRED:
            if key not in row:
                errors.append(f"decision[{i}] missing {key}")
        if row.get("schema") != DECISION_SCHEMA:
            errors.append(f"decision[{i}] bad schema {row.get('schema')!r}")
        did = str(row.get("decision_id") or "")
        if not DECISION_ID_RE.match(did):
            errors.append(f"decision[{i}] bad id {did!r}")
        ids[did] += 1
        tier = str(row.get("tier") or "")
        if tier not in TIERS:
            errors.append(f"decision[{i}] bad tier {tier!r}")
        tier_counts[tier] += 1
        if row.get("paper_only") is not True:
            errors.append(f"decision[{i}] paper_only != true")
        if any(k in row for k in ("order", "order_id", "qty", "quantity", "broker", "execute", "trade_id")):
            bad_exec += 1
    dup = [k for k, v in ids.items() if v > 1]
    if dup:
        errors.append(f"duplicate decision_id {dup[:5]}")
    if bad_exec:
        errors.append(f"decisions with execution-like keys={bad_exec}")
    return {
        "exists": decisions["exists"],
        "rows": len(decisions["rows"]),
        "invalid_lines": decisions["invalid"],
        "errors_sample": decisions["errors"],
        "unique_ids": len(ids),
        "duplicate_ids": dup,
        "tiers": dict(tier_counts),
        "paper_only_all": all(r.get("paper_only") is True for r in decisions["rows"]),
        "clean": not errors,
    }, errors


def _check_handoffs(handoffs: dict) -> tuple[dict, list[str]]:
    errors: list[str] = []
    ids = Counter()
    decision_ids = set()
    plan_ids: list[str] = []
    for i, row in enumerate(handoffs["rows"]):
        for key in HANDOFF_REQUIRED:
            if key not in row:
                errors.append(f"handoff[{i}] missing {key}")
        if row.get("schema") != HANDOFF_SCHEMA:
            errors.append(f"handoff[{i}] bad schema {row.get('schema')!r}")
        rid = str(row.get("receipt_id") or "")
        if not HANDOFF_ID_RE.match(rid):
            errors.append(f"handoff[{i}] bad receipt_id {rid!r}")
        ids[rid] += 1
        decision_ids.add(str(row.get("decision_id") or ""))
        plans = row.get("watch_plans") or []
        if not isinstance(plans, list) or not plans:
            errors.append(f"handoff[{i}] watch_plans empty")
        else:
            for plan in plans:
                pid = str((plan or {}).get("plan_id") or "")
                if not pid:
                    errors.append(f"handoff[{i}] plan missing plan_id")
                else:
                    plan_ids.append(pid)
    dup = [k for k, v in ids.items() if v > 1]
    if dup:
        errors.append(f"duplicate receipt_id {dup[:5]}")
    return {
        "exists": handoffs["exists"],
        "rows": len(handoffs["rows"]),
        "invalid_lines": handoffs["invalid"],
        "errors_sample": handoffs["errors"],
        "unique_receipts": len(ids),
        "duplicate_ids": dup,
        "decision_ids": sorted(x for x in decision_ids if x),
        "watch_plan_ids": sorted(set(plan_ids)),
        "clean": not errors,
    }, errors


def _check_meetings(meetings: dict) -> tuple[dict, list[str]]:
    errors: list[str] = []
    status = Counter()
    schema_ok = 0
    for i, doc in enumerate(meetings["rows"]):
        if doc.get("schema") != MEETING_SCHEMA:
            errors.append(f"meeting[{i}] bad schema {doc.get('schema')!r}")
        else:
            schema_ok += 1
        if not str(doc.get("meeting_id") or ""):
            errors.append(f"meeting[{i}] missing meeting_id")
        status[str(doc.get("status") or "")] += 1
    return {
        "exists": meetings["exists"],
        "rows": len(meetings["rows"]),
        "schema_ok": schema_ok,
        "invalid_files": meetings["invalid"],
        "errors_sample": meetings["errors"],
        "status": dict(status),
        "data_unavailable": not meetings["exists"] or len(meetings["rows"]) == 0,
        "clean": not errors,
    }, errors


def _check_episodes(episodes: dict) -> tuple[dict, list[str]]:
    errors: list[str] = []
    ids = Counter()
    for i, row in enumerate(episodes["rows"]):
        if row.get("schema") != EPISODE_SCHEMA:
            errors.append(f"episode[{i}] bad schema {row.get('schema')!r}")
        eid = str(row.get("training_episode_id") or "")
        if not EPISODE_ID_RE.match(eid):
            errors.append(f"episode[{i}] bad training_episode_id {eid!r}")
        ids[eid] += 1
    dup = [k for k, v in ids.items() if v > 1]
    if dup:
        errors.append(f"duplicate episode id {dup[:5]}")
    return {
        "exists": episodes["exists"],
        "rows": len(episodes["rows"]),
        "invalid_lines": episodes["invalid"],
        "errors_sample": episodes["errors"],
        "valid_ids": len(ids),
        "duplicate_ids": dup,
        "data_unavailable": not episodes["exists"] or len(episodes["rows"]) == 0,
        "clean": not errors,
    }, errors


def _trace_chain(
    decisions: dict,
    handoffs: dict,
    plans: dict,
    triggers: dict,
    paper_state: dict,
    paper_ledger: dict,
) -> dict:
    decision_ids = {str(r.get("decision_id")) for r in decisions["rows"] if r.get("decision_id")}
    handoff_decision_ids = {str(r.get("decision_id")) for r in handoffs["rows"] if r.get("decision_id")}
    plan_decision_ids = set()
    plan_to_trigger = {}
    for plan in plans["rows"]:
        src = plan.get("source") or {}
        did = str(src.get("decision_id") or "")
        pid = str(plan.get("plan_id") or "")
        if did:
            plan_decision_ids.add(did)
        if pid:
            plan_to_trigger[pid] = str(plan.get("trigger_id") or "")
    trigger_ids = {str(r.get("trigger_id")) for r in triggers["rows"] if r.get("trigger_id")}
    confirmation_ids = {str(r.get("confirmation_id")) for r in triggers["rows"] if r.get("confirmation_id")}
    trade_ids = set()
    for row in paper_ledger["rows"]:
        for key in ("trade_id", "receipt_id"):
            if row.get(key):
                trade_ids.add(str(row.get(key)))
    positions = paper_state.get("positions") if isinstance(paper_state, dict) else None
    return {
        "decisions": len(decision_ids),
        "decisions_with_handoff": len(decision_ids & handoff_decision_ids),
        "handoffs": len(handoffs["rows"]),
        "handoffs_with_plan": sum(1 for r in handoffs["rows"] if r.get("watch_plans")),
        "plans": len(plans["rows"]),
        "plans_with_trigger": len([p for p in plan_to_trigger.values() if p]),
        "triggers": len(triggers["rows"]),
        "triggers_with_confirmation": sum(1 for r in triggers["rows"] if r.get("confirmation_id")),
        "confirmations": len(confirmation_ids),
        "paper_positions": len(positions) if isinstance(positions, dict) else None,
        "paper_trade_ids": len(trade_ids),
        "missing_links": {
            "decisions_without_handoff": sorted(decision_ids - handoff_decision_ids),
            "handoffs_without_plan": [r.get("receipt_id") for r in handoffs["rows"] if not r.get("watch_plans")],
            "plans_without_trigger": sorted([pid for pid, tid in plan_to_trigger.items() if not tid]),
        },
    }


def _http_json(path: str, timeout: float) -> tuple[int | None, dict | None, str]:
    url = "http://127.0.0.1:8091" + path
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return int(resp.status), json.loads(body), ""
    except urllib.error.HTTPError as exc:
        return int(exc.code), None, "HTTPError"
    except Exception as exc:  # noqa: BLE001
        return None, None, f"{type(exc).__name__}: {exc}"[:300]


def _api_checks(api: dict, decisions: dict, handoffs: dict, meetings: dict, episodes: dict) -> dict:
    out = {}
    expected = {
        "/api/d1/decisions": len(decisions["rows"]),
        "/api/d1/handoffs": len(handoffs["rows"]),
        "/api/d1/meeting": len(meetings["rows"]),
        "/api/d1/training/episodes": len(episodes["rows"]),
    }
    for path, count in expected.items():
        code, obj, err = _http_json(path, api.get("timeout", 4.0))
        item = {"path": path, "http": code, "expected_rows": count, "error": err}
        if isinstance(obj, dict):
            if path.endswith("/decisions"):
                got = (obj.get("summary") or {}).get("total", len(obj.get("decisions") or []))
            elif path.endswith("/handoffs"):
                got = (obj.get("summary") or {}).get("total", len(obj.get("handoffs") or []))
            elif path.endswith("/meeting"):
                got = (obj.get("summary") or {}).get("total", len(obj.get("meetings") or []))
            else:
                got = obj.get("count", len(obj.get("episodes") or []))
            item["api_rows"] = got
            item["data_unavailable"] = bool(obj.get("data_unavailable"))
            item["match"] = got == count
        else:
            item["match"] = False
        out[path] = item
    return out


def check(
    decisions_path: Path = DECISIONS,
    handoffs_path: Path = HANDOFFS,
    meetings_dir: Path = MEETINGS_DIR,
    episodes_path: Path = EPISODES,
    plans_path: Path = WATCH_PLANS,
    triggers_path: Path = WATCH_TRIGGERS,
    paper_state_path: Path = PAPER_STATE,
    paper_ledger_path: Path = PAPER_LEDGER,
    probe_api: bool = True,
    api_timeout: float = 4.0,
) -> dict:
    decisions_raw = _read_jsonl(decisions_path)
    handoffs_raw = _read_jsonl(handoffs_path)
    meetings_raw = _read_json_dir(meetings_dir)
    episodes_raw = _read_jsonl(episodes_path)
    plans_raw = _read_jsonl(plans_path)
    triggers_raw = _read_jsonl(triggers_path)
    paper_ledger_raw = _read_jsonl(paper_ledger_path)

    decisions, d_err = _check_decisions(decisions_raw)
    handoffs, h_err = _check_handoffs(handoffs_raw)
    meetings, m_err = _check_meetings(meetings_raw)
    episodes, e_err = _check_episodes(episodes_raw)
    paper_state = {}
    if paper_state_path.exists():
        try:
            paper_state = json.loads(paper_state_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            paper_state = {"error": f"{type(exc).__name__}: {exc}"[:200]}

    chain = _trace_chain(decisions_raw, handoffs_raw, plans_raw, triggers_raw, paper_state, paper_ledger_raw)
    api = _api_checks({"timeout": api_timeout}, decisions_raw, handoffs_raw, meetings_raw, episodes_raw) if probe_api else {}
    structural_errors = d_err + h_err + m_err + e_err
    incomplete = [name for name, book in (("d1_decisions", decisions), ("meetings", meetings), ("training", episodes)) if book.get("data_unavailable") or book.get("rows", 0) < 1]
    books = {
        "d1_decisions": decisions,
        "meetings": meetings,
        "training": episodes,
        "chain": chain,
        "paper_state": {"path": str(paper_state_path), "positions": (paper_state.get("positions") if isinstance(paper_state, dict) else None), "nav": (paper_state.get("nav") if isinstance(paper_state, dict) else None)},
    }
    return {
        "schema": SCHEMA,
        "checked_at": _now(),
        "mode": "readiness",
        "ok": not structural_errors,
        "ready": not incomplete,
        "structural_errors": structural_errors,
        "incomplete_books": incomplete,
        "books": books,
        "api": api,
        "gaps": (
            ([
                "d1_decisions 已有真实记录，但 meeting/training 账本为空或不存在；P0-05 需真实 D1 会议与 episode，禁止造数",
            ] if incomplete else [])
            + ([
                "trigger 已能追到 confirmation，但 paper 当前空仓、无 trade_id；decision→handoff→watch plan→trigger 已通，trade 段未有真实成交可追",
            ] if chain.get("paper_trade_ids", 0) == 0 else [])
        ),
        "next_actions": [
            "由 D1 在真实投研会中创建会议并完成 quorum/最终化 → meetings/d1/*.json",
            "由真实 paper 成交/outcome 链生成 training_episode → training/episodes.jsonl",
            "P0-05 验收前重跑本检查；不把空态或样例当真实事件",
        ],
    }


def render_markdown(result: dict) -> str:
    books = result.get("books") or {}
    lines = [
        "# D1 P0-05 运行时账本 readiness",
        "",
        f"- 检查时间：{result.get('checked_at')}",
        f"- 结构：{'PASS' if result.get('ok') else 'FAIL'}；三类账齐备：{'YES' if result.get('ready') else 'NO'}",
        f"- 未齐备：{', '.join(result.get('incomplete_books') or []) or '无'}",
        "",
        "## 账本",
        "",
        "| 账本 | 行数 | 状态 |",
        "| --- | --- | --- |",
    ]
    for name in ("d1_decisions", "meetings", "training"):
        book = books.get(name) or {}
        state = "data_unavailable" if book.get("data_unavailable") else ("clean" if book.get("clean", True) else "ERROR")
        lines.append(f"| {name} | {book.get('rows', 0)} | {state} |")
    chain = books.get("chain") or {}
    lines += [
        "",
        "## 四 ID 链（现有数据）",
        "",
        f"- decision → handoff：{chain.get('decisions_with_handoff', 0)}/{chain.get('decisions', 0)}",
        f"- handoff → watch plan：{chain.get('handoffs_with_plan', 0)}/{chain.get('handoffs', 0)}",
        f"- plan → trigger：{chain.get('plans_with_trigger', 0)}/{chain.get('plans', 0)}",
        f"- trigger → confirmation：{chain.get('triggers_with_confirmation', 0)}/{chain.get('triggers', 0)}",
        f"- paper trade ids：{chain.get('paper_trade_ids', 0)}；positions={chain.get('paper_positions')}",
        "",
        "## 缺口",
        "",
    ]
    lines += [f"- {x}" for x in (result.get("gaps") or ["无"])]
    lines += ["", "## 下一步", ""]
    lines += [f"{i + 1}. {x}" for i, x in enumerate(result.get("next_actions") or [])]
    lines += ["", "> 本工具只读，不创建会议/episode，不造数。", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D1 P0-05 runtime ledger readiness")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", default="")
    parser.add_argument("--no-api", action="store_true")
    parser.add_argument("--require-data", action="store_true")
    parser.add_argument("--timeout", type=float, default=4.0)
    args = parser.parse_args(argv)
    result = check(probe_api=not args.no_api, api_timeout=args.timeout)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix.lower() == ".json":
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            out.write_text(render_markdown(result), encoding="utf-8")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(result))
    if not result["ok"]:
        return 2
    if args.require_data and not result["ready"]:
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
