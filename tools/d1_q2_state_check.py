#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D1 P0-01 状态口径检查（只读）。

检查 manifest / compositions registry / trading_bot / A2A card / interface registry
/ q2_probe target / HR 名册是否使用同一套状态口径。

用法：
  python -X utf8 tools/d1_q2_state_check.py
  python -X utf8 tools/d1_q2_state_check.py --json
  python -X utf8 tools/d1_q2_state_check.py --strict

退出码：0=PASS；1=FAIL；2=参数/环境错误。
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "zerobound/d1_q2_state_check/v1"

MANIFEST = "compositions/lusu-d1-flow/manifest.yaml"
REGISTRY = "compositions/registry.json"
TRADING_BOT = "agent_platform/registry/trading_bot.json"
A2A_CARD = "platform/a2a/agent_cards/lusu-d1-flow.json"
INTERFACES = "docs/standards/INTERFACE_REGISTRY_v1.yaml"
PROBE = "tools/q2_probe.py"
ORG = "agent_platform/registry/org.json"
HR_DIR = "agent_platform/hr"

EXPECTED_SEATS = {
    "D1", "T1", "T2", "N1", "F1", "ST1", "QS1",
    "WA1", "WA2", "WA3", "TR1", "TR2", "TR3",
}
EXPECTED_HR_MISSING = {"WA2", "WA3", "TR2", "TR3"}
EXPECTED_INTERFACES = {
    "/api/d1/research",
    "/api/d1/decisions",
    "/api/d1/decision",
    "/api/d1/meeting",
    "/api/d1/handoffs",
    "/api/d1/handoff",
    "/api/d1/training/episodes",
    "/api/d1/watch-plans",
    "/api/war-room/snapshot",
}
EXPECTED_PROBE_NAMES = {
    "api-health", "health", "d1-research", "d1-decisions",
    "d1-handoffs", "d1-meeting", "d1-watch-plans", "d1-training-episodes",
}


def _load_yaml(path: Path):
    try:
        import yaml  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("缺少 PyYAML，无法检查 YAML 状态文件") from exc
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _probe_names(root: Path) -> set[str]:
    """从 q2_probe.TARGETS 静态读取 D1 目标名，不 import、不发请求。"""
    tree = ast.parse((root / PROBE).read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(t, ast.Name) and t.id == "TARGETS" for t in node.targets):
            targets = ast.literal_eval(node.value)
            if not isinstance(targets, dict):
                return set()
            return {name for name, _url in targets.get("lusu-d1-flow", [])}
    return set()


def _seat_refs(manifest: dict) -> list[str]:
    out = []
    for item in (manifest.get("spec") or {}).get("seats") or []:
        ref = str((item or {}).get("ref") or "")
        if ref.startswith("agent/"):
            out.append(ref.split("/", 1)[1])
    return out


def _org_agents(root: Path) -> set[str]:
    org = _load_json(root / ORG)
    agents = set()
    for dept in org.get("departments") or []:
        agents.update(str(x) for x in (dept.get("agents") or []))
    return agents


