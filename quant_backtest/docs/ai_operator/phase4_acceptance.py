"""真实本地文件只读联调；来源未审核的标签保持审计状态。"""

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


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
    from ai_operator.provenance import validate_snapshot
    from ai_operator.trading_calendar import validate_calendar_capture
    from ai_operator.shadow import build_fact_pack, make_shadow_decision

    source_root = Path(args.source_root)
    price_path = source_root / "data" / "ashare" / "000001_SH.parquet"
    calendar_paths = sorted((source_root / "data" / "tushare" / "trade_cal").glob("part_*.parquet"))
    if not calendar_paths:
        raise RuntimeError("真实本地交易日历分片尚未就绪")
    protected = [price_path, *calendar_paths, *[
        source_root / "data" / "distill" / name for name in ("kg_distill.parquet", "kg_distill_neg.parquet")]]
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
    artifacts = Path(args.artifact_dir)
    artifacts.mkdir(parents=True, exist_ok=True)
    commands = []

    def run(command, request):
        request_path = artifacts / (command + "-请求.json")
        request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        result = subprocess.run([sys.executable, "-B", "-m", "ai_operator", command,
                                 "--input", str(request_path)], cwd=artifacts,
                                env=dict(os.environ, PYTHONPATH=args.operator_root),
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
        if result.returncode != 0 or result.stderr:
            raise RuntimeError(f"真实文件 CLI {command} 失败：{result.stdout} {result.stderr}")
        value = json.loads(result.stdout)
        (artifacts / (command + "-结果.json")).write_text(result.stdout, encoding="utf-8")
        commands.append({"command": command, "exit_code": result.returncode})
        return value

    snapshot = run("capture-source", {"path": str(price_path), "source_id": "local-ashare-index-file",
                                      "code": "000001.SH", "price_basis": "raw",
                                      "revision": "existing-file-current-capture-unverified",
                                      "format": "ashare_parquet"})
    validate_snapshot(snapshot)
    if snapshot["source_file"]["sha256"] != before[str(price_path)]:
        raise RuntimeError("源文件在验收前与快照读取间发生变化")
    calendar = run("capture-calendar", {"paths": [str(path) for path in calendar_paths], "exchange": "SSE",
                                        "source_id": "local-tushare-calendar-files",
                                        "revision": "existing-file-current-capture-unverified",
                                        "coverage_start": snapshot["bars"][0]["session"],
                                        "coverage_end": snapshot["bars"][-1]["session"], "format": "tushare_parquet"})
    validate_calendar_capture(calendar)
    for item in calendar["source_files"]:
        if item["sha256"] != before[item["path"]]:
            raise RuntimeError("日历文件在验收前与快照读取间发生变化")
    adapted = run("source-inputs", {"snapshot": snapshot, "calendar_capture": calendar})
    now = utc_now()
    fact_id = snapshot["snapshot_id"]
    pack = build_fact_pack(snapshot["code"], "1d", now,
                           [{"id": fact_id, "source": snapshot["source_id"],
                             "occurred_at": normalize_time(snapshot["bars"][-1]["close_at"]),
                             "available_at": snapshot["received_at"],
                             "data": {"bar_count": len(snapshot["bars"]),
                                      "source_snapshot_id": fact_id,
                                      "supplier_verification": "unverified"}}],
                           {"data": fact_id, "algorithm": "no-algorithm-current-capture-audit",
                            "knowledge": "no-cloud-bundle-verification", "rules": "no-rule-activation"})
    decision = make_shadow_decision(pack, "observe", [fact_id], "真实本地来源当前接收的工程审计",
                                    (datetime.fromisoformat(now) + timedelta(days=1)).isoformat())
    outcome = run("captured-outcome", {"snapshot": snapshot, "calendar_capture": calendar,
                                       "pack": pack, "decision": decision, "evaluated_at": utc_now(),
                                       "horizons": [5, 20]})
    if adapted["source"]["availability_status"] != "unknown":
        raise RuntimeError("未知供应商来源被错误升级")
    if any(item["status"] != "audit_only" or item["target"] is not None for item in outcome["labels"]):
        raise RuntimeError("未审核真实源产生了成熟训练目标")
    if any(bar["available_at"] != snapshot["received_at"] for bar in snapshot["bars"]):
        raise RuntimeError("旧日线被补造历史可用时间")
    after = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
    if before != after:
        raise RuntimeError("受保护的真实存量源文件发生变化")
    report = {"schema_version": 1, "batch": 4, "ok": True, "verified_at": utc_now(),
              "commands": commands, "snapshot_id": fact_id, "received_at": snapshot["received_at"],
              "bars": len(snapshot["bars"]), "calendar_capture_id": calendar["capture_id"],
              "calendar_version": calendar["calendar"]["version"],
              "calendar_days": len(calendar["days"]), "open_sessions": len(calendar["calendar"]["sessions"]),
              "calendar_source_files": len(calendar["source_files"]),
              "coverage_start": calendar["coverage_start"], "coverage_end": calendar["coverage_end"],
              "audit_labels": [item["status"] for item in outcome["labels"]],
              "source_hashes_before": before, "source_hashes_after": after,
              "source_files_unchanged": True, "production_eligible": False,
              "supplier_verified": False, "historical_availability_verified": False,
              "model_training_started": False, "trading_started": False,
              "limitations": ["当前系统接收不证明历史可用", "内容哈希不是供应商签名",
                              "价格口径和修订为显式未审核声明", "当前日历不证明过去决策时已知该版本",
                              "原始云端规则包仍待取得核验"]}
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(canonical_json({key: report[key] for key in ("ok", "bars", "calendar_days", "open_sessions",
                                                       "audit_labels", "source_files_unchanged")}))


if __name__ == "__main__":
    main()
