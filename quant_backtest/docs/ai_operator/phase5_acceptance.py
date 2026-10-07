"""真实源持久接收与审核缺口联调；独立验收库，不触碰生产库。"""

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--operator-root", required=True)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, args.operator_root)
    from ai_operator.contracts import canonical_json, normalize_time, utc_now
    from ai_operator.memory import open_store
    from ai_operator.capture_journal import load_capture
    from ai_operator.shadow import build_fact_pack, make_shadow_decision

    source_root = Path(args.source_root)
    price_path = source_root / "data" / "ashare" / "000001_SH.parquet"
    calendar_paths = sorted((source_root / "data" / "tushare" / "trade_cal").glob("part_*.parquet"))
    if not calendar_paths:
        raise RuntimeError("真实本地日历分片尚未就绪")
    protected = [price_path, *calendar_paths, *[
        source_root / "data" / "distill" / name for name in ("kg_distill.parquet", "kg_distill_neg.parquet")]]
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
    artifacts = Path(args.artifact_dir)
    artifacts.mkdir(parents=True, exist_ok=True)
    db = artifacts / ("audit-" + uuid.uuid4().hex + ".sqlite")
    commands = []

    def run(command, request=None, suffix=""):
        argv = [sys.executable, "-B", "-m", "ai_operator", command, "--db", str(db)]
        if request is not None:
            request_path = artifacts / (command + suffix + "-请求.json")
            request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
            argv.extend(["--input", str(request_path)])
        if command == "export":
            argv.extend(["--output-dir", str(artifacts / "outbox")])
        result = subprocess.run(argv, cwd=artifacts, env=dict(os.environ, PYTHONPATH=args.operator_root),
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
        if result.returncode != 0 or result.stderr:
            raise RuntimeError(f"CLI {command} 失败：{result.stdout} {result.stderr}")
        value = json.loads(result.stdout)
        (artifacts / (command + suffix + "-结果.json")).write_text(result.stdout, encoding="utf-8")
        commands.append({"command": command, "exit_code": 0})
        return value

    run("init")
    capture_request = {"source_request": {"path": str(price_path), "source_id": "local-ashare-index-file",
                                          "code": "000001.SH", "price_basis": "raw",
                                          "revision": "existing-file-current-capture-unverified", "format": "ashare_parquet"},
                       "calendar_request": {"paths": [str(path) for path in calendar_paths], "exchange": "SSE",
                                            "source_id": "local-tushare-calendar-files",
                                            "revision": "existing-file-current-capture-unverified",
                                            "coverage_start": "2025-01-02", "coverage_end": "2026-04-01",
                                            "format": "tushare_parquet"}}
    first = run("capture-journal", capture_request, "-首次")
    second = run("capture-journal", dict(capture_request, previous_event_id=first["capture_event_id"]), "-续接")
    history = run("capture-history", {"capture_event_id": second["capture_event_id"], "as_of": utc_now()})
    if [entry["capture_event_id"] for entry in history] != [first["capture_event_id"], second["capture_event_id"]]:
        raise RuntimeError("重开库后的接收链未完整返回")
    with open_store(db, readonly=True) as store:
        receipt = load_capture(store, second["capture_event_id"], utc_now())
    review = run("source-review", {"capture_event_id": receipt["capture_event_id"],
                                    "reviewer": "Codex真实来源缺口登记（未人审通过）",
                                    "evidence": dict.fromkeys(("supplier_identity", "price_basis", "revision", "official_calendar"))})
    gate = run("review-gate", {"capture_event_id": receipt["capture_event_id"],
                                "review_event_id": review["review_event_id"], "as_of": utc_now()})
    if gate["status"] != "blocked_missing_evidence" or gate["eligible"] is not False:
        raise RuntimeError("真实未知来源被错误放行")
    snapshot = receipt["snapshot"]
    now = utc_now()
    pack = build_fact_pack(snapshot["code"], "1d", now,
                           [{"id": receipt["capture_event_id"], "source": snapshot["source_id"],
                             "occurred_at": normalize_time(snapshot["bars"][-1]["close_at"]),
                             "available_at": receipt["recorded_available_at"],
                             "data": {"bar_count": len(snapshot["bars"]), "capture_event_id": receipt["capture_event_id"]}}],
                           {"data": snapshot["snapshot_id"], "algorithm": "current-capture-audit-only",
                            "knowledge": "no-cloud-bundle-verification", "rules": "no-rule-activation"})
    decision = make_shadow_decision(pack, "observe", [receipt["capture_event_id"]], "持久来源审计，未进行信号交易",
                                    (datetime.fromisoformat(now) + timedelta(days=1)).isoformat())
    outcome = run("journal-outcome", {"capture_event_id": receipt["capture_event_id"], "pack": pack,
                                       "decision": decision, "evaluated_at": utc_now(), "horizons": [5, 20]})
    if any(label["status"] != "audit_only" or label["target"] is not None for label in outcome["labels"]):
        raise RuntimeError("未审核来源产生了训练目标")
    run("export")
    exported = [json.loads(line) for line in (artifacts / "outbox" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    with open_store(db, readonly=True) as store:
        state = store.snapshot()
        if state["events"] != 3 or len(exported) != 3 or state["pending_events"] != 0:
            raise RuntimeError("三个完整事件的导出或outbox状态不符")
        for item in exported:
            if canonical_json(item) != canonical_json(store.get_event(item["event_id"])):
                raise RuntimeError("导出事件无法与库内完整内容对照")
    after = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
    if before != after:
        raise RuntimeError("受保护真实源文件发生变化")
    report = {"schema_version": 1, "batch": 5, "ok": True, "verified_at": utc_now(),
              "audit_database": str(db), "commands": commands, "capture_chain_length": len(history),
              "capture_event_ids": [item["capture_event_id"] for item in history],
              "stream_id": receipt["stream_id"], "bars": len(snapshot["bars"]),
              "review_event_id": review["review_event_id"], "review_status": review["status"],
              "gate_status": gate["status"], "checks": gate["checks"],
              "audit_labels": [label["status"] for label in outcome["labels"]],
              "exported_events": len(exported), "pending_events_after_export": 0,
              "source_hashes_before": before, "source_hashes_after": after,
              "source_files_unchanged": True, "eligible": False, "production_eligible": False,
              "supplier_verified": False, "historical_availability_verified": False,
              "model_training_started": False, "trading_started": False,
              "limitations": ["当前存量行情截至2026-04-01，不是实时行情", "代码候选不证明历史下载执行",
                              "没有供应商、价格口径、修订和独立官方日历完整凭据",
                              "声明URL不是下载证明或签名", "缺人审和受控批准流程，未提供trusted开关"]}
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(canonical_json({key: report[key] for key in ("ok", "capture_chain_length", "bars", "review_status",
                                                       "gate_status", "exported_events", "source_files_unchanged")}))


if __name__ == "__main__":
    main()
