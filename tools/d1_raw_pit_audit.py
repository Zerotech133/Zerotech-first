#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D1 raw/decision_id/PIT 只读审计（2026-11-01 K-ORG-20261101-D1RAWPIT）。

用途：对生产影子账本 / outcome sidecar 做**只读**体检，回答三件事：

1. **decision_id 覆盖与可关联性**
   - shadow_ledger 每行是否落 decision_id；按 ``engine/decision_policy.py`` 同公式
     复算后是否一致（无落值 ≠ 不一致，分开计数）；
   - outcome sidecar 的 decision_id 覆盖率，以及与 shadow_ledger 同 run_id 的
     关联/一致性（跨账 join）。
2. **raw refs 覆盖**
   - ``evidence_refs`` 里 raw ref（schema=evidence_ref/v2）与摘要 ref 的计数；
   - ``evidence_raw_refs_summary`` / ``raw_refs_status`` 覆盖；
   - 对 raw ref 做真实文件 sha256 复核（缺文件/哈希不符 → 明确计数，不假成功）。
3. **PIT 时点账**
   - ``as_of`` = **采集时刻**，``pit_as_of`` = **数据时点**；
   - 统计 ref 级四态：``data``（有 pit_as_of）/ ``collection_only``（只有 as_of）/
     ``missing``（都没有）；outcome 行是否落 pit_as_of / raw_refs_status。

纪律：**不写任何文件**（不写账本/不写 sidecar/不建目录）；不跑 D1 回写；不联网；
只读打开 JSONL + 读 raw 文件算 sha。缺文件时输出 ``exists=false`` 并从统计中排除，
退出码仍然 0（审计成功），JSON 内 ``findings`` 明示缺口。

用法::

    python -X utf8 tools/d1_raw_pit_audit.py                    # 人读摘要
    python -X utf8 tools/d1_raw_pit_audit.py --json             # 机器可读
    python -X utf8 tools/d1_raw_pit_audit.py --root <repo>      # 指定生产仓根只读审计
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True  # 只读审计：连 __pycache__ 也不写

try:  # Windows 管道重定向防 GBK 崩（仓库工具惯例）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

REPO = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO
SHADOW_REL = Path("agent_platform") / "data" / "shadow" / "shadow_ledger.jsonl"
OUTCOMES_REL = Path("agent_platform") / "data" / "shadow" / "decision_outcomes.jsonl"
SCHEMA = "d1_raw_pit_audit/v1"
FALLBACK_STALE_DAYS = 2  # shadow 最后行 mtime 距今超过该天数 → 记 finding（非阻断）

# ---------------------------------------------------------------------------
# 只读基础
# ---------------------------------------------------------------------------
def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stat(path: Path) -> dict:
    if not path.is_file():
        return {"path": str(path), "exists": False}
    st = path.stat()
    return {
        "path": str(path),
        "exists": True,
        "bytes": st.st_size,
        "mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                               time.localtime(st.st_mtime)),
        "sha256": _sha256_file(path),
    }


def _load_jsonl(path: Path):
    """只读 JSONL；返回 (rows, bad_lines)。文件不存在 → ([], 0)。"""
    rows, bad = [], 0
    if not path.is_file():
        return rows, bad
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if isinstance(obj, dict):
                rows.append(obj)
            else:
                bad += 1
    return rows, bad


def _load_decision_id_fn():
    """复用 decision_outcomes.decision_id（单一真源）；失败回退同公式。"""
    target = REPO / "agent_platform" / "decision_outcomes.py"
    try:
        spec = importlib.util.spec_from_file_location(
            "_d1rawwriter_decision_outcomes", target)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.decision_id
    except Exception:  # noqa: BLE001 - 审计工具回退同公式（tests 断言一致）
        def _fallback(symbol, date, run_id):
            raw = "%s|%s|%s" % (symbol, date, run_id)
            return "D1-%s-%s" % (
                str(date).replace("-", ""),
                hashlib.sha256(raw.encode()).hexdigest()[:10])
        return _fallback