def check(root: Path = ROOT, strict: bool = False) -> dict:
    root = Path(root)
    errors: list[str] = []
    warnings: list[str] = []
    checks: list[dict] = []

    def add(cid: str, ok: bool, detail: str) -> None:
        checks.append({"id": cid, "ok": bool(ok), "detail": detail})
        if not ok:
            errors.append(f"{cid}: {detail}")

    def warn(cid: str, detail: str) -> None:
        warnings.append(f"{cid}: {detail}")

    manifest = _load_yaml(root / MANIFEST) or {}
    registry = _load_json(root / REGISTRY)
    bot = _load_json(root / TRADING_BOT)
    card = _load_json(root / A2A_CARD)
    ireg = _load_yaml(root / INTERFACES) or {}

    meta = manifest.get("metadata") or {}
    spec = manifest.get("spec") or {}
    status = manifest.get("status") or {}
    sla = ((spec.get("data") or {}).get("sla") or {}) or (spec.get("sla") or {})
    slo = spec.get("slo") or {}
    q2 = status
    mid = "lusu-d1-flow"

    # 1. manifest identity / lifecycle / status
    add("manifest.identity",
        meta.get("id") == mid and meta.get("owner") == "D1" and meta.get("tier") == "product",
        f"id={meta.get('id')} owner={meta.get('owner')} tier={meta.get('tier')}")
    add("manifest.lifecycle",
        meta.get("lifecycle") == "draft" and status.get("phase") == "shadow",
        f"lifecycle={meta.get('lifecycle')} phase={status.get('phase')}")
    add("manifest.q2_status",
        status.get("health") == "unknown"
        and status.get("health_basis") == "single_probe_pass"
        and status.get("health_window") == "not_measured"
        and status.get("validated") is False
        and status.get("q2_complete") is False
        and status.get("q_level") == "Q1",
        "health/health_basis/health_window/validated/q2_complete/q_level 口径")
    add("manifest.safety",
        sla.get("paper_only") is True
        and sla.get("human_gate_external_only") is True
        and sla.get("kill_switch") == "PAPER_KILL"
        and slo.get("trading_permitted") is False
        and slo.get("real_exchange_adapter") == "disabled",
        "paper_only / human_gate / kill_switch / trading_permitted / real_exchange_adapter")

    exposes = [
        str((x or {}).get("http") or "")
        for x in ((spec.get("interfaces") or {}).get("exposes") or [])
        if (x or {}).get("http")
    ]
    add("manifest.interfaces",
        len(exposes) == 9
        and len(set(exposes)) == 9
        and set(exposes) == EXPECTED_INTERFACES
        and (status.get("interfaces") or {}).get("active") == 9,
        f"count={len(exposes)} active={(status.get('interfaces') or {}).get('active')}")
    pages = [str((x or {}).get("ref") or "") for x in (spec.get("pages") or [])]
    jobs = [str((x or {}).get("ref") or "") for x in (spec.get("jobs") or [])]
    add("manifest.pages_jobs",
        len(pages) == 3 and len(jobs) == 4
        and (status.get("pages") or {}).get("active") == 3
        and (status.get("jobs") or {}).get("active") == 4,
        f"pages={len(pages)} jobs={len(jobs)}")

    # 2. watch runtime / health / slo / rollback
    wr = status.get("watch_runtime") or {}
    add("manifest.watch_runtime",
        slo.get("watch_runtime") == "watch_engine_running_paper_only"
        and wr.get("state") == "running_paper_only"
        and wr.get("enabled_live") is True
        and wr.get("paper_execution") is True
        and wr.get("real_exchange") is False
        and wr.get("human_confirm_required") == "external_test"
        and wr.get("watchbot_project") == "offline_contract",
        f"slo={slo.get('watch_runtime')} status={wr}")
    hp = status.get("health_probe") or {}
    add("manifest.health_probe",
        hp.get("last_result") == "ok_8_of_8"
        and hp.get("mode") == "single_probe"
        and hp.get("window") == "not_measured"
        and len(hp.get("endpoints") or []) == 8
        and "q2_probe.py" in str(hp.get("tool") or ""),
        f"last={hp.get('last_result')} mode={hp.get('mode')} window={hp.get('window')} endpoints={len(hp.get('endpoints') or [])}")
    sa = status.get("slo_alert") or {}
    add("manifest.slo_alert",
        str(sa.get("last_result") or "").startswith("not_measured")
        and "q2_slo_alert.py" in str(sa.get("tool") or "")
        and "2026-11-07" in str(sa.get("window") or "")
        and "2026-11-14" in str(sa.get("window") or ""),
        f"last={sa.get('last_result')} window={sa.get('window')}")
    rb = status.get("rollback") or {}
    add("manifest.rollback",
        str(rb.get("last_drilled") or "").startswith("pending")
        and "dry-run" in str(rb.get("last_result") or ""),
        f"last_drilled={rb.get('last_drilled')} last_result={rb.get('last_result')}")
    ct = status.get("contract_test") or {}
    state_ck = ct.get("state_check") or status.get("state_check") or {}
    add("manifest.contract_test",
        "45 passed" in str(ct.get("unit") or "")
        and "38" in str(ct.get("ifs_registry") or "")
        and "d1_q2_state_check.py" in str(state_ck)
        and (state_ck.get("status") if isinstance(state_ck, dict) else "") == "pass",
        f"unit={ct.get('unit')} ifs={ct.get('ifs_registry')} state_check={state_ck}")

    # 3. composition registry snapshot
    entries = [x for x in (registry.get("compositions") or []) if (x or {}).get("id") == mid]
    add("registry.entry", len(entries) == 1, f"entries={len(entries)}")
    if entries:
        e = entries[0]
        es = e.get("status") or {}
        add("registry.metadata",
            e.get("version") == meta.get("version")
            and e.get("lifecycle") == meta.get("lifecycle")
            and e.get("owner") == meta.get("owner")
            and e.get("tier") == meta.get("tier")
            and e.get("host") == meta.get("host"),
            f"version={e.get('version')} lifecycle={e.get('lifecycle')}")
        add("registry.status",
            es.get("phase") == status.get("phase")
            and es.get("health") == status.get("health")
            and es.get("health_basis") == status.get("health_basis")
            and es.get("health_window") == status.get("health_window")
            and es.get("validated") is status.get("validated")
            and es.get("q2_complete") is status.get("q2_complete")
            and es.get("q_level") == status.get("q_level"),
            f"registry={es}")

    # 4. trading bot flags / watch runtime
    add("trading_bot.flags",
        bot.get("owner") == "D1"
        and bot.get("manager") == "TR1"
        and bot.get("status") == "paper_only"
        and bot.get("trading_permitted") is False
        and bot.get("real_exchange_adapter") == "disabled"
        and bot.get("paper_only") is True,
        f"status={bot.get('status')} trading_permitted={bot.get('trading_permitted')}")
    bwr = bot.get("watch_runtime") or {}
    add("trading_bot.watch_runtime",
        bwr.get("enabled_live") is True
        and bwr.get("mode") == "paper_only"
        and bwr.get("paper_execution") is True
        and bwr.get("real_exchange") is False
        and bwr.get("human_confirm_required") == "external_test"
        and bwr.get("engine") == "WATCH_TOWER"
        and bwr.get("watchbot_project") == "offline_contract",
        f"watch_runtime={bwr}")

    # 5. A2A card
    xq2 = card.get("x-q2") or {}
    add("a2a.identity",
        card.get("x-composition") == mid
        and (card.get("provider") or {}).get("owner") == "D1"
        and card.get("version") == meta.get("version")
        and card.get("x-lifecycle") == meta.get("lifecycle"),
        f"version={card.get('version')} lifecycle={card.get('x-lifecycle')}")
    add("a2a.q2_status",
        xq2.get("health") == status.get("health")
        and xq2.get("health_basis") == status.get("health_basis")
        and xq2.get("health_window") == status.get("health_window")
        and xq2.get("validated") is status.get("validated")
        and xq2.get("q2_complete") is status.get("q2_complete")
        and xq2.get("q_level") == status.get("q_level"),
        f"x-q2={xq2}")

    # 6. interface registry
    interfaces = ireg.get("interfaces") or []
    paths = {
        str(x.get("path"))
        for x in interfaces
        if isinstance(x, dict) and str(x.get("path") or "").startswith(("/api/d1/", "/api/war-room/"))
    }
    counts = ireg.get("counts") or {}
    add("interface_registry.paths",
        len(interfaces) == 38
        and EXPECTED_INTERFACES <= paths
        and (counts.get("by_owner") or {}).get("D1") == 12,
        f"interfaces={len(interfaces)} d1_paths={len(paths)} owners={counts.get('by_owner')}")

    # 7. q2_probe target
    probe_names = _probe_names(root)
    add("q2_probe.target",
        probe_names == EXPECTED_PROBE_NAMES,
        f"names={sorted(probe_names)}")

    # 8. seats / HR / org
    seats = _seat_refs(manifest)
    hr_missing = {s for s in seats if not (root / HR_DIR / s / "resume.yaml").is_file()}
    org_agents = _org_agents(root)
    add("staffing.seats",
        len(seats) == 13 and set(seats) == EXPECTED_SEATS and set(seats) <= org_agents,
        f"seats={len(seats)} missing_in_org={sorted(set(seats) - org_agents)}")
    add("staffing.hr",
        hr_missing == EXPECTED_HR_MISSING,
        f"hr_missing={sorted(hr_missing)}")

    # 9. Q2 不完整时不冒充完成
    if status.get("q2_complete") is True:
        add("q2.complete_invariant", False, "q2_complete=true 但 Q2 证据未全绿，禁止")
    else:
        add("q2.complete_invariant", True, "q2_complete=false")
    if status.get("health_window") == "not_measured":
        warn("q2.health_window", "持续窗口未测；当前仅为单次探针通过")
    if status.get("health_probe", {}).get("last_result") == "ok_8_of_8":
        warn("q2.health_probe", "8/8 是单次探针，不等于 7d 可用性")

    if strict:
        errors.extend(warnings)
        warnings = []

    report = {
        "schema": SCHEMA,
        "ok": not errors,
        "root": str(root),
        "checked_at": "2026-11-06T07:38:00+08:00",
        "references": {
            "manifest": MANIFEST,
            "registry": REGISTRY,
            "trading_bot": TRADING_BOT,
            "a2a_card": A2A_CARD,
            "interfaces": INTERFACES,
            "q2_probe": PROBE,
        },
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
    }
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="D1 P0-01 状态口径检查（只读）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--strict", action="store_true", help="warning 也计为失败")
    ap.add_argument("--root", default=str(ROOT), help="仓库根目录")
    args = ap.parse_args(argv)
    try:
        report = check(Path(args.root), strict=args.strict)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("D1 P0-01 状态口径检查")
        for item in report["checks"]:
            print(f"[{'PASS' if item['ok'] else 'FAIL'}] {item['id']}: {item['detail']}")
        for item in report["warnings"]:
            print(f"[WARN] {item}")
        for item in report["errors"]:
            print(f"[ERROR] {item}")
        print("PASS" if report["ok"] else "FAIL")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
