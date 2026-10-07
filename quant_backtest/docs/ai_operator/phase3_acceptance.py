"""显式工程夹具的标签、切分、摘要联调；不补造真实市场可用时间。"""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--operator-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--source-root")
    args = parser.parse_args()
    sys.path.insert(0, args.operator_root)
    from ai_operator.contracts import canonical_json, normalize_time, utc_now
    from ai_operator.shadow import build_fact_pack, make_shadow_decision
    from ai_operator.outcomes import label_decision
    from ai_operator.dataset import split_dataset, summarize_split

    sources = []
    if args.source_root:
        sources = [Path(args.source_root) / "data" / "distill" / name
                   for name in ("kg_distill.parquet", "kg_distill_neg.parquet")]
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    sessions = []
    current = datetime(2026, 1, 1, 7, tzinfo=timezone.utc)
    while len(sessions) < 90:
        if current.weekday() < 5:
            sessions.append({"session": current.strftime("%Y-%m-%d"), "close_at": normalize_time(current)})
        current += timedelta(days=1)
    bars = [dict(item, close=100.0 + index + (index % 5) / 10,
                 available_at=normalize_time(datetime.fromisoformat(item["close_at"]) + timedelta(minutes=1)))
            for index, item in enumerate(sessions)]
    calendar = {"version": "engineering-fixture-calendar-v1", "sessions": sessions}
    source = {"source_id": "engineering-fixture-not-market-data",
              "source_hash": hashlib.sha256(canonical_json(bars).encode("utf-8")).hexdigest(),
              "price_basis": "raw", "availability_status": "trusted"}
    evaluated = "2026-07-01T00:00:00Z"
    requests = []
    rows = []
    for number, date in enumerate(("2026-01-02", "2026-01-08", "2026-01-19", "2026-01-20",
                                 "2026-01-22", "2026-02-09", "2026-02-10", "2026-02-12",
                                 "2026-02-20", "2026-03-10")):
        instant = date + "T06:00:00Z"
        code = "FIXTURE" + str(number)
        pack = build_fact_pack(code, "1d", instant,
                               [{"id": "structure-counts", "source": "engineering-fixture",
                                 "occurred_at": instant, "available_at": instant,
                                 "data": {"pivot_count": number % 3, "bi_count": number % 7}}],
                               {key: "engineering-fixture-v1" for key in ("data", "algorithm", "knowledge", "rules")})
        decision = make_shadow_decision(pack, "observe", ["structure-counts"], "工程夹具观察，非投资建议",
                                        normalize_time(datetime.fromisoformat(instant) + timedelta(days=1)))
        request = {"pack": pack, "decision": decision, "bars": bars, "calendar": calendar,
                   "source": source, "evaluated_at": evaluated, "horizons": [5, 20]}
        result = label_decision(**request)
        repeated = label_decision(**request)
        if canonical_json(result) != canonical_json(repeated):
            raise RuntimeError("同输入标签结果不一致")
        label = result["labels"][0]
        if label["status"] != "matured":
            raise RuntimeError("完整已到达夹具未生成成熟 H5 标签")
        family = "shared-fixture-family" if number in (0, 8) else None
        rows.append({"sample_id": decision["decision_id"], "code": code, "content_family_id": family,
                     "decision_at": pack["as_of"], "feature_available_at": pack["as_of"],
                     "label_end_at": label["label_end_at"], "label_available_at": label["label_available_at"],
                     "features": pack["facts"][0]["data"], "target": label["target"],
                     "target_kind": label["target_kind"], "horizon": 5,
                     "label_policy": "engineering-fixture-forward-close-v1-raw"})
        requests.append(request)

    base = requests[0]
    early_request = dict(base, evaluated_at=bars[5]["available_at"])
    early = label_decision(**early_request)
    if any(label["status"] == "matured" or label["target"] is not None for label in early["labels"]):
        raise RuntimeError("尚未到达终点的夹具出现成熟标签")
    unknown = label_decision(**dict(base, source=dict(source, availability_status="unknown")))
    if any(label["status"] != "audit_only" or label["target"] is not None for label in unknown["labels"]):
        raise RuntimeError("未知来源夹具不应产生训练目标")
    missing = label_decision(**dict(base, bars=[bar for bar in bars if bar["session"] != "2026-01-06"]))
    if any(label["status"] != "pending" or label["reason"] != "missing_bar" for label in missing["labels"]):
        raise RuntimeError("缺少窗口行情时不应跳过日期")
    immature = dict(rows[1], sample_id="immature-fixture", code="IMMATURE",
                    content_family_id=None, label_available_at="2026-08-01T00:00:00Z")
    rows.append(immature)
    dataset_request = {"rows": rows, "feature_allowlist": ["pivot_count", "bi_count"],
                       "train_end": "2026-01-20T00:00:00Z", "validation_end": "2026-02-10T00:00:00Z",
                       "test_end": "2026-03-10T00:00:00Z", "as_of": evaluated, "embargo_seconds": 86400}
    split = split_dataset(**dataset_request)
    reverse = split_dataset(**dict(dataset_request, rows=list(reversed(rows))))
    if canonical_json(split) != canonical_json(reverse):
        raise RuntimeError("样本顺序改变导致切分变化")
    summary = summarize_split(split)
    expected_reasons = {"purge_train", "purge_validation", "embargo", "group_overlap", "immature", "outside_window"}
    if set(summary["excluded_by_reason"]) != expected_reasons:
        raise RuntimeError("夹具未覆盖预期剔除原因：" + canonical_json(summary))
    if any(summary["partitions"][name]["n"] == 0 for name in ("train", "validation", "test")):
        raise RuntimeError("夹具切分出现意外空折")
    after = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    if before != after:
        raise RuntimeError("本地存量数据源在验收中改变")
    output_dir = Path(args.artifact_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {"label-request.json": base, "label-result.json": label_decision(**base),
                 "dataset-request.json": dataset_request, "split-result.json": split, "summary.json": summary}
    for name, value in artifacts.items():
        (output_dir / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {"ok": True, "verified_at": utc_now(), "operator_root": args.operator_root,
              "input_type": "engineering_fixture_not_market_data", "calendar_sessions": len(sessions),
              "label_cases": {"matured": len(requests) * 2, "pending_early": len(early["labels"]),
                              "audit_only_unknown": len(unknown["labels"]), "pending_missing_bar": len(missing["labels"])},
              "summary": summary, "input_hash": split["input_hash"], "input_order_independent": True,
              "source_hashes": before, "source_hashes_unchanged": before == after,
              "artifact_dir": str(output_dir.resolve()), "model_trained": False,
              "historical_profitability_verified": False, "production_eligible": False}
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