def _load_verify_raw_ref():
    """复用 engine/evidence_bundle.verify_raw_ref（raw 复核唯一真源）。"""
    engine = REPO / "agent_platform" / "engine"
    if str(engine) not in sys.path:
        sys.path.insert(0, str(engine))
    try:
        from evidence_bundle import verify_raw_ref  # noqa: PLC0415
    except Exception:  # noqa: BLE001 - 引擎不可用 → 审计标记未核验
        return None
    return verify_raw_ref


# ---------------------------------------------------------------------------
# 分类
# ---------------------------------------------------------------------------
def _is_raw_ref(ref: dict) -> bool:
    return (str(ref.get("schema") or "") == "evidence_ref/v2"
            and bool(str(ref.get("raw_uri") or "").strip())
            and bool(str(ref.get("raw_sha256") or "").strip()))


def _as_of_kind(ref: dict) -> str:
    """PIT 分账：data（有数据时点）/ collection_only / missing。"""
    if str(ref.get("pit_as_of") or "").strip():
        return "data"
    if str(ref.get("as_of") or "").strip():
        return "collection_only"
    return "missing"


def _counter_add(counter: dict, key: str):
    counter[key] = counter.get(key, 0) + 1


# ---------------------------------------------------------------------------
# shadow_ledger 审计
# ---------------------------------------------------------------------------
def audit_shadow(rows, bad_lines, stat, *, verify_fn, repo_root: Path,
                 decision_id_fn, limit=5) -> dict:
    out = {
        "file": stat,
        "rows": len(rows),
        "bad_lines": bad_lines,
        "status": {},
        "decision_id": {"nonempty": 0, "missing": 0, "recomputed": 0,
                        "mismatch": 0, "samples": []},
        "refs": {"rows_with_evidence_refs": 0, "total": 0, "raw": 0,
                 "summary": 0, "raw_verified": 0, "raw_unverified": 0,
                 "verify_failures": {}, "samples_raw": []},
        "raw_summary": {"rows_with_summary": 0, "status": {},
                        "extra_raw_refs": 0},
        "pit": {"refs_data": 0, "refs_collection_only": 0,
                "refs_missing": 0, "rows_with_pit_as_of": 0,
                "refs_with_collection_as_of": 0,
                "as_of_matches_row_ts": 0},
        "date_range": [None, None],
        "last_row": {},
    }
    max_ts = None
    dates = []
    for row in rows:
        status = str(row.get("status") or "unknown")
        _counter_add(out["status"], status)
        date = str(row.get("date") or "")
        if date:
            dates.append(date)
        ts = row.get("ts")
        if isinstance(ts, (int, float)):
            if max_ts is None or ts > max_ts:
                max_ts = ts
        # decision_id
        did = row.get("decision_id")
        if str(did or "").strip():
            out["decision_id"]["nonempty"] += 1
            expected = decision_id_fn(row.get("symbol"), row.get("date"),
                                      row.get("run_id"))
            if expected is not None:
                out["decision_id"]["recomputed"] += 1
                if str(did) != expected:
                    out["decision_id"]["mismatch"] += 1
                    if len(out["decision_id"]["samples"]) < limit:
                        out["decision_id"]["samples"].append({
                            "run_id": row.get("run_id"), "recorded": str(did),
                            "expected": expected})
        else:
            out["decision_id"]["missing"] += 1
        # refs
        refs = row.get("evidence_refs")
        if isinstance(refs, list) and refs:
            out["refs"]["rows_with_evidence_refs"] += 1
        for ref in refs if isinstance(refs, list) else []:
            if not isinstance(ref, dict):
                continue
            out["refs"]["total"] += 1
            kind = _as_of_kind(ref)
            out["pit"]["refs_" + kind] += 1
            if str(ref.get("as_of") or "").strip():
                out["pit"]["refs_with_collection_as_of"] += 1
            if _is_raw_ref(ref):
                out["refs"]["raw"] += 1
                if len(out["refs"]["samples_raw"]) < limit:
                    out["refs"]["samples_raw"].append({
                        "source": ref.get("source"),
                        "kind": ref.get("kind"),
                        "raw_uri": ref.get("raw_uri"),
                        "as_of": ref.get("as_of"),
                        "pit_as_of": ref.get("pit_as_of")})
                if verify_fn is not None:
                    ok, reason = verify_fn(ref, root=repo_root)
                    if ok:
                        out["refs"]["raw_verified"] += 1
                    else:
                        out["refs"]["raw_unverified"] += 1
                        _counter_add(out["refs"]["verify_failures"],
                                     str(reason or "unknown"))
            else:
                out["refs"]["summary"] += 1
        # raw summary（EVIDRAW 落账字段）
        summary = row.get("evidence_raw_refs_summary")
        if isinstance(summary, dict):
            out["raw_summary"]["rows_with_summary"] += 1
            status = str(summary.get("status") or "").strip()
            if status:
                _counter_add(out["raw_summary"]["status"], status)
            extra = summary.get("extra_raw_refs")
            if isinstance(extra, int) and not isinstance(extra, bool):
                out["raw_summary"]["extra_raw_refs"] += extra
        if str(row.get("pit_as_of") or "").strip():
            out["pit"]["rows_with_pit_as_of"] += 1
    out["date_range"] = [min(dates), max(dates)] if dates else [None, None]
    if max_ts is not None:
        out["last_row"] = {
            "ts": max_ts,
            "ts_iso": time.strftime("%Y-%m-%d %H:%M:%S",
                                    time.localtime(max_ts)),
            "date": max(dates) if dates else None,
        }
    return out


# ---------------------------------------------------------------------------
# decision_outcomes 审计
# ---------------------------------------------------------------------------
def audit_outcomes(rows, bad_lines, stat, *, decision_id_fn,
                   ledger_index, limit=5) -> dict:
    out = {
        "file": stat,
        "rows": len(rows),
        "bad_lines": bad_lines,
        "decision_id": {"nonempty": 0, "missing": 0, "recomputed": 0,
                        "mismatch": 0, "samples": []},
        "model_nonempty": 0,
        "hit_nonnull": 0,
        "pit_as_of_nonempty": 0,
        "raw_refs_status": {},
        "date_range": [None, None],
        "run_ids": 0,
    }
    dates = []
    run_ids = set()
    for row in rows:
        date = str(row.get("date") or "")
        if date:
            dates.append(date)
        rid = str(row.get("run_id") or "")
        if rid:
            run_ids.add(rid)
        did = row.get("decision_id")
        if str(did or "").strip():
            out["decision_id"]["nonempty"] += 1
            expected = decision_id_fn(row.get("symbol"), row.get("date"),
                                      row.get("run_id"))
            if expected is not None:
                out["decision_id"]["recomputed"] += 1
                if str(did) != expected:
                    out["decision_id"]["mismatch"] += 1
                    if len(out["decision_id"]["samples"]) < limit:
                        out["decision_id"]["samples"].append({
                            "run_id": rid, "recorded": str(did),
                            "expected": expected})
        else:
            out["decision_id"]["missing"] += 1
        if str(row.get("model") or "").strip():
            out["model_nonempty"] += 1
        if row.get("hit") is not None:
            out["hit_nonnull"] += 1
        if str(row.get("pit_as_of") or "").strip():
            out["pit_as_of_nonempty"] += 1
        status = str(row.get("raw_refs_status") or "").strip()
        if status:
            _counter_add(out["raw_refs_status"], status)
    out["date_range"] = [min(dates), max(dates)] if dates else [None, None]
    out["run_ids"] = len(run_ids)
    # 跨账 join：outcome ↔ shadow（只统计两账都有的 run_id）
    compared = agree = mismatch = 0
    mismatch_samples = []
    for row in rows:
        rid = str(row.get("run_id") or "")
        ledger_row = ledger_index.get(rid) if rid else None
        if not isinstance(ledger_row, dict):
            continue
        expected = decision_id_fn(ledger_row.get("symbol"),
                                  ledger_row.get("date"),
                                  ledger_row.get("run_id"))
        recorded = row.get("decision_id")
        if expected is None or not str(recorded or "").strip():
            continue
        compared += 1
        if str(recorded) == expected:
            agree += 1
        else:
            mismatch += 1
            if len(mismatch_samples) < limit:
                mismatch_samples.append({"run_id": rid,
                                         "recorded": str(recorded),
                                         "expected": expected})
    out["cross_shadow"] = {"compared": compared, "agree": agree,
                           "mismatch": mismatch,
                           "mismatch_samples": mismatch_samples}
    return out


def audit_cross(shadow_rows, outcome_rows, decision_id_fn, limit=5) -> dict:
    """两账 run_id 覆盖 + shadow 内复算可关联性。"""
    ledger_index = {str(r.get("run_id")): r for r in shadow_rows
                    if str(r.get("run_id") or "")}
    out = {
        "shadow_run_ids": len(ledger_index),
        "outcome_run_ids": len({str(r.get("run_id")) for r in outcome_rows
                                if str(r.get("run_id") or "")}),
        "outcome_missing_in_shadow": 0,
        "shadow_done_without_outcome": 0,
        "samples_missing_in_shadow": [],
        "samples_shadow_done_without_outcome": [],
        "recomputable_decision_id": 0,
    }
    outcome_ids = {str(r.get("run_id")) for r in outcome_rows
                   if str(r.get("run_id") or "")}
    shadow_done = {str(r.get("run_id")) for r in shadow_rows
                   if str(r.get("run_id") or "")
                   and r.get("status") == "done"}
    missing = sorted(outcome_ids - set(ledger_index))
    no_outcome = sorted(shadow_done - outcome_ids)
    out["outcome_missing_in_shadow"] = len(missing)
    out["shadow_done_without_outcome"] = len(no_outcome)
    out["samples_missing_in_shadow"] = missing[:limit]
    out["samples_shadow_done_without_outcome"] = no_outcome[:limit]
    recomputable = 0
    for row in shadow_rows:
        if decision_id_fn(row.get("symbol"), row.get("date"),
                          row.get("run_id")) is not None:
            recomputable += 1
    out["recomputable_decision_id"] = recomputable
    return out


# ---------------------------------------------------------------------------
# findings（人读/机器共用；不阻断，只如实记录）
# ---------------------------------------------------------------------------
def build_findings(shadow, outcomes, cross) -> list:
    findings = []
    if not shadow["file"].get("exists"):
        findings.append({"id": "shadow_missing", "level": "warn",
                         "detail": "shadow_ledger.jsonl 不存在"})
    if not outcomes["file"].get("exists"):
        findings.append({"id": "outcomes_missing", "level": "warn",
                         "detail": "decision_outcomes.jsonl 不存在"})
    if shadow.get("rows"):
        did = shadow["decision_id"]
        if did["missing"]:
            findings.append({
                "id": "shadow_decision_id_missing", "level": "info",
                "detail": "%d/%d 行无 decision_id（接线前旧行；按 run_id 可复算 %d 行）"
                          % (did["missing"], shadow["rows"], cross["recomputable_decision_id"])})
        if did["mismatch"]:
            findings.append({
                "id": "shadow_decision_id_mismatch", "level": "high",
                "detail": "%d 行 decision_id 与公式复算不一致" % did["mismatch"]})
        refs = shadow["refs"]
        if refs["total"] == 0:
            findings.append({
                "id": "shadow_refs_empty", "level": "warn",
                "detail": "全部行无 evidence_refs（接线前旧行；raw ref 无从核验）"})
        elif refs["raw"] == 0:
            findings.append({
                "id": "shadow_refs_no_raw", "level": "warn",
                "detail": "evidence_refs 全是摘要 ref（raw=0），raw_ref 缺口未整改"})
        elif refs["raw_unverified"]:
            findings.append({
                "id": "shadow_raw_unverified", "level": "high",
                "detail": "%d 条 raw ref 复核失败（%s）"
                          % (refs["raw_unverified"],
                             refs["verify_failures"])})
        if shadow["pit"]["refs_data"] == 0 and shadow["pit"]["refs_with_collection_as_of"]:
            findings.append({
                "id": "shadow_pit_collection_only", "level": "warn",
                "detail": "refs 只有 as_of(采集时刻)、无 pit_as_of(数据时点)：PIT 非数据时点"})
    if shadow.get("pit", {}).get("rows_with_pit_as_of") == 0 and shadow.get("rows"):
        findings.append({"id": "shadow_pit_row_missing", "level": "warn",
                         "detail": "shadow 行级 pit_as_of 覆盖 0（旧行；11-02 首跑后复查）"})
    if outcomes.get("rows"):
        if outcomes["pit_as_of_nonempty"] == 0:
            findings.append({"id": "outcomes_pit_missing", "level": "warn",
                             "detail": "outcome 行 pit_as_of 覆盖 0（旧行；本单写入器已补，待 11-02 回写验证）"})
        if not outcomes["raw_refs_status"]:
            findings.append({"id": "outcomes_raw_status_missing", "level": "warn",
                             "detail": "outcome 行 raw_refs_status 覆盖 0（旧行；待 11-02 回写）"})
    if cross.get("outcome_missing_in_shadow"):
        findings.append({"id": "outcome_run_id_not_in_shadow", "level": "high",
                         "detail": "%d 个 outcome run_id 不在 shadow_ledger"
                                   % cross["outcome_missing_in_shadow"]})
    return findings


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
def render_report(report) -> str:
    s, o, c = report["shadow"], report["outcomes"], report["cross"]
    lines = ["# D1 raw/decision_id/PIT 只读审计", ""]
    lines.append("> 生成 %s ｜ root=%s" % (report["generated_at"], report["root"]))
    lines.append("")
    lines.append("## shadow_ledger（%s）" % (
        "缺失" if not s["file"].get("exists") else "%d 行" % s["rows"]))
    if s["file"].get("exists"):
        lines.append("- 文件：%s ｜ bytes=%d ｜ mtime=%s" % (
            s["file"]["path"], s["file"]["bytes"], s["file"]["mtime"]))
        lines.append("- status：%s ｜ 日期范围 %s ~ %s ｜ 末行 %s" % (
            s["status"], s["date_range"][0], s["date_range"][1],
            s["last_row"].get("ts_iso") or "-"))
        lines.append("- decision_id：非空 %d / 缺失 %d / 复算一致 %d / 不一致 %d" % (
            s["decision_id"]["nonempty"], s["decision_id"]["missing"],
            s["decision_id"]["recomputed"], s["decision_id"]["mismatch"]))
        lines.append("- refs：总 %d（raw %d / 摘要 %d）｜ raw 复核 ok=%d fail=%d ｜ 摘要行=%d" % (
            s["refs"]["total"], s["refs"]["raw"], s["refs"]["summary"],
            s["refs"]["raw_verified"], s["refs"]["raw_unverified"],
            s["raw_summary"]["rows_with_summary"]))
        lines.append("- PIT：refs data=%d / collection_only=%d / missing=%d ｜ 行级 pit_as_of=%d" % (
            s["pit"]["refs_data"], s["pit"]["refs_collection_only"],
            s["pit"]["refs_missing"], s["pit"]["rows_with_pit_as_of"]))
    lines.append("")
    lines.append("## decision_outcomes（%s）" % (
        "缺失" if not o["file"].get("exists") else "%d 行" % o["rows"]))
    if o["file"].get("exists"):
        lines.append("- decision_id：非空 %d / 缺失 %d / 复算不一致 %d" % (
            o["decision_id"]["nonempty"], o["decision_id"]["missing"],
            o["decision_id"]["mismatch"]))
        lines.append("- model 非空 %d ｜ hit 非空 %d ｜ pit_as_of 非空 %d ｜ raw_refs_status %s" % (
            o["model_nonempty"], o["hit_nonnull"], o["pit_as_of_nonempty"],
            o["raw_refs_status"] or "{}"))
        lines.append("- 跨账：与 shadow 同 run_id 比 decision_id 共 %d 条（一致 %d / 不一致 %d）" % (
            o["cross_shadow"]["compared"], o["cross_shadow"]["agree"],
            o["cross_shadow"]["mismatch"]))
    lines.append("")
    lines.append("## 跨账覆盖")
    lines.append("- shadow run_id=%d ｜ outcome run_id=%d ｜ outcome 不在 shadow=%d ｜ shadow done 无 outcome=%d ｜ shadow 可复算 decision_id=%d" % (
        c["shadow_run_ids"], c["outcome_run_ids"],
        c["outcome_missing_in_shadow"], c["shadow_done_without_outcome"],
        c["recomputable_decision_id"]))
    lines.append("")
    lines.append("## findings（只读记录，不阻断）")
    if not report["findings"]:
        lines.append("- 无")
    for item in report["findings"]:
        lines.append("- [%s] %s：%s" % (item["level"], item["id"], item["detail"]))
    lines.append("")
    lines.append("> 纪律：本工具只读（不写账本/不写 sidecar/不建目录），PIT 缺=缺、"
                 "raw 缺文件=拒绝，不伪造。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def run_audit(root=None) -> dict:
    base = Path(root).resolve() if root else DEFAULT_ROOT.resolve()
    shadow_path = base / SHADOW_REL
    outcomes_path = base / OUTCOMES_REL
    shadow_rows, shadow_bad = _load_jsonl(shadow_path)
    outcome_rows, outcome_bad = _load_jsonl(outcomes_path)
    decision_id_fn = _load_decision_id_fn()
    verify_fn = _load_verify_raw_ref()
    ledger_index = {str(r.get("run_id")): r for r in shadow_rows
                    if str(r.get("run_id") or "")}
    report = {
        "schema": SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z",
                                      time.localtime()),
        "root": str(base),
        "read_only": True,
        "shadow": audit_shadow(shadow_rows, shadow_bad, _stat(shadow_path),
                               verify_fn=verify_fn, repo_root=base,
                               decision_id_fn=decision_id_fn),
        "outcomes": audit_outcomes(outcome_rows, outcome_bad,
                                   _stat(outcomes_path),
                                   decision_id_fn=decision_id_fn,
                                   ledger_index=ledger_index),
        "cross": audit_cross(shadow_rows, outcome_rows, decision_id_fn),
        "findings": [],
    }
    report["findings"] = build_findings(report["shadow"], report["outcomes"],
                                        report["cross"])
    report["ok"] = True
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="D1 raw/decision_id/PIT 只读审计")
    ap.add_argument("--root", default=None,
                    help="生产仓根（默认本工具所在仓）；只读使用其 data/shadow 两账")
    ap.add_argument("--json", action="store_true", help="机器可读 JSON")
    args = ap.parse_args(argv)
    try:
        report = run_audit(args.root)
    except Exception as exc:  # noqa: BLE001 - 审计失败必须显式（不假成功）
        print(json.dumps({"schema": SCHEMA, "ok": False,
                          "error": "%s: %s" % (type(exc).__name__, exc)},
                         ensure_ascii=False, indent=2))
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(render_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
